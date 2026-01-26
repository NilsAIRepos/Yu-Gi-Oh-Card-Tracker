from src.core.models import Collection, CollectionCard, CollectionVariant, CollectionEntry, ApiCard
from src.services.collection_editor import CollectionEditor

# Mock Data
collection = Collection(name="Test", cards=[])
card = ApiCard(id=1, name="Test Card", type="Monster", frameType="normal", desc="Test")
collection_card = CollectionCard(card_id=1, name="Test Card")
collection.cards.append(collection_card)

variant = CollectionVariant(variant_id="v1", set_code="TEST-EN001", rarity="Common")
collection_card.variants.append(variant)

# Entry 1: Box A
entry1 = CollectionEntry(storage_location="Box A", quantity=1)
variant.entries.append(entry1)

# Entry 2: Box B
entry2 = CollectionEntry(storage_location="Box B", quantity=2)
variant.entries.append(entry2)

# Test get_quantity (Specific)
qty_a = CollectionEditor.get_quantity(collection, 1, variant_id="v1", storage_location="Box A")
print(f"Quantity Box A: {qty_a} (Expected: 1)")

qty_b = CollectionEditor.get_quantity(collection, 1, variant_id="v1", storage_location="Box B")
print(f"Quantity Box B: {qty_b} (Expected: 2)")

qty_none = CollectionEditor.get_quantity(collection, 1, variant_id="v1", storage_location=None)
print(f"Quantity None: {qty_none} (Expected: 0)")

# Test get_total_quantity (Aggregated)
total_qty = CollectionEditor.get_total_quantity(collection, 1, variant_id="v1")
print(f"Total Quantity: {total_qty} (Expected: 3)")

assert qty_a == 1
assert qty_b == 2
assert qty_none == 0
assert total_qty == 3
print("All assertions passed.")
