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
from src.core.models import Deck

class TestDeckBuilderBanlist(unittest.TestCase):
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

        # Patch banlist_service
        self.banlist_patcher = patch('src.ui.deck_builder.banlist_service')
        self.banlist_mock = self.banlist_patcher.start()

        # Create page instance
        self.page = DeckBuilderPage()

        # Mock UI refresh methods to avoid errors during logic calls
        self.page.render_header = MagicMock()
        self.page.refresh_search_results = MagicMock()
        self.page.prepare_current_page_images = AsyncMock()
        self.page.update_pagination = MagicMock()
        self.page.update_zone_headers = MagicMock()
        self.page.refresh_zone = MagicMock()

    def tearDown(self):
        self.persistence_patcher.stop()
        self.config_patcher.stop()
        self.banlist_patcher.stop()

    def test_calculate_genesys_points(self):
        """Test calculation of total points for Genesys banlist."""
        # Setup Genesys Banlist
        self.page.state['current_banlist_type'] = 'genesys'
        self.page.state['current_banlist_map'] = {
            "1": "10", # Card 1 costs 10 points
            "2": "20", # Card 2 costs 20 points
            "3": "5"   # Card 3 costs 5 points
        }

        # Setup Deck
        deck = Deck(name="Test Deck")
        deck.main = [1, 1, 2] # 10 + 10 + 20 = 40
        deck.extra = [3]      # 5
        deck.side = [2]       # 20
        # Total = 40 + 5 + 20 = 65

        self.page.state['current_deck'] = deck

        points = self.page.calculate_genesys_points()
        self.assertEqual(points, 65)

    def test_check_violations_genesys(self):
        """Test Genesys point limit violation."""
        self.page.state['current_banlist_type'] = 'genesys'
        self.page.state['current_banlist_limit'] = 50
        self.page.state['current_banlist_map'] = {
            "1": "30"
        }

        # Setup Deck
        deck = Deck(name="Test Deck")
        self.page.state['current_deck'] = deck

        # Case 1: Under limit (30 < 50)
        deck.main = [1]
        violations = self.page.check_violations()
        self.assertFalse(violations['global'])
        self.assertFalse(violations['main'])

        # Case 2: Over limit (60 > 50)
        deck.main = [1, 1]
        violations = self.page.check_violations()
        self.assertTrue(violations['global'])
        self.assertTrue(violations['main']) # Check that Main Deck is flagged as requested

    def test_check_violations_classical(self):
        """Test Classical banlist violations (Limited/Banned cards)."""
        self.page.state['current_banlist_type'] = 'classical'
        self.page.state['current_banlist_map'] = {
            "1": "Banned",    # Limit 0
            "2": "Limited",   # Limit 1
            "3": "Semi-Limited" # Limit 2
        }

        deck = Deck(name="Test Deck")
        self.page.state['current_deck'] = deck

        # Case 1: No violations
        deck.main = [2] # 1x Limited (OK)
        deck.side = [3, 3] # 2x Semi-Limited (OK)
        violations = self.page.check_violations()
        self.assertFalse(violations['global'])

        # Case 2: Banned card present
        deck.main = [1] # 1x Banned (Violation)
        violations = self.page.check_violations()
        self.assertTrue(violations['global'])
        self.assertTrue(violations['main'])

        # Case 3: Limit exceeded across zones
        deck.main = [2] # 1x Limited
        deck.side = [2] # 1x Limited -> Total 2 (Violation)
        violations = self.page.check_violations()
        self.assertTrue(violations['global'])
        self.assertTrue(violations['main'])
        self.assertTrue(violations['side'])

        # Case 4: Semi-limit exceeded
        deck.main = [3, 3]
        deck.extra = [3] # Total 3 (Violation)
        violations = self.page.check_violations()
        self.assertTrue(violations['global'])
        self.assertTrue(violations['main'])
        self.assertTrue(violations['extra'])

    def test_check_violations_classical_ignored_cards(self):
        """Test that cards not on banlist are unlimited (default 3, though logic uses default limit 3)."""
        self.page.state['current_banlist_type'] = 'classical'
        self.page.state['current_banlist_map'] = {} # Empty banlist

        deck = Deck(name="Test Deck")
        self.page.state['current_deck'] = deck

        # 3 copies OK
        deck.main = [4, 4, 4]
        violations = self.page.check_violations()
        self.assertFalse(violations['global'])

        # 4 copies (Technically game rule violation, but `check_violations` strictly checks against BANLIST map currently?)
        # Let's check the code:
        # status = ban_map.get(str(cid))
        # limit = 3 # Default limit
        # if count > limit: violation

        deck.main = [4, 4, 4, 4]
        violations = self.page.check_violations()
        self.assertTrue(violations['global'])
        self.assertTrue(violations['main'])

if __name__ == '__main__':
    unittest.main()
