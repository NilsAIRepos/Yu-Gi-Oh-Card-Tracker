import logging
import re
import os
from typing import Optional, Tuple, List, Dict, Any

# Force legacy keras for keras-ocr compatibility with TF 2.x
os.environ["TF_USE_LEGACY_KERAS"] = "1"

try:
    import cv2
    import numpy as np
except ImportError:
    pass

try:
    import torch
    from ultralytics import YOLO
except ImportError:
    pass

try:
    from langdetect import detect, LangDetectException
except ImportError:
    pass

try:
    import easyocr
except ImportError:
    pass

try:
    # New Engines
    import keras_ocr
    from doctr.io import DocumentFile
    from doctr.models import ocr_predictor
    from mmocr.apis import MMOCRInferencer
except ImportError:
    pass  # Handled in __init__.py or via lazy loading checks

if 'src.services.scanner.models' in locals() or 'src.services.scanner.models' in globals():
    from src.services.scanner.models import OCRResult
else:
    try:
        from src.services.scanner.models import OCRResult
    except ImportError:
        pass

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
        self.yolo_model = None
        self.yolo_model_name = None
        self.yolo_cls_model = None
        self.yolo_cls_model_name = None
        self.keras_pipeline = None
        self.mmocr_inferencer = None
        self.doctr_model = None

    def get_easyocr(self):
        if self.easyocr_reader is None:
            logger.info("Initializing EasyOCR Reader...")
            use_gpu = hasattr(torch, 'cuda') and torch.cuda.is_available()
            self.easyocr_reader = easyocr.Reader(['en'], gpu=use_gpu)
        return self.easyocr_reader

    def get_yolo(self, model_name: str = 'yolov8l.pt'):
        # If model is not loaded or loaded model is different from requested
        if self.yolo_model is None or self.yolo_model_name != model_name:
            logger.info(f"Initializing YOLO model ({model_name})...")
            try:
                self.yolo_model = YOLO(model_name)
                self.yolo_model_name = model_name
            except Exception as e:
                logger.error(f"Failed to load YOLO model {model_name}: {e}. Falling back to yolov8l.pt")
                if model_name != 'yolov8l.pt':
                    return self.get_yolo('yolov8l.pt')
                else:
                    raise
        return self.yolo_model

    def get_yolo_cls(self, model_name: str = 'yolo26l-cls.pt'):
        # If model is not loaded or loaded model is different from requested
        if self.yolo_cls_model is None or self.yolo_cls_model_name != model_name:
            logger.info(f"Initializing YOLO CLS model ({model_name})...")
            try:
                self.yolo_cls_model = YOLO(model_name)
                self.yolo_cls_model_name = model_name
            except Exception as e:
                logger.error(f"Failed to load YOLO CLS model {model_name}: {e}.")
                # Fallback to yolov8n-cls.pt if 26 is not found?
                # User specifically asked for YOLO 26.
                # If it fails, we assume it might be a download issue or typo.
                # We'll re-raise for now.
                raise
        return self.yolo_cls_model

    def extract_yolo_features(self, image: 'np.ndarray', model_name: str = 'yolo26l-cls.pt') -> Optional['np.ndarray']:
        """
        Extracts feature embedding from the image using the classification model.
        Returns a numpy array (1D vector).
        """
        try:
            model = self.get_yolo_cls(model_name)

            # Use 'embed' if available in this version of Ultralytics
            # results = model.embed(image) # Not always standard

            # Standard approach: Forward pass with hook or stripping head
            # For YOLOv8/26 CLS, the model ends with a 'Classify' head.
            # We want the output of the pooling layer (before the linear layer).

            # Let's inspect the model structure dynamically if possible, or use a hook.
            # A robust way is to use `embed=[layer_index]` in predict if supported,
            # but usually it's `model.predict(..., embed=True)` in very new versions.

            # Let's try the hook approach on the penultimate layer of the head or the backbone output.
            # The 'Classify' head in YOLOv8 is usually:
            #   self.conv
            #   self.pool
            #   self.drop
            #   self.linear

            # We want the output of `self.pool` (and flatten).

            # However, `ultralytics` models wrap this.
            # Let's try to run `model(image)` and see if we can get features.
            # If not, we register a hook.

            # Simplest for now: The output of the backbone (before head) or global pool.

            # We will use a forward hook on the 'model.model[-1].linear' to capture its INPUT.
            # The input to the linear layer is the feature vector.

            features = []
            def hook(module, input, output):
                # input is a tuple, taking the first element
                # Flatten it
                feat = input[0].flatten().cpu().numpy()
                features.append(feat)

            # Find the linear layer
            # model.model is the DetectionModel or ClassificationModel
            # model.model.model is the Sequential
            # model.model.model[-1] is usually the Head (Classify)

            # We need to be careful about traversing.

            # Let's assume standard structure:
            head = model.model.model[-1]
            if hasattr(head, 'linear'):
                handle = head.linear.register_forward_hook(hook)
            else:
                # Fallback: maybe it's just a linear layer?
                if isinstance(head, torch.nn.Linear):
                    handle = head.register_forward_hook(hook)
                else:
                    logger.error("Could not find linear layer in YOLO CLS head")
                    return None

            # Run inference
            try:
                model(image, verbose=False)
            finally:
                handle.remove()

            if features:
                return features[0]
            return None

        except Exception as e:
            logger.error(f"Error extracting YOLO features: {e}")
            return None

    def calculate_similarity(self, embedding1: 'np.ndarray', embedding2: 'np.ndarray') -> float:
        """Calculates Cosine Similarity between two embeddings."""
        if embedding1 is None or embedding2 is None:
            return 0.0

        norm_1 = np.linalg.norm(embedding1)
        norm_2 = np.linalg.norm(embedding2)

        if norm_1 == 0 or norm_2 == 0:
            return 0.0

        return np.dot(embedding1, embedding2) / (norm_1 * norm_2)

    def get_keras(self):
        if self.keras_pipeline is None:
            logger.info("Initializing Keras-OCR...")
            # Keras-OCR loads models automatically on first use of Pipeline
            self.keras_pipeline = keras_ocr.pipeline.Pipeline()
        return self.keras_pipeline

    def get_mmocr(self):
        if self.mmocr_inferencer is None:
            logger.info("Initializing MMOCR...")
            # Using DBNet for detection and SAR for recognition by default
            try:
                device = 'cuda' if hasattr(torch, 'cuda') and torch.cuda.is_available() else 'cpu'
                self.mmocr_inferencer = MMOCRInferencer(det='DBNet', rec='SAR', device=device)
            except Exception as e:
                logger.error(f"Failed to init MMOCR: {e}")
                raise
        return self.mmocr_inferencer

    def get_doctr(self):
        if self.doctr_model is None:
            logger.info("Initializing DocTR...")
            # pretrained=True downloads models if needed
            self.doctr_model = ocr_predictor(det_arch='db_resnet50', reco_arch='crnn_vgg16_bn', pretrained=True)
            if hasattr(torch, 'cuda') and torch.cuda.is_available():
                self.doctr_model = self.doctr_model.cuda()
        return self.doctr_model

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
        try:
            h_frame, w_frame = frame.shape[:2]
            target_ar = self.width / self.height

            crop_h = int(h_frame * 0.7)
            crop_w = int(crop_h * target_ar)

            if crop_w > w_frame:
                crop_w = int(w_frame * 0.8)
                crop_h = int(crop_w / target_ar)

            x_start = max(0, (w_frame - crop_w) // 2)
            y_start = max(0, (h_frame - crop_h) // 2)

            crop = frame[y_start:y_start+crop_h, x_start:x_start+crop_w]

            if crop.size == 0:
                 return cv2.resize(frame, (self.width, self.height))

            resized = cv2.resize(crop, (self.width, self.height))
            return resized
        except Exception as e:
            logger.error(f"Fallback crop error: {e}")
            return cv2.resize(frame, (self.width, self.height))

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

    def find_card_yolo(self, frame, model_name='yolov8l.pt') -> Optional[np.ndarray]:
        """Finds card using YOLO object detection (Supports AABB and OBB)."""
        model = self.get_yolo(model_name)
        # Run inference
        results = model(frame, verbose=False)

        best_box = None
        max_area = 0
        h_img, w_img = frame.shape[:2]

        for result in results:
            # Check for OBB results first
            if result.obb is not None:
                for obb in result.obb:
                    # OBB format: xyxyxyxy (4 points)
                    # shape: (1, 4, 2)
                    points = obb.xyxyxyxy.cpu().numpy().reshape(4, 2)

                    # Calculate Area via Polygon
                    cnt_temp = points.astype(np.int32)
                    area = cv2.contourArea(cnt_temp)

                    if area > 25000:
                         if area > max_area:
                             max_area = area
                             # Ensure shape is (4, 1, 2)
                             best_box = cnt_temp.reshape(4, 1, 2)

            # Fallback to Axis-Aligned Boxes if no OBB found (or mixed usage)
            elif result.boxes is not None:
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
                            # Convert AABB to contour (TL, TR, BR, BL)
                            best_box = np.array([
                                [[x1, y1]],
                                [[x2, y1]],
                                [[x2, y2]],
                                [[x1, y2]]
                            ], dtype=np.int32)

        return best_box

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

    def ocr_scan(self, image: np.ndarray, engine: str = 'easyocr') -> 'OCRResult':
        """
        Runs OCR on the provided image using the specified engine.
        Returns an OCRResult Pydantic model.
        """
        raw_text_list = []
        confidences = []
        full_text = ""
        set_id = None
        set_id_conf = 0.0
        lang = "EN"

        try:
            # Resize for better small text detection (standard strategy)
            h, w = image.shape[:2]
            if w < 1000:
                 scale = 1600 / w
                 image = cv2.resize(image, (0, 0), fx=scale, fy=scale)

            if engine == 'keras':
                pipeline = self.get_keras()
                # Keras-OCR expects a list of images
                # And it might expect RGB? OpenCV reads BGR.
                # Keras-OCR uses keras_ocr.tools.read which uses imageio/cv2 logic but pipeline expects numpy array
                rgb_img = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
                prediction_groups = pipeline.recognize([rgb_img])
                # prediction_groups[0] contains list of (text, box)
                for text, box in prediction_groups[0]:
                    raw_text_list.append(text)
                    confidences.append(0.9) # Keras-OCR doesn't provide confidence easily, assumes high

            elif engine == 'mmocr':
                mm = self.get_mmocr()
                # MMOCRInferencer handles BGR/RGB? Usually BGR via OpenCV is fine.
                result = mm(image, return_vis=False)
                # Structure: {'predictions': [{'rec_texts': [...], 'rec_scores': [...]}]}
                if result and 'predictions' in result:
                    pred = result['predictions'][0]
                    texts = pred.get('rec_texts', [])
                    scores = pred.get('rec_scores', [])
                    raw_text_list.extend(texts)
                    confidences.extend(scores)

            elif engine == 'doctr':
                model = self.get_doctr()
                # DocTR expects RGB
                rgb_img = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
                # Pass list of numpy arrays directly to the model
                result = model([rgb_img])
                # Iterate result: Document -> Page -> Block -> Line -> Word
                for page in result.pages:
                    for block in page.blocks:
                        for line in block.lines:
                            for word in line.words:
                                raw_text_list.append(word.value)
                                confidences.append(word.confidence)

            elif engine == 'easyocr':
                reader = self.get_easyocr()
                results = reader.readtext(image, detail=1, paragraph=False, mag_ratio=1.5)
                for (bbox, text, conf) in results:
                    raw_text_list.append(text)
                    confidences.append(conf)

            else:
                logger.warning(f"Unknown OCR engine: {engine}")

            full_text = " | ".join(raw_text_list)

            # Parse Set ID
            set_id, set_id_conf, lang = self._parse_set_id(raw_text_list, confidences)

        except Exception as e:
            logger.error(f"OCR Scan Error ({engine}): {e}")
            full_text = " | ".join(raw_text_list)

        return OCRResult(
            engine=engine,
            raw_text=full_text,
            set_id=set_id,
            set_id_conf=set_id_conf * 100, # Normalize to 0-100
            language=lang
        )

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

                found_id = f"{prefix}-{region}{number}" if region else f"{prefix}-{number}"

                # Handle mismatch in list lengths (defensive)
                score = 0.0
                if i < len(confs):
                    score = confs[i]

                if region in ['EN', 'DE', 'FR', 'IT', 'ES', 'PT', 'JP']:
                    score += 0.2

                matches.append((found_id, score, region))

        if not matches:
            return None, 0.0, "EN"

        matches.sort(key=lambda x: x[1], reverse=True)
        best_id, best_score, best_region = matches[0]

        lang = "EN"
        if best_region in ['EN', 'DE', 'FR', 'IT', 'ES', 'PT', 'JP']:
            lang = best_region

        return best_id, best_score, lang

    def detect_first_edition(self, warped, engine='easyocr') -> bool:
        """Checks for '1st Edition' text using generic OCR on ROI."""
        x, y, w, h = self.roi_1st_ed
        roi = warped[y:y+h, x:x+w]

        res = self.ocr_scan(roi, engine=engine)
        text = res.raw_text.lower()

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

        try:
             x, y, w, h = self.roi_desc
             roi = warped[y:y+h, x:x+w]
             res = self.ocr_scan(roi, engine='easyocr')
             text = res.raw_text
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
