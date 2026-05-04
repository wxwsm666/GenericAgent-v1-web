"""GenericAgent Web UI — Full-featured dashboard with 20 sections."""
import os, sys, json, time, queue, threading, re, glob, platform, socket, subprocess, uuid, copy

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
agent_lock = threading.Lock()

def get_agent():
    global agent
    if agent is None:
        with agent_lock:
            if agent is None:
                agent = GeneraticAgent()
                if agent.llmclient is None:
                    raise RuntimeError("未配置 LLM，请设置 mykey.py")
                threading.Thread(target=agent.run, daemon=True).start()
    return agent

# ──────────── Pages ────────────
@app.route('/')
def index():
    return render_template('index.html')

# ──────────── Chat API ────────────
@app.route('/api/chat', methods=['POST'])
def api_chat():
    data = request.json
    prompt = (data.get('message') or '').strip()
    if not prompt:
        return jsonify({'error': 'empty message'}), 400
    ag = get_agent()
    if prompt.startswith('/'):
        result = _handle_command(ag, prompt)
        return jsonify({'type': 'command', 'content': result})
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
                if 'next' in item:
                    response = item['next']
                    yield f"data: {json.dumps({'type':'chunk','content':response})}\n\n"
                if 'done' in item:
                    yield f"data: {json.dumps({'type':'done','content':item.get('done',response)})}\n\n"
                    break
        except GeneratorExit:
            ag.abort()
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
    get_agent().abort()
    return jsonify({'ok': True})

# ──────────── Status ────────────
@app.route('/api/status')
def api_status():
    ag = get_agent()
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
    ag = get_agent()
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

def _load_custom_models():
    if os.path.isfile(CUSTOM_MODELS_FILE):
        try: return json.load(open(CUSTOM_MODELS_FILE, encoding='utf-8'))
        except: return []
    return []

def _save_custom_models(models):
    os.makedirs(os.path.dirname(CUSTOM_MODELS_FILE), exist_ok=True)
    with open(CUSTOM_MODELS_FILE, 'w', encoding='utf-8') as f:
        json.dump(models, f, ensure_ascii=False, indent=2)

def _merge_models():
    ag = get_agent()
    llm_list = ag.list_llms()
    mk, _ = reload_mykeys()
    # Build ordered key list matching llmclients iteration order in load_llm_sessions()
    key_order = []
    for k, cfg in mk.items():
        if isinstance(cfg, dict) and any(x in k for x in ['api', 'config', 'cookie', 'mixin']):
            key_order.append(k)
    builtin = []
    for i, name, active in llm_list:
        b = ag.llmclients[i] if i < len(ag.llmclients) else None
        info = {'index': i, 'name': name, 'active': active, 'source': 'mykey.py',
                'type': type(b.backend).__name__ if b and hasattr(b, 'backend') else 'unknown',
                'key': key_order[i] if i < len(key_order) else ''}
        if b and hasattr(b, 'backend'):
            info['model'] = getattr(b.backend, 'model', '')
            info['api_base'] = getattr(b.backend, 'api_base', '')
            info['context_win'] = getattr(b.backend, 'context_win', 0)
        builtin.append(info)
    custom = _load_custom_models()
    for i, cm in enumerate(custom):
        cm['index'] = len(builtin) + i
        cm['source'] = 'custom'
        cm['active'] = False
        cm['key'] = f"custom:{cm.get('id', '')}"
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

@app.route('/api/models/custom/<mid>/test', methods=['POST'])
def api_custom_models_test(mid):
    models = _load_custom_models()
    model = next((m for m in models if m.get('id') == mid), None)
    if not model:
        return jsonify({'error': 'model not found'}), 404
    try:
        import urllib.request, urllib.error
        base = model['api_base'].rstrip('/')
        url = base + ('/v1/models' if '/v1' not in base else '/models')
        req = urllib.request.Request(url)
        req.add_header('Authorization', f'Bearer {model["api_key"]}')
        req.add_header('Content-Type', 'application/json')
        start = time.time()
        resp = urllib.request.urlopen(req, timeout=10)
        latency_ms = int((time.time() - start) * 1000)
        body = resp.read().decode('utf-8', errors='ignore')
        return jsonify({'ok': True, 'status_code': resp.status, 'latency_ms': latency_ms, 'body_preview': body[:200]})
    except urllib.error.HTTPError as e:
        return jsonify({'ok': False, 'status_code': e.code, 'error': str(e.reason)})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})

# ──────────── Sessions / History ────────────
@app.route('/api/sessions')
def api_sessions():
    try:
        sess = list_sessions(exclude_pid=os.getpid())
        return jsonify({'sessions': [{'path': p, 'mtime': m, 'preview': prev, 'rounds': r} for p,m,prev,r in sess]})
    except:
        return jsonify({'sessions': []})

@app.route('/api/session/<path:filepath>')
def api_session_detail(filepath):
    try:
        msgs = extract_ui_messages(filepath)
        return jsonify({'messages': msgs})
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/api/session/resume', methods=['POST'])
def api_session_resume():
    idx = request.json.get('index', 0)
    ag = get_agent()
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
    ag = get_agent()
    working = {}
    if ag.handler:
        working = ag.handler.working
    return jsonify({'working': working, 'is_running': ag.is_running, 'stop_sig': ag.stop_sig})

@app.route('/api/agent/history')
def api_agent_history():
    ag = get_agent()
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
    return jsonify({'channel': name, 'config': configs.get(name, {})})

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
    history_count = len(ag.history)
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
    config = {}
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

# ──────────── Group Chat ────────────
@app.route('/api/groupchat')
def api_groupchat():
    return jsonify({'status': 'beta', 'message': '群聊功能开发中，敬请期待'})

# ──────────── Chat History Clear ────────────
@app.route('/api/chat/clear', methods=['POST'])
def api_chat_clear():
    ag = get_agent()
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
    ag = get_agent()
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
    ag = get_agent()
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
    ag = get_agent()
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
    ag = get_agent()
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
    stats = {
        'status': 'running' if ag.is_running else 'idle',
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
        self._autosave()

    def restore(self, ag, sid):
        """Restore session state into the agent. Resets backend context for clean isolation."""
        if sid not in self.sessions:
            return False
        self.save_current(ag)
        self.active_sid = sid
        s = self.sessions[sid]
        ag.history = list(s.get('history', []))
        if hasattr(ag.llmclient, 'backend') and hasattr(ag.llmclient.backend, 'history'):
            ag.llmclient.backend.history = []
        if ag.handler:
            ag.handler.history_info = list(s.get('handler_history', s.get('history', [])))
            ag.handler.working = dict(s.get('working', {}))
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
    ok = session_mgr.restore(ag, sid)
    return jsonify({'ok': ok, 'active': session_mgr.active_sid})

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

# ──────────── Group Chat Agent Management ────────────
GC_AGENTS_FILE = os.path.join(project_dir, 'temp', 'groupchat_agents.json')

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
                     correction_prompt='', previous_response=''):
    """Run a single agent turn using its assigned model. Yields SSE event strings."""
    model_key = agent.get('model_key')
    session = _resolve_model_session(model_key, ag)

    role_label = '协调者' if is_coordinator or agent.get('role') == 'coordinator' else '专家'
    prompt = f"[群聊模式] 你是{agent['name']}（{role_label}）。{agent.get('prompt','')}\n\n"

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
    ag = get_agent()

    # Clear session cache for fresh contexts each group chat round
    _gc_session_cache.clear()

    def generate():
        yield f"data: {json.dumps({'type':'info','content':f'👥 群聊已收到消息，{len(agents)}个Agent参与讨论...'})}\n\n"

        # ── Step 1: Coordinator ──
        coordinator = next((a for a in agents if a.get('role') == 'coordinator'), None)
        coord_id = None
        coord_response = ''
        if coordinator:
            coord_id = coordinator['id']
            coord_response = yield from _run_agent_turn(coordinator, message, ag, is_coordinator=True)

        # ── Step 2: Specialists ──
        specialist_responses = {}  # agent_id -> response_text
        specialists = [a for a in agents if a.get('role') != 'coordinator']
        for agent in specialists:
            resp = yield from _run_agent_turn(agent, message, ag, coord_response=coord_response)
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

# ──────────── Enhanced Chat with session support ────────────
@app.route('/api/chat/stream', methods=['POST'])
def api_chat_stream():
    """SSE chat with session_id support and optional file context."""
    data = request.json or {}
    prompt = (data.get('message') or '').strip()
    sid = data.get('session_id', '')
    file_paths = data.get('files', [])  # Optional: file paths for context
    if not prompt:
        return jsonify({'error': 'empty message'}), 400
    ag = get_agent()
    # Handle session
    if sid and sid in session_mgr.sessions:
        session_mgr.restore(ag, sid)
    elif sid:
        session_mgr.create('新对话')
        session_mgr.restore(ag, sid)
    if prompt.startswith('/'):
        result = _handle_command(ag, prompt)
        return jsonify({'type': 'command', 'content': result})
    # Inject file context if present
    if file_paths:
        file_hint = '\n'.join(f'[FILE:{p}]' for p in file_paths)
        prompt = f"{file_hint}\n\n{prompt}"
    display_queue = ag.put_task(prompt, source="user")
    if sid:
        session_mgr.add_message(sid, 'user', prompt)
    def generate():
        response = ''
        try:
            while True:
                try:
                    item = display_queue.get(timeout=1)
                except queue.Empty:
                    yield f"data: {json.dumps({'type':'heartbeat'})}\n\n"
                    continue
                if 'next' in item:
                    response = item['next']
                    yield f"data: {json.dumps({'type':'chunk','content':response})}\n\n"
                if 'done' in item:
                    final = item.get('done', response)
                    if sid:
                        session_mgr.add_message(sid, 'assistant', final)
                        session_mgr.save_current(ag)
                    yield f"data: {json.dumps({'type':'done','content':final})}\n\n"
                    break
        except GeneratorExit:
            ag.abort()
        except Exception as e:
            yield f"data: {json.dumps({'type':'error','content':str(e)})}\n\n"
    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})


def start_web_server(port=18600, open_browser=True):
    get_agent()
    print(f'\n🌐 GenericAgent Web UI: http://localhost:{port}')
    if open_browser:
        import webbrowser
        threading.Timer(1.5, lambda: webbrowser.open(f'http://localhost:{port}')).start()
    app.run(host='0.0.0.0', port=port, threaded=True, debug=False)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=18600)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    start_web_server(port=args.port, open_browser=not args.no_browser)
