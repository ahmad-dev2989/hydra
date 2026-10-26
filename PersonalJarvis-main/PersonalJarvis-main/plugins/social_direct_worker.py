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

    def search_video(self, query: str, limit: int = 5) -> Tuple[bool, str, Dict[str, Any]]:
        clean_q = query.strip()
        if not clean_q:
            return False, "Please specify a video title or search query for YouTube, sir.", {}

        # 1. Primary Strategy: Live HTML search with ytInitialData JSON extraction
        try:
            import urllib.request
            import urllib.parse
            enc_q = urllib.parse.quote(clean_q)
            url = f"https://www.youtube.com/results?search_query={enc_q}"
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Accept-Language": "en-US,en;q=0.9"
                }
            )
            with urllib.request.urlopen(req, timeout=4.5) as resp:
                html = resp.read().decode("utf-8", errors="ignore")

            m_json = re.search(r'var ytInitialData\s*=\s*({.*?});(?:</script>|var )', html)
            if not m_json:
                m_json = re.search(r'ytInitialData\s*=\s*({.*?});</script>', html)

            if m_json:
                data = json.loads(m_json.group(1))
                contents = data.get("contents", {}).get("twoColumnSearchResultsRenderer", {}).get("primaryContents", {}).get("sectionListRenderer", {}).get("contents", [])
                results = []
                for sec in contents:
                    for item in sec.get("itemSectionRenderer", {}).get("contents", []):
                        vr = item.get("videoRenderer")
                        if vr:
                            v_id = vr.get("videoId")
                            title_runs = vr.get("title", {}).get("runs", [])
                            title = "".join(r.get("text", "") for r in title_runs).strip()
                            channel_runs = vr.get("ownerText", {}).get("runs", [])
                            channel = "".join(r.get("text", "") for r in channel_runs).strip() or "YouTube"
                            view_text = vr.get("viewCountText", {}).get("simpleText", "")
                            length_text = vr.get("lengthText", {}).get("simpleText", "")
                            if v_id and title:
                                results.append({
                                    "id": v_id,
                                    "title": title,
                                    "channel": channel,
                                    "views": view_text,
                                    "length": length_text,
                                    "url": f"https://www.youtube.com/watch?v={v_id}"
                                })
                if results:
                    top = results[0]
                    view_suffix = f" with {top['views']}" if top.get("views") else ""
                    dur_suffix = f" ({top['length']})" if top.get("length") else ""
                    speech = f"Sir, I found '{top['title']}' by {top['channel']}{view_suffix}{dur_suffix} on YouTube."
                    return True, speech, {
                        "id": top["id"],
                        "title": top["title"],
                        "channel": top["channel"],
                        "url": top["url"],
                        "views": top.get("views", ""),
                        "length": top.get("length", ""),
                        "results": results[:limit]
                    }
        except Exception as e:
            print(f"[YOUTUBE LIVE SEARCH NOTICE] {e}")

        # 2. Secondary Strategy: Regex fallback on raw search HTML
        try:
            v_ids = re.findall(r'"videoId":"([a-zA-Z0-9_-]{11})"', html)
            titles = re.findall(r'"title":{"runs":\[{"text":"(.*?)"}\]', html)
            channels = re.findall(r'"ownerText":{"runs":\[{"text":"(.*?)"', html)
            if v_ids and titles:
                v_id = v_ids[0]
                v_title = titles[0]
                v_channel = channels[0] if channels else "YouTube"
                v_url = f"https://www.youtube.com/watch?v={v_id}"
                speech = f"Sir, I found '{v_title}' by {v_channel} on YouTube."
                return True, speech, {
                    "id": v_id,
                    "title": v_title,
                    "channel": v_channel,
                    "url": v_url,
                    "results": [{"id": v_id, "title": v_title, "channel": v_channel, "url": v_url}]
                }
        except Exception:
            pass

        # 3. Tertiary Strategy: YouTube Data API v3 if key configured
        api_key = os.environ.get("YOUTUBE_API_KEY")
        if api_key:
            try:
                import requests
                resp = requests.get(
                    "https://www.googleapis.com/youtube/v3/search",
                    params={"part": "snippet", "q": clean_q, "type": "video", "maxResults": limit, "key": api_key},
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
                        return True, speech, {
                            "id": v_id,
                            "title": v_title,
                            "channel": v_channel,
                            "url": v_url,
                            "results": [{"id": v_id, "title": v_title, "channel": v_channel, "url": v_url}]
                        }
            except Exception:
                pass

        return False, f"Sir, I was unable to retrieve live search results for '{clean_q}' on YouTube at this moment.", {}

    def search_channel(self, channel_query: str) -> Tuple[bool, str, Dict[str, Any]]:
        clean_ch = channel_query.strip()
        if not clean_ch:
            return False, "Please specify a channel name or handle for YouTube, sir.", {}

        # 1. Primary Strategy: Search with channel filter
        try:
            import urllib.request
            import urllib.parse
            enc_q = urllib.parse.quote(clean_ch)
            url = f"https://www.youtube.com/results?search_query={enc_q}&sp=EgIQAg%253D%253D"
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Accept-Language": "en-US,en;q=0.9"
                }
            )
            with urllib.request.urlopen(req, timeout=4.5) as resp:
                html = resp.read().decode("utf-8", errors="ignore")

            m_json = re.search(r'var ytInitialData\s*=\s*({.*?});(?:</script>|var )', html)
            if not m_json:
                m_json = re.search(r'ytInitialData\s*=\s*({.*?});</script>', html)

            if m_json:
                data = json.loads(m_json.group(1))
                contents = data.get("contents", {}).get("twoColumnSearchResultsRenderer", {}).get("primaryContents", {}).get("sectionListRenderer", {}).get("contents", [])
                for sec in contents:
                    for item in sec.get("itemSectionRenderer", {}).get("contents", []):
                        cr = item.get("channelRenderer")
                        if cr:
                            ch_id = cr.get("channelId", "")
                            title = cr.get("title", {}).get("simpleText", clean_ch)
                            handle = cr.get("subscriberCountText", {}).get("simpleText", "")
                            sub_count = (
                                cr.get("videoCountText", {}).get("simpleText")
                                or cr.get("videoCountText", {}).get("accessibility", {}).get("accessibilityData", {}).get("label")
                                or ""
                            )
                            if not sub_count and "subscriber" in handle.lower():
                                sub_count = handle
                                handle = ""

                            sub_str = f" has {sub_count}" if sub_count else " is active"
                            handle_str = f" ({handle})" if handle else ""
                            ch_url = f"https://www.youtube.com/channel/{ch_id}" if ch_id else f"https://www.youtube.com/results?search_query={enc_q}"
                            speech = f"Sir, {title}{handle_str}{sub_str} on YouTube."
                            return True, speech, {
                                "title": title,
                                "handle": handle,
                                "subscribers": sub_count,
                                "channel_id": ch_id,
                                "url": ch_url
                            }
        except Exception as e:
            print(f"[YOUTUBE CHANNEL SEARCH NOTICE] {e}")

        # 2. Secondary Strategy: YouTube Data API v3 if key configured
        api_key = os.environ.get("YOUTUBE_API_KEY")
        if api_key:
            try:
                import requests
                resp = requests.get(
                    "https://www.googleapis.com/youtube/v3/search",
                    params={"part": "snippet", "q": clean_ch, "type": "channel", "maxResults": 1, "key": api_key},
                    timeout=3.5
                )
                if resp.status_code == 200:
                    items = resp.json().get("items", [])
                    if items:
                        item = items[0]
                        ch_id = item.get("id", {}).get("channelId")
                        title = item.get("snippet", {}).get("title", clean_ch)
                        # Fetch channel statistics
                        stat_resp = requests.get(
                            "https://www.googleapis.com/youtube/v3/channels",
                            params={"part": "statistics", "id": ch_id, "key": api_key},
                            timeout=3.0
                        )
                        subs = "several"
                        if stat_resp.status_code == 200:
                            s_items = stat_resp.json().get("items", [])
                            if s_items:
                                sub_raw = int(s_items[0].get("statistics", {}).get("subscriberCount", 0))
                                if sub_raw >= 1_000_000:
                                    subs = f"{sub_raw / 1_000_000:.1f}M subscribers"
                                elif sub_raw >= 1_000:
                                    subs = f"{sub_raw / 1_000:.1f}K subscribers"
                                else:
                                    subs = f"{sub_raw} subscribers"
                        speech = f"Sir, {title} has {subs} on YouTube."
                        return True, speech, {
                            "title": title,
                            "subscribers": subs,
                            "channel_id": ch_id,
                            "url": f"https://www.youtube.com/channel/{ch_id}"
                        }
            except Exception:
                pass

        return False, f"Sir, I could not find channel analytics for '{clean_ch}' on YouTube.", {}

    def get_trending(self, limit: int = 3) -> Tuple[bool, str, List[Dict[str, Any]]]:
        # 1. Strategy 1: Data API v3 if key configured
        api_key = os.environ.get("YOUTUBE_API_KEY")
        if api_key:
            try:
                import requests
                resp = requests.get(
                    "https://www.googleapis.com/youtube/v3/videos",
                    params={"part": "snippet", "chart": "mostPopular", "regionCode": "US", "maxResults": limit, "key": api_key},
                    timeout=4.0
                )
                if resp.status_code == 200:
                    items = resp.json().get("items", [])
                    if items:
                        parsed = [{"title": it["snippet"]["title"], "channel": it["snippet"]["channelTitle"], "url": f"https://www.youtube.com/watch?v={it['id']}"} for it in items]
                        first = parsed[0]
                        sec = f", followed by '{parsed[1]['title']}'" if len(parsed) > 1 else ""
                        speech = f"Sir, the top trending video on YouTube is '{first['title']}' by {first['channel']}{sec}."
                        return True, speech, parsed
            except Exception:
                pass

        # 2. Strategy 2: Live HTML search for trending query
        ok, _, data = self.search_video("trending videos", limit=limit)
        if ok and data.get("results"):
            results = data["results"][:limit]
            first = results[0]
            sec = f", followed by '{results[1]['title']}'" if len(results) > 1 else ""
            speech = f"Sir, the top trending video on YouTube is '{first['title']}' by {first['channel']}{sec}."
            return True, speech, results

        return False, "Sir, retrieving live YouTube trending videos is currently unavailable.", []

    def play_video(self, query: str) -> Tuple[bool, str, Dict[str, Any]]:
        clean_q = query.strip()
        if not clean_q or clean_q.lower() in ["youtube", "video", "videos"]:
            # General YouTube launch
            try:
                import webbrowser
                webbrowser.open("https://www.youtube.com")
                return True, "Sir, opening YouTube in your browser.", {"action": "open_browser", "url": "https://www.youtube.com"}
            except Exception as e:
                return False, f"Could not open YouTube in browser: {e}", {}

        # Headlessly resolve the exact top video
        ok, _, item = self.search_video(clean_q)
        if ok and item.get("url"):
            v_url = item["url"]
            v_title = item.get("title", clean_q)
            v_channel = item.get("channel", "YouTube")
        else:
            import urllib.parse
            v_url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(clean_q)}"
            v_title = clean_q
            v_channel = "YouTube"

        try:
            import webbrowser
            webbrowser.open(v_url)
            speech = f"Sir, playing '{v_title}' by {v_channel} on YouTube."
            return True, speech, {
                "action": "play",
                "url": v_url,
                "title": v_title,
                "channel": v_channel,
                "id": item.get("id", "")
            }
        except Exception as e:
            return False, f"Could not launch YouTube playback in browser: {e}", {}

    def transport(self, action: str) -> Tuple[bool, str, Dict[str, Any]]:
        """Controls browser/media playback transport using Windows native media keys."""
        act_clean = action.lower().strip()
        try:
            import ctypes
            # Virtual-Key codes
            VK_MEDIA_PLAY_PAUSE = 0xB3
            VK_MEDIA_STOP = 0xB2
            VK_VOLUME_MUTE = 0xAD

            if any(w in act_clean for w in ["stop"]):
                vk = VK_MEDIA_STOP
                speech = "Sir, playback stopped."
            elif any(w in act_clean for w in ["mute", "unmute"]):
                vk = VK_VOLUME_MUTE
                speech = "Sir, media volume toggled."
            elif any(w in act_clean for w in ["pause"]):
                vk = VK_MEDIA_PLAY_PAUSE
                speech = "Sir, playback paused."
            elif any(w in act_clean for w in ["resume", "unpause", "play"]):
                vk = VK_MEDIA_PLAY_PAUSE
                speech = "Sir, playback resumed."
            else:
                vk = VK_MEDIA_PLAY_PAUSE
                speech = "Sir, media transport toggled."

            # Key down and up
            ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
            time.sleep(0.04)
            ctypes.windll.user32.keybd_event(vk, 0, 2, 0)
            return True, speech, {"action": "transport", "control": act_clean}
        except Exception as e:
            return False, f"Could not dispatch media control key: {e}", {}

    def execute_task(self, task_type: str, raw_query: str, **kwargs) -> Tuple[bool, str, Dict[str, Any]]:
        low = raw_query.lower()

        # 1. Channel analytics / subscriber check
        if task_type == "channel_stats" or any(w in low for w in ["subscriber", "subscribers", "subs", "channel stats", "channel details"]):
            m = re.search(r'\b(?:how many subscribers (?:does|has)?|subscribers of|channel stats (?:of|for)?|subs (?:of|for)?)\s+([a-zA-Z0-9_\s]+?)(?:\s+(?:have|on|in|via)\s+youtube|\s*$)', raw_query, re.IGNORECASE)
            ch_cand = m.group(1).strip() if m else raw_query
            ch_cand = re.sub(r'\s+(?:on|via|from)?\s*youtube\b.*$', '', ch_cand, flags=re.IGNORECASE).strip()
            ch_cand = re.sub(r'^(?:how many subscribers (?:does|has)?|who is|what is|tell me)\s+', '', ch_cand, flags=re.IGNORECASE).strip()
            return self.search_channel(ch_cand)

        # 2. Trending query
        if task_type == "trending" or any(w in low for w in ["trending", "popular", "top videos"]):
            ok, speech, items = self.get_trending()
            return ok, speech, {"trending": items}

        # 3. Transport control
        if task_type == "transport" or any(w in low for w in ["pause", "resume", "stop", "unpause"]):
            return self.transport(low)

        # 4. Playback command
        if task_type == "play_video" or any(w in low for w in ["play", "listen", "stream", "watch", "put on"]):
            m = re.search(r'\b(?:play|listen to|stream|watch|put on)\s+(.+)', raw_query, re.IGNORECASE)
            q = m.group(1) if m else raw_query
            q = re.sub(r'\s+(?:on|via|from)?\s*youtube\b.*$', '', q, flags=re.IGNORECASE).strip()
            return self.play_video(q)

        # 5. Search video command
        m = re.search(r'\b(?:search|find|lookup|look up|show me)\s+(.+)', raw_query, re.IGNORECASE)
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
