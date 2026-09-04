"""Regression tests for immutable views and AI feature consistency."""

from mahjong.core.hand import Hand
from mahjong.core.player_state import PlayerState, Wind
from mahjong.core.tile import ALL_TILES_136, make_tiles_from_string
from mahjong.player.advisor import _visible_counts, advise_discards
from mahjong.player.base import build_game_view
from mahjong.player.greedy_ai import GreedyAI, RefinedGreedyAI


def test_game_view_mutation_does_not_mutate_engine_state():
    player = PlayerState(0, "P0")
    player.hand.closed_tiles = list(make_tiles_from_string("123m456p789s東南西白"))
    other = PlayerState(1, "P1")
    other.hand.discard_pool = list(make_tiles_from_string("1m"))
    view = build_game_view(
        0, [player, other], Wind.EAST, 0, 0, 50, [], "",
    )

    view.my_hand.closed_tiles.clear()
    view.opponents[0].discard_pool.clear()

    assert player.hand.closed_tiles
    assert other.hand.discard_pool


def test_baseline_and_refined_features_share_draw_visibility():
    hand = Hand()
    hand.draw(ALL_TILES_136[0])
    player = PlayerState(0, "P0")
    player.hand = hand
    opponent = PlayerState(1, "P1")
    view = build_game_view(0, [player, opponent], Wind.EAST, 0, 0, 50, [])

    baseline = GreedyAI("base")._extract_features(view)
    refined = RefinedGreedyAI("refined")._extract_features(view)

    assert baseline.visible_34 == refined.visible_34
    assert baseline.visible_34[0] == 1


def test_advisor_excludes_removed_sanma_draws_and_counts_kita():
    hand = Hand()
    hand.closed_tiles = list(make_tiles_from_string("123p456p789s東南西北"))
    players = [PlayerState(0, "P0"), PlayerState(1, "P1"), PlayerState(2, "P2")]
    players[0].hand = hand
    view = build_game_view(0, players, Wind.EAST, 0, 0, 50, [])
    view.my_kita_count = 1

    visible = _visible_counts(view)
    assert visible[30] >= 1
    assert all(visible[i] == 4 for i in range(1, 8))

    advice = advise_discards(view, list(hand.closed_tiles))
    assert all(
        draw_idx not in range(1, 8)
        for item in advice
        for draw_idx in item.accepts
    )


def test_greedy_score_estimator_converts_indicator_to_actual_dora():
    players = [PlayerState(i, f"P{i}") for i in range(4)]
    view = build_game_view(
        0, players, Wind.EAST, 0, 0, 50, [ALL_TILES_136[0]],
    )

    assert GreedyAI("test")._dora_indices_for_scoring(view) == [1]
