import asyncio
import unittest
from unittest.mock import MagicMock, patch
import sys
import os

# Mock nicegui
mock_ui = MagicMock()
sys.modules['nicegui'] = mock_ui
sys.modules['nicegui.ui'] = mock_ui

from src.ui.collection import CollectionPage, CardViewModel
from src.core.models import ApiCard
from src.ui.components.filter_pane import FilterPane

class TestFilters(unittest.TestCase):
    def setUp(self):
        # Patch persistence and config
        self.persistence_patcher = patch('src.ui.collection.persistence')
        self.persistence_mock = self.persistence_patcher.start()
        self.persistence_mock.list_collections.return_value = []

        self.config_patcher = patch('src.ui.collection.config_manager')
        self.config_mock = self.config_patcher.start()
        self.config_mock.get_language.return_value = 'en'

        self.page = CollectionPage()

        # Mock content_area refresh
        self.page.content_area = MagicMock()
        self.page.prepare_current_page_images = MagicMock()
        # Make prepare_current_page_images async no-op
        async def async_noop(): pass
        self.page.prepare_current_page_images.side_effect = async_noop

    def tearDown(self):
        self.persistence_patcher.stop()
        self.config_patcher.stop()

    def test_filter_card_type_multi(self):
        # Setup data
        c1 = ApiCard(id=1, name="M1", type="Normal Monster", frameType="normal", desc="desc")
        c2 = ApiCard(id=2, name="S1", type="Spell Card", frameType="spell", desc="desc")
        c3 = ApiCard(id=3, name="T1", type="Trap Card", frameType="trap", desc="desc")
        c4 = ApiCard(id=4, name="SK1", type="Skill Card", frameType="skill", desc="desc")

        vm1 = CardViewModel(c1, 0, False)
        vm2 = CardViewModel(c2, 0, False)
        vm3 = CardViewModel(c3, 0, False)
        vm4 = CardViewModel(c4, 0, False)

        self.page.state['cards_consolidated'] = [vm1, vm2, vm3, vm4]

        # Test Default (Monster, Spell, Trap)
        self.page.state['filter_card_type'] = ['Monster', 'Spell', 'Trap']
        asyncio.run(self.page.apply_filters())
        res = self.page.state['filtered_items']
        self.assertEqual(len(res), 3)
        self.assertTrue(all(c.api_card.id in [1, 2, 3] for c in res))

        # Test Skill Only
        self.page.state['filter_card_type'] = ['Skill']
        asyncio.run(self.page.apply_filters())
        res = self.page.state['filtered_items']
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0].api_card.id, 4)

        # Test Monster OR Skill
        self.page.state['filter_card_type'] = ['Monster', 'Skill']
        asyncio.run(self.page.apply_filters())
        res = self.page.state['filtered_items']
        self.assertEqual(len(res), 2)
        self.assertTrue(all(c.api_card.id in [1, 4] for c in res))

    def test_filter_condition(self):
        # Setup data for Consolidated View (uses owned_conditions)
        c1 = ApiCard(id=1, name="Owned Mint", type="Monster", frameType="normal", desc="..")
        vm1 = CardViewModel(c1, 1, True, owned_conditions={'Mint'})

        c2 = ApiCard(id=2, name="Owned Played", type="Monster", frameType="normal", desc="..")
        vm2 = CardViewModel(c2, 1, True, owned_conditions={'Played'})

        c3 = ApiCard(id=3, name="Owned Both", type="Monster", frameType="normal", desc="..")
        vm3 = CardViewModel(c3, 2, True, owned_conditions={'Mint', 'Played'})

        c4 = ApiCard(id=4, name="Unowned", type="Monster", frameType="normal", desc="..")
        vm4 = CardViewModel(c4, 0, False, owned_conditions=set())

        self.page.state['cards_consolidated'] = [vm1, vm2, vm3, vm4]
        self.page.state['filter_card_type'] = [] # Disable type filter

        # Filter Mint
        self.page.state['filter_condition'] = ['Mint']
        asyncio.run(self.page.apply_filters())
        res = self.page.state['filtered_items']
        # Should match vm1 and vm3
        self.assertEqual(len(res), 2)
        ids = sorted([c.api_card.id for c in res])
        self.assertEqual(ids, [1, 3])

        # Filter Played
        self.page.state['filter_condition'] = ['Played']
        asyncio.run(self.page.apply_filters())
        res = self.page.state['filtered_items']
        # Should match vm2 and vm3
        self.assertEqual(len(res), 2)
        ids = sorted([c.api_card.id for c in res])
        self.assertEqual(ids, [2, 3])

        # Filter Mint OR Played
        self.page.state['filter_condition'] = ['Mint', 'Played']
        asyncio.run(self.page.apply_filters())
        res = self.page.state['filtered_items']
        # Should match vm1, vm2, vm3
        self.assertEqual(len(res), 3)
        ids = sorted([c.api_card.id for c in res])
        self.assertEqual(ids, [1, 2, 3])

    def test_sort_direction(self):
        c1 = ApiCard(id=1, name="A Card", type="Monster", frameType="normal", desc="..", atk=1000)
        c2 = ApiCard(id=2, name="B Card", type="Monster", frameType="normal", desc="..", atk=2000)

        vm1 = CardViewModel(c1, 0, False)
        vm2 = CardViewModel(c2, 0, False)
        self.page.state['cards_consolidated'] = [vm1, vm2]
        self.page.state['filter_card_type'] = []

        # Sort Name Asc (Default)
        self.page.state['sort_by'] = 'Name'
        self.page.state['sort_descending'] = False
        asyncio.run(self.page.apply_filters())
        res = self.page.state['filtered_items']
        self.assertEqual(res[0].api_card.name, "A Card")

        # Sort Name Desc
        self.page.state['sort_descending'] = True
        asyncio.run(self.page.apply_filters())
        res = self.page.state['filtered_items']
        self.assertEqual(res[0].api_card.name, "B Card")

        # Sort ATK Asc (Low to High)
        self.page.state['sort_by'] = 'ATK'
        self.page.state['sort_descending'] = False
        asyncio.run(self.page.apply_filters())
        res = self.page.state['filtered_items']
        self.assertEqual(res[0].api_card.atk, 1000)

        # Sort ATK Desc (High to Low)
        self.page.state['sort_descending'] = True
        asyncio.run(self.page.apply_filters())
        res = self.page.state['filtered_items']
        self.assertEqual(res[0].api_card.atk, 2000)

if __name__ == '__main__':
    unittest.main()
