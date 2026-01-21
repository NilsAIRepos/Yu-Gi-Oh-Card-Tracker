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
        self.scan_queue = [] # List of pending scan requests
        self.queue_lock = threading.Lock()

        self.lookup_queue = queue.Queue() # Best result -> DB Lookup
        self.result_queue = queue.Queue() # Finished results -> UI
        self.notification_queue = queue.Queue() # Notifications -> UI

        self.thread: Optional[threading.Thread] = None
        self.instance_id = str(uuid.uuid4())[:6]
        logger.info(f"ScannerManager initialized with ID: {self.instance_id}")

        # State
        self.latest_frame = None
        self.latest_frame_lock = threading.Lock()

        self.is_processing = False
        self.paused = True  # Default to paused (Stopped)
        self.status_message = "Stopped"

        # Debug State
        self.debug_state = {
            "logs": [],
            "queue_len": 0,
            "paused": True,
            "current_step": "Idle",
            "captured_image_url": None,
            "scan_result": "N/A",
            "warped_image_url": None,
            # Results
            "t1_full": None,
            "t1_crop": None,
            "t2_full": None,
            "t2_crop": None,
            # Metadata
            "preprocessing": "classic",
            "active_tracks": []
        }

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
        logger.info("Scanner stopped")

    def push_frame(self, frame_data: Union[str, bytes]):
        """Updates the latest frame buffer."""
        if not self.running: return

        try:
            # Decode immediately to avoid pileup? Or just store bytes?
            # Decoding is CPU intensive. Let's store bytes and decode on demand if needed.
            # But the UI sends it every 200ms.
            with self.latest_frame_lock:
                self.latest_frame = frame_data
        except Exception:
            pass

    def trigger_scan(self, options: Dict[str, Any]):
        """Triggers a scan using the latest frame and provided options."""
        with self.latest_frame_lock:
            if self.latest_frame is None:
                self.notification_queue.put(("warning", "No camera signal"))
                return
            frame_data = self.latest_frame

        self.submit_scan(frame_data, options, label="Manual Capture")

    def submit_scan(self, image_data: bytes, options: Dict[str, Any], label: str = "Manual Scan", filename: str = None):
        """Submits a scan task to the queue."""
        with self.queue_lock:
            self.scan_queue.append({
                "id": str(uuid.uuid4())[:8],
                "timestamp": time.time(),
                "image": image_data,
                "options": options,
                "type": label,
                "filename": filename
            })
        self._log_debug(f"Scan Queued: {label}")

    def pause(self):
        self.paused = True
        self.debug_state["paused"] = True
        self._log_debug("Scanner Paused")

    def resume(self):
        self.paused = False
        self.debug_state["paused"] = False
        self._log_debug("Scanner Resumed")

    def toggle_pause(self):
        if self.paused:
            self.resume()
        else:
            self.pause()

    def is_paused(self) -> bool:
        return self.paused

    def get_queue_snapshot(self) -> List[Dict[str, Any]]:
        with self.queue_lock:
            # Return metadata only
            return [
                {
                    "id": item["id"],
                    "timestamp": item["timestamp"],
                    "type": item.get("type", "Unknown"),
                    "filename": item.get("filename"),
                    "options": item["options"]
                }
                for item in self.scan_queue
            ]

    def remove_scan_request(self, index: int):
        with self.queue_lock:
            if 0 <= index < len(self.scan_queue):
                removed = self.scan_queue.pop(index)
                self._log_debug(f"Removed item {removed['id']} from queue")

    async def analyze_static_image(self, image_bytes: bytes, options: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Runs the pipeline on a static image (Upload or specific capture).
        This is called directly from UI, but we should probably offload it to the worker
        or run it in a thread to avoid blocking.
        For now, let's run it via run.io_bound wrapper around the sync pipeline.
        """
        if options is None:
            options = {"tracks": ["easyocr", "paddle"], "preprocessing": "classic"}

        return await run.io_bound(self._run_pipeline_sync, image_bytes, options)

    def _run_pipeline_sync(self, image_bytes, options):
        # Decode
        nparr = np.frombuffer(image_bytes, np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if frame is None: return {"error": "Decode failed"}

        return self._process_scan(frame, options)

    def get_debug_snapshot(self) -> Dict[str, Any]:
        with self.queue_lock:
            self.debug_state["queue_len"] = len(self.scan_queue)
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
        # We removed the continuous contour loop.
        # If we want live overlay, we'd need to re-enable the loop.
        # Given "Remove auto scan", and the focus on manual capture,
        # we will return None for now or we could implement a lightweight contour check.
        return None

    def _save_debug_image(self, image, prefix="img") -> str:
        if image is None: return None
        filename = f"{prefix}_{uuid.uuid4().hex[:8]}.jpg"
        path = os.path.join(self.debug_dir, filename)
        cv2.imwrite(path, image, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
        return f"/debug/scans/{filename}"

    def _worker(self):
        logger.info(f"Scanner worker thread started (Manager ID: {self.instance_id})")
        while self.running:
            try:
                if self.paused:
                    self.status_message = "Paused"
                    time.sleep(0.1)
                    continue

                task = None
                with self.queue_lock:
                    if self.scan_queue:
                        task = self.scan_queue.pop(0)

                if not task:
                    self.status_message = "Idle"
                    time.sleep(0.1)
                    continue

                filename = task.get("filename", "unknown")
                self.is_processing = True
                self.status_message = f"Processing: {filename}"
                logger.info(f"Starting scan for: {filename}")
                self._log_debug(f"Started: {filename}")

                try:
                    frame_data = task["image"]
                    options = task["options"]

                    # Decode
                    nparr = np.frombuffer(frame_data, np.uint8)
                    frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

                    if frame is not None:
                        # Update debug state basics
                        cap_url = self._save_debug_image(frame, "manual_cap")
                        self.debug_state["captured_image_url"] = cap_url
                        self.debug_state["preprocessing"] = options.get("preprocessing", "classic")
                        self.debug_state["active_tracks"] = options.get("tracks", [])

                        # Define status updater
                        def update_step(step_name):
                            self.debug_state["current_step"] = step_name
                            self.status_message = f"Processing: {filename} ({step_name})"

                        # Run Pipeline
                        report = self._process_scan(frame, options, status_cb=update_step)

                        # Merge report into debug state
                        self.debug_state.update(report)

                        # If we found a card, push to result queue
                        # We pick the best result.
                        best_res = self._pick_best_result(report)
                        if best_res:
                            # Enhance with visual traits if warped image exists
                            warped = None
                            # Construct lookup data
                            lookup_data = {
                                "set_code": best_res['set_id'],
                                "language": best_res['language'],
                                "ocr_conf": best_res['set_id_conf'],
                                "rarity": "Unknown",
                                "visual_rarity": report.get('visual_rarity', 'Common'),
                                "first_edition": report.get('first_edition', False),
                                "warped_image": report.get('warped_image_data')
                            }
                            self.lookup_queue.put(lookup_data)

                        logger.info(f"Finished scan for: {filename}")
                        self._log_debug(f"Finished: {filename}")
                    else:
                        self._log_debug("Frame decode failed")

                except Exception as e:
                    logger.error(f"Task Execution Error: {e}", exc_info=True)
                    self._log_debug(f"Error: {str(e)}")

            except Exception as e:
                logger.error(f"Worker Loop Fatal Error: {e}", exc_info=True)
                self.status_message = "Error"
                time.sleep(1.0) # Prevent tight loop on crash
            finally:
                self.is_processing = False
                self.debug_state["current_step"] = "Idle"
                if not self.paused:
                     self.status_message = "Idle"

    def _process_scan(self, frame, options, status_cb=None) -> Dict[str, Any]:
        """Runs the configured tracks on the frame."""
        if not self.scanner: return {}

        def set_step(msg):
            if status_cb: status_cb(msg)

        report = {
            "steps": [],
            "results": {},
            "t1_full": None, "t1_crop": None,
            "t2_full": None, "t2_crop": None
        }

        # 1. Preprocessing (Crop)
        set_step("Preprocessing: Contour/Crop")
        prep_method = options.get("preprocessing", "classic")
        contour = None
        warped = None

        if prep_method == "yolo":
             contour = self.scanner.find_card_yolo(frame)
        else:
             contour = self.scanner.find_card_contour(frame)

        if contour is not None:
             warped = self.scanner.warp_card(frame, contour)
             report["warped_image_url"] = self._save_debug_image(warped, "warped")
             roi_viz = self.scanner.debug_draw_rois(warped)
             report["roi_viz_url"] = self._save_debug_image(roi_viz, "roi_viz")
        else:
             report["steps"].append({"name": "Contour", "status": "FAIL", "details": f"{prep_method} failed"})
             warped = self.scanner.get_fallback_crop(frame) # Fallback for crop tracks

        tracks = options.get("tracks", ["easyocr"]) # ['easyocr', 'paddle']

        # 2. Run Tracks
        # Track 1: EasyOCR
        if "easyocr" in tracks:
            try:
                set_step("Track 1: EasyOCR (Full)")
                # Full Frame
                t1_full = self.scanner.ocr_scan(frame, engine='easyocr')
                t1_full['scope'] = 'full'
                report["t1_full"] = t1_full

                set_step("Track 1: EasyOCR (Crop)")
                # Crop
                if warped is not None:
                    t1_crop = self.scanner.ocr_scan(warped, engine='easyocr')
                    t1_crop['scope'] = 'crop'
                    report["t1_crop"] = t1_crop
            except Exception as e:
                logger.error(f"Track 1 (EasyOCR) Failed: {e}")
                report["steps"].append({"name": "Track 1", "status": "FAIL", "details": str(e)})

        # Track 2: PaddleOCR
        if "paddle" in tracks:
            try:
                set_step("Track 2: PaddleOCR (Full)")
                # Full Frame
                t2_full = self.scanner.ocr_scan(frame, engine='paddle')
                t2_full['scope'] = 'full'
                report["t2_full"] = t2_full

                set_step("Track 2: PaddleOCR (Crop)")
                # Crop
                if warped is not None:
                    t2_crop = self.scanner.ocr_scan(warped, engine='paddle')
                    t2_crop['scope'] = 'crop'
                    report["t2_crop"] = t2_crop
            except Exception as e:
                logger.error(f"Track 2 (PaddleOCR) Failed: {e}")
                report["steps"].append({"name": "Track 2", "status": "FAIL", "details": str(e)})

        # Extra Analysis on Warped (if available)
        if warped is not None:
             set_step("Analysis: Visual Features")
             report['visual_rarity'] = self.scanner.detect_rarity_visual(warped)
             report['first_edition'] = self.scanner.detect_first_edition(warped)
             report['warped_image_data'] = warped # Pass along for Art Match

        return report

    def _pick_best_result(self, report):
        """Heuristic to pick the best result from the 4 zones."""
        candidates = []
        for key in ["t1_full", "t1_crop", "t2_full", "t2_crop"]:
            res = report.get(key)
            if res and res.get('set_id'):
                candidates.append(res)

        if not candidates: return None
        # Sort by confidence
        candidates.sort(key=lambda x: x['set_id_conf'], reverse=True)
        return candidates[0]

    async def process_pending_lookups(self):
        try:
            try:
                data = self.lookup_queue.get_nowait()
            except queue.Empty:
                return

            logger.info(f"Processing lookup for {data.get('set_code', 'Unknown')}")

            set_id = data.get('set_code')
            warped = data.pop('warped_image', None)

            data['name'] = "Unknown Card"
            data['card_id'] = None
            data['image_path'] = None

            if set_id:
                # Wrap heavy DB/IO in io_bound if not already async-optimized
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
            logger.info(f"Lookup complete for {data.get('set_code')}")

        except Exception as e:
            logger.error(f"Error in process_pending_lookups: {e}", exc_info=True)

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
