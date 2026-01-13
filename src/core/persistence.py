import json
import yaml
import os
from typing import List, Optional
from src.core.models import Collection

DATA_DIR = "data"

class PersistenceManager:
    def __init__(self, data_dir: str = DATA_DIR):
        self.data_dir = data_dir
        os.makedirs(self.data_dir, exist_ok=True)

    def list_collections(self) -> List[str]:
        """Returns a list of available collection filenames."""
        files = [f for f in os.listdir(self.data_dir) if f.endswith(('.json', '.yaml', '.yml'))]
        return files

    def load_collection(self, filename: str) -> Collection:
        """Loads a collection from a JSON or YAML file."""
        filepath = os.path.join(self.data_dir, filename)
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"Collection file {filename} not found.")

        with open(filepath, 'r', encoding='utf-8') as f:
            if filename.endswith('.json'):
                data = json.load(f)
            elif filename.endswith(('.yaml', '.yml')):
                data = yaml.safe_load(f)
            else:
                raise ValueError("Unsupported file format")

        return Collection(**data)

    def save_collection(self, collection: Collection, filename: str):
        """Saves a collection to a file."""
        filepath = os.path.join(self.data_dir, filename)
        data = collection.model_dump(mode='json')

        with open(filepath, 'w', encoding='utf-8') as f:
            if filename.endswith('.json'):
                json.dump(data, f, indent=2)
            elif filename.endswith(('.yaml', '.yml')):
                yaml.safe_dump(data, f)
            else:
                raise ValueError("Unsupported file format")

# Global instance
persistence = PersistenceManager()
