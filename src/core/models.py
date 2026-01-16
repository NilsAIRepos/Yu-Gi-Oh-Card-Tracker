from typing import List, Optional, Literal
from pydantic import BaseModel, Field
import uuid

# --- Collection Models ---

class CardMetadata(BaseModel):
    set_code: str = Field(..., description="e.g., LOB-EN001")
    rarity: str = Field(..., description="e.g., Ultra Rare, Common")
    image_id: Optional[int] = Field(None, description="Specific image ID for art variation")
    language: str = Field("EN", description="Language code, e.g. EN, DE, FR")
    condition: Literal["Mint", "Near Mint", "Played", "Damaged"] = "Near Mint"
    first_edition: bool = False
    storage_location: Optional[str] = Field(None, description="e.g., Box A, Row 2")
    market_value: float = 0.0

class Card(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    quantity: int = 1
    image_url: Optional[str] = None
    metadata: CardMetadata

class Collection(BaseModel):
    name: str
    description: Optional[str] = ""
    cards: List[Card] = []

    @property
    def total_value(self) -> float:
        return sum(card.metadata.market_value * card.quantity for card in self.cards)

    @property
    def total_cards(self) -> int:
        return sum(card.quantity for card in self.cards)

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
