"""
HYDRA Phase 4: Human-in-the-Loop Conversational Disambiguation Engine
Handles unexpected barrier screens (e.g., Chrome/Edge profile selectors, account pickers,
permission dialogs, ambiguous options) by pausing execution, querying the user via Voice & HUD,
and resuming seamlessly with user preferences cached for future runs.
"""

import os
import sys
import time
import json
import re
import threading
from typing import Dict, Any, List, Optional, Tuple
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
PARENT_DIR = CURRENT_DIR.parent
GRANDPARENT_DIR = PARENT_DIR.parent

PREFS_CANDIDATE_PATHS = [
    CURRENT_DIR / "user_preferences.json",
    PARENT_DIR / "user_preferences.json",
    GRANDPARENT_DIR / "user_preferences.json"
]

def _resolve_prefs_path() -> Path:
    for p in PREFS_CANDIDATE_PATHS:
        if p.exists():
            return p
    return PREFS_CANDIDATE_PATHS[0]

PREFS_FILE = _resolve_prefs_path()


class DisambiguationManager:
    """
    Coordinates Human-in-the-Loop pauses, voice queries, HUD cards,
    and user clarification response routing.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._waiting = False
        self._event = threading.Event()
        self._current_question = ""
        self._current_options: List[str] = []
        self._current_barrier_type = ""
        self._user_response: Optional[str] = None
        self._preferences: Dict[str, str] = self._load_preferences()

    def _load_preferences(self) -> Dict[str, str]:
        """Loads cached user decisions (e.g., default Chrome profile, preferred account)."""
        if PREFS_FILE.exists():
            try:
                with open(PREFS_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"[PREFERENCES NOTICE] Could not load user preferences: {e}")
        return {}

    def save_preference(self, key: str, value: str):
        """Saves a user preference to avoid asking repeatedly for known barriers."""
        with self._lock:
            self._preferences[key.lower().strip()] = value.strip()
            for candidate in PREFS_CANDIDATE_PATHS:
                try:
                    with open(candidate, "w", encoding="utf-8") as f:
                        json.dump(self._preferences, f, indent=2)
                except Exception:
                    pass
        print(f"[PREFERENCES] Saved '{key}' -> '{value}' for future auto-selection.")

    def get_preference(self, key: str) -> Optional[str]:
        with self._lock:
            return self._preferences.get(key.lower().strip())

    def get_all_preferences(self) -> Dict[str, str]:
        with self._lock:
            return dict(self._preferences)

    def is_waiting(self) -> bool:
        with self._lock:
            return self._waiting

    def get_pending_question(self) -> str:
        with self._lock:
            return self._current_question

    def get_pending_options(self) -> List[str]:
        with self._lock:
            return list(self._current_options)

    def ask_user(
        self,
        question: str,
        options: Optional[List[str]] = None,
        barrier_type: str = "",
        timeout: float = 45.0,
        voice_callback=None,
        hud_callback=None
    ) -> Tuple[bool, str]:
        """
        Pauses current autonomous agent thread, speaks question to user, emits HUD card,
        and waits synchronously for user response via voice or HUD text input.
        Returns: (success: bool, user_response: str)
        """
        # 1. Check if preference already exists for this barrier (only for persistent barriers like browser_profile)
        is_dynamic = (barrier_type in ["dynamic", "no_cache", "param", "content"]) or any(w in question.lower() for w in ["message", "body", "name", "subject", "recipient", "public or private"])
        b_key = barrier_type.lower().strip() if (barrier_type and not is_dynamic) else ("" if is_dynamic else self._infer_barrier_key(question))
        candidate_keys = [b_key] if b_key else []
        if not is_dynamic:
            if b_key and "profile" in b_key and "browser_profile" not in candidate_keys:
                candidate_keys.append("browser_profile")
            elif not b_key and "profile" in question.lower():
                candidate_keys.append("browser_profile")

            for k in candidate_keys:
                if k and k in self._preferences:
                    cached_val = self._preferences[k]
                    # If options given, verify cached value is present
                    if not options or any(cached_val.lower() in opt.lower() for opt in options):
                        print(f"\n[DISAMBIGUATION] Auto-resolved via cached preference '{k}': '{cached_val}'")
                        return True, cached_val

        # 2. Setup disambiguation state
        with self._lock:
            self._waiting = True
            self._event.clear()
            self._current_question = question.strip()
            self._current_options = [opt.strip() for opt in (options or []) if opt.strip()]
            self._current_barrier_type = b_key
            self._user_response = None

        print("\n" + "=" * 65, flush=True)
        print("  [HUMAN-IN-THE-LOOP] HYDRA PAUSED FOR USER CLARIFICATION", flush=True)
        print(f"  Question: {self._current_question}", flush=True)
        if self._current_options:
            print(f"  Options : {', '.join(self._current_options)}", flush=True)
        print("=" * 65 + "\n", flush=True)

        # 3. Resolve callbacks if not provided
        if hud_callback is None:
            try:
                from main import safe_emit_js
                hud_callback = lambda q, opts: safe_emit_js(f"showDisambiguationPrompt({json.dumps(q)}, {json.dumps(opts)});")
            except Exception:
                pass

        if voice_callback is None:
            try:
                from main import speak_voice
                voice_callback = speak_voice
            except Exception:
                pass

        # 4. Notify UI HUD
        if hud_callback:
            try:
                hud_callback(self._current_question, self._current_options)
            except Exception as e:
                print(f"[HUD ERROR] {e}")

        # 5. Speak Voice Prompt
        if voice_callback:
            try:
                voice_callback(self._current_question)
            except Exception as e:
                print(f"[VOICE ERROR] {e}")

        # 6. Wait for user input (event signaled by provide_answer)
        signaled = self._event.wait(timeout=timeout)

        with self._lock:
            self._waiting = False
            response = self._user_response

        if signaled and response:
            clean_resp = response.strip()
            # Resolve numeric or ordinal response (e.g. "first one", "1", "option 2")
            resolved = self._resolve_choice(clean_resp, self._current_options)
            print(f"[DISAMBIGUATION] User clarified: '{resolved}' (Raw: '{clean_resp}')\n", flush=True)

            # Auto-save preference if relevant barrier
            if b_key:
                self.save_preference(b_key, resolved)

            return True, resolved
        else:
            print(f"[DISAMBIGUATION TIMEOUT] No response received within {timeout}s.", flush=True)
            # Default to first option if available
            fallback = self._current_options[0] if self._current_options else "Default"
            return False, fallback

    def provide_answer(self, user_input: str) -> bool:
        """
        Called when user submits text or speech while a disambiguation prompt is waiting.
        Unblocks the waiting thread immediately.
        """
        with self._lock:
            if not self._waiting:
                return False
            self._user_response = user_input.strip()
            self._event.set()
            return True

    def _infer_barrier_key(self, text: str) -> str:
        low = text.lower()
        if "brave" in low and ("profile" in low or "account" in low or "who" in low):
            return "brave_profile"
        if "chrome" in low and ("profile" in low or "account" in low or "who" in low):
            return "chrome_profile"
        if "edge" in low and ("profile" in low or "account" in low or "who" in low):
            return "edge_profile"
        if "profile" in low or "who's using" in low or "who is using" in low:
            return "browser_profile"
        if "google" in low and ("account" in low or "sign in" in low):
            return "google_account"
        if "permission" in low or "allow" in low:
            return "allow_permissions"
        if any(w in low for w in ["cred", "password", "login", "username", "email"]):
            return "login_credentials"
        return ""

    def _resolve_choice(self, user_text: str, options: List[str]) -> str:
        """Resolves ordinal or partial text match against available options."""
        if not options:
            return user_text

        low_u = user_text.lower().strip()

        # Check exact or substring match in options
        for opt in options:
            if opt.lower() == low_u:
                return opt
        for opt in options:
            if opt.lower() in low_u or low_u in opt.lower():
                return opt

        # Check numerical / ordinal words
        ordinal_map = {
            "1": 0, "first": 0, "one": 0, "option 1": 0, "first one": 0,
            "2": 1, "second": 1, "two": 1, "option 2": 1, "second one": 1,
            "3": 2, "third": 2, "three": 2, "option 3": 2, "third one": 2,
            "4": 3, "fourth": 3, "four": 3, "option 4": 3
        }
        for ord_word, idx in ordinal_map.items():
            if ord_word in low_u and idx < len(options):
                return options[idx]

        return user_text


# Global Singleton Instance
DISAMBIGUATION_MGR = DisambiguationManager()
