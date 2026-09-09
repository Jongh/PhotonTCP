"""디코더 백엔드 seam 테스트 (M11-T09, 완료 기준 1·3).

``photontcp/qr/decode.py`` 가 M11-T01 에서 얻은 두 성질을 방전한다:

1. **cv2 없이도 살아남는다** — cv2 부재를 시뮬레이션한 상태에서 ``import
   photontcp.qr`` 이 성공하고, ``encode_frame`` 은 정상 동작하며,
   ``decode_frame`` 은 ``None`` 을 돌려주고 **예외를 던지지 않는다**.
2. **백엔드가 실제로 교체 가능하다** — 가짜 백엔드를 등록·선택하면
   ``decode_frame`` 이 그것을 호출하고, ``set_decoder_backend(None)`` 으로
   되돌리면 cv2 경로로 복귀해 정상 디코드한다. 서드파티 백엔드가 예외를
   던져도 never-raise 계약은 유지된다.

**격리**: 이 모듈은 프로세스 전역 상태(``sys.modules``/``sys.meta_path``,
백엔드 레지스트리, 선택된 백엔드 이름)를 건드리므로 모든 변경을 fixture 로
감싸 되돌린다. 등록 해제 공개 API 가 없어 레지스트리는 dict 스냅샷으로
복원한다(다른 테스트 모듈이 ``"cv2"`` 자동 선택에 의존하기 때문).
"""

from __future__ import annotations

import sys

import pytest

pytest.importorskip("segno")
pytest.importorskip("cv2")

import numpy as np  # noqa: E402

from photontcp.qr import decode as _decode_mod  # noqa: E402
from photontcp.qr.decode import (  # noqa: E402
    active_decoder_backend,
    decode_frame,
    register_decoder_backend,
    set_decoder_backend,
)
from photontcp.qr.encode import encode_frame  # noqa: E402


# --------------------------------------------------------------------------- #
# 격리 fixture
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _restore_backend_registry():
    """백엔드 레지스트리·선택 상태를 테스트마다 원상복구한다.

    ``register_decoder_backend`` 에 짝이 되는 해제 API 가 없으므로 등록 dict 를
    스냅샷/복원한다. ``_ACCEPTS_DETECTOR`` 메모도 함께 되돌려, 가짜 백엔드
    객체가 다른 테스트로 새지 않게 한다.
    """
    saved_factories = dict(_decode_mod._BACKEND_FACTORIES)
    saved_accepts = dict(_decode_mod._ACCEPTS_DETECTOR)
    saved_selected = _decode_mod._SELECTED_BACKEND
    try:
        yield
    finally:
        set_decoder_backend(None)
        _decode_mod._BACKEND_FACTORIES.clear()
        _decode_mod._BACKEND_FACTORIES.update(saved_factories)
        _decode_mod._ACCEPTS_DETECTOR.clear()
        _decode_mod._ACCEPTS_DETECTOR.update(saved_accepts)
        _decode_mod._SELECTED_BACKEND = saved_selected


class _BlockCv2:
    """``import cv2`` 를 ImportError 로 만드는 ``sys.meta_path`` finder."""

    def find_spec(self, fullname, path=None, target=None):  # noqa: D102
        if fullname == "cv2" or fullname.startswith("cv2."):
            raise ImportError("cv2 blocked by test (simulated absence)")
        return None


@pytest.fixture
def no_cv2():
    """cv2 부재를 시뮬레이션한다 (모듈 캐시 + import 경로 양쪽).

    ``_require_cv2`` 는 매 호출마다 ``import cv2`` 를 시도하므로, 캐시된
    모듈을 치우고 import 자체를 막아야 실제 부재와 같아진다. 종료 시
    ``sys.meta_path`` 와 ``sys.modules`` 를 정확히 원복한다 — 이 모듈이
    다른 테스트의 cv2 를 망가뜨리면 안 된다.

    ``_alt_kind`` 캐시는 이 경로에서 건드려지지 않는다: cv2 백엔드 factory 가
    먼저 "unavailable" 을 반환해 ``decode_frame`` 이 백엔드에 진입조차 하지
    않기 때문이다. 그래도 안전하게 값을 스냅샷/복원한다.
    """
    saved_modules = {
        name: mod
        for name, mod in sys.modules.items()
        if name == "cv2" or name.startswith("cv2.")
    }
    for name in saved_modules:
        del sys.modules[name]
    blocker = _BlockCv2()
    sys.meta_path.insert(0, blocker)
    saved_alt_kind = _decode_mod._alt_kind
    try:
        yield
    finally:
        try:
            sys.meta_path.remove(blocker)
        except ValueError:  # pragma: no cover - defensive
            pass
        sys.modules.update(saved_modules)
        _decode_mod._alt_kind = saved_alt_kind


@pytest.fixture
def fresh_qr_import():
    """``photontcp.qr*`` 를 ``sys.modules`` 에서 걷어내 **신규 실행**을 강제한다.

    ``importlib.import_module`` 은 캐시된 모듈을 재실행 없이 돌려주므로, 모듈 상단이
    실제로 다시 도는지 보려면 캐시를 비워야 한다. 이것이 없으면 "cv2 없이도 import
    된다" 단언이 **공허해진다**(M11-review 차단 1의 되돌림 실측: eager ``import cv2``
    를 되살려도 전체 스위트가 초록이었다).

    ``no_cv2`` 와 **함께** 쓴다(순서 무관 — 둘 다 함수 스코프이고 서로 건드리는 전역이
    다르다). 종료 시 원래 모듈 객체를 정확히 되돌려, 재실행으로 생긴 별도의 모듈
    인스턴스가 다른 테스트로 새지 않게 한다 — 그러지 않으면 이 파일 상단이 import 한
    ``_decode_mod`` 와 ``sys.modules`` 의 것이 갈라진 채 남는다.

    **``sys.modules`` 만으로는 부족하다**: import 는 부모 패키지에도 서브모듈을
    **속성으로** 심으므로(``photontcp.qr = <module>``), 그것까지 되돌리지 않으면
    ``import photontcp`` 뒤의 ``photontcp.qr`` 속성 조회가 버려진 재실행 인스턴스를
    가리킨 채 남는다(``from ... import`` 는 ``sys.modules`` 를 보므로 드러나지 않아
    더 늦게 발견된다). 아래에서 두 자리를 함께 복원한다.
    """
    saved = {
        name: mod
        for name, mod in sys.modules.items()
        if name == "photontcp.qr" or name.startswith("photontcp.qr.")
    }
    # 부모 패키지에 심긴 서브모듈 속성도 함께 스냅샷한다 (예: photontcp.qr).
    saved_attrs = []
    for name, mod in saved.items():
        parent_name, _, child = name.rpartition(".")
        parent = sys.modules.get(parent_name)
        if parent is not None and getattr(parent, child, None) is mod:
            saved_attrs.append((parent, child, mod))
    for name in saved:
        del sys.modules[name]
    try:
        yield
    finally:
        # 재실행으로 새로 생긴 항목을 걷어내고 원본을 복원한다.
        for name in [
            n
            for n in sys.modules
            if n == "photontcp.qr" or n.startswith("photontcp.qr.")
        ]:
            del sys.modules[name]
        sys.modules.update(saved)
        for parent, child, mod in saved_attrs:
            setattr(parent, child, mod)


# --------------------------------------------------------------------------- #
# 1. cv2 부재 (완료 기준 1)
# --------------------------------------------------------------------------- #


def test_cv2_is_really_blocked(no_cv2) -> None:
    """전제 확인: fixture 안에서는 cv2 를 정말 import 할 수 없다."""
    with pytest.raises(ImportError):
        import cv2  # noqa: F401,PLC0415

    assert _decode_mod._require_cv2() is None


def test_import_photontcp_qr_succeeds_without_cv2(no_cv2, fresh_qr_import) -> None:
    """cv2 부재에서도 ``photontcp.qr`` 패키지가 **새로 실행**되어 import 된다.

    **이 테스트의 검수 기준은 하나다** — `photontcp/qr/decode.py` 상단에 모듈 수준
    ``import cv2`` 를 되살리면 **여기가 붉어야 한다**. 그래야 M11 이 세운 지연 import
    (cv2 없는 플랫폼에서도 패키지가 import 된다)를 실제로 무는 가드가 된다.

    그러려면 **캐시된 모듈을 돌려받아서는 안 된다**: ``importlib.import_module`` 은
    이미 ``sys.modules`` 에 있는 모듈을 **재실행 없이** 반환하므로, 이 파일 상단의
    ``from photontcp.qr.decode import ...`` 때문에 그냥 부르면 모듈 상단이 다시 돌지
    않아 eager import 를 되살려도 통과해 버린다(M11-review 차단 1). ``fresh_qr_import``
    fixture 가 ``sys.modules`` 에서 ``photontcp.qr*`` 를 걷어내 **신규 실행**을 강제한다.
    """
    import importlib  # noqa: PLC0415

    # cv2 를 막은 상태에서 처음부터 실행되는 import (모듈 상단이 실제로 다시 돈다).
    qr_pkg = importlib.import_module("photontcp.qr")
    assert qr_pkg is not None

    decode_mod = importlib.import_module("photontcp.qr.decode")
    assert hasattr(decode_mod, "decode_frame")
    # 정말 새로 실행된 객체인지 — 캐시된 것을 돌려받았다면 이 단언이 무의미해진다.
    assert decode_mod is not _decode_mod, (
        "decode 모듈이 재실행되지 않았다 — fixture 가 sys.modules 를 비우지 못했다"
    )

    # 그 신규 모듈에서 cv2 부재 계약이 성립한다: 가용 백엔드 없음 + None 반환.
    assert decode_mod.active_decoder_backend() is None
    assert decode_mod.decode_frame(encode_frame(b"no opencv here")) is None


def test_encode_frame_works_without_cv2(no_cv2) -> None:
    """인코드는 segno+numpy 만 쓰므로 cv2 없이도 정상 동작한다."""
    image = encode_frame(b"no opencv here")
    assert isinstance(image, np.ndarray)
    assert image.dtype == np.uint8
    assert image.ndim == 2
    assert image.size > 0


def test_decode_frame_returns_none_without_cv2(no_cv2) -> None:
    """가용 백엔드가 없으면 ``decode_frame`` 은 None (예외 없음)."""
    image = encode_frame(b"no opencv here")

    assert active_decoder_backend() is None
    assert decode_frame(image) is None
    # 명시 detector 인자를 줘도 마찬가지다.
    assert decode_frame(image, detector=object()) is None
    # None 입력도 여전히 None.
    assert decode_frame(None) is None


def test_decode_frame_never_raises_without_cv2(no_cv2) -> None:
    """망가진 입력들에서도 raise 하지 않는다 (never-raise 계약)."""
    for bad in (
        np.zeros((4, 4), dtype=np.uint8),
        np.zeros((4, 4, 3), dtype=np.uint8),
        np.zeros((2, 2, 7), dtype=np.uint8),
    ):
        assert decode_frame(bad) is None


def test_cv2_backend_returns_after_block_is_lifted() -> None:
    """부재 시뮬레이션이 끝나면 cv2 백엔드가 다시 활성이다 (오염 방지)."""
    assert active_decoder_backend() == "cv2"
    payload = b"back to normal"
    assert decode_frame(encode_frame(payload)) == payload


# --------------------------------------------------------------------------- #
# 2. 백엔드 교체 가능성 (완료 기준 3)
# --------------------------------------------------------------------------- #


def test_fake_backend_is_used_when_selected() -> None:
    """등록·선택한 가짜 백엔드를 ``decode_frame`` 이 실제로 호출한다."""
    calls: list[np.ndarray] = []

    def fake_backend(image):
        calls.append(image)
        return b"from-fake"

    register_decoder_backend("fake", lambda: fake_backend)
    set_decoder_backend("fake")

    assert active_decoder_backend() == "fake"

    image = encode_frame(b"real payload")
    assert decode_frame(image) == b"from-fake"
    assert len(calls) == 1
    assert calls[0] is image


def test_reverting_selection_restores_cv2_path() -> None:
    """``set_decoder_backend(None)`` 이 cv2 자동 선택으로 복귀시킨다."""
    register_decoder_backend("fake", lambda: (lambda image: b"from-fake"))
    set_decoder_backend("fake")
    payload = b"round trip payload"
    image = encode_frame(payload)
    assert decode_frame(image) == b"from-fake"

    set_decoder_backend(None)

    # cv2 가 등록 1번이므로 자동 선택이 cv2 로 돌아온다.
    assert active_decoder_backend() == "cv2"
    assert decode_frame(image) == payload


def test_unknown_backend_name_raises_value_error() -> None:
    """미등록 이름 선택은 조용한 열화가 아니라 ValueError 다."""
    with pytest.raises(ValueError):
        set_decoder_backend("definitely-not-registered")
    # 실패한 선택이 활성 백엔드를 바꾸지 않았다.
    assert active_decoder_backend() == "cv2"


def test_backend_exception_is_swallowed_to_none() -> None:
    """서드파티 백엔드가 던져도 ``decode_frame`` 은 None 을 반환한다."""

    def exploding_backend(image):
        raise RuntimeError("third-party backend blew up")

    register_decoder_backend("boom", lambda: exploding_backend)
    set_decoder_backend("boom")

    assert decode_frame(encode_frame(b"x")) is None


def test_unavailable_backend_selection_decodes_to_none() -> None:
    """factory 가 None/raise 면 선택돼 있어도 백엔드는 '없음' 이다."""
    register_decoder_backend("absent", lambda: None)
    register_decoder_backend("broken-factory", _raise_unavailable)

    set_decoder_backend("absent")
    assert active_decoder_backend() is None
    assert decode_frame(encode_frame(b"x")) is None

    set_decoder_backend("broken-factory")
    assert active_decoder_backend() is None
    assert decode_frame(encode_frame(b"x")) is None


def _raise_unavailable():
    raise ImportError("this backend is not installed here")


def test_detector_argument_is_forwarded_only_when_accepted() -> None:
    """``detector=`` 는 그것을 받는 백엔드에만 전달된다."""
    seen: dict[str, object] = {}

    def with_detector(image, detector=None):
        seen["detector"] = detector
        return b"ok"

    def without_detector(image):
        seen["detector"] = "not-offered"
        return b"ok"

    sentinel = object()

    register_decoder_backend("with-det", lambda: with_detector)
    set_decoder_backend("with-det")
    assert decode_frame(encode_frame(b"x"), detector=sentinel) == b"ok"
    assert seen["detector"] is sentinel

    register_decoder_backend("no-det", lambda: without_detector)
    set_decoder_backend("no-det")
    assert decode_frame(encode_frame(b"x"), detector=sentinel) == b"ok"
    assert seen["detector"] == "not-offered"


def test_automatic_selection_prefers_registration_order() -> None:
    """자동 선택은 등록 순서대로 첫 가용 백엔드를 쓴다 (cv2 가 1번)."""
    register_decoder_backend("late", lambda: (lambda image: b"late"))
    set_decoder_backend(None)
    assert active_decoder_backend() == "cv2"

    payload = b"still cv2"
    assert decode_frame(encode_frame(payload)) == payload
