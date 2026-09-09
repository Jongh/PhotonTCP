"""Mobile (Android / Kivy) layer of PhotonTCP (M11).

Holds the mobile-side implementations of the optical hardware boundary — a
:class:`~photontcp.optical.devices.DisplaySink` backed by a Kivy widget and a
:class:`~photontcp.optical.devices.CameraSource` fed by ``camera4kivy``'s
CameraX preview — so a phone can drive a PhotonTCP session with its own screen
and camera. Everything above the channel (session, reliability, apps) is
untouched: it depends only on :class:`~photontcp.channel.Channel`.

Install the mobile dependencies with the ``mobile`` extra
(``pip install photontcp[mobile]``); on Android they are declared in
``buildozer.spec`` instead.
"""

# The Kivy-backed adapters are re-exported only when Kivy (and its runtime) is
# importable. ``kivy_devices`` imports ``kivy.clock`` / ``kivy.graphics.texture``
# at module top, so importing it on a desktop without Kivy would raise
# ImportError. M11 completion criterion 6 requires ``import photontcp.mobile`` to
# NOT hard-fail there, so the re-export is GUARDED exactly like the cv2 guard in
# ``photontcp/optical/__init__.py``: when Kivy is missing, ``KivyDisplay`` /
# ``KivyCamera`` are bound to ``None`` and omitted from ``__all__``. When Kivy is
# present they are exported normally.
__all__: list[str] = []

try:
    from .kivy_devices import KivyCamera, KivyDisplay
except ImportError:  # pragma: no cover - exercised only on Kivy-absent machines
    KivyDisplay = None  # type: ignore[assignment]
    KivyCamera = None  # type: ignore[assignment]
else:
    __all__ += ["KivyDisplay", "KivyCamera"]

# Same guard for the Kivy app shell (M11-T04). ``app`` imports ``kivy.app`` and
# several ``kivy.uix`` modules at top level, so it is only importable where Kivy
# is. ``PhotonTCPApp`` / ``main`` are the app's entry points: ``main()`` is what
# ``python -m photontcp.mobile.app`` and buildozer's packaged ``main.py`` both
# call. On a Kivy-less desktop they are ``None`` and stay out of ``__all__``.
try:
    from .app import PhotonTCPApp, main
except ImportError:  # pragma: no cover - exercised only on Kivy-absent machines
    PhotonTCPApp = None  # type: ignore[assignment]
    main = None  # type: ignore[assignment]
else:
    __all__ += ["PhotonTCPApp", "main"]
