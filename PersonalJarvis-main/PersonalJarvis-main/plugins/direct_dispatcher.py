"""
HYDRA Direct Background Task Dispatcher (Phase 1 & Phase 7)
Intercepts user commands and dispatches them to connected headless background workers
before attempting desktop/browser window launches.
"""

import re
from typing import Optional, Tuple
from .vault_manager import VAULT_MGR
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

    def dispatch(self, cmd_clean: str) -> Optional[Tuple[bool, str]]:
        """
        Attempts to execute the user command directly in the background.
        Returns:
            (success: bool, speech: str) if handled by a background worker,
            or None if the task should proceed to visual desktop automation.
        """
        low = cmd_clean.lower().strip()

        # 1. Gmail Background Actions
        if any(w in low for w in ["gmail", "gmails", "email", "emails", "inbox", "mail"]):
            if GMAIL_WORKER.is_ready():
                if any(w in low for w in ["send", "write", "dispatch", "compose", "mail to", "email to"]):
                    print("[DIRECT DISPATCHER] Routing to Gmail Direct Background Worker (Send)...")
                    ok, speech, _ = GMAIL_WORKER.execute_task("send", cmd_clean)
                    return ok, speech
                elif any(w in low for w in ["search", "find", "look for", "query"]):
                    print("[DIRECT DISPATCHER] Routing to Gmail Direct Background Worker (Search)...")
                    ok, speech, _ = GMAIL_WORKER.execute_task("search", cmd_clean)
                    return ok, speech
                elif any(w in low for w in ["check", "unread", "read", "new", "get", "fetch", "any", "status", "have"]):
                    print("[DIRECT DISPATCHER] Routing to Gmail Direct Background Worker (Unread Check)...")
                    ok, speech, _ = GMAIL_WORKER.execute_task("check_unread", cmd_clean)
                    return ok, speech

        # 2. Google Classroom Background Actions
        if any(w in low for w in ["classroom", "assignment", "assignments", "homework", "due", "classwork", "coursework"]):
            if CLASSROOM_WORKER.is_ready():
                if any(w in low for w in ["today", "urgent"]):
                    print("[DIRECT DISPATCHER] Routing to Google Classroom Direct Background Worker (Due Today)...")
                    ok, speech, _ = CLASSROOM_WORKER.execute_task("due_today", cmd_clean)
                    return ok, speech
                elif any(w in low for w in ["announcement", "announcements", "stream", "notice", "notices"]):
                    print("[DIRECT DISPATCHER] Routing to Google Classroom Direct Background Worker (Announcements)...")
                    ok, speech, _ = CLASSROOM_WORKER.execute_task("announcements", cmd_clean)
                    return ok, speech
                else:
                    print("[DIRECT DISPATCHER] Routing to Google Classroom Direct Background Worker (Upcoming Assignments)...")
                    ok, speech, _ = CLASSROOM_WORKER.execute_task("check_assignments", cmd_clean)
                    return ok, speech

        # 3. Spotify Background Actions
        is_music_cmd = any(w in low for w in ["spotify", "music", "song", "track", "playlist"])
        is_audio_control = any(w in low for w in ["volume up", "volume down", "louder", "quieter", "mute", "unmute"])
        is_transport_action = any(w in low for w in ["play", "pause", "resume", "unpause", "stop", "next", "prev", "previous", "skip", "what song", "which song", "current song", "now playing", "playing song"])
        is_play_song = bool(re.search(r'\b(?:play|listen to|put on)\s+(.+)', low)) and not any(w in low for w in ["youtube", "video", "yt", "game"])
        is_resume = any(w in low for w in ["resume", "unpause"])
        is_spotify_query = ("spotify" in low or "music" in low) and any(w in low for w in ["subscription", "plan", "settings", "setting", "account", "status", "quality", "tier"])

        if (is_music_cmd and is_transport_action) or is_audio_control or is_play_song or is_resume or is_spotify_query:
            if SPOTIFY_WORKER.is_ready():
                print("[DIRECT DISPATCHER] Routing to Spotify Direct Background Worker...")
                ok, speech, _ = SPOTIFY_WORKER.execute_task("transport", cmd_clean)
                return ok, speech

        # 4. WhatsApp Background Actions
        is_whatsapp = "whatsapp" in low
        is_general_msg = any(w in low for w in ["text", "msg", "chat", "message"]) and not any(w in low for w in ["discord", "email", "gmail", "github", "linkedin", "slack"])
        if is_whatsapp or is_general_msg:
            if WHATSAPP_WORKER.is_ready():
                if any(w in low for w in ["send", "write", "tell", "say", "saying", "message to", "text to", "msg to", "whatsapp to"]):
                    print("[DIRECT DISPATCHER] Routing to WhatsApp Direct Background Worker (Send)...")
                    ok, speech, _ = WHATSAPP_WORKER.execute_task("send", cmd_clean)
                    return ok, speech
                elif any(w in low for w in ["check", "unread", "read", "new", "get", "status", "any", "receive"]):
                    print("[DIRECT DISPATCHER] Routing to WhatsApp Direct Background Worker (Unread Check)...")
                    ok, speech, _ = WHATSAPP_WORKER.execute_task("unread", cmd_clean)
                    return ok, speech
                elif "whatsapp" in low:
                    print("[DIRECT DISPATCHER] Routing to WhatsApp Direct Background Worker...")
                    ok, speech, _ = WHATSAPP_WORKER.execute_task("auto", cmd_clean)
                    return ok, speech

        # 5. YouTube Background Actions
        if any(w in low for w in ["youtube", "yt"]):
            if YOUTUBE_WORKER.is_ready():
                print("[DIRECT DISPATCHER] Routing to YouTube Direct Background Worker...")
                ok, speech, _ = YOUTUBE_WORKER.execute_task("auto", cmd_clean)
                return ok, speech

        # 6. Canva Background Actions
        if any(w in low for w in ["canva", "canva design", "canva project"]):
            if CANVA_WORKER.is_ready():
                print("[DIRECT DISPATCHER] Routing to Canva Direct Background Worker...")
                ok, speech, _ = CANVA_WORKER.execute_task("auto", cmd_clean)
                return ok, speech

        # 7. Discord Background Actions
        if any(w in low for w in ["discord"]):
            if DISCORD_WORKER.is_ready():
                print("[DIRECT DISPATCHER] Routing to Discord Direct Background Worker...")
                ok, speech, _ = DISCORD_WORKER.execute_task("auto", cmd_clean)
                return ok, speech

        # 8. GitHub Background Actions
        if any(w in low for w in ["github", "pull request", "pr review", "repo status"]):
            if GITHUB_WORKER.is_ready():
                print("[DIRECT DISPATCHER] Routing to GitHub Direct Background Worker...")
                ok, speech, _ = GITHUB_WORKER.execute_task("auto", cmd_clean)
                return ok, speech

        # 9. LinkedIn Background Actions
        if any(w in low for w in ["linkedin"]):
            if LINKEDIN_WORKER.is_ready():
                print("[DIRECT DISPATCHER] Routing to LinkedIn Direct Background Worker...")
                ok, speech, _ = LINKEDIN_WORKER.execute_task("auto", cmd_clean)
                return ok, speech

        return None


DIRECT_DISPATCHER = DirectBackgroundDispatcher()
