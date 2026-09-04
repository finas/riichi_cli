"""Versioned, frontend-neutral messages for external game clients.

The protocol deliberately transports only ``GameView`` data and legal
``AvailableActions``.  Hidden opponent hands, the wall, and engine-owned
objects are never exposed to a frontend.
"""

import json
from typing import Any, Dict, Optional

from mahjong.core.meld import Meld
from mahjong.core.tile import Tile
from mahjong.engine.action import Action, AvailableActions
from mahjong.engine.event import EventType, GameEvent
from mahjong.player.base import GameView
from mahjong.rules.scoring import ScoreResult
from mahjong.network.serialization import (
    action_to_dict,
    available_actions_to_dict,
    dict_to_action,
    game_view_to_dict,
    score_result_to_dict,
)


PROTOCOL_NAME = "riichi-mahjong"
PROTOCOL_VERSION = 1


class ProtocolError(ValueError):
    """Raised when a client sends an invalid or unsupported message."""


def make_message(message_type: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Build a protocol envelope."""
    if not isinstance(message_type, str) or not message_type:
        raise ProtocolError("message type must be a non-empty string")
    return {
        "protocol": PROTOCOL_NAME,
        "version": PROTOCOL_VERSION,
        "type": message_type,
        "payload": payload or {},
    }


def encode_message(message: Dict[str, Any]) -> str:
    """Encode one protocol message without introducing non-JSON values."""
    try:
        return json.dumps(message, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ProtocolError(f"message is not JSON serializable: {exc}") from exc


def decode_message(line: str) -> Dict[str, Any]:
    """Decode and validate one newline-delimited protocol message."""
    try:
        message = json.loads(line)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ProtocolError("invalid JSON message") from exc

    if not isinstance(message, dict):
        raise ProtocolError("message must be a JSON object")
    if message.get("protocol") != PROTOCOL_NAME:
        raise ProtocolError("unsupported protocol")
    if message.get("version") != PROTOCOL_VERSION:
        raise ProtocolError("unsupported protocol version")
    if not isinstance(message.get("type"), str) or not message["type"]:
        raise ProtocolError("message type is required")
    if not isinstance(message.get("payload", {}), dict):
        raise ProtocolError("message payload must be an object")
    return message


def action_request_message(request_id: int, game_view: GameView,
                           available: AvailableActions) -> Dict[str, Any]:
    """Build a request for a frontend to choose one legal action."""
    return make_message("action_request", {
        "request_id": request_id,
        "player": available.player,
        "game_view": game_view_to_dict(game_view),
        "available": available_actions_to_dict(available),
    })


def action_response_message(request_id: int, action: Action) -> Dict[str, Any]:
    """Build a client action response message."""
    return make_message("action_response", {
        "request_id": request_id,
        "action": action_to_dict(action),
    })


def action_from_response(message: Dict[str, Any]) -> tuple[int, Action]:
    """Extract a request ID and action from an action response."""
    if message.get("type") != "action_response":
        raise ProtocolError("expected action_response")
    payload = message.get("payload", {})
    request_id = payload.get("request_id")
    action_data = payload.get("action")
    if not isinstance(request_id, int) or not isinstance(action_data, dict):
        raise ProtocolError("action_response requires request_id and action")
    try:
        action = dict_to_action(action_data)
    except (KeyError, TypeError, ValueError) as exc:
        raise ProtocolError("invalid action payload") from exc
    return request_id, action


def game_config_to_dict(config) -> Dict[str, Any]:
    """Serialize the public match configuration."""
    time_control = config.time_control
    return {
        "num_players": config.num_players,
        "is_sanma": config.is_sanma,
        "is_tonpuu": config.is_tonpuu,
        "starting_score": config.starting_score,
        "target_score": config.target_score,
        "time_control": {
            "name": time_control.name,
            "base_seconds": time_control.base_seconds,
            "bank_seconds": time_control.bank_seconds,
        },
    }


def round_result_to_dict(result) -> Dict[str, Any]:
    """Serialize a completed round without leaking engine objects."""
    return {
        "winners": list(result.winners),
        "loser": result.loser,
        "score_results": [
            {"player": player, "result": score_result_to_dict(score)}
            for player, score in result.score_results
        ],
        "score_changes": list(result.score_changes),
        "is_draw": result.is_draw,
        "draw_type": result.draw_type,
        "tenpai_players": list(result.tenpai_players),
        "dealer_continues": result.dealer_continues,
        "riichi_sticks_winner": result.riichi_sticks_winner,
        "riichi_sticks_on_table": result.riichi_sticks_on_table,
        "win_tile": result.win_tile.id if result.win_tile is not None else None,
    }


_UNSUPPORTED = object()


def _safe_event_value(value: Any):
    """Convert public event values while dropping unknown engine objects."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Tile):
        return value.id
    if isinstance(value, Meld):
        return {
            "meld_type": value.meld_type.value,
            "tiles": [tile.id for tile in value.tiles],
            "called_tile": value.called_tile.id if value.called_tile else None,
            "from_player": value.from_player,
        }
    if isinstance(value, ScoreResult):
        return score_result_to_dict(value)
    if hasattr(value, "value") and isinstance(value.value, (bool, int, float, str)):
        return value.value
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            safe = _safe_event_value(item)
            if safe is not _UNSUPPORTED:
                result[str(key)] = safe
        return result
    if isinstance(value, (list, tuple)):
        result = []
        for item in value:
            safe = _safe_event_value(item)
            if safe is _UNSUPPORTED:
                return _UNSUPPORTED
            result.append(safe)
        return result
    return _UNSUPPORTED


def event_to_dict(event: GameEvent) -> Dict[str, Any]:
    """Serialize a safe event summary.

    ``DEAL`` events contain full ``PlayerState`` and ``Wall`` objects for the
    logger.  Those fields are intentionally omitted from the frontend event;
    the accompanying ``GameView`` snapshot is the only state a client gets.
    """
    data = {}
    for key, value in event.data.items():
        if key in {"players", "wall", "config"}:
            continue
        safe = _safe_event_value(value)
        if safe is not _UNSUPPORTED:
            data[key] = safe
    event_type = event.event_type.value if isinstance(event.event_type, EventType) else str(event.event_type)
    return {"event_type": event_type, "data": data}


def event_message(event: GameEvent, game_view: Optional[GameView] = None) -> Dict[str, Any]:
    """Build an event message with an optional visible player snapshot."""
    payload: Dict[str, Any] = {"event": event_to_dict(event)}
    if game_view is not None:
        payload["game_view"] = game_view_to_dict(game_view)
    return make_message("event", payload)
