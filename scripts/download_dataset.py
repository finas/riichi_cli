"""Download and bundle real Tenhou Phoenix matches into MJAI datasets.

Fetches real match logs from Tenhou Phoenix table (鳳凰卓), converts them to
standard MJAI (.mjson / .mjson.gz) format, and saves them locally for offline
benchmarking.

Usage:
    python scripts/download_dataset.py --num-games 10 --output data/benchmark/phoenix_4p_10games.mjson.gz
    python scripts/download_dataset.py --num-games 10 --player-type 3p --output data/benchmark/phoenix_3p_10games.mjson.gz
"""

import argparse
import gzip
import os
import sys
import tempfile
import time
import urllib.request

# Ensure repo root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.tenhou_replay.parser import parse
from tests.tenhou_replay.mjai_parser import rounds_to_mjai
from scripts.benchmark_ai import fetch_phoenix_log_ids, fetch_tenhou_xml


def download_and_bundle_dataset(num_games: int, player_type: str, output_path: str):
    print(f"🀄 Fetching {num_games} recent Phoenix ({player_type}) log IDs from Tenhou...")
    log_ids = fetch_phoenix_log_ids(num_games=num_games, player_type=player_type)
    print(f"  Found {len(log_ids)} log IDs. Downloading and converting to MJAI...\n")

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    all_mjson_lines = []
    total_rounds = 0
    success_count = 0

    for i, log_id in enumerate(log_ids, 1):
        print(f"  [{i:>2}/{len(log_ids)}] {log_id} ... ", end="")
        try:
            xml_data = fetch_tenhou_xml(log_id)
            with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as f:
                f.write(xml_data)
                temp_path = f.name
            rounds = parse(temp_path)
            os.unlink(temp_path)

            if rounds:
                mjson_str = rounds_to_mjai(rounds, player_names=[f"P{j}" for j in range(rounds[0].num_players)])
                lines = [l for l in mjson_str.splitlines() if l.strip()]
                all_mjson_lines.extend(lines)
                total_rounds += len(rounds)
                success_count += 1
                print(f"OK ({len(rounds)} rounds, {len(lines)} events)")
            else:
                print("SKIP (no rounds parsed)")
        except Exception as e:
            print(f"FAIL ({e})")
        time.sleep(0.4)  # Server rate limiting

    print(f"\n📦 Bundling {success_count} games ({total_rounds} rounds, {len(all_mjson_lines)} events) -> {output_path}")
    content = "\n".join(all_mjson_lines) + "\n"
    if output_path.endswith(".gz"):
        with gzip.open(output_path, "wt", encoding="utf-8") as f:
            f.write(content)
    else:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)

    file_size_kb = os.path.getsize(output_path) / 1024
    print(f"✅ Successfully saved dataset: {output_path} ({file_size_kb:.1f} KB)")


def main():
    parser = argparse.ArgumentParser(description="Download real Phoenix matches into an MJAI dataset.")
    parser.add_argument("--num-games", type=int, default=10, help="Number of games to download")
    parser.add_argument("--player-type", type=str, default="4p", choices=["4p", "3p"], help="4p or 3p (sanma)")
    parser.add_argument("--output", type=str, default="data/benchmark/phoenix_dataset.mjson.gz", help="Output file path")
    args = parser.parse_args()

    download_and_bundle_dataset(args.num_games, args.player_type, args.output)


if __name__ == "__main__":
    main()
