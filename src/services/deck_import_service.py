import aiohttp
import re
import json
import logging
import html
from typing import Optional
from src.core.models import Deck

logger = logging.getLogger(__name__)

async def fetch_ygoprodeck_deck(url: str) -> Optional[Deck]:
    """
    Fetches a deck from a YGOPRODeck URL.
    Parses the HTML to extract the deck name and card IDs.
    """
    if "ygoprodeck.com/deck/" not in url.lower():
        raise ValueError("Invalid URL. Must be a YGOPRODeck deck link.")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                if response.status != 200:
                    raise Exception(f"Failed to fetch URL: Status {response.status}")

                content = await response.text()

        # Extract Title
        title_match = re.search(r'<title>(.*?) - YGOPRODeck</title>', content)
        if title_match:
            name = html.unescape(title_match.group(1)).strip()
        else:
            name = "Imported Deck"

        # Extract Deck Data (JSON arrays in JS variables)
        main_match = re.search(r"var maindeckjs = '(\[.*?\])';", content)
        extra_match = re.search(r"var extradeckjs = '(\[.*?\])';", content)
        side_match = re.search(r"var sidedeckjs = '(\[.*?\])';", content)

        if not main_match:
             raise Exception("Could not find deck data in page source.")

        main_ids = json.loads(main_match.group(1))
        extra_ids = json.loads(extra_match.group(1)) if extra_match else []
        side_ids = json.loads(side_match.group(1)) if side_match else []

        # Convert strings to integers
        main = [int(x) for x in main_ids]
        extra = [int(x) for x in extra_ids]
        side = [int(x) for x in side_ids]

        return Deck(
            name=name,
            main=main,
            extra=extra,
            side=side
        )

    except Exception as e:
        logger.error(f"Error importing deck from {url}: {e}")
        raise e
