import logging
import re
import os
import json
import itertools
from typing import Optional, Tuple, List, Dict, Any

try:
    import cv2
    import numpy as np
except ImportError:
    cv2 = None
    np = None

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
    from doctr.io import DocumentFile
    from doctr.models import ocr_predictor
except ImportError:
    pass  # Handled in __init__.py or via lazy loading checks

if 'src.services.scanner.models' in locals() or 'src.services.scanner.models' in globals():
    from src.services.scanner.models import OCRResult
else:
    try:
        from src.services.scanner.models import OCRResult
    except ImportError:
        pass

# Import mappings from utils
try:
    from src.core.utils import REGION_TO_LANGUAGE_MAP, LANGUAGE_TO_LEGACY_REGION_MAP
except ImportError:
    # Fallback if utils not available in test context
    REGION_TO_LANGUAGE_MAP = {
        'E': 'EN', 'G': 'DE', 'F': 'FR', 'I': 'IT', 'S': 'ES', 'P': 'PT', 'J': 'JP', 'K': 'KR',
        'AE': 'EN', 'EN': 'EN', 'DE': 'DE', 'FR': 'FR', 'IT': 'IT', 'ES': 'ES', 'PT': 'PT', 'JP': 'JP', 'KR': 'KR'
    }
    LANGUAGE_TO_LEGACY_REGION_MAP = {
        'EN': 'E', 'DE': 'G', 'FR': 'F', 'IT': 'I', 'ES': 'S', 'PT': 'P', 'JP': 'J', 'KR': 'K'
    }

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
        self.doctr_model = None

        self.valid_set_codes = set()
        self.valid_card_names_norm = {} # normalized_str -> original_name

        # Translation Table for Normalization
        # Maps accented characters to their base ASCII equivalents
        self.TRANS_TABLE = str.maketrans({
            'ä': 'a', 'ö': 'o', 'ü': 'u',
            'Ä': 'A', 'Ö': 'O', 'Ü': 'U',
            'â': 'a', 'ê': 'e', 'î': 'i', 'ô': 'o', 'û': 'u',
            'Â': 'A', 'Ê': 'E', 'Î': 'I', 'Ô': 'O', 'Û': 'U',
            'à': 'a', 'è': 'e', 'ì': 'i', 'ò': 'o', 'ù': 'u',
            'À': 'A', 'È': 'E', 'Ì': 'I', 'Ò': 'O', 'Ù': 'U',
            'á': 'a', 'é': 'e', 'í': 'i', 'ó': 'o', 'ú': 'u',
            'Á': 'A', 'É': 'E', 'Í': 'I', 'Ó': 'O', 'Ú': 'U',
            'ñ': 'n', 'Ñ': 'N', 'ß': 's'
        })

        self._load_validation_data()

    def _normalize_card_name(self, text: str) -> str:
        """
        Normalizes card name text:
        1. Translates accented characters to ASCII base.
        2. Converts to lowercase.
        3. Strips non-alphanumeric characters.
        """
        if not text:
            return ""
        # Translate first, then lower, then strip
        text_trans = text.translate(self.TRANS_TABLE)
        text_lower = text_trans.lower()
        text_clean = re.sub(r'[^a-z0-9]', '', text_lower)
        return text_clean

    def _load_validation_data(self):
        try:
            db_dir = os.path.join(os.getcwd(), "data", "db")
            if not os.path.exists(db_dir):
                return

            # Helper to generate localized codes
            # Standard 2-letter regions
            supported_2_letter = ['EN', 'DE', 'FR', 'IT', 'ES', 'PT', 'JP', 'KR', 'AE']

            files = [f for f in os.listdir(db_dir) if f.startswith("card_db") and f.endswith(".json")]

            # Temporary set to avoid duplicates during processing
            loaded_names = set()

            for fname in files:
                path = os.path.join(db_dir, fname)
                is_main_db = (fname == "card_db.json")

                try:
                    with open(path, 'r', encoding='utf-8') as f:
                        data = json.load(f)

                        for card in data:
                            # Load Card Names
                            if 'name' in card:
                                name = card['name']
                                if name not in loaded_names:
                                    loaded_names.add(name)

                                    # Use centralized normalization
                                    norm = self._normalize_card_name(name)
                                    self.valid_card_names_norm[norm] = name

                            # Load Set Codes
                            if 'card_sets' in card and card['card_sets']:
                                for s in card['card_sets']:
                                    code = s['set_code']
                                    self.valid_set_codes.add(code)

                                    # If Main DB, generate localized variants
                                    if is_main_db:
                                        self._generate_localized_codes(code, supported_2_letter)
                except Exception as e:
                    logger.error(f"Error loading {fname}: {e}")

            logger.info(f"Loaded {len(self.valid_set_codes)} set codes and {len(loaded_names)} card names.")
        except Exception as e:
            logger.error(f"Failed to load validation data: {e}")

    def _generate_localized_codes(self, en_code: str, supported_2_letter: List[str]):
        """Generates localized set codes based on English code format."""
        # Regex to parse: Prefix-RegionNumber
        m = re.match(r'^([A-Z0-9]+)-([A-Z]+)(\d+)$', en_code)
        if not m: return

        prefix, region, number = m.groups()

        # Logic:
        # If region is 2 letters (e.g. EN) -> Generate all supported 2-letter codes
        # If region is 1 letter (e.g. E) -> Generate all supported 1-letter codes via mapping

        if len(region) == 2:
            for lang in supported_2_letter:
                if lang != region:
                    self.valid_set_codes.add(f"{prefix}-{lang}{number}")

        elif len(region) == 1:
            # Find which language this legacy code belongs to (usually E=EN)
            # Use LANGUAGE_TO_LEGACY_REGION_MAP values to find other legacy codes
            for lang_code, legacy_char in LANGUAGE_TO_LEGACY_REGION_MAP.items():
                if legacy_char != region:
                    self.valid_set_codes.add(f"{prefix}-{legacy_char}{number}")

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

    def extract_yolo_features(self, image: Any, model_name: str = 'yolo26l-cls.pt') -> Optional[Any]:
        """
        Extracts feature embedding from the image using the classification model.
        Returns a numpy array (1D vector).
        """
        try:
            model = self.get_yolo_cls(model_name)
            features = []
            def hook(module, input, output):
                # input is a tuple, taking the first element
                # Flatten it
                feat = input[0].flatten().cpu().numpy()
                features.append(feat)

            # Find the linear layer
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

    def calculate_similarity(self, embedding1: Any, embedding2: Any) -> float:
        """Calculates Cosine Similarity between two embeddings."""
        if embedding1 is None or embedding2 is None:
            return 0.0

        norm_1 = np.linalg.norm(embedding1)
        norm_2 = np.linalg.norm(embedding2)

        if norm_1 == 0 or norm_2 == 0:
            return 0.0

        return np.dot(embedding1, embedding2) / (norm_1 * norm_2)

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

    def get_fallback_crop(self, frame) -> Any:
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

    def get_roi_crop(self, warped, roi_name: str) -> Optional[Any]:
        if roi_name not in self.rois:
            return None
        x, y, w, h = self.rois[roi_name]
        return warped[y:y+h, x:x+w]

    def find_card_contour(self, frame) -> Optional[Any]:
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

    def find_card_contour_white_bg(self, frame) -> Optional[Any]:
        """
        Optimized contour detection for white backgrounds.
        Uses inverted thresholding to isolate darker cards from light background.
        """
        # 1. Convert to Gray
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        # 2. Gaussian Blur
        blur = cv2.GaussianBlur(gray, (5, 5), 0)

        # 3. Inverted Threshold (Background white -> Black, Card dark -> White)
        _, thresh = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

        # 4. Morphological Cleanup
        kernel = np.ones((5, 5), np.uint8)
        thresh = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel, iterations=2)

        # 5. Find Contours
        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        if not contours:
            return None

        h_img, w_img = frame.shape[:2]
        total_area = h_img * w_img
        candidates = []

        for cnt in contours:
            area = cv2.contourArea(cnt)

            # Ignore small noise/internal boxes (relative to frame size)
            if area < total_area * 0.02:
                continue

            # Ignore massive blobs (e.g. lighting shadows covering whole frame)
            if area > total_area * 0.95:
                continue

            hull = cv2.convexHull(cnt)
            peri = cv2.arcLength(hull, True)
            approx = cv2.approxPolyDP(hull, 0.02 * peri, True)

            if len(approx) == 4:
                rect_cnt = approx
            else:
                rect = cv2.minAreaRect(hull)
                box = cv2.boxPoints(rect)
                rect_cnt = np.int32(box)

            # Aspect Ratio Check
            rect = cv2.minAreaRect(rect_cnt)
            (center), (w, h), angle = rect

            if w == 0 or h == 0: continue

            ar = w / h
            if ar > 1: ar = 1 / ar

            # Relaxed AR filter for cards (handles stacked cards/skew)
            if 0.55 < ar < 0.95:
                # Store (Area, Contour)
                candidates.append((area, rect_cnt))

        # Sort by Area Descending (Largest valid object is likely the card, not the art box)
        candidates.sort(key=lambda x: x[0], reverse=True)

        if candidates:
            # Return the largest candidate
            return candidates[0][1].reshape(4, 1, 2)

        return None

    def find_card_yolo(self, frame, model_name='yolov8l.pt') -> Optional[Any]:
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

    def warp_card(self, frame, contour) -> Any:
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

    def ocr_scan(self, image: Any, engine: str = 'easyocr', scope: str = 'full') -> 'OCRResult':
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
        card_name = None

        try:
            # Resize for better small text detection (standard strategy)
            h, w = image.shape[:2]
            if w < 1000:
                 scale = 1600 / w
                 image = cv2.resize(image, (0, 0), fx=scale, fy=scale)

            if engine == 'doctr':
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

                # Card Name Extraction (DocTR + Crop or Full)
                card_name = self._parse_card_name(result, engine, scope=scope)

            elif engine == 'easyocr':
                reader = self.get_easyocr()
                results = reader.readtext(image, detail=1, paragraph=False, mag_ratio=1.5)
                for (bbox, text, conf) in results:
                    raw_text_list.append(text)
                    confidences.append(conf)

            else:
                logger.warning(f"Unknown OCR engine: {engine}")

            full_text = " | ".join(raw_text_list)

            # Parse Set ID (pass full_text for global regex fallback)
            set_id, set_id_conf, lang = self._parse_set_id(raw_text_list, confidences, full_text)

        except Exception as e:
            logger.error(f"OCR Scan Error ({engine}): {e}")
            full_text = " | ".join(raw_text_list)

        # Extract Stats & Type
        atk, def_val = self._extract_stats(full_text)
        card_type = self._detect_card_type(full_text)

        return OCRResult(
            engine=engine,
            scope=scope,
            raw_text=full_text,
            set_id=set_id,
            card_name=card_name,
            set_id_conf=set_id_conf * 100, # Normalize to 0-100
            language=lang,
            atk=atk,
            def_val=def_val,
            card_type=card_type
        )

    def _extract_stats(self, text: str) -> Tuple[Optional[str], Optional[str]]:
        """Extracts ATK and DEF values from text."""
        # Simple regex for ATK/DEF
        # Supports ATK/1800, ATK / 1800, ATK: 1800 etc.
        # And ? for values.
        atk_match = re.search(r'ATK\s*[:/.]?\s*([0-9?]+)', text, re.IGNORECASE)
        def_match = re.search(r'DEF\s*[:/.]?\s*([0-9?]+)', text, re.IGNORECASE)

        atk = atk_match.group(1) if atk_match else None
        def_val = def_match.group(1) if def_match else None
        return atk, def_val

    def _detect_card_type(self, text: str) -> Optional[str]:
        """Detects Spell/Trap keywords early in the text."""
        # Restrict to first 100 characters to ensure we are looking at the top of the card
        # (Relative beginning of OCR text)
        limit = 100
        upper_text = text[:limit].upper()

        # Prioritize finding these keywords
        if "SPELL CARD" in upper_text or "SPELL | CARD" in upper_text or "CARTE MAGIE" in upper_text or "ZAUBERKARTE" in upper_text:
            return "Spell"
        if "TRAP CARD" in upper_text or "TRAP | CARD" in upper_text or "CARTE PIÈGE" in upper_text or "FALLENKARTE" in upper_text:
            return "Trap"
        return None

    def _parse_card_name(self, raw_result: Any, engine: str, scope: str = 'full') -> Optional[str]:
        """Extracts card name from OCR result using Robust Database Matching."""
        if engine != 'doctr':
             return None

        try:
            # Helper to check a string candidate
            def check_candidate(candidate_str):
                if not candidate_str or len(candidate_str) < 3: return None

                # Normalize OCR output using the same logic as DB loading
                # This ensures characters like 'Â' are mapped to 'A' instead of stripped
                norm = self._normalize_card_name(candidate_str)

                if norm in self.valid_card_names_norm:
                    return self.valid_card_names_norm[norm]

                return None

            # Iterate blocks/lines
            for page in raw_result.pages:
                for block in page.blocks:
                    # 1. Check Full Block
                    block_text_lines = []
                    for line in block.lines:
                        line_text = " ".join([w.value for w in line.words])
                        block_text_lines.append(line_text)

                    full_block = " ".join(block_text_lines).strip()
                    match = check_candidate(full_block)
                    if match: return match

                    # 2. Check Individual Lines (Fallback)
                    for line_txt in block_text_lines:
                        match = check_candidate(line_txt.strip())
                        if match: return match

        except Exception as e:
            logger.error(f"Error parsing card name (DocTR): {e}")

        return None

    def _parse_set_id(self, texts: List[str], confs: List[float], full_text: str = "") -> Tuple[Optional[str], float, str]:
        """Extracts Set ID and Language from text lines."""
        # Groups: 1=Prefix, 2=Region(Optional), 3=Number
        # Allow letters in Number group for typo detection (S, O, Z, etc.)
        pattern = re.compile(r'([A-Z0-9]{3,4})[- ]?([A-Z0-9]{0,2})?[-]?([A-Z0-9]{3})')

        candidates = []

        typo_map = {
            'S': '5', 'I': '1', 'O': '0', 'Z': '7',
            'B': '8', 'G': '6', 'Q': '0', 'D': '0'
        }

        # Extended map for Prefix generation (Bidirectional)
        prefix_confusions = {
            '1': ['I', 'L'], 'I': ['1', 'L'], 'L': ['1', 'I'],
            '0': ['O', 'Q', 'D'], 'O': ['0', 'Q', 'D'], 'Q': ['0', 'O'], 'D': ['0', 'O'],
            '5': ['S'], 'S': ['5'],
            '8': ['B'], 'B': ['8'],
            '2': ['Z'], 'Z': ['2', '7'], '7': ['Z'],
            '6': ['G'], 'G': ['6']
        }

        def normalize_number_part(txt):
            res = ""
            for char in txt:
                res += typo_map.get(char, char)
            return res

        def generate_prefix_variants(txt):
            if not txt: return {txt}

            # Build options for each character position
            char_options = []
            for char in txt:
                opts = {char} # Set to dedup
                if char in prefix_confusions:
                    opts.update(prefix_confusions[char])
                char_options.append(list(opts))

            variants = set()
            # Cartesian product
            for p in itertools.product(*char_options):
                variants.add("".join(p))
            return variants

        def validate_and_score(raw_code, region_part, base_conf, list_index):
            is_valid = raw_code in self.valid_set_codes

            score = base_conf

            # 1. DB Validity Boost (Highest Importance)
            if is_valid:
                score += 0.5

            # 2. Region Format Validation & Boost
            # Standard 2-letter or Legacy 1-letter
            if region_part in REGION_TO_LANGUAGE_MAP or region_part in LANGUAGE_TO_LEGACY_REGION_MAP.values():
                score += 0.2
            elif len(region_part) > 0:
                 # Penalty for unknown region codes (e.g. random letters)
                 score -= 0.1

            # 3. Penalize All-Number Prefixes (e.g. 8552-0851)
            parts = raw_code.split('-')
            if parts and parts[0].isdigit():
                score -= 0.5

            # 4. Position Weighting (Prefer earlier candidates)
            # Assumption: Set ID is usually in the first few lines or bottom (if not cropped properly).
            # But prompt says "more in the beginning of the output text".
            # Penalty increases with index.
            if list_index < 5:
                score += 0.1
            else:
                score -= (list_index * 0.01)

            return raw_code, score

        # A. Line-by-line Search
        for i, text in enumerate(texts):
            # Try both raw (stripped) and space-merged versions
            text_stripped = text.strip().upper()
            text_merged = text_stripped.replace(" ", "")

            # Use a set to avoid duplicates per line
            line_candidates = set()

            for t_in in [text_stripped, text_merged]:
                matches = pattern.finditer(t_in)
                for m in matches:
                    prefix = m.group(1)
                    region = m.group(2) if m.group(2) else ""
                    number_raw = m.group(3)

                    # 1. Direct Match
                    code_direct = f"{prefix}-{region}{number_raw}" if region else f"{prefix}-{number_raw}"
                    line_candidates.add((code_direct, region, number_raw))

                    # 2. Typo Fixes
                    number_fixed = normalize_number_part(number_raw)
                    region_fixed = region.replace('0', 'O')

                    code_fixed = f"{prefix}-{region_fixed}{number_fixed}" if region_fixed else f"{prefix}-{number_fixed}"
                    line_candidates.add((code_fixed, region_fixed, number_fixed))

                    # 3. Prefix Variations (Permutations)
                    prefix_variants = generate_prefix_variants(prefix)
                    for p_var in prefix_variants:
                        if p_var == prefix: continue # Already added above

                        # Variant with raw number
                        c_raw = f"{p_var}-{region}{number_raw}" if region else f"{p_var}-{number_raw}"
                        line_candidates.add((c_raw, region, number_raw))

                        # Variant with fixed number
                        c_fixed = f"{p_var}-{region_fixed}{number_fixed}" if region_fixed else f"{p_var}-{number_fixed}"
                        line_candidates.add((c_fixed, region_fixed, number_fixed))

            # Process candidates for this line
            base_conf = confs[i] if i < len(confs) else 0.5

            for code, region, num_part in line_candidates:
                # Basic sanity check: Number part must be digits after fix
                if num_part.isdigit():
                    v_code, v_score = validate_and_score(code, region, base_conf, i)
                    candidates.append((v_code, v_score, region))

        # B. Fallback: Full Text Regex
        if not candidates and full_text:
             clean_full = full_text.upper().replace(" ", "")
             matches = pattern.finditer(clean_full)
             for m in matches:
                prefix = m.group(1)
                region = m.group(2) if m.group(2) else ""
                number_raw = m.group(3)

                number_fixed = normalize_number_part(number_raw)
                region_fixed = region.replace('0', 'O')

                if number_fixed.isdigit():
                    # Generate Prefix Variants
                    prefix_variants = generate_prefix_variants(prefix)
                    for p_var in prefix_variants:
                        code_cand = f"{p_var}-{region_fixed}{number_fixed}" if region_fixed else f"{p_var}-{number_fixed}"
                        # Use a moderate index for fallback (e.g., 5) to avoid huge penalties but not boost as "early"
                        v_code, v_score = validate_and_score(code_cand, region_fixed, 0.4, 5)
                        candidates.append((v_code, v_score, region_fixed))

        if not candidates:
            return None, 0.0, "EN"

        candidates.sort(key=lambda x: x[1], reverse=True)
        best_id, best_score, best_region = candidates[0]

        lang = "EN"
        if best_region in ['EN', 'DE', 'FR', 'IT', 'ES', 'PT', 'JP']:
            lang = best_region

        return best_id, best_score, lang

    def detect_first_edition(self, text_sources: List[str]) -> bool:
        """
        Simplified 1st Edition check based on keywords in provided text sources.
        Checks for: Edition, Auflage, Edizione, Edición, Edição
        """
        keywords = ["EDITION", "AUFLAGE", "EDIZIONE", "EDICIÓN", "EDIÇÃO", "EDICAO"]

        for text in text_sources:
            if not text:
                continue

            upper_text = text.upper()
            for kw in keywords:
                if kw in upper_text:
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
        """
        Visual analysis for rarity based on reflectivity (brightness variance)
        and specific color tones (Gold/Silver).
        """
        x, y, w, h = self.roi_name
        roi_name = warped[y:y+h, x:x+w]
        hsv = cv2.cvtColor(roi_name, cv2.COLOR_BGR2HSV)

        # Tuned Color Thresholds
        lower_gold = np.array([10, 80, 80])
        upper_gold = np.array([40, 255, 255])
        mask_gold = cv2.inRange(hsv, lower_gold, upper_gold)

        lower_silver = np.array([0, 0, 180]) # Bright whites/silvers
        upper_silver = np.array([180, 30, 255])
        mask_silver = cv2.inRange(hsv, lower_silver, upper_silver)

        gold_pixels = cv2.countNonZero(mask_gold)
        silver_pixels = cv2.countNonZero(mask_silver)
        total_pixels = w * h

        # Reflectivity (Variance) Analysis
        gray = cv2.cvtColor(roi_name, cv2.COLOR_BGR2GRAY)
        mean, std_dev = cv2.meanStdDev(gray)
        std_val = std_dev[0][0]

        rarity = "Common"
        confidence = 0.0 # 0.0 to 1.0

        # Thresholds
        if gold_pixels > total_pixels * 0.10:
            rarity = "Gold/Ultra Rare"
            confidence = 0.85
        elif silver_pixels > total_pixels * 0.10:
             # Distinguish Silver Foil vs White Ink (Common Spell/Trap titles)
             if std_val > 50: # High variance suggests foil noise
                 rarity = "Secret Rare"
                 confidence = 0.65
             else:
                 rarity = "Common" # Likely White Ink
                 confidence = 0.5
        else:
            rarity = "Common"
            confidence = 0.9

        # Return formatted string with confidence
        return f"{rarity} ({int(confidence*100)}%)"

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
        draw_roi(self.roi_art, (255, 255, 255)) # Changed color for visibility

        return canvas
