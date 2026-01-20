import sys
from unittest.mock import MagicMock

# Mock nicegui before imports
sys.modules['nicegui'] = MagicMock()
sys.modules['nicegui.ui'] = MagicMock()
sys.modules['nicegui.events'] = MagicMock()

import pytest
from src.services.cardmarket_parser import CardmarketParser, ParsedRow
from src.ui.import_tools import UnifiedImportController
from unittest.mock import patch, AsyncMock
from src.core.models import ApiCard, ApiCardSet

def test_rarity_mapping():
    # Verify QSCR maps to Quarter Century Secret Rare
    assert CardmarketParser.RARITY_MAP['QSCR'] == 'Quarter Century Secret Rare'

def test_parser_qscr():
    # Test parsing a line with QSCR
    # Format: Qty Name Number Lang Condition SetPrefix Rarity [First Edition] Price Currency
    line = "1 Constellar Pleiades 164 EN NM RA04 QSCR First Edition 3,40 EUR"
    rows = CardmarketParser.parse_text(line)
    assert len(rows) == 1
    row = rows[0]
    assert row.name == "Constellar Pleiades"
    assert row.number == "164"
    assert row.rarity_abbr == "QSCR"
    assert row.set_rarity == "Quarter Century Secret Rare"

@pytest.mark.asyncio
async def test_import_process_logic():
    # Test the UnifiedImportController.process_cardmarket logic mock
    with patch('src.ui.import_tools.ygo_service') as mock_ygo:
        controller = UnifiedImportController()

        # Mock DB Data
        mock_card = ApiCard(
            id=999,
            name="Test Card",
            type="Monster",
            frameType="effect",
            desc="Desc",
            race="Warrior",
            attribute="EARTH",
            card_sets=[
                ApiCardSet(set_name="Set", set_code="LOB-EN001", set_rarity="Common", set_price="1.0", variant_id="v1"),
                ApiCardSet(set_name="Set", set_code="LOB-E001", set_rarity="Common", set_price="1.0", variant_id="v2")
            ],
            card_images=[]
        )
        mock_ygo.load_card_database = AsyncMock(return_value=[mock_card])

        # 1. Exact Match Test
        # Import LOB-EN001 (Standard) -> Should find LOB-EN001
        content = b"1 Test Card 001 EN NM LOB C 1,00 EUR"
        await controller.process_cardmarket(content, "test.txt")
        assert len(controller.pending_changes) == 1
        assert controller.pending_changes[0].set_code == "LOB-EN001"

        # Reset
        controller.pending_changes = []

        # 2. Auto-Match Test (Import LOB-DE001, DB has LOB-EN001/LOB-E001 which normalize to LOB-001)
        # Wait, LOB-EN001 and LOB-E001 normalize to LOB-001.
        # If both exist in DB, `db_lookup['LOB-001']` has 2 entries.
        # So "Ambiguity" should trigger.

        content = b"1 Test Card 001 DE NM LOB C 1,00 EUR"
        # Row Target: LOB-DE001.
        # Exact Match: LOB-DE001 (No), LOB-G001 (No), LOB-001 (No).
        # Base Match: LOB-001.
        # Matches: LOB-EN001, LOB-E001.
        # Result: Ambiguity.

        await controller.process_cardmarket(content, "test.txt")
        assert len(controller.ambiguous_rows) == 1
        assert controller.ambiguous_rows[0]['target_code'] == "LOB-DE001"
        assert len(controller.ambiguous_rows[0]['matches']) == 2

        # Reset
        controller.pending_changes = []
        controller.ambiguous_rows = []

        # 3. Auto-Match Unique
        # Mock only LOB-EN001
        mock_card_unique = ApiCard(
            id=888,
            name="Unique Card",
            type="Monster",
            frameType="effect",
            desc="Desc",
            race="Warrior",
            attribute="EARTH",
            card_sets=[
                ApiCardSet(set_name="Set", set_code="EEN-EN008", set_rarity="Common", set_price="1.0")
            ],
            card_images=[]
        )
        mock_ygo.load_card_database = AsyncMock(return_value=[mock_card_unique])

        # Import EEN-DE008
        content = b"1 Unique Card 008 DE NM EEN C 1,00 EUR"
        await controller.process_cardmarket(content, "test.txt")

        assert len(controller.pending_changes) == 1
        assert controller.pending_changes[0].set_code == "EEN-DE008" # Auto-matched target
        assert controller.pending_changes[0].api_card.id == 888
