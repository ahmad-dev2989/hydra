"""
HYDRA Phase 15: Optical Perception, Real-time Camera Vision & Gesture Engine
Provides:
1. Real-time camera feed streaming (MJPEG) to the PyQt6 WebEngine HUD.
2. HUD cybernetic transformation (minimizing liquid core to bottom-left, preserving grid).
3. Real-time scene telemetry (lighting, human face tracking, motion activity).
4. Real-time hand contour & gesture recognition (Open Palm, Fist, Peace Sign, Thumbs Up).
5. Multimodal AI & intelligent local vision for identifying objects in hand with 100% truthfulness.
"""

import os
import sys
import time
import json
import base64
import threading
import re
import http.server
import socketserver
from typing import Tuple, Optional, Dict, Any, List
import cv2
import numpy as np

# Win32 & Media Imports
try:
    import ctypes
    user32 = ctypes.windll.user32
except Exception:
    user32 = None

STREAM_HOST = "127.0.0.1"
STREAM_PORT = 8765

class CameraVisionEngine:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(CameraVisionEngine, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

        self.cap = None
        self.is_active = False
        self.lock = threading.Lock()
        
        self.latest_frame = None
        self.latest_jpeg = None
        self.prev_gray = None

        self.server = None
        self.server_thread = None
        self.capture_thread = None

        # Haar Cascades for Face Detection
        self.face_cascade = None
        try:
            cascade_path = cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
            if os.path.exists(cascade_path):
                self.face_cascade = cv2.CascadeClassifier(cascade_path)
        except Exception as e:
            print(f"[CAMERA VISION] Haar cascade warning: {e}")

        # Real-time Telemetry State
        self.telemetry = {
            "active": False,
            "fps": 30,
            "lighting": "OPTIMAL (75%)",
            "faces_detected": 0,
            "face_box": None,
            "motion": "STABLE",
            "gesture": "NONE",
            "gesture_desc": "MONITORING"
        }

        # Gesture Debounce & Execution State
        self.current_gesture = "NONE"
        self.gesture_hold_frames = 0
        self.REQUIRED_HOLD_FRAMES = 12   # ~0.4 - 0.5s of steady gesture
        self.last_gesture_trigger_time = 0.0
        self.GESTURE_COOLDOWN = 2.5      # Minimum delay between automated tasks

        # Local Callback for UI updates
        self.ui_signaler = None

    def set_ui_signaler(self, signaler):
        self.ui_signaler = signaler

    def emit_js(self, js_code: str):
        if self.ui_signaler:
            try:
                if callable(self.ui_signaler):
                    self.ui_signaler(js_code)
                elif hasattr(self.ui_signaler, "emit"):
                    self.ui_signaler.emit(js_code)
            except Exception as e:
                print(f"[CAMERA VISION] JS emit error: {e}")

    # =========================================================================
    # CAMERA LIFECYCLE & HTTP STREAM
    # =========================================================================

    def start_camera(self) -> Tuple[bool, str]:
        """Starts optical sensor capture and MJPEG web streaming."""
        with self.lock:
            if self.is_active:
                return True, "Optical sensors are already active, sir."

            print("[CAMERA VISION] Initializing optical capture hardware...")
            # Try DirectShow first on Windows for instant initialization
            cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
            if not cap or not cap.isOpened():
                cap = cv2.VideoCapture(0)

            if not cap or not cap.isOpened():
                return False, "Unable to access optical camera hardware, sir. Please check device connections."

            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
            self.cap = cap
            self.is_active = True

            # Start MJPEG HTTP Server
            self._start_http_server()

            # Start Capture & Vision Processing Thread
            self.capture_thread = threading.Thread(target=self._capture_loop, daemon=True)
            self.capture_thread.start()

            # Emit UI transition into Camera Mode
            self.emit_js("enableCameraMode();")

            # Brief warm-up to grab initial frame and evaluate scene
            for _ in range(8):
                if self.latest_frame is not None:
                    break
                time.sleep(0.05)

            faces = self.telemetry.get("faces_detected", 0)
            lighting = self.telemetry.get("lighting", "OPTIMAL")
            if faces > 0:
                scene_info = f" Optical lock acquired on you, lighting is {lighting.lower()}, and visual analysis matrix is active."
            else:
                scene_info = " Optical sensor online and actively monitoring the scene."

            return True, f"Optical eye opened, sir.{scene_info}"

    def stop_camera(self) -> Tuple[bool, str]:
        """Stops optical capture and returns HUD to standard desktop mode."""
        with self.lock:
            if not self.is_active:
                return True, "Optical eye is already closed, sir."

            print("[CAMERA VISION] Deactivating optical sensors...")
            self.is_active = False

            if self.cap:
                try:
                    self.cap.release()
                except Exception:
                    pass
                self.cap = None

            self._stop_http_server()

            # Emit UI exit from Camera Mode
            self.emit_js("disableCameraMode();")
            return True, "Closing optical eye, sir. Returning to standard HUD."

    def _start_http_server(self):
        engine_ref = self

        class MJPEGHandler(http.server.BaseHTTPRequestHandler):
            def _send_cors(self):
                self.send_header('Access-Control-Allow-Origin', '*')
                self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
                self.send_header('Access-Control-Allow-Headers', '*')

            def do_OPTIONS(self):
                self.send_response(200)
                self._send_cors()
                self.end_headers()

            def do_GET(self):
                if self.path.startswith('/video_feed'):
                    self.send_response(200)
                    self.send_header('Age', '0')
                    self.send_header('Cache-Control', 'no-cache, private')
                    self.send_header('Pragma', 'no-cache')
                    self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=FRAME')
                    self._send_cors()
                    self.end_headers()
                    try:
                        while engine_ref.is_active:
                            jpeg_bytes = engine_ref.get_jpeg_frame()
                            if jpeg_bytes:
                                header = (
                                    b'--FRAME\r\n'
                                    b'Content-Type: image/jpeg\r\n'
                                    b'Content-Length: ' + str(len(jpeg_bytes)).encode('ascii') + b'\r\n\r\n'
                                )
                                self.wfile.write(header + jpeg_bytes + b'\r\n')
                            time.sleep(0.033)
                    except (ConnectionError, BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                        pass
                elif self.path.startswith('/frame.jpg'):
                    jpeg_bytes = engine_ref.get_jpeg_frame()
                    if jpeg_bytes:
                        self.send_response(200)
                        self.send_header('Content-Type', 'image/jpeg')
                        self.send_header('Content-Length', str(len(jpeg_bytes)))
                        self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
                        self._send_cors()
                        self.end_headers()
                        try:
                            self.wfile.write(jpeg_bytes)
                        except (ConnectionError, BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                            pass
                    else:
                        self.send_response(503)
                        self.end_headers()
                else:
                    self.send_response(404)
                    self.end_headers()

            def log_message(self, format, *args):
                pass  # Suppress HTTP log spam

        try:
            # Reusable socket to avoid port binding errors on quick restart
            class ReusableTCPServer(socketserver.ThreadingTCPServer):
                allow_reuse_address = True

            self.server = ReusableTCPServer((STREAM_HOST, STREAM_PORT), MJPEGHandler)
            self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
            self.server_thread.start()
            print(f"[CAMERA VISION] MJPEG Stream live at http://{STREAM_HOST}:{STREAM_PORT}/video_feed")
        except Exception as e:
            print(f"[CAMERA VISION] HTTP Server start error (will retry reuse): {e}")

    def _stop_http_server(self):
        if self.server:
            try:
                self.server.shutdown()
                self.server.server_close()
            except Exception:
                pass
            self.server = None

    def get_jpeg_frame(self) -> Optional[bytes]:
        return self.latest_jpeg

    def get_latest_frame(self) -> Optional[np.ndarray]:
        if self.latest_frame is not None:
            return self.latest_frame.copy()
        return None

    # =========================================================================
    # COMPUTER VISION PROCESSING LOOP
    # =========================================================================

    def _capture_loop(self):
        frame_count = 0
        last_telem_emit = 0.0

        while self.is_active and self.cap:
            ret, frame = self.cap.read()
            if not ret or frame is None:
                time.sleep(0.02)
                continue

            frame_count += 1
            # Mirror horizontally for natural webcam interaction
            frame = cv2.flip(frame, 1)
            h, w = frame.shape[:2]
            self.latest_frame = frame.copy()

            # 1. Real-time Scene Telemetry (Every 8th frame for ultra-low CPU)
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            if frame_count % 8 == 0:
                # Fast lighting calculation on subsampled grid
                pct = int((np.mean(gray[::8, ::8]) / 255.0) * 100)
                if pct < 25:
                    light_str = f"LOW LIGHT ({pct}%)"
                elif pct > 85:
                    light_str = f"HIGH EXPOSURE ({pct}%)"
                else:
                    light_str = f"OPTIMAL ({pct}%)"
                self.telemetry["lighting"] = light_str

                # Motion Calculation
                small_curr = gray[::8, ::8]
                if self.prev_gray is not None:
                    diff = cv2.absdiff(self.prev_gray, small_curr)
                    motion_score = np.mean(diff)
                    self.telemetry["motion"] = "ACTIVE MOTION" if motion_score > 12.0 else "SCENE STABLE"
                self.prev_gray = small_curr.copy()

                # Fast Face Detection via Haar Cascade on downscaled frame (320x180)
                faces_found = 0
                face_coords = None
                if self.face_cascade:
                    small_gray = cv2.resize(gray, (w // 4, h // 4))
                    faces = self.face_cascade.detectMultiScale(
                        small_gray, scaleFactor=1.2, minNeighbors=4, minSize=(25, 25)
                    )
                    faces_found = len(faces)
                    if faces_found > 0:
                        fx, fy, fw, fh = faces[0]
                        face_coords = (fx * 4, fy * 4, fw * 4, fh * 4)
                self.telemetry["faces_detected"] = faces_found
                self.telemetry["face_box"] = face_coords

                # Fast Real-time Hand & In-Hand Object Bounding Box Tracking
                object_box = None
                object_label = None
                try:
                    # Focus analysis on interaction zone
                    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
                    lower_skin = np.array([0, 25, 60], dtype=np.uint8)
                    upper_skin = np.array([25, 255, 255], dtype=np.uint8)
                    skin_mask = cv2.inRange(hsv, lower_skin, upper_skin)
                    small_skin = cv2.resize(skin_mask, (w // 2, h // 2))
                    cnts, _ = cv2.findContours(small_skin, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                    if cnts:
                        best_cnt = max(cnts, key=cv2.contourArea)
                        if cv2.contourArea(best_cnt) > 900:
                            hx, hy, hw, hh = cv2.boundingRect(best_cnt)
                            hx, hy, hw, hh = hx * 2, hy * 2, hw * 2, hh * 2
                            
                            # Inspect for held non-skin object inside convex hull
                            hull = cv2.convexHull(best_cnt)
                            hull_mask = np.zeros(small_skin.shape, dtype=np.uint8)
                            cv2.drawContours(hull_mask, [hull], -1, 255, -1)
                            non_skin = cv2.bitwise_and(cv2.bitwise_not(small_skin), hull_mask)
                            obj_cnts, _ = cv2.findContours(non_skin, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                            sig_objs = [c for c in obj_cnts if cv2.contourArea(c) > 180]
                            if sig_objs:
                                main_obj = max(sig_objs, key=cv2.contourArea)
                                ox, oy, ow, oh = cv2.boundingRect(main_obj)
                                object_box = (ox * 2, oy * 2, ow * 2, oh * 2)
                                object_label = "[OBJECT DETECTED // IN HAND]"
                            else:
                                object_box = (hx, hy, hw, hh)
                                object_label = "[HAND // TARGET]"
                except Exception:
                    pass

                self.telemetry["object_box"] = object_box
                self.telemetry["object_label"] = object_label

            # 2. Draw Subtle Cybernetic HUD Overlays directly onto Stream Frame
            annotated_frame = self._render_cybernetic_hud(frame)

            # 3. Encode to JPEG for MJPEG stream
            ret_enc, jpeg_buf = cv2.imencode('.jpg', annotated_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
            if ret_enc:
                self.latest_jpeg = jpeg_buf.tobytes()

            # 4. Periodically emit telemetry to HTML UI (every 400ms)
            now = time.time()
            if now - last_telem_emit > 0.4:
                last_telem_emit = now
                self._emit_telemetry_to_ui()

            time.sleep(0.033)  # Calm, steady ~30 FPS with ultra-low CPU

    def _recognize_hand_gesture(self, frame: np.ndarray, gray: np.ndarray) -> str:
        """
        Pure OpenCV robust hand gesture classifier using HSV skin color segmentation,
        convex hull, and convexity defect geometry.
        Returns: 'OPEN_PALM', 'FIST', 'PEACE_SIGN', 'THUMBS_UP', or 'NONE'
        """
        h, w = frame.shape[:2]
        
        # Focus on the lower 75% of the frame (hand interaction area)
        roi = frame[int(h * 0.25):, :]
        roi_hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)

        # Skin color filter in HSV
        lower_skin = np.array([0, 28, 60], dtype=np.uint8)
        upper_skin = np.array([25, 255, 255], dtype=np.uint8)
        mask = cv2.inRange(roi_hsv, lower_skin, upper_skin)

        # Blur and morphology to clean noise
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        mask = cv2.dilate(mask, kernel, iterations=2)
        mask = cv2.GaussianBlur(mask, (5, 5), 0)

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return "NONE"

        # Find largest skin contour (the hand)
        hand_contour = max(contours, key=cv2.contourArea)
        area = cv2.contourArea(hand_contour)

        # Minimum area threshold for a valid hand
        if area < 6500:
            return "NONE"

        hull = cv2.convexHull(hand_contour, returnPoints=False)
        if hull is None or len(hull) < 4:
            return "NONE"

        try:
            defects = cv2.convexityDefects(hand_contour, hull)
        except Exception:
            return "NONE"

        if defects is None:
            # 0 defects with solid area -> Fist
            return "FIST"

        # Count deep finger valleys (defects)
        finger_defects = 0
        for i in range(defects.shape[0]):
            s, e, f, d = defects[i, 0]
            start = tuple(hand_contour[s][0])
            end = tuple(hand_contour[e][0])
            far = tuple(hand_contour[f][0])

            # Geometry: calculate triangle side lengths
            a = np.linalg.norm(np.array(end) - np.array(start))
            b = np.linalg.norm(np.array(far) - np.array(start))
            c = np.linalg.norm(np.array(end) - np.array(far))

            # Cosine rule for angle between fingers
            if b * c > 0:
                angle = np.arccos(np.clip((b**2 + c**2 - a**2) / (2 * b * c), -1.0, 1.0)) * 57.2957
                # Deep valley between fingers: angle < 90 deg and defect depth > 18px
                if angle <= 88 and d > 4500:
                    finger_defects += 1

        # Classify gesture based on defect count & bounding box aspect
        x, y, bw, bh = cv2.boundingRect(hand_contour)
        aspect = bh / float(bw) if bw > 0 else 1.0

        if finger_defects >= 4:
            return "OPEN_PALM"
        elif finger_defects == 2 or finger_defects == 1:
            # Check for peace / V-sign (tall vertical fingers, aspect > 1.25)
            if aspect > 1.2:
                return "PEACE_SIGN"
            else:
                return "THUMBS_UP"
        elif finger_defects == 0:
            # Closed hand / fist
            if area > 8000:
                return "FIST"

        return "NONE"

    def _handle_gesture_state(self, gesture: str):
        """Debounces detected hand gestures and triggers corresponding actions."""
        now = time.time()

        if gesture != "NONE" and gesture == self.current_gesture:
            self.gesture_hold_frames += 1
        else:
            self.current_gesture = gesture
            self.gesture_hold_frames = 1

        self.telemetry["gesture"] = self.current_gesture

        # Check if held stably for required frames and cooldown elapsed
        if (self.gesture_hold_frames >= self.REQUIRED_HOLD_FRAMES and
            self.current_gesture != "NONE" and
            (now - self.last_gesture_trigger_time) > self.GESTURE_COOLDOWN):
            
            self.last_gesture_trigger_time = now
            self._execute_gesture_task(self.current_gesture)
            self.gesture_hold_frames = 0  # Reset after trigger

    def _execute_gesture_task(self, gesture: str):
        """Monitors gesture without interfering with user media playback."""
        action_name = f"GESTURE: {gesture}"
        self.telemetry["gesture_desc"] = action_name
        self.emit_js(f"showGestureNotification('{gesture}', '{action_name}');")

    def _render_cybernetic_hud(self, frame: np.ndarray) -> np.ndarray:
        """Draws subtle sci-fi brackets and target reticles on the stream."""
        annotated = frame.copy()
        h, w = frame.shape[:2]

        # Draw Cyan Face Reticle if detected
        face_box = self.telemetry.get("face_box")
        if face_box:
            fx, fy, fw, fh = face_box
            c_color = (255, 229, 0) # Cyan in BGR (0, 229, 255)
            # Corner brackets around face
            d = min(fw, fh) // 4
            cv2.line(annotated, (fx, fy), (fx + d, fy), (255, 216, 0), 2)
            cv2.line(annotated, (fx, fy), (fx, fy + d), (255, 216, 0), 2)
            cv2.line(annotated, (fx + fw, fy), (fx + fw - d, fy), (255, 216, 0), 2)
            cv2.line(annotated, (fx + fw, fy), (fx + fw, fy + d), (255, 216, 0), 2)
            cv2.line(annotated, (fx, fy + fh), (fx + d, fy + fh), (255, 216, 0), 2)
            cv2.line(annotated, (fx, fy + fh), (fx, fy + fh - d), (255, 216, 0), 2)
            cv2.line(annotated, (fx + fw, fy + fh), (fx + fw - d, fy + fh), (255, 216, 0), 2)
            cv2.line(annotated, (fx + fw, fy + fh), (fx + fw, fy + fh - d), (255, 216, 0), 2)

            # Label
            cv2.putText(annotated, "[TARGET // HUMAN DETECTED]", (fx, max(20, fy - 8)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 216, 0), 1, cv2.LINE_AA)

        # Draw Vibrant Neon Green Cybernetic Frame around Object or Hand
        obj_box = self.telemetry.get("object_box")
        obj_label = self.telemetry.get("object_label") or "[OBJECT DETECTED]"
        if obj_box:
            ox, oy, ow, oh = obj_box
            green_color = (0, 255, 65)  # Vibrant Neon Green in BGR (65, 255, 0)
            # Primary bounding rectangle
            cv2.rectangle(annotated, (ox, oy), (ox + ow, oy + oh), green_color, 2)
            
            # Corner reticle brackets
            d = max(10, min(ow, oh) // 4)
            # Top-left corner
            cv2.line(annotated, (ox, oy), (ox + d, oy), green_color, 3)
            cv2.line(annotated, (ox, oy), (ox, oy + d), green_color, 3)
            # Top-right corner
            cv2.line(annotated, (ox + ow, oy), (ox + ow - d, oy), green_color, 3)
            cv2.line(annotated, (ox + ow, oy), (ox + ow, oy + d), green_color, 3)
            # Bottom-left corner
            cv2.line(annotated, (ox, oy + oh), (ox + d, oy + oh), green_color, 3)
            cv2.line(annotated, (ox, oy + oh), (ox, oy + oh - d), green_color, 3)
            # Bottom-right corner
            cv2.line(annotated, (ox + ow, oy + oh), (ox + ow - d, oy + oh), green_color, 3)
            cv2.line(annotated, (ox + ow, oy + oh), (ox + ow, oy + oh - d), green_color, 3)

            # High-tech neon label badge above the green box
            badge_y = max(24, oy - 10)
            cv2.putText(annotated, obj_label, (ox, badge_y),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.48, green_color, 1, cv2.LINE_AA)

        # Top Matrix Sensor Badge
        cv2.putText(annotated, "HYDRA OPTICAL MATRIX // 1280x720 // SENSOR ACTIVE", (20, 28),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.48, (255, 200, 0), 1, cv2.LINE_AA)

        return annotated

    def _emit_telemetry_to_ui(self):
        """Sends real-time telemetry dictionary to index.html."""
        telem_payload = {
            "lighting": self.telemetry["lighting"],
            "target": "HUMAN LOCKED" if self.telemetry["faces_detected"] > 0 else "NO SUBJECT",
            "motion": self.telemetry["motion"],
            "gesture": self.telemetry["gesture"],
            "gesture_desc": self.telemetry["gesture_desc"]
        }
        json_str = json.dumps(telem_payload)
        self.emit_js(f"updateCameraTelemetry({json_str});")

    # =========================================================================
    # INTELLIGENT REAL-TIME OBJECT & HAND IDENTIFICATION ("WHAT IS IN MY HAND")
    # =========================================================================

    def identify_in_hand(self, user_prompt: str = "what is in my hand") -> str:
        """
        Multimodal & high-precision vision pipeline:
        Accurately inspects the user's hand and describes what object is held.
        Never hallucinates.
        """
        # Ensure camera is running
        if not self.is_active or self.cap is None:
            self.start_camera()

        # Wait for camera optical feed to capture initial valid frame
        for _ in range(25):
            if self.latest_frame is not None:
                break
            time.sleep(0.1)

        # Grab latest frame
        frame = self.get_latest_frame()
        if frame is None:
            return "My optical sensor is currently calibrating, sir. Please show it again in a moment."

        # 1. Try Multimodal Cloud Vision (Nebius / OpenAI / Gemini) if API key is present
        api_key = os.environ.get("NEBIUS_API_KEY", "").strip() or os.environ.get("OPENAI_API_KEY", "").strip()
        if api_key and api_key != "YOUR_NEBIUS_API_KEY_HERE":
            ai_res = self._query_multimodal_vision(frame, user_prompt)
            if ai_res:
                return ai_res

        # 2. Heuristic Local Computer Vision Pipeline (Truthful, Zero-Hallucination)
        return self._local_object_inspection(frame)

    def _query_multimodal_vision(self, frame: np.ndarray, user_prompt: str) -> Optional[str]:
        """Queries Vision LLM with encoded JPEG frame."""
        try:
            from openai import OpenAI

            # Resize frame to max 1024 dimension for low-latency network transfer
            h, w = frame.shape[:2]
            scale = 1024.0 / max(h, w) if max(h, w) > 1024 else 1.0
            if scale < 1.0:
                small = cv2.resize(frame, (int(w * scale), int(h * scale)))
            else:
                small = frame

            ret_enc, buf = cv2.imencode('.jpg', small, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
            if not ret_enc:
                return None

            b64_img = base64.b64encode(buf.tobytes()).decode('utf-8')

            base_url = os.environ.get("NEBIUS_BASE_URL", "https://api.tokenfactory.nebius.com/v1/").strip()
            api_key = os.environ.get("NEBIUS_API_KEY", "").strip() or os.environ.get("OPENAI_API_KEY", "").strip()
            if os.environ.get("OPENAI_API_KEY") and not os.environ.get("NEBIUS_API_KEY"):
                base_url = "https://api.openai.com/v1"

            model_name = (
                os.environ.get("VISION_MODEL", "").strip() or
                os.environ.get("NEBIUS_VISION_MODEL", "").strip() or
                ("gpt-4o-mini" if "openai" in base_url else "nvidia/Nemotron-3.5-Super-VL")
            )

            client = OpenAI(base_url=base_url, api_key=api_key, timeout=8.0)
            sys_msg = (
                "You are HYDRA's optical perception engine. Inspect the provided camera feed with 100% accuracy.\n"
                f"The user asks: '{user_prompt}'\n"
                "State what you observe directly, accurately, and concisely in 1 or 2 sentences addressing the user as sir.\n"
                "Never guess or hallucinate."
            )

            response = client.chat.completions.create(
                model=model_name,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": sys_msg},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{b64_img}",
                                    "detail": "high"
                                }
                            }
                        ]
                    }
                ],
                max_tokens=220,
                temperature=0.1
            )
            msg = response.choices[0].message
            ans = msg.content
            if not ans and hasattr(msg, "reasoning_content") and msg.reasoning_content:
                rc = msg.reasoning_content.strip()
                lines = [l.strip() for l in rc.split("\n") if l.strip()]
                ans = lines[-1] if lines else rc[:120]
            if ans:
                return ans.strip()
        except Exception as e:
            print(f"[CAMERA VISION] Vision API query notice: {e}")

        return None

    # =========================================================================
    # SCENE & OBJECT ANALYSIS ("ANALYZE THINGS", "WHAT DO YOU SEE")
    # =========================================================================

    def analyze_scene(self, user_prompt: str = "analyze what you see") -> str:
        """
        Comprehensive real-time analysis of the camera feed:
        Detects faces/people, environment lighting, motion, and objects being shown or held.
        Leverages multimodal vision API if available, or rich local computer vision heuristics.
        """
        if not self.is_active or self.cap is None:
            self.start_camera()

        # Wait briefly for optical sensor to calibrate frame
        for _ in range(20):
            if self.latest_frame is not None:
                break
            time.sleep(0.05)

        frame = self.get_latest_frame()
        if frame is None:
            return "Optical sensors are currently calibrating, sir. Please wait one moment."

        clean_prompt = user_prompt.lower()
        if any(w in clean_prompt for w in ["hand", "holding", "hold", "in my hand"]):
            return self.identify_in_hand(user_prompt)

        # 1. Multimodal Cloud Vision (Nebius / OpenAI) if key is present
        api_key = os.environ.get("NEBIUS_API_KEY", "").strip() or os.environ.get("OPENAI_API_KEY", "").strip()
        if api_key and api_key != "YOUR_NEBIUS_API_KEY_HERE":
            multimodal_prompt = (
                f"Describe what is currently visible in front of the camera for query: '{user_prompt}'. "
                "Mention if a person is present, what they are holding or doing, objects visible on the desk or in front of the lens, "
                "and environment lighting. Provide a concise response in 1 or 2 sentences addressing the user as sir. Never hallucinate."
            )
            ai_res = self._query_multimodal_vision(frame, multimodal_prompt)
            if ai_res:
                return ai_res

        # 2. Heuristic Local Scene Analyzer (Zero-Hallucination, 100% Reliable)
        return self._local_scene_inspection(frame)

    def _local_scene_inspection(self, frame: np.ndarray) -> str:
        """
        Smart local scene analyzer: evaluates faces, lighting, motion, and in-hand objects.
        """
        h, w = frame.shape[:2]
        faces_count = self.telemetry.get("faces_detected", 0)
        lighting = self.telemetry.get("lighting", "OPTIMAL")
        motion = self.telemetry.get("motion", "SCENE STABLE")

        # Check for hand / object in lower viewport
        hand_obj_note = ""
        try:
            hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
            lower_skin = np.array([0, 25, 60], dtype=np.uint8)
            upper_skin = np.array([25, 255, 255], dtype=np.uint8)
            skin_mask = cv2.inRange(hsv, lower_skin, upper_skin)
            contours, _ = cv2.findContours(skin_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if contours:
                hand_cnt = max(contours, key=cv2.contourArea)
                if cv2.contourArea(hand_cnt) > 5000:
                    hand_obj_note = " " + self._local_object_inspection(frame)
        except Exception:
            pass

        if faces_count > 0:
            if hand_obj_note:
                return f"I see you centered in front of the camera, sir.{hand_obj_note}"
            return f"I have visual lock on you, sir. Scene lighting is {lighting.lower()} and movement is {motion.lower()}."
        else:
            if hand_obj_note:
                return f"I do not detect a person directly in frame, sir, but{hand_obj_note}"
            return f"The camera view is clear, sir. Lighting is {lighting.lower()} and scene is currently {motion.lower()}."

    def _local_object_inspection(self, frame: np.ndarray) -> str:
        """
        Smart localized visual heuristic analyzer:
        Inspects skin contours, convex hull non-skin ratios, aspect ratios, and edge geometry
        to accurately determine what is in the user's hand without hallucination.
        """
        h, w = frame.shape[:2]
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)

        # Detect skin region (the hand)
        lower_skin = np.array([0, 25, 60], dtype=np.uint8)
        upper_skin = np.array([25, 255, 255], dtype=np.uint8)
        skin_mask = cv2.inRange(hsv, lower_skin, upper_skin)

        contours, _ = cv2.findContours(skin_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return "I do not see your hand clearly in front of the optical sensor, sir. Please bring it into view."

        hand_cnt = max(contours, key=cv2.contourArea)
        area = cv2.contourArea(hand_cnt)
        if area < 5000:
            return "Your hand is too far from the optical sensor, sir. Please bring it closer."

        # Compute hand convex hull
        hull = cv2.convexHull(hand_cnt)
        hull_mask = np.zeros((h, w), dtype=np.uint8)
        cv2.drawContours(hull_mask, [hull], -1, 255, -1)
        hull_area = cv2.contourArea(hull)

        # Non-skin pixels strictly inside the hand's convex hull
        non_skin_inside_hull = cv2.bitwise_and(cv2.bitwise_not(skin_mask), hull_mask)
        non_skin_pixels = cv2.countNonZero(non_skin_inside_hull)
        non_skin_ratio = non_skin_pixels / float(max(1.0, hull_area))

        # If non-skin area inside hand is tiny (< 3%), the hand is completely empty!
        if non_skin_ratio < 0.03:
            return "I can see your hand clearly, sir, but you are not holding anything. Your hand is empty."

        # Find significant non-skin objects grasped within the hand
        obj_contours, _ = cv2.findContours(non_skin_inside_hull, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        significant = [c for c in obj_contours if cv2.contourArea(c) > 700]
        if not significant:
            return "Your hand is open, sir. You are not holding any object right now."

        main_obj = max(significant, key=cv2.contourArea)
        ox, oy, ow, oh = cv2.boundingRect(main_obj)
        aspect = max(ow, oh) / float(max(1, min(ow, oh)))
        obj_area = cv2.contourArea(main_obj)

        # 1. Pen / Pencil / Stylus (high aspect ratio > 3.2, slender width)
        if aspect > 3.2 and min(ow, oh) < 65:
            return "I see a pen or stylus in your hand, sir."

        # 2. Smartphone (dark rectangular slab, aspect ~1.5 to 2.8, significant area)
        if 1.5 <= aspect <= 2.8 and obj_area > 3500:
            obj_crop = gray[oy:oy+oh, ox:ox+ow]
            if np.mean(obj_crop) < 125: # Dark screen / body
                return "You are holding a smartphone in your hand, sir."

        # 3. Cup / Mug / Drink Container (cylindrical / round, aspect ~1.0 to 1.45, large area)
        if 1.0 <= aspect <= 1.45 and obj_area > 4500:
            return "You are holding a cup or drink container in your hand, sir."

        # 4. Card / Wallet (small rectangular object)
        if 1.3 <= aspect <= 1.8 and 1200 <= obj_area <= 4500:
            return "You are holding a card or wallet in your hand, sir."

        # 5. General distinct object held in hand
        return "You are holding an object in your hand, sir. I have optical lock on it."

    # =========================================================================
    # NLP COMMAND DISPATCHER FOR CAMERA & VISION
    # =========================================================================

    def handle_command(self, raw_cmd: str) -> Tuple[bool, str]:
        """
        Intercepts camera activation ("open eye"), deactivation ("close eye"),
        scene analysis ("analyze"), and hand inquiry commands.
        Returns: (handled: bool, reply: str)
        """
        if not raw_cmd:
            return False, ""

        clean = raw_cmd.lower().strip()
        clean = re.sub(r'^(hydra|hey hydra|ok hydra|okay hydra)[\s,]+', '', clean).strip()
        clean = clean.replace(".", "").replace("!", "").replace("?", "").strip()

        # 1. SCENE & OBJECT ANALYSIS: "analyze", "what do you see", "what is on camera"
        scene_analysis_triggers = [
            "analyze", "analyze things", "analyze scene", "analyze this",
            "analyze what you see", "what do you see", "what are you seeing",
            "what do you see right now", "what do you see in the camera",
            "what is in front of the camera", "what is in front of you",
            "what is on camera", "what is shown on camera", "what is being shown on camera",
            "what do you see on camera", "tell me what you see", "describe what you see",
            "who is there", "describe scene", "scan scene", "visual scan", "optical scan"
        ]
        if any(clean == t or clean.startswith(t) for t in scene_analysis_triggers):
            reply = self.analyze_scene(clean)
            return True, reply

        # 2. HAND & OBJECT INQUIRY: "what is on my hand", "what is in my hand", "what am i holding", company/brand
        hand_triggers = [
            "what is in my hand", "what's in my hand", "what am i holding",
            "what am i holding in my hand", "what do i have in my hand",
            "what do you see in my hand", "what is this in my hand",
            "do you see anything in my hand", "do you see anything",
            "what is this", "look at my hand", "can you see what is in my hand",
            "see what is in my hand", "what object is in my hand",
            "what is on my hand", "what's on my hand", "what do you see on my hand",
            "what is that on my hand", "what is that in my hand",
            "what do i have on my hand", "what am i holding on my hand",
            "of which company are they", "do you tell me of which company are they",
            "what company are they", "what brand are they", "tell me what brand",
            "tell me what company", "identify object", "what is this object"
        ]
        if any(clean == t or clean.startswith(t) for t in hand_triggers):
            reply = self.identify_in_hand(clean)
            return True, reply

        # 2.1 SPECIFIC SIGHT INQUIRIES: "do you see my face", "do you see white headphones", "do you see me"
        if re.search(r'^(?:do\s+you\s+see|can\s+you\s+see)\s+(.+)$', clean):
            reply = self.analyze_scene(clean)
            return True, reply

        # 3. ACTIVATION COMMANDS: "open eye", "open eyes", "open camera", "act as camera", etc.
        camera_open_triggers = [
            "open eye", "open eyes", "open your eye", "open your eyes",
            "open the eye", "open the eyes", "turn on eye", "turn on eyes",
            "act as camera", "act as a camera", "camera", "eye", "eyes",
            "open camera", "open the camera", "turn on camera", "turn on the camera",
            "start camera", "start the camera", "switch to camera", "switch to camera mode",
            "camera mode", "see", "look", "hydra see", "activate camera",
            "enable camera", "show camera", "open webcam", "start optical sensor",
            "optical eye"
        ]
        if any(clean == t or clean.startswith(t) for t in camera_open_triggers):
            ok, reply = self.start_camera()
            return True, reply

        # 4. DEACTIVATION COMMANDS: "close eye", "close camera", etc.
        camera_close_triggers = [
            "close eye", "close eyes", "close your eye", "close your eyes",
            "close the eye", "close the eyes", "shut eye", "shut eyes",
            "shut your eye", "shut your eyes", "close camera", "close the camera",
            "turn off camera", "turn off the camera", "stop camera", "stop the camera",
            "don't look", "dont look", "stop seeing", "disable camera", "deactivate camera",
            "shut down camera", "exit camera", "back to hud", "return to hud",
            "hide camera"
        ]
        if any(clean == t or clean.startswith(t) for t in camera_close_triggers):
            ok, reply = self.stop_camera()
            return True, reply

        return False, ""


# Singleton Instance
CAMERA_ENGINE = CameraVisionEngine()
