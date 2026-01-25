import unittest
from src.core.models import Collection, ApiCard
from src.services.undo_service import UndoService
from src.services.collection_editor import CollectionEditor

class TestUndoService(unittest.TestCase):
    def setUp(self):
        self.collection = Collection(name="Test Collection")
        self.api_card = ApiCard(id=123, name="Blue-Eyes", type="Monster", frameType="normal", desc="Dragon")

    def test_undo_add(self):
        # Setup: Add a card
        CollectionEditor.apply_change(
            self.collection, self.api_card, "LOB-001", "Ultra Rare", "EN", 1, "Near Mint", False
        )
        self.assertEqual(len(self.collection.cards), 1)
        self.assertEqual(self.collection.cards[0].variants[0].entries[0].quantity, 1)

        # Simulate Log for ADD
        change_record = {
            'action': 'ADD',
            'quantity': 1,
            'card_data': {
                'card_id': 123,
                'set_code': 'LOB-001',
                'rarity': 'Ultra Rare',
                'language': 'EN',
                'condition': 'Near Mint',
                'first_edition': False
            }
        }

        # Undo
        UndoService.apply_inverse(self.collection, change_record)

        # Verify removal
        self.assertEqual(len(self.collection.cards), 0)

    def test_undo_remove(self):
        # Setup: Empty collection.
        # Simulate Log for REMOVE (we removed something that was there).
        # To test Undo Remove (which is Add), we start with empty and expect add.

        change_record = {
            'action': 'REMOVE',
            'quantity': 1,
            'card_data': {
                'card_id': 123,
                'set_code': 'LOB-001',
                'rarity': 'Ultra Rare',
                'language': 'EN',
                'condition': 'Near Mint',
                'first_edition': False
            }
        }

        UndoService.apply_inverse(self.collection, change_record)

        self.assertEqual(len(self.collection.cards), 1)
        self.assertEqual(self.collection.cards[0].variants[0].entries[0].quantity, 1)

    def test_undo_batch(self):
        # Batch log
        change_record = {
            'type': 'batch',
            'changes': [
                {
                    'action': 'ADD',
                    'quantity': 1,
                    'card_data': {'card_id': 123, 'set_code': 'A', 'rarity': 'R', 'language': 'EN', 'condition': 'Near Mint', 'first_edition': False}
                },
                {
                    'action': 'ADD',
                    'quantity': 1,
                    'card_data': {'card_id': 123, 'set_code': 'B', 'rarity': 'R', 'language': 'EN', 'condition': 'Near Mint', 'first_edition': False}
                }
            ]
        }

        # Apply changes first manually to simulate state before undo
        CollectionEditor.apply_change(self.collection, self.api_card, "A", "R", "EN", 1, "Near Mint", False)
        CollectionEditor.apply_change(self.collection, self.api_card, "B", "R", "EN", 1, "Near Mint", False)

        self.assertEqual(len(self.collection.cards[0].variants), 2)

        # Undo Batch
        UndoService.apply_inverse(self.collection, change_record)

        # Should be empty
        self.assertEqual(len(self.collection.cards), 0)

if __name__ == '__main__':
    unittest.main()
