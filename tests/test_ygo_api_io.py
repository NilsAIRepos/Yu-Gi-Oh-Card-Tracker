import unittest
from unittest.mock import MagicMock, patch
import os
import json
import asyncio
from src.services.ygo_api import YugiohService, SETS_FILE

class TestYugiohServiceIO(unittest.TestCase):
    def setUp(self):
        self.service = YugiohService()
        self.test_file = "test_io.json"
        # Data matching the expected structure of sets.json (new format)
        self.data = {
            "SET1": {"name": "Test Set", "code": "SET1", "image": "img", "date": "2023", "count": 10}
        }

    def tearDown(self):
        if os.path.exists(self.test_file):
            os.remove(self.test_file)

    def test_read_write_json_file(self):
        # Test Save
        self.service._save_json_file(self.test_file, self.data)
        self.assertTrue(os.path.exists(self.test_file))

        # Verify content with standard json to ensure compatibility
        with open(self.test_file, 'r', encoding='utf-8') as f:
            loaded_std = json.load(f)
        self.assertEqual(loaded_std, self.data)

        # Test Read using service method
        loaded_service = self.service._read_json_file(self.test_file)
        self.assertEqual(loaded_service, self.data)

    def test_fetch_all_sets_io_bound(self):
        # Mock run.io_bound to verify it's called
        with patch('src.services.ygo_api.run.io_bound', new_callable=MagicMock) as mock_io_bound:
            # We need to mock the coroutine return
            f = asyncio.Future()
            f.set_result(self.data)
            mock_io_bound.return_value = f

            # Setup a fake sets file so it tries to read from disk
            # Ensure the directory exists because _save_json_file might rely on it or the main code does
            # but _save_json_file just opens. The main code ensures dir exists.
            # We'll rely on setUp creating a valid state or just writing to SETS_FILE (mocked or real)
            # Actually writing to SETS_FILE is dangerous if it overwrites real data.
            # But SETS_FILE is imported. We should probably patch SETS_FILE path,
            # but simpler to just write it if we know we are in a sandbox.
            # Just to be safe, we'll write it, then restore it or delete it.

            # Use a safe path
            safe_sets_file = "test_sets.json"
            with patch('src.services.ygo_api.SETS_FILE', safe_sets_file):
                self.service._save_json_file(safe_sets_file, self.data)

                # Since fetch_all_sets is async, we need to run it
                asyncio.run(self.service.fetch_all_sets(force_refresh=False))

                # Clean up
                if os.path.exists(safe_sets_file):
                    os.remove(safe_sets_file)

            # Verify io_bound was called
            # It should have been called to read the file
            mock_io_bound.assert_called()

    def test_orjson_usage(self):
        # Check if HAS_ORJSON matches imports
        try:
            import orjson
            has_orjson_env = True
        except ImportError:
            has_orjson_env = False

        from src.services.ygo_api import HAS_ORJSON
        self.assertEqual(HAS_ORJSON, has_orjson_env)

if __name__ == "__main__":
    unittest.main()
