import os
import sys
from io import StringIO
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from rich.console import Console

from mahjong.core.hand import Hand
from mahjong.core.tile import Tile, ALL_TILES_136, make_tiles_from_string
from mahjong.core.meld import Meld, MeldType
from mahjong.engine.action import Action, ActionType, AvailableActions
from mahjong.player.greedy_ai import GreedyAI
from mahjong.player.human import HumanPlayer
from mahjong.player.mjai_player import MjaiPlayer, event_to_mjai
from mahjong.player.base import GameView


def test_event_translation_discard_and_draw():
    tile = make_tiles_from_string("4m")[0]
    assert event_to_mjai("draw", {"player": 2, "tile": tile}, 0) == {
        "type": "tsumo", "actor": 2, "pai": "?"
    }
    assert event_to_mjai("discard", {"player": 2, "tile": tile}, 0)["type"] == "dahai"


def test_event_translation_red_five_and_calls():
    red = make_tiles_from_string("0m")[0]
    assert event_to_mjai("draw", {"player": 0, "tile": red}, 0)["pai"] == "5mr"

    tiles = [Tile(17), Tile(18), Tile(19)]
    meld = Meld(MeldType.PON, tuple(tiles), called_tile=tiles[-1], from_player=2)
    assert event_to_mjai("pon", {"player": 1, "meld": meld}, 1) == {
        "type": "pon", "actor": 1, "target": 2, "pai": "5m",
        "consumed": ["5m", "5m"],
    }

    added = Tile(16)
    kakan = Meld(MeldType.SHOUMINKAN, tuple(tiles) + (added,),
                 called_tile=tiles[-1], from_player=2)
    assert event_to_mjai("kan", {"player": 1, "meld": kakan}, 1) == {
        "type": "kakan", "actor": 1, "pai": "5mr",
        "consumed": ["5m", "5m", "5m"],
    }


def test_event_translation_wins_and_lifecycle():
    assert event_to_mjai("tsumo", {"player": 1}, 1) == {
        "type": "hora", "actor": 1, "target": 1,
    }
    assert event_to_mjai("ron", {"player": 1, "from_player": 3}, 1) == {
        "type": "hora", "actor": 1, "target": 3,
    }
    assert event_to_mjai("dora_reveal", {"tile": make_tiles_from_string("3s")[0]}, 0) == {
        "type": "dora", "dora_marker": "3s",
    }
    assert event_to_mjai("exhaustive_draw", {}, 0) == {"type": "ryukyoku"}
    assert event_to_mjai("round_end", {}, 0) == {"type": "end_kyoku"}
    assert event_to_mjai("game_end", {}, 0) == {"type": "end_game"}


def test_decode_hora_uses_available_win_type():
    player = MjaiPlayer("x", 0, ["missing-command"], GreedyAI("fallback"))
    assert player._decode({"type": "hora"}, AvailableActions(player=0, can_tsumo=True)).action_type == ActionType.TSUMO
    assert player._decode({"type": "hora"}, AvailableActions(player=0, can_ron=True)).action_type == ActionType.RON


def test_decode_valid_discard():
    tile = make_tiles_from_string("4m")[0]
    available = AvailableActions(player=0, can_discard=[tile])
    player = MjaiPlayer("x", 0, ["missing-command"], GreedyAI("fallback"))
    action = player._decode({"type": "dahai", "pai": "4m"}, available)
    assert action is not None
    assert action.action_type == ActionType.DISCARD
    assert action.tile == tile


def test_decode_rejects_unknown_tile():
    tile = make_tiles_from_string("4m")[0]
    available = AvailableActions(player=0, can_discard=[tile])
    player = MjaiPlayer("x", 0, ["missing-command"], GreedyAI("fallback"))
    assert player._decode({"type": "dahai", "pai": "9s"}, available) is None


def test_fallback_clears_stale_mortal_metadata():
    """A failed external engine must not leave old Q/P values on screen."""
    tile = make_tiles_from_string("4m")[0]
    hand = Hand()
    hand.closed_tiles = [tile]
    view = GameView(
        my_hand=hand,
        my_seat=0,
        my_wind=None,
        round_wind=None,
        my_score=25000,
        is_dealer=True,
    )
    available = AvailableActions(player=0, can_discard=[tile])
    player = MjaiPlayer("x", 0, ["missing-command"], GreedyAI("fallback"))
    player.last_evaluations = [{"name": "4m", "q_value": 1.0, "prob_pct": 100.0}]

    action = player.choose_action(view, available)

    assert action.tile == tile
    assert player.get_last_action_source() == "fallback"
    assert player.get_last_evaluations() == []


class _FakeStream:
    def __init__(self, lines=None):
        self.lines = list(lines or [])
        self.writes = []
        self.closed = False

    def readline(self):
        return self.lines.pop(0) if self.lines else ""

    def write(self, value):
        self.writes.append(value)

    def flush(self):
        pass

    def close(self):
        self.closed = True


class _FakeProcess:
    def __init__(self, lines=None):
        self.stdin = _FakeStream()
        self.stdout = _FakeStream(lines)
        self.killed = False
        self.waited = False

    def poll(self):
        return None if not self.killed else -9

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        self.waited = True


def _simple_mjai_view():
    tile = make_tiles_from_string("4m")[0]
    hand = Hand()
    hand.closed_tiles = [tile]
    return GameView(
        my_hand=hand,
        my_seat=0,
        my_wind=None,
        round_wind=None,
        my_score=25000,
        is_dealer=True,
    ), tile


def test_mjai_timeout_disables_and_reaps_external_process(monkeypatch):
    from mahjong.player import mjai_player as mjai_module

    view, tile = _simple_mjai_view()
    available = AvailableActions(player=0, can_discard=[tile])
    player = MjaiPlayer("x", 0, ["fake"], GreedyAI("fallback"), response_timeout=0.01)
    process = _FakeProcess()
    player.process = process
    monkeypatch.setattr(player, "start", lambda: True)
    monkeypatch.setattr(mjai_module.select, "select", lambda *_args: ([], [], []))

    player.choose_action(view, available)

    assert player._disabled
    assert process.killed
    assert process.waited


def test_mjai_invalid_response_disables_external_process(monkeypatch):
    from mahjong.player import mjai_player as mjai_module

    view, tile = _simple_mjai_view()
    available = AvailableActions(player=0, can_discard=[tile])
    player = MjaiPlayer("x", 0, ["fake"], GreedyAI("fallback"))
    process = _FakeProcess(['{"type":"dahai","pai":"9s"}\n'])
    player.process = process
    monkeypatch.setattr(player, "start", lambda: True)
    monkeypatch.setattr(mjai_module.select, "select", lambda *_args: ([process.stdout], [], []))

    action = player.choose_action(view, available)

    assert action.tile == tile
    assert player._disabled
    assert process.killed


def test_mjai_deferred_reach_ack_waits_for_confirmation(monkeypatch):
    from mahjong.player import mjai_player as mjai_module

    view, tile = _simple_mjai_view()
    available = AvailableActions(
        player=0,
        can_riichi=True,
        riichi_candidates=[tile],
        can_discard=[tile],
    )
    player = MjaiPlayer(
        "x", 0, ["fake"], GreedyAI("fallback"),
        defer_reach_ack=True,
    )
    process = _FakeProcess(['{"type":"reach"}\n'])
    player.process = process
    monkeypatch.setattr(player, "start", lambda: True)
    monkeypatch.setattr(mjai_module.select, "select", lambda *_args: ([process.stdout], [], []))

    action = player.choose_action(view, available)

    assert action.action_type == ActionType.RIICHI
    assert player.has_pending_reach
    assert process.stdin.writes == []

    player._read_response = Mock(return_value={"type": "dahai", "pai": "4m"})
    assert player.accept_pending_reach()
    assert not player.has_pending_reach
    assert '"type": "reach"' in process.stdin.writes[0]


def test_human_only_acknowledges_deferred_reach_after_riichi_choice():
    coach = SimpleNamespace(
        has_pending_reach=True,
        choose_action=Mock(return_value=Action(ActionType.RIICHI, 0)),
        accept_pending_reach=Mock(return_value=True),
        cancel_pending_reach=Mock(),
    )
    renderer = Mock()
    console = Console(file=StringIO())
    tile = make_tiles_from_string("4m")[0]
    hand = Hand()
    hand.closed_tiles = [tile]
    view = GameView(
        my_hand=hand,
        my_seat=0,
        my_wind=None,
        round_wind=None,
        my_score=25000,
        is_dealer=True,
    )
    available = AvailableActions(
        player=0, can_riichi=True, riichi_candidates=[tile], can_discard=[tile]
    )
    human = HumanPlayer("You", console, renderer, coach=coach)
    human._ai_coach = Mock()
    human._ai_coach.get_coach_suggestion.return_value = SimpleNamespace()

    from unittest.mock import patch
    with patch(
        "mahjong.player.human.get_player_input",
        return_value=Action(ActionType.RIICHI, 0, riichi_discard=tile),
    ):
        human.choose_action(view, available)

    coach.accept_pending_reach.assert_called_once_with()
    coach.cancel_pending_reach.assert_not_called()


def test_spectator_selects_bundled_akochan(monkeypatch):
    monkeypatch.delenv("MAHJONG_AI_COMMAND", raising=False)
    monkeypatch.delenv("MAHJONG_AI_BACKEND", raising=False)
    from mahjong.cli import create_game
    _config, _bus, _renderer, _names, players = create_game(5, is_spectator=True)
    assert all(isinstance(player, MjaiPlayer) for player in players)
    assert all(player.oracle for player in players)
    for player in players:
        player.close()


def test_explicit_mortal_selects_bundled_launcher(monkeypatch):
    monkeypatch.delenv("MAHJONG_AI_COMMAND", raising=False)
    monkeypatch.delenv("MAHJONG_AI_DISPLAY_NAME", raising=False)
    monkeypatch.setenv("MAHJONG_AI_BACKEND", "mortal")
    from mahjong.cli import create_game
    _config, _bus, _renderer, names, players = create_game(1)
    assert names[0] == "你"
    assert all("[Mortal]" in name for name in names[1:])
    assert all(isinstance(player, MjaiPlayer) for player in players[1:])
    assert all(player.response_timeout == 30.0 for player in players[1:])
    for player in players[1:]:
        player.close()


def test_external_ai_display_name_can_be_overridden(monkeypatch):
    monkeypatch.setenv("MAHJONG_AI_BACKEND", "mortal")
    monkeypatch.setenv("MAHJONG_AI_COMMAND", "mortal-wrapper")
    monkeypatch.setenv("MAHJONG_AI_DISPLAY_NAME", "Mortal v4")
    from mahjong.cli import create_game
    _config, _bus, _renderer, names, players = create_game(1)
    assert all("[Mortal v4]" in name for name in names[1:])
    for player in players[1:]:
        player.close()


def test_display_name_does_not_label_native_ai(monkeypatch):
    monkeypatch.delenv("MAHJONG_AI_COMMAND", raising=False)
    monkeypatch.setenv("MAHJONG_AI_BACKEND", "native")
    monkeypatch.setenv("MAHJONG_AI_DISPLAY_NAME", "Not active")
    from mahjong.cli import create_game
    _config, _bus, _renderer, names, _players = create_game(1)
    assert all("[Not active]" not in name for name in names)
