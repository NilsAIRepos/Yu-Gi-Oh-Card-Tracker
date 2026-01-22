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

# Helper mocks for DocTR structures
class MockWord:
    def __init__(self, value):
        self.value = value
        self.confidence = 0.9

class MockLine:
    def __init__(self, text):
        self.words = [MockWord(w) for w in text.split()]

class MockBlock:
    def __init__(self, text, geometry=((0,0),(1,1))):
        self.lines = [MockLine(text)]
        self.geometry = geometry

class MockPage:
    def __init__(self, blocks):
        self.blocks = blocks

class MockDocTRResult:
    def __init__(self, blocks):
        self.pages = [MockPage(blocks)]

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
        self.scanner.valid_card_names = {
            'blue-eyes white dragon', 'dark magician', 'pot of greed'
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

    def test_card_name_crop_db_match(self):
        # DocTR result matching DB, regardless of position
        block = MockBlock("Blue-Eyes White Dragon", geometry=((0, 0.05), (1, 0.10)))
        res = MockDocTRResult([block])

        name = self.scanner._parse_card_name(res, 'doctr', scope='crop')
        self.assertEqual(name, "Blue-Eyes White Dragon")

        # Should find it even if mixed with other text
        block_noise = MockBlock("Effect Monster", geometry=((0, 0.8), (1, 0.9)))
        res_mixed = MockDocTRResult([block, block_noise])
        name = self.scanner._parse_card_name(res_mixed, 'doctr', scope='crop')
        self.assertEqual(name, "Blue-Eyes White Dragon")

    def test_card_name_full_match(self):
        # DocTR result anywhere in frame, matching DB
        block = MockBlock("Dark Magician", geometry=((0.4, 0.4), (0.6, 0.6)))
        res = MockDocTRResult([block])

        name = self.scanner._parse_card_name(res, 'doctr', scope='full')
        self.assertEqual(name, "Dark Magician")

    def test_card_name_full_no_match(self):
        # DocTR result anywhere, NOT matching DB
        block = MockBlock("Some Random Text", geometry=((0.4, 0.4), (0.6, 0.6)))
        res = MockDocTRResult([block])

        name = self.scanner._parse_card_name(res, 'doctr', scope='full')
        self.assertIsNone(name)

if __name__ == '__main__':
    unittest.main()
