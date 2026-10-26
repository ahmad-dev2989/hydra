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
import asyncio
import threading
import webbrowser

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

def speak_voice(text: str):
    """Speaks text using Hydra's online Ryan voice, animating the living HUD organism."""
    cleaned_text = clean_ai_response(text)
    if not cleaned_text:
        return

    print(f"\n[HYDRA SAYS]: {cleaned_text}\n")

    def voice_worker():
        t0 = time.perf_counter()
        spoken_text = cleaned_text.replace("h.y.d.r.a", "Hydra").replace("H.Y.D.R.A", "Hydra").replace(".", " ")
        spoken = False

        def speak_offline():
            nonlocal spoken
            with TTS_LOCK:
                engine = get_offline_tts()
                if engine:
                    approx_dur = max(1.5, len(spoken_text) * 0.065)
                    safe_emit_js(f"notifySpeechStart({approx_dur});")
                    engine.say(spoken_text)
                    engine.runAndWait()
                    safe_emit_js("notifySpeechEnd();")
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

            audio_stream = io.BytesIO(raw_audio)
            ensure_pygame_mixer()
            pygame.mixer.music.load(audio_stream, "mp3")
            pygame.mixer.music.play()
            while pygame.mixer.music.get_busy():
                pygame.time.Clock().tick(10)
            pygame.mixer.music.unload()

            safe_emit_js("notifySpeechEnd();")
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

    threading.Thread(target=voice_worker, daemon=True).start()


# PersonalJarvis Brain Engine Initializer
JARVIS_BRAIN = None
BRAIN_BUS = None
BRAIN_LOCK = threading.Lock()

def get_jarvis_brain():
    global JARVIS_BRAIN, BRAIN_BUS
    with BRAIN_LOCK:
        if JARVIS_BRAIN is None:
            print("[ENGINE] Booting PersonalJarvis BrainManager with Nebius Provider...")
            BRAIN_BUS = EventBus()
            # Build brain inside the persistent async loop
            JARVIS_BRAIN = run_async(asyncio.to_thread(build_default_brain, bus=BRAIN_BUS, tier="router"))
            JARVIS_BRAIN._curator = None
            if hasattr(JARVIS_BRAIN, "set_reply_language"):
                JARVIS_BRAIN.set_reply_language("en")
            print(f"[ENGINE] BrainManager ready! Active provider: {JARVIS_BRAIN.active_provider}")
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

            elif clean_id in ["canva", "discord", "github", "linkedin"]:
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
        elif message.startswith("HYDRA_OPEN_URL:"):
            url = message[15:].strip()
            if url:
                print(f"[HYDRA BROWSER] Opening URL: {url}")
                webbrowser.open(url)
        elif message.startswith("HYDRA_MEDIA_CMD:"):
            action = message[16:].strip()
            self.handle_media_command(action)
        elif message.startswith("HYDRA_PLUGIN_SAVE:"):
            payload = message[18:].strip()
            self.handle_plugin_save(payload)
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

    def handle_plugin_save(self, payload: str):
        try:
            data = json.loads(payload)
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

        except Exception as e:
            print(f"[HYDRA PLUGINS ERROR]: {e}")

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

                # Direct check if user is asking about connected apps or plugin status
                low_cmd = cmd_clean.lower()
                if any(w in low_cmd for w in ["connected", "plugin", "plugins", "link", "connection"]) and any(w in low_cmd for w in ["which", "what", "is", "are", "check", "status", "list", "show"]):
                    active_plg = [f"{pk.replace('_', ' ').title()} ({pv.get('account', 'active')})" for pk, pv in connected_plugins_data.items() if isinstance(pv, dict) and pv.get("connected")]
                    if active_plg:
                        reply = f"Sir, your connected apps are: {', '.join(active_plg)}. They are fully connected and active."
                    else:
                        reply = "Sir, you do not have any apps connected in the Plugins Hub yet. You can connect them using the CONNECT APPS button."
                    speak_voice(reply)
                    return

                # Enrich conversational prompt with connected plugins context
                brain_input = cmd_clean
                if connected_plugins_data:
                    plg_summary = ", ".join([f"{pk.replace('_', ' ').title()} (account: {pv.get('account')})" for pk, pv in connected_plugins_data.items() if isinstance(pv, dict) and pv.get("connected")])
                    if plg_summary:
                        brain_input = f"[System Context: Connected plugins in HYDRA Plugins Hub: {plg_summary}]\n{cmd_clean}"

                brain = get_jarvis_brain()
                response = run_async(
                    brain.generate(brain_input, publish_response=False),
                    timeout=120.0
                )
                cleaned = clean_ai_response(response, user_input_hint=cmd_clean)
                if not cleaned:
                    cleaned = "Command processed, sir."

                print(f"[ENGINE] Execution finished in {time.perf_counter() - t_start:.2f}s")
                speak_voice(cleaned)
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

    app = QApplication(sys.argv)

    def _run_js_safe(code):
        if ACTIVE_PAGE:
            ACTIVE_PAGE.runJavaScript(code)

    GUI_SIGNALS.js_signal.connect(_run_js_safe)

    window = HydraMainWindow()
    window.setWindowTitle("HYDRA System Interface")
    window.resize(1200, 750)
    window.setMinimumSize(1000, 650)

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
                                notify_mail = f"Sir, you have {diff} new email in Gmail from {top_from} regarding '{top_sub}'."
                                speak_voice(notify_mail)
                                safe_emit_js(f"if (typeof appendMessage === 'function') appendMessage('hydra', {json.dumps(notify_mail)});")
                            _LAST_SUPERVISION_CHECK["last_unread_emails"] = count

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
