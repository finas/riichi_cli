"""Test Hybrid AI Player."""

from mahjong.core.hand import Hand
from mahjong.core.player_state import Wind
from mahjong.core.tile import ALL_TILES_136, Tile
from mahjong.engine.action import ActionType, AvailableActions
from mahjong.player.base import GameView, OpponentView
from mahjong.player.hybrid_ai import HybridAI


def make_tile(index34: int, is_red: bool = False) -> Tile:
    return ALL_TILES_136[index34 * 4]


def test_hybrid_ai_initialization():
    ai = HybridAI(name="TestHybrid")
    assert ai.name == "TestHybrid"
    assert ai.policy.is_loaded is True or ai.policy.is_loaded is False


def test_hybrid_ai_discard_decision():
    ai = HybridAI(name="TestHybrid")
    tiles = [make_tile(0), make_tile(1), make_tile(2), make_tile(30), make_tile(31)]
    hand = Hand()
    hand.closed_tiles = list(tiles)
    gv = GameView(
        my_hand=hand,
        my_seat=0,
        my_wind=Wind.EAST,
        my_score=25000,
        is_dealer=True,
    )
    available = AvailableActions(player=0, can_discard=tiles)
    action = ai.choose_action(gv, available)
    assert action.action_type == ActionType.DISCARD
    assert action.tile in tiles


def test_hybrid_ai_fold_against_riichi():
    ai = HybridAI(name="TestHybrid")
    # 2-shanten / distant hand (14 tiles)
    tiles = [
        make_tile(0), make_tile(1), make_tile(4), make_tile(7),
        make_tile(9), make_tile(12), make_tile(15),
        make_tile(18), make_tile(21), make_tile(24),
        make_tile(27), make_tile(28), make_tile(29), make_tile(30)
    ]
    hand = Hand()
    hand.closed_tiles = list(tiles)
    opp = OpponentView(
        seat=1,
        name="Opp1",
        score=25000,
        seat_wind=Wind.SOUTH,
        is_dealer=False,
        is_riichi=True,
        discard_pool=[make_tile(27)],  # East is genbutsu
    )
    gv = GameView(
        my_hand=hand,
        my_seat=0,
        my_wind=Wind.EAST,
        my_score=25000,
        is_dealer=True,
        opponents=[opp],
    )
    available = AvailableActions(player=0, can_discard=tiles)
    action = ai.choose_action(gv, available)
    # Should choose East wind (genbutsu safe)
    assert action.tile.index34 == 27
