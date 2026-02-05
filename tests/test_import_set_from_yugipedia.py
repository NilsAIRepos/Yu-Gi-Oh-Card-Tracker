import unittest
from unittest.mock import MagicMock, AsyncMock, patch
from src.services.ygo_api import YugiohService
from src.core.models import ApiCard, ApiCardSet, ApiCardImage

class TestImportSetFromYugipedia(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.service = YugiohService()
        self.service.save_card_database = AsyncMock()
        # Mock image manager
        self.patcher = patch('src.services.ygo_api.image_manager')
        self.mock_image_manager = self.patcher.start()
        self.mock_image_manager.ensure_set_image = AsyncMock()

    def tearDown(self):
        self.patcher.stop()

    async def test_import_set_success(self):
        # Setup existing cards
        existing_card = ApiCard(
            id=1,
            name="Test Card",
            type="Normal Monster",
            frameType="normal",
            desc="Desc",
            race="Warrior",
            card_sets=[],
            card_images=[ApiCardImage(id=1, image_url="url", image_url_small="url")]
        )
        self.service.load_card_database = AsyncMock(return_value=[existing_card])

        set_data = {
            "name": "New Set",
            "code": "NEW",
            "image_url": "http://image.url",
            "cards": [
                {
                    "set_code": "NEW-EN001",
                    "name": "Test Card",
                    "set_rarity": "Common"
                },
                {
                    "set_code": "NEW-EN002",
                    "name": "Missing Card",
                    "set_rarity": "Rare"
                }
            ]
        }

        success, msg = await self.service.import_set_from_yugipedia(set_data)

        self.assertTrue(success)
        self.assertIn("Imported 1 variants", msg)
        self.assertIn("1 cards not found", msg)

        # Verify card updated
        self.assertEqual(len(existing_card.card_sets), 1)
        self.assertEqual(existing_card.card_sets[0].set_code, "NEW-EN001")
        self.assertEqual(existing_card.card_sets[0].set_rarity, "Common")
        self.assertEqual(existing_card.card_sets[0].image_id, 1)

        # Verify set image updated
        self.mock_image_manager.ensure_set_image.assert_called_with("NEW", "http://image.url")
        self.service.save_card_database.assert_called_once()

    async def test_import_set_duplicate(self):
        # Setup existing card with variant
        existing_card = ApiCard(
            id=1,
            name="Test Card",
            type="Normal Monster",
            frameType="normal",
            desc="Desc",
            race="Warrior",
            card_sets=[
                ApiCardSet(variant_id="v1", set_name="New Set", set_code="NEW-EN001", set_rarity="Common", set_price="0")
            ],
            card_images=[ApiCardImage(id=1, image_url="url", image_url_small="url")]
        )
        self.service.load_card_database = AsyncMock(return_value=[existing_card])

        set_data = {
            "name": "New Set",
            "cards": [
                {
                    "set_code": "NEW-EN001",
                    "name": "Test Card",
                    "set_rarity": "Common"
                }
            ]
        }

        success, msg = await self.service.import_set_from_yugipedia(set_data)

        # Should report no changes needed if only duplicates found
        self.assertTrue(success)
        self.assertIn("No changes needed", msg)
        self.assertEqual(len(existing_card.card_sets), 1) # Still 1

if __name__ == '__main__':
    unittest.main()
