"""PhotonTCP session layer (M2).

Connection-oriented session logic: handshake, graceful shutdown, and
heartbeats, built as a pure state machine (:class:`SessionStateMachine`) driven
synchronously by :class:`Session` over a pluggable :class:`Channel` and an
injectable :class:`Clock`.

This package ``__init__`` re-exports the public session API so callers can
import from ``photontcp.session`` directly::

    from photontcp.session import Session, ManualClock, SessionEvent
"""

from .clock import Clock, ManualClock, MonotonicClock
from .session import Session
from .state_machine import Output, SessionStateMachine
from .states import (
    DEFAULT_HEARTBEAT_INTERVAL,
    DEFAULT_IDLE_TIMEOUT,
    SessionEvent,
    SessionState,
)

__all__ = [
    # Clocks
    "Clock",
    "ManualClock",
    "MonotonicClock",
    # States / events / timing constants
    "SessionState",
    "SessionEvent",
    "DEFAULT_HEARTBEAT_INTERVAL",
    "DEFAULT_IDLE_TIMEOUT",
    # State machine
    "SessionStateMachine",
    "Output",
    # Synchronous driver
    "Session",
]
