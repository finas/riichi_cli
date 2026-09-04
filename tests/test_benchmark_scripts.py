"""Regression tests for benchmark command parsing and annotations."""

from typing import get_type_hints

from scripts.benchmark_mortal_vs_greedy import (
    play_single_benchmark_game,
    resolve_mortal_cmd,
)


def test_mortal_command_uses_shell_like_argument_parsing(monkeypatch):
    monkeypatch.setenv(
        "MAHJONG_AI_COMMAND",
        'python "engine wrapper.py" --model "Mortal v2"',
    )

    assert resolve_mortal_cmd() == [
        "python", "engine wrapper.py", "--model", "Mortal v2"
    ]


def test_invalid_mortal_command_is_disabled(monkeypatch):
    monkeypatch.setenv("MAHJONG_AI_COMMAND", 'python "unterminated')

    assert resolve_mortal_cmd() is None


def test_benchmark_callback_annotation_resolves():
    hints = get_type_hints(play_single_benchmark_game)

    assert "on_round" in hints
