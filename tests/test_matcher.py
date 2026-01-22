import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from src.services.scanner.matcher import CardMatcher
from src.core.models import ApiCard, ApiCardSet

@pytest.mark.asyncio
async def test_matcher_set_code_correction():
    # Mock service
    mock_service = MagicMock()

    # Mock Cards
    card1 = ApiCard(
        id=1,
        name="Blue-Eyes White Dragon",
        type="Normal Monster",
        frameType="normal",
        desc="Desc",
        card_sets=[
            ApiCardSet(variant_id="v1", set_code="LOB-001", set_rarity="Ultra Rare", set_name="Legend of Blue Eyes")
        ]
    )

    # Setup load_card_database to return list
    mock_service.load_card_database = AsyncMock(return_value=[card1])
    mock_service.get_image_path = AsyncMock(return_value="img.jpg")

    matcher = CardMatcher(mock_service)

    # Test Input with Error: LOB-OO1 (Letter O instead of Digit 0)
    ocr_dump = {
        "t1_full": {"set_id": "LOB-OO1", "set_id_conf": 90.0, "raw_text": "Blue-Eyes White Dragon | ...", "language": "EN"},
        "t1_crop": {"set_id": "LOB-OO1", "set_id_conf": 95.0, "raw_text": "Blue-Eyes White Dragon | ...", "language": "EN"}
    }

    result = await matcher.match_card(ocr_dump, "en")

    assert result.best_match is not None
    assert result.best_match.set_code == "LOB-001"
    assert "Set Code Match" in result.best_match.reason
    assert result.best_match.confidence > 80

@pytest.mark.asyncio
async def test_matcher_fuzzy_name():
    mock_service = MagicMock()
    card1 = ApiCard(
        id=2,
        name="Dark Magician",
        type="Normal Monster",
        frameType="normal",
        desc="Desc",
        card_sets=[
            ApiCardSet(variant_id="v2", set_code="SDY-006", set_rarity="Ultra Rare", set_name="Starter Deck Yugi")
        ]
    )
    mock_service.load_card_database = AsyncMock(return_value=[card1])
    mock_service.get_image_path = AsyncMock(return_value="img.jpg")

    matcher = CardMatcher(mock_service)

    # Input: No Set Code, but Name is "Dark Magiclan" (Typo)
    ocr_dump = {
        "t1_full": {"set_id": None, "raw_text": "Dark Magiclan | Spellcaster", "language": "EN"},
    }

    result = await matcher.match_card(ocr_dump, "en")

    assert result.best_match is not None
    assert result.best_match.name == "Dark Magician"
    assert "Fuzzy Name Match" in result.best_match.reason
