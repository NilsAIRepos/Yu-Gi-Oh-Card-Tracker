import json
import yaml
import os
import time
import logging
import uuid
from typing import List, Optional
from src.core.models import Collection, Deck

DATA_DIR = "data"
COLLECTIONS_DIR = os.path.join(DATA_DIR, "collections")
DECKS_DIR = os.path.join(DATA_DIR, "decks")
logger = logging.getLogger(__name__)

class PersistenceManager:
    def __init__(self, data_dir: str = COLLECTIONS_DIR, decks_dir: str = DECKS_DIR):
        self.data_dir = data_dir
        self.decks_dir = decks_dir
        os.makedirs(self.data_dir, exist_ok=True)
        os.makedirs(self.decks_dir, exist_ok=True)

    def list_collections(self) -> List[str]:
        """Returns a list of available collection filenames."""
        files = [f for f in os.listdir(self.data_dir) if f.endswith(('.json', '.yaml', '.yml'))]
        return files

    def load_collection(self, filename: str) -> Collection:
        """Loads a collection from a JSON or YAML file."""
        logger.info(f"Loading collection: {filename}")
        filepath = os.path.join(self.data_dir, filename)
        if not os.path.exists(filepath):
            logger.error(f"Collection file {filename} not found.")
            raise FileNotFoundError(f"Collection file {filename} not found.")

        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                if filename.endswith('.json'):
                    data = json.load(f)
                elif filename.endswith(('.yaml', '.yml')):
                    data = yaml.safe_load(f)
                else:
                    raise ValueError("Unsupported file format")

            return Collection(**data)
        except Exception as e:
            logger.error(f"Error loading collection {filename}: {e}")
            raise

    def save_collection(self, collection: Collection, filename: str):
        """Saves a collection to a file."""
        logger.info(f"Saving collection: {filename}")
        filepath = os.path.join(self.data_dir, filename)
        data = collection.model_dump(mode='json')
        # Use UUID to prevent collisions if multiple saves run concurrently
        temp_filepath = filepath + f".{uuid.uuid4()}.tmp"

        try:
            with open(temp_filepath, 'w', encoding='utf-8') as f:
                if filename.endswith('.json'):
                    json.dump(data, f, indent=2)
                elif filename.endswith(('.yaml', '.yml')):
                    yaml.safe_dump(data, f)
                else:
                    raise ValueError("Unsupported file format")
                f.flush()
                os.fsync(f.fileno())

            # Retry logic for Windows file locking issues
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    os.replace(temp_filepath, filepath)
                    break
                except PermissionError as e:
                    if attempt < max_retries - 1:
                        time.sleep(0.1)  # Wait a bit before retrying
                    else:
                        raise e
        except Exception as e:
            logger.error(f"Error saving collection {filename}: {e}")
            if os.path.exists(temp_filepath):
                try:
                    os.remove(temp_filepath)
                except OSError:
                    pass
            raise

    # --- Deck Management ---

    def list_decks(self) -> List[str]:
        """Returns a list of available deck filenames."""
        files = [f for f in os.listdir(self.decks_dir) if f.endswith('.ydk')]
        return files

    def load_deck(self, filename: str) -> Deck:
        """Loads a deck from a .ydk file."""
        logger.info(f"Loading deck: {filename}")
        filepath = os.path.join(self.decks_dir, filename)
        if not os.path.exists(filepath):
            logger.error(f"Deck file {filename} not found.")
            raise FileNotFoundError(f"Deck file {filename} not found.")

        deck = Deck(name=filename.replace('.ydk', ''))
        current_section = 'main'

        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line: continue
                    if line.startswith('#'):
                        if 'main' in line.lower():
                            current_section = 'main'
                        elif 'extra' in line.lower():
                            current_section = 'extra'
                        elif 'side' in line.lower():
                            current_section = 'side'
                        continue
                    elif line.startswith('!'):
                        if 'side' in line.lower():
                            current_section = 'side'
                        continue

                    if not line.isdigit():
                        continue

                    card_id = int(line)
                    if current_section == 'main':
                        deck.main.append(card_id)
                    elif current_section == 'extra':
                        deck.extra.append(card_id)
                    elif current_section == 'side':
                        deck.side.append(card_id)

            return deck
        except Exception as e:
            logger.error(f"Error loading deck {filename}: {e}")
            raise

    def save_deck(self, deck: Deck, filename: str):
        """Saves a deck to a .ydk file."""
        logger.info(f"Saving deck: {filename}")
        filepath = os.path.join(self.decks_dir, filename)

        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write("#created by OpenYugi\n")
                f.write("#main\n")
                for card_id in deck.main:
                    f.write(f"{card_id}\n")

                f.write("#extra\n")
                for card_id in deck.extra:
                    f.write(f"{card_id}\n")

                f.write("!side\n")
                for card_id in deck.side:
                    f.write(f"{card_id}\n")

        except Exception as e:
            logger.error(f"Error saving deck {filename}: {e}")
            raise

    # --- UI State Persistence ---

    def load_ui_state(self) -> dict:
        """Loads UI state from data/ui_state.json."""
        filepath = os.path.join(DATA_DIR, "ui_state.json")
        if not os.path.exists(filepath):
            return {}
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading UI state: {e}")
            return {}

    def save_ui_state(self, state: dict):
        """Saves UI state to data/ui_state.json. Merges with existing state."""
        filepath = os.path.join(DATA_DIR, "ui_state.json")
        try:
            current = self.load_ui_state()
            current.update(state)
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(current, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving UI state: {e}")

# Global instance
persistence = PersistenceManager()
