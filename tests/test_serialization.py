"""Unit tests for LAN multiplayer serialization and deserialization."""

from mahjong.core.tile import ALL_TILES_136, Tile, TileSuit
from mahjong.core.meld import Meld, MeldType
from mahjong.core.hand import Hand
from mahjong.core.player_state import Wind
from mahjong.player.base import GameView, OpponentView
from mahjong.engine.action import Action, ActionType, AvailableActions
from mahjong.rules.scoring import ScoreResult
from mahjong.network.serialization import (
    tile_to_id, id_to_tile, tiles_to_ids, ids_to_tiles,
    meld_to_dict, dict_to_meld,
    hand_to_dict, dict_to_hand,
    opponent_view_to_dict, dict_to_opponent_view,
    game_view_to_dict, dict_to_game_view,
    available_actions_to_dict, dict_to_available_actions,
    action_to_dict, dict_to_action,
    score_result_to_dict, dict_to_score_result,
)


def test_tile_serialization():
    for tid in range(136):
        tile = ALL_TILES_136[tid]
        serialized_id = tile_to_id(tile)
        assert serialized_id == tid
        restored = id_to_tile(serialized_id)
        assert restored is tile
        assert restored.is_red == tile.is_red

    assert tile_to_id(None) is None
    assert id_to_tile(None) is None


def test_meld_serialization():
    # Chi
    meld_chi = Meld(
        meld_type=MeldType.CHI,
        tiles=(ALL_TILES_136[0], ALL_TILES_136[4], ALL_TILES_136[8]),
        called_tile=ALL_TILES_136[0],
        from_player=3,
    )
    d = meld_to_dict(meld_chi)
    restored = dict_to_meld(d)
    assert restored.meld_type == MeldType.CHI
    assert len(restored.tiles) == 3
    assert restored.called_tile.id == 0
    assert restored.from_player == 3

    # Ankan
    meld_ankan = Meld(
        meld_type=MeldType.ANKAN,
        tiles=(ALL_TILES_136[12], ALL_TILES_136[13], ALL_TILES_136[14], ALL_TILES_136[15]),
    )
    d = meld_to_dict(meld_ankan)
    restored = dict_to_meld(d)
    assert restored.meld_type == MeldType.ANKAN
    assert len(restored.tiles) == 4
    assert restored.called_tile is None
    assert restored.from_player is None


def test_hand_serialization():
    hand = Hand()
    for tid in [0, 4, 8, 16, 20, 24, 28, 32, 36, 40, 44, 48, 52]:
        hand.closed_tiles.append(ALL_TILES_136[tid])
    hand.draw(ALL_TILES_136[56])
    hand.is_riichi = True
    hand.riichi_discard_index = 2
    hand.discard_pool = [ALL_TILES_136[100], ALL_TILES_136[104]]
    hand.discard_is_tsumogiri = [False, True]
    hand.discard_called = [False, False]

    d = hand_to_dict(hand)
    restored = dict_to_hand(d)

    assert len(restored.closed_tiles) == len(hand.closed_tiles)
    assert restored.draw_tile.id == 56
    assert restored.is_riichi is True
    assert restored.riichi_discard_index == 2
    assert len(restored.discard_pool) == 2
    assert restored.discard_is_tsumogiri == [False, True]


def test_game_view_serialization():
    hand = Hand()
    for tid in [0, 4, 8, 16, 20, 24, 28, 32, 36, 40, 44, 48, 52]:
        hand.closed_tiles.append(ALL_TILES_136[tid])

    opponents = [
        OpponentView(
            seat=1, name="Player 2", score=25000, seat_wind=Wind.SOUTH,
            is_dealer=False, is_riichi=True, melds=[],
            discard_pool=[ALL_TILES_136[100]], discard_called=[False],
            discard_is_tsumogiri=[False],
            num_closed_tiles=13, kita_count=0, riichi_discard_index=0
        )
    ]

    gv = GameView(
        my_hand=hand,
        my_seat=0,
        my_wind=Wind.EAST,
        my_score=25000,
        is_dealer=True,
        opponents=opponents,
        round_wind=Wind.EAST,
        honba=1,
        riichi_sticks=1,
        remaining_tiles=65,
        dora_indicators=[ALL_TILES_136[16]],
        round_label="東1局 1本场",
        last_discard=ALL_TILES_136[100],
        last_discard_player=1,
    )

    d = game_view_to_dict(gv)
    restored = dict_to_game_view(d)

    assert restored.my_seat == 0
    assert restored.my_wind == Wind.EAST
    assert restored.my_score == 25000
    assert restored.is_dealer is True
    assert restored.honba == 1
    assert restored.riichi_sticks == 1
    assert len(restored.opponents) == 1
    assert restored.opponents[0].name == "Player 2"
    assert restored.opponents[0].is_riichi is True
    assert len(restored.dora_indicators) == 1
    assert restored.dora_indicators[0].id == 16
    assert restored.last_discard.id == 100
    assert restored.last_discard_player == 1


def test_available_actions_serialization():
    av = AvailableActions(
        player=2,
        can_tsumo=True,
        can_riichi=True,
        can_ankan=[[ALL_TILES_136[0], ALL_TILES_136[1], ALL_TILES_136[2], ALL_TILES_136[3]]],
        can_discard=[ALL_TILES_136[4], ALL_TILES_136[8]],
        can_ron=False,
        riichi_candidates=[ALL_TILES_136[4]],
    )

    d = available_actions_to_dict(av)
    restored = dict_to_available_actions(d)

    assert restored.player == 2
    assert restored.can_tsumo is True
    assert restored.can_riichi is True
    assert len(restored.can_ankan) == 1
    assert len(restored.can_ankan[0]) == 4
    assert len(restored.can_discard) == 2
    assert len(restored.riichi_candidates) == 1
    assert restored.riichi_candidates[0].id == 4


def test_action_serialization():
    # Discard Action
    act1 = Action(ActionType.DISCARD, player=1, tile=ALL_TILES_136[16])
    d1 = action_to_dict(act1)
    restored1 = dict_to_action(d1)
    assert restored1.action_type == ActionType.DISCARD
    assert restored1.player == 1
    assert restored1.tile.id == 16

    # Riichi Action
    act2 = Action(ActionType.RIICHI, player=0, riichi_discard=ALL_TILES_136[20])
    d2 = action_to_dict(act2)
    restored2 = dict_to_action(d2)
    assert restored2.action_type == ActionType.RIICHI
    assert restored2.riichi_discard.id == 20

    # Chi Action
    meld = Meld(MeldType.CHI, (ALL_TILES_136[0], ALL_TILES_136[4], ALL_TILES_136[8]), called_tile=ALL_TILES_136[0], from_player=3)
    act3 = Action(ActionType.CHI, player=0, meld=meld)
    d3 = action_to_dict(act3)
    restored3 = dict_to_action(d3)
    assert restored3.action_type == ActionType.CHI
    assert restored3.meld.meld_type == MeldType.CHI
    assert len(restored3.meld.tiles) == 3


def test_score_result_serialization():
    sr = ScoreResult(
        yaku=[("立直", 1), ("門前清自摸和", 1), ("断幺九", 1), ("平和", 1)],
        han=4,
        fu=30,
        base_points=2000,
        total_points=7900,
        dealer_payment=2000,
        non_dealer_payment=3900,
        ron_payment=0,
        is_dealer=False,
        is_tsumo=True,
        honba=0,
    )

    d = score_result_to_dict(sr)
    restored = dict_to_score_result(d)

    assert restored.han == 4
    assert restored.fu == 30
    assert len(restored.yaku) == 4
    assert restored.yaku[0] == ("立直", 1)
    assert restored.total_points == 7900
    assert restored.is_tsumo is True
    assert restored.is_dealer is False
