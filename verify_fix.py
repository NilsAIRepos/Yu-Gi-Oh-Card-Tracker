import asyncio
from src.core.models import Collection, CollectionCard, CollectionVariant, CollectionEntry, ApiCard, ApiCardImage
from src.core.utils import generate_variant_id

# Simulate SingleCardView logic
def logic_open_collectors(card, image_id_in):
    # FIXED: Do not force ID if None. Return as is.
    return image_id_in

def logic_handle_update(card, set_code, rarity, image_id):
    return generate_variant_id(card.id, set_code, rarity, image_id)

async def save_card_change_simulated(col, api_card, quantity, variant_id_passed):
    target_card = None
    for c in col.cards:
        if c.card_id == api_card.id:
            target_card = c
            break

    if not target_card:
        return

    target_variant = None
    for v in target_card.variants:
        if v.variant_id == variant_id_passed:
            target_variant = v
            break

    if not target_variant:
        print(f"Target variant {variant_id_passed} NOT found in collection.")
        if quantity == 0:
            print("Qty is 0, doing nothing.")
            return

    print("Variant found, proceeding to update/remove...")
    print("SUCCESS: Fix works.")

async def test():
    # Setup Data
    card_id = 123
    set_code = "SET-001"
    rarity = "Common"

    # DB has image_id = None
    db_image_id = None

    # API has image
    api_img = ApiCardImage(id=999, image_url="http://x", image_url_small="http://x")
    api_card = ApiCard(id=card_id, name="Test", type="Monster", frameType="normal", desc="x", card_images=[api_img])

    # Generate DB Variant ID (as stored)
    db_variant_id = generate_variant_id(card_id, set_code, rarity, db_image_id)

    # Create Collection
    entry = CollectionEntry(quantity=1)
    variant = CollectionVariant(variant_id=db_variant_id, set_code=set_code, rarity=rarity, image_id=db_image_id, entries=[entry])
    card = CollectionCard(card_id=card_id, name="Test", variants=[variant])
    col = Collection(name="Test", cards=[card])

    print(f"DB Variant ID (img=None): {db_variant_id}")

    # Simulate User Flow
    # 1. User clicks row. Row has image_id=None.
    passed_image_id = None

    # 2. open_collectors logic (FIXED)
    ui_image_id = logic_open_collectors(api_card, passed_image_id)
    print(f"UI Image ID (after logic): {ui_image_id}")

    # 3. handle_update logic
    ui_variant_id = logic_handle_update(api_card, set_code, rarity, ui_image_id)
    print(f"UI Variant ID (generated): {ui_variant_id}")

    # 4. Save with Qty 0
    await save_card_change_simulated(col, api_card, 0, ui_variant_id)

if __name__ == "__main__":
    asyncio.run(test())
