from nicegui import ui, run
from src.core.persistence import persistence
from src.core.models import Collection, CollectionCard, CollectionVariant, CollectionEntry, Card, CardMetadata
from src.services.ygo_api import ygo_service, ApiCard
from src.services.image_manager import image_manager
from src.core.config import config_manager
from src.core.utils import transform_set_code, generate_variant_id, normalize_set_code
from src.ui.components.filter_pane import FilterPane
from src.ui.components.single_card_view import SingleCardView
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Set, Callable
import asyncio
import traceback
import re
import logging
import os

logger = logging.getLogger(__name__)

@dataclass
class CardViewModel:
    api_card: ApiCard
    owned_quantity: int
    is_owned: bool
    lowest_price: float = 0.0
    owned_languages: Set[str] = field(default_factory=set)
    owned_conditions: Set[str] = field(default_factory=set)

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
        owned_conds = set()
        if c_card:
            for v in c_card.variants:
                for e in v.entries:
                    owned_langs.add(e.language)
                    owned_conds.add(e.condition)

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

        vms.append(CardViewModel(card, qty, qty > 0, lowest, owned_langs, owned_conds))
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

                # Find ALL matching variants (Exact ID match + Fuzzy/Normalized matches)
                matching_variants = []

                # 1. Exact Match
                exact_match = owned_variants.get(target_variant_id)
                if exact_match:
                    matching_variants.append(exact_match)

                # 2. Fuzzy Match (Normalized Code + Rarity)
                norm_api = normalize_set_code(cset.set_code)
                for var_id, var in owned_variants.items():
                    # Skip if already matched exactly (avoid duplicates)
                    if var.variant_id == target_variant_id:
                        continue

                    # Skip if previously processed (e.g. matched by another API set entry? Unlikely but safe)
                    if var_id in processed_variant_ids:
                         continue

                    # Check normalization match
                    if normalize_set_code(var.set_code) == norm_api and var.rarity == cset.set_rarity:
                        matching_variants.append(var)

                if matching_variants:
                    for matched_cv in matching_variants:
                        processed_variant_ids.add(matched_cv.variant_id)

                        groups = {}
                        for entry in matched_cv.entries:
                            k = (entry.language, entry.condition, entry.first_edition)
                            groups[k] = groups.get(k, 0) + entry.quantity

                        for (lang, cond, first), qty in groups.items():
                            rows.append(CollectorRow(
                                api_card=card,
                                set_code=matched_cv.set_code, # Use the actual owned set code (e.g. MP25-DE278)
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
                    # No owned variants matched this API set -> Show empty placeholder row
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
            'available_card_types': ['Monster', 'Spell', 'Trap', 'Skill'],
            'max_owned_quantity': 100,

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
            'language': config_manager.get_language(),
            'sort_by': 'Name',
            'sort_descending': False,

            'view_scope': 'consolidated',
            'view_mode': 'grid',
            'page': 1,
            'page_size': 48,
            'total_pages': 1,
        }

        files = persistence.list_collections()
        self.state['selected_file'] = files[0] if files else None
        self.filter_pane: Optional[FilterPane] = None
        self.single_card_view = SingleCardView()

        # UI Element references for pagination updates
        self.pagination_showing_label = None
        self.pagination_total_label = None

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
        if self.filter_pane:
            self.filter_pane.update_options()

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

        if self.filter_pane:
            self.filter_pane.reset_ui_elements()

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
            if hasattr(self, 'render_card_display'): self.render_card_display.refresh()
            self.update_pagination_labels()
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

        if self.state.get('filter_condition'):
            conds = self.state['filter_condition']
            if self.state['view_scope'] == 'consolidated':
                res = [c for c in res if any(cond in c.owned_conditions for cond in conds)]
            else:
                res = [c for c in res if c.condition in conds]

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
             res.sort(key=lambda x: get_price(x), reverse=reverse)

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

    async def save_card_change(self, api_card: ApiCard, set_code, rarity, language, quantity, condition, first_edition, image_id: Optional[int] = None, variant_id: Optional[str] = None, mode: str = 'SET'):
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
            # If removing/setting 0 and it doesn't exist, do nothing
            if quantity <= 0 and mode == 'SET': return
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
             # Need to add if quantity > 0
             should_add = False
             if mode == 'SET' and quantity > 0: should_add = True
             elif mode == 'ADD' and quantity > 0: should_add = True

             if should_add:
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

            # Calculate new quantity
            final_quantity = 0
            current_quantity = target_entry.quantity if target_entry else 0

            if mode == 'SET':
                final_quantity = quantity
            elif mode == 'ADD':
                final_quantity = current_quantity + quantity

            if final_quantity > 0:
                if target_entry:
                    target_entry.quantity = final_quantity
                else:
                    target_variant.entries.append(CollectionEntry(
                        condition=condition,
                        language=language,
                        first_edition=first_edition,
                        quantity=final_quantity
                    ))
            else:
                if target_entry:
                    target_variant.entries.remove(target_entry)

            if not target_variant.entries:
                target_card.variants.remove(target_variant)

        if not target_card.variants:
            if target_card in col.cards:
                col.cards.remove(target_card)

        try:
            await run.io_bound(persistence.save_collection, col, self.state['selected_file'])
            logger.info(f"Collection saved: {self.state['selected_file']}")
            ui.notify('Collection saved.', type='positive')
            await self.load_data()
        except Exception as e:
            logger.error(f"Error saving collection: {e}")
            ui.notify(f"Error saving: {e}", type='negative')

    async def open_single_view(self, card: ApiCard, is_owned: bool = False, quantity: int = 0, initial_set: str = None, owned_languages: Set[str] = None, rarity: str = None, set_name: str = None, language: str = None, condition: str = "Near Mint", first_edition: bool = False, image_url: str = None, image_id: int = None, set_price: float = 0.0):
        async def on_save(c, set_code, rarity, language, quantity, condition, first_edition, image_id, variant_id, mode):
            await self.save_card_change(c, set_code, rarity, language, quantity, condition, first_edition, image_id, variant_id, mode)

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

            await self.single_card_view.open_consolidated(card, total_owned, owned_breakdown, on_save)
            return

        if self.state['view_scope'] == 'collectors':
             await self.single_card_view.open_collectors(card, quantity, initial_set or "N/A", rarity, set_name, language, condition, first_edition, image_url, image_id, set_price, self.state['current_collection'], on_save)
             return

        # Fallback removed

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

                    with ui.row().classes('w-full justify-center'):
                         if vm.is_owned:
                              ui.label(str(vm.owned_quantity)).classes('font-bold text-accent text-lg')
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

                    with ui.row().classes('w-full justify-center'):
                         if item.is_owned:
                              ui.label(str(item.owned_count)).classes('font-bold text-accent text-lg')
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
                # Smart default: non-Name fields usually sort descending (High to Low)
                if e.value != 'Name':
                    self.state['sort_descending'] = True
                else:
                    self.state['sort_descending'] = False
                self.render_header.refresh()
                await self.apply_filters()

            with ui.row().classes('items-center gap-1'):
                with ui.select(['Name', 'ATK', 'DEF', 'Level', 'Newest', 'Price'], value=self.state['sort_by'], label='Sort',
                        on_change=on_sort_change).classes('w-32'):
                    ui.tooltip('Choose how to sort the displayed cards')

                async def toggle_sort_dir():
                    self.state['sort_descending'] = not self.state['sort_descending']
                    self.render_header.refresh()
                    await self.apply_filters()

                icon = 'arrow_downward' if self.state.get('sort_descending') else 'arrow_upward'
                with ui.button(icon=icon, on_click=toggle_sort_dir).props('flat round dense color=white'):
                    ui.tooltip('Toggle sort direction')

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
                with ui.button(icon='grid_view', on_click=lambda: [self.state.update({'view_mode': 'grid'}), self.render_card_display.refresh(), self.render_header.refresh()]) \
                    .props(f'flat={not is_grid} color=accent'):
                    ui.tooltip('Show cards in a grid layout')
                with ui.button(icon='list', on_click=lambda: [self.state.update({'view_mode': 'list'}), self.render_card_display.refresh(), self.render_header.refresh()]) \
                    .props(f'flat={is_grid} color=accent'):
                    ui.tooltip('Show cards in a list layout')

            ui.space()
            with ui.button(icon='filter_list', on_click=self.filter_dialog.open).props('color=primary size=lg'):
                ui.tooltip('Open advanced filters')

    @ui.refreshable
    def render_card_display(self):
        start = (self.state['page'] - 1) * self.state['page_size']
        end = min(start + self.state['page_size'], len(self.state['filtered_items']))
        page_items = self.state['filtered_items'][start:end]

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

    def content_area(self):
        # Pagination controls - static
        with ui.row().classes('w-full items-center justify-between q-mb-sm px-4'):
            self.pagination_showing_label = ui.label("Loading...").classes('text-grey')

            with ui.row().classes('items-center gap-2'):
                async def set_page(p):
                    new_val = int(p) if p else 1
                    self.state['page'] = new_val
                    await self.prepare_current_page_images()
                    self.render_card_display.refresh()
                    self.update_pagination_labels()

                async def change_page(delta):
                    new_p = max(1, min(self.state['total_pages'], self.state['page'] + delta))
                    if new_p != self.state['page']:
                        self.state['page'] = new_p
                        await self.prepare_current_page_images()
                        self.render_card_display.refresh()
                        self.update_pagination_labels()

                with ui.button(icon='chevron_left', on_click=lambda: change_page(-1)).props('flat dense'):
                    ui.tooltip('Go to previous page')

                # Bind value to state, but handle on_change for actions
                # Important: on_change fires on every keystroke usually unless debounced, or lazy.
                # For page numbers, enter or blur is better, but NiceGUI number input usually updates on change.
                # To prevent focus loss, this element is NOT rebuilt.

                n_input = ui.number(min=1).bind_value(self.state, 'page').props('dense borderless input-class="text-center"').classes('w-20')
                n_input.on('change', lambda e: set_page(e.value))
                n_input.on('keydown.enter', lambda: set_page(self.state['page']))

                self.pagination_total_label = ui.label("/ 1")

                with ui.button(icon='chevron_right', on_click=lambda: change_page(1)).props('flat dense'):
                    ui.tooltip('Go to next page')

        # Render the refreshable card display
        self.render_card_display()

        # Initial label update
        self.update_pagination_labels()

    def build_ui(self):
        self.filter_dialog = ui.dialog().props('position=right')
        with self.filter_dialog, ui.card().classes('h-full w-96 bg-gray-900 border-l border-gray-700 p-0 flex flex-col'):
             with ui.scroll_area().classes('flex-grow w-full'):
                 self.filter_pane = FilterPane(self.state, self.apply_filters, self.reset_filters)
                 self.filter_pane.build()

        self.render_header()

        self.content_area()
        ui.timer(0.1, self.load_data, once=True)

def collection_page():
    page = CollectionPage()
    page.build_ui()
