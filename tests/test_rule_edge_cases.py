"""Regression tests for round and game boundary rules."""

from mahjong.core.meld import Meld, MeldType
from mahjong.core.player_state import PlayerState, Wind
from mahjong.core.tile import ALL_TILES_136, make_tiles_from_string
from mahjong.core.wall import Wall
from mahjong.engine.action import Action, ActionType, AvailableActions
from mahjong.engine.event import EventBus
from mahjong.engine.game import GameConfig, GameState
from mahjong.engine.round import RoundResult, RoundState, run_round


def _round(num_players=4, is_sanma=False):
    players = [PlayerState(i, f"P{i}") for i in range(num_players)]
    for i, player in enumerate(players):
        player.seat_wind = Wind(i)
        player.is_dealer = i == 0
    return RoundState(
        players=players,
        wall=Wall(is_sanma=is_sanma),
        round_wind=Wind.EAST,
        honba=0,
        riichi_sticks=0,
        event_bus=EventBus(),
        is_sanma=is_sanma,
    ), players


def test_tonpuu_ends_after_east_even_without_target_score():
    game = GameState(
        GameConfig(num_players=4, is_tonpuu=True, target_score=30000),
        ["A", "B", "C", "D"],
        EventBus(),
    )
    game.round_number = 3
    result = RoundResult(4)

    game.advance_round(result)

    assert game.is_finished
    assert game.round_wind == Wind.EAST


def test_four_wind_abort_requires_a_no_call_round():
    rs, players = _round()
    rs.first_discard_winds = [27, 27, 27, 27]
    assert rs.check_abortive_draw() == "4wind"

    players[1].hand.melds.append(
        Meld(MeldType.PON, tuple(make_tiles_from_string("白白白")))
    )
    assert rs.check_abortive_draw() is None


def test_four_kan_allows_kan_owner_to_win_on_replacement_draw():
    """The fourth kan aborts only after its replacement draw is declined."""

    kan_tile = ALL_TILES_136[0]

    class FakeWall:
        is_empty = False

    class FakeRound:
        num_players = 4
        current_player = 0
        wall = FakeWall()
        is_finished = False
        result = None
        turn_count = 0

        def deal_tiles(self):
            pass

        def process_draw(self, _player):
            return kan_tile

        def clear_temp_furiten(self, _player):
            pass

        def get_draw_actions(self, _player):
            if not hasattr(self, "kan_done"):
                return AvailableActions(
                    player=0,
                    can_ankan=[[kan_tile, kan_tile, kan_tile, kan_tile]],
                )
            return AvailableActions(player=0, can_tsumo=True)

        def process_ankan(self, _player, _tiles):
            self.kan_done = True

        def process_rinshan_draw(self, _player):
            return kan_tile

        def check_abortive_draw(self):
            return "4kan"

        def process_tsumo(self, _player):
            self.tsumo_called = True
            self.result = RoundResult(4)
            self.is_finished = True

    round_state = FakeRound()

    def choose(_player, available):
        if available.can_ankan:
            return Action(ActionType.ANKAN, 0, tile=kan_tile)
        return Action(ActionType.TSUMO, 0)

    result = run_round(round_state, choose)

    assert round_state.tsumo_called
    assert result is not None
    assert not result.is_draw


def test_kita_live_wall_replacement_clears_kan_flags():
    rs, players = _round(num_players=3, is_sanma=True)
    north = make_tiles_from_string("北")[0]
    players[0].hand.closed_tiles = [north]
    players[0].hand.draw_tile = north
    rs.wall.live_wall = [ALL_TILES_136[0], ALL_TILES_136[4]]
    rs.is_rinshan = True
    rs.is_haitei = True

    rs.process_kita(0)
    rs.process_draw(0)

    assert not rs.is_rinshan
    assert not rs.is_haitei
    assert len(players[0].kita_tiles) == 1


def test_riichi_ankan_must_preserve_the_exact_wait_set():
    rs, players = _round()
    player = players[0]
    legal_baseline = make_tiles_from_string("111m234m567p678s2s")
    player.hand.closed_tiles = list(legal_baseline) + [ALL_TILES_136[3]]
    player.hand.draw_tile = ALL_TILES_136[3]
    player.hand.is_riichi = True

    actions = rs.get_draw_actions(0)

    assert actions.can_ankan

    # This tenpai hand waits on 8s before the kan, but the wait disappears
    # after removing the four 1m tiles.
    baseline = make_tiles_from_string("111m23567m678p79s")
    player.hand.closed_tiles = list(baseline) + [ALL_TILES_136[3]]
    player.hand.draw_tile = ALL_TILES_136[3]
    baseline_waiting = rs.riichi_waits[0]
    rs.riichi_waits[0] = {25}
    actions = rs.get_draw_actions(0)

    assert not actions.can_ankan
    rs.riichi_waits[0] = baseline_waiting
