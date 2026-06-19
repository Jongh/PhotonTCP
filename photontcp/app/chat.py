"""Chat application endpoint (M4-T04).

This module ties together the two halves of the chat application: the
:class:`~photontcp.session.session.Session` (the reliable, multiplexed byte
transport with its synchronous pump) and the codec
(:class:`~photontcp.app.codec.ChatMessage`,
:func:`~photontcp.app.codec.encode_message`, and
:class:`~photontcp.app.codec.StreamReassembler`).

:class:`ChatSession` is a thin, message-oriented facade over a single
application stream of a :class:`Session`. Outgoing text is wrapped in a
:class:`ChatMessage` (stamped with a monotonically increasing ``msg_id`` and
the *injected* clock's time), framed by :func:`encode_message`, and handed to
the session. Incoming bytes drained from the session are fed to a
:class:`StreamReassembler` which yields fully reassembled
:class:`ChatMessage`\\s in stream order.

Time is read **only** through the injected :class:`~photontcp.session.clock.Clock`
(never the wall clock), so message timestamps -- and therefore the whole chat
exchange -- are fully deterministic and testable.

Only the standard library and precedent PhotonTCP modules are used. Imports use
submodule paths so this module does not depend on package ``__init__``
re-exports.
"""

from __future__ import annotations

from photontcp.session.clock import Clock
from photontcp.session.session import Session
from photontcp.session.states import SessionEvent, SessionState
from photontcp.stream.mux import DEFAULT_STREAM_ID

from .codec import ChatMessage, StreamReassembler, encode_message

__all__ = ["ChatSession"]


class ChatSession:
    """A message-oriented chat endpoint over a single :class:`Session` stream.

    Wraps a connected (or connecting) :class:`Session` and a
    :class:`StreamReassembler` to expose a simple send/receive API in terms of
    :class:`ChatMessage`\\s instead of raw bytes. All transport concerns
    (handshake, reliability, retransmission, ordering) are owned by the
    underlying session; this class only handles message framing and timestamp
    stamping.

    Sent messages get a monotonically increasing ``msg_id`` starting at ``1``
    and a ``timestamp`` taken from the injected clock. Received bytes are
    reassembled into messages, returned from :meth:`pump`, and also accumulated
    in :attr:`received`.

    Args:
        session: The underlying transport session. It is driven (pumped) by
            this endpoint but otherwise owned by the caller.
        clock: The injected monotonic time source used to stamp outgoing
            message timestamps. Using the same clock the session uses keeps the
            whole exchange deterministic.
        stream_id: The application stream this endpoint sends and receives on.
            Defaults to :data:`DEFAULT_STREAM_ID` (the shared default stream).
    """

    def __init__(
        self,
        session: Session,
        clock: Clock,
        *,
        stream_id: int = DEFAULT_STREAM_ID,
    ) -> None:
        self.session = session
        self.clock = clock
        self.stream_id = stream_id

        #: Next msg_id to assign to an outgoing message (1-based, monotonic).
        self._next_msg_id = 1
        #: Reassembles inbound stream bytes into complete chat messages.
        self._reassembler = StreamReassembler()
        #: All messages received so far, in stream order.
        self._received: list[ChatMessage] = []

    # ------------------------------------------------------------------ #
    # Read-only state introspection (delegated to the session)
    # ------------------------------------------------------------------ #

    @property
    def state(self) -> SessionState:
        """Current session lifecycle state (delegates to the session)."""
        return self.session.state

    @property
    def is_established(self) -> bool:
        """``True`` while the underlying session is ESTABLISHED."""
        return self.session.is_established

    @property
    def is_closed(self) -> bool:
        """``True`` while the underlying session is CLOSED."""
        return self.session.is_closed

    @property
    def received(self) -> list[ChatMessage]:
        """All messages received so far, in stream order.

        Returns the live internal list of accumulated messages (extended by
        each :meth:`pump`). Callers should treat it as read-only.
        """
        return self._received

    # ------------------------------------------------------------------ #
    # Lifecycle drivers (delegated to the session)
    # ------------------------------------------------------------------ #

    def connect(self) -> list[SessionEvent]:
        """Begin the handshake on the underlying session (initiator only).

        Returns the lifecycle events surfaced by the session.
        """
        return self.session.connect()

    def close(self) -> list[SessionEvent]:
        """Begin a graceful close of the underlying session.

        Returns the lifecycle events surfaced by the session.
        """
        return self.session.close()

    # ------------------------------------------------------------------ #
    # Message send / receive
    # ------------------------------------------------------------------ #

    def send_message(self, text: str) -> int:
        """Frame and queue a chat message for reliable delivery.

        Builds a :class:`ChatMessage` with the next ``msg_id`` and the injected
        clock's current time, encodes it with :func:`encode_message`, and hands
        the framed bytes to the session's stream. The message is not delivered
        locally; the peer will receive it via its own :meth:`pump`.

        Args:
            text: The message body text (arbitrary Unicode).

        Returns:
            The ``msg_id`` assigned to this message.

        Raises:
            RuntimeError: Propagated from the session if it is not ESTABLISHED
                (data may only be sent on an established connection).
        """
        msg = ChatMessage(
            msg_id=self._next_msg_id,
            timestamp=self.clock.now(),
            text=text,
        )
        frame = encode_message(msg)
        # send_on raises RuntimeError if not ESTABLISHED; let it propagate
        # before consuming the msg_id so a failed send doesn't burn an id.
        self.session.send_on(self.stream_id, frame)
        self._next_msg_id += 1
        return msg.msg_id

    def pump(self) -> list[ChatMessage]:
        """Advance the session and return newly received chat messages.

        Pumping the session drives the handshake/close, retransmission timers,
        and inbound frame processing. Any bytes delivered in-order on this
        endpoint's stream are then drained and fed to the reassembler; the
        resulting fully reassembled messages are appended to :attr:`received`
        and returned.

        Returns:
            The chat messages that completed during this cycle, in stream
            order. May be empty.
        """
        self.session.pump()

        new_messages: list[ChatMessage] = []
        for chunk in self.session.recv_on(self.stream_id):
            new_messages.extend(self._reassembler.feed(chunk))

        self._received.extend(new_messages)
        return new_messages
