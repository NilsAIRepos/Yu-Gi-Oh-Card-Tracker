from nicegui import ui, run
from src.services.storage import storage_service
from src.services.ygo_api import ygo_service, ApiCard
from src.services.image_manager import image_manager
from src.services.collection_editor import CollectionEditor
from src.core.persistence import persistence
from src.core.config import config_manager
from src.ui.components.filter_pane import FilterPane
from src.ui.components.single_card_view import SingleCardView
from src.core.utils import LANGUAGE_COUNTRY_MAP
from dataclasses import dataclass
from typing import List, Optional, Dict, Callable
import logging
import asyncio

logger = logging.getLogger(__name__)

@dataclass
class StorageRow:
    api_card: ApiCard
    set_code: str
    set_name: str
    rarity: str
    image_url: str
    quantity: int
    language: str
    condition: str
    first_edition: bool
    image_id: Optional[int] = None
    variant_id: Optional[str] = None
    storage_location: Optional[str] = None

class StorageDialog:
    def __init__(self, on_save: Callable):
        self.on_save = on_save
        self.dialog = ui.dialog()
        self.sets_info = []
        self.set_options = {} # code -> name

        self.current_data = {} # For edit mode
        self.uploaded_image_path = None

        self._build_ui()

    async def load_sets(self):
        try:
            all_sets = await ygo_service.get_all_sets_info()
            # Filter out promo cards
            self.sets_info = [
                s for s in all_sets
                if "promo card" not in s['name'].lower() and "promocard" not in s['name'].lower() and "promotional card" not in s['name'].lower() and "prize card" not in s['name'].lower()
            ]
            self.set_options = {s['code']: f"{s['code']} - {s['name']}" for s in self.sets_info}
            # Update select if it exists
            if self.set_select:
                self.set_select.set_options(self.set_options)
        except Exception as e:
            logger.error(f"Error loading sets for storage dialog: {e}")

    def _build_ui(self):
        with self.dialog, ui.card().classes('w-[500px]'):
            self.title_label = ui.label('New Storage').classes('text-h6')

            with ui.column().classes('w-full gap-2'):
                # Name
                self.name_input = ui.input('Name').classes('w-full').props('autofocus')

                # Type
                type_opts = ['Box', 'Binder', 'Sealed Product']
                self.type_select = ui.select(type_opts, label='Type', value='Box').classes('w-full')

                # Set Selection Container
                self.set_select_container = ui.column().classes('w-full')

                def on_type_change(e):
                    val = e.value if hasattr(e, 'value') else e
                    if isinstance(val, dict) and 'value' in val:
                        val = val['value']
                    self.set_select_container.set_visibility(val == 'Sealed Product')

                self.type_select.on_value_change(on_type_change)

                with self.set_select_container:
                     async def on_set_change(e):
                         if not e.value: return
                         set_code = e.value

                         try:
                             # Auto-fill name if empty or default
                             current_name = self.name_input.value
                             if not current_name or current_name.endswith('Box'):
                                 set_name_full = self.set_options.get(set_code, '')
                                 if ' - ' in set_name_full:
                                    set_name = set_name_full.split(' - ')[-1]
                                    self.name_input.value = f"{set_name} Box"

                             # Preview Image
                             if self.sets_info and self.image_preview:
                                 s_info = next((s for s in self.sets_info if s['code'] == set_code), None)
                                 if s_info and s_info.get('image'):
                                      url = s_info.get('image')
                                      path = await ygo_service.download_set_image(set_code, url)
                                      if path:
                                           safe_code = "".join(c for c in set_code if c.isalnum() or c in ('-', '_')).strip()
                                           self.image_preview.set_source(f"/sets/{safe_code}.jpg")
                         except Exception as ex:
                             logger.error(f"Error handling set change: {ex}")
                             ui.notify(f"Error loading set info: {ex}", type='warning')

                     # Filter Input
                     def filter_sets(e):
                         val = e.value
                         if not val:
                             self.set_select.options = self.set_options
                         else:
                             self.set_select.options = {k: v for k, v in self.set_options.items() if val.lower() in v.lower()}
                         self.set_select.update()

                     self.filter_input = ui.input(placeholder='Filter Product...').classes('w-full') \
                         .props('debounce=300 clearable').on('update:model-value', filter_sets)

                     self.set_select = ui.select(self.set_options, label='Select Product', on_change=on_set_change) \
                        .classes('w-full').props('clearable behavior="menu"')

                self.set_select_container.set_visibility(False)

                # Description
                self.desc_input = ui.textarea('Description').classes('w-full').props('rows=2')

                # Image Upload
                ui.label('Image').classes('text-sm text-gray-500')

                async def handle_upload(e):
                    # e is UploadEventArguments
                    # e.file is FileUpload (Small or Large)
                    # e.file.name is filename
                    path = await storage_service.save_uploaded_image(e.file, e.file.name)
                    if path:
                        self.uploaded_image_path = path
                        self.image_preview.set_source(f"/storage/{path}")
                        ui.notify('Image uploaded', type='positive')

                self.upload_element = ui.upload(on_upload=handle_upload, auto_upload=True).props('accept=".jpg, .jpeg, .png" flat dense').classes('w-full')

                # Preview
                self.image_preview = ui.image().classes('w-full h-40 object-contain bg-black rounded')

            with ui.row().classes('w-full justify-end q-mt-md gap-4'):
                ui.button('Cancel', on_click=self.dialog.close).props('flat')
                ui.button('Save', on_click=self.save).props('color=primary')

    def open(self, existing_data: Optional[Dict] = None):
        self.current_data = existing_data or {}
        self.uploaded_image_path = self.current_data.get('image_path')

        if not self.sets_info:
            asyncio.create_task(self.load_sets())

        # Update UI Elements
        is_edit = bool(existing_data)
        self.title_label.text = "Edit Storage" if is_edit else "New Storage"

        self.name_input.value = self.current_data.get('name', '')
        self.type_select.value = self.current_data.get('type', 'Box')
        self.desc_input.value = self.current_data.get('description', '')
        self.set_select.value = self.current_data.get('set_code')

        # Visibility
        self.set_select_container.set_visibility(self.type_select.value == 'Sealed Product')

        # Image
        if self.uploaded_image_path:
            self.image_preview.set_source(f"/storage/{self.uploaded_image_path}")
        elif self.current_data.get('set_code'):
             safe_code = "".join(c for c in self.current_data.get('set_code') if c.isalnum() or c in ('-', '_')).strip()
             self.image_preview.set_source(f"/sets/{safe_code}.jpg")
        else:
            self.image_preview.set_source(None)

        self.dialog.open()

    async def save(self):
        name = self.name_input.value.strip()
        if not name:
            ui.notify('Name is required', type='warning')
            return

        # Check uniqueness handled by storage page callback context

        type_val = self.type_select.value
        set_code = self.set_select.value if type_val == 'Sealed Product' else None
        desc = self.desc_input.value

        data = {
            'name': name,
            'type': type_val,
            'description': desc,
            'set_code': set_code,
            'image_path': self.uploaded_image_path
        }

        await self.on_save(self.current_data.get('name'), data)
        self.dialog.close()


class StoragePage:
    def __init__(self):
        # Load persisted UI state
        saved_state = persistence.load_ui_state()

        self.state = {
            'view': 'gallery', # gallery, detail
            'current_storage': None, # Storage dict
            'storages': [],

            'rows': [],
            'filtered_rows': [],
            'in_storage_only': True,
            # 'add_mode': True, # Deprecated

            'current_collection': None,
            'selected_collection_file': None,

            'search_text': '',
            'page': 1,
            'page_size': 48,
            'total_pages': 1,

            'filter_set': '',
            'filter_rarity': '',
            'filter_attr': '',
            'filter_card_type': [],
            'filter_condition': [],
            'filter_monster_race': '',
            'filter_st_race': '',
            'filter_archetype': '',
            'filter_monster_category': [],
            'filter_level': None,
            'filter_atk_min': 0,
            'filter_atk_max': 5000,
            'filter_def_min': 0,
            'filter_def_max': 5000,
            'filter_ownership_min': 0,
            'filter_ownership_max': 100,
            'filter_price_min': 0.0,
            'filter_price_max': 1000.0,
            'filter_owned_lang': '',
            'filter_owned_only': True,
            'sort_by': 'Name',
            'sort_desc': False,

            'storage_detail_sort_by': saved_state.get('storage_detail_sort_by', 'Name'),
            'storage_detail_sort_desc': saved_state.get('storage_detail_sort_desc', False),

            'storage_sort_by': saved_state.get('storage_sort_by', 'Name'),
            'storage_sort_desc': saved_state.get('storage_sort_desc', False),
            'storage_counts': {},

            'available_sets': [],
            'available_monster_races': [],
            'available_st_races': [],
            'available_archetypes': [],
            'available_card_types': ['Monster', 'Spell', 'Trap', 'Skill'],
            'max_owned_quantity': 100,
        }

        files = persistence.list_collections()
        saved_file = saved_state.get('storage_selected_file')

        if saved_file and saved_file in files:
            self.state['selected_collection_file'] = saved_file
        elif files:
            self.state['selected_collection_file'] = files[0]

        self.single_card_view = SingleCardView()
        self.filter_pane = None
        self.filter_dialog = None

        self.storage_dialog = StorageDialog(self.on_storage_save)
        self.save_lock = asyncio.Lock()
        self.save_task = None

    async def load_data(self):
        if self.state['selected_collection_file']:
            try:
                self.state['current_collection'] = await run.io_bound(persistence.load_collection, self.state['selected_collection_file'])
                # Load Storages from Collection
                self.state['storages'] = storage_service.get_all_storage(self.state['current_collection'])
            except Exception as e:
                logger.error(f"Error loading collection: {e}")
                ui.notify(f"Error loading collection: {e}", type='negative')
                self.state['storages'] = []
        else:
            self.state['storages'] = []

        # Calculate storage counts
        counts = {}
        if self.state['current_collection']:
            for card in self.state['current_collection'].cards:
                for variant in card.variants:
                    for entry in variant.entries:
                        if entry.storage_location:
                            counts[entry.storage_location] = counts.get(entry.storage_location, 0) + entry.quantity
        self.state['storage_counts'] = counts

        self.sort_storages()

        if self.state['view'] == 'gallery':
            self.render_content.refresh()
        elif self.state['view'] == 'detail':
            # Refresh storage object
            if self.state['current_storage']:
                updated = storage_service.get_storage(self.state['current_collection'], self.state['current_storage']['name'])
                if updated:
                    self.state['current_storage'] = updated
                else:
                    # Storage might have been deleted or we switched collection
                    self.state['view'] = 'gallery'
                    self.state['current_storage'] = None

            await self.load_detail_rows()
            self.render_content.refresh()

    async def _perform_save(self):
        """Internal method to perform the save with locking."""
        if self.state['current_collection'] and self.state['selected_collection_file']:
            async with self.save_lock:
                await run.io_bound(persistence.save_collection, self.state['current_collection'], self.state['selected_collection_file'])
                logger.info(f"Saved collection {self.state['selected_collection_file']}")

    def schedule_save(self):
        """Debounced save: schedules a save in the future, cancelling any pending one."""
        if self.save_task:
            self.save_task.cancel()

        async def delayed_save():
            try:
                await asyncio.sleep(2.0)
                await self._perform_save()
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.error(f"Error in debounced save: {e}")
                ui.notify(f"Auto-save failed: {e}", type='negative')
            finally:
                self.save_task = None

        self.save_task = asyncio.create_task(delayed_save())

    async def save_immediately(self):
        """Cancels any pending debounced save and saves immediately."""
        if self.save_task:
            self.save_task.cancel()
            self.save_task = None
        await self._perform_save()

    async def on_storage_save(self, original_name, data):
        col = self.state['current_collection']
        if not col:
            ui.notify('No collection selected', type='negative')
            return

        if original_name:
            # Update
            success = storage_service.update_storage(
                col, original_name, data['name'], data['type'], data['description'], data['image_path'], data['set_code']
            )
            if success:
                ui.notify('Storage updated', type='positive')

                # Handle Rename: Update internal card references
                if original_name != data['name']:
                    logger.info(f"Storage renamed from {original_name} to {data['name']}. Updating collection entries...")
                    modified = CollectionEditor.rename_storage_location(col, original_name, data['name'])
                    if modified:
                        ui.notify(f"Updated card references for rename.", type='positive')

                # Save Collection
                await self.save_immediately()

                # Update current_storage reference
                if self.state['current_storage'] and self.state['current_storage']['name'] == original_name:
                    self.state['current_storage'] = storage_service.get_storage(col, data['name'])
        else:
            # Create
            success = storage_service.add_storage(
                col, data['name'], data['type'], data['description'], data['image_path'], data['set_code']
            )
            if success:
                await self.save_immediately()
                ui.notify('Storage created', type='positive')

        if not success:
            ui.notify('Operation failed (Name collision?)', type='negative')

        await self.load_data()

    async def load_detail_rows(self, reset_page: bool = True):
        if not self.state['current_collection']: return

        target_loc = self.state['current_storage']['name'] if self.state['current_storage'] else None
        rows = []

        lang = config_manager.get_language()
        await ygo_service.load_card_database(lang)

        api_card_map = {c.id: c for c in ygo_service._cards_cache.get(lang, [])}

        sets = set()
        m_races = set()
        st_races = set()
        archetypes = set()

        for c_card in self.state['current_collection'].cards:
            api_card = api_card_map.get(c_card.card_id)
            if not api_card: continue

            if api_card.archetype: archetypes.add(api_card.archetype)
            if "Monster" in api_card.type: m_races.add(api_card.race)
            elif "Spell" in api_card.type or "Trap" in api_card.type:
                 if api_card.race: st_races.add(api_card.race)

            for v in c_card.variants:
                set_name = "Unknown"
                if api_card.card_sets:
                    for s in api_card.card_sets:
                         if s.set_code == v.set_code:
                             set_name = s.set_name
                             sets.add(f"{s.set_name} | {s.set_code.split('-')[0]}")
                             break

                for e in v.entries:
                    qty = e.quantity
                    if qty <= 0: continue

                    if self.state['in_storage_only']:
                        # Showing items IN the current box
                        if e.storage_location != target_loc: continue
                    else:
                        # Showing items NOT in ANY box (Unassigned) to add to current box
                        if e.storage_location is not None: continue

                    img_url = api_card.card_images[0].image_url_small if api_card.card_images else None
                    if v.image_id:
                        for img in api_card.card_images:
                            if img.id == v.image_id:
                                img_url = img.image_url_small
                                break

                    rows.append(StorageRow(
                        api_card=api_card,
                        set_code=v.set_code,
                        set_name=set_name,
                        rarity=v.rarity,
                        image_url=img_url,
                        quantity=qty,
                        language=e.language,
                        condition=e.condition,
                        first_edition=e.first_edition,
                        image_id=v.image_id,
                        variant_id=v.variant_id,
                        storage_location=e.storage_location
                    ))

        self.state['available_sets'] = sorted(list(sets))
        self.state['available_monster_races'] = sorted(list(m_races))

        # Ensure standard Spell/Trap types are always available
        standard_st_races = {"Normal", "Continuous", "Equip", "Field", "Quick-Play", "Ritual", "Counter"}
        self.state['available_st_races'] = sorted(list(st_races.union(standard_st_races)))

        self.state['available_archetypes'] = sorted(list(archetypes))

        if self.filter_pane: self.filter_pane.update_options()

        self.state['rows'] = rows

        # Download flags
        unique_langs = set(row.language for row in rows if row.language)
        unique_codes = set()
        for lang in unique_langs:
             code = LANGUAGE_COUNTRY_MAP.get(lang.strip().upper())
             if code: unique_codes.add(code)

        if unique_codes:
             tasks = [image_manager.ensure_flag_image(code) for code in unique_codes]
             await asyncio.gather(*tasks)

        await self.apply_filters(reset_page=reset_page)

    async def apply_filters(self, reset_page: bool = True):
        res = list(self.state['rows'])

        txt = self.state['search_text'].lower()
        if txt:
            res = [r for r in res if txt in r.api_card.name.lower() or txt in r.set_code.lower()]

        if self.state['filter_rarity']:
             res = [r for r in res if r.rarity.lower() == self.state['filter_rarity'].lower()]

        if self.state['filter_attr']:
             res = [r for r in res if r.api_card.attribute == self.state['filter_attr']]

        if self.state['filter_card_type']:
             ctypes = self.state['filter_card_type']
             if isinstance(ctypes, str): ctypes = [ctypes]
             res = [r for r in res if any(t in r.api_card.type for t in ctypes)]

        # Set
        if self.state['filter_set']:
            target = self.state['filter_set']
            res = [r for r in res if f"{r.set_name} | {r.set_code.split('-')[0]}" == target]

        # Monster Type (Race)
        if self.state['filter_monster_race']:
            res = [r for r in res if r.api_card.race == self.state['filter_monster_race']]

        # Spell/Trap Type (Race)
        if self.state['filter_st_race']:
            res = [r for r in res if r.api_card.race == self.state['filter_st_race']]

        # Archetype
        if self.state['filter_archetype']:
            res = [r for r in res if r.api_card.archetype == self.state['filter_archetype']]

        # Monster Category
        if self.state['filter_monster_category']:
            cats = self.state['filter_monster_category']
            if isinstance(cats, str): cats = [cats]
            if cats:
                res = [r for r in res if any(r.api_card.matches_category(c) for c in cats)]

        # Level/Rank
        if self.state['filter_level'] is not None:
             res = [r for r in res if r.api_card.level == self.state['filter_level']]

        # ATK
        if self.state['filter_atk_min'] > 0 or self.state['filter_atk_max'] < 5000:
            min_v = self.state['filter_atk_min']
            max_v = self.state['filter_atk_max']
            res = [r for r in res if r.api_card.atk is not None and min_v <= r.api_card.atk <= max_v]

        # DEF
        if self.state['filter_def_min'] > 0 or self.state['filter_def_max'] < 5000:
            min_v = self.state['filter_def_min']
            max_v = self.state['filter_def_max']
            res = [r for r in res if r.api_card.def_ is not None and min_v <= r.api_card.def_ <= max_v]

        # Ownership Quantity
        if self.state['filter_ownership_min'] > 0 or self.state['filter_ownership_max'] < self.state['max_owned_quantity']:
            min_v = self.state['filter_ownership_min']
            max_v = self.state['filter_ownership_max']
            res = [r for r in res if min_v <= r.quantity <= max_v]

        # Price
        def get_price(card):
            if not card.card_prices: return 0.0
            try:
                p = card.card_prices[0].tcgplayer_price
                return float(p) if p else 0.0
            except: return 0.0

        if self.state['filter_price_min'] > 0 or self.state['filter_price_max'] < 1000:
            min_v = self.state['filter_price_min']
            max_v = self.state['filter_price_max']
            res = [r for r in res if min_v <= get_price(r.api_card) <= max_v]

        # Owned Language
        if self.state['filter_owned_lang']:
             res = [r for r in res if r.language == self.state['filter_owned_lang']]

        # Condition
        if self.state['filter_condition']:
             conds = self.state['filter_condition']
             if isinstance(conds, str): conds = [conds]
             if conds:
                res = [r for r in res if r.condition in conds]

        key = self.state['storage_detail_sort_by']
        desc = self.state['storage_detail_sort_desc']

        if key == 'Name':
            res.sort(key=lambda x: x.api_card.name, reverse=desc)
        elif key == 'ATK':
            res.sort(key=lambda x: (x.api_card.atk or -1), reverse=desc)
        elif key == 'DEF':
            res.sort(key=lambda x: (getattr(x.api_card, 'def_', None) or -1), reverse=desc)
        elif key == 'Level':
            res.sort(key=lambda x: (x.api_card.level or -1), reverse=desc)
        elif key == 'Newest':
            res.sort(key=lambda x: x.api_card.id, reverse=desc)
        elif key == 'Price':
             res.sort(key=lambda x: get_price(x.api_card), reverse=desc)
        elif key == 'Quantity':
             res.sort(key=lambda x: x.quantity, reverse=desc)
        elif key == 'Set Code':
            res.sort(key=lambda x: x.set_code, reverse=desc)

        self.state['filtered_rows'] = res
        self.update_pagination()

        if reset_page:
            self.state['page'] = 1
        elif self.state['page'] > self.state['total_pages']:
            self.state['page'] = max(1, self.state['total_pages'])

        if hasattr(self, 'render_detail_grid'): self.render_detail_grid.refresh()
        if hasattr(self, 'render_pagination_controls'): self.render_pagination_controls.refresh()

    def update_pagination(self):
        count = len(self.state['filtered_rows'])
        self.state['total_pages'] = (count + self.state['page_size'] - 1) // self.state['page_size']

    def sort_storages(self):
        key = self.state['storage_sort_by']
        desc = self.state['storage_sort_desc']

        def get_sort_key(s):
            if key == 'Name':
                return s['name'].lower()
            elif key == 'Count':
                return self.state['storage_counts'].get(s['name'], 0)
            return s['name']

        self.state['storages'].sort(key=get_sort_key, reverse=desc)

    async def open_storage(self, storage):
        self.state['current_storage'] = storage
        self.state['view'] = 'detail'
        await self.load_detail_rows()
        self.render_content.refresh()

    async def back_to_gallery(self):
        self.state['view'] = 'gallery'
        self.state['current_storage'] = None
        self.render_content.refresh()

    # --- Renderers ---

    @ui.refreshable
    def render_content(self):
        if self.state['view'] == 'gallery':
            self.render_gallery_view()
        else:
            self.render_detail_view()

    def render_gallery_view(self):
        # Header
        with ui.row().classes('w-full items-center justify-between p-4 bg-gray-900 rounded-lg border border-gray-800 mb-4'):
            ui.label('Storage').classes('text-h5 text-white')

            with ui.row().classes('items-center gap-4'):
                # Collection Selector
                files = persistence.list_collections()
                file_options = {}
                for f in files:
                    display_name = f
                    if f.endswith('.json'): display_name = f[:-5]
                    file_options[f] = display_name

                async def handle_collection_change(e):
                    self.state['selected_collection_file'] = e.value
                    persistence.save_ui_state({'storage_selected_file': e.value})
                    await self.load_data()

                ui.select(file_options, value=self.state['selected_collection_file'], label='Collection',
                          on_change=handle_collection_change).classes('w-40')

                # Sorting Controls
                async def handle_sort_change(e):
                    self.state['storage_sort_by'] = e.value
                    persistence.save_ui_state({'storage_sort_by': e.value})
                    self.sort_storages()
                    self.render_content.refresh()

                ui.select(['Name', 'Count'], value=self.state['storage_sort_by'], label='Sort By',
                          on_change=handle_sort_change).classes('w-32')

                async def toggle_sort_order():
                    self.state['storage_sort_desc'] = not self.state['storage_sort_desc']
                    persistence.save_ui_state({'storage_sort_desc': self.state['storage_sort_desc']})
                    self.sort_storages()
                    self.render_content.refresh()

                ui.button(icon='arrow_downward' if self.state['storage_sort_desc'] else 'arrow_upward',
                          on_click=toggle_sort_order).props('flat round color=white')

                ui.button('New Storage', icon='add', on_click=self.open_new_storage_dialog).props('color=primary')

        # Grid
        with ui.grid(columns='repeat(auto-fill, minmax(250px, 1fr))').classes('w-full gap-6'):
            for s in self.state['storages']:
                self.render_storage_card(s)

            self.render_add_storage_card()

    def render_add_storage_card(self):
        with ui.card().classes('w-full h-full min-h-[14rem] p-0 cursor-pointer hover:scale-105 transition-transform border border-gray-700 bg-gray-800 flex items-center justify-center group') \
                .on('click', self.open_new_storage_dialog):

            with ui.column().classes('items-center gap-2 group-hover:text-primary transition-colors'):
                ui.icon('add_circle_outline', size='4xl', color='grey').classes('group-hover:text-primary transition-colors')
                ui.label('Add Storage').classes('text-lg font-bold text-gray-400 group-hover:text-primary transition-colors')

    def render_storage_card(self, storage):
        count = self.state['storage_counts'].get(storage['name'], 0)

        with ui.card().classes('w-full p-0 cursor-pointer hover:scale-105 transition-transform border border-gray-700 bg-gray-800') \
                .on('click', lambda s=storage: self.open_storage(s)):

            with ui.element('div').classes('relative w-full h-48 bg-black overflow-hidden'):
                if storage.get('image_path'):
                    src = f"/storage/{storage['image_path']}"
                    ui.image(src).classes('w-full h-full object-cover')
                elif storage.get('set_code'):
                    safe_code = "".join(c for c in storage['set_code'] if c.isalnum() or c in ('-', '_')).strip()
                    src = f"/sets/{safe_code}.jpg"
                    ui.image(src).classes('w-full h-full object-contain')
                else:
                    ui.icon('inventory_2', size='4xl', color='grey').classes('absolute top-1/2 left-1/2 transform -translate-x-1/2 -translate-y-1/2')

            with ui.column().classes('p-3 w-full gap-1'):
                ui.label(storage['name']).classes('text-lg font-bold truncate w-full text-white')

                with ui.row().classes('w-full justify-between items-center'):
                    ui.label(storage.get('type', 'Unknown')).classes('text-sm text-yellow-500 font-bold')
                    ui.label(f"{count} Cards").classes('text-xs text-gray-400')

                if storage.get('description'):
                    ui.label(storage['description']).classes('text-xs text-gray-400 truncate w-full')

    def render_detail_view(self):
        s = self.state['current_storage']
        if not s: return

        with ui.row().classes('w-full items-start gap-6 mb-4 p-4 bg-gray-900 rounded-lg border border-gray-800'):
            with ui.element('div').classes('w-24 h-24 relative bg-black rounded shadow-lg overflow-hidden'):
                 if s.get('image_path'):
                     ui.image(f"/storage/{s['image_path']}").classes('w-full h-full object-cover')
                 elif s.get('set_code'):
                     safe_code = "".join(c for c in s['set_code'] if c.isalnum() or c in ('-', '_')).strip()
                     ui.image(f"/sets/{safe_code}.jpg").classes('w-full h-full object-contain')
                 else:
                     ui.icon('inventory_2', size='xl', color='grey').classes('absolute top-1/2 left-1/2 transform -translate-x-1/2 -translate-y-1/2')

            with ui.column().classes('gap-1 flex-grow'):
                ui.label(s['name']).classes('text-h4 font-bold text-white leading-none')
                ui.label(s.get('type', '')).classes('text-lg text-yellow-500')
                ui.label(s.get('description', '')).classes('text-sm text-gray-400')

            with ui.column().classes('items-end gap-2'):
                ui.button('Back', icon='arrow_back', on_click=self.back_to_gallery).props('flat color=white')
                ui.button('Edit Storage', icon='edit', on_click=lambda: self.open_edit_storage_dialog(s)).props('outline color=white')

        with ui.row().classes('w-full items-center gap-4 bg-gray-800 p-2 rounded mb-4'):
            async def on_search(e):
                self.state['search_text'] = e.value
                await self.apply_filters()
            ui.input(placeholder='Search cards...', on_change=on_search).props('dark icon=search debounce=300').classes('w-64')

            async def on_sort_change(e):
                self.state['storage_detail_sort_by'] = e.value
                persistence.save_ui_state({'storage_detail_sort_by': self.state['storage_detail_sort_by']})
                self.render_content.refresh()
                await self.apply_filters()

            with ui.row().classes('items-center gap-1'):
                with ui.select(['Name', 'ATK', 'DEF', 'Level', 'Newest', 'Price', 'Quantity', 'Set Code'], value=self.state['storage_detail_sort_by'], label='Sort',
                        on_change=on_sort_change).classes('w-32'):
                    ui.tooltip('Choose how to sort the displayed cards')

                async def toggle_sort_dir():
                    self.state['storage_detail_sort_desc'] = not self.state['storage_detail_sort_desc']
                    persistence.save_ui_state({'storage_detail_sort_desc': self.state['storage_detail_sort_desc']})
                    self.render_content.refresh()
                    await self.apply_filters()

                icon = 'arrow_downward' if self.state.get('storage_detail_sort_desc') else 'arrow_upward'
                with ui.button(icon=icon, on_click=toggle_sort_dir).props('flat round dense color=white'):
                    ui.tooltip('Toggle sort direction')

            ui.separator().props('vertical')

            # Action Label Reference
            action_label = ui.label().classes('text-sm font-bold ml-4')

            def update_action_label():
                txt = "Right-Click: Remove from Storage" if self.state['in_storage_only'] else "Right-Click: Add to Storage"
                color = "text-negative" if self.state['in_storage_only'] else "text-positive"
                # Clear existing color classes
                action_label.classes(remove="text-negative text-positive")
                action_label.classes(add=color)
                action_label.text = txt

            async def toggle_storage(e):
                self.state['in_storage_only'] = e.value
                update_action_label()
                await self.load_detail_rows()
                self.render_detail_grid.refresh()
                self.render_pagination_controls.refresh()

            ui.switch('In Storage', value=self.state['in_storage_only'], on_change=toggle_storage).props('color=secondary').classes('mr-4')

            ui.separator().props('vertical')

            # Init label
            update_action_label()

            ui.space()

            self.render_pagination_controls()
            ui.button('Filters', icon='filter_list', on_click=self.filter_dialog.open).props('color=primary')

        self.render_detail_grid()

        with ui.row().classes('w-full justify-center mt-4'):
             self.render_pagination_controls()

    @ui.refreshable
    def render_detail_grid(self):
        rows = self.state['filtered_rows']
        start = (self.state['page'] - 1) * self.state['page_size']
        end = min(start + self.state['page_size'], len(rows))
        visible_rows = rows[start:end]

        with ui.grid(columns='repeat(auto-fill, minmax(160px, 1fr))').classes('w-full gap-4'):
            for row in visible_rows:
                self.render_card(row)

    def render_card(self, row: StorageRow):
        opacity = "opacity-100"
        border_color = "border-gray-700"
        if self.state['in_storage_only']:
            border_color = "border-accent"

        with ui.card().classes(f'collection-card w-full p-0 cursor-pointer {opacity} border {border_color} hover:scale-105 transition-transform') \
                .on('contextmenu.prevent', lambda e, r=row: self.handle_right_click(e, r)):

            with ui.element('div').classes('relative w-full aspect-[2/3] bg-black'):
                if row.image_url:
                    ui.image(row.image_url).classes('w-full h-full object-cover')

                lang_code = row.language.strip().upper()
                country_code = LANGUAGE_COUNTRY_MAP.get(lang_code)
                flag_url = image_manager.get_flag_image_url(country_code) if country_code else None

                if flag_url:
                     ui.image(flag_url).classes('absolute top-[1px] left-[1px] h-4 w-6 shadow-black drop-shadow-md rounded bg-black/30')
                else:
                     ui.label(lang_code).classes('absolute top-[1px] left-[1px] text-xs font-bold shadow-black drop-shadow-md bg-black/30 rounded px-1')

                ui.label(f"{row.quantity}").classes('absolute top-1 right-1 bg-accent text-dark font-bold px-2 rounded-full text-xs')

                with ui.row().classes('absolute bottom-0 left-0 bg-black/80 text-white text-[10px] px-1 gap-1 items-center rounded-tr'):
                    ui.label(row.condition).classes('font-bold text-yellow-500')
                    if row.first_edition:
                         ui.label("1st").classes('font-bold text-orange-400')

                ui.label(row.set_code).classes('absolute bottom-0 right-0 bg-black/80 text-white text-[10px] px-1 font-mono rounded-tl')

            with ui.column().classes('p-2 gap-0 w-full'):
                ui.label(row.api_card.name).classes('text-xs font-bold truncate w-full')
                ui.label(row.rarity).classes('text-[10px] text-gray-400')

    async def handle_right_click(self, e, row: StorageRow):
        col = self.state['current_collection']
        storage_name = self.state['current_storage']['name']
        qty = 1
        success = False
        msg = ""

        # Logic derived from View State
        # In Storage View -> Remove (Move to None)
        # Unassigned View -> Add (Move from None)

        if not self.state['in_storage_only']:
            # ADD Logic (None -> Storage)
            avail = CollectionEditor.get_quantity(
                col, row.api_card.id, row.variant_id, row.set_code, row.rarity, row.image_id,
                row.language, row.condition, row.first_edition, storage_location=None
            )
            if avail >= qty:
                CollectionEditor.move_card(
                    col, row.api_card, row.set_code, row.rarity, row.language,
                    row.condition, row.first_edition, from_storage=None, to_storage=storage_name,
                    quantity=qty, image_id=row.image_id, variant_id=row.variant_id
                )
                success = True
                msg = f"Added 1 {row.api_card.name} to {storage_name}"
            else:
                msg = "No unassigned copies available!"
        else:
            # SUBTRACT Logic (Storage -> None)
            avail = CollectionEditor.get_quantity(
                col, row.api_card.id, row.variant_id, row.set_code, row.rarity, row.image_id,
                row.language, row.condition, row.first_edition, storage_location=storage_name
            )
            if avail >= qty:
                CollectionEditor.move_card(
                    col, row.api_card, row.set_code, row.rarity, row.language,
                    row.condition, row.first_edition, from_storage=storage_name, to_storage=None,
                    quantity=qty, image_id=row.image_id, variant_id=row.variant_id
                )
                success = True
                msg = f"Removed 1 {row.api_card.name} from {storage_name}"
            else:
                msg = "Not enough copies in storage!"

        if success:
            self.schedule_save()
            ui.notify(msg, type='positive')
            await self.load_detail_rows(reset_page=False)
            self.render_detail_grid.refresh()
        else:
            ui.notify(msg, type='warning')

    @ui.refreshable
    def render_pagination_controls(self):
        if self.state['total_pages'] <= 1: return
        async def change_p(delta):
            self.state['page'] += delta
            self.render_detail_grid.refresh()
            self.render_pagination_controls.refresh()
        with ui.row().classes('items-center gap-2'):
            ui.button(icon='chevron_left', on_click=lambda: change_p(-1)).props('flat dense color=white').set_enabled(self.state['page'] > 1)
            ui.label(f"{self.state['page']} / {self.state['total_pages']}").classes('text-white text-sm font-bold')
            ui.button(icon='chevron_right', on_click=lambda: change_p(1)).props('flat dense color=white').set_enabled(self.state['page'] < self.state['total_pages'])

    def open_new_storage_dialog(self):
        self.storage_dialog.open()

    def open_edit_storage_dialog(self, storage):
        self.storage_dialog.open(storage)

    def build_ui(self):
        self.filter_dialog = ui.dialog().props('position=right')
        with self.filter_dialog, ui.card().classes('h-full w-96 bg-gray-900 border-l border-gray-700 p-0 flex flex-col'):
             with ui.scroll_area().classes('flex-grow w-full'):
                 self.filter_pane = FilterPane(self.state, self.apply_filters, self.reset_filters, show_set_selector=False)
                 self.filter_pane.build()

        self.render_content()
        ui.timer(0.1, self.load_data, once=True)

    async def reset_filters(self):
        self.state['search_text'] = ''
        self.state['filter_rarity'] = ''
        if self.filter_pane: self.filter_pane.reset_ui_elements()
        await self.apply_filters()

def storage_page():
    page = StoragePage()
    page.build_ui()
