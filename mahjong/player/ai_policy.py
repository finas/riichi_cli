"""DiscardPolicy protocol and learned discard policy adapter.

Defines the DiscardPolicy protocol and provides a lightweight, pure-Python
learned discard policy that loads trained weights (Linear or Neural MLP)
with automatic deterministic fallback to GreedyAI.
"""

import json
import math
import os
from typing import Any, Dict, List, Optional, Protocol, Tuple

from mahjong.core.tile import Tile, YAOCHU_INDICES
from mahjong.player.base import GameView
from mahjong.player.greedy_ai import GreedyAI, _cached_ukeire
from mahjong.player.ai_features import extract_public_features, PublicFeatures
from mahjong.player.feature_schema import (
    build_candidate_features, normalize_legacy_features,
)
from mahjong.rules.shanten import shanten


class DiscardPolicy(Protocol):
    """Protocol for pluggable discard ranking policies."""
    def rank_discards(self, game_view: GameView, legal_discards: List[Tile]) -> List[Tile]:
        """Rank legal discards from most recommended to least recommended."""
        ...


def extract_candidate_features(
    game_view: GameView,
    features: PublicFeatures,
    tile: Tile,
    current_34: List[int],
    current_shanten: int,
) -> List[float]:
    """Extract a rich 18-dimensional numeric feature vector for a candidate discard tile."""
    test_34 = list(current_34)
    test_34[tile.index34] -= 1
    post_s = float(shanten(test_34))
    is_sanma = features.is_sanma

    # Ukeire for top shapes
    ukeire = 0.0
    if post_s <= 2:
        ukeire = float(_cached_ukeire(tuple(test_34), tuple(features.visible_34), int(post_s), is_sanma))

    is_dora = 1.0 if (tile.index34 in features.dora_indices or tile.is_red) else 0.0
    tile_34 = tile.index34
    is_yaochu = 1.0 if (tile_34 in YAOCHU_INDICES) else 0.0
    is_honor = 1.0 if tile.is_honor else 0.0
    is_yakuhai = 1.0 if (tile_34 in (31, 32, 33) or tile_34 == game_view.my_wind.index34 or tile_34 == game_view.round_wind.index34) else 0.0

    visible_count = float(features.visible_34[tile_34])
    live_count = float(features.live_34[tile_34])
    in_hand = float(current_34[tile_34])

    is_safe = 1.0 if (tile_34 in features.common_genbutsu or tile_34 in features.suji_safe or tile_34 in features.kabe_no_chance) else 0.0
    has_threat = 1.0 if len(features.riichi_opponents) > 0 else 0.0
    is_dealer = 1.0 if game_view.is_dealer else 0.0
    remaining = float(game_view.remaining_tiles) / 70.0

    num = (tile_34 % 9) + 1 if tile_34 < 27 else 0
    is_central = 1.0 if (2 <= num <= 8) else 0.0
    is_pair = 1.0 if in_hand >= 2 else 0.0

    suit = tile_34 // 9 if tile_34 < 27 else -1
    suit_density = 0.0
    if suit != -1:
        suit_total = sum(current_34[suit * 9 : suit * 9 + 9])
        suit_density = suit_total / 14.0

    return build_candidate_features(
        post_shanten=post_s,
        ukeire=ukeire,
        is_dora=bool(is_dora),
        is_yaochu=bool(is_yaochu),
        is_honor=bool(is_honor),
        is_yakuhai=bool(is_yakuhai),
        visible_count=visible_count,
        live_count=live_count,
        in_hand=in_hand,
        is_safe=bool(is_safe),
        has_threat=bool(has_threat),
        is_dealer=bool(is_dealer),
        remaining_tiles=game_view.remaining_tiles,
        is_pair=bool(is_pair),
        is_central=bool(is_central),
        suit_density=suit_density,
    )


class LearnedDiscardPolicy:
    """Lightweight learned discard policy with pure-Python inference (Linear & Neural MLP)."""

    def __init__(self, model_path: Optional[str] = None):
        self.model_type: str = "linear"
        self.weights: List[float] = []
        self.bias: float = 0.0
        self.W1: List[List[float]] = []
        self.b1: List[float] = []
        self.W2: List[float] = []
        self.b2: float = 0.0
        self.is_loaded = False
        self.feature_transform = "identity"
        self.fallback = GreedyAI(name="FallbackGreedyAI")

        if model_path and os.path.exists(model_path):
            self.load(model_path)

    def load(self, model_path: str):
        """Load trained weights from JSON."""
        try:
            with open(model_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                self.model_type = data.get("type", "linear")
                self.feature_transform = data.get(
                    "feature_transform", "legacy_normalized"
                )
                if self.model_type == "mlp":
                    self.W1 = data.get("W1", [])
                    self.b1 = data.get("b1", [])
                    self.W2 = data.get("W2", [])
                    self.b2 = data.get("b2", 0.0)
                    self.is_loaded = bool(self.W1 and self.W2)
                else:
                    self.weights = data.get("weights", [])
                    self.bias = data.get("bias", 0.0)
                    self.is_loaded = bool(self.weights)
        except Exception:
            self.is_loaded = False

    def score_candidate(self, feature_vec: List[float]) -> float:
        """Compute score for discard recommendation (higher score = more recommended discard)."""
        if not self.is_loaded:
            return 0.0

        if self.feature_transform == "legacy_normalized":
            feature_vec = normalize_legacy_features(feature_vec)

        if self.model_type == "mlp":
            hidden_dim = len(self.b1)
            input_dim = len(feature_vec)
            if len(self.W1) != input_dim:
                return 0.0
            # Hidden layer (ReLU)
            score = self.b2
            for j in range(hidden_dim):
                val = self.b1[j] + sum(feature_vec[i] * self.W1[i][j] for i in range(input_dim))
                h_j = max(0.0, val) # ReLU
                score += h_j * self.W2[j]
            return score
        else:
            if len(feature_vec) != len(self.weights):
                return 0.0
            return sum(w * x for w, x in zip(self.weights, feature_vec)) + self.bias

    def rank_discards(self, game_view: GameView, legal_discards: List[Tile]) -> List[Tile]:
        """Rank legal discards using learned weights, falling back to GreedyAI on error."""
        if not self.is_loaded or not legal_discards:
            return self.fallback.rank_discards(game_view, legal_discards)

        try:
            feat = extract_public_features(game_view)
            current_34 = game_view.my_hand.to_34_array()
            cur_s = shanten(current_34)

            scored = []
            for t in legal_discards:
                vec = extract_candidate_features(game_view, feat, t, current_34, cur_s)
                s = self.score_candidate(vec)
                scored.append((s, t))

            # Higher score = more recommended discard
            scored.sort(key=lambda item: -item[0])
            return [item[1] for item in scored]
        except Exception:
            # Deterministic fallback guarantee
            return self.fallback.rank_discards(game_view, legal_discards)
