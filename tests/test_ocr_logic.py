import unittest
import sys
import os
from unittest.mock import MagicMock, patch

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

# Mock Helper Classes for DocTR
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
        # Suppress logging
        import logging
        logging.getLogger('src.services.scanner.pipeline').setLevel(logging.CRITICAL)

        self.scanner = CardScanner()
        # Mock validation data manually since we want to control it
        self.scanner.valid_set_codes = {
            'LOB-EN001', 'SDK-001', 'TAMA-EN056', 'MP19-EN001', 'LOB-E001', 'ABC-EN007',
            'LOB-G001', # Mocking a legacy generated code
            'RA02-DE052' # German code
        }
        self.scanner.valid_card_names_norm = {
            'blueeyeswhitedragon': 'Blue-Eyes White Dragon',
            'darkmagician': 'Dark Magician',
            'potofgreed': 'Pot of Greed',
            'schwarzermagier': 'Schwarzer Magier',
            'ruckkehr': 'Rückkehr', # Umlaut normalized
            'kashtiraoger': 'Kashtira Oger',
            'kanguruchampion': 'Känguru-Champion', # DB entry (ä -> a)
            'giftzahne': 'Giftzähne' # DB entry (ä -> a)
        }

    def test_all_number_prefix_penalty(self):
        # "8552-0851" (Pure number) vs "LOB-EN001" (Valid)
        texts = ["8552-0851", "LOB-EN001"]
        confs = [0.9, 0.9] # Same confidence

        set_id, score, lang = self.scanner._parse_set_id(texts, confs)
        self.assertEqual(set_id, "LOB-EN001")
        # 8552-0851 should be penalized

    def test_position_weighting(self):
        # "LOB-EN001" at index 0 vs "ABC-EN007" at index 10
        # Both valid. Index 0 should win if confidences are equal.
        texts = ["LOB-EN001", "Text", "Text", "Text", "Text", "Text", "Text", "Text", "Text", "Text", "ABC-EN007"]
        confs = [0.9] * 11

        set_id, score, lang = self.scanner._parse_set_id(texts, confs)
        self.assertEqual(set_id, "LOB-EN001")

    def test_localized_code_generation(self):
        # Verify that _generate_localized_codes logic works
        # If we pass LOB-EN001, we expect LOB-DE001, LOB-FR001 etc.
        # This tests the method directly

        # Reset valid sets to empty to verify generation
        self.scanner.valid_set_codes = set()
        supported = ['EN', 'DE']

        self.scanner._generate_localized_codes("LOB-EN001", supported)
        self.assertIn("LOB-DE001", self.scanner.valid_set_codes)

        # Legacy
        self.scanner.valid_set_codes = set()
        self.scanner._generate_localized_codes("LOB-E001", supported) # E -> EN -> G -> DE
        # The map logic: E is EN legacy. G is DE legacy.
        self.assertIn("LOB-G001", self.scanner.valid_set_codes)

    def test_db_name_match_german(self):
        block = MockBlock("Schwarzer Magier")
        res = MockDocTRResult([block])
        name = self.scanner._parse_card_name(res, 'doctr', scope='full')
        self.assertEqual(name, "Schwarzer Magier")

    def test_name_match_missing_space(self):
        # "Blue-EyesWhite Dragon" (missing space)
        block = MockBlock("Blue-EyesWhite Dragon")
        res = MockDocTRResult([block])
        name = self.scanner._parse_card_name(res, 'doctr')
        self.assertEqual(name, "Blue-Eyes White Dragon")

    def test_name_match_umlaut_normalization(self):
        # "Ruckkehr" (OCR missing umlaut) should match "Rückkehr" (DB)
        block = MockBlock("Ruckkehr")
        res = MockDocTRResult([block])
        name = self.scanner._parse_card_name(res, 'doctr')
        self.assertEqual(name, "Rückkehr")

    def test_name_match_pipe_separator(self):
        # "KASHTIRA | OGER" should match "Kashtira Oger"
        block = MockBlock("KASHTIRA | OGER")
        res = MockDocTRResult([block])
        name = self.scanner._parse_card_name(res, 'doctr')
        self.assertEqual(name, "Kashtira Oger")

    def test_name_match_hyphen_handling(self):
        # "KANGARU-CHAMPION" should match "Kangaru Champion" if DB has it
        # Actually in our mock we updated it to Känguru-Champion (kanguruchampion)
        # IF OCR is "KANGURU-CHAMPION" (correct OCR of Känguru) -> kanguruchampion -> Match

        # Test 1: Correct OCR of Umlaut
        block = MockBlock("Känguru-Champion")
        res = MockDocTRResult([block])
        name = self.scanner._parse_card_name(res, 'doctr')
        self.assertEqual(name, "Känguru-Champion")

    def test_name_match_accent_handling(self):
        # Test fix for "GIFTZÂHNE" -> "Giftzähne"
        # OCR reads "GIFTZÂHNE" (Â instead of ä/A).
        # Normalization should map Â -> A.
        # DB 'Giftzähne' -> 'giftzahne'.
        # OCR 'GIFTZÂHNE' -> 'giftzahne'. Match!

        block = MockBlock("GIFTZÂHNE")
        res = MockDocTRResult([block])
        name = self.scanner._parse_card_name(res, 'doctr')
        self.assertEqual(name, "Giftzähne")

    def test_name_match_mixed_accents(self):
        # Test "KÀNGURU" -> "Känguru"
        block = MockBlock("KÀNGURU-CHAMPION")
        res = MockDocTRResult([block])
        name = self.scanner._parse_card_name(res, 'doctr')
        self.assertEqual(name, "Känguru-Champion")

if __name__ == '__main__':
    unittest.main()
