import json
import os
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

CONFIG_PATH = "data/scanner_config.json"

DEFAULT_CONFIG = {
    "ocr_tracks": ["doctr"],
    "preprocessing_mode": "classic",
    "art_match_yolo": True,
    "ambiguity_threshold": 10.0,
    "rotation": 0,
    "scan_overlay_duration": 1000
}

def load_config() -> Dict[str, Any]:
    if not os.path.exists(CONFIG_PATH):
        return DEFAULT_CONFIG.copy()

    try:
        with open(CONFIG_PATH, 'r') as f:
            config = json.load(f)
            # Merge with defaults to ensure all keys exist
            merged = DEFAULT_CONFIG.copy()
            merged.update(config)
            return merged
    except Exception as e:
        logger.error(f"Failed to load config: {e}")
        return DEFAULT_CONFIG.copy()

def save_config(config: Dict[str, Any]):
    try:
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, 'w') as f:
            json.dump(config, f, indent=4)
    except Exception as e:
        logger.error(f"Failed to save config: {e}")
