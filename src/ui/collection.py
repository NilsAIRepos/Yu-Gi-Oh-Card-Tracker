from nicegui import ui, run
from src.core.persistence import persistence
from src.core.models import Collection
from src.services.ygo_api import ygo_service, ApiCard
from src.services.image_manager import image_manager
from dataclasses import dataclass
from typing import List, Optional
import asyncio

@dataclass
class CardViewModel:
    api_card: ApiCard
    owned_quantity: int
    is_owned: bool

def build_view_models_helper(api_cards: List[ApiCard], owned_map: dict) -> List[CardViewModel]:
    vms = []
    for card in api_cards:
        qty = owned_map.get(card.name.lower(), 0)
        vms.append(CardViewModel(card, qty, qty > 0))
    return vms

def collection_page():
    # --- State ---
    state = {
        'cards': [], # List[CardViewModel]
        'filtered_cards': [], # List[CardViewModel]
        'current_collection': None, # Optional[Collection]
        'selected_file': None,
        'search_text': '',
        'sort_by': 'Name',
        'only_owned': False,
        'view_mode': 'grid', # 'grid' | 'list'
        'page': 1,
        'page_size': 48,
        'total_pages': 1
    }

    files = persistence.list_collections()
    state['selected_file'] = files[0] if files else None

    # --- Actions ---

    async def load_data():
        """Loads API data and merges with user collection."""
        ui.notify('Loading card database...', type='info')

        # 1. Load API Cards
        try:
            api_cards = await ygo_service.load_card_database()
        except Exception as e:
            ui.notify(f"Error loading database: {e}", type='negative')
            return

        # 2. Load User Collection
        collection = None
        if state['selected_file']:
            try:
                collection = await run.io_bound(persistence.load_collection, state['selected_file'])
            except Exception as e:
                ui.notify(f"Error loading collection: {e}", type='warning')

        state['current_collection'] = collection

        # 3. Merge
        owned_map = {}
        if collection:
            for c in collection.cards:
                name_key = c.name.lower()
                owned_map[name_key] = c.quantity + owned_map.get(name_key, 0)

        # Create View Models
        state['cards'] = await run.cpu_bound(build_view_models_helper, api_cards, owned_map)

        apply_filters()
        ui.notify('Data loaded.', type='positive')

    def apply_filters():
        """Filters the master list based on search and ownership."""
        res = list(state['cards']) # Copy to sort safely

        # Text Search
        txt = state['search_text'].lower()
        if txt:
            res = [c for c in res if txt in c.api_card.name.lower() or
                   (c.api_card.type and txt in c.api_card.type.lower())]

        # Owned Filter
        if state['only_owned']:
            res = [c for c in res if c.is_owned]

        # Sort
        key = state['sort_by']
        if key == 'Name':
            res.sort(key=lambda x: x.api_card.name)
        elif key == 'ATK':
            res.sort(key=lambda x: (x.api_card.atk or -1), reverse=True)
        elif key == 'DEF':
            res.sort(key=lambda x: (x.api_card.def_ or -1), reverse=True)
        elif key == 'Level':
            res.sort(key=lambda x: (x.api_card.level or -1), reverse=True)
        elif key == 'Newest':
            res.sort(key=lambda x: x.api_card.id, reverse=True)

        state['filtered_cards'] = res

        # Reset Pagination
        state['page'] = 1
        count = len(res)
        state['total_pages'] = (count + state['page_size'] - 1) // state['page_size']

        content_area.refresh()

    async def switch_collection(filename):
        state['selected_file'] = filename
        await load_data()

    async def refresh_database():
        ui.notify('Updating database from API...', type='ongoing')
        try:
            count = await ygo_service.fetch_card_database()
            ui.notify(f'Database updated. {count} cards.', type='positive')
            await load_data()
        except Exception as e:
            ui.notify(f'Update failed: {e}', type='negative')

    def toggle_view_mode(mode):
        state['view_mode'] = mode
        content_area.refresh()

    async def preload_images():
        dialog = ui.dialog()
        with dialog, ui.card():
            ui.label('Downloading Images...')
            progress = ui.linear_progress(value=0).classes('w-64')
            status = ui.label('Starting...')

        dialog.open()

        def update_progress(p):
            progress.value = p
            status.text = f"{int(p*100)}%"

        await ygo_service.download_all_images(progress_callback=update_progress)
        dialog.close()
        ui.notify('Download complete.')
        content_area.refresh() # Refresh to show local images

    # --- Renderers ---

    def render_card_grid_item(vm: CardViewModel):
        card = vm.api_card
        # Styles
        opacity = "opacity-100" if vm.is_owned else "opacity-40 grayscale"
        border = "border-accent" if vm.is_owned else "border-gray-700"

        with ui.card().classes(f'collection-card w-full p-0 cursor-pointer {opacity} border {border} hover:scale-105 transition-transform duration-200') \
                .on('click', lambda: open_details(vm)):

            ui.tooltip(f"{card.name}\n{card.type}\nOwned: {vm.owned_quantity}")

            # Image Area
            with ui.element('div').classes('relative w-full aspect-[2/3] bg-black'):
                # Default remote
                img_src = card.card_images[0].image_url_small if card.card_images else None

                # Check local existence
                if image_manager.image_exists(card.id):
                    img_src = f"/images/{card.id}.jpg"

                if img_src:
                    ui.image(img_src).classes('w-full h-full object-cover')
                else:
                    ui.label('No Image').classes('text-center text-grey self-center')

                # Badge for Qty
                if vm.owned_quantity > 0:
                    ui.label(f"x{vm.owned_quantity}").classes(
                        'absolute top-1 right-1 bg-accent text-dark font-bold px-2 py-0.5 rounded text-xs shadow-md'
                    )

            # Name Label
            ui.label(card.name).classes('p-2 text-sm font-bold leading-tight text-center truncate w-full')

    def open_details(vm: CardViewModel):
        card = vm.api_card
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
                    if vm.is_owned:
                        ui.badge(f"Owned: {vm.owned_quantity}", color='accent').classes('text-lg')

                ui.separator().classes('q-my-md')

                with ui.grid(columns=4).classes('w-full gap-4 text-lg'):
                    def stat(label, value):
                        with ui.column():
                            ui.label(label).classes('text-grey text-sm uppercase')
                            ui.label(str(value)).classes('font-bold')
                    stat('Type', card.type)
                    stat('Race', card.race)
                    stat('Attribute', card.attribute)
                    stat('Level/Rank', card.level)
                    stat('ATK', card.atk)
                    stat('DEF', getattr(card, 'def_', '-'))
                    stat('Archetype', card.archetype or '-')

                ui.separator().classes('q-my-md')
                ui.label('Description').classes('text-h6 q-mb-sm')
                ui.markdown(card.desc).classes('text-grey-3 leading-relaxed')

                ui.separator().classes('q-my-md')
                ui.label('Sets & Rarity').classes('text-h6 q-mb-sm')
                if card.card_sets:
                    with ui.grid(columns=3).classes('w-full gap-2'):
                        for cset in card.card_sets:
                            ui.label(f"{cset.set_code} - {cset.set_rarity}").classes(
                                'bg-grey-9 p-2 rounded text-sm border border-grey-800'
                            )
                else:
                    ui.label('No set information available.').classes('text-italic text-grey')

            d.open()

    # --- Main Layout ---

    ui.label('Collection Manager').classes('text-h4 q-mb-md')

    # Top Controls
    with ui.row().classes('w-full items-center gap-4 q-mb-lg p-4 bg-gray-900 rounded-lg border border-gray-800'):
        ui.select(files, value=state['selected_file'], label='Active Collection',
                  on_change=lambda e: switch_collection(e.value)).classes('w-48')

        ui.input(placeholder='Search cards...', on_change=lambda e: [state.update({'search_text': e.value}), apply_filters()]) \
            .props('debounce=300 icon=search').classes('w-64')

        ui.select(['Name', 'ATK', 'DEF', 'Level', 'Newest'], value=state['sort_by'], label='Sort',
                  on_change=lambda e: [state.update({'sort_by': e.value}), apply_filters()]) \
            .classes('w-32')

        ui.switch('Owned Only', on_change=lambda e: [state.update({'only_owned': e.value}), apply_filters()])

        ui.space()

        with ui.button_group():
            ui.button(icon='grid_view', on_click=lambda: toggle_view_mode('grid')).props('flat')
            ui.button(icon='list', on_click=lambda: toggle_view_mode('list')).props('flat')

        with ui.dropdown_button('Database Tools', icon='build', color='secondary'):
            ui.menu_item('Refresh Database', on_click=refresh_database)
            ui.menu_item('Preload All Images', on_click=preload_images)

    # Content Area
    @ui.refreshable
    def content_area():
        start = (state['page'] - 1) * state['page_size']
        end = min(start + state['page_size'], len(state['filtered_cards']))
        page_items = state['filtered_cards'][start:end]

        with ui.row().classes('w-full items-center justify-between q-mb-sm'):
            ui.label(f"Showing {start+1}-{end} of {len(state['filtered_cards'])}").classes('text-grey')

            if state['view_mode'] == 'grid':
                with ui.row().classes('items-center gap-2'):
                    def change_page(delta):
                        state['page'] += delta
                        content_area.refresh()

                    b_prev = ui.button(icon='chevron_left', on_click=lambda: change_page(-1)).props('flat dense')
                    if state['page'] <= 1: b_prev.disable()

                    ui.label(f"Page {state['page']} / {max(1, state['total_pages'])}")

                    b_next = ui.button(icon='chevron_right', on_click=lambda: change_page(1)).props('flat dense')
                    if state['page'] >= state['total_pages']: b_next.disable()

        if state['view_mode'] == 'grid':
            if not page_items:
                ui.label('No cards found.').classes('w-full text-center text-xl text-grey italic q-mt-xl')
            else:
                with ui.grid(columns='repeat(auto-fill, minmax(180px, 1fr))').classes('w-full gap-4'):
                    for vm in page_items:
                        render_card_grid_item(vm)

        elif state['view_mode'] == 'list':
            rows = [
                {
                    'name': vm.api_card.name,
                    'type': vm.api_card.type,
                    'race': vm.api_card.race,
                    'atk': vm.api_card.atk,
                    'def': getattr(vm.api_card, 'def_', None),
                    'level': vm.api_card.level,
                    'owned': vm.owned_quantity
                }
                for vm in state['filtered_cards']
            ]

            ui.aggrid({
                'columnDefs': [
                    {'headerName': 'Name', 'field': 'name', 'sortable': True, 'filter': True, 'flex': 2},
                    {'headerName': 'Type', 'field': 'type', 'sortable': True, 'filter': True, 'flex': 1},
                    {'headerName': 'Race', 'field': 'race', 'sortable': True, 'filter': True, 'flex': 1},
                    {'headerName': 'ATK', 'field': 'atk', 'sortable': True, 'width': 80},
                    {'headerName': 'DEF', 'field': 'def', 'sortable': True, 'width': 80},
                    {'headerName': 'Owned', 'field': 'owned', 'sortable': True, 'width': 80, 'cellStyle': {'fontWeight': 'bold', 'color': 'lightgreen'}}
                ],
                'rowData': rows,
                'pagination': True,
                'paginationPageSize': 50,
                'theme': 'ag-theme-alpine-dark'
            }).classes('w-full h-[600px]')

    content_area()

    ui.timer(0.1, load_data, once=True)
