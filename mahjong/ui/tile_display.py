"""Tile display formatting with colors, Mahjong Unicode icons, and terminal graphics support."""

import os
import base64
from enum import IntEnum
from rich.text import Text

from mahjong.core.tile import Tile, TileSuit, TILE_NAMES_34


class TileDisplayMode(IntEnum):
    IMAGE = 0  # Real PNG images via Ghostty / Kitty / iTerm2 protocol
    EMOJI = 1  # Unicode Mahjong Icons (🀇 🀈 🀉)
    TEXT = 2   # Classic Short Text (1m 2p 3s)


# Internal short names (stable, language-independent, used by game_logger)
TILE_SHORT_NAMES = [
    "1m", "2m", "3m", "4m", "5m", "6m", "7m", "8m", "9m",
    "1p", "2p", "3p", "4p", "5p", "6p", "7p", "8p", "9p",
    "1s", "2s", "3s", "4s", "5s", "6s", "7s", "8s", "9s",
    "東", "南", "西", "北", "白", "發", "中",
]

# Unicode Mahjong Tiles (U+1F000 - U+1F021)
TILE_UNICODE_34 = [
    # 1m - 9m
    "\U0001F007", "\U0001F008", "\U0001F009", "\U0001F00A", "\U0001F00B", "\U0001F00C", "\U0001F00D", "\U0001F00E", "\U0001F00F",
    # 1p - 9p
    "\U0001F019", "\U0001F01A", "\U0001F01B", "\U0001F01C", "\U0001F01D", "\U0001F01E", "\U0001F01F", "\U0001F020", "\U0001F021",
    # 1s - 9s
    "\U0001F010", "\U0001F011", "\U0001F012", "\U0001F013", "\U0001F014", "\U0001F015", "\U0001F016", "\U0001F017", "\U0001F018",
    # 東 南 西 北
    "\U0001F000", "\U0001F001", "\U0001F002", "\U0001F003",
    # 白 發 中
    "\U0001F006", "\U0001F005", "\U0001F004",
]

# Asset directory for PNG tiles
TILES_ASSET_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "tiles"
)


def get_tile_png_filename(index34: int, is_red: bool = False) -> str:
    """Map 34-index and red dora to PNG filename."""
    if is_red:
        if index34 == 4:
            return "0m.png"
        elif index34 == 13:
            return "0p.png"
        elif index34 == 22:
            return "0s.png"
    if 0 <= index34 <= 8:
        return f"{index34 + 1}.png"
    elif 9 <= index34 <= 17:
        return f"{19 + (index34 - 9)}.png"
    elif 18 <= index34 <= 26:
        return f"{10 + (index34 - 18)}.png"
    elif index34 == 27:
        return "28.png"
    elif index34 == 28:
        return "29.png"
    elif index34 == 29:
        return "30.png"
    elif index34 == 30:
        return "31.png"
    elif index34 == 31:
        # White dragon (白板) is a blank tile.  Keep a visible, full-size
        # placeholder instead of the legacy transparent asset so it occupies
        # the same image footprint as every other tile.
        return "34-white.png"
    elif index34 == 32:
        return "33.png"
    elif index34 == 33:
        return "32.png"
    return "1.png"


def is_graphics_supported() -> bool:
    """Check if current terminal supports inline graphics (Ghostty, Kitty, WezTerm, iTerm2)."""
    term_prog = os.environ.get("TERM_PROGRAM", "").lower()
    term = os.environ.get("TERM", "").lower()
    if "ghostty" in term_prog or os.environ.get("GHOSTTY_RESOURCES_DIR"):
        return True
    if "kitty" in term_prog or "kitty" in term or os.environ.get("KITTY_WINDOW_ID"):
        return True
    if "iterm" in term_prog or "wezterm" in term_prog or "vscode" in term_prog:
        return True
    return False


def detect_default_display_mode() -> TileDisplayMode:
    """Choose graphics only when the terminal positively advertises support."""
    return TileDisplayMode.IMAGE if is_graphics_supported() else TileDisplayMode.TEXT


# Global current display mode
CURRENT_DISPLAY_MODE = detect_default_display_mode()


def get_display_mode() -> TileDisplayMode:
    return CURRENT_DISPLAY_MODE


def set_display_mode(mode: TileDisplayMode):
    global CURRENT_DISPLAY_MODE
    CURRENT_DISPLAY_MODE = mode


# Preload base64 cache for PNG tiles
_PNG_CACHE: dict[str, str] = {}


def _get_png_base64(filename: str) -> str:
    if filename not in _PNG_CACHE:
        path = os.path.join(TILES_ASSET_DIR, filename)
        if os.path.exists(path):
            with open(path, "rb") as f:
                _PNG_CACHE[filename] = base64.b64encode(f.read()).decode("ascii")
        else:
            _PNG_CACHE[filename] = ""
    return _PNG_CACHE[filename]


def tile_to_image_escape(tile: Tile, cols: int = 4, rows: int = 2, no_cursor_move: bool = False) -> str:
    """Generate inline image escape sequence for Ghostty / Kitty / iTerm2."""
    fn = get_tile_png_filename(tile.index34, tile.is_red)
    b64 = _get_png_base64(fn)
    if not b64:
        return tile_to_display_str(tile)

    term_prog = os.environ.get("TERM_PROGRAM", "").lower()
    if "iterm" in term_prog and not ("ghostty" in term_prog or "kitty" in term_prog or os.environ.get("GHOSTTY_RESOURCES_DIR")):
        # iTerm2 protocol
        return f"\x1b]1337;File=inline=1;width={cols};height={rows};preserveAspectRatio=1:{b64}\x07"
    else:
        # Kitty Graphics Protocol with direct base64 chunking
        # C=1: do not move cursor automatically after rendering
        c_flag = ",C=1" if no_cursor_move else ""
        chunk_size = 4096
        chunks = [b64[i:i+chunk_size] for i in range(0, len(b64), chunk_size)]
        parts = []
        for i, chunk in enumerate(chunks):
            m = 1 if i < len(chunks) - 1 else 0
            if i == 0:
                parts.append(f"\x1b_Ga=T,f=100,c={cols},r={rows}{c_flag},m={m};{chunk}\x1b\\")
            else:
                parts.append(f"\x1b_Gm={m};{chunk}\x1b\\")
        return "".join(parts)


def get_tile_short_names() -> list:
    """Get localized tile short names for display.

    Number tiles (1m-9s) are universal. Honor tiles are translated.
    """
    from mahjong.ui.i18n import t
    return [
        "1m", "2m", "3m", "4m", "5m", "6m", "7m", "8m", "9m",
        "1p", "2p", "3p", "4p", "5p", "6p", "7p", "8p", "9p",
        "1s", "2s", "3s", "4s", "5s", "6s", "7s", "8s", "9s",
        t("tile.east"), t("tile.south"), t("tile.west"), t("tile.north"),
        t("tile.haku"), t("tile.hatsu"), t("tile.chun"),
    ]


# Color schemes
SUIT_COLORS = {
    TileSuit.MAN: "red",
    TileSuit.PIN: "blue",
    TileSuit.SOU: "green",
    TileSuit.WIND: "yellow",
    TileSuit.DRAGON: "yellow",
}


def _tile_display_width(name: str) -> int:
    """Calculate the display width of a tile name, accounting for fullwidth and emoji chars."""
    w = 0
    for ch in name:
        if ('\u4e00' <= ch <= '\u9fff' or '\u3000' <= ch <= '\u30ff'
                or '\uff00' <= ch <= '\uffef' or '\U0001F000' <= ch <= '\U0001F02F'):
            w += 2  # Fullwidth or Mahjong Unicode character
        else:
            w += 1
    return w


def tile_to_rich_text(tile: Tile, highlight: bool = False) -> Text:
    """Convert a tile to a Rich Text object with appropriate colors and Mahjong icon."""
    mode = get_display_mode()
    if mode == TileDisplayMode.TEXT:
        names = get_tile_short_names()
        if tile.is_red:
            suit_char = {TileSuit.MAN: 'm', TileSuit.PIN: 'p', TileSuit.SOU: 's'}
            name = f"0{suit_char[tile.suit]}"
            style = "bold dark_orange on white" if highlight else "bold dark_orange"
        else:
            name = names[tile.index34]
            if tile.index34 == 31:
                color = "bright_white"
            elif tile.index34 == 32:
                color = "green"
            elif tile.index34 == 33:
                color = "red"
            else:
                color = SUIT_COLORS[tile.suit]
            style = f"bold {color} on white" if highlight else f"bold {color}"
        return Text(f"[{name}]", style=style)

    icon = TILE_UNICODE_34[tile.index34]
    if tile.is_red:
        style = "bold dark_orange on white" if highlight else "bold dark_orange"
    else:
        if tile.index34 == 31:
            color = "bright_white"
        elif tile.index34 == 32:
            color = "green"
        elif tile.index34 == 33:
            color = "red"
        else:
            color = SUIT_COLORS[tile.suit]
        style = f"bold {color} on white" if highlight else f"bold {color}"

    # Pad with space when highlighted so background color spans full 2-cell terminal width
    text_content = f"{icon} " if highlight else icon
    return Text(text_content, style=style)

def tile_to_rich_markup(tile: Tile) -> str:
    """Format a tile with its full color and styling as a Rich markup string."""
    rt = tile_to_rich_text(tile)
    style = str(rt.style) if rt.style else "bold"
    return f"[{style}]{rt.plain}[/{style}]"



def tile_to_simple_str(tile: Tile) -> str:
    """Simple string representation of a tile (stable, for logging)."""
    if tile.is_red:
        suit_char = {TileSuit.MAN: 'm', TileSuit.PIN: 'p', TileSuit.SOU: 's'}
        return f"0{suit_char[tile.suit]}"
    return TILE_SHORT_NAMES[tile.index34]


def tile_to_display_str(tile: Tile) -> str:
    """Icon representation of a tile (for UI display)."""
    mode = get_display_mode()
    if mode == TileDisplayMode.TEXT:
        names = get_tile_short_names()
        if tile.is_red:
            suit_char = {TileSuit.MAN: 'm', TileSuit.PIN: 'p', TileSuit.SOU: 's'}
            return f"0{suit_char[tile.suit]}"
        return names[tile.index34]
    return TILE_UNICODE_34[tile.index34]


def tiles_to_rich_text(tiles: list, separator: str = " ") -> Text:
    """Convert a list of tiles to Rich Text."""
    result = Text()
    for i, tile in enumerate(tiles):
        if i > 0:
            result.append(separator)
        result.append_text(tile_to_rich_text(tile))
    return result


def format_discard_pool(tiles: list, called: list = None,
                        riichi_index: int = -1,
                        highlight_last: bool = False) -> Text:
    """Format a discard pool with called tiles marked."""
    result = Text()
    last_idx = len(tiles) - 1
    for i, tile in enumerate(tiles):
        if i > 0:
            result.append(" ")
        is_target = highlight_last and i == last_idx
        t = tile_to_rich_text(tile, highlight=is_target)
        if called and i < len(called) and called[i]:
            t.stylize("dim")
        if i == riichi_index:
            t = Text("(", style="bold") + t + Text(")", style="bold")
        result.append_text(t)
    return result
