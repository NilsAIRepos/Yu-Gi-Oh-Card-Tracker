from nicegui import ui, run
from src.services.ygo_api import ygo_service, ApiCard
from src.services.image_manager import image_manager
from src.core.constants import RARITY_RANKING
from src.ui.components.filter_pane import FilterPane
from src.ui.components.single_card_view import SingleCardView
from src.ui.collection import build_collector_rows, CollectorRow, CardViewModel
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

            if collection is None:
                is_owned = True
                owned_count = 0
            elif card.id in owned_map:
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

def build_consolidated_rows(api_cards, collection):
    rows = []
    owned_map = {}
    if collection:
        for c in collection.cards:
            owned_map[c.card_id] = c

    for card in api_cards:
        is_owned = False
        qty = 0
        owned_langs = set()
        owned_conds = set()

        if collection is None:
            # All Owned Mode
            is_owned = True
            qty = 0 # Visuals should handle is_owned=True with qty=0 correctly (opaque but no badge)
        else:
            c_card = owned_map.get(card.id)
            if c_card:
                qty = c_card.total_quantity
                is_owned = qty > 0
                for v in c_card.variants:
                    for e in v.entries:
                        owned_langs.add(e.language)
                        owned_conds.add(e.condition)

        # Calculate lowest price
        lowest = 0.0
        prices = []
        if card.card_prices:
            p = card.card_prices[0]
            for val in [p.cardmarket_price, p.tcgplayer_price, p.coolstuffinc_price]:
                 if val:
                     try: prices.append(float(val))
                     except: pass
        if prices: lowest = min(prices)

        rows.append(CardViewModel(
            api_card=card,
            owned_quantity=qty,
            is_owned=is_owned,
            lowest_price=lowest,
            owned_languages=owned_langs,
            owned_conditions=owned_conds
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
            'view_scope': 'collectors', # collectors, consolidated
            'detail_cards': [], # Raw ApiCards
            'detail_rows': [], # Legacy reference, will point to collectors
            'detail_rows_collectors': [],
            'detail_rows_consolidated': [],
            'detail_filtered_rows': [],
            'detail_page': 1,
            'detail_page_size': 48,
            'detail_total_pages': 1,

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
            'filter_owned_only': False,
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
        self.state['selected_collection_file'] = None

        self.single_card_view = SingleCardView()
        self.filter_pane = None # For detail view
        self.filter_dialog = None

    async def load_data(self):
        # Load Sets
        sets_info = await ygo_service.get_all_sets_info()

        # Override with real counts from local DB
        try:
            real_counts = await ygo_service.get_real_set_counts()
            for s in sets_info:
                code = s.get('code')
                if code:
                    prefix = code.split('-')[0].upper()
                    if prefix in real_counts:
                         s['count'] = real_counts[prefix]
        except Exception as e:
            logger.error(f"Error loading real set counts: {e}")

        self.state['sets'] = sets_info

        self.calc_filter_ranges()
        if hasattr(self, 'render_filter_row'): self.render_filter_row.refresh()

        await self.apply_set_filters()

        # Reset current collection
        self.state['current_collection'] = None

        # Load Collection
        self.state['current_collection'] = None
        if self.state['selected_collection_file']:
             try:
                self.state['current_collection'] = await run.io_bound(persistence.load_collection, self.state['selected_collection_file'])
             except Exception as e:
                logger.error(f"Error loading collection: {e}")

    def date_to_int(self, date_str):
        # YYYY-MM-DD -> total_months
        if not date_str: return None
        try:
            d = datetime.strptime(date_str, "%Y-%m-%d")
            return d.year * 12 + (d.month - 1)
        except:
            return None

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
            val = self.date_to_int(d_str)
            if val is not None and val > 0: # Check > 0 to exclude Year 0 issues
                dates.append(val)

        if dates:
            min_d, max_d = min(dates), max(dates)
            self.state['slider_min_date'] = min_d
            self.state['slider_max_date'] = max_d
            # Only reset if we haven't set a valid range yet or if current range is invalid
            # But usually we want to start with full range
            self.state['filter_date_range'] = {'min': min_d, 'max': max_d}

    async def apply_set_filters(self):
        res = list(self.state['sets'])

        # Search
        q = (self.state['search_query'] or "").lower()
        if q:
            res = [s for s in res if q in s['name'].lower() or q in s['code'].lower()]

        # Date Filter Slider
        d_range = self.state['filter_date_range']
        try:
            min_d = int(d_range['min'])
            max_d = int(d_range['max'])
        except (ValueError, TypeError) as e:
            logger.error(f"Invalid date range values: {d_range} - {e}")
            min_d = 0
            max_d = 999999

        def check_date(s):
            d_str = s.get('date')
            d_val = self.date_to_int(d_str)
            if d_val is None: return False
            return min_d <= d_val <= max_d

        try:
            res = [s for s in res if check_date(s)]
        except Exception as e:
            logger.error(f"Error filtering dates: {e}")

        # Count Filter Slider
        c_range = self.state['filter_count_range']
        try:
            min_c = int(c_range['min'])
            max_c = int(c_range['max'])
        except (ValueError, TypeError) as e:
            logger.error(f"Invalid count range values: {c_range} - {e}")
            min_c = 0
            max_c = 9999

        try:
            res = [s for s in res if min_c <= int(s.get('count', 0)) <= max_c]
        except Exception as e:
             logger.error(f"Error filtering counts: {e}")

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
        if self.state['view'] == 'gallery':
             if hasattr(self, 'render_gallery_content'): self.render_gallery_content.refresh()
        elif hasattr(self, 'render_content'):
             self.render_content.refresh()

    def update_pagination(self):
        count = len(self.state['filtered_sets'])
        self.state['total_pages'] = (count + self.state['page_size'] - 1) // self.state['page_size']
        if self.state['page'] > self.state['total_pages']:
            self.state['page'] = 1

    async def open_set_detail(self, set_code):
        logger.info(f"Opening set detail for: {set_code}")
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
        self.state['detail_rows_collectors'] = rows
        self.state['detail_rows'] = rows # Keep legacy ref just in case

        con_rows = await run.io_bound(build_consolidated_rows, cards, self.state['current_collection'])
        self.state['detail_rows_consolidated'] = con_rows

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
        is_cons = self.state['view_scope'] == 'consolidated'
        source = self.state['detail_rows_consolidated'] if is_cons else self.state['detail_rows_collectors']
        res = list(source)

        # Filter Logic
        txt = self.state['detail_search'].lower()
        if txt:
             res = [r for r in res if txt in r.api_card.name.lower()]

        if self.state.get('filter_owned_only'):
             res = [c for c in res if c.is_owned]

        # Reuse state filters
        if self.state['filter_rarity']:
             r = self.state['filter_rarity'].lower()
             if is_cons:
                 res = [c for c in res if c.api_card.card_sets and any(r == cs.set_rarity.lower() for cs in c.api_card.card_sets)]
             else:
                 res = [c for c in res if r == c.rarity.lower()]

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

        def get_qty(c):
            return c.owned_quantity if hasattr(c, 'owned_quantity') else c.owned_count

        res = [c for c in res if min_q <= get_qty(c) <= max_q]

        p_min = self.state['filter_price_min']
        p_max = self.state['filter_price_max']

        def get_price(c):
            return c.lowest_price if hasattr(c, 'lowest_price') else c.price

        res = [c for c in res if p_min <= get_price(c) <= p_max]

        if self.state['filter_condition']:
            conds = self.state['filter_condition']
            if is_cons:
                 res = [c for c in res if any(cond in c.owned_conditions for cond in conds)]
            else:
                 res = [c for c in res if c.condition in conds]

        if self.state['filter_owned_lang']:
            target_lang = self.state['filter_owned_lang']
            if is_cons:
                 res = [c for c in res if target_lang in c.owned_languages]
            else:
                 res = [c for c in res if c.language == target_lang]

        # Sort
        key = self.state['detail_sort']
        desc = self.state['detail_sort_desc']

        if key == 'Name':
            res.sort(key=lambda x: x.api_card.name, reverse=desc)
        elif key == 'Rarity':
            # Use RARITY_RANKING index
            def rarity_rank(x):
                if hasattr(x, 'rarity'):
                     r = x.rarity
                else:
                     # For consolidated, use lowest rarity index found? or highest?
                     # Let's say we sort by "best" rarity available in the set if possible?
                     # Actually consolidated rows don't have a single rarity.
                     # We can sort by best rarity index of any set in the card?
                     r = "Common" # Fallback
                     if x.api_card.card_sets:
                         # Find best rarity
                         best_idx = 999
                         for cs in x.api_card.card_sets:
                             try: idx = RARITY_RANKING.index(cs.set_rarity)
                             except: idx = 999
                             if idx < best_idx:
                                 best_idx = idx
                                 r = cs.set_rarity
                try: return RARITY_RANKING.index(r)
                except: return 999
            res.sort(key=rarity_rank, reverse=not desc)
        elif key == 'Price':
             res.sort(key=lambda x: get_price(x), reverse=desc)
        elif key == 'Owned':
             res.sort(key=lambda x: get_qty(x), reverse=desc)
        elif key == 'Set Code':
             current_set_prefix = self.state.get('selected_set', '').split('-')[0].lower() if is_cons else ""
             def get_set_code(x):
                 if is_cons:
                     # For consolidated, try to find the matching set code for current set
                     if x.api_card.card_sets:
                         for s in x.api_card.card_sets:
                             if s.set_code.split('-')[0].lower() == current_set_prefix:
                                 return s.set_code
                         return x.api_card.card_sets[0].set_code
                     return ""
                 else:
                     return x.set_code
             res.sort(key=get_set_code, reverse=desc)

        self.state['detail_filtered_rows'] = res
        self.state['detail_page'] = 1

        count = len(res)
        self.state['detail_total_pages'] = (count + self.state['detail_page_size'] - 1) // self.state['detail_page_size']

        if hasattr(self, 'render_detail_grid'): self.render_detail_grid.refresh()
        if hasattr(self, 'render_view_scope_toggles'): self.render_view_scope_toggles.refresh()
        if hasattr(self, 'render_detail_pagination_controls'): self.render_detail_pagination_controls.refresh()

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
            'filter_owned_only': False,
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

    def _setup_card_tooltip(self, card: ApiCard, specific_image_id: int = None):
        if not card: return

        # Default to first image
        target_img = card.card_images[0] if card.card_images else None

        # If specific ID provided, try to find it
        if specific_image_id and card.card_images:
            for img in card.card_images:
                if img.id == specific_image_id:
                    target_img = img
                    break

        if not target_img:
             return

        img_id = target_img.id
        high_res_url = target_img.image_url
        low_res_url = target_img.image_url_small

        # Check local high-res existence immediately
        is_local = image_manager.image_exists(img_id, high_res=True)
        initial_src = f"/images/{img_id}_high.jpg" if is_local else (high_res_url or low_res_url)

        # Create tooltip with transparent background and no padding
        with ui.tooltip().classes('bg-transparent shadow-none border-none p-0 overflow-visible z-[9999] max-w-none') \
                         .props('style="max-width: none" delay=1050') as tooltip:
            # Image at 65vh height and 1000px min width for readability
            if initial_src:
                ui.image(initial_src).classes('w-auto h-[65vh] min-w-[1000px] object-contain rounded-lg shadow-2xl') \
                                     .props('fit=contain')

            # Trigger download on show if needed
            if not is_local and high_res_url:
                async def ensure_high():
                    # Check again to avoid redundant downloads
                    if not image_manager.image_exists(img_id, high_res=True):
                         await image_manager.ensure_image(img_id, high_res_url, high_res=True)

                tooltip.on('show', ensure_high)

    def render_set_visual(self, container: ui.element, set_code: str, image_url: str):
        """
        Renders the set image or fallback fan into the provided container.
        Validates local image resolution.
        """

        def render_fan_spinner():
            if container.is_deleted: return
            container.clear()
            with container:
                ui.spinner('dots', size='lg').classes('absolute top-1/2 left-1/2 transform -translate-x-1/2 -translate-y-1/2 text-gray-600')

        async def load_fan():
             if container.is_deleted: return
             try:
                cards = await ygo_service.get_set_cards(set_code)
                if container.is_deleted: return
                container.clear()
                with container:
                     fan_div = ui.element('div').classes('relative w-full h-full bg-gray-800 overflow-hidden')
                     with fan_div:
                        if not cards:
                            ui.icon('image_not_supported', size='xl', color='grey').classes('absolute top-1/2 left-1/2 transform -translate-x-1/2 -translate-y-1/2')
                            return

                        # Sort by rarity (rarest first)
                        def get_card_rarity_index(c):
                             target_prefix = set_code.split('-')[0].lower()
                             best_idx = 999
                             if c.card_sets:
                                 for s in c.card_sets:
                                     if s.set_code.split('-')[0].lower() == target_prefix:
                                         try:
                                             idx = RARITY_RANKING.index(s.set_rarity)
                                         except ValueError:
                                             idx = 999
                                         if idx < best_idx:
                                             best_idx = idx
                             return best_idx

                        sorted_cards = sorted(cards, key=get_card_rarity_index)

                        # Take top 9 (rarest)
                        top_cards = sorted_cards[:9]
                        # Reverse so the rarest (first in sorted list) ends up last in rendering order (on top)
                        top_cards.reverse()

                        # Scattered pile positions (ordered from back to front)
                        positions = [
                            # Background Layer
                            {'left': '-5%', 'top': '5%', 'rotate': '-15deg'},
                            {'left': '60%', 'top': '5%', 'rotate': '15deg'},
                            {'left': '-5%', 'top': '60%', 'rotate': '-10deg'},
                            {'left': '60%', 'top': '60%', 'rotate': '10deg'},

                            # Mid Layer
                            {'left': '10%', 'top': '30%', 'rotate': '-25deg'},
                            {'left': '45%', 'top': '30%', 'rotate': '25deg'},

                            # Inner Layer
                            {'left': '20%', 'top': '15%', 'rotate': '-5deg'},
                            {'left': '35%', 'top': '50%', 'rotate': '5deg'},

                            # Top (Rarest) - Center
                            {'left': '27.5%', 'top': '32.5%', 'rotate': '0deg'},
                        ]

                        # Adjust positions if we have fewer cards to ensure the last one (rarest) is somewhat central
                        # But simpler to just fill from the start.
                        # If we have 1 card -> index 0 (was rarest) -> Pos 0.
                        # Wait, if we have 1 card, top_cards has 1. reversed -> same.
                        # It will use position 0 which is Left/Top. Not ideal.
                        # If we have 1 card, we want it center.
                        # Let's map based on how many we have.

                        # Actually, if we use the last N positions for N cards, the "top" card always lands on the "Center-ish top" position.
                        # Let's try that.

                        start_index = len(positions) - len(top_cards)
                        # e.g. 7 cards: start 0.
                        # e.g. 1 card: start 6. Pos 6 is center.

                        for i, card in enumerate(top_cards):
                            pos_idx = start_index + i
                            if pos_idx < 0: pos_idx = 0 # Should not happen
                            pos = positions[pos_idx]

                            img_url = card.card_images[0].image_url_small if card.card_images else None
                            if img_url:
                                ui.image(img_url).classes('absolute w-[45%] shadow-lg border border-white/10 rounded') \
                                    .style(f"left: {pos['left']}; top: {pos['top']}; transform: rotate({pos['rotate']});")
             except Exception as e:
                logger.error(f"Error loading fallback for set {set_code}: {e}")
                if not container.is_deleted: container.clear()

        # Check Local existence AND resolution
        path = image_manager.get_set_image_path(set_code)
        if image_manager.set_image_exists(set_code) and image_manager.check_image_resolution(path):
             safe_code = "".join(c for c in set_code if c.isalnum() or c in ('-', '_')).strip()
             with container:
                ui.image(f"/sets/{safe_code}.jpg").classes('w-full h-full object-contain')
        elif image_url:
             # Spinner
             render_fan_spinner()

             async def download_and_update():
                 if container.is_deleted: return
                 try:
                     # ensure_set_image checks resolution
                     path = await image_manager.ensure_set_image(set_code, image_url)
                     if container.is_deleted: return

                     if path:
                         safe_code = "".join(c for c in set_code if c.isalnum() or c in ('-', '_')).strip()
                         container.clear()
                         with container:
                             ui.image(f"/sets/{safe_code}.jpg").classes('w-full h-full object-contain')
                     else:
                         # Download failed or Low Res -> Fan
                         await load_fan()
                 except Exception as e:
                     logger.error(f"Error updating set image: {e}")

             # Use container context to ensure timer is cleaned up if container is removed
             with container:
                 ui.timer(0.1, download_and_update, once=True)
        else:
            render_fan_spinner()
            with container:
                ui.timer(0.1, load_fan, once=True)

    def render_set_card(self, set_info):
        async def on_click(e):
            logger.info(f"Click detected on set: {set_info['code']}")
            await self.open_set_detail(set_info['code'])

        # Use a plain div with q-card class
        with ui.element('div').classes('q-card w-full p-0 cursor-pointer hover:scale-105 transition-transform border border-gray-700 bg-gray-900 shadow-2xl relative') \
                .on('click', on_click):

            # Image Area wrapper - h-[600px] as requested
            with ui.element('div').classes('relative w-full h-[600px] bg-black overflow-hidden'):
                # Content Container
                content_container = ui.element('div').classes('w-full h-full')
                self.render_set_visual(content_container, set_info['code'], set_info.get('image'))

                # Overlay Info
                with ui.row().classes('absolute bottom-0 w-full bg-black/80 p-1 justify-between items-center z-20 pointer-events-none'):
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
                    val = e.value if e.value is not None else ""
                    self.state['search_query'] = val
                    await self.apply_set_filters()

                ui.input(placeholder='Search Sets...', on_change=on_search) \
                    .bind_value(self.state, 'search_query').props('debounce=300 icon=search dark clearable').classes('w-64')

                ui.select(['Name', 'Date', 'Card Count', 'Code'], label='Sort', value=self.state['sort_by'],
                        on_change=lambda e: self.update_filter('sort_by', e.value)) \
                        .classes('w-32').props('dark')

                async def toggle_sort():
                    self.state['sort_desc'] = not self.state['sort_desc']
                    await self.apply_set_filters()

                ui.button(icon='arrow_downward', on_click=toggle_sort) \
                    .bind_icon_from(self.state, 'sort_desc', lambda x: 'arrow_downward' if x else 'arrow_upward') \
                    .props('flat round dense color=white')

            self.render_filter_row()

        self.render_gallery_content()

    @ui.refreshable
    def render_filter_row(self):
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
                        val = e.sender.value if e.sender.value else self.state['filter_date_range']
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

                            if v_min is None: v_min = self.state['slider_min_date']
                            if v_max is None: v_max = self.state['slider_max_date']

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
                        val = e.sender.value if e.sender.value else self.state['filter_count_range']
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

    @ui.refreshable
    def render_gallery_content(self):
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
                    if self.state['view'] == 'gallery':
                        self.render_gallery_content.refresh()
                    else:
                        self.render_content.refresh()

            # Debounce prevents refresh while typing (e.g. typing "12")
            ui.number(value=self.state['page'], on_change=on_page_change) \
                .props(f'min=1 max={self.state["total_pages"]} dense dark outlined debounce=800 hide-bottom-space') \
                .classes('w-20')

            ui.label(f"/ {self.state['total_pages']}").classes('text-white')
            ui.button(icon='chevron_right', on_click=lambda: self.change_page(1)).props('flat dense color=white').set_enabled(self.state['page'] < self.state['total_pages'])

    def change_page(self, delta):
        self.state['page'] += delta
        if self.state['view'] == 'gallery':
             self.render_gallery_content.refresh()
        else:
             self.render_content.refresh()

    @ui.refreshable
    def render_set_header(self):
        info = self.state['selected_set_info']
        if not info: return

        # Calculate Stats
        is_cons = self.state['view_scope'] == 'consolidated'
        source = self.state['detail_rows_consolidated'] if is_cons else self.state['detail_rows_collectors']

        total = len(source)
        owned = sum(1 for c in source if c.is_owned)
        pct = (owned / total * 100) if total > 0 else 0

        # Header
        with ui.row().classes('w-full items-start gap-6 mb-6 p-6 bg-gray-900 rounded-lg border border-gray-800'):
            # Image
            with ui.element('div').classes('w-32 h-64 relative bg-black rounded shadow-lg overflow-hidden'):
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

                # Completion Stat
                with ui.row().classes('items-center gap-2 mt-2'):
                    color = 'text-green-400' if pct == 100 else ('text-yellow-400' if pct > 50 else 'text-gray-400')
                    ui.label(f"Completion: {pct:.1f}%").classes(f'text-xl font-bold {color}')
                    ui.label(f"({owned}/{total})").classes('text-sm text-gray-500')

                ui.button('Back to Sets', icon='arrow_back', on_click=self.back_to_gallery).props('flat color=white').classes('mt-4')

            ui.space()

            # Collection Selector
            with ui.column().classes('items-end'):
                 files = persistence.list_collections()
                 file_options = {None: 'None (All Owned)'}
                 for f in files:
                     file_options[f] = (f[:-5] if f.endswith('.json') else f)

                 async def change_col(e):
                     self.state['selected_collection_file'] = e.value
                     await self.load_data() # Reloads collection
                     # Need to reload rows too
                     await self.load_set_details(self.state['selected_set'])
                     self.render_set_header.refresh()

                 ui.select(file_options, label='Collection', value=self.state['selected_collection_file'], on_change=change_col).classes('min-w-[200px]').props('dark')

    def render_detail_view(self):
        if not self.state['selected_set_info']:
            ui.label("Loading...").classes('text-white')
            return

        self.render_set_header()

        # Controls & Grid
        with ui.row().classes('w-full gap-4'):
             with ui.column().classes('w-full'):
                  self.render_detail_controls()
                  self.render_detail_grid()

                  # Bottom Pagination
                  with ui.row().classes('w-full justify-center mt-4'):
                       self.render_detail_pagination_controls()

    async def switch_view_scope(self, scope):
        if self.state['view_scope'] == scope: return
        self.state['view_scope'] = scope
        await self.apply_detail_filters()
        self.render_set_header.refresh()
        # Grid and controls refresh handled by apply_detail_filters

    @ui.refreshable
    def render_detail_pagination_controls(self):
        if self.state['detail_total_pages'] <= 1: return

        async def change_p(delta):
            p = self.state['detail_page'] + delta
            p = max(1, min(p, self.state['detail_total_pages']))
            if p != self.state['detail_page']:
                self.state['detail_page'] = p
                self.render_detail_grid.refresh()
                # Controls are no longer refreshable, so no refresh call here needed for inputs
                # Pagination controls are refreshing themselves via this method being refreshable
                self.render_detail_pagination_controls.refresh()

        with ui.row().classes('items-center gap-2'):
            ui.button(icon='chevron_left', on_click=lambda: change_p(-1)).props('flat dense color=white').set_enabled(self.state['detail_page'] > 1)
            ui.label(f"{self.state['detail_page']} / {self.state['detail_total_pages']}").classes('text-white text-sm font-bold')
            ui.button(icon='chevron_right', on_click=lambda: change_p(1)).props('flat dense color=white').set_enabled(self.state['detail_page'] < self.state['detail_total_pages'])

    @ui.refreshable
    def render_view_scope_toggles(self):
        is_cons = self.state['view_scope'] == 'consolidated'
        with ui.button_group():
            with ui.button('Collectors', on_click=lambda: self.switch_view_scope('collectors')).props(f'flat={is_cons} color=accent'):
                ui.tooltip('View all printings separately')
            with ui.button('Consolidated', on_click=lambda: self.switch_view_scope('consolidated')).props(f'flat={not is_cons} color=accent'):
                ui.tooltip('View unique cards only')

    def render_detail_controls(self):
        with ui.row().classes('w-full items-center gap-4 bg-gray-800 p-2 rounded mb-4'):
            async def on_detail_search(e):
                self.state['detail_search'] = e.value
                await self.apply_detail_filters()

            ui.input(placeholder='Filter cards...', on_change=on_detail_search) \
                .bind_value(self.state, 'detail_search') \
                .props('dark icon=search debounce=300').classes('w-64')

            async def on_detail_sort(e):
                self.state['detail_sort'] = e.value
                await self.apply_detail_filters()

            ui.select(['Name', 'Rarity', 'Price', 'Owned', 'Set Code'], label='Sort', value=self.state['detail_sort'], on_change=on_detail_sort).props('dark').classes('w-40')

            async def toggle_detail_sort():
                self.state['detail_sort_desc'] = not self.state['detail_sort_desc']
                await self.apply_detail_filters()

            ui.button(icon='arrow_downward', on_click=toggle_detail_sort).bind_icon_from(self.state, 'detail_sort_desc', lambda x: 'arrow_downward' if x else 'arrow_upward').props('flat round dense color=white')

            ui.separator().props('vertical')

            # Owned Only Toggle
            async def on_owned_only_change(e):
                 self.state['filter_owned_only'] = e.value
                 await self.apply_detail_filters()

            ui.checkbox('Owned Only', value=self.state.get('filter_owned_only', False), on_change=on_owned_only_change).props('dense dark color=green')

            ui.separator().props('vertical')

            # View Toggle
            self.render_view_scope_toggles()

            ui.separator().props('vertical')

            self.render_detail_pagination_controls()

            ui.space()
            ui.button('Filters', icon='filter_list', on_click=self.filter_dialog.open).props('color=primary')

    async def open_consolidated_view(self, vm: CardViewModel):
         async def on_save(c, set_code, rarity, language, quantity, condition, first_edition, image_id, variant_id, mode):
             if not self.state['current_collection']:
                 ui.notify("Cannot edit 'All Owned' view.", type='warning')
                 return

             col = self.state['current_collection']
             from src.services.collection_editor import CollectionEditor
             CollectionEditor.apply_change(col, c, set_code, rarity, language, quantity, condition, first_edition, image_id, variant_id, mode)
             await run.io_bound(persistence.save_collection, col, self.state['selected_collection_file'])

             await self.load_set_details(self.state['selected_set'])
             self.render_detail_grid.refresh()
             ui.notify('Collection Updated', type='positive')

         owned_breakdown = {}
         if self.state['current_collection']:
             for c in self.state['current_collection'].cards:
                 if c.card_id == vm.api_card.id:
                     for v in c.variants:
                         for e in v.entries:
                             owned_breakdown[e.language] = owned_breakdown.get(e.language, 0) + e.quantity
                     break

         await self.single_card_view.open_consolidated(
             card=vm.api_card,
             total_owned=vm.owned_quantity,
             owned_breakdown=owned_breakdown,
             save_callback=on_save
         )

    @ui.refreshable
    def render_detail_grid(self):
        all_rows = self.state['detail_filtered_rows']

        # Pagination Slice
        start = (self.state['detail_page'] - 1) * self.state['detail_page_size']
        end = min(start + self.state['detail_page_size'], len(all_rows))
        rows = all_rows[start:end]

        if rows:
            to_download = {}
            for r in rows:
                if isinstance(r, CollectorRow):
                    if r.image_id and r.image_url: to_download[r.image_id] = r.image_url
                elif isinstance(r, CardViewModel):
                     c = r.api_card
                     img = c.card_images[0] if c.card_images else None
                     if img: to_download[img.id] = img.image_url_small

            if to_download:
                asyncio.create_task(image_manager.download_batch(to_download, high_res=False))

        is_cons = self.state['view_scope'] == 'consolidated'

        with ui.grid(columns='repeat(auto-fill, minmax(160px, 1fr))').classes('w-full gap-4'):
            for item in rows:
                if is_cons:
                     # Consolidated Rendering
                     card = item.api_card
                     opacity = "opacity-100" if item.is_owned else "opacity-60 grayscale"
                     border = "border-accent" if item.is_owned else "border-gray-700"

                     img_src = card.card_images[0].image_url_small if card.card_images else None
                     img_id = card.card_images[0].id if card.card_images else card.id
                     if image_manager.image_exists(img_id):
                         img_src = f"/images/{img_id}.jpg"

                     with ui.card().classes(f'collection-card w-full p-0 cursor-pointer {opacity} border {border} hover:scale-105 transition-transform') \
                            .on('click', lambda c=item: self.open_consolidated_view(c)):

                         with ui.element('div').classes('relative w-full aspect-[2/3] bg-black'):
                             if img_src: ui.image(img_src).classes('w-full h-full object-cover').props('loading="lazy"')

                             if item.owned_quantity > 0:
                                 ui.label(f"{item.owned_quantity}").classes('absolute top-1 right-1 bg-accent text-dark font-bold px-2 rounded-full text-xs')

                             if card.level:
                                  ui.label(f"Lv {card.level}").classes('absolute bottom-1 right-1 bg-black/70 text-white text-[10px] px-1 rounded')

                         with ui.column().classes('p-2 gap-0 w-full'):
                            ui.label(card.name).classes('text-xs font-bold truncate w-full')
                            ui.label(card.type).classes('text-[10px] text-gray-400 truncate w-full')

                         self._setup_card_tooltip(card)

                else:
                    # Collectors Rendering
                    opacity = "opacity-100" if item.is_owned else "opacity-60 grayscale"
                    border = "border-accent" if item.is_owned else "border-gray-700"

                    img_src = item.image_url
                    if item.image_id and image_manager.image_exists(item.image_id):
                        img_src = f"/images/{item.image_id}.jpg"

                    with ui.card().classes(f'collection-card w-full p-0 cursor-pointer {opacity} border {border} hover:scale-105 transition-transform') \
                            .on('click', lambda c=item: self.open_single_view(c)):

                        with ui.element('div').classes('relative w-full aspect-[2/3] bg-black'):
                            if img_src: ui.image(img_src).classes('w-full h-full object-cover').props('loading="lazy"')

                            if item.is_owned and item.owned_count > 0:
                                 ui.label(f"{item.owned_count}").classes('absolute top-1 right-1 bg-accent text-dark font-bold px-2 rounded-full text-xs')

                            with ui.row().classes('absolute bottom-0 left-0 bg-black/80 text-white text-[10px] px-1 gap-1 items-center rounded-tr'):
                                ui.label(item.rarity).classes('font-bold text-yellow-500 truncate max-w-[100px]')

                            ui.label(item.set_code).classes('absolute bottom-0 right-0 bg-black/80 text-white text-[10px] px-1 font-mono rounded-tl')

                        with ui.column().classes('p-2 gap-0 w-full'):
                            ui.label(item.api_card.name).classes('text-xs font-bold truncate w-full')
                            ui.label(f"${item.price:.2f}").classes('text-xs text-green-400')

                        self._setup_card_tooltip(item.api_card, specific_image_id=item.image_id)

    async def open_single_view(self, row: CollectorRow):
        # Wrapper for SingleCardView
        async def on_save(c, set_code, rarity, language, quantity, condition, first_edition, image_id, variant_id, mode):
             # Save to collection
             if not self.state['current_collection']:
                 ui.notify("Cannot edit 'All Owned' view.", type='warning')
                 return

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
