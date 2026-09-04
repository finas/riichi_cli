"""Tests for Learning Mode (Index 9) and Mortal Coach with Possibility."""

from unittest.mock import MagicMock, patch
from rich.console import Console

from mahjong.analysis.coach import AICoach, CoachSuggestion, DiscardCandidateEval
from mahjong.cli import create_game
from mahjong.core.hand import Hand
from mahjong.core.player_state import Wind
from mahjong.core.tile import ALL_TILES_136, Tile
from mahjong.engine.action import Action, ActionType, AvailableActions
from mahjong.player.base import GameView, Player
from mahjong.player.greedy_ai import GreedyAI
from mahjong.player.human import HumanPlayer
from mahjong.player.mjai_player import MjaiPlayer
from mahjong.ui.input_handler import _render_coach_suggestion


def make_tile(index34: int, is_red: bool = False) -> Tile:
    return ALL_TILES_136[index34 * 4]


def test_learning_mode_creation_without_external_cmd(monkeypatch):
    """Mode 9 should create a 4-player game with human at seat 0, AI opponents, and coach attached."""
    monkeypatch.delenv("MAHJONG_COACH_COMMAND", raising=False)
    monkeypatch.delenv("MAHJONG_AI_COMMAND", raising=False)
    monkeypatch.delenv("MAHJONG_AI_BACKEND", raising=False)
    # also simulate no bundled mortal run.sh
    monkeypatch.setattr("os.path.isfile", lambda path: False)

    config, event_bus, renderer, names, players = create_game(9)

    assert config.num_players == 4
    assert not config.is_sanma
    assert not config.is_tonpuu
    assert renderer.helper_panels_visible is True

    # Seat 0 is human with coach attached
    assert names[0] == "你"
    assert isinstance(players[0], HumanPlayer)
    assert players[0].coach is not None
    assert isinstance(players[0].coach, GreedyAI)  # fallback
    assert players[0].coach_name == "Mortal"

    # Seats 1, 2, 3 are AI players
    assert len(players) == 4
    assert all(not isinstance(p, HumanPlayer) for p in players[1:])


def test_learning_mode_creation_with_mortal_coach_cmd(monkeypatch):
    """Mode 9 with custom coach command should bind an MjaiPlayer coach to Seat 0."""
    monkeypatch.setenv("MAHJONG_COACH_COMMAND", "mortal-wrapper")
    monkeypatch.setenv("MAHJONG_COACH_DISPLAY_NAME", "Mortal v4")

    config, event_bus, renderer, names, players = create_game(9)

    human = players[0]
    assert isinstance(human, HumanPlayer)
    assert isinstance(human.coach, MjaiPlayer)
    assert human.coach.seat == 0
    assert human.coach_name == "Mortal v4"

    human.close()


def test_coach_suggestion_with_possibilities():
    """Coach suggestion should calculate win probability, deal-in risk, and alternatives."""
    coach = AICoach()
    tiles = [
        make_tile(0), make_tile(1), make_tile(2),      # 123m
        make_tile(9), make_tile(10), make_tile(11),    # 123p
        make_tile(18), make_tile(19), make_tile(20),   # 123s
        make_tile(27), make_tile(27),                  # East East (pair)
        make_tile(30), make_tile(31),                  # North White (isolated)
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

    suggestion = coach.get_coach_suggestion(gv, available, engine_name="Mortal")
    assert suggestion is not None
    assert suggestion.engine_name == "Greedy fallback"
    assert suggestion.best_tile is not None
    # North (30) or White (31) should be chosen to reach tenpai
    assert suggestion.best_tile.index34 in (30, 31)

    ev = suggestion.best_eval
    assert ev is not None
    assert 0.0 <= ev.win_prob <= 100.0
    assert 0.0 <= ev.deal_in_risk <= 100.0
    assert ev.shanten == 1  # 1-shanten after discarding isolated honor
    assert ev.ukeire > 0

    assert len(suggestion.alternatives) > 0
    for alt in suggestion.alternatives:
        assert 0.0 <= alt.win_prob <= 100.0
        assert 0.0 <= alt.deal_in_risk <= 100.0


def test_coach_suggestion_with_external_ai_override():
    """When an external AI (like Mortal) suggests a move, it becomes the top suggestion."""
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

    # Mock external AI (Mortal) specifically choosing White (31)
    mock_ai = MagicMock()
    mock_ai.choose_action.return_value = Action(ActionType.DISCARD, 0, tile=make_tile(31))

    suggestion = coach.get_coach_suggestion(gv, available, external_ai=mock_ai, engine_name="Mortal")
    assert suggestion is not None
    assert suggestion.best_tile.index34 == 31
    assert suggestion.best_eval.tile.index34 == 31
    assert suggestion.best_eval.win_prob > 0.0


def test_render_coach_suggestion_ui():
    """_render_coach_suggestion should output formatted Rich panel without error."""
    test_console = Console(record=True, width=100)
    tile = make_tile(0)
    eval_info = DiscardCandidateEval(
        tile=tile,
        shanten=1,
        ukeire=12,
        win_prob=35.4,
        deal_in_risk=2.1,
        is_safe=True,
        note="100% Genbutsu Safe",
    )
    alt_eval = DiscardCandidateEval(
        tile=make_tile(9),
        shanten=1,
        ukeire=8,
        win_prob=28.0,
        deal_in_risk=8.5,
    )
    suggestion = CoachSuggestion(
        best_tile=tile,
        best_eval=eval_info,
        alternatives=[alt_eval],
        engine_name="Mortal",
    )

    _render_coach_suggestion(test_console, suggestion)
    output = test_console.export_text()

    assert "Mortal" in output
    assert "35.4%" in output
    assert "2.1%" in output
    assert "12" in output


def test_human_player_queries_coach_without_auto_play():
    """HumanPlayer should query coach for suggestions and pass them to UI, never auto-playing."""
    test_console = Console(record=True)
    renderer_mock = MagicMock()
    tiles = [make_tile(0), make_tile(1), make_tile(2), make_tile(3)]
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

    mock_coach = MagicMock(spec=Player)
    # Mortal suggests tile 0
    mock_coach.choose_action.return_value = Action(ActionType.DISCARD, 0, tile=tiles[0])

    human = HumanPlayer("You", test_console, renderer_mock, coach=mock_coach, coach_name="Mortal")

    # Mock get_player_input: human rejects suggestion (tile 0) and chooses tile 3 instead
    with patch("mahjong.player.human.get_player_input") as mock_input:
        chosen_action = Action(ActionType.DISCARD, 0, tile=tiles[3])
        mock_input.return_value = chosen_action

        result = human.choose_action(gv, available)

        # Verify coach was queried
        mock_coach.choose_action.assert_called_once_with(gv, available)
        # Verify get_player_input received coach_suggestion
        _, kwargs = mock_input.call_args
        assert "coach_suggestion" in kwargs
        assert kwargs["coach_suggestion"].best_tile.index34 == tiles[0].index34
        # Verify Human's own chosen action was returned (not coach's!)
        assert result.tile == tiles[3]


def test_coach_suggests_tsumo_when_available():
    """Coach should recommend Tsumo with 100% win possibility when agari draw is available."""
    coach = AICoach()
    draw_tile = make_tile(0)
    hand = Hand()
    hand.draw_tile = draw_tile
    hand.closed_tiles = [draw_tile]
    gv = GameView(
        my_hand=hand,
        my_seat=0,
        my_wind=Wind.EAST,
        my_score=25000,
        is_dealer=True,
    )
    available = AvailableActions(player=0)
    available.can_tsumo = True
    available.can_discard = [draw_tile]  # In Riichi, must tsumogiri if not tsumo

    suggestion = coach.get_coach_suggestion(gv, available, engine_name="Mortal")
    assert suggestion is not None
    assert suggestion.action is not None
    assert suggestion.action.action_type == ActionType.TSUMO
    assert suggestion.best_eval.win_prob == 100.0
    assert suggestion.best_eval.deal_in_risk == 0.0

    test_console = Console(record=True, width=100)
    _render_coach_suggestion(test_console, suggestion)
    output = test_console.export_text()
    assert "100.0%" in output
    assert "0.0%" in output


def test_coach_suggests_ron_when_available():
    """Coach should recommend Ron with 100% win possibility when opponent discards agari tile."""
    coach = AICoach()
    hand = Hand()
    gv = GameView(
        my_hand=hand,
        my_seat=0,
        my_wind=Wind.EAST,
        my_score=25000,
        is_dealer=True,
        last_discard=make_tile(5),
    )
    available = AvailableActions(player=0)
    available.can_ron = True

    suggestion = coach.get_coach_suggestion(gv, available, engine_name="Mortal")
    assert suggestion is not None
    assert suggestion.action is not None
    assert suggestion.action.action_type == ActionType.RON
    assert suggestion.best_eval.win_prob == 100.0
    assert suggestion.best_eval.deal_in_risk == 0.0


def test_mortal_meta_parsing_and_softmax():
    """MjaiPlayer should unpack mask_bits and compute correct Softmax probabilities."""
    mask_bits = (1 << 27) | (1 << 32)  # East (27) and Green Dragon (32)
    q_values = [0.09, -0.40]
    meta = {"q_values": q_values, "mask_bits": mask_bits}

    evals = MjaiPlayer._parse_mortal_meta(meta, temperature=0.1)
    assert len(evals) == 2
    assert evals[0]["name"] == "E"
    assert evals[0]["q_value"] == 0.09
    assert evals[1]["name"] == "F"
    assert evals[1]["q_value"] == -0.40

    # Softmax check
    assert evals[0]["prob"] > evals[1]["prob"]
    assert abs(evals[0]["prob"] + evals[1]["prob"] - 1.0) < 1e-4
    assert evals[0]["prob_pct"] > 99.0  # (0.09 - (-0.40))/0.1 = 4.9 -> exp(4.9) / (1 + exp(4.9)) ~ 99.2%


def test_coach_suggestion_integrates_mortal_q_and_prob():
    """Coach suggestion should integrate Mortal Q-values, P%, and rank alternatives by Mortal."""
    coach = AICoach()
    tiles = [
        make_tile(0), make_tile(1), make_tile(2),      # 123m
        make_tile(9), make_tile(10), make_tile(11),    # 123p
        make_tile(18), make_tile(19), make_tile(20),   # 123s
        make_tile(25), make_tile(25),                  # 8s 8s
        make_tile(27), make_tile(32),                  # East (27) and Green dragon (32)
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

    mock_mortal = MagicMock()
    mock_mortal.choose_action.return_value = Action(ActionType.DISCARD, 0, tile=make_tile(27))
    mock_mortal.get_last_evaluations.return_value = [
        {"name": "E", "q_value": 0.09, "prob_pct": 99.19},
        {"name": "F", "q_value": -0.40, "prob_pct": 0.80},
        {"name": "8s", "q_value": -0.84, "prob_pct": 0.01},
    ]

    suggestion = coach.get_coach_suggestion(gv, available, external_ai=mock_mortal, engine_name="Mortal")
    assert suggestion is not None
    assert suggestion.best_tile.index34 == 27  # East
    assert suggestion.best_eval.q_value == 0.09
    assert suggestion.best_eval.mortal_prob == 99.19

    # Alternatives should prioritize Mortal Q-values: 1st alt should be Green dragon (32), 2nd 8s (25)
    assert len(suggestion.alternatives) >= 2
    assert suggestion.alternatives[0].tile.index34 == 32  # Green dragon
    assert suggestion.alternatives[0].q_value == -0.40
    assert suggestion.alternatives[0].mortal_prob == 0.80
    assert suggestion.alternatives[1].tile.index34 == 25  # 8s
    assert suggestion.alternatives[1].q_value == -0.84
    assert suggestion.alternatives[1].mortal_prob == 0.01


def test_prob_bar_rendering():
    """Visual probability bars and Q/P metrics should render cleanly in UI."""
    from mahjong.ui.input_handler import _make_prob_bar

    # Test bar helper
    assert len(_make_prob_bar(100.0, width=10)) == 10
    assert _make_prob_bar(0.0, width=10).strip() == ""
    assert "█" in _make_prob_bar(50.0, width=10)

    # Test panel output
    test_console = Console(record=True, width=100)
    eval_info = DiscardCandidateEval(
        tile=make_tile(27),
        shanten=2,
        ukeire=16,
        win_prob=30.0,
        deal_in_risk=0.0,
        is_safe=True,
        q_value=0.09,
        mortal_prob=99.2,
    )
    alt_info = DiscardCandidateEval(
        tile=make_tile(32),
        shanten=2,
        ukeire=16,
        win_prob=28.0,
        deal_in_risk=2.0,
        q_value=-0.40,
        mortal_prob=0.8,
    )
    suggestion = CoachSuggestion(
        best_tile=make_tile(27),
        best_eval=eval_info,
        alternatives=[alt_info],
        engine_name="Mortal",
    )

    _render_coach_suggestion(test_console, suggestion)
    output = test_console.export_text()
    assert "Q: +0.09" in output
    assert "P: 99.2%" in output
    assert "Q: -0.40" in output
    assert "P: 0.8%" in output
    assert "█" in output


def test_spectator_with_mortal_backend_override(monkeypatch):
    """Spectator mode with backend_override='mortal' should instantiate Mortal AI players."""
    monkeypatch.delenv("MAHJONG_AI_COMMAND", raising=False)
    monkeypatch.delenv("MAHJONG_AI_DISPLAY_NAME", raising=False)
    config, event_bus, renderer, names, players = create_game(5, is_spectator=True, backend_override="mortal")
    assert len(players) == 4
    assert all(isinstance(p, MjaiPlayer) for p in players)
    assert all("[Mortal]" in name for name in names)
    for p in players:
        p.close()


def test_spectator_with_native_backend_override(monkeypatch):
    """Spectator mode with backend_override='native' should instantiate native AI players without external command."""
    config, event_bus, renderer, names, players = create_game(5, is_spectator=True, backend_override="native")
    assert len(players) == 4
    assert all(isinstance(p, GreedyAI) for p in players)
    assert all("[Mortal]" not in name and "[Akochan]" not in name for name in names)


def test_choose_spectator_engine_prompt(monkeypatch):
    """_choose_spectator_engine should return appropriate backend based on user input."""
    from mahjong.cli import _choose_spectator_engine
    import sys

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)

    with patch("mahjong.cli.console.input", side_effect=["1", "2", "3"]):
        assert _choose_spectator_engine() == "mortal"
        assert _choose_spectator_engine() == "akochan"
        assert _choose_spectator_engine() == "native"


def test_render_decision_review_on_various_actions():
    """_render_decision_review should execute cleanly on discard, draw, and call steps without NameError."""
    from mahjong.replay.state import RoundReplay
    from mahjong.ui.replay_screen import _render_decision_review

    round_data = {
        "round_wind": "east",
        "wall": {"tile_ids": list(range(136))},
        "honba": 0,
        "initial_hands": {
            "P0": {"seat": 0, "wind": "東", "is_dealer": True, "tiles": ["1m", "2m", "3m", "4m", "5m", "6m", "7m", "8m", "9m", "1p", "2p", "3p", "4p"]},
            "P1": {"seat": 1, "wind": "南", "is_dealer": False, "tiles": ["5p", "5p", "3s", "4s", "5s", "6s", "7s", "8s", "9s", "1p", "2p", "3p", "4p"]},
            "P2": {"seat": 2, "wind": "西", "is_dealer": False, "tiles": ["1m", "2m", "3m", "4m", "5m", "6m", "7m", "8m", "9m", "1s", "2s", "3s", "4s"]},
            "P3": {"seat": 3, "wind": "北", "is_dealer": False, "tiles": ["1m", "2m", "3m", "4m", "5m", "6m", "7m", "8m", "9m", "1p", "2p", "3p", "4p"]},
        },
        "actions": [
            {"action": "draw", "seat": 0, "tile": "5p"},
            {"action": "discard", "seat": 0, "tile": "5p"},
            {"action": "pon", "seat": 1, "tiles": ["5p", "5p", "5p"], "from_seat": 0},
        ],
    }
    player_names = ["P0", "P1", "P2", "P3"]
    rr = RoundReplay(round_data, player_names, is_sanma=False)
    test_console = Console(record=True)
    from mahjong.ui.replay_screen import _active_seat

    # Step 1: Draw (P0 draws)
    rr.goto(1)
    assert _active_seat(rr) == 0
    _render_decision_review(test_console, rr)

    # Step 2: Discard (P0 discarded 5p, so _active_seat is 0 and matches review P0!)
    rr.goto(2)
    assert _active_seat(rr) == 0
    _render_decision_review(test_console, rr)
    output = test_console.export_text()
    assert "实际打牌: 🀝" in output
    assert "更高效的选择是 🀙" in output
    assert "建议打出: 🀙" in output

    # Step 3: Pon (P1 calls pon, so _active_seat is 1)
    rr.goto(3)
    assert _active_seat(rr) == 1
    _render_decision_review(test_console, rr)

def test_main_menu_routes_play_game(monkeypatch):
    """Modes 1-5 and 9 should call play_game properly."""
    from mahjong import cli
    calls = []
    monkeypatch.setattr(cli, "show_menu", MagicMock(side_effect=[1, 2, 3, 4, 5, 9, 0]))
    monkeypatch.setattr(cli, "play_game", lambda choice, tc, delay: calls.append(choice))

    cli.main()
    assert calls == [1, 2, 3, 4, 5, 9]

def test_active_player_arrow_marker_on_board():
    """Active player in live game view should be marked with ▶ arrow."""
    from mahjong.ui.board_layout import _render_all_players
    from mahjong.player.base import GameView, OpponentView
    from mahjong.core.hand import Hand
    from mahjong.core.player_state import Wind

    test_console = Console(record=True)
    hand = Hand()
    gv = GameView(
        my_hand=hand,
        my_seat=0,
        my_wind=Wind.EAST,
        my_score=25000,
        is_dealer=True,
        opponents=[
            OpponentView(seat=1, name="AI-南", score=25000, seat_wind=Wind.SOUTH, is_dealer=False, is_riichi=False),
        ],
        active_player=1,  # AI-南 is currently acting
    )
    _render_all_players(test_console, gv)
    output = test_console.export_text()
    assert "▶" in output
    assert "AI-南" in output
