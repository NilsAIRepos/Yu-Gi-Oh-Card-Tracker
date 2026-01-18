from nicegui import ui, run
from src.core.persistence import persistence
from src.core.models import Deck, Collection
from src.services.ygo_api import ygo_service, ApiCard
from src.services.banlist_service import banlist_service
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
import uuid
import json

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
        ui.add_head_html('<style>.sortable-ghost-custom { opacity: 0.5; }</style>')
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
                    ghostClass: 'sortable-ghost-custom',
                    forceFallback: true,
                    fallbackTolerance: 3,
                    onClone: function (evt) {
                         evt.clone.removeAttribute('id');
                    },
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
                                    from_ids: fromIds,
                                    new_index: evt.newIndex,
                                    old_index: evt.oldIndex
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

        last_sort_by = ui_state.get('deck_builder_sort_by', 'Name')
        last_sort_desc = ui_state.get('deck_builder_sort_desc', False)

        # Default to TCG if no state exists, but respect None (No Banlist) if saved
        if 'deck_builder_last_banlist' in ui_state:
            last_banlist = ui_state['deck_builder_last_banlist']
        else:
            last_banlist = 'TCG'

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

            'sort_by': last_sort_by,
            'sort_descending': last_sort_desc,

            'current_deck': None, # Deck object
            'current_deck_name': last_deck, # Initialize from session
            'reference_collection': None, # Collection object for ownership check
            'reference_collection_name': last_col, # Track filename

            'available_decks': [],
            'available_collections': [],

            'available_banlists': [],
            'current_banlist_name': last_banlist,
            'current_banlist_map': {}, # id -> status

            'all_api_cards': [], # List[ApiCard]
            'filtered_items': [], # List[ApiCard] for search results

            'page': 1,
            'page_size': config_manager.get_deck_builder_page_size(),
            'total_pages': 1,

            'loading': False
        }

        self.single_card_view = SingleCardView()
        self.filter_pane: Optional[FilterPane] = None
        self.api_card_map = {} # ID -> ApiCard
        self.dragged_item = None

        self.search_results_container = None
        self.deck_area_container = None

    def calculate_hierarchical_usage(self, target_zone: str) -> Dict[int, int]:
        """
        Calculates usage from zones with strictly higher priority (Main > Extra > Side).
        Used for full zone refreshes (e.g., page load) to ensure deterministic coloring.
        """
        deck = self.state['current_deck']
        if not deck: return {}

        base_usage = {}
        zones_order = ['main', 'extra', 'side']

        try:
            target_idx = zones_order.index(target_zone)
        except ValueError:
            return {}

        for i in range(target_idx):
            zone_name = zones_order[i]
            zone_ids = getattr(deck, zone_name, [])
            for cid in zone_ids:
                base_usage[cid] = base_usage.get(cid, 0) + 1

        return base_usage

    def calculate_global_usage(self) -> Dict[int, int]:
        """
        Calculates total usage across ALL zones.
        Used for dynamic surgical updates to treat the new card as the 'last' one.
        """
        deck = self.state['current_deck']
        if not deck: return {}

        usage = {}
        for zone in ['main', 'extra', 'side']:
            for cid in getattr(deck, zone, []):
                usage[cid] = usage.get(cid, 0) + 1
        return usage

    def calculate_deck_counts(self) -> Dict[int, int]:
        """Calculates total quantities of each card across Main, Extra, and Side decks."""
        deck = self.state['current_deck']
        if not deck: return {}

        counts = {}
        for zone in ['main', 'extra', 'side']:
            for cid in getattr(deck, zone, []):
                counts[cid] = counts.get(cid, 0) + 1
        return counts

    def calculate_missing_counts(self, deck_counts: Dict[int, int]) -> Dict[int, int]:
        """Compares deck counts against the reference collection and returns the difference."""
        ref_col = self.state['reference_collection']
        missing = {}

        # If no collection is selected, everything is missing
        if not ref_col:
            return deck_counts

        # Create a map of owned quantities
        owned_map = {}
        for c in ref_col.cards:
            owned_map[c.card_id] = c.total_quantity

        for cid, required_qty in deck_counts.items():
            owned_qty = owned_map.get(cid, 0)
            if owned_qty < required_qty:
                missing[cid] = required_qty - owned_qty

        return missing

    def get_export_data(self, mode: str) -> List[Dict]:
        """
        Orchestrates the export data preparation.
        mode: 'full' or 'missing'
        """
        deck_counts = self.calculate_deck_counts()

        if mode == 'missing':
            target_counts = self.calculate_missing_counts(deck_counts)
        else:
            target_counts = deck_counts

        export_list = []
        for cid, qty in target_counts.items():
            card = self.api_card_map.get(cid)
            name = card.name if card else f"Unknown Card ({cid})"
            export_list.append({
                'id': cid,
                'name': name,
                'quantity': qty
            })

        # Sort by name for nicer output
        export_list.sort(key=lambda x: x['name'])
        return export_list

    def generate_csv_export(self, data: List[Dict]) -> str:
        """Generates a CSV string from the export data."""
        lines = ["Card Name,Quantity"]
        for item in data:
            # Escape quotes in names if necessary
            name = item['name'].replace('"', '""')
            lines.append(f'"{name}",{item["quantity"]}')
        return "\n".join(lines)

    def generate_json_export(self, data: List[Dict]) -> str:
        """Generates a JSON string from the export data."""
        return json.dumps(data, indent=2)

    def generate_cardmarket_export(self, data: List[Dict]) -> str:
        """Generates a Cardmarket-compatible wants list string."""
        lines = []
        for item in data:
            lines.append(f"{item['quantity']} {item['name']}")
        return "\n".join(lines)

    def refresh_zone(self, zone):
        self._refresh_zone_content(zone)


    async def load_initial_data(self):
        self.state['loading'] = True
        try:
            # Load API Data
            lang = config_manager.get_language()
            api_cards = await ygo_service.load_card_database(lang)
            self.state['all_api_cards'] = api_cards
            self.api_card_map = {c.id: c for c in api_cards}

            # Load Banlists
            await banlist_service.fetch_default_banlists()
            self.state['available_banlists'] = banlist_service.get_banlists()

            # Load default banlist
            # If current selection is invalid (e.g. file deleted), revert to None (No Banlist)
            if self.state['current_banlist_name'] and self.state['current_banlist_name'] not in self.state['available_banlists']:
                 self.state['current_banlist_name'] = None

            # Load the actual map if a banlist is selected
            if self.state['current_banlist_name']:
                 self.state['current_banlist_map'] = await banlist_service.load_banlist(self.state['current_banlist_name'])
            else:
                 self.state['current_banlist_map'] = {}

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

    async def remove_card_from_deck(self, card_id: int, target: str, card_element: ui.card = None, card_uid: str = None):
        if not self.state['current_deck']: return

        real_target = target
        if card_uid:
            try:
                # Find which deck-zone contains this card element (in case it was moved via drag-and-drop)
                zone_id = await ui.run_javascript(f"return document.getElementById('{card_uid}')?.closest('[id^=deck-]')?.id", timeout=1.0)
                if zone_id:
                    real_target = zone_id.replace('deck-', '')
            except Exception as e:
                logger.warning(f"Failed to detect card zone via JS: {e}")

        deck = self.state['current_deck']
        if not hasattr(deck, real_target): return

        target_list = getattr(deck, real_target)

        if card_id in target_list:
            target_list.remove(card_id)
            await self.save_current_deck()

            if card_element:
                card_element.delete()
            else:
                self.refresh_zone(real_target)

            self.update_zone_headers()

    async def apply_filters(self):
        source = self.state['all_api_cards']
        res = list(source)

        # Helpers for sorting/filtering
        ref_col = self.state['reference_collection']
        owned_map = {}
        if ref_col:
            owned_map = {c.card_id: c for c in ref_col.cards}

        def get_qty(c):
             if not ref_col: return 0
             found = owned_map.get(c.id)
             return found.total_quantity if found else 0

        def get_price(c):
             if not c.card_prices: return 0.0
             try:
                 return float(c.card_prices[0].tcgplayer_price or 0)
             except: return 0.0

        txt = self.state['search_text'].lower()
        if txt:
             def matches(c):
                 if txt in c.name.lower() or txt in c.type.lower() or txt in c.desc.lower():
                     return True
                 if c.card_sets:
                     for s in c.card_sets:
                         if txt in s.set_code.lower():
                             return True
                 return False
             res = [c for c in res if matches(c)]

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

        if self.state['filter_set']:
             # Format: "Set Name | Code"
             target = self.state['filter_set'].split('|')[0].strip().lower()
             res = [c for c in res if any(target in (s.set_name or '').lower() or target in (s.set_code or '').lower() for s in c.card_sets)]

        if self.state['filter_rarity']:
             target = self.state['filter_rarity'].lower()
             res = [c for c in res if any(target == (s.set_rarity or '').lower() for s in c.card_sets)]

        if self.state['filter_monster_category']:
             # Check if card matches ANY of the selected categories
             cats = self.state['filter_monster_category']
             res = [c for c in res if any(c.matches_category(cat) for cat in cats)]

        if self.state['filter_level'] is not None:
             res = [c for c in res if c.level == int(self.state['filter_level'])]

        atk_min, atk_max = self.state['filter_atk_min'], self.state['filter_atk_max']
        if atk_min > 0 or atk_max < 5000:
             res = [c for c in res if c.atk is not None and atk_min <= int(c.atk) <= atk_max]

        def_min, def_max = self.state['filter_def_min'], self.state['filter_def_max']
        if def_min > 0 or def_max < 5000:
             res = [c for c in res if c.def_ is not None and def_min <= int(c.def_) <= def_max]

        # Ownership Filters - (Helper map already created at top)

        # Quantity Range
        own_min, own_max = self.state['filter_ownership_min'], self.state['filter_ownership_max']
        if own_min > 0 or own_max < 100:
             res = [c for c in res if own_min <= get_qty(c) <= own_max]

        # Condition
        if self.state['filter_condition'] and ref_col:
             conds = set(self.state['filter_condition'])
             def has_condition(c):
                 found = owned_map.get(c.id)
                 if not found: return False
                 for v in found.variants:
                     for e in v.entries:
                         if e.condition in conds and e.quantity > 0:
                             return True
                 return False
             res = [c for c in res if has_condition(c)]

        # Owned Language
        if self.state['filter_owned_lang'] and ref_col:
             lang = self.state['filter_owned_lang']
             def has_lang(c):
                 found = owned_map.get(c.id)
                 if not found: return False
                 for v in found.variants:
                     for e in v.entries:
                         if e.language == lang and e.quantity > 0:
                             return True
                 return False
             res = [c for c in res if has_lang(c)]

        # Price Range
        p_min, p_max = self.state['filter_price_min'], self.state['filter_price_max']
        if p_min > 0 or p_max < 1000:
             res = [c for c in res if p_min <= get_price(c) <= p_max]

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
        elif key == 'Price':
             res.sort(key=lambda x: get_price(x), reverse=reverse)
        elif key == 'Quantity':
             res.sort(key=lambda x: get_qty(x), reverse=reverse)

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
            'filter_condition': [],
            'filter_monster_race': '',
            'filter_st_race': '',
            'filter_archetype': '',
            'filter_monster_category': [],
            'filter_level': None,
            'filter_atk_min': 0, 'filter_atk_max': 5000,
            'filter_def_min': 0, 'filter_def_max': 5000,
            'filter_ownership_min': 0, 'filter_ownership_max': 100,
            'filter_price_min': 0.0, 'filter_price_max': 1000.0,
            'filter_owned_lang': '',
            'only_owned': False
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

            async def save_deck_as():
                if not self.state['current_deck']:
                    ui.notify("No deck loaded.", type='warning')
                    return

                with ui.dialog() as d, ui.card():
                    ui.label('Save Deck As').classes('text-h6')
                    name_input = ui.input('New Name')
                    async def save():
                        name = name_input.value
                        if not name: return

                        filename = f"{name}.ydk"
                        if filename in self.state['available_decks']:
                             ui.notify(f"Deck '{name}' already exists!", type='negative')
                             return

                        try:
                            # Save current deck content to new file
                            await run.io_bound(persistence.save_deck, self.state['current_deck'], filename)

                            # Switch to new deck
                            self.state['current_deck_name'] = name
                            self.state['available_decks'] = persistence.list_decks()
                            persistence.save_ui_state({'deck_builder_last_deck': name})

                            self.render_header.refresh()
                            d.close()
                            ui.notify(f"Saved deck as: {name}", type='positive')
                        except Exception as e:
                            logger.error(f"Error saving deck: {e}")
                            ui.notify(f"Error saving: {e}", type='negative')

                    ui.button('Save', on_click=save).props('color=primary')
                d.open()

            ui.button(icon='save_as', on_click=save_deck_as).props('flat round color=white').tooltip('Save Deck As')

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

            # Banlist Selection
            banlist_options = {None: 'No Banlist'}
            for b in sorted(self.state['available_banlists']):
                banlist_options[b] = b

            # Ensure current value is in options to prevent 'Invalid value' error
            # This handles the initial load state where available_banlists might be empty
            curr_ban = self.state['current_banlist_name']
            if curr_ban is not None and curr_ban not in banlist_options:
                banlist_options[curr_ban] = curr_ban

            async def on_banlist_change(e):
                val = e.value
                persistence.save_ui_state({'deck_builder_last_banlist': val})
                self.state['current_banlist_name'] = val
                if val:
                     self.state['current_banlist_map'] = await banlist_service.load_banlist(val)
                else:
                     self.state['current_banlist_map'] = {}

                self.refresh_deck_area()
                self.refresh_search_results()

            ui.select(banlist_options, value=curr_ban, label='Banlist', on_change=on_banlist_change).classes('min-w-[150px]')

            async def save_banlist_as():
                with ui.dialog() as d, ui.card():
                    ui.label('Save Banlist As').classes('text-h6')
                    name_input = ui.input('New Name')
                    async def save():
                        if not name_input.value: return
                        await banlist_service.save_banlist(name_input.value, self.state['current_banlist_map'])
                        self.state['available_banlists'] = banlist_service.get_banlists()
                        self.state['current_banlist_name'] = name_input.value
                        self.render_header.refresh()
                        d.close()
                        ui.notify(f"Saved banlist: {name_input.value}", type='positive')

                    ui.button('Save', on_click=save).props('color=primary')
                d.open()

            ui.button(icon='save_as', on_click=save_banlist_as).props('flat round color=white').tooltip('Save Banlist As')

            ui.button(icon='download', on_click=self.open_export_dialog).props('flat round color=white').tooltip('Export Deck / Missing Cards')

            # Search and filters moved to library column

    def _render_ban_icon(self, card_id: int):
        status = self.state['current_banlist_map'].get(str(card_id))
        if not status: return

        with ui.element('div').classes('absolute top-1 left-1 z-10 pointer-events-none'):
             if status in ["Forbidden", "Banned"]:
                 ui.icon('block', color='red').classes('text-xl bg-white rounded-full shadow-sm')
             elif status == "Limited":
                 with ui.element('div').classes('w-5 h-5 rounded-full bg-orange-600 text-white flex items-center justify-center font-bold text-xs border border-white shadow-sm'):
                     ui.label('1')
             elif status == "Semi-Limited":
                 with ui.element('div').classes('w-5 h-5 rounded-full bg-yellow-500 text-black flex items-center justify-center font-bold text-xs border border-white shadow-sm'):
                     ui.label('2')

    def _setup_card_tooltip(self, card: ApiCard):
        if not card: return

        img_id = card.card_images[0].id if card.card_images else card.id
        high_res_url = card.card_images[0].image_url if card.card_images else None
        low_res_url = card.card_images[0].image_url_small if card.card_images else None

        # Check local high-res existence immediately
        is_local = image_manager.image_exists(img_id, high_res=True)
        initial_src = f"/images/{img_id}_high.jpg" if is_local else (high_res_url or low_res_url)

        # Create tooltip with transparent background and no padding
        # anchor/self props can be adjusted if needed, but default behavior is usually acceptable
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

    def refresh_search_results(self):
        if not self.search_results_container: return
        self.search_results_container.clear()
        with self.search_results_container:
            # Header is now static in build_ui

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
                     ui.button(icon='chevron_left', on_click=lambda: change_page(-1)).props('flat dense color=white')
                     ui.button(icon='chevron_right', on_click=lambda: change_page(1)).props('flat dense color=white')

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

                         with ui.card().classes('p-0 cursor-pointer hover:scale-105 transition-transform border border-gray-800 w-full h-full select-none') \
                            .props(f'data-id="{card.id}"') \
                            .on('click', lambda c=card: self.open_deck_builder_wrapper(c)):

                             with ui.element('div').classes('relative w-full aspect-[2/3]'):
                                 ui.image(img_src).classes('w-full h-full object-cover')
                                 if owned_qty > 0:
                                     ui.label(f"{owned_qty}").classes('absolute top-1 right-1 bg-accent text-dark font-bold px-2 rounded-full text-xs')

                                 self._render_ban_icon(card.id)

                             with ui.column().classes('p-1 gap-0 w-full'):
                                 ui.label(card.name).classes('text-[10px] font-bold w-full leading-tight line-clamp-2 text-wrap h-6 select-none')
                                 ui.label(card.type).classes('text-[9px] text-gray-400 truncate w-full select-none')

                             self._setup_card_tooltip(card)

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

    def _render_deck_card(self, card_id: int, target: str, usage_counter: Dict[int, int] = None, owned_map: Dict[int, int] = None):
        if usage_counter is None: usage_counter = {}
        if owned_map is None: owned_map = {}

        card = self.api_card_map.get(card_id)
        if not card: return None

        img_id = card.card_images[0].id if card.card_images else card.id
        img_src = f"/images/{img_id}.jpg" if image_manager.image_exists(img_id) else (card.card_images[0].image_url_small if card.card_images else None)

        # Ownership
        is_owned_copy = True
        if self.state['reference_collection']:
            owned_total = owned_map.get(card_id, 0)
            used_so_far = usage_counter.get(card_id, 0)
            if used_so_far >= owned_total:
                is_owned_copy = False
            usage_counter[card_id] = used_so_far + 1

        classes = 'p-0 cursor-pointer w-full aspect-[2/3] border-transparent hover:scale-105 transition-transform relative group border border-gray-800 select-none'
        if not is_owned_copy:
            classes += ' opacity-50 grayscale'
        else:
            classes += ' opacity-100'

        uid = f"card-{uuid.uuid4()}"
        card_el = ui.card().classes(classes).props(f'data-id="{card_id}" id="{uid}"')
        with card_el:
            ui.image(img_src).classes('w-full h-full object-cover rounded')
            with ui.element('div').classes('absolute inset-0 bg-black/50 hidden group-hover:flex items-center justify-center'):
                ui.icon('remove', color='white').classes('text-lg')

            self._render_ban_icon(card.id)
            self._setup_card_tooltip(card)

        card_el.on('click', lambda: self.open_deck_builder_wrapper(card))
        card_el.on('contextmenu.prevent', lambda _, c=card, t=target, el=card_el, u=uid: self.remove_card_from_deck(c.id, t, el, u))
        return card_el

    def _refresh_zone_content(self, target):
        if not hasattr(self, 'deck_grids') or target not in self.deck_grids: return
        grid = self.deck_grids[target]
        grid.clear()

        deck = self.state['current_deck']
        if not deck: return

        real_card_ids = getattr(deck, target)

        # Prepare ownership maps
        ref_col = self.state['reference_collection']
        owned_map = {}
        if ref_col:
            for c in ref_col.cards:
                owned_map[c.card_id] = c.total_quantity

        # Initialize usage counter with hierarchical usage (Main > Extra > Side)
        usage_counter = self.calculate_hierarchical_usage(target)

        with grid:
            for cid in real_card_ids:
                self._render_deck_card(cid, target, usage_counter, owned_map)

        ui.run_javascript(f'initSortable("deck-{target}", "deck", true, true)')

    def refresh_zone(self, zone):
        self._refresh_zone_content(zone)

    def refresh_deck_area(self):
        self.refresh_zone('main')
        self.refresh_zone('extra')
        self.refresh_zone('side')
        self.update_zone_headers()

    def setup_header(self, title, target):
        with ui.row().classes('w-full items-center justify-between q-mb-sm'):
            with ui.row().classes('gap-1 items-center'):
                ui.label(title).classes('font-bold text-white text-xs uppercase tracking-wider')
                # Initialize label with placeholder
                lbl = ui.label('(0)').classes('font-bold text-white text-xs uppercase tracking-wider')
                if not hasattr(self, 'header_count_labels'): self.header_count_labels = {}
                self.header_count_labels[target] = lbl

            with ui.button(icon='sort', on_click=lambda t=target: self.sort_deck(t)).props('flat dense size=sm color=white'):
                 ui.tooltip(f'Sort {title}')

    def update_zone_headers(self):
        if not hasattr(self, 'header_count_labels'): return

        deck = self.state['current_deck']
        for target in ['main', 'extra', 'side']:
            if target not in self.header_count_labels: continue

            lbl = self.header_count_labels[target]
            count = 0
            if deck: count = len(getattr(deck, target))

            is_invalid = False
            if target == 'main':
                 if count < 40 or count > 60: is_invalid = True
            elif target in ['extra', 'side']:
                 if count > 15: is_invalid = True

            lbl.text = f"({count})"
            if is_invalid:
                lbl.classes(remove='text-white', add='text-red-400')
            else:
                lbl.classes(remove='text-red-400', add='text-white')

    def setup_zone(self, title, target):
        # Zones expand dynamically based on content
        height_class = 'h-auto min-h-[220px]'
        with ui.column().classes(f'w-full {height_class} bg-dark border border-gray-700 p-2 rounded flex flex-col relative'):
            self.setup_header(title, target)

            # The container handles drops on empty space (appending)
            with ui.column().classes('w-full bg-black/20 rounded p-2 block relative transition-colors'):
                if not hasattr(self, 'deck_grids'): self.deck_grids = {}

                # Use standard ui.grid instead of refreshable
                self.deck_grids[target] = ui.grid(columns='repeat(auto-fill, minmax(110px, 1fr))') \
                    .classes('w-full gap-2 min-h-[100px]') \
                    .props(f'id="deck-{target}"')

                # Initial render handled by load_initial_data -> refresh_deck_area

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

        # Check for no-op moves to prevent unnecessary saves
        new_index = args.get('new_index')
        old_index = args.get('old_index')

        # 1. Gallery to Gallery (micro-drag in gallery)
        if from_zone == 'gallery' and to_zone == 'gallery':
            return

        # 2. Same zone, same index (drop in place)
        if from_zone == to_zone and new_index == old_index:
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
        zones_to_refresh = set()

        if from_zone == 'gallery':
            # Optimize Gallery -> Deck addition to prevent flashing.
            # Instead of refreshing the whole zone, we replace the SortableJS clone with a real deck card.
            new_index = args.get('new_index')
            if new_index is not None and to_zone in self.deck_grids:
                 # 1. Identify the new card ID (it's the one in to_ids that wasn't there before, or we just trust the index)
                 if new_index < len(to_ids):
                     new_card_id = to_ids[new_index]

                     # 2. Remove the "dumb clone" dropped by SortableJS
                     await ui.run_javascript(f"var p = document.getElementById('deck-{to_zone}'); if(p && p.children[{new_index}]) p.children[{new_index}].remove();")

                     # 3. Prepare ownership data for rendering
                     ref_col = self.state['reference_collection']
                     owned_map = {}
                     if ref_col:
                         for c in ref_col.cards:
                             owned_map[c.card_id] = c.total_quantity

                     # Dynamic Update Strategy: "Last Arrived = Lowest Priority"
                     # We calculate the TOTAL count of this card in the entire deck (including the new one).
                     # We treat this new specific card instance as the Nth copy, where N is the total count.
                     # This ensures that if we have enough Owned copies, this one is colored.
                     # If we exceeded Owned copies, this NEW one becomes Grayscale, preserving the others.

                     global_usage = self.calculate_global_usage()
                     total_copies = global_usage.get(new_card_id, 0)

                     # The 'usage_so_far' passed to _render_deck_card is the count of *previous* copies.
                     # Since we want this card to be the Last one, we say there are (Total - 1) copies before it.
                     used_so_far = max(0, total_copies - 1)
                     usage_counter = {new_card_id: used_so_far}

                     # 4. Render the new real card (appends to end)
                     grid = self.deck_grids[to_zone]
                     with grid:
                         new_card = self._render_deck_card(new_card_id, to_zone, usage_counter, owned_map)

                     # 5. Move to correct index
                     if new_card:
                         new_card.move(grid, new_index)

            # Refresh gallery to reset state/listeners and fix potential UI glitches
            self.refresh_search_results()

            # No full refresh needed for the deck zone!
        else:
             # Intra-deck moves logic remains same (skip refresh)
             pass

        for z in zones_to_refresh:
            if z in valid_zones:
                self.refresh_zone(z)

        self.update_zone_headers()

    def open_export_dialog(self):
        if not self.state['current_deck']:
            ui.notify("Please select a deck first.", type='warning')
            return

        with ui.dialog() as d, ui.card().classes('w-[500px]') as container:
            # Content container that we can clear/replace
            content_area = ui.column().classes('w-full')

            def render_initial_options():
                content_area.clear()
                with content_area:
                    ui.label('Export Deck / Missing Cards').classes('text-h6')
                    scope_radio = ui.radio(['Full Deck', 'Missing Cards'], value='Full Deck').props('inline')

                    with ui.row().classes('w-full gap-2 q-mt-md'):
                        async def handle_export(format_type):
                            mode = 'full' if scope_radio.value == 'Full Deck' else 'missing'
                            data = self.get_export_data(mode)

                            if not data:
                                ui.notify("No cards to export.", type='warning')
                                return

                            if format_type == 'csv':
                                content = self.generate_csv_export(data)
                                ui.download(content.encode('utf-8'), f"{self.state['current_deck_name']}_{mode}.csv")
                                d.close()
                            elif format_type == 'json':
                                content = self.generate_json_export(data)
                                ui.download(content.encode('utf-8'), f"{self.state['current_deck_name']}_{mode}.json")
                                d.close()
                            elif format_type == 'cardmarket':
                                content = self.generate_cardmarket_export(data)
                                render_cardmarket_view(content)

                        ui.button('CSV', on_click=lambda: handle_export('csv')).classes('flex-grow')
                        ui.button('JSON', on_click=lambda: handle_export('json')).classes('flex-grow')
                        ui.button('Cardmarket', on_click=lambda: handle_export('cardmarket')).classes('flex-grow')

            def render_cardmarket_view(content):
                content_area.clear()
                with content_area:
                    ui.label('Cardmarket Wants List').classes('text-h6')
                    ui.label('Copy the text below and paste it into Cardmarket.').classes('text-sm text-grey')
                    ui.textarea(value=content).props('readonly autogrow').classes('w-full h-[300px]')

                    with ui.row().classes('w-full gap-2 q-mt-md'):
                        ui.button('Back', on_click=render_initial_options).props('flat')
                        ui.button('Close', on_click=d.close).classes('flex-grow')

            render_initial_options()

        d.open()

    def build_ui(self):
        self.filter_dialog = ui.dialog().props('position=right')
        with self.filter_dialog, ui.card().classes('h-full w-96 bg-gray-900 border-l border-gray-700 p-0 flex flex-col'):
             with ui.scroll_area().classes('flex-grow w-full'):
                 self.filter_pane = FilterPane(self.state, self.apply_filters, self.reset_filters)
                 self.filter_pane.build()

        self.render_header()
        # Removed fixed height to allow page scrolling
        with ui.row().classes('w-full gap-4 flex-nowrap items-start') \
            .props('id="deck-builder-container"') \
            .on('deck_change', self.handle_deck_change):

            # Gallery is sticky so it stays visible while scrolling decks
            with ui.column().classes('w-1/4 h-[calc(100vh-140px)] sticky top-4 bg-dark border border-gray-800 rounded flex flex-col deck-builder-search-results relative overflow-hidden'):
                # HEADER (Search, Filters, etc.)
                with ui.column().classes('w-full p-2 gap-2 border-b border-gray-800 bg-gray-900'):
                     with ui.row().classes('w-full items-center justify-between'):
                         ui.label('Library').classes('text-h6 text-white font-bold')

                         with ui.row().classes('gap-1 items-center'):
                             # Sort Controls
                             sort_btn = None

                             async def on_sort_change(e):
                                 self.state['sort_by'] = e.value
                                 # Smart default similar to Collection
                                 if e.value != 'Name': self.state['sort_descending'] = True
                                 else: self.state['sort_descending'] = False

                                 persistence.save_ui_state({
                                     'deck_builder_sort_by': self.state['sort_by'],
                                     'deck_builder_sort_desc': self.state['sort_descending']
                                 })

                                 if sort_btn:
                                     sort_btn.props(f'icon={"arrow_downward" if self.state["sort_descending"] else "arrow_upward"}')
                                 await self.apply_filters()

                             ui.select(['Name', 'ATK', 'DEF', 'Level', 'Newest', 'Price', 'Quantity'],
                                       value=self.state['sort_by'], on_change=on_sort_change) \
                                       .props('dense options-dense').classes('w-24 text-xs')

                             async def toggle_sort():
                                 self.state['sort_descending'] = not self.state['sort_descending']
                                 persistence.save_ui_state({'deck_builder_sort_desc': self.state['sort_descending']})
                                 if sort_btn:
                                     sort_btn.props(f'icon={"arrow_downward" if self.state["sort_descending"] else "arrow_upward"}')
                                 await self.apply_filters()

                             sort_icon = 'arrow_downward' if self.state['sort_descending'] else 'arrow_upward'
                             with ui.button(icon=sort_icon, on_click=toggle_sort).props('flat dense size=sm color=white') as b:
                                 sort_btn = b
                                 ui.tooltip('Toggle Sort Direction')

                             with ui.button(icon='filter_list', on_click=self.filter_dialog.open).props('flat color=white dense'):
                                 ui.tooltip('Filters')

                     async def on_search(e):
                        self.state['search_text'] = e.value
                        await self.apply_filters()
                     ui.input(placeholder='Search...', value=self.state['search_text'], on_change=on_search) \
                        .props('debounce=300 icon=search dense outlined dark input-class=text-white').classes('w-full')

                     async def on_owned_toggle(e):
                        self.state['only_owned'] = e.value
                        await self.apply_filters()
                     ui.switch('Owned Only', value=self.state['only_owned'], on_change=on_owned_toggle).props('dense').classes('text-white text-xs')

                # RESULTS CONTAINER
                self.search_results_container = ui.column().classes('w-full flex-grow overflow-hidden flex flex-col')

            # Deck area grows with content
            with ui.column().classes('flex-grow relative deck-builder-deck-area gap-2'):
                 self.setup_zone('Main Deck', 'main')
                 self.setup_zone('Extra Deck', 'extra')
                 self.setup_zone('Side Deck', 'side')

        self.refresh_search_results()
        ui.timer(0.1, self.load_initial_data, once=True)

def deck_builder_page():
    page = DeckBuilderPage()
    page.build_ui()
