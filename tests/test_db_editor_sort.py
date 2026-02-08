import asyncio
import unittest
from unittest.mock import MagicMock, patch
import sys
from dataclasses import dataclass
from typing import List, Optional

# --- Mocks Setup ---

# 1. NiceGUI
mock_ui = MagicMock()
sys.modules['nicegui'] = mock_ui
sys.modules['nicegui.ui'] = mock_ui
sys.modules['nicegui.run'] = MagicMock()

# 2. Persistence
mock_persistence_module = MagicMock()
mock_persistence_obj = MagicMock()
mock_persistence_obj.load_ui_state.return_value = {}
mock_persistence_module.persistence = mock_persistence_obj
sys.modules['src.core.persistence'] = mock_persistence_module

# 3. Other Services
sys.modules['src.services.ygo_api'] = MagicMock()
sys.modules['src.services.image_manager'] = MagicMock()
sys.modules['src.core.config'] = MagicMock()

# 4. Models
mock_models = MagicMock()
@dataclass
class MockApiCard:
    id: int
    name: str
    type: str = "Monster"
    desc: str = ""
    atk: int = 0
    def_: int = 0
    level: int = 0
    race: str = ""
    attribute: str = ""
    archetype: str = ""
    card_sets: list = None
    card_images: list = None

    def matches_category(self, cat): return True

mock_models.ApiCard = MockApiCard
sys.modules['src.core.models'] = mock_models

# 5. FilterPane (used in db_editor)
sys.modules['src.ui.components.filter_pane'] = MagicMock()

# 6. SingleCardView (used in db_editor)
mock_single_card_view = MagicMock()
sys.modules['src.ui.components.single_card_view'] = mock_single_card_view
# It also imports STANDARD_RARITIES
mock_single_card_view.STANDARD_RARITIES = ["Common", "Rare"]

# --- Imports after Mocks ---
try:
    from src.ui.db_editor import DbEditorPage, DbEditorRow
except ImportError as e:
    print(f"Import Error: {e}")
    raise e

# Helpers
class AsyncMock(MagicMock):
    async def __call__(self, *args, **kwargs):
        return super(AsyncMock, self).__call__(*args, **kwargs)

class TestDbEditorSort(unittest.TestCase):
    def setUp(self):
        # We need to patch 'run' where it is used in db_editor
        self.run_patcher = patch('src.ui.db_editor.run')
        self.mock_run = self.run_patcher.start()

        # Configure io_bound to just return result
        async def side_effect(func, *args, **kwargs):
            return func(*args, **kwargs)

        self.mock_run.io_bound = AsyncMock(side_effect=side_effect)

        self.page = DbEditorPage()

        # Mock async methods to prevent actual execution logic
        self.page.prepare_current_page_images = AsyncMock()

        # Mock UI updates
        self.page.update_pagination = MagicMock()
        self.page.update_pagination_labels = MagicMock()

        self.page.render_card_display = MagicMock()
        self.page.render_card_display.refresh = MagicMock()

    def tearDown(self):
        self.run_patcher.stop()

    def test_sort_by_set_code(self):
        # Create test data
        c1 = MockApiCard(id=1, name="Card A")
        c2 = MockApiCard(id=2, name="Card B")
        c3 = MockApiCard(id=3, name="Card C")

        row1 = DbEditorRow(
            api_card=c1,
            set_code="LOB-002",
            set_name="Legend",
            rarity="Common",
            image_url="",
            image_id=None,
            variant_id="1"
        )
        row2 = DbEditorRow(
            api_card=c2,
            set_code="LOB-001",
            set_name="Legend",
            rarity="Common",
            image_url="",
            image_id=None,
            variant_id="2"
        )
        row3 = DbEditorRow(
            api_card=c3,
            set_code="NO SET",
            set_name="No Set",
            rarity="Common",
            image_url="",
            image_id=None,
            variant_id="3"
        )

        # Set initial state
        self.page.state['cards_rows'] = [row1, row2, row3]

        # --- TEST 1: Ascending ---
        self.page.state['sort_by'] = 'Set Code'
        self.page.state['sort_descending'] = False

        # Run filters
        asyncio.run(self.page.apply_filters())

        res = self.page.state['filtered_items']
        self.assertEqual(len(res), 3)
        self.assertEqual(res[0].set_code, "LOB-001")
        self.assertEqual(res[1].set_code, "LOB-002")
        self.assertEqual(res[2].set_code, "NO SET")

        # --- TEST 2: Descending ---
        self.page.state['sort_descending'] = True

        asyncio.run(self.page.apply_filters())

        res = self.page.state['filtered_items']
        self.assertEqual(res[0].set_code, "NO SET")
        self.assertEqual(res[1].set_code, "LOB-002")
        self.assertEqual(res[2].set_code, "LOB-001")

if __name__ == '__main__':
    unittest.main()
