import unittest
import os
import shutil
from src.core.persistence import PersistenceManager
from src.core.models import Deck

class TestPersistenceDelete(unittest.TestCase):
    def setUp(self):
        self.test_dir = "test_data_delete"
        self.decks_dir = os.path.join(self.test_dir, "decks")
        self.pm = PersistenceManager(data_dir=os.path.join(self.test_dir, "collections"), decks_dir=self.decks_dir)

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def test_delete_deck(self):
        deck_name = "ToDelete"
        deck = Deck(name=deck_name)
        filename = f"{deck_name}.ydk"
        self.pm.save_deck(deck, filename)

        self.assertTrue(os.path.exists(os.path.join(self.decks_dir, filename)))

        self.pm.delete_deck(filename)

        self.assertFalse(os.path.exists(os.path.join(self.decks_dir, filename)))

if __name__ == '__main__':
    unittest.main()
