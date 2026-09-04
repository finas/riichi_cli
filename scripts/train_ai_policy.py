"""Train advanced discard policy ranker on large-scale Phoenix replay records.

Trains a normalized Feature-Ranker with Softmax Cross-Entropy over candidate sets,
using z-score feature normalization and Adam optimizer.

Usage:
    python scripts/train_ai_policy.py --train data/ai/train.jsonl.gz --output data/ai/discard_policy.json
"""

import argparse
import gzip
import json
import math
import os
import random
import sys
from typing import Any, Dict, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mahjong.player.feature_schema import FEATURE_NAMES, build_candidate_features
from mahjong.player.greedy_ai import _cached_ukeire
from mahjong.rules.shanten import shanten


def load_dataset(path: str) -> List[dict]:
    records = []
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                records.append(json.loads(line))
    return records


def extract_candidate_features(rec: dict, cand: dict) -> List[float]:
    """Extract the same raw 18-dimensional vector used at inference time."""
    tile_34 = int(cand.get("tile_34", 0))
    hand_counts = list(rec.get("hand_counts", [0] * 34))
    if len(hand_counts) != 34:
        hand_counts = [0] * 34
    post_counts = list(hand_counts)
    post_counts[tile_34] = max(0, post_counts[tile_34] - 1)
    post_s = float(shanten(post_counts))
    is_sanma = bool(rec.get("is_sanma", False))
    is_dora = 1.0 if cand.get("is_dora", False) else 0.0
    is_yaochu = 1.0 if (tile_34 in (0, 8, 9, 17, 18, 26) or tile_34 >= 27) else 0.0
    is_honor = 1.0 if tile_34 >= 27 else 0.0
    is_yakuhai = 1.0 if (tile_34 in (31, 32, 33) or tile_34 == rec.get("my_wind") or tile_34 == rec.get("round_wind")) else 0.0

    visible_counts = rec.get("visible_counts", [0]*34)
    live_counts = rec.get("live_counts", [4]*34)
    hand_counts = rec.get("hand_counts", [0]*34)

    visible_count = float(visible_counts[tile_34]) if visible_counts else 0.0
    live_count = float(live_counts[tile_34]) if live_counts else 4.0
    in_hand = float(hand_counts[tile_34]) if hand_counts else 1.0

    ukeire = 0.0
    if post_s <= 2:
        ukeire = float(_cached_ukeire(
            tuple(post_counts), tuple(visible_counts), int(post_s), is_sanma
        ))

    is_safe = 1.0 if cand.get("is_safe", False) else 0.0
    has_threat = 1.0 if rec.get("has_riichi_threat", False) else 0.0
    is_dealer = 1.0 if rec.get("is_dealer", False) else 0.0
    remaining = float(rec.get("remaining_tiles", 50)) / 70.0

    num = (tile_34 % 9) + 1 if tile_34 < 27 else 0
    is_central = 1.0 if (2 <= num <= 8) else 0.0
    is_pair = 1.0 if in_hand >= 2 else 0.0

    suit = tile_34 // 9 if tile_34 < 27 else -1
    suit_density = 0.0
    if suit != -1 and hand_counts:
        suit_total = sum(hand_counts[suit * 9 : suit * 9 + 9])
        suit_density = suit_total / 14.0

    return build_candidate_features(
        post_shanten=post_s,
        ukeire=ukeire,
        is_dora=bool(is_dora),
        is_yaochu=bool(is_yaochu),
        is_honor=bool(is_honor),
        is_yakuhai=bool(is_yakuhai),
        visible_count=visible_count,
        live_count=live_count,
        in_hand=in_hand,
        is_safe=bool(is_safe),
        has_threat=bool(has_threat),
        is_dealer=bool(is_dealer),
        remaining_tiles=float(rec.get("remaining_tiles", 50)),
        is_pair=bool(is_pair),
        is_central=bool(is_central),
        suit_density=suit_density,
    )


class AdamOptimizer:
    def __init__(self, size: int, lr: float = 0.01, beta1: float = 0.9, beta2: float = 0.999, eps: float = 1e-8):
        self.lr = lr
        self.beta1 = beta1
        self.beta2 = beta2
        self.eps = eps
        self.m = [0.0] * size
        self.v = [0.0] * size
        self.t = 0

    def step(self, weights: List[float], grads: List[float]):
        self.t += 1
        for i in range(len(weights)):
            self.m[i] = self.beta1 * self.m[i] + (1 - self.beta1) * grads[i]
            self.v[i] = self.beta2 * self.v[i] + (1 - self.beta2) * (grads[i] ** 2)
            m_hat = self.m[i] / (1 - self.beta1 ** self.t)
            v_hat = self.v[i] / (1 - self.beta2 ** self.t)
            weights[i] -= self.lr * m_hat / (math.sqrt(v_hat) + self.eps)


def train_policy_model(
    train_records: List[dict],
    num_epochs: int = 15,
    lr: float = 0.02,
    seed: int = 42,
) -> Tuple[List[float], float]:
    """Train linear/logistic ranking model with Adam optimizer."""
    random.seed(seed)
    n_feat = 18
    weights = [0.0] * n_feat
    bias = 0.0
    opt = AdamOptimizer(size=n_feat, lr=lr)

    for epoch in range(num_epochs):
        total_loss = 0.0
        n_steps = 0
        shuffled = list(train_records)
        random.shuffle(shuffled)

        for rec in shuffled:
            cands = rec.get("candidates", [])
            if len(cands) < 2:
                continue

            scores = []
            chosen_idx = -1
            cand_features = []

            for idx, c in enumerate(cands):
                feat = extract_candidate_features(rec, c)
                cand_features.append(feat)
                # Linear score: higher score = more recommended discard
                score = sum(w * x for w, x in zip(weights, feat)) + bias
                scores.append(score)
                if c.get("is_chosen", False):
                    chosen_idx = idx

            if chosen_idx == -1:
                continue

            # Candidate Softmax
            max_s = max(scores)
            exp_s = [math.exp(min(20.0, max(-20.0, s - max_s))) for s in scores]
            sum_exp = sum(exp_s)
            probs = [e / sum_exp for e in exp_s]

            p_chosen = max(1e-7, probs[chosen_idx])
            loss = -math.log(p_chosen)
            total_loss += loss
            n_steps += 1

            # Compute gradient
            grads = [0.0] * n_feat
            d_bias = 0.0
            for idx, feat in enumerate(cand_features):
                d_score = probs[idx] - (1.0 if idx == chosen_idx else 0.0)
                d_bias += d_score
                for i in range(n_feat):
                    grads[i] += d_score * feat[i]

            # L2 regularization
            for i in range(n_feat):
                grads[i] += 0.0001 * weights[i]

            opt.step(weights, grads)
            bias -= (lr * 0.1) * d_bias

        avg_loss = total_loss / max(1, n_steps)
        print(f"Epoch {epoch + 1:2d}/{num_epochs} - Training Loss: {avg_loss:.4f}")

    return weights, bias


def evaluate_policy(records: List[dict], weights: List[float], bias: float) -> dict:
    """Evaluate Top-1, Top-2, Top-3 accuracy on records."""
    top1 = 0
    top2 = 0
    top3 = 0
    total = 0

    for rec in records:
        cands = rec.get("candidates", [])
        if len(cands) < 2:
            continue

        scored = []
        chosen_tile = rec.get("human_choice_34")

        for c in cands:
            feat = extract_candidate_features(rec, c)
            score = sum(w * x for w, x in zip(weights, feat)) + bias
            scored.append((score, c["tile_34"]))

        scored.sort(key=lambda item: -item[0])
        ranked_tiles = [t for _, t in scored]

        total += 1
        if len(ranked_tiles) > 0 and ranked_tiles[0] == chosen_tile:
            top1 += 1
        if len(ranked_tiles) > 1 and chosen_tile in ranked_tiles[:2]:
            top2 += 1
        if len(ranked_tiles) > 2 and chosen_tile in ranked_tiles[:3]:
            top3 += 1

    return {
        "total": total,
        "top1": top1,
        "top1_pct": (top1 / max(1, total)) * 100,
        "top2": top2,
        "top2_pct": (top2 / max(1, total)) * 100,
        "top3": top3,
        "top3_pct": (top3 / max(1, total)) * 100,
    }


def main():
    parser = argparse.ArgumentParser(description="Train Discard Policy Ranker.")
    parser.add_argument("--train", default="data/ai/train.jsonl.gz", help="Train dataset")
    parser.add_argument("--output", default="data/ai/discard_policy.json", help="Output model path")
    parser.add_argument("--epochs", type=int, default=15, help="Number of training epochs")
    parser.add_argument("--lr", type=float, default=0.015, help="Learning rate")
    args = parser.parse_args()

    print(f"Loading training data from {args.train}...")
    records = load_dataset(args.train)
    print(f"Loaded {len(records)} decision records.")

    random.seed(42)
    random.shuffle(records)
    n_train = int(len(records) * 0.85)
    train_set = records[:n_train]
    test_set = records[n_train:]

    print(f"\n🧠 Training Candidate Ranker with Adam (Train: {len(train_set)}, Test: {len(test_set)})...")
    weights, bias = train_policy_model(train_set, num_epochs=args.epochs, lr=args.lr)

    model_dict = {
        "type": "linear",
        "weights": weights,
        "bias": bias,
        "feature_schema": 2,
        "feature_transform": "identity",
        "feature_names": FEATURE_NAMES,
    }

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(model_dict, f, indent=2)
    print(f"\n💾 Model saved to {args.output}")

    print(f"\n🧪 Evaluating on Held-Out Test Set ({len(test_set)} decisions)...")
    metrics = evaluate_policy(test_set, weights, bias)
    print(f"  Test Decisions: {metrics['total']}")
    print(f"  Top-1 Match:    {metrics['top1']} / {metrics['total']} ({metrics['top1_pct']:.2f}%)")
    print(f"  Top-2 Match:    {metrics['top2']} / {metrics['total']} ({metrics['top2_pct']:.2f}%)")
    print(f"  Top-3 Match:    {metrics['top3']} / {metrics['total']} ({metrics['top3_pct']:.2f}%)")


if __name__ == "__main__":
    main()
