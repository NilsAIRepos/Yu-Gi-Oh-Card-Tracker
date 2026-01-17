from nicegui import ui, run
from src.core.persistence import persistence
from src.core.models import Deck, Collection
from src.services.ygo_api import ygo_service, ApiCard
from src.services.image_manager import image_manager
from src.core.config import config_manager
from src.ui.components.filter_pane import FilterPane
from src.ui.components.single_card_view import SingleCardView
from dataclasses import dataclass
from typing import List, Optional, Dict, Set
import logging
import asyncio
import copy
import os

logger = logging.getLogger(__name__)

@dataclass
class DeckCardViewModel:
    api_card: ApiCard
    quantity: int
    is_owned: bool # Owned in the reference collection
    owned_quantity: int
    side_quantity: int = 0
    extra_quantity: int = 0
    main_quantity: int = 0

class DeckBuilderPage:
    def __init__(self):
        ui.add_head_html('<script src="https://cdnjs.cloudflare.com/ajax/libs/Sortable/1.15.0/Sortable.min.js"></script>')
        ui.add_body_html('''
            <script>
            window.initSortable = function(elementId, groupName, pullMode, putMode) {
                var el = document.getElementById(elementId);
                if (!el) return;
                if (el._sortable) el._sortable.destroy();

                el._sortable = new Sortable(el, {
                    group: {
                        name: groupName,
                        pull: pullMode,
                        put: putMode
                    },
                    animation: 150,
                    ghostClass: 'opacity-50',
                    forceFallback: true,
                    onEnd: function (evt) {
                        var toIds = Array.from(evt.to.children).map(c => c.getAttribute('data-id')).filter(id => id);
                        var fromIds = Array.from(evt.from.children).map(c => c.getAttribute('data-id')).filter(id => id);
                        var toZone = evt.to.id.replace('deck-', '').replace('gallery-list', 'gallery');
                        var fromZone = evt.from.id.replace('deck-', '').replace('gallery-list', 'gallery');

                        var container = document.getElementById('deck-builder-container');
                        if (container) {
                            container.dispatchEvent(new CustomEvent('deck_change', {
                                detail: {
                                    to_zone: toZone,
                                    to_ids: toIds,
                                    from_zone: fromZone,
                                    from_ids: fromIds
                                },
                                bubbles: true
                            }));
                        }
                    }
                });
            }
            </script>
        ''')

        # Load persisted UI state
        ui_state = persistence.load_ui_state()
        last_deck = ui_state.get('deck_builder_last_deck')
        last_col = ui_state.get('deck_builder_last_collection')

        self.state = {
            'search_text': '',
            'filter_set': '',
            'filter_rarity': '',
            'filter_attr': '',
            'filter_card_type': ['Monster', 'Spell', 'Trap'],
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

            'only_owned': False,

            'sort_by': 'Name',
            'sort_descending': False,

            'current_deck': None, # Deck object
            'current_deck_name': last_deck, # Initialize from session
            'reference_collection': None, # Collection object for ownership check
            'reference_collection_name': last_col, # Track filename

            'available_decks': [],
            'available_collections': [],

            'all_api_cards': [], # List[ApiCard]
            'filtered_items': [], # List[ApiCard] for search results

            'page': 1,
            'page_size': 48,
            'total_pages': 1,

            'loading': False
        }

        self.single_card_view = SingleCardView()
        self.filter_pane: Optional[FilterPane] = None
        self.api_card_map = {} # ID -> ApiCard
        self.dragged_item = None

        self.search_results_container = None
        self.deck_area_container = None

    def refresh_zone(self, zone):
        if zone == 'main': self.render_main_deck_grid.refresh()
        elif zone == 'extra': self.render_extra_deck_grid.refresh()
        elif zone == 'side': self.render_side_deck_grid.refresh()


    async def load_initial_data(self):
        self.state['loading'] = True
        try:
            # Load API Data
            lang = config_manager.get_language()
            api_cards = await ygo_service.load_card_database(lang)
            self.state['all_api_cards'] = api_cards
            self.api_card_map = {c.id: c for c in api_cards}

            # Setup Filters Metadata
            sets = set()
            m_races = set()
            st_races = set()
            archetypes = set()

            for c in api_cards:
                if c.card_sets:
                    for s in c.card_sets:
                        parts = s.set_code.split('-')
                        prefix = parts[0] if len(parts) > 0 else s.set_code
                        sets.add(f"{s.set_name} | {prefix}")
                if c.archetype: archetypes.add(c.archetype)
                if "Monster" in c.type: m_races.add(c.race)
                elif "Spell" in c.type or "Trap" in c.type:
                    if c.race: st_races.add(c.race)

            self.state['available_sets'] = sorted(list(sets))
            self.state['available_monster_races'] = sorted(list(m_races))
            self.state['available_st_races'] = sorted(list(st_races))
            self.state['available_archetypes'] = sorted(list(archetypes))
            self.state['available_card_types'] = ['Monster', 'Spell', 'Trap', 'Skill']

            # Load Decks List
            self.state['available_decks'] = persistence.list_decks()

            # Load Collections List (for reference)
            cols = persistence.list_collections()
            self.state['available_collections'] = cols

            # Load Reference Collection
            target_col = self.state.get('reference_collection_name')

            if target_col and target_col in cols:
                 try:
                    self.state['reference_collection'] = await run.io_bound(persistence.load_collection, target_col)
                 except Exception as e:
                    logger.error(f"Failed to load reference collection {target_col}: {e}")
                    self.state['reference_collection'] = None
            else:
                 self.state['reference_collection'] = None
                 self.state['reference_collection_name'] = None

            # Load Deck if present in session
            if self.state['current_deck_name']:
                 await self.load_deck(f"{self.state['current_deck_name']}.ydk")

            # Apply initial filters
            await self.apply_filters()
            self.filter_pane.update_options()

        except Exception as e:
            logger.error(f"Error loading initial data: {e}", exc_info=True)
            ui.notify(f"Error loading data: {e}", type='negative')
        finally:
            self.state['loading'] = False
            self.render_header.refresh()
            self.refresh_search_results()

    async def load_deck(self, filename):
        try:
            deck = await run.io_bound(persistence.load_deck, filename)
            self.state['current_deck'] = deck
            name = filename.replace('.ydk', '')
            self.state['current_deck_name'] = name

            persistence.save_ui_state({'deck_builder_last_deck': name})

            self.refresh_deck_area()
            self.render_header.refresh()
            ui.notify(f"Loaded deck: {self.state['current_deck_name']}", type='positive')
        except Exception as e:
            logger.error(f"Error loading deck {filename}: {e}")
            ui.notify(f"Error loading deck: {e}", type='negative')

    async def save_current_deck(self):
        if not self.state['current_deck'] or not self.state['current_deck_name']:
            return
        try:
            filename = f"{self.state['current_deck_name']}.ydk"
            await run.io_bound(persistence.save_deck, self.state['current_deck'], filename)
            ui.notify('Deck saved.', type='positive')
            self.state['available_decks'] = persistence.list_decks()
            self.render_header.refresh()
        except Exception as e:
            logger.error(f"Error saving deck: {e}")
            ui.notify(f"Error saving deck: {e}", type='negative')

    async def create_new_deck(self, name):
        if not name: return
        filename = f"{name}.ydk"
        if filename in self.state['available_decks']:
             ui.notify("Deck already exists!", type='warning')
             return

        new_deck = Deck(name=name)
        self.state['current_deck'] = new_deck
        self.state['current_deck_name'] = name
        persistence.save_ui_state({'deck_builder_last_deck': name})

        await self.save_current_deck()
        self.render_header.refresh()
        self.refresh_deck_area()

    async def add_card_to_deck(self, card_id: int, quantity: int, target: str):
        if not self.state['current_deck']:
            ui.notify("Please select or create a deck first.", type='warning')
            return

        deck = self.state['current_deck']
        target_list = getattr(deck, target)

        for _ in range(quantity):
            target_list.append(card_id)

        await self.save_current_deck()
        self.refresh_zone(target)
        self.update_zone_headers()

    async def remove_card_from_deck(self, card_id: int, target: str, card_element: ui.card = None):
        if not self.state['current_deck']: return

        deck = self.state['current_deck']
        target_list = getattr(deck, target)

        if card_id in target_list:
            target_list.remove(card_id)
            await self.save_current_deck()

            if card_element:
                card_element.delete()
            else:
                self.refresh_zone(target)

            self.update_zone_headers()

    async def apply_filters(self):
        source = self.state['all_api_cards']
        res = list(source)

        txt = self.state['search_text'].lower()
        if txt:
             res = [c for c in res if txt in c.name.lower() or txt in c.type.lower() or txt in c.desc.lower()]

        if self.state['filter_card_type']:
             ctypes = self.state['filter_card_type']
             if isinstance(ctypes, str): ctypes = [ctypes]
             res = [c for c in res if any(t in c.type for t in ctypes)]

        if self.state['filter_attr']:
             res = [c for c in res if c.attribute == self.state['filter_attr']]

        if self.state['filter_monster_race']:
             res = [c for c in res if "Monster" in c.type and c.race == self.state['filter_monster_race']]
        if self.state['filter_st_race']:
             res = [c for c in res if ("Spell" in c.type or "Trap" in c.type) and c.race == self.state['filter_st_race']]
        if self.state['filter_archetype']:
             res = [c for c in res if c.archetype == self.state['filter_archetype']]

        if self.state['filter_level']:
             res = [c for c in res if c.level == int(self.state['filter_level'])]

        atk_min, atk_max = self.state['filter_atk_min'], self.state['filter_atk_max']
        if atk_min > 0 or atk_max < 5000:
             res = [c for c in res if c.atk is not None and atk_min <= int(c.atk) <= atk_max]

        key = self.state['sort_by']
        reverse = self.state['sort_descending']

        if key == 'Name':
            res.sort(key=lambda x: x.name, reverse=reverse)
        elif key == 'ATK':
            res.sort(key=lambda x: (x.atk or -1), reverse=reverse)
        elif key == 'DEF':
            res.sort(key=lambda x: (getattr(x, 'def_', None) or -1), reverse=reverse)
        elif key == 'Level':
            res.sort(key=lambda x: (x.level or -1), reverse=reverse)
        elif key == 'Newest':
            res.sort(key=lambda x: x.id, reverse=reverse)

        if self.state['only_owned'] and self.state['reference_collection']:
             owned_ids = set(c.card_id for c in self.state['reference_collection'].cards)
             res = [c for c in res if c.id in owned_ids]

        self.state['filtered_items'] = res
        self.state['page'] = 1
        self.update_pagination()
        await self.prepare_current_page_images()
        self.refresh_search_results()

    def update_pagination(self):
        count = len(self.state['filtered_items'])
        self.state['total_pages'] = (count + self.state['page_size'] - 1) // self.state['page_size']

    async def prepare_current_page_images(self):
        start = (self.state['page'] - 1) * self.state['page_size']
        end = min(start + self.state['page_size'], len(self.state['filtered_items']))
        items = self.state['filtered_items'][start:end]
        if not items: return

        url_map = {}
        for card in items:
             if card.card_images:
                 url_map[card.card_images[0].id] = card.card_images[0].image_url_small

        if url_map:
             await image_manager.download_batch(url_map, concurrency=5)

    async def reset_filters(self):
        self.state.update({
            'search_text': '',
            'filter_set': '',
            'filter_rarity': '',
            'filter_attr': '',
            'filter_card_type': ['Monster', 'Spell', 'Trap'],
            'filter_level': None,
            'filter_atk_min': 0, 'filter_atk_max': 5000,
        })
        if self.filter_pane: self.filter_pane.reset_ui_elements()
        await self.apply_filters()

    @ui.refreshable
    def render_header(self):
        with ui.row().classes('w-full items-center gap-4 q-mb-md p-4 bg-gray-900 rounded-lg border border-gray-800'):
            ui.label('Deck Builder').classes('text-h5')

            deck_options = {f: f.replace('.ydk', '') for f in self.state['available_decks']}
            deck_options['__NEW__'] = '+ New Deck'

            async def on_deck_change(e):
                if e.value == '__NEW__':
                    with ui.dialog() as d, ui.card().classes('w-96'):
                         ui.label('Create New Deck').classes('text-h6')
                         with ui.tabs().classes('w-full') as tabs:
                             t_new = ui.tab('New Empty')
                             t_import = ui.tab('Import .ydk')
                         with ui.tab_panels(tabs, value=t_new).classes('w-full'):
                             with ui.tab_panel(t_new):
                                 name_input = ui.input('Deck Name').classes('w-full')
                                 async def create():
                                     await self.create_new_deck(name_input.value)
                                     d.close()
                                 ui.button('Create', on_click=create).props('color=positive').classes('w-full q-mt-md')
                             with ui.tab_panel(t_import):
                                 ui.label('Select .ydk file').classes('text-sm text-grey')
                                 async def handle_upload(e):
                                     try:
                                         f_obj = None
                                         if hasattr(e, 'content'): f_obj = e.content
                                         elif hasattr(e, 'file'): f_obj = e.file
                                         if not f_obj: raise Exception("Could not find file content")

                                         content = (await f_obj.read()).decode('utf-8')

                                         raw_name = None
                                         if hasattr(e, 'name'): raw_name = e.name
                                         elif hasattr(f_obj, 'name'): raw_name = f_obj.name
                                         elif hasattr(f_obj, 'filename'): raw_name = f_obj.filename

                                         if not raw_name: raise Exception("Could not determine filename")

                                         name = os.path.basename(raw_name).replace('.ydk', '')
                                         filename = f"{name}.ydk"
                                         filepath = f"data/decks/{filename}"
                                         with open(filepath, 'w', encoding='utf-8') as f: f.write(content)
                                         await self.load_deck(filename)
                                         d.close()
                                         ui.notify(f"Imported deck: {name}", type='positive')
                                     except Exception as ex:
                                         logger.error(f"Error importing deck: {ex}", exc_info=True)
                                         ui.notify(f"Error importing: {ex}", type='negative')
                                 ui.upload(on_upload=handle_upload, auto_upload=True).props('accept=.ydk').classes('w-full')
                    d.open()
                elif e.value:
                    await self.load_deck(e.value)

            selected = f"{self.state['current_deck_name']}.ydk" if self.state['current_deck_name'] else None
            if selected and selected not in deck_options: selected = None
            ui.select(deck_options, value=selected, label='Current Deck', on_change=on_deck_change).classes('min-w-[200px]')

            col_options = {f: f.replace('.json', '') for f in self.state['available_collections']}
            col_options[None] = 'None (All Owned)'

            async def on_col_change(e):
                val = e.value
                persistence.save_ui_state({'deck_builder_last_collection': val})
                self.state['reference_collection_name'] = val
                if val:
                     self.state['reference_collection'] = await run.io_bound(persistence.load_collection, val)
                else:
                     self.state['reference_collection'] = None
                await self.apply_filters()
                self.refresh_deck_area()

            curr_col_file = self.state.get('reference_collection_name')
            if curr_col_file and curr_col_file not in col_options: curr_col_file = None
            ui.select(col_options, value=curr_col_file, label='Reference Collection', on_change=on_col_change).classes('min-w-[200px]')

            ui.space()

            async def on_search(e):
                self.state['search_text'] = e.value
                await self.apply_filters()
            ui.input(placeholder='Search cards...', value=self.state['search_text'], on_change=on_search).props('debounce=300 icon=search').classes('w-64')

            async def on_owned_toggle(e):
                self.state['only_owned'] = e.value
                await self.apply_filters()
            ui.switch('Owned Only', value=self.state['only_owned'], on_change=on_owned_toggle).classes('text-white')

            with ui.button(icon='filter_list', on_click=self.filter_dialog.open).props('color=primary'):
                ui.tooltip('Filters')

    def refresh_search_results(self):
        if not self.search_results_container: return
        self.search_results_container.clear()
        with self.search_results_container:
            start = (self.state['page'] - 1) * self.state['page_size']
            end = min(start + self.state['page_size'], len(self.state['filtered_items']))
            items = self.state['filtered_items'][start:end]

            with ui.row().classes('w-full items-center justify-between q-mb-xs px-2'):
                ui.label(f"{start+1}-{end} of {len(self.state['filtered_items'])}").classes('text-xs text-grey')
                with ui.row().classes('gap-1'):
                     async def change_page(delta):
                         new_p = max(1, min(self.state['total_pages'], self.state['page'] + delta))
                         if new_p != self.state['page']:
                             self.state['page'] = new_p
                             await self.prepare_current_page_images()
                             self.refresh_search_results()
                     ui.button(icon='chevron_left', on_click=lambda: change_page(-1)).props('flat dense size=sm')
                     ui.button(icon='chevron_right', on_click=lambda: change_page(1)).props('flat dense size=sm')

            with ui.scroll_area().classes('w-full flex-grow border border-gray-800 rounded p-2'):
                if not items:
                    ui.label('No cards found.').classes('text-grey italic w-full text-center')
                    return

                # Calculate owned counts for the current page
                owned_map = {}
                if self.state['reference_collection']:
                    for c in self.state['reference_collection'].cards:
                        owned_map[c.card_id] = c.total_quantity

                with ui.grid(columns='repeat(auto-fill, minmax(120px, 1fr))').classes('w-full gap-2').props('id="gallery-list"'):
                    for card in items:
                         img_id = card.card_images[0].id if card.card_images else card.id
                         img_src = f"/images/{img_id}.jpg" if image_manager.image_exists(img_id) else (card.card_images[0].image_url_small if card.card_images else None)

                         owned_qty = owned_map.get(card.id, 0)

                         with ui.card().classes('p-0 cursor-pointer hover:scale-105 transition-transform border border-gray-800 w-full h-full') \
                            .props(f'data-id="{card.id}"') \
                            .on('click', lambda c=card: self.open_deck_builder_wrapper(c)):

                             with ui.element('div').classes('relative w-full aspect-[2/3]'):
                                 ui.image(img_src).classes('w-full h-full object-cover')
                                 if owned_qty > 0:
                                     ui.label(f"{owned_qty}").classes('absolute top-1 right-1 bg-accent text-dark font-bold px-2 rounded-full text-xs')

                             with ui.column().classes('p-1 gap-0 w-full'):
                                 ui.label(card.name).classes('text-[10px] font-bold w-full leading-tight line-clamp-2 text-wrap h-6')
                                 ui.label(card.type).classes('text-[9px] text-gray-400 truncate w-full')

                ui.run_javascript('initSortable("gallery-list", "deck", "clone", false)')

    async def open_deck_builder_wrapper(self, card):
        owned_count = 0
        owned_breakdown = {}
        if self.state['reference_collection']:
             for c in self.state['reference_collection'].cards:
                 if c.card_id == card.id:
                     for v in c.variants:
                         for e in v.entries:
                             owned_breakdown[e.language] = owned_breakdown.get(e.language, 0) + e.quantity
                             owned_count += e.quantity
                     break
        await self.single_card_view.open_deck_builder(card, self.add_card_to_deck, owned_count, owned_breakdown)

    @ui.refreshable
    def render_main_deck_grid(self):
        self._render_deck_grid_content('main')

    @ui.refreshable
    def render_extra_deck_grid(self):
        self._render_deck_grid_content('extra')

    @ui.refreshable
    def render_side_deck_grid(self):
        self._render_deck_grid_content('side')

    def _render_deck_grid_content(self, target):
        deck = self.state['current_deck']
        if not deck: return

        real_card_ids = getattr(deck, target)

        # Check Ownership (for coloring)
        ref_col = self.state['reference_collection']
        owned_map = {}
        if ref_col:
            for c in ref_col.cards:
                owned_map[c.card_id] = c.total_quantity

        usage_counter = {}

        # Always render the grid to ensure Sortable can attach, even if empty.
        # min-h ensures drop target exists.
        with ui.grid(columns='repeat(auto-fill, minmax(110px, 1fr))').classes('w-full gap-2 min-h-[100px]').props(f'id="deck-{target}"'):
            for i, cid in enumerate(real_card_ids):
                # Fetch API Data
                card = self.api_card_map.get(cid)
                if not card: continue # Should not happen

                img_id = card.card_images[0].id if card.card_images else card.id
                img_src = f"/images/{img_id}.jpg" if image_manager.image_exists(img_id) else (card.card_images[0].image_url_small if card.card_images else None)

                # Ownership
                is_owned_copy = True
                if ref_col:
                    owned_total = owned_map.get(cid, 0)
                    used_so_far = usage_counter.get(cid, 0)
                    if used_so_far >= owned_total:
                        is_owned_copy = False
                    usage_counter[cid] = used_so_far + 1

                classes = 'p-0 cursor-pointer w-full aspect-[2/3] border-transparent hover:scale-105 transition-transform relative group border border-gray-800'

                if not is_owned_copy:
                    classes += ' opacity-50 grayscale'
                else:
                    classes += ' opacity-100'

                # Render Card
                card_el = ui.card().classes(classes).props(f'data-id="{cid}"')
                with card_el:
                    ui.image(img_src).classes('w-full h-full object-cover rounded')

                    with ui.element('div').classes('absolute inset-0 bg-black/50 hidden group-hover:flex items-center justify-center'):
                        ui.icon('remove', color='white').classes('text-lg')
                    ui.tooltip(card.name)

                card_el.on('click', lambda _, c=card, t=target, el=card_el: self.remove_card_from_deck(c.id, t, el))

        ui.run_javascript(f'initSortable("deck-{target}", "deck", true, true)')

    def refresh_deck_area(self):
        self.render_main_deck_grid.refresh()
        self.render_extra_deck_grid.refresh()
        self.render_side_deck_grid.refresh()
        self.update_zone_headers()

    @ui.refreshable
    def render_header_main(self): self._render_zone_header_content('Main Deck', 'main')
    @ui.refreshable
    def render_header_extra(self): self._render_zone_header_content('Extra Deck', 'extra')
    @ui.refreshable
    def render_header_side(self): self._render_zone_header_content('Side Deck', 'side')

    def _render_zone_header_content(self, title, target):
        deck = self.state['current_deck']
        count = 0
        if deck: count = len(getattr(deck, target))

        is_invalid = False
        if target == 'main':
             if count < 40 or count > 60: is_invalid = True
        elif target in ['extra', 'side']:
             if count > 15: is_invalid = True

        count_color = 'text-red-400' if is_invalid else 'text-white'

        with ui.row().classes('w-full items-center justify-between q-mb-sm'):
            with ui.row().classes('gap-1 items-center'):
                ui.label(title).classes('font-bold text-white text-xs uppercase tracking-wider')
                ui.label(f"({count})").classes(f'font-bold {count_color} text-xs uppercase tracking-wider')

            with ui.button(icon='sort', on_click=lambda t=target: self.sort_deck(t)).props('flat dense size=sm'):
                 ui.tooltip(f'Sort {title}')

    def update_zone_headers(self):
        self.render_header_main.refresh()
        self.render_header_extra.refresh()
        self.render_header_side.refresh()

    def setup_zone(self, title, target, flex_grow=False):
        height_class = 'flex-grow' if flex_grow else 'h-auto min-h-[220px]'
        with ui.column().classes(f'w-full {height_class} bg-dark border border-gray-700 p-2 rounded flex flex-col relative'):
            if target == 'main': self.render_header_main()
            elif target == 'extra': self.render_header_extra()
            elif target == 'side': self.render_header_side()

            # The container handles drops on empty space (appending)
            with ui.column().classes('w-full flex-grow bg-black/20 rounded p-2 overflow-y-auto block relative transition-colors'):

                if target == 'main': self.render_main_deck_grid()
                elif target == 'extra': self.render_extra_deck_grid()
                elif target == 'side': self.render_side_deck_grid()

    async def sort_deck(self, zone):
        if not self.state['current_deck']: return
        deck = self.state['current_deck']
        target_list = getattr(deck, zone)
        cards = []
        unknown = []
        for cid in target_list:
            if cid in self.api_card_map: cards.append(self.api_card_map[cid])
            else: unknown.append(cid)
        def sort_key(c):
             t_score = 3
             if "Monster" in c.type: t_score = 0
             elif "Spell" in c.type: t_score = 1
             elif "Trap" in c.type: t_score = 2
             lvl = c.level or 0
             return (t_score, -lvl, c.name)
        cards.sort(key=sort_key)
        new_list = [c.id for c in cards] + unknown
        setattr(deck, zone, new_list)
        await self.save_current_deck()
        self.refresh_zone(zone)
        ui.notify(f"Sorted {zone} deck.", type='positive')

    async def handle_deck_change(self, e):
        args = e.args.get('detail', {})
        to_zone = args.get('to_zone')
        to_ids_str = args.get('to_ids')
        from_zone = args.get('from_zone')
        from_ids_str = args.get('from_ids')

        # Convert strings to ints
        try:
            to_ids = [int(x) for x in to_ids_str] if to_ids_str else []
            from_ids = [int(x) for x in from_ids_str] if from_ids_str else []
        except ValueError:
            return

        deck = self.state['current_deck']
        if not deck: return

        # Validate zones
        valid_zones = ['main', 'extra', 'side']

        # Update 'to' zone
        if to_zone in valid_zones:
            setattr(deck, to_zone, to_ids)

        # Update 'from' zone if it's a valid deck zone and different from 'to'
        if from_zone in valid_zones and from_zone != to_zone:
             setattr(deck, from_zone, from_ids)

        await self.save_current_deck()

        # Refresh UI
        zones_to_refresh = {to_zone}
        if from_zone in valid_zones:
             zones_to_refresh.add(from_zone)

        for z in zones_to_refresh:
            if z in valid_zones:
                self.refresh_zone(z)

        self.update_zone_headers()

    def build_ui(self):
        self.filter_dialog = ui.dialog().props('position=right')
        with self.filter_dialog, ui.card().classes('h-full w-96 bg-gray-900 border-l border-gray-700 p-0 flex flex-col'):
             with ui.scroll_area().classes('flex-grow w-full'):
                 self.filter_pane = FilterPane(self.state, self.apply_filters, self.reset_filters)
                 self.filter_pane.build()

        self.render_header()
        with ui.row().classes('w-full h-[calc(100vh-140px)] gap-4 flex-nowrap') \
            .props('id="deck-builder-container"') \
            .on('deck_change', self.handle_deck_change):

            self.search_results_container = ui.column().classes('w-1/4 h-full bg-dark border border-gray-800 rounded flex flex-col deck-builder-search-results relative overflow-hidden')
            with ui.column().classes('flex-grow h-full relative deck-builder-deck-area overflow-hidden gap-2'):
                 self.setup_zone('Main Deck', 'main', flex_grow=True)
                 self.setup_zone('Extra Deck', 'extra')
                 self.setup_zone('Side Deck', 'side')

        self.refresh_search_results()
        ui.timer(0.1, self.load_initial_data, once=True)

def deck_builder_page():
    page = DeckBuilderPage()
    page.build_ui()
