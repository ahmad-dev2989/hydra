"""
HYDRA Social & Productivity Direct Background Workers (Phase 6)
Extends direct background execution capabilities to:
1. YouTube (audio stream search & playback, video metadata, trending)
2. Canva (recent designs, project export status, canvas creation)
3. Discord (headless channel messaging, unread mentions/DMs)
4. GitHub (notifications, PR reviews, unmerged commits, repo status)
5. LinkedIn / X (notifications, background post drafts)
"""

import os
import sys
import re
import time
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Tuple, List, Optional
from .base_connector import BaseDirectWorker
from .vault_manager import VAULT_MGR

CURRENT_DIR = Path(__file__).resolve().parent
ROOT_DIR = CURRENT_DIR.parent
VAULT_DATA_DIR = ROOT_DIR / "data" / "vault"


# =============================================================================
# 1. YouTube Direct Background Worker
# =============================================================================

class YouTubeDirectWorker(BaseDirectWorker):
    app_id = "youtube"
    app_name = "YouTube"

    def is_ready(self) -> bool:
        """Returns True if Google session is active or YouTube plugin is connected."""
        return (
            VAULT_MGR.is_connected(self.app_id)
            or bool(VAULT_MGR.load_google_session())
            or bool(os.environ.get("YOUTUBE_API_KEY"))
        )

    def search_video(self, query: str) -> Tuple[bool, str, Dict[str, Any]]:
        clean_q = query.strip()
        if not clean_q:
            return False, "Please specify a video title or search query for YouTube, sir.", {}

        # 1. Try live scrape of YouTube search results
        try:
            import urllib.request
            import urllib.parse
            enc_q = urllib.parse.quote(clean_q)
            url = f"https://www.youtube.com/results?search_query={enc_q}"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            with urllib.request.urlopen(req, timeout=3.5) as resp:
                html = resp.read().decode("utf-8", errors="ignore")

            m_id = re.search(r'"videoId":"([a-zA-Z0-9_-]{11})"', html)
            m_title = re.search(r'"title":{"runs":\[{"text":"(.*?)"}\]', html)
            m_channel = re.search(r'"ownerText":{"runs":\[{"text":"(.*?)"', html)
            if m_id and m_title:
                v_id = m_id.group(1)
                v_title = m_title.group(1)
                v_channel = m_channel.group(1) if m_channel else "YouTube"
                v_url = f"https://www.youtube.com/watch?v={v_id}"
                speech = f"Sir, I found '{v_title}' by {v_channel} on YouTube."
                return True, speech, {"title": v_title, "channel": v_channel, "url": v_url, "id": v_id}
        except Exception as e:
            print(f"[YOUTUBE LIVE SEARCH NOTICE] {e}")

        # 2. Try YouTube Data API v3 if API key configured
        api_key = os.environ.get("YOUTUBE_API_KEY")
        if api_key:
            try:
                import requests
                resp = requests.get(
                    "https://www.googleapis.com/youtube/v3/search",
                    params={"part": "snippet", "q": clean_q, "type": "video", "maxResults": 1, "key": api_key},
                    timeout=3.5
                )
                if resp.status_code == 200:
                    items = resp.json().get("items", [])
                    if items:
                        item = items[0]
                        v_id = item.get("id", {}).get("videoId")
                        v_title = item.get("snippet", {}).get("title", clean_q)
                        v_channel = item.get("snippet", {}).get("channelTitle", "YouTube")
                        v_url = f"https://www.youtube.com/watch?v={v_id}"
                        speech = f"Sir, I found '{v_title}' by {v_channel} on YouTube."
                        return True, speech, {"title": v_title, "channel": v_channel, "url": v_url, "id": v_id}
            except Exception:
                pass

        return False, f"Sir, I was unable to retrieve live search results for '{clean_q}' on YouTube at this moment.", {}

    def play_audio(self, query: str) -> Tuple[bool, str, Dict[str, Any]]:
        clean_q = query.strip()
        ok, _, item = self.search_video(clean_q)
        v_url = item.get("url") if ok else f"https://www.youtube.com/results?search_query={urllib.parse.quote(clean_q)}"
        v_title = item.get("title", clean_q)

        try:
            import webbrowser
            webbrowser.open(v_url)
            speech = f"Sir, opening '{v_title}' on YouTube in your browser for playback."
            return True, speech, {"action": "open_browser", "url": v_url, "title": v_title}
        except Exception as e:
            return False, f"Could not launch YouTube playback in browser: {e}", {}

    def get_trending(self) -> Tuple[bool, str, List[Dict[str, Any]]]:
        api_key = os.environ.get("YOUTUBE_API_KEY")
        if api_key:
            try:
                import requests
                resp = requests.get(
                    "https://www.googleapis.com/youtube/v3/videos",
                    params={"part": "snippet", "chart": "mostPopular", "regionCode": "US", "maxResults": 3, "key": api_key},
                    timeout=4.0
                )
                if resp.status_code == 200:
                    items = resp.json().get("items", [])
                    if items:
                        parsed = [{"title": it["snippet"]["title"], "channel": it["snippet"]["channelTitle"]} for it in items]
                        speech = f"Sir, the top trending video on YouTube is '{parsed[0]['title']}' by {parsed[0]['channel']}."
                        return True, speech, parsed
            except Exception:
                pass
        return False, "Sir, retrieving live YouTube trending videos requires a YOUTUBE_API_KEY configured in your Plugins Hub.", []

    def execute_task(self, task_type: str, raw_query: str, **kwargs) -> Tuple[bool, str, Dict[str, Any]]:
        low = raw_query.lower()
        if any(w in low for w in ["play", "listen", "stream"]):
            m = re.search(r'\b(?:play|listen to|stream)\s+(.+)', raw_query, re.IGNORECASE)
            q = m.group(1) if m else raw_query
            q = re.sub(r'\s+(?:on|via|from)?\s*youtube\b.*$', '', q, flags=re.IGNORECASE).strip()
            return self.play_audio(q)
        elif any(w in low for w in ["trending", "popular", "top"]):
            ok, speech, items = self.get_trending()
            return ok, speech, {"trending": items}
        else:
            m = re.search(r'\b(?:search|find|lookup)\s+(.+)', raw_query, re.IGNORECASE)
            q = m.group(1) if m else raw_query
            q = re.sub(r'\s+(?:on|via|from)?\s*youtube\b.*$', '', q, flags=re.IGNORECASE).strip()
            ok, speech, data = self.search_video(q)
            return ok, speech, data


# =============================================================================
# 2. Canva Direct Background Worker
# =============================================================================

class CanvaDirectWorker(BaseDirectWorker):
    app_id = "canva"
    app_name = "Canva"

    def is_ready(self) -> bool:
        return VAULT_MGR.is_connected(self.app_id) or (VAULT_DATA_DIR / "canva_session.enc").exists()

    def get_recent_projects(self) -> Tuple[bool, str, List[Dict[str, Any]]]:
        # Truthful report: Canva does not offer unauthenticated headless background scraping
        speech = "Sir, direct background API inspection for Canva is not configured. To view or edit your designs, I can open Canva in your browser."
        return False, speech, []

    def check_export_status(self) -> Tuple[bool, str, Dict[str, Any]]:
        speech = "Sir, direct background export tracking for Canva is not available without active Canva Connect API. Please check your browser downloads."
        return False, speech, {}

    def create_design(self, template_type: str = "Presentation") -> Tuple[bool, str, Dict[str, Any]]:
        clean_type = template_type.strip().title() or "Presentation"
        try:
            import webbrowser
            url = f"https://www.canva.com/create/{clean_type.lower()}/"
            webbrowser.open(url)
            speech = f"Sir, opening Canva in your browser to create a new {clean_type} design."
            return True, speech, {"action": "open_browser", "url": url, "type": clean_type}
        except Exception as e:
            return False, f"Could not launch Canva in browser: {e}", {}

    def execute_task(self, task_type: str, raw_query: str, **kwargs) -> Tuple[bool, str, Dict[str, Any]]:
        low = raw_query.lower()
        if any(w in low for w in ["export", "download", "render"]):
            return self.check_export_status()
        elif any(w in low for w in ["create", "new", "blank", "template", "poster", "deck"]):
            # Extract template type if present
            tmpl = "Presentation"
            if "poster" in low:
                tmpl = "Poster"
            elif "thumbnail" in low:
                tmpl = "YouTube Thumbnail"
            elif "doc" in low or "document" in low:
                tmpl = "Document"
            return self.create_design(tmpl)
        else:
            ok, speech, projs = self.get_recent_projects()
            return ok, speech, {"projects": projs}


# =============================================================================
# 3. Discord Direct Background Worker
# =============================================================================

class DiscordDirectWorker(BaseDirectWorker):
    app_id = "discord"
    app_name = "Discord"

    def is_ready(self) -> bool:
        return (
            VAULT_MGR.is_connected(self.app_id)
            or bool(os.environ.get("DISCORD_TOKEN"))
            or bool(os.environ.get("DISCORD_WEBHOOK_URL"))
            or (VAULT_DATA_DIR / "discord_session.enc").exists()
        )

    def send_message(self, channel: str, message: str) -> Tuple[bool, str, Dict[str, Any]]:
        clean_ch = channel.strip().lstrip("#") or "general"
        clean_msg = message.strip()
        if not clean_msg:
            return False, "No message content was provided to send to Discord, sir.", {}

        webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
        if webhook_url:
            try:
                import requests
                resp = requests.post(webhook_url, json={"content": clean_msg}, timeout=5.0)
                if resp.status_code in [200, 204]:
                    speech = f"Sir, your message has been dispatched to Discord: '{clean_msg}'."
                    return True, speech, {"channel": clean_ch, "message": clean_msg, "status": "SENT"}
                else:
                    return False, f"Sir, Discord webhook returned error status code {resp.status_code}.", {}
            except Exception as e:
                return False, f"Sir, failed to dispatch message to Discord: {e}", {}

        # If no webhook URL or bot token configured
        speech = "Sir, sending Discord messages in the background requires a DISCORD_WEBHOOK_URL or Bot Token configured in your environment or Plugins Hub."
        return False, speech, {}

    def get_unread_mentions(self) -> Tuple[bool, str, List[Dict[str, Any]]]:
        token = os.environ.get("DISCORD_TOKEN")
        if token:
            try:
                import requests
                headers = {"Authorization": f"Bot {token}", "User-Agent": "HYDRA-PersonalJarvis"}
                resp = requests.get("https://discord.com/api/v10/users/@me/mentions", headers=headers, timeout=4.0)
                if resp.status_code == 200:
                    mentions = resp.json()
                    if not mentions:
                        return True, "Sir, you have no unread mentions on Discord.", []
                    speech = f"Sir, you have {len(mentions)} unread mention(s) on Discord."
                    return True, speech, mentions
            except Exception:
                pass
        return False, "Sir, checking Discord mentions requires an active Discord Token configured in your Plugins Hub.", []

    def execute_task(self, task_type: str, raw_query: str, **kwargs) -> Tuple[bool, str, Dict[str, Any]]:
        low = raw_query.lower()
        if any(w in low for w in ["send", "write", "post", "message", "say"]):
            ch_match = re.search(r'\b(?:in|to)\s+#?([a-zA-Z0-9_\-]+)', raw_query, re.IGNORECASE)
            channel = ch_match.group(1) if ch_match else "general"
            msg_match = re.search(r'\b(?:saying|that|with|:|,)\s+(.+)', raw_query, re.IGNORECASE)
            msg = msg_match.group(1) if msg_match else "Hello from HYDRA background engine."
            return self.send_message(channel, msg)
        else:
            ok, speech, items = self.get_unread_mentions()
            return ok, speech, {"mentions": items}


# =============================================================================
# 4. GitHub Direct Background Worker
# =============================================================================

class GitHubDirectWorker(BaseDirectWorker):
    app_id = "github"
    app_name = "GitHub"

    def is_ready(self) -> bool:
        return (
            VAULT_MGR.is_connected(self.app_id)
            or bool(os.environ.get("GITHUB_TOKEN"))
            or (VAULT_DATA_DIR / "github_session.enc").exists()
        )

    def create_repo(self, repo_name: str = "", visibility: str = "", **kwargs) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Creates a new GitHub repository with interactive clarification for missing parameters.
        Asks whether the repository should be Public or Private if not specified.
        """
        try:
            from disambiguation_manager import DISAMBIGUATION_MGR
        except ImportError:
            DISAMBIGUATION_MGR = None

        # 1. Ask for repo name if missing
        clean_name = repo_name.strip()
        if not clean_name and DISAMBIGUATION_MGR:
            ok, name_ans = DISAMBIGUATION_MGR.ask_user(
                "Sir, what name would you like for the new GitHub repository?",
                barrier_type="dynamic"
            )
            clean_name = name_ans.strip() if ok else ""
            clean_name = re.sub(r'[^a-zA-Z0-9_.-]', '-', clean_name).strip('-')

        if not clean_name:
            return False, "Repository creation was cancelled because no name was provided, sir.", {}

        # 2. Ask for visibility if missing (Public vs Private)
        clean_vis = visibility.lower().strip()
        if clean_vis not in ["public", "private"] and DISAMBIGUATION_MGR:
            ok, vis_ans = DISAMBIGUATION_MGR.ask_user(
                f"Sir, would you like the new repository '{clean_name}' to be Public or Private?",
                options=["Public", "Private"],
                barrier_type="dynamic"
            )
            clean_vis = "public" if (ok and "pub" in vis_ans.lower()) else "private"

        is_private = (clean_vis == "private")
        token = os.environ.get("GITHUB_TOKEN") or VAULT_MGR.get_token(self.app_id)

        # 3. Create repository via GitHub REST API if authenticated
        if token:
            try:
                import requests
                headers = {
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github.v3+json",
                    "User-Agent": "HYDRA-PersonalJarvis"
                }
                payload = {
                    "name": clean_name,
                    "private": is_private,
                    "auto_init": True,
                    "description": "Created autonomously by HYDRA System Assistant."
                }
                resp = requests.post("https://api.github.com/user/repos", json=payload, headers=headers, timeout=6.0)
                if resp.status_code in [200, 201]:
                    r_data = resp.json()
                    html_url = r_data.get("html_url", f"https://github.com/user/{clean_name}")
                    speech = f"Sir, your {clean_vis} repository '{clean_name}' is ready on GitHub at {html_url}."
                    return True, speech, {"repo_name": clean_name, "visibility": clean_vis, "url": html_url, "created": True}
                elif resp.status_code == 422:
                    return False, f"Sir, a repository named '{clean_name}' already exists on your GitHub account.", {}
                else:
                    return False, f"Sir, GitHub returned status code {resp.status_code} while creating repository '{clean_name}'.", {}
            except Exception as e:
                print(f"[GITHUB DIRECT API ERROR] {e}")

        # If no token, report TRUTHFULLY - do not fake repository creation
        speech = f"Sir, I cannot create the repository '{clean_name}' on GitHub because no GITHUB_TOKEN is configured in your Plugins Hub."
        return False, speech, {}

    def get_notifications(self) -> Tuple[bool, str, List[Dict[str, Any]]]:
        token = os.environ.get("GITHUB_TOKEN") or VAULT_MGR.get_token(self.app_id)
        if token:
            try:
                import requests
                headers = {
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github.v3+json",
                    "User-Agent": "HYDRA-PersonalJarvis"
                }
                resp = requests.get("https://api.github.com/notifications", headers=headers, timeout=5.0)
                if resp.status_code == 200:
                    notifs = resp.json()
                    if not notifs:
                        return True, "Sir, you have no unread notifications on GitHub.", []
                    count = len(notifs)
                    top_r = notifs[0].get("repository", {}).get("name", "repository")
                    top_subj = notifs[0].get("subject", {}).get("title", "notification")
                    speech = f"Sir, you have {count} unread GitHub notification(s): the newest is on '{top_r}' regarding '{top_subj}'."
                    return True, speech, notifs
                elif resp.status_code in [401, 403]:
                    return False, f"Sir, GitHub returned an authorization error ({resp.status_code}). Please verify your GITHUB_TOKEN.", []
            except Exception as e:
                return False, f"Could not check GitHub notifications: {e}", []

        return False, "Sir, checking GitHub notifications requires a GITHUB_TOKEN configured in your Plugins Hub.", []

    def check_pull_requests(self) -> Tuple[bool, str, List[Dict[str, Any]]]:
        token = os.environ.get("GITHUB_TOKEN") or VAULT_MGR.get_token(self.app_id)
        if token:
            try:
                import requests
                headers = {
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github.v3+json",
                    "User-Agent": "HYDRA-PersonalJarvis"
                }
                resp = requests.get("https://api.github.com/search/issues", headers=headers, params={"q": "is:pr is:open author:@me"}, timeout=5.0)
                if resp.status_code == 200:
                    prs = resp.json().get("items", [])
                    if not prs:
                        return True, "Sir, you have no open pull requests awaiting review on GitHub.", []
                    top = prs[0]
                    speech = f"Sir, you have {len(prs)} open pull request(s) on GitHub: PR #{top.get('number')} '{top.get('title')}'."
                    return True, speech, prs
                elif resp.status_code in [401, 403]:
                    return False, f"Sir, GitHub returned an authorization error ({resp.status_code}). Please verify your GITHUB_TOKEN.", []
            except Exception as e:
                return False, f"Could not check GitHub pull requests: {e}", []

        return False, "Sir, inspecting pull requests requires a GITHUB_TOKEN configured in your Plugins Hub.", []

    def execute_task(self, task_type: str, raw_query: str, **kwargs) -> Tuple[bool, str, Dict[str, Any]]:
        low = raw_query.lower()
        if task_type == "create_repo" or any(w in low for w in ["create repo", "create a repo", "new repo", "create repository"]):
            m_name = re.search(r"(?:named|name|called)\s+([a-zA-Z0-9_.-]+)", raw_query, re.IGNORECASE)
            r_name = m_name.group(1).strip() if m_name else kwargs.get("repo_name", "")

            vis = ""
            if "public" in low:
                vis = "public"
            elif "private" in low:
                vis = "private"

            return self.create_repo(repo_name=r_name, visibility=vis)
        elif any(w in low for w in ["pr", "pull request", "review", "merge"]):
            ok, speech, items = self.check_pull_requests()
            return ok, speech, {"pull_requests": items}
        else:
            ok, speech, items = self.get_notifications()
            return ok, speech, {"notifications": items}


# =============================================================================
# 5. LinkedIn Direct Background Worker
# =============================================================================

class LinkedInDirectWorker(BaseDirectWorker):
    app_id = "linkedin"
    app_name = "LinkedIn"

    def is_ready(self) -> bool:
        return VAULT_MGR.is_connected(self.app_id) or (VAULT_DATA_DIR / "linkedin_session.enc").exists()

    def get_notifications(self) -> Tuple[bool, str, List[Dict[str, Any]]]:
        speech = "Sir, direct background access for LinkedIn is not configured. To view notifications, please allow me to open LinkedIn in your browser."
        return False, speech, []

    def draft_post(self, content: str) -> Tuple[bool, str, Dict[str, Any]]:
        speech = "Sir, direct background posting to LinkedIn requires an authorized LinkedIn API integration. I can open LinkedIn in your browser to draft this post."
        return False, speech, {}

    def execute_task(self, task_type: str, raw_query: str, **kwargs) -> Tuple[bool, str, Dict[str, Any]]:
        low = raw_query.lower()
        if any(w in low for w in ["draft", "post", "write", "publish", "share"]):
            m = re.search(r'\b(?:draft|post|write|share)\s+(.+)', raw_query, re.IGNORECASE)
            content = m.group(1) if m else raw_query
            return self.draft_post(content)
        else:
            ok, speech, items = self.get_notifications()
            return ok, speech, {"notifications": items}


# Singleton worker instances
YOUTUBE_WORKER = YouTubeDirectWorker()
CANVA_WORKER = CanvaDirectWorker()
DISCORD_WORKER = DiscordDirectWorker()
GITHUB_WORKER = GitHubDirectWorker()
LINKEDIN_WORKER = LinkedInDirectWorker()
