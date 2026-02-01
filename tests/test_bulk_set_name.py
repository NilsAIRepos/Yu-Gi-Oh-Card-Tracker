import pytest
from unittest.mock import MagicMock
from src.core.models import Collection, CollectionCard, CollectionVariant, CollectionEntry, ApiCard, ApiCardSet, ApiCardImage
from src.ui.bulk_add import _build_collection_entries

def test_build_collection_entries_german_card_set_name_resolution():
    # Setup ApiCard with EN set
    api_set = ApiCardSet(
        set_name="Legend of Blue Eyes",
        set_code="LOB-EN001",
        set_rarity="Ultra Rare"
    )
    api_card = ApiCard(
        id=123,
        name="Blue-Eyes White Dragon",
        type="Monster",
        frameType="normal",
        desc="Dragon",
        card_sets=[api_set],
        card_images=[ApiCardImage(id=123, image_url="", image_url_small="")]
    )

    # Setup Collection with DE variant (Transformed code: LOB-DE001)
    col_entry = CollectionEntry(language="DE", quantity=1)
    col_variant = CollectionVariant(
        variant_id="v1",
        set_code="LOB-DE001",
        rarity="Ultra Rare",
        image_id=123,
        entries=[col_entry]
    )
    col_card = CollectionCard(card_id=123, name="Blue-Eyes White Dragon", variants=[col_variant])
    collection = Collection(name="Test Col", cards=[col_card])

    api_map = {123: api_card}

    # Execute
    entries = _build_collection_entries(collection, api_map)

    # Verify
    assert len(entries) == 1
    entry = entries[0]
    assert entry.set_code == "LOB-DE001"

    # This is the expected behavior AFTER the fix.
    # Currently it should fail (it will return "Unknown Set").
    assert entry.set_name == "Legend of Blue Eyes"
