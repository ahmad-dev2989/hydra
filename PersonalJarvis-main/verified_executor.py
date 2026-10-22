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

from visual_verifier import VISUAL_VERIFIER
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
            
            v_ok, v_details, v_raw = self.verifier.adaptive_wait_until_verified(
                step_description=f"Step {idx}: {details}",
                verification_prompt=verify_prompt,
                app_name=app_name,
                timeout=6.5,
                interval=0.5,
                status_callback=status_callback
            )

            step_record["verified"] = v_ok
            step_record["visual_observation"] = v_details
            execution_log.append(step_record)

            if not v_ok:
                fail_msg = f"Visual verification failed at step {idx} ({action_type}): {v_details}"
                print(f"[EXECUTION FAILED] {fail_msg}")
                return False, fail_msg, execution_log

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
        """Ensures the application window is brought to foreground."""
        low_app = app_name.lower()
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
        # Always guarantee target application is focused before any input
        self._ensure_app_foreground(app_name)

        if not pyautogui:
            return True, "Simulated (pyautogui not installed)"

        try:
            if action_type == "open_app":
                self._ensure_app_foreground(target or app_name)
                return True, f"Focused/opened {target or app_name}"

            elif action_type == "hotkey":
                # Parse hotkey string like "Ctrl+L" or "Ctrl+Shift+P"
                keys = [k.strip().lower() for k in re.split(r'[\+\s]+', target) if k.strip()]
                if not keys and "ctrl+l" in details.lower():
                    keys = ["ctrl", "l"]
                elif not keys and "ctrl+shift+p" in details.lower():
                    keys = ["ctrl", "shift", "p"]
                
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

                # Use clipboard paste for instant, 100% accurate text entry
                if pyperclip:
                    pyperclip.copy(text_to_type)
                    pyautogui.hotkey("ctrl", "v")
                else:
                    pyautogui.typewrite(text_to_type, interval=0.03)

                time.sleep(0.2)
                if "enter" in details.lower():
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
                # If clicking Play in Spotify, space bar is the universal instant trigger
                if "spotify" in app_name.lower() and "play" in details.lower():
                    pyautogui.press("space")
                    return True, "Triggered Spotify playback via Space/Play action"
                
                # If adding to liked songs in Spotify, use native shortcut or safe click
                if "spotify" in app_name.lower() and ("plus" in details.lower() or "add" in details.lower()):
                    # Alt+Shift+B is Spotify native shortcut to save current track to Liked Songs
                    pyautogui.hotkey("alt", "shift", "b")
                    time.sleep(0.2)
                    return True, "Saved song to Liked Songs via Spotify shortcut"

                # If navigating to liked songs on sidebar
                if "spotify" in app_name.lower() and "liked" in details.lower():
                    # Click on left sidebar playlist area
                    pyautogui.click(140, 310)
                    time.sleep(0.3)
                    return True, "Navigated to Liked Songs in left sidebar"

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

        if action_type == "open_app" or "open" in low_det:
            return f"Verify if {app_name} is currently open and visible on screen with its main interface loaded."

        if action_type == "hotkey" or "ctrl+l" in low_det:
            return f"In {app_name}, is the search bar focused, active, or ready for text input?"

        if action_type == "type_text" or "type" in low_det:
            match = re.search(r"['\"](.*?)['\"]", details)
            txt = match.group(1) if match else "query text"
            return f"In {app_name}, is '{txt}' entered into the search bar, or are search results starting to appear?"

        if "results" in low_det or "search" in low_det:
            return f"In {app_name}, are search results or tracks displayed on the screen?"

        if "play" in low_det or "playback" in low_det:
            return f"In {app_name}, is audio playing or is a play/pause button or track progress bar visible on the playback bar?"

        if "liked" in low_det or "plus" in low_det or "playlist" in low_det:
            return f"In {app_name}, is the song present in Liked Songs, added to the playlist, or is the Liked Songs view visible and active?"

        if "theme" in low_det:
            return f"In {app_name}, has the color theme changed or is the theme selection menu visible?"

        # Default fallback visual prompt
        return f"Verify if the following step succeeded on screen: '{details}' in {app_name}."


# Singleton instance
VERIFIED_EXECUTOR = VerifiedStepExecutor()
