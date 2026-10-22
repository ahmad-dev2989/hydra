"""
HYDRA Phase 3: Autonomous Verified Step Executor
Executes keyboard-first UI action plans with pre-action UI idle synchronization,
post-action Nemotron-3.5-Super-VL visual verification, adaptive slow-PC waiting,
and compiles verified successful paths into learned_task_memory.json.
"""

import os
import sys
import time
import json
import re
from typing import Dict, Any, List, Optional, Tuple
from pathlib import Path

# Add directories to path
CURRENT_DIR = Path(__file__).resolve().parent
PARENT_DIR = CURRENT_DIR.parent
GRANDPARENT_DIR = PARENT_DIR.parent

for p in [CURRENT_DIR, PARENT_DIR, GRANDPARENT_DIR]:
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

try:
    import pyautogui
    import pyperclip
    pyautogui.FAILSAFE = False
except ImportError:
    pyautogui = None
    pyperclip = None

from visual_verifier import VISUAL_VERIFIER, capture_screen_gdi, NEBIUS_VISION_MODEL
from window_ops import WINDOW_MGR
from media_controller import MEDIA_CONTROLLER

# Path to persistent prior knowledge database
TASK_MEMORY_FILE = CURRENT_DIR / "learned_task_memory.json"
if not TASK_MEMORY_FILE.exists() and (PARENT_DIR / "learned_task_memory.json").exists():
    TASK_MEMORY_FILE = PARENT_DIR / "learned_task_memory.json"
elif not TASK_MEMORY_FILE.exists() and (GRANDPARENT_DIR / "learned_task_memory.json").exists():
    TASK_MEMORY_FILE = GRANDPARENT_DIR / "learned_task_memory.json"


def save_successful_task_to_memory(task_key: str, app_name: str, intent_name: str, steps: List[Dict[str, Any]]):
    """Saves a fully visually verified action sequence into learned_task_memory.json for future zero-delay execution."""
    try:
        tasks_data = {}
        if TASK_MEMORY_FILE.exists():
            with open(TASK_MEMORY_FILE, "r", encoding="utf-8") as f:
                tasks_data = json.load(f)

        record = {
            "id": task_key,
            "application": app_name.lower(),
            "intent": intent_name.upper(),
            "steps": steps,
            "verified_by": "nvidia/Nemotron-3.5-Super-VL",
            "success_count": tasks_data.get(task_key, {}).get("success_count", 0) + 1,
            "last_verified_timestamp": time.time()
        }

        tasks_data[task_key] = record

        # Save to all directories in workspace to keep them in sync
        for candidate in [
            CURRENT_DIR / "learned_task_memory.json",
            PARENT_DIR / "learned_task_memory.json",
            GRANDPARENT_DIR / "learned_task_memory.json"
        ]:
            try:
                with open(candidate, "w", encoding="utf-8") as f:
                    json.dump(tasks_data, f, indent=2)
            except Exception:
                pass

        print(f"[MEMORY] Successfully saved verified prior procedure '{task_key}' to database.")
    except Exception as e:
        print(f"[MEMORY ERROR] Could not save task to database: {e}")


class VerifiedStepExecutor:
    """
    Executes each step of a goal plan, gating every action with visual verification
    and slow-PC adaptive retries.
    """

    def __init__(self):
        self.verifier = VISUAL_VERIFIER

    def execute_plan(
        self,
        plan: Dict[str, Any],
        status_callback=None
    ) -> Tuple[bool, str, List[Dict[str, Any]]]:
        """
        Executes a planned sequence of steps with visual verification at each stage.
        Returns: (success: bool, summary_reply: str, execution_log: List[dict])
        """
        task_name = plan.get("task", "Unknown Task")
        app_name = plan.get("app", "Desktop")
        steps = plan.get("steps", [])
        shortcuts = plan.get("shortcuts_used", [])

        print("=" * 65)
        print(f"  STARTING VISUALLY VERIFIED EXECUTION: {task_name}")
        print(f"  Target Application: {app_name.upper()}")
        print(f"  Total Steps to Verify: {len(steps)}")
        print("=" * 65)

        execution_log = []
        t_start = time.perf_counter()

        # Step 0: Pre-Action UI Idle Synchronization
        # Ensure target application is in foreground and finished initial loading
        if status_callback:
            status_callback(f"Synchronizing UI: checking if {app_name} is ready...")

        self._ensure_app_foreground(app_name)

        pre_prompt = (
            f"Is the application '{app_name}' open and in the foreground? "
            f"Is the main user interface visible and finished loading (not blank or loading spinner)?"
        )
        ok, details, _ = self.verifier.adaptive_wait_until_verified(
            step_description=f"UI Idle Sync: {app_name} ready",
            verification_prompt=pre_prompt,
            app_name=app_name,
            timeout=8.0,
            interval=0.5,
            status_callback=status_callback
        )

        if not ok:
            print(f"[EXECUTION WARNING] {app_name} pre-action check timed out. Proceeding with adaptive recovery...")

        # Step-by-Step Execution Loop
        for idx, step_item in enumerate(steps, 1):
            action_type = (step_item.get("action") or step_item.get("action_type") or "").lower()
            target = step_item.get("target") or ""
            details = step_item.get("details") or step_item.get("description") or ""

            print(f"\n--- [EXECUTING STEP {idx:02d}/{len(steps):02d}] {action_type.upper()}: {details} ---")
            if status_callback:
                status_callback(f"Step {idx}/{len(steps)}: {details[:45]}...")

            # 1. Execute Actuation
            act_ok, act_msg = self._perform_action(action_type, target, details, app_name)
            step_record = {
                "step": idx,
                "action": action_type,
                "target": target,
                "details": details,
                "actuation_result": act_msg,
                "verified": False
            }

            # 2. Post-Action Visual Verification Gate
            # Build verification question for this specific step
            verify_prompt = self._build_verification_prompt(action_type, target, details, app_name)
            
            # Fast verification for audio playback via SMTC if active
            is_audio_step = any(w in f"{details} {target}".lower() for w in ["play", "space", "playback", "audio", "green circle"])
            if is_audio_step and "spotify" in app_name.lower():
                try:
                    state = MEDIA_CONTROLLER.get_state()
                    if state.get("status") == "PLAYING":
                        v_ok = True
                        v_details = f"Audio playback confirmed active: '{state.get('title')}' by {state.get('artist')}"
                        v_raw = {"verified": True, "app_state": "ready", "reason": v_details}
                        print(f"[SMTC VERIFIED] {v_details}")
                    else:
                        v_ok, v_details, v_raw = self.verifier.adaptive_wait_until_verified(
                            step_description=f"Step {idx}: {details}",
                            verification_prompt=verify_prompt,
                            app_name=app_name,
                            timeout=6.5,
                            interval=0.5,
                            status_callback=status_callback
                        )
                except Exception:
                    v_ok, v_details, v_raw = self.verifier.adaptive_wait_until_verified(
                        step_description=f"Step {idx}: {details}",
                        verification_prompt=verify_prompt,
                        app_name=app_name,
                        timeout=6.5,
                        interval=0.5,
                        status_callback=status_callback
                    )
            else:
                v_ok, v_details, v_raw = self.verifier.adaptive_wait_until_verified(
                    step_description=f"Step {idx}: {details}",
                    verification_prompt=verify_prompt,
                    app_name=app_name,
                    timeout=15.0,
                    interval=0.5,
                    status_callback=status_callback
                )

            step_record["verified"] = v_ok
            step_record["visual_observation"] = v_details
            execution_log.append(step_record)

            if not v_ok:
                fail_msg = f"Visual verification notice at step {idx} ({action_type}): {v_details}"
                print(f"\n[OBSTACLE ENCOUNTERED] {fail_msg}")
                print("[RECOVERY] Handing over to Dynamic Vision-In-The-Loop Agent to inspect live screen and complete task...")
                return self.run_dynamic_vision_loop(
                    task_text=task_name,
                    app_name=app_name,
                    last_problem=f"Step {idx} ({details}) encountered an issue: {v_details}",
                    status_callback=status_callback
                )

            print(f"[STEP {idx:02d} COMPLETE] Visually verified: {v_details[:60]}")

        # All Steps Completed and Visually Verified!
        total_time = time.perf_counter() - t_start
        print("\n" + "=" * 65)
        print(f"  ALL {len(steps)} STEPS VISUALLY VERIFIED & COMPLETED ({total_time:.2f}s)!")
        print("=" * 65)

        # Save verified procedure to database for zero-delay prior knowledge next time
        task_slug = f"{app_name.lower()}:{re.sub(r'[^a-zA-Z0-9]+', '_', task_name.lower()).strip('_')}"
        save_successful_task_to_memory(
            task_key=task_slug,
            app_name=app_name,
            intent_name=task_name,
            steps=steps
        )

        success_reply = f"Task completed and visually verified across all {len(steps)} steps for {app_name}, sir."
        return True, success_reply, execution_log

    def _ensure_app_foreground(self, app_name: str):
        """Ensures the application window is brought to foreground without stealing focus if active."""
        if not app_name:
            return
        low_app = app_name.lower()
        try:
            import ctypes
            u = ctypes.windll.user32
            fg_hwnd = u.GetForegroundWindow()
            win = WINDOW_MGR.resolve_window(app_name)
            if win and fg_hwnd == win.get("hwnd"):
                # Already active foreground window! Do not pulse Alt or touch focus
                return
        except Exception:
            pass

        if "spotify" in low_app:
            WINDOW_MGR.switch_to("spotify")
        elif "antigrav" in low_app or "code" in low_app:
            WINDOW_MGR.switch_to("antigravity")
        elif "chrome" in low_app:
            WINDOW_MGR.switch_to("chrome")
        elif "brave" in low_app:
            WINDOW_MGR.switch_to("brave")
        elif "notepad" in low_app:
            WINDOW_MGR.switch_to("notepad")
        time.sleep(0.3)

    def _perform_action(self, action_type: str, target: str, details: str, app_name: str) -> Tuple[bool, str]:
        """Performs native actuation: launching, typing, hotkey pressing, or clicking."""
        self._ensure_app_foreground(app_name)

        if not pyautogui:
            return True, "Simulated (pyautogui not installed)"

        try:
            low_det = details.lower()
            low_target = (target or "").lower()

            if action_type == "open_app":
                self._ensure_app_foreground(target or app_name)
                return True, f"Focused/opened {target or app_name}"

            elif action_type == "hotkey":
                # Spotify play / toggle
                if "spotify" in app_name.lower() and ("space" in low_target or "play" in low_det or "circle" in low_det):
                    MEDIA_CONTROLLER.toggle_play_pause()
                    pyautogui.press("space")
                    return True, "Toggled Spotify playback"

                # Parse hotkey string like "Ctrl+L", "Ctrl+Shift+P", "Ctrl+,"
                keys = [k.strip().lower() for k in re.split(r'[\+\s]+', target) if k.strip()]
                if not keys and "ctrl+l" in low_det:
                    keys = ["ctrl", "l"]
                elif not keys and "ctrl+shift+p" in low_det:
                    keys = ["ctrl", "shift", "p"]
                elif not keys and "ctrl+," in low_det:
                    keys = ["ctrl", ","]
                
                if keys:
                    pyautogui.hotkey(*keys)
                    time.sleep(0.3)
                    return True, f"Pressed hotkey {'+'.join(keys)}"
                return False, f"Could not parse hotkey '{target}'"

            elif action_type == "type_text":
                # Extract text to type from quotes or details
                match = re.search(r"['\"](.*?)['\"]", details)
                text_to_type = match.group(1) if match else target
                
                if not text_to_type:
                    words = details.split("Type", 1)[-1].split("and", 1)[0].strip(" '\"")
                    text_to_type = words or "search"

                # If typing search in Spotify, ensure search bar is focused first
                if "spotify" in app_name.lower() and ("search" in low_det or "search" in low_target):
                    pyautogui.hotkey("ctrl", "l")
                    time.sleep(0.2)

                # Select all first to overwrite any existing query
                pyautogui.hotkey("ctrl", "a")
                time.sleep(0.1)

                # Use clipboard paste for instant, 100% accurate text entry
                if pyperclip:
                    pyperclip.copy(text_to_type)
                    pyautogui.hotkey("ctrl", "v")
                else:
                    pyautogui.typewrite(text_to_type, interval=0.03)

                time.sleep(0.2)
                if "enter" in low_det or action_type == "type_text":
                    pyautogui.press("enter")
                    time.sleep(0.4)

                return True, f"Typed text '{text_to_type}'"

            elif action_type == "press_key":
                key = target.lower() or "enter"
                pyautogui.press(key)
                return True, f"Pressed key '{key}'"

            elif action_type == "wait":
                time.sleep(0.5)
                return True, "Waited for UI render"

            elif action_type in ["move_cursor", "click"]:
                # If clicking Play / Green circle in Spotify, toggle playback
                if "spotify" in app_name.lower() and any(w in low_det for w in ["play", "circle", "pause"]):
                    MEDIA_CONTROLLER.toggle_play_pause()
                    pyautogui.press("space")
                    return True, "Triggered Spotify playback via play action"
                
                # If adding to liked songs in Spotify, use native shortcut
                if "spotify" in app_name.lower() and ("plus" in low_det or "add" in low_det or "like" in low_det):
                    pyautogui.hotkey("alt", "shift", "b")
                    time.sleep(0.2)
                    return True, "Saved song to Liked Songs via Spotify shortcut"

                # If navigating to liked songs on sidebar
                if "spotify" in app_name.lower() and "liked" in low_det:
                    pyautogui.click(140, 310)
                    time.sleep(0.3)
                    return True, "Navigated to Liked Songs in left sidebar"

                # Theme selection in Antigravity
                if "theme" in low_det and "antigrav" in app_name.lower():
                    pyautogui.hotkey("ctrl", "k")
                    time.sleep(0.1)
                    pyautogui.hotkey("ctrl", "t")
                    time.sleep(0.4)
                    pyautogui.write("Light", interval=0.03)
                    time.sleep(0.2)
                    pyautogui.press("enter")
                    return True, "Selected Light Theme in Antigravity"

                pyautogui.click()
                return True, f"Clicked on {target or 'target'}"

            elif action_type == "complete":
                return True, "Task completion checkpoint reached"

            return True, f"Action {action_type} executed"

        except Exception as e:
            return False, f"Actuation error: {e}"

    def _build_verification_prompt(self, action_type: str, target: str, details: str, app_name: str) -> str:
        """Constructs a targeted visual question for Nemotron-3.5-Super-VL."""
        low_det = details.lower()
        low_target = (target or "").lower()
        combined = f"{low_det} {low_target}"

        # 1. Open / launch app
        if action_type == "open_app" or (("open" in combined or "launch" in combined) and "app" in combined):
            return f"Verify if {app_name} is currently open and visible on screen with its main interface loaded."

        # 2. Audio playback / play / pause / space / green circle
        if any(w in combined for w in ["space", "play", "pause", "playback", "green circle", "green button", "audio"]):
            return f"In {app_name}, is audio playing or is a play/pause button or track progress bar visible on the playback bar?"

        # 3. Search bar focusing (Ctrl+L, Ctrl+K)
        if any(w in combined for w in ["ctrl+l", "ctrl+k", "search_bar", "search bar", "focus search"]):
            return f"In {app_name}, is the search input field or search bar focused with a text cursor, or ready for typing?"

        # 4. Text typing
        if action_type == "type_text" or "type" in low_det:
            match = re.search(r"['\"](.*?)['\"]", details)
            txt = match.group(1) if match else (target or "query")
            return f"In {app_name}, is '{txt}' entered into the search field, or are search results matching '{txt}' visible on screen?"

        # 5. Search results
        if any(w in combined for w in ["search_results", "results_list", "results", "song row"]):
            return f"In {app_name}, are search results or tracks matching the query displayed on the screen?"

        # 6. Liked songs / playlist
        if any(w in combined for w in ["liked", "plus", "playlist", "heart", "favorite", "save"]):
            return f"In {app_name}, is the song added to Liked Songs, or is the Liked Songs view visible and active?"

        # 7. Theme change
        if any(w in combined for w in ["theme", "color theme", "light", "dark"]):
            return f"In {app_name}, has the color theme changed or is the theme picker/settings menu visible?"

        # 8. Command palette / Settings
        if any(w in combined for w in ["command palette", "ctrl+shift+p", "settings", "ctrl+,"]):
            return f"In {app_name}, is the command palette or settings interface visible?"

        # 9. Wait for window / render
        if action_type == "wait" or "wait" in low_det:
            return f"In {app_name}, has the window or UI finished loading and is visible without a blank screen?"

        # Default fallback
        return f"Verify if the following step succeeded on screen: '{details}' in {app_name}."

    # =========================================================================
    # DYNAMIC VISION-IN-THE-LOOP AGENT (Per-Step Vision & Self-Recovery)
    # =========================================================================

    def run_vision_agent_task(self, task_text: str, status_callback=None) -> Tuple[bool, str, List[Dict[str, Any]]]:
        """
        Unified Entry Point:
        1. Instant media controls (stop/pause/resume/next/previous) with zero latency.
        2. Fast check of prior knowledge database. If an obstacle is encountered,
           seamlessly recovers via the Dynamic Vision Agent.
        3. Real-time Vision-In-The-Loop Agent capturing screenshots on each step,
           with prominent 'RESPONSE BY AI' output in the CMD.
        """
        low_t = task_text.lower().strip()

        # 1. Instant Media Commands
        if any(w in low_t for w in ["stop playing", "stop music", "pause music", "pause song", "stop spotify", "pause spotify", "pause playback"]):
            MEDIA_CONTROLLER.pause()
            msg = "Spotify playback stopped, sir."
            print(f"[MEDIA CONTROLLER] {msg}")
            return True, msg, []

        if any(w in low_t for w in ["resume music", "resume playing", "resume spotify", "unmute spotify"]):
            MEDIA_CONTROLLER.play()
            msg = "Spotify playback resumed, sir."
            print(f"[MEDIA CONTROLLER] {msg}")
            return True, msg, []

        if any(w in low_t for w in ["next song", "next track", "skip song", "skip track"]):
            MEDIA_CONTROLLER.next_track()
            msg = "Skipped to next track, sir."
            print(f"[MEDIA CONTROLLER] {msg}")
            return True, msg, []

        if any(w in low_t for w in ["previous song", "previous track", "prev track", "prev song"]):
            MEDIA_CONTROLLER.prev_track()
            msg = "Playing previous track, sir."
            print(f"[MEDIA CONTROLLER] {msg}")
            return True, msg, []

        # 2. Check Database for verified prior procedure
        try:
            from goal_decomposer import check_prior_knowledge
            prior = check_prior_knowledge(task_text)
            if prior and prior.get("steps"):
                print(f"[GOAL ROUTER] Found prior verified procedure for '{task_text}'. Executing...")
                ok, reply, logs = self.execute_plan(prior, status_callback=status_callback)
                if ok:
                    return True, reply, logs
                print("[GOAL ROUTER] Prior procedure encountered an obstacle. Handing over to Dynamic Vision Agent...")
        except Exception as e:
            print(f"[PRIOR LOOKUP NOTICE] {e}")

        # 3. Dynamic Vision-In-The-Loop Agent Execution
        return self.run_dynamic_vision_loop(
            task_text=task_text,
            status_callback=status_callback
        )

    def run_dynamic_vision_loop(
        self,
        task_text: str,
        app_name: str = "",
        last_problem: str = "",
        max_steps: int = 14,
        status_callback=None
    ) -> Tuple[bool, str, List[Dict[str, Any]]]:
        """
        Dynamic Vision-In-The-Loop Agent:
        Captures a desktop screenshot on EACH step and prompts Nemotron-3.5-Super-VL
        to determine the next action dynamically.
        Displays real-time 'RESPONSE BY AI' in the console.
        """
        if not app_name:
            low_t = task_text.lower()
            if "spotify" in low_t:
                app_name = "Spotify"
            elif "antigrav" in low_t or "code" in low_t:
                app_name = "Antigravity"
            elif "chrome" in low_t:
                app_name = "Chrome"
            elif "brave" in low_t:
                app_name = "Brave"
            elif "notepad" in low_t:
                app_name = "Notepad"
            else:
                app_name = "Desktop"

        print("\n" + "=" * 65, flush=True)
        print("  STARTING DYNAMIC VISION-IN-THE-LOOP AGENT", flush=True)
        print(f"  Goal: {task_text}", flush=True)
        print(f"  Target Application: {app_name.upper()}", flush=True)
        print(f"  Max Steps: {max_steps} | Dynamic Screen Analysis: ACTIVE", flush=True)
        print("=" * 65, flush=True)

        self._ensure_app_foreground(app_name)
        time.sleep(0.4)

        client = self.verifier.get_client()
        screen_w, screen_h = pyautogui.size() if pyautogui else (1920, 1080)
        history = []

        for step_idx in range(1, max_steps + 1):
            if status_callback:
                status_callback(f"Vision Agent Step {step_idx}: Analyzing screen...")

            # 1. Capture live screenshot for this specific step
            img = capture_screen_gdi()
            b64_str = self.verifier._prepare_image_b64(img)

            # Fast check: If task is to play music and playback is already active
            if any(w in task_text.lower() for w in ["play", "song", "music", "circle"]) and "spotify" in app_name.lower() and step_idx > 1:
                state = MEDIA_CONTROLLER.get_state()
                if state.get("status") == "PLAYING":
                    m_song = re.search(r"play\s+(?:the\s+song\s+)?([a-zA-Z0-9_\s]+?)(?:\s+on\s+spotify|$)", task_text.lower())
                    req_song = m_song.group(1).strip() if m_song else ""
                    if not req_song or req_song in ["song", "music", "circle"] or req_song in state.get("title", "").lower():
                        success_msg = f"Audio playback confirmed active: '{state.get('title')}' by {state.get('artist')} on {app_name}!"
                        print("\n" + "=" * 65)
                        print(f"  [GOAL ACHIEVED] {success_msg}")
                        print("=" * 65)
                        task_slug = f"{app_name.lower()}:{re.sub(r'[^a-zA-Z0-9]+', '_', task_text.lower()).strip('_')}"
                        save_successful_task_to_memory(task_slug, app_name, task_text, history)
                        return True, f"Goal achieved! Playing '{state.get('title')}' by {state.get('artist')} on {app_name}, sir.", history

            # 2. Build Vision AI prompt
            hist_str = "\n".join([f"- Step {h['step']}: {h['action']} -> Result: {h.get('result', 'ok')}" for h in history]) if history else "No previous steps executed yet."
            alert_str = f"\nATTENTION / OBSTACLE REPORT:\n{last_problem}\nPlease analyze the new screen capture to diagnose why and provide the corrective action." if last_problem else ""

            sys_prompt = (
                "You are HYDRA's Real-Time Desktop Vision-in-the-Loop Agent.\n"
                f"User Goal: '{task_text}'\n"
                f"Target Application: {app_name}\n"
                f"Step: {step_idx}/{max_steps}\n"
                f"Screen Resolution: {screen_w}x{screen_h}\n\n"
                f"Steps executed so far:\n{hist_str}\n"
                f"{alert_str}\n\n"
                "Examine the screenshot carefully. Identify the application state, determine what must be done next to make progress, and output your answer in this exact format:\n\n"
                "OBSERVATION: <describe what is currently visible on screen>\n"
                "REASONING: <why this specific next step is needed to achieve the goal>\n"
                "ACTION: <single executable command from the list below>\n\n"
                "Supported Action Commands:\n"
                "- click(x=..., y=...)  (normalized 0.0-1.0 or pixel coordinates)\n"
                "- hotkey('key1', 'key2') (e.g. hotkey('ctrl', 'l'), hotkey('ctrl', 'k'), hotkey('space'), hotkey('enter'))\n"
                "- type_text('text') (types text into active field and presses Enter)\n"
                "- open_app('app_name')\n"
                "- wait(seconds=1.0)\n"
                "- complete() (only when the goal is achieved and visually verified on screen)"
            )

            user_query = f"Goal: {task_text}. Current Step {step_idx}/{max_steps}. Analyze live screen and specify next action."
            
            try:
                res = client.chat.completions.create(
                    model=NEBIUS_VISION_MODEL,
                    messages=[
                        {"role": "system", "content": sys_prompt},
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": user_query},
                                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_str}"}}
                            ]
                        }
                    ],
                    max_tokens=600,
                    temperature=0.1,
                    timeout=35.0
                )
                raw_reply = res.choices[0].message.content or ""
                raw_reasoning = getattr(res.choices[0].message, "reasoning_content", "") or ""
            except Exception as e:
                print(f"[VISION AGENT ERROR] Vision model call failed: {e}")
                last_problem = f"Vision model API error: {e}"
                time.sleep(1.0)
                continue

            # 3. Parse Observation, Reasoning, and Action
            obs, rea, act = self._parse_vision_agent_reply(raw_reply, raw_reasoning)

            # 4. Print Prominent Heading in CMD (as explicitly requested by user)
            print("\n" + "=" * 65, flush=True)
            print(f"                       RESPONSE BY AI (Step {step_idx}/{max_steps})", flush=True)
            print("=" * 65, flush=True)
            print(f"[OBSERVATION]: {obs or 'Analyzing screen...'}", flush=True)
            print(f"[REASONING]  : {rea or 'Determining next step toward goal...'}", flush=True)
            print(f"[NEXT ACTION] : {act or 'wait(1.0)'}", flush=True)
            print("=" * 65 + "\n", flush=True)

            if status_callback:
                status_callback(f"AI Step {step_idx}: {act[:45]}")

            # 5. Check if goal is complete
            if "complete()" in act.lower() or "complete" == act.lower().strip() or "goal achieved" in act.lower():
                print(f"\n[GOAL COMPLETE] Vision Agent confirmed goal achieved at step {step_idx}!")
                task_slug = f"{app_name.lower()}:{re.sub(r'[^a-zA-Z0-9]+', '_', task_text.lower()).strip('_')}"
                save_successful_task_to_memory(task_slug, app_name, task_text, history)
                return True, f"Task '{task_text}' completed and visually verified across {step_idx} steps, sir.", history

            # 6. Execute Action
            img_w, img_h = img.size if hasattr(img, 'size') else (1707, 960)
            exec_ok, exec_msg = self._execute_agent_action_cmd(act, app_name, screen_w, screen_h, img_w, img_h)
            
            # 7. Post-action state check
            time.sleep(1.0) # Adaptive wait for UI rendering
            
            # Check for audio playback confirmation
            if ("play" in task_text.lower() or "play" in act.lower()) and "spotify" in app_name.lower():
                state = MEDIA_CONTROLLER.get_state()
                if state.get("status") == "PLAYING":
                    exec_msg += f" (Confirmed playback active: '{state.get('title')}')"
                    m_song = re.search(r"play\s+(?:the\s+song\s+)?([a-zA-Z0-9_\s]+?)(?:\s+on\s+spotify|$)", task_text.lower())
                    req_song = m_song.group(1).strip() if m_song else ""
                    if not req_song or req_song in ["song", "music", "circle"] or req_song in state.get("title", "").lower():
                        success_msg = f"Audio playback confirmed active: '{state.get('title')}' by {state.get('artist')} on {app_name}!"
                        print("\n" + "=" * 65, flush=True)
                        print(f"  [GOAL ACHIEVED] {success_msg}", flush=True)
                        print("=" * 65 + "\n", flush=True)
                        task_slug = f"{app_name.lower()}:{re.sub(r'[^a-zA-Z0-9]+', '_', task_text.lower()).strip('_')}"
                        save_successful_task_to_memory(task_slug, app_name, task_text, history)
                        return True, f"Goal achieved! Playing '{state.get('title')}' by {state.get('artist')} on {app_name}, sir.", history

            history_entry = {
                "step": step_idx,
                "observation": obs,
                "reasoning": rea,
                "action": act,
                "result": exec_msg
            }
            history.append(history_entry)

            if not exec_ok:
                last_problem = f"Action '{act}' failed: {exec_msg}"
            elif len(history) >= 2 and history[-1]["action"] == act:
                last_problem = f"Notice: The action '{act}' was already executed in the previous step, but did not progress screen state. Please inspect the fresh screen capture and take a different action (e.g. click directly on the target, use hotkey, or verify)."
            else:
                last_problem = ""

        # Finished max steps
        fail_msg = f"Vision agent completed {max_steps} steps for task '{task_text}'."
        print(f"\n[EXECUTION TIMEOUT] {fail_msg}", flush=True)
        return False, fail_msg, history

    def _parse_vision_agent_reply(self, content: str, reasoning: str = "") -> Tuple[str, str, str]:
        """Extracts OBSERVATION, REASONING, and ACTION from vision model output."""
        full_text = f"{content} {reasoning}".strip()
        obs, rea, act = "", "", ""

        m_obs = re.search(r"OBSERVATION:\s*(.*?)(?=(?:REASONING|ACTION|$))", full_text, re.DOTALL | re.I)
        if m_obs:
            obs = m_obs.group(1).strip()

        m_rea = re.search(r"REASONING:\s*(.*?)(?=(?:ACTION|$))", full_text, re.DOTALL | re.I)
        if m_rea:
            rea = m_rea.group(1).strip()
        elif reasoning:
            rea = reasoning.strip()[:300]

        m_act = re.search(r"ACTION:\s*(.*?)(?=(?:OBSERVATION|REASONING|\n\n|$))", full_text, re.DOTALL | re.I)
        if m_act:
            act = m_act.group(1).strip()
        else:
            # Check for <action> tag fallback
            m_tag = re.search(r"<action>(.*?)</action>", full_text, re.DOTALL | re.I)
            if m_tag:
                act = m_tag.group(1).strip()
            elif "complete" in full_text.lower():
                act = "complete()"
            elif "space" in full_text.lower():
                act = "hotkey('space')"
            elif "ctrl+l" in full_text.lower() or "ctrl+k" in full_text.lower():
                act = "hotkey('ctrl', 'l')"

        return obs, rea, act

    def _execute_agent_action_cmd(
        self,
        act_str: str,
        app_name: str,
        screen_w: int,
        screen_h: int,
        img_w: int = 1707,
        img_h: int = 960
    ) -> Tuple[bool, str]:
        """Executes a single action command string parsed from Vision AI."""
        if not pyautogui:
            return True, "Simulated (pyautogui not installed)"

        self._ensure_app_foreground(app_name)
        act_clean = act_str.strip()

        try:
            # 1. click(x=..., y=...)
            m_click = re.search(r"click\s*\(\s*(?:x\s*=\s*)?([0-9\.]+)\s*,\s*(?:y\s*=\s*)?([0-9\.]+)\s*\)", act_clean, re.I)
            if m_click:
                x_val = float(m_click.group(1))
                y_val = float(m_click.group(2))
                if 0.0 <= x_val <= 1.0 and 0.0 <= y_val <= 1.0:
                    px = int(x_val * screen_w)
                    py = int(y_val * screen_h)
                elif img_w > 0 and x_val <= img_w * 1.05 and y_val <= img_h * 1.05:
                    # Scaled from captured image coordinates to actual desktop pixel resolution
                    px = int((x_val / img_w) * screen_w)
                    py = int((y_val / img_h) * screen_h)
                else:
                    px = int(x_val)
                    py = int(y_val)
                pyautogui.click(px, py)
                return True, f"Clicked at ({px}, {py})"

            # 2. hotkey('k1', 'k2', ...)
            m_hotkey = re.search(r"hotkey\s*\((.*?)\)", act_clean, re.I)
            if m_hotkey:
                raw_keys = m_hotkey.group(1)
                keys = [k.strip(" '\"").lower() for k in raw_keys.split(",") if k.strip(" '\"")]
                # Spotify play/pause shortcut
                if "spotify" in app_name.lower() and ("space" in keys or "play" in act_clean.lower()):
                    MEDIA_CONTROLLER.toggle_play_pause()
                    pyautogui.press("space")
                    return True, "Toggled Spotify playback"
                # Spotify search shortcut
                if "spotify" in app_name.lower() and (("ctrl" in keys and "l" in keys) or ("ctrl" in keys and "k" in keys)):
                    pyautogui.hotkey("ctrl", "k")
                    time.sleep(0.1)
                    pyautogui.hotkey("ctrl", "l")
                    return True, "Triggered Spotify search shortcut"
                pyautogui.hotkey(*keys)
                return True, f"Pressed hotkey {'+'.join(keys)}"

            # 3. type_text('text')
            m_type = re.search(r"type(?:_text)?\s*\(\s*['\"](.*?)['\"]\s*\)", act_clean, re.I)
            if m_type:
                text_to_type = m_type.group(1)
                # If Spotify and typing search query, ensure search input is focused first
                if "spotify" in app_name.lower():
                    search_px = int(0.454 * screen_w)
                    search_py = int(0.036 * screen_h)
                    pyautogui.click(search_px, search_py)
                    time.sleep(0.25)
                    pyautogui.hotkey("ctrl", "a")
                    time.sleep(0.1)
                else:
                    pyautogui.hotkey("ctrl", "a")
                    time.sleep(0.1)

                if pyperclip:
                    pyperclip.copy(text_to_type)
                    pyautogui.hotkey("ctrl", "v")
                else:
                    pyautogui.typewrite(text_to_type, interval=0.03)
                time.sleep(0.25)
                pyautogui.press("enter")
                return True, f"Typed text '{text_to_type}' and pressed Enter"

            # 4. open_app('app_name')
            m_open = re.search(r"open(?:_app)?\s*\(\s*['\"](.*?)['\"]\s*\)", act_clean, re.I)
            if m_open:
                target_app = m_open.group(1)
                self._ensure_app_foreground(target_app)
                return True, f"Switched to application '{target_app}'"

            # 5. wait(seconds=...)
            m_wait = re.search(r"wait\s*\(\s*(?:seconds\s*=\s*)?([0-9\.]+)\s*\)", act_clean, re.I)
            if m_wait:
                sec = float(m_wait.group(1))
                time.sleep(sec)
                return True, f"Waited {sec} seconds"

            # 6. Semantic Spotify Actions
            low_act = act_clean.lower()
            if "spotify" in app_name.lower():
                if any(w in low_act for w in ["first song", "top result", "first result", "song row"]):
                    song_px = int(0.35 * screen_w)
                    song_py = int(0.33 * screen_h)
                    pyautogui.click(song_px, song_py)
                    time.sleep(0.3)
                    MEDIA_CONTROLLER.play()
                    return True, f"Clicked top search result at ({song_px}, {song_py})"

                if any(w in low_act for w in ["play button", "green circle", "play track", "start playback"]) and "search" not in low_act:
                    MEDIA_CONTROLLER.toggle_play_pause()
                    pyautogui.press("space")
                    return True, "Triggered Spotify playback via play action"

            # Fallback direct keyboard commands
            if "space" in act_clean.lower():
                if "spotify" in app_name.lower():
                    MEDIA_CONTROLLER.toggle_play_pause()
                pyautogui.press("space")
                return True, "Pressed space bar"

            if "enter" in act_clean.lower():
                pyautogui.press("enter")
                return True, "Pressed enter"

            return True, f"Action '{act_clean}' executed"

        except Exception as e:
            return False, f"Actuation error: {e}"


# Singleton instance
VERIFIED_EXECUTOR = VerifiedStepExecutor()
