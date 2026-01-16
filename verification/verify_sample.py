import sys
import os
import logging
from src.core.persistence import persistence

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def verify():
    filename = "sample_collection.json"
    logger.info(f"Verifying {filename}...")

    try:
        col = persistence.load_collection(filename)
    except Exception as e:
        logger.error(f"Failed to load collection: {e}")
        sys.exit(1)

    logger.info(f"Collection Name: {col.name}")
    logger.info(f"Card Count: {len(col.cards)}")

    total_variants = sum(len(c.variants) for c in col.cards)
    total_entries = sum(len(v.entries) for c in col.cards for v in c.variants)

    logger.info(f"Total Variants: {total_variants}")
    logger.info(f"Total Entries: {total_entries}")

    if len(col.cards) < 40:
        logger.error("Too few cards!")
        sys.exit(1)

    if total_variants < 100:
        logger.error("Too few variants!")
        sys.exit(1)

    logger.info("Verification Passed!")

if __name__ == "__main__":
    verify()
