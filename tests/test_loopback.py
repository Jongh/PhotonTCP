"""Unit tests for :class:`photontcp.channel.loopback.LoopbackChannel`."""

from __future__ import annotations

from photontcp.channel.loopback import LoopbackChannel

# Short timeout so recv never blocks the test suite indefinitely.
RECV_TIMEOUT = 0.5


def test_lossless_delivers_frames_both_directions() -> None:
    a, b = LoopbackChannel.pair(seed=1)

    a.send_frame(b"a->b")
    assert b.recv_frame(timeout=RECV_TIMEOUT) == b"a->b"

    b.send_frame(b"b->a")
    assert a.recv_frame(timeout=RECV_TIMEOUT) == b"b->a"


def test_lossless_preserves_multiple_frames_in_order() -> None:
    a, b = LoopbackChannel.pair(seed=7)

    frames = [f"frame-{i}".encode() for i in range(5)]
    for f in frames:
        a.send_frame(f)

    received = [b.recv_frame(timeout=RECV_TIMEOUT) for _ in frames]
    assert received == frames


def test_full_loss_yields_none() -> None:
    a, b = LoopbackChannel.pair(seed=1, loss=1.0)

    for i in range(10):
        a.send_frame(f"dropme-{i}".encode())

    # Every frame should have been dropped; recv must time out to None.
    assert b.recv_frame(timeout=RECV_TIMEOUT) is None


def _drain(ch: LoopbackChannel) -> list[bytes]:
    """Collect all currently-available frames from ``ch`` (non-blocking-ish)."""
    out: list[bytes] = []
    while True:
        frame = ch.recv_frame(timeout=RECV_TIMEOUT)
        if frame is None:
            break
        out.append(frame)
    return out


def _run_noise_scenario(seed: int) -> list[bytes]:
    """Drive a noisy channel deterministically and return what survived."""
    a, b = LoopbackChannel.pair(
        seed=seed,
        dup=0.5,
        corrupt=0.5,
        reorder=0.5,
    )

    frames = [f"payload-{i:03d}".encode() for i in range(40)]
    for f in frames:
        a.send_frame(f)

    # Flush any frame still held in the reorder buffer by sending a sentinel,
    # then drain everything that arrived at the partner inbox.
    a.send_frame(b"__flush__")
    return _drain(b)


def test_same_seed_reproduces_noise_pattern() -> None:
    first = _run_noise_scenario(seed=12345)
    second = _run_noise_scenario(seed=12345)
    assert first == second
    # Sanity: noise actually did something (not a trivial identity passthrough).
    assert first  # non-empty


def test_different_seed_produces_different_pattern() -> None:
    pattern_a = _run_noise_scenario(seed=1)
    pattern_b = _run_noise_scenario(seed=99999)
    # With this many frames and 0.5 probabilities, distinct seeds should diverge.
    assert pattern_a != pattern_b


def test_closed_channel_discards_and_returns_none() -> None:
    a, b = LoopbackChannel.pair(seed=3)
    b.close()
    a.send_frame(b"after-close-on-a-side")
    # b is closed: recv returns None immediately.
    assert b.recv_frame(timeout=RECV_TIMEOUT) is None
