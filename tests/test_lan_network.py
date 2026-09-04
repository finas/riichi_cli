"""Integration tests for LAN networking, handshake, and remote player action loop."""

import socket
import threading
import time
from io import StringIO
from types import SimpleNamespace
from unittest.mock import Mock

from rich.console import Console

from mahjong.core.tile import ALL_TILES_136
from mahjong.core.hand import Hand
from mahjong.core.player_state import Wind
from mahjong.player.base import GameView, OpponentView
from mahjong.engine.action import Action, ActionType, AvailableActions
from mahjong.network.protocol import MessageType, NetworkMessage, send_message, recv_message
from mahjong.network.remote_player import RemotePlayer
from mahjong.network.server import LANServer
from mahjong.network.client import LANClient
from mahjong.network.serialization import (
    dict_to_game_view, dict_to_available_actions, action_to_dict
)
from mahjong.engine.ai_delay import AIDelay
from mahjong.engine.time_control import TimeControl


def test_protocol_framing_roundtrip():
    msg = NetworkMessage(
        msg_type=MessageType.HELLO,
        payload={"name": "Alice", "version": "1.0", "custom": [1, 2, 3]}
    )
    raw_bytes = msg.to_bytes()
    # First 4 bytes must be length prefix
    assert len(raw_bytes) > 4
    # Recreate from payload
    restored = NetworkMessage.from_bytes(raw_bytes[4:])
    assert restored.msg_type == MessageType.HELLO
    assert restored.payload["name"] == "Alice"
    assert restored.payload["custom"] == [1, 2, 3]


def test_send_message_timeout_is_bounded_and_restored():
    class StalledSocket:
        def __init__(self):
            self.timeout = None
            self.timeouts = []

        def gettimeout(self):
            return self.timeout

        def settimeout(self, value):
            self.timeouts.append(value)
            self.timeout = value

        def sendall(self, _data):
            raise socket.timeout()

    sock = StalledSocket()
    assert not send_message(
        sock,
        NetworkMessage(MessageType.PING),
        timeout=0.25,
    )
    assert sock.timeouts == [0.25, None]


def test_server_client_handshake_and_lobby():
    # Pick an ephemeral free port
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.bind(("127.0.0.1", 0))
    port = server_sock.getsockname()[1]
    server_sock.close()

    server = LANServer(host="127.0.0.1", port=port, num_players=4, host_player_name="HostAlice")
    server.start_lobby()

    try:
        # Create client 1
        sock1 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock1.connect(("127.0.0.1", port))
        send_message(sock1, NetworkMessage(MessageType.HELLO, {"name": "Bob"}))
        ack1 = recv_message(sock1)
        assert ack1 is not None
        assert ack1.msg_type == MessageType.HELLO_ACK
        assert ack1.payload["seat"] == 1

        # Check lobby broadcast on client 1
        lobby1 = recv_message(sock1)
        assert lobby1 is not None
        assert lobby1.msg_type == MessageType.LOBBY_UPDATE
        assert "HostAlice" in lobby1.payload["players"]
        assert "Bob" in lobby1.payload["players"]

        # Create client 2
        sock2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock2.connect(("127.0.0.1", port))
        send_message(sock2, NetworkMessage(MessageType.HELLO, {"name": "Charlie"}))
        ack2 = recv_message(sock2)
        assert ack2 is not None
        assert ack2.payload["seat"] == 2

        # Check updated lobby on client 1 and client 2
        lobby1_updated = recv_message(sock1)
        assert lobby1_updated is not None
        assert "Charlie" in lobby1_updated.payload["players"]

        sock1.close()
        sock2.close()
    finally:
        server.stop()


def test_server_rejects_duplicate_player_names():
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.bind(("127.0.0.1", 0))
    port = server_sock.getsockname()[1]
    server_sock.close()

    server = LANServer(
        host="127.0.0.1",
        port=port,
        num_players=4,
        host_player_name="HostAlice",
    )
    server.start_lobby()
    sock1 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock1.connect(("127.0.0.1", port))
        send_message(sock1, NetworkMessage(
            MessageType.HELLO, {"name": "Bob"}))
        assert recv_message(sock1).msg_type == MessageType.HELLO_ACK
        assert recv_message(sock1).msg_type == MessageType.LOBBY_UPDATE

        sock2.connect(("127.0.0.1", port))
        send_message(sock2, NetworkMessage(
            MessageType.HELLO, {"name": " bob "}))
        error = recv_message(sock2)
        assert error is not None
        assert error.msg_type == MessageType.ERROR
        assert "already in use" in error.payload["message"]
        assert len(server.clients) == 1
    finally:
        sock1.close()
        sock2.close()
        server.stop()


def test_remote_player_action_exchange():
    # Test remote player socket communication directly
    server_listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_listener.bind(("127.0.0.1", 0))
    server_listener.listen(1)
    port = server_listener.getsockname()[1]

    def client_behavior():
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect(("127.0.0.1", port))
        # Receive action request
        msg = recv_message(sock)
        assert msg is not None
        assert msg.msg_type == MessageType.ACTION_REQUEST
        gv = dict_to_game_view(msg.payload["game_view"])
        av = dict_to_available_actions(msg.payload["available"])
        assert av.can_discard
        # Respond with discard action
        chosen_tile = av.can_discard[0]
        resp = NetworkMessage(MessageType.ACTION_RESPONSE, {
            "action": action_to_dict(Action(ActionType.DISCARD, player=1, tile=chosen_tile))
        })
        send_message(sock, resp)
        sock.close()

    t = threading.Thread(target=client_behavior)
    t.start()

    conn, _ = server_listener.accept()
    remote = RemotePlayer("ClientPlayer", conn, seat=1, timeout=5.0)

    # Build dummy GameView and AvailableActions
    hand = Hand()
    for tid in [0, 4, 8, 12, 16, 20, 24, 28, 32, 36, 40, 44, 48]:
        hand.closed_tiles.append(ALL_TILES_136[tid])
    hand.draw(ALL_TILES_136[52])

    gv = GameView(
        my_hand=hand,
        my_seat=1,
        my_wind=Wind.SOUTH,
        my_score=25000,
        is_dealer=False,
    )
    available = AvailableActions(player=1, can_discard=hand.closed_tiles)

    chosen_action = remote.choose_action(gv, available)
    assert chosen_action.action_type == ActionType.DISCARD
    assert chosen_action.player == 1
    assert chosen_action.tile.id == hand.closed_tiles[0].id

    t.join()
    conn.close()
    server_listener.close()


def test_remote_player_timeout_fallback():
    # Test that RemotePlayer cleanly falls back to auto-discard on disconnect/timeout
    server_listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_listener.bind(("127.0.0.1", 0))
    server_listener.listen(1)
    port = server_listener.getsockname()[1]

    # Connect client and close immediately
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect(("127.0.0.1", port))
    sock.close()

    conn, _ = server_listener.accept()
    remote = RemotePlayer("DisconnectedPlayer", conn, seat=1, timeout=0.5)

    hand = Hand()
    for tid in [0, 4, 8, 12, 16, 20, 24, 28, 32, 36, 40, 44, 48]:
        hand.closed_tiles.append(ALL_TILES_136[tid])
    hand.draw(ALL_TILES_136[52])

    gv = GameView(
        my_hand=hand,
        my_seat=1,
        my_wind=Wind.SOUTH,
        my_score=25000,
        is_dealer=False,
    )
    available = AvailableActions(player=1, can_discard=hand.closed_tiles)

    # Should not hang or raise; should return fallback discard
    fallback_act = remote.choose_action(gv, available)
    assert fallback_act.action_type == ActionType.DISCARD
    assert fallback_act.player == 1
    assert fallback_act.tile.id == hand.closed_tiles[-1].id

    conn.close()
    server_listener.close()


def test_full_lan_game_simulation():
    # Complete end-to-end LAN game simulation over socket (1 host + 1 remote + 1 AI in 3p tonpuusen)
    from mahjong.player.greedy_ai import GreedyAI
    from mahjong.engine.ai_delay import AIDelay

    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.bind(("127.0.0.1", 0))
    port = server_sock.getsockname()[1]
    server_sock.close()

    server = LANServer(
        host="127.0.0.1",
        port=port,
        is_sanma=True,
        is_tonpuu=True,
        host_player_name="HostAlice",
        ai_delay=AIDelay("ai_delay.instant", 0.0),
    )
    server.start_lobby()

    # Remote Client thread
    client_done = threading.Event()
    client_received_game_end = threading.Event()

    def remote_client_thread():
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect(("127.0.0.1", port))
        send_message(sock, NetworkMessage(MessageType.HELLO, {"name": "RemoteBob"}))
        ack = recv_message(sock)
        assert ack.msg_type == MessageType.HELLO_ACK

        ai_brain = GreedyAI("RemoteBob")

        while True:
            msg = recv_message(sock)
            if not msg:
                break
            if msg.msg_type == MessageType.ACTION_REQUEST:
                gv = dict_to_game_view(msg.payload["game_view"])
                av = dict_to_available_actions(msg.payload["available"])
                action = ai_brain.choose_action(gv, av)
                resp = NetworkMessage(MessageType.ACTION_RESPONSE, {"action": action_to_dict(action)})
                send_message(sock, resp)
            elif msg.msg_type == MessageType.GAME_END:
                client_received_game_end.set()
                break

        sock.close()
        client_done.set()

    c_thread = threading.Thread(target=remote_client_thread)
    c_thread.start()

    # Wait for client to join lobby
    for _ in range(50):
        if len(server.clients) == 1:
            break
        time.sleep(0.05)
    assert len(server.clients) == 1

    # Run game on host
    host_ai = GreedyAI("HostAlice")
    server.run_game(host_ai)

    c_thread.join(timeout=10.0)
    assert client_received_game_end.is_set()


def test_remote_player_marks_client_failed_when_passive_send_fails(monkeypatch):
    from mahjong.network import remote_player as remote_player_module

    sock = Mock()
    remote = RemotePlayer("ClientPlayer", sock, seat=1)
    monkeypatch.setattr(remote_player_module, "send_message", Mock(return_value=False))

    remote.notify_event("draw", {})

    assert not remote.is_connected
    sock.close.assert_called_once()


def test_client_game_loop_can_play_event_sounds(monkeypatch):
    from mahjong.network import client as client_module

    client = LANClient(Console(file=StringIO()))
    client.sock = object()
    client.is_connected = True
    client.renderer.render_game_view = Mock()

    game_view = SimpleNamespace(last_discard=ALL_TILES_136[0])
    monkeypatch.setattr(
        client_module,
        "dict_to_game_view",
        Mock(return_value=game_view),
    )
    monkeypatch.setattr(client_module, "play_tile_sound", Mock())
    monkeypatch.setattr(client_module, "play_action_sound", Mock())
    monkeypatch.setattr(
        client_module,
        "recv_message",
        Mock(side_effect=[
            NetworkMessage(MessageType.GAME_VIEW_UPDATE, {"game_view": {"present": True}}),
            NetworkMessage(MessageType.EVENT, {"event_type": "chi"}),
            None,
        ]),
    )

    client.run_game_loop()

    client_module.play_tile_sound.assert_called_once_with(ALL_TILES_136[0])
    client_module.play_action_sound.assert_called_once_with("chi")


def test_client_preserves_server_handshake_error(monkeypatch):
    from mahjong.network import client as client_module

    class FakeSocket:
        def __init__(self, *_args):
            self.closed = False

        def settimeout(self, _timeout):
            pass

        def connect(self, _address):
            pass

        def close(self):
            self.closed = True

    fake_sock = FakeSocket()
    monkeypatch.setattr(client_module.socket, "socket", lambda *_args: fake_sock)
    monkeypatch.setattr(client_module, "send_message", Mock(return_value=True))
    monkeypatch.setattr(
        client_module,
        "recv_message",
        Mock(return_value=NetworkMessage(
            MessageType.ERROR, {"message": "Player name already in use"})),
    )

    client = LANClient(Console(file=StringIO()))
    assert not client.connect("127.0.0.1", 7777, "Bob")
    assert client.last_error == "Player name already in use"
    assert fake_sock.closed


def test_host_constructs_human_player_with_explicit_dependencies(monkeypatch):
    from mahjong.network import lan_ui

    captured = {}

    class FakeServer:
        def __init__(self, **kwargs):
            captured["server_kwargs"] = kwargs

        def start_lobby(self):
            pass

        def run_game(self, player, renderer):
            captured["player"] = player
            captured["renderer"] = renderer

        def stop(self):
            captured["stopped"] = True

    class FakeRenderer:
        def __init__(self, console):
            captured["renderer_console"] = console

    class FakeHumanPlayer:
        def __init__(self, *args, **kwargs):
            captured["human_args"] = args
            captured["human_kwargs"] = kwargs

    console = Mock()
    console.input.side_effect = ["1", "Alice", "7777", "s"]
    monkeypatch.setattr(lan_ui, "LANServer", FakeServer)
    monkeypatch.setattr(lan_ui, "Renderer", FakeRenderer)
    monkeypatch.setattr(lan_ui, "HumanPlayer", FakeHumanPlayer)
    monkeypatch.setattr(lan_ui, "get_local_ip", lambda: "127.0.0.1")

    time_control = TimeControl("tc.unlimited", None, 0)
    lan_ui.host_lan_game(console, time_control, AIDelay("instant", 0.0))

    assert captured["human_args"] == ("Alice",)
    assert captured["human_kwargs"] == {
        "console": console,
        "renderer": captured["renderer"],
        "time_control": time_control,
    }


def test_server_saves_logger_when_round_loop_ends(monkeypatch):
    from mahjong.network import server as server_module
    from mahjong.player.greedy_ai import GreedyAI

    saved = {}

    class FakeLogger:
        def __init__(self, player_names, config):
            saved["player_names"] = player_names
            saved["config"] = config

        def subscribe_events(self, _event_bus):
            pass

        def save(self, final_scores):
            saved["final_scores"] = final_scores

    monkeypatch.setattr(server_module, "GameLogger", FakeLogger)
    monkeypatch.setattr(server_module, "run_round", lambda *_args: None)

    server = LANServer(
        host="127.0.0.1",
        port=0,
        is_tonpuu=True,
        ai_delay=AIDelay("instant", 0.0),
    )
    server.run_game(GreedyAI("Host"))

    assert saved["final_scores"] == {
        name: score for name, score in zip(
            saved["player_names"], [25000, 25000, 25000, 25000]
        )
    }


def test_server_autofill_names_avoid_host_collision(monkeypatch):
    from mahjong.network import server as server_module
    from mahjong.player.greedy_ai import GreedyAI

    captured = {}

    class FakeLogger:
        def __init__(self, player_names, _config):
            captured["names"] = player_names

        def subscribe_events(self, _event_bus):
            pass

        def save(self, _final_scores):
            pass

    monkeypatch.setattr(server_module, "GameLogger", FakeLogger)
    monkeypatch.setattr(server_module, "run_round", lambda *_args: None)

    server = LANServer(
        host="127.0.0.1",
        port=0,
        is_tonpuu=True,
        host_player_name="电脑A (AI)",
        ai_delay=AIDelay("instant", 0.0),
    )
    server.run_game(GreedyAI("电脑A (AI)"))

    assert len(captured["names"]) == 4
    assert len({name.casefold() for name in captured["names"]}) == 4
