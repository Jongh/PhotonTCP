"""``photontcp/mobile/app.py`` 상태 전이 테스트 (M12-T02, 완료 기준 3).

무엇을 고정하는가
=================

앱 셸에서 **UI 없이 관찰 가능한** 네 갈래를 방전한다:

1. ``PhotonTCPApp.format_summary()`` 가 ``PASS``/``FAIL`` 양쪽 요약 줄을 **문자
   그대로** 렌더한다(정적·순수 함수).
2. ``_read_settings()`` 가 ``""``·``"abc"``·``"0"``·``"-1"`` 네 입력에서
   **``run_peer`` 가 거부하지 않는 ``hold``** 를 낸다. ``run_peer`` 는
   ``hold <= 0`` 을 ``ValueError`` 로 거부하므로(M11-review 권장 4의 프로토콜
   쪽 절반), 여기서는 산출된 ``hold`` 를 **실제 ``run_peer`` 에 넣어** 인자
   검증을 통과하는지까지 본다 — ``hold > 0`` 단언만 두면 두 쪽의 계약이
   갈라져도 초록이 유지된다.
3. ``on_event()`` 가 ``EVENT_KINDS`` **전 종류** + 미지의 kind 에서 예외를
   내지 않는다.
4. ``_make_preview()`` 가 ``camera4kivy`` 부재에서 ``(None, 사유 문자열)`` 을
   낸다.

거기에 더해 **마샬링 경로**(모든 UI 변경이 ``Clock.schedule_once`` 를 거친다)를
관찰 가능한 형태로 단언한다 — ``set_status`` 가 위젯을 인라인으로 건드리면
붉어진다.

어떻게 도는가 (Kivy 유무와 무관하게)
====================================

``tests/test_mobile_devices.py`` 의 선례를 따른다. Kivy 가 설치돼 있으면 스텁
없이 실물을 import 하고, 없으면 최소 스텁을 ``sys.modules`` 에 심어 **실제**
``photontcp/mobile/app.py`` 를 불러온다. 검사 대상은 어느 쪽에서도 실물
모듈이지 스텁이 아니다.

**격리**: 스텁 주입·모듈 로드는 fixture 안에서만 일어난다. ``sys.modules`` 만
되돌리는 것으로는 부족하다 — import 는 부모 패키지에도 서브모듈을 **속성으로**
심으므로(``photontcp.mobile.app = <module>``) 그 자리까지 복원한다
(``tests/test_decode_backend.py`` 의 ``fresh_qr_import`` 가 같은 함정을 적어
두었다).
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
import types

import pytest

from photontcp.optical.peer import EVENT_KINDS, PeerResult, run_peer


KIVY_INSTALLED = importlib.util.find_spec("kivy") is not None

_STUBBED_MODULES = (
    "kivy",
    "kivy.app",
    "kivy.clock",
    "kivy.graphics",
    "kivy.graphics.texture",
    "kivy.uix",
    "kivy.uix.boxlayout",
    "kivy.uix.button",
    "kivy.uix.image",
    "kivy.uix.label",
    "kivy.uix.spinner",
    "kivy.uix.textinput",
)


# --------------------------------------------------------------------------- #
# 1. 최소 Kivy 스텁 (Kivy 미설치 환경에서만 쓰인다)
# --------------------------------------------------------------------------- #


class _StubWidget:
    """``app.py`` 가 위젯에 요구하는 표면만 갖춘 대역.

    생성자 키워드를 그대로 속성으로 심어(``text``/``disabled`` 등) 실제
    ``build()`` 가 세우는 트리를 스텁 위에서도 그대로 재현한다.
    """

    def __init__(self, **kwargs):
        self.children: list = []
        self.disabled = False
        self.text = ""
        self.texture = None
        for key, value in kwargs.items():
            setattr(self, key, value)

    def add_widget(self, widget):  # noqa: D102
        self.children.append(widget)

    def clear_widgets(self):  # noqa: D102
        self.children = []

    def bind(self, **kwargs):  # noqa: D102
        # 배선을 관찰 가능하게 기록만 한다 (기존 동작은 그대로 no-op).
        if not hasattr(self, "bound_events"):
            self.bound_events = {}
        self.bound_events.update(kwargs)
        return None


class _StubApp:
    """``kivy.app.App`` 대역 — ``PhotonTCPApp`` 이 상속하므로 필요하다."""

    def __init__(self, **_kwargs):
        self.root = None

    def run(self):  # pragma: no cover - 테스트는 메인 루프를 돌리지 않는다
        raise AssertionError("the test suite must never enter the Kivy main loop")

    def stop(self):  # pragma: no cover - defensive
        return None


class _SyncClock:
    """``Clock`` 대역: ``schedule_once`` 를 **동기 실행**한다.

    실제 Kivy 는 다음 프레임으로 미루지만, 테스트에서 미루면 마샬링 뒤의 상태가
    영영 관찰되지 않는다. 동기로 두어 ``set_status`` → 라벨까지의 경로 전체가
    한 호출 안에서 보이게 한다(경로가 *존재하는지* 는
    :func:`test_status_updates_go_through_the_clock` 이 따로 본다).
    """

    @staticmethod
    def schedule_once(callback, timeout=0):
        callback(0.0)


def _make_kivy_stub() -> dict[str, types.ModuleType]:
    """``app.py`` + ``kivy_devices.py`` 가 import 하는 최소 표면만 만든다."""

    def _module(name: str, package: bool = False) -> types.ModuleType:
        mod = types.ModuleType(name)
        if package:
            mod.__path__ = []
        return mod

    kivy = _module("kivy", package=True)

    app_mod = _module("kivy.app")
    app_mod.App = _StubApp

    clock_mod = _module("kivy.clock")
    clock_mod.Clock = _SyncClock

    graphics_mod = _module("kivy.graphics", package=True)
    texture_mod = _module("kivy.graphics.texture")

    class _Texture:
        @staticmethod
        def create(size=None, colorfmt=None, **_kwargs):
            return _StubWidget(size=size, colorfmt=colorfmt)

    texture_mod.Texture = _Texture

    uix = _module("kivy.uix", package=True)
    widget_modules = {
        "kivy.uix.boxlayout": "BoxLayout",
        "kivy.uix.button": "Button",
        "kivy.uix.image": "Image",
        "kivy.uix.label": "Label",
        "kivy.uix.spinner": "Spinner",
        "kivy.uix.textinput": "TextInput",
    }

    modules = {
        "kivy": kivy,
        "kivy.app": app_mod,
        "kivy.clock": clock_mod,
        "kivy.graphics": graphics_mod,
        "kivy.graphics.texture": texture_mod,
        "kivy.uix": uix,
    }
    for mod_name, cls_name in widget_modules.items():
        mod = _module(mod_name)
        setattr(mod, cls_name, type(cls_name, (_StubWidget,), {}))
        modules[mod_name] = mod
        setattr(uix, mod_name.rpartition(".")[2], mod)

    kivy.app = app_mod
    kivy.clock = clock_mod
    kivy.graphics = graphics_mod
    kivy.uix = uix
    graphics_mod.texture = texture_mod

    return modules


# --------------------------------------------------------------------------- #
# 2. 실물 app 모듈 로드 fixture
# --------------------------------------------------------------------------- #

_LOADED_SUBMODULES = ("photontcp.mobile.app", "photontcp.mobile.kivy_devices")


@pytest.fixture(scope="module")
def app_module():
    """실제 ``photontcp.mobile.app`` 모듈을 준다 (필요하면 스텁 위에서).

    Kivy 가 설치돼 있으면 그대로 import 한다. 없으면 스텁을 심고 실물을 불러온
    뒤, ``sys.modules`` 항목과 **부모 패키지에 심긴 서브모듈 속성**을 둘 다
    원복한다.
    """
    if KIVY_INSTALLED:
        yield importlib.import_module("photontcp.mobile.app")
        return

    saved_kivy = {name: sys.modules.get(name) for name in _STUBBED_MODULES}
    saved_sub = {name: sys.modules.get(name) for name in _LOADED_SUBMODULES}
    mobile_pkg = importlib.import_module("photontcp.mobile")
    saved_attrs = {
        name.rpartition(".")[2]: getattr(mobile_pkg, name.rpartition(".")[2], None)
        for name in _LOADED_SUBMODULES
    }
    had_attr = {
        name.rpartition(".")[2]: hasattr(mobile_pkg, name.rpartition(".")[2])
        for name in _LOADED_SUBMODULES
    }

    sys.modules.update(_make_kivy_stub())
    try:
        yield importlib.import_module("photontcp.mobile.app")
    finally:
        for name, original in saved_sub.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original
        for attr, original in saved_attrs.items():
            if had_attr[attr]:
                setattr(mobile_pkg, attr, original)
            else:
                try:
                    delattr(mobile_pkg, attr)
                except AttributeError:  # pragma: no cover - defensive
                    pass
        for name, original in saved_kivy.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


class _Field:
    """설정 행 위젯 대역 — ``_read_settings`` 가 읽는 것은 ``.text`` 뿐이다."""

    def __init__(self, text):
        self.text = text


@pytest.fixture
def app(app_module, monkeypatch):
    """``build()`` 없이 설정 행/상태 줄만 갖춘 ``PhotonTCPApp`` 인스턴스.

    ``build()`` 는 실제 Kivy 에서 GL 컨텍스트(Window)를 요구하므로 여기서는
    부르지 않는다 — 대신 ``_read_settings``/``on_event`` 가 실제로 읽고 쓰는
    속성만 심는다. 상태 줄 갱신은 ``Clock`` 을 거치므로, 어느 환경에서도
    동기 실행되도록 모듈 전역 ``Clock`` 을 :class:`_SyncClock` 으로 바꾼다.
    """
    monkeypatch.setattr(app_module, "Clock", _SyncClock)
    instance = app_module.PhotonTCPApp()
    instance.role_spinner = _Field("sender")
    instance.scale_input = _Field("8")
    instance.hold_input = _Field("0.4")
    instance.facing_spinner = _Field("back")
    instance.status_label = _Field("idle - press Start")
    return instance


# --------------------------------------------------------------------------- #
# 3. format_summary — PASS / FAIL 양쪽을 문자 그대로
# --------------------------------------------------------------------------- #


def test_format_summary_renders_a_passing_result(app_module) -> None:
    """네 필드가 모두 참인 결과는 ``PASS`` 요약 줄이 된다."""
    result = PeerResult(
        role="sender",
        established=True,
        messages_match=True,
        closed=True,
        wall_seconds=12.34,
        passed=True,
        sent=["a", "b"],
        received=["x", "y"],
        expected=["x", "y"],
    )
    assert app_module.PhotonTCPApp.format_summary(result) == (
        "PASS | established=True | MATCH (2/2) | closed=True | wall=12.3s"
    )


def test_format_summary_renders_a_failing_result(app_module) -> None:
    """실패는 ``FAIL``·``MISMATCH``·부분 카운트·``TIMED OUT`` 까지 그대로 보인다."""
    result = PeerResult(
        role="receiver",
        established=True,
        messages_match=False,
        closed=False,
        wall_seconds=60.0,
        passed=False,
        received=["x"],
        expected=["x", "y"],
        timed_out=True,
    )
    assert app_module.PhotonTCPApp.format_summary(result) == (
        "FAIL | established=True | MISMATCH (1/2) | closed=False | "
        "wall=60.0s | TIMED OUT"
    )


def test_format_summary_without_a_result(app_module) -> None:
    """결과가 없는 종료(예: 취소)도 사람이 읽는 한 줄로 끝난다."""
    assert app_module.PhotonTCPApp.format_summary(None) == "session ended (no result)"


# --------------------------------------------------------------------------- #
# 4. _read_settings — run_peer 가 거부하지 않는 hold
# --------------------------------------------------------------------------- #


class _Sentinel(Exception):
    """``run_peer`` 가 인자 검증을 통과했음을 알리는 표식."""


class _ExplodingChannel:
    """첫 전송에서 :class:`_Sentinel` 을 던지는 ``Channel`` 대역.

    ``run_peer`` 의 인자 검증(``role``/``hold``/``messages``)은 채널을 만지기
    **전에** 끝난다. 따라서 이 채널로 부른 ``run_peer`` 가 ``ValueError`` 대신
    ``_Sentinel`` 을 내면 "검증을 통과했다" 는 뜻이고, 드라이브 본체는 한
    라운드도 돌지 않으므로 테스트가 느려지지 않는다.
    """

    def __init__(self) -> None:
        self.closed = False

    def start(self) -> None:  # pragma: no cover - run_peer 는 start 를 부르지 않는다
        raise _Sentinel

    def send_frame(self, frame: bytes) -> None:
        raise _Sentinel

    def recv_frame(self, timeout=None):  # pragma: no cover - sender 경로에선 안 온다
        raise _Sentinel

    def close(self) -> None:
        self.closed = True


def test_run_peer_still_rejects_a_non_positive_hold() -> None:
    """이 파일의 나머지 단언이 기대는 성질을 먼저 못 박는다.

    ``run_peer`` 가 ``hold <= 0`` 을 거부하지 않게 되면
    :func:`test_read_settings_never_yields_a_hold_run_peer_rejects` 가 조용히
    공허해지므로, 그 전제를 여기서 직접 단언한다.
    """
    for bad in (0.0, -1.0):
        with pytest.raises(ValueError, match="hold must be > 0"):
            run_peer(_ExplodingChannel(), "sender", hold=bad)


@pytest.mark.parametrize("typed", ["", "abc", "0", "-1"])
def test_read_settings_never_yields_a_hold_run_peer_rejects(app, typed) -> None:
    """설정 행이 낼 수 있는 ``hold`` 는 언제나 ``run_peer`` 가 받는 값이다.

    빈 문자열/비수치는 기본값으로, ``"0"``·``"-1"`` 은 ``MIN_HOLD`` 로 올려
    잡힌다. 단언은 두 겹이다 — ``hold >= MIN_HOLD > 0`` 이라는 값 자체와,
    그 값으로 ``run_peer`` 가 실제로 인자 검증을 통과한다는 것.
    """
    app.hold_input.text = typed

    _role, _scale, hold, _facing = app._read_settings()

    module = sys.modules[type(app).__module__]
    assert hold >= module.MIN_HOLD > 0, f"{typed!r} produced hold={hold!r}"

    channel = _ExplodingChannel()
    with pytest.raises(_Sentinel):
        run_peer(channel, "sender", hold=hold)
    assert channel.closed, "run_peer must still close the channel on the way out"


def test_read_settings_keeps_a_usable_typed_hold(app) -> None:
    """정상 입력은 그대로 통과한다 (클램프가 값을 덮어쓰지 않는다)."""
    app.hold_input.text = "0.75"
    assert app._read_settings()[2] == pytest.approx(0.75)


@pytest.mark.parametrize(
    "typed, expected", [("", None), ("abc", None), ("0", 1), ("-4", 1), ("12", 12)]
)
def test_read_settings_scale_is_always_a_positive_int(app, typed, expected) -> None:
    """``scale`` 도 같은 규율: 0/음수는 1 로, 비수치는 기본값으로."""
    module = sys.modules[type(app).__module__]
    app.scale_input.text = typed

    scale = app._read_settings()[1]

    assert scale == (module.DEFAULT_SCALE if expected is None else expected)
    assert scale >= 1


def test_read_settings_passes_role_and_facing_through(app) -> None:
    """role/facing 은 스피너 값 그대로 나온다 (드라이브가 받는 값이다)."""
    app.role_spinner.text = "receiver"
    app.facing_spinner.text = "front"
    role, _scale, _hold, facing = app._read_settings()
    assert (role, facing) == ("receiver", "front")


# --------------------------------------------------------------------------- #
# 5. on_event — 전 kind + 미지의 kind
# --------------------------------------------------------------------------- #

#: ``EVENT_KINDS`` 각 종류에 대해 ``run_peer`` 가 실제로 싣는 detail 모양.
_DETAILS: dict[str, dict] = {
    "start": {"role": "sender", "is_initiator": True},
    "connect": {"is_initiator": True},
    "handshake": {"established": True, "rounds": 3},
    "sent": {"msg_id": 1, "text": "hi over light"},
    "received": {"msg_id": 2, "text": "frame two"},
    "match": {"received": 2, "expected": 2, "match": True},
    "closing": {"is_initiator": True},
    "closed": {"closed": True, "rounds": 4},
    "error": {"stage": "handshake", "message": "timed out"},
    "done": {"result": PeerResult(passed=True, expected=["x"], received=["x"])},
}


def test_details_cover_every_event_kind() -> None:
    """``EVENT_KINDS`` 가 늘어나면 이 테이블이 먼저 붉어진다 (커버리지 가드)."""
    assert set(_DETAILS) == set(EVENT_KINDS)


@pytest.mark.parametrize("kind", EVENT_KINDS)
def test_on_event_renders_every_kind_without_raising(app, kind) -> None:
    """전 종류가 예외 없이 **비어 있지 않은** 상태 줄을 만든다."""
    app.status_label.text = ""
    app.on_event(kind, _DETAILS[kind])
    assert app.status_label.text, f"{kind!r} produced no status text"


@pytest.mark.parametrize("kind", EVENT_KINDS)
def test_on_event_survives_an_empty_detail(app, kind) -> None:
    """detail 이 비어도 죽지 않는다 (``.get`` 기반이라는 계약)."""
    app.on_event(kind, {})


@pytest.mark.parametrize("kind", ["", "unknown", "handshake_v2", "DONE"])
def test_on_event_ignores_unknown_kinds_silently(app, kind) -> None:
    """미지의 kind 는 예외도, 상태 줄 변경도 만들지 않는다.

    ``EVENT_KINDS`` 는 additive 로 문서화돼 있으므로, 새 kind 를 싣는 미래의
    ``run_peer`` 가 구버전 앱을 깨뜨려서는 안 된다.
    """
    app.status_label.text = "sentinel"
    app.on_event(kind, {"anything": 1})
    assert app.status_label.text == "sentinel"


def test_on_event_done_uses_the_summary_line(app) -> None:
    """``done`` 은 ``format_summary`` 와 **같은 문자열**을 상태 줄에 놓는다."""
    result = _DETAILS["done"]["result"]
    app.on_event("done", {"result": result})
    assert app.status_label.text == type(app).format_summary(result)


def test_on_event_handshake_failure_reads_as_a_failure(app) -> None:
    """분기의 반대쪽 — 실패한 핸드셰이크가 성공처럼 읽히지 않는다."""
    app.on_event("handshake", {"established": False, "rounds": None})
    assert "FAILED" in app.status_label.text
    app.on_event("closed", {"closed": False, "rounds": None})
    assert "FAILED" in app.status_label.text


# --------------------------------------------------------------------------- #
# 6. 마샬링 — 위젯은 Clock 을 거쳐서만 바뀐다
# --------------------------------------------------------------------------- #


class _RecordingClock:
    """콜백을 모아두고 명시적으로 실행하는 ``Clock`` 대역."""

    def __init__(self):
        self.calls: list = []

    def schedule_once(self, callback, timeout=0):
        self.calls.append(callback)

    def run_all(self) -> int:
        pending, self.calls = self.calls, []
        for callback in pending:
            callback(0.0)
        return len(pending)


def test_status_updates_go_through_the_clock(app_module, app, monkeypatch) -> None:
    """``set_status`` 는 위젯을 인라인으로 만지지 않고 메인 스레드로 넘긴다.

    ``run_peer`` 의 ``on_event`` 는 워커 스레드에서 불리므로, 인라인 변경은
    Kivy 에서 곧 GL 스레드 위반이다. 예약 전에는 라벨이 그대로여야 한다.
    """
    clock = _RecordingClock()
    monkeypatch.setattr(app_module, "Clock", clock)
    app.status_label.text = "before"

    app.on_event("start", {"role": "sender"})

    assert app.status_label.text == "before", "status must not be mutated inline"
    assert len(clock.calls) == 1
    assert clock.run_all() == 1
    assert app.status_label.text == "start: role=sender"


# --------------------------------------------------------------------------- #
# 7. _make_preview — camera4kivy 부재
# --------------------------------------------------------------------------- #


class _BlockCamera4Kivy:
    """``import camera4kivy`` 를 ImportError 로 만드는 meta_path finder."""

    def find_spec(self, fullname, path=None, target=None):  # noqa: D102
        if fullname == "camera4kivy" or fullname.startswith("camera4kivy."):
            raise ImportError("camera4kivy blocked by test (simulated absence)")
        return None


@pytest.fixture
def no_camera4kivy():
    """camera4kivy 부재를 시뮬레이션한다 (모듈 캐시 + import 경로 양쪽)."""
    finder = _BlockCamera4Kivy()
    saved = {
        name: mod
        for name, mod in sys.modules.items()
        if name == "camera4kivy" or name.startswith("camera4kivy.")
    }
    for name in saved:
        del sys.modules[name]
    sys.meta_path.insert(0, finder)
    try:
        yield
    finally:
        sys.meta_path.remove(finder)
        sys.modules.update(saved)


def test_make_preview_reports_a_reason_when_camera4kivy_is_absent(
    app_module, no_camera4kivy
) -> None:
    """``(None, 사유 문자열)`` — 데스크톱 경로이고, 이것은 오류가 아니다."""
    preview, error = app_module._make_preview(camera=None, facing="back")

    assert preview is None
    assert isinstance(error, str) and error
    assert "camera4kivy" in error


def test_make_preview_reason_is_what_the_status_line_would_show(
    app_module, no_camera4kivy
) -> None:
    """사유가 사람이 읽는 한 줄이라 상태 줄에 그대로 실릴 수 있다."""
    _preview, error = app_module._make_preview(camera=None, facing="front")
    assert "\n" not in error
    assert error.strip() == error


# --------------------------------------------------------------------------- #
# 8. build() — 위젯 트리 구성 + 폐기예정 속성 가드 (M13-T03)
# --------------------------------------------------------------------------- #

#: ``build()`` 가 부르는 위젯 생성자 이름들. 전부 대역으로 바꿔치기하므로 이
#: 절의 단언은 Kivy 설치 여부와 무관하게(그리고 GL 컨텍스트 없이) 돈다 —
#: 실물 Kivy 에서 ``build()`` 를 그대로 부르면 Window/GL 이 필요하다.
_WIDGET_NAMES = ("BoxLayout", "Button", "Image", "Label", "Spinner", "TextInput")


class _RecordingWidget(_StubWidget):
    """생성자 키워드를 **그대로 보관**하는 위젯 대역.

    ``qr_image`` 가 어떤 키워드로 만들어졌는지를 관찰 가능하게 만드는 것이
    목적이다 — 실물 ``kivy.uix.image.Image`` 는 ``fit_mode`` 와 폐기예정
    ``allow_stretch``/``keep_ratio`` 를 **셋 다** 속성으로 갖고 있어서,
    인스턴스를 들여다보는 것만으로는 무엇을 넘겼는지 알 수 없다.
    """

    def __init__(self, **kwargs):
        self.init_kwargs = dict(kwargs)
        super().__init__(**kwargs)


@pytest.fixture
def built(app_module, monkeypatch):
    """``build()`` 를 대역 위젯 위에서 돌리고 ``(app, root)`` 를 준다."""
    for name in _WIDGET_NAMES:
        monkeypatch.setattr(
            app_module, name, type(f"_Rec{name}", (_RecordingWidget,), {})
        )
    instance = app_module.PhotonTCPApp()
    return instance, instance.build()


def test_build_constructs_the_expected_widget_tree(built) -> None:
    """설정 행 / QR 영역 + 프리뷰 슬롯 / 상태 줄 / Start·Stop 이 모두 선다."""
    app_instance, root = built

    settings, middle, status, buttons = root.children

    assert settings.children == [
        app_instance.role_spinner,
        app_instance.scale_input,
        app_instance.hold_input,
        app_instance.facing_spinner,
    ]
    assert middle.children == [app_instance.qr_image, app_instance.preview_slot]
    assert status is app_instance.status_label and status.text
    assert buttons.children == [app_instance.start_button, app_instance.stop_button]
    assert (app_instance.start_button.text, app_instance.stop_button.text) == (
        "Start",
        "Stop",
    )
    assert app_instance.stop_button.disabled is True


def test_qr_image_uses_fit_mode_not_the_deprecated_pair(built) -> None:
    """QR 위젯은 ``fit_mode`` 로 만들어지고 폐기예정 속성을 넘기지 않는다.

    Kivy 2.3.1 은 ``allow_stretch``/``keep_ratio`` 에 *"will be removed in a
    future version"* 경고를 낸다. 대체는 ``fit_mode="contain"``(Kivy 2.2+)이며,
    p4a 의 kivy 레시피가 2.3.1 을 고정하므로 실기에서도 존재한다. 둘 중 하나라도
    되살아나면 이 단언이 붉어진다.

    ``"contain"`` 인 것까지 못 박는 이유: ``"fill"``/``"scale-down"`` 은 QR 을
    늘리거나 축소를 막아 디코드를 깨뜨릴 수 있다 — 구 조합
    (``allow_stretch=True, keep_ratio=True``)과 등가인 것은 ``"contain"`` 뿐이다.
    """
    app_instance, _root = built
    kwargs = app_instance.qr_image.init_kwargs

    assert kwargs.get("fit_mode") == "contain"
    assert "allow_stretch" not in kwargs
    assert "keep_ratio" not in kwargs


# --------------------------------------------------------------------------- #
# 9. Kivy 라이프사이클 이벤트 이름과의 충돌 (M13 — 계획 밖 발견)
# --------------------------------------------------------------------------- #


def test_start_handler_does_not_shadow_the_kivy_lifecycle_event(app_module) -> None:
    """``PhotonTCPApp`` 은 ``on_start`` 를 **정의하면 안 된다**.

    Kivy 의 ``App.run()`` 은 기동 중 ``self.dispatch('on_start')`` 를 부른다.
    그래서 그 이름으로 메서드를 두면 **프레임워크가 창을 여는 순간 그것을 실행**한다.
    M13 이전에 이 클래스가 실제로 ``on_start`` 를 Start 버튼 핸들러로 정의하고 있었고,
    그 결과 **아무도 Start 를 누르지 않았는데 카메라 권한 요청과 핸드셰이크가 시작**됐다
    (데스크톱 실행에서 실측). 그 자체로 잘못이고, 실기 스모크 관찰도 오염시킨다.

    ``on_stop`` 은 **의도적 예외**다 — 프레임워크의 종료 훅과 Stop 버튼이 같은 일을
    원하므로 이름을 공유하는 것이 맞고, 그 사유가 해당 메서드 docstring 에 적혀 있다.
    """
    app_cls = app_module.PhotonTCPApp
    assert "on_start" not in vars(app_cls), (
        "PhotonTCPApp 이 on_start 를 정의했다 — Kivy 가 기동 시 이것을 디스패치하므로 "
        "세션이 저절로 시작된다. 버튼 핸들러는 on_start_pressed 처럼 다른 이름을 쓴다."
    )
    assert callable(getattr(app_cls, "on_start_pressed", None)), (
        "Start 버튼 핸들러 on_start_pressed 가 없다"
    )
    # on_stop 은 반대로 **의도적으로** 정의돼 있어야 한다(종료 훅 겸용).
    assert "on_stop" in vars(app_cls)


def test_start_button_is_bound_to_the_renamed_handler(built) -> None:
    """개명이 배선까지 갔는가 — Start 버튼이 ``on_start_pressed`` 에 묶인다."""
    app_instance, _ = built
    handler = getattr(app_instance.start_button, "bound_events", {}).get("on_release")
    assert handler is not None, "start_button 에 on_release 바인딩이 없다"
    assert handler == app_instance.on_start_pressed
    # Stop 은 종전대로 on_stop 에 묶인다(의도적 이중 역할).
    stop_handler = getattr(app_instance.stop_button, "bound_events", {}).get("on_release")
    assert stop_handler == app_instance.on_stop
