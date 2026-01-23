import logging

logger = logging.getLogger(__name__)

SCANNER_AVAILABLE = False

try:
    import cv2
    import langdetect
    import numpy as np
    import easyocr
    from doctr.io import DocumentFile
    import ultralytics
    SCANNER_AVAILABLE = True
except ImportError as e:
    logger.warning(f"Scanner dependencies missing: {e}. Scanner module will be disabled.")
    SCANNER_AVAILABLE = False
