"""Test LearnedDiscardPolicy and DiscardPolicy protocol."""

import os
from mahjong.core.hand import Hand
from mahjong.core.player_state import Wind
from mahjong.core.tile import Tile, make_tiles_from_string
from mahjong.player.base import GameView
from mahjong.player.ai_policy import LearnedDiscardPolicy, extract_candidate_features
from mahjong.player.ai_features import extract_public_features
from scripts.train_ai_policy import extract_candidate_features as extract_training_features


def make_test_game_view(hand_str="123m456p789s東南西白發"):
    hand = Hand()
    for t in make_tiles_from_string(hand_str):
        hand.closed_tiles.append(t)
    return GameView(
        my_hand=hand,
        my_seat=0,
        my_wind=Wind.EAST,
        my_score=25000,
        is_dealer=True,
        opponents=[],
        round_wind=Wind.EAST,
        honba=0,
        riichi_sticks=0,
        remaining_tiles=50,
        dora_indicators=make_tiles_from_string("1m"),
    )


def test_learned_policy_fallback_on_unloaded():
    policy = LearnedDiscardPolicy()
    assert not policy.is_loaded
    gv = make_test_game_view()
    ranked = policy.rank_discards(gv, gv.my_hand.closed_tiles)
    assert len(ranked) == len(gv.my_hand.closed_tiles)


def test_learned_policy_loads_and_ranks():
    model_path = "data/ai/discard_policy.json"
    assert os.path.exists(model_path)

    policy = LearnedDiscardPolicy(model_path)
    assert policy.is_loaded

    gv = make_test_game_view()
    ranked = policy.rank_discards(gv, gv.my_hand.closed_tiles)
    assert len(ranked) == len(gv.my_hand.closed_tiles)
    assert isinstance(ranked[0], Tile)


def test_training_and_inference_candidate_features_match():
    gv = make_test_game_view()
    features = extract_public_features(gv)
    tile = gv.my_hand.closed_tiles[0]
    current = gv.my_hand.to_34_array()
    inferred = extract_candidate_features(gv, features, tile, current, 0)
    record = {
        "is_sanma": features.is_sanma,
        "is_dealer": gv.is_dealer,
        "my_wind": gv.my_wind.index34,
        "round_wind": gv.round_wind.index34,
        "remaining_tiles": gv.remaining_tiles,
        "hand_counts": current,
        "visible_counts": features.visible_34,
        "live_counts": features.live_34,
        "has_riichi_threat": bool(features.riichi_opponents),
    }
    candidate = {
        "tile_34": tile.index34,
        "is_red": tile.is_red,
        "is_dora": tile.index34 in features.dora_indices or tile.is_red,
        "is_safe": False,
    }

    trained = extract_training_features(record, candidate)

    assert trained == inferred


def test_bundled_policy_declares_legacy_feature_transform():
    policy = LearnedDiscardPolicy("data/ai/discard_policy.json")

    assert policy.feature_transform == "legacy_normalized"
