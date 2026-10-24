"""
HYDRA Phase 2: Autonomous Goal Decomposer & Keyboard-First Planning Engine
Powered by Nebius AI (NVIDIA Nemotron-3_5-Lightning).

Workflow:
1. Checks prior knowledge database (learned_task_memory.json & learned_skills.json)
   to determine if this or a similar task was completed in the past.
   - If yes: retrieves exact verified procedure from database.
2. If not found in database:
   - Issues a structured prompt to NVIDIA Nemotron-3_5-Lightning.
   - Corrects application name typos (e.g., 'antigravty' -> 'antigravity', 'spotfy' -> 'spotify').
   - Prioritizes keyboard shortcuts (Ctrl+L for Spotify search, Ctrl+Shift+P / Ctrl+, for Antigravity/VS Code).
   - Generates granular UI action steps: open app, wait, shortcuts, typing, screen analysis,
     cursor movement, clicking, and intermediate/final visual verification checkpoints.
"""

import os
import sys
import re
import json
import time
import difflib
from typing import Dict, Any, List, Optional, Tuple
from pathlib import Path

# Load environment
try:
    from dotenv import load_dotenv
    # Look for .env in current, parent, or grandparent directories
    for d in [Path(__file__).resolve().parent, Path(__file__).resolve().parent.parent, Path(__file__).resolve().parent.parent.parent]:
        env_f = d / ".env"
        if env_f.exists():
            load_dotenv(dotenv_path=env_f)
            break
except ImportError:
    pass

# Nebius & Model Configuration
NEBIUS_API_KEY = os.environ.get("NEBIUS_API_KEY", "").strip()
NEBIUS_BASE_URL = os.environ.get("NEBIUS_BASE_URL", "https://api.tokenfactory.nebius.com/v1/").strip()
NEBIUS_FAST_MODEL = os.environ.get("NEBIUS_FAST_MODEL", "nvidia/Nemotron-3_5-Lightning").strip()
if NEBIUS_FAST_MODEL.startswith("nebius/"):
    NEBIUS_FAST_MODEL = NEBIUS_FAST_MODEL[7:]

# Database File Paths
CURRENT_DIR = Path(__file__).resolve().parent
DB_CANDIDATE_PATHS = [
    CURRENT_DIR / "learned_task_memory.json",
    CURRENT_DIR.parent / "learned_task_memory.json",
    CURRENT_DIR.parent.parent / "learned_task_memory.json"
]
SKILLS_CANDIDATE_PATHS = [
    CURRENT_DIR / "learned_skills.json",
    CURRENT_DIR.parent / "learned_skills.json",
    CURRENT_DIR.parent.parent / "learned_skills.json"
]

def _resolve_db_path(candidates: List[Path]) -> Optional[Path]:
    for p in candidates:
        if p.exists():
            return p
    return candidates[0] if candidates else None

TASK_DB_PATH = _resolve_db_path(DB_CANDIDATE_PATHS)
SKILLS_DB_PATH = _resolve_db_path(SKILLS_CANDIDATE_PATHS)

# Known App Corrections & Shortcuts Dictionary
APP_CORRECTIONS = {
    "antigravty": "antigravity",
    "antigraviti": "antigravity",
    "anti gravity": "antigravity",
    "spotfy": "spotify",
    "spotifi": "spotify",
    "sopitfy": "spotify",
    "spoitfy": "spotify",
    "chrme": "chrome",
    "chrom": "chrome",
    "googl chrome": "chrome",
    "brav": "brave",
    "brave browser": "brave",
    "notpad": "notepad",
    "notepd": "notepad",
    "vscod": "visual studio code",
    "vs code": "visual studio code",
    "whatsap": "whatsapp",
    "watsapp": "whatsapp",
    "watsp": "whatsapp",
    "youtub": "youtube",
    "calclator": "calculator",
    "calc": "calculator",
    "canwa": "canva",
    "canv": "canva"
}

def correct_app_name(text: str) -> str:
    """Normalizes and corrects common typos in application names."""
    low = text.lower().strip()
    for typo, corrected in APP_CORRECTIONS.items():
        if typo in low:
            return corrected
    return low


# =============================================================================
# 1. DATABASE / PRIOR KNOWLEDGE LOOKUP
# =============================================================================

def load_prior_knowledge_databases() -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Loads tasks and skills databases from disk."""
    task_memory = {}
    skills = {}
    
    if TASK_DB_PATH and TASK_DB_PATH.exists():
        try:
            with open(TASK_DB_PATH, "r", encoding="utf-8") as f:
                task_memory = json.load(f)
        except Exception as e:
            print(f"[PRIOR KNOWLEDGE] Error loading task memory: {e}")

    if SKILLS_DB_PATH and SKILLS_DB_PATH.exists():
        try:
            with open(SKILLS_DB_PATH, "r", encoding="utf-8") as f:
                skills = json.load(f)
        except Exception as e:
            print(f"[PRIOR KNOWLEDGE] Error loading learned skills: {e}")

    return task_memory, skills


def check_prior_knowledge(task_text: str) -> Optional[Dict[str, Any]]:
    """
    Checks if this exact or similar task was previously accomplished.
    Returns the compiled procedural steps if found, or None if unknown.
    """
    clean_task = task_text.lower().strip()
    # Strip leading conversational prefixes
    for prefix in ["hydra", "jarvis", "please", "can you", "hey hydra", "could you", "do"]:
        if clean_task.startswith(prefix):
            clean_task = clean_task[len(prefix):].strip(", ").strip()

    task_memory, skills = load_prior_knowledge_databases()
    
    # 1. Check exact key match in task_memory
    for key, data in task_memory.items():
        if key.lower() == clean_task or data.get("id", "").lower() == clean_task:
            return {
                "found_in_database": True,
                "source": "database_prior_knowledge",
                "matched_key": key,
                "app": data.get("application", "unknown"),
                "intent": data.get("intent", ""),
                "summary": f"Retrieved verified action procedure from prior knowledge database for '{key}'.",
                "steps": data.get("steps", []),
                "confidence": data.get("confidence", 0.95)
            }

    # 2. Check exact key match in skills
    for key, data in skills.items():
        if key.lower() == clean_task:
            return {
                "found_in_database": True,
                "source": "database_prior_knowledge",
                "matched_key": key,
                "app": data.get("app", "unknown"),
                "intent": data.get("intent", ""),
                "summary": f"Retrieved compiled skill macro from prior knowledge database for '{key}'.",
                "steps": data.get("steps", []),
                "confidence": 0.99
            }

    # 3. Fuzzy / Semantic Similarity Match
    best_match = None
    best_score = 0.0

    # Search task memory entries
    for key, data in task_memory.items():
        intent_str = f"{data.get('application', '')} {data.get('intent', '')} {json.dumps(data.get('parameters', {}))}".lower()
        score = difflib.SequenceMatcher(None, clean_task, intent_str).ratio()
        
        # Token overlap bonus
        query_words = set(re.findall(r'\w+', clean_task))
        target_words = set(re.findall(r'\w+', intent_str))
        if query_words and target_words:
            overlap = len(query_words & target_words) / len(query_words)
            combined_score = (score * 0.4) + (overlap * 0.6)
        else:
            combined_score = score

        if combined_score > best_score:
            best_score = combined_score
            best_match = (key, data, "task_memory")

    # Search skills entries
    for key, data in skills.items():
        desc_str = f"{data.get('app', '')} {data.get('intent', '')} {data.get('description', '')}".lower()
        score = difflib.SequenceMatcher(None, clean_task, desc_str).ratio()
        
        query_words = set(re.findall(r'\w+', clean_task))
        target_words = set(re.findall(r'\w+', desc_str))
        if query_words and target_words:
            overlap = len(query_words & target_words) / len(query_words)
            combined_score = (score * 0.4) + (overlap * 0.6)
        else:
            combined_score = score

        if combined_score > best_score:
            best_score = combined_score
            best_match = (key, data, "skills")

    # Require high similarity threshold (>0.82) to avoid false positives on new tasks
    if best_match and best_score >= 0.82:
        key, data, source_db = best_match
        app_name = data.get("application") or data.get("app", "unknown")
        return {
            "found_in_database": True,
            "source": f"database_{source_db}",
            "matched_key": key,
            "app": app_name,
            "intent": data.get("intent", ""),
            "summary": f"Found high-confidence ({best_score:.2f}) prior procedure for '{key}' in database.",
            "steps": data.get("steps", []),
            "confidence": best_score
        }

    return None


# =============================================================================
# 2. AI GOAL DECOMPOSER PROMPT & INFERENCE
# =============================================================================

PLANNER_SYSTEM_PROMPT = """You are HYDRA's Autonomous Desktop Action Planner.
Output pure JSON only. Do not output any thinking process, reasoning scratchpad, markdown tags, or conversational text. Start directly with { and end with }.

RULES:
1. APP NAME CORRECTION:
   Correct any misspelled application names (e.g., 'antigravty' -> 'antigravity', 'spotfy' -> 'spotify', 'chrme' -> 'chrome', 'notpad' -> 'notepad', 'whatsap' -> 'whatsapp').
2. KEYBOARD SHORTCUTS FIRST:
   Always prioritize native keyboard shortcuts over mouse navigation wherever possible:
   - Spotify: Ctrl+L to search, Space for play/pause, Ctrl+Right for next track.
   - Antigravity / VS Code: Ctrl+, for settings, Ctrl+P for quick open, Ctrl+Shift+P for command palette.
   - Browsers (Chrome / Brave / Edge): Ctrl+T for new tab, Ctrl+L for address bar, Ctrl+W to close tab.
   - Windows Desktop: Win+R for Run dialog, Win key for search, Alt+Tab to switch apps.
3. DETAILED UI & SCREEN SCAN STEPS:
   Provide explicit, granular steps:
   - Open app and wait for window to render.
   - Use keyboard shortcuts (e.g., Ctrl+L) and type required query.
   - Wait for UI search results to load.
   - Scan/analyze screen to locate items.
   - Move cursor to UI elements and click (e.g. move cursor to play button on song row, click).
   - Checkpoint verification: scan screen to verify intermediate state (e.g. verify song is playing).
   - Next actions: move cursor to plus icon, click, move to liked songs icon, click.
   - Final verification: scan screen to verify goal is achieved.

REFERENCE EXAMPLE:
User Task: "hydra find laapata, play that song and also add that in my liked playlist"
Generated Plan:
{
  "task": "find laapata, play that song and also add that in my liked playlist",
  "app": "Spotify",
  "shortcuts_used": ["Ctrl+L"],
  "summary": "Open Spotify, search 'laapata' via Ctrl+L, play track, verify playback, add to liked songs, and verify in Liked Songs playlist.",
  "steps": [
    {"step": 1, "action": "open_app", "target": "Spotify", "details": "Open Spotify application."},
    {"step": 2, "action": "wait", "target": "Spotify window", "details": "Wait until Spotify opens and UI is visible."},
    {"step": 3, "action": "hotkey", "target": "Ctrl+L", "details": "Press Ctrl+L on keyboard to instantly focus search bar."},
    {"step": 4, "action": "type_text", "target": "search_bar", "details": "Type 'laapata' and press Enter."},
    {"step": 5, "action": "wait", "target": "search_results", "details": "Wait until search results appear."},
    {"step": 6, "action": "scan_screen", "target": "results_list", "details": "Scan screen and locate the row where 'laapata' is written."},
    {"step": 7, "action": "move_cursor", "target": "play_button", "details": "Move cursor to the Play button on the 'laapata' song row."},
    {"step": 8, "action": "click", "target": "play_button", "details": "Click Play button."},
    {"step": 9, "action": "wait", "target": "audio_playback", "details": "Wait until song plays."},
    {"step": 10, "action": "scan_screen_verify", "target": "playback_state", "details": "Scan screen and verify that the song is currently playing."},
    {"step": 11, "action": "move_cursor", "target": "plus_icon", "details": "Move cursor to the plus (+) icon on that song row."},
    {"step": 12, "action": "click", "target": "plus_icon", "details": "Click plus (+) icon to add song."},
    {"step": 13, "action": "move_cursor", "target": "liked_songs_nav", "details": "Move cursor to the purple square with heart icon (Liked Songs) on the left sidebar."},
    {"step": 14, "action": "click", "target": "liked_songs_nav", "details": "Click Liked Songs icon."},
    {"step": 15, "action": "scan_screen_verify", "target": "liked_songs_list", "details": "Scan screen and verify if 'laapata' is present in Liked Songs."},
    {"step": 16, "action": "complete", "target": "goal", "details": "Task completed successfully."}
  ]
}
"""

def extract_json_plan(raw_text: str) -> Optional[Dict[str, Any]]:
    """Robustly extracts and parses JSON plan from raw model output."""
    if not raw_text:
        return None
    cleaned = raw_text.strip()
    
    # 1. Direct parse
    try:
        return json.loads(cleaned)
    except Exception:
        pass

    # 2. Extract from markdown code fences
    match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', raw_text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except Exception:
            pass

    # 3. Find outermost matching { ... } containing "steps"
    starts = [i for i, c in enumerate(raw_text) if c == '{']
    for start in starts:
        for end in range(len(raw_text), start, -1):
            if raw_text[end-1] == '}':
                sub = raw_text[start:end]
                if '"steps"' in sub:
                    try:
                        return json.loads(sub)
                    except Exception:
                        pass
    return None


def decompose_goal_plan(task_text: str) -> Dict[str, Any]:
    """
    Main Phase 2 Entry Point:
    1. Checks if task exists in database (prior knowledge). If found, returns exact steps.
    2. If not found, prompts NVIDIA Nemotron-3_5-Lightning for a keyboard-first action plan.
    """
    t0 = time.perf_counter()
    clean_task = task_text.strip()
    
    print(f"\n[GOAL DECOMPOSER] Received task: '{clean_task}'")
    print(f"[GOAL DECOMPOSER] Step 1: Checking prior knowledge database...")

    # Step 1: Check Database First
    prior_match = check_prior_knowledge(clean_task)
    if prior_match:
        latency = time.perf_counter() - t0
        print(f"[GOAL DECOMPOSER] -> MATCH FOUND in database ({prior_match['matched_key']}) in {latency:.2f}s!")
        return prior_match

    print(f"[GOAL DECOMPOSER] -> No prior knowledge found in database. Asking AI Planner (NVIDIA Nemotron)...")

    # Step 2: Ask NVIDIA Nemotron Model via Nebius
    try:
        from openai import OpenAI
        client = OpenAI(
            base_url=NEBIUS_BASE_URL,
            api_key=NEBIUS_API_KEY or "not_set",
            timeout=35.0,
            max_retries=2
        )

        user_content = f"Task: {clean_task}\nGenerate the detailed keyboard-first action plan as JSON:"
        messages = [
            {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
            {"role": "user", "content": user_content}
        ]

        # Use extra_body chat_template_kwargs to minimize thinking overhead
        try:
            completion = client.chat.completions.create(
                model=NEBIUS_FAST_MODEL,
                messages=messages,
                max_tokens=4000,
                temperature=0.1,
                extra_body={"chat_template_kwargs": {"thinking": False}}
            )
        except Exception:
            # Fallback without extra_body if not supported
            completion = client.chat.completions.create(
                model=NEBIUS_FAST_MODEL,
                messages=messages,
                max_tokens=4000,
                temperature=0.1
            )

        raw_output = completion.choices[0].message.content or ""
        plan = extract_json_plan(raw_output)
        latency = time.perf_counter() - t0

        if plan and isinstance(plan, dict):
            plan["found_in_database"] = False
            plan["source"] = "ai_planner_nemotron"
            plan["latency_seconds"] = round(latency, 2)
            print(f"[GOAL DECOMPOSER] -> AI plan generated successfully in {latency:.2f}s! Total steps: {len(plan.get('steps', []))}")
            return plan

        # If JSON extraction had issues, construct a safe structured fallback from text
        print(f"[GOAL DECOMPOSER] Notice: Parsing structured text plan into schema...")
        lines = [line.strip() for line in raw_output.split("\n") if line.strip()]
        steps = []
        step_num = 1
        for line in lines:
            if re.match(r'^(?:\d+[\.:\)]|[-*])\s*', line):
                step_text = re.sub(r'^(?:\d+[\.:\)]|[-*])\s*', '', line).strip()
                steps.append({
                    "step": step_num,
                    "action": "action",
                    "target": "",
                    "details": step_text
                })
                step_num += 1

        fallback_plan = {
            "task": clean_task,
            "app": correct_app_name(clean_task),
            "shortcuts_used": ["Ctrl+L"],
            "summary": f"Action plan decomposed for {clean_task}.",
            "found_in_database": False,
            "source": "ai_planner_nemotron",
            "latency_seconds": round(latency, 2),
            "steps": steps or [{"step": 1, "action": "open_app", "target": "", "details": clean_task}]
        }
        return fallback_plan

    except Exception as e:
        print(f"[GOAL DECOMPOSER ERROR] AI planner failed: {e}")
        return {
            "task": clean_task,
            "app": correct_app_name(clean_task),
            "shortcuts_used": [],
            "summary": f"Could not contact AI planner: {e}",
            "found_in_database": False,
            "source": "error_fallback",
            "steps": []
        }


def format_plan_for_display(plan: Dict[str, Any]) -> str:
    """Formats the plan into a clean readable string for logs and terminal display."""
    lines = []
    lines.append("=" * 65)
    lines.append(f"  HYDRA ACTION PLAN: {plan.get('task', 'Untitled')}")
    lines.append(f"  Target Application: {plan.get('app', 'Unknown').upper()}")
    lines.append(f"  Source: {'Prior Database' if plan.get('found_in_database') else 'NVIDIA Nemotron AI Planner'}")
    if plan.get("shortcuts_used"):
        lines.append(f"  Key Shortcuts: {', '.join(plan.get('shortcuts_used', []))}")
    lines.append(f"  Summary: {plan.get('summary', '')}")
    lines.append("-" * 65)
    
    steps = plan.get("steps", [])
    for idx, s in enumerate(steps, 1):
        if isinstance(s, dict):
            num = s.get("step") or s.get("id") or idx
            action = (s.get("action") or s.get("action_type") or "action").upper()
            target = s.get("target") or ""
            details = s.get("details") or s.get("description") or ""
            line_str = f"  [{num:02d}] {action:18} | {details}"
            if target and target not in details:
                line_str += f" (Target: {target})"
            lines.append(line_str)
        else:
            lines.append(f"  [{idx:02d}] {str(s)}")

    lines.append("=" * 65)
    return "\n".join(lines)


if __name__ == "__main__":
    test_queries = [
        "hydra find laapata, play that song and also add that in my liked playlist",
        "antigravty change theme to abyss",
        "spotify mute"
    ]
    for q in test_queries:
        plan_res = decompose_goal_plan(q)
        print(format_plan_for_display(plan_res))
