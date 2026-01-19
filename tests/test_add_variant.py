import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from src.services.ygo_api import YugiohService
from src.core.models import ApiCard, ApiCardSet, ApiCardImage
from src.core.constants import RARITY_ABBREVIATIONS

@pytest.fixture
def mock_ygo_service():
    service = YugiohService()
    service.load_card_database = AsyncMock()
    service.save_card_database = AsyncMock()
    service.get_set_info = AsyncMock()
    return service

@pytest.mark.asyncio
async def test_add_card_variant_success_auto_code(mock_ygo_service):
    # Setup
    card_id = 123
    card = ApiCard(
        id=card_id, name="Test Card", type="Monster", frameType="normal", desc="desc",
        card_sets=[]
    )
    mock_ygo_service.load_card_database.return_value = [card]

    # Execute
    new_set = await mock_ygo_service.add_card_variant(
        card_id=card_id,
        set_name="Test Set",
        set_code="TEST-EN001",
        set_rarity="Ultra Rare", # Should map to (UR)
        language="en"
    )

    # Verify
    assert new_set is not None
    assert new_set.set_code == "TEST-EN001"
    assert new_set.set_rarity == "Ultra Rare"
    assert new_set.set_rarity_code == "(UR)" # Auto-generated
    assert len(card.card_sets) == 1
    mock_ygo_service.save_card_database.assert_called_once()

@pytest.mark.asyncio
async def test_add_card_variant_duplicate(mock_ygo_service):
    # Setup
    card_id = 123
    existing_set = ApiCardSet(
        set_name="Test Set", set_code="TEST-EN001", set_rarity="Ultra Rare",
        set_rarity_code="(UR)", variant_id="existing_id"
    )
    card = ApiCard(
        id=card_id, name="Test Card", type="Monster", frameType="normal", desc="desc",
        card_sets=[existing_set]
    )
    mock_ygo_service.load_card_database.return_value = [card]

    # Execute - Attempt to add same code/rarity
    result = await mock_ygo_service.add_card_variant(
        card_id=card_id,
        set_name="Test Set",
        set_code="TEST-EN001",
        set_rarity="Ultra Rare",
        language="en"
    )

    # Verify
    assert result is None # Should return None for duplicate
    assert len(card.card_sets) == 1 # No new addition
    mock_ygo_service.save_card_database.assert_not_called()

@pytest.mark.asyncio
async def test_add_card_variant_explicit_code(mock_ygo_service):
    # Setup
    card_id = 123
    card = ApiCard(
        id=card_id, name="Test Card", type="Monster", frameType="normal", desc="desc",
        card_sets=[]
    )
    mock_ygo_service.load_card_database.return_value = [card]

    # Execute
    new_set = await mock_ygo_service.add_card_variant(
        card_id=card_id,
        set_name="Test Set",
        set_code="TEST-EN002",
        set_rarity="Secret Rare",
        set_rarity_code="(CustomCode)", # Explicit
        language="en"
    )

    # Verify
    assert new_set is not None
    assert new_set.set_rarity_code == "(CustomCode)" # Should use explicit

@pytest.mark.asyncio
async def test_update_card_variant_creates_new_with_code(mock_ygo_service):
    # Setup
    card_id = 123
    card = ApiCard(
        id=card_id, name="Test Card", type="Monster", frameType="normal", desc="desc",
        card_sets=[]
    )
    mock_ygo_service.load_card_database.return_value = [card]
    mock_ygo_service.get_set_info.return_value = None

    # Execute - Update non-existent variant -> create new
    success = await mock_ygo_service.update_card_variant(
        card_id=card_id,
        variant_id="non_existent",
        set_code="TEST-EN003",
        set_rarity="Super Rare", # Should map to (SR)
        image_id=None
    )

    # Verify
    assert success is True
    assert len(card.card_sets) == 1
    new_set = card.card_sets[0]
    assert new_set.set_code == "TEST-EN003"
    assert new_set.set_rarity_code == "(SR)" # Check fallback generation
