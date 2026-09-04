"""Tests for 5-Block Hand Planning Engine and Tactical Yaku Routes."""

from mahjong.core.hand import Hand
from mahjong.core.player_state import Wind
from mahjong.core.tile import ALL_TILES_136, Tile
from mahjong.player.advisor import DiscardAdvice, analyze_5_blocks, potential_yaku_names
from mahjong.player.base import GameView


def make_tile(index34: int, is_red: bool = False) -> Tile:
    return ALL_TILES_136[index34 * 4]


def _build_view(tiles: list[Tile], is_menzen: bool = True, dora_indicators: list[Tile] = None) -> tuple[GameView, DiscardAdvice]:
    hand = Hand()
    hand.closed_tiles = list(tiles)
    counts = [0] * 34
    for t in tiles:
        counts[t.index34] += 1
    hand.to_34_array = lambda: list(counts)
    if not is_menzen:
        from mahjong.core.meld import Meld, MeldType
        hand.melds = [Meld(meld_type=MeldType.PON, tiles=[make_tile(0)]*3, from_player=1)]
    else:
        hand.melds = []

    gv = GameView(
        my_hand=hand,
        my_seat=0,
        my_wind=Wind.EAST,
        my_score=25000,
        is_dealer=True,
        dora_indicators=dora_indicators or [make_tile(0)],
    )
    advice = DiscardAdvice(tile=tiles[-1], shanten=1, ukeire=10, dora=0, danger=0, shape=0)
    return gv, advice


def test_5_block_analysis():
    """analyze_5_blocks should detect 5-blocks locked, surplus, and sparse status."""
    # 1. Five blocks locked: 123m (1), 456p (1), 789s (1), 23s (1), 55m (1) = 5 blocks
    counts = [0] * 34
    for i in [0, 1, 2,  12, 13, 14,  24, 25, 26,  19, 20,  4, 4]:
        counts[i] += 1
    blocks = analyze_5_blocks(counts)
    assert blocks.total_blocks == 5
    assert blocks.status_key == "helper.blocks_fixed"

    # 2. Surplus blocks (6+ blocks): 12m, 45m, 78m, 12p, 45p, 78s, 55s = 7 blocks
    counts_surplus = [0] * 34
    for i in [0, 1, 3, 4, 6, 7, 9, 10, 12, 13, 24, 25, 22, 22]:
        counts_surplus[i] += 1
    blocks_surplus = analyze_5_blocks(counts_surplus)
    assert blocks_surplus.total_blocks >= 6
    assert blocks_surplus.status_key == "helper.blocks_surplus"

    # 3. Sparse blocks (< 5 blocks): scattered floating tiles
    counts_sparse = [0] * 34
    for i in [0, 4, 8, 9, 13, 17, 18, 22, 26, 27, 28, 29, 30]:
        counts_sparse[i] += 1
    blocks_sparse = analyze_5_blocks(counts_sparse)
    assert blocks_sparse.total_blocks < 5
    assert blocks_sparse.status_key == "helper.blocks_sparse"


def test_pinfu_hand_route():
    """Closed sequence-oriented hand without yakuhai pair should identify Pinfu line."""
    # 234m, 345p, 678s, 23s, 55m (discard 1m)
    tiles = [
        make_tile(1), make_tile(2), make_tile(3),
        make_tile(11), make_tile(12), make_tile(13),
        make_tile(23), make_tile(24), make_tile(25),
        make_tile(19), make_tile(20),
        make_tile(4), make_tile(4),
        make_tile(0),  # discard
    ]
    gv, advice = _build_view(tiles)
    plans = potential_yaku_names(gv, advice)
    assert "helper.plan_pinfu" in plans
    assert "helper.blocks_fixed" in plans


def test_tanyao_hand_route():
    """Hand made entirely of simples (2-8) should identify Tanyao line."""
    # 234m, 345p, 456s, 678s, 55p
    tiles = [
        make_tile(1), make_tile(2), make_tile(3),
        make_tile(11), make_tile(12), make_tile(13),
        make_tile(21), make_tile(22), make_tile(23),
        make_tile(23), make_tile(24), make_tile(25),
        make_tile(13), make_tile(13),
        make_tile(0),  # discard 1m
    ]
    gv, advice = _build_view(tiles)
    plans = potential_yaku_names(gv, advice)
    assert "helper.plan_tanyao" in plans


def test_chiitoi_hand_route():
    """Closed hand with 4+ pairs should identify Chiitoitsu line."""
    # 11m, 33m, 55p, 77p, 22s, 8s, White (discard White)
    tiles = [
        make_tile(0), make_tile(0),
        make_tile(2), make_tile(2),
        make_tile(13), make_tile(13),
        make_tile(15), make_tile(15),
        make_tile(19), make_tile(19),
        make_tile(25), make_tile(31),
        make_tile(31),  # discard White
    ]
    gv, advice = _build_view(tiles)
    plans = potential_yaku_names(gv, advice)
    assert "helper.plan_chiitoi" in plans


def test_honitsu_and_chinitsu_routes():
    """Dominant suit hands should identify Honitsu or Chinitsu routes."""
    # 1. Chinitsu (9+ of same suit, 0 honors)
    tiles_chinitsu = [
        make_tile(18), make_tile(19), make_tile(20),  # 123s
        make_tile(21), make_tile(22), make_tile(23),  # 456s
        make_tile(24), make_tile(25), make_tile(26),  # 789s
        make_tile(19), make_tile(20),                 # 23s
        make_tile(22), make_tile(22),                 # 55s
        make_tile(0),                                 # discard 1m
    ]
    gv, advice = _build_view(tiles_chinitsu)
    plans = potential_yaku_names(gv, advice)
    assert "helper.plan_chinitsu" in plans

    # 2. Honitsu (6+ suit + honors >= 9 total)
    tiles_honitsu = [
        make_tile(18), make_tile(19), make_tile(20),  # 123s
        make_tile(21), make_tile(22), make_tile(23),  # 456s
        make_tile(31), make_tile(31), make_tile(31),  # White x3
        make_tile(32), make_tile(32),                 # Green x2
        make_tile(25), make_tile(26),                 # 89s
        make_tile(0),                                 # discard 1m
    ]
    gv2, advice2 = _build_view(tiles_honitsu)
    plans2 = potential_yaku_names(gv2, advice2)
    assert "helper.plan_honitsu" in plans2
    assert "helper.plan_open_yakuhai" in plans2


def test_sanshoku_and_ittsu_routes():
    """Hands with triple sequences or full suit straights should identify Sanshoku/Ittsu."""
    # 1. Sanshoku: 234m, 234p, 234s
    tiles_sanshoku = [
        make_tile(1), make_tile(2), make_tile(3),      # 234m
        make_tile(10), make_tile(11), make_tile(12),   # 234p
        make_tile(19), make_tile(20), make_tile(21),   # 234s
        make_tile(24), make_tile(25),                  # 78s
        make_tile(4), make_tile(4),                    # 55m
        make_tile(33),                                 # discard Red dragon
    ]
    gv, advice = _build_view(tiles_sanshoku)
    plans = potential_yaku_names(gv, advice)
    assert "helper.plan_sanshoku" in plans

    # 2. Ittsu: 123s, 456s, 789s
    tiles_ittsu = [
        make_tile(18), make_tile(19), make_tile(20),   # 123s
        make_tile(21), make_tile(22), make_tile(23),   # 456s
        make_tile(24), make_tile(25), make_tile(26),   # 789s
        make_tile(1), make_tile(2),                    # 23m
        make_tile(13), make_tile(13),                  # 55p
        make_tile(33),                                 # discard Red dragon
    ]
    gv2, advice2 = _build_view(tiles_ittsu)
    plans2 = potential_yaku_names(gv2, advice2)
    assert "helper.plan_ittsu" in plans2
