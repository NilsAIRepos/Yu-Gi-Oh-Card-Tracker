import pytest
from unittest.mock import MagicMock, AsyncMock, patch
import asyncio
import time
from src.ui.scan import ScanPage

@pytest.fixture
def scan_page():
    # Mock dependencies
    with patch('src.ui.scan.persistence.load_ui_state', return_value={}), \
         patch('src.ui.scan.persistence.list_collections', return_value=[]), \
         patch('src.ui.scan.config_manager.load_config', return_value={}):
        page = ScanPage()

        # Mock UI elements to prevent NiceGUI errors
        page.auto_mode_btn = MagicMock()
        page.auto_mode_btn.props = MagicMock()

        # Mock scanner service access
        page.is_active = True

        return page

@pytest.mark.asyncio
async def test_toggle_auto_mode(scan_page):
    # Mock scanner manager
    with patch('src.ui.scan.scanner_service.scanner_manager') as mock_mgr:
        mock_mgr.is_paused.return_value = True

        # Mock ui.timer
        with patch('src.ui.scan.ui.timer') as mock_timer:
            # Enable
            await scan_page.toggle_auto_mode()
            assert scan_page.auto_mode == True
            assert mock_mgr.resume.called
            assert scan_page.auto_scan_timer is not None

            # Disable
            await scan_page.toggle_auto_mode()
            assert scan_page.auto_mode == False
            assert scan_page.auto_scan_timer is None

@pytest.mark.asyncio
async def test_auto_scan_loop_trigger(scan_page):
    scan_page.auto_mode = True
    scan_page.scan_in_progress = False
    scan_page.motion_threshold = 50
    scan_page.last_scan_time = 0
    scan_page.auto_scan_timeout = 1000

    # Mock scanner manager
    with patch('src.ui.scan.scanner_service.scanner_manager') as mock_mgr:
        mock_mgr.is_paused.return_value = False

        # Mock JS execution
        async def mock_run_js(script):
            if 'calculateMotion' in script:
                return 10 # Low score < 50
            return None

        with patch('src.ui.scan.ui.run_javascript', new_callable=AsyncMock) as mock_js, \
             patch('src.ui.scan.ui.notify'):

            mock_js.side_effect = mock_run_js

            # Mock triggering scan
            scan_page.trigger_live_scan = AsyncMock()

            # Run loop for 5 ticks (required for consecutive still frames)
            for _ in range(5):
                await scan_page._auto_scan_tick()

            # Should have triggered
            assert scan_page.trigger_live_scan.called
            assert scan_page.scan_in_progress == True

@pytest.mark.asyncio
async def test_auto_scan_loop_motion_reset(scan_page):
    scan_page.auto_mode = True
    scan_page.consecutive_still_frames = 3
    scan_page.motion_threshold = 50

    with patch('src.ui.scan.scanner_service.scanner_manager') as mock_mgr:
        mock_mgr.is_paused.return_value = False

        # Simulate High Motion (Use AsyncMock)
        mock_js = AsyncMock(return_value=100)
        with patch('src.ui.scan.ui.run_javascript', mock_js), \
             patch('src.ui.scan.ui.notify'):

            await scan_page._auto_scan_tick()

            # Should reset counter
            assert scan_page.consecutive_still_frames == 0

@pytest.mark.asyncio
async def test_auto_scan_wait_for_movement(scan_page):
    scan_page.auto_mode = True
    scan_page.waiting_for_movement = True
    scan_page.consecutive_still_frames = 0
    scan_page.motion_threshold = 50

    with patch('src.ui.scan.scanner_service.scanner_manager') as mock_mgr:
        mock_mgr.is_paused.return_value = False

        with patch('src.ui.scan.ui.notify'): # Mock notify
            # 1. Stillness (should NOT trigger logic, just wait)
            mock_js_still = AsyncMock(return_value=10)
            with patch('src.ui.scan.ui.run_javascript', mock_js_still):
                await scan_page._auto_scan_tick()
                assert scan_page.waiting_for_movement == True

            # 2. Movement (should reset state)
            mock_js_move = AsyncMock(return_value=100)
            with patch('src.ui.scan.ui.run_javascript', mock_js_move):
                await scan_page._auto_scan_tick()
                assert scan_page.waiting_for_movement == False
                assert scan_page.consecutive_still_frames == 0

@pytest.mark.asyncio
async def test_auto_scan_timeout_logic(scan_page):
    scan_page.auto_mode = True
    scan_page.last_scan_result = 'fail'
    scan_page.last_scan_time = time.time() # Just failed
    scan_page.auto_scan_timeout = 5000 # 5 seconds
    scan_page.consecutive_still_frames = 5 # Ready to scan
    scan_page.motion_threshold = 50

    with patch('src.ui.scan.scanner_service.scanner_manager') as mock_mgr:
        mock_mgr.is_paused.return_value = False
        scan_page.trigger_live_scan = AsyncMock()

        with patch('src.ui.scan.ui.notify'): # Mock notify
            # 1. Immediate tick (should skip due to timeout)
            mock_js = AsyncMock(return_value=10)
            with patch('src.ui.scan.ui.run_javascript', mock_js):
                await scan_page._auto_scan_tick()
                assert not scan_page.trigger_live_scan.called

            # 2. Advance time past timeout
            scan_page.last_scan_time = time.time() - 6 # 6 seconds ago

            with patch('src.ui.scan.ui.run_javascript', mock_js):
                await scan_page._auto_scan_tick()
                assert scan_page.trigger_live_scan.called
