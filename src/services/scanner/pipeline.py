import logging
import re
import os
from typing import Optional, Tuple, List, Dict, Any

try:
    import cv2
    import numpy as np
    import pytesseract
    from pytesseract import Output
    from langdetect import detect, LangDetectException
except ImportError:
    pass  # Handled in __init__.py

logger = logging.getLogger(__name__)

class CardScanner:
    def __init__(self):
        self.width = 600
        self.height = 875

        # Region of Interest (ROI) definitions (x, y, w, h) based on 600x875

        # Set ID: Usually mid-right, below artwork
        self.roi_set_id_search = (300, 580, 290, 80)

        # 1st Edition: Usually mid-left, below artwork
        self.roi_1st_ed = (20, 595, 180, 45)

        # Description Box: Bottom area
        self.roi_desc = (35, 650, 530, 180)

        # Art Area: The main artwork box
        self.roi_art = (50, 110, 500, 490)

        # Name Area: Top of the card
        self.roi_name = (30, 25, 480, 50)

        # Map for helper access
        self.rois = {
            "set_id": self.roi_set_id_search,
            "first_ed": self.roi_1st_ed,
            "desc": self.roi_desc,
            "art": self.roi_art,
            "name": self.roi_name
        }

    def preprocess_image(self, frame):
        """Basic preprocessing for contour detection."""
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        blur = cv2.GaussianBlur(gray, (5, 5), 0)

        # Use Morphological Gradient to detect edges regardless of contrast direction
        # Using 5x5 kernel to recover gradient magnitude after blurring
        kernel = np.ones((5, 5), np.uint8)
        grad = cv2.morphologyEx(blur, cv2.MORPH_GRADIENT, kernel)

        # Use Otsu's thresholding on the gradient image
        # This dynamically determines the best threshold value
        _, thresh = cv2.threshold(grad, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        # Close gaps in the border
        thresh = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel, iterations=2)

        return thresh

    def get_fallback_crop(self, frame) -> np.ndarray:
        """Creates a 'center crop' of the frame."""
        h_frame, w_frame = frame.shape[:2]
        target_ar = self.width / self.height # ~0.68

        crop_h = int(h_frame * 0.7)
        crop_w = int(crop_h * target_ar)

        if crop_w > w_frame:
            crop_w = int(w_frame * 0.8)
            crop_h = int(crop_w / target_ar)

        x_start = (w_frame - crop_w) // 2
        y_start = (h_frame - crop_h) // 2

        crop = frame[y_start:y_start+crop_h, x_start:x_start+crop_w]
        resized = cv2.resize(crop, (self.width, self.height))
        return resized

    def get_roi_crop(self, warped, roi_name: str) -> Optional[np.ndarray]:
        """Returns the cropped image for a specific ROI."""
        if roi_name not in self.rois:
            return None
        x, y, w, h = self.rois[roi_name]
        return warped[y:y+h, x:x+w]

    def find_card_contour(self, frame) -> Optional[np.ndarray]:
        """Finds the largest rectangular contour that looks like a card."""
        thresh = self.preprocess_image(frame)
        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        if not contours:
            return None

        h_img, w_img = frame.shape[:2]
        center_x, center_y = w_img // 2, h_img // 2

        # Filter contours by size first to remove noise
        # Increase min area to avoid detecting small internal boxes (like art box)
        valid_contours = []
        for cnt in contours:
            if cv2.contourArea(cnt) > 25000:
                 valid_contours.append(cnt)

        contours = valid_contours

        # Central Bias Score: Prioritize large areas near the center
        def score_contour(cnt):
            area = cv2.contourArea(cnt)

            M = cv2.moments(cnt)
            if M["m00"] != 0:
                cX = int(M["m10"] / M["m00"])
                cY = int(M["m01"] / M["m00"])
                # Normalized distance from center (0 to ~1)
                dist_norm = np.sqrt((cX - center_x)**2 + (cY - center_y)**2) / (np.sqrt(w_img**2 + h_img**2))
            else:
                dist_norm = 1.0

            # Score = Area weighted by proximity to center
            # We penalize distance.
            return area * (1.0 - dist_norm)

        contours = sorted(contours, key=score_contour, reverse=True)

        for cnt in contours[:5]:
            hull = cv2.convexHull(cnt)
            peri = cv2.arcLength(hull, True)
            approx = cv2.approxPolyDP(hull, 0.02 * peri, True)

            # Check for 4 corners directly
            if len(approx) == 4:
                # Validate Aspect Ratio for 4-point polygon
                # It's harder to get AR from poly, so we check minAreaRect
                pass # fall through to rect check for consistency

            rect = cv2.minAreaRect(hull)
            (center), (w, h), angle = rect

            if w == 0 or h == 0:
                continue

            ar = w / h
            if ar > 1:
                ar = 1 / ar

            # Yugioh Card Ratio is ~0.68 (59mm / 86mm)
            # Stricter bounds to avoid capturing square art boxes or wide table areas
            if 0.60 < ar < 0.78:
                box = cv2.boxPoints(rect)
                box = np.int32(box)
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

    def extract_set_id(self, warped) -> Tuple[Optional[str], int, str]:
        """Extracts the Set ID using OCR. Returns (set_id, confidence, raw_text)."""
        # scan full image instead of ROI to be robust against bad crops
        roi = warped

        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        # Resize to improve OCR accuracy on small text
        gray = cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        # psm 11 (Sparse text) is still good, but psm 3 (Fully automatic) might be better for full card
        # sticking to 11 as we are looking for specific code blocks
        config = r'--oem 3 --psm 11'

        # Get data to find confidence of the matched word
        data = pytesseract.image_to_data(thresh, config=config, output_type=Output.DICT)

        text = " ".join(data['text']).strip().upper()

        # Regex for standard Set ID
        # Looking for pattern in the full text
        match = re.search(r'([A-Z0-9]{3,4}-[A-Z]{0,2}?[0-9]{3})', text)
        if match:
            found_text = match.group(1)
            # Try to find confidence for this specific match
            confs = [int(c) for c in data['conf'] if int(c) != -1]
            avg_conf = sum(confs) / len(confs) if confs else 0
            return found_text, avg_conf, text

        # Fallback liberal match
        match = re.search(r'([A-Z0-9]+-[A-Z0-9]+)', text)
        if match:
            found_text = match.group(1)
            confs = [int(c) for c in data['conf'] if int(c) != -1]
            avg_conf = sum(confs) / len(confs) if confs else 0
            return found_text, avg_conf, text

        return None, 0, text

    def detect_first_edition(self, warped) -> bool:
        """Checks for '1st Edition' text using OCR."""
        x, y, w, h = self.roi_1st_ed
        roi = warped[y:y+h, x:x+w]

        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        gray = cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        config = r'--oem 3 --psm 7'
        text = pytesseract.image_to_string(thresh, config=config).lower()

        if '1st' in text or 'edition' in text:
            return True
        return False

    def detect_language(self, warped, set_id: Optional[str]) -> str:
        """Determines card language."""
        if set_id:
            parts = set_id.split('-')
            if len(parts) > 1:
                suffix = parts[1][:2]
                if suffix in ['EN', 'DE', 'FR', 'IT', 'ES', 'PT', 'JP']:
                    return suffix

        # Scan full image instead of ROI to reduce "EN" bias from bad crops
        roi = warped
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        _, thresh = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY)

        text = pytesseract.image_to_string(thresh)
        # Reduced text length threshold
        if len(text) < 5:
            return "EN"

        try:
            lang = detect(text)
            return lang.upper()
        except LangDetectException:
            return "EN"

    def match_artwork(self, warped, reference_paths: List[str]) -> Tuple[Optional[str], int]:
        """
        Matches the card artwork against local reference images.
        Returns (best_image_path, score). Score is number of good matches.
        """
        if not reference_paths:
            return None, 0

        orb = cv2.ORB_create()
        bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)

        x, y, w, h = self.roi_art
        scan_art = warped[y:y+h, x:x+w]
        scan_kp, scan_des = orb.detectAndCompute(scan_art, None)

        if scan_des is None:
            return None, 0

        best_match_count = 0
        best_image_path = None

        for ref_path in reference_paths:
            try:
                ref_img = cv2.imread(ref_path)
                if ref_img is None:
                    continue

                h_ref, w_ref = ref_img.shape[:2]
                if 1.4 < h_ref / w_ref < 1.6: # Full card
                    ref_resized = cv2.resize(ref_img, (self.width, self.height))
                    ref_art = ref_resized[y:y+h, x:x+w]
                    kp, des = orb.detectAndCompute(ref_art, None)
                else:
                    kp, des = orb.detectAndCompute(ref_img, None)

                if des is None:
                    continue

                matches = bf.match(scan_des, des)
                good_matches = [m for m in matches if m.distance < 60]
                count = len(good_matches)

                if count > best_match_count:
                    best_match_count = count
                    best_image_path = ref_path

            except Exception as e:
                logger.error(f"Error matching artwork for {ref_path}: {e}")

        if best_match_count > 10:
            return best_image_path, best_match_count

        return None, 0

    def detect_rarity_visual(self, warped) -> str:
        """Visual fallback for rarity detection."""
        x, y, w, h = self.roi_name
        roi = warped[y:y+h, x:x+w]
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)

        lower_gold = np.array([10, 100, 100])
        upper_gold = np.array([40, 255, 255])
        mask_gold = cv2.inRange(hsv, lower_gold, upper_gold)

        lower_silver = np.array([0, 0, 150])
        upper_silver = np.array([180, 50, 255])
        mask_silver = cv2.inRange(hsv, lower_silver, upper_silver)

        gold_pixels = cv2.countNonZero(mask_gold)
        silver_pixels = cv2.countNonZero(mask_silver)
        total_pixels = w * h

        if gold_pixels > total_pixels * 0.05:
            return "Gold Rare"
        elif silver_pixels > total_pixels * 0.05:
            return "Secret Rare"

        return "Common"

    def debug_draw_rois(self, image=None):
        """Draws ROIs on the provided image."""
        if image is not None:
             canvas = image.copy()
        else:
             canvas = np.zeros((self.height, self.width, 3), dtype=np.uint8)

        def draw_roi(roi, color):
            x, y, w, h = roi
            cv2.rectangle(canvas, (x, y), (x + w, y + h), color, 2)

        # Indicate full scan for Set ID and Language (Green Border)
        cv2.rectangle(canvas, (0, 0), (self.width, self.height), (0, 255, 0), 4)

        # Draw specific ROIs that are still used
        draw_roi(self.roi_1st_ed, (255, 0, 0))
        draw_roi(self.roi_name, (0, 255, 255))
        draw_roi(self.roi_art, (255, 0, 255))

        # roi_set_id_search and roi_desc are no longer used (Full Scan)

        return canvas

    def scan_full_frame(self, frame) -> Dict[str, Any]:
        """
        Track 2: Scans the entire frame for text without relying on contour detection.
        Useful when the card is not clearly defined against the background.
        """
        # 1. Resize strategy
        # For small text like Set IDs, higher resolution is better.
        # But full 1080p+ might be slow.
        # A width of 1500px is a good balance for full-frame text detection.
        h, w = frame.shape[:2]
        target_width = 1500
        if w != target_width:
            scale = target_width / w
            frame_resized = cv2.resize(frame, (0, 0), fx=scale, fy=scale)
        else:
            frame_resized = frame

        # 2. Preprocess
        gray = cv2.cvtColor(frame_resized, cv2.COLOR_BGR2GRAY)

        # Use CLAHE (Contrast Limited Adaptive Histogram Equalization)
        # This brings out details in dark/bright areas better than global equalization
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8))
        enhanced = clahe.apply(gray)

        # Denoise before thresholding to reduce salt-and-pepper noise
        # h=10 is strength, 7/21 are window sizes
        denoised = cv2.fastNlMeansDenoising(enhanced, None, 10, 7, 21)

        # Adaptive Thresholding
        thresh = cv2.adaptiveThreshold(denoised, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2)

        # 3. OCR
        # psm 11 = Sparse text (good for finding isolated codes)
        config = r'--oem 3 --psm 11'

        # Try OCR on both the Thresholded image AND the Enhanced Grayscale
        # Sometimes thresholding destroys characters that grayscale preserves

        results_pool = []

        # Pass 1: Thresholded
        data_thresh = pytesseract.image_to_data(thresh, config=config, output_type=Output.DICT)
        results_pool.append(data_thresh)

        # Pass 2: Enhanced Grayscale (only if Pass 1 fails? No, let's run both for robustness)
        # (Optional optimization: only run if Pass 1 yields no ID)

        full_text_list = []
        matches = []

        # Regex: (3-4 alphanumeric) - (optional Region 2 chars) (3-4 alphanumeric)
        # Examples: LOB-EN001, SDY-001, BODE-EN050, MP19-EN005
        pattern = re.compile(r'\b([A-Z0-9]{3,4}-[A-Z]{0,2}?[0-9]{3})\b')

        # Combine results from all passes
        for data in results_pool:
            num_boxes = len(data['text'])
            for i in range(num_boxes):
                word = data['text'][i].strip().upper()
                if not word: continue

                # Avoid adding duplicates to full text list just for display cleanliness
                if word not in full_text_list:
                    full_text_list.append(word)

                # Check if this word looks like a set ID
                m = pattern.search(word)
                if m:
                    found_id = m.group(1)
                    conf = int(data['conf'][i])
                    if conf == -1: conf = 0
                    matches.append((found_id, conf))

        full_text = " ".join(full_text_list)

        result = {
            "track_type": "full_frame_ocr",
            "raw_text": full_text,
            "set_id": None,
            "set_id_conf": 0,
            "language": "EN" # Default
        }

        # If no exact word match, try searching the joined string (less reliable for confidence)
        if not matches:
             m = pattern.search(full_text)
             if m:
                 matches.append((m.group(1), 50)) # Arbitrary medium confidence

        if matches:
            # Pick best confidence
            matches.sort(key=lambda x: x[1], reverse=True)
            best_id, best_conf = matches[0]
            result["set_id"] = best_id
            result["set_id_conf"] = best_conf

            # Deduce language from ID
            parts = best_id.split('-')
            if len(parts) > 1:
                suffix = parts[1]
                # If suffix starts with letters, extract them
                region_match = re.match(r'^([A-Z]{2})', suffix)
                if region_match:
                    lang_code = region_match.group(1)
                    if lang_code in ['EN', 'DE', 'FR', 'IT', 'ES', 'PT', 'JP']:
                        result["language"] = lang_code

        return result
