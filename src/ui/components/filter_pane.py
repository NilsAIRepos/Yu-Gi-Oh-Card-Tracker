from nicegui import ui
from typing import Callable, Dict, Any, List
from src.core.constants import CARD_CONDITIONS

class FilterPane:
    def __init__(self, state: Dict[str, Any], on_change: Callable, on_reset: Callable, show_set_selector: bool = True):
        self.state = state
        self.on_change = on_change
        self.on_reset = on_reset
        self.show_set_selector = show_set_selector
        self.filter_inputs = {}

    def build(self):
        with ui.column().classes('w-full p-4 gap-4'):
            ui.label('Filters').classes('text-h6')

            if self.show_set_selector:
                # Set Selector
                self.set_selector = ui.select(
                    self.state.get('available_sets', []),
                    label='Set', with_input=True, clearable=True,
                    on_change=self.on_change
                ).bind_value(self.state, 'filter_set').classes('w-full').props('use-input fill-input input-debounce=0')

            # Rarity
            common_rarities = [
                "Common", "Rare", "Super Rare", "Ultra Rare", "Secret Rare",
                "Ghost Rare", "Ultimate Rare", "Starlight Rare", "Collector's Rare"
            ]
            ui.select(common_rarities, label='Rarity', with_input=True, clearable=True,
                      on_change=self.on_change).bind_value(self.state, 'filter_rarity').classes('w-full')

            # Attribute
            ui.select(['DARK', 'LIGHT', 'EARTH', 'WIND', 'FIRE', 'WATER', 'DIVINE'],
                      label='Attribute', clearable=True,
                      on_change=self.on_change).bind_value(self.state, 'filter_attr').classes('w-full')

            # Card Type
            self.ctype_selector = ui.select(
                self.state.get('available_card_types', []),
                label='Card Types', multiple=True, clearable=True,
                on_change=self.on_change
            ).bind_value(self.state, 'filter_card_type').classes('w-full').props('use-chips')

            # Monster Type (Race)
            self.m_race_selector = ui.select(
                self.state.get('available_monster_races', []),
                label='Monster Type', with_input=True, clearable=True,
                on_change=self.on_change
            ).bind_value(self.state, 'filter_monster_race').classes('w-full')

            # Spell/Trap Type
            self.st_race_selector = ui.select(
                self.state.get('available_st_races', []),
                label='Spell/Trap Type', with_input=True, clearable=True,
                on_change=self.on_change
            ).bind_value(self.state, 'filter_st_race').classes('w-full')

            # Archetype
            self.archetype_selector = ui.select(
                self.state.get('available_archetypes', []),
                label='Archetype', with_input=True, clearable=True,
                on_change=self.on_change
            ).bind_value(self.state, 'filter_archetype').classes('w-full')

            # Monster Category
            categories = ['Effect', 'Normal', 'Synchro', 'Xyz', 'Ritual', 'Fusion', 'Link', 'Pendulum', 'Toon', 'Spirit', 'Union', 'Gemini', 'Flip']
            ui.select(categories, label='Monster Category', multiple=True, clearable=True,
                      on_change=self.on_change).bind_value(self.state, 'filter_monster_category').classes('w-full').props('use-chips')

            # Level
            ui.number('Level/Rank', min=0, max=13, on_change=self.on_change).bind_value(self.state, 'filter_level').classes('w-full')

            # Ranges
            self.setup_range_filter('ATK', 'filter_atk_min', 'filter_atk_max', 0, 5000, 50, 'atk')
            self.setup_range_filter('DEF', 'filter_def_min', 'filter_def_max', 0, 5000, 50, 'def')

            ui.separator()
            ui.label('Ownership & Price').classes('text-h6')

            # Condition (Moved here)
            ui.select(CARD_CONDITIONS, label='Condition', multiple=True, clearable=True,
                      on_change=self.on_change).bind_value(self.state, 'filter_condition').classes('w-full').props('use-chips')

            # Storage
            self.storage_selector = ui.select(
                self.state.get('available_storage', []),
                label='Storage Location', multiple=True, clearable=True,
                on_change=self.on_change
            ).bind_value(self.state, 'filter_storage').classes('w-full').props('use-chips')

            self.setup_range_filter('Ownership Quantity Range', 'filter_ownership_min', 'filter_ownership_max', 0, self.state.get('max_owned_quantity', 100), 1, 'ownership')
            self.setup_range_filter('Price Range ($)', 'filter_price_min', 'filter_price_max', 0, 1000, 1, 'price')

            # Owned Language
            ui.select(['EN', 'DE', 'FR', 'IT', 'PT'], label='Owned Language', clearable=True,
                      on_change=self.on_change).bind_value(self.state, 'filter_owned_lang').classes('w-full')

        with ui.column().classes('p-4 border-t border-gray-700 bg-gray-900 w-full'):
             with ui.button('Reset All Filters', on_click=self.on_reset).classes('w-full').props('color=red-9 outline'):
                 ui.tooltip('Clear all active filters and reset to default')

    def setup_range_filter(self, label, min_key, max_key, min_limit, max_limit, step=1, name=''):
        ui.label(label).classes('text-sm text-gray-400')
        with ui.row().classes('w-full items-center gap-2'):
            min_input = ui.number(min=min_limit, max=max_limit, step=step).classes('w-16').props('dense borderless')
            max_input = ui.number(min=min_limit, max=max_limit, step=step).classes('w-16').props('dense borderless')

            slider = ui.range(min=min_limit, max=max_limit, step=step).classes('col-grow')

            def update_from_val(val):
                self.state[min_key] = val['min']
                self.state[max_key] = val['max']
                min_input.value = val['min']
                max_input.value = val['max']

            def _get_event_val(e):
                val = getattr(e, 'value', None)
                if val is not None:
                    return val

                # Check args
                args = getattr(e, 'args', None)
                if not args:
                    return None

                # If args is a dictionary (NiceGUI sometimes returns the value directly in args for certain elements)
                if isinstance(args, dict):
                    return args

                # If args is a list/tuple
                if isinstance(args, (list, tuple)) and len(args) > 0:
                    return args[0]

                # Fallback: return args itself (e.g. if it's a single primitive value not wrapped in list)
                return args

            async def on_slider_update(e):
                val = _get_event_val(e)
                if isinstance(val, dict):
                    update_from_val(val)

            async def on_slider_change(e):
                val = _get_event_val(e)
                if isinstance(val, dict):
                    update_from_val(val)
                    if self.on_change: await self.on_change()

            async def on_min_input_change(e):
                try:
                    # e.value might be missing on GenericEventArguments, prioritize sender.value
                    raw_val = None
                    if hasattr(e, 'sender') and hasattr(e.sender, 'value'):
                        raw_val = e.sender.value
                    elif hasattr(e, 'value'): # Standard ValueChangeEventArguments
                        raw_val = e.value

                    if raw_val is None:
                        # Fallback to args if sender.value is not set/reliable
                        raw_val = _get_event_val(e)

                    val = float(raw_val) if raw_val is not None else min_limit
                except: val = min_limit
                self.state[min_key] = val
                slider.value = {'min': val, 'max': self.state[max_key]}
                if self.on_change: await self.on_change()

            async def on_max_input_change(e):
                try:
                    raw_val = None
                    if hasattr(e, 'sender') and hasattr(e.sender, 'value'):
                        raw_val = e.sender.value
                    elif hasattr(e, 'value'):
                        raw_val = e.value

                    if raw_val is None:
                        raw_val = _get_event_val(e)

                    val = float(raw_val) if raw_val is not None else max_limit
                except: val = max_limit
                self.state[max_key] = val
                slider.value = {'min': self.state[min_key], 'max': val}
                if self.on_change: await self.on_change()

            slider.on('update:model-value', on_slider_update)
            slider.on('change', on_slider_change)
            slider.value = {'min': self.state[min_key], 'max': self.state[max_key]}

            min_input.on('change', on_min_input_change)
            min_input.value = self.state[min_key]

            max_input.on('change', on_max_input_change)
            max_input.value = self.state[max_key]

            if name:
                self.filter_inputs[name] = (slider, min_input, max_input)

    def update_options(self):
        if hasattr(self, 'set_selector'):
            self.set_selector.options = self.state.get('available_sets', [])
            self.set_selector.update()
        if hasattr(self, 'ctype_selector'):
            self.ctype_selector.options = self.state.get('available_card_types', [])
            self.ctype_selector.update()
        if hasattr(self, 'm_race_selector'):
            self.m_race_selector.options = self.state.get('available_monster_races', [])
            self.m_race_selector.update()
        if hasattr(self, 'st_race_selector'):
            self.st_race_selector.options = self.state.get('available_st_races', [])
            self.st_race_selector.update()
        if hasattr(self, 'archetype_selector'):
            self.archetype_selector.options = self.state.get('available_archetypes', [])
            self.archetype_selector.update()

        if hasattr(self, 'storage_selector'):
            self.storage_selector.options = self.state.get('available_storage', [])
            self.storage_selector.update()

        if 'ownership' in self.filter_inputs:
             slider, min_inp, max_inp = self.filter_inputs['ownership']
             max_qty = self.state.get('max_owned_quantity', 100)
             slider.max = max_qty
             slider.update()
             max_inp.max = max_qty
             max_inp.update()

    def reset_ui_elements(self):
        for key, components in self.filter_inputs.items():
            slider, min_inp, max_inp = components

            if key == 'atk':
                min_val, max_val = 0, 5000
            elif key == 'def':
                min_val, max_val = 0, 5000
            elif key == 'ownership':
                min_val, max_val = 0, self.state.get('max_owned_quantity', 100)
            elif key == 'price':
                min_val, max_val = 0.0, 1000.0

            slider.value = {'min': min_val, 'max': max_val}
            min_inp.value = min_val
            max_inp.value = max_val
