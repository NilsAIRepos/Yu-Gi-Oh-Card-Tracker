import re
import hashlib
from typing import Optional
from functools import lru_cache

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

# Mapping of Language Codes to ISO Country Codes for Flag Images
LANGUAGE_COUNTRY_MAP = {
    'EN': 'gb',
    'GB': 'gb',
    'DE': 'de',
    'FR': 'fr',
    'IT': 'it',
    'ES': 'es',
    'PT': 'pt',
    'JP': 'jp',
    'KR': 'kr',
    'CN': 'cn',
    'ZH': 'cn',
    'TC': 'cn',
    'SC': 'cn',
    'AE': 'ae',
}

# Pre-calculate sorted region keys by length (descending) to match longest first
_SORTED_REGION_KEYS = sorted(REGION_TO_LANGUAGE_MAP.keys(), key=len, reverse=True)

@lru_cache(maxsize=1024)
def _parse_set_code(set_code: str):
    """
    Parses a set code into (Prefix, Region, Suffix)
    Region is matched against known valid region codes.
    Returns (prefix, region, suffix) or None if format doesn't match expected pattern.
    """
    if '-' not in set_code:
        return None

    parts = set_code.split('-', 1)
    prefix = parts[0]
    rest = parts[1]

    # Try to match start of 'rest' with a known region
    best_region = None

    for r in _SORTED_REGION_KEYS:
        if rest.startswith(r):
            # Potential match. Check if what follows is likely the number/suffix.
            # Usually suffix starts with a digit OR could be letter-digit mix.
            # But the region must be the *prefix* of 'rest'.
            # Also, we should prefer 'EN' over 'E' if rest is 'EN001'.
            # Since _SORTED_REGION_KEYS is sorted by length descending, 'EN' comes first.
            best_region = r
            break

    if best_region:
        suffix = rest[len(best_region):]
        # Only consider it a valid region match if there is a suffix
        # (e.g. LOB-EN is weird, usually LOB-EN001. But even LOB-EN could imply suffix is empty?)
        # Let's assume suffix must exist, but could be anything (digits, letters+digits)
        # Note: Some codes might be just Prefix-Region? Unlikely for card codes.
        if suffix:
            return prefix, best_region, suffix

    # If no region matched, check if it looks like Prefix-Number (No Region)
    # e.g. SDY-006. rest="006". No region starts with '0'.
    # So if no region matched, we treat region as None.
    return prefix, None, rest

def transform_set_code(set_code: str, language: str) -> str:
    """
    Transforms a set code based on the language.
    If the set code contains a region code (e.g. RA01-EN054, LOB-E001), replace it with the target language.
    If the existing set code uses a legacy 1-letter region (e.g. 'E', 'G'), try to use the corresponding
    1-letter code for the new language (e.g. 'G' for German, 'E' for English).
    If it does not have a region code (e.g. SDY-006), it remains unchanged.
    """
    lang_code = language.upper()
    parsed = _parse_set_code(set_code)

    if parsed:
        prefix, region, suffix = parsed

        if region:
            new_region_code = lang_code

            # Check if original was 1-letter and in our map
            if len(region) == 1:
                 # Try to find legacy code for target language
                 if lang_code in LANGUAGE_TO_LEGACY_REGION_MAP:
                     new_region_code = LANGUAGE_TO_LEGACY_REGION_MAP[lang_code]

            return f"{prefix}-{new_region_code}{suffix}"

        # No region found (e.g. SDY-006), return as is
        return set_code

    # Fallback to original if parsing completely failed (no hyphen?)
    return set_code

def normalize_set_code(set_code: str) -> str:
    """
    Normalizes a set code to Prefix-Number format, stripping the region code.
    e.g. SDY-G006 -> SDY-006
         LOB-EN005 -> LOB-005
         SDY-006 -> SDY-006
         SGX2-END16 -> SGX2-D16 (Strips EN, keeps D16)
    """
    parsed = _parse_set_code(set_code)
    if parsed:
        prefix, region, suffix = parsed
        if region:
            return f"{prefix}-{suffix}"
        return set_code # No region, so Prefix-Suffix is just set_code
    return set_code

def extract_language_code(set_code: str) -> str:
    """
    Extracts the language code from a set code.
    Returns a standard language code (e.g., 'EN', 'DE').
    Defaults to 'EN' if no specific region is found or if it maps to English.
    """
    parsed = _parse_set_code(set_code)
    if parsed:
        prefix, region, suffix = parsed
        if region:
             return REGION_TO_LANGUAGE_MAP.get(region, 'EN')

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
    parsed = _parse_set_code(set_code)

    if parsed:
        prefix, region, suffix = parsed
        if region:
            mapped_lang = REGION_TO_LANGUAGE_MAP.get(region)
            if mapped_lang:
                return mapped_lang == target_lang
            else:
                return False # Unknown region

        # No region -> Compatible
        return True

    return True # Unparsable -> assume compatible or ignore

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
