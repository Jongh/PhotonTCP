"""QR frame decoder for PhotonTCP (M5).

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

The decoder is defensive: detection failures, base64 decode failures, and any
OpenCV exception are all treated as a damaged/undetectable frame and reported
by returning ``None`` (never raising).
"""

from __future__ import annotations

import base64
import binascii

import cv2
import numpy as np

__all__ = ["decode_frame"]

# Module-level detector reused across calls for performance. cv2.QRCodeDetector
# is stateless across detectAndDecode invocations, so a single shared instance
# is safe for repeated decoding within a single thread.
_DETECTOR = cv2.QRCodeDetector()


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
        ``None`` a shared module-level detector is used.

    Returns
    -------
    bytes | None
        The recovered original bytes (after base64 decoding the QR text), or
        ``None`` if the frame could not be detected/decoded for any reason
        (no QR found, malformed base64, or an OpenCV error). Damaged frames
        are reported as ``None`` rather than raising.

    Notes
    -----
    Shared protocol: the QR text is the ASCII base64 encoding of the original
    bytes, so this function applies ``base64.b64decode`` to the extracted
    string to recover the payload.
    """
    if detector is None:
        detector = _DETECTOR

    try:
        # Normalize input to a 2D uint8 grayscale array.
        if image is None:
            return None
        if image.ndim == 3:
            # Color image (assume BGR as produced by OpenCV) -> grayscale.
            image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        # (A 2D uint8 array is used as-is; detectAndDecode accepts it directly.)

        data_str, points, _ = detector.detectAndDecode(image)
    except cv2.error:
        # Any OpenCV failure -> treat as undecodable frame.
        return None

    # Detection failure: empty string and/or no localization points.
    if not data_str or points is None:
        return None

    try:
        return base64.b64decode(data_str, validate=True)
    except (binascii.Error, ValueError):
        # Malformed base64 payload -> damaged frame.
        return None
