"""Network protocol framing and message definitions."""

import json
import socket
import struct
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


class MessageType(str, Enum):
    # Handshake & Lobby
    HELLO = "hello"                  # Client -> Server: {"name": str, "version": str}
    HELLO_ACK = "hello_ack"          # Server -> Client: {"seat": int, "player_id": str, "is_host": bool}
    LOBBY_UPDATE = "lobby_update"    # Server -> Client: {"players": list, "max_players": int, "is_sanma": bool}
    START_GAME = "start_game"        # Host Client -> Server: {"config": dict}

    # Game Loop
    GAME_START = "game_start"        # Server -> Client: {"config": dict, "players": list}
    GAME_VIEW_UPDATE = "gv_update"   # Server -> Client: {"game_view": dict}
    ACTION_REQUEST = "action_req"    # Server -> Client: {"game_view": dict, "available": dict, "deadline": float}
    ACTION_RESPONSE = "action_resp"  # Client -> Server: {"action": dict}
    EVENT = "event"                  # Server -> Client: {"event_type": str, "data": dict}
    ROUND_RESULT = "round_result"    # Server -> Client: {"result": dict, "players": list, "player_names": list}
    GAME_END = "game_end"            # Server -> Client: {"final_scores": dict}

    # Control
    PING = "ping"
    PONG = "pong"
    ERROR = "error"                  # Server -> Client: {"message": str}
    DISCONNECT = "disconnect"        # Client/Server -> other


@dataclass
class NetworkMessage:
    msg_type: MessageType
    payload: Dict[str, Any] = field(default_factory=dict)

    def to_bytes(self) -> bytes:
        """Encode message to 4-byte length-prefixed UTF-8 JSON bytes."""
        raw_json = json.dumps({
            "type": self.msg_type.value,
            "payload": self.payload,
        }, ensure_ascii=False).encode("utf-8")
        header = struct.pack(">I", len(raw_json))
        return header + raw_json

    @classmethod
    def from_bytes(cls, data: bytes) -> "NetworkMessage":
        obj = json.loads(data.decode("utf-8"))
        return cls(
            msg_type=MessageType(obj["type"]),
            payload=obj.get("payload", {}),
        )


def _recv_exact(sock: socket.socket, num_bytes: int) -> Optional[bytes]:
    """Read exactly num_bytes from socket, or None if connection closed."""
    buf = bytearray()
    while len(buf) < num_bytes:
        try:
            chunk = sock.recv(num_bytes - len(buf))
            if not chunk:
                return None
            buf.extend(chunk)
        # Let timeout/non-blocking errors propagate to the caller.  Retrying
        # unconditionally here defeats socket deadlines and can hang a game
        # forever when a client stops responding.
        except (socket.timeout, BlockingIOError):
            raise
        except (ConnectionResetError, BrokenPipeError, OSError):
            return None
    return bytes(buf)


def send_message(
    sock: socket.socket,
    msg: NetworkMessage,
    timeout: Optional[float] = None,
) -> bool:
    """Send a framed NetworkMessage over socket. Returns True on success.

    A timeout is applied only for this operation and the socket's previous
    timeout is restored afterwards.  This is important for the LAN server:
    a client that stops reading must not be able to block the game loop.
    """
    original_timeout = sock.gettimeout()
    try:
        if timeout is not None:
            sock.settimeout(timeout)
        data = msg.to_bytes()
        sock.sendall(data)
        return True
    except (socket.timeout, ConnectionResetError, BrokenPipeError, OSError):
        return False
    finally:
        if timeout is not None:
            try:
                sock.settimeout(original_timeout)
            except OSError:
                pass


def recv_message(sock: socket.socket) -> Optional[NetworkMessage]:
    """Read one framed NetworkMessage from socket. Returns None on disconnect."""
    header = _recv_exact(sock, 4)
    if not header:
        return None
    length = struct.unpack(">I", header)[0]
    # Sanity guard against giant or corrupt packets (> 10MB)
    if length > 10 * 1024 * 1024:
        return None
    payload_bytes = _recv_exact(sock, length)
    if not payload_bytes:
        return None
    try:
        return NetworkMessage.from_bytes(payload_bytes)
    except Exception:
        return None
