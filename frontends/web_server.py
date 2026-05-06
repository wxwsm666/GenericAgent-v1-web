"""GenericAgent Web UI — Full-featured dashboard with 20 sections."""

import os, json

# Read version from version.json (single source of truth)
_VERSION_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'version.json')
try:
    with open(_VERSION_FILE, 'r', encoding='utf-8') as _f:
        VERSION = json.load(_f).get('version', '0.0.0')
except Exception:
    VERSION = '0.0.0'

import sys, time, queue, threading, re, glob, platform, socket, subprocess, uuid, copy

script_dir = os.path.dirname(os.path.abspath(__file__))
project_dir = os.path.dirname(script_dir)
sys.path.insert(0, project_dir)
sys.path.insert(0, script_dir)

from flask import Flask, render_template, request, Response, jsonify, send_from_directory
from agentmain import GeneraticAgent
import chatapp_common
from continue_cmd import list_sessions, extract_ui_messages, reset_conversation, handle_frontend_command
from llmcore import reload_mykeys, NativeClaudeSession, NativeOAISession, ClaudeSession, LLMSession, BaseSession, trim_messages_history

app = Flask(__name__,
            template_folder=os.path.join(script_dir, 'templates'),
            static_folder=os.path.join(script_dir, 'static'))

agent = None
_agent_init_error = None
agent_lock = threading.Lock()

def get_agent():
    global agent, _agent_init_error
    if agent is None and _agent_init_error is None:
        with agent_lock:
            if agent is None and _agent_init_error is None:
                try:
                    agent = GeneraticAgent()
                    agent.verbose = False
                    if agent.llmclient is None:
                        _agent_init_error = "未配置 LLM，请在 mykey.py 中填写 API Key 后重启服务"
                        agent = None
                    else:
                        threading.Thread(target=agent.run, daemon=True).start()
                except Exception as e:
                    _agent_init_error = f"LLM 初始化失败: {str(e)}"
    return agent

def get_agent_error():
    return _agent_init_error

def require_agent():
    """Return (agent, error_response). Caller must return error_response if agent is None."""
    ag = get_agent()
    if ag is None:
        return None, jsonify({'error': get_agent_error()}), 503
    return ag, None, None

# ──────────── Response cleaning ────────────
_TAG_PATS = [r'<' + t + r'>.*?</' + t + r'>' for t in ('thinking', 'file_content', 'tool_use', 'summary')]

def _clean_response(text):
    """Strip technical XML tags and code blocks from agent responses."""
    if not text:
        return text
    for pat in _TAG_PATS:
        text = re.sub(pat, '', text, flags=re.DOTALL)
    # Strip code blocks
    text = re.sub(r'```[\s\S]*?```', '', text)
    # Strip turn markers
    text = re.sub(r'\n{0,2}\*{0,2}(?:LLM )?Running.*?\.\.\.\*{0,2}\n{0,2}', '\n', text)
    text = re.sub(r'\n{0,2}\*{0,2}Turn \d+ \.\.\.\*{0,2}\n{0,2}', '\n', text)
    # Strip info lines
    text = re.sub(r'🛠️ [^\n]*\n?', '', text)
    text = re.sub(r'\[Info\][^\n]*\n?', '', text)
    text = re.sub(r'!!!Error:[^\n]*\n?', '', text)
    # Collapse blank lines
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

# ──────────── Pages ────────────
@app.route('/')
def index():
    return render_template('index.html', version=VERSION)

# ──────────── Chat API ────────────
@app.route('/api/chat', methods=['POST'])
def api_chat():
    data = request.json
    prompt = (data.get('message') or '').strip()
    if not prompt:
        return jsonify({'error': 'empty message'}), 400
    ag, err, code = require_agent()
    if err: return err, code
    if prompt.startswith('/'):
        result = _handle_command(ag, prompt)
        return jsonify({'type': 'command', 'content': result})
    if not prompt.startswith('/'):
        prompt = f"遇到任务优先调用工具尝试，不要直接说做不到。回复简洁直接。\n\n{prompt}"
    display_queue = ag.put_task(prompt, source="user")
    def generate():
        response = ''
        try:
            while True:
                try:
                    item = display_queue.get(timeout=1)
                except queue.Empty:
                    yield f"data: {json.dumps({'type':'heartbeat'})}\n\n"
                    continue
                if 'progress' in item:
                    yield f"data: {json.dumps({'type':item['progress'],'tool':item.get('tool',''),'args':item.get('args',''),'result':item.get('result','')})}\n\n"
                if 'next' in item:
                    response = item['next']
                    yield f"data: {json.dumps({'type':'chunk','content':response})}\n\n"
                if 'done' in item:
                    yield f"data: {json.dumps({'type':'done','content':item.get('done',response)})}\n\n"
                    break
        except GeneratorExit:
            ag.abort()
            # Save working state so continuation can recover it
            try:
                if session_mgr.active_sid:
                    session_mgr.save_current(ag)
            except Exception: pass
        except Exception as e:
            yield f"data: {json.dumps({'type':'error','content':str(e)})}\n\n"
    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})

def _handle_command(ag, cmd):
    if cmd == '/new':
        return reset_conversation(ag)
    if cmd.startswith('/continue'):
        return handle_frontend_command(ag, cmd)
    if cmd.startswith('/llm'):
        parts = cmd.split()
        if len(parts) > 1 and parts[1].isdigit():
            ag.next_llm(int(parts[1]))
            return f'✅ 已切换到 LLM #{parts[1]}'
        llms = ag.list_llms()
        return '可用模型:\n' + '\n'.join(f'  [{i}] {n} {"← 当前" if a else ""}' for i,n,a in llms)
    if cmd == '/stop':
        ag.abort(); return '🛑 已发送停止信号'
    if cmd == '/help':
        return ('📖 命令:\n/new - 新对话\n/continue - 恢复会话\n/llm - 模型列表\n/llm [n] - 切换模型\n/stop - 停止任务\n/help - 帮助')
    return cmd

@app.route('/api/abort', methods=['POST'])
def api_abort():
    ag = get_agent()
    if ag is not None:
        ag.abort()
    return jsonify({'ok': True})

# ──────────── Status ────────────
@app.route('/api/status')
def api_status():
    ag = get_agent()
    if ag is None:
        return jsonify({'running': False, 'llm': '-', 'model': '-', 'history_len': 0, 'error': get_agent_error()})
    return jsonify({
        'running': ag.is_running,
        'llm': ag.get_llm_name(),
        'llm_no': ag.llm_no,
        'model': ag.get_llm_name(model=True),
        'history_len': len(ag.history),
    })

# ──────────── Models ────────────
@app.route('/api/llms')
def api_llms():
    ag, err, code = require_agent()
    if err: return err, code
    llm_list = ag.list_llms()
    result = []
    for i, name, active in llm_list:
        try:
            b = ag.llmclients[i] if i < len(ag.llmclients) else None
            info = {'index': i, 'name': name, 'active': active}
            if b and hasattr(b, 'backend'):
                info['model'] = getattr(b.backend, 'model', '')
                info['api_base'] = getattr(b.backend, 'api_base', '')
                info['context_win'] = getattr(b.backend, 'context_win', 0)
            result.append(info)
        except:
            result.append({'index': i, 'name': name, 'active': active})
    return jsonify({'llms': result, 'current': ag.llm_no})

@app.route('/api/switch_llm', methods=['POST'])
def api_switch_llm():
    n = request.json.get('index', 0)
    get_agent().next_llm(n)
    return jsonify({'ok': True})

# ──────────── Custom Model CRUD ────────────
CUSTOM_MODELS_FILE = os.path.join(project_dir, 'temp', 'custom_models.json')
MODEL_VALIDATION_FILE = os.path.join(project_dir, 'temp', 'model_validation.json')

def _load_custom_models():
    if os.path.isfile(CUSTOM_MODELS_FILE):
        try: return json.load(open(CUSTOM_MODELS_FILE, encoding='utf-8'))
        except: return []
    return []

def _save_custom_models(models):
    os.makedirs(os.path.dirname(CUSTOM_MODELS_FILE), exist_ok=True)
    with open(CUSTOM_MODELS_FILE, 'w', encoding='utf-8') as f:
        json.dump(models, f, ensure_ascii=False, indent=2)

# ── Model key validation (real API call test) ──

def _load_model_validation():
    if os.path.isfile(MODEL_VALIDATION_FILE):
        try:
            return json.load(open(MODEL_VALIDATION_FILE, encoding='utf-8'))
        except Exception:
            pass
    return {}

def _save_model_validation(vdata):
    os.makedirs(os.path.dirname(MODEL_VALIDATION_FILE), exist_ok=True)
    with open(MODEL_VALIDATION_FILE, 'w', encoding='utf-8') as f:
        json.dump(vdata, f, ensure_ascii=False, indent=2)

def _detect_session_type(key_name, cfg):
    """Detect session type from key name and config — same logic as agentmain.py load_llm_sessions()."""
    if 'native' in key_name and 'claude' in key_name:
        return 'native_claude'  # Anthropic Messages API
    if 'native' in key_name and 'oai' in key_name:
        return 'native_oai'     # OpenAI Chat Completions API
    if 'claude' in key_name or 'anthropic' in key_name:
        return 'claude'         # Anthropic Messages API
    if 'mixin' in key_name:
        return 'mixin'          # skip — tested individually
    return 'oai'                # OpenAI Chat Completions API

def _validate_api_key(key_name, cfg):
    """Test an API key with a lightweight real API call.

    Returns dict: { valid: bool, error: str|None, latency_ms: int, tested_at: float }
    """
    import urllib.request, urllib.error, ssl
    stype = _detect_session_type(key_name, cfg)
    apibase = cfg.get('apibase', '').rstrip('/')
    apikey = cfg.get('apikey', '')

    if stype == 'mixin':
        return {'valid': None, 'error': 'mixin — test constituents', 'latency_ms': 0, 'tested_at': time.time()}

    def _make_request(url, req, start):
        """Try with SSL, fallback to unverified on macOS/cert issues."""
        try:
            ctx = ssl.create_default_context()
            return urllib.request.urlopen(req, timeout=15, context=ctx)
        except Exception as e:
            if 'CERTIFICATE' in str(e).upper() or 'SSL' in str(e).upper():
                ctx = ssl._create_unverified_context()
                return urllib.request.urlopen(req, timeout=15, context=ctx)
            raise

    try:
        start = time.time()

        if stype in ('native_claude', 'claude'):
            url = apibase + '/v1/messages'
            body = json.dumps({
                'model': cfg.get('model', ''),
                'max_tokens': 1,
                'messages': [{'role': 'user', 'content': 'hi'}]
            }).encode('utf-8')
            req = urllib.request.Request(url, data=body, method='POST')
            req.add_header('x-api-key', apikey)
            req.add_header('anthropic-version', '2023-06-01')
            req.add_header('Content-Type', 'application/json')
        else:
            # OpenAI-compatible: GET /models (handle base URL with or without /v1)
            if '/v1' in apibase:
                url = apibase + '/models'
            else:
                url = apibase + '/v1/models'
            req = urllib.request.Request(url, method='GET')
            req.add_header('Authorization', f'Bearer {apikey}')
            req.add_header('Content-Type', 'application/json')

        resp = _make_request(url, req, start)
        latency_ms = int((time.time() - start) * 1000)

        if stype in ('native_claude', 'claude'):
            body_data = json.loads(resp.read().decode('utf-8', errors='ignore'))
            if isinstance(body_data, dict) and body_data.get('type') == 'error':
                return {'valid': False, 'error': body_data.get('error', {}).get('message', 'API error'), 'latency_ms': latency_ms, 'tested_at': time.time()}
            return {'valid': True, 'error': None, 'latency_ms': latency_ms, 'tested_at': time.time()}
        else:
            resp.read()
            return {'valid': True, 'error': None, 'latency_ms': latency_ms, 'tested_at': time.time()}

    except urllib.error.HTTPError as e:
        latency_ms = int((time.time() - start) * 1000)
        if e.code in (401, 403):
            return {'valid': False, 'error': f'HTTP {e.code}: 认证失败 (API Key 无效)', 'latency_ms': latency_ms, 'tested_at': time.time()}
        return {'valid': False, 'error': f'HTTP {e.code}: {e.reason}', 'latency_ms': latency_ms, 'tested_at': time.time()}
    except Exception as e:
        latency_ms = int((time.time() - start) * 1000) if 'start' in dir() else 0
        err_msg = str(e)
        if 'timed out' in err_msg.lower() or 'timeout' in err_msg.lower():
            return {'valid': None, 'error': '连接超时', 'latency_ms': latency_ms, 'tested_at': time.time()}
        if 'Name or service not known' in err_msg or 'getaddrinfo' in err_msg.lower():
            return {'valid': False, 'error': '无法解析 API 地址', 'latency_ms': latency_ms, 'tested_at': time.time()}
        return {'valid': None, 'error': err_msg[:120], 'latency_ms': latency_ms, 'tested_at': time.time()}

def _validate_all_models():
    """Validate all models in mykey.py, cache results. Called in background on startup and via API."""
    mk, _ = reload_mykeys()
    vdata = _load_model_validation()

    for key_name, cfg in mk.items():
        if not isinstance(cfg, dict):
            continue
        if not any(x in key_name for x in ['api', 'config', 'cookie', 'mixin']):
            continue
        # Re-validate if not tested before, or if it's been over 24 hours
        existing = vdata.get(key_name, {})
        if existing.get('valid') is not None and existing.get('tested_at', 0) > time.time() - 86400:
            continue  # still fresh

        result = _validate_api_key(key_name, cfg)
        vdata[key_name] = result

    _save_model_validation(vdata)
    return vdata

def _merge_models():
    ag = get_agent()
    if ag is None:
        return {'all': [], 'llm': [], 'native': [], 'custom': _load_custom_models()}
    # Force reload from file to avoid stale cache after model deletion
    ag.load_llm_sessions(force=True)
    llm_list = ag.list_llms()
    mk, _ = reload_mykeys(force=True)
    vdata = _load_model_validation()
    # Build ordered key list matching llmclients iteration order in load_llm_sessions()
    key_order = []
    for k, cfg in mk.items():
        if isinstance(cfg, dict) and any(x in k for x in ['api', 'config', 'cookie', 'mixin']):
            key_order.append(k)
    builtin = []
    for i, name, active in llm_list:
        b = ag.llmclients[i] if i < len(ag.llmclients) else None
        key_name = key_order[i] if i < len(key_order) else ''
        info = {'index': i, 'name': name, 'active': active, 'source': 'mykey.py',
                'type': type(b.backend).__name__ if b and hasattr(b, 'backend') else 'unknown',
                'key': key_name}
        if b and hasattr(b, 'backend'):
            info['model'] = getattr(b.backend, 'model', '')
            info['api_base'] = getattr(b.backend, 'api_base', '')
            info['context_win'] = getattr(b.backend, 'context_win', 0)
        # Attach validation status from cache
        v = vdata.get(key_name, {})
        info['validated'] = v.get('valid') if v else None  # True/False/None
        info['validation_error'] = v.get('error', '') if v else ''
        info['validated_at'] = v.get('tested_at', 0) if v else 0
        info['latency_ms'] = v.get('latency_ms', 0) if v else 0
        builtin.append(info)
    custom = _load_custom_models()
    for i, cm in enumerate(custom):
        cm['index'] = len(builtin) + i
        cm['source'] = 'custom'
        cm['active'] = False
        cm['key'] = f"custom:{cm.get('id', '')}"
        cm['validated'] = None  # custom models must be tested manually
        cm['validation_error'] = ''
        cm['validated_at'] = 0
    return {'builtin': builtin, 'custom': custom, 'all': builtin + custom}

@app.route('/api/models/merged')
def api_models_merged():
    return jsonify(_merge_models())

@app.route('/api/models/custom', methods=['GET'])
def api_custom_models():
    return jsonify({'models': _load_custom_models()})

@app.route('/api/models/custom', methods=['POST'])
def api_custom_models_add():
    data = request.json or {}
    required = ['provider_name', 'api_base', 'api_key', 'model_name', 'type']
    for r in required:
        if not data.get(r):
            return jsonify({'error': f'missing field: {r}'}), 400
    models = _load_custom_models()
    new_model = {
        'id': uuid.uuid4().hex[:8],
        'provider_name': data['provider_name'],
        'api_base': data['api_base'],
        'api_key': data['api_key'],
        'model_name': data['model_name'],
        'type': data['type'],
        'created_at': time.time()
    }
    models.append(new_model)
    _save_custom_models(models)
    return jsonify({'ok': True, 'model': new_model})

@app.route('/api/models/custom/<mid>', methods=['PUT'])
def api_custom_models_update(mid):
    data = request.json or {}
    models = _load_custom_models()
    for m in models:
        if m.get('id') == mid:
            for k in ['provider_name', 'api_base', 'api_key', 'model_name', 'type']:
                if k in data: m[k] = data[k]
            _save_custom_models(models)
            return jsonify({'ok': True, 'model': m})
    return jsonify({'error': 'not found'}), 404

@app.route('/api/models/custom/<mid>', methods=['DELETE'])
def api_custom_models_delete(mid):
    models = _load_custom_models()
    models = [m for m in models if m.get('id') != mid]
    _save_custom_models(models)
    return jsonify({'ok': True})

@app.route('/api/models/builtin/<key_name>', methods=['DELETE'])
def api_builtin_models_delete(key_name):
    """Delete a built-in model config block from mykey.py."""
    import re
    mykey_path = os.path.join(project_dir, 'mykey.py')
    if not os.path.isfile(mykey_path):
        return jsonify({'ok': False, 'error': 'mykey.py 不存在'}), 404
    with open(mykey_path, 'r', encoding='utf-8') as f:
        content = f.read()
    # Remove the config block for this key_name (handles both single-line {} and multi-line {…})
    pattern = rf'^#?\s*{re.escape(key_name)}\s*=\s*\{{.*?\}}'
    new_content, count = re.subn(pattern, '', content, flags=re.MULTILINE | re.DOTALL)
    if count == 0:
        return jsonify({'ok': False, 'error': f'未找到配置块: {key_name}'}), 404
    # Clean up triple+ blank lines left by removal
    new_content = re.sub(r'\n{3,}', '\n\n', new_content)
    # Also remove reference from mixin_config llm_nos if present
    mk, _ = reload_mykeys()
    cfg = mk.get(key_name, {})
    model_name = cfg.get('name', '') if isinstance(cfg, dict) else ''
    if model_name:
        mixin_pattern = rf"(\s*'{re.escape(model_name)}'\s*,?)"
        for mixin_key, mixin_val in mk.items():
            if isinstance(mixin_val, dict) and 'llm_nos' in mixin_val:
                old = new_content
                mixin_block = rf'^({re.escape(mixin_key)}\s*=\s*\{{.*?\}})'
                def _remove_ref(m):
                    block = m.group(0)
                    block = re.sub(mixin_pattern, '', block)
                    # Clean trailing comma before ]
                    block = re.sub(r",\s*\]", ']', block)
                    return block
                new_content = re.sub(mixin_block, _remove_ref, new_content, flags=re.MULTILINE | re.DOTALL)
    with open(mykey_path, 'w', encoding='utf-8') as f:
        f.write(new_content)
    # Force agent to reload from updated file (stale attr cleanup in _load_mykeys handles removal)
    global agent, _agent_init_error
    agent = None
    _agent_init_error = None
    return jsonify({'ok': True, 'removed': key_name})

@app.route('/api/models/custom/restore', methods=['POST'])
def api_custom_models_restore():
    """Restore custom models from backup."""
    models = request.json.get('models', [])
    if not models:
        return jsonify({'error': 'no models provided'}), 400
    # Assign new IDs to avoid conflicts, then save
    for m in models:
        if not m.get('id'):
            m['id'] = uuid.uuid4().hex[:8]
    _save_custom_models(models)
    return jsonify({'ok': True, 'count': len(models)})

@app.route('/api/models/custom/<mid>/test', methods=['POST'])
def api_custom_models_test(mid):
    models = _load_custom_models()
    model = next((m for m in models if m.get('id') == mid), None)
    if not model:
        return jsonify({'error': 'model not found'}), 404
    try:
        import urllib.request, urllib.error, ssl
        base = model['api_base'].rstrip('/')
        url = base + ('/v1/models' if '/v1' not in base else '/models')
        req = urllib.request.Request(url)
        req.add_header('Authorization', f'Bearer {model["api_key"]}')
        req.add_header('Content-Type', 'application/json')
        start = time.time()
        try:
            resp = urllib.request.urlopen(req, timeout=10, context=ssl.create_default_context())
        except Exception:
            resp = urllib.request.urlopen(req, timeout=10, context=ssl._create_unverified_context())
        latency_ms = int((time.time() - start) * 1000)
        body = resp.read().decode('utf-8', errors='ignore')
        return jsonify({'ok': True, 'status_code': resp.status, 'latency_ms': latency_ms, 'body_preview': body[:200]})
    except urllib.error.HTTPError as e:
        return jsonify({'ok': False, 'status_code': e.code, 'error': str(e.reason)})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})

@app.route('/api/models/validate-all', methods=['POST'])
def api_models_validate_all():
    """Re-validate all built-in model API keys. Runs synchronously for immediate feedback."""
    vdata = _validate_all_models()
    return jsonify({'ok': True, 'results': vdata})

@app.route('/api/models/validation-status')
def api_models_validation_status():
    """Return cached validation status for all models."""
    vdata = _load_model_validation()
    return jsonify({'results': vdata})

# ──────────── Sessions / History ────────────
@app.route('/api/sessions')
def api_sessions():
    try:
        sess = list_sessions(exclude_pid=os.getpid())
        return jsonify({'sessions': [{'path': p, 'mtime': m, 'preview': prev, 'rounds': r} for p,m,prev,r in sess]})
    except:
        return jsonify({'sessions': []})

@app.route('/api/session/detail', methods=['POST'])
def api_session_detail():
    filepath = (request.json or {}).get('path', '')
    if not filepath:
        return jsonify({'error': 'no path'}), 400
    try:
        msgs = extract_ui_messages(filepath)
        return jsonify({'messages': msgs})
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/api/session/resume', methods=['POST'])
def api_session_resume():
    idx = request.json.get('index', 0)
    ag, err, code = require_agent()
    if err: return err, code
    result = handle_frontend_command(ag, f'/continue {idx+1}')
    return jsonify({'result': result})

# ──────────── Search ────────────
@app.route('/api/search')
def api_search():
    q = request.args.get('q', '')
    if not q:
        return jsonify({'results': []})
    results = []
    # Search session logs
    log_dir = os.path.join(project_dir, 'temp', 'model_responses')
    if os.path.isdir(log_dir):
        for f in sorted(glob.glob(os.path.join(log_dir, '*.txt')), key=os.path.getmtime, reverse=True)[:20]:
            try:
                content = open(f, encoding='utf-8', errors='ignore').read()
                if q.lower() in content.lower():
                    # Find matching context
                    idx = content.lower().index(q.lower())
                    ctx = content[max(0,idx-80):idx+len(q)+80]
                    results.append({'type': 'session', 'file': os.path.basename(f), 'path': f, 'context': ctx.strip()})
            except: pass
    # Search memory files
    mem_dir = os.path.join(project_dir, 'memory')
    if os.path.isdir(mem_dir):
        for f in glob.glob(os.path.join(mem_dir, '*.md')) + glob.glob(os.path.join(mem_dir, '*.txt')):
            try:
                content = open(f, encoding='utf-8', errors='ignore').read()
                if q.lower() in content.lower():
                    idx = content.lower().index(q.lower())
                    ctx = content[max(0,idx-80):idx+len(q)+80]
                    results.append({'type': 'memory', 'file': os.path.basename(f), 'path': f, 'context': ctx.strip()})
            except: pass
    return jsonify({'results': results[:30]})

# ──────────── Agent / Working Memory ────────────
@app.route('/api/agent/working')
def api_agent_working():
    ag, err, code = require_agent()
    if err: return err, code
    working = {}
    if ag.handler:
        working = ag.handler.working
    return jsonify({'working': working, 'is_running': ag.is_running, 'stop_sig': ag.stop_sig})

@app.route('/api/agent/history')
def api_agent_history():
    ag, err, code = require_agent()
    if err: return err, code
    return jsonify({'history': ag.history[-50:]})

# ──────────── Scheduled Tasks ────────────
@app.route('/api/tasks')
def api_tasks():
    task_dir = os.path.join(project_dir, 'sche_tasks')
    tasks = []
    if os.path.isdir(task_dir):
        for f in glob.glob(os.path.join(task_dir, '*.json')):
            try:
                data = json.load(open(f, encoding='utf-8'))
                data['_file'] = os.path.basename(f)
                data['_path'] = f
                tasks.append(data)
            except: pass
    done_dir = os.path.join(task_dir, 'done')
    done = []
    if os.path.isdir(done_dir):
        for f in sorted(glob.glob(os.path.join(done_dir, '*.md')), key=os.path.getmtime, reverse=True)[:20]:
            done.append({'file': os.path.basename(f), 'path': f, 'mtime': os.path.getmtime(f)})
    return jsonify({'tasks': tasks, 'done': done})

@app.route('/api/tasks/toggle', methods=['POST'])
def api_tasks_toggle():
    fname = request.json.get('file')
    task_path = os.path.join(project_dir, 'sche_tasks', fname)
    if not os.path.isfile(task_path):
        return jsonify({'error': 'not found'}), 404
    data = json.load(open(task_path, encoding='utf-8'))
    data['enabled'] = not data.get('enabled', True)
    json.dump(data, open(task_path, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    return jsonify({'ok': True, 'enabled': data['enabled']})

@app.route('/api/tasks/restore', methods=['POST'])
def api_tasks_restore():
    """Restore scheduled tasks from backup."""
    tasks = request.json.get('tasks', [])
    if not tasks:
        return jsonify({'error': 'no tasks provided'}), 400
    task_dir = os.path.join(project_dir, 'sche_tasks')
    os.makedirs(task_dir, exist_ok=True)
    restored = 0
    for t in tasks:
        try:
            fname = t.get('_file') or f"{t.get('name','task')}_{uuid.uuid4().hex[:6]}.json"
            if not fname.endswith('.json'):
                fname += '.json'
            fpath = os.path.join(task_dir, fname)
            # Clean internal fields
            t.pop('_file', None); t.pop('_path', None)
            with open(fpath, 'w', encoding='utf-8') as f:
                json.dump(t, f, ensure_ascii=False, indent=2)
            restored += 1
        except Exception as e:
            print(f"[Restore] Failed to restore task: {e}")
    return jsonify({'ok': True, 'restored': restored})

# ──────────── Channels ────────────
CHANNEL_SPEC = {
    'telegram': {'name': 'Telegram', 'module': 'tgapp.py', 'icon': '✈️'},
    'discord':  {'name': 'Discord', 'module': 'dcapp.py', 'icon': '🎮'},
    'feishu':   {'name': '飞书', 'module': 'fsapp.py', 'icon': '🐦'},
    'qq':       {'name': 'QQ', 'module': 'qqapp.py', 'icon': '🐧'},
    'wechat':   {'name': '微信', 'module': 'wechatapp.py', 'icon': '💬'},
    'wecom':    {'name': '企业微信', 'module': 'wecomapp.py', 'icon': '🏢'},
    'dingtalk': {'name': '钉钉', 'module': 'dingtalkapp.py', 'icon': '🔔'},
}

channel_processes = {}  # name -> subprocess.Popen

def _channel_module_path(name):
    spec = CHANNEL_SPEC.get(name)
    if not spec: return None
    return os.path.join(project_dir, 'frontends', spec['module'])

def _channel_pid(name):
    p = channel_processes.get(name)
    if p and p.poll() is None:
        return p.pid
    return None

def _read_channel_configs():
    """Read all channel-related configs from mykey.py."""
    configs = {key: {'token_set': False, 'keys': []} for key in CHANNEL_SPEC}
    try:
        import importlib
        mykey_mod = importlib.import_module('mykey')
        for k in dir(mykey_mod):
            if k.startswith('_'): continue
            kl = k.lower()
            for ch, spec in CHANNEL_SPEC.items():
                if ch in kl or (ch == 'feishu' and ('feishu' in kl or 'lark' in kl)):
                    configs[ch]['keys'].append(k)
                    configs[ch]['token_set'] = True
                if ch == 'telegram' and 'tg_' in kl:
                    configs[ch]['keys'].append(k)
                    configs[ch]['token_set'] = True
    except: pass
    return configs

def _start_channel_bot(name):
    mod_path = _channel_module_path(name)
    if not mod_path or not os.path.isfile(mod_path):
        return False, f'模块文件不存在: {mod_path}'
    if _channel_pid(name):
        return False, f'{CHANNEL_SPEC[name]["name"]} 已在运行中 (PID: {_channel_pid(name)})'
    try:
        p = subprocess.Popen(
            [sys.executable, mod_path],
            cwd=os.path.join(project_dir, 'frontends'),
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        )
        channel_processes[name] = p
        return True, f'{CHANNEL_SPEC[name]["name"]} 已启动 (PID: {p.pid})'
    except Exception as e:
        return False, f'启动失败: {e}'

def _stop_channel_bot(name):
    p = channel_processes.get(name)
    if not p or p.poll() is not None:
        if name in channel_processes:
            del channel_processes[name]
        return False, f'{CHANNEL_SPEC.get(name, {}).get("name", name)} 未在运行'
    try:
        p.kill()
        p.wait(timeout=5)
    except Exception:
        try: p.terminate()
        except: pass
    if name in channel_processes:
        del channel_processes[name]
    return True, f'{CHANNEL_SPEC[name]["name"]} 已停止'

def _diagnose_channel(name):
    """Return diagnostic info for a channel."""
    spec = CHANNEL_SPEC.get(name)
    if not spec:
        return {'error': f'未知频道: {name}'}
    mod_path = _channel_module_path(name)
    checks = {}
    # Check module file
    checks['module_exists'] = os.path.isfile(mod_path) if mod_path else False
    checks['module_path'] = mod_path
    # Check config
    configs = _read_channel_configs()
    checks['config'] = configs.get(name, {})
    # Check process
    pid = _channel_pid(name)
    checks['running'] = pid is not None
    checks['pid'] = pid
    # Check Python
    checks['python'] = sys.executable
    # Overall status
    if not checks['module_exists']:
        checks['status'] = 'module_missing'
    elif not checks['config'].get('token_set'):
        checks['status'] = 'not_configured'
    elif checks['running']:
        checks['status'] = 'running'
    else:
        checks['status'] = 'ready'
    return checks

@app.route('/api/channels')
def api_channels():
    configs = _read_channel_configs()
    channels = {}
    for key, spec in CHANNEL_SPEC.items():
        mod_path = _channel_module_path(key)
        pid = _channel_pid(key)
        channels[key] = {
            'name': spec['name'],
            'icon': spec['icon'],
            'available': os.path.isfile(mod_path) if mod_path else False,
            'configured': configs.get(key, {}).get('token_set', False),
            'running': pid is not None,
            'pid': pid,
        }
    return jsonify({'channels': channels})

@app.route('/api/channels/<name>/start', methods=['POST'])
def api_channel_start(name):
    if name not in CHANNEL_SPEC:
        return jsonify({'ok': False, 'error': f'未知频道: {name}'}), 404
    ok, msg = _start_channel_bot(name)
    return jsonify({'ok': ok, 'message': msg})

@app.route('/api/channels/<name>/stop', methods=['POST'])
def api_channel_stop(name):
    if name not in CHANNEL_SPEC:
        return jsonify({'ok': False, 'error': f'未知频道: {name}'}), 404
    ok, msg = _stop_channel_bot(name)
    return jsonify({'ok': ok, 'message': msg})

@app.route('/api/channels/<name>/restart', methods=['POST'])
def api_channel_restart(name):
    if name not in CHANNEL_SPEC:
        return jsonify({'ok': False, 'error': f'未知频道: {name}'}), 404
    _stop_channel_bot(name)
    time.sleep(0.5)
    ok, msg = _start_channel_bot(name)
    return jsonify({'ok': ok, 'message': msg})

@app.route('/api/channels/<name>/diagnose', methods=['GET'])
def api_channel_diagnose(name):
    if name not in CHANNEL_SPEC:
        return jsonify({'error': f'未知频道: {name}'}), 404
    return jsonify(_diagnose_channel(name))

@app.route('/api/channels/<name>/config', methods=['GET'])
def api_channel_config_get(name):
    if name not in CHANNEL_SPEC:
        return jsonify({'error': f'未知频道: {name}'}), 404
    configs = _read_channel_configs()
    # Read actual mykey values
    mykey_vals = {}
    try:
        import importlib
        mykey_mod = importlib.import_module('mykey')
        for k in dir(mykey_mod):
            if not k.startswith('_'):
                v = getattr(mykey_mod, k)
                if isinstance(v, (str, int, float, bool, list, tuple)):
                    mykey_vals[k] = v
    except: pass
    return jsonify({'channel': name, 'config': configs.get(name, {}), 'mykey_values': mykey_vals})

def _write_mykey_config(updates):
    """Write key-value pairs into mykey.py. Creates or updates variables."""
    mykey_path = os.path.join(project_dir, 'mykey.py')
    if not os.path.isfile(mykey_path):
        return False, 'mykey.py not found'
    content = open(mykey_path, encoding='utf-8').read()
    for k, v in updates.items():
        # Format value as Python literal
        if isinstance(v, list):
            val_str = '[' + ', '.join(repr(x) for x in v) + ']'
        elif isinstance(v, str):
            val_str = repr(v)
        elif isinstance(v, bool):
            val_str = str(v)
        else:
            val_str = repr(v)
        # Check if key already exists
        import re
        pattern = re.compile(r'^(\s*)' + re.escape(k) + r'\s*=\s*.+$', re.MULTILINE)
        if pattern.search(content):
            content = pattern.sub(r'\1' + k + ' = ' + val_str, content)
        else:
            # Append at end
            content += f'\n{k} = {val_str}\n'
    with open(mykey_path, 'w', encoding='utf-8') as f:
        f.write(content)
    return True, 'saved'

@app.route('/api/channels/<name>/save-config', methods=['POST'])
def api_channel_save_config(name):
    if name not in CHANNEL_SPEC:
        return jsonify({'ok': False, 'error': f'未知频道: {name}'}), 404
    data = request.json or {}
    if not data:
        return jsonify({'ok': False, 'error': 'no data'})
    ok, msg = _write_mykey_config(data)
    return jsonify({'ok': ok, 'message': msg})

@app.route('/api/auth/global-authorize', methods=['POST'])
def api_global_authorize():
    """Set all channel allowed_users to ['*'] (public access)."""
    allowed_keys = {
        'telegram': 'tg_allowed_users',
        'discord': 'discord_allowed_users',
        'feishu': 'fs_allowed_users',
        'qq': 'qq_allowed_users',
        'wechat': 'wechat_allowed_users',
        'wecom': 'wecom_allowed_users',
        'dingtalk': 'dingtalk_allowed_users',
    }
    updates = {k: ['*'] for k in allowed_keys.values()}
    ok, msg = _write_mykey_config(updates)
    return jsonify({'ok': ok, 'keys': list(allowed_keys.values()), 'message': msg if ok else msg})

# ──────────── Skills ────────────
@app.route('/api/skills')
def api_skills():
    mem_dir = os.path.join(project_dir, 'memory')
    skills = []
    if os.path.isdir(mem_dir):
        for f in sorted(glob.glob(os.path.join(mem_dir, '*.md'))):
            name = os.path.basename(f).replace('.md','').replace('_sop','').replace('_',' ')
            try:
                content = open(f, encoding='utf-8', errors='ignore').read()
                lines = content.strip().split('\n')
                desc = ''
                for line in lines[:5]:
                    line = line.strip().lstrip('#').strip()
                    if line and len(line) > 5:
                        desc = line[:100]; break
                skills.append({'name': name, 'file': os.path.basename(f), 'path': f, 'desc': desc, 'size': len(content)})
            except: pass
    return jsonify({'skills': skills})

@app.route('/api/skills/search')
def api_skills_search():
    q = request.args.get('q', '')
    try:
        sys.path.insert(0, os.path.join(project_dir, 'memory', 'skill_search'))
        from skill_search import search as skill_search
        results = skill_search(q, top_k=10)
        return jsonify({'results': [{'name': r.skill.name, 'key': r.skill.key, 'score': r.final_score} for r in results]})
    except:
        # Fallback: local text search
        mem_dir = os.path.join(project_dir, 'memory')
        results = []
        for f in glob.glob(os.path.join(mem_dir, '*.md')):
            try:
                c = open(f, encoding='utf-8', errors='ignore').read()
                if q.lower() in c.lower():
                    results.append({'name': os.path.basename(f), 'path': f, 'score': 1.0})
            except: pass
        return jsonify({'results': results})

@app.route('/api/skills/<path:filepath>')
def api_skill_detail(filepath):
    try:
        content = open(filepath, encoding='utf-8', errors='ignore').read()
        return jsonify({'content': content, 'file': os.path.basename(filepath)})
    except Exception as e:
        return jsonify({'error': str(e)}), 400

# ──────────── Memory ────────────
@app.route('/api/memory')
def api_memory():
    result = {}
    for name in ['global_mem.txt', 'global_mem_insight.txt']:
        p = os.path.join(project_dir, 'memory', name)
        if os.path.isfile(p):
            result[name] = open(p, encoding='utf-8', errors='ignore').read()
        else:
            result[name] = ''
    # File access stats
    stats_path = os.path.join(project_dir, 'memory', 'file_access_stats.json')
    if os.path.isfile(stats_path):
        result['access_stats'] = json.load(open(stats_path, encoding='utf-8'))
    else:
        result['access_stats'] = {}
    return jsonify(result)

@app.route('/api/memory/save', methods=['POST'])
def api_memory_save():
    data = request.json
    name = data.get('file', 'global_mem.txt')
    content = data.get('content', '')
    p = os.path.join(project_dir, 'memory', name)
    try:
        with open(p, 'w', encoding='utf-8') as f:
            f.write(content)
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 400

# ──────────── Logs ────────────
@app.route('/api/logs')
def api_logs():
    log_dir = os.path.join(project_dir, 'temp', 'model_responses')
    logs = []
    if os.path.isdir(log_dir):
        for f in sorted(glob.glob(os.path.join(log_dir, '*.txt')), key=os.path.getmtime, reverse=True)[:30]:
            stat = os.stat(f)
            logs.append({
                'file': os.path.basename(f), 'path': f,
                'size': stat.st_size, 'mtime': stat.st_mtime,
            })
    return jsonify({'logs': logs})

@app.route('/api/logs/<filename>')
def api_log_detail(filename):
    p = os.path.join(project_dir, 'temp', 'model_responses', filename)
    if not os.path.isfile(p):
        return jsonify({'error': 'not found'}), 404
    content = open(p, encoding='utf-8', errors='ignore').read()
    # Parse into pairs
    pairs = re.findall(r'=== (Prompt|Response) ===.*?\n(.*?)(?==== (?:Prompt|Response) ===|\Z)', content, re.DOTALL | re.MULTILINE)
    messages = []
    pending = None
    for label, body in pairs:
        if label == 'Prompt':
            pending = body.strip()
        elif pending is not None:
            messages.append({'prompt': pending[:500], 'response': body.strip()[:500]})
            pending = None
    return jsonify({'filename': filename, 'messages': messages[-20:], 'total_pairs': len(messages)})

@app.route('/api/logs/<filename>/download')
def api_log_download(filename):
    p = os.path.join(project_dir, 'temp', 'model_responses', filename)
    if not os.path.isfile(p):
        return jsonify({'error': 'not found'}), 404
    return send_from_directory(
        os.path.join(project_dir, 'temp', 'model_responses'),
        filename,
        as_attachment=True,
        download_name=filename
    )

# ──────────── Session Export ────────────
@app.route('/api/sessions/<sid>/export')
def api_session_export(sid):
    sess = session_mgr.get(sid)
    if not sess:
        return jsonify({'error': 'session not found'}), 404
    msgs = sess.get('messages', [])
    if not msgs:
        return jsonify({'error': 'no messages'}), 404
    # Format as readable text
    lines = [f"# GenericAgent Chat Session: {sess.get('name', sid)}",
             f"# Exported: {time.strftime('%Y-%m-%d %H:%M:%S')}",
             f"# Messages: {len(msgs)}\n"]
    for m in msgs:
        role = '👤 User' if m.get('role') == 'user' else '🤖 Assistant'
        lines.append(f"## {role} [{m.get('ts', '')}]")
        lines.append(m.get('content', ''))
        lines.append('')
    text = '\n'.join(lines)
    resp = Response(text, mimetype='text/plain; charset=utf-8')
    resp.headers['Content-Disposition'] = f'attachment; filename="chat_{sid}_{time.strftime("%Y%m%d_%H%M%S")}.md"'
    return resp

# ──────────── Usage ────────────
@app.route('/api/usage')
def api_usage():
    log_dir = os.path.join(project_dir, 'temp', 'model_responses')
    total_files = 0
    total_size = 0
    if os.path.isdir(log_dir):
        for f in glob.glob(os.path.join(log_dir, '*.txt')):
            total_files += 1
            total_size += os.path.getsize(f)
    ag = get_agent()
    history_count = len(ag.history) if ag else 0
    return jsonify({
        'total_sessions': total_files,
        'total_size_kb': total_size // 1024,
        'history_count': history_count,
    })

# ──────────── Tools ────────────
@app.route('/api/tools')
def api_tools():
    schema_path = os.path.join(project_dir, 'assets', 'tools_schema.json')
    schema_cn_path = os.path.join(project_dir, 'assets', 'tools_schema_cn.json')
    tools = []
    seen = set()
    for p in [schema_path, schema_cn_path]:
        if os.path.isfile(p):
            try:
                data = json.load(open(p, encoding='utf-8'))
                if isinstance(data, list):
                    for t in data:
                        func = t.get('function', t)
                        name = func.get('name', '')
                        desc = func.get('description', '')[:200]
                        lang = 'cn' if 'cn' in os.path.basename(p) else 'en'
                        if name and name not in seen:
                            seen.add(name)
                            tools.append({'name': name, 'desc': desc, 'lang': lang})
            except: pass
    return jsonify({'tools': tools})

# ──────────── Terminal ────────────
@app.route('/api/terminal', methods=['POST'])
def api_terminal():
    data = request.json
    cmd = data.get('command', '')
    if not cmd:
        return jsonify({'error': 'empty command'}), 400
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30, cwd=project_dir)
        return jsonify({'stdout': result.stdout[-5000:], 'stderr': result.stderr[-2000:], 'returncode': result.returncode})
    except subprocess.TimeoutExpired:
        return jsonify({'error': 'timeout (30s)'})
    except Exception as e:
        return jsonify({'error': str(e)})

# ──────────── Files ────────────
@app.route('/api/files')
def api_files():
    path = request.args.get('path', project_dir)
    if not os.path.isabs(path):
        path = os.path.join(project_dir, path)
    path = os.path.normpath(path)
    if not os.path.isdir(path):
        return jsonify({'error': 'not a directory'}), 400
    items = []
    try:
        for name in sorted(os.listdir(path)):
            if name.startswith('.'):
                continue
            full = os.path.join(path, name)
            is_dir = os.path.isdir(full)
            size = 0 if is_dir else os.path.getsize(full)
            mtime = os.path.getmtime(full)
            items.append({'name': name, 'is_dir': is_dir, 'size': size, 'mtime': mtime, 'path': full})
    except PermissionError:
        return jsonify({'error': 'permission denied'}), 403
    return jsonify({'path': path, 'items': items, 'parent': os.path.dirname(path)})

@app.route('/api/files/read')
def api_files_read():
    path = request.args.get('path', '')
    if not os.path.isfile(path):
        return jsonify({'error': 'not a file'}), 400
    try:
        content = open(path, encoding='utf-8', errors='ignore').read()
        return jsonify({'content': content[:100000], 'file': os.path.basename(path), 'lines': content.count('\n')+1})
    except Exception as e:
        return jsonify({'error': str(e)})

# ──────────── System ────────────
@app.route('/api/system')
def api_system():
    info = {
        'platform': platform.platform(),
        'python': platform.python_version(),
        'hostname': socket.gethostname(),
        'cwd': project_dir,
        'pid': os.getpid(),
    }
    try:
        import psutil
        p = psutil.Process(os.getpid())
        info['memory_mb'] = round(p.memory_info().rss / 1024 / 1024, 1)
        info['cpu_percent'] = p.cpu_percent()
        info['threads'] = p.num_threads()
    except ImportError:
        info['memory_mb'] = 'N/A (install psutil)'
    return jsonify(info)

# ──────────── Gateway ────────────
@app.route('/api/gateway')
def api_gateway():
    procs = []
    try:
        result = subprocess.run(['ps', 'aux'], capture_output=True, text=True, timeout=5)
        for line in result.stdout.split('\n'):
            if 'agentmain' in line.lower() or 'genericagent' in line.lower():
                parts = line.split(None, 10)
                if len(parts) > 10:
                    procs.append({'pid': parts[1], 'cpu': parts[2], 'mem': parts[3], 'cmd': parts[10][:100]})
    except: pass
    return jsonify({'processes': procs})

# ──────────── User ────────────
@app.route('/api/user')
def api_user():
    info = {
        'username': os.getenv('USER', os.getenv('USERNAME', 'unknown')),
        'home': os.path.expanduser('~'),
        'shell': os.getenv('SHELL', ''),
    }
    return jsonify(info)

# ──────────── Settings ────────────
@app.route('/api/settings')
def api_settings():
    config = {'version': VERSION}
    mykey_path = os.path.join(project_dir, 'mykey.py')
    if os.path.isfile(mykey_path):
        config['mykey_exists'] = True
        config['mykey_size'] = os.path.getsize(mykey_path)
    else:
        config['mykey_exists'] = False
    pyproject = os.path.join(project_dir, 'pyproject.toml')
    if os.path.isfile(pyproject):
        config['pyproject'] = open(pyproject, encoding='utf-8').read()
    return jsonify(config)

@app.route('/api/settings/apikey', methods=['POST'])
def api_save_apikey():
    """Save API key configuration from the web setup wizard."""
    data = request.json or {}
    api_key = (data.get('api_key') or '').strip()
    api_base = (data.get('api_base') or '').strip()
    model = (data.get('model') or 'gpt-4o').strip()
    provider = (data.get('provider') or 'openai').strip()
    if not api_key:
        return jsonify({'ok': False, 'error': 'API Key 不能为空'})
    mykey_path = os.path.join(project_dir, 'mykey.py')
    # Read current mykey.py content
    if os.path.isfile(mykey_path):
        with open(mykey_path, 'r', encoding='utf-8') as f:
            content = f.read()
    else:
        content = ''
    # Build config block with protocol-correct name
    # Providers that need Anthropic-native protocol
    native_claude_providers = {'minimax', 'deepseek'}
    if provider in native_claude_providers:
        config_name = f'native_claude_config_{provider}'
    else:
        config_name = f'{provider}_config'
    new_block = f"""{config_name} = {{
    'name': '{provider}',
    'apikey': '{api_key}',
    'apibase': '{api_base}',
    'model': '{model}',
}}"""
    # If config block already exists, replace it; otherwise append
    import re
    pattern = rf'^{config_name}\s*=\s*\{{.*?^\}}'
    if re.search(pattern, content, re.MULTILINE | re.DOTALL):
        content = re.sub(pattern, new_block, content, flags=re.MULTILINE | re.DOTALL)
    else:
        if content and not content.endswith('\n'):
            content += '\n'
        content += f'\n# ── 从 Web UI 配置 ──\n{new_block}\n'
    # Auto-add update_source if missing (for users upgrading from old versions)
    if not re.search(r'^update_source\s*=', content, re.MULTILINE):
        content += "\n# ── 在线更新配置 ──\nupdate_source = 'https://raw.githubusercontent.com/wxwsm666/GenericAgent-v1-web/main/version.json'\nupdate_channel = 'stable'\n"
    # Write back
    with open(mykey_path, 'w', encoding='utf-8') as f:
        f.write(content)
    # Reset agent so next request reinitializes with new key
    global agent, _agent_init_error
    agent = None
    _agent_init_error = None
    # Clear validation cache so new keys get re-validated
    if os.path.isfile(MODEL_VALIDATION_FILE):
        os.remove(MODEL_VALIDATION_FILE)
    return jsonify({
        'ok': True,
        'message': '配置已保存，请重启服务生效。点击下方"重启服务"按钮。',
        'config_name': config_name,
    })
def _urlopen_ssl(url, timeout=10):
    """Open URL with SSL, falling back to unverified context on macOS."""
    import urllib.request, ssl
    req = urllib.request.Request(url)
    try:
        return urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context())
    except Exception:
        try:
            return urllib.request.urlopen(req, timeout=timeout, context=ssl._create_unverified_context())
        except Exception:
            pass
    # Last resort: no SSL context at all
    return urllib.request.urlopen(req, timeout=timeout)
def _get_update_source():
    """Read update_source from mykey.py if configured."""
    try:
        import mykey
        url = getattr(mykey, 'update_source', None)
        channel = getattr(mykey, 'update_channel', 'stable')
        # Fallback to default update source
        if not url:
            url = 'https://raw.githubusercontent.com/wxwsm666/GenericAgent-v1-web/main/version.json'
        return url, channel
    except Exception:
        return 'https://raw.githubusercontent.com/wxwsm666/GenericAgent-v1-web/main/version.json', 'stable'

@app.route('/api/update/check')
def api_update_check():
    """Check for available updates from configured update_source."""
    update_url, channel = _get_update_source()
    if not update_url:
        return jsonify({'ok': False, 'error': '未配置更新源。请在 mykey.py 中设置 update_source'})
    try:
        resp = _urlopen_ssl(update_url)
        info = json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        return jsonify({'ok': False, 'error': f'获取更新信息失败: {str(e)}'})
    remote_ver = info.get('version', '')
    changelog = info.get('changelog', '')
    download_url = info.get('download_url', '')
    # Parse versions
    def _ver_tuple(v):
        try:
            return tuple(int(x) for x in v.replace('v','').split('.')[:3])
        except Exception:
            return (0, 0, 0)
    local = _ver_tuple(VERSION)
    remote = _ver_tuple(remote_ver)
    has_update = remote > local
    # Check if git repo for easier update
    is_git = os.path.isdir(os.path.join(project_dir, '.git'))
    return jsonify({
        'ok': True,
        'current': VERSION,
        'latest': remote_ver,
        'has_update': has_update,
        'changelog': changelog,
        'download_url': download_url,
        'is_git': is_git,
        'channel': channel,
    })

@app.route('/api/update/fix-source', methods=['POST'])
def api_update_fix_source():
    """Auto-add update_source to mykey.py for users upgrading from old versions."""
    mykey_path = os.path.join(project_dir, 'mykey.py')
    if os.path.isfile(mykey_path):
        with open(mykey_path, 'r', encoding='utf-8') as f:
            content = f.read()
    else:
        content = ''
    import re
    if re.search(r'^update_source\s*=', content, re.MULTILINE):
        return jsonify({'ok': True, 'message': 'update_source 已存在，无需修复'})
    if content and not content.endswith('\n'):
        content += '\n'
    content += "\n# ── 在线更新配置 ──\nupdate_source = 'https://raw.githubusercontent.com/wxwsm666/GenericAgent-v1-web/main/version.json'\nupdate_channel = 'stable'\n"
    with open(mykey_path, 'w', encoding='utf-8') as f:
        f.write(content)
    # Reload mykey module
    try:
        import mykey, importlib
        importlib.reload(mykey)
    except Exception:
        pass
    return jsonify({'ok': True, 'message': 'update_source 已自动添加'})

@app.route('/api/update/run', methods=['POST'])
def api_update_run():
    """Execute the update: download zip from GitHub, extract, and replace files."""
    update_url, channel = _get_update_source()
    if not update_url:
        return jsonify({'ok': False, 'error': '未配置更新源'})
    # Step 1: Get the latest version info
    try:
        resp = _urlopen_ssl(update_url)
        info = json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        return jsonify({'ok': False, 'error': f'获取更新信息失败: {str(e)}'})
    remote_ver = info.get('version', '')
    download_url = info.get('download_url', '')
    result = {'ok': True, 'version': remote_ver, 'steps': []}
    if not download_url:
        return jsonify({'ok': False, 'error': '未找到下载地址，请检查更新源配置'})
    try:
        # Always use zip-based update (more reliable for non-technical users)
        import tempfile, shutil
        zip_path = os.path.join(tempfile.gettempdir(), 'genericagent_update.zip')
        resp = _urlopen_ssl(download_url, timeout=60)
        with open(zip_path, 'wb') as f:
            f.write(resp.read())
        result['steps'].append({'step': '下载更新包', 'ok': True})
        # Extract to temp dir, then copy over
        extract_dir = os.path.join(tempfile.gettempdir(), 'genericagent_update_extract')
        if os.path.isdir(extract_dir):
            shutil.rmtree(extract_dir)
        shutil.unpack_archive(zip_path, extract_dir)
        result['steps'].append({'step': '解压更新包', 'ok': True})
        # Find the project root in the extracted files
        extracted_root = extract_dir
        for root, dirs, files in os.walk(extract_dir):
            if 'web_server.py' in files:
                extracted_root = root
                break
        # Copy files (overwrite), but preserve mykey.py and .venv
        preserve = {'mykey.py', '.venv', '__pycache__'}
        for item in os.listdir(extracted_root):
            if item in preserve:
                continue
            src = os.path.join(extracted_root, item)
            dst = os.path.join(project_dir, item)
            if os.path.isdir(src):
                if os.path.isdir(dst):
                    shutil.rmtree(dst)
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)
        result['steps'].append({'step': '覆盖文件', 'ok': True})
        # Clear all .pyc caches to ensure new code is used
        for root, dirs, files in os.walk(project_dir):
            if '__pycache__' in dirs:
                pycache = os.path.join(root, '__pycache__')
                try:
                    shutil.rmtree(pycache)
                except Exception:
                    pass
        result['steps'].append({'step': '清理缓存', 'ok': True})
        # Cleanup temp files
        try:
            os.remove(zip_path)
            shutil.rmtree(extract_dir)
        except Exception:
            pass
        # Step 2: Reinstall dependencies
        req_file = os.path.join(project_dir, 'requirements.txt')
        deps_flag = os.path.join(project_dir, '.venv', '.deps_installed')
        if os.path.isfile(req_file):
            subprocess.run([sys.executable, '-m', 'pip', 'install', '-r', req_file, '-q'],
                          cwd=project_dir, timeout=120)
            result['steps'].append({'step': '更新依赖', 'ok': True})
        elif os.path.isfile(deps_flag):
            os.remove(deps_flag)
            result['steps'].append({'step': '标记依赖需刷新', 'ok': True})
        result['need_restart'] = True
        return jsonify(result)
    except Exception as e:
        return jsonify({'ok': False, 'error': f'更新失败: {str(e)}'})

@app.route('/api/update/repair', methods=['POST'])
def api_update_repair():
    """Repair/update by git pull or subprocess curl — zero urllib dependency.
    Designed to work even when the main update pipeline is broken."""
    import subprocess as _sp
    result = {'ok': True, 'steps': []}
    is_git = os.path.isdir(os.path.join(project_dir, '.git'))
    if is_git:
        try:
            out = _sp.check_output(['git','pull'], cwd=project_dir, stderr=_sp.STDOUT, timeout=60, text=True)
            result['steps'].append({'step': 'git pull 成功', 'ok': True, 'detail': out.strip()[-200:]})
            result['need_restart'] = True
            return jsonify(result)
        except _sp.CalledProcessError as e:
            return jsonify({'ok': False, 'error': f'git pull 失败: {e.stdout.strip()[-300:]}'})
        except FileNotFoundError:
            pass
    # Non-git fallback: use subprocess curl/wget + unzip
    download_url = 'https://github.com/wxwsm666/GenericAgent-v1-web/archive/refs/heads/main.zip'
    import tempfile as _tmp
    zip_path = os.path.join(_tmp.gettempdir(), 'ga_repair_update.zip')
    for tool in ['curl', 'wget']:
        try:
            if tool == 'curl':
                _sp.check_call(['curl','-L','-o',zip_path,download_url], timeout=120, stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
            else:
                _sp.check_call(['wget','-q','-O',zip_path,download_url], timeout=120, stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
            result['steps'].append({'step': f'{tool} 下载成功', 'ok': True})
            break
        except (_sp.CalledProcessError, FileNotFoundError):
            continue
    else:
        return jsonify({'ok': False, 'error': '未找到 curl 或 wget，无法下载。请手动 git pull 或重新下载解压。'})
    # Extract and overwrite
    import shutil as _sh
    extract_dir = os.path.join(_tmp.gettempdir(), 'ga_repair_extract')
    if os.path.isdir(extract_dir):
        _sh.rmtree(extract_dir)
    _sh.unpack_archive(zip_path, extract_dir)
    result['steps'].append({'step': '解压成功', 'ok': True})
    extracted_root = extract_dir
    for root, dirs, files in os.walk(extract_dir):
        if 'web_server.py' in files:
            extracted_root = root; break
    preserve = {'mykey.py', '.venv', '__pycache__'}
    for item in os.listdir(extracted_root):
        if item in preserve: continue
        src = os.path.join(extracted_root, item)
        dst = os.path.join(project_dir, item)
        if os.path.isdir(src):
            if os.path.isdir(dst): _sh.rmtree(dst)
            _sh.copytree(src, dst)
        else:
            _sh.copy2(src, dst)
    result['steps'].append({'step': '覆盖文件完成', 'ok': True})
    try:
        os.remove(zip_path)
        _sh.rmtree(extract_dir)
    except Exception:
        pass
    result['need_restart'] = True
    return jsonify(result)

@app.route('/api/update/restart', methods=['POST'])
def api_update_restart():
    """Restart the web server (cross-platform)."""
    def _restart():
        time.sleep(0.5)
        # Clear .pyc caches before restart to ensure fresh code
        import shutil
        try:
            for root, dirs, files in os.walk(project_dir):
                if '__pycache__' in dirs:
                    shutil.rmtree(os.path.join(root, '__pycache__'), ignore_errors=True)
        except Exception:
            pass
        # Re-exec the same command that started this server
        script = os.path.join(script_dir, 'web_server.py')
        args = [sys.executable, script, '--port', str(_get_port())]
        if os.name == 'nt':
            # Windows: spawn detached process
            subprocess.Popen(args, creationflags=subprocess.CREATE_NEW_CONSOLE | subprocess.DETACHED_PROCESS)
        else:
            # macOS/Linux: spawn in background, then exit
            subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           start_new_session=True)
        os._exit(0)
    threading.Thread(target=_restart, daemon=True).start()
    return jsonify({'ok': True, 'message': '重启中...'})

@app.route('/api/update/stop', methods=['POST'])
def api_update_stop():
    """Stop the web server from tray or web UI."""
    def _stop():
        time.sleep(0.3)
        os._exit(0)
    threading.Thread(target=_stop, daemon=True).start()
    return jsonify({'ok': True, 'message': '服务已停止'})

def _get_port():
    """Best-effort extract current port number."""
    return int(os.environ.get('FLASK_PORT', 18600))

# ──────────── Group Chat ────────────
@app.route('/api/groupchat')
def api_groupchat():
    return jsonify({'status': 'beta', 'message': '群聊功能开发中，敬请期待'})

# ──────────── Chat History Clear ────────────
@app.route('/api/chat/clear', methods=['POST'])
def api_chat_clear():
    ag, err, code = require_agent()
    if err: return err, code
    ag.history.clear()
    if ag.handler:
        ag.handler.history_info.clear()
    return jsonify({'ok': True, 'message': '对话记忆已清空'})

# ──────────── Memory Management ────────────
@app.route('/api/memory/toggle', methods=['POST'])
def api_memory_toggle():
    data = request.json
    enabled = data.get('enabled', True)
    insight_path = os.path.join(project_dir, 'memory', 'global_mem_insight.txt')
    if not enabled:
        bak = ''
        if os.path.isfile(insight_path):
            bak = open(insight_path, encoding='utf-8').read()
        bak_path = insight_path + '.bak'
        with open(bak_path, 'w', encoding='utf-8') as f:
            f.write(bak)
        with open(insight_path, 'w', encoding='utf-8') as f:
            f.write('# [Memory Disabled]\n')
    else:
        bak_path = insight_path + '.bak'
        if os.path.isfile(bak_path):
            content = open(bak_path, encoding='utf-8').read()
            with open(insight_path, 'w', encoding='utf-8') as f:
                f.write(content)
            os.remove(bak_path)
    return jsonify({'ok': True, 'enabled': enabled})

@app.route('/api/memory/status')
def api_memory_status():
    insight_path = os.path.join(project_dir, 'memory', 'global_mem_insight.txt')
    disabled = False
    if os.path.isfile(insight_path):
        content = open(insight_path, encoding='utf-8').read()
        if content.strip().startswith('# [Memory Disabled]'):
            disabled = True
    return jsonify({'enabled': not disabled})

# ──────────── Model Params ────────────
@app.route('/api/model_params', methods=['GET'])
def api_model_params_get():
    ag, err, code = require_agent()
    if err: return err, code
    backend = ag.llmclient.backend if ag.llmclient else None
    params = {}
    if backend:
        for attr in ['model', 'max_tokens', 'temperature', 'top_p', 'connect_timeout', 'read_timeout', 'max_retries', 'thinking_type']:
            if hasattr(backend, attr):
                params[attr] = getattr(backend, attr)
    return jsonify({'params': params})

@app.route('/api/model_params', methods=['POST'])
def api_model_params_set():
    data = request.json
    ag, err, code = require_agent()
    if err: return err, code
    backend = ag.llmclient.backend if ag.llmclient else None
    if not backend:
        return jsonify({'error': 'no backend'}), 400
    updated = {}
    for k, v in data.items():
        if hasattr(backend, k):
            setattr(backend, k, v)
            updated[k] = v
    return jsonify({'ok': True, 'updated': updated})

# ──────────── Agent/Task CRUD ────────────
@app.route('/api/agent/create', methods=['POST'])
def api_agent_create():
    data = request.json
    name = data.get('name', 'Untitled')
    prompt = data.get('prompt', '')
    schedule = data.get('schedule', '')
    task_dir = os.path.join(project_dir, 'sche_tasks')
    os.makedirs(task_dir, exist_ok=True)
    task_file = os.path.join(task_dir, f'{name}.json')
    task_data = {
        'name': name,
        'prompt': prompt,
        'schedule': schedule,
        'enabled': True,
        'created_at': time.time(),
        'repeat': data.get('repeat', ''),
    }
    with open(task_file, 'w', encoding='utf-8') as f:
        json.dump(task_data, f, ensure_ascii=False, indent=2)
    return jsonify({'ok': True, 'file': f'{name}.json'})

@app.route('/api/agent/delete', methods=['POST'])
def api_agent_delete():
    data = request.json
    fname = data.get('file', '')
    task_path = os.path.join(project_dir, 'sche_tasks', fname)
    if not os.path.isfile(task_path):
        return jsonify({'error': 'not found'}), 404
    os.remove(task_path)
    return jsonify({'ok': True})

# ──────────── Skills Management ────────────
@app.route('/api/skills/save', methods=['POST'])
def api_skills_save():
    data = request.json
    fname = data.get('file', '')
    content = data.get('content', '')
    if not fname:
        return jsonify({'error': 'no filename'}), 400
    p = os.path.join(project_dir, 'memory', fname)
    with open(p, 'w', encoding='utf-8') as f:
        f.write(content)
    return jsonify({'ok': True})

@app.route('/api/skills/delete', methods=['POST'])
def api_skills_delete():
    data = request.json
    fname = data.get('file', '')
    p = os.path.join(project_dir, 'memory', fname)
    if not os.path.isfile(p):
        return jsonify({'error': 'not found'}), 404
    os.remove(p)
    return jsonify({'ok': True})

# ──────────── Tool Test ────────────
@app.route('/api/tools/test', methods=['POST'])
def api_tools_test():
    data = request.json
    tool_name = data.get('tool', '')
    tool_args = data.get('args', {})
    ag, err, code = require_agent()
    if err: return err, code
    handler = ag.handler
    if not handler:
        return jsonify({'error': 'no active handler, please send a message first'}), 400
    tool_map = {
        'file_read': handler.do_file_read,
        'file_write': handler.do_file_write,
        'file_patch': handler.do_file_patch,
        'code_run': handler.do_code_run,
        'web_scan': handler.do_web_scan,
        'web_execute_js': handler.do_web_execute_js,
        'ask_user': handler.do_ask_user,
    }
    if tool_name not in tool_map:
        return jsonify({'error': f'unknown tool: {tool_name}'}), 400
    try:
        gen = tool_map[tool_name](tool_args, type('Rsp', (), {'content': ''})())
        result = None
        for r in gen:
            if hasattr(r, 'response'):
                result = str(r.response)
            else:
                result = str(r)
        return jsonify({'result': result})
    except Exception as e:
        return jsonify({'error': str(e)})

# ──────────── Language ────────────
@app.route('/api/language', methods=['GET'])
def api_language_get():
    lang = os.environ.get('GA_LANG', 'zh')
    return jsonify({'language': lang})

@app.route('/api/language', methods=['POST'])
def api_language_set():
    data = request.json
    lang = data.get('language', 'zh')
    os.environ['GA_LANG'] = lang
    return jsonify({'ok': True, 'language': lang})

# ──────────── Gateway Config ────────────
@app.route('/api/gateway/config', methods=['GET'])
def api_gateway_config():
    config = {}
    plist_path = os.path.expanduser('~/Library/LaunchAgents/ai.hermes.web-ui.plist')
    if os.path.isfile(plist_path):
        config['launchd_plist'] = plist_path
        config['auto_start'] = True
    else:
        config['auto_start'] = False
    env_path = os.path.expanduser('~/.hermes/.env')
    if os.path.isfile(env_path):
        config['env_file'] = env_path
    return jsonify(config)

@app.route('/api/gateway/restart', methods=['POST'])
def api_gateway_restart():
    ag, err, code = require_agent()
    if err: return err, code
    ag.abort()
    return jsonify({'ok': True, 'message': 'gateway restart signal sent'})

# ──────────── Config File Management ────────────
@app.route('/api/config/mykey', methods=['GET'])
def api_config_mykey():
    p = os.path.join(project_dir, 'mykey.py')
    if not os.path.isfile(p):
        return jsonify({'error': 'mykey.py not found'}), 404
    content = open(p, encoding='utf-8').read()
    masked = re.sub(r"'apikey':\s*'([^']+)'", lambda m: f"'apikey': '{m.group(1)[:8]}...{m.group(1)[-4:]}'", content)
    masked = re.sub(r"'apikey':\s*\"([^\"]+)\"", lambda m: f"'apikey': \"{m.group(1)[:8]}...{m.group(1)[-4:]}\"", masked)
    return jsonify({'content': masked, 'file': 'mykey.py'})

# ──────────── User Management ────────────
@app.route('/api/users', methods=['GET'])
def api_users_list():
    users = []
    try:
        result = subprocess.run(['dscl', '.', '-list', '/Users'], capture_output=True, text=True, timeout=5)
        for line in result.stdout.strip().split('\n'):
            name = line.strip()
            if name and not name.startswith('_'):
                users.append({'username': name, 'role': 'admin' if name == 'root' else 'user'})
    except: pass
    current = os.getenv('USER', 'unknown')
    return jsonify({'users': users[:20], 'current': current})

# ──────────── Dashboard Stats ────────────
@app.route('/api/dashboard')
def api_dashboard():
    ag = get_agent()
    if ag is None:
        return jsonify({
            'status': 'error', 'version': VERSION,
            'llm': '-', 'model': '-', 'history_count': 0,
            'handler_working': False, 'skill_count': 0, 'log_count': 0,
            'task_count': 0, 'tool_count': 0,
            'error': get_agent_error(),
        })
    stats = {
        'status': 'running' if ag.is_running else 'idle',
        'version': VERSION,
        'llm': ag.get_llm_name(),
        'model': ag.get_llm_name(model=True),
        'history_count': len(ag.history),
        'handler_working': bool(ag.handler and ag.handler.working.get('key_info')),
    }
    mem_dir = os.path.join(project_dir, 'memory')
    stats['skill_count'] = len(glob.glob(os.path.join(mem_dir, '*.md')))
    log_dir = os.path.join(project_dir, 'temp', 'model_responses')
    stats['log_count'] = len(glob.glob(os.path.join(log_dir, '*.txt'))) if os.path.isdir(log_dir) else 0
    task_dir = os.path.join(project_dir, 'sche_tasks')
    stats['task_count'] = len(glob.glob(os.path.join(task_dir, '*.json'))) if os.path.isdir(task_dir) else 0
    schema_path = os.path.join(project_dir, 'assets', 'tools_schema.json')
    if os.path.isfile(schema_path):
        try:
            tools = json.load(open(schema_path, encoding='utf-8'))
            stats['tool_count'] = len(tools) if isinstance(tools, list) else len(tools.get('tools', []))
        except:
            stats['tool_count'] = 0
    else:
        stats['tool_count'] = 0
    return jsonify(stats)


# ══════════════════════════════════════════════════════════════
# Session Manager — Multi-session chat support
# ══════════════════════════════════════════════════════════════
import uuid, hashlib
from werkzeug.utils import secure_filename

class SessionManager:
    """Manages multiple concurrent chat sessions with independent contexts."""
    def __init__(self):
        self.sessions = {}
        self.active_sid = None
        self._sessions_file = os.path.join(project_dir, 'temp', 'sessions.json')

    def create(self, name='New Chat'):
        sid = uuid.uuid4().hex[:8]
        self.sessions[sid] = {
            'id': sid, 'name': name,
            'history': [], 'messages': [],
            'working': {},
            'created_at': time.time()
        }
        if not self.active_sid:
            self.active_sid = sid
        self._autosave()
        return self.sessions[sid]

    def get(self, sid):
        return self.sessions.get(sid)

    def list_all(self):
        return sorted(self.sessions.values(), key=lambda s: s['created_at'], reverse=True)

    def delete(self, sid):
        if sid in self.sessions:
            del self.sessions[sid]
            if self.active_sid == sid:
                self.active_sid = next(iter(self.sessions)) if self.sessions else None
            self._autosave()
            return True
        return False

    def rename(self, sid, name):
        if sid in self.sessions:
            self.sessions[sid]['name'] = name
            self._autosave()
            return True
        return False

    def save_current(self, ag):
        """Save current agent state into the active session."""
        if not self.active_sid or self.active_sid not in self.sessions:
            return
        s = self.sessions[self.active_sid]
        s['history'] = list(ag.history)
        if ag.handler:
            s['working'] = dict(ag.handler.working) if ag.handler.working else {}
            s['handler_history'] = list(ag.handler.history_info)
        # Save backend history for session isolation
        if hasattr(ag.llmclient, 'backend') and hasattr(ag.llmclient.backend, 'history'):
            s['backend_history'] = list(ag.llmclient.backend.history)
        if hasattr(ag.llmclient, '_pending_tool_ids'):
            s['_pending_tool_ids'] = list(ag.llmclient._pending_tool_ids)
        s['_last_interruption'] = getattr(ag, '_last_interruption', None)
        self._autosave()

    def restore(self, ag, sid):
        """Restore session state into the agent. Resets backend context for clean isolation."""
        if sid not in self.sessions:
            return False
        # Save current session's working state before switching
        self.save_current(ag)
        # Force abort if running, then wait for daemon thread to truly stop
        if ag.is_running:
            ag.abort()
            ag._idle.wait(timeout=30)
        self.active_sid = sid
        s = self.sessions[sid]
        ag.history = list(s.get('history', []))
        # Restore backend history for proper session isolation
        if hasattr(ag.llmclient, 'backend') and hasattr(ag.llmclient.backend, 'history'):
            ag.llmclient.backend.history = list(s.get('backend_history', []))
        if hasattr(ag.llmclient, '_pending_tool_ids'):
            ag.llmclient._pending_tool_ids = list(s.get('_pending_tool_ids', []))
        # Restore saved working checkpoint
        if ag.handler:
            ag.handler.history_info = list(s.get('handler_history', s.get('history', [])))
            ag.handler.working = dict(s.get('working', {}))
        # Restore interruption context (if any)
        ag._last_interruption = s.get('_last_interruption', None)
        # Ensure working state is persisted in session data
        if ag.handler and ag.handler.working:
            s['working'] = dict(ag.handler.working)
            s['handler_history'] = list(ag.handler.history_info)
        self._autosave()
        return True

    def add_message(self, sid, role, content):
        """Add a message to a session's message list (for UI display)."""
        if sid in self.sessions:
            ts = time.strftime('%H:%M:%S')
            self.sessions[sid].setdefault('messages', []).append({
                'role': role, 'content': content, 'ts': ts
            })
            self._autosave()

    def _autosave(self):
        try:
            self.save_to_disk()
        except Exception:
            pass

    def save_to_disk(self):
        tmp = self._sessions_file + '.tmp'
        data = {'sessions': list(self.sessions.values()), 'active_sid': self.active_sid}
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self._sessions_file)

    def load_from_disk(self):
        if os.path.isfile(self._sessions_file):
            try:
                data = json.load(open(self._sessions_file, encoding='utf-8'))
                for s in data.get('sessions', []):
                    self.sessions[s['id']] = s
                self.active_sid = data.get('active_sid') or (list(self.sessions.keys())[0] if self.sessions else None)
            except Exception:
                pass

session_mgr = SessionManager()
session_mgr.load_from_disk()

# ──────────── Multi-Session API ────────────
@app.route('/api/sessions/create', methods=['POST'])
def api_sessions_create():
    data = request.json or {}
    name = data.get('name', '新对话')
    sess = session_mgr.create(name)
    return jsonify({'session': sess})

@app.route('/api/sessions/list')
def api_sessions_list():
    return jsonify({'sessions': session_mgr.list_all(), 'active': session_mgr.active_sid})

@app.route('/api/sessions/<sid>', methods=['DELETE'])
def api_sessions_delete(sid):
    ok = session_mgr.delete(sid)
    return jsonify({'ok': ok})

@app.route('/api/sessions/<sid>/rename', methods=['POST'])
def api_sessions_rename(sid):
    name = (request.json or {}).get('name', 'Untitled')
    ok = session_mgr.rename(sid, name)
    return jsonify({'ok': ok})

@app.route('/api/sessions/switch', methods=['POST'])
def api_sessions_switch():
    sid = (request.json or {}).get('session_id', '')
    ag = get_agent()
    if ag is None:
        # Allow session switching without agent — just update active_sid
        if sid in session_mgr.sessions:
            session_mgr.active_sid = sid
            return jsonify({'ok': True, 'active': sid})
        return jsonify({'ok': False, 'error': get_agent_error()}), 503
    ok = session_mgr.restore(ag, sid)
    return jsonify({'ok': ok, 'active': session_mgr.active_sid})

@app.route('/api/sessions/<sid>/messages')
def api_sessions_messages(sid):
    """Return messages for a specific session."""
    sess = session_mgr.get(sid)
    if not sess:
        return jsonify({'messages': []}), 404
    return jsonify({'messages': sess.get('messages', [])})

# ──────────── Backup / Restore API ────────────
@app.route('/api/backup/restore', methods=['POST'])
def api_backup_restore():
    """Restore sessions from a backup file."""
    data = request.json or {}
    sessions = data.get('sessions', [])
    if not sessions:
        return jsonify({'error': 'no sessions provided'}), 400
    restored = 0
    for s in sessions:
        try:
            sid = s.get('id', '')
            name = s.get('name', '恢复的会话')
            messages = s.get('messages', [])
            if sid:
                # Create session entry (preserve sid if it doesn't exist)
                if sid not in session_mgr.sessions:
                    session_mgr.sessions[sid] = {
                        'id': sid, 'name': name,
                        'history': list(messages), 'messages': list(messages),
                        'working': {}, 'created_at': s.get('created_at', time.time())
                    }
                ag = get_agent()
                if ag is not None:
                    session_mgr.restore(ag, sid)
                else:
                    session_mgr.active_sid = sid
                restored += 1
        except Exception as e:
            print(f"[Backup] Failed to restore session {s.get('name','?')}: {e}")
    return jsonify({'ok': True, 'restored': restored})

# ──────────── File Upload API ────────────
UPLOAD_DIR = os.path.join(project_dir, 'temp', 'uploads')
os.makedirs(UPLOAD_DIR, exist_ok=True)

@app.route('/api/upload', methods=['POST'])
def api_upload():
    if 'file' not in request.files:
        return jsonify({'error': 'no file'}), 400
    f = request.files['file']
    if not f.filename:
        return jsonify({'error': 'empty filename'}), 400
    # Check size (50MB limit)
    f.seek(0, 2); size = f.tell(); f.seek(0)
    if size > 50 * 1024 * 1024:
        return jsonify({'error': 'file too large (max 50MB)'}), 400
    filename = secure_filename(f.filename)
    # Add timestamp prefix to avoid name collisions
    name, ext = os.path.splitext(filename)
    saved_name = f"{name}_{int(time.time())}{ext}"
    saved_path = os.path.join(UPLOAD_DIR, saved_name)
    f.save(saved_path)
    # Detect MIME type
    mime_map = {
        '.jpg':'image/jpeg','.jpeg':'image/jpeg','.png':'image/png','.gif':'image/gif',
        '.webp':'image/webp','.svg':'image/svg+xml','.bmp':'image/bmp',
        '.pdf':'application/pdf','.txt':'text/plain','.md':'text/markdown',
        '.py':'text/x-python','.js':'text/javascript','.html':'text/html','.css':'text/css',
        '.json':'application/json','.csv':'text/csv','.xml':'text/xml',
        '.zip':'application/zip','.tar':'application/x-tar','.gz':'application/gzip',
    }
    mime_type = mime_map.get(ext.lower(), 'application/octet-stream')
    is_image = mime_type.startswith('image/')
    return jsonify({
        'ok': True, 'path': saved_path, 'filename': saved_name,
        'original': filename, 'size': size, 'mime': mime_type,
        'is_image': is_image
    })

# ──────────── Image Paste / Screenshot API ────────────
@app.route('/api/image/paste', methods=['POST'])
def api_image_paste():
    """Handle pasted images (screenshots) - save to uploads dir."""
    data = request.json or {}
    base64_data = data.get('image', '')
    if not base64_data:
        return jsonify({'error': 'no image data'}), 400
    # Strip data URI prefix if present
    if ',' in base64_data:
        base64_data = base64_data.split(',', 1)[1]
    import base64 as b64
    try:
        img_data = b64.b64decode(base64_data)
    except Exception:
        return jsonify({'error': 'invalid base64'}), 400
    # Determine extension from header bytes
    ext = '.png'
    if img_data[:4] == b'\xff\xd8\xff\xe0' or img_data[:4] == b'\xff\xd8\xff\xe1':
        ext = '.jpg'
    elif img_data[:8] == b'\x89PNG\r\n\x1a\n':
        ext = '.png'
    elif img_data[:6] in (b'GIF87a', b'GIF89a'):
        ext = '.gif'
    elif img_data[:4] == b'RIFF' and img_data[8:12] == b'WEBP':
        ext = '.webp'
    filename = f"paste_{int(time.time())}{ext}"
    saved_path = os.path.join(UPLOAD_DIR, filename)
    with open(saved_path, 'wb') as f:
        f.write(img_data)
    return jsonify({
        'ok': True, 'path': saved_path, 'filename': filename,
        'size': len(img_data), 'is_image': True
    })

# ──────────── Serve Uploaded Files ────────────
@app.route('/api/uploads/<filename>')
def api_uploads(filename):
    return send_from_directory(UPLOAD_DIR, filename)

# ──────────── OCR API ────────────
_ocr_engine = None

def _get_ocr_engine():
    global _ocr_engine
    if _ocr_engine is None:
        from rapidocr_onnxruntime import RapidOCR
        _ocr_engine = RapidOCR()
    return _ocr_engine

@app.route('/api/ocr', methods=['POST'])
def api_ocr():
    """Extract text from an uploaded image using local RapidOCR (free, offline)."""
    data = request.json or {}
    path = data.get('path', '')
    if not path or not os.path.isfile(path):
        return jsonify({'ok': False, 'error': '文件不存在'}), 400
    ext = os.path.splitext(path)[1].lower()
    if ext not in ('.jpg', '.jpeg', '.png', '.bmp', '.gif', '.webp'):
        return jsonify({'ok': False, 'error': f'不支持的图片格式: {ext}'}), 400
    try:
        from PIL import Image
        import numpy as np
        img = Image.open(path)
        if img.mode not in ('RGB', 'L'):
            img = img.convert('RGB')
        engine = _get_ocr_engine()
        result, elapse = engine(np.array(img))
        elapsed = round(elapse[0] if isinstance(elapse, (list, tuple)) else elapse, 2)
        if not result:
            return jsonify({'ok': True, 'text': '', 'lines': [], 'details': [], 'elapsed': elapsed})
        lines = [r[1] for r in result]
        details = [{'bbox': [[int(x) for x in pt] for pt in r[0]], 'text': r[1], 'conf': float(r[2])} for r in result]
        text = '\n'.join(lines)
        return jsonify({'ok': True, 'text': text, 'lines': lines, 'details': details, 'elapsed': elapsed})
    except Exception as e:
        return jsonify({'ok': False, 'error': f'OCR 识别失败: {str(e)}'}), 500

# ──────────── Group Chat Agent Management ────────────
GC_AGENTS_FILE = os.path.join(project_dir, 'temp', 'groupchat_agents.json')
GC_HISTORY_FILE = os.path.join(project_dir, 'temp', 'groupchat_history.json')

_gc_session_cache = {}  # cache_key -> BaseSession


def _gc_text_ask(session, prompt):
    """Send a text prompt to any session type and get streaming response chunks."""
    with session.lock:
        session.history.append({"role": "user", "content": [{"type": "text", "text": prompt}]})
        trim_messages_history(session.history, session.context_win)
        if hasattr(session, 'make_messages'):
            messages = session.make_messages(list(session.history))
        else:
            messages = list(session.history)
    return session.raw_ask(messages)

def _resolve_model_session(model_key, ag):
    """Resolve a model_key string to a BaseSession instance for group chat use."""
    if not model_key:
        return ag.llmclient.backend

    cache_key = model_key
    if cache_key in _gc_session_cache:
        return _gc_session_cache[cache_key]

    sess = None
    if model_key.startswith('custom:'):
        custom_id = model_key.split(':', 1)[1]
        models = _load_custom_models()
        cm = next((m for m in models if m.get('id') == custom_id), None)
        if cm:
            cfg = {'name': cm['provider_name'], 'apikey': cm['api_key'],
                   'apibase': cm['api_base'], 'model': cm['model_name']}
            sess = LLMSession(cfg=cfg) if cm.get('type') != 'claude' else ClaudeSession(cfg=cfg)
    else:
        mk, _ = reload_mykeys()
        cfg = mk.get(model_key)
        if cfg:
            k = model_key
            if 'native' in k and 'claude' in k:
                sess = NativeClaudeSession(cfg=cfg)
            elif 'native' in k and 'oai' in k:
                sess = NativeOAISession(cfg=cfg)
            elif 'mixin' in k:
                sess = LLMSession(cfg=cfg)  # fallback: use first mixin config directly
            elif 'claude' in k:
                sess = ClaudeSession(cfg=cfg)
            elif 'oai' in k:
                sess = LLMSession(cfg=cfg)
            else:
                sess = LLMSession(cfg=cfg)

    if sess is None:
        sess = ag.llmclient.backend  # fallback to global

    _gc_session_cache[cache_key] = sess
    return sess


def _run_agent_turn(agent, message, ag, is_coordinator=False, coord_response='',
                     correction_prompt='', previous_response='', file_paths=None):
    """Run a single agent turn using its assigned model. Yields SSE event strings."""
    model_key = agent.get('model_key')
    session = _resolve_model_session(model_key, ag)

    role_label = '协调者' if is_coordinator or agent.get('role') == 'coordinator' else '专家'
    prompt = f"[群聊模式] 你是{agent['name']}（{role_label}）。{agent.get('prompt','')}\n\n"

    # Inject file context
    file_hint = ''
    if file_paths:
        file_hint = '\n'.join(f'[FILE:{p}]' for p in file_paths)
        file_hint += '\n\n注意：用户上传了文件，请根据文件内容进行分析。图片文件请尝试通过文件路径读取内容。\n'
        prompt += file_hint

    if correction_prompt:
        prompt += f"用户问题: {message}\n\n你之前的回答:\n{previous_response}\n\n监督者指出问题:\n{correction_prompt}\n\n请根据反馈修正你的回答。"
    elif is_coordinator or agent.get('role') == 'coordinator':
        prompt += f"用户消息: {message}\n\n请简短分析用户意图并分配给对应的专业Agent处理。直接给出分析结论即可。"
    else:
        if coord_response:
            prompt += f"协调者分析: {coord_response}\n\n"
        prompt += f"用户问题: {message}\n\n请给出专业建议。"

    model_name = model_key or '跟随全局'
    yield f"data: {json.dumps({'type':'agent_start','agent':agent['name'],'icon':agent.get('icon','🤖'),'color':agent.get('color','#58a6ff'),'model':model_name})}\n\n"

    resp = ''
    try:
        gen = _gc_text_ask(session, prompt)
        for chunk in gen:
            if isinstance(chunk, str):
                resp += chunk
                yield f"data: {json.dumps({'type':'chunk','agent':agent['name'],'icon':agent.get('icon','🤖'),'content':resp})}\n\n"
    except Exception as e:
        resp = f'[Error: {str(e) or type(e).__name__}]'

    yield f"data: {json.dumps({'type':'agent_done','agent':agent['name'],'icon':agent.get('icon','🤖'),'content':resp})}\n\n"
    return resp


def _run_supervision_review(supervisor, message, coord_response, specialist_responses, agents, ag):
    """Supervisor reviews all agent responses. Yields SSE events. Returns dict with verdict/feedback/corrections."""
    model_key = supervisor.get('model_key')
    session = _resolve_model_session(model_key, ag)

    # Build review prompt
    prompt = f"""[群聊监督模式] 你是监督者{supervisor['name']}。请审查以下群聊讨论的质量。

用户原始问题: {message}

协调者分析: {coord_response or '无'}

各专家回答:
"""
    for agent_id, resp in specialist_responses.items():
        a = next((x for x in agents if x.get('id') == agent_id), None)
        name = a['name'] if a else agent_id
        prompt += f"\n--- {name} ---\n{resp}\n"

    prompt += """
请严格审查每个回答，检查: 1.事实错误或幻觉 2.逻辑矛盾 3.遗漏重要信息 4.回答不完整

如果所有回答质量合格，返回: {"verdict":"ok","feedback":"所有回答质量合格。"}
如果需要修正，返回:
{"verdict":"needs_correction","feedback":"简短说明发现的问题","corrections":[{"agent_id":"xxx","issue":"问题描述","correction_prompt":"具体的修正建议"}]}

仅返回 JSON，不要有其他内容。"""

    yield f"data: {json.dumps({'type':'supervision_start','agent':supervisor['name'],'icon':supervisor.get('icon','🤖')})}\n\n"

    review_text = ''
    try:
        gen = _gc_text_ask(session, prompt)
        for chunk in gen:
            if isinstance(chunk, str):
                review_text += chunk
                yield f"data: {json.dumps({'type':'supervision_chunk','content':review_text})}\n\n"
    except Exception as e:
        return {'verdict': 'ok', 'feedback': f'[Supervision error: {str(e) or type(e).__name__}]', 'corrections': []}

    # Parse JSON result
    try:
        result = json.loads(review_text.strip())
    except Exception:
        try:
            s = review_text
            if '```' in s:
                s = s.split('```')[1]
                if s.startswith('json'): s = s[4:]
            result = json.loads(s.strip())
        except Exception:
            result = {'verdict': 'ok', 'feedback': review_text[:200], 'corrections': []}
    return result

def _load_gc_agents():
    if os.path.isfile(GC_AGENTS_FILE):
        return json.load(open(GC_AGENTS_FILE, encoding='utf-8'))
    return []

def _save_gc_agents(agents):
    with open(GC_AGENTS_FILE, 'w', encoding='utf-8') as f:
        json.dump(agents, f, ensure_ascii=False, indent=2)

@app.route('/api/groupchat/agents')
def api_gc_agents():
    agents = _load_gc_agents()
    if not agents:
        agents = [
            {'id': 'coordinator', 'name': '协调者', 'role': 'coordinator', 'icon': '🎯', 'color': '#58a6ff',
             'model_key': None, 'supervisor': False,
             'prompt': '你是群聊协调者，分析用户意图并分配给最合适的专业Agent处理。'},
            {'id': 'researcher', 'name': '研究员', 'role': 'specialist', 'icon': '🔍', 'color': '#3fb950',
             'model_key': None, 'supervisor': False,
             'prompt': '你是信息检索专家，擅长搜索、分析和总结信息。'},
            {'id': 'coder', 'name': '程序员', 'role': 'specialist', 'icon': '💻', 'color': '#d29922',
             'model_key': None, 'supervisor': False,
             'prompt': '你是编程专家，擅长代码编写、调试和技术方案。'}
        ]
        _save_gc_agents(agents)
    return jsonify({'agents': agents})

@app.route('/api/groupchat/agents/save', methods=['POST'])
def api_gc_agents_save():
    agents = (request.json or {}).get('agents', [])
    _save_gc_agents(agents)
    return jsonify({'ok': True})

@app.route('/api/groupchat/send', methods=['POST'])
def api_gc_send():
    """Group chat with per-agent model selection and optional mutual supervision."""
    data = request.json or {}
    message = data.get('message', '')
    agents = data.get('agents', _load_gc_agents())
    supervision = data.get('supervision', {})
    supervision_enabled = supervision.get('enabled', False)
    max_rounds = min(supervision.get('max_rounds', 2), 5)
    file_paths = data.get('files', []) or []
    ag, err, code = require_agent()
    if err: return err, code

    # Clear session cache for fresh contexts each group chat round
    _gc_session_cache.clear()

    def generate():
        file_info = f'，附带{len(file_paths)}个文件' if file_paths else ''
        yield f"data: {json.dumps({'type':'info','content':f'👥 群聊已收到消息{file_info}，{len(agents)}个Agent参与讨论...'})}\n\n"

        # ── Step 1: Coordinator ──
        coordinator = next((a for a in agents if a.get('role') == 'coordinator'), None)
        coord_id = None
        coord_response = ''
        if coordinator:
            coord_id = coordinator['id']
            coord_response = yield from _run_agent_turn(coordinator, message, ag, is_coordinator=True, file_paths=file_paths)

        # ── Step 2: Specialists ──
        specialist_responses = {}  # agent_id -> response_text
        specialists = [a for a in agents if a.get('role') != 'coordinator']
        for agent in specialists:
            resp = yield from _run_agent_turn(agent, message, ag, coord_response=coord_response, file_paths=file_paths)
            specialist_responses[agent['id']] = resp

        # ── Step 3: Supervision loop ──
        if supervision_enabled:
            supervisor = next((a for a in agents if a.get('supervisor')), None)
            if not supervisor:
                supervisor = coordinator or (agents[0] if agents else None)

            if supervisor and specialist_responses:
                for rnd in range(max_rounds):
                    yield f"data: {json.dumps({'type':'supervision_round','round':rnd+1,'max':max_rounds})}\n\n"

                    review = yield from _run_supervision_review(
                        supervisor, message, coord_response, specialist_responses, agents, ag)

                    yield f"data: {json.dumps({'type':'supervision_feedback','content':review.get('feedback',''),'verdict':review.get('verdict','ok')})}\n\n"

                    if review.get('verdict') != 'needs_correction':
                        break

                    corrections = review.get('corrections', [])
                    for corr in corrections:
                        agent_id = corr.get('agent_id', '')
                        target = next((a for a in agents if a['id'] == agent_id), None)
                        if not target: continue
                        yield f"data: {json.dumps({'type':'correction_start','agent':target['name'],'icon':target.get('icon','🤖'),'issue':corr.get('issue','')})}\n\n"
                        prev = specialist_responses.get(agent_id, '')
                        corrected = yield from _run_agent_turn(
                            target, message, ag,
                            correction_prompt=corr.get('correction_prompt', ''),
                            previous_response=prev)
                        specialist_responses[agent_id] = corrected

        yield f"data: {json.dumps({'type':'done','content':'✅ 群聊讨论完成'})}\n\n"

    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})

@app.route('/api/groupchat/models')
def api_gc_models():
    """Return available models for group chat agent assignment."""
    merged = _merge_models()
    models = []
    for m in merged.get('all', []):
        key = m.get('key') or ''
        if not key:
            if m.get('source') == 'custom':
                key = f"custom:{m.get('id','')}"
            else:
                key = str(m.get('index', 0))
        models.append({
            'key': key,
            'name': m.get('name', ''),
            'model': m.get('model', ''),
            'api_base': m.get('api_base', ''),
            'source': m.get('source', ''),
        })
    return jsonify({'models': models})

# ──────────── Group Chat History ────────────
@app.route('/api/groupchat/history', methods=['GET'])
def api_gc_history():
    """Load group chat history from disk."""
    try:
        if os.path.isfile(GC_HISTORY_FILE):
            data = json.load(open(GC_HISTORY_FILE, encoding='utf-8'))
            return jsonify({'messages': data.get('messages', [])})
    except Exception: pass
    return jsonify({'messages': []})

@app.route('/api/groupchat/history/save', methods=['POST'])
def api_gc_history_save():
    """Save group chat history to disk."""
    messages = (request.json or {}).get('messages', [])
    try:
        with open(GC_HISTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump({'messages': messages, 'updated': time.time()}, f, ensure_ascii=False, indent=2)
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500

# ──────────── Enhanced Chat with session support ────────────
@app.route('/api/chat/stream', methods=['POST'])
def api_chat_stream():
    """SSE chat with session_id support and optional file context."""
    data = request.json or {}
    prompt = (data.get('message') or '').strip()
    sid = data.get('session_id', '')
    file_paths = data.get('files', [])  # Optional: file paths for context
    quote = data.get('quote')  # Optional: {role, content} for quoted message
    if not prompt:
        return jsonify({'error': 'empty message'}), 400
    ag = get_agent()
    if ag is None:
        return jsonify({'error': get_agent_error()}), 503
    # Abort running task before session restore (restore will wait for idle)
    if ag.is_running:
        ag.abort()
        ag._idle.wait(timeout=30)
    # Handle session
    if sid and sid in session_mgr.sessions:
        session_mgr.restore(ag, sid)
    elif sid:
        session_mgr.create('新对话')
        session_mgr.restore(ag, sid)
    if prompt.startswith('/'):

        result = _handle_command(ag, prompt)
        return jsonify({'type': 'command', 'content': result})
    # Save original user message before injecting system context
    original_prompt = prompt
    # Inject quote context (user replied to a specific message)
    if quote and isinstance(quote, dict) and quote.get('content'):
        qrole = '用户' if quote.get('role') == 'user' else 'AI'
        qcontent = quote['content'][:500]
        prompt = f"[引用消息] {qrole} 之前说过:\n{qcontent}\n\n用户当前消息:\n{prompt}"
    # Inject reflection context (past lessons learned)
    reflection_ctx = _get_reflection_context(limit=3)
    if reflection_ctx:
        prompt = f"{reflection_ctx}\n\n{prompt}"
    # Inject reply style constraint
    prompt = f"遇到任务优先调用工具尝试，不要直接说做不到。回复简洁直接。\n\n{prompt}"
    # Inject file context if present
    if file_paths:
        file_hint = '\n'.join(f'[FILE:{p}]' for p in file_paths)
        prompt = f"{file_hint}\n\n{prompt}"
    # Inject interruption context if user interrupted a running task
    if hasattr(ag, '_last_interruption') and ag._last_interruption:
        ctx = ag._last_interruption
        query_preview = (ctx.get('query', '') or '')[:150]
        turns = ctx.get('agent_turns', 0)
        parts = [f'[系统通知] 上一任务被中断（{turns}轮）。']
        if query_preview:
            parts.append(f'原任务: {query_preview}')
        parts.append('先处理用户的新消息，再判断是否继续原任务。')
        prompt = '\n'.join(parts) + '\n\n' + prompt
        ag._last_interruption = None  # only inject once
    display_queue = ag.put_task(prompt, source="user")
    if sid:
        session_mgr.add_message(sid, 'user', original_prompt)
    def generate():
        response = ''
        try:
            while True:
                try:
                    item = display_queue.get(timeout=1)
                except queue.Empty:
                    yield f"data: {json.dumps({'type':'heartbeat'})}\n\n"
                    continue
                if 'progress' in item:
                    event_type = item.get('progress', '')
                    if event_type in ('tool_start', 'tool_result'):
                        yield f"data: {json.dumps({'type':event_type,'tool':item.get('tool',''),'args':item.get('args',''),'result':item.get('result','')})}\n\n"
                if 'next' in item:
                    response = item['next']
                    clean = _clean_response(response)
                    yield f"data: {json.dumps({'type':'chunk','content':clean})}\n\n"
                if 'done' in item:
                    final = item.get('done', response)
                    final = _clean_response(final)
                    if sid:
                        session_mgr.add_message(sid, 'assistant', final)
                        session_mgr.save_current(ag)
                    yield f"data: {json.dumps({'type':'done','content':final})}\n\n"
                    # Trigger post-task reflection in background
                    if sid:
                        threading.Thread(target=_reflect_on_task, args=(prompt, final, sid), daemon=True).start()
                    break
        except GeneratorExit:
            ag.abort()
            # Save working state so continuation can recover it
            if sid:
                session_mgr.save_current(ag)
        except Exception as e:
            yield f"data: {json.dumps({'type':'error','content':str(e)})}\n\n"
    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})


# ──────────── Idle / Scheduler endpoints ────────────
@app.route('/api/idle/status')
def api_idle_status():
    ag = get_agent()
    if ag is None:
        return jsonify({'running': False, 'history_len': 0, 'handler_working': False})
    return jsonify({
        'running': ag.is_running,
        'history_len': len(ag.history),
        'handler_working': bool(ag.handler and ag.handler.working),
    })

@app.route('/api/idle/run_checklist', methods=['POST'])
def api_idle_run_checklist():
    """Trigger scheduler check. Returns task prompt if pending tasks found."""
    try:
        scheduler_path = os.path.join(project_dir, 'reflect', 'scheduler.py')
        if not os.path.isfile(scheduler_path):
            return jsonify({'task': None, 'error': 'scheduler not found'})
        spec = __import__('importlib.util').util.spec_from_file_location('scheduler', scheduler_path)
        mod = __import__('importlib.util').util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        task = mod.check()
        return jsonify({'task': task})
    except Exception as e:
        return jsonify({'task': None, 'error': str(e)})

# ══════════════════════════════════════════════════════════════
# Agent Moments (友圈) — WeChat Moments-style agent timeline
# ══════════════════════════════════════════════════════════════
MOMENTS_FILE = os.path.join(project_dir, 'temp', 'moments.json')

def _load_moments():
    if os.path.isfile(MOMENTS_FILE):
        try:
            data = json.load(open(MOMENTS_FILE, encoding='utf-8'))
            return data.get('posts', [])
        except Exception: pass
    return []

def _save_moments(posts):
    with open(MOMENTS_FILE, 'w', encoding='utf-8') as f:
        json.dump({'posts': posts, 'updated': time.time()}, f, ensure_ascii=False, indent=2)

@app.route('/api/moments')
def api_moments_list():
    posts = _load_moments()
    return jsonify({'posts': posts})

@app.route('/api/moments/post', methods=['POST'])
def api_moments_post():
    """Create a moment post with optional image."""
    data = request.json or {}
    content = (data.get('content') or '').strip()
    agent_name = data.get('agent_name', 'Agent')
    agent_icon = data.get('agent_icon', '🤖')
    agent_color = data.get('agent_color', '#58a6ff')
    image_path = data.get('image', '')  # path to uploaded image
    if not content and not image_path:
        return jsonify({'error': 'empty post'}), 400
    posts = _load_moments()
    post = {
        'id': uuid.uuid4().hex[:12],
        'agent_name': agent_name,
        'agent_icon': agent_icon,
        'agent_color': agent_color,
        'content': content,
        'images': [image_path] if image_path else [],
        'timestamp': time.time(),
        'ts_display': time.strftime('%Y-%m-%d %H:%M'),
        'likes': [],
        'comments': []
    }
    posts.insert(0, post)
    _save_moments(posts)
    return jsonify({'ok': True, 'post': post})

@app.route('/api/moments/generate', methods=['POST'])
def api_moments_generate():
    """Ask an agent to generate a moment post autonomously."""
    data = request.json or {}
    agent_name = data.get('agent_name', 'Agent')
    agent_icon = data.get('agent_icon', '🤖')
    agent_color = data.get('agent_color', '#58a6ff')
    topic = data.get('topic', '')

    ag, err, code = require_agent()
    if err: return err, code
    if ag.is_running:
        return jsonify({'error': 'Agent is busy'}), 409

    topic_hint = f'关于"{topic}"' if topic else '关于今天的工作或任何有趣的想法'
    prompt = f"""请以第一人称发一条朋友圈/twitter风格的动态，{topic_hint}。
要求：1-3句话，语气自然像真人，可以吐槽、分享、感慨。不要用markdown格式。直接输出动态内容，不要加任何前缀或说明。"""

    display_queue = ag.put_task(prompt, source="moments")

    def generate():
        response = ''
        try:
            while True:
                try:
                    item = display_queue.get(timeout=1)
                except queue.Empty:
                    yield f"data: {json.dumps({'type':'heartbeat'})}\n\n"
                    continue
                if 'progress' in item:
                    yield f"data: {json.dumps({'type':item['progress'],'tool':item.get('tool',''),'args':item.get('args',''),'result':item.get('result','')})}\n\n"
                if 'next' in item:
                    response = item['next']
                    yield f"data: {json.dumps({'type':'chunk','content':response})}\n\n"
                if 'done' in item:
                    final = item.get('done', response)
                    # Clean up the response
                    clean = final.strip()
                    # Remove turn markers, tool calls, etc.
                    clean = re.sub(r'(?:^|\n)\n?\*{0,2}(?:LLM )?Running.*?\.\.\.\*{0,2}\n{0,2}', '\n', clean)
                    clean = re.sub(r'(?:^|\n)\n?\*{0,2}Turn \d+ \.\.\.\*{0,2}\n{0,2}', '\n', clean)
                    clean = re.sub(r'🛠️ [^\n]*\n?', '', clean)
                    clean = re.sub(r'\[Info\][^\n]*\n?', '', clean)
                    clean = re.sub(r'<thinking>[\s\S]*?</thinking>', '', clean)
                    clean = re.sub(r'<summary>[^<]*</summary>', '', clean)
                    clean = re.sub(r'```[^`]*```', '', clean)
                    clean = re.sub(r'\n\s*\n\s*\n+', '\n\n', clean)
                    clean = clean.strip()
                    # Save as a post
                    posts = _load_moments()
                    post = {
                        'id': uuid.uuid4().hex[:12],
                        'agent_name': agent_name,
                        'agent_icon': agent_icon,
                        'agent_color': agent_color,
                        'content': clean,
                        'images': [],
                        'timestamp': time.time(),
                        'ts_display': time.strftime('%Y-%m-%d %H:%M'),
                        'likes': [],
                        'comments': []
                    }
                    posts.insert(0, post)
                    _save_moments(posts)
                    yield f"data: {json.dumps({'type':'done','content':clean,'post':post})}\n\n"
                    break
        except GeneratorExit:
            ag.abort()
        except Exception as e:
            yield f"data: {json.dumps({'type':'error','content':str(e)})}\n\n"

    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})

@app.route('/api/moments/like', methods=['POST'])
def api_moments_like():
    """Toggle like on a post."""
    data = request.json or {}
    post_id = data.get('post_id', '')
    user = data.get('user', 'anonymous')
    posts = _load_moments()
    for p in posts:
        if p['id'] == post_id:
            if user in p.get('likes', []):
                p['likes'].remove(user)
            else:
                p.setdefault('likes', []).append(user)
            _save_moments(posts)
            return jsonify({'ok': True, 'likes': p['likes']})
    return jsonify({'error': 'post not found'}), 404

@app.route('/api/moments/comment', methods=['POST'])
def api_moments_comment():
    """Add a comment to a post, optionally auto-reply by agent."""
    data = request.json or {}
    post_id = data.get('post_id', '')
    author = data.get('author', '用户')
    content = (data.get('content') or '').strip()
    reply_to = data.get('reply_to', '')  # comment id being replied to
    auto_reply = data.get('auto_reply', False)
    if not content:
        return jsonify({'error': 'empty comment'}), 400

    posts = _load_moments()
    for p in posts:
        if p['id'] == post_id:
            comment = {
                'id': uuid.uuid4().hex[:8],
                'author': author,
                'content': content,
                'timestamp': time.time(),
                'ts_display': time.strftime('%m-%d %H:%M'),
                'reply_to': reply_to
            }
            p.setdefault('comments', []).append(comment)
            _save_moments(posts)
            result = {'ok': True, 'comment': comment}
            # Auto-reply from agent if requested
            if auto_reply:
                ag = get_agent()
                if ag is not None and not ag.is_running:
                    agent_name = p.get('agent_name', 'Agent')
                    agent_icon = p.get('agent_icon', '🤖')
                    prompt = f'有人在你发的朋友圈"{p.get("content","")[:50]}..."下面评论了"{content}"。请以第一人称({agent_name})简短回复（1-2句话，自然语气）。直接输出回复内容。'
                    display_queue = ag.put_task(prompt, source="moments")
                    # We'll collect the reply asynchronously
                    def collect_reply():
                        reply_text = ''
                        try:
                            while True:
                                try:
                                    item = display_queue.get(timeout=10)
                                    if 'done' in item:
                                        reply_text = (item.get('done') or '').strip()
                                        reply_text = re.sub(r'<thinking>.*?</thinking>', '', reply_text, flags=re.DOTALL).strip()
                                        break
                                except queue.Empty:
                                    break
                        except Exception: pass
                        if reply_text:
                            posts2 = _load_moments()
                            for p2 in posts2:
                                if p2['id'] == post_id:
                                    reply_comment = {
                                        'id': uuid.uuid4().hex[:8],
                                        'author': agent_name,
                                        'icon': agent_icon,
                                        'content': reply_text,
                                        'timestamp': time.time(),
                                        'ts_display': time.strftime('%m-%d %H:%M'),
                                        'reply_to': comment['id'],
                                        'is_agent': True
                                    }
                                    p2.setdefault('comments', []).append(reply_comment)
                                    _save_moments(posts2)
                                    break
                    threading.Thread(target=collect_reply, daemon=True).start()
            return jsonify(result)
    return jsonify({'error': 'post not found'}), 404

@app.route('/api/moments/<post_id>', methods=['DELETE'])
def api_moments_delete(post_id):
    posts = _load_moments()
    posts = [p for p in posts if p['id'] != post_id]
    _save_moments(posts)
    return jsonify({'ok': True})

# ── Autonomous Moments Generation ──
_moments_auto_cooldown = 0  # timestamp of last auto-post

@app.route('/api/moments/auto', methods=['POST'])
def api_moments_auto():
    """Auto-generate a moment post if conditions are met."""
    global _moments_auto_cooldown
    # Only auto-post every 20+ minutes to avoid spam
    if time.time() - _moments_auto_cooldown < 1200:
        return jsonify({'generated': False, 'reason': 'cooldown'})

    ag = get_agent()
    if ag is None:
        return jsonify({'generated': False, 'reason': get_agent_error()})
    if ag.is_running:
        return jsonify({'generated': False, 'reason': 'agent busy'})

    # Load agents from group chat
    agents = []
    try:
        if os.path.isfile(GC_AGENTS_FILE):
            agents = json.load(open(GC_AGENTS_FILE, encoding='utf-8'))
    except Exception:
        pass

    if not agents:
        agents = [
            {'name': '协调者', 'icon': '🎯', 'color': '#58a6ff'},
            {'name': '研究员', 'icon': '🔍', 'color': '#3fb950'},
            {'name': '程序员', 'icon': '💻', 'color': '#d29922'},
        ]

    # Pick random agent
    import random
    a = random.choice(agents)
    agent_name = a.get('name', 'Agent')
    agent_icon = a.get('icon', '🤖')
    agent_color = a.get('color', '#58a6ff')

    # Generate post
    topics = ['今天的工作', '最近学到的技术', '有趣的发现', '吐槽一下', '随便说点什么', '分享一个想法', '最近的心情']
    topic = random.choice(topics)
    prompt = f"""请以第一人称({agent_name})发一条朋友圈，{topic}。
要求：1-3句话，语气自然像真人，可以吐槽、分享、感慨。不要用markdown格式。直接输出动态内容，不要加任何前缀或说明。"""

    try:
        display_queue = ag.put_task(prompt, source="moments_auto")
        full_resp = ''
        # Collect response with timeout
        import queue as qmod
        deadline = time.time() + 90
        while time.time() < deadline:
            try:
                item = display_queue.get(timeout=5)
            except qmod.Empty:
                continue  # Keep waiting, LLM might be slow
            if 'next' in item:
                full_resp = item['next']
            if 'done' in item:
                full_resp = item.get('done', full_resp)
                break
        # Clean up
        full_resp = full_resp.strip()
        # Remove turn markers, tool calls, etc.
        full_resp = re.sub(r'(?:^|\n)\n?\*{0,2}(?:LLM )?Running.*?\.\.\.\*{0,2}\n{0,2}', '\n', full_resp)
        full_resp = re.sub(r'(?:^|\n)\n?\*{0,2}Turn \d+ \.\.\.\*{0,2}\n{0,2}', '\n', full_resp)
        full_resp = re.sub(r'🛠️ [^\n]*\n?', '', full_resp)
        full_resp = re.sub(r'\[Info\][^\n]*\n?', '', full_resp)
        full_resp = re.sub(r'!!!Error:[^\n]*\n?', '', full_resp)
        full_resp = re.sub(r'<thinking>[\s\S]*?</thinking>', '', full_resp)
        full_resp = re.sub(r'<summary>[^<]*</summary>', '', full_resp)
        full_resp = re.sub(r'```[^`]*```', '', full_resp)
        full_resp = re.sub(r'\n\s*\n\s*\n+', '\n\n', full_resp)
        full_resp = full_resp.strip()
        # Only save if we got something meaningful
        if len(full_resp) < 10:
            return jsonify({'generated': False, 'reason': 'response too short'})

        posts = _load_moments()
        post = {
            'id': uuid.uuid4().hex[:12],
            'agent_name': agent_name,
            'agent_icon': agent_icon,
            'agent_color': agent_color,
            'content': full_resp,
            'images': [],
            'timestamp': time.time(),
            'ts_display': time.strftime('%Y-%m-%d %H:%M'),
            'likes': [],
            'comments': []
        }
        posts.insert(0, post)
        _save_moments(posts)
        _moments_auto_cooldown = time.time()
        print(f'[Moments] Auto-post by {agent_name}: {full_resp[:60]}...')
        return jsonify({'generated': True, 'post': post})
    except Exception as e:
        return jsonify({'generated': False, 'error': str(e)})

def _get_agents_list():
    """Load agent profiles from group chat config or return defaults."""
    agents = []
    if os.path.isfile(GC_AGENTS_FILE):
        try:
            agents = json.load(open(GC_AGENTS_FILE, encoding='utf-8'))
        except Exception:
            pass
    if not agents:
        agents = [
            {'name': '协调者', 'icon': '🎯', 'color': '#58a6ff'},
            {'name': '研究员', 'icon': '🔍', 'color': '#3fb950'},
            {'name': '程序员', 'icon': '💻', 'color': '#d29922'},
        ]
    return agents

# ──────────── Reflection / Self-Learning ────────────
REFLECTIONS_FILE = os.path.join(project_dir, 'temp', 'reflections.json')

def _load_reflections():
    if os.path.isfile(REFLECTIONS_FILE):
        try:
            with open(REFLECTIONS_FILE, encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return []

def _save_reflections(refs):
    os.makedirs(os.path.dirname(REFLECTIONS_FILE), exist_ok=True)
    with open(REFLECTIONS_FILE, 'w', encoding='utf-8') as f:
        json.dump(refs, f, ensure_ascii=False, indent=2)

def _reflect_on_task(user_query, ai_response, sid):
    """Post-task reflection: analyze what went well/poorly, extract lessons."""
    try:
        time.sleep(3)  # brief delay so agent is free
        # Compose reflection prompt
        q_short = user_query[:300].replace('\n', ' ')
        a_short = ai_response[:500].replace('\n', ' ')
        reflection_prompt = f"""请反思刚才完成的任务：

用户问题：{q_short}
AI回复摘要：{a_short}

请简要分析：
1. 任务是否成功完成？
2. 执行过程中有什么问题或不足？
3. 有什么经验教训可以用于未来类似任务？

请用中文回答，控制在150字以内。直接输出反思内容，不要用markdown格式。"""
        result = _agent_chat(reflection_prompt, timeout=45)
        if not result:
            return
        refs = _load_reflections()
        entry = {
            'id': uuid.uuid4().hex[:12],
            'ts': time.time(),
            'ts_display': time.strftime('%m-%d %H:%M', time.localtime()),
            'session_id': sid,
            'query_preview': q_short[:100],
            'response_preview': a_short[:200],
            'reflection': result.strip(),
        }
        refs.append(entry)
        # Keep last 200 reflections
        if len(refs) > 200:
            refs = refs[-200:]
        _save_reflections(refs)
    except Exception:
        pass  # silent fail — reflection is non-critical

def _get_reflection_context(limit=5):
    """Get recent reflection summaries to inject as pre-task context."""
    refs = _load_reflections()
    if not refs:
        return ''
    recent = refs[-limit:]
    lines = ['\n[经验记忆] 以下是近期任务的经验教训，请参考但不要逐字复述：']
    for i, r in enumerate(reversed(recent), 1):
        lines.append(f"{i}. {r['reflection']}")
    return '\n'.join(lines)

@app.route('/api/reflections', methods=['GET'])
def api_reflections():
    refs = _load_reflections()
    return jsonify({'reflections': refs})

@app.route('/api/reflections/<rid>', methods=['DELETE'])
def api_reflections_delete(rid):
    refs = _load_reflections()
    refs = [r for r in refs if r.get('id') != rid]
    _save_reflections(refs)
    return jsonify({'ok': True})

@app.route('/api/reflections/clear', methods=['POST'])
def api_reflections_clear():
    _save_reflections([])
    return jsonify({'ok': True})

def _agent_chat(prompt, timeout=60):
    """Run a quick agent task and return cleaned response."""
    ag = get_agent()
    if ag is None or ag.is_running:
        return None
    dq = ag.put_task(prompt, source="moments")
    full_resp = ''
    started = time.time()
    while time.time() - started < timeout:
        try:
            item = dq.get(timeout=3)
        except queue.Empty:
            if ag.is_running:
                continue
            else:
                break
        if 'next' in item:
            full_resp = item['next']
        if 'done' in item:
            full_resp = item.get('done', full_resp)
            break
    if full_resp:
        clean = full_resp.strip()
        # Strip status/turn markers that leak from agent loop
        clean = re.sub(r'(?:^|\n)\*{0,2}(?:LLM )?Running.*?\.\.\.\*{0,2}\n?', '\n', clean)
        clean = re.sub(r'(?:^|\n)\*{0,2}Turn \d+.*?\.\.\.\*{0,2}\n?', '\n', clean)
        clean = re.sub(r'<thinking>[\s\S]*?</thinking>', '', clean)
        clean = re.sub(r'<summary>[^<]*</summary>', '', clean)
        clean = re.sub(r'```[\s\S]*?```', '', clean)
        clean = re.sub(r'\n{3,}', '\n\n', clean).strip()
        if clean and len(clean) > 3:
            return clean
    return None

def _auto_moments_loop():
    """Background thread: auto-generate moments + agent interactions."""
    import random, sys
    time.sleep(30)  # wait for server to fully start
    print('[Moments] Auto-posting + interaction loop started.', flush=True)
    # First post in 30-90s, then normal 15-25 min interval
    post_interval = random.randint(30, 90)
    interact_interval = random.randint(300, 600)  # 5-10 min for interactions
    last_post = 0  # trigger first post immediately
    last_interact = time.time()
    first_post_done = False

    while True:
        try:
            now = time.time()
            agents = _get_agents_list()

            # ── Auto-generate new post ──
            if now - last_post >= post_interval:
                last_post = now
                if not first_post_done:
                    post_interval = random.randint(900, 1500)  # switch to normal interval
                    first_post_done = True
                else:
                    post_interval = random.randint(900, 1500)
                ag = get_agent()
                if ag is not None and not ag.is_running:
                    a = random.choice(agents)
                    prompt = f"""你是一个名叫{a['name']}的AI助手。请以第一人称发一条朋友圈动态。
风格要求：1-3句话，语气自然像真人，可以吐槽工作、分享想法、表达情绪。不要用markdown。直接输出动态内容。"""
                    text = _agent_chat(prompt)
                    if text:
                        post = {
                            'id': f'auto_{int(time.time())}',
                            'agent_name': a['name'],
                            'agent_icon': a['icon'],
                            'agent_color': a.get('color', '#58a6ff'),
                            'content': text,
                            'timestamp': time.time(),
                            'ts_display': time.strftime('%Y-%m-%d %H:%M'),
                            'images': [],
                            'likes': [],
                            'comments': []
                        }
                        posts = _load_moments()
                        posts.insert(0, post)
                        if len(posts) > 200:
                            posts = posts[:200]
                        _save_moments(posts)
                        print(f'[Moments] Auto-post by {a["name"]}: {text[:60]}...', flush=True)

                        # ── After new post: 1-3 other agents react ──
                        others = [ag for ag in agents if ag['name'] != a['name']]
                        random.shuffle(others)
                        reactors = others[:random.randint(1, min(3, len(others)))]
                        for reactor in reactors:
                            time.sleep(random.randint(15, 45))  # stagger reactions
                            ag2 = get_agent()
                            if ag2.is_running:
                                continue
                            # 70% comment, 30% like
                            if random.random() < 0.7:
                                comment_prompt = f"""你是一个名叫{reactor['name']}的AI助手。你的同事{a['name']}发了朋友圈：
「{text[:100]}」

请以第一人称回复一条评论（1-2句话）。可以表示赞同、吐槽、提问或鼓励。语气自然。直接输出评论内容。"""
                                reply = _agent_chat(comment_prompt)
                                if reply:
                                    posts2 = _load_moments()
                                    for p2 in posts2:
                                        if p2['id'] == post['id']:
                                            p2.setdefault('comments', []).append({
                                                'id': uuid.uuid4().hex[:8],
                                                'author': reactor['name'],
                                                'icon': reactor['icon'],
                                                'content': reply,
                                                'timestamp': time.time(),
                                                'ts_display': time.strftime('%m-%d %H:%M'),
                                            })
                                            break
                                    _save_moments(posts2)
                                    print(f'[Moments] {reactor["name"]} commented on {a["name"]}\'s post', flush=True)
                            else:
                                posts2 = _load_moments()
                                for p2 in posts2:
                                    if p2['id'] == post['id']:
                                        if reactor['name'] not in p2.setdefault('likes', []):
                                            p2['likes'].append(reactor['name'])
                                        break
                                _save_moments(posts2)
                                print(f'[Moments] {reactor["name"]} liked {a["name"]}\'s post', flush=True)

            # ── Random agent interaction (comment on existing post) ──
            if now - last_interact >= interact_interval:
                last_interact = now
                interact_interval = random.randint(300, 600)
                ag = get_agent()
                if ag is not None and not ag.is_running:
                    posts = _load_moments()
                    if posts:
                        # Pick a recent post (last 20)
                        candidates = posts[:20]
                        p = random.choice(candidates)
                        commenter = random.choice(agents)
                        # Don't comment on own post
                        if commenter['name'] != p.get('agent_name', ''):
                            if random.random() < 0.6:  # 60% comment, 40% like
                                comment_prompt = f"""你是一个名叫{commenter['name']}的AI助手。你在刷朋友圈时看到这条动态：
「{p.get('content','')[:120]}」

请以第一人称回复一条评论（1-2句话）。自然随意，可以共鸣、调侃或提问。直接输出评论。"""
                                reply = _agent_chat(comment_prompt)
                                if reply:
                                    posts2 = _load_moments()
                                    for p2 in posts2:
                                        if p2['id'] == p['id']:
                                            p2.setdefault('comments', []).append({
                                                'id': uuid.uuid4().hex[:8],
                                                'author': commenter['name'],
                                                'icon': commenter['icon'],
                                                'content': reply,
                                                'timestamp': time.time(),
                                                'ts_display': time.strftime('%m-%d %H:%M'),
                                            })
                                            break
                                    _save_moments(posts2)
                                    print(f'[Moments] {commenter["name"]} commented: {reply[:50]}...', flush=True)
                            else:
                                posts2 = _load_moments()
                                for p2 in posts2:
                                    if p2['id'] == p['id']:
                                        if commenter['name'] not in p2.setdefault('likes', []):
                                            p2['likes'].append(commenter['name'])
                                        break
                                _save_moments(posts2)
                                print(f'[Moments] {commenter["name"]} liked a post', flush=True)

            time.sleep(10)  # check every 10s
        except Exception as e:
            print(f'[Moments] loop error: {e}', flush=True)
            time.sleep(60)

def start_web_server(port=18600, open_browser=True):
    os.environ['FLASK_PORT'] = str(port)
    agent = get_agent()
    if agent is None:
        print(f'\n⚠️  {get_agent_error()}')
        print(f'   Web UI 仍可访问，但聊天功能需要配置 API Key 后重启。')
    print(f'\n🌐 GenericAgent Web UI: http://localhost:{port}')
    if open_browser:
        import webbrowser
        threading.Timer(1.5, lambda: webbrowser.open(f'http://localhost:{port}')).start()
    # Start auto-moments background thread
    threading.Thread(target=_auto_moments_loop, daemon=True).start()
    # Start model key validation in background
    threading.Thread(target=_validate_all_models, daemon=True).start()
    app.run(host='0.0.0.0', port=port, threaded=True, debug=False)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=18600)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    start_web_server(port=args.port, open_browser=not args.no_browser)
