"""QR frame decoder for PhotonTCP (M5, hardened in M10, seamed in M11).

Pure-function counterpart to ``qr/encode.py``. Decodes a QR-code image back
into the original packet bytes.

Shared protocol (must match the encoder, see docs/milestones/M5.md):

- **Image format**: a 2D ``numpy.ndarray`` of dtype ``uint8`` with values
  0 (black) / 255 (white) grayscale. This decoder is lenient and also accepts
  ``HxW`` or ``HxWx3`` ``uint8`` images (a color image is converted to gray).
- **Byte payload representation**: the encoder stores
  ``base64.b64encode(data).decode("ascii")`` as the QR text. Decoding therefore
  reads that ASCII string from the QR and applies ``base64.b64decode`` to
  recover the original bytes.

Decoder backend seam (M11)
--------------------------
The actual pixels-to-bytes work lives in a **decoder backend**: a callable
taking a ``np.ndarray`` image and returning ``bytes | None``. Backends are
registered by name with :func:`register_decoder_backend` (a *factory* that
returns the callable, or ``None``/raises when the backend is unavailable on
this platform) and selected with :func:`set_decoder_backend`;
:func:`active_decoder_backend` reports the name :func:`decode_frame` would use
right now.

The OpenCV path described below is registered as the ``"cv2"`` backend and is
the only one shipped today. ``cv2`` is imported **lazily** (never at module
import time), so ``import photontcp.qr`` succeeds on a platform without
OpenCV — e.g. an Android/p4a build where the opencv recipe is not included.
With no available backend :func:`decode_frame` simply returns ``None``; it
still never raises. ``numpy`` remains a hard import (the encoder and the
device contracts already traffic in ``ndarray``).

Selection rule: an explicit :func:`set_decoder_backend` name wins; otherwise
backends are probed in registration order and the first available one is used.
``set_decoder_backend(None)`` returns to that automatic selection.

A backend callable is invoked as ``backend(image)``. If its signature also
accepts a ``detector`` parameter (as the cv2 backend does), a ``detector=``
argument handed to :func:`decode_frame` is forwarded to it; backends that do
not take one simply never see it. Whether a backend accepts ``detector`` is
introspected once per backend callable and memoized, not per frame.

Preprocessing cascade (M10, cv2 backend)
----------------------------------------
Real optical captures (camera-grabbed QR frames) can defeat a single
``detectAndDecode`` pass — blur, uneven lighting, and a content-dependent cv2
blind spot all reduce the decode rate. To recover those frames, the cv2
backend tries a **deterministic variant cascade** produced lazily by
:func:`_decode_variants`, cheapest first, short-circuiting on the first
variant that yields a QR whose text base64-decodes:

  1. raw grayscale (the original path — a clean QR succeeds here with ZERO
     extra work; later variants are never even computed);
  2. Otsu binarization;
  3. sharpen (unsharp ``filter2D``) then Otsu binarize — for blurry captures;
  4. 2.0x and 3.0x upscale of the binarized/sharpened images (bare upscale
     alone was found useless in M9, so upscale is combined with preprocessing).

The variant generator is a clean reusable helper: it takes a normalized 2D
``uint8`` gray image and yields candidate arrays (variant 1 is the input
unchanged). The alternate detector is fed the same variants.

Alternate detector fallback (M10, cv2 backend)
----------------------------------------------
``cv2.QRCodeDetector`` has a content-dependent blind spot: certain valid QR
images fail to decode under it at *any* scale or preprocessing (observed in
M9). As a last resort — only after the primary detector has failed on every
preprocessing variant — the cv2 backend retries the same variants with an
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
The decoder is defensive: detection failures, malformed base64, a missing
backend, and any OpenCV exception are all treated as a damaged/undecodable
frame. ``cv2.error`` is caught **per variant**, so one variant's failure (or a
garbage detection that does not base64-decode) just advances to the next
variant rather than aborting the whole decode. :func:`decode_frame` returns
``bytes`` on success and ``None`` otherwise. It never raises.
"""

from __future__ import annotations

import base64
import binascii
import inspect
import threading
from collections.abc import Callable, Iterator
from typing import Any

import numpy as np

__all__ = [
    "decode_frame",
    "register_decoder_backend",
    "set_decoder_backend",
    "active_decoder_backend",
]

# A decoder backend: image -> recovered bytes (or None if undecodable).
DecoderBackend = Callable[..., "bytes | None"]
# A backend factory: returns a backend callable, or None when this platform
# cannot provide it. Raising is also read as "unavailable".
DecoderBackendFactory = Callable[[], "DecoderBackend | None"]

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


# --------------------------------------------------------------------------
# Lazy cv2 access
# --------------------------------------------------------------------------


def _require_cv2() -> Any | None:
    """Import and return the ``cv2`` module, or ``None`` if it is unavailable.

    The import is deliberately *not* done at module import time (M11): a
    platform without OpenCV must still be able to ``import photontcp.qr``.
    ``sys.modules`` makes the successful case a dict lookup, so calling this on
    every frame is cheap; the failing case is slow but only happens where
    decoding is impossible anyway.
    """
    try:
        import cv2  # noqa: PLC0415 - intentional lazy import (see docstring)
    except Exception:  # noqa: BLE001 - any import failure = cv2 unavailable
        return None
    return cv2


# --------------------------------------------------------------------------
# cv2 backend internals (moved verbatim from the pre-M11 module level; the
# names are kept module-level because docs/v1.0-signoff.md invokes
# ``_resolve_alt_kind`` by name and tests monkeypatch these helpers)
# --------------------------------------------------------------------------


def _thread_detector() -> Any | None:
    """Return this thread's private, lazily-created ``cv2.QRCodeDetector``.

    ``None`` when OpenCV is not available in this environment.
    """
    detector = getattr(_THREAD_LOCAL, "detector", None)
    if detector is None:
        cv2 = _require_cv2()
        if cv2 is None:
            return None
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
    cv2 = _require_cv2()
    if cv2 is None:
        return None

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
        cv2 = _require_cv2()
        if cv2 is None:
            return None, None
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

    cv2 = _require_cv2()
    if cv2 is None:
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

    This helper is intentionally reusable: the alternate QR detector is fed the
    same variants. It does not swallow ``cv2`` errors itself — the consumer
    wraps each yielded variant in its own ``try/except cv2.error`` so one
    failing variant simply advances to the next.

    Without OpenCV only variant 1 can be produced, so the generator stops after
    it (the cv2 backend is not selectable in that case anyway).
    """
    # Variant 1: raw grayscale (hot path — no extra work).
    yield image

    cv2 = _require_cv2()
    if cv2 is None:
        return

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


def _cv2_decode(
    image: np.ndarray,
    detector: Any | None = None,
) -> bytes | None:
    """The ``"cv2"`` decoder backend: cascade + alternate-detector fallback.

    This is the pre-M11 body of :func:`decode_frame`, unchanged: normalize to
    2D gray, run the primary detector over :func:`_decode_variants` (short-
    circuiting on the first base64-decodable hit), then fall back to
    :func:`_alt_decode`. Returns ``None`` (never raises) when nothing decodes.
    """
    cv2 = _require_cv2()
    if cv2 is None:
        return None

    if detector is None:
        detector = _thread_detector()
        if detector is None:
            return None

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


def _cv2_backend_factory() -> DecoderBackend | None:
    """Factory for the ``"cv2"`` backend: available iff ``cv2`` imports."""
    if _require_cv2() is None:
        return None
    return _cv2_decode


# --------------------------------------------------------------------------
# Backend registry
# --------------------------------------------------------------------------

# name -> factory, in registration order (dicts preserve insertion order, and
# that order *is* the automatic-selection probe order).
_BACKEND_FACTORIES: dict[str, DecoderBackendFactory] = {}
# backend callable -> whether it accepts a ``detector`` argument. Signature
# introspection is done once per distinct backend callable; *availability* is
# deliberately NOT cached, so that a platform/import change (or a test
# simulating cv2's absence) is reflected immediately by
# ``active_decoder_backend()``. Factories are contractually cheap — the cv2 one
# is a ``sys.modules`` lookup — so re-probing per frame costs nothing material.
_ACCEPTS_DETECTOR: dict[DecoderBackend, bool] = {}
_SELECTED_BACKEND: str | None = None
_BACKEND_LOCK = threading.Lock()


def register_decoder_backend(name: str, factory: DecoderBackendFactory) -> None:
    """Register a decoder backend under ``name``.

    ``factory`` is a zero-argument callable invoked lazily whenever the backend
    is resolved (it is the availability probe, so keep it cheap). It returns
    the backend callable — ``backend(image) -> bytes | None``, optionally
    also accepting ``detector=`` — or ``None`` (or raises) if the backend is
    not available in this environment.

    **Return the same callable object every time** (a module-level function or a
    cached instance, as the shipped ``"cv2"`` backend does) rather than building
    a fresh closure per call. Availability is deliberately not cached, so the
    factory runs once per :func:`decode_frame`; the ``detector``-signature probe
    is memoized *per backend object*, so a factory that mints a new object each
    time would re-introspect on every frame and grow that memo without bound.

    Registering an existing name replaces its factory. Registration order is
    the automatic-selection probe order; a re-registration keeps the original
    position.
    """
    if not isinstance(name, str) or not name:
        raise ValueError("backend name must be a non-empty str")
    if not callable(factory):
        raise TypeError("backend factory must be callable")
    with _BACKEND_LOCK:
        _BACKEND_FACTORIES[name] = factory


def set_decoder_backend(name: str | None) -> None:
    """Select the decoder backend explicitly, or ``None`` for automatic.

    With a name, :func:`decode_frame` uses only that backend (and decodes to
    ``None`` if it turns out to be unavailable). With ``None``, selection
    returns to probing registered backends in registration order.

    Raises ``ValueError`` for an unregistered name — this is a configuration
    call, not part of the decode hot path, so a typo is reported rather than
    silently degrading every subsequent frame to ``None``.
    """
    global _SELECTED_BACKEND
    if name is not None and name not in _BACKEND_FACTORIES:
        raise ValueError(f"unknown decoder backend: {name!r}")
    _SELECTED_BACKEND = name


def _resolve_one(name: str) -> tuple[DecoderBackend, bool] | None:
    """Resolve a single registered backend by probing it, or ``None``.

    The factory is invoked on every resolution (availability is live, see
    :data:`_ACCEPTS_DETECTOR`); only the signature introspection is memoized.
    """
    factory = _BACKEND_FACTORIES.get(name)
    if factory is None:
        return None
    try:
        backend = factory()
    except Exception:  # noqa: BLE001 - a failing factory just means unavailable
        return None
    if backend is None or not callable(backend):
        return None
    try:
        accepts = _ACCEPTS_DETECTOR[backend]
    except (KeyError, TypeError):  # TypeError: unhashable backend object
        accepts = _accepts_detector(backend)
        try:
            _ACCEPTS_DETECTOR[backend] = accepts
        except TypeError:
            pass
    return backend, accepts


def _accepts_detector(backend: DecoderBackend) -> bool:
    """Whether ``backend`` takes a ``detector`` argument (checked once)."""
    try:
        params = inspect.signature(backend).parameters
    except (TypeError, ValueError):
        return False
    if "detector" in params:
        return True
    return any(p.kind is p.VAR_KEYWORD for p in params.values())


def _resolve_backend() -> tuple[str | None, DecoderBackend | None, bool]:
    """Return ``(name, backend, accepts_detector)`` for the effective backend.

    ``(None, None, False)`` when nothing usable is registered/available.
    """
    selected = _SELECTED_BACKEND
    if selected is not None:
        entry = _resolve_one(selected)
        return (selected, entry[0], entry[1]) if entry else (None, None, False)
    for name in list(_BACKEND_FACTORIES):
        entry = _resolve_one(name)
        if entry is not None:
            return name, entry[0], entry[1]
    return None, None, False


def active_decoder_backend() -> str | None:
    """Name of the backend :func:`decode_frame` would use now, else ``None``.

    Reflects the *effective* choice: an explicit selection that turns out to be
    unavailable reports ``None``, as does an environment with no usable backend
    at all.
    """
    name, _, _ = _resolve_backend()
    return name


register_decoder_backend("cv2", _cv2_backend_factory)


# --------------------------------------------------------------------------
# Public decode entry point
# --------------------------------------------------------------------------


def decode_frame(
    image: np.ndarray,
    detector: Any | None = None,
) -> bytes | None:
    """Decode a QR-code image back into the original packet bytes.

    Parameters
    ----------
    image:
        QR-code image as a ``numpy.ndarray``. A 2D ``uint8`` grayscale array
        (0/255) is expected, but a 3-channel (``HxWx3``, BGR) ``uint8`` image
        is also accepted and converted to grayscale internally.
    detector:
        Optional detector instance to reuse (performance), forwarded to the
        active backend if it accepts one — for the ``"cv2"`` backend this is a
        ``cv2.QRCodeDetector``. When ``None`` the cv2 backend uses a per-thread
        detector (thread-local, lazily created), which keeps concurrent
        decoding from multiple threads safe.

    Returns
    -------
    bytes | None
        The recovered original bytes (after base64 decoding the QR text), or
        ``None`` if the frame could not be detected/decoded for any reason
        (no QR found in any variant, malformed base64, an OpenCV error, or no
        decoder backend available at all). Damaged frames are reported as
        ``None`` rather than raising.

    Notes
    -----
    The work is delegated to the active decoder backend (see the module
    docstring). With the shipped ``"cv2"`` backend, a deterministic
    preprocessing cascade (see :func:`_decode_variants`) is tried in order,
    cheapest first, short-circuiting on the first variant that yields a QR
    whose text base64-decodes; a clean QR decodes on variant 1 with no extra
    preprocessing, and ``cv2.error`` is caught per variant so one variant's
    failure never aborts the whole decode.

    Shared protocol: the QR text is the ASCII base64 encoding of the original
    bytes, so this function applies ``base64.b64decode`` to the extracted
    string to recover the payload.
    """
    if image is None:
        return None

    _, backend, accepts_detector = _resolve_backend()
    if backend is None:
        # No decoder available on this platform: undecodable frame, not an
        # error (keeps the None contract on cv2-less builds).
        return None

    try:
        if detector is not None and accepts_detector:
            return backend(image, detector=detector)
        return backend(image)
    except Exception:  # noqa: BLE001 - never-raise contract, incl. 3rd-party backends
        return None
