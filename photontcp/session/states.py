"""Session-layer state, event, and timing definitions for PhotonTCP.

This module defines the building blocks consumed by the pure session state
machine (M2-T03) and the synchronous session driver (M2-T04):

* :class:`SessionState` -- the connection lifecycle states traversed during the
  3-way handshake, the established phase, and the graceful close handshake::

      CLOSED -> SYN_SENT / SYN_RCVD -> ESTABLISHED -> FIN_WAIT / CLOSE_WAIT -> CLOSED

* :class:`SessionEvent` -- the events the state machine surfaces to the upper
  layer. The state machine returns these as a ``list[SessionEvent]`` so callers
  can react to lifecycle changes (connection established, peer closed, fully
  closed, or died from heartbeat absence).

* Default timing constants (seconds) for the heartbeat / idle-timeout machinery.
  These are plain floats so they can be overridden per session and advanced via
  an injectable virtual clock during deterministic tests.

Only the standard library (:mod:`enum`) is used; this module has no I/O,
channel, or clock dependencies.
"""

from enum import Enum

__all__ = [
    "SessionState",
    "SessionEvent",
    "DEFAULT_HEARTBEAT_INTERVAL",
    "DEFAULT_IDLE_TIMEOUT",
    "DEFAULT_CONTROL_RTO",
    "DEFAULT_MAX_CONTROL_RETRIES",
]


class SessionState(Enum):
    """Lifecycle states of a PhotonTCP session.

    Transition overview (lossless M2 assumption)::

        CLOSED ──connect()/SYN sent──────────────> SYN_SENT
        CLOSED ──SYN received/SYN_ACK sent────────> SYN_RCVD
        SYN_SENT ──SYN_ACK received/ACK sent──────> ESTABLISHED
        SYN_RCVD ──ACK received────────────────────> ESTABLISHED
        ESTABLISHED ──local close()/FIN sent──────> FIN_WAIT
        ESTABLISHED ──FIN received/FIN_ACK sent───> CLOSE_WAIT
        FIN_WAIT ──FIN_ACK received───────────────> CLOSED
        CLOSE_WAIT ──local close() then FIN_ACK───> CLOSED
        (any) ──idle timeout──────────────────────> CLOSED
    """

    CLOSED = "CLOSED"
    SYN_SENT = "SYN_SENT"
    SYN_RCVD = "SYN_RCVD"
    ESTABLISHED = "ESTABLISHED"
    FIN_WAIT = "FIN_WAIT"
    CLOSE_WAIT = "CLOSE_WAIT"


class SessionEvent(Enum):
    """Events the session state machine reports to the upper layer.

    Returned from state-machine input methods as a ``list[SessionEvent]`` so a
    single step may emit zero, one, or several events.
    """

    #: The 3-way handshake completed; the session is now usable.
    ESTABLISHED = "ESTABLISHED"
    #: The remote peer initiated a graceful close (sent FIN).
    PEER_CLOSED = "PEER_CLOSED"
    #: The session is fully closed (close handshake finished).
    CLOSED = "CLOSED"
    #: The session died because no traffic arrived within the idle timeout.
    TIMED_OUT = "TIMED_OUT"
    #: The connection could not be established (or torn down) because a control
    #: packet (SYN/SYN_ACK/FIN) went unacknowledged past ``max_control_retries``
    #: retransmissions. Emitted from the establishment phase (SYN_SENT/SYN_RCVD);
    #: an analogous failure during the close phase surfaces as ``TIMED_OUT``.
    CONNECT_FAILED = "CONNECT_FAILED"


#: Seconds between HEARTBEAT frames sent while a session is ESTABLISHED and idle.
DEFAULT_HEARTBEAT_INTERVAL: float = 1.0

#: Seconds without any received frame after which a session is declared dead.
#: Must be strictly greater than :data:`DEFAULT_HEARTBEAT_INTERVAL` so that a
#: peer sending heartbeats on schedule keeps the link alive.
DEFAULT_IDLE_TIMEOUT: float = 3.0

#: Fixed retransmission timeout (seconds) for unacknowledged control packets
#: (SYN/SYN_ACK/FIN). M3 uses a simple fixed RTO rather than the adaptive
#: :class:`~photontcp.reliability.rto.RtoEstimator` for the handshake/close path.
DEFAULT_CONTROL_RTO: float = 0.5

#: Maximum number of control-packet retransmissions before giving up. Exceeding
#: this aborts the session (CONNECT_FAILED during establishment, TIMED_OUT
#: during close).
DEFAULT_MAX_CONTROL_RETRIES: int = 5
