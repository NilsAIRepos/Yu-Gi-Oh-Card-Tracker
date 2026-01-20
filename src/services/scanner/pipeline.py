import logging
import re
import os
from typing import Optional, Tuple, List, Dict, Any

try:
    import cv2
    import numpy as np
    import pytesseract
    from langdetect import detect, LangDetectException
except ImportError:
    pass  # Handled in __init__.py

logger = logging.getLogger(__name__)

class CardScanner:
    def __init__(self):
        self.width = 600
        self.height = 875

        # Region of Interest (ROI) definitions (x, y, w, h) based on 600x875
        # Note: These are initial approximations and may need tuning.
        # Y coordinate 605 is approx just below the artwork box.

        # Set ID: Usually mid-right, below artwork
        self.roi_set_id = (400, 595, 180, 45)

        # 1st Edition: Usually mid-left, below artwork
        self.roi_1st_ed = (20, 595, 180, 45)

        # Description Box: Bottom area (skipping ATK/DEF line)
        self.roi_desc = (35, 650, 530, 180)

        # Art Area: The main artwork box
        self.roi_art = (50, 110, 500, 490)

        # Name Area: Top of the card
        self.roi_name = (30, 25, 480, 50)

    def preprocess_image(self, frame):
        """Basic preprocessing for contour detection."""
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        blur = cv2.GaussianBlur(gray, (5, 5), 0)
        # Adaptive thresholding often works better for varying lighting
        thresh = cv2.adaptiveThreshold(blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                      cv2.THRESH_BINARY, 11, 2)
        # Invert because contours are found on white
        thresh = cv2.bitwise_not(thresh)
        return thresh

    def find_card_contour(self, frame) -> Optional[np.ndarray]:
        """Finds the largest rectangular contour that looks like a card."""
        thresh = self.preprocess_image(frame)
        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        if not contours:
            return None

        # Sort by area
        contours = sorted(contours, key=cv2.contourArea, reverse=True)

        for cnt in contours[:5]: # Check top 5
            area = cv2.contourArea(cnt)
            if area < 10000: # Minimum area threshold
                continue

            peri = cv2.arcLength(cnt, True)
            approx = cv2.approxPolyDP(cnt, 0.02 * peri, True)

            # Preference 1: Perfect 4-sided polygon
            if len(approx) == 4:
                return approx

            # Preference 2: Robust Fallback (Rotated Rectangle)
            # Handles cards with rounded corners or slight occlusion
            rect = cv2.minAreaRect(cnt)
            (center), (w, h), angle = rect

            if w == 0 or h == 0:
                continue

            ar = w / h
            if ar > 1:
                ar = 1 / ar

            # Yugioh Card Ratio is ~0.68. Allow tolerance (0.55 - 0.85)
            if 0.55 < ar < 0.85:
                box = cv2.boxPoints(rect)
                box = np.int32(box)
                # Reshape to match approxPolyDP output format (4, 1, 2)
                return box.reshape(4, 1, 2)

        return None

    def warp_card(self, frame, contour) -> np.ndarray:
        """Warps the perspective of the detected card to standard dimensions."""
        pts = contour.reshape(4, 2)

        # Order points: TL, TR, BR, BL
        rect = np.zeros((4, 2), dtype="float32")
        s = pts.sum(axis=1)
        rect[0] = pts[np.argmin(s)]
        rect[2] = pts[np.argmax(s)]

        diff = np.diff(pts, axis=1)
        rect[1] = pts[np.argmin(diff)]
        rect[3] = pts[np.argmax(diff)]

        dst = np.array([
            [0, 0],
            [self.width - 1, 0],
            [self.width - 1, self.height - 1],
            [0, self.height - 1]
        ], dtype="float32")

        M = cv2.getPerspectiveTransform(rect, dst)
        warped = cv2.warpPerspective(frame, M, (self.width, self.height))
        return warped

    def extract_set_id(self, warped) -> Optional[str]:
        """Extracts the Set ID (e.g., LOB-EN001) using OCR."""
        x, y, w, h = self.roi_set_id
        roi = warped[y:y+h, x:x+w]

        # Preprocess ROI for OCR
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        # Resize to improve OCR accuracy
        gray = cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        # Threshold
        _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        # Configure Tesseract
        # psm 7 = Treat the image as a single text line
        config = r'--oem 3 --psm 7'
        text = pytesseract.image_to_string(thresh, config=config)

        # Cleanup
        text = text.strip().upper()
        # Regex for standard Set ID: 3-4 chars, hyphen, optional region (2 chars), 3 digits
        # Examples: SDK-001, LOB-EN001, MP19-EN005
        match = re.search(r'([A-Z0-9]{3,4}-[A-Z]{0,2}?[0-9]{3})', text)
        if match:
            return match.group(1)

        # Fallback liberal match
        match = re.search(r'([A-Z0-9]+-[A-Z0-9]+)', text)
        if match:
            return match.group(1)

        return None

    def detect_first_edition(self, warped) -> bool:
        """Checks for '1st Edition' text using OCR."""
        x, y, w, h = self.roi_1st_ed
        roi = warped[y:y+h, x:x+w]

        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        gray = cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        config = r'--oem 3 --psm 7'
        text = pytesseract.image_to_string(thresh, config=config).lower()

        # Check for variations
        if '1st' in text or 'edition' in text:
            return True
        return False

    def detect_language(self, warped, set_id: Optional[str]) -> str:
        """Determines card language."""
        # 1. Fast check via Set ID
        if set_id:
            parts = set_id.split('-')
            if len(parts) > 1:
                # E.g., LOB-EN001 -> EN, LOB-DE001 -> DE
                suffix = parts[1][:2]
                if suffix in ['EN', 'DE', 'FR', 'IT', 'ES', 'PT', 'JP']:
                    return suffix

        # 2. Fallback: OCR Description
        x, y, w, h = self.roi_desc
        roi = warped[y:y+h, x:x+w]

        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        _, thresh = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY)

        text = pytesseract.image_to_string(thresh)
        if len(text) < 10:
            return "EN" # Default

        try:
            lang = detect(text)
            return lang.upper()
        except LangDetectException:
            return "EN"

    def match_artwork(self, warped, reference_paths: List[str]) -> Optional[str]:
        """
        Matches the card artwork against a list of local reference images using ORB.
        Returns the path of the best matching image.
        """
        if not reference_paths:
            return None

        orb = cv2.ORB_create()
        bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)

        # Extract Art ROI from warped
        x, y, w, h = self.roi_art
        scan_art = warped[y:y+h, x:x+w]
        scan_kp, scan_des = orb.detectAndCompute(scan_art, None)

        if scan_des is None:
            return None

        best_match_count = 0
        best_image_path = None

        for ref_path in reference_paths:
            try:
                ref_img = cv2.imread(ref_path)
                if ref_img is None:
                    continue

                # Resize ref to match ROI approximately? ORB is scale invariant-ish but helping it helps.
                # But reference images are likely full card images. We should crop them too?
                # Assumption: Reference images are full cards.
                # If they are full cards, we should crop them using the same ROI if possible,
                # OR just match against the whole thing. Cropping is safer if the ref is standard.
                # However, if ref is just the artwork (from API), we don't crop.
                # The prompt says: "Load local reference images for the specific Set ID... Download missing pictures... after identifying set code."
                # API images are usually full cards.

                # Try to crop ref if it looks like a full card (approx aspect ratio)
                h_ref, w_ref = ref_img.shape[:2]
                if 1.4 < h_ref / w_ref < 1.6: # It's a full card
                    # Scale to our size
                    ref_resized = cv2.resize(ref_img, (self.width, self.height))
                    ref_art = ref_resized[y:y+h, x:x+w]
                    kp, des = orb.detectAndCompute(ref_art, None)
                else:
                    # Maybe it's just artwork? Use as is.
                    kp, des = orb.detectAndCompute(ref_img, None)

                if des is None:
                    continue

                matches = bf.match(scan_des, des)
                # Sort matches by distance
                matches = sorted(matches, key=lambda x: x.distance)

                # Count "good" matches (distance < 50 is a common heuristic)
                good_matches = [m for m in matches if m.distance < 60]
                count = len(good_matches)

                if count > best_match_count:
                    best_match_count = count
                    best_image_path = ref_path

            except Exception as e:
                logger.error(f"Error matching artwork for {ref_path}: {e}")

        # threshold for a valid match?
        if best_match_count > 10: # Arbitrary threshold
            return best_image_path

        return None

    def detect_rarity_visual(self, warped) -> str:
        """
        Visual fallback for rarity detection using color masking on the name.
        Returns 'Gold', 'Silver', or 'Common'.
        """
        x, y, w, h = self.roi_name
        roi = warped[y:y+h, x:x+w]

        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)

        # Gold Mask (approximate)
        lower_gold = np.array([10, 100, 100])
        upper_gold = np.array([40, 255, 255])
        mask_gold = cv2.inRange(hsv, lower_gold, upper_gold)

        # Silver Mask
        lower_silver = np.array([0, 0, 150]) # Low saturation, high value
        upper_silver = np.array([180, 50, 255])
        mask_silver = cv2.inRange(hsv, lower_silver, upper_silver)

        gold_pixels = cv2.countNonZero(mask_gold)
        silver_pixels = cv2.countNonZero(mask_silver)
        total_pixels = w * h

        if gold_pixels > total_pixels * 0.05:
            return "Gold Rare" # or Ultra/Gold
        elif silver_pixels > total_pixels * 0.05:
            return "Secret Rare" # or Secret/Silver

        return "Common"

    def debug_draw_rois(self, image=None):
        """Draws ROIs on the provided image (or a blank one)."""
        if image is not None:
             canvas = image.copy()
        else:
             canvas = np.zeros((self.height, self.width, 3), dtype=np.uint8)

        # Helper to draw
        def draw_roi(roi, color):
            x, y, w, h = roi
            cv2.rectangle(canvas, (x, y), (x + w, y + h), color, 2)

        # Draw Set ID ROI (Green)
        draw_roi(self.roi_set_id, (0, 255, 0))

        # Draw 1st Ed ROI (Blue)
        draw_roi(self.roi_1st_ed, (255, 0, 0))

        # Draw Name ROI (Yellow)
        draw_roi(self.roi_name, (0, 255, 255))

        # Draw Art ROI (Magenta)
        draw_roi(self.roi_art, (255, 0, 255))

        # Draw Desc ROI (White)
        draw_roi(self.roi_desc, (255, 255, 255))

        return canvas
