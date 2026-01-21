import logging
import re
import os
from typing import Optional, Tuple, List, Dict, Any

try:
    import cv2
    import numpy as np
    from langdetect import detect, LangDetectException
    import easyocr
    import torch
    from paddleocr import PaddleOCR
    from ultralytics import YOLO
except ImportError:
    pass  # Handled in __init__.py

logger = logging.getLogger(__name__)

class CardScanner:
    def __init__(self):
        self.width = 600
        self.height = 875

        # ROI definitions (x, y, w, h) based on 600x875
        self.roi_set_id_search = (300, 580, 290, 80)
        self.roi_1st_ed = (20, 595, 180, 45)
        self.roi_desc = (35, 650, 530, 180)
        self.roi_art = (50, 110, 500, 490)
        self.roi_name = (30, 25, 480, 50)

        self.rois = {
            "set_id": self.roi_set_id_search,
            "first_ed": self.roi_1st_ed,
            "desc": self.roi_desc,
            "art": self.roi_art,
            "name": self.roi_name
        }

        # Lazy Init
        self.easyocr_reader = None
        self.paddle_ocr = None
        self.yolo_model = None

    def get_easyocr(self):
        if self.easyocr_reader is None:
            logger.info("Initializing EasyOCR Reader...")
            use_gpu = hasattr(torch, 'cuda') and torch.cuda.is_available()
            self.easyocr_reader = easyocr.Reader(['en'], gpu=use_gpu)
        return self.easyocr_reader

    def get_paddleocr(self):
        if self.paddle_ocr is None:
            logger.info("Initializing PaddleOCR...")
            # suppress console output
            self.paddle_ocr = PaddleOCR(use_angle_cls=True, lang='en')
        return self.paddle_ocr

    def get_yolo(self):
        if self.yolo_model is None:
            logger.info("Initializing YOLO model...")
            # Load a pretrained YOLOv8n model
            self.yolo_model = YOLO('yolov8n.pt')
        return self.yolo_model

    def preprocess_image(self, frame):
        """Basic preprocessing for contour detection."""
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        blur = cv2.GaussianBlur(gray, (5, 5), 0)
        kernel = np.ones((5, 5), np.uint8)
        grad = cv2.morphologyEx(blur, cv2.MORPH_GRADIENT, kernel)
        _, thresh = cv2.threshold(grad, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        thresh = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel, iterations=2)
        return thresh

    def get_fallback_crop(self, frame) -> np.ndarray:
        """Creates a 'center crop' of the frame."""
        h_frame, w_frame = frame.shape[:2]
        target_ar = self.width / self.height

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
        if roi_name not in self.rois:
            return None
        x, y, w, h = self.rois[roi_name]
        return warped[y:y+h, x:x+w]

    def find_card_contour(self, frame) -> Optional[np.ndarray]:
        """Classic contour detection with relaxed edge penalty."""
        thresh = self.preprocess_image(frame)
        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        if not contours:
            return None

        h_img, w_img = frame.shape[:2]
        center_x, center_y = w_img // 2, h_img // 2

        valid_contours = []
        for cnt in contours:
            if cv2.contourArea(cnt) > 25000:
                 valid_contours.append(cnt)

        contours = valid_contours

        # Relaxed Central Bias Score
        def score_contour(cnt):
            area = cv2.contourArea(cnt)
            M = cv2.moments(cnt)
            if M["m00"] != 0:
                cX = int(M["m10"] / M["m00"])
                cY = int(M["m01"] / M["m00"])
                dist_norm = np.sqrt((cX - center_x)**2 + (cY - center_y)**2) / (np.sqrt(w_img**2 + h_img**2))
            else:
                dist_norm = 1.0

            # Reduced penalty for distance (0.5 weight instead of 1.0)
            return area * (1.0 - (dist_norm * 0.5))

        contours = sorted(contours, key=score_contour, reverse=True)

        for cnt in contours[:5]:
            hull = cv2.convexHull(cnt)
            peri = cv2.arcLength(hull, True)
            approx = cv2.approxPolyDP(hull, 0.02 * peri, True)

            rect = cv2.minAreaRect(hull)
            (center), (w, h), angle = rect

            if w == 0 or h == 0: continue

            ar = w / h
            if ar > 1: ar = 1 / ar

            # Relaxed AR check (0.55 - 0.85) to be more robust
            if 0.55 < ar < 0.85:
                box = cv2.boxPoints(rect)
                box = np.int32(box)
                return box.reshape(4, 1, 2)

        return None

    def find_card_yolo(self, frame) -> Optional[np.ndarray]:
        """Finds card using YOLO object detection."""
        model = self.get_yolo()
        # Run inference
        results = model(frame, verbose=False)

        # Look for the best bounding box
        # Assuming class 0 is often 'person', but generic YOLOv8n might detect 'book' or 'cell phone' or similar for card.
        # Actually, standard YOLO classes don't include 'yugioh card'.
        # But 'book', 'remote', 'cell phone' might be detected.
        # Ideally we'd retrain, but for now we look for *any* prominent object that fits the AR.
        # OR we just take the largest detection.

        best_box = None
        max_area = 0
        h_img, w_img = frame.shape[:2]

        for result in results:
            boxes = result.boxes
            for box in boxes:
                x1, y1, x2, y2 = box.xyxy[0].tolist()
                w = x2 - x1
                h = y2 - y1
                area = w * h

                ar = w / h
                if ar > 1: ar = 1 / ar

                # Check AR match
                if 0.55 < ar < 0.85 and area > 25000:
                    if area > max_area:
                        max_area = area
                        best_box = [x1, y1, x2, y2]

        if best_box:
            x1, y1, x2, y2 = best_box
            # Convert to contour format (TL, TR, BR, BL)
            cnt = np.array([
                [[x1, y1]],
                [[x2, y1]],
                [[x2, y2]],
                [[x1, y2]]
            ], dtype=np.int32)
            return cnt

        return None

    def warp_card(self, frame, contour) -> np.ndarray:
        pts = contour.reshape(4, 2)
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

    def ocr_scan(self, image: np.ndarray, engine: str = 'easyocr') -> Dict[str, Any]:
        """
        Runs OCR on the provided image using the specified engine.
        Returns a dict with 'raw_text', 'set_id', 'set_id_conf', 'language'.
        """
        raw_text_list = []
        confidences = []

        # Resize for better small text detection (standard strategy)
        h, w = image.shape[:2]
        if w < 1000:
             scale = 1600 / w
             image = cv2.resize(image, (0, 0), fx=scale, fy=scale)

        if engine == 'paddle':
            ocr = self.get_paddleocr()
            # result = [[[[x1,y1],...], (text, conf)], ...]
            try:
                result = ocr.ocr(image, cls=True)
            except TypeError:
                # Fallback for versions where cls arg is unexpected
                result = ocr.ocr(image)

            if result and result[0]:
                for line in result[0]:
                    text = line[1][0]
                    conf = line[1][1]
                    raw_text_list.append(text)
                    confidences.append(conf)
        else: # easyocr
            reader = self.get_easyocr()
            # mag_ratio=1.5 for better small text
            results = reader.readtext(image, detail=1, paragraph=False, mag_ratio=1.5)
            for (bbox, text, conf) in results:
                raw_text_list.append(text)
                confidences.append(conf)

        full_text = " | ".join(raw_text_list)

        # Parse Set ID
        set_id, set_id_conf, lang = self._parse_set_id(raw_text_list, confidences)

        return {
            "engine": engine,
            "raw_text": full_text,
            "set_id": set_id,
            "set_id_conf": set_id_conf * 100, # Normalize to 0-100
            "language": lang
        }

    def _parse_set_id(self, texts: List[str], confs: List[float]) -> Tuple[Optional[str], float, str]:
        """Extracts Set ID and Language from text lines."""
        pattern = re.compile(r'([A-Z0-9]{3,4})[- ]?([A-Z]{0,2})?([0-9]{3})')
        matches = []

        for i, text in enumerate(texts):
            text = text.strip().upper()
            clean_text = text.replace(" ", "")
            m = pattern.search(clean_text)
            if m:
                prefix = m.group(1)
                region = m.group(2) if m.group(2) else ""
                number = m.group(3)

                # Reconstruct standardized ID
                found_id = f"{prefix}-{region}{number}" if region else f"{prefix}-{number}"

                # Score based on OCR confidence + bonus for 'EN' or known region
                score = confs[i]
                if region in ['EN', 'DE', 'FR', 'IT', 'ES', 'PT', 'JP']:
                    score += 0.2

                matches.append((found_id, score, region))

        if not matches:
            return None, 0.0, "EN"

        matches.sort(key=lambda x: x[1], reverse=True)
        best_id, best_score, best_region = matches[0]

        # Deduce language
        lang = "EN"
        if best_region in ['EN', 'DE', 'FR', 'IT', 'ES', 'PT', 'JP']:
            lang = best_region

        return best_id, best_score, lang

    def detect_first_edition(self, warped, engine='easyocr') -> bool:
        """Checks for '1st Edition' text using generic OCR on ROI."""
        x, y, w, h = self.roi_1st_ed
        roi = warped[y:y+h, x:x+w]

        # Fast scan on ROI
        res = self.ocr_scan(roi, engine=engine)
        text = res['raw_text'].lower()

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

        # Fallback: check full text using langdetect on a sample
        # We can re-use OCR result from previous steps if available,
        # but here we might need a quick check if set_id failed.
        # For efficiency, we assume the caller passes the best info they have.
        # If we really need to scan:
        try:
             # Just crop the description box for language detection
             x, y, w, h = self.roi_desc
             roi = warped[y:y+h, x:x+w]
             # Run a quick OCR? Or assume the caller has full text?
             # Let's run a quick EasyOCR on description if needed.
             res = self.ocr_scan(roi, engine='easyocr')
             text = res['raw_text']
             if len(text) > 5:
                  return detect(text).upper()
        except:
             pass

        return "EN"

    def match_artwork(self, warped, reference_paths: List[str]) -> Tuple[Optional[str], int]:
        """Matches artwork using ORB features."""
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
                if ref_img is None: continue

                h_ref, w_ref = ref_img.shape[:2]
                if 1.4 < h_ref / w_ref < 1.6:
                    ref_resized = cv2.resize(ref_img, (self.width, self.height))
                    ref_art = ref_resized[y:y+h, x:x+w]
                    kp, des = orb.detectAndCompute(ref_art, None)
                else:
                    kp, des = orb.detectAndCompute(ref_img, None)

                if des is None: continue

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

        cv2.rectangle(canvas, (0, 0), (self.width, self.height), (0, 255, 0), 4)
        draw_roi(self.roi_set_id_search, (0, 0, 255))
        draw_roi(self.roi_1st_ed, (255, 0, 0))
        draw_roi(self.roi_name, (0, 255, 255))
        draw_roi(self.roi_art, (255, 0, 255))

        return canvas
