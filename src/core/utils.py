import re
import hashlib
from typing import Optional

def transform_set_code(set_code: str, language: str) -> str:
    """
    Transforms a set code based on the language.
    If the set code contains a 2-letter region code (e.g. RA01-EN054), replace it with the target language.
    If it does not (e.g. SDY-006), keep it as is.
    """
    # Regex for Code-RegionNumber (e.g. RA01-EN054)
    # We look for a hyphen, exactly 2 letters, then digits.
    match = re.match(r'^([A-Za-z0-9]+)-([A-Za-z]{2})(\d+)$', set_code)

    if match:
        prefix = match.group(1)
        number = match.group(3)
        lang_code = language.upper()
        return f"{prefix}-{lang_code}{number}"

    return set_code

def generate_variant_id(card_id: int, set_code: str, rarity: str, image_id: Optional[int] = None) -> str:
    """
    Generates a deterministic unique ID for a card variant using MD5.
    Useful for ensuring stable IDs across API fetches.
    """
    # Normalize inputs to ensure stability
    s_code = set_code.strip().upper()
    s_rarity = rarity.strip().lower()
    s_img = str(image_id) if image_id is not None else ""

    raw_str = f"{card_id}|{s_code}|{s_rarity}|{s_img}"
    return hashlib.md5(raw_str.encode('utf-8')).hexdigest()
