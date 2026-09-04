"""Board layout rendering using Rich."""

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.columns import Columns

from mahjong.player.base import GameView, OpponentView
from mahjong.core.tile import Tile, ALL_TILES_136
from mahjong.core.player_state import PlayerState, Wind
from mahjong.ui.tile_display import (
    tile_to_rich_text, tiles_to_rich_text, format_discard_pool,
    tile_to_display_str, _tile_display_width,
    TileDisplayMode, get_display_mode, tile_to_image_escape
)
from mahjong.ui.i18n import t, translate_yaku, get_draw_message
from mahjong.ui.labels import wind_display, rank_display
from mahjong.rules.scoring import ScoreResult, calculate_score
from mahjong.core.tile import next_tile_index
from mahjong.engine.action import ActionType
from mahjong.player.advisor import advise_discards, recommend_route, potential_yaku_names


def _render_melds_line(console: Console, melds):
    """Render one line of melds ('副露: ...'), or nothing if no melds."""
    if not melds:
        return
    if get_display_mode() == TileDisplayMode.IMAGE:
        # Inline image protocols are cursor-relative; rendering melds as
        # text keeps them on their own row and prevents overlap with the hand.
        meld_text = Text(f"  {t('label.melds')} ")
        for i, meld in enumerate(melds):
            if i > 0:
                meld_text.append(" | ")
            meld_kind = getattr(meld, "meld_type", None)
            kind = getattr(meld_kind, "value", meld_kind or "")
            if kind:
                meld_text.append(f"[{t(f'meld.{kind}')}] ", style="bold yellow")
            meld_text.append(" ".join(tile_to_display_str(tile) for tile in meld.tiles))
        console.print(meld_text)
    else:
        meld_text = Text(f"  {t('label.melds')} ")
        for i, meld in enumerate(melds):
            if i > 0:
                meld_text.append(" | ")
            meld_kind = getattr(meld, "meld_type", None)
            kind = getattr(meld_kind, "value", meld_kind or "")
            if kind:
                meld_text.append(f"[{t(f'meld.{kind}')}] ", style="bold yellow")
            meld_text.append_text(tiles_to_rich_text(list(meld.tiles)))
        console.print(meld_text)


def render_board(console: Console, game_view: GameView):
    """Render the full board state."""
    console.clear()

    # Header: round info
    if get_display_mode() == TileDisplayMode.IMAGE:
        header = Text()
        header.append(f"  {game_view.round_label}  {t('label.round_wind')}:{wind_display(game_view.round_wind)}")
        header.append(f"\n  {t('label.remaining', n=game_view.remaining_tiles)}")
        header.append(f"  {t('label.riichi_sticks', n=game_view.riichi_sticks)}")
        console.print(Panel(header, title=f"[bold]{t('label.game_title')}[/bold]", border_style="cyan"))

        import sys
        sys.stdout.write(f"  {t('label.dora')}: ")
        for tile in game_view.dora_indicators:
            sys.stdout.write(tile_to_image_escape(tile, cols=3, rows=2, no_cursor_move=True))
            sys.stdout.write("\x1b[4C")
        sys.stdout.write("\r\n" * 3)
        sys.stdout.flush()
    else:
        dora_text = tiles_to_rich_text(game_view.dora_indicators)
        header = Text()
        header.append(f"  {game_view.round_label}  {t('label.round_wind')}:{wind_display(game_view.round_wind)}  {t('label.dora')}: ")
        header.append_text(dora_text)
        header.append(f"\n  {t('label.remaining', n=game_view.remaining_tiles)}")
        header.append(f"  {t('label.riichi_sticks', n=game_view.riichi_sticks)}")
        console.print(Panel(header, title=f"[bold]{t('label.game_title')}[/bold]", border_style="cyan"))

    # Keep the latest discard unambiguous: player rows can be several lines
    # apart, especially in a long hand.
    if game_view.last_discard is not None and game_view.last_discard_player is not None:
        if game_view.last_discard_player == game_view.my_seat:
            latest_player = t('label.you')
        else:
            latest_player = next(
                (opp.name for opp in game_view.opponents
                 if opp.seat == game_view.last_discard_player),
                t('label.player') + f" {game_view.last_discard_player}",
            )
        latest = Text()
        latest.append(t('label.latest_discard', player=latest_player) + ": ", style="bold yellow")
        latest.append_text(tile_to_rich_text(game_view.last_discard, highlight=True))
        console.print(Panel(latest, border_style="yellow", padding=(0, 1)))

    # Build all-player list sorted by seat wind (東→南→西→北 = turn order)
    _render_all_players(console, game_view)

    # Separator
    console.print("─" * 60, style="dim")

    # Player's hand tiles
    _render_player_hand(console, game_view)


def _render_all_players(console: Console, game_view: GameView):
    """Render all players' info and discard pools in turn order."""
    active_seat = game_view.active_player if game_view.active_player is not None else game_view.my_seat
    entries = []

    # Self
    hand = game_view.my_hand
    entries.append({
        'seat': game_view.my_seat,
        'is_active': (game_view.my_seat == active_seat),
        'wind': game_view.my_wind,
        'is_self': True,
        'name': t('label.you'),
        'score': game_view.my_score,
        'is_dealer': game_view.is_dealer,
        'is_riichi': hand.is_riichi,
        'kita_count': game_view.my_kita_count,
        'melds': hand.melds,
        'discard_pool': hand.discard_pool,
        'discard_called': hand.discard_called,
        'riichi_discard_index': hand.riichi_discard_index,
        'highlight_last': False,
    })

    # Opponents
    for opp in game_view.opponents:
        entries.append({
            'seat': opp.seat,
            'is_active': (opp.seat == active_seat),
            'wind': opp.seat_wind,
            'is_self': False,
            'name': opp.name,
            'score': opp.score,
            'is_dealer': opp.is_dealer,
            'is_riichi': opp.is_riichi,
            'kita_count': opp.kita_count,
            'melds': opp.melds,
            'discard_pool': opp.discard_pool,
            'discard_called': opp.discard_called,
            'riichi_discard_index': opp.riichi_discard_index,
            'highlight_last': opp.seat == game_view.last_discard_player,
        })

    entries.sort(key=lambda e: e['wind'].value)

    for e in entries:
        _render_player_row(console, e)


def _render_player_row(console: Console, entry: dict):
    """Render one player's header + melds + discard pool."""
    wind_str = wind_display(entry['wind'])
    dealer_mark = f" ({t('label.dealer_mark')})" if entry['is_dealer'] else ""
    riichi_mark = f" [bold red]【{t('label.riichi_mark')}】[/bold red]" if entry['is_riichi'] else ""
    kita_mark = (f" [bold yellow]{t('label.kita_count', n=entry['kita_count'])}[/bold yellow]"
                 if entry.get('kita_count', 0) > 0 else "")

    is_active = entry.get('is_active', False)
    marker = "[bold cyan]▶[/bold cyan]  " if is_active else "   "

    if entry['is_self']:
        name_display = f"[bold cyan]{t('label.you')}[/bold cyan]"
    else:
        name_display = f"[bold]{entry['name']}[/bold]" if is_active else entry['name']

    pts = t('label.points_suffix')
    header_line = (
        f" {marker}{name_display} ({wind_str}{dealer_mark}) "
        f"{entry['score']}{pts}{riichi_mark}{kita_mark}"
    )
    if is_active:
        console.print(f"[cyan]{header_line}[/cyan]")
    else:
        console.print(header_line)

    _render_melds_line(console, entry['melds'])

    # Discard pool
    if entry['discard_pool']:
        if get_display_mode() == TileDisplayMode.IMAGE:
            import sys
            sys.stdout.write(f"  {t('label.discards')} ")
            for i, tile in enumerate(entry['discard_pool']):
                is_riichi = (i == entry.get('riichi_discard_index', -1))
                if is_riichi:
                    sys.stdout.write("(")
                sys.stdout.write(tile_to_image_escape(tile, cols=3, rows=2, no_cursor_move=True))
                sys.stdout.write("\x1b[4C")
                if is_riichi:
                    sys.stdout.write(")")
            sys.stdout.write("\x1b[3B\r\n")
            sys.stdout.flush()
        else:
            discard_text = Text(f"  {t('label.discards')} ")
            discard_text.append_text(format_discard_pool(
                entry['discard_pool'],
                entry['discard_called'],
                entry['riichi_discard_index'],
                highlight_last=entry.get('highlight_last', False),
            ))
            console.print(discard_text)
    else:
        console.print(f"  {t('label.discards')} ", style="dim")

    console.print()


def _render_player_hand(console: Console, game_view: GameView):
    """Render the player's own hand tiles."""
    hand = game_view.my_hand

    console.print(
        f"  [bold]{t('label.your_hand')}[/bold]"
    )

    # Number labels
    tiles = hand.closed_tiles
    draw_tile = hand.draw_tile

    # Safety guard: only treat draw_tile as separate if it's actually in closed_tiles
    if draw_tile is not None and draw_tile not in tiles:
        draw_tile = None

    # Separate drawn tile from rest
    display_tiles = []
    for tile in tiles:
        if tile == draw_tile:
            continue
        display_tiles.append(tile)
    display_tiles.sort()

    mode = get_display_mode()

    if mode == TileDisplayMode.IMAGE:
        TILE_COLS = 4
        TILE_ROWS = 2

        num_text = Text("  ")
        for i in range(len(display_tiles)):
            label = str(i + 1)
            num_text.append(f"{label:^{TILE_COLS}} ", style="bold cyan")

        if draw_tile:
            label = str(len(display_tiles) + 1)
            num_text.append(f"  {label:^{TILE_COLS}}", style="bold yellow")

        console.print(num_text)

        import sys
        # Render all image tiles side-by-side on a single horizontal row
        sys.stdout.write("  ")
        for tile in display_tiles:
            sys.stdout.write(tile_to_image_escape(tile, cols=TILE_COLS, rows=TILE_ROWS, no_cursor_move=True))
            sys.stdout.write(f"\x1b[{TILE_COLS + 1}C")

        if draw_tile:
            sys.stdout.write("\x1b[2C")
            sys.stdout.write(tile_to_image_escape(draw_tile, cols=TILE_COLS, rows=TILE_ROWS, no_cursor_move=True))

        # Advance down below the 2-row image block and return carriage
        sys.stdout.write("\r\n" * (TILE_ROWS + 1))
        sys.stdout.flush()
    else:
        grid = Table.grid(padding=(0, 2))
        for _ in range(len(display_tiles)):
            grid.add_column(justify="center")

        num_cells = [Text(str(i + 1), style="bold cyan") for i in range(len(display_tiles))]
        tile_cells = [tile_to_rich_text(tile) for tile in display_tiles]

        if draw_tile:
            grid.add_column(justify="center")  # gap column
            grid.add_column(justify="center")  # draw tile column
            num_cells.append(Text(" "))
            num_cells.append(Text(str(len(display_tiles) + 1), style="bold yellow"))
            tile_cells.append(Text(" "))
            tile_cells.append(tile_to_rich_text(draw_tile, highlight=False))

        grid.add_row(*num_cells)
        grid.add_row(*tile_cells)

        from rich.padding import Padding
        console.print(Padding(grid, (0, 0, 0, 2)))

    _render_melds_line(console, hand.melds)

    console.print()


def render_action_prompt(console: Console, available):
    """Render available actions as a compact, high-contrast action bar."""
    def _without_hotkey(label: str) -> str:
        # Locale labels retain a legacy ``[X]`` prefix; the action bar renders
        # the key in its own column so it remains aligned and non-duplicated.
        return label[1:].split("]", 1)[-1].lstrip() if label.startswith("[") else label

    actions = []
    if available.can_tsumo:
        actions.append(("T", t('action.tsumo'), "bold green"))
    if available.can_ron:
        actions.append(("H", t('action.ron'), "bold green"))
    if available.can_riichi:
        actions.append(("R", t('action.riichi'), "bold yellow"))
    if available.can_pon:
        actions.append(("P", t('action.pon'), "bold cyan"))
    if available.can_chi:
        actions.append(("C", t('action.chi'), "bold cyan"))
    if available.can_ankan or available.can_shouminkan:
        actions.append(("K", t('action.kan'), "bold cyan"))
    if available.can_kita:
        actions.append(("N", t('action.kita'), "bold yellow"))
    if available.can_kyuushu:
        actions.append(("9", t('action.kyuushu'), "bold magenta"))

    if actions:
        table = Table.grid(padding=(0, 2))
        table.add_column(style="bold white", justify="center")
        table.add_column(style="white")
        for key, label, style in actions:
            table.add_row(f"[{style}][{key}][/{style}]", _without_hotkey(label))
        table.add_row("[dim][S][/dim]", _without_hotkey(t('action.skip')))
        console.print(Panel(
            table,
            title=f"[bold]{t('prompt.choose_action')}[/bold]",
            border_style="cyan",
            padding=(0, 1),
        ))
    elif available.can_discard:
        console.print(Panel(
            f"[bold cyan]{t('prompt.action_discard')}[/bold cyan]  "
            f"[dim]({len(available.can_discard)} tiles)[/dim]",
            border_style="dim",
            padding=(0, 1),
        ))


def render_current_yaku_hint(console: Console, game_view: GameView,
                             available) -> None:
    """Show yaku/han when the current hand has a legal win."""
    is_tsumo = bool(available.can_tsumo)
    is_ron = bool(available.can_ron)
    if not (is_tsumo or is_ron):
        return
    hand = game_view.my_hand.clone()
    win_tile = hand.draw_tile if is_tsumo else game_view.last_discard
    if win_tile is None:
        return
    if is_ron:
        hand.closed_tiles.append(win_tile)
    sanma = len(game_view.opponents) == 2
    dora_indices = [next_tile_index(tile.index34, sanma)
                    for tile in game_view.dora_indicators]
    result = calculate_score(
        hand=hand, win_tile=win_tile, is_tsumo=is_tsumo,
        seat_wind_34=game_view.my_wind.index34,
        round_wind_34=game_view.round_wind.index34,
        is_dealer=game_view.is_dealer,
        dora_tiles_34=dora_indices, uradora_tiles_34=[],
        is_riichi=hand.is_riichi,
        is_double_riichi=hand.is_double_riichi,
        is_ippatsu=hand.is_ippatsu,
        is_sanma=sanma, kita_count=game_view.my_kita_count,
    )
    if result is None:
        return
    yaku = "  ".join(
        f"{translate_yaku(name)} ({han}{t('label.han_short')})"
        for name, han in result.yaku
    )
    console.print(Panel(
        f"[bold green]{yaku}[/bold green]\n"
        f"{t('label.han_count')}: {result.han}  "
        f"{t('label.fu')}: {result.fu}  "
        f"{t('label.score')}: {result.total_points}{t('label.points_suffix')}",
        title=f"[bold]{t('label.current_yaku')}[/bold]",
        border_style="green", padding=(0, 1),
    ))


def render_discard_review(console: Console, game_view: GameView,
                          available, action) -> None:
    """Give trainer-style feedback for a chosen discard.

    A useful review needs to distinguish a structural error (shanten), an
    efficiency error (ukeire), and a deliberate value/defence trade-off.  We
    therefore show the chosen line, its rank, the best alternatives, and the
    concrete tiles accepted by each line instead of a single ukeire delta.
    """
    if action.action_type == ActionType.RIICHI:
        chosen = action.riichi_discard or action.tile
    elif action.action_type == ActionType.DISCARD:
        chosen = action.tile
    else:
        return
    if chosen is None:
        return
    advice = advise_discards(game_view, available.can_discard, limit=20)
    # Physical tile IDs distinguish the four copies, but discard advice is
    # evaluated by tile type. Match on 34-index plus red-dora status so a
    # duplicate copy selected by the player still receives a review.
    chosen_advice = next((item for item in advice
                          if item.tile.index34 == chosen.index34
                          and item.tile.is_red == chosen.is_red), None)
    if chosen_advice is None or not advice:
        return
    best = advice[0]
    chosen_rank = next(i for i, item in enumerate(advice, 1)
                       if item is chosen_advice)
    shanten_gap = chosen_advice.shanten - best.shanten
    ukeire_gap = best.ukeire - chosen_advice.ukeire
    if shanten_gap > 0:
        title, style = t('helper.review_mistake'), "red"
        verdict = t('helper.review_shanten_loss', n=shanten_gap)
    elif ukeire_gap > 0:
        title, style = t('helper.review_efficiency'), "yellow"
        verdict = t('helper.review_ukeire_loss', n=ukeire_gap)
    else:
        title, style = t('helper.review_good'), "green"
        verdict = t('helper.review_best')

    def _tile_set(indices):
        return " ".join(tile_to_display_str(ALL_TILES_136[i * 4])
                         for i in indices) or t('label.none')

    if chosen_rank == 1:
        detail = (
            f"{verdict}\n"
            f"{t('helper.review_chosen')}: {tile_to_display_str(chosen)}  "
            f"{t('helper.shanten')}: {chosen_advice.shanten}  "
            f"{t('helper.ukeire')}: {chosen_advice.ukeire}\n"
            f"{t('helper.review_accepts')}: {_tile_set(chosen_advice.accepts)}"
        )
    else:
        detail = (
            f"{verdict}\n"
            f"{t('helper.review_chosen')}: {tile_to_display_str(chosen)}  "
            f"{t('helper.review_rank')}: {chosen_rank}/{len(advice)}  "
            f"{t('helper.shanten')}: {chosen_advice.shanten}  "
            f"{t('helper.ukeire')}: {chosen_advice.ukeire}\n"
            f"{t('helper.review_best_stat')}: {tile_to_display_str(best.tile)}  "
            f"{t('helper.shanten')}: {best.shanten}  "
            f"{t('helper.ukeire')}: {best.ukeire}  "
            f"{t('helper.review_loss')}: {max(0, ukeire_gap)}\n"
            f"{t('helper.review_accepts')}: {_tile_set(chosen_advice.accepts)}\n"
            f"{t('helper.review_best_accepts')}: {_tile_set(best.accepts)}"
        )
    # Surface the trade-off even when two choices are equally efficient.
    if chosen_advice.dora != best.dora or chosen_advice.danger != best.danger:
        detail += (f"\n{t('helper.review_tradeoff')}: "
                   f"{t('helper.dora_value')} {chosen_advice.dora} vs {best.dora}; "
                   f"{t('helper.danger')} {chosen_advice.danger} vs {best.danger}")
    route = recommend_route(game_view, best)
    plans = potential_yaku_names(game_view, best)
    if plans:
        detail += f"\n{t('helper.review_plan')}: {' / '.join(t(name) for name in plans)}"
    if route:
        route_text = "  |  ".join(
            f"{_tile_set(draws)} → {tile_to_display_str(discard)}"
            for draws, discard in route
        )
        detail += f"\n{t('helper.review_route')}: {tile_to_display_str(best.tile)} → {route_text}"
    console.print(Panel(detail, title=f"[bold]{title}[/bold]",
                        border_style=style, padding=(0, 1)))

    table = Table(title=t('helper.review_candidates'), border_style=style,
                  show_header=True)
    table.add_column("#", justify="right")
    table.add_column(t('helper.review_discard'))
    table.add_column(t('helper.shanten'), justify="right")
    table.add_column(t('helper.ukeire'), justify="right")
    table.add_column(t('helper.dora_value'), justify="right")
    table.add_column(t('helper.danger'), justify="right")
    for rank, item in enumerate(advice[:5], 1):
        marker = "* " if item is chosen_advice else ""
        table.add_row(str(rank), marker + tile_to_display_str(item.tile),
                      str(item.shanten), str(item.ukeire), str(item.dora),
                      str(item.danger))
    console.print(table)


def render_win_announcement(console: Console, player_name: str,
                            is_tsumo: bool, loser_name: str = ""):
    """Render just the win announcement banner."""
    console.print()
    if is_tsumo:
        console.print(Panel(
            f"[bold green]{t('msg.tsumo_win', player=player_name)}[/bold green]",
            border_style="green"
        ))
    else:
        console.print(Panel(
            f"[bold green]{t('msg.ron_win', player=player_name, loser=loser_name)}[/bold green]",
            border_style="green"
        ))


def render_yaku_summary(console: Console, score_result: ScoreResult):
    """Render yaku table and score summary line."""
    table = Table(title=t('label.yaku_list'), show_header=True, border_style="cyan")
    table.add_column(t('label.yaku_name'), style="bold")
    table.add_column(t('label.han_count'), justify="right")

    for yaku_name, han in score_result.yaku:
        table.add_row(translate_yaku(yaku_name), t('label.han_format', han=han))

    console.print(table)

    pts = t('label.points_suffix')
    if score_result.is_yakuman:
        console.print(f"  [bold red]{t('msg.yakuman', points=score_result.total_points)}[/bold red]")
    else:
        console.print(f"  {rank_display(score_result)} "
                      f"{score_result.total_points}{pts}")
    console.print()


def render_win_screen(console: Console, player_name: str,
                      score_result: ScoreResult, is_tsumo: bool,
                      loser_name: str = ""):
    """Render winning screen (announcement + yaku). Kept for compatibility."""
    render_win_announcement(console, player_name, is_tsumo, loser_name)
    render_yaku_summary(console, score_result)


def render_round_end_hands(console: Console, players: list,
                           player_names: list, winners: list,
                           loser: int = None, ron_tile=None):
    """Render all players' hands after a round ends (win or draw)."""
    if ron_tile is None and loser is not None and players[loser].hand.discard_pool:
        ron_tile = players[loser].hand.discard_pool[-1]

    console.print(f"  [bold]{t('label.all_hands')}[/bold]")
    console.print()

    for i, p in enumerate(players):
        hand = p.hand
        wind_str = wind_display(p.seat_wind)
        is_winner = i in winners

        # Name header
        if is_winner:
            tag = f" [bold green]【{t('label.winner_tag')}】[/bold green]"
        elif i == loser:
            tag = f" [red]【{t('label.loser_tag')}】[/red]"
        else:
            tag = ""

        console.print(f"  {player_names[i]} ({wind_str}){tag}")

        # Hand tiles
        if get_display_mode() == TileDisplayMode.IMAGE:
            # The result screen contains several consecutive hands and meld
            # rows. Cursor-relative inline images can overwrite neighboring
            # rows here, so use the stable Rich tile representation.
            tile_text = Text(f"  {t('label.hand_tiles')} ")
            if is_winner and hand.draw_tile:
                display = [tile for tile in hand.closed_tiles if tile != hand.draw_tile]
                display.sort()
                for tile in display:
                    tile_text.append_text(tile_to_rich_text(tile))
                    tile_text.append(" ")
                tile_text.append_text(tile_to_rich_text(hand.draw_tile, highlight=True))
                tile_text.append(f" {t('label.tsumo_indicator')}", style="bold green")
            elif is_winner and ron_tile:
                display = sorted(hand.closed_tiles)
                for tile in display:
                    tile_text.append_text(tile_to_rich_text(tile))
                    tile_text.append(" ")
                tile_text.append_text(tile_to_rich_text(ron_tile, highlight=True))
                tile_text.append(f" {t('label.ron_indicator')}", style="bold green")
            else:
                display = sorted(hand.closed_tiles)
                if display:
                    for tile in display:
                        tile_text.append_text(tile_to_rich_text(tile))
                        tile_text.append(" ")
                else:
                    tile_text.append(t('label.none'), style="dim")
            console.print(tile_text)
        elif is_winner and hand.draw_tile:
            display = [tile for tile in hand.closed_tiles if tile != hand.draw_tile]
            display.sort()
            tile_text = Text(f"  {t('label.hand_tiles')} ")
            for tile in display:
                tile_text.append_text(tile_to_rich_text(tile))
                tile_text.append(" ")
            tile_text.append(" ")
            tile_text.append_text(tile_to_rich_text(hand.draw_tile, highlight=True))
            tile_text.append(f" {t('label.tsumo_indicator')}", style="bold green")
            console.print(tile_text)
        elif is_winner and ron_tile:
            display = sorted(hand.closed_tiles)
            tile_text = Text(f"  {t('label.hand_tiles')} ")
            for tile in display:
                tile_text.append_text(tile_to_rich_text(tile))
                tile_text.append(" ")
            tile_text.append(" ")
            tile_text.append_text(tile_to_rich_text(ron_tile, highlight=True))
            tile_text.append(f" {t('label.ron_indicator')}", style="bold green")
            console.print(tile_text)
        else:
            display = sorted(hand.closed_tiles)
            if display:
                tile_text = Text(f"  {t('label.hand_tiles')} ")
                for tile in display:
                    tile_text.append_text(tile_to_rich_text(tile))
                    tile_text.append(" ")
                console.print(tile_text)
            else:
                console.print(f"  {t('label.hand_tiles')} {t('label.none')}", style="dim")

        _render_melds_line(console, hand.melds)

        console.print()


def render_draw_screen(console: Console, draw_type: str,
                       tenpai_players: list = None):
    """Render draw screen."""
    console.print()
    msg = get_draw_message(draw_type)
    console.print(Panel(f"[bold yellow]{msg}[/bold yellow]",
                        border_style="yellow"))

    if tenpai_players is not None and draw_type == "exhaustive":
        for name, is_tenpai in tenpai_players:
            status = t('msg.tenpai') if is_tenpai else t('msg.noten')
            console.print(f"  {name}: {status}")
    console.print()


def render_scores(console: Console, players: list):
    """Render current scores."""
    table = Table(title=t('label.scores_title'), border_style="cyan")
    table.add_column(t('label.player'), style="bold")
    table.add_column(t('label.score'), justify="right")

    pts = t('label.points_suffix')
    for name, score in players:
        style = "green" if score > 0 else "red" if score < 0 else ""
        table.add_row(name, f"{score}{pts}", style=style)

    console.print(table)


def render_score_changes(console: Console, player_names: list,
                         score_changes: list, current_scores: list):
    """Render a merged table of score changes and current totals."""
    pts = t('label.points_suffix')
    table = Table(title=t('label.score_changes'), border_style="cyan")
    table.add_column(t('label.player'), style="bold")
    table.add_column(t('label.score_change'), justify="right")
    table.add_column(t('label.score'), justify="right")

    for i, name in enumerate(player_names):
        change = score_changes[i]
        score = current_scores[i]
        if change > 0:
            change_str = f"[green]+{change}[/green]"
        elif change < 0:
            change_str = f"[red]{change}[/red]"
        else:
            change_str = f"[dim]±0[/dim]"
        table.add_row(name, change_str, f"{score}{pts}")

    console.print(table)


def render_game_end(console: Console, players: list):
    """Render final game results."""
    console.print()
    console.print(Panel(f"[bold]{t('msg.game_end')}[/bold]", border_style="gold1"))

    sorted_players = sorted(players, key=lambda x: x[1], reverse=True)

    table = Table(title=t('label.final_scores'), border_style="gold1")
    table.add_column(t('label.rank'), justify="center")
    table.add_column(t('label.player'), style="bold")
    table.add_column(t('label.score'), justify="right")

    pts = t('label.points_suffix')
    for i, (name, score) in enumerate(sorted_players):
        rank = ["🥇", "🥈", "🥉", "4"][i] if i < 4 else str(i + 1)
        style = "bold green" if i == 0 else ""
        table.add_row(rank, name, f"{score}{pts}", style=style)

    console.print(table)
    console.print(f"\n  {t('msg.winner', player=sorted_players[0][0])}")
    console.print()
