import unittest
import sys
import os
from unittest.mock import MagicMock

# Ensure src is in path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Mocking modules that might be missing or heavy
sys.modules['cv2'] = MagicMock()
sys.modules['numpy'] = MagicMock()
sys.modules['torch'] = MagicMock()
sys.modules['ultralytics'] = MagicMock()
sys.modules['langdetect'] = MagicMock()
sys.modules['easyocr'] = MagicMock()
sys.modules['keras_ocr'] = MagicMock()
sys.modules['doctr'] = MagicMock()
sys.modules['doctr.io'] = MagicMock()
sys.modules['doctr.models'] = MagicMock()
sys.modules['mmocr'] = MagicMock()
sys.modules['mmocr.apis'] = MagicMock()

# Now import the class to test
from src.services.scanner.pipeline import CardScanner

class TestOCRLogic(unittest.TestCase):
    def setUp(self):
        # Suppress logging during tests
        import logging
        logging.getLogger('src.services.scanner.pipeline').setLevel(logging.CRITICAL)

        self.scanner = CardScanner()
        # Mock validation data
        self.scanner.valid_set_codes = {
            'LOB-EN001', 'SDK-001', 'TAMA-EN056', 'MP19-EN001', 'LOB-E001', 'ABC-EN007'
        }

    def test_standard_set_id(self):
        texts = ["Some text", "LOB-EN001", "Other text"]
        confs = [0.9, 0.95, 0.9]

        set_id, score, lang = self.scanner._parse_set_id(texts, confs)
        self.assertEqual(set_id, "LOB-EN001")
        self.assertEqual(lang, "EN")

    def test_typo_s_to_5_in_number(self):
        texts = ["TAMA-EN0S6"]
        confs = [0.8]
        set_id, score, lang = self.scanner._parse_set_id(texts, confs)
        self.assertEqual(set_id, "TAMA-EN056")

    def test_typo_o_to_0_in_number(self):
        texts = ["SDK-OO1"] # SDK-001 (Number O01 -> 001)
        confs = [0.8]
        set_id, score, lang = self.scanner._parse_set_id(texts, confs)
        self.assertEqual(set_id, "SDK-001")

    def test_full_text_fallback(self):
        texts = ["Garbage", "Noise"]
        confs = [0.1, 0.1]
        full_text = "Some random text | LOB-EN001 | more text"

        set_id, score, lang = self.scanner._parse_set_id(texts, confs, full_text=full_text)
        self.assertEqual(set_id, "LOB-EN001")

    def test_typo_z_to_7(self):
        texts = ["ABC-EN00Z"]
        confs = [0.9]
        set_id, score, lang = self.scanner._parse_set_id(texts, confs)
        self.assertEqual(set_id, "ABC-EN007")

    def test_space_handling(self):
        # OCR might output spaced text
        texts = ["L O B - E N 0 0 1"]
        confs = [0.9]
        set_id, score, lang = self.scanner._parse_set_id(texts, confs)
        self.assertEqual(set_id, "LOB-EN001")

if __name__ == '__main__':
    unittest.main()
