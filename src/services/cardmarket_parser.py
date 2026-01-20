import re
import io
import logging
from typing import List, Dict, Optional, Any
from dataclasses import dataclass

from src.core.constants import RARITY_ABBREVIATIONS

logger = logging.getLogger(__name__)

@dataclass
class ParsedRow:
    quantity: int
    name: str
    number: str
    language: str
    condition: str
    set_prefix: str
    rarity_abbr: str
    first_edition: bool
    original_line: str

    # Derived/Resolved fields
    set_rarity: str = "" # Full rarity name
    set_condition: str = "" # Full condition name

class CardmarketParser:

    # Mapping Cardmarket codes to internal full names
    CONDITION_MAP = {
        'M': 'Mint',
        'NM': 'Near Mint',
        'EX': 'Excellent',
        'GD': 'Good',
        'LP': 'Light Played',
        'PL': 'Played',
        'PO': 'Poor'
    }

    # Reverse mapping for rarities (Abbr -> Full Name)
    # Note: Some abbreviations might be ambiguous or missing, we do our best.
    RARITY_MAP = {v: k for k, v in RARITY_ABBREVIATIONS.items()}

    # Regex to parse the line
    # Format: Qty Name Number Lang Condition SetPrefix Rarity [First Edition] Price Currency
    # Example: 1 Hinotama Soul (V.1 - Common) 020 DE NM LOB C 0,04 EUR
    # Example: 1 Cure Mermaid (V.2 - Common) 041 EN NM LON C First Edition 0,02 EUR
    # We use a non-greedy match for Name (.+?) and anchor the rest to the specific columns.
    # Note: Rarity can be 1-4 chars (C, R, ScR, 10000ScR).
    # Price is usually "0,04 EUR" or "1.234,00 EUR" (European format).
    LINE_REGEX = re.compile(
        r'^(\d+)\s+(.+?)\s+(\d{3})\s+([A-Z]{2})\s+([A-Z]{2})\s+([A-Z0-9]+)\s+([A-Za-z0-9]+)(?:\s+(First Edition))?\s+[\d,.]+\s+EUR$'
    )

    @staticmethod
    def parse_file(file_content: bytes, filename: str) -> List[ParsedRow]:
        """
        Parses a file (PDF or Text) and returns a list of ParsedRows.
        """
        text = ""
        if filename.lower().endswith('.pdf'):
            try:
                import pypdf
                reader = pypdf.PdfReader(io.BytesIO(file_content))
                for page in reader.pages:
                    text += page.extract_text() + "\n"
            except ImportError:
                raise ImportError("pypdf is required for PDF import. Please install it.")
            except Exception as e:
                logger.error(f"Error parsing PDF: {e}")
                raise ValueError(f"Failed to parse PDF: {e}")
        else:
            # Assume text/json/csv - but here we only handle the text dump format provided
            try:
                text = file_content.decode('utf-8')
            except UnicodeDecodeError:
                text = file_content.decode('latin-1') # Fallback

        return CardmarketParser.parse_text(text)

    @staticmethod
    def parse_text(text: str) -> List[ParsedRow]:
        rows = []
        lines = text.split('\n')

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Skip header lines commonly found in Cardmarket PDFs
            if any(x in line for x in ["Yugioh Singles:", "Contents", "Article Value", "Shipping", "Total", "Trustee Service"]):
                continue

            # Skip date/time lines
            if "Unpaid:" in line or "Paid:" in line:
                continue

            match = CardmarketParser.LINE_REGEX.match(line)
            if match:
                qty_str, name, number, lang, cond, prefix, rarity_abbr, first_ed_group = match.groups()

                # Normalize data
                full_condition = CardmarketParser.CONDITION_MAP.get(cond, "Near Mint") # Default fallback?
                full_rarity = CardmarketParser.RARITY_MAP.get(rarity_abbr, rarity_abbr) # Fallback to abbr if unknown

                is_first_ed = bool(first_ed_group)

                # Cleanup Name: remove "(V.X - Rarity)" if present, as it confuses fuzzy matching if needed
                # Example: "Hinotama Soul (V.1 - Common)" -> "Hinotama Soul"
                clean_name = re.sub(r'\s*\(V\.\d+\s*-\s*[^\)]+\)', '', name).strip()

                rows.append(ParsedRow(
                    quantity=int(qty_str),
                    name=clean_name,
                    number=number,
                    language=lang,
                    condition=cond, # Store raw code
                    set_condition=full_condition,
                    set_prefix=prefix,
                    rarity_abbr=rarity_abbr,
                    set_rarity=full_rarity,
                    first_edition=is_first_ed,
                    original_line=line
                ))
            else:
                # Log or track unparsed lines?
                # For now we skip, but the controller might want to know about them.
                # But since we are inside a static method returning a list, we might miss them.
                # Let's return them as a special error row?
                # Or just ignore assuming they are garbage/headers.
                # Given strict requirements, maybe we should log invalid lines that LOOK like data.
                if re.match(r'^\d+\s', line): # Starts with a number, likely a card line that failed regex
                    logger.warning(f"Failed to parse potential card line: {line}")
                    # We could add a 'failed' row type if needed, but for now let's just log.

        return rows
