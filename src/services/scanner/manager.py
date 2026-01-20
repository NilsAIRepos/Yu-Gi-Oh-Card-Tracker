import logging
import threading
import queue
import time
import base64
import asyncio
import os
import uuid
from typing import Optional, Dict, Any, List, Tuple, Union

try:
    import numpy as np
except ImportError:
    np = None

from src.services.scanner import SCANNER_AVAILABLE
# Conditional import for cv2
try:
    import cv2
except ImportError:
    cv2 = None

if SCANNER_AVAILABLE:
    from src.services.scanner.pipeline import CardScanner
else:
    CardScanner = None

from src.services.ygo_api import ygo_service
from src.services.image_manager import image_manager
from nicegui import run

logger = logging.getLogger(__name__)

class ScannerManager:
    def __init__(self):
        self.running = False
        self.scanner = CardScanner() if SCANNER_AVAILABLE else None

        # Queues
        self.input_queue = queue.Queue(maxsize=1) # Frames from Client
        self.lookup_queue = queue.Queue() # From CV Thread -> Main Loop
        self.result_queue = queue.Queue() # From Main Loop -> UI
        self.notification_queue = queue.Queue() # Notifications -> UI

        self.thread: Optional[threading.Thread] = None

        # State
        self.stable_frames = 0
        self.last_corners: Any = None
        self.is_processing = False
        self.cooldown = 0
        self.status_message = "Idle"
        self.latest_normalized_contour: Optional[List[List[float]]] = None
        self.auto_scan_paused = True # Default to True (Manual Mode Only)

        # Sharpness Buffering
        self.stability_buffer: List[Tuple[Any, Any, float]] = []

        # Debug State
        self.manual_scan_requested = False
        self.debug_state = {
            "logs": [],
            "captured_image_url": None,
            "capture_timestamp": 0.0,
            "scan_result": "N/A",
            "warped_image_url": None,
            "ocr_text": None,
            "ocr_conf": 0,
            "contour_area": 0,
            "stability": 0,
            "sharpness": 0.0,
            "crops": {}
        }

        # Configuration
        self.stability_threshold = 10.0
        self.required_stable_frames = 3
        self.scan_cooldown_frames = 10
        self.debug_dir = "debug/scans"
        os.makedirs(self.debug_dir, exist_ok=True)

    def start(self):
        if not SCANNER_AVAILABLE:
            logger.error("Scanner dependencies missing. Cannot start.")
            return

        if self.running:
            return

        self.running = True
        self.thread = threading.Thread(target=self._worker, daemon=True)
        self.thread.start()
        logger.info(f"Scanner started (Client-Side Mode)")

    def stop(self):
        if not self.running:
            return

        self.running = False
        if self.thread:
            self.thread.join(timeout=2.0)
            self.thread = None

        with self.input_queue.mutex:
            self.input_queue.queue.clear()

        self.latest_normalized_contour = None
        self.stability_buffer.clear()
        logger.info("Scanner stopped")

    def set_auto_scan(self, enabled: bool):
        self.auto_scan_paused = not enabled
        if enabled:
            self.debug_state["captured_image_url"] = None
            self.debug_state["scan_result"] = "N/A"
            self._log_debug("Automatic Scan Resumed")
        else:
             self._log_debug("Automatic Scan Paused")

    def push_frame(self, frame_data: Union[str, bytes]):
        """Receives a frame from the client."""
        if not self.running:
            return

        try:
            if self.input_queue.full():
                self.input_queue.get_nowait()
            self.input_queue.put_nowait(frame_data)
        except queue.Full:
            pass

    def trigger_manual_scan(self):
        self.auto_scan_paused = True
        self.manual_scan_requested = True
        self._log_debug("Manual Scan Triggered")

    def resume_automatic_scan(self):
        self.auto_scan_paused = False
        self.debug_state["captured_image_url"] = None
        self.debug_state["scan_result"] = "N/A"
        self._log_debug("Automatic Scan Resumed")

    def get_debug_snapshot(self) -> Dict[str, Any]:
        return self.debug_state.copy()

    def _log_debug(self, message: str):
        timestamp = time.strftime("%H:%M:%S")
        entry = f"[{timestamp}] {message}"
        self.debug_state["logs"] = [entry] + self.debug_state["logs"][:19]

    def get_status(self) -> str:
        return self.status_message

    def get_latest_result(self) -> Optional[Dict[str, Any]]:
        try:
            return self.result_queue.get_nowait()
        except queue.Empty:
            return None

    def get_latest_notification(self) -> Optional[Tuple[str, str]]:
        try:
            return self.notification_queue.get_nowait()
        except queue.Empty:
            return None

    def get_live_contour(self) -> Optional[List[List[float]]]:
        return self.latest_normalized_contour

    def _save_debug_image(self, image, prefix="img") -> str:
        """Saves an image to debug/scans and returns the relative URL."""
        if image is None: return None
        filename = f"{prefix}_{uuid.uuid4().hex[:8]}.jpg"
        path = os.path.join(self.debug_dir, filename)
        cv2.imwrite(path, image, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
        return f"/debug/scans/{filename}"

    async def analyze_static_image(self, image_bytes: bytes) -> Dict[str, Any]:
        """
        Runs the full scanning pipeline on a static image for debugging/validation.
        Returns a comprehensive report.
        """
        if not self.scanner:
            return {"error": "Scanner not available"}

        # Decode
        nparr = np.frombuffer(image_bytes, np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if frame is None:
            return {"error": "Failed to decode image"}

        report = {
            "steps": [],
            "crops": {},
            "results": {}
        }

        # 0. Save Input
        input_url = self._save_debug_image(frame, "input")
        report["input_image_url"] = input_url
        report["steps"].append({"name": "Input Received", "status": "OK", "details": f"Size: {frame.shape}"})

        # 1. Contour
        contour = self.scanner.find_card_contour(frame)
        if contour is None:
            report["steps"].append({"name": "Contour Detection", "status": "FAIL", "details": "No card contour found"})
            # Fallback
            warped = self.scanner.get_fallback_crop(frame)
        else:
            report["steps"].append({"name": "Contour Detection", "status": "OK", "details": f"Area: {cv2.contourArea(contour):.0f}"})
            warped = self.scanner.warp_card(frame, contour)

        # Save Warped
        warped_url = self._save_debug_image(warped, "warped")
        report["warped_image_url"] = warped_url

        # Draw ROIs on Warped for visualization
        roi_viz = self.scanner.debug_draw_rois(warped)
        roi_viz_url = self._save_debug_image(roi_viz, "roi_viz")
        report["roi_viz_url"] = roi_viz_url

        # 2. OCR Set ID
        set_id_crop = self.scanner.get_roi_crop(warped, "set_id")
        report["crops"]["set_id"] = self._save_debug_image(set_id_crop, "crop_set_id")

        set_id, set_id_conf = self.scanner.extract_set_id(warped)
        report["results"]["set_id"] = set_id
        report["results"]["set_id_conf"] = set_id_conf

        if set_id:
             report["steps"].append({"name": "OCR Set ID", "status": "OK", "details": f"ID: {set_id}, Conf: {set_id_conf:.1f}"})
        else:
             report["steps"].append({"name": "OCR Set ID", "status": "WARN", "details": "No ID found"})

        # 3. Language
        lang = self.scanner.detect_language(warped, set_id)
        report["results"]["language"] = lang
        report["steps"].append({"name": "Language Detect", "status": "OK", "details": lang})

        # 4. Lookup
        card_info = None
        if set_id:
             card_info = await self._resolve_card_details(set_id)

        if card_info:
            report["steps"].append({"name": "DB Lookup", "status": "OK", "details": card_info.get("name")})
            report["results"]["card_name"] = card_info.get("name")

            # 5. Art Match
            art_crop = self.scanner.get_roi_crop(warped, "art")
            report["crops"]["art"] = self._save_debug_image(art_crop, "crop_art")

            if card_info.get("potential_art_paths"):
                 match_path, match_score = await run.io_bound(
                    self.scanner.match_artwork, warped, card_info["potential_art_paths"]
                 )
                 report["results"]["match_score"] = match_score
                 if match_path:
                      report["steps"].append({"name": "Art Match", "status": "OK", "details": f"Score: {match_score}"})
                 else:
                      report["steps"].append({"name": "Art Match", "status": "WARN", "details": "No match above threshold"})
        else:
             report["steps"].append({"name": "DB Lookup", "status": "FAIL", "details": "Card not found in DB"})

        return report


    def _calculate_sharpness(self, image, contour=None) -> float:
        if image is None: return 0.0
        try:
            target_area = image
            if contour is not None:
                x, y, w, h = cv2.boundingRect(contour)
                if w > 0 and h > 0:
                    target_area = image[y:y+h, x:x+w]
            gray = cv2.cvtColor(target_area, cv2.COLOR_BGR2GRAY)
            return cv2.Laplacian(gray, cv2.CV_64F).var()
        except Exception as e:
            return 0.0

    def _worker(self):
        while self.running:
            try:
                frame_data = self.input_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            if self.cooldown > 0:
                self.cooldown -= 1

            try:
                if self.auto_scan_paused and not self.manual_scan_requested:
                    continue

                # Decode
                frame = None
                if isinstance(frame_data, bytes):
                    nparr = np.frombuffer(frame_data, np.uint8)
                    frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                elif isinstance(frame_data, str):
                    # Handle base64
                    pass # Simplified for now, client sends bytes usually

                if frame is None: continue

                height, width = frame.shape[:2]
                contour = self.scanner.find_card_contour(frame)

                self.debug_state["stability"] = self.stable_frames

                force_scan = False
                if self.manual_scan_requested:
                    self.manual_scan_requested = False
                    force_scan = True

                    # Save raw capture
                    cap_url = self._save_debug_image(frame, "manual_cap")
                    self.debug_state["captured_image_url"] = cap_url
                    self.debug_state["capture_timestamp"] = time.time()

                    is_black = np.mean(frame) < 10
                    if is_black:
                        force_scan = False
                        self.debug_state["scan_result"] = "ERR: Black Image"
                        self.notification_queue.put(("negative", "Black Image Detected"))
                    elif contour is not None:
                        self.debug_state["scan_result"] = "Card Detected"
                    else:
                        self.debug_state["scan_result"] = "Fallback Mode"

                # Stability
                is_stable = False
                if contour is not None:
                    area = cv2.contourArea(contour)
                    self.debug_state["contour_area"] = area

                    pts = contour.reshape(4, 2).astype(float)
                    pts[:, 0] /= width
                    pts[:, 1] /= height
                    self.latest_normalized_contour = pts.tolist()

                    if self._check_stability(contour):
                        self.stable_frames += 1
                        is_stable = True
                        sharpness = self._calculate_sharpness(frame, contour)
                        self.debug_state["sharpness"] = sharpness
                    else:
                        self.stable_frames = 0
                        self.stability_buffer.clear()
                else:
                    self.stable_frames = 0
                    self.latest_normalized_contour = None
                    self.stability_buffer.clear()

                if is_stable and not force_scan:
                    self.stability_buffer.append((frame, contour, sharpness))
                    if len(self.stability_buffer) > self.required_stable_frames:
                        self.stability_buffer.pop(0)

                should_process = (self.stable_frames == self.required_stable_frames) or force_scan

                if should_process and not self.is_processing and (self.cooldown == 0 or force_scan):
                    self.is_processing = True
                    self.cooldown = self.scan_cooldown_frames

                    target_frame = frame
                    target_contour = contour

                    if not force_scan and self.stability_buffer:
                        best_entry = max(self.stability_buffer, key=lambda x: x[2])
                        target_frame, target_contour, _ = best_entry
                        self.stability_buffer.clear()
                        self.stable_frames = 0

                        # Save auto-capture debug image
                        cap_url = self._save_debug_image(target_frame, "auto_cap")
                        self.debug_state["captured_image_url"] = cap_url
                        self.debug_state["capture_timestamp"] = time.time()

                    self.status_message = "Processing..."
                    threading.Thread(target=self._cv_scan_task, args=(target_frame.copy(), target_contour)).start()

                elif self.is_processing:
                    self.status_message = "Analyzing..."
                elif self.stable_frames > 0:
                    self.status_message = f"Stabilizing: {self.stable_frames}/{self.required_stable_frames}"
                elif not force_scan and not self.auto_scan_paused:
                    self.status_message = "Scanning..."

            except Exception as e:
                logger.error(f"Error in scanner worker: {e}")
                self.status_message = "Error"

    def _check_stability(self, contour) -> bool:
        peri = cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, 0.02 * peri, True)
        if len(approx) != 4: return False
        corners = approx.reshape(4, 2)
        corners = corners[np.argsort(corners.sum(axis=1))]
        if self.last_corners is None:
            self.last_corners = corners
            return False
        dist = np.max(np.linalg.norm(self.last_corners - corners, axis=1))
        self.last_corners = corners
        return dist < self.stability_threshold

    def _cv_scan_task(self, frame, contour):
        try:
            if contour is not None:
                warped = self.scanner.warp_card(frame, contour)
            else:
                warped = self.scanner.get_fallback_crop(frame)

            # Debug Images
            roi_viz = self.scanner.debug_draw_rois(warped)
            self.debug_state["warped_image_url"] = self._save_debug_image(roi_viz, "warped_roi")

            # Save Crops for Debug
            set_id_crop = self.scanner.get_roi_crop(warped, "set_id")
            self.debug_state["crops"]["set_id"] = self._save_debug_image(set_id_crop, "crop_set_id")

            # 1. OCR Set ID
            set_id, set_id_conf = self.scanner.extract_set_id(warped)
            self.debug_state["ocr_text"] = set_id
            self.debug_state["ocr_conf"] = set_id_conf

            # 2. Detect Language
            language = self.scanner.detect_language(warped, set_id)

            # 3. Detect 1st Edition
            first_ed = self.scanner.detect_first_edition(warped)

            # 4. Visual Rarity
            visual_rarity = self.scanner.detect_rarity_visual(warped)

            data = {
                "set_code": set_id,
                "language": language,
                "first_edition": first_ed,
                "rarity": "Unknown",
                "visual_rarity": visual_rarity,
                "warped_image": warped,
                "ocr_conf": set_id_conf
            }

            self.lookup_queue.put(data)

        except Exception as e:
            logger.error(f"Error in CV scan task: {e}")
            self.is_processing = False

    async def process_pending_lookups(self):
        try:
            try:
                data = self.lookup_queue.get_nowait()
            except queue.Empty:
                return

            set_id = data.get('set_code')
            warped = data.pop('warped_image', None)
            ocr_conf = data.pop('ocr_conf', 0)

            data['name'] = "Unknown Card"
            data['card_id'] = None
            data['image_path'] = None

            if set_id:
                card_info = await self._resolve_card_details(set_id)

                if card_info:
                    data.update(card_info)

                    if warped is not None and card_info.get("potential_art_paths"):
                        match_path, match_score = await run.io_bound(
                            self.scanner.match_artwork, warped, card_info["potential_art_paths"]
                        )
                        if match_path:
                            data["image_path"] = match_path
                            data["match_score"] = match_score
                        else:
                            data["image_path"] = card_info["potential_art_paths"][0]
                            data["match_score"] = 0

            if data["rarity"] == "Unknown":
                data["rarity"] = data["visual_rarity"]

            self.result_queue.put(data)
            self.is_processing = False

        except Exception as e:
            logger.error(f"Error in process_pending_lookups: {e}")
            self.is_processing = False

    async def _resolve_card_details(self, set_id: str) -> Optional[Dict[str, Any]]:
        set_id = set_id.upper()
        cards = await ygo_service.load_card_database("en")

        candidates = []
        for card in cards:
            if not card.card_sets: continue
            for s in card.card_sets:
                if s.set_code == set_id:
                    candidates.append((card, s))

        if not candidates:
            return None

        card, card_set = candidates[0]

        potential_paths = []
        if card.card_images:
            for img in card.card_images:
                path = await image_manager.ensure_image(card.id, img.image_url, high_res=True)
                if path:
                    potential_paths.append(path)

        return {
            "name": card.name,
            "card_id": card.id,
            "rarity": card_set.set_rarity,
            "potential_art_paths": potential_paths
        }

scanner_manager = ScannerManager()
