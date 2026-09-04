"""Human player - interfaces with terminal UI for input."""

import time
from typing import Any, Optional

from rich.console import Console

from mahjong.engine.action import Action, ActionType, AvailableActions
from mahjong.engine.time_control import TimeControl, TIME_CONTROL_PRESETS
from mahjong.player.base import Player, GameView
from mahjong.ui.renderer import Renderer
from mahjong.ui.input_handler import get_player_input
from mahjong.analysis.coach import AICoach


class HumanPlayer(Player):
    """Human player that uses terminal UI for interaction."""

    def __init__(self, name: str, console: Console, renderer: Renderer,
                 time_control: Optional[TimeControl] = None,
                 coach: Optional[Any] = None,
                 coach_name: str = "Mortal"):
        super().__init__(name)
        self.console = console
        self.renderer = renderer
        self.time_control: TimeControl = time_control or TIME_CONTROL_PRESETS[0]
        self.bank_remaining: float = float(self.time_control.bank_seconds)
        self.coach = coach
        self.coach_name = coach_name
        self._ai_coach = AICoach() if coach is not None else None

    def choose_action(self, game_view: GameView,
                      available: AvailableActions) -> Action:
        """Get action from human via UI."""
        deadline, base_end = self._compute_deadline()

        self.renderer.render_game_view(game_view)
        self.renderer.render_actions(available)

        suggestion = None
        if self._ai_coach is not None:
            suggestion = self._ai_coach.get_coach_suggestion(
                game_view, available,
                external_ai=self.coach,
                engine_name=self.coach_name,
            )

        t_start = time.monotonic()
        action = get_player_input(self.console, game_view, available,
                                  deadline, base_end,
                                  on_toggle_helper=self.renderer.toggle_helper_panels,
                                  coach_suggestion=suggestion)
        if getattr(self.coach, "has_pending_reach", False):
            if action.action_type == ActionType.RIICHI:
                self.coach.accept_pending_reach()
            else:
                self.coach.cancel_pending_reach()
        self.renderer.render_discard_review(game_view, available, action)
        self._update_bank(time.monotonic() - t_start)
        return action
    def close(self) -> None:
        if self.coach is not None and hasattr(self.coach, "close"):
            self.coach.close()


    # ------------------------------------------------------------------
    # Time control helpers
    # ------------------------------------------------------------------

    def _compute_deadline(self):
        """Return (deadline, base_end) for the current action."""
        tc = self.time_control
        if tc.is_unlimited:
            return None, None

        now = time.monotonic()
        base_end = now + tc.base_seconds
        deadline = now + tc.base_seconds + max(0.0, self.bank_remaining)
        return deadline, base_end

    def _update_bank(self, elapsed: float) -> None:
        """Consume bank time if the action took longer than base_seconds."""
        tc = self.time_control
        if tc.is_unlimited:
            return
        bank_used = max(0.0, elapsed - tc.base_seconds)
        self.bank_remaining = max(0.0, self.bank_remaining - bank_used)
