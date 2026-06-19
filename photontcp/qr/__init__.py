"""PhotonTCP QR codec package.

Encodes packet bytes into QR-code grayscale images and decodes them back.
See ``docs/milestones/M5.md`` for the shared codec contract (image format,
base64 payload representation, error-correction level).
"""

from .decode import decode_frame
from .encode import encode_frame

__all__ = ["encode_frame", "decode_frame"]
