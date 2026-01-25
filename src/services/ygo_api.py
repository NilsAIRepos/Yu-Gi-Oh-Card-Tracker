import requests
import json
import os
import asyncio
import uuid
import logging
from typing import List, Optional, Callable, Dict, Any, Tuple
from src.core.models import ApiCard, ApiCardSet
from src.services.image_manager import image_manager
from src.core.persistence import persistence
from src.core.utils import generate_variant_id
from src.core.constants import RARITY_RANKING, RARITY_ABBREVIATIONS
from nicegui import run

API_URL = "https://db.ygoprodeck.com/api/v7/cardinfo.php"
SETS_API_URL = "https://db.ygoprodeck.com/api/v7/cardsets.php"
DATA_DIR = os.path.join(os.getcwd(), "data")
DB_DIR = os.path.join(DATA_DIR, "db")
SETS_FILE = os.path.join(DB_DIR, "sets.json")

logger = logging.getLogger(__name__)

def parse_cards_data(data: List[dict]) -> List[ApiCard]:
    return [ApiCard(**c) for c in data]

class YugiohService:
    def __init__(self):
        self._cards_cache: Dict[str, List[ApiCard]] = {}
        self._sets_cache: Dict[str, Dict[str, Any]] = {} # set_code_prefix -> {name, code, image, date, count}
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

            # Filter out cards without sets (unreleased/leaked cards)
            api_cards = [c for c in api_cards if c.card_sets]

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
                               image_id: Optional[int] = None, language: str = "en") -> Optional[ApiCardSet]:
        """
        Adds a new custom variant to a card in the database.
        Generates a unique variant_id using UUID to distinguish it from API-sourced variants.
        Returns the new ApiCardSet if successful, or None if a duplicate exists.
        """
        cards = await self.load_card_database(language)
        card = next((c for c in cards if c.id == card_id), None)

        if not card:
            raise ValueError(f"Card with ID {card_id} not found.")

        # Check for duplicates (same set_code, rarity, and image_id)
        for existing in card.card_sets:
            same_img = (existing.image_id == image_id) or (existing.image_id is None and image_id is None)
            if existing.set_code == set_code and existing.set_rarity == set_rarity and same_img:
                logger.warning(f"Duplicate variant attempt: {set_code} / {set_rarity}")
                return None

        # Resolve set_rarity_code if missing
        if not set_rarity_code:
            abbr = RARITY_ABBREVIATIONS.get(set_rarity)
            if abbr:
                set_rarity_code = f"({abbr})"

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

    async def update_card_variant(self, card_id: int, variant_id: str,
                                  set_code: str, set_rarity: str, image_id: int,
                                  language: str = "en") -> bool:
        """
        Updates an existing card variant in the database.
        """
        cards = await self.load_card_database(language)
        card = next((c for c in cards if c.id == card_id), None)

        if not card:
            logger.error(f"Card {card_id} not found for update.")
            return False

        variant = next((v for v in card.card_sets if v.variant_id == variant_id), None)
        if not variant:
            # If variant not found, assume we are creating a new one (e.g. from "No Set" state)
            logger.info(f"Variant {variant_id} not found for card {card_id}. Creating new variant.")

            new_id = str(uuid.uuid4())
            set_name = "Custom Set"

            # Attempt to resolve set name from global sets
            prefix = set_code.split('-')[0]
            set_info = await self.get_set_info(prefix)
            if set_info:
                set_name = set_info.get('name', set_name)

            # Resolve rarity code
            rarity_code = None
            abbr = RARITY_ABBREVIATIONS.get(set_rarity)
            if abbr:
                rarity_code = f"({abbr})"

            new_set = ApiCardSet(
                variant_id=new_id,
                set_name=set_name,
                set_code=set_code,
                set_rarity=set_rarity,
                set_rarity_code=rarity_code,
                image_id=image_id
            )
            card.card_sets.append(new_set)

            await self.save_card_database(cards, language)
            logger.info(f"Added new variant {new_id} to card {card_id} (update fallback)")
            return True

        # Update fields
        variant.set_code = set_code
        variant.set_rarity = set_rarity
        variant.image_id = image_id

        # Update rarity code
        abbr = RARITY_ABBREVIATIONS.get(set_rarity)
        if abbr:
            variant.set_rarity_code = f"({abbr})"

        # Attempt to refresh set_name from global sets if code changed
        prefix = set_code.split('-')[0]
        set_info = await self.get_set_info(prefix)
        if set_info:
            variant.set_name = set_info.get('name', variant.set_name)

        await self.save_card_database(cards, language)
        logger.info(f"Updated variant {variant_id} for card {card_id}")
        return True

    async def delete_card_variant(self, card_id: int, variant_id: str, language: str = "en") -> bool:
        """
        Deletes a specific variant from a card in the database.
        If the card has no variants left after deletion, the card itself is removed.
        """
        cards = await self.load_card_database(language)
        card = next((c for c in cards if c.id == card_id), None)

        if not card:
            logger.error(f"Card {card_id} not found for deletion.")
            return False

        # Find and remove the variant
        original_count = len(card.card_sets)
        card.card_sets = [v for v in card.card_sets if v.variant_id != variant_id]
        new_count = len(card.card_sets)

        if new_count == original_count:
            logger.warning(f"Variant {variant_id} not found in card {card_id}.")
            return False

        # If no variants left, remove the card entirely to prevent "NO SET" entries
        if new_count == 0:
            cards = [c for c in cards if c.id != card_id]
            logger.info(f"Card {card_id} removed because it has no variants left.")

        await self.save_card_database(cards, language)
        logger.info(f"Deleted variant {variant_id} from card {card_id}")
        return True

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

    # --- Global Sets Logic ---

    async def fetch_all_sets(self, force_refresh=False):
        """Fetches the global set list from the API and caches it."""
        if self._sets_cache and not force_refresh:
            return

        # Try load from disk if not forcing refresh
        if not force_refresh and os.path.exists(SETS_FILE):
             try:
                 with open(SETS_FILE, 'r', encoding='utf-8') as f:
                     data = json.load(f)
                     if data:
                         # Validate structure - check if values are strings (old format) or dicts (new format)
                         first_val = next(iter(data.values())) if data else None
                         if isinstance(first_val, str):
                             logger.info("Old sets cache format detected. Migrating...")
                             force_refresh = True # Force fetch to update
                         else:
                            self._sets_cache = data
                            return
             except Exception as e:
                 logger.error(f"Error reading sets file: {e}")

        logger.info("Fetching global card sets from API...")
        try:
            response = await run.io_bound(requests.get, SETS_API_URL)
        except RuntimeError:
            response = await asyncio.to_thread(requests.get, SETS_API_URL)

        if response.status_code == 200:
            sets_data = response.json()
            # Map set_code_prefix -> {name, code, image, date, count}
            # We track num_of_cards to prioritize larger sets when prefixes collide (e.g. SDY)
            temp_cache = {}

            for s in sets_data:
                code = s.get("set_code")
                name = s.get("set_name")
                count = s.get("num_of_cards", 0)
                image = s.get("set_image")
                date = s.get("tcg_date")

                if code and name:
                    # Logic to handle prefixes. Note that set_code might be full code (unlikely in this API) or prefix.
                    # This API usually returns prefixes like "SDY", "LOB", "MP19".
                    # However, local cards might reference "MP19-EN001".
                    # We store by the prefix provided by the API.

                    entry = {
                        "name": name,
                        "code": code,
                        "image": image,
                        "date": date,
                        "count": count
                    }

                    if code not in temp_cache:
                        temp_cache[code] = entry
                    else:
                        # Overwrite if current set has more cards
                        if count > temp_cache[code]["count"]:
                            temp_cache[code] = entry

            # Finalize cache
            self._sets_cache = temp_cache

            # Save to disk
            try:
                if not os.path.exists(DB_DIR): os.makedirs(DB_DIR)
                with open(SETS_FILE, 'w', encoding='utf-8') as f:
                    json.dump(self._sets_cache, f)
            except Exception as e:
                logger.error(f"Error saving sets file: {e}")
        else:
            logger.error(f"Failed to fetch sets: {response.status_code}")

    async def get_set_name_by_code(self, set_code: str) -> Optional[str]:
        """
        Resolves a full set code (e.g. 'BP02-DE137' or 'BP02') to its set name (e.g. 'Battle Pack 2').
        Uses the global sets cache.
        """
        await self.fetch_all_sets()

        # Extract prefix
        prefix = set_code.split('-')[0]
        entry = self._sets_cache.get(prefix)
        if isinstance(entry, dict):
            return entry.get("name")
        elif isinstance(entry, str): # Fallback/Legacy
            return entry
        return None

    async def get_set_info(self, set_code: str) -> Optional[Dict[str, Any]]:
        """Retrieves full metadata for a set by code prefix."""
        await self.fetch_all_sets()
        prefix = set_code.split('-')[0]
        entry = self._sets_cache.get(prefix)
        if isinstance(entry, dict):
            return entry
        return None

    async def get_all_sets_info(self) -> List[Dict[str, Any]]:
        """Returns list of all set metadatas."""
        await self.fetch_all_sets()
        return list(self._sets_cache.values())

    async def get_set_cards(self, set_code: str, language: str = "en") -> List[ApiCard]:
        """
        Returns a list of ApiCard objects that belong to the specified set code/prefix.
        Cards are sorted by highest rarity using the global RARITY_RANKING.
        """
        cards = await self.load_card_database(language)
        matching_cards = []

        prefix = set_code.split('-')[0].lower()

        for card in cards:
            if not card.card_sets:
                continue

            # Check if any set in card.card_sets matches
            belongs = False
            best_rarity_index = 999

            for cs in card.card_sets:
                cs_prefix = cs.set_code.split('-')[0].lower()
                if cs_prefix == prefix:
                    belongs = True
                    # Determine rarity priority
                    r = cs.set_rarity
                    try:
                        idx = RARITY_RANKING.index(r)
                    except ValueError:
                        idx = 999
                    if idx < best_rarity_index:
                        best_rarity_index = idx

            if belongs:
                # Store tuple for sorting
                matching_cards.append((card, best_rarity_index))

        # Sort by best rarity index
        matching_cards.sort(key=lambda x: x[1])

        return [x[0] for x in matching_cards]

    async def download_set_image(self, set_code: str, url: str) -> Optional[str]:
        """Downloads/Caches set image."""
        return await image_manager.ensure_set_image(set_code, url)

    async def download_set_statistics_and_images(self, progress_callback: Optional[Callable[[float], None]] = None):
        """
        Downloads set information (metadata) and all associated set images.
        """
        # 1. Update Set Information
        logger.info("Updating set information...")
        if progress_callback:
            progress_callback(0.0)

        await self.fetch_all_sets(force_refresh=True)

        # 2. Download Images
        if not self._sets_cache:
            if progress_callback:
                progress_callback(1.0)
            return

        sets = list(self._sets_cache.values())
        total = len(sets)

        logger.info(f"Downloading images for {total} sets...")

        semaphore = asyncio.Semaphore(10) # Limit concurrency
        completed = 0

        async def _download_task(set_info):
            nonlocal completed
            async with semaphore:
                code = set_info.get("code")
                image_url = set_info.get("image")
                if code and image_url:
                    await self.download_set_image(code, image_url)

                completed += 1
                if progress_callback:
                    progress_callback(completed / total)

        # Create tasks
        tasks = [_download_task(s) for s in sets]
        await asyncio.gather(*tasks)

        logger.info("Set statistics and images download complete.")

    async def get_real_set_counts(self, language: str = "en") -> Dict[str, int]:
        """
        Calculates the real number of unique cards per set based on the local card database.
        Returns a dict mapping set_code_prefix -> unique card count.
        """
        cards = await self.load_card_database(language)
        counts = {}

        for card in cards:
            if not card.card_sets:
                continue

            # Identify unique sets this card belongs to
            seen_prefixes = set()
            for cs in card.card_sets:
                # Normalize prefix
                parts = cs.set_code.split('-')
                if not parts: continue
                prefix = parts[0].upper() # Normalize to Upper for matching

                if prefix not in seen_prefixes:
                    seen_prefixes.add(prefix)
                    counts[prefix] = counts.get(prefix, 0) + 1

        return counts

    async def bulk_update_set_prefix(self, old_prefix: str, new_prefix: str, language: str = "en") -> int:
        """
        Updates the set prefix for all cards in the specified set.
        Preserves the suffix (region + number).
        Returns the number of variants updated.
        """
        cards = await self.load_card_database(language)
        updated_count = 0

        # Normalize prefixes for comparison
        old_p = old_prefix.strip()
        new_p = new_prefix.strip()

        if old_p == new_p:
            return 0

        for card in cards:
            if not card.card_sets:
                continue

            card_updated = False
            for s in card.card_sets:
                parts = s.set_code.split('-')
                if parts and parts[0] == old_p:
                    # Construct new code
                    parts[0] = new_p
                    new_code = "-".join(parts)
                    s.set_code = new_code
                    updated_count += 1
                    card_updated = True

        if updated_count > 0:
            await self.save_card_database(cards, language)

        logger.info(f"Bulk updated prefix from {old_p} to {new_p}. Updated {updated_count} variants.")
        return updated_count

    async def bulk_add_rarity_to_set(self, set_prefix: str, rarity: str, language: str = "en") -> int:
        """
        Adds a new variant with the specified rarity to all cards belonging to the set.
        """
        cards = await self.load_card_database(language)
        added_count = 0
        target_prefix = set_prefix.strip()

        # Resolve rarity code
        rarity_code = None
        abbr = RARITY_ABBREVIATIONS.get(rarity)
        if abbr:
            rarity_code = f"({abbr})"

        for card in cards:
            if not card.card_sets:
                continue

            # Identify unique set codes for this prefix (handling Alt Arts / multiple codes)
            codes_in_set = {} # code -> set_name

            for s in card.card_sets:
                parts = s.set_code.split('-')
                if parts and parts[0] == target_prefix:
                    codes_in_set[s.set_code] = s.set_name

            for code, name in codes_in_set.items():
                # Check if this specific code+rarity exists
                exists = False
                for s in card.card_sets:
                    if s.set_code == code and s.set_rarity == rarity:
                        exists = True
                        break

                if not exists:
                    # Create new variant
                    new_id = str(uuid.uuid4())

                    # Try to use image_id from an existing variant of this code
                    ref_img_id = None
                    for s in card.card_sets:
                        if s.set_code == code:
                            ref_img_id = s.image_id
                            break
                    if ref_img_id is None:
                         ref_img_id = card.card_images[0].id if card.card_images else None

                    new_set = ApiCardSet(
                        variant_id=new_id,
                        set_name=name,
                        set_code=code,
                        set_rarity=rarity,
                        set_rarity_code=rarity_code,
                        set_price="0.00",
                        image_id=ref_img_id
                    )
                    card.card_sets.append(new_set)
                    added_count += 1

        if added_count > 0:
            await self.save_card_database(cards, language)

        logger.info(f"Bulk added rarity {rarity} to set {target_prefix}. Added {added_count} variants.")
        return added_count

    async def bulk_delete_set(self, set_prefix: str, language: str = "en") -> int:
        """
        Deletes all variants belonging to the specified set prefix.
        """
        cards = await self.load_card_database(language)
        deleted_count = 0
        target_prefix = set_prefix.strip()

        cards_to_remove = []

        for card in cards:
            if not card.card_sets:
                continue

            original_len = len(card.card_sets)
            # Keep variants that DO NOT match the prefix
            card.card_sets = [s for s in card.card_sets if s.set_code.split('-')[0] != target_prefix]

            removed = original_len - len(card.card_sets)
            deleted_count += removed

            if len(card.card_sets) == 0:
                cards_to_remove.append(card.id)

        if deleted_count > 0:
            if cards_to_remove:
                remove_set = set(cards_to_remove)
                new_cards = [c for c in cards if c.id not in remove_set]
                await self.save_card_database(new_cards, language)
            else:
                await self.save_card_database(cards, language)

        logger.info(f"Bulk deleted set {target_prefix}. Removed {deleted_count} variants.")
        return deleted_count

ygo_service = YugiohService()
