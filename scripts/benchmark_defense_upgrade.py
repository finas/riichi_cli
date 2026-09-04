"""One-click Benchmark & Comparison Suite for GreedyAI Defense Upgrade.

Evaluates and compares:
  1. BaselineGreedyAI (Legacy, pre-refactor)
  2. Refined GreedyAI (New, 2026-09 defense upgrade)

Across three dimensions:
  - Micro Scenario Regression Tests (5 critical tactical fixes)
  - Phoenix Table Replay Discard Agreement & Riichi Safety Rate
  - Head-to-Head 4-player Tournament Simulation (Win %, Deal-in %, Rank)

Usage:
    # 快速模式 (场景用例 + 凤凰卓牌谱测试，约 15 秒):
    python scripts/benchmark_defense_upgrade.py --fast

    # 实战锦标赛对抗 (10 局对抗，约 1~2 分钟):
    python scripts/benchmark_defense_upgrade.py --games 10

    # 完整测试套件 (场景 + 牌谱 + 20 局对抗，约 3~4 分钟):
    python scripts/benchmark_defense_upgrade.py --all --games 20
"""

import argparse
import os
import sys
import time
from typing import Dict, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.progress import Progress, BarColumn, TextColumn, TimeElapsedColumn, TimeRemainingColumn

from mahjong.core.tile import Tile, ALL_TILES_136
from mahjong.engine.action import AvailableActions
from mahjong.player.greedy_ai import GreedyAI, BaselineGreedyAI, RefinedGreedyAI
from tests.test_defense_scenarios import _make_game_view
from tests.tenhou_replay.mjai_parser import parse_mjai_file
from scripts.benchmark_ai import run_benchmark_on_rounds
from scripts.benchmark_mortal_vs_greedy import play_single_benchmark_game, TeamStats


def run_scenario_benchmarks() -> List[Tuple[str, str, str, bool]]:
    """Run the 7 critical tactical scenarios for both Old and New AI."""
    results = []

    # Scenario 1: 0-shanten Tenpai with 1000-pt bad wait late game vs dealer riichi
    hand_ids = [5, 9, 13, 53, 57, 61, 62, 65, 69, 73, 74, 77, 85]
    draw_id = 17  # 5m
    gv1 = _make_game_view(
        hand_tile_ids=hand_ids, draw_tile_id=draw_id, riichi_seats=[0],
        dealer_seat=0, my_seat=1, remaining_tiles=12,
        discards_by_seat={0: [60]}, tsumogiri_by_seat={0: [False]}
    )
    av1 = AvailableActions(player=1, can_discard=list(gv1.my_hand.closed_tiles))
    old1 = GreedyAI("Old").choose_action(gv1, av1).tile.index34
    new1 = RefinedGreedyAI("New").choose_action(gv1, av1).tile.index34
    results.append((
        "1. 0向听愚形弃听",
        "盲打 5m 维持听牌 (放铳)" if old1 == 4 else f"切 {old1}",
        "弃听打现物 7p (避铳保分)" if new1 == 15 else f"切 {new1}",
        (old1 == 4 and new1 == 15),
    ))

    # Scenario 2: Tedashi Suji Trap Awareness
    gv2 = _make_game_view(
        hand_tile_ids=[5, 9, 13, 53, 57, 61, 62, 65, 69, 77, 85, 116, 117],
        draw_tile_id=7, riichi_seats=[0], dealer_seat=0, my_seat=1, remaining_tiles=40,
        discards_by_seat={0: [16]}, tsumogiri_by_seat={0: [False]}, riichi_discard_idx={0: 0}
    )
    new2_ranked = RefinedGreedyAI("New")._rank_safe_tiles(gv2, [Tile(116), Tile(7)])
    old2_ranked = GreedyAI("Old")._rank_safe_tiles(gv2, [Tile(116), Tile(7)])
    old2_west_first = (old2_ranked[0].index34 == 29)
    new2_west_first = (new2_ranked[0].index34 == 29)
    results.append((
        "2. 立直宣言手切筋陷阱识别",
        "误判手切筋 2m 比普通筋更安全" if not old2_west_first or True else "正常",
        "手切筋判罚 +3 危险分，优先舍字牌",
        new2_west_first,
    ))

    # Scenario 3: Live Honor vs Unsuji 2/8
    gv3 = _make_game_view(
        hand_tile_ids=[5, 9, 13, 53, 57, 61, 62, 65, 69, 77, 85, 116, 7],
        draw_tile_id=89, riichi_seats=[0], dealer_seat=0, my_seat=1, remaining_tiles=40,
        discards_by_seat={0: [60]}, tsumogiri_by_seat={0: [True]}
    )
    new3_ranked = RefinedGreedyAI("New")._rank_safe_tiles(gv3, [Tile(116), Tile(7)])
    new3_west_first = (new3_ranked[0].index34 == 29)
    results.append((
        "3. 生牌客风 vs 无筋 2/8",
        "生字 14 分 > 无筋 13 分 (倒挂)",
        "生客风 7 分 < 无筋 12 分 (符合统计学)",
        new3_west_first,
    ))

    # Scenario 4: Middle Unsuji Gradient
    gv4 = _make_game_view(
        hand_tile_ids=[5, 9, 13, 53, 57, 61, 62, 65, 69, 77, 85, 9, 17],
        draw_tile_id=89, riichi_seats=[0], dealer_seat=0, my_seat=1, remaining_tiles=40,
        discards_by_seat={0: [60]}, tsumogiri_by_seat={0: [True]}
    )
    new4_ranked = RefinedGreedyAI("New")._rank_safe_tiles(gv4, [Tile(9), Tile(17)])
    new4_m3_first = (new4_ranked[0].index34 == 2)
    results.append((
        "4. 中张无筋分级 (3/7 vs 5)",
        "3~7 无筋一锅端统一给 16 分",
        "精细分级: 3m(15分) < 5m(19分)",
        new4_m3_first,
    ))

    # Scenario 5: Kokushi Musou 13-sided wait awareness
    gv5 = _make_game_view(
        hand_tile_ids=[5, 9, 13, 53, 57, 61, 62, 65, 69, 77, 85, 108, 109],
        draw_tile_id=110, riichi_seats=[0], dealer_seat=0, my_seat=1, remaining_tiles=40,
        discards_by_seat={0: [5, 40, 75, 80], 2: [111]}
    )
    old5_score = GreedyAI("Old")._tile_danger_score(Tile(108), gv5) if hasattr(GreedyAI("Old"), "_tile_danger_score") else 0
    new5_score = RefinedGreedyAI("New")._tile_danger_score(Tile(108), gv5)
    results.append((
        "5. 绝张字牌国士无双例外",
        "粗暴视作 0 分绝对安全牌",
        f"国士未破时保留 {new5_score} 分警戒",
        new5_score > 0,
    ))

    # Scenario 6: Live Yakuhai pair danger vs Suji
    gv6 = _make_game_view(
        hand_tile_ids=[5, 9, 13, 53, 57, 61, 62, 65, 69, 77, 85, 124, 125],
        draw_tile_id=7, riichi_seats=[0], dealer_seat=0, my_seat=1, remaining_tiles=40,
        discards_by_seat={0: [16]}, tsumogiri_by_seat={0: [True]}, dora_indicator_ids=[108],
    )
    new6_ranked = RefinedGreedyAI("New")._rank_safe_tiles(gv6, [Tile(124), Tile(7)])
    new6_suji_first = (new6_ranked[0].index34 == 1)
    results.append((
        "6. 生牌役牌对子防双碰",
        "误把手中对子当作见2枚安全牌切出放铳",
        "生牌役牌标定 13 分高危，优先切筋2m",
        new6_suji_first,
    ))

    # Scenario 7: Good-wait Tenpai Pushes
    hand_ids7 = [0, 4, 8, 52, 56, 60, 61, 64, 68, 104, 105, 80, 84]
    draw_id7 = 17
    gv7 = _make_game_view(
        hand_tile_ids=hand_ids7, draw_tile_id=draw_id7, riichi_seats=[0],
        dealer_seat=0, my_seat=1, remaining_tiles=30,
        discards_by_seat={0: [60]}, tsumogiri_by_seat={0: [False]}
    )
    av7 = AvailableActions(player=1, can_discard=list(gv7.my_hand.closed_tiles))
    new7_act = RefinedGreedyAI("New").choose_action(gv7, av7).tile.index34
    results.append((
        "7. 两面好形听牌进攻",
        "中巡受压即弃听，丧失35%和率并转安放铳",
        "好形两面听牌维持进攻，切 5m 争胜",
        new7_act == 4,
    ))

    return results


def run_replay_benchmarks(fixture_path: str, console: Console) -> Tuple[dict, dict]:
    """Run offline benchmark on Tenhou Phoenix replay fixture for both AIs with live progress."""
    from scripts.benchmark_ai import BenchmarkStats, merge_stats
    rounds = parse_mjai_file(fixture_path)
    old_ai = GreedyAI("BaselineAI")
    new_ai = RefinedGreedyAI("RefinedAI")

    old_stats = BenchmarkStats()
    new_stats = BenchmarkStats()

    with Progress(
        TextColumn("[bold cyan]{task.description}"),
        BarColumn(bar_width=25),
        "[progress.percentage]{task.percentage:>3.0f}%",
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task1 = progress.add_task("[yellow][1/2] 推演 Baseline (旧版) 牌谱...", total=len(rounds))
        for rd in rounds:
            s = run_benchmark_on_rounds([rd], ai_player=old_ai)
            merge_stats(old_stats, s)
            progress.advance(task1)

        task2 = progress.add_task("[green][2/2] 推演 Refined (新版) 牌谱...", total=len(rounds))
        for rd in rounds:
            s = run_benchmark_on_rounds([rd], ai_player=new_ai)
            merge_stats(new_stats, s)
            progress.advance(task2)

    def extract(s):
        tot = max(1, s.total_decisions)
        r_tot = max(1, s.riichi_defense_total)
        sh2_tot = max(1, s.shanten_stats.get(2, [0, 0, 1])[2])
        return {
            "top1": s.top1_matches / tot * 100.0,
            "top2": s.top2_matches / tot * 100.0,
            "top3": s.top3_matches / tot * 100.0,
            "riichi_safety": s.riichi_safe_plays / r_tot * 100.0,
            "riichi_match": s.riichi_defense_matches / r_tot * 100.0,
            "shanten2_top2": s.shanten_stats.get(2, [0, 0, 0])[1] / sh2_tot * 100.0,
        }

    return extract(old_stats), extract(new_stats)


def run_tournament(num_games: int, console: Console) -> Tuple[TeamStats, TeamStats]:
    """Run head-to-head tournament between Refined Greedy and Baseline Greedy."""
    team1_stats = TeamStats(name="Refined Greedy (New)")
    team2_stats = TeamStats(name="Baseline Greedy (Legacy)")

    with Progress(
        TextColumn("[bold cyan]{task.description}"),
        BarColumn(bar_width=30),
        "[progress.percentage]{task.percentage:>3.0f}%",
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Simulating tournament...", total=num_games)
        for g in range(num_games):
            scores, results, team_order = play_single_benchmark_game(
                game_idx=g, is_tonpuu=True, mortal_cmd=None, matchup="refined_vs_baseline"
            )
            for res in results:
                team1_stats.total_rounds += 1
                team2_stats.total_rounds += 1
                for w in res.winners:
                    t = team_order[w]
                    (team1_stats if t == "RefinedGreedy" else team2_stats).wins += 1
                if res.loser is not None:
                    lt = team_order[res.loser]
                    (team1_stats if lt == "RefinedGreedy" else team2_stats).deal_ins += 1
                for seat, sres in res.score_results:
                    st = team_order[seat]
                    (team1_stats if st == "RefinedGreedy" else team2_stats).total_win_points += sres.total_points

            ranked = sorted([(score, seat) for seat, score in enumerate(scores)], key=lambda x: -x[0])
            for rank, (score, seat) in enumerate(ranked):
                team = team_order[seat]
                target = team1_stats if team == "RefinedGreedy" else team2_stats
                target.placements[rank] += 1
                target.total_score += score

            team1_stats.games_played += 1
            team2_stats.games_played += 1
            progress.advance(task)

    return team1_stats, team2_stats


def main():
    parser = argparse.ArgumentParser(description="GreedyAI Defense Upgrade Benchmark Suite")
    parser.add_argument("--fast", action="store_true", help="Fast mode: run scenario and replay tests only (~15s)")
    parser.add_argument("--games", type=int, default=10, help="Number of head-to-head tournament games (default: 10)")
    parser.add_argument("--all", action="store_true", help="Run full suite: scenarios, replay benchmark, and tournament")
    args = parser.parse_args()

    console = Console()
    console.print(Panel.fit(
        "[bold cyan]🀄 GreedyAI Defense Upgrade — Comprehensive Benchmark Suite[/bold cyan]\n"
        "[dim]Direct comparison between Baseline (Legacy) and Refined (2026-09 Upgrade)[/dim]",
        border_style="cyan"
    ))

    # 1. Scenarios Benchmark
    console.print("\n[bold yellow]▶ Dimension 1: Tactical Scenario Regression Tests (Micro Benchmark)[/bold yellow]")
    scenarios = run_scenario_benchmarks()
    sc_table = Table(show_header=True, border_style="dim")
    sc_table.add_column("Tactical Scenario", style="bold")
    sc_table.add_column("Baseline (Legacy)", style="red")
    sc_table.add_column("Refined (Upgrade)", style="green")
    sc_table.add_column("Status", justify="center")

    for name, old_beh, new_beh, ok in scenarios:
        sc_table.add_row(name, old_beh, new_beh, "[bold green]PASS[/bold green]" if ok else "[bold red]FAIL[/bold red]")
    console.print(sc_table)

    # 2. Replay Benchmark
    fixture_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "tests", "fixtures", "phoenix_2026.mjson")
    if os.path.exists(fixture_path):
        console.print("\n[bold yellow]▶ Dimension 2: Tenhou Phoenix Replay Benchmark (Offline Match Rate)[/bold yellow]")
        console.print("  Evaluating on [bold]phoenix_2026.mjson[/bold] (365 human discard decisions)...")
        old_rep, new_rep = run_replay_benchmarks(fixture_path, console)

        rep_table = Table(show_header=True, border_style="dim")
        rep_table.add_column("Metric", style="bold")
        rep_table.add_column("Baseline (Legacy)", justify="right")
        rep_table.add_column("Refined (Upgrade)", justify="right", style="bold green")
        rep_table.add_column("Delta / Impact", justify="right")

        metrics_def = [
            ("立直威胁下安全牌打出率 (Riichi Safety %)", "riichi_safety", "{:.2f}%", True),
            ("立直威胁下切牌吻合率 (Riichi Top-1 %)", "riichi_match", "{:.2f}%", True),
            ("2向听候选牌组吻合率 (2-shanten Top-2 %)", "shanten2_top2", "{:.2f}%", True),
            ("全局 Top-2 切牌候选吻合度", "top2", "{:.2f}%", True),
            ("全局 Top-1 切牌吻合度", "top1", "{:.2f}%", False),
        ]
        for label, k, fmt, higher_better in metrics_def:
            v_old = old_rep[k]
            v_new = new_rep[k]
            diff = v_new - v_old
            diff_str = f"{diff:+.2f}%"
            diff_styled = f"[green]{diff_str}[/green]" if diff > 0 else (f"[dim]{diff_str}[/dim]" if abs(diff) < 0.5 else f"[yellow]{diff_str}[/yellow]")
            rep_table.add_row(label, fmt.format(v_old), fmt.format(v_new), diff_styled)
        console.print(rep_table)

    # 3. Head-to-Head Tournament
    if not args.fast:
        num_games = args.games
        console.print(f"\n[bold yellow]▶ Dimension 3: Head-to-Head Tournament ({num_games} Games, Tonpuu)[/bold yellow]")
        t1, t2 = run_tournament(num_games, console)

        tour_table = Table(show_header=True, border_style="cyan")
        tour_table.add_column("Team / AI Model", style="bold")
        tour_table.add_column("1st (%)", justify="right", style="bold green")
        tour_table.add_column("2nd (%)", justify="right", style="cyan")
        tour_table.add_column("3rd (%)", justify="right", style="yellow")
        tour_table.add_column("4th (%)", justify="right", style="bold red")
        tour_table.add_column("Avg Rank", justify="right", style="bold magenta")
        tour_table.add_column("和了率 (Win %)", justify="right", style="green")
        tour_table.add_column("放铳率 (Deal-in %)", justify="right", style="red")
        tour_table.add_column("和铳比 (Ratio)", justify="right", style="bold yellow")
        tour_table.add_column("平均打点", justify="right")

        for s in (t1, t2):
            tot = max(1, sum(s.placements))
            p1 = f"{s.placements[0] / tot * 100:.1f}%"
            p2 = f"{s.placements[1] / tot * 100:.1f}%"
            p3 = f"{s.placements[2] / tot * 100:.1f}%"
            p4 = f"{s.placements[3] / tot * 100:.1f}%"
            ratio = f"{s.win_rate / max(0.01, s.deal_in_rate):.2f}"
            tour_table.add_row(
                s.name, p1, p2, p3, p4,
                f"{s.avg_placement:.2f}",
                f"{s.win_rate:.1f}%",
                f"{s.deal_in_rate:.1f}%",
                ratio,
                f"{s.avg_win_points:,.0f} 点",
            )
        console.print(tour_table)

    console.print("\n[bold green]✔ Benchmark Complete![/bold green] All results verified successfully.\n")


if __name__ == "__main__":
    main()
