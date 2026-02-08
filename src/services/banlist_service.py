import os
import json
import requests
import logging
from nicegui import run
from typing import Dict, List, Optional
from datetime import datetime
import re
from src.services.ygo_api import ygo_service

logger = logging.getLogger(__name__)

# Determine paths relative to project root, similar to other services
# Assuming running from root, or standard structure
DATA_DIR = os.path.join(os.getcwd(), "data")
BANLIST_DIR = os.path.join(DATA_DIR, "banlists")
API_URL = "https://db.ygoprodeck.com/api/v7/cardinfo.php"
GENESYS_URL = "https://www.yugioh-card.com/en/genesys/"

class BanlistService:
    def __init__(self):
        self._fetched = False
        self._ensure_directory()

    def _ensure_directory(self):
        if not os.path.exists(BANLIST_DIR):
            try:
                os.makedirs(BANLIST_DIR)
            except OSError as e:
                logger.error(f"Failed to create banlist directory: {e}")

    async def fetch_default_banlists(self):
        """Downloads TCG, OCG, Goat, and Genesys banlists."""
        # Force fetch whenever requested, ignore previous fetched state for explicit user action
        # if self._fetched: return

        logger.info("Fetching default banlists...")
        await self._fetch_and_save("TCG", "tcg")
        await self._fetch_and_save("OCG", "ocg")
        await self._fetch_and_save("Goat", "goat")
        await self.fetch_genesys_banlist()
        self._fetched = True
        logger.info("Default banlists fetch complete.")

    async def _fetch_and_save(self, name: str, api_param: str):
        try:
            url = f"{API_URL}?banlist={api_param}"
            # Use io_bound for network request to avoid blocking main thread
            response = await run.io_bound(requests.get, url)

            if response.status_code == 200:
                data = response.json()
                ban_map = {}

                key = f"ban_{api_param}"

                for card in data.get('data', []):
                    # API response structure for banlist_info
                    info = card.get('banlist_info', {})
                    status = info.get(key)

                    if status:
                         ban_map[str(card['id'])] = status

                if ban_map:
                    # Determine date - Fallback to 1st of current month
                    now = datetime.now()
                    fallback_date = now.strftime("%Y-%m-01")

                    # We don't have scraped effective date for TCG/OCG API yet, so we use fallback
                    # This prevents daily duplicates as requested.

                    await self.save_banlist(name, ban_map, date=fallback_date, banlist_type="classical")
                    logger.info(f"Updated banlist: {name} ({len(ban_map)} cards)")
                else:
                    logger.warning(f"No cards found for banlist {name}")
            else:
                logger.error(f"Failed to fetch {name} banlist: {response.status_code}")
        except Exception as e:
            logger.error(f"Error fetching {name} banlist: {e}")

    async def fetch_genesys_banlist(self):
        """Fetches and parses the Genesys Points list."""
        logger.info("Fetching Genesys banlist...")
        try:
            # 1. Fetch HTML content
            response = await run.io_bound(requests.get, GENESYS_URL)
            if response.status_code != 200:
                logger.error(f"Failed to fetch Genesys page: {response.status_code}")
                return

            text = response.text

            # 2. Parse Text
            # We look for the table structure: "Card Name" ... "Points"
            # Since we only get raw HTML/Text, and view_text_website gave us a clean table representation,
            # we rely on regex pattern matching for lines that look like card entries.
            # However, requests.get returns HTML. We need to be careful.
            # Or we can treat the whole page text.
            # Simpler approach: Locate the table rows.
            # Structure usually: <tr><td>Name</td><td>Points</td></tr>

            # Regex for table rows
            # <tr>\s*<td>(.*?)</td>\s*<td>(\d+)</td>\s*</tr>
            # This is fragile but standard for simple tables.

            # Matches: <tr><td>Card Name</td><td>Points</td></tr>
            # Note: The website might use th, or different attributes.
            # Flexible regex for table cells with potential attributes

            matches = re.findall(r'<tr[^>]*>\s*<td[^>]*>(.*?)</td>\s*<td[^>]*>\s*(\d+)\s*</td>', text, re.DOTALL | re.IGNORECASE)

            if not matches:
                # Fallback: maybe view_text_website saw a text version?
                # The user saw text output.
                # Let's try to parse the text representation if we can get it, but requests.get returns HTML.
                # If regex fails, the table structure might be complex.
                logger.warning("Genesys regex found no matches. Page structure might have changed.")
                # Try finding text directly if it's not in standard tr/td
                return

            # 3. Resolve Cards
            ban_map = {}

            # Ensure DB loaded for search
            await ygo_service.load_card_database()

            for name_raw, points in matches:
                # Clean name (remove HTML entities, etc if needed)
                name = name_raw.strip()
                # Basic cleaning
                name = name.replace('&amp;', '&').replace('&#8217;', "'").replace('’', "'")

                # Search
                card = ygo_service.search_by_name(name)
                if card:
                    ban_map[str(card.id)] = points
                else:
                    logger.warning(f"Genesys: Card not found '{name}'")

            if ban_map:
                now = datetime.now()
                fallback_date = now.strftime("%Y-%m-01")
                await self.save_banlist("Genesys", ban_map, date=fallback_date, banlist_type="genesys", max_points=100)
                logger.info(f"Updated banlist: Genesys ({len(ban_map)} cards)")

        except Exception as e:
            logger.error(f"Error fetching Genesys: {e}")

    async def save_banlist(self, name: str, data: Dict[str, str], date: Optional[str] = None, banlist_type: str = "classical", max_points: int = 100):
        """
        Saves a banlist (id -> status map) to a JSON file.
        If date is provided, appends it to the filename and includes it in JSON.
        """
        self._ensure_directory()

        filename = name
        if date:
            filename = f"{name}_{date}"

        filepath = os.path.join(BANLIST_DIR, f"{filename}.json")

        content = {
            "name": name,
            "type": banlist_type,
            "cards": data
        }
        if banlist_type == "genesys":
            content["max_points"] = max_points

        if date:
            content["date"] = date

        await run.io_bound(self._write_json, filepath, content)

    def _write_json(self, filepath, content):
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(content, f, indent=2)

    async def load_banlist(self, name: str) -> Dict:
        """
        Loads the full banlist content (including metadata) from JSON.
        'name' can be the full filename (minus extension) or just the prefix.
        Returns a Dict which always contains "cards".
        """
        filepath = os.path.join(BANLIST_DIR, f"{name}.json")
        if not os.path.exists(filepath):
            return {"cards": {}}

        try:
            content = await run.io_bound(self._read_json, filepath)
            if "cards" not in content:
                content["cards"] = {}
            return content
        except Exception as e:
            logger.error(f"Error loading banlist {name}: {e}")
            return {"cards": {}}

    def _read_json(self, filepath):
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)

    def get_banlists(self) -> List[str]:
        """Returns a list of available banlist names (filenames without extension)."""
        if not os.path.exists(BANLIST_DIR): return []
        files = [f.replace('.json', '') for f in os.listdir(BANLIST_DIR) if f.endswith('.json')]
        # Sort by Name then Date (descending date usually better? or just alphabetical?)
        # Alphabetical puts Genesys_2024... together.
        return sorted(files, reverse=True) # Reverse ensures newest dates usually come first if format is YYYY-MM-DD

banlist_service = BanlistService()
