import json
import os
from typing import Dict, Any

CONFIG_FILE = "config.json"

class ConfigManager:
    def __init__(self, config_file: str = CONFIG_FILE):
        self.config_file = config_file
        self.config: Dict[str, Any] = self._load_config()

    def _load_config(self) -> Dict[str, Any]:
        if not os.path.exists(self.config_file):
            return self._default_config()

        try:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return self._default_config()

    def _default_config(self) -> Dict[str, Any]:
        return {
            "language": "en",
            "theme": "dark"
        }

    def save_config(self):
        with open(self.config_file, 'w', encoding='utf-8') as f:
            json.dump(self.config, f, indent=2)

    def get_language(self) -> str:
        return self.config.get("language", "en")

    def set_language(self, language: str):
        self.config["language"] = language
        self.save_config()

config_manager = ConfigManager()
