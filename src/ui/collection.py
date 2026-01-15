from nicegui import ui, run
from src.core.persistence import persistence
from src.core.models import Collection, Card, CardMetadata
from src.services.ygo_api import ygo_service, ApiCard
from src.services.image_manager import image_manager
from src.core.config import config_manager
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Set
import asyncio

@dataclass
class CardViewModel:
    api_card: ApiCard
    owned_quantity: int
    is_owned: bool
    lowest_price: float = 0.0
    owned_languages: Set[str] = field(default_factory=set)

@dataclass
class CollectorRow:
    api_card: ApiCard
    set_code: str
    set_name: str
    rarity: str
    price: float
    image_url: str
    owned_count: int
    is_owned: bool
    language: str

def build_consolidated_vms(api_cards: List[ApiCard], owned_details: Dict[str, List[Card]]) -> List[CardViewModel]:
    vms = []
    for card in api_cards:
        details = owned_details.get(card.name.lower(), [])
        qty = sum(c.quantity for c in details)
        owned_langs = set(c.metadata.language for c in details)

        # Calculate lowest price
        lowest = 0.0
        prices = []
        if card.card_prices:
            p = card.card_prices[0]
            for val in [p.cardmarket_price, p.tcgplayer_price, p.ebay_price, p.amazon_price]:
                 if val:
                     try:
                         prices.append(float(val))
                     except:
                         pass
        if prices:
            lowest = min(prices)

        vms.append(CardViewModel(card, qty, qty > 0, lowest, owned_langs))
    return vms

def build_collector_rows(api_cards: List[ApiCard], owned_details: Dict[str, List[Card]], language: str) -> List[CollectorRow]:
    rows = []
    lang_upper = language.upper()

    for card in api_cards:
        owned_list = owned_details.get(card.name.lower(), [])

        img_url = card.card_images[0].image_url_small if card.card_images else None

        if card.card_sets:
            for cset in card.card_sets:
                qty = 0
                for c in owned_list:
                    if c.metadata.set_code == cset.set_code and c.metadata.language.upper() == lang_upper:
                        qty += c.quantity

                price = 0.0
                if cset.set_price:
                    try: price = float(cset.set_price)
                    except: pass

                rows.append(CollectorRow(
                    api_card=card,
                    set_code=cset.set_code,
                    set_name=cset.set_name,
                    rarity=cset.set_rarity,
                    price=price,
                    image_url=img_url,
                    owned_count=qty,
                    is_owned=qty > 0,
                    language=lang_upper
                ))
        else:
            qty = 0
            for c in owned_list:
                if c.metadata.language.upper() == lang_upper:
                     qty += c.quantity

            rows.append(CollectorRow(
                api_card=card,
                set_code="N/A",
                set_name="No Set Info",
                rarity="Common",
                price=0.0,
                image_url=img_url,
                owned_count=qty,
                is_owned=qty > 0,
                language=lang_upper
            ))
    return rows

class CollectionPage:
    def __init__(self):
        self.state = {
            'cards_consolidated': [],
            'cards_collectors': [],
            'filtered_items': [],
            'current_collection': None,
            'selected_file': None,
            'available_sets': [],

            # Filters
            'search_text': '',
            'filter_set': '',
            'filter_rarity': '',
            'filter_attr': '',
            'filter_type': '',
            'filter_level': None,
            'filter_quantity': 1,
            'filter_price_max': None,
            'filter_owned_lang': '',
            'only_owned': False,
            'language': config_manager.get_language(),
            'sort_by': 'Name',

            # View
            'view_scope': 'consolidated', # consolidated, collectors
            'view_mode': 'grid',          # grid, list
            'page': 1,
            'page_size': 48,
            'total_pages': 1,
        }

        files = persistence.list_collections()
        self.state['selected_file'] = files[0] if files else None

    async def load_data(self):
        # ui.notify(f'Loading data ({self.state["language"]})...', type='info')
        print("Loading data...")

        try:
            api_cards = await ygo_service.load_card_database(self.state['language'])
        except Exception as e:
            ui.notify(f"Error loading database: {e}", type='negative')
            return

        # Extract Sets
        sets = set()
        for c in api_cards:
            if c.card_sets:
                for s in c.card_sets:
                    sets.add(f"{s.set_name} | {s.set_code}")
        self.state['available_sets'] = sorted(list(sets))

        # Load Collection
        collection = None
        if self.state['selected_file']:
            try:
                collection = await run.io_bound(persistence.load_collection, self.state['selected_file'])
            except Exception as e:
                ui.notify(f"Error loading collection: {e}", type='warning')

        self.state['current_collection'] = collection

        # Build Maps
        owned_details = {}
        if collection:
            for c in collection.cards:
                key = c.name.lower()
                if key not in owned_details: owned_details[key] = []
                owned_details[key].append(c)

        self.state['cards_consolidated'] = await run.io_bound(build_consolidated_vms, api_cards, owned_details)

        # Lazy load collectors view if needed, or just clear it so it rebuilds on switch
        self.state['cards_collectors'] = []
        if self.state['view_scope'] == 'collectors':
             self.state['cards_collectors'] = await run.io_bound(build_collector_rows, api_cards, owned_details, self.state['language'])

        self.apply_filters()

        # Update set selector options if rendered
        if hasattr(self, 'set_selector'):
            self.set_selector.options = self.state['available_sets']
            self.set_selector.update()

        # ui.notify('Data loaded.', type='positive')
        print(f"Data loaded. Items: {len(self.state['cards_consolidated'])}")

    def apply_filters(self, e=None):
        if self.state['view_scope'] == 'consolidated':
            source = self.state['cards_consolidated']
        else:
            source = self.state['cards_collectors']

        if not source:
            self.state['filtered_items'] = []
            self.content_area.refresh()
            return

        res = list(source)

        # Search Text
        txt = self.state['search_text'].lower()
        if txt:
            res = [c for c in res if txt in c.api_card.name.lower() or
                   txt in c.api_card.type.lower() or
                   txt in c.api_card.desc.lower()]

        # Owned Filter
        if self.state['only_owned']:
            min_q = int(self.state['filter_quantity'] or 1)
            res = [c for c in res if c.is_owned and
                   (getattr(c, 'owned_quantity', 0) if hasattr(c, 'owned_quantity') else getattr(c, 'owned_count', 0)) >= min_q]

        # Owned Language Filter
        if self.state['filter_owned_lang']:
            target_lang = self.state['filter_owned_lang']
            if self.state['view_scope'] == 'consolidated':
                res = [c for c in res if target_lang in c.owned_languages]
            # For collectors view, it's tricky as rows are strictly current DB language.
            # We skip it or implement complex logic. For now, skip or maybe strict match if the row language matches?
            # User requirement implies filtering what I own.
            # If I filter 'DE', and I am in 'EN' view, Collectors view (EN rows) won't have 'DE' cards.
            # So this filter effectively returns 0 in Collectors view unless we are in DE view.

        # Common Filters
        if self.state['filter_attr']:
            res = [c for c in res if c.api_card.attribute == self.state['filter_attr']]
        if self.state['filter_type']:
             res = [c for c in res if self.state['filter_type'] in c.api_card.type]
        if self.state['filter_level']:
             res = [c for c in res if c.api_card.level == int(self.state['filter_level'])]
        if self.state['filter_price_max']:
             try:
                 p_max = float(self.state['filter_price_max'])
                 if self.state['view_scope'] == 'consolidated':
                     res = [c for c in res if c.lowest_price <= p_max]
                 else:
                     res = [c for c in res if c.price <= p_max]
             except: pass

        # Set Filter (Enhanced)
        if self.state['filter_set']:
            s_val = self.state['filter_set']
            # Format is "Name | Code" or just Code
            # We match strictly or loosely?
            # Extract code if possible
            code_search = s_val.split('|')[-1].strip().lower()
            name_search = s_val.split('|')[0].strip().lower()

            if self.state['view_scope'] == 'consolidated':
                # Check api_card.card_sets
                def match_set(c):
                    if not c.api_card.card_sets: return False
                    for cs in c.api_card.card_sets:
                        if code_search in cs.set_code.lower() or name_search in cs.set_name.lower():
                            return True
                    return False
                res = [c for c in res if match_set(c)]
            else:
                res = [c for c in res if code_search in c.set_code.lower() or name_search in c.set_name.lower()]

        # Rarity Filter
        if self.state['filter_rarity']:
            r = self.state['filter_rarity'].lower()
            if self.state['view_scope'] == 'consolidated':
                 # Check sets
                 res = [c for c in res if c.api_card.card_sets and any(r in cs.set_rarity.lower() for cs in c.api_card.card_sets)]
            else:
                 res = [c for c in res if r in c.rarity.lower()]

        # Sorting
        key = self.state['sort_by']
        if key == 'Name':
            res.sort(key=lambda x: x.api_card.name)
        elif key == 'ATK':
            res.sort(key=lambda x: (x.api_card.atk or -1), reverse=True)
        elif key == 'DEF':
            res.sort(key=lambda x: (getattr(x.api_card, 'def_', None) or -1), reverse=True)
        elif key == 'Level':
            res.sort(key=lambda x: (x.api_card.level or -1), reverse=True)
        elif key == 'Newest':
            res.sort(key=lambda x: x.api_card.id, reverse=True)
        elif key == 'Price':
            if self.state['view_scope'] == 'consolidated':
                 res.sort(key=lambda x: x.lowest_price)
            else:
                 res.sort(key=lambda x: x.price)

        self.state['filtered_items'] = res
        self.state['page'] = 1
        self.update_pagination()
        self.content_area.refresh()

    def update_pagination(self):
        count = len(self.state['filtered_items'])
        self.state['total_pages'] = (count + self.state['page_size'] - 1) // self.state['page_size']

    async def switch_scope(self, scope):
        self.state['view_scope'] = scope
        if scope == 'collectors' and not self.state['cards_collectors']:
             # Need to fetch api_cards again? No, we need stored api_cards.
             # Ideally we cache api_cards in state, but it might be heavy?
             # Current implementation calls load_data() which re-fetches.
             # Optimization: Store api_cards in self.state?
             # For now, just call load_data() as in original.
             await self.load_data()
        else:
            self.apply_filters()

    async def save_card_change(self, api_card: ApiCard, set_code, rarity, language, quantity):
        if not self.state['current_collection']:
            ui.notify('No collection selected.', type='negative')
            return

        col = self.state['current_collection']
        target = None
        for c in col.cards:
            if c.name == api_card.name and c.metadata.set_code == set_code and c.metadata.language == language and c.metadata.rarity == rarity:
                target = c
                break

        if quantity > 0:
            if target:
                target.quantity = quantity
            else:
                new_card = Card(
                    name=api_card.name,
                    quantity=quantity,
                    image_url=api_card.card_images[0].image_url_small if api_card.card_images else None,
                    metadata=CardMetadata(
                        set_code=set_code,
                        rarity=rarity,
                        language=language,
                        market_value=0.0
                    )
                )
                col.cards.append(new_card)
        else:
            if target:
                col.cards.remove(target)

        try:
            await run.io_bound(persistence.save_collection, self.state['selected_file'], col)
            ui.notify('Collection saved.', type='positive')
            await self.load_data()
        except Exception as e:
            ui.notify(f"Error saving: {e}", type='negative')

    def open_details(self, card: ApiCard, is_owned: bool = False, quantity: int = 0, initial_set: str = None):
        set_opts = [s.set_code for s in card.card_sets] if card.card_sets else ["N/A"]

        edit_state = {
            'set': initial_set if initial_set and initial_set in set_opts else (set_opts[0] if set_opts else "N/A"),
            'rarity': card.card_sets[0].set_rarity if card.card_sets else "Common",
            'language': self.state['language'].upper(), # Default to current view lang
            'quantity': quantity
        }

        # If editing existing owned card logic?
        # The logic in original was: initial_set passed from clicked row.
        # But if we open from Consolidated, we don't know which one.
        # If we open from Collectors, we know.

        if initial_set and card.card_sets:
            for s in card.card_sets:
                if s.set_code == initial_set:
                    edit_state['rarity'] = s.set_rarity
                    break

        with ui.dialog().props('maximized') as d, ui.card().classes('w-full h-full p-0 flex flex-row overflow-hidden'):
            ui.button(icon='close', on_click=d.close).props('flat round color=white').classes('absolute top-2 right-2 z-50')

            with ui.column().classes('w-1/3 h-full bg-black items-center justify-center p-8'):
                img_url = card.card_images[0].image_url if card.card_images else None
                if image_manager.image_exists(card.id):
                    img_url = f"/images/{card.id}.jpg"
                if img_url:
                    ui.image(img_url).classes('max-h-full max-w-full object-contain shadow-2xl')

            with ui.column().classes('w-2/3 h-full p-8 scroll'):
                with ui.row().classes('w-full items-center justify-between'):
                    ui.label(card.name).classes('text-h3 font-bold')
                    if is_owned:
                        ui.badge(f"Owned: {quantity}", color='accent').classes('text-lg')

                with ui.card().classes('w-full bg-gray-800 p-4 q-my-md border border-gray-700'):
                    ui.label('Manage Collection').classes('text-h6 q-mb-sm')
                    with ui.grid(columns=4).classes('w-full gap-4 items-end'):
                        ui.select(set_opts, label='Set').bind_value(edit_state, 'set').classes('w-full')
                        ui.input('Rarity').bind_value(edit_state, 'rarity').classes('w-full')
                        ui.select(['EN', 'DE', 'FR', 'IT', 'PT'], label='Language').bind_value(edit_state, 'language').classes('w-full')
                        ui.number('Quantity', min=0).bind_value(edit_state, 'quantity').classes('w-full')

                    ui.button('Update Collection', on_click=lambda: [self.save_card_change(card, edit_state['set'], edit_state['rarity'], edit_state['language'], int(edit_state['quantity'])), d.close()]) \
                        .classes('w-full q-mt-md').props('color=secondary')

                ui.separator().classes('q-my-md')
                with ui.grid(columns=4).classes('w-full gap-4 text-lg'):
                    def stat(label, value):
                        with ui.column():
                            ui.label(label).classes('text-grey text-sm uppercase')
                            ui.label(str(value)).classes('font-bold')
                    stat('Type', card.type)
                    stat('Race', card.race)
                    stat('Attribute', card.attribute)
                    stat('Level', card.level)
                    stat('ATK', card.atk)
                    stat('DEF', getattr(card, 'def_', '-'))

                ui.separator().classes('q-my-md')
                ui.label('Description').classes('text-h6 q-mb-sm')
                ui.markdown(card.desc).classes('text-grey-3 leading-relaxed')

                ui.separator().classes('q-my-md')
                ui.label('Set List').classes('text-h6 q-mb-sm')
                if card.card_sets:
                    with ui.grid(columns=3).classes('w-full gap-2'):
                        for cset in card.card_sets:
                            ui.label(f"{cset.set_code} - {cset.set_rarity}").classes('bg-grey-9 p-2 rounded text-sm border border-grey-800')
            d.open()

    # --- Renderers ---

    def render_consolidated_grid(self, items: List[CardViewModel]):
        with ui.grid(columns='repeat(auto-fill, minmax(160px, 1fr))').classes('w-full gap-4'):
            for vm in items:
                card = vm.api_card
                opacity = "opacity-100" if vm.is_owned else "opacity-60 grayscale"
                border = "border-accent" if vm.is_owned else "border-gray-700"

                with ui.card().classes(f'collection-card w-full p-0 cursor-pointer {opacity} border {border} hover:scale-105 transition-transform') \
                        .on('click', lambda c=vm: self.open_details(c.api_card, c.is_owned, c.owned_quantity)):

                    img_src = card.card_images[0].image_url_small if card.card_images else None
                    if image_manager.image_exists(card.id):
                        img_src = f"/images/{card.id}.jpg"

                    with ui.element('div').classes('relative w-full aspect-[2/3] bg-black'):
                        if img_src: ui.image(img_src).classes('w-full h-full object-cover')
                        if vm.owned_quantity > 0:
                            ui.label(f"{vm.owned_quantity}").classes('absolute top-1 right-1 bg-accent text-dark font-bold px-2 rounded-full text-xs')

                    with ui.column().classes('p-2 gap-0 w-full'):
                        ui.label(card.name).classes('text-xs font-bold truncate w-full')
                        ui.label(f"${vm.lowest_price:.2f}").classes('text-xs text-green-400')

    def render_consolidated_list(self, items: List[CardViewModel]):
         headers = ['Image', 'Name', 'Type', 'Price', 'Owned']
         cols = '60px 4fr 2fr 1fr 1fr'
         with ui.column().classes('w-full gap-1'):
            with ui.grid(columns=cols).classes('w-full bg-gray-800 p-2 font-bold rounded'):
                for h in headers: ui.label(h)

            for vm in items:
                card = vm.api_card
                bg = 'bg-gray-900' if not vm.is_owned else 'bg-gray-800 border border-accent'
                img_src = card.card_images[0].image_url_small if card.card_images else None

                with ui.grid(columns=cols).classes(f'w-full {bg} p-1 items-center rounded hover:bg-gray-700 transition cursor-pointer') \
                        .on('click', lambda c=vm: self.open_details(c.api_card, c.is_owned, c.owned_quantity)):
                    ui.image(img_src).classes('h-10 w-8 object-cover')
                    ui.label(card.name).classes('truncate text-sm font-bold')
                    ui.label(card.type).classes('text-xs text-gray-400')
                    ui.label(f"${vm.lowest_price:.2f}").classes('text-sm text-green-400')
                    if vm.is_owned:
                         ui.badge(str(vm.owned_quantity), color='accent').classes('text-dark')
                    else:
                         ui.label('-').classes('text-gray-600')

    def render_collectors_list(self, items: List[CollectorRow]):
        headers = ['Image', 'Name', 'Set', 'Rarity', 'Lang', 'Price', 'Owned']
        cols = '80px 3fr 2fr 1.5fr 0.5fr 1fr 1fr'

        with ui.column().classes('w-full gap-1'):
            with ui.grid(columns=cols).classes('w-full bg-gray-800 p-2 font-bold rounded'):
                for h in headers: ui.label(h)

            for item in items:
                bg = 'bg-gray-900' if not item.is_owned else 'bg-gray-800 border border-accent'
                with ui.grid(columns=cols).classes(f'w-full {bg} p-1 items-center rounded hover:bg-gray-700 transition cursor-pointer') \
                        .on('click', lambda c=item: self.open_details(c.api_card, c.is_owned, c.owned_count, initial_set=c.set_code)):
                    ui.image(item.image_url).classes('h-12 w-8 object-cover')
                    ui.label(item.api_card.name).classes('truncate text-sm font-bold')
                    with ui.column().classes('gap-0'):
                        ui.label(item.set_code).classes('text-xs font-mono font-bold text-yellow-500')
                        ui.label(item.set_name).classes('text-xs text-gray-400 truncate')
                    ui.label(item.rarity).classes('text-xs')
                    ui.label(item.language).classes('text-xs uppercase text-gray-400')
                    ui.label(f"${item.price:.2f}").classes('text-sm text-green-400')
                    if item.is_owned:
                         ui.badge(str(item.owned_count), color='accent').classes('text-dark')
                    else:
                         ui.label('-').classes('text-gray-600')

    def render_collectors_grid(self, items: List[CollectorRow]):
        with ui.grid(columns='repeat(auto-fill, minmax(160px, 1fr))').classes('w-full gap-4'):
            for item in items:
                opacity = "opacity-100" if item.is_owned else "opacity-60 grayscale"
                border = "border-accent" if item.is_owned else "border-gray-700"

                with ui.card().classes(f'collection-card w-full p-0 cursor-pointer {opacity} border {border} hover:scale-105 transition-transform') \
                        .on('click', lambda c=item: self.open_details(c.api_card, c.is_owned, c.owned_count, initial_set=c.set_code)):

                    with ui.element('div').classes('relative w-full aspect-[2/3] bg-black'):
                        if item.image_url: ui.image(item.image_url).classes('w-full h-full object-cover')
                        if item.is_owned:
                             ui.label(f"{item.owned_count}").classes('absolute top-1 right-1 bg-accent text-dark font-bold px-2 rounded-full text-xs')

                        ui.label(item.set_code).classes('absolute bottom-0 right-0 bg-black/80 text-white text-[10px] px-1 font-mono')

                    with ui.column().classes('p-2 gap-0 w-full'):
                        ui.label(item.api_card.name).classes('text-xs font-bold truncate w-full')
                        ui.label(f"{item.rarity}").classes('text-[10px] text-gray-400')
                        ui.label(f"${item.price:.2f}").classes('text-xs text-green-400')

    @ui.refreshable
    def content_area(self):
        start = (self.state['page'] - 1) * self.state['page_size']
        end = min(start + self.state['page_size'], len(self.state['filtered_items']))
        page_items = self.state['filtered_items'][start:end]

        with ui.row().classes('w-full items-center justify-between q-mb-sm px-4'):
            ui.label(f"Showing {start+1}-{end} of {len(self.state['filtered_items'])}").classes('text-grey')

            with ui.row().classes('items-center gap-2'):
                ui.button(icon='chevron_left', on_click=lambda: self.change_page(-1)).props('flat dense')
                ui.number(value=self.state['page'], min=1, max=self.state['total_pages'],
                          on_change=lambda e: self.set_page(e.value)).classes('w-20').props('dense borderless input-class="text-center"')
                ui.label(f"/ {max(1, self.state['total_pages'])}")
                ui.button(icon='chevron_right', on_click=lambda: self.change_page(1)).props('flat dense')

        if not page_items:
            ui.label('No items found.').classes('w-full text-center text-xl text-grey italic q-mt-xl')
            return

        if self.state['view_scope'] == 'consolidated':
            if self.state['view_mode'] == 'grid':
                self.render_consolidated_grid(page_items)
            else:
                self.render_consolidated_list(page_items)
        else:
            if self.state['view_mode'] == 'grid':
                self.render_collectors_grid(page_items)
            else:
                self.render_collectors_list(page_items)

    def change_page(self, delta):
        new_page = self.state['page'] + delta
        if 1 <= new_page <= self.state['total_pages']:
            self.state['page'] = new_page
            self.content_area.refresh()

    def set_page(self, val):
        if val and 1 <= val <= self.state['total_pages']:
            self.state['page'] = int(val)
            self.content_area.refresh()

    def build_ui(self):
        # Drawer (Filter)
        # We use a dialog for the filter drawer to avoid nesting in main layout
        filter_dialog = ui.dialog().props('position=right')
        with filter_dialog, ui.card().classes('h-full w-80 bg-gray-900 border-l border-gray-700 p-0'):
             with ui.scroll_area().classes('h-full w-full'):
                 with ui.column().classes('h-full w-full p-4 gap-4'):
                    ui.label('Filters').classes('text-h6')

                    # Set Filter (Dropdown)
                    self.set_selector = ui.select(self.state['available_sets'], label='Set', with_input=True, clearable=True,
                              on_change=self.apply_filters).bind_value(self.state, 'filter_set').classes('w-full').props('use-input fill-input input-debounce=0')

                    ui.input('Rarity', on_change=self.apply_filters).bind_value(self.state, 'filter_rarity').classes('w-full').props('clearable')
                    ui.number('Max Price', min=0, on_change=self.apply_filters).bind_value(self.state, 'filter_price_max').classes('w-full').props('clearable')

                    ui.select(['DARK', 'LIGHT', 'EARTH', 'WIND', 'FIRE', 'WATER', 'DIVINE'],
                              label='Attribute', clearable=True, on_change=self.apply_filters).bind_value(self.state, 'filter_attr').classes('w-full')

                    ui.select(['Normal Monster', 'Effect Monster', 'Spell Card', 'Trap Card', 'Fusion', 'Synchro', 'Xyz', 'Link'],
                              label='Type', clearable=True, on_change=self.apply_filters).bind_value(self.state, 'filter_type').classes('w-full')

                    ui.number('Level/Rank', min=0, max=13, on_change=self.apply_filters).bind_value(self.state, 'filter_level').classes('w-full')

                    ui.separator()
                    ui.label('Ownership').classes('text-h6')

                    ui.select(['EN', 'DE', 'FR', 'IT', 'PT'], label='Owned Language', clearable=True,
                              on_change=self.apply_filters).bind_value(self.state, 'filter_owned_lang').classes('w-full')

        # Toolbar
        with ui.row().classes('w-full items-center gap-4 q-mb-md p-4 bg-gray-900 rounded-lg border border-gray-800'):
            ui.label('Gallery').classes('text-h5')

            files = persistence.list_collections()
            ui.select(files, value=self.state['selected_file'], label='Collection',
                      on_change=lambda e: [self.state.update({'selected_file': e.value}), self.load_data()]).classes('w-40')

            ui.input(placeholder='Search...', on_change=lambda e: [self.state.update({'search_text': e.value}), self.apply_filters()]) \
                .props('debounce=300 icon=search').classes('w-64')

            ui.select(['Name', 'ATK', 'DEF', 'Level', 'Newest', 'Price'], value=self.state['sort_by'], label='Sort',
                      on_change=lambda e: [self.state.update({'sort_by': e.value}), self.apply_filters()]).classes('w-32')

            with ui.row().classes('items-center'):
                ui.switch('Owned', on_change=lambda e: [self.state.update({'only_owned': e.value}), self.apply_filters()])
                if self.state['only_owned']:
                     ui.number(min=1, on_change=self.apply_filters).bind_value(self.state, 'filter_quantity').classes('w-16').props('dense borderless')

            ui.separator().props('vertical')

            with ui.button_group():
                ui.button('Consolidated', on_click=lambda: self.switch_scope('consolidated')) \
                    .props(f'flat={"collectors" in self.state["view_scope"]} color=accent')
                ui.button('Collectors', on_click=lambda: self.switch_scope('collectors')) \
                    .props(f'flat={"consolidated" in self.state["view_scope"]} color=accent')

            with ui.button_group():
                ui.button(icon='grid_view', on_click=lambda: [self.state.update({'view_mode': 'grid'}), self.content_area.refresh()]) \
                    .props(f'flat={"list" == self.state["view_mode"]} color=accent')
                ui.button(icon='list', on_click=lambda: [self.state.update({'view_mode': 'list'}), self.content_area.refresh()]) \
                    .props(f'flat={"grid" == self.state["view_mode"]} color=accent')

            ui.space()
            # Prominent Filter Button
            ui.button(icon='filter_list', on_click=filter_dialog.open).props('color=primary size=lg')

        self.content_area()
        ui.timer(0.1, self.load_data, once=True)

def collection_page():
    page = CollectionPage()
    page.build_ui()
