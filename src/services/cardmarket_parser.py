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
    comment: Optional[str] = None # New field for comments

    # Derived/Resolved fields
    set_rarity: str = "" # Full rarity name
    set_condition: str = "" # Full condition name
    failure_reason: Optional[str] = None

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
    RARITY_MAP = {v: k for k, v in RARITY_ABBREVIATIONS.items()}
    RARITY_MAP['QSCR'] = 'Quarter Century Secret Rare'

    # Cardmarket Specific Mappings (Overrides)
    RARITY_MAP['SUR'] = 'Super Rare'
    RARITY_MAP['SCR'] = 'Secret Rare'
    RARITY_MAP['UTR'] = 'Ultimate Rare'

    # Updated Regex:
    # 1. Number: \d{3} -> [A-Za-z0-9]+
    # 2. Added optional Comment Group: (?:\s+(.+?))? before price
    LINE_REGEX = re.compile(
        r'^(\d+)\s+(.+?)\s+([A-Za-z0-9]+)\s+([A-Z]{2})\s+([A-Z]{2})\s+([A-Z0-9]+)\s+([A-Za-z0-9]+)(?:\s+(First Edition))?(?:\s+(.+?))?\s+([\d,.]+\s+EUR)$'
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
            try:
                text = file_content.decode('utf-8')
            except UnicodeDecodeError:
                text = file_content.decode('latin-1')

        return CardmarketParser.parse_text(text)

    @staticmethod
    def parse_text(text: str) -> List[ParsedRow]:
        rows = []
        lines = text.split('\n')

        parsing_singles = False

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Section detection
            if "Yugioh Singles:" in line:
                parsing_singles = True
                continue

            # Stop parsing if we hit another header while parsing singles
            # Heuristic: Line ends with ':' and is not the singles header
            if parsing_singles and line.endswith(":") and not "Yugioh Singles:" in line:
                parsing_singles = False
                continue

            # Only process lines if we are inside the singles section
            if not parsing_singles:
                continue

            # Skip other known garbage lines inside section (if any)
            if any(x in line for x in ["Contents", "Article Value", "Shipping", "Total", "Trustee Service", "Unpaid:", "Paid:"]):
                continue

            match = CardmarketParser.LINE_REGEX.match(line)
            if match:
                qty_str, name, number, lang, cond, prefix, rarity_abbr, first_ed_group, comment_group, price_group = match.groups()

                full_condition = CardmarketParser.CONDITION_MAP.get(cond, "Near Mint")
                full_rarity = CardmarketParser.RARITY_MAP.get(rarity_abbr, rarity_abbr)

                is_first_ed = bool(first_ed_group)

                # Cleanup Name
                clean_name = re.sub(r'\s*\(V\.\d+\s*-\s*[^\)]+\)', '', name).strip()

                rows.append(ParsedRow(
                    quantity=int(qty_str),
                    name=clean_name,
                    number=number,
                    language=lang,
                    condition=cond,
                    set_condition=full_condition,
                    set_prefix=prefix,
                    rarity_abbr=rarity_abbr,
                    set_rarity=full_rarity,
                    first_edition=is_first_ed,
                    original_line=line,
                    comment=comment_group.strip() if comment_group else None
                ))
            else:
                # Log potential failures
                m_pot = re.match(r'^(\d+)\s', line)
                if m_pot:
                    qty = int(m_pot.group(1))
                    if qty < 1000:
                        logger.warning(f"Failed to parse potential card line: {line}")
                        # Optionally we could add a failed row here if we wanted strict reporting

        return rows
