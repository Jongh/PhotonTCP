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
# NOTE: ``.peer`` is deliberately NOT imported here — see the lazy re-export at
# the bottom of this module.

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
    "run_peer",
    "PeerResult",
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


# ---------------------------------------------------------------------------
# Lazy re-export of the session driver (M12-T03, PEP 562)
# ---------------------------------------------------------------------------
# ``peer.py`` imports ``photontcp.app`` and ``photontcp.session`` — the *upper*
# layers. Importing it from this ``__init__`` made ``import photontcp.optical``
# (a transport-layer package) drag the whole session/app stack in, which is the
# reverse of the dependency direction README states ("upper layers depend only
# on ``Channel``"). M11-review flagged that as minor 11.
#
# ``run_peer`` / ``PeerResult`` stay part of this package's public surface —
# ``__all__`` above is unchanged and ``from photontcp.optical import run_peer``
# still works (``examples/optical_link.py`` relies on it) — but the submodule is
# now imported on FIRST ATTRIBUTE ACCESS instead of at package import time.
# The resolved objects are cached into module globals, so the ``__getattr__``
# hook runs at most once per name.
_LAZY_PEER_EXPORTS = ("run_peer", "PeerResult")


def __getattr__(name: str):
    """PEP 562 hook: resolve ``run_peer`` / ``PeerResult`` (and submodules) on demand.

    Besides the two lazy re-exports, this also restores plain **submodule
    attribute access** (``import photontcp.optical as o; o.peer``). Before the
    lazy re-export, ``from .peer import …`` at package-import time had the side
    effect of binding ``peer`` as an attribute of this package; dropping the
    eager import silently removed that access form (M12-review minor 8).

    The fallback is deliberately narrow: a name is imported only when it is a
    *real* submodule of this package (checked with :func:`importlib.util.find_spec`),
    so unknown names still raise :class:`AttributeError` rather than triggering an
    arbitrary import. And it stays **lazy** — nothing is imported until the
    attribute is actually touched, so ``import photontcp.optical`` still does not
    drag ``photontcp.app`` / ``photontcp.session`` in.

    **A real submodule that fails to import raises that failure, not**
    ``AttributeError``. On a machine without OpenCV, ``o.cv2_devices`` raises
    :class:`ImportError` — and because ``hasattr`` only swallows
    ``AttributeError``, it propagates there too. That matches what
    ``import photontcp.optical.cv2_devices`` does, but it differs from the
    pre-M13 shape where the eager cv2 guard had already bound the *name*
    ``Cv2Display``/``Cv2Camera`` to ``None``. To test availability use those
    guarded names (``Cv2Display is None``), not ``hasattr`` on the submodule.
    """
    if name in _LAZY_PEER_EXPORTS:
        from .peer import PeerResult, run_peer  # noqa: PLC0415 - intentional

        globals()["run_peer"] = run_peer
        globals()["PeerResult"] = PeerResult
        return globals()[name]

    if not name.startswith("_"):
        import importlib  # noqa: PLC0415 - intentional (lazy)
        import importlib.util  # noqa: PLC0415

        try:
            spec = importlib.util.find_spec(f"{__name__}.{name}")
        except (ImportError, ValueError):  # pragma: no cover - defensive
            spec = None
        if spec is not None:
            # ``import_module`` binds the submodule onto this package as a side
            # effect, so the hook runs at most once per submodule name.
            return importlib.import_module(f"{__name__}.{name}")

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    """Keep the lazy names visible to ``dir()`` / tab completion."""
    return sorted(set(globals()) | set(_LAZY_PEER_EXPORTS))
