import asyncio
import unittest
from unittest.mock import MagicMock, patch, AsyncMock
import sys
import os
import logging

# Add src to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

# Mock nicegui before importing single_card_view to avoid runtime errors if it tries to init things
# But we patch it inside the test, which is safer if the import doesn't do side effects.
# single_card_view imports `ui` at top level. We can patch `src.ui.components.single_card_view.ui`.

from src.ui.components.single_card_view import SingleCardView
from src.services.ygo_api import ApiCard

class TestSingleCardViewAsync(unittest.IsolatedAsyncioTestCase):
    async def test_remove_button_async_issue(self):
        # Mock dependencies
        with patch('src.ui.components.single_card_view.ui') as mock_ui, \
             patch('src.ui.components.single_card_view.ygo_service', new_callable=AsyncMock) as mock_service:

            # Setup Mock UI Context Managers
            mock_context = MagicMock()
            mock_context.__enter__.return_value = mock_context
            mock_context.__exit__.return_value = None

            mock_ui.card.return_value = mock_context
            mock_ui.row.return_value = mock_context
            mock_ui.column.return_value = mock_context
            mock_ui.dialog.return_value = mock_context
            mock_ui.expansion.return_value = mock_context

            # Capture button on_click handlers
            buttons = []
            def mock_button(text, on_click=None):
                btn = MagicMock()
                btn.text = text
                btn.on_click = on_click
                # Store for inspection
                buttons.append({'text': text, 'on_click': on_click})
                return btn
            mock_ui.button.side_effect = mock_button

            # Instantiate View
            view = SingleCardView()

            # Dummy Data
            card = MagicMock(spec=ApiCard)
            card.id = 123
            card.card_sets = []
            card.card_images = []

            input_state = {
                'language': 'EN',
                'set_base_code': 'SDK-001',
                'rarity': 'Common',
                'condition': 'Near Mint',
                'first_edition': False,
                'image_id': 123,
                'quantity': 1
            }

            set_options = {'SDK-001': 'SDK-001'}
            set_info_map = {'SDK-001': MagicMock(set_name="Start", set_rarity="Common")}

            on_save_callback = AsyncMock()

            # Call method under test
            view._render_inventory_management(
                card=card,
                input_state=input_state,
                set_options=set_options,
                set_info_map=set_info_map,
                on_change_callback=MagicMock(),
                on_save_callback=on_save_callback,
                default_set_base_code='SDK-001'
            )

            # Find the "REMOVE" button (the one that triggers the dialog)
            # Note: in code it is "REMOVE" (caps)
            remove_trigger_btn = next((b for b in buttons if b['text'] == 'REMOVE'), None)
            self.assertIsNotNone(remove_trigger_btn, "Remove trigger button not found")

            # Simulate clicking "REMOVE" to open dialog
            confirm_remove_handler = remove_trigger_btn['on_click']

            # Verify confirm_remove_handler is a coroutine function
            if asyncio.iscoroutinefunction(confirm_remove_handler):
                await confirm_remove_handler()
            else:
                 confirm_remove_handler()

            # Now inside confirm_remove, it creates a dialog and another "Remove" button
            # We need to find the NEW "Remove" button added during that call
            # The `buttons` list should have grown.

            # The inner button is "Remove" (Title case)
            confirm_btn = next((b for b in buttons if b['text'] == 'Remove'), None)
            self.assertIsNotNone(confirm_btn, "Confirmation Remove button not found")

            # This is the handler with the bug: `do_remove`
            do_remove_handler = confirm_btn['on_click']

            print(f"Handler type: {type(do_remove_handler)}")

            # Execute it
            res = do_remove_handler()

            # If it returns a coroutine (and we are in the patched version), we should await it.
            if asyncio.iscoroutine(res):
                await res

            # ASSERTION:
            # After the fix, on_save_callback SHOULD be called.

            if on_save_callback.called:
                print("SUCCESS: on_save_callback WAS called (Bug Fixed).")
            else:
                self.fail("on_save_callback was NOT called! The bug is still present.")

if __name__ == '__main__':
    unittest.main()
