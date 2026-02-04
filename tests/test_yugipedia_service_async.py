
import unittest
import asyncio
from unittest.mock import MagicMock, patch
from src.services.yugipedia_service import YugipediaService

class TestYugipediaServiceAsync(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.service = YugipediaService()

    @patch('src.services.yugipedia_service.run.io_bound')
    async def test_get_all_decks(self, mock_get):
        # Mock Response
        mock_response = MagicMock()
        mock_response.status_code = 200
        # We need to simulate the response structure for the loop.
        # But wait, the loop calls fetch_category_smw 3 times.
        # It's easier if we mock side_effect to return different responses based on query.

        def side_effect(*args, **kwargs):
            params = kwargs.get('params', {})
            query = params.get('query', '')

            resp = MagicMock()
            resp.status_code = 200

            if "TCG_Structure_Decks" in query:
                resp.json.return_value = {
                    "query": {
                        "results": {
                            "Structure Deck: Test": {"printouts": {"English set prefix": ["SD01"]}}
                        }
                    }
                }
            elif "TCG_Starter_Decks" in query:
                resp.json.return_value = {
                    "query": {
                        "results": {
                            "Starter Deck: Standard": {"printouts": {"English set prefix": ["ST01"]}},
                             "Structure Deck: Test": {"printouts": {"English set prefix": ["SD01"]}}, # Overlap test
                             "Speed Duel Starter Decks: Test Speed": {"printouts": {"English set prefix": ["SS01"]}}
                        }
                    }
                }
            elif "Preconstructed_Decks" in query:
                resp.json.return_value = {
                    "query": {
                        "results": {
                             "2-Player Starter Set": {"printouts": {"English set prefix": ["2PSS"]}},
                             "Structure Deck: Test": {"printouts": {"English set prefix": ["SD01"]}} # Overlap test
                        }
                    }
                }
            else:
                 resp.json.return_value = {"query": {"results": {}}}

            return resp

        mock_get.side_effect = side_effect

        decks = await self.service.get_all_decks()

        # Verify count (deduplicated)
        # Unique Titles:
        # 1. Structure Deck: Test (Structure, Starter, Precon) -> Should remain STRUCTURE (first wins unless Speed)
        # 2. Starter Deck: Standard (Starter)
        # 3. Speed Duel Starter Decks: Test Speed (Starter) -> SPEED
        # 4. 2-Player Starter Set (Precon) -> STRUCTURE
        self.assertEqual(len(decks), 4)

        sd = next(d for d in decks if d.title == "Structure Deck: Test")
        speed = next(d for d in decks if d.title == "Speed Duel Starter Decks: Test Speed")
        starter = next(d for d in decks if d.title == "Starter Deck: Standard")
        precon = next(d for d in decks if d.title == "2-Player Starter Set")

        self.assertEqual(sd.code, "SD01")
        self.assertEqual(sd.deck_type, "STRUCTURE")

        self.assertEqual(speed.code, "SS01")
        self.assertEqual(speed.deck_type, "SPEED")

        self.assertEqual(starter.code, "ST01")
        self.assertEqual(starter.deck_type, "STARTER")

        self.assertEqual(precon.code, "2PSS")
        self.assertEqual(precon.deck_type, "STRUCTURE")

    @patch('src.services.yugipedia_service.run.io_bound')
    async def test_get_all_decks_pagination(self, mock_get):
         def side_effect(*args, **kwargs):
             params = kwargs.get('params', {})
             query = params.get('query', '')
             offset = 0
             import re
             m = re.search(r'offset=(\d+)', query)
             if m: offset = int(m.group(1))

             resp = MagicMock()
             resp.status_code = 200

             if "TCG_Structure_Decks" in query:
                 if offset == 0:
                     resp.json.return_value = {
                        "query": {"results": {"Struct 1": {"printouts": {"English set prefix": ["S1"]}}}},
                        "query-continue-offset": 500
                     }
                 else:
                     resp.json.return_value = {
                        "query": {"results": {"Struct 2": {"printouts": {"English set prefix": ["S2"]}}}}
                     }
             else: # Empty for others to simplify test
                 resp.json.return_value = {
                    "query": {"results": {}}
                 }
             return resp

         mock_get.side_effect = side_effect

         decks = await self.service.get_all_decks()

         titles = sorted([d.title for d in decks])
         self.assertEqual(titles, ["Struct 1", "Struct 2"])

if __name__ == '__main__':
    unittest.main()
