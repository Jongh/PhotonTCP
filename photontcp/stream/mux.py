"""Stream multiplexer for PhotonTCP (M4-T01).

A single PhotonTCP session carries several independent logical *streams*. This
module provides :class:`StreamMux`, a pure, I/O- and clock-independent router
that owns one :class:`~photontcp.reliability.arq.ArqEndpoint` per stream so that
each stream gets its own reliability, ordering and retransmission. A loss or gap
on one stream therefore never blocks delivery on another (no head-of-line
blocking across streams).

Stream ID convention
--------------------

* ``0`` (:data:`CONTROL_STREAM_ID`) is reserved for the control plane
  (handshake / session management). The multiplexer never owns an ARQ endpoint
  for stream 0 and defensively ignores inbound packets carrying it -- the
  ``Session`` handles control packets itself.
* ``1`` (:data:`DEFAULT_STREAM_ID`) is the default shared application stream,
  used by the legacy ``send()``/``recv()`` path. Because both peers share it,
  :meth:`StreamMux.open_stream` never returns it.
* Additional streams allocated via :meth:`StreamMux.open_stream` use a parity
  convention to avoid collisions between the two peers: the **initiator** hands
  out odd ids (``3, 5, 7, ...``) and the **responder** hands out even ids
  (``2, 4, 6, ...``).

Design principles (shared with the rest of PhotonTCP):

* **No I/O, no channel, no real clock.** Every method that needs the current
  time takes an explicit ``now: float`` argument; the multiplexer is purely
  deterministic.
* **Implicit stream open.** A stream springs into existence the first time data
  flows on it (either an outbound :meth:`send` or an inbound DATA packet) -- no
  explicit per-stream open handshake.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..packet.header import Packet
from ..reliability.arq import ArqEndpoint
from ..reliability.rto import RtoEstimator

__all__ = [
    "CONTROL_STREAM_ID",
    "DEFAULT_STREAM_ID",
    "MuxOutput",
    "StreamMux",
]

#: Reserved control-plane stream id (handshake / session management).
CONTROL_STREAM_ID = 0

#: Default shared application stream id (legacy ``send()``/``recv()`` path).
DEFAULT_STREAM_ID = 1


@dataclass
class MuxOutput:
    """Result of a multiplexer operation.

    Attributes:
        packets: Packets the host should transmit, in order, across all streams.
        delivered: Per-stream payload chunks delivered to the application,
            keyed by ``stream_id`` and in order. A stream key is present only
            when that stream delivered at least one chunk.
    """

    packets: list[Packet] = field(default_factory=list)
    delivered: dict[int, list[bytes]] = field(default_factory=dict)


class StreamMux:
    """Per-stream ARQ multiplexer for a single session.

    The multiplexer owns one :class:`ArqEndpoint` per application stream
    (``stream_id >= 1``) and routes outbound/inbound traffic to the right
    endpoint, giving each stream independent reliability and ordering. It is
    purely deterministic and takes the current time as an explicit ``now``
    argument on every method that needs it.
    """

    def __init__(
        self,
        *,
        session_id: int,
        is_initiator: bool,
        window_size: int = 32,
        max_payload: int = 200,
        rto_factory=None,
    ) -> None:
        """Initialise the multiplexer.

        Args:
            session_id: Session identifier stamped on every emitted packet.
                Mutable: update it via :meth:`set_session_id` (e.g. when the
                responder adopts the negotiated id from the handshake).
            is_initiator: Whether this peer is the connection initiator. Decides
                the parity of ids handed out by :meth:`open_stream` (initiator =
                odd, responder = even).
            window_size: Per-stream send-window limit passed to each endpoint.
            max_payload: Chunk size used to split application data per stream.
            rto_factory: Zero-argument callable producing a fresh
                :class:`RtoEstimator` for each new stream. Defaults to
                ``lambda: RtoEstimator()`` when ``None``.
        """
        self.session_id = int(session_id)
        self.is_initiator = bool(is_initiator)
        self.window_size = int(window_size)
        self.max_payload = int(max_payload)
        self._rto_factory = rto_factory if rto_factory is not None else (
            lambda: RtoEstimator()
        )

        # stream_id -> ArqEndpoint (application streams, stream_id >= 1 only).
        self._endpoints: dict[int, ArqEndpoint] = {}

        # Next id to consider for open_stream(). Initiator hands out odd ids
        # starting at 3; responder hands out even ids starting at 2. (Stream 1
        # is the shared default and is never returned by open_stream.)
        self._next_open_id = 3 if self.is_initiator else 2

    # ------------------------------------------------------------------
    # Read-only helpers (introspection / testing)
    # ------------------------------------------------------------------
    def stream_ids(self) -> list[int]:
        """Return the ids of all currently-known streams, sorted ascending."""
        return sorted(self._endpoints)

    def has_stream(self, stream_id: int) -> bool:
        """Return whether an endpoint exists for *stream_id*."""
        return stream_id in self._endpoints

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _endpoint(self, stream_id: int) -> ArqEndpoint:
        """Return the endpoint for *stream_id*, creating it on first use.

        This is the implicit-open path: any stream id that has not been seen
        before gets a fresh :class:`ArqEndpoint` lazily.
        """
        ep = self._endpoints.get(stream_id)
        if ep is None:
            ep = ArqEndpoint(
                session_id=self.session_id,
                send_isn=0,
                recv_isn=0,
                window_size=self.window_size,
                rto=self._rto_factory(),
                max_payload=self.max_payload,
                stream_id=stream_id,
            )
            self._endpoints[stream_id] = ep
        return ep

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def open_stream(self) -> int:
        """Allocate and open a new application stream, returning its id.

        Ids follow the parity convention (initiator: odd ``3, 5, ...``;
        responder: even ``2, 4, ...``). The lowest free id of the correct
        parity is chosen so the two peers never collide.
        """
        sid = self._next_open_id
        while sid in self._endpoints:
            sid += 2
        self._endpoint(sid)  # create the endpoint
        self._next_open_id = sid + 2
        return sid

    def send(self, stream_id: int, data: bytes, now: float) -> MuxOutput:
        """Queue *data* for reliable, ordered delivery on *stream_id*.

        The target endpoint is created on first use (implicit open).
        """
        out = MuxOutput()
        result = self._endpoint(stream_id).send(data, now)
        out.packets.extend(result.packets)
        return out

    def on_packet(self, pkt: Packet, now: float) -> MuxOutput:
        """Route one received packet to its stream's endpoint.

        Packets carrying :data:`CONTROL_STREAM_ID` are ignored (returns an empty
        :class:`MuxOutput`) because the control plane belongs to the
        ``Session``. For application streams the inbound packet is routed to the
        matching endpoint (created on first use), and any delivered chunks are
        recorded under that stream's id.
        """
        out = MuxOutput()
        if pkt.stream_id == CONTROL_STREAM_ID:
            return out

        result = self._endpoint(pkt.stream_id).on_packet(pkt, now)
        out.packets.extend(result.packets)
        if result.delivered:
            out.delivered[pkt.stream_id] = list(result.delivered)
        return out

    def on_tick(self, now: float) -> MuxOutput:
        """Advance every stream's retransmission timers at time *now*.

        Returns the union of all packets each endpoint asks to (re)transmit.
        """
        out = MuxOutput()
        for ep in self._endpoints.values():
            result = ep.on_tick(now)
            out.packets.extend(result.packets)
        return out

    def set_session_id(self, sid: int) -> None:
        """Adopt session id *sid* and propagate it to all known endpoints.

        Used when the responder learns the negotiated session id from the
        handshake; new endpoints created afterwards inherit it too.
        """
        self.session_id = int(sid)
        for ep in self._endpoints.values():
            ep.session_id = self.session_id
