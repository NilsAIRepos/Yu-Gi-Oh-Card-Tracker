import sys
import os
import asyncio
import queue
from unittest.mock import MagicMock, patch

# Add project root to path
sys.path.append(os.getcwd())

# Mock modules that might be missing or heavy
sys.modules['cv2'] = MagicMock()
sys.modules['src.services.scanner.pipeline'] = MagicMock()

from src.services.scanner.manager import ScannerManager
from src.services.scanner.models import OCRResult

async def test_image_id_propagation():
    print("Initializing ScannerManager...")
    manager = ScannerManager()

    # Mock find_best_match to return a candidate with image_id
    async def mock_find_best_match(*args, **kwargs):
        print("Mock find_best_match called")
        return {
            "ambiguity": False,
            "candidates": [{
                "name": "Test Card",
                "card_id": 12345,
                "set_code": "SRL-G021",
                "rarity": "Common",
                "score": 90.0,
                "image_id": 99999, # The ID we want to see propagated
                "variant_id": None
            }]
        }

    manager.find_best_match = mock_find_best_match

    # Mock ygo_service to avoid DB calls
    with patch('src.services.scanner.manager.ygo_service') as mock_ygo:
        # Mock get_card to return None so we skip image path resolution logic
        mock_ygo.get_card.return_value = None

        # Prepare lookup data
        lookup_data = {
            "ocr_result": {
                "engine": "test",
                "raw_text": "Test",
                "language": "EN"
            },
            "art_match": None
        }

        manager.lookup_queue.put(lookup_data)

        print("Processing pending lookups...")
        await manager.process_pending_lookups()

        try:
            result = manager.get_latest_result()
            print(f"Result keys: {result.keys() if result else 'None'}")

            if result and 'image_id' in result:
                print(f"SUCCESS: image_id found in result: {result['image_id']}")
                if result['image_id'] == 99999:
                    print("Verified value matches.")
                else:
                    print(f"Value mismatch. Expected 99999, got {result['image_id']}")
            else:
                print("FAILURE: image_id NOT found in result.")

        except queue.Empty:
            print("FAILURE: No result in queue.")

if __name__ == "__main__":
    asyncio.run(test_image_id_propagation())
