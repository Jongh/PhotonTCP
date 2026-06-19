"""Single-file, one-directional file-transfer application (M6-T02).

This module layers a reliable, single-directional **file transfer** with an
**application-level completion handshake** on top of the PhotonTCP transport
(:class:`~photontcp.session.session.Session`, which provides the M3 ARQ
reliability + M4 multiplexed streams). It mirrors the chat application's design
(:class:`~photontcp.app.chat.ChatSession`): a pure frame codec
(:mod:`photontcp.app.file_codec`) plus a **synchronous pump driver**. There is
no background thread; the caller advances each endpoint by repeatedly calling
:meth:`FileSender.pump` / :meth:`FileReceiver.pump`.

The protocol (see :mod:`photontcp.app.file_codec` for the wire format)::

    sender                              receiver
    ------                              --------
    OFFER {name,size,sha256}  ----->
                              <-----    ACCEPT            (or REJECT -> FAILED)
    CHUNK (raw bytes) * N     ----->    (append to buffer)
    DONE {}                   ----->
                              <-----    ACK {ok:true}     (sha matches  -> COMPLETE)
                                        NACK {ok:false}   (sha mismatch -> FAILED)
    (COMPLETE: caller closes session)

Completion handshake = flush-on-close guarantee
-----------------------------------------------
The sender reaches :attr:`FileTransferState.COMPLETE` **only after it has
reliably received the receiver's ACK** -- which the receiver sends only after
it has received *every* CHUNK and *DONE*, reassembled the whole file, and
verified its SHA-256 against the OFFER. Because the ACK rides the same reliable,
ordered stream as the data, its delivery proves that all preceding bytes were
delivered too. Therefore the sender can safely close the session once it is
COMPLETE without losing any in-flight data: the handshake stands in for an
explicit transport-level flush-on-close API (the M4 review follow-up). This
class deliberately **never closes the session itself** -- it only exposes
:attr:`FileSender.is_complete`; the caller closes after COMPLETE.

Determinism
-----------
All time is read through the injected :class:`~photontcp.session.clock.Clock`
(never the wall clock). The codec carries no timestamps, so a transfer over a
seeded loopback channel + :class:`~photontcp.session.clock.ManualClock` is fully
deterministic and testable.

The transport's own ARQ owns reliability, ordering, retransmission, and flow
control (the send window). This layer only does **framing + the completion
handshake + progress accounting**; it queues all chunk frames eagerly and lets
the ARQ pace them onto the wire.

Only the standard library and precedent PhotonTCP modules are used. Imports use
submodule paths so this module does not depend on package ``__init__``
re-exports.
"""

from __future__ import annotations

from enum import Enum, auto

from photontcp.session.clock import Clock
from photontcp.session.session import Session
from photontcp.stream.mux import DEFAULT_STREAM_ID

from .file_codec import (
    FileFrameReassembler,
    FileFrameType,
    decode_control,
    encode_control,
    encode_frame,
    sha256_hex,
)

__all__ = [
    "FILE_STREAM_ID",
    "FileTransferState",
    "FileSender",
    "FileReceiver",
]

#: Default application stream id used by both endpoints. Both peers must agree
#: on the same stream, so this is a fixed constant (and may be overridden per
#: endpoint via the ``stream_id`` argument -- both sides must match). It shares
#: the transport's default stream; pass a distinct ``stream_id`` (e.g. from
#: :meth:`Session.open_stream` agreed out of band) to run a file transfer
#: alongside chat on the same session.
FILE_STREAM_ID = DEFAULT_STREAM_ID


class FileTransferState(Enum):
    """Lifecycle state shared by the file sender and receiver.

    Not every state is used by both roles. The sender walks
    ``IDLE -> OFFERED -> SENDING -> DONE_SENT -> COMPLETE`` (or ``FAILED``); the
    receiver walks ``IDLE -> RECEIVING -> COMPLETE`` (or ``FAILED``).
    """

    #: Initial state, before any frame has been exchanged.
    IDLE = auto()
    #: Sender only: OFFER has been queued, awaiting ACCEPT/REJECT.
    OFFERED = auto()
    #: Sender only: ACCEPT received; chunk frames have been/are being queued.
    SENDING = auto()
    #: Sender only: all chunks + DONE queued, awaiting the ACK/NACK.
    DONE_SENT = auto()
    #: Receiver only: OFFER accepted; CHUNK frames are being accumulated.
    RECEIVING = auto()
    #: Terminal success: sender got ACK / receiver verified the file.
    COMPLETE = auto()
    #: Terminal failure: REJECT/NACK, or SHA-256 verification mismatch.
    FAILED = auto()


class FileSender:
    """Reliable single-file sender with a completion handshake.

    Drives the OFFER/CHUNK/DONE half of the protocol over one
    :class:`Session` stream. The whole file is held in memory and queued in
    ``chunk_size`` slices; the transport's ARQ paces them onto the wire and
    guarantees in-order delivery, so this class never retransmits or reorders.

    State transitions (driven by :meth:`pump`)::

        IDLE --start()--> OFFERED
        OFFERED --recv ACCEPT--> SENDING   (queue all chunks)
        OFFERED --recv REJECT--> FAILED
        SENDING --all chunks queued + DONE--> DONE_SENT
        DONE_SENT --recv ACK--> COMPLETE
        DONE_SENT --recv NACK--> FAILED

    The session is **never closed by this class**: the caller closes it after
    observing :attr:`is_complete` (the completion handshake guarantees all bytes
    were already delivered and verified -- see the module docstring).

    Args:
        session: The underlying transport session (pumped here, owned by the
            caller).
        clock: The injected monotonic time source (kept for symmetry with the
            chat endpoint and deterministic timing; no timestamps are sent).
        name: The file name advertised in the OFFER.
        data: The complete file contents to send.
        stream_id: The application stream both peers use. Defaults to
            :data:`FILE_STREAM_ID`; must match the receiver's ``stream_id``.
        chunk_size: Maximum payload bytes per CHUNK frame. Must be positive.
    """

    def __init__(
        self,
        session: Session,
        clock: Clock,
        *,
        name: str,
        data: bytes,
        stream_id: int = FILE_STREAM_ID,
        chunk_size: int = 1024,
    ) -> None:
        if chunk_size <= 0:
            raise ValueError(f"chunk_size must be positive, got {chunk_size!r}")

        self.session = session
        self.clock = clock
        self.name = name
        self.data = bytes(data)
        self.stream_id = stream_id
        self.chunk_size = chunk_size

        self._state = FileTransferState.IDLE
        self._reassembler = FileFrameReassembler()
        #: Bytes handed to the transport so far (for progress accounting).
        self._sent_bytes = 0

    # ------------------------------------------------------------------ #
    # Read-only introspection
    # ------------------------------------------------------------------ #

    @property
    def state(self) -> FileTransferState:
        """The sender's current :class:`FileTransferState`."""
        return self._state

    @property
    def is_complete(self) -> bool:
        """``True`` once the ACK has been received (transfer verified)."""
        return self._state is FileTransferState.COMPLETE

    @property
    def is_failed(self) -> bool:
        """``True`` if the transfer terminated via REJECT or NACK."""
        return self._state is FileTransferState.FAILED

    @property
    def progress(self) -> float:
        """Fraction of the file queued for delivery, in ``[0.0, 1.0]``.

        Counts bytes handed to the transport (not yet acknowledged). An empty
        file reports ``1.0`` once it has reached/ passed the SENDING phase, else
        ``0.0``.
        """
        total = len(self.data)
        if total == 0:
            return 1.0 if self._sent_bytes_done() else 0.0
        return self._sent_bytes / total

    def _sent_bytes_done(self) -> bool:
        """Whether all chunks have been queued (used for empty-file progress)."""
        return self._state in (
            FileTransferState.SENDING,
            FileTransferState.DONE_SENT,
            FileTransferState.COMPLETE,
        )

    # ------------------------------------------------------------------ #
    # Driver
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        """Queue the OFFER frame and move to OFFERED.

        Must be called on an ESTABLISHED session (the underlying
        :meth:`Session.send_on` raises :class:`RuntimeError` otherwise).

        Raises:
            RuntimeError: If called more than once, or if the session is not
                ESTABLISHED (propagated from the transport).
        """
        if self._state is not FileTransferState.IDLE:
            raise RuntimeError(f"start() already called (state={self._state})")

        offer = encode_control(
            FileFrameType.OFFER,
            {
                "name": self.name,
                "size": len(self.data),
                "sha256": sha256_hex(self.data),
            },
        )
        # send_on raises RuntimeError if not ESTABLISHED; let it propagate so
        # the state is not advanced on a failed send.
        self.session.send_on(self.stream_id, offer)
        self._state = FileTransferState.OFFERED

    def pump(self) -> FileTransferState:
        """Advance the session, process inbound frames, and return the state.

        One cycle:

        1. Pump the underlying session (handshake/close, timers, I/O).
        2. Feed any bytes delivered on this stream to the reassembler and act on
           each control frame: ACCEPT -> queue all chunks + DONE (SENDING ->
           DONE_SENT); REJECT -> FAILED; ACK -> COMPLETE; NACK -> FAILED.

        Returns:
            The sender's :class:`FileTransferState` after this cycle.
        """
        self.session.pump()

        for chunk in self.session.recv_on(self.stream_id):
            for frame in self._reassembler.feed(chunk):
                self._handle_frame(frame)

        return self._state

    def _handle_frame(self, frame) -> None:
        """Apply one decoded inbound control frame to the sender's state."""
        ftype = frame.type

        if ftype is FileFrameType.ACCEPT:
            # Only meaningful while OFFERED; ignore duplicates/late frames.
            if self._state is FileTransferState.OFFERED:
                self._state = FileTransferState.SENDING
                self._send_all_chunks_and_done()
        elif ftype is FileFrameType.REJECT:
            if self._state is FileTransferState.OFFERED:
                self._state = FileTransferState.FAILED
        elif ftype is FileFrameType.ACK:
            if self._state is FileTransferState.DONE_SENT:
                self._state = FileTransferState.COMPLETE
        elif ftype is FileFrameType.NACK:
            if self._state is FileTransferState.DONE_SENT:
                self._state = FileTransferState.FAILED
        # OFFER/CHUNK/DONE are receiver-bound; ignore on the sender side.

    def _send_all_chunks_and_done(self) -> None:
        """Queue every CHUNK frame then the DONE frame; move to DONE_SENT.

        All frames are handed to the transport at once; the ARQ send window
        paces them onto the wire, so this does not flood the channel. An empty
        file simply sends DONE with no chunks.
        """
        data = self.data
        size = len(data)
        step = self.chunk_size

        offset = 0
        while offset < size:
            piece = data[offset:offset + step]
            self.session.send_on(
                self.stream_id, encode_frame(FileFrameType.CHUNK, piece)
            )
            self._sent_bytes += len(piece)
            offset += len(piece)

        self.session.send_on(
            self.stream_id, encode_control(FileFrameType.DONE, {})
        )
        self._state = FileTransferState.DONE_SENT


class FileReceiver:
    """Reliable single-file receiver with a completion handshake.

    Drives the ACCEPT/ACK/NACK half of the protocol over one :class:`Session`
    stream. Chunks are concatenated in arrival order (the stream guarantees
    order); on DONE the full buffer's SHA-256 is verified against the OFFER's.

    State transitions (driven by :meth:`pump`)::

        IDLE --recv OFFER (auto_accept) --send ACCEPT--> RECEIVING
        RECEIVING --recv CHUNK--> RECEIVING (append)
        RECEIVING --recv DONE, sha matches  --send ACK--> COMPLETE
        RECEIVING --recv DONE, sha mismatch --send NACK--> FAILED

    The session is **never closed by this class**; the caller manages closing
    (typically the sender closes after its COMPLETE).

    Args:
        session: The underlying transport session (pumped here, owned by the
            caller).
        clock: The injected monotonic time source (kept for symmetry; no
            timestamps are exchanged).
        stream_id: The application stream both peers use. Defaults to
            :data:`FILE_STREAM_ID`; must match the sender's ``stream_id``.
        auto_accept: When ``True`` (default), an OFFER is accepted automatically
            by sending ACCEPT. When ``False`` the receiver stays IDLE after the
            OFFER (manual acceptance is out of scope for this milestone, but the
            offered metadata is still captured).
    """

    def __init__(
        self,
        session: Session,
        clock: Clock,
        *,
        stream_id: int = FILE_STREAM_ID,
        auto_accept: bool = True,
    ) -> None:
        self.session = session
        self.clock = clock
        self.stream_id = stream_id
        self.auto_accept = auto_accept

        self._state = FileTransferState.IDLE
        self._reassembler = FileFrameReassembler()
        self._buffer = bytearray()

        #: Offered metadata, populated on OFFER.
        self._name: str | None = None
        self._expected_size: int | None = None
        self._expected_sha: str | None = None
        #: Whether the completed file's SHA-256 matched the OFFER's.
        self._verified = False

    # ------------------------------------------------------------------ #
    # Read-only introspection
    # ------------------------------------------------------------------ #

    @property
    def state(self) -> FileTransferState:
        """The receiver's current :class:`FileTransferState`."""
        return self._state

    @property
    def is_complete(self) -> bool:
        """``True`` once the file is fully received and verified (ACK sent)."""
        return self._state is FileTransferState.COMPLETE

    @property
    def is_failed(self) -> bool:
        """``True`` if the transfer failed verification (NACK sent)."""
        return self._state is FileTransferState.FAILED

    @property
    def name(self) -> str | None:
        """The offered file name, or ``None`` before the OFFER arrives."""
        return self._name

    @property
    def verified(self) -> bool:
        """``True`` once the received file's SHA-256 matched the OFFER's."""
        return self._verified

    @property
    def progress(self) -> float:
        """Fraction of the file received so far, in ``[0.0, 1.0]``.

        ``0.0`` before the OFFER establishes the expected size. An offered size
        of ``0`` reports ``1.0`` once an OFFER has been seen.
        """
        if self._expected_size is None:
            return 0.0
        if self._expected_size == 0:
            return 1.0
        # Clamp in case more bytes than expected somehow arrive.
        return min(len(self._buffer) / self._expected_size, 1.0)

    @property
    def file_bytes(self) -> bytes | None:
        """The fully received file contents once COMPLETE, else ``None``."""
        if self._state is FileTransferState.COMPLETE:
            return bytes(self._buffer)
        return None

    # ------------------------------------------------------------------ #
    # Driver
    # ------------------------------------------------------------------ #

    def pump(self) -> FileTransferState:
        """Advance the session, process inbound frames, and return the state.

        One cycle:

        1. Pump the underlying session (handshake/close, timers, I/O).
        2. Feed any bytes delivered on this stream to the reassembler and act on
           each frame: OFFER -> capture metadata + (auto_accept) send ACCEPT
           (RECEIVING); CHUNK -> append; DONE -> verify SHA-256, send ACK +
           COMPLETE on match or NACK + FAILED on mismatch.

        Returns:
            The receiver's :class:`FileTransferState` after this cycle.
        """
        self.session.pump()

        for chunk in self.session.recv_on(self.stream_id):
            for frame in self._reassembler.feed(chunk):
                self._handle_frame(frame)

        return self._state

    def _handle_frame(self, frame) -> None:
        """Apply one decoded inbound frame to the receiver's state."""
        ftype = frame.type

        if ftype is FileFrameType.OFFER:
            if self._state is FileTransferState.IDLE:
                self._handle_offer(frame.body)
        elif ftype is FileFrameType.CHUNK:
            if self._state is FileTransferState.RECEIVING:
                self._buffer.extend(frame.body)
        elif ftype is FileFrameType.DONE:
            if self._state is FileTransferState.RECEIVING:
                self._handle_done()
        # ACCEPT/REJECT/ACK/NACK are sender-bound; ignore on the receiver side.

    def _handle_offer(self, body: bytes) -> None:
        """Capture OFFER metadata and (optionally) accept the transfer."""
        meta = decode_control(body)
        self._name = meta.get("name")
        self._expected_size = meta.get("size")
        self._expected_sha = meta.get("sha256")

        if self.auto_accept:
            self.session.send_on(
                self.stream_id, encode_control(FileFrameType.ACCEPT, {})
            )
            self._state = FileTransferState.RECEIVING

    def _handle_done(self) -> None:
        """Verify the reassembled file and send ACK (match) or NACK (mismatch)."""
        actual_sha = sha256_hex(bytes(self._buffer))
        if actual_sha == self._expected_sha:
            self._verified = True
            self.session.send_on(
                self.stream_id,
                encode_control(FileFrameType.ACK, {"ok": True}),
            )
            self._state = FileTransferState.COMPLETE
        else:
            self._verified = False
            self.session.send_on(
                self.stream_id,
                encode_control(FileFrameType.NACK, {"ok": False}),
            )
            self._state = FileTransferState.FAILED
