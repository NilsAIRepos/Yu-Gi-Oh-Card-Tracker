import sys
from unittest.mock import MagicMock, patch

# Mock nicegui modules to prevent runtime error during import
sys.modules['nicegui'] = MagicMock()
sys.modules['nicegui.ui'] = MagicMock()
sys.modules['nicegui.run'] = MagicMock()

import unittest
import os

# Add src to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

# Explicitly import the module under test
import src.ui.components.single_card_view

class TestSingleCardViewImage(unittest.IsolatedAsyncioTestCase):
    def test_setup_high_res_image_logic_custom_art(self):
        # Since we imported the module, we can patch objects on it directly or via string
        # Using string is safer if we want to ensure we patch the one used by the class

        with patch('src.ui.components.single_card_view.image_manager') as mock_img_mgr:

            # Access class directly from the imported module
            from src.ui.components.single_card_view import SingleCardView

            view = SingleCardView()
            mock_image_element = MagicMock()

            # --- Scenario 1: Custom Artstyle (The Bug) ---
            img_id = 999
            high_res_remote = None
            low_res_fallback = "http://default.com/low.jpg"

            def side_effect_exists(iid, high_res=False):
                if iid == 999:
                    return not high_res # True if high_res=False (standard), False if high_res=True
                return False

            mock_img_mgr.image_exists.side_effect = side_effect_exists

            view._setup_high_res_image_logic(img_id, high_res_remote, low_res_fallback, mock_image_element)

            # Desired behavior: Use local standard image "/images/999.jpg"
            # Current BUG behavior: Falls back to low_res_fallback because it misses the local standard check
            self.assertEqual(mock_image_element.source, "/images/999.jpg",
                             f"Should use local standard image for custom art, got {mock_image_element.source}")

    def test_setup_high_res_image_logic_standard_card(self):
         with patch('src.ui.components.single_card_view.image_manager') as mock_img_mgr, \
              patch('asyncio.create_task') as mock_create_task:

            from src.ui.components.single_card_view import SingleCardView

            view = SingleCardView()
            mock_image_element = MagicMock()

            # --- Scenario 2: Standard Card (Remote High Res Preferred) ---
            img_id_std = 123
            high_res_remote_std = "http://remote.com/high.jpg"
            low_res_fallback = "http://default.com/low.jpg"

            def side_effect_exists(iid, high_res=False):
                if iid == 123:
                    return not high_res
                return False

            mock_img_mgr.image_exists.side_effect = side_effect_exists

            view._setup_high_res_image_logic(img_id_std, high_res_remote_std, low_res_fallback, mock_image_element)

            # Should prefer remote high res because local is not high res
            self.assertEqual(mock_image_element.source, high_res_remote_std,
                             f"Should prefer remote high res over local low res, got {mock_image_element.source}")

if __name__ == '__main__':
    unittest.main()
