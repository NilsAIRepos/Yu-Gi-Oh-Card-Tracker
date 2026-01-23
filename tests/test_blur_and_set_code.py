import unittest
import sys
import os
from unittest.mock import MagicMock, patch

# Ensure src is in path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Mocking dependencies
sys.modules['cv2'] = MagicMock()
sys.modules['numpy'] = MagicMock()
sys.modules['torch'] = MagicMock()
sys.modules['ultralytics'] = MagicMock()
sys.modules['langdetect'] = MagicMock()
sys.modules['easyocr'] = MagicMock()
sys.modules['doctr'] = MagicMock()

# Import code under test
from src.services.scanner.pipeline import CardScanner
from src.core.utils import normalize_set_code

class TestBlurAndSetCode(unittest.TestCase):
    def setUp(self):
        self.scanner = CardScanner()

    def test_detect_blur(self):
        # Mock cv2.cvtColor to return something
        sys.modules['cv2'].cvtColor.return_value = "gray_image"

        # Mock cv2.Laplacian(gray, cv2.CV_64F).var()
        mock_laplacian = MagicMock()
        sys.modules['cv2'].Laplacian.return_value = mock_laplacian

        # Case 1: Not Blurry (High Variance)
        mock_laplacian.var.return_value = 500.0
        is_blurred, score = self.scanner.detect_blur("image", threshold=100.0)
        self.assertFalse(is_blurred)
        self.assertEqual(score, 500.0)

        # Case 2: Blurry (Low Variance)
        mock_laplacian.var.return_value = 50.0
        is_blurred, score = self.scanner.detect_blur("image", threshold=100.0)
        self.assertTrue(is_blurred)
        self.assertEqual(score, 50.0)

    def test_normalize_set_code(self):
        # Case 1: Standard 2-letter
        self.assertEqual(normalize_set_code("LOB-EN001"), "LOB-001")
        # Case 2: Legacy 1-letter
        self.assertEqual(normalize_set_code("LOB-E001"), "LOB-001")
        # Case 3: Already normalized
        self.assertEqual(normalize_set_code("LOB-001"), "LOB-001")
        # Case 4: No Match
        self.assertEqual(normalize_set_code("RandomString"), "RandomString")
        # Case 5: 3-letter region (Asian English sometimes?)
        self.assertEqual(normalize_set_code("LOB-AE001"), "LOB-001")
        # Case 6: Different set code length
        self.assertEqual(normalize_set_code("SDY-006"), "SDY-006")
        self.assertEqual(normalize_set_code("RA02-DE004"), "RA02-004")

    def test_set_code_compatibility_logic(self):
        # This simulates the logic added to manager.py

        ocr_code = "RA02-DE004" # OCR found German
        db_code = "RA02-EN004" # DB has English

        # Strict Match Fails
        self.assertNotEqual(ocr_code, db_code)

        # Normalized Match Succeeds
        self.assertEqual(normalize_set_code(ocr_code), normalize_set_code(db_code))
        self.assertEqual(normalize_set_code(ocr_code), "RA02-004")

if __name__ == '__main__':
    unittest.main()
