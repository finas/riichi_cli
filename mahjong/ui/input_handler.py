"""User input handling for the terminal UI."""

import time
from typing import List, Optional

from rich.console import Console
from rich.panel import Panel

from mahjong.core.tile import Tile
from mahjong.engine.action import Action, ActionType, AvailableActions
from mahjong.core.meld import Meld
from mahjong.ui.tile_display import (
    tile_to_display_str, tile_to_rich_markup, TileDisplayMode, get_display_mode, tile_to_image_escape
)
from mahjong.ui.i18n import t
from mahjong.ui.timeout_input import timed_input
from mahjong.rules.shanten import shanten
from mahjong.player.advisor import advise_discards
from mahjong.core.tile import ALL_TILES_136


def _render_helper_hint(console: Console, game_view,
                        available: AvailableActions) -> None:
    """Render a lightweight discard hint without changing the game state."""
    if not available.can_discard:
        return
    current = shanten(game_view.my_hand.to_34_array())
    open_melds = sum(len(opp.melds) for opp in game_view.opponents)
    advice = advise_discards(game_view, available.can_discard, limit=3)
    if not advice:
        return
    best = advice[0]
    alternatives = "  |  ".join(
        t('helper.alt_format', tile=tile_to_display_str(item.tile),
          shanten=item.shanten, ukeire=item.ukeire)
        for item in advice[1:]
    )
    detail = (f"{t('helper.shanten')}: {current} → {best.shanten}  |  "
              f"{t('helper.ukeire')}: {best.ukeire}  |  "
              f"{t('helper.dora_value')}: {best.dora}  |  "
              f"{t('helper.shape_value')}: {best.shape}  |  "
              f"{t('helper.danger')}: {best.danger}  |  "
              f"{t('helper.open_melds')}: {open_melds}")
    if best.waits:
        waits = " ".join(tile_to_display_str(ALL_TILES_136[i * 4])
                          for i in best.waits)
        detail += f"\n{t('helper.waits')}: {waits}"
    if alternatives:
        detail += f"\n{t('helper.alternatives')}: {alternatives}"
    console.print(Panel(
        f"[bold cyan]{t('helper.suggestion')}[/bold cyan] "
        f"[bold yellow]{tile_to_display_str(best.tile)}[/bold yellow]\n{detail}",
        title=f"[bold]{t('helper.title')}[/bold]",
        border_style="cyan", padding=(0, 1),
    ))

def _make_prob_bar(p: float, width: int = 12) -> str:
    """Render a proportional Unicode block bar for a percentage (0.0 to 100.0)."""
    p = max(0.0, min(100.0, float(p)))
    filled = int(p / 100.0 * width)
    rem = (p / 100.0 * width) - filled
    sub = ""
    if rem >= 0.875:
        sub = "█"
    elif rem >= 0.75:
        sub = "▉"
    elif rem >= 0.625:
        sub = "▊"
    elif rem >= 0.5:
        sub = "▋"
    elif rem >= 0.375:
        sub = "▌"
    elif rem >= 0.25:
        sub = "▍"
    elif rem >= 0.125:
        sub = "▎"
    elif rem > 0.02:
        sub = "▏"
    bar = "█" * filled + sub
    return bar if bar else " "

def _render_coach_suggestion(console: Console, suggestion) -> None:
    """Render Mortal/AI coach suggestion with win possibility and deal-in risk."""
    if not suggestion:
        return
    best_str = ""
    prompt_label = t('coach.suggestion')
    act_type = getattr(suggestion.action, "action_type", None) if suggestion.action else None

    if act_type == ActionType.TSUMO:
        prompt_label = t('coach.suggestion_action')
        best_str = f"[bold green]{t('action.tsumo')}[/bold green] ([bold yellow]{t('msg.tenpai')}[/bold yellow])"
    elif act_type == ActionType.RON:
        prompt_label = t('coach.suggestion_action')
        best_str = f"[bold green]{t('action.ron')}[/bold green] ([bold yellow]{t('msg.tenpai')}[/bold yellow])"
    elif act_type == ActionType.RIICHI:
        best_str = f"[bold magenta]{t('action.riichi')}[/bold magenta]"
        if suggestion.best_tile:
            best_str += f" → {tile_to_rich_markup(suggestion.best_tile)}"
    elif suggestion.best_tile:
        best_str = tile_to_rich_markup(suggestion.best_tile)
        ev = getattr(suggestion, "best_eval", None)
        if ev and ev.q_value is not None and ev.mortal_prob is not None:
            bar = _make_prob_bar(ev.mortal_prob, width=12)
            best_str += f"  [dim]([/dim][bold yellow]Q: {ev.q_value:+.2f}[/bold yellow] [dim]|[/dim] [bold cyan]P: {ev.mortal_prob:.1f}%[/bold cyan][dim])[/dim]  [bold green]{bar}[/bold green]"
    elif act_type is not None:
        prompt_label = t('coach.suggestion_action')
        action_map = {
            ActionType.KITA: t('action.kita'),
            ActionType.KYUUSHU: t('action.kyuushu'),
            ActionType.PON: t('action.pon'),
            ActionType.CHI: t('action.chi'),
            ActionType.ANKAN: t('action.kan'),
            ActionType.SHOUMINKAN: t('action.kan'),
            ActionType.DAIMINKAN: t('action.kan'),
        }
        best_str = f"[bold green]{action_map.get(act_type, getattr(act_type, 'name', str(suggestion.action)))}[/bold green]"
    else:
        return

    content = f"[bold cyan]{prompt_label}[/bold cyan] {best_str}\n"

    stance_key = getattr(suggestion, "tactical_stance", "")
    stance_map = {
        "push_tenpai": f"[bold red]🔥 {t('coach.stance_push_tenpai')}[/bold red]",
        "push_mawashi": f"[bold yellow]⚔️ {t('coach.stance_push_mawashi')}[/bold yellow]",
        "fold": f"[bold green]🛡️ {t('coach.stance_fold')}[/bold green]",
        "late_defense": f"[bold yellow]⚠️ {t('coach.stance_late')}[/bold yellow]",
        "attack": f"[bold cyan]🚀 {t('coach.stance_attack')}[/bold cyan]",
    }
    if stance_key in stance_map:
        content += f"  • [bold]{t('coach.tactical_stance')}:[/bold] {stance_map[stance_key]}\n"

    safe_tiles = getattr(suggestion, "safe_tiles", [])
    if safe_tiles:
        safe_labels = []
        for tile, kind in safe_tiles[:4]:
            tag = (
                t("coach.genbutsu_tag")
                if kind == "genbutsu"
                else t("coach.suji_tag")
                if kind == "suji"
                else t("coach.kabe_tag")
            )
            safe_labels.append(f"{tile_to_rich_markup(tile)} [dim]({tag})[/dim]")
        content += f"  • [bold]{t('coach.safe_tiles_radar')}:[/bold] " + "  ".join(safe_labels) + "\n"

    ev = getattr(suggestion, "best_eval", None)
    if ev:
        risk_color = "green" if ev.deal_in_risk < 5.0 else "yellow" if ev.deal_in_risk < 15.0 else "red"
        risk_tag = f" ({t('coach.safe')})" if ev.is_safe else f" ({t('coach.danger')})" if ev.deal_in_risk >= 15.0 else ""
        content += (
            f"  • [bold]{t('coach.win_possibility')}:[/bold] [bold cyan]{ev.win_prob:.1f}%[/bold cyan]    "
            f"• [bold]{t('coach.deal_in_risk')}:[/bold] [{risk_color}]{ev.deal_in_risk:.1f}%{risk_tag}[/{risk_color}]\n"
        )
        if ev.win_prob >= 100.0:
            content += f"  • [bold green]{t('msg.tenpai')}:[/bold green] [bold yellow]{best_str}[/bold yellow]"
        else:
            content += (
                f"  • {t('coach.shanten')}: {ev.shanten}    "
                f"• {t('coach.ukeire')}: {ev.ukeire}"
            )
        if ev.note:
            content += f"  [dim]({ev.note})[/dim]"

    alternatives = getattr(suggestion, "alternatives", [])
    if alternatives:
        alt_lines = []
        for alt in alternatives[:3]:
            a_risk_color = "green" if alt.deal_in_risk < 5.0 else "yellow" if alt.deal_in_risk < 15.0 else "red"
            alt_mortal = ""
            if alt.q_value is not None and alt.mortal_prob is not None:
                a_bar = _make_prob_bar(alt.mortal_prob, width=8)
                alt_mortal = f"[bold yellow]Q: {alt.q_value:+.2f}[/bold yellow] | [bold cyan]P: {alt.mortal_prob:.1f}%[/bold cyan] [green]{a_bar}[/green] | "
            else:
                alt_mortal = f"{t('coach.win_possibility')} {alt.win_prob:.1f}% | "
            alt_lines.append(
                f"  - {tile_to_rich_markup(alt.tile)}  "
                f"{alt_mortal}"
                f"{t('coach.deal_in_risk')} [{a_risk_color}]{alt.deal_in_risk:.1f}%[/{a_risk_color}] | "
                f"{t('coach.ukeire')} {alt.ukeire}"
            )
        content += f"\n  [dim]{t('coach.alternatives')}[/dim]\n" + "\n".join(alt_lines)

    engine_label = getattr(suggestion, "engine_name", "Mortal") or "Mortal"
    console.print(Panel(
        content,
        title=f"[bold magenta]{t('coach.title')} [{engine_label}][/bold magenta]",
        border_style="magenta",
        padding=(0, 1),
    ))


def _consult_and_render_gemini(
    console: Console,
    game_view,
    available: AvailableActions,
) -> None:
    """Fetch and render Google Gemini strategic advice via agy."""
    from mahjong.analysis.gemini_coach import (
        get_gemini_advice, get_gemini_model, is_gemini_available, format_gemini_advice_markup
    )
    if not is_gemini_available():
        console.print(Panel(
            f"[yellow]{t('coach.gemini_not_found')}[/yellow]",
            title=f"[bold magenta]{t('coach.title_gemini')}[/bold magenta]",
            border_style="magenta",
            padding=(0, 1),
        ))
        return

    with console.status(f"[bold cyan]{t('coach.consulting_gemini')}[/bold cyan]", spinner="dots"):
        advice = get_gemini_advice(game_view, available)
    model = get_gemini_model()
    formatted_advice = format_gemini_advice_markup(advice)
    console.print(Panel(
        formatted_advice,
        title=f"[bold magenta]{t('coach.title_gemini')} [{model}][/bold magenta]",
        border_style="magenta",
        padding=(0, 1),
    ))


def get_player_input(console: Console, game_view, available: AvailableActions,
                     deadline: Optional[float] = None,
                     base_end: Optional[float] = None,
                     on_toggle_helper=None,
                     coach_suggestion=None) -> Action:
    """Get action from human player via terminal input."""
    player_idx = available.player
    allowed_set = set(tile.id for tile in available.can_discard)

    # Riichi: only tsumogiri allowed — pause to show board, then auto-discard
    if game_view.my_hand.is_riichi and available.can_discard:
        if not available.can_tsumo and not available.can_ankan and not available.can_kita:
            tile = available.can_discard[0]
            if get_display_mode() == TileDisplayMode.IMAGE:
                import sys
                sys.stdout.write(f"  {t('msg.riichi_tsumogiri')} ")
                sys.stdout.write(tile_to_image_escape(tile, cols=3, rows=2, no_cursor_move=True))
                sys.stdout.write("\r\n" * 3)
                sys.stdout.flush()
            else:
                console.print(f"  {t('msg.riichi_tsumogiri')} [bold]{tile_to_display_str(tile)}[/bold]")
            # Tsumogiri after riichi is forced by the rules; do not pause for
            # an extra Enter key. The engine will process the discard
            # immediately after this action is returned.
            return Action(ActionType.DISCARD, player_idx, tile=tile)

    # If only discard is available (and nothing else special)
    if not available.has_action and available.can_discard:
        return _get_discard_input(console, game_view, available, allowed_set,
                                  deadline, base_end, on_toggle_helper,
                                  coach_suggestion=coach_suggestion)

    # Show action options
    helper_enabled = coach_suggestion is not None
    panels_visible = True
    rendered_panels = False
    while True:
        if helper_enabled and panels_visible and not rendered_panels:
            if coach_suggestion:
                _render_coach_suggestion(console, coach_suggestion)
            else:
                _render_helper_hint(console, game_view, available)
            rendered_panels = True
        if available.can_tsumo or available.can_ron:
            if available.can_tsumo:
                console.print(f"  {t('action.tsumo')}")
            if available.can_ron:
                console.print(f"  {t('action.ron')}")

        prompt_parts = []
        if available.can_tsumo:
            prompt_parts.append(t('prompt.action_tsumo'))
        if available.can_ron:
            prompt_parts.append(t('prompt.action_ron'))
        if available.can_riichi:
            prompt_parts.append(t('prompt.action_riichi'))
        if available.can_pon:
            prompt_parts.append(t('prompt.action_pon'))
        if available.can_chi:
            prompt_parts.append(t('prompt.action_chi'))
        if (available.can_ankan or available.can_shouminkan
                or available.can_daiminkan):
            prompt_parts.append(t('prompt.action_kan'))
        if available.can_kita:
            prompt_parts.append(t('prompt.action_kita'))
        if available.can_kyuushu:
            prompt_parts.append(t('prompt.action_kyuushu'))
        if available.can_discard:
            prompt_parts.append(t('prompt.action_discard'))
        prompt_parts.append(t('prompt.action_skip'))
        prompt_parts.append(t('prompt.action_gemini'))

        prompt = "  > " + " | ".join(prompt_parts) + ": "
        choice = timed_input(prompt, deadline, base_end=base_end,
                             instant_keys={'h', 'H', 'c', 'C', 's', 'S', 'g', 'G'})

        if choice is None:
            console.print(f"  [yellow]{t('tc.timeout')}[/yellow]")
            return _default_action(available, player_idx)

        raw_choice = choice.strip()
        # Uppercase H is reserved for toggling the trainer panels. Lowercase
        # h remains the Ron hotkey when Ron is available.
        if raw_choice == 'H':
            if on_toggle_helper is not None:
                panels_visible = on_toggle_helper()
            else:
                panels_visible = not panels_visible
            helper_enabled = panels_visible
            rendered_panels = False
            console.clear()
            continue
        if raw_choice.lower() == 'g':
            t0 = time.monotonic()
            _consult_and_render_gemini(console, game_view, available)
            dt = time.monotonic() - t0
            if deadline is not None:
                deadline += dt
            if base_end is not None:
                base_end += dt
            continue
        choice = raw_choice.lower()

        if choice == 't' and available.can_tsumo:
            return Action(ActionType.TSUMO, player_idx)

        if choice == 'h' and available.can_ron:
            return Action(ActionType.RON, player_idx)
        # Lowercase h is Ron (when available); uppercase H toggles hints.

        if choice == 'r' and available.can_riichi:
            # Choose riichi discard
            console.print(f"  {t('prompt.choose_riichi_discard')}")
            if get_display_mode() == TileDisplayMode.IMAGE:
                import sys
                for i, tile in enumerate(available.riichi_candidates):
                    sys.stdout.write(f"    {i+1}. ")
                    sys.stdout.write(tile_to_image_escape(tile, cols=3, rows=2, no_cursor_move=True))
                    sys.stdout.write("\r\n" * 3)
                sys.stdout.flush()
            else:
                for i, tile in enumerate(available.riichi_candidates):
                    console.print(f"    {i+1}. {tile_to_display_str(tile)}")
            while True:
                result = timed_input(f"  > {t('prompt.number')} ", deadline, base_end=base_end)
                if result is None:
                    console.print(f"  [yellow]{t('tc.timeout')}[/yellow]")
                    return Action(ActionType.RIICHI, player_idx,
                                  riichi_discard=available.riichi_candidates[0])
                try:
                    idx = int(result.strip()) - 1
                    if 0 <= idx < len(available.riichi_candidates):
                        return Action(ActionType.RIICHI, player_idx,
                                      riichi_discard=available.riichi_candidates[idx])
                except ValueError:
                    pass
                console.print(f"  [red]{t('prompt.invalid_input')}[/red]")

        if choice == 'p' and available.can_pon:
            return Action(ActionType.PON, player_idx, meld=available.can_pon[0])

        if choice == 'c' and available.can_chi:
            if len(available.can_chi) == 1:
                return Action(ActionType.CHI, player_idx, meld=available.can_chi[0])
            console.print(f"  {t('prompt.choose_chi')}")
            if get_display_mode() == TileDisplayMode.IMAGE:
                import sys
                for i, meld in enumerate(available.can_chi):
                    sys.stdout.write(f"    {i+1}. ")
                    for tile in meld.tiles:
                        sys.stdout.write(tile_to_image_escape(tile, cols=3, rows=2, no_cursor_move=True))
                        sys.stdout.write("\x1b[4C")
                    sys.stdout.write("\r\n" * 3)
                sys.stdout.flush()
            else:
                for i, meld in enumerate(available.can_chi):
                    tiles_str = " ".join(tile_to_display_str(tile) for tile in meld.tiles)
                    console.print(f"    {i+1}. {tiles_str}")
            while True:
                result = timed_input(f"  > {t('prompt.number')} ", deadline, base_end=base_end)
                if result is None:
                    console.print(f"  [yellow]{t('tc.timeout')}[/yellow]")
                    return Action(ActionType.CHI, player_idx,
                                  meld=available.can_chi[0])
                try:
                    idx = int(result.strip()) - 1
                    if 0 <= idx < len(available.can_chi):
                        return Action(ActionType.CHI, player_idx,
                                      meld=available.can_chi[idx])
                except ValueError:
                    pass
                console.print(f"  [red]{t('prompt.invalid_input')}[/red]")

        if choice == 'k':
            if available.can_ankan:
                return Action(ActionType.ANKAN, player_idx,
                              tile=available.can_ankan[0][0])
            if available.can_shouminkan:
                return Action(ActionType.SHOUMINKAN, player_idx,
                              tile=available.can_shouminkan[0])
            if available.can_daiminkan:
                return Action(ActionType.DAIMINKAN, player_idx,
                              meld=available.can_daiminkan[0])

        if choice == 'n' and available.can_kita:
            return Action(ActionType.KITA, player_idx)

        if choice == '9' and available.can_kyuushu:
            return Action(ActionType.KYUUSHU, player_idx)

        if choice == 's':
            if available.can_discard:
                return _get_discard_input(console, game_view, available, allowed_set, deadline, base_end, coach_suggestion=coach_suggestion)
            return Action(ActionType.SKIP, player_idx)

        # Try as number for discard
        if available.can_discard:
            try:
                idx = int(choice) - 1
                tiles = _get_sorted_display_tiles(game_view)
                if 0 <= idx < len(tiles):
                    tile = tiles[idx]
                    if tile.id in allowed_set:
                        return Action(ActionType.DISCARD, player_idx, tile=tile)
                    else:
                        console.print(f"  [red]{t('msg.riichi_only_tsumogiri')}[/red]")
                        continue
            except ValueError:
                pass

        console.print(f"  [red]{t('prompt.invalid_retry')}[/red]")


def _default_action(available: AvailableActions, player_idx: int) -> Action:
    """Return the safest default action on timeout."""
    if available.can_discard:
        # Discard the last tile in can_discard (draw tile position)
        return Action(ActionType.DISCARD, player_idx, tile=available.can_discard[-1])
    return Action(ActionType.SKIP, player_idx)


def _get_discard_input(console: Console, game_view, available: AvailableActions,
                       allowed_set: set, deadline: Optional[float] = None,
                       base_end: Optional[float] = None,
                       on_toggle_helper=None,
                       coach_suggestion=None) -> Action:
    """Get discard tile selection, validating against allowed tiles."""
    tiles = _get_sorted_display_tiles(game_view)
    n = len(tiles)

    prompt = f"  > {t('prompt.choose_discard', n=n)} [G: Gemini] "
    helper_enabled = coach_suggestion is not None
    panels_visible = True
    rendered_panels = False
    while True:
        if helper_enabled and panels_visible and not rendered_panels:
            if coach_suggestion:
                _render_coach_suggestion(console, coach_suggestion)
            else:
                _render_helper_hint(console, game_view, available)
            rendered_panels = True
        choice = timed_input(prompt, deadline, base_end=base_end,
                             instant_keys={'h', 'H', 'g', 'G'})
        if choice is None:
            console.print(f"  [yellow]{t('tc.timeout')}[/yellow]")
            # Default: discard last tile (draw tile)
            return Action(ActionType.DISCARD, available.player, tile=available.can_discard[-1])
        try:
            choice_str = choice.strip()
            if choice_str == 'H' or choice_str == 'h':
                if on_toggle_helper is not None:
                    panels_visible = on_toggle_helper()
                else:
                    panels_visible = not panels_visible
                helper_enabled = panels_visible
                rendered_panels = False
                console.clear()
                continue
            if choice_str.lower() == 'g':
                t0 = time.monotonic()
                _consult_and_render_gemini(console, game_view, available)
                dt = time.monotonic() - t0
                if deadline is not None:
                    deadline += dt
                if base_end is not None:
                    base_end += dt
                continue
            idx = int(choice.strip()) - 1
            if 0 <= idx < n:
                tile = tiles[idx]
                if tile.id in allowed_set:
                    return Action(ActionType.DISCARD, available.player, tile=tile)
                else:
                    console.print(f"  [red]{t('msg.riichi_only_tsumogiri')}[/red]")
                    continue
        except ValueError:
            pass
        console.print(f"  [red]{t('prompt.invalid_input')}[/red]")


def _get_sorted_display_tiles(game_view) -> list:
    """Get tiles in display order (sorted, draw tile last)."""
    hand = game_view.my_hand
    draw_tile = hand.draw_tile

    display = []
    for tile in hand.closed_tiles:
        if tile == draw_tile:
            continue
        display.append(tile)
    display.sort()

    if draw_tile:
        display.append(draw_tile)

    return display
