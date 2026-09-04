"""Tests for Early Hand Route Plan Practice Mode."""

from unittest.mock import MagicMock
from rich.console import Console

from mahjong.analysis.early_plan_practice import (
    EarlyPlanPracticeSession, DrillReport, TurnRecord
)
from mahjong.core.hand import Hand
from mahjong.core.tile import ALL_TILES_136
from mahjong.player.base import GameView


def test_early_plan_candidate_plans():
    """get_candidate_plans should provide diverse strategic choices."""
    console = Console(record=True)
    session = EarlyPlanPracticeSession(console, max_turns=6)

    hand = Hand()
    hand.closed_tiles = [ALL_TILES_136[i * 4] for i in range(13)]
    hand.melds = []
    gv = GameView(
        my_hand=hand,
        my_seat=0,
        my_wind=None,
        round_wind=None,
        my_score=25000,
        is_dealer=True,
        opponents=[],
        remaining_tiles=60,
    )

    plans = session.get_candidate_plans(gv)
    assert 3 <= len(plans) <= 5
    plan_keys = [k for k, _ in plans]
    assert "drill.custom_plan" in plan_keys
    assert "drill.fold_plan" in plan_keys
    assert plan_keys[0] in session._active_plan_keys
    assert any(key in plan_keys for key in (
        "helper.plan_pinfu", "helper.plan_tanyao", "helper.plan_chinitsu",
        "helper.plan_riichi", "helper.plan_menzen",
    ))


def test_best_post_discard_state_uses_thirteen_tile_shanten():
    """The displayed baseline must be measured after a legal discard."""
    console = Console(record=True)
    session = EarlyPlanPracticeSession(console)
    hand = Hand()
    hand.closed_tiles = [
        ALL_TILES_136[i * 4] for i in
        [0, 1, 2, 9, 10, 11, 18, 19, 20, 27, 27, 28, 28, 29]
    ]

    post_shanten, post_counts, discard = session._best_post_discard_state(hand)

    assert sum(post_counts) == 13
    assert post_shanten == 0
    assert discard is not None


def test_early_plan_drill_full_run():
    """run_drill should play 6 turns, record decisions, and generate a report card."""
    console = Console(record=True)
    session = EarlyPlanPracticeSession(console, max_turns=6)

    # Simulated inputs:
    # 1st input: Choose Plan 1
    # Next inputs: Discard tile "14" on each turn
    inputs = ["1", "14", "14", "14", "14", "14", "14", "14", "14"]
    input_iter = iter(inputs)
    input_fn = lambda prompt: next(input_iter)

    report = session.run_drill(input_fn=input_fn)
    assert report is not None
    assert isinstance(report, DrillReport)
    assert report.turns_played >= 1
    assert len(report.records) == report.turns_played
    assert report.concordance_rate >= 0.0
    assert report.initial_shanten >= 0
    assert report.final_shanten >= 0
    assert report.block_status_start != ""
    assert report.block_status_end != ""
    assert report.records[0].draw_tile is not None
    output = console.export_text()
    assert "Mortal" in output or "Greedy fallback" in output
    assert "向听" in output or "向聴" in output or "Shanten" in output


def test_early_plan_drill_handles_eof_cleanly():
    """EOF should leave the drill instead of retrying forever."""
    console = Console(record=True)
    session = EarlyPlanPracticeSession(console)

    def eof(_prompt):
        raise EOFError

    assert session.run_drill(input_fn=eof) is None


def test_early_plan_drill_quit_with_q():
    """Entering 'q' should exit cleanly without raising exceptions."""
    console = Console(record=True)
    session = EarlyPlanPracticeSession(console, max_turns=6)

    report = session.run_drill(input_fn=lambda prompt: "q")
    assert report is None
