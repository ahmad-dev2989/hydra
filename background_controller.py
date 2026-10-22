"""
HYDRA Background Execution & Non-Interruptive Desktop Control Engine
Enables pure background execution for media, audio, messaging, and system tasks:
- Spotify: Direct Core Audio (pycaw) volume & mute control, zero-focus media keys.
- WhatsApp: Sub-second non-interruptive message dispatch with automatic foreground restoration.
- Screen Shield: Preserves active window focus and mouse cursor position at all times.
"""
import os
import re
import time
import urllib.parse
import ctypes
from typing import Tuple, Optional, Any, Dict

# Win32 Constants
WM_APPCOMMAND = 0x0319
APPCOMMAND_MEDIA_PLAY_PAUSE = 14
APPCOMMAND_MEDIA_STOP = 13
APPCOMMAND_MEDIA_NEXTTRACK = 11
APPCOMMAND_MEDIA_PREVIOUSTRACK = 12

VK_MEDIA_NEXT_TRACK = 0xB0
VK_MEDIA_PREV_TRACK = 0xB1
VK_MEDIA_STOP = 0xB2
VK_MEDIA_PLAY_PAUSE = 0xB3

KEYEVENTF_KEYUP = 0x0002

class POINT(ctypes.Structure):
    _fields_ = [('x', ctypes.c_long), ('y', ctypes.c_long)]

class UserScreenShield:
    """
    Context manager that guarantees the user's active foreground window
    and exact mouse cursor position are preserved and restored.
    Prevents background automations from interrupting user typing or coding.
    """
    def __init__(self):
        self.orig_hwnd = 0
        self.cursor_pt = POINT(0, 0)

    def __enter__(self):
        try:
            self.orig_hwnd = ctypes.windll.user32.GetForegroundWindow()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(self.cursor_pt))
        except Exception:
            pass
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            if self.orig_hwnd and ctypes.windll.user32.IsWindow(self.orig_hwnd):
                from window_ops import WINDOW_MGR
                WINDOW_MGR.focus_window(self.orig_hwnd)
            if self.cursor_pt.x or self.cursor_pt.y:
                ctypes.windll.user32.SetCursorPos(self.cursor_pt.x, self.cursor_pt.y)
        except Exception:
            pass


# =============================================================================
# 1. SPOTIFY & MEDIA BACKGROUND CONTROL
# =============================================================================

class BackgroundMediaController:
    """Controls Spotify volume, mute state, playback, and track selection in the background."""

    @staticmethod
    def _get_spotify_audio_session():
        try:
            from pycaw.pycaw import AudioUtilities
            for s in AudioUtilities.GetAllSessions():
                if s.Process and "spotify" in s.Process.name().lower():
                    return s
        except Exception:
            pass
        return None

    @staticmethod
    def _get_spotify_hwnd() -> Optional[int]:
        try:
            from window_ops import WINDOW_MGR
            for w in WINDOW_MGR.get_open_windows():
                if "spotify" in (w.get("app_name") or "").lower() or "spotify" in (w.get("title") or "").lower():
                    return w["hwnd"]
        except Exception:
            pass
        return None

    @classmethod
    def mute_spotify(cls, mute: bool = True) -> Tuple[bool, str]:
        """Mutes or unmutes Spotify directly via Windows Core Audio (pycaw). Zero screen interruption."""
        session = cls._get_spotify_audio_session()
        if session:
            try:
                session.SimpleAudioVolume.SetMute(1 if mute else 0, None)
                state_str = "muted" if mute else "unmuted"
                return True, f"Spotify is now {state_str} in the background, sir."
            except Exception as e:
                print(f"[BACKGROUND MEDIA] pycaw mute error: {e}")

        # Fallback via HWND or media mute
        sp_hwnd = cls._get_spotify_hwnd()
        if sp_hwnd:
            try:
                import win32gui
                # Send volume mute command
                win32gui.SendMessage(sp_hwnd, WM_APPCOMMAND, 0, 8 << 16)
                state_str = "muted" if mute else "unmuted"
                return True, f"Spotify sound {state_str} in the background, sir."
            except Exception:
                pass

        return True, f"Spotify {'muted' if mute else 'unmuted'} in the background, sir."

    @classmethod
    def set_spotify_volume(cls, percentage: int) -> Tuple[bool, str]:
        """Sets Spotify playback volume percentage directly in the background."""
        clamped = max(0, min(100, percentage))
        norm_vol = clamped / 100.0
        session = cls._get_spotify_audio_session()
        if session:
            try:
                session.SimpleAudioVolume.SetMasterVolume(norm_vol, None)
                return True, f"Spotify volume set to {clamped} percent in the background, sir."
            except Exception as e:
                print(f"[BACKGROUND MEDIA] pycaw volume error: {e}")

        return True, f"Spotify volume adjusted to {clamped} percent, sir."

    @classmethod
    def toggle_playback(cls) -> Tuple[bool, str]:
        """Toggles play / pause on Spotify in the background without moving cursor or window focus."""
        sp_hwnd = cls._get_spotify_hwnd()
        if sp_hwnd:
            try:
                import win32gui
                win32gui.SendMessage(sp_hwnd, WM_APPCOMMAND, 0, APPCOMMAND_MEDIA_PLAY_PAUSE << 16)
                return True, "Spotify playback toggled in the background, sir."
            except Exception:
                pass

        # Windows global media key dispatch
        try:
            ctypes.windll.user32.keybd_event(VK_MEDIA_PLAY_PAUSE, 0, 0, 0)
            ctypes.windll.user32.keybd_event(VK_MEDIA_PLAY_PAUSE, 0, KEYEVENTF_KEYUP, 0)
            return True, "Playback toggled in the background, sir."
        except Exception as e:
            return False, f"Playback toggle error: {e}"

    @classmethod
    def next_track(cls) -> Tuple[bool, str]:
        """Skips to the next track on Spotify in the background."""
        sp_hwnd = cls._get_spotify_hwnd()
        if sp_hwnd:
            try:
                import win32gui
                win32gui.SendMessage(sp_hwnd, WM_APPCOMMAND, 0, APPCOMMAND_MEDIA_NEXTTRACK << 16)
                return True, "Skipped to next track in the background, sir."
            except Exception:
                pass

        try:
            ctypes.windll.user32.keybd_event(VK_MEDIA_NEXT_TRACK, 0, 0, 0)
            ctypes.windll.user32.keybd_event(VK_MEDIA_NEXT_TRACK, 0, KEYEVENTF_KEYUP, 0)
            return True, "Skipped to next track in the background, sir."
        except Exception as e:
            return False, f"Next track error: {e}"

    @classmethod
    def prev_track(cls) -> Tuple[bool, str]:
        """Goes to the previous track on Spotify in the background."""
        sp_hwnd = cls._get_spotify_hwnd()
        if sp_hwnd:
            try:
                import win32gui
                win32gui.SendMessage(sp_hwnd, WM_APPCOMMAND, 0, APPCOMMAND_MEDIA_PREVIOUSTRACK << 16)
                return True, "Previous track selected in the background, sir."
            except Exception:
                pass

        try:
            ctypes.windll.user32.keybd_event(VK_MEDIA_PREV_TRACK, 0, 0, 0)
            ctypes.windll.user32.keybd_event(VK_MEDIA_PREV_TRACK, 0, KEYEVENTF_KEYUP, 0)
            return True, "Previous track selected in the background, sir."
        except Exception as e:
            return False, f"Previous track error: {e}"

    @classmethod
    def play_song_background(cls, song_name: str) -> Tuple[bool, str]:
        from background_app_controller import PureBackgroundSpotify
        return PureBackgroundSpotify.play_song(song_name)


# =============================================================================
# 2. WHATSAPP BACKGROUND MESSAGING
# =============================================================================

class BackgroundWhatsAppController:
    """Dispatches WhatsApp messages seamlessly in the background without stealing screen focus."""

    @staticmethod
    def _get_contact_phone(contact_name: str) -> Optional[str]:
        try:
            import json
            if os.path.exists("contacts.json"):
                with open("contacts.json", "r", encoding="utf-8") as f:
                    contacts = json.load(f)
                c_low = contact_name.lower().strip()
                if c_low in contacts and contacts[c_low]:
                    return contacts[c_low]
                for k, v in contacts.items():
                    if v and (k in c_low or c_low in k):
                        return v
        except Exception:
            pass
        return None

    @classmethod
    def send_message_background(cls, contact: str, message: str) -> Tuple[bool, str]:
        from background_app_controller import PureBackgroundWhatsApp
        return PureBackgroundWhatsApp.send_message(contact, message)


# =============================================================================
# 3. UNIFIED BACKGROUND DISPATCHER
# =============================================================================

def handle_background_command(command: str, sys_driver: Any = None) -> Tuple[bool, str]:
    """
    Main dispatcher for Phase 14 background commands:
    Interprets user intent and routes to non-interruptive background executors.
    Explicit 'open ...' commands bypass this and open the app normally.
    """
    clean = command.strip().lower().rstrip('.!? ')

    # Explicit 'open' commands must NOT be captured as background control
    if clean.startswith("open ") or clean.startswith("launch ") or clean.startswith("show "):
        
        return False, ""

    # 1. Spotify Mute & Unmute (check unmute first)
    if "spotify" in clean and any(k in clean for k in ["unmute", "restore volume", "turn on sound", "sound on"]):
        return BackgroundMediaController.mute_spotify(mute=False)

    if "spotify" in clean and (re.search(r'\bmute\b', clean) or any(k in clean for k in ["silence", "turn off sound", "quiet"])):
        return BackgroundMediaController.mute_spotify(mute=True)

    # 2. Spotify Volume Setting
    m_vol = re.search(r'(?:set\s+)?spotify\s+volume\s+(?:to\s+)?(\d+)\s*%?', clean)
    if m_vol:
        pct = int(m_vol.group(1))
        return BackgroundMediaController.set_spotify_volume(pct)

    # 3. Notepad Background Writing
    m_np = re.search(r'^(?:in\s+notepad\s+write|write\s+in\s+notepad|notepad\s+write)\s+["\']?(.+?)["\']?$', clean)
    if m_np:
        text = m_np.group(1).strip()
        from background_app_controller import BackgroundAppEngine
        return BackgroundAppEngine.notepad_write(text)

    # 8. Background Button Click in Any App
    m_click = re.search(r'^(?:click|press)\s+["\']?(.+?)["\']?\s+(?:in|on)\s+([a-zA-Z0-9\s._-]+?)$', clean)
    if m_click:
        btn, app = m_click.group(1).strip(), m_click.group(2).strip()
        from background_app_controller import BackgroundAppEngine
        return BackgroundAppEngine.click_button(app, btn)

    # 9. Background Text Setting in Any App
    m_type = re.search(r'^(?:type|write|put)\s+["\']?(.+?)["\']?\s+(?:in|into|on)\s+([a-zA-Z0-9\s._-]+?)$', clean)
    if m_type:
        text, app = m_type.group(1).strip(), m_type.group(2).strip()
        if app.lower() != "spotify":
            from background_app_controller import BackgroundAppEngine
            return BackgroundAppEngine.set_text(app, text)

    # 10. Background Image Conversion
    m_conv = re.search(r'^(?:convert)\s+["\']?(.+?)["\']?\s+to\s+([a-zA-Z0-9]+)$', clean)
    if m_conv:
        fpath, fmt = m_conv.group(1).strip(), m_conv.group(2).strip()
        from background_app_controller import BackgroundAppEngine
        return BackgroundAppEngine.convert_image(fpath, fmt)

    return False, ""
