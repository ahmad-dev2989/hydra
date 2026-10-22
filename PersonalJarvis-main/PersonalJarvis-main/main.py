"""
HYDRA System Interface — PersonalJarvis Core Engine
Powered by Nebius AI (NVIDIA Nemotron) with Hydra Living HUD Interface & Edge-TTS Voice.
"""

import os
import sys
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

        # Always close side drawer on new query
        safe_emit_js("resetScreenSwipe();")

        # Built-in UI commands
        if any(w in cmd_lower for w in ["close info", "close panel", "hide info", "go back"]):
            speak_voice("Closing information panel, sir.")
            return

        if any(w in cmd_lower for w in ["dark mode", "night mode", "dark theme"]):
            speak_voice("Dark mode has been retired; Hydra operates exclusively in high-clarity Light HUD mode, sir.")
            return

        if any(w in cmd_lower for w in ["light mode", "day mode", "light theme"]):
            speak_voice("Interface is operating in high-clarity Light HUD mode, sir.")
            return

        # Autonomous Desktop Goal & Keyboard-First Planning (Phase 2)
        # Bypasses cloud plugin barriers for native desktop app control
        is_action_goal = any(kw in cmd_lower for kw in [
            "find ", "play ", "like ", "add to liked", "change theme", "set theme",
            "open app", "mute spotify", "unmute spotify", "search for "
        ]) or (
            any(app in cmd_lower for app in ["spotify", "antigrav", "chrome", "brave", "notepad", "vscode", "whatsapp", "calc"])
            and any(act in cmd_lower for act in ["play", "find", "search", "open", "theme", "mute", "like", "type", "add", "send"])
        )

        if is_action_goal:
            def goal_worker():
                try:
                    from goal_decomposer import decompose_goal_plan, format_plan_for_display
                    plan = decompose_goal_plan(cmd_clean)
                    print("\n" + format_plan_for_display(plan))

                    steps = plan.get("steps", [])
                    app_name = plan.get("app", "the application")
                    shortcuts = plan.get("shortcuts_used", [])
                    shortcut_str = f" using {shortcuts[0]}" if shortcuts else ""

                    if plan.get("found_in_database"):
                        reply = f"Found verified prior plan in database with {len(steps)} steps for {app_name}, sir."
                    else:
                        reply = f"Decomposed detailed plan with {len(steps)} steps for {app_name}{shortcut_str}, sir."
                    speak_voice(reply)
                except Exception as e:
                    print(f"[GOAL ERROR] {e}")
                    speak_voice("I encountered an issue planning that action, sir.")

            threading.Thread(target=goal_worker, daemon=True).start()
            return

        # Execute general conversational queries through PersonalJarvis BrainManager
        def command_worker():
            t_start = time.perf_counter()
            try:
                brain = get_jarvis_brain()
                response = run_async(
                    brain.generate(cmd_clean, publish_response=False),
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

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
