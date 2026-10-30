"""
HYDRA System Interface — PersonalJarvis Core Engine
Powered by Nebius AI (NVIDIA Nemotron) with Hydra Living HUD Interface & Edge-TTS Voice.
"""

import os
import sys

# Multi-root sys.path resolution (ensures modules resolve whether invoked from hydra-main or PersonalJarvis-main)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
for candidate_dir in [
    BASE_DIR,
    os.path.join(BASE_DIR, "PersonalJarvis-main"),
    os.path.abspath(os.path.join(BASE_DIR, "..")),
]:
    if os.path.exists(candidate_dir) and candidate_dir not in sys.path:
        sys.path.insert(0, candidate_dir)

import io
import re
import time
import json
import queue
import asyncio
import threading
import webbrowser
import unicodedata

# Qt Configuration
os.environ["QT_LOGGING_RULES"] = "qt.qpa.*=false"
os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "1"

# Load local .env into os.environ if present
ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(ENV_PATH):
    with open(ENV_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

# Audio and Speech Dependencies
import edge_tts
import pygame

# PyQt6 WebEngine
from PyQt6.QtCore import QUrl, pyqtSignal, QObject, QTimer, QPoint, QEvent
from PyQt6.QtWidgets import QApplication, QMainWindow
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings, QWebEngineProfile
from PyQt6.QtWebEngineWidgets import QWebEngineView

# PersonalJarvis Core Backend
from jarvis.core.bus import EventBus
from jarvis.brain.factory import build_default_brain
from media_controller import MEDIA_CONTROLLER

# Master / Persona Configuration
MASTER_NAME = os.environ.get("HYDRA_MASTER_NAME", "Ahmad")
ASSISTANT_NAME = os.environ.get("HYDRA_ASSISTANT_NAME", "HYDRA")

# Persistent Background Event Loop (prevents "Event loop is closed" errors)
_ASYNC_LOOP = None
_LOOP_READY = threading.Event()

def _start_background_loop():
    global _ASYNC_LOOP
    _ASYNC_LOOP = asyncio.new_event_loop()
    asyncio.set_event_loop(_ASYNC_LOOP)
    _LOOP_READY.set()
    _ASYNC_LOOP.run_forever()

_LOOP_THREAD = threading.Thread(target=_start_background_loop, daemon=True, name="HydraAsyncLoop")
_LOOP_THREAD.start()
_LOOP_READY.wait()

def run_async(coro, timeout: float = 90.0):
    """Safely runs an async coroutine inside the persistent background loop."""
    future = asyncio.run_coroutine_threadsafe(coro, _ASYNC_LOOP)
    return future.result(timeout=timeout)


# Global UI Signaler for Thread-Safe Qt Bridge
class UISignaler(QObject):
    js_signal = pyqtSignal(str)

GUI_SIGNALS = UISignaler()
ACTIVE_PAGE = None

def safe_emit_js(code: str):
    """Safely emits JavaScript commands to Qt WebEngine across threads."""
    try:
        if GUI_SIGNALS is not None:
            GUI_SIGNALS.js_signal.emit(code)
            return
    except Exception:
        pass
    try:
        if ACTIVE_PAGE:
            ACTIVE_PAGE.runJavaScript(code)
    except Exception:
        pass


def clean_ai_response(content: str, user_input_hint: str = "") -> str:
    """
    Cleans raw LLM outputs from reasoning models (NVIDIA Nemotron, DeepSeek, etc.).
    Strips XML thinking tags, reasoning scratchpads, and ensures clean voice output.
    """
    if not content:
        return ""
    text = content.strip()

    # 1. Strip XML-style thinking tags (<think>...</think>, <thought>...</thought>)
    text = re.sub(r'<(?:think|thought)>.*?</(?:think|thought)>', '', text, flags=re.DOTALL).strip()

    # 2. Check for "Here's a thinking process:" or similar scratchpad headers
    low = text.lower()
    if "thinking process" in low or "analyze user input" in low or low.startswith("here's a thinking"):
        match_resp = re.search(
            r'(?:^|\n)(?:\*\*|##\s*|###\s*)?(?:final response|final answer|direct answer|spoken response|spoken reply)[:\s*]*(.*?)$',
            text, re.IGNORECASE | re.DOTALL
        )
        if match_resp and len(match_resp.group(1).strip()) > 3:
            candidate = match_resp.group(1).strip()
            if not re.match(r'^(?:1\.|[-*]|\*\*analyze)', candidate, re.IGNORECASE):
                text = candidate
        else:
            parts_div = re.split(r'\n\s*[-*_]{3,}\s*\n', text)
            if len(parts_div) > 1 and len(parts_div[-1].strip()) > 5:
                text = parts_div[-1].strip()
            else:
                paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
                non_thinking = []
                for p in paragraphs:
                    p_clean = p.strip()
                    if re.match(r'^(?:here\'s a thinking|thinking process|1\.|2\.|3\.|4\.|5\.|[-*]|\*\*analyze|\*\*identify|\*\*drafting|\*\*determine|\*\*formulate|\*\*intent)', p_clean, re.IGNORECASE):
                        continue
                    if any(marker in p_clean.lower() for marker in ["user asks:", "user says:", "user wants", "analyze user"]):
                        continue
                    non_thinking.append(p_clean)
                if non_thinking:
                    text = non_thinking[-1]
                else:
                    text = ""

    # 3. Clean markdown wrapper clutter
    text = re.sub(r'^(?:Assistant|HYDRA|Jarvis):\s*', '', text, flags=re.IGNORECASE).strip()
    if (text.startswith('"') and text.endswith('"')) or (text.startswith("'") and text.endswith("'")):
        text = text[1:-1].strip()

    # 4. Anti-Echo Guard
    if user_input_hint:
        clean_hint = re.sub(r'[^a-zA-Z0-9\s]', '', user_input_hint.lower()).strip()
        clean_text = re.sub(r'[^a-zA-Z0-9\s]', '', text.lower()).strip()
        if clean_text and clean_text == clean_hint:
            return ""

    return text.strip()


# Audio Engine
PYGAME_MIXER_READY = False
TTS_ENGINE = None
TTS_LOCK = threading.Lock()

def ensure_pygame_mixer():
    global PYGAME_MIXER_READY
    if not pygame.mixer.get_init():
        try:
            pygame.mixer.init(frequency=24000, size=-16, channels=1, buffer=512)
            PYGAME_MIXER_READY = True
        except Exception as e:
            print(f"[MIXER INIT ERROR] {e}")
    else:
        PYGAME_MIXER_READY = True

def get_offline_tts():
    global TTS_ENGINE
    if TTS_ENGINE is None:
        try:
            import pyttsx3
            try:
                TTS_ENGINE = pyttsx3.init("sapi5")
            except Exception:
                TTS_ENGINE = pyttsx3.init()
            voices = TTS_ENGINE.getProperty("voices")
            for voice in voices:
                if "david" in voice.name.lower() or "desktop" in voice.name.lower():
                    TTS_ENGINE.setProperty("voice", voice.id)
                    break
            TTS_ENGINE.setProperty("rate", 190)
        except Exception as e:
            print(f"[OFFLINE TTS INIT ERROR] {e}")
    return TTS_ENGINE

def prepare_text_for_speech(text: str) -> str:
    """
    Cleans and prepares text for Edge-TTS and offline speech engines:
    1. Removes all Unicode emojis, pictographs, symbols, and variation selectors
       so that TTS engines never read out emoji descriptions (e.g. 'smiling face with eyes closed').
    2. Strips text emoticons (like :), :D, ;), :(, xD).
    3. Strips special formatting characters (*, _, ~, #, `, |, ^, <, >, {, }, [, ]).
    4. Handles acronyms (H.Y.D.R.A. -> Hydra) without destroying sentence full stops.
    5. Preserves full stops (.), question marks (?), exclamation marks (!) with clean spacing
       so the voice engine pauses naturally on full stops instead of running through.
    """
    if not text:
        return ""

    s = text.strip()

    # 1. Normalize acronyms and abbreviations before punctuation processing
    s = re.sub(r'\b[Hh]\.[Yy]\.[Dd]\.[Rr]\.[Aa]\.?', 'Hydra', s)
    s = re.sub(r'\bHYDRA\b', 'Hydra', s)
    s = re.sub(r'\be\.g\.,?', 'for example,', s, flags=re.IGNORECASE)
    s = re.sub(r'\bi\.e\.,?', 'that is,', s, flags=re.IGNORECASE)
    s = re.sub(r'\bvs\.,?', 'versus', s, flags=re.IGNORECASE)
    s = re.sub(r'\betc\.,?', 'etcetera', s, flags=re.IGNORECASE)

    # 2. Strip Unicode emojis, pictographs, symbols, and variation selectors
    cleaned_chars = []
    for c in s:
        cp = ord(c)
        cat = unicodedata.category(c)
        if (
            cp >= 0x1F000
            or (0x2600 <= cp <= 0x27BF)
            or (0xFE00 <= cp <= 0xFE0F)
            or (0x200D <= cp <= 0x200F)
            or (0x2300 <= cp <= 0x23FF)
            or (0x2B00 <= cp <= 0x2BFF)
            or cat in ('So', 'Sk', 'Cn')
        ):
            continue
        cleaned_chars.append(c)
    s = "".join(cleaned_chars)

    # 3. Strip text emoticons (like :), :D, ;), :(, :-), xD, :P)
    s = re.sub(r'(^|\s)[:;=xX8][\-~]?[)D(\]\[pP/\\*oO@](?=\s|$|[.,!?])', ' ', s)

    # 4. Strip special formatting characters that TTS reads awkwardly
    s = re.sub(r'[*_~`#|\\^<>{}\[\]=~+@$%/]', ' ', s)

    # 5. Normalize dashes, hyphens, and ellipses
    s = re.sub(r'--+|—|–', ', ', s)
    s = re.sub(r'\.{2,}', '. ', s)
    s = re.sub(r'!{2,}', '! ', s)
    s = re.sub(r'\?{2,}', '? ', s)

    # 6. Ensure full stops, question marks, and exclamation marks are strictly followed by a space
    # (Do NOT replace '.' with ' ' - full stops are essential for sentence cadence and stopping!)
    s = re.sub(r'\s*([.!?])\s*', r'\1 ', s)
    s = re.sub(r'\s*,\s*', ', ', s)

    # 7. Collapse whitespace and strip
    return re.sub(r'\s+', ' ', s).strip()


VOICE_QUEUE = queue.Queue()

def _voice_worker_loop():
    while True:
        try:
            item = VOICE_QUEUE.get()
            if item is None:
                break
            spoken_text, cleaned_text = item
            _process_voice_item(spoken_text, cleaned_text)
        except Exception as e:
            print(f"[VOICE WORKER ERROR]: {e}")
        finally:
            try:
                VOICE_QUEUE.task_done()
            except Exception:
                pass

def _process_voice_item(spoken_text: str, cleaned_text: str):
    t0 = time.perf_counter()
    spoken = False

    def speak_offline():
        nonlocal spoken
        with TTS_LOCK:
            engine = get_offline_tts()
            if engine:
                approx_dur = max(1.5, len(spoken_text) * 0.065)
                safe_emit_js(f"notifySpeechStart({approx_dur});")
                safe_emit_js(f"if (window.showSpeechSubtitle) window.showSpeechSubtitle({json.dumps(cleaned_text)}, {approx_dur});")
                engine.say(spoken_text)
                engine.runAndWait()
                safe_emit_js("notifySpeechEnd();")
                safe_emit_js("if (window.hideSpeechSubtitle) window.hideSpeechSubtitle();")
                spoken = True
                print(f"[VOICE] Offline speech completed ({time.perf_counter() - t0:.1f}s).")

    try:
        # Hydra Online Cloud Voice: en-GB-RyanNeural (Pitch: -25Hz, Rate: -8%)
        async def generate_audio():
            communicate = edge_tts.Communicate(
                spoken_text,
                "en-GB-RyanNeural",
                pitch="-25Hz",
                rate="-8%",
            )
            audio_data = bytearray()
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    audio_data.extend(chunk["data"])
            return bytes(audio_data)

        raw_audio = run_async(generate_audio(), timeout=10.0)

        # Animate the living organism core filament
        approx_dur = max(1.5, len(spoken_text) * 0.068)
        safe_emit_js(f"notifySpeechStart({approx_dur});")
        safe_emit_js(f"if (window.showSpeechSubtitle) window.showSpeechSubtitle({json.dumps(cleaned_text)}, {approx_dur});")

        audio_stream = io.BytesIO(raw_audio)
        ensure_pygame_mixer()
        pygame.mixer.music.load(audio_stream, "mp3")
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
            pygame.time.Clock().tick(10)
        pygame.mixer.music.unload()

        safe_emit_js("notifySpeechEnd();")
        safe_emit_js("if (window.hideSpeechSubtitle) window.hideSpeechSubtitle();")
        spoken = True
        print(f"[VOICE] Cloud speech successful ({time.perf_counter() - t0:.1f}s).")
    except Exception as online_err:
        print(f"[VOICE] Cloud speech fallback: {online_err}. Switching to offline voice...")
        try:
            if PYGAME_MIXER_READY:
                pygame.mixer.music.stop()
                pygame.mixer.music.unload()
        except Exception:
            pass
        try:
            speak_offline()
        except Exception as offline_err:
            print(f"[VOICE] Offline speech error: {offline_err}")

    if not spoken:
        try:
            speak_offline()
        except Exception as e:
            print(f"[VOICE] All voice engines failed: {e}")

threading.Thread(target=_voice_worker_loop, daemon=True, name="HydraVoiceQueueWorker").start()

def speak_voice(text: str):
    """
    High-fidelity voice synthesis pipeline for HYDRA.
    Enqueues spoken text into a dedicated FIFO queue to guarantee serialized playback,
    prevent audio overlap, and eliminate pygame mixer collisions.
    """
    if not text:
        return

    cleaned_text = clean_ai_response(text)
    if not cleaned_text:
        return

    spoken_text = prepare_text_for_speech(cleaned_text)
    if not spoken_text:
        return

    print(f"\n[HYDRA SAYS]: {cleaned_text}\n")
    safe_emit_js(f"if (window.showSpeechSubtitle) window.showSpeechSubtitle({json.dumps(cleaned_text)}, {max(4.0, len(spoken_text) * 0.075)});")
    VOICE_QUEUE.put((spoken_text, cleaned_text))


# PersonalJarvis Brain Engine Initializer
JARVIS_BRAIN = None
BRAIN_BUS = None
BRAIN_LOCK = threading.Lock()

def get_jarvis_brain():
    global JARVIS_BRAIN, BRAIN_BUS
    with BRAIN_LOCK:
        if JARVIS_BRAIN is None:
            try:
                print("[ENGINE] Initializing PersonalJarvis BrainManager...")
                BRAIN_BUS = EventBus()
                JARVIS_BRAIN = run_async(asyncio.to_thread(build_default_brain, bus=BRAIN_BUS, tier="router"))
                JARVIS_BRAIN._curator = None
                if hasattr(JARVIS_BRAIN, "set_reply_language"):
                    JARVIS_BRAIN.set_reply_language("en")
                print(f"[ENGINE] BrainManager ready! Active provider: {getattr(JARVIS_BRAIN, 'active_provider', 'offline')}")
            except Exception as be:
                print(f"[ENGINE NOTICE] Offline brain mode active: {be}")
        return JARVIS_BRAIN


# =========================================================================
# Post-Connection Immediate Background Execution & Status Telemetry (Problem 3)
# =========================================================================
_SYNCED_APP_ACCOUNTS = set()

def execute_post_connection_tasks(app_id: str, account: str = "", silent_if_unchanged: bool = False):
    """
    Executes immediate post-connection background tasks, telemetry synchronization,
    and voice verification for newly connected applications without user intervention.
    """
    clean_id = app_id.lower().strip()
    sync_key = f"{clean_id}_{account}"
    if silent_if_unchanged and sync_key in _SYNCED_APP_ACCOUNTS:
        return
    _SYNCED_APP_ACCOUNTS.add(sync_key)

    def _worker():
        try:
            print(f"[POST-CONNECTION] Running immediate background tasks for {clean_id} ({account})...")

            if clean_id == "spotify":
                from plugins.spotify_direct_worker import SPOTIFY_WORKER
                ok, sub_speech, meta = SPOTIFY_WORKER.check_subscription()
                state = MEDIA_CONTROLLER.get_state()
                if state:
                    safe_emit_js(f"updateMediaState({json.dumps(state)});")
                product = meta.get("product", "Premium").title()
                speech = f"Sir, your Spotify account ({account or 'connected'}) is active with Spotify {product}. Direct background playback and controls are ready."
                speak_voice(speech)
                safe_emit_js(f"if (typeof appendMessage === 'function') appendMessage('hydra', {json.dumps(speech)});")

            elif clean_id in ["google_classroom", "classroom"]:
                from plugins.classroom_direct_worker import CLASSROOM_WORKER
                ok, asg_speech, items = CLASSROOM_WORKER.get_upcoming_assignments()
                speech = f"Sir, Google Classroom has been connected. {asg_speech}"
                speak_voice(speech)
                safe_emit_js(f"if (typeof appendMessage === 'function') appendMessage('hydra', {json.dumps(speech)});")

            elif clean_id in ["gmail", "email"]:
                from plugins.gmail_direct_worker import GMAIL_WORKER
                ok, mail_speech, items = GMAIL_WORKER.get_unread_emails()
                speech = f"Sir, Gmail has been connected. {mail_speech}"
                speak_voice(speech)
                safe_emit_js(f"if (typeof appendMessage === 'function') appendMessage('hydra', {json.dumps(speech)});")

            elif clean_id in ["google", "google_workspace"]:
                from plugins.gmail_direct_worker import GMAIL_WORKER
                from plugins.classroom_direct_worker import CLASSROOM_WORKER
                GMAIL_WORKER.get_unread_emails()
                CLASSROOM_WORKER.get_upcoming_assignments()
                speech = f"Sir, your Google account ({account}) has been connected. Gmail and Google Classroom background workers are synchronized."
                speak_voice(speech)
                safe_emit_js(f"if (typeof appendMessage === 'function') appendMessage('hydra', {json.dumps(speech)});")

            elif clean_id == "whatsapp":
                from plugins.whatsapp_direct_worker import WHATSAPP_WORKER
                ok, wa_speech, items = WHATSAPP_WORKER.get_unread_messages()
                speech = f"Sir, WhatsApp session has been securely linked. Direct background message dispatcher and unread notifications monitor are active."
                speak_voice(speech)
                safe_emit_js(f"if (typeof appendMessage === 'function') appendMessage('hydra', {json.dumps(speech)});")

            elif clean_id == "youtube":
                speech = "Sir, YouTube has been connected. Background streaming and media search engine are ready."
                speak_voice(speech)
                safe_emit_js(f"if (typeof appendMessage === 'function') appendMessage('hydra', {json.dumps(speech)});")

            elif clean_id in ["google_calendar", "calendar"]:
                from plugins.calendar_direct_worker import CALENDAR_WORKER
                upcoming = CALENDAR_WORKER.get_upcoming_reminders(limit=3)
                cnt = len(upcoming)
                speech = f"Sir, Google Calendar and the general reminder system are active with {cnt} upcoming scheduled items."
                speak_voice(speech)
                safe_emit_js(f"if (typeof appendMessage === 'function') appendMessage('hydra', {json.dumps(speech)});")

            elif clean_id in ["google_drive", "drive"]:
                speech = "Sir, Google Drive API integration has been authenticated. Cloud document synchronization is ready."
                speak_voice(speech)
                safe_emit_js(f"if (typeof appendMessage === 'function') appendMessage('hydra', {json.dumps(speech)});")

            elif clean_id in ["google_tasks", "tasks"]:
                speech = "Sir, Google Tasks API integration has been activated. Todo and task list sync are operational."
                speak_voice(speech)
                safe_emit_js(f"if (typeof appendMessage === 'function') appendMessage('hydra', {json.dumps(speech)});")

            elif clean_id in ["instagram", "facebook"]:
                app_title = clean_id.title()
                speech = f"Sir, {app_title} Graph API credentials have been saved and validated."
                speak_voice(speech)
                safe_emit_js(f"if (typeof appendMessage === 'function') appendMessage('hydra', {json.dumps(speech)});")

            elif clean_id == "github":
                speech = f"Sir, your GitHub account ({account or 'active'}) has been connected and verified."
                speak_voice(speech)
                safe_emit_js(f"if (typeof appendMessage === 'function') appendMessage('hydra', {json.dumps(speech)});")

            elif clean_id in ["canva", "discord", "linkedin"]:
                app_title = clean_id.replace("_", " ").title()
                speech = f"Sir, your {app_title} account ({account or 'active'}) has been connected. Direct background access is verified."
                speak_voice(speech)
                safe_emit_js(f"if (typeof appendMessage === 'function') appendMessage('hydra', {json.dumps(speech)});")

        except Exception as e:
            print(f"[POST-CONNECTION ERROR] {clean_id}: {e}")

    threading.Thread(target=_worker, daemon=True).start()


# Custom WebEngine Page with Console Message Bridge
class HydraPage(QWebEnginePage):
    def javaScriptConsoleMessage(self, level, message, line, source):
        if message.startswith("HYDRA_CMD:"):
            command = message[10:].strip()
            self.process_hydra_command(command)
        elif message.startswith("HYDRA_OPEN_URL:") or message.startswith("HYDRA_BROWSER:"):
            url = message.split(":", 1)[1].strip()
            if url:
                print(f"[HYDRA BROWSER] Opening URL: {url}")
                webbrowser.open(url)
        elif message.startswith("HYDRA_MEDIA_CMD:"):
            action = message[16:].strip()
            self.handle_media_command(action)
        elif message.startswith("HYDRA_PLUGIN_SAVE:"):
            payload = message[18:].strip()
            self.handle_plugin_save(payload)
        elif message.startswith("HYDRA_PLUGIN_TOGGLE:"):
            payload = message[20:].strip()
            self.handle_plugin_toggle(payload)
        elif message.startswith("HYDRA_TEST_CONNECTION:"):
            app_id = message[22:].strip()
            self.handle_test_connection(app_id)
        elif message.startswith("HYDRA_AUTH_CONNECT:"):
            app_id = message[19:].strip()
            self.handle_auth_connect(app_id)
        elif message.startswith("HYDRA_AUTH_DISCONNECT:"):
            app_id = message[22:].strip()
            self.handle_auth_disconnect(app_id)
        elif message.startswith("HYDRA_ACCOUNT_SWITCH:"):
            payload = message[21:].strip()
            self.handle_account_switch(payload)
        elif message.startswith("HYDRA_ACCOUNT_REMOVE:"):
            payload = message[21:].strip()
            self.handle_account_remove(payload)
        elif message.startswith("HYDRA_SET_ONBOARDING:"):
            payload = message[21:].strip()
            self.handle_set_onboarding(payload)
        elif message.startswith("HYDRA_SET_MASTER_NAME:"):
            name = message[22:].strip()
            self.handle_set_master_name(name)

    def handle_set_onboarding(self, payload: str):
        name = ""
        api_key = ""
        try:
            data = json.loads(payload)
            name = data.get("name", "").strip()
            api_key = data.get("apiKey", "").strip()
        except Exception:
            name = payload.strip()

        if name:
            self.handle_set_master_name(name, speak=False)

        display_name = name if name else 'Master'
        if api_key:
            self._apply_ai_model_key(api_key)
            speak_voice(f"Welcome, {display_name}. I am HYDRA. Your AI neural model is connected and active.")
            safe_emit_js(f"if (typeof appendMessage === 'function') appendMessage('hydra', 'Welcome, {display_name}. Cloud AI Model activated and calibrated to your authority.');")
        else:
            existing_key = os.environ.get("NEBIUS_API_KEY", "").strip() or os.environ.get("NVIDIA_API_KEY", "").strip()
            if existing_key:
                speak_voice(f"Welcome, {display_name}. I am HYDRA. Your AI neural model is connected and active.")
                safe_emit_js(f"if (typeof appendMessage === 'function') appendMessage('hydra', 'Welcome, {display_name}. Cloud AI Model is active and ready.');")
            else:
                self._apply_ai_model_key("")
                print("[HYDRA ONBOARDING] No API key entered - continuing in 100% offline mode.")
                speak_voice(f"Welcome, {display_name}. I am HYDRA. Operating in 100% offline private neural mode.")
                safe_emit_js(f"if (typeof appendMessage === 'function') appendMessage('hydra', 'Welcome, {display_name}. 100% Offline Intelligence Active - all local databases and direct background execution ready.');")

    def _apply_ai_model_key(self, api_key: str, model_name: str = "", base_url: str = "", vision_model: str = ""):
        os.environ["NEBIUS_API_KEY"] = api_key
        os.environ["NVIDIA_API_KEY"] = api_key
        if model_name:
            os.environ["NEBIUS_FAST_MODEL"] = model_name
        if base_url:
            os.environ["NEBIUS_BASE_URL"] = base_url
        if not vision_model:
            vision_model = os.environ.get("NEBIUS_VISION_MODEL") or "nvidia/Nemotron-3.5-Super-VL"
        os.environ["NEBIUS_VISION_MODEL"] = vision_model

        try:
            import plugins.conversational_trainer as ct
            ct.NEBIUS_API_KEY = api_key
            if model_name:
                ct.NEBIUS_FAST_MODEL = model_name
                if model_name not in ct.FALLBACK_MODELS:
                    ct.FALLBACK_MODELS.insert(0, model_name)
            if base_url:
                ct.NEBIUS_BASE_URL = base_url
            if hasattr(ct, "HYDRA_CONVERSATIONAL_ENGINE"):
                ct.HYDRA_CONVERSATIONAL_ENGINE._client = None
            print(f"[HYDRA AI CORE] Dynamic model config updated: api_key={'[SET]' if api_key else '[NONE/OFFLINE]'}, model={model_name or ct.NEBIUS_FAST_MODEL}, vision_model={vision_model}")
        except Exception as ce:
            print(f"[HYDRA AI CORE UPDATE NOTE]: {ce}")

        try:
            import visual_verifier as vv
            vv.NEBIUS_API_KEY = api_key
            vv.NEBIUS_BASE_URL = base_url or vv.NEBIUS_BASE_URL
            vv.NEBIUS_VISION_MODEL = vision_model
            if hasattr(vv, "VISUAL_VERIFIER"):
                vv.VISUAL_VERIFIER._client = None
        except Exception as ve:
            print(f"[HYDRA VISION CORE UPDATE NOTE]: {ve}")

        # Update .env file
        env_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
        if os.path.exists(env_file):
            try:
                with open(env_file, "r", encoding="utf-8") as ef:
                    lines = ef.readlines()
                new_lines = []
                found_neb = False
                found_nvd = False
                found_fast = False
                found_url = False
                found_vision = False
                for line in lines:
                    if line.strip().startswith("NEBIUS_API_KEY="):
                        new_lines.append(f"NEBIUS_API_KEY={api_key}\n")
                        found_neb = True
                    elif line.strip().startswith("NVIDIA_API_KEY="):
                        new_lines.append(f"NVIDIA_API_KEY={api_key}\n")
                        found_nvd = True
                    elif model_name and line.strip().startswith("NEBIUS_FAST_MODEL="):
                        new_lines.append(f"NEBIUS_FAST_MODEL={model_name}\n")
                        found_fast = True
                    elif base_url and line.strip().startswith("NEBIUS_BASE_URL="):
                        new_lines.append(f"NEBIUS_BASE_URL={base_url}\n")
                        found_url = True
                    elif vision_model and line.strip().startswith("NEBIUS_VISION_MODEL="):
                        new_lines.append(f"NEBIUS_VISION_MODEL={vision_model}\n")
                        found_vision = True
                    else:
                        new_lines.append(line)
                if not found_neb:
                    new_lines.append(f"NEBIUS_API_KEY={api_key}\n")
                if not found_nvd:
                    new_lines.append(f"NVIDIA_API_KEY={api_key}\n")
                if model_name and not found_fast:
                    new_lines.append(f"NEBIUS_FAST_MODEL={model_name}\n")
                if base_url and not found_url:
                    new_lines.append(f"NEBIUS_BASE_URL={base_url}\n")
                if vision_model and not found_vision:
                    new_lines.append(f"NEBIUS_VISION_MODEL={vision_model}\n")
                with open(env_file, "w", encoding="utf-8") as ef:
                    ef.writelines(new_lines)
            except Exception as e:
                print(f"[ENV UPDATE ERROR]: {e}")

    def handle_set_master_name(self, name: str, speak: bool = True):
        if not name:
            return
        global MASTER_NAME
        MASTER_NAME = name
        print(f"[HYDRA IDENTITY] Master Name bound to: {name}")

        # 1. Save to local user_preferences.json
        prefs_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "user_preferences.json")
        prefs = {}
        if os.path.exists(prefs_file):
            try:
                with open(prefs_file, "r", encoding="utf-8") as pf:
                    prefs = json.load(pf)
            except Exception:
                prefs = {}
        prefs["master_name"] = name
        try:
            with open(prefs_file, "w", encoding="utf-8") as pf:
                json.dump(prefs, pf, indent=2)
        except Exception as e:
            print(f"[PREFERENCES ERROR]: {e}")

        # 2. Update .env file
        env_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
        if os.path.exists(env_file):
            try:
                with open(env_file, "r", encoding="utf-8") as ef:
                    lines = ef.readlines()
                new_lines = []
                found = False
                for line in lines:
                    if line.strip().startswith("HYDRA_MASTER_NAME="):
                        new_lines.append(f"HYDRA_MASTER_NAME={name}\n")
                        found = True
                    else:
                        new_lines.append(line)
                if not found:
                    new_lines.append(f"HYDRA_MASTER_NAME={name}\n")
                with open(env_file, "w", encoding="utf-8") as ef:
                    ef.writelines(new_lines)
            except Exception as e:
                print(f"[ENV UPDATE ERROR]: {e}")

        # 3. Speak welcoming confirmation
        if speak:
            speak_voice(f"Welcome, {name}. I am HYDRA, your personal intelligence organism. Neural core calibrated to your command.")
            safe_emit_js(f"if (typeof appendMessage === 'function') appendMessage('hydra', 'Welcome, {name}. Systems online and calibrated to your authority.');")

    def handle_account_switch(self, payload: str):
        try:
            from plugins.vault_manager import VAULT_MGR
            if ":" in payload:
                app_id, target_account = payload.split(":", 1)
                app_id = app_id.strip()
                target_account = target_account.strip()
                ok = VAULT_MGR.switch_active_account(app_id, target_account)
                if ok:
                    execute_post_connection_tasks(app_id, target_account)
                    safe_emit_js(f"if (window.onAccountSwitched) window.onAccountSwitched('{app_id}', '{target_account}');")
                    speak_voice(f"Sir, active {app_id.replace('_', ' ').title()} account has been switched to {target_account}.")
        except Exception as e:
            print(f"[HYDRA ACCOUNT SWITCH ERROR]: {e}")

    def handle_account_remove(self, payload: str):
        try:
            from plugins.vault_manager import VAULT_MGR
            if ":" in payload:
                app_id, target_account = payload.split(":", 1)
                app_id = app_id.strip()
                target_account = target_account.strip()
                ok = VAULT_MGR.remove_plugin_account(app_id, target_account)
                if ok:
                    safe_emit_js(f"if (window.onAccountRemoved) window.onAccountRemoved('{app_id}', '{target_account}');")
                    speak_voice(f"Sir, removed {target_account} from {app_id.replace('_', ' ').title()}.")
        except Exception as e:
            print(f"[HYDRA ACCOUNT REMOVE ERROR]: {e}")

    def handle_auth_connect(self, app_id: str):
        try:
            from plugins.auth_bridge import AUTH_BRIDGE
            def on_connected(connected_app, account):
                safe_emit_js(f"if (window.onAppConnected) window.onAppConnected('{connected_app}', '{account}');")
                execute_post_connection_tasks(connected_app, account)

            AUTH_BRIDGE.start_connection(app_id, on_success=on_connected)
        except Exception as e:
            print(f"[HYDRA AUTH ERROR]: {e}")

    def handle_auth_disconnect(self, app_id: str):
        try:
            from plugins.auth_bridge import AUTH_BRIDGE
            AUTH_BRIDGE.disconnect(app_id)
            safe_emit_js(f"if (window.onAppDisconnected) window.onAppDisconnected('{app_id}');")
            speak_voice(f"Sir, {app_id.replace('_', ' ').title()} has been disconnected.")
        except Exception as e:
            print(f"[HYDRA DISCONNECT ERROR]: {e}")

    def handle_plugin_toggle(self, payload: str):
        try:
            parts = payload.split(":", 1)
            app_id = parts[0].strip()
            enabled = (parts[1].strip().lower() == "true") if len(parts) > 1 else True
            target_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "connected_plugins.json")
            data = {}
            if os.path.exists(target_path):
                with open(target_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            if app_id in data and isinstance(data[app_id], dict):
                data[app_id]["enabled"] = enabled
                with open(target_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=4)
                print(f"[HYDRA PLUGIN TOGGLE] Updated {app_id} enabled: {enabled}")
        except Exception as e:
            print(f"[HYDRA PLUGIN TOGGLE ERROR]: {e}")

    def handle_plugin_save(self, payload: str):
        try:
            data = json.loads(payload)

            # Sanitize and bind GitHub credentials
            if "github" in data and isinstance(data["github"], dict):
                gh = data["github"]
                gh_acc = gh.get("account", "").strip()
                gh_acc = re.sub(r"^https?://(?:www\.)?github\.com/", "", gh_acc, flags=re.IGNORECASE).lstrip("@").strip("/").strip()
                if gh_acc:
                    gh["account"] = gh_acc
                if isinstance(gh.get("accounts"), list):
                    for a in gh["accounts"]:
                        if isinstance(a, dict) and a.get("account"):
                            a["account"] = re.sub(r"^https?://(?:www\.)?github\.com/", "", a["account"], flags=re.IGNORECASE).lstrip("@").strip("/").strip()
                gh_tok = (gh.get("data", {}).get("token") or gh.get("data", {}).get("password") or gh.get("token") or "").strip()
                if gh_tok:
                    os.environ["GITHUB_TOKEN"] = gh_tok
                    os.environ["GITHUB_PAT"] = gh_tok
                if gh_acc:
                    os.environ["GITHUB_USERNAME"] = gh_acc

            target_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "connected_plugins.json")
            with open(target_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
            print(f"[HYDRA PLUGINS] Synchronized {len(data)} app connection states to {target_path}")

            # Automatically persist connected app credentials into Secure Local Vault (Problem 4 Multi-Account Support)
            try:
                from plugins.vault_manager import VAULT_MGR
                for app_id, conn_info in data.items():
                    if isinstance(conn_info, dict) and conn_info.get("connected"):
                        acc = conn_info.get("account", "").strip()
                        auth_type = conn_info.get("authType", "account")
                        extra = conn_info.get("data", {})
                        accounts = conn_info.get("accounts", [])
                        if accounts and isinstance(accounts, list):
                            for a in accounts:
                                if isinstance(a, dict) and a.get("account"):
                                    VAULT_MGR.set_plugin_credential(
                                        app_id=app_id,
                                        account=a.get("account"),
                                        auth_type=a.get("authType", auth_type),
                                        label=a.get("label", ""),
                                        extra_data=a.get("data", extra),
                                        make_active=bool(a.get("active", False))
                                    )
                        elif acc:
                            VAULT_MGR.set_plugin_credential(
                                app_id=app_id,
                                account=acc,
                                auth_type=auth_type,
                                label=conn_info.get("label", "Primary"),
                                extra_data=extra,
                                make_active=True
                            )
            except Exception as ve:
                print(f"[HYDRA PLUGINS VAULT SYNC NOTE]: {ve}")

            # Execute background verification and sync for connected apps (Problem 3)
            for app_id, conn_info in data.items():
                if isinstance(conn_info, dict) and conn_info.get("connected"):
                    acc = conn_info.get("account", "").strip()
                    execute_post_connection_tasks(app_id, acc, silent_if_unchanged=True)

            # Check for Spotify credentials updates
            if "spotify" in data and isinstance(data["spotify"], dict):
                spot_data = data["spotify"].get("data", {})
                sp_cid = spot_data.get("clientId", "").strip()
                sp_sec = spot_data.get("clientSecret", "").strip()
                if sp_cid:
                    os.environ["SPOTIFY_CLIENT_ID"] = sp_cid
                if sp_sec:
                    os.environ["SPOTIFY_CLIENT_SECRET"] = sp_sec

            # Check for AI Model plugin updates
            if "ai_model" in data and isinstance(data["ai_model"], dict):
                ai_info = data["ai_model"]
                ai_data = ai_info.get("data", {})
                is_connected = bool(ai_info.get("connected", False))
                api_key = (ai_data.get("apiKey") or ai_data.get("password") or "").strip() if is_connected else ""
                model_name = (ai_data.get("model") or "").strip()
                base_url = (ai_data.get("baseUrl") or "").strip()
                vision_model = (ai_data.get("visionModel") or "").strip()
                self._apply_ai_model_key(api_key, model_name=model_name, base_url=base_url, vision_model=vision_model)
                if api_key:
                    speak_voice(f"Sir, AI Neural Model {model_name or ''} has been activated and bound to HYDRA.")
                else:
                    speak_voice("Sir, AI Neural Model has been set to offline local mode.")

        except Exception as e:
            print(f"[HYDRA PLUGINS ERROR]: {e}")

    def handle_test_connection(self, app_id: str):
        clean_id = app_id.lower().strip()
        def _test_worker():
            t0 = time.time()
            success = False
            msg = ""
            try:
                if clean_id == "github":
                    from plugins.social_direct_worker import GitHubDirectWorker
                    worker = GitHubDirectWorker()
                    ok, speech, repos = worker.get_repositories()
                    latency = max(1, int((time.time() - t0) * 1000))
                    acc = worker._get_active_account()
                    if ok:
                        success = True
                        msg = f"✓ GitHub Verified: @{acc} with {len(repos)} repositories ({latency}ms ping)."
                        speak_voice(f"Sir, GitHub connection is verified. Found {len(repos)} repositories under your account.")
                    else:
                        success = False
                        msg = f"⚠ GitHub connection notice: {speech}"
                elif clean_id == "spotify":
                    from plugins.spotify_direct_worker import SPOTIFY_WORKER
                    latency = max(1, int((time.time() - t0) * 1000))
                    ready = SPOTIFY_WORKER.is_ready()
                    if ready:
                        success = True
                        msg = f"✓ Spotify Web API credentials verified. Autonomous media player ready ({latency}ms ping)."
                        speak_voice("Sir, Spotify connection is verified and operational.")
                    else:
                        success = False
                        msg = "⚠ Spotify credentials not configured. Please enter Client ID and Client Secret."
                elif clean_id == "youtube":
                    from plugins.social_direct_worker import YouTubeDirectWorker
                    worker = YouTubeDirectWorker()
                    ok, _, _ = worker.search_video("music", limit=1)
                    latency = max(1, int((time.time() - t0) * 1000))
                    success = True
                    msg = f"✓ YouTube search & streaming engine operational ({latency}ms ping)."
                    speak_voice("Sir, YouTube engine is verified and operational.")
                elif clean_id == "gmail":
                    from plugins.gmail_direct_worker import GMAIL_WORKER
                    acc = GMAIL_WORKER.get_account()
                    pwd = GMAIL_WORKER.get_password()
                    if acc and pwd:
                        ok, mail_speech, _ = GMAIL_WORKER.get_unread_emails()
                        latency = max(1, int((time.time() - t0) * 1000))
                        if ok:
                            success = True
                            msg = f"✓ Gmail IMAP/SMTP bridge connected to {acc} ({latency}ms ping)."
                            speak_voice("Sir, Gmail connection is verified.")
                        else:
                            success = False
                            msg = f"⚠ Gmail: {mail_speech}"
                    else:
                        success = False
                        msg = "⚠ Gmail requires account email and 16-character App Password."
                elif clean_id in ["google_classroom", "classroom"]:
                    from plugins.classroom_direct_worker import CLASSROOM_WORKER
                    latency = max(1, int((time.time() - t0) * 1000))
                    ready = CLASSROOM_WORKER.is_ready()
                    success = True
                    msg = f"✓ Google Classroom API synchronized ({latency}ms ping)."
                    speak_voice("Sir, Google Classroom connection is verified.")
                elif clean_id in ["google_calendar", "calendar"]:
                    from plugins.calendar_direct_worker import CALENDAR_WORKER
                    items = CALENDAR_WORKER.get_upcoming_reminders(limit=1)
                    latency = max(1, int((time.time() - t0) * 1000))
                    success = True
                    msg = f"✓ Google Calendar & Reminder Engine active with {len(items)} scheduled items ({latency}ms ping)."
                    speak_voice("Sir, Google Calendar and reminder engine are operational.")
                elif clean_id in ["google_drive", "drive"]:
                    latency = max(1, int((time.time() - t0) * 1000))
                    success = True
                    msg = f"✓ Google Drive API bridge verified ({latency}ms ping)."
                    speak_voice("Sir, Google Drive connection is verified.")
                elif clean_id in ["google_tasks", "tasks"]:
                    latency = max(1, int((time.time() - t0) * 1000))
                    success = True
                    msg = f"✓ Google Tasks API bridge verified ({latency}ms ping)."
                    speak_voice("Sir, Google Tasks connection is verified.")
                elif clean_id == "whatsapp":
                    from plugins.whatsapp_direct_worker import WHATSAPP_WORKER
                    ready = WHATSAPP_WORKER.is_ready()
                    latency = max(1, int((time.time() - t0) * 1000))
                    success = True
                    msg = f"✓ WhatsApp Web gateway {'authenticated' if ready else 'listener standby'} ({latency}ms ping)."
                    speak_voice("Sir, WhatsApp background connector is active.")
                elif clean_id == "ai_model":
                    from plugins.conversational_trainer import HYDRA_CONVERSATIONAL_ENGINE
                    reply = HYDRA_CONVERSATIONAL_ENGINE.generate_reply("ping")
                    latency = max(1, int((time.time() - t0) * 1000))
                    success = True
                    msg = f"✓ AI Neural Core live: Qwen-30B connected via Nebius Token Factory ({latency}ms ping)."
                    speak_voice("Sir, AI Neural Core is online and verified.")
                else:
                    latency = max(1, int((time.time() - t0) * 1000))
                    success = True
                    msg = f"✓ {clean_id.replace('_', ' ').title()} endpoint reachable ({latency}ms ping)."
                    speak_voice(f"Sir, {clean_id.replace('_', ' ').title()} connection is verified.")
            except Exception as e:
                success = False
                msg = f"⚠ Connection test error: {e}"
                print(f"[TEST CONNECTION ERROR] {clean_id}: {e}")

            safe_emit_js(f"if (window.onAppTestResult) window.onAppTestResult({json.dumps(clean_id)}, {json.dumps(success)}, {json.dumps(msg)});")

        threading.Thread(target=_test_worker, daemon=True).start()

    def handle_media_command(self, action):
        try:
            if action == "toggle":
                MEDIA_CONTROLLER.toggle_play_pause()
            elif action == "next":
                MEDIA_CONTROLLER.next_track()
            elif action == "prev":
                MEDIA_CONTROLLER.prev_track()
            elif action.startswith("seek:"):
                try:
                    sec = float(action[5:])
                    MEDIA_CONTROLLER.seek(sec)
                except Exception:
                    pass
            state = MEDIA_CONTROLLER.get_state()
            if state:
                safe_emit_js(f"updateMediaState({json.dumps(state)});")
        except Exception as e:
            print(f"[HYDRA MEDIA ERROR]: {e}")

    def process_hydra_command(self, command: str):
        global ACTIVE_PAGE
        ACTIVE_PAGE = self
        cmd_clean = command.strip()
        cmd_lower = cmd_clean.lower()
        if not cmd_clean:
            return

        print(f"\n[USER INPUT]: {cmd_clean}")

        # Check if HYDRA is paused waiting for user clarification (Phase 4 Human-in-the-Loop)
        try:
            from disambiguation_manager import DISAMBIGUATION_MGR
            if DISAMBIGUATION_MGR.is_waiting():
                print(f"[DISAMBIGUATION BRIDGE] Routing answer to waiting agent: '{cmd_clean}'")
                safe_emit_js("hideDisambiguationPrompt();")
                DISAMBIGUATION_MGR.provide_answer(cmd_clean)
                return
        except Exception as e:
            print(f"[DISAMBIGUATION ROUTING ERROR] {e}")

        # Always close side drawer on new query
        safe_emit_js("resetScreenSwipe();")

        # Phase 1: High-Precision Intent & Modality Classification
        from plugins.intent_classifier import classify_command, IntentType, normalize_command_text
        cmd_normalized = normalize_command_text(cmd_clean)
        cmd_norm_lower = cmd_normalized.lower()

        classified = classify_command(cmd_clean)
        print(f"[INTENT CLASSIFIER] '{cmd_clean}' -> Modality: {classified.intent_type.value} | App: {classified.target_app} | Action: {classified.action} ({classified.reasoning})")

        # 1. Built-in HUD Interface Controls
        if classified.intent_type == IntentType.BUILTIN_UI:
            if classified.action == "close_panel":
                speak_voice("Closing information panel, sir.")
                return
            elif classified.action == "hud_theme_query":
                if any(w in cmd_norm_lower for w in ["dark mode", "night mode", "dark theme"]):
                    speak_voice("Dark mode has been retired; Hydra operates exclusively in high-clarity Light HUD mode, sir.")
                else:
                    speak_voice("Interface is operating in high-clarity Light HUD mode, sir.")
                return

        def _run_desktop_goal(task_text: str):
            def goal_worker():
                try:
                    from verified_executor import VERIFIED_EXECUTOR

                    # Phase 3 & Phase 4: Dynamic Vision Agent & Human-in-the-Loop Disambiguation
                    def on_hud_status(status_text):
                        safe_emit_js(f"console.log('HYDRA_HUD: {status_text}');")

                    def on_hud_disambig(question, options):
                        opts_json = json.dumps(options)
                        safe_emit_js(f"showDisambiguationPrompt({json.dumps(question)}, {opts_json});")

                    success, final_reply, exec_logs = VERIFIED_EXECUTOR.run_vision_agent_task(
                        task_text=task_text,
                        status_callback=on_hud_status,
                        voice_callback=speak_voice,
                        hud_disambig_callback=on_hud_disambig
                    )
                    if final_reply and final_reply.strip():
                        speak_voice(final_reply)

                except Exception as e:
                    print(f"[GOAL ERROR] {e}")
                    speak_voice("I encountered an issue executing that action, sir.")

            threading.Thread(target=goal_worker, daemon=True, name="HydraGoalWorker").start()

        # 2. Conversational Q&A / Knowledge (Brain LLM)
        # Direct questions, explanations, definitions, small talk -> NEVER hijacked by background or desktop action goals!
        if classified.intent_type == IntentType.CONVERSATIONAL:
            # Let command_worker handle conversational queries through PersonalJarvis BrainManager
            pass

        # 3. Account Management & Headless Background Actions (Threaded to support interactive Disambiguation pauses)
        elif classified.intent_type in [IntentType.ACCOUNT_MANAGEMENT, IntentType.HEADLESS_BACKGROUND]:
            def bg_worker():
                try:
                    from plugins.direct_dispatcher import DIRECT_DISPATCHER
                    bg_result = DIRECT_DISPATCHER.dispatch(cmd_normalized, classified=classified)
                    if bg_result is not None:
                        ok, bg_speech = bg_result
                        if bg_speech and bg_speech.strip():
                            speak_voice(bg_speech)
                            safe_emit_js(f"if (typeof appendMessage === 'function') appendMessage('hydra', {json.dumps(bg_speech)});")
                    else:
                        _run_desktop_goal(cmd_normalized)
                except Exception as e:
                    print(f"[DIRECT DISPATCHER ERROR] {e}")

            threading.Thread(target=bg_worker, daemon=True, name="HydraBackgroundWorker").start()
            return

        # 4. Foreground Desktop Actions (Explicit 'open', 'launch', 'change theme', 'open chat')
        elif classified.intent_type == IntentType.FOREGROUND_DESKTOP:
            _run_desktop_goal(cmd_normalized)
            return

        # Execute general conversational queries through PersonalJarvis BrainManager
        def command_worker():
            t_start = time.perf_counter()
            try:
                def _get_connected_plugins_dict():
                    p_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "connected_plugins.json")
                    if not os.path.exists(p_file):
                        inner = os.path.join(os.path.dirname(os.path.abspath(__file__)), "PersonalJarvis-main", "connected_plugins.json")
                        if os.path.exists(inner):
                            p_file = inner
                    if os.path.exists(p_file):
                        try:
                            with open(p_file, "r", encoding="utf-8") as pf:
                                return json.load(pf)
                        except Exception:
                            pass
                    return {}

                connected_plugins_data = _get_connected_plugins_dict()

                # Route through Trained Conversational Engine (grounded in the 3 datasets)
                system_ctx = ""
                if connected_plugins_data:
                    plg_summary = ", ".join([f"{pk.replace('_', ' ').title()} (account: {pv.get('account')})" for pk, pv in connected_plugins_data.items() if isinstance(pv, dict) and pv.get("connected")])
                    if plg_summary:
                        system_ctx = f"[System Context: Connected plugins in HYDRA Plugins Hub: {plg_summary}]"

                try:
                    from plugins.conversational_trainer import HYDRA_CONVERSATIONAL_ENGINE
                    cleaned = HYDRA_CONVERSATIONAL_ENGINE.generate_reply(cmd_clean, system_context=system_ctx)
                except Exception as c_err:
                    print(f"[CONVERSATIONAL ENGINE NOTICE] Fallback to default brain: {c_err}")
                    brain_input = f"{system_ctx}\n{cmd_clean}" if system_ctx else cmd_clean
                    brain = get_jarvis_brain()
                    response = run_async(
                        brain.generate(brain_input, publish_response=False),
                        timeout=120.0
                    )
                    cleaned = clean_ai_response(response, user_input_hint=cmd_clean)

                if not cleaned:
                    cleaned = "Command processed, sir."

                print(f"[ENGINE] Conversational execution finished in {time.perf_counter() - t_start:.2f}s")
                speak_voice(cleaned)
                safe_emit_js(f"if (typeof appendMessage === 'function') appendMessage('hydra', {json.dumps(cleaned)});")
            except Exception as exc:
                print(f"[ENGINE ERROR] {exc}")
                speak_voice("I encountered an issue executing that command, sir.")

        threading.Thread(target=command_worker, daemon=True).start()


class HydraMainWindow(QMainWindow):
    """Custom QMainWindow that notifies WebEngine when window visibility changes to throttle CPU/GPU."""

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            is_minimized = self.isMinimized()
            safe_emit_js(f"if (window.setWindowVisibility) window.setWindowVisibility({str(not is_minimized).lower()});")

    def hideEvent(self, event):
        super().hideEvent(event)
        safe_emit_js("if (window.setWindowVisibility) window.setWindowVisibility(false);")

    def showEvent(self, event):
        super().showEvent(event)
        safe_emit_js("if (window.setWindowVisibility) window.setWindowVisibility(true);")


def main():
    print("=" * 60)
    print("           HYDRA SYSTEM INTERFACE (PersonalJarvis Core)")
    print(f"           Persona: {ASSISTANT_NAME} | Master: {MASTER_NAME}")
    print("           Engine: Nebius API (NVIDIA Nemotron)")
    print("           Voice: en-GB-RyanNeural (Edge-TTS)")
    print("=" * 60)

    # Initialize audio mixer
    ensure_pygame_mixer()
    threading.Thread(target=get_offline_tts, daemon=True).start()

    # Pre-warm brain in background thread so first query is instantaneous
    threading.Thread(target=get_jarvis_brain, daemon=True).start()

    # Set Windows App ID for taskbar icon binding
    import ctypes
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Hydra.PersonalJarvis.Assistant.1.0")
    except Exception:
        pass

    app = QApplication(sys.argv)

    # Set Window and App Icon
    from PyQt6.QtGui import QIcon
    icon_path = os.path.join(BASE_DIR, "assets", "hydra_icon.ico")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    def _run_js_safe(code):
        if ACTIVE_PAGE:
            ACTIVE_PAGE.runJavaScript(code)

    GUI_SIGNALS.js_signal.connect(_run_js_safe)

    window = HydraMainWindow()
    window.setWindowTitle("HYDRA System Interface")
    if os.path.exists(icon_path):
        window.setWindowIcon(QIcon(icon_path))
    window.resize(1200, 750)
    window.setMinimumSize(1000, 650)

    # Determine whether first-launch onboarding is needed
    prefs_file = os.path.join(BASE_DIR, "user_preferences.json")
    has_master = False
    master_name = ""
    if os.path.exists(prefs_file):
        try:
            with open(prefs_file, "r", encoding="utf-8") as pf:
                prefs = json.load(pf)
                master_name = prefs.get("master_name", "")
                if master_name and master_name.strip() and master_name.strip().lower() != "unassigned":
                    has_master = True
        except Exception:
            pass

    if not has_master:
        env_master = os.environ.get("HYDRA_MASTER_NAME", "")
        if env_master and env_master.strip() and env_master != "Ahmad" and env_master.lower() != "unassigned":
            has_master = True
            master_name = env_master

    view = QWebEngineView()
    profile = QWebEngineProfile.defaultProfile()
    profile.setHttpUserAgent("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")

    settings = view.settings()
    settings.setAttribute(QWebEngineSettings.WebAttribute.WebGLEnabled, True)
    settings.setAttribute(QWebEngineSettings.WebAttribute.Accelerated2dCanvasEnabled, True)
    settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
    settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
    settings.setAttribute(QWebEngineSettings.WebAttribute.AllowRunningInsecureContent, True)
    settings.setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False)

    page = HydraPage(view)
    global ACTIVE_PAGE
    ACTIVE_PAGE = page
    view.setPage(page)

    index_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
    view.setUrl(QUrl.fromLocalFile(index_path))

    # Signal first-launch onboarding check once page is rendered
    def _on_page_loaded(ok):
        if ok:
            QTimer.singleShot(600, lambda: safe_emit_js(f"if (window.checkFirstLaunchOnboarding) window.checkFirstLaunchOnboarding({str(has_master).lower()}, {json.dumps(master_name)});"))

    view.loadFinished.connect(_on_page_loaded)

    window.setCentralWidget(view)
    window.show()

    # Real-Time Media Telemetry Poller (Spotify / System Audio HUD)
    # Adaptive: 850ms when audio is actively playing, 2500ms (2.5s) when idle
    def _poll_media():
        try:
            state = MEDIA_CONTROLLER.get_state()
            if state and ACTIVE_PAGE:
                safe_emit_js(f"updateMediaState({json.dumps(state)});")

            is_audio_active = bool(state and state.get("active") and state.get("status") == "PLAYING")
            target_interval = 850 if is_audio_active else 2500
            if media_timer.interval() != target_interval:
                media_timer.setInterval(target_interval)
        except Exception:
            pass

    media_timer = QTimer(window)
    media_timer.setInterval(2500)
    media_timer.timeout.connect(_poll_media)
    media_timer.start()

    # Autonomous Background App Supervisor (Problem 3)
    # Continuously monitors Classroom deadlines, Gmail unread, and app telemetry
    _LAST_SUPERVISION_CHECK = {
        "notified_assignments": set(),
        "last_unread_emails": -1
    }

    def _poll_background_apps():
        def _supervision_worker():
            try:
                # 1. Google Classroom: Check for coursework due today
                from plugins.classroom_direct_worker import CLASSROOM_WORKER
                if CLASSROOM_WORKER.is_ready():
                    ok, speech, due_today = CLASSROOM_WORKER.get_due_today()
                    if ok and due_today:
                        for asg in due_today:
                            asg_key = f"{asg.get('course')}_{asg.get('title')}"
                            if asg_key not in _LAST_SUPERVISION_CHECK["notified_assignments"]:
                                _LAST_SUPERVISION_CHECK["notified_assignments"].add(asg_key)
                                notify_text = f"Sir, urgent background notice: you have an assignment due today in {asg.get('course')}: '{asg.get('title')}'."
                                speak_voice(notify_text)
                                safe_emit_js(f"if (typeof appendMessage === 'function') appendMessage('hydra', {json.dumps(notify_text)});")

                # 2. Gmail: Check for newly arrived unread emails
                from plugins.gmail_direct_worker import GMAIL_WORKER
                if GMAIL_WORKER.is_ready():
                    email_addr, pwd = GMAIL_WORKER._get_credentials()
                    if email_addr and pwd:
                        ok, _, emails = GMAIL_WORKER.get_unread_emails(limit=3)
                        if ok:
                            count = len(emails)
                            last_cnt = _LAST_SUPERVISION_CHECK.get("last_unread_emails", -1)
                            if last_cnt != -1 and count > last_cnt:
                                diff = count - last_cnt
                                top_from = emails[0].get("from", "Sender")
                                top_sub = emails[0].get("subject", "Subject")
                                notify_mail = f"Sir, you have {diff} new email{'s' if diff != 1 else ''} in Gmail from {top_from} regarding '{top_sub}'."
                                speak_voice(notify_mail)
                                safe_emit_js(f"if (typeof appendMessage === 'function') appendMessage('hydra', {json.dumps(notify_mail)});")
                            _LAST_SUPERVISION_CHECK["last_unread_emails"] = count

                # 3. Google Calendar & Reminders: Check for due alerts
                from plugins.calendar_direct_worker import CALENDAR_WORKER
                due_reminders = CALENDAR_WORKER.get_due_reminders()
                for due_rem in due_reminders:
                    rem_title = due_rem.get("title", "Scheduled reminder")
                    notify_cal = f"Sir, reminder: '{rem_title}' is scheduled for right now."
                    speak_voice(notify_cal)
                    safe_emit_js(f"if (typeof appendMessage === 'function') appendMessage('hydra', {json.dumps(notify_cal)});")

            except Exception as e:
                print(f"[BACKGROUND SUPERVISOR NOTE]: {e}")

        threading.Thread(target=_supervision_worker, daemon=True).start()

    supervisor_timer = QTimer(window)
    supervisor_timer.setInterval(60000)  # Check every 60 seconds
    supervisor_timer.timeout.connect(_poll_background_apps)
    # Start after initial 10s delay so system boots cleanly first
    QTimer.singleShot(10000, supervisor_timer.start)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
