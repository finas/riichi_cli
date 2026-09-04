"""Unit and integration tests for Google Gemini Coach in MahjongCLI."""

from unittest.mock import MagicMock, patch
import pytest

from mahjong.analysis.gemini_coach import (
    build_gemini_prompt,
    extract_recommended_tile,
    extract_recommended_action,
    get_gemini_advice,
    GeminiCoachPlayer,
    is_gemini_available,
    get_agy_path,
    _ADVICE_CACHE,
    format_gemini_advice_markup,
)
from mahjong.cli import create_game
from mahjong.core.hand import Hand
from mahjong.core.player_state import Wind
from mahjong.core.tile import ALL_TILES_136, Tile
from mahjong.engine.action import Action, ActionType, AvailableActions
from mahjong.player.base import GameView, OpponentView
from mahjong.player.human import HumanPlayer
from mahjong.player.greedy_ai import GreedyAI


def make_tile(index34: int, is_red: bool = False) -> Tile:
    t = ALL_TILES_136[index34 * 4]
    if is_red:
        return Tile(t.id, t.suit, t.value, is_red=True)
    return t


def test_gemini_availability_and_path(monkeypatch):
    monkeypatch.setenv("MAHJONG_AGY_PATH", "/nonexistent/agy")
    assert not is_gemini_available()

    monkeypatch.delenv("MAHJONG_AGY_PATH", raising=False)
    # Checks either system agy or None
    assert is_gemini_available() in (True, False)


def test_build_gemini_prompt():
    hand = Hand()
    tiles = [
        make_tile(0), make_tile(1), make_tile(2),       # 123m
        make_tile(9), make_tile(10), make_tile(11),     # 123p
        make_tile(18), make_tile(19), make_tile(20),    # 123s
        make_tile(27), make_tile(27),                   # East pair
        make_tile(29),                                  # West
    ]
    draw = make_tile(21)                                # 4s
    hand.closed_tiles = list(tiles) + [draw]
    hand.draw_tile = draw

    avail = AvailableActions(player=0, can_discard=list(hand.closed_tiles), can_riichi=True)
    gv = GameView(
        my_hand=hand,
        my_seat=0,
        my_wind=Wind.EAST,
        my_score=25000,
        is_dealer=True,
        round_wind=Wind.EAST,
        honba=0,
        remaining_tiles=65,
        dora_indicators=[make_tile(8)],  # 9m -> 1m dora
        opponents=[
            OpponentView(seat=1, name="AI_1", score=25000, seat_wind=Wind.SOUTH, is_dealer=False, is_riichi=True),
            OpponentView(seat=2, name="AI_2", score=25000, seat_wind=Wind.WEST, is_dealer=False, is_riichi=False),
            OpponentView(seat=3, name="AI_3", score=25000, seat_wind=Wind.NORTH, is_dealer=False, is_riichi=False),
        ]
    )

    prompt_zh = build_gemini_prompt(gv, avail, language="zh")
    assert "你是一位专业的日本立直麻将大师级教练" in prompt_zh
    assert "场风 东风 (Ton)" in prompt_zh
    assert "是否庄家: 是 (庄家)" in prompt_zh
    assert "宝牌指示牌: 9m" in prompt_zh
    assert "1号位 (AI_1)" in prompt_zh

    prompt_en = build_gemini_prompt(gv, avail, language="en")
    assert "You are a professional Japanese Riichi Mahjong coach" in prompt_en
    assert "Round: East (Ton)" in prompt_en
    assert "Dealer: Yes" in prompt_en
    assert "Dora indicators: 9m" in prompt_en
    assert "Seat 1 (AI_1)" in prompt_en


def test_extract_recommended_tile():
    candidates = [
        make_tile(0),   # 1m
        make_tile(18),  # 1s
        make_tile(27),  # East
        make_tile(29),  # West
    ]

    # Test explicit "Discard X"
    resp1 = "1. Recommended Action/Discard: Discard 1m.\n2. Target Yaku: Riichi."
    t1 = extract_recommended_tile(resp1, candidates)
    assert t1 is not None and t1.index34 == 0

    # Test bold markdown with honor tile
    resp2 = "### 1. Recommended Action/Discard\n**Discard 西 (West) and declare Riichi.**\n\n### 2. Plan"
    t2 = extract_recommended_tile(resp2, candidates)
    assert t2 is not None and t2.index34 == 29

    # Test English honor name
    resp3 = "Recommended Discard: East. You should keep simple shapes."
    t3 = extract_recommended_tile(resp3, candidates)

    # Test Chinese discard format
    resp_zh = "1. **建议操作 / 舍牌**: **立直 打 西**。\n2. **目标役种**: 立直 + 役牌东。"
    t_zh = extract_recommended_tile(resp_zh, candidates)
    assert t_zh is not None and t_zh.index34 == 29
    assert t3 is not None and t3.index34 == 27


def test_extract_recommended_action():
    hand = Hand()
    tiles = [make_tile(0), make_tile(1), make_tile(2), make_tile(29)]
    hand.closed_tiles = list(tiles)
    avail = AvailableActions(
        player=0,
        can_discard=list(tiles),
        can_riichi=True,
        riichi_candidates=[make_tile(29)],
        can_tsumo=True,
        can_ron=False,
    )
    gv = GameView(
        my_hand=hand,
        my_seat=0,
        my_wind=Wind.EAST,
        my_score=25000,
        is_dealer=True,
    )

    # 1. Tsumo priority
    act_tsumo = extract_recommended_action("Call Tsumo to win.", avail, gv)
    assert act_tsumo.action_type == ActionType.TSUMO

    # 2. Riichi action
    avail_no_tsumo = AvailableActions(
        player=0,
        can_discard=list(tiles),
        can_riichi=True,
        riichi_candidates=[make_tile(29)],
    )
    act_riichi = extract_recommended_action(
        "Discard 西 and declare Riichi.", avail_no_tsumo, gv
    )
    assert act_riichi.action_type == ActionType.RIICHI
    assert act_riichi.tile.index34 == 29

    # 3. Plain discard
    act_discard = extract_recommended_action(
        "Discard 1m for tile efficiency.", avail_no_tsumo, gv
    )
    assert act_discard.action_type == ActionType.DISCARD
    assert act_discard.tile.index34 == 0


def test_get_gemini_advice_caching():
    _ADVICE_CACHE.clear()
    hand = Hand()
    hand.closed_tiles = [make_tile(0), make_tile(1)]
    avail = AvailableActions(player=0, can_discard=list(hand.closed_tiles))
    gv = GameView(
        my_hand=hand,
        my_seat=0,
        my_wind=Wind.EAST,
        my_score=25000,
        is_dealer=True,
        remaining_tiles=60,
    )

    with patch("mahjong.analysis.gemini_coach.is_gemini_available", return_value=True):
        with patch("mahjong.analysis.gemini_coach.query_gemini", return_value="Great advice") as mock_query:
            adv1 = get_gemini_advice(gv, avail)
            adv2 = get_gemini_advice(gv, avail)
            assert adv1 == "Great advice"
            assert adv2 == "Great advice"
            # Should be called only once due to cache
            assert mock_query.call_count == 1


def test_gemini_coach_player():
    hand = Hand()
    tiles = [make_tile(0), make_tile(1), make_tile(2), make_tile(29)]
    hand.closed_tiles = list(tiles)
    gv = GameView(
        my_hand=hand,
        my_seat=0,
        my_wind=Wind.EAST,
        my_score=25000,
        is_dealer=True,
    )
    avail_discard = AvailableActions(player=0, can_discard=list(tiles))

    # 1. On-demand mode (default): uses instant local fallback on routine turns
    default_player = GeminiCoachPlayer(name="TestDefault", auto_query=False)
    act_default = default_player.choose_action(gv, avail_discard)
    assert act_default.action_type == ActionType.DISCARD
    assert default_player.get_last_action_source() == "fallback"
    default_player.close()

    # 2. Auto-query mode: queries Gemini
    player = GeminiCoachPlayer(name="TestGemini", auto_query=True)

    # Immediate winning action
    avail_tsumo = AvailableActions(player=0, can_tsumo=True)
    act = player.choose_action(gv, avail_tsumo)
    assert act.action_type == ActionType.TSUMO
    assert player.get_last_action_source() == "gemini"

    # Action via Gemini query
    avail_discard = AvailableActions(player=0, can_discard=list(tiles))
    with patch("mahjong.analysis.gemini_coach.is_gemini_available", return_value=True):
        with patch(
            "mahjong.analysis.gemini_coach.query_gemini",
            return_value="Discard 西 for defensive reasons.",
        ):
            act = player.choose_action(gv, avail_discard)
            assert act.action_type == ActionType.DISCARD
            assert act.tile.index34 == 29
            assert player.get_last_action_source() == "external"
            assert "Discard 西" in player.get_last_advice()

    # Fallback to GreedyAI on query failure
    with patch("mahjong.analysis.gemini_coach.is_gemini_available", return_value=True):
        with patch("mahjong.analysis.gemini_coach.query_gemini", return_value=None):
            act = player.choose_action(gv, avail_discard)
            assert act.action_type == ActionType.DISCARD
            assert player.get_last_action_source() == "fallback"

    player.close()


def test_learning_mode_creation_with_gemini_coach(monkeypatch):
    """Mode 9 with coach_override='gemini' should create a 4-player game with Gemini coach."""
    monkeypatch.setenv("MAHJONG_GEMINI_MODEL", "gemini-3.8-flash-low")

    config, event_bus, renderer, names, players = create_game(
        choice=9, coach_override="gemini"
    )

    assert config.num_players == 4
    assert renderer.helper_panels_visible is True

    # Seat 0 is human with Gemini coach attached
    human = players[0]
    assert isinstance(human, HumanPlayer)
    assert isinstance(human.coach, GeminiCoachPlayer)
    assert "Google Gemini [gemini-3.8-flash-low]" in human.coach_name

    # Seats 1, 2, 3 are native AI players
    assert len(players) == 4
    assert all(not isinstance(p, HumanPlayer) for p in players[1:])
    assert all(isinstance(p, GreedyAI) for p in players[1:])

    human.close()


def test_learning_mode_creation_with_native_coach():
    """Mode 9 with coach_override='native' should attach GreedyAI coach."""
    config, event_bus, renderer, names, players = create_game(
        choice=9, coach_override="native"
    )

    human = players[0]
    assert isinstance(human, HumanPlayer)
    assert isinstance(human.coach, GreedyAI)
    assert human.coach_name == "Native Heuristic"

    human.close()


def test_input_handler_gemini_key():
    """Typing 'g' at the discard prompt should consult Gemini, then continue loop."""
    from rich.console import Console
    from mahjong.ui.input_handler import get_player_input

    test_console = Console(record=True, width=100)
    hand = Hand()
    tiles = [make_tile(0), make_tile(1)]
    hand.closed_tiles = list(tiles)
    avail = AvailableActions(player=0, can_discard=list(tiles))
    gv = GameView(
        my_hand=hand,
        my_seat=0,
        my_wind=Wind.EAST,
        my_score=25000,
        is_dealer=True,
    )

    # Return 'g' first (consult Gemini), then '1' (discard first tile)
    inputs = ["g", "1"]
    def mock_timed_input(prompt, deadline=None, base_end=None, instant_keys=None):
        return inputs.pop(0)

    with patch("mahjong.ui.input_handler.timed_input", side_effect=mock_timed_input):
        with patch("mahjong.ui.input_handler._consult_and_render_gemini") as mock_gemini:
            action = get_player_input(test_console, gv, avail)
            assert mock_gemini.call_count == 1
            assert action.action_type == ActionType.DISCARD
            assert action.tile.index34 == 0


def test_format_gemini_advice_markup():
    raw_md = (
        "1. **建议操作 / 舍牌:** 打 發。\n"
        "2. **目标役种:** 平和。\n"
        "3. **战术理由:** `123m` 完整。"
    )
    formatted = format_gemini_advice_markup(raw_md)
    # Raw double asterisks should be converted
    assert "**" not in formatted
    assert "[bold cyan]建议操作 / 舍牌:[/bold cyan]" in formatted
    assert "[bold yellow]1.[/bold yellow]" in formatted
    assert "[yellow]123m[/yellow]" in formatted


def test_call_decision_prompt_and_extraction():
    """Verify Chi/Pon/Kan decision prompt details and action extraction."""
    from mahjong.core.meld import Meld, MeldType

    hand = Hand()
    tiles = [make_tile(2), make_tile(3), make_tile(22), make_tile(22)]
    hand.closed_tiles = list(tiles)
    last_discard = make_tile(4)  # 5m

    chi_meld = Meld(MeldType.CHI, (make_tile(2), make_tile(3), make_tile(4)), called_tile=last_discard, from_player=3)
    pon_meld = Meld(MeldType.PON, (make_tile(22), make_tile(22), make_tile(22)), called_tile=make_tile(22), from_player=2)

    avail = AvailableActions(
        player=0,
        can_chi=[chi_meld],
        can_pon=[pon_meld],
        can_discard=[],
    )
    gv = GameView(
        my_hand=hand,
        my_seat=0,
        my_wind=Wind.EAST,
        my_score=25000,
        is_dealer=True,
        last_discard=last_discard,
        last_discard_player=3,
    )

    prompt = build_gemini_prompt(gv, avail, language="zh")
    assert "【当前鸣牌（副露）决策点】:" in prompt
    assert "对手打出的牌:" in prompt
    assert "可选吃牌: 吃 3m 4m" in prompt
    assert "可选碰牌: 碰" in prompt
    assert "跳过/不鸣保持门前清" in prompt

    # Test Skip
    act_skip = extract_recommended_action("建议操作: 跳过保持门清。", avail, gv)
    assert act_skip.action_type == ActionType.SKIP

    # Test Chi
    act_chi = extract_recommended_action("建议操作: 吃 345m 快速进听。", avail, gv)
    assert act_chi.action_type == ActionType.CHI
    assert act_chi.meld == chi_meld

    # Test Pon
    act_pon = extract_recommended_action("建议操作: 碰 5s 确立对子刻子化。", avail, gv)
    assert act_pon.action_type == ActionType.PON
    assert act_pon.meld == pon_meld

def test_defense_awareness_native_and_gemini():
    """Verify defense posture and safe tiles radar in both native coach and Gemini prompt."""
    from mahjong.analysis.coach import AICoach

    hand = Hand()
    tiles = [
        make_tile(0), make_tile(1), make_tile(4),   # 1m 2m 5m
        make_tile(13), make_tile(14),               # 5p 6p
        make_tile(22), make_tile(23),               # 5s 6s
        make_tile(27),                              # East
        make_tile(31),                              # White
    ]
    hand.closed_tiles = list(tiles)

    # Opponent 1 declared Riichi, discarded East (27)
    opp1 = OpponentView(
        seat=1, name="AI_1", score=25000, seat_wind=Wind.SOUTH,
        is_dealer=False, is_riichi=True, discard_pool=[make_tile(27), make_tile(9)]
    )
    gv = GameView(
        my_hand=hand,
        my_seat=0,
        my_wind=Wind.EAST,
        my_score=25000,
        is_dealer=True,
        opponents=[opp1]
    )
    avail = AvailableActions(player=0, can_discard=list(tiles))

    # 1. Native coach suggestion should identify fold stance and East as genbutsu
    coach = AICoach()
    sugg = coach.get_coach_suggestion(gv, avail)
    assert sugg is not None
    assert sugg.tactical_stance == "fold"
    assert any(kind == "genbutsu" and t.index34 == 27 for t, kind in sugg.safe_tiles)

    # 2. Gemini prompt should contain the defense radar and fold recommendation
    prompt = build_gemini_prompt(gv, avail, language="zh")
    assert "【立直威胁与防守安全牌态势】:" in prompt
    assert "危险警报" in prompt
    assert "手牌对立直绝对安全牌 (现物 100%安全): 東" in prompt
    assert "强烈建议【彻底弃和】" in prompt
