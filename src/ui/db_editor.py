from nicegui import ui, run
from src.core.persistence import persistence
from src.core.models import ApiCard
from src.services.ygo_api import ygo_service
from src.services.image_manager import image_manager
from src.core.config import config_manager
from src.core.utils import generate_variant_id, normalize_set_code
from src.ui.components.filter_pane import FilterPane
from src.ui.components.single_card_view import SingleCardView
from dataclasses import dataclass
from typing import List, Optional
import logging
import re

logger = logging.getLogger(__name__)

@dataclass
class DbEditorRow:
    api_card: ApiCard
    set_code: str
    set_name: str
    rarity: str
    image_url: str
    image_id: Optional[int]
    variant_id: str
    set_price: float = 0.0

def build_db_rows(api_cards: List[ApiCard]) -> List[DbEditorRow]:
    rows = []
    for card in api_cards:
        img_url = card.card_images[0].image_url_small if card.card_images else None
        default_image_id = card.card_images[0].id if card.card_images else None

        if card.card_sets:
            for cset in card.card_sets:
                row_img_url = img_url
                if cset.image_id:
                     for img in card.card_images:
                         if img.id == cset.image_id:
                             row_img_url = img.image_url_small
                             break

                price = 0.0
                if cset.set_price:
                    try: price = float(cset.set_price)
                    except: pass

                rows.append(DbEditorRow(
                    api_card=card,
                    set_code=cset.set_code,
                    set_name=cset.set_name,
                    rarity=cset.set_rarity,
                    image_url=row_img_url,
                    image_id=cset.image_id or default_image_id,
                    variant_id=cset.variant_id,
                    set_price=price
                ))
        else:
             # Card with no sets
             rows.append(DbEditorRow(
                api_card=card,
                set_code="NO SET",
                set_name="No Set Info",
                rarity="Common",
                image_url=img_url,
                image_id=default_image_id,
                variant_id=generate_variant_id(card.id, "NO SET", "Common", default_image_id),
                set_price=0.0
            ))
    return rows

class DbEditorPage:
    def __init__(self):
        saved_state = persistence.load_ui_state()
        self.state = {
            'cards_rows': [],
            'filtered_items': [],
            'language': config_manager.get_language(),

            # Filters
            'search_text': '',
            'filter_set': '',
            'filter_rarity': '',
            'filter_attr': '',
            'filter_card_type': ['Monster', 'Spell', 'Trap'],
            'filter_condition': [], # Not used but kept for FilterPane compatibility
            'filter_monster_race': '',
            'filter_st_race': '',
            'filter_archetype': '',
            'filter_monster_category': [],
            'filter_level': None,
            'filter_atk_min': 0,
            'filter_atk_max': 5000,
            'filter_def_min': 0,
            'filter_def_max': 5000,
            'filter_ownership_min': 0, # Not used
            'filter_ownership_max': 100, # Not used
            'filter_price_min': 0.0,
            'filter_price_max': 1000.0,
            'filter_owned_lang': '', # Not used
            'only_owned': False, # Not used

            'sort_by': saved_state.get('db_editor_sort_by', 'Name'),
            'sort_descending': saved_state.get('db_editor_sort_descending', False),
            'view_mode': saved_state.get('db_editor_view_mode', 'grid'),

            'page': 1,
            'page_size': 48,
            'total_pages': 1,
            'max_owned_quantity': 100, # Mock for FilterPane
            'available_sets': [],
            'available_monster_races': [],
            'available_st_races': [],
            'available_archetypes': [],
            'available_card_types': ['Monster', 'Spell', 'Trap', 'Skill'],
        }

        self.filter_pane: Optional[FilterPane] = None
        self.single_card_view = SingleCardView()
        self.pagination_showing_label = None
        self.pagination_total_label = None

    async def load_data(self):
        logger.info(f"Loading DB Editor data... (Language: {self.state['language']})")
        try:
            lang_code = self.state['language'].lower() if self.state['language'] else 'en'
            api_cards = await ygo_service.load_card_database(lang_code)
        except Exception as e:
            logger.error(f"Error loading database: {e}")
            ui.notify(f"Error loading database: {e}", type='negative')
            return

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
            elif ("Spell" in c.type or "Trap" in c.type) and c.race: st_races.add(c.race)

        self.state['available_sets'] = sorted(list(sets))
        self.state['available_monster_races'] = sorted(list(m_races))
        self.state['available_st_races'] = sorted(list(st_races))
        self.state['available_archetypes'] = sorted(list(archetypes))

        self.state['cards_rows'] = await run.io_bound(build_db_rows, api_cards)
        await self.apply_filters()
        self.update_filter_ui()

    def update_filter_ui(self):
        if self.filter_pane: self.filter_pane.update_options()

    async def reset_filters(self):
        self.state.update({
            'search_text': '',
            'filter_set': '',
            'filter_rarity': '',
            'filter_attr': '',
            'filter_card_type': ['Monster', 'Spell', 'Trap'],
            'filter_monster_race': '',
            'filter_st_race': '',
            'filter_archetype': '',
            'filter_monster_category': [],
            'filter_level': None,
            'filter_atk_min': 0,
            'filter_atk_max': 5000,
            'filter_def_min': 0,
            'filter_def_max': 5000,
            'filter_price_min': 0.0,
            'filter_price_max': 1000.0,
        })
        if self.filter_pane: self.filter_pane.reset_ui_elements()
        await self.apply_filters()

    async def prepare_current_page_images(self):
        start = (self.state['page'] - 1) * self.state['page_size']
        end = min(start + self.state['page_size'], len(self.state['filtered_items']))
        items = self.state['filtered_items'][start:end]
        if not items: return
        url_map = {item.image_id: item.image_url for item in items if item.image_id and item.image_url}
        if url_map:
             await image_manager.download_batch(url_map, concurrency=10)

    async def apply_filters(self):
        res = list(self.state['cards_rows'])
        txt = self.state['search_text'].lower()
        if txt:
            def matches(item):
                if (txt in item.api_card.name.lower() or
                    txt in item.api_card.type.lower() or
                    txt in item.api_card.desc.lower() or
                    txt in item.set_code.lower()):
                    return True
                return False
            res = [c for c in res if matches(c)]

        # Price Filter
        p_min = self.state['filter_price_min']
        p_max = self.state['filter_price_max']
        res = [c for c in res if p_min <= c.set_price <= p_max]

        if self.state['filter_attr']:
            res = [c for c in res if c.api_card.attribute == self.state['filter_attr']]

        if self.state['filter_card_type']:
             ctypes = self.state['filter_card_type']
             if isinstance(ctypes, str): ctypes = [ctypes]
             res = [c for c in res if any(t in c.api_card.type for t in ctypes)]

        if self.state['filter_monster_race']:
             res = [c for c in res if "Monster" in c.api_card.type and c.api_card.race == self.state['filter_monster_race']]
        if self.state['filter_st_race']:
             res = [c for c in res if ("Spell" in c.api_card.type or "Trap" in c.api_card.type) and c.api_card.race == self.state['filter_st_race']]
        if self.state['filter_archetype']:
             res = [c for c in res if c.api_card.archetype == self.state['filter_archetype']]

        if self.state['filter_monster_category']:
             categories = self.state['filter_monster_category']
             if isinstance(categories, list) and categories:
                 res = [c for c in res if all(c.api_card.matches_category(cat) for cat in categories)]

        if self.state['filter_level']:
             res = [c for c in res if c.api_card.level == int(self.state['filter_level'])]

        atk_min, atk_max = self.state['filter_atk_min'], self.state['filter_atk_max']
        if atk_min > 0 or atk_max < 5000:
             res = [c for c in res if c.api_card.atk is not None and atk_min <= int(c.api_card.atk) <= atk_max]

        def_min, def_max = self.state['filter_def_min'], self.state['filter_def_max']
        if def_min > 0 or def_max < 5000:
             res = [c for c in res if getattr(c.api_card, 'def_', None) is not None and def_min <= getattr(c.api_card, 'def_', -1) <= def_max]

        if self.state['filter_set']:
            s_val = self.state['filter_set']
            is_strict = '|' in s_val
            if is_strict:
                target_prefix = s_val.split('|')[-1].strip().lower()
                def match_strict(c):
                    parts = c.set_code.split('-')
                    c_prefix = parts[0].lower() if parts else c.set_code.lower()
                    return c_prefix == target_prefix
                res = [c for c in res if match_strict(c)]
            else:
                s_txt = s_val.strip().lower()
                res = [c for c in res if s_txt in c.set_code.lower() or s_txt in c.set_name.lower()]

        if self.state['filter_rarity']:
            r = self.state['filter_rarity'].lower()
            res = [c for c in res if r == c.rarity.lower()]

        key = self.state['sort_by']
        reverse = self.state.get('sort_descending', False)

        if key == 'Name':
            res.sort(key=lambda x: x.api_card.name, reverse=reverse)
        elif key == 'ATK':
            res.sort(key=lambda x: (x.api_card.atk or -1), reverse=reverse)
        elif key == 'DEF':
            res.sort(key=lambda x: (getattr(x.api_card, 'def_', None) or -1), reverse=reverse)
        elif key == 'Level':
            res.sort(key=lambda x: (x.api_card.level or -1), reverse=reverse)
        elif key == 'Newest':
            res.sort(key=lambda x: x.api_card.id, reverse=reverse)
        elif key == 'Price':
             res.sort(key=lambda x: x.set_price, reverse=reverse)

        self.state['filtered_items'] = res
        self.state['page'] = 1
        self.update_pagination()
        await self.prepare_current_page_images()
        if hasattr(self, 'render_card_display'): self.render_card_display.refresh()
        self.update_pagination_labels()

    def update_pagination(self):
        count = len(self.state['filtered_items'])
        self.state['total_pages'] = (count + self.state['page_size'] - 1) // self.state['page_size']

    def update_pagination_labels(self):
        if self.pagination_showing_label:
            start = (self.state['page'] - 1) * self.state['page_size']
            end = min(start + self.state['page_size'], len(self.state['filtered_items']))
            self.pagination_showing_label.text = f"Showing {start+1}-{end} of {len(self.state['filtered_items'])}"
        if self.pagination_total_label:
            self.pagination_total_label.text = f"/ {max(1, self.state['total_pages'])}"

    async def open_edit_view(self, row: DbEditorRow):
        logger.info(f"Opening edit view for card: {row.api_card.name}, Variant: {row.variant_id}")
        async def on_save(set_code, rarity, image_id):
            logger.info(f"Saving changes for variant {row.variant_id}")
            success = await ygo_service.update_card_variant(
                card_id=row.api_card.id,
                variant_id=row.variant_id,
                set_code=set_code,
                set_rarity=rarity,
                image_id=image_id,
                language=self.state['language']
            )
            if success:
                # Reload data to reflect changes
                await self.load_data()
            return success

        async def on_delete():
            logger.info(f"Deleting variant {row.variant_id}")
            lang = self.state['language'].lower() if self.state['language'] else 'en'
            success = await ygo_service.delete_card_variant(
                card_id=row.api_card.id,
                variant_id=row.variant_id,
                language=lang
            )
            if success:
                ui.notify(f"Deleted variant {row.variant_id}", type='positive')
                await self.load_data()
            else:
                ui.notify(f"Failed to delete variant {row.variant_id}", type='negative')

        async def on_add(set_code, rarity, image_id):
            logger.info(f"Adding new variant: {set_code} / {rarity}")
            # Try to resolve set name from global or fallback to custom
            s_name = await ygo_service.get_set_name_by_code(set_code) or "Custom Set"

            new_variant = await ygo_service.add_card_variant(
                card_id=row.api_card.id,
                set_name=s_name,
                set_code=set_code,
                set_rarity=rarity,
                image_id=image_id,
                language=self.state['language']
            )

            if new_variant:
                 ui.notify(f"Added new variant: {set_code}", type='positive')
                 await self.load_data()
            else:
                 ui.notify(f"Variant already exists: {set_code} / {rarity}", type='warning')

        try:
            await self.single_card_view.open_db_edit_view(
                card=row.api_card,
                variant_id=row.variant_id,
                set_code=row.set_code,
                rarity=row.rarity,
                image_id=row.image_id,
                on_save_callback=on_save,
                on_delete_callback=on_delete,
                on_add_callback=on_add
            )
            logger.info("Edit view opened successfully")
        except Exception as e:
            logger.error(f"Error opening edit view: {e}", exc_info=True)

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
        is_local = image_manager.image_exists(img_id, high_res=True)
        initial_src = f"/images/{img_id}_high.jpg" if is_local else high_res_url
        with ui.tooltip().classes('bg-transparent shadow-none border-none p-0 overflow-visible z-[9999] max-w-none') \
                         .props('style="max-width: none" delay=1050') as tooltip:
            if initial_src:
                ui.image(initial_src).classes('w-auto h-[65vh] min-w-[1000px] object-contain rounded-lg shadow-2xl').props('fit=contain')

    def render_grid(self, items: List[DbEditorRow]):
        with ui.grid(columns='repeat(auto-fill, minmax(160px, 1fr))').classes('w-full gap-4'):
            for item in items:
                img_src = item.image_url
                if image_manager.image_exists(item.image_id): img_src = f"/images/{item.image_id}.jpg"

                with ui.card().classes('collection-card w-full p-0 cursor-pointer opacity-100 border border-gray-700 hover:scale-105 transition-transform') \
                        .on('click', lambda c=item: self.open_edit_view(c)):
                    with ui.element('div').classes('relative w-full aspect-[2/3] bg-black'):
                        if img_src: ui.image(img_src).classes('w-full h-full object-cover')
                        ui.label(item.set_code).classes('absolute bottom-0 right-0 bg-black/80 text-white text-[10px] px-1 font-mono rounded-tl')
                    with ui.column().classes('p-2 gap-0 w-full'):
                        ui.label(item.api_card.name).classes('text-xs font-bold truncate w-full')
                        ui.label(f"{item.rarity}").classes('text-[10px] text-gray-400')
                    self._setup_card_tooltip(item.api_card, specific_image_id=item.image_id)

    def render_list(self, items: List[DbEditorRow]):
        headers = ['Image', 'Name', 'Set', 'Rarity', 'Price']
        cols = '60px 4fr 2fr 1.5fr 1fr'
        with ui.column().classes('w-full gap-1'):
            with ui.grid(columns=cols).classes('w-full bg-gray-800 p-2 font-bold rounded'):
                for h in headers: ui.label(h)
            for item in items:
                img_src = item.image_url
                if image_manager.image_exists(item.image_id): img_src = f"/images/{item.image_id}.jpg"

                with ui.grid(columns=cols).classes('w-full bg-gray-900 p-1 items-center rounded hover:bg-gray-700 transition cursor-pointer') \
                        .on('click', lambda c=item: self.open_edit_view(c)):
                    with ui.image(img_src).classes('h-10 w-8 object-cover'):
                         self._setup_card_tooltip(item.api_card, specific_image_id=item.image_id)
                    ui.label(item.api_card.name).classes('truncate text-sm font-bold')
                    with ui.column().classes('gap-0'):
                        ui.label(item.set_code).classes('text-xs font-mono font-bold text-yellow-500')
                        ui.label(item.set_name).classes('text-xs text-gray-400 truncate')
                    ui.label(item.rarity).classes('text-xs')
                    ui.label(f"${item.set_price:.2f}").classes('text-sm text-green-400')

    @ui.refreshable
    def render_card_display(self):
        start = (self.state['page'] - 1) * self.state['page_size']
        end = min(start + self.state['page_size'], len(self.state['filtered_items']))
        page_items = self.state['filtered_items'][start:end]

        if not page_items:
            ui.label('No items found.').classes('w-full text-center text-xl text-grey italic q-mt-xl')
            return

        if self.state['view_mode'] == 'grid':
            self.render_grid(page_items)
        else:
            self.render_list(page_items)

    def render_header(self):
        with ui.row().classes('w-full items-center gap-4 q-mb-md p-4 bg-gray-900 rounded-lg border border-gray-800'):
            ui.label('Card Database Editor').classes('text-h5')

            async def on_search(e):
                self.state['search_text'] = e.value
                await self.apply_filters()

            with ui.input(placeholder='Search...', on_change=on_search) \
                .props('debounce=300 icon=search').classes('w-64') as i:
                i.value = self.state['search_text']
                ui.tooltip('Search by card name, set code, etc.')

            async def on_sort_change(e):
                self.state['sort_by'] = e.value
                if e.value != 'Name': self.state['sort_descending'] = True
                else: self.state['sort_descending'] = False
                persistence.save_ui_state({'db_editor_sort_by': e.value, 'db_editor_sort_descending': self.state['sort_descending']})
                self.render_header.refresh()
                await self.apply_filters()

            with ui.row().classes('items-center gap-1'):
                with ui.select(['Name', 'ATK', 'DEF', 'Level', 'Newest', 'Price'], value=self.state['sort_by'], label='Sort',
                        on_change=on_sort_change).classes('w-32'): pass

                async def toggle_sort():
                    self.state['sort_descending'] = not self.state['sort_descending']
                    persistence.save_ui_state({'db_editor_sort_descending': self.state['sort_descending']})
                    self.render_header.refresh()
                    await self.apply_filters()

                icon = 'arrow_downward' if self.state.get('sort_descending') else 'arrow_upward'
                ui.button(icon=icon, on_click=toggle_sort).props('flat round dense color=white')

            ui.separator().props('vertical')

            with ui.button_group():
                is_grid = self.state['view_mode'] == 'grid'
                with ui.button(icon='grid_view', on_click=lambda: self.switch_view_mode('grid')).props(f'flat={not is_grid} color=accent'): pass
                with ui.button(icon='list', on_click=lambda: self.switch_view_mode('list')).props(f'flat={is_grid} color=accent'): pass

            ui.space()
            ui.button(icon='filter_list', on_click=self.filter_dialog.open).props('color=primary size=lg')

    def switch_view_mode(self, mode):
        self.state['view_mode'] = mode
        persistence.save_ui_state({'db_editor_view_mode': mode})
        self.render_card_display.refresh()
        self.render_header.refresh()

    def build_ui(self):
        self.filter_dialog = ui.dialog().props('position=right')
        with self.filter_dialog, ui.card().classes('h-full w-96 bg-gray-900 border-l border-gray-700 p-0 flex flex-col'):
             with ui.scroll_area().classes('flex-grow w-full'):
                 self.filter_pane = FilterPane(self.state, self.apply_filters, self.reset_filters)
                 self.filter_pane.build()

        self.render_header()

        # Pagination
        with ui.row().classes('w-full items-center justify-between q-mb-sm px-4'):
            self.pagination_showing_label = ui.label("Loading...").classes('text-grey')
            with ui.row().classes('items-center gap-2'):
                async def change_page(delta):
                    new_p = max(1, min(self.state['total_pages'], self.state['page'] + delta))
                    if new_p != self.state['page']:
                        self.state['page'] = new_p
                        await self.prepare_current_page_images()
                        self.render_card_display.refresh()
                        self.update_pagination_labels()

                ui.button(icon='chevron_left', on_click=lambda: change_page(-1)).props('flat dense')

                async def set_page(p):
                    new_val = int(p) if p else 1
                    self.state['page'] = new_val
                    await self.prepare_current_page_images()
                    self.render_card_display.refresh()
                    self.update_pagination_labels()

                n_input = ui.number(min=1).bind_value(self.state, 'page').props('dense borderless input-class="text-center"').classes('w-20')
                n_input.on('change', lambda e: set_page(e.value))
                n_input.on('keydown.enter', lambda: set_page(self.state['page']))

                self.pagination_total_label = ui.label("/ 1")
                ui.button(icon='chevron_right', on_click=lambda: change_page(1)).props('flat dense')

        self.render_card_display()
        self.update_pagination_labels()
        ui.timer(0.1, self.load_data, once=True)

def db_editor_page():
    page = DbEditorPage()
    page.build_ui()
