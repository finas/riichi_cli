"""LAN Multiplayer Server."""

import socket
import threading
import time
from typing import Callable, List, Optional, Tuple

from mahjong.core.player_state import Wind, player_name_key, validate_player_names
from mahjong.engine.action import Action, ActionType, AvailableActions
from mahjong.engine.ai_delay import AIDelay
from mahjong.engine.event import EventBus, EventType, GameEvent
from mahjong.engine.game import GameConfig, GameState
from mahjong.engine.game_logger import GameLogger
from mahjong.engine.round import run_round, RoundResult
from mahjong.engine.time_control import TimeControl
from mahjong.network.protocol import MessageType, NetworkMessage, send_message, recv_message
from mahjong.network.remote_player import RemotePlayer
from mahjong.network.serialization import (
    game_view_to_dict, score_result_to_dict, tiles_to_ids
)
from mahjong.player.base import Player, GameView, build_game_view
from mahjong.player.greedy_ai import GreedyAI
from mahjong.player.human import HumanPlayer
from mahjong.ui.i18n import t
from mahjong.ui.labels import round_label


HANDSHAKE_TIMEOUT = 5.0
SEND_TIMEOUT = 1.0


def get_local_ip() -> str:
    """Detect the local LAN IP address of this machine."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


class LANServer:
    """Hosting server for LAN multiplayer games."""

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 7777,
        num_players: int = 4,
        is_sanma: bool = False,
        is_tonpuu: bool = False,
        host_player_name: str = "房主 (Host)",
        time_control: Optional[TimeControl] = None,
        ai_delay: Optional[AIDelay] = None,
    ):
        self.host = host
        self.port = port
        self.max_players = 3 if is_sanma else 4
        self.is_sanma = is_sanma
        self.is_tonpuu = is_tonpuu
        validate_player_names([host_player_name])
        self.host_player_name = host_player_name.strip()
        self.time_control = time_control or TimeControl("tc.unlimited", base_seconds=None, bank_seconds=0)
        self.ai_delay = ai_delay or AIDelay("ai_delay.1s", 1.0)

        self.server_sock: Optional[socket.socket] = None
        self.is_running = False
        self.in_lobby = True

        # Connected clients: list of (name, socket)
        self.clients: List[Tuple[str, socket.socket]] = []
        self.remote_players: List[RemotePlayer] = []
        self.listener_thread: Optional[threading.Thread] = None

        self.on_lobby_change: Optional[Callable[[List[str]], None]] = None

    def start_lobby(self):
        """Start listening for client connections in background thread."""
        self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_sock.bind((self.host, self.port))
        self.server_sock.listen(5)
        self.is_running = True
        self.in_lobby = True

        self.listener_thread = threading.Thread(target=self._listen_loop, daemon=True)
        self.listener_thread.start()

    def _listen_loop(self):
        while self.is_running and self.in_lobby:
            try:
                self.server_sock.settimeout(1.0)
                client_sock, addr = self.server_sock.accept()
            except socket.timeout:
                continue
            except Exception:
                break

            # Handle handshake
            try:
                client_sock.settimeout(HANDSHAKE_TIMEOUT)
                msg = recv_message(client_sock)
                if msg and msg.msg_type == MessageType.HELLO:
                    raw_name = msg.payload.get(
                        "name", f"Player {len(self.clients) + 2}")
                    try:
                        validate_player_names([raw_name])
                        player_name = raw_name.strip()
                    except (TypeError, ValueError):
                        send_message(
                            client_sock,
                            NetworkMessage(
                                MessageType.ERROR,
                                {"message": "Invalid player name"},
                            ),
                            timeout=SEND_TIMEOUT,
                        )
                        client_sock.close()
                        continue

                    existing_keys = {
                        player_name_key(name)
                        for name in self.get_player_names_in_lobby()
                    }
                    if player_name_key(player_name) in existing_keys:
                        send_message(
                            client_sock,
                            NetworkMessage(
                                MessageType.ERROR,
                                {"message": "Player name already in use"},
                            ),
                            timeout=SEND_TIMEOUT,
                        )
                        client_sock.close()
                        continue

                    if len(self.clients) + 1 >= self.max_players:
                        send_message(
                            client_sock,
                            NetworkMessage(MessageType.ERROR, {"message": "Room is full"}),
                            timeout=SEND_TIMEOUT,
                        )
                        client_sock.close()
                        continue

                    seat = len(self.clients) + 1
                    self.clients.append((player_name, client_sock))

                    # Send ACK
                    send_message(
                        client_sock,
                        NetworkMessage(MessageType.HELLO_ACK, {
                            "seat": seat,
                            "is_host": False,
                            "max_players": self.max_players,
                            "is_sanma": self.is_sanma,
                            "is_tonpuu": self.is_tonpuu,
                        }),
                        timeout=SEND_TIMEOUT,
                    )

                    self._broadcast_lobby_state()
                    if self.on_lobby_change:
                        self.on_lobby_change(self.get_player_names_in_lobby())
                else:
                    client_sock.close()
            except Exception:
                try:
                    client_sock.close()
                except Exception:
                    pass

    def get_player_names_in_lobby(self) -> List[str]:
        names = [self.host_player_name]
        for name, _ in self.clients:
            names.append(name)
        return names

    def _broadcast_lobby_state(self):
        names = self.get_player_names_in_lobby()
        msg = NetworkMessage(MessageType.LOBBY_UPDATE, {
            "players": names,
            "max_players": self.max_players,
            "is_sanma": self.is_sanma,
            "is_tonpuu": self.is_tonpuu,
        })
        for _, sock in self.clients:
            if not send_message(sock, msg, timeout=SEND_TIMEOUT):
                try:
                    sock.close()
                except OSError:
                    pass

    def run_game(self, host_player: Player, host_renderer=None):
        """Transition from lobby to active game."""
        self.in_lobby = False

        # Build players list
        players: List[Player] = [host_player]
        player_names: List[str] = [self.host_player_name]

        # Add connected remote human players
        for i, (name, sock) in enumerate(self.clients):
            seat = i + 1
            remote = RemotePlayer(name, sock, seat=seat, timeout=self.time_control.base_seconds or 30.0)
            self.remote_players.append(remote)
            players.append(remote)
            player_names.append(name)

        # Auto-fill remaining empty seats with AI bots
        ai_names = ["电脑A (AI)", "电脑B (AI)", "电脑C (AI)"]
        used_name_keys = {player_name_key(name) for name in player_names}
        while len(players) < self.max_players:
            ai_idx = len(players) - 1
            ai_name = ai_names[ai_idx % len(ai_names)]
            suffix = 2
            while player_name_key(ai_name) in used_name_keys:
                ai_name = f"{ai_names[ai_idx % len(ai_names)]} {suffix}"
                suffix += 1
            players.append(GreedyAI(ai_name))
            player_names.append(ai_name)
            used_name_keys.add(player_name_key(ai_name))

        config = GameConfig(
            num_players=self.max_players,
            is_sanma=self.is_sanma,
            is_tonpuu=self.is_tonpuu,
            starting_score=35000 if self.is_sanma else 25000,
        )

        event_bus = EventBus()

        # Broadcast game start to all remote clients
        game_start_payload = {
            "config": {
                "num_players": config.num_players,
                "is_sanma": config.is_sanma,
                "is_tonpuu": config.is_tonpuu,
                "starting_score": config.starting_score,
            },
            "players": player_names,
        }
        for remote in self.remote_players:
            remote._send(NetworkMessage(MessageType.GAME_START, game_start_payload))

        game = GameState(config, player_names, event_bus)
        logger = GameLogger(player_names, {
            "num_players": config.num_players,
            "is_sanma": config.is_sanma,
            "is_tonpuu": config.is_tonpuu,
            "starting_score": config.starting_score,
        })
        logger.subscribe_events(event_bus)

        # Forward all events to remote players
        def on_event_forward(event: GameEvent):
            for remote in self.remote_players:
                remote.notify_event(event.event_type.value, {})

        for et in EventType:
            event_bus.subscribe(et, on_event_forward)

        try:
            while not game.is_finished:
                round_state = game.setup_round()
                label = round_label(game.round_wind, game.round_number, game.honba)

                def get_action(player_idx: int, available: AvailableActions) -> Action:
                    player = players[player_idx]

                    # Update board state on host UI and all remote clients
                    for seat_i, p in enumerate(players):
                        gv = build_game_view(
                            seat_i, game.players,
                            game.round_wind, game.honba, game.riichi_sticks,
                            round_state.wall.remaining,
                            round_state.wall.dora_indicators,
                            label,
                            round_state.last_discard,
                            round_state.last_discard_player,
                        )
                        if seat_i == 0 and host_renderer:
                            host_renderer.render_game_view(gv)
                        elif isinstance(p, RemotePlayer):
                            p.notify_game_view(gv)

                    if isinstance(player, GreedyAI):
                        time.sleep(self.ai_delay.get_delay())

                    target_gv = build_game_view(
                        player_idx, game.players,
                        game.round_wind, game.honba, game.riichi_sticks,
                        round_state.wall.remaining,
                        round_state.wall.dora_indicators,
                        label,
                        round_state.last_discard,
                        round_state.last_discard_player,
                    )
                    return player.choose_action(target_gv, available)

                result = run_round(round_state, get_action)
                if result is None:
                    break

                logger.end_round(result)

                # Broadcast round result to remote players
                res_payload = {
                    "winners": result.winners,
                    "loser": result.loser,
                    "score_changes": result.score_changes,
                    "is_draw": result.is_draw,
                    "draw_type": result.draw_type,
                    "tenpai_players": result.tenpai_players,
                    "score_results": [
                        (w_seat, score_result_to_dict(s_res))
                        for w_seat, s_res in result.score_results
                    ],
                    "player_names": player_names,
                    "players_hands": [
                        {
                            "closed_tiles": tiles_to_ids(p.hand.closed_tiles),
                            "draw_tile": p.hand.draw_tile.id if p.hand.draw_tile else None,
                            "seat_wind": p.seat_wind.value,
                        }
                        for p in game.players
                    ],
                }
                for remote in self.remote_players:
                    remote.notify_round_result(res_payload)

                game.advance_round(result)
        finally:
            final_scores = {p.name: p.score for p in game.players}
            logger.save(final_scores)
            for remote in self.remote_players:
                remote.notify_game_end(final_scores)
            self.stop()

    def stop(self):
        """Close server and disconnect all sockets."""
        self.is_running = False
        self.in_lobby = False
        for _, sock in self.clients:
            try:
                sock.close()
            except Exception:
                pass
        self.clients.clear()
        for remote in self.remote_players:
            remote.close()
        self.remote_players.clear()
        if self.server_sock:
            try:
                self.server_sock.close()
            except Exception:
                pass
