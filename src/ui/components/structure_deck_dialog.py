
from nicegui import ui
from src.services.yugipedia_service import yugipedia_service, DeckCard
from typing import Callable, List, Dict, Optional, Any
import logging
import re

logger = logging.getLogger(__name__)

class StructureDeckDialog:
    def __init__(self, on_add: Callable[[str, List[Dict[str, Any]]], None]):
        """
        on_add: callback function(deck_name, list_of_cards)
                where list_of_cards is [{'set_code': str, 'quantity': int, 'rarity': str}]
        """
        self.on_add = on_add
        self.dialog = ui.dialog()
        self.decks = []
        self.selected_deck_title: Optional[str] = None
        self.main_deck_cards: List[DeckCard] = []

        # UI Elements
        self.deck_select: Optional[ui.select] = None
        self.preview_container: Optional[ui.element] = None
        self.add_btn: Optional[ui.button] = None

    def open(self):
        self.dialog.clear()
        self.dialog.open()
        with self.dialog, ui.card().classes('w-[600px] h-[80vh] flex flex-col bg-gray-900 text-white border border-gray-700'):
            ui.label('Add Structure / Starter Deck').classes('text-h6 font-bold mb-4')
            ui.label('Note: Promo / Bonus cards have to be added manually.').classes('text-sm text-gray-400 italic mb-2')

            with ui.column().classes('w-full flex-grow gap-4'):
                # 1. Deck Selector
                self.deck_select = ui.select(
                    options={},
                    label='Select Deck',
                    on_change=self._on_deck_selected
                ).classes('w-full').props('use-input input-debounce="0" dark behavior="menu"')

                # Loading spinner for deck fetching
                self.deck_loading = ui.spinner().classes('self-center hidden')

                # 2. Preview / Info
                self.preview_container = ui.scroll_area().classes('w-full flex-grow border border-gray-700 rounded p-2 bg-black/20')

                # 3. Actions
                with ui.row().classes('w-full justify-end mt-4'):
                    ui.button('Cancel', on_click=self.dialog.close).props('flat')
                    self.add_btn = ui.button('Add Deck', on_click=self._on_confirm).props('color=primary')
                    self.add_btn.disable()

        # Start loading decks
        ui.timer(0.1, self._load_decks, once=True)

    async def _load_decks(self):
        self.deck_select.disable()
        self.deck_loading.classes(remove='hidden')

        decks = await yugipedia_service.get_all_decks()
        self.decks = decks

        options = {}
        for d in decks:
            display = d.title

            # Determine prefix based on type
            # Safe defaults if deck_type missing (legacy/fallback)
            d_type = getattr(d, 'deck_type', 'STRUCTURE')

            if d_type == 'STARTER':
                prefix = "Starter Deck: "
                phrase = "Starter Deck"
            else:
                # STRUCTURE or PRECON (or fallback)
                prefix = "Structure Deck: "
                phrase = "Structure Deck"

            # Remove existing phrase (case insensitive) to avoid duplication
            cleaned = re.sub(re.escape(phrase), '', display, flags=re.IGNORECASE).strip()
            # Remove leading/trailing colons, hyphens, spaces
            cleaned = re.sub(r'^[:\-\s]+', '', cleaned).strip()

            options[d.title] = f"{prefix}{cleaned}"

        self.deck_select.options = options
        self.deck_select.enable()
        self.deck_loading.classes(add='hidden')

    async def _on_deck_selected(self, e):
        title = e.value
        if not title: return

        self.selected_deck_title = title
        self.add_btn.disable()
        self.preview_container.clear()

        with self.preview_container:
            ui.spinner().classes('self-center')

        # Fetch details
        deck_data = await yugipedia_service.get_deck_list(title)
        self.main_deck_cards = deck_data['main']
        # Bonus cards are ignored as per requirements

        # Update UI
        self.preview_container.clear()
        with self.preview_container:
            ui.label(f"Main Deck: {sum(c.quantity for c in self.main_deck_cards)} cards").classes('font-bold')
            if not self.main_deck_cards:
                 ui.label("No cards found or failed to parse.").classes('text-red-500 italic')
            else:
                 # Simple list preview
                 with ui.column().classes('gap-1 text-xs'):
                     for c in self.main_deck_cards:
                         ui.label(f"{c.quantity}x {c.name} ({c.code}) - {c.rarity}")

        if self.main_deck_cards:
            self.add_btn.enable()

    async def _on_confirm(self):
        if not self.selected_deck_title: return

        # Gather all cards
        final_list = []

        # Main Deck
        for c in self.main_deck_cards:
            final_list.append({
                'set_code': c.code,
                'quantity': c.quantity,
                'rarity': c.rarity,
                'name': c.name # Debug info
            })

        self.dialog.close()

        if self.on_add:
            await self.on_add(self.selected_deck_title, final_list)
