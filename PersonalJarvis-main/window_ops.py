"""
HYDRA Window Management & Multitasking Engine (Phase 5)
Provides high-performance, autonomous control over Windows applications,
window placement, focus switching, snapping, multitasking, and desktop views.
"""

import ctypes
from ctypes import wintypes
import psutil
import re
import difflib
import time
import win32con
import win32com.client
from typing import Dict, Any, List, Optional, Tuple

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

user32.OpenWindowStationW.restype = wintypes.HANDLE
user32.OpenWindowStationW.argtypes = [wintypes.LPCWSTR, wintypes.BOOL, wintypes.DWORD]
user32.SetProcessWindowStation.restype = wintypes.BOOL
user32.SetProcessWindowStation.argtypes = [wintypes.HANDLE]
user32.OpenDesktopW.restype = wintypes.HANDLE
user32.OpenDesktopW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
user32.SetThreadDesktop.restype = wintypes.BOOL
user32.SetThreadDesktop.argtypes = [wintypes.HANDLE]

IGNORE_TITLES = {
    "Program Manager", "Default IME", "MSCTFIME UI", "DesktopWindowXamlSource",
    "DDE Server Window", "PopupHost", "Windows Input Experience", "Task Host Window",
    "_q_titlebar", "Touchpad driver tray window", "Syn Zoom Window", "Syn Visual Window",
    "Battery Meter", "Windows Widgets", "TouchPad object helper window", "CrossDeviceResumeWindow",
    "Windows Push Notifications Platform", "SynLenovoHelper", "Touchpad driver helper window",
    "The Event Manager Dashboard", "DWM Notification Window"
}

# Known process/app name aliases for fast, natural voice recognition
APP_ALIASES = {
    "chrome": ["google chrome", "chrome.exe", "chrome"],
    "brave": ["brave browser", "brave.exe", "brave"],
    "edge": ["microsoft edge", "msedge.exe", "edge"],
    "spotify": ["spotify.exe", "spotify music", "spotify"],
    "whatsapp": ["whatsapp.exe", "whatsapp.root.exe", "whatsapp"],
    "code": ["visual studio code", "vscode", "code.exe", "vs code"],
    "terminal": ["windows terminal", "windowsterminal.exe", "wt.exe", "cmd.exe", "powershell.exe", "terminal", "command prompt", "powershell"],
    "explorer": ["file explorer", "explorer.exe", "files", "folder", "this pc"],
    "canva": ["canva.exe", "canva"],
    "settings": ["system settings", "windows settings", "settings.exe", "settings"],
    "notepad": ["notepad.exe", "notepad"],
    "discord": ["discord.exe", "discord"],
    "telegram": ["telegram.exe", "telegram"],
    "gmail": ["gmail", "inbox", "google mail", "mail.google.com"],
    "classroom": ["google classroom", "classroom", "classes", "classroom.google.com"],
    "youtube": ["youtube", "youtube.com"],
    "hydra": ["hydra system interface", "hydra-main", "hydra"]
}


class WindowManagerDriver:
    def __init__(self):
        self.hdesk = None
        self._ensure_desktop_access()

    def _ensure_desktop_access(self):
        """Attaches to the interactive user desktop session (WinSta0\\Default)."""
        try:
            hwinsta = user32.OpenWindowStationW('WinSta0', False, 0x0000037F)
            if hwinsta:
                user32.SetProcessWindowStation(hwinsta)
            self.hdesk = user32.OpenDesktopW('Default', 0, False, 0x000001FF)
            if self.hdesk:
                user32.SetThreadDesktop(self.hdesk)
        except Exception:
            pass

    def get_work_area(self) -> Tuple[int, int, int, int]:
        """Returns the screen work area (excluding Windows taskbar)."""
        rect = wintypes.RECT()
        # SPI_GETWORKAREA = 0x0030
        user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)
        return rect.left, rect.top, rect.right, rect.bottom

    def get_open_windows(self) -> List[Dict[str, Any]]:
        """Returns all real user-visible desktop application windows."""
        self._ensure_desktop_access()
        windows = []

        def enum_cb(hwnd, _):
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value.strip()

            if (not title or title in IGNORE_TITLES or 
                title.startswith("GDI+ Window") or 
                ".NET-Broadcast" in title or 
                "H.NotifyIcon" in title):
                return True

            is_min = bool(user32.IsIconic(hwnd))
            is_max = bool(user32.IsZoomed(hwnd))

            rect = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            w = rect.right - rect.left
            h = rect.bottom - rect.top
            if not is_min and (w < 100 or h < 100):
                return True

            ex_style = user32.GetWindowLongW(hwnd, -20)  # GWL_EXSTYLE
            # Skip tool windows unless explicitly marked as app windows
            if (ex_style & 0x00000080) and not (ex_style & 0x00040000):
                return True

            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            pname = ""
            try:
                pname = psutil.Process(pid.value).name()
            except Exception:
                pass

            is_min = bool(user32.IsIconic(hwnd))
            is_max = bool(user32.IsZoomed(hwnd))

            # Clean friendly app name
            clean_app_name = pname.replace(".exe", "").title()
            for alias_key, alias_list in APP_ALIASES.items():
                if pname.lower() in alias_list or any(a in title.lower() for a in alias_list):
                    clean_app_name = alias_key.title()
                    break

            windows.append({
                "hwnd": hwnd,
                "title": title,
                "process": pname,
                "app_name": clean_app_name,
                "pid": pid.value,
                "rect": (rect.left, rect.top, rect.right, rect.bottom),
                "width": w,
                "height": h,
                "is_minimized": is_min,
                "is_maximized": is_max
            })
            return True

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        user32.EnumDesktopWindows(self.hdesk if self.hdesk else 0, WNDENUMPROC(enum_cb), 0)
        return windows

    def resolve_window(self, query: str) -> Optional[Dict[str, Any]]:
        """Intelligently matches a spoken query to an open window by alias, process, or title."""
        windows = self.get_open_windows()
        if not windows:
            return None

        clean = query.lower().strip() if query else ""
        for fill in [
            "switch to", "switch window to", "bring to front", "bring", "focus on", 
            "focus", "go to", "activate", "maximize", "minimize", "restore", "snap", 
            "to left", "to right", "window", "app", "application", "browser", "please"
        ]:
            clean = clean.replace(fill, "").strip()

        if not clean or clean in ["this", "current", "active", "it"]:
            fg = user32.GetForegroundWindow()
            for w in windows:
                if w["hwnd"] == fg:
                    return w
            return windows[0]

        # 1. Alias match
        for alias_key, alias_list in APP_ALIASES.items():
            if clean == alias_key or any(clean == a or clean in a for a in alias_list):
                for w in windows:
                    pname = w["process"].lower()
                    title = w["title"].lower()
                    if any(a in pname or a in title for a in alias_list):
                        return w

        # 2. Substring match in process or title
        for w in windows:
            pname = w["process"].lower().replace(".exe", "")
            title = w["title"].lower()
            if clean in pname or clean in title:
                return w

        # 3. Fuzzy match
        scored = []
        for w in windows:
            pname = w["process"].lower().replace(".exe", "")
            title = w["title"].lower()
            s1 = difflib.SequenceMatcher(None, clean, pname).ratio()
            s2 = difflib.SequenceMatcher(None, clean, title[:len(clean)+6]).ratio()
            score = max(s1, s2)
            scored.append((score, w))

        scored.sort(key=lambda x: x[0], reverse=True)
        if scored and scored[0][0] >= 0.45:
            return scored[0][1]

        return None

    def focus_window(self, hwnd: int) -> bool:
        """Brings the window to the foreground, bypassing Windows focus locks."""
        try:
            cur_thread = kernel32.GetCurrentThreadId()
            fg_hwnd = user32.GetForegroundWindow()
            fg_thread = user32.GetWindowThreadProcessId(fg_hwnd, None) if fg_hwnd else 0
            target_thread = user32.GetWindowThreadProcessId(hwnd, None)

            if fg_thread and fg_thread != cur_thread:
                user32.AttachThreadInput(cur_thread, fg_thread, True)
            if target_thread and target_thread != cur_thread:
                user32.AttachThreadInput(cur_thread, target_thread, True)

            # Alt key pulse to pierce through Windows foreground lock
            user32.keybd_event(0x12, 0, 0, 0)
            time.sleep(0.02)
            user32.keybd_event(0x12, 0, 2, 0)
            time.sleep(0.02)

            if user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, win32con.SW_RESTORE)
            else:
                user32.ShowWindow(hwnd, win32con.SW_SHOW)

            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)

            if fg_thread and fg_thread != cur_thread:
                user32.AttachThreadInput(cur_thread, fg_thread, False)
            if target_thread and target_thread != cur_thread:
                user32.AttachThreadInput(cur_thread, target_thread, False)
            return True
        except Exception as e:
            print(f"[WINDOW_MGR] Focus error: {e}")
            return False

    def switch_to(self, query: str) -> Tuple[bool, str]:
        win = self.resolve_window(query)
        if not win:
            return False, f"I couldn't find an open window matching '{query}', sir."
        self.focus_window(win["hwnd"])
        app_name = win.get("app_name") or win.get("title")
        return True, f"Switched to {app_name}, sir."

    def maximize(self, query: Optional[str] = None) -> Tuple[bool, str]:
        win = self.resolve_window(query)
        if not win:
            return False, "I couldn't find a window to maximize, sir."
        self.focus_window(win["hwnd"])
        user32.ShowWindow(win["hwnd"], win32con.SW_MAXIMIZE)
        app_name = win.get("app_name") or win.get("title")
        return True, f"Maximized {app_name}, sir."

    def minimize(self, query: Optional[str] = None) -> Tuple[bool, str]:
        win = self.resolve_window(query)
        if not win:
            return False, "I couldn't find a window to minimize, sir."
        user32.ShowWindow(win["hwnd"], win32con.SW_MINIMIZE)
        app_name = win.get("app_name") or win.get("title")
        return True, f"Minimized {app_name}, sir."

    def restore(self, query: Optional[str] = None) -> Tuple[bool, str]:
        win = self.resolve_window(query)
        if not win:
            return False, "I couldn't find a window to restore, sir."
        user32.ShowWindow(win["hwnd"], win32con.SW_RESTORE)
        self.focus_window(win["hwnd"])
        app_name = win.get("app_name") or win.get("title")
        return True, f"Restored {app_name} to normal size, sir."

    def close(self, query: Optional[str] = None) -> Tuple[bool, str]:
        win = self.resolve_window(query)
        if not win:
            return False, "I couldn't find a window to close, sir."
        user32.PostMessageW(win["hwnd"], win32con.WM_CLOSE, 0, 0)
        app_name = win.get("app_name") or win.get("title")
        return True, f"Closed {app_name} window, sir."

    def snap_left(self, query: Optional[str] = None) -> Tuple[bool, str]:
        win = self.resolve_window(query)
        if not win:
            return False, "I couldn't find a window to snap left, sir."
        left, top, right, bottom = self.get_work_area()
        w = (right - left) // 2
        h = bottom - top
        self.focus_window(win["hwnd"])
        user32.ShowWindow(win["hwnd"], win32con.SW_RESTORE)
        user32.MoveWindow(win["hwnd"], left, top, w, h, True)
        app_name = win.get("app_name") or win.get("title")
        return True, f"Snapped {app_name} to the left half, sir."

    def snap_right(self, query: Optional[str] = None) -> Tuple[bool, str]:
        win = self.resolve_window(query)
        if not win:
            return False, "I couldn't find a window to snap right, sir."
        left, top, right, bottom = self.get_work_area()
        w = (right - left) // 2
        h = bottom - top
        self.focus_window(win["hwnd"])
        user32.ShowWindow(win["hwnd"], win32con.SW_RESTORE)
        user32.MoveWindow(win["hwnd"], left + w, top, w, h, True)
        app_name = win.get("app_name") or win.get("title")
        return True, f"Snapped {app_name} to the right half, sir."

    def snap_top(self, query: Optional[str] = None) -> Tuple[bool, str]:
        win = self.resolve_window(query)
        if not win:
            return False, "I couldn't find a window to snap top, sir."
        left, top, right, bottom = self.get_work_area()
        w = right - left
        h = (bottom - top) // 2
        self.focus_window(win["hwnd"])
        user32.ShowWindow(win["hwnd"], win32con.SW_RESTORE)
        user32.MoveWindow(win["hwnd"], left, top, w, h, True)
        app_name = win.get("app_name") or win.get("title")
        return True, f"Snapped {app_name} to the top half, sir."

    def snap_bottom(self, query: Optional[str] = None) -> Tuple[bool, str]:
        win = self.resolve_window(query)
        if not win:
            return False, "I couldn't find a window to snap bottom, sir."
        left, top, right, bottom = self.get_work_area()
        w = right - left
        h = (bottom - top) // 2
        self.focus_window(win["hwnd"])
        user32.ShowWindow(win["hwnd"], win32con.SW_RESTORE)
        user32.MoveWindow(win["hwnd"], left, top + h, w, h, True)
        app_name = win.get("app_name") or win.get("title")
        return True, f"Snapped {app_name} to the bottom half, sir."

    def snap_upper_left(self, query: Optional[str] = None) -> Tuple[bool, str]:
        win = self.resolve_window(query)
        if not win:
            return False, "I couldn't find a window to snap to the upper-left, sir."
        left, top, right, bottom = self.get_work_area()
        w = (right - left) // 2
        h = (bottom - top) // 2
        self.focus_window(win["hwnd"])
        user32.ShowWindow(win["hwnd"], win32con.SW_RESTORE)
        user32.MoveWindow(win["hwnd"], left, top, w, h, True)
        app_name = win.get("app_name") or win.get("title")
        return True, f"Snapped {app_name} to the upper-left quadrant, sir."

    def snap_upper_right(self, query: Optional[str] = None) -> Tuple[bool, str]:
        win = self.resolve_window(query)
        if not win:
            return False, "I couldn't find a window to snap to the upper-right, sir."
        left, top, right, bottom = self.get_work_area()
        w = (right - left) // 2
        h = (bottom - top) // 2
        self.focus_window(win["hwnd"])
        user32.ShowWindow(win["hwnd"], win32con.SW_RESTORE)
        user32.MoveWindow(win["hwnd"], left + w, top, w, h, True)
        app_name = win.get("app_name") or win.get("title")
        return True, f"Snapped {app_name} to the upper-right quadrant, sir."

    def snap_bottom_left(self, query: Optional[str] = None) -> Tuple[bool, str]:
        win = self.resolve_window(query)
        if not win:
            return False, "I couldn't find a window to snap to the bottom-left, sir."
        left, top, right, bottom = self.get_work_area()
        w = (right - left) // 2
        h = (bottom - top) // 2
        self.focus_window(win["hwnd"])
        user32.ShowWindow(win["hwnd"], win32con.SW_RESTORE)
        user32.MoveWindow(win["hwnd"], left, top + h, w, (bottom - top) - h, True)
        app_name = win.get("app_name") or win.get("title")
        return True, f"Snapped {app_name} to the bottom-left quadrant, sir."

    def snap_bottom_right(self, query: Optional[str] = None) -> Tuple[bool, str]:
        win = self.resolve_window(query)
        if not win:
            return False, "I couldn't find a window to snap to the bottom-right, sir."
        left, top, right, bottom = self.get_work_area()
        w = (right - left) // 2
        h = (bottom - top) // 2
        self.focus_window(win["hwnd"])
        user32.ShowWindow(win["hwnd"], win32con.SW_RESTORE)
        user32.MoveWindow(win["hwnd"], left + w, top + h, w, (bottom - top) - h, True)
        app_name = win.get("app_name") or win.get("title")
        return True, f"Snapped {app_name} to the bottom-right quadrant, sir."

    def center_window(self, query: Optional[str] = None) -> Tuple[bool, str]:
        win = self.resolve_window(query)
        if not win:
            return False, "I couldn't find a window to center, sir."
        left, top, right, bottom = self.get_work_area()
        screen_w = right - left
        screen_h = bottom - top
        target_w = int(screen_w * 0.7)
        target_h = int(screen_h * 0.8)
        pos_x = left + (screen_w - target_w) // 2
        pos_y = top + (screen_h - target_h) // 2
        self.focus_window(win["hwnd"])
        user32.ShowWindow(win["hwnd"], win32con.SW_RESTORE)
        user32.MoveWindow(win["hwnd"], pos_x, pos_y, target_w, target_h, True)
        app_name = win.get("app_name") or win.get("title")
        return True, f"Centered {app_name} on your screen, sir."

    def show_desktop(self) -> Tuple[bool, str]:
        try:
            shell = win32com.client.Dispatch("Shell.Application")
            shell.MinimizeAll()
            return True, "Showing desktop, sir."
        except Exception:
            user32.keybd_event(0x5B, 0, 0, 0)  # Win
            user32.keybd_event(0x44, 0, 0, 0)  # D
            user32.keybd_event(0x44, 0, 2, 0)
            user32.keybd_event(0x5B, 0, 2, 0)
            return True, "Showing desktop, sir."

    def restore_all(self) -> Tuple[bool, str]:
        try:
            shell = win32com.client.Dispatch("Shell.Application")
            shell.UndoMinimizeALL()
            return True, "Restored all windows, sir."
        except Exception:
            return False, "Could not restore all windows, sir."

    def list_open_windows(self) -> Tuple[bool, str]:
        windows = self.get_open_windows()
        if not windows:
            return True, "You do not have any open application windows, sir."
        
        names = []
        for w in windows:
            name = w.get("app_name") or w.get("title")
            if name and name not in names:
                names.append(name)
        
        count = len(names)
        if count == 1:
            reply = f"You currently have 1 window open: {names[0]}."
        elif count <= 4:
            reply = f"You currently have {count} windows open: {', '.join(names[:-1])}, and {names[-1]}."
        else:
            sample = ", ".join(names[:4])
            reply = f"You have {count} active windows open, including {sample}, and others."
        return True, reply

    def parse_and_execute(self, raw_cmd: str) -> Tuple[bool, str]:
        """Dispatches natural language window management voice commands."""
        cmd = raw_cmd.lower().strip().replace(".", "").replace("!", "").replace("?", "").strip()

        # 1. SHOW DESKTOP / MINIMIZE ALL
        if cmd in ["show desktop", "minimize all", "minimize all windows", "hide all windows", "go to desktop", "display desktop", "desktop view"]:
            return self.show_desktop()

        # 2. RESTORE ALL WINDOWS
        if cmd in ["restore all", "restore all windows", "unminimize all", "unminimize all windows", "bring back all windows"]:
            return self.restore_all()

        # 3. LIST OPEN WINDOWS
        if any(cmd == t for t in [
            "list windows", "list open windows", "show active windows", "what windows are open", 
            "what apps are open", "which windows are open", "which apps are open", "show open windows",
            "list running windows", "open windows", "active windows"
        ]) or any(cmd.startswith(t) for t in ["what windows are", "which windows are", "list open windows", "list all windows"]):
            return self.list_open_windows()

        # 3.5 SNAP QUADRANTS (Upper-Left, Upper-Right, Bottom-Left, Bottom-Right)
        # Upper Left:
        if any(w in cmd for w in ["upper left", "top left", "upper-left", "top-left"]) and any(w in cmd for w in ["snap", "tile", "put"]):
            target = re.sub(r'snap|tile|put|to|the|window|upper|top|left|-', ' ', cmd).strip()
            return self.snap_upper_left(target or None)

        # Upper Right:
        if any(w in cmd for w in ["upper right", "top right", "upper-right", "top-right"]) and any(w in cmd for w in ["snap", "tile", "put"]):
            target = re.sub(r'snap|tile|put|to|the|window|upper|top|right|-', ' ', cmd).strip()
            return self.snap_upper_right(target or None)

        # Bottom Left:
        if any(w in cmd for w in ["bottom left", "lower left", "bottom-left", "lower-left"]) and any(w in cmd for w in ["snap", "tile", "put"]):
            target = re.sub(r'snap|tile|put|to|the|window|bottom|lower|left|-', ' ', cmd).strip()
            return self.snap_bottom_left(target or None)

        # Bottom Right:
        if any(w in cmd for w in ["bottom right", "lower right", "bottom-right", "lower-right"]) and any(w in cmd for w in ["snap", "tile", "put"]):
            target = re.sub(r'snap|tile|put|to|the|window|bottom|lower|right|-', ' ', cmd).strip()
            return self.snap_bottom_right(target or None)

        # 4. SNAP WINDOW LEFT
        snap_left_triggers = [
            "snap to left", "snap window to left", "snap window left", "snap left", 
            "put on left", "tile left", "tile to left", "split left"
        ]
        for tr in snap_left_triggers:
            if tr in cmd:
                target = cmd.replace(tr, "").replace("window", "").strip()
                return self.snap_left(target or None)
        if cmd.startswith("snap ") and "left" in cmd:
            target = cmd[5:].replace("left", "").replace("to", "").replace("window", "").strip()
            return self.snap_left(target or None)

        # 5. SNAP WINDOW RIGHT
        snap_right_triggers = [
            "snap to right", "snap window to right", "snap window right", "snap right", 
            "put on right", "tile right", "tile to right", "split right"
        ]
        for tr in snap_right_triggers:
            if tr in cmd:
                target = cmd.replace(tr, "").replace("window", "").strip()
                return self.snap_right(target or None)
        if cmd.startswith("snap ") and "right" in cmd:
            target = cmd[5:].replace("right", "").replace("to", "").replace("window", "").strip()
            return self.snap_right(target or None)

        # 6. SNAP TOP / BOTTOM
        if any(w in cmd for w in ["snap to top", "snap top", "snap window top"]):
            target = cmd.replace("snap", "").replace("to", "").replace("top", "").replace("window", "").strip()
            return self.snap_top(target or None)

        if any(w in cmd for w in ["snap to bottom", "snap bottom", "snap window bottom"]):
            target = cmd.replace("snap", "").replace("to", "").replace("bottom", "").replace("window", "").strip()
            return self.snap_bottom(target or None)

        # 7. CENTER WINDOW
        if any(cmd.startswith(t) for t in ["center window", "center the window", "center "]):
            target = cmd.replace("center", "").replace("window", "").replace("the", "").strip()
            return self.center_window(target or None)

        # 8. MAXIMIZE WINDOW
        maximize_triggers = ["maximize window", "maximize this", "maximize", "make full screen", "fullscreen window"]
        for mt in maximize_triggers:
            if cmd == mt:
                return self.maximize(None)
            if cmd.startswith(mt + " "):
                target = cmd[len(mt):].strip()
                return self.maximize(target)
        if cmd.startswith("maximize "):
            target = cmd[9:].replace("window", "").strip()
            return self.maximize(target or None)

        # 9. MINIMIZE WINDOW
        minimize_triggers = ["minimize window", "minimize this", "minimize"]
        for mt in minimize_triggers:
            if cmd == mt:
                return self.minimize(None)
            if cmd.startswith(mt + " "):
                target = cmd[len(mt):].strip()
                return self.minimize(target)
        if cmd.startswith("minimize "):
            target = cmd[9:].replace("window", "").strip()
            return self.minimize(target or None)

        # 10. RESTORE / UNMAXIMIZE WINDOW
        restore_triggers = ["restore window", "restore this", "unmaximize window", "unmaximize"]
        for rt in restore_triggers:
            if cmd == rt:
                return self.restore(None)
            if cmd.startswith(rt + " "):
                target = cmd[len(rt):].strip()
                return self.restore(target)
        if cmd.startswith("restore "):
            target = cmd[8:].replace("window", "").strip()
            return self.restore(target or None)

        # 11. CLOSE WINDOW
        close_win_triggers = ["close window", "close this window", "close current window", "close active window"]
        for cwt in close_win_triggers:
            if cmd == cwt:
                return self.close(None)
        if cmd.startswith("close window ") or cmd.startswith("close active ") or cmd.endswith(" window"):
            target = cmd.replace("close", "").replace("window", "").replace("active", "").replace("current", "").strip()
            if target and not any(bad in target for bad in ["tab", "file", "panel", "info", "wifi"]):
                return self.close(target)

        # 12. SWITCH TO / FOCUS WINDOW
        switch_prefixes = [
            "switch to ", "switch window to ", "bring to front ", "bring to the front ",
            "focus on ", "focus ", "go to window ", "go to "
        ]
        for sp in switch_prefixes:
            if cmd.startswith(sp):
                target = cmd[len(sp):].replace("window", "").replace("app", "").strip()
                # Don't hijack system controls like wifi, bluetooth, light mode, dark mode
                if target and not any(bad in target for bad in ["dark", "light", "theme", "wifi", "bluetooth", "mode", "desktop"]):
                    return self.switch_to(target)

        return False, ""


# Global singleton
WINDOW_MGR = WindowManagerDriver()
