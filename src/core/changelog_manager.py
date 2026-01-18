import os
import json
import time
import logging
from typing import Dict, Any, Optional, List, Union

logger = logging.getLogger(__name__)

CHANGELOGS_DIR = os.path.join("data", "changelogs")

class ChangelogManager:
    def __init__(self, data_dir: str = CHANGELOGS_DIR):
        self.data_dir = data_dir
        os.makedirs(self.data_dir, exist_ok=True)

    def _get_filepath(self, collection_name: str) -> str:
        # Sanitize name to avoid path traversal
        safe_name = os.path.basename(collection_name)
        # Distinct log file for each collection file
        return os.path.join(self.data_dir, f"{safe_name}.log")

    def log_change(self, collection_name: str, action_type: str, card_data: Dict[str, Any], quantity: int):
        """
        Logs a single change to the collection's changelog.
        action_type: 'ADD' or 'REMOVE'
        card_data: dict containing card identifiers and variant properties
        """
        self._write_entry(collection_name, {
            "action": action_type,
            "quantity": quantity,
            "card_data": card_data,
            "type": "single"
        })

    def log_batch_change(self, collection_name: str, description: str, changes: List[Dict[str, Any]]):
        """
        Logs a batch of changes as a single transaction.
        changes: List of dicts, each containing {'action':..., 'quantity':..., 'card_data':...}
        """
        self._write_entry(collection_name, {
            "action": "BATCH",
            "description": description,
            "changes": changes,
            "type": "batch"
        })

    def _write_entry(self, collection_name: str, entry_data: Dict[str, Any]):
        filepath = self._get_filepath(collection_name)
        timestamp = time.time()

        # Calculate ID based on existing lines
        current_id = 0
        if os.path.exists(filepath):
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    current_id = sum(1 for _ in f)
            except Exception:
                pass

        new_id = current_id + 1
        entry_data['id'] = new_id
        entry_data['timestamp'] = timestamp

        try:
            with open(filepath, 'a', encoding='utf-8') as f:
                f.write(json.dumps(entry_data) + "\n")

            if entry_data.get('type') == 'batch':
                logger.info(f"Logged batch change for {collection_name}: {entry_data.get('description')}")
            else:
                logger.info(f"Logged change for {collection_name}: {entry_data.get('action')} {entry_data.get('quantity')}x")
        except Exception as e:
            logger.error(f"Failed to log change for {collection_name}: {e}")

    def get_last_change(self, collection_name: str) -> Optional[Dict[str, Any]]:
        """Returns the last change for a collection, or None if empty."""
        history = self.load_history(collection_name)
        if history:
            return history[-1]
        return None

    def load_history(self, collection_name: str) -> List[Dict[str, Any]]:
        filepath = self._get_filepath(collection_name)
        if not os.path.exists(filepath):
            return []

        history = []
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.strip():
                        history.append(json.loads(line))
        except Exception as e:
            logger.error(f"Error loading history for {collection_name}: {e}")

        return history

    def undo_last_change(self, collection_name: str) -> Optional[Dict[str, Any]]:
        """
        Removes the last entry from the log and returns it.
        """
        history = self.load_history(collection_name)
        if not history:
            return None

        last_item = history.pop()

        # Rewrite file
        filepath = self._get_filepath(collection_name)
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                for item in history:
                    f.write(json.dumps(item) + "\n")
        except Exception as e:
            logger.error(f"Error rewriting history for {collection_name}: {e}")
            return None

        return last_item

changelog_manager = ChangelogManager()
