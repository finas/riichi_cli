"""Parse MJAI (.mjson / JSON lines) game logs into RoundData and Events.

MJAI is the standardized open format for Riichi Mahjong AI research (used by
Mortal, Tenhou-to-MJAI archives, Mahjong Soul conversions, and M-League transcripts).

Event specification mapping:
  start_game      -> metadata / player names
  start_kyoku     -> RoundData initialization (hands, dealer, dora, honba)
  tsumo           -> EventType.DRAW
  dahai           -> EventType.DISCARD
  pon/chi/kan     -> EventType.MELD
  reach           -> EventType.RIICHI_DECLARE
  reach_accepted  -> EventType.RIICHI_SCORE
  hora            -> EventType.AGARI
  ryukyoku        -> EventType.RYUUKYOKU
  end_kyoku       -> score updates
"""

import gzip
import json
import os
from typing import Dict, List, Optional, Tuple, Union

from mahjong.core.tile import (
    ALL_TILES_136, RED_FIVE_MAN, RED_FIVE_PIN, RED_FIVE_SOU
)
from .decoder import DecodedMeld, TenhouMeldType
from .parser import Event, EventType, RoundData


# Mapping from MJAI tile string to index34
MJAI_HONOR_MAP = {
    "E": 27, "ton": 27, "1z": 27,
    "S": 28, "nan": 28, "2z": 28,
    "W": 29, "sha": 29, "3z": 29,
    "N": 30, "pei": 30, "4z": 30,
    "P": 31, "white": 31, "haku": 31, "5z": 31,
    "F": 32, "green": 32, "hatsu": 32, "6z": 32,
    "C": 33, "red": 33, "chun": 33, "7z": 33,
}

RED_IDS = {4: RED_FIVE_MAN, 13: RED_FIVE_PIN, 22: RED_FIVE_SOU}


def mjai_tile_to_index34_and_red(s: str) -> Tuple[int, bool]:
    """Parse MJAI tile string into (index34, is_red)."""
    if not s or s == "?":
        return 0, False

    # Red fives: "5mr", "5pr", "5sr" or "0m", "0p", "0s"
    if len(s) == 3 and s[2] == "r" and s[0] == "5":
        suit = s[1]
        offset = {"m": 0, "p": 9, "s": 18}[suit]
        return offset + 4, True
    elif len(s) == 2 and s[0] == "0":
        suit = s[1]
        offset = {"m": 0, "p": 9, "s": 18}[suit]
        return offset + 4, True

    # Numbered tiles: "1m".."9m", "1p".."9p", "1s".."9s"
    if len(s) == 2 and s[0].isdigit() and s[1] in ("m", "p", "s"):
        num = int(s[0])
        suit = s[1]
        offset = {"m": 0, "p": 9, "s": 18}[suit]
        return offset + (num - 1), False

    # Honors
    if s in MJAI_HONOR_MAP:
        return MJAI_HONOR_MAP[s], False

    return 0, False


class MJAITracker:
    """Tracks tile allocations and hand holdings per player."""

    def __init__(self, num_players: int = 4):
        self.num_players = num_players
        self.pools: Dict[int, List[int]] = {}
        for idx in range(34):
            base = idx * 4
            self.pools[idx] = [base, base + 1, base + 2, base + 3]
        
        self.player_hands: Dict[int, List[int]] = {p: [] for p in range(num_players)}

    def allocate_tile(self, s: str, player: Optional[int] = None) -> int:
        """Allocate a tile from the available pool and assign to player if specified."""
        idx34, is_red = mjai_tile_to_index34_and_red(s)
        pool = self.pools[idx34]

        allocated_id = -1
        if is_red and idx34 in RED_IDS:
            red_id = RED_IDS[idx34]
            if red_id in pool:
                pool.remove(red_id)
                allocated_id = red_id

        if allocated_id == -1:
            # Pick non-red if possible
            if idx34 in RED_IDS:
                red_id = RED_IDS[idx34]
                non_reds = [x for x in pool if x != red_id]
                if non_reds and not is_red:
                    allocated_id = non_reds[0]
                    pool.remove(allocated_id)
            
            if allocated_id == -1 and pool:
                allocated_id = pool.pop(0)
            elif allocated_id == -1:
                allocated_id = idx34 * 4  # Fallback

        if player is not None and 0 <= player < self.num_players:
            self.player_hands[player].append(allocated_id)

        return allocated_id

    def pop_player_tile(self, s: str, player: int) -> int:
        """Find and remove a tile from the player's held hand."""
        idx34, is_red = mjai_tile_to_index34_and_red(s)
        hand = self.player_hands.get(player, [])

        # 1. Match exact index34 and red
        for tid in hand:
            if tid // 4 == idx34:
                tid_is_red = tid in (RED_FIVE_MAN, RED_FIVE_PIN, RED_FIVE_SOU)
                if tid_is_red == is_red:
                    hand.remove(tid)
                    return tid

        # 2. Match index34
        for tid in hand:
            if tid // 4 == idx34:
                hand.remove(tid)
                return tid

        # 3. Fallback: allocate fresh
        return self.allocate_tile(s)


def parse_mjai_content(content_or_lines: Union[str, List[str], List[dict]]) -> List[RoundData]:
    """Parse MJAI JSON-lines format into a list of RoundData objects."""
    if isinstance(content_or_lines, str):
        lines = content_or_lines.strip().split("\n")
        events_json = [json.loads(line) for line in lines if line.strip()]
    elif isinstance(content_or_lines, list):
        if content_or_lines and isinstance(content_or_lines[0], dict):
            events_json = content_or_lines
        else:
            events_json = [json.loads(line) for line in content_or_lines if line.strip()]
    else:
        raise ValueError("Unsupported MJAI input type")

    rounds: List[RoundData] = []
    cur_round: Optional[RoundData] = None
    tracker: Optional[MJAITracker] = None
    player_scores = [25000, 25000, 25000, 25000]
    num_players = 4

    for ev in events_json:
        ev_type = ev.get("type")

        if ev_type == "start_game":
            names = ev.get("names", [])
            num_players = len(names) if names else 4
            if num_players == 3:
                player_scores = [35000, 35000, 35000]

        elif ev_type == "start_kyoku":
            tracker = MJAITracker(num_players=num_players)
            bakaze = ev.get("bakaze", "E")
            bakaze_idx = {"E": 0, "S": 1, "W": 2, "N": 3}.get(bakaze, 0)
            kyoku = ev.get("kyoku", 1)
            round_number = bakaze_idx * 4 + (kyoku - 1)
            honba = ev.get("honba", 0)
            kyotaku = ev.get("kyotaku", 0)
            oya = ev.get("oya", 0)
            dora_marker_str = ev.get("dora_marker", "1m")
            dora_id = tracker.allocate_tile(dora_marker_str)

            tehais_raw = ev.get("tehais", [])
            hands = []
            for p_idx, th in enumerate(tehais_raw):
                hand_ids = [tracker.allocate_tile(t, player=p_idx) for t in th]
                hands.append(hand_ids)

            scores = list(ev.get("scores", player_scores))
            if len(scores) < num_players:
                scores = [25000] * num_players

            cur_round = RoundData(
                round_number=round_number,
                honba=honba,
                riichi_sticks=kyotaku,
                dealer=oya,
                scores=scores,
                dora_indicator=dora_id,
                hands=hands,
                is_sanma=(num_players == 3),
                num_players=num_players,
                events=[],
            )
            rounds.append(cur_round)

        elif cur_round is not None and tracker is not None:
            if ev_type == "tsumo":
                actor = ev.get("actor", 0)
                pai_str = ev.get("pai", "?")
                tile_id = tracker.allocate_tile(pai_str, player=actor)
                cur_round.events.append(Event(
                    event_type=EventType.DRAW,
                    player=actor,
                    tile_id=tile_id,
                ))

            elif ev_type == "dahai":
                actor = ev.get("actor", 0)
                pai_str = ev.get("pai", "1m")
                tile_id = tracker.pop_player_tile(pai_str, player=actor)
                cur_round.events.append(Event(
                    event_type=EventType.DISCARD,
                    player=actor,
                    tile_id=tile_id,
                ))

            elif ev_type == "pon":
                actor = ev.get("actor", 0)
                target = ev.get("target", 0)
                pai = ev.get("pai", "1m")
                consumed = ev.get("consumed", [])
                
                called_tile_id = tracker.allocate_tile(pai)
                meld_tile_ids = [called_tile_id] + [tracker.pop_player_tile(t, player=actor) for t in consumed]
                from_who = (target - actor) % num_players
                dm = DecodedMeld(
                    meld_type=TenhouMeldType.PON,
                    tiles_136=meld_tile_ids,
                    called_tile_136=called_tile_id,
                    from_who_relative=from_who,
                )
                cur_round.events.append(Event(
                    event_type=EventType.MELD,
                    player=actor,
                    decoded_meld=dm,
                ))

            elif ev_type == "chi":
                actor = ev.get("actor", 0)
                target = ev.get("target", 0)
                pai = ev.get("pai", "1m")
                consumed = ev.get("consumed", [])
                
                called_tile_id = tracker.allocate_tile(pai)
                meld_tile_ids = [called_tile_id] + [tracker.pop_player_tile(t, player=actor) for t in consumed]
                meld_tile_ids.sort(key=lambda tid: tid // 4)
                from_who = (target - actor) % num_players
                dm = DecodedMeld(
                    meld_type=TenhouMeldType.CHI,
                    tiles_136=meld_tile_ids,
                    called_tile_136=called_tile_id,
                    from_who_relative=from_who,
                )
                cur_round.events.append(Event(
                    event_type=EventType.MELD,
                    player=actor,
                    decoded_meld=dm,
                ))

            elif ev_type == "daiminkan":
                actor = ev.get("actor", 0)
                target = ev.get("target", 0)
                pai = ev.get("pai", "1m")
                consumed = ev.get("consumed", [])
                called_tile_id = tracker.allocate_tile(pai)
                meld_tile_ids = [called_tile_id] + [tracker.pop_player_tile(t, player=actor) for t in consumed]
                from_who = (target - actor) % num_players
                dm = DecodedMeld(
                    meld_type=TenhouMeldType.DAIMINKAN,
                    tiles_136=meld_tile_ids,
                    called_tile_136=called_tile_id,
                    from_who_relative=from_who,
                )
                cur_round.events.append(Event(
                    event_type=EventType.MELD,
                    player=actor,
                    decoded_meld=dm,
                ))

            elif ev_type == "kakan":
                actor = ev.get("actor", 0)
                pai = ev.get("pai", "1m")
                called_tile_id = tracker.pop_player_tile(pai, player=actor)
                dm = DecodedMeld(
                    meld_type=TenhouMeldType.KAKAN,
                    tiles_136=[called_tile_id],
                    called_tile_136=called_tile_id,
                    from_who_relative=0,
                )
                cur_round.events.append(Event(
                    event_type=EventType.MELD,
                    player=actor,
                    decoded_meld=dm,
                ))

            elif ev_type == "ankan":
                actor = ev.get("actor", 0)
                consumed = ev.get("consumed", [])
                meld_tile_ids = [tracker.pop_player_tile(t, player=actor) for t in consumed]
                dm = DecodedMeld(
                    meld_type=TenhouMeldType.ANKAN,
                    tiles_136=meld_tile_ids,
                    called_tile_136=-1,
                    from_who_relative=0,
                )
                cur_round.events.append(Event(
                    event_type=EventType.MELD,
                    player=actor,
                    decoded_meld=dm,
                ))

            elif ev_type == "reach":
                actor = ev.get("actor", 0)
                cur_round.events.append(Event(
                    event_type=EventType.RIICHI_DECLARE,
                    player=actor,
                ))

            elif ev_type == "reach_accepted":
                actor = ev.get("actor", 0)
                cur_round.events.append(Event(
                    event_type=EventType.RIICHI_SCORE,
                    player=actor,
                ))

            elif ev_type == "hora":
                actor = ev.get("actor", 0)
                target = ev.get("target", 0)
                pai = ev.get("pai", "1m")
                cur_round.events.append(Event(
                    event_type=EventType.AGARI,
                    player=actor,
                    agari_who=actor,
                    agari_from=target,
                    agari_machi=tracker.allocate_tile(pai),
                ))

            elif ev_type == "ryukyoku":
                cur_round.events.append(Event(
                    event_type=EventType.RYUUKYOKU,
                    player=0,
                ))

            elif ev_type == "end_kyoku":
                deltas = ev.get("scores", [])
                if deltas and len(deltas) == len(player_scores):
                    player_scores = list(deltas)

    return rounds


def parse_mjai_file(path: str) -> List[RoundData]:
    """Parse an MJAI (.mjson or .mjson.gz) file into RoundData."""
    if path.endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return parse_mjai_content(f.read())
    else:
        with open(path, "r", encoding="utf-8") as f:
            return parse_mjai_content(f.read())


def tile_to_mjai_str(tile: Tile) -> str:
    """Convert a Tile object to its MJAI standard string."""
    if tile.is_red:
        suit_char = {0: "m", 1: "p", 2: "s"}[tile.suit.value]
        return f"5{suit_char}r"
    honor_map = {
        27: "E", 28: "S", 29: "W", 30: "N",
        31: "P", 32: "F", 33: "C",
    }
    if tile.index34 in honor_map:
        return honor_map[tile.index34]
    return tile.name


def rounds_to_mjai(rounds: List[RoundData], player_names: Optional[List[str]] = None) -> str:
    """Serialize a list of RoundData objects into standard MJAI JSON-lines."""
    if not rounds:
        return ""

    num_players = rounds[0].num_players
    names = player_names or [f"Player{i}" for i in range(num_players)]
    lines = [json.dumps({"type": "start_game", "names": names}, ensure_ascii=False)]

    for rd in rounds:
        bakaze_str = "E" if rd.round_number < 4 else "S"
        kyoku_num = (rd.round_number % 4) + 1
        dora_tile = ALL_TILES_136[rd.dora_indicator]
        tehais = []
        for hand in rd.hands:
            tehais.append([tile_to_mjai_str(ALL_TILES_136[tid]) for tid in hand])

        start_ev = {
            "type": "start_kyoku",
            "bakaze": bakaze_str,
            "kyoku": kyoku_num,
            "honba": rd.honba,
            "kyotaku": rd.riichi_sticks,
            "oya": rd.dealer,
            "dora_marker": tile_to_mjai_str(dora_tile),
            "tehais": tehais,
            "scores": rd.scores,
        }
        lines.append(json.dumps(start_ev, ensure_ascii=False))

        for ev in rd.events:
            if ev.event_type == EventType.DRAW:
                t = ALL_TILES_136[ev.tile_id] if ev.tile_id >= 0 else None
                lines.append(json.dumps({
                    "type": "tsumo",
                    "actor": ev.player,
                    "pai": tile_to_mjai_str(t) if t else "?",
                }, ensure_ascii=False))

            elif ev.event_type == EventType.DISCARD:
                t = ALL_TILES_136[ev.tile_id]
                lines.append(json.dumps({
                    "type": "dahai",
                    "actor": ev.player,
                    "pai": tile_to_mjai_str(t),
                }, ensure_ascii=False))

            elif ev.event_type == EventType.MELD and ev.decoded_meld:
                dm = ev.decoded_meld
                tiles = [ALL_TILES_136[tid] for tid in dm.tiles_136]
                called_tile = ALL_TILES_136[dm.called_tile_136] if dm.called_tile_136 >= 0 else None

                if dm.meld_type == TenhouMeldType.PON:
                    consumed = [tile_to_mjai_str(t) for t in tiles if t != called_tile]
                    lines.append(json.dumps({
                        "type": "pon",
                        "actor": ev.player,
                        "target": (ev.player + dm.from_who_relative) % num_players,
                        "pai": tile_to_mjai_str(called_tile) if called_tile else "",
                        "consumed": consumed,
                    }, ensure_ascii=False))
                elif dm.meld_type == TenhouMeldType.CHI:
                    consumed = [tile_to_mjai_str(t) for t in tiles if t != called_tile]
                    lines.append(json.dumps({
                        "type": "chi",
                        "actor": ev.player,
                        "target": (ev.player + dm.from_who_relative) % num_players,
                        "pai": tile_to_mjai_str(called_tile) if called_tile else "",
                        "consumed": consumed,
                    }, ensure_ascii=False))
                elif dm.meld_type == TenhouMeldType.ANKAN:
                    consumed = [tile_to_mjai_str(t) for t in tiles]
                    lines.append(json.dumps({
                        "type": "ankan",
                        "actor": ev.player,
                        "consumed": consumed,
                    }, ensure_ascii=False))
                elif dm.meld_type == TenhouMeldType.DAIMINKAN:
                    consumed = [tile_to_mjai_str(t) for t in tiles if t != called_tile]
                    lines.append(json.dumps({
                        "type": "daiminkan",
                        "actor": ev.player,
                        "target": (ev.player + dm.from_who_relative) % num_players,
                        "pai": tile_to_mjai_str(called_tile) if called_tile else "",
                        "consumed": consumed,
                    }, ensure_ascii=False))
                elif dm.meld_type == TenhouMeldType.KAKAN:
                    lines.append(json.dumps({
                        "type": "kakan",
                        "actor": ev.player,
                        "pai": tile_to_mjai_str(called_tile) if called_tile else "",
                    }, ensure_ascii=False))

            elif ev.event_type == EventType.RIICHI_DECLARE:
                lines.append(json.dumps({"type": "reach", "actor": ev.player}, ensure_ascii=False))

            elif ev.event_type == EventType.RIICHI_SCORE:
                lines.append(json.dumps({"type": "reach_accepted", "actor": ev.player}, ensure_ascii=False))

            elif ev.event_type == EventType.AGARI:
                t = ALL_TILES_136[ev.agari_machi] if ev.agari_machi >= 0 else None
                lines.append(json.dumps({
                    "type": "hora",
                    "actor": ev.agari_who,
                    "target": ev.agari_from if ev.agari_from >= 0 else ev.agari_who,
                    "pai": tile_to_mjai_str(t) if t else "?",
                }, ensure_ascii=False))

            elif ev.event_type == EventType.RYUUKYOKU:
                lines.append(json.dumps({"type": "ryukyoku"}, ensure_ascii=False))

        lines.append(json.dumps({"type": "end_kyoku"}, ensure_ascii=False))

    lines.append(json.dumps({"type": "end_game"}, ensure_ascii=False))
    return "\n".join(lines) + "\n"

