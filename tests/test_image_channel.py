"""Full-stack integration tests over the real QR :class:`ImageLoopbackChannel`.

These tests (M5-T05) verify that the *existing* PhotonTCP stack (packet codec,
session handshake/teardown, ARQ reliable data path, and the chat application)
runs **unmodified** on top of :class:`ImageLoopbackChannel` -- a channel that
actually encodes every frame into a QR-code image and decodes it back on the
receiving side via segno + OpenCV.

Everything is deterministic: a single seeded RNG (``pair(seed=...)``) drives the
channel and a :class:`ManualClock` drives time (no real ``sleep``). The scenarios
are lossless (``loss=0.0``): the goal here is to exercise the real QR encode /
decode round-trip end to end, not to stress ARQ retransmission (which the
loopback ARQ tests already cover).

Because each frame pays the cost of a QR encode plus a cv2 decode, payloads are
deliberately small (tens to a couple hundred bytes) and message counts are low so
the suite finishes in a few seconds. Every progression loop is bounded by a
finite iteration cap and fails fast (``pytest.fail``) if it does not converge.
"""

from __future__ import annotations

import pytest

# QR-dependent: skip cleanly when the optical libraries are not installed.
pytest.importorskip("segno")
pytest.importorskip("cv2")

from photontcp.app.chat import ChatSession
from photontcp.channel import ImageLoopbackChannel
from photontcp.packet.header import HEADER_SIZE, Packet
from photontcp.packet.types import Flags, PacketType
from photontcp.session import (
    ManualClock,
    Session,
    SessionEvent,
    SessionState,
)

# Finite hard cap on every progression loop so a stuck handshake/teardown/data
# transfer fails fast instead of hanging the suite. Kept modest because the QR
# round-trip per pump is comparatively expensive.
MAX_ITERS = 200


def _make_session_pair(
    *,
    seed: int = 0,
    a_session_id: int = 1,
    b_session_id: int = 1,
    a_isn: int = 1000,
    b_isn: int = 5000,
):
    """Create two cross-wired sessions over a lossless QR image channel.

    Returns ``(a, b, clock)`` where ``a`` is the initiator and ``b`` the passive
    responder. Both share one :class:`ManualClock`; since virtual time never
    advances during these scenarios, ``now - last_send`` stays at ``0`` so no
    spurious heartbeats interfere with the handshake or teardown.
    """
    ch_a, ch_b = ImageLoopbackChannel.pair(seed=seed, loss=0.0, dup=0.0)
    clock = ManualClock()
    a = Session(
        ch_a, clock, is_initiator=True, session_id=a_session_id, isn=a_isn
    )
    b = Session(
        ch_b, clock, is_initiator=False, session_id=b_session_id, isn=b_isn
    )
    return a, b, clock


def _pump_both_until(a, b, pred, *, max_iters=MAX_ITERS):
    """Alternately pump ``a`` then ``b`` until ``pred()`` or the cap is hit.

    Returns ``(events_a, events_b)``. ``pytest.fail`` if the predicate never
    becomes true within ``max_iters`` iterations.
    """
    events_a: list[SessionEvent] = []
    events_b: list[SessionEvent] = []
    for _ in range(max_iters):
        if pred():
            break
        events_a.extend(a.pump())
        events_b.extend(b.pump())
    else:
        pytest.fail(
            f"progression did not converge within {max_iters} iterations "
            f"(a={a.state}, b={b.state})"
        )
    return events_a, events_b


def _establish(a, b):
    """Drive the 3-way handshake to completion; return ``(events_a, events_b)``."""
    a.connect()
    return _pump_both_until(
        a, b, lambda: a.is_established and b.is_established
    )


# --------------------------------------------------------------------------- #
# (a) Raw frame round-trip: Packet.pack() bytes survive the QR encode/decode.
# --------------------------------------------------------------------------- #


def test_frame_roundtrip_through_qr_image():
    """A packed packet (>= 22B) sent through a QR image is recovered verbatim."""
    ch_a, ch_b = ImageLoopbackChannel.pair(seed=1, loss=0.0, dup=0.0)

    pkt = Packet(
        type=PacketType.DATA,
        session_id=4242,
        stream_id=1,
        seq=7,
        ack=3,
        window=32,
        flags=Flags.ACK,
        payload=b"photon-qr-roundtrip-payload",
    )
    raw = pkt.pack()
    assert len(raw) >= HEADER_SIZE  # frame is at least a full header.

    ch_a.send_frame(raw)
    received = ch_b.recv_frame(timeout=1.0)

    # The QR encode + cv2 decode must reproduce the exact bytes.
    assert received == raw

    # And the recovered bytes still parse + pass the CRC check, preserving fields.
    out = Packet.unpack(received)
    assert out.type is PacketType.DATA
    assert out.session_id == 4242
    assert out.stream_id == 1
    assert out.seq == 7
    assert out.ack == 3
    assert out.window == 32
    assert out.flags is Flags.ACK
    assert out.payload == b"photon-qr-roundtrip-payload"

    ch_a.close()
    ch_b.close()


def test_empty_payload_frame_roundtrip_through_qr_image():
    """A header-only (empty payload) frame also survives the QR round-trip."""
    ch_a, ch_b = ImageLoopbackChannel.pair(seed=2, loss=0.0, dup=0.0)

    pkt = Packet(
        type=PacketType.SYN,
        session_id=1,
        stream_id=0,
        seq=100,
        ack=0,
        window=16,
    )
    raw = pkt.pack()
    assert len(raw) == HEADER_SIZE  # no payload -> exactly the 22B header.

    ch_a.send_frame(raw)
    received = ch_b.recv_frame(timeout=1.0)

    assert received == raw
    assert Packet.unpack(received).type is PacketType.SYN

    ch_a.close()
    ch_b.close()


# --------------------------------------------------------------------------- #
# (b) Session full-stack: handshake establish + graceful close over QR images.
# --------------------------------------------------------------------------- #


def test_session_handshake_and_close_over_qr_channel():
    """Two sessions establish, then a single close tears both down -- via QR."""
    a, b, _clock = _make_session_pair(seed=10)

    events_a, events_b = _establish(a, b)
    assert a.is_established and b.is_established
    assert a.state is SessionState.ESTABLISHED
    assert b.state is SessionState.ESTABLISHED
    assert SessionEvent.ESTABLISHED in events_a
    assert SessionEvent.ESTABLISHED in events_b

    # Active close: symmetric auto-close must drive both peers to CLOSED.
    close_events_a = a.close()
    pumped_a, events_b = _pump_both_until(
        a, b, lambda: a.is_closed and b.is_closed
    )
    events_a = close_events_a + pumped_a

    assert a.is_closed and b.is_closed
    assert a.state is SessionState.CLOSED
    assert b.state is SessionState.CLOSED
    assert SessionEvent.CLOSED in events_a
    assert SessionEvent.PEER_CLOSED in events_b
    assert SessionEvent.CLOSED in events_b


# --------------------------------------------------------------------------- #
# (c) Reliable data path: ARQ delivers application bytes in order over QR.
# --------------------------------------------------------------------------- #


def test_reliable_data_transfer_over_qr_channel():
    """ESTABLISHED bytes are delivered in order, intact, over the QR channel."""
    a, b, _clock = _make_session_pair(seed=20)
    _establish(a, b)

    # Small payload (~150B): one or two DATA packets given the default 200B
    # ARQ chunk size, keeping the QR encode/decode cost low.
    data = bytes((i * 37 + 11) % 256 for i in range(150))
    a.send(data)

    # Drain loop: pump both sides until the receiver has reassembled the bytes.
    received = bytearray()
    for _ in range(MAX_ITERS):
        a.pump()
        b.pump()
        for chunk in b.recv():
            received.extend(chunk)
        if bytes(received) == data:
            break
    else:
        pytest.fail(
            f"reliable transfer did not complete within {MAX_ITERS} iterations "
            f"(got {len(received)}/{len(data)} bytes)"
        )

    assert bytes(received) == data


def test_bidirectional_reliable_data_over_qr_channel():
    """Both directions deliver distinct byte streams intact over the QR channel."""
    a, b, _clock = _make_session_pair(seed=21)
    _establish(a, b)

    a_to_b = b"hello-from-a:" + bytes(range(80))
    b_to_a = b"hello-from-b:" + bytes(range(80, 160))
    a.send(a_to_b)
    b.send(b_to_a)

    got_b = bytearray()
    got_a = bytearray()
    for _ in range(MAX_ITERS):
        a.pump()
        b.pump()
        for chunk in b.recv():
            got_b.extend(chunk)
        for chunk in a.recv():
            got_a.extend(chunk)
        if bytes(got_b) == a_to_b and bytes(got_a) == b_to_a:
            break
    else:
        pytest.fail(
            "bidirectional transfer did not complete: "
            f"a->b {len(got_b)}/{len(a_to_b)}, b->a {len(got_a)}/{len(b_to_a)}"
        )

    assert bytes(got_b) == a_to_b
    assert bytes(got_a) == b_to_a


# --------------------------------------------------------------------------- #
# (d) ChatSession application messages exchanged over the QR channel.
# --------------------------------------------------------------------------- #


def test_chat_messages_over_qr_channel():
    """A short bidirectional chat exchange works on top of the QR channel."""
    a, b, clock = _make_session_pair(seed=30)
    _establish(a, b)

    chat_a = ChatSession(a, clock)
    chat_b = ChatSession(b, clock)

    chat_a.send_message("hi from a")
    chat_b.send_message("hello from b")

    for _ in range(MAX_ITERS):
        chat_a.pump()
        chat_b.pump()
        if chat_b.received and chat_a.received:
            break
    else:
        pytest.fail(
            "chat exchange did not converge: "
            f"a received {len(chat_a.received)}, b received {len(chat_b.received)}"
        )

    assert [m.text for m in chat_b.received] == ["hi from a"]
    assert [m.text for m in chat_a.received] == ["hello from b"]
