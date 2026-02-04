import os
import json
import requests
import logging
import re
import datetime
from nicegui import run
from typing import Dict, List, Optional, Any
from src.services.ygo_api import ygo_service
from src.core.config import config_manager

logger = logging.getLogger(__name__)

# Determine paths relative to project root, similar to other services
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

    async def fetch_default_banlists(self, force: bool = False):
        """Downloads TCG, OCG, Goat, Genesys, and Master Duel banlists."""
        if self._fetched and not force:
            return

        logger.info("Fetching default banlists...")

        # 1. Classic Lists (TCG, OCG, Goat)
        await self._fetch_and_save("TCG", "tcg")
        await self._fetch_and_save("OCG", "ocg")
        await self._fetch_and_save("Goat", "goat")

        # 2. Genesys (Points)
        await self.fetch_genesys_banlist()

        # 3. Master Duel (Classic with Date)
        await self.fetch_master_duel_banlist()

        self._fetched = True
        logger.info("Default banlists fetch complete.")

    async def _fetch_and_save(self, name: str, api_param: str):
        try:
            url = f"{API_URL}?banlist={api_param}"
            response = await run.io_bound(requests.get, url)

            if response.status_code == 200:
                data = response.json()
                ban_map = {}
                key = f"ban_{api_param}"

                for card in data.get('data', []):
                    info = card.get('banlist_info', {})
                    status = info.get(key)
                    if status:
                         ban_map[str(card['id'])] = status

                if ban_map:
                    # Save as Classic
                    await self.save_banlist(name, ban_map, metadata={"type": "classic"})
                    logger.info(f"Updated banlist: {name} ({len(ban_map)} cards)")
                else:
                    logger.warning(f"No cards found for banlist {name}")
            else:
                logger.error(f"Failed to fetch {name} banlist: {response.status_code}")
        except Exception as e:
            logger.error(f"Error fetching {name} banlist: {e}")

    async def fetch_genesys_banlist(self):
        """Fetches Genesys points list and saves it as a Points banlist."""
        try:
            # Ensure we have card data for name resolution
            lang = config_manager.get_language()
            cards = await ygo_service.load_card_database(lang)

            # Create Name -> ID map (normalize names)
            name_map = {}
            for c in cards:
                name_map[c.name.lower()] = c.id

            logger.info("Fetching Genesys banlist...")
            response = await run.io_bound(requests.get, GENESYS_URL)

            if response.status_code != 200:
                logger.error(f"Failed to fetch Genesys page: {response.status_code}")
                return

            html = response.text

            # Parse Table using Regex
            # Pattern: <tr>\s*<td>(.*?)</td>\s*<td>(\d+)</td>\s*</tr>
            pattern = re.compile(r'<tr>\s*<td>(.*?)</td>\s*<td>(\d+)</td>\s*</tr>', re.IGNORECASE | re.DOTALL)
            matches = pattern.findall(html)

            points_map = {}

            for name_raw, points_str in matches:
                name = name_raw.strip()
                # Basic HTML entity cleanup
                name = name.replace('&quot;', '"').replace('&amp;', '&').replace('&#039;', "'")

                cid = name_map.get(name.lower())
                if cid:
                    points_map[str(cid)] = int(points_str)
                else:
                    # Fallback: Try stripping quotes if they exist in name but not map
                    if name.startswith('"') and name.endswith('"'):
                         inner = name[1:-1]
                         cid = name_map.get(inner.lower())
                         if cid:
                             points_map[str(cid)] = int(points_str)

                    if not cid:
                        logger.warning(f"Genesys: Could not resolve card '{name}'")

            if points_map:
                await self.save_banlist("Genesys", points_map, metadata={
                    "type": "points",
                    "max_points": 100
                })
                logger.info(f"Updated Genesys banlist ({len(points_map)} cards)")
            else:
                logger.warning("No cards found for Genesys banlist via regex.")

        except Exception as e:
            logger.error(f"Error fetching Genesys banlist: {e}", exc_info=True)

    async def fetch_master_duel_banlist(self):
        """Fetches Master Duel banlist and saves with Date in filename."""
        try:
            url = f"{API_URL}?banlist=Master Duel"
            response = await run.io_bound(requests.get, url)

            if response.status_code == 200:
                data = response.json()
                ban_map = {}
                key = "ban_master_duel"

                for card in data.get('data', []):
                    info = card.get('banlist_info', {})
                    # Try specific key first, fallback to checking others if structure differs
                    status = info.get(key)
                    if status:
                         ban_map[str(card['id'])] = status

                if ban_map:
                    date_str = datetime.date.today().strftime("%Y-%m-%d")
                    name = f"Master Duel_{date_str}"
                    await self.save_banlist(name, ban_map, metadata={
                        "type": "classic",
                        "date": date_str
                    })
                    logger.info(f"Updated Master Duel banlist: {name}")
            else:
                logger.error(f"Failed to fetch MD banlist: {response.status_code}")
        except Exception as e:
            logger.error(f"Error fetching MD banlist: {e}")

    async def save_banlist(self, name: str, data: Dict[str, Any], metadata: Dict[str, Any] = None):
        """
        Saves a banlist to a JSON file.
        data: mapping of card_id -> status/points
        metadata: extra fields like type, max_points, date
        """
        self._ensure_directory()
        filepath = os.path.join(BANLIST_DIR, f"{name}.json")

        content = {
            "name": name,
            "cards": data
        }
        if metadata:
            content.update(metadata)

        # Ensure type is set
        if "type" not in content:
            content["type"] = "classic"

        await run.io_bound(self._write_json, filepath, content)

    def _write_json(self, filepath, content):
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(content, f, indent=2)

    async def load_banlist(self, name: str) -> Dict[str, Any]:
        """
        Loads the FULL banlist object from JSON.
        Returns dict with keys: name, cards, type, max_points, etc.
        """
        filepath = os.path.join(BANLIST_DIR, f"{name}.json")
        if not os.path.exists(filepath):
            return {"name": name, "cards": {}, "type": "classic"}

        try:
            content = await run.io_bound(self._read_json, filepath)
            if "type" not in content:
                content["type"] = "classic"
            return content
        except Exception as e:
            logger.error(f"Error loading banlist {name}: {e}")
            return {"name": name, "cards": {}, "type": "classic"}

    def _read_json(self, filepath):
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)

    def get_banlists(self) -> List[str]:
        """Returns a list of available banlist names."""
        if not os.path.exists(BANLIST_DIR): return []
        files = [f.replace('.json', '') for f in os.listdir(BANLIST_DIR) if f.endswith('.json')]
        return sorted(files)

    def get_banlists_details(self) -> List[Dict[str, Any]]:
        """
        Returns a list of summary dicts for all banlists.
        Useful for UI dropdowns to show types/icons.
        """
        if not os.path.exists(BANLIST_DIR): return []

        results = []
        files = sorted([f for f in os.listdir(BANLIST_DIR) if f.endswith('.json')])

        for f in files:
            path = os.path.join(BANLIST_DIR, f)
            try:
                with open(path, 'r', encoding='utf-8') as fh:
                    data = json.load(fh)
                    results.append({
                        "name": data.get("name", f.replace('.json', '')),
                        "filename": f.replace('.json', ''),
                        "type": data.get("type", "classic"),
                        "max_points": data.get("max_points")
                    })
            except Exception as e:
                logger.warning(f"Failed to read banlist {f}: {e}")
                results.append({
                    "name": f.replace('.json', ''),
                    "filename": f.replace('.json', ''),
                    "type": "classic"
                })
        return results

banlist_service = BanlistService()
