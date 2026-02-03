from nicegui import ui, run
from src.core.persistence import persistence
from src.core.changelog_manager import changelog_manager
from src.core.config import config_manager
from src.services.ygo_api import ygo_service, ApiCard
from src.services.image_manager import image_manager
from src.services.collection_editor import CollectionEditor
from src.core.utils import generate_variant_id, normalize_set_code, extract_language_code, transform_set_code, LANGUAGE_COUNTRY_MAP
from src.core.constants import CARD_CONDITIONS, CONDITION_ABBREVIATIONS
from src.ui.components.filter_pane import FilterPane
from src.ui.components.single_card_view import SingleCardView
from src.ui.components.structure_deck_dialog import StructureDeckDialog
from src.core.models import Collection
from dataclasses import dataclass, field
from typing import List, Optional, Any, Dict
import logging
import uuid
import asyncio
import re

logger = logging.getLogger(__name__)

@dataclass
class LibraryEntry:
    id: str # Unique ID for UI (card_id + variant hash)
    api_card: ApiCard
    set_code: str
    set_name: str
    rarity: str
    image_url: str
    image_id: int
    price: float = 0.0

@dataclass
class BulkCollectionEntry:
    id: str # Unique ID for UI
    api_card: ApiCard
    quantity: int
    set_code: str
    set_name: str
    rarity: str
    language: str
    condition: str
    first_edition: bool
    image_url: str
    image_id: int
    variant_id: str
    storage_location: Optional[str] = None
    price: float = 0.0

def _resolve_set_name(api_card: ApiCard, target_set_code: str) -> str:
    if not api_card or not api_card.card_sets:
        return "Unknown Set"

    # 1. Exact Match
    for s in api_card.card_sets:
         if s.set_code == target_set_code:
             return s.set_name

    # 2. Normalized Match
    target_norm = normalize_set_code(target_set_code)
    for s in api_card.card_sets:
        if normalize_set_code(s.set_code) == target_norm:
            return s.set_name

    return "Unknown Set"

def _build_collection_entries(col: Collection, api_card_map: Dict[int, ApiCard]) -> List[BulkCollectionEntry]:
    entries = []
    for card in col.cards:
        api_card = api_card_map.get(card.card_id)
        if not api_card: continue

        for variant in card.variants:
            img_id = variant.image_id if variant.image_id else (api_card.card_images[0].id if api_card.card_images else api_card.id)
            img_url = api_card.card_images[0].image_url_small if api_card.card_images else None
            if variant.image_id and api_card.card_images:
                for img in api_card.card_images:
                    if img.id == variant.image_id:
                        img_url = img.image_url_small
                        break

            set_name = _resolve_set_name(api_card, variant.set_code)

            for entry in variant.entries:
                # Include storage_location in ID to distinguish stacks
                loc_str = str(entry.storage_location) if entry.storage_location else "None"
                unique_id = f"{variant.variant_id}_{entry.language}_{entry.condition}_{entry.first_edition}_{loc_str}"
                entries.append(BulkCollectionEntry(
                    id=unique_id,
                    api_card=api_card,
                    quantity=entry.quantity,
                    set_code=variant.set_code,
                    set_name=set_name,
                    rarity=variant.rarity,
                    language=entry.language,
                    condition=entry.condition,
                    first_edition=entry.first_edition,
                    image_url=img_url,
                    image_id=img_id,
                    variant_id=variant.variant_id,
                    storage_location=entry.storage_location,
                    price=0.0
                ))
    return entries

class BulkEditPage:
    def __init__(self):
        # Global Metadata (shared)
        self.metadata = {
            'available_sets': [],
            'available_monster_races': [],
            'available_st_races': [],
            'available_archetypes': [],
            'available_card_types': ['Monster', 'Spell', 'Trap', 'Skill'],
        }

        # UI State
        default_lang = config_manager.get_language() or 'EN'
        if default_lang: default_lang = default_lang.upper()

        page_size = config_manager.get_bulk_add_page_size()

        self.state = {
            'selected_collection': None,
            'default_language': default_lang,
            'default_condition': 'Near Mint',
            'default_first_ed': False,
            'default_storage': None,
            'available_collections': [],

            # Library State
            'library_cards': [], # List[LibraryEntry]
            'library_filtered': [],
            'library_page': 1,
            'library_page_size': page_size,
            'library_total_pages': 1,
            'library_search_text': '',
            'library_sort_by': 'Name',
            'library_sort_desc': False,

            # Library Filters (FilterPane keys)
            'filter_set': '',
            'filter_rarity': '',
            'filter_attr': '',
            'filter_card_type': ['Monster', 'Spell', 'Trap'],
            'filter_monster_race': '',
            'filter_st_race': '',
            'filter_archetype': '',
            'filter_monster_category': [],
            'filter_level': None,
            'filter_atk_min': 0, 'filter_atk_max': 5000,
            'filter_def_min': 0, 'filter_def_max': 5000,
            'filter_price_min': 0.0, 'filter_price_max': 1000.0,
            'filter_ownership_min': 0, 'filter_ownership_max': 100,
            'filter_condition': [], 'filter_owned_lang': '',

            # Linking metadata to state for FilterPane
            **self.metadata
        }

        # Collection View State (Separate filter state)
        self.col_state = {
            'collection_cards': [], # List[BulkCollectionEntry]
            'collection_filtered': [],
            'collection_page': 1,
            'collection_page_size': page_size,
            'collection_total_pages': 1,
            'search_text': '', # mapped manually in apply
            'sort_by': 'Newest',
            'sort_desc': True,

            # Filters (standard keys for FilterPane)
            'filter_set': '',
            'filter_rarity': '',
            'filter_attr': '',
            'filter_card_type': ['Monster', 'Spell', 'Trap'],
            'filter_monster_race': '',
            'filter_st_race': '',
            'filter_archetype': '',
            'filter_monster_category': [],
            'filter_level': None,
            'filter_atk_min': 0, 'filter_atk_max': 5000,
            'filter_def_min': 0, 'filter_def_max': 5000,
            'filter_price_min': 0.0, 'filter_price_max': 1000.0,
            'filter_ownership_min': 0, 'filter_ownership_max': 100,
            'filter_condition': [], 'filter_owned_lang': '',
            'filter_storage': [], 'available_storage': [],

             # Metadata linking
            **self.metadata
        }

        self.single_card_view = SingleCardView()
        self.structure_deck_dialog = StructureDeckDialog(self.process_structure_deck_add)
        self.library_filter_pane = None
        self.collection_filter_pane = None
        self.current_collection_obj = None
        self.api_card_map = {} # id -> ApiCard
        self.set_code_map = {} # set_code (normalized or exact) -> ApiCard

        # Load available collections
        self.state['available_collections'] = persistence.list_collections()

        # Load UI state for persistence
        ui_state = persistence.load_ui_state()

        saved_col = ui_state.get('bulk_selected_collection')
        if saved_col and saved_col in self.state['available_collections']:
             self.state['selected_collection'] = saved_col
        elif self.state['available_collections']:
             self.state['selected_collection'] = self.state['available_collections'][0]

        # Load defaults
        self.state['default_language'] = ui_state.get('bulk_default_lang', self.state['default_language'])
        self.state['default_condition'] = ui_state.get('bulk_default_cond', self.state['default_condition'])
        self.state['default_first_ed'] = ui_state.get('bulk_default_first', self.state['default_first_ed'])
        self.state['default_storage'] = ui_state.get('bulk_default_storage', self.state['default_storage'])

        # Load update options
        self.state['update_apply_lang'] = ui_state.get('bulk_update_apply_lang', False)
        self.state['update_apply_cond'] = ui_state.get('bulk_update_apply_cond', False)
        self.state['update_apply_first'] = ui_state.get('bulk_update_apply_first', False)
        self.state['update_apply_storage'] = ui_state.get('bulk_update_apply_storage', False)

        # Load sort preferences
        self.state['library_sort_by'] = ui_state.get('bulk_library_sort_by', self.state['library_sort_by'])
        self.state['library_sort_desc'] = ui_state.get('bulk_library_sort_desc', self.state['library_sort_desc'])
        self.col_state['sort_by'] = ui_state.get('bulk_collection_sort_by', self.col_state['sort_by'])
        self.col_state['sort_desc'] = ui_state.get('bulk_collection_sort_desc', self.col_state['sort_desc'])

        self.save_task = None

    async def _perform_save(self):
        try:
            if self.current_collection_obj and self.state['selected_collection']:
                 await run.io_bound(persistence.save_collection, self.current_collection_obj, self.state['selected_collection'])
                 logger.info(f"Debounced save complete for {self.state['selected_collection']}")
        except Exception as e:
            logger.error(f"Error in debounced save: {e}")
            ui.notify(f"Save Failed: {e}", type='negative')
        finally:
            self.save_task = None

    def _schedule_save(self):
        if self.save_task:
            self.save_task.cancel()

        async def delayed_save():
            try:
                await asyncio.sleep(2.0) # 2 seconds debounce
                await self._perform_save()
            except asyncio.CancelledError:
                pass

        self.save_task = asyncio.create_task(delayed_save())

    async def reset_library_filters(self):
        # Reset State
        s = self.state
        s['library_search_text'] = ''
        s['filter_set'] = ''
        s['filter_rarity'] = ''
        s['filter_attr'] = ''
        s['filter_card_type'] = ['Monster', 'Spell', 'Trap']
        s['filter_monster_race'] = ''
        s['filter_st_race'] = ''
        s['filter_archetype'] = ''
        s['filter_monster_category'] = []
        s['filter_level'] = None
        s['filter_atk_min'] = 0
        s['filter_atk_max'] = 5000
        s['filter_def_min'] = 0
        s['filter_def_max'] = 5000
        s['filter_price_min'] = 0.0
        s['filter_price_max'] = 1000.0
        s['filter_ownership_min'] = 0
        s['filter_ownership_max'] = 100
        s['filter_condition'] = []
        s['filter_owned_lang'] = ''
        s['filter_storage'] = []

        # Reset UI
        if self.library_filter_pane:
            self.library_filter_pane.reset_ui_elements()

        # Apply
        await self.apply_library_filters()

        # Force update of search input if bound
        # Note: Since we updated s['library_search_text'], if the input is bound, it should update.
        # However, for search inputs, we usually bind explicitly or use on_change.
        # In build_ui: ui.input(..., on_change=on_search)
        # It is NOT bound to value. We need to find the element and set value or force update.
        # I'll handle this in build_ui by assigning the input to a variable.

    async def reset_collection_filters(self):
        # Reset State
        s = self.col_state
        s['search_text'] = ''
        s['filter_set'] = ''
        s['filter_rarity'] = ''
        s['filter_attr'] = ''
        s['filter_card_type'] = ['Monster', 'Spell', 'Trap']
        s['filter_monster_race'] = ''
        s['filter_st_race'] = ''
        s['filter_archetype'] = ''
        s['filter_monster_category'] = []
        s['filter_level'] = None
        s['filter_atk_min'] = 0
        s['filter_atk_max'] = 5000
        s['filter_def_min'] = 0
        s['filter_def_max'] = 5000
        s['filter_price_min'] = 0.0
        s['filter_price_max'] = 1000.0
        s['filter_ownership_min'] = 0
        s['filter_ownership_max'] = 100
        s['filter_condition'] = []
        s['filter_owned_lang'] = ''

        # Reset UI
        if self.collection_filter_pane:
            self.collection_filter_pane.reset_ui_elements()

        # Apply
        await self.apply_collection_filters()

    async def _update_collection(self, api_card, set_code, rarity, lang, qty, cond, first, img_id, mode='ADD', variant_id=None, save=True, storage_location=None):
        if not self.current_collection_obj or not self.state['selected_collection']:
            return False

        try:
            # Ensure variant exists in global DB (using app language)
            await ygo_service.ensure_card_variant(
                card_id=api_card.id,
                set_code=set_code,
                set_rarity=rarity,
                image_id=img_id,
                language=config_manager.get_language().lower()
            )

            modified = CollectionEditor.apply_change(
                collection=self.current_collection_obj,
                api_card=api_card,
                set_code=set_code,
                rarity=rarity,
                language=lang,
                quantity=qty,
                condition=cond,
                first_edition=first,
                image_id=img_id,
                variant_id=variant_id,
                mode=mode,
                storage_location=storage_location
            )

            if modified:
                # Update View Model directly
                await self._update_view_model(api_card, set_code, rarity, lang, qty, cond, first, img_id, variant_id, mode, storage_location)

                if save:
                    self._schedule_save()
                return True
            return False
        except Exception as e:
            logger.error(f"Error updating collection: {e}")
            ui.notify(f"Error: {e}", type='negative')
            return False

    async def _update_view_model(self, api_card, set_code, rarity, lang, qty, cond, first, img_id, variant_id, mode, storage_location=None):
        # We need to replicate the ID logic
        if not variant_id:
            variant_id = generate_variant_id(api_card.id, set_code, rarity, img_id)

        loc_str = str(storage_location) if storage_location else "None"
        unique_id = f"{variant_id}_{lang}_{cond}_{first}_{loc_str}"

        cards = self.col_state['collection_cards']

        target_index = -1
        for i, entry in enumerate(cards):
            if entry.id == unique_id:
                target_index = i
                break

        if target_index != -1:
            entry = cards[target_index]
            if mode == 'SET':
                entry.quantity = qty
            else:
                entry.quantity += qty

            if entry.quantity <= 0:
                cards.pop(target_index)
        else:
            if mode == 'SET' and qty > 0:
                new_qty = qty
            elif mode == 'ADD' and qty > 0:
                new_qty = qty
            else:
                new_qty = 0

            if new_qty > 0:
                # Create new entry
                set_name = _resolve_set_name(api_card, set_code)

                # Image URL
                img_url = api_card.card_images[0].image_url_small if api_card.card_images else None
                if img_id and api_card.card_images:
                    for img in api_card.card_images:
                        if img.id == img_id:
                            img_url = img.image_url_small
                            break

                new_entry = BulkCollectionEntry(
                    id=unique_id,
                    api_card=api_card,
                    quantity=new_qty,
                    set_code=set_code,
                    set_name=set_name,
                    rarity=rarity,
                    language=lang,
                    condition=cond,
                    first_edition=first,
                    image_url=img_url,
                    image_id=img_id,
                    variant_id=variant_id,
                    storage_location=storage_location,
                    price=0.0
                )
                cards.insert(0, new_entry) # Add to top

        # Refresh View (preserve page)
        await self.apply_collection_filters(reset_page=False)

    async def undo_last_action(self):
        col_name = self.state['selected_collection']
        if not col_name: return

        last_change = changelog_manager.undo_last_change(col_name)
        if last_change:
            # Handle Batch Undo
            if last_change.get('type') == 'batch':
                changes = last_change.get('changes', [])
                count = 0
                for c in changes:
                    action = c['action']
                    qty = c['quantity']
                    data = c['card_data']

                    revert_qty = -qty if action == 'ADD' else qty

                    api_card = self.api_card_map.get(data['card_id'])
                    if api_card:
                        await self._update_collection(
                            api_card=api_card,
                            set_code=data['set_code'],
                            rarity=data['rarity'],
                            lang=data['language'],
                            qty=revert_qty,
                            cond=data['condition'],
                            first=data['first_edition'],
                            img_id=data['image_id'],
                            variant_id=data.get('variant_id'),
                            mode='ADD',
                            save=False
                        )
                        count += 1

                if count > 0:
                    await run.io_bound(persistence.save_collection, self.current_collection_obj, self.state['selected_collection'])

                ui.notify(f"Undid batch: {last_change.get('description')} ({count} items)", type='positive')
                self.render_header.refresh()
                await self.refresh_collection_view_from_memory()
                return

            # Revert single logic
            action = last_change['action']
            qty = last_change['quantity']
            data = last_change['card_data']

            # If action was ADD, we REMOVE (mode='ADD' with negative qty)
            # If action was REMOVE, we ADD
            revert_qty = -qty if action == 'ADD' else qty

            # API Card retrieval
            api_card = self.api_card_map.get(data['card_id'])
            if not api_card:
                ui.notify("Error: Card data missing from database.", type='negative')
                return

            success = await self._update_collection(
                api_card=api_card,
                set_code=data['set_code'],
                rarity=data['rarity'],
                lang=data['language'],
                qty=revert_qty,
                cond=data['condition'],
                first=data['first_edition'],
                img_id=data['image_id'],
                variant_id=data.get('variant_id'),
                mode='ADD'
            )

            if success:
                ui.notify(f"Undid: {action} {qty}x {data.get('name')}", type='positive')
                self.render_header.refresh()
            else:
                ui.notify("Undo failed (no changes made).", type='warning')
        else:
            ui.notify("Nothing to undo.", type='warning')

    async def process_structure_deck_add(self, deck_name: str, cards: List[Dict[str, Any]]):
        if not self.current_collection_obj or not self.state['selected_collection']:
            ui.notify("No collection selected", type='negative')
            return

        defaults = {
            'lang': self.state['default_language'],
            'cond': self.state['default_condition'],
            'first': self.state['default_first_ed'],
            'storage': self.state['default_storage']
        }

        processed_changes = []
        added_count = 0

        # We need to perform all additions in memory first, then save once.
        # But _update_collection saves every time.
        # Ideally, we should update the in-memory object multiple times and then save once.
        # However, _update_collection logic is coupled with persistence.
        # Refactoring _update_collection to support a 'save=False' flag would be best.
        # For now, I will modify _update_collection locally or override behavior.
        # Actually, let's just create a modified version or use CollectionEditor directly and save at the end.

        collection = self.current_collection_obj

        for card_info in cards:
            set_code = card_info['set_code']
            qty = card_info['quantity']
            rarity = card_info['rarity']

            # Find ApiCard
            api_card = self.set_code_map.get(set_code)

            # If not found by exact match, try normalized
            if not api_card:
                 # Check if the set code exists in our known sets?
                 # If the card is not in our DB, we skip it as per instructions.
                 logger.warning(f"Card {set_code} not found in local DB. Skipping.")
                 continue

            # Determine Image ID
            # Look for the specific set variant in api_card
            image_id = None
            variant_id = None

            if api_card.card_sets:
                for s in api_card.card_sets:
                    if s.set_code == set_code:
                        image_id = s.image_id
                        variant_id = s.variant_id
                        break

            if not image_id and api_card.card_images:
                image_id = api_card.card_images[0].id

            # Transform Set Code
            final_set_code = transform_set_code(set_code, defaults['lang'])
            if final_set_code != set_code:
                # If set code changed, we cannot reuse the variant_id from the original set code
                variant_id = None

            # Apply Change In-Memory
            CollectionEditor.apply_change(
                collection=collection,
                api_card=api_card,
                set_code=final_set_code,
                rarity=rarity,
                language=defaults['lang'],
                quantity=qty,
                condition=defaults['cond'],
                first_edition=defaults['first'],
                image_id=image_id,
                variant_id=variant_id,
                storage_location=defaults['storage'],
                mode='ADD'
            )

            # Prepare log entry
            # Need variant_id if it was generated/found
            if not variant_id:
                 variant_id = generate_variant_id(api_card.id, final_set_code, rarity, image_id)

            processed_changes.append({
                'action': 'ADD',
                'quantity': qty,
                'card_data': {
                    'card_id': api_card.id,
                    'name': api_card.name,
                    'set_code': final_set_code,
                    'rarity': rarity,
                    'image_id': image_id,
                    'language': defaults['lang'],
                    'condition': defaults['cond'],
                    'first_edition': defaults['first'],
                    'variant_id': variant_id
                }
            })
            added_count += qty

        if processed_changes:
            # Save Collection
            await run.io_bound(persistence.save_collection, collection, self.state['selected_collection'])

            # Log Batch
            changelog_manager.log_batch_change(
                self.state['selected_collection'],
                f"Imported {deck_name}",
                processed_changes
            )

            ui.notify(f"Added {added_count} cards from {deck_name}", type='positive')
            self.render_header.refresh()
            await self.refresh_collection_view_from_memory()
        else:
            ui.notify("No valid cards found to add (check database update?)", type='warning')

    async def add_to_staging(self, entry):
        # Check if already in staging
        exists = any(x.id == entry.id for x in self.col_state['collection_cards'])
        if exists:
            ui.notify("Item already in Edit Batch", type='warning')
            return

        # Add copy to Staging
        from dataclasses import replace
        new_entry = replace(entry)
        self.col_state['collection_cards'].insert(0, new_entry)

        await self.apply_collection_filters(reset_page=False)
        ui.notify("Added to Edit Batch", type='positive')

    async def remove_from_staging(self, entry: BulkCollectionEntry):
        if entry in self.col_state['collection_cards']:
            self.col_state['collection_cards'].remove(entry)
            await self.apply_collection_filters(reset_page=False)
            ui.notify("Removed from Edit Batch", type='info')

    async def handle_drop(self, e):
        detail = e.args.get('detail', {})
        data_id = detail.get('data_id')
        from_id = detail.get('from_id')
        to_id = detail.get('to_id')

        if not data_id: return

        # ADD: Library (Source) -> Collection (Staging)
        if from_id == 'library-list' and to_id == 'collection-list':
             entry = next((item for item in self.state['library_filtered'] if item.id == data_id), None)
             if not entry: return

             # Check if already in staging
             exists = any(x.id == entry.id for x in self.col_state['collection_cards'])
             if exists:
                 ui.notify("Item already in Edit Batch", type='warning')
                 # Remove ghost element from DOM if sortable added it
                 self.render_collection_content.refresh()
                 return

             # Add copy to Staging
             from dataclasses import replace
             new_entry = replace(entry)
             self.col_state['collection_cards'].insert(0, new_entry)

             await self.apply_collection_filters(reset_page=False)

        # REMOVE: Collection (Staging) -> Library (Source)
        elif from_id == 'collection-list' and to_id == 'library-list':
             entry = next((item for item in self.col_state['collection_cards'] if item.id == data_id), None)
             if not entry: return

             # Remove from Staging
             self.col_state['collection_cards'].remove(entry)

             await self.apply_collection_filters(reset_page=False)

        # REORDER/REFRESH: Collection -> Collection
        elif from_id == 'collection-list' and to_id == 'collection-list':
            self.render_collection_content.refresh()

    # ... [Previous methods: on_collection_change, _setup_card_tooltip, load_library_data, apply_library_filters, etc.]
    # (I will include the full class content in write_file to ensure consistency)

    async def process_batch_update(self, entries: List[BulkCollectionEntry]):
        if not self.current_collection_obj or not self.state['selected_collection']:
            return

        # Check what we are updating
        apply_lang = self.state.get('update_apply_lang', False)
        apply_cond = self.state.get('update_apply_cond', False)
        apply_first = self.state.get('update_apply_first', False)
        apply_storage = self.state.get('update_apply_storage', False)

        if not (apply_lang or apply_cond or apply_first or apply_storage):
            ui.notify("No update options selected.", type='warning')
            return

        defaults = {
            'lang': self.state['default_language'],
            'cond': self.state['default_condition'],
            'first': self.state['default_first_ed'],
            'storage': self.state['default_storage']
        }

        processed_changes = []
        updated_count = 0
        collection = self.current_collection_obj

        # Pre-process to ensure new variants exist if set code changes due to language
        variants_to_ensure = []
        for entry in entries:
            new_lang = defaults['lang'] if apply_lang else entry.language
            final_set_code = transform_set_code(entry.set_code, new_lang)
            if final_set_code != entry.set_code:
                 variants_to_ensure.append({
                    'card_id': entry.api_card.id,
                    'set_code': final_set_code,
                    'set_rarity': entry.rarity,
                    'image_id': entry.image_id
                })

        if variants_to_ensure:
            await ygo_service.ensure_card_variants(variants_to_ensure, language=config_manager.get_language().lower())

        for entry in entries:
            # Determine new values
            new_lang = defaults['lang'] if apply_lang else entry.language
            new_cond = defaults['cond'] if apply_cond else entry.condition
            new_first = defaults['first'] if apply_first else entry.first_edition
            new_storage = defaults['storage'] if apply_storage else entry.storage_location

            # Check if any change is actually needed
            if (new_lang == entry.language and
                new_cond == entry.condition and
                new_first == entry.first_edition and
                new_storage == entry.storage_location):
                continue

            qty = entry.quantity
            if qty <= 0: continue

            # REMOVE OLD
            CollectionEditor.apply_change(
                collection=collection,
                api_card=entry.api_card,
                set_code=entry.set_code,
                rarity=entry.rarity,
                language=entry.language,
                quantity=-qty,
                condition=entry.condition,
                first_edition=entry.first_edition,
                image_id=entry.image_id,
                variant_id=entry.variant_id,
                mode='ADD',
                storage_location=entry.storage_location
            )

            # ADD NEW
            final_set_code = transform_set_code(entry.set_code, new_lang)
            final_variant_id = entry.variant_id

            if final_set_code != entry.set_code:
                # Set code changed (e.g. EN -> DE), so we need a new variant ID
                final_variant_id = None

            CollectionEditor.apply_change(
                collection=collection,
                api_card=entry.api_card,
                set_code=final_set_code,
                rarity=entry.rarity,
                language=new_lang,
                quantity=qty,
                condition=new_cond,
                first_edition=new_first,
                image_id=entry.image_id,
                variant_id=final_variant_id,
                mode='ADD',
                storage_location=new_storage
            )

            if not final_variant_id:
                final_variant_id = generate_variant_id(entry.api_card.id, final_set_code, entry.rarity, entry.image_id)

            # Update Staging Entry In-Place
            entry.language = new_lang
            entry.condition = new_cond
            entry.first_edition = new_first
            entry.storage_location = new_storage
            entry.set_code = final_set_code
            entry.variant_id = final_variant_id

            # Recalculate ID for UI
            loc_str = str(entry.storage_location) if entry.storage_location else "None"
            entry.id = f"{final_variant_id}_{new_lang}_{new_cond}_{new_first}_{loc_str}"

            processed_changes.append({
                'action': 'UPDATE',
                'quantity': qty,
                'card_data': {
                    'card_id': entry.api_card.id,
                    'name': entry.api_card.name,
                    'set_code': final_set_code,
                    'rarity': entry.rarity,
                    'image_id': entry.image_id,
                    'language': new_lang,
                    'condition': new_cond,
                    'first_edition': new_first,
                    'variant_id': final_variant_id,
                    'storage_location': new_storage
                },
                'old_data': {
                    'language': entry.language,
                    'condition': entry.condition,
                    'first_edition': entry.first_edition,
                    'storage_location': entry.storage_location
                }
            })
            updated_count += qty

        if processed_changes:
            await run.io_bound(persistence.save_collection, collection, self.state['selected_collection'])

            changelog_manager.log_batch_change(
                self.state['selected_collection'],
                f"Bulk Updated {len(processed_changes)} stacks",
                processed_changes
            )

            ui.notify(f"Updated {len(processed_changes)} entries", type='positive')
            self.render_header.refresh()

            # Refresh Source (Left)
            await self.refresh_collection_view_from_memory()

            # Refresh Staging (Right) with updated values
            await self.apply_collection_filters(reset_page=False)
        else:
            ui.notify("No cards required updates.", type='info')

    async def on_update_all_click(self):
        count = len(self.col_state['collection_filtered'])
        if count == 0:
            ui.notify("No cards to update.", type='warning')
            return

        if not (self.state.get('update_apply_lang') or
                self.state.get('update_apply_cond') or
                self.state.get('update_apply_first') or
                self.state.get('update_apply_storage')):
            ui.notify("Select at least one property to update (Lang, Cond, 1st, or Storage).", type='warning')
            return

        async def execute():
             await self.process_batch_update(self.col_state['collection_filtered'])

        updates = []
        if self.state.get('update_apply_lang'): updates.append(f"Language -> {self.state['default_language']}")
        if self.state.get('update_apply_cond'): updates.append(f"Condition -> {self.state['default_condition']}")
        if self.state.get('update_apply_first'): updates.append(f"1st Ed -> {'Yes' if self.state['default_first_ed'] else 'No'}")
        if self.state.get('update_apply_storage'): updates.append(f"Storage -> {self.state['default_storage'] or 'None'}")

        update_str = ", ".join(updates)

        with ui.dialog() as d, ui.card():
             ui.label("Confirm Bulk Update").classes('text-h6')
             ui.label(f"Update {count} filtered entries?")
             ui.label(f"Applying: {update_str}").classes('font-bold text-accent')

             with ui.row().classes('justify-end'):
                 ui.button("Cancel", on_click=d.close).props('flat')
                 async def on_confirm():
                     d.close()
                     await asyncio.sleep(0.2)
                     if not self.is_collection_filtered():
                         await self.show_double_confirmation(count, execute, "UPDATE ALL")
                     else:
                         await execute()
                 ui.button("Update All", on_click=on_confirm).props('color=warning')
        d.open()

    async def process_batch_add(self, entries: List[LibraryEntry]):
        # This is for Structure Decks or Bulk Add, might not be used here but good to keep or remove.
        # Removing to clean up as it's not part of Bulk Edit scope (adding new cards from API)
        pass

    async def process_batch_remove(self, entries: List[BulkCollectionEntry]):
        if not self.current_collection_obj or not self.state['selected_collection']:
            return

        processed_changes = []
        removed_count = 0
        collection = self.current_collection_obj

        for entry in entries:
            qty_to_remove = entry.quantity
            if qty_to_remove <= 0: continue

            CollectionEditor.apply_change(
                collection=collection,
                api_card=entry.api_card,
                set_code=entry.set_code,
                rarity=entry.rarity,
                language=entry.language,
                quantity=-qty_to_remove,
                condition=entry.condition,
                first_edition=entry.first_edition,
                image_id=entry.image_id,
                variant_id=entry.variant_id,
                mode='ADD'
            )

            processed_changes.append({
                'action': 'REMOVE',
                'quantity': qty_to_remove,
                'card_data': {
                    'card_id': entry.api_card.id,
                    'name': entry.api_card.name,
                    'set_code': entry.set_code,
                    'rarity': entry.rarity,
                    'image_id': entry.image_id,
                    'language': entry.language,
                    'condition': entry.condition,
                    'first_edition': entry.first_edition,
                    'variant_id': entry.variant_id
                }
            })
            removed_count += qty_to_remove

        if processed_changes:
            await run.io_bound(persistence.save_collection, collection, self.state['selected_collection'])

            changelog_manager.log_batch_change(
                self.state['selected_collection'],
                f"Bulk Removed {len(processed_changes)} entries",
                processed_changes
            )

            # Remove from Staging
            for entry in entries:
                 if entry in self.col_state['collection_cards']:
                     self.col_state['collection_cards'].remove(entry)

            ui.notify(f"Removed {len(processed_changes)} entries", type='positive')
            self.render_header.refresh()

            # Refresh Source
            await self.refresh_collection_view_from_memory()

            # Refresh Staging
            await self.apply_collection_filters(reset_page=False)
        else:
            ui.notify("No cards to remove.", type='warning')

    async def clear_staging_area(self):
        self.col_state['collection_cards'] = []
        await self.apply_collection_filters()
        ui.notify("Edit Batch Cleared", type='positive')

    def is_library_filtered(self):
        s = self.state
        if s['library_search_text'].strip(): return True
        if s['filter_set']: return True
        if s['filter_rarity']: return True
        if s['filter_attr']: return True
        if s['filter_monster_race']: return True
        if s['filter_st_race']: return True
        if s['filter_archetype']: return True
        if s['filter_monster_category']: return True
        if s['filter_level'] is not None: return True

        if s['filter_atk_min'] > 0 or s['filter_atk_max'] < 5000: return True
        if s['filter_def_min'] > 0 or s['filter_def_max'] < 5000: return True
        if s['filter_price_min'] > 0.0 or s['filter_price_max'] < 1000.0: return True
        if s['filter_ownership_min'] > 0 or s['filter_ownership_max'] < 100: return True

        if set(s['filter_card_type']) != {'Monster', 'Spell', 'Trap'}: return True
        if s['filter_condition']: return True
        if s['filter_owned_lang']: return True
        if s['filter_storage']: return True

        return False

    def is_collection_filtered(self):
        s = self.col_state
        if s['search_text'].strip(): return True
        if s['filter_set']: return True
        if s['filter_rarity']: return True
        if s['filter_attr']: return True
        if s['filter_monster_race']: return True
        if s['filter_st_race']: return True
        if s['filter_archetype']: return True
        if s['filter_monster_category']: return True
        if s['filter_level'] is not None: return True

        if s['filter_atk_min'] > 0 or s['filter_atk_max'] < 5000: return True
        if s['filter_def_min'] > 0 or s['filter_def_max'] < 5000: return True
        if s['filter_price_min'] > 0.0 or s['filter_price_max'] < 1000.0: return True
        if s['filter_ownership_min'] > 0 or s['filter_ownership_max'] < 100: return True

        if set(s['filter_card_type']) != {'Monster', 'Spell', 'Trap'}: return True
        if s['filter_condition']: return True
        if s['filter_owned_lang']: return True

        return False

    async def show_double_confirmation(self, count, callback, action_label):
        self.warning_dialog.clear()
        with self.warning_dialog, ui.card():
             ui.label("WARNING: NO FILTERS ACTIVE").classes('text-h6 text-red font-bold')
             ui.label(f"You are about to {action_label} {count} items (ENTIRE SET).")
             ui.label("This cannot be easily undone.")
             with ui.row().classes('justify-end'):
                 ui.button("Cancel", on_click=self.warning_dialog.close).props('flat')
                 async def on_ok():
                     self.warning_dialog.close()
                     await callback()
                 ui.button(f"I UNDERSTAND - {action_label}", on_click=on_ok).props('color=red')
        self.warning_dialog.open()

    async def on_add_all_click(self):
        count = len(self.state['library_filtered'])
        if count == 0:
            ui.notify("No cards to add.", type='warning')
            return

        async def execute():
             await self.process_batch_add(self.state['library_filtered'])

        with ui.dialog() as d, ui.card():
             ui.label("Confirm Bulk Add").classes('text-h6')
             ui.label(f"Add {count} filtered cards to collection?")
             with ui.row().classes('justify-end'):
                 ui.button("Cancel", on_click=d.close).props('flat')
                 async def on_confirm():
                     d.close()
                     await asyncio.sleep(0.2)
                     if not self.is_library_filtered():
                         await self.show_double_confirmation(count, execute, "ADD ALL")
                     else:
                         await execute()
                 ui.button("Add", on_click=on_confirm).props('color=positive')
        d.open()

    async def on_remove_all_click(self):
        count = len(self.col_state['collection_filtered'])
        if count == 0:
            ui.notify("No cards to remove.", type='warning')
            return

        async def execute():
             await self.process_batch_remove(self.col_state['collection_filtered'])

        with ui.dialog() as d, ui.card():
             ui.label("Confirm Bulk Remove").classes('text-h6')
             ui.label(f"Remove {count} filtered entries from collection?")
             ui.label("(This will set their quantity to 0)").classes('text-caption text-gray-400')
             with ui.row().classes('justify-end'):
                 ui.button("Cancel", on_click=d.close).props('flat')
                 async def on_confirm():
                     d.close()
                     await asyncio.sleep(0.2)
                     if not self.is_collection_filtered():
                         await self.show_double_confirmation(count, execute, "REMOVE ALL")
                     else:
                         await execute()
                 ui.button("Remove All", on_click=on_confirm).props('color=negative')
        d.open()

    async def on_collection_change(self, new_val):
        self.state['selected_collection'] = new_val
        persistence.save_ui_state({'bulk_selected_collection': new_val})
        self.render_header.refresh()
        await self.load_collection_data()

    def _setup_card_tooltip(self, card: ApiCard, specific_image_id: int = None):
        if not card: return
        target_img = card.card_images[0] if card.card_images else None
        if specific_image_id and card.card_images:
            for img in card.card_images:
                if img.id == specific_image_id:
                    target_img = img
                    break
        if not target_img: return

        img_id = target_img.id
        high_res_url = target_img.image_url
        low_res_url = target_img.image_url_small
        is_local = image_manager.image_exists(img_id, high_res=True)
        initial_src = f"/images/{img_id}_high.jpg" if is_local else (high_res_url or low_res_url)

        with ui.tooltip().classes('bg-transparent shadow-none border-none p-0 overflow-visible z-[9999] max-w-none').props('style="max-width: none" delay=5000') as tooltip:
            if initial_src:
                ui.image(initial_src).classes('w-auto h-[65vh] min-w-[1000px] object-contain rounded-lg shadow-2xl').props('fit=contain')
            if not is_local and high_res_url:
                async def ensure_high():
                    if not image_manager.image_exists(img_id, high_res=True):
                         await image_manager.ensure_image(img_id, high_res_url, high_res=True)
                tooltip.on('show', ensure_high)

    async def load_library_data(self):
        try:
            logger.info("Starting load_library_data (Bulk Edit)")
            lang_code = config_manager.get_language().lower()

            # 1. Load API Database (Reference)
            api_cards = await ygo_service.load_card_database(lang_code)
            self.api_card_map = {c.id: c for c in api_cards}
            logger.info(f"Loaded {len(api_cards)} cards into API map")

            # Build Set Code Map
            self.set_code_map = {}
            for c in api_cards:
                if c.card_sets:
                    for s in c.card_sets:
                        self.set_code_map[s.set_code] = c

            # Build Metadata (Sets, Races, etc.) from Full DB for Filters
            sets = set()
            m_races = set()
            st_races = set()
            archetypes = set()

            for c in api_cards:
                if c.card_sets:
                    for s in c.card_sets:
                        sets.add(f"{s.set_name} | {s.set_code.split('-')[0] if '-' in s.set_code else s.set_code}")
                if c.archetype: archetypes.add(c.archetype)
                if "Monster" in c.type: m_races.add(c.race)
                elif "Spell" in c.type or "Trap" in c.type:
                    if c.race: st_races.add(c.race)

            self.metadata['available_sets'][:] = sorted(list(sets))
            self.metadata['available_monster_races'][:] = sorted(list(m_races))
            self.metadata['available_st_races'][:] = sorted(list(st_races))
            self.metadata['available_archetypes'][:] = sorted(list(archetypes))

            for k, v in self.metadata.items():
                self.state[k] = v
                self.col_state[k] = v

            # 2. Load Collection (Source for Left Side)
            if not self.state['selected_collection']:
                 self.state['library_cards'] = []
                 logger.warning("No collection selected")
            else:
                 try:
                     col = await run.io_bound(persistence.load_collection, self.state['selected_collection'])
                     self.current_collection_obj = col

                     # Build Entries for LEFT side (Library)
                     entries = await run.io_bound(_build_collection_entries, col, self.api_card_map)
                     self.state['library_cards'] = entries

                     # Populate available storage options
                     storage_opts = ['None']
                     if col.storage_definitions:
                        storage_opts.extend(sorted([s.name for s in col.storage_definitions]))
                     self.state['available_storage'] = storage_opts
                     self.col_state['available_storage'] = storage_opts # Also for right side

                 except Exception as e:
                     logger.error(f"Failed to load collection for source: {e}")
                     ui.notify(f"Failed to load collection: {e}", type='negative')
                     self.state['library_cards'] = []

            # 3. Initialize Staging Area (Right Side) - Empty by default
            # We do NOT load collection into right side.
            if not self.col_state['collection_cards']:
                self.col_state['collection_cards'] = []

            logger.info("Data loaded, applying filters")
            await self.apply_library_filters()
            await self.apply_collection_filters()

            if self.library_filter_pane: self.library_filter_pane.update_options()
            if self.collection_filter_pane: self.collection_filter_pane.update_options()

            self.render_header.refresh()
            logger.info("Initialization complete")

        except Exception as e:
            logger.exception("Error in load_library_data")
            ui.notify(f"Error loading data: {e}", type='negative')

    async def refresh_collection_view_from_memory(self):
        # In Bulk Edit, "Refreshing Collection View" usually means refreshing the SOURCE (Left Side).
        # But we also have the Staging Area (Right Side).
        # After an update, the Source (Left) should reflect changes.
        # The Staging (Right) items should also update their displayed values if we want them to stay valid.

        if not self.current_collection_obj:
            return

        # Reload Source (Left)
        entries = await run.io_bound(_build_collection_entries, self.current_collection_obj, self.api_card_map)
        self.state['library_cards'] = entries
        await self.apply_library_filters()

        # We generally preserve the Staging Area (Right) state unless explicit clear.
        # However, if items in Staging were updated, their objects in memory (referenced in staging)
        # *might* need refreshing if they were deep-copied or if we want to show new state.
        # The 'process_batch_update' logic usually updates the objects in place if possible,
        # or we might need to re-fetch/re-match them.
        # For now, we refresh the Left Side (Source).
        pass

    async def load_collection_data(self):
        # In Bulk Edit, this is replaced by logic in load_library_data.
        # We keep this method empty or redirect to load_library_data to avoid breaking calls
        # (like on_collection_change calling it).
        await self.load_library_data()

    async def apply_library_filters(self):
        source = self.state['library_cards']
        res = list(source)
        txt = self.state['library_search_text'].lower()
        if txt:
            def matches(e: LibraryEntry):
                return (txt in e.api_card.name.lower() or
                        txt in e.set_code.lower() or
                        txt in e.set_name.lower() or
                        txt in e.api_card.desc.lower())
            res = [e for e in res if matches(e)]

        s = self.state
        if s['filter_card_type']: res = [e for e in res if any(t in e.api_card.type for t in s['filter_card_type'])]
        if s['filter_attr']: res = [e for e in res if e.api_card.attribute == s['filter_attr']]
        if s['filter_monster_race']: res = [e for e in res if "Monster" in e.api_card.type and e.api_card.race == s['filter_monster_race']]
        if s['filter_st_race']: res = [e for e in res if ("Spell" in e.api_card.type or "Trap" in e.api_card.type) and e.api_card.race == s['filter_st_race']]
        if s['filter_archetype']: res = [e for e in res if e.api_card.archetype == s['filter_archetype']]
        if s['filter_set']:
             target = s['filter_set'].split('|')[0].strip().lower()
             res = [e for e in res if target in e.set_name.lower() or target in e.set_code.lower()]
        if s['filter_rarity']:
             target = s['filter_rarity'].lower()
             res = [e for e in res if e.rarity.lower() == target]
        if s['filter_monster_category']:
             cats = s['filter_monster_category']
             res = [e for e in res if any(e.api_card.matches_category(cat) for cat in cats)]
        if s['filter_level'] is not None:
             res = [e for e in res if e.api_card.level == int(s['filter_level'])]
        atk_min, atk_max = s['filter_atk_min'], s['filter_atk_max']
        if atk_min > 0 or atk_max < 5000:
             res = [e for e in res if e.api_card.atk is not None and atk_min <= int(e.api_card.atk) <= atk_max]
        def_min, def_max = s['filter_def_min'], s['filter_def_max']
        if def_min > 0 or def_max < 5000:
             res = [e for e in res if e.api_card.def_ is not None and def_min <= int(e.api_card.def_) <= def_max]
        p_min, p_max = s['filter_price_min'], s['filter_price_max']
        if p_min > 0 or p_max < 1000:
             res = [e for e in res if p_min <= e.price <= p_max]

        key = s['library_sort_by']
        reverse = s['library_sort_desc']
        if key == 'Name': res.sort(key=lambda x: x.api_card.name, reverse=reverse)
        elif key == 'ATK': res.sort(key=lambda x: (x.api_card.atk or -1), reverse=reverse)
        elif key == 'DEF': res.sort(key=lambda x: (getattr(x.api_card, 'def_', None) or -1), reverse=reverse)
        elif key == 'Level': res.sort(key=lambda x: (x.api_card.level or -1), reverse=reverse)
        elif key == 'Price': res.sort(key=lambda x: x.price, reverse=reverse)
        elif key == 'Set Code': res.sort(key=lambda x: x.set_code, reverse=reverse)
        elif key == 'Newest': res.sort(key=lambda x: x.api_card.id, reverse=reverse)

        self.state['library_filtered'] = res
        self.state['library_page'] = 1
        self.update_library_pagination()
        self.render_library_content.refresh()

    def update_library_pagination(self):
        count = len(self.state['library_filtered'])
        self.state['library_total_pages'] = max(1, (count + self.state['library_page_size'] - 1) // self.state['library_page_size'])

    async def load_collection_data(self):
        if not self.state['selected_collection']:
            self.col_state['collection_cards'] = []
            await self.apply_collection_filters()
            return

        try:
            col = await run.io_bound(persistence.load_collection, self.state['selected_collection'])
            self.current_collection_obj = col

            storage_opts = ['None']
            if col.storage_definitions:
                storage_opts.extend(sorted([s.name for s in col.storage_definitions]))
            self.col_state['available_storage'] = storage_opts

            self.render_header.refresh()
        except Exception as e:
            logger.error(f"Failed to load collection: {e}")
            ui.notify(f"Failed to load collection: {e}", type='negative')
            return

        entries = await run.io_bound(_build_collection_entries, col, self.api_card_map)
        self.col_state['collection_cards'] = entries
        await self.apply_collection_filters()
        if self.collection_filter_pane: self.collection_filter_pane.update_options()

    async def apply_collection_filters(self, reset_page=True):
        source = self.col_state['collection_cards']
        res = list(source)
        s = self.col_state

        txt = s['search_text'].lower()
        if txt:
            def matches(e: BulkCollectionEntry):
                return (txt in e.api_card.name.lower() or
                        txt in e.set_code.lower() or
                        txt in e.api_card.desc.lower())
            res = [e for e in res if matches(e)]

        if s['filter_card_type']: res = [e for e in res if any(t in e.api_card.type for t in s['filter_card_type'])]
        if s['filter_attr']: res = [e for e in res if e.api_card.attribute == s['filter_attr']]
        if s['filter_monster_race']: res = [e for e in res if "Monster" in e.api_card.type and e.api_card.race == s['filter_monster_race']]
        if s['filter_st_race']: res = [e for e in res if ("Spell" in e.api_card.type or "Trap" in e.api_card.type) and e.api_card.race == s['filter_st_race']]
        if s['filter_archetype']: res = [e for e in res if e.api_card.archetype == s['filter_archetype']]
        if s['filter_set']:
             target = s['filter_set'].split('|')[0].strip().lower()
             res = [e for e in res if target in e.set_name.lower() or target in e.set_code.lower()]
        if s['filter_rarity']:
             target = s['filter_rarity'].lower()
             res = [e for e in res if e.rarity.lower() == target]
        if s['filter_monster_category']:
             cats = s['filter_monster_category']
             res = [e for e in res if any(e.api_card.matches_category(cat) for cat in cats)]
        if s['filter_owned_lang']:
             res = [e for e in res if e.language == s['filter_owned_lang']]
        if s['filter_condition']:
             res = [e for e in res if e.condition in s['filter_condition']]
        if s['filter_storage']:
             selected = set(s['filter_storage'])
             def match_storage(e):
                 loc = e.storage_location if e.storage_location else 'None'
                 return loc in selected
             res = [e for e in res if match_storage(e)]

        key = s['sort_by']
        reverse = s['sort_desc']
        if key == 'Name': res.sort(key=lambda x: x.api_card.name, reverse=reverse)
        elif key == 'ATK': res.sort(key=lambda x: (x.api_card.atk or -1), reverse=reverse)
        elif key == 'DEF': res.sort(key=lambda x: (getattr(x.api_card, 'def_', None) or -1), reverse=reverse)
        elif key == 'Level': res.sort(key=lambda x: (x.api_card.level or -1), reverse=reverse)
        elif key == 'Set Code': res.sort(key=lambda x: x.set_code, reverse=reverse)
        elif key == 'Quantity': res.sort(key=lambda x: x.quantity, reverse=reverse)
        elif key == 'Newest': res.sort(key=lambda x: x.api_card.id, reverse=reverse)

        self.col_state['collection_filtered'] = res
        if reset_page:
            self.col_state['collection_page'] = 1
        self.update_collection_pagination()
        self.render_collection_content.refresh()

    def update_collection_pagination(self):
        count = len(self.col_state['collection_filtered'])
        self.col_state['collection_total_pages'] = max(1, (count + self.col_state['collection_page_size'] - 1) // self.col_state['collection_page_size'])

    async def open_single_view_library(self, entry: LibraryEntry):
        async def on_save(card, set_code, rarity, language, quantity, condition, first_edition, image_id, variant_id, mode, storage_location=None, **kwargs):
             success = await self._update_collection(
                 api_card=card,
                 set_code=set_code,
                 rarity=rarity,
                 lang=language,
                 qty=quantity,
                 cond=condition,
                 first=first_edition,
                 img_id=image_id,
                 mode=mode,
                 variant_id=variant_id,
                 storage_location=storage_location
             )

             if success:
                 card_data = {
                    'card_id': card.id,
                    'name': card.name,
                    'set_code': set_code,
                    'rarity': rarity,
                    'image_id': image_id,
                    'language': language,
                    'condition': condition,
                    'first_edition': first_edition,
                    'variant_id': variant_id,
                    'storage_location': storage_location
                 }
                 # For logging, if mode is SET, we might need to know delta.
                 # But simplistic logging: just log the action.
                 # Undo might be tricky for SET if we don't know previous state.
                 # User said "undo functionality for the last couple actions! (adding or removing)".
                 # SET implies manual inventory management. Undo support for complex SET is harder.
                 # We'll log it as generic update or try to infer.
                 # For "Add new versions", usually it's ADD.
                 changelog_manager.log_change(self.state['selected_collection'], mode, card_data, quantity)

                 ui.notify('Collection updated.', type='positive')

        await self.single_card_view.open_collectors(
            card=entry.api_card,
            owned_count=0,
            set_code=entry.set_code,
            rarity=entry.rarity,
            set_name=entry.set_name,
            language=self.state['default_language'],
            condition=self.state['default_condition'],
            first_edition=self.state['default_first_ed'],
            image_url=entry.image_url,
            image_id=entry.image_id,
            set_price=entry.price,
            current_collection=self.current_collection_obj,
            save_callback=on_save,
            hide_header_stats=True
        )

    async def open_single_view_collection(self, entry: BulkCollectionEntry):
        async def on_save(card, set_code, rarity, language, quantity, condition, first_edition, image_id, variant_id, mode, storage_location=None, **kwargs):
             success = await self._update_collection(
                 api_card=card,
                 set_code=set_code,
                 rarity=rarity,
                 lang=language,
                 qty=quantity,
                 cond=condition,
                 first=first_edition,
                 img_id=image_id,
                 mode=mode,
                 variant_id=variant_id,
                 storage_location=storage_location
             )

             if success:
                 card_data = {
                    'card_id': card.id,
                    'name': card.name,
                    'set_code': set_code,
                    'rarity': rarity,
                    'image_id': image_id,
                    'language': language,
                    'condition': condition,
                    'first_edition': first_edition,
                    'variant_id': variant_id,
                    'storage_location': storage_location
                 }
                 changelog_manager.log_change(self.state['selected_collection'], mode, card_data, quantity)

                 ui.notify('Collection updated.', type='positive')

        await self.single_card_view.open_collectors(
            card=entry.api_card,
            owned_count=entry.quantity,
            set_code=entry.set_code,
            rarity=entry.rarity,
            set_name=entry.set_name,
            language=entry.language,
            condition=entry.condition,
            first_edition=entry.first_edition,
            image_url=entry.image_url,
            image_id=entry.image_id,
            set_price=entry.price,
            current_collection=self.current_collection_obj,
            save_callback=on_save,
            variant_id=entry.variant_id,
            hide_header_stats=False
        )

    def open_new_collection_dialog(self):
        with ui.dialog() as d, ui.card().classes('w-96 bg-gray-900 border border-gray-700'):
            ui.label('Create New Collection').classes('text-h6')

            name_input = ui.input('Collection Name').classes('w-full').props('autofocus')

            async def create():
                name = name_input.value.strip()
                if not name:
                    ui.notify('Please enter a name.', type='warning')
                    return

                # Ensure extension
                if not name.endswith(('.json', '.yaml', '.yml')):
                    name += '.json'

                # Check if exists
                existing = persistence.list_collections()
                if name in existing:
                    ui.notify(f'Collection "{name}" already exists.', type='negative')
                    return

                # Create empty collection
                new_col = Collection(name=name.replace('.json', '').replace('.yaml', '').replace('.yml', ''), cards=[])
                try:
                    await run.io_bound(persistence.save_collection, new_col, name)
                    ui.notify(f'Collection "{name}" created.', type='positive')

                    # Update state
                    self.state['available_collections'] = persistence.list_collections()
                    self.state['selected_collection'] = name
                    persistence.save_ui_state({'bulk_selected_collection': name})

                    d.close()
                    # Reload header and data
                    self.render_header.refresh()
                    await self.load_collection_data()

                except Exception as e:
                    logger.error(f"Error creating collection: {e}")
                    ui.notify(f"Error creating collection: {e}", type='negative')

            with ui.row().classes('w-full justify-end q-mt-md'):
                ui.button('Cancel', on_click=lambda: [d.close(), self.render_header.refresh()]).props('flat')
                ui.button('Create', on_click=create).props('color=positive')
        d.open()

    @ui.refreshable
    def render_header(self):
        with ui.row().classes('w-full items-center gap-4 p-4 bg-gray-900 rounded-lg border border-gray-800 mb-4 shadow-lg'):
             with ui.column().classes('gap-0'):
                ui.label('Bulk Edit').classes('text-h5 font-bold leading-none')
                ui.label('Drag cards to Edit Batch to modify or remove them').classes('text-xs text-gray-400')

             ui.separator().props('vertical')

             cols = {c: c.replace('.json', '').replace('.yaml', '') for c in self.state['available_collections']}
             cols['__NEW_COLLECTION__'] = '+ New Collection'

             async def handle_col_change(e):
                 if e.value == '__NEW_COLLECTION__':
                     self.open_new_collection_dialog()
                 else:
                     await self.on_collection_change(e.value)

             ui.select(cols, label='Target Collection', value=self.state['selected_collection'],
                       on_change=handle_col_change).classes('w-48')

             ui.separator().props('vertical')

             storage_opts = {None: 'None'}
             if self.current_collection_obj:
                 for s in self.current_collection_obj.storage_definitions:
                     storage_opts[s.name] = s.name

             # Validate default_storage against current options to prevent ValueError
             if self.state['default_storage'] not in storage_opts:
                 self.state['default_storage'] = None

             with ui.row().classes('items-center gap-2 bg-gray-800 p-2 rounded border border-gray-700'):
                 ui.label('Defaults:').classes('text-accent font-bold text-xs uppercase mr-2')
                 ui.select(['EN', 'DE', 'FR', 'IT', 'ES', 'PT', 'JP', 'KR'], label='Lang',
                           value=self.state['default_language'],
                           on_change=lambda e: [self.state.update({'default_language': e.value}), persistence.save_ui_state({'bulk_default_lang': e.value})]).props('dense options-dense').classes('w-20')
                 ui.select(CARD_CONDITIONS, label='Cond',
                           value=self.state['default_condition'],
                           on_change=lambda e: [self.state.update({'default_condition': e.value}), persistence.save_ui_state({'bulk_default_cond': e.value})]).props('dense options-dense').classes('w-32')
                 ui.checkbox('1st Ed', value=self.state['default_first_ed'],
                             on_change=lambda e: [self.state.update({'default_first_ed': e.value}), persistence.save_ui_state({'bulk_default_first': e.value})]).props('dense')

                 ui.select(storage_opts, label='Storage',
                           value=self.state['default_storage'],
                           on_change=lambda e: [self.state.update({'default_storage': e.value}), persistence.save_ui_state({'bulk_default_storage': e.value})]).props('dense options-dense').classes('w-32')

             ui.space()

             # Add Structure Deck Button
             ui.button("Add Structure Deck", icon="library_add", on_click=self.structure_deck_dialog.open).props('flat color=accent')

             has_history = False
             if self.state['selected_collection']:
                 last = changelog_manager.get_last_change(self.state['selected_collection'])
                 has_history = last is not None
             btn = ui.button('Undo Last', icon='undo', on_click=self.undo_last_action).props('flat color=white')
             if not has_history:
                 btn.disable()
                 btn.classes('opacity-50')
             else:
                 with btn: ui.tooltip('Undo the last add/remove action')

    @ui.refreshable
    def render_library_content(self):
        start = (self.state['library_page'] - 1) * self.state['library_page_size']
        end = min(start + self.state['library_page_size'], len(self.state['library_filtered']))
        items = self.state['library_filtered'][start:end]

        url_map = {}
        for item in items:
            if item.image_url: url_map[item.image_id] = item.image_url
        if url_map:
            asyncio.create_task(image_manager.download_batch(url_map, concurrency=5))

        if not items:
            ui.label('No cards found.').classes('text-gray-500 italic w-full text-center mt-10')
            return

        with ui.grid(columns='repeat(auto-fill, minmax(110px, 1fr))').classes('w-full gap-2 p-2').props('id="library-list"'):
            for item in items:
                img_src = f"/images/{item.image_id}.jpg" if image_manager.image_exists(item.image_id) else item.image_url

                cond_short = CONDITION_ABBREVIATIONS.get(item.condition, item.condition[:2].upper())

                with ui.card().classes('p-0 cursor-pointer hover:scale-105 transition-transform border border-gray-800 w-full aspect-[2/3] select-none') \
                        .props(f'data-id="{item.id}"') \
                        .on('click', lambda i=item: self.open_single_view_library(i)) \
                        .on('contextmenu.prevent', lambda i=item: self.add_to_staging(i)):

                    with ui.element('div').classes('relative w-full h-full'):
                         ui.image(img_src).classes('w-full h-full object-cover')

                         lang_code = item.language.strip().upper()
                         country_code = LANGUAGE_COUNTRY_MAP.get(lang_code)
                         if country_code:
                             ui.element('img').props(f'src="https://flagcdn.com/h24/{country_code}.png" alt="{lang_code}"').classes('absolute top-[1px] left-[1px] h-4 w-6 shadow-black drop-shadow-md rounded bg-black/30')
                         else:
                             ui.label(lang_code).classes('absolute top-[1px] left-[1px] text-xs font-bold shadow-black drop-shadow-md bg-black/30 rounded px-1')

                         ui.label(f"{item.quantity}").classes('absolute top-1 right-1 bg-accent text-dark font-bold px-2 rounded-full text-xs shadow-md')

                         with ui.column().classes('absolute bottom-0 left-0 w-full bg-black/80 p-0.5 gap-0'):
                             ui.label(item.api_card.name).classes('text-[9px] font-bold text-white leading-none truncate w-full')
                             with ui.row().classes('w-full justify-between items-center'):
                                 with ui.row().classes('gap-1'):
                                     ui.label(cond_short).classes('font-bold text-yellow-500')
                                     if item.first_edition:
                                         ui.label('1st').classes('font-bold text-orange-400')
                                 ui.label(item.set_code).classes('font-mono text-[9px] truncate')
                             with ui.row().classes('w-full justify-between items-center gap-1'):
                                 ui.label(item.rarity).classes('text-[8px] text-gray-300 leading-none truncate')
                                 ui.label(item.storage_location or "None").classes('text-[8px] text-gray-400 font-mono truncate flex-shrink text-right')

                    self._setup_card_tooltip(item.api_card, specific_image_id=item.image_id)

        # putMode = true to allow dropping from collection (to remove)
        ui.run_javascript('initSortable("library-list", "shared", "clone", true)')

    @ui.refreshable
    def render_collection_content(self):
        start = (self.col_state['collection_page'] - 1) * self.col_state['collection_page_size']
        end = min(start + self.col_state['collection_page_size'], len(self.col_state['collection_filtered']))
        items = self.col_state['collection_filtered'][start:end]

        url_map = {}
        for item in items:
            if item.image_url: url_map[item.image_id] = item.image_url
        if url_map:
            asyncio.create_task(image_manager.download_batch(url_map, concurrency=5))

        if not items:
            ui.label('Collection is empty or no matches.').classes('text-gray-500 italic w-full text-center mt-10')
            return

        with ui.grid(columns='repeat(auto-fill, minmax(110px, 1fr))').classes('w-full gap-2 p-2').props('id="collection-list"'):
            for item in items:
                img_src = f"/images/{item.image_id}.jpg" if image_manager.image_exists(item.image_id) else item.image_url

                cond_short = CONDITION_ABBREVIATIONS.get(item.condition, item.condition[:2].upper())

                with ui.card().classes('p-0 cursor-pointer hover:scale-105 transition-transform border border-accent w-full aspect-[2/3] select-none') \
                        .props(f'data-id="{item.id}"') \
                        .on('click', lambda i=item: self.open_single_view_collection(i)) \
                        .on('contextmenu.prevent', lambda i=item: self.remove_from_staging(i)):

                    with ui.element('div').classes('relative w-full h-full'):
                         ui.image(img_src).classes('w-full h-full object-cover')

                         lang_code = item.language.strip().upper()
                         country_code = LANGUAGE_COUNTRY_MAP.get(lang_code)
                         if country_code:
                             ui.element('img').props(f'src="https://flagcdn.com/h24/{country_code}.png" alt="{lang_code}"').classes('absolute top-[1px] left-[1px] h-4 w-6 shadow-black drop-shadow-md rounded bg-black/30')
                         else:
                             ui.label(lang_code).classes('absolute top-[1px] left-[1px] text-xs font-bold shadow-black drop-shadow-md bg-black/30 rounded px-1')

                         ui.label(f"{item.quantity}").classes('absolute top-1 right-1 bg-accent text-dark font-bold px-2 rounded-full text-xs shadow-md')

                         with ui.column().classes('absolute bottom-0 left-0 bg-black/80 text-white text-[9px] px-1 gap-0 w-full'):
                             ui.label(item.api_card.name).classes('text-[9px] font-bold text-white leading-none truncate w-full')
                             with ui.row().classes('w-full justify-between items-center'):
                                 with ui.row().classes('gap-1'):
                                     ui.label(cond_short).classes('font-bold text-yellow-500')
                                     if item.first_edition:
                                         ui.label('1st').classes('font-bold text-orange-400')
                                 ui.label(item.set_code).classes('font-mono')
                             with ui.row().classes('w-full justify-between items-center gap-1'):
                                 ui.label(item.rarity).classes('text-[8px] text-gray-300 truncate flex-shrink')
                                 ui.label(item.storage_location or "None").classes('text-[8px] text-gray-400 font-mono truncate flex-shrink text-right')

                    self._setup_card_tooltip(item.api_card, specific_image_id=item.image_id)

        ui.run_javascript('initSortable("collection-list", "shared", true, true)')

    def build_ui(self):
        ui.add_head_html('<script src="https://cdnjs.cloudflare.com/ajax/libs/Sortable/1.15.0/Sortable.min.js"></script>')
        ui.add_head_html('<style>.sortable-ghost-custom { opacity: 0.5; }</style>')
        ui.add_body_html('''
            <script>
            window.initSortable = function(elementId, groupName, pullMode, putMode) {
                var el = document.getElementById(elementId);
                if (!el) return;
                if (el._sortable) el._sortable.destroy();

                el._sortable = new Sortable(el, {
                    group: { name: groupName, pull: pullMode, put: putMode },
                    animation: 150,
                    sort: true,
                    ghostClass: 'sortable-ghost-custom',
                    forceFallback: true,
                    fallbackTolerance: 3,
                    onClone: function (evt) { evt.clone.removeAttribute('id'); },
                    onEnd: function (evt) {
                        // Fix for context menu not working after drag (restore original element with events to source)
                        if (pullMode === 'clone' && evt.item && evt.clone) {
                            if (evt.to !== evt.from && evt.clone.parentNode === evt.from) {
                                evt.from.replaceChild(evt.item, evt.clone);
                            }
                        }
                    },
                    onAdd: function (evt) {
                         var itemEl = evt.item;
                         var fromId = evt.from.id;
                         var toId = evt.to.id;
                         var dataId = itemEl.getAttribute('data-id');

                         var container = document.getElementById('bulk-add-container');
                         if (container) {
                             container.dispatchEvent(new CustomEvent('card_drop', {
                                 detail: {
                                     data_id: dataId,
                                     from_id: fromId,
                                     to_id: toId
                                 },
                                 bubbles: true
                             }));
                         }
                         // For drops into library (from collection) or collection (from library), we remove the element visually
                         // so it doesn't stay as a "ghost" with incorrect events/state while the backend processes.
                         if (toId === 'library-list' || toId === 'collection-list') {
                             itemEl.remove();
                         }
                    }
                });
            }
            </script>
        ''')

        self.library_filter_dialog = ui.dialog().props('position=right')
        with self.library_filter_dialog, ui.card().classes('h-full w-96 bg-gray-900 border-l border-gray-700 p-0 flex flex-col'):
             with ui.scroll_area().classes('flex-grow w-full'):
                 self.library_filter_pane = FilterPane(self.state, self.apply_library_filters, self.reset_library_filters)
                 self.library_filter_pane.build()

        self.collection_filter_dialog = ui.dialog().props('position=right')
        with self.collection_filter_dialog, ui.card().classes('h-full w-96 bg-gray-900 border-l border-gray-700 p-0 flex flex-col'):
             with ui.scroll_area().classes('flex-grow w-full'):
                 self.collection_filter_pane = FilterPane(self.col_state, self.apply_collection_filters, self.reset_collection_filters)
                 self.collection_filter_pane.build()

        self.warning_dialog = ui.dialog()
        self.render_header()

        with ui.row().classes('w-full h-[calc(100vh-140px)] gap-4 flex-nowrap relative z-[60]').props('id="bulk-add-container"').on('card_drop', self.handle_drop):
            # Left: Library (Collection Source)
            with ui.column().classes('w-1/2 h-full bg-dark border border-gray-800 rounded flex flex-col overflow-hidden'):
                # Header
                with ui.row().classes('w-full p-2 bg-gray-900 border-b border-gray-800 items-center justify-between gap-2 flex-nowrap overflow-x-auto'):
                    ui.label('My Collection').classes('text-h6 font-bold')
                    with ui.row().classes('items-center gap-1 flex-nowrap'):
                        # ui.button("Add All", on_click=self.on_add_all_click).props('flat dense color=positive size=sm')
                        # "Add All" might be too dangerous/heavy if we duplicate thousands of cards to staging.
                        # Maybe remove it or rename to "Add All to Edit" but user didn't explicitly ask for it.
                        # I'll leave it but commented out or just not include it if not asked.
                        # Actually, "Add All" from Bulk Add adds to collection. Here it adds to Staging.
                        # Adding 1000 cards to Staging is fine.

                        ui.separator().props('vertical')

                        ui.input(placeholder='Search...',
                                 on_change=lambda e: self.apply_library_filters()) \
                            .bind_value(self.state, 'library_search_text') \
                            .props('dense borderless dark debounce=300') \
                            .classes('w-52 text-sm')

                        ui.separator().props('vertical')

                        # Pagination
                        async def change_page(delta):
                             new_p = max(1, min(self.state['library_total_pages'], self.state['library_page'] + delta))
                             if new_p != self.state['library_page']:
                                 self.state['library_page'] = new_p
                                 self.render_library_content.refresh()
                        ui.button(icon='chevron_left', on_click=lambda: change_page(-1)).props('flat dense color=white size=sm')
                        ui.label().bind_text_from(self.state, 'library_page', lambda p: f"{p}/{self.state['library_total_pages']}").classes('text-xs font-mono')
                        ui.button(icon='chevron_right', on_click=lambda: change_page(1)).props('flat dense color=white size=sm')

                        ui.separator().props('vertical')

                        # Sort
                        lib_sort_opts = ['Name', 'ATK', 'DEF', 'Level', 'Set Code', 'Price', 'Newest']
                        async def on_lib_sort(e):
                            self.state['library_sort_by'] = e.value
                            persistence.save_ui_state({'bulk_library_sort_by': e.value})
                            await self.apply_library_filters()
                        ui.select(lib_sort_opts, value=self.state['library_sort_by'], on_change=on_lib_sort).props('dense options-dense borderless').classes('w-20 text-xs')

                        async def toggle_sort():
                            self.state['library_sort_desc'] = not self.state['library_sort_desc']
                            persistence.save_ui_state({'bulk_library_sort_desc': self.state['library_sort_desc']})
                            await self.apply_library_filters()
                        ui.button(on_click=toggle_sort).props('flat dense color=white size=sm').bind_icon_from(self.state, 'library_sort_desc', lambda d: 'arrow_downward' if d else 'arrow_upward')

                        ui.button(icon='filter_list', on_click=self.library_filter_dialog.open).props('flat dense color=white size=sm')

                with ui.column().classes('w-full flex-grow relative bg-black/20 overflow-hidden'):
                    with ui.scroll_area().classes('w-full h-full'):
                         self.render_library_content()

            # Right: Staging (Edit Batch)
            with ui.column().classes('w-1/2 h-full bg-dark border border-gray-800 rounded flex flex-col overflow-hidden'):
                # Header
                with ui.row().classes('w-full p-2 bg-gray-900 border-b border-gray-800 items-center justify-between gap-2 flex-nowrap overflow-x-auto'):
                    ui.label('Edit Batch').classes('text-h6 font-bold')
                    with ui.row().classes('items-center gap-1 flex-nowrap'):
                        # Update Controls
                        with ui.row().classes('gap-1 items-center bg-gray-800 rounded px-1 border border-gray-700'):
                            ui.button("Update", on_click=self.on_update_all_click).props('flat dense color=warning size=sm')
                            ui.checkbox('Lang', value=self.state['update_apply_lang'],
                                        on_change=lambda e: [self.state.update({'update_apply_lang': e.value}), persistence.save_ui_state({'bulk_update_apply_lang': e.value})]).props('dense size=xs').classes('text-[10px]')
                            ui.checkbox('Cond', value=self.state['update_apply_cond'],
                                        on_change=lambda e: [self.state.update({'update_apply_cond': e.value}), persistence.save_ui_state({'bulk_update_apply_cond': e.value})]).props('dense size=xs').classes('text-[10px]')
                            ui.checkbox('1st', value=self.state['update_apply_first'],
                                        on_change=lambda e: [self.state.update({'update_apply_first': e.value}), persistence.save_ui_state({'bulk_update_apply_first': e.value})]).props('dense size=xs').classes('text-[10px]')
                            ui.checkbox('Storage', value=self.state['update_apply_storage'],
                                        on_change=lambda e: [self.state.update({'update_apply_storage': e.value}), persistence.save_ui_state({'bulk_update_apply_storage': e.value})]).props('dense size=xs').classes('text-[10px]')

                        ui.button("Clear Batch", on_click=self.clear_staging_area).props('flat dense color=white size=sm')
                        ui.button("Remove All", on_click=self.on_remove_all_click).props('flat dense color=negative size=sm')

                        ui.separator().props('vertical')

                        ui.input(placeholder='Search...',
                                 on_change=lambda e: self.apply_collection_filters()) \
                            .bind_value(self.col_state, 'search_text') \
                            .props('dense borderless dark debounce=300') \
                            .classes('w-52 text-sm')

                        ui.separator().props('vertical')

                        # Pagination
                        async def change_col_page(delta):
                             new_p = max(1, min(self.col_state['collection_total_pages'], self.col_state['collection_page'] + delta))
                             if new_p != self.col_state['collection_page']:
                                 self.col_state['collection_page'] = new_p
                                 self.render_collection_content.refresh()
                        ui.button(icon='chevron_left', on_click=lambda: change_col_page(-1)).props('flat dense color=white size=sm')
                        ui.label().bind_text_from(self.col_state, 'collection_page', lambda p: f"{p}/{self.col_state['collection_total_pages']}").classes('text-xs font-mono')
                        ui.button(icon='chevron_right', on_click=lambda: change_col_page(1)).props('flat dense color=white size=sm')

                        ui.separator().props('vertical')

                        # Sort
                        col_sort_opts = ['Name', 'ATK', 'DEF', 'Level', 'Set Code', 'Quantity', 'Newest']
                        async def on_col_sort(e):
                            self.col_state['sort_by'] = e.value
                            persistence.save_ui_state({'bulk_collection_sort_by': e.value})
                            await self.apply_collection_filters()
                        ui.select(col_sort_opts, value=self.col_state['sort_by'], on_change=on_col_sort).props('dense options-dense borderless').classes('w-20 text-xs')

                        async def toggle_col_sort():
                            self.col_state['sort_desc'] = not self.col_state['sort_desc']
                            persistence.save_ui_state({'bulk_collection_sort_desc': self.col_state['sort_desc']})
                            await self.apply_collection_filters()
                        ui.button(on_click=toggle_col_sort).props('flat dense color=white size=sm').bind_icon_from(self.col_state, 'sort_desc', lambda d: 'arrow_downward' if d else 'arrow_upward')

                        ui.button(icon='filter_list', on_click=self.collection_filter_dialog.open).props('flat dense color=white size=sm')

                with ui.column().classes('w-full flex-grow relative bg-black/20 overflow-hidden'):
                     with ui.scroll_area().classes('w-full h-full'):
                        self.render_collection_content()

        ui.timer(0.1, self.load_library_data, once=True)

def bulk_edit_page():
    page = BulkEditPage()
    page.build_ui()
