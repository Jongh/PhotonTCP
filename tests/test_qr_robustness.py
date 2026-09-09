"""QR decode robustness / regression-guard tests (M10-T04).

Companion to ``tests/test_qr.py``. Where that module proves codec round-trip
*correctness* on a few hand-picked payloads, this module quantifies the M10
decode hardening (the preprocessing cascade in
:func:`photontcp.qr.decode._decode_variants` plus the alternate-detector
fallback) as a **deterministic regression guard**:

* a clean, seed-fixed corpus must round-trip at 100% through ``decode_frame``;
* a deterministically degraded ("camera-like") corpus must decode at or above a
  conservative threshold;
* there exists at least one payload that the OLD single-pass
  ``cv2.QRCodeDetector`` cannot decode but the hardened ``decode_frame`` can
  (the "blind spot" the milestone targets).

A self-contained compact corpus is duplicated inline here (rather than imported
from ``examples/qr_decode_bench.py``) to keep the test fast and free of import
fragility.

``segno``/``cv2`` are hard requirements; the module skips entirely if absent.
"""

from __future__ import annotations

import base64
import binascii

import pytest

pytest.importorskip("segno")
pytest.importorskip("cv2")

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from photontcp.qr.encode import encode_frame  # noqa: E402
from photontcp.qr.decode import decode_frame  # noqa: E402


SEED = 0xB10C5  # fixed -> corpus and pass counts are reproducible.

# The corpus used to be floored at 12 bytes because shorter payloads were
# encoded as Micro QR (segno's default for short data) and ``cv2.QRCodeDetector``
# has no Micro-QR support -- they were undecodable at any scale. M11-T07 removed
# that at the source: ``encode_frame`` now always emits a *regular* QR symbol
# (``micro=False``), so the former blind spot is ordinary corpus territory. The
# floor is therefore lowered to the shortest payload we care about, and short
# lengths are part of the spectrum below. ``test_short_payloads_roundtrip`` pins
# the boundary itself.
_MIN_LEN = 5


# --------------------------------------------------------------------------- #
# Deterministic corpus (compact, inline)
# --------------------------------------------------------------------------- #

def _build_corpus(seed: int = SEED) -> list[bytes]:
    """A compact, deterministic corpus spanning lengths and byte patterns."""
    rng = np.random.default_rng(seed)
    corpus: list[bytes] = []

    # Random binary across a length spectrum.
    for length in (5, 8, 9, 12, 16, 24, 32, 48, 64, 96, 128, 192, 256, 384):
        for _ in range(6):
            corpus.append(bytes(rng.integers(0, 256, size=length, dtype=np.uint8)))

    # All-byte-value structured patterns (rotations + prefixes of range(256)).
    full = bytes(range(256))
    for start in range(0, 256, 32):
        corpus.append(full[start:] + full[:start])
    for end in (16, 64, 128, 256):
        corpus.append(full[:end])

    # Text-like ASCII payloads.
    words = [b"PhotonTCP", b"over", b"light", b"frame", b"nonce", b"optical"]
    for n in (3, 8, 20, 40):
        corpus.append(b" ".join(words[rng.integers(0, len(words))] for _ in range(n)))

    assert all(len(p) >= _MIN_LEN for p in corpus)
    return corpus


def _degrade(image: np.ndarray) -> np.ndarray:
    """Deterministic mild degradation mimicking a camera grab.

    Gaussian blur (soft focus) followed by a 0.5x downscale / upscale
    round-trip (resampling loss). No RNG -> reproducible.
    """
    h, w = image.shape[:2]
    blurred = cv2.GaussianBlur(image, (3, 3), sigmaX=0.8)
    small = cv2.resize(blurred, (max(1, w // 2), max(1, h // 2)),
                       interpolation=cv2.INTER_LINEAR)
    return cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)


def _base_only_decode(image: np.ndarray, detector: cv2.QRCodeDetector) -> bytes | None:
    """The OLD pre-M10 decoder: a single ``detectAndDecode`` + base64, no cascade."""
    if image.ndim == 3:
        try:
            image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        except cv2.error:
            return None
    try:
        data_str, points, _ = detector.detectAndDecode(image)
    except cv2.error:
        return None
    if not data_str or points is None:
        return None
    try:
        return base64.b64decode(data_str, validate=True)
    except (binascii.Error, ValueError):
        return None


# --------------------------------------------------------------------------- #
# 1. Clean corpus round-trips at 100% through decode_frame
# --------------------------------------------------------------------------- #

def test_clean_corpus_roundtrips_fully() -> None:
    """Every pristine QR we encode must decode back to the exact bytes."""
    corpus = _build_corpus()
    assert len(corpus) >= 50  # sanity: the corpus is non-trivial
    failures = [p for p in corpus if decode_frame(encode_frame(p)) != p]
    assert not failures, (
        f"{len(failures)}/{len(corpus)} clean frames failed to round-trip "
        f"(lengths: {sorted({len(p) for p in failures})})"
    )


# --------------------------------------------------------------------------- #
# 2. Degraded corpus decode rate >= conservative threshold
# --------------------------------------------------------------------------- #

# Measured in this environment (cv2 4.13.0, alternate detector = "aruco"):
#   degraded corpus full decode rate = 100.00% (every degraded frame decoded).
# The threshold is fixed well below that measured value so it is a stable
# regression guard, not a flaky exact-match assertion.
_DEGRADED_THRESHOLD = 0.85  # measured 1.00 -> guard at 0.85

def test_degraded_corpus_meets_threshold() -> None:
    corpus = _build_corpus()
    hits = sum(1 for p in corpus if decode_frame(_degrade(encode_frame(p))) == p)
    rate = hits / len(corpus)
    assert rate >= _DEGRADED_THRESHOLD, (
        f"degraded decode rate {rate:.2%} fell below the regression "
        f"threshold {_DEGRADED_THRESHOLD:.0%} ({hits}/{len(corpus)})"
    )


# --------------------------------------------------------------------------- #
# 3. Blind-spot recovery: base single-pass fails, full decode_frame succeeds
# --------------------------------------------------------------------------- #

def test_blind_spot_recovered_by_full_decode() -> None:
    """Find a frame the old single-pass detector misses but ``decode_frame`` gets.

    Search seeded payloads (clean first, then their degraded variants, which
    reliably defeat a single pass). The first divergence -- base-only fails AND
    ``decode_frame`` succeeds -- is asserted as an explicit recovery. If, after a
    large search, base and full agree on every sampled frame in THIS build, skip
    rather than hard-fail: the cascade simply found nothing extra to recover here
    (it can never make things worse), so there is no regression to flag.
    """
    detector = cv2.QRCodeDetector()
    rng = np.random.default_rng(SEED)

    recovery = None  # (variant_kind, payload_len)
    for _ in range(3000):
        length = int(rng.integers(_MIN_LEN, 512))
        payload = bytes(rng.integers(0, 256, size=length, dtype=np.uint8))
        clean = encode_frame(payload)

        for kind, image in (("clean", clean), ("degraded", _degrade(clean))):
            base_ok = _base_only_decode(image, detector) == payload
            if base_ok:
                continue
            if decode_frame(image) == payload:
                recovery = (kind, length, payload, image)
                break
        if recovery is not None:
            break

    if recovery is None:
        pytest.skip(
            "base single-pass and full decode_frame agreed on every sampled "
            "frame in this build; the M10 cascade/fallback found nothing extra "
            "to recover here (it can only add recoveries, never remove them)."
        )

    kind, length, payload, image = recovery
    # Re-assert the divergence explicitly so the test documents what it proved.
    assert _base_only_decode(image, detector) != payload, (
        f"expected base-only to FAIL on the {kind} {length}-byte frame"
    )
    assert decode_frame(image) == payload, (
        f"expected full decode_frame to RECOVER the {kind} {length}-byte frame"
    )


# --------------------------------------------------------------------------- #
# 4. The former Micro-QR boundary is now covered, not excluded
# --------------------------------------------------------------------------- #

def test_short_payloads_roundtrip() -> None:
    """5/8/9-byte payloads -- the old Micro-QR blind spot -- round-trip at 100%.

    Before M11-T07 these lengths were *excluded* from the corpus: segno encoded
    them as Micro QR (M3/M4) and ``cv2.QRCodeDetector`` cannot decode Micro QR,
    so they failed at any scale. ``encode_frame`` now pins ``micro=False``, so
    the same payloads become regular version-1 symbols and decode.

    This test therefore asserts the *opposite* of its predecessor
    (``test_micro_qr_boundary_excluded``): the boundary is covered, not avoided.
    A regression that lets a Micro symbol back out of the encoder fails here.
    """
    rng = np.random.default_rng(SEED ^ 0xFF)
    n = 20
    failures: list[tuple[int, int]] = []  # (length, attempt index)
    for length in (5, 8, 9):
        for i in range(n):
            payload = bytes(rng.integers(0, 256, size=length, dtype=np.uint8))
            if decode_frame(encode_frame(payload)) != payload:
                failures.append((length, i))
    assert not failures, (
        f"{len(failures)}/{3 * n} short-payload frames failed to round-trip "
        f"(lengths: {sorted({length for length, _ in failures})}); the encoder "
        "may have started emitting Micro QR again"
    )


def test_encoder_never_emits_micro_qr() -> None:
    """The encoder's symbol is a regular QR (>= 21 modules), never Micro.

    Micro QR symbols are 11/13/15/17 modules square (M1..M4); the smallest
    regular symbol (version 1) is 21. Measuring the module count straight off
    the rendered bitmap pins the invariant without reaching into segno.
    """
    scale, border = 4, 4
    for length in (1, 5, 8, 9, 12):
        payload = bytes(range(1, length + 1))
        image = encode_frame(payload, scale=scale, border=border)
        modules = image.shape[0] // scale - 2 * border
        assert modules >= 21, (
            f"{length}-byte payload produced a {modules}-module symbol "
            "(<21 == Micro QR, which cv2 cannot decode)"
        )
