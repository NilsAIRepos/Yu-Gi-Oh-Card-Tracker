import asyncio
import unittest
from unittest.mock import MagicMock, patch
import sys

# Mock nicegui
mock_ui = MagicMock()
sys.modules['nicegui'] = mock_ui
sys.modules['nicegui.ui'] = mock_ui
sys.modules['nicegui.run'] = MagicMock()

from src.ui.storage import StoragePage, StorageRow
from src.core.models import ApiCard, ApiCardPrice

class TestStorageSort(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Patch persistence
        self.persistence_patcher = patch('src.ui.storage.persistence')
        self.persistence_mock = self.persistence_patcher.start()
        self.persistence_mock.list_collections.return_value = []
        self.persistence_mock.load_ui_state.return_value = {}

        # Patch ygo_service
        self.ygo_patcher = patch('src.ui.storage.ygo_service')
        self.ygo_mock = self.ygo_patcher.start()

        # Patch config_manager
        self.config_patcher = patch('src.ui.storage.config_manager')
        self.config_mock = self.config_patcher.start()
        self.config_mock.get_language.return_value = 'en'

        # Patch image_manager
        self.img_patcher = patch('src.ui.storage.image_manager')
        self.img_mock = self.img_patcher.start()

        # Create a future that doesn't need a loop at creation time or use a helper
        f = asyncio.Future()
        f.set_result(None)
        self.img_mock.ensure_flag_image.return_value = f

        # Patch StorageDialog since it's instantiated in __init__
        self.dialog_patcher = patch('src.ui.storage.StorageDialog')
        self.dialog_mock = self.dialog_patcher.start()

        self.page = StoragePage()

        # Mock refreshable methods
        self.page.render_detail_grid = MagicMock()
        self.page.render_pagination_controls = MagicMock()
        self.page.render_content = MagicMock()

    async def asyncTearDown(self):
        self.persistence_patcher.stop()
        self.ygo_patcher.stop()
        self.config_patcher.stop()
        self.img_patcher.stop()
        self.dialog_patcher.stop()

    async def test_sort_atk(self):
        c1 = ApiCard(id=1, name="Weak", type="Monster", frameType="normal", desc="..", atk=100)
        c2 = ApiCard(id=2, name="Strong", type="Monster", frameType="normal", desc="..", atk=2000)

        row1 = StorageRow(c1, "A", "Set", "C", "url", 1, "en", "NM", False)
        row2 = StorageRow(c2, "B", "Set", "C", "url", 1, "en", "NM", False)

        self.page.state['rows'] = [row1, row2]
        self.page.state['storage_detail_sort_by'] = 'ATK'
        self.page.state['storage_detail_sort_desc'] = False

        await self.page.apply_filters()
        res = self.page.state['filtered_rows']
        self.assertEqual(res[0].api_card.id, 1) # 100 < 2000

        self.page.state['storage_detail_sort_desc'] = True
        await self.page.apply_filters()
        res = self.page.state['filtered_rows']
        self.assertEqual(res[0].api_card.id, 2) # 2000 > 100

    async def test_sort_def(self):
        c1 = ApiCard(id=1, name="Weak Def", type="Monster", frameType="normal", desc="..")
        c1.def_ = 0
        c2 = ApiCard(id=2, name="Strong Def", type="Monster", frameType="normal", desc="..")
        c2.def_ = 3000

        row1 = StorageRow(c1, "A", "Set", "C", "url", 1, "en", "NM", False)
        row2 = StorageRow(c2, "B", "Set", "C", "url", 1, "en", "NM", False)

        self.page.state['rows'] = [row1, row2]
        self.page.state['storage_detail_sort_by'] = 'DEF'
        self.page.state['storage_detail_sort_desc'] = False

        await self.page.apply_filters()
        res = self.page.state['filtered_rows']
        self.assertEqual(res[0].api_card.id, 1)

        self.page.state['storage_detail_sort_desc'] = True
        await self.page.apply_filters()
        res = self.page.state['filtered_rows']
        self.assertEqual(res[0].api_card.id, 2)

    async def test_sort_level(self):
        c1 = ApiCard(id=1, name="Low Level", type="Monster", frameType="normal", desc="..", level=1)
        c2 = ApiCard(id=2, name="High Level", type="Monster", frameType="normal", desc="..", level=12)

        row1 = StorageRow(c1, "A", "Set", "C", "url", 1, "en", "NM", False)
        row2 = StorageRow(c2, "B", "Set", "C", "url", 1, "en", "NM", False)

        self.page.state['rows'] = [row1, row2]
        self.page.state['storage_detail_sort_by'] = 'Level'
        self.page.state['storage_detail_sort_desc'] = False

        await self.page.apply_filters()
        res = self.page.state['filtered_rows']
        self.assertEqual(res[0].api_card.id, 1)

        self.page.state['storage_detail_sort_desc'] = True
        await self.page.apply_filters()
        res = self.page.state['filtered_rows']
        self.assertEqual(res[0].api_card.id, 2)

    async def test_sort_price(self):
        p1 = ApiCardPrice(tcgplayer_price="1.50")
        c1 = ApiCard(id=1, name="Cheap", type="Monster", frameType="normal", desc="..", card_prices=[p1])

        p2 = ApiCardPrice(tcgplayer_price="100.00")
        c2 = ApiCard(id=2, name="Expensive", type="Monster", frameType="normal", desc="..", card_prices=[p2])

        row1 = StorageRow(c1, "A", "Set", "C", "url", 1, "en", "NM", False)
        row2 = StorageRow(c2, "B", "Set", "C", "url", 1, "en", "NM", False)

        self.page.state['rows'] = [row1, row2]
        self.page.state['storage_detail_sort_by'] = 'Price'
        self.page.state['storage_detail_sort_desc'] = False

        await self.page.apply_filters()
        res = self.page.state['filtered_rows']
        self.assertEqual(res[0].api_card.id, 1) # 1.50 < 100.00

        self.page.state['storage_detail_sort_desc'] = True
        await self.page.apply_filters()
        res = self.page.state['filtered_rows']
        self.assertEqual(res[0].api_card.id, 2) # 100.00 > 1.50

    async def test_sort_quantity(self):
        c1 = ApiCard(id=1, name="Few", type="Monster", frameType="normal", desc="..")
        c2 = ApiCard(id=2, name="Many", type="Monster", frameType="normal", desc="..")

        row1 = StorageRow(c1, "A", "Set", "C", "url", 1, "en", "NM", False)
        row2 = StorageRow(c2, "B", "Set", "C", "url", 10, "en", "NM", False)

        self.page.state['rows'] = [row1, row2]
        self.page.state['storage_detail_sort_by'] = 'Quantity'
        self.page.state['storage_detail_sort_desc'] = False

        await self.page.apply_filters()
        res = self.page.state['filtered_rows']
        self.assertEqual(res[0].api_card.id, 1) # 1 < 10

        self.page.state['storage_detail_sort_desc'] = True
        await self.page.apply_filters()
        res = self.page.state['filtered_rows']
        self.assertEqual(res[0].api_card.id, 2) # 10 > 1

if __name__ == '__main__':
    unittest.main()
