"""Remote player proxy for network clients."""

import socket
import time
from typing import Optional

from mahjong.player.base import Player, GameView
from mahjong.engine.action import Action, ActionType, AvailableActions
from mahjong.network.protocol import MessageType, NetworkMessage, send_message, recv_message
from mahjong.network.serialization import (
    game_view_to_dict, available_actions_to_dict, dict_to_action, action_to_dict
)


class RemotePlayer(Player):
    """Player proxy that delegates decision-making to a remote client over TCP."""

    def __init__(self, name: str, sock: socket.socket, seat: int, timeout: float = 30.0):
        super().__init__(name)
        self.sock = sock
        self.seat = seat
        self.timeout = timeout
        self.is_connected = True

    def _send(self, message: NetworkMessage, timeout: float = 1.0) -> bool:
        """Send a message without allowing a slow client to stall the game."""
        if not self.is_connected:
            return False
        if send_message(self.sock, message, timeout=timeout):
            return True
        self.close()
        return False

    def choose_action(self, game_view: GameView, available: AvailableActions) -> Action:
        """Request action from remote client and await response."""
        if not self.is_connected:
            return self._default_fallback(available)

        payload = {
            "game_view": game_view_to_dict(game_view),
            "available": available_actions_to_dict(available),
            "deadline": time.time() + self.timeout,
        }

        msg = NetworkMessage(MessageType.ACTION_REQUEST, payload)
        if not self._send(msg):
            return self._default_fallback(available)

        # Set socket timeout slightly larger than player timeout to allow network latency
        orig_timeout = self.sock.gettimeout()
        self.sock.settimeout(self.timeout + 2.0)
        try:
            resp = recv_message(self.sock)
            if resp is None or resp.msg_type != MessageType.ACTION_RESPONSE:
                self.is_connected = False
                return self._default_fallback(available)
            action_data = resp.payload.get("action")
            if not action_data:
                return self._default_fallback(available)
            return dict_to_action(action_data)
        except (socket.timeout, ConnectionError, OSError):
            self.is_connected = False
            return self._default_fallback(available)
        except Exception:
            # Malformed responses are treated as a client fault, but do not
            # bring down the host game.
            self.is_connected = False
            return self._default_fallback(available)
        finally:
            try:
                self.sock.settimeout(orig_timeout)
            except Exception:
                pass

    def notify_game_view(self, game_view: GameView):
        """Send passive board state update to client."""
        if not self.is_connected:
            return
        payload = {"game_view": game_view_to_dict(game_view)}
        self._send(NetworkMessage(MessageType.GAME_VIEW_UPDATE, payload))

    def notify_event(self, event_type: str, event_data: dict):
        """Send game event notification to client."""
        if not self.is_connected:
            return
        payload = {"event_type": event_type, "data": event_data}
        self._send(NetworkMessage(MessageType.EVENT, payload))

    def notify_round_result(self, result_payload: dict):
        """Send round result summary to client."""
        if not self.is_connected:
            return
        self._send(NetworkMessage(MessageType.ROUND_RESULT, result_payload))

    def notify_game_end(self, final_scores: dict):
        """Send game end summary to client."""
        if not self.is_connected:
            return
        self._send(NetworkMessage(MessageType.GAME_END, {"final_scores": final_scores}))

    def close(self):
        """Mark the client disconnected and close its socket."""
        self.is_connected = False
        try:
            self.sock.close()
        except OSError:
            pass

    def _default_fallback(self, available: AvailableActions) -> Action:
        """Safest default action on client disconnect or timeout."""
        if available.can_discard:
            return Action(ActionType.DISCARD, self.seat, tile=available.can_discard[-1])
        return Action(ActionType.SKIP, self.seat)
