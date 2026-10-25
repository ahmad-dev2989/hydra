"""
HYDRA Direct Background Task Dispatcher (Phase 1 & Phase 7)
Intercepts user commands and dispatches them to connected headless background workers
strictly based on intelligent intent classification.
Preserves foreground GUI tasks and conversational inquiries without false intercepts.
"""

import re
from typing import Optional, Tuple
from .vault_manager import VAULT_MGR
from .intent_classifier import classify_command, IntentType, IntentResult
from .gmail_direct_worker import GMAIL_WORKER
from .classroom_direct_worker import CLASSROOM_WORKER
from .spotify_direct_worker import SPOTIFY_WORKER
from .whatsapp_direct_worker import WHATSAPP_WORKER
from .social_direct_worker import (
    YOUTUBE_WORKER,
    CANVA_WORKER,
    DISCORD_WORKER,
    GITHUB_WORKER,
    LINKEDIN_WORKER
)


class DirectBackgroundDispatcher:
    """Routes voice and text action goals directly to headless workers."""

    def __init__(self):
        self.workers = {
            "gmail": GMAIL_WORKER,
            "google_classroom": CLASSROOM_WORKER,
            "spotify": SPOTIFY_WORKER,
            "whatsapp": WHATSAPP_WORKER,
            "youtube": YOUTUBE_WORKER,
            "canva": CANVA_WORKER,
            "discord": DISCORD_WORKER,
            "github": GITHUB_WORKER,
            "linkedin": LINKEDIN_WORKER
        }

    def dispatch(self, cmd_clean: str, classified: Optional[IntentResult] = None) -> Optional[Tuple[bool, str]]:
        """
        Attempts to execute the user command directly in the background.
        Returns:
            (success: bool, speech: str) if handled by a background worker,
            or None if the task should proceed to visual desktop automation or Brain LLM.
        """
        if classified is None:
            classified = classify_command(cmd_clean)

        # 1. Do NOT intercept Conversational queries, Foreground UI tasks, or Builtin UI
        if classified.intent_type in [IntentType.CONVERSATIONAL, IntentType.FOREGROUND_DESKTOP, IntentType.BUILTIN_UI]:
            return None

        low = cmd_clean.lower().strip()

        # 2. Account Management (Switching & Listing registered Vault accounts)
        if classified.intent_type == IntentType.ACCOUNT_MANAGEMENT:
            return self._handle_account_management(cmd_clean, low, classified.target_app)

        # 3. Headless Background Actions
        if classified.intent_type == IntentType.HEADLESS_BACKGROUND:
            target_app = classified.target_app
            action = classified.action or "auto"
            params = classified.parameters or {}

            # Standalone Transport
            if action in ["transport", "next_track", "prev_track", "pause", "resume"] or low in ["pause", "resume", "stop", "next", "skip", "previous", "prev", "unpause", "mute", "unmute"]:
                print(f"[DIRECT DISPATCHER] Routing transport '{action}' ({low}) to Spotify Direct Background Worker...")
                ok, speech, _ = SPOTIFY_WORKER.execute_task(action, cmd_clean, **params)
                return ok, speech

            # Route through Transparent Fallback Engine
            if target_app and target_app in self.workers:
                worker = self.workers[target_app]
                print(f"[DIRECT DISPATCHER] Routing to {worker.app_name} Direct Background Worker ({action})...")
                return self._dispatch_with_fallback(target_app, action, cmd_clean, worker, params)

        return None

    def _dispatch_with_fallback(self, target_app: str, action: str, cmd_clean: str, worker, params: dict) -> Tuple[bool, str]:
        """
        Executes headless worker and transparently applies foreground fallback if needed.
        """
        try:
            from .fallback_engine import FALLBACK_ENGINE
        except ImportError:
            from fallback_engine import FALLBACK_ENGINE

        # 1. If user explicitly demanded visual display on screen/browser upfront
        if FALLBACK_ENGINE.is_visual_request(cmd_clean):
            print(f"[DIRECT DISPATCHER] User explicitly requested visual display for '{target_app}'. Triggering immediate foreground fallback...")
            ok_f, speech_f, _ = FALLBACK_ENGINE.handle_fallback(target_app, cmd_clean, auto_launch=True)
            return ok_f, speech_f

        # 2. Execute headless worker
        ok, speech, meta = worker.execute_task(action, cmd_clean, **params)

        # 3. If worker succeeded, return clean headless result
        if ok:
            return ok, speech

        # 4. If worker failed, evaluate if transparent foreground fallback is appropriate
        if FALLBACK_ENGINE.should_trigger_fallback(target_app, action, cmd_clean, (ok, speech, meta)):
            print(f"[DIRECT DISPATCHER] Headless {target_app} returned unauthenticated/permission blocker. Offering foreground fallback...")
            ok_f, speech_f, meta_f = FALLBACK_ENGINE.handle_fallback(target_app, cmd_clean, reason=speech, worker_speech=speech)
            return ok_f, speech_f

        return ok, speech

    def _handle_account_management(self, cmd_clean: str, low: str, target_app: Optional[str]) -> Tuple[bool, str]:
        """Handles explicit account listing and switching."""
        # Check if user wants global list of connected accounts
        if not target_app or any(w in low for w in ["which accounts", "what accounts", "list accounts", "connected accounts", "show accounts"]):
            if not target_app:
                all_status = []
                for a_id in ["gmail", "google_classroom", "spotify", "whatsapp", "github", "linkedin", "canva", "discord"]:
                    accs = VAULT_MGR.get_all_accounts(a_id)
                    if accs:
                        act = next((a["account"] for a in accs if a.get("active")), accs[0]["account"])
                        all_status.append(f"{a_id.replace('_', ' ').title()}: {act} ({len(accs)} account{'s' if len(accs)>1 else ''})")
                if all_status:
                    return True, f"Sir, here are your registered accounts: {'; '.join(all_status)}."
                else:
                    return True, "Sir, there are no accounts connected yet."

        if target_app:
            accounts = VAULT_MGR.get_all_accounts(target_app)
            if any(w in low for w in ["which", "what", "list", "show", "connected"]) and not any(w in low for w in ["switch", "change", "use", "toggle"]):
                if accounts:
                    acc_list_str = ", ".join([f"{a['account']}" + (f" ({a.get('label')}, Active)" if a.get("active") else (f" ({a.get('label')})" if a.get("label") else "")) for a in accounts])
                    return True, f"Sir, you have {len(accounts)} account(s) registered for {target_app.replace('_', ' ').title()}: {acc_list_str}."
                else:
                    return True, f"Sir, you do not have any accounts connected for {target_app.replace('_', ' ').title()} yet."

            matched_acc = None
            for a in accounts:
                cand_acc = a["account"].lower()
                cand_lbl = a.get("label", "").lower()
                acc_user = cand_acc.split("@")[0] if "@" in cand_acc else cand_acc
                if cand_acc in low or (len(acc_user) >= 4 and acc_user in low) or (cand_lbl and cand_lbl in low):
                    matched_acc = a
                    break

            if matched_acc:
                ok = VAULT_MGR.switch_active_account(target_app, matched_acc["account"])
                if ok:
                    try:
                        from main import safe_emit_js
                        safe_emit_js(f"if (window.onAccountSwitched) window.onAccountSwitched('{target_app}', '{matched_acc['account']}');")
                    except Exception:
                        pass
                    return True, f"Sir, switched active {target_app.replace('_', ' ').title()} account to {matched_acc['account']}."
            elif len(accounts) >= 2:
                inactive_accs = [a for a in accounts if not a.get("active")]
                if inactive_accs:
                    target_switch = inactive_accs[0]
                    ok = VAULT_MGR.switch_active_account(target_app, target_switch["account"])
                    if ok:
                        try:
                            from main import safe_emit_js
                            safe_emit_js(f"if (window.onAccountSwitched) window.onAccountSwitched('{target_app}', '{target_switch['account']}');")
                        except Exception:
                            pass
                        return True, f"Sir, switched active {target_app.replace('_', ' ').title()} account to {target_switch['account']} ({target_switch.get('label', 'Account')})."

        return False, "Could not determine which account to switch to, sir."


DIRECT_DISPATCHER = DirectBackgroundDispatcher()
