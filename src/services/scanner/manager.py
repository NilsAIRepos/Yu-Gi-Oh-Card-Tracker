import logging
import threading
import queue
import time
import base64
import asyncio
from typing import Optional, Dict, Any, List, Tuple

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
        self.auto_scan_paused = False

        # Debug State
        self.manual_scan_requested = False
        self.debug_state = {
            "logs": [],
            "captured_image": None,
            "scan_result": "N/A",
            "warped_image": None,
            "ocr_text": None,
            "contour_area": 0,
            "stability": 0
        }

        # Configuration
        self.stability_threshold = 10.0 # Max pixel movement allowed
        self.required_stable_frames = 3 # Reduced for lower FPS
        self.scan_cooldown_frames = 10 # Ignore same card for a bit

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
        self.running = False
        if self.thread:
            self.thread.join(timeout=2.0)
            self.thread = None

        with self.input_queue.mutex:
            self.input_queue.queue.clear()

        self.latest_normalized_contour = None
        logger.info("Scanner stopped")

    def push_frame(self, b64_frame: str):
        """Receives a frame from the client."""
        if not self.running:
            return

        try:
            # Keep only latest frame
            if self.input_queue.full():
                self.input_queue.get_nowait()
            self.input_queue.put_nowait(b64_frame)
        except queue.Full:
            pass

    def trigger_manual_scan(self):
        """Triggers a manual scan on the next frame, bypassing checks."""
        self.auto_scan_paused = True
        self.manual_scan_requested = True
        self._log_debug("Manual Scan Triggered")

    def resume_automatic_scan(self):
        """Resumes automatic scanning and clears debug captured image."""
        self.auto_scan_paused = False
        self.debug_state["captured_image"] = None
        self.debug_state["scan_result"] = "N/A"
        self._log_debug("Automatic Scan Resumed")

    def get_debug_snapshot(self) -> Dict[str, Any]:
        """Returns the current debug state."""
        return self.debug_state.copy()

    def _log_debug(self, message: str):
        """Appends a message to the debug log."""
        timestamp = time.strftime("%H:%M:%S")
        entry = f"[{timestamp}] {message}"
        # Keep last 20 logs
        self.debug_state["logs"] = [entry] + self.debug_state["logs"][:19]

    def get_status(self) -> str:
        return self.status_message

    def get_latest_result(self) -> Optional[Dict[str, Any]]:
        """Returns the latest scanned card data."""
        try:
            return self.result_queue.get_nowait()
        except queue.Empty:
            return None

    def get_latest_notification(self) -> Optional[Tuple[str, str]]:
        """Returns the latest notification (type, message)."""
        try:
            return self.notification_queue.get_nowait()
        except queue.Empty:
            return None

    def get_live_contour(self) -> Optional[List[List[float]]]:
        """Returns the latest detected card contour as normalized coordinates (0.0-1.0)."""
        return self.latest_normalized_contour

    def _worker(self):
        last_frame_time = time.time()

        while self.running:
            try:
                b64_str = self.input_queue.get(timeout=0.5)
                last_frame_time = time.time()
            except queue.Empty:
                # Diagnostics: Check if we are starving for frames
                if time.time() - last_frame_time > 5.0:
                    # If we haven't seen a frame in 5 seconds
                    if self.manual_scan_requested:
                        self._log_debug("WARNING: Manual Scan requested but no video frames received!")
                        self.notification_queue.put(("warning", "No Camera Signal - Cannot Scan"))
                        self.manual_scan_requested = False # Reset to prevent stuck state
                continue

            if self.cooldown > 0:
                self.cooldown -= 1

            try:
                # Skip if paused and no manual request
                if self.auto_scan_paused and not self.manual_scan_requested:
                    continue

                # Decode Base64
                if ',' in b64_str:
                    b64_str = b64_str.split(',')[1]

                img_bytes = base64.b64decode(b64_str)
                nparr = np.frombuffer(img_bytes, np.uint8)
                frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

                if frame is None:
                    continue

                height, width = frame.shape[:2]

                # Fast Detection
                contour = self.scanner.find_card_contour(frame)

                # Update Debug Stats
                self.debug_state["stability"] = self.stable_frames

                # Check for manual trigger
                force_scan = False
                capture_snapshot = False

                if self.manual_scan_requested:
                    self.manual_scan_requested = False
                    capture_snapshot = True  # Always capture on manual

                    # Capture the RAW frame immediately for feedback
                    # This ensures the user sees exactly what they snapped
                    debug_frame_raw = frame.copy()
                    if contour is not None:
                         cv2.drawContours(debug_frame_raw, [contour], -1, (0, 255, 0), 2)

                    _, buffer = cv2.imencode('.jpg', debug_frame_raw)
                    b64_debug = base64.b64encode(buffer).decode('utf-8')
                    self.debug_state["captured_image"] = f"data:image/jpeg;base64,{b64_debug}"

                    if contour is not None:
                         force_scan = True
                         self.debug_state["scan_result"] = "Card Detected"
                         self._log_debug("Manual Scan: Proceeding with contour")
                    else:
                         # FALLBACK LOGIC: Even if no contour, proceed!
                         force_scan = True # Force processing anyway
                         self.debug_state["scan_result"] = "Fallback Mode"
                         self._log_debug("Manual Scan: No contour, using fallback crop")
                         self.status_message = "Processing Manual Capture..."
                         self.notification_queue.put(("info", "Processing Manual Capture..."))

                if contour is not None:
                    area = cv2.contourArea(contour)
                    self.debug_state["contour_area"] = area

                    # Normalize and store contour
                    # contour is shape (4, 1, 2)
                    pts = contour.reshape(4, 2).astype(float)
                    # Normalize x by width, y by height
                    pts[:, 0] /= width
                    pts[:, 1] /= height
                    self.latest_normalized_contour = pts.tolist()

                    # Check Stability
                    if self._check_stability(contour):
                        self.stable_frames += 1
                    else:
                        self.stable_frames = 0
                        # Log instability only occasionally? Or implies movement
                        pass
                else:
                    self.stable_frames = 0
                    self.last_corners = None
                    self.latest_normalized_contour = None
                    self.debug_state["contour_area"] = 0

                # Trigger Processing
                # Trigger if: Stable enough OR Forced
                should_process = (self.stable_frames >= self.required_stable_frames) or force_scan

                if should_process and not self.is_processing and (self.cooldown == 0 or force_scan):
                    self.is_processing = True

                    # If it wasn't manual, we still want a snapshot of the auto-scan success
                    if not capture_snapshot:
                         debug_frame = frame.copy()
                         cv2.drawContours(debug_frame, [contour], -1, (0, 255, 0), 2)
                         _, buffer = cv2.imencode('.jpg', debug_frame)
                         b64_debug = base64.b64encode(buffer).decode('utf-8')
                         self.debug_state["captured_image"] = f"data:image/jpeg;base64,{b64_debug}"

                    self.status_message = "Processing..."
                    self._log_debug(f"Starting Processing (Stable: {self.stable_frames}, Force: {force_scan})")

                    # Run CV tasks in thread
                    # Note: contour might be None if force_scan is True (Manual Fallback)
                    threading.Thread(target=self._cv_scan_task, args=(frame.copy(), contour)).start()

                elif self.is_processing:
                    self.status_message = "Analyzing..."
                elif self.stable_frames > 0:
                    self.status_message = f"Stabilizing: {self.stable_frames}/{self.required_stable_frames}"
                elif not force_scan and not self.auto_scan_paused:
                    self.status_message = "Scanning..."


            except Exception as e:
                logger.error(f"Error in scanner worker: {e}")
                self.status_message = "Error"
                self._log_debug(f"Worker Error: {str(e)}")

    def _check_stability(self, contour) -> bool:
        """Checks if the contour corners have moved significantly."""
        peri = cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, 0.02 * peri, True)

        if len(approx) != 4:
            return False

        corners = approx.reshape(4, 2)
        corners = corners[np.argsort(corners.sum(axis=1))]

        if self.last_corners is None:
            self.last_corners = corners
            return False

        dist = np.max(np.linalg.norm(self.last_corners - corners, axis=1))
        self.last_corners = corners

        return dist < self.stability_threshold

    def _cv_scan_task(self, frame, contour):
        """Phase 1: Heavy CV extraction (Threaded)."""
        try:
            logger.info("Starting CV scan task...")

            # Determine Warped Image: Contour or Fallback
            if contour is not None:
                warped = self.scanner.warp_card(frame, contour)
            else:
                # Fallback for manual scans with no detection
                logger.info("Using fallback crop for processing.")
                warped = self.scanner.get_fallback_crop(frame)

            # Generate Debug Image (Warped + ROIs)
            # This ensures the user sees exactly what the OCR engine is looking at
            debug_img = self.scanner.debug_draw_rois(warped)
            _, buffer = cv2.imencode('.jpg', debug_img)
            b64_debug = base64.b64encode(buffer).decode('utf-8')
            self.debug_state["warped_image"] = f"data:image/jpeg;base64,{b64_debug}"

            # 1. OCR Set ID
            set_id = self.scanner.extract_set_id(warped)
            self._log_debug(f"OCR Result: '{set_id}'")
            self.debug_state["ocr_text"] = set_id

            # 2. Detect Language (Visual/OCR)
            language = self.scanner.detect_language(warped, set_id)

            # 3. Detect 1st Edition
            first_ed = self.scanner.detect_first_edition(warped)

            # 4. Visual Rarity Fallback
            visual_rarity = self.scanner.detect_rarity_visual(warped)

            data = {
                "set_code": set_id,
                "language": language,
                "first_edition": first_ed,
                "rarity": "Unknown", # Will be resolved
                "visual_rarity": visual_rarity,
                "warped_image": warped # Pass warped image for Phase 2 (Art matching)
            }

            self.lookup_queue.put(data)

        except Exception as e:
            logger.error(f"Error in CV scan task: {e}")
            self._log_debug(f"CV Task Error: {str(e)}")
            self.is_processing = False # Reset flag if error

    async def process_pending_lookups(self):
        """Phase 2: Data Lookup & Art Matching (Main Async Loop)."""
        try:
            try:
                data = self.lookup_queue.get_nowait()
            except queue.Empty:
                return

            logger.info(f"Processing lookup for Set ID: {data.get('set_code')}")
            self._log_debug(f"Database Lookup: {data.get('set_code')}")

            set_id = data.get('set_code')
            warped = data.pop('warped_image', None) # Remove from dict to be clean

            # Default name
            data['name'] = "Unknown Card"
            data['card_id'] = None
            data['image_path'] = None

            if set_id:
                # 1. Resolve Card & Download Images
                card_info = await self._resolve_card_details(set_id)

                if card_info:
                    data.update(card_info)
                    self._log_debug(f"Found Card: {card_info.get('name')}")

                    # 2. Match Art (if multiple arts exist and we have the warped image)
                    if warped is not None and card_info.get("potential_art_paths"):
                        self._log_debug("Matching artwork...")
                        # Run ORB in thread to avoid blocking loop
                        match_path = await run.io_bound(
                            self.scanner.match_artwork, warped, card_info["potential_art_paths"]
                        )
                        if match_path:
                            data["image_path"] = match_path
                            logger.info(f"Art matched: {match_path}")
                            self._log_debug("Artwork matched successfully")
                        else:
                            data["image_path"] = card_info["potential_art_paths"][0]
                            self._log_debug("Artwork match failed, using default")
                else:
                    self._log_debug("Card not found in database")

            # Finalize Rarity
            if data["rarity"] == "Unknown":
                data["rarity"] = data["visual_rarity"]

            # Add to result queue
            self.result_queue.put(data)

            # Reset processing flag
            self.is_processing = False
            self.cooldown = self.scan_cooldown_frames
            self._log_debug("Scan cycle complete")

        except Exception as e:
            logger.error(f"Error in process_pending_lookups: {e}")
            self._log_debug(f"Lookup Error: {str(e)}")
            self.is_processing = False

    async def _resolve_card_details(self, set_id: str) -> Optional[Dict[str, Any]]:
        """Finds card in DB and ensures images are downloaded."""
        # Clean ID
        set_id = set_id.upper()

        # 1. Search DB
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

        # 2. Download Images
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
