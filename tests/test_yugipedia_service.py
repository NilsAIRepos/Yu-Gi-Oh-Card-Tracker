
import unittest
import asyncio
from unittest.mock import MagicMock, patch
from src.services.yugipedia_service import YugipediaService, DeckCard, StructureDeck

class TestYugipediaService(unittest.TestCase):
    def setUp(self):
        self.service = YugipediaService()

    def test_parse_wikitext_simple(self):
        text = """{{Set page header}}
{{Set list|region=EN|rarities=C|print=Reprint|
SDAZ-EN001; Tri-Brigade Mercourier; UR; New
SDAZ-EN002; Springans Kitt; UR; New
SDAZ-EN004; Fallen of Albaz
}}"""
        result = self.service._parse_wikitext(text)
        self.assertIn('main', result)
        self.assertEqual(len(result['main']), 3)
        self.assertEqual(result['main'][0].code, "SDAZ-EN001")
        self.assertEqual(result['main'][0].rarity, "Ultra Rare") # Mapped
        self.assertEqual(result['main'][2].rarity, "Common") # Default

    def test_parse_wikitext_with_bonus(self):
        text = """{{Set page header}}

== Bonus cards ==
{{Set list|region=EN|rarities=Secret Rare, Quarter Century Secret Rare|print=New|
SDWD-EN041; Maiden of White
}}

== Preconstructed Deck ==
{{Set list|region=EN|rarities=Common|print=Reprint|qty=1|
SDWD-EN001; Blue-Eyes White Dragon
}}"""
        result = self.service._parse_wikitext(text)
        self.assertIn('main', result)
        self.assertIn('bonus', result)
        self.assertEqual(len(result['main']), 1)
        self.assertEqual(len(result['bonus']), 1)

        self.assertEqual(result['bonus'][0].code, "SDWD-EN041")
        # Check rarity parsing with comma (should pick first)
        self.assertEqual(result['bonus'][0].rarity, "Secret Rare")
        self.assertEqual(result['main'][0].code, "SDWD-EN001")

    def test_parse_wikitext_leading_whitespace_header(self):
        # Header has leading space
        text = """{{Set page header}}

 == Bonus cards ==
{{Set list|
SDWD-EN041; Maiden of White
}}
"""
        result = self.service._parse_wikitext(text)
        self.assertIn('bonus', result)
        self.assertEqual(len(result['bonus']), 1)
        self.assertEqual(result['bonus'][0].code, "SDWD-EN041")

    def test_parse_wikitext_defaults(self):
         text = """{{Set list|rarities=Super Rare|
TEST-EN001; Card 1
TEST-EN002; Card 2; Common
}}"""
         result = self.service._parse_wikitext(text)
         self.assertEqual(result['main'][0].rarity, "Super Rare")
         self.assertEqual(result['main'][1].rarity, "Common")

    def test_parse_wikitext_qty(self):
         text = """{{Set list|qty=2|
TEST-EN001; Card 1
TEST-EN002; Card 2;;; 3
}}"""
         result = self.service._parse_wikitext(text)
         self.assertEqual(result['main'][0].quantity, 2)
         self.assertEqual(result['main'][1].quantity, 3)

    def test_parse_card_table(self):
        text = """{{CardTable2
| name = Stardust Dragon
| types = Dragon / Synchro / Effect
| atk = 2500
| def = 2000
| level = 8
| attribute = WIND
| database_id = 12345
| en_sets =
CODE-EN001; Test Set; Ultra Rare
CODE-EN002; Test Set 2; Common, Rare
}}"""
        result = self.service._parse_card_table(text, "Stardust_Dragon")
        self.assertEqual(result['name'], "Stardust Dragon")
        self.assertEqual(result['type'], "Synchro Monster")
        self.assertEqual(result['atk'], 2500)
        self.assertEqual(result['database_id'], 12345)
        self.assertEqual(len(result['sets']), 3) # Ultra, Common, Rare
        self.assertEqual(result['sets'][0]['set_code'], "CODE-EN001")
        self.assertEqual(result['sets'][1]['set_code'], "CODE-EN002")
        self.assertEqual(result['sets'][1]['set_rarity'], "Common")
        self.assertEqual(result['sets'][2]['set_rarity'], "Rare")

    @patch('src.services.yugipedia_service.requests.get')
    def test_get_all_decks_deduplication(self, mock_get):
        # Setup mock responses
        # We expect 3 calls for the 3 categories

        # Helper to create mock response
        def create_mock_response(members):
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "query": {
                    "categorymembers": members
                }
            }
            return mock_resp

        # Category 1: Structure Decks
        # Contains Deck A and Deck B
        cat1_members = [
            {'pageid': 101, 'title': 'Structure Deck: A', 'ns': 0},
            {'pageid': 102, 'title': 'Structure Deck: B', 'ns': 0}
        ]

        # Category 2: Starter Decks
        # Contains Deck C
        cat2_members = [
            {'pageid': 103, 'title': 'Starter Deck: C', 'ns': 0}
        ]

        # Category 3: Preconstructed Decks
        # Contains Deck B (duplicate) and Deck D (new)
        cat3_members = [
            {'pageid': 102, 'title': 'Structure Deck: B', 'ns': 0}, # Duplicate of 102
            {'pageid': 104, 'title': 'Speed Duel: D', 'ns': 0}
        ]

        # Configure side_effect for requests.get
        # The service calls them concurrently, but we can inspect the params to return correct data
        # However, asyncio.gather runs them.

        def side_effect(url, params=None, headers=None):
            if params['cmtitle'] == "Category:TCG_Structure_Decks":
                return create_mock_response(cat1_members)
            elif params['cmtitle'] == "Category:TCG_Starter_Decks":
                return create_mock_response(cat2_members)
            elif params['cmtitle'] == "Category:Preconstructed_Decks":
                return create_mock_response(cat3_members)
            return MagicMock(status_code=404)

        mock_get.side_effect = side_effect

        # Run the async method
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        results = loop.run_until_complete(self.service.get_all_decks())
        loop.close()

        # Verification
        # Total unique decks should be 4: A, B, C, D
        self.assertEqual(len(results), 4)

        # Check titles and types
        # Sort order is by title.
        # "Speed Duel: D", "Starter Deck: C", "Structure Deck: A", "Structure Deck: B"
        # Alphabetical:
        # 1. Speed Duel: D
        # 2. Starter Deck: C
        # 3. Structure Deck: A
        # 4. Structure Deck: B

        titles = [d.title for d in results]
        self.assertEqual(titles, sorted(["Structure Deck: A", "Structure Deck: B", "Starter Deck: C", "Speed Duel: D"]))

        # Check types
        deck_map = {d.title: d for d in results}
        self.assertEqual(deck_map["Structure Deck: A"].deck_type, 'STRUCTURE')
        self.assertEqual(deck_map["Starter Deck: C"].deck_type, 'STARTER')
        self.assertEqual(deck_map["Speed Duel: D"].deck_type, 'PRECON')

        # Deck B could be STRUCTURE or PRECON depending on which one was processed first/kept.
        # Logic says: results = results[0] + results[1] + results[2]
        # results[0] is STRUCTURE. results[2] is PRECON.
        # Deduplication keeps first occurrence.
        # So Deck B should be STRUCTURE.
        self.assertEqual(deck_map["Structure Deck: B"].deck_type, 'STRUCTURE')

if __name__ == '__main__':
    unittest.main()
