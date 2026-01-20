import asyncio
import unittest
from unittest.mock import MagicMock, patch
import sys
import os

# Mock nicegui
mock_ui = MagicMock()
sys.modules['nicegui'] = mock_ui
sys.modules['nicegui.ui'] = mock_ui

# Ensure src is in path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.ui.deck_builder import DeckBuilderPage
from src.core.models import ApiCard, Deck

class TestDeckValidation(unittest.TestCase):
    def setUp(self):
        self.persistence_patcher = patch('src.ui.deck_builder.persistence')
        self.persistence_mock = self.persistence_patcher.start()
        self.persistence_mock.load_ui_state.return_value = {}

        self.config_patcher = patch('src.ui.deck_builder.config_manager')
        self.config_mock = self.config_patcher.start()
        self.config_mock.get_deck_builder_page_size.return_value = 50

        self.page = DeckBuilderPage()

    def tearDown(self):
        self.persistence_patcher.stop()
        self.config_patcher.stop()

    def test_is_extra_deck_card(self):
        # Test cases for is_extra_deck_card

        # Main Deck Types
        c1 = ApiCard(id=1, name="Normal Monster", type="Normal Monster", frameType="normal", desc=".")
        self.assertFalse(c1.is_extra_deck_card())

        c2 = ApiCard(id=2, name="Effect Monster", type="Effect Monster", frameType="effect", desc=".")
        self.assertFalse(c2.is_extra_deck_card())

        c3 = ApiCard(id=3, name="Ritual Monster", type="Ritual Monster", frameType="ritual", desc=".")
        self.assertFalse(c3.is_extra_deck_card()) # Rituals go in main deck

        c4 = ApiCard(id=4, name="Spell", type="Spell Card", frameType="spell", desc=".")
        self.assertFalse(c4.is_extra_deck_card())

        c5 = ApiCard(id=5, name="Trap", type="Trap Card", frameType="trap", desc=".")
        self.assertFalse(c5.is_extra_deck_card())

        c6 = ApiCard(id=6, name="Pendulum Monster", type="Pendulum Effect Monster", frameType="effect_pendulum", desc=".")
        self.assertFalse(c6.is_extra_deck_card()) # Pendulums start in main deck

        # Extra Deck Types
        c7 = ApiCard(id=7, name="Fusion Monster", type="Fusion Monster", frameType="fusion", desc=".")
        self.assertTrue(c7.is_extra_deck_card())

        c8 = ApiCard(id=8, name="Synchro Monster", type="Synchro Monster", frameType="synchro", desc=".")
        self.assertTrue(c8.is_extra_deck_card())

        c9 = ApiCard(id=9, name="XYZ Monster", type="XYZ Monster", frameType="xyz", desc=".")
        self.assertTrue(c9.is_extra_deck_card())

        c10 = ApiCard(id=10, name="Link Monster", type="Link Monster", frameType="link", desc=".")
        self.assertTrue(c10.is_extra_deck_card())

        c11 = ApiCard(id=11, name="Synchro Pendulum", type="Synchro Pendulum Effect Monster", frameType="synchro_pendulum", desc=".")
        self.assertTrue(c11.is_extra_deck_card()) # Face down in extra deck initially

    def test_validate_deck_counts(self):
        deck = Deck()
        # 39 cards in main (too few)
        deck.main = [1] * 39
        self.page.state['current_deck'] = deck
        self.page.api_card_map = {1: ApiCard(id=1, name="C1", type="Normal Monster", frameType="normal", desc=".")}

        violations, errors = self.page._validate_deck()
        self.assertTrue(any("Main Deck too small" in e for e in errors))

        # 40 cards (valid)
        deck.main = [1] * 40
        violations, errors = self.page._validate_deck()
        # Note: Might violate copy limit (40 copies of ID 1), but size is ok
        self.assertFalse(any("Main Deck too small" in e for e in errors))

        # 61 cards (too many)
        deck.main = [1] * 61
        violations, errors = self.page._validate_deck()
        self.assertTrue(any("Main Deck too large" in e for e in errors))

        # Extra deck
        deck.main = [1] * 40
        deck.extra = [2] * 16 # Too many
        self.page.api_card_map[2] = ApiCard(id=2, name="C2", type="Fusion Monster", frameType="fusion", desc=".")
        violations, errors = self.page._validate_deck()
        self.assertTrue(any("Extra Deck too large" in e for e in errors))

    def test_validate_copy_limit(self):
        deck = Deck()
        deck.main = [1, 1, 1, 1] # 4 copies
        self.page.state['current_deck'] = deck
        self.page.api_card_map = {1: ApiCard(id=1, name="C1", type="Normal Monster", frameType="normal", desc=".")}

        violations, errors = self.page._validate_deck()
        self.assertIn(1, violations)
        self.assertTrue(any("Max 3 copies allowed" in m for m in violations[1]))

    def test_validate_banlist(self):
        deck = Deck()
        deck.main = [1, 1]
        self.page.state['current_deck'] = deck
        self.page.api_card_map = {1: ApiCard(id=1, name="C1", type="Normal Monster", frameType="normal", desc=".")}

        # Mock banlist: ID 1 is Limited (Max 1)
        self.page.state['current_banlist_map'] = {"1": "Limited"}

        violations, errors = self.page._validate_deck()
        self.assertIn(1, violations)
        self.assertTrue(any("Limited: Max 1 allowed" in m for m in violations[1]))

    def test_validate_zone_validity(self):
        deck = Deck()
        # Extra monster in Main
        deck.main = [2]
        # Main monster in Extra
        deck.extra = [1]

        self.page.state['current_deck'] = deck
        self.page.api_card_map = {
            1: ApiCard(id=1, name="Main Mon", type="Normal Monster", frameType="normal", desc="."),
            2: ApiCard(id=2, name="Extra Mon", type="Fusion Monster", frameType="fusion", desc=".")
        }

        violations, errors = self.page._validate_deck()

        self.assertIn(1, violations)
        self.assertTrue(any("Main Deck card in Extra Deck" in m for m in violations[1]))

        self.assertIn(2, violations)
        self.assertTrue(any("Extra Deck card in Main Deck" in m for m in violations[2]))

if __name__ == '__main__':
    unittest.main()
