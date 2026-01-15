from typing import List, Optional, Literal
from pydantic import BaseModel, Field
from datetime import datetime
import uuid

# --- Collection Models ---

class CardMetadata(BaseModel):
    set_code: str = Field(..., description="e.g., LOB-EN001")
    rarity: str = Field(..., description="e.g., Ultra Rare, Common")
    language: str = Field("EN", description="Language code, e.g. EN, DE, FR")
    condition: Literal["Mint", "Near Mint", "Played", "Damaged"] = "Near Mint"
    first_edition: bool = False
    storage_location: Optional[str] = Field(None, description="e.g., Box A, Row 2")
    purchase_price: float = 0.0
    market_value: float = 0.0
    purchase_date: Optional[datetime] = None

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
    set_name: str
    set_code: str
    set_rarity: str
    set_rarity_code: Optional[str] = None
    set_price: Optional[str] = None
    image_id: Optional[int] = None

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
