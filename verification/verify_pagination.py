
import sys
import os
import asyncio
import logging
from unittest.mock import MagicMock

# Adjust path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.ui.collection import CollectionPage, CardViewModel
from src.core.models import ApiCard, ApiCardImage, ApiCardSet

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def verify_pagination():
    logger.info("Starting pagination verification...")

    page = CollectionPage()

    # Mock UI dependencies
    page.update_pagination = MagicMock()
    page.update_pagination_labels = MagicMock()
    page.prepare_current_page_images = MagicMock(return_value=asyncio.Future())
    page.prepare_current_page_images.return_value.set_result(None)
    page.render_card_display = MagicMock()
    page.render_card_display.refresh = MagicMock()

    # Populate dummy data so apply_filters doesn't return early
    api_card = ApiCard(
        id=1, name="Test", type="Monster", frameType="normal", desc="Desc",
        card_images=[ApiCardImage(id=1, image_url="u", image_url_small="s")]
    )
    vm = CardViewModel(api_card, 0, False)
    page.state['cards_consolidated'] = [vm]
    page.state['view_scope'] = 'consolidated'

    # Test 1: Default behavior (reset_page=True)
    page.state['page'] = 5
    logger.info(f"Test 1 - Initial Page: {page.state['page']}")
    await page.apply_filters()
    logger.info(f"Test 1 - After apply_filters(): {page.state['page']}")

    if page.state['page'] == 1:
        logger.info("PASS: Page reset to 1 by default.")
    else:
        logger.error("FAIL: Page did not reset to 1.")

    # Test 2: explicit reset_page=False
    page.state['page'] = 5
    logger.info(f"Test 2 - Initial Page: {page.state['page']}")
    await page.apply_filters(reset_page=False)
    logger.info(f"Test 2 - After apply_filters(reset_page=False): {page.state['page']}")

    if page.state['page'] == 5:
        logger.info("PASS: Page preserved when reset_page=False.")
    else:
        logger.error("FAIL: Page was reset unexpectedly.")

    # Test 3: load_data(keep_page=True)
    # Mock dependencies for load_data

    # Let's check if we can import run and mock it
    from nicegui import run
    f_run = asyncio.Future()
    f_run.set_result([vm])
    run.io_bound = MagicMock(return_value=f_run)

    # Mock ygo_service
    from src.services.ygo_api import ygo_service
    f = asyncio.Future()
    f.set_result([api_card])
    ygo_service.load_card_database = MagicMock(return_value=f)

    # Also need to mock persistence.load_collection since it's awaited in run.io_bound
    # Actually run.io_bound is mocked above, but let's be safe

    page.apply_filters = MagicMock(return_value=asyncio.Future())
    page.apply_filters.return_value.set_result(None)

    await page.load_data(keep_page=True)

    # Check if apply_filters was called with reset_page=False
    call_args = page.apply_filters.call_args
    logger.info(f"load_data(keep_page=True) called apply_filters with: {call_args}")

    if call_args and call_args.kwargs.get('reset_page') is False:
        logger.info("PASS: load_data(keep_page=True) called apply_filters(reset_page=False).")
    else:
        logger.error(f"FAIL: load_data arguments incorrect: {call_args}")

if __name__ == "__main__":
    asyncio.run(verify_pagination())
