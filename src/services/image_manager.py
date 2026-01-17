import os
import aiohttp
import asyncio
from nicegui import run
import logging
from typing import Dict, List, Optional, Callable
from PIL import Image

DATA_DIR = "data"
IMAGES_DIR = os.path.join(DATA_DIR, "images")
SETS_DIR = os.path.join(DATA_DIR, "sets")

class ImageManager:
    def __init__(self, images_dir: str = IMAGES_DIR):
        self.images_dir = images_dir
        self.sets_dir = SETS_DIR
        os.makedirs(self.images_dir, exist_ok=True)
        os.makedirs(self.sets_dir, exist_ok=True)
        self.logger = logging.getLogger(__name__)

    def get_set_image_path(self, set_code: str) -> str:
        """Returns the local file path for a set image."""
        # Sanitize set code for filename
        safe_code = "".join(c for c in set_code if c.isalnum() or c in ('-', '_')).strip()
        return os.path.join(self.sets_dir, f"{safe_code}.jpg")

    def set_image_exists(self, set_code: str) -> bool:
        """Checks if the set image exists locally. Note: Does not verify resolution."""
        return os.path.exists(self.get_set_image_path(set_code))

    def check_image_resolution(self, path: str, min_height: int = 240) -> bool:
        """Checks if the image at path meets the minimum resolution requirement."""
        try:
            with Image.open(path) as img:
                width, height = img.size
                # If height is less than min_height, consider it low res
                if height < min_height:
                    return False
            return True
        except Exception as e:
            self.logger.error(f"Error checking resolution for {path}: {e}")
            return False

    async def ensure_set_image(self, set_code: str, url: str) -> Optional[str]:
        """Ensures the set image exists locally and meets resolution requirements."""
        if not url: return None
        local_path = self.get_set_image_path(set_code)

        # Check existing
        if os.path.exists(local_path):
             # Verify resolution of existing file
             is_good = await run.io_bound(self.check_image_resolution, local_path)
             if is_good:
                 return local_path
             else:
                 self.logger.info(f"Existing image for {set_code} is low resolution (<240p). Deleting.")
                 try:
                    os.remove(local_path)
                 except OSError:
                    pass

        # Download
        try:
            async with aiohttp.ClientSession() as session:
                # Reuse the internal downloader logic but with string ID
                async with session.get(url) as response:
                    if response.status == 200:
                        data = await response.read()
                        await run.io_bound(self._write_file, local_path, data)

                        # Check resolution of new file
                        is_good = await run.io_bound(self.check_image_resolution, local_path)
                        if not is_good:
                            self.logger.warning(f"Downloaded image for {set_code} is low resolution (<240p). Deleting.")
                            try:
                                os.remove(local_path)
                            except OSError:
                                pass
                            return None

                        return local_path
                    else:
                        self.logger.warning(f"Failed to download set image {set_code}: {response.status}")
                        return None
        except Exception as e:
            self.logger.error(f"Error downloading set image {set_code}: {e}")
            return None

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

        self.logger.info(f"Batch download requested for {len(url_map)} images. {total} need downloading.")

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

        self.logger.info(f"Batch download complete. Downloaded {total} images.")

    async def download_images_batch(self, tasks: list):
        """Helper to run a batch of downloads. Deprecated but kept for compatibility."""
        await asyncio.gather(*tasks)

# Global instance
image_manager = ImageManager()
