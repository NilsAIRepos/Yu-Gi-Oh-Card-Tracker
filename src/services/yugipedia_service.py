import requests
import re
import logging
from typing import List, Dict, Optional, Any, Tuple
from dataclasses import dataclass
from nicegui import run
import asyncio
from datetime import datetime

logger = logging.getLogger(__name__)

@dataclass
class StructureDeck:
    page_id: int
    title: str
    deck_type: str = 'STRUCTURE' # 'STRUCTURE', 'STARTER', or 'PRECON'

@dataclass
class DeckCard:
    code: str
    name: str
    rarity: str
    quantity: int
    is_bonus: bool = False

class YugipediaService:
    API_URL = "https://yugipedia.com/api.php"
    HEADERS = {"User-Agent": "YgoCollectionManager/1.0"}

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

    async def get_all_decks(self) -> List[StructureDeck]:
        """Fetches list of TCG Structure Decks and Starter Decks."""
        async def fetch_category(category: str, deck_type: str) -> List[StructureDeck]:
            params = {
                "action": "query",
                "list": "categorymembers",
                "cmtitle": category,
                "cmlimit": "500",
                "format": "json"
            }
            try:
                if hasattr(run, 'io_bound'):
                    response = await run.io_bound(requests.get, self.API_URL, params=params, headers=self.HEADERS)
                else:
                    response = await asyncio.to_thread(requests.get, self.API_URL, params=params, headers=self.HEADERS)

                if response.status_code == 200:
                    data = response.json()
                    members = data.get("query", {}).get("categorymembers", [])
                    # Filter out "Category:" subcategories if any, keep pages (ns=0)
                    return [
                        StructureDeck(page_id=m['pageid'], title=m['title'], deck_type=deck_type)
                        for m in members if m['ns'] == 0
                    ]
                else:
                    logger.error(f"Yugipedia API error for {category}: {response.status_code}")
                    return []
            except Exception as e:
                logger.error(f"Failed to fetch {category}: {e}")
                return []

        # Fetch categories concurrently
        results = await asyncio.gather(
            fetch_category("Category:TCG_Structure_Decks", 'STRUCTURE'),
            fetch_category("Category:TCG_Starter_Decks", 'STARTER'),
            fetch_category("Category:Preconstructed_Decks", 'PRECON')
        )

        # Flatten list
        all_decks = results[0] + results[1] + results[2]

        # Deduplicate by page_id (keep first occurrence)
        seen_ids = set()
        unique_decks = []
        for d in all_decks:
            if d.page_id not in seen_ids:
                seen_ids.add(d.page_id)
                unique_decks.append(d)

        # Sort by title
        return sorted(unique_decks, key=lambda x: x.title)

    # Legacy alias for compatibility
    async def get_structure_decks(self) -> List[StructureDeck]:
        return await self.get_all_decks()

    async def get_set_image_url(self, set_name: str) -> Optional[str]:
        """
        Fetches the image URL for a set from Yugipedia.
        """
        params = {
            "action": "query",
            "titles": set_name,
            "prop": "pageimages",
            "format": "json",
            "pithumbsize": 500 # Request a reasonable size (500px width/height constraint)
        }

        try:
            if hasattr(run, 'io_bound'):
                response = await run.io_bound(requests.get, self.API_URL, params=params, headers=self.HEADERS)
            else:
                response = await asyncio.to_thread(requests.get, self.API_URL, params=params, headers=self.HEADERS)

            if response.status_code == 200:
                data = response.json()
                pages = data.get("query", {}).get("pages", {})

                for pid, page in pages.items():
                    if pid == "-1": continue # Not found

                    if "thumbnail" in page:
                        return page["thumbnail"]["source"]

            return None

        except Exception as e:
            logger.error(f"Error fetching set image for {set_name}: {e}")
            return None

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
                response = await run.io_bound(requests.get, self.API_URL, params=params, headers=self.HEADERS)
            else:
                response = await asyncio.to_thread(requests.get, self.API_URL, params=params, headers=self.HEADERS)

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
                res = await run.io_bound(requests.get, self.API_URL, params=params, headers=self.HEADERS)
            else:
                res = await asyncio.to_thread(requests.get, self.API_URL, params=params, headers=self.HEADERS)

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
                    res = await run.io_bound(requests.get, self.API_URL, params=p_params, headers=self.HEADERS)
                else:
                    res = await asyncio.to_thread(requests.get, self.API_URL, params=p_params, headers=self.HEADERS)

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
        # Updated regex to handle leading whitespace before headers
        sections = re.split(r'(^\s*==.*?==)', wikitext, flags=re.MULTILINE)

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
            # Check if header (allowing for leading whitespace)
            clean_part = part.strip()
            if clean_part.startswith("==") and clean_part.endswith("=="):
                header = clean_part.lower()
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

    def _extract_set_list_blocks(self, text: str) -> List[str]:
        """
        Extracts content of {{Set list|...}} blocks using brace counting
        to correctly handle nested templates like {{=}} or {{Link}}.
        """
        blocks = []
        # Find all start indices
        matches = list(re.finditer(r'\{\{Set list\|', text, re.IGNORECASE))

        for match in matches:
            start_idx = match.start()
            # Find matching closing braces
            open_braces = 0
            curr = start_idx
            content_start = match.end() # Start after {{Set list|
            found_end = False

            # Start counting from the beginning of the match
            # But simpler: start loop at start_idx

            i = start_idx
            while i < len(text):
                # Check for {{
                if text[i:i+2] == '{{':
                    open_braces += 2
                    i += 2
                # Check for }}
                elif text[i:i+2] == '}}':
                    open_braces -= 2
                    i += 2
                    if open_braces == 0:
                        found_end = True
                        # Current i is just after }}
                        # Content is between content_start and i-2
                        blocks.append(text[content_start : i - 2])
                        break
                else:
                    i += 1

            # Note: malformed blocks are ignored or handled by next iteration

        return blocks

    def _smart_split_params(self, text: str) -> List[str]:
        """
        Splits text by pipe '|', but respects nested braces {{ }} and brackets [[ ]].
        """
        parts = []
        start = 0
        curr = 0
        braces = 0
        brackets = 0

        while curr < len(text):
            char = text[curr]

            if char == '{':
                if curr + 1 < len(text) and text[curr+1] == '{':
                    braces += 2
                    curr += 1
                else:
                    braces += 1
            elif char == '}':
                if curr + 1 < len(text) and text[curr+1] == '}':
                    braces -= 2
                    curr += 1
                else:
                    braces -= 1
            elif char == '[':
                 if curr + 1 < len(text) and text[curr+1] == '[':
                    brackets += 2
                    curr += 1
                 else:
                    brackets += 1
            elif char == ']':
                 if curr + 1 < len(text) and text[curr+1] == ']':
                    brackets -= 2
                    curr += 1
                 else:
                    brackets -= 1
            elif char == '|':
                if braces == 0 and brackets == 0:
                    parts.append(text[start:curr])
                    start = curr + 1

            curr += 1

        parts.append(text[start:])
        return parts

    def _extract_cards_from_block(self, text: str) -> List[DeckCard]:
        # Use brace counting instead of regex
        matches = self._extract_set_list_blocks(text)

        cards = []

        known_params = [
            'region', 'rarities', 'qty', 'options', 'width',
            'class', 'style', 'header', 'footer'
        ]

        for block in matches:
            # Smart split respecting nested templates
            parts = self._smart_split_params(block)

            list_content = ""
            default_qty = 1
            default_rarity = "Common" # Fallback

            # Heuristic: Find the part that is most likely the card list
            # It usually contains many semicolons and is NOT a known param.

            best_content_candidate = ""
            max_semicolons = 0

            for part in parts:
                part = part.strip()
                is_param = False

                # Check for key=value
                if '=' in part:
                    # Check if it looks like a known param
                    # Split only on the first =
                    key_cand, val_cand = part.split('=', 1)
                    key_cand = key_cand.strip().lower()

                    if key_cand in known_params:
                        is_param = True
                        if key_cand == 'qty':
                            try: default_qty = int(val_cand)
                            except: pass
                        elif key_cand == 'rarities':
                            if ',' in val_cand:
                                 val_cand = val_cand.split(',')[0].strip()
                            default_rarity = self._map_rarity(val_cand)

                if not is_param:
                    # Potential list content
                    semicolon_count = part.count(';')
                    if semicolon_count > 0:
                        # If multiple candidates, pick the one with most semicolons
                        if semicolon_count > max_semicolons:
                            max_semicolons = semicolon_count
                            best_content_candidate = part

            list_content = best_content_candidate

            card_lines = list_content.split('\n')

            for line in card_lines:
                line = line.strip()
                if not line: continue

                # Basic cleanup of line if it contains templates like {{=}}
                # Replace {{=}} with = for display (though we split by ;)
                line = line.replace('{{=}}', '=')

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

                rarity_str_val = rarity_str if rarity_str else default_rarity

                # Split rarities if comma separated
                rarities = [r.strip() for r in rarity_str_val.split(',')]

                for r in rarities:
                    # Strip comments like " // description::(alternate artwork)"
                    clean_r = re.split(r'\s*//', r)[0].strip()
                    mapped_rarity = self._map_rarity(clean_r)

                    cards.append(DeckCard(
                        code=code,
                        name=name,
                        rarity=mapped_rarity,
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

    def _parse_date(self, date_str: str) -> Optional[str]:
        """
        Parses date string (e.g. 'October 21, 2023') into 'YYYY-MM-DD'.
        Returns None if parsing fails.
        """
        if not date_str: return None

        # Clean up input (remove [[ ]], refs, etc)
        # e.g. [[October 21]], [[2023]]
        date_str = re.sub(r'\[\[(?:[^|\]]*\|)?([^\]]+)\]\]', r'\1', date_str)
        # Remove refs <ref>...</ref> or <ref ... />
        date_str = re.sub(r'<ref.*?>.*?</ref>', '', date_str)
        date_str = re.sub(r'<ref.*?/>', '', date_str)
        date_str = date_str.strip()

        formats = [
            "%B %d, %Y",       # October 21, 2023
            "%d %B %Y",        # 21 October 2023
            "%Y-%m-%d",        # 2023-10-21
            "%B %Y",           # October 2023
            "%Y",              # 2023
        ]

        for fmt in formats:
            try:
                dt = datetime.strptime(date_str, fmt)
                return dt.strftime("%Y-%m-%d")
            except ValueError:
                continue

        return None

    async def get_set_details(self, url: str) -> Optional[Dict[str, Any]]:
        """
        Parses a Yugipedia Set page URL.
        Returns dictionary with:
        - name: Set Name
        - code: Set Prefix (if found)
        - image_url: URL to set image
        - cards: List of card dicts {set_code, name, set_rarity}
        """
        try:
            # Extract title from URL
            match = re.search(r'/wiki/([^/]+)$', url)
            if not match:
                logger.error(f"Invalid Yugipedia URL: {url}")
                return None

            title = match.group(1)
            import urllib.parse
            title = urllib.parse.unquote(title)

            # Fetch Page
            wikitext = await self._fetch_wikitext(title)
            if not wikitext:
                return None

            # Parse Infobox for metadata
            # {{Infobox set ...}}
            # Use dotall to match across lines, but be careful with greedy matching
            infobox_match = re.search(r'\{\{Infobox set(.*?)(\n\}\}|^\}\})', wikitext, re.DOTALL | re.MULTILINE)
            if not infobox_match:
                 # Try looser match
                 infobox_match = re.search(r'\{\{Infobox set(.*)', wikitext, re.DOTALL)

            data = {
                "name": title.replace('_', ' '),
                "code": None,
                "image_url": None,
                "cards": [],
                "date": None
            }

            if infobox_match:
                ib_content = infobox_match.group(1)

                # Helper to extract param value (reused)
                def get_param(key: str, content: str) -> Optional[str]:
                    pattern = r'\|\s*' + re.escape(key) + r'\s*=\s*(.*?)(?=\n\s*\||\}\}|$)'
                    m = re.search(pattern, content, re.DOTALL)
                    if m: return m.group(1).strip()
                    return None

                data["name"] = get_param("en_name", ib_content) or data["name"]
                data["code"] = get_param("en_prefix", ib_content) or get_param("prefix", ib_content)

                # Date parsing
                raw_date = get_param("eu_release_date", ib_content) or \
                           get_param("eu_date", ib_content) or \
                           get_param("na_release_date", ib_content) or \
                           get_param("na_date", ib_content) or \
                           get_param("en_release_date", ib_content) or \
                           get_param("en_date", ib_content) or \
                           get_param("release_date", ib_content)

                if raw_date:
                    data["date"] = self._parse_date(raw_date)

            # Fetch Image URL via API if possible
            img_url = await self.get_set_image_url(title)
            if img_url:
                data["image_url"] = img_url

            # Parse Cards
            # 1. Check for {{Set list}} in current page
            cards = self._extract_cards_from_block(wikitext)

            # 2. If no cards, check for "Set Card Lists" link or logic
            if not cards:
                # Look for "Set Card Lists:..." link logic via get_deck_list
                # get_deck_list handles searching for the list page
                deck_list = await self.get_deck_list(title)
                if deck_list and (deck_list['main'] or deck_list['bonus']):
                     cards = deck_list['main'] + deck_list['bonus']

            # Format cards
            formatted_cards = []
            for c in cards:
                formatted_cards.append({
                    "set_code": c.code,
                    "name": c.name,
                    "set_rarity": c.rarity
                })

            data["cards"] = formatted_cards

            return data

        except Exception as e:
            logger.error(f"Error parsing set details from {url}: {e}")
            return None

    async def get_card_images(self, page_title: str) -> Tuple[Optional[str], Optional[str]]:
        """
        Fetches the high and low resolution image URLs for a card page.
        Returns (high_res_url, low_res_url).
        """
        params = {
            "action": "query",
            "titles": page_title,
            "prop": "pageimages",
            "format": "json",
            "piprop": "original|thumbnail",
            "pithumbsize": 300 # Matches typical Yugipedia thumb size
        }

        try:
            if hasattr(run, 'io_bound'):
                response = await run.io_bound(requests.get, self.API_URL, params=params, headers=self.HEADERS)
            else:
                response = await asyncio.to_thread(requests.get, self.API_URL, params=params, headers=self.HEADERS)

            if response.status_code == 200:
                data = response.json()
                pages = data.get("query", {}).get("pages", {})

                for pid, page in pages.items():
                    if pid == "-1": continue

                    high_res = None
                    low_res = None

                    # Original
                    if "original" in page:
                         high_res = page["original"].get("source")
                    elif "thumbnail" in page:
                         # Fallback if original property not directly present but nested?
                         # Yugipedia API output showed original inside thumbnail sometimes or separate?
                         # My test showed 'original' as separate property when piprop=original is set.
                         # But let's check thumbnail['original'] too as backup
                         if "original" in page["thumbnail"]:
                              high_res = page["thumbnail"]["original"]

                    # Thumbnail
                    if "thumbnail" in page:
                        low_res = page["thumbnail"].get("source")

                    return high_res, low_res

            return None, None
        except Exception as e:
            logger.error(f"Error fetching images for {page_title}: {e}")
            return None, None

    async def get_card_details(self, url: str) -> Optional[Dict[str, Any]]:
        """
        Parses a Yugipedia card page URL and returns card details.
        """
        try:
            # Extract title from URL
            # Format: https://yugipedia.com/wiki/Stardust_Dragon
            match = re.search(r'/wiki/([^/]+)$', url)
            if not match:
                logger.error(f"Invalid Yugipedia URL: {url}")
                return None

            title = match.group(1)
            # Decode URL encoding (e.g. %20 -> space)
            import urllib.parse
            title = urllib.parse.unquote(title)

            wikitext = await self._fetch_wikitext(title)
            if not wikitext:
                return None

            data = self._parse_card_table(wikitext, title)

            # Fetch Images
            high, low = await self.get_card_images(title)
            data["image_url"] = high
            data["image_url_small"] = low

            return data

        except Exception as e:
            logger.error(f"Error parsing card details from {url}: {e}")
            return None

    async def get_card_data_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """
        Fetches card details by name (page title) directly.
        """
        try:
            # Assume name matches page title (Yugipedia handles redirects usually)
            # Need to handle potential spaces vs underscores? Yugipedia API handles it.
            wikitext = await self._fetch_wikitext(name)
            if not wikitext:
                return None

            data = self._parse_card_table(wikitext, name)

            # Fetch Images
            high, low = await self.get_card_images(name)
            data["image_url"] = high
            data["image_url_small"] = low

            return data
        except Exception as e:
            logger.error(f"Error fetching card data for {name}: {e}")
            return None

    async def _fetch_wikitext(self, title: str) -> Optional[str]:
        params = {
            "action": "query",
            "titles": title,
            "prop": "revisions",
            "rvprop": "content",
            "format": "json"
        }

        try:
            if hasattr(run, 'io_bound'):
                response = await run.io_bound(requests.get, self.API_URL, params=params, headers=self.HEADERS)
            else:
                response = await asyncio.to_thread(requests.get, self.API_URL, params=params, headers=self.HEADERS)

            if response.status_code == 200:
                data = response.json()
                pages = data.get("query", {}).get("pages", {})
                for pid, page in pages.items():
                    if pid == "-1": return None
                    if "revisions" in page:
                        return page["revisions"][0]["*"]
        except Exception as e:
            logger.error(f"Error fetching wikitext for {title}: {e}")
        return None

    def _parse_card_table(self, wikitext: str, page_title: str) -> Dict[str, Any]:
        """Parses the {{CardTable2}} template."""
        data = {
            "name": page_title.replace('_', ' '),
            "type": "Normal Monster", # Default
            "desc": "",
            "atk": None,
            "def": None,
            "level": None,
            "race": None,
            "attribute": None,
            "sets": [],
            "database_id": None,
            "image_url": None
        }

        # Regex to find the content of CardTable2
        table_match = re.search(r'\{\{CardTable2(.*)\}\}', wikitext, re.DOTALL)
        if not table_match:
             # Try fallback if it's not strictly enclosed or has trailing chars
             table_match = re.search(r'\{\{CardTable2(.*)', wikitext, re.DOTALL)

        if not table_match:
            logger.warning("No CardTable2 found")
            return data

        content = table_match.group(1)
        # Remove comments from content
        content = re.sub(r'<!--.*?-->', '', content, flags=re.DOTALL)

        # Helper to extract param value
        def get_param(key: str) -> Optional[str]:
            # Matches | key = value (multiline safe)
            pattern = r'\|\s*' + re.escape(key) + r'\s*=\s*(.*?)(?=\n\s*\||\}\}|$)'
            m = re.search(pattern, content, re.DOTALL)
            if m:
                return m.group(1).strip()
            return None

        # Stats
        data["name"] = get_param("en_name") or get_param("name") or data["name"]

        # Attribute
        attr = get_param("attribute")
        if attr: data["attribute"] = attr.upper()

        # Types
        types_raw = get_param("types") or get_param("type")
        card_type = get_param("card_type")
        property_ = get_param("property")

        if types_raw:
            # Parse types "Dragon / Synchro / Effect"
            parts = [t.strip() for t in types_raw.split('/')]
            if parts:
                data["race"] = parts[0] # First is usually race

                # Construct Type string
                is_effect = "Effect" in parts

                base = ""
                if "Link" in parts: base = "Link Monster"
                elif "XYZ" in parts:
                    base = "XYZ Monster"
                    if "Pendulum" in parts: base = "XYZ Pendulum Effect Monster"
                elif "Synchro" in parts:
                    base = "Synchro Monster"
                    if "Pendulum" in parts: base = "Synchro Pendulum Effect Monster"
                    elif "Tuner" in parts: base = "Synchro Tuner Monster"
                elif "Fusion" in parts:
                    base = "Fusion Monster"
                elif "Ritual" in parts:
                    base = "Ritual Monster"
                elif "Pendulum" in parts:
                     base = "Pendulum Effect Monster" if is_effect else "Pendulum Normal Monster"
                elif "Token" in parts:
                    base = "Token"
                elif "Skill" in parts:
                    base = "Skill Card"
                elif "Spell" in parts:
                    base = "Spell Card"
                elif "Trap" in parts:
                    base = "Trap Card"
                else:
                    base = "Effect Monster" if is_effect else "Normal Monster"

                if base == "Normal Monster" and "Tuner" in parts: base = "Normal Tuner Monster"

                data["type"] = base
        elif card_type:
            # Fallback when 'types' is missing (e.g. Spells/Traps often have card_type=Spell)
            if "Spell" in card_type:
                data["type"] = "Spell Card"
            elif "Trap" in card_type:
                data["type"] = "Trap Card"
            elif "Skill" in card_type:
                data["type"] = "Skill Card"
            elif "Token" in card_type:
                data["type"] = "Token"

        # Race handling for Spells/Traps (use Property)
        if (not data["race"] or data["race"] == "None") and property_:
            data["race"] = property_

        # ATK/DEF/Level/Link
        atk = get_param("atk")
        if atk and atk.isdigit(): data["atk"] = int(atk)

        def_ = get_param("def")
        if def_ and def_.isdigit(): data["def"] = int(def_)

        level = get_param("level") or get_param("rank")
        if level and level.isdigit(): data["level"] = int(level)

        link_rating = get_param("link_rating")
        if link_rating and link_rating.isdigit():
             data["linkval"] = int(link_rating)
             # Map linkval to level for generic UI display if level is missing
             if data["level"] is None:
                 data["level"] = int(link_rating)

        link_arrows_raw = get_param("link_arrows")
        if link_arrows_raw:
             # Split by comma
             arrows = [a.strip() for a in link_arrows_raw.split(',')]
             data["linkmarkers"] = arrows

             # Fallback for linkval if explicitly missing
             if data.get("linkval") is None:
                  data["linkval"] = len(arrows)
                  if data["level"] is None:
                      data["level"] = len(arrows)

        # Desc
        text = get_param("text") or ""
        data["desc"] = self._clean_wikitext(text)

        # ID: Prioritize Passcode (printed on card) over database_id (internal ID)
        passcode = get_param("password") or get_param("passcode")
        db_id = get_param("database_id")

        if passcode and passcode.isdigit():
            data["database_id"] = int(passcode)
        elif db_id and db_id.isdigit():
             data["database_id"] = int(db_id)

        # Sets
        en_sets = get_param("en_sets")
        if en_sets:
            data["sets"] = self._parse_sets_data(en_sets)

        return data

    def _clean_wikitext(self, text: str) -> str:
        # Remove [[Link|Text]] -> Text
        text = re.sub(r'\[\[(?:[^|\]]*\|)?([^\]]+)\]\]', r'\1', text)
        # Remove <br /> -> \n
        text = text.replace('<br />', '\n').replace('<br>', '\n')
        # Remove ''Italic'' -> Italic
        text = text.replace("''", "")
        # Remove HTML tags
        text = re.sub(r'<[^>]+>', '', text)
        return text.strip()

    def _parse_sets_data(self, en_sets_str: str) -> List[Dict[str, str]]:
        sets = []
        # Format: Code; Name; Rarity
        lines = en_sets_str.strip().split('\n')
        for line in lines:
            parts = [p.strip() for p in line.split(';')]
            if len(parts) >= 3:
                code = parts[0]
                name = parts[1]
                rarity_raw = parts[2]

                rarities = [r.strip() for r in rarity_raw.split(',')]
                for r in rarities:
                    mapped_r = self._map_rarity(r)
                    sets.append({
                        "set_code": code,
                        "set_name": name,
                        "set_rarity": mapped_r
                    })
        return sets

yugipedia_service = YugipediaService()
