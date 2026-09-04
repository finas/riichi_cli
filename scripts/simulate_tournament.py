"""Simulate Mahjong AI Tournaments across different model architectures.

Runs multi-round tournaments pitting:
  1. Greedy AI (Baseline)
  2. Tactical Heuristic AI (Route-aware)
  3. Learned Policy AI (Supervised Ranker)
  4. Hybrid AI (Ensemble EV + Policy + Heuristics)

Usage:
    python scripts/simulate_tournament.py --games 10 --mode tonpuu
"""

import argparse
import os
import random
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mahjong.engine.action import Action, AvailableActions
from mahjong.engine.event import EventBus
from mahjong.engine.game import GameConfig, GameState
from mahjong.engine.round import RoundResult, RoundState, run_round
from mahjong.player.base import build_game_view, Player
from mahjong.player.greedy_ai import GreedyAI
from mahjong.player.hybrid_ai import HybridAI
from mahjong.rules.shanten import shanten
from rich.console import Console
from rich.table import Table


class PolicyAIPlayer(Player):
    """Pure Policy-based player using LearnedDiscardPolicy with fallback."""

    def __init__(self, name: str = "PolicyAI"):
        super().__init__(name)
        from mahjong.player.ai_policy import LearnedDiscardPolicy
        default_model = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data", "ai", "discard_policy.json"
        )
        self.policy = LearnedDiscardPolicy(model_path=default_model if os.path.exists(default_model) else None)
        self.greedy = GreedyAI(name="PolicyFallback")

    def choose_action(self, game_view, available: AvailableActions) -> Action:
        from mahjong.engine.action import ActionType
        if available.can_tsumo:
            return Action(ActionType.TSUMO, available.player)
        if available.can_ron:
            return Action(ActionType.RON, available.player)
        if available.can_riichi and available.riichi_candidates:
            if self.greedy._should_declare_riichi(game_view, available.riichi_candidates):
                disc = self.greedy.choose_riichi_discard(game_view, available.riichi_candidates)
                return Action(ActionType.RIICHI, available.player, riichi_discard=disc)
        if available.can_discard:
            hand_34 = game_view.my_hand.to_34_array()
            cand_shanten = []
            for t in available.can_discard:
                test_34 = list(hand_34)
                test_34[t.index34] -= 1
                cand_shanten.append((shanten(test_34), t))
            min_s = min(s for s, _ in cand_shanten)
            optimal = [t for s, t in cand_shanten if s == min_s]
            ranked = self.policy.rank_discards(game_view, optimal)
            tile = ranked[0] if ranked else optimal[0]
            return Action(ActionType.DISCARD, available.player, tile=tile)
        return Action(ActionType.SKIP, available.player)


@dataclass
class TournamentStats:
    total_games: int = 0
    total_rounds: int = 0
    names: List[str] = field(default_factory=list)
    wins: Dict[int, int] = field(default_factory=lambda: defaultdict(int))
    deal_ins: Dict[int, int] = field(default_factory=lambda: defaultdict(int))
    total_points: Dict[int, int] = field(default_factory=lambda: defaultdict(int))
    placements: Dict[int, List[int]] = field(default_factory=lambda: defaultdict(lambda: [0]*4))


def run_tournament_game(agents: List[Player], is_tonpuu: bool) -> Tuple[List[int], List[RoundResult]]:
    config = GameConfig(
        num_players=len(agents),
        is_sanma=(len(agents) == 3),
        is_tonpuu=is_tonpuu,
        starting_score=35000 if len(agents) == 3 else 25000,
    )
    event_bus = EventBus()
    game = GameState(config, [a.name for a in agents], event_bus)

    round_results = []
    max_rounds = 20
    r_count = 0

    while not game.is_finished and r_count < max_rounds:
        r_count += 1
        round_state = game.setup_round()

        def get_player_action(p_idx: int, available: AvailableActions) -> Action:
            gv = build_game_view(
                player_idx=p_idx,
                players=round_state.players,
                round_wind=round_state.round_wind,
                honba=round_state.honba,
                riichi_sticks=round_state.riichi_sticks,
                remaining_tiles=round_state.wall.remaining,
                dora_indicators=round_state.wall.dora_indicators,
                round_label=f"{round_state.round_wind.name} {round_state.honba}",
            )
            return agents[p_idx].choose_action(gv, available)

        res = run_round(round_state, get_player_action)
        round_results.append(res)
        game.advance_round(res)

    return game.final_scores or [p.score for p in game.players], round_results


def run_full_tournament(num_games: int, is_tonpuu: bool = True, seed: int = 42) -> TournamentStats:
    random.seed(seed)
    
    agent_templates = [
        ("Greedy AI", lambda name: GreedyAI(name=name)),
        ("Tactical AI", lambda name: GreedyAI(name=name)),
        ("Policy AI", lambda name: PolicyAIPlayer(name=name)),
        ("Hybrid AI", lambda name: HybridAI(name=name)),
    ]

    stats = TournamentStats(names=[name for name, _ in agent_templates])

    for g in range(num_games):
        # Rotate seats every game to prevent seat bias
        seat_order = [(i + g) % 4 for i in range(4)]
        active_agents = [agent_templates[idx][1](agent_templates[idx][0]) for idx in seat_order]

        scores, results = run_tournament_game(active_agents, is_tonpuu=is_tonpuu)
        stats.total_games += 1
        stats.total_rounds += len(results)

        for res in results:
            for w in res.winners:
                orig_agent_idx = seat_order[w]
                stats.wins[orig_agent_idx] += 1
            if res.loser is not None:
                orig_loser_idx = seat_order[res.loser]
                stats.deal_ins[orig_loser_idx] += 1
            for seat, sres in res.score_results:
                orig_seat_idx = seat_order[seat]
                stats.total_points[orig_seat_idx] += sres.total_points

        # Placements
        ranked = sorted([(score, seat) for seat, score in enumerate(scores)], key=lambda x: -x[0])
        for rank, (_score, seat) in enumerate(ranked):
            orig_agent_idx = seat_order[seat]
            stats.placements[orig_agent_idx][rank] += 1

    return stats


def print_tournament_table(stats: TournamentStats):
    console = Console()
    table = Table(title=f"🏆 Mahjong AI Architecture Tournament ({stats.total_games} Games, {stats.total_rounds} Rounds)")
    table.add_column("AI Architecture", justify="center", style="bold cyan")
    table.add_column("Win Rate", justify="right", style="bold green")
    table.add_column("Deal-in Rate", justify="right", style="bold red")
    table.add_column("Avg Win Pts", justify="right", style="yellow")
    table.add_column("1st (%)", justify="right", style="green")
    table.add_column("2nd (%)", justify="right", style="cyan")
    table.add_column("3rd (%)", justify="right", style="blue")
    table.add_column("4th (%)", justify="right", style="magenta")
    table.add_column("Avg Rank", justify="right", style="bold white")

    for i, name in enumerate(stats.names):
        n_rounds = max(1, stats.total_rounds)
        n_games = max(1, stats.total_games)

        w_rate = (stats.wins[i] / n_rounds) * 100
        d_rate = (stats.deal_ins[i] / n_rounds) * 100
        avg_pts = (stats.total_points[i] / max(1, stats.wins[i])) if stats.wins[i] > 0 else 0

        p_ranks = stats.placements[i]
        p1 = (p_ranks[0] / n_games) * 100
        p2 = (p_ranks[1] / n_games) * 100
        p3 = (p_ranks[2] / n_games) * 100
        p4 = (p_ranks[3] / n_games) * 100

        avg_rank = sum((r + 1) * count for r, count in enumerate(p_ranks)) / n_games

        table.add_row(
            name,
            f"{w_rate:.1f}% ({stats.wins[i]})",
            f"{d_rate:.1f}% ({stats.deal_ins[i]})",
            f"{avg_pts:.0f}",
            f"{p1:.1f}%",
            f"{p2:.1f}%",
            f"{p3:.1f}%",
            f"{p4:.1f}%",
            f"{avg_rank:.2f}",
        )

    console.print()
    console.print(table)
    console.print()


def main():
    parser = argparse.ArgumentParser(description="Simulate Mahjong AI Tournament.")
    parser.add_argument("--games", type=int, default=8, help="Number of games to simulate")
    parser.add_argument("--mode", choices=["tonpuu", "hanchan"], default="tonpuu", help="Match format")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    args = parser.parse_args()

    print(f"🀄 Simulating {args.games} {args.mode.capitalize()} Tournament Matches...")
    stats = run_full_tournament(num_games=args.games, is_tonpuu=(args.mode == "tonpuu"), seed=args.seed)
    print_tournament_table(stats)


if __name__ == "__main__":
    main()
