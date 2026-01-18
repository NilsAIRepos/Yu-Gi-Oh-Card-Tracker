from typing import List, Optional, Literal
from pydantic import BaseModel, Field
import uuid

# --- Collection Models ---

class CollectionEntry(BaseModel):
    condition: Literal["Mint", "Near Mint", "Played", "Damaged"] = "Near Mint"
    language: str = "EN"
    first_edition: bool = False
    quantity: int = 1
    storage_location: Optional[str] = Field(None, description="e.g., Box A, Row 2")
    purchase_price: Optional[float] = 0.0
    market_value: Optional[float] = 0.0
    purchase_date: Optional[str] = None

class CollectionVariant(BaseModel):
    variant_id: str
    set_code: str
    rarity: str
    image_id: Optional[int] = None
    entries: List[CollectionEntry] = []

    @property
    def total_quantity(self) -> int:
        return sum(e.quantity for e in self.entries)

class CollectionCard(BaseModel):
    card_id: int
    name: str
    variants: List[CollectionVariant] = []

    @property
    def total_quantity(self) -> int:
        return sum(v.total_quantity for v in self.variants)

class Collection(BaseModel):
    name: str
    description: Optional[str] = ""
    cards: List[CollectionCard] = []

    @property
    def total_value(self) -> float:
        val = 0.0
        for card in self.cards:
            for var in card.variants:
                for entry in var.entries:
                    val += (entry.market_value or 0.0) * entry.quantity
        return val

    @property
    def total_cards(self) -> int:
        return sum(c.total_quantity for c in self.cards)

class Deck(BaseModel):
    name: str = "New Deck"
    main: List[int] = []
    extra: List[int] = []
    side: List[int] = []

# --- Legacy Models (Deprecated) ---
# Keeping these temporarily to prevent immediate ImportErrors in other files during refactoring
class CardMetadata(BaseModel):
    set_code: str
    rarity: str
    image_id: Optional[int] = None
    language: str = "EN"
    condition: str = "Near Mint"
    first_edition: bool = False
    market_value: float = 0.0

class Card(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    quantity: int = 1
    image_url: Optional[str] = None
    metadata: CardMetadata

# --- API/Database Models ---

class ApiCardImage(BaseModel):
    id: int
    image_url: str
    image_url_small: str
    image_url_cropped: Optional[str] = None

class ApiCardSet(BaseModel):
    variant_id: Optional[str] = None
    set_name: str
    set_code: str
    set_rarity: str
    set_rarity_code: Optional[str] = None
    set_price: Optional[str] = None
    image_id: Optional[int] = Field(None, alias='card_image_id')

    model_config = {
        "populate_by_name": True
    }

class ApiCardPrice(BaseModel):
    cardmarket_price: Optional[str] = None
    tcgplayer_price: Optional[str] = None
    ebay_price: Optional[str] = None
    amazon_price: Optional[str] = None
    coolstuffinc_price: Optional[str] = None

class ApiCard(BaseModel):
    id: int
    name: str
    type: str
    frameType: str
    desc: str
    typeline: Optional[List[str]] = None
    race: Optional[str] = None
    atk: Optional[int] = None
    def_: Optional[int] = Field(None, alias="def")
    level: Optional[int] = None
    scale: Optional[int] = None
    linkval: Optional[int] = None
    linkmarkers: Optional[List[str]] = None
    attribute: Optional[str] = None
    archetype: Optional[str] = None
    card_images: List[ApiCardImage] = []
    card_sets: List[ApiCardSet] = []
    card_prices: List[ApiCardPrice] = []

    def matches_category(self, category: str) -> bool:
        """
        Checks if the card belongs to the specified monster category (e.g., 'Normal', 'Effect', 'Synchro').
        Handles special logic for 'Normal' vs 'Effect' distinction for Extra Deck monsters.
        """
        # Use typeline if available
        if self.typeline is not None:
            if category == "Effect":
                return "Effect" in self.typeline
            elif category == "Normal":
                if "Normal" in self.typeline:
                    return True
                # Check for Non-Effect Extra Deck / Ritual
                # Synchro/Fusion/XYZ/Link/Ritual without "Effect" in typeline are "Normal" (Non-Effect).
                is_extra_or_ritual = any(t in self.type for t in ["Synchro", "Fusion", "XYZ", "Link", "Ritual"])
                if is_extra_or_ritual and "Effect" not in self.typeline:
                    return True
                return False
            else:
                return category in self.type or category in self.typeline

        # Fallback Legacy Logic
        card_type = self.type
        if category == "Effect":
            # Special logic for Effect:
            # 1. Explicitly in type string
            if "Effect" in card_type: return True
            # 2. Implied by Extra Deck / Ritual / Pendulum types (unless Normal is present)
            implied_types = ["Synchro", "Fusion", "XYZ", "Link", "Ritual", "Pendulum"]
            if any(t in card_type for t in implied_types) and "Normal" not in card_type:
                return True
            return False
        else:
            return category in card_type
