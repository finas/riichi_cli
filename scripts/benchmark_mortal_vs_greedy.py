"""Benchmark: 2 Mortal vs 2 GreedyAI Head-to-Head Tournament.

Runs multi-game matches with alternating seats and collects statistical
benchmarks comparing the deep reinforcement learning model (Mortal)
against the heuristic baseline (GreedyAI).

Usage:
    python scripts/benchmark_mortal_vs_greedy.py --games 100 --mode tonpuu
    python scripts/benchmark_mortal_vs_greedy.py --games 10 --mode hanchan
"""

import argparse
from collections import defaultdict
from dataclasses import dataclass, field
import os
import random
import shlex
import sys
import time
from typing import Callable, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, BarColumn, TextColumn, TimeElapsedColumn, TimeRemainingColumn

from mahjong.engine.action import Action, ActionType, AvailableActions
from mahjong.engine.event import EventBus, EventType, GameEvent
from mahjong.engine.game import GameConfig, GameState
from mahjong.engine.round import RoundResult, RoundState, run_round
from mahjong.player.base import build_game_view, Player
from mahjong.player.mjai_player import MjaiPlayer

from mahjong.player.greedy_ai import GreedyAI, RefinedGreedyAI

@dataclass
class TeamStats:
    name: str
    games_played: int = 0
    total_rounds: int = 0
    placements: List[int] = field(default_factory=lambda: [0, 0, 0, 0])
    total_score: int = 0
    wins: int = 0
    deal_ins: int = 0
    total_win_points: int = 0
    riichi_declarations: int = 0
    melds_called: int = 0

    @property
    def avg_placement(self) -> float:
        total = sum(self.placements)
        if total == 0:
            return 0.0
        return sum((i + 1) * count for i, count in enumerate(self.placements)) / total

    @property
    def avg_rank(self) -> float:
        return self.avg_placement

    @property
    def avg_score(self) -> float:
        total = sum(self.placements)
        return (self.total_score / total) if total > 0 else 0.0

    @property
    def win_rate(self) -> float:
        player_rounds = self.total_rounds * 2
        return (self.wins / player_rounds * 100.0) if player_rounds > 0 else 0.0

    @property
    def deal_in_rate(self) -> float:
        player_rounds = self.total_rounds * 2
        return (self.deal_ins / player_rounds * 100.0) if player_rounds > 0 else 0.0

    @property
    def avg_win_points(self) -> float:
        return (self.total_win_points / self.wins) if self.wins > 0 else 0.0

    @property
    def riichi_rate(self) -> float:
        player_rounds = self.total_rounds * 2
        return (self.riichi_declarations / player_rounds * 100.0) if player_rounds > 0 else 0.0


def resolve_mortal_cmd() -> Optional[List[str]]:
    raw_cmd = os.environ.get("MAHJONG_AI_COMMAND", "").strip()
    if not raw_cmd:
        bundled = os.path.abspath(os.path.join(
            os.path.dirname(__file__), "..", "engines", "mortal", "run.sh"))
        if os.path.isfile(bundled) and os.access(bundled, os.X_OK):
            raw_cmd = bundled
    if not raw_cmd:
        return None
    try:
        command = shlex.split(raw_cmd)
    except ValueError:
        return None
    return command or None


def play_single_benchmark_game(game_idx: int, is_tonpuu: bool, mortal_cmd: Optional[List[str]],
                               matchup: str = "mortal_vs_refined",
                               on_round: Optional[Callable[[str], None]] = None) -> Tuple[List[int], List[RoundResult], List[str]]:
    """Play one 4-player game with seat rotation and configurable matchups."""
    config = GameConfig(
        num_players=4,
        is_sanma=False,
        is_tonpuu=is_tonpuu,
        starting_score=25000,
    )
    event_bus = EventBus()

    if matchup == "mortal_vs_baseline":
        team1, team2 = "Mortal", "BaselineGreedy"
    elif matchup == "refined_vs_baseline":
        team1, team2 = "RefinedGreedy", "BaselineGreedy"
    else:
        team1, team2 = "Mortal", "RefinedGreedy"

    base_teams = [team1, team2, team1, team2]
    base_names = [f"{team1}-A", f"{team2}-A", f"{team1}-B", f"{team2}-B"]

    # Rotate seats by game_idx to prevent position/dealer bias
    shift = game_idx % 4
    team_order = [base_teams[(i + shift) % 4] for i in range(4)]
    name_order = [base_names[(i + shift) % 4] for i in range(4)]

    game = GameState(config, name_order, event_bus)

    # Instantiate players
    players: List[Player] = []
    for seat, (team, name) in enumerate(zip(team_order, name_order)):
        if team == "Mortal" and mortal_cmd:
            p = MjaiPlayer(name, seat, mortal_cmd, GreedyAI(f"FB-{name}"), response_timeout=15.0)
            p.bind_event_bus(event_bus)
            players.append(p)
        elif team == "BaselineGreedy":
            players.append(GreedyAI(name))
        elif team == "RefinedGreedy":
            players.append(RefinedGreedyAI(name))
        else:
            players.append(GreedyAI(name))

    # Emit initial game start
    event_bus.emit(GameEvent(EventType.GAME_START, {
        "config": config,
        "players": [(p.name, p.score) for p in game.players],
    }))

    round_results: List[RoundResult] = []
    max_rounds = 16
    r_count = 0

    try:
        while not game.is_finished and r_count < max_rounds:
            r_count += 1
            round_state = game.setup_round()
            if on_round:
                on_round(f"Game {game_idx + 1} [{round_state.round_wind.kanji}{round_state.honba + 1}局]")

            def get_action(p_idx: int, available: AvailableActions) -> Action:
                gv = build_game_view(
                    player_idx=p_idx,
                    players=round_state.players,
                    round_wind=round_state.round_wind,
                    honba=round_state.honba,
                    riichi_sticks=round_state.riichi_sticks,
                    remaining_tiles=round_state.wall.remaining,
                    dora_indicators=round_state.wall.dora_indicators,
                    round_label=f"{round_state.round_wind.name} {round_state.honba}",
                    last_discard=round_state.last_discard,
                    last_discard_player=round_state.last_discard_player,
                    active_player=p_idx,
                )
                return players[p_idx].choose_action(gv, available)

            res = run_round(round_state, get_action)
            round_results.append(res)
            game.advance_round(res)

            event_bus.emit(GameEvent(EventType.ROUND_END, {"result": res}))
    finally:
        for p in players:
            if hasattr(p, "close"):
                p.close()

    final_scores = [p.score for p in game.players]
    return final_scores, round_results, team_order


def run_benchmark(num_games: int = 100, is_tonpuu: bool = True, seed: int = 42,
                  matchup: str = "mortal_vs_refined"):
    random.seed(seed)
    console = Console()

    mortal_cmd = resolve_mortal_cmd() if "mortal" in matchup else None
    if "mortal" in matchup and not mortal_cmd:
        console.print("[red]Error: Mortal engine not found at engines/mortal/run.sh[/red]")
        return

    if matchup == "mortal_vs_baseline":
        team1_stats = TeamStats(name="Mortal (Deep RL)")
        team2_stats = TeamStats(name="Baseline GreedyAI (Legacy)")
        team1_key, team2_key = "Mortal", "BaselineGreedy"
    elif matchup == "refined_vs_baseline":
        team1_stats = TeamStats(name="Refined GreedyAI (Safety)")
        team2_stats = TeamStats(name="Baseline GreedyAI (Legacy)")
        team1_key, team2_key = "RefinedGreedy", "BaselineGreedy"
    else:
        team1_stats = TeamStats(name="Mortal (Deep RL)")
        team2_stats = TeamStats(name="Refined GreedyAI (Safety)")
        team1_key, team2_key = "Mortal", "RefinedGreedy"

    mode_label = "东风战 (Tonpuu)" if is_tonpuu else "半庄 (Hanchan)"
    console.print(Panel(
        f"[bold cyan]Mahjong Benchmark: {team1_stats.name} vs {team2_stats.name}[/bold cyan]\n"
        f"[dim]Total Games: {num_games}  |  Mode: {mode_label}  |  Matchup: {matchup}  |  Seed: {seed}[/dim]",
        border_style="cyan",
        padding=(1, 2)
    ))

    t_start = time.monotonic()

    with Progress(
        TextColumn("[bold cyan]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TextColumn("• Game {task.completed}/{task.total}"),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Running matches...", total=num_games)

        for g in range(num_games):
            def _on_round(msg: str):
                progress.update(task, description=f"[bold cyan]{msg}")

            scores, results, team_order = play_single_benchmark_game(
                g, is_tonpuu, mortal_cmd, matchup=matchup, on_round=_on_round
            )
            progress.advance(task)

            # Record round events
            for res in results:
                team1_stats.total_rounds += 1
                team2_stats.total_rounds += 1

                for w in res.winners:
                    team = team_order[w]
                    if team == team1_key:
                        team1_stats.wins += 1
                    else:
                        team2_stats.wins += 1

                if res.loser is not None:
                    loser_team = team_order[res.loser]
                    if loser_team == team1_key:
                        team1_stats.deal_ins += 1
                    else:
                        team2_stats.deal_ins += 1

                for seat, sres in res.score_results:
                    pts = sres.total_points
                    team = team_order[seat]
                    if team == team1_key:
                        team1_stats.total_win_points += pts
                    else:
                        team2_stats.total_win_points += pts

            # Record game placements
            ranked = sorted([(score, seat) for seat, score in enumerate(scores)], key=lambda x: -x[0])
            for rank, (score, seat) in enumerate(ranked):
                team = team_order[seat]
                target_stats = team1_stats if team == team1_key else team2_stats
                target_stats.placements[rank] += 1
                target_stats.total_score += score

            team1_stats.games_played += 1
            team2_stats.games_played += 1

            t1_total = sum(team1_stats.placements)
            t1_avg = team1_stats.avg_placement if t1_total else 0.0
            print(f"[Progress] Game {g+1:3d}/{num_games} | {team1_key} 1st: {team1_stats.placements[0]:2d}, 4th: {team1_stats.placements[3]:2d}, AvgRank: {t1_avg:.2f} | Time: {time.monotonic() - t_start:.1f}s", flush=True)
    elapsed = time.monotonic() - t_start

    # Render results table
    console.print()
    table = Table(
        title=f"📊 Mortal vs GreedyAI Benchmark ({num_games} Games, Total Time: {elapsed:.1f}s)",
        border_style="cyan",
        show_header=True,
    )
    table.add_column("Team / AI Model", style="bold")
    table.add_column("1st (%)", justify="right", style="bold green")
    table.add_column("2nd (%)", justify="right", style="cyan")
    table.add_column("3rd (%)", justify="right", style="yellow")
    table.add_column("4th (%)", justify="right", style="bold red")
    table.add_column("Avg Rank", justify="right", style="bold magenta")
    table.add_column("Avg Score", justify="right")
    table.add_column("和了率 (Win %)", justify="right", style="green")
    table.add_column("放铳率 (Deal-in %)", justify="right", style="red")
    table.add_column("和铳比 (Ratio)", justify="right", style="bold yellow")
    table.add_column("平均打点 (Avg Win)", justify="right")

    for s in (team1_stats, team2_stats):
        total_p = sum(s.placements)
        p1 = f"{s.placements[0] / total_p * 100:.1f}%" if total_p else "0%"
        p2 = f"{s.placements[1] / total_p * 100:.1f}%" if total_p else "0%"
        p3 = f"{s.placements[2] / total_p * 100:.1f}%" if total_p else "0%"
        p4 = f"{s.placements[3] / total_p * 100:.1f}%" if total_p else "0%"
        ratio_str = f"{s.win_rate / s.deal_in_rate:.2f}" if s.deal_in_rate > 0 else "N/A"

        table.add_row(
            s.name,
            p1, p2, p3, p4,
            f"{s.avg_placement:.2f}",
            f"{s.avg_score:+,.0f}",
            f"{s.win_rate:.1f}%",
            f"{s.deal_in_rate:.1f}%",
            ratio_str,
            f"{s.avg_win_points:,.0f}",
        )

    console.print(table)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Benchmark Mahjong AI Matchups")
    parser.add_argument("--games", type=int, default=100, help="Number of games to simulate")
    parser.add_argument("--mode", choices=["tonpuu", "hanchan"], default="tonpuu", help="Game mode")
    parser.add_argument("--matchup", choices=["mortal_vs_refined", "mortal_vs_baseline", "refined_vs_baseline"], default="mortal_vs_refined", help="Matchup pair to simulate")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    args = parser.parse_args()

    run_benchmark(num_games=args.games, is_tonpuu=(args.mode == "tonpuu"), seed=args.seed, matchup=args.matchup)
