"""Regression tests for terminal defaults, resource paths, and logging."""

import os

from mahjong.engine.game_logger import GameLogger
from mahjong.ui import tile_display


def test_plain_terminal_defaults_to_text(monkeypatch):
    for key in ("TERM_PROGRAM", "TERM", "GHOSTTY_RESOURCES_DIR", "KITTY_WINDOW_ID"):
        monkeypatch.delenv(key, raising=False)

    assert tile_display.detect_default_display_mode() == tile_display.TileDisplayMode.TEXT


def test_graphics_terminal_defaults_to_images(monkeypatch):
    monkeypatch.setenv("TERM_PROGRAM", "WezTerm")

    assert tile_display.detect_default_display_mode() == tile_display.TileDisplayMode.IMAGE


def test_logger_accepts_a_writable_runtime_directory(tmp_path):
    logger = GameLogger([], {}, log_dir=str(tmp_path))

    path = logger.save({})

    assert os.path.dirname(path) == str(tmp_path)
    assert os.path.exists(path)


def test_runtime_assets_are_available_from_expected_paths():
    assert os.path.exists(tile_display.TILES_ASSET_DIR)

    from mahjong.ui import sound
    assert os.path.exists(sound.AUDIO_DIR)
