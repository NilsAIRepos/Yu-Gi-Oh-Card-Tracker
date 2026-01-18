import json
import os

DB_DIR = "data/db"
LANG = "en"
FILENAME = f"card_db_{LANG}.json"
FILEPATH = os.path.join(DB_DIR, FILENAME)

mock_cards = [
    {
        "id": 12345678,
        "name": "Dark Magician",
        "type": "Normal Monster",
        "desc": "The ultimate wizard.",
        "atk": 2500,
        "def": 2100,
        "level": 7,
        "race": "Spellcaster",
        "attribute": "DARK",
        "card_sets": [
            {
                "set_name": "Legend of Blue Eyes White Dragon",
                "set_code": "LOB-EN005",
                "set_rarity": "Ultra Rare",
                "set_price": "50.00"
            }
        ],
        "card_images": [
            {
                "id": 12345678,
                "image_url": "https://images.ygoprodeck.com/images/cards/12345678.jpg",
                "image_url_small": "https://images.ygoprodeck.com/images/cards_small/12345678.jpg"
            }
        ]
    },
    {
        "id": 87654321,
        "name": "Blue-Eyes White Dragon",
        "type": "Normal Monster",
        "desc": "Powerful engine of destruction.",
        "atk": 3000,
        "def": 2500,
        "level": 8,
        "race": "Dragon",
        "attribute": "LIGHT",
        "card_sets": [
            {
                "set_name": "Legend of Blue Eyes White Dragon",
                "set_code": "LOB-EN001",
                "set_rarity": "Ultra Rare",
                "set_price": "80.00"
            }
        ],
        "card_images": [
            {
                "id": 87654321,
                "image_url": "https://images.ygoprodeck.com/images/cards/89631139.jpg",
                "image_url_small": "https://images.ygoprodeck.com/images/cards_small/89631139.jpg"
            }
        ]
    }
]

if not os.path.exists(DB_DIR):
    os.makedirs(DB_DIR)

with open(FILEPATH, 'w') as f:
    json.dump(mock_cards, f, indent=2)

print(f"Seeded {len(mock_cards)} cards into {FILEPATH}")
