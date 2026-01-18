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
        # Mock load_ui_state to return empty dict by default so defaults work
        p.load_ui_state.return_value = {}
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
def mock_structure_deck_dialog():
    with patch('src.ui.bulk_add.StructureDeckDialog') as s:
        s.return_value = MagicMock()
        yield s

@pytest.fixture
def mock_ui():
    with patch('src.ui.bulk_add.ui') as u:
        yield u

@pytest.mark.asyncio
async def test_bulk_add_initialization(mock_persistence, mock_config, mock_ygo, mock_structure_deck_dialog):
    page = BulkAddPage()
    assert page.state['selected_collection'] == 'test_collection.json'
    assert page.state['default_language'] == 'EN'

@pytest.mark.asyncio
async def test_add_card_to_collection(mock_persistence, mock_changelog, mock_config, mock_ygo, mock_ui, mock_structure_deck_dialog):
    page = BulkAddPage()
    page.current_collection_obj = MagicMock()
    page.current_collection_obj.cards = []

    # Mock render_header refresh
    page.render_header = MagicMock()
    page.render_header.refresh = MagicMock()

    # Mock render_library_content refresh
    page.render_library_content = MagicMock()
    page.render_library_content.refresh = MagicMock()

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
async def test_undo_last_action(mock_persistence, mock_changelog, mock_config, mock_ygo, mock_ui, mock_structure_deck_dialog):
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

@pytest.mark.asyncio
async def test_apply_collection_filters_monster_category(mock_persistence, mock_config, mock_ygo, mock_structure_deck_dialog):
    page = BulkAddPage()

    # Mock data
    c1 = ApiCard(id=1, name="Synchro Mon", type="Synchro Monster", desc="...", frameType="synchro")
    c2 = ApiCard(id=2, name="Effect Mon", type="Effect Monster", desc="...", frameType="effect")

    e1 = BulkCollectionEntry(
        id="1", api_card=c1, quantity=1, set_code="SET", rarity="Common",
        language="EN", condition="NM", first_edition=False, image_url="", image_id=1, variant_id="v1"
    )
    e2 = BulkCollectionEntry(
        id="2", api_card=c2, quantity=1, set_code="SET", rarity="Common",
        language="EN", condition="NM", first_edition=False, image_url="", image_id=2, variant_id="v2"
    )

    page.col_state['collection_cards'] = [e1, e2]
    page.col_state['collection_page_size'] = 10 # Set explicitly to avoid Mock
    page.render_collection_content = MagicMock()
    page.render_collection_content.refresh = MagicMock()

    # Filter Synchro
    page.col_state['filter_monster_category'] = ['Synchro']
    await page.apply_collection_filters()

    res = page.col_state['collection_filtered']
    assert len(res) == 1
    assert res[0].id == "1"

    # Filter Effect
    page.col_state['filter_monster_category'] = ['Effect']
    await page.apply_collection_filters()

    res = page.col_state['collection_filtered']
    # Note: Synchro Monster is usually also Effect Monster if not Normal.
    # But matches_category handles logic.
    # In API data, "Synchro Monster" implies Effect unless explicitly Normal.
    # So "Effect" should match both if logic is robust, or just Effect Mon if specific.
    # ApiCard.matches_category logic:
    # if "Effect" in type -> True.
    # "Synchro Monster" does NOT have "Effect" in string.
    # Fallback: if Synchro/Fusion/... and not Normal in type -> True.
    # So Synchro Mon should match Effect too.
    assert len(res) == 2

    # Filter Normal (assuming neither is Normal)
    page.col_state['filter_monster_category'] = ['Normal']
    await page.apply_collection_filters()

    res = page.col_state['collection_filtered']
    assert len(res) == 0

@pytest.mark.asyncio
async def test_bulk_add_loads_persisted_state(mock_persistence, mock_config, mock_ygo, mock_structure_deck_dialog):
    # Setup persisted state
    mock_persistence.load_ui_state.return_value = {
        'bulk_selected_collection': 'test_collection.json',
        'bulk_default_lang': 'DE',
        'bulk_default_cond': 'Played',
        'bulk_default_first': True
    }

    page = BulkAddPage()

    assert page.state['selected_collection'] == 'test_collection.json'
    assert page.state['default_language'] == 'DE'
    assert page.state['default_condition'] == 'Played'
    assert page.state['default_first_ed'] is True

@pytest.mark.asyncio
async def test_bulk_add_saves_collection_on_change(mock_persistence, mock_config, mock_ygo, mock_structure_deck_dialog):
    page = BulkAddPage()
    # Mock render_header refresh and load_collection_data
    page.render_header = MagicMock()
    page.load_collection_data = AsyncMock()

    await page.on_collection_change('test_collection.json')

    mock_persistence.save_ui_state.assert_called_with({'bulk_selected_collection': 'test_collection.json'})
