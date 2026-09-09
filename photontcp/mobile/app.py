"""Kivy application shell for driving one optical peer from a phone (M11-T04).

What this module is
===================

A **thin UI shell**. It owns exactly three things:

1. the widget tree (settings row, QR display area, status line, start/stop),
2. assembling an :class:`~photontcp.optical.channel.OpticalChannel` out of
   :class:`~photontcp.mobile.kivy_devices.KivyDisplay` +
   :class:`~photontcp.mobile.kivy_devices.KivyCamera`, and
3. running :func:`photontcp.optical.peer.run_peer` on a worker thread while
   painting its ``on_event`` progress into the status line.

It deliberately contains **no session/protocol logic**: every handshake, message
and close decision lives in :func:`~photontcp.optical.peer.run_peer` (M11-T02),
which is hardware-free and unit-tested. If you find yourself wanting to add a
protocol branch here, it belongs in ``peer.py`` instead.

Threading
=========

Three threads are in play and the boundaries are fixed:

* **Kivy main thread** — owns every widget and every GL call. All UI mutation in
  this module goes through :meth:`PhotonTCPApp._ui`, which marshals with
  ``Clock.schedule_once``. Nothing else touches a widget.
* **Session worker** (one, started per run) — runs ``run_peer`` start to finish.
  It is where ``time.sleep``-based ``hold`` pacing happens, which is exactly why
  it must not be the main thread.
* **Camera capture thread** — already owned by ``OpticalChannel`` (its capture
  loop) and by ``camera4kivy``'s analysis callback. No new thread boundary is
  introduced here: ``KivyCamera.submit_frame`` is the hand-off point.

Running it
==========

* Desktop (Kivy installed)::

      python -m photontcp.mobile.app

* Android (buildozer): the packaged ``main.py`` entry script should be::

      from photontcp.mobile.app import main
      main()

  ``PhotonTCPApp`` and :func:`main` are the two public names; both are
  re-exported from :mod:`photontcp.mobile` when Kivy is importable.

Import guard
============

This module imports Kivy at top level, so importing it on a Kivy-less desktop
raises ``ImportError`` — same as :mod:`photontcp.mobile.kivy_devices`.
:mod:`photontcp.mobile` guards both imports (binding the names to ``None``), so
``import photontcp.mobile`` keeps working there, which is what M11 completion
criterion 6 requires.

실기 실측 (M13, 2026-09-09 — Galaxy S26 + Galaxy Tab S9 FE+)
=============================================================

Confirmed on real Android hardware:

* the widget tree renders and the app survives a full session (Android 16,
  Python 3.14.2, Kivy 2.3.1);
* **Android GLES accepts the ``"luminance"`` texture** ``KivyDisplay`` uploads —
  the on-screen QR was decoded straight off a screenshot by this project's own
  ``decode_frame`` (Xclipse 960/ANGLE and Mali-G68 both);
* the runtime CAMERA permission flow works (request -> grant -> session runs);
* ``android.wakelock = True`` holds a ``SCREEN_BRIGHT_WAKE_LOCK`` while a
  session runs (``dumpsys power``);
* the camera path delivers frames — two devices completed a full bidirectional
  session over light (``established`` / ``MATCH 2/2`` / ``closed`` on both).

**Orientation is NOT a correctness concern.** Earlier revisions of this file
said a mirrored QR cannot be decoded and made ``flip_*`` the first thing to
check on device. That was wrong: ``cv2.QRCodeDetector`` decodes 90/180/270°
rotations *and* horizontal/vertical mirroring. Alignment (how large and how
square-on the peer's QR lands in frame) is what actually decides the link.

확인 필요 (still unverified)
===========================

* ``camera4kivy``'s exact ``analyze_pixels_callback`` argument list. The path is
  known to *work* (the session above ran through it), but the concrete signature
  was never recorded on device; the subclass below still absorbs drift with
  ``*args``/``**kwargs`` and binds only the first two parameters.
* Whether CameraX/``camera4kivy`` **recycles** the analysis buffer after the
  callback returns. ``KivyCamera`` copies on submit by default
  (``copy_on_submit``), which makes the question moot for correctness — so the
  device run could not distinguish the two cases.
"""

from __future__ import annotations

import os
import threading
import traceback

from kivy.app import App
from kivy.clock import Clock
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.image import Image
from kivy.uix.label import Label
from kivy.uix.spinner import Spinner
from kivy.uix.textinput import TextInput

from ..optical.channel import OpticalChannel
from ..optical.peer import DEFAULT_HOLD, DEFAULT_TIMEOUT, run_peer
from .kivy_devices import KivyCamera, KivyDisplay

__all__ = ["PhotonTCPApp", "SessionCancelled", "main", "is_android"]

#: QR error-correction level used on the real optical link. ``"q"`` (~25%
#: recovery) buys capture robustness over the channel default ``"m"``; this is
#: the value ``docs/v1.0-signoff.md`` treats as the hardware setting.
LINK_ERROR_LEVEL = "q"

#: Default QR module size in pixels for the phone screen.
DEFAULT_SCALE = 8

#: Smallest ``hold`` the settings row may produce. ``run_peer`` rejects
#: ``hold <= 0`` outright (a zero pause starves the channel's capture thread, so
#: the drive can never complete), and a UI must not be able to launch a session
#: that is guaranteed to fail — typing 0 into the field clamps up to this instead
#: of producing a handshake timeout that reads like a hardware fault
#: (M11-review 권장 4). It is a floor, not a recommendation: over a real link the
#: useful value is at least one camera frame period (see ``DEFAULT_HOLD``).
MIN_HOLD = 0.01


def is_android() -> bool:
    """Return ``True`` when running on Android (python-for-android runtime).

    Uses the environment marker p4a sets plus a probe of the ``android`` module,
    so it stays ``False`` on every desktop — where the permission and camera
    plumbing below must be skipped rather than attempted.
    """
    if "ANDROID_ARGUMENT" in os.environ or "ANDROID_APP_PATH" in os.environ:
        return True
    try:  # pragma: no cover - only importable on a device
        import android  # noqa: F401
    except ImportError:
        return False
    return True


class SessionCancelled(Exception):
    """Raised inside the worker when the user presses Stop.

    ``run_peer`` has no cancellation token (it is a straight-line drive bounded
    by round caps and a wall-clock deadline), and a closed channel merely makes
    sends/receives no-ops — the drive would keep pumping empty rounds until its
    deadline. So Stop is implemented at the *channel* boundary, which is a UI
    concern rather than protocol logic: :class:`_CancellableChannel` starts
    raising this, it propagates out of ``run_peer`` (whose ``finally`` still
    closes the channel), and the worker reports "stopped".
    """


class _CancellableChannel:
    """Delegating :class:`~photontcp.channel.Channel` with a user Stop switch.

    Wraps the real :class:`OpticalChannel`. Until :meth:`cancel` is called every
    method is a straight pass-through, so the session behaves exactly as it
    would over the bare channel. After :meth:`cancel`, ``send_frame`` and
    ``recv_frame`` raise :class:`SessionCancelled` so the in-flight drive unwinds
    immediately instead of burning its remaining deadline.

    Intentionally *not* a protocol participant: it inspects nothing and rewrites
    nothing, it only refuses to keep transporting.
    """

    def __init__(self, channel: OpticalChannel) -> None:
        self._channel = channel
        self._cancelled = False

    def cancel(self) -> None:
        """Ask the next channel operation to abort the drive (idempotent)."""
        self._cancelled = True

    def _check(self) -> None:
        if self._cancelled:
            raise SessionCancelled("session stopped by the user")

    def start(self) -> None:
        self._channel.start()

    def send_frame(self, frame: bytes) -> None:
        self._check()
        self._channel.send_frame(frame)

    def recv_frame(self, timeout: float | None = None) -> bytes | None:
        self._check()
        return self._channel.recv_frame(timeout)

    def close(self) -> None:
        self._channel.close()


def _make_preview(camera: KivyCamera, facing: str):
    """Build a ``camera4kivy`` ``Preview`` wired to ``camera.submit_frame``.

    :param camera: The :class:`KivyCamera` that receives every analysed frame.
    :param facing: ``"back"`` or ``"front"`` — the CameraX lens to open.
    :returns: ``(preview_widget, error_message)``. Exactly one is non-``None``:
        on any failure (``camera4kivy`` missing, no camera) the widget is
        ``None`` and the message is a human-readable reason for the status line.

    **확인 필요**: the analysis-callback signature is ``camera4kivy``'s
    documented ``analyze_pixels_callback(self, pixels, image_size, image_pos,
    scale, mirror)`` with RGBA pixels, but no device was available to verify it.
    The subclass therefore binds only ``pixels`` and ``image_size`` positionally
    and swallows any further arguments, so a signature change downstream cannot
    break the wiring — and any exception raised inside the callback is caught so
    a camera glitch can never kill CameraX's analysis thread.
    """
    try:
        from camera4kivy import Preview  # type: ignore[import-not-found]
    except ImportError as exc:
        return None, f"camera4kivy not installed ({exc}) - cannot capture frames"

    class _QRPreview(Preview):  # pragma: no cover - needs camera4kivy + a device
        """A ``Preview`` that forwards every analysed frame to the adapter."""

        def analyze_pixels_callback(self, pixels, image_size=None, *args, **kwargs):
            try:
                camera.submit_frame(pixels, image_size, colorfmt="rgba")
            except Exception:
                # The analysis thread belongs to CameraX; never raise into it.
                pass

    try:  # pragma: no cover - needs camera4kivy
        preview = _QRPreview()
    except Exception as exc:  # pragma: no cover - defensive
        return None, f"camera preview could not be created ({exc})"

    return preview, None


class PhotonTCPApp(App):
    """The PhotonTCP mobile shell.

    Screen layout (top to bottom):

    * **settings row** — role (``sender``/``receiver``), ``scale``, ``hold``,
      camera facing (``back``/``front``). Frozen while a session runs.
    * **QR area** — the ``Image`` widget ``KivyDisplay`` blits each outgoing QR
      into, next to the (small) camera preview so the operator can aim the phone.
    * **status line** — updated from ``run_peer``'s ``on_event`` stream, and
      replaced by the ``established / MATCH / closed / PASS·FAIL`` summary when
      the drive ends.
    * **buttons** — Start / Stop.

    Every UI mutation happens on the Kivy main thread (see :meth:`_ui`); the
    session itself runs on :attr:`_worker`.
    """

    title = "PhotonTCP"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._worker: threading.Thread | None = None
        self._channel: _CancellableChannel | None = None
        self._display: KivyDisplay | None = None
        self._camera: KivyCamera | None = None
        self._preview = None
        self._running = False
        # Set once the Android camera permission is known to be granted, so the
        # request is only made when it is actually needed.
        self._permission_granted = not is_android()

    # ------------------------------------------------------------------ UI --

    def build(self):
        """Construct the widget tree. Runs on the Kivy main thread."""
        root = BoxLayout(orientation="vertical", padding=6, spacing=6)

        # --- Settings row. ------------------------------------------------ #
        settings = BoxLayout(size_hint_y=None, height=44, spacing=6)
        self.role_spinner = Spinner(
            text="sender", values=("sender", "receiver"), size_hint_x=0.3
        )
        self.scale_input = TextInput(
            text=str(DEFAULT_SCALE),
            multiline=False,
            input_filter="int",
            size_hint_x=0.2,
        )
        self.hold_input = TextInput(
            text=str(DEFAULT_HOLD),
            multiline=False,
            input_filter="float",
            size_hint_x=0.2,
        )
        self.facing_spinner = Spinner(
            text="back", values=("back", "front"), size_hint_x=0.3
        )
        settings.add_widget(self.role_spinner)
        settings.add_widget(self.scale_input)
        settings.add_widget(self.hold_input)
        settings.add_widget(self.facing_spinner)
        root.add_widget(settings)

        # --- QR display area (+ a small camera preview slot). ------------- #
        middle = BoxLayout(spacing=6)
        # ``fit_mode="contain"`` is the modern spelling of the old
        # ``allow_stretch=True, keep_ratio=True`` pair (which Kivy 2.3.1 emits a
        # deprecation warning for): scale the QR up to fill the slot but never
        # distort it — a stretched QR is a QR that does not decode. Nearest
        # filtering is not forced here because the texture is created by
        # KivyDisplay.
        #
        # ``fit_mode`` exists from Kivy 2.2 on, so it is safe on **both** targets
        # this app ships to and no compatibility branch is needed:
        #   * desktop — the installed Kivy is 2.3.1 (M12-T02);
        #   * Android — p4a's kivy recipe pins ``version = '2.3.1'`` at the
        #     revision this project builds against, and the recipe has pinned
        #     >= 2.2.0 since p4a v2023.05.21 — far older than any revision that
        #     supports the NDK r28c / Python 3.14 / api 33 combination this
        #     project requires, so no reachable p4a pin can hand us Kivy < 2.2.
        self.qr_image = Image(fit_mode="contain", size_hint_x=0.7)
        middle.add_widget(self.qr_image)
        # camera4kivy's Preview must live in the widget tree to run, so it gets a
        # narrow column; it doubles as the operator's aiming aid.
        self.preview_slot = BoxLayout(size_hint_x=0.3)
        middle.add_widget(self.preview_slot)
        root.add_widget(middle)

        # --- Status line. -------------------------------------------------- #
        self.status_label = Label(
            text="idle - press Start",
            size_hint_y=None,
            height=72,
            halign="left",
            valign="middle",
        )
        # Wrap the text inside the label's own width instead of overflowing.
        self.status_label.bind(  # type: ignore[attr-defined]
            size=lambda widget, value: setattr(widget, "text_size", value)
        )
        root.add_widget(self.status_label)

        # --- Buttons. ------------------------------------------------------ #
        buttons = BoxLayout(size_hint_y=None, height=52, spacing=6)
        self.start_button = Button(text="Start")
        self.start_button.bind(on_release=self.on_start_pressed)  # type: ignore[attr-defined]
        self.stop_button = Button(text="Stop", disabled=True)
        self.stop_button.bind(on_release=self.on_stop)  # type: ignore[attr-defined]
        buttons.add_widget(self.start_button)
        buttons.add_widget(self.stop_button)
        root.add_widget(buttons)

        return root

    def _ui(self, func, *args) -> None:
        """Run ``func(*args)`` on the Kivy main thread.

        The single marshalling point for this module: worker-thread and
        camera-thread code calls this instead of touching widgets. Safe to call
        from the main thread too (it just defers by one frame).
        """
        Clock.schedule_once(lambda _dt: func(*args), 0)

    def set_status(self, text: str) -> None:
        """Replace the status line (thread-safe)."""
        self._ui(lambda: setattr(self.status_label, "text", text))

    def _set_running(self, running: bool) -> None:
        """Reflect run state in the controls. Main thread only (via :meth:`_ui`)."""
        self._running = running
        self.start_button.disabled = running
        self.stop_button.disabled = not running
        for widget in (
            self.role_spinner,
            self.scale_input,
            self.hold_input,
            self.facing_spinner,
        ):
            widget.disabled = running

    # ------------------------------------------------------- permissions --

    def _ensure_permission(self, then) -> None:
        """Ensure the Android CAMERA permission, then call ``then()``.

        Off Android (or once already granted) ``then`` is called immediately.
        On Android the runtime request is issued and ``then`` runs only if the
        grant comes back positive; a denial updates the status line and stops
        there — no crash, no session.

        :param then: Zero-argument callable run on the Kivy main thread.
        """
        if self._permission_granted:
            then()
            return

        try:  # pragma: no cover - only importable on a device
            from android.permissions import (  # type: ignore[import-not-found]
                Permission,
                check_permission,
                request_permissions,
            )
        except Exception as exc:  # pragma: no cover - defensive
            self.set_status(
                f"camera permission API unavailable ({exc}); cannot start a session"
            )
            return

        try:  # pragma: no cover - device only
            if check_permission(Permission.CAMERA):
                self._permission_granted = True
                then()
                return
        except Exception:
            # check_permission is advisory; fall through to the request.
            pass

        def _callback(permissions, grants):  # pragma: no cover - device only
            """Android hands this back on its own thread: marshal to the UI."""
            granted = bool(grants) and all(grants)
            if granted:
                self._permission_granted = True
                self._ui(then)
            else:
                self.set_status(
                    "camera permission denied - grant it in Settings, then Start again"
                )

        self.set_status("requesting camera permission...")
        try:  # pragma: no cover - device only
            request_permissions([Permission.CAMERA], _callback)
        except Exception as exc:
            self.set_status(f"camera permission request failed ({exc})")

    # ------------------------------------------------------------ session --

    def on_start_pressed(self, *_args) -> None:
        """Start button handler: request permission, then launch the session.

        **The name matters.** This must NOT be called ``on_start``: Kivy's
        :class:`~kivy.app.App` dispatches an ``on_start`` lifecycle event while
        the app boots (``App.run`` → ``self.dispatch('on_start')``), so a method
        with that name is invoked *by the framework* the moment the window
        opens. Before M13 this class did define ``on_start``, and the effect was
        that **the session started by itself** — camera permission was requested
        and a handshake began without anyone pressing Start, which is both wrong
        on its own and would corrupt the first on-device smoke observations.

        :meth:`on_stop` is the deliberate exception (see its docstring): there
        the framework's shutdown hook and the Stop button want the same thing.
        Here they do not, so the names are kept apart.
        """
        if self._running:
            return
        self.set_status("starting...")
        self._ensure_permission(self._launch_session)

    def _read_settings(self) -> tuple[str, int, float, str]:
        """Read the settings row, falling back to defaults on unusable input."""
        role = self.role_spinner.text
        try:
            scale = max(1, int(self.scale_input.text))
        except (TypeError, ValueError):
            scale = DEFAULT_SCALE
        try:
            # Clamp UP to MIN_HOLD: 0 (or a negative) is a value run_peer refuses,
            # so the UI must never hand it one.
            hold = max(MIN_HOLD, float(self.hold_input.text))
        except (TypeError, ValueError):
            hold = DEFAULT_HOLD
        facing = self.facing_spinner.text
        return role, scale, hold, facing

    def _launch_session(self) -> None:
        """Assemble devices + channel and hand the drive to a worker thread.

        Runs on the Kivy main thread (the preview widget is added to the tree
        here). If no camera can be opened, the status line says so and no
        session starts — that is the desktop path, and it is not an error.
        """
        if self._running:
            return
        role, scale, hold, facing = self._read_settings()

        display = KivyDisplay(self.qr_image)
        # A front lens previews mirrored. That is NOT a decoding problem — cv2
        # reads mirrored QR fine (M13 measurement) — but normalising keeps the
        # captured frame in the same orientation as the desktop path.
        camera = KivyCamera(flip_horizontal=(facing == "front"))
        preview, error = _make_preview(camera, facing)
        if preview is None:
            display.close()
            camera.close()
            self.set_status(f"{error}. Session not started.")
            return

        self.preview_slot.clear_widgets()
        self.preview_slot.add_widget(preview)
        try:  # pragma: no cover - needs camera4kivy + a device
            # 확인 필요: keyword names are camera4kivy's documented ones; fall
            # back to a bare connect so a signature change is not fatal.
            try:
                preview.connect_camera(
                    enable_analyze_pixels=True, camera_id=facing
                )
            except TypeError:
                preview.connect_camera(enable_analyze_pixels=True)
        except Exception as exc:  # pragma: no cover - device only
            self.preview_slot.clear_widgets()
            display.close()
            camera.close()
            self.set_status(f"camera unavailable ({exc}). Session not started.")
            return

        channel = _CancellableChannel(
            OpticalChannel(
                display,
                camera,
                scale=scale,
                error=LINK_ERROR_LEVEL,
                hold=hold,
            )
        )

        self._display = display
        self._camera = camera
        self._preview = preview
        self._channel = channel
        self._set_running(True)
        self.set_status(
            f"role={role} scale={scale} hold={hold}s camera={facing} - starting"
        )

        self._worker = threading.Thread(
            target=self._run_session,
            args=(channel, role, hold),
            name="photontcp-session",
            daemon=True,
        )
        self._worker.start()

    def _run_session(self, channel: _CancellableChannel, role: str, hold: float) -> None:
        """Worker-thread body: one ``run_peer`` drive, nothing else.

        Every outcome — success, user stop, unexpected exception — ends with the
        UI unlocked and the devices released, so a failed run never leaves the
        app stuck in "running".
        """
        try:
            run_peer(
                channel,
                role,
                hold=hold,
                timeout=DEFAULT_TIMEOUT,
                on_event=self.on_event,
            )
        except SessionCancelled:
            self.set_status("stopped by user")
        except Exception as exc:  # pragma: no cover - defensive
            traceback.print_exc()
            self.set_status(f"session failed: {type(exc).__name__}: {exc}")
        finally:
            self._ui(self._teardown)

    def on_event(self, kind: str, detail: dict) -> None:
        """Turn one ``run_peer`` progress event into a status line.

        Called on the worker thread; every branch only builds a string and hands
        it to :meth:`set_status`, which marshals to the main thread. Unknown
        kinds are ignored on purpose — ``EVENT_KINDS`` is documented as additive.
        """
        if kind == "start":
            self.set_status(f"start: role={detail.get('role')}")
        elif kind == "connect":
            self.set_status(
                "handshaking: SYN shown as QR"
                if detail.get("is_initiator")
                else "handshaking: waiting for the peer's SYN"
            )
        elif kind == "handshake":
            if detail.get("established"):
                self.set_status(f"ESTABLISHED after {detail.get('rounds')} round(s)")
            else:
                self.set_status("handshake FAILED (timed out)")
        elif kind == "sent":
            self.set_status(f"sent #{detail.get('msg_id')}: {detail.get('text')}")
        elif kind == "received":
            self.set_status(f"recv #{detail.get('msg_id')}: {detail.get('text')}")
        elif kind == "match":
            self.set_status(
                f"messages {detail.get('received')}/{detail.get('expected')} - "
                f"{'MATCH' if detail.get('match') else 'MISMATCH'}"
            )
        elif kind == "closing":
            self.set_status("closing the session...")
        elif kind == "closed":
            self.set_status(
                f"CLOSED after {detail.get('rounds')} round(s)"
                if detail.get("closed")
                else "close FAILED (timed out)"
            )
        elif kind == "error":
            self.set_status(f"error [{detail.get('stage')}]: {detail.get('message')}")
        elif kind == "done":
            self.set_status(self.format_summary(detail.get("result")))

    @staticmethod
    def format_summary(result) -> str:
        """Render a :class:`~photontcp.optical.peer.PeerResult` as the summary line.

        Static and pure so the exact wording can be asserted without a UI: it is
        the ``established / MATCH / closed / PASS·FAIL`` line the milestone asks
        the app to show after a run.
        """
        if result is None:
            return "session ended (no result)"
        return (
            f"{'PASS' if result.passed else 'FAIL'} | "
            f"established={result.established} | "
            f"{'MATCH' if result.messages_match else 'MISMATCH'} "
            f"({len(result.received)}/{len(result.expected)}) | "
            f"closed={result.closed} | wall={result.wall_seconds:.1f}s"
            + (" | TIMED OUT" if result.timed_out else "")
        )

    def on_stop(self, *_args) -> None:
        """Stop button handler: cancel the drive at the channel boundary.

        ``App.on_stop`` (the Kivy lifecycle hook fired when the window closes)
        has the same name and no arguments, so this doubles as the shutdown hook:
        either way the running drive is cancelled and the devices are released.
        """
        channel = self._channel
        if channel is not None:
            self.set_status("stopping...")
            channel.cancel()

    def _teardown(self) -> None:
        """Release devices and unlock the UI. Main thread only.

        ``run_peer`` already closed the channel (its ``finally``), which closes
        the display and camera; this disconnects the preview, drops references,
        and re-enables the settings row.
        """
        preview, self._preview = self._preview, None
        if preview is not None:
            try:  # pragma: no cover - needs camera4kivy
                preview.disconnect_camera()
            except Exception:
                pass
            self.preview_slot.clear_widgets()
        self._channel = None
        self._display = None
        self._camera = None
        self._worker = None
        self._set_running(False)


def main() -> None:
    """Run the app. The entry point for both ``python -m`` and buildozer's ``main.py``."""
    PhotonTCPApp().run()


if __name__ == "__main__":
    main()
