import unittest
from unittest.mock import MagicMock, patch
import sys
from dataclasses import dataclass

# Define dummy model
@dataclass
class OCRResult:
    engine: str
    raw_text: str
    set_id: str = None
    set_id_conf: float = 0.0
    language: str = "EN"

# Mock dependencies
sys.modules['cv2'] = MagicMock()
sys.modules['numpy'] = MagicMock()
sys.modules['langdetect'] = MagicMock()
sys.modules['easyocr'] = MagicMock()
sys.modules['torch'] = MagicMock()
# sys.modules['paddleocr'] = MagicMock() # Removed from pipeline
sys.modules['ultralytics'] = MagicMock()

# Mock models module
mock_models = MagicMock()
mock_models.OCRResult = OCRResult
sys.modules['src.services.scanner.models'] = mock_models

# We need to mock concurrent.futures before importing pipeline
sys.modules['concurrent.futures'] = MagicMock()

from src.services.scanner.pipeline import CardScanner

class TestPaddleParsing(unittest.TestCase):

    @patch('src.services.scanner.pipeline.ProcessPoolExecutor')
    @patch('src.services.scanner.pipeline.cv2.imwrite') # Prevent file write
    @patch('src.services.scanner.pipeline.os.remove')   # Prevent file delete
    def test_paddle_subprocess_handling(self, mock_remove, mock_imwrite, mock_executor_cls):
        """
        Verify that ocr_scan correctly handles data returned from the subprocess.
        """
        scanner = CardScanner()

        # Mock Executor Context Manager
        mock_executor = MagicMock()
        mock_executor_cls.return_value.__enter__.return_value = mock_executor

        # Mock Future
        mock_future = MagicMock()
        mock_executor.submit.return_value = mock_future

        # Mock Worker Result
        # The worker returns {"status": "success", "data": [{"text": "...", "conf": ...}]}
        worker_result = {
            "status": "success",
            "data": [
                {"text": "GoodLine", "conf": 0.99},
                {"text": "BadLine", "conf": 0.0}
            ]
        }
        mock_future.result.return_value = worker_result

        mock_image = MagicMock()
        mock_image.shape = (100, 100, 3)

        # Execute
        result = scanner.ocr_scan(mock_image, engine='paddle')

        # Assert
        self.assertIn("GoodLine", result.raw_text)
        self.assertIn("BadLine", result.raw_text)

        # Verify timeout was passed
        mock_future.result.assert_called_with(timeout=60)

    @patch('src.services.scanner.pipeline.ProcessPoolExecutor')
    @patch('src.services.scanner.pipeline.cv2.imwrite')
    @patch('src.services.scanner.pipeline.os.remove')
    def test_paddle_subprocess_timeout(self, mock_remove, mock_imwrite, mock_executor_cls):
        """
        Verify handling of TimeoutError from subprocess.
        """
        scanner = CardScanner()
        mock_executor = MagicMock()
        mock_executor_cls.return_value.__enter__.return_value = mock_executor
        mock_future = MagicMock()
        mock_executor.submit.return_value = mock_future

        # Simulate Timeout
        mock_future.result.side_effect = TimeoutError()

        mock_image = MagicMock()
        mock_image.shape = (100, 100, 3)

        result = scanner.ocr_scan(mock_image, engine='paddle')

        # Should handle timeout gracefully and return empty result
        self.assertEqual(result.raw_text, "")

if __name__ == '__main__':
    unittest.main()
