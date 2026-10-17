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
from typing import Dict, List, Optional, Tuple
import pyautogui
import pyperclip

pyautogui.FAILSAFE = False

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
        self.screen_width, self.screen_height = pyautogui.size()

    def get_position(self):
        x, y = pyautogui.position()
        return x, y

    def move_to(self, x, y, duration=0.15):
        x = max(0, min(self.screen_width - 1, int(x)))
        y = max(0, min(self.screen_height - 1, int(y)))
        pyautogui.moveTo(x, y, duration=duration)
        return True, f"Moved mouse to {x}, {y}, sir."

    def move_relative(self, dx, dy, duration=0.15):
        pyautogui.moveRel(int(dx), int(dy), duration=duration)
        x, y = pyautogui.position()
        return True, f"Moved mouse to {x}, {y}, sir."

    def click(self, button="left", clicks=1):
        pyautogui.click(button=button, clicks=clicks)
        click_type = "Double clicked" if clicks == 2 else ("Triple clicked" if clicks == 3 else f"{button.capitalize()} clicked")
        return True, f"{click_type}, sir."

    def click_at(self, x, y, button="left", clicks=1):
        self.move_to(x, y, duration=0.1)
        return self.click(button=button, clicks=clicks)

    def mouse_down(self, button="left"):
        pyautogui.mouseDown(button=button)
        return True, f"Holding {button} mouse button, sir."

    def mouse_up(self, button="left"):
        pyautogui.mouseUp(button=button)
        return True, f"Released {button} mouse button, sir."

    def drag_to(self, x, y, duration=0.4):
        x = max(0, min(self.screen_width - 1, int(x)))
        y = max(0, min(self.screen_height - 1, int(y)))
        pyautogui.dragTo(x, y, duration=duration)
        return True, f"Dragged to {x}, {y}, sir."

    def drag_relative(self, dx, dy, duration=0.4):
        pyautogui.dragRel(int(dx), int(dy), duration=duration)
        return True, f"Dragged item, sir."

    def scroll(self, amount):
        pyautogui.scroll(int(amount * 120))
        direction = "up" if amount > 0 else "down"
        return True, f"Scrolled {direction}, sir."

    def type_text(self, text: str):
        if not text:
            return False, "No text provided to type, sir."
        if (text.startswith('"') and text.endswith('"')) or (text.startswith("'") and text.endswith("'")):
            text = text[1:-1]
        try:
            old_clip = ""
            try: old_clip = pyperclip.paste()
            except: pass
            
            pyperclip.copy(text)
            pyautogui.hotkey('ctrl', 'v')
            time.sleep(0.05)
            
            try: pyperclip.copy(old_clip)
            except: pass
            return True, f"Typed requested text, sir."
        except Exception:
            pyautogui.write(text, interval=0.01)
            return True, f"Typed requested text, sir."

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
            "capslock": "capslock"
        }
        actual_key = key_map.get(low, low)
        try:
            for _ in range(count):
                pyautogui.press(actual_key)
            return True, f"Pressed {key_name.capitalize()}, sir."
        except Exception as e:
            return False, f"Failed to press key {key_name}: {e}"

    def hotkey(self, *keys):
        try:
            pyautogui.hotkey(*keys)
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
        if cmd in ["double click", "double-click", "double click mouse", "double click please"]:
            return self.click("left", clicks=2)
        if cmd in ["triple click", "triple-click"]:
            return self.click("left", clicks=3)
        if cmd in ["right click", "right click mouse", "right-click", "context menu"]:
            return self.click("right", clicks=1)
        if cmd in ["middle click", "middle-click"]:
            return self.click("middle", clicks=1)
        if cmd in ["click", "left click", "click mouse", "click here", "press click"]:
            return self.click("left", clicks=1)

        click_coord_match = re.search(r'click (?:at|on)?\s*(\d+)[,\s]+(\d+)', cmd)
        if click_coord_match:
            x, y = int(click_coord_match.group(1)), int(click_coord_match.group(2))
            return self.click_at(x, y)

        # 3. MOUSE MOVEMENT
        move_coord_match = re.search(r'(?:move|cursor|mouse) (?:to)?\s*(\d+)[,\s]+(\d+)', cmd)
        if move_coord_match:
            x, y = int(move_coord_match.group(1)), int(move_coord_match.group(2))
            return self.move_to(x, y)

        if any(w in cmd for w in ["mouse to center", "cursor to center", "mouse to middle", "cursor to middle"]):
            return self.move_to(self.screen_width // 2, self.screen_height // 2)
        if any(w in cmd for w in ["mouse to top left", "cursor to top left"]):
            return self.move_to(50, 50)
        if any(w in cmd for w in ["mouse to top right", "cursor to top right"]):
            return self.move_to(self.screen_width - 50, 50)
        if any(w in cmd for w in ["mouse to bottom left", "cursor to bottom left"]):
            return self.move_to(50, self.screen_height - 50)
        if any(w in cmd for w in ["mouse to bottom right", "cursor to bottom right"]):
            return self.move_to(self.screen_width - 50, self.screen_height - 50)

        rel_match = re.search(r'(?:move )?(?:mouse |cursor )?(up|down|left|right)(?:\s*(?:by\s*)?(\d+))?', cmd)
        if rel_match and any(w in cmd for w in ["move", "mouse", "cursor"]):
            direction = rel_match.group(1)
            dist = int(rel_match.group(2)) if rel_match.group(2) else 150
            if direction == "up": return self.move_relative(0, -dist)
            if direction == "down": return self.move_relative(0, dist)
            if direction == "left": return self.move_relative(-dist, 0)
            if direction == "right": return self.move_relative(dist, 0)

        if any(w in cmd for w in ["where is the mouse", "where is mouse", "mouse position", "cursor position", "current coordinates"]):
            x, y = self.get_position()
            return True, f"Mouse cursor is currently at coordinates {x} by {y} on your {self.screen_width} by {self.screen_height} screen, sir."

        # Drag and drop
        drag_coord_match = re.search(r'drag (?:to)?\s*(\d+)[,\s]+(\d+)', cmd)
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

        # 4. TYPING TEXT (Preserve case from raw_cmd)
        type_match = re.search(r'^(?:type|write|enter text)\s+(.*)', raw_cmd, re.IGNORECASE)
        if type_match:
            text_to_type = type_match.group(1).strip()
            if text_to_type.lower().startswith("this:"):
                text_to_type = text_to_type[5:].strip()
            elif text_to_type.lower().startswith("this "):
                text_to_type = text_to_type[5:].strip()
            return self.type_text(text_to_type)

        # 5. KEYBOARD PRESSES
        key_press_triggers = {
            "enter": ["press enter", "hit enter", "press return", "hit return"],
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
            "f11": ["full screen", "fullscreen", "press f11", "toggle full screen"]
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

        return False, None

class SystemDriver:
    def __init__(self):
        self.app_engine = AppIndexEngine()
        self.hardware = HardwareDriver()
        # Fast load cache synchronously in <15ms
        self.app_engine.load_cache()
        # Start background scanner to keep registry/UWP apps completely fresh
        self.app_engine.start_background_indexer()

    def execute_hardware(self, command):
        return self.hardware.parse_and_execute(command)

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
        state = "ENABLED" if enable else "DISABLED"
        try:
            cmd = f'netsh interface set interface name="Wi-Fi" admin={state}'
            subprocess.run(cmd, shell=True, capture_output=True)
            return True, f"Wi-Fi adapter {state.lower()}"
        except Exception as e:
            return False, str(e)

    def connect_to_wifi(self, ssid):
        if not ssid:
            return False, "No network SSID specified."
        try:
            cmd = f'netsh wlan connect name="{ssid}"'
            subprocess.run(cmd, shell=True, capture_output=True, text=True)
            return True, ssid
        except Exception as e:
            return False, str(e)

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