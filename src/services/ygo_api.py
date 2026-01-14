import requests
import json
import os
import asyncio
from typing import List, Optional, Callable
from src.core.models import ApiCard
from src.services.image_manager import image_manager
from nicegui import run

API_URL = "https://db.ygoprodeck.com/api/v7/cardinfo.php"
DB_FILE = os.path.join("data", "card_db.json")

class YugiohService:
    def __init__(self):
        self._cards_cache: List[ApiCard] = []

    async def fetch_card_database(self) -> int:
        """Downloads the full database from the API. Returns count of cards."""
        # Run synchronous request in a thread
        response = await run.io_bound(requests.get, API_URL)
        if response.status_code == 200:
            data = response.json()
            # The API returns {"data": [...] }
            cards_data = data.get("data", [])

            # Save raw JSON first
            await run.io_bound(self._save_db_file, cards_data)

            # Update cache (parse in thread)
            self._cards_cache = await run.cpu_bound(self._parse_cards, cards_data)
            return len(self._cards_cache)
        else:
            raise Exception(f"API Error: {response.status_code}")

    def _save_db_file(self, data):
        with open(DB_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f)

    async def load_card_database(self) -> List[ApiCard]:
        """Loads the database from disk. If missing, fetches it."""
        if self._cards_cache:
            return self._cards_cache

        if not os.path.exists(DB_FILE):
            await self.fetch_card_database()

        if not self._cards_cache and os.path.exists(DB_FILE):
             # Read file
             data = await run.io_bound(self._read_db_file)
             self._cards_cache = await run.cpu_bound(self._parse_cards, data)

        return self._cards_cache

    def _read_db_file(self):
        with open(DB_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)

    def _parse_cards(self, data: List[dict]) -> List[ApiCard]:
        return [ApiCard(**c) for c in data]

    def get_card(self, card_id: int) -> Optional[ApiCard]:
        # Optimization: Build a dict index if lookups are frequent
        for c in self._cards_cache:
            if c.id == card_id:
                return c
        return None

    def search_by_name(self, name: str) -> Optional[ApiCard]:
        for c in self._cards_cache:
            if c.name.lower() == name.lower():
                return c
        return None

    # Forwarding image manager calls
    async def get_image_path(self, card_id: int) -> Optional[str]:
        card = self.get_card(card_id)
        if not card or not card.card_images:
            return None

        # Use first image
        url = card.card_images[0].image_url
        return await image_manager.ensure_image(card_id, url)

    async def download_all_images(self, progress_callback: Optional[Callable[[float], None]] = None):
        """Downloads images for all cards in the database."""
        cards = await self.load_card_database()
        total = len(cards)
        chunk_size = 20 # Conservative chunk size to avoid socket limits

        for i in range(0, total, chunk_size):
            chunk = cards[i:i + chunk_size]
            tasks = []
            for card in chunk:
                 if card.card_images:
                     url = card.card_images[0].image_url_small # Use small image for browsing to save space/bandwidth
                     tasks.append(image_manager.ensure_image(card.id, url))

            await asyncio.gather(*tasks)

            if progress_callback:
                progress_callback((i + len(chunk)) / total)

ygo_service = YugiohService()
