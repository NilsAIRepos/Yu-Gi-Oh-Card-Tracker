from src.core.models import ApiCard
from typing import List, Optional

def test_matches_category():
    print("Testing ApiCard.matches_category logic...")

    # Stardust Dragon
    stardust = ApiCard(
        id=1, name="Stardust Dragon", type="Synchro Monster", frameType="synchro", desc="...",
        typeline=["Dragon", "Synchro", "Effect"]
    )
    # Gaia Knight
    gaia = ApiCard(
        id=2, name="Gaia Knight", type="Synchro Monster", frameType="synchro", desc="...",
        typeline=["Warrior", "Synchro"]
    )
    # Blue-Eyes
    blue_eyes = ApiCard(
        id=3, name="Blue-Eyes", type="Normal Monster", frameType="normal", desc="...",
        typeline=["Dragon", "Normal"]
    )
    # Token (assuming no typeline or no Normal/Effect in it)
    token = ApiCard(
        id=4, name="Token", type="Token", frameType="token", desc="...",
        typeline=[] # Assume empty
    )

    # Test "Normal" Filter
    be_normal = blue_eyes.matches_category('Normal')
    sd_normal = stardust.matches_category('Normal')
    gaia_normal = gaia.matches_category('Normal')

    print(f"Blue-Eyes is Normal? {be_normal}") # Expected: True
    print(f"Stardust is Normal? {sd_normal}")   # Expected: False
    print(f"Gaia is Normal? {gaia_normal}")     # Expected: True

    assert be_normal == True, "Blue-Eyes should be Normal"
    assert sd_normal == False, "Stardust should NOT be Normal"
    assert gaia_normal == True, "Gaia Knight SHOULD be Normal"

    # Test "Effect" Filter
    be_effect = blue_eyes.matches_category('Effect')
    sd_effect = stardust.matches_category('Effect')
    gaia_effect = gaia.matches_category('Effect')

    print(f"Blue-Eyes is Effect? {be_effect}") # Expected: False
    print(f"Stardust is Effect? {sd_effect}")   # Expected: True
    print(f"Gaia is Effect? {gaia_effect}")     # Expected: False

    assert be_effect == False, "Blue-Eyes should NOT be Effect"
    assert sd_effect == True, "Stardust SHOULD be Effect"
    assert gaia_effect == False, "Gaia Knight should NOT be Effect"

    # Test Other Categories
    assert stardust.matches_category('Synchro') == True
    assert gaia.matches_category('Synchro') == True
    assert blue_eyes.matches_category('Dragon') == True

    print("SUCCESS: ApiCard.matches_category handles Gaia Knight correctly.")

if __name__ == "__main__":
    try:
        test_matches_category()
    except AssertionError as e:
        print(f"FAILURE: {e}")
    except Exception as e:
        print(f"Error: {e}")
