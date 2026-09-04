"""Frontend-neutral application services for MahjongCLI.

The terminal UI remains the default frontend.  The modules in this package
provide a stable session boundary for other clients, including a future Rust
desktop application.
"""

from .protocol import PROTOCOL_NAME, PROTOCOL_VERSION, ProtocolError
from .session import ActionRequest, GameSession, SessionResult

__all__ = [
    "ActionRequest",
    "GameSession",
    "PROTOCOL_NAME",
    "PROTOCOL_VERSION",
    "ProtocolError",
    "SessionResult",
]
