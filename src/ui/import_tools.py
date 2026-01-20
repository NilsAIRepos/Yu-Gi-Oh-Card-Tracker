from nicegui import ui, events
import json
import logging
import asyncio
import uuid
from typing import Optional, List, Dict, Any
from dataclasses import dataclass

from src.core.persistence import persistence
from src.core.models import Collection, ApiCard, ApiCardSet
from src.core.utils import LANGUAGE_TO_LEGACY_REGION_MAP, normalize_set_code
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
                     entry = {'rarity': s.set_rarity, 'card': card, 'variant': s}

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
            # Construct Target Set Code (Standard format preferred for new entries)
            target_code = f"{row.set_prefix}-{row.language}{row.number}"
            base_code = f"{row.set_prefix}-{row.number}"

            # Gather all potential variants for this card (ignoring rarity for now)
            # We look up by Exact Candidates AND Base Code
            potential_variants = []
            seen_variant_ids = set()

            # 1. Define Lookup Keys
            lookup_keys = [target_code]
            legacy_char = LANGUAGE_TO_LEGACY_REGION_MAP.get(row.language)
            if legacy_char:
                lookup_keys.append(f"{row.set_prefix}-{legacy_char}{row.number}")
            lookup_keys.append(base_code)

            # 2. Collect DB Matches
            for key in lookup_keys:
                if key in self.db_lookup:
                    for entry in self.db_lookup[key]:
                        vid = entry['variant'].variant_id
                        if vid not in seen_variant_ids:
                            potential_variants.append({
                                'card': entry['card'],
                                'variant': entry['variant'],
                                'code': entry['variant'].set_code, # Keep actual DB code
                                'rarity': entry['rarity']
                            })
                            seen_variant_ids.add(vid)

            # 3. Analyze Matches
            # Exact Match: Rarity AND Set Code match
            exact_matches = []
            for m in potential_variants:
                if m['rarity'] == row.set_rarity:
                    # Check if set code is "close enough" (Exact or Base match)
                    # We consider it a match if the DB code matches one of our lookup keys
                    # OR if we found it via base code lookup (implies it's the same card/slot)
                    exact_matches.append(m)

            if len(exact_matches) == 1:
                # Perfect Single Match
                # Use target_code for the import (normalizing to user's file region preference usually)
                # But if we matched an exact legacy code (e.g. LOB-E001), maybe preserve it?
                # Requirement: "No matching set code found in DB for MRD-DE032 while MRD-032... exist"
                # If we found it via base code (MRD-032), we map to target_code (MRD-DE032) usually.
                m = exact_matches[0]
                self._add_pending_from_match(row, m, override_set_code=target_code)

            elif len(exact_matches) > 1:
                # Ambiguity: Multiple variants have matching rarity (e.g. same card, same rarity, diff codes in DB?)
                self._add_ambiguity(row, potential_variants, target_code, row.set_rarity)

            else:
                # No Exact Rarity Match
                if potential_variants:
                    # We found the Set Code (or Base Code), but NOT the Rarity.
                    # This is the "Rarity Ambiguity" case.
                    self._add_ambiguity(row, potential_variants, target_code, row.set_rarity)
                else:
                    # No Set Code match at all
                    self.failed_rows.append(row)

    def _add_ambiguity(self, row, matches, default_set_code, default_rarity):
        # Select default card if possible (take first match)
        default_card = matches[0]['card'] if matches else None

        self.ambiguous_rows.append({
            'row': row,
            'matches': matches, # Context for UI
            'selected_set_code': default_set_code,
            'selected_rarity': default_rarity,
            'selected_card': default_card,
            'include': True,
            'target_code': default_set_code
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
        modified_card_ids = set()

        for item in self.pending_changes:
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

    def open_ambiguity_dialog(self):
        if not self.ambiguous_rows: return

        # Prepare Rarity Options
        rarity_options = sorted(list(RARITY_ABBREVIATIONS.keys()))

        with ui.dialog() as dialog, ui.card().classes('w-full max-w-6xl bg-dark border border-gray-700'):
            ui.label("Resolve Ambiguities").classes('text-h6')
            ui.label("Cards with missing Set Code/Rarity combinations or multiple matches.").classes('text-caption text-grey')

            # Declare container ref
            rows_container = None

            def render_rows():
                if not rows_container: return
                rows_container.clear()
                with rows_container:
                    for item in self.ambiguous_rows:
                        row = item['row']
                        matches = item['matches']

                        # Prepare Set Code Options: Matches + Target Code
                        code_opts = {}
                        if item.get('target_code'):
                             code_opts[item['target_code']] = f"{item['target_code']} (New/Target)"

                        for m in matches:
                            code_opts[m['code']] = f"{m['code']} (Existing)"

                        # Ensure selected value is in options (fallback)
                        if item['selected_set_code'] not in code_opts:
                            code_opts[item['selected_set_code']] = item['selected_set_code']

                        with ui.row().classes('w-full items-center gap-2 q-mb-sm border-b border-gray-800 pb-2'):
                            # 1. Include Checkbox
                            ui.checkbox(value=item['include'],
                                        on_change=lambda e, it=item: it.update({'include': e.value})).classes('w-10 justify-center')

                            # 2. Card Info
                            with ui.column().classes('w-1/4'):
                                ui.label(f"{row.quantity}x {row.name}").classes('font-bold')
                                ui.label(f"Orig: {row.set_prefix} | {row.rarity_abbr}").classes('text-xs text-grey-5')

                            # 3. Set Code Dropdown
                            def update_code(e, it=item):
                                it['selected_set_code'] = e.value
                                pass

                            ui.select(options=code_opts, value=item['selected_set_code'],
                                      on_change=lambda e: update_code(e)) \
                                      .classes('w-1/4').props('dark dense options-dense')

                            # 4. Rarity Dropdown
                            ui.select(options=rarity_options, value=item['selected_rarity'],
                                      on_change=lambda e, it=item: it.update({'selected_rarity': e.value})) \
                                      .classes('w-1/4').props('dark dense options-dense')

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
                    ui.label(f"Failed Items: {len(self.failed_rows)}").classes('text-negative font-bold text-lg')
                    ui.button("Download Report", on_click=self.download_failures).props('flat color=negative')

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
                    on_change=lambda e: setattr(controller, 'selected_collection', e.value)
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
                    }, value='ADD', on_change=lambda e: setattr(controller, 'import_mode', e.value)).props('dark color=red')

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
