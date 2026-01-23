import unittest
import sys
import os
from unittest.mock import MagicMock, patch

# Ensure src is in path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Mock modules
sys.modules['cv2'] = MagicMock()
sys.modules['numpy'] = MagicMock()
sys.modules['torch'] = MagicMock()
sys.modules['ultralytics'] = MagicMock()
sys.modules['langdetect'] = MagicMock()
sys.modules['easyocr'] = MagicMock()
sys.modules['doctr'] = MagicMock()
sys.modules['doctr.io'] = MagicMock()
sys.modules['doctr.models'] = MagicMock()

from src.services.scanner.pipeline import CardScanner
import cv2 # This refers to the mock

class TestBlurDetection(unittest.TestCase):
    def setUp(self):
        self.scanner = CardScanner()

    def test_blur_detection_blurry(self):
        # Setup Mock
        mock_img = MagicMock()
        # Mock shape behavior
        mock_img.shape = (1000, 1000, 3)

        # Mock cv2 behavior
        # cv2.resize return mock
        cv2.resize.return_value = mock_img
        # cv2.cvtColor return mock
        cv2.cvtColor.return_value = mock_img
        # cv2.Laplacian return mock
        mock_lap = MagicMock()
        cv2.Laplacian.return_value = mock_lap
        # .var() return low value (blurry)
        mock_lap.var.return_value = 50.0

        is_blurred = self.scanner.is_image_blurred(mock_img, threshold=100.0)

        self.assertTrue(is_blurred)
        cv2.resize.assert_called() # Should verify resize happened (width 1000 > 800)
        cv2.Laplacian.assert_called()

    def test_blur_detection_sharp(self):
        # Setup Mock
        mock_img = MagicMock()
        mock_img.shape = (1000, 1000, 3)

        mock_lap = MagicMock()
        cv2.Laplacian.return_value = mock_lap
        mock_lap.var.return_value = 200.0

        is_blurred = self.scanner.is_image_blurred(mock_img, threshold=100.0)

        self.assertFalse(is_blurred)

    def test_resize_logic_small_image(self):
        # Small image, no resize
        mock_img = MagicMock()
        mock_img.shape = (500, 500, 3) # Width 500 < 800

        cv2.resize.reset_mock()

        # Setup laplacian mock to avoid crash
        mock_lap = MagicMock()
        cv2.Laplacian.return_value = mock_lap
        mock_lap.var.return_value = 50.0

        self.scanner.is_image_blurred(mock_img)

        cv2.resize.assert_not_called()

if __name__ == '__main__':
    unittest.main()
