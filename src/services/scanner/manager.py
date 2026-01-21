import logging
import time
import os
import uuid
from typing import Optional, Dict, Any, List, Union

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
        self.scanner = CardScanner() if SCANNER_AVAILABLE else None
        self.instance_id = str(uuid.uuid4())[:6]
        logger.info(f"ScannerManager initialized with ID: {self.instance_id}")

        self.debug_dir = "debug/scans"
        os.makedirs(self.debug_dir, exist_ok=True)

    async def process_scan(self, image_data: bytes, options: Dict[str, Any], filename: str = "scan.jpg") -> Dict[str, Any]:
        """
        Processes a single scan request synchronously (to be awaited).
        Returns a comprehensive result dictionary containing both debug info and lookup results.
        """
        if not self.scanner:
            logger.error("Scanner dependencies not available.")
            return {"error": "Scanner unavailable"}

        start_time = time.time()
        logger.info(f"Starting scan for: {filename}")

        # Decode Image
        nparr = np.frombuffer(image_data, np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

        if frame is None:
            logger.error(f"Failed to decode image: {filename}")
            return {"error": "Image decode failed"}

        # Initialize Report
        report = {
            "filename": filename,
            "timestamp": start_time,
            "preprocessing": options.get("preprocessing", "classic"),
            "active_tracks": options.get("tracks", []),
            "captured_image_url": self._save_debug_image(frame, "manual_cap"),
            "steps": [],
            "scan_result": None
        }

        try:
            # 1. Run Pipeline
            pipeline_report = await run.io_bound(self._process_pipeline_logic, frame, options)
            report.update(pipeline_report)

            # 2. Pick Best Result
            best_res = self._pick_best_result(report)

            if best_res:
                logger.info(f"Card detected: {best_res['set_id']} (Conf: {best_res['set_id_conf']}%)")

                # 3. DB Lookup
                lookup_data = {
                    "set_code": best_res['set_id'],
                    "language": best_res['language'],
                    "ocr_conf": best_res['set_id_conf'],
                    "rarity": "Unknown",
                    "visual_rarity": report.get('visual_rarity', 'Common'),
                    "first_edition": report.get('first_edition', False),
                }

                # Resolve Details
                card_info = await self._resolve_card_details(lookup_data['set_code'])

                if card_info:
                    lookup_data.update(card_info)

                    # Art Matching
                    warped = report.get('warped_image_data')
                    if warped is not None and card_info.get("potential_art_paths"):
                        logger.info("Performing artwork matching...")
                        # Run heavy opencv task in thread
                        match_path, match_score = await run.io_bound(
                            self.scanner.match_artwork, warped, card_info["potential_art_paths"]
                        )
                        if match_path:
                            lookup_data["image_path"] = match_path
                            lookup_data["match_score"] = match_score
                        else:
                            lookup_data["image_path"] = card_info["potential_art_paths"][0]
                            lookup_data["match_score"] = 0

                if lookup_data["rarity"] == "Unknown":
                    lookup_data["rarity"] = lookup_data["visual_rarity"]

                report["scan_result"] = lookup_data
                logger.info(f"Finished scan for: {filename} -> {lookup_data.get('name', 'Unknown')}")
            else:
                logger.warning(f"No valid card found in: {filename}")

        except Exception as e:
            logger.error(f"Scan failed for {filename}: {e}", exc_info=True)
            report["error"] = str(e)

        return report

    def _process_pipeline_logic(self, frame, options) -> Dict[str, Any]:
        """Runs the configured tracks on the frame."""
        report = {
            "t1_full": None, "t1_crop": None,
            "t2_full": None, "t2_crop": None,
            "warped_image_data": None
        }

        # 1. Preprocessing (Crop)
        logger.info("Preprocessing: Contour/Crop detection...")
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
             report["warped_image_data"] = warped
        else:
             logger.warning(f"Contour detection ({prep_method}) failed. Using fallback crop.")
             warped = self.scanner.get_fallback_crop(frame)

        tracks = options.get("tracks", ["easyocr"])

        # 2. Run Tracks
        # Track 1: EasyOCR
        if "easyocr" in tracks:
            try:
                logger.info("Running Track 1: EasyOCR...")
                # Full Frame
                t1_full = self.scanner.ocr_scan(frame, engine='easyocr')
                t1_full['scope'] = 'full'
                report["t1_full"] = t1_full

                # Crop
                if warped is not None:
                    t1_crop = self.scanner.ocr_scan(warped, engine='easyocr')
                    t1_crop['scope'] = 'crop'
                    report["t1_crop"] = t1_crop
            except Exception as e:
                logger.error(f"Track 1 (EasyOCR) Failed: {e}")

        # Track 2: PaddleOCR
        if "paddle" in tracks:
            try:
                logger.info("Running Track 2: PaddleOCR...")
                # Full Frame
                t2_full = self.scanner.ocr_scan(frame, engine='paddle')
                t2_full['scope'] = 'full'
                report["t2_full"] = t2_full

                # Crop
                if warped is not None:
                    t2_crop = self.scanner.ocr_scan(warped, engine='paddle')
                    t2_crop['scope'] = 'crop'
                    report["t2_crop"] = t2_crop
            except Exception as e:
                logger.error(f"Track 2 (PaddleOCR) Failed: {e}")

        # Extra Analysis on Warped (if available)
        if warped is not None:
             report['visual_rarity'] = self.scanner.detect_rarity_visual(warped)
             report['first_edition'] = self.scanner.detect_first_edition(warped)

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

    def _save_debug_image(self, image, prefix="img") -> str:
        if image is None: return None
        filename = f"{prefix}_{uuid.uuid4().hex[:8]}.jpg"
        path = os.path.join(self.debug_dir, filename)
        cv2.imwrite(path, image, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
        return f"/debug/scans/{filename}"

scanner_manager = ScannerManager()
