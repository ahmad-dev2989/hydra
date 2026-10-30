"""
HYDRA Google Calendar & General Reminder Worker
Provides full natural language reminder, scheduling, and event management:
- Natural expression parsing (absolute dates like '5 November at 11:55 PM', relative expressions like 'tomorrow at 9 AM', 'in 2 hours', 'next Monday')
- Google Calendar API v3 integration with OAuth/Service tokens
- Local high-speed persistent reminder store (data/calendar_reminders.json)
- Event creation, listing, rescheduling, deletion, and proactive alarm notifications
"""

import os
import re
import json
import time
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List, Tuple
from pathlib import Path
from .base_connector import BaseDirectWorker

# Paths
CURRENT_DIR = Path(__file__).resolve().parent
ROOT_DIR = CURRENT_DIR.parent
REMINDERS_DB_FILE = ROOT_DIR / "data" / "calendar_reminders.json"
CONNECTED_PLUGINS_FILE = ROOT_DIR / "connected_plugins.json"


class GoogleCalendarWorker(BaseDirectWorker):
    """Manages Google Calendar API events and HYDRA's general reminder system."""

    app_id: str = "google_calendar"
    app_name: str = "Google Calendar"

    def __init__(self):
        self._ensure_db()

    def is_ready(self) -> bool:
        """Returns True since local scheduler is always available and cloud sync enhances it."""
        return True

    def _ensure_db(self):
        os.makedirs(REMINDERS_DB_FILE.parent, exist_ok=True)
        if not REMINDERS_DB_FILE.exists():
            with open(REMINDERS_DB_FILE, "w", encoding="utf-8") as f:
                json.dump({"events": [], "last_sync": 0}, f, indent=2)

    def _load_reminders(self) -> List[Dict[str, Any]]:
        self._ensure_db()
        try:
            with open(REMINDERS_DB_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("events", [])
        except Exception:
            return []

    def _save_reminders(self, events: List[Dict[str, Any]]):
        self._ensure_db()
        try:
            with open(REMINDERS_DB_FILE, "w", encoding="utf-8") as f:
                json.dump({"events": events, "last_updated": time.time()}, f, indent=2)
        except Exception as e:
            print(f"[CALENDAR WORKER ERROR] Could not save reminders: {e}")

    def is_configured(self) -> bool:
        """Checks if Google Calendar API / OAuth credentials are configured."""
        try:
            if CONNECTED_PLUGINS_FILE.exists():
                with open(CONNECTED_PLUGINS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    cal_data = data.get("google_calendar", {})
                    if isinstance(cal_data, dict) and cal_data.get("connected"):
                        return True
        except Exception:
            pass
        return False

    def parse_reminder_expression(self, text: str, ref_time: Optional[datetime] = None) -> Tuple[Optional[str], Optional[datetime], Optional[str]]:
        """
        Parses natural language date, time, and reminder title.
        Returns:
            (title: str, target_datetime: datetime, error_message: str)
        """
        if ref_time is None:
            ref_time = datetime.now()

        clean = text.strip()
        low = clean.lower()

        # Remove leading trigger phrases: "remind me to", "remind me", "don't let me forget to", "schedule", "set a reminder"
        prefixes = [
            r"^remind\s+me\s+(?:to\s+|about\s+|that\s+)?",
            r"^don't\s+let\s+me\s+forget\s+(?:to\s+|about\s+)?",
            r"^schedule\s+(?:a\s+meeting\s+|an\s+event\s+|a\s+reminder\s+)?(?:to\s+|for\s+|about\s+)?",
            r"^set\s+a\s+reminder\s+(?:to\s+|for\s+|about\s+)?",
            r"^create\s+a\s+(?:calendar\s+)?event\s+(?:for\s+|about\s+)?"
        ]
        task_str = clean
        for p in prefixes:
            task_str = re.sub(p, "", task_str, flags=re.IGNORECASE).strip()

        target_dt = None

        # 1. Check relative offsets: "in X hours", "in X minutes", "in X days"
        offset_match = re.search(r'\bin\s+(\d+)\s*(hour|hr|minute|min|day|sec)s?\b', low)
        if offset_match:
            amount = int(offset_match.group(1))
            unit = offset_match.group(2)
            if "hour" in unit or "hr" in unit:
                target_dt = ref_time + timedelta(hours=amount)
            elif "min" in unit:
                target_dt = ref_time + timedelta(minutes=amount)
            elif "day" in unit:
                target_dt = ref_time + timedelta(days=amount)
            elif "sec" in unit:
                target_dt = ref_time + timedelta(seconds=amount)
            # Remove "in X hours" from task title
            task_str = re.sub(r'\bin\s+\d+\s*(?:hours?|hrs?|minutes?|mins?|days?|secs?)\b', '', task_str, flags=re.IGNORECASE).strip()

        # 2. Check "tomorrow", "tonight", "today"
        elif "tomorrow" in low or "tonight" in low or "today" in low:
            day_offset = 1 if "tomorrow" in low else 0
            base_date = (ref_time + timedelta(days=day_offset)).date()

            # Time parsing: e.g. "at 9 AM", "at 11:55 PM", "at 8"
            t_match = re.search(r'\bat\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b', low)
            if t_match:
                hr = int(t_match.group(1))
                mn = int(t_match.group(2)) if t_match.group(2) else 0
                meridiem = t_match.group(3)
                if meridiem == "pm" and hr < 12:
                    hr += 12
                elif meridiem == "am" and hr == 12:
                    hr = 0
                elif not meridiem and "tonight" in low and hr < 12:
                    hr += 12
                target_dt = datetime.combine(base_date, datetime.min.time()).replace(hour=hr, minute=mn)
            elif "tonight" in low:
                target_dt = datetime.combine(base_date, datetime.min.time()).replace(hour=20, minute=0)
            elif "tomorrow" in low:
                # Default tomorrow morning 9:00 AM
                target_dt = datetime.combine(base_date, datetime.min.time()).replace(hour=9, minute=0)

            task_str = re.sub(r'\b(?:tomorrow|tonight|today)\b', '', task_str, flags=re.IGNORECASE).strip()
            task_str = re.sub(r'\bat\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?\b', '', task_str, flags=re.IGNORECASE).strip()

        # 3. Check specific day names: "next Monday", "this Friday", "on Tuesday"
        elif any(day in low for day in ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]):
            days_map = {
                "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
                "friday": 4, "saturday": 5, "sunday": 6
            }
            matched_day = next((day for day in days_map if day in low), None)
            if matched_day is not None:
                target_weekday = days_map[matched_day]
                current_weekday = ref_time.weekday()
                days_ahead = (target_weekday - current_weekday) % 7
                if days_ahead == 0 or "next" in low:
                    days_ahead += 7
                base_date = (ref_time + timedelta(days=days_ahead)).date()

                t_match = re.search(r'\bat\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b', low)
                if t_match:
                    hr = int(t_match.group(1))
                    mn = int(t_match.group(2)) if t_match.group(2) else 0
                    meridiem = t_match.group(3)
                    if meridiem == "pm" and hr < 12:
                        hr += 12
                    elif meridiem == "am" and hr == 12:
                        hr = 0
                    target_dt = datetime.combine(base_date, datetime.min.time()).replace(hour=hr, minute=mn)
                else:
                    target_dt = datetime.combine(base_date, datetime.min.time()).replace(hour=9, minute=0)

                task_str = re.sub(r'\b(?:next|this|on)?\s*(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b', '', task_str, flags=re.IGNORECASE).strip()
                task_str = re.sub(r'\bat\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?\b', '', task_str, flags=re.IGNORECASE).strip()

        # 4. Check absolute dates: "on 5 November at 11:55 PM", "November 5 at 11:55pm"
        months = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"]
        month_match = None
        for idx, m_name in enumerate(months, start=1):
            if m_name in low or m_name[:3] in low:
                # e.g. "5 november" or "november 5"
                p1 = rf'\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?{m_name}\b'
                p2 = rf'\b{m_name}\s+(\d{{1,2}})(?:st|nd|rd|th)?\b'
                m1 = re.search(p1, low)
                m2 = re.search(p2, low)
                day_num = None
                if m1:
                    day_num = int(m1.group(1))
                    task_str = re.sub(p1, '', task_str, flags=re.IGNORECASE).strip()
                elif m2:
                    day_num = int(m2.group(1))
                    task_str = re.sub(p2, '', task_str, flags=re.IGNORECASE).strip()

                if day_num:
                    year = ref_time.year
                    # Check if date has already passed this year, then set to next year
                    try:
                        tentative_dt = datetime(year, idx, day_num)
                        if tentative_dt < ref_time - timedelta(days=1):
                            year += 1
                    except Exception:
                        pass

                    t_match = re.search(r'\bat\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b', low)
                    hr = 9
                    mn = 0
                    if t_match:
                        hr = int(t_match.group(1))
                        mn = int(t_match.group(2)) if t_match.group(2) else 0
                        meridiem = t_match.group(3)
                        if meridiem == "pm" and hr < 12:
                            hr += 12
                        elif meridiem == "am" and hr == 12:
                            hr = 0
                        task_str = re.sub(r'\bat\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?\b', '', task_str, flags=re.IGNORECASE).strip()

                    try:
                        target_dt = datetime(year, idx, day_num, hr, mn)
                    except Exception as date_err:
                        return None, None, f"Invalid date: {date_err}"
                    break

        if target_dt is None:
            return None, None, "Sir, what date and time should I schedule the reminder for?"

        # Clean title
        task_str = re.sub(r'\b(?:on|at|for|about|to)\b', ' ', task_str, flags=re.IGNORECASE)
        task_str = re.sub(r'\s+', ' ', task_str).strip()
        # Capitalize nicely
        if task_str:
            title = task_str[0].upper() + task_str[1:]
        else:
            title = "Reminder"

        return title, target_dt, None

    def create_reminder(self, title: str, start_dt: datetime, end_dt: Optional[datetime] = None, description: str = "") -> Tuple[bool, str, Dict[str, Any]]:
        """
        Creates a reminder event on Google Calendar and local store with popup notification.
        """
        if end_dt is None:
            end_dt = start_dt + timedelta(minutes=30)

        event_id = f"evt_{int(time.time() * 1000)}"
        event_obj = {
            "id": event_id,
            "title": title,
            "description": description or f"HYDRA Scheduled Reminder: {title}",
            "start": start_dt.strftime("%Y-%m-%d %H:%M"),
            "end": end_dt.strftime("%Y-%m-%d %H:%M"),
            "notification": "exact_time",
            "created_at": time.time(),
            "synced_google": False,
            "notified": False
        }

        # 1. Attempt Google Calendar Cloud API creation if credentials are ready
        google_synced = False
        if self.is_configured():
            try:
                # Call Google Calendar API v3
                google_synced = self._create_google_calendar_api_event(title, start_dt, end_dt, description)
                if google_synced:
                    event_obj["synced_google"] = True
            except Exception as e:
                print(f"[GOOGLE CALENDAR API SYNC NOTE]: {e}")

        # 2. Persist in local reminder database
        events = self._load_reminders()
        events.append(event_obj)
        self._save_reminders(events)

        # Format pleasant confirmation message
        date_str = start_dt.strftime("%A, %B %d at %I:%M %p").replace(" 0", " ")
        cloud_tag = " on Google Calendar" if google_synced else ""
        speech = f"Sir, I have scheduled your reminder for '{title}' on {date_str}{cloud_tag} with notification set."
        return True, speech, event_obj

    def _create_google_calendar_api_event(self, title: str, start_dt: datetime, end_dt: datetime, description: str) -> bool:
        """Invokes official Google Calendar API v3 if API key / token exists."""
        try:
            import urllib.request
            import urllib.parse

            with open(CONNECTED_PLUGINS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                cal = data.get("google_calendar", {})
                api_key = (cal.get("data") or {}).get("apiKey") or (cal.get("data") or {}).get("password")

            if not api_key:
                return False

            # Format ISO datetime
            body = {
                "summary": title,
                "description": description,
                "start": {"dateTime": start_dt.isoformat() + "Z"},
                "end": {"dateTime": end_dt.isoformat() + "Z"},
                "reminders": {
                    "useDefault": False,
                    "overrides": [{"method": "popup", "minutes": 0}]
                }
            }
            # Post to Google Calendar API
            url = f"https://www.googleapis.com/calendar/v3/calendars/primary/events?key={api_key}"
            req = urllib.request.Request(
                url,
                data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                return response.status in (200, 201)
        except Exception:
            return False

    def get_upcoming_reminders(self, limit: int = 5) -> List[Dict[str, Any]]:
        """Returns upcoming reminders sorted by time."""
        events = self._load_reminders()
        now = datetime.now()
        upcoming = []
        for e in events:
            try:
                dt = datetime.strptime(e["start"], "%Y-%m-%d %H:%M")
                if dt >= now - timedelta(minutes=5):
                    upcoming.append((dt, e))
            except Exception:
                pass
        upcoming.sort(key=lambda x: x[0])
        return [e for _, e in upcoming[:limit]]

    def find_reminders(self, query: str = "") -> List[Dict[str, Any]]:
        """Finds reminders matching a keyword."""
        events = self._load_reminders()
        if not query:
            return events
        low = query.lower()
        return [e for e in events if low in e.get("title", "").lower() or low in e.get("description", "").lower()]

    def delete_reminder(self, identifier: str) -> Tuple[bool, str]:
        """Deletes a reminder by event ID or matching title."""
        events = self._load_reminders()
        low = identifier.lower().strip()
        filtered = []
        deleted_title = None
        for e in events:
            if e.get("id") == identifier or low in e.get("title", "").lower():
                deleted_title = e.get("title")
            else:
                filtered.append(e)

        if deleted_title:
            self._save_reminders(filtered)
            return True, f"Sir, I have deleted the reminder for '{deleted_title}'."
        return False, f"Sir, I could not find a reminder matching '{identifier}'."

    def reschedule_reminder(self, identifier: str, new_cmd: str) -> Tuple[bool, str, Dict[str, Any]]:
        """Reschedules an existing reminder to a new date and time."""
        events = self._load_reminders()
        low_id = identifier.lower().strip()
        matched_idx = None
        for idx, e in enumerate(events):
            if e.get("id") == identifier or low_id in e.get("title", "").lower():
                matched_idx = idx
                break

        if matched_idx is None:
            return False, f"Sir, I could not find a reminder matching '{identifier}' to reschedule.", {}

        title, new_dt, err = self.parse_reminder_expression(new_cmd)
        if err or not new_dt:
            return False, "Sir, what new date and time should I reschedule this reminder to?", {}

        event = events[matched_idx]
        event["start"] = new_dt.strftime("%Y-%m-%d %H:%M")
        event["end"] = (new_dt + timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M")
        event["notified"] = False
        self._save_reminders(events)
        date_str = new_dt.strftime("%A, %B %d at %I:%M %p").replace(" 0", " ")
        return True, f"Sir, I have rescheduled '{event['title']}' to {date_str}.", event

    def get_due_reminders(self) -> List[Dict[str, Any]]:
        """Returns reminders that are due right now and not yet notified."""
        events = self._load_reminders()
        now = datetime.now()
        due = []
        updated = False
        for e in events:
            if e.get("notified"):
                continue
            try:
                dt = datetime.strptime(e["start"], "%Y-%m-%d %H:%M")
                if timedelta(minutes=-3) <= (now - dt) <= timedelta(minutes=1):
                    due.append(e)
                    e["notified"] = True
                    updated = True
            except Exception:
                pass
        if updated:
            self._save_reminders(events)
        return due

    def execute_task(self, action: str, cmd_clean: str, **kwargs) -> Tuple[bool, str, Dict[str, Any]]:
        """Direct dispatcher interface for calendar commands."""
        low = cmd_clean.lower()

        # 1. Listing reminders:
        listing_patterns = [
            r"\bwhat\s+are\s+(?:my\s+)?(?:upcoming\s+)?reminders\b",
            r"\b(?:list|show|check|get|view)\s+(?:my\s+)?(?:upcoming\s+)?(?:reminders|schedule|events)\b",
            r"\bupcoming\s+(?:reminders|schedule|events)\b",
            r"\bwhat\s+is\s+on\s+my\s+schedule\b"
        ]
        if any(re.search(p, low) for p in listing_patterns):
            upcoming = self.get_upcoming_reminders(limit=5)
            if not upcoming:
                return True, "Sir, you currently have no upcoming reminders on your schedule.", {}
            titles = [f"'{u['title']}' on {u['start']}" for u in upcoming]
            speech = f"Sir, your upcoming schedule has: {', '.join(titles)}."
            return True, speech, {"reminders": upcoming}

        # 2. Rescheduling reminders:
        if any(re.search(p, low) for p in [r"\breschedule\b", r"\bchange\s+(?:the\s+)?reminder\b", r"\bmove\s+(?:the\s+)?reminder\b"]):
            m = re.search(r'(?:reschedule|change|move)\s+(?:the\s+)?reminder\s+(?:for\s+|about\s+)?([^\bto\b]+)\s+to\s+(.+)', cmd_clean, re.IGNORECASE)
            if m:
                target_name = m.group(1).strip()
                new_time_str = m.group(2).strip()
                ok, speech, ev = self.reschedule_reminder(target_name, new_time_str)
                return ok, speech, ev
            return False, "Sir, please tell me which reminder you'd like to reschedule and the new date or time.", {}

        # 3. Finding reminders:
        if any(re.search(p, low) for p in [r"\bfind\s+(?:the\s+)?reminder\b", r"\bsearch\s+(?:for\s+)?(?:the\s+)?reminders?\b", r"\blook\s+for\s+(?:the\s+)?reminder\b"]):
            query = re.sub(r'^(?:find|search|look\s+for)\s+(?:the\s+)?reminders?\s+(?:for\s+|about\s+)?', '', cmd_clean, flags=re.IGNORECASE).strip()
            found = self.find_reminders(query)
            if not found:
                return True, f"Sir, I found no reminders matching '{query}'.", {}
            titles = [f"'{f['title']}' on {f['start']}" for f in found]
            return True, f"Sir, I found: {', '.join(titles)}.", {"reminders": found}

        # 4. Deleting reminders:
        if any(re.search(p, low) for p in [r"\bdelete\s+(?:the\s+)?reminder\b", r"\bcancel\s+(?:the\s+)?reminder\b", r"\bremove\s+(?:the\s+)?reminder\b"]):
            query = re.sub(r'^(?:delete|cancel|remove)\s+(?:the\s+)?reminder\s+(?:for\s+|about\s+)?', '', cmd_clean, flags=re.IGNORECASE).strip()
            ok, speech = self.delete_reminder(query)
            return ok, speech, {}

        # 5. Creating reminder (Default):
        title, start_dt, err = self.parse_reminder_expression(cmd_clean)
        if err:
            return False, err, {}

        return self.create_reminder(title, start_dt)


# Singleton
CALENDAR_WORKER = GoogleCalendarWorker()
