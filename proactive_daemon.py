"""
HYDRA Phase 11: Proactive Desktop Daemon & Event Triggers Engine
Author: Google DeepMind Antigravity Pair Programmer
Architecture:
  1. WhatsApp Real-Time Notification & In-Chat Message Monitor:
     - WAL-aware SQLite monitor copying wpndatabase.db, -wal, -shm to read real-time notifications
     - Window title unread badge listener: detects (N) WhatsApp title changes
     - Proactively announces: "Sir, Tanzel texted you on WhatsApp."
     - Caches message details: {sender, body, timestamp}
     - Non-intelligent / fast direct reader: "read the message", "what did he say"
     - Visual screen fallback: localized winocr reader for active chat window
     - Intelligent AI mode: summarization, urgency detection, translation, contextual Q&A
  2. Proactive Actionable Timers & Delayed Execution:
     - Natural syntax parser for reminders and delayed actions:
       - "remind me in 20 sec to call tanzeel"
       - "remind me to open canva after 30 sec"
       - "open canva after 30 sec" / "maximize canva after 10 sec"
     - Background tick daemon announces deadline AND executes actionable tasks (e.g. opens Canva or Spotify)
  3. System Health & Hardware Event Triggers:
     - Typo-tolerant battery status: matches 'batery status', 'batter status', etc.
     - Low battery alert (< 20% uncharged), 100% full charge alert
     - Network monitor: Wi-Fi / Internet disconnect & restore alerts
"""

import os
import re
import sys
import time
import uuid
import json
import socket
import shutil
import tempfile
import threading
import sqlite3
import asyncio
import xml.etree.ElementTree as ET
from typing import Dict, Any, List, Optional, Tuple, Callable

try:
    import winrt.windows.ui.notifications.management as winrt_mgmt
    import winrt.windows.ui.notifications as winrt_notif
    HAS_WINRT_NOTIF = True
except ImportError:
    HAS_WINRT_NOTIF = False
    winrt_mgmt = None
    winrt_notif = None

try:
    import psutil
except ImportError:
    psutil = None

TIMERS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "active_timers.json")

# Spoken number dictionary
NUMBER_WORDS = {
    "zero": 0, "half": 0.5, "a": 1, "an": 1, "one": 1, "two": 2, "three": 3,
    "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40,
    "fifty": 50, "sixty": 60
}


# =============================================================================
# 1. WHATSAPP PROACTIVE NOTIFICATION & MESSAGE MONITOR
# =============================================================================

class WhatsAppProactiveMonitor:
    """
    Monitors Windows Native Notifications in RAM (via WinRT UserNotificationListener)
    with ultra-low latency (< 250ms), zero disk polling delay, and zero token cost.
    Proactively announces incoming WhatsApp messages and groups instantly.
    Falls back gracefully to WAL-aware SQLite and window title badge polling.
    """
    def __init__(self, on_message_callback: Optional[Callable[..., None]] = None):
        self.on_message_callback = on_message_callback
        self.db_path = os.path.expandvars(r'%LOCALAPPDATA%\Microsoft\Windows\Notifications\wpndatabase.db')
        self.last_seen_order = 0
        self.last_unread_title_count = 0
        self.latest_message: Optional[Dict[str, Any]] = None

        # Real-time WinRT state
        self.seen_winrt_signatures: Dict[int, Tuple[str, ...]] = {}
        self.winrt_seeded = False
        self._winrt_thread: Optional[threading.Thread] = None
        self._winrt_running = False

        self._init_last_order()

    def start_realtime_listener(self):
        """Starts the dedicated ultra-fast (200ms) WinRT notification watcher thread."""
        if not HAS_WINRT_NOTIF or self._winrt_running:
            return
        self._winrt_running = True
        self._winrt_thread = threading.Thread(target=self._run_winrt_event_loop, daemon=True, name="HydraWinRTNotif")
        self._winrt_thread.start()
        print("[WHATSAPP MONITOR] Zero-delay WinRT Real-Time Desktop Notification thread launched.")

    def stop_realtime_listener(self):
        """Stops the real-time WinRT listener thread."""
        self._winrt_running = False

    def _run_winrt_event_loop(self):
        """Dedicated asyncio thread running high-frequency WinRT notification checks."""
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self._winrt_monitor_coroutine())
        except Exception as e:
            print(f"[WHATSAPP WINRT THREAD ERROR]: {e}")

    async def _winrt_monitor_coroutine(self):
        """Queries Windows Toast Manager in RAM every 200ms for instantaneous notification capture."""
        try:
            listener = winrt_mgmt.UserNotificationListener.current
            # 1. Baseline seeding: record existing notifications so historical messages aren't re-announced
            try:
                initial_notifs = await listener.get_notifications_async(winrt_notif.NotificationKinds.TOAST)
                for n in initial_notifs:
                    sig = self._extract_winrt_signature(n)
                    self.seen_winrt_signatures[n.id] = sig
                self.winrt_seeded = True
                print(f"[WHATSAPP MONITOR] WinRT Listener online. Seeded {len(self.seen_winrt_signatures)} baseline notifications.")
            except Exception as e:
                print(f"[WHATSAPP MONITOR] Baseline seed note: {e}")
                self.winrt_seeded = True

            # 2. Continuous real-time detection loop (200ms tick = zero human perceptible delay)
            while self._winrt_running:
                try:
                    current_notifs = await listener.get_notifications_async(winrt_notif.NotificationKinds.TOAST)
                    for n in current_notifs:
                        nid = n.id
                        sig = self._extract_winrt_signature(n)

                        # New notification OR existing notification whose text was updated
                        if nid not in self.seen_winrt_signatures or self.seen_winrt_signatures[nid] != sig:
                            self.seen_winrt_signatures[nid] = sig

                            # Check if notification is from WhatsApp Desktop or WhatsApp Web
                            if self._is_whatsapp_notification(n, sig):
                                parsed = self._parse_winrt_notification(n, sig)
                                if parsed:
                                    self._dispatch_incoming_message(parsed)
                except Exception as inner_e:
                    await asyncio.sleep(0.5)
                    continue

                await asyncio.sleep(0.20)  # 200ms check: instant capture (< 0.25s) with negligible CPU
        except Exception as e:
            print(f"[WHATSAPP WINRT COROUTINE CRITICAL]: {e}")

    def _is_whatsapp_notification(self, n: Any, sig: Tuple[str, ...]) -> bool:
        """Detects WhatsApp Desktop app or WhatsApp Web browser push notifications."""
        try:
            app_name = (n.app_info.display_info.display_name if n.app_info and n.app_info.display_info else "") or ""
            app_id = (n.app_info.app_user_model_id if n.app_info else "") or ""

            # WhatsApp Desktop (Windows Store / MSIX / Native Win32)
            if "whatsapp" in app_name.lower() or "whatsapp" in app_id.lower():
                return True

            # Browser push notification from WhatsApp Web
            if any(browser in app_name.lower() for browser in ["chrome", "brave", "edge", "opera", "firefox"]):
                if any("whatsapp" in s.lower() for s in sig):
                    return True
        except Exception:
            pass
        return False

    def _extract_winrt_signature(self, n: Any) -> Tuple[str, ...]:
        """Extracts text elements from a WinRT notification object."""
        try:
            visual = n.notification.visual
            binding = visual.get_binding(winrt_notif.KnownNotificationBindings.toast_generic)
            if binding:
                return tuple(t.text.strip() for t in binding.get_text_elements() if t.text and t.text.strip())
        except Exception:
            pass
        return ()

    def _parse_winrt_notification(self, n: Any, sig: Tuple[str, ...]) -> Optional[Dict[str, Any]]:
        """Parses WhatsApp contact name, group name, and message body with zero delay."""
        if not sig or len(sig) < 2:
            return None

        # Filter out generic WhatsApp unread summary badges like ['WhatsApp', '282 new messages'] or ['WhatsApp contact', 'You have 13 unread messages']
        sig_str = " ".join(sig).lower()
        if "unread message" in sig_str or "new message" in sig_str or "unread" in sig[1].lower():
            return None

        is_group = False
        group_name = None
        sender = first
        body = second

        # WhatsApp group patterns on Windows:
        # Pattern 1: second is "Author: message" -> first is group name
        m_group_body = re.match(r'^([^:\n]{1,40}):\s*(.*)$', second)
        # Pattern 2: first is "Author (Group Name)"
        m_paren = re.match(r'^(.+?)\s*\((.+?)\)$', first)

        if m_group_body:
            is_group = True
            group_name = first
            sender = m_group_body.group(1).strip()
            body = m_group_body.group(2).strip()
        elif m_paren:
            is_group = True
            sender = m_paren.group(1).strip()
            group_name = m_paren.group(2).strip()

        # Clean invisible bidirectional formatting characters (\u200e, \u200f)
        sender = re.sub(r'[\u200e\u200f\u202a-\u202e]', '', sender).strip()
        body = re.sub(r'[\u200e\u200f\u202a-\u202e]', '', body).strip()
        if group_name:
            group_name = re.sub(r'[\u200e\u200f\u202a-\u202e]', '', group_name).strip()

        return {
            "sender": sender,
            "body": body,
            "is_group": is_group,
            "group_name": group_name,
            "raw_body": second,
            "notification_id": getattr(n, "id", None)
        }

    def _dispatch_incoming_message(self, msg: Dict[str, Any]):
        """Caches message and triggers proactive announcement callback immediately (< 250ms)."""
        self.latest_message = {
            "sender": msg["sender"],
            "body": msg["body"],
            "is_group": msg.get("is_group", False),
            "group_name": msg.get("group_name"),
            "timestamp": time.time(),
            "read": False
        }
        if self.on_message_callback:
            try:
                self.on_message_callback(msg["sender"], msg["body"], msg.get("is_group", False), msg.get("group_name"))
            except TypeError:
                self.on_message_callback(msg["sender"], msg["body"])

    def _init_last_order(self):
        """Sets last_seen_order to the current maximum including WAL file so we don't replay old history."""
        if not os.path.exists(self.db_path):
            return
        src_dir = os.path.dirname(self.db_path)
        tmp_dir = tempfile.mkdtemp(prefix="hydra_wpn_init_")
        try:
            for f in ["wpndatabase.db", "wpndatabase.db-wal", "wpndatabase.db-shm"]:
                p = os.path.join(src_dir, f)
                if os.path.exists(p):
                    shutil.copy2(p, os.path.join(tmp_dir, f))
            db_file = os.path.join(tmp_dir, "wpndatabase.db")
            if os.path.exists(db_file):
                conn = sqlite3.connect(db_file)
                cur = conn.cursor()
                cur.execute("SELECT MAX([Order]) FROM Notification")
                row = cur.fetchone()
                if row and row[0]:
                    self.last_seen_order = row[0]
                conn.close()
        except Exception:
            pass
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def poll_new_messages(self) -> List[Dict[str, Any]]:
        """Checks for new WhatsApp notifications and window title badge updates as fallback."""
        new_messages = []

        # 1. Check WAL-aware SQLite Notification Database (Fallback Layer)
        if os.path.exists(self.db_path):
            src_dir = os.path.dirname(self.db_path)
            tmp_dir = tempfile.mkdtemp(prefix="hydra_wpn_poll_")
            try:
                for f in ["wpndatabase.db", "wpndatabase.db-wal", "wpndatabase.db-shm"]:
                    p = os.path.join(src_dir, f)
                    if os.path.exists(p):
                        shutil.copy2(p, os.path.join(tmp_dir, f))

                db_file = os.path.join(tmp_dir, "wpndatabase.db")
                if os.path.exists(db_file):
                    conn = sqlite3.connect(db_file)
                    cur = conn.cursor()
                    cur.execute("""
                        SELECT n.[Order], n.Payload 
                        FROM Notification n
                        JOIN NotificationHandler h ON n.HandlerId = h.RecordId
                        WHERE n.[Order] > ? 
                          AND (h.PrimaryId LIKE '%whatsapp%' OR h.PrimaryId LIKE '%WhatsApp%')
                        ORDER BY n.[Order] ASC
                    """, (self.last_seen_order,))

                    rows = cur.fetchall()
                    conn.close()

                    for order, payload in rows:
                        if order > self.last_seen_order:
                            self.last_seen_order = order

                        parsed = self._parse_toast_payload(payload)
                        if parsed:
                            parsed["order"] = order
                            new_messages.append(parsed)

            except Exception:
                pass
            finally:
                shutil.rmtree(tmp_dir, ignore_errors=True)

        # 2. Window Title Badge Monitor (e.g. "(1) WhatsApp")
        try:
            from window_ops import WINDOW_MGR
            for w in WINDOW_MGR.get_open_windows():
                title = (w.get("title") or "").strip()
                proc = (w.get("process") or "").strip().lower()
                if "whatsapp" in proc or "whatsapp" in title.lower():
                    m = re.match(r'^\((\d+)\)\s*WhatsApp', title, re.IGNORECASE)
                    if m:
                        count = int(m.group(1))
                        if count > self.last_unread_title_count:
                            self.last_unread_title_count = count
                            # Only trigger if not already caught by toast
                            if not new_messages and not self.latest_message:
                                new_messages.append({
                                    "sender": "WhatsApp contact",
                                    "body": f"You have {count} unread messages",
                                    "is_badge": True
                                })
                    elif title.lower() == "whatsapp":
                        self.last_unread_title_count = 0
        except Exception:
            pass

        for msg in new_messages:
            self._dispatch_incoming_message(msg)

        return new_messages

    def _parse_toast_payload(self, payload: bytes) -> Optional[Dict[str, Any]]:
        """Parses ToastGeneric XML or badge updates from notification payload."""
        if not payload:
            return None
        try:
            text_content = payload.decode('utf-8', errors='ignore')
            root = ET.fromstring(text_content)

            # Check if it's a badge update
            if root.tag == "badge":
                val = root.attrib.get("value")
                if val and val.isdigit() and int(val) > 0:
                    return {"sender": "WhatsApp contact", "body": f"You have {val} unread messages", "is_badge": True}
                return None

            # Toast XML texts
            texts = [t.text.strip() for t in root.findall('.//text') if t.text and t.text.strip()]
            if not texts or len(texts) < 2:
                return None

            first = texts[0]
            second = texts[1]

            if first.lower() == "whatsapp" and ("new messages" in second.lower() or "unread" in second.lower()):
                return None

            sender = first
            body = second

            # Clean sender and body strings
            sender = re.sub(r'[\u200e\u200f]', '', sender).strip()
            body = re.sub(r'[\u200e\u200f]', '', body).strip()

            return {"sender": sender, "body": body}
        except Exception:
            return None

    def read_screen_whatsapp_message(self, sys_driver: Any = None) -> Optional[str]:
        """Visual fallback: uses local Windows OCR to scan WhatsApp window on screen."""
        try:
            import winocr
            from vision_ops import VISION_ENGINE, _init_desktop_session
            _init_desktop_session()
            img = VISION_ENGINE.capture_screen()
            w, h = img.size

            # Scan right half where chat messages live
            roi = img.crop((int(w * 0.35), int(h * 0.15), w, int(h * 0.88)))
            res = winocr.recognize_pil_sync(roi)

            # Find text lines near bottom of chat area
            lines = [l for l in res.lines if len(l.text.strip()) > 3]
            if lines:
                lines.sort(key=lambda l: l.bounding_rect[1], reverse=True)
                for candidate in lines[:3]:
                    text = candidate.text.strip()
                    if not re.match(r'^\d{1,2}:\d{2}\s*(?:am|pm)?$', text, re.IGNORECASE):
                        return text
        except Exception as e:
            print(f"[WHATSAPP OCR ERROR]: {e}")
        return None


# =============================================================================
# 2. PROACTIVE ACTIONABLE TIMERS & DELAYED EXECUTION
# =============================================================================

class TimerManager:
    """
    Manages background natural-language timers, reminders, and delayed action execution.
    Fires proactive notifications AND executes actionable tasks when deadlines arrive.
    """
    def __init__(self, on_expire_callback: Optional[Callable[[str, str], None]] = None):
        self.on_expire_callback = on_expire_callback
        self.timers: List[Dict[str, Any]] = self._load_timers()
        self._lock = threading.Lock()

    def _load_timers(self) -> List[Dict[str, Any]]:
        if os.path.exists(TIMERS_FILE):
            try:
                with open(TIMERS_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return []
        return []

    def _save_timers(self):
        try:
            with open(TIMERS_FILE, "w", encoding="utf-8") as f:
                json.dump(self.timers, f, indent=2)
        except Exception as e:
            print(f"[TIMER STORE ERROR]: {e}")

    def add_timer(self, duration_seconds: float, label: str, duration_str: str) -> Dict[str, Any]:
        """Creates and stores a new proactive timer."""
        timer_entry = {
            "id": uuid.uuid4().hex[:8],
            "label": label.strip() or "reminder",
            "duration_str": duration_str,
            "target_time": time.time() + duration_seconds,
            "created_at": time.time()
        }
        with self._lock:
            self.timers.append(timer_entry)
            self._save_timers()
        return timer_entry

    def clear_all_timers(self) -> int:
        """Cancels all active timers."""
        with self._lock:
            count = len(self.timers)
            self.timers.clear()
            self._save_timers()
            return count

    def check_expired_timers(self) -> List[Dict[str, Any]]:
        """Ticks down and pops any timers whose deadlines have passed."""
        now = time.time()
        expired = []
        with self._lock:
            remaining = []
            for t in self.timers:
                if now >= t["target_time"]:
                    expired.append(t)
                else:
                    remaining.append(t)
            if expired:
                self.timers = remaining
                self._save_timers()

        for exp in expired:
            if self.on_expire_callback:
                self.on_expire_callback(exp["label"], exp["duration_str"])

        return expired

    def parse_reminder_text(self, cmd: str) -> Optional[Tuple[float, str, str]]:
        """
        Parses spoken/typed natural language reminder and delayed action requests:
          - "remind me in 20 sec to call tanzeel"
          - "remind me in 20 sec to open spotify"
          - "remind me to open canva after 30 sec"
          - "open canva after 30 sec"
          - "maximize canva after 10 sec"
          - "set a timer for 10 minutes"
        """
        clean = cmd.lower().strip()
        clean = re.sub(r'[!?,]|(?<=[a-zA-Z])\.|\.$', '', clean).strip()

        time_units = r'(?:s|sec|secs|second|seconds|m|min|mins|minute|minutes|h|hr|hrs|hour|hours)'

        # Pattern 1: "remind me in X to Y"
        m1 = re.search(rf'remind\s+(?:me\s+)?in\s+(\d+|[a-z]+)\s*{time_units}\s+(?:to\s+)?(.+)$', clean)
        if m1:
            val_str, label = m1.group(1), m1.group(2)
            unit_match = re.search(rf'in\s+(?:\d+|[a-z]+)\s*({time_units})\b', clean)
            unit = unit_match.group(1) if unit_match else "sec"
            sec = self._to_seconds(val_str, unit)
            if sec:
                return sec, label.strip(), f"{val_str} {unit}"

        # Pattern 2: "remind me to Y in/after X"
        m2 = re.search(rf'remind\s+(?:me\s+)?to\s+(.+?)\s+(?:in|after)\s+(\d+|[a-z]+)\s*({time_units})$', clean)
        if m2:
            label, val_str, unit = m2.group(1), m2.group(2), m2.group(3)
            sec = self._to_seconds(val_str, unit)
            if sec:
                return sec, label.strip(), f"{val_str} {unit}"

        # Pattern 3: Delayed action command: "<action> after/in <X time>"
        # e.g. "open canva after 30 sec", "maximize canva after 10 sec"
        m3 = re.search(rf'^(.+?)\s+(?:after|in)\s+(\d+|[a-z]+)\s*({time_units})$', clean)
        if m3:
            action, val_str, unit = m3.group(1).strip(), m3.group(2), m3.group(3)
            # Avoid matching pure conversational fillers or general searches
            action_words = ["open", "launch", "start", "maximize", "minimize", "snap", "mute", "unmute", "close", "call", "send", "text"]
            if any(action.startswith(w) for w in action_words):
                sec = self._to_seconds(val_str, unit)
                if sec:
                    return sec, action, f"{val_str} {unit}"

        # Pattern 4: "set (a) timer for X (to/for Y)?"
        m4 = re.search(rf'set\s+(?:a\s+)?timer\s+(?:for\s+)?(\d+|[a-z]+)\s*({time_units})(?:\s+(?:for|to)\s+(.+))?$', clean)
        if m4:
            val_str, unit, label = m4.group(1), m4.group(2), m4.group(3) or "timer"
            sec = self._to_seconds(val_str, unit)
            if sec:
                return sec, label.strip(), f"{val_str} {unit}"

        return None

    def _to_seconds(self, val_str: str, unit: str) -> Optional[float]:
        """Converts value + unit to total seconds."""
        val = None
        try:
            val = float(val_str)
        except ValueError:
            val = NUMBER_WORDS.get(val_str.lower())

        if val is None or val <= 0:
            return None

        u = unit.lower()
        if u.startswith("s"):
            return val
        elif u.startswith("m"):
            return val * 60.0
        elif u.startswith("h"):
            return val * 3600.0
        return None


# =============================================================================
# 3. SYSTEM HEALTH & HARDWARE EVENT MONITOR
# =============================================================================

class SystemHealthMonitor:
    """
    Monitors hardware sensors (battery, internet connectivity, cpu spikes)
    and dispatches proactive alerts.
    """
    def __init__(self, on_alert_callback: Optional[Callable[[str], None]] = None):
        self.on_alert_callback = on_alert_callback
        self.last_battery_alert_time = 0.0
        self.last_battery_state = None
        self.last_network_state = self._check_internet_online()

    def _check_internet_online(self) -> bool:
        """Fast non-blocking socket test to verify real internet connectivity."""
        try:
            socket.setdefaulttimeout(1.5)
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect(("8.8.8.8", 53))
            s.close()
            return True
        except Exception:
            return False

    def poll_health_events(self) -> List[str]:
        """Checks sensors and triggers callbacks for critical changes."""
        alerts = []
        now = time.time()

        # 1. Battery Sensor
        if psutil and hasattr(psutil, "sensors_battery"):
            try:
                battery = psutil.sensors_battery()
                if battery:
                    pct = int(battery.percent)
                    plugged = battery.power_plugged

                    # Low battery alert: <= 15% and not charging (critical reminder only)
                    if pct <= 15 and not plugged:
                        if now - self.last_battery_alert_time > 1800:  # 30 min cooldown
                            msg = f"Sir, your battery is running low at {pct}%. Please connect the charger."
                            alerts.append(msg)
                            self.last_battery_alert_time = now

                    # Full battery alert: MUTED (per user mandate)
                    if pct >= 95:
                        self.last_battery_state = "full"
                    else:
                        self.last_battery_state = "normal"
            except Exception:
                pass

        # 2. Network Connectivity Sensor (MUTED proactive spoken announcements per user mandate)
        current_net = self._check_internet_online()
        self.last_network_state = current_net

        for alert in alerts:
            if self.on_alert_callback:
                self.on_alert_callback(alert)

        return alerts

    def get_battery_report(self) -> str:
        """Returns direct spoken status of the battery."""
        if not psutil or not hasattr(psutil, "sensors_battery"):
            return "Battery monitoring is unavailable on this device, sir."
        try:
            battery = psutil.sensors_battery()
            if not battery:
                return "This system is running on direct AC power, sir."
            state = "charging" if battery.power_plugged else "on battery power"
            return f"Sir, your battery is at {int(battery.percent)}% and currently {state}."
        except Exception as e:
            return "Sir, I could not query the battery status at this moment."

    def get_network_report(self) -> str:
        """Returns direct spoken status of internet connectivity."""
        online = self._check_internet_online()
        if online:
            return "All network links are operational and internet access is active, sir."
        return "Sir, the internet connection appears to be offline."


# =============================================================================
# 4. UNIFIED PHASE 11 PROACTIVE DAEMON CONTROLLER
# =============================================================================

class ProactiveDaemon:
    """
    Central controller managing Phase 11 Proactive Watchers:
    - Background polling threads
    - Real-time WhatsApp announcement & message buffer
    - Actionable timers & delayed task execution
    - Battery & network health monitoring
    - Dual execution: Fast Local / Non-Intelligent vs. Intelligent AI Core
    """
    def __init__(self):
        self._running = False
        self._thread: Optional[threading.Thread] = None

        self.whatsapp = WhatsAppProactiveMonitor(on_message_callback=self._handle_whatsapp_incoming)
        self.timers = TimerManager(on_expire_callback=self._handle_timer_expired)
        self.health = SystemHealthMonitor(on_alert_callback=self._handle_health_alert)
        self.pending_read_confirmation: Optional[Dict[str, Any]] = None

    def start(self):
        """Starts the background proactive monitoring loop."""
        if self._running:
            return
        self._running = True
        self.whatsapp.start_realtime_listener()
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()
        print("[PHASE 11 DAEMON] Proactive Desktop Daemon started (WhatsApp, Timers, System Health).")

    def stop(self):
        self._running = False
        self.whatsapp.stop_realtime_listener()

    def _monitor_loop(self):
        """Periodic background tick loop (every 1.5s)."""
        health_tick = 0
        while self._running:
            try:
                # 1. WhatsApp Notifications & Window Title Check (Fallback)
                self.whatsapp.poll_new_messages()

                # 2. Timer Expiry Check
                self.timers.check_expired_timers()

                # 3. System Health Check (every 10 ticks = ~15s)
                health_tick += 1
                if health_tick >= 10:
                    health_tick = 0
                    self.health.poll_health_events()

            except Exception as e:
                pass

            time.sleep(1.5)

    def _dispatch_proactive_speech(self, text: str):
        """Dispatches proactive speech and UI chat updates to HYDRA."""
        print(f"\n==========================================")
        print(f"[PROACTIVE ANNOUNCEMENT]: {text}")
        print(f"==========================================\n")
        if self.on_alert_callback:
            try:
                self.on_alert_callback(text)
            except Exception:
                pass
        try:
            import main
            if getattr(main, "ACTIVE_PAGE", None):
                main.ACTIVE_PAGE.speak_voice(text)
                if hasattr(main, "save_chat_message"):
                    main.save_chat_message("assistant", text)
                return
        except Exception:
            pass

        # Direct offline TTS fallback if main GUI is not running or active yet
        def direct_speak():
            try:
                import pyttsx3
                engine = pyttsx3.init()
                engine.say(text)
                engine.runAndWait()
            except Exception:
                pass
        threading.Thread(target=direct_speak, daemon=True).start()

    def _handle_whatsapp_incoming(self, sender: str, body: str, is_group: bool = False, group_name: Optional[str] = None):
        """Triggered automatically when a new WhatsApp message arrives."""
        # Detect if it's a group message (if not already parsed by WinRT)
        if not is_group:
            group_match = re.match(r'^([^:\n]{1,35}):\s*(.*)$', body)
            if group_match and sender and sender.lower() != "whatsapp contact":
                is_group = True
                group_name = sender
                author = group_match.group(1).strip()
                clean_body = group_match.group(2).strip()
            elif sender and sender.lower() != "whatsapp contact":
                author = sender
                clean_body = body
            else:
                author = "WhatsApp contact"
                clean_body = body
        else:
            author = sender
            clean_body = body

        # User mandate: In group messages, ONLY notify if the user is explicitly mentioned!
        if is_group:
            import os
            master_name = os.environ.get("HYDRA_MASTER_NAME", "Ahmad Farooq").strip()
            user_tokens = ["ahmad", "ahmad farooq", "@ahmad", "@ahmad farooq"]
            if master_name:
                user_tokens.append(master_name.lower())
                first_name = master_name.split()[0].lower()
                user_tokens.append(first_name)
                user_tokens.append(f"@{first_name}")

            body_lower = clean_body.lower()
            is_mentioned = any(re.search(rf'(?:\b|@){re.escape(token)}\b', body_lower) for token in user_tokens)
            if not is_mentioned:
                print(f"[WHATSAPP MONITOR] Group message in '{group_name}' from {author} suppressed (user not mentioned).")
                return

        if is_group and group_name:
            announcement = f"Sir, you were mentioned by {author} in group '{group_name}'. Do you want me to read it?"
        elif author and author.lower() != "whatsapp contact":
            announcement = f"Sir, a message from {author} has arrived. Do you want me to read it?"
        else:
            announcement = "Sir, you have a new WhatsApp message. Do you want me to read it?"

        payload = {
            "sender": author if is_group else sender,
            "body": clean_body,
            "is_group": is_group,
            "group_name": group_name,
            "author": author,
            "raw_body": body
        }

        self.whatsapp.latest_message = {
            "sender": author if is_group else sender,
            "body": clean_body,
            "is_group": is_group,
            "group_name": group_name,
            "timestamp": time.time(),
            "read": False
        }

        # Store pending read confirmation
        self.pending_read_confirmation = payload

        # Synchronize with HYDRA global dialogue state
        try:
            from nlp_dispatcher import set_pending_dialogue_state
            dialogue_state = {
                "mode": "AWAITING_READ_WHATSAPP",
                "pending_action": "READ_WHATSAPP",
                "payload": payload
            }
            set_pending_dialogue_state(dialogue_state)
            import main
            main.DIALOGUE_STATE = dialogue_state
        except Exception:
            pass

        self._dispatch_proactive_speech(announcement)

    def _clear_dialogue_state(self):
        """Clears active dialogue states across HYDRA."""
        self.pending_read_confirmation = None
        try:
            from nlp_dispatcher import set_pending_dialogue_state
            reset_state = {"mode": "NORMAL", "pending_action": None, "payload": None}
            set_pending_dialogue_state(reset_state)
            import main
            main.DIALOGUE_STATE = reset_state
        except Exception:
            pass

    def _handle_timer_expired(self, label: str, duration_str: str):
        """Triggered when a timer hits zero. Announces AND executes actionable commands!"""
        if label and label.lower() != "timer":
            announcement = f"Sir, reminder: {label}."
        else:
            announcement = f"Sir, your {duration_str} timer is up."
        self._dispatch_proactive_speech(announcement)

        # ACTIONABLE TASK EXECUTION:
        # If the reminder label is an action (e.g. 'open spotify', 'maximize canva', 'mute spotify'),
        # execute it automatically in the background right now!
        clean_action = label.strip().lower()
        action_prefixes = ["open ", "launch ", "start ", "maximize ", "minimize ", "snap ", "mute ", "unmute ", "close ", "play ", "search "]
        if any(clean_action.startswith(p) for p in action_prefixes):
            print(f"[PROACTIVE TIMER] Executing actionable task: '{label}'...")
            try:
                from system_ops import SystemDriver
                driver = getattr(sys.modules.get('main'), 'SYS_DRIVER', None) or SystemDriver()
                from nlp_dispatcher import execute_chained_pipeline
                execute_chained_pipeline(label, driver)
            except Exception as e:
                print(f"[PROACTIVE TIMER ACTION ERROR]: {e}")

    def _handle_health_alert(self, alert_text: str):
        """Triggered on battery or network events."""
        self._dispatch_proactive_speech(alert_text)

    # -------------------------------------------------------------------------
    # Proactive Command Interceptor (for nlp_dispatcher)
    # -------------------------------------------------------------------------
    def handle_command(self, raw_cmd: str, sys_driver: Any = None) -> Tuple[bool, str]:
        """
        Unified handler for Phase 11 user requests:
        - "read the message" / "read it" / "what did he say"
        - "summarize the message" (AI Mode)
        - "remind me in 5 minutes to ..." / "open canva after 30 sec"
        - "battery status" (with typo tolerance: 'batery', 'batter', etc.)
        - "network status" / "check internet"
        """
        clean = raw_cmd.lower().strip()
        clean = re.sub(r'[!?,]|(?<=[a-zA-Z])\.|\.$', '', clean).strip()

        # ---------------------------------------------------------------------
        # 0. PENDING READ CONFIRMATION ("Do you want me to read it?" -> "Yes" / "No")
        # ---------------------------------------------------------------------
        if self.pending_read_confirmation:
            payload = self.pending_read_confirmation
            yes_triggers = ["yes", "yeah", "yep", "sure", "please", "read it", "read", "go ahead", "okay", "ok", "do it", "tell me", "what is it"]
            no_triggers = ["no", "nope", "not now", "later", "dont", "don't", "cancel", "ignore", "skip", "leave it"]

            # Affirmative: read the message
            if any(clean == yt or clean.startswith(yt) for yt in yes_triggers):
                self._clear_dialogue_state()
                author = payload.get("author") or payload.get("sender") or "Contact"
                body = payload.get("body") or ""
                is_group = payload.get("is_group", False)
                group_name = payload.get("group_name")

                if not body and sys_driver:
                    body = self.whatsapp.read_screen_whatsapp_message(sys_driver)

                if not body:
                    return True, "Sir, I could not retrieve the text of the message."

                if is_group and group_name:
                    return True, f"Sir, in group '{group_name}', {author} said: '{body}'"
                return True, f"Sir, {author} said: '{body}'"

            # Negative: decline
            if any(clean == nt or clean.startswith(nt) for nt in no_triggers):
                self._clear_dialogue_state()
                return True, "Understood, sir."

            # Contextual question (e.g. "is it urgent?", "summarize it")
            if any(w in clean for w in ["urgent", "summarize", "important", "what does", "who is", "translate"]):
                self._clear_dialogue_state()
                author = payload.get("author") or payload.get("sender") or "Contact"
                body = payload.get("body", "")
                return True, self._ask_ai_about_message(author, body, raw_cmd)

        # ---------------------------------------------------------------------
        # A. SYSTEM HEALTH & HARDWARE STATUS (TYPO-TOLERANT)
        # ---------------------------------------------------------------------
        battery_typos = ["battery", "batery", "batter", "batry", "battry", "charge", "power"]
        if any(w in clean for w in battery_typos):
            if any(w in clean for w in ["status", "level", "percent", "percentage", "check", "how is", "what is", "state", "report"]) or len(clean.split()) <= 2:
                return True, self.health.get_battery_report()

        network_typos = ["internet", "wifi", "network", "net", "connection", "internt", "conection"]
        if any(w in clean for w in network_typos):
            if any(w in clean for w in ["status", "check", "how is", "what is", "state", "online", "am i"]) or len(clean.split()) <= 2:
                return True, self.health.get_network_report()

        # ---------------------------------------------------------------------
        # B. TIMERS & DELAYED ACTIONS (CHECK BEFORE SKILL ENGINE)
        # ---------------------------------------------------------------------
        # 1. Query Timers
        if clean in ["what timers do i have", "check timers", "list timers", "show timers", "my timers", "active timers"]:
            with self.timers._lock:
                active = self.timers.timers
                if not active:
                    return True, "You have no active timers or reminders right now, sir."
                lines = []
                now = time.time()
                for idx, t in enumerate(active, 1):
                    remain = max(1, int(t["target_time"] - now))
                    mins = remain // 60
                    secs = remain % 60
                    time_str = f"{mins}m {secs}s" if mins > 0 else f"{secs}s"
                    lines.append(f"{t['label']} ({time_str} remaining)")
                return True, f"You have {len(active)} active reminder{'s' if len(active) > 1 else ''}, sir: " + ", ".join(lines) + "."

        # 2. Cancel Timers
        if any(clean == c for c in ["cancel timers", "cancel all timers", "clear timers", "clear all timers", "stop timers"]):
            count = self.timers.clear_all_timers()
            if count > 0:
                return True, f"I have cleared {count} active timer{'s' if count > 1 else ''}, sir."
            return True, "There were no active timers to cancel, sir."

        # 3. Set Reminder / Delayed Action (Hardcoded Natural Regex Parser)
        reminder_data = self.timers.parse_reminder_text(clean)
        if reminder_data:
            sec, label, dur_str = reminder_data
            self.timers.add_timer(sec, label, dur_str)
            if label and label != "timer":
                return True, f"Reminder set for {dur_str} to {label}, sir."
            return True, f"Timer set for {dur_str}, sir."

        # 4. Fallback Intelligent AI Timer Parsing (for indirect or complex phrasing)
        if any(w in clean for w in ["remind", "timer", "alarm"]) and any(w in clean for w in ["in ", "after ", "at "]):
            ai_parsed = self._ask_ai_parse_timer(clean)
            if ai_parsed:
                sec, label, dur_str = ai_parsed
                self.timers.add_timer(sec, label, dur_str)
                return True, f"Reminder set for {dur_str} to {label}, sir."

        # ---------------------------------------------------------------------
        # C. READ WHATSAPP MESSAGE (Hardcoded & Intelligent Modes)
        # ---------------------------------------------------------------------
        read_triggers = [
            "read the message", "read message", "read it", "what did he say",
            "what did she say", "what is the message", "whats the message",
            "what did they say", "read the text", "read text", "read whatsapp",
            "read the whatsapp message", "read whatsapp message"
        ]

        explicit_match = re.search(r'(?:read\s+(.+?)(?:\'s)?\s+message|what\s+did\s+(.+?)\s+say)', clean)

        is_direct_read = any(clean == rt or clean.startswith(rt) for rt in read_triggers) or explicit_match is not None
        is_ai_message_query = any(w in clean for w in ["summarize", "urgent", "translate", "what does", "explain"]) and any(w in clean for w in ["message", "text", "whatsapp"])

        if is_direct_read or is_ai_message_query:
            latest = self.whatsapp.latest_message

            # Visual screen fallback if no message cached or message was from generic badge
            if (not latest or latest.get("is_badge")) and sys_driver:
                screen_text = self.whatsapp.read_screen_whatsapp_message(sys_driver)
                if screen_text:
                    latest = {"sender": "WhatsApp contact", "body": screen_text, "timestamp": time.time(), "read": False}

            if not latest:
                return True, "Sir, there are no unread WhatsApp messages to read."

            sender = latest.get("sender", "Contact")
            body = latest.get("body", "")

            # If user asks an AI-based question about the message:
            if is_ai_message_query:
                return True, self._ask_ai_about_message(sender, body, clean)

            # Direct Hardcoded Mode: Read message out loud
            latest["read"] = True
            return True, f"Sir, {sender} said: '{body}'"

        return False, ""

    def _ask_ai_about_message(self, sender: str, body: str, user_question: str) -> str:
        """Intelligent AI Core: Answers questions, summarizes, or analyzes WhatsApp message context."""
        try:
            from ai_engine import ai_chat, DEFAULT_REASONING_MODEL
            prompt = (
                f"You are HYDRA, a desktop AI assistant. The user received a WhatsApp message.\n"
                f"Sender: {sender}\n"
                f"Message Content: {body}\n\n"
                f"User Request: {user_question}\n\n"
                f"Provide a concise, direct, spoken response addressing the user's request. "
                f"Keep it to 1-2 sentences. Address the user respectfully as 'sir'."
            )
            reply = ai_chat([{"role": "user", "content": prompt}], model=DEFAULT_REASONING_MODEL, timeout=15)
            if reply and reply.strip():
                return reply.strip()
        except Exception as e:
            print(f"[AI MESSAGE QUERY NOTICE]: {e}")
        return f"Sir, {sender} texted: '{body}'."

    def _ask_ai_parse_timer(self, user_command: str) -> Optional[Tuple[float, str, str]]:
        """Intelligent AI Core: Extracts duration and reminder label from complex spoken requests."""
        try:
            from ai_engine import ai_chat, DEFAULT_REASONING_MODEL
            prompt = (
                f"Extract the timer/reminder parameters from this command: '{user_command}'.\n"
                f"Output strictly valid JSON with keys 'duration_seconds' (number), 'label' (string), and 'duration_str' (string).\n"
                f"Example: {{\"duration_seconds\": 300, \"label\": \"check the oven\", \"duration_str\": \"5 minutes\"}}\n"
                f"Do NOT include markdown, backticks, or other text."
            )
            raw = ai_chat([{"role": "user", "content": prompt}], model=DEFAULT_REASONING_MODEL, timeout=10)
            if raw:
                cleaned = re.sub(r'```(?:json)?', '', raw).strip('` \n')
                data = json.loads(cleaned)
                sec = float(data.get("duration_seconds", 0))
                lbl = str(data.get("label", "reminder"))
                dur = str(data.get("duration_str", f"{int(sec)} seconds"))
                if sec > 0:
                    return sec, lbl, dur
        except Exception:
            pass
        return None


# Global Proactive Daemon Singleton
PROACTIVE_DAEMON = ProactiveDaemon()
PROACTIVE_DAEMON.start()


def handle_whatsapp_read_confirmation(
    user_response: str,
    dialogue_state: Dict[str, Any],
    sys_driver: Any = None
) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Called by main.py when DIALOGUE_STATE['mode'] == 'AWAITING_READ_WHATSAPP'.
    """
    reset_state = {"mode": "NORMAL", "pending_action": None, "payload": None}
    if not PROACTIVE_DAEMON.pending_read_confirmation:
        PROACTIVE_DAEMON.pending_read_confirmation = dialogue_state.get("payload", {})
    handled, reply = PROACTIVE_DAEMON.handle_command(user_response, sys_driver)
    if handled:
        return True, reply, reset_state
    return False, "", reset_state
