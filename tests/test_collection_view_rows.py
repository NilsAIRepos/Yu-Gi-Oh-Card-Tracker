import sys
import unittest
from unittest.mock import MagicMock
import os

# Mock dependencies before importing src.ui.collection
sys.modules['nicegui'] = MagicMock()
sys.modules['nicegui.ui'] = MagicMock()
sys.modules['nicegui.run'] = MagicMock()
sys.modules['yaml'] = MagicMock()

# Mock persistence explicitly to avoid import errors if its dependencies fail
mock_persistence = MagicMock()
sys.modules['src.core.persistence'] = mock_persistence
# We need to ensure 'from src.core.persistence import persistence' works
mock_persistence.persistence = MagicMock()

# Mock other potential heavy dependencies
sys.modules['src.services.ygo_api'] = MagicMock()
sys.modules['src.services.image_manager'] = MagicMock()
sys.modules['src.core.config'] = MagicMock()

# Now import the module under test
from src.ui.collection import build_collector_rows
from src.core.models import ApiCard, ApiCardSet, CollectionCard, CollectionVariant, CollectionEntry

class TestCollectionViewRows(unittest.TestCase):
    def test_regional_variant_grouping(self):
        """
        Tests that a regional variant (e.g. SDK-E001) is grouped with the main API set (SDK-001)
        instead of being treated as Custom/Unmatched, even if the main set (SDK-001) is also owned.
        """
        card_id = 12345

        # Setup API Card with one set: SDK-001
        api_set = ApiCardSet(
            set_code="SDK-001",
            set_name="Starter Deck: Kaiba",
            set_rarity="Ultra Rare",
            set_rarity_code="(UR)",
            set_price="10.00",
            image_id=12345,
            variant_id="var_sdk_001_api" # Use a distinct ID for API source
        )

        api_card = ApiCard(
            id=card_id,
            name="Blue-Eyes White Dragon",
            type="Normal Monster",
            frameType="normal",
            desc="Dragon",
            card_sets=[api_set],
            card_images=[]
        )

        # Setup Owned Variants

        # 1. SDK-001 (Exact Match to API)
        owned_var_1 = CollectionVariant(
            variant_id="var_sdk_001_api", # Matches API ID
            set_code="SDK-001",
            rarity="Ultra Rare",
            image_id=12345,
            entries=[CollectionEntry(quantity=1, language="EN", condition="Near Mint", first_edition=False)]
        )

        # 2. SDK-E001 (Fuzzy Match)
        owned_var_2 = CollectionVariant(
            variant_id="var_sdk_e001_owned",
            set_code="SDK-E001",
            rarity="Ultra Rare",
            image_id=12345,
            entries=[CollectionEntry(quantity=1, language="EN", condition="Near Mint", first_edition=False)]
        )

        owned_details = {
            card_id: CollectionCard(
                card_id=card_id,
                name="Blue-Eyes White Dragon",
                variants=[owned_var_1, owned_var_2]
            )
        }

        # Run function
        rows = build_collector_rows([api_card], owned_details, "en")

        # Verify SDK-001 Row
        row_sdk_001 = next((r for r in rows if r.set_code == "SDK-001"), None)
        self.assertIsNotNone(row_sdk_001, "SDK-001 row should exist")
        self.assertEqual(row_sdk_001.set_name, "Starter Deck: Kaiba")

        # Verify SDK-E001 Row
        row_sdk_e001 = next((r for r in rows if r.set_code == "SDK-E001"), None)
        self.assertIsNotNone(row_sdk_e001, "SDK-E001 row should exist")

        # Check for SUCCESS
        if row_sdk_e001.set_name == "Starter Deck: Kaiba":
             print("\n[SUCCESS] SDK-E001 is grouped with the main API set!")
        else:
             print(f"\n[FAILURE] SDK-E001 set name is: {row_sdk_e001.set_name}")

        self.assertEqual(row_sdk_e001.set_name, "Starter Deck: Kaiba")

if __name__ == "__main__":
    unittest.main()
