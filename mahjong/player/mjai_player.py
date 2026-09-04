"""Optional adapter for neural engines speaking the mjai JSON-lines protocol.

The adapter is deliberately transport-only: the game engine remains the
authority for legality, while Mortal/Akochan supplies a decision.  A caller
feeds protocol events with :meth:`send_event` and asks for a legal action with
``choose_action``.  If the process is absent or returns an invalid move, the
configured fallback player is used.
"""

import json
import math
import select
import subprocess
from collections import Counter
from typing import Optional

from mahjong.core.tile import Tile
from mahjong.engine.action import Action, ActionType, AvailableActions
from mahjong.player.base import Player, GameView
from mahjong.ui.tile_display import tile_to_simple_str

_HONOR_TO_MJAI = {"東": "E", "南": "S", "西": "W", "北": "N",
                  "白": "P", "發": "F", "中": "C"}


def tile_to_mjai_str(tile: Optional[Tile]) -> Optional[str]:
    if tile is None:
        return None
    # MJAI spells red fives as 5mr/5pr/5sr.  MahjongCLI uses 0m/0p/0s
    # internally, so normalize at the protocol boundary.
    if tile.is_red:
        suit = {0: "m", 1: "p", 2: "s"}[int(tile.suit)]
        return f"5{suit}r"
    name = tile_to_simple_str(tile)
    return _HONOR_TO_MJAI.get(name, name)


class MjaiPlayer(Player):
    """Persistent mjai subprocess with safe native-AI fallback."""

    def __init__(self, name: str, seat: int, command: list[str], fallback: Player,
                 response_timeout: float = 2.0, oracle: bool = False,
                 defer_reach_ack: bool = False):
        super().__init__(name)
        self.seat = seat
        self.command = command
        self.fallback = fallback
        self.response_timeout = response_timeout
        self.oracle = oracle
        self.defer_reach_ack = defer_reach_ack
        self.process: Optional[subprocess.Popen] = None
        self._round_meta: dict = {}
        self._disabled = False
        self._suppress_next_reach = False
        self.last_response: Optional[dict] = None
        self.last_evaluations: list = []
        self.last_action_source = "fallback"
        self._pending_reach = False

    def _fallback_action(self, game_view: GameView,
                         available: AvailableActions) -> Action:
        """Use the native player and discard metadata from the old state."""
        self.last_action_source = "fallback"
        self.last_evaluations = []
        return self.fallback.choose_action(game_view, available)

    def start(self) -> bool:
        if self._disabled:
            return False
        if self.process is not None and self.process.poll() is None:
            return True
        if self.process is not None and self.process.poll() is not None:
            # A crashed/invalid external engine must not be respawned for
            # every draw; permanently use the legal native fallback instead.
            self.process = None
            self._disabled = True
            return False
        try:
            self.process = subprocess.Popen(
                [*self.command, str(self.seat)], stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, bufsize=1,
            )
            return True
        except (OSError, ValueError):
            self.process = None
            self._disabled = True
            return False

    def send_event(self, event: dict) -> None:
        if not self.start() or self.process.stdin is None:
            return
        try:
            self.process.stdin.write(json.dumps(event, ensure_ascii=False) + "\n")
            self.process.stdin.flush()
        except (BrokenPipeError, OSError, ValueError):
            self._disable()

    def choose_action(self, game_view: GameView,
                      available: AvailableActions) -> Action:
        # The full event stream is supplied by the host; this method only
        # consumes the engine's next response and validates it structurally.
        if self.start() and self.process.stdout is not None:
            try:
                # Never let a broken/misconfigured engine freeze a round.
                ready, _, _ = select.select([self.process.stdout], [], [], self.response_timeout)
                if not ready:
                    self._disable()
                    return self._fallback_action(game_view, available)
                line = self.process.stdout.readline().strip()
                if not line:
                    self._disable()
                    return self._fallback_action(game_view, available)
                if line:
                    decoded = json.loads(line)
                    # Akochan's pipe mode returns a JSON array of candidate
                    # moves; most mjai engines return one object. Accept both.
                    if isinstance(decoded, list):
                        decoded = next((item for item in decoded
                                        if isinstance(item, dict)), {})
                    self.last_response = decoded if isinstance(decoded, dict) else None
                    if isinstance(decoded, dict) and decoded.get("meta"):
                        self.last_evaluations = self._parse_mortal_meta(decoded.get("meta"))
                    else:
                        self.last_evaluations = []
                    action = self._decode(decoded, available) if isinstance(decoded, dict) else None
                    # Mortal announces riichi first, then waits for the host
                    # to echo Reach before returning the discard.  The game
                    # engine exposes riichi as one atomic Action, so complete
                    # that two-message exchange inside this adapter.
                    if (isinstance(decoded, dict) and decoded.get("type") == "reach"
                            and available.can_riichi):
                        if self.defer_reach_ack:
                            self._pending_reach = True
                            self.last_action_source = "external"
                            return Action(
                                ActionType.RIICHI,
                                available.player,
                                riichi_discard=available.riichi_candidates[0],
                            )
                        self.send_event({"type": "reach", "actor": self.seat})
                        follow_up = self._read_response()
                        if isinstance(follow_up, dict) and follow_up.get("meta"):
                            self.last_evaluations = self._parse_mortal_meta(follow_up.get("meta"))
                        else:
                            self.last_evaluations = []
                        if isinstance(follow_up, dict) and follow_up.get("type") == "dahai":
                            tile = self._find_tile(follow_up.get("pai"),
                                                   available.riichi_candidates)
                            if tile is not None:
                                self._suppress_next_reach = True
                                return Action(ActionType.RIICHI, available.player,
                                              riichi_discard=tile)
                        self._disable()
                        return self._fallback_action(game_view, available)
                    if action is not None and available.contains(action):
                        self.last_action_source = "external"
                        return action
                    self._disable()
            except (json.JSONDecodeError, OSError, ValueError):
                self._disable()
        return self._fallback_action(game_view, available)

    def _read_response(self) -> Optional[dict]:
        """Read one JSON response from the engine, respecting the timeout."""
        if self.process is None or self.process.stdout is None:
            return None
        ready, _, _ = select.select([self.process.stdout], [], [], self.response_timeout)
        if not ready:
            return None
        line = self.process.stdout.readline().strip()
        if not line:
            return None
        decoded = json.loads(line)
        if isinstance(decoded, list):
            decoded = next((item for item in decoded if isinstance(item, dict)), {})
        return decoded if isinstance(decoded, dict) else None
    @staticmethod
    def _parse_mortal_meta(meta: Optional[dict], temperature: float = 0.1) -> list[dict]:
        """Parse Mortal's meta containing q_values and mask_bits and compute softmax probabilities."""
        if not isinstance(meta, dict):
            return []
        q_values = meta.get("q_values")
        mask_bits = meta.get("mask_bits")
        if not isinstance(q_values, list) or not isinstance(mask_bits, int):
            return []
        if not q_values:
            return []

        labels = [
            "1m", "2m", "3m", "4m", "5m", "6m", "7m", "8m", "9m",
            "1p", "2p", "3p", "4p", "5p", "6p", "7p", "8p", "9p",
            "1s", "2s", "3s", "4s", "5s", "6s", "7s", "8s", "9s",
            "E", "S", "W", "N", "P", "F", "C",
            "5mr", "5pr", "5sr",
            "reach",
            "chi_low", "chi_mid", "chi_high",
            "pon",
            "kan",
            "hora",
            "ryukyoku",
            "none",
        ]

        active_indices = [i for i in range(len(labels)) if (mask_bits >> i) & 1]
        if len(active_indices) != len(q_values):
            return []

        temp = max(1e-4, float(temperature))
        scaled = [float(q) / temp for q in q_values]
        max_val = max(scaled)
        exps = [math.exp(x - max_val) for x in scaled]
        sum_exps = sum(exps)
        probs = [x / sum_exps for x in exps] if sum_exps > 0 else [0.0] * len(q_values)

        results = []
        for idx, q, p in zip(active_indices, q_values, probs):
            results.append({
                "label_index": idx,
                "name": labels[idx],
                "q_value": float(q),
                "prob": float(p),
                "prob_pct": float(p * 100.0),
            })
        results.sort(key=lambda x: x["q_value"], reverse=True)
        return results

    def get_last_evaluations(self) -> list[dict]:
        """Return the most recent evaluations from Mortal."""
        return list(self.last_evaluations)

    def get_last_action_source(self) -> str:
        """Return whether the last action came from the external engine."""
        return self.last_action_source

    @property
    def has_pending_reach(self) -> bool:
        """Whether a deferred riichi response awaits human confirmation."""
        return self._pending_reach

    def accept_pending_reach(self) -> bool:
        """Acknowledge a deferred reach after the human accepts riichi."""
        if not self._pending_reach:
            return True
        self._pending_reach = False
        self.send_event({"type": "reach", "actor": self.seat})
        try:
            follow_up = self._read_response()
        except (json.JSONDecodeError, OSError, ValueError):
            follow_up = None
        if not isinstance(follow_up, dict) or follow_up.get("type") != "dahai":
            self._disable()
            return False
        return True

    def cancel_pending_reach(self) -> None:
        """Abort a deferred reach when the human chooses another action."""
        if self._pending_reach:
            self._pending_reach = False
            self._disable()


    def _decode(self, msg: dict, available: AvailableActions) -> Optional[Action]:
        kind = msg.get("type")
        if kind == "none":
            return Action(ActionType.SKIP, available.player)
        mapping = {"dahai": ActionType.DISCARD, "reach": ActionType.RIICHI,
                   "chi": ActionType.CHI, "pon": ActionType.PON,
                   "ankan": ActionType.ANKAN, "kakan": ActionType.SHOUMINKAN,
                   "daiminkan": ActionType.DAIMINKAN, "hora": ActionType.RON,
                   "tsumo": ActionType.TSUMO, "ryukyoku": ActionType.SKIP}
        action_type = mapping.get(kind)
        if action_type is None:
            return None
        if kind == "hora":
            if available.can_tsumo:
                return Action(ActionType.TSUMO, available.player)
            if available.can_ron:
                return Action(ActionType.RON, available.player)
            return None
        if action_type in (ActionType.TSUMO, ActionType.RON):
            return Action(action_type, available.player)
        if action_type == ActionType.ANKAN:
            pool = [group[0] for group in available.can_ankan if group]
            if msg.get("consumed"):
                tile = self._find_tile(msg["consumed"][0], pool)
                return (Action(action_type, available.player, tile=tile)
                        if tile is not None else None)
        elif action_type == ActionType.SHOUMINKAN:
            pool = list(available.can_shouminkan)
        else:
            pool = list(available.can_discard)
        tile = self._find_tile(msg.get("pai"), pool)
        if action_type == ActionType.DISCARD:
            return Action(action_type, available.player, tile=tile) if tile is not None else None
        if action_type == ActionType.RIICHI:
            return Action(action_type, available.player, riichi_discard=tile) if tile is not None else None
        if action_type in (ActionType.ANKAN, ActionType.SHOUMINKAN):
            return Action(action_type, available.player, tile=tile) if tile is not None else None
        # Match calls against the engine-created Meld objects.  Never invent a
        # Meld from untrusted JSON: AvailableActions.contains remains the final
        # legality boundary.
        candidates = {
            ActionType.CHI: available.can_chi,
            ActionType.PON: available.can_pon,
            ActionType.DAIMINKAN: available.can_daiminkan,
        }.get(action_type, [])
        consumed = Counter(str(x) for x in (msg.get("consumed") or []))
        called = msg.get("pai")
        for meld in candidates:
            meld_names = Counter(tile_to_mjai_str(t) for t in meld.tiles)
            if consumed and any(meld_names[name] < count
                                for name, count in consumed.items()):
                continue
            if called and meld.called_tile is not None and called not in {
                    tile_to_mjai_str(meld.called_tile),
                    tile_to_simple_str(meld.called_tile)}:
                continue
            return Action(action_type, available.player, meld=meld)
        return None

    def bind_event_bus(self, event_bus) -> None:
        """Forward engine events as mjai-compatible JSON notifications."""
        for event_type in event_bus_event_types():
            event_bus.subscribe(event_type, self._on_event)

    def _on_event(self, event) -> None:
        if event.event_type.value == "game_start":
            players = event.data.get("players") or []
            names = [p[0] if isinstance(p, (tuple, list)) else str(p) for p in players]
            self.send_event({"type": "start_game", "id": self.seat,
                             "names": names,
                             "kyoku_first": 0, "aka_flag": True})
            return
        if event.event_type.value == "game_end":
            self.send_event({"type": "end_game"})
            return
        if event.event_type.value == "round_end":
            self.send_event({"type": "end_kyoku"})
            return
        if event.event_type.value == "deal":
            players = event.data.get("players") or []
            wall = event.data.get("wall")
            if self.seat < len(players):
                indicators = getattr(wall, "dora_indicators", []) if wall else []
                self.send_event({
                    "type": "start_kyoku",
                    "bakaze": self._round_meta.get("bakaze", "E"),
                    "kyoku": self._round_meta.get("kyoku", 1),
                    "honba": self._round_meta.get("honba", 0),
                    "kyotaku": self._round_meta.get("kyotaku", 0),
                    "oya": self._round_meta.get("oya", 0),
                    "dora_marker": tile_to_mjai_str(indicators[0]) if indicators else "1m",
                    "tehais": [
                        ([tile_to_mjai_str(t) for t in p.hand.closed_tiles]
                         if (i == self.seat or self.oracle) else ["?"] * 13)
                        for i, p in enumerate(players)
                    ],
                    "scores": [p.score for p in players],
                })
            return
        if event.event_type.value == "round_start":
            wind = event.data.get("round_wind")
            self._round_meta = {
                "bakaze": {"east": "E", "south": "S", "west": "W", "north": "N"}.get(
                    str(getattr(wind, "name", "E")).lower(), "E"),
                "honba": event.data.get("honba", 0),
                "kyotaku": event.data.get("kyotaku", 0),
                "kyoku": event.data.get("kyoku", 1),
                "oya": event.data.get("oya", 0),
            }
            self._suppress_next_reach = False
            return
        if (event.event_type.value == "riichi_declare"
                and self._suppress_next_reach
                and event.data.get("player") == self.seat):
            self._suppress_next_reach = False
            return
        if event.event_type.value == "discard" and event.data.get("player") == self.seat:
            self._suppress_next_reach = False
        payload = event_to_mjai(event.event_type.value, event.data, self.seat)
        if payload is not None:
            self.send_event(payload)

    @staticmethod
    def _find_tile(name: Optional[str], tiles: list[Tile]) -> Optional[Tile]:
        return next((tile for tile in tiles if tile_to_mjai_str(tile) == name
                     or tile_to_simple_str(tile) == name), None)

    def _terminate_process(self) -> None:
        process = self.process
        self.process = None
        if process is None:
            return
        for stream in (getattr(process, "stdin", None),
                       getattr(process, "stdout", None)):
            try:
                if stream is not None:
                    stream.close()
            except (OSError, ValueError):
                pass
        try:
            if process.poll() is None:
                process.kill()
        except (OSError, ValueError):
            pass
        try:
            process.wait(timeout=0.5)
        except (subprocess.TimeoutExpired, OSError, ValueError):
            pass

    def _disable(self) -> None:
        self._disabled = True
        self._pending_reach = False
        self._terminate_process()

    def close(self) -> None:
        self._pending_reach = False
        self._terminate_process()

    def __del__(self):
        self.close()


def event_bus_event_types():
    from mahjong.engine.event import EventType
    return list(EventType)


def event_to_mjai(kind: str, data: dict, seat: int) -> Optional[dict]:
    """Translate the engine's public event payload to the mjai wire format."""
    tile = data.get("tile")
    pai = tile_to_mjai_str(tile) if isinstance(tile, Tile) else None
    actor = data.get("player", seat)
    if kind == "game_start":
        # kyoku_first/aka_flag are accepted by Akochan's pipe mode and are
        # harmless for other mjai engines.
        return {"type": "start_game", "id": seat, "kyoku_first": 0,
                "aka_flag": True}
    if kind == "round_start":
        # The following DEAL event carries the private hand and complete
        # start_kyoku payload required by mjai engines.
        return None
    if kind == "draw" and pai:
        # A Mortal instance must only see its own drawn tile.
        return {"type": "tsumo", "actor": actor,
                "pai": pai if actor == seat else "?"}
    if kind == "discard" and pai:
        return {"type": "dahai", "actor": actor, "pai": pai,
                "tsumogiri": bool(data.get("is_tsumogiri", False))}
    if kind in ("chi", "pon", "kan"):
        meld = data.get("meld")
        if meld is not None:
            meld_type = getattr(getattr(meld, "meld_type", None), "value", kind)
            called = getattr(meld, "called_tile", None)
            tiles = list(getattr(meld, "tiles", ()))
            if meld_type == "shouminkan":
                return {"type": "kakan", "actor": actor,
                        "pai": tile_to_mjai_str(tiles[-1]),
                        "consumed": [tile_to_mjai_str(t) for t in tiles[:-1]]}
            if meld_type == "ankan":
                return {"type": "ankan", "actor": actor,
                        "consumed": [tile_to_mjai_str(t) for t in tiles]}
            consumed = list(tiles)
            if called is not None:
                consumed.remove(called)
            payload = {"type": meld_type, "actor": actor,
                       "pai": tile_to_mjai_str(called),
                       "consumed": [tile_to_mjai_str(t) for t in consumed]}
            if getattr(meld, "from_player", None) is not None:
                payload["target"] = meld.from_player
            return payload
    if kind == "riichi_declare":
        return {"type": "reach", "actor": actor}
    if kind == "riichi_accepted":
        return {"type": "reach_accepted", "actor": actor}
    if kind == "dora_reveal":
        marker = data.get("tile") or data.get("new_dora")
        return {"type": "dora", "dora_marker": tile_to_mjai_str(marker)} if marker else None
    if kind in ("tsumo", "ron"):
        target = data.get("from_player", actor)
        return {"type": "hora", "actor": actor, "target": target}
    if kind in ("exhaustive_draw", "abortive_draw"):
        return {"type": "ryukyoku"}
    if kind == "round_end":
        return {"type": "end_kyoku"}
    if kind == "game_end":
        return {"type": "end_game"}
    return None
