import re
import hashlib
from typing import Optional

# Region Code Mapping
# Maps region codes (both legacy 1-letter and standard 2-letter) to standard Language Codes.
REGION_TO_LANGUAGE_MAP = {
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
    'KR': 'KR',
    'ZH': 'ZH'
}

# Reverse mapping for legacy 1-letter codes.
# Used when transforming codes to preserve legacy format.
LANGUAGE_TO_LEGACY_REGION_MAP = {
    'EN': 'E',
    'DE': 'G',
    'FR': 'F',
    'IT': 'I',
    'ES': 'S',
    'PT': 'P',
    'JP': 'J',
    'KR': 'K'
}

# Mapping of Language Codes to Emoji Flags
LANGUAGE_FLAG_MAP = {
    'EN': '🇬🇧',
    'GB': '🇬🇧', # Handle GB explicitly
    'DE': '🇩🇪',
    'FR': '🇫🇷',
    'IT': '🇮🇹',
    'ES': '🇪🇸',
    'PT': '🇵🇹',
    'JP': '🇯🇵',
    'KR': '🇰🇷',
    'CN': '🇨🇳',
    'ZH': '🇨🇳',
    'TC': '🇨🇳', # Traditional Chinese
    'SC': '🇨🇳', # Simplified Chinese
    'AE': '🇦🇪', # Asian English (UAE flag? or just generic? Usually treated as EN, but if we distinguish...)
                 # Actually, usually AE in Yugioh is Asian English.
                 # Let's map AE to UK flag or maybe ignore?
                 # Existing code didn't handle AE.
                 # I'll stick to the ones that were present + GB.
}

def transform_set_code(set_code: str, language: str) -> str:
    """
    Transforms a set code based on the language.
    If the set code contains a region code (e.g. RA01-EN054, LOB-E001), replace it with the target language.
    If the existing set code uses a legacy 1-letter region (e.g. 'E', 'G'), try to use the corresponding
    1-letter code for the new language (e.g. 'G' for German, 'E' for English).
    If it does not have a region code (e.g. SDY-006), it remains unchanged.
    """
    lang_code = language.upper()

    # Case 1: Code-RegionNumber (e.g. RA01-EN054, LOB-E001)
    # We look for a hyphen, 1 or more letters (region), then digits.
    match = re.match(r'^([A-Za-z0-9]+)-([A-Za-z]+)(\d+)$', set_code)
    if match:
        prefix = match.group(1)
        region = match.group(2).upper()
        number = match.group(3)

        new_region_code = lang_code

        # Check if the existing region is a legacy 1-letter code
        # But we only want to switch to 1-letter if the target language SUPPORTS it.
        # And usually we only do this if the original was 1-letter?
        # Requirement: "SDK-E001" (1-letter) -> DE -> "SDK-G001" (1-letter)
        # Requirement: "SDK-EN001" (2-letter) -> DE -> "SDK-DE001" (2-letter)

        # Check if original was 1-letter and in our map (to avoid treating random 1-letter typos as legacy)
        if len(region) == 1 and region in REGION_TO_LANGUAGE_MAP:
             # Try to find legacy code for target language
             if lang_code in LANGUAGE_TO_LEGACY_REGION_MAP:
                 new_region_code = LANGUAGE_TO_LEGACY_REGION_MAP[lang_code]

        return f"{prefix}-{new_region_code}{number}"

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
        return REGION_TO_LANGUAGE_MAP.get(region, 'EN') # Default to EN if unknown region or unmapped

    # Case 2: No region code (e.g. SDY-006) -> Usually EN (NA print)
    return 'EN'

def is_set_code_compatible(set_code: str, language: str) -> bool:
    """
    Checks if a set code is compatible with the target language.
    Compatible means:
    1. The code has NO region identifier (e.g. LOB-001) -> Treated as neutral/base.
    2. The code has a region identifier that MATCHES the target language (e.g. LOB-DE001 for DE).
    3. The code has a legacy region identifier that MATCHES the target language (e.g. LOB-G001 for DE).

    Incompatible means:
    - The code has a region identifier for a DIFFERENT language (e.g. LOB-EN001 for DE).
    """
    target_lang = language.upper()

    # Extract Region
    match = re.match(r'^([A-Za-z0-9]+)-([A-Za-z]+)(\d+)$', set_code)
    if match:
        region = match.group(2).upper()

        # Resolve to standard language code
        # If region is not in map, assume it's some specific code we don't know, treat as mismatch unless equal?
        # But our map covers standard ones.
        mapped_lang = REGION_TO_LANGUAGE_MAP.get(region)

        if mapped_lang:
            return mapped_lang == target_lang
        else:
            # Unknown region code. Safe to assume incompatible?
            # Or maybe compatible if we don't know it?
            # Safest is strict check.
            return False

    # No region code (e.g. LOB-001) -> Compatible (Neutral/Base)
    return True

def get_legacy_code(prefix: str, number: str, language: str) -> Optional[str]:
    """
    Returns the legacy format code (e.g. LOB-G020) if a legacy mapping exists for the language.
    """
    legacy_char = LANGUAGE_TO_LEGACY_REGION_MAP.get(language.upper())
    if legacy_char:
        return f"{prefix}-{legacy_char}{number}"
    return None

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
