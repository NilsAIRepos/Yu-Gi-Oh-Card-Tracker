import asyncio
import logging
import random
import sys
import os
from typing import List, Optional

# Add root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.services.ygo_api import ygo_service
from src.core.persistence import persistence
from src.core.models import Collection, CollectionCard, CollectionVariant, CollectionEntry, ApiCard
from src.core.utils import generate_variant_id, transform_set_code

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

LANGUAGES = ["EN", "DE", "FR", "IT", "JP"]
CONDITIONS = ["Mint", "Near Mint", "Played", "Damaged"]

async def main():
    logger.info("Initializing generation...")

    # 1. Ensure DB is loaded
    logger.info("Loading Card Database...")
    cards = await ygo_service.load_card_database("en")
    logger.info(f"Loaded {len(cards)} cards from database.")

    if len(cards) == 0:
        logger.error("No cards found. Exiting.")
        return

    # 2. Select Candidates
    # Filter for cards that have at least one set
    valid_cards = [c for c in cards if c.card_sets and len(c.card_sets) > 0]

    # Prioritize cards with multiple images or many sets for diversity
    rich_cards = [c for c in valid_cards if (c.card_images and len(c.card_images) > 1) or len(c.card_sets) > 5]

    # If we have enough rich cards, pick from them, otherwise mix
    candidates = []
    if len(rich_cards) >= 50:
        candidates = random.sample(rich_cards, 50)
    else:
        candidates = rich_cards + random.sample([c for c in valid_cards if c not in rich_cards], 50 - len(rich_cards))

    logger.info(f"Selected {len(candidates)} cards for collection.")

    collection_cards = []
    total_variants_count = 0

    for card in candidates:
        variants = []
        # Target roughly 4 variants per card
        num_variants = random.randint(3, 5)

        # Available sets from API
        available_sets = card.card_sets

        for _ in range(num_variants):
            # Pick a base set configuration
            base_set = random.choice(available_sets)

            # Randomize metadata
            lang = random.choice(LANGUAGES)

            # Transform set code based on language
            final_set_code = transform_set_code(base_set.set_code, lang)

            # Determine image ID
            # If the base set has a specific image_id, try to use it.
            # Otherwise use default (first image of card)
            image_id = base_set.image_id
            if image_id is None and card.card_images:
                image_id = card.card_images[0].id

            # Generate Variant ID
            # Note: We must ensure we generate the variant ID based on the *transformed* set code
            # if we want them to be distinct per language/code combination?
            # Actually, standard logic uses the set code. If we change the set code (LOB-DE001),
            # the variant ID should change too to distinguish it.
            var_id = generate_variant_id(card.id, final_set_code, base_set.set_rarity, image_id)

            # Check if we already added this variant to this card
            if any(v.variant_id == var_id for v in variants):
                continue

            # Create Entries
            entries = []
            qty = random.randint(1, 3)
            # Split quantity across conditions sometimes?
            # For simplicity, one entry per variant mostly, but let's sometimes add a second entry

            cond = random.choice(CONDITIONS)
            first_ed = random.choice([True, False])

            entries.append(CollectionEntry(
                condition=cond,
                language=lang,
                first_edition=first_ed,
                quantity=qty,
                storage_location=f"Box {random.choice(['A', 'B', 'C'])}",
                purchase_price=round(random.uniform(1.0, 50.0), 2),
                market_value=round(random.uniform(1.0, 50.0), 2)
            ))

            # Occasionally add a second entry for the same variant (different condition)
            if random.random() < 0.2:
                 entries.append(CollectionEntry(
                    condition=random.choice([c for c in CONDITIONS if c != cond]),
                    language=lang, # Keep language same for same variant usually?
                    # Actually, if language is different, set code changes, so variant changes.
                    # So language must be consistent for the variant ID unless we group variants differently.
                    # In this model, Variant is tied to Set Code. So Language is tied to Set Code.
                    # Wait, CollectionEntry has a `language` field too.
                    # Does Variant ID encode language? No, `generate_variant_id` uses `set_code`.
                    # If `set_code` is transformed (LOB-DE...), it implies language.
                    # If `set_code` is static (SDY-006), it does NOT imply language.
                    # So for Old sets, we can have multiple languages under same Variant ID?
                    # Let's check `generate_variant_id` inputs: card_id, set_code, rarity, image_id.
                    # So if set_code is same, Variant ID is same.
                    # For Old sets, SDY-006 is same for EN and DE. So same Variant ID.
                    # So yes, we can have mixed languages in entries for Old sets.
                    # For New sets, set code differs, so different Variant IDs.
                    first_edition=first_ed,
                    quantity=1
                ))

            variants.append(CollectionVariant(
                variant_id=var_id,
                set_code=final_set_code,
                rarity=base_set.set_rarity,
                image_id=image_id,
                entries=entries
            ))

        if variants:
            collection_cards.append(CollectionCard(
                card_id=card.id,
                name=card.name,
                variants=variants
            ))
            total_variants_count += len(variants)

    # 3. Construct Collection
    collection = Collection(
        name="Sample Collection",
        description="A generated sample collection with diverse cards.",
        cards=collection_cards
    )

    # 4. Save
    output_filename = "sample_collection.json"
    persistence.save_collection(collection, output_filename)
    logger.info(f"Saved collection to {output_filename}")

    # Summary
    logger.info(f"Total Cards: {len(collection_cards)}")
    logger.info(f"Total Variants: {total_variants_count}")

    # 5. Verify Load (Sanity Check)
    loaded = persistence.load_collection(output_filename)
    logger.info(f"Verification Load: Success. Loaded {len(loaded.cards)} cards.")

if __name__ == "__main__":
    asyncio.run(main())
