"""Rich rendering facade for the terminal UI.

The game loop (cli.py) drives rendering directly via this class.
Subscribes to events for audio playback and instant notices.
"""

from typing import Optional
from copy import copy
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from mahjong.player.base import GameView
from mahjong.engine.action import AvailableActions
from mahjong.engine.event import EventBus, EventType, GameEvent
from mahjong.ui.board_layout import (
    render_board, render_action_prompt, render_current_yaku_hint,
    render_discard_review,
)
from mahjong.ui.i18n import t
from mahjong.ui.sound import play_tile_sound, play_action_sound
from mahjong.ui.tile_display import tile_to_display_str


class Renderer:
    """Rendering facade; handles board state rendering and audio feedback."""

    def __init__(self, console: Console, event_bus: Optional[EventBus] = None,
                 human_seat: int = 0):
        self.console = console
        self.event_bus = event_bus
        self.human_seat = human_seat
        self._hand_history = []
        self._last_discard_review = None
        self.helper_panels_visible = False
        self.spectator_mode = False
        if self.event_bus:
            self.event_bus.subscribe(EventType.ROUND_START, self._on_round_start)
            self.event_bus.subscribe(EventType.RIICHI_DECLARE, self._on_riichi)
            self.event_bus.subscribe(EventType.DISCARD, self._on_discard)
            self.event_bus.subscribe(EventType.CHI, self._on_chi)
            self.event_bus.subscribe(EventType.PON, self._on_pon)
            self.event_bus.subscribe(EventType.KAN, self._on_kan)
            self.event_bus.subscribe(EventType.TSUMO, self._on_tsumo)
            self.event_bus.subscribe(EventType.RON, self._on_ron)
            self.event_bus.subscribe(EventType.EXHAUSTIVE_DRAW, self._on_draw)
            self.event_bus.subscribe(EventType.ABORTIVE_DRAW, self._on_draw)

    def _on_round_start(self, event: GameEvent):
        """Start a fresh visible history for each hand/round."""
        self._hand_history.clear()
        self._last_discard_review = None
        self.helper_panels_visible = self.spectator_mode

    def render_game_view(self, game_view: GameView):
        """Render the current board state from the human player's perspective."""
        self._last_game_view = game_view
        render_board(self.console, game_view)
        if self._hand_history:
            history = Text()
            for entry in self._hand_history[-8:]:
                history.append(f"{entry}\n")
            self.console.print(Panel(
                history, title=f"[bold]{t('label.hand_history')}[/bold]",
                border_style="dim", padding=(0, 1),
            ))
        # Keep the latest review visible across board redraws. This prevents
        # the AI's next action (which clears the console) from erasing it.
        review = self._last_discard_review
        if self.helper_panels_visible and review is not None:
            review_view, review_available, review_action = review
            render_discard_review(self.console, review_view,
                                  review_available, review_action)

    def render_actions(self, available: AvailableActions):
        """Show available actions to the player."""
        if hasattr(self, "_last_game_view"):
            render_current_yaku_hint(self.console, self._last_game_view, available)
        render_action_prompt(self.console, available)

    def render_discard_review(self, game_view, available, action):
        """Show efficiency feedback for the player's selected discard."""
        # GameView references the live Hand object. The engine mutates that
        # hand as soon as this method returns, so retain a pre-discard clone
        # for stable review output on later board redraws.
        review_view = copy(game_view)
        review_view.my_hand = game_view.my_hand.clone()
        if self.helper_panels_visible:
            render_discard_review(self.console, review_view, available, action)
            self._last_discard_review = (review_view, available, action)

    def toggle_helper_panels(self) -> bool:
        """Toggle trainer panels and discard any cached visible review."""
        self.helper_panels_visible = not self.helper_panels_visible
        self._last_discard_review = None
        return self.helper_panels_visible

    def _on_riichi(self, event: GameEvent):
        player_idx = event.data.get("player", -1)
        if player_idx != self.human_seat:
            self.console.print(
                f"  [bold yellow]{t('msg.riichi_declare', player=t('label.player_n', n=player_idx))}[/bold yellow]"
            )

    def _on_discard(self, event: GameEvent):
        tile = event.data.get("tile")
        # Tile index 0 is a valid tile (1-man), so test for ``None`` rather
        # than truthiness when recording the event.
        if tile is not None:
            play_tile_sound(tile)
            player = event.data.get("player", -1)
            name = t('label.you') if player == self.human_seat else t(
                'label.player_n', n=player)
            tsumogiri = " " + t('label.tsumogiri') if event.data.get('is_tsumogiri') else ""
            self._hand_history.append(
                f"{name}: {t('label.discarded')} {tile_to_display_str(tile)}{tsumogiri}"
            )

    def _on_chi(self, event: GameEvent):
        play_action_sound("chi")

    def _on_pon(self, event: GameEvent):
        play_action_sound("peng")

    def _on_kan(self, event: GameEvent):
        play_action_sound("gang")

    def _on_tsumo(self, event: GameEvent):
        play_action_sound("zimo")

    def _on_ron(self, event: GameEvent):
        play_action_sound("hu")

    def _on_draw(self, event: GameEvent):
        play_action_sound("liuju")

    def pause(self, message: str = None):
        """Pause and wait for user input."""
        if message is None:
            message = t('prompt.press_enter')
        self.console.input(f"\n  {message}")
