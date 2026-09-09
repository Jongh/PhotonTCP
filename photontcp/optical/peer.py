"""Headless single-peer session driver (M11-T02).

This module holds the *session driving* half of what used to live inside
``examples/optical_link.py``'s ``_drive_real_peer``: the handshake -> tiny message
exchange -> graceful close progression for ONE peer of a two-peer optical link,
with every ``print`` / ``argparse`` / UI concern removed.

Why it exists
=============

Two front-ends need exactly the same drive loop:

* ``examples/optical_link.py --real --role sender|receiver`` (a CLI on a desktop), and
* the Kivy mobile app (``photontcp.mobile.app``), which runs the session on a worker
  thread and paints progress into a status label.

Keeping the loop here means the mobile app adds **no** new session logic (and the
loop is unit-testable without hardware).

Contract
========

:func:`run_peer` depends only on the :class:`~photontcp.channel.Channel` interface —
never on ``OpticalChannel`` or on any cv2/Kivy device — so it can be exercised
in-memory over :meth:`OpticalChannel.pair` by driving the two roles on two threads.
It never prints: progress is reported through the optional ``on_event`` callback.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from photontcp.app import ChatSession
from photontcp.channel import Channel
from photontcp.session import ManualClock, Session

__all__ = [
    "PeerResult",
    "run_peer",
    "DEFAULT_MESSAGES",
    "EVENT_KINDS",
]

# --- Link / timing parameters (mirrors of the values the CLI demo used). ---- #

#: Virtual seconds advanced per pump round. Must exceed the control RTO so any
#: unacknowledged handshake/close frame would be retransmitted each round.
ROUND_DT = 0.6

#: Heartbeat / idle-timeout window handed to the underlying :class:`Session`.
HEARTBEAT_INTERVAL = 5.0
IDLE_TIMEOUT = 120.0

# Hard upper bounds so no pump loop can ever run forever (a round cap AND the
# wall-clock ``timeout``, since a real link also burns real time per round).
MAX_HANDSHAKE_ROUNDS = 200
MAX_CHAT_ROUNDS = 200
MAX_CLOSE_ROUNDS = 200

#: Default absolute wall-clock budget for the whole drive.
DEFAULT_TIMEOUT = 60.0

#: Default seconds to pause per pump round. Over a real optical link this must be
#: at least one camera frame period so the peer's camera can capture each shown
#: frame; in-memory callers may lower it to make tests fast.
DEFAULT_HOLD = 0.4

#: Default message sets as ``(sender_messages, receiver_messages)``. QR decode is
#: slow over light, so both bodies and counts stay tiny.
DEFAULT_MESSAGES: tuple[tuple[str, ...], tuple[str, ...]] = (
    ("hi over light", "frame two"),
    ("got your light", "B done"),
)

#: The complete, stable set of ``kind`` values passed to ``on_event``. Front-ends
#: (CLI prints, Kivy status label) may switch on these; new kinds are additive, so
#: an unknown kind must simply be ignored.
EVENT_KINDS: tuple[str, ...] = (
    "start",  # detail: {"role", "is_initiator"} - drive is starting
    "connect",  # detail: {"is_initiator"} - SYN sent (initiator) / awaiting SYN
    "handshake",  # detail: {"established": bool, "rounds": int | None}
    "sent",  # detail: {"msg_id": int, "text": str} - one outgoing chat message
    "received",  # detail: {"msg_id": int, "text": str} - one arrived chat message
    "match",  # detail: {"received": int, "expected": int, "match": bool}
    "closing",  # detail: {"is_initiator"} - FIN sent (initiator) / awaiting FIN
    "closed",  # detail: {"closed": bool, "rounds": int | None}
    "error",  # detail: {"stage": str, "message": str} - a stage failed/timed out
    "done",  # detail: {"result": PeerResult} - final outcome (see note below)
)

# ``done`` is the LAST event on every path that RETURNS -- including a failed
# handshake or a failed close, which report an ``error`` first and still finish
# with ``done``. It is NOT emitted when the drive unwinds via an exception
# raised by the channel itself (the mobile app's Stop button does exactly that,
# via its cancelling channel wrapper): there the caller learns the outcome from
# the exception, not from an event. So: "if run_peer returns, ``done`` was the
# last event"; a caller that must react to cancellation catches the exception.

EventCallback = Callable[[str, dict], None]


@dataclass
class PeerResult:
    """Outcome of one :func:`run_peer` drive.

    The first five fields are exactly what the CLI summary line reports; the rest
    are extra detail that a UI (or a test) may show.
    """

    #: Peer role that was driven (``"sender"`` or ``"receiver"``).
    role: str = ""
    #: True when this peer reached ESTABLISHED within the caps/deadline.
    established: bool = False
    #: True when the messages received equal the peer's expected set, in order.
    messages_match: bool = False
    #: True when this peer reached CLOSED within the caps/deadline.
    closed: bool = False
    #: Wall-clock seconds the whole drive took.
    wall_seconds: float = 0.0
    #: Overall verdict: ``established and messages_match and closed``.
    passed: bool = False

    #: Pump rounds the handshake took (``None`` if it never completed).
    handshake_rounds: int | None = None
    #: Pump rounds the close took (``None`` if it never completed).
    close_rounds: int | None = None
    #: Message bodies this peer sent, in order.
    sent: list[str] = field(default_factory=list)
    #: Message bodies this peer received, in order.
    received: list[str] = field(default_factory=list)
    #: Message bodies this peer expected to receive (the peer's outgoing set).
    expected: list[str] = field(default_factory=list)
    #: True when the wall-clock budget ran out before the drive finished.
    timed_out: bool = False


def _make_emitter(on_event: EventCallback | None) -> Callable[[str, dict], None]:
    """Build a never-raising event emitter.

    A front-end callback (a Kivy label update, a print) must never be able to kill
    a live hardware session, so callback exceptions are swallowed here.
    """

    if on_event is None:
        return lambda kind, detail: None

    def emit(kind: str, detail: dict) -> None:
        try:
            on_event(kind, detail)
        except Exception:  # pragma: no cover - defensive: UI callbacks must not kill the drive
            pass

    return emit


def run_peer(
    channel: Channel,
    role: str,
    *,
    messages: tuple[Sequence[str], Sequence[str]] = DEFAULT_MESSAGES,
    timeout: float = DEFAULT_TIMEOUT,
    hold: float = DEFAULT_HOLD,
    on_event: EventCallback | None = None,
    close_channel: bool = True,
) -> PeerResult:
    """Drive ONE peer of a two-peer link through handshake -> chat -> close.

    The other peer must be driving the opposite ``role`` concurrently (another
    process over real hardware, or another thread over
    :meth:`~photontcp.optical.channel.OpticalChannel.pair` in memory).

    :param channel: Any :class:`~photontcp.channel.Channel`. Only the ``Channel``
        contract is used, so the caller decides whether frames travel over light,
        over an in-memory pair, or over anything else.
    :param role: ``"sender"`` (the connection initiator) or ``"receiver"`` (the
        responder). Any other value raises :class:`ValueError`.
    :param messages: ``(sender_messages, receiver_messages)``. The driven peer
        sends its own half and expects the other half to arrive, in order.
    :param timeout: Absolute wall-clock budget in seconds for the whole drive.
        Every stage is bounded by it *and* by a hard round cap, so this call can
        never hang.
    :param hold: Seconds to pause per pump round (pacing for a real optical link;
        lower it for fast in-memory runs). It must be **strictly positive** even
        in memory: the optical channel delivers on a background capture thread, so
        a zero pause simply burns the round caps before any frame is decoded --
        a guaranteed failure, which is why ``hold <= 0`` raises :class:`ValueError`
        rather than running a drive that cannot succeed.
    :param on_event: Optional ``on_event(kind, detail)`` progress callback. ``kind``
        is one of :data:`EVENT_KINDS`; ``detail`` is a dict. Exceptions raised by
        the callback are swallowed. This function itself never prints.
    :param close_channel: When true (the default) the channel is closed before
        returning, stopping any background capture thread. Devices handed to the
        channel remain the caller's to close.
    :returns: A :class:`PeerResult`.
    """
    if role not in ("sender", "receiver"):
        raise ValueError(f"role must be 'sender' or 'receiver', got {role!r}")
    if hold <= 0:
        # A zero/negative pause never lets the capture thread run: the drive would
        # burn its round caps and report a failure that looks like a hardware
        # problem. Refuse loudly instead of driving a session that cannot pass.
        raise ValueError(
            f"hold must be > 0 (got {hold!r}); a zero pause starves the channel's "
            "capture thread and the drive can never complete"
        )
    if len(messages) < 2:
        raise ValueError(
            "messages must be a (sender_messages, receiver_messages) pair, "
            f"got {len(messages)} element(s)"
        )

    emit = _make_emitter(on_event)
    is_initiator = role == "sender"

    sender_msgs = list(messages[0])
    receiver_msgs = list(messages[1])
    out_msgs = sender_msgs if is_initiator else receiver_msgs
    expected_in = receiver_msgs if is_initiator else sender_msgs

    result = PeerResult(role=role, expected=list(expected_in))

    started = time.perf_counter()
    deadline = started + timeout
    emit("start", {"role": role, "is_initiator": is_initiator})

    clock = ManualClock()
    session = Session(
        channel,
        clock,
        is_initiator=is_initiator,
        session_id=1 if is_initiator else 0,
        isn=1000 if is_initiator else 5000,
        heartbeat_interval=HEARTBEAT_INTERVAL,
        idle_timeout=IDLE_TIMEOUT,
    )
    chat = ChatSession(session, clock)

    def _round() -> bool:
        """Advance virtual time and pace one round. False when out of budget."""
        if time.perf_counter() > deadline:
            result.timed_out = True
            return False
        clock.advance(ROUND_DT)
        time.sleep(hold)  # validated > 0 above
        return True

    def _drive() -> None:
        # --- [1] Handshake. ------------------------------------------------ #
        if is_initiator:
            chat.connect()
        emit("connect", {"is_initiator": is_initiator})

        for rnd in range(1, MAX_HANDSHAKE_ROUNDS + 1):
            if not _round():
                break
            chat.pump()
            if chat.is_established:
                result.established = True
                result.handshake_rounds = rnd
                break
        emit(
            "handshake",
            {"established": result.established, "rounds": result.handshake_rounds},
        )
        if not result.established:
            emit(
                "error",
                {
                    "stage": "handshake",
                    "message": "handshake did not complete within the cap/deadline",
                },
            )
            return

        # --- [2] Exchange the tiny message set. ---------------------------- #
        next_out = 0
        for _rnd in range(1, MAX_CHAT_ROUNDS + 1):
            if not _round():
                break
            if next_out < len(out_msgs):
                text = out_msgs[next_out]
                msg_id = chat.send_message(text)
                result.sent.append(text)
                next_out += 1
                emit("sent", {"msg_id": msg_id, "text": text})
            for msg in chat.pump():
                emit("received", {"msg_id": msg.msg_id, "text": msg.text})
            if next_out >= len(out_msgs) and len(chat.received) >= len(expected_in):
                break

        result.received = [m.text for m in chat.received]
        result.messages_match = result.received == expected_in
        emit(
            "match",
            {
                "received": len(result.received),
                "expected": len(expected_in),
                "match": result.messages_match,
            },
        )

        # --- [3] Graceful close. ------------------------------------------- #
        if is_initiator:
            chat.close()
        emit("closing", {"is_initiator": is_initiator})

        for rnd in range(1, MAX_CLOSE_ROUNDS + 1):
            if not _round():
                break
            chat.pump()
            if chat.is_closed:
                result.closed = True
                result.close_rounds = rnd
                break
        emit("closed", {"closed": result.closed, "rounds": result.close_rounds})
        if not result.closed:
            emit(
                "error",
                {
                    "stage": "close",
                    "message": "this peer did not reach CLOSED within the cap/deadline",
                },
            )

    try:
        _drive()
    finally:
        if close_channel:
            # Always stop any background capture thread the channel owns.
            channel.close()

    return _finish(result, started, emit)


def _finish(
    result: PeerResult, started: float, emit: Callable[[str, dict], None]
) -> PeerResult:
    """Stamp the wall clock + verdict onto ``result`` and emit the final event."""
    result.wall_seconds = time.perf_counter() - started
    result.passed = result.established and result.messages_match and result.closed
    emit("done", {"result": result})
    return result
