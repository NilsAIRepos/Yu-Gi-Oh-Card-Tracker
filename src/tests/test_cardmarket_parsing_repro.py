import unittest
from src.services.cardmarket_parser import CardmarketParser

class TestCardmarketParsingRepro(unittest.TestCase):
    def test_repro_parsing_logic(self):
        # Simulated content based on user report
        # Note: The "Two-Pronged Attack" line has a comment "Check out my other Cards/10.11.20"
        # The Sleeves/Dice sections should be ignored.
        raw_text = """
Start of file garbage...
Yugioh Singles:
1 Two-Pronged Attack (V.2 - Common) 034 EN PO SDK C Check out my other Cards/10.11.20 0,02 EUR
1 Blue-Eyes White Dragon 001 EN NM LOB UTR First Edition 123,45 EUR

NOT RELEVANT This llike this:
Yugioh Promo Products:
1 Legendary Collection Kaiba (2025 Reprint) EN LC06 YT: Spielestyler - OVP - Sealed 15,69 EUR
Sleeves:
1 50 Yu-Gi-Oh Cardback Sleeves EN YGPR OVP - Ready to ship/Lieferbar 2,68 EUR
Dice:
1 Legendary Duelists: Season 2: "Cyber Harpie Lady" Die EN LDS2 new/neu 0,19 EUR
1 Hidden Arsenal: Chapter 1: "Flamvell" Die EN HAC1 Check out my other Cards 0,05 EUR
"""

        # Current behavior (before fix):
        # - Likely fails to parse "Two-Pronged Attack" due to comment.
        # - Might incorrectly try to parse lines in Sleeves/Dice if they match the regex (unlikely for Dice lines without rarity, but Sleeves line looks dangerously close to a card format).

        rows = CardmarketParser.parse_text(raw_text)

        # We expect exactly 2 rows from the Singles section.
        # 1. Two-Pronged Attack
        # 2. Blue-Eyes White Dragon

        # Debug print
        print(f"Parsed {len(rows)} rows.")
        for r in rows:
            print(f" - {r.quantity}x {r.name} ({r.set_prefix}-{r.language}{r.number})")

        # Assertions for the DESIRED behavior (will likely fail now)
        # Note: Depending on current implementation, this might find 0 or 1 rows.

        # We want to ensure we found the tricky one
        names = [r.name for r in rows]
        self.assertIn("Two-Pronged Attack", names, "Should parse line with comment")
        self.assertIn("Blue-Eyes White Dragon", names, "Should parse standard line")

        # We want to ensure we IGNORED the sleeves
        self.assertNotIn("50 Yu-Gi-Oh Cardback Sleeves", names, "Should ignore Sleeves section")

        # Total count check
        self.assertEqual(len(rows), 2, "Should only parse exactly 2 singles")

if __name__ == '__main__':
    unittest.main()
