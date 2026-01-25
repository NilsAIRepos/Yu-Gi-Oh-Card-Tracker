import sys
import os
import asyncio
import json
import shutil
from src.core.models import Collection, ApiCard, ApiCardSet, ApiCardImage
from src.services.collection_editor import CollectionEditor
from src.core.persistence import persistence
from src.core.changelog_manager import changelog_manager

# Mock Data
MOCK_TEMP_PATH = "data/scans/test_scans_temp.json"
MOCK_TARGET_PATH = "data/collections/test_target.json"

def setup():
    os.makedirs("data/scans", exist_ok=True)
    os.makedirs("data/collections", exist_ok=True)
    # Clean previous
    if os.path.exists(MOCK_TEMP_PATH): os.remove(MOCK_TEMP_PATH)
    if os.path.exists(MOCK_TARGET_PATH): os.remove(MOCK_TARGET_PATH)

    # Create Target
    target = Collection(name="test_target", cards=[])
    with open(MOCK_TARGET_PATH, 'w') as f:
        f.write(target.model_dump_json(indent=4))

def get_mock_card(id=12345):
    return ApiCard(
        id=id,
        name="Test Card",
        type="Monster",
        frameType="normal",
        desc="Test Desc",
        card_images=[ApiCardImage(id=id, image_url="url", image_url_small="url_small")],
        card_sets=[
            ApiCardSet(
                set_name="Test Set",
                set_code="TEST-EN001",
                set_rarity="Common",
                set_price="1.00",
                image_id=id
            )
        ]
    )

async def test_backend_logic():
    print(">>> Starting Backend Verification")
    setup()

    # 1. Test Adding to Temp Collection (Scan)
    print("[1] Testing Scan Addition...")
    temp_col = Collection(name="scans_temp", cards=[])
    card = get_mock_card()

    CollectionEditor.apply_change(
        collection=temp_col,
        api_card=card,
        set_code="TEST-EN001",
        rarity="Common",
        language="EN",
        quantity=1,
        condition="Near Mint",
        first_edition=False,
        mode='ADD'
    )

    assert len(temp_col.cards) == 1
    assert temp_col.cards[0].variants[0].entries[0].quantity == 1
    print("    -> Added successfully.")

    # 2. Test Undo Logic (via Changelog)
    print("[2] Testing Undo Logic...")
    # Simulate logging the add
    changelog_manager.log_change("test_scans_temp", "ADD", {
        'card_id': card.id,
        'set_code': "TEST-EN001",
        'rarity': "Common",
        'language': "EN",
        'condition': "Near Mint",
        'first_edition': False,
        'image_id': card.id
    }, 1)

    # Perform Undo
    last = changelog_manager.undo_last_change("test_scans_temp")
    assert last is not None
    assert last['action'] == 'ADD'

    # Revert in model
    CollectionEditor.apply_change(
        collection=temp_col,
        api_card=card,
        set_code="TEST-EN001",
        rarity="Common",
        language="EN",
        quantity=-1,
        condition="Near Mint",
        first_edition=False,
        mode='ADD'
    )

    assert len(temp_col.cards) == 0
    print("    -> Undo successful.")

    # 3. Test Commit Logic
    print("[3] Testing Commit Logic...")
    # Add card back
    CollectionEditor.apply_change(temp_col, card, "TEST-EN001", "Common", "EN", 1, "Near Mint", False, mode='ADD')

    # Load Target
    target_col = persistence.load_collection("test_target.json")

    # Commit: Add to Target, Remove from Temp
    CollectionEditor.apply_change(target_col, card, "TEST-EN001", "Common", "EN", 1, "Near Mint", False, mode='ADD')
    temp_col.cards = [] # Clear temp

    persistence.save_collection(target_col, "test_target.json")

    assert len(target_col.cards) == 1
    assert len(temp_col.cards) == 0
    print("    -> Commit successful.")

    print(">>> Verification Complete!")

if __name__ == "__main__":
    asyncio.run(test_backend_logic())
