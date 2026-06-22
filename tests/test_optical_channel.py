"""Deterministic in-memory tests for :class:`OpticalChannel` (M8-T05).

These tests drive :class:`~photontcp.optical.channel.OpticalChannel` over its
in-memory fake device pair (:meth:`OpticalChannel.pair`) -- no screen, no webcam,
no ``cv2`` hardware. They pin the channel's *logic*: byte-exact frame round-trip
over a real QR encode/decode, re-capture de-duplication (the camera keeps
re-returning the still-displayed QR yet a frame is delivered exactly once),
consecutive-identical-packet preservation (the rolling nonce keeps a legitimate
ARQ retransmission from being swallowed), idempotent/clean ``close()``, and that
the *existing* :class:`~photontcp.session.Session` runs unchanged on top of the
optical channel (channel swap only -- upper layers untouched).

Unlike the loopback channels, ``OpticalChannel`` receives on a **real background
capture thread** running on wall-clock time, so delivery is asynchronous. The
tests therefore poll with generous timeouts (a couple of seconds) rather than
assuming instant delivery; the payloads are tiny so they still finish fast. Every
channel is closed in a ``finally`` so no capture thread leaks.

Note on payload size: OpenCV's ``QRCodeDetector`` cannot localize a QR generated
from a *tiny* payload (e.g. ``b"hello"``), so all round-trip assertions use
packet-sized payloads (~60+ bytes), matching the sizes ``tests/test_qr.py`` shows
round-tripping reliably through ``encode_frame`` / ``decode_frame``.

``segno`` / ``cv2`` / ``numpy`` missing -> skip the whole module.
"""

from __future__ import annotations

import time

import pytest

pytest.importorskip("segno")
pytest.importorskip("cv2")
pytest.importorskip("numpy")

from photontcp.optical.channel import OpticalChannel
from photontcp.session import (
    ManualClock,
    Session,
    SessionState,
)

# A generous receive timeout: the capture thread runs on real time, so we must
# wait for it to poll + decode. The data is tiny, so a hit returns well before
# this; only a genuine failure ever pays the full wait.
RECV_TIMEOUT = 3.0

# Packet-sized payloads. cv2's detector fails to localize QRs made from a few
# bytes, so we keep every round-tripped payload comfortably large (~88 bytes).
PAYLOAD_A = b"PhotonTCP optical frame A " + bytes(range(64))
PAYLOAD_B = b"PhotonTCP optical frame B " + bytes(range(64, 128))


def _recv_until(chan: OpticalChannel, timeout: float = RECV_TIMEOUT) -> bytes | None:
    """Receive one packet, blocking up to ``timeout`` for the capture thread.

    ``recv_frame`` already blocks on the inbox queue up to ``timeout``; this thin
    wrapper just names the polling intent at the call sites.
    """
    return chan.recv_frame(timeout=timeout)


# --------------------------------------------------------------------------- #
# 1. Round-trip both directions (completion criterion 2).
# --------------------------------------------------------------------------- #


def test_roundtrip_both_directions() -> None:
    """A frame sent by either endpoint is recovered byte-exact by the other.

    Pins the full-duplex round-trip: ``a -> b`` and ``b -> a`` each survive a real
    QR encode/decode (with the channel's nonce prefix stripped) and come back as
    the exact bytes that were sent.
    """
    a, b = OpticalChannel.pair()
    try:
        # a -> b
        a.send_frame(PAYLOAD_A)
        assert _recv_until(b) == PAYLOAD_A

        # b -> a (reverse direction over the cross-wired duplex link)
        b.send_frame(PAYLOAD_B)
        assert _recv_until(a) == PAYLOAD_B
    finally:
        a.close()
        b.close()


# --------------------------------------------------------------------------- #
# 2. Re-capture de-duplication (completion criterion 3).
# --------------------------------------------------------------------------- #


def test_recapture_deduped_to_single_delivery() -> None:
    """One displayed frame is delivered exactly once despite repeated capture.

    The memory camera (``repeat_last=True``) keeps re-returning the *same* still
    QR while the display does not advance. The channel's byte-exact nonce de-dup
    must collapse those re-captures: the first ``recv_frame`` yields the packet,
    and a second ``recv_frame`` finds nothing (returns ``None``) even though the
    camera is still re-seeing the identical displayed QR.
    """
    a, b = OpticalChannel.pair()
    try:
        a.send_frame(PAYLOAD_A)

        # First receive: the packet is delivered once.
        assert _recv_until(b) == PAYLOAD_A

        # Second receive: the still-displayed QR is being re-captured repeatedly,
        # but de-dup drops every repeat, so nothing new arrives. A short timeout
        # is enough -- if a duplicate were going to leak it would already be here.
        assert b.recv_frame(timeout=0.5) is None
    finally:
        a.close()
        b.close()


# --------------------------------------------------------------------------- #
# 3. Consecutive identical packets preserved (completion criterion 4).
# --------------------------------------------------------------------------- #


def test_consecutive_identical_packets_both_delivered() -> None:
    """Sending the same bytes twice delivers it twice (ARQ retransmit survives).

    Two identical packets carry *different* rolling nonces, so their displayed
    QRs differ byte-for-byte and the de-dup does not swallow the second. This pins
    that a legitimate ARQ retransmission of an identical packet is still delivered.
    """
    a, b = OpticalChannel.pair()
    try:
        a.send_frame(PAYLOAD_A)
        first = _recv_until(b)

        a.send_frame(PAYLOAD_A)  # same bytes again -> different nonce on the wire
        second = _recv_until(b)

        assert first == PAYLOAD_A
        assert second == PAYLOAD_A
    finally:
        a.close()
        b.close()


# --------------------------------------------------------------------------- #
# 4. Close: idempotent, send no-op, recv None, thread joined (criterion 7).
# --------------------------------------------------------------------------- #


def test_close_is_idempotent_and_quiesces_channel() -> None:
    """After ``close()`` the channel is inert and the capture thread is gone.

    Pins teardown semantics: ``close()`` joins the capture thread cleanly; a
    second ``close()`` is harmless (idempotent); a post-close ``send_frame`` is a
    silent no-op (no raise) and ``recv_frame`` returns ``None`` promptly.
    """
    a, b = OpticalChannel.pair()
    try:
        # Force the capture thread to spin up so close() actually has a thread to
        # join (recv_frame lazily starts it).
        a.send_frame(PAYLOAD_A)
        assert _recv_until(b) == PAYLOAD_A
        assert b._capture_thread is not None and b._capture_thread.is_alive()

        b.close()

        # The capture thread must be joined (not left alive) after close.
        assert b._capture_thread is not None
        assert not b._capture_thread.is_alive()

        # close() is idempotent: a second call is harmless.
        b.close()

        # Post-close send is a silent no-op (must not raise) ...
        b.send_frame(PAYLOAD_B)
        # ... and recv returns None promptly without blocking for the full window.
        assert b.recv_frame(timeout=0.1) is None
    finally:
        a.close()
        b.close()


# --------------------------------------------------------------------------- #
# 5. Session integration: existing Session runs unchanged over the channel
#    (completion criterion 5).
# --------------------------------------------------------------------------- #

# Hard cap on every pump-progression loop so a stuck handshake/transfer fails
# fast instead of hanging the suite.
MAX_ROUNDS = 200

# Small real sleep per pump round: the capture thread delivers asynchronously,
# while Session.pump drains with recv_frame(timeout=0). This gives the thread a
# moment to decode and enqueue each frame before the next pump polls.
ROUND_SLEEP = 0.02


def _pump_both_until(a: Session, b: Session, pred) -> None:
    """Pump both sessions (with a per-round real sleep) until ``pred()`` is true.

    The sleep lets the optical channel's real capture thread deliver frames
    between synchronous pumps. ``pytest.fail`` if ``pred`` never holds within the
    round cap.
    """
    for _ in range(MAX_ROUNDS):
        if pred():
            return
        a.pump()
        b.pump()
        # Let the capture threads on both endpoints decode + enqueue any frames
        # the pumps just displayed before the next round drains them.
        time.sleep(ROUND_SLEEP)
    pytest.fail(
        f"progression did not converge within {MAX_ROUNDS} rounds "
        f"(a={a.state}, b={b.state})"
    )


def test_session_handshake_and_data_over_optical_channel() -> None:
    """The existing Session establishes + exchanges data over OpticalChannel.

    Proves the channel swap is upper-layer-transparent: a minimal raw ``Session``
    handshake reaches ESTABLISHED on both sides and a short bidirectional data
    exchange round-trips intact -- all with the session, reliability and codec
    layers entirely unmodified, only the channel replaced by ``OpticalChannel``.
    A shared :class:`ManualClock` (never advanced) drives session timing so no
    spurious heartbeats fire while the channel delivers asynchronously.
    """
    clock = ManualClock()
    chan_a, chan_b = OpticalChannel.pair()
    a = Session(chan_a, clock, is_initiator=True, session_id=1, isn=1000)
    b = Session(chan_b, clock, is_initiator=False, session_id=0, isn=5000)
    try:
        # --- Handshake to ESTABLISHED on both sides. -----------------------
        a.connect()
        _pump_both_until(a, b, lambda: a.is_established and b.is_established)
        assert a.state is SessionState.ESTABLISHED
        assert b.state is SessionState.ESTABLISHED

        # --- Short bidirectional data exchange (tiny, but packet-sized). ----
        a_to_b = b"optical-a->b:" + bytes(range(60))
        b_to_a = b"optical-b->a:" + bytes(range(60, 120))
        a.send(a_to_b)
        b.send(b_to_a)

        got_b = bytearray()
        got_a = bytearray()

        def _drained() -> bool:
            for chunk in b.recv():
                got_b.extend(chunk)
            for chunk in a.recv():
                got_a.extend(chunk)
            return bytes(got_b) == a_to_b and bytes(got_a) == b_to_a

        _pump_both_until(a, b, _drained)

        assert bytes(got_b) == a_to_b
        assert bytes(got_a) == b_to_a

        # --- Graceful close: one close() drives both peers to CLOSED. -------
        a.close()
        _pump_both_until(a, b, lambda: a.is_closed and b.is_closed)
        assert a.state is SessionState.CLOSED
        assert b.state is SessionState.CLOSED
    finally:
        # Session.close() only sends a FIN -- it does not close the channel, so we
        # must close the channels directly to stop their capture threads.
        try:
            chan_a.close()
        finally:
            chan_b.close()
