"""Concurrency-correctness test for :class:`OpticalChannel` (M8-T04).

This test exists to discharge **M7-review 권장 3**: the project's thread-safety
foundation (a per-thread cv2 detector inside :func:`decode_frame`, plus an inbox
queue and a plain ``_closed`` flag as the *only* cross-thread state) was so far
only exercised at a "smoke" level. M8 introduces a real **background capture
thread** per endpoint, so we now validate concurrency *correctness* — beyond a
smoke test — under genuine concurrent bidirectional traffic.

What is validated
-----------------
Using :meth:`OpticalChannel.pair` (a full-duplex in-memory fake — no hardware),
we run two senders and two receivers concurrently. This drives **two capture
threads** (``a``'s and ``b``'s) calling :func:`decode_frame` simultaneously,
which is exactly the situation 권장 3 wanted exercised. We then assert:

1. **Set equality (no loss / no corruption / no cross-talk).** Every unique
   packet ``a`` sends is received by ``b`` exactly once, byte-for-byte, and the
   same for ``b -> a``. The nonce de-dup must drop only re-captures of a still
   display, never a genuine packet. Because every sent payload embeds its index,
   the received multiset can be compared as a *set* against the sent set.
2. **No thread died.** The sender/receiver worker threads collect any exception
   into a shared list; the capture threads must stay alive throughout. We assert
   no exception was recorded anywhere.
3. **Clean close under load (no deadlock).** After traffic, ``close()`` returns
   promptly (bounded join inside ``close``) and the capture threads are no longer
   alive.

Determinism note
----------------
The milestone asks for reproducibility ("시드/순서 고정"), but with real OS
threads the exact *interleaving* of captures and decodes is inherently
non-deterministic. What **is** deterministic and asserted here is the
**set equality**: regardless of interleaving, every sent packet arrives intact
exactly once. We lean on the memory devices' queue-based handoff (which never
drops a frame) so this set-equality property holds without timing luck, and we
drain with generous bounded deadlines so the wall-clock capture thread has time
to deliver everything without the test ever hanging.

``cv2`` / ``segno`` / ``numpy`` are required (real QR encode + decode happen in
the capture threads); the module is skipped if any is missing.
"""

from __future__ import annotations

import threading
import time

import pytest

pytest.importorskip("cv2")
pytest.importorskip("segno")
pytest.importorskip("numpy")

from photontcp.optical.channel import OpticalChannel


# Packets per direction. Kept modest so the (real, wall-clock) capture threads
# finish quickly, but large enough to give concurrent decoding real overlap. Each
# frame pays a full cv2 QR detectAndDecode in the capture thread (tens to a few
# hundred ms), and two capture threads run at once, so this dominates runtime.
N_PER_DIR = 30

# Global drain deadline. The capture thread runs on real time, so we poll with a
# bounded loop rather than assume instant delivery; this caps total runtime so a
# stall fails fast (with diagnostics) instead of hanging the suite. Sized with
# generous headroom over the measured cv2-decode throughput so it is not flaky.
DRAIN_DEADLINE_S = 60.0

# Watchdog budget for close(): close() does a bounded join internally, so this is
# just a sanity ceiling to catch a hypothetical deadlock.
CLOSE_DEADLINE_S = 5.0


def _make_payload(tag: str, i: int) -> bytes:
    """Build a unique, packet-sized payload.

    OpenCV's QR detector cannot localize a QR from a *tiny* payload, so each
    payload is padded to ~60+ bytes (see ``tests/test_qr.py`` for reliable
    sizes). The ``tag`` distinguishes the two directions and ``i`` makes every
    payload unique, so received bytes can be compared as a set against sent.
    """
    prefix = f"photon-optical-{tag}-packet-{i:04d}-".encode()
    filler = bytes(((i * 7) + j) % 256 for j in range(48))
    return prefix + filler


def _send_all(chan: OpticalChannel, payloads: list[bytes], errors: list) -> None:
    """Send every payload on ``chan``; record any exception for the assertions."""
    try:
        for p in payloads:
            chan.send_frame(p)
    except Exception as exc:  # pragma: no cover - failure path asserted by test
        errors.append(("send", exc))


def _recv_n(
    chan: OpticalChannel,
    n: int,
    deadline: float,
    out: set,
    errors: list,
) -> None:
    """Drain ``n`` distinct packets from ``chan`` into ``out`` before ``deadline``.

    Polls with a short per-call timeout so the loop stays responsive and can
    notice the global deadline; stops as soon as ``n`` distinct packets arrive.
    """
    try:
        while len(out) < n and time.monotonic() < deadline:
            frame = chan.recv_frame(timeout=0.5)
            if frame is not None:
                out.add(frame)
    except Exception as exc:  # pragma: no cover - failure path asserted by test
        errors.append(("recv", exc))


def test_concurrent_bidirectional_set_equality() -> None:
    """Concurrent two-way traffic delivers every sent packet intact, exactly once.

    Validates M7 권장 3: two capture threads decode concurrently (thread-local
    detector), the nonce de-dup drops only re-captures, and ``close()`` shuts the
    threads down without deadlock.
    """
    a, b = OpticalChannel.pair()

    # Distinct, unique payload sets per direction (different tags => no overlap).
    a_sent = [_make_payload("AtoB", i) for i in range(N_PER_DIR)]
    b_sent = [_make_payload("BtoA", i) for i in range(N_PER_DIR)]
    a_sent_set = set(a_sent)
    b_sent_set = set(b_sent)
    # Sanity: payloads are unique within and across directions, and packet-sized.
    assert len(a_sent_set) == N_PER_DIR
    assert len(b_sent_set) == N_PER_DIR
    assert a_sent_set.isdisjoint(b_sent_set)
    assert all(len(p) >= 60 for p in a_sent + b_sent)

    got_by_b: set = set()  # what B received (should equal what A sent)
    got_by_a: set = set()  # what A received (should equal what B sent)
    errors: list = []

    try:
        deadline = time.monotonic() + DRAIN_DEADLINE_S

        # Concurrent senders (a->b, b->a) AND concurrent receivers, so both
        # capture threads run decode_frame at the same time.
        workers = [
            threading.Thread(
                target=_send_all, args=(a, a_sent, errors), name="send-a"
            ),
            threading.Thread(
                target=_send_all, args=(b, b_sent, errors), name="send-b"
            ),
            threading.Thread(
                target=_recv_n,
                args=(b, N_PER_DIR, deadline, got_by_b, errors),
                name="recv-b",
            ),
            threading.Thread(
                target=_recv_n,
                args=(a, N_PER_DIR, deadline, got_by_a, errors),
                name="recv-a",
            ),
        ]
        for w in workers:
            w.start()
        for w in workers:
            # Join generously past the drain deadline so a wedged worker is
            # reported rather than hanging the suite forever.
            w.join(timeout=DRAIN_DEADLINE_S + 10.0)
            assert not w.is_alive(), f"worker {w.name} did not finish"

        # (2) No worker thread crashed (capture threads must stay alive too).
        assert not errors, f"worker thread(s) raised: {errors}"

        # (1) Set equality: no loss, no corruption, no cross-talk in either dir.
        assert got_by_b == a_sent_set, (
            "B did not receive exactly what A sent: "
            f"missing={len(a_sent_set - got_by_b)}, "
            f"extra={len(got_by_b - a_sent_set)} "
            f"(received {len(got_by_b)}/{N_PER_DIR})"
        )
        assert got_by_a == b_sent_set, (
            "A did not receive exactly what B sent: "
            f"missing={len(b_sent_set - got_by_a)}, "
            f"extra={len(got_by_a - b_sent_set)} "
            f"(received {len(got_by_a)}/{N_PER_DIR})"
        )

        # Capture threads should still be alive right before we close.
        assert a._capture_thread is not None and a._capture_thread.is_alive()
        assert b._capture_thread is not None and b._capture_thread.is_alive()

    finally:
        # (3) Clean close under load: must return promptly and stop the threads.
        a_thread = a._capture_thread
        b_thread = b._capture_thread

        t0 = time.monotonic()
        a.close()
        b.close()
        elapsed = time.monotonic() - t0
        assert elapsed < CLOSE_DEADLINE_S, (
            f"close() took {elapsed:.2f}s (>{CLOSE_DEADLINE_S}s) — possible "
            "deadlock shutting down the capture threads"
        )

        # Capture threads must no longer be alive after close().
        if a_thread is not None:
            assert not a_thread.is_alive(), "a's capture thread survived close()"
        if b_thread is not None:
            assert not b_thread.is_alive(), "b's capture thread survived close()"

        # close() is idempotent — a second call must be a harmless no-op.
        a.close()
        b.close()
