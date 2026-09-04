"""Test ValueAndRiskEstimator (Phase 4 EV Model)."""

from mahjong.core.hand import Hand
from mahjong.core.player_state import Wind
from mahjong.core.tile import Tile, make_tiles_from_string
from mahjong.player.base import GameView, OpponentView
from mahjong.player.ai_value import ValueAndRiskEstimator


def make_test_game_view(hand_str="123m456p789s東南西白發", opponents=None, my_score=25000, round_label="East 1"):
    hand = Hand()
    for t in make_tiles_from_string(hand_str):
        hand.closed_tiles.append(t)
    return GameView(
        my_hand=hand,
        my_seat=0,
        my_wind=Wind.EAST,
        my_score=my_score,
        is_dealer=True,
        opponents=opponents or [],
        round_wind=Wind.EAST,
        honba=0,
        riichi_sticks=0,
        remaining_tiles=50,
        dora_indicators=make_tiles_from_string("1m"),
        round_label=round_label,
    )


def test_win_probability_estimation():
    estimator = ValueAndRiskEstimator()
    # Tenpai (0-shanten) has higher win probability than 2-shanten
    p_tenpai = estimator.estimate_win_probability(0, ukeire=8, remaining_tiles=50, is_dealer=True)
    p_2shanten = estimator.estimate_win_probability(2, ukeire=12, remaining_tiles=50, is_dealer=True)
    assert p_tenpai > p_2shanten


def test_deal_in_probability_genbutsu_vs_unsuji():
    estimator = ValueAndRiskEstimator()
    opp = OpponentView(
        seat=1, name="Opp1", score=25000, seat_wind=Wind.SOUTH,
        is_dealer=False, is_riichi=True,
        discard_pool=make_tiles_from_string("1m"),
        discard_called=[False],
        discard_is_tsumogiri=[False],
        num_closed_tiles=13,
    )
    gv = make_test_game_view(opponents=[opp])
    from mahjong.player.ai_features import extract_public_features
    features = extract_public_features(gv)

    genbutsu_tile = make_tiles_from_string("1m")[0]
    dangerous_tile = make_tiles_from_string("5s")[0]

    p_genbutsu = estimator.estimate_deal_in_probability(genbutsu_tile, features)
    p_danger = estimator.estimate_deal_in_probability(dangerous_tile, features)

    assert p_genbutsu == 0.0
    assert p_danger > 0.15


def test_placement_pressure_all_last():
    estimator = ValueAndRiskEstimator()
    opps = [
        OpponentView(seat=1, name="P1", score=30000, seat_wind=Wind.SOUTH, is_dealer=False, is_riichi=False, num_closed_tiles=13),
        OpponentView(seat=2, name="P2", score=25000, seat_wind=Wind.WEST, is_dealer=False, is_riichi=False, num_closed_tiles=13),
        OpponentView(seat=3, name="P3", score=20000, seat_wind=Wind.NORTH, is_dealer=False, is_riichi=False, num_closed_tiles=13),
    ]
    # In 1st place in South 4: placement pressure is negative (protect lead)
    gv_1st = make_test_game_view(opponents=opps, my_score=40000, round_label="南4局")
    assert estimator.calculate_placement_pressure(gv_1st) < 0

    # In 4th place in South 4: placement pressure is positive (push for comeback)
    gv_4th = make_test_game_view(opponents=opps, my_score=10000, round_label="南4局")
    assert estimator.calculate_placement_pressure(gv_4th) > 0
