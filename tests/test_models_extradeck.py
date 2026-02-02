import pytest
from src.core.models import ApiCard

class TestApiCardExtraDeck:
    def create_card(self, type_str: str) -> ApiCard:
        return ApiCard(
            id=123,
            name="Test Card",
            type=type_str,
            frameType="normal",
            desc="desc"
        )

    def test_main_deck_cards(self):
        # Normal Monster
        assert self.create_card("Normal Monster").is_extra_deck is False
        # Effect Monster
        assert self.create_card("Effect Monster").is_extra_deck is False
        # Ritual Monster
        assert self.create_card("Ritual Monster").is_extra_deck is False
        # Ritual Effect Monster
        assert self.create_card("Ritual Effect Monster").is_extra_deck is False
        # Pendulum Monster
        assert self.create_card("Pendulum Effect Monster").is_extra_deck is False
        assert self.create_card("Pendulum Normal Monster").is_extra_deck is False
        # Spells and Traps
        assert self.create_card("Spell Card").is_extra_deck is False
        assert self.create_card("Trap Card").is_extra_deck is False

    def test_extra_deck_cards(self):
        # Fusion
        assert self.create_card("Fusion Monster").is_extra_deck is True
        # Synchro
        assert self.create_card("Synchro Monster").is_extra_deck is True
        # XYZ
        assert self.create_card("XYZ Monster").is_extra_deck is True
        # Link
        assert self.create_card("Link Monster").is_extra_deck is True

    def test_hybrid_cards(self):
        # Synchro Pendulum
        assert self.create_card("Synchro Pendulum Effect Monster").is_extra_deck is True
        # Fusion Pendulum
        assert self.create_card("Fusion Pendulum Effect Monster").is_extra_deck is True
        # XYZ Pendulum
        assert self.create_card("XYZ Pendulum Effect Monster").is_extra_deck is True
