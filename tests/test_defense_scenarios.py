"""Unit regression tests for Riichi defense and push/fold scenarios.

Compares BaselineGreedyAI (flawed heuristic baseline) against
the refactored GreedyAI (tenpai push/fold + refined safety models).
"""

import pytest
from mahjong.core.tile import Tile, ALL_TILES_136
from mahjong.core.hand import Hand
from mahjong.core.player_state import PlayerState, Wind
from mahjong.core.meld import Meld, MeldType
from mahjong.engine.action import AvailableActions
from mahjong.player.base import GameView, OpponentView, build_game_view
from mahjong.player.greedy_ai import GreedyAI, BaselineGreedyAI, RefinedGreedyAI
from mahjong.rules.shanten import shanten


def _make_game_view(
    hand_tile_ids: list[int],
    draw_tile_id: int,
    riichi_seats: list[int] = None,
    discards_by_seat: dict[int, list[int]] = None,
    tsumogiri_by_seat: dict[int, list[bool]] = None,
    riichi_discard_idx: dict[int, int] = None,
    remaining_tiles: int = 40,
    dealer_seat: int = 0,
    my_seat: int = 1,
    dora_indicator_ids: list[int] = None,
):
    """Helper to construct a realistic GameView for defensive testing."""
    players = []
    for i in range(4):
        p = PlayerState(seat=i, name=f"P{i}", score=25000)
        p.is_dealer = (i == dealer_seat)
        p.seat_wind = Wind((i - dealer_seat) % 4)
        if riichi_seats and i in riichi_seats:
            p.hand.is_riichi = True
            if riichi_discard_idx and i in riichi_discard_idx:
                p.hand.riichi_discard_index = riichi_discard_idx[i]
            else:
                p.hand.riichi_discard_index = 0
        if discards_by_seat and i in discards_by_seat:
            p.hand.discard_pool = [ALL_TILES_136[tid] for tid in discards_by_seat[i]]
        if tsumogiri_by_seat and i in tsumogiri_by_seat:
            p.hand.discard_is_tsumogiri = list(tsumogiri_by_seat[i])
        players.append(p)

    # Set up own hand
    my_hand = players[my_seat].hand
    my_hand.closed_tiles = [ALL_TILES_136[tid] for tid in hand_tile_ids]
    draw_tile = ALL_TILES_136[draw_tile_id]
    my_hand.closed_tiles.append(draw_tile)
    my_hand.draw_tile = draw_tile
    my_hand.sort_closed()

    dora_indicators = [ALL_TILES_136[tid] for tid in (dora_indicator_ids or [0])]

    gv = build_game_view(
        player_idx=my_seat,
        players=players,
        round_wind=Wind.EAST,
        honba=0,
        riichi_sticks=len(riichi_seats or []),
        remaining_tiles=remaining_tiles,
        dora_indicators=dora_indicators,
        round_label="东1局 0本场",
    )
    return gv


def test_scenario1_tenpai_fold_against_dealer_riichi():
    """Scenario 1: 0-shanten (Tenpai) with 1000-pt bad wait late game against dealer riichi.
    
    Hand:
      234m (3 tiles)
      567p (3 tiles)
      789p (3 tiles)
      11s (pair, 2 tiles)
      24s (kanchan 3s wait, 2 tiles)
      Total 13 tiles, tenpai!
    Draw: 5m (unsuji dangerous tile)
    Genbutsu available in hand: 7p (dealer discarded 7p)
    
    Before refactor (BaselineGreedyAI):
      Blindly pushes: chooses 5m to maintain tenpai!
    After refactor (GreedyAI):
      Folds (betaori): chooses safe 7p (genbutsu) instead of dangerous 5m!
    """
    # 234m: 5, 9, 13
    # 567p: 53, 57, 61
    # 789p: 62, 65, 69
    # 11s: 73, 74
    # 24s: 77, 85
    hand_ids = [5, 9, 13, 53, 57, 61, 62, 65, 69, 73, 74, 77, 85]
    draw_id = 17 # 5m

    # Dealer (seat 0) declared riichi, discarded 7p (60)
    gv = _make_game_view(
        hand_tile_ids=hand_ids,
        draw_tile_id=draw_id,
        riichi_seats=[0],
        dealer_seat=0,
        my_seat=1,
        remaining_tiles=12,
        discards_by_seat={0: [60]}, # 7p is 100% genbutsu
        tsumogiri_by_seat={0: [False]},
    )

    available = AvailableActions(player=1, can_discard=list(gv.my_hand.closed_tiles))
    
    # 1. Baseline AI blindly pushes 5m
    old_ai = BaselineGreedyAI("OldAI")
    old_action = old_ai.choose_action(gv, available)
    assert old_action.tile.index34 == 4, f"Old AI should blindly push 5m (4), got {old_action.tile}"

    # 2. Refactored AI folds and discards safe 7p (genbutsu)
    new_ai = RefinedGreedyAI("NewAI")
    new_action = new_ai.choose_action(gv, available)
    assert new_action.tile.index34 == 15, f"New AI should fold and discard 7p (15), got {new_action.tile}"


def test_scenario2_tedashi_suji_trap_awareness():
    """Scenario 2: Pre-riichi hand-discard (tedashi) suji should NOT be prioritized over safe honors.
    
    Dealer discards 5m from hand on riichi (tedashi).
    2m is a suji to 5m (tedashi suji), but classic 135 -> cut 5 riichi wait 2 trap!
    Hand has: 2m vs 2-seen West (guest wind).
    
    Before refactor:
      Tedashi suji 2/8 gets danger 5, tied or prioritized.
    After refactor:
      Tedashi trap suji 2/8 gets danger 8, safely ranked behind 2-seen honors (danger 4).
    """
    gv = _make_game_view(
        hand_tile_ids=[5, 9, 13, 53, 57, 61, 62, 65, 69, 77, 85, 116, 117], # 116, 117 = West (2 visible)
        draw_tile_id=7, # 2m (id 7)
        riichi_seats=[0],
        dealer_seat=0,
        my_seat=1,
        remaining_tiles=40,
        discards_by_seat={0: [16]}, # 5m discarded
        tsumogiri_by_seat={0: [False]}, # Hand-discard (tedashi)
        riichi_discard_idx={0: 0},
    )

    new_ai = RefinedGreedyAI("NewAI")
    ranked = new_ai._rank_safe_tiles(gv, [Tile(116), Tile(7)])
    
    # West must be ranked safer than 2m (trap risk)
    assert ranked[0].index34 == 29, f"West (29) should be ranked safer than 2m (1), got {ranked[0]}"


def test_scenario3_live_honor_vs_unsuji_28():
    """Scenario 3: 0-seen guest honor (生牌客风) is statistically safer than unsuji 2/8.
    
    Hand has: West (pure live honor, 0 seen on table) and 2m (unsuji 2m).
    
    Before refactor:
      Old danger had live honor at 14 > unsuji 2/8 at 13 (statistical inversion bug).
    After refactor:
      Live guest honor (otakaze) has danger 7, safer than unsuji 2m (danger 12).
    """
    gv = _make_game_view(
        hand_tile_ids=[5, 9, 13, 53, 57, 61, 62, 65, 69, 77, 85, 116, 7], # 116 = West, 7 = 2m
        draw_tile_id=89,
        riichi_seats=[0],
        dealer_seat=0,
        my_seat=1,
        remaining_tiles=40,
        discards_by_seat={0: [60]}, # 7p discarded
        tsumogiri_by_seat={0: [True]},
    )

    new_ai = RefinedGreedyAI("NewAI")
    ranked = new_ai._rank_safe_tiles(gv, [Tile(116), Tile(7)])
    
    # West (index34=29) must be ranked safer than unsuji 2m (index34=1)
    assert ranked[0].index34 == 29, f"Live guest West (29) should be ranked safer than unsuji 2m (1), got {ranked[0]}"


def test_scenario4_unsuji_middle_gradient():
    """Scenario 4: Unsuji 5 is significantly more dangerous than unsuji 3/7.
    
    Before refactor:
      Both 3m and 5m got danger 16 (undifferentiated).
    After refactor:
      Unsuji 5m gets danger 19; unsuji 3m gets danger 15.
      3m is strictly safer than 5m.
    """
    gv = _make_game_view(
        hand_tile_ids=[5, 9, 13, 53, 57, 61, 62, 65, 69, 77, 85, 9, 17], # 9 = 3m, 17 = 5m
        draw_tile_id=89,
        riichi_seats=[0],
        dealer_seat=0,
        my_seat=1,
        remaining_tiles=40,
        discards_by_seat={0: [60]}, # 7p
        tsumogiri_by_seat={0: [True]},
    )

    new_ai = RefinedGreedyAI("NewAI")
    ranked = new_ai._rank_safe_tiles(gv, [Tile(9), Tile(17)])
    
    # 3m (index34=2) must be ranked safer than 5m (index34=4)
    assert ranked[0].index34 == 2, f"Unsuji 3m (2) should be safer than unsuji 5m (4), got {ranked[0]}"


def test_scenario5_kokushi_exception():
    """Scenario 5: 4th honor tile is not 0-danger when Kokushi Musou is live."""
    # Dealer has 0 calls, discarded only 2m, 3p, 4s (kokushi pattern!)
    gv = _make_game_view(
        hand_tile_ids=[5, 9, 13, 53, 57, 61, 62, 65, 69, 77, 85, 108, 109],
        draw_tile_id=110, # East (3 in hand + 1 discarded = 4 visible)
        riichi_seats=[0],
        dealer_seat=0,
        my_seat=1,
        remaining_tiles=40,
        discards_by_seat={0: [5, 40, 75, 80], 2: [111]}, # simple tiles by dealer + 1 East discarded by player 2
    )

    new_ai = RefinedGreedyAI("NewAI")
    score = new_ai._tile_danger_score(Tile(108), gv)
    assert score > 0, f"4th honor should have non-zero danger when Kokushi is possible, got {score}"


def test_scenario6_live_yakuhai_pair_not_discarded_over_suji():
    """Scenario 6: Live Yakuhai pair (0 on table, 2 in hand) must NOT be treated as danger 4.
    
    Hand holds:
      Pair of White Dragons (124, 125 - 0 on table, pure live yakuhai!)
      Suji 2m (7 - dealer discarded 5m tsumogiri, so 2m is safe suji, danger 6)
    
    Bug in old code:
      `seen == 2` gave White Dragon danger 4!
      AI preferred to discard White Dragon (4) over Suji 2m (6), dealing into Shanpon!
    Fixed code:
      Live Yakuhai gets danger 13.
      Suji 2m (danger 6) is ranked much safer than live White Dragon (danger 13).
    """
    gv = _make_game_view(
        hand_tile_ids=[5, 9, 13, 53, 57, 61, 62, 65, 69, 77, 85, 124, 125], # 124, 125 = White Dragon (index34=31)
        draw_tile_id=7, # 2m
        riichi_seats=[0],
        dealer_seat=0,
        my_seat=1,
        remaining_tiles=40,
        discards_by_seat={0: [16]}, # 5m discarded (tsumogiri)
        tsumogiri_by_seat={0: [True]},
        dora_indicator_ids=[108], # East indicator -> South is dora, 2m is non-dora
    )

    new_ai = RefinedGreedyAI("NewAI")
    white_dragon = Tile(124)
    suji_2m = Tile(7)

    wd_score = new_ai._tile_danger_score(white_dragon, gv)
    suji_score = new_ai._tile_danger_score(suji_2m, gv)

    # White dragon must be significantly more dangerous than suji 2m
    assert wd_score >= 12, f"Live yakuhai pair should have danger >= 12, got {wd_score}"
    assert suji_score <= 6, f"Suji 2m should have danger <= 6, got {suji_score}"

    ranked = new_ai._rank_safe_tiles(gv, [white_dragon, suji_2m])
    assert ranked[0].index34 == 1, f"Suji 2m (1) should be chosen before live yakuhai (31), got {ranked[0]}"


def test_scenario7_good_wait_tenpai_pushes_against_riichi():
    """Scenario 7: Good-shape (Ryanmen) tenpai with 3+ live outs should push against riichi.
    
    Hand:
      123m (3 tiles)
      567p (3 tiles)
      789p (3 tiles)
      99s (pair, 2 tiles)
      34s (ryanmen wait on 2-5s, 2 tiles)
    Draw: 5m (unsuji dangerous tile, danger 19)
    Genbutsu available in hand: 7p (dealer discarded 7p)
    Dealer riichi, mid-game (remaining_tiles = 30).
    
    Unlike bad waits (Scenario 1), a 2-sided tenpai has high win-rate (>35%)
    and should push rather than half-heartedly breaking tenpai.
    """
    # 123m: 0, 4, 8
    # 567p: 52, 56, 60
    # 789p: 61, 64, 68
    # 99s: 104, 105
    # 34s: 80, 84 (waiting on 2s and 5s)
    hand_ids = [0, 4, 8, 52, 56, 60, 61, 64, 68, 104, 105, 80, 84]
    draw_id = 17 # 5m

    # Dealer (seat 0) declared riichi, discarded 7p (60)
    gv = _make_game_view(
        hand_tile_ids=hand_ids,
        draw_tile_id=draw_id,
        riichi_seats=[0],
        dealer_seat=0,
        my_seat=1,
        remaining_tiles=30,
        discards_by_seat={0: [60]}, # 7p is 100% genbutsu
        tsumogiri_by_seat={0: [False]},
    )

    available = AvailableActions(player=1, can_discard=list(gv.my_hand.closed_tiles))
    new_ai = RefinedGreedyAI("NewAI")
    new_action = new_ai.choose_action(gv, available)

    # New AI should PUSH 5m to keep good-shape tenpai
    assert new_action.tile.index34 == 4, f"New AI should push 5m (4) on good wait tenpai, got {new_action.tile}"



def test_refined_normalizes_drawn_tile_visibility_only():
    player = PlayerState(seat=0, name="P0")
    player.seat_wind = Wind.EAST
    player.is_dealer = True
    player.hand.draw(Tile(0))
    game_view = build_game_view(
        player_idx=0,
        players=[player],
        round_wind=Wind.EAST,
        honba=0,
        riichi_sticks=0,
        remaining_tiles=70,
        dora_indicators=[],
    )

    baseline_features = BaselineGreedyAI("baseline")._extract_features(game_view)
    refined_features = RefinedGreedyAI("refined")._extract_features(game_view)

    assert baseline_features.visible_34[0] == 1
    assert refined_features.visible_34[0] == 1


def test_refined_riichi_tie_retains_dora():
    hand = Hand()
    hand.closed_tiles = [
        Tile(i) for i in [
            4, 5, 8, 9, 12, 13, 17, 36, 40, 44, 49, 50, 89, 90
        ]
    ]
    game_view = GameView(
        my_hand=hand,
        my_seat=0,
        my_wind=Wind.EAST,
        my_score=25000,
        is_dealer=True,
        opponents=[],
        dora_indicators=[Tile(12)],
    )
    candidates = [Tile(4), Tile(17)]

    assert BaselineGreedyAI("baseline").choose_riichi_discard(
        game_view, candidates
    ).index34 == 4
    assert RefinedGreedyAI("refined").choose_riichi_discard(
        game_view, candidates
    ).index34 == 1


def test_refined_dora_value_uses_actual_dora_tile():
    hand = Hand()
    hand.closed_tiles = [
        Tile(i) for i in [
            0, 4, 8, 17, 20, 24, 60, 64, 68, 72, 76, 80, 53, 54
        ]
    ]
    game_view = GameView(
        my_hand=hand,
        my_seat=0,
        my_wind=Wind.EAST,
        my_score=25000,
        is_dealer=False,
        opponents=[],
        dora_indicators=[Tile(12)],
    )
    discard = Tile(53)
    tenpai = hand.to_34_array()
    tenpai[discard.index34] -= 1
    waits = [13]
    refined = RefinedGreedyAI("refined")

    value = refined._estimate_tenpai_value(
        game_view,
        hand,
        discard,
        tenpai,
        waits,
        refined._extract_features(game_view).visible_34,
    )

    assert value == 2600


def test_refined_folds_open_tenpai_without_yaku():
    hand = Hand()
    hand.closed_tiles = [
        Tile(i) for i in [
            48, 55, 56, 96, 100, 104, 9, 12, 53, 54, 68
        ]
    ]
    hand.melds = [
        Meld(
            MeldType.CHI,
            (Tile(0), Tile(4), Tile(8)),
            Tile(0),
            1,
        )
    ]
    opponent = OpponentView(
        seat=1,
        name="riichi",
        score=25000,
        seat_wind=Wind.SOUTH,
        is_dealer=False,
        is_riichi=True,
    )
    game_view = GameView(
        my_hand=hand,
        my_seat=0,
        my_wind=Wind.EAST,
        my_score=25000,
        is_dealer=False,
        opponents=[opponent],
        dora_indicators=[Tile(48)],
        remaining_tiles=40,
    )
    refined = RefinedGreedyAI("refined")

    assert refined._hand_dora_count(game_view) == 3
    assert not refined._should_push_on_tenpai(
        game_view, list(hand.closed_tiles)
    )
