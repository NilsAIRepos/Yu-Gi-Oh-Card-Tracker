import sys
from unittest.mock import MagicMock, AsyncMock, patch
import unittest
import asyncio

# Mock nicegui modules before importing src.ui.bulk_add
sys.modules['nicegui'] = MagicMock()
sys.modules['nicegui.ui'] = MagicMock()
sys.modules['nicegui.run'] = MagicMock()

# Mock dependencies
sys.modules['src.core.persistence'] = MagicMock()
sys.modules['src.core.changelog_manager'] = MagicMock()
sys.modules['src.core.config'] = MagicMock()
sys.modules['src.services.ygo_api'] = MagicMock()
sys.modules['src.services.image_manager'] = MagicMock()
sys.modules['src.services.collection_editor'] = MagicMock()

from src.ui.bulk_add import BulkAddPage, BulkCollectionEntry

class TestBulkAddRemove(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Setup mock returns
        self.persistence_mock = sys.modules['src.core.persistence'].persistence
        self.persistence_mock.list_collections.return_value = ['TestCol']
        self.persistence_mock.load_ui_state.return_value = {}

        self.collection_editor_mock = sys.modules['src.services.collection_editor'].CollectionEditor
        self.collection_editor_mock.reset_mock()
        self.changelog_manager_mock = sys.modules['src.core.changelog_manager'].changelog_manager

        # Mock run.io_bound to execute immediately
        self.run_patcher = patch('src.ui.bulk_add.run')
        self.run_mock = self.run_patcher.start()
        self.run_mock.io_bound = AsyncMock()
        self.addCleanup(self.run_patcher.stop)

        self.config_mock = sys.modules['src.core.config'].config_manager
        self.config_mock.get_language.return_value = 'EN'
        self.config_mock.get_bulk_add_page_size.return_value = 50

        with patch('src.ui.bulk_add.SingleCardView'), \
             patch('src.ui.bulk_add.StructureDeckDialog'), \
             patch('src.ui.bulk_add.FilterPane'):
            self.page = BulkAddPage()
            self.page.current_collection_obj = MagicMock()
            self.page.state['selected_collection'] = 'TestCol'

    async def test_remove_with_storage_location(self):
        # Input Entry
        api_card = MagicMock()
        api_card.id = 123
        api_card.name = "Test Card"

        entry = BulkCollectionEntry(
            id="test_id",
            api_card=api_card,
            quantity=3,
            set_code="LOB-EN001",
            set_name="LOB",
            rarity="Ultra Rare",
            language="EN",
            condition="Near Mint",
            first_edition=False,
            image_url="url",
            image_id=123,
            variant_id="var_123",
            price=10.0,
            storage_location="Binder 1"  # Crucial
        )

        # Act
        await self.page.process_batch_remove([entry])

        # Assert
        self.assertEqual(self.collection_editor_mock.apply_change.call_count, 1)

        call1 = self.collection_editor_mock.apply_change.call_args_list[0]
        self.assertEqual(call1.kwargs['quantity'], -3)
        self.assertEqual(call1.kwargs['storage_location'], "Binder 1") # Verify fix

        # Check Changelog
        self.changelog_manager_mock.log_batch_change.assert_called_once()
        changes = self.changelog_manager_mock.log_batch_change.call_args[0][2]
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]['card_data']['storage_location'], "Binder 1")

    async def test_undo_crash_prevention(self):
        # Setup
        self.page.state['selected_collection'] = 'TestCol'
        self.changelog_manager_mock.undo_last_change.return_value = {
            'action': 'ADD', 'quantity': 1, 'card_data': {'card_id': 1, 'name': 'Test', 'set_code': 'S', 'rarity': 'R', 'image_id': 1, 'language': 'EN', 'condition': 'NM', 'first_edition': False, 'storage_location': 'Box'}
        }
        self.page.api_card_map = {1: MagicMock()}

        # Mock _update_collection to return True
        self.page._update_collection = AsyncMock(return_value=True)

        # Patch ui.notify to raise RuntimeError
        with patch('src.ui.bulk_add.ui.notify', side_effect=RuntimeError("The parent element this slot belongs to has been deleted.")):
            # This should NOT crash
            await self.page.undo_last_action()

        # Also check re-entrancy
        self.assertFalse(self.page.undoing)

if __name__ == '__main__':
    unittest.main()
