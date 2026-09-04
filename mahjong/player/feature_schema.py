"""Canonical candidate-feature schema shared by training and inference."""

from typing import List


FEATURE_NAMES = [
    "post_shanten", "ukeire", "is_dora", "is_yaochu", "is_honor",
    "is_yakuhai", "visible_count", "live_count", "in_hand", "is_pair",
    "is_central", "suit_density", "is_safe", "has_threat", "is_dealer",
    "remaining", "dead_honor", "isolated_terminal",
]


def build_candidate_features(
    *,
    post_shanten: float,
    ukeire: float,
    is_dora: bool,
    is_yaochu: bool,
    is_honor: bool,
    is_yakuhai: bool,
    visible_count: float,
    live_count: float,
    in_hand: float,
    is_safe: bool,
    has_threat: bool,
    is_dealer: bool,
    remaining_tiles: float,
    is_pair: bool,
    is_central: bool,
    suit_density: float,
) -> List[float]:
    """Build the raw feature vector used by new models."""
    return [
        float(post_shanten),
        float(ukeire),
        float(bool(is_dora)),
        float(bool(is_yaochu)),
        float(bool(is_honor)),
        float(bool(is_yakuhai)),
        float(visible_count),
        float(live_count),
        float(in_hand),
        float(bool(is_pair)),
        float(bool(is_central)),
        float(suit_density),
        float(bool(is_safe)),
        float(bool(has_threat)),
        float(bool(is_dealer)),
        float(remaining_tiles) / 70.0,
        float(bool(is_honor and visible_count >= 2)),
        float(bool(is_yaochu and not is_honor and in_hand == 1)),
    ]


def normalize_legacy_features(feature_vec: List[float]) -> List[float]:
    """Adapt canonical raw features for models trained by the old script."""
    if len(feature_vec) != len(FEATURE_NAMES):
        return list(feature_vec)
    scales = [1.0, 20.0, 1.0, 1.0, 1.0, 1.0,
              4.0, 4.0, 4.0, 1.0, 1.0, 1.0,
              1.0, 1.0, 1.0, 1.0, 1.0, 1.0]
    return [value / scale for value, scale in zip(feature_vec, scales)]
