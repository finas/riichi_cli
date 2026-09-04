# Riichi Mahjong CLI

An interactive Japanese riichi mahjong game for the terminal, written in
Python and rendered with [Rich](https://github.com/Textualize/rich).

The project combines a playable rules engine, explainable native AI, optional
MJAI engine adapters, LAN multiplayer, replay logs, and learning tools in one
source tree.

[简体中文文档](README-CN.md)

## Requirements

- Python 3.10 or newer
- `rich` for the game UI
- `pytest` for the test suite
- A terminal that supports standard ANSI output

The image tile mode additionally requires a compatible terminal such as
Ghostty, Kitty, iTerm2, or WezTerm. Text and Unicode tile modes work in a
a normal terminal without inline image support.

## Install and run

From a checkout:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python3 main.py
```

The package also exposes the `riichi` console command:

```bash
python -m pip install -e .
riichi
```

`python3 -m mahjong` is another supported entrypoint. The game starts in
Chinese; language can be changed in Settings.

## Main menu

The current menu provides:

| Option | Mode |
|---:|---|
| 1 | 4-player hanchan |
| 2 | 4-player east-only game (tonpuu) |
| 3 | 3-player hanchan (sanma) |
| 4 | 3-player east-only game |
| 5 | Spectator mode: AI versus AI |
| 6 | LAN multiplayer: host or join |
| 7 | Settings |
| 8 | Replay browser |
| 9 | Learning mode with an AI coach |
| 10 | Early-hand route practice |
| 0 | Quit |

Settings is main menu option 7. It controls language, time control, AI action
delay, tile display mode, and sound. The available time controls are unlimited,
5+20, 10+20, 15+30, and 60+0 seconds in A+B notation. AI delays are 1, 3, 5,
or random 1–5 seconds.

## Game rules

The engine supports both standard 136-tile four-player games and 108-tile
sanma games. Sanma removes 2m–8m, disables chi, and supports kita (north-tile
extraction). The default starting scores are 25,000 for four-player games and
35,000 for sanma.

Implemented engine responsibilities include:

- Standard, chiitoitsu, and kokushi agari detection
- Standard, chiitoitsu, and kokushi shanten calculation
- Fu calculation and han/fu score calculation for tsumo and ron
- Riichi, double riichi, ippatsu, furiten, and riichi wait locking
- Chi, pon, open kan, concealed kan, added kan, rinshan, and chankan
- Haitei, houtei, abortive draws, exhaustive draws, and triple ron handling
- Dora, ura-dora, red dora, and sanma kita scoring
- Dealer rotation, honba, riichi sticks, game-end ranking, and bust handling

The implemented yaku set includes:

- Regular yaku: riichi, double riichi, ippatsu, menzen tsumo, tanyao, pinfu,
  iipeikou, ryanpeikou, yakuhai, haitei, houtei, rinshan, chankan, chanta,
  junchan, ittsu, sanshoku doujun, sanshoku doukou, toitoi, sanankou,
  honroutou, shousangen, chiitoitsu, honitsu, and chinitsu
- Yakuman: tenhou, chiihou, kokushi musou, suu ankou, daisangen,
  shousuushii, daisuushii, tsuuiisou, chinroutou, ryuuiisou, chuuren poutou,
  and suukantsu

Dora are counted for scoring but do not, by themselves, satisfy the real-yaku
requirement for a win.

## Controls

During a normal turn, the numbered choices select a discard. Available action
keys are shown by the prompt and are validated by the rules engine:

| Key | Action |
|---|---|
| `1`–`14` | Select a tile to discard |
| `t` | Tsumo, when legal |
| `h` | Ron, when legal |
| `r` | Declare riichi and select the riichi discard |
| `p` | Pon, when legal |
| `c` | Chi, when legal |
| `k` | Kan, when legal |
| `n` | Kita in sanma, when legal |
| `9` | Declare the nine-tile abortive draw, when legal |
| `s` | Skip an optional action |
| `H` | Toggle helper panels |
| `G` | Request on-demand Gemini advice |

Lowercase `h` is Ron. Uppercase `H` toggles the helper panels. After riichi,
tsumogiri is forced unless another legal special action is available.

The helper and discard-review panels are read-only. They explain shanten,
ukeire, waits, dora value, shape, danger, alternative discards, and possible
hand routes without changing the live game state.

## AI and coaching

### Native AI

Human games use the built-in `GreedyAI` by default. Its bounded, explainable
ranking considers legal actions, shanten, ukeire, continuation shape, waits,
dora and yaku routes, visible information, and riichi danger. The engine
remains authoritative: an AI can only return an action present in
`AvailableActions`.

The native `AICoach` exposes the same style of analysis to the player,
including tactical stance, safe tiles, win possibility, deal-in risk, and
ranked alternatives.

### Spectator mode

Spectator mode is an AI-versus-AI trainer. The menu offers:

- Native rule-based AI
- Akochan, using the bundled launcher when available
- Mortal, using the bundled Docker launcher when configured

External engines are optional. If a process is unavailable, malformed, or
exceeds its response timeout, `MjaiPlayer` disables it and falls back to the
native AI rather than blocking or mutating the game.

### MJAI-compatible engines

To use a custom JSON-lines MJAI process, select a non-native backend and set
the command. The project appends the seat number as the command's final
argument:

```bash
MAHJONG_AI_BACKEND=external \
MAHJONG_AI_COMMAND='python3 /path/to/engine.py' \
python3 main.py
```

The host sends game events to the process, reads its response, and validates
the response against the legal action set. Useful configuration variables are:

| Variable | Purpose |
|---|---|
| `MAHJONG_AI_BACKEND` | `native`, `akochan`, `mortal`, or another external label |
| `MAHJONG_AI_COMMAND` | Custom MJAI command; ignored when backend is `native` |
| `MAHJONG_AI_TIMEOUT` | External response timeout in seconds |
| `MAHJONG_AI_DISPLAY_NAME` | Label shown for the active external engine |
| `MAHJONG_AI_ORACLE` | Enables Akochan oracle mode for spectator analysis when `1` |

Akochan's bundled executable is platform-specific and is intended for
spectator or offline analysis. Native AI remains the safe default for human
games because its decisions use the visible information barrier.

### Mortal

The launcher at `engines/mortal/run.sh` expects:

- A local Docker image named `mortal:latest`
- A compatible four-player checkpoint named `mortal.pth`
- The checkpoint in `MORTAL_MODEL_DIR`, which defaults to
  `~/Models/mortal`

Run it after configuring those external prerequisites:

```bash
MORTAL_MODEL_DIR=/path/to/mortal-models \
MAHJONG_AI_BACKEND=mortal \
python3 main.py
```

The launcher uses Docker's `linux/amd64` platform and mounts the model
directory read-only. Mortal support is intended for four-player games.

### Learning mode

Learning mode is menu option 9. All opponents are AI-controlled while seat 0
remains manual. Choose a native, Mortal, or Gemini coach. The coach reports
recommended actions, shanten, ukeire, win possibility, deal-in risk, tactical
stance, safe tiles, and top alternatives. The native coach remains available
when an external coach cannot be reached.

### Gemini coach

Gemini is an optional, on-demand strategic layer using the external `agy` CLI.
Install and authenticate `agy` separately, then use Google Gemini in Learning
Mode or press `G` during an action prompt:

```bash
MAHJONG_AGY_PATH=/path/to/agy \
MAHJONG_GEMINI_MODEL=gemini-3.8-flash-low \
python3 main.py
```

Configuration variables:

| Variable | Default | Purpose |
|---|---|---|
| `MAHJONG_AGY_PATH` | `agy` found on `PATH` | Gemini CLI location |
| `MAHJONG_GEMINI_MODEL` | `gemini-3.8-flash-low` | Model name |
| `MAHJONG_GEMINI_LANG` | `zh` | Prompt language |
| `MAHJONG_GEMINI_AUTO` | `0` | Let the Gemini coach query automatically |
| `MAHJONG_COACH_BACKEND` | interactive selection | `gemini`, `mortal`, or `native` |
| `MAHJONG_COACH_COMMAND` | unset | Custom coach MJAI command |
| `MAHJONG_COACH_DISPLAY_NAME` | `Mortal` | Coach label |
| `MAHJONG_COACH_TIMEOUT` | `5` | Early-practice coach timeout |

Gemini advice is cached per visible turn state. Missing credentials, timeouts,
or unavailable network access produce a readable message and preserve local
heuristic fallback behavior.

## LAN multiplayer

Select LAN Multiplayer from the main menu. A host chooses the game format,
nickname, and port (default `7777`), then shares the displayed address. A
client enters the host IP, port, and nickname. Empty seats can be filled by
the host with native AI. The protocol uses length-prefixed JSON messages over
TCP and validates player names and actions at the host.

LAN mode supports the same four-player and sanma formats, including hanchan,
tonpuu, timing settings, score settlement, sound, and board rendering.

## Logs and replay

Every completed local game is recorded as a JSON file named
`game_<session-id>.json`. The default writable log directory is:

- macOS: `~/Library/Application Support/mahjong-cli/logs`
- Linux and other Unix-like systems: `~/.local/state/mahjong-cli/logs`
- Windows: `%LOCALAPPDATA%\\mahjong-cli\\logs`

Override it with:

```bash
MAHJONG_LOG_DIR=/path/to/logs python3 main.py
```

Replay is menu option 8. It scans the same directory, lists the newest logs,
and reconstructs rounds step by step in `mahjong/ui/replay_screen.py`.

Replay controls:

| Key | Action |
|---|---|
| `Enter` | Next step |
| `b` | Previous step |
| number | Jump to a step |
| `q` | Return |

## Tests and verification

Run the full test suite with:

```bash
pytest -q
```

The suite covers core tile/hand/wall behavior, agari and shanten, yaku and
scoring, furiten, round invariants, AI decisions, coach prompts, replay
reconstruction, resource loading, LAN serialization and socket flows, and the
root entrypoint.

The optional Tenhou replay harness lives under `tests/tenhou_replay/`. Tenhou
XML fixtures are not committed; place local XML files under
`tests/xml/failed/` before running the external replay verification workflow:

```bash
pytest -q tests/tenhou_replay/
```

## Offline simulation and data tools

All scripts are runnable from the repository root:

```bash
# Native AI self-play: four-player or sanma
python3 scripts/simulate_ai.py --games 20 --players 4 --mode tonpuu --seed 42

# Compare Greedy, Policy, and Hybrid agents
python3 scripts/simulate_tournament.py --games 8 --mode tonpuu --seed 42

# Build train/validation/test JSONL.GZ splits from replay inputs
python3 scripts/build_ai_dataset.py \
  --input data/benchmark/phoenix_4p_10games.mjson.gz \
  --output-dir data/ai/

# Train the lightweight discard policy
python3 scripts/train_ai_policy.py \
  --train data/ai/train.jsonl.gz \
  --output data/ai/discard_policy.json
```

Additional benchmark entrypoints are available in `scripts/`:

- `benchmark_ai.py` evaluates discard choices against Tenhou/Phoenix replays
- `benchmark_defense_upgrade.py` runs defense scenarios and matchup checks
- `benchmark_mortal_vs_greedy.py` compares Mortal and native AI variants
- `run_benchmark.sh` is a convenience wrapper for the Mortal matchup

These tools are offline unless a command explicitly requests replay downloads
or an external engine.

## Project layout

```text
.
├── main.py                         # Backward-compatible source entrypoint
├── pyproject.toml                  # Package metadata and riichi console script
├── mahjong/
│   ├── cli.py                      # Interactive menu and game orchestration
│   ├── core/                       # Tiles, hands, melds, wall, player state
│   ├── rules/                      # Agari, shanten, yaku, fu, scoring, furiten
│   ├── engine/                     # Game/round state, events, timing, logging
│   ├── player/                     # Human, native AI, policy, hybrid, MJAI
│   ├── analysis/                   # Coach and early-hand practice systems
│   ├── network/                    # LAN server, client, protocol, serialization
│   ├── replay/                     # Game-log loading and state reconstruction
│   └── ui/                         # Rich rendering, input, locales, replay screen
├── data/                           # Scoring table, tiles, audio, AI datasets
├── engines/                        # Akochan and Mortal launchers/assets
├── scripts/                        # Simulation, benchmark, dataset, training tools
├── tests/                          # Regression, invariant, integration, and replay tests
└── docs/                           # AI design and benchmark reports
```

## Design boundaries

The rules engine is the source of truth for legality and scoring. AI and
coaching components receive a `GameView`, rank legal candidates, and fall back
to native behavior on errors or timeouts. Fair-play paths use the player's
hand and public information only; full-deal/oracle behavior is reserved for
explicit spectator or offline analysis configurations.

## License and bundled assets

The project is released under the MIT license. See the repository attribution
files for bundled tile artwork, audio, and the Akochan engine.
