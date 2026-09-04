"""AI Cleverness Benchmark against Real Tenhou Replays.

Evaluates AI decision accuracy (match rate, defense rate, phase breakdown)
by stepping through real game logs and comparing AI decisions with human play.

Usage:
    python scripts/benchmark_ai.py --url "http://tenhou.net/0/?log=2012112403gm-0001-0000-91426bc4"
    python scripts/benchmark_ai.py --file path/to/replay.xml
"""

import argparse
import gzip
import os
import sys

# Ensure repository root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tempfile
import urllib.request
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from mahjong.core.tile import ALL_TILES_136, Tile
from mahjong.core.wall import Wall
from mahjong.core.player_state import PlayerState, Wind
from mahjong.engine.round import RoundState
from mahjong.engine.event import EventBus
from mahjong.player.base import build_game_view
from mahjong.player.greedy_ai import GreedyAI
from mahjong.rules.shanten import shanten
from tests.tenhou_replay.parser import parse, EventType, RoundData
from tests.tenhou_replay.mjai_parser import parse_mjai_file
from tests.tenhou_replay.wall_builder import build_wall
from tests.tenhou_replay.driver import _verify_meld_available, _process_meld_event, TenhouMeldType


@dataclass
class DiscardDecision:
    round_idx: int
    turn: int
    player: int
    hand_tiles: List[Tile]
    human_tile: Tile
    ai_tile: Tile
    is_top1_match: bool
    is_top2_match: bool
    is_top3_match: bool
    is_riichi_threat: bool
    ai_defense_safe: bool
    current_shanten: int
    ai_top_candidates: List[Tile] = field(default_factory=list)


@dataclass
class BenchmarkStats:
    total_decisions: int = 0
    top1_matches: int = 0
    top2_matches: int = 0
    top3_matches: int = 0
    
    # Phase Breakdown (Top-1)
    early_total: int = 0
    early_matches: int = 0
    early_top2: int = 0
    mid_total: int = 0
    mid_matches: int = 0
    mid_top2: int = 0
    late_total: int = 0
    late_matches: int = 0
    late_top2: int = 0
    
    # Defense Context
    riichi_defense_total: int = 0
    riichi_defense_matches: int = 0
    riichi_safe_plays: int = 0
    
    # Shanten Breakdown
    shanten_stats: Dict[int, List[int]] = field(default_factory=dict)  # shanten -> [top1, top2, total]
    
    decisions: List[DiscardDecision] = field(default_factory=list)

    def record(self, decision: DiscardDecision):
        self.decisions.append(decision)
        self.total_decisions += 1
        if decision.is_top1_match:
            self.top1_matches += 1
        if decision.is_top2_match:
            self.top2_matches += 1
        if decision.is_top3_match:
            self.top3_matches += 1
            
        # By turn phase
        if decision.turn <= 6:
            self.early_total += 1
            if decision.is_top1_match:
                self.early_matches += 1
            if decision.is_top2_match:
                self.early_top2 += 1
        elif decision.turn <= 12:
            self.mid_total += 1
            if decision.is_top1_match:
                self.mid_matches += 1
            if decision.is_top2_match:
                self.mid_top2 += 1
        else:
            self.late_total += 1
            if decision.is_top1_match:
                self.late_matches += 1
            if decision.is_top2_match:
                self.late_top2 += 1
                
        # Defense under Riichi
        if decision.is_riichi_threat:
            self.riichi_defense_total += 1
            if decision.is_top1_match:
                self.riichi_defense_matches += 1
            if decision.ai_defense_safe:
                self.riichi_safe_plays += 1
                
        # Shanten breakdown
        s = decision.current_shanten
        if s not in self.shanten_stats:
            self.shanten_stats[s] = [0, 0, 0]
        self.shanten_stats[s][2] += 1
        if decision.is_top1_match:
            self.shanten_stats[s][0] += 1
        if decision.is_top2_match:
            self.shanten_stats[s][1] += 1


def fetch_tenhou_xml(url_or_log_id: str) -> bytes:
    """Download Tenhou XML replay log from ID or URL."""
    if "log=" in url_or_log_id:
        log_id = url_or_log_id.split("log=")[1].split("&")[0]
    else:
        log_id = url_or_log_id.strip()

    raw_url = f"https://tenhou.net/0/log/?{log_id}"
    req = urllib.request.Request(raw_url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        content = resp.read()
        try:
            content = gzip.decompress(content)
        except Exception:
            pass
        return content


def run_benchmark_on_rounds(rounds: List[RoundData], ai_player: Optional[GreedyAI] = None) -> BenchmarkStats:
    """Step through all rounds and benchmark AI against human decisions."""
    if ai_player is None:
        ai_player = GreedyAI("BenchmarkAI")

    stats = BenchmarkStats()

    for r_idx, rd in enumerate(rounds):
        num_players = rd.num_players
        live_tiles, dead_tiles, dora_revealed = build_wall(rd)
        wall = Wall.from_tiles(
            live_tiles, dead_tiles, is_sanma=rd.is_sanma, dora_revealed=dora_revealed
        )
        players = [PlayerState(seat=i, name=f"P{i}", score=rd.scores[i]) for i in range(num_players)]
        round_wind = Wind(rd.round_number // 4)
        dealer = rd.dealer
        for i in range(num_players):
            players[i].seat_wind = Wind((i - dealer) % num_players)
            players[i].is_dealer = (i == dealer)

        event_bus = EventBus()
        rs = RoundState(
            players=players,
            wall=wall,
            round_wind=round_wind,
            honba=rd.honba,
            riichi_sticks=rd.riichi_sticks,
            event_bus=event_bus,
            is_sanma=rd.is_sanma,
        )

        for i in range(num_players):
            for tile_id in rd.hands[i]:
                rs.players[i].hand.closed_tiles.append(ALL_TILES_136[tile_id])
            rs.players[i].hand.sort_closed()
            rs.players[i].hand.draw_tile = None

        last_draw_available = {}
        expect_rinshan = False
        rinshan_from_live = False
        turn_counter = {i: 0 for i in range(num_players)}

        for event in rd.events:
            if event.event_type == EventType.DRAW:
                p = event.player
                turn_counter[p] += 1
                if expect_rinshan:
                    tile = wall.draw() if rinshan_from_live else wall.draw_rinshan()
                    expect_rinshan = False
                    rinshan_from_live = False
                    rs.is_rinshan = True
                else:
                    rs.clear_temp_furiten(p)
                    rs.turn_count += 1
                    rs.is_rinshan = False
                    tile = wall.draw()
                rs.players[p].hand.draw(tile)
                last_draw_available[p] = rs.get_draw_actions(p)

            elif event.event_type == EventType.DISCARD:
                p = event.player
                tile = ALL_TILES_136[event.tile_id]
                available = last_draw_available.get(p)

                # If in riichi, discard is forced tsumogiri - skip voluntary choice eval
                if available and available.can_discard and not rs.players[p].hand.is_riichi:
                    gv = build_game_view(
                        player_idx=p,
                        players=rs.players,
                        round_wind=rs.round_wind,
                        honba=rs.honba,
                        riichi_sticks=rs.riichi_sticks,
                        remaining_tiles=rs.wall.remaining,
                        dora_indicators=rs.wall.dora_indicators,
                        round_label="",
                        last_discard=rs.last_discard,
                        last_discard_player=rs.last_discard_player,
                    )

                    ranked_candidates = ai_player.rank_discards(gv, available.can_discard)
                    if ranked_candidates:
                        ai_tile = ranked_candidates[0]
                        cand_34 = [t.index34 for t in ranked_candidates]
                        
                        top1_match = (cand_34[0] == tile.index34)
                        top2_match = (tile.index34 in cand_34[:2])
                        top3_match = (tile.index34 in cand_34[:3])
                        
                        riichi_opps = [opp for opp in gv.opponents if opp.is_riichi]
                        has_riichi_opp = len(riichi_opps) > 0
                        
                        # Check whether the AI chose a tile that is provably
                        # safe against every riichi opponent.  Do not count a
                        # human Top-1 agreement as "safe" unless the tile is
                        # actually genbutsu; agreement and safety are separate
                        # benchmark dimensions.
                        ai_safe = False
                        if has_riichi_opp:
                            genbutsu_sets = [
                                {dp.index34 for dp in opp.discard_pool}
                                for opp in riichi_opps
                            ]
                            common_genbutsu = (
                                set.intersection(*genbutsu_sets)
                                if genbutsu_sets else set()
                            )
                            ai_safe = ai_tile.index34 in common_genbutsu

                        # Hand shanten before discard
                        hand_34 = [0] * 34
                        for t in rs.players[p].hand.closed_tiles:
                            hand_34[t.index34] += 1
                        if rs.players[p].hand.draw_tile:
                            hand_34[rs.players[p].hand.draw_tile.index34] += 1
                        cur_shanten = shanten(hand_34)

                        stats.record(DiscardDecision(
                            round_idx=r_idx,
                            turn=turn_counter[p],
                            player=p,
                            hand_tiles=list(rs.players[p].hand.closed_tiles),
                            human_tile=tile,
                            ai_tile=ai_tile,
                            is_top1_match=top1_match,
                            is_top2_match=top2_match,
                            is_top3_match=top3_match,
                            is_riichi_threat=has_riichi_opp,
                            ai_defense_safe=ai_safe,
                            current_shanten=cur_shanten,
                            ai_top_candidates=ranked_candidates[:3],
                        ))

                rs.process_discard(p, tile)
                last_draw_available.pop(p, None)

            elif event.event_type == EventType.MELD:
                _verify_meld_available(rs, event, "", last_draw_available)
                _process_meld_event(rs, event, "")
                if event.decoded_meld and event.decoded_meld.meld_type in (
                    TenhouMeldType.ANKAN, TenhouMeldType.DAIMINKAN, TenhouMeldType.KAKAN
                ):
                    expect_rinshan = True
                elif event.decoded_meld and event.decoded_meld.meld_type == TenhouMeldType.KITA:
                    expect_rinshan = True
                    rinshan_from_live = True
                last_draw_available.pop(event.player, None)

            elif event.event_type == EventType.RIICHI_SCORE:
                rs.players[event.player].hand.is_riichi = True
                rs.players[event.player].score -= 1000
                rs.riichi_sticks += 1

    return stats


def print_benchmark_report(stats: BenchmarkStats, source_title: str = "Tenhou Replay"):
    console = Console()
    
    if stats.total_decisions == 0:
        console.print("[yellow]No discard decisions evaluated.[/yellow]")
        return

    top1_pct = stats.top1_matches / stats.total_decisions * 100
    top2_pct = stats.top2_matches / stats.total_decisions * 100
    top3_pct = stats.top3_matches / stats.total_decisions * 100
    
    table = Table(title=f"🎯 AI Cleverness Benchmark - {source_title}", header_style="bold cyan")
    table.add_column("Category / Phase", style="bold")
    table.add_column("Top-1 Match", justify="right")
    table.add_column("Top-1 (%)", justify="right", style="green")
    table.add_column("Top-2 Match", justify="right")
    table.add_column("Top-2 (%)", justify="right", style="cyan")
    table.add_column("Total", justify="right")

    table.add_row(
        "Overall Discards",
        str(stats.top1_matches),
        f"[bold]{top1_pct:.2f}%[/bold]",
        str(stats.top2_matches),
        f"[bold]{top2_pct:.2f}%[/bold]",
        str(stats.total_decisions),
    )
    table.add_section()
    
    # Phase rows
    e_top1 = stats.early_matches / max(1, stats.early_total) * 100
    e_top2 = stats.early_top2 / max(1, stats.early_total) * 100
    m_top1 = stats.mid_matches / max(1, stats.mid_total) * 100
    m_top2 = stats.mid_top2 / max(1, stats.mid_total) * 100
    l_top1 = stats.late_matches / max(1, stats.late_total) * 100
    l_top2 = stats.late_top2 / max(1, stats.late_total) * 100
    
    table.add_row("Early Turns (1-6)", str(stats.early_matches), f"{e_top1:.2f}%", str(stats.early_top2), f"{e_top2:.2f}%", str(stats.early_total))
    table.add_row("Mid Turns (7-12)", str(stats.mid_matches), f"{m_top1:.2f}%", str(stats.mid_top2), f"{m_top2:.2f}%", str(stats.mid_total))
    table.add_row("Late Turns (13+)", str(stats.late_matches), f"{l_top1:.2f}%", str(stats.late_top2), f"{l_top2:.2f}%", str(stats.late_total))
    table.add_section()

    # Defense under Riichi
    d_top1 = stats.riichi_defense_matches / max(1, stats.riichi_defense_total) * 100
    d_safe = stats.riichi_safe_plays / max(1, stats.riichi_defense_total) * 100
    table.add_row(
        "Under Riichi Threat (Defense)",
        str(stats.riichi_defense_matches),
        f"{d_top1:.2f}%",
        f"{stats.riichi_safe_plays} (Safe)",
        f"{d_safe:.2f}%",
        str(stats.riichi_defense_total),
    )
    table.add_section()

    # Shanten breakdown
    for s in sorted(stats.shanten_stats.keys()):
        m1, m2, tot = stats.shanten_stats[s]
        s1_pct = m1 / max(1, tot) * 100
        s2_pct = m2 / max(1, tot) * 100
        s_name = "Tenpai (0-shanten)" if s == 0 else f"{s}-shanten"
        table.add_row(f"  Shape: {s_name}", str(m1), f"{s1_pct:.2f}%", str(m2), f"{s2_pct:.2f}%", str(tot))

    console.print()
    console.print(table)
    
    # Cleverness assessment rating
    if top1_pct >= 75:
        tier = "[bold magenta]Tier 1: Master / Superhuman (Mortal / Suphx Level)[/bold magenta]"
    elif top1_pct >= 65 or top2_pct >= 85:
        tier = "[bold cyan]Tier 2: Expert / High-Dan (Dan 5-7 Level)[/bold cyan]"
    elif top1_pct >= 55 or top2_pct >= 75:
        tier = "[bold green]Tier 3: Advanced Greedy / Ukeire Solver (Tokujou Level)[/bold green]"
    elif top1_pct >= 40:
        tier = "[bold yellow]Tier 4: Intermediate / Basic Heuristic (Joukyuu Level)[/bold yellow]"
    else:
        tier = "[bold red]Tier 5: Novice / Simple Rule Bot[/bold red]"
        
    console.print(Panel(f"[bold]AI Cleverness Rating:[/bold] {tier}\n"
                        f"[dim]Top-1 Discard Match: {top1_pct:.2f}% | Top-2 Discard Match: {top2_pct:.2f}% | Top-3 Match: {top3_pct:.2f}%[/dim]",
                        title="Skill Evaluation", border_style="blue"))


def fetch_phoenix_log_ids(num_games: int = 20, player_type: str = "4p") -> List[str]:
    """Fetch recent Phoenix (鳳凰卓) game log IDs from Tenhou's live index.

    Args:
        num_games: How many log IDs to return.
        player_type: '4p' for 4-player (00a9), '3p' for 3-player (00b9).
    """
    import re

    game_code = "00a9" if player_type == "4p" else "00b9"

    # Fetch the live file list from list.cgi (JS callback format)
    req = urllib.request.Request(
        "https://tenhou.net/sc/raw/list.cgi",
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://tenhou.net/sc/raw/"}
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        raw = r.read()
    try:
        index_data = gzip.decompress(raw).decode("utf-8", "replace")
    except Exception:
        index_data = raw.decode("utf-8", "replace")

    # Extract scc*.html.gz filenames (Phoenix table)
    scc_files = re.findall(r"file:'(scc\d+\.html\.gz)'", index_data)
    if not scc_files:
        raise RuntimeError("No Phoenix (scc) log files found in list.cgi")

    # Sort descending (newest first), skip today's partial file
    scc_files = sorted(scc_files, reverse=True)

    log_ids: List[str] = []
    for filename in scc_files:
        if len(log_ids) >= num_games:
            break
        url = f"https://tenhou.net/sc/raw/dat/{filename}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as r:
                raw = r.read()
            html = gzip.decompress(raw).decode("utf-8", "replace")
            # Extract log IDs matching requested player type
            ids = re.findall(rf"log=(\d+gm-{game_code}-\d+-[0-9a-f]+)", html)
            for lid in ids:
                if lid not in log_ids:
                    log_ids.append(lid)
                    if len(log_ids) >= num_games:
                        break
        except Exception as e:
            print(f"  [warn] Failed to fetch {filename}: {e}")
        # Be polite to the server
        import time
        time.sleep(0.3)

    return log_ids[:num_games]


def merge_stats(base: BenchmarkStats, other: BenchmarkStats) -> None:
    """Merge `other` into `base` in-place."""
    base.total_decisions += other.total_decisions
    base.top1_matches += other.top1_matches
    base.top2_matches += other.top2_matches
    base.top3_matches += other.top3_matches
    base.early_total += other.early_total
    base.early_matches += other.early_matches
    base.early_top2 += other.early_top2
    base.mid_total += other.mid_total
    base.mid_matches += other.mid_matches
    base.mid_top2 += other.mid_top2
    base.late_total += other.late_total
    base.late_matches += other.late_matches
    base.late_top2 += other.late_top2
    base.riichi_defense_total += other.riichi_defense_total
    base.riichi_defense_matches += other.riichi_defense_matches
    base.riichi_safe_plays += other.riichi_safe_plays
    for s, vals in other.shanten_stats.items():
        if s not in base.shanten_stats:
            base.shanten_stats[s] = [0, 0, 0]
        for i in range(3):
            base.shanten_stats[s][i] += vals[i]


def main():
    parser_arg = argparse.ArgumentParser(
        description="Benchmark Mahjong AI cleverness against Tenhou replays."
    )
    parser_arg.add_argument("--url", type=str,
        help="Single Tenhou replay URL or log ID")
    parser_arg.add_argument("--file", type=str,
        help="Path to local Tenhou XML file")
    parser_arg.add_argument("--phoenix", type=int, default=0, metavar="N",
        help="Fetch N recent Phoenix (鳳凰卓) games and benchmark across all of them")
    parser_arg.add_argument("--player-type", type=str, default="4p", choices=["4p", "3p"],
        help="Phoenix game type: 4p (default) or 3p (sanma)")
    args = parser_arg.parse_args()

    console = Console()
    ai_player = GreedyAI("BenchmarkAI")

    if args.phoenix and args.phoenix > 0:
        # ── Multi-game Phoenix mode ──────────────────────────────────────────
        n = args.phoenix
        console.print(f"\n[bold cyan]🀄 Phoenix Benchmark Mode[/bold cyan] — fetching [bold]{n}[/bold] {args.player_type} games...")
        try:
            log_ids = fetch_phoenix_log_ids(num_games=n, player_type=args.player_type)
        except Exception as e:
            console.print(f"[red]Failed to fetch Phoenix log list: {e}[/red]")
            return

        if not log_ids:
            console.print("[red]No log IDs found. Aborting.[/red]")
            return

        console.print(f"  Found [bold]{len(log_ids)}[/bold] log IDs. Running benchmark...\n")
        aggregate = BenchmarkStats()
        failed = 0

        for i, log_id in enumerate(log_ids, 1):
            console.print(f"  [{i:>3}/{len(log_ids)}] {log_id} ... ", end="")
            try:
                xml_data = fetch_tenhou_xml(log_id)
                with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as f:
                    f.write(xml_data)
                    temp_path = f.name
                rounds = parse(temp_path)
                os.unlink(temp_path)
                stats = run_benchmark_on_rounds(rounds, ai_player=ai_player)
                merge_stats(aggregate, stats)
                top1 = stats.top1_matches / max(1, stats.total_decisions) * 100
                console.print(f"[green]{stats.total_decisions} decisions, Top-1={top1:.1f}%[/green]")
            except Exception as e:
                console.print(f"[yellow]SKIP ({e})[/yellow]")
                failed += 1
            import time
            time.sleep(0.5)  # Rate-limiting

        source_name = f"Phoenix {args.player_type} × {len(log_ids)} games ({failed} failed)"
        print_benchmark_report(aggregate, source_title=source_name)

    elif args.file:
        # ── Single local file mode (supports .xml, .mjson, .jsonl, .json) ────
        file_path = args.file
        source_name = os.path.basename(file_path)
        if file_path.endswith((".mjson", ".mjson.gz", ".jsonl", ".json")):
            rounds = parse_mjai_file(file_path)
        else:
            rounds = parse(file_path)
        stats = run_benchmark_on_rounds(rounds, ai_player=ai_player)
        print_benchmark_report(stats, source_title=source_name)

    else:
        # ── Single URL / log ID mode ─────────────────────────────────────────
        log_id = args.url or "2012112403gm-0001-0000-91426bc4"
        source_name = f"Log: {log_id}"
        print(f"Fetching Tenhou replay: {log_id} ...")
        xml_data = fetch_tenhou_xml(log_id)
        with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as f:
            f.write(xml_data)
            temp_path = f.name
        rounds = parse(temp_path)
        os.unlink(temp_path)
        stats = run_benchmark_on_rounds(rounds, ai_player=ai_player)
        print_benchmark_report(stats, source_title=source_name)


if __name__ == "__main__":
    main()
