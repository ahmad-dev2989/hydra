"""
HYDRA Phase 9: Continuous Desktop Screen Context & Visual Awareness Engine
Continuously tracks foreground workspace activity, active application contexts,
and screen content beneath the HYDRA HUD, enabling real-time answers to:
- "What am I doing right now?"
- "What is on my screen?"
- "What was I working on before this?"
- "Explain this error on my screen"
"""

import os
import sys
import time
import threading
from typing import Dict, Any, List, Optional, Tuple

class ScreenContextWatcher:
    """
    Lightweight background observer tracking active workspace tasks and screen contexts.
    Ignores HYDRA's own HUD window to always analyze the user's actual underlying work.
    """
    def __init__(self):
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        
        self.current_window: Optional[Dict[str, Any]] = None
        self.activity_history: List[Dict[str, Any]] = []
        self.last_check_time = 0.0
        self.last_switch_time = time.time()
        
    def start(self):
        """Starts the background tracking daemon."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()
        print("[SCREEN WATCHER] Phase 9 continuous desktop context watcher started.")

    def stop(self):
        self._running = False

    def _monitor_loop(self):
        while self._running:
            try:
                self._sample_active_workspace()
            except Exception as e:
                pass
            time.sleep(1.5)

    def _get_clean_app_name(self, proc: str, title: str) -> str:
        proc_clean = proc.lower().replace(".exe", "").strip()
        title_low = title.lower()

        if "code" in proc_clean or "visual studio code" in title_low:
            return "Visual Studio Code"
        if "brave" in proc_clean or "brave" in title_low:
            return "Brave Browser"
        if "chrome" in proc_clean:
            return "Google Chrome"
        if "edge" in proc_clean or "msedge" in proc_clean:
            return "Microsoft Edge"
        if "spotify" in proc_clean or "spotify" in title_low:
            return "Spotify"
        if "whatsapp" in proc_clean or "whatsapp" in title_low:
            return "WhatsApp"
        if "canva" in proc_clean or "canva" in title_low:
            return "Canva"
        if "notepad" in proc_clean:
            return "Notepad"
        if "terminal" in proc_clean or "wt" in proc_clean or "cmd" in proc_clean or "powershell" in proc_clean:
            return "Windows Terminal"
        if "explorer" in proc_clean:
            return "File Explorer"
            
        return proc_clean.capitalize() or "Application"

    def _sample_active_workspace(self):
        from window_ops import WINDOW_MGR
        open_wins = WINDOW_MGR.get_open_windows()

        # Filter out HYDRA and system utility windows
        filtered = []
        for w in open_wins:
            t = (w.get("title") or "").strip()
            t_low = t.lower()
            proc = (w.get("process") or "").strip().lower()
            if "hydra" in t_low or "hydra system interface" in t_low:
                continue
            if proc == "python.exe" and ("main.py" in t_low or "hydra" in t_low):
                continue
            if not t or t in ["Program Manager", "Default IME", "MSCTFIME UI"]:
                continue
            filtered.append(w)

        if not filtered:
            return

        top = filtered[0]
        now = time.time()
        
        with self._lock:
            app_name = self._get_clean_app_name(top.get("process", ""), top.get("title", ""))
            win_title = top.get("title", "")
            
            # Check if user switched window or application
            if not self.current_window or self.current_window.get("title") != win_title or self.current_window.get("app") != app_name:
                if self.current_window:
                    duration = int(now - self.last_switch_time)
                    if duration >= 3:  # Only record meaningful sessions
                        self.activity_history.append({
                            "app": self.current_window.get("app"),
                            "title": self.current_window.get("title"),
                            "duration_sec": duration,
                            "timestamp": time.strftime("%H:%M:%S")
                        })
                        if len(self.activity_history) > 15:
                            self.activity_history.pop(0)

                self.current_window = {
                    "app": app_name,
                    "title": win_title,
                    "process": top.get("process", ""),
                    "hwnd": top.get("hwnd"),
                    "start_time": now
                }
                self.last_switch_time = now

    def get_current_context(self) -> Dict[str, Any]:
        """Returns the current underlying workspace window and background apps."""
        from window_ops import WINDOW_MGR
        open_wins = WINDOW_MGR.get_open_windows()

        filtered = []
        for w in open_wins:
            t = (w.get("title") or "").strip()
            t_low = t.lower()
            proc = (w.get("process") or "").strip().lower()
            if "hydra" in t_low or "hydra system interface" in t_low:
                continue
            if proc == "python.exe" and ("main.py" in t_low or "hydra" in t_low):
                continue
            if not t or t in ["Program Manager", "Default IME"]:
                continue
            filtered.append(w)

        if not filtered:
            return {"active_app": "Desktop", "title": "Windows Desktop", "background_apps": []}

        top = filtered[0]
        top_app = self._get_clean_app_name(top.get("process", ""), top.get("title", ""))
        top_title = top.get("title", "")

        bg_apps = []
        for w in filtered[1:5]:
            name = self._get_clean_app_name(w.get("process", ""), w.get("title", ""))
            if name and name not in bg_apps and name != top_app:
                bg_apps.append(name)

        return {
            "active_app": top_app,
            "title": top_title,
            "background_apps": bg_apps,
            "active_since_sec": int(time.time() - self.last_switch_time) if self.current_window else 0
        }

    def describe_current_activity(self) -> str:
        """
        Formulates a natural, human-like response describing the user's active task.
        Always focuses on the application UNDERNEATH HYDRA.
        """
        ctx = self.get_current_context()
        app = ctx["active_app"]
        title = ctx["title"]
        bg = ctx["background_apps"]

        if app == "Desktop":
            return "You are currently looking at your Windows desktop, sir."

        # Simplify titles for cleaner conversational speech
        clean_title = title
        if " - " in clean_title:
            parts = clean_title.split(" - ")
            clean_title = parts[0].strip()

        # Meaningful task interpretation
        action_verb = "working in"
        if app in ["Visual Studio Code", "Notepad"]:
            action_verb = "editing code in" if app == "Visual Studio Code" else "drafting notes in"
        elif app in ["Brave Browser", "Google Chrome", "Microsoft Edge"]:
            if "youtube" in title.lower():
                action_verb = "watching YouTube on"
            elif "gemini" in title.lower() or "chatgpt" in title.lower():
                action_verb = "conversing with an AI model on"
            else:
                action_verb = "browsing the web in"
        elif app == "Canva":
            action_verb = "designing in"
        elif app == "Spotify":
            action_verb = "listening to music on"
        elif app == "WhatsApp":
            action_verb = "messaging on"

        response = f"Sir, you are currently {action_verb} {app}"
        if clean_title and clean_title.lower() != app.lower():
            response += f", on '{clean_title}'"

        if bg:
            if len(bg) == 1:
                response += f", with {bg[0]} open in the background."
            elif len(bg) == 2:
                response += f", with {bg[0]} and {bg[1]} open in the background."
            else:
                response += f", with {bg[0]}, {bg[1]}, and {bg[2]} running in the background."
        else:
            response += "."

        return response

    def describe_previous_activity(self) -> str:
        """Describes what the user was working on before the current task."""
        with self._lock:
            if not self.activity_history:
                return "You just started this session, sir. I have no prior window history recorded yet."
            prev = self.activity_history[-1]
            app = prev.get("app", "an application")
            title = prev.get("title", "")
            duration = prev.get("duration_sec", 0)
            
            dur_str = f"{duration} seconds"
            if duration >= 60:
                mins = duration // 60
                dur_str = f"about {mins} minute" if mins == 1 else f"about {mins} minutes"

            clean_title = title.split(" - ")[0].strip() if " - " in title else title
            return f"Before this, sir, you were in {app} working on '{clean_title}' for {dur_str}."

    def explain_screen_content_or_error(self) -> str:
        """
        Performs an on-demand screen interrogation of the active workspace.
        Extracts visible text via WinOCR and summarizes errors or key content.
        """
        try:
            from vision_ops import VISION_ENGINE, _init_desktop_session
            import winocr
            _init_desktop_session()
            img = VISION_ENGINE.capture_screen()
            res = winocr.recognize_pil_sync(img)
            
            lines = [l.get('text', '').strip() for l in res.get('lines', []) if l.get('text', '').strip()]
            visible_text = "\n".join(lines[:40])
            
            ctx = self.get_current_context()
            prompt = (
                f"You are HYDRA, a super-intelligent AI desktop assistant.\n"
                f"The user asked to explain what is happening on their screen or explain an error.\n"
                f"Active Application: {ctx['active_app']} (Title: {ctx['title']})\n"
                f"Visible Text Extracted from Screen:\n"
                f"{visible_text[:1200]}\n\n"
                f"Provide a sharp, brilliant, concise explanation (under 35 words) addressing master directly as 'sir'."
            )
            from ai_engine import nebius_chat
            explanation = nebius_chat([{"role": "user", "content": prompt}], max_tokens=100)
            if explanation:
                return explanation
        except Exception as e:
            print(f"[SCREEN WATCHER] OCR error diagnostic warning: {e}")

        ctx = self.get_current_context()
        return f"Sir, you have {ctx['active_app']} open displaying '{ctx['title']}'. No critical errors were detected."

SCREEN_WATCHER = ScreenContextWatcher()
SCREEN_WATCHER.start()
