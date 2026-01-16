from nicegui import ui, run
from src.core.persistence import persistence
from src.core.models import Collection, CollectionCard, CollectionVariant, CollectionEntry, Card, CardMetadata
from src.services.ygo_api import ygo_service, ApiCard
from src.services.image_manager import image_manager
from src.core.config import config_manager
from src.core.utils import transform_set_code, generate_variant_id
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Set, Callable
import asyncio
import traceback
import re
import logging
import os

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

def build_consolidated_vms(api_cards: List[ApiCard], owned_details: Dict[int, CollectionCard]) -> List[CardViewModel]:
    vms = []
    for card in api_cards:
        c_card = owned_details.get(card.id)
        qty = c_card.total_quantity if c_card else 0
        owned_langs = set()
        if c_card:
            for v in c_card.variants:
                for e in v.entries:
                    owned_langs.add(e.language)

        lowest = 0.0
        prices = []
        if card.card_prices:
            p = card.card_prices[0]
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

def build_collector_rows(api_cards: List[ApiCard], owned_details: Dict[int, CollectionCard], language: str) -> List[CollectorRow]:
    rows = []

    for card in api_cards:
        c_card = owned_details.get(card.id)

        owned_variants = {v.variant_id: v for v in c_card.variants} if c_card else {}
        processed_variant_ids = set()

        img_url = card.card_images[0].image_url_small if card.card_images else None
        default_image_id = card.card_images[0].id if card.card_images else None

        # 1. Process API Sets
        if card.card_sets:
            for cset in card.card_sets:
                set_name = cset.set_name
                set_code = cset.set_code
                rarity = cset.set_rarity
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

                target_variant_id = cset.variant_id
                matched_cv = owned_variants.get(target_variant_id)

                if matched_cv:
                    processed_variant_ids.add(target_variant_id)
                    groups = {}
                    for entry in matched_cv.entries:
                        k = (entry.language, entry.condition, entry.first_edition)
                        groups[k] = groups.get(k, 0) + entry.quantity

                    for (lang, cond, first), qty in groups.items():
                        rows.append(CollectorRow(
                            api_card=card,
                            set_code=set_code,
                            set_name=set_name,
                            rarity=rarity,
                            price=price,
                            image_url=row_img_url,
                            owned_count=qty,
                            is_owned=True,
                            language=lang,
                            condition=cond,
                            first_edition=first,
                            image_id=cset.image_id
                        ))
                else:
                    base_lang = "EN"
                    if "-" in set_code:
                        parts = set_code.split('-')
                        if len(parts) > 1:
                            reg_match = re.match(r'^([A-Za-z]+)', parts[1])
                            if reg_match:
                                r = reg_match.group(1).upper()
                                if r in ['EN', 'DE', 'FR', 'IT', 'PT', 'ES', 'JP']:
                                    base_lang = r

                    rows.append(CollectorRow(
                        api_card=card,
                        set_code=set_code,
                        set_name=set_name,
                        rarity=rarity,
                        price=price,
                        image_url=row_img_url,
                        owned_count=0,
                        is_owned=False,
                        language=base_lang,
                        condition="Near Mint",
                        first_edition=False,
                        image_id=cset.image_id
                    ))

        # 2. Handle Custom/Unknown Variants
        for var_id, cv in owned_variants.items():
            if var_id not in processed_variant_ids:
                groups = {}
                for entry in cv.entries:
                    k = (entry.language, entry.condition, entry.first_edition)
                    groups[k] = groups.get(k, 0) + entry.quantity

                row_img_url = img_url
                if cv.image_id:
                     for img in card.card_images:
                         if img.id == cv.image_id:
                             row_img_url = img.image_url_small
                             break

                for (lang, cond, first), qty in groups.items():
                     rows.append(CollectorRow(
                        api_card=card,
                        set_code=cv.set_code,
                        set_name="Custom / Unmatched",
                        rarity=cv.rarity,
                        price=0.0,
                        image_url=row_img_url,
                        owned_count=qty,
                        is_owned=True,
                        language=lang,
                        condition=cond,
                        first_edition=first,
                        image_id=cv.image_id
                    ))

        # 3. Fallback if no sets in API and no owned variants
        if not card.card_sets and not owned_variants:
             rows.append(CollectorRow(
                    api_card=card,
                    set_code="N/A",
                    set_name="No Set Info",
                    rarity="Common",
                    price=0.0,
                    image_url=img_url,
                    owned_count=0,
                    is_owned=False,
                    language="EN",
                    condition="Near Mint",
                    first_edition=False,
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
            'max_owned_quantity': 100,

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
            'filter_ownership_max': 100,
            'filter_price_min': 0.0,
            'filter_price_max': 1000.0,

            'filter_owned_lang': '',
            'only_owned': False,
            'language': config_manager.get_language(),
            'sort_by': 'Name',

            'view_scope': 'consolidated',
            'view_mode': 'grid',
            'page': 1,
            'page_size': 48,
            'total_pages': 1,
        }

        files = persistence.list_collections()
        self.state['selected_file'] = files[0] if files else None
        self.filter_inputs = {}

    async def load_data(self):
        logger.info(f"Loading data... (Language: {self.state['language']})")

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

        collection = None
        if self.state['selected_file']:
            try:
                collection = await run.io_bound(persistence.load_collection, self.state['selected_file'])
            except Exception as e:
                logger.warning(f"Error loading collection {self.state['selected_file']}: {e}")
                ui.notify(f"Error loading collection: {e}", type='warning')

        self.state['current_collection'] = collection

        owned_details = {}
        max_qty = 0
        if collection:
            for c in collection.cards:
                owned_details[c.card_id] = c
                max_qty = max(max_qty, c.total_quantity)

        self.state['max_owned_quantity'] = max(100, max_qty)

        self.state['cards_consolidated'] = await run.io_bound(build_consolidated_vms, api_cards, owned_details)

        self.state['cards_collectors'] = []
        if self.state['view_scope'] == 'collectors':
             self.state['cards_collectors'] = await run.io_bound(build_collector_rows, api_cards, owned_details, self.state['language'])

        await self.apply_filters()
        self.update_filter_ui()
        logger.info(f"Data loaded. Items: {len(self.state['cards_consolidated'])}")

    def update_filter_ui(self):
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
            card = item.api_card
            image_id = None
            url = None

            if self.state['view_scope'] == 'collectors':
                image_id = item.image_id
                if not image_id and card.card_images:
                     image_id = card.card_images[0].id
                elif not image_id:
                     image_id = card.id
                url = item.image_url
            else:
                if card.card_images:
                    image_id = card.card_images[0].id
                    url = card.card_images[0].image_url_small
                else:
                    image_id = card.id

            if image_id and url:
                url_map[image_id] = url

        if url_map:
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

        txt = self.state['search_text'].lower()
        if txt:
            res = [c for c in res if txt in c.api_card.name.lower() or
                   txt in c.api_card.type.lower() or
                   txt in c.api_card.desc.lower()]

        if self.state['only_owned']:
            res = [c for c in res if c.is_owned]

        min_q = self.state['filter_ownership_min']
        max_q = self.state['filter_ownership_max']

        def get_qty(item):
            if hasattr(item, 'owned_quantity'): return item.owned_quantity
            return getattr(item, 'owned_count', 0)

        res = [c for c in res if min_q <= get_qty(c) <= max_q]

        p_min = self.state['filter_price_min']
        p_max = self.state['filter_price_max']

        def get_price(item):
             if hasattr(item, 'lowest_price'): return item.lowest_price
             return getattr(item, 'price', 0.0)

        res = [c for c in res if p_min <= get_price(c) <= p_max]

        if self.state['filter_owned_lang']:
            target_lang = self.state['filter_owned_lang']
            if self.state['view_scope'] == 'consolidated':
                res = [c for c in res if target_lang in c.owned_languages]
            else:
                 res = [c for c in res if c.language == target_lang]

        if self.state['filter_attr']:
            res = [c for c in res if c.api_card.attribute == self.state['filter_attr']]

        if self.state['filter_card_type']:
             res = [c for c in res if self.state['filter_card_type'] in c.api_card.type]

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

                if self.state['view_scope'] == 'consolidated':
                    def match_set_strict(c):
                        if not c.api_card.card_sets: return False
                        for cs in c.api_card.card_sets:
                             parts = cs.set_code.split('-')
                             c_prefix = parts[0].lower() if parts else cs.set_code.lower()
                             if c_prefix == target_prefix:
                                 return True
                        return False
                    res = [c for c in res if match_set_strict(c)]
                else:
                    def match_row_strict(c):
                        parts = c.set_code.split('-')
                        c_prefix = parts[0].lower() if parts else c.set_code.lower()
                        return c_prefix == target_prefix

                    res = [c for c in res if match_row_strict(c)]

            else:
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

        if self.state['filter_rarity']:
            r = self.state['filter_rarity'].lower()
            if self.state['view_scope'] == 'consolidated':
                 res = [c for c in res if c.api_card.card_sets and any(r == cs.set_rarity.lower() for cs in c.api_card.card_sets)]
            else:
                 res = [c for c in res if r == c.rarity.lower()]

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

    async def save_card_change(self, api_card: ApiCard, set_code, rarity, language, quantity, condition, first_edition, image_id: Optional[int] = None, variant_id: Optional[str] = None):
        if not self.state['current_collection']:
            ui.notify('No collection selected.', type='negative')
            return

        col = self.state['current_collection']

        target_card = None
        for c in col.cards:
            if c.card_id == api_card.id:
                target_card = c
                break

        if not target_card:
            if quantity <= 0: return
            target_card = CollectionCard(card_id=api_card.id, name=api_card.name)
            col.cards.append(target_card)

        target_variant_id = variant_id
        if not target_variant_id:
             target_variant_id = generate_variant_id(api_card.id, set_code, rarity, image_id)

        target_variant = None
        for v in target_card.variants:
            if v.variant_id == target_variant_id:
                target_variant = v
                break

        if not target_variant:
             if quantity > 0:
                 target_variant = CollectionVariant(
                     variant_id=target_variant_id,
                     set_code=set_code,
                     rarity=rarity,
                     image_id=image_id
                 )
                 target_card.variants.append(target_variant)

        if target_variant:
            target_entry = None
            for e in target_variant.entries:
                if e.condition == condition and e.language == language and e.first_edition == first_edition:
                    target_entry = e
                    break

            if quantity > 0:
                if target_entry:
                    target_entry.quantity = quantity
                else:
                    target_variant.entries.append(CollectionEntry(
                        condition=condition,
                        language=language,
                        first_edition=first_edition,
                        quantity=quantity
                    ))
            else:
                if target_entry:
                    target_variant.entries.remove(target_entry)

            if not target_variant.entries:
                target_card.variants.remove(target_variant)

        if not target_card.variants:
            col.cards.remove(target_card)

        try:
            await run.io_bound(persistence.save_collection, col, self.state['selected_file'])
            logger.info(f"Collection saved: {self.state['selected_file']}")
            ui.notify('Collection saved.', type='positive')
            await self.load_data()
        except Exception as e:
            logger.error(f"Error saving collection: {e}")
            ui.notify(f"Error saving: {e}", type='negative')

    def setup_high_res_image_logic(self, img_id: int, high_res_remote_url: str, low_res_url: str, image_element: ui.image, current_id_check: Callable[[], bool] = None):
        """
        Sets the source of the image element.
        Prioritizes local high-res > remote high-res.
        If local high-res is missing but remote high-res is available, downloads it in background.
        """
        if not img_id:
             image_element.source = high_res_remote_url or low_res_url
             return

        # Check local high-res
        if image_manager.image_exists(img_id, high_res=True):
             image_element.source = f"/images/{img_id}_high.jpg"
             image_element.update()
             return

        # Use remote high-res directly, fallback to low-res only if high-res is missing
        image_element.source = high_res_remote_url if high_res_remote_url else low_res_url
        image_element.update()

        # Background download high-res
        if high_res_remote_url:
             async def download_task():
                 await image_manager.ensure_image(img_id, high_res_remote_url, high_res=True)

             # Run in background
             asyncio.create_task(download_task())

    def open_single_view(self, card: ApiCard, is_owned: bool = False, quantity: int = 0, initial_set: str = None, owned_languages: Set[str] = None, rarity: str = None, set_name: str = None, language: str = None, condition: str = "Near Mint", first_edition: bool = False, image_url: str = None, image_id: int = None, set_price: float = 0.0):
        if self.state['view_scope'] == 'consolidated':
            owned_breakdown = {}
            total_owned = 0
            if self.state['current_collection']:
                 for c in self.state['current_collection'].cards:
                     if c.card_id == card.id:
                         for v in c.variants:
                             for e in v.entries:
                                 owned_breakdown[e.language] = owned_breakdown.get(e.language, 0) + e.quantity
                                 total_owned += e.quantity
                         break

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
                    with ui.column().classes('w-1/3 min-w-[300px] h-full bg-black items-center justify-center p-8 shrink-0'):
                        img_id = card.card_images[0].id if card.card_images else card.id
                        high_res_url = card.card_images[0].image_url if card.card_images else None
                        low_res_url = card.card_images[0].image_url_small if card.card_images else None

                        image_element = ui.image().classes('max-h-full max-w-full object-contain shadow-2xl')
                        self.setup_high_res_image_logic(img_id, high_res_url, low_res_url, image_element)

                    with ui.column().classes('col h-full bg-gray-900 text-white p-8 scroll-y-auto'):
                        with ui.row().classes('w-full items-center justify-between'):
                            ui.label(card.name).classes('text-4xl font-bold text-white select-text')
                        if total_owned > 0:
                            ui.badge(f"Total Owned: {total_owned}", color='accent').classes('text-lg')

                        ui.separator().classes('q-my-md bg-gray-700')

                        with ui.grid(columns=4).classes('w-full gap-4 text-lg'):
                            def stat(label, value):
                                with ui.column():
                                    ui.label(label).classes('text-gray-400 text-sm uppercase select-none font-bold')
                                    ui.label(str(value) if value is not None else '-').classes('font-bold select-text text-xl')

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
                                    val = card.def_
                                    stat('DEF', val if val is not None else '-')
                            else:
                                stat('Property', card.race)
                                stat('Archetype', card.archetype or '-')

                        if card.typeline:
                             ui.label(' / '.join(card.typeline)).classes('text-gray-400 text-sm mt-2 select-text')

                        ui.separator().classes('q-my-md')
                        ui.label('Effect').classes('text-h6 q-mb-sm select-none text-accent')
                        ui.markdown(card.desc).classes('text-gray-300 leading-relaxed text-lg select-text')
                        ui.separator().classes('q-my-md')

                        ui.label('Collection Status').classes('text-h6 q-mb-sm select-none text-accent')
                        if owned_breakdown:
                            with ui.row().classes('gap-2'):
                                for lang, count in owned_breakdown.items():
                                    with ui.chip(icon='layers').props('color=secondary text-color=white'):
                                        ui.label(f"{lang}: {count}").classes('select-text')
                        else:
                            ui.label('Not in collection').classes('text-gray-500 italic')
        except Exception as e:
            logger.error(f"ERROR in render_consolidated_single_view: {e}", exc_info=True)

    def render_collectors_single_view(self, card: ApiCard, owned_count: int, set_code: str, rarity: str, set_name: str, language: str, condition: str, first_edition: bool, image_url: str = None, image_id: int = None, set_price: float = 0.0):
        try:
            if image_id is None:
                image_id = card.card_images[0].id if card.card_images else None

            set_options = {}
            set_info_map = {}

            if card.card_sets:
                for s in card.card_sets:
                    code = s.set_code
                    if code not in set_options:
                        set_options[code] = f"{s.set_name} ({code})"
                        set_info_map[code] = s
            else:
                set_options["Custom"] = "Custom Set"

            initial_base_code = None
            if set_code in set_options:
                initial_base_code = set_code
            else:
                found = False
                for base in set_options.keys():
                    if transform_set_code(base, language) == set_code:
                        initial_base_code = base
                        found = True
                        break
                if not found:
                     initial_base_code = list(set_options.keys())[0] if set_options else "Custom"

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

                    with ui.column().classes('col h-full bg-gray-900 text-white p-8 scroll-y-auto'):
                        with ui.row().classes('w-full items-center justify-between'):
                            ui.label(card.name).classes('text-h3 font-bold text-white select-text')

                        owned_badge = ui.badge(f"Owned: {owned_count}", color='accent').classes('text-lg')
                        if owned_count == 0:
                            owned_badge.set_visibility(False)

                        ui.separator().classes('q-my-md bg-gray-700')

                        # Card Details Grid
                        with ui.grid(columns=3).classes('w-full gap-4 text-lg'):
                             def info_label(title, initial_value, color='white'):
                                 with ui.column().classes('gap-0'):
                                     ui.label(title).classes('text-gray-400 text-xs uppercase font-bold select-none')
                                     l = ui.label(str(initial_value)).classes(f'text-{color} font-bold select-text text-lg')
                                 return l

                             lbl_set_name = info_label('Set Name', set_name or 'N/A')
                             lbl_set_code = info_label('Set Code', set_code, 'yellow-500')
                             lbl_rarity = info_label('Rarity', rarity)

                             lbl_lang = info_label('Language', language)
                             lbl_cond = info_label('Condition', condition)
                             lbl_edition = info_label('Edition', "1st Edition" if first_edition else "Unlimited")

                        ui.separator().classes('q-my-md bg-gray-700')

                        # Market Prices
                        ui.label('Market Prices').classes('text-h6 q-mb-sm select-none text-accent')
                        with ui.grid(columns=4).classes('w-full gap-4'):
                             tcg_price = '-'
                             cm_price = '-'
                             csi_price = '-'
                             if card.card_prices:
                                 p = card.card_prices[0]
                                 if p.tcgplayer_price: tcg_price = f"${p.tcgplayer_price}"
                                 if p.cardmarket_price: cm_price = f"€{p.cardmarket_price}"
                                 if p.coolstuffinc_price: csi_price = f"${p.coolstuffinc_price}"

                             info_label('TCGPlayer', tcg_price, 'green-400')
                             info_label('CardMarket', cm_price, 'blue-400')
                             info_label('CoolStuffInc', csi_price, 'orange-400')

                             lbl_set_price = info_label('Set Price', f"${set_price:.2f}" if set_price else "-", 'purple-400')

                        def update_display_stats():
                            base_code = input_state['set_base_code']
                            s_name = "N/A"
                            s_price = None

                            if base_code in set_info_map:
                                s_obj = set_info_map[base_code]
                                s_name = s_obj.set_name
                                matched_set = None
                                for s in card.card_sets:
                                    s_img = s.image_id if s.image_id is not None else (card.card_images[0].id if card.card_images else None)
                                    if s.set_code == base_code and s.set_rarity == input_state['rarity'] and s_img == input_state['image_id']:
                                        matched_set = s
                                        break
                                if matched_set and matched_set.set_price:
                                    try: s_price = float(matched_set.set_price)
                                    except: pass

                            lbl_set_name.text = s_name
                            final_code = transform_set_code(base_code, input_state['language'])
                            lbl_set_code.text = final_code
                            lbl_rarity.text = input_state['rarity']
                            lbl_lang.text = input_state['language']
                            lbl_cond.text = input_state['condition']
                            lbl_edition.text = "1st Edition" if input_state['first_edition'] else "Unlimited"

                            lbl_set_price.text = f"${s_price:.2f}" if s_price is not None else "-"

                            cur_owned = 0
                            if self.state['current_collection']:
                                for c in self.state['current_collection'].cards:
                                    if c.card_id == card.id:
                                         for v in c.variants:
                                             if v.set_code == final_code and v.rarity == input_state['rarity'] and v.image_id == input_state['image_id']:
                                                 for e in v.entries:
                                                     if e.language == input_state['language'] and e.condition == input_state['condition'] and e.first_edition == input_state['first_edition']:
                                                         cur_owned = e.quantity
                                                         break
                                                 break
                                         break

                            owned_badge.text = f"Owned: {cur_owned}"
                            owned_badge.set_visibility(cur_owned > 0)


                        ui.separator().classes('q-my-md')

                        inventory_expansion = ui.expansion().classes('w-full bg-gray-800 rounded').props('icon=edit label="Manage Inventory"')
                        inventory_expansion.value = True
                        with inventory_expansion:
                            with ui.card().classes('w-full bg-transparent p-4 gap-4'):
                                with ui.row().classes('w-full gap-4'):
                                    ui.select(SUPPORTED_LANGUAGES, label='Language', value=input_state['language'],
                                              on_change=lambda e: [input_state.update({'language': e.value}), update_display_stats()]).classes('w-1/3')
                                    ui.select(set_options, label='Set Name', value=input_state['set_base_code'],
                                              on_change=lambda e: [input_state.update({'set_base_code': e.value}), update_display_stats()]).classes('col-grow')

                                with ui.row().classes('w-full gap-4'):
                                    ui.select(STANDARD_RARITIES, label='Rarity', value=input_state['rarity'],
                                              on_change=lambda e: [input_state.update({'rarity': e.value}), update_display_stats()]).classes('w-1/3')
                                    ui.select(['Mint', 'Near Mint', 'Played', 'Damaged'], label='Condition', value=input_state['condition'],
                                              on_change=lambda e: [input_state.update({'condition': e.value}), update_display_stats()]).classes('w-1/3')
                                    ui.checkbox('1st Edition', value=input_state['first_edition'],
                                                on_change=lambda e: [input_state.update({'first_edition': e.value}), update_display_stats()]).classes('my-auto')

                                with ui.row().classes('w-full gap-4 items-center'):
                                    if card.card_images and len(card.card_images) > 1:
                                        art_options = {img.id: f"Artwork {i+1} (ID: {img.id})" for i, img in enumerate(card.card_images)}
                                        ui.select(art_options, label='Artwork', value=input_state['image_id'],
                                                  on_change=lambda e: [input_state.update({'image_id': e.value}), update_image(), update_display_stats()]).classes('col-grow')
                                    ui.number('Quantity', min=0, value=input_state['quantity'],
                                              on_change=lambda e: input_state.update({'quantity': int(e.value or 0)})).classes('w-32')

                                with ui.row().classes('w-full gap-4 justify-end q-mt-md'):
                                    async def handle_update(mode):
                                        base_code = input_state['set_base_code']
                                        sel_lang = input_state['language']
                                        sel_rarity = input_state['rarity']
                                        sel_img = input_state['image_id']
                                        sel_cond = input_state['condition']
                                        sel_first = input_state['first_edition']
                                        input_qty = int(input_state['quantity'])

                                        variant_exists = False
                                        matched_variant_id = None

                                        for s in card.card_sets:
                                            s_img = s.image_id if s.image_id is not None else (card.card_images[0].id if card.card_images else None)
                                            if s.set_code == base_code and s.set_rarity == sel_rarity and s_img == sel_img:
                                                variant_exists = True
                                                matched_variant_id = s.variant_id
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
                                            matched_variant_id = generate_variant_id(card.id, base_code, sel_rarity, sel_img)

                                        final_set_code = transform_set_code(base_code, sel_lang)

                                        current_owned = 0
                                        if self.state['current_collection']:
                                             for c in self.state['current_collection'].cards:
                                                 if c.card_id == card.id:
                                                     for v in c.variants:
                                                         if v.variant_id == matched_variant_id:
                                                             for e in v.entries:
                                                                 if e.language == sel_lang and e.condition == sel_cond and e.first_edition == sel_first:
                                                                     current_owned = e.quantity
                                                                     break
                                                             break
                                                     break

                                        new_quantity = 0
                                        if mode == 'SET':
                                            new_quantity = input_qty
                                        elif mode == 'ADD':
                                            new_quantity = max(0, current_owned + input_qty)

                                        await self.save_card_change(
                                            card,
                                            final_set_code,
                                            sel_rarity,
                                            sel_lang,
                                            new_quantity,
                                            sel_cond,
                                            sel_first,
                                            image_id=sel_img,
                                            variant_id=matched_variant_id
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

                        matched_variant_id = None
                        if card.card_sets:
                             for s in card.card_sets:
                                 if s.set_code == edit_state['set'] and s.set_rarity == edit_state['rarity']:
                                     matched_variant_id = s.variant_id
                                     break

                        await self.save_card_change(
                            card,
                            final_set_code,
                            edit_state['rarity'],
                            edit_state['language'],
                            int(edit_state['quantity']),
                            "Near Mint",
                            False,
                            variant_id=matched_variant_id
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

                        flag = flag_map.get(item.language, item.language)
                        ui.label(flag).classes('absolute top-1 left-1 text-lg shadow-black drop-shadow-md bg-black/30 rounded px-1')

                        if item.is_owned:
                             ui.label(f"{item.owned_count}").classes('absolute top-1 right-1 bg-accent text-dark font-bold px-2 rounded-full text-xs')

                        cond_short = cond_map.get(item.condition, item.condition[:2].upper())
                        ed_text = "1st" if item.first_edition else ""

                        with ui.row().classes('absolute bottom-0 left-0 bg-black/80 text-white text-[10px] px-1 gap-1 items-center rounded-tr'):
                            ui.label(cond_short).classes('font-bold text-yellow-500')
                            if ed_text:
                                ui.label(ed_text).classes('font-bold text-orange-400')

                        ui.label(item.set_code).classes('absolute bottom-0 right-0 bg-black/80 text-white text-[10px] px-1 font-mono rounded-tl')

                    with ui.column().classes('p-2 gap-0 w-full'):
                        ui.label(item.api_card.name).classes('text-xs font-bold truncate w-full')
                        ui.label(f"{item.rarity}").classes('text-[10px] text-gray-400')
                        ui.label(f"${item.price:.2f}").classes('text-xs text-green-400')

    async def switch_scope(self, scope):
        self.state['view_scope'] = scope
        await self.load_data()
        self.render_header.refresh()

    def open_new_collection_dialog(self):
        with ui.dialog() as d, ui.card().classes('w-96'):
            ui.label('Create New Collection').classes('text-h6')

            name_input = ui.input('Collection Name').classes('w-full').props('autofocus')

            async def create():
                name = name_input.value.strip()
                if not name:
                    ui.notify('Please enter a name.', type='warning')
                    return

                # Ensure extension
                if not name.endswith(('.json', '.yaml', '.yml')):
                    name += '.json'

                # Check if exists
                existing = persistence.list_collections()
                if name in existing:
                    ui.notify(f'Collection "{name}" already exists.', type='negative')
                    return

                # Create empty collection
                new_col = Collection(name=name.replace('.json', '').replace('.yaml', '').replace('.yml', ''), cards=[])
                try:
                    await run.io_bound(persistence.save_collection, new_col, name)
                    ui.notify(f'Collection "{name}" created.', type='positive')
                    self.state['selected_file'] = name
                    d.close()
                    # Reload data and header
                    await self.load_data()
                    self.render_header.refresh()
                except Exception as e:
                    logger.error(f"Error creating collection: {e}")
                    ui.notify(f"Error creating collection: {e}", type='negative')

            with ui.row().classes('w-full justify-end q-mt-md'):
                ui.button('Cancel', on_click=lambda: [d.close(), self.render_header.refresh()]).props('flat')
                ui.button('Create', on_click=create).props('color=positive')
        d.open()

    @ui.refreshable
    def render_header(self):
        with ui.row().classes('w-full items-center gap-4 q-mb-md p-4 bg-gray-900 rounded-lg border border-gray-800'):
            ui.label('Gallery').classes('text-h5')

            files = persistence.list_collections()
            # Transform file list to dict for cleaner display (hide .json/.yaml)
            file_options = {}
            for f in files:
                display_name = f
                if f.endswith('.json'): display_name = f[:-5]
                elif f.endswith('.yaml'): display_name = f[:-5]
                elif f.endswith('.yml'): display_name = f[:-4]
                file_options[f] = display_name

            # Add option to create new
            file_options['__NEW_COLLECTION__'] = '+ New Collection'

            async def handle_collection_change(e):
                val = e.value
                if val == '__NEW_COLLECTION__':
                    # Reset selection to previous valid one temporarily or None to avoid sticking on 'New'
                    # Actually keeping it momentarily is fine as we open dialog
                    self.open_new_collection_dialog()
                    # Revert selection to current real collection if dialog is cancelled?
                    # We will handle that in the dialog logic or just refresh header
                else:
                    self.state['selected_file'] = val
                    await self.load_data()

            with ui.select(file_options, value=self.state['selected_file'], label='Collection',
                      on_change=handle_collection_change).classes('w-40'):
                ui.tooltip('Select which collection file to view')

            async def on_search(e):
                self.state['search_text'] = e.value
                await self.apply_filters()

            with ui.input(placeholder='Search...', on_change=on_search) \
                .props('debounce=300 icon=search').classes('w-64') as i:
                i.value = self.state['search_text']
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
                with ui.switch('Owned', on_change=on_owned_switch).bind_value(self.state, 'only_owned'):
                    ui.tooltip('Toggle to show only cards you own')

            ui.separator().props('vertical')

            with ui.button_group():
                is_cons = self.state['view_scope'] == 'consolidated'
                with ui.button('Consolidated', on_click=lambda: self.switch_scope('consolidated')) \
                    .props(f'flat={not is_cons} color=accent'):
                    ui.tooltip('View consolidated gameplay statistics (totals per card)')
                with ui.button('Collectors', on_click=lambda: self.switch_scope('collectors')) \
                    .props(f'flat={is_cons} color=accent'):
                    ui.tooltip('View detailed market and collection data (separate entries per set/rarity)')

            with ui.button_group():
                is_grid = self.state['view_mode'] == 'grid'
                with ui.button(icon='grid_view', on_click=lambda: [self.state.update({'view_mode': 'grid'}), self.content_area.refresh(), self.render_header.refresh()]) \
                    .props(f'flat={not is_grid} color=accent'):
                    ui.tooltip('Show cards in a grid layout')
                with ui.button(icon='list', on_click=lambda: [self.state.update({'view_mode': 'list'}), self.content_area.refresh(), self.render_header.refresh()]) \
                    .props(f'flat={is_grid} color=accent'):
                    ui.tooltip('Show cards in a list layout')

            ui.space()
            with ui.button(icon='filter_list', on_click=self.filter_dialog.open).props('color=primary size=lg'):
                ui.tooltip('Open advanced filters')

    @ui.refreshable
    def content_area(self):
        start = (self.state['page'] - 1) * self.state['page_size']
        end = min(start + self.state['page_size'], len(self.state['filtered_items']))
        page_items = self.state['filtered_items'][start:end]

        with ui.row().classes('w-full items-center justify-between q-mb-sm px-4'):
            ui.label(f"Showing {start+1}-{end} of {len(self.state['filtered_items'])}").classes('text-grey')

            with ui.row().classes('items-center gap-2'):
                async def set_page(p):
                    self.state['page'] = int(p) if p else 1
                    await self.prepare_current_page_images()
                    self.content_area.refresh()

                async def change_page(delta):
                    new_p = max(1, min(self.state['total_pages'], self.state['page'] + delta))
                    if new_p != self.state['page']:
                        self.state['page'] = new_p
                        await self.prepare_current_page_images()
                        self.content_area.refresh()

                with ui.button(icon='chevron_left', on_click=lambda: change_page(-1)).props('flat dense'):
                    ui.tooltip('Go to previous page')
                with ui.number(value=self.state['page'], min=1, max=self.state['total_pages'],
                          on_change=lambda e: set_page(e.value)).classes('w-20').props('dense borderless input-class="text-center"'):
                    ui.tooltip('Current page number')
                ui.label(f"/ {max(1, self.state['total_pages'])}")
                with ui.button(icon='chevron_right', on_click=lambda: change_page(1)).props('flat dense'):
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

    def build_ui(self):
        self.filter_dialog = ui.dialog().props('position=right')
        with self.filter_dialog, ui.card().classes('h-full w-96 bg-gray-900 border-l border-gray-700 p-0 flex flex-col'):
             with ui.scroll_area().classes('flex-grow w-full'):
                 with ui.column().classes('w-full p-4 gap-4'):
                    ui.label('Filters').classes('text-h6')

                    self.set_selector = ui.select(self.state['available_sets'], label='Set', with_input=True, clearable=True,
                              on_change=self.apply_filters).bind_value(self.state, 'filter_set').classes('w-full').props('use-input fill-input input-debounce=0')

                    common_rarities = ["Common", "Rare", "Super Rare", "Ultra Rare", "Secret Rare", "Ghost Rare", "Ultimate Rare", "Starlight Rare", "Collector's Rare"]
                    ui.select(common_rarities, label='Rarity', with_input=True, clearable=True, on_change=self.apply_filters).bind_value(self.state, 'filter_rarity').classes('w-full')

                    ui.select(['DARK', 'LIGHT', 'EARTH', 'WIND', 'FIRE', 'WATER', 'DIVINE'],
                              label='Attribute', clearable=True, on_change=self.apply_filters).bind_value(self.state, 'filter_attr').classes('w-full')

                    self.ctype_selector = ui.select(self.state['available_card_types'], label='Card Types', with_input=True, clearable=True,
                                                    on_change=self.apply_filters).bind_value(self.state, 'filter_card_type').classes('w-full')

                    self.m_race_selector = ui.select(self.state['available_monster_races'], label='Monster Type', with_input=True, clearable=True,
                                                    on_change=self.apply_filters).bind_value(self.state, 'filter_monster_race').classes('w-full')

                    self.st_race_selector = ui.select(self.state['available_st_races'], label='Spell/Trap Type', with_input=True, clearable=True,
                                                    on_change=self.apply_filters).bind_value(self.state, 'filter_st_race').classes('w-full')

                    self.archetype_selector = ui.select(self.state['available_archetypes'], label='Archetype', with_input=True, clearable=True,
                                                    on_change=self.apply_filters).bind_value(self.state, 'filter_archetype').classes('w-full')

                    categories = ['Effect', 'Normal', 'Synchro', 'Xyz', 'Ritual', 'Fusion', 'Link', 'Pendulum', 'Toon', 'Spirit', 'Union', 'Gemini', 'Flip']
                    ui.select(categories, label='Monster Category', multiple=True, clearable=True, on_change=self.apply_filters).bind_value(self.state, 'filter_monster_category').classes('w-full').props('use-chips')

                    ui.number('Level/Rank', min=0, max=13, on_change=self.apply_filters).bind_value(self.state, 'filter_level').classes('w-full')

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

             with ui.column().classes('p-4 border-t border-gray-700 bg-gray-900 w-full'):
                 with ui.button('Reset All Filters', on_click=self.reset_filters).classes('w-full').props('color=red-9 outline'):
                     ui.tooltip('Clear all active filters and reset to default')

        self.render_header()

        self.content_area()
        ui.timer(0.1, self.load_data, once=True)

def collection_page():
    page = CollectionPage()
    page.build_ui()
