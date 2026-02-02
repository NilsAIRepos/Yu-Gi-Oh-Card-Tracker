
import unittest
import asyncio
from unittest.mock import MagicMock, patch
from src.services.yugipedia_service import YugipediaService, DeckCard

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

if __name__ == '__main__':
    unittest.main()
