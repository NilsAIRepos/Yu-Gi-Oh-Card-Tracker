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
from src.core.models import ApiCard

class TestDeckBuilderWarnings(unittest.TestCase):
    def setUp(self):
        self.persistence_patcher = patch('src.ui.deck_builder.persistence')
        self.persistence_mock = self.persistence_patcher.start()
        self.persistence_mock.load_ui_state.return_value = {}

        self.config_patcher = patch('src.ui.deck_builder.config_manager')
        self.config_mock = self.config_patcher.start()

        # Patch image manager
        self.img_mgr_patcher = patch('src.ui.deck_builder.image_manager')
        self.img_mgr = self.img_mgr_patcher.start()
        self.img_mgr.image_exists.return_value = False

        self.page = DeckBuilderPage()

        # Setup test cards
        # 1: Main Deck (Normal Monster)
        self.c1 = ApiCard(id=1, name="Blue-Eyes", type="Normal Monster", frameType="normal", desc=".")
        # 2: Extra Deck (Synchro)
        self.c2 = ApiCard(id=2, name="Stardust", type="Synchro Monster", frameType="synchro", desc=".")

        self.page.api_card_map = {1: self.c1, 2: self.c2}
        self.page.state['reference_collection'] = None

    def tearDown(self):
        self.persistence_patcher.stop()
        self.config_patcher.stop()
        self.img_mgr_patcher.stop()

    def test_zone_warnings(self):
        # Reset mocks
        mock_ui.ui.icon.reset_mock()

        # 1. Main in Main -> OK
        self.page._render_deck_card(1, 'main', {})
        calls = [c for c in mock_ui.ui.icon.call_args_list if c.args and c.args[0] == 'warning']
        self.assertEqual(len(calls), 0, "Should not warn for Main in Main")

        # 2. Extra in Extra -> OK
        self.page._render_deck_card(2, 'extra', {})
        calls = [c for c in mock_ui.ui.icon.call_args_list if c.args and c.args[0] == 'warning']
        self.assertEqual(len(calls), 0, "Should not warn for Extra in Extra")

        # 3. Extra in Main -> Warning
        self.page._render_deck_card(2, 'main', {})
        calls = [c for c in mock_ui.ui.icon.call_args_list if c.args and c.args[0] == 'warning']
        self.assertGreater(len(calls), 0, "Should warn for Extra in Main")

        # 4. Main in Extra -> Warning
        mock_ui.ui.icon.reset_mock()
        self.page._render_deck_card(1, 'extra', {})
        calls = [c for c in mock_ui.ui.icon.call_args_list if c.args and c.args[0] == 'warning']
        self.assertGreater(len(calls), 0, "Should warn for Main in Extra")

        # 5. Side Deck -> No Zone Warning
        mock_ui.ui.icon.reset_mock()
        self.page._render_deck_card(2, 'side', {}) # Extra in Side -> Allowed
        calls = [c for c in mock_ui.ui.icon.call_args_list if c.args and c.args[0] == 'warning']
        self.assertEqual(len(calls), 0, "Should not warn for zone in Side Deck")

    def test_quantity_warnings(self):
        mock_ui.ui.icon.reset_mock()
        usage_counter = {}

        # 1st Copy
        self.page._render_deck_card(1, 'main', usage_counter)
        calls = [c for c in mock_ui.ui.icon.call_args_list if c.args and c.args[0] == 'warning']
        self.assertEqual(len(calls), 0)

        # 2nd
        self.page._render_deck_card(1, 'main', usage_counter)
        calls = [c for c in mock_ui.ui.icon.call_args_list if c.args and c.args[0] == 'warning']
        self.assertEqual(len(calls), 0)

        # 3rd
        self.page._render_deck_card(1, 'main', usage_counter)
        calls = [c for c in mock_ui.ui.icon.call_args_list if c.args and c.args[0] == 'warning']
        self.assertEqual(len(calls), 0)

        # 4th -> Warning
        self.page._render_deck_card(1, 'main', usage_counter)
        calls = [c for c in mock_ui.ui.icon.call_args_list if c.args and c.args[0] == 'warning']
        self.assertGreater(len(calls), 0, "Should warn for 4th copy")

        # 5th -> Warning
        mock_ui.ui.icon.reset_mock()
        self.page._render_deck_card(1, 'main', usage_counter)
        calls = [c for c in mock_ui.ui.icon.call_args_list if c.args and c.args[0] == 'warning']
        self.assertGreater(len(calls), 0, "Should warn for 5th copy")

if __name__ == '__main__':
    unittest.main()
