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
async def test_add_card_triggers_refresh(mock_persistence, mock_changelog, mock_config, mock_ygo, mock_ui):
    page = BulkAddPage()
    page.current_collection_obj = MagicMock()
    page.current_collection_obj.cards = []

    # Mock render_header refresh
    page.render_header = MagicMock()
    page.render_header.refresh = MagicMock()

    # Mock render_library_content refresh
    page.render_library_content = MagicMock()
    page.render_library_content.refresh = MagicMock()

    # Mock load_collection_data
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
        # Test direct call (context menu)
        await page.add_card_to_collection(entry, 'EN', 'Near Mint', False, 1)

        # Expect refresh NOT called
        page.render_library_content.refresh.assert_not_called()

@pytest.mark.asyncio
async def test_handle_drop_triggers_refresh(mock_persistence, mock_changelog, mock_config, mock_ygo, mock_ui):
    page = BulkAddPage()
    page.current_collection_obj = MagicMock()

    # Mock refresh methods
    page.render_library_content = MagicMock()
    page.render_library_content.refresh = MagicMock()
    page.render_header = MagicMock()
    page.render_header.refresh = MagicMock()
    page.load_collection_data = AsyncMock()

    # Setup library filtered state so it finds the entry
    api_card = ApiCard(id=1, name="Test Card", type="Monster", desc="Desc", frameType="normal")
    entry = LibraryEntry(
        id="card_1",
        api_card=api_card,
        set_code="SET",
        set_name="Set",
        rarity="Common",
        image_url="url",
        image_id=1
    )
    page.state['library_filtered'] = [entry]

    # Mock event
    event = MagicMock()
    event.args = {
        'detail': {
            'data_id': 'card_1',
            'from_id': 'library-list',
            'to_id': 'collection-list'
        }
    }

    with patch('src.ui.bulk_add.CollectionEditor.apply_change', return_value=True):
        await page.handle_drop(event)

        # Expect refresh NOT called
        page.render_library_content.refresh.assert_not_called()
