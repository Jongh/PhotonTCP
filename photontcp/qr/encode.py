"""QR encoder for PhotonTCP (M5).

This module turns arbitrary packet bytes into a QR-code image rendered as a
plain 2D ``numpy`` grayscale array, ready to be carried by an image channel.

Shared codec contract (see ``docs/milestones/M5.md`` -> "공유 규약"):

* **Image format**: a 2D ``numpy.ndarray`` of dtype ``uint8``. Values are only
  ``0`` (black / dark module) or ``255`` (white / light module and quiet zone),
  i.e. a grayscale bitmap. Each QR module is scaled to ``scale`` x ``scale``
  pixels and surrounded by a ``border`` (quiet zone) of ``border`` modules.
  Final shape is ``(H, W)`` with ``H == W == (modules + 2 * border) * scale``.
* **Byte payload representation**: raw ``bytes`` are wrapped with
  ``base64.b64encode`` and the resulting ASCII string is the QR text payload.
  The decoder reverses this with ``base64.b64decode``. base64 keeps the payload
  ASCII-safe so a string-returning decoder never corrupts binary data.
* **EC level**: the error-correction level follows segno's notation and is
  passed through the ``error`` parameter (default ``"m"`` = ~15% recovery).

The encoder is a pure function with no side effects (no file/stdout I/O, no
global state mutation): given the same input it always returns the same array.
"""

from __future__ import annotations

import base64

import numpy as np
import segno

__all__ = ["encode_frame", "QRCapacityError"]


class QRCapacityError(Exception):
    """Raised when ``data`` does not fit in a single QR symbol.

    segno raises :class:`segno.DataOverflowError` when the (base64-encoded)
    payload exceeds the largest QR symbol capacity for the requested error
    level. ``encode_frame`` converts that into this clearer, codec-specific
    error whose message reports both the raw byte length and the base64 length
    so callers can size/split payloads instead of seeing an opaque overflow.
    """


def encode_frame(
    data: bytes,
    *,
    scale: int = 8,
    border: int = 4,
    error: str = "m",
) -> np.ndarray:
    """Encode ``data`` bytes into a QR-code grayscale image.

    The bytes are base64-encoded to an ASCII string, rendered to a QR code by
    segno (which auto-selects the symbol version), and the QR module matrix is
    rasterized directly into a 2D ``uint8`` array. Rendering the matrix
    ourselves keeps the output deterministic and avoids an image-decoding
    dependency.

    Args:
        data: Raw payload bytes (e.g. a packed PhotonTCP packet).
        scale: Pixel size of each QR module (each module becomes a
            ``scale`` x ``scale`` block). Must be >= 1.
        border: Quiet-zone width in modules on every side. Must be >= 0.
        error: Error-correction level in segno notation
            (``"l"``, ``"m"``, ``"q"``, ``"h"``).

    Returns:
        A 2D ``numpy.ndarray`` of dtype ``uint8`` containing only the values
        ``0`` (dark) and ``255`` (light/quiet). Shape is ``(H, W)`` with
        ``H == W == (modules + 2 * border) * scale``.

    Raises:
        ValueError: If ``scale < 1`` or ``border < 0``.
        QRCapacityError: If ``data`` (after base64 encoding) is too large to
            fit in a single QR symbol at the requested error level.
    """
    if scale < 1:
        raise ValueError(f"scale must be >= 1, got {scale}")
    if border < 0:
        raise ValueError(f"border must be >= 0, got {border}")

    b64 = base64.b64encode(data).decode("ascii")
    try:
        qr = segno.make(b64, error=error)
    except segno.DataOverflowError as exc:
        raise QRCapacityError(
            "payload too large for a single QR symbol at error level "
            f"{error!r}: {len(data)} raw bytes -> {len(b64)} base64 chars "
            f"({exc})"
        ) from exc

    # segno's qr.matrix is a sequence of rows; each row is a sequence of module
    # values where dark == 1 (truthy) and light == 0 (falsy). Build a compact
    # module-level bitmap first (dark=0/black, light=255/white), then expand by
    # the quiet zone and scale factor.
    rows = list(qr.matrix)
    modules = len(rows)
    full = modules + 2 * border

    # Quiet zone defaults to white (255); fill the interior from the matrix.
    grid = np.full((full, full), 255, dtype=np.uint8)
    for r, row in enumerate(rows):
        for c, module in enumerate(row):
            if module:  # dark module
                grid[border + r, border + c] = 0

    # Expand each module to scale x scale pixels via nearest-neighbour repeat.
    image = np.kron(grid, np.ones((scale, scale), dtype=np.uint8))
    return image
