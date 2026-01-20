import sys
from unittest.mock import MagicMock

# Mock nicegui before imports to avoid runtime dependency
sys.modules['nicegui'] = MagicMock()
sys.modules['nicegui.ui'] = MagicMock()
sys.modules['nicegui.events'] = MagicMock()

import pytest
from unittest.mock import patch, AsyncMock
import json
from src.ui.import_tools import UnifiedImportController, PendingChange
from src.core.models import ApiCard, ApiCardSet, ApiCardImage

@pytest.fixture
def mock_dependencies():
    with patch('src.ui.import_tools.persistence') as mock_persistence, \
         patch('src.ui.import_tools.ygo_service') as mock_ygo, \
         patch('src.ui.import_tools.ui') as mock_ui:

        mock_persistence.list_collections.return_value = ["test_col.json"]

        # Mock ApiCard
        mock_card = ApiCard(
            id=123,
            name="Test Card",
            type="Monster",
            frameType="effect",
            desc="Desc",
            race="Dragon",
            attribute="LIGHT",
            card_sets=[
                ApiCardSet(set_name="Set", set_code="SET-EN001", set_rarity="Common", set_price="1.0")
            ],
            card_images=[
                ApiCardImage(id=123, image_url="url", image_url_small="url_small")
            ]
        )
        mock_ygo.get_card.return_value = mock_card
        mock_ygo.load_card_database = AsyncMock(return_value=[mock_card])

        yield mock_persistence, mock_ygo, mock_ui

@pytest.mark.asyncio
async def test_process_json_logic(mock_dependencies):
    mock_persistence, mock_ygo, mock_ui = mock_dependencies

    controller = UnifiedImportController()

    # Valid JSON content
    data = {
        "cards": [
            {
                "card_id": 123,
                "variants": [
                    {
                        "set_code": "SET-EN001",
                        "rarity": "Common",
                        "entries": [
                            {"quantity": 3, "condition": "Near Mint", "language": "EN"}
                        ]
                    }
                ]
            }
        ]
    }
    content = json.dumps(data).encode('utf-8')

    await controller.process_json(content)

    assert len(controller.pending_changes) == 1
    item = controller.pending_changes[0]
    assert item.api_card.id == 123
    assert item.quantity == 3
    assert item.set_code == "SET-EN001"

    # Verify UI notification
    mock_ui.notify.assert_called_with("Parsed 1 entries from JSON.", type='positive')

@pytest.mark.asyncio
async def test_handle_upload_robustness(mock_dependencies):
    mock_persistence, mock_ygo, mock_ui = mock_dependencies
    controller = UnifiedImportController()

    # Mock Event with 'file' (New NiceGUI)
    mock_event_new = MagicMock()
    del mock_event_new.content # Ensure it doesn't have content
    mock_event_new.file.read = AsyncMock(return_value=b'{"cards": []}')
    mock_event_new.file.name = "test.json"

    await controller.handle_upload(mock_event_new)
    # Should not crash and call notify
    assert mock_ui.notify.call_count >= 2 # "Processing..." + "No valid entries..."

    # Mock Event with 'content' (Old NiceGUI)
    mock_event_old = MagicMock()
    del mock_event_old.file # Ensure it doesn't have file
    mock_event_old.content.read.return_value = b'{"cards": []}'
    mock_event_old.name = "test.json"

    await controller.handle_upload(mock_event_old)
    # Should work too
