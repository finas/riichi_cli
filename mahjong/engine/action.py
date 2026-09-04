"""Action definitions for the game engine."""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

from mahjong.core.tile import Tile
from mahjong.core.meld import Meld


class ActionType(Enum):
    DISCARD = "discard"
    CHI = "chi"
    PON = "pon"
    ANKAN = "ankan"         # Closed kan
    DAIMINKAN = "daiminkan"  # Open kan from discard
    SHOUMINKAN = "shouminkan"  # Added kan (promote pon)
    RIICHI = "riichi"
    TSUMO = "tsumo"
    RON = "ron"
    SKIP = "skip"
    KITA = "kita"           # North tile declaration (sanma)
    KYUUSHU = "kyuushu"     # Nine different terminals/honors redraw


@dataclass
class Action:
    """A player action."""
    action_type: ActionType
    player: int  # Seat index
    tile: Optional[Tile] = None  # The tile involved
    meld: Optional[Meld] = None  # Meld formed (for chi/pon/kan)
    riichi_discard: Optional[Tile] = None  # Tile to discard for riichi

    def __repr__(self):
        parts = [f"{self.action_type.value}"]
        if self.tile:
            parts.append(f"tile={self.tile.name}")
        return f"Action({', '.join(parts)}, p{self.player})"


@dataclass
class AvailableActions:
    """Available actions for a player at a decision point."""
    player: int
    can_tsumo: bool = False
    can_riichi: bool = False
    can_ankan: List[List[Tile]] = field(default_factory=list)
    can_shouminkan: List[Tile] = field(default_factory=list)
    can_discard: List[Tile] = field(default_factory=list)
    can_chi: List[Meld] = field(default_factory=list)
    can_pon: List[Meld] = field(default_factory=list)
    can_daiminkan: List[Meld] = field(default_factory=list)
    can_ron: bool = False
    can_kita: bool = False
    can_kyuushu: bool = False
    riichi_candidates: List[Tile] = field(default_factory=list)

    @property
    def has_action(self) -> bool:
        """Whether there's any action available beyond just discarding."""
        return (self.can_tsumo or self.can_riichi or
                len(self.can_ankan) > 0 or len(self.can_shouminkan) > 0 or
                len(self.can_chi) > 0 or len(self.can_pon) > 0 or
                len(self.can_daiminkan) > 0 or self.can_ron or
                self.can_kita or self.can_kyuushu)

    def contains(self, action: Action) -> bool:
        """Return whether *action* is legal at this decision point.

        This is intentionally a structural check; the rules engine remains
        responsible for calculating the available actions.  It provides a
        single validation boundary for network and other untrusted players.
        """
        if action.player != self.player:
            return False
        if action.action_type == ActionType.SKIP:
            return True
        if action.action_type == ActionType.TSUMO:
            return self.can_tsumo
        if action.action_type == ActionType.RON:
            return self.can_ron
        if action.action_type == ActionType.KITA:
            return self.can_kita
        if action.action_type == ActionType.KYUUSHU:
            return self.can_kyuushu
        if action.action_type == ActionType.DISCARD:
            return action.tile is not None and action.tile in self.can_discard
        if action.action_type == ActionType.RIICHI:
            tile = action.riichi_discard or action.tile
            return self.can_riichi and tile is not None and tile in self.riichi_candidates
        if action.action_type == ActionType.SHOUMINKAN:
            return action.tile is not None and action.tile in self.can_shouminkan
        if action.action_type == ActionType.ANKAN:
            return action.tile is not None and any(
                action.tile in group for group in self.can_ankan
            )
        if action.action_type == ActionType.CHI:
            return action.meld is not None and action.meld in self.can_chi
        if action.action_type == ActionType.PON:
            return action.meld is not None and action.meld in self.can_pon
        if action.action_type == ActionType.DAIMINKAN:
            return action.meld is not None and action.meld in self.can_daiminkan
        return False
