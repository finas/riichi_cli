#!/usr/bin/env python3
"""Japanese Riichi Mahjong - Terminal CLI Game

This is the package entry point, invoked by the `riichi` console command.
"""

import sys
import time
import os
import shlex
import shutil
from rich.console import Console
from rich.panel import Panel

from mahjong.engine.event import EventBus, EventType, GameEvent
from mahjong.engine.game import GameConfig, GameState
from mahjong.engine.round import run_round
from mahjong.engine.action import ActionType
from mahjong.player.base import build_game_view
from mahjong.player.greedy_ai import GreedyAI
from mahjong.player.human import HumanPlayer
from mahjong.player.mjai_player import MjaiPlayer
from mahjong.ui.renderer import Renderer
from mahjong.ui.board_layout import (
    render_win_announcement, render_yaku_summary, render_draw_screen,
    render_scores, render_score_changes, render_game_end,
    render_round_end_hands
)
from mahjong.engine.game_logger import GameLogger
from mahjong.engine.time_control import TIME_CONTROL_PRESETS, TimeControl
from mahjong.engine.ai_delay import AI_DELAY_PRESETS, AIDelay
from mahjong.ui.i18n import t, set_language, get_language
from mahjong.ui.labels import round_label
from mahjong.ui.tile_display import TileDisplayMode, get_display_mode, set_display_mode

console = Console()


def _change_language():
    """Language selection sub-screen."""
    console.print(f"\n  {t('lang.select')}")
    console.print(f"    1. {t('lang.zh')}")
    console.print(f"    2. {t('lang.ja')}")
    console.print(f"    3. {t('lang.en')}")
    console.print()

    while True:
        try:
            choice = int(console.input("  > 1/2/3: ").strip())
            if choice == 1:
                set_language("zh")
                return
            elif choice == 2:
                set_language("ja")
                return
            elif choice == 3:
                set_language("en")
                return
        except (ValueError, EOFError):
            pass
        console.print("  [red]Invalid / 无效 / 無効[/red]")


def _choose_time_control(current: TimeControl) -> TimeControl:
    """Time control selection sub-screen. Returns new selection."""
    console.print(f"\n  {t('tc.select')}")
    for i, tc in enumerate(TIME_CONTROL_PRESETS):
        marker = " *" if tc is current else ""
        console.print(f"    {i}. {t(tc.name)}{marker}")
    console.print()

    while True:
        try:
            idx = int(console.input(
                f"  > {t('prompt.choose_mode', n=len(TIME_CONTROL_PRESETS) - 1)} "
            ).strip())
            if 0 <= idx < len(TIME_CONTROL_PRESETS):
                return TIME_CONTROL_PRESETS[idx]
        except (ValueError, EOFError):
            pass
        console.print(f"  [red]{t('prompt.invalid_input')}[/red]")


def _choose_ai_delay(current: AIDelay) -> AIDelay:
    """AI delay selection sub-screen. Returns new selection."""
    console.print(f"\n  {t('ai_delay.select')}")
    for i, d in enumerate(AI_DELAY_PRESETS):
        marker = " *" if d is current else ""
        console.print(f"    {i}. {t(d.name)}{marker}")
    console.print()

    while True:
        try:
            idx = int(console.input(
                f"  > {t('prompt.choose_mode', n=len(AI_DELAY_PRESETS) - 1)} "
            ).strip())
            if 0 <= idx < len(AI_DELAY_PRESETS):
                return AI_DELAY_PRESETS[idx]
        except (ValueError, EOFError):
            pass
        console.print(f"  [red]{t('prompt.invalid_input')}[/red]")


def _choose_tile_style():
    """Tile display style selection sub-screen."""
    console.print(f"\n  {t('tile_style.select')}")
    styles = [
        (TileDisplayMode.IMAGE, t("tile_style.image")),
        (TileDisplayMode.EMOJI, t("tile_style.emoji")),
        (TileDisplayMode.TEXT, t("tile_style.text")),
    ]
    cur = get_display_mode()
    for i, (mode, name) in enumerate(styles):
        marker = " *" if mode == cur else ""
        console.print(f"    {i + 1}. {name}{marker}")
    console.print()

    while True:
        try:
            choice = int(console.input(
                f"  > {t('prompt.choose_mode', n=len(styles))} "
            ).strip())
            if 1 <= choice <= len(styles):
                set_display_mode(styles[choice - 1][0])
                return
        except (ValueError, EOFError):
            pass
        console.print(f"  [red]{t('prompt.invalid_input')}[/red]")


def show_settings(current_time_control: TimeControl,
                  current_ai_delay: AIDelay) -> tuple[TimeControl, AIDelay]:
    """Show settings submenu. Returns (possibly updated) time control and ai delay."""
    from mahjong.ui.sound import is_sound_enabled, set_sound_enabled

    while True:
        lang_name = t(f"lang.{get_language()}")
        tc_name = t(current_time_control.name)
        delay_name = t(current_ai_delay.name)
        cur_mode = get_display_mode()
        style_key = "image" if cur_mode == TileDisplayMode.IMAGE else "emoji" if cur_mode == TileDisplayMode.EMOJI else "text"
        style_name = t(f"tile_style.{style_key}")
        sound_status = t('settings.sound_on') if is_sound_enabled() else t('settings.sound_off')

        console.print()
        console.print(Panel(
            f"[bold]{t('settings.title')}[/bold]\n"
            f"[dim]{t('settings.current_lang', lang=lang_name)}[/dim]\n"
            f"[dim]{t('settings.current_time', tc=tc_name)}[/dim]\n"
            f"[dim]{t('settings.current_ai_delay', delay=delay_name)}[/dim]\n"
            f"[dim]{t('settings.current_tile_style', style=style_name)}[/dim]\n"
            f"[dim]{t('settings.current_sound', sound=sound_status)}[/dim]",
            border_style="dim",
            padding=(0, 4),
        ))
        console.print()
        console.print(f"    1. {t('settings.change_lang')}  [dim](Change Language / 言語切替)[/dim]")
        console.print(f"    2. {t('settings.change_time')}")
        console.print(f"    3. {t('settings.change_ai_delay')}")
        console.print(f"    4. {t('settings.change_tile_style')}")
        console.print(f"    5. {t('settings.change_sound')}")
        console.print(f"    0. {t('settings.back')}")
        console.print()

        while True:
            try:
                choice = int(console.input(f"  > {t('prompt.choose_mode', n=5)} ").strip())
                if 0 <= choice <= 5:
                    break
            except (ValueError, EOFError):
                pass
            console.print(f"  [red]{t('prompt.invalid_input')}[/red]")

        if choice == 0:
            return current_time_control, current_ai_delay
        elif choice == 1:
            _change_language()
        elif choice == 2:
            current_time_control = _choose_time_control(current_time_control)
        elif choice == 3:
            current_ai_delay = _choose_ai_delay(current_ai_delay)
        elif choice == 4:
            _choose_tile_style()
        elif choice == 5:
            set_sound_enabled(not is_sound_enabled())


def show_lan_menu(time_control: TimeControl, ai_delay: AIDelay):
    """Show LAN Multiplayer submenu."""
    console.print()
    console.print(Panel(
        f"[bold cyan]{t('lan.menu_title')}[/bold cyan]",
        border_style="cyan",
        padding=(0, 4),
    ))
    console.print()
    console.print(f"    1. {t('lan.host')}")
    console.print(f"    2. {t('lan.join')}")
    console.print(f"    0. {t('settings.back')}")
    console.print()

    while True:
        try:
            choice = int(console.input(f"  > {t('prompt.choose_mode', n=2)} ").strip())
            if 0 <= choice <= 2:
                break
        except (ValueError, EOFError):
            pass
        console.print(f"  [red]{t('prompt.invalid_input')}[/red]")

    if choice == 1:
        from mahjong.network.lan_ui import host_lan_game
        host_lan_game(console, time_control, ai_delay)
    elif choice == 2:
        from mahjong.network.lan_ui import join_lan_game
        join_lan_game(console)

def _choose_spectator_engine() -> str:
    """Prompt the user to choose an AI engine for spectator mode."""
    if not sys.stdin.isatty():
        return os.environ.get("MAHJONG_AI_BACKEND", "akochan").lower()

    console.print()
    console.print(f"  {t('spectator.choose_engine')}")
    console.print(f"    1. {t('spectator.engine_mortal')}")
    console.print(f"    2. {t('spectator.engine_akochan')}")
    console.print(f"    3. {t('spectator.engine_native')}")
    console.print()

    while True:
        try:
            line = console.input(f"  > {t('prompt.choose_mode', n=3)} ").strip()
            if not line:
                return os.environ.get("MAHJONG_AI_BACKEND", "mortal").lower()
            choice = int(line)
            if choice == 1:
                return "mortal"
            elif choice == 2:
                return "akochan"
            elif choice == 3:
                return "native"
        except (ValueError, EOFError):
            pass
        console.print(f"  [red]{t('prompt.invalid_input')}[/red]")



def _choose_coach_engine() -> str:
    """Prompt the user to choose a Coach engine for Learning Mode."""
    env_coach = os.environ.get("MAHJONG_COACH_BACKEND", "").lower()
    if env_coach:
        return env_coach
    if not sys.stdin.isatty():
        return "mortal"

    console.print()
    console.print(f"  {t('coach.choose_engine')}")
    console.print(f"    1. {t('coach.engine_gemini')}")
    console.print(f"    2. {t('coach.engine_mortal')}")
    console.print(f"    3. {t('coach.engine_native')}")
    console.print()

    while True:
        try:
            line = console.input(f"  > {t('prompt.choose_mode', n=3)} ").strip()
            if not line:
                return "gemini" if shutil.which("agy") else "mortal"
            choice = int(line)
            if choice == 1:
                return "gemini"
            elif choice == 2:
                return "mortal"
            elif choice == 3:
                return "native"
        except (ValueError, EOFError):
            pass
        console.print(f"  [red]{t('prompt.invalid_input')}[/red]")
def show_menu() -> int:
    """Show mode selection menu and return choice."""
    console.print()
    console.print(Panel(
        f"[bold cyan]{t('label.game_title')}[/bold cyan]\n"
        f"[dim]{t('label.subtitle')}[/dim]",
        border_style="cyan",
        padding=(1, 4),
    ))
    console.print()
    console.print(f"  {t('mode.select')}")
    console.print(f"    1. {t('mode.4p')}")
    console.print(f"    2. {t('mode.4p_tonpuu')}")
    console.print(f"    3. {t('mode.3p')}")
    console.print(f"    4. {t('mode.3p_tonpuu')}")
    console.print(f"    5. {t('mode.spectator')}")
    console.print(f"    6. {t('mode.lan')}")
    console.print(f"    7. {t('mode.settings')}  [dim](Settings / 設定)[/dim]")
    console.print(f"    8. {t('mode.replay')}")
    console.print(f"    9. {t('mode.learning')}")
    console.print(f"   10. {t('mode.early_plan_practice')}")
    console.print(f"    0. {t('mode.quit')}")
    console.print()

    while True:
        try:
            choice = int(console.input(f"  > {t('prompt.choose_mode', n=10)} ").strip())
            if 0 <= choice <= 10:
                return choice
        except (ValueError, EOFError):
            pass
        console.print(f"  [red]{t('prompt.invalid_input')}[/red]")


def create_game(choice: int, time_control: TimeControl = None,
                is_spectator: bool = False,
                backend_override: str = None,
                coach_override: str = None):
    """Create game based on menu choice."""
    is_learning = choice == 9
    is_sanma = choice in (3, 4)
    is_tonpuu = choice in (2, 4)
    num_players = 3 if is_sanma else 4

    config = GameConfig(
        num_players=num_players,
        is_sanma=is_sanma,
        is_tonpuu=is_tonpuu,
        time_control=time_control,
    )

    event_bus = EventBus()
    renderer = Renderer(console, event_bus, human_seat=0)
    # Optional neural backend.  Set MAHJONG_AI_COMMAND to a command that
    # speaks mjai JSON-lines (for example a Mortal wrapper).  Native AI remains
    # the automatic fallback when unset, unavailable, or timing out.
    external_cmd = None
    backend_name = (backend_override or os.environ.get(
        "MAHJONG_AI_BACKEND", "akochan" if is_spectator else "native")).lower()
    raw_cmd = os.environ.get("MAHJONG_AI_COMMAND", "").strip() if backend_name != "native" else ""
    # Akochan is opt-in because its legacy pipe protocol expects complete
    # initial hands (including opponents), which is unsuitable for a fair
    # information-barrier match without an additional masking shim.
    if not raw_cmd and backend_name == "akochan":
        bundled = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "engines", "akochan", "run.sh"))
        if os.path.isfile(bundled) and os.access(bundled, os.X_OK):
            raw_cmd = bundled
    if not raw_cmd and backend_name == "mortal":
        bundled = os.path.abspath(os.path.join(
            os.path.dirname(__file__), "..", "engines", "mortal", "run.sh"))
        if os.path.isfile(bundled) and os.access(bundled, os.X_OK):
            raw_cmd = bundled
    if raw_cmd:
        try:
            external_cmd = shlex.split(raw_cmd)
        except ValueError:
            external_cmd = None

    # Make the active external engine visible anywhere player names are
    # rendered (board, results, logs, and replays). A custom label is useful
    # when running a named checkpoint or another MJAI-compatible engine.
    ai_display_name = ""
    if external_cmd:
        ai_display_name = os.environ.get(
            "MAHJONG_AI_DISPLAY_NAME", "").strip()
        if not ai_display_name:
            ai_display_name = {
                "mortal": "Mortal",
                "akochan": "Akochan",
            }.get(backend_name, "MJAI")

    def display_ai_name(name):
        return f"{name} [{ai_display_name}]" if ai_display_name else name

    def make_ai(name, seat):
        native = GreedyAI(name)
        if external_cmd:
            default_timeout = (30.0 if backend_name == "mortal"
                               else 10.0 if is_spectator else 2.0)
            try:
                response_timeout = float(os.environ.get(
                    "MAHJONG_AI_TIMEOUT", default_timeout))
            except ValueError:
                response_timeout = default_timeout
            neural = MjaiPlayer(name, seat, external_cmd, native,
                                response_timeout=response_timeout,
                                oracle=(is_spectator and
                                        backend_name == "akochan" and
                                        os.environ.get("MAHJONG_AI_ORACLE", "1") == "1"))
            neural.bind_event_bus(event_bus)
            return neural
        return native

    if is_spectator:
        # Spectator mode is a trainer: AI reasoning/review is visible by
        # default instead of being hidden behind the human-player H toggle.
        renderer.helper_panels_visible = True
        renderer.spectator_mode = True
    elif is_learning:
        renderer.helper_panels_visible = True

    # In learning mode, prepare coach for Seat 0
    coach_player = None
    coach_name = "Mortal"
    if is_learning:
        coach_backend = (coach_override or os.environ.get("MAHJONG_COACH_BACKEND", "")).lower()
        if coach_backend == "gemini":
            from mahjong.analysis.gemini_coach import GeminiCoachPlayer, get_gemini_model
            model_name = get_gemini_model()
            coach_hint = t("coach.press_g_hint")
            coach_name = f"Google Gemini [{model_name}]{coach_hint}"
            coach_player = GeminiCoachPlayer(name="CoachGemini", auto_query=False)
        elif coach_backend == "native":
            coach_fallback = GreedyAI("CoachFallback")
            coach_name = "Native Heuristic"
            coach_player = coach_fallback
        else:
            raw_coach_cmd = os.environ.get("MAHJONG_COACH_COMMAND", "").strip()
            if not raw_coach_cmd and (backend_name == "mortal" or os.environ.get("MAHJONG_AI_COMMAND")):
                raw_coach_cmd = os.environ.get("MAHJONG_AI_COMMAND", "").strip()
            if not raw_coach_cmd:
                bundled = os.path.abspath(os.path.join(
                    os.path.dirname(__file__), "..", "engines", "mortal", "run.sh"))
                if os.path.isfile(bundled) and os.access(bundled, os.X_OK):
                    raw_coach_cmd = bundled
                elif raw_cmd:
                    raw_coach_cmd = raw_cmd
            coach_cmd = None
            if raw_coach_cmd:
                try:
                    coach_cmd = shlex.split(raw_coach_cmd)
                except ValueError:
                    coach_cmd = None
            coach_fallback = GreedyAI("CoachFallback")
            coach_name = os.environ.get("MAHJONG_COACH_DISPLAY_NAME", "").strip() or "Mortal"
            if coach_cmd:
                default_timeout = float(os.environ.get("MAHJONG_AI_TIMEOUT", 30.0))
                coach_player = MjaiPlayer("CoachMortal", 0, coach_cmd, coach_fallback,
                                          response_timeout=default_timeout,
                                          defer_reach_ack=True)
                coach_player.bind_event_bus(event_bus)
            else:
                coach_player = coach_fallback
    # Create players (indexed by seat; seat 0 is the human unless spectating)
    ai_names = [display_ai_name(t('ai.name_a')),
                display_ai_name(t('ai.name_b')),
                display_ai_name(t('ai.name_c'))]
    if is_spectator:
        player_names = [
            display_ai_name(t('ai.spectator.east')),
            display_ai_name(t('ai.spectator.south')),
            display_ai_name(t('ai.spectator.west')),
        ]
        if not is_sanma:
            player_names.append(display_ai_name(t('ai.spectator.north')))
        players = [make_ai(name, i) for i, name in enumerate(player_names)]
    else:
        player_name = t('label.you')
        player_names = [player_name] + ai_names[:num_players - 1]
        human = HumanPlayer(player_name, console, renderer, time_control,
                            coach=coach_player if is_learning else None,
                            coach_name=coach_name)
        players = [human] + [make_ai(n, i) for i, n in enumerate(ai_names[:num_players - 1], 1)]

    return config, event_bus, renderer, player_names, players


def play_game(choice: int, time_control: TimeControl, ai_delay: AIDelay):
    """Play a complete game."""
    is_spectator = choice == 5
    is_learning = choice == 9
    backend_override = None
    coach_override = None
    if is_spectator:
        backend_override = _choose_spectator_engine()
    elif is_learning:
        coach_override = _choose_coach_engine()
    config, event_bus, renderer, player_names, players = create_game(
        choice, time_control if not is_spectator else None, is_spectator,
        backend_override=backend_override,
        coach_override=coach_override,
    )

    game = GameState(config, player_names, event_bus)

    # Initialize game logger
    logger = GameLogger(player_names, {
        "num_players": config.num_players,
        "is_sanma": config.is_sanma,
        "is_tonpuu": config.is_tonpuu,
        "starting_score": config.starting_score,
    })
    logger.subscribe_events(event_bus)

    event_bus.emit(GameEvent(EventType.GAME_START, {
        "config": config,
        "players": [(p.name, p.score) for p in game.players],
    }))

    console.print(f"\n  [bold]{t('msg.game_start')}[/bold]")
    console.print(f"  [dim]{t('msg.session_id', id=logger.session_id)}[/dim]")
    render_scores(console, [(p.name, p.score) for p in game.players])

    if not is_spectator:
        renderer.pause()

    round_count = 0

    while not game.is_finished:
        round_state = game.setup_round()
        label = round_label(game.round_wind, game.round_number, game.honba)
        console.print(f"\n  [bold cyan]{'='*50}[/bold cyan]")
        console.print(f"  [bold]{label}[/bold]")

        def get_action(player_idx, available):
            """Route action requests to appropriate player."""
            player = players[player_idx]

            if not isinstance(player, HumanPlayer):
                # Render current board state (from seat-0 perspective) before AI thinks,
                # so the human sees each board update immediately (e.g. their own discard,
                # previous AI discard) rather than waiting until their next turn.
                gv_render = build_game_view(
                    0, game.players,
                    game.round_wind, game.honba, game.riichi_sticks,
                    round_state.wall.remaining,
                    round_state.wall.dora_indicators,
                    label,
                    round_state.last_discard,
                    round_state.last_discard_player,
                    active_player=player_idx,
                )
                if not is_spectator:
                    renderer.render_game_view(gv_render)
                # Pace only a real AI turn (where the player must choose a
                # discard).  Reaction prompts after a discard have no
                # ``can_discard`` action; delaying each ron/pon/chi check
                # would stack one delay per opponent and make the human's
                # previous turn appear frozen for several seconds.
                if (ai_delay is not None and not is_spectator
                        and available.can_discard):
                    time.sleep(ai_delay.get_delay())

            gv = build_game_view(
                player_idx, game.players,
                game.round_wind, game.honba, game.riichi_sticks,
                round_state.wall.remaining,
                round_state.wall.dora_indicators,
                label,
                round_state.last_discard,
                round_state.last_discard_player,
                active_player=player_idx,
            )
            if is_spectator and logger.rounds:
                # Right at the newest state releases exactly one AI decision;
                # Left browses the immutable action prefix without rewinding
                # the live rules engine.
                from mahjong.ui.replay_screen import spectator_wait_for_right
                spectator_wait_for_right(
                    console, logger.rounds[-1], player_names, config.is_sanma)
            action = player.choose_action(gv, available)
            # Trainer review is not limited to the human seat. When helper
            # panels are enabled (H), retain a pre-action snapshot for every
            # AI discard/riichi decision so the next board render can explain
            # it just like the human's move.
            if (not is_spectator and not isinstance(player, HumanPlayer) and
                    action.action_type in (ActionType.DISCARD, ActionType.RIICHI)):
                renderer.render_discard_review(gv, available, action)
            # In spectator mode, expose the same efficiency review for every
            # AI decision. Use the AI's own GameView so hidden information is
            # not accidentally mixed with the observer's perspective.
            return action

        result = run_round(round_state, get_action)

        if result is None:
            console.print(f"  [red]{t('msg.round_error')}[/red]")
            break

        # MJAI engines (including Mortal) need an explicit round boundary to
        # clear their per-kyoku state before the next start_kyoku message.
        event_bus.emit(GameEvent(EventType.ROUND_END, {
            "result": result,
        }))

        logger.end_round(result)

        if is_spectator and logger.rounds:
            # One final interactive state exposes the win/draw result and lets
            # the spectator review any prior decision before continuing.
            from mahjong.ui.replay_screen import spectator_wait_for_right
            spectator_wait_for_right(
                console, logger.rounds[-1], player_names, config.is_sanma)

        # Show round result
        _show_round_result(game, result, player_names)

        game.advance_round(result)
        round_count += 1

        if not game.is_finished:
            if not is_spectator:
                renderer.pause()

    # Game end
    render_game_end(console, [(p.name, p.score) for p in game.players])

    # Save game log
    final_scores = {p.name: p.score for p in game.players}
    log_path = logger.save(final_scores)
    console.print(f"  [dim]{t('msg.log_saved', path=log_path)}[/dim]")


def _show_round_result(game, result, player_names):
    """Display the result of a round."""
    if result.is_draw:
        if result.draw_type == "exhaustive":
            tenpai_info = [
                (player_names[i], i in result.tenpai_players)
                for i in range(len(player_names))
            ]
            render_draw_screen(console, result.draw_type, tenpai_info)
        else:
            render_draw_screen(console, result.draw_type)
    else:
        # Announcement banners only (no yaku yet — shown after hands)
        for winner_idx, score_result in result.score_results:
            winner_name = player_names[winner_idx]
            loser_name = player_names[result.loser] if result.loser is not None else ""
            render_win_announcement(console, winner_name,
                                    score_result.is_tsumo, loser_name)

    # Hands first (god view)
    render_round_end_hands(
        console, game.players, player_names,
        result.winners, result.loser,
        ron_tile=result.win_tile,
    )

    # Yaku + point total for each winner
    if not result.is_draw:
        for _winner_idx, score_result in result.score_results:
            render_yaku_summary(console, score_result)

    # Merged score change + post-change score table
    current_scores = [p.score + result.score_changes[i]
                      for i, p in enumerate(game.players)]
    render_score_changes(console, player_names, result.score_changes, current_scores)


def main():
    """Main entry point."""
    try:
        set_language("zh")
        time_control = TIME_CONTROL_PRESETS[0]  # default: unlimited
        ai_delay = AI_DELAY_PRESETS[0]          # default: 1s
        while True:
            choice = show_menu()
            if choice == 0:
                console.print(f"\n  {t('msg.goodbye')}\n")
                break
            elif choice == 6:
                show_lan_menu(time_control, ai_delay)
                continue
            elif choice == 7:
                time_control, ai_delay = show_settings(time_control, ai_delay)
                continue
            elif choice == 8:
                from mahjong.ui.replay_screen import show_replay_browser
                show_replay_browser(console)
                continue
            elif choice == 10:
                from mahjong.analysis.early_plan_practice import run_early_plan_practice
                coach_player = None
                raw_coach_cmd = os.environ.get("MAHJONG_COACH_COMMAND", "").strip()
                if not raw_coach_cmd:
                    raw_coach_cmd = os.environ.get("MAHJONG_AI_COMMAND", "").strip()
                if not raw_coach_cmd:
                    bundled = os.path.abspath(os.path.join(
                        os.path.dirname(__file__), "..", "engines", "mortal", "run.sh"))
                    if os.path.isfile(bundled) and os.access(bundled, os.X_OK):
                        raw_coach_cmd = bundled
                if raw_coach_cmd:
                    try:
                        coach_cmd = shlex.split(raw_coach_cmd)
                        drill_timeout = float(os.environ.get("MAHJONG_COACH_TIMEOUT", 5.0))
                        coach_player = MjaiPlayer("MortalCoach", 0, coach_cmd, GreedyAI("Fallback"), response_timeout=drill_timeout)
                    except (ValueError, TypeError):
                        coach_player = None
                try:
                    run_early_plan_practice(console, coach_player=coach_player)
                finally:
                    if coach_player is not None:
                        coach_player.close()
                continue
            play_game(choice, time_control, ai_delay)
            console.print()
    except KeyboardInterrupt:
        console.print(f"\n\n  [dim]{t('msg.game_exit')}[/dim]\n")
    except EOFError:
        console.print(f"\n\n  [dim]{t('msg.game_exit')}[/dim]\n")
