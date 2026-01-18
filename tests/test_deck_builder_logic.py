import asyncio
import unittest
from unittest.mock import MagicMock, patch, AsyncMock
import sys
import os

# Mock nicegui
mock_ui = MagicMock()
sys.modules['nicegui'] = mock_ui
sys.modules['nicegui.ui'] = mock_ui

# Ensure src is in path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.ui.deck_builder import DeckBuilderPage
from src.core.models import ApiCard, ApiCardSet, ApiCardPrice, Collection, CollectionCard, CollectionVariant, CollectionEntry
from src.ui.components.filter_pane import FilterPane

class TestDeckBuilderLogic(unittest.TestCase):
    def setUp(self):
        # Patch dependencies
        self.persistence_patcher = patch('src.ui.deck_builder.persistence')
        self.persistence_mock = self.persistence_patcher.start()
        self.persistence_mock.load_ui_state.return_value = {}
        self.persistence_mock.list_decks.return_value = []
        self.persistence_mock.list_collections.return_value = []

        self.config_patcher = patch('src.ui.deck_builder.config_manager')
        self.config_mock = self.config_patcher.start()
        self.config_mock.get_deck_builder_page_size.return_value = 50

        # Create page instance
        self.page = DeckBuilderPage()

        # Mock UI refresh methods
        self.page.render_header = MagicMock()
        self.page.refresh_search_results = MagicMock()
        self.page.prepare_current_page_images = AsyncMock()
        self.page.update_pagination = MagicMock()

    def tearDown(self):
        self.persistence_patcher.stop()
        self.config_patcher.stop()

    def test_search_set_code(self):
        # Setup data
        c1 = ApiCard(id=1, name="Card 1", type="Monster", frameType="normal", desc="desc",
                     card_sets=[ApiCardSet(set_name="Set A", set_code="SETA-EN001", set_rarity="Common")])
        c2 = ApiCard(id=2, name="Card 2", type="Monster", frameType="normal", desc="desc",
                     card_sets=[ApiCardSet(set_name="Set B", set_code="SETB-EN001", set_rarity="Rare")])

        self.page.state['all_api_cards'] = [c1, c2]
        self.page.state['filter_card_type'] = [] # Disable default type filter for clarity

        # Test Search "SETA"
        self.page.state['search_text'] = "SETA"
        asyncio.run(self.page.apply_filters())
        res = self.page.state['filtered_items']
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0].id, 1)

        # Test Search "SETB"
        self.page.state['search_text'] = "SETB"
        asyncio.run(self.page.apply_filters())
        res = self.page.state['filtered_items']
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0].id, 2)

        # Test Search "EN001" (Both)
        self.page.state['search_text'] = "EN001"
        asyncio.run(self.page.apply_filters())
        res = self.page.state['filtered_items']
        self.assertEqual(len(res), 2)

    def test_sort_price(self):
        c1 = ApiCard(id=1, name="Cheap", type="Monster", frameType="normal", desc=".",
                     card_prices=[ApiCardPrice(tcgplayer_price="1.50")])
        c2 = ApiCard(id=2, name="Expensive", type="Monster", frameType="normal", desc=".",
                     card_prices=[ApiCardPrice(tcgplayer_price="100.00")])
        c3 = ApiCard(id=3, name="Unknown", type="Monster", frameType="normal", desc=".", card_prices=[])

        self.page.state['all_api_cards'] = [c1, c2, c3]
        self.page.state['filter_card_type'] = []

        # Sort Price Asc
        self.page.state['sort_by'] = 'Price'
        self.page.state['sort_descending'] = False
        asyncio.run(self.page.apply_filters())
        res = self.page.state['filtered_items']
        # Order: Unknown (0), Cheap (1.50), Expensive (100)
        self.assertEqual([c.id for c in res], [3, 1, 2])

        # Sort Price Desc
        self.page.state['sort_descending'] = True
        asyncio.run(self.page.apply_filters())
        res = self.page.state['filtered_items']
        # Order: Expensive, Cheap, Unknown
        self.assertEqual([c.id for c in res], [2, 1, 3])

    def test_sort_quantity(self):
        c1 = ApiCard(id=1, name="Owned 5", type="Monster", frameType="normal", desc=".")
        c2 = ApiCard(id=2, name="Owned 0", type="Monster", frameType="normal", desc=".")
        c3 = ApiCard(id=3, name="Owned 2", type="Monster", frameType="normal", desc=".")

        col = Collection(name="TestCol", cards=[
            CollectionCard(card_id=1, name="C1", variants=[
                CollectionVariant(variant_id="v1", set_code="S", rarity="C", entries=[
                    CollectionEntry(quantity=5)
                ])
            ]),
            CollectionCard(card_id=3, name="C3", variants=[
                 CollectionVariant(variant_id="v2", set_code="S", rarity="C", entries=[
                    CollectionEntry(quantity=2)
                ])
            ])
        ])

        self.page.state['all_api_cards'] = [c1, c2, c3]
        self.page.state['reference_collection'] = col
        self.page.state['filter_card_type'] = []

        # Sort Quantity Asc
        self.page.state['sort_by'] = 'Quantity'
        self.page.state['sort_descending'] = False
        asyncio.run(self.page.apply_filters())
        res = self.page.state['filtered_items']
        # Order: Owned 0, Owned 2, Owned 5 -> id: 2, 3, 1
        self.assertEqual([c.id for c in res], [2, 3, 1])

        # Sort Quantity Desc
        self.page.state['sort_descending'] = True
        asyncio.run(self.page.apply_filters())
        res = self.page.state['filtered_items']
        # Order: Owned 5, Owned 2, Owned 0 -> id: 1, 3, 2
        self.assertEqual([c.id for c in res], [1, 3, 2])

    def test_duplicate_check(self):
        # Setup available decks
        self.page.state['available_decks'] = ['MyDeck.ydk', 'OtherDeck.ydk']

        # Exact match
        self.assertTrue(self.page._is_duplicate_deck("MyDeck"))

        # Case insensitive match
        self.assertTrue(self.page._is_duplicate_deck("mydeck"))
        self.assertTrue(self.page._is_duplicate_deck("MYDECK"))

        # No match
        self.assertFalse(self.page._is_duplicate_deck("NewDeck"))
        self.assertFalse(self.page._is_duplicate_deck("MyDeck2"))

if __name__ == '__main__':
    unittest.main()
