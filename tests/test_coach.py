"""Test AI Coach and Move Reviewer."""

from mahjong.analysis.coach import AICoach, MoveRating
from mahjong.core.hand import Hand
from mahjong.core.player_state import Wind
from mahjong.core.tile import ALL_TILES_136, Tile
from mahjong.engine.action import Action, ActionType, AvailableActions
from mahjong.player.base import GameView, OpponentView


def make_tile(index34: int, is_red: bool = False) -> Tile:
    return ALL_TILES_136[index34 * 4]


def test_coach_live_hint():
    coach = AICoach()
    tiles = [
        make_tile(0), make_tile(1), make_tile(2), # 123m
        make_tile(9), make_tile(10), make_tile(11), # 123p
        make_tile(18), make_tile(19), make_tile(20), # 123s
        make_tile(27), make_tile(27), # East East
        make_tile(30), make_tile(31), # North White
    ]
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
    hints = coach.get_live_hint(gv, available)
    assert len(hints) > 0
    # Top discard recommendation should discard guest wind North (30) or isolated White (31)
    top_tile, explanation = hints[0]
    assert top_tile.index34 in (30, 31)
    assert "Shanten" in explanation


def test_coach_review_decision_best():
    coach = AICoach()
    tiles = [
        make_tile(0), make_tile(1), make_tile(2),
        make_tile(9), make_tile(10), make_tile(11),
        make_tile(18), make_tile(19), make_tile(20),
        make_tile(27), make_tile(27),
        make_tile(30), make_tile(31),
    ]
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
    # Player discards guest wind North (optimal)
    chosen_act = Action(ActionType.DISCARD, player=0, tile=make_tile(30))
    review = coach.review_decision(gv, available, chosen_act, turn_number=1)
    assert review.rating in (MoveRating.BEST, MoveRating.GOOD)
    assert review.score_loss_ev <= 500


def test_coach_review_decision_blunder_into_riichi():
    coach = AICoach()
    tiles = [
        make_tile(0), make_tile(1), make_tile(4), make_tile(7),
        make_tile(9), make_tile(12), make_tile(15),
        make_tile(18), make_tile(21), make_tile(24),
        make_tile(27), make_tile(28), make_tile(29), make_tile(4) # 5m dangerous
    ]
    hand = Hand()
    hand.closed_tiles = list(tiles)
    opp = OpponentView(
        seat=1,
        name="RiichiOpp",
        score=25000,
        seat_wind=Wind.SOUTH,
        is_dealer=False,
        is_riichi=True,
        discard_pool=[make_tile(27)], # East is Genbutsu
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
    # Player discards dangerous 5m instead of Genbutsu East
    chosen_act = Action(ActionType.DISCARD, player=0, tile=make_tile(4))
    review = coach.review_decision(gv, available, chosen_act, turn_number=10)
    assert review.rating == MoveRating.BLUNDER
    assert "Riichi" in review.explanation or "deal-in" in review.explanation
