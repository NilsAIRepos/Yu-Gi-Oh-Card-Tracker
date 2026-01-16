from nicegui import ui, run
from src.core.persistence import persistence
from src.core.models import Collection, Card, CardMetadata
from src.services.ygo_api import ygo_service, ApiCard
from src.services.image_manager import image_manager
from src.core.config import config_manager
from src.core.utils import transform_set_code
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Set, Callable
import asyncio
import traceback
import re
import logging

logger = logging.getLogger(__name__)

SUPPORTED_LANGUAGES = ['EN', 'DE', 'FR', 'IT', 'ES', 'PT']
STANDARD_RARITIES = [
    'Common', 'Rare', 'Super Rare', 'Ultra Rare', 'Secret Rare',
    'Ultimate Rare', 'Ghost Rare', 'Starlight Rare', "Collector's Rare",
    'Prismatic Secret Rare', 'Platinum Secret Rare', 'Quarter Century Secret Rare',
    'Gold Rare'
]

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
    condition: str
    first_edition: bool
    image_id: Optional[int] = None

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
            # Exclude eBay and Amazon as requested
            for val in [p.cardmarket_price, p.tcgplayer_price, p.coolstuffinc_price]:
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

    def parse_set_code(code):
        # Parses LOB-EN001 into (LOB, 001). Returns (None, None) if format doesn't match.
        match = re.match(r'^([A-Za-z0-9]+)-([A-Za-z]+)?(\d+)$', code)
        if match:
            return match.group(1).upper(), match.group(3)
        return None, None

    for card in api_cards:
        owned_list = owned_details.get(card.name.lower(), [])

        img_url = card.card_images[0].image_url_small if card.card_images else None
        default_image_id = card.card_images[0].id if card.card_images else None

        matched_card_ids = set()

        if card.card_sets:
            for cset in card.card_sets:
                # API Set Data
                api_prefix, api_number = parse_set_code(cset.set_code)

                # Find matching owned cards (match Set Code Prefix + Number, ignoring language)
                matched_groups = {} # (lang, code, condition, first_edition) -> list[Card]

                for c in owned_list:
                    if c.id in matched_card_ids: continue # Already matched to another set

                    c_prefix, c_number = parse_set_code(c.metadata.set_code)

                    is_set_match = False
                    if api_prefix and c_prefix:
                        if api_prefix == c_prefix and api_number == c_number:
                            is_set_match = True
                    else:
                        if c.metadata.set_code == cset.set_code:
                            is_set_match = True

                    if not is_set_match: continue

                    if cset.image_id:
                        if c.metadata.image_id != cset.image_id:
                            continue
                    else:
                        if c.metadata.image_id is not None and c.metadata.image_id != default_image_id:
                            continue

                    # Group Key: Lang, Code, Condition, First Edition
                    g_key = (c.metadata.language.upper(), c.metadata.set_code, c.metadata.condition, c.metadata.first_edition)
                    if g_key not in matched_groups: matched_groups[g_key] = []
                    matched_groups[g_key].append(c)

                    matched_card_ids.add(c.id)

                # Determine Base Properties (from API set)
                base_lang = "EN"
                if "-" in cset.set_code:
                    parts = cset.set_code.split('-')
                    if len(parts) > 1:
                        reg_match = re.match(r'^([A-Za-z]+)', parts[1])
                        if reg_match:
                            r = reg_match.group(1).upper()
                            if r in ['EN', 'DE', 'FR', 'IT', 'PT', 'ES', 'JP']:
                                base_lang = r

                # Base Variant Key (Standard Placeholder)
                base_key = (base_lang, cset.set_code, "Near Mint", False)

                # Identify all unique keys to display (Union of owned and base)
                all_keys = set(matched_groups.keys())
                all_keys.add(base_key)

                # Prepare common data
                price = 0.0
                if cset.set_price:
                    try: price = float(cset.set_price)
                    except: pass

                row_img_url = img_url
                if cset.image_id:
                     for img in card.card_images:
                         if img.id == cset.image_id:
                             row_img_url = img.image_url_small
                             break

                # Generate Rows
                for key in sorted(list(all_keys), key=lambda k: (k != base_key, k)): # Put base key first if possible, or sort
                    (gl, gcode, gcond, gfirst) = key
                    gcards = matched_groups.get(key, [])
                    g_qty = sum(c.quantity for c in gcards)
                    is_owned = g_qty > 0

                    # If it's the base key and we don't own it, we still show it (Placeholder)
                    # If it's another key and we don't own it (shouldn't happen logic-wise as keys come from owned), we wouldn't add it unless it matches base key.

                    rows.append(CollectorRow(
                        api_card=card,
                        set_code=gcode,
                        set_name=cset.set_name,
                        rarity=cset.set_rarity,
                        price=price,
                        image_url=row_img_url,
                        owned_count=g_qty,
                        is_owned=is_owned,
                        language=gl,
                        condition=gcond,
                        first_edition=gfirst,
                        image_id=cset.image_id
                    ))

            # Handle unmatched owned cards
            for c in owned_list:
                if c.id not in matched_card_ids:
                    rows.append(CollectorRow(
                        api_card=card,
                        set_code=c.metadata.set_code,
                        set_name="Unknown / Custom Set",
                        rarity=c.metadata.rarity,
                        price=0.0,
                        image_url=c.image_url or img_url,
                        owned_count=c.quantity,
                        is_owned=True,
                        language=c.metadata.language.upper(),
                        condition=c.metadata.condition,
                        first_edition=c.metadata.first_edition,
                        image_id=c.metadata.image_id
                    ))
        else:
            # No sets in API - Fallback
            # Group owned by (lang, cond, 1st)
            groups = {}
            for c in owned_list:
                k = (c.metadata.language.upper(), c.metadata.condition, c.metadata.first_edition)
                groups[k] = groups.get(k, 0) + c.quantity

            # Add default placeholder if nothing owned or just to show existence
            # Assuming EN, NM, Unl
            base_key = (lang_upper, "Near Mint", False)
            all_keys = set(groups.keys())
            all_keys.add(base_key)

            for key in sorted(list(all_keys)):
                (gl, gcond, gfirst) = key
                qty = groups.get(key, 0)

                rows.append(CollectorRow(
                    api_card=card,
                    set_code="N/A",
                    set_name="No Set Info",
                    rarity="Common",
                    price=0.0,
                    image_url=img_url,
                    owned_count=qty,
                    is_owned=qty > 0,
                    language=gl,
                    condition=gcond,
                    first_edition=gfirst,
                    image_id=default_image_id
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
            'available_monster_races': [],
            'available_st_races': [],
            'available_archetypes': [],
            'available_card_types': ['Monster', 'Spell', 'Trap'],
            'max_owned_quantity': 100, # dynamic

            # Filters
            'search_text': '',
            'filter_set': '',
            'filter_rarity': '',
            'filter_attr': '',
            'filter_card_type': '',
            'filter_monster_race': '',
            'filter_st_race': '',
            'filter_archetype': '',
            'filter_monster_category': [], # List for multi-select
            'filter_level': None,
            'filter_atk_min': 0,
            'filter_atk_max': 5000,
            'filter_def_min': 0,
            'filter_def_max': 5000,

            # Ranges
            'filter_ownership_min': 0,
            'filter_ownership_max': 100,
            'filter_price_min': 0.0,
            'filter_price_max': 1000.0,

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

        # Refs for filter UI updates
        self.filter_inputs = {}

    async def load_data(self):
        # ui.notify(f'Loading data ({self.state["language"]})...', type='info')
        logger.info(f"Loading data... (Language: {self.state['language']})")

        try:
            # Ensure we load lowercase language to avoid API errors
            lang_code = self.state['language'].lower() if self.state['language'] else 'en'
            api_cards = await ygo_service.load_card_database(lang_code)
        except Exception as e:
            logger.error(f"Error loading database: {e}")
            ui.notify(f"Error loading database: {e}", type='negative')
            return

        # Extract Meta Data for Filters
        sets = set()
        m_races = set()
        st_races = set()
        archetypes = set()

        # We don't overwrite available_card_types anymore, hardcoded

        for c in api_cards:
            # Sets: Name | Prefix
            if c.card_sets:
                for s in c.card_sets:
                    # Extract prefix from Code (e.g. LOB-EN001 -> LOB)
                    # If split fails, just use code
                    parts = s.set_code.split('-')
                    prefix = parts[0] if len(parts) > 0 else s.set_code
                    sets.add(f"{s.set_name} | {prefix}")

            if c.archetype:
                archetypes.add(c.archetype)

            if "Monster" in c.type:
                m_races.add(c.race)
            elif "Spell" in c.type or "Trap" in c.type:
                if c.race: st_races.add(c.race)

        self.state['available_sets'] = sorted(list(sets))
        self.state['available_monster_races'] = sorted(list(m_races))
        self.state['available_st_races'] = sorted(list(st_races))
        self.state['available_archetypes'] = sorted(list(archetypes))

        # Load Collection
        collection = None
        if self.state['selected_file']:
            try:
                collection = await run.io_bound(persistence.load_collection, self.state['selected_file'])
            except Exception as e:
                logger.warning(f"Error loading collection {self.state['selected_file']}: {e}")
                ui.notify(f"Error loading collection: {e}", type='warning')

        self.state['current_collection'] = collection

        # Build Maps
        owned_details = {}
        max_qty = 0
        if collection:
            for c in collection.cards:
                key = c.name.lower()
                if key not in owned_details: owned_details[key] = []
                owned_details[key].append(c)
                max_qty = max(max_qty, c.quantity)

        # Update max owned for slider
        self.state['max_owned_quantity'] = max(100, max_qty)

        self.state['cards_consolidated'] = await run.io_bound(build_consolidated_vms, api_cards, owned_details)

        # Lazy load collectors view if needed, or just clear it so it rebuilds on switch
        self.state['cards_collectors'] = []
        if self.state['view_scope'] == 'collectors':
             self.state['cards_collectors'] = await run.io_bound(build_collector_rows, api_cards, owned_details, self.state['language'])

        await self.apply_filters()
        self.update_filter_ui()

        # ui.notify('Data loaded.', type='positive')
        logger.info(f"Data loaded. Items: {len(self.state['cards_consolidated'])}")

    def update_filter_ui(self):
        # Update dropdown options if they exist
        if hasattr(self, 'set_selector'):
            self.set_selector.options = self.state['available_sets']
            self.set_selector.update()
        if hasattr(self, 'm_race_selector'):
            self.m_race_selector.options = self.state['available_monster_races']
            self.m_race_selector.update()
        if hasattr(self, 'st_race_selector'):
            self.st_race_selector.options = self.state['available_st_races']
            self.st_race_selector.update()
        if hasattr(self, 'archetype_selector'):
            self.archetype_selector.options = self.state['available_archetypes']
            self.archetype_selector.update()

        # Update sliders max
        if 'ownership' in self.filter_inputs:
             slider, min_inp, max_inp = self.filter_inputs['ownership']
             slider.max = self.state['max_owned_quantity']
             slider.update()
             max_inp.max = self.state['max_owned_quantity']
             max_inp.update()

    async def reset_filters(self):
        self.state.update({
            'search_text': '',
            'filter_set': '',
            'filter_rarity': '',
            'filter_attr': '',
            'filter_card_type': '',
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
            'only_owned': False
        })

        # Manually update UI components that might not auto-sync completely via binding
        # (especially custom bound inputs/sliders)
        for key, components in self.filter_inputs.items():
            slider, min_inp, max_inp = components

            if key == 'atk':
                min_val, max_val = 0, 5000
            elif key == 'def':
                min_val, max_val = 0, 5000
            elif key == 'ownership':
                min_val, max_val = 0, self.state['max_owned_quantity']
            elif key == 'price':
                min_val, max_val = 0.0, 1000.0

            slider.value = {'min': min_val, 'max': max_val}
            min_inp.value = min_val
            max_inp.value = max_val

        await self.apply_filters()

    async def prepare_current_page_images(self):
        start = (self.state['page'] - 1) * self.state['page_size']
        end = min(start + self.state['page_size'], len(self.state['filtered_items']))
        items = self.state['filtered_items'][start:end]
        if not items: return

        url_map = {}
        for item in items:
            # Both have api_card
            card = item.api_card
            image_id = None
            url = None

            if self.state['view_scope'] == 'collectors':
                # item is CollectorRow
                # Use image_id if present, else default id
                image_id = item.image_id
                if not image_id and card.card_images:
                     image_id = card.card_images[0].id
                elif not image_id:
                     image_id = card.id

                url = item.image_url
            else:
                # Consolidated: use default
                if card.card_images:
                    image_id = card.card_images[0].id
                    url = card.card_images[0].image_url_small
                else:
                    image_id = card.id

            if image_id and url:
                url_map[image_id] = url

        if url_map:
             # Lazy load: download batch
             await image_manager.download_batch(url_map, concurrency=10)

    async def apply_filters(self, e=None):
        if self.state['view_scope'] == 'consolidated':
            source = self.state['cards_consolidated']
        else:
            source = self.state['cards_collectors']

        if not source:
            self.state['filtered_items'] = []
            if hasattr(self, 'content_area'): self.content_area.refresh()
            return

        res = list(source)

        # Search Text
        txt = self.state['search_text'].lower()
        if txt:
            res = [c for c in res if txt in c.api_card.name.lower() or
                   txt in c.api_card.type.lower() or
                   txt in c.api_card.desc.lower()]

        # Owned Filter (Switch)
        if self.state['only_owned']:
            res = [c for c in res if c.is_owned]

        # Ownership Range
        min_q = self.state['filter_ownership_min']
        max_q = self.state['filter_ownership_max']

        def get_qty(item):
            if hasattr(item, 'owned_quantity'): return item.owned_quantity
            return getattr(item, 'owned_count', 0)

        res = [c for c in res if min_q <= get_qty(c) <= max_q]

        # Price Range
        p_min = self.state['filter_price_min']
        p_max = self.state['filter_price_max']

        def get_price(item):
             if hasattr(item, 'lowest_price'): return item.lowest_price
             return getattr(item, 'price', 0.0)

        res = [c for c in res if p_min <= get_price(c) <= p_max]

        # Owned Language Filter
        if self.state['filter_owned_lang']:
            target_lang = self.state['filter_owned_lang']
            if self.state['view_scope'] == 'consolidated':
                res = [c for c in res if target_lang in c.owned_languages]
            else:
                 res = [c for c in res if c.language == target_lang]

        # Common Filters
        if self.state['filter_attr']:
            res = [c for c in res if c.api_card.attribute == self.state['filter_attr']]

        # Card Type Filter (Substring match: e.g. "Monster" matches "Effect Monster")
        if self.state['filter_card_type']:
             res = [c for c in res if self.state['filter_card_type'] in c.api_card.type]

        if self.state['filter_monster_race']:
             # Only applies to monsters
             res = [c for c in res if "Monster" in c.api_card.type and c.api_card.race == self.state['filter_monster_race']]

        if self.state['filter_st_race']:
             # Only Spells/Traps
             res = [c for c in res if ("Spell" in c.api_card.type or "Trap" in c.api_card.type) and c.api_card.race == self.state['filter_st_race']]

        if self.state['filter_archetype']:
             res = [c for c in res if c.api_card.archetype == self.state['filter_archetype']]

        # Monster Category Filter (Multi-select, AND logic)
        if self.state['filter_monster_category']:
             categories = self.state['filter_monster_category']
             if isinstance(categories, list) and categories:
                 # Check if ALL selected categories match
                 res = [c for c in res if all(c.api_card.matches_category(cat) for cat in categories)]

        if self.state['filter_level']:
             res = [c for c in res if c.api_card.level == int(self.state['filter_level'])]

        # ATK Filter
        atk_min, atk_max = self.state['filter_atk_min'], self.state['filter_atk_max']
        if atk_min > 0 or atk_max < 5000:
             res = [c for c in res if c.api_card.atk is not None and atk_min <= int(c.api_card.atk) <= atk_max]

        # DEF Filter
        def_min, def_max = self.state['filter_def_min'], self.state['filter_def_max']
        if def_min > 0 or def_max < 5000:
             # Use def_ field if available (aliased in pydantic usually) or getattr
             res = [c for c in res if getattr(c.api_card, 'def_', None) is not None and def_min <= getattr(c.api_card, 'def_', -1) <= def_max]

        # Set Filter (Enhanced)
        if self.state['filter_set']:
            s_val = self.state['filter_set']

            # Detect Strict Mode (Dropdown Selection has '|')
            is_strict = '|' in s_val

            if is_strict:
                # Format: "Name | Prefix"
                target_prefix = s_val.split('|')[-1].strip().lower()

                if self.state['view_scope'] == 'consolidated':
                    def match_set_strict(c):
                        if not c.api_card.card_sets: return False
                        for cs in c.api_card.card_sets:
                             # Strict Match on Prefix
                             parts = cs.set_code.split('-')
                             c_prefix = parts[0].lower() if parts else cs.set_code.lower()
                             if c_prefix == target_prefix:
                                 return True
                        return False
                    res = [c for c in res if match_set_strict(c)]
                else:
                    # Collectors view - row has set_code
                    def match_row_strict(c):
                        parts = c.set_code.split('-')
                        c_prefix = parts[0].lower() if parts else c.set_code.lower()
                        return c_prefix == target_prefix

                    res = [c for c in res if match_row_strict(c)]

            else:
                # Loose Match (Search Text)
                txt = s_val.strip().lower()

                if self.state['view_scope'] == 'consolidated':
                    def match_set_loose(c):
                        if not c.api_card.card_sets: return False
                        for cs in c.api_card.card_sets:
                            if txt in cs.set_code.lower() or txt in cs.set_name.lower():
                                return True
                        return False
                    res = [c for c in res if match_set_loose(c)]
                else:
                    res = [c for c in res if txt in c.set_code.lower() or txt in c.set_name.lower()]

        # Rarity Filter
        if self.state['filter_rarity']:
            r = self.state['filter_rarity'].lower()
            if self.state['view_scope'] == 'consolidated':
                 res = [c for c in res if c.api_card.card_sets and any(r == cs.set_rarity.lower() for cs in c.api_card.card_sets)]
            else:
                 res = [c for c in res if r == c.rarity.lower()]

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
             res.sort(key=lambda x: get_price(x))

        self.state['filtered_items'] = res
        self.state['page'] = 1
        self.update_pagination()

        await self.prepare_current_page_images()
        if hasattr(self, 'content_area'): self.content_area.refresh()

    def update_pagination(self):
        count = len(self.state['filtered_items'])
        self.state['total_pages'] = (count + self.state['page_size'] - 1) // self.state['page_size']

    async def switch_scope(self, scope):
        self.state['view_scope'] = scope
        if scope == 'collectors' and not self.state['cards_collectors']:
             await self.load_data()
        else:
            await self.apply_filters()

    def setup_high_res_image_logic(self, img_id: int, high_res_url: Optional[str], low_res_url: Optional[str], image_element: ui.image, current_id_check: Optional[Callable[[], bool]] = None):
        """
        Determines the initial image source (preferring local high-res) and schedules a download if needed.
        Updates the image_element.source when download completes.
        """
        # Determine initial source
        if image_manager.image_exists(img_id, high_res=True):
            display_url = f"/images/{img_id}_high.jpg"
            needs_download = False
        elif image_manager.image_exists(img_id, high_res=False):
            display_url = f"/images/{img_id}.jpg"
            needs_download = True
        else:
            # Fallback: Prefer low res url for display while loading high res
            display_url = low_res_url or high_res_url
            needs_download = True

        image_element.source = display_url

        async def download_task():
            if high_res_url:
                await image_manager.ensure_image(img_id, high_res_url, high_res=True)
                # Check consistency
                if current_id_check and not current_id_check():
                    return
                image_element.source = f"/images/{img_id}_high.jpg"

        if needs_download and high_res_url and not image_manager.image_exists(img_id, high_res=True):
             ui.timer(0.1, download_task, once=True)

    async def save_card_change(self, api_card: ApiCard, set_code, rarity, language, quantity, condition, first_edition, image_id: Optional[int] = None):
        if not self.state['current_collection']:
            ui.notify('No collection selected.', type='negative')
            return

        col = self.state['current_collection']
        target = None

        # If image_id is not provided, default to the first image id
        if image_id is None and api_card.card_images:
            image_id = api_card.card_images[0].id

        for c in col.cards:
            # Match on Name, Set, Rarity, Language, Condition, First Ed, AND Image ID
            c_img_id = c.metadata.image_id
            if c_img_id is None and api_card.card_images:
                c_img_id = api_card.card_images[0].id

            if (c.name == api_card.name and
                c.metadata.set_code == set_code and
                c.metadata.language == language and
                c.metadata.rarity == rarity and
                c.metadata.condition == condition and
                c.metadata.first_edition == first_edition and
                c_img_id == image_id):
                target = c
                break

        if quantity > 0:
            if target:
                target.quantity = quantity
                # Update image_id in case it was None and we matched it
                target.metadata.image_id = image_id
            else:
                # Find image URL for this ID
                img_url = None
                if api_card.card_images:
                    for img in api_card.card_images:
                        if img.id == image_id:
                            img_url = img.image_url_small
                            break
                    if not img_url:
                        img_url = api_card.card_images[0].image_url_small

                new_card = Card(
                    name=api_card.name,
                    quantity=quantity,
                    image_url=img_url,
                    metadata=CardMetadata(
                        set_code=set_code,
                        rarity=rarity,
                        language=language,
                        condition=condition,
                        first_edition=first_edition,
                        market_value=0.0,
                        image_id=image_id
                    )
                )
                col.cards.append(new_card)
        else:
            if target:
                col.cards.remove(target)

        try:
            await run.io_bound(persistence.save_collection, col, self.state['selected_file'])
            logger.info(f"Collection saved: {self.state['selected_file']}")
            ui.notify('Collection saved.', type='positive')
            await self.load_data()
        except Exception as e:
            logger.error(f"Error saving collection: {e}")
            ui.notify(f"Error saving: {e}", type='negative')

    def open_single_view(self, card: ApiCard, is_owned: bool = False, quantity: int = 0, initial_set: str = None, owned_languages: Set[str] = None, rarity: str = None, set_name: str = None, language: str = None, condition: str = "Near Mint", first_edition: bool = False, image_url: str = None, image_id: int = None, set_price: float = 0.0):
        if self.state['view_scope'] == 'consolidated':
            # Derive ownership data from current collection
            owned_breakdown = {}
            total_owned = 0
            if self.state['current_collection']:
                 for c in self.state['current_collection'].cards:
                     if c.name == card.name:
                         lang = c.metadata.language
                         owned_breakdown[lang] = owned_breakdown.get(lang, 0) + c.quantity
                         total_owned += c.quantity

            self.render_consolidated_single_view(card, total_owned, owned_breakdown)
            return

        if self.state['view_scope'] == 'collectors':
             self.render_collectors_single_view(card, quantity, initial_set, rarity, set_name, language, condition, first_edition, image_url, image_id, set_price)
             return

        self.open_single_view_legacy(card, is_owned, quantity, initial_set, owned_languages)

    def render_consolidated_single_view(self, card: ApiCard, total_owned: int, owned_breakdown: Dict[str, int]):
        try:
            with ui.dialog().props('maximized transition-show=slide-up transition-hide=slide-down') as d, ui.card().classes('w-full h-full p-0 no-shadow'):
                d.open()
                ui.button(icon='close', on_click=d.close).props('flat round color=white').classes('absolute top-2 right-2 z-50')

                with ui.row().classes('w-full h-full no-wrap gap-0'):
                    # Left: Image
                    with ui.column().classes('w-1/3 min-w-[300px] h-full bg-black items-center justify-center p-8 shrink-0'):
                        img_id = card.card_images[0].id if card.card_images else card.id
                        high_res_url = card.card_images[0].image_url if card.card_images else None
                        low_res_url = card.card_images[0].image_url_small if card.card_images else None

                        image_element = ui.image().classes('max-h-full max-w-full object-contain shadow-2xl')
                        self.setup_high_res_image_logic(img_id, high_res_url, low_res_url, image_element)

                    # Right: Info
                    with ui.column().classes('col h-full bg-gray-900 text-white p-8 scroll-y-auto'):
                        # Header
                        with ui.row().classes('w-full items-center justify-between'):
                            # Ensure title is selectable
                            ui.label(card.name).classes('text-4xl font-bold text-white select-text')
                        if total_owned > 0:
                            ui.badge(f"Total Owned: {total_owned}", color='accent').classes('text-lg')

                        ui.separator().classes('q-my-md bg-gray-700')

                        # Card Stats Grid
                        with ui.grid(columns=4).classes('w-full gap-4 text-lg'):
                            def stat(label, value):
                                with ui.column():
                                    ui.label(label).classes('text-grey text-sm uppercase select-none')
                                    # Ensure values are selectable
                                    ui.label(str(value) if value is not None else '-').classes('font-bold select-text')

                            stat('Card Type', card.type)

                            if 'Monster' in card.type:
                                stat('Attribute', card.attribute)
                                stat('Race', card.race)
                                stat('Archetype', card.archetype or '-')

                                if 'Link' in card.type:
                                    stat('Link Rating', card.linkval)
                                    if card.linkmarkers:
                                        stat('Link Markers', ', '.join(card.linkmarkers))
                                else:
                                    stat('Level/Rank', card.level)

                                if 'Pendulum' in card.type:
                                    stat('Scale', card.scale)

                                stat('ATK', card.atk)

                                if 'Link' not in card.type:
                                    # Use direct access to aliased field
                                    val = card.def_
                                    stat('DEF', val if val is not None else '-')
                            else:
                                stat('Property', card.race)
                                stat('Archetype', card.archetype or '-')

                        ui.separator().classes('q-my-md')

                        # Effect
                        ui.label('Effect').classes('text-h6 q-mb-sm select-none')
                        # Markdown is usually selectable, adding class to be sure
                        ui.markdown(card.desc).classes('text-grey-3 leading-relaxed text-lg select-text')

                        ui.separator().classes('q-my-md')

                        # Owned Breakdown
                        if owned_breakdown:
                            ui.label('Collection Status').classes('text-h6 q-mb-sm select-none')
                            with ui.row().classes('gap-2'):
                                for lang, count in owned_breakdown.items():
                                    ui.chip(f"{lang}: {count}", icon='layers').props('color=secondary text-color=white')
                        else:
                            ui.label('Not in collection').classes('text-grey italic')
        except Exception as e:
            logger.error(f"ERROR in render_consolidated_single_view: {e}", exc_info=True)

    def render_collectors_single_view(self, card: ApiCard, owned_count: int, set_code: str, rarity: str, set_name: str, language: str, condition: str, first_edition: bool, image_url: str = None, image_id: int = None, set_price: float = 0.0):
        try:
            # Set default image_id if not provided
            if image_id is None:
                image_id = card.card_images[0].id if card.card_images else None

            # Prepare Set Options
            # Map: Base Code -> {label, set_name}
            set_options = {}
            # Also need to map Back: Base Code -> ApiCardSet (sample) to get Name if needed
            set_info_map = {}

            if card.card_sets:
                for s in card.card_sets:
                    # Use set_code as key. Multiple rarities/arts share set_code.
                    code = s.set_code
                    if code not in set_options:
                        set_options[code] = f"{s.set_name} ({code})"
                        set_info_map[code] = s
            else:
                # If no sets, provide a fallback option or "Custom"
                set_options["Custom"] = "Custom Set"

            # Determine initial base set code from passed set_code
            initial_base_code = None
            if set_code in set_options:
                initial_base_code = set_code
            else:
                # Try to reverse transform or match prefix
                found = False
                for base in set_options.keys():
                    if transform_set_code(base, language) == set_code:
                        initial_base_code = base
                        found = True
                        break
                if not found:
                     initial_base_code = list(set_options.keys())[0] if set_options else "Custom"

            # Input State
            input_state = {
                'language': language,
                'quantity': 1,
                'rarity': rarity,
                'condition': condition,
                'first_edition': first_edition,
                'set_base_code': initial_base_code,
                'image_id': image_id
            }

            with ui.dialog().props('maximized transition-show=slide-up transition-hide=slide-down') as d, ui.card().classes('w-full h-full p-0 no-shadow'):
                d.open()
                ui.button(icon='close', on_click=d.close).props('flat round color=white').classes('absolute top-2 right-2 z-50')

                with ui.row().classes('w-full h-full no-wrap gap-0'):
                    # Left: Image
                    with ui.column().classes('w-1/3 min-w-[300px] h-full bg-black items-center justify-center p-8 shrink-0'):

                        image_element = ui.image().classes('max-h-full max-w-full object-contain shadow-2xl')

                        def update_image():
                            img_id = input_state['image_id']
                            high_res_remote_url = None
                            low_res_url = None

                            if card.card_images:
                                for img in card.card_images:
                                    if img.id == img_id:
                                        high_res_remote_url = img.image_url
                                        low_res_url = img.image_url_small
                                        break

                            if not low_res_url:
                                low_res_url = image_url or (card.card_images[0].image_url_small if card.card_images else None)

                            self.setup_high_res_image_logic(
                                img_id,
                                high_res_remote_url,
                                low_res_url,
                                image_element,
                                current_id_check=lambda: input_state['image_id'] == img_id
                            )

                        update_image()

                    # Right: Info
                    with ui.column().classes('col h-full bg-gray-900 text-white p-8 scroll-y-auto'):
                        # Header
                        with ui.row().classes('w-full items-center justify-between'):
                            ui.label(card.name).classes('text-h3 font-bold text-white select-text')

                        # Top Owned Badge (Reactive)
                        owned_badge = ui.badge(f"Owned: {owned_count}", color='accent').classes('text-lg')
                        if owned_count == 0:
                            owned_badge.set_visibility(False)

                        ui.separator().classes('q-my-md bg-gray-700')

                        # Dynamic Info State
                        # Use labels that we can update
                        with ui.grid(columns=4).classes('w-full gap-4 text-lg items-end'):
                            lbl_set_name = ui.label(f"Set: {set_name or 'N/A'}").classes('text-gray-400 text-sm')
                            lbl_set_code = ui.label(f"Code: {set_code}").classes('text-yellow-500 font-mono')
                            lbl_rarity = ui.label(f"Rarity: {rarity}").classes('text-sm')
                            lbl_lang = ui.label(f"Lang: {language}").classes('text-sm')

                        ui.separator().classes('q-my-md')

                        # Prices
                        ui.label('Market Prices').classes('text-h6 q-mb-sm select-none')
                        lbl_set_price = ui.label(f"Set Price: ${set_price:.2f}" if set_price else "Set Price: -").classes('text-purple-400 font-bold select-text')

                        # Update Function
                        def update_display_stats():
                            # 1. Update Labels based on selection
                            base_code = input_state['set_base_code']

                            s_name = "N/A"
                            s_price = None

                            if base_code in set_info_map:
                                s_obj = set_info_map[base_code]
                                s_name = s_obj.set_name
                                # Try to find price for this specific rarity if possible?
                                # card.card_sets has one entry per variant in DB.
                                # But we might select a rarity that doesn't exist in DB yet.
                                # Search DB for exact match
                                matched_set = None
                                for s in card.card_sets:
                                    s_img = s.image_id if s.image_id is not None else (card.card_images[0].id if card.card_images else None)
                                    if s.set_code == base_code and s.set_rarity == input_state['rarity'] and s_img == input_state['image_id']:
                                        matched_set = s
                                        break

                                if matched_set and matched_set.set_price:
                                    try: s_price = float(matched_set.set_price)
                                    except: pass

                            lbl_set_name.text = f"Set: {s_name}"
                            final_code = transform_set_code(base_code, input_state['language'])
                            lbl_set_code.text = f"Code: {final_code}"
                            lbl_rarity.text = f"Rarity: {input_state['rarity']}"
                            lbl_lang.text = f"Lang: {input_state['language']}"

                            lbl_set_price.text = f"Set Price: ${s_price:.2f}" if s_price is not None else "Set Price: -"

                            # 2. Update Owned Count
                            cur_owned = 0
                            if self.state['current_collection']:
                                for c in self.state['current_collection'].cards:
                                    c_img = c.metadata.image_id
                                    if c_img is None and card.card_images: c_img = card.card_images[0].id

                                    if (c.name == card.name and
                                        c.metadata.set_code == final_code and
                                        c.metadata.language == input_state['language'] and
                                        c.metadata.rarity == input_state['rarity'] and
                                        c.metadata.condition == input_state['condition'] and
                                        c.metadata.first_edition == input_state['first_edition'] and
                                        c_img == input_state['image_id']):
                                        cur_owned = c.quantity
                                        break

                            owned_badge.text = f"Owned: {cur_owned}"
                            owned_badge.set_visibility(cur_owned > 0)


                        ui.separator().classes('q-my-md')

                        # Manage Inventory Section
                        inventory_expansion = ui.expansion().classes('w-full bg-gray-800 rounded').props('icon=edit label="Manage Inventory"')
                        inventory_expansion.value = True
                        with inventory_expansion:
                            with ui.card().classes('w-full bg-transparent p-4 gap-4'):

                                # Row 1: Language & Set
                                with ui.row().classes('w-full gap-4'):
                                    ui.select(SUPPORTED_LANGUAGES, label='Language', value=input_state['language'],
                                              on_change=lambda e: [input_state.update({'language': e.value}), update_display_stats()]).classes('w-1/3')

                                    ui.select(set_options, label='Set Name', value=input_state['set_base_code'],
                                              on_change=lambda e: [input_state.update({'set_base_code': e.value}), update_display_stats()]).classes('col-grow')

                                # Row 2: Rarity & Condition & 1st Ed
                                with ui.row().classes('w-full gap-4'):
                                    ui.select(STANDARD_RARITIES, label='Rarity', value=input_state['rarity'],
                                              on_change=lambda e: [input_state.update({'rarity': e.value}), update_display_stats()]).classes('w-1/3')

                                    ui.select(['Mint', 'Near Mint', 'Played', 'Damaged'], label='Condition', value=input_state['condition'],
                                              on_change=lambda e: [input_state.update({'condition': e.value}), update_display_stats()]).classes('w-1/3')

                                    ui.checkbox('1st Edition', value=input_state['first_edition'],
                                                on_change=lambda e: [input_state.update({'first_edition': e.value}), update_display_stats()]).classes('my-auto')

                                # Row 3: Artwork & Quantity
                                with ui.row().classes('w-full gap-4 items-center'):
                                    if card.card_images and len(card.card_images) > 1:
                                        art_options = {img.id: f"Artwork {i+1} (ID: {img.id})" for i, img in enumerate(card.card_images)}
                                        ui.select(art_options, label='Artwork', value=input_state['image_id'],
                                                  on_change=lambda e: [input_state.update({'image_id': e.value}), update_image(), update_display_stats()]).classes('col-grow')

                                    ui.number('Quantity', min=0, value=input_state['quantity'],
                                              on_change=lambda e: input_state.update({'quantity': int(e.value or 0)})).classes('w-32')

                                # Row 4: Action Buttons
                                with ui.row().classes('w-full gap-4 justify-end q-mt-md'):

                                    async def handle_update(mode):
                                        # 1. Resolve Inputs
                                        base_code = input_state['set_base_code']
                                        sel_lang = input_state['language']
                                        sel_rarity = input_state['rarity']
                                        sel_img = input_state['image_id']
                                        sel_cond = input_state['condition']
                                        sel_first = input_state['first_edition']
                                        input_qty = int(input_state['quantity'])

                                        # 2. Check/Add DB Variant
                                        variant_exists = False
                                        for s in card.card_sets:
                                            s_img = s.image_id if s.image_id is not None else (card.card_images[0].id if card.card_images else None)
                                            if s.set_code == base_code and s.set_rarity == sel_rarity and s_img == sel_img:
                                                variant_exists = True
                                                break

                                        if not variant_exists:
                                            s_name = set_info_map[base_code].set_name if base_code in set_info_map else "Custom Set"
                                            await ygo_service.add_card_variant(
                                                card_id=card.id,
                                                set_name=s_name,
                                                set_code=base_code,
                                                set_rarity=sel_rarity,
                                                image_id=sel_img,
                                                language="en"
                                            )
                                            ui.notify(f"Added new variant: {base_code} / {sel_rarity}", type='positive')

                                        # 3. Calculate Target Quantity
                                        final_set_code = transform_set_code(base_code, sel_lang)

                                        current_owned = 0
                                        if self.state['current_collection']:
                                            for c in self.state['current_collection'].cards:
                                                c_img = c.metadata.image_id
                                                if c_img is None and card.card_images: c_img = card.card_images[0].id

                                                if (c.name == card.name and
                                                    c.metadata.set_code == final_set_code and
                                                    c.metadata.language == sel_lang and
                                                    c.metadata.rarity == sel_rarity and
                                                    c.metadata.condition == sel_cond and
                                                    c.metadata.first_edition == sel_first and
                                                    c_img == sel_img):
                                                    current_owned = c.quantity
                                                    break

                                        new_quantity = 0
                                        if mode == 'SET':
                                            new_quantity = input_qty
                                        elif mode == 'ADD':
                                            new_quantity = max(0, current_owned + input_qty)

                                        # 4. Save
                                        await self.save_card_change(
                                            card,
                                            final_set_code,
                                            sel_rarity,
                                            sel_lang,
                                            new_quantity,
                                            sel_cond,
                                            sel_first,
                                            image_id=sel_img
                                        )
                                        d.close()

                                    ui.button('SET', on_click=lambda: handle_update('SET')).props('color=warning text-color=dark')
                                    ui.button('ADD', on_click=lambda: handle_update('ADD')).props('color=secondary')

        except Exception as e:
            logger.error(f"ERROR in render_collectors_single_view: {e}", exc_info=True)

    def open_single_view_legacy(self, card: ApiCard, is_owned: bool = False, quantity: int = 0, initial_set: str = None, owned_languages: Set[str] = None):
        set_opts = [s.set_code for s in card.card_sets] if card.card_sets else ["N/A"]

        edit_state = {
            'set': initial_set if initial_set and initial_set in set_opts else (set_opts[0] if set_opts else "N/A"),
            'rarity': card.card_sets[0].set_rarity if card.card_sets else "Common",
            'language': self.state['language'].upper(),
            'quantity': quantity
        }

        if initial_set and card.card_sets:
            for s in card.card_sets:
                if s.set_code == initial_set:
                    edit_state['rarity'] = s.set_rarity
                    break

        with ui.dialog().props('maximized') as d, ui.card().classes('w-full h-full p-0 flex flex-row overflow-hidden'):
            ui.button(icon='close', on_click=d.close).props('flat round color=white').classes('absolute top-2 right-2 z-50')

            with ui.column().classes('w-1/3 h-full bg-black items-center justify-center p-8'):
                img_id = card.card_images[0].id if card.card_images else card.id
                high_res_url = card.card_images[0].image_url if card.card_images else None
                low_res_url = card.card_images[0].image_url_small if card.card_images else None

                image_element = ui.image().classes('max-h-full max-w-full object-contain shadow-2xl')
                self.setup_high_res_image_logic(img_id, high_res_url, low_res_url, image_element)

            with ui.column().classes('w-2/3 h-full p-8 scroll'):
                with ui.row().classes('w-full items-center justify-between'):
                    ui.label(card.name).classes('text-h3 font-bold')
                    if is_owned:
                        ui.badge(f"Owned: {quantity}", color='accent').classes('text-lg')

                # Owned Languages Display (Only if owned)
                if is_owned and owned_languages:
                     with ui.row().classes('w-full gap-2 q-mb-sm'):
                         ui.label('Owned Languages:').classes('font-bold text-gray-400')
                         for lang in sorted(list(owned_languages)):
                             ui.badge(lang, color='positive').props('outline')

                with ui.card().classes('w-full bg-gray-800 p-4 q-my-md border border-gray-700'):
                    ui.label('Manage Collection').classes('text-h6 q-mb-sm')
                    with ui.grid(columns=4).classes('w-full gap-4 items-end'):
                        ui.select(set_opts, label='Set').bind_value(edit_state, 'set').classes('w-full')
                        ui.input('Rarity').bind_value(edit_state, 'rarity').classes('w-full')
                        ui.select(['EN', 'DE', 'FR', 'IT', 'PT'], label='Language').bind_value(edit_state, 'language').classes('w-full')
                        ui.number('Quantity', min=0).bind_value(edit_state, 'quantity').classes('w-full')

                    async def on_legacy_update():
                        final_set_code = transform_set_code(edit_state['set'], edit_state['language'])
                        await self.save_card_change(
                            card,
                            final_set_code,
                            edit_state['rarity'],
                            edit_state['language'],
                            int(edit_state['quantity']),
                            "Near Mint", # Default Condition
                            False # Default First Edition
                        )
                        d.close()

                    ui.button('Update Collection', on_click=on_legacy_update) \
                        .classes('w-full q-mt-md').props('color=secondary')

                ui.separator().classes('q-my-md')
                with ui.grid(columns=4).classes('w-full gap-4 text-lg'):
                    def stat(label, value):
                        with ui.column():
                            ui.label(label).classes('text-grey text-sm uppercase')
                            ui.label(str(value)).classes('font-bold')
                    stat('Card Type', card.type)

                    race_label = 'Monster Type'
                    if 'Spell' in card.type or 'Trap' in card.type:
                        race_label = 'Property'

                    stat(race_label, card.race)
                    stat('Attribute', card.attribute)
                    stat('Level', card.level)
                    stat('ATK', card.atk)
                    stat('DEF', getattr(card, 'def_', '-'))
                    # Added details
                    stat('Category', next((p for p in ['Tuner', 'Spirit', 'Gemini', 'Toon', 'Union'] if p in card.type), '-'))

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
                        .on('click', lambda c=vm: self.open_single_view(c.api_card, c.is_owned, c.owned_quantity, owned_languages=c.owned_languages)):

                    img_src = card.card_images[0].image_url_small if card.card_images else None

                    # Logic: Use local if exists, else remote
                    img_id = card.card_images[0].id if card.card_images else card.id
                    if image_manager.image_exists(img_id):
                        img_src = f"/images/{img_id}.jpg"

                    with ui.element('div').classes('relative w-full aspect-[2/3] bg-black'):
                        if img_src: ui.image(img_src).classes('w-full h-full object-cover')
                        if vm.owned_quantity > 0:
                            ui.label(f"{vm.owned_quantity}").classes('absolute top-1 right-1 bg-accent text-dark font-bold px-2 rounded-full text-xs')

                        if card.level:
                             ui.label(f"Lv {card.level}").classes('absolute bottom-1 right-1 bg-black/70 text-white text-[10px] px-1 rounded')

                    with ui.column().classes('p-2 gap-0 w-full'):
                        ui.label(card.name).classes('text-xs font-bold truncate w-full')
                        ui.label(card.type).classes('text-[10px] text-gray-400 truncate w-full')

    def render_consolidated_list(self, items: List[CardViewModel]):
         headers = ['Image', 'Name', 'Type', 'Card Type', 'Owned']
         cols = '60px 4fr 2fr 2fr 1fr'
         with ui.column().classes('w-full gap-1'):
            with ui.grid(columns=cols).classes('w-full bg-gray-800 p-2 font-bold rounded'):
                for h in headers: ui.label(h)

            for vm in items:
                card = vm.api_card
                bg = 'bg-gray-900' if not vm.is_owned else 'bg-gray-800 border border-accent'
                img_src = card.card_images[0].image_url_small if card.card_images else None

                img_id = card.card_images[0].id if card.card_images else card.id
                if image_manager.image_exists(img_id):
                    img_src = f"/images/{img_id}.jpg"

                with ui.grid(columns=cols).classes(f'w-full {bg} p-1 items-center rounded hover:bg-gray-700 transition cursor-pointer') \
                        .on('click', lambda c=vm: self.open_single_view(c.api_card, c.is_owned, c.owned_quantity, owned_languages=c.owned_languages)):
                    ui.image(img_src).classes('h-10 w-8 object-cover')
                    with ui.column().classes('gap-0'):
                        ui.label(card.name).classes('truncate text-sm font-bold')
                        if card.level:
                            ui.label(f"Lv {card.level}").classes('text-[10px] text-gray-500')
                    ui.label(card.race).classes('text-xs text-gray-400')
                    ui.label(card.type).classes('text-xs text-gray-400')
                    if vm.is_owned:
                         ui.badge(str(vm.owned_quantity), color='accent').classes('text-dark')
                    else:
                         ui.label('-').classes('text-gray-600')

    def render_collectors_list(self, items: List[CollectorRow]):
        flag_map = {'EN': '🇬🇧', 'DE': '🇩🇪', 'FR': '🇫🇷', 'IT': '🇮🇹', 'ES': '🇪🇸', 'PT': '🇵🇹', 'JP': '🇯🇵', 'KR': '🇰🇷', 'CN': '🇨🇳'}
        cond_map = {'Mint': 'MT', 'Near Mint': 'NM', 'Played': 'PL', 'Damaged': 'DM'}

        headers = ['Image', 'Name', 'Set', 'Rarity', 'Cond', '1st', 'Lang', 'Price', 'Owned']
        cols = '60px 4fr 2fr 1.5fr 0.8fr 0.5fr 0.5fr 1fr 0.8fr'

        with ui.column().classes('w-full gap-1'):
            with ui.grid(columns=cols).classes('w-full bg-gray-800 p-2 font-bold rounded'):
                for h in headers: ui.label(h)

            for item in items:
                bg = 'bg-gray-900' if not item.is_owned else 'bg-gray-800 border border-accent'

                img_src = item.image_url
                img_id = item.image_id if item.image_id else (item.api_card.card_images[0].id if item.api_card.card_images else item.api_card.id)
                if image_manager.image_exists(img_id):
                    img_src = f"/images/{img_id}.jpg"

                with ui.grid(columns=cols).classes(f'w-full {bg} p-1 items-center rounded hover:bg-gray-700 transition cursor-pointer') \
                        .on('click', lambda c=item: self.open_single_view(c.api_card, c.is_owned, c.owned_count, initial_set=c.set_code, rarity=c.rarity, set_name=c.set_name, language=c.language, condition=c.condition, first_edition=c.first_edition, image_url=c.image_url, image_id=c.image_id, set_price=c.price)):
                    ui.image(img_src).classes('h-10 w-8 object-cover')
                    ui.label(item.api_card.name).classes('truncate text-sm font-bold')
                    with ui.column().classes('gap-0'):
                        ui.label(item.set_code).classes('text-xs font-mono font-bold text-yellow-500')
                        ui.label(item.set_name).classes('text-xs text-gray-400 truncate')
                    ui.label(item.rarity).classes('text-xs')

                    ui.label(cond_map.get(item.condition, item.condition[:2].upper())).classes('text-xs font-bold text-yellow-500')
                    ui.label("1st" if item.first_edition else "").classes('text-xs font-bold text-orange-400')
                    ui.label(flag_map.get(item.language, item.language)).classes('text-lg')

                    ui.label(f"${item.price:.2f}").classes('text-sm text-green-400')
                    if item.is_owned:
                         ui.badge(str(item.owned_count), color='accent').classes('text-dark')
                    else:
                         ui.label('-').classes('text-gray-600')

    def render_collectors_grid(self, items: List[CollectorRow]):
        flag_map = {'EN': '🇬🇧', 'DE': '🇩🇪', 'FR': '🇫🇷', 'IT': '🇮🇹', 'ES': '🇪🇸', 'PT': '🇵🇹', 'JP': '🇯🇵', 'KR': '🇰🇷', 'CN': '🇨🇳'}
        cond_map = {'Mint': 'MT', 'Near Mint': 'NM', 'Played': 'PL', 'Damaged': 'DM'}

        with ui.grid(columns='repeat(auto-fill, minmax(160px, 1fr))').classes('w-full gap-4'):
            for item in items:
                opacity = "opacity-100" if item.is_owned else "opacity-60 grayscale"
                border = "border-accent" if item.is_owned else "border-gray-700"

                img_src = item.image_url
                img_id = item.image_id if item.image_id else (item.api_card.card_images[0].id if item.api_card.card_images else item.api_card.id)
                if image_manager.image_exists(img_id):
                    img_src = f"/images/{img_id}.jpg"

                with ui.card().classes(f'collection-card w-full p-0 cursor-pointer {opacity} border {border} hover:scale-105 transition-transform') \
                        .on('click', lambda c=item: self.open_single_view(c.api_card, c.is_owned, c.owned_count, initial_set=c.set_code, rarity=c.rarity, set_name=c.set_name, language=c.language, condition=c.condition, first_edition=c.first_edition, image_url=c.image_url, image_id=c.image_id, set_price=c.price)):

                    with ui.element('div').classes('relative w-full aspect-[2/3] bg-black'):
                        if img_src: ui.image(img_src).classes('w-full h-full object-cover')

                        # Top Left: Flag
                        flag = flag_map.get(item.language, item.language)
                        ui.label(flag).classes('absolute top-1 left-1 text-lg shadow-black drop-shadow-md bg-black/30 rounded px-1')

                        # Top Right: Count
                        if item.is_owned:
                             ui.label(f"{item.owned_count}").classes('absolute top-1 right-1 bg-accent text-dark font-bold px-2 rounded-full text-xs')

                        # Bottom Left: Condition + Edition
                        cond_short = cond_map.get(item.condition, item.condition[:2].upper())
                        ed_text = "1st" if item.first_edition else ""

                        with ui.row().classes('absolute bottom-0 left-0 bg-black/80 text-white text-[10px] px-1 gap-1 items-center rounded-tr'):
                            ui.label(cond_short).classes('font-bold text-yellow-500')
                            if ed_text:
                                ui.label(ed_text).classes('font-bold text-orange-400')

                        # Bottom Right: Set Code
                        ui.label(item.set_code).classes('absolute bottom-0 right-0 bg-black/80 text-white text-[10px] px-1 font-mono rounded-tl')

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
                with ui.button(icon='chevron_left', on_click=lambda: self.change_page(-1)).props('flat dense'):
                    ui.tooltip('Go to previous page')
                with ui.number(value=self.state['page'], min=1, max=self.state['total_pages'],
                          on_change=lambda e: self.set_page(e.value)).classes('w-20').props('dense borderless input-class="text-center"'):
                    ui.tooltip('Current page number')
                ui.label(f"/ {max(1, self.state['total_pages'])}")
                with ui.button(icon='chevron_right', on_click=lambda: self.change_page(1)).props('flat dense'):
                    ui.tooltip('Go to next page')

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

    async def change_page(self, delta):
        new_page = self.state['page'] + delta
        if 1 <= new_page <= self.state['total_pages']:
            self.state['page'] = new_page
            await self.prepare_current_page_images()
            self.content_area.refresh()

    async def set_page(self, val):
        if val and 1 <= val <= self.state['total_pages']:
            self.state['page'] = int(val)
            await self.prepare_current_page_images()
            self.content_area.refresh()

    def build_ui(self):
        # Drawer (Filter)
        filter_dialog = ui.dialog().props('position=right')
        with filter_dialog, ui.card().classes('h-full w-96 bg-gray-900 border-l border-gray-700 p-0 flex flex-col'):
             with ui.scroll_area().classes('flex-grow w-full'):
                 with ui.column().classes('w-full p-4 gap-4'):
                    ui.label('Filters').classes('text-h6')

                    # Set Filter (Dropdown)
                    self.set_selector = ui.select(self.state['available_sets'], label='Set', with_input=True, clearable=True,
                              on_change=self.apply_filters).bind_value(self.state, 'filter_set').classes('w-full').props('use-input fill-input input-debounce=0')

                    # Rarity (Dropdown with common rarities)
                    common_rarities = ["Common", "Rare", "Super Rare", "Ultra Rare", "Secret Rare", "Ghost Rare", "Ultimate Rare", "Starlight Rare", "Collector's Rare"]
                    ui.select(common_rarities, label='Rarity', with_input=True, clearable=True, on_change=self.apply_filters).bind_value(self.state, 'filter_rarity').classes('w-full')

                    # Attribute
                    ui.select(['DARK', 'LIGHT', 'EARTH', 'WIND', 'FIRE', 'WATER', 'DIVINE'],
                              label='Attribute', clearable=True, on_change=self.apply_filters).bind_value(self.state, 'filter_attr').classes('w-full')

                    # Card Types (was Type)
                    self.ctype_selector = ui.select(self.state['available_card_types'], label='Card Types', with_input=True, clearable=True,
                                                    on_change=self.apply_filters).bind_value(self.state, 'filter_card_type').classes('w-full')

                    # Monster Type (Race)
                    self.m_race_selector = ui.select(self.state['available_monster_races'], label='Monster Type', with_input=True, clearable=True,
                                                    on_change=self.apply_filters).bind_value(self.state, 'filter_monster_race').classes('w-full')

                    # Spell/Trap Type (Race)
                    self.st_race_selector = ui.select(self.state['available_st_races'], label='Spell/Trap Type', with_input=True, clearable=True,
                                                    on_change=self.apply_filters).bind_value(self.state, 'filter_st_race').classes('w-full')

                    # Archetype
                    self.archetype_selector = ui.select(self.state['available_archetypes'], label='Archetype', with_input=True, clearable=True,
                                                    on_change=self.apply_filters).bind_value(self.state, 'filter_archetype').classes('w-full')

                    # Monster Category
                    categories = ['Effect', 'Normal', 'Synchro', 'Xyz', 'Ritual', 'Fusion', 'Link', 'Pendulum', 'Toon', 'Spirit', 'Union', 'Gemini', 'Flip']
                    ui.select(categories, label='Monster Category', multiple=True, clearable=True, on_change=self.apply_filters).bind_value(self.state, 'filter_monster_category').classes('w-full').props('use-chips')

                    ui.number('Level/Rank', min=0, max=13, on_change=self.apply_filters).bind_value(self.state, 'filter_level').classes('w-full')

                    # Range Helper
                    def setup_range_filter(label, min_key, max_key, min_limit, max_limit, step=1, name=''):
                        ui.label(label).classes('text-sm text-gray-400')
                        with ui.row().classes('w-full items-center gap-2'):
                            min_input = ui.number(min=min_limit, max=max_limit, step=step).classes('w-16').props('dense borderless')
                            max_input = ui.number(min=min_limit, max=max_limit, step=step).classes('w-16').props('dense borderless')

                            slider = ui.range(min=min_limit, max=max_limit, step=step).classes('col-grow')

                            async def on_slider_change(e):
                                val = e.args[0] if isinstance(e.args[0], dict) else e.value
                                self.state[min_key] = val['min']
                                self.state[max_key] = val['max']
                                min_input.value = val['min']
                                max_input.value = val['max']
                                await self.apply_filters()

                            async def on_min_input_change(e):
                                try:
                                    val = float(e.value) if e.value is not None else min_limit
                                except: val = min_limit
                                self.state[min_key] = val
                                slider.value = {'min': val, 'max': self.state[max_key]}
                                await self.apply_filters()

                            async def on_max_input_change(e):
                                try:
                                    val = float(e.value) if e.value is not None else max_limit
                                except: val = max_limit
                                self.state[max_key] = val
                                slider.value = {'min': self.state[min_key], 'max': val}
                                await self.apply_filters()

                            slider.on('update:model-value', on_slider_change)
                            # Initial values
                            slider.value = {'min': self.state[min_key], 'max': self.state[max_key]}

                            min_input.on('change', on_min_input_change)
                            min_input.value = self.state[min_key]

                            max_input.on('change', on_max_input_change)
                            max_input.value = self.state[max_key]

                            if name:
                                self.filter_inputs[name] = (slider, min_input, max_input)


                    setup_range_filter('ATK', 'filter_atk_min', 'filter_atk_max', 0, 5000, 50, 'atk')
                    setup_range_filter('DEF', 'filter_def_min', 'filter_def_max', 0, 5000, 50, 'def')

                    ui.separator()
                    ui.label('Ownership & Price').classes('text-h6')

                    setup_range_filter('Ownership Quantity Range', 'filter_ownership_min', 'filter_ownership_max', 0, self.state['max_owned_quantity'], 1, 'ownership')
                    setup_range_filter('Price Range ($)', 'filter_price_min', 'filter_price_max', 0, 1000, 1, 'price')

                    ui.select(['EN', 'DE', 'FR', 'IT', 'PT'], label='Owned Language', clearable=True,
                              on_change=self.apply_filters).bind_value(self.state, 'filter_owned_lang').classes('w-full')

             # Footer with Reset Button
             with ui.column().classes('p-4 border-t border-gray-700 bg-gray-900 w-full'):
                 with ui.button('Reset All Filters', on_click=self.reset_filters).classes('w-full').props('color=red-9 outline'):
                     ui.tooltip('Clear all active filters and reset to default')

        # Toolbar
        with ui.row().classes('w-full items-center gap-4 q-mb-md p-4 bg-gray-900 rounded-lg border border-gray-800'):
            ui.label('Gallery').classes('text-h5')

            files = persistence.list_collections()
            with ui.select(files, value=self.state['selected_file'], label='Collection',
                      on_change=lambda e: [self.state.update({'selected_file': e.value}), self.load_data()]).classes('w-40'):
                ui.tooltip('Select which collection file to view')

            async def on_search(e):
                self.state['search_text'] = e.value
                await self.apply_filters()

            with ui.input(placeholder='Search...', on_change=on_search) \
                .props('debounce=300 icon=search').classes('w-64'):
                ui.tooltip('Search by card name, type, or description')

            async def on_sort_change(e):
                self.state['sort_by'] = e.value
                await self.apply_filters()

            with ui.select(['Name', 'ATK', 'DEF', 'Level', 'Newest', 'Price'], value=self.state['sort_by'], label='Sort',
                      on_change=on_sort_change).classes('w-32'):
                ui.tooltip('Choose how to sort the displayed cards')

            async def on_owned_switch(e):
                self.state['only_owned'] = e.value
                await self.apply_filters()

            with ui.row().classes('items-center'):
                with ui.switch('Owned', on_change=on_owned_switch):
                    ui.tooltip('Toggle to show only cards you own')

            ui.separator().props('vertical')

            with ui.button_group():
                with ui.button('Consolidated', on_click=lambda: self.switch_scope('consolidated')) \
                    .props(f'flat={"collectors" in self.state["view_scope"]} color=accent'):
                    ui.tooltip('View consolidated gameplay statistics (totals per card)')
                with ui.button('Collectors', on_click=lambda: self.switch_scope('collectors')) \
                    .props(f'flat={"consolidated" in self.state["view_scope"]} color=accent'):
                    ui.tooltip('View detailed market and collection data (separate entries per set/rarity)')

            with ui.button_group():
                with ui.button(icon='grid_view', on_click=lambda: [self.state.update({'view_mode': 'grid'}), self.content_area.refresh()]) \
                    .props(f'flat={"list" == self.state["view_mode"]} color=accent'):
                    ui.tooltip('Show cards in a grid layout')
                with ui.button(icon='list', on_click=lambda: [self.state.update({'view_mode': 'list'}), self.content_area.refresh()]) \
                    .props(f'flat={"grid" == self.state["view_mode"]} color=accent'):
                    ui.tooltip('Show cards in a list layout')

            ui.space()
            with ui.button(icon='filter_list', on_click=filter_dialog.open).props('color=primary size=lg'):
                ui.tooltip('Open advanced filters')

        self.content_area()
        ui.timer(0.1, self.load_data, once=True)

def collection_page():
    page = CollectionPage()
    page.build_ui()
