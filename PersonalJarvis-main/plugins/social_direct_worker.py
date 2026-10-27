"""
HYDRA Social & Productivity Direct Background Workers (Phase 6)
Extends direct background execution capabilities to:
1. YouTube (audio stream search & playback, video metadata, trending)
2. GitHub (notifications, PR reviews, repo status, repository creation)
3. LinkedIn (post drafting with pre-filled composer, notifications, messaging, network)
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

    def get_trending(self, topic: str = "", limit: int = 3) -> Tuple[bool, str, List[Dict[str, Any]]]:
        clean_topic = topic.strip()
        if clean_topic.lower() in ["youtube", "yt", "videos", "vedios", "video", "vedio"]:
            clean_topic = ""

        if clean_topic:
            # Topic-specific trending search (e.g., "trending machine learning")
            ok, _, data = self.search_video(f"trending {clean_topic}", limit=limit)
            if ok and data.get("results"):
                results = data["results"][:limit]
                first = results[0]
                t1 = re.sub(r'[\U00010000-\U0010ffff]', '', first['title']).strip()
                view_str = f" with {first['views']}" if first.get("views") else ""
                sec = ""
                if len(results) > 1:
                    t2 = re.sub(r'[\U00010000-\U0010ffff]', '', results[1]['title']).strip()
                    sec = f", followed by '{t2}'"
                speech = f"Sir, the top trending {clean_topic} video on YouTube is '{t1}' by {first['channel']}{view_str}{sec}."
                return True, speech, results
            elif ok and data.get("title"):
                first = data
                t1 = re.sub(r'[\U00010000-\U0010ffff]', '', first['title']).strip()
                speech = f"Sir, I found trending {clean_topic} video '{t1}' by {first.get('channel', 'YouTube')} on YouTube."
                return True, speech, [first]

        # General YouTube Trending
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
                        parsed = [{"title": re.sub(r'[\U00010000-\U0010ffff]', '', it["snippet"]["title"]).strip(), "channel": it["snippet"]["channelTitle"], "url": f"https://www.youtube.com/watch?v={it['id']}"} for it in items]
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
            t1 = re.sub(r'[\U00010000-\U0010ffff]', '', first['title']).strip()
            sec = ""
            if len(results) > 1:
                t2 = re.sub(r'[\U00010000-\U0010ffff]', '', results[1]['title']).strip()
                sec = f", followed by '{t2}'"
            speech = f"Sir, the top trending video on YouTube is '{t1}' by {first['channel']}{sec}."
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
            topic = kwargs.get("topic") or ""
            if not topic:
                m_top = re.search(r'\btrending\s+(?:videos?|vedios?|clips?)?\s*(?:on|about|in|for)?\s*(.+)', raw_query, re.IGNORECASE)
                if m_top:
                    topic = m_top.group(1).strip()
            if topic:
                topic = re.sub(r'\b(?:on|via|from)?\s*youtube\b.*$', '', topic, flags=re.IGNORECASE).strip()
            if topic.lower() in ["", "youtube", "yt", "videos", "vedios", "video", "vedio"]:
                topic = ""
            ok, speech, items = self.get_trending(topic=topic)
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
# 2. GitHub Direct Background Worker
# =============================================================================

class GitHubDirectWorker(BaseDirectWorker):
    app_id = "github"
    app_name = "GitHub"

    def is_ready(self) -> bool:
        return (
            VAULT_MGR.is_connected(self.app_id)
            or bool(os.environ.get("GITHUB_TOKEN"))
            or bool(os.environ.get("GITHUB_PAT"))
            or (VAULT_DATA_DIR / "github_session.enc").exists()
        )

    def _get_auth_headers(self) -> Dict[str, str]:
        token = (
            os.environ.get("GITHUB_TOKEN")
            or os.environ.get("GITHUB_PAT")
            or VAULT_MGR.get_token(self.app_id)
        )
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "HYDRA-PersonalJarvis"
        }
        if token:
            headers["Authorization"] = f"Bearer {token.strip()}"
        return headers

    def _get_active_account(self) -> str:
        acc = VAULT_MGR.get_account_email(self.app_id)
        if acc:
            return acc.strip()
        try:
            with open(ROOT_DIR / "connected_plugins.json", "r", encoding="utf-8") as f:
                d = json.load(f)
                gh = d.get("github", {})
                if gh.get("account"):
                    return gh["account"].strip()
        except Exception:
            pass
        return "ahmad-dev2989"

    def get_repositories(self, limit: int = 5) -> Tuple[bool, str, List[Dict[str, Any]]]:
        account = self._get_active_account()
        headers = self._get_auth_headers()
        url = f"https://api.github.com/users/{account}/repos?sort=updated&per_page={limit}"

        try:
            import urllib.request
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                repos = json.loads(resp.read().decode("utf-8"))

            if not repos:
                return True, f"Sir, no public repositories were found under GitHub account '{account}'.", []

            summary_items = []
            for r in repos:
                summary_items.append({
                    "name": r.get("name"),
                    "stars": r.get("stargazers_count", 0),
                    "forks": r.get("forks_count", 0),
                    "open_issues": r.get("open_issues_count", 0),
                    "url": r.get("html_url"),
                    "updated_at": r.get("updated_at")
                })

            top = summary_items[0]
            sec = f", followed by '{summary_items[1]['name']}'" if len(summary_items) > 1 else ""
            speech = f"Sir, you have {len(summary_items)}+ repositories on GitHub under '{account}'. Your most recently updated repository is '{top['name']}' with {top['stars']} stars{sec}."
            return True, speech, summary_items
        except Exception as e:
            print(f"[GITHUB REPOS ERROR] {e}")
            return False, f"Sir, could not retrieve GitHub repositories for '{account}': {e}", []

    def get_repo_status(self, repo_name: str) -> Tuple[bool, str, Dict[str, Any]]:
        account = self._get_active_account()
        clean_repo = repo_name.strip().strip("/").split("/")[-1]
        headers = self._get_auth_headers()

        # Try user's repo first
        url = f"https://api.github.com/repos/{account}/{clean_repo}"
        try:
            import urllib.request
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            name = data.get("name", clean_repo)
            stars = data.get("stargazers_count", 0)
            forks = data.get("forks_count", 0)
            issues = data.get("open_issues_count", 0)
            branch = data.get("default_branch", "main")
            updated = data.get("updated_at", "")[:10]
            html_url = data.get("html_url", f"https://github.com/{account}/{clean_repo}")

            speech = f"Sir, the '{name}' repository on GitHub has {issues} open issues, {stars} stars, {forks} forks, and default branch '{branch}', last updated on {updated}."
            return True, speech, {
                "name": name,
                "stars": stars,
                "forks": forks,
                "open_issues": issues,
                "default_branch": branch,
                "updated_at": updated,
                "url": html_url
            }
        except Exception as e:
            print(f"[GITHUB REPO STATUS NOTICE] {e}")
            # Try search across GitHub if not found under personal account
            try:
                import urllib.parse
                s_url = f"https://api.github.com/search/repositories?q={urllib.parse.quote(clean_repo)}&per_page=1"
                req = urllib.request.Request(s_url, headers=headers)
                with urllib.request.urlopen(req, timeout=5.0) as resp:
                    s_data = json.loads(resp.read().decode("utf-8"))
                    items = s_data.get("items", [])
                    if items:
                        r = items[0]
                        full_name = r.get("full_name")
                        stars = r.get("stargazers_count", 0)
                        issues = r.get("open_issues_count", 0)
                        html_url = r.get("html_url")
                        speech = f"Sir, found repository '{full_name}' on GitHub with {stars} stars and {issues} open issues at {html_url}."
                        return True, speech, {"name": full_name, "stars": stars, "open_issues": issues, "url": html_url}
            except Exception:
                pass

            return False, f"Sir, could not locate repository '{clean_repo}' on GitHub.", {}

    def get_notifications(self) -> Tuple[bool, str, List[Dict[str, Any]]]:
        headers = self._get_auth_headers()
        account = self._get_active_account()

        if "Authorization" in headers:
            try:
                import urllib.request
                req = urllib.request.Request("https://api.github.com/notifications", headers=headers)
                with urllib.request.urlopen(req, timeout=5.0) as resp:
                    notifs = json.loads(resp.read().decode("utf-8"))

                if not notifs:
                    return True, "Sir, you have no unread notifications on GitHub.", []

                count = len(notifs)
                top_r = notifs[0].get("repository", {}).get("name", "repository")
                top_subj = notifs[0].get("subject", {}).get("title", "notification")
                speech = f"Sir, you have {count} unread GitHub notification(s): the newest is on '{top_r}' regarding '{top_subj}'."
                return True, speech, notifs
            except Exception as e:
                print(f"[GITHUB NOTIFICATIONS API NOTICE] {e}")

        # If no personal token configured, provide intelligent public repo updates check
        ok, _, repos = self.get_repositories(limit=3)
        if ok and repos:
            top = repos[0]
            speech = f"Sir, direct private notification inspection requires a GITHUB_TOKEN in your Plugins Hub. Your public account '{account}' has active repository '{top['name']}' with {top['open_issues']} open issues."
            return True, speech, repos

        return False, f"Sir, checking GitHub notifications requires an authenticated GITHUB_TOKEN configured in your Plugins Hub.", []

    def check_pull_requests(self) -> Tuple[bool, str, List[Dict[str, Any]]]:
        headers = self._get_auth_headers()
        account = self._get_active_account()

        q = "is:pr is:open author:@me" if "Authorization" in headers else f"is:pr is:open author:{account}"
        try:
            import urllib.request
            import urllib.parse
            url = f"https://api.github.com/search/issues?q={urllib.parse.quote(q)}"
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                prs = json.loads(resp.read().decode("utf-8")).get("items", [])

            if not prs:
                return True, f"Sir, you have no open pull requests on GitHub for '{account}'.", []

            top = prs[0]
            num = top.get("number")
            title = top.get("title")
            speech = f"Sir, you have {len(prs)} open pull request(s) on GitHub: PR #{num} '{title}'."
            return True, speech, prs
        except Exception as e:
            print(f"[GITHUB PR SEARCH ERROR] {e}")
            return False, f"Sir, could not check pull requests on GitHub: {e}", []

    def create_repo(self, repo_name: str = "", visibility: str = "", **kwargs) -> Tuple[bool, str, Dict[str, Any]]:
        clean_name = repo_name.strip()
        if not clean_name:
            return False, "Please specify a repository name to create on GitHub, sir.", {}

        clean_vis = visibility.lower().strip()
        is_private = (clean_vis == "private")
        headers = self._get_auth_headers()

        if "Authorization" in headers:
            try:
                import requests
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
                    speech = f"Sir, your {clean_vis or 'public'} repository '{clean_name}' is ready on GitHub at {html_url}."
                    return True, speech, {"repo_name": clean_name, "visibility": clean_vis, "url": html_url, "created": True}
                elif resp.status_code == 422:
                    return False, f"Sir, a repository named '{clean_name}' already exists on your GitHub account.", {}
            except Exception as e:
                print(f"[GITHUB CREATE REPO API ERROR] {e}")

        # If no write token configured, truthful report + browser fallback
        account = self._get_active_account()
        speech = f"Sir, creating repository '{clean_name}' headlessly requires a GITHUB_TOKEN with repo scope in your Plugins Hub. Opening GitHub in your browser to create it."
        try:
            import webbrowser
            webbrowser.open(f"https://github.com/new?name={clean_name}")
        except Exception:
            pass
        return True, speech, {"action": "open_browser", "url": f"https://github.com/new?name={clean_name}"}

    def execute_task(self, task_type: str, raw_query: str, **kwargs) -> Tuple[bool, str, Dict[str, Any]]:
        low = raw_query.lower()

        # 1. Create repo
        if task_type == "create_repo" or any(w in low for w in ["create repo", "create a repo", "new repo", "create repository"]):
            m_name = re.search(r"(?:named|name|called)\s+([a-zA-Z0-9_.-]+)", raw_query, re.IGNORECASE)
            r_name = m_name.group(1).strip() if m_name else kwargs.get("repo_name", "")
            vis = "private" if "private" in low else ("public" if "public" in low else "")
            return self.create_repo(repo_name=r_name, visibility=vis)

        # 2. Check PRs
        if task_type == "check_prs" or any(w in low for w in ["pr", "pull request", "review", "merge"]):
            ok, speech, items = self.check_pull_requests()
            return ok, speech, {"pull_requests": items}

        # 3. Status of specific repository
        if task_type == "repo_status" or any(w in low for w in ["status of", "details of", "stars of", "how many stars", "issues on", "issues in", "commits on"]):
            m_repo = re.search(r'\b(?:status of|details of|stars of|issues on|issues in|commits on|repo|repository)\s+([a-zA-Z0-9_.-]+)', raw_query, re.IGNORECASE)
            repo_cand = m_repo.group(1).strip() if m_repo else "hydra"
            if repo_cand.lower() in ["github", "my", "the", "a"]:
                repo_cand = "hydra"
            return self.get_repo_status(repo_cand)

        # 4. List repositories
        if any(w in low for w in ["list repos", "my repos", "my repositories", "what repos", "which repos"]):
            ok, speech, items = self.get_repositories()
            return ok, speech, {"repositories": items}

        # 5. Default: notifications
        ok, speech, items = self.get_notifications()
        return ok, speech, {"notifications": items}


# =============================================================================
# 3. LinkedIn Direct Background Worker
# =============================================================================

class LinkedInDirectWorker(BaseDirectWorker):
    app_id = "linkedin"
    app_name = "LinkedIn"

    def is_ready(self) -> bool:
        return (
            VAULT_MGR.is_connected(self.app_id)
            or bool(os.environ.get("LINKEDIN_ACCESS_TOKEN"))
            or (VAULT_DATA_DIR / "linkedin_session.enc").exists()
        )

    def _get_active_account(self) -> str:
        acc = VAULT_MGR.get_account_email(self.app_id)
        if acc:
            return acc.strip()
        try:
            with open(ROOT_DIR / "connected_plugins.json", "r", encoding="utf-8") as f:
                d = json.load(f)
                li = d.get("linkedin", {})
                if li.get("account"):
                    return li["account"].strip()
        except Exception:
            pass
        return "ahmad-pro"

    def draft_post(self, content: str) -> Tuple[bool, str, Dict[str, Any]]:
        clean_content = content.strip()
        if not clean_content:
            return False, "Please specify the content you would like to share on LinkedIn, sir.", {}

        # 1. Try LinkedIn API if token configured with w_member_social scope
        token = os.environ.get("LINKEDIN_ACCESS_TOKEN") or VAULT_MGR.get_token(self.app_id)
        if token:
            try:
                import requests
                headers = {
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "X-Restli-Protocol-Version": "2.0.0"
                }
                # Check user profile ID first
                me_resp = requests.get("https://api.linkedin.com/v2/userinfo", headers=headers, timeout=4.0)
                if me_resp.status_code == 200:
                    author_urn = f"urn:li:person:{me_resp.json().get('sub')}"
                    payload = {
                        "author": author_urn,
                        "lifecycleState": "PUBLISHED",
                        "specificContent": {
                            "com.linkedin.ugc.ShareContent": {
                                "shareCommentary": {"text": clean_content},
                                "shareMediaCategory": "NONE"
                            }
                        },
                        "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"}
                    }
                    post_resp = requests.post("https://api.linkedin.com/v2/ugcPosts", json=payload, headers=headers, timeout=6.0)
                    if post_resp.status_code in [200, 201]:
                        speech = f"Sir, your post has been published to LinkedIn: '{clean_content[:60]}...'."
                        return True, speech, {"action": "post", "status": "PUBLISHED", "content": clean_content}
            except Exception as e:
                print(f"[LINKEDIN API POST ERROR] {e}")

        # 2. Transparent zero-friction browser handoff with pre-filled composer
        import urllib.parse
        enc_text = urllib.parse.quote(clean_content)
        url = f"https://www.linkedin.com/feed/?shareActive=true&text={enc_text}"
        try:
            import webbrowser
            webbrowser.open(url)
            speech = f"Sir, opening LinkedIn in your browser with your post draft pre-filled: '{clean_content[:60]}...'."
            return True, speech, {"action": "open_browser", "url": url, "content": clean_content}
        except Exception as e:
            return False, f"Could not launch LinkedIn composer: {e}", {}

    def get_notifications(self) -> Tuple[bool, str, List[Dict[str, Any]]]:
        account = self._get_active_account()
        # LinkedIn restricts consumer notification reading without specialized Partner API
        speech = f"Sir, reading private notifications headlessly requires an authorized LinkedIn Partner API token. Opening your LinkedIn notifications in your browser for account '{account}'."
        try:
            import webbrowser
            webbrowser.open("https://www.linkedin.com/notifications/")
        except Exception:
            pass
        return True, speech, []

    def check_messages(self) -> Tuple[bool, str, Dict[str, Any]]:
        account = self._get_active_account()
        speech = f"Sir, opening your LinkedIn messaging inbox in your browser for account '{account}'."
        try:
            import webbrowser
            webbrowser.open("https://www.linkedin.com/messaging/")
        except Exception:
            pass
        return True, speech, {"action": "open_browser", "url": "https://www.linkedin.com/messaging/"}

    def view_network(self) -> Tuple[bool, str, Dict[str, Any]]:
        account = self._get_active_account()
        speech = f"Sir, opening your LinkedIn network invitations in your browser for account '{account}'."
        try:
            import webbrowser
            webbrowser.open("https://www.linkedin.com/mynetwork/")
        except Exception:
            pass
        return True, speech, {"action": "open_browser", "url": "https://www.linkedin.com/mynetwork/"}

    def execute_task(self, task_type: str, raw_query: str, **kwargs) -> Tuple[bool, str, Dict[str, Any]]:
        low = raw_query.lower()
        if kwargs.get("content"):
            return self.draft_post(kwargs["content"])

        if any(w in low for w in ["draft", "post", "write", "publish", "share"]):
            m1 = re.search(r'\b(?:draft|post|share|write)\s+(?:a\s+)?(?:post\s+)?(?:on|to|in)\s+linkedin\s*(?:saying|that|about|with)?[:\s]+(.+)', raw_query, re.IGNORECASE)
            m2 = re.search(r'\b(?:draft|post|share|write)\s+(.+?)\s+(?:on|to|in)\s+linkedin\b', raw_query, re.IGNORECASE)
            if m1:
                content = m1.group(1).strip().strip('"\'')
            elif m2:
                content = m2.group(1).strip().strip('"\'')
            else:
                m3 = re.search(r'\b(?:draft|post|write|share)\s+(.+)', raw_query, re.IGNORECASE)
                content = m3.group(1).strip() if m3 else raw_query
                content = re.sub(r'\s+(?:on|to|via)?\s*linkedin\b.*$', '', content, flags=re.IGNORECASE).strip().strip('"\'')
            return self.draft_post(content)
        elif any(w in low for w in ["message", "messages", "inbox", "dm", "dms"]):
            return self.check_messages()
        elif any(w in low for w in ["network", "invitation", "invitations", "connections"]):
            return self.view_network()
        else:
            ok, speech, items = self.get_notifications()
            return ok, speech, {"notifications": items}


# Singleton worker instances
YOUTUBE_WORKER = YouTubeDirectWorker()
GITHUB_WORKER = GitHubDirectWorker()
LINKEDIN_WORKER = LinkedInDirectWorker()
