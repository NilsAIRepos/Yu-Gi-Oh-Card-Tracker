import re
from difflib import SequenceMatcher
from typing import List, Tuple, Optional

# Common OCR mix-ups
# Key: The character that might be mistakenly read
# Value: The likely correct character(s)
OCR_MIXUPS = {
    'S': '5', '5': 'S',
    'I': '1', '1': 'I',
    'O': '0', '0': 'O',
    'Z': '7', '7': 'Z',
    'B': '8', '8': 'B',
    'Q': '0',
    'D': '0',
    'U': 'V', 'V': 'U'
}

# Specific mapping for the numeric part of a set code
NUMERIC_FIXES = {
    'S': '5', 'O': '0', 'I': '1', 'Z': '7', 'B': '8', 'Q': '0', 'D': '0'
}

# Specific mapping for the alpha part of a set code
ALPHA_FIXES = {
    '5': 'S', '0': 'O', '1': 'I', '7': 'Z', '8': 'B'
}

def fuzzy_ratio(s1: str, s2: str) -> int:
    """Returns a similarity score between 0 and 100."""
    if not s1 or not s2: return 0
    return int(SequenceMatcher(None, s1.lower(), s2.lower()).ratio() * 100)

def correct_set_code(raw_text: str) -> Tuple[str, List[str]]:
    """
    Returns (Best Guess, List of Alternatives).
    """
    raw_text = raw_text.replace(" ", "").upper()

    # 1. Regex Extraction of the code pattern from a larger string
    # Pattern: [3-4 chars] - [optional 2 chars] [3 chars]
    # We allow for some "mixup characters" in the regex

    # Basic cleanup
    # Ensure dash is present
    if '-' not in raw_text:
        # Try to find a split point
        # AAAA000 or AAAAEN000
        m = re.search(r'([A-Z0-9]{3,4})([A-Z0-9]{3,6})', raw_text)
        if m:
            raw_text = f"{m.group(1)}-{m.group(2)}"

    parts = raw_text.split('-')
    if len(parts) != 2:
        return raw_text, []

    prefix, suffix = parts

    # Fix Prefix: Mostly Alphanumeric.
    # Rare logic: If 4 chars and starts with '1', maybe it's 'I'? usually sets are letters.
    # But some sets have numbers.

    # Fix Suffix:
    # Logic: Look at last 3 characters. They MUST be digits for a valid card.
    # (Excluding special promos like "LOB-E001"? No, usually "EN001")

    fixed_suffix = suffix
    if len(suffix) >= 3:
        digits = list(suffix[-3:])
        for i, char in enumerate(digits):
            if not char.isdigit():
                if char in NUMERIC_FIXES:
                    digits[i] = NUMERIC_FIXES[char]

        # Reassemble
        fixed_suffix = suffix[:-3] + "".join(digits)

    # Fix Region in Suffix (before the digits)
    # If there are characters before the last 3 digits, they should be letters.
    if len(fixed_suffix) > 3:
        region_part = list(fixed_suffix[:-3])
        for i, char in enumerate(region_part):
            if char.isdigit(): # If digit found where letter should be
                if char in ALPHA_FIXES:
                    region_part[i] = ALPHA_FIXES[char]
        fixed_suffix = "".join(region_part) + fixed_suffix[-3:]

    corrected = f"{prefix}-{fixed_suffix}"

    alternatives = []
    if corrected != raw_text:
        alternatives.append(raw_text)

    return corrected, alternatives
