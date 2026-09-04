"""Interactive replay browser - step through logged games.

Entry point: show_replay_browser(console), wired to the main menu.
God view: all hands are visible (it is a record of a finished game).
"""

import sys

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from mahjong.core.player_state import Wind
from mahjong.core.tile import ALL_TILES_136
from mahjong.replay.loader import GameSummary, list_game_logs, load_game
from mahjong.replay.state import RoundReplay, parse_tile_str
from mahjong.ui.board_layout import _render_melds_line
from mahjong.ui.i18n import t, translate_yaku, get_draw_message
from mahjong.ui.labels import wind_display
from mahjong.ui.tile_display import (
    tile_to_rich_text, tiles_to_rich_text, tile_to_display_str,
    format_discard_pool, get_display_mode, TileDisplayMode,
    tile_to_image_escape, tile_to_simple_str,
)
from mahjong.player.advisor import advise_discards, potential_yaku_names
from mahjong.player.base import GameView, OpponentView

_KANJI_TO_WIND = {"東": Wind.EAST, "南": Wind.SOUTH,
                  "西": Wind.WEST, "北": Wind.NORTH}


def show_replay_browser(console: Console):
    """Top level: pick a logged game, then browse its rounds."""
    while True:
        summaries = list_game_logs()
        if not summaries:
            console.print(f"\n  [yellow]{t('replay.no_logs')}[/yellow]")
            return

        console.print(f"\n  [bold]{t('replay.title')}[/bold]")
        for i, s in enumerate(summaries):
            console.print(f"    {i + 1}. {_game_line(s)}")
        console.print(f"    0. {t('replay.back')}")

        idx = _read_index(console, t('replay.select_game'), len(summaries))
        if idx is None or idx == 0:
            return
        _browse_game(console, summaries[idx - 1])


def _game_line(s: GameSummary) -> str:
    """One list row: time, players with final scores, round count."""
    when = s.timestamp[:16].replace("T", " ")
    scores = " ".join(
        f"{name}({s.final_scores.get(name, '?')})" for name in s.players)
    return f"{when}  {scores}  [{t('replay.rounds_count', n=s.num_rounds)}]"


def _browse_game(console: Console, summary: GameSummary):
    """Round list for one game."""
    data = load_game(summary.path)
    rounds = data.get("rounds", [])
    is_sanma = bool(data.get("config", {}).get("is_sanma"))

    while True:
        console.print(f"\n  [bold]{t('replay.select_round')}[/bold]")
        for i, rd in enumerate(rounds):
            console.print(f"    {i + 1}. {t('replay.round_item', n=i + 1)}"
                          f"  {_round_summary(rd)}")
        console.print(f"    0. {t('replay.back')}")

        idx = _read_index(console, t('replay.select_round'), len(rounds))
        if idx is None or idx == 0:
            return
        _step_through(console, rounds[idx - 1], data["players"], is_sanma)


def _round_summary(round_data: dict) -> str:
    """Short result line for the round list."""
    result = round_data.get("result")
    if not result:
        return ""
    if result.get("is_draw"):
        return get_draw_message(result.get("draw_type") or "exhaustive")
    winners = result.get("winners") or []
    return " ".join(t('replay.winner', player=w) for w in winners)


def _step_through(console: Console, round_data: dict,
                  player_names: list, is_sanma: bool):
    """Step viewer: Enter=next, b=prev, number=jump, q=quit."""
    rr = RoundReplay(round_data, player_names, is_sanma)
    rr.goto(0)

    while True:
        _render_board(console, rr)
        _render_decision_review(console, rr)

        if rr.is_finished:
            _render_result(console, rr)

        console.print(f"  [dim]{t('replay.controls')}[/dim]")
        try:
            raw = _read_step_command(
                console, f"  {t('replay.step', i=rr.step, n=rr.num_steps)} > "
            )
        except EOFError:
            return

        if raw == "q":
            return
        elif raw == "l":
            _show_hand_history(console, rr)
        elif raw in ("b", "left"):
            rr.goto(rr.step - 1)
        elif raw in ("right",):
            if rr.is_finished:
                return
            rr.goto(rr.step + 1)
        elif raw.isdigit():
            rr.goto(int(raw))
        else:  # Enter or anything else = next
            if rr.is_finished:
                return
            rr.goto(rr.step + 1)


def spectator_wait_for_right(console: Console, round_data: dict,
                             player_names: list, is_sanma: bool) -> None:
    """Pause a live AI-vs-AI hand until the spectator presses Right.

    The logger appends actions before the engine requests the next decision.
    Rebuilding ``RoundReplay`` from that immutable prefix lets Left inspect
    earlier states while Right at the newest state releases exactly one AI
    decision back to the engine.  This keeps backward navigation safe: the
    live engine is never mutated by replay browsing.
    """
    rr = RoundReplay(round_data, player_names, is_sanma)
    rr.goto(rr.num_steps)
    while True:
        _render_board(console, rr)
        _render_decision_review(console, rr)
        if rr.is_finished:
            _render_result(console, rr)
        console.print("  [dim]←/→ browse  •  → at latest: next AI action  •  q: pause[/dim]")
        try:
            raw = _read_step_command(console, f"  {t('replay.step', i=rr.step, n=rr.num_steps)} > ")
        except EOFError:
            return
        if raw == "q":
            # Do not terminate the game; Right is the only command that
            # advances the engine. Treat q as a pause escape for compatibility.
            continue
        if raw in ("left", "b"):
            rr.goto(rr.step - 1)
        elif raw in ("right", ""):
            if rr.step >= rr.num_steps:
                return
            rr.goto(rr.step + 1)
        elif raw.isdigit():
            rr.goto(int(raw))


def _show_hand_history(console: Console, rr: RoundReplay) -> None:
    """Display a numbered, trainer-style action history for this round."""
    saved_step = rr.step
    table = Table(title=t('replay.history_title'), border_style="cyan")
    table.add_column("#", justify="right", style="dim")
    table.add_column(t('replay.history_action'))
    for step in range(1, rr.num_steps + 1):
        rr.goto(step)
        style = "bold yellow" if step == saved_step else None
        table.add_row(str(step), _action_description(rr), style=style)
    rr.goto(saved_step)
    console.print(table)
    try:
        console.input(f"  {t('prompt.press_enter')}")
    except EOFError:
        pass


def _render_decision_review(console: Console, rr: RoundReplay) -> None:
    """Show advisor feedback for the discard at the current replay step."""
    if rr.step <= 0 or rr.step > len(rr.actions):
        return
    action = rr.actions[rr.step - 1]
    is_discard = action.get("action") == "discard"
    if not is_discard and action.get("action") != "draw":
        return
    target_step = rr.step
    seat = action.get("seat", -1)
    if not (0 <= seat < len(rr.players)):
        return
    # The visible replay state is post-action, but discard evaluation needs
    # the hand before the tile was removed.
    before = RoundReplay(rr.data, rr.player_names, rr.is_sanma)
    before.goto(target_step if action.get("action") == "draw" else target_step - 1)
    player = before.players[seat]
    legal = list(player.closed)
    if player.draw_tile is not None and player.draw_tile not in legal:
        legal.append(player.draw_tile)
    if not legal:
        return
    winds = {"E": Wind.EAST, "S": Wind.SOUTH, "W": Wind.WEST, "N": Wind.NORTH}
    opponents = []
    for opp in rr.players:
        if opp.seat == seat:
            continue
        opponents.append(OpponentView(
            seat=opp.seat, name=opp.name, score=opp.score,
            seat_wind=opp.seat_wind, is_dealer=opp.is_dealer, is_riichi=opp.is_riichi,
            melds=list(opp.melds), discard_pool=opp.discards, discard_called=opp.discard_called,
            num_closed_tiles=len(opp.closed), kita_count=opp.kita_count,
            riichi_discard_index=opp.riichi_index))
    hand = type("ReplayHand", (), {})()
    hand.closed_tiles = list(player.closed)
    hand.draw_tile = player.draw_tile
    hand.melds = list(player.melds)
    hand.discard_pool = list(player.discards)
    hand.discard_called = list(player.discard_called)
    hand.discard_is_tsumogiri = []
    hand.is_riichi = player.is_riichi
    hand.to_34_array = lambda: [sum(t.index34 == i for t in hand.closed_tiles) for i in range(34)]
    hand.is_menzen = all(getattr(m, "kind", "") == "ankan" for m in getattr(player, "melds", []))
    view = GameView(
        my_hand=hand,
        my_seat=seat,
        my_wind=player.seat_wind,
        my_score=player.score,
        is_dealer=player.is_dealer,
        opponents=opponents,
        round_wind=rr.round_wind,
        honba=rr.honba,
        riichi_sticks=rr.riichi_sticks,
        remaining_tiles=rr.remaining,
    )
    advice = advise_discards(view, legal, limit=len(legal))
    if not advice:
        # A malformed/terminal replay hand should not make an AI turn look
        # unreviewed. Show a deterministic legal fallback recommendation.
        fallback = legal[0]
        console.print(Panel(
            f"{t('helper.suggestion')} {tile_to_display_str(fallback)}",
            title=f"[bold]{t('helper.review_title')} — {rr.player_names[seat]}[/bold]",
            border_style="cyan"))
        return
    actor_name = rr.player_names[seat]
    best = advice[0]
    actual_tile_str = action.get("tile")
    chosen = None
    if actual_tile_str:
        try:
            actual_tile = parse_tile_str(actual_tile_str)
            chosen = next((a for a in advice
                           if a.tile.index34 == actual_tile.index34
                           and a.tile.is_red == actual_tile.is_red), None)
            if chosen is None:
                chosen = next((a for a in advice if a.tile.index34 == actual_tile.index34), None)
        except Exception:
            chosen = None
    if chosen is None:
        chosen = next((a for a in advice
                       if tile_to_simple_str(a.tile) == actual_tile_str), None)
    if not is_discard:
        console.print(Panel(
            f"{t('helper.suggestion')} {tile_to_display_str(best.tile)}\n"
            f"{t('helper.shanten')}: {best.shanten}  {t('helper.ukeire')}: {best.ukeire}",
            title=f"[bold]{t('helper.review_title')} — {actor_name}[/bold]",
            border_style="cyan"))
        return
    if chosen is None:
        try:
            fallback_tile = parse_tile_str(actual_tile_str)
            fallback_advice = advise_discards(view, [fallback_tile], limit=1)
            chosen = fallback_advice[0] if fallback_advice else best
        except Exception:
            chosen = best
    is_best = (chosen.tile.index34 == best.tile.index34 and
               chosen.tile.is_red == best.tile.is_red)
    verdict = t('helper.review_best') if is_best else t(
        'helper.review_better', tile=tile_to_display_str(best.tile))
    def tile_set(indices):
        return " ".join(tile_to_display_str(ALL_TILES_136[idx * 4])
                         for idx in indices) or t('label.none')
    plans = potential_yaku_names(view, best)
    if is_best:
        detail = (
            f"{verdict}\n"
            f"{t('helper.suggestion')} {tile_to_display_str(best.tile)}\n"
            f"{t('helper.review_chosen')}: {tile_to_display_str(chosen.tile)}  "
            f"{t('helper.shanten')}: {chosen.shanten}  {t('helper.ukeire')}: {chosen.ukeire}\n"
            f"{t('helper.review_accepts')}: {tile_set(chosen.accepts)}"
        )
    else:
        detail = (
            f"{verdict}\n"
            f"{t('helper.suggestion')} {tile_to_display_str(best.tile)}\n"
            f"{t('helper.review_chosen')}: {tile_to_display_str(chosen.tile)}  "
            f"{t('helper.shanten')}: {chosen.shanten}  {t('helper.ukeire')}: {chosen.ukeire}\n"
            f"{t('helper.review_best_stat')}: {tile_to_display_str(best.tile)}  "
            f"{t('helper.shanten')}: {best.shanten}  {t('helper.ukeire')}: {best.ukeire}\n"
            f"{t('helper.review_accepts')}: {tile_set(chosen.accepts)}\n"
            f"{t('helper.review_best_accepts')}: {tile_set(best.accepts)}"
        )
    if plans:
        detail += f"\n{t('helper.review_plan')}: {' / '.join(t(name) for name in plans)}"
    console.print(Panel(
        detail,
        title=f"[bold]{t('helper.review_title')} — {actor_name}[/bold]",
        border_style="green" if is_best else "yellow"))


def _read_step_command(console: Console, prompt: str) -> str:
    """Read a replay command, including immediate left/right arrow keys.

    In a TTY we temporarily use character mode so arrows do not require an
    extra Enter. Piped input and Windows/non-TTY environments retain the
    normal line-input fallback.
    """
    if not sys.stdin.isatty():
        return console.input(prompt).strip().lower()

    try:
        import termios
        import tty
    except ImportError:
        return console.input(prompt).strip().lower()

    sys.stdout.write(prompt)
    sys.stdout.flush()
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    chars = []
    try:
        tty.setraw(fd)
        while True:
            ch = sys.stdin.read(1)
            if ch in ("\r", "\n"):
                sys.stdout.write("\n")
                return "".join(chars).strip().lower()
            if ch == "\x03":  # Ctrl-C
                raise KeyboardInterrupt
            if ch == "\x1b":
                sequence = sys.stdin.read(2)
                if sequence == "[D":
                    sys.stdout.write("\n")
                    return "left"
                if sequence == "[C":
                    sys.stdout.write("\n")
                    return "right"
                continue
            if ch in ("\x7f", "\b"):
                if chars:
                    chars.pop()
                    sys.stdout.write("\b \b")
                continue
            chars.append(ch)
            sys.stdout.write(ch)
            sys.stdout.flush()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _render_board(console: Console, rr: RoundReplay):
    """Render the full god-view board at the current step."""
    console.clear()

    header = Text()
    header.append(f"  {t('label.dora')}: ")
    header.append_text(tiles_to_rich_text(rr.dora_indicators))
    header.append(f"   {t('label.remaining', n=rr.remaining)}")
    console.print(Panel(header, title=f"[bold]{t('replay.title')}[/bold]",
                        border_style="cyan"))

    for p in rr.players:
        _render_player(console, rr, p)

    desc = _action_description(rr)
    if desc:
        console.print(Panel(desc, border_style="yellow", padding=(0, 2)))


def _active_seat(rr: RoundReplay):
    """Return the seat of the player who acted at the current replay step."""
    if rr.step <= 0:
        return 0 if rr.players else None
    action = rr.actions[rr.step - 1]
    seat = action.get("seat")
    if isinstance(seat, int) and 0 <= seat < len(rr.players):
        return seat
    return None

def _render_player(console: Console, rr: RoundReplay, p):
    """One player block: header, hand, melds, discards."""
    wind = rr.data["initial_hands"][p.name].get("wind", "")
    wind_str = wind_display(_KANJI_TO_WIND[wind]) if wind in _KANJI_TO_WIND else wind
    dealer = f" ({t('label.dealer_mark')})" if p.is_dealer else ""
    riichi = (f" [bold red]【{t('label.riichi_mark')}】[/bold red]"
              if p.is_riichi else "")
    kita = (f" [yellow]{t('replay.kita_count', n=p.kita_count)}[/yellow]"
            if p.kita_count else "")
    perspective = f" [cyan]({t('replay.you_marker')})[/cyan]" if p.seat == 0 else ""
    route = _player_route_label(rr, p)
    route_text = f" [dim]— {t('helper.review_plan')}: {route}[/dim]" if route else ""
    is_active = p.seat == _active_seat(rr)
    marker = "▶  " if is_active else "  "
    header = (f"{marker}[bold]{p.name}[/bold]{perspective} "
              f"({wind_str}{dealer}){riichi}{kita}{route_text}")
    if is_active:
        # Highlight the active seat without adding a bulky border around the
        # whole player block; this remains readable in both light and dark
        # terminals.
        console.print(f"[bold on #e8f1fb]{header}[/bold on #e8f1fb]")
    else:
        console.print(header)

    shown = [tile for tile in p.closed if tile is not p.draw_tile]
    tiles = sorted(shown)
    if p.draw_tile is not None:
        tiles.append(p.draw_tile)
    if get_display_mode() == TileDisplayMode.IMAGE and tiles:
        import sys
        sys.stdout.write(f"  {t('label.hand_tiles')} ")
        for tile in tiles:
            sys.stdout.write(tile_to_image_escape(tile, cols=4, rows=2,
                                                  no_cursor_move=True))
            sys.stdout.write("\x1b[5C")
        sys.stdout.write("\x1b[3B\r\n")
        sys.stdout.flush()
    else:
        hand_text = Text(f"  {t('label.hand_tiles')} ")
        for tile in tiles:
            hand_text.append_text(tile_to_rich_text(tile, highlight=tile is p.draw_tile))
            hand_text.append(" ")
        console.print(hand_text)

    _render_melds_line(console, [_MeldView(
        m.tiles, getattr(m, "meld_type", getattr(m, "kind", None)))
        for m in p.melds])

    if p.discards:
        discard_text = Text(f"  {t('label.discards')} ")
        discard_text.append_text(format_discard_pool(
            p.discards, p.discard_called, p.riichi_index))
        console.print(discard_text)
    console.print()


class _MeldView:
    """Adapter: _render_melds_line expects objects with a .tiles attribute."""

    def __init__(self, tiles, meld_type=None):
        self.tiles = tiles
        self.meld_type = meld_type


def _player_route_label(rr: RoundReplay, p) -> str:
    """Unified hand plan for a player header using the 5-block planning engine."""
    tiles = list(p.closed)
    if not tiles:
        return ""
    hand = type("ReplayHand", (), {})()
    hand.closed_tiles = list(tiles)
    hand.melds = list(p.melds)
    hand.discard_pool = list(p.discards)
    hand.to_34_array = lambda: [sum(t.index34 == i for t in hand.closed_tiles) for i in range(34)]
    hand.is_menzen = all(getattr(m, "kind", "") == "ankan" for m in getattr(p, "melds", []))

    wind_name = rr.data.get("initial_hands", {}).get(p.name, {}).get("wind", "E")
    winds = {"東": Wind.EAST, "南": Wind.SOUTH, "西": Wind.WEST, "北": Wind.NORTH}
    my_wind = winds.get(wind_name, Wind.EAST)
    round_wind_str = str(rr.data.get("round_wind", "east")).lower()
    round_wind = {"east": Wind.EAST, "south": Wind.SOUTH, "west": Wind.WEST, "north": Wind.NORTH}.get(round_wind_str, Wind.EAST)

    view = GameView(
        my_hand=hand,
        my_seat=p.seat,
        my_wind=my_wind,
        round_wind=round_wind,
        my_score=25000,
        is_dealer=p.is_dealer,
        opponents=[],
        remaining_tiles=rr.remaining,
        dora_indicators=rr.dora_indicators,
    )

    advice_list = advise_discards(view, tiles, limit=1)
    if not advice_list:
        return ""
    plans = potential_yaku_names(view, advice_list[0])
    if not plans:
        return ""
    return " · ".join(t(name) for name in plans)


def _action_description(rr: RoundReplay) -> str:
    """Localized description of the action that produced this state."""
    action = rr.current_action
    if action is None:
        return t('replay.deal')

    kind = action["action"]
    player = action["player"]

    if kind == "draw":
        tile = tile_to_display_str(parse_tile_str(action["tile"]))
        return t('replay.act.draw', player=player, tile=tile)
    if kind == "discard":
        tile = tile_to_display_str(parse_tile_str(action["tile"]))
        key = ('replay.act.discard_tsumogiri' if action.get("tsumogiri")
               else 'replay.act.discard')
        return t(key, player=player, tile=tile)
    if kind in ("chi", "pon", "ankan", "daiminkan", "shouminkan",
                "riichi", "kita", "tsumo"):
        return t(f'replay.act.{kind}', player=player)
    if kind == "ron":
        return t('replay.act.ron', player=player,
                 from_player=action.get("from_player", ""))
    return f"{player}: {kind}"


def _render_result(console: Console, rr: RoundReplay):
    """Result panel at the final step."""
    result = rr.result
    if not result:
        return

    if result.get("is_draw"):
        msg = get_draw_message(result.get("draw_type") or "exhaustive")
        console.print(Panel(f"[bold yellow]{msg}[/bold yellow]",
                            border_style="yellow"))
    else:
        for winner, info in (result.get("yaku") or {}).items():
            table = Table(title=f"{t('replay.winner', player=winner)}  "
                                f"{info['total_points']}{t('label.points_suffix')}",
                          border_style="green")
            table.add_column(t('label.yaku_name'), style="bold")
            table.add_column(t('label.han_count'), justify="right")
            for y in info.get("yaku_list", []):
                table.add_row(translate_yaku(y["name"]),
                              t('label.han_format', han=y["han"]))
            console.print(table)

    changes = result.get("score_changes") or {}
    if any(v != 0 for v in changes.values()):
        console.print(f"  {t('label.score_changes')}")
        for name, change in changes.items():
            if change:
                style = "green" if change > 0 else "red"
                sign = "+" if change > 0 else ""
                console.print(f"    {name}: [{style}]{sign}{change}[/{style}]")
    console.print()


def _read_index(console: Console, prompt: str, max_idx: int):
    """Read an index 0..max_idx; None on EOF."""
    while True:
        try:
            raw = console.input(f"  > {prompt} ").strip()
            idx = int(raw)
            if 0 <= idx <= max_idx:
                return idx
        except ValueError:
            pass
        except EOFError:
            return None
        console.print(f"  [red]{t('prompt.invalid_input')}[/red]")
