"""
HYDRA Phase 7: Unified NLP Command Dispatcher & Multi-Action Command Chaining Engine
Handles natural conversational speech, intelligent filler stripping, compound command splitting,
sequential execution with adaptive window delays, and unified natural voice response synthesis.
"""

import re
import time
import subprocess
from typing import List, Tuple, Optional, Dict, Any, Callable

# Global Conversational Dialogue State Management (Dodaios / Assistant)
_PENDING_DIALOGUE_STATE: Optional[Dict[str, Any]] = None

def set_pending_dialogue_state(state: Dict[str, Any]):
    global _PENDING_DIALOGUE_STATE
    _PENDING_DIALOGUE_STATE = state

def pop_pending_dialogue_state() -> Optional[Dict[str, Any]]:
    global _PENDING_DIALOGUE_STATE
    s = _PENDING_DIALOGUE_STATE
    _PENDING_DIALOGUE_STATE = None
    return s


# Action verbs that indicate a new command after conjunctions
ACTION_VERBS = (
    r'(?:open|launch|start|run|pull\s+up|bring\s+up|fire\s+up|'
    r'close|kill|terminate|stop|exit|quit|'
    r'snap|minimize|maximize|restore|switch\s+to|focus|bring\s+to\s+front|show\s+desktop|'
    r'click|double\s*click|right\s*click|press|type|write|drag|move|glide|point|nudge|shift|scroll|'
    r'turn\s+on|turn\s+off|enable|disable|connect|disconnect|toggle|'
    r'take\s+a\s+screenshot|capture\s+screen|screenshot|'
    r'search|look\s+up|find\s+out|explain|browse|'
    r'send|text|dm|play|put\s+on|listen\s+to|'
    r'mute|unmute|create|make|design|'
    r'tell|say|read|describe|scan|what\s+do\s+you\s+see|'
    r'wait|pause|sleep|hold\s+on|delay)'
)

# Common non-command phrases that contain 'and' and should NEVER be split
NON_SPLIT_PHRASES = [
    "black and white", "up and down", "left and right", "copy and paste",
    "drag and drop", "back and forth", "pros and cons", "dos and don'ts",
    "rock and roll", "peace and quiet"
]

WORD_NUMBERS = {
    "zero": 0, "half": 0.5, "a": 1, "an": 1, "one": 1, "two": 2, "three": 3,
    "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40,
    "fifty": 50, "sixty": 60
}

def parse_wait_duration(cmd: str) -> Optional[float]:
    """
    Parses conversational and numeric wait durations from natural speech.
    Supports:
      - 'wait for 3 sec', 'wait for 3 seconds', 'wait 3 sec', 'wait 3s'
      - 'pause for 5 seconds', 'sleep 2 sec', 'delay 4 seconds'
      - 'wait a second' (1s), 'wait a moment' (2s), 'wait a minute' (60s)
      - 'wait for three seconds' (3s), 'wait two seconds' (2s)
      - 'wait for 1.5 seconds' (1.5s), 'wait 2 mins' (120s)
    """
    if not cmd:
        return None
    clean = cmd.lower().strip()
    clean = re.sub(r'[!?,]|(?<=[a-zA-Z])\.|\.$', '', clean).strip()

    if not any(clean.startswith(v) for v in ["wait", "pause", "sleep", "hold on", "delay"]):
        return None

    # Common spoken idioms
    if clean in ["wait a second", "wait a sec", "pause a second", "just a second", "just a sec", "hold on a sec", "hold on a second"]:
        return 1.0
    if clean in ["wait a moment", "wait a bit", "hold on a moment", "pause a moment", "hold on a bit"]:
        return 2.0
    if clean in ["wait a minute", "pause a minute", "wait for a minute"]:
        return 60.0

    # Numeric match: 'wait for 3 seconds', 'wait 3s', 'wait 2 mins'
    match_num = re.search(r'(?:wait|pause|sleep|hold\s+on|delay)\s+(?:for\s+)?(\d+(?:\.\d+)?)\s*(s|sec|secs|second|seconds|m|min|mins|minute|minutes)?\b', clean)
    if match_num:
        val = float(match_num.group(1))
        unit = match_num.group(2) or "sec"
        if unit.startswith("m"):
            return val * 60.0
        return val

    # Word match: 'wait for three seconds', 'wait two seconds'
    match_words = re.search(r'(?:wait|pause|sleep|hold\s+on|delay)\s+(?:for\s+)?([a-z]+)\s*(s|sec|secs|second|seconds|m|min|mins|minute|minutes)\b', clean)
    if match_words:
        w = match_words.group(1).lower()
        if w in WORD_NUMBERS:
            val = float(WORD_NUMBERS[w])
            unit = match_words.group(2) or "sec"
            if unit.startswith("m"):
                return val * 60.0
            return val

    return None

def clean_conversational_fillers(cmd: str) -> str:
    """
    Strips polite conversational padding and assistant invocation tags.
    Example: 'hey hydra could you please open spotify for me' -> 'open spotify'
    """
    if not cmd:
        return ""
    text = cmd.strip()

    # Strip leading hesitation sounds ('uh', 'um', 'er', 'ah')
    text = re.sub(r'^(?:uh+|um+|er+|ah+)[\s,]+', '', text, flags=re.IGNORECASE)

    # Strip leading assistant invocation ('hydra', 'hey hydra', etc.)
    text = re.sub(r'^(?:hey\s+|hi\s+|ok\s+)?hydra[\s,:]+', '', text, flags=re.IGNORECASE)

    # Strip leading conversational polite phrases
    leading_fillers = [
        r'could you please\s+', r'can you please\s+', r'would you please\s+',
        r'could you kindly\s+', r'can you kindly\s+', r'would you kindly\s+',
        r'could you\s+', r'can you\s+', r'would you\s+',
        r'please\s+', r'kindly\s+', r'just\s+',
        r'i want as i tell (?:him|you) to\s+', r'as i tell (?:him|you) to\s+',
        r'i want you to\s+', r'i need you to\s+', r'i would like you to\s+',
        r'tell him to\s+', r'tell you to\s+', r'tell me to\s+',
        r'help me\s+', r'go ahead and\s+', r'now\s+'
    ]
    for p in leading_fillers:
        text = re.sub(r'^' + p, '', text, flags=re.IGNORECASE)

    # Strip trailing polite tags
    trailing_fillers = [
        r'\s+for me$', r'\s+please$', r'\s+if you can$', r'\s+right now$',
        r'\s+sir$', r'\s+thanks$', r'\s+thank you$'
    ]
    for s in trailing_fillers:
        text = re.sub(s, '', text, flags=re.IGNORECASE)

    return text.strip()

def split_compound_commands(cmd: str) -> List[str]:
    """
    Intelligently segments compound multi-action commands joined by connectors.
    Example: 'open spotify, snap it to the left, and click the play button'
    Returns: ['open spotify', 'snap it to the left', 'click the play button']
    Guards search queries and note drafting from unintended splitting.
    """
    if not cmd:
        return []
    text = cmd.strip()

    # 1. Protection Guards: Do NOT split search queries or note contents
    search_starters = [
        "search", "who is", "what is", "explain", "look up",
        "find out", "tell me about", "i want to know about", "how many songs"
    ]
    clean_low = text.lower()
    if any(clean_low.startswith(s) for s in search_starters):
        return [text]

    if "notepad" in clean_low and any(w in clean_low for w in ["write", "type", "draft", "note"]):
        return [text]

    if ("spotify" in clean_low and any(w in clean_low for w in ["play", "listen"])) or \
       ("whatsapp" in clean_low and any(w in clean_low for w in ["send", "message", "text"])):
        return [text]

    for nsp in NON_SPLIT_PHRASES:
        if nsp in clean_low:
            # Replace temporarily to preserve
            safe_nsp = nsp.replace(" and ", " _AND_ ")
            text = text.replace(nsp, safe_nsp)

    # 2. Split on connectors followed by an action verb or explicit step delimiters
    # Matches: 'and then', 'then', 'after that', ', and', or 'and <verb>'
    pattern = (
        rf'(?:\s+and\s+then\s+|\s+then\s+|\s+after\s+that\s+|'
        rf'\s*,\s*and\s+|\s*;\s*|'
        rf'\s+and\s+(?={ACTION_VERBS}\b)|\s*,\s*(?={ACTION_VERBS}\b)|'
        rf'\s+(?=(?:wait|pause|sleep|hold\s+on)\s+(?:for\s+)?(?:\d+|a|an|one|two|three|four|five|ten)\b)|'
        rf'\s+(?=(?:search\s+for|click\s+(?:on\s+)?the|write\s+|type\s+|press\s+)\b))'
    )
    raw_parts = [p.strip() for p in re.split(pattern, text, flags=re.IGNORECASE) if p.strip()]

    # Restore preserved phrases
    cleaned_parts = []
    for part in raw_parts:
        restored = part.replace(" _AND_ ", " and ").strip()
        if restored:
            cleaned_parts.append(restored)

    return cleaned_parts if cleaned_parts else [text]

def synthesize_chained_replies(replies: List[str]) -> str:
    """
    Consolidates multiple sequential execution replies into a single natural, cohesive sentence.
    Example: ['Opening Spotify, sir.', 'Snapped Spotify to the left half, sir.', 'Done sir, clicked the play button.']
    Output: 'Done sir: opening Spotify, snapped Spotify to the left half of your screen, and clicked the play button.'
    """
    if not replies:
        return "Done, sir."
    if len(replies) == 1:
        return replies[0]

    cleaned_actions = []
    for r in replies:
        c = r.strip()
        # Clean repetitive greetings, confirmations, and polite suffixes
        c = re.sub(r'^(?:Done\s+sir,?\s*|Systems\s+flushed,?\s*|I\s+have\s+|Pulling\s+up\s+)', '', c, flags=re.IGNORECASE)
        c = re.sub(r'[,.]?\s*sir\.?$', '', c, flags=re.IGNORECASE).strip()
        c = c.rstrip(".,; ").strip()
        if c:
            # Ensure proper lowercasing of first letter unless acronym
            if not c[:2].isupper():
                c = c[0].lower() + c[1:]
            cleaned_actions.append(c)

    if not cleaned_actions:
        return "Done, sir."
    if len(cleaned_actions) == 1:
        return f"Done sir, {cleaned_actions[0]}."
    if len(cleaned_actions) == 2:
        return f"Done sir, {cleaned_actions[0]} and {cleaned_actions[1]}."

    all_but_last = ", ".join(cleaned_actions[:-1])
    return f"Done sir: {all_but_last}, and {cleaned_actions[-1]}."

def execute_atomic_command(cmd: str, sys_driver: Any, search_func: Optional[Callable] = None) -> Tuple[bool, str]:
    """
    Executes a single atomic system action across all HYDRA capability phases:
    Phase 6 (Vision) -> Phase 5 (Window Mgr) -> Phase 2 (Hardware/Mouse) ->
    Phase 3 (Wireless) -> Phase 4 (Search) -> Phase 1 (App Launch/Close) -> System Settings/Shell.
    Returns: (handled: bool, reply: str)
    """
    if not cmd:
        return False, ""

    clean_cmd = clean_conversational_fillers(cmd)
    clean_low = clean_cmd.lower().replace(".", "").replace("!", "").replace("?", "").strip()

    # 0. TIMING & WAIT DELAYS (e.g. 'wait for 3 sec', 'pause for 5 seconds', 'sleep 2s')
    wait_sec = parse_wait_duration(clean_cmd)
    if wait_sec is not None:
        duration = min(max(0.05, wait_sec), 300.0)
        print(f"[PHASE 7 DISPATCHER] Pausing pipeline execution for {duration:.1f} seconds...")
        time.sleep(duration)
        unit = "second" if duration == 1.0 else "seconds"
        num = f"{int(duration)}" if duration.is_integer() else f"{duration:.1f}"
        return True, f"Waited {num} {unit}, sir."

    # 0.05 PHASE 15: OPTICAL PERCEPTION & CAMERA VISION ("see", "open your eyes", "what is in my hand", "close camera")
    try:
        from camera_vision import CAMERA_ENGINE
        cam_handled, cam_reply = CAMERA_ENGINE.handle_command(clean_cmd)
        if cam_handled:
            return True, cam_reply
    except Exception as e:
        print(f"[PHASE 7 DISPATCHER] Camera vision notice: {e}")

    # 0.1 PHASE 9: CONTINUOUS SCREEN CONTEXT & VISUAL AWARENESS (What am I doing? / What is on my screen?)
    try:
        from intelligent_agent import check_screen_context_intent
        screen_res = check_screen_context_intent(clean_cmd)
        if screen_res:
            handled, screen_reply = screen_res
            if handled and screen_reply:
                return True, screen_reply
    except Exception as e:
        print(f"[PHASE 7 DISPATCHER] Screen context warning: {e}")

    # 0.2 PROACTIVE INTENTS & CLARIFYING QUESTIONS ("I want to design something" -> "Would you like me to open Canva?")
    try:
        from intelligent_agent import check_indirect_intent
        indirect = check_indirect_intent(clean_cmd)
        if indirect:
            question, state = indirect
            set_pending_dialogue_state(state)
            return True, question
    except Exception as e:
        print(f"[PHASE 7 DISPATCHER] Indirect intent warning: {e}")

    # 0.2 DODAIOS UNIFIED ENGINE (Cron Routines, Persistent Tasks, Knowledge Vault & Recall, REST Tools, Weather)
    try:
        from dodaios_bridge import DODAIOS_BRIDGE
        dodaios_handled, dodaios_reply = DODAIOS_BRIDGE.handle_intent(clean_cmd, sys_driver)
        if dodaios_handled:
            return True, dodaios_reply
    except Exception as e:
        print(f"[PHASE 7 DISPATCHER] Dodaios notice: {e}")

    # 0.21 PHASE 11: PROACTIVE DAEMON COMMANDS (Read WhatsApp message, Timers, Reminders, Battery & System Health)
    try:
        from proactive_daemon import PROACTIVE_DAEMON
        proactive_handled, proactive_reply = PROACTIVE_DAEMON.handle_command(clean_cmd, sys_driver)
        if proactive_handled:
            return True, proactive_reply
    except Exception as e:
        print(f"[PHASE 7 DISPATCHER] Proactive daemon notice: {e}")

    # 0.26 DODAIOS SAFE SHELL EXECUTION (Direct Terminal Commands with Impact Blast-Radius Protection)
    shell_match = re.match(r'^(?:run\s+command|run\s+in\s+terminal|execute\s+command|execute\s+shell)\s+(.+)$', clean_low)
    if shell_match:
        raw_sh_cmd = shell_match.group(1).strip()
        from dodaios_bridge import DODAIOS_BRIDGE
        is_safe, safety_msg = DODAIOS_BRIDGE.safety.assess_execution_safety(raw_sh_cmd)
        if not is_safe:
            return True, safety_msg
        try:
            res = subprocess.run(raw_sh_cmd, shell=True, capture_output=True, text=True, timeout=15)
            out = res.stdout if res.stdout else res.stderr
            res_summary = out.strip()[:300] if out else "Command completed."
            return True, f"Command executed, sir: {res_summary}"
        except Exception as ex:
            return True, f"Command execution error: {ex}"

    # 0.3 PHASE 12: DEEP INTERACTIVE WEB AUTOMATION (SPAs, Forms, File Downloads, Table Scraping, Persistent Sessions)
    try:
        from browser_automation import handle_web_automation_command
        web_handled, web_reply = handle_web_automation_command(clean_cmd, sys_driver)
        if web_handled:
            return True, web_reply
    except Exception as e:
        print(f"[PHASE 7 DISPATCHER] Browser automation notice: {e}")

    # 0.4 DODAIOS AUTONOMOUS COMPUTER-USE OPERATOR (Primary Task Completion Engine)
    try:
        from dodaios_operator import DODAIOS_OPERATOR
        is_greeting = clean_low in ["hi", "hey", "hello", "who are you", "what is your name", "whats your name"]
        is_pure_query = any(clean_low.startswith(p) for p in ["who is ", "what is ", "what time is it", "tell me a joke", "how is the weather"])
        is_basic_app_launch = clean_low.startswith(("open ", "launch ", "start ", "run ")) and len(clean_low.split()) <= 2 and not any(w in clean_low for w in ["classroom", "browser", "brave", "chrome", "edge", "spotify", "whatsapp", "portal"])

        if not is_greeting and not is_pure_query and not is_basic_app_launch:
            is_actionable = any(w in clean_low for w in [
                "classroom", "homework", "assignment", "spotify", "song", "playlist", "liked", "music",
                "whatsapp", "message", "text", "notepad", "type", "note", "write", "play", "theme", "dark mode", "light mode", "tab", "search"
            ]) or len(clean_low.split()) >= 3
            if is_actionable:
                report = DODAIOS_OPERATOR.execute_goal(clean_cmd, sys_driver=sys_driver)
                if report and report.spoken_reply:
                    return True, report.spoken_reply
    except Exception as e:
        print(f"[DODAIOS OPERATOR DISPATCHER NOTICE] {e}")

    # 0.41 PHASE 14: NON-INTERRUPTIVE BACKGROUND HARDWARE/SYSTEM AUDIO (Mute/Volume)
    try:
        from background_controller import handle_background_command
        bg_handled, bg_reply = handle_background_command(clean_cmd, sys_driver)
        if bg_handled:
            return True, bg_reply
    except Exception as e:
        print(f"[PHASE 7 DISPATCHER] Background controller notice: {e}")

    # 0.45 PHASE 13: IN-APP CONTENT SEARCH & WORKSPACE INTELLIGENCE
    try:
        from inapp_search import handle_inapp_search
        inapp_handled, inapp_reply = handle_inapp_search(clean_cmd, sys_driver)
        if inapp_handled:
            return True, inapp_reply
    except Exception as e:
        print(f"[PHASE 7 DISPATCHER] In-app search notice: {e}")

    try:
        from workspace_intelligence import handle_workspace_intelligence
        ws_handled, ws_reply = handle_workspace_intelligence(clean_cmd, sys_driver)
        if ws_handled:
            return True, ws_reply
    except Exception as e:
        print(f"[PHASE 7 DISPATCHER] Workspace intelligence notice: {e}")

    # 0.51 DODAIOS AUTONOMOUS TASK FALLBACK
    try:
        from dodaios_operator import DODAIOS_OPERATOR
        if len(clean_low.split()) >= 2 and not is_greeting and not is_pure_query:
            report = DODAIOS_OPERATOR.execute_goal(clean_cmd, sys_driver=sys_driver)
            if report and report.spoken_reply:
                return True, report.spoken_reply
    except Exception as e:
        print(f"[PHASE 7 DISPATCHER] Dodaios task notice: {e}")

    # 0.6 AUTONOMOUS COMPLEX TASK ENGINE (Learned Skills & Silent Background Planner)
    try:
        from skill_engine import handle_complex_task
        skill_handled, skill_reply = handle_complex_task(clean_cmd, sys_driver)
        if skill_handled:
            return True, skill_reply
    except Exception as e:
        print(f"[PHASE 7 DISPATCHER] Skill engine notice: {e}")

    # 1. PHASE 6: Screen Understanding & Computer Vision (Clicks, OCR, Shapes, Icons, Screenshot)
    try:
        vision_handled, vision_reply = sys_driver.execute_vision(clean_cmd)
        if vision_handled:
            return True, vision_reply
    except Exception as e:
        print(f"[PHASE 7 DISPATCHER] Vision warning: {e}")

    # 2. PHASE 5: Window Management & Multitasking (Snap, Focus, Minimize, Maximize, Close, Show Desktop)
    try:
        wm_handled, wm_reply = sys_driver.execute_window_management(clean_cmd)
        if wm_handled:
            return True, wm_reply
    except Exception as e:
        print(f"[PHASE 7 DISPATCHER] Window Mgr warning: {e}")

    # 3. PHASE 2: Hardware Input (Mouse movement, Clicks, Coordinates, Keyboard Shortcuts)
    try:
        hw_handled, hw_reply = sys_driver.execute_hardware(clean_cmd)
        if hw_handled:
            return True, hw_reply
    except Exception as e:
        print(f"[PHASE 7 DISPATCHER] Hardware warning: {e}")

    # 4. PHASE 3: Wireless Networking (Wi-Fi, Bluetooth, Hotspot, Airplane Mode)
    try:
        wl_handled, wl_reply = sys_driver.execute_wireless(clean_cmd)
        if wl_handled:
            return True, wl_reply
    except Exception as e:
        print(f"[PHASE 7 DISPATCHER] Wireless warning: {e}")

    # 4.5 IN-APP APPLICATION SEARCH (e.g. 'search for laapata' when Spotify is active)
    inapp_search_match = re.match(r'^(?:search\s+for|search|find)\s+(.+)$', clean_low)
    if inapp_search_match and not clean_low.startswith("search web for") and not any(clean_low.startswith(w) for w in ["who is", "what is", "how many", "tell me"]):
        search_query = inapp_search_match.group(1).strip()
        if search_query and not any(w in search_query for w in ["your name", "the time", "my name"]):
            try:
                from window_ops import WINDOW_MGR
                import ctypes
                user32 = ctypes.windll.user32
                open_wins = WINDOW_MGR.get_open_windows()
                fg_hwnd = user32.GetForegroundWindow()
                active_win = next((w for w in open_wins if w["hwnd"] == fg_hwnd), None)
                if not active_win and open_wins:
                    active_win = open_wins[0]

                if active_win:
                    app_name = (active_win.get("app_name") or "").lower()
                    title = (active_win.get("title") or "").lower()
                    if "spotify" in app_name or "spotify" in title:
                        vis_ok, _ = sys_driver.vision.click_visual_target("search icon")
                        if not vis_ok:
                            sys_driver.hardware.hotkey("ctrl", "k")
                        time.sleep(0.15)
                        sys_driver.hardware.type_text(search_query)
                        time.sleep(0.1)
                        sys_driver.hardware.press_key("enter")
                        time.sleep(0.35)
                        WINDOW_MGR.minimize("spotify")
                        return True, f"Searched for '{search_query}' in Spotify, sir."
            except Exception as e:
                print(f"[PHASE 7 DISPATCHER] In-app search notice: {e}")

    # 5. PHASE 4: Web Search & Information Retrieval
    if search_func:
        search_triggers = [
            "how many songs are in the ", "how many songs are in ", "how many tracks are in ",
            "what are the songs in ", "what are the tracks in ", "tracklist of ",
            "i want to know about ", "i wanna know about ", "tell me about ",
            "find out about ", "search for ", "search web for ", "search ",
            "who is ", "what is ", "explain ", "look up "
        ]
        for st in search_triggers:
            if clean_low.startswith(st):
                query = clean_low[len(st):].strip()
                if query and not any(w in query for w in ["your name", "the time", "my name", "hand", "holding", "screen", "battery", "camera", "weather", "profile", "on my", "in my"]):
                    spoken_reply, _ = search_func(query)
                    return True, spoken_reply

    # 6. PHASE 1: App Launching & Closing Engine (with Typo-Tolerance)
    launch_verbs = ["open ", "launch ", "start ", "run ", "pull up ", "bring up ", "fire up "]
    close_verbs = ["close ", "kill ", "terminate ", "stop ", "exit ", "quit "]

    matched_close = next((v for v in close_verbs if clean_low.startswith(v)), None)
    if matched_close:
        app_target = clean_low[len(matched_close):].strip()
        success, spoken = sys_driver.close_application(app_target)
        return True, spoken

    matched_launch = next((v for v in launch_verbs if clean_low.startswith(v)), None)
    if matched_launch:
        app_target = clean_low[len(matched_launch):].strip()
        # Guard: In-app destinations, web services, or browser tasks ('on brave', 'in chrome', 'my profile', etc.)
        if any(p in app_target for p in ["my profile", "profile", "account", "on brave", "in brave", "on chrome", "in chrome", "on edge", "in edge", "classroom", "google classroom"]):
            try:
                from dodaios_operator import DODAIOS_OPERATOR
                rep = DODAIOS_OPERATOR.execute_goal(clean_cmd, sys_driver=sys_driver)
                if rep and rep.spoken_reply:
                    return True, rep.spoken_reply
            except Exception:
                pass
        success, spoken = sys_driver.open_application(app_target)
        if not success:
            # If local binary not found, try Dodaios Operator as intelligent fallback
            try:
                from dodaios_operator import DODAIOS_OPERATOR
                rep = DODAIOS_OPERATOR.execute_goal(clean_cmd, sys_driver=sys_driver)
                if rep and rep.spoken_reply and "could not understand" not in rep.spoken_reply.lower():
                    return True, rep.spoken_reply
            except Exception:
                pass
        return True, spoken

    # Standalone App Name Resolution (e.g., user just says "spotify" or "canva")
    if len(clean_low.split()) <= 2 and not any(w in clean_low for w in ["yes", "no", "hi", "hey", "hello", "what", "how"]):
        app_entry, resolved_name, is_typo = sys_driver.app_engine.resolve_app(clean_low)
        if app_entry:
            success, spoken, _ = sys_driver.app_engine.launch(clean_low)
            return True, spoken

    # 7. Windows System Settings URI Deep-links
    if "setting" in clean_low:
        sub_settings = {
            "bluetooth": ("ms-settings:bluetooth", "bluetooth"),
            "display": ("ms-settings:display", "display"),
            "sound": ("ms-settings:sound", "audio"),
            "volume": ("ms-settings:sound", "audio"),
            "apps": ("ms-settings:appsfeatures", "installed applications"),
            "update": ("ms-settings:windowsupdate", "windows update"),
            "battery": ("ms-settings:batterysaver", "battery"),
            "storage": ("ms-settings:storagesense", "storage")
        }
        for key, (uri, name) in sub_settings.items():
            if key in clean_low:
                subprocess.Popen(f"cmd /c start {uri}", shell=True)
                return True, f"Navigating directly to {name} settings, sir."

    # 8. Shell Directory Navigation
    explorer_triggers = ["open ", "go to ", "show "]
    if any(clean_low.startswith(t) for t in explorer_triggers):
        sub_folders = {
            "downloads": ("shell:Downloads", "downloads"),
            "documents": ("shell:Personal", "documents"),
            "pictures": ("shell:My Pictures", "pictures"),
            "music": ("shell:My Music", "music"),
            "videos": ("shell:My Video", "videos"),
            "desktop": ("shell:Desktop", "desktop"),
            "recycle bin": ("shell:RecycleBinFolder", "recycle bin"),
            "trash": ("shell:RecycleBinFolder", "recycle bin")
        }
        for key, (path, name) in sub_folders.items():
            if key in clean_low:
                subprocess.Popen(f"explorer {path}", shell=True)
                return True, f"Pulling up your {name} directory, sir."

    return False, ""

def execute_chained_pipeline(
    full_cmd: str,
    sys_driver: Any,
    search_func: Optional[Callable] = None,
    delay_between_actions: float = 0.4
) -> Tuple[bool, str]:
    """
    Main Phase 7 Pipeline Entrypoint:
    1. Splits compound sentences into sequential sub-actions.
    2. Executes each sub-action in order.
    3. Adapts timing delays (e.g. allowing newly launched apps time to draw their UI).
    4. Synthesizes a unified, natural voice confirmation.
    """
    if not full_cmd:
        return False, ""

    sub_commands = split_compound_commands(full_cmd)
    if not sub_commands:
        return False, ""

    # If it's a single command, execute directly
    if len(sub_commands) == 1:
        return execute_atomic_command(sub_commands[0], sys_driver, search_func)

    # Chained multi-action execution
    print(f"\n[PHASE 7 DISPATCHER] Chained pipeline executing {len(sub_commands)} sub-actions:")
    for idx, sc in enumerate(sub_commands):
        print(f"  [{idx + 1}/{len(sub_commands)}] -> '{sc}'")

    executed_replies = []
    any_success = False

    for idx, sc in enumerate(sub_commands):
        clean_sub = clean_conversational_fillers(sc)
        handled, reply = execute_atomic_command(clean_sub, sys_driver, search_func)

        if handled:
            any_success = True
            executed_replies.append(reply)
            print(f"  [Action {idx + 1} Handled]: {reply}")

            # Dynamic adaptive delay:
            # If this sub-command launched or switched an application, give the OS window
            # a moment to render before the next command (like a click or snap) runs!
            if idx < len(sub_commands) - 1:
                clean_low = clean_sub.lower()
                if any(v in clean_low for v in ["open ", "launch ", "start ", "switch to ", "focus "]):
                    time.sleep(0.85)
                else:
                    time.sleep(delay_between_actions)
        else:
            print(f"  [Action {idx + 1} Unhandled]: '{sc}'")

    if not any_success:
        return False, ""

    unified_reply = synthesize_chained_replies(executed_replies)
    return True, unified_reply
