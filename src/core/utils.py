import re

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
