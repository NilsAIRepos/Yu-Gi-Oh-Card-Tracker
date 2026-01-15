import asyncio
import re
from typing import Optional, Tuple
from nicegui import run
from src.core.persistence import persistence
from src.services.ygo_api import ygo_service
from src.core.utils import transform_set_code
from src.core.config import config_manager

def parse_set_code(code: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Parses a set code into (Prefix, Number).
    Handles both New (LOB-EN001) and Old (SDY-006) formats.
    Returns (None, None) if parsing fails.
    """
    # Try New Format first: Prefix-RegionNum (LOB-EN001)
    # We want to capture the Prefix (LOB) and the Number (001).
    # Region (EN) is ignored for matching purposes.
    match_new = re.match(r'^([A-Za-z0-9]+)-([A-Za-z]{2})(\d+)$', code)
    if match_new:
        return match_new.group(1).upper(), match_new.group(3)

    # Try Old Format: Prefix-Num (SDY-006)
    match_old = re.match(r'^([A-Za-z0-9]+)-(\d+)$', code)
    if match_old:
        return match_old.group(1).upper(), match_old.group(2)

    return None, None

async def fix_legacy_set_codes() -> int:
    """
    Iterates through all collections and corrects set codes based on the rule:
    - If the ORIGINAL (English) set code has a region code (e.g. MACR-EN036),
      the localized code should have the target language region (e.g. MACR-DE036).
    - If the ORIGINAL set code has NO region code (e.g. SDY-006),
      the localized code should be identical to the original (e.g. SDY-006).

    Uses card.metadata.language to determine target language.
    Returns the number of cards updated.
    """
    print("Starting Legacy Set Code Fix...")

    # 1. Load Databases
    # English DB is the Source of Truth for "Original Set Codes"
    print("Loading English DB...")
    db_en = await ygo_service.load_card_database("en")
    id_to_card_en = {c.id: c for c in db_en}
    name_to_id_en = {c.name.lower(): c.id for c in db_en}

    # Current Language DB (to resolve IDs from localized names)
    current_lang = config_manager.get_language()
    name_to_id_local = {}
    if current_lang != 'en':
        print(f"Loading {current_lang} DB...")
        db_local = await ygo_service.load_card_database(current_lang)
        name_to_id_local = {c.name.lower(): c.id for c in db_local}

    updated_count = 0
    collections = persistence.list_collections()

    for col_file in collections:
        try:
            try:
                col = await run.io_bound(persistence.load_collection, col_file)
            except RuntimeError:
                col = await asyncio.to_thread(persistence.load_collection, col_file)

            modified = False

            for card in col.cards:
                # Resolve API ID
                api_id = None
                c_name = card.name.lower()

                # Try local DB first
                if c_name in name_to_id_local:
                    api_id = name_to_id_local[c_name]
                # Try EN DB fallback
                elif c_name in name_to_id_en:
                    api_id = name_to_id_en[c_name]

                if not api_id or api_id not in id_to_card_en:
                    # Could not identify card in English DB
                    continue

                api_card = id_to_card_en[api_id]

                # Analyze Set Code
                current_code = card.metadata.set_code
                target_lang = card.metadata.language or "EN"

                # Parse current code to find matching set in API
                curr_prefix, curr_num = parse_set_code(current_code)
                if not curr_prefix:
                    continue

                # Find matching Original Set
                matched_original_code = None

                if api_card.card_sets:
                    for cset in api_card.card_sets:
                        api_code = cset.set_code
                        api_prefix, api_num = parse_set_code(api_code)

                        if api_prefix == curr_prefix and api_num == curr_num:
                            matched_original_code = api_code
                            break

                if matched_original_code:
                    # Determine Correct Code
                    # Check if Original is New Style (has region)
                    is_new_style = bool(re.match(r'^([A-Za-z0-9]+)-([A-Za-z]{2})(\d+)$', matched_original_code))

                    correct_code = matched_original_code
                    if is_new_style:
                        # Apply transformation
                        correct_code = transform_set_code(matched_original_code, target_lang)
                    else:
                        # Keep original (Old Style)
                        correct_code = matched_original_code

                    # Update if different
                    if current_code != correct_code:
                        print(f"[{col.name}] Fixed {card.name}: {current_code} -> {correct_code}")
                        card.metadata.set_code = correct_code
                        modified = True
                        updated_count += 1

            if modified:
                try:
                    await run.io_bound(persistence.save_collection, col, col_file)
                except RuntimeError:
                    await asyncio.to_thread(persistence.save_collection, col, col_file)

        except Exception as e:
            print(f"Error processing collection {col_file}: {e}")

    print(f"Legacy Set Code Fix Complete. Updated {updated_count} cards.")
    return updated_count
