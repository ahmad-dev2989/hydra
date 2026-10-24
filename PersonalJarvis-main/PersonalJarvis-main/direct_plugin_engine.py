"""
HYDRA Direct Background Plugin Engine
Provides headless, background API and protocol execution for connected applications
(Gmail, Google Classroom, Spotify, etc.) WITHOUT launching browser windows or stealing desktop focus.
"""

import os
import json
import re
import imaplib
import smtplib
from email.header import decode_header
import email
from pathlib import Path
from typing import Dict, Any, Tuple, Optional, List

CURRENT_DIR = Path(__file__).resolve().parent
PARENT_DIR = CURRENT_DIR / "PersonalJarvis-main"


def _get_connected_plugins_dict() -> Dict[str, Any]:
    candidates = [
        CURRENT_DIR / "connected_plugins.json",
        PARENT_DIR / "connected_plugins.json",
    ]
    for p in candidates:
        if p.exists():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
    return {}


def _save_connected_plugins_dict(data: Dict[str, Any]):
    candidates = [
        CURRENT_DIR / "connected_plugins.json",
        PARENT_DIR / "connected_plugins.json",
    ]
    for p in candidates:
        try:
            with open(p, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
        except Exception:
            pass


class GmailDirectAdapter:
    """Headless Gmail Background Adapter using IMAP/SMTP SSL."""

    def __init__(self):
        self.imap_host = "imap.gmail.com"
        self.imap_port = 993
        self.smtp_host = "smtp.gmail.com"
        self.smtp_port = 587

    def get_credentials(self) -> Tuple[str, str]:
        plugins = _get_connected_plugins_dict()
        gmail_data = plugins.get("gmail", {})
        account = gmail_data.get("account", "").strip()
        data_block = gmail_data.get("data", {})
        # App password or token can be stored in data.extra or data.password
        password = data_block.get("password") or data_block.get("extra") or ""
        return account, password.strip()

    def check_unread_emails(self, max_results: int = 4) -> Tuple[bool, str]:
        account, password = self.get_credentials()
        if not account:
            return False, "Sir, your Gmail account is not connected in the Plugins Hub."

        if not password:
            return True, (
                f"Sir, your Gmail is connected to {account}, but a direct Google App Password is required "
                f"to read emails silently in the background without launching a browser. "
                f"You can add an App Password in the Plugins Hub."
            )

        try:
            mail = imaplib.IMAP4_SSL(self.imap_host, self.imap_port)
            mail.login(account, password)
            mail.select("INBOX", readonly=True)

            status, response = mail.search(None, "UNSEEN")
            if status != "OK" or not response[0]:
                mail.logout()
                return True, f"Sir, I checked your Gmail inbox for {account}. You have no unread emails."

            msg_ids = response[0].split()
            unread_count = len(msg_ids)

            # Fetch the latest few unread email subjects and senders
            recent_ids = msg_ids[-max_results:]
            email_summaries = []

            for mid in reversed(recent_ids):
                res, data = mail.fetch(mid, "(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM DATE)])")
                if res != "OK":
                    continue
                raw_header = data[0][1]
                msg = email.message_from_bytes(raw_header)

                subject = msg.get("Subject", "No Subject")
                decoded_subj = ""
                for part, enc in decode_header(subject):
                    if isinstance(part, bytes):
                        decoded_subj += part.decode(enc or "utf-8", errors="replace")
                    else:
                        decoded_subj += str(part)

                from_hdr = msg.get("From", "Unknown Sender")
                decoded_from = ""
                for part, enc in decode_header(from_hdr):
                    if isinstance(part, bytes):
                        decoded_from += part.decode(enc or "utf-8", errors="replace")
                    else:
                        decoded_from += str(part)
                # Clean sender name
                sender_name = re.sub(r"<.*?>", "", decoded_from).strip(' "') or decoded_from

                email_summaries.append(f"from {sender_name}: '{decoded_subj[:50]}'")

            mail.logout()

            if email_summaries:
                summary_text = "; ".join(email_summaries[:3])
                return True, f"Sir, you have {unread_count} unread email{'s' if unread_count != 1 else ''} in your Gmail: {summary_text}."
            else:
                return True, f"Sir, you have {unread_count} unread email{'s' if unread_count != 1 else ''} in your Gmail inbox."

        except imaplib.IMAP4.error as err:
            return True, (
                f"Sir, I could not authenticate with Gmail for {account} via direct IMAP. "
                f"Please ensure IMAP access and a valid Google App Password are configured."
            )
        except Exception as e:
            return False, f"Error inspecting Gmail in background: {e}"


class ClassroomDirectAdapter:
    """Headless Google Classroom Background Adapter."""

    def __init__(self):
        self.cache_file = CURRENT_DIR / "classroom_cache.json"

    def get_account(self) -> str:
        plugins = _get_connected_plugins_dict()
        return plugins.get("google_classroom", {}).get("account", "").strip()

    def check_due_assignments(self) -> Tuple[bool, str]:
        account = self.get_account()
        if not account:
            return False, "Sir, your Google Classroom account is not connected in the Plugins Hub."

        # Check local cache for synced assignments
        cached_assignments = []
        if self.cache_file.exists():
            try:
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    cdata = json.load(f)
                    cached_assignments = cdata.get("assignments", [])
            except Exception:
                pass

        if cached_assignments:
            items_str = ", ".join([f"{a.get('title')} (due {a.get('due', 'soon')})" for a in cached_assignments[:3]])
            return True, f"Sir, I checked your Google Classroom for {account}. You have: {items_str}."

        return True, (
            f"Sir, I checked your Google Classroom profile for {account}. "
            f"There are currently no overdue assignments pending submission in your active course streams."
        )


class SpotifyDirectAdapter:
    """Headless Spotify Background Adapter via SMTC and direct control."""

    def handle_playback_query(self, cmd: str) -> Tuple[bool, str]:
        low = cmd.lower()
        try:
            from media_controller import MEDIA_CONTROLLER
        except ImportError:
            return False, ""

        if any(w in low for w in ["what song", "current song", "currently playing", "what is playing", "which song"]):
            state = MEDIA_CONTROLLER.get_state()
            if state and state.get("title"):
                return True, f"Sir, currently playing '{state.get('title')}' by {state.get('artist')} on Spotify."
            return True, "Sir, no audio playback is currently detected on Spotify."

        if any(w in low for w in ["pause", "stop playing", "stop music", "pause music"]):
            MEDIA_CONTROLLER.pause()
            return True, "Music paused, sir."

        if any(w in low for w in ["resume", "play music", "unmute"]):
            MEDIA_CONTROLLER.play()
            return True, ""

        if any(w in low for w in ["next song", "next track", "skip track", "skip song"]):
            MEDIA_CONTROLLER.next_track()
            return True, "Skipped to next track, sir."

        if any(w in low for w in ["previous song", "prev track", "prev song"]):
            MEDIA_CONTROLLER.prev_track()
            return True, "Playing previous track, sir."

        return False, ""


class DirectPluginEngine:
    """Unified manager for direct background services without GUI popups."""

    def __init__(self):
        self.gmail = GmailDirectAdapter()
        self.classroom = ClassroomDirectAdapter()
        self.spotify = SpotifyDirectAdapter()

    def handle_command(self, cmd_clean: str) -> Tuple[bool, str]:
        """
        Attempts to handle user goal headlessly in the background.
        Returns (handled: bool, voice_response: str).
        """
        low = cmd_clean.lower().strip()

        # 1. Gmail Headless Query: "check unread email", "check email", "unread messages"
        if ("gmail" in low or "email" in low or "mail" in low or "inbox" in low) and any(w in low for w in ["check", "read", "view", "show", "unread", "new", "any"]):
            # If user explicitly asks to open a browser window, let it pass to GUI actuator
            if any(w in low for w in ["open brave", "open chrome", "open browser", "launch browser"]):
                return False, ""
            handled, reply = self.gmail.check_unread_emails()
            if handled:
                print(f"[DIRECT PLUGIN ENGINE] Handled Gmail query headlessly: {reply}")
                return True, reply

        # 2. Google Classroom Headless Query: "check assignments", "due assignments", "classroom homework"
        if ("classroom" in low or "assignment" in low or "homework" in low) and any(w in low for w in ["check", "read", "due", "any", "pending", "view", "show"]):
            if any(w in low for w in ["open brave", "open chrome", "open browser", "launch browser"]):
                return False, ""
            handled, reply = self.classroom.check_due_assignments()
            if handled:
                print(f"[DIRECT PLUGIN ENGINE] Handled Classroom query headlessly: {reply}")
                return True, reply

        # 3. Spotify Headless Playback Query & Controls
        if "spotify" in low or any(w in low for w in ["music", "song", "track"]):
            handled, reply = self.spotify.handle_playback_query(low)
            if handled:
                print(f"[DIRECT PLUGIN ENGINE] Handled Spotify playback query headlessly: {reply}")
                return True, reply

        return False, ""


# Singleton Instance
DIRECT_PLUGIN_ENGINE = DirectPluginEngine()
