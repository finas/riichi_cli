"""Simulate offline Mahjong matches between AI players.

Runs N full matches (tonpuusen or hanchan, 4-player or 3-player) with fixed seeds
and computes playing metrics (win rate, deal-in rate, tenpai rate, placement distribution).

Usage:
    python scripts/simulate_ai.py --games 20 --mode hanchan
    python scripts/simulate_ai.py --games 20 --players 3 --mode tonpuu
"""

import argparse
import os
import random
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mahjong.core.player_state import PlayerState, Wind
from mahjong.engine.action import Action, ActionType, AvailableActions
from mahjong.engine.event import EventBus
from mahjong.engine.game import GameConfig, GameState
from mahjong.engine.round import RoundState, RoundResult, run_round
from mahjong.player.base import build_game_view
from mahjong.player.greedy_ai import GreedyAI
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, BarColumn, TextColumn, TimeElapsedColumn, TimeRemainingColumn

@dataclass
class SimulationStats:
    """Metrics accumulated across simulated games."""
    total_games: int = 0
    total_rounds: int = 0
    num_players: int = 4
    
    # Per-player stats: seat -> count
    wins: Dict[int, int] = field(default_factory=lambda: defaultdict(int))
    deal_ins: Dict[int, int] = field(default_factory=lambda: defaultdict(int))
    tsumo_wins: Dict[int, int] = field(default_factory=lambda: defaultdict(int))
    ron_wins: Dict[int, int] = field(default_factory=lambda: defaultdict(int))
    tenpai_draws: Dict[int, int] = field(default_factory=lambda: defaultdict(int))
    total_win_points: Dict[int, int] = field(default_factory=lambda: defaultdict(int))
    placements: Dict[int, List[int]] = field(default_factory=lambda: defaultdict(lambda: [0]*4))


def make_ai_action_getter(round_state: RoundState, ai_agents: List[GreedyAI]):
    """Create action callback for engine run_round."""
    def get_player_action(player_idx: int, available: AvailableActions) -> Action:
        gv = build_game_view(
            player_idx=player_idx,
            players=round_state.players,
            round_wind=round_state.round_wind,
            honba=round_state.honba,
            riichi_sticks=round_state.riichi_sticks,
            remaining_tiles=round_state.wall.remaining,
            dora_indicators=round_state.wall.dora_indicators,
            round_label=f"{round_state.round_wind.name} {round_state.honba}",
        )
        return ai_agents[player_idx].choose_action(gv, available)
    return get_player_action


def run_single_simulation(num_players: int, is_tonpuu: bool, shift: int = 0) -> Tuple[List[int], List[RoundResult], List[int]]:
    """Simulate a single full match with seat rotation."""
    config = GameConfig(
        num_players=num_players,
        is_sanma=(num_players == 3),
        is_tonpuu=is_tonpuu,
        starting_score=35000 if num_players == 3 else 25000,
    )
    event_bus = EventBus()
    seat_to_agent = [(i + shift) % num_players for i in range(num_players)]
    player_names = [f"AI_{seat_to_agent[i]}" for i in range(num_players)]
    game = GameState(config, player_names, event_bus)
    ai_agents = [GreedyAI(name=player_names[i]) for i in range(num_players)]

    round_results = []
    max_rounds = 24
    r_count = 0

    while not game.is_finished and r_count < max_rounds:
        r_count += 1
        round_state = game.setup_round()
        action_getter = make_ai_action_getter(round_state, ai_agents)
        res = run_round(round_state, action_getter)
        round_results.append(res)
        game.advance_round(res)

    return game.final_scores or [p.score for p in game.players], round_results, seat_to_agent

def simulate_matches(num_games: int, num_players: int = 4, is_tonpuu: bool = False, seed: int = 42) -> SimulationStats:
    """Run batch match simulations."""
    random.seed(seed)
    stats = SimulationStats(num_players=num_players)
    console = Console()

    with Progress(
        TextColumn("[bold cyan]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TextColumn("• Game {task.completed}/{task.total}"),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Simulating matches...", total=num_games)
        for g in range(num_games):
            shift = g % num_players
            scores, results, seat_to_agent = run_single_simulation(num_players=num_players, is_tonpuu=is_tonpuu, shift=shift)
            progress.advance(task)
            stats.total_games += 1
            stats.total_rounds += len(results)
            for res in results:
                for w in res.winners:
                    orig_agent = seat_to_agent[w]
                    stats.wins[orig_agent] += 1
                    if res.loser is None:
                        stats.tsumo_wins[orig_agent] += 1
                    else:
                        stats.ron_wins[orig_agent] += 1
                if res.loser is not None:
                    orig_loser = seat_to_agent[res.loser]
                    stats.deal_ins[orig_loser] += 1
                for t in res.tenpai_players:
                    orig_t = seat_to_agent[t]
                    stats.tenpai_draws[orig_t] += 1
                for seat, sres in res.score_results:
                    orig_seat = seat_to_agent[seat]
                    stats.total_win_points[orig_seat] += sres.total_points

            # Rank placements
            ranked = sorted([(score, i) for i, score in enumerate(scores)], key=lambda x: -x[0])
            for rank, (_score, seat) in enumerate(ranked):
                orig_agent = seat_to_agent[seat]
                stats.placements[orig_agent][rank] += 1
            print(f"[Progress] Game {g+1:3d}/{num_games} | Total Rounds: {stats.total_rounds} | Winner: AI {seat_to_agent[ranked[0][1]]} ({ranked[0][0]} pts)", flush=True)

    return stats


def print_simulation_report(stats: SimulationStats, title: str = "Mahjong AI Match Simulation"):
    """Format and print full rich table simulation report."""
    console = Console()
    
    table = Table(title=f"🎲 {title} ({stats.total_games} Games, {stats.total_rounds} Rounds)")
    table.add_column("Seat / AI", justify="center", style="bold cyan")
    table.add_column("Win Rate (和了)", justify="right", style="bold green")
    table.add_column("Deal-in (放铳)", justify="right", style="bold red")
    table.add_column("Avg Points (打点)", justify="right", style="yellow")
    table.add_column("1st (%)", justify="right", style="green")
    table.add_column("2nd (%)", justify="right", style="cyan")
    table.add_column("3rd (%)", justify="right", style="blue")
    if stats.num_players == 4:
        table.add_column("4th (%)", justify="right", style="magenta")
    table.add_column("Avg Rank (顺位)", justify="right", style="bold white")

    for i in range(stats.num_players):
        n_rounds = max(1, stats.total_rounds)
        n_games = max(1, stats.total_games)
        
        w_rate = (stats.wins[i] / n_rounds) * 100
        d_rate = (stats.deal_ins[i] / n_rounds) * 100
        avg_pts = (stats.total_win_points[i] / max(1, stats.wins[i])) if stats.wins[i] > 0 else 0
        
        p_ranks = stats.placements[i]
        p1 = (p_ranks[0] / n_games) * 100
        p2 = (p_ranks[1] / n_games) * 100
        p3 = (p_ranks[2] / n_games) * 100
        p4 = (p_ranks[3] / n_games) * 100 if stats.num_players == 4 else 0.0

        avg_rank = sum((r + 1) * count for r, count in enumerate(p_ranks)) / n_games

        if stats.num_players == 4:
            table.add_row(
                f"AI {i}",
                f"{w_rate:.1f}% ({stats.wins[i]})",
                f"{d_rate:.1f}% ({stats.deal_ins[i]})",
                f"{avg_pts:.0f}",
                f"{p1:.1f}%",
                f"{p2:.1f}%",
                f"{p3:.1f}%",
                f"{p4:.1f}%",
                f"{avg_rank:.2f}",
            )
        else:
            table.add_row(
                f"Seat {i} (AI)",
                f"{w_rate:.1f}% ({stats.wins[i]})",
                f"{d_rate:.1f}% ({stats.deal_ins[i]})",
                f"{avg_pts:.0f}",
                f"{p1:.1f}%",
                f"{p2:.1f}%",
                f"{p3:.1f}%",
                f"{avg_rank:.2f}",
            )

    console.print()
    console.print(table)
    console.print()


def main():
    parser = argparse.ArgumentParser(description="Simulate Mahjong matches between AI players.")
    parser.add_argument("--games", type=int, default=10, help="Number of games to simulate")
    parser.add_argument("--players", type=int, default=4, choices=[3, 4], help="3 (sanma) or 4 players")
    parser.add_argument("--mode", choices=["tonpuu", "hanchan"], default="tonpuu", help="Match format")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    args = parser.parse_args()

    print(f"🀄 Simulating {args.games} {args.mode.capitalize()} matches ({args.players}-Player AI self-play)...")
    stats = simulate_matches(
        num_games=args.games,
        num_players=args.players,
        is_tonpuu=(args.mode == "tonpuu"),
        seed=args.seed,
    )
    print_simulation_report(stats, title=f"{args.players}P {args.mode.capitalize()} AI Simulation")


if __name__ == "__main__":
    main()
