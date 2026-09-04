# MahjongCLI AI Design and Improvement Plan

## 1. Purpose

This document defines a practical path from the current native `GreedyAI` to a stronger, measurable Mahjong decision system without breaking the existing game engine.

The design is deliberately incremental. The current CLI must continue to work offline, preserve legal-action guarantees, and retain a deterministic fallback when an optional model is unavailable.

## 2. Current system

### Existing components

| Component | Location | Responsibility |
| --- | --- | --- |
| Player interface | `mahjong/player/base.py` | `Player`, `GameView`, and the information barrier |
| Native AI | `mahjong/player/greedy_ai.py` | Discard, riichi, call, kita, kan, and defense decisions |
| Rule engine | `mahjong/rules/` | Shanten, agari, yaku, fu, scoring, and furiten |
| Runtime engine | `mahjong/engine/` | Legal actions, wall state, round flow, and score updates |
| Replay input | `tests/tenhou_replay/mjai_parser.py` and `parser.py` | MJAI/Tenhou replay decoding |
| Offline benchmark | `scripts/benchmark_ai.py` | Ranked-discard agreement and phase/shanten breakdowns |
| Bundled data | `data/benchmark/` | Reproducible four-player and Sanma Phoenix fixtures |
| Optional external engine | `engines/akochan/` and `mahjong/player/mjai_player.py` | External MJAI-compatible analysis backend |

### Current decision pipeline

```text
GameView + AvailableActions
            |
            v
       GreedyAI.choose_action
            |
   +--------+---------+----------------+
   |                  |                |
  win             riichi/call       discard
   |                  |                |
 accept       value/yaku gates   shanten -> ukeire
                                      |
                              defense or offense
                                      |
                         ranked legal tile candidates
```

The native AI already has special-hand preservation, dora-aware tenpai value, bounded continuation lookahead, Sanma removed-manzu handling, North/kita handling, and riichi defense heuristics. It should remain the safety net for every future model.

## 3. Baseline and objective

The bundled offline baseline is approximately:

| Dataset | Decisions | Top-1 | Top-2 | Top-3 | Main weakness |
| --- | ---: | ---: | ---: | ---: | --- |
| Phoenix 4-player ×10 | 4,413 | 51.82% | 74.55% | 84.70% | 2-shanten route selection |
| Phoenix Sanma ×5 | 1,109 | 52.21% | 74.57% | 84.13% | 2-shanten and open-value choices |

These are discard-agreement measurements, not playing strength. The project must not use Top-1 agreement as a proxy for win rate.

### Success targets

Short-term targets should be measured against held-out replay data, not only the bundled fixtures:

1. No regression in legal-action or invariant tests.
2. At least 55% four-player Top-1 agreement while retaining at least 75% Top-2.
3. At least 55% Sanma Top-1 agreement while retaining Sanma-specific legality.
4. Better 2-shanten agreement without sacrificing 1-shanten performance.
5. Add game outcomes: win rate, deal-in rate, tenpai rate, average hand points, and final placement.

## 4. Design principles

### 4.1 Rules stay authoritative

`mahjong/engine/round.py` and `mahjong/rules/` remain the source of truth. An AI may rank only actions returned by `AvailableActions`; it must never invent a tile, call, win, or kan.

### 4.2 Information barrier is mandatory

Features may use the AI’s hand and public state only:

- own closed tiles, melds, discards, kita count, and riichi state;
- opponent discards, melds, riichi state, kita count, score, and seat information;
- dora indicators, round state, remaining wall count, and legal actions.

Hidden opponent tiles and the unrevealed wall are prohibited in fair-play mode. Full-deal/oracle data is allowed only for offline analysis and self-play tooling.

### 4.3 Deterministic fallback

The native ranker remains callable through the same `Player` interface. Optional models must fail closed to `GreedyAI`, with bounded latency and no game-state mutation.

### 4.4 Separate tactical choice from game value

The system should distinguish:

- hand efficiency: shanten, ukeire, block quality, and wait quality;
- hand value: yaku, dora, fu, tsumo/ron value, and kita;
- risk: riichi danger, visible waits, deal-in probability, and score pressure;
- match strategy: current rank, round, lead/deficit, and placement objectives.

## 5. Staged implementation plan

### Phase 0 — Measurement and correctness

**Goal:** Make every improvement measurable and prevent silent regressions.

Tasks:

- Keep the bundled 4-player and Sanma fixtures as smoke benchmarks.
- Add a fixed train/validation/test split when more replay data is added.
- Extend `BenchmarkStats` with win/tenpai/deal-in/score/placement fields.
- Record decision context: shanten, turn, dealer, dora count, riichi threats, call state, and Sanma flag.
- Keep strict safety separate from human-discard agreement.
- Add regression tests for Sanma removed tiles, kita visibility, furiten, and draw-tile accounting.

Exit criteria:

- `pytest` rule/invariant/AI tests pass.
- Both offline benchmark commands run without network access.
- Every benchmark result includes dataset name, commit, decision count, and configuration.

### Phase 1 — Stronger native tactical evaluator

**Goal:** Improve the current engine without introducing a model dependency.

Tasks:

- Create a reusable visible-tile feature builder instead of duplicating visibility loops.
- Add route-aware 2-shanten evaluation:
  - complete blocks and useful taatsu;
  - excess-block/floating-tile penalties;
  - pair/head preservation;
  - dora and yakuhai retention;
  - special routes for chiitoitsu, kokushi, honitsu, and chanta-like shapes.
- Replace raw ukeire-only ties with a bounded score such as:

  ```text
  tactical_score =
      efficiency_score
    + continuation_score
    + value_route_score
    - danger_score
  ```

- Tune thresholds separately for 4-player and Sanma.
- Keep search bounded to the best few legal discards and cache immutable count-state calculations.

Exit criteria:

- No regression greater than 1 percentage point on any existing bundled phase.
- 2-shanten Top-1 improves on a held-out set.
- Median discard latency remains suitable for interactive play.

### Phase 2 — Replay feature and label pipeline

**Goal:** Turn replay data into clean training and analysis examples.

Add `scripts/build_ai_dataset.py` that converts MJAI/Tenhou rounds into JSONL or compressed JSONL records. Each record should contain:

```json
{
  "version": 1,
  "mode": "4p",
  "round": "E2-1",
  "turn": 8,
  "public": {"remaining_tiles": 50, "dora": ["4p"]},
  "hand": {"closed": ["2m", "3m"], "melds": [], "kita": 0},
  "opponents": [],
  "legal_discards": ["2m", "3m"],
  "human_discard": "2m",
  "outcome": {"won": false, "deal_in": false}
}
```

The actual implementation should use tile indices/integers rather than display strings for compactness. The example is explanatory only.

Requirements:

- Split by game, never by individual decisions, to prevent replay leakage.
- Preserve all legal candidates, not just the human choice.
- Store outcome labels only when they are causally valid for the decision.
- Support both `data/benchmark/*.mjson.gz` and existing Tenhou XML fixtures.

### Phase 3 — Lightweight learned discard ranker

**Goal:** Improve tie-breaking while preserving transparent fallback behavior.

Recommended first model: a small pairwise or multiclass gradient-boosted/tree model from `scikit-learn`.

Features:

- 34-tile hand counts and meld counts;
- shanten form: standard/chiitoitsu/kokushi;
- ukeire and live-copy counts;
- block/taatsu/isolated-tile features;
- dora, red dora, yakuhai, kita, and flush-route indicators;
- turn, remaining tiles, dealer, score rank, and riichi threats;
- per-candidate discard tile features and post-discard features.

Inference contract:

```python
class DiscardPolicy(Protocol):
    def rank(self, game_view: GameView,
             legal_discards: list[Tile]) -> list[Tile]: ...
```

`GreedyAI.rank_discards` remains the fallback and can also supply features to the model. The model must be loaded lazily, have a versioned artifact, and return to native ranking on any error or timeout.

Evaluation:

- candidate Top-1/2/3;
- log loss or pairwise ranking accuracy;
- per-shanten and per-phase slices;
- calibration of confidence;
- game simulation outcomes against fixed opponents.

### Phase 4 — Value and risk model

**Goal:** Make decisions optimize expected match value, not only replay similarity.

Implement a bounded value estimator using the existing scoring engine:

```text
EV(action) =
  P(win)       * expected_win_points
  - P(deal-in) * expected_loss_points
  + placement_pressure
  + riichi_stick/honba effects
```

Initial probabilities can be empirical tables from replay data. Do not claim exact probabilities until they are calibrated against held-out games.

Required scenarios:

- dealer/non-dealer;
- all-last lead or deficit;
- one or more riichi opponents;
- open hands with and without a guaranteed yaku;
- Sanma kita and higher scoring conventions.

### Phase 5 — Self-play and external engines

**Goal:** Use stronger search or learning only after the state/action/evaluation pipeline is stable.

Options, in increasing complexity:

1. Native AI versus native AI for regression simulations.
2. Native AI versus Akochan/MJAI in offline oracle analysis.
3. Replay-trained policy/value model.
4. Self-play reinforcement learning with legality masking.

The bundled Akochan binary and `MjaiPlayer` should be treated as comparison or analysis opponents, not as a hidden dependency of the fair native AI. External engines must communicate through the existing legal-action validation path.

## 6. Proposed module layout

```text
mahjong/player/
├── base.py                  # Player, GameView, OpponentView
├── greedy_ai.py             # deterministic native fallback
├── ai_features.py           # public-state and candidate feature extraction
├── ai_policy.py             # DiscardPolicy protocol and native/model adapters
├── ai_value.py              # score/risk/placement EV estimates
└── mjai_player.py           # external MJAI process adapter

scripts/
├── benchmark_ai.py          # replay agreement + outcome metrics
├── build_ai_dataset.py      # replay -> versioned training records
├── train_ai_policy.py       # optional lightweight model training
└── simulate_ai.py           # offline self-play and A/B simulation

data/
├── benchmark/               # fixed reproducible fixtures
└── ai/                      # generated datasets and model metadata
```

Do not split `greedy_ai.py` prematurely. Extract a module only when the corresponding feature logic has tests and a stable interface.

## 7. Testing strategy

### Unit tests

- Exact shanten/ukeire and Sanma removed-tile behavior.
- Visible tile counts, kita, dora, red fives, and furiten.
- Special-hand route preservation.
- Riichi wait/value ranking.
- Defense safety ordering and no false kabe from duplicate draw accounting.

### Property/invariant tests

- Every returned action is in `AvailableActions`.
- Tile counts remain valid after discard, meld, kan, kita, ron, and tsumo.
- No AI decision mutates `GameView` or hidden state.
- Sanma never proposes 2–8 manzu draws or chi actions.

### Offline evaluation

Run both fixed commands after every AI change:

```bash
python3 scripts/benchmark_ai.py --file data/benchmark/phoenix_4p_10games.mjson.gz
python3 scripts/benchmark_ai.py --file data/benchmark/phoenix_3p_5games.mjson.gz
```

For model changes, also run a held-out split and at least 10,000 simulated rounds against fixed seeds.

## 8. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Replay agreement rewards imitation of bad moves | Add outcome and EV metrics; report both |
| Overfitting to five/ten bundled games | Split by game and add new Phoenix datasets |
| Illegal model action | Rank only engine-provided legal candidates |
| Hidden-information leakage | Construct features only from `GameView` |
| Sanma/yonma rule mixing | Carry `is_sanma` through every evaluator and test both modes |
| Slower scoring/search | Cache count-state calculations and cap candidate expansions |
| External model unavailable | Deterministic native fallback |
| Safety metric optimism | Use common genbutsu intersection and report strict definition |

## 9. Recommended execution order

1. Finish Phase 0 metrics and regression coverage.
2. Extract shared visible-tile features.
3. Improve and benchmark route-aware 2-shanten scoring.
4. Add more replay data and game-level train/validation/test splits.
5. Train a small discard ranker only for close native candidates.
6. Add calibrated value/risk features and simulation metrics.
7. Compare against Akochan/MJAI and only then consider self-play RL.

This order keeps the project shippable at every step: the CLI remains playable, the native AI remains understandable, and every stronger component can be disabled without changing Mahjong rules.
