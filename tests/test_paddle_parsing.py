import unittest
from unittest.mock import MagicMock
import sys
from dataclasses import dataclass

# Define dummy model
@dataclass
class OCRResult:
    engine: str
    raw_text: str
    set_id: str = None
    set_id_conf: float = 0.0
    language: str = "EN"

# Mock dependencies
sys.modules['cv2'] = MagicMock()
sys.modules['numpy'] = MagicMock()
sys.modules['langdetect'] = MagicMock()
sys.modules['easyocr'] = MagicMock()
sys.modules['torch'] = MagicMock()
sys.modules['paddleocr'] = MagicMock()
sys.modules['ultralytics'] = MagicMock()

# Mock models module but provide our real dummy class for usage
mock_models = MagicMock()
mock_models.OCRResult = OCRResult
sys.modules['src.services.scanner.models'] = mock_models

from src.services.scanner.pipeline import CardScanner

class TestPaddleParsing(unittest.TestCase):
    def test_paddle_malformed_result(self):
        """
        Verify that ocr_scan handles malformed PaddleOCR results without crashing.
        Case: line[1] has only text, no confidence.
        """
        # Setup
        scanner = CardScanner()
        mock_ocr_instance = MagicMock()

        # Simulate a result where one line is malformed: ("TextOnly",) instead of ("Text", Conf)
        malformed_line = [ [[0,0]], ("BadLine",) ]
        good_line = [ [[0,0]], ("GoodLine", 0.99) ]

        mock_ocr_instance.ocr.return_value = [ [good_line, malformed_line] ]

        # Inject our mock into the scanner
        scanner.paddle_ocr = mock_ocr_instance

        mock_image = MagicMock()
        mock_image.shape = (100, 100, 3)

        # Execute
        result = scanner.ocr_scan(mock_image, engine='paddle')

        # Assert
        self.assertIn("GoodLine", result.raw_text)
        self.assertIn("BadLine", result.raw_text)

    def test_paddle_very_malformed_result(self):
        """
        Verify skipping of completely broken lines.
        """
        scanner = CardScanner()
        mock_ocr_instance = MagicMock()

        # Line too short: [ [coords] ] (missing text element)
        bad_structure = [ [[0,0]] ]

        mock_ocr_instance.ocr.return_value = [ [bad_structure] ]
        scanner.paddle_ocr = mock_ocr_instance
        mock_image = MagicMock()
        mock_image.shape = (100, 100, 3)

        result = scanner.ocr_scan(mock_image, engine='paddle')

        # Should just be empty string, no crash
        self.assertEqual(result.raw_text, "")

if __name__ == '__main__':
    unittest.main()
