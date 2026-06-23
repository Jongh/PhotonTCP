"""QR frame decoder for PhotonTCP (M5, hardened in M10).

Pure-function counterpart to ``qr/encode.py``. Decodes a QR-code image back
into the original packet bytes using OpenCV's ``cv2.QRCodeDetector``.

Shared protocol (must match the encoder, see docs/milestones/M5.md):

- **Image format**: a 2D ``numpy.ndarray`` of dtype ``uint8`` with values
  0 (black) / 255 (white) grayscale. This decoder is lenient and also accepts
  ``HxW`` or ``HxWx3`` ``uint8`` images (a color image is converted to gray).
- **Byte payload representation**: the encoder stores
  ``base64.b64encode(data).decode("ascii")`` as the QR text. Decoding therefore
  reads that ASCII string from the QR and applies ``base64.b64decode`` to
  recover the original bytes.

Preprocessing cascade (M10)
---------------------------
Real optical captures (camera-grabbed QR frames) can defeat a single
``detectAndDecode`` pass — blur, uneven lighting, and a content-dependent cv2
blind spot all reduce the decode rate. To recover those frames,
:func:`decode_frame` tries a **deterministic variant cascade** produced lazily
by :func:`_decode_variants`, cheapest first, short-circuiting on the first
variant that yields a QR whose text base64-decodes:

  1. raw grayscale (the original path — a clean QR succeeds here with ZERO
     extra work; later variants are never even computed);
  2. Otsu binarization;
  3. sharpen (unsharp ``filter2D``) then Otsu binarize — for blurry captures;
  4. 2.0x and 3.0x upscale of the binarized/sharpened images (bare upscale
     alone was found useless in M9, so upscale is combined with preprocessing).

The variant generator is a clean reusable helper: it takes a normalized 2D
``uint8`` gray image and yields candidate arrays (variant 1 is the input
unchanged). T02 reuses it to feed an alternate detector the same variants.

Alternate detector fallback (M10)
---------------------------------
``cv2.QRCodeDetector`` has a content-dependent blind spot: certain valid QR
images fail to decode under it at *any* scale or preprocessing (observed in
M9). As a last resort — only after the primary detector has failed on every
preprocessing variant — :func:`decode_frame` retries the same variants with an
**alternate** detector if the running OpenCV build provides one. The preferred
alternate is opencv-contrib's ``cv2.wechat_qrcode_WeChatQRCode`` (robust to
that blind spot); if it is unavailable or cannot be constructed (model files
absent), the build-in ``cv2.QRCodeDetectorAruco`` (OpenCV >= 4.7, needs no
model files) is tried instead. On a core ``opencv-python`` install with neither
available, the fallback is simply skipped — behaviour is then identical to the
cascade alone (no error, undecodable frames still return ``None``). Capability
is probed **once** and cached; each thread keeps its own alternate detector
instance (thread-safety, mirroring the primary per-thread detector).

None contract / never raises
----------------------------
The decoder is defensive: detection failures, malformed base64, and any
OpenCV exception are all treated as a damaged/undecodable frame. ``cv2.error``
is caught **per variant**, so one variant's failure (or a garbage detection
that does not base64-decode) just advances to the next variant rather than
aborting the whole decode. The function returns ``bytes`` on success and
``None`` only if **every** variant — across both the primary and (if present)
the alternate detector — fails. It never raises.
"""

from __future__ import annotations

import base64
import binascii
import threading
from collections.abc import Iterator

import cv2
import numpy as np

__all__ = ["decode_frame"]

# Per-thread detector storage. ``cv2.QRCodeDetector`` is not guaranteed to be
# safe for concurrent ``detectAndDecode`` calls from multiple threads, so each
# thread lazily creates and reuses its own private detector instance (in
# preparation for the M6+ camera thread). Within a single thread the behaviour
# is identical to the previous shared module-level detector.
_THREAD_LOCAL = threading.local()

# Unsharp / sharpening kernel for variant 3 (and its upscaled descendants).
# A standard 3x3 sharpen: centre weight 5, 4-neighbour weights -1, sums to 1 so
# overall brightness is preserved while edges (QR module boundaries) are crisped.
_SHARPEN_KERNEL = np.array(
    [
        [0, -1, 0],
        [-1, 5, -1],
        [0, -1, 0],
    ],
    dtype=np.float32,
)

# Upscale factors applied to the binarized/sharpened variants (variant 4).
_UPSCALE_FACTORS = (2.0, 3.0)

# Alternate-detector capability probe (M10). Resolved at most once across the
# process: the value is the *kind* of alternate QR detector this OpenCV build
# offers — "wechat", "aruco", or None (none available). ``_ALT_UNRESOLVED`` is a
# sentinel meaning "not probed yet". The probe constructs each candidate to be
# sure it actually works (e.g. WeChat needs model files), so it is guarded and
# cached under a lock; per-thread *instances* are created lazily afterwards.
_ALT_UNRESOLVED = object()
_alt_kind = _ALT_UNRESOLVED
_alt_kind_lock = threading.Lock()


def _thread_detector() -> "cv2.QRCodeDetector":
    """Return this thread's private, lazily-created ``cv2.QRCodeDetector``."""
    detector = getattr(_THREAD_LOCAL, "detector", None)
    if detector is None:
        detector = cv2.QRCodeDetector()
        _THREAD_LOCAL.detector = detector
    return detector


def _resolve_alt_kind() -> "str | None":
    """Probe (once) which alternate QR detector this OpenCV build supports.

    Returns ``"wechat"`` if ``cv2.wechat_qrcode_WeChatQRCode`` exists and can be
    constructed (opencv-contrib with bundled models), else ``"aruco"`` if
    ``cv2.QRCodeDetectorAruco`` (OpenCV >= 4.7, no model files) is constructable,
    else ``None``. Construction is wrapped broadly because a missing-model build
    raises build-specific errors — any failure just means "not available".
    """
    wechat_cls = getattr(cv2, "wechat_qrcode_WeChatQRCode", None)
    if wechat_cls is not None:
        try:
            wechat_cls()  # probe: succeeds only if usable (models present)
            return "wechat"
        except Exception:  # noqa: BLE001 - capability probe, any failure = N/A
            pass

    aruco_cls = getattr(cv2, "QRCodeDetectorAruco", None)
    if aruco_cls is not None:
        try:
            aruco_cls()
            return "aruco"
        except Exception:  # noqa: BLE001 - capability probe, any failure = N/A
            pass

    return None


def _alt_kind_cached() -> "str | None":
    """Return the alternate-detector kind, resolving and caching it once."""
    global _alt_kind
    if _alt_kind is _ALT_UNRESOLVED:
        with _alt_kind_lock:
            if _alt_kind is _ALT_UNRESOLVED:
                _alt_kind = _resolve_alt_kind()
    return _alt_kind


def _thread_alt_detector():
    """Return ``(kind, detector)`` for this thread's alternate detector.

    ``(None, None)`` when no alternate detector is available in this build.
    The instance is thread-local (mirrors :func:`_thread_detector`) because the
    alternate detectors' thread-safety is not guaranteed.
    """
    kind = _alt_kind_cached()
    if kind is None:
        return None, None
    detector = getattr(_THREAD_LOCAL, "alt_detector", None)
    if detector is None:
        detector = (
            cv2.wechat_qrcode_WeChatQRCode()
            if kind == "wechat"
            else cv2.QRCodeDetectorAruco()
        )
        _THREAD_LOCAL.alt_detector = detector
    return kind, detector


def _alt_decode(image: np.ndarray) -> bytes | None:
    """Last-resort decode with the alternate detector over the same variants.

    Returns the recovered bytes, or ``None`` if no alternate detector is
    available or none of the variants decode under it. Never raises: ``cv2``
    errors are caught per variant and malformed base64 is skipped, mirroring the
    primary path's None contract.
    """
    kind, detector = _thread_alt_detector()
    if detector is None:
        return None

    for variant in _decode_variants(image):
        try:
            if kind == "wechat":
                # WeChat: detectAndDecode -> (tuple_of_texts, points). It may
                # report several QRs; try each text.
                texts, _ = detector.detectAndDecode(variant)
                candidates = list(texts) if texts is not None else []
            else:
                # Aruco shares QRCodeDetector's (text, points, straight) shape.
                data_str, points, _ = detector.detectAndDecode(variant)
                candidates = [data_str] if (data_str and points is not None) else []
        except cv2.error:
            continue

        for text in candidates:
            if not text:
                continue
            try:
                return base64.b64decode(text, validate=True)
            except (binascii.Error, ValueError):
                continue

    return None


def _decode_variants(image: np.ndarray) -> Iterator[np.ndarray]:
    """Yield preprocessing variants of a normalized gray image, cheapest first.

    ``image`` must already be a 2D ``uint8`` grayscale array (the caller is
    responsible for normalization). Variants are produced **lazily**: each
    ``cv2`` operation runs only when the consumer advances the generator, so a
    decode that succeeds on variant 1 incurs zero extra preprocessing cost.

    The variant order (matching ``docs/milestones/M10.md``) is:

    1. the input unchanged (raw grayscale);
    2. Otsu binarization;
    3. sharpen (unsharp ``filter2D``) then Otsu binarize;
    4. 2.0x / 3.0x upscale of the binarized variant, then of the sharpened
       variant (upscale combined with preprocessing, never bare).

    This helper is intentionally reusable: M10-T02 feeds the same variants to
    an alternate QR detector. It does not swallow ``cv2`` errors itself — the
    consumer wraps each yielded variant in its own ``try/except cv2.error`` so
    one failing variant simply advances to the next.
    """
    # Variant 1: raw grayscale (hot path — no extra work).
    yield image

    # Variant 2: Otsu binarization.
    _, otsu = cv2.threshold(image, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    yield otsu

    # Variant 3: sharpen, then Otsu binarize (blurry-capture recovery).
    sharpened = cv2.filter2D(image, -1, _SHARPEN_KERNEL)
    _, sharp_otsu = cv2.threshold(
        sharpened, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU
    )
    yield sharp_otsu

    # Variant 4: upscale the *preprocessed* images (binarized then sharpened),
    # since M9 found bare upscale alone ineffective. INTER_NEAREST keeps the
    # binary modules crisp (no interpolation smear) and is cheap.
    for base in (otsu, sharp_otsu):
        for factor in _UPSCALE_FACTORS:
            yield cv2.resize(
                base,
                None,
                fx=factor,
                fy=factor,
                interpolation=cv2.INTER_NEAREST,
            )


def decode_frame(
    image: np.ndarray,
    detector: "cv2.QRCodeDetector | None" = None,
) -> bytes | None:
    """Decode a QR-code image back into the original packet bytes.

    Parameters
    ----------
    image:
        QR-code image as a ``numpy.ndarray``. A 2D ``uint8`` grayscale array
        (0/255) is expected, but a 3-channel (``HxWx3``, BGR) ``uint8`` image
        is also accepted and converted to grayscale internally.
    detector:
        Optional ``cv2.QRCodeDetector`` instance to reuse (performance). When
        ``None`` a per-thread detector (thread-local, lazily created) is used,
        which keeps concurrent decoding from multiple threads safe.

    Returns
    -------
    bytes | None
        The recovered original bytes (after base64 decoding the QR text), or
        ``None`` if the frame could not be detected/decoded for any reason
        (no QR found in any variant, malformed base64, or an OpenCV error).
        Damaged frames are reported as ``None`` rather than raising.

    Notes
    -----
    A deterministic preprocessing cascade (see :func:`_decode_variants` and the
    module docstring) is tried in order, cheapest first, short-circuiting on the
    first variant that yields a QR whose text base64-decodes. A clean QR decodes
    on variant 1 with no extra preprocessing; ``cv2.error`` is caught per variant
    so one variant's failure never aborts the whole decode.

    Shared protocol: the QR text is the ASCII base64 encoding of the original
    bytes, so this function applies ``base64.b64decode`` to the extracted
    string to recover the payload.
    """
    if detector is None:
        detector = _thread_detector()

    # Normalize input to a 2D uint8 grayscale array.
    if image is None:
        return None
    if image.ndim == 3:
        # Color image (assume BGR as produced by OpenCV) -> grayscale.
        try:
            image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        except cv2.error:
            return None
    # (A 2D uint8 array is used as-is; detectAndDecode accepts it directly.)

    for variant in _decode_variants(image):
        try:
            data_str, points, _ = detector.detectAndDecode(variant)
        except cv2.error:
            # Any OpenCV failure on this variant -> try the next one.
            continue

        # Detection failure on this variant: empty string and/or no points.
        if not data_str or points is None:
            continue

        try:
            return base64.b64decode(data_str, validate=True)
        except (binascii.Error, ValueError):
            # Garbage / malformed base64 from this variant's detection. Keep
            # trying remaining variants — a later one may yield a real QR.
            continue

    # Primary detector failed on every variant. Try the alternate detector (if
    # this OpenCV build offers one) as a last resort against cv2's blind spot.
    return _alt_decode(image)
