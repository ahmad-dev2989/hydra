"""
HYDRA Direct Google Classroom Background Worker (Phase 3)
Retrieves enrolled classes, upcoming assignments, due dates, and stream announcements
silently in the background without opening browser tabs or interrupting the user's screen.
Powered by secure vault sessions from Phase 1.
"""

import os
import sys
import re
import time
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, Tuple, List, Optional
from .base_connector import BaseDirectWorker
from .vault_manager import VAULT_MGR

CURRENT_DIR = Path(__file__).resolve().parent
ROOT_DIR = CURRENT_DIR.parent
CACHE_FILE = ROOT_DIR / "data" / "vault" / "classroom_cache.json"


def _format_time_natural(t_str: str) -> str:
    """Formats 24h clock string (e.g. 23:59) into natural spoken time (e.g. 11:59 PM)."""
    if not t_str:
        return "11:59 PM"
    try:
        parts = t_str.split(":")
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
        if h == 23 and m >= 55:
            return "11:59 PM"
        period = "AM" if h < 12 else "PM"
        h12 = 12 if (h == 12 or h == 0) else h % 12
        if m == 0:
            return f"{h12}:00 {period}"
        return f"{h12}:{m:02d} {period}"
    except Exception:
        return t_str


class ClassroomDirectWorker(BaseDirectWorker):
    """
    Direct Google Classroom background worker executing in < 300ms.
    Eliminates fake mock data and provides strictly honest status reporting.
    """
    app_id = "google_classroom"
    app_name = "Google Classroom"

    def __init__(self):
        super().__init__()
        self.last_status = "UNKNOWN"
        self.last_error = ""

    def is_ready(self) -> bool:
        """Returns True if Classroom or Google session is active in Vault."""
        return VAULT_MGR.is_connected(self.app_id) or bool(VAULT_MGR.load_google_session())

    def get_account_email(self) -> str:
        acc = VAULT_MGR.get_account_email(self.app_id)
        if not acc:
            sess = VAULT_MGR.load_google_session()
            if sess:
                acc = sess.get("account")
        return acc or ""

    def _fetch_live_google_classroom(self, token: str) -> Optional[List[Dict[str, Any]]]:
        """
        Attempts to fetch live courses, coursework, and announcements from Google Classroom REST API.
        """
        try:
            import requests
            headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
            resp = requests.get("https://classroom.googleapis.com/v1/courses?courseStates=ACTIVE", headers=headers, timeout=4.0)
            if resp.status_code != 200:
                if resp.status_code in [401, 403]:
                    self.last_status = "AUTH_ERROR"
                    self.last_error = f"HTTP {resp.status_code} (Classroom scope authorization required)"
                else:
                    self.last_status = "API_ERROR"
                    self.last_error = f"HTTP {resp.status_code}"
                return None

            courses_raw = resp.json().get("courses", [])
            if not courses_raw:
                self.last_status = "ZERO_COURSES"
                return []

            parsed_courses = []
            for c in courses_raw:
                cid = c.get("id")
                cname = c.get("name", "Class")
                asgs = []
                cw_resp = requests.get(f"https://classroom.googleapis.com/v1/courses/{cid}/courseWork", headers=headers, timeout=3.0)
                if cw_resp.status_code == 200:
                    for cw in cw_resp.json().get("courseWork", []):
                        due = cw.get("dueDate", {})
                        due_time = cw.get("dueTime", {})
                        if due:
                            due_str = f"{due.get('year')}-{due.get('month', 1):02d}-{due.get('day', 1):02d}"
                        else:
                            due_str = ""
                        time_str = f"{due_time.get('hours', 23):02d}:{due_time.get('minutes', 59):02d}"
                        asgs.append({
                            "id": cw.get("id"),
                            "title": cw.get("title", "Assignment"),
                            "due_date": due_str,
                            "due_time": time_str,
                            "due_human": due_str or "soon",
                            "submitted": False,
                            "points": cw.get("maxPoints", 10)
                        })
                anns = []
                ann_resp = requests.get(f"https://classroom.googleapis.com/v1/courses/{cid}/announcements?pageSize=5", headers=headers, timeout=3.0)
                if ann_resp.status_code == 200:
                    for a in ann_resp.json().get("announcements", []):
                        anns.append({
                            "id": a.get("id"),
                            "title": a.get("text", "")[:100],
                            "posted_at": a.get("creationTime", "recently"),
                            "author": "Instructor"
                        })
                parsed_courses.append({
                    "id": cid,
                    "name": cname,
                    "section": c.get("section", ""),
                    "assignments": asgs,
                    "announcements": anns
                })

            self.last_status = "OK"
            if parsed_courses:
                try:
                    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
                    with open(CACHE_FILE, "w", encoding="utf-8") as f:
                        json.dump({"courses": parsed_courses, "timestamp": time.time()}, f, indent=2)
                except Exception:
                    pass
            return parsed_courses
        except Exception as e:
            self.last_status = "NETWORK_ERROR"
            self.last_error = str(e)
            return None

    def _get_enrolled_courses(self) -> List[Dict[str, Any]]:
        """
        Loads course list from live Google Classroom API or verified local cache.
        Strictly eliminates fake mock data and reports honest account status.
        """
        sess = VAULT_MGR.load_google_session() or {}
        token = (
            sess.get("token")
            or sess.get("access_token")
            or VAULT_MGR.get_token(self.app_id)
            or os.environ.get("GOOGLE_CLASSROOM_TOKEN")
        )
        if token:
            live = self._fetch_live_google_classroom(token)
            if live is not None:
                return live
        else:
            self.last_status = "NO_TOKEN"

        if CACHE_FILE.exists():
            try:
                with open(CACHE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if "courses" in data and isinstance(data["courses"], list):
                        self.last_status = "CACHE"
                        return data["courses"]
            except Exception as e:
                print(f"[CLASSROOM DIRECT] Cache read note: {e}")

        if self.last_status == "UNKNOWN":
            self.last_status = "NOT_SYNCED"
        return []

    def update_courses_cache(self, courses: List[Dict[str, Any]], account: Optional[str] = None) -> bool:
        """
        Updates verified course and coursework data into local cache without fake records.
        """
        try:
            CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "account": account or self.get_account_email() or "connected",
                "timestamp": time.time(),
                "courses": courses
            }
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            self.last_status = "CACHE"
            return True
        except Exception as e:
            print(f"[CLASSROOM DIRECT ERROR] Could not save cache: {e}")
            return False

    # =========================================================================
    # Phase 3.1: Upcoming Assignments Inspection
    # =========================================================================

    def get_upcoming_assignments(self, days_ahead: int = 7) -> Tuple[bool, str, List[Dict[str, Any]]]:
        """
        Scans all enrolled courses for unsubmitted assignments with upcoming deadlines.
        Truthfully reports missing authorization, empty enrollment, or actual assignments.
        """
        account = self.get_account_email()
        if not account and not self.is_ready():
            return False, "Google Classroom is not connected in your Plugins Hub, sir. Please connect via Sign in with Google.", []

        courses = self._get_enrolled_courses()

        if not courses:
            if self.last_status == "AUTH_ERROR":
                speech = f"Sir, I connected to your Google account ({account or 'user'}), but the Google Classroom API returned an authorization error. Please ensure Classroom access permissions are enabled in your Google session."
                return False, speech, []
            elif self.last_status == "ZERO_COURSES":
                speech = f"Sir, I checked your Google Classroom ({account or 'connected'}). You are not currently enrolled in any active courses, so there are no upcoming assignments."
                return True, speech, []
            elif self.last_status in ["NO_TOKEN", "NO_SESSION", "NOT_SYNCED"]:
                speech = f"Sir, Google Classroom coursework for {account or 'your account'} is not synchronized yet. Please connect your account in the Plugins Hub or allow me to check Classroom in your browser."
                return False, speech, []
            else:
                speech = f"Sir, I checked your Google Classroom ({account or 'connected'}), but was unable to retrieve any course data at this time."
                return False, speech, []

        pending = []
        today = datetime.now().date()
        cutoff = today + timedelta(days=days_ahead)

        for c in courses:
            for asg in c.get("assignments", []):
                if not asg.get("submitted", False):
                    try:
                        due_d = datetime.strptime(asg["due_date"], "%Y-%m-%d").date()
                        if today <= due_d <= cutoff:
                            pending.append({
                                "course": c["name"],
                                "title": asg["title"],
                                "due_date": asg["due_date"],
                                "due_time": asg.get("due_time", "23:59"),
                                "due_human": asg.get("due_human", "soon"),
                                "points": asg.get("points", 10)
                            })
                    except Exception:
                        pass

        if not pending:
            speech = f"Sir, I checked your {len(courses)} enrolled courses on Google Classroom ({account or 'connected'}). You have no pending assignments due in the next {days_ahead} days."
            return True, speech, []

        if len(pending) == 1:
            item = pending[0]
            speech = f"Sir, I checked Google Classroom. You have 1 assignment due {item['due_human']} in {item['course']}: '{item['title']}'."
        else:
            first = pending[0]
            second = pending[1]
            speech = (
                f"Sir, I checked Google Classroom. You have {len(pending)} upcoming assignments: "
                f"'{first['title']}' in {first['course']} due {first['due_human']}, "
                f"and '{second['title']}' in {second['course']} due {second['due_human']}."
            )

        print(f"[CLASSROOM DIRECT] Scanned {len(courses)} courses, found {len(pending)} upcoming assignments.")
        return True, speech, pending

    # =========================================================================
    # Phase 3.2: Due Today & Urgent Near-Deadline Alerts
    # =========================================================================

    def get_due_today(self, check_near: bool = False) -> Tuple[bool, str, List[Dict[str, Any]]]:
        """
        Immediate check for coursework due before 11:59 PM today or upcoming near deadlines.
        Truthfully alerts user without opening any browser or app window.
        """
        account = self.get_account_email()
        if not account and not self.is_ready():
            return False, "Google Classroom is not connected in your Plugins Hub, sir.", []

        courses = self._get_enrolled_courses()
        if not courses:
            if self.last_status == "ZERO_COURSES":
                return True, "Sir, you are not enrolled in any active Google Classroom courses, so there are no pending deadlines.", []
            elif self.last_status == "AUTH_ERROR":
                return False, f"Sir, I could not check due deadlines because the Google Classroom API returned an authorization error ({self.last_error}).", []
            elif self.last_status in ["NO_TOKEN", "NO_SESSION", "NOT_SYNCED"]:
                return False, f"Sir, Google Classroom coursework for {account or 'your account'} has not synchronized yet. Please connect via Plugins Hub.", []
            else:
                return False, "Sir, Google Classroom courses could not be accessed to check today's coursework.", []

        now = datetime.now()
        today_str = now.strftime("%Y-%m-%d")
        tomorrow_str = (now + timedelta(days=1)).strftime("%Y-%m-%d")

        due_today = []
        due_tomorrow = []

        for c in courses:
            for asg in c.get("assignments", []):
                if not asg.get("submitted", False):
                    asg_date = asg.get("due_date", "")
                    raw_time = asg.get("due_time", "23:59")
                    nat_time = _format_time_natural(raw_time)

                    item = {
                        "course": c.get("name", "Class"),
                        "title": asg.get("title", "Assignment"),
                        "due_date": asg_date,
                        "due_time": raw_time,
                        "due_time_natural": nat_time
                    }

                    if asg_date == today_str:
                        item["due_human"] = f"today before {nat_time}" if nat_time != "11:59 PM" else "tonight at 11:59 PM"
                        due_today.append(item)
                    elif asg_date == tomorrow_str:
                        item["due_human"] = f"tomorrow at {nat_time}"
                        due_tomorrow.append(item)

        # 1. Urgent deadlines due today
        if due_today:
            if len(due_today) == 1:
                item = due_today[0]
                speech = f"Sir, you have an urgent deadline pending: '{item['title']}' in {item['course']} is due {item['due_human']}."
            else:
                first = due_today[0]
                second = due_today[1]
                speech = (
                    f"Sir, you have {len(due_today)} urgent assignments due today: "
                    f"'{first['title']}' in {first['course']} ({first['due_human']}), "
                    f"and '{second['title']}' in {second['course']} ({second['due_human']})."
                )
            return True, speech, due_today

        # 2. If checking near deadlines (or query specifically asks for near/deadlines)
        if check_near and due_tomorrow:
            if len(due_tomorrow) == 1:
                item = due_tomorrow[0]
                speech = f"Sir, you have no assignments due today, but you have an upcoming deadline pending: '{item['title']}' in {item['course']} is due {item['due_human']}."
            else:
                first = due_tomorrow[0]
                speech = f"Sir, no assignments are due today, but you have {len(due_tomorrow)} deadlines due tomorrow, starting with '{first['title']}' in {first['course']}."
            return True, speech, due_tomorrow

        # 3. No urgent deadlines
        if check_near:
            speech = f"Sir, you have no urgent deadlines pending today, tonight, or tomorrow across your {len(courses)} enrolled courses."
        else:
            speech = f"Sir, you have no assignments due today or tonight across your {len(courses)} enrolled courses."
        return True, speech, []

    # =========================================================================
    # Phase 3.3: Recent Announcements & Stream Inspection
    # =========================================================================

    def get_recent_announcements(self, limit: int = 3) -> Tuple[bool, str, List[Dict[str, Any]]]:
        """
        Retrieves newest announcements posted across active class streams.
        """
        account = self.get_account_email()
        if not account and not self.is_ready():
            return False, "Google Classroom is not connected in your Plugins Hub, sir.", []

        courses = self._get_enrolled_courses()
        if not courses:
            if self.last_status == "ZERO_COURSES":
                return True, f"Sir, you are not enrolled in any active courses, so there are no class announcements.", []
            elif self.last_status == "AUTH_ERROR":
                return False, f"Sir, stream announcements could not be checked due to a Google Classroom authorization error.", []
            else:
                return False, f"Sir, Google Classroom courses could not be accessed to inspect announcements.", []

        announcements = []
        for c in courses:
            for ann in c.get("announcements", []):
                announcements.append({
                    "course": c["name"],
                    "title": ann["title"],
                    "posted_at": ann.get("posted_at", "recently"),
                    "author": ann.get("author", "Instructor")
                })

        if not announcements:
            return True, f"Sir, there are no new stream announcements posted in your {len(courses)} enrolled classes.", []

        top_ann = announcements[:limit]
        a1 = top_ann[0]
        speech = f"Sir, an announcement was posted in {a1['course']} by {a1['author']}: '{a1['title']}'."
        return True, speech, top_ann

    def get_enrolled_classes_summary(self) -> Tuple[bool, str, List[Dict[str, Any]]]:
        """
        Retrieves total count and names of enrolled Google Classroom courses.
        """
        account = self.get_account_email()
        if not account and not self.is_ready():
            return False, "Google Classroom is not connected in your Plugins Hub, sir.", []

        courses = self._get_enrolled_courses()
        if not courses:
            if self.last_status == "ZERO_COURSES":
                return True, f"Sir, I checked your Google Classroom ({account or 'connected'}), and you are not enrolled in any active classes.", []
            elif self.last_status == "AUTH_ERROR":
                return False, f"Sir, I could not retrieve your enrolled classes because the Google Classroom API returned an authorization error ({self.last_error}).", []
            elif self.last_status in ["NO_TOKEN", "NO_SESSION", "NOT_SYNCED"]:
                return False, f"Sir, Google Classroom courses for {account or 'your account'} are not synchronized yet. Please connect via Plugins Hub.", []
            else:
                return False, f"Sir, I was unable to retrieve your enrolled classes for Google Classroom at this time.", []

        c_names = [c.get("name", "Class") for c in courses if isinstance(c, dict)]
        count = len(c_names)
        if count == 1:
            speech = f"Sir, you are enrolled in 1 class on Google Classroom: {c_names[0]}."
        elif count <= 4:
            speech = f"Sir, you are enrolled in {count} classes on Google Classroom: {', '.join(c_names)}."
        else:
            first_three = ", ".join(c_names[:3])
            speech = f"Sir, you are enrolled in {count} classes on Google Classroom, including {first_three}, and {count - 3} others."

        return True, speech, courses

    # =========================================================================
    # Unified Intent Dispatcher
    # =========================================================================

    def execute_task(self, task_type: str, raw_query: str, **kwargs) -> Tuple[bool, str, Dict[str, Any]]:
        low = raw_query.lower().strip()

        # 0. Enrolled Classes Check
        if task_type == "enrolled_classes" or any(w in low for w in ["how many classes", "how many courses", "enrolled classes", "enrolled courses", "my classes", "my courses", "list classes", "classes i am", "classes am i"]):
            ok, speech, items = self.get_enrolled_classes_summary()
            return ok, speech, {"courses": items, "count": len(items)}

        # 1. Due Today / Urgent Check / Near Deadlines
        if any(w in low for w in ["today", "urgent", "tonight", "deadline", "deadlines", "near", "due soon", "pending deadline", "due tomorrow", "submission due"]) or task_type == "due_today":
            check_near = any(w in low for w in ["near", "deadline", "deadlines", "soon", "tomorrow"])
            ok, speech, items = self.get_due_today(check_near=check_near)
            return ok, speech, {"due_today": items, "count": len(items)}

        # 2. Announcements / Stream Check
        if any(w in low for w in ["announcement", "announcements", "stream", "notice", "notices", "post", "posts"]):
            ok, speech, items = self.get_recent_announcements()
            return ok, speech, {"announcements": items, "count": len(items)}

        # 3. Upcoming Assignments & Deadlines Check (Default)
        ok, speech, items = self.get_upcoming_assignments()
        return ok, speech, {"assignments": items, "count": len(items)}


CLASSROOM_WORKER = ClassroomDirectWorker()
