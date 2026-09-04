"""Headless match sessions shared by terminal and future native frontends."""

from dataclasses import dataclass
from typing import Callable, List, Optional, TextIO

from mahjong.engine.action import Action, ActionType, AvailableActions
from mahjong.engine.event import EventBus, EventType, GameEvent
from mahjong.engine.game import GameConfig, GameState
from mahjong.engine.game_logger import GameLogger
from mahjong.engine.round import RoundResult, RoundState, run_round
from mahjong.engine.time_control import TimeControl
from mahjong.player.base import GameView, Player, build_game_view
from mahjong.player.greedy_ai import GreedyAI

from .protocol import (
    ProtocolError,
    action_from_response,
    action_request_message,
    decode_message,
    encode_message,
    event_message,
    game_config_to_dict,
    make_message,
    round_result_to_dict,
)


@dataclass(frozen=True)
class ActionRequest:
    """A frontend decision request containing only visible state."""

    request_id: int
    game_view: GameView
    available: AvailableActions


@dataclass
class SessionResult:
    """Result returned after a headless match finishes."""

    game: GameState
    round_results: List[RoundResult]
    log_path: Optional[str] = None


ActionProvider = Callable[[ActionRequest], Action]
EventSink = Callable[[GameEvent, Optional[GameView]], None]
RoundSink = Callable[[RoundResult, GameState], None]


class GameSession:
    """Run a complete match without importing a frontend renderer.

    ``human_seat`` identifies the seat controlled by ``action_provider``.
    All other seats use the supplied AI factory.  The existing terminal CLI
    can continue using its current orchestration while a Rust client can use
    this session through :class:`JsonLinesGameService`.
    """

    def __init__(
        self,
        config: GameConfig,
        player_names: List[str],
        human_seat: Optional[int] = 0,
        action_provider: Optional[ActionProvider] = None,
        ai_factory: Optional[Callable[[str], Player]] = None,
        event_bus: Optional[EventBus] = None,
        event_sink: Optional[EventSink] = None,
        round_sink: Optional[RoundSink] = None,
        logger: Optional[GameLogger] = None,
    ):
        if human_seat is not None and not 0 <= human_seat < config.num_players:
            raise ValueError("human_seat must be a valid seat or None")
        if human_seat is not None and action_provider is None:
            raise ValueError("action_provider is required for a human seat")
        if len(player_names) < config.num_players:
            raise ValueError("player_names must include every seat")

        self.config = config
        self.player_names = list(player_names)
        self.human_seat = human_seat
        self.action_provider = action_provider
        self.ai_factory = ai_factory or (lambda name: GreedyAI(name))
        self.event_bus = event_bus or EventBus()
        self.event_sink = event_sink
        self.round_sink = round_sink
        self.logger = logger

        self.game: Optional[GameState] = None
        self.current_round: Optional[RoundState] = None
        self._request_id = 0

    def _build_view(self, player_idx: int, round_state: RoundState) -> GameView:
        round_number = getattr(self.game, "round_number", 0) + 1
        round_label = f"{round_state.round_wind.name}{round_number} {round_state.honba}"
        return build_game_view(
            player_idx,
            round_state.players,
            round_state.round_wind,
            round_state.honba,
            round_state.riichi_sticks,
            round_state.wall.remaining,
            round_state.wall.dora_indicators,
            round_label,
            round_state.last_discard,
            round_state.last_discard_player,
            active_player=player_idx,
        )

    def _on_event(self, event: GameEvent) -> None:
        view = None
        if self.current_round is not None and self.human_seat is not None:
            view = self._build_view(self.human_seat, self.current_round)
        if self.event_sink is not None:
            self.event_sink(event, view)

    def _choose_action(self, players: List[Player], player_idx: int,
                       available: AvailableActions) -> Action:
        if self.current_round is None:
            raise RuntimeError("action requested outside an active round")

        view = self._build_view(player_idx, self.current_round)
        if player_idx == self.human_seat:
            self._request_id += 1
            request = ActionRequest(self._request_id, view, available)
            action = self.action_provider(request)
        else:
            action = players[player_idx].choose_action(view, available)

        # Keep the same safety boundary used by run_round for every frontend.
        if not isinstance(action, Action) or not available.contains(action):
            return Action(ActionType.SKIP, available.player)
        return action

    def run(self) -> SessionResult:
        """Run all rounds and return the final game state."""
        self.game = GameState(self.config, self.player_names, self.event_bus)
        players: List[Player] = []
        for seat, name in enumerate(self.player_names[:self.config.num_players]):
            if seat == self.human_seat:
                players.append(None)  # type: ignore[arg-type]
            else:
                players.append(self.ai_factory(name))

        if self.logger is not None:
            self.logger.subscribe_events(self.event_bus)
        self.event_bus.subscribe(EventType.GAME_START, self._on_event)
        self.event_bus.subscribe(EventType.GAME_END, self._on_event)
        for event_type in EventType:
            if event_type not in (EventType.GAME_START, EventType.GAME_END):
                self.event_bus.subscribe(event_type, self._on_event)

        self.event_bus.emit(GameEvent(EventType.GAME_START, {
            "config": self.config,
            "players": [(name, self.config.starting_score) for name in self.player_names],
        }))

        round_results: List[RoundResult] = []
        try:
            while not self.game.is_finished:
                self.current_round = self.game.setup_round()
                result = run_round(
                    self.current_round,
                    lambda player_idx, available: self._choose_action(
                        players, player_idx, available
                    ),
                )
                if result is None:
                    break

                self.event_bus.emit(GameEvent(EventType.ROUND_END, {
                    "result": result,
                }))
                if self.logger is not None:
                    self.logger.end_round(result)
                round_results.append(result)
                if self.round_sink is not None:
                    self.round_sink(result, self.game)

                self.current_round = None
                self.game.advance_round(result)
        finally:
            for player in players:
                if player is not None and hasattr(player, "close"):
                    player.close()

        log_path = None
        if self.logger is not None:
            final_scores = {p.name: p.score for p in self.game.players}
            log_path = self.logger.save(final_scores)

        return SessionResult(self.game, round_results, log_path)


class SessionCancelled(Exception):
    """Raised when a frontend asks a running session to stop."""


class JsonLinesGameService:
    """Expose one ``GameSession`` over a persistent JSON-lines connection.

    Startup protocol:

    1. The service emits ``hello``.
    2. The client sends ``start`` with optional configuration.
    3. The service emits events and ``action_request`` messages.
    4. The client answers with validated ``action_response`` messages.

    This intentionally uses blocking stdio: a Rust UI can own the event loop,
    while the Python engine remains a single persistent process.
    """

    def __init__(self, reader: TextIO, writer: TextIO):
        self.reader = reader
        self.writer = writer
        self._request_id = 0

    def _send(self, message) -> None:
        self.writer.write(encode_message(message) + "\n")
        self.writer.flush()

    def _read(self):
        line = self.reader.readline()
        if not line:
            raise SessionCancelled("frontend disconnected")
        return decode_message(line.strip())

    def _read_start(self):
        message = self._read()
        if message.get("type") == "quit":
            raise SessionCancelled("frontend requested shutdown")
        if message.get("type") != "start":
            raise ProtocolError("first client message must be start")
        return message.get("payload", {})

    @staticmethod
    def _make_config(payload) -> GameConfig:
        config_data = payload.get("config", payload)
        if not isinstance(config_data, dict):
            raise ValueError("config must be an object")
        is_sanma = bool(config_data.get("is_sanma", False))
        num_players = int(config_data.get("num_players", 3 if is_sanma else 4))
        if num_players not in (3, 4):
            raise ValueError("num_players must be 3 or 4")
        if is_sanma != (num_players == 3):
            raise ValueError("is_sanma and num_players disagree")
        time_control_data = config_data.get("time_control", {})
        time_control = None
        if time_control_data:
            if not isinstance(time_control_data, dict):
                raise ValueError("time_control must be an object")
            base_seconds = time_control_data.get("base_seconds")
            if base_seconds is not None:
                base_seconds = int(base_seconds)
            time_control = TimeControl(
                name=str(time_control_data.get("name", "tc.custom")),
                base_seconds=base_seconds,
                bank_seconds=int(time_control_data.get("bank_seconds", 0)),
            )
        return GameConfig(
            num_players=num_players,
            is_sanma=is_sanma,
            is_tonpuu=bool(config_data.get("is_tonpuu", False)),
            time_control=time_control,
        )

    @staticmethod
    def _make_names(payload, config: GameConfig) -> List[str]:
        names = payload.get("player_names")
        if isinstance(names, list) and len(names) >= config.num_players:
            return [str(name) for name in names[:config.num_players]]
        human_name = str(payload.get("player_name", "You"))
        return [human_name] + [f"AI {seat}" for seat in range(1, config.num_players)]

    def _on_event(self, event: GameEvent, game_view: Optional[GameView]) -> None:
        self._send(event_message(event, game_view))

    def _on_round_end(self, result: RoundResult, game: GameState) -> None:
        self._send(make_message("round_result", {
            "result": round_result_to_dict(result),
            "scores": [player.score + result.score_changes[i]
                       for i, player in enumerate(game.players)],
        }))

    def _choose_action(self, request: ActionRequest) -> Action:
        self._send(action_request_message(
            request.request_id, request.game_view, request.available
        ))
        while True:
            message = self._read()
            if message.get("type") == "quit":
                raise SessionCancelled("frontend requested shutdown")
            try:
                request_id, action = action_from_response(message)
            except ProtocolError as exc:
                self._send(make_message("error", {"message": str(exc)}))
                continue
            if request_id != request.request_id:
                self._send(make_message("error", {
                    "message": "action response does not match request_id",
                    "request_id": request.request_id,
                }))
                continue
            if not request.available.contains(action):
                self._send(make_message("error", {
                    "message": "action is not legal for the current request",
                    "request_id": request.request_id,
                }))
                continue
            return action

    def run(self) -> Optional[SessionResult]:
        """Run the stdio service until the match finishes or is cancelled."""
        self._send(make_message("hello", {
            "capabilities": [
                "game_view",
                "available_actions",
                "action_requests",
                "events",
                "round_results",
            ],
        }))
        try:
            payload = self._read_start()
            config = self._make_config(payload)
            names = self._make_names(payload, config)
            logger = GameLogger(names, game_config_to_dict(config))
            self._send(make_message("ready", {
                "config": game_config_to_dict(config),
                "player_names": names,
            }))
            session = GameSession(
                config,
                names,
                human_seat=0,
                action_provider=self._choose_action,
                event_sink=self._on_event,
                round_sink=self._on_round_end,
                logger=logger,
            )
            result = session.run()
            self._send(make_message("game_end", {
                "scores": result.game.final_scores,
                "rounds": len(result.round_results),
                "log_path": result.log_path,
            }))
            return result
        except SessionCancelled as exc:
            self._send(make_message("cancelled", {"reason": str(exc)}))
            return None
        except (ProtocolError, ValueError, TypeError) as exc:
            self._send(make_message("error", {"message": str(exc)}))
            return None
