import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from src.services.ygo_api import YugiohService, ApiCard, ApiCardSet

@pytest.fixture
def ygo_service():
    service = YugiohService()
    # Mock load_card_database to return a fake card
    card = ApiCard(id=123, name="Test Card", type="Monster", frameType="normal", desc="Desc")
    card.card_sets = [
        ApiCardSet(set_code="LOB-EN001", set_name="Legend of Blue Eyes", set_rarity="Ultra Rare", set_rarity_code="(UR)", set_price="1.00", variant_id="v1", image_id=123)
    ]

    mock_load = AsyncMock(return_value=[card])
    service.load_card_database = mock_load

    # Mock save_card_database
    service.save_card_database = AsyncMock()

    # Mock get_set_name_by_code
    service.get_set_name_by_code = AsyncMock(return_value="Resolved Set Name")

    return service

@pytest.mark.asyncio
async def test_ensure_card_variant_existing(ygo_service):
    # Test ensuring an existing variant
    result = await ygo_service.ensure_card_variant(123, "LOB-EN001", "Ultra Rare", image_id=123)
    assert result is False # No new variant added
    assert ygo_service.save_card_database.call_count == 0

@pytest.mark.asyncio
async def test_ensure_card_variant_new(ygo_service):
    # Test ensuring a NEW variant
    result = await ygo_service.ensure_card_variant(123, "LOB-DE001", "Ultra Rare", image_id=123, language="de")
    assert result is True # New variant added

    # Verify save called
    assert ygo_service.save_card_database.call_count == 1

    # Verify card was modified in memory (mock returns same object reference effectively if we access it via load)
    # But since load_card_database returns a fresh list in the mock usually, we need to capture the object used.
    # In my fixture, I return `[card]`. `load_card_database` is called. The service modifies `card`.
    # Let's inspect the args passed to save_card_database

    args, _ = ygo_service.save_card_database.call_args
    saved_cards = args[0]
    assert len(saved_cards) == 1
    card = saved_cards[0]
    assert len(card.card_sets) == 2

    new_set = card.card_sets[1]
    assert new_set.set_code == "LOB-DE001"
    assert new_set.set_rarity == "Ultra Rare"
    assert new_set.set_name == "Resolved Set Name"

@pytest.mark.asyncio
async def test_ensure_card_variants_batch(ygo_service):
    variants = [
        {'card_id': 123, 'set_code': 'LOB-EN001', 'set_rarity': 'Ultra Rare', 'image_id': 123}, # Exists
        {'card_id': 123, 'set_code': 'LOB-FR001', 'set_rarity': 'Ultra Rare', 'image_id': 123}, # New
        {'card_id': 123, 'set_code': 'LOB-IT001', 'set_rarity': 'Ultra Rare', 'image_id': 123}  # New
    ]

    count = await ygo_service.ensure_card_variants(variants, language="en")
    assert count == 2

    assert ygo_service.save_card_database.call_count == 1

    args, _ = ygo_service.save_card_database.call_args
    saved_cards = args[0]
    card = saved_cards[0]
    assert len(card.card_sets) == 3 # 1 existing + 2 new
