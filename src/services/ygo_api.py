import requests
import json
import os
import asyncio
from typing import List, Optional, Callable, Dict
from src.core.models import ApiCard
from src.services.image_manager import image_manager
from src.core.persistence import persistence
from nicegui import run

API_URL = "https://db.ygoprodeck.com/api/v7/cardinfo.php"
DATA_DIR = os.path.join(os.getcwd(), "data")
DB_DIR = os.path.join(DATA_DIR, "db")

def parse_cards_data(data: List[dict]) -> List[ApiCard]:
    return [ApiCard(**c) for c in data]

class YugiohService:
    def __init__(self):
        self._cards_cache: Dict[str, List[ApiCard]] = {}
        self._migrate_old_db_files()

    def _migrate_old_db_files(self):
        """Moves existing database files from data/ to data/db/."""
        if not os.path.exists(DB_DIR):
            os.makedirs(DB_DIR)

        # List of potential language files (or just scan)
        for filename in os.listdir(DATA_DIR):
            if filename.startswith("card_db") and filename.endswith(".json"):
                old_path = os.path.join(DATA_DIR, filename)
                new_path = os.path.join(DB_DIR, filename)
                if not os.path.exists(new_path):
                    try:
                        os.rename(old_path, new_path)
                        print(f"Migrated {filename} to {DB_DIR}")
                    except OSError as e:
                        print(f"Error migrating {filename}: {e}")

    def _get_db_file(self, language: str = "en") -> str:
        filename = "card_db.json" if language == "en" else f"card_db_{language}.json"
        return os.path.join(DB_DIR, filename)

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
                parsed_cards = await run.io_bound(parse_cards_data, cards_data)
            except RuntimeError:
                # If run.io_bound fails, run directly
                parsed_cards = parse_cards_data(cards_data)

            self._cards_cache[language] = parsed_cards
            return len(self._cards_cache[language])
        else:
            raise Exception(f"API Error: {response.status_code}")

    def _save_db_file(self, data, language: str = "en"):
        if not os.path.exists(DB_DIR):
            os.makedirs(DB_DIR)

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
                 parsed_cards = await run.io_bound(parse_cards_data, data)
             except RuntimeError:
                 data = await asyncio.to_thread(self._read_db_file, language)
                 parsed_cards = parse_cards_data(data)

             self._cards_cache[language] = parsed_cards

        return self._cards_cache.get(language, [])

    def _read_db_file(self, language: str = "en"):
        db_file = self._get_db_file(language)
        with open(db_file, 'r', encoding='utf-8') as f:
            return json.load(f)

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
    async def get_image_path(self, card_id: int, language: str = "en", high_res: bool = False) -> Optional[str]:
        # Image URLs might be same across languages usually (Japanese art exists but usually handled by alt IDs)
        # We look up card in cache to get image URL
        card = self.get_card(card_id, language)
        if not card or not card.card_images:
            return None

        # Use first image
        url = card.card_images[0].image_url if high_res else card.card_images[0].image_url_small
        return await image_manager.ensure_image(card_id, url, high_res=high_res)

    async def download_all_images(self, progress_callback: Optional[Callable[[float], None]] = None, language: str = "en"):
        """Downloads images for all cards in the database."""
        cards = await self.load_card_database(language)

        url_map = {}
        for card in cards:
             if card.card_images:
                 # Use small image for list views mainly, or large?
                 # Usually cached locally we might want large if space permits, but small is 90% of use.
                 # Actually, UI logic uses small for lists and usually small for card view unless specified.
                 # Let's stick to small as per original code usage
                 url_map[card.id] = card.card_images[0].image_url_small

        await image_manager.download_batch(url_map, progress_callback=progress_callback)

    async def download_all_images_high_res(self, progress_callback: Optional[Callable[[float], None]] = None, language: str = "en"):
        """Downloads high-resolution images for all cards in the database."""
        cards = await self.load_card_database(language)

        url_map = {}
        for card in cards:
             if card.card_images:
                 # Use high res image
                 url_map[card.id] = card.card_images[0].image_url

        await image_manager.download_batch(url_map, progress_callback=progress_callback, high_res=True)

    async def ensure_images_for_cards(self, cards: List[ApiCard]):
        """Ensures images exist for the specified list of cards (using default artwork)."""
        url_map = {}
        for card in cards:
            if card.card_images:
                 url_map[card.id] = card.card_images[0].image_url_small

        await image_manager.download_batch(url_map, concurrency=10)

    async def migrate_collections(self):
        """Updates all user collections to use specific artwork URLs based on set codes."""
        # Load DB to resolve mappings
        cards_db = await self.load_card_database("en")

        # Build a quick lookup map: (name, set_code) -> image_url
        mapping = {}
        for card in cards_db:
            if not card.card_sets:
                continue
            for cset in card.card_sets:
                if cset.image_id:
                    # Find image url
                    img_url = None
                    for img in card.card_images:
                        if img.id == cset.image_id:
                            img_url = img.image_url_small
                            break

                    if img_url:
                        key = (card.name.lower(), cset.set_code.lower())
                        mapping[key] = img_url

        # Iterate collections
        files = persistence.list_collections()
        updated_count = 0

        for filename in files:
            try:
                col = await run.io_bound(persistence.load_collection, filename)
                modified = False
                for card in col.cards:
                    key = (card.name.lower(), card.metadata.set_code.lower())
                    if key in mapping:
                        new_url = mapping[key]
                        if card.image_url != new_url:
                            card.image_url = new_url
                            modified = True

                if modified:
                    await run.io_bound(persistence.save_collection, col, filename)
                    updated_count += 1
            except Exception as e:
                print(f"Error migrating {filename}: {e}")

        return updated_count

    async def fetch_artwork_mappings(self, progress_callback: Optional[Callable[[float], None]] = None, language: str = "en"):
        """
        Iterates over cards with multiple images and fetches specific image IDs by querying the card ID endpoint.
        This allows mapping specific set codes to specific artworks.
        """
        cards = await self.load_card_database(language)

        # Identify candidates: Cards with >1 images
        candidates = [c for c in cards if c.card_images and len(c.card_images) > 1 and c.card_sets]
        total = len(candidates)
        if total == 0:
            return 0

        print(f"Found {total} cards with multiple artworks needing mapping.")

        # Limit concurrency (20 req/1s max, so 10 concurrent with small delays is safe)
        sem = asyncio.Semaphore(10)
        processed_count = 0

        async def process_card(card):
            nonlocal processed_count

            # For cards with multiple images, we want to find which set uses which image.
            # We query the API for each specific image ID (which is treated as a card ID by the API).
            for img in card.card_images:
                async with sem:
                    try:
                        # Rate limit: Wait a bit to ensure we don't burst too hard
                        await asyncio.sleep(0.05)

                        url = "https://db.ygoprodeck.com/api/v7/cardinfo.php"
                        # Querying by ID returns the specific view of the card for that artwork/ID
                        response = await run.io_bound(requests.get, url, params={"id": img.id})

                        if response.status_code == 200:
                            data = response.json()
                            if "data" in data and data["data"]:
                                card_data = data["data"][0]
                                returned_sets = card_data.get("card_sets", [])
                                returned_codes = set(s["set_code"] for s in returned_sets)

                                # Update matching sets in our local object
                                for cset in card.card_sets:
                                    if cset.set_code in returned_codes:
                                        cset.image_id = img.id

                    except Exception as e:
                        print(f"Error mapping artwork for {card.name} ({img.id}): {e}")

            processed_count += 1
            if progress_callback:
                progress_callback(processed_count / total)

        # Process in chunks to avoid creating too many tasks at once
        chunk_size = 20
        for i in range(0, total, chunk_size):
            chunk = candidates[i:i + chunk_size]
            await asyncio.gather(*[process_card(c) for c in chunk])

        # Save back to disk
        try:
             # Use model_dump if available (Pydantic v2), else dict
             # And ensure aliases (like 'def') are used
             if hasattr(cards[0], 'model_dump'):
                 raw_data = [c.model_dump(mode='json', by_alias=True) for c in cards]
             else:
                 # Fallback for older Pydantic
                 raw_data = [c.dict(by_alias=True) for c in cards]

             await run.io_bound(self._save_db_file, raw_data, language)
        except Exception as e:
            print(f"Error saving updated database: {e}")

        return total

ygo_service = YugiohService()
