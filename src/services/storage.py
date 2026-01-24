import os
import json
import logging
import shutil
from typing import List, Optional, Dict, Any
from nicegui import run
from src.core.models import Collection, StorageDefinition

DATA_DIR = os.path.join(os.getcwd(), "data")
STORAGE_IMG_DIR = os.path.join(DATA_DIR, "collections", "storage")

logger = logging.getLogger(__name__)

class StorageService:
    def __init__(self):
        self._ensure_dirs()

    def _ensure_dirs(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        os.makedirs(STORAGE_IMG_DIR, exist_ok=True)

    def get_all_storage(self, collection: Collection) -> List[Dict[str, Any]]:
        """Retrieves all storage definitions from the collection."""
        if not collection:
            return []

        # Convert Pydantic models to dict for UI consumption
        return [s.model_dump() for s in collection.storage_definitions]

    def get_storage(self, collection: Collection, name: str) -> Optional[Dict[str, Any]]:
        if not collection:
            return None

        for s in collection.storage_definitions:
            if s.name == name:
                return s.model_dump()
        return None

    def add_storage(self, collection: Collection, name: str, type_name: str, description: str = "", image_path: str = None, set_code: str = None) -> bool:
        if not collection:
            return False

        if self.get_storage(collection, name):
            return False # Exists

        new_storage = StorageDefinition(
            name=name,
            type=type_name,
            description=description,
            image_path=image_path,
            set_code=set_code
        )
        collection.storage_definitions.append(new_storage)
        return True

    def update_storage(self, collection: Collection, old_name: str, new_name: str, type_name: str, description: str, image_path: str, set_code: str) -> bool:
        if not collection:
            return False

        # Find storage
        target_s = None
        for s in collection.storage_definitions:
            if s.name == old_name:
                target_s = s
                break

        if not target_s:
            return False

        # If renaming, check conflict
        if old_name != new_name:
            if self.get_storage(collection, new_name):
                return False

        target_s.name = new_name
        target_s.type = type_name
        target_s.description = description
        target_s.image_path = image_path
        target_s.set_code = set_code

        return True

    def delete_storage(self, collection: Collection, name: str) -> bool:
        if not collection:
            return False

        target_s = None
        for s in collection.storage_definitions:
            if s.name == name:
                target_s = s
                break

        if not target_s:
            return False

        collection.storage_definitions.remove(target_s)

        # Cleanup image if custom?
        # Maybe safer to keep images as they might be shared or re-used if logic changes.
        # But per user request images are in data/storage.
        # If we delete, we risk deleting used images if user re-uploaded same file for another storage.
        # For now, let's skip auto-delete of files to be safe, or implement ref counting later.

        return True

    async def save_uploaded_image(self, file_obj, filename: str) -> str:
        """
        Saves an uploaded file to data/storage/ and returns the filename.
        """
        # Clean filename
        safe_name = "".join(c for c in filename if c.isalnum() or c in ('-', '_', '.')).strip()
        file_path = os.path.join(STORAGE_IMG_DIR, safe_name)

        try:
            # Check if it has a save method (NiceGUI FileUpload)
            if hasattr(file_obj, 'save'):
                await file_obj.save(file_path)
            else:
                # Fallback for generic file-like objects
                with open(file_path, 'wb') as f:
                    shutil.copyfileobj(file_obj.file, f)
            return safe_name
        except Exception as e:
            logger.error(f"Error saving storage image: {e}")
            return None

storage_service = StorageService()
