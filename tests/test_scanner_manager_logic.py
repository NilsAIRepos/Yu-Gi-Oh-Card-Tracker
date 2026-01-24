import unittest
from unittest.mock import MagicMock, patch, AsyncMock
import sys
import os

sys.path.append(os.getcwd())

# Mock dependencies before import
sys.path.append(os.getcwd())
sys.modules['langdetect'] = MagicMock()
sys.modules['easyocr'] = MagicMock()
sys.modules['keras_ocr'] = MagicMock()
sys.modules['doctr'] = MagicMock()
sys.modules['doctr.io'] = MagicMock()
sys.modules['doctr.models'] = MagicMock()
sys.modules['mmocr'] = MagicMock()
sys.modules['mmocr.apis'] = MagicMock()

from src.services.scanner.manager import ScannerManager
from src.services.scanner.models import OCRResult

class TestScannerManagerLogic(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.manager = ScannerManager()
        self.manager.scanner = MagicMock()
        # Mock _normalize_card_name helper
        self.manager.scanner._normalize_card_name = lambda s: s.lower() if s else ""

    @patch('src.services.scanner.manager.ygo_service')
    async def test_find_best_match_art_threshold(self, mock_ygo):
        # Setup Mock DB
        card_mock = MagicMock()
        card_mock.id = 1
        card_mock.name = "Test Card"
        card_mock.type = "Monster"

        variant = MagicMock()
        variant.set_code = "TEST-EN001"
        variant.set_rarity = "Common"
        variant.image_id = 12345

        card_mock.card_sets = [variant]
        card_mock.card_images = [MagicMock(id=12345)]

        mock_ygo.load_card_database = AsyncMock(return_value=[card_mock])

        ocr_res = OCRResult(
            engine="test",
            raw_text="Test Card",
            card_name="Test Card",
            set_id="TEST-EN001",
            set_id_conf=90.0
        )

        # Case 1: High Score > Threshold (Should include Art Bonus)
        art_match = {"filename": "12345.jpg", "score": 0.8}
        res_high = await self.manager.find_best_match(ocr_res, art_match, threshold=10, art_threshold=0.5)
        # Verify score includes Art Bonus (40 pts)
        # Base: 80 (Set Code) + 9 (Conf) + 50 (Name) = 139
        # Art: +40 -> 179
        # Exact calculation depends on logic, but it should be significantly higher
        score_high = res_high['candidates'][0]['score']

        # Case 2: Low Score < Threshold (Should Exclude Art Bonus)
        art_match_low = {"filename": "12345.jpg", "score": 0.3}
        res_low = await self.manager.find_best_match(ocr_res, art_match_low, threshold=10, art_threshold=0.5)
        score_low = res_low['candidates'][0]['score']

        self.assertTrue(score_high > score_low, f"High score {score_high} should be > Low score {score_low}")
        # The difference should be exactly 40 (Art Match bonus)
        self.assertAlmostEqual(score_high - score_low, 40.0, delta=1.0)

    @patch('src.services.scanner.manager.ygo_service')
    async def test_find_best_match_spell_trap_bonus(self, mock_ygo):
        # Setup Mock DB
        card_spell = MagicMock()
        card_spell.id = 1
        card_spell.name = "Test Spell"
        card_spell.type = "Spell Card" # DB format

        variant = MagicMock()
        variant.set_code = "SPELL-001"
        variant.set_rarity = "Common"
        variant.image_id = 111
        card_spell.card_sets = [variant]
        card_spell.card_images = []

        card_trap = MagicMock()
        card_trap.id = 2
        card_trap.name = "Test Trap"
        card_trap.type = "Trap Card"
        variant2 = MagicMock()
        variant2.set_code = "TRAP-001"
        card_trap.card_sets = [variant2]

        mock_ygo.load_card_database = AsyncMock(return_value=[card_spell, card_trap])

        # 1. Test Spell Match
        ocr_res_spell = OCRResult(
            engine="test", raw_text="Spell", card_name="Test Spell",
            set_id="SPELL-001", set_id_conf=90.0,
            card_type="SPELL CARD" # Detected type
        )

        res = await self.manager.find_best_match(ocr_res_spell, None)
        score = res['candidates'][0]['score']

        # Verify bonus
        # 80 (Set) + 9 (Conf) + 50 (Name) + 10 (Type Bonus) = 149
        # Without bonus = 139
        # We can check against a baseline without type

        ocr_res_no_type = OCRResult(
            engine="test", raw_text="Spell", card_name="Test Spell",
            set_id="SPELL-001", set_id_conf=90.0,
            card_type=None
        )
        res_base = await self.manager.find_best_match(ocr_res_no_type, None)
        score_base = res_base['candidates'][0]['score']

        self.assertEqual(score - score_base, 10.0)

    @patch('src.services.scanner.manager.ygo_service')
    async def test_find_best_match_mismatch_type(self, mock_ygo):
         # If OCR says SPELL but card is TRAP, no bonus
        card_spell = MagicMock()
        card_spell.id = 1
        card_spell.name = "Test Spell"
        card_spell.type = "Spell Card"
        variant = MagicMock()
        variant.set_code = "SPELL-001"
        card_spell.card_sets = [variant]
        card_spell.card_images = []

        mock_ygo.load_card_database = AsyncMock(return_value=[card_spell])

        ocr_res = OCRResult(
            engine="test", raw_text="Spell", card_name="Test Spell",
            set_id="SPELL-001", set_id_conf=90.0,
            card_type="TRAP CARD" # Mismatch
        )

        res = await self.manager.find_best_match(ocr_res, None)
        score = res['candidates'][0]['score']

        ocr_res_base = OCRResult(
            engine="test", raw_text="Spell", card_name="Test Spell",
            set_id="SPELL-001", set_id_conf=90.0,
            card_type=None
        )
        res_base = await self.manager.find_best_match(ocr_res_base, None)
        score_base = res_base['candidates'][0]['score']

        self.assertEqual(score, score_base)


if __name__ == '__main__':
    unittest.main()
