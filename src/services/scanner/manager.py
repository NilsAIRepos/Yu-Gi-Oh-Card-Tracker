import logging
import threading
import queue
import time
import base64
import asyncio
import os
import uuid
import shutil
from typing import Optional, Dict, Any, List, Tuple, Union, Callable

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
    from src.services.scanner.models import (
        ScanRequest, ScanResult, ScanDebugReport, OCRResult, ScanStep, ScanEvent
    )
else:
    CardScanner = None
    # Dummy models if needed, but we check availability first
    # Need to define dummies for type hinting if module not avail
    class ScanEvent: pass
    class ScanRequest: pass
    class ScanDebugReport: pass

from src.services.ygo_api import ygo_service
from src.services.image_manager import image_manager
from nicegui import run

logger = logging.getLogger(__name__)

class ScanAborted(Exception):
    pass

class ScannerManager:
    def __init__(self):
        self.running = False
        self.scanner = CardScanner() if SCANNER_AVAILABLE else None

        # Queues
        self.scan_queue: List[ScanRequest] = []
        self.queue_lock = threading.Lock()

        self.lookup_queue = queue.Queue() # Best result -> DB Lookup
        self.result_queue = queue.Queue() # Finished results (ScanResult) -> UI
        self.notification_queue = queue.Queue() # Notifications -> UI

        self.thread: Optional[threading.Thread] = None
        self.instance_id = str(uuid.uuid4())[:6]
        logger.info(f"ScannerManager initialized with ID: {self.instance_id}")

        # State
        self.is_processing = False
        self.paused = True  # Default to paused (Stopped)
        self.status_message = "Stopped"

        # Debug State (using Model)
        self.debug_state = ScanDebugReport() if SCANNER_AVAILABLE else None

        # Event System
        self.listeners: List[Callable[[ScanEvent], None]] = []
        self.listeners_lock = threading.Lock()

        self.debug_dir = "debug/scans"
        self.queue_dir = "scans/queue"
        os.makedirs(self.debug_dir, exist_ok=True)
        os.makedirs(self.queue_dir, exist_ok=True)

    def register_listener(self, callback: Callable[["ScanEvent"], None]):
        """Registers a callback for scanner events."""
        with self.listeners_lock:
            if callback not in self.listeners:
                self.listeners.append(callback)

    def unregister_listener(self, callback: Callable[["ScanEvent"], None]):
        with self.listeners_lock:
            if callback in self.listeners:
                self.listeners.remove(callback)

    def _emit(self, event_type: str, data: Dict[str, Any] = {}):
        """Emits an event to all listeners."""
        if not SCANNER_AVAILABLE: return

        # Include current snapshot
        snapshot = self.debug_state.model_copy()
        with self.queue_lock:
            snapshot.queue_len = len(self.scan_queue)

        event = ScanEvent(type=event_type, data=data, snapshot=snapshot)

        with self.listeners_lock:
            for listener in self.listeners:
                try:
                    listener(event)
                except Exception as e:
                    logger.error(f"Error in event listener: {e}")

    def start(self):
        if not SCANNER_AVAILABLE:
            logger.error("Scanner dependencies missing. Cannot start.")
            return

        if self.running and self.thread and self.thread.is_alive():
            return

        self.running = True
        self.thread = threading.Thread(target=self._worker, daemon=True)
        self.thread.start()
        logger.info(f"Scanner started (Client-Side Mode)")
        self._emit("status_update", {"status": "Started"})

    def stop(self):
        if not self.running:
            return

        self.running = False
        if self.thread:
            self.thread.join(timeout=2.0)
            self.thread = None
        logger.info("Scanner stopped")
        self._emit("status_update", {"status": "Stopped"})

    def submit_scan(self, image_data: bytes, options: Dict[str, Any], label: str = "Manual Scan", filename: str = None):
        """Submits a scan task to the queue."""
        if not filename:
             filename = f"scan_{int(time.time())}_{uuid.uuid4().hex[:6]}.jpg"

        filepath = os.path.join(self.queue_dir, filename)

        # Save file to disk
        try:
            with open(filepath, "wb") as f:
                f.write(image_data)
        except Exception as e:
            logger.error(f"Failed to write scan file {filepath}: {e}")
            self.notification_queue.put(("negative", f"Failed to save scan: {e}"))
            return

        if SCANNER_AVAILABLE:
            request = ScanRequest(
                id=str(uuid.uuid4())[:8],
                timestamp=time.time(),
                filepath=filepath,
                options=options,
                type=label,
                filename=filename
            )

            with self.queue_lock:
                self.scan_queue.append(request)

            self._log_debug(f"Scan Queued: {label}")
            self._emit("scan_queued", {"filename": filename})

    def pause(self):
        self.paused = True
        if self.debug_state: self.debug_state.paused = True
        self._log_debug("Scanner Paused")
        self._emit("status_update", {"paused": True})

    def resume(self):
        self.paused = False
        if self.debug_state: self.debug_state.paused = False
        self._log_debug("Scanner Resumed")
        self._emit("status_update", {"paused": False})

    def toggle_pause(self):
        if self.paused:
            self.resume()
        else:
            self.pause()

    def is_paused(self) -> bool:
        return self.paused

    def get_queue_snapshot(self) -> List[Dict[str, Any]]:
        with self.queue_lock:
            if SCANNER_AVAILABLE:
                return [req.model_dump() for req in self.scan_queue]
            return []

    def remove_scan_request(self, index: int):
        with self.queue_lock:
            if 0 <= index < len(self.scan_queue):
                removed = self.scan_queue.pop(index)
                filepath = removed.filepath
                if filepath and os.path.exists(filepath):
                    try:
                        os.remove(filepath)
                    except OSError:
                        pass
                self._log_debug(f"Removed item {removed.id} from queue")
                self._emit("status_update", {"queue_len": len(self.scan_queue)})

    def get_debug_snapshot(self) -> Dict[str, Any]:
        with self.queue_lock:
            if self.debug_state:
                self.debug_state.queue_len = len(self.scan_queue)
                return self.debug_state.model_dump()
            return {}

    def _log_debug(self, message: str):
        if not self.debug_state: return
        timestamp = time.strftime("%H:%M:%S")
        entry = f"[{timestamp}] {message}"
        # Prepend log, keep last 20
        self.debug_state.logs.insert(0, entry)
        if len(self.debug_state.logs) > 20:
             self.debug_state.logs = self.debug_state.logs[:20]

    def get_status(self) -> str:
        return self.status_message

    def get_latest_result(self) -> Optional[Dict[str, Any]]:
        # Returns dict for UI consumption
        try:
            res = self.result_queue.get_nowait()
            if hasattr(res, 'model_dump'):
                return res.model_dump()
            return res
        except queue.Empty:
            return None

    def get_latest_notification(self) -> Optional[Tuple[str, str]]:
        try:
            return self.notification_queue.get_nowait()
        except queue.Empty:
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
                # 1. Check Paused
                if self.paused:
                    if self.status_message != "Paused":
                        self.status_message = "Paused"
                        self._emit("status_update", {"status": "Paused"})
                    time.sleep(0.1)
                    continue

                # 2. Peek Queue
                task: Optional[ScanRequest] = None
                with self.queue_lock:
                    if self.scan_queue:
                        task = self.scan_queue[0] # Peek

                if not task:
                    if self.status_message != "Idle":
                        self.status_message = "Idle"
                        self._emit("status_update", {"status": "Idle"})
                    time.sleep(0.1)
                    continue

                filename = task.filename
                filepath = task.filepath

                # Check if file exists
                if not filepath or not os.path.exists(filepath):
                     logger.error(f"File not found: {filepath}")
                     with self.queue_lock:
                         if self.scan_queue and self.scan_queue[0] == task:
                             self.scan_queue.pop(0)
                     self._emit("status_update", {})
                     continue

                self.is_processing = True
                self.status_message = f"Processing: {filename}"
                logger.info(f"Starting scan for: {filename}")
                self._log_debug(f"Started: {filename}")
                self._emit("scan_started", {"filename": filename})

                try:
                    # Load Image
                    frame = cv2.imread(filepath)

                    if frame is not None:
                        # Update debug state basics
                        cap_url = self._save_debug_image(frame, "manual_cap")

                        # Reset previous results to avoid confusion
                        self.debug_state.t1_full = None
                        self.debug_state.t1_crop = None
                        self.debug_state.t2_full = None
                        self.debug_state.t2_crop = None
                        self.debug_state.warped_image_url = None
                        self.debug_state.roi_viz_url = None

                        self.debug_state.captured_image_url = cap_url
                        self.debug_state.preprocessing = task.options.get("preprocessing", "classic")
                        self.debug_state.active_tracks = task.options.get("tracks", [])

                        self._emit("step_complete", {"step": "init"})

                        # Define status updater
                        def update_step(step_name):
                            self.debug_state.current_step = step_name
                            self.status_message = f"Processing: {filename} ({step_name})"
                            self._emit("step_complete", {"step": step_name})

                        # Run Pipeline
                        report = self._process_scan(frame, task.options, status_cb=update_step)

                        # Merge report into debug state
                        logger.info(f"Scan Report Merged: {list(report.keys())}")

                        for key, value in report.items():
                             if hasattr(self.debug_state, key):
                                 setattr(self.debug_state, key, value)

                        # If we found a card, push to result queue
                        # We pick the best result.
                        best_res = self._pick_best_result(report)
                        if best_res:
                            # Enhance with visual traits if warped image exists
                            warped = report.get('warped_image_data') # Not in model, but returned by _process_scan

                            # Construct lookup data (Internal structure for LookupQueue)
                            lookup_data = {
                                "set_code": best_res.set_id,
                                "language": best_res.language,
                                "ocr_conf": best_res.set_id_conf,
                                "rarity": "Unknown",
                                "visual_rarity": report.get('visual_rarity', 'Common'),
                                "first_edition": report.get('first_edition', False),
                                "warped_image": warped
                            }
                            self.lookup_queue.put(lookup_data)

                        logger.info(f"Finished scan for: {filename}")
                        self._log_debug(f"Finished: {filename}")

                        # Remove from queue and delete file ON SUCCESS (or clean fail)
                        with self.queue_lock:
                             if self.scan_queue and self.scan_queue[0].id == task.id:
                                 self.scan_queue.pop(0)

                        try:
                            os.remove(filepath)
                        except OSError:
                            pass

                        self._emit("scan_finished", {"success": True})

                    else:
                        self._log_debug(f"Frame read failed for {filepath}")
                        # Remove bad file
                        with self.queue_lock:
                             if self.scan_queue and self.scan_queue[0].id == task.id:
                                 self.scan_queue.pop(0)
                        self._emit("scan_finished", {"success": False, "error": "Frame read failed"})

                except ScanAborted:
                    logger.info(f"Scan aborted for: {filename}")
                    self._log_debug(f"Aborted: {filename}")
                    self.status_message = "Paused"
                    self._emit("status_update", {"status": "Paused"})
                    # Do NOT remove from queue.
                    # Do NOT delete file.

                except Exception as e:
                    logger.error(f"Task Execution Error: {e}", exc_info=True)
                    self._log_debug(f"Error: {str(e)}")
                    # On error, remove to prevent infinite loop
                    with self.queue_lock:
                        if self.scan_queue and self.scan_queue[0].id == task.id:
                            self.scan_queue.pop(0)
                    self._emit("scan_finished", {"success": False, "error": str(e)})

            except Exception as e:
                logger.error(f"Worker Loop Fatal Error: {e}", exc_info=True)
                self.status_message = "Error"
                self._emit("error", {"message": str(e)})
                time.sleep(1.0) # Prevent tight loop on crash
            finally:
                self.is_processing = False
                if self.debug_state: self.debug_state.current_step = "Idle"
                if not self.paused:
                     self.status_message = "Idle"
                     # Final idle emit will happen at top of loop

    def _process_scan(self, frame, options, status_cb=None) -> Dict[str, Any]:
        """Runs the configured tracks on the frame."""
        if not self.scanner: return {}

        def check_pause():
            if self.paused:
                raise ScanAborted()

        def set_step(msg):
            if status_cb: status_cb(msg)

        check_pause()

        # Temporary dict to hold results (kept for return value)
        report = {
            "steps": [],
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

        check_pause()

        if contour is not None:
             warped = self.scanner.warp_card(frame, contour)
             url = self._save_debug_image(warped, "warped")
             report["warped_image_url"] = url
             if self.debug_state: self.debug_state.warped_image_url = url

             roi_viz = self.scanner.debug_draw_rois(warped)
             url_roi = self._save_debug_image(roi_viz, "roi_viz")
             report["roi_viz_url"] = url_roi
             if self.debug_state: self.debug_state.roi_viz_url = url_roi
        else:
             report["steps"].append(ScanStep(name="Contour", status="FAIL", details=f"{prep_method} failed"))
             warped = self.scanner.get_fallback_crop(frame) # Fallback for crop tracks
             url = self._save_debug_image(warped, "warped")
             report["warped_image_url"] = url
             if self.debug_state: self.debug_state.warped_image_url = url

             roi_viz = self.scanner.debug_draw_rois(warped)
             url_roi = self._save_debug_image(roi_viz, "roi_viz")
             report["roi_viz_url"] = url_roi
             if self.debug_state: self.debug_state.roi_viz_url = url_roi


        check_pause()

        tracks = options.get("tracks", ["easyocr"]) # ['easyocr', 'paddle']

        # 2. Run Tracks
        # Track 1: EasyOCR
        if "easyocr" in tracks:
            try:
                set_step("Track 1: EasyOCR (Full)")
                t1_full = self.scanner.ocr_scan(frame, engine='easyocr')
                t1_full.scope = 'full'
                report["t1_full"] = t1_full
                if self.debug_state: self.debug_state.t1_full = t1_full

                check_pause()

                set_step("Track 1: EasyOCR (Crop)")
                if warped is not None:
                    t1_crop = self.scanner.ocr_scan(warped, engine='easyocr')
                    t1_crop.scope = 'crop'
                    report["t1_crop"] = t1_crop
                    if self.debug_state: self.debug_state.t1_crop = t1_crop
            except ScanAborted:
                raise
            except Exception as e:
                logger.error(f"Track 1 (EasyOCR) Failed: {e}")
                report["steps"].append(ScanStep(name="Track 1", status="FAIL", details=str(e)))

        check_pause()

        # Track 2: PaddleOCR
        if "paddle" in tracks:
            try:
                set_step("Track 2: PaddleOCR (Full)")
                t2_full = self.scanner.ocr_scan(frame, engine='paddle')
                t2_full.scope = 'full'
                report["t2_full"] = t2_full
                if self.debug_state: self.debug_state.t2_full = t2_full

                check_pause()

                set_step("Track 2: PaddleOCR (Crop)")
                if warped is not None:
                    t2_crop = self.scanner.ocr_scan(warped, engine='paddle')
                    t2_crop.scope = 'crop'
                    report["t2_crop"] = t2_crop
                    if self.debug_state: self.debug_state.t2_crop = t2_crop
            except ScanAborted:
                raise
            except Exception as e:
                logger.error(f"Track 2 (PaddleOCR) Failed: {e}")
                report["steps"].append(ScanStep(name="Track 2", status="FAIL", details=str(e)))

        # Extra Analysis on Warped (if available)
        if warped is not None:
             check_pause()
             set_step("Analysis: Visual Features")
             report['visual_rarity'] = self.scanner.detect_rarity_visual(warped)
             report['first_edition'] = self.scanner.detect_first_edition(warped)
             report['warped_image_data'] = warped # Pass along for Art Match

             if self.debug_state:
                 if hasattr(self.debug_state, 'visual_rarity'): self.debug_state.visual_rarity = report['visual_rarity']
                 if hasattr(self.debug_state, 'first_edition'): self.debug_state.first_edition = report['first_edition']

        return report

    def _pick_best_result(self, report):
        """Heuristic to pick the best result from the 4 zones."""
        candidates = []
        for key in ["t1_full", "t1_crop", "t2_full", "t2_crop"]:
            res = report.get(key)
            if res and res.set_id:
                candidates.append(res)

        if not candidates: return None
        # Sort by confidence
        candidates.sort(key=lambda x: x.set_id_conf, reverse=True)
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

            # Create base result
            result = ScanResult()
            result.set_code = set_id
            result.language = data.get('language', 'EN')
            result.ocr_conf = data.get('ocr_conf', 0.0)
            result.visual_rarity = data.get('visual_rarity', 'Common')
            result.first_edition = data.get('first_edition', False)

            if set_id:
                card_info = await self._resolve_card_details(set_id)

                if card_info:
                    result.name = card_info['name']
                    result.card_id = card_info['card_id']
                    result.rarity = card_info['rarity']

                    if warped is not None and card_info.get("potential_art_paths"):
                        match_path, match_score = await run.io_bound(
                            self.scanner.match_artwork, warped, card_info["potential_art_paths"]
                        )
                        if match_path:
                            result.image_path = match_path
                            result.match_score = match_score
                        else:
                            result.image_path = card_info["potential_art_paths"][0]
                            result.match_score = 0

            if result.rarity == "Unknown":
                result.rarity = result.visual_rarity

            self.result_queue.put(result)
            logger.info(f"Lookup complete for {result.set_code}")

            # Emit event for live results
            self._emit("result_ready", {"set_code": result.set_code})

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

# Singleton instantiation logic
# To avoid multiple instances during reload, we might need a more complex strategy if this wasn't enough.
# However, module-level variables are usually reset on reload.
# The issue is typically that the *importing* module holds a reference to the old module's variable.
# Since we fixed the importing side to use `scanner_service.scanner_manager`, we are good.
scanner_manager = ScannerManager()
