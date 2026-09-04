"""Audio effects and voice announcements for MahjongCLI."""

import os
import shutil
import subprocess
from typing import Optional

from mahjong.core.tile import Tile


AUDIO_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "audio"
)

# Detect system audio player
_PLAYER_CMD: Optional[list] = None


def _get_audio_player() -> list:
    global _PLAYER_CMD
    if _PLAYER_CMD is not None:
        return _PLAYER_CMD

    if shutil.which("afplay"):  # macOS native
        _PLAYER_CMD = ["afplay"]
    elif shutil.which("paplay"):  # Linux PulseAudio
        _PLAYER_CMD = ["paplay"]
    elif shutil.which("aplay"):   # Linux ALSA
        _PLAYER_CMD = ["aplay"]
    elif shutil.which("ffplay"):  # FFmpeg
        _PLAYER_CMD = ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"]
    else:
        _PLAYER_CMD = []
    return _PLAYER_CMD


_SOUND_ENABLED = True


def is_sound_enabled() -> bool:
    return _SOUND_ENABLED


def set_sound_enabled(enabled: bool):
    global _SOUND_ENABLED
    _SOUND_ENABLED = enabled


def play_sound_file(filename: str):
    """Play an audio file asynchronously in the background (non-blocking)."""
    if not _SOUND_ENABLED:
        return
    player = _get_audio_player()
    if not player:
        return

    filepath = os.path.join(AUDIO_DIR, filename)
    if not os.path.exists(filepath):
        return

    try:
        cmd = player + [filepath]
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def get_tile_audio_filename(index34: int) -> str:
    """Map 34-index to mp3 filename."""
    if 0 <= index34 <= 8:
        return f"tile_{index34 + 1}.mp3"
    elif 9 <= index34 <= 17:
        return f"tile_{19 + (index34 - 9)}.mp3"
    elif 18 <= index34 <= 26:
        return f"tile_{10 + (index34 - 18)}.mp3"
    elif index34 == 27:
        return "tile_28.mp3"
    elif index34 == 28:
        return "tile_29.mp3"
    elif index34 == 29:
        return "tile_30.mp3"
    elif index34 == 30:
        return "tile_31.mp3"
    elif index34 == 31:
        return "tile_34.mp3"
    elif index34 == 32:
        return "tile_33.mp3"
    elif index34 == 33:
        return "tile_32.mp3"
    return ""


def play_tile_sound(tile: Tile):
    """Play voice announcement for a discarded tile."""
    fn = get_tile_audio_filename(tile.index34)
    if fn:
        play_sound_file(fn)


def play_action_sound(action_name: str):
    """Play sound effect for game action ('chi', 'peng', 'gang', 'hu', 'zimo', 'liuju')."""
    sound_map = {
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
        "exhaustive_draw": "liuju.mp3",
        "abortive_draw": "liuju.mp3",
    }
    fn = sound_map.get(action_name.lower())
    if fn:
        play_sound_file(fn)
