import requests
import re
import logging
from typing import List, Dict, Optional, Any
from dataclasses import dataclass
from nicegui import run
import asyncio

logger = logging.getLogger(__name__)

@dataclass
class StructureDeck:
    page_id: int
    title: str

@dataclass
class DeckCard:
    code: str
    name: str
    rarity: str
    quantity: int
    is_bonus: bool = False

class YugipediaService:
    API_URL = "https://yugipedia.com/api.php"

    # Rarity Mapping (Yugipedia Abbr -> Full Name)
    RARITY_MAP = {
        "C": "Common",
        "R": "Rare",
        "SR": "Super Rare",
        "UR": "Ultra Rare",
        "ScR": "Secret Rare",
        "SE": "Secret Rare",
        "UScR": "Ultimate Rare", # Confirm mappings as needed
        "UtR": "Ultimate Rare",
        "GR": "Gold Rare",
        "GScR": "Gold Secret Rare",
        "PScR": "Prismatic Secret Rare",
        "QCScR": "Quarter Century Secret Rare",
        "QCC": "Quarter Century Secret Rare",
        # Add full names to map to themselves to be safe
        "Common": "Common",
        "Rare": "Rare",
        "Super Rare": "Super Rare",
        "Ultra Rare": "Ultra Rare",
        "Secret Rare": "Secret Rare",
    }

    async def get_structure_decks(self) -> List[StructureDeck]:
        """Fetches list of TCG Structure Decks."""
        params = {
            "action": "query",
            "list": "categorymembers",
            "cmtitle": "Category:TCG_Structure_Decks",
            "cmlimit": "500",
            "format": "json"
        }

        try:
            # Use run.io_bound for blocking requests
            if hasattr(run, 'io_bound'):
                response = await run.io_bound(requests.get, self.API_URL, params=params)
            else:
                response = await asyncio.to_thread(requests.get, self.API_URL, params=params)

            if response.status_code == 200:
                data = response.json()
                members = data.get("query", {}).get("categorymembers", [])
                # Filter out "Category:" subcategories if any, keep pages (ns=0)
                decks = [
                    StructureDeck(page_id=m['pageid'], title=m['title'])
                    for m in members if m['ns'] == 0
                ]
                return sorted(decks, key=lambda x: x.title)
            else:
                logger.error(f"Yugipedia API error: {response.status_code}")
                return []
        except Exception as e:
            logger.error(f"Failed to fetch structure decks: {e}")
            return []

    async def get_deck_list(self, page_title: str) -> Dict[str, List[DeckCard]]:
        """
        Fetches the card list for a structure deck.
        Returns a dict: {'main': [cards], 'bonus': [cards]}

        We need to find the "Set Card Lists:..." page associated with the structure deck.
        Usually it's linked or follows a pattern.
        Pattern: "Set Card Lists:{Title} (TCG-EN)"
        """

        # 1. Try to find the Set Card Lists page
        # Sanitize title: "Structure Deck: Albaz Strike" -> "Set Card Lists:Structure Deck: Albaz Strike (TCG-EN)"
        # Note: Some might be (TCG-DE), etc. We want EN.

        # We assume the title passed is like "Structure Deck: Albaz Strike"
        # We construct the list title.

        list_title = f"Set Card Lists:{page_title} (TCG-EN)"

        params = {
            "action": "query",
            "titles": list_title,
            "prop": "revisions",
            "rvprop": "content",
            "format": "json"
        }

        try:
            if hasattr(run, 'io_bound'):
                response = await run.io_bound(requests.get, self.API_URL, params=params)
            else:
                response = await asyncio.to_thread(requests.get, self.API_URL, params=params)

            if response.status_code != 200:
                return {'main': [], 'bonus': []}

            data = response.json()
            pages = data.get("query", {}).get("pages", {})

            content = None
            for pid, page in pages.items():
                if pid == "-1": # Not found
                    # Try searching for it? Or maybe the title format is slightly different?
                    # Fallback: Search for "Set Card Lists:{Title}"
                    logger.warning(f"Direct lookup failed for {list_title}. Trying search.")
                    return await self._search_and_fetch_list(page_title)

                if "revisions" in page:
                    content = page["revisions"][0]["*"]
                    break

            if content:
                return self._parse_wikitext(content)

            return {'main': [], 'bonus': []}

        except Exception as e:
            logger.error(f"Error getting deck list for {page_title}: {e}")
            return {'main': [], 'bonus': []}

    async def _search_and_fetch_list(self, deck_title: str) -> Dict[str, List[DeckCard]]:
        # Search for "Set Card Lists:{deck_title}"
        search_query = f"Set Card Lists:{deck_title}"
        params = {
            "action": "query",
            "list": "search",
            "srsearch": search_query,
            "srlimit": 5,
            "format": "json"
        }

        try:
            if hasattr(run, 'io_bound'):
                res = await run.io_bound(requests.get, self.API_URL, params=params)
            else:
                res = await asyncio.to_thread(requests.get, self.API_URL, params=params)

            results = res.json().get("query", {}).get("search", [])

            target_title = None
            # Prefer (TCG-EN)
            for r in results:
                t = r['title']
                if "(TCG-EN)" in t:
                    target_title = t
                    break

            if not target_title and results:
                # Take first if no EN specific found (might be just TCG?)
                target_title = results[0]['title']

            if target_title:
                # Fetch content
                p_params = {
                    "action": "query",
                    "titles": target_title,
                    "prop": "revisions",
                    "rvprop": "content",
                    "format": "json"
                }
                if hasattr(run, 'io_bound'):
                    res = await run.io_bound(requests.get, self.API_URL, params=p_params)
                else:
                    res = await asyncio.to_thread(requests.get, self.API_URL, params=p_params)

                pages = res.json().get("query", {}).get("pages", {})
                for pid, page in pages.items():
                    if "revisions" in page:
                        return self._parse_wikitext(page["revisions"][0]["*"])

        except Exception as e:
            logger.error(f"Search fallback failed: {e}")

        return {'main': [], 'bonus': []}

    def _parse_wikitext(self, wikitext: str) -> Dict[str, List[DeckCard]]:
        """
        Parses the wikitext to extract card lists.
        Distinguishes between Main Deck (default) and Bonus Cards.
        """
        # We need to identify sections.
        # {{Set list ...}} blocks.
        # Often preceded by headers like == Bonus cards == or == Preconstructed Deck ==

        # Strategy: Split by "== ... ==" headers to identify context, then find {{Set list}} inside.

        sections = re.split(r'(^==.*?==)', wikitext, flags=re.MULTILINE)

        main_cards = []
        bonus_cards = []

        current_section = "main" # Default to main if no header

        # If no headers found, sections will just be [content]
        # If headers found, it will be [preamble, header, content, header, content...]

        # Iterate and parse
        for i, part in enumerate(sections):
            part = part.strip()
            if not part: continue

            # Check if header
            if part.startswith("==") and part.endswith("=="):
                header = part.lower()
                if "bonus" in header:
                    current_section = "bonus"
                else:
                    current_section = "main"
                continue

            # Parse Set list in this part
            cards = self._extract_cards_from_block(part)
            if cards:
                if current_section == "bonus":
                    bonus_cards.extend(cards)
                else:
                    main_cards.extend(cards)

        # Post-processing: If only one list found and it was put in main, assume it is the deck.
        # If we have explicit bonus section, we labeled it.

        return {
            'main': main_cards,
            'bonus': sorted(bonus_cards, key=lambda c: c.name) # Sort bonus for UI
        }

    def _extract_cards_from_block(self, text: str) -> List[DeckCard]:
        pattern = r'\{\{Set list\|(.*?)\}\}'
        matches = re.findall(pattern, text, re.DOTALL)

        cards = []

        for block in matches:
            parts = block.split('|')
            list_content = ""
            default_qty = 1
            default_rarity = "Common" # Fallback

            for part in parts:
                part = part.strip()
                if '=' in part:
                    key, val = part.split('=', 1)
                    key = key.strip().lower()
                    val = val.strip()
                    if key == 'qty':
                        try: default_qty = int(val)
                        except: pass
                    elif key == 'rarities':
                        # Example: rarities=Common
                        # If comma separated, it might be list of rarities for the set, but here we want default?
                        # Usually rarities param defines the columns or general rarity?
                        # Actually in the example: "rarities=Common" -> cards are Common unless specified.
                        # "rarities=Secret Rare, Quarter Century..." -> Just info?
                        if ',' not in val:
                            default_rarity = self._map_rarity(val)
                else:
                    if ';' in part:
                        list_content = part

            card_lines = list_content.split('\n')

            for line in card_lines:
                line = line.strip()
                if not line: continue

                columns = [c.strip() for c in line.split(';')]
                if len(columns) < 2: continue

                code = columns[0]
                name = columns[1]

                # Rarity is 3rd col
                rarity_str = columns[2] if len(columns) > 2 and columns[2] else None

                # Qty
                qty = default_qty
                # Check 4th or 5th col for qty
                # Format: Code; Name; Rarity; Notes; Qty
                if len(columns) > 4 and columns[4].isdigit():
                    qty = int(columns[4])
                elif len(columns) > 3 and columns[3].isdigit():
                    qty = int(columns[3])

                rarity = self._map_rarity(rarity_str) if rarity_str else default_rarity

                cards.append(DeckCard(
                    code=code,
                    name=name,
                    rarity=rarity,
                    quantity=qty,
                    is_bonus=False # Will be set by section logic
                ))

        return cards

    def _map_rarity(self, rarity_abbr: str) -> str:
        # Clean up input
        r = rarity_abbr.strip()
        # Handle "Ultra Rare" full string or "UR"
        # Check map
        if r in self.RARITY_MAP:
            return self.RARITY_MAP[r]

        # Fallback: Return as is, or try simple lookup
        return r

yugipedia_service = YugipediaService()
