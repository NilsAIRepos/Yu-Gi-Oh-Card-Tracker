import cv2
import numpy as np
import easyocr
import logging
import asyncio
from typing import Optional, Tuple, Dict, Any
from src.services.ygo_api import ygo_service, ApiCard
from difflib import get_close_matches

logger = logging.getLogger(__name__)

class CardScanner:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(CardScanner, cls).__new__(cls)
            cls._instance.reader = None
            cls._instance.initialized = False
        return cls._instance

    def initialize(self):
        if not self.initialized:
            logger.info("Initializing EasyOCR...")
            # en for English names. gpu=False if no GPU, but usually safe to let it detect or force False if stability issues.
            # In this env, probably CPU only.
            try:
                self.reader = easyocr.Reader(['en'], gpu=False)
                self.initialized = True
                logger.info("EasyOCR initialized.")
            except Exception as e:
                logger.error(f"Failed to initialize EasyOCR: {e}")

    def preprocess_image(self, image_bytes: bytes) -> np.ndarray:
        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        return img

    def get_card_warp(self, img: np.ndarray) -> Optional[np.ndarray]:
        """
        Finds the largest 4-point contour and warps it to a standard card size.
        """
        if img is None: return None

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        blur = cv2.GaussianBlur(gray, (5, 5), 0)
        edges = cv2.Canny(blur, 50, 150)

        contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return None

        # Sort by area
        contours = sorted(contours, key=cv2.contourArea, reverse=True)

        card_contour = None
        for cnt in contours[:5]: # Check top 5
            perimeter = cv2.arcLength(cnt, True)
            approx = cv2.approxPolyDP(cnt, 0.02 * perimeter, True)

            if len(approx) == 4:
                card_contour = approx
                break

        if card_contour is None:
            # Fallback: if no 4-point contour found, maybe the whole image is the card?
            # For now, return None to indicate failure to lock on.
            return None

        # Warp
        pts = card_contour.reshape(4, 2)
        rect = self._order_points(pts)

        # Standard YuGiOh ratio ~ 5.9 x 8.6
        # Let's use a high resolution for OCR: 590 x 860
        width = 590
        height = 860

        dst = np.array([
            [0, 0],
            [width - 1, 0],
            [width - 1, height - 1],
            [0, height - 1]
        ], dtype="float32")

        M = cv2.getPerspectiveTransform(rect, dst)
        warped = cv2.warpPerspective(img, M, (width, height))
        return warped

    def _order_points(self, pts):
        # Initialzie a list of coordinates that will be ordered
        # such that the first entry in the list is the top-left,
        # the second entry is the top-right, the third is the
        # bottom-right, and the fourth is the bottom-left
        rect = np.zeros((4, 2), dtype="float32")

        # the top-left point will have the smallest sum, whereas
        # the bottom-right point will have the largest sum
        s = pts.sum(axis=1)
        rect[0] = pts[np.argmin(s)]
        rect[2] = pts[np.argmax(s)]

        # now, compute the difference between the points, the
        # top-right point will have the smallest difference,
        # whereas the bottom-left will have the largest difference
        diff = np.diff(pts, axis=1)
        rect[1] = pts[np.argmin(diff)]
        rect[3] = pts[np.argmax(diff)]

        return rect

    def identify_card(self, image_bytes: bytes) -> Dict[str, Any]:
        """
        Returns: {
            "success": bool,
            "card": ApiCard (obj) or None,
            "confidence": float,
            "scanned_name": str
        }
        """
        if not self.initialized:
            self.initialize()

        original_img = self.preprocess_image(image_bytes)
        if original_img is None:
             return {"success": False, "error": "Invalid image"}

        warped = self.get_card_warp(original_img)

        # If warp fails, we might try to run OCR on the whole image
        # (assuming the user cropped it via camera view), but warping is better.
        # Let's try to use the warped image if available, else original.
        target_img = warped if warped is not None else original_img

        # Crop Name Region (Top 12% roughly)
        h, w, _ = target_img.shape
        # Yugioh name box is at the very top.
        # Let's crop top 15%
        crop_h = int(h * 0.15)
        name_region = target_img[0:crop_h, 0:w]

        # Run OCR
        try:
            # detail=0 returns simple list of strings
            results = self.reader.readtext(name_region, detail=0)
        except Exception as e:
            logger.error(f"OCR Error: {e}")
            return {"success": False, "error": "OCR Failed"}

        if not results:
             return {"success": False, "error": "No text detected"}

        # Join text in case it's split (e.g. "Blue-Eyes" "White Dragon")
        scanned_name = " ".join(results).strip()
        if len(scanned_name) < 3:
             return {"success": False, "error": "Text too short", "scanned_name": scanned_name}

        logger.info(f"Scanned Name: {scanned_name}")

        # Fuzzy Match against DB
        # We need all card names.
        # This might be slow if we do it every time. ygo_service caches cards.
        # Ideally we'd have a name -> card_id map.

        # Accessing _cards_cache directly.
        # In a real app, the DB should be loaded by the UI before scanning starts.
        # We assume 'en' for now or we could inspect the card language if EasyOCR supports it (future).
        all_cards = ygo_service._cards_cache.get('en', [])

        if not all_cards:
             return {"success": False, "error": "Database not loaded"}

        # Create name map
        name_map = {c.name.lower(): c for c in all_cards}
        all_names = list(name_map.keys())

        matches = get_close_matches(scanned_name.lower(), all_names, n=1, cutoff=0.6)

        if matches:
            best_match_name = matches[0]
            matched_card = name_map[best_match_name]

            # Simple confidence metric based on ratio (difflib uses Ratcliff-Obershelp)
            # We can use SequenceMatcher to get a score for the user.
            from difflib import SequenceMatcher
            score = SequenceMatcher(None, scanned_name.lower(), best_match_name).ratio()

            return {
                "success": True,
                "card": matched_card,
                "confidence": score,
                "scanned_name": scanned_name,
                "matched_name": best_match_name
            }

        return {
            "success": False,
            "error": "No match found",
            "scanned_name": scanned_name
        }

scanner_service = CardScanner()
