from nicegui import ui, events
import json
import logging
import asyncio
import uuid
import difflib
from typing import Optional, List, Dict, Any
from dataclasses import dataclass

import re
from src.core.persistence import persistence
from src.core.models import Collection, ApiCard, ApiCardSet
from src.core.utils import LANGUAGE_TO_LEGACY_REGION_MAP, normalize_set_code, is_set_code_compatible, get_legacy_code
from src.core.constants import RARITY_ABBREVIATIONS
from src.services.ygo_api import ygo_service
from src.services.collection_editor import CollectionEditor
from src.services.cardmarket_parser import CardmarketParser, ParsedRow

logger = logging.getLogger(__name__)

@dataclass
class PendingChange:
    api_card: ApiCard
    set_code: str
    rarity: str
    quantity: int
    condition: str
    language: str
    first_edition: bool
    image_id: Optional[int] = None
    source_row: Any = None # Original row data for debugging/logging

class UnifiedImportController:
    def __init__(self):
        self.collections: List[str] = persistence.list_collections()
        self.selected_collection: Optional[str] = None

        self.import_type: str = 'JSON' # 'JSON' or 'CARDMARKET'
        self.import_mode: str = 'ADD'  # 'ADD' or 'SUBTRACT'

        # State for Re-scan
        self.last_uploaded_content: Optional[bytes] = None
        self.last_uploaded_filename: str = ""

        # Staging
        self.pending_changes: List[PendingChange] = []
        self.successful_imports: List[str] = []
        self.import_failures: List[str] = [] # Failures during apply phase

        # Cardmarket specific
        self.ambiguous_rows: List[Dict[str, Any]] = [] # {row, matches, selected_index}
        self.failed_rows: List[ParsedRow] = []

        self.undo_stack: List[Dict[str, Any]] = []
        self.db_lookup: Dict[str, List[Dict[str, Any]]] = {}

        # UI References
        self.ui_container = None
        self.status_container = None
        self.import_btn = None
        self.undo_btn = None
        self.collection_select = None

        # Load persisted selection
        saved_state = persistence.load_ui_state()
        last_col = saved_state.get('import_last_collection')
        if last_col and last_col in self.collections:
            self.selected_collection = last_col

    def on_collection_change(self, e):
        self.selected_collection = e.value
        persistence.save_ui_state({'import_last_collection': e.value})

    def refresh_collections(self):
        self.collections = persistence.list_collections()
        if self.collection_select:
            self.collection_select.options = self.collections
            self.collection_select.update()

    async def create_new_collection(self, name: str):
        if not name:
            ui.notify("Collection name cannot be empty", type='warning')
            return

        filename = f"{name}.json"
        if filename in self.collections:
             ui.notify("Collection already exists", type='negative')
             return

        new_collection = Collection(name=name)
        persistence.save_collection(new_collection, filename)

        self.refresh_collections()
        self.selected_collection = filename
        if self.collection_select:
            self.collection_select.value = filename
            self.collection_select.update()

        ui.notify(f"Created collection: {name}", type='positive')

    async def handle_upload(self, e: events.UploadEventArguments):
        ui.notify("Processing file...", type='info')

        # Robust File Extraction (Fixes AttributeError on NiceGUI 3.5+)
        content = None
        filename = "unknown"

        try:
            if hasattr(e, 'file'): # NiceGUI 1.4.15+ / 2.0+
                content = await e.file.read()
                filename = e.file.name
            elif hasattr(e, 'content'): # Legacy
                content = e.content.read()
                filename = e.name

            # Double check if read returned a coroutine (some versions might)
            if asyncio.iscoroutine(content):
                content = await content

            if not content:
                raise ValueError("Empty file content")

            # Save for re-scan
            self.last_uploaded_content = content
            self.last_uploaded_filename = filename

            await self.process_current_file()

        except Exception as ex:
            logger.error(f"Upload Error: {ex}")
            ui.notify(f"Error reading file: {ex}", type='negative')

    async def process_current_file(self):
        if not self.last_uploaded_content:
            return

        # Clear previous state
        self.pending_changes = []
        self.ambiguous_rows = []
        self.failed_rows = []
        self.import_failures = []
        self.refresh_status_ui()

        # Ensure DB is loaded
        await ygo_service.load_card_database()

        # Dispatch
        try:
            if self.import_type == 'JSON':
                await self.process_json(self.last_uploaded_content)
            else:
                await self.process_cardmarket(self.last_uploaded_content, self.last_uploaded_filename)
        except Exception as e:
             ui.notify(f"Processing Error: {e}", type='negative')

        self.refresh_status_ui()

    async def process_json(self, content: bytes):
        try:
            json_str = content.decode('utf-8')
            data = json.loads(json_str)
        except Exception as ex:
            ui.notify(f"Invalid JSON: {ex}", type='negative')
            return

        if "cards" not in data:
            ui.notify("Invalid JSON format: missing 'cards' list", type='negative')
            return

        count = 0
        for card_data in data.get("cards", []):
            card_id = card_data.get("card_id")
            if not card_id: continue

            api_card = ygo_service.get_card(card_id)
            if not api_card:
                logger.warning(f"Card {card_id} not found in DB. Skipping.")
                continue

            default_image_id = api_card.card_images[0].id if api_card.card_images else None

            for variant_data in card_data.get("variants", []):
                set_code = variant_data.get("set_code")
                rarity = variant_data.get("rarity")
                image_id = variant_data.get("image_id", default_image_id)

                if not set_code or not rarity: continue

                for entry_data in variant_data.get("entries", []):
                    qty = entry_data.get("quantity", 0)
                    if qty <= 0: continue

                    self.pending_changes.append(PendingChange(
                        api_card=api_card,
                        set_code=set_code,
                        rarity=rarity,
                        quantity=qty,
                        condition=entry_data.get("condition", "Near Mint"),
                        language=entry_data.get("language", "EN"),
                        first_edition=entry_data.get("first_edition", False),
                        image_id=image_id,
                        source_row=entry_data
                    ))
                    count += 1

        if count > 0:
            ui.notify(f"Parsed {count} entries from JSON.", type='positive')
        else:
            ui.notify("No valid entries found in JSON.", type='warning')

    async def process_cardmarket(self, content: bytes, filename: str):
        # 1. Parse
        try:
            rows = await asyncio.to_thread(CardmarketParser.parse_file, content, filename)
        except Exception as ex:
            ui.notify(f"Parser Error: {ex}", type='negative')
            return

        if not rows:
            ui.notify("No rows found in file.", type='warning')
            return

        # 2. Resolve
        # Build Lookup for efficiency (Exact + Normalized)
        row_langs = set(row.language for row in rows)
        required_langs = {l.lower() for l in row_langs}
        required_langs.add('en')  # Always load EN for fallback

        self.db_lookup = {}
        for db_lang in required_langs:
             try:
                 cards = await ygo_service.load_card_database(db_lang)
             except Exception:
                 logger.warning(f"Could not load DB for language: {db_lang}")
                 continue

             for card in cards:
                 for s in card.card_sets:
                     code = s.set_code
                     entry = {'rarity': s.set_rarity, 'card': card, 'variant': s, 'lang': db_lang}

                     # Key 1: Exact Code
                     if code not in self.db_lookup: self.db_lookup[code] = []
                     if not any(x['variant'].variant_id == s.variant_id for x in self.db_lookup[code]):
                         self.db_lookup[code].append(entry)

                     # Key 2: Base Code (Normalized)
                     base_code = normalize_set_code(code)
                     if base_code != code:
                         if base_code not in self.db_lookup: self.db_lookup[base_code] = []
                         if not any(x['variant'].variant_id == s.variant_id for x in self.db_lookup[base_code]):
                             self.db_lookup[base_code].append(entry)

        # Match Rows
        for row in rows:
            base_code = f"{row.set_prefix}-{row.number}"

            # 1. Gather All Siblings from DB (Base Code matches)
            # These include compatible AND incompatible variants (e.g. LOB-EN020)
            # We look up by Base Code primarily.
            # We also used to look up by Target Code, but we haven't defined Target Code yet.
            # Actually, to find ALL siblings, we should look up by Base Code + any potential candidates.
            # But Base Code lookup should cover most "siblings" that share the prefix-number.
            # However, sometimes codes differ slightly? No, Base Code is the common denominator.

            all_siblings = []
            seen_variant_ids = set()

            # We generate temporary potential targets just for lookup keys (including legacy)
            # to ensure we find everything even if base code logic is flawed (though it shouldn't be).
            # But simpler: Just look up Base Code + Standard Target + Legacy Target candidates?

            potential_lookup_targets = [f"{row.set_prefix}-{row.language}{row.number}"]
            legacy_candidate = get_legacy_code(row.set_prefix, row.number, row.language)
            if legacy_candidate: potential_lookup_targets.append(legacy_candidate)

            lookup_keys = set(potential_lookup_targets)
            lookup_keys.add(base_code)

            for key in lookup_keys:
                if key in self.db_lookup:
                    for entry in self.db_lookup[key]:
                        vid = entry['variant'].variant_id
                        if vid not in seen_variant_ids:
                            all_siblings.append({
                                'card': entry['card'],
                                'variant': entry['variant'],
                                'code': entry['variant'].set_code, # Keep actual DB code
                                'rarity': entry['rarity'],
                                'lang': entry.get('lang', 'en')
                            })
                            seen_variant_ids.add(vid)

            # 2. Identify Target Codes (Virtual Candidates) based on Siblings
            target_codes = []
            std_target = f"{row.set_prefix}-{row.language}{row.number}"
            target_codes.append(std_target) # Always add standard

            if legacy_candidate and legacy_candidate != std_target:
                # Only add legacy target if we observe legacy format in siblings OR no siblings
                # Check for 1-letter region codes in siblings
                has_legacy_sibling = False
                for s in all_siblings:
                    # Check region length of sibling code
                    m = re.match(r'^([A-Za-z0-9]+)-([A-Za-z]+)(\d+)$', s['code'])
                    if m:
                        region = m.group(2)
                        if len(region) == 1:
                            has_legacy_sibling = True
                            break

                if has_legacy_sibling or not all_siblings:
                    target_codes.append(legacy_candidate)

            # 3. Filter Compatible Matches (for Set Code selection)
            compatible_matches = []
            for m in all_siblings:
                if not is_set_code_compatible(m['code'], row.language):
                    continue

                # Name Similarity Check
                # We enforce strict name matching (Threshold 0.95) to prevent Legacy Code Collisions.
                # To handle Cross-Language matches (e.g. German Input vs English DB Match), we:
                # 1. Check strict match against the DB card name.
                # 2. If that fails, check strict match against the LOCALIZED card name (by ID lookup).
                # This ensures we accept valid translations (verified by ID) but reject collisions (Raigeki vs Hinotama).

                threshold = 0.95
                input_name = row.name.lower().strip()
                db_name = m['card'].name.lower().strip()

                # 1. Primary Check (Direct Match)
                ratio = difflib.SequenceMatcher(None, input_name, db_name).ratio()
                is_match = ratio >= threshold

                # 2. Secondary Check (ID-Based English Verification)
                # Since Cardmarket PDF exports always use English names (even for non-English cards),
                # we must verify against the English DB name if the match came from a non-English DB.
                if not is_match and m['lang'] != 'en':
                    try:
                        # Load English DB to verify identity
                        english_card = await ygo_service.load_card_database('en')
                        # Find by ID (Optimization: this scan is repeated, could be faster with dict, but acceptable for import)
                        # Actually ygo_service.get_card uses cached list scan.
                        en_card = ygo_service.get_card(m['card'].id, 'en')

                        if en_card:
                            en_name = en_card.name.lower().strip()
                            ratio_en = difflib.SequenceMatcher(None, input_name, en_name).ratio()
                            if ratio_en >= threshold:
                                is_match = True
                                # logger.info(f"Verified English match via ID: {row.name} == {en_card.name} (ID {m['card'].id})")
                    except Exception as e:
                        logger.warning(f"Failed to lookup English card: {e}")

                if not is_match:
                     logger.warning(f"Rejected mismatch ({row.language}/{m['lang']}): {row.name} vs {m['card'].name} ({ratio:.2f} < {threshold})")
                     continue

                compatible_matches.append(m)

            # 3.5 Name Lookup Fallback (If no compatible matches found via Code)
            # This handles cases where Code Lookup matched nothing valid (e.g. Legacy Mismatch rejected),
            # but we can find the correct card by Name (e.g. "Hinotama Soul" in EN DB).
            if not compatible_matches and row.name:
                found_by_name = None
                # Search loaded DBs for fuzzy name match
                # Prioritize EN DB as it is most complete
                langs_to_search = sorted(list(required_langs), key=lambda l: 0 if l == 'en' else 1)

                for lang in langs_to_search:
                     # Access cache safely or reload (cached)
                     # Since we are in async function, we can await
                     cards = await ygo_service.load_card_database(lang)
                     if not cards: continue

                     # Simple scan (Optimization: ygo_service could have a name index, but iteration is acceptable for error recovery)
                     # Note: row.name might be in DE ("Hinotama Seele"). Searching in EN DB ("Hinotama Soul") requires fuzzy match.
                     # Searching in DE DB ("Hinotama Seele") requires exact/fuzzy.

                     # Exact Match first
                     for c in cards:
                         if c.name.lower() == row.name.lower():
                             found_by_name = (c, lang)
                             break
                     if found_by_name: break

                     # Fuzzy Match (if exact failed)
                     # Only if we are desperate. Let's try high threshold.
                     best_ratio = 0
                     best_card = None
                     for c in cards:
                         r = difflib.SequenceMatcher(None, row.name, c.name).ratio()
                         if r > 0.85 and r > best_ratio:
                             best_ratio = r
                             best_card = c

                     if best_card:
                         found_by_name = (best_card, lang)
                         break

                if found_by_name:
                    card, lang = found_by_name
                    # We found the correct CARD Identity.
                    # Add its variants to matches so user can select them.
                    # This allows linking LOB-G020 (Input) to LOB-EN026 (DB Variant).
                    for s in card.card_sets:
                        entry = {
                                'card': card,
                                'variant': s,
                                'code': s.set_code,
                                'rarity': s.set_rarity,
                                'lang': lang
                        }
                        compatible_matches.append(entry)
                        all_siblings.append(entry)
                    logger.info(f"Resolved by Name Fallback: {row.name} -> {card.name}")

                    # Attempt Auto-Resolve if Rarity Matches
                    # If we found the correct card by name, and the input rarity matches one of its variants,
                    # we can safely import it using the input Set Code (e.g. LOB-G020) as a custom variant.
                    # This avoids the Ambiguity Dialog for clear-cut Legacy imports.

                    # Find sibling matching rarity
                    sibling_match = next((s for s in all_siblings if s['rarity'] == row.set_rarity), None)

                    if sibling_match:
                        # Determine best Set Code dynamically
                        # Using Number Pattern Analysis to distinguish Legacy shifts (e.g. LOB-G020 vs LOB-EN026)
                        best_code = self._deduce_best_set_code(row, all_siblings, std_target, legacy_candidate)

                        # Add to Pending
                        self._add_pending_from_match(row, sibling_match, override_set_code=best_code)
                        logger.info(f"Auto-Resolved Name Fallback: {row.name} ({best_code})")
                        continue

            # 4. Determine Valid Set Code Options
            # Union of Compatible Matches (from DB) and Target Codes (Virtual)
            valid_code_options = set()
            for m in compatible_matches:
                valid_code_options.add(m['code'])
            for t in target_codes:
                valid_code_options.add(t)

            valid_code_options = sorted(list(valid_code_options))

            # 5. Resolution Logic

            # Check for Perfect Match in Compatible Matches (Code + Rarity)
            # If we find exactly one compatible match that also matches rarity, that's a strong candidate.
            # But we must also consider if the user *prefers* the Target Code (e.g. LOB-DE020) over a neutral DB match (LOB-020).
            # If LOB-020 exists and matches rarity, and LOB-DE020 is target.
            # Ambiguity? LOB-020 vs LOB-DE020. User probably wants LOB-DE020 but DB has LOB-020.
            # Current logic: If multiple valid options, it's ambiguous.

            if len(valid_code_options) == 1:
                # Only one valid code option (e.g. RA01-DE049, where RA01-EN049 is incompatible).
                single_code = valid_code_options[0]

                # Verify Rarity Validity
                # We check if the requested rarity exists in ANY sibling (compatible or not).
                # (e.g. RA01-DE049 is target, we check RA01-EN049 to see if "Common" exists)

                rarity_found_in_siblings = False
                sibling_match = None
                for s in all_siblings:
                    if s['rarity'] == row.set_rarity:
                        rarity_found_in_siblings = True
                        sibling_match = s
                        break

                if rarity_found_in_siblings:
                    # Auto-Resolve
                    # If the single code corresponds to an existing DB match, use it directly.
                    # Else (it's a new virtual target), use the sibling match for card info.

                    # Find if single_code is in compatible_matches
                    existing_match = next((m for m in compatible_matches if m['code'] == single_code and m['rarity'] == row.set_rarity), None)

                    if existing_match:
                        self._add_pending_from_match(row, existing_match)
                    else:
                        # New Target Code. Use sibling for card data.
                        self._add_pending_from_match(row, sibling_match, override_set_code=single_code)
                else:
                    # Rarity Mismatch Ambiguity
                    self._add_ambiguity(row, compatible_matches, all_siblings, target_codes, row.set_rarity)

            else:
                # Multiple Valid Code Options (e.g. LOB-020, LOB-DE020)
                # OR Zero Valid Options (shouldn't happen if we added targets, unless empty targets?)
                if not valid_code_options and not all_siblings:
                     self.failed_rows.append(row)
                else:
                     # Ambiguity
                     self._add_ambiguity(row, compatible_matches, all_siblings, target_codes, row.set_rarity)

    def _add_ambiguity(self, row, compatible_matches, all_siblings, target_codes, default_rarity):
        # Select default card if possible (take first sibling)
        default_card = all_siblings[0]['card'] if all_siblings else None

        # Default Set Code: Prefer the standard target code if available
        default_set_code = target_codes[0] if target_codes else (compatible_matches[0]['code'] if compatible_matches else "")

        self.ambiguous_rows.append({
            'row': row,
            'matches': compatible_matches, # Only compatible matches for UI list
            'all_siblings': all_siblings,  # All siblings for Rarity lookup
            'target_codes': target_codes,
            'selected_set_code': default_set_code,
            'selected_rarity': default_rarity,
            'selected_card': default_card,
            'include': True
        })

    def _add_pending_from_match(self, row: ParsedRow, match: Dict, override_set_code: Optional[str] = None):
        self.pending_changes.append(PendingChange(
            api_card=match['card'],
            set_code=override_set_code if override_set_code else match['code'],
            rarity=match['variant'].set_rarity,
            quantity=row.quantity,
            condition=row.set_condition,
            language=row.language,
            first_edition=row.first_edition,
            image_id=match['variant'].image_id,
            source_row=row
        ))

    def _deduce_best_set_code(self, row, siblings, std_target, legacy_candidate):
        """
        Deduces the best set code based on Number Pattern Analysis.
        Checks if the input number matches known patterns for 2-Letter or 1-Letter regions.
        """
        formats = {} # '2-Letter', '1-Letter', '0-Letter' -> Number

        for s in siblings:
            code = s['code']
            # Analyze format
            m = re.match(r'^([A-Za-z0-9]+)-([A-Za-z]+)(\d+)$', code)
            if m:
                region = m.group(2)
                number = m.group(3)
                fmt = '1-Letter' if len(region) == 1 else '2-Letter'
                formats[fmt] = number
            else:
                m = re.match(r'^([A-Za-z0-9]+)-(\d+)$', code)
                if m:
                    number = m.group(2)
                    formats['0-Letter'] = number

        # Compare Input Number
        input_num = row.number

        # 1. Check 2-Letter Match (Standard)
        if '2-Letter' in formats:
            if formats['2-Letter'] == input_num:
                return std_target # Matches Standard Pattern
            else:
                # Mismatch! Standard target is invalid.
                # If legacy candidate exists, prefer it.
                if legacy_candidate: return legacy_candidate

        # 2. Check 1-Letter Match (Legacy)
        if '1-Letter' in formats:
            if formats['1-Letter'] == input_num:
                return legacy_candidate # Matches Legacy Pattern

        # 3. Check 0-Letter Match (Base/Neutral)
        if '0-Letter' in formats:
            if formats['0-Letter'] == input_num:
                # Ambiguous if Standard also exists? But we handled that above.
                # If only 0-Letter exists and matches, Standard is usually safe fallback.
                return std_target

        # 4. No direct match found, but we have a mismatch with known formats?
        # If input_num didn't match 2-Letter (and 2-Letter existed), we returned legacy above.
        # If input_num didn't match anything we know?
        # Fallback to legacy if available (conservative for older sets), else standard.
        return legacy_candidate if legacy_candidate else std_target

    async def apply_import(self):
        if not self.selected_collection:
            ui.notify("No collection selected", type='warning')
            return

        if not self.pending_changes:
            ui.notify("No entries to import", type='warning')
            return

        try:
            collection = persistence.load_collection(self.selected_collection)
        except Exception as e:
            ui.notify(f"Error loading collection: {e}", type='negative')
            return

        # Undo Snapshot
        self.undo_stack.append({
            "filename": self.selected_collection,
            "data": collection.model_dump(mode='json')
        })
        if self.undo_btn:
            self.undo_btn.visible = True
            self.undo_btn.update()

        changes = 0
        self.successful_imports = []
        self.import_failures = []
        modified_card_ids = set()

        for item in self.pending_changes:
            try:
                # 1. Database Update Check
                # Check if variant exists in ApiCard; if not, add it
                # We do this to ensure DB consistency for new custom/ambiguous variants
                variant_exists = False
                for s in item.api_card.card_sets:
                    if s.set_code == item.set_code and s.set_rarity == item.rarity:
                        variant_exists = True
                        # Ensure image_id is preserved if we found an existing match but the item didn't have it set (e.g. from ambiguity resolution)
                        if item.image_id is None:
                            item.image_id = s.image_id
                        break

                if not variant_exists:
                    # Create new variant
                    new_id = str(uuid.uuid4())
                    rarity_abbr = RARITY_ABBREVIATIONS.get(item.rarity, "")
                    rarity_code = f"({rarity_abbr})" if rarity_abbr else ""

                    # Try to infer set name/image from other variants in same set
                    set_name = "Custom Set"
                    image_id = item.image_id

                    # Look for siblings
                    prefix = item.set_code.split('-')[0]
                    for s in item.api_card.card_sets:
                        if s.set_code.startswith(prefix):
                            set_name = s.set_name
                            if image_id is None: image_id = s.image_id
                            break

                    if image_id is None and item.api_card.card_images:
                        image_id = item.api_card.card_images[0].id

                    new_set = ApiCardSet(
                        variant_id=new_id,
                        set_name=set_name,
                        set_code=item.set_code,
                        set_rarity=item.rarity,
                        set_rarity_code=rarity_code,
                        set_price="0.00",
                        image_id=image_id
                    )
                    item.api_card.card_sets.append(new_set)
                    modified_card_ids.add(item.api_card.id)
                    # Update item image_id if it was missing
                    if item.image_id is None:
                        item.image_id = image_id

                # 2. Collection Update
                # Determine Quantity Delta
                delta = item.quantity
                if self.import_mode == 'SUBTRACT':
                    delta = -delta

                modified = CollectionEditor.apply_change(
                    collection=collection,
                    api_card=item.api_card,
                    set_code=item.set_code,
                    rarity=item.rarity,
                    language=item.language,
                    quantity=delta,
                    condition=item.condition,
                    first_edition=item.first_edition,
                    image_id=item.image_id,
                    mode='ADD' # We always use ADD mode with pos/neg delta
                )
                if modified:
                    changes += 1
                    self.successful_imports.append(f"{item.quantity}x {item.api_card.name} ({item.set_code} - {item.rarity})")
                else:
                    reason = "No changes applied"
                    if self.import_mode == 'SUBTRACT':
                         reason = "Card not found for removal"
                    self.import_failures.append(f"{item.quantity}x {item.api_card.name} ({item.set_code}): {reason}")
            except Exception as e:
                logger.error(f"Import Error for item {item.set_code}: {e}")
                self.import_failures.append(f"{item.quantity}x {item.api_card.name} ({item.set_code}): {str(e)}")

        # Save DB Updates if any
        if modified_card_ids:
            # We need to save the DBs that contain these cards.
            # Iterate all loaded languages in service cache.
            for lang, cards in ygo_service._cards_cache.items():
                # Check if any modified card is in this list (by reference or ID)
                # Since we modified the object in place, and the object is (presumably) the one in the cache...
                # We can just check IDs.
                ids_in_lang = {c.id for c in cards}
                if not ids_in_lang.isdisjoint(modified_card_ids):
                    await ygo_service.save_card_database(cards, lang)
                    logger.info(f"Saved updated DB for language: {lang}")

        if changes > 0 or (changes == 0 and self.import_mode == 'ADD'):
            # Note: 0 changes might happen if subtract removes non-existent cards, but we still save/notify
            persistence.save_collection(collection, self.selected_collection)
            ui.notify(f"Successfully processed {changes} changes.", type='positive')

            # Reset
            self.pending_changes = []
            self.refresh_status_ui()
        else:
            ui.notify("No changes were necessary (e.g. subtracting from empty).", type='info')

    def undo_last(self):
        if not self.undo_stack: return

        state = self.undo_stack.pop()
        filename = state['filename']
        data = state['data']

        try:
            collection = Collection(**data)
            persistence.save_collection(collection, filename)
            ui.notify(f"Undid last import for {filename}", type='positive')

            if not self.undo_stack and self.undo_btn:
                self.undo_btn.visible = False
                self.undo_btn.update()
        except Exception as e:
            ui.notify(f"Undo failed: {e}", type='negative')

    def download_failures(self):
        if not self.failed_rows: return
        lines = ["Original Line | Reason"]
        for row in self.failed_rows:
            # Handle both ParsedRow objects and Dicts (from JSON)
            if isinstance(row, dict):
                reason = row.get('failure_reason', "Import failed")
                line = str(row) # JSON entries don't have original_line usually
            else:
                default_reason = f"No matching set code found in DB for {row.set_prefix}-{row.language}{row.number}"
                reason = getattr(row, 'failure_reason', default_reason)
                line = row.original_line

            lines.append(f"{line} | {reason}")
        ui.download("\n".join(lines).encode('utf-8'), "import_failures.txt")

    def download_success_report(self):
        if not self.successful_imports: return
        lines = ["Quantity Name (Set - Rarity)"] + self.successful_imports
        ui.download("\n".join(lines).encode('utf-8'), "import_success.txt")

    def download_import_failures(self):
        if not self.import_failures: return
        lines = ["Original Line | Reason"] + self.import_failures
        ui.download("\n".join(lines).encode('utf-8'), "import_errors.txt")

    def open_ambiguity_dialog(self):
        if not self.ambiguous_rows: return

        with ui.dialog() as dialog, ui.card().classes('w-full max-w-6xl bg-dark border border-gray-700'):
            with ui.row().classes('w-full justify-between items-center'):
                with ui.column().classes('gap-0'):
                    ui.label("Resolve Ambiguities").classes('text-h6')
                    ui.label("Cards with missing Set Code/Rarity combinations or multiple matches.").classes('text-caption text-grey')

                # Bulk Actions
                with ui.row().classes('gap-2'):
                    def apply_bulk_region(regex_pattern):
                        count = 0
                        for item in self.ambiguous_rows:
                            if not item['include']: continue
                            # Find matching option
                            # Check Targets
                            found = None
                            for t in item['target_codes']:
                                if re.search(regex_pattern, t):
                                    found = t
                                    break
                            # Check Matches
                            if not found:
                                for m in item['matches']:
                                    if re.search(regex_pattern, m['code']):
                                        found = m['code']
                                        break

                            if found:
                                item['selected_set_code'] = found
                                # Sanitize Rarity Immediately
                                valid = get_valid_rarities(item)
                                if item['selected_rarity'] not in valid and valid:
                                    item['selected_rarity'] = valid[0]
                                count += 1
                        if count > 0:
                            render_rows()
                            ui.notify(f"Updated {count} rows", type='info')

                    ui.button("Set 2-Letter (-EN)", on_click=lambda: apply_bulk_region(r'-[A-Za-z]{2}\d+')).props('outline dense size=sm color=white')
                    ui.button("Set 1-Letter (-E)", on_click=lambda: apply_bulk_region(r'-[A-Za-z]\d+')).props('outline dense size=sm color=white')
                    ui.button("Set No-Letter (-001)", on_click=lambda: apply_bulk_region(r'-\d+')).props('outline dense size=sm color=white')

            # Declare container ref
            rows_container = None

            def render_printings_content(siblings):
                with ui.column().classes('p-2 gap-1'):
                    ui.label("Known Printings").classes('text-xs font-bold text-gray-400 uppercase mb-1')
                    if siblings:
                         for s in siblings:
                              ui.label(f"{s['code']} - {s['rarity']}").classes('text-sm whitespace-nowrap')
                    else:
                        ui.label("No known siblings").classes('text-sm italic text-gray-500')

            def get_valid_rarities(item):
                """
                Returns available rarities based on the selected set code.
                - If set code is Existing (in compatible matches): Return rarity of matches with that code.
                - If set code is New (Target): Return ALL rarities found in ANY sibling (all_siblings).
                """
                selected_code = item['selected_set_code']

                # Check if selected code is an existing compatible match
                # Gather all matches with this code (handle potential duplicates in DB with diff rarities)
                matches_for_code = [m for m in item['matches'] if m['code'] == selected_code]

                if matches_for_code:
                    return sorted(list({m['rarity'] for m in matches_for_code}))

                # New/Target Code: Can borrow rarity from any sibling
                # Collect all unique rarities from all_siblings
                rarities = set()
                for s in item['all_siblings']:
                    rarities.add(s['rarity'])

                return sorted(list(rarities))

            def render_rows():
                if not rows_container: return
                rows_container.clear()
                with rows_container:
                    for item in self.ambiguous_rows:
                        row = item['row']

                        # Prepare Set Code Options: Matches + Target Codes
                        code_opts = {}

                        # 1. Targets (New)
                        for t in item['target_codes']:
                            code_opts[t] = f"{t} (New)"

                        # 2. Existing Compatible Matches
                        for m in item['matches']:
                            code_opts[m['code']] = f"{m['code']} (Existing)"

                        # Ensure selected value is in options (fallback)
                        if item['selected_set_code'] not in code_opts:
                            code_opts[item['selected_set_code']] = item['selected_set_code']

                        # Prepare Rarity Options (Dynamic)
                        valid_rarities = get_valid_rarities(item)
                        # If selected rarity is invalid (e.g. switched set code), reset it to first valid
                        if item['selected_rarity'] not in valid_rarities and valid_rarities:
                            item['selected_rarity'] = valid_rarities[0]

                        with ui.row().classes('w-full items-center gap-2 q-mb-sm border-b border-gray-800 pb-2'):
                            # 1. Include Checkbox
                            ui.checkbox(value=item['include'],
                                        on_change=lambda e, it=item: it.update({'include': e.value})).classes('w-10 justify-center')

                            # 2. Card Info (Added Language)
                            with ui.column().classes('w-1/4'):
                                ui.label(f"{row.quantity}x {row.name}").classes('font-bold')
                                ui.label(f"Orig: {row.set_prefix} | {row.language} | {row.rarity_abbr}").classes('text-xs text-grey-5')

                            # 3. Set Code Dropdown
                            def update_code(e, it):
                                it['selected_set_code'] = e.value
                                render_rows() # Re-render to update rarity options

                            ui.select(options=code_opts, value=item['selected_set_code'],
                                      on_change=lambda e, i=item: update_code(e, i)) \
                                      .classes('w-1/4').props('dark dense options-dense')

                            # 4. Rarity Control (Dropdown or Static)
                            def update_rarity(e, it):
                                it['selected_rarity'] = e.value

                            if len(valid_rarities) <= 1:
                                # Static Text
                                with ui.row().classes('w-1/4 items-center gap-2'):
                                    ui.label(item['selected_rarity']).classes('text-sm font-bold bg-gray-700 px-2 py-1 rounded text-gray-300')
                            else:
                                ui.select(options=valid_rarities, value=item['selected_rarity'],
                                          on_change=lambda e, i=item: update_rarity(e, i)) \
                                          .classes('w-1/4').props('dark dense options-dense')

                            # 5. Info Icon (Expandable Overview)
                            with ui.button(icon='info').props('flat round dense size=sm color=info'):
                                with ui.tooltip().classes('bg-gray-900 border border-gray-700'):
                                    render_printings_content(item['all_siblings'])
                                with ui.menu().classes('bg-gray-900 border border-gray-700'):
                                    render_printings_content(item['all_siblings'])


            def toggle_all(e):
                for item in self.ambiguous_rows:
                    item['include'] = e.value
                render_rows()

            with ui.scroll_area().classes('h-96 w-full q-my-md'):
                # Header
                with ui.row().classes('w-full items-center gap-2 font-bold text-grey-4 q-mb-sm border-b border-gray-600 pb-2'):
                    ui.checkbox(value=True, on_change=toggle_all).classes('w-10 justify-center').props('dense')
                    ui.label("Card").classes('w-1/4')
                    ui.label("Set Code").classes('w-1/4')
                    ui.label("Rarity").classes('w-1/4')

                # Render initial rows
                rows_container = ui.column().classes('w-full')
                render_rows()

            with ui.row().classes('w-full justify-end gap-4 q-mt-md'):
                ui.button("Cancel", on_click=dialog.close).props('outline color=white')

                def confirm():
                    for item in self.ambiguous_rows:
                        if not item['include']:
                            # Add to failed rows with reason
                            # We modify the row object slightly or wrap it?
                            # Failed rows expects ParsedRow objects.
                            # We can just append the original row.
                            # The download_failures reads 'original_line' and appends a fixed error message.
                            # The user requested custom reason.
                            # I need to handle this in download_failures or here.
                            # Let's attach the reason to the row object if possible, or use a wrapper.
                            # Since ParsedRow is a dataclass, I can't easily add attributes unless I redefine it.
                            # I'll just append to failed_rows and maybe handle the reason logic in download_failures by looking up context?
                            # Or better: I'll append a tuple or dict to failed_rows if possible?
                            # Current implementation of download_failures iterates failed_rows which are ParsedRow.
                            # I'll modify download_failures in next step to handle dicts or annotated rows.
                            # For now, I'll add a 'failure_reason' attr to the row instance dynamically.
                            item['row'].failure_reason = "Not selected by user in resolution"
                            self.failed_rows.append(item['row'])
                        else:
                            # Add to pending changes
                            # We need to find the correct ApiCard.
                            # If we have matches, use the one from matches if set code matches, else use 'selected_card' (default)

                            # Determine correct ApiCard
                            chosen_card = item['selected_card']
                            # If the selected code corresponds to a specific match, use that match's card
                            # (Useful if ambiguity was between two completely different cards sharing a code, though unlikely)
                            for m in item['matches']:
                                if m['code'] == item['selected_set_code']:
                                    chosen_card = m['card']
                                    break

                            if not chosen_card:
                                # Should not happen if matches exists, but if it does:
                                # We can't import without an ApiCard.
                                # Fallback: Add to failed
                                item['row'].failure_reason = "Could not resolve ApiCard reference"
                                self.failed_rows.append(item['row'])
                                continue

                            # Add Pending
                            self.pending_changes.append(PendingChange(
                                api_card=chosen_card,
                                set_code=item['selected_set_code'],
                                rarity=item['selected_rarity'],
                                quantity=item['row'].quantity,
                                condition=item['row'].set_condition,
                                language=item['row'].language,
                                first_edition=item['row'].first_edition,
                                image_id=None, # Will resolve later or use default
                                source_row=item['row']
                            ))

                    self.ambiguous_rows = []
                    self.refresh_status_ui()
                    dialog.close()

                ui.button("Confirm Resolution", on_click=confirm).classes('bg-primary text-white')

        dialog.open()

    def open_preview_dialog(self):
        if not self.pending_changes: return

        # Create a temporary state list for the dialog
        # We wrap each pending change to track 'include' status
        preview_items = [{'data': p, 'include': True} for p in self.pending_changes]

        with ui.dialog() as dialog, ui.card().classes('w-full max-w-6xl bg-dark border border-gray-700'):
            ui.label("Import Preview").classes('text-h6')
            ui.label("Review items before importing. Uncheck to exclude.").classes('text-caption text-grey')

            rows_container = None

            def render_rows():
                if not rows_container: return
                rows_container.clear()
                with rows_container:
                    for item in preview_items:
                        p = item['data']
                        with ui.row().classes('w-full items-center gap-2 q-mb-sm border-b border-gray-800 pb-2'):
                            ui.checkbox(value=item['include'],
                                        on_change=lambda e, it=item: it.update({'include': e.value})).classes('w-10 justify-center')

                            ui.label(str(p.quantity)).classes('w-10 text-center')
                            ui.label(p.api_card.name).classes('w-1/3 font-bold truncate')
                            ui.label(p.set_code).classes('w-1/4 text-sm')
                            ui.label(p.rarity).classes('w-1/4 text-sm')

            def toggle_all(e):
                for item in preview_items:
                    item['include'] = e.value
                render_rows()

            with ui.scroll_area().classes('h-96 w-full q-my-md'):
                 # Header
                with ui.row().classes('w-full items-center gap-2 font-bold text-grey-4 q-mb-sm border-b border-gray-600 pb-2'):
                    ui.checkbox(value=True, on_change=toggle_all).classes('w-10 justify-center').props('dense')
                    ui.label("Qty").classes('w-10 text-center')
                    ui.label("Card").classes('w-1/3')
                    ui.label("Set Code").classes('w-1/4')
                    ui.label("Rarity").classes('w-1/4')

                rows_container = ui.column().classes('w-full')
                render_rows()

            with ui.row().classes('w-full justify-end gap-4 q-mt-md'):
                ui.button("Cancel", on_click=dialog.close).props('outline color=white')

                def confirm():
                    new_pending = []
                    excluded_count = 0
                    for item in preview_items:
                        if item['include']:
                            new_pending.append(item['data'])
                        else:
                            # Move to failed rows
                            p = item['data']
                            if p.source_row:
                                # Inject reason safely (handle dict vs object)
                                reason = "Excluded from preview by user"
                                if isinstance(p.source_row, dict):
                                    p.source_row['failure_reason'] = reason
                                else:
                                    p.source_row.failure_reason = reason

                                self.failed_rows.append(p.source_row)
                            excluded_count += 1

                    self.pending_changes = new_pending
                    if excluded_count > 0:
                        ui.notify(f"Excluded {excluded_count} items.", type='warning')

                    self.refresh_status_ui()
                    dialog.close()

                ui.button("Update Selection", on_click=confirm).classes('bg-primary text-white')

        dialog.open()

    def refresh_status_ui(self):
        if not self.status_container: return
        self.status_container.clear()

        with self.status_container:
            # Stats
            if self.successful_imports and not self.pending_changes:
                with ui.row().classes('items-center gap-2'):
                    ui.label(f"Last Import: {len(self.successful_imports)} items added").classes('text-positive font-bold text-lg')
                    ui.button("Download Report", on_click=self.download_success_report).props('flat color=positive')

            if self.pending_changes:
                with ui.row().classes('items-center gap-4'):
                    ui.label(f"Ready to Import: {len(self.pending_changes)} items").classes('text-positive font-bold text-lg')
                    ui.button("See Preview", on_click=self.open_preview_dialog).props('outline size=sm color=positive')

            if self.ambiguous_rows:
                with ui.row().classes('items-center gap-2'):
                    ui.label(f"Ambiguous Items: {len(self.ambiguous_rows)}").classes('text-warning font-bold text-lg')
                    ui.button("Resolve", on_click=self.open_ambiguity_dialog).classes('bg-warning text-dark')

            if self.failed_rows:
                with ui.row().classes('items-center gap-2'):
                    ui.label(f"Parsing Failures: {len(self.failed_rows)}").classes('text-negative font-bold text-lg')
                    ui.button("Download Report", on_click=self.download_failures).props('flat color=negative')

            if self.import_failures:
                with ui.row().classes('items-center gap-2'):
                    ui.label(f"Import Errors: {len(self.import_failures)}").classes('text-negative font-bold text-lg')
                    ui.button("Download Report", on_click=self.download_import_failures).props('flat color=negative')

            # Update Import Button
            if self.import_btn:
                can_import = len(self.pending_changes) > 0 and len(self.ambiguous_rows) == 0
                self.import_btn.enabled = can_import
                mode_text = "ADD" if self.import_mode == 'ADD' else "SUBTRACT"
                self.import_btn.text = f"Import {len(self.pending_changes)} Items ({mode_text})"

class MergeController:
    def __init__(self):
        self.collections: List[str] = []
        self.coll_a: Optional[str] = None
        self.coll_b: Optional[str] = None
        self.new_name: str = ""
        self.refresh_collections()

    def refresh_collections(self):
        self.collections = persistence.list_collections()

    async def handle_merge(self):
        if not self.coll_a or not self.coll_b:
            ui.notify("Please select two collections.", type='warning')
            return
        if self.coll_a == self.coll_b:
            ui.notify("Cannot merge collection into itself.", type='warning')
            return
        if not self.new_name.strip():
            ui.notify("Enter a new collection name.", type='warning')
            return

        new_filename = f"{self.new_name.strip()}.json"
        if new_filename in self.collections:
            ui.notify("Collection exists.", type='negative')
            return

        ui.notify("Merging...", type='info')
        try:
            coll_a_obj = persistence.load_collection(self.coll_a)
            coll_b_obj = persistence.load_collection(self.coll_b)
            new_collection = Collection(name=self.new_name.strip())

            await ygo_service.load_card_database()

            async def merge_into(source):
                for card in source.cards:
                    api_card = ygo_service.get_card(card.card_id)
                    if not api_card: continue
                    for variant in card.variants:
                        for entry in variant.entries:
                            CollectionEditor.apply_change(
                                collection=new_collection,
                                api_card=api_card,
                                set_code=variant.set_code,
                                rarity=variant.rarity,
                                language=entry.language,
                                quantity=entry.quantity,
                                condition=entry.condition,
                                first_edition=entry.first_edition,
                                image_id=variant.image_id,
                                mode='ADD'
                            )

            await merge_into(coll_a_obj)
            await merge_into(coll_b_obj)

            persistence.save_collection(new_collection, new_filename)
            ui.notify(f"Created '{self.new_name}'", type='positive')
            self.refresh_collections()
            self.new_name = ""
        except Exception as e:
            logger.error(f"Merge error: {e}")
            ui.notify(f"Merge failed: {e}", type='negative')


def import_tools_page():
    controller = UnifiedImportController()
    merge_controller = MergeController()

    with ui.column().classes('w-full q-pa-md gap-6'):
        ui.label('Import Tools').classes('text-h4')

        # --- UNIFIED IMPORT CARD ---
        with ui.card().classes('w-full bg-dark border border-gray-700 p-6'):
            ui.label('Import Manager').classes('text-xl font-bold q-mb-md')

            # Row 1: Target Collection
            with ui.row().classes('items-center gap-4 w-full'):
                controller.collection_select = ui.select(
                    options=controller.collections,
                    label="Target Collection",
                    value=controller.selected_collection,
                    on_change=controller.on_collection_change
                ).classes('w-64').props('dark')

                def open_new_col_dialog():
                    with ui.dialog() as d, ui.card().classes('bg-dark border border-gray-700'):
                        ui.label('New Collection').classes('text-h6')
                        name_in = ui.input(placeholder='Name').props('dark autofocus')
                        async def create():
                            await controller.create_new_collection(name_in.value)
                            merge_controller.refresh_collections() # Sync
                            d.close()
                        ui.button('Create', on_click=create).classes('bg-accent text-dark')
                    d.open()
                ui.button(icon='add', on_click=open_new_col_dialog).props('flat round dense')

            # Row 2 & 3: Settings (Type & Mode)
            with ui.row().classes('items-center gap-8 q-my-md'):
                # Type Toggle
                with ui.column().classes('gap-1'):
                    ui.label('Source Type').classes('text-sm text-grey')
                    ui.toggle({
                        'JSON': 'JSON Backup',
                        'CARDMARKET': 'Cardmarket (PDF/Text)'
                    }, value='JSON', on_change=lambda e: setattr(controller, 'import_type', e.value)).props('dark')

                # Mode Toggle
                with ui.column().classes('gap-1'):
                    ui.label('Mode').classes('text-sm text-grey')
                    ui.toggle({
                        'ADD': 'Add to Collection',
                        'SUBTRACT': 'Remove from Collection'
                    }, value='ADD', on_change=lambda e: [setattr(controller, 'import_mode', e.value), controller.refresh_status_ui()]).props('dark color=red')

            # Row 4: Upload Area
            # Note: We can't easily change props of ui.upload after creation dynamically in a clean way
            # without re-rendering. But we can just handle the file type in validation.
            # Or we can re-render the upload component. Let's rely on backend validation/parsing mostly,
            # but setting a generous accept filter.
            ui.upload(
                label='Drop File Here (JSON, PDF, TXT)',
                auto_upload=True,
                on_upload=controller.handle_upload
            ).props('dark accept=".json, .pdf, .txt"').classes('w-full')

            # Row 5: Status/Preview
            controller.status_container = ui.column().classes('w-full q-mt-md')

            # Row 6: Actions
            with ui.row().classes('w-full justify-between items-center q-mt-lg'):
                controller.undo_btn = ui.button('Undo Last Import', on_click=controller.undo_last, icon='undo') \
                    .classes('bg-red-500 text-white').props('flat')
                controller.undo_btn.visible = False

                with ui.row().classes('gap-4 items-center'):
                    ui.button('Scan Again', on_click=controller.process_current_file, icon='refresh') \
                        .props('outline color=warning')

                    controller.import_btn = ui.button('Import', on_click=controller.apply_import) \
                        .classes('bg-primary text-white text-lg px-8')
                    controller.import_btn.enabled = False

        # --- MERGE CARD ---
        with ui.card().classes('w-full bg-dark border border-gray-700 p-6'):
            ui.label('Merge Collections').classes('text-xl font-bold q-mb-md')
            with ui.grid().classes('grid-cols-1 md:grid-cols-3 gap-4 w-full'):
                ui.select(merge_controller.collections, label='Collection A',
                          on_change=lambda e: setattr(merge_controller, 'coll_a', e.value)).props('dark')
                ui.select(merge_controller.collections, label='Collection B',
                          on_change=lambda e: setattr(merge_controller, 'coll_b', e.value)).props('dark')
                ui.input(label='New Name', on_change=lambda e: setattr(merge_controller, 'new_name', e.value)).props('dark')

            with ui.row().classes('w-full justify-end q-mt-md'):
                ui.button('Merge', on_click=merge_controller.handle_merge, icon='merge_type').classes('bg-primary text-white')
