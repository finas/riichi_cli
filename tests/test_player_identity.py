"""Regression tests for player identity and name-keyed game data."""

import pytest

from mahjong.engine.event import EventBus
from mahjong.engine.game import GameConfig, GameState
from mahjong.engine.game_logger import GameLogger


def test_game_state_rejects_duplicate_names_case_insensitively():
    with pytest.raises(ValueError, match="duplicate player name"):
        GameState(GameConfig(), ["Alice", "alice", "C", "D"], EventBus())


def test_game_logger_rejects_blank_or_duplicate_names(tmp_path):
    with pytest.raises(ValueError, match="non-empty"):
        GameLogger(["Alice", "  ", "C", "D"], {}, log_dir=str(tmp_path))

    with pytest.raises(ValueError, match="duplicate player name"):
        GameLogger(["Alice", "alice", "C", "D"], {}, log_dir=str(tmp_path))
