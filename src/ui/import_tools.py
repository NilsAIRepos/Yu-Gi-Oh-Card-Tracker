from nicegui import ui, events
import json
import logging
from typing import Optional, List, Dict, Any
import asyncio

from src.core.persistence import persistence
from src.core.models import Collection
from src.services.ygo_api import ygo_service
from src.services.collection_editor import CollectionEditor
from src.ui.components.scanner_ui import ScannerUI

logger = logging.getLogger(__name__)

class ImportController:
    def __init__(self):
        self.collections: List[str] = []
        self.selected_collection: Optional[str] = None
        self.import_mode: str = 'ADD'
        self.undo_stack: List[Dict[str, Any]] = []

        self.collection_select = None
        self.undo_button = None

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


def import_tools_page():
    controller = ImportController()

    with ui.column().classes('w-full q-pa-md gap-4'):
        ui.label('Import Tools').classes('text-h4 q-mb-md')

        # Top Bar: Collection Selection
        with ui.card().classes('w-full bg-dark border border-gray-700 p-4'):
             with ui.row().classes('w-full items-center gap-4'):
                controller.collection_select = ui.select(
                    options=controller.collections,
                    label="Select Collection",
                    value=controller.selected_collection,
                    on_change=lambda e: setattr(controller, 'selected_collection', e.value)
                ).classes('w-64').props('dark')

                def open_new_collection_dialog():
                    with ui.dialog() as dialog, ui.card().classes('bg-dark border border-gray-700'):
                        ui.label('New Collection Name').classes('text-h6')
                        name_input = ui.input(placeholder='Collection Name').props('dark autofocus')
                        with ui.row().classes('w-full justify-end q-mt-md'):
                             ui.button('Cancel', on_click=dialog.close).props('flat color=grey')

                             async def create_click():
                                 await controller.create_new_collection(name_input.value)
                                 dialog.close()

                             ui.button('Create', on_click=create_click).classes('bg-accent text-dark')
                    dialog.open()

                ui.button('New Collection', on_click=open_new_collection_dialog, icon='add').classes('bg-secondary text-dark')

        # Tabs
        with ui.tabs().classes('w-full text-accent') as tabs:
            ui.tab('File Import')
            ui.tab('Camera Scan')

        with ui.tab_panels(tabs, value='File Import').classes('w-full bg-transparent p-0'):

            # PANEL 1: File Import
            with ui.tab_panel('File Import').classes('p-0'):
                with ui.card().classes('w-full bg-dark border border-gray-700 p-6'):
                    ui.label('JSON Import').classes('text-xl font-bold q-mb-md')

                    with ui.row().classes('items-center gap-6 q-mb-md'):
                        ui.label('Mode:').classes('text-lg')
                        with ui.row():
                            ui.radio(['ADD', 'SUBTRACT'], value='ADD', on_change=lambda e: setattr(controller, 'import_mode', e.value)).props('dark inline')

                    ui.upload(label='Drop JSON here', auto_upload=True, on_upload=controller.handle_import).props('dark accept=.json').classes('w-full')

                    # Undo Button (Initially Hidden)
                    controller.undo_button = ui.button('Undo Last Import', on_click=controller.undo_last, icon='undo') \
                        .classes('bg-red-500 text-white q-mt-md').props('flat')
                    controller.undo_button.visible = False

            # PANEL 2: Camera Scan
            with ui.tab_panel('Camera Scan').classes('p-0'):
                 def get_current_collection():
                     if not controller.selected_collection:
                         return None
                     return persistence.load_collection(controller.selected_collection)

                 scanner = ScannerUI(
                     collection_provider=get_current_collection,
                     on_collection_update=lambda: None
                 )
                 scanner.render()
