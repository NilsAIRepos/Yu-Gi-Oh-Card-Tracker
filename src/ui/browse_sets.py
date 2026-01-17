from nicegui import ui, run
from src.services.ygo_api import ygo_service, ApiCard
from src.services.image_manager import image_manager
from src.core.constants import RARITY_RANKING
from src.ui.components.filter_pane import FilterPane
from src.ui.components.single_card_view import SingleCardView
from src.ui.collection import build_collector_rows, CollectorRow
from src.core.persistence import persistence
from src.core.utils import transform_set_code, normalize_set_code
import asyncio
import logging
from datetime import datetime
import re

logger = logging.getLogger(__name__)

def build_set_rows(api_cards, collection, target_set_code):
    rows = []
    # Build ownership map
    owned_map = {} # card_id -> CollectionCard
    if collection:
        for c in collection.cards:
            owned_map[c.card_id] = c

    # Normalize target prefix
    prefix = target_set_code.split('-')[0].lower()

    for card in api_cards:
        if not card.card_sets: continue

        # Filter sets for this specific target set
        # We match if the set code starts with the prefix (case insensitive)
        matching_sets = [s for s in card.card_sets if s.set_code.split('-')[0].lower() == prefix]

        for s in matching_sets:
            # Check ownership
            owned_count = 0
            is_owned = False

            if card.id in owned_map:
                c_card = owned_map[card.id]
                # Find matching variant
                for v in c_card.variants:
                    # Logic: exact match set_code + rarity
                    # If local set code is "MP19-EN001" and API is "MP19-EN001", match.
                    if normalize_set_code(v.set_code) == normalize_set_code(s.set_code) and v.rarity == s.set_rarity:
                         owned_count += v.total_quantity

            is_owned = owned_count > 0

            # Construct Row
            img_url = card.card_images[0].image_url_small if card.card_images else None
            if s.image_id:
                for img in card.card_images:
                    if img.id == s.image_id:
                        img_url = img.image_url_small
                        break

            rows.append(CollectorRow(
                api_card=card,
                set_code=s.set_code,
                set_name=s.set_name,
                rarity=s.set_rarity,
                price=float(s.set_price) if s.set_price else 0.0,
                image_url=img_url,
                owned_count=owned_count,
                is_owned=is_owned,
                language="EN",
                condition="Near Mint",
                first_edition=False,
                image_id=s.image_id,
                variant_id=s.variant_id
            ))

    return rows

class BrowseSetsPage:
    def __init__(self):
        self.state = {
            'view': 'gallery', # gallery, detail
            'sets': [],
            'filtered_sets': [],
            'search_query': '',
            'sort_by': 'Date',
            'sort_desc': True,
            'page': 1,
            'page_size': 24,
            'total_pages': 1,
            'selected_set': None, # code
            'selected_set_info': None,

            # Collection State
            'current_collection': None,
            'selected_collection_file': None,

            # Detail View State
            'detail_cards': [], # Raw ApiCards
            'detail_rows': [], # CollectorRows
            'detail_filtered_rows': [],
            'detail_search': '',
            'detail_sort': 'Name',
            'detail_sort_desc': False,

            # Filters for Detail View (reusing FilterPane state structure)
            'filter_set': '', # Unused/Hidden
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
            'max_owned_quantity': 100, # Max for sliders

            # Options for filters
            'available_sets': [],
            'available_monster_races': [],
            'available_st_races': [],
            'available_archetypes': [],
            'available_card_types': ['Monster', 'Spell', 'Trap', 'Skill'],

            # Set Date Filter
            'filter_date_start': None,
            'filter_date_end': None,

            # Set Count Filter
            'filter_count_min': 20,
            'filter_count_max': None,

            # Slider Bounds (Calculated from data)
            'slider_min_date': 0,
            'slider_max_date': 300,
            'slider_min_count': 0,
            'slider_max_count': 100,

            # Slider Values
            'filter_date_range': {'min': 0, 'max': 300},
            'filter_count_range': {'min': 0, 'max': 100},
        }

        # Load initial collection
        files = persistence.list_collections()
        self.state['selected_collection_file'] = files[0] if files else None

        self.single_card_view = SingleCardView()
        self.filter_pane = None # For detail view
        self.filter_dialog = None

    async def load_data(self):
        # Load Sets
        sets_info = await ygo_service.get_all_sets_info()
        self.state['sets'] = sets_info

        self.calc_filter_ranges()

        await self.apply_set_filters()

        # Load Collection
        if self.state['selected_collection_file']:
             try:
                self.state['current_collection'] = await run.io_bound(persistence.load_collection, self.state['selected_collection_file'])
             except Exception as e:
                logger.error(f"Error loading collection: {e}")

    def date_to_int(self, date_str):
        # YYYY-MM-DD -> total_months
        if not date_str: return 0
        try:
            d = datetime.strptime(date_str, "%Y-%m-%d")
            return d.year * 12 + (d.month - 1)
        except:
            return 0

    def int_to_date_str(self, val):
        # total_months -> YYYY-MM
        year = val // 12
        month = (val % 12) + 1
        return f"{year}-{month:02d}"

    def calc_filter_ranges(self):
        sets = self.state['sets']
        if not sets: return

        # Count Range
        counts = [int(s.get('count', 0)) for s in sets]
        if counts:
            min_c, max_c = min(counts), max(counts)
            self.state['slider_min_count'] = min_c
            self.state['slider_max_count'] = max_c
            # Reset values if first load or invalid
            self.state['filter_count_range'] = {'min': min_c, 'max': max_c}

        # Date Range
        dates = []
        for s in sets:
            d_str = s.get('date')
            if d_str:
                dates.append(self.date_to_int(d_str))

        if dates:
            min_d, max_d = min(dates), max(dates)
            self.state['slider_min_date'] = min_d
            self.state['slider_max_date'] = max_d
            self.state['filter_date_range'] = {'min': min_d, 'max': max_d}

    async def apply_set_filters(self):
        res = list(self.state['sets'])

        # Search
        q = self.state['search_query'].lower()
        if q:
            res = [s for s in res if q in s['name'].lower() or q in s['code'].lower()]

        # Date Filter Slider
        d_range = self.state['filter_date_range']
        # Convert slider ints to comparable values?
        # Actually easier to convert set date to int for comparison
        min_d = d_range['min']
        max_d = d_range['max']

        # Optimization: Don't filter if range is full (optional, but good for perf)
        # But we must be careful if data changed. Just filter.

        def check_date(s):
            d_str = s.get('date')
            if not d_str: return False # Exclude unknown dates if filtering? Or include? Usually exclude.
            d_val = self.date_to_int(d_str)
            return min_d <= d_val <= max_d

        res = [s for s in res if check_date(s)]

        # Count Filter Slider
        c_range = self.state['filter_count_range']
        min_c = c_range['min']
        max_c = c_range['max']

        res = [s for s in res if min_c <= int(s.get('count', 0)) <= max_c]

        # Sort
        key = self.state['sort_by']
        desc = self.state['sort_desc']

        if key == 'Name':
            res.sort(key=lambda x: x['name'], reverse=desc)
        elif key == 'Date':
            def date_key(x):
                d = x.get('date')
                return d if d else "0000-00-00"
            res.sort(key=date_key, reverse=desc)
        elif key == 'Card Count':
            res.sort(key=lambda x: int(x.get('count', 0)), reverse=desc)

        self.state['filtered_sets'] = res
        self.update_pagination()
        if hasattr(self, 'render_content'): self.render_content.refresh()

    def update_pagination(self):
        count = len(self.state['filtered_sets'])
        self.state['total_pages'] = (count + self.state['page_size'] - 1) // self.state['page_size']
        if self.state['page'] > self.state['total_pages']:
            self.state['page'] = 1

    async def open_set_detail(self, set_code):
        try:
            self.state['selected_set'] = set_code
            self.state['view'] = 'detail'
            # Check if we already have the info in the filtered list to save a call, though get_set_info is fast
            self.state['selected_set_info'] = await ygo_service.get_set_info(set_code)

            await self.load_set_details(set_code)
            self.render_content.refresh()
        except Exception as e:
            logger.error(f"Error opening set detail for {set_code}: {e}")
            ui.notify(f"Error opening set: {e}", type='negative')

    async def load_set_details(self, set_code):
        # Load Cards
        cards = await ygo_service.get_set_cards(set_code)
        self.state['detail_cards'] = cards

        # Build Rows
        rows = await run.io_bound(build_set_rows, cards, self.state['current_collection'], set_code)
        self.state['detail_rows'] = rows

        # Populate Filters
        m_races = set()
        st_races = set()
        archetypes = set()

        for c in cards:
            if c.archetype: archetypes.add(c.archetype)
            if "Monster" in c.type:
                m_races.add(c.race)
            elif "Spell" in c.type or "Trap" in c.type:
                if c.race: st_races.add(c.race)

        self.state['available_monster_races'] = sorted(list(m_races))
        self.state['available_st_races'] = sorted(list(st_races))
        self.state['available_archetypes'] = sorted(list(archetypes))

        if self.filter_pane:
            self.filter_pane.update_options()

        await self.apply_detail_filters()

    async def apply_detail_filters(self):
        res = list(self.state['detail_rows'])

        # Filter Logic (Subset of CollectionPage logic)
        txt = self.state['detail_search'].lower()
        if txt:
             res = [r for r in res if txt in r.api_card.name.lower()]

        # Reuse state filters
        if self.state['filter_rarity']:
             r = self.state['filter_rarity'].lower()
             res = [c for c in res if r == c.rarity.lower()]

        # ... Other filters ...
        # (Simplified implementation reusing logic structure)
        # Condition, Attr, Type, etc.
        # Since 'r' is CollectorRow, we use r.api_card for card props.

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

        # Ownership & Price
        min_q = self.state['filter_ownership_min']
        max_q = self.state['filter_ownership_max']
        res = [c for c in res if min_q <= c.owned_count <= max_q]

        p_min = self.state['filter_price_min']
        p_max = self.state['filter_price_max']
        res = [c for c in res if p_min <= c.price <= p_max]

        if self.state['filter_condition']:
            conds = self.state['filter_condition']
            res = [c for c in res if c.condition in conds]

        if self.state['filter_owned_lang']:
            target_lang = self.state['filter_owned_lang']
            res = [c for c in res if c.language == target_lang]

        # Sort
        key = self.state['detail_sort']
        desc = self.state['detail_sort_desc']

        if key == 'Name':
            res.sort(key=lambda x: x.api_card.name, reverse=desc)
        elif key == 'Rarity':
            # Use RARITY_RANKING index
            def rarity_rank(x):
                try: return RARITY_RANKING.index(x.rarity)
                except: return 999
            res.sort(key=rarity_rank, reverse=not desc) # Higher rank (lower index) usually top
        elif key == 'Price':
             res.sort(key=lambda x: x.price, reverse=desc)
        elif key == 'Owned':
             res.sort(key=lambda x: x.owned_count, reverse=desc)

        self.state['detail_filtered_rows'] = res
        if hasattr(self, 'render_detail_grid'): self.render_detail_grid.refresh()

    async def reset_filters(self):
        self.state.update({
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
            'filter_ownership_max': self.state['max_owned_quantity'],
            'filter_price_min': 0.0,
            'filter_price_max': 1000.0,
            'filter_owned_lang': '',
            'detail_search': '',
        })
        if self.filter_pane: self.filter_pane.reset_ui_elements()
        await self.apply_detail_filters()

    async def back_to_gallery(self):
        self.state['view'] = 'gallery'
        self.state['selected_set'] = None
        self.state['selected_set_info'] = None
        self.render_content.refresh()

    # --- Renderers ---

    def render_set_visual(self, container: ui.element, set_code: str, image_url: str):
        """
        Renders the set image or fallback fan into the provided container.
        Validates local image resolution.
        """

        def render_fan_spinner():
            container.clear()
            with container:
                ui.spinner('dots', size='lg').classes('absolute top-1/2 left-1/2 transform -translate-x-1/2 -translate-y-1/2 text-gray-600')

        async def load_fan():
             try:
                cards = await ygo_service.get_set_cards(set_code)
                container.clear()
                with container:
                     fan_div = ui.element('div').classes('relative w-full h-full bg-gray-800 overflow-hidden')
                     with fan_div:
                        if not cards:
                            ui.icon('image_not_supported', size='xl', color='grey').classes('absolute top-1/2 left-1/2 transform -translate-x-1/2 -translate-y-1/2')
                            return

                        # Take top 3
                        top_3 = cards[:3]

                        # 3rd Card (Left Back)
                        if len(top_3) > 2:
                            img = top_3[2].card_images[0].image_url_small
                            ui.image(img).classes('absolute w-[45%] top-4 left-4 opacity-60 rotate-[-15deg] shadow-lg border border-white/10 rounded')

                        # 2nd Card (Right Back)
                        if len(top_3) > 1:
                            img = top_3[1].card_images[0].image_url_small
                            ui.image(img).classes('absolute w-[45%] top-2 right-4 opacity-80 rotate-[15deg] shadow-lg border border-white/10 rounded')

                        # 1st Card (Center Front)
                        if len(top_3) > 0:
                            img = top_3[0].card_images[0].image_url_small
                            ui.image(img).classes('absolute w-[50%] left-1/2 transform -translate-x-1/2 -bottom-8 z-10 shadow-xl border border-white/20 rounded')
             except Exception as e:
                logger.error(f"Error loading fallback for set {set_code}: {e}")
                container.clear()

        # Check Local existence AND resolution
        # We check resolution synchronously here because checking ~24 files (one page) is fast enough
        # and ensures we never show a bad cached image.
        path = image_manager.get_set_image_path(set_code)
        if image_manager.set_image_exists(set_code) and image_manager.check_image_resolution(path):
             safe_code = "".join(c for c in set_code if c.isalnum() or c in ('-', '_')).strip()
             with container:
                container.clear()
                ui.image(f"/sets/{safe_code}.jpg").classes('w-full h-full object-contain')
        elif image_url:
             # Spinner
             render_fan_spinner()

             async def download_and_update():
                 # ensure_set_image checks resolution
                 path = await image_manager.ensure_set_image(set_code, image_url)
                 if path:
                     safe_code = "".join(c for c in set_code if c.isalnum() or c in ('-', '_')).strip()
                     container.clear()
                     with container:
                         ui.image(f"/sets/{safe_code}.jpg").classes('w-full h-full object-contain')
                 else:
                     # Download failed or Low Res -> Fan
                     await load_fan()

             ui.timer(0.1, download_and_update, once=True)
        else:
            render_fan_spinner()
            ui.timer(0.1, load_fan, once=True)

    def render_set_card(self, set_info):
        from functools import partial

        with ui.card().classes('w-full p-0 cursor-pointer hover:scale-105 transition-transform border border-gray-700 bg-gray-900') \
                .on('click', partial(self.open_set_detail, set_info['code'])):

            # Image Area wrapper - h-96 for better visibility
            with ui.element('div').classes('relative w-full h-96 bg-black overflow-hidden'):
                # Content Container
                content_container = ui.element('div').classes('w-full h-full')
                self.render_set_visual(content_container, set_info['code'], set_info.get('image'))

                # Overlay Info
                with ui.row().classes('absolute bottom-0 w-full bg-black/80 p-1 justify-between items-center z-20'):
                    ui.label(set_info['code']).classes('text-xs font-mono font-bold text-yellow-500')
                    count = set_info.get('count', 0)
                    ui.label(f"{count} Cards").classes('text-xs text-gray-400')

            # Text Area
            with ui.column().classes('p-2 w-full gap-0'):
                ui.label(set_info['name']).classes('text-sm font-bold truncate w-full text-white')
                date = set_info.get('date')
                ui.label(date if date else "Unknown Date").classes('text-xs text-gray-500')

    @ui.refreshable
    def render_content(self):
        if self.state['view'] == 'gallery':
            self.render_gallery_view()
        else:
            self.render_detail_view()

    def render_gallery_view(self):
        # Header Controls & Filters
        with ui.column().classes('w-full q-mb-md p-4 bg-gray-900 rounded-lg border border-gray-800 gap-6'):
            # Top Row: Title, Search, Sort
            with ui.row().classes('w-full items-center gap-4'):
                ui.label('Browse Sets').classes('text-h5 text-white')

                async def on_search(e):
                    self.state['search_query'] = e.value
                    await self.apply_set_filters()

                ui.input(placeholder='Search Sets...', on_change=on_search) \
                    .bind_value(self.state, 'search_query').props('debounce=300 icon=search dark').classes('w-64')

                ui.select(['Name', 'Date', 'Card Count'], label='Sort', value=self.state['sort_by'],
                        on_change=lambda e: self.update_filter('sort_by', e.value)) \
                        .classes('w-32').props('dark')

                async def toggle_sort():
                    self.state['sort_desc'] = not self.state['sort_desc']
                    await self.apply_set_filters()

                ui.button(icon='arrow_downward', on_click=toggle_sort) \
                    .bind_icon_from(self.state, 'sort_desc', lambda x: 'arrow_downward' if x else 'arrow_upward') \
                    .props('flat round dense color=white')

            # Filter Row: Sliders
            with ui.row().classes('w-full items-start gap-8'):
                # Date Slider
                with ui.column().classes('gap-1 flex-grow'):
                    ui.label('Release Date').classes('text-xs text-gray-400')
                    with ui.row().classes('w-full items-center gap-2'):
                        date_min_input = ui.input(placeholder='YYYY-MM').classes('w-28').props('dense borderless dark')
                        date_slider = ui.range(min=self.state['slider_min_date'],
                                               max=self.state['slider_max_date'],
                                               step=1).classes('col-grow').props('label=false color=primary')
                        date_max_input = ui.input(placeholder='YYYY-MM').classes('w-28').props('dense borderless dark')

                        # Init
                        date_min_input.value = self.int_to_date_str(self.state['filter_date_range']['min'])
                        date_max_input.value = self.int_to_date_str(self.state['filter_date_range']['max'])
                        date_slider.value = self.state['filter_date_range']

                        # Logic
                        async def on_date_slider_change(e):
                            val = e.value if e.value else self.state['filter_date_range']
                            self.state['filter_date_range'] = val
                            date_min_input.value = self.int_to_date_str(val['min'])
                            date_max_input.value = self.int_to_date_str(val['max'])
                            await self.apply_set_filters()

                        async def on_date_slider_update(e):
                            # Live update of inputs while dragging
                            # e.args might be dictionary directly
                            args = e.args
                            if isinstance(args, dict) and 'min' in args and 'max' in args:
                                date_min_input.value = self.int_to_date_str(int(args['min']))
                                date_max_input.value = self.int_to_date_str(int(args['max']))

                        date_slider.on('change', on_date_slider_change)
                        date_slider.on('update:model-value', on_date_slider_update)

                        async def on_date_input_change():
                            try:
                                d_min_str = date_min_input.value
                                d_max_str = date_max_input.value

                                # Validate format roughly
                                if len(d_min_str) == 4: d_min_str += "-01"
                                if len(d_max_str) == 4: d_max_str += "-12"
                                if len(d_min_str) == 7: d_min_str += "-01"
                                if len(d_max_str) == 7: d_max_str += "-01" # Logic: date_to_int just needs YYYY-MM-DD

                                v_min = self.date_to_int(d_min_str)
                                v_max = self.date_to_int(d_max_str)

                                # If parsing failed (returns 0), revert to bounds if 0 is not valid start
                                # But 0 is valid (year 0). If parsing fails, date_to_int returns 0.
                                # Let's trust user inputs somewhat but clamp.

                                # Clamp
                                v_min = max(self.state['slider_min_date'], v_min)
                                v_max = min(self.state['slider_max_date'], v_max)
                                if v_min > v_max: v_min = v_max

                                self.state['filter_date_range'] = {'min': v_min, 'max': v_max}
                                date_slider.value = self.state['filter_date_range']
                                await self.apply_set_filters()
                            except Exception as e:
                                logger.error(f"Date filter error: {e}")

                        date_min_input.on('change', on_date_input_change)
                        date_max_input.on('change', on_date_input_change)

                # Count Slider
                with ui.column().classes('gap-1 w-1/3'):
                    ui.label('Card Count').classes('text-xs text-gray-400')
                    with ui.row().classes('w-full items-center gap-2'):
                        count_min_input = ui.number().classes('w-16').props('dense borderless dark')
                        count_slider = ui.range(min=self.state['slider_min_count'],
                                                max=self.state['slider_max_count'],
                                                step=1).classes('col-grow').props('label=false color=primary')
                        count_max_input = ui.number().classes('w-16').props('dense borderless dark')

                        # Init
                        count_min_input.value = self.state['filter_count_range']['min']
                        count_max_input.value = self.state['filter_count_range']['max']
                        count_slider.value = self.state['filter_count_range']

                        # Logic
                        async def on_count_slider_change(e):
                            val = e.value if e.value else self.state['filter_count_range']
                            self.state['filter_count_range'] = val
                            count_min_input.value = val['min']
                            count_max_input.value = val['max']
                            await self.apply_set_filters()

                        async def on_count_slider_update(e):
                            args = e.args
                            if isinstance(args, dict) and 'min' in args and 'max' in args:
                                count_min_input.value = int(args['min'])
                                count_max_input.value = int(args['max'])

                        count_slider.on('change', on_count_slider_change)
                        count_slider.on('update:model-value', on_count_slider_update)

                        async def on_count_input_change():
                            try:
                                v_min = int(count_min_input.value or 0)
                                v_max = int(count_max_input.value or 0)

                                v_min = max(self.state['slider_min_count'], v_min)
                                v_max = min(self.state['slider_max_count'], v_max)
                                if v_min > v_max: v_min = v_max

                                self.state['filter_count_range'] = {'min': v_min, 'max': v_max}
                                count_slider.value = self.state['filter_count_range']
                                await self.apply_set_filters()
                            except: pass

                        count_min_input.on('change', on_count_input_change)
                        count_max_input.on('change', on_count_input_change)

        self.render_pagination()

        start = (self.state['page'] - 1) * self.state['page_size']
        end = start + self.state['page_size']
        visible_sets = self.state['filtered_sets'][start:end]

        # Increased grid size as requested (300px min)
        with ui.grid(columns='repeat(auto-fill, minmax(300px, 1fr))').classes('w-full gap-4'):
            for s in visible_sets:
                self.render_set_card(s)

        self.render_pagination()

    def render_pagination(self):
        if self.state['total_pages'] <= 1:
            return

        with ui.row().classes('w-full justify-center items-center gap-2 q-my-sm'):
            ui.button(icon='chevron_left', on_click=lambda: self.change_page(-1)).props('flat dense color=white').set_enabled(self.state['page'] > 1)

            async def on_page_change(e):
                if e.value is None: return
                try:
                    p = int(e.value)
                except:
                    return
                p = max(1, min(p, self.state['total_pages']))
                if p != self.state['page']:
                    self.state['page'] = p
                    self.render_content.refresh()

            # Debounce prevents refresh while typing (e.g. typing "12")
            ui.number(value=self.state['page'], on_change=on_page_change) \
                .props(f'min=1 max={self.state["total_pages"]} dense dark outlined debounce=800 hide-bottom-space') \
                .classes('w-20')

            ui.label(f"/ {self.state['total_pages']}").classes('text-white')
            ui.button(icon='chevron_right', on_click=lambda: self.change_page(1)).props('flat dense color=white').set_enabled(self.state['page'] < self.state['total_pages'])

    def change_page(self, delta):
        self.state['page'] += delta
        self.render_content.refresh()

    def render_detail_view(self):
        if not self.state['selected_set_info']:
            ui.label("Loading...").classes('text-white')
            return

        info = self.state['selected_set_info']

        # Header
        with ui.row().classes('w-full items-start gap-6 mb-6 p-6 bg-gray-900 rounded-lg border border-gray-800'):
            # Image
            with ui.element('div').classes('w-64 h-64 relative bg-black rounded shadow-lg overflow-hidden'):
                 container = ui.element('div').classes('w-full h-full')
                 self.render_set_visual(container, info['code'], info.get('image'))

            # Info
            with ui.column().classes('gap-2'):
                ui.label(info['name']).classes('text-h3 font-bold text-white leading-none')
                with ui.row().classes('gap-4 items-center'):
                    ui.label(info['code']).classes('text-xl font-mono text-yellow-500 font-bold')
                    ui.label(f"{info.get('count', 0)} Cards").classes('text-lg text-gray-400')
                    if info.get('date'):
                        ui.label(f"Released: {info['date']}").classes('text-lg text-gray-400')

                ui.button('Back to Sets', icon='arrow_back', on_click=self.back_to_gallery).props('flat color=white').classes('mt-4')

            ui.space()

            # Collection Selector
            with ui.column().classes('items-end'):
                 files = persistence.list_collections()
                 file_options = {f: (f[:-5] if f.endswith('.json') else f) for f in files}

                 async def change_col(e):
                     self.state['selected_collection_file'] = e.value
                     await self.load_data() # Reloads collection
                     # Need to reload rows too
                     await self.load_set_details(self.state['selected_set'])

                 ui.select(file_options, label='Collection', value=self.state['selected_collection_file'], on_change=change_col).classes('w-40').props('dark')

        # Controls & Grid
        with ui.row().classes('w-full gap-4'):
             # Left Filter Pane (if implemented) or Button
             # Reusing FilterPane logic requires a container

             with ui.column().classes('w-full'):
                  # Filter/Sort Bar
                  with ui.row().classes('w-full items-center gap-4 bg-gray-800 p-2 rounded mb-4'):
                       async def on_detail_search(e):
                           self.state['detail_search'] = e.value
                           await self.apply_detail_filters()

                       ui.input(placeholder='Filter cards...', on_change=on_detail_search).props('dark icon=search debounce=300').classes('w-64')

                       async def on_detail_sort(e):
                           self.state['detail_sort'] = e.value
                           await self.apply_detail_filters()

                       ui.select(['Name', 'Rarity', 'Price', 'Owned'], label='Sort', value=self.state['detail_sort'], on_change=on_detail_sort).props('dark').classes('w-40')

                       async def toggle_detail_sort():
                           self.state['detail_sort_desc'] = not self.state['detail_sort_desc']
                           await self.apply_detail_filters()

                       ui.button(icon='arrow_downward', on_click=toggle_detail_sort).bind_icon_from(self.state, 'detail_sort_desc', lambda x: 'arrow_downward' if x else 'arrow_upward').props('flat round dense color=white')

                       ui.space()
                       ui.button('Filters', icon='filter_list', on_click=self.filter_dialog.open).props('color=primary')

                  self.render_detail_grid()

    @ui.refreshable
    def render_detail_grid(self):
        rows = self.state['detail_filtered_rows']

        # Trigger background download for these cards (Fire and forget)
        # This ensures images are cached for next visit while current view uses remote/lazy if needed
        if rows:
            to_download = {r.image_id: r.image_url for r in rows if r.image_id and r.image_url}
            # Use create_task to run in background without blocking render
            asyncio.create_task(image_manager.download_batch(to_download, high_res=False))

        # Reuse CollectorRow Grid Logic (Copy of render_collectors_grid)
        flag_map = {'EN': '🇬🇧', 'DE': '🇩🇪', 'FR': '🇫🇷', 'IT': '🇮🇹', 'ES': '🇪🇸', 'PT': '🇵🇹', 'JP': '🇯🇵', 'KR': '🇰🇷', 'CN': '🇨🇳'}
        cond_map = {'Mint': 'MT', 'Near Mint': 'NM', 'Played': 'PL', 'Damaged': 'DM'}

        with ui.grid(columns='repeat(auto-fill, minmax(160px, 1fr))').classes('w-full gap-4'):
            for item in rows:
                opacity = "opacity-100" if item.is_owned else "opacity-60 grayscale"
                border = "border-accent" if item.is_owned else "border-gray-700"

                img_src = item.image_url
                # Simplified check as image_manager handles id check internally usually but here we have URL
                # Use local path if possible
                if item.image_id and image_manager.image_exists(item.image_id):
                    img_src = f"/images/{item.image_id}.jpg"

                with ui.card().classes(f'collection-card w-full p-0 cursor-pointer {opacity} border {border} hover:scale-105 transition-transform') \
                        .on('click', lambda c=item: self.open_single_view(c)):

                    with ui.element('div').classes('relative w-full aspect-[2/3] bg-black'):
                        if img_src: ui.image(img_src).classes('w-full h-full object-cover').props('loading="lazy"')

                        if item.is_owned:
                             ui.label(f"{item.owned_count}").classes('absolute top-1 right-1 bg-accent text-dark font-bold px-2 rounded-full text-xs')

                        with ui.row().classes('absolute bottom-0 left-0 bg-black/80 text-white text-[10px] px-1 gap-1 items-center rounded-tr'):
                            ui.label(item.rarity).classes('font-bold text-yellow-500 truncate max-w-[100px]')

                        ui.label(item.set_code).classes('absolute bottom-0 right-0 bg-black/80 text-white text-[10px] px-1 font-mono rounded-tl')

                    with ui.column().classes('p-2 gap-0 w-full'):
                        ui.label(item.api_card.name).classes('text-xs font-bold truncate w-full')
                        ui.label(f"${item.price:.2f}").classes('text-xs text-green-400')

    async def open_single_view(self, row: CollectorRow):
        # Wrapper for SingleCardView
        async def on_save(c, set_code, rarity, language, quantity, condition, first_edition, image_id, variant_id, mode):
             # Save to collection
             if not self.state['current_collection']: return

             col = self.state['current_collection']
             from src.services.collection_editor import CollectionEditor
             CollectionEditor.apply_change(col, c, set_code, rarity, language, quantity, condition, first_edition, image_id, variant_id, mode)
             await run.io_bound(persistence.save_collection, col, self.state['selected_collection_file'])

             # Reload details
             await self.load_set_details(self.state['selected_set'])
             self.render_detail_grid.refresh()
             ui.notify('Collection Updated', type='positive')

        await self.single_card_view.open_collectors(
            card=row.api_card,
            owned_count=row.owned_count,
            set_code=row.set_code,
            rarity=row.rarity,
            set_name=row.set_name,
            language=row.language,
            condition=row.condition,
            first_edition=row.first_edition,
            image_url=row.image_url,
            image_id=row.image_id,
            set_price=row.price,
            current_collection=self.state['current_collection'],
            save_callback=on_save,
            variant_id=row.variant_id
        )

    def build_ui(self):
        # Detail Filter Dialog
        self.filter_dialog = ui.dialog().props('position=right')
        with self.filter_dialog, ui.card().classes('h-full w-96 bg-gray-900 border-l border-gray-700 p-0 flex flex-col'):
             with ui.scroll_area().classes('flex-grow w-full'):
                 # Initialize FilterPane with show_set_selector=False
                 self.filter_pane = FilterPane(self.state, self.apply_detail_filters, self.reset_filters, show_set_selector=False)
                 self.filter_pane.build()

        self.render_content()
        ui.timer(0.1, self.load_data, once=True)

    async def update_filter(self, key, value):
        self.state[key] = value
        await self.apply_set_filters()

def browse_sets_page():
    page = BrowseSetsPage()
    page.build_ui()
