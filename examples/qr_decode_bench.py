"""Hardware-free, in-memory benchmark of the M10 QR decode hardening.

This script measures how much the M10 decode hardening (the preprocessing
cascade in :func:`photontcp.qr.decode._decode_variants` plus the alternate
detector fallback) improves the QR decode rate over the *old* single-pass
behaviour -- with **no camera and no display**, purely in memory.

For a deterministic, seed-fixed corpus of payloads (spanning lengths and byte
distributions) each payload is encoded once with
:func:`photontcp.qr.encode.encode_frame`, then decoded two ways:

* **base-only** -- a freshly constructed ``cv2.QRCodeDetector`` doing exactly
  ONE ``detectAndDecode`` + ``base64`` decode (this re-creates the pre-M10
  decoder, before the cascade/fallback existed);
* **full** -- :func:`photontcp.qr.decode.decode_frame`, i.e. the hardened
  cascade + alternate-detector fallback.

The same comparison is then run on a **degraded** corpus: each clean QR image
is passed through a deterministic mild degradation (gaussian blur +
downscale/upscale round-trip) to mimic a camera grab. The report prints the
corpus size, the base-only vs full decode rate on each set, and how many frames
the full decoder recovered that the base-only pass missed.

The full decoder's rate is always ``>=`` the base-only rate on each set (the
cascade can only add recoveries, never remove them); the report states this
explicitly.

Run it from the repository root::

    python examples/qr_decode_bench.py

It exits 0 on success and needs no arguments and no hardware. Output is
English-only so it stays readable on any console code page.
"""

from __future__ import annotations

import base64
import binascii
import os
import sys

# Allow running directly from the repository root without installation.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from photontcp.qr.encode import encode_frame  # noqa: E402
from photontcp.qr.decode import decode_frame  # noqa: E402


SEED = 0xC0DEC  # fixed -> the whole corpus and report are reproducible.


# --------------------------------------------------------------------------- #
# Corpus construction (deterministic)
# --------------------------------------------------------------------------- #

def build_corpus(seed: int = SEED) -> list[bytes]:
    """Build a deterministic corpus of payload byte strings.

    The corpus spans a spectrum of lengths and byte distributions so the
    benchmark exercises many distinct QR symbol versions and content patterns
    (content is what triggers cv2's decode blind spot):

    * short / medium / long random binary payloads;
    * slices of ``bytes(range(256))`` (every byte value, structured);
    * text-like ASCII payloads;
    * structured "packet-like" payloads (a small fixed header + body) without
      importing the real packet module -- keeps this script self-contained.

    A few hundred payloads are produced: enough to be statistically meaningful
    while still finishing in a couple of seconds.
    """
    rng = np.random.default_rng(seed)
    corpus: list[bytes] = []

    # Payloads are kept >= 12 bytes on purpose. A base64 string short enough to
    # land in QR version 1 / Micro-QR (~<=9 raw bytes) is NOT decodable by
    # ``cv2.QRCodeDetector`` in this build at *any* scale or preprocessing
    # (cv2 has no Micro-QR support) -- that limitation is orthogonal to the M10
    # hardening (the cascade operates on the same unreadable symbol), so the
    # corpus excludes it to keep the clean round-trip a meaningful 100% target.
    # See tests/test_qr_robustness.py for the boundary measurement.

    # (a) Random binary payloads across a length spectrum. Many short-to-medium
    #     ones (cheap to encode/decode, and where content blind spots show up).
    for length in (12, 16, 24, 32, 48, 64, 96, 128, 192, 256, 384, 512):
        for _ in range(12):
            corpus.append(bytes(rng.integers(0, 256, size=length, dtype=np.uint8)))

    # (b) All-byte-value structured patterns: slices/rotations of range(256).
    full = bytes(range(256))
    for start in range(0, 256, 16):
        corpus.append(full[start:] + full[:start])  # rotated, length 256
    for end in (16, 32, 64, 128, 200, 256):
        corpus.append(full[:end])

    # (c) Text-like ASCII payloads (realistic-ish for chat/file metadata).
    words = [b"PhotonTCP", b"over", b"light", b"frame", b"nonce", b"window",
             b"seq", b"ack", b"crc", b"payload", b"optical", b"camera"]
    for n in (3, 6, 12, 24, 48):  # n>=3 words -> comfortably past the M4 boundary
        chunk = b" ".join(words[rng.integers(0, len(words))] for _ in range(n))
        corpus.append(chunk)

    # (d) "Packet-like" structured payloads: a small fixed-ish header followed
    #     by a random body, packed by hand (no photontcp.packet import needed).
    for body_len in (4, 16, 40, 80, 160, 320):  # header(9)+body >=12 bytes
        for _ in range(6):
            header = bytes([
                1,                                   # version
                int(rng.integers(0, 4)),             # type
                *rng.integers(0, 256, size=2),       # session_id (2B)
                *rng.integers(0, 256, size=2),       # seq (2B)
                *rng.integers(0, 256, size=2),       # ack (2B)
            ])
            body = bytes(rng.integers(0, 256, size=body_len, dtype=np.uint8))
            corpus.append(header + body)

    return corpus


# --------------------------------------------------------------------------- #
# Decoders under comparison
# --------------------------------------------------------------------------- #

def base_only_decode(image: np.ndarray, detector: cv2.QRCodeDetector) -> bytes | None:
    """Emulate the OLD (pre-M10) decoder: ONE detectAndDecode + base64 decode.

    No preprocessing cascade, no alternate-detector fallback -- a single pass on
    the (grayscale-normalized) image, exactly the behaviour ``decode_frame`` had
    before M10. Returns the recovered bytes or ``None``; never raises.
    """
    if image is None:
        return None
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
# Deterministic "capture-like" degradation
# --------------------------------------------------------------------------- #

def degrade(image: np.ndarray) -> np.ndarray:
    """Apply a deterministic mild degradation mimicking a camera grab.

    Two cheap, fully deterministic steps (no RNG, so the degraded corpus is
    reproducible):

    1. a 3x3 gaussian blur (softens module edges, like an out-of-focus grab);
    2. a downscale-to-0.5x then upscale-back round-trip with linear
       interpolation (resampling loss, like a low-resolution capture).

    The result is still the same shape as the input so both decoders see a
    comparable image.
    """
    h, w = image.shape[:2]
    blurred = cv2.GaussianBlur(image, (3, 3), sigmaX=0.8)
    small = cv2.resize(blurred, (max(1, w // 2), max(1, h // 2)),
                       interpolation=cv2.INTER_LINEAR)
    return cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)


# --------------------------------------------------------------------------- #
# Benchmark driver
# --------------------------------------------------------------------------- #

def _rate(hits: int, total: int) -> float:
    return (hits / total) if total else 0.0


def run_set(label: str, images_payloads: list[tuple[np.ndarray, bytes]]) -> dict:
    """Decode every (image, payload) both ways; return a stats dict.

    A decode "succeeds" only if it returns the EXACT original payload bytes
    (round-trip correctness, not merely 'something decoded').
    """
    detector = cv2.QRCodeDetector()  # one shared base-only detector for the set
    base_hits = 0
    full_hits = 0
    recovered = 0  # full succeeded where base-only failed
    total = len(images_payloads)

    for image, payload in images_payloads:
        base_ok = base_only_decode(image, detector) == payload
        full_ok = decode_frame(image) == payload
        base_hits += int(base_ok)
        full_hits += int(full_ok)
        if full_ok and not base_ok:
            recovered += 1

    return {
        "label": label,
        "total": total,
        "base_hits": base_hits,
        "full_hits": full_hits,
        "recovered": recovered,
        "base_rate": _rate(base_hits, total),
        "full_rate": _rate(full_hits, total),
    }


def _print_set(stats: dict) -> None:
    print(f"  [{stats['label']}] corpus = {stats['total']} frames")
    print(f"    base-only (1 pass) : {stats['base_hits']:>4}/{stats['total']}"
          f"  = {stats['base_rate']:6.2%}")
    print(f"    full (cascade+fb)  : {stats['full_hits']:>4}/{stats['total']}"
          f"  = {stats['full_rate']:6.2%}")
    print(f"    recovered by full  : {stats['recovered']:>4}"
          f"  (full succeeded where base-only failed)")
    relation = ">=" if stats["full_rate"] >= stats["base_rate"] else "<  (!!)"
    print(f"    full {relation} base-only : "
          f"{'OK' if stats['full_rate'] >= stats['base_rate'] else 'REGRESSION'}")


def main() -> int:
    print("=" * 70)
    print("PhotonTCP QR decode hardening benchmark (M10-T04)")
    print("hardware-free / in-memory / deterministic (seed = "
          f"0x{SEED:X})")
    print("=" * 70)

    payloads = build_corpus()
    print(f"corpus payloads: {len(payloads)}")

    # Encode each payload once; reuse the clean image for both the clean and
    # (after degradation) the degraded set.
    clean: list[tuple[np.ndarray, bytes]] = []
    degraded: list[tuple[np.ndarray, bytes]] = []
    for payload in payloads:
        image = encode_frame(payload)
        clean.append((image, payload))
        degraded.append((degrade(image), payload))

    alt = None
    try:
        from photontcp.qr.decode import _alt_kind_cached
        alt = _alt_kind_cached()
    except Exception:  # noqa: BLE001 - informational only
        pass
    print(f"alternate detector in this build: {alt!r} "
          f"({'fallback active' if alt else 'fallback skipped'})")
    print()

    print("CLEAN corpus (pristine encoder output):")
    clean_stats = run_set("clean", clean)
    _print_set(clean_stats)
    print()

    print("DEGRADED corpus (blur + downscale/upscale, camera-like):")
    deg_stats = run_set("degraded", degraded)
    _print_set(deg_stats)
    print()

    print("-" * 70)
    print("Summary")
    print(f"  clean    : base {clean_stats['base_rate']:.2%} -> "
          f"full {clean_stats['full_rate']:.2%}  "
          f"(+{clean_stats['recovered']} recovered)")
    print(f"  degraded : base {deg_stats['base_rate']:.2%} -> "
          f"full {deg_stats['full_rate']:.2%}  "
          f"(+{deg_stats['recovered']} recovered)")
    monotone = (clean_stats["full_rate"] >= clean_stats["base_rate"]
                and deg_stats["full_rate"] >= deg_stats["base_rate"])
    print(f"  full decoder rate >= base-only rate on every set: "
          f"{'YES' if monotone else 'NO'}")
    print("-" * 70)

    # Exit 0 as long as the hardened decoder never regressed below the base-only
    # decoder. This is the benchmark's invariant (the cascade can only add).
    return 0 if monotone else 1


if __name__ == "__main__":
    sys.exit(main())
