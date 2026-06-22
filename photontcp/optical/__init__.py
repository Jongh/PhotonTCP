"""Real optical channel layer (M8).

Transports QR frames over light — outgoing frames shown on a display, incoming
frames captured by a camera — behind the same :class:`~photontcp.channel.Channel`
interface the rest of PhotonTCP depends on. Hardware is abstracted by the
:class:`DisplaySink` / :class:`CameraSource` devices so the channel logic runs
either on a real webcam+screen link or fully in-memory for deterministic tests.
"""

from .channel import OpticalChannel
from .devices import (
    CameraSource,
    DisplaySink,
    MemoryCamera,
    MemoryDisplay,
    memory_device_pair,
)

# The device abstractions, the in-memory fakes, and OpticalChannel are all
# hardware-free (channel.py imports only numpy / the QR codec / devices, never
# cv2), so they are always safe to re-export.
__all__ = [
    "OpticalChannel",
    "DisplaySink",
    "CameraSource",
    "MemoryDisplay",
    "MemoryCamera",
    "memory_device_pair",
]

# The cv2-backed real device adapters are re-exported only when opencv-python is
# importable. ``cv2_devices`` does ``import cv2`` at module top, so importing it
# on a machine without OpenCV would raise ImportError. M8 completion criterion 8
# requires ``import photontcp.optical`` to NOT hard-fail when cv2 is absent, so
# the cv2 re-export is GUARDED: when cv2 is missing, ``Cv2Display`` / ``Cv2Camera``
# are bound to ``None`` and omitted from ``__all__`` (the rest of this package
# stays fully usable in-memory). When cv2 is present they are exported normally.
try:
    from .cv2_devices import Cv2Camera, Cv2Display
except ImportError:  # pragma: no cover - exercised only on cv2-absent machines
    Cv2Display = None  # type: ignore[assignment]
    Cv2Camera = None  # type: ignore[assignment]
else:
    __all__ += ["Cv2Display", "Cv2Camera"]
