"""
media_controller.py - Windows System Media Transport Controls (SMTC) Controller
Extracts real-time playing media metadata (Spotify / system media), album artwork,
playback status, and timeline progress, with transport control actions (Play/Pause,
Next, Prev, Seek).
"""

import sys
import ctypes
import base64
import asyncio
import threading
import time

try:
    from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as SMTCManager
    from winrt.windows.storage.streams import DataReader
    HAS_WINRT_SMTC = True
except Exception as e:
    HAS_WINRT_SMTC = False
    print(f"[MEDIA_CONTROLLER] WinRT SMTC not available: {e}")

# Virtual Key Codes for Windows Media Key Fallbacks
VK_MEDIA_NEXT_TRACK = 0xB0
VK_MEDIA_PREV_TRACK = 0xB1
VK_MEDIA_STOP = 0xB2
VK_MEDIA_PLAY_PAUSE = 0xB3
VK_VOLUME_MUTE = 0xAD
VK_VOLUME_DOWN = 0xAE
VK_VOLUME_UP = 0xAF
KEYEVENTF_KEYUP = 0x0002


def _send_media_key(vk_code):
    """Sends a virtual hardware media key event via Windows user32."""
    try:
        user32 = ctypes.windll.user32
        user32.keybd_event(vk_code, 0, 0, 0)
        time.sleep(0.02)
        user32.keybd_event(vk_code, 0, KEYEVENTF_KEYUP, 0)
        return True
    except Exception as e:
        print(f"[MEDIA KEY ERROR] vk={vk_code}: {e}")
        return False


class MediaController:
    """Manages real-time Spotify and Windows Media transport state and controls."""

    def __init__(self):
        self._lock = threading.Lock()
        self._cached_track_id = ""
        self._cached_thumb_b64 = ""
        self._last_state = {
            "active": False,
            "app": "Spotify",
            "title": "Spotify",
            "artist": "No track playing",
            "album": "",
            "status": "STOPPED",
            "position": 0,
            "duration": 0,
            "thumb": ""
        }
        self._loop = None
        self._thread = None
        self._init_event_loop()

    def _init_event_loop(self):
        """Initializes a dedicated background asyncio thread for WinRT calls."""
        self._ready_event = threading.Event()
        def run_loop():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._ready_event.set()
            self._loop.run_forever()

        self._thread = threading.Thread(target=run_loop, daemon=True, name="MediaControllerLoop")
        self._thread.start()
        self._ready_event.wait(timeout=2.0)

    def _run_coroutine(self, coro, timeout=3.0):
        """Dispatches an async coroutine to the background loop and waits for result."""
        if not self._loop or not self._loop.is_running():
            try:
                coro.close()
            except Exception:
                pass
            return None
        try:
            future = asyncio.run_coroutine_threadsafe(coro, self._loop)
            return future.result(timeout=timeout)
        except Exception as e:
            try:
                coro.close()
            except Exception:
                pass
            return None

    def get_state(self):
        """Synchronously returns the current media playback state dict."""
        if not HAS_WINRT_SMTC:
            return self._last_state

        result = self._run_coroutine(self._fetch_smtc_state())
        if result is not None:
            with self._lock:
                self._last_state = result
        return self._last_state

    async def _fetch_smtc_state(self):
        try:
            manager = await SMTCManager.request_async()
            curr = manager.get_current_session()
            
            # If current session is null, try to find any Spotify session among active sessions
            if not curr:
                sessions = manager.get_sessions()
                for s in sessions:
                    app_id = s.source_app_user_model_id or ""
                    if "spotify" in app_id.lower():
                        curr = s
                        break
                if not curr and len(sessions) > 0:
                    curr = sessions[0]

            if not curr:
                return {
                    "active": False,
                    "app": "Spotify",
                    "title": "Spotify Ready",
                    "artist": "Click play to listen",
                    "album": "",
                    "status": "STOPPED",
                    "position": 0,
                    "duration": 0,
                    "thumb": self._cached_thumb_b64
                }

            app_id = curr.source_app_user_model_id or "Spotify"
            app_name = "Spotify" if "spotify" in app_id.lower() else app_id.replace(".exe", "")

            # Playback status
            pb_info = curr.get_playback_info()
            status_str = "STOPPED"
            if pb_info and pb_info.playback_status is not None:
                status_str = pb_info.playback_status.name  # 'PLAYING', 'PAUSED', 'STOPPED'

            # Media Properties (Title, Artist, Album)
            media_props = await curr.try_get_media_properties_async()
            title = media_props.title if media_props else ""
            artist = media_props.artist if media_props else ""
            album = media_props.album_title if media_props else ""

            # Timeline properties (position, duration)
            timeline = curr.get_timeline_properties()
            pos = timeline.position.total_seconds() if timeline and timeline.position else 0
            end = timeline.end_time.total_seconds() if timeline and timeline.end_time else 0

            # Unique track key to avoid re-reading thumbnail stream on every tick
            track_key = f"{app_name}:{title}:{artist}"
            thumb_b64 = self._cached_thumb_b64

            if track_key != self._cached_track_id:
                self._cached_track_id = track_key
                self._cached_thumb_b64 = ""
                thumb_b64 = ""
                if media_props and media_props.thumbnail:
                    try:
                        stream = await media_props.thumbnail.open_read_async()
                        size = stream.size
                        if size > 0:
                            reader = DataReader(stream)
                            await reader.load_async(size)
                            buf = bytearray(size)
                            reader.read_bytes(buf)
                            thumb_b64 = "data:image/jpeg;base64," + base64.b64encode(buf).decode("ascii")
                            self._cached_thumb_b64 = thumb_b64
                    except Exception:
                        pass

            return {
                "active": True,
                "app": app_name,
                "title": title or "Unknown Track",
                "artist": artist or "Unknown Artist",
                "album": album,
                "status": status_str,
                "position": round(pos, 1),
                "duration": round(end, 1),
                "thumb": thumb_b64
            }

        except Exception as e:
            return {
                "active": False,
                "app": "Spotify",
                "title": "Spotify",
                "artist": "Idle",
                "album": "",
                "status": "STOPPED",
                "position": 0,
                "duration": 0,
                "thumb": self._cached_thumb_b64
            }

    def toggle_play_pause(self):
        """Toggles play/pause on current session or falls back to hardware media key."""
        res = self._run_coroutine(self._async_toggle_play_pause(), timeout=1.5)
        if not res:
            _send_media_key(VK_MEDIA_PLAY_PAUSE)
        return True

    def pause(self):
        """Ensures playback is paused."""
        state = self.get_state()
        if state.get("status") == "PLAYING":
            return self.toggle_play_pause()
        return True

    def play(self):
        """Ensures playback is active."""
        state = self.get_state()
        if state.get("status") != "PLAYING":
            return self.toggle_play_pause()
        return True

    async def _async_toggle_play_pause(self):
        try:
            manager = await SMTCManager.request_async()
            curr = manager.get_current_session()
            if curr:
                return await curr.try_toggle_play_pause_async()
        except Exception:
            pass
        return False

    def next_track(self):
        """Skips to next track."""
        res = self._run_coroutine(self._async_next(), timeout=1.5)
        if not res:
            _send_media_key(VK_MEDIA_NEXT_TRACK)
        return True

    async def _async_next(self):
        try:
            manager = await SMTCManager.request_async()
            curr = manager.get_current_session()
            if curr:
                return await curr.try_skip_next_async()
        except Exception:
            pass
        return False

    def prev_track(self):
        """Skips to previous track."""
        res = self._run_coroutine(self._async_prev(), timeout=1.5)
        if not res:
            _send_media_key(VK_MEDIA_PREV_TRACK)
        return True

    async def _async_prev(self):
        try:
            manager = await SMTCManager.request_async()
            curr = manager.get_current_session()
            if curr:
                return await curr.try_skip_previous_async()
        except Exception:
            pass
        return False

    def seek(self, target_seconds):
        """Seeks to playback position in seconds."""
        res = self._run_coroutine(self._async_seek(target_seconds), timeout=1.5)
        return bool(res)

    def volume_up(self, steps=2):
        """Increases system/media master volume."""
        for _ in range(steps):
            _send_media_key(VK_VOLUME_UP)
            time.sleep(0.01)
        return True

    def volume_down(self, steps=2):
        """Decreases system/media master volume."""
        for _ in range(steps):
            _send_media_key(VK_VOLUME_DOWN)
            time.sleep(0.01)
        return True

    def toggle_mute(self):
        """Toggles audio mute state."""
        return _send_media_key(VK_VOLUME_MUTE)


# Singleton instance
MEDIA_CONTROLLER = MediaController()
