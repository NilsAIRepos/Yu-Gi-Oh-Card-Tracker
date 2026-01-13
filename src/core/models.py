from typing import List, Optional, Literal
from pydantic import BaseModel, Field
from datetime import datetime
import uuid

class CardMetadata(BaseModel):
    set_code: str = Field(..., description="e.g., LOB-EN001")
    rarity: str = Field(..., description="e.g., Ultra Rare, Common")
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
