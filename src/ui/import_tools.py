from nicegui import ui, events
import json
import logging
import asyncio
from typing import Optional, List, Dict, Any
from dataclasses import dataclass

from src.core.persistence import persistence
from src.core.models import Collection, ApiCard, ApiCardSet
from src.core.utils import LANGUAGE_TO_LEGACY_REGION_MAP, normalize_set_code
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

            # 1. Try Exact Matches (Standard, Legacy, Regionless)
            exact_candidates = [target_code]
            legacy_char = LANGUAGE_TO_LEGACY_REGION_MAP.get(row.language)
            if legacy_char:
                exact_candidates.append(f"{row.set_prefix}-{legacy_char}{row.number}")
            exact_candidates.append(f"{row.set_prefix}-{row.number}")

            found_exact_matches = []
            seen_exact_vars = set()

            for cand in exact_candidates:
                if cand in self.db_lookup:
                    # Filter: Only accept variants that explicitly match the candidate code
                    # (Prevent bucket lookups from returning normalization-mapped variants)
                    matches = [
                        m for m in self.db_lookup[cand]
                        if m['rarity'] == row.set_rarity and m['variant'].set_code == cand
                    ]

                    for m in matches:
                        if m['variant'].variant_id not in seen_exact_vars:
                            found_exact_matches.append({
                                'match': m,
                                'set_code': cand
                            })
                            seen_exact_vars.add(m['variant'].variant_id)

            if found_exact_matches:
                if len(found_exact_matches) == 1:
                    m_data = found_exact_matches[0]
                    self._add_pending_from_match(row, m_data['match'], override_set_code=m_data['set_code'])
                else:
                    # Ambiguity among Exact Matches (e.g. LOB-EN091 vs LOB-E091)
                    ambig_matches = []
                    for fm in found_exact_matches:
                        ambig_matches.append({
                            'code': fm['set_code'],
                            'card': fm['match']['card'],
                            'variant': fm['match']['variant']
                        })

                    self.ambiguous_rows.append({
                        'row': row,
                        'matches': ambig_matches,
                        'selected_index': 0,
                        'target_code': target_code
                    })
                continue

            # 2. No Exact Match -> Try Base Code (Normalized)
            base_code = f"{row.set_prefix}-{row.number}"

            if base_code in self.db_lookup:
                matches = [m for m in self.db_lookup[base_code] if m['rarity'] == row.set_rarity]

                if not matches:
                    self.failed_rows.append(row)
                    continue

                if len(matches) == 1:
                    # Auto-Match: Use the existing variant data but create as target_code
                    self._add_pending_from_match(row, matches[0], override_set_code=target_code)
                else:
                    # Ambiguity: Multiple variants map to this base code
                    ambig_matches = []
                    seen_vars = set()
                    for m in matches:
                        # Deduplicate if same variant ID (e.g. from multiple DB loads/keys)
                        if m['variant'].variant_id in seen_vars: continue
                        seen_vars.add(m['variant'].variant_id)

                        ambig_matches.append({
                            'code': m['variant'].set_code,
                            'card': m['card'],
                            'variant': m['variant']
                        })

                    if len(ambig_matches) == 1:
                        self._add_pending_from_match(row, {'card': ambig_matches[0]['card'], 'variant': ambig_matches[0]['variant'], 'code': ambig_matches[0]['code']}, override_set_code=target_code)
                    else:
                        self.ambiguous_rows.append({
                            'row': row,
                            'matches': ambig_matches,
                            'selected_index': 0,
                            'target_code': target_code
                        })
            else:
                self.failed_rows.append(row)

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
        for item in self.pending_changes:
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
            lines.append(f"{row.original_line} | No matching set code found in DB for {row.set_prefix}-{row.language}{row.number}")
        ui.download("\n".join(lines).encode('utf-8'), "import_failures.txt")

    def download_success_report(self):
        if not self.successful_imports: return
        lines = ["Quantity Name (Set - Rarity)"] + self.successful_imports
        ui.download("\n".join(lines).encode('utf-8'), "import_success.txt")

    def open_ambiguity_dialog(self):
        if not self.ambiguous_rows: return

        with ui.dialog() as dialog, ui.card().classes('w-full max-w-4xl bg-dark border border-gray-700'):
            ui.label("Resolve Ambiguities").classes('text-h6')

            # Helper to bulk resolve
            def set_all(type_idx):
                count = 0
                for item in self.ambiguous_rows:
                    row = item['row']
                    legacy_char = LANGUAGE_TO_LEGACY_REGION_MAP.get(row.language)

                    best_idx = -1
                    for idx, m in enumerate(item['matches']):
                        code = m['code']
                        parts = code.split('-')
                        if len(parts) < 2: continue
                        suffix = parts[1]

                        if type_idx == 0: # Standard (DE001)
                            if suffix.startswith(row.language): best_idx = idx
                        elif type_idx == 1 and legacy_char: # Legacy (G001)
                            if suffix.startswith(legacy_char) and not suffix.startswith(row.language): best_idx = idx
                        elif type_idx == 2: # No Region (001)
                            if suffix[0].isdigit(): best_idx = idx

                        if best_idx != -1:
                            item['selected_index'] = best_idx
                            count += 1

                dialog.close()
                self.open_ambiguity_dialog()
                ui.notify(f"Auto-selected {count} rows.")

            with ui.row().classes('gap-2 q-mb-md'):
                ui.button("Set All Standard (e.g. DE001)", on_click=lambda: set_all(0)).props('outline size=sm color=white')
                ui.button("Set All Legacy (e.g. G001)", on_click=lambda: set_all(1)).props('outline size=sm color=white')
                ui.button("Set All No-Region (e.g. 001)", on_click=lambda: set_all(2)).props('outline size=sm color=white')

            with ui.scroll_area().classes('h-96 w-full'):
                for item in self.ambiguous_rows:
                    row = item['row']
                    matches = item['matches']

                    with ui.row().classes('w-full items-center gap-4 q-mb-sm border-b border-gray-800 pb-2'):
                        ui.label(f"{row.quantity}x {row.name}").classes('font-bold w-1/4')
                        ui.label(f"{row.set_prefix} | {row.language}").classes('text-xs text-grey-4 w-1/4')

                        opts = {idx: f"{m['code']} ({m['variant'].set_rarity})" for idx, m in enumerate(matches)}
                        ui.select(options=opts, value=item['selected_index'],
                                  on_change=lambda e, it=item: it.update({'selected_index': e.value})) \
                                  .classes('w-1/3').props('dark dense')

            with ui.row().classes('w-full justify-end q-mt-md'):
                def confirm():
                    for item in self.ambiguous_rows:
                        idx = item['selected_index']
                        match = item['matches'][idx]
                        target = item.get('target_code')
                        self._add_pending_from_match(item['row'], match, override_set_code=target)
                    self.ambiguous_rows = []
                    self.refresh_status_ui()
                    dialog.close()
                ui.button("Confirm Resolution", on_click=confirm).classes('bg-primary text-white')

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
                ui.label(f"Ready to Import: {len(self.pending_changes)} items").classes('text-positive font-bold text-lg')

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
