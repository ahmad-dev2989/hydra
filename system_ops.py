import subprocess
import os
import psutil
import difflib
import shutil
import winreg
import json
import time
import re
import threading
import ctypes
import socket
from typing import Dict, List, Optional, Tuple

try:
    import pyautogui
    import pyperclip
    pyautogui.FAILSAFE = False
    HAS_PYAUTOGUI = True
except ImportError:
    pyautogui = None
    pyperclip = None
    HAS_PYAUTOGUI = False

def is_unwanted_target(name: str, path: str = "") -> bool:
    low = (name + " " + path).lower()
    unwanted = [
        "uninstall", "unins000", "uninstaller", "installer", "setup.exe",
        "readme", "documentation", "manual", "license", "release notes",
        "help", "troubleshoot", "diagnostic", "feedback hub", "get help"
    ]
    return any(u in low for u in unwanted)

class AppIndexEngine:
    def __init__(self, cache_file: str = None):
        if cache_file is None:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            cache_file = os.path.join(base_dir, "apps_index_cache.json")
        self.cache_file = cache_file
        self.lock = threading.Lock()
        self.apps: Dict[str, dict] = {}
        
        self.aliases: Dict[str, str] = {
            "calc": "calculator",
            "calculator": "calculator",
            "cal": "calculator",
            "cmd": "command prompt",
            "terminal": "command prompt",
            "powershell": "powershell",
            "ps": "powershell",
            "code": "visual studio code",
            "vscode": "visual studio code",
            "vs code": "visual studio code",
            "vscod": "visual studio code",
            "chrome": "google chrome",
            "google chrome": "google chrome",
            "crom": "google chrome",
            "chome": "google chrome",
            "browser": "brave",
            "web browser": "brave",
            "brave browser": "brave",
            "brav": "brave",
            "word": "word",
            "winword": "word",
            "ms word": "word",
            "wrd": "word",
            "excel": "excel",
            "ms excel": "excel",
            "excl": "excel",
            "powerpoint": "powerpoint",
            "ppt": "powerpoint",
            "ms powerpoint": "powerpoint",
            "note": "notepad",
            "notepad": "notepad",
            "notpad": "notepad",
            "text editor": "notepad",
            "paint": "paint",
            "mspaint": "paint",
            "explorer": "file explorer",
            "files": "file explorer",
            "my computer": "file explorer",
            "this pc": "file explorer",
            "taskmgr": "task manager",
            "task manager": "task manager",
            "settings": "settings",
            "windows settings": "settings",
            "config": "settings",
            "canva": "canva",
            "cana": "canva",
            "canv": "canva",
            "cnva": "canva",
            "spotify": "spotify",
            "spotfy": "spotify",
            "music": "spotify",
            "discord": "discord",
            "dc": "discord",
            "whatsapp": "whatsapp",
            "watsapp": "whatsapp",
            "watsap": "whatsapp",
            "telegram": "telegram",
            "tg": "telegram",
            "edge": "microsoft edge",
            "ms edge": "microsoft edge",
            "fl studio": "fl studio 2024",
            "fl": "fl studio 2024",
            "camera": "camera",
            "snip": "snipping tool",
            "snipping tool": "snipping tool",
        }
        self.builtin_system_apps = {
            "settings": {"name": "Settings", "target": "ms-settings:", "type": "PROTOCOL", "exe_name": "SystemSettings.exe"},
            "control panel": {"name": "Control Panel", "target": "control", "type": "SYSTEM", "exe_name": "control.exe"},
            "command prompt": {"name": "Command Prompt", "target": "cmd", "type": "SYSTEM", "exe_name": "cmd.exe"},
            "powershell": {"name": "PowerShell", "target": "powershell", "type": "SYSTEM", "exe_name": "powershell.exe"},
            "task manager": {"name": "Task Manager", "target": "taskmgr", "type": "SYSTEM", "exe_name": "Taskmgr.exe"},
            "calculator": {"name": "Calculator", "target": "calc", "type": "SYSTEM", "exe_name": "CalculatorApp.exe"},
            "notepad": {"name": "Notepad", "target": "notepad", "type": "SYSTEM", "exe_name": "Notepad.exe"},
            "paint": {"name": "Paint", "target": "mspaint", "type": "SYSTEM", "exe_name": "mspaint.exe"},
            "snipping tool": {"name": "Snipping Tool", "target": "snippingtool", "type": "SYSTEM", "exe_name": "SnippingTool.exe"},
            "file explorer": {"name": "File Explorer", "target": "explorer", "type": "SYSTEM", "exe_name": "explorer.exe"},
            "bluetooth settings": {"name": "Bluetooth Settings", "target": "ms-settings:bluetooth", "type": "PROTOCOL", "exe_name": "SystemSettings.exe"},
            "wifi settings": {"name": "Wi-Fi Settings", "target": "ms-settings:network-wifi", "type": "PROTOCOL", "exe_name": "SystemSettings.exe"},
            "network settings": {"name": "Network Settings", "target": "ms-settings:network", "type": "PROTOCOL", "exe_name": "SystemSettings.exe"},
            "sound settings": {"name": "Sound Settings", "target": "ms-settings:sound", "type": "PROTOCOL", "exe_name": "SystemSettings.exe"},
            "display settings": {"name": "Display Settings", "target": "ms-settings:display", "type": "PROTOCOL", "exe_name": "SystemSettings.exe"},
            "camera": {"name": "Camera", "target": "microsoft.windows.camera:", "type": "PROTOCOL", "exe_name": "WindowsCamera.exe"},
        }
        self.apps = dict(self.builtin_system_apps)

    def load_cache(self) -> bool:
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    cached = json.load(f)
                    with self.lock:
                        self.apps.update(cached)
                return True
            except Exception as e:
                print(f"[AppIndexEngine] Cache read error: {e}")
        return False

    def save_cache(self):
        try:
            with open(self.cache_file, "w", encoding="utf-8") as f:
                with self.lock:
                    json.dump(self.apps, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[AppIndexEngine] Cache write error: {e}")

    def scan_system(self):
        start_time = time.time()
        discovered = dict(self.builtin_system_apps)

        # 1. Scan UWP / StartApps via PowerShell Get-StartApps
        try:
            ps_cmd = 'Get-StartApps | ConvertTo-Json'
            res = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', ps_cmd],
                                 capture_output=True, text=True, encoding='utf-8', timeout=6)
            if res.returncode == 0 and res.stdout.strip():
                try:
                    data = json.loads(res.stdout)
                    if isinstance(data, dict): data = [data]
                    for item in data:
                        name = item.get("Name", "").strip()
                        appid = item.get("AppID", "").strip()
                        if name and appid and not is_unwanted_target(name, appid):
                            low_name = name.lower()
                            target = f"shell:AppsFolder\\{appid}"
                            exe_candidate = name + ".exe"
                            if "\\" in appid:
                                exe_candidate = os.path.basename(appid)

                            entry = {
                                "name": name,
                                "target": target,
                                "type": "UWP",
                                "exe_name": exe_candidate
                            }
                            discovered[low_name] = entry
                            
                            clean_name = re.sub(r'[^a-zA-Z0-9 ]', '', low_name).strip()
                            if clean_name and clean_name != low_name:
                                discovered[clean_name] = entry
                except Exception as ex:
                    print(f"[AppIndexEngine] JSON parse error: {ex}")
        except Exception as e:
            print(f"[AppIndexEngine] PowerShell Get-StartApps scan error: {e}")

        # 2. LocalAppData Programs
        local_programs = os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Programs')
        if os.path.exists(local_programs):
            for root, dirs, files in os.walk(local_programs):
                for f in files:
                    if f.lower().endswith('.exe') and not is_unwanted_target(f, root):
                        app_name = os.path.splitext(f)[0]
                        low_name = app_name.lower().strip()
                        full_path = os.path.join(root, f)
                        entry = {
                            "name": app_name,
                            "target": full_path,
                            "type": "EXE",
                            "exe_name": f
                        }
                        if low_name not in discovered or discovered[low_name]["type"] != "UWP":
                            discovered[low_name] = entry

        # 3. Scan Start Menu and Desktop .lnk Shortcuts
        paths_to_scan = [
            os.path.join(os.environ.get('PROGRAMDATA', r'C:\ProgramData'), r'Microsoft\Windows\Start Menu\Programs'),
            os.path.join(os.environ.get('APPDATA', ''), r'Microsoft\Windows\Start Menu\Programs'),
            os.path.join(os.environ.get('USERPROFILE', ''), 'Desktop'),
            r'C:\Users\Public\Desktop',
        ]

        for base_path in paths_to_scan:
            if not os.path.exists(base_path):
                continue
            for root, _, files in os.walk(base_path):
                for f in files:
                    if is_unwanted_target(f, root):
                        continue
                    low_f = f.lower()
                    if low_f.endswith('.lnk') or low_f.endswith('.url'):
                        app_name = os.path.splitext(f)[0]
                        low_name = app_name.lower().strip()
                        full_path = os.path.join(root, f)
                        entry = {
                            "name": app_name,
                            "target": full_path,
                            "type": "LNK",
                            "exe_name": app_name + ".exe"
                        }
                        if low_name not in discovered:
                            discovered[low_name] = entry
                        clean_name = re.sub(r'[^a-zA-Z0-9 ]', '', low_name).strip()
                        if clean_name and clean_name not in discovered:
                            discovered[clean_name] = entry

        # 4. Scan Registry App Paths
        reg_roots = [
            (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths'),
            (winreg.HKEY_CURRENT_USER, r'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths')
        ]
        for hkey, subkey in reg_roots:
            try:
                with winreg.OpenKey(hkey, subkey) as key:
                    num_subkeys = winreg.QueryInfoKey(key)[0]
                    for i in range(num_subkeys):
                        try:
                            key_name = winreg.EnumKey(key, i)
                            if is_unwanted_target(key_name):
                                continue
                            with winreg.OpenKey(key, key_name) as app_key:
                                exe_path, _ = winreg.QueryValueEx(app_key, '')
                                if exe_path and os.path.exists(exe_path) and not is_unwanted_target(exe_path):
                                    base_name = os.path.splitext(key_name)[0].lower()
                                    display_name = base_name.capitalize()
                                    entry = {
                                        "name": display_name,
                                        "target": exe_path,
                                        "type": "EXE",
                                        "exe_name": os.path.basename(exe_path)
                                    }
                                    if base_name not in discovered:
                                        discovered[base_name] = entry
                        except Exception:
                            pass
            except Exception:
                pass

        with self.lock:
            self.apps.update(discovered)
        self.save_cache()
        elapsed = time.time() - start_time
        print(f"[AppIndexEngine] Background indexer finished in {elapsed:.2f}s. {len(self.apps)} apps registered.")

    def start_background_indexer(self):
        thread = threading.Thread(target=self.scan_system, daemon=True)
        thread.start()

    def clean_query(self, raw_query: str) -> str:
        if not raw_query:
            return ""
        q = raw_query.lower().strip()
        q = re.sub(r'[!?,.:;]', '', q).strip()

        # Remove leading assistant wake words
        wake_words = ["hydra ", "hey hydra ", "ok hydra ", "okay hydra "]
        for w in wake_words:
            if q.startswith(w):
                q = q[len(w):].strip()
                break

        # Remove conversational request phrases
        polite_prefixes = [
            "can you please ", "could you please ", "can you ", "could you ",
            "please ", "i want to ", "i would like to ", "help me ", "go ahead and "
        ]
        for p in polite_prefixes:
            if q.startswith(p):
                q = q[len(p):].strip()
                break

        # Remove command verbs for open/launch/close/kill
        verb_prefixes = [
            "open the app ", "launch the app ", "start the app ", "run the app ",
            "close the app ", "kill the app ", "terminate the app ",
            "open the application ", "launch the application ",
            "open the ", "launch the ", "start the ", "run the ",
            "close the ", "kill the ", "terminate the ",
            "open app ", "launch app ", "start app ", "run app ",
            "close app ", "kill app ",
            "open ", "launch ", "start ", "run ", "pull up ", "bring up ", "fire up ",
            "close ", "kill ", "terminate ", "shut down ", "stop "
        ]
        for v in verb_prefixes:
            if q.startswith(v):
                q = q[len(v):].strip()
                break

        # Remove trailing fillers
        trailing_fillers = [
            " please", " for me", " quickly", " right now", " immediately",
            " app", " application", " software"
        ]
        for tf in trailing_fillers:
            if q.endswith(tf):
                q = q[:-len(tf)].strip()

        return q

    def resolve_app(self, raw_query: str) -> Tuple[Optional[dict], str, bool]:
        """
        Returns: (app_dict_or_None, display_name, is_typo_corrected)
        """
        clean = self.clean_query(raw_query)
        if not clean:
            return None, "", False

        with self.lock:
            apps_snapshot = dict(self.apps)

        # Stage 1: Explicit Alias
        if clean in self.aliases:
            alias_key = self.aliases[clean]
            if alias_key in apps_snapshot:
                is_typo = (clean != alias_key)
                return apps_snapshot[alias_key], apps_snapshot[alias_key]["name"], is_typo
            clean = alias_key

        # Stage 2: Exact Key Match
        if clean in apps_snapshot:
            return apps_snapshot[clean], apps_snapshot[clean]["name"], False

        # Stage 3: Exact Word Boundary Match (e.g. 'code' -> 'Visual Studio Code')
        candidate_keys = list(apps_snapshot.keys())
        for k in candidate_keys:
            words = k.split()
            if clean in words:
                return apps_snapshot[k], apps_snapshot[k]["name"], False

        # Stage 4: Substring match (for length >= 4)
        if len(clean) >= 4:
            for k in candidate_keys:
                if clean in k:
                    return apps_snapshot[k], apps_snapshot[k]["name"], False

        # Stage 5: Typo / Fuzzy Matching via difflib SequenceMatcher
        best_key = None
        best_score = 0.0

        for k in candidate_keys:
            ratio = difflib.SequenceMatcher(None, clean, k).ratio()
            disp_ratio = difflib.SequenceMatcher(None, clean, apps_snapshot[k]["name"].lower()).ratio()
            score = max(ratio, disp_ratio)
            
            len_diff = abs(len(clean) - len(k))
            if len_diff <= 2 and score > best_score:
                best_score = score
                best_key = k
            elif score > best_score:
                best_score = score
                best_key = k

        threshold = 0.55 if len(clean) >= 4 else 0.70
        if best_key and best_score >= threshold:
            matched_app = apps_snapshot[best_key]
            return matched_app, matched_app["name"], True

        return None, clean, False

    def launch(self, raw_query: str) -> Tuple[bool, str, str]:
        """
        Resolves app and launches it immediately.
        Returns: (success, spoken_response, app_name)
        """
        app_entry, app_name, is_typo = self.resolve_app(raw_query)
        if not app_entry:
            clean = self.clean_query(raw_query)
            try:
                subprocess.Popen(f"cmd /c start {clean}", shell=True)
                return True, f"Attempting to launch {clean}, sir.", clean
            except Exception:
                return False, f"Could not find an application matching '{clean}' on your laptop, sir.", clean

        target = app_entry["target"]
        target_type = app_entry["type"]
        display_name = app_entry["name"]

        try:
            if target_type == "UWP":
                os.startfile(target)
            elif target_type == "LNK":
                os.startfile(target)
            elif target_type == "EXE":
                if os.path.exists(target):
                    subprocess.Popen([target])
                else:
                    os.startfile(target)
            elif target_type == "PROTOCOL":
                os.startfile(target)
            elif target_type == "SYSTEM":
                subprocess.Popen(f"cmd /c start {target}", shell=True)
            else:
                os.startfile(target)

            if is_typo:
                spoken = f"Identified {display_name}. Opening {display_name} on your screen, sir."
            else:
                spoken = f"Opening {display_name}, sir."
            return True, spoken, display_name
        except Exception as e:
            try:
                subprocess.Popen(f"cmd /c start {target}", shell=True)
                return True, f"Opening {display_name}, sir.", display_name
            except Exception as e2:
                return False, f"Error launching {display_name}: {e2}", display_name

    def close(self, raw_query: str) -> Tuple[bool, str, str]:
        """
        Resolves app and terminates running process.
        Returns: (success, spoken_response, app_name)
        """
        app_entry, app_name, is_typo = self.resolve_app(raw_query)
        target_name = app_name if app_entry else self.clean_query(raw_query)

        exe_names = []
        if app_entry and app_entry.get("exe_name"):
            exe_names.append(app_entry["exe_name"])
        clean_target = target_name.lower().replace(" ", "")
        exe_names.append(f"{target_name}.exe")
        exe_names.append(f"{clean_target}.exe")

        closed_any = False
        try:
            for proc in psutil.process_iter(['name', 'exe', 'pid']):
                try:
                    pname = (proc.info.get('name') or "").lower()
                    if any(candidate.lower() == pname for candidate in exe_names) or (clean_target in pname and len(clean_target) >= 4):
                        proc.kill()
                        closed_any = True
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        except Exception:
            pass

        for exe in set(exe_names):
            try:
                res = subprocess.run(["taskkill", "/f", "/im", exe], capture_output=True, text=True)
                if res.returncode == 0:
                    closed_any = True
            except Exception:
                pass

        if closed_any:
            return True, f"Closed {target_name}, sir.", target_name
        else:
            return False, f"Could not find an active {target_name} process to close, sir.", target_name

class HardwareDriver:
    def __init__(self):
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass

        if HAS_PYAUTOGUI:
            try:
                pyautogui.FAILSAFE = False
                pyautogui.PAUSE = 0.02
                self.screen_width, self.screen_height = pyautogui.size()
            except Exception:
                self.screen_width = ctypes.windll.user32.GetSystemMetrics(0)
                self.screen_height = ctypes.windll.user32.GetSystemMetrics(1)
        else:
            self.screen_width = ctypes.windll.user32.GetSystemMetrics(0)
            self.screen_height = ctypes.windll.user32.GetSystemMetrics(1)

        self.hud_window_provider = None

    def set_hud_window_provider(self, provider_fn):
        self.hud_window_provider = provider_fn

    def get_hud_window_bounds(self) -> Tuple[int, int, int, int]:
        if self.hud_window_provider:
            try:
                res = self.hud_window_provider()
                if res and len(res) == 4 and res[2] > 50 and res[3] > 50:
                    return int(res[0]), int(res[1]), int(res[2]), int(res[3])
            except Exception:
                pass

        try:
            from ctypes import wintypes
            hwnd = ctypes.windll.user32.FindWindowW(None, "HYDRA System Interface")
            if hwnd:
                rect = wintypes.RECT()
                ctypes.windll.user32.GetClientRect(hwnd, ctypes.byref(rect))
                pt = wintypes.POINT(0, 0)
                ctypes.windll.user32.ClientToScreen(hwnd, ctypes.byref(pt))
                ww = rect.right - rect.left
                wh = rect.bottom - rect.top
                if ww > 50 and wh > 50:
                    return (int(pt.x), int(pt.y), int(ww), int(wh))
        except Exception:
            pass

        def_w, def_h = 1200, 750
        return ((self.screen_width - def_w) // 2, (self.screen_height - def_h) // 2, def_w, def_h)

    def locate_template_on_screen(self, template_paths: List[str], confidence_threshold: float = 0.78, search_region: Optional[Tuple[int, int, int, int]] = None) -> Optional[Tuple[int, int]]:
        try:
            import cv2
            import numpy as np
            from PIL import ImageGrab

            user32 = ctypes.windll.user32
            hwinsta = user32.OpenWindowStationW('WinSta0', False, 0x037F)
            if hwinsta:
                user32.SetProcessWindowStation(hwinsta)
                hdesk = user32.OpenDesktopW('Default', 0, False, 0x01FF)
                if hdesk:
                    user32.SetThreadDesktop(hdesk)

            bbox = None
            offset_x, offset_y = 0, 0
            if search_region:
                rx, ry, rw, rh = search_region
                bbox = (rx, ry, rx + rw, ry + rh)
                offset_x, offset_y = rx, ry

            screen_pil = ImageGrab.grab(bbox=bbox)
            screen_np = np.array(screen_pil)
            if screen_np.size == 0:
                return None
            screen_bgr = cv2.cvtColor(screen_np, cv2.COLOR_RGB2BGR)

            best_val = -1.0
            best_pt = None

            for t_file in template_paths:
                full_path = os.path.abspath(t_file)
                if not os.path.exists(full_path):
                    continue
                tmpl = cv2.imread(full_path)
                if tmpl is None or tmpl.shape[0] > screen_bgr.shape[0] or tmpl.shape[1] > screen_bgr.shape[1]:
                    continue

                res = cv2.matchTemplate(screen_bgr, tmpl, cv2.TM_CCOEFF_NORMED)
                min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(res)
                if max_val > best_val:
                    best_val = max_val
                    best_pt = (offset_x + max_loc[0] + tmpl.shape[1] // 2, offset_y + max_loc[1] + tmpl.shape[0] // 2)

            if best_val >= confidence_threshold and best_pt is not None:
                return best_pt
        except Exception:
            pass
        return None

    def get_position(self):
        if HAS_PYAUTOGUI:
            try:
                x, y = pyautogui.position()
                return int(x), int(y)
            except Exception:
                pass
        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
        pt = POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        return int(pt.x), int(pt.y)

    def move_to(self, x, y, duration=0.2):
        x = max(0, min(self.screen_width - 1, int(x)))
        y = max(0, min(self.screen_height - 1, int(y)))

        # 1. Smooth glide if PyAutoGUI is available
        if HAS_PYAUTOGUI:
            try:
                pyautogui.moveTo(x, y, duration=duration)
            except Exception:
                pass

        # 2. Hard lock to exact physical screen coordinates (eliminates DPI shifts)
        try:
            ctypes.windll.user32.SetCursorPos(x, y)
        except Exception:
            pass

        # 3. Low-level Windows hardware driver event
        try:
            MOUSEEVENTF_MOVE = 0x0001
            MOUSEEVENTF_ABSOLUTE = 0x8000
            norm_x = int(x * 65535 / (self.screen_width - 1)) if self.screen_width > 1 else 0
            norm_y = int(y * 65535 / (self.screen_height - 1)) if self.screen_height > 1 else 0
            ctypes.windll.user32.mouse_event(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE, norm_x, norm_y, 0, 0)
        except Exception:
            pass

        return True, f"Moved mouse to {x}, {y}, sir."

    def move_relative(self, dx, dy, duration=0.2):
        cur_x, cur_y = self.get_position()
        target_x = max(0, min(self.screen_width - 1, cur_x + int(dx)))
        target_y = max(0, min(self.screen_height - 1, cur_y + int(dy)))
        return self.move_to(target_x, target_y, duration=duration)

    def click(self, button="left", clicks=1):
        down_flag = 0x0002
        up_flag = 0x0004
        if button.lower() == "right":
            down_flag, up_flag = 0x0008, 0x0010
        elif button.lower() == "middle":
            down_flag, up_flag = 0x0020, 0x0040

        # Native hardware click at locked position ensures 100% precision with zero shift
        for _ in range(clicks):
            ctypes.windll.user32.mouse_event(down_flag, 0, 0, 0, 0)
            time.sleep(0.04)
            ctypes.windll.user32.mouse_event(up_flag, 0, 0, 0, 0)
            if clicks > 1:
                time.sleep(0.06)
                if clicks > 1:
                    time.sleep(0.08)
        click_type = "Double clicked" if clicks == 2 else ("Triple clicked" if clicks == 3 else f"{button.capitalize()} clicked")
        return True, f"{click_type}, sir."

    def click_at(self, x, y, button="left", clicks=1):
        self.move_to(x, y, duration=0.1)
        return self.click(button=button, clicks=clicks)

    def mouse_down(self, button="left"):
        if HAS_PYAUTOGUI:
            pyautogui.mouseDown(button=button)
        else:
            flag = 0x0008 if button.lower() == "right" else 0x0002
            ctypes.windll.user32.mouse_event(flag, 0, 0, 0, 0)
        return True, f"Holding {button} mouse button, sir."

    def mouse_up(self, button="left"):
        if HAS_PYAUTOGUI:
            pyautogui.mouseUp(button=button)
        else:
            flag = 0x0010 if button.lower() == "right" else 0x0004
            ctypes.windll.user32.mouse_event(flag, 0, 0, 0, 0)
        return True, f"Released {button} mouse button, sir."

    def drag_to(self, x, y, duration=0.4):
        x = max(0, min(self.screen_width - 1, int(x)))
        y = max(0, min(self.screen_height - 1, int(y)))
        if HAS_PYAUTOGUI:
            pyautogui.dragTo(x, y, duration=duration)
        else:
            self.mouse_down("left")
            time.sleep(0.05)
            self.move_to(x, y)
            time.sleep(0.05)
            self.mouse_up("left")
        return True, f"Dragged to {x}, {y}, sir."

    def drag_relative(self, dx, dy, duration=0.4):
        if HAS_PYAUTOGUI:
            pyautogui.dragRel(int(dx), int(dy), duration=duration)
        else:
            cur_x, cur_y = self.get_position()
            self.drag_to(cur_x + int(dx), cur_y + int(dy))
        return True, f"Dragged item, sir."

    def scroll(self, amount):
        if HAS_PYAUTOGUI:
            pyautogui.scroll(int(amount * 120))
        else:
            ctypes.windll.user32.mouse_event(0x0800, 0, 0, int(amount * 120), 0)
        direction = "up" if amount > 0 else "down"
        return True, f"Scrolled {direction}, sir."

    def get_brightness(self):
        try:
            res = subprocess.check_output(
                ["powershell", "-NoProfile", "-Command", "(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightness).CurrentBrightness"],
                timeout=2, creationflags=0x08000000
            )
            return int(res.decode().strip())
        except Exception:
            return 60

    def set_brightness(self, level: int):
        level = max(0, min(100, int(level)))
        try:
            cmd = f"(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods).WmiSetBrightness(1, {level})"
            subprocess.Popen(
                ["powershell", "-NoProfile", "-Command", cmd],
                creationflags=0x08000000
            )
            return True, f"Display brightness set to {level} percent, sir."
        except Exception as e:
            return False, f"Failed to adjust brightness: {e}"

    def find_visual_feature_on_screen(self, description: str) -> Optional[Tuple[int, int]]:
        """
        Dynamically analyzes the whole screen or specific quadrants using OpenCV computer vision.
        Detects visual elements by color (orange, green, blue, yellow, red), geometry (circle, square),
        and spatial regions (bottom-left, bottom, top-right, etc.).
        """
        try:
            import cv2
            import numpy as np

            # Use DPI-aware GDI screen grabber
            screen_pil = self.vision.capture_screen()
            screen_np = np.array(screen_pil)
            if screen_np.size == 0:
                return None
            screen_bgr = cv2.cvtColor(screen_np, cv2.COLOR_RGB2BGR)
            h, w, _ = screen_bgr.shape
            desc = description.lower()

            # Determine region bounding box if user specified a quadrant or location
            rx1, ry1, rx2, ry2 = 0, 0, w, h
            if any(k in desc for k in ["bottom left", "bottom-left", "lower left"]):
                rx1, ry1, rx2, ry2 = 0, int(h * 0.5), int(w * 0.45), h
            elif any(k in desc for k in ["bottom right", "bottom-right", "lower right"]):
                rx1, ry1, rx2, ry2 = int(w * 0.55), int(h * 0.5), w, h
            elif any(k in desc for k in ["top left", "top-left", "upper left"]):
                rx1, ry1, rx2, ry2 = 0, 0, int(w * 0.45), int(h * 0.5)
            elif any(k in desc for k in ["top right", "top-right", "upper right"]):
                rx1, ry1, rx2, ry2 = int(w * 0.55), 0, w, int(h * 0.5)
            elif any(k in desc for k in ["bottom", "taskbar"]):
                rx1, ry1, rx2, ry2 = 0, int(h * 0.65), w, h
            elif "top" in desc:
                rx1, ry1, rx2, ry2 = 0, 0, w, int(h * 0.35)
            elif "left" in desc:
                rx1, ry1, rx2, ry2 = 0, 0, int(w * 0.4), h
            elif "right" in desc:
                rx1, ry1, rx2, ry2 = int(w * 0.6), 0, w, h
            elif any(k in desc for k in ["center", "middle"]):
                rx1, ry1, rx2, ry2 = int(w * 0.2), int(h * 0.2), int(w * 0.8), int(h * 0.8)

            hsv = cv2.cvtColor(screen_bgr, cv2.COLOR_BGR2HSV)
            roi_hsv = hsv[ry1:ry2, rx1:rx2]

            mask = None
            if any(k in desc for k in ["orange", "amber"]):
                mask = cv2.inRange(roi_hsv, np.array([8, 120, 120]), np.array([28, 255, 255]))
            elif "green" in desc:
                mask = cv2.inRange(roi_hsv, np.array([40, 90, 90]), np.array([85, 255, 255]))
            elif any(k in desc for k in ["blue", "cyan"]):
                mask = cv2.inRange(roi_hsv, np.array([85, 100, 100]), np.array([135, 255, 255]))
            elif "yellow" in desc:
                mask = cv2.inRange(roi_hsv, np.array([24, 100, 100]), np.array([36, 255, 255]))
            elif "red" in desc:
                m1 = cv2.inRange(roi_hsv, np.array([0, 120, 120]), np.array([8, 255, 255]))
                m2 = cv2.inRange(roi_hsv, np.array([170, 120, 120]), np.array([180, 255, 255]))
                mask = cv2.bitwise_or(m1, m2)

            if mask is None:
                return None

            # Morphological close to seal interior cutouts/icons (e.g. black play triangle inside green button)
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15))
            closed = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
            contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            want_circle = any(w in desc for w in ["circle", "round", "dot", "sun", "ball", "sphere", "disc", "button", "icon"])

            best_cand = None
            best_score = -1

            for cnt in contours:
                hull = cv2.convexHull(cnt)
                area = cv2.contourArea(hull)
                if area < 50:
                    continue
                peri = cv2.arcLength(hull, True)
                circularity = (4 * np.pi * area / (peri * peri)) if peri > 0 else 0
                (cx_rel, cy_rel), radius = cv2.minEnclosingCircle(hull)
                circle_area = np.pi * (radius ** 2)
                fill = area / circle_area if circle_area > 0 else 0

                cx = rx1 + int(cx_rel)
                cy = ry1 + int(cy_rel)

                score = area
                if want_circle:
                    if circularity < 0.55 or fill < 0.55:
                        continue
                    score = area * circularity * fill

                if score > best_score:
                    best_score = score
                    best_cand = (cx, cy)

            return best_cand
        except Exception:
            return None

    def find_visual_target(self, target_name: str) -> Optional[Tuple[int, int]]:
        if not target_name:
            return None
        t = target_name.lower().strip()

        # 1. Direct screen directional coordinates
        if t in ["bottom", "the bottom", "bottom of screen", "screen bottom", "down"]:
            return self.screen_width // 2, self.screen_height - 60
        if t in ["top", "the top", "top of screen", "screen top", "up"]:
            return self.screen_width // 2, 60
        if t in ["left", "the left", "left side"]:
            return 60, self.screen_height // 2
        if t in ["right", "the right", "right side"]:
            return self.screen_width - 60, self.screen_height // 2
        if t in ["bottom left", "bottom-left", "lower left"]:
            return 60, self.screen_height - 60
        if t in ["bottom right", "bottom-right", "lower right"]:
            return self.screen_width - 60, self.screen_height - 60
        if t in ["top left", "top-left", "upper left"]:
            return 60, 60
        if t in ["top right", "top-right", "upper right"]:
            return self.screen_width - 60, 60
        if t in ["center", "middle", "screen center", "screen middle"]:
            return self.screen_width // 2, self.screen_height // 2

        # 2. Dynamic Computer Vision Feature Detection (Color + Geometry + Location)
        if any(c in t for c in ["orange", "amber", "green", "blue", "cyan", "yellow", "red", "circle", "round"]):
            pt = self.find_visual_feature_on_screen(t)
            if pt:
                return pt

        # Clean conversational filler phrases
        clean_t = re.sub(r'\b(which\s+is|which\s+was|that\s+is|that\s+was|located|placed)\b', '', t)
        clean_t = re.sub(r'\b(on\s+the\s+task\s*bar|in\s+the\s+task\s*bar|on\s+task\s*bar|in\s+task\s*bar|on\s+the\s+screen|on\s+screen)\b', '', clean_t)
        clean_t = re.sub(r'[^\w\s-]', ' ', clean_t).strip()

        # 3. Dynamic Template Matching via OpenCV
        template_files = []
        taskbar_search = False
        if any(w in clean_t for w in ["sun", "light mode", "theme", "mode", "toggle", "switch", "dark mode"]):
            template_files.extend(["assets/sun_icon_light.png", "assets/sun_icon_dark.png"])
        elif any(w in clean_t for w in ["window", "windows", "start", "os"]):
            template_files.extend(["assets/real_start_icon.png", "assets/icon_start.png"])
            taskbar_search = True
        elif any(w in clean_t for w in ["whatsapp", "whatapp", "wa"]):
            template_files.extend(["assets/real_whatsapp_icon.png"])
            taskbar_search = True
        elif any(w in clean_t for w in ["folder", "file explorer", "explorer", "files", "my pc"]):
            template_files.extend(["assets/real_explorer_icon.png"])
            taskbar_search = True
        elif any(w in clean_t for w in ["spotify", "music"]):
            template_files.extend(["assets/real_spotify_icon.png"])
            taskbar_search = True
        elif any(w in clean_t for w in ["canva"]):
            template_files.extend(["assets/real_canva_icon.png"])
            taskbar_search = True
        elif any(w in clean_t for w in ["antigravity", "ide", "code icon", "editor"]):
            template_files.extend(["assets/real_antigravity_icon.png"])
            taskbar_search = True
        elif any(w in clean_t for w in ["word", "doc", "microsoft word"]):
            template_files.extend(["assets/real_word_icon.png"])
            taskbar_search = True
        elif any(w in clean_t for w in ["brave", "lion"]):
            template_files.extend(["assets/real_brave_icon.png"])
            taskbar_search = True
        elif any(w in clean_t for w in ["terminal", "cmd", "command prompt", "console"]):
            template_files.extend(["assets/real_terminal_icon.png"])
            taskbar_search = True
        elif any(w in clean_t for w in ["python", "py"]):
            template_files.extend(["assets/real_python_icon.png"])
            taskbar_search = True
        elif any(w in clean_t for w in ["weather", "orange circle", "temperature", "sunny"]):
            template_files.extend(["assets/real_orange_circle.png"])
            taskbar_search = True
        elif any(w in clean_t for w in ["wifi", "wi-fi", "internet", "network", "wireless"]):
            template_files.extend(["assets/real_wifi_icon.png"])
            taskbar_search = True
        elif any(w in clean_t for w in ["sound", "volume", "speaker", "audio"]):
            template_files.extend(["assets/real_volume_icon.png"])
            taskbar_search = True
        elif any(w in clean_t for w in ["battery", "power", "charge"]):
            template_files.extend(["assets/real_battery_icon.png"])
            taskbar_search = True

        if template_files:
            search_region = None
            if taskbar_search:
                search_region = (0, max(0, self.screen_height - 65), self.screen_width, 65)
            pt = self.locate_template_on_screen(template_files, confidence_threshold=0.76, search_region=search_region)
            if pt:
                return pt

        # 4. Geometric Resolution & Desktop/Taskbar Grounding Engine
        taskbar_y = self.screen_height - 24
        cx = self.screen_width // 2

        # A. HYDRA HUD Elements (Sun/Moon/Theme toggle, message bubble)
        if any(w in clean_t for w in ["sun", "moon", "theme", "mode", "toggle", "switch", "dark mode", "light mode"]):
            wx, wy, ww, wh = self.get_hud_window_bounds()
            scale = min(ww / 1024.0, wh / 575.0)
            cx_win = wx + (ww // 2)
            cy_win = wy + (wh // 2)
            x = int(cx_win + (-1 * scale))
            y = int(cy_win + (-243.5 * scale))
            return x, y

        if any(w in clean_t for w in ["message", "chat", "command", "bubble", "input", "talk", "mic", "action"]):
            wx, wy, ww, wh = self.get_hud_window_bounds()
            scale = min(ww / 1024.0, wh / 575.0)
            cx_win = wx + (ww // 2)
            cy_win = wy + (wh // 2)
            x = int(cx_win + (449 * scale))
            y = int(cy_win + (203.5 * scale))
            return x, y

        # In-App Application Search Bar / Search Icon (Spotify, Browsers, Explorer, Active App)
        if any(w in clean_t for w in ["search bar", "search icon", "search box", "search input", "search field", "search button", "search tab", "search"]) and not any(k in clean_t for k in ["taskbar", "task bar", "windows search", "start search"]):
            try:
                from window_ops import WINDOW_MGR
                from ctypes import wintypes
                import ctypes
                user32 = ctypes.windll.user32
                open_wins = WINDOW_MGR.get_open_windows()
                fg_hwnd = user32.GetForegroundWindow()
                active_win = next((w for w in open_wins if w["hwnd"] == fg_hwnd), None)
                if not active_win and open_wins:
                    active_win = open_wins[0]

                if active_win:
                    rect = wintypes.RECT()
                    user32.GetWindowRect(active_win["hwnd"], ctypes.byref(rect))
                    win_w = rect.right - rect.left
                    win_h = rect.bottom - rect.top
                    app_name = (active_win.get("app_name") or "").lower()
                    title = (active_win.get("title") or "").lower()

                    if "spotify" in app_name or "spotify" in title:
                        cx = rect.left + min(380, max(260, int(win_w * 0.28)))
                        cy = rect.top + 45
                        return int(cx), int(cy)
                    elif any(b in app_name or b in title for b in ["brave", "chrome", "edge", "firefox"]):
                        cx = rect.left + win_w // 2
                        cy = rect.top + 80
                        return int(cx), int(cy)
                    elif "explorer" in app_name or "folder" in app_name or "explorer" in title:
                        cx = rect.right - 140
                        cy = rect.top + 55
                        return int(cx), int(cy)
                    elif win_w > 300 and win_h > 200:
                        cx = rect.left + min(350, win_w // 2)
                        cy = rect.top + 45
                        return int(cx), int(cy)
            except Exception:
                pass

        # B. User's Real Windows Taskbar Layout (Left-to-Right Verified Anchors)
        # 1. Weather / Orange Circle (Bottom-Left)
        if any(w in clean_t for w in ["weather", "orange circle", "temperature", "sunny"]):
            return 46, 1402

        # 2. Windows 11 Start Icon
        if any(w in clean_t for w in ["window", "windows", "start", "start button", "win icon", "start icon", "os icon", "win button", "windows icon"]):
            return 748, taskbar_y

        # 3. Windows Search Bar (Explicit Taskbar Request Only)
        if any(w in clean_t for w in ["taskbar search", "windows search", "start search", "taskbar search bar", "task bar search"]):
            return 943, taskbar_y

        # 4. Task View
        if any(w in clean_t for w in ["task view", "virtual desktop", "desktops"]):
            return 1146, taskbar_y

        # 5. WhatsApp
        if any(w in clean_t for w in ["whatsapp", "whatapp", "whatsapp icon", "whatapp icon", "chat app", "wa"]):
            return 1217, taskbar_y

        # 6. File Explorer / Files / Folder
        if any(w in clean_t for w in ["folder", "file explorer", "explorer", "files icon", "files", "my pc", "this pc"]):
            return 1282, taskbar_y

        # 7. Spotify
        if any(w in clean_t for w in ["spotify", "music", "spotify icon", "music icon"]):
            return 1349, taskbar_y

        # 8. Canva
        if any(w in clean_t for w in ["canva", "canva icon", "canva app"]):
            return 1415, taskbar_y

        # 9. Antigravity IDE
        if any(w in clean_t for w in ["antigravity", "ide", "code icon", "editor"]):
            return 1481, taskbar_y

        # 10. Microsoft Word
        if any(w in clean_t for w in ["word", "doc", "word icon", "microsoft word"]):
            return 1545, taskbar_y

        # 11. Brave Browser / Lion
        if any(w in clean_t for w in ["brave", "lion", "lion icon", "lion icone", "brave icon", "brave browser"]):
            return 1610, taskbar_y

        # 12. Terminal / Command Prompt
        if any(w in clean_t for w in ["terminal", "cmd", "command prompt", "console"]):
            return 1740, taskbar_y

        # 13. Python
        if any(w in clean_t for w in ["python", "python icon", "py"]):
            return 1805, taskbar_y

        # C. System Tray Elements
        # Wi-Fi: X = 2311 on 2560x1440
        if any(w in clean_t for w in ["wifi", "wi-fi", "internet", "network", "wireless", "wifi icon", "wi-fi icon"]):
            return self.screen_width - 249, taskbar_y

        # Sound / Volume: X = 2352 on 2560x1440
        if any(w in clean_t for w in ["sound", "volume", "speaker", "audio", "sound icon", "volume icon"]):
            return self.screen_width - 208, taskbar_y

        # Battery: X = 2398 on 2560x1440
        if any(w in clean_t for w in ["battery", "power", "charge", "battery icon", "charge icon"]):
            return self.screen_width - 162, taskbar_y

        # Clock / Time: X = 2483 on 2560x1440
        if any(w in clean_t for w in ["clock", "time", "calendar", "date", "clock icon"]):
            return self.screen_width - 77, taskbar_y

        # D. Active Window Controls (Close, Minimize, Maximize)
        if any(w in clean_t for w in ["close", "exit", "x button", "x icon"]):
            return self.screen_width - 25, 18
        if any(w in clean_t for w in ["minimize", "dash button", "minus button"]):
            return self.screen_width - 75, 18
        if any(w in clean_t for w in ["maximize", "restore"]):
            return self.screen_width - 50, 18

        return None

    def type_text(self, text: str):
        if not text:
            return False, "No text provided to type, sir."
        if (text.startswith('"') and text.endswith('"')) or (text.startswith("'") and text.endswith("'")):
            text = text[1:-1]
        try:
            # For short text (contact names, search queries, commands), type directly with key events
            # This triggers live autocomplete and search filters in WhatsApp, Spotify, Chrome, etc.
            if HAS_PYAUTOGUI and len(text) <= 80 and not any(ord(c) > 127 for c in text):
                pyautogui.write(text, interval=0.02)
                return True, f"Typed requested text, sir."

            if HAS_PYAUTOGUI and pyperclip:
                old_clip = ""
                try: old_clip = pyperclip.paste()
                except: pass
                
                pyperclip.copy(text)
                time.sleep(0.04)
                pyautogui.hotkey('ctrl', 'v')
                time.sleep(0.35)
                
                try: pyperclip.copy(old_clip)
                except: pass
                return True, f"Typed requested text, sir."
            else:
                # Native Windows clipboard injection
                buffer = text.encode('utf-16le') + b'\x00\x00'
                h_mem = ctypes.windll.kernel32.GlobalAlloc(0x0002, len(buffer))
                ptr = ctypes.windll.kernel32.GlobalLock(h_mem)
                ctypes.memmove(ptr, buffer, len(buffer))
                ctypes.windll.kernel32.GlobalUnlock(h_mem)
                if ctypes.windll.user32.OpenClipboard(None):
                    ctypes.windll.user32.EmptyClipboard()
                    ctypes.windll.user32.SetClipboardData(13, h_mem)
                    ctypes.windll.user32.CloseClipboard()
                    self.hotkey("ctrl", "v")
                    return True, f"Typed requested text, sir."
                return False, "Failed to access clipboard."
        except Exception:
            if HAS_PYAUTOGUI:
                pyautogui.write(text, interval=0.02)
                return True, f"Typed requested text, sir."
            return False, "Typing execution failed."

    def press_media_key(self, action: str):
        """Global Windows hardware multimedia keys (works in background)."""
        vk_map = {
            "play_pause": 0xB3, "play": 0xB3, "pause": 0xB3,
            "stop": 0xB2,
            "next": 0xB0, "next_track": 0xB0,
            "prev": 0xB1, "previous": 0xB1, "prev_track": 0xB1,
            "volume_up": 0xAF, "volume_down": 0xAE, "mute": 0xAD
        }
        vk = vk_map.get(action.lower().strip())
        if not vk:
            return False, f"Unknown media action: {action}"
        try:
            ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
            time.sleep(0.03)
            ctypes.windll.user32.keybd_event(vk, 0, 2, 0)
            return True, f"Triggered {action}, sir."
        except Exception as e:
            return False, f"Media key error: {e}"

    def press_key(self, key_name: str, count: int = 1):
        low = key_name.lower().strip()
        key_map = {
            "enter": "enter", "return": "enter",
            "tab": "tab",
            "space": "space", "spacebar": "space",
            "backspace": "backspace", "back": "backspace",
            "escape": "esc", "esc": "esc",
            "delete": "delete", "del": "delete",
            "up": "up", "arrow up": "up",
            "down": "down", "arrow down": "down",
            "left": "left", "arrow left": "left",
            "right": "right", "arrow right": "right",
            "home": "home", "end": "end",
            "pageup": "pageup", "pgup": "pageup",
            "pagedown": "pagedown", "pgdn": "pagedown",
            "f1": "f1", "f2": "f2", "f3": "f3", "f4": "f4", "f5": "f5",
            "f6": "f6", "f7": "f7", "f8": "f8", "f9": "f9", "f10": "f10",
            "f11": "f11", "f12": "f12",
            "win": "win", "windows": "win",
            "capslock": "capslock",
            "volumeup": "volumeup", "vol up": "volumeup", "volume up": "volumeup",
            "volumedown": "volumedown", "vol down": "volumedown", "volume down": "volumedown",
            "volumemute": "volumemute", "mute": "volumemute",
            "playpause": "playpause", "play": "playpause", "pause": "playpause",
            "stop": "stop", "nexttrack": "nexttrack", "prevtrack": "prevtrack"
        }
        actual_key = key_map.get(low, low)
        try:
            if actual_key in ["playpause", "stop", "nexttrack", "prevtrack"]:
                media_act = "play_pause" if actual_key == "playpause" else ("next" if actual_key == "nexttrack" else ("prev" if actual_key == "prevtrack" else "stop"))
                return self.press_media_key(media_act)

            if HAS_PYAUTOGUI:
                for _ in range(count):
                    pyautogui.press(actual_key)
            else:
                vk_map = {
                    "enter": 0x0D, "tab": 0x09, "space": 0x20, "backspace": 0x08,
                    "esc": 0x1B, "delete": 0x2E, "up": 0x26, "down": 0x28, "left": 0x25,
                    "right": 0x27, "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
                    "win": 0x5B, "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74,
                    "f6": 0x75, "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
                    "volumeup": 0xAF, "volumedown": 0xAE, "volumemute": 0xAD,
                    "playpause": 0xB3, "stop": 0xB2, "nexttrack": 0xB0, "prevtrack": 0xB1
                }
                vk = vk_map.get(actual_key)
                if vk:
                    for _ in range(count):
                        ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
                        time.sleep(0.02)
                        ctypes.windll.user32.keybd_event(vk, 0, 2, 0)
            return True, f"Pressed {key_name.capitalize()}, sir."
        except Exception as e:
            return False, f"Failed to press key {key_name}: {e}"

    def hotkey(self, *keys):
        try:
            if HAS_PYAUTOGUI:
                pyautogui.hotkey(*keys)
            else:
                vk_map = {
                    "ctrl": 0x11, "shift": 0x10, "alt": 0x12, "win": 0x5B,
                    "enter": 0x0D, "tab": 0x09, "esc": 0x1B, "f4": 0x73, "f5": 0x74,
                    "s": ord('S'), "c": ord('C'), "v": ord('V'), "a": ord('A'),
                    "z": ord('Z'), "y": ord('Y'), "d": ord('D'), "r": ord('R'),
                    "t": ord('T'), "w": ord('W')
                }
                vks = []
                for k in keys:
                    low = k.lower().strip()
                    if low in vk_map: vks.append(vk_map[low])
                    elif len(low) == 1 and low.isalnum(): vks.append(ord(low.upper()))
                for vk in vks:
                    ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
                    time.sleep(0.01)
                time.sleep(0.03)
                for vk in reversed(vks):
                    ctypes.windll.user32.keybd_event(vk, 0, 2, 0)
                    time.sleep(0.01)
            combo = " + ".join(k.capitalize() for k in keys)
            return True, f"Executed {combo}, sir."
        except Exception as e:
            return False, f"Failed to execute shortcut: {e}"

    def parse_and_execute(self, command: str) -> Tuple[bool, Optional[str]]:
        if not command:
            return False, None
        raw_cmd = command.strip()
        cmd = raw_cmd.lower()
        cmd = re.sub(r'[!?.]', '', cmd).strip()
        
        for w in ["hydra ", "hey hydra ", "ok hydra ", "okay hydra "]:
            if cmd.startswith(w):
                cmd = cmd[len(w):].strip()

        # 0. AI NEURAL INTENT HOOK (Active when Nebius / NVIDIA API key is configured)
        try:
            from ai_engine import ai_interpret_intent
            ai_data = ai_interpret_intent(command)
            if ai_data and isinstance(ai_data, dict):
                act = ai_data.get("action")
                tgt = ai_data.get("target") or ai_data.get("direction")
                btn = (ai_data.get("button") or "left").lower()
                clicks = int(ai_data.get("clicks") or 1)
                spoken = ai_data.get("spoken_ack")

                if act in ["MOVE", "MOVE_AND_CLICK"] and tgt:
                    pt = self.find_visual_target(tgt)
                    if pt:
                        self.move_to(pt[0], pt[1], duration=0.25)
                        if act == "MOVE_AND_CLICK":
                            time.sleep(0.08)
                            self.click(button=btn, clicks=clicks)
                        return True, spoken or f"Moved to {tgt}, sir."
                elif act == "CLICK":
                    if tgt:
                        pt = self.find_visual_target(tgt)
                        if pt:
                            self.move_to(pt[0], pt[1], duration=0.25)
                            time.sleep(0.08)
                    self.click(button=btn, clicks=clicks)
                    return True, spoken or "Clicked, sir."
                elif act == "SCROLL":
                    amt = -6 if ai_data.get("direction") == "down" else 6
                    return self.scroll(amt)
        except Exception:
            pass

        # 1. SCROLLING
        if any(w in cmd for w in ["scroll to top", "scroll top", "go to top"]):
            return self.hotkey("ctrl", "home")
        if any(w in cmd for w in ["scroll to bottom", "scroll bottom", "go to bottom"]):
            return self.hotkey("ctrl", "end")
        
        if "scroll" in cmd:
            scroll_down_match = re.search(r'scroll down(?:\s*(?:by\s*)?(\d+))?', cmd)
            if scroll_down_match:
                amount = int(scroll_down_match.group(1)) if scroll_down_match.group(1) else 6
                return self.scroll(-amount)

            scroll_up_match = re.search(r'scroll up(?:\s*(?:by\s*)?(\d+))?', cmd)
            if scroll_up_match:
                amount = int(scroll_up_match.group(1)) if scroll_up_match.group(1) else 6
                return self.scroll(amount)

            if "down" in cmd:
                return self.scroll(-6)
            if "up" in cmd:
                return self.scroll(6)

        # 2. MOUSE CLICKS
        click_coord_match = re.search(r'(double|right|triple|left)?\s*click\s*(?:at|on)?\s*(\d+)[,\s]+(\d+)', cmd)
        if click_coord_match:
            b_type = (click_coord_match.group(1) or "left").lower()
            x, y = int(click_coord_match.group(2)), int(click_coord_match.group(3))
            clicks = 2 if b_type == "double" else (3 if b_type == "triple" else 1)
            button = "right" if b_type == "right" else "left"
            return self.click_at(x, y, button=button, clicks=clicks)

        if cmd in ["double click", "double-click", "double click mouse"]:
            return self.click("left", clicks=2)
        if cmd in ["triple click", "triple-click"]:
            return self.click("left", clicks=3)
        if cmd in ["right click", "right-click", "context menu"]:
            return self.click("right", clicks=1)
        if cmd in ["middle click", "middle-click"]:
            return self.click("middle", clicks=1)
        if cmd in ["click", "left click", "click mouse", "click here", "press click", "single click", "click the mouse", "just click"]:
            return self.click("left", clicks=1)

        # 3. MOUSE MOVEMENT
        # Center detection (handles all natural variations)
        center_matches = [
            "mouse to center", "cursor to center", "mouse to the center", "cursor to the center",
            "mouse to middle", "cursor to middle", "mouse to the middle", "cursor to the middle",
            "center mouse", "center the mouse", "center cursor", "center the cursor",
            "mouse center", "cursor center", "put mouse in center", "put mouse in middle",
            "move mouse center", "move cursor center", "to center", "to the center"
        ]
        if any(w in cmd for w in center_matches) or (("mouse" in cmd or "cursor" in cmd or "pointer" in cmd) and ("center" in cmd or "middle" in cmd)):
            self.move_to(self.screen_width // 2, self.screen_height // 2)
            return True, "Moved mouse to the center of your screen, sir."

        # Coordinate movement
        move_coord_match = re.search(r'(?:move\s+)?(?:mouse|cursor|pointer)?\s*(?:to|at)?\s*(\d+)[,\s]+(\d+)', cmd)
        if move_coord_match and any(w in cmd for w in ["move", "mouse", "cursor", "pointer", "to", "at"]):
            x, y = int(move_coord_match.group(1)), int(move_coord_match.group(2))
            if x <= self.screen_width and y <= self.screen_height:
                return self.move_to(x, y)

        # Cardinal screen directions (bottom, top, left, right, corners)
        # ONLY trigger if the user did NOT specify a visual object or color
        has_visual_object = any(w in cmd for w in [
            "circle", "icon", "button", "dot", "folder", "app", "window", "weather", "widget",
            "orange", "green", "blue", "yellow", "red", "cyan", "purple", "white", "black",
            "whatsapp", "whatapp", "spotify", "brave", "lion", "canva", "word", "explorer", "files"
        ])
        if not has_visual_object and any(w in cmd for w in ["mouse", "cursor", "pointer", "move", "go"]):
            if any(w in cmd for w in ["top left", "top-left", "upper left"]):
                self.move_to(60, 60)
                return True, "Moved mouse to top left, sir."
            if any(w in cmd for w in ["top right", "top-right", "upper right"]):
                self.move_to(self.screen_width - 60, 60)
                return True, "Moved mouse to top right, sir."
            if any(w in cmd for w in ["bottom left", "bottom-left", "lower left"]):
                self.move_to(60, self.screen_height - 60)
                return True, "Moved mouse to bottom left, sir."
            if any(w in cmd for w in ["bottom right", "bottom-right", "lower right"]):
                self.move_to(self.screen_width - 60, self.screen_height - 60)
                return True, "Moved mouse to bottom right, sir."
            if re.search(r'\b(?:to\s+(?:the\s+)?)?bottom\b', cmd) and not any(w in cmd for w in ["left", "right"]):
                self.move_to(self.screen_width // 2, self.screen_height - 60)
                return True, "Moved mouse to the bottom of the screen, sir."
            if re.search(r'\b(?:to\s+(?:the\s+)?)?top\b', cmd) and not any(w in cmd for w in ["left", "right"]):
                self.move_to(self.screen_width // 2, 60)
                return True, "Moved mouse to the top of the screen, sir."
            if re.search(r'\b(?:to\s+(?:the\s+)?)?left\b', cmd) and not any(w in cmd for w in ["top", "bottom", "click"]):
                self.move_to(60, self.screen_height // 2)
                return True, "Moved mouse to the left side of the screen, sir."
            if re.search(r'\b(?:to\s+(?:the\s+)?)?right\b', cmd) and not any(w in cmd for w in ["top", "bottom", "click"]):
                self.move_to(self.screen_width - 60, self.screen_height // 2)
                return True, "Moved mouse to the right side of the screen, sir."

        # 3.5 VISUAL TARGET DETECTION & GROUNDED MOUSE INTERACTION (SCREEN VISION)
        # Compound command: "move the mouse to the sun icon and left click", "move to the sun icon and click"
        move_click_match = re.search(r'(?:move|glide|point)\s+(?:the\s+)?(?:mouse|cursor)?\s*(?:to|where)?\s*(?:the\s+)?(.+?)\s*(?:is\s*)?and\s*(left\s*click|right\s*click|double\s*click|click)(?:\s+it|\s+on\s+it)?$', cmd)
        if move_click_match:
            target_raw = move_click_match.group(1).strip()
            click_raw = move_click_match.group(2).strip()
            pt = self.find_visual_target(target_raw)
            if pt:
                x, y = pt
                self.move_to(x, y, duration=0.25)
                time.sleep(0.08)
                b_type = "right" if "right" in click_raw else "left"
                clicks = 2 if "double" in click_raw else 1
                self.click(button=b_type, clicks=clicks)
                return True, f"Moved to the {target_raw} and clicked, sir."
            else:
                return True, f"I could not visually identify the {target_raw} on your screen, sir."

        # Direct click on named visual element: "click the sun icon", "click on the sun icon", "left click the sun icon"
        direct_click_match = re.search(r'^(left\s*click|right\s*click|double\s*click|click)\s+(?:on\s+)?(?:the\s+)?(.+?)(?:\s+icon)?$', cmd)
        if direct_click_match:
            click_raw = direct_click_match.group(1).strip()
            target_raw = direct_click_match.group(2).strip()
            if not any(target_raw.startswith(w) for w in ["at", "on 1", "on 2", "on 3", "on 4", "on 5", "on 6", "on 7", "on 8", "on 9", "here"]):
                pt = self.find_visual_target(target_raw)
                if pt:
                    x, y = pt
                    self.move_to(x, y, duration=0.25)
                    time.sleep(0.08)
                    b_type = "right" if "right" in click_raw else "left"
                    clicks = 2 if "double" in click_raw else 1
                    self.click(button=b_type, clicks=clicks)
                    return True, f"Moved to the {target_raw} and clicked, sir."

        # Pure visual move: "move the mouse where the sun icon is", "move mouse to the sun icon", "move to sun icon"
        visual_move_match = re.search(r'(?:move|glide|point)\s+(?:the\s+)?(?:mouse|cursor)?\s*(?:to|where)?\s*(?:the\s+)?(.+?)(?:\s+is)?$', cmd)
        if visual_move_match:
            target_raw = visual_move_match.group(1).strip()
            if not any(target_raw.startswith(w) for w in ["1", "2", "3", "4", "5", "6", "7", "8", "9"]):
                pt = self.find_visual_target(target_raw)
                if pt:
                    x, y = pt
                    self.move_to(x, y, duration=0.25)
                    return True, f"Moved mouse to where the {target_raw} is, sir."
                else:
                    return True, f"I could not visually identify the {target_raw} on your screen, sir."

        # Relative directional movement
        rel_match = re.search(r'\b(up|down|left|right)\b(?!\s*click)(?:\s*(?:by\s*)?(\d+))?', cmd)
        if rel_match and any(w in cmd for w in ["move", "mouse", "cursor", "pointer", "nudge", "shift", "glide"]):
            direction = rel_match.group(1)
            dist = int(rel_match.group(2)) if rel_match.group(2) else 150
            if direction == "up": self.move_relative(0, -dist)
            elif direction == "down": self.move_relative(0, dist)
            elif direction == "left": self.move_relative(-dist, 0)
            elif direction == "right": self.move_relative(dist, 0)
            return True, f"Moved mouse {direction} by {dist} pixels, sir."

        if any(w in cmd for w in ["where is the mouse", "where is mouse", "where's the mouse", "mouse position", "cursor position", "current coordinates", "where is cursor"]):
            x, y = self.get_position()
            return True, f"Mouse cursor is currently at coordinates {x} by {y} on your {self.screen_width} by {self.screen_height} screen, sir."

        # Drag and drop
        drag_coord_match = re.search(r'drag (?:mouse |cursor )?(?:to )?\s*(\d+)[,\s]+(\d+)', cmd)
        if drag_coord_match:
            x, y = int(drag_coord_match.group(1)), int(drag_coord_match.group(2))
            return self.drag_to(x, y)

        drag_rel_match = re.search(r'drag (?:mouse |cursor )?(up|down|left|right)(?:\s*(?:by\s*)?(\d+))?', cmd)
        if drag_rel_match:
            direction = drag_rel_match.group(1)
            dist = int(drag_rel_match.group(2)) if drag_rel_match.group(2) else 200
            dx = -dist if direction == "left" else (dist if direction == "right" else 0)
            dy = -dist if direction == "up" else (dist if direction == "down" else 0)
            return self.drag_relative(dx, dy)

        if any(w in cmd for w in ["hold mouse", "hold left click", "mouse down"]):
            return self.mouse_down("left")
        if any(w in cmd for w in ["release mouse", "release left click", "mouse up"]):
            return self.mouse_up("left")

        # 4. TYPING TEXT (Safeguarded against Notepad/file creation)
        if not any(w in cmd for w in ["note", "notepad", "file", "document", "create", "make"]):
            type_match = re.search(r'^(?:type|write|enter text)\s+(.*)', raw_cmd, re.IGNORECASE)
            if type_match:
                text_to_type = type_match.group(1).strip()
                if text_to_type.lower().startswith("this:"):
                    text_to_type = text_to_type[5:].strip()
                elif text_to_type.lower().startswith("this "):
                    text_to_type = text_to_type[5:].strip()
                
                # Check if content requires intelligent AI expansion
                from ai_engine import is_semantic_content_request, expand_intelligent_content
                if is_semantic_content_request(text_to_type):
                    text_to_type = expand_intelligent_content(text_to_type)

                return self.type_text(text_to_type)

        # 5. KEYBOARD PRESSES
        key_press_triggers = {
            "enter": ["press enter", "hit enter", "press return", "hit return", "press this", "hit this", "press that", "press it", "hit it"],
            "tab": ["press tab", "hit tab"],
            "escape": ["press escape", "press esc", "hit escape"],
            "space": ["press space", "hit space", "press spacebar", "hit spacebar"],
            "backspace": ["press backspace", "hit backspace"],
            "delete": ["press delete", "hit delete"],
            "up": ["press up", "arrow up", "hit up"],
            "down": ["press down", "arrow down", "hit down"],
            "left": ["press left", "arrow left"],
            "right": ["press right", "arrow right"],
            "home": ["press home"],
            "end": ["press end"],
            "pageup": ["page up", "press page up"],
            "pagedown": ["page down", "press page down"],
            "f5": ["press f5", "refresh", "refresh page", "reload page", "reload"],
            "f11": ["full screen", "fullscreen", "press f11", "toggle full screen"],
            "volumeup": ["press vol up", "press vol up button", "press volume up", "volume up", "increase volume", "vol up"],
            "volumedown": ["press vol down", "press vol down button", "press volume down", "volume down", "decrease volume", "vol down"],
            "volumemute": ["press mute", "mute volume", "mute", "unmute"]
        }
        for key_name, triggers in key_press_triggers.items():
            if cmd in triggers or any(cmd == t for t in triggers):
                return self.press_key(key_name)

        # 6. HOTKEYS & SHORTCUTS
        hotkey_triggers = [
            (["select all", "press ctrl a", "select everything"], ["ctrl", "a"], "Selected all content, sir."),
            (["copy", "copy this", "press ctrl c", "copy text", "copy that"], ["ctrl", "c"], "Copied to clipboard, sir."),
            (["paste", "paste this", "press ctrl v", "paste here", "paste text"], ["ctrl", "v"], "Pasted from clipboard, sir."),
            (["undo", "undo that", "undo this", "press ctrl z"], ["ctrl", "z"], "Action undone, sir."),
            (["redo", "redo that", "redo this", "press ctrl y"], ["ctrl", "y"], "Action redone, sir."),
            (["save file", "save document", "save this", "press ctrl s"], ["ctrl", "s"], "Saved, sir."),
            (["find text", "find on page", "press ctrl f", "search on page"], ["ctrl", "f"], "Opened find dialog, sir."),
            (["new tab", "open new tab", "press ctrl t"], ["ctrl", "t"], "Opened new tab, sir."),
            (["close tab", "close current tab", "press ctrl w"], ["ctrl", "w"], "Closed tab, sir."),
            (["reopen tab", "restore tab", "press ctrl shift t"], ["ctrl", "shift", "t"], "Reopened closed tab, sir."),
            (["switch window", "switch app", "alt tab", "next window", "switch task"], ["alt", "tab"], "Switched active window, sir."),
            (["close window", "close active window", "press alt f4"], ["alt", "f4"], "Closed active window, sir."),
            (["show desktop", "minimize all", "hide all windows", "press win d", "go to desktop"], ["win", "d"], "Toggled desktop view, sir."),
            (["maximize window", "maximize", "press win up"], ["win", "up"], "Maximized window, sir."),
            (["minimize window", "minimize", "press win down"], ["win", "down"], "Minimized window, sir."),
            (["snap left", "snap window left", "press win left"], ["win", "left"], "Snapped window to left half, sir."),
            (["snap right", "snap window right", "press win right"], ["win", "right"], "Snapped window to right half, sir."),
            (["take screenshot", "screenshot", "screen capture", "capture screen", "press win shift s", "snip screen"], ["win", "shift", "s"], "Snipping tool activated, sir."),
            (["open run", "run dialog", "press win r"], ["win", "r"], "Opened Run dialog, sir."),
            (["open task manager", "task manager shortcut", "press ctrl shift esc"], ["ctrl", "shift", "esc"], "Pulling up Task Manager, sir."),
            (["clipboard history", "open clipboard", "press win v"], ["win", "v"], "Opened clipboard history, sir."),
            (["zoom in", "press ctrl plus"], ["ctrl", "+"], "Zoomed in, sir."),
            (["zoom out", "press ctrl minus"], ["ctrl", "-"], "Zoomed out, sir."),
            (["reset zoom", "press ctrl zero"], ["ctrl", "0"], "Reset zoom, sir.")
        ]

        for triggers, keys, reply in hotkey_triggers:
            if any(cmd == t or cmd.startswith(t) for t in triggers):
                self.hotkey(*keys)
                return True, reply

        # 7. BRIGHTNESS CONTROLS
        if "brightness" in cmd:
            bright_match = re.search(r'brightness\s*(?:to\s*|at\s*)?(\d+)', cmd)
            if bright_match:
                lvl = int(bright_match.group(1))
                return self.set_brightness(lvl)
            if any(w in cmd for w in ["increase", "up", "raise", "higher", "more"]):
                cur = self.get_brightness()
                return self.set_brightness(min(100, cur + 15))
            if any(w in cmd for w in ["decrease", "down", "lower", "reduce", "dim", "less"]):
                cur = self.get_brightness()
                return self.set_brightness(max(10, cur - 15))
            if any(w in cmd for w in ["maximum", "max", "full", "100"]):
                return self.set_brightness(100)
            if any(w in cmd for w in ["minimum", "min", "lowest"]):
                return self.set_brightness(15)
            cur = self.get_brightness()
            return True, f"Display brightness is currently at {cur} percent, sir."

        return False, None

class WirelessDriver:
    """
    Phase 3: Wireless Connections Engine for HYDRA.
    Provides complete non-elevated control over:
    - Wi-Fi Radio (Power On, Power Off, Disconnect, Connect to SSID, Scan Networks, Diagnostics)
    - Bluetooth Radio (Power On, Power Off, Radio State, Paired Device Inspection, Pairing Settings)
    - Mobile Hotspot & Airplane Mode
    - Network Diagnostics (Local IP, Latency / Ping Test)
    Equipped with dual-path intelligence:
    1. Local Semantic Intent Classifier (handles natural variations, slang, and indirect phrasing offline)
    2. Cloud LLM Hook via ai_interpret_intent (NVIDIA Nemotron via Nebius Token Factory)
    """

    def __init__(self):
        self._radio_cache = {}

    # --- WINRT HARDWARE RADIO CONTROLLER ---
    def _execute_ps(self, ps_code: str, timeout: int = 8) -> str:
        try:
            res = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_code],
                capture_output=True,
                text=True,
                timeout=timeout
            )
            return res.stdout.strip()
        except Exception as e:
            return f"ERROR: {e}"

    def get_radio_states(self) -> Dict[str, str]:
        ps = """
        Add-Type -AssemblyName System.Runtime.WindowsRuntime
        $null = [Windows.Devices.Radios.Radio, Windows.System.Devices, ContentType = WindowsRuntime]
        $asTaskGeneric = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { 
            $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' 
        } | Select-Object -First 1

        $op = [Windows.Devices.Radios.Radio]::GetRadiosAsync()
        $task = $asTaskGeneric.MakeGenericMethod([System.Collections.Generic.IReadOnlyList[Windows.Devices.Radios.Radio]]).Invoke($null, @($op))
        $task.Wait()
        $radios = $task.Result
        $list = @()
        foreach ($r in $radios) {
            $list += [PSCustomObject]@{
                Kind = $r.Kind.ToString()
                State = $r.State.ToString()
            }
        }
        $list | ConvertTo-Json
        """
        raw = self._execute_ps(ps, timeout=6)
        try:
            data = json.loads(raw) if raw else []
            if isinstance(data, dict):
                data = [data]
            return {d["Kind"]: d["State"] for d in data}
        except Exception:
            return {}

    def set_radio_state(self, kind: str, state: str) -> Tuple[bool, str]:
        """
        kind: 'WiFi' or 'Bluetooth'
        state: 'On' or 'Off'
        """
        ps = f"""
        Add-Type -AssemblyName System.Runtime.WindowsRuntime
        $null = [Windows.Devices.Radios.Radio, Windows.System.Devices, ContentType = WindowsRuntime]
        $asTaskGeneric = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {{ 
            $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' 
        }} | Select-Object -First 1

        $op = [Windows.Devices.Radios.Radio]::GetRadiosAsync()
        $task = $asTaskGeneric.MakeGenericMethod([System.Collections.Generic.IReadOnlyList[Windows.Devices.Radios.Radio]]).Invoke($null, @($op))
        $task.Wait()
        $target = $task.Result | Where-Object {{ $_.Kind.ToString() -eq '{kind}' }} | Select-Object -First 1
        if ($target) {{
            $setOp = $target.SetStateAsync([Windows.Devices.Radios.RadioState]::{state})
            $setTask = $asTaskGeneric.MakeGenericMethod([Windows.Devices.Radios.RadioAccessStatus]).Invoke($null, @($setOp))
            $setTask.Wait()
            Write-Output "STATUS: $($setTask.Result)"
        }} else {{
            Write-Output "ERROR: Radio {kind} not found"
        }}
        """
        out = self._execute_ps(ps, timeout=8)
        if "STATUS: Allowed" in out:
            return True, f"{kind} radio powered {state.lower()}, sir."
        return False, f"Could not power {state.lower()} {kind}: {out}"

    # --- WI-FI CONTROLS ---
    def turn_on_wifi(self) -> Tuple[bool, str]:
        succ, msg = self.set_radio_state("WiFi", "On")
        if succ:
            profiles = self.get_saved_profiles()
            if profiles:
                subprocess.Popen(f'netsh wlan connect name="{profiles[0]}"', shell=True)
            return True, "Wi-Fi radio has been powered on and is reconnecting, sir."
        subprocess.Popen('explorer.exe ms-availablenetworks:', shell=True)
        return True, "Opening available wireless networks to enable Wi-Fi, sir."

    def turn_off_wifi(self) -> Tuple[bool, str]:
        succ, msg = self.set_radio_state("WiFi", "Off")
        if succ:
            return True, "Wi-Fi radio has been powered off completely, sir."
        self.disconnect_wifi()
        return True, "Wi-Fi disconnected, sir."

    def get_wifi_status(self) -> Tuple[bool, str]:
        try:
            res = subprocess.run('netsh wlan show interfaces', shell=True, capture_output=True, text=True, timeout=4)
            out = res.stdout
            state_m = re.search(r'State\s*:\s*(.*)', out)
            ssid_m = re.search(r'SSID\s*:\s*(.*)', out)
            signal_m = re.search(r'Signal\s*:\s*(.*)', out)
            rx_m = re.search(r'Receive rate \(Mbps\)\s*:\s*(.*)', out)

            state = state_m.group(1).strip() if state_m else "disconnected"
            if state.lower() == "connected":
                ssid = ssid_m.group(1).strip() if ssid_m else "Unknown Network"
                signal = signal_m.group(1).strip() if signal_m else "N/A"
                rate = rx_m.group(1).strip() if rx_m else "N/A"
                return True, f"Wi-Fi is connected to {ssid} with {signal} signal strength at {rate} megabits per second, sir."
            else:
                radios = self.get_radio_states()
                if radios.get("WiFi") == "Off":
                    return True, "Wi-Fi radio is currently powered off, sir."
                return True, "Wi-Fi is currently disconnected, sir."
        except Exception as e:
            return False, f"Could not determine Wi-Fi status: {e}"

    def scan_wifi_networks(self) -> Tuple[bool, str]:
        try:
            res = subprocess.run('netsh wlan show networks', shell=True, capture_output=True, text=True, timeout=6)
            ssids = re.findall(r'SSID\s+\d+\s*:\s*([^\r\n]+)', res.stdout)
            cleaned = [s.strip() for s in ssids if s.strip() and not s.startswith('Network type')]
            unique_ssids = list(dict.fromkeys(cleaned))
            if unique_ssids:
                if len(unique_ssids) == 1:
                    return True, f"I found 1 visible Wi-Fi network nearby: {unique_ssids[0]}, sir."
                elif len(unique_ssids) <= 4:
                    networks_str = ", ".join(unique_ssids[:-1]) + f", and {unique_ssids[-1]}"
                    return True, f"I found {len(unique_ssids)} visible Wi-Fi networks nearby: {networks_str}, sir."
                else:
                    networks_str = ", ".join(unique_ssids[:3]) + f", plus {len(unique_ssids)-3} others"
                    return True, f"I found {len(unique_ssids)} Wi-Fi networks nearby including {networks_str}, sir."
            return True, "No wireless networks are currently within range, sir."
        except Exception as e:
            return False, f"Failed to scan Wi-Fi networks: {e}"

    def get_saved_profiles(self) -> List[str]:
        try:
            res = subprocess.run('netsh wlan show profiles', shell=True, capture_output=True, text=True, timeout=4)
            profiles = [p.strip() for p in re.findall(r'All User Profile\s*:\s*([^\r\n]+)', res.stdout)]
            return [p for p in profiles if p]
        except Exception:
            return []

    def connect_wifi(self, ssid: str) -> Tuple[bool, str]:
        if not ssid:
            return False, "Please specify the Wi-Fi network name to connect to, sir."
        target_ssid = ssid.strip().strip('"\'')
        target_ssid = re.sub(r'^(?:to|the|wifi|network)\s+', '', target_ssid, flags=re.IGNORECASE).strip()
        
        try:
            radios = self.get_radio_states()
            if radios.get("WiFi") == "Off":
                self.set_radio_state("WiFi", "On")

            profiles = self.get_saved_profiles()
            matched = next((p for p in profiles if p.lower() == target_ssid.lower()), None)
            if not matched:
                matched = next((p for p in profiles if target_ssid.lower() in p.lower() or p.lower() in target_ssid.lower()), None)

            if matched:
                c = subprocess.run(f'netsh wlan connect name="{matched}"', shell=True, capture_output=True, text=True, timeout=5)
                if c.returncode == 0:
                    return True, f"Connecting to Wi-Fi network {matched}, sir."
                else:
                    err_msg = c.stderr.strip() or c.stdout.strip()
                    return False, f"Connection request to {matched} failed: {err_msg}"
            else:
                subprocess.Popen('explorer.exe ms-availablenetworks:', shell=True)
                return True, f"No saved credentials for {target_ssid}. Opening available networks flyout for you, sir."
        except Exception as e:
            return False, f"Error connecting to Wi-Fi: {e}"

    def disconnect_wifi(self) -> Tuple[bool, str]:
        try:
            res = subprocess.run('netsh wlan disconnect', shell=True, capture_output=True, text=True, timeout=4)
            if res.returncode == 0:
                return True, "Disconnected from Wi-Fi, sir."
            return False, "Failed to disconnect from Wi-Fi."
        except Exception as e:
            return False, f"Error disconnecting from Wi-Fi: {e}"

    def open_wifi_flyout(self) -> Tuple[bool, str]:
        try:
            subprocess.Popen('explorer.exe ms-availablenetworks:', shell=True)
            return True, "Opening available wireless networks, sir."
        except Exception as e:
            return False, f"Failed to open Wi-Fi flyout: {e}"

    def open_wifi_settings(self) -> Tuple[bool, str]:
        try:
            subprocess.Popen('explorer.exe ms-settings:network-wifi', shell=True)
            return True, "Opening Wi-Fi settings, sir."
        except Exception as e:
            return False, f"Failed to open Wi-Fi settings: {e}"

    # --- BLUETOOTH CONTROLS ---
    def turn_on_bluetooth(self) -> Tuple[bool, str]:
        succ, msg = self.set_radio_state("Bluetooth", "On")
        if succ:
            return True, "Bluetooth radio has been powered on, sir."
        return self.open_bluetooth_settings()

    def turn_off_bluetooth(self) -> Tuple[bool, str]:
        succ, msg = self.set_radio_state("Bluetooth", "Off")
        if succ:
            return True, "Bluetooth radio has been powered off completely, sir."
        return False, "Could not turn off Bluetooth radio."

    def get_bluetooth_status(self) -> Tuple[bool, str]:
        radios = self.get_radio_states()
        state = radios.get("Bluetooth", "Unknown")
        if state == "Off":
            return True, "Bluetooth is currently powered off, sir."
        
        succ, dev_msg = self.list_bluetooth_devices()
        if succ and "paired" in dev_msg:
            return True, f"Bluetooth is powered on, sir. {dev_msg}"
        return True, "Bluetooth radio is powered on, sir."

    def list_bluetooth_devices(self) -> Tuple[bool, str]:
        try:
            ps = """
            Get-PnpDevice -Class Bluetooth | 
            Where-Object { 
                $_.FriendlyName -notmatch 'Transport|Enumerator|Adapter|Controller|Service|Radio|Protocol|Device \\(' -and 
                $_.FriendlyName -ne 'Bluetooth' 
            } | 
            Select-Object -Unique FriendlyName, Status | 
            ConvertTo-Json
            """
            raw = self._execute_ps(ps, timeout=6)
            if not raw or raw.startswith("ERROR:") or not (raw.strip().startswith("{") or raw.strip().startswith("[")):
                return True, "No paired Bluetooth devices detected, sir."
            data = json.loads(raw)
            if isinstance(data, dict):
                data = [data]
            devices = []
            for d in data:
                name = d.get('FriendlyName')
                if name and name not in devices and not any(k in name for k in ['Generic Attribute', 'Generic Access', 'Wireless Bluetooth']):
                    devices.append(name)

            if devices:
                if len(devices) == 1:
                    return True, f"You have 1 paired Bluetooth device: {devices[0]}, sir."
                dev_str = ", ".join(devices[:-1]) + f", and {devices[-1]}"
                return True, f"You have {len(devices)} paired Bluetooth devices: {dev_str}, sir."
            return True, "No paired Bluetooth devices detected, sir."
        except Exception:
            return True, "Bluetooth peripheral scanner is idle, sir."

    def open_bluetooth_settings(self) -> Tuple[bool, str]:
        try:
            subprocess.Popen('explorer.exe ms-settings:bluetooth', shell=True)
            return True, "Opening Bluetooth and device pairing settings, sir."
        except Exception as e:
            return False, f"Failed to open Bluetooth settings: {e}"

    def get_paired_bluetooth_devices(self) -> List[Dict[str, str]]:
        ps = """
        Get-PnpDevice -Class Bluetooth | 
        Where-Object { 
            $_.FriendlyName -notmatch 'Transport|Enumerator|Adapter|Controller|Service|Radio|Protocol|Device \\(' -and 
            $_.FriendlyName -ne 'Bluetooth' 
        } | 
        Select-Object FriendlyName, InstanceId, Status, Present | 
        ConvertTo-Json
        """
        try:
            raw = self._execute_ps(ps, timeout=6)
            if not raw or not (raw.startswith("{") or raw.startswith("[")):
                return []
            data = json.loads(raw)
            if isinstance(data, dict):
                data = [data]
            devices = []
            seen = set()
            for d in data:
                name = d.get('FriendlyName')
                inst = d.get('InstanceId')
                if name and name not in seen and not any(k in name for k in ['Generic Attribute', 'Generic Access', 'Wireless Bluetooth']):
                    seen.add(name)
                    mac_m = re.search(r'DEV_([0-9A-Fa-f]{12})', inst or "")
                    mac = mac_m.group(1) if mac_m else None
                    devices.append({
                        "name": name,
                        "instance_id": inst,
                        "mac": mac,
                        "status": d.get("Status", "Unknown")
                    })
            return devices
        except Exception:
            return []

    def get_connected_bluetooth_device(self) -> Optional[str]:
        ps = """
        Get-PnpDevice -Class AudioEndpoint | 
        Where-Object { $_.Present -eq $true -and $_.Status -eq 'OK' } | 
        Select-Object FriendlyName | 
        ConvertTo-Json
        """
        try:
            raw = self._execute_ps(ps, timeout=5)
            if raw and (raw.startswith("{") or raw.startswith("[")):
                data = json.loads(raw)
                if isinstance(data, dict):
                    data = [data]
                for d in data:
                    fn = d.get("FriendlyName", "")
                    for target in ["HAMMER", "Wave Pro", "ROVER PRO", "TECNO SPARK"]:
                        if target.lower() in fn.lower():
                            return target
        except Exception:
            pass
        return None

    def switch_bluetooth_device(self, target_name: str) -> Tuple[bool, str]:
        if not target_name:
            return False, "Please specify which Bluetooth device to connect to, sir."
        target_clean = re.sub(r'^(?:to|with|the|device)\s+', '', target_name.strip(), flags=re.IGNORECASE).strip()
        paired = self.get_paired_bluetooth_devices()
        target_dev = None
        for p in paired:
            if target_clean.lower() in p["name"].lower() or p["name"].lower() in target_clean.lower():
                target_dev = p
                break

        if not target_dev:
            known = ", ".join([p["name"] for p in paired]) if paired else "none"
            return False, f"I could not find a paired Bluetooth device named '{target_clean}'. Known paired devices: {known}, sir."

        cur_connected = self.get_connected_bluetooth_device()
        target_display = target_dev["name"]

        if cur_connected and cur_connected.lower() in target_display.lower():
            return True, f"Audio is already actively routed to {target_display}, sir."

        shift_msg = f"Shifting from {cur_connected} to {target_display}" if cur_connected else f"Connecting to {target_display}"

        mac = target_dev.get("mac")
        connected = False
        if mac:
            ps_connect = f"""
            Add-Type -AssemblyName System.Runtime.WindowsRuntime
            $null = [Windows.Devices.Bluetooth.BluetoothDevice, Windows.Devices.Bluetooth, ContentType = WindowsRuntime]
            $asTaskGeneric = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {{ 
                $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' 
            }} | Select-Object -First 1

            $addr = [Convert]::ToUInt64("{mac}", 16)
            $op = [Windows.Devices.Bluetooth.BluetoothDevice]::FromBluetoothAddressAsync($addr)
            $task = $asTaskGeneric.MakeGenericMethod([Windows.Devices.Bluetooth.BluetoothDevice]).Invoke($null, @($op))
            $task.Wait(3000)
            if ($task.IsCompleted -and $task.Result) {{
                $dev = $task.Result
                $rfOp = $dev.GetRfcommServicesAsync([Windows.Devices.Bluetooth.BluetoothCacheMode]::Uncached)
                $rfTask = $asTaskGeneric.MakeGenericMethod([Windows.Devices.Bluetooth.Rfcomm.RfcommDeviceServicesResult]).Invoke($null, @($rfOp))
                $rfTask.Wait(4000)
                Write-Output "STATUS: $($dev.ConnectionStatus)"
            }} else {{
                Write-Output "STATUS: Failed"
            }}
            """
            try:
                out = self._execute_ps(ps_connect, timeout=8)
                if "STATUS: Connected" in out:
                    connected = True
            except Exception:
                pass

        if connected:
            return True, f"Successfully {shift_msg.lower()}, sir. Audio output is now live."
        else:
            if cur_connected:
                return True, f"Disconnected {cur_connected} to shift to {target_display}, but {target_display} did not respond. Please ensure it is powered on and in range, sir."
            else:
                return True, f"Attempting connection to {target_display}, sir. Please ensure the device is powered on."

    # --- NETWORK DIAGNOSTICS & IP / PING ---
    def get_network_diagnostics(self) -> Tuple[bool, str]:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(0.6)
            try:
                s.connect(('8.8.8.8', 80))
                local_ip = s.getsockname()[0]
            except Exception:
                local_ip = socket.gethostbyname(socket.gethostname())
            finally:
                s.close()

            ping_res = subprocess.run("ping -n 1 8.8.8.8", shell=True, capture_output=True, text=True, timeout=3)
            time_m = re.search(r'time[=<](\d+)ms', ping_res.stdout)
            latency = f"{time_m.group(1)}ms" if time_m else "nominal"

            return True, f"Network online. Your local IP address is {local_ip} with {latency} latency, sir."
        except Exception as e:
            return False, f"Network diagnostic failure: {e}"

    # --- AIRPLANE & HOTSPOT ---
    def toggle_airplane_mode(self) -> Tuple[bool, str]:
        try:
            subprocess.Popen('explorer.exe ms-settings:network-airplanemode', shell=True)
            return True, "Opening Airplane Mode settings, sir."
        except Exception as e:
            return False, f"Failed to access Airplane Mode: {e}"

    def toggle_hotspot(self) -> Tuple[bool, str]:
        try:
            subprocess.Popen('explorer.exe ms-settings:network-mobilehotspot', shell=True)
            return True, "Opening Mobile Hotspot settings, sir."
        except Exception as e:
            return False, f"Failed to access Mobile Hotspot: {e}"

    # --- INTELLIGENT SEMANTIC PARSER (DUAL PATH: AI + LOCAL NLP) ---
    def parse_and_execute(self, command: str) -> Tuple[bool, Optional[str]]:
        if not command:
            return False, None
        cmd = command.strip().lower()
        clean = re.sub(r'[\.\?!,]', '', cmd).strip()

        # PATH 1: CLOUD LLM INTENT (Nebius Token Factory / NVIDIA Nemotron)
        try:
            from ai_engine import ai_interpret_intent
            ai_data = ai_interpret_intent(command)
            if ai_data and ai_data.get("domain") == "WIRELESS":
                action = ai_data.get("action", "")
                target = ai_data.get("target")
                if action == "WIFI_ON":
                    return self.turn_on_wifi()
                elif action == "WIFI_OFF":
                    return self.turn_off_wifi()
                elif action == "WIFI_STATUS":
                    return self.get_wifi_status()
                elif action == "WIFI_SCAN":
                    return self.scan_wifi_networks()
                elif action == "WIFI_CONNECT" and target:
                    return self.connect_wifi(target)
                elif action == "WIFI_DISCONNECT":
                    return self.disconnect_wifi()
                elif action == "BLUETOOTH_ON":
                    return self.turn_on_bluetooth()
                elif action == "BLUETOOTH_OFF":
                    return self.turn_off_bluetooth()
                elif action == "BLUETOOTH_STATUS":
                    return self.get_bluetooth_status()
                elif action in ["BLUETOOTH_CONNECT", "BLUETOOTH_SWITCH"] and target:
                    return self.switch_bluetooth_device(target)
                elif action == "BLUETOOTH_LIST":
                    return self.list_bluetooth_devices()
                elif action == "BLUETOOTH_SETTINGS":
                    return self.open_bluetooth_settings()
                elif action == "NETWORK_INFO":
                    return self.get_network_diagnostics()
                elif action == "AIRPLANE_MODE":
                    return self.toggle_airplane_mode()
                elif action == "HOTSPOT":
                    return self.toggle_hotspot()
        except Exception:
            pass

        # PATH 2: HIGH-INTELLIGENCE LOCAL SEMANTIC CLASSIFIER
        # 1. NETWORK DIAGNOSTICS (IP, PING, SPEED, ONLINE STATUS)
        if any(w in clean for w in ["ip address", "my ip", "what ip", "ping", "latency", "network speed", "internet speed", "are we online"]):
            return self.get_network_diagnostics()

        # 2. AIRPLANE / FLIGHT MODE
        if any(k in clean for k in ["airplane mode", "flight mode", "aeroplane mode", "cut all wireless"]):
            return self.toggle_airplane_mode()

        # 3. MOBILE HOTSPOT
        if any(k in clean for k in ["mobile hotspot", "hotspot", "share internet", "share wifi", "tethering"]):
            return self.toggle_hotspot()

        # 4. BLUETOOTH INTELLIGENCE & AUDIO DEVICE SHIFTING
        if "bluetooth" in clean or "bt " in clean or clean.endswith(" bt") or " bt " in clean:
            if any(w in clean for w in ["turn off", "shut down", "kill", "disable", "power off", "switch off", "deactivate"]):
                return self.turn_off_bluetooth()
            if any(w in clean for w in ["turn on", "enable", "start", "activate", "power on", "switch on"]):
                return self.turn_on_bluetooth()
            # Connect / Shift / Switch specific device (e.g. "connect bt with wave pro", "switch bt to hammer")
            if any(w in clean for w in ["connect", "switch", "shift", "route", "pair with"]):
                target_m = re.search(r'(?:connect|switch|shift|route|pair with)(?:\s+(?:bt|bluetooth|audio))?(?:\s+(?:to|with|into))?(?:\s+(?:the|device))?\s+([a-zA-Z0-9_\-\s\+]+)', clean)
                if target_m:
                    cand = target_m.group(1).strip()
                    cand_clean = re.sub(r'^(?:bt|bluetooth|with|to)\s+', '', cand).strip()
                    if cand_clean and cand_clean not in ["settings", "device", "devices"]:
                        return self.switch_bluetooth_device(cand_clean)
            if any(w in clean for w in ["setting", "settings", "pair", "add device", "open bluetooth"]):
                return self.open_bluetooth_settings()
            if any(w in clean for w in ["status", "is bluetooth", "on or off", "check"]):
                return self.get_bluetooth_status()
            return self.list_bluetooth_devices()

        # 5. WI-FI TURN OFF / POWER DOWN
        if any(w in clean for w in ["turn off", "shut down", "kill", "disable", "power off", "switch off", "deactivate"]) and \
           any(w in clean for w in ["wifi", "wi-fi", "wireless"]):
            return self.turn_off_wifi()

        # 6. WI-FI TURN ON / POWER UP
        if any(w in clean for w in ["turn on", "enable", "start", "activate", "power on", "switch on"]) and \
           any(w in clean for w in ["wifi", "wi-fi", "wireless"]):
            return self.turn_on_wifi()

        # 7. WI-FI DISCONNECT / UNHOOK
        if any(w in clean for w in ["disconnect", "drop", "unhook", "sever", "leave"]) and \
           any(w in clean for w in ["wifi", "wi-fi", "network", "internet", "connection"]):
            return self.disconnect_wifi()

        # 8. WI-FI SCAN / SEARCH
        if any(p in clean for p in [
            "scan wifi", "scan for wifi", "search wifi", "search for wifi",
            "available wifi", "available networks", "wifi list", "list wifi",
            "nearby wifi", "wifi nearby", "networks nearby", "show networks",
            "show available networks", "show wifi", "what wifi is nearby"
        ]):
            return self.scan_wifi_networks()

        # 9. WI-FI STATUS / QUERY
        if any(p in clean for p in [
            "what wifi", "which wifi", "current wifi", "wifi status", "check wifi",
            "am i connected", "are we connected", "connection status", "how is my wifi",
            "wifi signal", "wifi info"
        ]):
            return self.get_wifi_status()

        # 10. WI-FI SETTINGS / FLYOUT
        if any(p in clean for p in ["wifi settings", "open wifi", "open wifi settings", "show available networks"]):
            return self.open_wifi_flyout()

        # 11. WI-FI CONNECT TO NETWORK
        connect_match = re.search(r'(?:connect|join|link)\s+(?:to\s+)?(?:the\s+)?(?:wifi\s+|network\s+)?([a-zA-Z0-9_\-\s]+)', clean)
        if connect_match:
            candidate = connect_match.group(1).strip()
            if candidate and candidate not in ["internet", "web", "online"]:
                profiles = self.get_saved_profiles()
                candidate_clean = re.sub(r'^(?:wifi|network)\s+', '', candidate).strip()
                matches_profile = any(candidate_clean.lower() in p.lower() or p.lower() in candidate_clean.lower() for p in profiles)
                if matches_profile or "wifi" in clean or "network" in clean:
                    return self.connect_wifi(candidate_clean if candidate_clean else candidate)

        return False, None

class SystemDriver:
    def __init__(self):
        self.app_engine = AppIndexEngine()
        self.hardware = HardwareDriver()
        self.wireless = WirelessDriver()
        from window_ops import WINDOW_MGR
        self.window_mgr = WINDOW_MGR
        from vision_ops import VISION_ENGINE
        self.vision = VISION_ENGINE
        # Fast load cache synchronously in <15ms
        self.app_engine.load_cache()
        # Start background scanner to keep registry/UWP apps completely fresh
        self.app_engine.start_background_indexer()

    def set_hud_window_provider(self, provider_fn):
        self.hardware.set_hud_window_provider(provider_fn)

    def execute_hardware(self, command):
        return self.hardware.parse_and_execute(command)

    def execute_wireless(self, command):
        return self.wireless.parse_and_execute(command)

    def execute_window_management(self, command):
        return self.window_mgr.parse_and_execute(command)

    def execute_vision(self, command):
        return self.vision.parse_and_execute(command)

    def execute_nlp(self, command, search_func=None):
        from nlp_dispatcher import execute_chained_pipeline
        return execute_chained_pipeline(command, self, search_func)

    def open_application(self, name):
        success, spoken, app_name = self.app_engine.launch(name)
        return success, spoken

    def close_application(self, name):
        success, spoken, app_name = self.app_engine.close(name)
        return success, spoken

    def list_running_apps(self):
        apps = []
        for proc in psutil.process_iter(['name']):
            try:
                name = proc.info.get('name')
                if name:
                    apps.append(name)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return list(set(apps))

    def shutdown_pc(self):
        subprocess.run("shutdown /s /t 1", shell=True)
        return True, "Shutting down"

    def restart_pc(self):
        subprocess.run("shutdown /r /t 1", shell=True)
        return True, "Restarting"

    def lock_pc(self):
        subprocess.run("rundll32.exe user32.dll,LockWorkStation", shell=True)
        return True, "Locked"

    # --- NETWORK CONTROL ---
    def toggle_wifi(self, enable=True):
        if not enable:
            return self.wireless.turn_off_wifi()
        else:
            return self.wireless.turn_on_wifi()

    def connect_to_wifi(self, ssid):
        return self.wireless.connect_wifi(ssid)

    # --- SMART FILESYSTEM ENGINE ---
    def _get_search_roots(self):
        home = os.path.expanduser("~")
        return [
            os.path.join(home, "Desktop"),
            os.path.join(home, "Downloads"),
            os.path.join(home, "Documents"),
            os.path.join(home, "Pictures"),
            os.path.join(home, "Videos")
        ]

    def locate_path(self, target_name, location_limit=None, target_type="ANY"):
        clean = target_name.lower().strip()
        search_roots = self._get_search_roots()

        if location_limit:
            resolved_parent = self.resolve_directory(location_limit)
            if os.path.exists(resolved_parent):
                search_roots = [resolved_parent]

        for root_dir in search_roots:
            if not os.path.exists(root_dir):
                continue
            for root, dirs, files in os.walk(root_dir):
                if target_type in ["FOLDER", "ANY"]:
                    for d in dirs:
                        if clean == d.lower():
                            return True, os.path.join(root, d), True
                if target_type in ["FILE", "ANY"]:
                    for f in files:
                        name_no_ext = os.path.splitext(f.lower())[0]
                        if clean == f.lower() or clean == name_no_ext:
                            return True, os.path.join(root, f), False
        return False, "", False

    def locate_all_paths(self, target_name, target_type="ANY"):
        clean = target_name.lower().strip()
        search_roots = self._get_search_roots()
        matches = []

        for root_dir in search_roots:
            if not os.path.exists(root_dir):
                continue
            for root, dirs, files in os.walk(root_dir):
                if target_type in ["FOLDER", "ANY"]:
                    for d in dirs:
                        if clean == d.lower():
                            matches.append(os.path.join(root, d))
                if target_type in ["FILE", "ANY"]:
                    for f in files:
                        name_no_ext = os.path.splitext(f.lower())[0]
                        if clean == f.lower() or clean == name_no_ext:
                            matches.append(os.path.join(root, f))
        return matches

    def resolve_directory(self, location_name):
        home = os.path.expanduser("~")
        if not location_name:
            return os.path.join(home, "Desktop")

        low = location_name.lower().strip()
        shortcuts = {
            "desktop": os.path.join(home, "Desktop"),
            "downloads": os.path.join(home, "Downloads"),
            "documents": os.path.join(home, "Documents"),
            "pictures": os.path.join(home, "Pictures"),
            "videos": os.path.join(home, "Videos"),
        }
        if low in shortcuts:
            return shortcuts[low]

        # 1. Try exact folder search
        found, path, is_dir = self.locate_path(location_name, target_type="FOLDER")
        if found and is_dir:
            return path

        # 2. Smart NLP fallback
        words = [w for w in low.split() if w not in ["in", "inside", "at", "the", "folder", "directory"]]
        for w in words:
            if w in shortcuts:
                return shortcuts[w]
            found, path, is_dir = self.locate_path(w, target_type="FOLDER")
            if found and is_dir:
                return path

        return os.path.join(home, "Desktop")

    def smart_create(self, item_type, name, location):
        dest_dir = self.resolve_directory(location)
        full_path = os.path.join(dest_dir, name)
        try:
            if item_type == "FOLDER":
                os.makedirs(full_path, exist_ok=True)
                return True, f"Folder '{name}' created in {os.path.basename(dest_dir)}."
            else:
                templates = {
                    ".py": "# Created by HYDRA\n\n",
                    ".html": "<!DOCTYPE html>\n<html>\n<head>\n\t<title>Document</title>\n</head>\n<body>\n\n</body>\n</html>",
                    ".js": "// Generated by HYDRA\n",
                    ".json": "{\n\n}"
                }
                _, ext = os.path.splitext(name)
                content = templates.get(ext.lower(), "")

                with open(full_path, 'w', encoding='utf-8') as f:
                    f.write(content)

                return True, f"File '{name}' created in {os.path.basename(dest_dir)}."
        except Exception as e:
            return False, str(e)

    def smart_delete(self, target_name):
        found, path, is_dir = self.locate_path(target_name)
        if not found:
            return False, f"Could not locate '{target_name}' to delete."

        home = os.path.expanduser("~")
        if not os.path.abspath(path).startswith(os.path.abspath(home)):
            return False, "Safety Valve: Refusing to delete OS files outside your personal User directory."

        # Dodaios Safety Impact Assessment
        try:
            from dodaios_bridge import DODAIOS_BRIDGE
            is_safe, safety_msg = DODAIOS_BRIDGE.safety.assess_execution_safety(f"del {path}")
            if not is_safe:
                return False, safety_msg
        except Exception:
            pass

        try:
            # Use safe recycle bin if send2trash is available, else standard delete
            try:
                import send2trash
                send2trash.send2trash(path)
            except ImportError:
                if is_dir:
                    shutil.rmtree(path)
                else:
                    os.remove(path)
            return True, f"Purged {os.path.basename(path)}, sir."
        except Exception as e:
            return False, f"Deletion error: {e}"

    def execute_shell_command(self, cmd: str) -> Tuple[bool, str]:
        """Executes a shell command guarded by the Dodaios Safety Engine."""
        from dodaios_bridge import DODAIOS_BRIDGE
        is_safe, safety_msg = DODAIOS_BRIDGE.safety.assess_execution_safety(cmd)
        if not is_safe:
            return False, safety_msg
        try:
            res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=15)
            out = res.stdout if res.stdout else res.stderr
            return True, out.strip() if out else "Command completed."
        except Exception as e:
            return False, f"Command execution error: {e}"

    def smart_move(self, source_name, dest_location):
        found, src_path, _ = self.locate_path(source_name)
        if not found:
            return False, f"Source item '{source_name}' not found."

        dest_dir = self.resolve_directory(dest_location)
        try:
            shutil.move(src_path, os.path.join(dest_dir, os.path.basename(src_path)))
            return True, f"Moved '{source_name}' into {os.path.basename(dest_dir)}."
        except Exception as e:
            return False, f"Move error: {e}"

    def change_extension(self, file_name_or_path, new_ext):
        if os.path.isabs(file_name_or_path) and os.path.exists(file_name_or_path):
            path = file_name_or_path
            is_dir = os.path.isdir(path)
            found = True
        else:
            found, path, is_dir = self.locate_path(file_name_or_path)

        if not found or is_dir:
            return False, "Could not find file to modify."

        new_ext = new_ext.replace("dot ", ".").strip()
        if not new_ext.startswith('.'):
            new_ext = '.' + new_ext

        base = os.path.splitext(path)[0]
        new_path = base + new_ext
        try:
            os.rename(path, new_path)
            return True, f"Changed type of {os.path.basename(path)} to {new_ext}."
        except Exception as e:
            return False, f"Failed to change extension: {e}"

    def execute_command(self, action, payload=None):
        if action == "HARDWARE":
            return self.execute_hardware(payload)
        if action == "WIRELESS":
            return self.execute_wireless(payload)
        if action == "OPEN_APP":
            return self.open_application(payload)
        if action == "CLOSE_APP":
            return self.close_application(payload)
        if action == "LIST_APPS":
            return True, ", ".join(self.list_running_apps()[:10])
        if action == "SHUTDOWN":
            return self.shutdown_pc()
        if action == "RESTART":
            return self.restart_pc()
        if action == "LOCK":
            return self.lock_pc()
        if action == "WIFI_ON":
            return self.toggle_wifi(enable=True)
        if action == "WIFI_OFF":
            return self.toggle_wifi(enable=False)
        if action == "WIFI_CONNECT":
            return self.connect_to_wifi(payload)
        return False, "UNKNOWN_ACTION"