from nicegui import ui, events
import json
import logging
import asyncio
from typing import Optional, List, Dict, Any, Tuple

from src.core.persistence import persistence
from src.core.models import Collection, ApiCard, ApiCardSet
from src.core.utils import LANGUAGE_TO_LEGACY_REGION_MAP
from src.services.ygo_api import ygo_service
from src.services.collection_editor import CollectionEditor
from src.services.cardmarket_parser import CardmarketParser, ParsedRow

logger = logging.getLogger(__name__)

class CardmarketImportController:
    def __init__(self):
        self.rows: List[ParsedRow] = []
        self.ambiguous_rows: List[Dict[str, Any]] = [] # {row, matches, selected_index}
        self.failed_rows: List[ParsedRow] = []
        self.ready_rows: List[Dict[str, Any]] = [] # {row, card, variant, final_code}

        self.collections: List[str] = persistence.list_collections()
        self.selected_collection: Optional[str] = None

        self.undo_stack: List[Dict[str, Any]] = []

        # UI References
        self.collection_select = None
        self.undo_button = None
        self.preview_container = None
        self.ambiguity_dialog = None
        self.failure_container = None
        self.import_button = None

        # Resolvers
        self.db_lookup: Dict[str, List[Dict[str, Any]]] = {}

    def refresh_collections(self):
        self.collections = persistence.list_collections()
        if self.collection_select:
            self.collection_select.options = self.collections
            self.collection_select.update()

    async def process_file(self, e: events.UploadEventArguments):
        ui.notify("Parsing file...", type='info')

        # 1. Parse
        content = e.content.read()
        # Handle async content read if necessary (NiceGUI 1.4+ vs older)
        if asyncio.iscoroutine(content):
            content = await content

        try:
            # Run in thread/executor if heavy? parsing is text based, usually fast.
            # But PDF parsing might be slow.
            self.rows = await asyncio.to_thread(CardmarketParser.parse_file, content, e.name)
            ui.notify(f"Parsed {len(self.rows)} items. Resolving against database...", type='info')
        except Exception as ex:
            logger.error(f"Parser Error: {ex}")
            ui.notify(f"Error parsing file: {ex}", type='negative')
            return

        # 2. Resolve
        await self.resolve_rows()

        # 3. Update UI
        self.refresh_ui()

    async def resolve_rows(self):
        self.ambiguous_rows = []
        self.failed_rows = []
        self.ready_rows = []

        # Identify languages needed
        languages = set(row.language for row in self.rows)
        if not languages:
             languages = {'EN'}

        # Build DB Lookup
        # key: set_code (normalized/raw?) -> list of {rarity, card, variant}
        self.db_lookup = {}

        for lang in languages:
             # Map CM lang code to DB lang code (usually same, but ensure)
             # Our DB uses 'en', 'de', 'fr'. CM uses 'EN', 'DE'.
             db_lang = lang.lower()

             cards = await ygo_service.load_card_database(db_lang)
             for card in cards:
                 for s in card.card_sets:
                     code = s.set_code
                     entry = {'rarity': s.set_rarity, 'card': card, 'variant': s}

                     if code not in self.db_lookup:
                         self.db_lookup[code] = []

                     # Deduplicate based on variant_id to avoid multi-lang load issues if they overlap
                     exists = any(x['variant'].variant_id == s.variant_id for x in self.db_lookup[code])
                     if not exists:
                        self.db_lookup[code].append(entry)

        # Match
        for row in self.rows:
            candidates = []

            # 1. Standard: Prefix-LangNumber (e.g. LOB-DE020)
            std_code = f"{row.set_prefix}-{row.language}{row.number}"
            candidates.append(std_code)

            # 2. No Region: Prefix-Number (e.g. LOB-020)
            no_region_code = f"{row.set_prefix}-{row.number}"
            if no_region_code != std_code:
                 candidates.append(no_region_code)

            # 3. Legacy: Prefix-LegacyLangNumber (e.g. LOB-G020)
            legacy_char = LANGUAGE_TO_LEGACY_REGION_MAP.get(row.language)
            if legacy_char:
                legacy_code = f"{row.set_prefix}-{legacy_char}{row.number}"
                if legacy_code not in candidates:
                    candidates.append(legacy_code)

            potential_matches = []

            for code in candidates:
                if code in self.db_lookup:
                    # Filter by rarity
                    for entry in self.db_lookup[code]:
                        # Compare rarities
                        # row.set_rarity is full name (e.g. "Common")
                        # entry['rarity'] is full name
                        if entry['rarity'] == row.set_rarity:
                            potential_matches.append({
                                'code': code,
                                'card': entry['card'],
                                'variant': entry['variant']
                            })

            if len(potential_matches) == 1:
                self.ready_rows.append({
                    'row': row,
                    'card': potential_matches[0]['card'],
                    'variant': potential_matches[0]['variant'],
                    'final_code': potential_matches[0]['code']
                })
            elif len(potential_matches) > 1:
                # Check if all matches are actually the SAME variant (duplicate entries in DB?)
                # If variant_ids are same, it's fine.
                first_vid = potential_matches[0]['variant'].variant_id
                if all(m['variant'].variant_id == first_vid for m in potential_matches):
                     self.ready_rows.append({
                        'row': row,
                        'card': potential_matches[0]['card'],
                        'variant': potential_matches[0]['variant'],
                        'final_code': potential_matches[0]['code']
                    })
                else:
                    self.ambiguous_rows.append({
                        'row': row,
                        'matches': potential_matches,
                        'selected_index': 0 # Default first
                    })
            else:
                self.failed_rows.append(row)

    def refresh_ui(self):
        # Update Preview
        if self.preview_container:
            self.preview_container.clear()
            with self.preview_container:
                if self.ready_rows:
                     ui.label(f"Ready to Import: {len(self.ready_rows)} cards").classes('text-positive text-lg font-bold')
                     # Show sample?

                if self.ambiguous_rows:
                    ui.label(f"Ambiguous Items: {len(self.ambiguous_rows)}").classes('text-warning text-lg font-bold')
                    ui.button("Resolve Ambiguities", on_click=self.open_ambiguity_dialog).classes('bg-warning text-dark')

                if self.failed_rows:
                    ui.label(f"Failed Items: {len(self.failed_rows)}").classes('text-negative text-lg font-bold')

        # Update Import Button state
        if self.import_button:
            can_import = len(self.ready_rows) > 0 and len(self.ambiguous_rows) == 0
            self.import_button.enabled = can_import
            if not can_import and len(self.ambiguous_rows) > 0:
                 self.import_button.text = "Resolve Ambiguities First"
            else:
                 self.import_button.text = f"Import {len(self.ready_rows)} Cards"

        # Auto-open ambiguity dialog if needed?
        if self.ambiguous_rows:
            self.open_ambiguity_dialog()

    def open_ambiguity_dialog(self):
        if not self.ambiguous_rows:
            return

        with ui.dialog() as dialog, ui.card().classes('w-full max-w-4xl bg-dark border border-gray-700'):
            ui.label("Resolve Ambiguities").classes('text-h6')
            ui.label("The following cards have multiple potential set code matches. Please select the correct one.").classes('text-sm text-grey-4')

            # Global Actions
            with ui.row().classes('w-full gap-4 q-my-md'):
                def set_all(type_idx):
                    # type_idx: 0=Standard (2-letter), 1=Legacy (1-letter), 2=NoRegion
                    # This is heuristical.
                    count = 0
                    for item in self.ambiguous_rows:
                        # Find match that looks like the requested type
                        row_lang = item['row'].language
                        legacy_char = LANGUAGE_TO_LEGACY_REGION_MAP.get(row_lang)

                        best_idx = -1
                        for idx, m in enumerate(item['matches']):
                            code = m['code']
                            parts = code.split('-')
                            if len(parts) < 2: continue
                            suffix = parts[1]

                            # Standard: DE001
                            if type_idx == 0:
                                if suffix.startswith(row_lang):
                                    best_idx = idx
                                    break
                            # Legacy: G001
                            elif type_idx == 1 and legacy_char:
                                if suffix.startswith(legacy_char) and not suffix.startswith(row_lang): # Avoid EN vs E confusion if EN=E? No EN!=E.
                                    best_idx = idx
                                    break
                            # No Region: 001
                            elif type_idx == 2:
                                if suffix[0].isdigit():
                                    best_idx = idx
                                    break

                        if best_idx != -1:
                            item['selected_index'] = best_idx
                            count += 1

                    dialog.close()
                    self.open_ambiguity_dialog() # Re-render
                    ui.notify(f"Updated {count} rows.")

                ui.button("Set All Standard (e.g. DE020)", on_click=lambda: set_all(0)).props('outline size=sm')
                ui.button("Set All Legacy (e.g. G020)", on_click=lambda: set_all(1)).props('outline size=sm')
                ui.button("Set All No-Region (e.g. 020)", on_click=lambda: set_all(2)).props('outline size=sm')

            # List
            with ui.scroll_area().classes('h-96 w-full'):
                for i, item in enumerate(self.ambiguous_rows):
                    row: ParsedRow = item['row']
                    matches = item['matches']

                    with ui.row().classes('w-full items-center gap-4 q-mb-sm border-b border-gray-800 pb-2'):
                        ui.label(f"{row.quantity}x {row.name}").classes('font-bold w-1/4')
                        ui.label(f"{row.set_prefix} | {row.language} | {row.condition}").classes('text-xs text-grey-4 w-1/4')

                        options = {idx: f"{m['code']} ({m['variant'].set_rarity})" for idx, m in enumerate(matches)}

                        select = ui.select(
                            options=options,
                            value=item['selected_index'],
                            on_change=lambda e, it=item: it.update({'selected_index': e.value})
                        ).classes('w-1/3').props('dark dense')

            with ui.row().classes('w-full justify-end q-mt-md'):
                def confirm():
                    # Move ambiguous to ready
                    for item in self.ambiguous_rows:
                        idx = item['selected_index']
                        match = item['matches'][idx]
                        self.ready_rows.append({
                            'row': item['row'],
                            'card': match['card'],
                            'variant': match['variant'],
                            'final_code': match['code']
                        })
                    self.ambiguous_rows = []
                    self.refresh_ui()
                    dialog.close()

                ui.button("Confirm Resolution", on_click=confirm).classes('bg-primary text-white')

        dialog.open()

    async def apply_import(self):
        if not self.selected_collection:
             ui.notify("No collection selected", type='warning')
             return

        if not self.ready_rows:
             ui.notify("No rows to import", type='warning')
             return

        try:
            collection = persistence.load_collection(self.selected_collection)
        except Exception as e:
             ui.notify(f"Error loading collection: {e}", type='negative')
             return

        # Undo stack
        self.undo_stack.append({ # Shared undo stack? No, separate controller. We need to implement own undo or share.
             # We'll implement local undo for now.
             "filename": self.selected_collection,
             "data": collection.model_dump(mode='json')
        })

        changes = 0
        for item in self.ready_rows:
            row: ParsedRow = item['row']
            card: ApiCard = item['card']
            variant: ApiCardSet = item['variant']

            # Map condition
            # row.set_condition is the full name

            modified = CollectionEditor.apply_change(
                collection=collection,
                api_card=card,
                set_code=variant.set_code, # Use the matched code (which might differ from PDF if resolved)
                rarity=variant.set_rarity,
                language=row.language, # Use PDF language? Or Variant language? Variant doesn't strictly have language, the set code implies it.
                                       # Actually CollectionEntry has language. We should use the PDF language (e.g. DE).
                quantity=row.quantity,
                condition=row.set_condition,
                first_edition=row.first_edition,
                image_id=variant.image_id,
                mode='ADD'
            )
            if modified: changes += 1

        if changes > 0:
            persistence.save_collection(collection, self.selected_collection)
            ui.notify(f"Imported {changes} entries successfully.", type='positive')

            if self.undo_button:
                self.undo_button.visible = True
                self.undo_button.update()

            self.ready_rows = [] # Clear
            self.rows = []
            self.refresh_ui()
        else:
             ui.notify("No changes applied.", type='info')

    def undo_last(self):
        if not self.undo_stack:
            return

        state = self.undo_stack.pop()
        filename = state['filename']
        data = state['data']

        try:
            collection = Collection(**data)
            persistence.save_collection(collection, filename)
            ui.notify(f"Undid last import for {filename}", type='positive')

            if not self.undo_stack:
                if self.undo_button:
                    self.undo_button.visible = False
                    self.undo_button.update()
        except Exception as e:
            ui.notify(f"Error undoing: {e}", type='negative')

    def download_failures(self):
        if not self.failed_rows:
            return

        # Create text report
        lines = ["Original Line | Reason"]
        for row in self.failed_rows:
            lines.append(f"{row.original_line} | No matching set code found in DB for {row.set_prefix}-{row.language}{row.number} (Rarity: {row.set_rarity})")

        content = "\n".join(lines)
        ui.download(content.encode('utf-8'), "import_failures.txt")


class ImportController:
    def __init__(self):
        self.collections: List[str] = []
        self.selected_collection: Optional[str] = None
        self.import_mode: str = 'ADD'
        self.undo_stack: List[Dict[str, Any]] = []

        self.collection_select = None
        self.undo_button = None

        # We assume common collections list is managed by this controller or page?
        # In the original code, ImportController managed the list.
        self.refresh_collections()

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

    async def handle_import(self, e: events.UploadEventArguments):
        if not self.selected_collection:
            ui.notify("Please select a collection first", type='warning')
            return

        try:
            content = e.content.read()
            if asyncio.iscoroutine(content):
                content = await content

            logger.info(f"File content read: {len(content)} bytes")
            json_str = content.decode('utf-8')
            data = json.loads(json_str)
        except Exception as ex:
            ui.notify(f"Invalid JSON: {ex}", type='negative')
            return

        if "cards" not in data:
            ui.notify("Invalid JSON format: missing 'cards' list", type='negative')
            return

        # Load target collection
        try:
            collection = persistence.load_collection(self.selected_collection)
        except Exception as ex:
            ui.notify(f"Error loading collection: {ex}", type='negative')
            return

        # Save state for undo (deep copy via model_dump)
        self.undo_stack.append({
            "filename": self.selected_collection,
            "data": collection.model_dump(mode='json')
        })

        if self.undo_button:
            self.undo_button.visible = True
            self.undo_button.update()

        # Process
        changes_count = 0

        # Ensure DB is loaded for lookups
        logger.info("Loading card database...")
        await ygo_service.load_card_database()
        logger.info("Card database loaded.")

        for card_data in data.get("cards", []):
            card_id = card_data.get("card_id")
            if not card_id:
                continue

            api_card = ygo_service.get_card(card_id)
            if not api_card:
                logger.warning(f"Card {card_id} not found in database. Skipping.")
                continue

            # Default image ID if needed
            default_image_id = api_card.card_images[0].id if api_card.card_images else None

            for variant_data in card_data.get("variants", []):
                set_code = variant_data.get("set_code")
                rarity = variant_data.get("rarity")
                image_id = variant_data.get("image_id", default_image_id)

                if not set_code or not rarity:
                    continue

                for entry_data in variant_data.get("entries", []):
                    quantity = entry_data.get("quantity", 0)
                    condition = entry_data.get("condition", "Near Mint")
                    language = entry_data.get("language", "EN")
                    first_edition = entry_data.get("first_edition", False)

                    if quantity <= 0:
                        continue

                    # Adjust quantity for subtract mode
                    final_qty_change = quantity if self.import_mode == 'ADD' else -quantity

                    # Use CollectionEditor
                    modified = CollectionEditor.apply_change(
                        collection=collection,
                        api_card=api_card,
                        set_code=set_code,
                        rarity=rarity,
                        language=language,
                        quantity=final_qty_change,
                        condition=condition,
                        first_edition=first_edition,
                        image_id=image_id,
                        mode='ADD'
                    )

                    if modified:
                        changes_count += 1

        if changes_count > 0:
            persistence.save_collection(collection, self.selected_collection)
            ui.notify(f"Import successful. Processed {changes_count} updates.", type='positive')
        else:
            ui.notify("No changes applied.", type='info')

    def undo_last(self):
        if not self.undo_stack:
            return

        state = self.undo_stack.pop()
        filename = state['filename']
        data = state['data']

        try:
            # Restore
            collection = Collection(**data)
            persistence.save_collection(collection, filename)
            ui.notify(f"Undid last import for {filename}", type='positive')

            if not self.undo_stack:
                if self.undo_button:
                    self.undo_button.visible = False
                    self.undo_button.update()

        except Exception as e:
            ui.notify(f"Error undoing: {e}", type='negative')


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
        # Validate inputs
        if not self.coll_a or not self.coll_b:
            ui.notify("Please select two collections to merge.", type='warning')
            return

        if self.coll_a == self.coll_b:
            ui.notify("Cannot merge a collection into itself.", type='warning')
            return

        if not self.new_name.strip():
            ui.notify("Please enter a name for the new collection.", type='warning')
            return

        new_filename = f"{self.new_name.strip()}.json"
        if new_filename in self.collections:
            ui.notify(f"A collection named '{self.new_name}' already exists.", type='negative')
            return

        ui.notify("Starting merge process...", type='info')

        try:
            # Load Collections
            coll_a_obj = persistence.load_collection(self.coll_a)
            coll_b_obj = persistence.load_collection(self.coll_b)

            # Create New Collection
            new_collection = Collection(name=self.new_name.strip())

            # Ensure DB is loaded
            await ygo_service.load_card_database()

            # Helper function to merge a collection into the new one
            async def merge_into_new(source_coll: Collection):
                for card in source_coll.cards:
                    # We need the ApiCard for CollectionEditor
                    api_card = ygo_service.get_card(card.card_id)
                    if not api_card:
                         # Attempt to construct minimal ApiCard if missing from DB (should verify if this is safe)
                         # Fallback: create mock ApiCard if real one missing?
                         # Better to skip or log warning.
                         logger.warning(f"Card {card.card_id} not found in DB during merge. Skipping.")
                         continue

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

            # Merge A
            await merge_into_new(coll_a_obj)
            # Merge B
            await merge_into_new(coll_b_obj)

            # Save
            persistence.save_collection(new_collection, new_filename)

            ui.notify(f"Successfully created '{self.new_name}' with merged data.", type='positive')

            # Refresh lists
            self.refresh_collections()
            self.new_name = "" # Reset input

        except Exception as e:
            logger.error(f"Merge failed: {e}")
            ui.notify(f"Merge failed: {e}", type='negative')


def import_tools_page():
    # Instantiate controllers
    json_controller = ImportController()
    cm_controller = CardmarketImportController()
    merge_controller = MergeController()

    with ui.column().classes('w-full q-pa-md gap-4'):
        ui.label('Import Tools').classes('text-h4 q-mb-md')

        # Top Bar: Collection Selection (Shared Logic?)
        # Actually each section might want its own selection, OR we use a global selection for the page.
        # But controllers track their own `selected_collection`.
        # To avoid UI clutter, let's keep the original design: Selection block at top affecting JSON import.
        # But we need it to affect CM import too.
        # Let's link them or duplicate the selector?
        # Duplicating the selector for each "Tool" is cleaner if they are separate cards.
        # But typically you select a "Target Collection" for the page.
        # Let's make a shared selector? No, that requires shared state.
        # I'll add a selector inside the CM card too.

        # --- Section 1: JSON Import ---
        with ui.card().classes('w-full bg-dark border border-gray-700 p-6'):
            ui.label('JSON Backup Import').classes('text-xl font-bold q-mb-md')

            # Selector
            with ui.row().classes('items-center gap-4 q-mb-md'):
                 json_controller.collection_select = ui.select(
                    options=json_controller.collections,
                    label="Target Collection",
                    value=json_controller.selected_collection,
                    on_change=lambda e: setattr(json_controller, 'selected_collection', e.value)
                ).classes('w-64').props('dark')

                 # New Collection Button (Shared logic but hooked to this controller)
                 def open_new_collection_dialog_json():
                    with ui.dialog() as dialog, ui.card().classes('bg-dark border border-gray-700'):
                        ui.label('New Collection Name').classes('text-h6')
                        name_input = ui.input(placeholder='Collection Name').props('dark autofocus')
                        with ui.row().classes('w-full justify-end q-mt-md'):
                             ui.button('Cancel', on_click=dialog.close).props('flat color=grey')
                             async def create_click():
                                 await json_controller.create_new_collection(name_input.value)
                                 # Sync other controllers
                                 cm_controller.refresh_collections()
                                 merge_controller.refresh_collections()
                                 dialog.close()
                             ui.button('Create', on_click=create_click).classes('bg-accent text-dark')
                    dialog.open()
                 ui.button(on_click=open_new_collection_dialog_json, icon='add').props('round flat dense')

            with ui.row().classes('items-center gap-6 q-mb-md'):
                ui.label('Mode:').classes('text-lg')
                with ui.row():
                    ui.radio(['ADD', 'SUBTRACT'], value='ADD', on_change=lambda e: setattr(json_controller, 'import_mode', e.value)).props('dark inline')

            ui.upload(label='Drop JSON here', auto_upload=True, on_upload=json_controller.handle_import).props('dark accept=.json').classes('w-full')

            # Undo Button
            json_controller.undo_button = ui.button('Undo Last Import', on_click=json_controller.undo_last, icon='undo') \
                .classes('bg-red-500 text-white q-mt-md').props('flat')
            json_controller.undo_button.visible = False

        # --- Section 2: Cardmarket Import ---
        with ui.card().classes('w-full bg-dark border border-gray-700 p-6 q-mt-md'):
            ui.label('Cardmarket PDF/Text Import').classes('text-xl font-bold q-mb-md')
            ui.label('Import cards from Cardmarket export files (PDF) or text copy-paste.').classes('text-sm text-grey-4 q-mb-md')

            # Selector
            with ui.row().classes('items-center gap-4 q-mb-md'):
                 cm_controller.collection_select = ui.select(
                    options=persistence.list_collections(), # Init options
                    label="Target Collection",
                    value=cm_controller.selected_collection,
                    on_change=lambda e: setattr(cm_controller, 'selected_collection', e.value)
                ).classes('w-64').props('dark')

            ui.upload(
                label='Drop PDF or Text File',
                auto_upload=True,
                on_upload=cm_controller.process_file
            ).props('dark accept=".pdf, .txt"').classes('w-full')

            # Preview Area
            cm_controller.preview_container = ui.column().classes('w-full q-mt-md')

            # Failures Area
            cm_controller.failure_container = ui.column().classes('w-full q-mt-md')
            with cm_controller.failure_container:
                # Dynamic content
                pass

            # Import Action
            cm_controller.import_button = ui.button('Import', on_click=cm_controller.apply_import).classes('bg-primary text-white q-mt-md')
            cm_controller.import_button.enabled = False

            # Failed Download
            with ui.row().classes('w-full justify-end gap-4'):
                 cm_controller.undo_button = ui.button('Undo Last Import', on_click=cm_controller.undo_last, icon='undo') \
                    .classes('bg-red-500 text-white').props('flat')
                 cm_controller.undo_button.visible = False

                 ui.button("Download Failures Report", on_click=cm_controller.download_failures, icon='download').props('flat color=negative').bind_visibility_from(cm_controller, 'failed_rows', backward=lambda x: len(x) > 0)

        # --- Section 3: Merge ---
        with ui.card().classes('w-full bg-dark border border-gray-700 p-6 q-mt-md'):
            ui.label('Merge Collections').classes('text-xl font-bold q-mb-md')

            with ui.grid().classes('grid-cols-1 md:grid-cols-3 gap-4 w-full items-start'):
                # Collection A
                ui.select(
                    options=merge_controller.collections,
                    label="Collection A",
                    on_change=lambda e: setattr(merge_controller, 'coll_a', e.value)
                ).props('dark').classes('w-full')

                # Collection B
                ui.select(
                    options=merge_controller.collections,
                    label="Collection B",
                    on_change=lambda e: setattr(merge_controller, 'coll_b', e.value)
                ).props('dark').classes('w-full')

                # New Name
                ui.input(
                    label="New Collection Name",
                    placeholder="e.g. Master Collection",
                    on_change=lambda e: setattr(merge_controller, 'new_name', e.value)
                ).props('dark').classes('w-full')

            with ui.row().classes('w-full justify-end q-mt-md'):
                ui.button('Merge Collections', on_click=merge_controller.handle_merge, icon='merge_type').classes('bg-primary text-white')
