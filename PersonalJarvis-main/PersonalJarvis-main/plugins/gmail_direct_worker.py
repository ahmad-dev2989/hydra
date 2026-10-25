"""
HYDRA Direct Gmail Background Worker (Phase 2)
Executes unread email checking, searching, and sending 100% in the background
without opening browser windows or interrupting the user's active screen.
Powered by Python native IMAP4_SSL and SMTP_SSL with secure vault tokens.
"""

import os
import sys
import re
import time
import imaplib
import smtplib
import email
from email.header import decode_header
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from typing import Dict, Any, Tuple, List, Optional
from .base_connector import BaseDirectWorker
from .vault_manager import VAULT_MGR

CURRENT_DIR = Path(__file__).resolve().parent
ROOT_DIR = CURRENT_DIR.parent

# Contacts candidate files
CONTACTS_CANDIDATES = [
    ROOT_DIR / "contacts.json",
    ROOT_DIR.parent / "contacts.json",
    ROOT_DIR / "data" / "vault" / "contacts.json",
    ROOT_DIR / "PersonalJarvis-main" / "contacts.json"
]


def _decode_mime(val: str) -> str:
    """Robustly decodes MIME encoded header strings."""
    if not val:
        return ""
    try:
        decoded_list = decode_header(val)
        res = []
        for text, encoding in decoded_list:
            if isinstance(text, bytes):
                res.append(text.decode(encoding or "utf-8", errors="replace"))
            else:
                res.append(str(text))
        return " ".join(res).strip()
    except Exception:
        return str(val)


def _resolve_contact_email(name_or_email: str) -> str:
    """Resolves contact name to email from contacts.json if known."""
    clean = name_or_email.strip()
    if "@" in clean:
        return clean

    # Check contacts files
    for cp in CONTACTS_CANDIDATES:
        if cp.exists():
            try:
                import json
                with open(cp, "r", encoding="utf-8") as f:
                    cdata = json.load(f)
                low_k = clean.lower()
                for k, v in cdata.items():
                    if k.lower() == low_k and v:
                        if isinstance(v, dict):
                            email_val = str(v.get("email", "")).strip()
                            if "@" in email_val:
                                return email_val
                        elif "@" in str(v):
                            return str(v).strip()
            except Exception:
                pass
    return clean


def _save_contact_email(name: str, email_addr: str):
    """Persists contact email into candidate contacts files."""
    clean_n = name.strip().lower()
    clean_e = email_addr.strip()
    if not clean_n or "@" not in clean_e:
        return
    for cp in CONTACTS_CANDIDATES:
        try:
            cdata = {}
            if cp.exists():
                try:
                    import json
                    with open(cp, "r", encoding="utf-8") as f:
                        cdata = json.load(f)
                except Exception:
                    cdata = {}
            if isinstance(cdata.get(clean_n), dict):
                cdata[clean_n]["email"] = clean_e
            else:
                cdata[clean_n] = {"email": clean_e}
            cp.parent.mkdir(parents=True, exist_ok=True)
            with open(cp, "w", encoding="utf-8") as f:
                json.dump(cdata, f, indent=2)
        except Exception:
            pass


class GmailDirectWorker(BaseDirectWorker):
    """
    Direct Gmail worker executing IMAP/SMTP background tasks in < 300ms.
    """
    app_id = "gmail"
    app_name = "Gmail"

    def is_ready(self) -> bool:
        """Returns True if user has connected Gmail via 1-Click Google or Vault."""
        return VAULT_MGR.is_connected(self.app_id) or bool(VAULT_MGR.load_google_session())

    def get_account_email(self) -> str:
        acc = VAULT_MGR.get_account_email(self.app_id)
        if not acc:
            sess = VAULT_MGR.load_google_session()
            if sess:
                acc = sess.get("account")
        return acc or ""

    def _get_credentials(self) -> Tuple[str, str]:
        """Retrieves account email and authentication password/token."""
        email_addr = self.get_account_email()
        plugin_data = VAULT_MGR.get_plugin_data(self.app_id) or {}
        password = (
            plugin_data.get("data", {}).get("password")
            or plugin_data.get("password")
            or os.environ.get("GMAIL_APP_PASSWORD", "")
        )
        return email_addr, password

    def _fetch_unread_from_web_profile(self, email_addr: str, limit: int = 5) -> Tuple[bool, str, List[Dict[str, Any]]]:
        """
        Attempts to read live unread emails from connected web profile or advises App Password.
        Completely eliminates fake simulated emails.
        """
        try:
            profile_dir = ROOT_DIR / "data" / "vault" / "google_web_profile"
            if profile_dir.exists():
                from PyQt6.QtCore import QUrl, QEventLoop, QTimer
                from PyQt6.QtWidgets import QApplication
                from PyQt6.QtWebEngineCore import QWebEngineProfile, QWebEnginePage

                email_titles = []
                authors = []
                import threading
                if threading.current_thread() == threading.main_thread():
                    app = QApplication.instance()
                    if app:
                        profile = QWebEngineProfile.defaultProfile()
                        page = QWebEnginePage(profile, app)

                        loop = QEventLoop()
                        page.loadFinished.connect(lambda ok: loop.quit())
                        QTimer.singleShot(4000, loop.quit)
                        page.load(QUrl("https://mail.google.com/mail/u/0/feed/atom"))
                        loop.exec()

                        feed_html = ""
                        loop2 = QEventLoop()
                        def on_html(h):
                            nonlocal feed_html
                            feed_html = h
                            loop2.quit()
                        page.toHtml(on_html)
                        QTimer.singleShot(2000, loop2.quit)
                        loop2.exec()

                        import re
                        titles = re.findall(r'<title>(.*?)</title>', feed_html)
                        authors = re.findall(r'<name>(.*?)</name>', feed_html)
                        email_titles = titles[1:] if len(titles) > 1 else []

                    if email_titles:
                        emails_summary = []
                        for i in range(min(len(email_titles), limit)):
                            raw_subj = email_titles[i] if i < len(email_titles) else "New Message"
                            raw_author = authors[i] if i < len(authors) else "Unknown"
                            clean_subj = re.sub(r'[^\x00-\x7F]+', ' ', raw_subj).strip()
                            clean_author = re.sub(r'[^\x00-\x7F]+', ' ', raw_author).strip()
                            emails_summary.append({
                                "from": clean_author or "Sender",
                                "subject": clean_subj or "Subject",
                                "date": "Recent"
                            })
                        top_items = [f"{e['from']} regarding '{e['subject']}'" for e in emails_summary[:min(3, len(emails_summary))]]
                        speech = f"Sir, you have {len(email_titles)} unread email(s) on {email_addr}: from {', '.join(top_items)}."
                        return True, speech, emails_summary
        except Exception as e:
            print(f"[GMAIL DIRECT NOTICE] {e}")

        # Truthful status — no fake emails
        speech = f"Sir, your Gmail account ({email_addr}) is connected, but reading your live inbox directly in the background requires either Gmail API authorization or a Gmail App Password configured in the Plugins Hub."
        return False, speech, []

    def _fetch_unread_from_api(self, token: str, limit: int = 5) -> Optional[Tuple[bool, str, List[Dict[str, Any]]]]:
        """Fetches live unread emails via Gmail REST API."""
        import requests
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        try:
            resp = requests.get(
                "https://gmail.googleapis.com/gmail/v1/users/me/messages",
                headers=headers,
                params={"q": "is:unread", "maxResults": limit},
                timeout=4.0
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
            messages = data.get("messages", [])
            if not messages:
                return True, "Sir, you have no unread emails in your Gmail inbox.", []

            emails_summary = []
            for m in messages:
                mid = m.get("id")
                m_resp = requests.get(
                    f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{mid}",
                    headers=headers,
                    params={"format": "metadata", "metadataHeaders": ["From", "Subject", "Date"]},
                    timeout=3.0
                )
                if m_resp.status_code == 200:
                    payload = m_resp.json().get("payload", {})
                    hdrs = {h.get("name"): h.get("value") for h in payload.get("headers", [])}
                    clean_from = re.sub(r"<.*?>", "", hdrs.get("From", "Unknown Sender")).strip().strip('"')
                    emails_summary.append({
                        "from": clean_from or "Sender",
                        "subject": hdrs.get("Subject", "No Subject"),
                        "date": hdrs.get("Date", "")
                    })

            if not emails_summary:
                return True, "Sir, you have no unread emails in your Gmail inbox.", []

            if len(emails_summary) == 1:
                e1 = emails_summary[0]
                speech = f"Sir, you have 1 unread email in your Gmail from {e1['from']} regarding '{e1['subject']}'."
            else:
                top_items = [f"{e['from']} regarding '{e['subject']}'" for e in emails_summary[:min(3, len(emails_summary))]]
                speech = f"Sir, you have unread emails in your Gmail: from {', and from '.join(top_items)}."

            return True, speech, emails_summary
        except Exception as e:
            print(f"[GMAIL API NOTICE] {e}")
            return None

    def _search_via_api(self, token: str, query: str, limit: int = 5) -> Optional[Tuple[bool, str, List[Dict[str, Any]]]]:
        """Searches emails via Gmail REST API."""
        import requests
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        try:
            resp = requests.get(
                "https://gmail.googleapis.com/gmail/v1/users/me/messages",
                headers=headers,
                params={"q": query, "maxResults": limit},
                timeout=4.0
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
            messages = data.get("messages", [])
            if not messages:
                return True, f"Sir, I searched your Gmail inbox but found no messages matching '{query}'.", []

            results = []
            for m in messages:
                mid = m.get("id")
                m_resp = requests.get(
                    f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{mid}",
                    headers=headers,
                    params={"format": "metadata", "metadataHeaders": ["From", "Subject", "Date"]},
                    timeout=3.0
                )
                if m_resp.status_code == 200:
                    payload = m_resp.json().get("payload", {})
                    hdrs = {h.get("name"): h.get("value") for h in payload.get("headers", [])}
                    clean_from = re.sub(r"<.*?>", "", hdrs.get("From", "Unknown Sender")).strip().strip('"')
                    results.append({
                        "from": clean_from or "Sender",
                        "subject": hdrs.get("Subject", "No Subject"),
                        "date": hdrs.get("Date", "")
                    })

            if not results:
                return True, f"Sir, I searched your Gmail inbox but found no messages matching '{query}'.", []

            top = results[0]
            speech = f"Sir, I found {len(results)} email(s) matching '{query}'. The newest is from {top['from']} with subject '{top['subject']}'."
            return True, speech, results
        except Exception:
            return None

    def _send_via_api(self, token: str, email_addr: str, to_addr: str, subject: str, body: str) -> Tuple[bool, str, Dict[str, Any]]:
        """Sends email via Gmail REST API."""
        import base64
        import requests
        try:
            msg = MIMEMultipart()
            msg["From"] = email_addr
            msg["To"] = to_addr
            msg["Subject"] = subject
            msg.attach(MIMEText(body, "plain", "utf-8"))
            raw_b64 = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json"
            }
            resp = requests.post(
                "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
                headers=headers,
                json={"raw": raw_b64},
                timeout=8.0
            )
            if resp.status_code in [200, 201]:
                speech = f"Sir, your email to {to_addr} regarding '{subject}' has been sent successfully via Gmail API."
                return True, speech, {"to": to_addr, "subject": subject, "status": "sent"}
            return False, f"Sir, Gmail API returned status {resp.status_code} while sending the email.", {}
        except Exception as e:
            return False, f"Sir, failed to dispatch email via Gmail API: {e}", {}

    # =========================================================================
    # Phase 2.1: Check Unread Emails (Native IMAP SSL or REST API in < 300ms)
    # =========================================================================

    def get_unread_emails(self, limit: int = 5) -> Tuple[bool, str, List[Dict[str, Any]]]:
        """
        Fetches newest unread email headers directly over SSL or REST API.
        Returns:
            (success: bool, speech: str, emails_list: List[Dict])
        """
        email_addr, password = self._get_credentials()
        if not email_addr:
            return False, "Gmail is not connected in your Plugins Hub, sir. Please connect via Sign in with Google.", []

        # Tier 1: Try Gmail REST API if OAuth token exists
        sess = VAULT_MGR.load_google_session() or {}
        token = sess.get("token") or sess.get("access_token") or os.environ.get("GMAIL_TOKEN")
        if token:
            api_res = self._fetch_unread_from_api(token, limit)
            if api_res is not None:
                return api_res

        # Tier 2: If no IMAP password, try connected Google web profile atom feed
        if not password:
            return self._fetch_unread_from_web_profile(email_addr, limit)

        # Tier 3: Native IMAP SSL
        try:
            t0 = time.perf_counter()
            mail = imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=5.0)
            mail.login(email_addr, password)
            mail.select("INBOX", readonly=True)

            status, search_data = mail.search(None, "UNSEEN")
            if status != "OK":
                mail.logout()
                return False, "Could not search your Gmail inbox at this moment, sir.", []

            msg_ids = search_data[0].split()
            unread_count = len(msg_ids)

            if unread_count == 0:
                mail.logout()
                return True, f"Sir, you have no unread emails in your Gmail inbox ({email_addr}).", []

            recent_ids = msg_ids[-limit:]
            emails_summary = []

            for mid in reversed(recent_ids):
                res, data = mail.fetch(mid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
                if res == "OK" and data and isinstance(data[0], tuple):
                    msg = email.message_from_bytes(data[0][1])
                    subj = _decode_mime(msg.get("Subject", "No Subject"))
                    sender = _decode_mime(msg.get("From", "Unknown Sender"))
                    date_hdr = _decode_mime(msg.get("Date", ""))
                    clean_sender = re.sub(r"<.*?>", "", sender).strip().strip('"')
                    emails_summary.append({
                        "from": clean_sender,
                        "subject": subj,
                        "date": date_hdr
                    })

            mail.logout()
            latency = (time.perf_counter() - t0) * 1000

            # Generate natural voice speech response
            if unread_count == 1:
                e1 = emails_summary[0]
                speech = f"Sir, you have 1 unread email in your Gmail from {e1['from']} regarding '{e1['subject']}'."
            else:
                top_items = [f"{e['from']} regarding '{e['subject']}'" for e in emails_summary[:3]]
                if len(top_items) == 2:
                    speech = f"Sir, you have {unread_count} unread emails in your Gmail: from {top_items[0]}, and from {top_items[1]}."
                else:
                    speech = f"Sir, you have {unread_count} unread emails in your Gmail: from {top_items[0]}, from {top_items[1]}, and from {top_items[2]}."

            print(f"[GMAIL DIRECT] Checked unread emails in {latency:.1f}ms ({unread_count} unread).")
            return True, speech, emails_summary

        except Exception as e:
            print(f"[GMAIL DIRECT ERROR] {e}")
            return False, f"I encountered an issue checking your Gmail directly, sir: {e}", []

    # =========================================================================
    # Phase 2.2: Search Emails (Keyword / Sender Search)
    # =========================================================================

    def search_emails(self, query: str, limit: int = 5) -> Tuple[bool, str, List[Dict[str, Any]]]:
        """
        Searches inbox by keyword or sender name in the background.
        """
        email_addr, password = self._get_credentials()
        clean_q = query.strip()
        if not clean_q:
            return False, "Please specify an email keyword or sender to search for, sir.", []

        if not email_addr:
            return False, "Gmail is not connected in your Plugins Hub, sir.", []

        # Tier 1: Try Gmail REST API if OAuth token exists
        sess = VAULT_MGR.load_google_session() or {}
        token = sess.get("token") or sess.get("access_token") or os.environ.get("GMAIL_TOKEN")
        if token:
            api_res = self._search_via_api(token, clean_q, limit)
            if api_res is not None:
                return api_res

        # Tier 2: Check IMAP password
        if not password:
            ok, _, unread_list = self._fetch_unread_from_web_profile(email_addr, limit=20)
            if ok and unread_list:
                low_q = clean_q.lower()
                matches = [e for e in unread_list if low_q in e.get("subject", "").lower() or low_q in e.get("from", "").lower()]
                if matches:
                    top = matches[0]
                    return True, f"Sir, I found {len(matches)} email(s) matching '{clean_q}': the newest is from {top['from']} regarding '{top['subject']}'.", matches[:limit]
            return False, f"Sir, searching your Gmail inbox in the background requires a Gmail App Password or API authorization in the Plugins Hub.", []

        try:
            t0 = time.perf_counter()
            mail = imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=6.0)
            mail.login(email_addr, password)
            mail.select("INBOX", readonly=True)

            # Search with OR between FROM and SUBJECT
            search_crit = f'(OR FROM "{clean_q}" SUBJECT "{clean_q}")'
            status, search_data = mail.search(None, search_crit)
            if status != "OK" or not search_data[0]:
                # Fallback to general TEXT search
                status, search_data = mail.search(None, f'TEXT "{clean_q}"')

            msg_ids = search_data[0].split() if status == "OK" and search_data[0] else []
            match_count = len(msg_ids)

            if match_count == 0:
                mail.logout()
                return True, f"Sir, I searched your Gmail but found no emails matching '{clean_q}'.", []

            recent_ids = msg_ids[-limit:]
            results = []

            for mid in reversed(recent_ids):
                res, data = mail.fetch(mid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
                if res == "OK" and data and isinstance(data[0], tuple):
                    msg = email.message_from_bytes(data[0][1])
                    subj = _decode_mime(msg.get("Subject", "No Subject"))
                    sender = _decode_mime(msg.get("From", "Unknown Sender"))
                    clean_sender = re.sub(r"<.*?>", "", sender).strip().strip('"')
                    results.append({"from": clean_sender, "subject": subj})

            mail.logout()
            latency = (time.perf_counter() - t0) * 1000

            top_item = results[0]
            speech = f"Sir, I found {match_count} emails matching '{clean_q}'. The most recent is from {top_item['from']} with subject '{top_item['subject']}'."
            print(f"[GMAIL SEARCH] Found {match_count} matches for '{clean_q}' in {latency:.1f}ms.")
            return True, speech, results

        except Exception as e:
            print(f"[GMAIL SEARCH ERROR] {e}")
            return False, f"Could not complete email search: {e}", []

    # =========================================================================
    # Phase 2.3: Send Email in Background (< 1 second)
    # =========================================================================

    def send_email(self, to_addr: str, subject: str, body: str) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Sends an email completely headlessly via background SMTP_SSL or Gmail API.
        Never fakes success when credentials or tokens are absent.
        """
        email_addr, password = self._get_credentials()
        resolved_to = _resolve_contact_email(to_addr)
        if to_addr != resolved_to and "@" in resolved_to:
            _save_contact_email(to_addr, resolved_to)

        if not resolved_to:
            return False, "Recipient email address was not specified, sir.", {}

        clean_subj = subject.strip() or "Message from HYDRA"
        clean_body = body.strip() or "Sent via HYDRA Autonomous Assistant."

        # Tier 1: SMTP_SSL if app password is configured
        if email_addr and password:
            try:
                t0 = time.perf_counter()
                msg = MIMEMultipart()
                msg["From"] = email_addr
                msg["To"] = resolved_to
                msg["Subject"] = clean_subj
                msg.attach(MIMEText(clean_body, "plain", "utf-8"))

                server = smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=8.0)
                server.login(email_addr, password)
                server.sendmail(email_addr, [resolved_to], msg.as_string())
                server.quit()

                latency = (time.perf_counter() - t0) * 1000
                print(f"[GMAIL SEND] Dispatched email to {resolved_to} in {latency:.1f}ms.")

                speech = f"Sir, your email to {to_addr} regarding '{clean_subj}' has been sent successfully."
                return True, speech, {"to": resolved_to, "subject": clean_subj, "latency_ms": latency}

            except Exception as e:
                print(f"[GMAIL SEND SMTP ERROR] {e}")
                # Fall through to Tier 2 (Gmail API)

        # Tier 2: Gmail REST API if OAuth token is present
        sess = VAULT_MGR.load_google_session() or {}
        token = sess.get("token") or sess.get("access_token") or os.environ.get("GMAIL_TOKEN")
        if token and email_addr:
            ok, speech, res_data = self._send_via_api(token, email_addr, resolved_to, clean_subj, clean_body)
            if ok:
                return True, speech, res_data

        # Tier 3: Honest reporting - NEVER fake dispatch
        speech = f"Sir, I could not send the email to {to_addr}. Your Gmail account requires an App Password or send authorization configured in the Plugins Hub to dispatch emails."
        return False, speech, {}

    # =========================================================================
    # Unified Intent Dispatcher
    # =========================================================================

    def execute_task(self, task_type: str, raw_query: str, **kwargs) -> Tuple[bool, str, Dict[str, Any]]:
        low = raw_query.lower().strip()

        # 1. Send Email Task
        if task_type == "send" or any(w in low for w in ["send email", "send an email", "send mail", "email to", "mail to"]):
            try:
                from disambiguation_manager import DISAMBIGUATION_MGR
            except ImportError:
                DISAMBIGUATION_MGR = None

            # Extract recipient
            m_to = re.search(r"(?:to|unto)\s+([a-zA-Z0-9_.+-]+(?:@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)?|[a-zA-Z]+)", raw_query, re.IGNORECASE)
            to_target = m_to.group(1).strip() if m_to else kwargs.get("to", "")
            if not to_target or to_target.lower() in ["email", "mail", "someone", "recipient"]:
                if DISAMBIGUATION_MGR:
                    ok, to_ans = DISAMBIGUATION_MGR.ask_user(
                        "Sir, who would you like me to send this email to?",
                        barrier_type="dynamic"
                    )
                    to_target = to_ans.strip() if ok else ""

            if not to_target:
                return False, "Email sending was cancelled because no recipient was specified, sir.", {}

            # Extract body
            m_body = re.search(r"(?:saying|message|body|that)\s+(.+)", raw_query, re.IGNORECASE)
            body_text = m_body.group(1).strip() if m_body else kwargs.get("body", "")
            if not body_text and DISAMBIGUATION_MGR:
                ok, body_ans = DISAMBIGUATION_MGR.ask_user(
                    f"Sir, what message or body would you like me to include in the email to {to_target}?",
                    options=["Hello, this is Ahmad.", "Please review the attached project updates.", "Let's schedule a meeting."],
                    barrier_type="dynamic"
                )
                body_text = body_ans.strip() if ok else ""

            if not body_text:
                return False, f"Email to {to_target} was cancelled because no message body was provided, sir.", {}

            # Extract subject
            m_subj = re.search(r"(?:subject|regarding|about)\s+([a-zA-Z0-9\s]+?)(?:saying|message|body|$)", raw_query, re.IGNORECASE)
            subj_text = m_subj.group(1).strip() if m_subj else kwargs.get("subject", "")
            if not subj_text and DISAMBIGUATION_MGR:
                ok, subj_ans = DISAMBIGUATION_MGR.ask_user(
                    "Sir, what subject would you like for this email?",
                    options=["HYDRA Project Update", "Important Notification", "General Inquiry"],
                    barrier_type="dynamic"
                )
                subj_text = subj_ans.strip() if ok else "HYDRA Update"

            clean_subj = subj_text or "HYDRA Update"
            ok, speech, meta = self.send_email(to_target, clean_subj, body_text)
            return ok, speech, meta

        # 2. Search Email Task
        if task_type == "search" or any(w in low for w in ["search email", "find email", "search mail", "find mail"]):
            m_q = re.search(r"(?:for|about|from|regarding)\s+(.+)", low)
            query_str = m_q.group(1).strip() if m_q else "update"
            ok, speech, results = self.search_emails(query_str)
            return ok, speech, {"results": results, "query": query_str}

        # 3. Check Unread Emails (Default)
        ok, speech, emails = self.get_unread_emails()
        return ok, speech, {"emails": emails, "count": len(emails)}


GMAIL_WORKER = GmailDirectWorker()
