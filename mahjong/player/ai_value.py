"""Value and risk expected point (EV) model for Mahjong AI.

Models expected point outcomes based on win probabilities, deal-in risks,
hand values, and placement pressure in match situations.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from mahjong.core.tile import Tile, YAOCHU_INDICES
from mahjong.player.base import GameView
from mahjong.player.ai_features import PublicFeatures, extract_public_features
from mahjong.rules.shanten import shanten


@dataclass
class ActionEV:
    """Expected value analysis for an action."""
    action_name: str
    expected_value: float            # Net expected match points (+/-)
    win_prob: float                  # Estimated probability of winning the hand
    win_points: float                # Estimated points on win
    deal_in_prob: float              # Estimated probability of dealing into opponent
    deal_in_points: float            # Estimated point loss on deal-in
    placement_adjustment: float      # Placement bonus/penalty based on rank & round


class ValueAndRiskEstimator:
    """Calculates match EV for tactical choices."""

    def __init__(self):
        # Base win probabilities by shanten and round progress
        self._base_win_prob_by_shanten = {
            0: 0.55,  # Tenpai
            1: 0.25,  # 1-shanten
            2: 0.10,  # 2-shanten
            3: 0.03,  # 3-shanten
            4: 0.01,
        }

    def estimate_win_probability(
        self,
        current_shanten: int,
        ukeire: int,
        remaining_tiles: int,
        is_dealer: bool,
    ) -> float:
        """Estimate win probability from shanten, ukeire, and remaining wall."""
        base_p = self._base_win_prob_by_shanten.get(current_shanten, 0.005)
        
        # Turn decay: late game has less time to complete shapes
        turn_factor = max(0.1, min(1.2, remaining_tiles / 50.0))
        
        # Ukeire boost
        ukeire_factor = 1.0 + min(0.5, (ukeire - 4) * 0.05) if ukeire > 4 else 0.8
        
        # Dealer has slight offensive tempo edge
        dealer_factor = 1.15 if is_dealer else 1.0

        return max(0.001, min(0.85, base_p * turn_factor * ukeire_factor * dealer_factor))

    def estimate_deal_in_probability(
        self,
        tile: Tile,
        features: PublicFeatures,
    ) -> float:
        """Estimate deal-in probability for discarding a specific tile."""
        if not features.riichi_opponents:
            return 0.02  # Low background dama risk

        idx = tile.index34
        # 1. 100% Genbutsu to all
        if idx in features.common_genbutsu:
            return 0.0

        # 2. Genbutsu to at least one opponent
        if any(idx in g_set for g_set in features.genbutsu_by_seat.values()):
            return 0.06

        # 3. Kabe No-Chance
        if idx in features.kabe_no_chance:
            return 0.04

        # 4. Tedashi Suji (1 or 9)
        if idx in features.tedashi_suji and idx < 27 and (idx % 9) in (0, 8):
            return 0.04

        # 5. Regular Suji
        if idx in features.suji_safe:
            return 0.07

        # 6. Honors
        if tile.is_honor:
            seen = features.visible_34[idx]
            if seen >= 4:
                return 0.0
            if seen == 3:
                return 0.03
            if seen == 2:
                return 0.08
            return 0.18  # Live honor (生牌)

        # 7. Dangerous Middle Tile (Unsuji 4, 5, 6)
        num = (idx % 9) + 1
        if num in (4, 5, 6):
            return 0.22
        return 0.14

    def calculate_placement_pressure(
        self,
        game_view: GameView,
    ) -> float:
        """Calculate placement pressure modifier based on rank and round."""
        scores = [(opp.score, opp.seat) for opp in game_view.opponents]
        scores.append((game_view.my_score, game_view.my_seat))
        scores.sort(key=lambda x: -x[0])

        my_rank = 0
        for rank, (score, seat) in enumerate(scores):
            if seat == game_view.my_seat:
                my_rank = rank
                break

        is_late_round = ("南" in game_view.round_label or "South" in game_view.round_label or "West" in game_view.round_label)

        if not is_late_round:
            return 0.0

        # Late game (South / All-Last) placement dynamics:
        if my_rank == 0:
            # 1st place: strongly penalize dealing in (protect lead)
            return -500.0
        elif my_rank == len(scores) - 1:
            # 4th place: reward aggressive pushes for comebacks
            return +800.0
        return 0.0

    def evaluate_discard_ev(
        self,
        game_view: GameView,
        tile: Tile,
        post_shanten: int,
        ukeire: int,
        estimated_win_points: float,
    ) -> ActionEV:
        """Evaluate match EV for discarding candidate tile."""
        features = extract_public_features(game_view)
        
        p_win = self.estimate_win_probability(
            post_shanten, ukeire, game_view.remaining_tiles, game_view.is_dealer
        )
        p_deal_in = self.estimate_deal_in_probability(tile, features)
        
        # Dealer averages ~6000 pt win; non-dealer ~4000 pt win
        win_pts = max(1000.0, estimated_win_points if estimated_win_points > 0 else (6000.0 if game_view.is_dealer else 4000.0))
        # Expected riichi loss is typically ~6000-8000 points
        loss_pts = 8000.0 if any(opp.is_dealer and opp.is_riichi for opp in game_view.opponents) else 5500.0

        placement_adj = self.calculate_placement_pressure(game_view)

        net_ev = (p_win * win_pts) - (p_deal_in * loss_pts) + placement_adj

        return ActionEV(
            action_name=f"discard_{tile}",
            expected_value=net_ev,
            win_prob=p_win,
            win_points=win_pts,
            deal_in_prob=p_deal_in,
            deal_in_points=loss_pts,
            placement_adjustment=placement_adj,
        )
