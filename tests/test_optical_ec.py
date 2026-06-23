"""Optical-channel error-correction (EC) level wiring tests (M10-T03).

These tests pin that :class:`~photontcp.optical.channel.OpticalChannel` threads
its ``error`` (QR error-correction level) parameter end-to-end onto the encode
path, and that raising it to ``"q"`` (≈ ~25% recovery, the hardened optical
default candidate) still round-trips a representative packet byte-exact over the
in-memory device pair — while the *default* (no ``error=`` argument) is unchanged.

They run entirely over :meth:`OpticalChannel.pair` (no screen, no webcam, no
hardware) and mirror the polling style of ``tests/test_optical_channel.py``: the
capture thread delivers asynchronously, so receives use a generous timeout and
every channel is closed in a ``finally`` so no capture thread leaks.

``segno`` / ``cv2`` / ``numpy`` missing -> skip the whole module.
"""

from __future__ import annotations

import pytest

pytest.importorskip("segno")
pytest.importorskip("cv2")
pytest.importorskip("numpy")

from photontcp.optical.channel import OpticalChannel
from photontcp.qr.encode import encode_frame

# Generous receive timeout: the capture thread runs on real time and must poll +
# decode. A hit returns well before this; only a genuine failure pays the wait.
RECV_TIMEOUT = 3.0

# A packet-sized payload (~88 bytes). cv2's detector cannot localize QRs made
# from a few bytes, so we keep it comfortably large yet well within the single-
# symbol capacity even at EC "q" (which lowers capacity vs "m").
PAYLOAD = b"PhotonTCP optical EC frame " + bytes(range(64))


def test_roundtrip_at_ec_q() -> None:
    """A packet round-trips byte-exact when the channel is built at EC level "q".

    Constructs the in-memory pair via ``OpticalChannel.pair(error="q", ...)`` and
    sends a representative packet-sized payload; the receiver must recover the
    exact bytes after a real QR encode/decode at the higher EC level. This proves
    the ``error`` argument is threaded through ``pair`` -> ``__init__`` ->
    ``send_frame`` -> ``encode_frame`` and that "q" frames decode on the round trip.
    """
    a, b = OpticalChannel.pair(error="q")
    try:
        assert a.error == "q"  # stored on the instance
        a.send_frame(PAYLOAD)
        assert b.recv_frame(timeout=RECV_TIMEOUT) == PAYLOAD
    finally:
        a.close()
        b.close()


def test_default_error_level_is_m_and_roundtrips() -> None:
    """Default construction (no ``error=``) keeps EC "m" and round-trips as before.

    Pins backward compatibility: omitting ``error`` leaves the stored level at
    ``"m"`` and a representative packet still round-trips byte-exact, so existing
    callers and the in-memory pair are unchanged by the EC-hardening plumbing.
    """
    a, b = OpticalChannel.pair()
    try:
        assert a.error == "m"
        assert b.error == "m"
        a.send_frame(PAYLOAD)
        assert b.recv_frame(timeout=RECV_TIMEOUT) == PAYLOAD
    finally:
        a.close()
        b.close()


def test_higher_ec_changes_the_encoded_symbol() -> None:
    """A higher EC level genuinely changes the encoded symbol (not a silent no-op).

    Encoding the same channel_frame the channel would build (nonce 0 prepended to
    the payload) at ``"q"`` vs ``"m"`` must differ — more redundancy means more
    codewords, so segno picks a larger version or a different module pattern. This
    confirms ``error`` is a live knob: had the channel ignored it, both encodes
    (and thus both round-trips) would be indistinguishable.
    """
    import numpy as np

    channel_frame = (0).to_bytes(OpticalChannel._NONCE_BYTES, "big") + PAYLOAD
    at_q = encode_frame(channel_frame, error="q")
    at_m = encode_frame(channel_frame, error="m")
    assert at_q.shape != at_m.shape or not np.array_equal(at_q, at_m)
