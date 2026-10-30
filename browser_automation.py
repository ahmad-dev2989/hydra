"""
HYDRA Phase 12: Deep Interactive Web Automation Engine
======================================================
Capabilities:
  1. Full Browser Navigation beyond basic search:
     - Chromium / Edge headless and headed execution.
     - Multi-page and multi-tab management.
     - Back, forward, reload, scroll, and screenshot.
  2. Dynamic SPAs & Network State:
     - Handles dynamic Single Page Applications (React, Vue, Angular, Next.js).
     - Waits for DOM hydration and network idle.
  3. Persistent Sessions & Authenticated Logins:
     - Manages cookies, localStorage, and storageState per session.
     - Sessions persist in `web_sessions/` so users remain logged into AWS, GitHub, Canva, etc.
  4. Form Autofill & Element Interaction:
     - Heuristic field matching (label, placeholder, name, id, ARIA).
     - Text inputs, textareas, checkboxes, dropdowns, radio buttons, and submit buttons.
  5. File Downloads:
     - Listens to browser download events (e.g. "Download the latest invoice from AWS").
     - Saves downloaded files to user's `Downloads` or workspace directory.
  6. Structured Table Scraping into Spreadsheets:
     - Detects tables and data grids on any web page.
     - Extracts headers, rows, and cells.
     - Exports cleanly to UTF-8 CSV spreadsheet files.
  7. Dual Mode:
     - Fast Local / Hardcoded Mode: 0ms AI latency, 100% offline rule-based execution.
     - Intelligent AI Mode: High-level natural language prompt deconstructed into DOM actions.
"""

import os
import re
import csv
import json
import time
import shutil
import urllib.parse
from typing import Dict, Any, List, Optional, Tuple, Callable

# Directories
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SESSIONS_DIR = os.path.join(BASE_DIR, "web_sessions")
EXPORTS_DIR = os.path.join(BASE_DIR, "exports")
DEFAULT_DOWNLOADS = os.path.join(os.path.expanduser("~"), "Downloads")

os.makedirs(SESSIONS_DIR, exist_ok=True)
os.makedirs(EXPORTS_DIR, exist_ok=True)

# Playwright availability
try:
    from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page, Download
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False
    sync_playwright = None


# =============================================================================
# 1. BROWSER SESSION & PROFILE MANAGER
# =============================================================================

class BrowserSessionManager:
    """
    Maintains persistent storage states (cookies, localStorage, session tokens)
    so sessions (AWS, GitHub, Canva, portals) persist across HYDRA restarts.
    """
    def __init__(self, sessions_dir: str = SESSIONS_DIR):
        self.sessions_dir = sessions_dir
        os.makedirs(self.sessions_dir, exist_ok=True)

    def get_session_path(self, session_name: str) -> str:
        clean_name = re.sub(r'[^\w\-]', '_', session_name.strip().lower())
        return os.path.join(self.sessions_dir, f"{clean_name}.json")

    def has_session(self, session_name: str) -> bool:
        return os.path.exists(self.get_session_path(session_name))

    def list_sessions(self) -> List[str]:
        sessions = []
        for f in os.listdir(self.sessions_dir):
            if f.endswith(".json"):
                sessions.append(f[:-5])
        return sorted(sessions)

    def delete_session(self, session_name: str) -> bool:
        p = self.get_session_path(session_name)
        if os.path.exists(p):
            try:
                os.remove(p)
                return True
            except Exception:
                pass
        return False


# =============================================================================
# 2. DEEP INTERACTIVE WEB ENGINE
# =============================================================================

class InteractiveWebEngine:
    """
    Core Phase 12 Web Automation Engine:
    Handles dynamic SPAs, multi-page workflows, form autofill, file downloads,
    and structured table scraping into spreadsheets.
    """
    def __init__(self, headless: bool = True, session_name: Optional[str] = None):
        self.headless = headless
        self.session_name = session_name
        self.session_mgr = BrowserSessionManager()
        
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self.active_url: str = ""

    # -------------------------------------------------------------------------
    # Lifecycle: Launch & Close
    # -------------------------------------------------------------------------
    def ensure_browser(self, headless: Optional[bool] = None, session_name: Optional[str] = None) -> Page:
        """Ensures a browser, context, and page are alive, loading session state if requested."""
        if not HAS_PLAYWRIGHT:
            raise RuntimeError("Playwright package is not installed.")

        if headless is not None:
            self.headless = headless
        if session_name:
            self.session_name = session_name

        if self._page and not self._page.is_closed():
            return self._page

        if not self._playwright:
            self._playwright = sync_playwright().start()

        # Browser launch hierarchy:
        # 1. Bundled Chromium
        # 2. Native Microsoft Edge channel
        # 3. Native Google Chrome channel
        if not self._browser:
            launched = False
            errors = []
            
            # Option 1: Standard Chromium
            try:
                self._browser = self._playwright.chromium.launch(
                    headless=self.headless,
                    args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
                )
                launched = True
            except Exception as e:
                errors.append(f"Chromium: {e}")

            # Option 2: Native Edge
            if not launched:
                try:
                    self._browser = self._playwright.chromium.launch(
                        channel="msedge",
                        headless=self.headless,
                        args=["--disable-blink-features=AutomationControlled"]
                    )
                    launched = True
                except Exception as e:
                    errors.append(f"MSEdge: {e}")

            # Option 3: Native Chrome
            if not launched:
                try:
                    self._browser = self._playwright.chromium.launch(
                        channel="chrome",
                        headless=self.headless,
                        args=["--disable-blink-features=AutomationControlled"]
                    )
                    launched = True
                except Exception as e:
                    errors.append(f"Chrome: {e}")

            if not launched:
                raise RuntimeError(f"Could not launch any browser. Errors: {'; '.join(errors)}")

        # Create or restore context with persistent storage state
        state_file = None
        if self.session_name and self.session_mgr.has_session(self.session_name):
            state_file = self.session_mgr.get_session_path(self.session_name)

        context_kwargs: Dict[str, Any] = {
            "viewport": {"width": 1280, "height": 800},
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "accept_downloads": True
        }
        if state_file:
            context_kwargs["storage_state"] = state_file

        self._context = self._browser.new_context(**context_kwargs)
        self._page = self._context.new_page()
        return self._page

    def close(self):
        """Cleanly releases browser and context resources."""
        try:
            if self._context:
                # Auto-save active session state if session_name is configured
                if self.session_name:
                    self.save_session(self.session_name)
                self._context.close()
        except Exception:
            pass
        try:
            if self._browser:
                self._browser.close()
        except Exception:
            pass
        try:
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass
        self._page = None
        self._context = None
        self._browser = None
        self._playwright = None

    # -------------------------------------------------------------------------
    # Navigation & SPA State Handling
    # -------------------------------------------------------------------------
    def navigate(self, url: str, wait_until: str = "load", timeout_ms: int = 30000) -> Dict[str, Any]:
        """
        Navigates to a target URL, waiting for SPA dynamic hydration and network idle.
        """
        clean_url = url.strip()
        if clean_url.startswith("file://") or clean_url.startswith("about:"):
            url = clean_url
        elif re.match(r'^[a-zA-Z]:[\\/]', clean_url) or clean_url.startswith("/"):
            url = f"file:///{os.path.abspath(clean_url).replace(os.sep, '/')}"
        elif not re.match(r'^https?://', clean_url, re.IGNORECASE):
            url = f"https://{clean_url}"

        page = self.ensure_browser()
        t0 = time.time()
        response = page.goto(url, wait_until=wait_until, timeout=timeout_ms)
        self.active_url = page.url

        # Allow SPA framework (React/Vue/Angular) to render components
        try:
            page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass

        title = page.title()
        status = response.status if response else 200
        elapsed = round(time.time() - t0, 2)

        return {
            "url": page.url,
            "title": title,
            "status": status,
            "load_time_sec": elapsed
        }

    # -------------------------------------------------------------------------
    # Element Interaction: Click, Fill, Select
    # -------------------------------------------------------------------------
    def click(self, target: str, timeout_ms: int = 8000) -> bool:
        """
        Clicks an element using flexible selector heuristics:
        1. Exact CSS or XPath selector
        2. Text contents (button, link, label)
        3. ARIA role or placeholder
        """
        page = self.ensure_browser()
        target_clean = target.strip()

        # Try direct selector
        try:
            loc = page.locator(target_clean)
            if loc.count() > 0 and loc.first.is_visible():
                loc.first.click(timeout=timeout_ms)
                return True
        except Exception:
            pass

        # Try get_by_role ('button', 'link')
        for role in ["button", "link", "menuitem", "tab"]:
            try:
                loc = page.get_by_role(role, name=re.compile(re.escape(target_clean), re.IGNORECASE))
                if loc.count() > 0:
                    loc.first.click(timeout=timeout_ms)
                    return True
            except Exception:
                pass

        # Try get_by_text
        try:
            loc = page.get_by_text(re.compile(re.escape(target_clean), re.IGNORECASE))
            if loc.count() > 0:
                loc.first.click(timeout=timeout_ms)
                return True
        except Exception:
            pass

        return False

    def fill_input(self, target: str, value: str, timeout_ms: int = 8000) -> bool:
        """
        Fills a single input/textarea matching by label, placeholder, name, id, or selector.
        """
        page = self.ensure_browser()
        clean_target = target.strip()

        # 1. By Label
        try:
            loc = page.get_by_label(re.compile(re.escape(clean_target), re.IGNORECASE))
            if loc.count() > 0:
                loc.first.fill(value, timeout=timeout_ms)
                return True
        except Exception:
            pass

        # 2. By Placeholder
        try:
            loc = page.get_by_placeholder(re.compile(re.escape(clean_target), re.IGNORECASE))
            if loc.count() > 0:
                loc.first.fill(value, timeout=timeout_ms)
                return True
        except Exception:
            pass

        # 3. By Name, Id, or CSS Selector
        candidates = [
            clean_target,
            f'input[name*="{clean_target}" i]',
            f'input[id*="{clean_target}" i]',
            f'textarea[name*="{clean_target}" i]',
            f'textarea[id*="{clean_target}" i]',
            f'[aria-label*="{clean_target}" i]'
        ]
        for sel in candidates:
            try:
                loc = page.locator(sel)
                if loc.count() > 0 and loc.first.is_visible():
                    loc.first.fill(value, timeout=timeout_ms)
                    return True
            except Exception:
                pass

        return False

    def fill_form(
        self,
        fields: Dict[str, str],
        submit_target: Optional[str] = None,
        timeout_ms: int = 10000
    ) -> Dict[str, Any]:
        """
        Intelligently autofills an entire form matching input fields and submits if requested.
        """
        page = self.ensure_browser()
        results: Dict[str, bool] = {}

        for field_name, value in fields.items():
            success = self.fill_input(field_name, value, timeout_ms=timeout_ms)
            results[field_name] = success

        submitted = False
        if submit_target:
            submitted = self.click(submit_target, timeout_ms=timeout_ms)
        else:
            # Look for default submit button
            for submit_btn in ['button[type="submit"]', 'input[type="submit"]', "submit", "login", "sign in", "save"]:
                if self.click(submit_btn, timeout_ms=3000):
                    submitted = True
                    break

        return {
            "fields_filled": results,
            "all_success": all(results.values()) if results else False,
            "submitted": submitted
        }

    # -------------------------------------------------------------------------
    # File Downloads: AWS Invoices, PDF Reports, Datasets
    # -------------------------------------------------------------------------
    def download_file(
        self,
        trigger: str,
        output_dir: Optional[str] = None,
        timeout_ms: int = 35000
    ) -> Dict[str, Any]:
        """
        Intercepts and manages file downloads initiated by clicking a link/button
        or navigating directly to a downloadable file.
        e.g. 'Download the latest invoice from AWS'
        """
        page = self.ensure_browser()
        dest_dir = output_dir or DEFAULT_DOWNLOADS
        os.makedirs(dest_dir, exist_ok=True)

        is_direct_url = bool(re.match(r'^https?://', trigger, re.IGNORECASE))

        try:
            with page.expect_download(timeout=timeout_ms) as download_info:
                if is_direct_url:
                    page.goto(trigger)
                else:
                    clicked = self.click(trigger, timeout_ms=10000)
                    if not clicked:
                        # Try finding element containing 'download' or 'invoice'
                        loc = page.locator(f'a[download], a:has-text("{trigger}"), button:has-text("{trigger}")')
                        if loc.count() > 0:
                            loc.first.click()
                        else:
                            return {"success": False, "error": f"Could not find download trigger '{trigger}'."}

            download: Download = download_info.value
            suggested_name = download.suggested_filename
            target_path = os.path.join(dest_dir, suggested_name)

            # Prevent overwriting
            base, ext = os.path.splitext(suggested_name)
            counter = 1
            while os.path.exists(target_path):
                target_path = os.path.join(dest_dir, f"{base}_{counter}{ext}")
                counter += 1

            download.save_as(target_path)
            size_bytes = os.path.getsize(target_path) if os.path.exists(target_path) else 0

            return {
                "success": True,
                "filename": os.path.basename(target_path),
                "filepath": target_path,
                "size_bytes": size_bytes,
                "url": download.url
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    # -------------------------------------------------------------------------
    # Structured Table Scraping into Spreadsheets (CSV)
    # -------------------------------------------------------------------------
    def scrape_table_to_csv(
        self,
        url: Optional[str] = None,
        table_selector: Optional[str] = None,
        output_filename: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Detects tables / data-grids on a web page, parses rows/columns,
        and saves them as a structured UTF-8 CSV spreadsheet.
        """
        if url:
            self.navigate(url)

        page = self.ensure_browser()

        # JavaScript Table Extraction Script
        js_extract = """
        () => {
            const tables = Array.from(document.querySelectorAll('table'));
            if (tables.length === 0) {
                // Try ARIA data grids
                const grids = Array.from(document.querySelectorAll('[role="table"], [role="grid"]'));
                if (grids.length === 0) return null;
                
                const grid = grids[0];
                const headerEls = Array.from(grid.querySelectorAll('[role="columnheader"]'));
                const headers = headerEls.map(h => h.innerText.trim());
                const rowEls = Array.from(grid.querySelectorAll('[role="row"]'));
                const rows = [];
                for (const r of rowEls) {
                    const cells = Array.from(r.querySelectorAll('[role="cell"], [role="gridcell"]')).map(c => c.innerText.trim());
                    if (cells.length > 0) rows.push(cells);
                }
                return { headers, rows, count: rows.length };
            }

            // Find largest table by cell count
            let bestTable = tables[0];
            let maxCells = 0;
            for (const t of tables) {
                const cellCount = t.querySelectorAll('td').length;
                if (cellCount > maxCells) {
                    maxCells = cellCount;
                    bestTable = t;
                }
            }

            // Extract headers
            let headers = [];
            const thEls = Array.from(bestTable.querySelectorAll('th'));
            if (thEls.length > 0) {
                headers = thEls.map(th => th.innerText.trim().replace(/\\s+/g, ' '));
            }

            // Extract data rows
            const rows = [];
            const trEls = Array.from(bestTable.querySelectorAll('tr'));
            for (const tr of trEls) {
                const tdEls = Array.from(tr.querySelectorAll('td'));
                if (tdEls.length > 0) {
                    const rowData = tdEls.map(td => td.innerText.trim().replace(/\\s+/g, ' '));
                    rows.push(rowData);
                }
            }

            // If no <th> tags, use first row as headers
            if (headers.length === 0 && rows.length > 0) {
                headers = rows.shift();
            }

            return {
                headers: headers,
                rows: rows,
                count: rows.length
            };
        }
        """

        try:
            table_data = page.evaluate(js_extract)
        except Exception as e:
            return {"success": False, "error": f"Failed evaluating table extraction: {e}"}

        if not table_data or (not table_data.get("rows") and not table_data.get("headers")):
            return {"success": False, "error": "No structured table or grid elements found on this page."}

        headers: List[str] = table_data.get("headers") or []
        rows: List[List[str]] = table_data.get("rows") or []

        # Determine target filename
        if not output_filename:
            page_title = re.sub(r'[^\w\-]', '_', page.title().strip().lower())[:30] or "scraped_table"
            output_filename = f"{page_title}_{int(time.time())}.csv"
        elif not output_filename.endswith(".csv"):
            output_filename += ".csv"

        csv_path = os.path.join(EXPORTS_DIR, output_filename)

        # Write clean UTF-8 with BOM for Excel compatibility
        try:
            with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                if headers:
                    writer.writerow(headers)
                writer.writerows(rows)
        except Exception as e:
            return {"success": False, "error": f"Failed writing CSV file: {e}"}

        return {
            "success": True,
            "csv_path": csv_path,
            "filename": os.path.basename(csv_path),
            "headers": headers,
            "row_count": len(rows),
            "column_count": len(headers) if headers else (len(rows[0]) if rows else 0),
            "preview": rows[:3] if rows else []
        }

    # -------------------------------------------------------------------------
    # Session Persistence: Save & Load
    # -------------------------------------------------------------------------
    def save_session(self, session_name: str) -> str:
        """Saves active cookies and storage state to persistent JSON."""
        self.ensure_browser()
        if not self._context:
            return ""
        dest_path = self.session_mgr.get_session_path(session_name)
        try:
            self._context.storage_state(path=dest_path)
            self.session_name = session_name
            return dest_path
        except Exception as e:
            print(f"[SESSION SAVE ERROR]: {e}")
            return ""

    def load_session(self, session_name: str) -> bool:
        """Closes current context and reopens with the saved session storage state."""
        if not self.session_mgr.has_session(session_name):
            return False
        self.session_name = session_name
        if self._context:
            self._context.close()
            self._context = None
            self._page = None
        self.ensure_browser(session_name=session_name)
        return True

    # -------------------------------------------------------------------------
    # DOM Summary for AI Reasoning
    # -------------------------------------------------------------------------
    def get_dom_summary(self) -> Dict[str, Any]:
        """Provides a compact summary of interactable elements for AI planning."""
        page = self.ensure_browser()
        js_summary = """
        () => {
            const inputs = Array.from(document.querySelectorAll('input, textarea, select')).map(el => ({
                tag: el.tagName.toLowerCase(),
                type: el.type || '',
                name: el.name || '',
                id: el.id || '',
                placeholder: el.placeholder || '',
                label: el.labels && el.labels[0] ? el.labels[0].innerText.trim() : ''
            })).filter(i => i.type !== 'hidden');

            const buttons = Array.from(document.querySelectorAll('button, input[type="submit"], a.btn, [role="button"]'))
                .map(b => b.innerText.trim() || b.value || b.getAttribute('aria-label') || '')
                .filter(t => t.length > 0 && t.length < 50);

            const tables = document.querySelectorAll('table').length;

            return {
                title: document.title,
                url: window.location.href,
                inputs: inputs.slice(0, 15),
                buttons: buttons.slice(0, 15),
                tablesCount: tables
            };
        }
        """
        try:
            return page.evaluate(js_summary)
        except Exception:
            return {"title": page.title(), "url": page.url, "inputs": [], "buttons": [], "tablesCount": 0}


# =============================================================================
# 3. INTELLIGENT AI WEB AGENT
# =============================================================================

class IntelligentWebAgent:
    """
    Translates complex natural language browser automation instructions into
    structured action sequences, executes them, and reports results.
    e.g. "Log into AWS, navigate to Billing, and download the latest invoice"
    e.g. "Scrape the top gainers table on Yahoo Finance and save as finance.csv"
    """
    def __init__(self, engine: InteractiveWebEngine):
        self.engine = engine

    def execute_natural_instruction(self, user_command: str) -> Tuple[bool, str]:
        """Deconstructs high-level instruction and executes deep web automation."""
        clean = user_command.strip()
        clean_low = clean.lower()

        # 1. Hardcoded Fast Route: Direct Table Scraping
        # e.g. "scrape table from https://en.wikipedia.org/wiki/List_of_countries to countries.csv"
        # e.g. "export table to countries.csv"
        scrape_match = re.search(r'(?:scrape\s+table|export\s+table|save\s+table)(?:\s+from\s+(\S+?))?(?:\s+to\s+([\w\.\-]+))?$', clean, re.IGNORECASE)
        if scrape_match and (scrape_match.group(1) or scrape_match.group(2) or "table" in clean_low):
            target_url = scrape_match.group(1)
            target_file = scrape_match.group(2)
            res = self.engine.scrape_table_to_csv(url=target_url, output_filename=target_file)
            if res.get("success"):
                return True, f"Sir, I have scraped the table with {res['row_count']} rows and exported it to {res['filename']}."
            return True, f"Sir, I could not extract the table: {res.get('error', 'unknown error')}"

        # 2. Hardcoded Fast Route: Direct File Download
        # e.g. "download file from https://example.com/report.pdf"
        # e.g. "download the invoice"
        download_match = re.search(r'download\s+(?:the\s+)?(?:file|invoice|report|document|pdf)?(?:\s+from\s+(\S+))?', clean, re.IGNORECASE)
        if download_match and ("from " in clean_low or "http" in clean_low):
            target = download_match.group(1) or "download"
            res = self.engine.download_file(trigger=target)
            if res.get("success"):
                return True, f"Sir, your download '{res['filename']}' ({res['size_bytes']} bytes) is complete and saved to your Downloads folder."
            return True, f"Sir, the download could not be completed: {res.get('error', 'unknown error')}"

        # 3. Hardcoded Fast Route: Browser Session Management
        # e.g. "save browser session as aws" / "load browser session aws"
        if "save browser session" in clean_low or "save web session" in clean_low:
            s_match = re.search(r'(?:save\s+(?:browser|web)\s+session\s+(?:as\s+)?)([\w\-]+)', clean_low)
            if s_match:
                s_name = s_match.group(1)
                path = self.engine.save_session(s_name)
                if path:
                    return True, f"Browser login session saved as '{s_name}', sir. Future automations can reuse these authenticated credentials."
                return True, "Sir, there was no active web context to save as a session."

        if "load browser session" in clean_low or "use browser session" in clean_low:
            s_match = re.search(r'(?:load|use)\s+(?:browser|web)\s+session\s+([\w\-]+)', clean_low)
            if s_match:
                s_name = s_match.group(1)
                ok = self.engine.load_session(s_name)
                if ok:
                    return True, f"Loaded authenticated browser session '{s_name}', sir."
                return True, f"Sir, I could not find a saved session named '{s_name}'."

        # 4. Hardcoded Fast Route: Form Autofill Syntax
        # e.g. "fill form username=admin, password=secret"
        if clean_low.startswith("fill form") or "autofill" in clean_low:
            pairs = re.findall(r'(\w+)\s*[:=]\s*([^\s,]+)', clean)
            if pairs:
                field_dict = {k: v for k, v in pairs}
                res = self.engine.fill_form(field_dict)
                filled_count = sum(1 for v in res["fields_filled"].values() if v)
                return True, f"Sir, autofilled {filled_count} fields on the active page."

        # 5. Intelligent AI Reasoning Route: Complex Dynamic Multi-Step Web Plan
        try:
            from ai_engine import ai_chat, DEFAULT_REASONING_MODEL
            dom_summary = self.engine.get_dom_summary() if self.engine._page else {}
            
            prompt = (
                f"You are HYDRA's Deep Web Automation Engine (Phase 12).\n"
                f"User Instruction: '{user_command}'\n"
                f"Current Page DOM Context: {json.dumps(dom_summary)}\n\n"
                f"Generate a strictly valid JSON array of executable browser actions to accomplish this goal.\n"
                f"Allowed action objects:\n"
                f"  - {{\"action\": \"goto\", \"url\": \"https://...\"}}\n"
                f"  - {{\"action\": \"click\", \"target\": \"selector or button text\"}}\n"
                f"  - {{\"action\": \"fill\", \"target\": \"field name/placeholder\", \"value\": \"text\"}}\n"
                f"  - {{\"action\": \"download\", \"trigger\": \"download button or url\"}}\n"
                f"  - {{\"action\": \"scrape_table\", \"output_filename\": \"name.csv\"}}\n"
                f"  - {{\"action\": \"wait\", \"seconds\": 2}}\n\n"
                f"Output strictly valid JSON with no backticks, markdown, or commentary."
            )
            raw_reply = ai_chat([{"role": "user", "content": prompt}], model=DEFAULT_REASONING_MODEL, timeout=12)
            if raw_reply:
                cleaned_json = re.sub(r'```(?:json)?', '', raw_reply).strip('` \n')
                actions = json.loads(cleaned_json)
                if isinstance(actions, list) and len(actions) > 0:
                    return self._execute_plan(actions)
        except Exception as e:
            print(f"[INTELLIGENT WEB AGENT NOTICE]: {e}")

        # Fallback: Navigate to URL if instruction contains one
        url_match = re.search(r'(https?://\S+)', clean)
        if url_match:
            nav = self.engine.navigate(url_match.group(1))
            return True, f"Navigated to {nav.get('title', 'page')} at {nav['url']}, sir."

        return False, ""

    def _execute_plan(self, actions: List[Dict[str, Any]]) -> Tuple[bool, str]:
        """Executes a parsed multi-action browser automation plan sequentially."""
        executed = 0
        last_info = ""

        for step in actions:
            act = step.get("action", "").lower()
            try:
                if act == "goto":
                    nav = self.engine.navigate(step["url"])
                    last_info = f"Navigated to {nav['title']}"
                    executed += 1
                elif act == "click":
                    self.engine.click(step["target"])
                    last_info = f"Clicked '{step['target']}'"
                    executed += 1
                elif act == "fill":
                    self.engine.fill_input(step["target"], step["value"])
                    last_info = f"Filled '{step['target']}'"
                    executed += 1
                elif act == "download":
                    d_res = self.engine.download_file(step["trigger"])
                    if d_res.get("success"):
                        last_info = f"Downloaded {d_res['filename']}"
                    executed += 1
                elif act == "scrape_table":
                    t_res = self.engine.scrape_table_to_csv(output_filename=step.get("output_filename"))
                    if t_res.get("success"):
                        last_info = f"Exported {t_res['row_count']} rows to {t_res['filename']}"
                    executed += 1
                elif act == "wait":
                    time.sleep(float(step.get("seconds", 1)))
                    executed += 1
            except Exception as e:
                print(f"[PLAN STEP ERROR]: {act} -> {e}")

        if executed > 0:
            return True, f"Completed web automation plan with {executed} actions, sir. {last_info}."
        return False, ""


from concurrent.futures import ThreadPoolExecutor

# Dedicated worker thread pool to completely isolate Playwright Sync API from any ambient asyncio loops
BROWSER_WORKER_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="HydraBrowserWorker")

# Global Engine Singleton
INTERACTIVE_WEB = InteractiveWebEngine()
WEB_AGENT = IntelligentWebAgent(INTERACTIVE_WEB)


# =============================================================================
# 4. DISPATCHER HANDLER (for nlp_dispatcher.py)
# =============================================================================

def handle_web_automation_command(command: str, sys_driver: Any = None) -> Tuple[bool, str]:
    """
    Entrypoint hooked into nlp_dispatcher.py to intercept Phase 12 commands:
    - "scrape table from <url>" / "export table"
    - "download <file> from <url>" / "download the latest invoice"
    - "fill form <field>=<val>"
    - "save browser session as <name>" / "load browser session <name>"
    - Dynamic SPA navigation & multi-step web queries
    """
    clean = command.strip().lower()

    # Trigger patterns for Phase 12
    web_triggers = [
        "scrape table", "export table", "save table",
        "download invoice", "download report", "download file",
        "fill form", "autofill form", "autofill",
        "save browser session", "load browser session", "use browser session",
        "web automation", "automate browser", "interactive web"
    ]

    is_explicit_phase12 = any(t in clean for t in web_triggers)
    is_browser_nav_complex = (
        clean.startswith("go to http") or clean.startswith("navigate to http") or
        clean.startswith("open http")
    ) and any(w in clean for w in ["and fill", "and click", "and download", "and scrape", "then fill", "then click"])

    if is_explicit_phase12 or is_browser_nav_complex:
        # Submit to dedicated worker thread to prevent Playwright Sync API / asyncio loop conflicts
        future = BROWSER_WORKER_POOL.submit(WEB_AGENT.execute_natural_instruction, command)
        return future.result()

    return False, ""
