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
        local_map = {c.id: c for c in local_cards}
        merged_list = []

        for api_card in api_cards:
            local_card = local_map.get(api_card.id)
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
                processed_local_sets = set() # Track by object id or variant_id

                for api_set in api_card.card_sets:
                    key = (api_set.set_code, api_set.set_rarity)
                    if key in local_sets_map:
                        # Match found. Update all matching local sets with fresh price/info
                        # but keep their IDs and image_ids
                        for local_s in local_sets_map[key]:
                            if local_s.variant_id in processed_local_sets:
                                continue

                            # Update mutable fields from API
                            local_s.set_price = api_set.set_price
                            # Keep local_s in merged list
                            merged_sets.append(local_s)
                            # Mark as processed
                            processed_local_sets.add(local_s.variant_id)
                    else:
                        # New set from API
                        api_set.variant_id = generate_variant_id(
                            api_card.id, api_set.set_code, api_set.set_rarity, api_set.image_id
                        )
                        merged_sets.append(api_set)

                # Add remaining local sets (custom or those not returned by API currently)
                for sets in local_sets_map.values():
                    for s in sets:
                        if s.variant_id not in processed_local_sets:
                            merged_sets.append(s)

                merged_card.card_sets = merged_sets
                merged_list.append(merged_card)
            else:
                # New card entirely
                for s in api_card.card_sets:
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

    async def migrate_collections(self):
        """Updates all user collections to use specific artwork URLs based on set codes."""
        logger.info("Starting collection migration...")
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
                    logger.info(f"Updated collection: {filename}")
            except Exception as e:
                logger.error(f"Error migrating {filename}: {e}")

        logger.info(f"Migration complete. Updated {updated_count} collections.")
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

        logger.info(f"Found {total} cards with multiple artworks needing mapping.")

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
                        logger.error(f"Error mapping artwork for {card.name} ({img.id}): {e}")

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
             await self.save_card_database(cards, language)
             logger.info("Saved updated database with artwork mappings.")
        except Exception as e:
            logger.error(f"Error saving updated database: {e}")

        return total

ygo_service = YugiohService()
