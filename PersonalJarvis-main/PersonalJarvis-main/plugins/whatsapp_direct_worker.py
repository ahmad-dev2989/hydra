"""
HYDRA Direct WhatsApp Background Worker (Phase 1.1)
Provides reliable background and desktop chat dispatching, zero dummy data,
and direct contact chat search without requiring manual phone numbers.

Supported Modes:
1. Direct Chat Search & Send: Opens WhatsApp, searches contact name, and sends message directly without requiring a phone number.
2. Verified Phone Dispatch: Dispatches directly via protocol if a verified real phone number exists.
3. Unread Message Monitoring: Real unread status checking without mock/synthesized responses.
"""

import os
import sys
import re
import time
import json
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Tuple, List, Optional
from .base_connector import BaseDirectWorker
from .vault_manager import VAULT_MGR

CURRENT_DIR = Path(__file__).resolve().parent
ROOT_DIR = CURRENT_DIR.parent
SENT_LOG_FILE = ROOT_DIR / "data" / "vault" / "whatsapp_sent.json"
UNREAD_CACHE_FILE = ROOT_DIR / "data" / "vault" / "whatsapp_unread.json"

CONTACTS_CANDIDATES = [
    ROOT_DIR / "contacts.json",
    ROOT_DIR.parent / "contacts.json",
    ROOT_DIR / "PersonalJarvis-main" / "contacts.json",
    ROOT_DIR / "data" / "contacts.json",
    ROOT_DIR / "data" / "vault" / "contacts.json"
]


def _is_dummy_phone(phone: str) -> bool:
    """Detects dummy placeholder phone numbers like +923001234567, 7654321, etc."""
    clean = re.sub(r'[^0-9]', '', str(phone or ''))
    if len(clean) < 7:
        return True
    dummy_patterns = ["1234567", "7654321", "1112233", "2233445", "0000000", "9999999"]
    if any(p in clean for p in dummy_patterns):
        return True
    return False


def _resolve_contact(name_or_number: str) -> Tuple[str, str]:
    """
    Resolves contact name and phone number from candidate contacts.json files.
    Strictly discards any dummy/mock numbers.
    Returns:
        (resolved_name: str, phone_number: str)
    """
    clean = name_or_number.strip()
    if not clean:
        return "", ""

    # If already a valid phone number (not dummy)
    if re.match(r'^\+?[0-9\s\-()]{7,}$', clean):
        clean_num = re.sub(r'[^0-9+]', '', clean)
        if not _is_dummy_phone(clean_num):
            return clean_num, clean_num
        return "", ""

    low_name = clean.lower()
    for cp in CONTACTS_CANDIDATES:
        if cp.exists():
            try:
                with open(cp, "r", encoding="utf-8") as f:
                    cdata = json.load(f)
                for k, v in cdata.items():
                    if k.lower() == low_name and v:
                        phone = ""
                        if isinstance(v, dict):
                            phone = str(v.get("phone", "")).strip()
                        elif isinstance(v, str) and re.match(r'^\+?[0-9\s\-()]{7,}$', v):
                            phone = v.strip()
                        if phone and not _is_dummy_phone(phone):
                            return k.title(), phone
            except Exception:
                pass

    return clean.title(), ""


def _save_contact_phone(name: str, phone: str):
    """Saves or updates verified contact phone number across candidate contacts files."""
    clean_n = name.strip().lower()
    clean_p = phone.strip()
    if not clean_n or not clean_p or _is_dummy_phone(clean_p):
        return
    for cp in CONTACTS_CANDIDATES:
        try:
            cdata = {}
            if cp.exists():
                try:
                    with open(cp, "r", encoding="utf-8") as f:
                        cdata = json.load(f)
                except Exception:
                    cdata = {}
            if isinstance(cdata.get(clean_n), dict):
                cdata[clean_n]["phone"] = clean_p
            else:
                cdata[clean_n] = {"phone": clean_p}
            cp.parent.mkdir(parents=True, exist_ok=True)
            with open(cp, "w", encoding="utf-8") as f:
                json.dump(cdata, f, indent=2)
        except Exception:
            pass


def parse_whatsapp_command(raw_text: str) -> Tuple[str, str]:
    """
    Extracts recipient contact name and message body from natural language queries.
    Examples:
        - 'send whatsapp message to tanzeel' -> ('tanzeel', '')
        - 'send message to tanzeel saying this' -> ('tanzeel', 'this')
        - 'send message to tanzeel saying hello' -> ('tanzeel', 'hello')
        - 'text ali on whatsapp let us meet' -> ('ali', 'let us meet')
        - 'open chat with tanzeel on whatsapp' -> ('tanzeel', '')
        - 'open chat with tanzeel' -> ('tanzeel', '')
    """
    text = raw_text.strip()
    cleaned = re.sub(r'\s+(?:on|via|through|using)\s+whatsapp\b', '', text, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r'\bwhatsapp\b', '', cleaned, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r'\s+', ' ', cleaned)

    # 0. 'open chat (with)? <contact>'
    m_open = re.search(
        r'\bopen\s+chat\s+(?:with\s+)?([a-zA-Z0-9_+]+)',
        cleaned,
        re.IGNORECASE
    )
    if m_open:
        cand = m_open.group(1).strip()
        if cand.lower() not in ['a', 'the', 'chat', 'someone']:
            return cand, ''

    # 1. 'send (a)? (message)? to <contact>' (strictly without body)
    m_to_only = re.search(
        r'(?:send\s+)?(?:a\s+)?(?:message|msg|text)?\s*(?:to\s+)([a-zA-Z0-9_+]+)$',
        cleaned,
        re.IGNORECASE
    )
    if m_to_only:
        contact_cand = m_to_only.group(1).strip()
        if contact_cand.lower() not in ['message', 'text', 'someone', 'contact']:
            return contact_cand, ''

    # 2. 'send message to <contact> saying <message>'
    m1 = re.search(
        r'(?:send\s+)?(?:a\s+)?(?:message|msg|text)\s+to\s+([a-zA-Z0-9_+]+)\s*(?:saying|that|with|:|,)\s*(.+)',
        cleaned,
        re.IGNORECASE
    )
    if m1:
        return m1.group(1).strip(), m1.group(2).strip()

    m2 = re.search(
        r'\b(?:tell|message|msg|text)\s+(?:to\s+)?([a-zA-Z0-9_+]+)\s*(?:saying|that|with|:|,)\s*(.+)',
        cleaned,
        re.IGNORECASE
    )
    if m2:
        return m2.group(1).strip(), m2.group(2).strip()

    # 3. 'tell/text/message <contact>' (strictly without body)
    m_verb_to = re.search(
        r'\b(?:tell|message|msg|text)\s+([a-zA-Z0-9_+]+)$',
        cleaned,
        re.IGNORECASE
    )
    if m_verb_to:
        contact_cand = m_verb_to.group(1).strip()
        if contact_cand.lower() not in ['a', 'the', 'message', 'text', 'someone']:
            return contact_cand, ''

    # 4. 'text <contact> <message>'
    m3 = re.search(
        r'\b(?:tell|message|msg|text)\s+(?:to\s+)?([a-zA-Z0-9_+]+)\s+(.+)',
        cleaned,
        re.IGNORECASE
    )
    if m3:
        cand_c = m3.group(1).strip()
        cand_b = m3.group(2).strip()
        if cand_c.lower() not in ['a', 'the', 'message', 'msg', 'text', 'to']:
            return cand_c, cand_b

    return "", ""


class WhatsAppDirectWorker(BaseDirectWorker):
    """
    Direct WhatsApp background and foreground chat worker.
    Supports headless protocol dispatch when verified phone is known,
    and direct WhatsApp desktop chat search when phone is unknown.
    """
    app_id = "whatsapp"
    app_name = "WhatsApp"

    def is_ready(self) -> bool:
        """Returns True if WhatsApp is paired in Vault or running locally."""
        return VAULT_MGR.is_connected(self.app_id) or bool(VAULT_MGR.load_whatsapp_session())

    def get_account_email(self) -> str:
        acc = VAULT_MGR.get_account_email(self.app_id)
        if not acc:
            sess = VAULT_MGR.load_whatsapp_session()
            if sess:
                acc = sess.get("account")
        return acc or "Connected WhatsApp Device"

    def _log_sent_message(self, entry: Dict[str, Any]):
        """Persists dispatched message entry into audit history."""
        try:
            SENT_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            sent_history = []
            if SENT_LOG_FILE.exists():
                try:
                    with open(SENT_LOG_FILE, "r", encoding="utf-8") as f:
                        sent_history = json.load(f)
                except Exception:
                    sent_history = []
            sent_history.append(entry)
            with open(SENT_LOG_FILE, "w", encoding="utf-8") as f:
                json.dump(sent_history, f, indent=2)
        except Exception as e:
            print(f"[WHATSAPP DIRECT NOTICE] Could not save sent log: {e}")

    # =========================================================================
    # Phase 1.1: Direct Chat Search & Send (Zero Fake Numbers, Opens Real Chat)
    # =========================================================================

    def open_chat_and_send(self, contact_name: str, message: str = "") -> Tuple[bool, str, Dict[str, Any]]:
        """
        Opens WhatsApp, uses universal shortcut to search for contact name directly,
        opens the chat, and sends the message without requiring a phone number.
        """
        display_name = contact_name.strip().title()
        clean_msg = message.strip()
        if not display_name:
            return False, "Sir, who would you like to message on WhatsApp?", {}

        try:
            from window_ops import WINDOW_MGR
        except ImportError:
            WINDOW_MGR = None

        # 1. Focus WhatsApp window
        focused = False
        if WINDOW_MGR:
            focused, _ = WINDOW_MGR.switch_to("whatsapp")

        if not focused:
            try:
                os.startfile("whatsapp:")
                time.sleep(1.2)
                if WINDOW_MGR:
                    focused, _ = WINDOW_MGR.switch_to("whatsapp")
            except Exception:
                pass

        if not focused:
            try:
                import pyautogui
                pyautogui.press("win")
                time.sleep(0.3)
                pyautogui.write("whatsapp", interval=0.02)
                time.sleep(0.4)
                pyautogui.press("enter")
                time.sleep(1.5)
                if WINDOW_MGR:
                    focused, _ = WINDOW_MGR.switch_to("whatsapp")
            except Exception:
                pass

        # 2. Search contact in WhatsApp
        try:
            import pyautogui
            import pyperclip
            pyautogui.FAILSAFE = False

            time.sleep(0.4)

            # Universal WhatsApp desktop shortcut to focus search / new chat (Ctrl+F)
            pyautogui.hotkey("ctrl", "f")
            time.sleep(0.2)

            # Clear search box
            pyautogui.hotkey("ctrl", "a")
            time.sleep(0.05)
            pyautogui.press("backspace")
            time.sleep(0.1)

            # Type contact name
            pyautogui.write(contact_name, interval=0.03)
            time.sleep(0.7)  # Let WhatsApp filter contacts

            # Press Enter to open the top filtered chat
            pyautogui.press("enter")
            time.sleep(0.4)

            entry = {
                "id": f"wa_msg_{int(time.time() * 1000)}",
                "timestamp": time.time(),
                "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "recipient_name": display_name,
                "phone": "",
                "message": clean_msg,
                "method": "direct_chat_search",
                "status": "SENT_VIA_CHAT" if clean_msg else "CHAT_OPENED"
            }

            if clean_msg:
                # Use clipboard paste to handle spaces, unicode, punctuation and emojis safely
                pyperclip.copy(clean_msg)
                pyautogui.hotkey("ctrl", "v")
                time.sleep(0.2)
                pyautogui.press("enter")
                time.sleep(0.2)

                self._log_sent_message(entry)
                speech = f"Sir, I have opened the chat with {display_name} on WhatsApp and sent your message."
                return True, speech, entry
            else:
                speech = f"Sir, I have opened the chat with {display_name} on WhatsApp for you."
                return True, speech, entry

        except Exception as e:
            return False, f"Sir, I encountered an error searching for {display_name} in WhatsApp: {e}", {}

    def send_message(self, contact_name_or_number: str, message: str) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Dispatches WhatsApp message:
        - If phone is verified and real: sends directly.
        - If phone is not known: opens chat by contact name in WhatsApp and sends message.
        """
        resolved_name, phone = _resolve_contact(contact_name_or_number)
        display_name = resolved_name if resolved_name else contact_name_or_number.strip().title()
        clean_msg = message.strip()

        # If phone is not known / cannot access direct number:
        # Fall back to opening chat by name directly on WhatsApp without using any dummy number
        if not phone:
            return self.open_chat_and_send(display_name, clean_msg)

        # If phone IS verified and real
        clean_phone = re.sub(r'[^0-9+]', '', phone)
        try:
            encoded_msg = urllib.parse.quote(clean_msg) if clean_msg else ""
            uri = f"whatsapp://send?phone={clean_phone}&text={encoded_msg}"
            os.startfile(uri)
            time.sleep(1.0)
            if clean_msg:
                try:
                    import pyautogui
                    time.sleep(0.4)
                    pyautogui.press("enter")
                except Exception:
                    pass

            entry = {
                "id": f"wa_msg_{int(time.time() * 1000)}",
                "timestamp": time.time(),
                "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "recipient_name": display_name,
                "phone": clean_phone,
                "message": clean_msg,
                "method": "verified_phone_protocol",
                "status": "DISPATCHED"
            }
            self._log_sent_message(entry)
            speech = f"Sir, I have dispatched your WhatsApp message to {display_name}."
            return True, speech, entry

        except Exception:
            # Fall back to opening chat by name
            return self.open_chat_and_send(display_name, clean_msg)

    # =========================================================================
    # Phase 1.2: Real-Time Incoming Notifications & Unread Messages Monitoring
    # =========================================================================

    def _fetch_real_notifications(self, max_count: int = 15) -> List[Dict[str, Any]]:
        """
        Directly extracts genuine incoming WhatsApp notifications from Windows Notification Store (wpndatabase.db)
        without opening WhatsApp, stealing screen focus, or touching keyboard/mouse.
        """
        import sqlite3
        import shutil
        import tempfile
        import xml.etree.ElementTree as ET
        from datetime import datetime, timedelta

        db_path = Path(os.environ.get('LOCALAPPDATA', '')) / 'Microsoft' / 'Windows' / 'Notifications' / 'wpndatabase.db'
        if not db_path.exists():
            return []

        temp_dir = tempfile.gettempdir()
        temp_db = Path(temp_dir) / f'wpndatabase_wa_{os.getpid()}.db'

        messages = []
        seen = set()

        try:
            shutil.copy2(db_path, temp_db)
            conn = sqlite3.connect(str(temp_db))
            cursor = conn.cursor()

            cursor.execute("""
                SELECT h.PrimaryId, n.Payload, n.ArrivalTime 
                FROM Notification n 
                LEFT JOIN NotificationHandler h ON n.HandlerId = h.RecordId
                WHERE (h.PrimaryId LIKE '%WhatsAppDesktop%' OR h.PrimaryId LIKE '%YourPhoneNotifications_com.whatsapp%')
                ORDER BY n.ArrivalTime DESC
                LIMIT 50
            """)
            rows = cursor.fetchall()

            for h_id, payload_bytes, arrival_filetime in rows:
                if not payload_bytes:
                    continue
                try:
                    xml_str = payload_bytes.decode('utf-8', errors='ignore')
                    if '<toast' in xml_str:
                        root = ET.fromstring(xml_str)
                        texts = [elem.text for elem in root.iter('text') if elem.text]
                        if len(texts) >= 2:
                            sender = texts[0].strip()
                            body = texts[1].strip()
                        elif len(texts) == 1:
                            sender = "WhatsApp"
                            body = texts[0].strip()
                        else:
                            continue

                        # Dedup consecutive duplicate notifications
                        dedup_key = f"{sender}_{body}"
                        if dedup_key in seen:
                            continue
                        seen.add(dedup_key)

                        dt_str = ""
                        try:
                            if arrival_filetime:
                                dt = datetime(1601, 1, 1) + timedelta(microseconds=arrival_filetime // 10)
                                dt_str = dt.strftime("%Y-%m-%d %H:%M:%S")
                        except Exception:
                            dt_str = ""

                        messages.append({
                            "sender": sender,
                            "message": body,
                            "timestamp": arrival_filetime,
                            "date": dt_str
                        })
                        if len(messages) >= max_count:
                            break
                except Exception:
                    pass

            conn.close()
        except Exception as e:
            print(f"[WHATSAPP NOTIFICATIONS] Read notice: {e}")

        return messages

    def get_unread_messages(self, filter_sender: str = "") -> Tuple[bool, str, List[Dict[str, Any]]]:
        """
        Retrieves real incoming WhatsApp message notifications directly from Windows Notification Store.
        Zero window opening, zero screen stealing, strictly genuine messages.
        """
        notifications = self._fetch_real_notifications(max_count=10)

        # Filter by specific sender if requested (e.g. "what did Qaseem say on whatsapp")
        if filter_sender:
            low_f = filter_sender.lower().strip()
            notifications = [m for m in notifications if low_f in m.get("sender", "").lower()]

        # Fallback to local cache if DB was empty
        if not notifications and UNREAD_CACHE_FILE.exists():
            try:
                with open(UNREAD_CACHE_FILE, "r", encoding="utf-8") as f:
                    notifications = json.load(f).get("unread", [])
                if filter_sender:
                    low_f = filter_sender.lower().strip()
                    notifications = [m for m in notifications if low_f in m.get("sender", "").lower()]
            except Exception:
                notifications = []

        if not notifications:
            if filter_sender:
                return True, f"Sir, you have no recent messages from {filter_sender.title()} on WhatsApp.", []
            return True, "Sir, you have no unread messages on WhatsApp.", []

        first = notifications[0]
        sender = first.get("sender", "a contact")
        msg = first.get("message", "")

        if len(notifications) == 1:
            speech = f"Sir, you received a message from {sender} on WhatsApp: '{msg}'."
        else:
            # Highlight top 2
            second = notifications[1]
            s2 = second.get("sender", "")
            m2 = second.get("message", "")
            if s2 and s2 != sender:
                speech = f"Sir, you have {len(notifications)} recent messages on WhatsApp: {sender} said '{msg}', and {s2} said '{m2}'."
            else:
                speech = f"Sir, you have {len(notifications)} recent messages on WhatsApp from {sender}, saying '{msg}'."

        return True, speech, notifications

    # =========================================================================
    # Natural Language Intent Dispatcher
    # =========================================================================

    def execute_task(self, task_type: str, raw_query: str, **kwargs) -> Tuple[bool, str, Dict[str, Any]]:
        low = raw_query.lower().strip()

        # 0. Save Contact Phone
        m_save = re.search(r'\b(?:save|add|store)\s+(?:the\s+)?(?:phone\s+)?(?:number\s+)?(?:for\s+|of\s+)?([a-zA-Z0-9_+]+)(?:s|\'s)?\s+(?:phone\s+)?(?:number\s+)?(?:as\s+|to\s+|is\s+|:\s*)\s*(\+?[0-9\s\-()]{7,})', raw_query, re.IGNORECASE)
        if m_save:
            c_name, c_num = m_save.group(1).strip().rstrip("'s").rstrip("s"), m_save.group(2).strip()
            _save_contact_phone(c_name, c_num)
            return True, f"Sir, I have saved the phone number for {c_name.title()} as {c_num}.", {"name": c_name, "phone": c_num}

        # 1. Unread / Notification Inquiry Check
        unread_triggers = [
            "unread", "check", "new message", "read message", "any message",
            "messages from", "messages on", "what did", "tell me about the messages",
            "who messaged", "who sent", "latest message", "recent message"
        ]
        is_read_inquiry = any(w in low for w in unread_triggers)
        if task_type in ["unread", "check_unread"] or (is_read_inquiry and not any(w in low for w in ["send", "write", "text", "tell to", "chat"])):
            filter_sender = ""
            m_from = re.search(r'\b(?:from|by)\s+([a-zA-Z0-9_+]+)', raw_query, re.IGNORECASE)
            if m_from:
                filter_sender = m_from.group(1).strip()
            else:
                m_did = re.search(r'\bwhat\s+did\s+([a-zA-Z0-9_+]+)\s+say\b', raw_query, re.IGNORECASE)
                if m_did:
                    filter_sender = m_did.group(1).strip()

            ok, speech, items = self.get_unread_messages(filter_sender=filter_sender)
            return ok, speech, {"unread_messages": items, "count": len(items)}

        # 2. Open Chat Specifically
        if task_type == "open_chat" or "open chat" in low:
            m_chat = re.search(r'\bopen\s+chat\s+(?:with\s+)?([a-zA-Z0-9_+]+)', raw_query, re.IGNORECASE)
            target = m_chat.group(1).strip() if m_chat else kwargs.get("contact", "")
            if not target:
                target, _ = parse_whatsapp_command(raw_query)
            if target:
                return self.open_chat_and_send(target, "")

        # 3. Send Message
        target, body = parse_whatsapp_command(raw_query)
        if not target and task_type in ["send", "send_message"]:
            m_target = re.search(r'\b(?:to|contact)\s+([a-zA-Z0-9_+]+)', raw_query, re.IGNORECASE)
            target = m_target.group(1).strip() if m_target else ""

        try:
            from disambiguation_manager import DISAMBIGUATION_MGR
        except ImportError:
            DISAMBIGUATION_MGR = None

        if task_type in ["send", "send_message", "auto"] or any(w in low for w in ["send", "write", "text", "message", "msg", "tell"]):
            if not target and DISAMBIGUATION_MGR:
                ok, c_ans = DISAMBIGUATION_MGR.ask_user(
                    "Sir, who would you like to message on WhatsApp?",
                    barrier_type="dynamic"
                )
                target = c_ans.strip() if ok else ""

            if not target:
                return False, "WhatsApp message was cancelled because no recipient contact was provided, sir.", {}

            if not body and DISAMBIGUATION_MGR:
                ok, b_ans = DISAMBIGUATION_MGR.ask_user(
                    f"Sir, what message would you like to send to {target} on WhatsApp?",
                    options=["I'll be there soon.", "Can we talk?", "All tasks are completed."],
                    barrier_type="dynamic"
                )
                body = b_ans.strip() if ok else ""

            # If body is still not provided, open chat with the contact so user can type!
            if not body:
                return self.open_chat_and_send(target, "")

            return self.send_message(target, body)

        # Default fallback: check unread
        ok, speech, items = self.get_unread_messages()
        return ok, speech, {"unread_messages": items, "count": len(items)}


WHATSAPP_WORKER = WhatsAppDirectWorker()
