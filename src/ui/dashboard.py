from nicegui import ui, run
from src.core.persistence import persistence
from src.services.ygo_api import ygo_service
from src.core.config import config_manager
import logging

logger = logging.getLogger(__name__)

async def load_dashboard_data(filename=None):
    """
    Loads the necessary data for the dashboard:
    1. Card Database (for total unique counts)
    2. Selected Collection (for user stats)

    Returns: (stats, all_collections, current_selection)
    """
    try:
        # 1. Load Database Size
        lang = config_manager.get_language()
        db_cards = await ygo_service.load_card_database(lang)
        total_db_unique = len(db_cards) if db_cards else 0

        # 2. Determine Collection to load
        files = persistence.list_collections()
        ui_state = persistence.load_ui_state()

        selected_file = filename
        if not selected_file:
             selected_file = ui_state.get('collection_selected_file')

        # Fallback if no selection or selection not found
        if not selected_file and files:
            selected_file = files[0]
        elif selected_file and selected_file not in files and files:
             selected_file = files[0]
        elif not files:
             selected_file = None

        collection = None
        if selected_file:
            try:
                collection = await run.io_bound(persistence.load_collection, selected_file)
            except Exception as e:
                logger.error(f"Failed to load collection {selected_file}: {e}")

        # 3. Calculate Stats
        stats = {
            'unique_owned': 0,
            'total_qty': 0,
            'total_value': 0.0,
            'completion_pct': 0.0,
            'rarity_dist': {},
            'condition_dist': {}
        }

        if collection:
            stats['unique_owned'] = len(collection.cards)
            stats['total_qty'] = collection.total_cards
            stats['total_value'] = collection.total_value

            if total_db_unique > 0:
                stats['completion_pct'] = (len(collection.cards) / total_db_unique) * 100

            # Distributions
            r_dist = {}
            c_dist = {}

            for card in collection.cards:
                for var in card.variants:
                    # Rarity - weighted by quantity
                    qty = var.total_quantity
                    r = var.rarity
                    r_dist[r] = r_dist.get(r, 0) + qty

                    # Condition
                    for entry in var.entries:
                        c = entry.condition
                        c_dist[c] = c_dist.get(c, 0) + entry.quantity

            stats['rarity_dist'] = r_dist
            stats['condition_dist'] = c_dist
            stats['collection_name'] = collection.name
        else:
            stats['collection_name'] = "No Collection Selected"

        return stats, files, selected_file

    except Exception as e:
        logger.error(f"Error loading dashboard data: {e}")
        return None, [], None

def metric_card(label, value, icon, color='accent', sub_text=None):
    with ui.card().classes('flex-1 bg-dark border border-gray-700 p-4 items-center flex-row gap-4 min-w-[200px] hover:border-gray-500 transition-colors'):
        with ui.element('div').classes(f'p-3 rounded-full bg-{color}/10'):
            ui.icon(icon, size='2rem').classes(f'text-{color}')

        with ui.column().classes('gap-0'):
            ui.label(label).classes('text-grey-4 text-xs uppercase font-bold tracking-wider')
            ui.label(str(value)).classes(f"text-2xl font-bold text-white")
            if sub_text:
                ui.label(sub_text).classes('text-xs text-grey-600')

def nav_card(title, description, icon, target_url, color_class='text-accent', is_large=False):
    with ui.card().classes('group relative overflow-hidden bg-gray-900 border border-gray-800 p-6 cursor-pointer hover:border-gray-600 hover:bg-gray-800 transition-all duration-300') \
            .on('click', lambda: ui.navigate.to(target_url)):

        # Hover glow effect
        ui.element('div').classes('absolute -right-6 -top-6 w-24 h-24 bg-white/5 rounded-full blur-xl group-hover:bg-white/10 transition-all')

        with ui.row().classes('w-full items-start justify-between q-mb-md'):
            with ui.element('div').classes('p-3 rounded-lg bg-gray-800 group-hover:bg-gray-700 transition-colors'):
                ui.icon(icon, size='2rem').classes(color_class)

            ui.icon('arrow_forward', size='1.2rem').classes('text-gray-600 group-hover:text-white transition-colors opacity-0 group-hover:opacity-100 transform translate-x-[-10px] group-hover:translate-x-0 transition-all duration-300')

        title_size = 'text-2xl' if is_large else 'text-xl'
        desc_size = 'text-base' if is_large else 'text-sm'

        ui.label(title).classes(f'{title_size} font-bold text-white q-mb-sm group-hover:text-accent transition-colors')
        ui.label(description).classes(f'{desc_size} text-gray-400 leading-relaxed')

@ui.refreshable
def render_metrics(stats):
    if not stats: return

    # Display Collection Name context inside metrics area or above?
    # User asked for dropdown in header, so maybe just metrics here.

    with ui.row().classes('w-full gap-4'):
        metric_card('Unique Cards', f"{stats['unique_owned']:,}", 'style', 'primary')
        metric_card('Total Quantity', f"{stats['total_qty']:,}", 'format_list_numbered', 'secondary')
        # metric_card('Est. Value', f"${stats['total_value']:,.2f}", 'attach_money', 'positive')
        metric_card('Completion (of total database)', f"{stats['completion_pct']:.1f}%", 'pie_chart', 'info')

@ui.refreshable
def render_charts_area(stats):
    if not stats: return

    # Rarity Pie Chart
    r_data = [{'value': v, 'name': k} for k, v in stats['rarity_dist'].items()]
    r_data.sort(key=lambda x: x['value'], reverse=True)

    # Condition Pie Chart
    c_data = [{'value': v, 'name': k} for k, v in stats['condition_dist'].items()]
    c_data.sort(key=lambda x: x['value'], reverse=True)

    def pie_option(title, data, color_palette=None):
        return {
            'backgroundColor': 'transparent',
            'title': {
                'text': title,
                'left': 'center',
                'textStyle': {'color': '#ccc'}
            },
            'tooltip': {
                'trigger': 'item',
                'formatter': '{b}: {c} ({d}%)'
            },
            'legend': {
                'type': 'scroll',
                'orient': 'vertical',
                'left': 'left',
                'top': 40,
                'bottom': 20,
                'textStyle': {'color': '#999'},
                'pageIconColor': '#fff',
                'pageTextStyle': {'color': '#fff'}
            },
            'series': [
                {
                    'name': title,
                    'type': 'pie',
                    'radius': ['40%', '70%'],
                    'center': ['60%', '50%'],
                    'avoidLabelOverlap': False,
                    'itemStyle': {
                        'borderRadius': 10,
                        'borderColor': '#11111b', # matches bg-dark
                        'borderWidth': 2
                    },
                    'label': {
                        'show': False,
                        'position': 'center'
                    },
                    'emphasis': {
                        'label': {
                            'show': True,
                            'fontSize': '20',
                            'fontWeight': 'bold',
                            'color': '#fff',
                            'formatter': '{d}%'
                        }
                    },
                    'labelLine': {
                        'show': False
                    },
                    'data': data
                }
            ]
        }

    with ui.row().classes('w-full gap-6'):
        # Rarity
        with ui.card().classes('flex-1 bg-dark border border-gray-700 h-80 p-4 min-w-0'):
             if r_data:
                ui.echart(pie_option('Rarity Distribution', r_data)).classes('w-full h-full')
             else:
                 ui.label('No Rarity Data').classes('w-full h-full flex items-center justify-center text-grey')

        # Condition
        with ui.card().classes('flex-1 bg-dark border border-gray-700 h-80 p-4 min-w-0'):
            if c_data:
                ui.echart(pie_option('Condition Distribution', c_data)).classes('w-full h-full')
            else:
                 ui.label('No Condition Data').classes('w-full h-full flex items-center justify-center text-grey')


def dashboard_page():
    # Container
    content = ui.column().classes('w-full gap-8 p-4')

    async def build_content():
        with content:
            # Load Initial Data
            spinner = ui.spinner('dots').classes('self-center q-my-xl')
            stats, files, current_file = await load_dashboard_data()
            spinner.delete()

            if stats is None:
                ui.label('Failed to load dashboard data.').classes('text-negative')
                return

            # --- Header & Dropdown ---
            with ui.row().classes('w-full items-center justify-between'):
                with ui.column().classes('gap-1'):
                    ui.label('Dashboard').classes('text-3xl font-bold text-white')
                    ui.label('Welcome back! Here is an overview of your collection.').classes('text-gray-400')

                with ui.row().classes('items-center gap-4'):
                    # Collection Dropdown
                    if files:
                        async def on_collection_change(e):
                            new_file = e.value
                            if new_file:
                                persistence.save_ui_state({'collection_selected_file': new_file})
                                # Reload Data
                                n = ui.notification('Loading...', spinner=True, timeout=None)
                                new_stats, _, _ = await load_dashboard_data(new_file)
                                render_metrics.refresh(new_stats)
                                render_charts_area.refresh(new_stats)
                                n.dismiss()

                        ui.select(options=files, value=current_file, label='Selected Collection',
                                  on_change=on_collection_change).classes('w-64')

                    # Github Link
                    ui.link('GitHub Repo', 'https://github.com/NilsAIRepos/Yu-Gi-Oh-Card-Tracker', new_tab=True) \
                        .classes('text-gray-500 hover:text-white transition-colors text-sm flex items-center gap-2') \
                        .props('icon=open_in_new')

            # --- Metrics ---
            render_metrics(stats)

            # --- Navigation (Middle) ---
            ui.separator().classes('bg-gray-800 q-my-sm')
            ui.label('Quick Navigation').classes('text-xl font-bold text-white')

            # Main Functions (2 Rows, 2 Cols)
            with ui.grid(columns=2).classes('w-full gap-6'):
                nav_card('Collection',
                         'Manage your inventory, view prices, and track your progress.',
                         'style', '/collection', 'text-blue-400', is_large=True)

                nav_card('Deck Builder',
                         'Create and edit decks using your collection or the full database.',
                         'construction', '/decks', 'text-yellow-500', is_large=True)

                nav_card('Browse Sets',
                         'Explore card sets, check rarity spread, and view completion.',
                         'library_books', '/sets', 'text-purple-400', is_large=True)

                nav_card('Bulk Add',
                         'Quickly add large numbers of cards via lists or drag-and-drop.',
                         'playlist_add', '/bulk_add', 'text-green-400', is_large=True)

            # Admin Functions (1 Row, 4 Cols)
            with ui.grid(columns=4).classes('w-full gap-6'):
                nav_card('Scan Cards',
                         'Use your webcam to scan physical cards and add them.',
                         'camera', '/scan', 'text-pink-400')

                nav_card('Import Tools',
                         'Import existing collections or merge data from other sources.',
                         'qr_code_scanner', '/import', 'text-orange-400')

                nav_card('Edit Card DB',
                         'Manually edit card database entries or fix issues.',
                         'edit', '/db_editor', 'text-red-400')

                nav_card('Documentation',
                         'View the Wiki for detailed guides and help.',
                         'menu_book', 'https://github.com/NilsAIRepos/Yu-Gi-Oh-Card-Tracker/tree/main/docs', 'text-cyan-400')

            # --- Charts (Bottom) ---
            ui.separator().classes('bg-gray-800 q-my-sm')
            ui.label('Analytics').classes('text-xl font-bold text-white')
            render_charts_area(stats)

    # Trigger load
    ui.timer(0.1, build_content, once=True)
