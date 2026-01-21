import asyncio
import queue
import logging
from unittest.mock import MagicMock, AsyncMock, patch

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

from src.services.scanner.manager import scanner_manager
from src.core.models import ApiCard, ApiCardSet, ApiCardImage
from src.services.ygo_api import ygo_service

async def verify_pipeline():
    print("--- Verifying Result Pipeline ---")

    # 1. Setup Mock Data
    mock_card = ApiCard(
        id=12345,
        name="Blue-Eyes White Dragon",
        type="Normal Monster",
        frameType="normal",
        desc="Dragon",
        race="Dragon",
        atk=3000,
        def_=2500,
        card_sets=[
            ApiCardSet(
                set_name="Legend of Blue Eyes",
                set_code="LOB-001",
                set_rarity="Ultra Rare",
                set_rarity_code="(UR)",
                set_price="99.99"
            )
        ],
        card_images=[
            ApiCardImage(
                id=999,
                image_url="http://example.com/img.jpg",
                image_url_small="http://example.com/img_small.jpg"
            )
        ]
    )

    # 2. Mock ygo_service
    # We need to mock load_card_database to return our mock card
    ygo_service.load_card_database = AsyncMock(return_value=[mock_card])

    # 3. Mock image_manager (to avoid networking)
    with patch('src.services.scanner.manager.image_manager.ensure_image', new_callable=AsyncMock) as mock_img:
        mock_img.return_value = "/images/lob-001.jpg"

        # 4. Inject Item into Lookup Queue
        lookup_item = {
            "set_code": "LOB-001",
            "language": "en",
            "ocr_conf": 95.0,
            "visual_rarity": "Ultra Rare",
            "rarity": "Unknown",
            "first_edition": False,
            "warped_image": None # Skip art matching for now
        }

        scanner_manager.lookup_queue.put(lookup_item)
        print(f"Item pushed to lookup_queue: {lookup_item['set_code']}")

        # 5. Run process_pending_lookups
        print("Running process_pending_lookups...")
        await scanner_manager.process_pending_lookups()

        # 6. Check Result Queue
        try:
            result = scanner_manager.result_queue.get_nowait()
            print("SUCCESS: Result received from queue.")
            print(f"Result Name: {result.get('name')}")
            print(f"Result Rarity: {result.get('rarity')}")

            if result.get('name') == "Blue-Eyes White Dragon":
                print("PASS: Correct card resolved.")
            else:
                print("FAIL: Incorrect card resolved.")

        except queue.Empty:
            print("FAIL: Result queue is empty. process_pending_lookups failed.")

if __name__ == "__main__":
    asyncio.run(verify_pipeline())
