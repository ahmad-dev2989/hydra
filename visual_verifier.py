"""
HYDRA Phase 3: Visual State Verification & Slow-PC Adaptive Waiting Engine
Powered by Nebius AI (NVIDIA Nemotron-3.5-Super-VL) & GDI Screen Capture.

Solves the Slow-PC problem:
1. UI Idle Synchronization: Verifies target window has finished loading (not blank/loading screen).
2. Post-Action Verification: Captures screen and uses Nemotron-3.5-Super-VL to verify text was inserted,
   cursor is in search bar, results appeared, and song is playing.
3. Adaptive Retries: Waits in small 0.5s increments up to a configurable timeout before progressing.
"""

import os
import io
import sys
import time
import json
import base64
import ctypes
from ctypes import wintypes
from typing import Dict, Any, List, Optional, Tuple
from pathlib import Path

# Load environment
try:
    from dotenv import load_dotenv
    for d in [Path(__file__).resolve().parent, Path(__file__).resolve().parent.parent, Path(__file__).resolve().parent.parent.parent]:
        env_f = d / ".env"
        if env_f.exists():
            load_dotenv(dotenv_path=env_f)
            break
except ImportError:
    pass

from PIL import Image

# Nebius & Vision Configuration
NEBIUS_API_KEY = os.environ.get("NEBIUS_API_KEY", "").strip()
NEBIUS_BASE_URL = os.environ.get("NEBIUS_BASE_URL", "https://api.tokenfactory.nebius.com/v1/").strip()
NEBIUS_VISION_MODEL = os.environ.get("NEBIUS_VISION_MODEL", "nvidia/Nemotron-3.5-Super-VL").strip()

# Win32 GDI Screen Capture Setup (Failsafe for background/desktop sessions)
user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

user32.OpenWindowStationW.restype = wintypes.HANDLE
user32.OpenWindowStationW.argtypes = [wintypes.LPCWSTR, wintypes.BOOL, wintypes.DWORD]
user32.SetProcessWindowStation.restype = wintypes.BOOL
user32.SetProcessWindowStation.argtypes = [wintypes.HANDLE]
user32.OpenDesktopW.restype = wintypes.HANDLE
user32.OpenDesktopW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
user32.SetThreadDesktop.restype = wintypes.BOOL
user32.SetThreadDesktop.argtypes = [wintypes.HANDLE]

def _ensure_desktop_access():
    try:
        hwinsta = user32.OpenWindowStationW('WinSta0', False, 0x037F)
        if hwinsta:
            user32.SetProcessWindowStation(hwinsta)
        hdesk = user32.OpenDesktopW('Default', 0, False, 0x01FF)
        if hdesk:
            user32.SetThreadDesktop(hdesk)
    except Exception:
        pass

_ensure_desktop_access()


def capture_screen_gdi() -> Image.Image:
    """Captures the entire desktop screen using GDI BitBlt (100% reliable on Windows)."""
    _ensure_desktop_access()
    left = user32.GetSystemMetrics(76)
    top = user32.GetSystemMetrics(77)
    width = user32.GetSystemMetrics(78)
    height = user32.GetSystemMetrics(79)
    if width <= 0 or height <= 0:
        width = user32.GetSystemMetrics(0)
        height = user32.GetSystemMetrics(1)
        left = 0
        top = 0

    h_desktop = user32.GetDesktopWindow()
    h_dc = user32.GetDC(h_desktop)
    m_dc = gdi32.CreateCompatibleDC(h_dc)
    h_bmp = gdi32.CreateCompatibleBitmap(h_dc, width, height)
    h_old = gdi32.SelectObject(m_dc, h_bmp)
    buf = None

    try:
        gdi32.BitBlt(m_dc, 0, 0, width, height, h_dc, left, top, 0x00CC0020)

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [
                ('biSize', wintypes.DWORD),
                ('biWidth', ctypes.c_long),
                ('biHeight', ctypes.c_long),
                ('biPlanes', wintypes.WORD),
                ('biBitCount', wintypes.WORD),
                ('biCompression', wintypes.DWORD),
                ('biSizeImage', wintypes.DWORD),
                ('biXPelsPerMeter', ctypes.c_long),
                ('biYPelsPerMeter', ctypes.c_long),
                ('biClrUsed', wintypes.DWORD),
                ('biClrImportant', wintypes.DWORD)
            ]

        bmi = BITMAPINFOHEADER()
        bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.biWidth = width
        bmi.biHeight = -height
        bmi.biPlanes = 1
        bmi.biBitCount = 32
        bmi.biCompression = 0

        buf_size = width * height * 4
        buf = (ctypes.c_char * buf_size)()
        gdi32.GetDIBits(m_dc, h_bmp, 0, height, ctypes.byref(buf), ctypes.byref(bmi), 0)
    finally:
        if h_old:
            gdi32.SelectObject(m_dc, h_old)
        if h_bmp:
            gdi32.DeleteObject(h_bmp)
        if m_dc:
            gdi32.DeleteDC(m_dc)
        if h_dc:
            user32.ReleaseDC(h_desktop, h_dc)

    if buf is not None:
        img = Image.frombuffer('RGBA', (width, height), buf, 'raw', 'BGRA', 0, 1)
        return img.convert('RGB')
    raise RuntimeError("Failed to capture screen via GDI")


def get_effective_vision_config() -> Tuple[str, str, str]:
    """Dynamically resolves API key, base URL, and vision model from current environment."""
    api_key = (
        os.environ.get("NEBIUS_API_KEY", "").strip()
        or os.environ.get("NVIDIA_API_KEY", "").strip()
        or os.environ.get("OPENAI_API_KEY", "").strip()
    )
    base_url = os.environ.get("NEBIUS_BASE_URL", "https://api.tokenfactory.nebius.com/v1/").strip()
    if not base_url:
        base_url = "https://api.tokenfactory.nebius.com/v1/"

    vision_model = os.environ.get("NEBIUS_VISION_MODEL", "").strip()
    if not vision_model:
        if "openai.com" in base_url.lower():
            vision_model = "gpt-4o-mini"
        else:
            vision_model = "nvidia/Nemotron-3.5-Super-VL"
    return api_key, base_url, vision_model


class VisualStateVerifier:
    """
    Core Phase 3 Verification Engine.
    Interrogates desktop screen state with NVIDIA Nemotron-3.5-Super-VL
    and implements adaptive waiting for slow PCs.
    """

    def __init__(self):
        self._client = None
        self._cached_key = None
        self._cached_base = None

    def get_vision_model(self) -> str:
        _, _, model = get_effective_vision_config()
        return model

    def get_client(self):
        api_key, base_url, _ = get_effective_vision_config()
        if self._client is None or self._cached_key != api_key or self._cached_base != base_url:
            from openai import OpenAI
            self._cached_key = api_key
            self._cached_base = base_url
            self._client = OpenAI(
                base_url=base_url,
                api_key=api_key or "not_set",
                timeout=25.0
            )
        return self._client

    def _prepare_image_b64(self, img: Image.Image, max_dim: int = 1280) -> str:
        """Resizes image maintaining aspect ratio and encodes to base64 JPEG."""
        img_copy = img.copy()
        if img_copy.mode in ("RGBA", "LA", "P"):
            img_copy = img_copy.convert("RGB")
        img_copy.thumbnail((max_dim, int(max_dim * 0.75)))
        buf = io.BytesIO()
        img_copy.save(buf, format="JPEG", quality=80)
        return base64.b64encode(buf.getvalue()).decode("utf-8")

    def query_vision_model(self, prompt: str, img: Optional[Image.Image] = None) -> Dict[str, Any]:
        """
        Sends a desktop screenshot and verification prompt to Nemotron-3.5-Super-VL (or fallback vision model).
        Extracts structured JSON verification outcome from either content or reasoning_content.
        """
        if img is None:
            img = capture_screen_gdi()

        b64_str = self._prepare_image_b64(img)
        client = self.get_client()
        active_model = self.get_vision_model()

        sys_instruction = (
            "You are HYDRA's Desktop Visual Verification AI.\n"
            "Analyze the provided screenshot carefully and verify whether the requested UI state is reached.\n"
            "Always output a valid JSON object matching this schema:\n"
            "{\n"
            "  \"verified\": true,\n"
            "  \"app_state\": \"ready | loading | blank | error | not_visible\",\n"
            "  \"observed_app\": \"<name of visible app>\",\n"
            "  \"observed_details\": \"<what is visible on screen>\",\n"
            "  \"target_found\": true,\n"
            "  \"reason\": \"<explanation>\"\n"
            "}"
        )

        try:
            res = client.chat.completions.create(
                model=active_model,
                messages=[
                    {"role": "system", "content": sys_instruction},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_str}"}}
                        ]
                    }
                ],
                max_tokens=600,
                temperature=0.1
            )

            msg = res.choices[0].message
            content = msg.content or ""
            reasoning = getattr(msg, "reasoning_content", "") or ""

            # Attempt JSON extraction from content first, then reasoning_content
            parsed = self._extract_json(content) or self._extract_json(reasoning)
            if parsed:
                return parsed

            # Fallback heuristic if pure JSON parse failed
            combined_text = (content + " " + reasoning).lower()
            is_verified = "verified\": true" in combined_text or ("true" in combined_text and "not visible" not in combined_text and "not loaded" not in combined_text)
            is_loading = "loading" in combined_text or "blank" in combined_text or "spinner" in combined_text
            return {
                "verified": is_verified and not is_loading,
                "app_state": "loading" if is_loading else ("ready" if is_verified else "unknown"),
                "observed_details": content[:200] or reasoning[:200],
                "reason": "Parsed from vision text response."
            }

        except Exception as e:
            print(f"[VISUAL VERIFIER ERROR] Vision model request failed: {e}")
            return {
                "verified": False,
                "app_state": "error",
                "reason": str(e)
            }

    def _extract_json(self, text: str) -> Optional[Dict[str, Any]]:
        if not text:
            return None
        text_clean = text.strip()
        try:
            return json.loads(text_clean)
        except Exception:
            pass

        # Check markdown blocks
        import re
        match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1).strip())
            except Exception:
                pass

        # Find outermost { ... } containing "verified"
        starts = [i for i, c in enumerate(text) if c == '{']
        for start in starts:
            for end in range(len(text), start, -1):
                if text[end-1] == '}':
                    candidate = text[start:end]
                    if '"verified"' in candidate:
                        try:
                            return json.loads(candidate)
                        except Exception:
                            pass
        return None

    # =========================================================================
    # SLOW-PC ADAPTIVE WAITING LOOP
    # =========================================================================

    def adaptive_wait_until_verified(
        self,
        step_description: str,
        verification_prompt: str,
        app_name: str = "",
        timeout: float = 16.0,
        interval: float = 0.5,
        status_callback=None
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Polls the desktop screen in 0.5s increments up to `timeout`.
        If the app is still loading or blank, it waits adaptively without failing.
        Once the visual state is verified by Nemotron-3.5-Super-VL, returns True immediately.
        """
        start_time = time.perf_counter()
        iteration = 1
        last_details = {}

        if status_callback:
            status_callback(f"Verifying: {step_description}...")

        print(f"\n[VISUAL VERIFY] Step: '{step_description}' (Target: {app_name or 'Desktop'})")

        while time.perf_counter() - start_time < timeout:
            elapsed = time.perf_counter() - start_time
            print(f"[VISUAL VERIFY] Check #{iteration} (+{elapsed:.1f}s) — Capturing screen and evaluating...")

            img = capture_screen_gdi()
            result = self.query_vision_model(verification_prompt, img=img)
            last_details = result

            verified = result.get("verified", False)
            app_state = result.get("app_state", "unknown")
            details = result.get("observed_details", "")

            # If verified and not stuck in a loading/blank state, succeed immediately!
            if verified and app_state not in ["loading", "blank", "not_visible"]:
                print(f"[VISUAL VERIFY] -> SUCCESS (+{elapsed:.2f}s)! {details}")
                if status_callback:
                    status_callback(f"Verified (+{elapsed:.1f}s): {details[:60]}")
                return True, details, result

            # If still loading, log slow-PC adaptive wait
            if app_state == "loading" or "loading" in details.lower() or "blank" in details.lower():
                print(f"[VISUAL VERIFY] -> Target is still loading (+{elapsed:.1f}s). Waiting adaptively for {interval}s...")
            else:
                print(f"[VISUAL VERIFY] -> Condition not met yet (+{elapsed:.1f}s). Retrying...")

            time.sleep(interval)
            iteration += 1

        total_time = time.perf_counter() - start_time
        err_msg = f"Timeout ({total_time:.1f}s) waiting for: {step_description}. Last state: {last_details.get('observed_details', 'Unknown')}"
        print(f"[VISUAL VERIFY] -> FAILED: {err_msg}")
        return False, err_msg, last_details

    def compare_before_after_state(
        self,
        goal: str,
        img_initial: Image.Image,
        img_final: Image.Image,
        app_name: str = ""
    ) -> Tuple[bool, str]:
        """
        Compares INITIAL screen (taken right after opening the app) with FINAL screen
        (taken after executing actions) to verify whether the goal (e.g. changing theme,
        updating profile picture, sending message, creating design, playing song)
        was successfully accomplished.
        """
        b64_init = self._prepare_image_b64(img_initial)
        b64_final = self._prepare_image_b64(img_final)
        client = self.get_client()

        sys_prompt = (
            "You are HYDRA's Expert Visual Verification AI.\n"
            "You are comparing TWO sequential screenshots of the user's desktop/application:\n"
            "- IMAGE 1: Initial state (taken right after opening/focusing the application, BEFORE executing the task).\n"
            "- IMAGE 2: Final state (taken AFTER executing the saved action steps).\n\n"
            "Your job is to determine whether the user's requested goal was successfully achieved by comparing Image 1 and Image 2.\n"
            "Examples of changes to detect:\n"
            "- Theme change: e.g. from light mode to dark mode, or from dark to light.\n"
            "- Profile picture change: e.g. a new avatar/photo has been applied.\n"
            "- Messaging: e.g. message sent and visible in the chat timeline.\n"
            "- Media playback: e.g. song playing, play button toggled, or track active.\n"
            "- Creation: e.g. new canvas/document/design opened with requested dimensions.\n\n"
            "Output strictly valid JSON matching this schema:\n"
            "{\n"
            "  \"completed\": true,\n"
            "  \"reason\": \"<brief 1-2 sentence explanation comparing Image 1 and Image 2>\"\n"
            "}"
        )

        user_content = [
            {
                "type": "text",
                "text": (
                    f"User Goal: '{goal}'\n"
                    f"Application: '{app_name}'\n\n"
                    "Image 1 is the INITIAL state before the actions.\n"
                    "Image 2 is the FINAL state after the actions.\n"
                    "Did the user's goal successfully complete in Image 2 compared to Image 1? Output valid JSON."
                )
            },
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64_init}"}
            },
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64_final}"}
            }
        ]

        try:
            active_model = self.get_vision_model()
            res = client.chat.completions.create(
                model=active_model,
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_content}
                ],
                max_tokens=300,
                temperature=0.1,
                timeout=30.0
            )
            msg = res.choices[0].message
            content = msg.content or ""
            reasoning = getattr(msg, "reasoning_content", "") or ""
            parsed = self._extract_json(content) or self._extract_json(reasoning)
            if parsed and "completed" in parsed:
                return bool(parsed["completed"]), parsed.get("reason", "Visual state comparison completed.")

            combined = (content + " " + reasoning).lower()
            is_done = "completed\": true" in combined or "true" in combined
            return is_done, content[:200] or reasoning[:200]
        except Exception as e:
            print(f"[BEFORE/AFTER VISION ERROR] {e}")
            return False, f"Vision model comparison error: {e}"


# Singleton instance
VISUAL_VERIFIER = VisualStateVerifier()
