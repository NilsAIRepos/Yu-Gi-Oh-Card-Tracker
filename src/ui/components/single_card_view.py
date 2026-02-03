from nicegui import ui, run
from src.core.models import ApiCardSet
from src.services.ygo_api import ApiCard, ygo_service
from src.services.image_manager import image_manager
from src.core.utils import transform_set_code, generate_variant_id, normalize_set_code, extract_language_code
from src.core.constants import CARD_CONDITIONS
from typing import List, Optional, Dict, Set, Callable, Any
import logging
import asyncio
import random
import requests
import os
from PIL import Image
import io
import base64

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

        # Check local standard-res (fallback for custom art that doesn't have remote high-res)
        if not high_res_remote_url and image_manager.image_exists(img_id, high_res=False):
                image_element.source = f"/images/{img_id}.jpg"
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
        default_set_base_code: str = None,
        original_variant_id: str = None,
        show_remove_button: bool = True,
        rarity_map: Dict[str, Set[str]] = None,
        view_mode: str = 'consolidated',
        current_collection: Any = None,
        original_quantity: int = 0,
        storage_options: Dict[str, str] = None
    ):
        """
        Renders the inventory management section (Language, Set, Rarity, etc.).
        Shared by both Consolidated and Collectors views.
        """
        # Ensure set_base_code is valid or default
        if input_state['set_base_code'] not in set_options and default_set_base_code:
            input_state['set_base_code'] = default_set_base_code

        # Ensure image_id is valid or default
        if input_state['image_id'] is None and card.card_images:
            input_state['image_id'] = card.card_images[0].id

        # Store initial input state for comparison
        initial_check_state = {
            'set_base_code': input_state['set_base_code'],
            'rarity': input_state['rarity'],
            'image_id': input_state['image_id'],
            'language': input_state['language']
        }

        with ui.card().classes('w-full bg-transparent p-4 gap-4'):
            # Determine initial rarity options based on the current set code
            current_base_code = input_state['set_base_code']
            if rarity_map and current_base_code in rarity_map:
                rarity_options = sorted(list(rarity_map[current_base_code]))
            else:
                rarity_options = list(STANDARD_RARITIES)

            # Ensure current rarity is available in options to prevent crash
            if input_state['rarity'] not in rarity_options:
                rarity_options.append(input_state['rarity'])

            with ui.grid(columns=12).classes('w-full gap-2 items-center'):
                lang_select = ui.select(SUPPORTED_LANGUAGES, label='Language', value=input_state['language'],
                            on_change=lambda e: [input_state.update({'language': e.value}), on_change_callback()]).classes('col-span-2').props('dense options-dense dark')

                set_select = ui.select(set_options, label='Set Name', value=input_state['set_base_code']).classes('col-span-6').props('dense options-dense dark')

                rarity_select = ui.select(rarity_options, label='Rarity', value=input_state['rarity'],
                            on_change=lambda e: [input_state.update({'rarity': e.value}), on_change_callback()]).classes('col-span-4').props('dense options-dense dark')

                def on_set_change(e):
                    new_code = e.value
                    input_state['set_base_code'] = new_code

                    # Update Rarity Options
                    if rarity_map and new_code in rarity_map:
                        new_rarity_opts = sorted(list(rarity_map[new_code]))
                        rarity_select.options = new_rarity_opts
                        # Default to first available rarity if current is invalid
                        if rarity_select.value not in new_rarity_opts and new_rarity_opts:
                             input_state['rarity'] = new_rarity_opts[0]
                             rarity_select.value = new_rarity_opts[0]
                    else:
                        # Fallback if no strict map found (e.g. Custom Set)
                        rarity_select.options = STANDARD_RARITIES

                    if new_code in set_info_map:
                        s_info = set_info_map[new_code]
                        # Update rarity if available and compatible
                        if s_info.set_rarity and s_info.set_rarity in rarity_select.options:
                            input_state['rarity'] = s_info.set_rarity
                            rarity_select.value = s_info.set_rarity
                        elif rarity_select.options:
                            # If default not available, ensure we pick a valid one from options
                            if rarity_select.value not in rarity_select.options:
                                input_state['rarity'] = rarity_select.options[0]
                                rarity_select.value = rarity_select.options[0]

                    # Update language based on set code
                    extracted_lang = extract_language_code(new_code)
                    if extracted_lang in SUPPORTED_LANGUAGES:
                        input_state['language'] = extracted_lang
                        lang_select.value = extracted_lang

                    on_change_callback()

                set_select.on_value_change(on_set_change)

                ui.select(CARD_CONDITIONS, label='Condition', value=input_state['condition'],
                            on_change=lambda e: [input_state.update({'condition': e.value}), on_change_callback()]).classes('col-span-3').props('dense options-dense dark')

                # Storage Dropdown
                storage_opts = {None: 'None'}
                if storage_options:
                     storage_opts = storage_options
                elif current_collection and hasattr(current_collection, 'storage_definitions'):
                     for s in current_collection.storage_definitions:
                         storage_opts[s.name] = s.name

                ui.select(storage_opts, label='Storage', value=input_state.get('storage_location'),
                          on_change=lambda e: input_state.update({'storage_location': e.value})).classes('col-span-5').props('dense options-dense dark')

                ui.checkbox('1st Edition', value=input_state['first_edition'],
                            on_change=lambda e: [input_state.update({'first_edition': e.value}), on_change_callback()]).classes('col-span-2 my-auto').props('dense dark')

                ui.number('Quantity', min=0, value=input_state['quantity'],
                            on_change=lambda e: input_state.update({'quantity': int(e.value or 0)})).classes('col-span-2').props('dense dark')

                # Build Artwork Options
                art_options = {}
                # 1. Official Images
                if card.card_images:
                    for i, img in enumerate(card.card_images):
                        art_options[img.id] = f"Artwork {i+1} (ID: {img.id})"

                # 2. Collection Variants (Custom Arts)
                if current_collection:
                    for c in current_collection.cards:
                        if c.card_id == card.id:
                            for v in c.variants:
                                if v.image_id and v.image_id not in art_options:
                                    art_options[v.image_id] = f"Custom Art (ID: {v.image_id})"
                            break

                # 3. Ensure Current ID is present
                current_img_id = int(input_state['image_id']) if input_state['image_id'] is not None else None
                if current_img_id is not None and current_img_id not in art_options:
                     art_options[current_img_id] = f"Custom/Unknown (ID: {current_img_id})"

                # Render if we have options (usually > 1, but if current is custom and only 1 official exists, we have 2)
                # Or if we have 1 custom option and no official?
                if len(art_options) > 1:
                    ui.select(art_options, label='Artwork', value=current_img_id,
                                on_change=lambda e: [input_state.update({'image_id': e.value}), on_change_callback()]).classes('col-span-12').props('dense options-dense dark')

            with ui.row().classes('w-full gap-4 justify-end q-mt-md'):
                async def handle_update(mode, quantity_override: int = None):
                    base_code = input_state['set_base_code']
                    sel_rarity = input_state['rarity']
                    sel_img = input_state['image_id']

                    # Check if inputs still match original
                    is_original = (
                        base_code == initial_check_state['set_base_code'] and
                        sel_rarity == initial_check_state['rarity'] and
                        sel_img == initial_check_state['image_id'] and
                        input_state['language'] == initial_check_state['language']
                    )

                    # If inputs haven't changed and we have the original variant ID, use it.
                    if is_original and original_variant_id:
                        if mode == 'MOVE' and view_mode == 'collectors':
                             # Moving to same place = no-op
                             ui.notify('No changes detected.', type='warning')
                             return
                        await on_save_callback(mode, original_variant_id, quantity_override=quantity_override, storage_location=input_state.get('storage_location'))
                        return

                    # Calculate the target code based on language
                    final_code = transform_set_code(base_code, input_state['language'])

                    # Resolve target variant ID
                    matched_variant_id = None
                    if card.card_sets:
                        for s in card.card_sets:
                            s_img = s.image_id if s.image_id is not None else (card.card_images[0].id if card.card_images else None)
                            if s.set_code == final_code and s.set_rarity == sel_rarity and s_img == sel_img:
                                matched_variant_id = s.variant_id
                                break

                    if not matched_variant_id:
                         # Use generated ID check/creation
                         matched_variant_id = generate_variant_id(card.id, final_code, sel_rarity, sel_img)
                         # We might need to create it if it doesn't exist in DB, handled by CollectionEditor/Service generally,
                         # but here we ensured logic adds it if missing previously.
                         # Re-implementing ensure-variant logic:
                         s_name = set_info_map[base_code].set_name if base_code in set_info_map else "Custom Set"
                         # Optimistic: It will be created if missing by ygo_service if we call add_card_variant.
                         # Since we need to be sure for duplicate check, we should probably ensure it exists or use the generated ID for check.
                         # The ID is deterministic.

                    # Duplicate Check for MOVE (Collectors Mode)
                    if mode == 'MOVE' and view_mode == 'collectors' and current_collection:
                         # Check if target entry exists
                         qty = CollectionEditor.get_quantity(
                             current_collection,
                             card.id,
                             variant_id=matched_variant_id,
                             language=input_state['language'],
                             condition=input_state['condition'],
                             first_edition=input_state['first_edition'],
                             storage_location=input_state.get('storage_location')
                         )

                         if qty > 0:
                             with ui.dialog() as d, ui.card():
                                 ui.label(f"You already have {qty} copies of this card in the target configuration.").classes('text-lg')
                                 ui.label(f"Do you want to merge your {original_quantity} copies into it? (Total: {qty + original_quantity})")
                                 with ui.row().classes('w-full justify-end'):
                                     ui.button('Cancel', on_click=d.close).props('flat')
                                     async def do_merge():
                                         d.close()
                                         # Proceed with MOVE
                                         await on_save_callback(mode, matched_variant_id, quantity_override=quantity_override, storage_location=input_state.get('storage_location'))
                                     ui.button('Merge', on_click=do_merge).props('color=primary')
                             d.open()
                             return

                    # Normal flow
                    await on_save_callback(mode, matched_variant_id, quantity_override=quantity_override, storage_location=input_state.get('storage_location'))

                async def do_add():
                    await handle_update('ADD')

                async def do_subtract():
                    qty = int(input_state['quantity'] or 0)
                    if qty > 0:
                        await handle_update('ADD', quantity_override=-qty)
                    else:
                        ui.notify("Quantity must be > 0", type='warning')

                with ui.button('ADD', on_click=do_add).props('color=secondary'):
                    ui.tooltip('Add the specified quantity to your collection').classes('bg-black text-white')

                if view_mode == 'collectors':
                    with ui.button('SUBTRACT', on_click=do_subtract).props('color=warning text-color=dark'):
                        ui.tooltip('Subtract the specified quantity from your collection').classes('bg-black text-white')

                if show_remove_button:
                    async def confirm_remove():
                        with ui.dialog() as d, ui.card():
                            ui.label("Are you sure you want to remove this card variant from your collection?").classes('text-lg')
                            with ui.row().classes('w-full justify-end'):
                                ui.button('Cancel', on_click=d.close).props('flat')
                                async def do_remove():
                                    d.close()
                                    input_state['quantity'] = 0
                                    # In collectors mode, remove means quantity -> 0 or just delete.
                                    # We can reuse SET 0 or ADD -qty.
                                    # Using SET 0 is cleaner for "Remove".
                                    await handle_update('SET')
                                ui.button('Remove', on_click=do_remove).props('color=negative')
                        d.open()

                    with ui.button('REMOVE', on_click=confirm_remove).props('color=negative'):
                         ui.tooltip('Remove this entry from your collection').classes('bg-black text-white')

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
        save_callback: Callable,
        current_collection: Any = None,
        storage_options: Dict[str, str] = None
    ):
        try:
            with ui.dialog().props('maximized transition-show=slide-up transition-hide=slide-down') as d, ui.card().classes('w-full h-full p-0 no-shadow'):
                d.open()
                ui.button(icon='close', on_click=d.close).props('flat round color=white').classes('absolute top-2 right-2 z-50')

                with ui.row().classes('w-full h-full no-wrap gap-0'):
                    # Image Column
                    with ui.column().classes('w-1/3 min-w-[300px] h-full bg-black items-center justify-center p-8 shrink-0'):
                        img_id = card.get_best_image_id()
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
                        with ui.row().classes('gap-2 items-center'):
                            with ui.chip(icon='format_list_numbered').props('color=primary text-color=white'):
                                ui.label(f"Total: {total_owned}").classes('select-text')

                            if owned_breakdown:
                                for lang, count in owned_breakdown.items():
                                    with ui.chip(icon='layers').props('color=secondary text-color=white'):
                                        ui.label(f"{lang}: {count}").classes('select-text')
                            elif total_owned == 0:
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
                            rarity_map = {}

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

                                    if code not in rarity_map:
                                        rarity_map[code] = set()
                                    rarity_map[code].add(s.set_rarity)
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

                            async def on_save_wrapper(mode, variant_id, quantity_override: int = None, storage_location: str = None):
                                # In Consolidated view, we probably just add/set.
                                # Logic:
                                # 1. Calculate final set code
                                final_set_code = transform_set_code(input_state['set_base_code'], input_state['language'])

                                qty = quantity_override if quantity_override is not None else input_state['quantity']

                                # 2. Call save callback
                                await save_callback(
                                    card,
                                    final_set_code,
                                    input_state['rarity'],
                                    input_state['language'],
                                    qty,
                                    input_state['condition'],
                                    input_state['first_edition'],
                                    input_state['image_id'],
                                    variant_id,
                                    mode, # Pass mode (SET/ADD) to handle logic in save_card_change or wrapper
                                    storage_location=storage_location
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
                                default_set_base_code=default_set_code,
                                show_remove_button=False,
                                rarity_map=rarity_map,
                                view_mode='consolidated',
                                current_collection=current_collection,
                                storage_options=storage_options
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
        save_callback: Callable = None,
        variant_id: str = None,
        hide_header_stats: bool = False,
        storage_options: Dict[str, str] = None
    ):
        try:
            set_options = {}
            set_info_map = {}
            rarity_map = {}

            if card.card_sets:
                for s in card.card_sets:
                    code = s.set_code
                    if code not in set_options:
                        set_options[code] = f"{s.set_name} ({code})"
                        set_info_map[code] = s

                    if code not in rarity_map:
                        rarity_map[code] = set()
                    rarity_map[code].add(s.set_rarity)
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

            def get_ownership_text(set_base_code, rarity, image_id, language, condition, first_edition):
                cur_owned = 0
                storage_breakdown = {}
                final_code = transform_set_code(set_base_code, language)

                # BulkAdd might save cards with the original base code (e.g. LOB-EN001) even if language is DE.
                # So we check both the transformed code (LOB-DE001) and the base code (LOB-EN001).
                target_codes = {final_code}
                if set_base_code:
                    target_codes.add(set_base_code)

                if current_collection:
                    for c in current_collection.cards:
                        if c.card_id == card.id:
                            for v in c.variants:
                                if v.set_code in target_codes and v.rarity == rarity and v.image_id == image_id:
                                    for e in v.entries:
                                        if e.language == language and e.condition == condition and e.first_edition == first_edition:
                                            cur_owned += e.quantity
                                            loc = e.storage_location if e.storage_location else "Unsorted"
                                            storage_breakdown[loc] = storage_breakdown.get(loc, 0) + e.quantity
                                    # Don't break here, we might have multiple variants matching (e.g. one EN code, one DE code)
                                    # break
                            break

                text = str(cur_owned)
                if cur_owned > 0 and storage_breakdown:
                    parts = []
                    for loc, qty in sorted(storage_breakdown.items()):
                        parts.append(f"[{loc}]: {qty}")
                    text += f" | Locations {', '.join(parts)}"
                return cur_owned, text

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

                        initial_owned_qty, initial_owned_text = get_ownership_text(
                            initial_base_code, rarity, image_id, language, condition, first_edition
                        )
                        # Fallback to passed owned_count if calculation fails (e.g. mismatch), though calculation is preferred for breakdown
                        if initial_owned_qty == 0 and owned_count > 0:
                             # This happens if there's a mismatch in finding the variant/entries by properties
                             # We use the simple count but lose the breakdown
                             initial_owned_qty = owned_count
                             initial_owned_text = str(owned_count)

                        with ui.row().classes('items-center gap-2'):
                             ui.label('Total Owned:').classes('text-lg text-gray-400 font-bold')
                             owned_label = ui.label(initial_owned_text).classes('text-2xl font-bold text-accent')
                             with owned_label:
                                ui.tooltip('Owned Count')

                        if initial_owned_qty == 0:
                            owned_label.set_visibility(False)

                        if hide_header_stats:
                            # Hide total owned section if requested (or just the count, prompt says 'no Total owned')
                            owned_label.parent_slot.parent.set_visibility(False)

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

                                if not hide_header_stats:
                                    lbl_lang = info_label('Language', language)
                                    lbl_cond = info_label('Condition', condition)
                                    lbl_edition = info_label('Edition', "1st Edition" if first_edition else "Unlimited")
                                else:
                                    # Create placeholders or just skip?
                                    # Since we use variables later in update_display_stats, we must define them.
                                    # But we can hide the UI elements.
                                    # Or simpler: Define them but set visibility false.
                                    lbl_lang = info_label('Language', language)
                                    lbl_lang.parent_slot.parent.set_visibility(False)
                                    lbl_cond = info_label('Condition', condition)
                                    lbl_cond.parent_slot.parent.set_visibility(False)
                                    lbl_edition = info_label('Edition', "1st Edition" if first_edition else "Unlimited")
                                    lbl_edition.parent_slot.parent.set_visibility(False)

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

                            cur_owned, text = get_ownership_text(
                                input_state['set_base_code'],
                                input_state['rarity'],
                                input_state['image_id'],
                                input_state['language'],
                                input_state['condition'],
                                input_state['first_edition']
                            )

                            owned_label.text = text
                            owned_label.set_visibility(cur_owned > 0)

                            update_image()

                        ui.separator().classes('q-my-md')

                        inventory_expansion = ui.expansion().classes('w-full bg-gray-800 rounded').props('icon=edit label="Manage Inventory"')
                        inventory_expansion.value = True

                        with inventory_expansion:
                             async def on_save_wrapper(mode, target_variant_id, quantity_override: int = None, storage_location: str = None):
                                final_set_code = transform_set_code(input_state['set_base_code'], input_state['language'])

                                qty = quantity_override if quantity_override is not None else input_state['quantity']

                                extra_args = {}
                                if mode == 'MOVE':
                                    extra_args = {
                                        'source_variant_id': variant_id,
                                        'source_language': language,
                                        'source_condition': condition,
                                        'source_first_edition': first_edition,
                                        'source_quantity': owned_count
                                    }

                                await save_callback(
                                    card,
                                    final_set_code,
                                    input_state['rarity'],
                                    input_state['language'],
                                    qty,
                                    input_state['condition'],
                                    input_state['first_edition'],
                                    input_state['image_id'],
                                    target_variant_id,
                                    mode,
                                    storage_location=storage_location,
                                    **extra_args
                                )
                                d.close()

                             self._render_inventory_management(
                                card=card,
                                input_state=input_state,
                                set_options=set_options,
                                set_info_map=set_info_map,
                                on_change_callback=update_display_stats,
                                on_save_callback=on_save_wrapper,
                                default_set_base_code=initial_base_code,
                                original_variant_id=variant_id,
                                rarity_map=rarity_map,
                                view_mode='collectors',
                                current_collection=current_collection,
                                original_quantity=owned_count,
                                storage_options=storage_options
                            )

                        self._render_available_sets(card)

        except Exception as e:
            logger.error(f"ERROR in render_collectors_single_view: {e}", exc_info=True)

    async def open_deck_builder(
        self,
        card: ApiCard,
        on_add_callback: Callable[[int, int, str], Any],
        owned_count: int = 0,
        owned_breakdown: Dict[str, int] = None
    ):
        try:
             with ui.dialog().props('maximized transition-show=slide-up transition-hide=slide-down') as d, ui.card().classes('w-full h-full p-0 no-shadow'):
                d.open()
                ui.button(icon='close', on_click=d.close).props('flat round color=white').classes('absolute top-2 right-2 z-50')

                with ui.row().classes('w-full h-full no-wrap gap-0'):
                    # Image Column (Simplified, just use default/first image)
                    with ui.column().classes('w-1/3 min-w-[300px] h-full bg-black items-center justify-center p-8 shrink-0'):
                         img_id = card.get_best_image_id()
                         url = card.card_images[0].image_url if card.card_images else None
                         small_url = card.card_images[0].image_url_small if card.card_images else None
                         image_element = ui.image().classes('max-h-full max-w-full object-contain shadow-2xl')
                         self._setup_high_res_image_logic(img_id, url, small_url, image_element)

                    with ui.column().classes('col h-full bg-gray-900 text-white p-8 scroll-y-auto'):
                         # Basic Info
                         with ui.row().classes('w-full items-center justify-between'):
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
                                 stat('Archetype', card.archetype or '-')
                             else:
                                 stat('Race', card.race)
                                 stat('Archetype', card.archetype or '-')

                         ui.markdown(card.desc).classes('text-gray-300 leading-relaxed text-lg q-mb-md')

                         ui.separator().classes('q-my-md bg-gray-700')
                         ui.label('Collection Status').classes('text-h6 q-mb-sm text-accent')
                         with ui.row().classes('gap-2 items-center'):
                             with ui.chip(icon='format_list_numbered').props('color=primary text-color=white'):
                                 ui.label(f"Total: {owned_count}").classes('select-text')

                             if owned_breakdown:
                                 for lang, count in owned_breakdown.items():
                                     with ui.chip(icon='layers').props('color=secondary text-color=white'):
                                         ui.label(f"{lang}: {count}")
                             elif owned_count == 0:
                                 ui.label('Not in collection').classes('text-gray-500 italic')

                         ui.separator().classes('q-my-md bg-gray-700')

                         # Add to Deck Section
                         ui.label('Add to Deck').classes('text-h6 q-mb-sm text-accent')

                         qty_input = ui.number('Quantity', value=1, min=1, max=3).classes('w-32').props('dark')

                         with ui.row().classes('gap-4 q-mt-md'):
                             async def add(target):
                                 qty = int(qty_input.value or 1)
                                 await on_add_callback(card.id, qty, target)
                                 d.close()

                             async def add_main(): await add('main')
                             async def add_side(): await add('side')
                             async def add_extra(): await add('extra')

                             if not card.is_extra_deck:
                                 ui.button('Add to Main', on_click=add_main).props('color=positive icon=add')

                             ui.button('Add to Side', on_click=add_side).props('color=warning text-color=dark icon=add')

                             if card.is_extra_deck:
                                 ui.button('Add to Extra', on_click=add_extra).props('color=purple icon=add')

                         self._render_available_sets(card)

        except Exception as e:
            logger.error(f"Error opening deck builder view: {e}", exc_info=True)

    def _open_new_art_dialog(self, on_success: Callable[[int], None], on_cancel: Callable[[], None]):
        # State to hold image data (bytes)
        new_art_state = {
            'low_res': None,
            'high_res': None
        }

        # UI Elements references for updates
        previews = {
            'low_res': None,
            'high_res': None
        }

        with ui.dialog() as new_art_d, ui.card().classes('w-[90vw] max-w-5xl bg-gray-900 border border-gray-700'):

            def on_close():
                on_cancel()

            new_art_d.on('close', on_close)

            ui.label('Add New Artstyle').classes('text-h5 text-white q-mb-md')
            ui.label('Both Low Resolution (Standard) and High Resolution images are required.').classes('text-gray-400 q-mb-lg')

            def update_preview(key, content: bytes):
                if not content: return
                try:
                    # Convert bytes to base64 for preview
                    b64 = base64.b64encode(content).decode('utf-8')
                    src = f"data:image/jpeg;base64,{b64}"
                    if previews[key]:
                        previews[key].source = src
                        previews[key].update()
                except Exception as e:
                    logger.error(f"Error updating preview for {key}: {e}")

            async def process_image_input(key, content: bytes):
                try:
                    # Validate/Convert to JPEG
                    img = Image.open(io.BytesIO(content))
                    img = img.convert('RGB')
                    out = io.BytesIO()
                    img.save(out, 'JPEG', quality=90)
                    final_content = out.getvalue()

                    new_art_state[key] = final_content
                    update_preview(key, final_content)
                    ui.notify(f"{'Low' if key == 'low_res' else 'High'} Res image updated!", type='positive')
                except Exception as e:
                    logger.error(f"Error processing image input: {e}")
                    ui.notify(f"Invalid image data: {e}", type='negative')

            def render_input_column(key, title):
                with ui.column().classes('w-full flex-1 p-4 bg-gray-800 rounded border border-gray-700'):
                    ui.label(title).classes('text-lg font-bold text-white mb-2')

                    # Preview Area
                    with ui.element('div').classes('w-full aspect-[2/3] bg-black mb-4 flex items-center justify-center overflow-hidden rounded relative'):
                        previews[key] = ui.image().classes('w-full h-full object-contain')
                        ui.label('No Image').classes('absolute text-gray-500 text-sm')

                    # Inputs
                    ui.label('Input Method (New input overwrites existing)').classes('text-xs text-gray-400 mb-1')

                    # 1. URL
                    url_input = ui.input('Image URL').props('dark dense').classes('w-full')

                    async def download_url():
                        url = url_input.value
                        if not url: return
                        ui.notify('Downloading...', type='info')
                        try:
                            resp = await run.io_bound(requests.get, url)
                            if resp.status_code == 200:
                                await process_image_input(key, resp.content)
                            else:
                                ui.notify(f"Download failed: {resp.status_code}", type='negative')
                        except Exception as e:
                            ui.notify(f"Error: {e}", type='negative')

                    ui.button('Download URL', on_click=download_url).props('color=secondary icon=cloud_download dense').classes('w-full mb-2')

                    # 2. Clipboard
                    async def paste_clipboard():
                        try:
                            js_code = """
                            (async () => {
                                try {
                                    const items = await navigator.clipboard.read();
                                    for (const item of items) {
                                        const imageType = item.types.find(type => type.startsWith('image/'));
                                        if (imageType) {
                                            const blob = await item.getType(imageType);
                                            return await new Promise((resolve) => {
                                                const reader = new FileReader();
                                                reader.onload = () => resolve(reader.result);
                                                reader.readAsDataURL(blob);
                                            });
                                        }
                                    }
                                    return null;
                                } catch (err) {
                                    return 'ERROR: ' + err.message;
                                }
                            })()
                            """
                            data_url = await ui.run_javascript(js_code, timeout=5.0)

                            if not data_url:
                                ui.notify('No image found in clipboard.', type='warning')
                                return

                            if isinstance(data_url, str) and data_url.startswith('ERROR:'):
                                ui.notify(f"Clipboard Error: {data_url[7:]}", type='negative')
                                return

                            if ',' in data_url:
                                _, encoded = data_url.split(',', 1)
                                content = base64.b64decode(encoded)
                                await process_image_input(key, content)
                            else:
                                ui.notify('Invalid clipboard data.', type='negative')
                        except Exception as e:
                            ui.notify(f"Paste failed: {e}", type='negative')

                    ui.button('Paste Clipboard', on_click=paste_clipboard).props('color=accent icon=content_paste dense').classes('w-full mb-2')

                    # 3. Upload
                    async def handle_upload(e):
                        try:
                            file_obj = getattr(e, 'content', getattr(e, 'file', None))
                            if not file_obj:
                                ui.notify("No file content found", type='negative')
                                return
                            content = await file_obj.read()
                            await process_image_input(key, content)
                        except Exception as err:
                            logger.error(f"Upload error: {err}")
                            ui.notify(f"Upload failed: {err}", type='negative')

                    ui.upload(on_upload=handle_upload, auto_upload=True).props('accept=".jpg,.jpeg,.png,.webp" dark dense flat').classes('w-full')

            # Layout
            with ui.row().classes('w-full gap-4'):
                render_input_column('low_res', 'Low Resolution (Standard)')
                render_input_column('high_res', 'High Resolution')

            ui.separator().classes('my-4 bg-gray-600')

            # Footer Actions
            with ui.row().classes('w-full justify-end gap-4'):
                ui.button('Cancel', on_click=new_art_d.close).props('flat color=white')

                async def save_final():
                    if not new_art_state['low_res'] or not new_art_state['high_res']:
                        ui.notify('Both Low and High resolution images are required.', type='warning')
                        return

                    # Generate ID
                    while True:
                        new_id = random.randint(100000000, 999999999)
                        if not image_manager.image_exists(new_id):
                            break

                    # Save Files
                    try:
                        # Ensure data dir exists
                        # get_local_path returns full path including filename
                        path_low = image_manager.get_local_path(new_id, high_res=False)
                        os.makedirs(os.path.dirname(path_low), exist_ok=True)

                        with open(path_low, 'wb') as f:
                            f.write(new_art_state['low_res'])

                        path_high = image_manager.get_local_path(new_id, high_res=True)
                        with open(path_high, 'wb') as f:
                            f.write(new_art_state['high_res'])

                        # Success
                        ui.notify('New artwork saved!', type='positive')

                        # Disable on_close handler to prevent cancel callback
                        new_art_d.on('close', None)
                        new_art_d.close()

                        on_success(new_id)

                    except Exception as e:
                        logger.error(f"Error saving new artwork files: {e}")
                        ui.notify(f"Save failed: {e}", type='negative')

                ui.button('Save Picture', on_click=save_final).props('color=positive icon=save')

        new_art_d.open()

    async def open_db_edit_view(
        self,
        card: ApiCard,
        variant_id: str,
        set_code: str,
        rarity: str,
        image_id: int,
        on_save_callback: Callable[[str, str, int], Any],
        on_delete_callback: Callable[[], Any] = None,
        on_add_callback: Callable[[str, str, int], Any] = None,
        known_variants: List[Any] = None
    ):
        try:
            input_state = {
                'set_code': set_code,
                'rarity': rarity,
                'image_id': image_id
            }

            with ui.dialog().props('maximized transition-show=slide-up transition-hide=slide-down') as d, ui.card().classes('w-full h-full p-0 no-shadow'):
                d.open()
                ui.button(icon='close', on_click=d.close).props('flat round color=white').classes('absolute top-2 right-2 z-50')

                with ui.row().classes('w-full h-full no-wrap gap-0'):
                    # Image Column
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
                                # Fallback if specific ID not found in images (legacy/custom)
                                if not low_res_url:
                                    low_res_url = card.card_images[0].image_url_small if card.card_images else None

                            self._setup_high_res_image_logic(
                                img_id,
                                high_res_remote_url,
                                low_res_url,
                                image_element,
                                current_id_check=lambda: input_state['image_id'] == img_id
                            )

                        update_image()

                    with ui.column().classes('col h-full bg-gray-900 text-white p-8 scroll-y-auto'):
                        # Header
                        ui.label(f"Edit Database Entry: {card.name}").classes('text-2xl font-bold text-accent q-mb-md')
                        ui.label(f"Card ID: {card.id}").classes('text-xs text-gray-500 font-mono')
                        ui.label(f"Variant ID: {variant_id}").classes('text-xs text-gray-500 font-mono q-mb-xl')

                        # Form
                        with ui.card().classes('w-full bg-gray-800 p-6 gap-6'):
                            ui.label('Edit Variant Details').classes('text-h6 text-white')

                            # Set Code Input
                            ui.input('Set Code', value=input_state['set_code'],
                                     on_change=lambda e: input_state.update({'set_code': e.value})).classes('w-full').props('dark')

                            # Rarity Select
                            rarity_options = list(STANDARD_RARITIES)
                            if input_state['rarity'] not in rarity_options:
                                rarity_options.append(input_state['rarity'])

                            ui.select(rarity_options, label='Rarity', value=input_state['rarity'],
                                      on_change=lambda e: input_state.update({'rarity': e.value})).classes('w-full').props('dark')

                            # Image/Artstyle Select
                            art_options = {}
                            if card.card_images:
                                for i, img in enumerate(card.card_images):
                                    art_options[img.id] = f"Artwork {i+1} (ID: {img.id})"

                            # Add custom arts from other variants
                            if known_variants:
                                for v in known_variants:
                                    vid = v.image_id
                                    if vid and vid not in art_options:
                                        art_options[vid] = f"Custom (ID: {vid})"

                            if card.card_sets:
                                for cset in card.card_sets:
                                    vid = cset.image_id
                                    if vid and vid not in art_options:
                                        art_options[vid] = f"Custom (ID: {vid})"

                            # Ensure current image_id is in options
                            if input_state['image_id'] not in art_options:
                                art_options[input_state['image_id']] = f"Custom/Unknown (ID: {input_state['image_id']})"

                            art_options['NEW'] = "+ New Artstyle"

                            art_select = ui.select(art_options, label='Artwork / Image ID', value=input_state['image_id'])
                            art_select.classes('w-full').props('dark')

                            def open_new_art_dialog():
                                def on_success(new_id):
                                    art_options[new_id] = f"Custom Art (ID: {new_id})"
                                    art_select.options = art_options
                                    art_select.value = new_id
                                    art_select.update()

                                def on_cancel():
                                    if art_select.value == 'NEW':
                                        art_select.value = input_state['image_id']

                                self._open_new_art_dialog(on_success, on_cancel)

                            def on_art_change(e):
                                if e.value == 'NEW':
                                    open_new_art_dialog()
                                else:
                                    input_state['image_id'] = e.value
                                    update_image()

                            art_select.on_value_change(on_art_change)

                            ui.separator().classes('q-my-md bg-gray-600')

                            # Actions
                            with ui.row().classes('w-full justify-between gap-4'):
                                with ui.row().classes('items-center gap-2'):
                                    if on_delete_callback:
                                        async def confirm_delete():
                                            with ui.dialog() as del_d, ui.card():
                                                ui.label('Are you sure you want to delete this variant?').classes('text-lg font-bold')
                                                ui.label('This cannot be undone. Deleted cards can only be restored via the API.')
                                                with ui.row().classes('w-full justify-end'):
                                                    ui.button('Cancel', on_click=del_d.close).props('flat')
                                                    async def do_delete():
                                                        del_d.close()
                                                        await on_delete_callback()
                                                        d.close()
                                                    ui.button('Delete', on_click=do_delete).props('color=negative')
                                            del_d.open()

                                        ui.button('Delete Variant', on_click=confirm_delete).props('color=negative icon=delete flat')

                                    if on_add_callback:
                                        async def do_add():
                                            await on_add_callback(
                                                input_state['set_code'],
                                                input_state['rarity'],
                                                input_state['image_id']
                                            )
                                        ui.button('Add Variant', on_click=do_add).props('color=secondary icon=add_circle outline')

                                with ui.row().classes('gap-4'):
                                    ui.button('Cancel', on_click=d.close).props('flat color=white')

                                    async def save():
                                        success = await on_save_callback(
                                            input_state['set_code'],
                                            input_state['rarity'],
                                            input_state['image_id']
                                        )
                                        if success:
                                            d.close()
                                            ui.notify('Changes saved to database.', type='positive')
                                        else:
                                            ui.notify('Failed to save changes.', type='negative')

                                    ui.button('Save Changes', on_click=save).props('color=positive icon=save')

        except Exception as e:
            logger.error(f"Error opening db edit view: {e}", exc_info=True)

    async def open_db_consolidated_view(
        self,
        card: ApiCard,
        variants: List[Any],
        on_apply_art: Callable[[List[str], int], Any],
        on_add_variant: Callable[[List[Any], int], Any]
    ):
        try:
            # Prepare initial state
            # Sort variants by Set Code
            sorted_variants = sorted(variants, key=lambda x: x.set_code)

            # Default selected image ID (first available)
            default_image_id = None
            if card.card_images:
                default_image_id = card.card_images[0].id
            elif variants:
                default_image_id = variants[0].image_id

            state = {
                'selected_image_id': default_image_id,
                'selected_variant_ids': set(),  # Set of selected variant IDs
                'all_selected': False
            }

            with ui.dialog().props('maximized transition-show=slide-up transition-hide=slide-down') as d, ui.card().classes('w-full h-full p-0 no-shadow'):
                d.open()
                ui.button(icon='close', on_click=d.close).props('flat round color=white').classes('absolute top-2 right-2 z-50')

                with ui.row().classes('w-full h-full no-wrap gap-0'):
                    # --- Left Column: Image Preview ---
                    with ui.column().classes('w-1/3 min-w-[300px] h-full bg-black items-center justify-center p-8 shrink-0'):
                        image_element = ui.image().classes('max-h-full max-w-full object-contain shadow-2xl')

                        def update_preview_image():
                            img_id = state['selected_image_id']
                            high_res_remote_url = None
                            low_res_url = None

                            if card.card_images:
                                for img in card.card_images:
                                    if img.id == img_id:
                                        high_res_remote_url = img.image_url
                                        low_res_url = img.image_url_small
                                        break

                            # Fallback
                            if not low_res_url:
                                low_res_url = card.card_images[0].image_url_small if card.card_images else None

                            self._setup_high_res_image_logic(
                                img_id,
                                high_res_remote_url,
                                low_res_url,
                                image_element,
                                current_id_check=lambda: state['selected_image_id'] == img_id
                            )

                        update_preview_image()

                    # --- Right Column: List & Controls ---
                    with ui.column().classes('col h-full bg-gray-900 text-white p-8 scroll-y-auto'):
                        # Header
                        ui.label(f"Consolidated View: {card.name}").classes('text-3xl font-bold text-white q-mb-md')
                        ui.label(f"Card ID: {card.id}").classes('text-xs text-gray-500 font-mono q-mb-xs')
                        ui.label(f"Total Variants: {len(sorted_variants)}").classes('text-gray-400 q-mb-lg')

                        # --- Variant List ---
                        with ui.card().classes('w-full bg-gray-800 p-0 flex-grow overflow-hidden flex flex-col'):
                            # Table Header
                            with ui.grid(columns=12).classes('w-full bg-gray-700 p-2 items-center text-gray-300 font-bold text-sm gap-2'):
                                # Select All Checkbox
                                select_all_cb = ui.checkbox(value=False).props('dense dark').classes('col-span-1 flex justify-center')
                                ui.label('Set Code').classes('col-span-4')
                                ui.label('Rarity').classes('col-span-4')
                                ui.label('Image ID').classes('col-span-3 text-right')

                            # Scrollable List Area
                            with ui.scroll_area().classes('flex-grow w-full p-2'):
                                checkboxes = {}
                                updating_batch = [False]  # Mutable flag for closure

                                for v in sorted_variants:
                                    with ui.grid(columns=12).classes('w-full p-2 items-center hover:bg-gray-700 rounded transition border-b border-gray-700 gap-2'):
                                        cb = ui.checkbox(value=False).props('dense dark').classes('col-span-1 flex justify-center')
                                        checkboxes[v.variant_id] = cb

                                        def on_cb_change(e, vid=v.variant_id):
                                            if updating_batch[0]:
                                                return

                                            if e.value:
                                                state['selected_variant_ids'].add(vid)
                                                # Check if all selected now? (Optional optimization)
                                            else:
                                                state['selected_variant_ids'].discard(vid)
                                                state['all_selected'] = False
                                                if select_all_cb.value:
                                                    updating_batch[0] = True
                                                    select_all_cb.value = False
                                                    updating_batch[0] = False

                                        cb.on_value_change(on_cb_change)

                                        # Row Content
                                        ui.label(v.set_code).classes('col-span-4 font-mono text-yellow-500 font-bold')
                                        ui.label(v.rarity).classes('col-span-4 truncate')
                                        ui.label(str(v.image_id)).classes('col-span-3 text-gray-400 text-right font-mono')

                                # Select All Logic
                                def toggle_select_all(e):
                                    if updating_batch[0]:
                                        return

                                    is_checked = e.value
                                    state['all_selected'] = is_checked
                                    state['selected_variant_ids'] = set()

                                    updating_batch[0] = True
                                    try:
                                        for vid, cb in checkboxes.items():
                                            if cb.value != is_checked:
                                                cb.value = is_checked
                                                cb.update() # Ensure UI update
                                            if is_checked:
                                                state['selected_variant_ids'].add(vid)
                                    finally:
                                        updating_batch[0] = False

                                select_all_cb.on_value_change(toggle_select_all)


                        # --- Controls Panel ---
                        with ui.card().classes('w-full bg-gray-800 p-4 mt-4 border-t border-gray-600'):
                            ui.label('Batch Actions').classes('text-h6 text-white mb-2')

                            with ui.row().classes('w-full items-end gap-4'):
                                # Art Style Selector
                                art_options = {}
                                if card.card_images:
                                    for i, img in enumerate(card.card_images):
                                        art_options[img.id] = f"Artwork {i+1} (ID: {img.id})"

                                # Add custom arts from other variants
                                for v in variants:
                                    vid = v.image_id
                                    if vid and vid not in art_options:
                                        art_options[vid] = f"Custom (ID: {vid})"

                                # Ensure default is in options
                                if state['selected_image_id'] and state['selected_image_id'] not in art_options:
                                    art_options[state['selected_image_id']] = f"Custom ({state['selected_image_id']})"

                                art_options['NEW'] = "+ New Artstyle"

                                art_select = ui.select(art_options, label='Target Art Style', value=state['selected_image_id'])
                                art_select.classes('flex-grow').props('dark')

                                def on_art_change(e):
                                    if e.value == 'NEW':
                                        def on_success(new_id):
                                            # Update local options
                                            art_options[new_id] = f"Custom Art (ID: {new_id})"
                                            art_select.options = art_options

                                            # Update state and selection
                                            state['selected_image_id'] = new_id
                                            art_select.value = new_id
                                            art_select.update()

                                            update_preview_image()

                                        def on_cancel():
                                            if art_select.value == 'NEW':
                                                art_select.value = state['selected_image_id']

                                        self._open_new_art_dialog(on_success, on_cancel)
                                    else:
                                        state['selected_image_id'] = e.value
                                        update_preview_image()

                                art_select.on_value_change(on_art_change)

                                # Buttons
                                async def do_apply():
                                    selected = list(state['selected_variant_ids'])
                                    if not selected:
                                        ui.notify('No variants selected.', type='warning')
                                        return

                                    await on_apply_art(selected, state['selected_image_id'])
                                    d.close()

                                async def do_add():
                                    selected_ids = list(state['selected_variant_ids'])
                                    if not selected_ids:
                                        ui.notify('No variants selected.', type='warning')
                                        return

                                    # Find the actual variant objects
                                    selected_objs = [v for v in sorted_variants if v.variant_id in state['selected_variant_ids']]

                                    await on_add_variant(selected_objs, state['selected_image_id'])
                                    d.close()

                                ui.button('Apply Art', on_click=do_apply).props('color=primary icon=brush')
                                ui.button('Add as New Variant', on_click=do_add).props('color=secondary icon=add_circle')

        except Exception as e:
            logger.error(f"Error opening consolidated view: {e}", exc_info=True)
