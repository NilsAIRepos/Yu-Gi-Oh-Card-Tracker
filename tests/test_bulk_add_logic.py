import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from src.ui.bulk_add import BulkAddPage, LibraryEntry, BulkCollectionEntry
from src.core.models import ApiCard

@pytest.fixture
def mock_persistence():
    with patch('src.ui.bulk_add.persistence') as p:
        p.list_collections.return_value = ['test_collection.json']
        p.load_collection = AsyncMock()
        p.save_collection = MagicMock()
        yield p

@pytest.fixture
def mock_changelog():
    with patch('src.ui.bulk_add.changelog_manager') as c:
        yield c

@pytest.fixture
def mock_config():
    with patch('src.ui.bulk_add.config_manager') as c:
        c.get_language.return_value = 'en'
        yield c

@pytest.fixture
def mock_ygo():
    with patch('src.ui.bulk_add.ygo_service') as y:
        yield y

@pytest.fixture
def mock_ui():
    with patch('src.ui.bulk_add.ui') as u:
        yield u

@pytest.mark.asyncio
async def test_bulk_add_initialization(mock_persistence, mock_config, mock_ygo):
    page = BulkAddPage()
    assert page.state['selected_collection'] == 'test_collection.json'
    assert page.state['default_language'] == 'en'

@pytest.mark.asyncio
async def test_add_card_to_collection(mock_persistence, mock_changelog, mock_config, mock_ygo, mock_ui):
    page = BulkAddPage()
    page.current_collection_obj = MagicMock()
    page.current_collection_obj.cards = []

    # Mock render_header refresh
    page.render_header = MagicMock()
    page.render_header.refresh = MagicMock()

    # Mock load_collection_data to avoid async complexity or just mock it out
    page.load_collection_data = AsyncMock()

    api_card = ApiCard(id=1, name="Test Card", type="Monster", desc="Desc", frameType="normal")
    entry = LibraryEntry(
        id="1_SET_Common",
        api_card=api_card,
        set_code="SET-EN001",
        set_name="Test Set",
        rarity="Common",
        image_url="url",
        image_id=1
    )

    with patch('src.ui.bulk_add.CollectionEditor.apply_change', return_value=True) as mock_apply:
        success = await page.add_card_to_collection(entry, 'EN', 'Near Mint', False, 1)

        assert success is True
        mock_apply.assert_called_once()
        mock_changelog.log_change.assert_called_once()
        args, _ = mock_changelog.log_change.call_args
        assert args[1] == 'ADD' # action
        assert args[2]['card_id'] == 1
        assert args[3] == 1 # qty

@pytest.mark.asyncio
async def test_undo_last_action(mock_persistence, mock_changelog, mock_config, mock_ygo, mock_ui):
    page = BulkAddPage()
    page.api_card_map = {1: ApiCard(id=1, name="Test Card", type="Monster", desc="Desc", frameType="normal")}
    page.current_collection_obj = MagicMock()
    page.render_header = MagicMock()
    page.load_collection_data = AsyncMock()

    # Mock changelog returning an ADD action
    mock_changelog.undo_last_change.return_value = {
        'action': 'ADD',
        'quantity': 2,
        'card_data': {
            'card_id': 1,
            'set_code': 'SET',
            'rarity': 'Common',
            'language': 'EN',
            'condition': 'NM',
            'first_edition': False,
            'image_id': 1,
            'name': 'Test Card'
        }
    }

    with patch('src.ui.bulk_add.CollectionEditor.apply_change', return_value=True) as mock_apply:
        await page.undo_last_action()

        # Should call apply_change with negative quantity to revert ADD
        mock_apply.assert_called_once()
        call_args = mock_apply.call_args
        assert call_args.kwargs['quantity'] == -2
        assert call_args.kwargs['mode'] == 'ADD'
