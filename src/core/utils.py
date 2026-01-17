import re
import hashlib
from typing import Optional

def transform_set_code(set_code: str, language: str) -> str:
    """
    Transforms a set code based on the language.
    If the set code contains a region code (e.g. RA01-EN054, LOB-E001), replace it with the target language.
    If it does not have a region code (e.g. SDY-006), inject the target language code.
    """
    lang_code = language.upper()

    # Case 1: Code-RegionNumber (e.g. RA01-EN054, LOB-E001)
    # We look for a hyphen, 1 or more letters (region), then digits.
    match = re.match(r'^([A-Za-z0-9]+)-([A-Za-z]+)(\d+)$', set_code)
    if match:
        prefix = match.group(1)
        # region = match.group(2) # discarded
        number = match.group(3)
        return f"{prefix}-{lang_code}{number}"

    # Case 2: Code-Number (e.g. SDY-006) - No region code
    # As per requirements, codes without region identifiers should remain unchanged.
    match = re.match(r'^([A-Za-z0-9]+)-(\d+)$', set_code)
    if match:
        return set_code

    return set_code

def normalize_set_code(set_code: str) -> str:
    """
    Normalizes a set code to Prefix-Number format, stripping the region code.
    e.g. SDY-G006 -> SDY-006
         LOB-EN005 -> LOB-005
         SDY-006 -> SDY-006
    """
    # Case 1: Code-RegionNumber
    match = re.match(r'^([A-Za-z0-9]+)-([A-Za-z]+)(\d+)$', set_code)
    if match:
        return f"{match.group(1)}-{match.group(3)}"

    # Case 2: Code-Number (Already normalized)
    match = re.match(r'^([A-Za-z0-9]+)-(\d+)$', set_code)
    if match:
        return set_code

    return set_code

def extract_language_code(set_code: str) -> str:
    """
    Extracts the language code from a set code.
    Returns a standard language code (e.g., 'EN', 'DE').
    Defaults to 'EN' if no specific region is found or if it maps to English.
    """
    # Regex to find region code: Code-RegionNumber (e.g. MRD-DE001, LOB-E001)
    match = re.match(r'^([A-Za-z0-9]+)-([A-Za-z]+)(\d+)$', set_code)
    if match:
        region = match.group(2).upper()

        # Legacy/Region Mapping
        mapping = {
            'E': 'EN',
            'G': 'DE',
            'F': 'FR',
            'I': 'IT',
            'S': 'ES',
            'P': 'PT',
            'J': 'JP',
            'K': 'KR',
            'AE': 'EN', # Asian English
            'TC': 'ZH',
            'SC': 'ZH',
            # Standard codes map to themselves
            'EN': 'EN',
            'DE': 'DE',
            'FR': 'FR',
            'IT': 'IT',
            'ES': 'ES',
            'PT': 'PT',
            'JP': 'JP',
            'KR': 'KR'
        }

        return mapping.get(region, 'EN') # Default to EN if unknown region or unmapped

    # Case 2: No region code (e.g. SDY-006) -> Usually EN (NA print)
    return 'EN'

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
