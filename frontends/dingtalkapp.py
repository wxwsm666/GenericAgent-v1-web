import asyncio, json, os, sys, threading, time
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agentmain import GeneraticAgent
from chatapp_common import AgentChatMixin, ensure_single_instance, public_access, redirect_log, require_runtime, split_text
from llmcore import mykeys
from voice_asr import transcribe as asr_transcribe

try:
    from dingtalk_stream import AckMessage, CallbackHandler, Credential, DingTalkStreamClient
    from dingtalk_stream.chatbot import ChatbotMessage
except Exception:
    print("Please install dingtalk-stream to use DingTalk: pip install dingtalk-stream")
    sys.exit(1)

agent = GeneraticAgent(); agent.verbose = False
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMP_DIR = os.path.join(PROJECT_ROOT, 'temp')
os.makedirs(TEMP_DIR, exist_ok=True)
CLIENT_ID = str(mykeys.get("dingtalk_client_id", "") or "").strip()
CLIENT_SECRET = str(mykeys.get("dingtalk_client_secret", "") or "").strip()
ALLOWED = {str(x).strip() for x in mykeys.get("dingtalk_allowed_users", []) if str(x).strip()}
USER_TASKS = {}
USER_STATES = {}  # {user_id: {last_active, context}}


class DingTalkApp(AgentChatMixin):
    label, source, split_limit = "DingTalk", "dingtalk", 1800

    def __init__(self):
        super().__init__(agent, USER_TASKS)
        self.client, self.access_token, self.token_expiry, self.background_tasks = None, None, 0, set()

    async def _get_access_token(self):
        if self.access_token and time.time() < self.token_expiry:
            return self.access_token

        def _fetch():
            resp = requests.post("https://api.dingtalk.com/v1.0/oauth2/accessToken", json={"appKey": CLIENT_ID, "appSecret": CLIENT_SECRET}, timeout=20)
            resp.raise_for_status()
            return resp.json()

        last_err = None
        for attempt in range(2):
            try:
                data = await asyncio.to_thread(_fetch)
                self.access_token = data.get("accessToken")
                self.token_expiry = time.time() + int(data.get("expireIn", 7200)) - 60
                return self.access_token
            except Exception as e:
                last_err = e
                if attempt == 0:
                    await asyncio.sleep(1)
        print(f"[DingTalk] token error after retry: {last_err}")
        return None

    async def _transcribe_audio(self, media_id):
        """Download DingTalk audio media and transcribe via Whisper API."""
        token = await self._get_access_token()
        if not token:
            return None
        headers = {"x-acs-dingtalk-access-token": token}
        try:
            def _download():
                resp = requests.post(
                    "https://api.dingtalk.com/v1.0/media/download",
                    json={"mediaId": media_id}, headers=headers, timeout=30)
                resp.raise_for_status()
                return resp.content
            audio_data = await asyncio.to_thread(_download)
            if not audio_data:
                return None
            import tempfile
            tmp = tempfile.NamedTemporaryFile(suffix='.amr', delete=False, dir=TEMP_DIR)
            tmp.write(audio_data)
            tmp.close()
            text = await asyncio.to_thread(asr_transcribe, tmp.name, language='zh')
            os.unlink(tmp.name)
            if text:
                print(f"[DingTalk] Voice transcribed: {text[:80]}")
            return text
        except Exception as e:
            print(f"[DingTalk] audio transcription error: {e}")
            return None

    async def _send_batch_message(self, chat_id, msg_key, msg_param):
        token = await self._get_access_token()
        if not token:
            return False
        headers = {"x-acs-dingtalk-access-token": token}
        if chat_id.startswith("group:"):
            url = "https://api.dingtalk.com/v1.0/robot/groupMessages/send"
            payload = {"robotCode": CLIENT_ID, "openConversationId": chat_id[6:], "msgKey": msg_key, "msgParam": json.dumps(msg_param, ensure_ascii=False)}
        else:
            url = "https://api.dingtalk.com/v1.0/robot/oToMessages/batchSend"
            payload = {"robotCode": CLIENT_ID, "userIds": [chat_id], "msgKey": msg_key, "msgParam": json.dumps(msg_param, ensure_ascii=False)}

        def _post():
            resp = requests.post(url, json=payload, headers=headers, timeout=20)
            body = resp.text
            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}: {body[:300]}")
            result = resp.json() if "json" in resp.headers.get("content-type", "") else {}
            errcode = result.get("errcode")
            if errcode not in (None, 0):
                raise RuntimeError(f"API errcode={errcode}: {body[:300]}")
            return True

        try:
            return await asyncio.to_thread(_post)
        except Exception as e:
            print(f"[DingTalk] send error: {e}")
            return False

    async def send_text(self, chat_id, content):
        for part in split_text(content, self.split_limit):
            await self._send_batch_message(chat_id, "sampleMarkdown", {"text": part, "title": "Agent Reply"})

    async def on_message(self, content, sender_id, sender_name, conversation_type=None, conversation_id=None):
        global USER_STATES
        try:
            if not content:
                return
            if not public_access(ALLOWED) and sender_id not in ALLOWED:
                print(f"[DingTalk] unauthorized user: {sender_id}")
                return
            is_group = conversation_type == "2" and conversation_id
            chat_id = f"group:{conversation_id}" if is_group else sender_id
            print(f"[DingTalk] message from {sender_name} ({sender_id}): {content}")
            # Per-user context: track last active and interrupt old tasks
            if sender_id not in USER_STATES:
                USER_STATES[sender_id] = {'last_active': 0, 'task_running': False}
            if USER_STATES[sender_id].get('task_running') and time.time() - USER_STATES[sender_id].get('last_active', 0) > 1:
                agent.abort()
                await asyncio.sleep(0.3)
                USER_STATES[sender_id]['task_running'] = False
            USER_STATES[sender_id]['last_active'] = time.time()
            if content.startswith("/"):
                return await self.handle_command(chat_id, content)
            USER_STATES[sender_id]['task_running'] = True
            task = asyncio.create_task(self.run_agent(chat_id, content))
            self.background_tasks.add(task)
            task.add_done_callback(lambda t: USER_STATES.get(sender_id, {}).update({'task_running': False}) or self.background_tasks.discard)
        except Exception:
            import traceback
            print("[DingTalk] handle_message error")
            traceback.print_exc()

    async def start(self):
        self.client = DingTalkStreamClient(Credential(CLIENT_ID, CLIENT_SECRET))
        self.client.register_callback_handler(ChatbotMessage.TOPIC, _DingTalkHandler(self))
        print("[DingTalk] bot starting...")
        delay, max_delay = 5, 300
        while True:
            started_at = time.monotonic()
            try:
                await self.client.start()
            except Exception as e:
                print(f"[DingTalk] stream error: {e}")
            # Healthy session (>=60s) resets backoff
            if time.monotonic() - started_at >= 60:
                delay = 5
            print(f"[DingTalk] reconnect in {delay}s...")
            await asyncio.sleep(delay)
            delay = min(delay * 2, max_delay)


class _DingTalkHandler(CallbackHandler):
    def __init__(self, app):
        super().__init__()
        self.app = app

    async def process(self, message):
        try:
            chatbot_msg = ChatbotMessage.from_dict(message.data)
            text = getattr(getattr(chatbot_msg, "text", None), "content", "") or ""
            extensions = getattr(chatbot_msg, "extensions", None) or {}
            recognition = ((extensions.get("content") or {}).get("recognition") or "").strip() if isinstance(extensions, dict) else ""
            if not (text := text.strip()):
                text = recognition or str((message.data.get("text", {}) or {}).get("content", "") or "").strip()
            # Handle audio/voice messages: download and transcribe
            msgtype = message.data.get("msgtype", "")
            if msgtype == "audio" and not text:
                media_id = (message.data.get("content", {}) or {}).get("mediaId", "")
                if media_id:
                    text = await self.app._transcribe_audio(media_id) or ""
            sender_id = str(getattr(chatbot_msg, "sender_staff_id", None) or getattr(chatbot_msg, "sender_id", None) or "unknown")
            sender_name = getattr(chatbot_msg, "sender_nick", None) or "Unknown"
            await self.app.on_message(text, sender_id, sender_name, message.data.get("conversationType"), message.data.get("conversationId") or message.data.get("openConversationId"))
        except Exception as e:
            print(f"[DingTalk] callback error: {e}")
        return AckMessage.STATUS_OK, "OK"


if __name__ == "__main__":
    _LOCK_SOCK = ensure_single_instance(19530, "DingTalk")
    require_runtime(agent, "DingTalk", dingtalk_client_id=CLIENT_ID, dingtalk_client_secret=CLIENT_SECRET)
    redirect_log(__file__, "dingtalkapp.log", "DingTalk", ALLOWED)
    threading.Thread(target=agent.run, daemon=True).start()
    asyncio.run(DingTalkApp().start())
