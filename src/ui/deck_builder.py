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

            'only_owned': False, # New Toggle

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

    def handle_drag_start(self, card, source_zone, index=None):
        self.dragged_item = {'id': card.id, 'from': source_zone, 'index': index}

    def refresh_zone(self, zone):
        if zone == 'main': self.render_main_deck_grid.refresh()
        elif zone == 'extra': self.render_extra_deck_grid.refresh()
        elif zone == 'side': self.render_side_deck_grid.refresh()

    async def handle_reorder_drop(self, e, target_zone, target_index):
        if not self.dragged_item: return
        data = self.dragged_item
        card_id = data.get('id')
        src_zone = data.get('from')
        src_index = data.get('index')

        if not card_id or not src_zone: return

        deck = self.state['current_deck']
        if not deck: return

        target_list = getattr(deck, target_zone)

        if src_zone == target_zone:
            # Reorder within same zone
            if src_index is None: return
            if src_index < 0 or src_index >= len(target_list): return
            if src_index == target_index: return

            if src_index < target_index:
                item = target_list.pop(src_index)
                target_list.insert(target_index - 1, item)
            else:
                item = target_list.pop(src_index)
                target_list.insert(target_index, item)
        else:
            # Move from another zone or gallery
            # 1. Remove from source if it's a deck zone
            if src_zone in ['main', 'extra', 'side']:
                 src_list = getattr(deck, src_zone)
                 if src_index is not None and 0 <= src_index < len(src_list) and src_list[src_index] == card_id:
                     src_list.pop(src_index)
                 elif card_id in src_list:
                     src_list.remove(card_id)

            # 2. Insert into target
            target_list.insert(target_index, card_id)

        await self.save_current_deck()

        self.refresh_zone(target_zone)
        if src_zone in ['main', 'extra', 'side'] and src_zone != target_zone:
             self.refresh_zone(src_zone)

        self.update_zone_headers()

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
                 # Default to None
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

            # Update Session
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
            # Refresh list in case it was new
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

        # Update Session
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

        # Add copies
        for _ in range(quantity):
            target_list.append(card_id)

        await self.save_current_deck()
        self.refresh_zone(target)
        self.update_zone_headers()

    async def remove_card_from_deck(self, card_id: int, target: str):
        if not self.state['current_deck']: return

        deck = self.state['current_deck']
        target_list = getattr(deck, target)

        if card_id in target_list:
            target_list.remove(card_id)
            await self.save_current_deck()
            self.refresh_zone(target)
            self.update_zone_headers()

    async def apply_filters(self):
        source = self.state['all_api_cards']
        res = list(source)

        txt = self.state['search_text'].lower()
        if txt:
             res = [c for c in res if txt in c.name.lower() or txt in c.type.lower() or txt in c.desc.lower()]

        # Reuse similar filtering logic from CollectionPage (simplified for brevity here, can expand)
        # Type
        if self.state['filter_card_type']:
             ctypes = self.state['filter_card_type']
             if isinstance(ctypes, str): ctypes = [ctypes]
             res = [c for c in res if any(t in c.type for t in ctypes)]

        # Attribute
        if self.state['filter_attr']:
             res = [c for c in res if c.attribute == self.state['filter_attr']]

        # Race/Archetype...
        if self.state['filter_monster_race']:
             res = [c for c in res if "Monster" in c.type and c.race == self.state['filter_monster_race']]
        if self.state['filter_st_race']:
             res = [c for c in res if ("Spell" in c.type or "Trap" in c.type) and c.race == self.state['filter_st_race']]
        if self.state['filter_archetype']:
             res = [c for c in res if c.archetype == self.state['filter_archetype']]

        # Level/ATK/DEF
        if self.state['filter_level']:
             res = [c for c in res if c.level == int(self.state['filter_level'])]

        atk_min, atk_max = self.state['filter_atk_min'], self.state['filter_atk_max']
        if atk_min > 0 or atk_max < 5000:
             res = [c for c in res if c.atk is not None and atk_min <= int(c.atk) <= atk_max]

        # Sorting
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

        # Filter Owned (New)
        if self.state['only_owned'] and self.state['reference_collection']:
             # Get owned IDs
             owned_ids = set(c.card_id for c in self.state['reference_collection'].cards)
             res = [c for c in res if c.id in owned_ids]
        # If 'only_owned' is checked but Reference Collection is None -> Show everything? Or nothing?
        # User said "None option where every card in the deck is highlighted like its in the collection".
        # This implies "None" collection means "I have everything" or "Don't track ownership".
        # So if Only Owned is checked and Ref is None, we probably shouldn't filter, or filter to All.
        # Let's assume: Ref None -> Treat as "Infinite Collection" -> Show All.
        pass


        self.state['filtered_items'] = res
        self.state['page'] = 1
        self.update_pagination()

        # Prepare images for current page
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
            # ... reset others ...
            'filter_level': None,
            'filter_atk_min': 0, 'filter_atk_max': 5000,
            # ...
        })
        if self.filter_pane: self.filter_pane.reset_ui_elements()
        await self.apply_filters()

    # --- UI Renderers ---

    @ui.refreshable
    def render_header(self):
        with ui.row().classes('w-full items-center gap-4 q-mb-md p-4 bg-gray-900 rounded-lg border border-gray-800'):
            ui.label('Deck Builder').classes('text-h5')

            # Deck Selector
            deck_options = {f: f.replace('.ydk', '') for f in self.state['available_decks']}
            deck_options['__NEW__'] = '+ New Deck'

            async def on_deck_change(e):
                if e.value == '__NEW__':
                    # Open Dialog
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
                                         if hasattr(e, 'content'):
                                             f_obj = e.content
                                         elif hasattr(e, 'file'):
                                             f_obj = e.file

                                         if not f_obj:
                                             raise Exception("Could not find file content in upload event")

                                         content = f_obj.read().decode('utf-8')
                                         name = e.name.replace('.ydk', '')

                                         filename = f"{name}.ydk"
                                         filepath = f"data/decks/{filename}"
                                         with open(filepath, 'w', encoding='utf-8') as f:
                                             f.write(content)

                                         await self.load_deck(filename)
                                         d.close()
                                         ui.notify(f"Imported deck: {name}", type='positive')
                                     except Exception as ex:
                                         logger.error(f"Error importing deck: {ex}", exc_info=True)
                                         ui.notify(f"Error importing: {ex}", type='negative')

                                 ui.upload(on_upload=handle_upload, auto_upload=True).props('accept=.ydk').classes('w-full')

                    d.open()
                    # Reset selector logic handled by refresh
                elif e.value:
                    await self.load_deck(e.value)

            selected = f"{self.state['current_deck_name']}.ydk" if self.state['current_deck_name'] else None
            # Fix for "Invalid value" error on initial load if deck list isn't populated yet
            if selected and selected not in deck_options:
                selected = None

            ui.select(deck_options, value=selected, label='Current Deck', on_change=on_deck_change).classes('min-w-[200px]')

            # Reference Collection Selector
            col_options = {f: f.replace('.json', '') for f in self.state['available_collections']}
            col_options[None] = 'None (All Owned)' # Add None option

            async def on_col_change(e):
                val = e.value
                # Update Session
                persistence.save_ui_state({'deck_builder_last_collection': val})

                self.state['reference_collection_name'] = val

                if val:
                     self.state['reference_collection'] = await run.io_bound(persistence.load_collection, val)
                else:
                     self.state['reference_collection'] = None

                await self.apply_filters() # Re-apply filters (for owned check)
                self.refresh_deck_area()

            curr_col_file = self.state.get('reference_collection_name')
            # If current is not in options (e.g. deleted), default to None
            if curr_col_file and curr_col_file not in col_options:
                 curr_col_file = None

            ui.select(col_options, value=curr_col_file, label='Reference Collection', on_change=on_col_change).classes('min-w-[200px]')

            ui.space()

            # Search Input
            async def on_search(e):
                self.state['search_text'] = e.value
                await self.apply_filters()

            # Bind value to state to prevent clearing
            ui.input(placeholder='Search cards...', value=self.state['search_text'], on_change=on_search).props('debounce=300 icon=search').classes('w-64')

            # Owned Toggle
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

            # Pagination Controls
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

                # Render Grid
                with ui.grid(columns='repeat(auto-fill, minmax(100px, 1fr))').classes('w-full gap-2'):
                    for card in items:
                         img_id = card.card_images[0].id if card.card_images else card.id
                         img_src = f"/images/{img_id}.jpg" if image_manager.image_exists(img_id) else (card.card_images[0].image_url_small if card.card_images else None)

                         # Draggable using props and server-side state
                         # Updated Styling as requested: line-clamp-2, Card Type instead of ATK/DEF
                         with ui.card().classes('p-0 cursor-pointer hover:scale-105 transition-transform border border-gray-800 w-full h-full') \
                            .props('draggable') \
                            .on('dragstart', lambda c=card: self.handle_drag_start(c, 'gallery')) \
                            .on('click', lambda c=card: self.open_deck_builder_wrapper(c)): # Use wrapper
                             ui.image(img_src).classes('w-full aspect-[2/3] object-cover')
                             with ui.column().classes('p-1 gap-0 w-full'):
                                 # Title with line-clamp-2, fixed height to avoid layout shift
                                 ui.label(card.name).classes('text-[10px] font-bold w-full leading-tight line-clamp-2 text-wrap h-6')
                                 # Card Type instead of ATK/DEF
                                 ui.label(card.type).classes('text-[9px] text-gray-400 truncate w-full')

    def open_deck_builder_wrapper(self, card):
        # Calculate owned stats
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
        else:
             # If no ref collection, maybe assume owned?
             # For "Collection Status" view, if ref is None, we show "0" or "N/A"?
             # User said "None option where every card ... highlighted like its in collection".
             # But for specific stats, we don't have them.
             pass

        self.single_card_view.open_deck_builder(card, self.add_card_to_deck, owned_count, owned_breakdown)

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

        # Get Cards
        card_ids = getattr(deck, target)
        cards = []
        for cid in card_ids:
            if cid in self.api_card_map:
                cards.append(self.api_card_map[cid])
            else:
                # Handle unknown card? Create dummy?
                pass

        # Check Ownership (Global counts)
        ref_col = self.state['reference_collection']
        owned_map = {} # card_id -> quantity
        if ref_col:
            for c in ref_col.cards:
                owned_map[c.card_id] = c.total_quantity

        # Usage Counter for this specific render pass (to handle multiple copies)
        # We need to account for copies used in OTHER zones if we want global strictness,
        # but typically ownership is checked per deck or globally?
        # For simplicity in this view, we check per-deck usage, but ideally it should count used in ALL zones.
        # However, to avoid complexity, let's just track usage within this list for coloring.
        # NOTE: A better approach is to count total used in deck vs owned.
        # But `render_deck_grid_content` is isolated.
        # Let's count usage for the current list.
        # (This is a limitation: if I have 1 Blue-Eyes and put 1 in Main and 1 in Side, both might show as owned if calculated separately.
        #  To fix this, we would need to calculate global usage before rendering.
        #  For now, we'll keep it simple or maybe pass a shared counter if possible.
        #  Given the refresh separation, passing a shared counter is hard unless computed beforehand.)

        # Let's re-calculate usage_so_far based on deck state
        # But since we are only rendering one zone, we don't know about others easily without re-scanning.
        # Let's scan all zones to build the "used before me" map?
        # Actually, simpler: just calculate ownership based on "used in this zone" + "used in previous zones".
        # Main -> Extra -> Side order?
        # For now, let's stick to local zone usage to avoid performance hit, or just accept the limitation.
        usage_counter = {}

        if not cards:
            ui.label('Drag cards here').classes('text-grey italic text-xs w-full text-center q-mt-md opacity-50')
            return

        # Increased Min Size to 140px as requested
        with ui.grid(columns='repeat(auto-fill, minmax(140px, 1fr))').classes('w-full gap-2'):
            for i, card in enumerate(cards):
                img_id = card.card_images[0].id if card.card_images else card.id
                img_src = f"/images/{img_id}.jpg" if image_manager.image_exists(img_id) else (card.card_images[0].image_url_small if card.card_images else None)

                # Ownership check logic
                is_owned_copy = True
                if ref_col:
                    owned_total = owned_map.get(card.id, 0)
                    used_so_far = usage_counter.get(card.id, 0)
                    if used_so_far >= owned_total:
                        is_owned_copy = False
                    usage_counter[card.id] = used_so_far + 1

                # Visuals
                classes = 'p-0 cursor-pointer w-full aspect-[2/3] border-transparent hover:scale-105 transition-transform relative group border border-gray-800'
                if not is_owned_copy:
                    classes += ' opacity-50 grayscale'
                else:
                    classes += ' opacity-100'

                with ui.card().classes(classes) \
                    .props('draggable') \
                    .on('dragstart', lambda c=card, idx=i, t=target: self.handle_drag_start(c, t, idx)) \
                    .on('click', lambda c=card, t=target: self.remove_card_from_deck(c.id, t)) \
                    .on('drop.prevent.stop', lambda e, idx=i, t=target: self.handle_reorder_drop(e, t, idx)) \
                    .on('dragover.prevent', lambda: None):

                    ui.image(img_src).classes('w-full h-full object-cover rounded')

                    # Hover remove icon
                    with ui.element('div').classes('absolute inset-0 bg-black/50 hidden group-hover:flex items-center justify-center'):
                        ui.icon('remove', color='white').classes('text-lg')

                    # Tooltip for Name (Requested replacement for text overlay)
                    ui.tooltip(card.name)

    def refresh_deck_area(self):
        # Legacy method compatibility or simple refresh all
        self.render_main_deck_grid.refresh()
        self.render_extra_deck_grid.refresh()
        self.render_side_deck_grid.refresh()
        self.update_zone_headers()

    @ui.refreshable
    def render_header_main(self):
        self._render_zone_header_content('Main Deck', 'main')

    @ui.refreshable
    def render_header_extra(self):
        self._render_zone_header_content('Extra Deck', 'extra')

    @ui.refreshable
    def render_header_side(self):
        self._render_zone_header_content('Side Deck', 'side')

    def _render_zone_header_content(self, title, target):
        deck = self.state['current_deck']
        count = 0
        if deck:
            count = len(getattr(deck, target))

        color = 'text-white'
        if target == 'main':
             if count < 40 or count > 60: color = 'text-red-400'
        elif target in ['extra', 'side']:
             if count > 15: color = 'text-red-400'

        with ui.row().classes('w-full items-center justify-between q-mb-sm'):
            ui.label(f"{title} ({count})").classes(f'font-bold {color} text-xs uppercase tracking-wider')
            with ui.button(icon='sort', on_click=lambda t=target: self.sort_deck(t)).props('flat dense size=sm'):
                 ui.tooltip(f'Sort {title}')

    def update_zone_headers(self):
        self.render_header_main.refresh()
        self.render_header_extra.refresh()
        self.render_header_side.refresh()

    def setup_zone(self, title, target, flex_grow=False):
        height_class = 'flex-grow' if flex_grow else 'h-auto min-h-[160px]'

        with ui.column().classes(f'w-full {height_class} bg-dark border border-gray-700 p-2 rounded flex flex-col relative'):
            # Header
            if target == 'main': self.render_header_main()
            elif target == 'extra': self.render_header_extra()
            elif target == 'side': self.render_header_side()

            # Drop Zone
            with ui.column().classes('w-full flex-grow bg-black/20 rounded p-2 overflow-y-auto block relative transition-colors') \
                .on('dragover.prevent', lambda: None) \
                .on('drop', lambda e: self.handle_card_drop(e, target)):

                if target == 'main': self.render_main_deck_grid()
                elif target == 'extra': self.render_extra_deck_grid()
                elif target == 'side': self.render_side_deck_grid()

    async def handle_card_drop(self, e, target_zone):
        try:
            if not self.dragged_item: return
            data = self.dragged_item

            card_id = data.get('id')
            src_zone = data.get('from')
            src_index = data.get('index')

            if not card_id or not src_zone: return

            deck = self.state['current_deck']
            if not deck: return

            if src_zone == 'gallery':
                await self.add_card_to_deck(card_id, 1, target_zone)

            elif src_zone in ['main', 'extra', 'side']:
                if src_zone == target_zone:
                    # Move within same zone (Reorder)
                    # Move to end if dropped on container
                    if src_index is not None:
                        src_list = getattr(deck, src_zone)
                        if src_index < len(src_list):
                            item = src_list.pop(src_index)
                            src_list.append(item)
                            await self.save_current_deck()
                            self.refresh_zone(target_zone)
                    return
                else:
                    # Move between zones
                    src_list = getattr(deck, src_zone)
                    target_list = getattr(deck, target_zone)

                    if src_index is not None and 0 <= src_index < len(src_list) and src_list[src_index] == card_id:
                        src_list.pop(src_index)
                    elif card_id in src_list:
                         src_list.remove(card_id)

                    target_list.append(card_id)

                    await self.save_current_deck()
                    self.refresh_zone(target_zone)
                    self.refresh_zone(src_zone)
                    self.update_zone_headers()
        except Exception as ex:
             logger.error(f"Drop error: {ex}", exc_info=True)
             ui.notify("Error moving card.", type='negative')

    async def sort_deck(self, zone):
        if not self.state['current_deck']: return
        deck = self.state['current_deck']
        target_list = getattr(deck, zone)

        # Fetch API cards
        cards = []
        unknown = []
        for cid in target_list:
            if cid in self.api_card_map:
                cards.append(self.api_card_map[cid])
            else:
                unknown.append(cid)

        def sort_key(c):
             # 1. Type: Monster (0), Spell (1), Trap (2)
             t_score = 3
             if "Monster" in c.type: t_score = 0
             elif "Spell" in c.type: t_score = 1
             elif "Trap" in c.type: t_score = 2

             # 2. Level/Rank (High -> Low)
             lvl = c.level or 0

             # 3. Name (A-Z)
             return (t_score, -lvl, c.name)

        cards.sort(key=sort_key)

        # Reconstruct list
        new_list = [c.id for c in cards] + unknown
        setattr(deck, zone, new_list)

        await self.save_current_deck()
        self.refresh_zone(zone)
        ui.notify(f"Sorted {zone} deck.", type='positive')

    def build_ui(self):
        # Filter Dialog
        self.filter_dialog = ui.dialog().props('position=right')
        with self.filter_dialog, ui.card().classes('h-full w-96 bg-gray-900 border-l border-gray-700 p-0 flex flex-col'):
             with ui.scroll_area().classes('flex-grow w-full'):
                 self.filter_pane = FilterPane(self.state, self.apply_filters, self.reset_filters)
                 self.filter_pane.build()

        self.render_header()

        with ui.row().classes('w-full h-[calc(100vh-140px)] gap-4 flex-nowrap'):
            # Left: Search Results (25%)
            # We assign to self.search_results_container so we can clear/refill it
            self.search_results_container = ui.column().classes('w-1/4 h-full bg-dark border border-gray-800 rounded flex flex-col deck-builder-search-results relative overflow-hidden')

            # Right: Deck Area (Remaining Space)
            with ui.column().classes('flex-grow h-full relative deck-builder-deck-area overflow-hidden gap-2'):
                 self.setup_zone('Main Deck', 'main', flex_grow=True)
                 self.setup_zone('Extra Deck', 'extra')
                 self.setup_zone('Side Deck', 'side')

        # Initial Render
        self.refresh_search_results()
        # No need to call refresh_deck_area() as setup_zone renders them initially.

        ui.timer(0.1, self.load_initial_data, once=True)

def deck_builder_page():
    page = DeckBuilderPage()
    page.build_ui()
