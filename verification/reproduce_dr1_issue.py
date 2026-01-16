
import sys
import os
import asyncio
import logging
from unittest.mock import MagicMock

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.core.models import Collection, CollectionCard, CollectionVariant, CollectionEntry, ApiCard, ApiCardSet, ApiCardImage
from src.core.utils import generate_variant_id, transform_set_code
from src.ui.collection import build_collector_rows

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def reproduce_dr1_issue():
    logger.info("Starting DR1-DE063 reproduction...")

    # 1. Setup Data for Pitch-Dark Dragon (DR1-DE063)
    card_id = 999
    # The API might have DR1-EN063 or DR1-DE063.
    # Let's assume API has DR1-DE063 explicitly as a set code.

    api_set = ApiCardSet(
        set_name="Dark Revelation Volume 1",
        set_code="DR1-DE063",
        set_rarity="Common",
        image_id=999200
    )

    api_card = ApiCard(
        id=card_id,
        name="Pitch-Dark Dragon",
        type="Monster",
        frameType="normal",
        desc="Desc",
        race="Dragon",
        card_sets=[api_set],
        card_images=[ApiCardImage(id=999200, image_url="u", image_url_small="s")]
    )

    # 2. Simulate User Owning this card
    # If the user owns DR1-DE063, how is it stored?
    # variant_id is generated from (card_id, set_code, rarity, image_id)

    owned_set_code = "DR1-DE063"
    owned_rarity = "Common"
    owned_image_id = 999200

    variant_id = generate_variant_id(card_id, owned_set_code, owned_rarity, owned_image_id)
    logger.info(f"Stored Variant ID: {variant_id}")

    owned_variant = CollectionVariant(
        variant_id=variant_id,
        set_code=owned_set_code,
        rarity=owned_rarity,
        image_id=owned_image_id,
        entries=[CollectionEntry(language="DE", quantity=1)]
    )

    owned_card = CollectionCard(card_id=card_id, name="Pitch-Dark Dragon", variants=[owned_variant])
    owned_details = {card_id: owned_card}

    # 3. Build Collector Rows
    # We expect the row to carry the correct ID and Code.
    rows = build_collector_rows([api_card], owned_details, "DE")

    if not rows:
        logger.error("No rows!")
        return

    row = rows[0]
    logger.info(f"Row generated: Code={row.set_code}, ImageID={row.image_id}")

    # 4. Simulate 'SingleCardView' Logic for Removal
    # When SingleCardView opens, it gets 'row.set_code' (DR1-DE063).
    # It sets input_state['set_base_code'] = "DR1-DE063".
    # User clicks Remove.
    # handle_update calls:
    # final_code = transform_set_code(base_code, language)

    ui_language = "DE" # Suppose user is viewing in DE context?
    # Or typically the input_state['language'] defaults to the card's language if opened?
    # In 'open_collectors', input_state['language'] = row.language (which is "DE").

    final_code = transform_set_code(row.set_code, ui_language)
    logger.info(f"UI Base Code: {row.set_code}, Lang: {ui_language} -> Final Code: {final_code}")

    # Then it generates ID
    # Note: open_collectors sets input_state['image_id'] = row.image_id.

    ui_gen_id = generate_variant_id(card_id, final_code, row.rarity, row.image_id)
    logger.info(f"UI Generated ID: {ui_gen_id}")

    if ui_gen_id != variant_id:
        logger.error("MISMATCH! Removal will fail.")

        # Why mismatch?
        # Check transform_set_code logic for DR1-DE063
        if final_code != owned_set_code:
             logger.error(f"Transform changed code: {owned_set_code} -> {final_code}")
    else:
        logger.info("IDs Match. Removal should work.")

    # Test Scenario 2: Legacy Code / Region Code Logic
    # What if the user owns "DR1-DE063" but selects language "EN" in the dropdown?
    # This transforms it to DR1-EN063?
    # If so, the generated ID changes, and we can't remove the DE variant.
    # But in Collectors View, we are looking at a specific row (DE).
    # Does the UI allow changing language in Collectors View?
    # Yes, "Manage Inventory" allows changing language.
    # But for "REMOVE", we just want to remove *this* one.
    # The 'Remove' button in `SingleCardView` calls `handle_update('SET')` with quantity 0.
    # `handle_update` reads `input_state`.
    # `input_state` is initialized from the row.

    # Verify transform_set_code behavior specifically
    t1 = transform_set_code("DR1-DE063", "EN")
    logger.info(f"DR1-DE063 + EN -> {t1}")

    t2 = transform_set_code("DR1-DE063", "DE")
    logger.info(f"DR1-DE063 + DE -> {t2}")

if __name__ == "__main__":
    asyncio.run(reproduce_dr1_issue())
