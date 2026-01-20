import pytest
from src.core.models import Collection, CollectionCard, CollectionVariant, CollectionEntry, ApiCard
from src.services.collection_editor import CollectionEditor

def test_apply_change_negative_quantity():
    # Setup
    col = Collection(name="test", cards=[])
    card = ApiCard(id=1, name="Test Card", type="Monster", desc="Desc", race="Dragon", attribute="DARK", frameType="normal")

    # Add initial 3 copies
    CollectionEditor.apply_change(
        collection=col,
        api_card=card,
        set_code="SET-001",
        rarity="Common",
        language="EN",
        quantity=3,
        condition="Near Mint",
        first_edition=False,
        image_id=1,
        mode="ADD"
    )

    # Verify initial state
    qty = CollectionEditor.get_quantity(col, 1, set_code="SET-001", rarity="Common", language="EN", image_id=1)
    assert qty == 3

    # Subtract 1 copy (ADD -1)
    CollectionEditor.apply_change(
        collection=col,
        api_card=card,
        set_code="SET-001",
        rarity="Common",
        language="EN",
        quantity=-1,
        condition="Near Mint",
        first_edition=False,
        image_id=1,
        mode="ADD"
    )

    # Verify state -> 2
    qty = CollectionEditor.get_quantity(col, 1, set_code="SET-001", rarity="Common", language="EN", image_id=1)
    assert qty == 2

    # Subtract 2 copies (ADD -2) -> Should remove entry
    CollectionEditor.apply_change(
        collection=col,
        api_card=card,
        set_code="SET-001",
        rarity="Common",
        language="EN",
        quantity=-2,
        condition="Near Mint",
        first_edition=False,
        image_id=1,
        mode="ADD"
    )

    # Verify state -> 0
    qty = CollectionEditor.get_quantity(col, 1, set_code="SET-001", rarity="Common", language="EN", image_id=1)
    assert qty == 0

    # Verify cleanup (no card in collection)
    assert len(col.cards) == 0
