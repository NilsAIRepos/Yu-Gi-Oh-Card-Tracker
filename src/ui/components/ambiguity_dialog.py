from nicegui import ui, run
from typing import Dict, Any, List, Optional, Callable
import logging
import asyncio

from src.core.utils import is_set_code_compatible, normalize_set_code, transform_set_code, REGION_TO_LANGUAGE_MAP
from src.services.ygo_api import ygo_service
from src.services.image_manager import image_manager

logger = logging.getLogger(__name__)

class AmbiguityDialog(ui.dialog):
    def __init__(self, scan_result: Dict[str, Any], on_confirm: Callable):
        super().__init__()
        self.result = scan_result
        self.on_confirm_cb = on_confirm

        # Initial Candidates from Scan
        self.candidates = scan_result.get('candidates', [])

        # State
        self.card_id = self.candidates[0]['card_id'] if self.candidates else None
        self.full_card_data = None # Will be loaded async
        self.english_card_data = None # Will be loaded async

        # Initial Selection (Best Guess)
        self.selected_name = scan_result.get('name') or (self.candidates[0]['name'] if self.candidates else "Unknown")
        self.selected_set_code = scan_result.get('set_code')
        self.other_set_code_val = ""

        self.selected_rarity = scan_result.get('rarity') or scan_result.get('visual_rarity')
        self.selected_language = scan_result.get('language', 'EN')
        self.selected_first_ed = scan_result.get('first_edition', False)

        # Image/Artstyle
        # Default to the image_id of the best candidate
        self.selected_image_id = None
        if self.candidates:
             self.selected_image_id = self.candidates[0].get('image_id')

        # Controls
        self.preview_image = None
        self.name_select = None
        self.set_code_select = None
        self.other_set_input = None
        self.artstyle_select = None
        self.rarity_select = None

        self.ocr_set_id = scan_result.get('raw_ocr', [{}])[0].get('set_id')

        # Trigger async load
        ui.timer(0.1, self.load_full_data, once=True)

        with self, ui.card().classes('w-[900px] h-[700px] flex flex-row p-4 gap-4'):
             # LEFT: Image Preview
             with ui.column().classes('w-1/3 h-full items-center justify-center bg-black rounded'):
                 self.preview_image = ui.image().classes('max-w-full max-h-full object-contain')
                 self.update_preview()

             # RIGHT: Controls
             with ui.column().classes('flex-grow h-full gap-2'):
                 ui.label("Resolve Ambiguity").classes('text-xl font-bold mb-2')

                 # 1. Card Name (Dropdown if multiple candidates have diff names)
                 unique_names = sorted(list(set(c['name'] for c in self.candidates)))
                 if len(unique_names) > 1:
                     self.name_select = ui.select(
                         options=unique_names,
                         value=self.selected_name,
                         label="Card Name",
                         on_change=self.on_name_change
                     ).classes('w-full')
                 else:
                     ui.label(self.selected_name).classes('text-lg font-bold text-primary mb-2')

                 # 2. Language
                 ui.select(
                     options=['EN', 'DE', 'FR', 'IT', 'ES', 'PT', 'JP', 'KR'],
                     value=self.selected_language,
                     label="Language",
                     on_change=self.on_language_change
                 ).classes('w-full')

                 # 3. Set Code
                 # Initialize options with candidates to avoid "Invalid value" error
                 initial_set_codes = sorted(list(set(c['set_code'] for c in self.candidates)))
                 if self.selected_set_code and self.selected_set_code not in initial_set_codes:
                     initial_set_codes.append(self.selected_set_code)
                 initial_set_codes.append("Other")

                 self.set_code_select = ui.select(
                     options=initial_set_codes,
                     value=self.selected_set_code,
                     label="Set Code",
                     on_change=self.on_set_code_change
                 ).classes('w-full')

                 # 4. Other Set Code Input (Conditional)
                 self.other_set_input = ui.input(
                     label="Custom Set Code",
                     value=self.ocr_set_id if self.ocr_set_id else "",
                     on_change=lambda e: setattr(self, 'other_set_code_val', e.value)
                 ).classes('w-full').bind_visibility_from(self.set_code_select, 'value', lambda x: x == "Other")

                 # 5. Artstyle (Image ID)
                 # Initialize options from candidates
                 initial_images = sorted(list(set(c.get('image_id') for c in self.candidates if c.get('image_id'))))
                 if self.selected_image_id and self.selected_image_id not in initial_images:
                     initial_images.append(self.selected_image_id)

                 initial_art_opts = {i: f"Art Variation (ID: {i})" for i in initial_images}

                 self.artstyle_select = ui.select(
                     options=initial_art_opts,
                     value=self.selected_image_id,
                     label="Artstyle",
                     on_change=self.on_artstyle_change
                 ).classes('w-full')

                 # 6. Rarity
                 # Initialize options from candidates
                 initial_rarities = sorted(list(set(c.get('rarity') for c in self.candidates if c.get('rarity'))))
                 if self.selected_rarity and self.selected_rarity not in initial_rarities:
                     initial_rarities.append(self.selected_rarity)

                 self.rarity_select = ui.select(
                     options=initial_rarities,
                     value=self.selected_rarity,
                     label="Rarity",
                     on_change=lambda e: setattr(self, 'selected_rarity', e.value)
                 ).classes('w-full')

                 # 7. 1st Edition
                 ui.checkbox("1st Edition", value=self.selected_first_ed,
                             on_change=lambda e: setattr(self, 'selected_first_ed', e.value)).classes('mt-2')

                 ui.space()

                 # Buttons
                 with ui.row().classes('w-full justify-end gap-2'):
                     ui.button("Cancel", on_click=self.close, color='secondary') # Cancel is Secondary
                     ui.button("Confirm", on_click=self.confirm, color='primary') # Confirm is Primary (Highlighted)

    async def load_full_data(self):
        """Loads full card data from API/DB to populate all variants."""
        if not self.card_id: return

        try:
            # 1. Fetch Target Language Data
            self.full_card_data = await run.io_bound(ygo_service.get_card, self.card_id, self.selected_language.lower())

            # 2. Fetch English Data (Always)
            # Optimization: If selected is English, we can reuse it, but safer to separate references or just use one logic.
            if self.selected_language.lower() == 'en':
                self.english_card_data = self.full_card_data
            else:
                self.english_card_data = await run.io_bound(ygo_service.get_card, self.card_id, 'en')

            self.update_options()
        except Exception as e:
            logger.error(f"Failed to load full card data: {e}")
            # Fallback to candidates only
            self.update_options()

    def _get_current_variants(self):
        """
        Helper to get list of variant dicts based on current loaded data.
        Implements merging of DB variants and Synthetic (transformed) variants.
        """
        variants = []
        seen_keys = set() # (set_code, rarity) to avoid duplicates

        target_lang = self.selected_language

        # 1. Existing Variants from DB (Target Language)
        if self.full_card_data and self.full_card_data.card_sets:
            for s in self.full_card_data.card_sets:
                # STRICT FILTERING: Only show codes compatible with the selected language
                if is_set_code_compatible(s.set_code, target_lang):
                    key = (s.set_code, s.set_rarity)
                    variants.append({
                        'set_code': s.set_code,
                        'rarity': s.set_rarity,
                        'image_id': s.image_id,
                        'variant_id': s.variant_id,
                        'name': self.full_card_data.name,
                        'source': 'db'
                    })
                    seen_keys.add(key)

        # 2. Transformed Variants from English DB
        # Only relevant if we have English data
        if self.english_card_data and self.english_card_data.card_sets:
             for s in self.english_card_data.card_sets:
                 # Transform English Code -> Target Language Code
                 new_code = transform_set_code(s.set_code, target_lang)

                 # STRICT FILTERING: Check if the *new* code is compatible with target language
                 if not is_set_code_compatible(new_code, target_lang):
                     continue

                 key = (new_code, s.set_rarity)

                 # If we already have this exact variant from the DB, skip it (prefer DB source)
                 if key in seen_keys:
                     continue

                 # Add Synthetic Variant
                 variants.append({
                     'set_code': new_code,
                     'rarity': s.set_rarity,
                     'image_id': s.image_id, # Inherit English Image
                     'variant_id': None,     # Synthetic, no ID yet
                     'name': self.english_card_data.name, # Use English name? Or target? Usually should align.
                     'source': 'synthetic'
                 })
                 seen_keys.add(key)

        # Fallback to candidates if no data loaded yet (and no full data)
        if not variants and not self.full_card_data and self.candidates:
             # Just return candidates, but we should probably filter them too?
             # Candidates usually come from OCR which might be mixed.
             # Let's filter candidates by compatibility too.
             for c in self.candidates:
                 if is_set_code_compatible(c['set_code'], target_lang):
                     variants.append(c)

        # Filter by Name (if applicable)
        if self.name_select:
             return [v for v in variants if v['name'] == self.selected_name]
        return variants

    def update_options(self):
        """Updates all dropdown options based on current selections."""
        # Safeguard against UI not being ready
        if not self.set_code_select: return

        filtered_vars = self._get_current_variants()

        # 1. Update Set Codes
        codes = set()

        # Add valid codes from filtered list
        for v in filtered_vars:
            codes.add(v['set_code'])

        # Add OCR Code if valid and compatible
        if self.ocr_set_id:
             # Check compatibility
             if is_set_code_compatible(self.ocr_set_id, self.selected_language):
                 # Also check if it somewhat matches normalized version of any variant?
                 # Or just allow it if compatible?
                 # Original logic checked normalized match.
                 norm_ocr = normalize_set_code(self.ocr_set_id)
                 if any(normalize_set_code(v['set_code']) == norm_ocr for v in filtered_vars):
                     codes.add(self.ocr_set_id)

        # Also ensure current selected Set Code is preserved if valid
        if self.selected_set_code and self.selected_set_code != "Other":
             # Check compatibility first
             if is_set_code_compatible(self.selected_set_code, self.selected_language):
                 norm_sel = normalize_set_code(self.selected_set_code)
                 if any(normalize_set_code(v['set_code']) == norm_sel for v in filtered_vars):
                     codes.add(self.selected_set_code)

        sorted_codes = sorted(list(codes))
        sorted_codes.append("Other")

        self.set_code_select.options = sorted_codes

        # Ensure value matches
        if self.selected_set_code not in sorted_codes:
            # Try to find best match in sorted_codes (ignoring "Other")
            match = next((c for c in sorted_codes if c != "Other" and normalize_set_code(c) == normalize_set_code(self.selected_set_code)), None)

            if match:
                 self.selected_set_code = match
            elif self.ocr_set_id and self.ocr_set_id in sorted_codes:
                self.selected_set_code = self.ocr_set_id
            elif len(sorted_codes) > 1:
                self.selected_set_code = sorted_codes[0] # Default to first
            else:
                self.selected_set_code = "Other"

        self.set_code_select.value = self.selected_set_code
        self.set_code_select.update()

        # Update Artstyle & Rarity
        self.update_art_and_rarity_options(filtered_vars)

    def update_art_and_rarity_options(self, variants):
        # Safeguard against UI not being ready
        if not self.artstyle_select or not self.rarity_select: return

        # Filter by Set Code (unless Other)
        if self.selected_set_code == "Other":
            # Show all images for this card? Or just the default?
            # User said: "All images for the selected SET CODE".
            # For "Other", we don't have a set code. Show all images for the CARD.
            relevant_vars = variants
        else:
            # BROADENED SEARCH: Use normalized set code to find all equivalent variants (e.g. EN and DE)
            # This ensures that if a non-English code is selected (which might lack images),
            # we still show images from the English equivalent (Synthetic variants handled this already).
            norm_sel = normalize_set_code(self.selected_set_code)
            relevant_vars = [v for v in variants if normalize_set_code(v['set_code']) == norm_sel]

        # 1. Artstyles (Image IDs)
        # Create map: Image ID -> Label
        # We can try to deduce label (e.g. "Art 1", "Art 2")
        image_ids = sorted(list(set(v['image_id'] for v in relevant_vars if v.get('image_id'))))

        art_opts = {}
        for i, img_id in enumerate(image_ids):
             art_opts[img_id] = f"Art Variation {i+1} (ID: {img_id})"

        self.artstyle_select.options = art_opts

        # Default image if current not in list
        if self.selected_image_id not in image_ids:
            if image_ids:
                self.selected_image_id = image_ids[0]

        self.artstyle_select.value = self.selected_image_id
        self.artstyle_select.update()
        self.update_preview()

        # 2. Rarities
        # Filter by Set Code AND Artstyle
        rarity_vars = [v for v in relevant_vars if v.get('image_id') == self.selected_image_id]
        rarities = sorted(list(set(v['rarity'] for v in rarity_vars)))

        self.rarity_select.options = rarities
        if self.selected_rarity not in rarities:
            if rarities:
                self.selected_rarity = rarities[0]

        self.rarity_select.value = self.selected_rarity
        self.rarity_select.update()

    def on_name_change(self, e):
        self.selected_name = e.value

        # Find corresponding card_id from candidates
        candidate = next((c for c in self.candidates if c['name'] == self.selected_name), None)
        if candidate and candidate.get('card_id') != self.card_id:
             self.card_id = candidate['card_id']
             self.full_card_data = None # Clear old data
             self.english_card_data = None
             self.update_options() # Update immediately with candidates fallback
             # Reload data for new card
             asyncio.create_task(self.load_full_data())
        else:
            self.update_options()

    def on_language_change(self, e):
        self.selected_language = e.value
        # Reload full data for new language
        asyncio.create_task(self.load_full_data())

    def on_set_code_change(self, e):
        self.selected_set_code = e.value
        # Update Art/Rarity
        variants = self._get_current_variants()
        self.update_art_and_rarity_options(variants)

        # Show/Hide input (handled by binding, but logic check here)
        if self.selected_set_code == "Other":
            # Focus?
            pass

    def on_artstyle_change(self, e):
        self.selected_image_id = e.value
        self.update_preview()
        # Update Rarity (constrained by art)
        variants = self._get_current_variants()
        self.update_art_and_rarity_options(variants)

    def update_preview(self):
        if self.selected_image_id:
             self.preview_image.set_source(f"/images/{self.selected_image_id}.jpg")
        else:
             self.preview_image.set_source(None)

    async def confirm(self):
        # Handle "Other" Set Code
        final_set_code = self.selected_set_code
        final_rarity = self.selected_rarity

        variant_id = None
        image_id = self.selected_image_id

        if self.selected_set_code == "Other":
            final_set_code = self.other_set_code_val

            if not final_set_code:
                ui.notify("Please enter a Set Code", type='warning')
                return

            # Add to DB
            if final_set_code and self.card_id:
                try:
                    # DUPLICATE CHECK:
                    # Check if this set code is already assigned to a DIFFERENT card in the database.
                    # We iterate over all cards (expensive? we can use a helper or cache).
                    # Actually, ygo_service keeps a cache.
                    # We need a method `find_card_by_set_code(set_code)`.

                    # Let's perform a check.
                    cards = await ygo_service.load_card_database(self.selected_language.lower())
                    duplicate_found = False
                    for c in cards:
                        if c.id == self.card_id: continue
                        if c.card_sets:
                            for s in c.card_sets:
                                if s.set_code == final_set_code:
                                    # Found a duplicate on another card!
                                    duplicate_found = True
                                    break
                        if duplicate_found: break

                    if duplicate_found:
                        ui.notify(f"Duplicate set code! '{final_set_code}' belongs to another card.", type='negative')
                        return

                    # Proceed to Add
                    # We need to add this variant
                    # add_card_variant(card_id, set_name, set_code, set_rarity, ...)
                    # We don't know set_name. Try to infer or use "Custom".

                    # Look up set info?
                    set_info = await ygo_service.get_set_info(final_set_code)
                    set_name = set_info.get('name', 'Custom Set') if set_info else 'Custom Set'

                    new_set = await ygo_service.add_card_variant(
                        card_id=self.card_id,
                        set_name=set_name,
                        set_code=final_set_code,
                        set_rarity=final_rarity,
                        image_id=image_id,
                        language=self.selected_language.lower()
                    )
                    if new_set:
                        variant_id = new_set.variant_id
                        ui.notify(f"Added new variant {final_set_code}", type='positive')
                    else:
                        # Duplicate? Find existing.
                        # Reload card to get fresh data
                        card = await run.io_bound(ygo_service.get_card, self.card_id, self.selected_language.lower())
                        if card and card.card_sets:
                             v = next((s for s in card.card_sets if s.set_code == final_set_code and s.set_rarity == final_rarity), None)
                             if v:
                                 variant_id = v.variant_id
                                 ui.notify("Using existing variant", type='info')

                except Exception as e:
                    logger.error(f"Failed to add variant: {e}")
                    ui.notify(f"Failed to add variant: {e}", type='negative')
                    return
        else:
             # Find existing variant_id (DB or Synthetic)

             # 1. Try finding in loaded Full Data (DB)
             if self.full_card_data and self.full_card_data.card_sets:
                 # Try exact match first
                 v = next((s for s in self.full_card_data.card_sets if s.set_code == final_set_code and s.set_rarity == final_rarity and s.image_id == image_id), None)
                 if v:
                     variant_id = v.variant_id
                 else:
                     # Fallback to loose match (ignore image id if not found)
                     v = next((s for s in self.full_card_data.card_sets if s.set_code == final_set_code and s.set_rarity == final_rarity), None)
                     if v: variant_id = v.variant_id

             # 2. If not found (Synthetic), variant_id remains None.
             # This signal to the ScanPage that it should verify/add this variant to the Global DB.
             # ScanPage._ensure_global_variant_exists handles this based on set_code/rarity.

        # Construct Result
        final_res = self.result.copy()
        final_res['set_code'] = final_set_code
        final_res['rarity'] = final_rarity
        final_res['language'] = self.selected_language
        final_res['first_edition'] = self.selected_first_ed
        final_res['name'] = self.selected_name
        final_res['card_id'] = self.card_id
        final_res['image_id'] = image_id
        final_res['variant_id'] = variant_id

        # Determine if callback is async and await if so
        if asyncio.iscoroutinefunction(self.on_confirm_cb):
            await self.on_confirm_cb(final_res)
        else:
            self.on_confirm_cb(final_res)

        self.close()
