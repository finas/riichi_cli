"""Contract tests for the frontend-neutral application protocol."""

from io import StringIO

import pytest

from mahjong.app.protocol import (
    ProtocolError,
    action_from_response,
    action_request_message,
    decode_message,
    encode_message,
    event_to_dict,
    game_config_to_dict,
    make_message,
)
from mahjong.app.session import ActionRequest, JsonLinesGameService
from mahjong.core.hand import Hand
from mahjong.core.player_state import Wind
from mahjong.core.tile import ALL_TILES_136
from mahjong.engine.action import Action, ActionType, AvailableActions
from mahjong.engine.event import EventType, GameEvent
from mahjong.engine.game import GameConfig
from mahjong.player.base import GameView


def _game_view() -> GameView:
    hand = Hand()
    hand.closed_tiles = [ALL_TILES_136[0]]
    return GameView(
        my_hand=hand,
        my_seat=0,
        my_wind=Wind.EAST,
        my_score=25000,
        is_dealer=True,
    )


def test_protocol_envelope_round_trips_and_rejects_wrong_version():
    message = make_message("ping", {"value": 1})
    restored = decode_message(encode_message(message))

    assert restored == message

    with pytest.raises(ProtocolError, match="unsupported protocol version"):
        decode_message(encode_message({**message, "version": 999}))


def test_action_request_contains_serialized_visible_state():
    available = AvailableActions(
        player=0,
        can_discard=[ALL_TILES_136[0]],
    )
    message = action_request_message(12, _game_view(), available)
    payload = message["payload"]

    assert message["type"] == "action_request"
    assert payload["request_id"] == 12
    assert payload["available"]["can_discard"] == [0]
    assert payload["game_view"]["my_hand"]["closed_tiles"] == [0]


def test_deal_event_drops_hidden_engine_objects():
    event = GameEvent(EventType.DEAL, {
        "players": [object()],
        "wall": object(),
        "dora": ALL_TILES_136[16],
    })

    payload = event_to_dict(event)

    assert payload["event_type"] == "deal"
    assert "players" not in payload["data"]
    assert "wall" not in payload["data"]
    assert payload["data"]["dora"] == 16


def test_json_service_rejects_invalid_action_then_accepts_legal_action():
    available = AvailableActions(player=0, can_discard=[ALL_TILES_136[0]])
    request = ActionRequest(7, _game_view(), available)
    invalid = Action(ActionType.DISCARD, player=0, tile=ALL_TILES_136[4])
    valid = Action(ActionType.DISCARD, player=0, tile=ALL_TILES_136[0])
    reader = StringIO(
        encode_message(make_message("action_response", {
            "request_id": 7,
            "action": {
                "action_type": invalid.action_type.value,
                "player": invalid.player,
                "tile": invalid.tile.id,
                "meld": None,
                "riichi_discard": None,
            },
        })) + "\n" +
        encode_message(make_message("action_response", {
            "request_id": 7,
            "action": {
                "action_type": valid.action_type.value,
                "player": valid.player,
                "tile": valid.tile.id,
                "meld": None,
                "riichi_discard": None,
            },
        })) + "\n"
    )
    writer = StringIO()
    service = JsonLinesGameService(reader, writer)

    result = service._choose_action(request)
    output = [decode_message(line) for line in writer.getvalue().splitlines()]

    assert result.tile is ALL_TILES_136[0]
    assert output[0]["type"] == "action_request"
    assert output[1]["type"] == "error"
    assert output[1]["payload"]["request_id"] == 7


def test_game_config_serializes_time_control_for_frontends():
    config = JsonLinesGameService._make_config({
        "num_players": 3,
        "is_sanma": True,
        "is_tonpuu": True,
        "time_control": {
            "name": "tc.custom",
            "base_seconds": 10,
            "bank_seconds": 20,
        },
    })
    payload = game_config_to_dict(config)

    assert payload["num_players"] == 3
    assert payload["is_sanma"] is True
    assert payload["time_control"]["base_seconds"] == 10
    assert payload["time_control"]["bank_seconds"] == 20


def test_json_service_rejects_inconsistent_player_configuration():
    with pytest.raises(ValueError, match="disagree"):
        JsonLinesGameService._make_config({"num_players": 4, "is_sanma": True})
