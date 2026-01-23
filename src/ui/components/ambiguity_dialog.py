from nicegui import ui
from typing import Dict, Any, List, Optional, Callable
import logging

from src.core.utils import is_set_code_compatible, normalize_set_code, REGION_TO_LANGUAGE_MAP
from src.services.ygo_api import ygo_service
from src.services.image_manager import image_manager

logger = logging.getLogger(__name__)

class AmbiguityDialog(ui.dialog):
    def __init__(self, scan_result: Dict[str, Any], on_confirm: Callable):
        super().__init__()
        self.result = scan_result
        self.candidates = scan_result.get('candidates', [])
        self.on_confirm_cb = on_confirm

        # Initial Selection (Best Guess)
        self.selected_set_code = scan_result.get('set_code')
        self.selected_rarity = scan_result.get('rarity') or scan_result.get('visual_rarity')
        self.selected_language = scan_result.get('language', 'EN')
        self.selected_first_ed = scan_result.get('first_edition', False)

        # Try to find best candidate to init image and basics
        best_cand = self.candidates[0] if self.candidates else {}
        self.selected_image_id = best_cand.get('image_id')
        self.card_id = best_cand.get('card_id')
        self.card_name = best_cand.get('name', 'Unknown')

        # Check for Multiple Names
        unique_names = sorted(list(set(c['name'] for c in self.candidates)))
        self.has_multiple_names = len(unique_names) > 1

        # State for Custom Set Code
        self.custom_set_code = ""
        self.is_custom_set_code = False

        # UI Elements
        self.preview_image = None
        self.rarity_select = None
        self.set_code_select = None
        self.custom_set_code_input = None
        self.artstyle_select = None

        with self, ui.card().classes('w-[900px] h-[700px] flex flex-row p-4 gap-4'):
             # LEFT: Image Preview
             with ui.column().classes('w-1/3 h-full items-center justify-center bg-black rounded'):
                 self.preview_image = ui.image().classes('max-w-full max-h-full object-contain')
                 self.update_preview()

             # RIGHT: Controls
             with ui.column().classes('flex-grow h-full overflow-y-auto'):
                 ui.label("Resolve Ambiguity").classes('text-xl font-bold mb-2')

                 # 0. Card Name (Optional)
                 if self.has_multiple_names:
                     ui.label("Card Name").classes('text-sm font-bold text-gray-500')
                     ui.select(
                         options=unique_names,
                         value=self.card_name,
                         on_change=self.on_name_change
                     ).classes('w-full mb-2')
                 else:
                     ui.label(self.card_name).classes('text-lg font-bold text-primary mb-4')

                 # 1. Language
                 ui.select(
                     options=['EN', 'DE', 'FR', 'IT', 'ES', 'PT', 'JP', 'KR'],
                     value=self.selected_language,
                     label="Language",
                     on_change=self.on_language_change
                 ).classes('w-full')

                 # 2. Set Code
                 initial_opts = self.get_set_code_options()

                 # Initialize Custom State if needed
                 if self.selected_set_code and self.selected_set_code not in initial_opts:
                     # This happens if the scan result has a code not in DB candidates.
                     # We should add it to options or treat as custom?
                     # Requirement says: "ALWAYS add the option to select the OCR identified set id... as long as valid"
                     # My logic in get_set_code_options handles validity.
                     # If it's here, let's add it to options.
                     initial_opts.insert(0, self.selected_set_code)

                 self.set_code_select = ui.select(
                     options=initial_opts,
                     value=self.selected_set_code,
                     label="Set Code",
                     on_change=self.on_set_code_change,
                     with_input=True
                 ).classes('w-full')

                 # Custom Set Code Input (Hidden by default)
                 self.custom_set_code_input = ui.input(
                     label="Enter Custom Set Code",
                     value=self.selected_set_code, # Prepopulate
                     on_change=lambda e: setattr(self, 'custom_set_code', e.value)
                 ).classes('w-full').props('clearable')
                 self.custom_set_code_input.visible = False

                 # 3. Rarity
                 self.rarity_select = ui.select(
                     options=self.get_rarity_options(),
                     value=self.selected_rarity,
                     label="Rarity",
                     on_change=self.on_rarity_change
                 ).classes('w-full')

                 # 4. Artstyle (Image ID)
                 ui.label("Artstyle Variant").classes('text-sm font-bold text-gray-500 mt-2')
                 self.artstyle_select = ui.select(
                     options=self.get_artstyle_options(),
                     value=self.selected_image_id,
                     on_change=self.on_artstyle_change,
                     label="Select Artwork"
                 ).classes('w-full')

                 # 5. 1st Edition
                 ui.checkbox("1st Edition", value=self.selected_first_ed,
                             on_change=lambda e: setattr(self, 'selected_first_ed', e.value)).classes('mt-2')

                 ui.space()

                 # Buttons
                 with ui.row().classes('w-full justify-end gap-2'):
                     ui.button("Cancel", on_click=self.close, color='secondary')
                     ui.button("Confirm", on_click=self.confirm, color='positive') # Changed to positive (Green)

    def get_set_code_options(self):
        codes = set()

        # 1. From Candidates
        for c in self.candidates:
            # Filter by name if multiple names exist
            if self.has_multiple_names and c['name'] != self.card_name:
                continue

            if is_set_code_compatible(c['set_code'], self.selected_language):
                codes.add(c['set_code'])

        # 2. Include OCR Set ID if Valid
        ocr_code = self.result.get('set_code')
        if ocr_code:
            # Check validity against candidates (normalized)
            is_valid = False
            norm_ocr = normalize_set_code(ocr_code)

            # Check against ALL candidates for this card (ignoring current filters for validity check)
            for c in self.candidates:
                if normalize_set_code(c['set_code']) == norm_ocr:
                    is_valid = True
                    break

            # Or if it's already in the DB list (exact match)
            if ocr_code in codes:
                is_valid = True

            if is_valid:
                codes.add(ocr_code)

        opts = sorted(list(codes))
        opts.append("Other") # Always add Other
        return opts

    def get_rarity_options(self):
        # Based on selected set code, what rarities are available?
        rarities = set()

        # If custom set code, we might not know rarities, so maybe allow free text?
        # For now, show all rarities from candidates to be safe?
        # Or just keep it restricted to known variants.

        target_code = self.custom_set_code if self.is_custom_set_code else self.selected_set_code

        found_match = False
        for c in self.candidates:
            if c['set_code'] == target_code:
                rarities.add(c['rarity'])
                found_match = True

        if not found_match:
            # If no exact match (e.g. OCR code or Custom), try normalized match
            norm_target = normalize_set_code(target_code) if target_code else ""
            for c in self.candidates:
                if normalize_set_code(c['set_code']) == norm_target:
                    rarities.add(c['rarity'])

        if not rarities:
            # Fallback: All rarities seen for this card
            for c in self.candidates:
                 rarities.add(c['rarity'])

        return sorted(list(rarities))

    def get_artstyle_options(self):
        # Return dict {image_id: "Image ID"}
        # Filter by name
        opts = {}
        for c in self.candidates:
            if self.has_multiple_names and c['name'] != self.card_name:
                continue

            # You might want to label them better (e.g. by Set Code)
            # But Image ID is unique identifier for art.
            if c.get('image_id'):
                label = f"Art {c['image_id']} ({c['set_code']})"
                opts[c['image_id']] = label
        return opts

    def on_name_change(self, e):
        self.card_name = e.value
        # Update Card ID based on name
        for c in self.candidates:
            if c['name'] == self.card_name:
                self.card_id = c['card_id']
                break

        # Refresh options
        self.refresh_ui_options()

    def on_language_change(self, e):
        self.selected_language = e.value
        self.refresh_ui_options()

    def on_set_code_change(self, e):
        val = e['value'] if isinstance(e, dict) else e.value

        if val == "Other":
            self.is_custom_set_code = True
            self.custom_set_code_input.visible = True
            # Prepopulate with highest score set code if empty
            if not self.custom_set_code and self.candidates:
                self.custom_set_code = self.candidates[0]['set_code']
            self.custom_set_code_input.value = self.custom_set_code
        else:
            self.is_custom_set_code = False
            self.selected_set_code = val
            self.custom_set_code_input.visible = False

        self.refresh_ui_options(skip_set_code=True)

    def on_rarity_change(self, e):
        self.selected_rarity = e.value
        self.update_image_selection()

    def on_artstyle_change(self, e):
        self.selected_image_id = e.value
        self.update_preview()

    def refresh_ui_options(self, skip_set_code=False):
        # Update Set Codes
        if not skip_set_code:
            opts = self.get_set_code_options()
            # If current selection invalid, reset
            if not self.is_custom_set_code:
                if self.selected_set_code not in opts:
                    if opts:
                         # Don't pick "Other" by default if possible
                         self.selected_set_code = opts[0] if opts[0] != "Other" else (opts[1] if len(opts)>1 else "Other")

                # Ensure we don't crash ui.select
                if self.selected_set_code not in opts:
                    opts.insert(0, self.selected_set_code)

            self.set_code_select.options = opts
            self.set_code_select.value = "Other" if self.is_custom_set_code else self.selected_set_code
            self.set_code_select.update()

        # Update Rarities
        r_opts = self.get_rarity_options()
        self.rarity_select.options = r_opts
        if r_opts and self.selected_rarity not in r_opts:
            self.selected_rarity = r_opts[0]
        self.rarity_select.value = self.selected_rarity
        self.rarity_select.update()

        # Update Artstyles
        a_opts = self.get_artstyle_options()
        self.artstyle_select.options = a_opts
        if a_opts and self.selected_image_id not in a_opts:
             # Pick first
             self.selected_image_id = list(a_opts.keys())[0]
        self.artstyle_select.value = self.selected_image_id
        self.artstyle_select.update()

        self.update_preview()

    def update_image_selection(self):
        # Try to find specific image for set/rarity combo
        target_code = self.custom_set_code if self.is_custom_set_code else self.selected_set_code

        cand = next((c for c in self.candidates if c['set_code'] == target_code and c['rarity'] == self.selected_rarity), None)
        if cand:
            self.selected_image_id = cand['image_id']
            self.artstyle_select.value = self.selected_image_id
            self.artstyle_select.update()
            self.update_preview()

    def update_preview(self):
        if self.selected_image_id:
             self.preview_image.set_source(f"/images/{self.selected_image_id}.jpg")
        else:
             self.preview_image.set_source(None)

    def confirm(self):
        final_res = self.result.copy()

        final_code = self.custom_set_code if self.is_custom_set_code else self.selected_set_code

        final_res['set_code'] = final_code
        final_res['rarity'] = self.selected_rarity
        final_res['language'] = self.selected_language
        final_res['first_edition'] = self.selected_first_ed
        final_res['name'] = self.card_name
        final_res['card_id'] = self.card_id
        final_res['image_id'] = self.selected_image_id

        # Try to match a variant_id if possible
        cand = next((c for c in self.candidates if c['set_code'] == final_code and c['rarity'] == self.selected_rarity), None)
        if cand:
            final_res['variant_id'] = cand.get('variant_id')

        self.on_confirm_cb(final_res)
        self.close()
