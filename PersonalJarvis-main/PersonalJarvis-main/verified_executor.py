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
import random
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

try:
    import cv2
    import numpy as np
    from PIL import Image
except ImportError:
    cv2 = None
    np = None
    Image = None

# Path to persistent prior knowledge database
TASK_MEMORY_FILE = CURRENT_DIR / "learned_task_memory.json"
if not TASK_MEMORY_FILE.exists() and (PARENT_DIR / "learned_task_memory.json").exists():
    TASK_MEMORY_FILE = PARENT_DIR / "learned_task_memory.json"
elif not TASK_MEMORY_FILE.exists() and (GRANDPARENT_DIR / "learned_task_memory.json").exists():
    TASK_MEMORY_FILE = GRANDPARENT_DIR / "learned_task_memory.json"

# Visual templates directory for non-AI icon/patch matching
TEMPLATES_DIR = CURRENT_DIR / "visual_templates"
TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
if PARENT_DIR.exists():
    try:
        (PARENT_DIR / "visual_templates").mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
# Known Web Service URLs for direct navigation and background launch
WEB_SERVICE_URLS = {
    "Gmail": "https://mail.google.com",
    "Google Classroom": "https://classroom.google.com",
    "YouTube": "https://www.youtube.com",
    "Canva": "https://www.canva.com",
    "Instagram": "https://www.instagram.com",
    "Facebook": "https://www.facebook.com",
    "LinkedIn": "https://www.linkedin.com",
    "WhatsApp": "https://web.whatsapp.com",
    "Spotify": "https://open.spotify.com"
}

def get_target_url_for_app(app_name: str) -> str:
    """Returns the direct service URL, embedding connected account parameters (e.g. authuser) when available."""
    url = WEB_SERVICE_URLS.get(app_name, "")
    if not url:
        return ""
    try:
        plg_file = CURRENT_DIR / "connected_plugins.json"
        if not plg_file.exists() and (PARENT_DIR / "connected_plugins.json").exists():
            plg_file = PARENT_DIR / "connected_plugins.json"
        if plg_file.exists():
            with open(plg_file, "r", encoding="utf-8") as pf:
                pdata = json.load(pf)
            plg_key = app_name.lower().replace(" ", "_")
            acc = pdata.get(plg_key, {}).get("account")
            if acc and ("google" in url or "gmail" in url):
                if "mail.google.com" in url:
                    return f"https://mail.google.com/mail/u/?authuser={acc}"
                elif "classroom.google.com" in url:
                    return f"https://classroom.google.com/u/?authuser={acc}"
    except Exception:
        pass
    return url


# Nebius AI Configuration for Fast Trajectory Distillation
NEBIUS_API_KEY = os.environ.get("NEBIUS_API_KEY", "").strip()
NEBIUS_BASE_URL = os.environ.get("NEBIUS_BASE_URL", "https://api.tokenfactory.nebius.com/v1/").strip()
NEBIUS_FAST_MODEL = os.environ.get("NEBIUS_FAST_MODEL", "nvidia/Nemotron-3_5-Lightning").strip()
if NEBIUS_FAST_MODEL.startswith("nebius/"):
    NEBIUS_FAST_MODEL = NEBIUS_FAST_MODEL[7:]


def _is_terminal_step(step: Dict[str, Any]) -> bool:
    """Detects whether a step is a termination/completion signal or empty action."""
    if not isinstance(step, dict):
        return True
    act = str(step.get("action", "")).lower().strip()
    return not act or "complete()" in act or act == "complete" or "task completed" in act


def _cleanup_pruned_visual_templates(pruned_steps: List[Dict[str, Any]]):
    """Safely deletes orphaned visual template icon files for discarded mistake steps."""
    for s in pruned_steps:
        if not isinstance(s, dict):
            continue
        tpl = s.get("visual_template")
        if not tpl:
            continue
        try:
            p = Path(tpl)
            candidates = [
                CURRENT_DIR / p,
                PARENT_DIR / p,
                TEMPLATES_DIR / p
            ]
            for c in candidates:
                if c.exists() and c.is_file():
                    c.unlink(missing_ok=True)
                    print(f"[DISTILLATION] Deleted orphaned mistake visual template: {c.name}", flush=True)
        except Exception as e:
            print(f"[DISTILLATION NOTICE] Could not delete visual template {tpl}: {e}", flush=True)


def _rule_based_distill(app_name: str, intent_name: str, steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Deterministic rule-based trajectory purifier:
    1. Prunes steps on wrong applications opened and abandoned before the target app.
    2. Prunes cancellation pairs (e.g. open mistake -> alt+f4 or escape).
    3. Prunes consecutive duplicate clicks on identical coordinates.
    4. Drops terminal complete() steps.
    """
    if not steps or len(steps) <= 1:
        return [s for s in steps if not _is_terminal_step(s)]

    target_app = (app_name or "").lower().strip()
    
    # 1. Identify first appearance of target app
    target_app_idx = -1
    if target_app and target_app not in ["desktop", ""]:
        for i, s in enumerate(steps):
            act = str(s.get("action", "")).lower()
            obs = str(s.get("observation", "")).lower()
            rea = str(s.get("reasoning", "") or s.get("details", "")).lower()
            if target_app in act or target_app in obs or target_app in rea:
                target_app_idx = i
                break

    detour_indices = set()
    # If target app was opened after earlier detour steps opening other apps
    if target_app_idx > 0:
        for i in range(target_app_idx):
            act = str(steps[i].get("action", "")).lower()
            if "open_app" in act or "hotkey" in act or "click" in act:
                detour_indices.add(i)

    # 2. Check cancellation pairs (e.g. alt+f4 or escape closing a previous mistake)
    for i, s in enumerate(steps):
        act = str(s.get("action", "")).lower()
        if "alt" in act and "f4" in act:
            detour_indices.add(i)
            if i > 0 and (i - 1) not in detour_indices:
                detour_indices.add(i - 1)

    # 3. Detect consecutive duplicate clicks
    for i in range(len(steps) - 1):
        if i in detour_indices:
            continue
        act1 = str(steps[i].get("action", "")).lower()
        act2 = str(steps[i + 1].get("action", "")).lower()
        m1 = re.search(r"click\s*\(\s*(?:x\s*=\s*)?([0-9\.]+)\s*,\s*(?:y\s*=\s*)?([0-9\.]+)\s*\)", act1)
        m2 = re.search(r"click\s*\(\s*(?:x\s*=\s*)?([0-9\.]+)\s*,\s*(?:y\s*=\s*)?([0-9\.]+)\s*\)", act2)
        if m1 and m2:
            try:
                x1, y1 = float(m1.group(1)), float(m1.group(2))
                x2, y2 = float(m2.group(1)), float(m2.group(2))
                dist = ((x1 - x2)**2 + (y1 - y2)**2)**0.5
                if dist < 0.05:  # Clicked same element twice in a row without typing
                    detour_indices.add(i)  # Drop earlier redundant click
            except Exception:
                pass

    filtered = []
    for i, s in enumerate(steps):
        if i in detour_indices or _is_terminal_step(s):
            continue
        filtered.append(s)

    return filtered if filtered else [s for s in steps if not _is_terminal_step(s)]


def distill_trajectory_steps(
    task_key: str,
    app_name: str,
    intent_name: str,
    raw_steps: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """
    Distills and optimizes execution history before persisting to memory:
    1. Runs AI Trajectory Distillation (via fast LLM) to detect and prune detours,
       misclicks, wrong app openings/closings, and dead-ends.
    2. Falls back to deterministic rule-based pruning if LLM is unavailable or fails.
    3. Cleans up orphaned visual template images from disk for pruned mistake steps.
    4. Re-indexes steps sequentially.
    """
    if not raw_steps or len(raw_steps) <= 1:
        return [s for s in raw_steps if not _is_terminal_step(s)]

    # Filter out empty or pure completion steps first
    working_steps = [s for s in raw_steps if s and not _is_terminal_step(s)]
    if len(working_steps) <= 1:
        return working_steps

    distilled_indices = None
    target_app_clean = (app_name or "").lower().strip()

    # 1. Attempt AI Distillation with Nebius LLM
    if NEBIUS_API_KEY:
        try:
            from openai import OpenAI
            client = OpenAI(
                base_url=NEBIUS_BASE_URL,
                api_key=NEBIUS_API_KEY,
                timeout=18.0
            )

            formatted = []
            for idx, s in enumerate(working_steps, 1):
                formatted.append({
                    "step_number": idx,
                    "action": s.get("action", ""),
                    "observation": str(s.get("observation", ""))[:120],
                    "details": str(s.get("reasoning", "") or s.get("details", ""))[:120]
                })

            prompt = (
                f"You are HYDRA's Trajectory Distillation Engine.\n"
                f"A desktop automation agent executed actions to achieve the following goal:\n"
                f"Target Application: '{app_name}'\n"
                f"User Goal: '{intent_name}'\n\n"
                f"TASK EVALUATION RULES:\n"
                f"1. ONLY prune steps if there were clear mistakes: such as opening an unrelated/wrong application and closing it, "
                f"opening an accidental dialog and escaping it, misclicking, or clicking the exact same button multiple times before typing.\n"
                f"2. DO NOT prune legitimate steps that belong to '{app_name}'! Opening '{app_name}', navigating its menus, settings, search bars, and playing items are essential steps and MUST BE KEPT.\n"
                f"3. If the trajectory is already direct and has NO mistakes, set \"detours_found\": false and include all step numbers in \"retained_step_numbers\".\n\n"
                f"Recorded trajectory ({len(working_steps)} steps):\n"
                f"{json.dumps(formatted, indent=2)}\n\n"
                f"Output valid JSON only matching schema: {{\"detours_found\": bool, \"pruned_step_numbers\": [int], \"retained_step_numbers\": [int], \"reasoning\": str}}"
            )

            try:
                res = client.chat.completions.create(
                    model=NEBIUS_FAST_MODEL,
                    messages=[
                        {"role": "system", "content": "You are a trajectory distillation and action optimizer engine. Output valid JSON only: {\"detours_found\": bool, \"pruned_step_numbers\": [int], \"retained_step_numbers\": [int], \"reasoning\": str}"},
                        {"role": "user", "content": prompt}
                    ],
                    max_tokens=2000,
                    temperature=0.1,
                    extra_body={"chat_template_kwargs": {"thinking": False}}
                )
            except Exception:
                res = client.chat.completions.create(
                    model=NEBIUS_FAST_MODEL,
                    messages=[
                        {"role": "system", "content": "You are a trajectory distillation and action optimizer engine. Output valid JSON only: {\"detours_found\": bool, \"pruned_step_numbers\": [int], \"retained_step_numbers\": [int], \"reasoning\": str}"},
                        {"role": "user", "content": prompt}
                    ],
                    max_tokens=2000,
                    temperature=0.1
                )

            content = res.choices[0].message.content or ""

            # Robust JSON extraction
            parsed = None
            for match in reversed(re.findall(r'```(?:json)?\s*(\{.*?\})\s*```', content, re.DOTALL)):
                try:
                    d = json.loads(match)
                    if "retained_step_numbers" in d:
                        parsed = d
                        break
                except Exception:
                    pass

            if not parsed:
                starts = [i for i, c in enumerate(content) if c == '{']
                for start in reversed(starts):
                    for end in range(len(content), start, -1):
                        if content[end-1] == '}':
                            sub = content[start:end]
                            if '"retained_step_numbers"' in sub:
                                try:
                                    parsed = json.loads(sub)
                                    break
                                except Exception:
                                    pass
                    if parsed:
                        break

            if parsed:
                detours_found = parsed.get("detours_found", False)
                ret = parsed.get("retained_step_numbers")
                if not detours_found:
                    print(f"\n[TRAJECTORY DISTILLATION] AI Engine confirmed clean trajectory for '{task_key}': all {len(working_steps)} steps retained.", flush=True)
                    distilled_indices = list(range(len(working_steps)))
                elif isinstance(ret, list) and len(ret) > 0:
                    valid_ret = [x for x in ret if isinstance(x, int) and 1 <= x <= len(working_steps)]
                    # Safeguard: Never allow AI to prune target application launch step
                    first_act = str(working_steps[0].get("action", "")).lower()
                    if target_app_clean and target_app_clean in first_act and 1 not in valid_ret:
                        valid_ret.insert(0, 1)

                    if len(valid_ret) > 0:
                        distilled_indices = [x - 1 for x in sorted(set(valid_ret))]
                        print(f"\n[TRAJECTORY DISTILLATION] AI Engine identified minimal critical path for '{task_key}': {valid_ret}", flush=True)
                        if parsed.get("reasoning"):
                            print(f"[TRAJECTORY DISTILLATION] Pruning Rationale: {parsed.get('reasoning')}", flush=True)
        except Exception as e:
            print(f"[TRAJECTORY DISTILLATION NOTICE] AI Distillation skipped: {e}. Falling back to Rule-Based purifier.", flush=True)

    # 2. Select kept vs pruned steps
    if distilled_indices is not None and len(distilled_indices) > 0:
        kept_steps = [working_steps[i] for i in distilled_indices]
        pruned_steps = [working_steps[i] for i in range(len(working_steps)) if i not in distilled_indices]
    else:
        kept_steps = _rule_based_distill(app_name, intent_name, working_steps)
        kept_set = set(id(s) for s in kept_steps)
        pruned_steps = [s for s in working_steps if id(s) not in kept_set]

    # 3. Clean up orphaned visual templates from disk
    if pruned_steps:
        _cleanup_pruned_visual_templates(pruned_steps)
        print(f"[TRAJECTORY DISTILLATION] Optimized '{task_key}': pruned {len(pruned_steps)} detour/mistake step(s). Kept {len(kept_steps)} essential step(s).\n", flush=True)
    else:
        print(f"[TRAJECTORY DISTILLATION] Trajectory for '{task_key}' is clean (0 detours detected).\n", flush=True)

    return kept_steps


def save_successful_task_to_memory(task_key: str, app_name: str, intent_name: str, steps: List[Dict[str, Any]]):
    """
    Saves or replaces a fully visually verified action sequence into learned_task_memory.json
    and learned_skills.json for future zero-delay execution.
    Automatically distills the recorded trajectory to prune mistakes, wrong app openings,
    and misclicks before persisting to memory.
    """
    try:
        tasks_data = {}
        if TASK_MEMORY_FILE.exists():
            try:
                with open(TASK_MEMORY_FILE, "r", encoding="utf-8") as f:
                    tasks_data = json.load(f)
            except Exception:
                tasks_data = {}

        # 1. Distill and optimize trajectory (prune detours & mistake templates)
        distilled_raw = distill_trajectory_steps(task_key, app_name, intent_name, steps)

        clean_steps = []
        for s in distilled_raw:
            if isinstance(s, dict):
                act_str = s.get("action") or ""
                if not act_str or "complete" in act_str.lower():
                    continue
                step_dict = {
                    "step": len(clean_steps) + 1,
                    "action": act_str,
                    "target": s.get("target", ""),
                    "details": s.get("reasoning", "") or act_str,
                    "observation": s.get("observation", "")
                }
                if "visual_template" in s:
                    step_dict["visual_template"] = s["visual_template"]
                if "template_coords" in s:
                    step_dict["template_coords"] = s["template_coords"]
                clean_steps.append(step_dict)

        # Ensure first step opens target application if not desktop
        if app_name and app_name.lower() not in ["desktop", ""]:
            has_open = any("open_app" in str(s.get("action", "")).lower() for s in clean_steps)
            if not has_open:
                clean_steps.insert(0, {
                    "step": 1,
                    "action": f"open_app('{app_name}')",
                    "target": app_name,
                    "details": f"Open and focus {app_name}",
                    "observation": f"Ensure {app_name} is active"
                })
                for i, cs in enumerate(clean_steps, 1):
                    cs["step"] = i

        record = {
            "id": task_key,
            "application": app_name.lower(),
            "intent": intent_name.upper(),
            "steps": clean_steps,
            "verified_by": "nvidia/Nemotron-3.5-Super-VL",
            "success_count": tasks_data.get(task_key, {}).get("success_count", 0) + 1,
            "last_verified_timestamp": time.time()
        }

        tasks_data[task_key] = record

        # Save across candidate locations
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

        # Also update learned_skills.json if task_key exists
        for sk_cand in [
            CURRENT_DIR / "learned_skills.json",
            PARENT_DIR / "learned_skills.json",
            GRANDPARENT_DIR / "learned_skills.json"
        ]:
            if sk_cand.exists():
                try:
                    with open(sk_cand, "r", encoding="utf-8") as sf:
                        skills_data = json.load(sf)
                    if task_key in skills_data:
                        skills_data[task_key]["steps"] = clean_steps
                        skills_data[task_key]["compiled_at"] = time.time()
                        skills_data[task_key]["success_count"] = skills_data[task_key].get("success_count", 0) + 1
                        with open(sk_cand, "w", encoding="utf-8") as sf:
                            json.dump(skills_data, sf, indent=2)
                except Exception:
                    pass

        print(f"[MEMORY] Successfully updated verified procedure for '{task_key}' in database ({len(clean_steps)} steps).")
    except Exception as e:
        print(f"[MEMORY ERROR] Could not save task to database: {e}")


def is_music_playback_task(task_text: str = "", app_name: str = "") -> bool:
    """Detects whether a command or task involves playing music, songs, or audio."""
    t = (task_text or "").lower().strip()
    a = (app_name or "").lower().strip()

    if any(w in t for w in ["song", "music", "track", "playlist", "album", "audio"]):
        return True
    if "spotify" in a or "spotify" in t:
        if any(w in t for w in ["play", "resume", "listen", "unmute", "start"]):
            return True
    if t.startswith("play "):
        return True
    if any(w in t for w in ["resume playing", "resume music", "resume song", "resume playback"]):
        return True
    return False


def _generate_humanized_completion_speech(task_text: str = "", app_name: str = "", observation: str = "") -> str:
    """
    Generates humanized, polite, non-hardcoded voice responses for task completion.
    CRITICAL RULE: For playing music/songs, returns empty string ("") so HYDRA
    remains completely silent and does not talk over the music.
    """
    t_low = (task_text or "").lower().strip()
    a_low = (app_name or "").lower().strip()

    # Rule 1: Music / Song / Audio playback -> TOTAL SILENCE so the music plays immediately
    if is_music_playback_task(task_text, app_name):
        return ""

    # Rule 2: Email, assignment, notification, and check inspection tasks
    if any(w in t_low for w in ["check", "read", "view", "what", "any", "due", "unread", "inspect", "email", "mail", "inbox", "assignment"]):
        clean_obs = re.sub(r'^(?:observation:?\s*)+', '', (observation or ""), flags=re.I).strip()
        if clean_obs and len(clean_obs) > 8:
            first_sent = clean_obs.split(". ")[0].strip()
            if not first_sent.endswith("."):
                first_sent += "."
            return f"Sir, I checked your {app_name}. {first_sent}"
        return f"Sir, I've checked your {app_name} for you."

    # Rule 3: Theme / Appearance change tasks
    if any(w in t_low for w in ["theme", "theem", "dark mode", "light mode", "color theme"]):
        theme_variations = [
            "The theme has been updated for you, sir.",
            "I've switched the theme for you, sir.",
            "Theme changed successfully, sir.",
            "All set, sir. The theme has been updated.",
            "Sir, your theme preferences have been applied.",
            "Sir, your task has been completed.",
        ]
        return random.choice(theme_variations)

    # Rule 3: Application launching / opening
    if any(t_low.startswith(w) for w in ["open ", "launch ", "start "]) and len(t_low.split()) <= 4:
        app_variations = [
            "I've opened that for you, sir.",
            "All set, sir. The application is ready.",
            "Your application is open and ready to use, sir.",
            "Opened and ready for you, sir.",
            "Sir, your task has been completed.",
        ]
        return random.choice(app_variations)

    # Rule 4: General tasks - Humanized, varied, polite responses
    general_variations = [
        "Sir, your task has been completed.",
        "Your task is complete, sir.",
        "I've taken care of that for you, sir.",
        "All done, sir. Your task has been completed.",
        "Sir, I have finished your task.",
        "Everything is done, sir.",
        "I've completed that task for you, sir.",
        "All done, sir. Everything is taken care of.",
        "Your request has been completed, sir.",
    ]
    return random.choice(general_variations)


class VerifiedStepExecutor:
    """
    Executes each step of a goal plan, gating every action with visual verification
    and slow-PC adaptive retries.
    """

    def __init__(self):
        self.verifier = VISUAL_VERIFIER
        self._voice_cb = None
        self._hud_cb = None

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

        success_reply = _generate_humanized_completion_speech(task_name, app_name)
        return True, success_reply, execution_log

    def execute_saved_plan_with_verification(
        self,
        task_text: str,
        plan: Dict[str, Any],
        status_callback=None
    ) -> Tuple[bool, str, List[Dict[str, Any]]]:
        """
        Executes a saved prior task with before-and-after visual verification:
        1. Ensures the target application is opened and focused in the foreground.
        2. Captures INITIAL screen (Image 1) of the opened application (NOT the HYDRA screen).
        3. Executes the fast saved steps.
        4. Captures FINAL screen (Image 2) of the application.
        5. Vision AI compares Image 1 and Image 2 to verify if the goal was achieved.
        """
        app_name = plan.get("app") or plan.get("application") or ""
        steps = plan.get("steps", [])

        if not app_name:
            low_t = task_text.lower()
            if "spotify" in low_t:
                app_name = "Spotify"
            elif "whatsapp" in low_t:
                app_name = "WhatsApp"
            elif "canva" in low_t:
                app_name = "Canva"
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
        print(f"  EXECUTING SAVED TASK FROM DATABASE: {task_text}", flush=True)
        print(f"  Target Application: {app_name.upper()}", flush=True)
        print(f"  Total Saved Steps: {len(steps)}", flush=True)
        print("=" * 65, flush=True)

        if status_callback:
            status_callback(f"Opening/focusing {app_name}...")

        # 1. Bring application to foreground / launch if needed
        first_step_is_open = False
        remaining_steps = list(steps)
        if steps:
            first_act = (steps[0].get("action") or steps[0].get("action_type") or steps[0].get("type") or "").lower()
            if any(k in first_act for k in ["open_app", "open_application", "focus_window", "launch"]):
                first_step_is_open = True
                print(f"[SAVED TASK] Executing initial step 1 to launch/focus '{app_name}'...", flush=True)
                self._perform_action(first_act, steps[0].get("target", app_name), steps[0].get("details", ""), app_name, step_item=steps[0])
                remaining_steps = steps[1:]
                time.sleep(1.0)
            else:
                self._ensure_app_foreground(app_name)
                time.sleep(0.8)
        else:
            self._ensure_app_foreground(app_name)
            time.sleep(0.8)

        # 2. Capture INITIAL screen of opened app (before actions)
        print(f"[SAVED TASK] Capturing INITIAL screen (Image 1) of opened '{app_name}' before performing actions...", flush=True)
        if status_callback:
            status_callback(f"Capturing initial state of {app_name}...")
        img_initial = capture_screen_gdi()

        # 3. Fast execution of saved steps
        execution_log = []
        if first_step_is_open:
            execution_log.append({
                "step": 1,
                "action": "open_app",
                "target": app_name,
                "details": f"Opened/focused {app_name}",
                "result": "ok"
            })

        for idx, step_item in enumerate(remaining_steps, len(execution_log) + 1):
            action_type = (step_item.get("action") or step_item.get("action_type") or step_item.get("type") or "").lower()
            target = step_item.get("target") or (",".join(step_item.get("keys", [])) if "keys" in step_item else "")
            details = step_item.get("details") or step_item.get("description") or ""

            if "complete" in action_type:
                continue

            print(f"[SAVED STEP {idx:02d}/{len(steps):02d}] {action_type.upper()}: {details or target}", flush=True)
            if status_callback:
                status_callback(f"Saved Step {idx}/{len(steps)}: {details[:40] or target}")

            act_ok, act_msg = self._perform_action(action_type, target, details, app_name, step_item=step_item)
            execution_log.append({
                "step": idx,
                "action": action_type,
                "target": target,
                "details": details,
                "result": act_msg
            })
            time.sleep(0.15)

        # 4. Wait for UI rendering and capture FINAL screen (after actions)
        time.sleep(1.0)
        print(f"[SAVED TASK] Capturing FINAL screen (Image 2) of '{app_name}' after actions...", flush=True)
        if status_callback:
            status_callback("Verifying outcome with Vision AI...")
        img_final = capture_screen_gdi()

        # Fast SMTC check for music playback
        if "spotify" in app_name.lower() and any(w in task_text.lower() for w in ["play", "song", "music"]):
            state = MEDIA_CONTROLLER.get_state()
            if state.get("status") == "PLAYING":
                msg = f"Audio playback confirmed active: '{state.get('title')}' by {state.get('artist')} on Spotify, sir."
                print(f"\n[SAVED TASK VERIFIED] {msg}", flush=True)
                return True, "", execution_log

        # 5. Vision AI compares Image 1 and Image 2
        print(f"[SAVED TASK] Sending Before (Image 1) and After (Image 2) to Vision AI for verification...", flush=True)
        verified, reason = self.verifier.compare_before_after_state(
            goal=task_text,
            img_initial=img_initial,
            img_final=img_final,
            app_name=app_name
        )
        print(f"[SAVED TASK VERIFICATION RESULT]: completed={verified} | Reason: {reason}\n", flush=True)

        if verified:
            speech_reply = _generate_humanized_completion_speech(task_text, app_name)
            return True, speech_reply, execution_log
        else:
            return False, f"Visual comparison showed task incomplete: {reason}", execution_log

    def _ensure_app_foreground(self, app_name: str):
        """Ensures the application window is brought to foreground without stealing focus if active."""
        if not app_name or app_name.lower() in ["desktop", ""]:
            return
        try:
            import ctypes
            u = ctypes.windll.user32
            fg_hwnd = u.GetForegroundWindow()
            win = WINDOW_MGR.resolve_window(app_name)
            if win and fg_hwnd == win.get("hwnd"):
                return
            ok, _ = WINDOW_MGR.switch_to(app_name)
            if not ok:
                target_url = get_target_url_for_app(app_name)
                if target_url:
                    import webbrowser
                    webbrowser.open(target_url)
                    time.sleep(1.5)
                    return
                if pyautogui:
                    pyautogui.press("win")
                    time.sleep(0.3)
                    pyautogui.write(app_name, interval=0.02)
                    time.sleep(0.4)
                    pyautogui.press("enter")
                    time.sleep(1.2)
            else:
                time.sleep(0.4)
        except Exception as e:
            print(f"[FOCUS ERROR] {e}")

    def find_visual_template_on_screen(
        self,
        template_path_str: str,
        screen_img: Optional[Any] = None,
        min_confidence: float = 0.70
    ) -> Optional[Tuple[int, int]]:
        """
        Non-AI Visualizer: Uses fast OpenCV template matching to find a saved icon patch
        on the current screen, even if the application window was moved, snapped, or repositioned.
        Returns (x, y) screen pixel coordinates of the matched icon center, or None if not found.
        """
        if not cv2 or not np:
            return None

        try:
            # Resolve template path
            p = Path(template_path_str)
            if not p.is_absolute():
                candidate = CURRENT_DIR / p
                if not candidate.exists() and (PARENT_DIR / p).exists():
                    candidate = PARENT_DIR / p
                elif not candidate.exists() and (GRANDPARENT_DIR / p).exists():
                    candidate = GRANDPARENT_DIR / p
                p = candidate

            if not p.exists():
                return None

            # Load template image
            tpl_pil = Image.open(p)
            tpl_w, tpl_h = tpl_pil.size
            if tpl_w < 10 or tpl_h < 10:
                return None

            # Capture current screen if not provided
            if screen_img is None:
                screen_img = capture_screen_gdi()
                if not screen_img:
                    return None

            # Convert to numpy BGR arrays
            screen_np = np.array(screen_img)
            tpl_np = np.array(tpl_pil)

            if screen_np.shape[-1] == 4:
                screen_bgr = cv2.cvtColor(screen_np, cv2.COLOR_RGBA2BGR)
            else:
                screen_bgr = cv2.cvtColor(screen_np, cv2.COLOR_RGB2BGR)

            if tpl_np.shape[-1] == 4:
                tpl_bgr = cv2.cvtColor(tpl_np, cv2.COLOR_RGBA2BGR)
            else:
                tpl_bgr = cv2.cvtColor(tpl_np, cv2.COLOR_RGB2BGR)

            best_val = -1.0
            best_loc = None
            best_scale = 1.0

            # Multi-scale check for window zoom or DPI scaling differences
            for scale in [1.0, 0.95, 1.05, 0.90, 1.10]:
                if scale == 1.0:
                    scaled_tpl = tpl_bgr
                else:
                    sw = int(tpl_w * scale)
                    sh = int(tpl_h * scale)
                    if sw >= screen_bgr.shape[1] or sh >= screen_bgr.shape[0] or sw < 10 or sh < 10:
                        continue
                    scaled_tpl = cv2.resize(tpl_bgr, (sw, sh), interpolation=cv2.INTER_LINEAR)

                res = cv2.matchTemplate(screen_bgr, scaled_tpl, cv2.TM_CCOEFF_NORMED)
                min_v, max_v, min_l, max_l = cv2.minMaxLoc(res)

                if max_v > best_val:
                    best_val = max_v
                    best_loc = max_l
                    best_scale = scale

            if best_val >= min_confidence and best_loc is not None:
                sw = int(tpl_w * best_scale)
                sh = int(tpl_h * best_scale)
                match_center_x = best_loc[0] + sw // 2
                match_center_y = best_loc[1] + sh // 2

                screen_w, screen_h = pyautogui.size() if pyautogui else (1920, 1080)
                img_w, img_h = screen_img.size
                if img_w > 0 and img_h > 0:
                    screen_x = int((match_center_x / img_w) * screen_w)
                    screen_y = int((match_center_y / img_h) * screen_h)
                else:
                    screen_x, screen_y = match_center_x, match_center_y

                print(f"[NON-AI VISUALIZER] Located visual icon '{p.name}' at screen ({screen_x}, {screen_y}) with confidence {best_val:.2f} (scale: {best_scale:.2f})", flush=True)
                return screen_x, screen_y
            else:
                if best_val > 0.40:
                    print(f"[NON-AI VISUALIZER] Visual template '{p.name}' best match {best_val:.2f} is below threshold {min_confidence}", flush=True)
                return None

        except Exception as e:
            print(f"[NON-AI VISUALIZER ERROR] {e}", flush=True)
            return None

    def _perform_action(self, action_type: str, target: str, details: str, app_name: str, step_item: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
        """Performs native actuation: launching, typing, hotkey pressing, or clicking."""
        self._ensure_app_foreground(app_name)

        if not pyautogui:
            return True, "Simulated (pyautogui not installed)"

        # Check if action is an atomic machine command like click(...), hotkey(...), etc.
        raw_cmd = ""
        if step_item and "action" in step_item and any(op in str(step_item["action"]).lower() for op in ["click(", "move_cursor(", "hotkey(", "type_text(", "press_key(", "open_app(", "double_click("]):
            raw_cmd = str(step_item["action"])
        elif any(op in str(action_type).lower() for op in ["click(", "move_cursor(", "hotkey(", "type_text(", "press_key(", "open_app(", "double_click("]):
            raw_cmd = str(action_type)

        # Non-AI Visualizer check: If step has a saved visual icon template, locate it on screen!
        visual_template = step_item.get("visual_template") if step_item else None
        act_check = str(raw_cmd or action_type).lower()
        if visual_template and any(op in act_check for op in ["click", "double_click", "right_click"]):
            match_coords = self.find_visual_template_on_screen(visual_template)
            if match_coords:
                mx, my = match_coords
                print(f"[NON-AI VISUALIZER] Found matched visual icon on screen at ({mx}, {my}). Moving mouse directly to icon!", flush=True)
                pyautogui.moveTo(mx, my, duration=0.15)
                time.sleep(0.05)
                if "double_click" in act_check:
                    pyautogui.doubleClick(mx, my)
                    return True, f"Double-clicked visual icon template at ({mx}, {my})"
                elif "right_click" in act_check:
                    pyautogui.rightClick(mx, my)
                    return True, f"Right-clicked visual icon template at ({mx}, {my})"
                else:
                    pyautogui.click(mx, my)
                    return True, f"Clicked visual icon template at ({mx}, {my})"
            else:
                print(f"[NON-AI VISUALIZER] Visual template '{visual_template}' not found on screen. Falling back to normalized coordinates.", flush=True)

        if raw_cmd:
            screen_w, screen_h = pyautogui.size()
            return self._execute_agent_action_cmd(raw_cmd, app_name, screen_w, screen_h)

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

    def run_vision_agent_task(
        self,
        task_text: str,
        status_callback=None,
        voice_callback=None,
        hud_disambig_callback=None
    ) -> Tuple[bool, str, List[Dict[str, Any]]]:
        """
        Unified Entry Point:
        1. Instant media controls (stop/pause/resume/next/previous) with zero latency.
        2. Fast check of prior knowledge database. If an obstacle is encountered,
           seamlessly recovers via the Dynamic Vision Agent.
        3. Real-time Vision-In-The-Loop Agent capturing screenshots on each step,
           with prominent 'RESPONSE BY AI' output in the CMD.
        4. Phase 4: Human-in-the-Loop Conversational Disambiguation for unexpected barriers.
        """
        self._voice_cb = voice_callback
        self._hud_cb = hud_disambig_callback
        low_t = task_text.lower().strip()

        # 1. Instant Media Commands
        if any(w in low_t for w in ["stop playing", "stop music", "pause music", "pause song", "stop spotify", "pause spotify", "pause playback"]):
            MEDIA_CONTROLLER.pause()
            msg = "Spotify playback stopped, sir."
            print(f"[MEDIA CONTROLLER] {msg}")
            return True, msg, []

        if any(w in low_t for w in ["resume music", "resume playing", "resume spotify", "unmute spotify"]):
            MEDIA_CONTROLLER.play()
            print("[MEDIA CONTROLLER] Spotify playback resumed, sir.")
            return True, "", []

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
        matched_key = None
        prior = None
        try:
            from goal_decomposer import check_prior_knowledge
            prior = check_prior_knowledge(task_text)
            if prior and prior.get("steps"):
                matched_key = prior.get("matched_key")
                app_name = prior.get("app", "")
                print(f"[GOAL ROUTER] Found prior verified procedure for '{task_text}' (key: {matched_key}). Executing with before/after visual verification...")
                ok, reply, logs = self.execute_saved_plan_with_verification(task_text, prior, status_callback=status_callback)
                if ok:
                    return True, reply, logs
                
                print(f"\n[GOAL ROUTER] Saved procedure did not verify as completed on screen.")
                print(f"[GOAL ROUTER] Reason: {reply}")
                print(f"[GOAL ROUTER] Seamlessly handing over to Dynamic Vision-In-The-Loop Agent to finish the goal and update memory...\n")
                return self.run_dynamic_vision_loop(
                    task_text=task_text,
                    app_name=app_name,
                    last_problem=f"Prior saved procedure failed visual verification: {reply}. Please inspect the current screen and take corrective actions.",
                    status_callback=status_callback,
                    memory_key_to_overwrite=matched_key,
                    prior_attempted_steps=logs or prior.get("steps", []),
                    voice_callback=voice_callback,
                    hud_disambig_callback=hud_disambig_callback
                )
        except Exception as e:
            print(f"[PRIOR LOOKUP NOTICE] {e}")

        # 3. Dynamic Vision-In-The-Loop Agent Execution
        return self.run_dynamic_vision_loop(
            task_text=task_text,
            status_callback=status_callback,
            voice_callback=voice_callback,
            hud_disambig_callback=hud_disambig_callback
        )

    def _extract_search_query(self, task_text: str) -> str:
        """Extracts search query string from task text like 'open youtube on brave and play laapata on youtube'."""
        low = task_text.lower().strip()
        # play ... on youtube/spotify/brave
        m = re.search(r"play\s+(?:the\s+(?:song|video|track)\s+)?(.+?)(?:\s+(?:on|in)\s+(?:youtube|spotify|brave|chrome)|$)", low)
        if m:
            q = m.group(1).strip()
            q = re.sub(r"\s+(?:on|in)\s+(?:youtube|spotify|brave|chrome).*$", "", q).strip()
            if q and q not in ["song", "video", "music", "track"]:
                return q
        # search ...
        m = re.search(r"search\s+(?:for\s+)?(.+?)(?:\s+(?:on|in)\s+(?:youtube|spotify|brave|chrome)|$)", low)
        if m:
            q = m.group(1).strip()
            q = re.sub(r"\s+(?:on|in)\s+(?:youtube|spotify|brave|chrome).*$", "", q).strip()
            if q:
                return q
        return ""

    def run_dynamic_vision_loop(
        self,
        task_text: str,
        app_name: str = "",
        last_problem: str = "",
        max_steps: int = 25,
        status_callback=None,
        memory_key_to_overwrite: Optional[str] = None,
        prior_attempted_steps: Optional[List[Dict[str, Any]]] = None,
        voice_callback=None,
        hud_disambig_callback=None
    ) -> Tuple[bool, str, List[Dict[str, Any]]]:
        """
        Dynamic Vision-In-The-Loop Agent:
        Captures a desktop screenshot on EACH step and prompts Nemotron-3.5-Super-VL
        to determine the next action dynamically.
        Displays real-time 'RESPONSE BY AI' in the console.
        """
        if voice_callback:
            self._voice_cb = voice_callback
        if hud_disambig_callback:
            self._hud_cb = hud_disambig_callback

        if not app_name:
            low_t = task_text.lower()
            if any(k in low_t for k in ["gmail", "email", "mail", "inbox"]):
                app_name = "Gmail"
            elif any(k in low_t for k in ["classroom", "assignment", "homework"]):
                app_name = "Google Classroom"
            elif "youtube" in low_t:
                app_name = "YouTube"
            elif "spotify" in low_t:
                app_name = "Spotify"
            elif "whatsapp" in low_t:
                app_name = "WhatsApp"
            elif "canva" in low_t:
                app_name = "Canva"
            elif "instagram" in low_t:
                app_name = "Instagram"
            elif "facebook" in low_t:
                app_name = "Facebook"
            elif "linkedin" in low_t:
                app_name = "LinkedIn"
            elif "antigrav" in low_t or "code" in low_t or "vscode" in low_t:
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
        user_clarifications: List[str] = []

        # Auto-inject connected account credentials from connected_plugins.json
        try:
            plg_file = CURRENT_DIR / "connected_plugins.json"
            if not plg_file.exists() and (PARENT_DIR / "connected_plugins.json").exists():
                plg_file = PARENT_DIR / "connected_plugins.json"
            if plg_file.exists():
                with open(plg_file, "r", encoding="utf-8") as pf:
                    pdata = json.load(pf)
                for pk, pv in pdata.items():
                    if isinstance(pv, dict) and pv.get("connected"):
                        p_acc = pv.get("account")
                        p_title = pk.replace("_", " ").title()
                        if p_acc:
                            user_clarifications.append(
                                f"Connected Account for {p_title}: '{p_acc}'. "
                                f"If prompted to select an account, choose profile, or enter email, use '{p_acc}'."
                            )
        except Exception as e:
            print(f"[PLUGIN CONTEXT NOTICE] {e}")

        if prior_attempted_steps:
            for s in prior_attempted_steps:
                if isinstance(s, dict):
                    act_cmd = s.get("action") or ""
                    if act_cmd and "complete" not in act_cmd.lower():
                        history.append({
                            "step": len(history) + 1,
                            "observation": s.get("observation") or "Executed as part of saved sequence",
                            "reasoning": s.get("details") or s.get("description") or act_cmd,
                            "action": act_cmd,
                            "result": s.get("result", "executed")
                        })

        start_step = len(history) + 1
        for step_idx in range(start_step, start_step + max_steps):
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
                        if memory_key_to_overwrite:
                            save_successful_task_to_memory(memory_key_to_overwrite, app_name, task_text, history)
                        save_successful_task_to_memory(task_slug, app_name, task_text, history)
                        return True, "", history

            # 2. Build Vision AI prompt with Disambiguation knowledge & persistent user clarifications
            if user_clarifications:
                clarif_lines = "\n".join([f"• {c}" for c in user_clarifications])
                active_clarif_block = f"\nACTIVE USER CLARIFICATIONS & DATA PROVIDED (YOU MUST ADHERE TO THIS):\n{clarif_lines}\n"
            else:
                active_clarif_block = ""

            if history:
                past_steps_summary = "COMPLETED STEPS SO FAR:\n" + "\n".join([f"- Step {h['step']}: {h['action']} (Result: {h.get('result', 'ok')})" for h in history])
            else:
                past_steps_summary = "No previous steps executed yet. This is Step 1."

            alert_str = f"\nATTENTION / OBSTACLE REPORT:\n{last_problem}\nPlease analyze the new screen capture to diagnose why and provide the corrective action." if last_problem else ""

            try:
                from disambiguation_manager import DISAMBIGUATION_MGR
                prefs = DISAMBIGUATION_MGR.get_all_preferences()
                pref_lines = "\n".join([f"- {k}: '{v}'" for k, v in prefs.items()]) if prefs else "None recorded yet."
                user_prefs_str = f"\nKNOWN USER PREFERENCES (Use these to auto-select without asking if visible on screen):\n{pref_lines}\n"
            except Exception:
                user_prefs_str = ""

            sys_prompt = (
                "You are HYDRA's Real-Time Desktop Machine Actuator Controller.\n"
                "IMPORTANT: You are communicating directly with an automated robot actuator, NOT a human.\n\n"
                f"User Goal: '{task_text}'\n"
                f"Target Application: {app_name}\n"
                f"Step: {step_idx}/{max_steps}\n"
                f"Screen Resolution: {screen_w}x{screen_h}\n\n"
                f"{active_clarif_block}"
                f"{past_steps_summary}\n"
                f"{alert_str}\n"
                f"{user_prefs_str}\n"
                "CRITICAL OUTPUT RULES:\n"
                "1. Provide EXACTLY ONE atomic action for this step. DO NOT chain steps together or give multi-step plans.\n"
                "2. Output format MUST be STRICTLY 3 lines:\n"
                "OBSERVATION: <1 sentence describing visible screen state>\n"
                "REASONING: <1 sentence explaining why this next single action is taken>\n"
                "ACTION: <ONE single command from the supported list below>\n"
                "3. The ACTION line must contain ONLY the command itself. DO NOT write explanations, prose, commentary, or XML tags on or after the ACTION line.\n"
                "4. EXACT LITERAL TEXT & PASSWORDS: When typing text, passwords, usernames, or codes via type_text, preserve the EXACT uppercase/lowercase capitalization, numbers, and special characters (such as **, @, #, $, !, _) as given in the user goal or active clarifications. DO NOT convert text to lowercase and DO NOT strip symbols or asterisks.\n\n"
                "SUPPORTED ONE-LINE COMPUTER COMMANDS:\n"
                "- move_cursor(x=0.5, y=0.5)                                         -> Moves mouse cursor to normalized (0.0 to 1.0) screen coordinates\n"
                "- click(x=0.5, y=0.5)                                               -> Moves and left-clicks (or click() at current position)\n"
                "- double_click(x=0.5, y=0.5)                                        -> Moves and double-clicks (or double_click() at current position). Use on song rows or video cards to play immediately!\n"
                "- right_click(x=0.5, y=0.5)                                         -> Moves and right-clicks to open context menu (or right_click())\n"
                "- hotkey('key1', 'key2')                                            -> Presses simultaneous keyboard shortcut, e.g. hotkey('ctrl', 't') for new tab, hotkey('ctrl', 'l') for address/search bar, hotkey('ctrl', 'shift', 'p')\n"
                "- press_key('key')                                                  -> Presses single key, e.g. press_key('enter'), press_key('space'), press_key('esc'), press_key('tab')\n"
                "- type_text('text', press_enter=True)                               -> Types text into the currently focused input field and optionally presses Enter\n"
                "- type_text('text', x=0.5, y=0.5, overwrite=True, press_enter=True) -> Clicks into input field, clears old text, types text, and presses Enter\n"
                "- open_app('app_name')                                              -> Launches or switches to application, e.g. open_app('brave'), open_app('whatsapp'), open_app('antigravity')\n"
                "- open_url('https://...')                                           -> Opens website URL directly in the default browser, e.g. open_url('https://mail.google.com')\n"
                "- wait(seconds=1.0)                                                 -> Waits for UI rendering\n"
                "- ask_user(question='...', options=['opt1', 'opt2'])                -> PAUSES execution to query user via voice & HUD when you need clarification, profile choice, login credentials, or permissions\n"
                "- complete()                                                        -> ONLY call this when the entire user goal is 100% completed on screen\n"
            )

            # Per-Step live screen query with explicit barrier checks directly attached to the screenshot
            user_query = (
                f"TARGET GOAL: '{task_text}'\n"
                f"TARGET APPLICATION: {app_name}\n"
                f"CURRENT STEP: {step_idx}/{max_steps}\n\n"
                f"{past_steps_summary}\n"
                f"{active_clarif_block}"
                f"{alert_str}\n"
                f"{user_prefs_str}\n"
                "INSTRUCTIONS FOR ANALYZING THIS LIVE SCREEN:\n"
                f"Based on the attached live screenshot and the completed steps above, what is the NEXT SINGLE ATOMIC STEP to reach the goal '{task_text}'?\n\n"
                "CRITICAL OPERATING RULES FOR THIS EXACT SCREEN:\n"
                "1. SEARCH & PLAY WORKFLOW (YouTube, Spotify, Browser, Desktop):\n"
                "   -> When your goal is to search or play media (e.g. 'play laapata on youtube', 'search for tutorials'):\n"
                "      * DO NOT CLICK THE SEARCH BAR WITHOUT TYPING! Combine clicking and typing in ONE atomic step:\n"
                "        ACTION: type_text('<query>', x=<search_bar_x>, y=<search_bar_y>, press_enter=True)\n"
                "      * If the search bar was ALREADY clicked or is focused: DO NOT click it again! Immediately type:\n"
                "        ACTION: type_text('<query>', press_enter=True)\n"
                "      * Once search results are displayed on screen: click or double-click the target video or song row to start playback:\n"
                "        ACTION: click(x=<video_result_x>, y=<video_result_y>)\n"
                "      * Once the video/media starts playing (video player open, watch URL loaded, or media active):\n"
                "        ACTION: complete()\n"
                "2. DISAMBIGUATION & ASKING USER (Browser Profile, Login Credentials, 2FA, Decisions):\n"
                "   -> BROWSER PROFILE PICKER ('Who is using Brave?', 'Who's using Chrome?', profile selection):\n"
                "      DO NOT CLICK 'Guest', DO NOT click 'Add person', and DO NOT guess.\n"
                "      Unless an ACTIVE USER CLARIFICATION above explicitly chose a profile, you MUST ASK THE USER:\n"
                "      ACTION: ask_user(question=\"Which profile should I choose in {app_name}?\", options=[<visible profiles>])\n"
                "   -> LOGIN CREDENTIALS & PASSWORDS:\n"
                "      If this is a login screen and credentials are NOT in ACTIVE USER CLARIFICATIONS above: You MUST ASK: ACTION: ask_user(question=\"Please provide your login credentials for this website.\", options=[])\n"
                "      If credentials ARE provided above: click username field and type_text('<username>'), then click password field and type_text('<password>'), then click Login or press_key('enter').\n"
                "   -> UNEXPECTED BARRIERS / POPUPS:\n"
                "      If an unexpected popup or choice appears requiring user decision: ACTION: ask_user(question=\"<question>\", options=[<options>])\n"
                "3. THEME CHANGES (WhatsApp, Antigravity, VS Code, Apps):\n"
                "   -> To change theme to Dark or Light:\n"
                "      * Step 1: Open settings/command palette (gear icon in WhatsApp, Ctrl+Shift+P in Antigravity).\n"
                "      * Step 2: Navigate to theme settings (Chats > Theme in WhatsApp; 'Preferences: Color Theme' in Antigravity).\n"
                "      * Step 3: Click the target theme option ('Dark' radio button or 'Light Modern').\n"
                "      * Step 4: If an 'OK', 'Apply', or 'Save' button is present (like WhatsApp Theme dialog), CLICK IT to confirm!\n"
                "      * Step 5: ONLY after the target theme is visibly applied on the interface: ACTION: complete()\n"
                "      * DO NOT output complete() while the dialog is still open or before clicking the theme option!\n"
                "4. BROWSER NAVIGATION & NEW TABS:\n"
                "   -> When navigating to a new website in an open browser: use hotkey('ctrl', 't') for a new tab or hotkey('ctrl', 'l') to focus the address bar, then type_text('url', press_enter=True), or directly output open_url('https://...').\n"
                "5. EXACT TEXT & PASSWORDS:\n"
                "   -> When typing passwords or text via type_text, PRESERVE THE EXACT UPPERCASE/LOWERCASE CAPITALIZATION, NUMBERS, AND SPECIAL SYMBOLS (e.g. 'A1h2m3a4d', '@', '#', '**'). DO NOT convert to lowercase and DO NOT strip symbols!\n"
                "6. EMAIL, NOTIFICATIONS & ASSIGNMENT INSPECTION (Gmail, Google Classroom, Web Services):\n"
                "   -> When your goal is to check unread emails, assignments, notifications, or due work:\n"
                "      * If the page is loading: wait: ACTION: wait(seconds=1.5)\n"
                "      * If a Google account or profile chooser is shown: click the account matching the connected email in ACTIVE USER CLARIFICATIONS.\n"
                "      * Once the inbox, classroom stream, or assignment list is visible:\n"
                "        - In OBSERVATION: describe clearly the unread emails, assignment titles, due dates, or status visible on screen (e.g. 'Found 2 unread emails from ...' or 'Found 1 assignment due tomorrow: ...' or 'No unread emails present in inbox').\n"
                "        - In REASONING: state that the required information has been retrieved from the screen.\n"
                "        - In ACTION: complete()\n"
                "7. GOAL COMPLETION:\n"
                "   -> Output ACTION: complete() ONLY when the entire user goal is visibly completed on screen.\n\n"
                "Output format MUST be EXACTLY 3 lines:\n"
                "OBSERVATION: <what you observe on screen>\n"
                "REASONING: <why this next step brings us closer to the goal>\n"
                "ACTION: <single one-line computer command from the supported list above, or ask_user(...)>"
            )
            
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
            act_lower = act.lower().strip()
            comb_text = f"{obs} {rea}".lower()
            is_complete = (
                "complete()" in act_lower
                or act_lower == "complete"
                or "goal achieved" in act_lower
                or "task completed" in act_lower
            )

            # Check for YouTube video playback confirmation
            if any(w in task_text.lower() for w in ["youtube", "play", "video"]) and ("brave" in app_name.lower() or "chrome" in app_name.lower()):
                if any(p in comb_text for p in ["video is playing", "video is now playing", "playing in the youtube video player", "watch page loaded", "video playing"]):
                    is_complete = True

            if is_complete:
                print(f"\n[GOAL COMPLETE] Vision Agent confirmed goal achieved at step {step_idx}!")
                task_slug = f"{app_name.lower()}:{re.sub(r'[^a-zA-Z0-9]+', '_', task_text.lower()).strip('_')}"
                if memory_key_to_overwrite:
                    save_successful_task_to_memory(memory_key_to_overwrite, app_name, task_text, history)
                save_successful_task_to_memory(task_slug, app_name, task_text, history)
                speech_reply = _generate_humanized_completion_speech(task_text, app_name, obs)
                return True, speech_reply, history

            # 6. Execute Action & Crop Visual Template Patch
            img_w, img_h = img.size if hasattr(img, 'size') else (1707, 960)
            template_rel_path = None
            m_c = re.search(r"(?:click|double_click|right_click)\s*\(\s*(?:x\s*=\s*)?([0-9\.]+)\s*,\s*(?:y\s*=\s*)?([0-9\.]+)\s*\)", act, re.I)
            if m_c and img:
                try:
                    raw_x = float(m_c.group(1))
                    raw_y = float(m_c.group(2))
                    c_px = int(raw_x * img_w) if 0.0 <= raw_x <= 1.0 else int(raw_x)
                    c_py = int(raw_y * img_h) if 0.0 <= raw_y <= 1.0 else int(raw_y)
                    # 64x64 bounding box centered on click target
                    box_x1 = max(0, c_px - 32)
                    box_y1 = max(0, c_py - 32)
                    box_x2 = min(img_w, c_px + 32)
                    box_y2 = min(img_h, c_py + 32)
                    patch = img.crop((box_x1, box_y1, box_x2, box_y2))

                    task_slug_folder = re.sub(r'[^a-zA-Z0-9_\-]+', '_', f"{app_name.lower()}_{task_text.lower()}").strip('_')
                    task_tpl_dir = TEMPLATES_DIR / task_slug_folder
                    task_tpl_dir.mkdir(parents=True, exist_ok=True)
                    template_filename = f"step_{step_idx}_{box_x1}_{box_y1}.png"
                    patch.save(task_tpl_dir / template_filename)

                    # Also save in inner directory if available
                    if PARENT_DIR.exists():
                        try:
                            inner_tpl_dir = PARENT_DIR / "visual_templates" / task_slug_folder
                            inner_tpl_dir.mkdir(parents=True, exist_ok=True)
                            patch.save(inner_tpl_dir / template_filename)
                        except Exception:
                            pass

                    template_rel_path = f"visual_templates/{task_slug_folder}/{template_filename}"
                    print(f"[NON-AI VISUALIZER] Saved visual icon patch for step {step_idx} -> {template_rel_path}", flush=True)
                except Exception as tpl_err:
                    print(f"[NON-AI VISUALIZER SAVE NOTICE] {tpl_err}", flush=True)

            exec_ok, exec_msg = self._execute_agent_action_cmd(
                act, app_name, screen_w, screen_h, img_w, img_h,
                task_text=task_text, history=history
            )
            
            # 7. Post-action state check
            time.sleep(0.8) # Adaptive wait for UI rendering
            
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
                        if memory_key_to_overwrite:
                            save_successful_task_to_memory(memory_key_to_overwrite, app_name, task_text, history)
                        save_successful_task_to_memory(task_slug, app_name, task_text, history)
                        return True, "", history

            history_entry = {
                "step": step_idx,
                "observation": obs,
                "reasoning": rea,
                "action": act,
                "result": exec_msg
            }
            if template_rel_path:
                history_entry["visual_template"] = template_rel_path
            history.append(history_entry)

            # Detect consecutive repetitive action loops, proximity click loops, or alternating 2-period loops (A -> B -> A -> B)
            act_sig = re.sub(r'[\s\'\"]+', '', act.lower())
            prev_sig = re.sub(r'[\s\'\"]+', '', history[-2].get("action", "").lower()) if len(history) >= 2 else ""
            two_back_sig = re.sub(r'[\s\'\"]+', '', history[-3].get("action", "").lower()) if len(history) >= 3 else ""
            is_alternating_loop = (len(history) >= 3 and act_sig == two_back_sig)

            # Detect proximity click loop (clicking within 0.12 normalized distance across 2+ steps)
            is_proximity_click_loop = False
            m_curr_click = re.search(r"click\s*\(\s*(?:x\s*=\s*)?([0-9\.]+)\s*,\s*(?:y\s*=\s*)?([0-9\.]+)\s*\)", act, re.I)
            if m_curr_click and len(history) >= 2:
                cx, cy = float(m_curr_click.group(1)), float(m_curr_click.group(2))
                cx_n = cx if 0.0 <= cx <= 1.0 else (cx / img_w if img_w > 0 else cx / screen_w)
                cy_n = cy if 0.0 <= cy <= 1.0 else (cy / img_h if img_h > 0 else cy / screen_h)
                for past_h in history[-2:]:
                    m_past_click = re.search(r"click\s*\(\s*(?:x\s*=\s*)?([0-9\.]+)\s*,\s*(?:y\s*=\s*)?([0-9\.]+)\s*\)", past_h.get("action", ""), re.I)
                    if m_past_click:
                        px, py = float(m_past_click.group(1)), float(m_past_click.group(2))
                        px_n = px if 0.0 <= px <= 1.0 else (px / img_w if img_w > 0 else px / screen_w)
                        py_n = py if 0.0 <= py <= 1.0 else (py / img_h if img_h > 0 else py / screen_h)
                        if ((cx_n - px_n)**2 + (cy_n - py_n)**2)**0.5 < 0.12:
                            is_proximity_click_loop = True
                            break

            if not exec_ok:
                last_problem = f"Action '{act}' failed: {exec_msg}"
            elif any(w in act.lower() for w in ["ask_user", "ask user", "ask the user", "tell user", "tell the user"]):
                user_clarif = getattr(self, "_last_user_clarification", None) or exec_msg
                if user_clarif and user_clarif not in [c.replace("User instructed: ", "").strip("'") for c in user_clarifications]:
                    user_clarifications.append(f"User instructed: '{user_clarif}'")
                last_problem = (
                    f"USER CLARIFICATION RECEIVED: The user instructed: '{user_clarif}'. "
                    f"Please inspect the current screen capture now and take the single atomic action "
                    f"using the user's decision (for example: if the user specified a profile, click that profile; "
                    f"if the user provided login credentials, usernames, or passwords, type them into the active input field with type_text; "
                    f"if the user gave instructions, follow them)."
                )
            elif is_proximity_click_loop or is_alternating_loop or (prev_sig and act_sig == prev_sig):
                search_q = self._extract_search_query(task_text)
                if search_q:
                    last_problem = (
                        f"OBSTACLE DETECTED: You are repeatedly clicking near the search bar without typing. "
                        f"DO NOT CLICK AGAIN! You MUST now type the search text: ACTION: type_text('{search_q}', press_enter=True). "
                        f"If the search results are already visible on screen, click the desired video or item."
                    )
                else:
                    last_problem = (
                        f"WARNING: Repeated action detected ('{act}'). DO NOT repeat this! "
                        f"If this is an input field, type the required text with type_text(..., press_enter=True). "
                        f"If you need to confirm, click OK/Enter. If the goal is already achieved, output ACTION: complete()."
                    )
            else:
                last_problem = ""

        # Finished max steps
        fail_msg = f"I reached the step limit of {max_steps} steps before finishing '{task_text}', sir."
        print(f"\n[EXECUTION TIMEOUT] {fail_msg}", flush=True)
        return False, fail_msg, history

    def _parse_vision_agent_reply(self, content: str, reasoning: str = "") -> Tuple[str, str, str]:
        """Extracts OBSERVATION, REASONING, and a single sanitized computer ACTION line from vision model output."""
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

        # Extract ACTION:
        m_act = re.search(r"ACTION:\s*(.*)", full_text, re.DOTALL | re.I)
        if m_act:
            raw_action_block = m_act.group(1).strip()
            lines = [l.strip() for l in raw_action_block.splitlines() if l.strip()]
            first_line = lines[0] if lines else ""
            # Strip XML tags like </parameter>, <action>
            first_line = re.sub(r'</?[a-zA-Z0-9_\-]+>', '', first_line).strip()

            # Look for exact command call on the line
            cmd_match = re.search(
                r"((?:move_cursor|mouse_move|double_click|right_click|left_click|click|hotkey|press_key|press|type_text|type|open_app|open_url|navigate_to|open|wait|complete|ask_user)"
                r"\s*\([^)]*\))",
                first_line,
                re.I
            )
            if not cmd_match and "ask_user" in first_line.lower():
                cmd_match = re.search(r"(ask_user\s*\(.*\))", first_line, re.I)

            if cmd_match:
                act = cmd_match.group(1).strip()
            elif any(k in first_line.lower() for k in ["ask_user", "ask user", "ask the user", "tell user", "tell the user"]):
                clean_q = re.sub(r'^(?:action:)?\s*(?:tell\s+me\s+to\s+)?ask\s+(?:the\s+)?user\s*(?:about|for|to\s+provide|what|which|to)?\s*:?\s*', '', first_line, flags=re.I).strip(" '\"")
                if not clean_q:
                    clean_q = "Please clarify how you would like me to proceed, sir."
                act = f'ask_user(question="{clean_q}")'
            elif "complete()" in first_line.lower() or first_line.lower().strip() == "complete":
                act = "complete()"
            else:
                # Strip trailing sentences if model rambles
                clean_first = re.split(r'\.\s+[A-Z]', first_line)[0].strip(" .")
                act = clean_first
        else:
            m_tag = re.search(r"<action>(.*?)</action>", full_text, re.DOTALL | re.I)
            if m_tag:
                act = m_tag.group(1).strip()
            elif any(k in full_text.lower() for k in ["ask_user", "ask the user", "ask user"]):
                m_ask = re.search(r"(ask_user\s*\(.*?\))", full_text, re.I)
                if m_ask:
                    act = m_ask.group(1).strip()
                else:
                    m_nl = re.search(r"(?:tell\s+me\s+to\s+)?ask\s+(?:the\s+)?user[^\n\.]+", full_text, re.I)
                    if m_nl:
                        clean_q = re.sub(r'^(?:tell\s+me\s+to\s+)?ask\s+(?:the\s+)?user\s*(?:about|for|to\s+provide|what|which|to)?\s*:?\s*', '', m_nl.group(0), flags=re.I).strip(" '\"")
                        act = f'ask_user(question="{clean_q}")'
            elif "complete" in full_text.lower():
                act = "complete()"
            elif "space" in full_text.lower():
                act = "press_key('space')"
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
        img_h: int = 960,
        task_text: str = "",
        history: Optional[List[Dict[str, Any]]] = None
    ) -> Tuple[bool, str]:
        """
        Executes a single atomic computer command string parsed from Vision AI.
        Supports:
        - move_cursor(x=..., y=...)
        - click(x=..., y=...) / click()
        - double_click(x=..., y=...) / double_click()
        - right_click(x=..., y=...) / right_click()
        - hotkey('k1', 'k2', ...)
        - press_key('key')
        - type_text('text', overwrite=True, press_enter=True)
        - open_app('app_name')
        - wait(seconds=...)
        - complete()
        """
        if not pyautogui:
            return True, "Simulated (pyautogui not installed)"

        self._ensure_app_foreground(app_name)
        act_clean = act_str.strip()
        low_act = act_clean.lower()

        def _coords(x_val: float, y_val: float) -> Tuple[int, int]:
            if 0.0 <= x_val <= 1.0 and 0.0 <= y_val <= 1.0:
                px = int(x_val * screen_w)
                py = int(y_val * screen_h)
            elif img_w > 0 and x_val <= img_w * 1.05 and y_val <= img_h * 1.05:
                px = int((x_val / img_w) * screen_w)
                py = int((y_val / img_h) * screen_h)
            else:
                px = int(x_val)
                py = int(y_val)
            return max(0, min(screen_w - 1, px)), max(0, min(screen_h - 1, py))

        def _release_modifiers():
            for mod in ['ctrl', 'shift', 'alt', 'win']:
                try:
                    pyautogui.keyUp(mod)
                except Exception:
                    pass

        try:
            # 0. Complete Check
            if any(w in low_act for w in ["complete()", "complete", "goal achieved", "task completed", "finished"]):
                return True, "Task completion confirmed"

            # 1. move_cursor(x=..., y=...) or mouse_move(...)
            m_move = re.search(r"(?:move_cursor|mouse_move|move)\s*\(\s*(?:x\s*=\s*)?([0-9\.]+)\s*,\s*(?:y\s*=\s*)?([0-9\.]+)\s*\)", act_clean, re.I)
            if m_move:
                px, py = _coords(float(m_move.group(1)), float(m_move.group(2)))
                pyautogui.moveTo(px, py, duration=0.2)
                return True, f"Moved cursor to ({px}, {py})"

            # 2. double_click(x=..., y=...) or double_click()
            m_dclick = re.search(r"double_click\s*(?:\(\s*(?:(?:x\s*=\s*)?([0-9\.]+)\s*,\s*(?:y\s*=\s*)?([0-9\.]+))?\s*\))?", act_clean, re.I)
            if m_dclick and "double_click" in low_act:
                if m_dclick.group(1) and m_dclick.group(2):
                    px, py = _coords(float(m_dclick.group(1)), float(m_dclick.group(2)))
                    pyautogui.moveTo(px, py, duration=0.15)
                    pyautogui.doubleClick(px, py)
                    return True, f"Double-clicked at ({px}, {py})"
                else:
                    pyautogui.doubleClick()
                    return True, "Double-clicked at current position"

            # 3. right_click(x=..., y=...) or right_click()
            m_rclick = re.search(r"right_click\s*(?:\(\s*(?:(?:x\s*=\s*)?([0-9\.]+)\s*,\s*(?:y\s*=\s*)?([0-9\.]+))?\s*\))?", act_clean, re.I)
            if m_rclick and "right_click" in low_act:
                if m_rclick.group(1) and m_rclick.group(2):
                    px, py = _coords(float(m_rclick.group(1)), float(m_rclick.group(2)))
                    pyautogui.moveTo(px, py, duration=0.15)
                    pyautogui.rightClick(px, py)
                    return True, f"Right-clicked at ({px}, {py})"
                else:
                    pyautogui.rightClick()
                    return True, "Right-clicked at current position"

            # 4. click(x=..., y=...) or left_click(...) or click()
            m_click = re.search(r"(?:left_click|click)\s*(?:\(\s*(?:(?:x\s*=\s*)?([0-9\.]+)\s*,\s*(?:y\s*=\s*)?([0-9\.]+))?\s*\))?", act_clean, re.I)
            if m_click and ("click" in low_act or "left_click" in low_act):
                if m_click.group(1) and m_click.group(2):
                    raw_x = float(m_click.group(1))
                    raw_y = float(m_click.group(2))
                    px, py = _coords(raw_x, raw_y)

                    # Smart Search Bar Auto-Type Resilience:
                    # Detect if clicking in search bar area (normalized y < 0.22, 0.15 <= x <= 0.85)
                    # when a search query exists in task_text and previous action was also a search bar click
                    norm_x = raw_x if 0.0 <= raw_x <= 1.0 else (raw_x / img_w if img_w > 0 else raw_x / screen_w)
                    norm_y = raw_y if 0.0 <= raw_y <= 1.0 else (raw_y / img_h if img_h > 0 else raw_y / screen_h)
                    
                    search_query = self._extract_search_query(task_text) if task_text else ""
                    prev_was_search_click = False
                    if history and len(history) >= 1:
                        prev_act = history[-1].get("action", "").lower()
                        m_prev_c = re.search(r"click\s*\(\s*(?:x\s*=\s*)?([0-9\.]+)\s*,\s*(?:y\s*=\s*)?([0-9\.]+)\s*\)", prev_act)
                        if m_prev_c:
                            prev_x = float(m_prev_c.group(1))
                            prev_y = float(m_prev_c.group(2))
                            p_norm_x = prev_x if 0.0 <= prev_x <= 1.0 else (prev_x / img_w if img_w > 0 else prev_x / screen_w)
                            p_norm_y = prev_y if 0.0 <= prev_y <= 1.0 else (prev_y / img_h if img_h > 0 else prev_y / screen_h)
                            if p_norm_y < 0.22 and 0.15 <= p_norm_x <= 0.85:
                                prev_was_search_click = True

                    if norm_y < 0.22 and 0.15 <= norm_x <= 0.85 and prev_was_search_click and search_query:
                        print(f"\n[ACTUATOR RESILIENCE] Detected repeated click in search bar area during search goal.", flush=True)
                        print(f"[ACTUATOR RESILIENCE] Auto-typing search query '{search_query}' and pressing Enter to proceed!\n", flush=True)
                        pyautogui.moveTo(px, py, duration=0.15)
                        pyautogui.click(px, py)
                        time.sleep(0.12)
                        pyautogui.hotkey("ctrl", "a")
                        time.sleep(0.06)
                        _release_modifiers()
                        if pyperclip:
                            pyperclip.copy(search_query)
                            time.sleep(0.04)
                            pyautogui.hotkey("ctrl", "v")
                        else:
                            pyautogui.write(search_query, interval=0.01)
                        time.sleep(0.12)
                        pyautogui.press("enter")
                        return True, f"Focused search bar, auto-typed '{search_query}', and pressed Enter"

                    pyautogui.moveTo(px, py, duration=0.15)
                    pyautogui.click(px, py)
                    return True, f"Clicked at ({px}, {py})"
                else:
                    pyautogui.click()
                    return True, "Clicked at current position"

            # 5. hotkey('k1', 'k2', ...)
            m_hotkey = re.search(r"hotkey\s*\((.*?)\)", act_clean, re.I)
            if m_hotkey:
                raw_keys = m_hotkey.group(1)
                keys = [k.strip(" '\"").lower() for k in raw_keys.split(",") if k.strip(" '\"")]
                key_map = {
                    "control": "ctrl", "cmd": "win", "windows": "win", "super": "win",
                    "return": "enter", "escape": "esc", "spacebar": "space", "del": "delete"
                }
                norm_keys = [key_map.get(k, k) for k in keys]
                _release_modifiers()
                if "spotify" in app_name.lower() and norm_keys == ["space"]:
                    MEDIA_CONTROLLER.toggle_play_pause()
                for k in norm_keys:
                    pyautogui.keyDown(k)
                    time.sleep(0.04)
                for k in reversed(norm_keys):
                    pyautogui.keyUp(k)
                    time.sleep(0.02)
                return True, f"Pressed hotkey {'+'.join(norm_keys)}"

            # 6. press_key('key') or press('key')
            m_press = re.search(r"(?:press_key|press)\s*\(\s*['\"](.*?)['\"]\s*\)", act_clean, re.I)
            if m_press:
                k = m_press.group(1).strip().lower()
                key_map = {
                    "control": "ctrl", "cmd": "win", "windows": "win", "super": "win",
                    "return": "enter", "escape": "esc", "spacebar": "space", "del": "delete"
                }
                k = key_map.get(k, k)
                _release_modifiers()
                if "spotify" in app_name.lower() and k == "space":
                    MEDIA_CONTROLLER.toggle_play_pause()
                pyautogui.press(k)
                return True, f"Pressed key '{k}'"

            # 7. type_text('text', ...) or type(...) optionally with x=..., y=..., overwrite=True
            m_type = re.search(r"type(?:_text)?\s*\(\s*(?:text\s*=\s*)?(?:'([^']*)'|\"([^\"]*)\")", act_clean, re.I)
            if not m_type:
                m_type = re.search(r"type(?:_text)?\s*\(\s*(?:text\s*=\s*)?['\"](.*?)['\"]", act_clean, re.I)
            if not m_type:
                m_type = re.search(r"text\s*=\s*['\"](.*?)['\"]", act_clean, re.I)
            if m_type:
                text_to_type = m_type.group(1) if m_type.group(1) is not None else (m_type.group(2) if len(m_type.groups()) > 1 and m_type.group(2) is not None else "")

                # If coordinates x and y are given in type_text, click the target input field first!
                m_x = re.search(r"x\s*=\s*([0-9\.]+)", act_clean, re.I)
                m_y = re.search(r"y\s*=\s*([0-9\.]+)", act_clean, re.I)
                if m_x and m_y:
                    px, py = _coords(float(m_x.group(1)), float(m_y.group(1)))
                    pyautogui.moveTo(px, py, duration=0.15)
                    pyautogui.click(px, py)
                    time.sleep(0.12)

                if "overwrite=true" in low_act or "select_all=true" in low_act or "clear=true" in low_act:
                    pyautogui.hotkey("ctrl", "a")
                    time.sleep(0.08)
                _release_modifiers()
                if pyperclip:
                    pyperclip.copy(text_to_type)
                    time.sleep(0.04)
                    pyautogui.hotkey("ctrl", "v")
                    time.sleep(0.04)
                else:
                    pyautogui.write(text_to_type, interval=0.01)
                if "press_enter=true" in low_act or "enter=true" in low_act:
                    time.sleep(0.1)
                    pyautogui.press("enter")
                return True, f"Typed text '{text_to_type}'"

            # 8. open_app('app_name')
            m_open = re.search(r"open(?:_app)?\s*\(\s*['\"](.*?)['\"]\s*\)", act_clean, re.I)
            if m_open:
                target_app = m_open.group(1).strip()
                ok, _ = WINDOW_MGR.switch_to(target_app)
                if not ok:
                    target_url = get_target_url_for_app(target_app)
                    if target_url:
                        import webbrowser
                        webbrowser.open(target_url)
                        time.sleep(1.5)
                        return True, f"Opened web application '{target_app}' ({target_url})"
                    pyautogui.press("win")
                    time.sleep(0.3)
                    pyautogui.write(target_app, interval=0.02)
                    time.sleep(0.4)
                    pyautogui.press("enter")
                    time.sleep(1.0)
                    return True, f"Launched application '{target_app}' via Windows Search"
                return True, f"Switched to application '{target_app}'"

            # 9. open_url('https://...') or navigate_to('https://...')
            m_url = re.search(r"(?:open_url|navigate_to|goto)\s*\(\s*(?:url\s*=\s*)?['\"](.*?)['\"]\s*\)", act_clean, re.I)
            if m_url:
                target_url = m_url.group(1).strip()
                import webbrowser
                webbrowser.open(target_url)
                time.sleep(1.5)
                return True, f"Navigated to '{target_url}' in default browser"

            # 9. wait(seconds=...)
            m_wait = re.search(r"wait\s*\(\s*(?:seconds\s*=\s*)?([0-9\.]+)\s*\)", act_clean, re.I)
            if m_wait:
                sec = float(m_wait.group(1))
                time.sleep(sec)
                return True, f"Waited {sec} seconds"

            # 10. ask_user(question='...', options=['...']) or natural language asking
            if any(w in low_act for w in ["ask_user", "ask user", "ask the user", "tell user", "tell the user"]):
                from disambiguation_manager import DISAMBIGUATION_MGR

                # Parse question parameter
                m_q = re.search(r"question\s*=\s*(?:'([^']*)'|\"([^\"]*)\")", act_clean, re.I)
                if not m_q:
                    m_q = re.search(r"['\"](.*?)['\"]", act_clean)
                if m_q:
                    question = (m_q.group(1) if m_q and m_q.group(1) is not None else (m_q.group(2) if m_q else ""))
                else:
                    question = re.sub(r'^(?:action:)?\s*(?:tell\s+me\s+to\s+)?ask\s+(?:the\s+)?user\s*(?:about|for|to\s+provide|what|which|to)?\s*:?\s*', '', act_clean, flags=re.I).strip(" '\"")

                if not question:
                    question = "Please clarify how you would like me to proceed, sir."

                # Parse options list
                options = []
                m_opts = re.search(r"options\s*=\s*\[(.*?)\]", act_clean, re.I | re.DOTALL)
                if m_opts:
                    raw_opts = m_opts.group(1)
                    found_opts = re.findall(r"['\"](.*?)['\"]", raw_opts)
                    if found_opts:
                        options = [o.strip() for o in found_opts if o.strip()]
                    else:
                        options = [o.strip() for o in raw_opts.split(",") if o.strip()]

                # Infer barrier type from question or target app
                barrier_type = ""
                low_q = question.lower()
                low_app = app_name.lower()
                if "profile" in low_q or "who's using" in low_q or "who is using" in low_q:
                    if "brave" in low_app or "brave" in low_q:
                        barrier_type = "brave_profile"
                    elif "chrome" in low_app or "chrome" in low_q:
                        barrier_type = "chrome_profile"
                    elif "edge" in low_app or "edge" in low_q:
                        barrier_type = "edge_profile"
                    else:
                        barrier_type = "browser_profile"
                elif any(w in low_q for w in ["cred", "password", "login", "username", "email"]):
                    barrier_type = "login_credentials"
                elif "account" in low_q:
                    barrier_type = "account"
                elif "permission" in low_q or "allow" in low_q or "access" in low_q:
                    barrier_type = "permission"

                print("\n" + "=" * 65, flush=True)
                print("  [DISAMBIGUATION TRIGGERED BY AGENT]", flush=True)
                print(f"  Question: {question}", flush=True)
                if options:
                    print(f"  Options : {options}", flush=True)
                print("=" * 65 + "\n", flush=True)

                ok, user_ans = DISAMBIGUATION_MGR.ask_user(
                    question=question,
                    options=options,
                    barrier_type=barrier_type,
                    timeout=45.0,
                    voice_callback=self._voice_cb,
                    hud_callback=self._hud_cb
                )

                self._last_user_clarification = user_ans
                return True, f"User stated: '{user_ans}'"

            # 11. Natural Language Fallbacks
            if any(k in low_act for k in ["ctrl+l", "ctrl + l", "ctrl+k", "ctrl + k", "focus search", "search bar"]):
                if "spotify" in app_name.lower():
                    pyautogui.hotkey("ctrl", "l")
                    time.sleep(0.1)
                    search_px, search_py = _coords(0.454, 0.036)
                    pyautogui.click(search_px, search_py)
                    return True, "Focused Spotify search bar"
                pyautogui.hotkey("ctrl", "l")
                return True, "Pressed Ctrl+L to focus address/search bar"

            if "whatsapp" in low_act and ("launch" in low_act or "open" in low_act or "taskbar" in low_act):
                ok, _ = WINDOW_MGR.switch_to("whatsapp")
                if not ok:
                    pyautogui.press("win")
                    time.sleep(0.3)
                    pyautogui.write("whatsapp", interval=0.02)
                    time.sleep(0.4)
                    pyautogui.press("enter")
                return True, "Switched/opened WhatsApp application"

            if "press enter" in low_act or "hit enter" in low_act:
                pyautogui.press("enter")
                return True, "Pressed Enter key"

            if "press space" in low_act or "hit space" in low_act:
                if "spotify" in app_name.lower():
                    MEDIA_CONTROLLER.toggle_play_pause()
                pyautogui.press("space")
                return True, "Pressed Space key"

            # If click was mentioned with no coordinates, click current cursor position
            if "click" in low_act:
                pyautogui.click()
                return True, f"Performed click for '{act_clean}'"

            return True, f"Action '{act_clean}' processed"

        except Exception as e:
            return False, f"Actuation error: {e}"


# Singleton instance
VERIFIED_EXECUTOR = VerifiedStepExecutor()
