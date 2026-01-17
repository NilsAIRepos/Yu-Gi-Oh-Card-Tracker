import os
import json
import time
import logging
from datetime import datetime
from nicegui import run
import cv2
import numpy as np

logger = logging.getLogger(__name__)

class ScanLogger:
    def __init__(self):
        self.base_dir = os.path.join(os.getcwd(), 'data', 'scan_logs')
        self.images_dir = os.path.join(self.base_dir, 'images')
        self.log_file = os.path.join(self.base_dir, 'scan_log.jsonl')

        self._ensure_dirs()

    def _ensure_dirs(self):
        if not os.path.exists(self.base_dir):
            os.makedirs(self.base_dir)
        if not os.path.exists(self.images_dir):
            os.makedirs(self.images_dir)

    async def log_scan(self, image_bytes: bytes, result: dict, mode: str):
        """
        Logs the scan result and saves the image.
        """
        timestamp = datetime.now().isoformat()
        ts_safe = int(time.time() * 1000)

        image_filename = f"{ts_safe}.jpg"
        image_path = os.path.join(self.images_dir, image_filename)

        # Prepare log entry
        entry = {
            "timestamp": timestamp,
            "mode": mode,
            "image_file": image_filename,
            "success": result.get("success", False),
            "scanned_name": result.get("scanned_name"),
            "matched_name": result.get("matched_name"),
            "confidence": result.get("confidence"),
            "error": result.get("error")
        }

        # IO operations
        await run.io_bound(self._write_log, image_path, image_bytes, entry)

    def _write_log(self, image_path, image_bytes, entry):
        # Save Image
        try:
            with open(image_path, 'wb') as f:
                f.write(image_bytes)
        except Exception as e:
            logger.error(f"Failed to save scan image: {e}")
            entry['image_saved'] = False
        else:
            entry['image_saved'] = True

        # Append to Log
        try:
            with open(self.log_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(entry) + '\n')
        except Exception as e:
            logger.error(f"Failed to write scan log: {e}")

scan_logger = ScanLogger()
