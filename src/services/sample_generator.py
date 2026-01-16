import logging
import random
import os
from typing import List, Optional
from src.services.ygo_api import ygo_service
from src.core.persistence import persistence, COLLECTIONS_DIR
from src.core.models import Collection, CollectionCard, CollectionVariant, CollectionEntry
from src.core.utils import generate_variant_id, transform_set_code

logger = logging.getLogger(__name__)

LANGUAGES = ["EN", "DE", "FR", "IT", "JP"]
CONDITIONS = ["Mint", "Near Mint", "Played", "Damaged"]

async def generate_sample_collection(base_filename: str = "sample_collection") -> str:
    """
    Generates a sample collection with diverse cards and saves it.
    Returns the filename of the saved collection.
    """
    logger.info("Initializing sample generation...")

    # 1. Ensure DB is loaded
    logger.info("Loading Card Database for generation...")
    # Ensure we have data loaded. 'en' is default.
    cards = await ygo_service.load_card_database("en")

    if not cards:
        # Try fetching if empty (though load_card_database usually handles it if cache exists)
        logger.info("Database empty, attempting fetch...")
        await ygo_service.fetch_card_database("en")
        cards = await ygo_service.load_card_database("en")

    if not cards:
        raise ValueError("Could not load card database. Cannot generate sample.")

    # 2. Select Candidates
    # Filter for cards that have at least one set
    valid_cards = [c for c in cards if c.card_sets and len(c.card_sets) > 0]

    # We want around 100 cards.
    # We want some "rich" cards (multiple arts/sets) and some "simple" cards.

    rich_cards = [c for c in valid_cards if (c.card_images and len(c.card_images) > 1) or len(c.card_sets) > 5]
    simple_cards = [c for c in valid_cards if c not in rich_cards]

    selected_cards = []

    # Let's say 30% rich cards, 70% simple cards for the mix
    target_count = 100
    num_rich = 30
    num_simple = 70

    if len(rich_cards) < num_rich:
        selected_cards.extend(rich_cards)
        num_simple += (num_rich - len(rich_cards))
    else:
        selected_cards.extend(random.sample(rich_cards, num_rich))

    if len(simple_cards) < num_simple:
        selected_cards.extend(simple_cards)
    else:
        selected_cards.extend(random.sample(simple_cards, num_simple))

    logger.info(f"Selected {len(selected_cards)} cards for generation.")

    collection_cards = []
    total_variants_count = 0

    # We want ~250 variants total.
    # Current cards ~100. So average 2.5 variants per card.
    # We will vary this heavily. Rich cards get many, simple cards get 1.

    for card in selected_cards:
        variants = []

        is_rich = card in rich_cards

        if is_rich:
            # Generate 3 to 8 variants
            num_variants = random.randint(3, 8)
        else:
            # Generate 1 to 2 variants
            num_variants = random.randint(1, 2)

        available_sets = card.card_sets

        # Avoid infinite loop if requested variants > available combinations (approx)
        # We can reuse sets with different languages though.

        attempts = 0
        while len(variants) < num_variants and attempts < 20:
            attempts += 1

            # Pick a base set configuration
            base_set = random.choice(available_sets)

            # Randomize metadata
            lang = random.choice(LANGUAGES)

            # Transform set code based on language
            final_set_code = transform_set_code(base_set.set_code, lang)

            # Determine image ID
            image_id = base_set.image_id
            if image_id is None and card.card_images:
                # Randomize image if multiple available for this card?
                # Ideally set matches image, but for sample diversity let's sometimes random if not strict
                if len(card.card_images) > 1 and random.random() > 0.5:
                     image_id = random.choice(card.card_images).id
                else:
                    image_id = card.card_images[0].id

            var_id = generate_variant_id(card.id, final_set_code, base_set.set_rarity, image_id)

            # Check if we already added this variant
            if any(v.variant_id == var_id for v in variants):
                continue

            # Create Entries
            entries = []

            # Some variants have multiple entries (different conditions/editions)
            num_entries = 1
            if random.random() < 0.3: # 30% chance of multiple entries
                num_entries = random.randint(2, 3)

            for _ in range(num_entries):
                cond = random.choice(CONDITIONS)
                first_ed = random.choice([True, False])
                qty = random.randint(1, 3)

                entries.append(CollectionEntry(
                    condition=cond,
                    language=lang, # Usually matches the set transformation language
                    first_edition=first_ed,
                    quantity=qty,
                    storage_location=f"Box {random.choice(['A', 'B', 'C', 'D'])}",
                    purchase_price=round(random.uniform(1.0, 100.0), 2),
                    market_value=round(random.uniform(1.0, 100.0), 2)
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
        description=f"A generated sample collection with {len(collection_cards)} cards and {total_variants_count} variants.",
        cards=collection_cards
    )

    # 4. Determine Filename
    # persistence.list_collections returns list of filenames
    existing_files = persistence.list_collections()

    # Clean base filename (remove extension if provided, though we expect just name part usually)
    if base_filename.endswith(".json"):
        base_name = base_filename[:-5]
    else:
        base_name = base_filename

    final_filename = f"{base_name}.json"
    counter = 1

    while final_filename in existing_files:
        final_filename = f"{base_name}({counter}).json"
        counter += 1

    # 5. Save
    persistence.save_collection(collection, final_filename)
    logger.info(f"Saved sample collection to {final_filename}")

    return final_filename
