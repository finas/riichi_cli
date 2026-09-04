"""Early Hand Route Plan Practice Mode (配牌构想与前巡实战特训).

Allows players to receive a dealt starting hand (配牌), formulate and declare an
opening strategic plan (e.g. Riichi/Pinfu, Tanyao, Flush, Yakuhai, Chiitoitsu, Defense),
and then play through the critical opening turns (1 to 6-8) with live Mortal
guidance and post-drill diagnosis.
"""

from dataclasses import dataclass, field
import os
import random
from typing import Callable, List, Optional, Tuple

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.padding import Padding
from mahjong.analysis.coach import AICoach, CoachSuggestion
from mahjong.core.hand import Hand
from mahjong.core.player_state import PlayerState, Wind
from mahjong.core.tile import ALL_TILES_136, Tile
from mahjong.core.wall import Wall
from mahjong.engine.action import Action, ActionType, AvailableActions
from mahjong.engine.round import RoundState
from mahjong.engine.event import EventBus, EventType, GameEvent
from mahjong.player.base import GameView, OpponentView
from mahjong.player.greedy_ai import GreedyAI
from mahjong.player.advisor import advise_discards, analyze_5_blocks, potential_yaku_names
from mahjong.rules.shanten import shanten
from mahjong.ui.i18n import t
from mahjong.ui.input_handler import _render_coach_suggestion
from mahjong.ui.tile_display import tile_to_display_str
from mahjong.ui.board_layout import tile_to_rich_text


ROUTE_PLAN_KEYS = {
    "helper.plan_menzen",
    "helper.plan_riichi",
    "helper.plan_pinfu",
    "helper.plan_tanyao",
    "helper.plan_open_yakuhai",
    "helper.plan_honitsu",
    "helper.plan_chinitsu",
    "helper.plan_chiitoi",
    "helper.plan_toitoi",
    "helper.plan_ittsu",
    "helper.plan_sanshoku",
    "helper.plan_dora_value",
}


@dataclass
class TurnRecord:
    turn: int
    draw_tile: Optional[Tile]
    user_discard: Tile
    mortal_discard: Tile
    mortal_q: Optional[float]
    mortal_prob: Optional[float]
    shanten_before: int
    shanten_after: int
    is_match: bool
    rating: str


@dataclass
class DrillReport:
    initial_shanten: int
    final_shanten: int
    declared_plan_key: str
    declared_plan_name: str
    turns_played: int
    records: List[TurnRecord]
    concordance_rate: float
    block_status_start: str
    block_status_end: str
    early_tenpai: bool = False
    ended_reason: str = ""
    route_recommended: bool = False


class EarlyPlanPracticeSession:
    """Manages an opening route planning drill session."""

    def __init__(self, console: Console, coach_player=None, max_turns: int = 6,
                 blind_mode: bool = True):
        self.console = console
        self.coach_player = coach_player
        self.ai_coach = AICoach()
        self.max_turns = max_turns
        self.blind_mode = blind_mode
        self.total_drills = 0
        self.total_matches = 0
        self.total_moves = 0
        self._active_plan_keys: set[str] = set()

    def get_candidate_plans(self, game_view: GameView) -> List[Tuple[str, str]]:
        """Return candidate tactical route plans tailored to the dealt hand."""
        hand = game_view.my_hand
        tiles = list(hand.closed_tiles)
        mock_advice = advise_discards(game_view, tiles, limit=1)
        active_plans = potential_yaku_names(game_view, mock_advice[0]) if mock_advice else ()

        recommended = [key for key in active_plans if key in ROUTE_PLAN_KEYS]
        if not recommended:
            recommended = ["helper.plan_riichi"]
        self._active_plan_keys = set(recommended)

        # Put routes detected from this hand first, then keep only a few
        # beginner-friendly fallback choices.
        candidate_keys = list(recommended[:3])
        for key in ("drill.custom_plan", "drill.fold_plan",
                    "helper.plan_riichi", "helper.plan_menzen"):
            if key not in candidate_keys and len(candidate_keys) < 5:
                candidate_keys.append(key)

        plans = []
        for k in candidate_keys:
            name = t(k)
            plans.append((k, name))
        return plans

    def run_drill(self, input_fn: Optional[Callable[[str], str]] = None) -> Optional[DrillReport]:
        """Run a single drill: Deal -> Plan -> Play 6 turns -> Report."""
        # 1. Setup simulated round
        wall = Wall(shuffle=True)
        player_names = [t("label.you"), t("ai.name_a"), t("ai.name_b"), t("ai.name_c")]
        event_bus = EventBus()

        players = []
        for i, name in enumerate(player_names):
            p = PlayerState(seat=i, name=name, score=25000)
            p.reset_for_round(Wind(i), i == 0)
            players.append(p)

        rs = RoundState(
            players=players,
            wall=wall,
            round_wind=Wind.EAST,
            honba=0,
            riichi_sticks=0,
            event_bus=event_bus,
        )
        if self.coach_player is not None and hasattr(self.coach_player, "bind_event_bus"):
            self.coach_player.bind_event_bus(event_bus)

        event_bus.emit(GameEvent(EventType.GAME_START, {
            "players": [(p.name, p.score) for p in players],
        }))

        rs.deal_tiles()

        # Human is dealer: draw 14th tile.
        human_state = rs.players[0]
        rs.process_draw(0)

        # Build game view
        gv = self._build_game_view(rs, 0)
        start_shanten, start_counts, _ = self._best_post_discard_state(human_state.hand)
        start_blocks = analyze_5_blocks(start_counts)
        candidate_plans = self.get_candidate_plans(gv)

        # Phase 1: Formulate Route Plan
        self.console.print()
        self.console.print(Panel(
            f"[bold cyan]{t('drill.title')}[/bold cyan]  "
            f"[dim]({t('label.dora')}: {tile_to_display_str(wall.dora_indicators[0])}  |  "
            f"{t('label.round_wind')}: {t('wind.east')}  |  "
            f"{t('label.seat_wind')}: {t('wind.east')})[/dim]",
            border_style="cyan",
        ))

        self.console.print(f"\n  [bold]{t('drill.phase_plan')}[/bold]")
        self._print_hand_line(human_state.hand)
        self.console.print(
            f"  [dim]{t('drill.best_post_shanten')}: {start_shanten}  |  "
            f"{t(start_blocks.status_key)}[/dim]\n"
        )

        self.console.print(f"  {t('drill.prompt_plan', n=len(candidate_plans))}")
        for idx, (key, name) in enumerate(candidate_plans, 1):
            marker = f" [{t('drill.recommended')}]" if key in self._active_plan_keys else ""
            self.console.print(f"    {idx}. {name}{marker}")
        self.console.print()

        # Read plan choice
        chosen_plan_idx = 0
        prompt_str = f"  > {t('prompt.choose_mode', n=len(candidate_plans))} "
        while True:
            try:
                raw = input_fn(prompt_str) if input_fn else self.console.input(prompt_str)
            except (EOFError, KeyboardInterrupt, StopIteration):
                return None
            raw = str(raw).strip().lower()
            if raw == "q":
                return None
            try:
                val = int(raw)
                if 1 <= val <= len(candidate_plans):
                    chosen_plan_idx = val - 1
                    break
            except (ValueError, EOFError):
                pass
            self.console.print(f"  [red]{t('prompt.invalid_input')}[/red]")

        declared_key, declared_name = candidate_plans[chosen_plan_idx]
        self.console.print(f"\n  [bold green]✓ {t('drill.declared_plan')} {declared_name}[/bold green]\n")

        # Phase 2: Play Opening Turns (up to max_turns)
        records: List[TurnRecord] = []
        turn = 1
        early_tenpai = False
        ended_reason = ""

        ai_opponents = [GreedyAI(players[i].name) for i in range(1, 4)]

        while turn <= self.max_turns:
            current_hand = human_state.hand
            human_actions = rs.get_draw_actions(0)
            best_post_shanten, best_post_counts, _ = self._best_post_discard_state(current_hand)

            # A 14-tile shanten of 0 still requires a discard. Only stop
            # immediately for a complete hand; tenpai is checked after the
            # user's actual discard below.
            if human_actions.can_tsumo:
                early_tenpai = True
                ended_reason = "tsumo"
                self.console.print(f"\n  [bold yellow]★ {t('drill.tsumo_available')}[/bold yellow]")
                break

            gv = self._build_game_view(rs, 0)
            avail = AvailableActions(player=0)
            avail.can_discard = list(current_hand.closed_tiles)

            suggestion: Optional[CoachSuggestion] = self.ai_coach.get_coach_suggestion(
                gv, avail, external_ai=self.coach_player, engine_name="Mortal"
            )

            # Display turn state
            self.console.print(f"  [bold cyan]─── {t('drill.turn_info', turn=turn, max_turns=self.max_turns)} ───[/bold cyan]")
            self.console.print(f"  [dim]{t('drill.declared_plan')} {declared_name}[/dim]")
            self._print_hand_line(current_hand)

            blocks = analyze_5_blocks(best_post_counts)
            self.console.print(
                f"  [dim]{t('drill.best_post_shanten')}: {best_post_shanten}  |  "
                f"{t(blocks.status_key)}[/dim]"
            )

            if suggestion and not self.blind_mode:
                _render_coach_suggestion(self.console, suggestion)
            elif suggestion and self.blind_mode:
                self.console.print(f"  [dim]{t('drill.blind_hint')}[/dim]")

            # Get user discard
            tiles = list(current_hand.closed_tiles)
            tiles.sort(key=lambda t: (t.index34, t.is_red))
            if current_hand.draw_tile and current_hand.draw_tile in tiles:
                tiles.remove(current_hand.draw_tile)
                tiles.append(current_hand.draw_tile)

            discard_prompt = f"  > {t('prompt.choose_discard', n=len(tiles))} [q: quit]: "
            chosen_tile: Optional[Tile] = None
            while True:
                try:
                    raw = input_fn(discard_prompt) if input_fn else self.console.input(discard_prompt)
                except (EOFError, KeyboardInterrupt, StopIteration):
                    return None
                raw = str(raw).strip().lower()
                if raw == "q":
                    return None
                try:
                    idx = int(raw) - 1
                    if 0 <= idx < len(tiles):
                        chosen_tile = tiles[idx]
                        break
                except (ValueError, EOFError):
                    pass
                self.console.print(f"  [red]{t('prompt.invalid_input')}[/red]")

            # Record turn before process_discard clears hand.draw_tile.
            draw_tile = current_hand.draw_tile
            mortal_tile = suggestion.best_tile if suggestion and suggestion.best_tile else chosen_tile
            is_match = (
                chosen_tile.index34 == mortal_tile.index34
                and chosen_tile.is_red == mortal_tile.is_red
            )
            q_val = suggestion.best_eval.q_value if suggestion and suggestion.best_eval else None
            p_val = suggestion.best_eval.mortal_prob if suggestion and suggestion.best_eval else None

            rating = t("helper.review_best") if is_match else t("helper.review_efficiency")

            if suggestion and self.blind_mode:
                self.console.print(f"\n  [dim]{t('drill.reveal')}[/dim]")
                _render_coach_suggestion(self.console, suggestion)
            self._render_choice_feedback(gv, chosen_tile, suggestion)

            # Execute discard
            rs.process_discard(0, chosen_tile)
            after_shanten = shanten(human_state.hand.to_34_array())

            records.append(TurnRecord(
                turn=turn,
                draw_tile=draw_tile,
                user_discard=chosen_tile,
                mortal_discard=mortal_tile,
                mortal_q=q_val,
                mortal_prob=p_val,
                shanten_before=best_post_shanten,
                shanten_after=after_shanten,
                is_match=is_match,
                rating=rating,
            ))

            if is_match:
                self.total_matches += 1
            self.total_moves += 1

            if after_shanten <= 0:
                early_tenpai = True
                ended_reason = "tenpai"
                self.console.print(
                    f"\n  [bold yellow]★ {t('msg.tenpai')}! "
                    f"{t('drill.declared_plan')} {declared_name} "
                    f"{t('drill.route_reached')}[/bold yellow]"
                )
                break

            # Opponents act (simulated turns)
            ended_reason = ""
            for opp_idx in range(1, 4):
                ended_reason = self._simulate_opponent_turn(
                    rs, ai_opponents[opp_idx - 1], opp_idx
                ) or ""
                if ended_reason:
                    break

            if ended_reason:
                if ended_reason == "opponent_tsumo":
                    self.console.print(f"\n  [yellow]{t('drill.opponent_tsumo')}[/yellow]")
                break

            # Human draws for next turn
            if not rs.wall.is_empty:
                rs.process_draw(0)
            else:
                ended_reason = "exhaustive"
                break
            turn += 1

        # Phase 3: Final Report Card
        if records and human_state.hand.draw_tile is not None:
            end_shanten, final_counts, _ = self._best_post_discard_state(human_state.hand)
        else:
            final_counts = human_state.hand.to_34_array()
            end_shanten = shanten(final_counts)
        end_blocks = analyze_5_blocks(final_counts)
        matches = sum(1 for r in records if r.is_match)
        rate = (matches / len(records) * 100.0) if records else 0.0

        report = DrillReport(
            initial_shanten=start_shanten,
            final_shanten=end_shanten,
            declared_plan_key=declared_key,
            declared_plan_name=declared_name,
            turns_played=len(records),
            records=records,
            concordance_rate=rate,
            block_status_start=t(start_blocks.status_key),
            block_status_end=t(end_blocks.status_key),
            early_tenpai=early_tenpai,
            ended_reason=ended_reason,
            route_recommended=declared_key in self._active_plan_keys,
        )

        event_bus.emit(GameEvent(EventType.ROUND_END, {
            "result": None,
        }))

        self._render_report_card(report)
        self.total_drills += 1
        return report

    def _render_report_card(self, report: DrillReport) -> None:
        """Display the summary report card for the opening drill."""
        self.console.print()
        table = Table(title=t("drill.report_title"), border_style="cyan")
        table.add_column("#", justify="right", style="dim")
        table.add_column(t("drill.turn_info", turn=1, max_turns=self.max_turns).split()[0])
        table.add_column(t("helper.review_chosen"))
        table.add_column(t("coach.title"))
        table.add_column("Q / P%")
        table.add_column(t("helper.shanten"), justify="right")
        table.add_column(t("helper.review_good"))

        for r in report.records:
            match_style = "bold green" if r.is_match else "yellow"
            qp_str = f"Q: {r.mortal_q:+.2f} | P: {r.mortal_prob:.1f}%" if r.mortal_q is not None else "-"
            table.add_row(
                str(r.turn),
                f"{r.turn}",
                tile_to_display_str(r.user_discard),
                tile_to_display_str(r.mortal_discard),
                qp_str,
                f"{r.shanten_before} → {r.shanten_after}",
                f"[{match_style}]{r.rating}[/{match_style}]",
            )

        self.console.print(table)

        summary_text = (
            f"[bold]{t('drill.declared_plan')}[/bold] [cyan]{report.declared_plan_name}[/cyan]\n"
            f"[bold]{t('drill.shanten_progress')}[/bold] {report.initial_shanten} → [bold yellow]{report.final_shanten}[/bold yellow] {t('helper.shanten')}\n"
            f"[bold]{t('drill.blocks_result')}[/bold] {report.block_status_start} → [bold green]{report.block_status_end}[/bold green]\n"
            f"[bold]{t('drill.concordance_rate')}[/bold] [bold cyan]{report.concordance_rate:.1f}%[/bold cyan] "
            f"({sum(1 for r in report.records if r.is_match)}/{len(report.records)})"
        )
        route_status = (
            t("drill.route_supported") if report.route_recommended
            else t("drill.route_experimental")
        )
        summary_text += f"\n[bold]{t('drill.route_status')}[/bold] {route_status}"
        if report.ended_reason == "opponent_tsumo":
            summary_text += f"\n[yellow]{t('drill.opponent_tsumo')}[/yellow]"
        self.console.print(Panel(summary_text, border_style="green", padding=(0, 2)))

    @staticmethod
    def _best_post_discard_state(hand: Hand) -> Tuple[int, List[int], Optional[Tile]]:
        """Return the best 13-tile state reachable by one legal discard."""
        counts = hand.to_34_array()
        best = None
        for tile in hand.closed_tiles:
            after = list(counts)
            after[tile.index34] -= 1
            candidate = (shanten(after), after, tile)
            if best is None or (candidate[0], candidate[2].index34, candidate[2].id) < (
                    best[0], best[2].index34, best[2].id):
                best = candidate
        if best is None:
            return shanten(counts), counts, None
        return best

    def _render_choice_feedback(self, game_view: GameView, chosen_tile: Tile,
                                suggestion: Optional[CoachSuggestion]) -> None:
        """Explain the chosen discard in beginner-friendly shape terms."""
        if suggestion is None or suggestion.best_eval is None:
            return
        chosen_advice = advise_discards(game_view, [chosen_tile], limit=1)
        if not chosen_advice:
            return
        chosen = chosen_advice[0]
        best = suggestion.best_eval
        if suggestion.best_tile and chosen_tile.index34 == suggestion.best_tile.index34 \
                and chosen_tile.is_red == suggestion.best_tile.is_red:
            message = t("drill.feedback_best")
        elif chosen.shanten < best.shanten:
            message = t("drill.feedback_better")
        elif chosen.shanten == best.shanten and chosen.ukeire == best.ukeire:
            message = t("drill.feedback_equivalent")
        elif chosen.shanten == best.shanten:
            message = t("drill.feedback_same_shanten", n=max(0, best.ukeire - chosen.ukeire))
        else:
            message = t("drill.feedback_shanten_loss", n=max(0, chosen.shanten - best.shanten))
        self.console.print(f"  [cyan]{message}[/cyan]")

    def _simulate_opponent_turn(self, rs: RoundState, ai: GreedyAI,
                                player_idx: int) -> Optional[str]:
        """Simulate one opponent turn using the round engine's legal actions."""
        draw = rs.process_draw(player_idx)
        if draw is None:
            return "exhaustive"

        game_view = self._build_game_view(rs, player_idx)
        available = rs.get_draw_actions(player_idx)
        action = ai.choose_action(game_view, available)
        if not isinstance(action, Action) or not available.contains(action):
            action = Action(ActionType.DISCARD, player_idx, tile=draw)

        if action.action_type == ActionType.TSUMO:
            return "opponent_tsumo"
        if action.action_type == ActionType.RIICHI:
            discard_tile = action.riichi_discard or action.tile or draw
            rs.process_riichi(player_idx, discard_tile)
            return None
        if action.action_type == ActionType.ANKAN:
            group = next(
                (tiles for tiles in available.can_ankan
                 if tiles and tiles[0].index34 == action.tile.index34),
                None,
            )
            if group:
                rs.process_ankan(player_idx, group)
                replacement = rs.process_rinshan_draw(player_idx)
                if replacement is None:
                    return "exhaustive"
                rs.process_discard(player_idx, replacement)
                return None
        if action.action_type == ActionType.DISCARD and action.tile is not None:
            rs.process_discard(player_idx, action.tile)
        else:
            rs.process_discard(player_idx, draw)
        return None

    def _build_game_view(self, rs: RoundState, seat: int) -> GameView:
        p = rs.players[seat]
        opponents = [
            OpponentView(
                seat=opp.seat,
                name=opp.name,
                score=opp.score,
                seat_wind=opp.seat_wind,
                is_dealer=opp.is_dealer,
                is_riichi=opp.hand.is_riichi,
                melds=list(opp.hand.melds),
                discard_pool=list(opp.hand.discard_pool),
                discard_called=list(opp.hand.discard_called),
                discard_is_tsumogiri=list(opp.hand.discard_is_tsumogiri),
                num_closed_tiles=len(opp.hand.closed_tiles),
                riichi_discard_index=opp.hand.riichi_discard_index,
            )
            for opp in rs.players if opp.seat != seat
        ]
        return GameView(
            my_hand=p.hand,
            my_seat=seat,
            my_wind=p.seat_wind,
            round_wind=rs.round_wind,
            my_score=p.score,
            is_dealer=p.is_dealer,
            opponents=opponents,
            remaining_tiles=rs.wall.remaining,
            dora_indicators=rs.wall.dora_indicators,
            last_discard=rs.last_discard,
            last_discard_player=rs.last_discard_player,
            active_player=seat,
        )

    def _print_hand_line(self, hand: Hand) -> None:
        tiles = list(hand.closed_tiles)
        tiles.sort(key=lambda t: (t.index34, t.is_red))
        draw_tile = hand.draw_tile
        if draw_tile and draw_tile in tiles:
            tiles.remove(draw_tile)
        else:
            draw_tile = None

        grid = Table.grid(padding=(0, 2))
        for _ in range(len(tiles)):
            grid.add_column(justify="center")

        num_cells = [Text(str(i + 1), style="bold cyan") for i in range(len(tiles))]
        tile_cells = [tile_to_rich_text(tile) for tile in tiles]

        if draw_tile:
            grid.add_column(justify="center")  # gap column
            grid.add_column(justify="center")  # draw tile column
            num_cells.append(Text(" "))
            num_cells.append(Text(str(len(tiles) + 1), style="bold yellow"))
            tile_cells.append(Text(" "))
            tile_cells.append(tile_to_rich_text(draw_tile, highlight=False))

        grid.add_row(*num_cells)
        grid.add_row(*tile_cells)

        self.console.print(f"  {t('label.hand_tiles')}")
        self.console.print(Padding(grid, (0, 0, 0, 4)))

def run_early_plan_practice(console: Console, coach_player=None,
                            blind_mode: bool = True) -> None:
    """Interactive loop for early hand route planning drills."""
    session = EarlyPlanPracticeSession(
        console, coach_player=coach_player, max_turns=6, blind_mode=blind_mode
    )
    while True:
        report = session.run_drill()
        if report is None:
            break
        console.print()
        try:
            next_cmd = console.input(f"  {t('drill.next_prompt')} ").strip().lower()
            if next_cmd == "q":
                break
        except (EOFError, KeyboardInterrupt):
            break
