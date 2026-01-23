import logging
import threading
import queue
import time
import base64
import asyncio
import os
import uuid
import shutil
import pickle
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
    class ScanResult: pass
    class OCRResult: pass

from src.services.ygo_api import ygo_service
from src.services.image_manager import image_manager
from src.core.utils import transform_set_code, extract_language_code, REGION_TO_LANGUAGE_MAP
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

        self.lookup_queue = queue.Queue() # Raw Report -> DB Lookup
        self.result_queue = queue.Queue() # Finished results (ScanResult) -> UI
        self.notification_queue = queue.Queue() # Notifications -> UI

        self.thread: Optional[threading.Thread] = None
        self.instance_id = str(uuid.uuid4())[:6]
        logger.info(f"ScannerManager initialized with ID: {self.instance_id}")

        # State
        self.is_processing = False
        self.paused = True  # Default to paused (Stopped)
        self.status_message = "Stopped"

        self.art_index = {}

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

    def rebuild_art_index(self, force=False):
        """Public method to rebuild the art index, possibly forcing a refresh."""
        if not self.scanner: return

        # Run in a separate thread to avoid blocking if called from UI main thread
        threading.Thread(target=self._build_art_index, args=(force,), daemon=True).start()

    def _build_art_index(self, force=False):
        """Builds or loads the Art Match index from data/images."""
        if not self.scanner: return

        index_path = os.path.join(self.debug_dir, "art_index_yolo.pkl")
        img_dir = "data/images"

        # Load Cache (if not forced)
        if not force and os.path.exists(index_path) and not self.art_index:
            try:
                with open(index_path, "rb") as f:
                    loaded_index = pickle.load(f)
                    if loaded_index and len(loaded_index) > 0:
                        self.art_index = loaded_index
                        logger.info(f"Loaded Art Index: {len(self.art_index)} items")
                        return
                    else:
                        logger.info("Cached Art Index is empty. Rebuilding...")
            except Exception as e:
                logger.error(f"Failed to load cache: {e}")

        # If cache failed or didn't exist, check images
        if not os.path.exists(img_dir):
            logger.error(f"Image directory not found: {img_dir}")
            return

        logger.info(f"Building Art Index (YOLO) from {img_dir}...")
        try:
            files = os.listdir(img_dir)
        except OSError as e:
            logger.error(f"Could not list {img_dir}: {e}")
            return

        count = 0
        new_index = {}

        # Check dependencies
        if cv2 is None:
            logger.error("CV2 is not available. Skipping Art Index.")
            return

        for f in files:
            if not f.lower().endswith(('.jpg', '.png', '.jpeg')): continue
            path = os.path.join(img_dir, f)
            try:
                img = cv2.imread(path)
                if img is None:
                    logger.warning(f"Failed to read image: {f}")
                    continue

                # extract_yolo_features(image, model_name='yolo26n-cls.pt')
                feat = self.scanner.extract_yolo_features(img)
                if feat is not None:
                    new_index[f] = feat
                    count += 1
                else:
                    # Log failure to help debug (throttled?)
                    if count < 5:
                        logger.warning(f"Feature extraction failed for {f}")

                if count > 0 and count % 50 == 0:
                    logger.info(f"Indexed {count} images...")

            except Exception as e:
                logger.error(f"Error indexing {f}: {e}")

        self.art_index = new_index
        logger.info(f"Art Index complete: {len(self.art_index)} items")

        # Save Cache
        try:
            with open(index_path, "wb") as f:
                pickle.dump(self.art_index, f)
        except Exception as e:
            logger.error(f"Failed to save cache: {e}")

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
                        for i in range(1, 3): # 1 to 2 (EasyOCR, DocTR)
                            setattr(self.debug_state, f"t{i}_full", None)
                            setattr(self.debug_state, f"t{i}_crop", None)

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

                        # Push Raw Report to Lookup Queue
                        # Extract art features for lookup if available
                        warped = report.get('warped_image_data')
                        art_features = None
                        if warped is not None and self.scanner:
                             art_features = self.scanner.extract_yolo_features(warped)

                        lookup_data = {
                            "report": report, # Contains OCRResults
                            "warped_image": warped,
                            "art_features": art_features,
                            "filename": filename
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
        }
        for i in range(1, 3): # 1 to 2
            report[f"t{i}_full"] = None
            report[f"t{i}_crop"] = None

        # 1. Preprocessing (Crop)
        set_step("Preprocessing: Contour/Crop")
        prep_method = options.get("preprocessing", "classic")
        contour = None
        warped = None

        if prep_method == "yolo":
             contour = self.scanner.find_card_yolo(frame, model_name='yolov8n-obb.pt')
        elif prep_method == "yolo26":
             contour = self.scanner.find_card_yolo(frame, model_name='yolo26l-obb.pt')
        elif prep_method == "classic_white_bg":
             contour = self.scanner.find_card_contour_white_bg(frame)
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

        tracks = options.get("tracks", ["easyocr"]) # ['easyocr', 'doctr']

        # Config mapping: (engine_key, label_base, field_prefix)
        track_config = [
            ("easyocr", "Track 1: EasyOCR", "t1"),
            ("doctr", "Track 2: DocTR", "t2"),
        ]

        # Flag to pass context
        full_text_context = ""

        for engine_key, label_base, field_prefix in track_config:
            if engine_key in tracks:
                 try:
                     check_pause()
                     set_step(f"{label_base} (Full)")
                     res_full = self.scanner.ocr_scan(frame, engine=engine_key, scope='full')
                     report[f"{field_prefix}_full"] = res_full
                     full_text_context = res_full.raw_text # Use last available
                     if self.debug_state: setattr(self.debug_state, f"{field_prefix}_full", res_full)

                     check_pause()

                     set_step(f"{label_base} (Crop)")
                     if warped is not None:
                         res_crop = self.scanner.ocr_scan(warped, engine=engine_key, scope='crop')
                         report[f"{field_prefix}_crop"] = res_crop
                         if self.debug_state: setattr(self.debug_state, f"{field_prefix}_crop", res_crop)
                 except ScanAborted:
                     raise
                 except Exception as e:
                     logger.error(f"{label_base} Failed: {e}")
                     report["steps"].append(ScanStep(name=label_base, status="FAIL", details=str(e)))

        # Extra Analysis on Warped (if available)
        if warped is not None:
             check_pause()
             set_step("Analysis: Visual Features")
             report['visual_rarity'] = self.scanner.detect_rarity_visual(warped)

             # Robust 1st Edition Check
             # Pass full text context from last OCR run if available
             card_name_context = ""
             if report.get('t2_full'): card_name_context = report['t2_full'].card_name or ""
             elif report.get('t1_full'): card_name_context = report['t1_full'].card_name or ""

             report['first_edition'] = self.scanner.detect_first_edition(
                 warped,
                 full_text_context=full_text_context,
                 card_name_context=card_name_context
             )

             report['warped_image_data'] = warped # Pass along for Art Match

             if self.debug_state:
                 if hasattr(self.debug_state, 'visual_rarity'): self.debug_state.visual_rarity = report['visual_rarity']
                 if hasattr(self.debug_state, 'first_edition'): self.debug_state.first_edition = report['first_edition']

        # Art Match (YOLO) - Initial Global Match
        if options.get("art_match_yolo", False) and self.scanner:
             check_pause()
             set_step("Art Match: YOLO")

             if not self.art_index:
                  self._build_art_index()

             if not self.art_index:
                  report["steps"].append(ScanStep(name="Art Match", status="SKIP", details="Index empty (no images found)"))
             elif warped is not None:
                  feat = self.scanner.extract_yolo_features(warped)
                  if feat is not None:
                      best_score = -1.0
                      best_match = None

                      # Find best match
                      for fname, ref_feat in self.art_index.items():
                           score = self.scanner.calculate_similarity(feat, ref_feat)
                           if score > best_score:
                               best_score = score
                               best_match = fname

                      # Threshold?
                      report["art_match_yolo"] = {
                          "filename": best_match,
                          "score": float(best_score),
                          "image_url": f"/images/{best_match}"
                      }
                      if self.debug_state: self.debug_state.art_match_yolo = report["art_match_yolo"]
                  else:
                      report["steps"].append(ScanStep(name="Art Match", status="FAIL", details="Feature extraction failed"))
             else:
                  report["steps"].append(ScanStep(name="Art Match", status="FAIL", details="No Warped Image"))

        return report

    async def process_pending_lookups(self):
        try:
            try:
                data = self.lookup_queue.get_nowait()
            except queue.Empty:
                return

            logger.info(f"Processing lookup for {data.get('filename', 'scan')}")

            # Use the robust algorithm
            result = await self._resolve_match(data)

            # Check for ambiguity
            if result.ambiguity_data:
                # Notify UI of ambiguity
                self._emit("scan_ambiguous", {"result": result.model_dump()})
                self.result_queue.put(result) # Still put it? UI might need to consume it to show dialog.
                # Actually, if we put it in result_queue, ScanPage.event_consumer handles it as success unless checked.
                # I'll emit "scan_ambiguous" and also put it in result queue but marked as ambiguous.
            else:
                self.result_queue.put(result)
                # Emit event for live results
                self._emit("result_ready", {"set_code": result.set_code})

            logger.info(f"Lookup complete. Ambiguous: {bool(result.ambiguity_data)}")

        except Exception as e:
            logger.error(f"Error in process_pending_lookups: {e}", exc_info=True)

    async def _resolve_match(self, data: Dict[str, Any]) -> ScanResult:
        """
        Weighted Matching Algorithm.
        """
        report = data.get('report', {})
        warped = data.get('warped_image')
        scan_features = data.get('art_features')

        # 1. Gather Signals
        # Collect all valid Set IDs and Names from all OCR tracks
        candidates_set_ids = []
        candidates_names = []

        best_ocr_conf = 0.0
        best_ocr_lang = "EN"

        ocr_atk = None
        ocr_def = None
        ocr_1st_ed = report.get('first_edition', False)
        visual_rarity = report.get('visual_rarity', 'Common')

        for key, res in report.items():
            if isinstance(res, OCRResult):
                if res.set_id:
                    candidates_set_ids.append((res.set_id, res.set_id_conf))
                    if res.set_id_conf > best_ocr_conf:
                        best_ocr_conf = res.set_id_conf
                        best_ocr_lang = res.language
                if res.card_name:
                    candidates_names.append(res.card_name)
                if res.atk: ocr_atk = res.atk
                if res.def_val: ocr_def = res.def_val

        # Art Match
        art_match_info = report.get('art_match_yolo', {})
        art_match_filename = art_match_info.get('filename')
        art_match_score = art_match_info.get('score', 0.0)

        print(f"MATCHING DEBUG: Candidates SetIDs: {candidates_set_ids}", flush=True)
        print(f"MATCHING DEBUG: Candidates Names: {candidates_names}", flush=True)
        print(f"MATCHING DEBUG: Art Match: {art_match_filename} ({art_match_score})", flush=True)

        # 2. Load Database (Async)
        # We load 'en' as base, but if best_ocr_lang is different, we might need that too.
        # Ideally, we load based on detected language, but for now stick to EN + mappings.
        all_cards = await ygo_service.load_card_database("en")

        scored_candidates = {} # card_id -> {score, card, reasons}

        def add_points(card, points, reason):
            if card.id not in scored_candidates:
                scored_candidates[card.id] = {"score": 0, "card": card, "reasons": []}
            scored_candidates[card.id]["score"] += points
            scored_candidates[card.id]["reasons"].append(reason)

        # A. Score by Set Code (Very High Weight)
        for set_id, conf in candidates_set_ids:
            # Normalize set_id
            norm_id = transform_set_code(set_id, "EN") # Normalize to EN base

            # Find cards with this set code
            for card in all_cards:
                if not card.card_sets: continue
                for s in card.card_sets:
                    # Check compatibility (allowing for region differences handled by transform)
                    if s.set_code == norm_id or s.set_code == set_id:
                        # Weight based on OCR confidence
                        points = 50 * (conf / 100.0)
                        add_points(card, points, f"Set Code {set_id}")

        # B. Score by Name (Medium High Weight)
        for name in candidates_names:
            norm_name = self.scanner._normalize_card_name(name)
            for card in all_cards:
                # Fuzzy or exact? Exact normalized is safer for now as per instructions.
                db_norm = self.scanner._normalize_card_name(card.name)
                if norm_name == db_norm:
                    add_points(card, 40, f"Name {name}")
                elif norm_name in db_norm or db_norm in norm_name:
                    # Partial match
                    if len(norm_name) > 4:
                        add_points(card, 20, f"Partial Name {name}")

        # C. Score by Art Match (Medium Weight)
        # If we have a filename, try to find the card it belongs to.
        # Filename is usually card_id.jpg
        if art_match_filename:
            try:
                # stored as "12345.jpg" or "12345_1.jpg"
                base_id = art_match_filename.split('_')[0].split('.')[0]
                if base_id.isdigit():
                    art_id = int(base_id)
                    # Find card with this ID
                    # Note: Card ID in DB matches image ID usually.
                    card = next((c for c in all_cards if c.id == art_id), None)
                    if card:
                         add_points(card, 30 * art_match_score, f"Art Match {art_match_filename}")
            except:
                pass

        # D. Validate with Stats (Tiebreaker)
        if ocr_atk or ocr_def:
            for cid, data in scored_candidates.items():
                c = data['card']
                # ATK/DEF are often integers, OCR returns strings with possible ?
                # Normalize
                def norm_stat(s): return str(s).strip()

                matched_stats = False
                if ocr_atk and norm_stat(c.atk) == norm_stat(ocr_atk):
                    add_points(c, 10, f"ATK {ocr_atk}")
                    matched_stats = True
                if ocr_def and norm_stat(c.def_val) == norm_stat(ocr_def):
                    add_points(c, 10, f"DEF {ocr_def}")
                    matched_stats = True

        # 3. Constrained Art Match (Refinement)
        # If top candidate has low Art score (or no art score), but we have warped image,
        # Try to match against the top candidate's specific art variants.

        # Sort current candidates
        sorted_cands = sorted(scored_candidates.values(), key=lambda x: x['score'], reverse=True)

        if sorted_cands and self.scanner and warped is not None and scan_features is not None:
            top = sorted_cands[0]
            card = top['card']

            # Check if we already matched this card via Art
            already_matched_art = any("Art Match" in r for r in top['reasons'])

            if not already_matched_art or art_match_score < 0.85:
                # Fetch images for this card
                logger.info(f"Running Constrained Art Match for {card.name}")
                if card.card_images:
                    # We need to download them if not present?
                    # Use high res or small? Manager uses cache.
                    # We can use `ygo_service` to get paths.
                    for img in card.card_images:
                        path = await image_manager.ensure_image(card.id, img.image_url, high_res=True)
                        if path and os.path.exists(path):
                            # Read and extract features
                            # This is synchronous I/O, might block loop slightly.
                            # Run in thread if heavy.
                            try:
                                ref_img = cv2.imread(path)
                                if ref_img is not None:
                                    ref_feat = self.scanner.extract_yolo_features(ref_img)
                                    sim = self.scanner.calculate_similarity(scan_features, ref_feat)

                                    if sim > 0.8:
                                        add_points(card, 30 * sim, f"Constrained Art Match ({sim:.2f})")
                                        print(f"MATCHING DEBUG: Constrained Match Success {sim:.2f}", flush=True)
                                        break
                            except Exception as e:
                                logger.error(f"Constrained match error: {e}")

        # Re-sort after updates
        sorted_cands = sorted(scored_candidates.values(), key=lambda x: x['score'], reverse=True)

        print(f"MATCHING DEBUG: Top Candidates: {[(c['card'].name, c['score']) for c in sorted_cands[:3]]}", flush=True)

        # 4. Determine Result & Ambiguity
        result = ScanResult()
        result.ocr_conf = best_ocr_conf
        result.first_edition = ocr_1st_ed
        result.visual_rarity = visual_rarity
        result.language = best_ocr_lang

        is_ambiguous = False
        ambiguity_reason = ""

        if not sorted_cands:
            result.name = "Unknown Card"
            is_ambiguous = True
            ambiguity_reason = "No candidates found"
        else:
            best = sorted_cands[0]
            card = best['card']
            result.name = card.name
            result.card_id = card.id
            result.match_score = int(best['score'])

            # Determine Set Code & Rarity
            # If we have a Set Code from OCR that matches this card, use it.
            # Otherwise use the most common/likely?

            matching_set = None
            if card.card_sets:
                # Try to find the exact set code extracted
                for sid, _ in candidates_set_ids:
                    norm_sid = transform_set_code(sid, "EN")
                    for s in card.card_sets:
                        if s.set_code == norm_sid or s.set_code == sid:
                            matching_set = s
                            break
                    if matching_set: break

            if matching_set:
                result.set_code = matching_set.set_code
                result.rarity = matching_set.set_rarity

                # Ambiguity Check: Does this Set Code have multiple rarities?
                # E.g. LOB-EN001 exists as Ultra Rare and ...?
                # Check other sets with same code
                same_code_sets = [s for s in card.card_sets if s.set_code == matching_set.set_code]
                if len(same_code_sets) > 1:
                    # Check visual rarity to resolve
                    # If visual rarity matches one, pick it.
                    # Else ambiguous.
                    # Simplification: If multiple rarities, mark ambiguous.
                    # Unless visual_rarity is strong?

                    # For now, mark ambiguous to let user confirm.
                    is_ambiguous = True
                    ambiguity_reason = "Multiple rarities for Set Code"

            else:
                # We matched card (maybe by name/art) but Set Code is unknown or doesn't match.
                is_ambiguous = True
                ambiguity_reason = "Set Code mismatch or unknown"
                # Default to first set?
                if card.card_sets:
                    result.set_code = card.card_sets[0].set_code
                    result.rarity = card.card_sets[0].set_rarity

        # 5. Populate Result
        if is_ambiguous:
            # Prepare data for dialog
            # Candidates for dropdowns
            # Current best guess
            result.ambiguity_data = {
                "reason": ambiguity_reason,
                "candidates": [
                    {
                        "card_id": c['card'].id,
                        "name": c['card'].name,
                        "score": c['score'],
                        "sets": [s.model_dump() for s in c['card'].card_sets] if c['card'].card_sets else []
                    }
                    for c in sorted_cands[:5]
                ],
                "ocr_set_codes": [sid for sid, _ in candidates_set_ids],
                "visual_rarity": visual_rarity,
                "detected_lang": best_ocr_lang
            }

        return result

# Singleton instantiation logic
scanner_manager = ScannerManager()
