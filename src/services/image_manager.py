import os
import aiohttp
import asyncio
from nicegui import run
import logging

DATA_DIR = "data"
IMAGES_DIR = os.path.join(DATA_DIR, "images")

class ImageManager:
    def __init__(self, images_dir: str = IMAGES_DIR):
        self.images_dir = images_dir
        os.makedirs(self.images_dir, exist_ok=True)
        self.logger = logging.getLogger(__name__)

    def get_local_path(self, card_id: int) -> str:
        """Returns the local file path for a card image."""
        return os.path.join(self.images_dir, f"{card_id}.jpg")

    def image_exists(self, card_id: int) -> bool:
        return os.path.exists(self.get_local_path(card_id))

    async def ensure_image(self, card_id: int, url: str) -> str:
        """
        Ensures the image exists locally. Downloads if missing.
        Returns the local path.
        """
        local_path = self.get_local_path(card_id)
        if os.path.exists(local_path):
            return local_path

        # Download
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        data = await response.read()
                        # Write file in a separate thread to avoid blocking
                        await run.io_bound(self._write_file, local_path, data)
                        return local_path
                    else:
                        self.logger.error(f"Failed to download image for {card_id}: {response.status}")
                        return None
        except Exception as e:
            self.logger.error(f"Error downloading image for {card_id}: {e}")
            return None

    def _write_file(self, path: str, data: bytes):
        with open(path, 'wb') as f:
            f.write(data)

    async def download_images_batch(self, tasks: list):
        """Helper to run a batch of downloads."""
        await asyncio.gather(*tasks)

# Global instance
image_manager = ImageManager()
