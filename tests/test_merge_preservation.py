
import unittest
import sys
import os

# Ensure src is in path
sys.path.append(os.getcwd())

from src.services.ygo_api import YugiohService
from src.core.models import ApiCard, ApiCardSet, ApiCardImage

class TestIssueReproduction(unittest.TestCase):
    def test_merge_drops_local_cards(self):
        service = YugiohService()

        # 1. Setup Local Data with a Custom Card
        custom_card = ApiCard(
            id=999999999,
            name="Custom Dark Magician",
            type="Normal Monster",
            frameType="normal",
            desc="A custom card.",
            race="Spellcaster",
            atk=2500,
            def_=2100,
            card_images=[
                ApiCardImage(id=12345, image_url="http://example.com/1.jpg", image_url_small="http://example.com/1s.jpg")
            ],
            card_sets=[
                ApiCardSet(
                    set_name="Custom Set",
                    set_code="CUST-EN001",
                    set_rarity="Common",
                    set_price="0.00"
                )
            ]
        )

        # 2. Setup API Data (Official Card only)
        official_card = ApiCard(
            id=46986414,
            name="Dark Magician",
            type="Normal Monster",
            frameType="normal",
            desc="The ultimate wizard.",
            race="Spellcaster",
            atk=2500,
            def_=2100,
            card_images=[
                ApiCardImage(id=46986414, image_url="http://example.com/dm.jpg", image_url_small="http://example.com/dms.jpg")
            ],
            card_sets=[
                ApiCardSet(
                    set_name="Legend of Blue Eyes",
                    set_code="LOB-EN005",
                    set_rarity="Ultra Rare",
                    set_price="100.00"
                )
            ]
        )

        local_cards = [custom_card, official_card]
        api_cards = [official_card] # Custom card is missing from API

        # 3. Perform Merge
        merged = service._merge_database_data(local_cards, api_cards)

        # 4. Assertions
        merged_ids = [c.id for c in merged]

        print(f"Merged IDs: {merged_ids}")

        # In the fixed state, the custom card (999999999) should be PRESENT.
        self.assertIn(999999999, merged_ids, "Custom card should be preserved after update")
        self.assertIn(46986414, merged_ids)

if __name__ == '__main__':
    unittest.main()
