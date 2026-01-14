from nicegui import ui, run
from src.core.persistence import persistence
from src.core.models import Collection, Card, CardMetadata
from src.services.ygo_api import ygo_service, ApiCard
from src.services.image_manager import image_manager
from dataclasses import dataclass
from typing import List, Optional, Dict
import asyncio
import datetime

@dataclass
class CardViewModel:
    api_card: ApiCard
    owned_quantity: int
    is_owned: bool
    lowest_price: float = 0.0

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

def build_consolidated_vms(api_cards: List[ApiCard], owned_map: dict) -> List[CardViewModel]:
    vms = []
    for card in api_cards:
        qty = owned_map.get(card.name.lower(), 0)

        # Calculate lowest price
        lowest = 0.0
        prices = []
        if card.card_prices:
            # check all markets in first entry
            p = card.card_prices[0]
            for val in [p.cardmarket_price, p.tcgplayer_price, p.ebay_price, p.amazon_price]:
                 if val:
                     try:
                         prices.append(float(val))
                     except:
                         pass
        if prices:
            lowest = min(prices)

        vms.append(CardViewModel(card, qty, qty > 0, lowest))
    return vms

def build_collector_rows(api_cards: List[ApiCard], owned_details: Dict[str, List[Card]], language: str) -> List[CollectorRow]:
    rows = []
    lang_upper = language.upper()

    for card in api_cards:
        owned_list = owned_details.get(card.name.lower(), [])

        # Determine image
        img_url = card.card_images[0].image_url_small if card.card_images else None

        # Expand Sets
        if card.card_sets:
            for cset in card.card_sets:
                # Match owned based on Set Code and Language
                qty = 0
                for c in owned_list:
                    # We match set_code. Note: API set_code matches the DB language usually.
                    # e.g. if loaded German DB, set_code might be LOB-DE001.
                    # Metadata language should also match.
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
            # No set info
            qty = 0
            for c in owned_list:
                if c.metadata.language.upper() == lang_upper: # Loose match if no set code
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

def collection_page():
    # --- State ---
    state = {
        'cards_consolidated': [],
        'cards_collectors': [],
        'filtered_items': [],
        'current_collection': None,
        'selected_file': None,

        # Filters
        'search_text': '',
        'filter_set': '',
        'filter_rarity': '',
        'filter_attr': '',
        'filter_type': '',
        'filter_level': None,
        'filter_quantity': 1,
        'filter_price_max': None,
        'only_owned': False,
        'language': 'en',
        'sort_by': 'Name',

        # View
        'view_scope': 'consolidated',
        'view_mode': 'grid',
        'page': 1,
        'page_size': 48,
        'total_pages': 1,

        'drawer_open': False
    }

    files = persistence.list_collections()
    state['selected_file'] = files[0] if files else None

    # --- Actions ---

    async def load_data():
        """Loads API data and merges with user collection."""
        ui.notify(f'Loading data ({state["language"]})...', type='info')

        try:
            api_cards = await ygo_service.load_card_database(state['language'])
        except Exception as e:
            ui.notify(f"Error loading database: {e}", type='negative')
            return

        collection = None
        if state['selected_file']:
            try:
                collection = await run.io_bound(persistence.load_collection, state['selected_file'])
            except Exception as e:
                ui.notify(f"Error loading collection: {e}", type='warning')

        state['current_collection'] = collection

        # 3. Merge
        owned_map = {} # name -> total qty
        owned_details = {} # name -> list of Card objects

        if collection:
            for c in collection.cards:
                key = c.name.lower()
                owned_map[key] = c.quantity + owned_map.get(key, 0)
                if key not in owned_details: owned_details[key] = []
                owned_details[key].append(c)

        state['cards_consolidated'] = await run.cpu_bound(build_consolidated_vms, api_cards, owned_map)

        if state['view_scope'] == 'collectors':
             state['cards_collectors'] = await run.cpu_bound(build_collector_rows, api_cards, owned_details, state['language'])

        apply_filters()
        ui.notify('Data loaded.', type='positive')

    def apply_filters(e=None): # Accept event arg
        if state['view_scope'] == 'consolidated':
            source = state['cards_consolidated']
        else:
            source = state['cards_collectors']

        if not source:
            state['filtered_items'] = []
            content_area.refresh()
            return

        res = list(source)

        txt = state['search_text'].lower()
        if txt:
            res = [c for c in res if txt in c.api_card.name.lower() or
                   txt in c.api_card.type.lower() or
                   txt in c.api_card.desc.lower()]

        if state['only_owned']:
            min_q = int(state['filter_quantity'] or 1)
            res = [c for c in res if c.is_owned and
                   (getattr(c, 'owned_quantity', 0) if hasattr(c, 'owned_quantity') else getattr(c, 'owned_count', 0)) >= min_q]

        if state['filter_attr']:
            res = [c for c in res if c.api_card.attribute == state['filter_attr']]

        if state['filter_type']:
             res = [c for c in res if state['filter_type'] in c.api_card.type]

        if state['filter_level']:
             res = [c for c in res if c.api_card.level == int(state['filter_level'])]

        if state['filter_price_max']:
             try:
                 p_max = float(state['filter_price_max'])
                 if state['view_scope'] == 'consolidated':
                     res = [c for c in res if c.lowest_price <= p_max]
                 else:
                     res = [c for c in res if c.price <= p_max]
             except:
                 pass

        if state['view_scope'] == 'collectors':
            if state['filter_set']:
                s = state['filter_set'].lower()
                res = [c for c in res if s in c.set_name.lower() or s in c.set_code.lower()]
            if state['filter_rarity']:
                r = state['filter_rarity'].lower()
                res = [c for c in res if r in c.rarity.lower()]

        # Sorting
        key = state['sort_by']
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
            if state['view_scope'] == 'consolidated':
                 res.sort(key=lambda x: x.lowest_price)
            else:
                 res.sort(key=lambda x: x.price)

        state['filtered_items'] = res

        state['page'] = 1
        count = len(res)
        state['total_pages'] = (count + state['page_size'] - 1) // state['page_size']

        content_area.refresh()

    async def switch_scope(scope):
        state['view_scope'] = scope
        if scope == 'collectors' and not state['cards_collectors']:
             await load_data()
        else:
            apply_filters()

    async def switch_language(lang):
        if state['language'] != lang:
            state['language'] = lang
            await load_data()

    async def save_card_change(api_card: ApiCard, set_code, rarity, language, quantity):
        if not state['current_collection']:
            ui.notify('No collection selected.', type='negative')
            return

        col = state['current_collection']

        # Check if exists
        target = None
        for c in col.cards:
            if c.name == api_card.name and c.metadata.set_code == set_code and c.metadata.language == language and c.metadata.rarity == rarity:
                target = c
                break

        if quantity > 0:
            if target:
                target.quantity = quantity
            else:
                # Create new
                new_card = Card(
                    name=api_card.name,
                    quantity=quantity,
                    image_url=api_card.card_images[0].image_url_small if api_card.card_images else None,
                    metadata=CardMetadata(
                        set_code=set_code,
                        rarity=rarity,
                        language=language,
                        market_value=0.0 # Could fetch price
                    )
                )
                col.cards.append(new_card)
        else:
            if target:
                col.cards.remove(target)

        # Save
        try:
            await run.io_bound(persistence.save_collection, state['selected_file'], col)
            ui.notify('Collection saved.', type='positive')
            await load_data()
        except Exception as e:
            ui.notify(f"Error saving: {e}", type='negative')


    # --- Renderers ---

    def render_drawer():
        with ui.column().classes('h-full w-full p-4 gap-4'):
            ui.label('Filters').classes('text-h6')

            ui.input('Set Name/Code', on_change=apply_filters).bind_value(state, 'filter_set') \
                .classes('w-full').props('clearable')

            ui.input('Rarity', on_change=apply_filters).bind_value(state, 'filter_rarity') \
                .classes('w-full').props('clearable')

            ui.number('Max Price', min=0, on_change=apply_filters).bind_value(state, 'filter_price_max').classes('w-full').props('clearable')

            ui.select(['DARK', 'LIGHT', 'EARTH', 'WIND', 'FIRE', 'WATER', 'DIVINE'],
                      label='Attribute', clearable=True, on_change=apply_filters).bind_value(state, 'filter_attr').classes('w-full')

            ui.select(['Normal Monster', 'Effect Monster', 'Spell Card', 'Trap Card', 'Fusion', 'Synchro', 'Xyz', 'Link'],
                      label='Type', clearable=True, on_change=apply_filters).bind_value(state, 'filter_type').classes('w-full')

            ui.number('Level/Rank', min=0, max=13, on_change=apply_filters).bind_value(state, 'filter_level').classes('w-full')

            ui.separator()
            ui.label('Settings').classes('text-h6')

            ui.select(['en', 'de', 'fr', 'it', 'pt'], label='Language', value=state['language'],
                      on_change=lambda e: switch_language(e.value)).classes('w-full')

    def render_consolidated_grid(items: List[CardViewModel]):
        with ui.grid(columns='repeat(auto-fill, minmax(160px, 1fr))').classes('w-full gap-4'):
            for vm in items:
                card = vm.api_card
                opacity = "opacity-100" if vm.is_owned else "opacity-60 grayscale"
                border = "border-accent" if vm.is_owned else "border-gray-700"

                with ui.card().classes(f'collection-card w-full p-0 cursor-pointer {opacity} border {border} hover:scale-105 transition-transform') \
                        .on('click', lambda c=vm: open_details(c.api_card, c.is_owned, c.owned_quantity)):

                    img_src = card.card_images[0].image_url_small if card.card_images else None
                    if image_manager.image_exists(card.id):
                        img_src = f"/images/{card.id}.jpg"

                    with ui.element('div').classes('relative w-full aspect-[2/3] bg-black'):
                        if img_src:
                            ui.image(img_src).classes('w-full h-full object-cover')

                        if vm.owned_quantity > 0:
                            ui.label(f"{vm.owned_quantity}").classes(
                                'absolute top-1 right-1 bg-accent text-dark font-bold px-2 rounded-full text-xs'
                            )

                    with ui.column().classes('p-2 gap-0 w-full'):
                        ui.label(card.name).classes('text-xs font-bold truncate w-full')
                        ui.label(f"${vm.lowest_price:.2f}").classes('text-xs text-green-400')

    def render_collectors_list(items: List[CollectorRow]):
        headers = ['Image', 'Name', 'Set', 'Rarity', 'Lang', 'Price', 'Owned']
        cols = '80px 3fr 2fr 1.5fr 0.5fr 1fr 1fr'

        with ui.column().classes('w-full gap-1'):
            with ui.grid(columns=cols).classes('w-full bg-gray-800 p-2 font-bold rounded'):
                for h in headers: ui.label(h)

            for item in items:
                bg = 'bg-gray-900' if not item.is_owned else 'bg-gray-800 border border-accent'
                with ui.grid(columns=cols).classes(f'w-full {bg} p-1 items-center rounded hover:bg-gray-700 transition cursor-pointer') \
                        .on('click', lambda c=item: open_details(c.api_card, c.is_owned, c.owned_count, initial_set=c.set_code)):
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

    def open_details(card: ApiCard, is_owned: bool = False, quantity: int = 0, initial_set: str = None):
        # Prepare Set Options
        set_opts = [s.set_code for s in card.card_sets] if card.card_sets else ["N/A"]

        # Defaults
        edit_state = {
            'set': initial_set if initial_set and initial_set in set_opts else (set_opts[0] if set_opts else "N/A"),
            'rarity': card.card_sets[0].set_rarity if card.card_sets else "Common",
            'language': state['language'].upper(),
            'quantity': quantity
        }

        # If we selected a specific set in collectors view, try to find its rarity
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
                # Header
                with ui.row().classes('w-full items-center justify-between'):
                    ui.label(card.name).classes('text-h3 font-bold')
                    if is_owned:
                        ui.badge(f"Owned: {quantity}", color='accent').classes('text-lg')

                # Management Section
                with ui.card().classes('w-full bg-gray-800 p-4 q-my-md border border-gray-700'):
                    ui.label('Manage Collection').classes('text-h6 q-mb-sm')
                    with ui.grid(columns=4).classes('w-full gap-4 items-end'):
                        ui.select(set_opts, label='Set').bind_value(edit_state, 'set').classes('w-full')
                        ui.input('Rarity').bind_value(edit_state, 'rarity').classes('w-full') # Editable as API rarities can vary
                        ui.select(['EN', 'DE', 'FR', 'IT', 'PT'], label='Language').bind_value(edit_state, 'language').classes('w-full')
                        ui.number('Quantity', min=0).bind_value(edit_state, 'quantity').classes('w-full')

                    ui.button('Update Collection', on_click=lambda: [save_card_change(card, edit_state['set'], edit_state['rarity'], edit_state['language'], int(edit_state['quantity'])), d.close()]) \
                        .classes('w-full q-mt-md').props('color=secondary')

                ui.separator().classes('q-my-md')

                # Stats
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
                            ui.label(f"{cset.set_code} - {cset.set_rarity}").classes(
                                'bg-grey-9 p-2 rounded text-sm border border-grey-800'
                            )

            d.open()

    # --- Layout ---

    # Drawer (Using Dialog to avoid layout nesting issues)
    filter_dialog = ui.dialog().props('position=right')
    with filter_dialog, ui.card().classes('h-full w-80 bg-gray-900 border-l border-gray-700 p-0'):
        with ui.scroll_area().classes('h-full w-full'):
             render_drawer()

    with ui.row().classes('w-full items-center gap-4 q-mb-md p-4 bg-gray-900 rounded-lg border border-gray-800'):
        ui.label('Gallery').classes('text-h5')

        ui.select(files, value=state['selected_file'], label='Collection',
                  on_change=lambda e: [state.update({'selected_file': e.value}), load_data()]).classes('w-40')

        ui.input(placeholder='Search...', on_change=lambda e: [state.update({'search_text': e.value}), apply_filters()]) \
            .props('debounce=300 icon=search').classes('w-64')

        ui.select(['Name', 'ATK', 'DEF', 'Level', 'Newest', 'Price'], value=state['sort_by'], label='Sort',
                  on_change=lambda e: [state.update({'sort_by': e.value}), apply_filters()]).classes('w-32')

        ui.switch('Owned', on_change=lambda e: [state.update({'only_owned': e.value}), apply_filters()])

        if state['only_owned']:
             ui.number('Min Qty', min=1, on_change=apply_filters).bind_value(state, 'filter_quantity').classes('w-24').props('dense borderless')

        with ui.button_group():
            ui.button('Consolidated', on_click=lambda: switch_scope('consolidated')) \
                .props(f'flat={"collectors" in state["view_scope"]} color=accent')
            ui.button('Collectors', on_click=lambda: switch_scope('collectors')) \
                .props(f'flat={"consolidated" in state["view_scope"]} color=accent')

        ui.space()
        ui.button(icon='filter_list', on_click=filter_dialog.open).props('flat')

    @ui.refreshable
    def content_area():
        start = (state['page'] - 1) * state['page_size']
        end = min(start + state['page_size'], len(state['filtered_items']))
        page_items = state['filtered_items'][start:end]

        with ui.row().classes('w-full items-center justify-between q-mb-sm px-4'):
            ui.label(f"Showing {start+1}-{end} of {len(state['filtered_items'])}").classes('text-grey')

            with ui.row().classes('items-center gap-2'):
                ui.button(icon='chevron_left', on_click=lambda: change_page(-1)).props('flat dense')
                ui.number(value=state['page'], min=1, max=state['total_pages'],
                          on_change=lambda e: set_page(e.value)).classes('w-20').props('dense borderless input-class="text-center"')
                ui.label(f"/ {max(1, state['total_pages'])}")
                ui.button(icon='chevron_right', on_click=lambda: change_page(1)).props('flat dense')

        if not page_items:
            ui.label('No items found.').classes('w-full text-center text-xl text-grey italic q-mt-xl')
            return

        if state['view_scope'] == 'consolidated':
            render_consolidated_grid(page_items)
        else:
            render_collectors_list(page_items)

    def change_page(delta):
        new_page = state['page'] + delta
        if 1 <= new_page <= state['total_pages']:
            state['page'] = new_page
            content_area.refresh()

    def set_page(val):
        if val and 1 <= val <= state['total_pages']:
            state['page'] = int(val)
            content_area.refresh()

    content_area()
    ui.timer(0.1, load_data, once=True)
