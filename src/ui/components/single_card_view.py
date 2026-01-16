from nicegui import ui
from src.core.models import ApiCardSet
from src.services.ygo_api import ApiCard, ygo_service
from src.services.image_manager import image_manager
from src.core.utils import transform_set_code, generate_variant_id, normalize_set_code
from typing import List, Optional, Dict, Set, Callable, Any
import logging
import asyncio

logger = logging.getLogger(__name__)

SUPPORTED_LANGUAGES = ['EN', 'DE', 'FR', 'IT', 'ES', 'PT']
STANDARD_RARITIES = [
    'Common', 'Rare', 'Super Rare', 'Ultra Rare', 'Secret Rare',
    'Ultimate Rare', 'Ghost Rare', 'Starlight Rare', "Collector's Rare",
    'Prismatic Secret Rare', 'Platinum Secret Rare', 'Quarter Century Secret Rare',
    'Gold Rare', 'Premium Gold Rare'
]

class SingleCardView:
    def _setup_high_res_image_logic(self, img_id: int, high_res_remote_url: str, low_res_url: str, image_element: ui.image, current_id_check: Callable[[], bool] = None):
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

    def _render_inventory_management(
        self,
        card: ApiCard,
        input_state: Dict[str, Any],
        set_options: Dict[str, str],
        set_info_map: Dict[str, Any],
        on_change_callback: Callable[[], None],
        on_save_callback: Callable[[str], Any],
        default_set_base_code: str = None
    ):
        """
        Renders the inventory management section (Language, Set, Rarity, etc.).
        Shared by both Consolidated and Collectors views.
        """
        with ui.card().classes('w-full bg-transparent p-4 gap-4'):
            with ui.row().classes('w-full gap-4'):
                ui.select(SUPPORTED_LANGUAGES, label='Language', value=input_state['language'],
                            on_change=lambda e: [input_state.update({'language': e.value}), on_change_callback()]).classes('w-1/3')

                # Ensure set_base_code is valid or default
                if input_state['set_base_code'] not in set_options and default_set_base_code:
                     input_state['set_base_code'] = default_set_base_code

                set_select = ui.select(set_options, label='Set Name', value=input_state['set_base_code']).classes('col-grow')

            with ui.row().classes('w-full gap-4'):
                # Ensure current rarity is available in options to prevent crash
                rarity_options = list(STANDARD_RARITIES)
                if input_state['rarity'] not in rarity_options:
                    rarity_options.append(input_state['rarity'])

                rarity_select = ui.select(rarity_options, label='Rarity', value=input_state['rarity'],
                            on_change=lambda e: [input_state.update({'rarity': e.value}), on_change_callback()]).classes('w-1/3')

                def on_set_change(e):
                    new_code = e.value
                    input_state['set_base_code'] = new_code

                    if new_code in set_info_map:
                        s_info = set_info_map[new_code]
                        # Update rarity if available
                        if s_info.set_rarity:
                            input_state['rarity'] = s_info.set_rarity
                            rarity_select.value = s_info.set_rarity

                    on_change_callback()

                set_select.on_value_change(on_set_change)
                ui.select(['Mint', 'Near Mint', 'Played', 'Damaged'], label='Condition', value=input_state['condition'],
                            on_change=lambda e: [input_state.update({'condition': e.value}), on_change_callback()]).classes('w-1/3')
                ui.checkbox('1st Edition', value=input_state['first_edition'],
                            on_change=lambda e: [input_state.update({'first_edition': e.value}), on_change_callback()]).classes('my-auto')

            with ui.row().classes('w-full gap-4 items-center'):
                if card.card_images and len(card.card_images) > 1:
                    art_options = {img.id: f"Artwork {i+1} (ID: {img.id})" for i, img in enumerate(card.card_images)}
                    # Ensure image_id is int for matching
                    current_img_id = int(input_state['image_id']) if input_state['image_id'] is not None else None
                    ui.select(art_options, label='Artwork', value=current_img_id,
                                on_change=lambda e: [input_state.update({'image_id': e.value}), on_change_callback()]).classes('col-grow')

                ui.number('Quantity', min=0, value=input_state['quantity'],
                            on_change=lambda e: input_state.update({'quantity': int(e.value or 0)})).classes('w-32')

            with ui.row().classes('w-full gap-4 justify-end q-mt-md'):
                async def handle_update(mode):
                    base_code = input_state['set_base_code']
                    sel_rarity = input_state['rarity']
                    sel_img = input_state['image_id']

                    # Check if variant exists in API data
                    variant_exists = False
                    matched_variant_id = None

                    if card.card_sets:
                        for s in card.card_sets:
                            s_img = s.image_id if s.image_id is not None else (card.card_images[0].id if card.card_images else None)
                            # We compare image IDs loosely? No, exact match usually.
                            # But input_state['image_id'] comes from UI, might be int.
                            if s.set_code == base_code and s.set_rarity == sel_rarity and s_img == sel_img:
                                variant_exists = True
                                matched_variant_id = s.variant_id
                                break

                    if not variant_exists:
                        s_name = set_info_map[base_code].set_name if base_code in set_info_map else "Custom Set"
                        # Create new variant if it doesn't exist
                        # We might need to handle this more gracefully or assume add_card_variant works
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

                    # We pass the variant_id back to the saver
                    await on_save_callback(mode, matched_variant_id)

                ui.button('SET', on_click=lambda: handle_update('SET')).props('color=warning text-color=dark')
                ui.button('ADD', on_click=lambda: handle_update('ADD')).props('color=secondary')

                async def confirm_remove():
                    with ui.dialog() as d, ui.card():
                        ui.label("Are you sure you want to remove this card variant from your collection?").classes('text-lg')
                        with ui.row().classes('w-full justify-end'):
                            ui.button('Cancel', on_click=d.close).props('flat')
                            def do_remove():
                                d.close()
                                input_state['quantity'] = 0
                                handle_update('SET')
                            ui.button('Remove', on_click=do_remove).props('color=negative')
                    d.open()

                ui.button('REMOVE', on_click=confirm_remove).props('color=negative')

    def _render_available_sets(self, card: ApiCard):
        ui.separator().classes('q-my-md')
        ui.label('Available Sets').classes('text-h6 q-mb-sm select-none text-accent')

        if card.card_sets:
            with ui.grid(columns=4).classes('w-full gap-2 text-sm'):
                # Header
                ui.label('Set Code').classes('font-bold text-gray-400')
                ui.label('Set Name').classes('font-bold text-gray-400')
                ui.label('Rarity').classes('font-bold text-gray-400')
                ui.label('Price').classes('font-bold text-gray-400')

                for s in card.card_sets:
                    ui.label(s.set_code).classes('font-mono font-bold text-yellow-500')
                    ui.label(s.set_name).classes('truncate')
                    ui.label(s.set_rarity)
                    price = s.set_price
                    if price:
                        try:
                            price_str = f"${float(price):.2f}"
                        except:
                            price_str = str(price)
                    else:
                        price_str = "-"
                    ui.label(price_str).classes('text-green-400')
        else:
            ui.label('No set information available.').classes('text-gray-500 italic')

    async def open_consolidated(
        self,
        card: ApiCard,
        total_owned: int,
        owned_breakdown: Dict[str, int],
        save_callback: Callable
    ):
        try:
            with ui.dialog().props('maximized transition-show=slide-up transition-hide=slide-down') as d, ui.card().classes('w-full h-full p-0 no-shadow'):
                d.open()
                ui.button(icon='close', on_click=d.close).props('flat round color=white').classes('absolute top-2 right-2 z-50')

                with ui.row().classes('w-full h-full no-wrap gap-0'):
                    # Image Column
                    with ui.column().classes('w-1/3 min-w-[300px] h-full bg-black items-center justify-center p-8 shrink-0'):
                        img_id = card.card_images[0].id if card.card_images else card.id
                        high_res_url = card.card_images[0].image_url if card.card_images else None
                        low_res_url = card.card_images[0].image_url_small if card.card_images else None

                        image_element = ui.image().classes('max-h-full max-w-full object-contain shadow-2xl')

                        # Initial image setup
                        self._setup_high_res_image_logic(img_id, high_res_url, low_res_url, image_element)

                        # Function to update image based on selection
                        def update_image(new_img_id):
                            h_res = None
                            l_res = None
                            if card.card_images:
                                for img in card.card_images:
                                    if img.id == new_img_id:
                                        h_res = img.image_url
                                        l_res = img.image_url_small
                                        break
                            if not l_res:
                                l_res = low_res_url

                            self._setup_high_res_image_logic(new_img_id, h_res, l_res, image_element, current_id_check=lambda: True)


                    with ui.column().classes('col h-full bg-gray-900 text-white p-8 scroll-y-auto'):
                        with ui.row().classes('w-full items-center justify-between'):
                            ui.label(card.name).classes('text-4xl font-bold text-white select-text')
                        if total_owned > 0:
                            with ui.label(str(total_owned)).classes('text-2xl font-bold text-accent'):
                                ui.tooltip('Total Owned')

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

                        ui.separator().classes('q-my-md')

                        # Add to inventory section
                        inventory_expansion = ui.expansion().classes('w-full bg-gray-800 rounded').props('icon=add label="Add to Inventory"')
                        # Default expanded for visibility? Maybe collapsed to avoid clutter. Let's keep collapsed by default or as requested.
                        # "Add to inventory section. That works similarly to the Manage Inventory section"

                        with inventory_expansion:
                            # Prepare options
                            set_options = {}
                            set_info_map = {}
                            if card.card_sets:
                                for s in card.card_sets:
                                    code = s.set_code
                                    s_name = s.set_name

                                    # Attempt fallback resolution if name seems missing or generic
                                    if not s_name or s_name == "Custom Set" or s_name == "N/A":
                                         fallback = await ygo_service.get_set_name_by_code(code)
                                         if fallback:
                                             s_name = fallback

                                    if code not in set_options:
                                        set_options[code] = f"{s_name} ({code})"
                                        set_info_map[code] = s
                            else:
                                set_options["Custom"] = "Custom Set"

                            # Default values
                            default_set_code = list(set_options.keys())[0] if set_options else "Custom"
                            default_rarity = "Common"
                            if default_set_code in set_info_map:
                                 default_rarity = set_info_map[default_set_code].set_rarity

                            input_state = {
                                'language': 'EN', # Default language?
                                'quantity': 1,
                                'rarity': default_rarity,
                                'condition': 'Near Mint',
                                'first_edition': False,
                                'set_base_code': default_set_code,
                                'image_id': img_id
                            }

                            async def on_save_wrapper(mode, variant_id):
                                # In Consolidated view, we probably just add/set.
                                # Logic:
                                # 1. Calculate final set code
                                final_set_code = transform_set_code(input_state['set_base_code'], input_state['language'])

                                # 2. Call save callback
                                await save_callback(
                                    card,
                                    final_set_code,
                                    input_state['rarity'],
                                    input_state['language'],
                                    input_state['quantity'],
                                    input_state['condition'],
                                    input_state['first_edition'],
                                    input_state['image_id'],
                                    variant_id,
                                    mode # Pass mode (SET/ADD) to handle logic in save_card_change or wrapper
                                )
                                d.close()

                            def on_change():
                                 # Update image if image_id changed
                                 if input_state['image_id'] != img_id:
                                     update_image(input_state['image_id'])

                            self._render_inventory_management(
                                card=card,
                                input_state=input_state,
                                set_options=set_options,
                                set_info_map=set_info_map,
                                on_change_callback=on_change,
                                on_save_callback=on_save_wrapper,
                                default_set_base_code=default_set_code
                            )

                        self._render_available_sets(card)

        except Exception as e:
            logger.error(f"ERROR in render_consolidated_single_view: {e}", exc_info=True)


    async def open_collectors(
        self,
        card: ApiCard,
        owned_count: int,
        set_code: str,
        rarity: str,
        set_name: str,
        language: str,
        condition: str,
        first_edition: bool,
        image_url: str = None,
        image_id: int = None,
        set_price: float = 0.0,
        current_collection: Any = None,
        save_callback: Callable = None
    ):
        try:
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
                    # Try normalized matching (ignore region code differences)
                    norm_target = normalize_set_code(set_code)
                    for base in set_options.keys():
                        if normalize_set_code(base) == norm_target:
                            initial_base_code = base
                            found = True
                            break

                if not found:
                    # Fallback: Attempt to resolve set name from global DB
                    fallback_name = await ygo_service.get_set_name_by_code(set_code)

                    # Determine name to use
                    final_name = fallback_name
                    if not final_name:
                        final_name = set_name if set_name and set_name != "Custom Set" else "Unknown Set"

                    set_options[set_code] = f"{final_name} ({set_code})"

                    # Create dummy ApiCardSet for set_info_map to prevent crash/Custom fallback
                    dummy_set = ApiCardSet(
                        set_name=final_name,
                        set_code=set_code,
                        set_rarity=rarity or "Common"
                    )
                    set_info_map[set_code] = dummy_set
                    initial_base_code = set_code

                    # Update the display name if it was missing/custom
                    if fallback_name:
                        set_name = fallback_name

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

                            self._setup_high_res_image_logic(
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

                        with ui.row().classes('items-center gap-2'):
                             ui.label('Total Owned:').classes('text-lg text-gray-400 font-bold')
                             owned_label = ui.label(str(owned_count)).classes('text-2xl font-bold text-accent')
                             with owned_label:
                                ui.tooltip('Owned Count')

                        if owned_count == 0:
                            owned_label.set_visibility(False)

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
                            if current_collection:
                                for c in current_collection.cards:
                                    if c.card_id == card.id:
                                            for v in c.variants:
                                                if v.set_code == final_code and v.rarity == input_state['rarity'] and v.image_id == input_state['image_id']:
                                                    for e in v.entries:
                                                        if e.language == input_state['language'] and e.condition == input_state['condition'] and e.first_edition == input_state['first_edition']:
                                                            cur_owned = e.quantity
                                                            break
                                                    break
                                            break
                                            break

                            owned_label.text = str(cur_owned)
                            owned_label.set_visibility(cur_owned > 0)

                            update_image()

                        ui.separator().classes('q-my-md')

                        inventory_expansion = ui.expansion().classes('w-full bg-gray-800 rounded').props('icon=edit label="Manage Inventory"')
                        inventory_expansion.value = True

                        with inventory_expansion:
                             async def on_save_wrapper(mode, variant_id):
                                final_set_code = transform_set_code(input_state['set_base_code'], input_state['language'])

                                await save_callback(
                                    card,
                                    final_set_code,
                                    input_state['rarity'],
                                    input_state['language'],
                                    input_state['quantity'],
                                    input_state['condition'],
                                    input_state['first_edition'],
                                    input_state['image_id'],
                                    variant_id,
                                    mode
                                )
                                d.close()

                             self._render_inventory_management(
                                card=card,
                                input_state=input_state,
                                set_options=set_options,
                                set_info_map=set_info_map,
                                on_change_callback=update_display_stats,
                                on_save_callback=on_save_wrapper,
                                default_set_base_code=initial_base_code
                            )

                        self._render_available_sets(card)

        except Exception as e:
            logger.error(f"ERROR in render_collectors_single_view: {e}", exc_info=True)

    async def open_deck_builder(
        self,
        card: ApiCard,
        on_add_callback: Callable[[int, int, str], Any]
    ):
        try:
             with ui.dialog().props('maximized transition-show=slide-up transition-hide=slide-down') as d, ui.card().classes('w-full h-full p-0 no-shadow'):
                d.open()
                ui.button(icon='close', on_click=d.close).props('flat round color=white').classes('absolute top-2 right-2 z-50')

                with ui.row().classes('w-full h-full no-wrap gap-0'):
                    # Image Column (Simplified, just use default/first image)
                    with ui.column().classes('w-1/3 min-w-[300px] h-full bg-black items-center justify-center p-8 shrink-0'):
                         img_id = card.card_images[0].id if card.card_images else card.id
                         url = card.card_images[0].image_url if card.card_images else None
                         small_url = card.card_images[0].image_url_small if card.card_images else None
                         image_element = ui.image().classes('max-h-full max-w-full object-contain shadow-2xl')
                         self._setup_high_res_image_logic(img_id, url, small_url, image_element)

                    with ui.column().classes('col h-full bg-gray-900 text-white p-8 scroll-y-auto'):
                         # Basic Info
                         ui.label(card.name).classes('text-4xl font-bold text-white')

                         with ui.grid(columns=4).classes('w-full gap-4 text-lg q-my-md'):
                             def stat(label, value):
                                 with ui.column():
                                     ui.label(label).classes('text-gray-400 text-sm uppercase font-bold')
                                     ui.label(str(value) if value is not None else '-').classes('font-bold text-xl')

                             stat('Type', card.type)
                             if 'Monster' in card.type:
                                 stat('ATK', card.atk)
                                 stat('DEF', getattr(card, 'def_', '-'))
                                 stat('Level', card.level)
                                 stat('Race', card.race)
                                 stat('Attribute', card.attribute)
                             else:
                                 stat('Race', card.race)

                         ui.markdown(card.desc).classes('text-gray-300 leading-relaxed text-lg q-mb-md')
                         ui.separator().classes('q-my-md bg-gray-700')

                         # Add to Deck Section
                         ui.label('Add to Deck').classes('text-h6 q-mb-sm text-accent')

                         qty_input = ui.number('Quantity', value=1, min=1, max=3).classes('w-32')

                         with ui.row().classes('gap-4 q-mt-md'):
                             async def add(target):
                                 qty = int(qty_input.value or 1)
                                 await on_add_callback(card.id, qty, target)
                                 d.close()

                             ui.button('Add to Main', on_click=lambda: add('main')).props('color=positive icon=add')
                             ui.button('Add to Side', on_click=lambda: add('side')).props('color=warning text-color=dark icon=add')
                             ui.button('Add to Extra', on_click=lambda: add('extra')).props('color=purple icon=add')

        except Exception as e:
            logger.error(f"Error opening deck builder view: {e}", exc_info=True)
