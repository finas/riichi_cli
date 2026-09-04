"""LAN Multiplayer Client."""

import socket
import time
from typing import Optional

from rich.console import Console
from rich.panel import Panel

from mahjong.core.player_state import Wind
from mahjong.core.tile import Tile, ALL_TILES_136
from mahjong.engine.action import Action
from mahjong.network.protocol import MessageType, NetworkMessage, send_message, recv_message
from mahjong.network.serialization import (
    dict_to_game_view, dict_to_available_actions, action_to_dict,
    dict_to_score_result, ids_to_tiles
)
from mahjong.ui.board_layout import (
    render_win_announcement, render_yaku_summary, render_draw_screen,
    render_round_end_hands, render_game_end
)
from mahjong.ui.i18n import t
from mahjong.ui.input_handler import get_player_input
from mahjong.ui.renderer import Renderer
from mahjong.ui.sound import play_action_sound, play_tile_sound


class LANClient:
    """Client for connecting to a LAN multiplayer game."""

    def __init__(self, console: Console):
        self.console = console
        self.sock: Optional[socket.socket] = None
        self.player_name: str = "Player"
        self.seat: int = 1
        self.is_connected = False
        self.last_error: Optional[str] = None
        self.renderer = Renderer(console)

    def connect(self, host: str, port: int, player_name: str) -> bool:
        """Connect to LAN host server."""
        self.player_name = player_name
        self.last_error = None
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(5.0)
        try:
            self.sock.connect((host, port))
            self.sock.settimeout(None)

            # Send HELLO
            hello_msg = NetworkMessage(MessageType.HELLO, {"name": player_name, "version": "1.0"})
            if not send_message(self.sock, hello_msg):
                return False

            ack = recv_message(self.sock)
            if ack and ack.msg_type == MessageType.HELLO_ACK:
                self.seat = ack.payload.get("seat", 1)
                self.is_connected = True
                return True
            if ack and ack.msg_type == MessageType.ERROR:
                self.last_error = ack.payload.get("message", "Connection rejected")
            else:
                self.last_error = "Invalid handshake response"
            return False
        except Exception as exc:
            self.last_error = str(exc) or "Connection failed"
            self.is_connected = False
            return False
        finally:
            if not self.is_connected and self.sock is not None:
                try:
                    self.sock.close()
                except OSError:
                    pass
                self.sock = None

    def run_lobby(self) -> bool:
        """Wait in lobby until game starts or disconnect occurs."""
        self.console.print(f"\n  [bold green]{t('lan.connected_waiting')}[/bold green]\n")
        while self.is_connected:
            msg = recv_message(self.sock)
            if not msg:
                self.console.print(f"  [red]{t('lan.disconnected')}[/red]")
                self.is_connected = False
                return False

            if msg.msg_type == MessageType.LOBBY_UPDATE:
                players = msg.payload.get("players", [])
                max_p = msg.payload.get("max_players", 4)
                self.console.clear()
                self.console.print(Panel(
                    f"[bold cyan]{t('lan.lobby_title')}[/bold cyan]\n\n"
                    + "\n".join([f"    {i+1}. {p}" for i, p in enumerate(players)])
                    + f"\n\n[dim]{t('lan.waiting_host', count=len(players), max=max_p)}[/dim]",
                    border_style="cyan"
                ))
            elif msg.msg_type == MessageType.GAME_START:
                return True
            elif msg.msg_type == MessageType.ERROR:
                self.console.print(f"  [red]{msg.payload.get('message', 'Error')}[/red]")
                return False
        return False

    def run_game_loop(self):
        """Main game event loop for connected client."""
        while self.is_connected:
            msg = recv_message(self.sock)
            if not msg:
                self.console.print(f"\n  [red]{t('lan.disconnected')}[/red]")
                break

            if msg.msg_type == MessageType.GAME_VIEW_UPDATE:
                gv_data = msg.payload.get("game_view")
                if gv_data:
                    gv = dict_to_game_view(gv_data)
                    self.renderer.render_game_view(gv)
                    if gv.last_discard:
                        play_tile_sound(gv.last_discard)

            elif msg.msg_type == MessageType.EVENT:
                ev_type = msg.payload.get("event_type", "")
                play_action_sound(ev_type)

            elif msg.msg_type == MessageType.ACTION_REQUEST:
                gv_data = msg.payload.get("game_view")
                av_data = msg.payload.get("available")
                deadline = msg.payload.get("deadline")

                gv = dict_to_game_view(gv_data)
                available = dict_to_available_actions(av_data)

                # Render current visible board
                self.renderer.render_game_view(gv)

                # Prompt user for action
                action = get_player_input(self.console, gv, available, deadline=deadline)

                # Send action back to server
                resp = NetworkMessage(MessageType.ACTION_RESPONSE, {"action": action_to_dict(action)})
                send_message(self.sock, resp)

            elif msg.msg_type == MessageType.ROUND_RESULT:
                res_data = msg.payload
                is_draw = res_data.get("is_draw", False)
                player_names = res_data.get("player_names", [])

                if is_draw:
                    draw_type = res_data.get("draw_type", "exhaustive")
                    tenpai_seats = res_data.get("tenpai_players", [])
                    tenpai_info = [(player_names[i], i in tenpai_seats) for i in range(len(player_names))]
                    render_draw_screen(self.console, draw_type, tenpai_info)
                else:
                    score_results = res_data.get("score_results", [])
                    loser_seat = res_data.get("loser")
                    loser_name = player_names[loser_seat] if loser_seat is not None else ""
                    for winner_seat, sr_dict in score_results:
                        winner_name = player_names[winner_seat]
                        sr = dict_to_score_result(sr_dict)
                        render_win_announcement(self.console, winner_name, sr.is_tsumo, loser_name)
                        render_yaku_summary(self.console, sr)

                time.sleep(1.0)

            elif msg.msg_type == MessageType.GAME_END:
                final_scores = msg.payload.get("final_scores", {})
                render_game_end(self.console, [(name, score) for name, score in final_scores.items()])
                break

        self.close()

    def close(self):
        """Close connection."""
        self.is_connected = False
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
