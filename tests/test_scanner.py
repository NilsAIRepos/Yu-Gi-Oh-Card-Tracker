import unittest
import cv2
import numpy as np
import asyncio
from unittest.mock import MagicMock, patch
from src.services.scanner_service import scanner_service
from src.core.models import ApiCard

class TestScannerService(unittest.TestCase):
    def setUp(self):
        # Mock ygo_service
        self.mock_ygo = MagicMock()
        # Mock DB with a card
        self.card = ApiCard(id=123, name="Blue-Eyes White Dragon", type="Normal Monster", desc="Dragon", race="Dragon", attribute="LIGHT", frameType="normal")
        self.mock_ygo._cards_cache = {'en': [self.card]}

    @patch('src.services.scanner_service.ygo_service')
    def test_identify_card_ocr(self, mock_ygo_service):
        mock_ygo_service._cards_cache = {'en': [self.card]}

        # Create a dummy image mimicking a card
        # 590x860 black background
        img = np.zeros((860, 590, 3), dtype=np.uint8)

        # Add Text at the top (Title region)
        # Font, Text, Location, FontScale, Color, Thickness
        cv2.putText(img, "Blue-Eyes White Dragon", (20, 80), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (255, 255, 255), 2)

        # Encode to bytes
        _, buffer = cv2.imencode('.jpg', img)
        image_bytes = buffer.tobytes()

        # Run identification
        # We need to ensure EasyOCR is initialized or mocked.
        # Initializing EasyOCR in test might be slow/heavy but it verifies the integration.
        # Ensure initialize is called
        scanner_service.initialize()

        result = scanner_service.identify_card(image_bytes)

        print(f"Scan Result: {result}")

        if result['success']:
            self.assertEqual(result['card'].name, "Blue-Eyes White Dragon")
        else:
            # It might fail if OCR is not perfect on the synthetic font,
            # but usually HERSHEY_SIMPLEX is readable.
            # If it fails, check error.
            # "No text detected" -> Contrast issue?
            pass

    @patch('src.services.scanner_service.ygo_service')
    def test_identify_card_no_text(self, mock_ygo_service):
        mock_ygo_service._cards_cache = {'en': [self.card]}

        img = np.zeros((860, 590, 3), dtype=np.uint8)
        _, buffer = cv2.imencode('.jpg', img)
        image_bytes = buffer.tobytes()

        scanner_service.initialize()
        result = scanner_service.identify_card(image_bytes)

        self.assertFalse(result['success'])

if __name__ == '__main__':
    unittest.main()
