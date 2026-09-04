"""Hybrid AI combining tactical heuristics, learned discard policy, and EV risk estimation."""

import os
from typing import List, Optional

from mahjong.core.tile import Tile
from mahjong.engine.action import Action, ActionType, AvailableActions
from mahjong.player.ai_features import extract_public_features
from mahjong.player.ai_policy import LearnedDiscardPolicy, extract_candidate_features
from mahjong.player.ai_value import ValueAndRiskEstimator
from mahjong.player.base import Player, GameView
from mahjong.player.greedy_ai import GreedyAI
from mahjong.rules.shanten import shanten


class HybridAI(Player):
    """Tournament-grade Hybrid AI combining heuristic search, EV estimation, and learned policy."""

    def __init__(
        self,
        name: str = "HybridAI",
        model_path: Optional[str] = None,
        enable_ev_fold: bool = True,
    ):
        super().__init__(name)
        if model_path is None:
            default_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                "data",
                "ai",
                "discard_policy.json",
            )
            model_path = default_path if os.path.exists(default_path) else None

        self.greedy = GreedyAI(name=f"{name}_greedy")
        self.policy = LearnedDiscardPolicy(model_path=model_path)
        self.ev_estimator = ValueAndRiskEstimator()
        self.enable_ev_fold = enable_ev_fold

    def choose_action(self, game_view: GameView, available: AvailableActions) -> Action:
        """Choose the optimal action using hybrid decision logic."""
        # 1. Instant wins: Tsumo, Ron, Kyuushu
        if available.can_tsumo:
            return Action(ActionType.TSUMO, available.player)
        if available.can_ron:
            return Action(ActionType.RON, available.player)
        if available.can_kyuushu:
            counts = game_view.my_hand.to_34_array()
            if sum(1 for i in range(27, 34) if counts[i] > 0) >= 5:
                return Action(ActionType.KYUUSHU, available.player)

        # 2. Sanma Kita
        if available.can_kita:
            current_34 = game_view.my_hand.to_34_array()
            test_34 = list(current_34)
            test_34[30] -= 1
            if shanten(test_34) <= shanten(current_34):
                return Action(ActionType.KITA, available.player)

        # 3. Riichi Declaration
        if available.can_riichi and available.riichi_candidates:
            if self.greedy._should_declare_riichi(game_view, available.riichi_candidates):
                discard = self.choose_riichi_discard(game_view, available.riichi_candidates)
                return Action(ActionType.RIICHI, available.player, riichi_discard=discard)

        # 4. Melds: Delegate to Greedy tactical evaluation
        if available.can_pon or available.can_chi or available.can_ankan or available.can_shouminkan or available.can_daiminkan:
            act = self.greedy.choose_action(game_view, available)
            if act.action_type != ActionType.SKIP:
                return act

        # 5. Discards: Evaluate Push / Fold / Efficiency
        if available.can_discard:
            discard_tile = self.choose_discard(game_view, available.can_discard)
            return Action(ActionType.DISCARD, available.player, tile=discard_tile)

        return Action(ActionType.SKIP, available.player)

    def choose_riichi_discard(self, game_view: GameView, candidates: List[Tile]) -> Tile:
        """Choose best discard for Riichi."""
        return self.greedy.choose_riichi_discard(game_view, candidates)

    def choose_discard(self, game_view: GameView, available: List[Tile]) -> Tile:
        """Rank discards using Minimum Shanten Filter + Learned Policy + EV Defense."""
        if not available:
            return game_view.my_hand.closed_tiles[-1]

        features = extract_public_features(game_view)
        hand_34 = game_view.my_hand.to_34_array()
        cur_s = shanten(hand_34)

        # A. Defense against Riichi: Use Value & Risk EV Model
        if features.riichi_opponents and self.enable_ev_fold:
            if cur_s >= 2:
                safe_ranked = self.greedy._rank_safe_tiles(game_view, available)
                if safe_ranked:
                    return safe_ranked[0]
            elif cur_s == 1:
                ev = self.ev_estimator.evaluate_discard_ev(
                    game_view=game_view,
                    tile=available[0],
                    post_shanten=1,
                    ukeire=6,
                    estimated_win_points=4000.0,
                )
                if ev.expected_value < 500:
                    safe_ranked = self.greedy._rank_safe_tiles(game_view, available)
                    if safe_ranked:
                        return safe_ranked[0]

        # B. Shanten-optimal candidates
        cand_shanten = []
        for t in available:
            test_34 = list(hand_34)
            test_34[t.index34] -= 1
            cand_shanten.append((shanten(test_34), t))

        min_s = min(s for s, _ in cand_shanten)
        optimal_candidates = [t for s, t in cand_shanten if s == min_s]

        # C. Offensive ranking with Learned Policy over optimal candidates
        if self.policy.is_loaded and len(optimal_candidates) > 1:
            scored = []
            for t in optimal_candidates:
                vec = extract_candidate_features(game_view, features, t, hand_34, cur_s)
                s = self.policy.score_candidate(vec)
                scored.append((s, t))
            scored.sort(key=lambda item: -item[0])
            return scored[0][1]

        # D. Heuristic fallback
        return self.greedy.choose_discard(game_view, available)
