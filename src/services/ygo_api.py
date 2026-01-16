import requests
import json
import os
import asyncio
import uuid
import logging
from typing import List, Optional, Callable, Dict
from src.core.models import ApiCard, ApiCardSet
from src.services.image_manager import image_manager
from src.core.persistence import persistence
from src.core.utils import generate_variant_id
from nicegui import run

API_URL = "https://db.ygoprodeck.com/api/v7/cardinfo.php"
DATA_DIR = os.path.join(os.getcwd(), "data")
DB_DIR = os.path.join(DATA_DIR, "db")

logger = logging.getLogger(__name__)

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
                        logger.info(f"Migrated {filename} to {DB_DIR}")
                    except OSError as e:
                        logger.error(f"Error migrating {filename}: {e}")

    def _get_db_file(self, language: str = "en") -> str:
        filename = "card_db.json" if language == "en" else f"card_db_{language}.json"
        return os.path.join(DB_DIR, filename)

    async def fetch_card_database(self, language: str = "en") -> int:
        """Downloads the full database from the API and merges it with local data."""
        logger.info(f"Fetching card database for language: {language}")
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
            cards_data = data.get("data", [])
            logger.info(f"Fetched {len(cards_data)} cards from API.")

            # Parse new API data
            try:
                api_cards = await run.io_bound(parse_cards_data, cards_data)
            except RuntimeError:
                api_cards = parse_cards_data(cards_data)

            # Load existing local data to merge
            local_cards = []
            try:
                # Attempt to read file directly to avoid recursive fetch
                if hasattr(run, 'io_bound'):
                     local_raw = await run.io_bound(self._read_db_file, language)
                else:
                     local_raw = self._read_db_file(language)

                if local_raw:
                    try:
                        local_cards = await run.io_bound(parse_cards_data, local_raw) if hasattr(run, 'io_bound') else parse_cards_data(local_raw)
                    except RuntimeError:
                        local_cards = parse_cards_data(local_raw)
            except (FileNotFoundError, json.JSONDecodeError):
                logger.info("No valid local database found, starting fresh.")

            # Merge
            try:
                 merged_cards = await run.io_bound(self._merge_database_data, local_cards, api_cards)
            except RuntimeError:
                 merged_cards = self._merge_database_data(local_cards, api_cards)

            # Save merged data
            await self.save_card_database(merged_cards, language)

            return len(self._cards_cache[language])
        else:
            logger.error(f"API Error: {response.status_code}")
            raise Exception(f"API Error: {response.status_code}")

    def _merge_database_data(self, local_cards: List[ApiCard], api_cards: List[ApiCard]) -> List[ApiCard]:
        """Merges API data into local data, preserving custom variants and IDs."""

        # PRE-CLEANUP: Ensure all local sets have valid IDs before merging.
        # This prevents duplicate/missing/empty IDs from causing drops during deduplication.
        for l_card in local_cards:
            # Fallback default image id
            l_default_img = l_card.card_images[0].id if l_card.card_images else None

            for s in l_card.card_sets:
                # 1. Ensure image_id is present
                if s.image_id is None:
                    s.image_id = l_default_img

                # 2. Ensure variant_id is present and valid (not None or empty string)
                if not s.variant_id:
                    s.variant_id = generate_variant_id(
                        l_card.id, s.set_code, s.set_rarity, s.image_id
                    )

        local_map = {c.id: c for c in local_cards}
        merged_list = []

        for api_card in api_cards:
            local_card = local_map.get(api_card.id)

            # Determine default image ID for this card (fallback if specific mapping missing)
            default_image_id = None
            if api_card.card_images:
                default_image_id = api_card.card_images[0].id

            if local_card:
                # Use API card as base for stats/text, but merge sets
                merged_card = api_card.model_copy() if hasattr(api_card, 'model_copy') else api_card.copy()

                # Map local sets by (code, rarity) for matching
                # Note: We group by key because there might be multiple (e.g. alt arts)
                local_sets_map = {}
                for s in local_card.card_sets:
                    key = (s.set_code, s.set_rarity)
                    if key not in local_sets_map:
                        local_sets_map[key] = []
                    local_sets_map[key].append(s)

                merged_sets = []
                processed_local_sets = set() # Track by variant_id

                for api_set in api_card.card_sets:
                    key = (api_set.set_code, api_set.set_rarity)
                    if key in local_sets_map:
                        # Match found. Update all matching local sets with fresh price/info
                        # but keep their IDs and image_ids
                        for local_s in local_sets_map[key]:
                            # This check is now safe because we pre-cleaned variant_id
                            if local_s.variant_id in processed_local_sets:
                                continue

                            # Update mutable fields from API
                            local_s.set_price = api_set.set_price

                            # (Redundant safety check, already handled in pre-cleanup but harmless to keep if logic changes)
                            if local_s.image_id is None:
                                local_s.image_id = default_image_id
                            if not local_s.variant_id:
                                local_s.variant_id = generate_variant_id(
                                    api_card.id, local_s.set_code, local_s.set_rarity, local_s.image_id
                                )

                            # Keep local_s in merged list
                            merged_sets.append(local_s)
                            # Mark as processed
                            processed_local_sets.add(local_s.variant_id)
                    else:
                        # New set from API
                        if api_set.image_id is None:
                            api_set.image_id = default_image_id

                        api_set.variant_id = generate_variant_id(
                            api_card.id, api_set.set_code, api_set.set_rarity, api_set.image_id
                        )
                        merged_sets.append(api_set)

                # Add remaining local sets (custom or those not returned by API currently)
                for sets in local_sets_map.values():
                    for s in sets:
                        if s.variant_id not in processed_local_sets:
                            # Ensure IDs for local orphans too
                            if s.image_id is None:
                                s.image_id = default_image_id
                            if s.variant_id is None:
                                s.variant_id = generate_variant_id(
                                    api_card.id, s.set_code, s.set_rarity, s.image_id
                                )
                            merged_sets.append(s)

                merged_card.card_sets = merged_sets
                merged_list.append(merged_card)
            else:
                # New card entirely
                for s in api_card.card_sets:
                    if s.image_id is None:
                        s.image_id = default_image_id

                    s.variant_id = generate_variant_id(
                        api_card.id, s.set_code, s.set_rarity, s.image_id
                    )
                merged_list.append(api_card)

        # We generally do not keep cards that are in Local but NOT in API,
        # as that usually means they were removed/invalid.
        # (Unless we support custom cards which have IDs not in API range?)
        # For now, we stick to API as master list for existence of cards.

        return merged_list

    async def save_card_database(self, cards: List[ApiCard], language: str = "en"):
        """Saves the card database to disk."""
        self._cards_cache[language] = cards

        if not cards:
            return

        # Serialize
        if hasattr(cards[0], 'model_dump'):
             raw_data = [c.model_dump(mode='json', by_alias=True) for c in cards]
        else:
             raw_data = [c.dict(by_alias=True) for c in cards]

        try:
            await run.io_bound(self._save_db_file, raw_data, language)
        except RuntimeError:
            await asyncio.to_thread(self._save_db_file, raw_data, language)

    def _save_db_file(self, data, language: str = "en"):
        if not os.path.exists(DB_DIR):
            os.makedirs(DB_DIR)

        filepath = self._get_db_file(language)
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f)

    async def add_card_variant(self, card_id: int, set_name: str, set_code: str, set_rarity: str,
                               set_rarity_code: Optional[str] = None, set_price: Optional[str] = None,
                               image_id: Optional[int] = None, language: str = "en") -> ApiCardSet:
        """
        Adds a new custom variant to a card in the database.
        Generates a unique variant_id using UUID to distinguish it from API-sourced variants.
        """
        cards = await self.load_card_database(language)
        card = next((c for c in cards if c.id == card_id), None)

        if not card:
            raise ValueError(f"Card with ID {card_id} not found.")

        new_variant_id = str(uuid.uuid4())

        new_set = ApiCardSet(
            variant_id=new_variant_id,
            set_name=set_name,
            set_code=set_code,
            set_rarity=set_rarity,
            set_rarity_code=set_rarity_code,
            set_price=set_price,
            image_id=image_id
        )

        card.card_sets.append(new_set)

        # Save updated database
        await self.save_card_database(cards, language)
        logger.info(f"Added new variant {new_variant_id} to card {card_id}")

        return new_set

    async def load_card_database(self, language: str = "en") -> List[ApiCard]:
        """Loads the database from disk. If missing, fetches it."""
        if language in self._cards_cache:
            return self._cards_cache[language]

        db_file = self._get_db_file(language)

        if not os.path.exists(db_file):
            logger.info(f"Database file not found: {db_file}. Fetching from API.")
            await self.fetch_card_database(language)

        if language not in self._cards_cache and os.path.exists(db_file):
             logger.info(f"Loading database from disk: {db_file}")
             # Read file
             try:
                 data = await run.io_bound(self._read_db_file, language)
                 parsed_cards = await run.io_bound(parse_cards_data, data)
             except RuntimeError:
                 data = await asyncio.to_thread(self._read_db_file, language)
                 parsed_cards = parse_cards_data(data)

             self._cards_cache[language] = parsed_cards
             logger.info(f"Loaded {len(parsed_cards)} cards.")

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

        logger.info(f"Queueing download for {len(url_map)} images.")
        await image_manager.download_batch(url_map, progress_callback=progress_callback)

    async def download_all_images_high_res(self, progress_callback: Optional[Callable[[float], None]] = None, language: str = "en"):
        """Downloads high-resolution images for all cards in the database."""
        cards = await self.load_card_database(language)

        url_map = {}
        for card in cards:
             if card.card_images:
                 # Use high res image
                 url_map[card.id] = card.card_images[0].image_url

        logger.info(f"Queueing download for {len(url_map)} high-res images.")
        await image_manager.download_batch(url_map, progress_callback=progress_callback, high_res=True)

    async def ensure_images_for_cards(self, cards: List[ApiCard]):
        """Ensures images exist for the specified list of cards (using default artwork)."""
        url_map = {}
        for card in cards:
            if card.card_images:
                 url_map[card.id] = card.card_images[0].image_url_small

        await image_manager.download_batch(url_map, concurrency=10)


ygo_service = YugiohService()
