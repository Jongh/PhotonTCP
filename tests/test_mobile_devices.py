"""모바일(Kivy) 어댑터 계약 테스트 (M11-T09, 완료 기준 6).

두 갈래를 본다:

1. **가드 경로** — Kivy 미설치 데스크톱에서 ``import photontcp.mobile`` 이
   성공하고 ``KivyDisplay``/``KivyCamera``/``PhotonTCPApp``/``main`` 이
   ``None`` 으로 노출되며 ``__all__`` 이 비어 있다. 이 환경에서 **무조건**
   단언한다(skip 하지 않는다).
2. **어댑터 계약** — ``issubclass(KivyDisplay, DisplaySink)``,
   ``issubclass(KivyCamera, CameraSource)`` 와 ``show``/``read`` 의 실동작.
   Kivy 가 없으면 최소 스텁(``kivy.clock`` / ``kivy.graphics.texture``)을
   ``sys.modules`` 에 심어 **실제** ``photontcp/mobile/kivy_devices.py`` 를
   불러온다 — 검사 대상은 언제나 실물 모듈이지 스텁이 아니다. Kivy 가 실제로
   설치돼 있으면 스텁 없이 그대로 실물을 쓴다.

**격리**: 스텁 주입·모듈 로드는 전부 fixture 안에서 일어나고, 끝나면
``sys.modules`` 와 ``photontcp.mobile`` 패키지 속성이 원상복구된다.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
import types

import numpy as np
import pytest

from photontcp.optical.devices import CameraSource, DisplaySink


KIVY_INSTALLED = importlib.util.find_spec("kivy") is not None

_STUBBED_MODULES = ("kivy", "kivy.clock", "kivy.graphics", "kivy.graphics.texture")


# --------------------------------------------------------------------------- #
# 1. 가드 경로 (Kivy 유무와 무관하게 정확한 쪽을 단언)
# --------------------------------------------------------------------------- #


def test_photontcp_mobile_imports_without_kivy() -> None:
    """``import photontcp.mobile`` 은 Kivy 부재에서도 하드 실패하지 않는다."""
    module = importlib.import_module("photontcp.mobile")
    assert module is not None


def test_guarded_names_are_none_when_kivy_is_absent() -> None:
    """Kivy 미설치면 네 이름 모두 ``None`` 이고 ``__all__`` 은 비어 있다."""
    mobile = importlib.import_module("photontcp.mobile")

    if KIVY_INSTALLED:
        pytest.skip("Kivy is installed here; the absent-guard branch cannot be observed")

    for name in ("KivyDisplay", "KivyCamera", "PhotonTCPApp", "main"):
        assert hasattr(mobile, name), f"{name} must exist even without Kivy"
        assert getattr(mobile, name) is None, f"{name} must be None without Kivy"
    assert mobile.__all__ == []


def test_kivy_devices_module_needs_kivy() -> None:
    """가드가 감싸는 실제 실패는 ``kivy`` ModuleNotFoundError 다 (설계된 거동)."""
    if KIVY_INSTALLED:
        pytest.skip("Kivy is installed here; the import does not fail")

    with pytest.raises(ImportError):
        importlib.import_module("photontcp.mobile.kivy_devices")


def test_exported_names_are_usable_when_kivy_is_present() -> None:
    """Kivy 가 있으면 반대로 네 이름이 실물이고 ``__all__`` 에 실린다."""
    if not KIVY_INSTALLED:
        pytest.skip("Kivy is not installed here; the present branch cannot be observed")

    mobile = importlib.import_module("photontcp.mobile")
    assert set(mobile.__all__) == {"KivyDisplay", "KivyCamera", "PhotonTCPApp", "main"}
    for name in mobile.__all__:
        assert getattr(mobile, name) is not None


# --------------------------------------------------------------------------- #
# 2. Kivy 스텁 + 실물 어댑터 모듈 로드
# --------------------------------------------------------------------------- #


class FakeTexture:
    """``Texture.create`` 가 돌려주는 것의 최소 대역 — GL 컨텍스트 불필요."""

    def __init__(self, size, colorfmt):
        self.size = size
        self.colorfmt = colorfmt
        self.buffer: bytes | None = None
        self.bufferfmt: str | None = None
        self.flipped = 0

    def blit_buffer(self, buffer, colorfmt=None, bufferfmt=None):  # noqa: D102
        self.buffer = bytes(buffer)
        self.colorfmt = colorfmt
        self.bufferfmt = bufferfmt

    def flip_vertical(self):  # noqa: D102
        self.flipped += 1


def _make_kivy_stub() -> dict[str, types.ModuleType]:
    """``kivy_devices`` 가 import 하는 최소 표면만 갖춘 가짜 kivy 패키지."""
    kivy = types.ModuleType("kivy")
    kivy.__path__ = []  # 서브모듈을 가진 패키지로 보이게 한다

    clock_mod = types.ModuleType("kivy.clock")

    class _Clock:
        @staticmethod
        def schedule_once(callback, timeout=0):
            # 실제 Kivy 는 메인 스레드로 미룬다. 스텁은 아무것도 하지 않아,
            # "show 가 인라인 렌더를 하지 않는다" 를 관찰 가능하게 만든다.
            return None

    clock_mod.Clock = _Clock

    graphics_mod = types.ModuleType("kivy.graphics")
    graphics_mod.__path__ = []
    texture_mod = types.ModuleType("kivy.graphics.texture")

    class _Texture:
        @staticmethod
        def create(size=None, colorfmt=None, **_kwargs):
            return FakeTexture(size, colorfmt)

    texture_mod.Texture = _Texture

    kivy.clock = clock_mod
    kivy.graphics = graphics_mod
    graphics_mod.texture = texture_mod

    return {
        "kivy": kivy,
        "kivy.clock": clock_mod,
        "kivy.graphics": graphics_mod,
        "kivy.graphics.texture": texture_mod,
    }


@pytest.fixture(scope="module")
def kivy_devices():
    """실제 ``photontcp.mobile.kivy_devices`` 모듈을 준다 (필요하면 스텁 위에서).

    Kivy 가 설치돼 있으면 스텁을 심지 않고 그대로 import 한다. 없으면 최소
    스텁을 ``sys.modules`` 에 넣고 실물 모듈을 불러온 뒤, 종료 시
    ``sys.modules`` 항목과 ``photontcp.mobile`` 패키지 속성을 정확히 원복한다.
    """
    if KIVY_INSTALLED:
        yield importlib.import_module("photontcp.mobile.kivy_devices")
        return

    saved = {name: sys.modules.get(name) for name in _STUBBED_MODULES}
    sys.modules.update(_make_kivy_stub())
    saved_devices = sys.modules.get("photontcp.mobile.kivy_devices")
    mobile_pkg = importlib.import_module("photontcp.mobile")
    had_attr = hasattr(mobile_pkg, "kivy_devices")
    try:
        module = importlib.import_module("photontcp.mobile.kivy_devices")
        yield module
    finally:
        # 스텁으로 로드된 모듈이 다른 테스트에 남지 않게 전부 되돌린다.
        if saved_devices is None:
            sys.modules.pop("photontcp.mobile.kivy_devices", None)
        else:
            sys.modules["photontcp.mobile.kivy_devices"] = saved_devices
        if not had_attr:
            try:
                delattr(mobile_pkg, "kivy_devices")
            except AttributeError:  # pragma: no cover - defensive
                pass
        for name, original in saved.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


@pytest.fixture
def KivyDisplay(kivy_devices):
    return kivy_devices.KivyDisplay


@pytest.fixture
def KivyCamera(kivy_devices):
    return kivy_devices.KivyCamera


class FakeWidget:
    """``texture`` 속성만 가진 위젯 대역."""

    def __init__(self):
        self.texture = None
        self.canvas = None


class RecordingScheduler:
    """``Clock.schedule_once`` 대역: 콜백을 모아두고 명시적으로 실행한다."""

    def __init__(self):
        self.calls: list = []

    def __call__(self, callback, timeout=0):
        self.calls.append(callback)

    def run_all(self):
        pending, self.calls = self.calls, []
        for callback in pending:
            callback(0.0)
        return len(pending)


# --------------------------------------------------------------------------- #
# 3. 어댑터 계약 — 타입
# --------------------------------------------------------------------------- #


def test_display_and_camera_satisfy_the_device_contracts(KivyDisplay, KivyCamera) -> None:
    """완료 기준 6 의 두 ``issubclass`` 단언."""
    assert issubclass(KivyDisplay, DisplaySink)
    assert issubclass(KivyCamera, CameraSource)


def test_adapters_are_concrete(KivyDisplay, KivyCamera) -> None:
    """추상 메서드가 전부 구현돼 있어 인스턴스화가 가능하다."""
    display = KivyDisplay(FakeWidget(), scheduler=RecordingScheduler())
    camera = KivyCamera()
    assert isinstance(display, DisplaySink)
    assert isinstance(camera, CameraSource)
    display.close()
    camera.close()


# --------------------------------------------------------------------------- #
# 4. KivyDisplay.show — 예약만, 마지막 프레임만
# --------------------------------------------------------------------------- #


def _qr_like(value: int, size: int = 8) -> np.ndarray:
    return np.full((size, size), value, dtype=np.uint8)


def test_show_schedules_instead_of_rendering_inline(KivyDisplay) -> None:
    """``show`` 는 GL 을 만지지 않고 렌더를 예약만 한다."""
    widget = FakeWidget()
    scheduler = RecordingScheduler()
    display = KivyDisplay(widget, scheduler=scheduler, texture_factory=FakeTexture)

    display.show(_qr_like(255))

    assert widget.texture is None, "show() must not render inline"
    assert len(scheduler.calls) == 1

    scheduler.run_all()
    assert isinstance(widget.texture, FakeTexture)


def test_render_shows_the_last_frame_only(KivyDisplay) -> None:
    """프레임 교체 의미: 여러 번 show 해도 렌더는 마지막 것 한 장."""
    widget = FakeWidget()
    scheduler = RecordingScheduler()
    display = KivyDisplay(widget, scheduler=scheduler, texture_factory=FakeTexture)

    display.show(_qr_like(0))
    display.show(_qr_like(128))
    last = _qr_like(255)
    display.show(last)

    # 예약은 한 번만 쌓인다 (프레임마다 콜백이 쌓이지 않는다).
    assert len(scheduler.calls) == 1
    assert scheduler.run_all() == 1

    assert widget.texture.buffer == last.tobytes()
    assert widget.texture.size == (last.shape[1], last.shape[0])


def test_render_flips_vertically_by_default(KivyDisplay) -> None:
    """텍스처 원점이 bottom-left 이므로 기본 flip 이 걸린다 (끌 수 있다)."""
    widget = FakeWidget()
    scheduler = RecordingScheduler()
    KivyDisplay(widget, scheduler=scheduler, texture_factory=FakeTexture).show(
        _qr_like(255)
    )
    scheduler.run_all()
    assert widget.texture.flipped == 1

    widget2 = FakeWidget()
    scheduler2 = RecordingScheduler()
    KivyDisplay(
        widget2,
        scheduler=scheduler2,
        texture_factory=FakeTexture,
        flip_vertical=False,
    ).show(_qr_like(255))
    scheduler2.run_all()
    assert widget2.texture.flipped == 0


def test_show_after_close_is_ignored(KivyDisplay) -> None:
    """닫힌 디스플레이는 조용히 무시한다 (채널이 죽으면 안 된다)."""
    widget = FakeWidget()
    scheduler = RecordingScheduler()
    display = KivyDisplay(widget, scheduler=scheduler, texture_factory=FakeTexture)

    display.close()
    display.show(_qr_like(255))

    assert scheduler.calls == []
    assert widget.texture is None
    display.close()  # 멱등


# --------------------------------------------------------------------------- #
# 5. KivyCamera.read — 최신 1장, 큐 없음, 색 순서
# --------------------------------------------------------------------------- #


def test_read_returns_none_when_nothing_was_ever_captured(KivyCamera) -> None:
    """프레임이 한 번도 없었으면 ``None`` ("실패한 grab" 과 같은 신호)."""
    camera = KivyCamera()
    try:
        assert camera.read(timeout=0) is None
        assert camera.read(timeout=0) is None
    finally:
        camera.close()


def test_read_converts_rgba_to_bgr(KivyCamera) -> None:
    """RGBA 로 들어온 프레임이 ``HxWx3`` BGR 로 나온다 (alpha 제거)."""
    camera = KivyCamera()
    try:
        rgba = np.zeros((2, 3, 4), dtype=np.uint8)
        rgba[..., 0] = 10  # R
        rgba[..., 1] = 20  # G
        rgba[..., 2] = 30  # B
        rgba[..., 3] = 255  # A
        camera.submit_frame(rgba, (3, 2), "rgba")

        frame = camera.read(timeout=0)
        assert frame is not None
        assert frame.shape == (2, 3, 3)
        assert frame.dtype == np.uint8
        assert (frame[..., 0] == 30).all(), "channel 0 must be Blue"
        assert (frame[..., 1] == 20).all(), "channel 1 must be Green"
        assert (frame[..., 2] == 10).all(), "channel 2 must be Red"
    finally:
        camera.close()


def test_read_accepts_a_packed_luminance_buffer(KivyCamera) -> None:
    """평평한 버퍼 + ``(width, height)`` 도 2D gray 로 복원된다."""
    camera = KivyCamera()
    try:
        gray = np.arange(6, dtype=np.uint8).reshape(2, 3)
        camera.submit_frame(gray.tobytes(), (3, 2), "luminance")

        frame = camera.read(timeout=0)
        assert frame is not None
        assert frame.shape == (2, 3)
        assert np.array_equal(frame, gray)
    finally:
        camera.close()


def test_read_reuses_the_same_object_when_no_new_frame(KivyCamera) -> None:
    """새 프레임이 없으면 **직전 배열을 같은 객체로** 재반환한다."""
    camera = KivyCamera()
    try:
        camera.submit_frame(np.full((2, 2), 7, dtype=np.uint8), (2, 2), "luminance")
        first = camera.read(timeout=0)
        assert first is not None

        again = camera.read(timeout=0)
        assert again is first, "no new frame must re-return the identical object"
        assert camera.read(timeout=0) is first
    finally:
        camera.close()


def test_consecutive_submits_yield_only_the_latest(KivyCamera) -> None:
    """큐가 없다: 연속 submit 후 read 한 번이면 최신 1장만 나온다."""
    camera = KivyCamera()
    try:
        camera.submit_frame(np.full((2, 2), 1, dtype=np.uint8), (2, 2), "luminance")
        camera.submit_frame(np.full((2, 2), 2, dtype=np.uint8), (2, 2), "luminance")
        camera.submit_frame(np.full((2, 2), 3, dtype=np.uint8), (2, 2), "luminance")

        frame = camera.read(timeout=0)
        assert frame is not None
        assert (frame == 3).all(), "read() must yield the newest submission"

        # 이전 두 장은 버려졌으므로 다음 read 는 방금 그 객체를 되돌려준다.
        assert camera.read(timeout=0) is frame
    finally:
        camera.close()


def test_read_after_close_is_none(KivyCamera) -> None:
    """닫힌 카메라는 즉시 ``None`` (블로킹 read 도 풀린다)."""
    camera = KivyCamera()
    camera.submit_frame(np.full((2, 2), 9, dtype=np.uint8), (2, 2), "luminance")
    camera.close()

    assert camera.read(timeout=0) is None
    assert camera.read(timeout=None) is None
    camera.submit_frame(np.full((2, 2), 9, dtype=np.uint8), (2, 2), "luminance")
    assert camera.read(timeout=0) is None
    camera.close()  # 멱등


def test_unusable_buffer_keeps_the_previous_good_frame(KivyCamera) -> None:
    """짧은/알 수 없는 버퍼는 실패한 grab 처럼 다뤄지고 직전 프레임이 남는다."""
    camera = KivyCamera()
    try:
        camera.submit_frame(np.full((2, 2), 5, dtype=np.uint8), (2, 2), "luminance")
        good = camera.read(timeout=0)
        assert good is not None

        camera.submit_frame(b"\x00\x01", (4, 4), "luminance")  # 너무 짧음
        assert camera.read(timeout=0) is good

        camera.submit_frame(b"\x00" * 16, (4, 4), "not-a-format")
        assert camera.read(timeout=0) is good
    finally:
        camera.close()


def test_submitted_buffer_is_owned_so_camera_recycling_cannot_tear_a_frame(
    KivyCamera,
) -> None:
    """제출된 버퍼를 카메라가 **재사용**해도 읽은 프레임이 찢어지지 않는다.

    변환이 ``read`` 로 미뤄져 있어 submit~read 사이에 어댑터는 남의 버퍼를 참조만
    한다. CameraX/camera4kivy 가 분석 버퍼를 콜백 반환 직후 재활용하면 그 사이에
    내용이 덮여 **조용한 디코드 실패**로 나타난다(M11-review 권장 3). 기본
    ``copy_on_submit=True`` 가 콜백 스레드에서 memcpy 한 장으로 그것을 막는다.

    재사용을 흉내 낸다 — 제출한 뒤 **같은 버퍼를 제자리에서 덮어쓴다**.
    """
    for raw in (bytearray(b"\x11" * 4), np.full((2, 2), 0x11, dtype=np.uint8)):
        camera = KivyCamera()
        try:
            camera.submit_frame(raw, (2, 2), "luminance")
            raw[:] = (  # 카메라가 버퍼를 재활용해 다음 프레임을 써 넣는다
                bytearray(b"\xEE" * 4)
                if isinstance(raw, bytearray)
                else np.full((2, 2), 0xEE, dtype=np.uint8)
            )
            frame = camera.read(timeout=0)
            assert frame is not None
            assert (frame == 0x11).all(), (
                f"{type(raw).__name__} 버퍼 재사용이 이미 제출된 프레임을 덮었다 — "
                "submit 시점에 소유권을 가져오지 못했다"
            )
        finally:
            camera.close()

    # 나열된 세 타입 밖의 버퍼(array.array)도 같은 보증을 받는다 — 소유권이 타입
    # 조건부면 클래스 docstring 의 "takes ownership" 이 사실보다 넓은 주장이 된다.
    import array as _array

    camera = KivyCamera()
    try:
        raw = _array.array("B", [0x11] * 4)
        camera.submit_frame(raw, (2, 2), "luminance")
        for i in range(4):
            raw[i] = 0xEE
        frame = camera.read(timeout=0)
        assert frame is not None and (frame == 0x11).all(), (
            "array.array 버퍼 재사용이 이미 제출된 프레임을 덮었다"
        )
    finally:
        camera.close()

    # 옵트아웃하면 참조만 잡으므로 재사용이 그대로 보인다(계약의 다른 쪽 끝).
    camera = KivyCamera(copy_on_submit=False)
    try:
        raw = bytearray(b"\x11" * 4)
        camera.submit_frame(raw, (2, 2), "luminance")
        raw[:] = bytearray(b"\xEE" * 4)
        frame = camera.read(timeout=0)
        assert frame is not None and (frame == 0xEE).all()
    finally:
        camera.close()


def test_source_is_connected_and_disconnected(KivyCamera) -> None:
    """``source`` 를 주면 생성 시 connect, close 시 disconnect 된다."""

    class FakeSource:
        def __init__(self):
            self.callback = None
            self.disconnected = False

        def connect(self, callback):
            self.callback = callback

        def disconnect(self):
            self.disconnected = True

    source = FakeSource()
    camera = KivyCamera(source)
    assert source.callback is not None

    # 소스가 준 콜백이 곧 submit_frame 이다.
    source.callback(np.full((2, 2), 4, dtype=np.uint8), (2, 2), "luminance")
    frame = camera.read(timeout=0)
    assert frame is not None and (frame == 4).all()

    camera.close()
    assert source.disconnected
