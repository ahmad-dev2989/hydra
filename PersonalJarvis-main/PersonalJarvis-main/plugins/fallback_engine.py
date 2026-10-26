"""
HYDRA Transparent Headless-to-Foreground Fallback Engine (Phase 5)
Provides an intelligent bridge when headless operations encounter auth blocks,
missing scopes, unconfigured API keys, or when the user explicitly requests visual display.

Features:
1. Fallback Target Registry: Complete registry of browser URLs (with authuser embedding for Google accounts)
   and Windows OS desktop protocols for all supported services.
2. Visual Request Detection: Intelligently identifies if the user command requested visual display
   (e.g., "show me", "open on screen", "let me see", "view assignments in browser").
3. Interactive Disambiguation Prompting: If a headless task fails due to unauthenticated / permission barriers,
   prompts the user: "Would you like me to open {app_name} on your screen so you can view/complete this, sir?"
4. Direct Headless-to-Foreground Hand-off: Seamlessly launches browser or desktop application and,
   if required, delegates to VerifiedExecutor desktop vision agent.
"""

import os
import sys
import re
import json
import webbrowser
from pathlib import Path
from typing import Dict, Any, Tuple, Optional

CURRENT_DIR = Path(__file__).resolve().parent
ROOT_DIR = CURRENT_DIR.parent

try:
    from .vault_manager import VAULT_MGR
except ImportError:
    from vault_manager import VAULT_MGR


# Registry of Fallback Targets with URLs, Protocols, and Browser requirements
FALLBACK_TARGETS: Dict[str, Dict[str, Any]] = {
    "google_classroom": {
        "name": "Google Classroom",
        "url_template": "https://classroom.google.com/u/?authuser={account}",
        "default_url": "https://classroom.google.com/",
        "protocol": None,
        "requires_browser": True
    },
    "gmail": {
        "name": "Gmail",
        "url_template": "https://mail.google.com/mail/u/?authuser={account}",
        "default_url": "https://mail.google.com/",
        "protocol": None,
        "requires_browser": True
    },
    "spotify": {
        "name": "Spotify",
        "url_template": "https://open.spotify.com/",
        "default_url": "https://open.spotify.com/",
        "protocol": "spotify:",
        "requires_browser": False
    },
    "whatsapp": {
        "name": "WhatsApp",
        "url_template": "https://web.whatsapp.com/",
        "default_url": "https://web.whatsapp.com/",
        "protocol": "whatsapp://",
        "requires_browser": False
    },
    "canva": {
        "name": "Canva",
        "url_template": "https://www.canva.com/create/",
        "default_url": "https://www.canva.com/",
        "protocol": None,
        "requires_browser": True
    },
    "discord": {
        "name": "Discord",
        "url_template": "https://discord.com/channels/@me",
        "default_url": "https://discord.com/app",
        "protocol": "discord://",
        "requires_browser": False
    },
    "github": {
        "name": "GitHub",
        "url_template": "https://github.com/{account}",
        "default_url": "https://github.com/",
        "protocol": None,
        "requires_browser": True
    },
    "linkedin": {
        "name": "LinkedIn",
        "url_template": "https://www.linkedin.com/feed/",
        "default_url": "https://www.linkedin.com/feed/",
        "protocol": None,
        "requires_browser": True
    },
    "youtube": {
        "name": "YouTube",
        "url_template": "https://www.youtube.com/",
        "default_url": "https://www.youtube.com/",
        "protocol": None,
        "requires_browser": True
    }
}

VISUAL_REQUEST_PATTERNS = [
    r"\bshow(?:\s+me)?\b",
    r"\blet\s+me\s+see\b",
    r"\bview\s+(?:on\s+)?(?:screen|browser|tab)\b",
    r"\bopen\s+(?:in\s+)?(?:browser|screen|tab|window)\b",
    r"\bdisplay\s+(?:on\s+)?(?:screen|browser)\b",
    r"\bon\s+my\s+screen\b",
    r"\bon\s+screen\b",
    r"\bin\s+browser\b",
    r"\bin\s+the\s+browser\b",
    r"\blook\s+at\b"
]


class FallbackEngine:
    """
    Manages intelligent, transparent transitions from headless execution
    to foreground browser / desktop application execution.
    """

    def __init__(self):
        self.targets = FALLBACK_TARGETS

    def get_target(self, app_id: str) -> Dict[str, Any]:
        """Returns target dictionary for given app_id with resolved account URLs."""
        clean_id = app_id.lower().strip().replace(" ", "_")
        target = dict(self.targets.get(clean_id, {
            "name": clean_id.replace("_", " ").title(),
            "url_template": "",
            "default_url": "",
            "protocol": None,
            "requires_browser": True
        }))

        # Resolve active account from Vault
        acc = VAULT_MGR.get_account_email(clean_id) or ""
        url_tpl = target.get("url_template", "")
        if url_tpl and acc:
            target["url"] = url_tpl.replace("{account}", acc)
        else:
            target["url"] = target.get("default_url", "")

        return target

    def is_visual_request(self, raw_query: str) -> bool:
        """Returns True if the query explicitly asks for visual / on-screen display."""
        low = raw_query.lower().strip()
        for pat in VISUAL_REQUEST_PATTERNS:
            if re.search(pat, low):
                return True
        return False

    def launch_target(self, target_info: Dict[str, Any], query: str = "") -> Tuple[bool, str]:
        """
        Launches the target application or web URL with zero desktop crash risk.
        """
        name = target_info.get("name", "Application")
        protocol = target_info.get("protocol")
        url = target_info.get("url")

        # 1. Try desktop protocol if available and not explicitly a browser request
        if protocol and "browser" not in query.lower():
            try:
                os.startfile(protocol)
                print(f"[FALLBACK ENGINE] Launched desktop protocol: '{protocol}' for {name}")
                return True, f"Opening {name} on your screen, sir."
            except Exception as e:
                print(f"[FALLBACK NOTICE] Protocol {protocol} failed, falling back to URL: {e}")

        # 2. Try Web URL in browser
        if url:
            try:
                webbrowser.open(url)
                print(f"[FALLBACK ENGINE] Opened web URL: '{url}' for {name}")
                return True, f"Opening {name} in your browser, sir."
            except Exception as e:
                print(f"[FALLBACK ERROR] Web browser launch failed for {name}: {e}")
                return False, f"Could not launch browser for {name}: {e}"

        return False, f"No launch destination configured for {name}, sir."

    def should_trigger_fallback(
        self,
        app_id: str,
        action: str,
        raw_query: str,
        worker_result: Tuple[bool, str, Dict[str, Any]]
    ) -> bool:
        """
        Determines whether a transparent fallback to foreground is required or appropriate.
        """
        ok, speech, meta = worker_result

        # Condition 1: User explicitly requested visual display on screen
        if self.is_visual_request(raw_query):
            return True

        # Condition 2: Headless execution succeeded and user didn't ask for visual -> no fallback needed
        if ok:
            return False

        # Condition 3: Headless execution failed due to auth/permission barriers or missing credentials
        low_speech = speech.lower()
        auth_blockers = [
            "not connected",
            "not authenticated",
            "authorization error",
            "api authorization",
            "app password",
            "webhook url",
            "personal access token",
            "allow me to open",
            "open in your browser",
            "open on your screen",
            "visual canvas",
            "interactive"
        ]

        if any(b in low_speech for b in auth_blockers):
            return True

        # Check worker metadata status
        status = meta.get("status", "") or meta.get("last_status", "")
        if status in ["AUTH_ERROR", "NO_TOKEN", "NO_SESSION", "NEEDS_AUTH", "WEBHOOK_MISSING"]:
            return True

        return False

    def handle_fallback(
        self,
        app_id: str,
        raw_query: str,
        reason: str = "",
        worker_speech: str = "",
        auto_launch: bool = False
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Executes transparent fallback or interactively asks the user via Disambiguation Manager.
        """
        target = self.get_target(app_id)
        app_name = target.get("name", app_id.replace("_", " ").title())

        # If user explicitly asked for visual/screen/browser display, launch immediately
        if auto_launch or self.is_visual_request(raw_query):
            ok_launch, sp_launch = self.launch_target(target, raw_query)
            return ok_launch, sp_launch, {
                "fallback": True,
                "target": app_name,
                "url": target.get("url"),
                "auto_launched": True
            }

        # Otherwise, offer interactive fallback through Disambiguation Manager
        try:
            from disambiguation_manager import DISAMBIGUATION_MGR
        except ImportError:
            DISAMBIGUATION_MGR = None

        if DISAMBIGUATION_MGR:
            # Extract clean reason summary from worker speech
            clean_reason = reason.strip()
            # Strip any leading 'Sir, ' to prevent "Sir, Sir, ..."
            clean_reason = re.sub(r'^(?:sir,?\s*)+', '', clean_reason, flags=re.IGNORECASE).strip()
            if not clean_reason:
                clean_reason = f"live background access for {app_name} is not available"

            # Clean speech for concise question
            if "Would you like me to open" in clean_reason:
                prompt_q = f"Sir, {clean_reason}"
            else:
                punct = "" if clean_reason.endswith((".", "!", "?")) else "."
                prompt_q = f"Sir, {clean_reason}{punct} Would you like me to open {app_name} on your screen?"

            ok_ans, ans = DISAMBIGUATION_MGR.ask_user(
                prompt_q,
                options=[f"Yes, open {app_name}", "No, cancel"],
                barrier_type="dynamic"
            )

            ans_clean = ans.lower().strip()
            if ok_ans and any(w in ans_clean for w in ["yes", "open", "sure", "ok", "launch", "show", "please", "1"]):
                ok_l, sp_l = self.launch_target(target, raw_query)
                return ok_l, sp_l, {
                    "fallback": True,
                    "target": app_name,
                    "url": target.get("url"),
                    "user_confirmed": True
                }
            else:
                speech_declined = f"Understood, sir. {app_name} was not opened."
                return False, speech_declined, {
                    "fallback": False,
                    "target": app_name,
                    "cancelled": True
                }

        # If no disambiguation manager available, return worker's original speech
        return False, worker_speech or f"{app_name} could not complete in the background, sir.", {}


FALLBACK_ENGINE = FallbackEngine()
