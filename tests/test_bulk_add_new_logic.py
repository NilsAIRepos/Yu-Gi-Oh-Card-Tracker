import sys
from unittest.mock import MagicMock, AsyncMock, patch
import unittest

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

from src.ui.bulk_add import BulkAddPage

class TestBulkAddFilterLogic(unittest.TestCase):
    def setUp(self):
        # Setup mock returns
        self.persistence_mock = sys.modules['src.core.persistence'].persistence
        self.persistence_mock.list_collections.return_value = []
        self.persistence_mock.load_ui_state.return_value = {}

        self.config_mock = sys.modules['src.core.config'].config_manager
        self.config_mock.get_language.return_value = 'EN'
        self.config_mock.get_bulk_add_page_size.return_value = 50

        with patch('src.ui.bulk_add.SingleCardView'), \
             patch('src.ui.bulk_add.StructureDeckDialog'), \
             patch('src.ui.bulk_add.FilterPane'):
            self.page = BulkAddPage()

    def test_library_not_filtered_default(self):
        # Default state should be "Not Filtered"
        # Defaults: search='', set='', filter_card_type=['Monster', 'Spell', 'Trap']

        self.assertEqual(self.page.state['library_search_text'], '')
        self.assertEqual(self.page.state['filter_set'], '')
        self.assertEqual(self.page.state['filter_card_type'], ['Monster', 'Spell', 'Trap'])

        self.assertFalse(self.page.is_library_filtered())

    def test_library_filtered_by_text(self):
        self.page.state['library_search_text'] = 'Blue-Eyes'
        self.assertTrue(self.page.is_library_filtered())

    def test_library_filtered_by_set(self):
        self.page.state['filter_set'] = 'LOB'
        self.assertTrue(self.page.is_library_filtered())

    def test_library_filtered_by_type_narrow(self):
        self.page.state['filter_card_type'] = ['Monster']
        self.assertTrue(self.page.is_library_filtered())

    def test_library_filtered_by_type_wide(self):
        # Even if "wider" than default (e.g. including Skill), it counts as "Changed"
        self.page.state['filter_card_type'] = ['Monster', 'Spell', 'Trap', 'Skill']
        self.assertTrue(self.page.is_library_filtered())

    def test_collection_not_filtered_default(self):
        self.assertEqual(self.page.col_state['search_text'], '')
        self.assertFalse(self.page.is_collection_filtered())

    def test_collection_filtered(self):
        self.page.col_state['search_text'] = 'Dark Magician'
        self.assertTrue(self.page.is_collection_filtered())

    def test_collection_filtered_by_set(self):
        self.page.col_state['filter_set'] = 'SDK'
        self.assertTrue(self.page.is_collection_filtered())

if __name__ == '__main__':
    unittest.main()
