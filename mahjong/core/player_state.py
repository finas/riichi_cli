"""Player state tracking during a game."""

from enum import IntEnum
from typing import List, Sequence

from .hand import Hand
from .tile import Tile


class Wind(IntEnum):
    EAST = 0    # 東
    SOUTH = 1   # 南
    WEST = 2    # 西
    NORTH = 3   # 北

    @property
    def kanji(self) -> str:
        return ['東', '南', '西', '北'][self.value]

    @property
    def index34(self) -> int:
        """34 encoding index for this wind tile."""
        return 27 + self.value


def player_name_key(name: str) -> str:
    """Return the canonical key used when comparing display names."""
    if not isinstance(name, str) or not name.strip():
        raise ValueError("player names must be non-empty strings")
    return name.strip().casefold()


def validate_player_names(names: Sequence[str]) -> None:
    """Reject blank or duplicate player names before state is created."""
    seen = {}
    for seat, name in enumerate(names):
        key = player_name_key(name)
        if key in seen:
            raise ValueError(
                f"duplicate player name {name!r} at seats {seen[key]} and {seat}"
            )
        seen[key] = seat


class PlayerState:
    """Complete state for one player across a game.

    Attributes:
        seat: Seat index (0-3, fixed)
        name: Display name
        score: Current score in points
        hand: Current hand state (reset each round)
        seat_wind: Current seat wind (changes each round)
        is_dealer: Whether this player is the dealer this round
        kita_tiles: Tiles declared as kita (sanma north tiles)
    """

    def __init__(self, seat: int, name: str, score: int = 25000):
        self.seat = seat
        self.name = name
        self.score = score
        self.hand = Hand()
        self.seat_wind = Wind.EAST
        self.is_dealer = False
        self.kita_tiles: List[Tile] = []

    def reset_for_round(self, seat_wind: Wind, is_dealer: bool):
        """Reset hand state for a new round."""
        self.hand = Hand()
        self.seat_wind = seat_wind
        self.is_dealer = is_dealer
        self.kita_tiles = []

    @property
    def is_riichi(self) -> bool:
        return self.hand.is_riichi

    @property
    def is_menzen(self) -> bool:
        return self.hand.is_menzen

    def __repr__(self):
        return f"PlayerState({self.name}, {self.seat_wind.kanji}, {self.score}点)"
