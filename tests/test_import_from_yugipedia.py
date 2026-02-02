import unittest
from unittest.mock import MagicMock, AsyncMock, patch
from src.services.ygo_api import YugiohService
from src.core.models import ApiCard, ApiCardSet

class TestImportFromYugipedia(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.service = YugiohService()
        self.service.load_card_database = AsyncMock(return_value=[])
        self.service.save_card_database = AsyncMock()
        self.service.get_set_name_by_code = AsyncMock(return_value="Test Set Name")

    async def test_import_new_card(self):
        card_data = {
            "name": "New Card",
            "type": "Normal Monster",
            "desc": "Description",
            "atk": 1000,
            "def": 1000,
            "level": 4,
            "attribute": "DARK",
            "race": "Warrior",
            "database_id": 123
        }
        selected_sets = [
            {"set_code": "TEST-EN001", "set_rarity": "Common"}
        ]

        success, msg = await self.service.import_from_yugipedia(card_data, selected_sets)

        self.assertTrue(success)
        self.service.save_card_database.assert_called_once()
        saved_cards = self.service.save_card_database.call_args[0][0]
        self.assertEqual(len(saved_cards), 1)
        card = saved_cards[0]
        self.assertEqual(card.name, "New Card")
        self.assertEqual(card.id, 123)
        self.assertEqual(len(card.card_sets), 1)
        self.assertEqual(card.card_sets[0].set_code, "TEST-EN001")

    async def test_import_existing_card_update(self):
        existing_card = ApiCard(
            id=123,
            name="Existing Card",
            type="Normal Monster",
            frameType="normal",
            desc="", # Empty desc
            race="Warrior",
            card_sets=[]
        )
        self.service.load_card_database = AsyncMock(return_value=[existing_card])

        card_data = {
            "name": "Existing Card",
            "desc": "New Description", # Update
            "database_id": 123
        }
        selected_sets = [
            {"set_code": "TEST-EN002", "set_rarity": "Rare"}
        ]

        success, msg = await self.service.import_from_yugipedia(card_data, selected_sets)

        self.assertTrue(success)
        self.assertEqual(existing_card.desc, "New Description")
        self.assertEqual(len(existing_card.card_sets), 1)
        self.assertEqual(existing_card.card_sets[0].set_code, "TEST-EN002")

if __name__ == '__main__':
    unittest.main()
