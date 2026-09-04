"""Test public feature extractor for AI decision engines."""

from mahjong.core.hand import Hand
from mahjong.core.player_state import Wind
from mahjong.core.tile import Tile, ALL_TILES_136, make_tiles_from_string
from mahjong.player.base import GameView, OpponentView
from mahjong.player.ai_features import extract_public_features


def make_test_game_view(hand_str="123m456p789s東南西白", dora_str="1m", opponents=None, is_sanma=False):
    hand = Hand()
    for t in make_tiles_from_string(hand_str):
        hand.closed_tiles.append(t)
    
    dora_indicators = make_tiles_from_string(dora_str)
    opps = opponents or []
    return GameView(
        my_hand=hand,
        my_seat=0,
        my_wind=Wind.EAST,
        my_score=25000,
        is_dealer=True,
        opponents=opps,
        round_wind=Wind.EAST,
        honba=0,
        riichi_sticks=0,
        remaining_tiles=60,
        dora_indicators=dora_indicators,
    )


def test_dora_and_visibility():
    gv = make_test_game_view(dora_str="1m")
    feat = extract_public_features(gv)
    
    # Dora of 1m is 2m (index34 = 1)
    assert 1 in feat.dora_indices
    # Visible counts of 1m in hand is 1
    assert feat.visible_34[0] >= 1
    # Live counts of 1m is at most 3
    assert feat.live_34[0] <= 3


def test_sanma_visibility():
    gv = make_test_game_view(is_sanma=True, opponents=[
        OpponentView(seat=1, name="P1", score=35000, seat_wind=Wind.SOUTH, is_dealer=False, is_riichi=False, num_closed_tiles=13),
        OpponentView(seat=2, name="P2", score=35000, seat_wind=Wind.WEST, is_dealer=False, is_riichi=False, num_closed_tiles=13),
    ])
    feat = extract_public_features(gv)
    assert feat.is_sanma
    # 2m to 8m (indices 1..7) must have 0 live copies in sanma
    for i in range(1, 8):
        assert feat.live_34[i] == 0


def test_suji_and_kabe_defense_extraction():
    # Opponent discarded 4m from hand before riichi
    opp = OpponentView(
        seat=1, name="Opp1", score=25000, seat_wind=Wind.SOUTH,
        is_dealer=False, is_riichi=True,
        discard_pool=make_tiles_from_string("4m"),
        discard_called=[False],
        discard_is_tsumogiri=[False],
        riichi_discard_index=0,
        num_closed_tiles=13,
    )
    # Give us all 4 copies of 2p in hand to test Kabe
    hand = Hand()
    for t in make_tiles_from_string("2222p123m456s東南西白"):
        hand.closed_tiles.append(t)
    
    gv = GameView(
        my_hand=hand,
        my_seat=0,
        my_wind=Wind.EAST,
        my_score=25000,
        is_dealer=True,
        opponents=[opp],
        round_wind=Wind.EAST,
        honba=0,
        riichi_sticks=0,
        remaining_tiles=50,
        dora_indicators=make_tiles_from_string("1s"),
    )
    
    feat = extract_public_features(gv)
    
    # Genbutsu: 4m (index 3)
    assert 3 in feat.common_genbutsu
    # Suji: 1m (index 0) and 7m (index 6)
    assert 0 in feat.suji_safe
    assert 6 in feat.suji_safe
    assert 0 in feat.tedashi_suji
    assert 6 in feat.tedashi_suji
    
    # Kabe: 2p (index 10) has 4 visible -> 1p (index 9) is No-Chance
    assert 9 in feat.kabe_no_chance


def test_draw_tile_is_not_counted_twice():
    hand = Hand()
    hand.draw(ALL_TILES_136[0])
    gv = make_test_game_view()
    gv.my_hand = hand
    gv.dora_indicators = []

    feat = extract_public_features(gv)

    assert feat.visible_34[0] == 1


def test_sanma_marks_kita_and_removed_tiles_visible():
    gv = make_test_game_view(is_sanma=True, opponents=[
        OpponentView(seat=1, name="P1", score=35000, seat_wind=Wind.SOUTH,
                     is_dealer=False, is_riichi=False),
        OpponentView(seat=2, name="P2", score=35000, seat_wind=Wind.WEST,
                     is_dealer=False, is_riichi=False),
    ])
    gv.my_kita_count = 1

    feat = extract_public_features(gv)

    assert feat.visible_34[30] >= 1
    assert all(feat.live_34[i] == 0 for i in range(1, 8))
