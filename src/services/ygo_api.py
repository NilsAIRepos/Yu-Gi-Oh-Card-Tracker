import requests
import json
import os
import asyncio
from typing import List, Optional, Callable, Dict
from src.core.models import ApiCard
from src.services.image_manager import image_manager
from nicegui import run

API_URL = "https://db.ygoprodeck.com/api/v7/cardinfo.php"
DATA_DIR = os.path.join(os.getcwd(), "data")

class YugiohService:
    def __init__(self):
        self._cards_cache: Dict[str, List[ApiCard]] = {}

    def _get_db_file(self, language: str = "en") -> str:
        filename = "card_db.json" if language == "en" else f"card_db_{language}.json"
        return os.path.join(DATA_DIR, filename)

    async def fetch_card_database(self, language: str = "en") -> int:
        """Downloads the full database from the API. Returns count of cards."""
        params = {}
        if language != "en":
            params["language"] = language

        try:
            response = await run.io_bound(requests.get, API_URL, params=params)
        except RuntimeError:
            # Fallback for testing environments without event loop integration
            response = await asyncio.to_thread(requests.get, API_URL, params=params)

        if response.status_code == 200:
            data = response.json()
            # The API returns {"data": [...] }
            cards_data = data.get("data", [])

            # Save raw JSON first
            try:
                await run.io_bound(self._save_db_file, cards_data, language)
            except RuntimeError:
                await asyncio.to_thread(self._save_db_file, cards_data, language)

            # Update cache (parse in thread)
            try:
                parsed_cards = await run.cpu_bound(self._parse_cards, cards_data)
            except RuntimeError:
                # If run.cpu_bound fails (no process pool), run directly (slow but works for test)
                parsed_cards = self._parse_cards(cards_data)

            self._cards_cache[language] = parsed_cards
            return len(self._cards_cache[language])
        else:
            raise Exception(f"API Error: {response.status_code}")

    def _save_db_file(self, data, language: str = "en"):
        if not os.path.exists(DATA_DIR):
            os.makedirs(DATA_DIR)

        filepath = self._get_db_file(language)
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f)

    async def load_card_database(self, language: str = "en") -> List[ApiCard]:
        """Loads the database from disk. If missing, fetches it."""
        if language in self._cards_cache:
            return self._cards_cache[language]

        db_file = self._get_db_file(language)

        if not os.path.exists(db_file):
            await self.fetch_card_database(language)

        if language not in self._cards_cache and os.path.exists(db_file):
             # Read file
             try:
                 data = await run.io_bound(self._read_db_file, language)
                 parsed_cards = await run.cpu_bound(self._parse_cards, data)
             except RuntimeError:
                 data = await asyncio.to_thread(self._read_db_file, language)
                 parsed_cards = self._parse_cards(data)

             self._cards_cache[language] = parsed_cards

        return self._cards_cache.get(language, [])

    def _read_db_file(self, language: str = "en"):
        db_file = self._get_db_file(language)
        with open(db_file, 'r', encoding='utf-8') as f:
            return json.load(f)

    def _parse_cards(self, data: List[dict]) -> List[ApiCard]:
        return [ApiCard(**c) for c in data]

    def get_card(self, card_id: int, language: str = "en") -> Optional[ApiCard]:
        cards = self._cards_cache.get(language, [])
        for c in cards:
            if c.id == card_id:
                return c
        return None

    def search_by_name(self, name: str, language: str = "en") -> Optional[ApiCard]:
        cards = self._cards_cache.get(language, [])
        for c in cards:
            if c.name.lower() == name.lower():
                return c
        return None

    # Forwarding image manager calls
    async def get_image_path(self, card_id: int, language: str = "en") -> Optional[str]:
        # Image URLs might be same across languages usually (Japanese art exists but usually handled by alt IDs)
        # We look up card in cache to get image URL
        card = self.get_card(card_id, language)
        if not card or not card.card_images:
            return None

        # Use first image
        url = card.card_images[0].image_url
        return await image_manager.ensure_image(card_id, url)

    async def download_all_images(self, progress_callback: Optional[Callable[[float], None]] = None, language: str = "en"):
        """Downloads images for all cards in the database."""
        cards = await self.load_card_database(language)
        total = len(cards)
        chunk_size = 20

        for i in range(0, total, chunk_size):
            chunk = cards[i:i + chunk_size]
            tasks = []
            for card in chunk:
                 if card.card_images:
                     url = card.card_images[0].image_url_small
                     tasks.append(image_manager.ensure_image(card.id, url))

            await asyncio.gather(*tasks)

            if progress_callback:
                progress_callback((i + len(chunk)) / total)

ygo_service = YugiohService()
