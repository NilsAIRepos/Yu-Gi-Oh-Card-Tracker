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

                        # If we found a card, push to result queue
                        # We pick the best result.
                        best_res = self._pick_best_result(report)
                        if best_res:
                            # Enhance with visual traits if warped image exists
                            warped = report.get('warped_image_data') # Not in model, but returned by _process_scan

                            # Construct lookup data (Internal structure for LookupQueue)
                            lookup_data = {
                                "ocr_result": best_res.model_dump(),
                                "art_match": report.get('art_match_yolo'),
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

        for engine_key, label_base, field_prefix in track_config:
            if engine_key in tracks:
                 try:
                     check_pause()
                     set_step(f"{label_base} (Full)")
                     res_full = self.scanner.ocr_scan(frame, engine=engine_key, scope='full')
                     report[f"{field_prefix}_full"] = res_full
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
             report['first_edition'] = self.scanner.detect_first_edition(warped)
             report['warped_image_data'] = warped # Pass along for Art Match

             if self.debug_state:
                 if hasattr(self.debug_state, 'visual_rarity'): self.debug_state.visual_rarity = report['visual_rarity']
                 if hasattr(self.debug_state, 'first_edition'): self.debug_state.first_edition = report['first_edition']

        # Art Match (YOLO)
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

    def _pick_best_result(self, report):
        """Heuristic to pick the best result from all zones."""
        candidates = []
        for i in range(1, 3): # 1 to 2
            for scope in ["full", "crop"]:
                key = f"t{i}_{scope}"
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

            logger.info("Starting matching process...")
            start_time = time.time()

            ocr_res = OCRResult(**data.get('ocr_result'))
            art_match = data.get('art_match')
            warped = data.pop('warped_image', None)

            # Base Result
            result = ScanResult()
            result.language = ocr_res.language
            result.ocr_conf = ocr_res.set_id_conf
            result.visual_rarity = data.get('visual_rarity', 'Common')
            result.first_edition = data.get('first_edition', False)
            result.raw_ocr = [ocr_res]

            # Find Best Match
            match_res = await self.find_best_match(ocr_res, art_match)

            if match_res:
                # If ambiguity or match found
                result.ambiguity_flag = match_res.get('ambiguity', False)
                result.candidates = match_res.get('candidates', [])

                # If we have a clear winner (first candidate), fill basics
                if result.candidates:
                    best = result.candidates[0]
                    result.name = best.get('name')
                    result.card_id = best.get('card_id')
                    result.set_code = best.get('set_code')
                    result.rarity = best.get('rarity', 'Unknown')
                    result.match_score = int(best.get('score', 0))

                    # Image Path
                    if best.get('image_id'):
                        # Resolve image path using image_manager
                        # We need to construct url or just use ID?
                        # ygo_api helper?
                        api_card = ygo_service.get_card(result.card_id)
                        if api_card:
                            # find image url
                            img = next((i for i in api_card.card_images if i.id == best['image_id']), None)
                            if img:
                                path = await image_manager.ensure_image(result.card_id, img.image_url, high_res=True)
                                result.image_path = path

            # Fallback if no match but Set ID exists in OCR?
            if not result.set_code and ocr_res.set_id:
                result.set_code = ocr_res.set_id

            logger.info(f"Matching finished in {time.time() - start_time:.3f}s. Ambiguity: {result.ambiguity_flag}")

            self.result_queue.put(result)
            self._emit("result_ready", {"set_code": result.set_code, "ambiguous": result.ambiguity_flag})

        except Exception as e:
            logger.error(f"Error in process_pending_lookups: {e}", exc_info=True)

    async def find_best_match(self, ocr_res: 'OCRResult', art_match: Dict) -> Dict[str, Any]:
        """
        Weighted matching algorithm.
        Returns dict with 'ambiguity' (bool) and 'candidates' (List).
        """
        cards = await ygo_service.load_card_database("en") # TODO: Optimize language loading?

        candidates = []

        # Helper to normalize
        def norm(s): return self.scanner._normalize_card_name(s) if s else ""

        ocr_norm_name = norm(ocr_res.card_name)

        # 0. Filter Potential Database Entries
        # Scanning 12k cards * variants is expensive if we do full scoring.
        # Filter first by: Set Code OR Name Match OR Art Match ID

        # Art Match ID
        art_id = None
        if art_match and art_match.get('filename'):
            try:
                art_id = int(os.path.splitext(art_match['filename'])[0])
            except: pass

        potential_cards = []

        for card in cards:
            is_candidate = False

            # Check Set Code
            if ocr_res.set_id and card.card_sets:
                 if any(s.set_code == ocr_res.set_id for s in card.card_sets):
                     is_candidate = True

            # Check Name (if Set Code didn't match or missing)
            if not is_candidate and ocr_norm_name:
                 if norm(card.name) == ocr_norm_name:
                     is_candidate = True

            # Check Art ID
            if not is_candidate and art_id:
                 if any(img.id == art_id for img in card.card_images):
                     is_candidate = True
                 elif card.card_sets and any(s.image_id == art_id for s in card.card_sets):
                     is_candidate = True

            if is_candidate:
                potential_cards.append(card)

        # 1. Score Candidates (Variants)
        scored_variants = []

        for card in potential_cards:
            if not card.card_sets: continue

            for variant in card.card_sets:
                score = 0.0

                # A. Set Code (80+)
                if ocr_res.set_id and variant.set_code == ocr_res.set_id:
                    score += 80.0
                    score += (ocr_res.set_id_conf / 100.0) * 10.0

                # B. Name (50+)
                if ocr_norm_name and norm(card.name) == ocr_norm_name:
                    score += 50.0
                elif ocr_norm_name and ocr_norm_name in norm(card.name): # Partial
                    score += 25.0

                # C. Art Match (40+)
                if art_id:
                    if variant.image_id == art_id:
                        score += 40.0
                    elif any(img.id == art_id for img in card.card_images):
                        score += 35.0 # Wrong variant but correct card

                # D. Stats (+/- 15)
                # Need to check OCR extraction of ATK/DEF vs Card
                # OCRResult now has atk/def
                if ocr_res.atk:
                    try:
                        # Handle ?
                        val = str(card.atk) if card.atk is not None else "?"
                        if val == ocr_res.atk: score += 15.0
                    except: pass

                if ocr_res.def_val:
                     try:
                        val = str(card.def_) if card.def_ is not None else "?"
                        if val == ocr_res.def_val: score += 15.0
                     except: pass

                if score > 30.0: # Minimum threshold
                    scored_variants.append({
                        "score": score,
                        "card_id": card.id,
                        "name": card.name,
                        "set_code": variant.set_code,
                        "rarity": variant.set_rarity,
                        "image_id": variant.image_id,
                        "variant_id": variant.variant_id
                    })

        scored_variants.sort(key=lambda x: x['score'], reverse=True)

        if not scored_variants:
            return {"ambiguity": False, "candidates": []}

        # 2. Determine Ambiguity
        ambiguous = False

        # A. Database Ambiguity: Top winner has multiple rarities for SAME Set Code
        top = scored_variants[0]
        same_code_variants = [v for v in scored_variants if v['set_code'] == top['set_code'] and v['score'] >= top['score'] - 5.0]
        # Check if they have different rarities
        rarities = set(v['rarity'] for v in same_code_variants)
        if len(rarities) > 1:
            ambiguous = True

        # B. Matching Ambiguity: Top 2 scores are close
        if len(scored_variants) > 1:
            second = scored_variants[1]
            if top['score'] - second['score'] < 10.0:
                 # Check if they are actually different cards/variants (not just same card diff rarity which is covered above)
                 if top['set_code'] != second['set_code']:
                     ambiguous = True

        return {
            "ambiguity": ambiguous,
            "candidates": scored_variants[:10] # Return top 10
        }

# Singleton instantiation logic
# To avoid multiple instances during reload, we might need a more complex strategy if this wasn't enough.
# However, module-level variables are usually reset on reload.
# The issue is typically that the *importing* module holds a reference to the old module's variable.
# Since we fixed the importing side to use `scanner_service.scanner_manager`, we are good.
scanner_manager = ScannerManager()
