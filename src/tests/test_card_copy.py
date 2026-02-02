import pytest
from unittest.mock import MagicMock, AsyncMock, patch
import sys

# Mock nicegui.run since it's used in ygo_api
with patch.dict(sys.modules, {'nicegui': MagicMock(), 'nicegui.run': MagicMock()}):
    from src.services.ygo_api import YugiohService
    from src.core.models import ApiCard, ApiCardSet

@pytest.mark.asyncio
async def test_copy_card_to_new_id():
    service = YugiohService()

    # Mock data
    original_id = 123
    new_id = 456

    variant_id = "v1"

    card = ApiCard(
        id=original_id,
        name="Test Card",
        type="Monster",
        frameType="normal",
        desc="Description",
        card_sets=[
            ApiCardSet(
                variant_id=variant_id,
                set_name="Test Set",
                set_code="TEST-EN001",
                set_rarity="Common",
                image_id=original_id
            )
        ],
        card_images=[]
    )

    # Test 1: Collision
    # Existing card with new_id
    existing_card = ApiCard(id=new_id, name="Collision", type="x", frameType="x", desc="x")
    service.load_card_database = AsyncMock(return_value=[card, existing_card])
    service.save_card_database = AsyncMock()

    result = await service.copy_card_to_new_id(original_id, new_id)
    assert result is False

    # Test 2: Success
    mock_db = [card] # Mutable list to simulate DB
    service.load_card_database = AsyncMock(return_value=mock_db)
    service.save_card_database = AsyncMock()

    result = await service.copy_card_to_new_id(original_id, new_id)

    assert result is True

    # Verify both cards exist
    assert len(mock_db) == 2
    assert mock_db[0].id == original_id

    new_card = mock_db[1]
    assert new_card.id == new_id
    assert new_card.name == "Test Card"

    # Check that variant ID has been updated for new card
    assert new_card.card_sets[0].variant_id != variant_id
    # Ensure original is untouched
    assert mock_db[0].card_sets[0].variant_id == variant_id

    service.save_card_database.assert_called_once()
