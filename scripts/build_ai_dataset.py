"""Build feature and label datasets from MJAI / Tenhou replay files.

Converts game logs into structured JSONL decision records for AI training and evaluation.
Splits datasets at the game level (train/validation/test) to prevent replay leakage.

Usage:
    python scripts/build_ai_dataset.py --input data/benchmark/phoenix_4p_10games.mjson.gz --output-dir data/ai/
"""

import argparse
import gzip
import json
import os
import random
import sys
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mahjong.core.tile import ALL_TILES_136, Tile, YAOCHU_INDICES
from mahjong.core.player_state import PlayerState, Wind
from mahjong.engine.round import RoundState
from mahjong.engine.event import EventBus
from mahjong.player.base import build_game_view
from mahjong.player.ai_features import extract_public_features
from mahjong.rules.shanten import shanten
from tests.tenhou_replay.parser import parse, EventType, RoundData
from tests.tenhou_replay.mjai_parser import parse_mjai_file
from tests.tenhou_replay.wall_builder import build_wall
from tests.tenhou_replay.driver import _verify_meld_available, _process_meld_event, TenhouMeldType


def extract_decision_record(
    gv,
    player_idx: int,
    round_idx: int,
    turn: int,
    human_tile: Tile,
    legal_discards: List[Tile],
    game_id: str,
) -> Dict[str, Any]:
    """Extract a single training/analysis feature record."""
    feat = extract_public_features(gv)
    hand_34 = gv.my_hand.to_34_array()
    cur_shanten = shanten(hand_34)

    # Hand composition
    closed_counts = list(hand_34)
    melds_count = len(gv.my_hand.melds)
    num_dora = sum(hand_34[i] for i in feat.dora_indices if i < 34)
    num_red = sum(1 for t in gv.my_hand.closed_tiles if t.is_red)

    # Candidates features
    candidates = []
    for t in legal_discards:
        post_34 = list(hand_34)
        post_34[t.index34] -= 1
        post_s = shanten(post_34)
        is_dora = t.index34 in feat.dora_indices or t.is_red
        is_safe = (t.index34 in feat.common_genbutsu or 
                   t.index34 in feat.suji_safe or 
                   t.index34 in feat.kabe_no_chance)
        candidates.append({
            "tile_34": t.index34,
            "is_red": t.is_red,
            "post_shanten": post_s,
            "is_dora": is_dora,
            "is_safe": is_safe,
            "is_chosen": (t.index34 == human_tile.index34 and t.is_red == human_tile.is_red),
        })

    return {
        "game_id": game_id,
        "round_idx": round_idx,
        "turn": turn,
        "player": player_idx,
        "is_sanma": feat.is_sanma,
        "is_dealer": gv.is_dealer,
        "my_wind": gv.my_wind.index34,
        "round_wind": gv.round_wind.index34,
        "remaining_tiles": gv.remaining_tiles,
        "score": gv.my_score,
        "hand_counts": closed_counts,
        "melds_count": melds_count,
        "current_shanten": cur_shanten,
        "num_dora": num_dora + num_red,
        "has_riichi_threat": len(feat.riichi_opponents) > 0,
        "visible_counts": feat.visible_34,
        "live_counts": feat.live_34,
        "human_choice_34": human_tile.index34,
        "human_choice_red": human_tile.is_red,
        "candidates": candidates,
    }


def process_rounds_to_records(rounds: List[RoundData], game_id: str) -> List[Dict[str, Any]]:
    """Process all rounds in a game and extract decision records."""
    records = []

    for r_idx, rd in enumerate(rounds):
        num_players = rd.num_players
        is_sanma = rd.is_sanma

        live_tiles, dead_tiles, dora_revealed = build_wall(rd)
        from mahjong.core.wall import Wall
        wall = Wall.from_tiles(
            live_tiles, dead_tiles, is_sanma=is_sanma, dora_revealed=dora_revealed
        )
        players = [PlayerState(seat=i, name=f"P{i}", score=rd.scores[i]) for i in range(num_players)]
        round_wind = Wind(rd.round_number // 4)
        dealer = rd.dealer
        for i in range(num_players):
            players[i].seat_wind = Wind((i - dealer) % num_players)
            players[i].is_dealer = (i == dealer)

        rs = RoundState(
            players=players,
            wall=wall,
            round_wind=round_wind,
            honba=rd.honba,
            riichi_sticks=rd.riichi_sticks,
            event_bus=EventBus(),
            is_sanma=is_sanma,
        )

        for i in range(num_players):
            for tid in rd.hands[i]:
                rs.players[i].hand.closed_tiles.append(ALL_TILES_136[tid])

        turn_counter = {i: 0 for i in range(num_players)}
        last_draw_available = {}

        for event in rd.events:
            if event.event_type == EventType.DRAW:
                p = event.player
                tile_id = event.tile_id
                tile = ALL_TILES_136[tile_id] if tile_id >= 0 else wall.draw()
                rs.players[p].hand.draw(tile)
                turn_counter[p] += 1
                last_draw_available[p] = rs.get_draw_actions(p)

            elif event.event_type == EventType.DISCARD:
                p = event.player
                tile_id = event.tile_id
                tile = ALL_TILES_136[tile_id]
                avail = last_draw_available.get(p)
                legal_discards = avail.can_discard if avail and avail.can_discard else rs.players[p].hand.closed_tiles

                if legal_discards and len(legal_discards) > 1:
                    gv = build_game_view(
                        player_idx=p,
                        players=rs.players,
                        round_wind=rs.round_wind,
                        honba=rs.honba,
                        riichi_sticks=rs.riichi_sticks,
                        remaining_tiles=rs.wall.remaining,
                        dora_indicators=rs.wall.dora_indicators,
                        round_label=f"Round {rd.round_number}",
                    )
                    rec = extract_decision_record(
                        gv=gv,
                        player_idx=p,
                        round_idx=r_idx,
                        turn=turn_counter[p],
                        human_tile=tile,
                        legal_discards=legal_discards,
                        game_id=game_id,
                    )
                    records.append(rec)

                # Match actual tile in hand
                actual_tile = next((t for t in rs.players[p].hand.closed_tiles if t.id == tile.id), None)
                if not actual_tile:
                    actual_tile = next((t for t in rs.players[p].hand.closed_tiles if t.index34 == tile.index34 and t.is_red == tile.is_red), None)
                if not actual_tile:
                    actual_tile = next((t for t in rs.players[p].hand.closed_tiles if t.index34 == tile.index34), tile)

                rs.process_discard(p, actual_tile)
                last_draw_available.pop(p, None)

            elif event.event_type == EventType.MELD:
                _verify_meld_available(rs, event, "", last_draw_available)
                _process_meld_event(rs, event, "")
                if event.decoded_meld and event.decoded_meld.meld_type in (
                    TenhouMeldType.ANKAN, TenhouMeldType.DAIMINKAN, TenhouMeldType.KAKAN
                ):
                    rs.wall.reveal_new_dora()

            elif event.event_type == EventType.RIICHI_DECLARE:
                rs.players[event.player].hand.is_riichi = True

            elif event.event_type == EventType.RIICHI_SCORE:
                rs.players[event.player].score -= 1000
                rs.riichi_sticks += 1

    return records


def build_dataset_from_files(
    input_paths: List[str],
    output_dir: str,
    train_ratio: float = 0.8,
    val_ratio: float = 0.1,
    seed: int = 42,
):
    """Process files and create train/val/test splits grouped by game."""
    os.makedirs(output_dir, exist_ok=True)
    random.seed(seed)

    all_game_records: Dict[str, List[Dict[str, Any]]] = {}

    for path in input_paths:
        print(f"Reading {path}...")
        if path.endswith((".mjson", ".mjson.gz", ".jsonl", ".json")):
            rounds = parse_mjai_file(path)
        else:
            rounds = parse(path)

        game_id = os.path.basename(path).replace(".mjson.gz", "").replace(".mjson", "").replace(".xml", "")
        records = process_rounds_to_records(rounds, game_id)
        if records:
            all_game_records[game_id] = records
            print(f"  {game_id}: {len(records)} decision records extracted")

    # Split by game_id
    game_ids = list(all_game_records.keys())
    random.shuffle(game_ids)

    n_train = max(1, int(len(game_ids) * train_ratio))
    n_val = max(1, int(len(game_ids) * val_ratio)) if len(game_ids) >= 3 else 0

    train_ids = set(game_ids[:n_train])
    val_ids = set(game_ids[n_train:n_train + n_val])
    test_ids = set(game_ids[n_train + n_val:]) if (n_train + n_val) < len(game_ids) else set(game_ids[n_train:])

    # If dataset has few files, allow overlap or fallback gracefully
    if not test_ids:
        test_ids = val_ids or train_ids

    splits = {
        "train": [r for gid in train_ids for r in all_game_records[gid]],
        "val": [r for gid in val_ids for r in all_game_records[gid]],
        "test": [r for gid in test_ids for r in all_game_records[gid]],
    }

    for split_name, split_recs in splits.items():
        out_file = os.path.join(output_dir, f"{split_name}.jsonl.gz")
        with gzip.open(out_file, "wt", encoding="utf-8") as f:
            for rec in split_recs:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"✅ Saved {split_name} split: {out_file} ({len(split_recs)} records)")


def main():
    parser = argparse.ArgumentParser(description="Extract dataset from replay logs.")
    parser.add_argument("--input", nargs="+", required=True, help="Input replay file paths (.mjson.gz, .xml)")
    parser.add_argument("--output-dir", default="data/ai/", help="Directory to store dataset splits")
    args = parser.parse_args()

    build_dataset_from_files(args.input, args.output_dir)


if __name__ == "__main__":
    main()
