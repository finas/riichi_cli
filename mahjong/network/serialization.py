"""Serialization utilities for network communication."""

from typing import Any, Dict, List, Optional, Tuple

from mahjong.core.tile import ALL_TILES_136, Tile
from mahjong.core.meld import Meld, MeldType
from mahjong.core.hand import Hand
from mahjong.core.player_state import Wind
from mahjong.player.base import GameView, OpponentView
from mahjong.engine.action import Action, ActionType, AvailableActions
from mahjong.rules.scoring import ScoreResult


# === Tile Serialization ===

def tile_to_id(tile: Optional[Tile]) -> Optional[int]:
    """Convert Tile object to its unique 136 integer ID."""
    return tile.id if tile is not None else None


def id_to_tile(tile_id: Optional[int]) -> Optional[Tile]:
    """Convert unique 136 integer ID back to Tile singleton."""
    if tile_id is None:
        return None
    if not (0 <= tile_id < 136):
        raise ValueError(f"Invalid tile ID {tile_id}")
    return ALL_TILES_136[tile_id]


def tiles_to_ids(tiles: List[Tile]) -> List[int]:
    return [t.id for t in tiles]


def ids_to_tiles(ids: List[int]) -> List[Tile]:
    return [ALL_TILES_136[i] for i in ids]


# === Meld Serialization ===

def meld_to_dict(meld: Optional[Meld]) -> Optional[Dict[str, Any]]:
    if meld is None:
        return None
    return {
        "meld_type": meld.meld_type.value,
        "tiles": tiles_to_ids(list(meld.tiles)),
        "called_tile": tile_to_id(meld.called_tile),
        "from_player": meld.from_player,
    }


def dict_to_meld(d: Optional[Dict[str, Any]]) -> Optional[Meld]:
    if d is None:
        return None
    return Meld(
        meld_type=MeldType(d["meld_type"]),
        tiles=tuple(ids_to_tiles(d["tiles"])),
        called_tile=id_to_tile(d.get("called_tile")),
        from_player=d.get("from_player"),
    )


# === Hand Serialization ===

def hand_to_dict(hand: Hand) -> Dict[str, Any]:
    return {
        "closed_tiles": tiles_to_ids(hand.closed_tiles),
        "melds": [meld_to_dict(m) for m in hand.melds],
        "discard_pool": tiles_to_ids(hand.discard_pool),
        "discard_is_tsumogiri": list(hand.discard_is_tsumogiri),
        "discard_called": list(hand.discard_called),
        "draw_tile": tile_to_id(hand.draw_tile),
        "is_riichi": hand.is_riichi,
        "is_double_riichi": hand.is_double_riichi,
        "is_ippatsu": hand.is_ippatsu,
        "riichi_discard_index": hand.riichi_discard_index,
    }


def dict_to_hand(d: Dict[str, Any]) -> Hand:
    hand = Hand()
    hand.closed_tiles = ids_to_tiles(d.get("closed_tiles", []))
    hand.melds = [dict_to_meld(m) for m in d.get("melds", []) if m is not None]
    hand.discard_pool = ids_to_tiles(d.get("discard_pool", []))
    hand.discard_is_tsumogiri = list(d.get("discard_is_tsumogiri", []))
    hand.discard_called = list(d.get("discard_called", []))
    hand.draw_tile = id_to_tile(d.get("draw_tile"))
    hand.is_riichi = bool(d.get("is_riichi", False))
    hand.is_double_riichi = bool(d.get("is_double_riichi", False))
    hand.is_ippatsu = bool(d.get("is_ippatsu", False))
    hand.riichi_discard_index = int(d.get("riichi_discard_index", -1))
    return hand


# === OpponentView Serialization ===

def opponent_view_to_dict(opp: OpponentView) -> Dict[str, Any]:
    return {
        "seat": opp.seat,
        "name": opp.name,
        "score": opp.score,
        "seat_wind": opp.seat_wind.value,
        "is_dealer": opp.is_dealer,
        "is_riichi": opp.is_riichi,
        "melds": [meld_to_dict(m) for m in opp.melds],
        "discard_pool": tiles_to_ids(opp.discard_pool),
        "discard_called": list(opp.discard_called),
        "discard_is_tsumogiri": list(opp.discard_is_tsumogiri),
        "num_closed_tiles": opp.num_closed_tiles,
        "kita_count": opp.kita_count,
        "riichi_discard_index": opp.riichi_discard_index,
    }


def dict_to_opponent_view(d: Dict[str, Any]) -> OpponentView:
    return OpponentView(
        seat=d["seat"],
        name=d["name"],
        score=d["score"],
        seat_wind=Wind(d["seat_wind"]),
        is_dealer=d["is_dealer"],
        is_riichi=d["is_riichi"],
        melds=[dict_to_meld(m) for m in d.get("melds", []) if m is not None],
        discard_pool=ids_to_tiles(d.get("discard_pool", [])),
        discard_called=list(d.get("discard_called", [])),
        discard_is_tsumogiri=list(d.get("discard_is_tsumogiri", [])),
        num_closed_tiles=d.get("num_closed_tiles", 13),
        kita_count=d.get("kita_count", 0),
        riichi_discard_index=d.get("riichi_discard_index", -1),
    )


# === GameView Serialization ===

def game_view_to_dict(gv: GameView) -> Dict[str, Any]:
    return {
        "my_hand": hand_to_dict(gv.my_hand),
        "my_seat": gv.my_seat,
        "my_wind": gv.my_wind.value,
        "my_score": gv.my_score,
        "is_dealer": gv.is_dealer,
        "opponents": [opponent_view_to_dict(opp) for opp in gv.opponents],
        "round_wind": gv.round_wind.value,
        "honba": gv.honba,
        "riichi_sticks": gv.riichi_sticks,
        "remaining_tiles": gv.remaining_tiles,
        "dora_indicators": tiles_to_ids(gv.dora_indicators),
        "round_label": gv.round_label,
        "my_kita_count": gv.my_kita_count,
        "last_discard": tile_to_id(gv.last_discard),
        "last_discard_player": gv.last_discard_player,
    }


def dict_to_game_view(d: Dict[str, Any]) -> GameView:
    return GameView(
        my_hand=dict_to_hand(d["my_hand"]),
        my_seat=d["my_seat"],
        my_wind=Wind(d["my_wind"]),
        my_score=d["my_score"],
        is_dealer=d["is_dealer"],
        opponents=[dict_to_opponent_view(opp) for opp in d.get("opponents", [])],
        round_wind=Wind(d.get("round_wind", 0)),
        honba=d.get("honba", 0),
        riichi_sticks=d.get("riichi_sticks", 0),
        remaining_tiles=d.get("remaining_tiles", 70),
        dora_indicators=ids_to_tiles(d.get("dora_indicators", [])),
        round_label=d.get("round_label", ""),
        my_kita_count=d.get("my_kita_count", 0),
        last_discard=id_to_tile(d.get("last_discard")),
        last_discard_player=d.get("last_discard_player"),
    )


# === AvailableActions Serialization ===

def available_actions_to_dict(av: AvailableActions) -> Dict[str, Any]:
    return {
        "player": av.player,
        "can_tsumo": av.can_tsumo,
        "can_riichi": av.can_riichi,
        "can_ankan": [tiles_to_ids(group) for group in av.can_ankan],
        "can_shouminkan": tiles_to_ids(av.can_shouminkan),
        "can_discard": tiles_to_ids(av.can_discard),
        "can_chi": [meld_to_dict(m) for m in av.can_chi],
        "can_pon": [meld_to_dict(m) for m in av.can_pon],
        "can_daiminkan": [meld_to_dict(m) for m in av.can_daiminkan],
        "can_ron": av.can_ron,
        "can_kita": av.can_kita,
        "can_kyuushu": av.can_kyuushu,
        "riichi_candidates": tiles_to_ids(av.riichi_candidates),
    }


def dict_to_available_actions(d: Dict[str, Any]) -> AvailableActions:
    return AvailableActions(
        player=d["player"],
        can_tsumo=bool(d.get("can_tsumo", False)),
        can_riichi=bool(d.get("can_riichi", False)),
        can_ankan=[ids_to_tiles(group) for group in d.get("can_ankan", [])],
        can_shouminkan=ids_to_tiles(d.get("can_shouminkan", [])),
        can_discard=ids_to_tiles(d.get("can_discard", [])),
        can_chi=[dict_to_meld(m) for m in d.get("can_chi", []) if m is not None],
        can_pon=[dict_to_meld(m) for m in d.get("can_pon", []) if m is not None],
        can_daiminkan=[dict_to_meld(m) for m in d.get("can_daiminkan", []) if m is not None],
        can_ron=bool(d.get("can_ron", False)),
        can_kita=bool(d.get("can_kita", False)),
        can_kyuushu=bool(d.get("can_kyuushu", False)),
        riichi_candidates=ids_to_tiles(d.get("riichi_candidates", [])),
    )


# === Action Serialization ===

def action_to_dict(act: Action) -> Dict[str, Any]:
    return {
        "action_type": act.action_type.value,
        "player": act.player,
        "tile": tile_to_id(act.tile),
        "meld": meld_to_dict(act.meld),
        "riichi_discard": tile_to_id(act.riichi_discard),
    }


def dict_to_action(d: Dict[str, Any]) -> Action:
    return Action(
        action_type=ActionType(d["action_type"]),
        player=d["player"],
        tile=id_to_tile(d.get("tile")),
        meld=dict_to_meld(d.get("meld")),
        riichi_discard=id_to_tile(d.get("riichi_discard")),
    )


# === ScoreResult Serialization ===

def score_result_to_dict(sr: Optional[ScoreResult]) -> Optional[Dict[str, Any]]:
    if sr is None:
        return None
    return {
        "yaku": [list(y) for y in sr.yaku],
        "han": sr.han,
        "fu": sr.fu,
        "base_points": sr.base_points,
        "total_points": sr.total_points,
        "dealer_payment": sr.dealer_payment,
        "non_dealer_payment": sr.non_dealer_payment,
        "ron_payment": sr.ron_payment,
        "is_dealer": sr.is_dealer,
        "is_tsumo": sr.is_tsumo,
        "honba": sr.honba,
    }


def dict_to_score_result(d: Optional[Dict[str, Any]]) -> Optional[ScoreResult]:
    if d is None:
        return None
    return ScoreResult(
        yaku=[tuple(y) for y in d.get("yaku", [])],
        han=d.get("han", 0),
        fu=d.get("fu", 0),
        base_points=d.get("base_points", 0),
        total_points=d.get("total_points", 0),
        dealer_payment=d.get("dealer_payment", 0),
        non_dealer_payment=d.get("non_dealer_payment", 0),
        ron_payment=d.get("ron_payment", 0),
        is_dealer=bool(d.get("is_dealer", False)),
        is_tsumo=bool(d.get("is_tsumo", False)),
        honba=d.get("honba", 0),
    )
