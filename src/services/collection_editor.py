from src.core.models import Collection, CollectionCard, CollectionVariant, CollectionEntry, ApiCard
from src.core.utils import generate_variant_id
from typing import Optional

class CollectionEditor:
    @staticmethod
    def apply_change(
        collection: Collection,
        api_card: ApiCard,
        set_code: str,
        rarity: str,
        language: str,
        quantity: int,
        condition: str,
        first_edition: bool,
        image_id: Optional[int] = None,
        variant_id: Optional[str] = None,
        mode: str = 'SET'
    ) -> bool:
        """
        Applies a change (add, set, remove) to a collection.
        Returns True if the collection was modified, False otherwise.
        """
        modified = False

        # 1. Find or Create CollectionCard
        target_card = None
        for c in collection.cards:
            if c.card_id == api_card.id:
                target_card = c
                break

        if not target_card:
            # If removing/setting 0 and it doesn't exist, do nothing
            if quantity <= 0 and mode == 'SET':
                return False
            target_card = CollectionCard(card_id=api_card.id, name=api_card.name)
            collection.cards.append(target_card)
            modified = True

        # 2. Determine Variant ID
        target_variant_id = variant_id
        if not target_variant_id:
             target_variant_id = generate_variant_id(api_card.id, set_code, rarity, image_id)

        # 3. Find or Create CollectionVariant
        target_variant = None
        for v in target_card.variants:
            if v.variant_id == target_variant_id:
                target_variant = v
                break

        if not target_variant:
             # Need to add if quantity > 0
             should_add = False
             if mode == 'SET' and quantity > 0: should_add = True
             elif mode == 'ADD' and quantity > 0: should_add = True

             if should_add:
                 target_variant = CollectionVariant(
                     variant_id=target_variant_id,
                     set_code=set_code,
                     rarity=rarity,
                     image_id=image_id
                 )
                 target_card.variants.append(target_variant)
                 modified = True

        if target_variant:
            # 4. Find or Create CollectionEntry
            target_entry = None
            for e in target_variant.entries:
                if e.condition == condition and e.language == language and e.first_edition == first_edition:
                    target_entry = e
                    break

            # 5. Calculate New Quantity
            final_quantity = 0
            current_quantity = target_entry.quantity if target_entry else 0

            if mode == 'SET':
                final_quantity = quantity
            elif mode == 'ADD':
                final_quantity = current_quantity + quantity

            # 6. Apply Quantity Change
            if final_quantity > 0:
                if target_entry:
                    if target_entry.quantity != final_quantity:
                        target_entry.quantity = final_quantity
                        modified = True
                else:
                    target_variant.entries.append(CollectionEntry(
                        condition=condition,
                        language=language,
                        first_edition=first_edition,
                        quantity=final_quantity
                    ))
                    modified = True
            else:
                if target_entry:
                    target_variant.entries.remove(target_entry)
                    modified = True

            # 7. Cleanup Empty Variant
            if not target_variant.entries:
                target_card.variants.remove(target_variant)
                modified = True

        # 8. Cleanup Empty Card
        if not target_card.variants:
            if target_card in collection.cards:
                collection.cards.remove(target_card)
                modified = True

        return modified
