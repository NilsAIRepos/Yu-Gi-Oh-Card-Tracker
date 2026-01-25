from src.core.models import Collection, ApiCard
from src.services.collection_editor import CollectionEditor
from src.services.ygo_api import ygo_service
from typing import Dict, Any, List
import logging

logger = logging.getLogger(__name__)

class UndoService:
    @staticmethod
    def apply_inverse(collection: Collection, change_record: Dict[str, Any]):
        """
        Applies the inverse of the recorded change to the collection.
        Handles both single and batch changes.
        """
        if not change_record:
            return

        change_type = change_record.get('type')

        if change_type == 'batch':
            # Reverse the list of changes to undo in LIFO order
            changes = change_record.get('changes', [])
            for change in reversed(changes):
                UndoService._apply_single_inverse(collection, change)
        else:
            UndoService._apply_single_inverse(collection, change_record)

    @staticmethod
    def _apply_single_inverse(collection: Collection, change: Dict[str, Any]):
        action = change.get('action')
        quantity = change.get('quantity', 1)
        card_data = change.get('card_data', {})

        # Invert Action
        # Logged ADD -> Undo is REMOVE (negative quantity add)
        # Logged REMOVE -> Undo is ADD (positive quantity add)

        final_quantity = 0
        if action == 'ADD':
            final_quantity = -quantity
        elif action == 'REMOVE':
            final_quantity = quantity
        else:
            return # Unknown action

        # Extract Card Data
        card_id = card_data.get('card_id')
        if not card_id:
            logger.error("Missing card_id in undo record")
            return

        # Try to get full card data, fall back to dummy if offline/error
        api_card = ygo_service.get_card(card_id)
        if not api_card:
            api_card = ApiCard(
                id=card_id,
                name=card_data.get('name', 'Unknown Card'),
                type="Unknown",
                frameType="unknown",
                desc="Restored from Undo"
            )

        CollectionEditor.apply_change(
            collection=collection,
            api_card=api_card,
            set_code=card_data.get('set_code'),
            rarity=card_data.get('rarity'),
            language=card_data.get('language', 'EN'),
            quantity=final_quantity,
            condition=card_data.get('condition', 'Near Mint'),
            first_edition=card_data.get('first_edition', False),
            image_id=card_data.get('image_id'),
            variant_id=card_data.get('variant_id'),
            mode='ADD', # CollectionEditor handles +/- quantity
            storage_location=card_data.get('storage_location')
        )
