import os
import aiohttp
import asyncio
from nicegui import run
import logging
from typing import Dict, List, Optional, Callable

DATA_DIR = "data"
IMAGES_DIR = os.path.join(DATA_DIR, "images")

class ImageManager:
    def __init__(self, images_dir: str = IMAGES_DIR):
        self.images_dir = images_dir
        os.makedirs(self.images_dir, exist_ok=True)
        self.logger = logging.getLogger(__name__)

    def get_local_path(self, card_id: int, high_res: bool = False) -> str:
        """Returns the local file path for a card image."""
        suffix = "_high" if high_res else ""
        return os.path.join(self.images_dir, f"{card_id}{suffix}.jpg")

    def image_exists(self, card_id: int, high_res: bool = False) -> bool:
        return os.path.exists(self.get_local_path(card_id, high_res))

    async def ensure_image(self, card_id: int, url: str, high_res: bool = False) -> str:
        """
        Ensures the image exists locally. Downloads if missing.
        Returns the local path.
        """
        local_path = self.get_local_path(card_id, high_res)
        if os.path.exists(local_path):
            return local_path

        # Download
        try:
            async with aiohttp.ClientSession() as session:
                return await self._download_with_session(session, card_id, url, local_path)
        except Exception as e:
            self.logger.error(f"Error downloading image for {card_id}: {e}")
            return None

    async def _download_with_session(self, session: aiohttp.ClientSession, card_id: int, url: str, local_path: str) -> Optional[str]:
        try:
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
             self.logger.error(f"Error downloading image for {card_id} inside session: {e}")
             return None

    def _write_file(self, path: str, data: bytes):
        with open(path, 'wb') as f:
            f.write(data)

    async def download_batch(self, url_map: Dict[int, str], concurrency: int = 20, progress_callback: Optional[Callable[[float], None]] = None, high_res: bool = False):
        """
        Downloads images for the given map of {card_id: url}.
        Skips existing images.
        """
        # Filter out existing
        to_download = {id: url for id, url in url_map.items() if not self.image_exists(id, high_res)}
        total = len(to_download)
        if total == 0:
            if progress_callback: progress_callback(1.0)
            return

        semaphore = asyncio.Semaphore(concurrency)
        completed = 0

        async def _task(card_id, url):
            nonlocal completed
            async with semaphore:
                local_path = self.get_local_path(card_id, high_res)
                await self._download_with_session(session, card_id, url, local_path)
                completed += 1
                if progress_callback:
                    progress_callback(completed / total)

        async with aiohttp.ClientSession() as session:
            tasks = [_task(cid, url) for cid, url in to_download.items()]
            await asyncio.gather(*tasks)

    async def download_images_batch(self, tasks: list):
        """Helper to run a batch of downloads. Deprecated but kept for compatibility."""
        await asyncio.gather(*tasks)

# Global instance
image_manager = ImageManager()
