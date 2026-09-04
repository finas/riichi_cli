"""Unit tests for sound effects and tile voice announcements."""

import os
from mahjong.core.tile import ALL_TILES_136
from mahjong.ui.sound import (
    AUDIO_DIR, get_tile_audio_filename, play_tile_sound,
    play_action_sound, is_sound_enabled, set_sound_enabled
)


def test_audio_files_exist():
    assert os.path.exists(AUDIO_DIR)
    for i in range(34):
        fn = get_tile_audio_filename(i)
        assert fn != ""
        assert os.path.exists(os.path.join(AUDIO_DIR, fn))


def test_action_sound_files_exist():
    for act in ["chi", "pon", "peng", "kan", "gang", "ankan", "daiminkan", "shouminkan", "tsumo", "zimo", "ron", "hu", "draw", "liuju"]:
        fn = {
            "chi": "chi.mp3",
            "pon": "peng.mp3",
            "peng": "peng.mp3",
            "kan": "gang.mp3",
            "gang": "gang.mp3",
            "ankan": "angang.mp3",
            "daiminkan": "gang.mp3",
            "shouminkan": "gang.mp3",
            "tsumo": "zimo.mp3",
            "zimo": "zimo.mp3",
            "ron": "hu.mp3",
            "hu": "hu.mp3",
            "draw": "liuju.mp3",
            "liuju": "liuju.mp3",
        }[act]
        assert os.path.exists(os.path.join(AUDIO_DIR, fn))


def test_sound_toggle():
    orig = is_sound_enabled()
    set_sound_enabled(False)
    assert not is_sound_enabled()
    set_sound_enabled(True)
    assert is_sound_enabled()
    set_sound_enabled(orig)


def test_tile_audio_mapping():
    # 1m -> tile_1.mp3
    assert get_tile_audio_filename(0) == "tile_1.mp3"
    # 9m -> tile_9.mp3
    assert get_tile_audio_filename(8) == "tile_9.mp3"
    # 1p -> tile_19.mp3
    assert get_tile_audio_filename(9) == "tile_19.mp3"
    # 9p -> tile_27.mp3
    assert get_tile_audio_filename(17) == "tile_27.mp3"
    # 1s -> tile_10.mp3
    assert get_tile_audio_filename(18) == "tile_10.mp3"
    # 9s -> tile_18.mp3
    assert get_tile_audio_filename(26) == "tile_18.mp3"
    # 東, 南, 西, 北 -> tile_28, 29, 30, 31
    assert get_tile_audio_filename(27) == "tile_28.mp3"
    assert get_tile_audio_filename(28) == "tile_29.mp3"
    assert get_tile_audio_filename(29) == "tile_30.mp3"
    assert get_tile_audio_filename(30) == "tile_31.mp3"
    # 白, 發, 中 -> tile_34, 33, 32
    assert get_tile_audio_filename(31) == "tile_34.mp3"
    assert get_tile_audio_filename(32) == "tile_33.mp3"
    assert get_tile_audio_filename(33) == "tile_32.mp3"
