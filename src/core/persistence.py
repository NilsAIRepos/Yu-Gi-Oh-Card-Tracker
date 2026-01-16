import json
import yaml
import os
import logging
from typing import List, Optional
from src.core.models import Collection

DATA_DIR = "data"
COLLECTIONS_DIR = os.path.join(DATA_DIR, "collections")
logger = logging.getLogger(__name__)

class PersistenceManager:
    def __init__(self, data_dir: str = COLLECTIONS_DIR):
        self.data_dir = data_dir
        os.makedirs(self.data_dir, exist_ok=True)

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

        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                if filename.endswith('.json'):
                    json.dump(data, f, indent=2)
                elif filename.endswith(('.yaml', '.yml')):
                    yaml.safe_dump(data, f)
                else:
                    raise ValueError("Unsupported file format")
        except Exception as e:
            logger.error(f"Error saving collection {filename}: {e}")
            raise

# Global instance
persistence = PersistenceManager()
