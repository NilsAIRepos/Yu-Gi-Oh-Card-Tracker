import os
import json
import requests
import logging
from nicegui import run
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Determine paths relative to project root, similar to other services
# Assuming running from root, or standard structure
DATA_DIR = os.path.join(os.getcwd(), "data")
BANLIST_DIR = os.path.join(DATA_DIR, "banlists")
API_URL = "https://db.ygoprodeck.com/api/v7/cardinfo.php"

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
        """Downloads TCG, OCG, and Goat banlists from the API."""
        if self._fetched: return

        # Also skip if files exist and are recent?
        # For simplicity, we just use session-based caching (one fetch per server run).
        # But if files exist, we might skip entirely to speed up dev?
        # User requirement: "Automatically download".
        # Safe bet: fetch once per session.

        logger.info("Fetching default banlists...")
        await self._fetch_and_save("TCG", "tcg")
        await self._fetch_and_save("OCG", "ocg")
        await self._fetch_and_save("Goat", "goat")
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
                    await self.save_banlist(name, ban_map)
                    logger.info(f"Updated banlist: {name} ({len(ban_map)} cards)")
                else:
                    logger.warning(f"No cards found for banlist {name}")
            else:
                logger.error(f"Failed to fetch {name} banlist: {response.status_code}")
        except Exception as e:
            logger.error(f"Error fetching {name} banlist: {e}")

    async def save_banlist(self, name: str, data: Dict[str, str]):
        """Saves a banlist (id -> status map) to a JSON file."""
        self._ensure_directory()
        filepath = os.path.join(BANLIST_DIR, f"{name}.json")
        content = {
            "name": name,
            "cards": data
        }
        await run.io_bound(self._write_json, filepath, content)

    def _write_json(self, filepath, content):
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(content, f, indent=2)

    async def load_banlist(self, name: str) -> Dict[str, str]:
        """Loads a banlist map (id -> status) from JSON."""
        filepath = os.path.join(BANLIST_DIR, f"{name}.json")
        if not os.path.exists(filepath):
            return {}

        try:
            content = await run.io_bound(self._read_json, filepath)
            return content.get("cards", {})
        except Exception as e:
            logger.error(f"Error loading banlist {name}: {e}")
            return {}

    def _read_json(self, filepath):
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)

    def get_banlists(self) -> List[str]:
        """Returns a list of available banlist names."""
        if not os.path.exists(BANLIST_DIR): return []
        files = [f.replace('.json', '') for f in os.listdir(BANLIST_DIR) if f.endswith('.json')]
        return sorted(files)

banlist_service = BanlistService()
