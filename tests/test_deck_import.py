import unittest
from unittest.mock import MagicMock, patch, AsyncMock
import asyncio
import sys
import os

# Add src to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.services.deck_import_service import fetch_ygoprodeck_deck

class TestDeckImport(unittest.IsolatedAsyncioTestCase):
    async def test_import_success(self):
        url = "https://ygoprodeck.com/deck/test-deck-123"

        html_content = """
        <html>
            <head>
                <title>Test Deck Name - YGOPRODeck</title>
            </head>
            <body>
                <script>
                    var maindeckjs = '[123, 456, 789]';
                    var extradeckjs = '[111, 222]';
                    var sidedeckjs = '[333]';
                </script>
            </body>
        </html>
        """

        # Mock Response
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.text.return_value = html_content

        # Mock Context Manager for session.get()
        # It needs __aenter__ and __aexit__
        get_ctx = MagicMock()
        get_ctx.__aenter__.return_value = mock_response
        get_ctx.__aexit__.return_value = None

        # Mock Session Instance
        # It needs __aenter__ and __aexit__ to support 'async with aiohttp.ClientSession()'
        mock_session = MagicMock()
        mock_session.get.return_value = get_ctx
        mock_session.__aenter__.return_value = mock_session
        mock_session.__aexit__.return_value = None

        # Correctly patch aiohttp.ClientSession
        # ClientSession() returns the instance, so we mock the class to return our mock_session
        with patch('aiohttp.ClientSession', return_value=mock_session):
            deck = await fetch_ygoprodeck_deck(url)

            self.assertIsNotNone(deck)
            self.assertEqual(deck.name, "Test Deck Name")
            self.assertEqual(deck.main, [123, 456, 789])
            self.assertEqual(deck.extra, [111, 222])
            self.assertEqual(deck.side, [333])

    async def test_invalid_url(self):
        with self.assertRaises(ValueError):
            await fetch_ygoprodeck_deck("https://google.com")

    async def test_fetch_failure(self):
        url = "https://ygoprodeck.com/deck/fail"

        mock_response = AsyncMock()
        mock_response.status = 404

        get_ctx = MagicMock()
        get_ctx.__aenter__.return_value = mock_response
        get_ctx.__aexit__.return_value = None

        mock_session = MagicMock()
        mock_session.get.return_value = get_ctx
        mock_session.__aenter__.return_value = mock_session
        mock_session.__aexit__.return_value = None

        with patch('aiohttp.ClientSession', return_value=mock_session):
            with self.assertRaises(Exception) as cm:
                await fetch_ygoprodeck_deck(url)
            self.assertIn("Failed to fetch URL", str(cm.exception))

if __name__ == '__main__':
    unittest.main()
