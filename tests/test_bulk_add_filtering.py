import asyncio
import unittest
from unittest.mock import MagicMock, patch, AsyncMock
import sys

# Mock nicegui
mock_ui = MagicMock()
sys.modules['nicegui'] = mock_ui
sys.modules['nicegui.ui'] = mock_ui
sys.modules['nicegui.run'] = MagicMock()

# Mock dependencies before importing BulkAddPage
sys.modules['src.core.persistence'] = MagicMock()
sys.modules['src.core.changelog_manager'] = MagicMock()
sys.modules['src.core.config'] = MagicMock()
sys.modules['src.services.ygo_api'] = MagicMock()
sys.modules['src.services.image_manager'] = MagicMock()
sys.modules['src.services.collection_editor'] = MagicMock()

from src.ui.bulk_add import BulkAddPage, BulkCollectionEntry
from src.core.models import ApiCard, ApiCardSet, Collection, CollectionCard, CollectionVariant, CollectionEntry

class TestBulkAddFiltering(unittest.TestCase):
    def setUp(self):
        # Setup mocks
        self.persistence_mock = sys.modules['src.core.persistence'].persistence
        self.persistence_mock.list_collections.return_value = []
        self.persistence_mock.load_ui_state.return_value = {}

        self.config_mock = sys.modules['src.core.config'].config_manager
        self.config_mock.get_language.return_value = 'en'
        self.config_mock.get_bulk_add_page_size.return_value = 50

        # Initialize page
        with patch('src.ui.bulk_add.SingleCardView'), \
             patch('src.ui.bulk_add.StructureDeckDialog'), \
             patch('src.ui.bulk_add.FilterPane'):
            self.page = BulkAddPage()

        # Mock render methods
        self.page.render_collection_content = MagicMock()
        self.page.render_collection_content.refresh = MagicMock()
        self.page.collection_filter_pane = MagicMock()

    def test_filter_set_by_name(self):
        # Setup data
        c1 = ApiCard(id=1, name="Blue-Eyes", type="Monster", frameType="normal", desc="desc")

        entry1 = BulkCollectionEntry(
            id="1", api_card=c1, quantity=1, set_code="LOB-EN001", set_name="Legend of Blue Eyes White Dragon",
            rarity="Ultra Rare", language="EN", condition="Near Mint", first_edition=False,
            image_url="", image_id=1, variant_id="v1"
        )

        self.page.col_state['collection_cards'] = [entry1]
        self.page.col_state['collection_page_size'] = 50

        # Test Filter by Set Name (partial match)
        self.page.col_state['filter_set'] = "Legend of Blue Eyes | LOB"

        asyncio.run(self.page.apply_collection_filters())

        res = self.page.col_state['collection_filtered']
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0].id, "1")

    def test_load_collection_data_populates_set_name(self):
        # Mock API Card
        c1 = ApiCard(id=1, name="Blue-Eyes", type="Monster", frameType="normal", desc="desc")
        c1.card_sets = [
            ApiCardSet(set_name="Legend of Blue Eyes White Dragon", set_code="LOB-EN001", set_rarity="Ultra Rare"),
            ApiCardSet(set_name="Structure Deck: Kaiba", set_code="SDK-001", set_rarity="Ultra Rare")
        ]

        # Mock api_card_map
        self.page.api_card_map = {1: c1}

        # Mock Collection Data
        col = Collection(name="Test Col", cards=[
            CollectionCard(card_id=1, name="Blue-Eyes", variants=[
                CollectionVariant(variant_id="v1", set_code="LOB-EN001", rarity="Ultra Rare", entries=[
                    CollectionEntry(quantity=1)
                ])
            ])
        ])

        # Setup run.io_bound to return the collection
        # We patch the 'io_bound' method on the 'run' object in src.ui.bulk_add
        with patch('src.ui.bulk_add.run.io_bound', new_callable=AsyncMock) as mock_io:
            # run.io_bound is called twice:
            # 1. persistence.load_collection -> returns col
            # 2. _build_collection_entries -> returns list[BulkCollectionEntry]

            # We need to construct the expected entries for the second call
            expected_entries = [
                BulkCollectionEntry(
                    id="v1_EN_Near Mint_False",
                    api_card=c1,
                    quantity=1,
                    set_code="LOB-EN001",
                    set_name="Legend of Blue Eyes White Dragon",
                    rarity="Ultra Rare",
                    language="EN",
                    condition="Near Mint",
                    first_edition=False,
                    image_url="url",
                    image_id=1,
                    variant_id="v1",
                    storage_entries={None: 1},
                    price=0.0
                )
            ]

            mock_io.side_effect = [col, expected_entries, col, expected_entries] # Providing enough side effects for multiple calls if needed

            self.page.state['selected_collection'] = "Test Col"

            # Run load_collection_data
            asyncio.run(self.page.load_collection_data())

            # Verify
            entries = self.page.col_state['collection_cards']
            self.assertEqual(len(entries), 1)
            entry = entries[0]
            self.assertEqual(entry.set_code, "LOB-EN001")
            self.assertEqual(entry.set_name, "Legend of Blue Eyes White Dragon")

            # Test Unknown Set Logic
            col2 = Collection(name="Test Col 2", cards=[
                CollectionCard(card_id=1, name="Blue-Eyes", variants=[
                    CollectionVariant(variant_id="v2", set_code="UNKNOWN-CODE", rarity="Common", entries=[
                        CollectionEntry(quantity=1)
                    ])
                ])
            ])

            expected_entries_2 = [
                BulkCollectionEntry(
                    id="v2_EN_Near Mint_False",
                    api_card=c1,
                    quantity=1,
                    set_code="UNKNOWN-CODE",
                    set_name="Unknown Set",
                    rarity="Common",
                    language="EN",
                    condition="Near Mint",
                    first_edition=False,
                    image_url="url",
                    image_id=1,
                    variant_id="v2",
                    storage_entries={None: 1},
                    price=0.0
                )
            ]

            # Reset side effect for next calls (load_collection, then build_entries)
            mock_io.side_effect = [col2, expected_entries_2]

            asyncio.run(self.page.load_collection_data())

            entries = self.page.col_state['collection_cards']
            entry = entries[0]
            self.assertEqual(entry.set_name, "Unknown Set")

if __name__ == '__main__':
    unittest.main()
