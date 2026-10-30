"""
HYDRA Phase 6: Visual Screen Understanding & Computer Vision Engine
High-fidelity desktop perception, screen interrogation, OCR element detection,
and hardware-assisted visual target clicking across third-party applications.
"""

import os
import sys
import time
import re
import difflib
import ctypes
from ctypes import wintypes
from typing import Tuple, Optional, Dict, Any, List
user32 = ctypes.windll.user32
user32.OpenWindowStationW.restype = wintypes.HANDLE
user32.OpenWindowStationW.argtypes = [wintypes.LPCWSTR, wintypes.BOOL, wintypes.DWORD]
user32.SetProcessWindowStation.restype = wintypes.BOOL
user32.SetProcessWindowStation.argtypes = [wintypes.HANDLE]
user32.OpenDesktopW.restype = wintypes.HANDLE
user32.OpenDesktopW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
user32.SetThreadDesktop.restype = wintypes.BOOL
user32.SetThreadDesktop.argtypes = [wintypes.HANDLE]
user32.GetDC.restype = wintypes.HDC
user32.GetDC.argtypes = [wintypes.HWND]
user32.ReleaseDC.restype = ctypes.c_int
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]

gdi32 = ctypes.windll.gdi32
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.DeleteObject.restype = wintypes.BOOL
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteDC.restype = wintypes.BOOL
gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.BitBlt.restype = wintypes.BOOL
gdi32.BitBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD]
gdi32.GetDIBits.restype = ctypes.c_int
gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT, ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]

def _init_desktop_session():
    try:
        hwinsta = user32.OpenWindowStationW('WinSta0', False, 0x037F)
        if hwinsta:
            user32.SetProcessWindowStation(hwinsta)
        hdesk = user32.OpenDesktopW('Default', 0, False, 0x01FF)
        if hdesk:
            user32.SetThreadDesktop(hdesk)
    except Exception as e:
        print(f"[VISION] Desktop attachment warning: {e}")

# Initialize session immediately before any UI/display imports
_init_desktop_session()

from PIL import Image, ImageGrab

# Try loading native Windows OCR (winocr)
try:
    import winocr
    WINOCR_AVAILABLE = True
except ImportError:
    WINOCR_AVAILABLE = False

# Try loading uiautomation
try:
    import uiautomation as auto
    UIA_AVAILABLE = True
except ImportError:
    UIA_AVAILABLE = False

# Try loading OpenCV
try:
    import cv2
    import numpy as np
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False

# Try loading PyAutoGUI
try:
    import pyautogui
    PYAUTOGUI_AVAILABLE = True
except ImportError:
    PYAUTOGUI_AVAILABLE = False

user32 = ctypes.windll.user32


class VisionEngine:
    def __init__(self):
        self._ensure_desktop_access()
        self.last_capture_path = os.path.abspath("current_screen.png")
        self.last_ocr_data = None
        self.last_capture_time = 0.0

    def _ensure_desktop_access(self):
        """Attaches background worker threads to the interactive user desktop session."""
        _init_desktop_session()

    # =========================================================================
    # 1. SCREEN CAPTURE & DESKTOP PERCEPTION
    # =========================================================================
    def capture_screen(self, save_path: Optional[str] = None, region: Optional[Tuple[int, int, int, int]] = None) -> Image.Image:
        """
        Captures a high-resolution screenshot of the native workspace.
        Supports full desktop or specific bounding box regions (left, top, right, bottom).
        Uses ImageGrab with high-speed, robust Win32 GDI fallback.
        """
        self._ensure_desktop_access()
        target_path = save_path or self.last_capture_path
        
        img = None
        # Priority 1: Direct Win32 GDI bitblt with CAPTUREBLT (captures DWM/hardware windows cleanly)
        try:
            img = self._gdi_grab_screen(region=region)
        except Exception as e:
            print(f"[VISION] GDI capture warning: {e}")

        # Priority 2: PIL ImageGrab fallback
        if img is None:
            try:
                if region:
                    img = ImageGrab.grab(bbox=region)
                else:
                    img = ImageGrab.grab()
            except Exception:
                pass

        # Priority 3: PyAutoGUI screenshot
        if img is None and PYAUTOGUI_AVAILABLE:
            try:
                img = pyautogui.screenshot(region=region)
            except Exception:
                pass

        # Priority 4: Active / Top Open Window PrintWindow fallback
        if img is None:
            try:
                import win32gui, win32ui
                hwnd = user32.GetForegroundWindow()
                if not hwnd or not user32.IsWindow(hwnd):
                    from window_ops import WINDOW_MGR
                    wins = WINDOW_MGR.get_open_windows()
                    if wins:
                        hwnd = wins[0]["hwnd"]
                if hwnd and user32.IsWindow(hwnd):
                    r = win32gui.GetWindowRect(hwnd)
                    w, h = max(1, r[2] - r[0]), max(1, r[3] - r[1])
                    hwndDC = win32gui.GetWindowDC(hwnd)
                    mfcDC = win32ui.CreateDCFromHandle(hwndDC)
                    saveDC = mfcDC.CreateCompatibleDC()
                    saveBitMap = win32ui.CreateBitmap()
                    saveBitMap.CreateCompatibleBitmap(mfcDC, w, h)
                    saveDC.SelectObject(saveBitMap)
                    res = user32.PrintWindow(hwnd, saveDC.GetSafeHdc(), 2)
                    if res:
                        bmpinfo = saveBitMap.GetInfo()
                        bmpstr = saveBitMap.GetBitmapBits(True)
                        img = Image.frombuffer('RGB', (bmpinfo['bmWidth'], bmpinfo['bmHeight']), bmpstr, 'raw', 'BGRX', 0, 1)
                    win32gui.DeleteObject(saveBitMap.GetHandle())
                    saveDC.DeleteDC()
                    mfcDC.DeleteDC()
                    win32gui.ReleaseDC(hwnd, hwndDC)
            except Exception:
                pass

        if img is None:
            if os.path.exists(target_path):
                try:
                    img = Image.open(target_path)
                except Exception:
                    pass
            if img is None:
                img = Image.new('RGB', (1707, 960), color=(30, 30, 30))

        if img is not None:
            try:
                img.save(target_path)
            except Exception:
                pass
            self.last_capture_time = time.time()
            return img

        return Image.new('RGB', (1707, 960), color=(30, 30, 30))

    def _gdi_grab_screen(self, region: Optional[Tuple[int, int, int, int]] = None) -> Image.Image:
        """High-performance Win32 GDI screen grab that bypasses virtual desktop handle locks."""
        gdi32 = ctypes.windll.gdi32
        hdc_screen = user32.GetDC(0)
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass

        screen_w = user32.GetSystemMetrics(0)
        screen_h = user32.GetSystemMetrics(1)

        if region:
            rx, ry, r_right, r_bottom = region
            w = max(1, r_right - rx)
            h = max(1, r_bottom - ry)
            src_x, src_y = rx, ry
        else:
            w, h = screen_w, screen_h
            src_x, src_y = 0, 0

        hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
        hbmp = gdi32.CreateCompatibleBitmap(hdc_screen, w, h)
        old_bmp = gdi32.SelectObject(hdc_mem, hbmp)

        # 0x00CC0020 = SRCCOPY, 0x40000000 = CAPTUREBLT
        res = gdi32.BitBlt(hdc_mem, 0, 0, w, h, hdc_screen, src_x, src_y, 0x00CC0020 | 0x40000000)
        if not res:
            res = gdi32.BitBlt(hdc_mem, 0, 0, w, h, hdc_screen, src_x, src_y, 0x00CC0020)

        if not res:
            gdi32.SelectObject(hdc_mem, old_bmp)
            gdi32.DeleteObject(hbmp)
            gdi32.DeleteDC(hdc_mem)
            user32.ReleaseDC(0, hdc_screen)
            raise RuntimeError("GDI BitBlt failed to capture desktop screen.")

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [
                ('biSize', wintypes.DWORD),
                ('biWidth', wintypes.LONG),
                ('biHeight', wintypes.LONG),
                ('biPlanes', wintypes.WORD),
                ('biBitCount', wintypes.WORD),
                ('biCompression', wintypes.DWORD),
                ('biSizeImage', wintypes.DWORD),
                ('biXPelsPerMeter', wintypes.LONG),
                ('biYPelsPerMeter', wintypes.LONG),
                ('biClrUsed', wintypes.DWORD),
                ('biClrImportant', wintypes.DWORD)
            ]

        bmi = BITMAPINFOHEADER()
        bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.biWidth = w
        bmi.biHeight = -h  # top-down DIB
        bmi.biPlanes = 1
        bmi.biBitCount = 32
        bmi.biCompression = 0  # BI_RGB

        buffer = ctypes.create_string_buffer(w * h * 4)
        dib_res = gdi32.GetDIBits(hdc_mem, hbmp, 0, h, buffer, ctypes.byref(bmi), 0)

        gdi32.SelectObject(hdc_mem, old_bmp)
        gdi32.DeleteObject(hbmp)
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(0, hdc_screen)

        if not dib_res:
            raise RuntimeError("GetDIBits failed to copy bitmap buffer.")

        raw_img = Image.frombuffer('RGBA', (w, h), buffer, 'raw', 'BGRA', 0, 1)
        return raw_img.convert('RGB')

    # =========================================================================
    # 2. OCR & TEXT ELEMENT RECOGNITION
    # =========================================================================
    def scan_screen_text(self, img: Optional[Image.Image] = None, force_refresh: bool = False) -> Dict[str, Any]:
        """
        Performs high-speed native Windows OCR (<100ms) across the screen image.
        Returns full text, segmented lines, and coordinate-indexed word list.
        """
        now = time.time()
        if not force_refresh and self.last_ocr_data and (now - self.last_capture_time < 1.0) and img is None:
            return self.last_ocr_data

        if img is None:
            img = self.capture_screen()

        if not WINOCR_AVAILABLE:
            return {"text": "", "lines": [], "words": []}

        try:
            raw_ocr = winocr.recognize_pil_sync(img)
            self.last_ocr_data = raw_ocr
            return raw_ocr
        except Exception as e:
            print(f"[VISION] OCR scan failed: {e}")
            return {"text": "", "lines": [], "words": []}

    def find_text_element(self, query: str, img: Optional[Image.Image] = None) -> Optional[Dict[str, Any]]:
        """
        Locates the exact screen coordinates and bounding center of a spoken word or phrase.
        Handles exact phrases, substring matches, and fuzzy token alignment.
        """
        clean_query = query.lower().strip()
        if not clean_query:
            return None

        # Strip action prefixes/suffixes for clean OCR matching
        for prefix in ["click on the ", "click on ", "click the ", "click ", "press the ", "press ", "the ", "button ", "icon "]:
            if clean_query.startswith(prefix):
                clean_query = clean_query[len(prefix):].strip()
        for suffix in [" button", " icon", " tab", " link", " option", " field", " bar"]:
            if clean_query.endswith(suffix):
                clean_query = clean_query[:-len(suffix)].strip()

        q_tokens = [re.sub(r'^\W+|\W+$', '', t) for t in clean_query.split() if re.sub(r'^\W+|\W+$', '', t)]
        if not q_tokens:
            return None

        ocr_data = self.scan_screen_text(img=img)
        lines = ocr_data.get("lines", [])

        # Priority 1: Multi-word strict token sequence match
        if len(q_tokens) > 1:
            k = len(q_tokens)
            for line in lines:
                words = line.get("words", [])
                w_cleans = [re.sub(r'^\W+|\W+$', '', w.get("text", "").lower()) for w in words]
                for i in range(len(w_cleans) - k + 1):
                    slice_w = w_cleans[i:i+k]
                    if slice_w == q_tokens:
                        matched = words[i:i+k]
                        return self._compute_bounds_from_words(matched, query, " ".join(w["text"] for w in matched))

        # Priority 2: Exact single-word match (avoids picking random substring words to the left)
        for line in lines:
            words = line.get("words", [])
            w_cleans = [re.sub(r'^\W+|\W+$', '', w.get("text", "").lower()) for w in words]
            for i, wc in enumerate(w_cleans):
                for qt in q_tokens:
                    if wc == qt:
                        target_word = words[i]
                        return self._compute_bounds_from_words([target_word], query, target_word.get("text", ""))

        # Priority 3: Stem / Prefix match (e.g. export vs exporting, minimum length 4)
        for line in lines:
            words = line.get("words", [])
            w_cleans = [re.sub(r'^\W+|\W+$', '', w.get("text", "").lower()) for w in words]
            for i, wc in enumerate(w_cleans):
                for qt in q_tokens:
                    if len(qt) >= 4 and (wc.startswith(qt) or (len(wc) >= 4 and qt.startswith(wc))):
                        target_word = words[i]
                        return self._compute_bounds_from_words([target_word], query, target_word.get("text", ""))

        # Priority 4: Fuzzy sequence match
        for line in lines:
            line_text = line.get("text", "").strip()
            if not line_text:
                continue
            ratio = difflib.SequenceMatcher(None, clean_query, line_text.lower()).ratio()
            if ratio >= 0.75:
                words = line.get("words", [])
                if words:
                    return self._compute_bounds_from_words(words, query, line_text)

        return None

    def _compute_bounds_from_words(self, words: List[Dict[str, Any]], query: str, matched_text: str) -> Dict[str, Any]:
        """Calculates precise bounding box and center coordinate from a list of OCR word objects."""
        min_x = min(w["bounding_rect"]["x"] for w in words)
        min_y = min(w["bounding_rect"]["y"] for w in words)
        max_x = max(w["bounding_rect"]["x"] + w["bounding_rect"]["width"] for w in words)
        max_y = max(w["bounding_rect"]["y"] + w["bounding_rect"]["height"] for w in words)
        
        center_x = int((min_x + max_x) / 2)
        center_y = int((min_y + max_y) / 2)
        
        return {
            "found": True,
            "target": query,
            "matched_text": matched_text,
            "center": (center_x, center_y),
            "rect": (int(min_x), int(min_y), int(max_x - min_x), int(max_y - min_y)),
            "source": "ocr"
        }

    # =========================================================================
    # 3. UI AUTOMATION BACKED TARGET FINDER (HYBRID FALLBACK)
    # =========================================================================
    def find_uia_element(self, query: str, control_type: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Searches native Windows UI Automation tree for buttons, tabs, links, and inputs.
        Guarded against small false-positive substring matches.
        """
        if not UIA_AVAILABLE:
            return None
            
        clean = query.lower().strip()
        for p in ["the ", "button ", "icon ", "tab ", "link "]:
            if clean.startswith(p): clean = clean[len(p):].strip()
        for s in [" button", " icon", " tab", " link"]:
            if clean.endswith(s): clean = clean[:-len(s)].strip()

        if len(clean) < 2:
            return None

        try:
            self._ensure_desktop_access()
            root = auto.GetRootControl()
            if not root:
                return None
                
            for c, _ in auto.WalkTree(root, maxDepth=4):
                name = c.Name.strip() if c.Name else ""
                if not name:
                    continue
                name_low = name.lower()
                # Strict matching: exact or substantial substring (at least 4 chars)
                if clean == name_low or (len(clean) >= 4 and clean in name_low):
                    rect = c.BoundingRectangle
                    if rect.width() > 0 and rect.height() > 0:
                        screen_h = user32.GetSystemMetrics(1)
                        if not any(k in clean for k in ["taskbar", "task bar", "start"]) and rect.top >= (screen_h - 65):
                            continue
                        cx = int((rect.left + rect.right) / 2)
                        cy = int((rect.top + rect.bottom) / 2)
                        return {
                            "found": True,
                            "target": query,
                            "matched_text": name,
                            "center": (cx, cy),
                            "rect": (rect.left, rect.top, rect.width(), rect.height()),
                            "control_type": c.ControlTypeName,
                            "source": "uia"
                        }
        except Exception as e:
            print(f"[VISION] UIA search warning: {e}")
        return None

    def get_screenshots_dir(self) -> str:
        """Returns the standard Windows Pictures\\Screenshots folder."""
        user_profile = os.environ.get('USERPROFILE', os.path.expanduser('~'))
        shots_dir = os.path.join(user_profile, 'Pictures', 'Screenshots')
        if not os.path.exists(shots_dir):
            try:
                os.makedirs(shots_dir, exist_ok=True)
            except Exception:
                shots_dir = os.path.join(user_profile, 'Pictures')
        return shots_dir

    def detect_colored_shape(self, query: str, img: Optional[Image.Image] = None) -> Optional[Dict[str, Any]]:
        """
        Detects colored geometric shapes (e.g. 'green circle', 'orange circle', 'blue button', 'red dot')
        using HSV color masks, morphological closing to fill interior icons, and convex hull circularity.
        """
        if not CV2_AVAILABLE:
            return None
        clean = query.lower().strip()

        # Identify target color
        color_name = None
        for c in ["green", "orange", "amber", "blue", "cyan", "red", "yellow", "purple", "violet", "white", "black"]:
            if c in clean:
                color_name = c
                break
        if not color_name:
            return None

        # Identify target shape
        shape_name = "circle"
        if any(s in clean for s in ["square", "box", "rectangle"]):
            shape_name = "square"
        elif any(s in clean for s in ["triangle", "arrow"]):
            shape_name = "triangle"
        elif any(s in clean for s in ["circle", "round", "dot", "disc", "ball", "sphere", "bubble", "button", "icon"]):
            shape_name = "circle"

        if img is None:
            img = self.capture_screen()
        img_np = np.array(img)
        img_bgr = cv2.cvtColor(img_np, cv2.COLOR_RGB2BGR)
        h, w, _ = img_bgr.shape
        hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)

        # Spatial region restriction if user gave directional cues
        rx1, ry1, rx2, ry2 = 0, 0, w, h
        if any(k in clean for k in ["bottom left", "bottom-left", "lower left"]):
            rx1, ry1, rx2, ry2 = 0, int(h * 0.5), int(w * 0.45), h
        elif any(k in clean for k in ["bottom right", "bottom-right", "lower right"]):
            rx1, ry1, rx2, ry2 = int(w * 0.55), int(h * 0.5), w, h
        elif any(k in clean for k in ["top left", "top-left", "upper left"]):
            rx1, ry1, rx2, ry2 = 0, 0, int(w * 0.45), int(h * 0.5)
        elif any(k in clean for k in ["top right", "top-right", "upper right"]):
            rx1, ry1, rx2, ry2 = int(w * 0.55), 0, w, int(h * 0.5)
        elif any(k in clean for k in ["bottom", "taskbar"]):
            rx1, ry1, rx2, ry2 = 0, int(h * 0.65), w, h
        elif "top" in clean:
            rx1, ry1, rx2, ry2 = 0, 0, w, int(h * 0.35)
        elif "left" in clean:
            rx1, ry1, rx2, ry2 = 0, 0, int(w * 0.4), h
        elif "right" in clean:
            rx1, ry1, rx2, ry2 = int(w * 0.6), 0, w, h
        elif any(k in clean for k in ["center", "middle"]):
            rx1, ry1, rx2, ry2 = int(w * 0.2), int(h * 0.2), int(w * 0.8), int(h * 0.8)

        roi_hsv = hsv[ry1:ry2, rx1:rx2]

        mask = None
        if color_name in ["green", "emerald"]:
            # Spotify & UI vibrant neon green: H 40-85, S 90-255, V 90-255
            mask = cv2.inRange(roi_hsv, np.array([40, 90, 90]), np.array([85, 255, 255]))
        elif color_name in ["orange", "amber"]:
            mask = cv2.inRange(roi_hsv, np.array([8, 120, 120]), np.array([28, 255, 255]))
        elif color_name in ["blue", "cyan"]:
            mask = cv2.inRange(roi_hsv, np.array([85, 100, 100]), np.array([135, 255, 255]))
        elif color_name in ["red", "crimson"]:
            m1 = cv2.inRange(roi_hsv, np.array([0, 120, 120]), np.array([8, 255, 255]))
            m2 = cv2.inRange(roi_hsv, np.array([170, 120, 120]), np.array([180, 255, 255]))
            mask = cv2.bitwise_or(m1, m2)
        elif color_name == "yellow":
            mask = cv2.inRange(roi_hsv, np.array([24, 100, 100]), np.array([36, 255, 255]))
        elif color_name in ["purple", "violet"]:
            mask = cv2.inRange(roi_hsv, np.array([135, 80, 80]), np.array([165, 255, 255]))
        elif color_name == "white":
            mask = cv2.inRange(roi_hsv, np.array([0, 0, 205]), np.array([180, 40, 255]))
        elif color_name == "black":
            mask = cv2.inRange(roi_hsv, np.array([0, 0, 0]), np.array([180, 255, 45]))

        if mask is None:
            return None

        # Morphological close to seal interior cutouts/icons (e.g. black play triangle inside green button)
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15))
        closed = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        cnts, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        candidates = []
        for c in cnts:
            hull = cv2.convexHull(c)
            area = cv2.contourArea(hull)
            if area < 60:
                continue
            peri = cv2.arcLength(hull, True)
            circ = (4 * np.pi * area / (peri * peri)) if peri > 0 else 0
            (cx_rel, cy_rel), radius = cv2.minEnclosingCircle(hull)
            circle_area = np.pi * (radius ** 2)
            fill = area / circle_area if circle_area > 0 else 0

            cx = rx1 + int(cx_rel)
            cy = ry1 + int(cy_rel)

            if shape_name == "circle":
                if circ > 0.55 and fill > 0.55:
                    score = area * circ * fill
                    candidates.append({
                        "found": True,
                        "target": query,
                        "matched_text": f"{color_name} circle",
                        "center": (cx, cy),
                        "rect": (int(cx - radius), int(cy - radius), int(radius * 2), int(radius * 2)),
                        "score": score,
                        "source": "opencv_colored_circle"
                    })
            else:
                candidates.append({
                    "found": True,
                    "target": query,
                    "matched_text": f"{color_name} {shape_name}",
                    "center": (cx, cy),
                    "rect": (int(cx - 25), int(cy - 25), 50, 50),
                    "score": area,
                    "source": "opencv_colored_shape"
                })

        if candidates:
            candidates.sort(key=lambda x: x["score"], reverse=True)
            return candidates[0]

        return None

    def detect_play_button_visual(self, img: Optional[Image.Image] = None) -> Optional[Dict[str, Any]]:
        """
        Detects primary and secondary play/pause buttons visually using OpenCV:
        1. Vibrant Spotify/media green circular button (with triangle cutout).
        2. High-contrast player bar circular button (white circle with black play/pause).
        3. Standalone right-pointing play triangle ▶ geometry.
        """
        if not CV2_AVAILABLE:
            return None

        if img is None:
            img = self.capture_screen()
        img_np = np.array(img)
        img_bgr = cv2.cvtColor(img_np, cv2.COLOR_RGB2BGR)
        h, w, _ = img_bgr.shape
        hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)

        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15))

        # 1. Vibrant Green Play Button (Spotify, Media apps)
        mask_green = cv2.inRange(hsv, np.array([45, 110, 110]), np.array([85, 255, 255]))
        closed_green = cv2.morphologyEx(mask_green, cv2.MORPH_CLOSE, kernel)
        cnts_g, _ = cv2.findContours(closed_green, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        green_cands = []
        for c in cnts_g:
            hull = cv2.convexHull(c)
            area = cv2.contourArea(hull)
            if area < 100:
                continue
            peri = cv2.arcLength(hull, True)
            circ = (4 * np.pi * area / (peri * peri)) if peri > 0 else 0
            (cx, cy), radius = cv2.minEnclosingCircle(hull)
            fill = area / (np.pi * (radius ** 2)) if radius > 0 else 0
            if circ > 0.60 and fill > 0.60 and 12 <= radius <= 60:
                score = area * circ * fill
                green_cands.append({
                    "found": True,
                    "target": "play button",
                    "matched_text": "play button",
                    "center": (int(cx), int(cy)),
                    "rect": (int(cx - radius), int(cy - radius), int(radius * 2), int(radius * 2)),
                    "score": score,
                    "source": "spotify_green_play_button"
                })

        if green_cands:
            green_cands.sort(key=lambda x: x["score"], reverse=True)
            return green_cands[0]

        # 2. Player Bar White Circular Button (e.g. Spotify bottom player bar, YouTube)
        # Search bottom 25% or middle of screen
        bottom_y1 = int(h * 0.75)
        roi_gray = gray[bottom_y1:h, 0:w]
        _, thresh_white = cv2.threshold(roi_gray, 215, 255, cv2.THRESH_BINARY)
        closed_w = cv2.morphologyEx(thresh_white, cv2.MORPH_CLOSE, kernel)
        cnts_w, _ = cv2.findContours(closed_w, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        white_cands = []
        for c in cnts_w:
            hull = cv2.convexHull(c)
            area = cv2.contourArea(hull)
            if area < 80:
                continue
            peri = cv2.arcLength(hull, True)
            circ = (4 * np.pi * area / (peri * peri)) if peri > 0 else 0
            (cx_rel, cy_rel), radius = cv2.minEnclosingCircle(hull)
            fill = area / (np.pi * (radius ** 2)) if radius > 0 else 0
            if circ > 0.65 and fill > 0.65 and 10 <= radius <= 45:
                cx = int(cx_rel)
                cy = bottom_y1 + int(cy_rel)
                white_cands.append({
                    "found": True,
                    "target": "play button",
                    "matched_text": "play button",
                    "center": (cx, cy),
                    "rect": (int(cx - radius), int(cy - radius), int(radius * 2), int(radius * 2)),
                    "score": area * circ,
                    "source": "bottom_bar_play_button"
                })

        if white_cands:
            # Pick the one closest to horizontal center
            white_cands.sort(key=lambda x: abs(x["center"][0] - w // 2))
            return white_cands[0]

        # 3. Geometric Right-Pointing Play Triangle ▶
        edges = cv2.Canny(gray, 50, 150)
        cnts_tri, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        for c in cnts_tri:
            area = cv2.contourArea(c)
            if area < 40 or area > 3000:
                continue
            peri = cv2.arcLength(c, True)
            approx = cv2.approxPolyDP(c, 0.08 * peri, True)
            if len(approx) == 3:
                pts = approx.reshape(3, 2)
                # Check rightward pointing
                xs = pts[:, 0]
                ys = pts[:, 1]
                rightmost_idx = np.argmax(xs)
                other_indices = [i for i in range(3) if i != rightmost_idx]
                x_right = xs[rightmost_idx]
                x_base_avg = (xs[other_indices[0]] + xs[other_indices[1]]) / 2.0
                if x_right > x_base_avg + 4: # points clearly rightward
                    cx = int(np.mean(xs))
                    cy = int(np.mean(ys))
                    return {
                        "found": True,
                        "target": "play button",
                        "matched_text": "play button",
                        "center": (cx, cy),
                        "rect": (int(cx - 15), int(cy - 15), 30, 30),
                        "source": "play_triangle_contour"
                    }

        return None

    def find_semantic_target(self, query: str, img: Optional[Image.Image] = None) -> Optional[Dict[str, Any]]:
        """
        Locates semantic visual elements (e.g., 'any person', 'play button', 'face', 'video player')
        using local Computer Vision (OpenCV Cascades, Skin-Tone Clustering, Shape Analysis) and Cloud AI.
        """
        clean = query.lower().strip()

        # 1. PERSON / FACE / HUMAN / CHARACTER / SOMEONE / PROFILE / AVATAR DETECTION
        if any(w in clean for w in ["person", "face", "human", "actor", "character", "someone", "man", "woman", "guy", "girl", "profile", "avatar"]):
            if CV2_AVAILABLE:
                if img is None:
                    img = self.capture_screen()
                img_np = np.array(img)
                img_bgr = cv2.cvtColor(img_np, cv2.COLOR_RGB2BGR)
                gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
                h, w, _ = img_bgr.shape

                # A. Multi-Cascade Face Detection (Frontal + Alt2 + Profile)
                cascade_files = [
                    cv2.data.haarcascades + 'haarcascade_frontalface_alt2.xml',
                    cv2.data.haarcascades + 'haarcascade_frontalface_default.xml',
                    cv2.data.haarcascades + 'haarcascade_profileface.xml'
                ]
                for c_file in cascade_files:
                    try:
                        cascade = cv2.CascadeClassifier(c_file)
                        faces = cascade.detectMultiScale(gray, scaleFactor=1.06, minNeighbors=2, minSize=(20, 20))
                        if len(faces) > 0:
                            faces = sorted(faces, key=lambda f: f[2] * f[3], reverse=True)
                            fx, fy, fw, fh = faces[0]
                            return {
                                "found": True,
                                "target": query,
                                "matched_text": "person on screen",
                                "center": (int(fx + fw // 2), int(fy + fh // 2)),
                                "rect": (int(fx), int(fy), int(fw), int(fh)),
                                "source": "opencv_face"
                            }
                    except Exception:
                        pass

                # B. Universal Human Skin-Tone & Portrait Cluster Detector (YCrCb: Cr 133-173, Cb 77-127)
                try:
                    ycrcb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2YCR_CB)
                    skin_mask = cv2.inRange(ycrcb, np.array([0, 133, 77]), np.array([255, 173, 127]))
                    kernel_skin = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
                    skin_mask = cv2.morphologyEx(skin_mask, cv2.MORPH_CLOSE, kernel_skin)
                    cnts_s, _ = cv2.findContours(skin_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                    portrait_cands = []
                    for cs in cnts_s:
                        area = cv2.contourArea(cs)
                        if area < 300:
                            continue
                        bx, by, bw, bh = cv2.boundingRect(cs)
                        aspect = float(bw) / bh
                        if 0.45 < aspect < 1.8 and bh >= 25 and bw >= 25:
                            # Verify skin pixel density within bounding box
                            roi_skin = skin_mask[by:by+bh, bx:bx+bw]
                            skin_ratio = np.count_nonzero(roi_skin) / float(bw * bh)
                            if skin_ratio > 0.20:
                                portrait_cands.append({
                                    "center": (bx + bw // 2, by + bh // 2),
                                    "rect": (bx, by, bw, bh),
                                    "score": area * skin_ratio
                                })
                    if portrait_cands:
                        portrait_cands.sort(key=lambda x: x["score"], reverse=True)
                        best_p = portrait_cands[0]
                        return {
                            "found": True,
                            "target": query,
                            "matched_text": "person on screen",
                            "center": best_p["center"],
                            "rect": best_p["rect"],
                            "source": "skin_portrait_cluster"
                        }
                except Exception as e:
                    print(f"[VISION] Skin detector note: {e}")

                # C. Circular Profile Avatar / Thumbnail Detection (Top bar / User Avatars)
                try:
                    # Circular avatars at top bar or header
                    top_roi_gray = gray[0:int(h * 0.35), 0:w]
                    circles = cv2.HoughCircles(top_roi_gray, cv2.HOUGH_GRADIENT, dp=1.2, minDist=30,
                                               param1=100, param2=30, minRadius=14, maxRadius=45)
                    if circles is not None:
                        circles = np.uint16(np.around(circles))
                        # Pick circular avatar closest to top-right (standard user profile placement)
                        c_list = sorted(circles[0, :], key=lambda c: (w - c[0]) + c[1])
                        best_c = c_list[0]
                        cx, cy, r = int(best_c[0]), int(best_c[1]), int(best_c[2])
                        return {
                            "found": True,
                            "target": query,
                            "matched_text": "profile avatar",
                            "center": (cx, cy),
                            "rect": (cx - r, cy - r, r * 2, r * 2),
                            "source": "circular_avatar"
                        }
                except Exception:
                    pass

                # D. HOG People Detector Fallback
                try:
                    hog = cv2.HOGDescriptor()
                    hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
                    small = cv2.resize(gray, (1280, 720))
                    boxes, weights = hog.detectMultiScale(small, winStride=(8, 8), padding=(4, 4), scale=1.05)
                    if len(boxes) > 0:
                        scale_x = img.size[0] / 1280.0
                        scale_y = img.size[1] / 720.0
                        bx, by, bw, bh = boxes[0]
                        cx = int((bx + bw / 2) * scale_x)
                        cy = int((by + bh / 2) * scale_y)
                        return {
                            "found": True,
                            "target": query,
                            "matched_text": "person on screen",
                            "center": (cx, cy),
                            "rect": (int(bx * scale_x), int(by * scale_y), int(bw * scale_x), int(bh * scale_y)),
                            "source": "opencv_hog"
                        }
                except Exception:
                    pass

        # 2. PLAY / PAUSE / VIDEO PLAYER DETECTION
        if any(w in clean for w in ["play button", "play icon", "pause button", "pause icon", "play", "pause", "video player", "video", "resume"]):
            # A. Visual Play Icon / Circular Button Detector (Spotify green button, white bottom bar button, triangle ▶)
            visual_play = self.detect_play_button_visual(img=img)
            if visual_play:
                return visual_play

            # B. Check OCR for text "play", "pause", "resume", "watch", "▶", "⏸"
            for t in ["play", "▶", "pause", "resume", "watch"]:
                m = self.find_text_element(t, img=img)
                if m:
                    m["matched_text"] = "play button"
                    return m

            # C. Foreground Media Window Active Canvas Center
            try:
                from window_ops import WINDOW_MGR
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
                    app_name = active_win.get("app_name", "").lower()
                    title = active_win.get("title", "").lower()

                    if any(m in title or m in app_name for m in ["netmirror", "youtube", "netflix", "spotify", "vlc", "media", "video", "brave", "chrome"]) and win_w > 400 and win_h > 300:
                        if "spotify" in app_name or "spotify" in title:
                            cx = rect.left + win_w // 2
                            cy = rect.bottom - 55
                            return {
                                "found": True,
                                "target": query,
                                "matched_text": "play button",
                                "center": (int(cx), int(cy)),
                                "rect": (int(cx - 25), int(cy - 25), 50, 50),
                                "source": "spotify_player"
                            }
                        else:
                            cx = rect.left + win_w // 2
                            cy = rect.top + win_h // 2
                            return {
                                "found": True,
                                "target": query,
                                "matched_text": "play button",
                                "center": (int(cx), int(cy)),
                                "rect": (int(cx - 50), int(cy - 50), 100, 100),
                                "source": "video_canvas"
                            }
            except Exception as e:
                print(f"[VISION] Media window detection note: {e}")

        # 3. SEARCH BAR / SEARCH ICON / SEARCH BOX
        if any(w in clean for w in ["search bar", "search box", "search input", "search field", "search icon", "search button", "search tab", "magnifying glass", "search"]):
            for st in ["search", "what do you want to play", "what do you want to listen", "type here to search", "type here"]:
                m = self.find_text_element(st, img=img)
                if m:
                    m["matched_text"] = "search icon" if "icon" in clean else "search bar"
                    return m
            try:
                from window_ops import WINDOW_MGR
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
                        return {
                            "found": True,
                            "target": query,
                            "matched_text": "search icon",
                            "center": (int(cx), int(cy)),
                            "rect": (int(cx - 120), int(cy - 15), 240, 30),
                            "source": "spotify_search_bar"
                        }
                    elif any(b in app_name or b in title for b in ["brave", "chrome", "edge", "firefox"]):
                        cx = rect.left + win_w // 2
                        cy = rect.top + 80
                        return {
                            "found": True,
                            "target": query,
                            "matched_text": "search bar",
                            "center": (int(cx), int(cy)),
                            "rect": (int(cx - 150), int(cy - 15), 300, 30),
                            "source": "browser_address_bar"
                        }
                    elif "explorer" in app_name or "folder" in app_name or "explorer" in title:
                        cx = rect.right - 140
                        cy = rect.top + 55
                        return {
                            "found": True,
                            "target": query,
                            "matched_text": "search box",
                            "center": (int(cx), int(cy)),
                            "rect": (int(cx - 70), int(cy - 15), 140, 30),
                            "source": "explorer_search_bar"
                        }
                    elif win_w > 300 and win_h > 200:
                        cx = rect.left + min(350, win_w // 2)
                        cy = rect.top + 45
                        return {
                            "found": True,
                            "target": query,
                            "matched_text": "search bar",
                            "center": (int(cx), int(cy)),
                            "rect": (int(cx - 100), int(cy - 15), 200, 30),
                            "source": "window_top_search"
                        }
            except Exception:
                pass

        # 4. CLOUD AI VISION API (Nebius Vision / OpenAI Vision LLM)
        try:
            from ai_engine import ai_locate_visual_element
            if img is None:
                img = self.capture_screen()
            ai_res = ai_locate_visual_element(img, clean)
            if ai_res and ai_res.get("found"):
                return ai_res
        except Exception as e:
            print(f"[VISION] AI vision fallback note: {e}")

        return None

    def find_element(self, target: str) -> Optional[Dict[str, Any]]:
        """
        Multi-tier element resolver:
        1. High-speed screen OCR (works for text, buttons, links, menus).
        2. Native UI Automation tree (for Win32/UWP controls, taskbar).
        3. Semantic Computer Vision (Play/Pause button, Person/Avatar, Search bar).
        4. Colored Shape Detector (Green circle, Orange dot, Blue button, etc.).
        5. Cloud AI Vision API (Nebius Vision / OpenAI Vision LLMs).
        """
        clean = target.lower().strip()
        for prefix in ["the ", "button ", "icon ", "tab ", "link ", "option ", "field "]:
            if clean.startswith(prefix):
                clean = clean[len(prefix):].strip()
        for suffix in [" button", " icon", " tab", " link", " option", " field", " bar"]:
            if clean.endswith(suffix):
                clean = clean[:-len(suffix)].strip()

        # Tier 1: Screen OCR Search
        ocr_match = self.find_text_element(clean)
        if ocr_match:
            return ocr_match

        # Tier 2: UIA Search Fallback
        uia_match = self.find_uia_element(clean)
        if uia_match:
            return uia_match

        # Tier 3: Semantic Computer Vision (Play Button, Person, Search Bar)
        semantic_match = self.find_semantic_target(clean)
        if semantic_match:
            return semantic_match

        # Tier 4: Colored Shape Recognition (e.g. "green circle", "orange circle", "blue button")
        shape_match = self.detect_colored_shape(clean)
        if shape_match:
            return shape_match

        return None

    # =========================================================================
    # 4. VISUAL TARGET CLICKING & HARDWARE INTERACTION
    # =========================================================================
    def click_visual_target(self, target: str, double_click: bool = False, right_click: bool = False) -> Tuple[bool, str]:
        """
        Finds target element on screen and performs smooth hardware click at target center.
        Guarantees zero coordinate shift via native Win32 SetCursorPos and mouse_event locking.
        """
        element = self.find_element(target)
        if not element:
            return False, f"I scanned your screen, sir, but could not locate '{target}'."

        cx, cy = element["center"]
        matched_text = element.get("matched_text", target)

        try:
            # 1. Smooth glide if PyAutoGUI is present
            if PYAUTOGUI_AVAILABLE:
                try:
                    pyautogui.moveTo(cx, cy, duration=0.18, tween=pyautogui.easeInOutQuad)
                except Exception:
                    pass

            # 2. Hard lock to exact physical screen coordinates (eliminates DPI scaling jumps)
            user32.SetCursorPos(int(cx), int(cy))
            time.sleep(0.04)

            # 3. Direct hardware click at locked position
            down_flag = 0x0002
            up_flag = 0x0004
            if right_click:
                down_flag, up_flag = 0x0008, 0x0010

            clicks_count = 2 if double_click else 1
            for _ in range(clicks_count):
                user32.mouse_event(down_flag, 0, 0, 0, 0)
                time.sleep(0.04)
                user32.mouse_event(up_flag, 0, 0, 0, 0)
                if clicks_count > 1:
                    time.sleep(0.06)

            # Concise, professional, human-like voice response (no coordinate numbers)
            display_label = matched_text.strip() or target.strip()
            if double_click:
                return True, f"Done sir, double-clicked on '{display_label}'."
            elif right_click:
                return True, f"Done sir, right-clicked on '{display_label}'."
            else:
                if any(display_label.lower().startswith(w) for w in ["the ", "a ", "an ", "this "]):
                    return True, f"Done sir, clicked {display_label}."
                elif any(w in display_label.lower() for w in ["button", "icon", "person", "face", "tab", "link", "player", "area", "circle", "avatar"]):
                    return True, f"Done sir, clicked the {display_label}."
                else:
                    return True, f"Done sir, clicked on '{display_label}'."
        except Exception as e:
            return False, f"Visual interaction failed: {e}"

    def type_into_target(self, target: str, text: str) -> Tuple[bool, str]:
        """Clicks an input box or search bar and enters text smoothly."""
        success, reply = self.click_visual_target(target)
        if not success:
            return False, reply

        time.sleep(0.1)
        if PYAUTOGUI_AVAILABLE:
            pyautogui.write(text, interval=0.03)
            return True, f"Done sir, focused '{target}' and entered '{text}'."
        return True, reply

    # =========================================================================
    # 5. SCREEN INTERROGATION & COMPREHENSION
    # =========================================================================
    def describe_screen(self) -> Tuple[bool, str]:
        """
        Provides a smart executive briefing of what the user is currently viewing:
        Identifies active foreground app, open document/tab title, and prominent on-screen elements.
        """
        from window_ops import WINDOW_MGR
        
        # 1. Inspect Foreground Window
        self._ensure_desktop_access()
        fg_hwnd = user32.GetForegroundWindow()
        active_win = None
        open_windows = WINDOW_MGR.get_open_windows()
        
        for w in open_windows:
            if w["hwnd"] == fg_hwnd:
                active_win = w
                break

        # If not matched directly, check title
        if not active_win and open_windows:
            active_win = open_windows[0]

        # 2. Capture and OCR
        img = self.capture_screen()
        ocr_data = self.scan_screen_text(img=img)
        lines = ocr_data.get("lines", [])
        
        # Extract prominent text lines (filtering very short symbols)
        meaningful_lines = []
        for l in lines:
            t = l.get("text", "").strip()
            if len(t) >= 4 and not re.match(r'^[\d\W_]+$', t):
                meaningful_lines.append(t)

        # 3. Compose Contextual Description
        app_name = active_win["app_name"] if active_win else "Desktop"
        title = active_win["title"] if active_win else "Windows Desktop"
        
        # Other background apps
        bg_apps = [w["app_name"] for w in open_windows if w != active_win][:4]
        bg_desc = f" Background applications include {', '.join(bg_apps)}." if bg_apps else ""

        if not meaningful_lines:
            if "Brave" in app_name or "Chrome" in app_name:
                return True, f"You are currently viewing {app_name} with the page '{title}'. It appears to be displaying media or a clean canvas.{bg_desc}"
            return True, f"You are currently focused on {app_name} ('{title}').{bg_desc}"

        sample_elements = meaningful_lines[:5]
        joined_elements = " • ".join(sample_elements)
        
        reply = (
            f"You are currently working in {app_name} on '{title}'. "
            f"Visible items on screen include: {joined_elements}.{bg_desc}"
        )
        return True, reply

    def read_screen_text(self, max_lines: int = 8) -> Tuple[bool, str]:
        """Reads visible text content on screen concisely."""
        img = self.capture_screen()
        ocr_data = self.scan_screen_text(img=img)
        lines = [l.get("text", "").strip() for l in ocr_data.get("lines", []) if l.get("text", "").strip()]
        
        if not lines:
            return True, "No readable text detected on screen, sir."

        # Filter duplicates and clean lines
        cleaned = []
        for l in lines:
            if len(l) >= 3 and l not in cleaned:
                cleaned.append(l)

        summary_lines = cleaned[:max_lines]
        spoken = "Here is what is visible on your screen, sir: " + ". ".join(summary_lines)
        return True, spoken

    # =========================================================================
    # 6. NATURAL LANGUAGE COMMAND ROUTER
    # =========================================================================
    def parse_and_execute(self, command: str) -> Tuple[bool, Optional[str]]:
        """
        Parses natural language commands for screen understanding, element clicking, and reading.
        Returns (handled, spoken_reply).
        """
        raw = command.strip().lower()
        clean = re.sub(r'[^\w\s]', '', raw).strip()

        # --- A. SCREEN INTERROGATION ---
        describe_triggers = [
            "what is on my screen", "whats on my screen", "what is on the screen", "whats on the screen",
            "describe my screen", "describe the screen", "what am i looking at",
            "inspect my screen", "inspect the screen", "look at my screen", "look at the screen",
            "tell me what is on my screen", "tell me what is on the screen",
            "what is currently on my screen", "what is open on my screen",
            "what do you see on my screen", "what can you see on my screen"
        ]
        if any(clean == dt or clean.startswith(dt) for dt in describe_triggers):
            return self.describe_screen()

        # --- B. READ SCREEN TEXT ---
        read_triggers = [
            "read my screen", "read the screen", "read what is on my screen", "read whats on my screen",
            "read what is on the screen", "read whats on the screen", "read visible text",
            "summarize my screen", "summarize the screen", "read text on screen"
        ]
        if any(clean == rt or clean.startswith(rt) for rt in read_triggers):
            return self.read_screen_text()

        # --- C. SCREENSHOT & SCREEN CAPTURE ---
        screenshot_triggers = [
            "take a screenshot", "take screenshot", "capture screen", "capture the screen",
            "screenshot", "grab screen", "save screenshot"
        ]
        if any(clean == st for st in screenshot_triggers):
            try:
                now_str = time.strftime("%Y-%m-%d %H%M%S")
                shots_dir = self.get_screenshots_dir()
                filename = f"Screenshot {now_str}.png"
                full_shot_path = os.path.join(shots_dir, filename)
                img = self.capture_screen(save_path=full_shot_path)
                try:
                    img.save(self.last_capture_path)
                except Exception:
                    pass
                return True, "Screenshot captured and saved to your Screenshots folder, sir."
            except Exception as e:
                return True, f"Failed to capture screen: {e}"

        # --- D. FIND / LOCATE TARGET ON SCREEN ---
        locate_match = re.match(r'^(?:where is|find|locate|point to|point at)\s+(?:the\s+)?(.+?)(?:\s+on\s+(?:the\s+|my\s+)?screen)?$', clean)
        if locate_match:
            target = locate_match.group(1).strip()
            if target and target not in ["mouse", "cursor", "i"]:
                element = self.find_element(target)
                if element:
                    cx, cy = element["center"]
                    if PYAUTOGUI_AVAILABLE:
                        pyautogui.moveTo(cx, cy, duration=0.3, tween=pyautogui.easeInOutQuad)
                    else:
                        user32.SetCursorPos(cx, cy)
                    lbl = element.get("matched_text", target)
                    return True, f"Found '{lbl}' on your screen, moved the mouse to it, sir."
                else:
                    return True, f"I scanned your screen, sir, but could not locate '{target}'."

        # --- E. TYPE INTO TARGET ---
        type_match = re.match(r'^(?:type|write|enter)\s+(.+?)\s+(?:in|into)\s+(?:the\s+)?(.+)$', clean)
        if type_match:
            text_to_type = type_match.group(1).strip()
            target_field = type_match.group(2).strip()
            success, reply = self.type_into_target(target_field, text_to_type)
            return True, reply

        # --- F. DOUBLE CLICK TARGET ---
        if clean.startswith("double click ") or clean.startswith("doubleclick "):
            target = re.sub(r'^(?:double click|doubleclick)\s+(?:on\s+)?(?:the\s+)?', '', clean).strip()
            if target and target not in ["it", "this", "mouse", "button"]:
                success, reply = self.click_visual_target(target, double_click=True)
                return True, reply

        # --- G. RIGHT CLICK TARGET ---
        if clean.startswith("right click ") or clean.startswith("rightclick "):
            target = re.sub(r'^(?:right click|rightclick)\s+(?:on\s+)?(?:the\s+)?', '', clean).strip()
            if target and target not in ["it", "this", "mouse", "button"]:
                success, reply = self.click_visual_target(target, right_click=True)
                return True, reply

        # --- H. CLICK TARGET ---
        KEYBOARD_KEYS = {
            "enter", "return", "tab", "esc", "escape", "space", "spacebar", "backspace", "delete", "del",
            "up", "down", "left", "right", "home", "end", "page up", "page down", "pageup", "pagedown",
            "f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10", "f11", "f12",
            "mute", "vol up", "vol down", "volume up", "volume down", "ctrl", "alt", "shift", "win", "windows"
        }

        click_prefixes = [
            "click on the ", "click on ", "click the ", "click ",
            "press the ", "press ", "hit the ", "hit ", "tap on the ", "tap on ", "tap "
        ]
        for cp in click_prefixes:
            if clean.startswith(cp):
                target = clean[len(cp):].strip()
                # Skip pure generic/ambiguous words like "it", "this", "button", "mouse"
                if target in ["it", "this", "that", "button", "screen", "mouse", "left button", "right button", "here"]:
                    return False, None
                # Skip pure hardware keyboard keys
                target_key = target.replace(" key", "").strip()
                if target in KEYBOARD_KEYS or target_key in KEYBOARD_KEYS:
                    return False, None
                # If target is specified, execute visual target click
                if target:
                    success, reply = self.click_visual_target(target)
                    return True, reply

        return False, None


# Global Singleton
VISION_ENGINE = VisionEngine()
