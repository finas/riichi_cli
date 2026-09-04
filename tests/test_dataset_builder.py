"""Test dataset builder pipeline."""

import gzip
import json
import os
import tempfile
import pytest

from tests.tenhou_replay.mjai_parser import parse_mjai_file
from scripts.build_ai_dataset import process_rounds_to_records, build_dataset_from_files


def test_process_rounds_to_records():
    fixture_path = "tests/fixtures/phoenix_2026.mjson"
    assert os.path.exists(fixture_path)

    rounds = parse_mjai_file(fixture_path)
    records = process_rounds_to_records(rounds, game_id="test_game")

    assert len(records) > 0
    first = records[0]
    assert "game_id" in first
    assert "hand_counts" in first
    assert "candidates" in first
    assert "human_choice_34" in first
    assert len(first["hand_counts"]) == 34
    assert len(first["candidates"]) >= 2
    # Ensure exactly one candidate is marked chosen
    chosen = [c for c in first["candidates"] if c["is_chosen"]]
    assert len(chosen) >= 1


def test_build_dataset_splits():
    fixture_path = "tests/fixtures/phoenix_2026.mjson"
    with tempfile.TemporaryDirectory() as tmp_dir:
        build_dataset_from_files([fixture_path], output_dir=tmp_dir)

        train_path = os.path.join(tmp_dir, "train.jsonl.gz")
        assert os.path.exists(train_path)
        with gzip.open(train_path, "rt", encoding="utf-8") as f:
            lines = [json.loads(line) for line in f]
            assert len(lines) > 0
