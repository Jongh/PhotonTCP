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

.. warning::

   **cv2 를 막는 테스트를 새로 쓴다면 여기를 먼저 읽어라** (M12-review 사소 9).
   M12-T03 이 cv2 **부재** 를 프로세스 수명 동안 캐시하므로, 차단을 푼 뒤
   :func:`~photontcp.qr.decode.reset_decoder_backend_probe` 를 부르지 않으면
   **세션 전체가 캐시된 부재로 오염**되고 실패가 한참 뒤의 무관한 테스트에서 난다.
   이 모듈의 :func:`blocked_cv2` 는 종료 시 그것을 부르고, 추가로
   ``tests/conftest.py`` 의 autouse 안전망이 **차단 방식과 무관하게** 매 테스트
   종료 시 같은 무효화를 수행한다. 그래도 새 헬퍼를 만들기보다 :func:`blocked_cv2`
   를 재사용하는 편이 낫다 — ``lookups`` 카운터와 ``reset_on_exit`` 스위치가
   음성 캐시 자체를 관찰하는 데 필요하기 때문이다.
"""

from __future__ import annotations

import contextlib
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
    reset_decoder_backend_probe,
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
    """``import cv2`` 를 ImportError 로 만드는 ``sys.meta_path`` finder.

    ``lookups`` 는 finder 가 실제로 조회된 횟수다 — M12-T03 이 넣은 **실패 import
    음성 캐시**가 정말 매 프레임의 ``sys.path`` 전수 탐색을 걷어냈는지 세는 데 쓴다.
    """

    def __init__(self) -> None:
        self.lookups = 0

    def find_spec(self, fullname, path=None, target=None):  # noqa: D102
        if fullname == "cv2" or fullname.startswith("cv2."):
            self.lookups += 1
            raise ImportError("cv2 blocked by test (simulated absence)")
        return None


@contextlib.contextmanager
def blocked_cv2(*, reset_on_exit: bool = True):
    """cv2 부재를 시뮬레이션한다 (모듈 캐시 + import 경로 양쪽).

    ``_require_cv2`` 가 cv2 를 못 찾도록 캐시된 모듈을 치우고 import 자체를 막는다.
    종료 시 ``sys.meta_path`` 와 ``sys.modules`` 를 정확히 원복한다 — 이 모듈이
    다른 테스트의 cv2 를 망가뜨리면 안 된다.

    **``reset_on_exit``**: M12-T03 이 실패한 cv2 import 를 캐시하므로, 차단을
    풀었다는 사실을 모듈에 알려야 가용성이 즉시 회복된다. 기본값 ``True`` 가
    :func:`reset_decoder_backend_probe` 를 불러 그 일을 한다(= 마일스톤이 지정한
    "기존 테스트 fixture 에서 무효화 함수를 부른다"). ``False`` 로 두면 무효화가
    **일어나지 않은** 상태를 관찰할 수 있어, 무효화 지점 자체를 테스트할 수 있다.

    ``_alt_kind`` 캐시는 이 경로에서 건드려지지 않는다: cv2 백엔드 factory 가
    먼저 "unavailable" 을 반환해 ``decode_frame`` 이 백엔드에 진입조차 하지
    않기 때문이다. 그래도 안전하게 값을 스냅샷/복원한다.

    ``yield`` 값은 blocker 객체라 ``lookups`` 로 import 시도 횟수를 볼 수 있다.
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
    # 차단을 시작하는 것도 환경 변화다 — 이전에 캐시된 판정을 들고 들어가지 않는다.
    reset_decoder_backend_probe()
    try:
        yield blocker
    finally:
        try:
            sys.meta_path.remove(blocker)
        except ValueError:  # pragma: no cover - defensive
            pass
        sys.modules.update(saved_modules)
        _decode_mod._alt_kind = saved_alt_kind
        if reset_on_exit:
            reset_decoder_backend_probe()
            _decode_mod._alt_kind = saved_alt_kind


@pytest.fixture
def no_cv2():
    """:func:`blocked_cv2` 의 fixture 판(기존 테스트들이 쓰는 이름)."""
    with blocked_cv2() as blocker:
        yield blocker


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


# --------------------------------------------------------------------------- #
# 3. cv2 부재 probe 비용의 음성 캐시 (M12-T03, 완료 기준 4)
# --------------------------------------------------------------------------- #
#
# M11-T01 은 가용성을 **일부러** 캐시하지 않았다 — ``active_decoder_backend()`` 가
# stale ``"cv2"`` 를 보고하지 않게 하기 위함이다. M12-T03 은 그 성질을 유지한 채
# **실패(부재)만** 캐시한다: 실패한 import 는 ``sys.modules`` 에 남지 않아 매
# ``decode_frame`` 호출이 ``sys.path``/``sys.meta_path`` 를 전수 탐색하는데, 그 상태가
# 바로 ``docs/mobile-build.md`` 5-3 우회 경로의 상시 조건이기 때문이다.
#
# 아래 테스트들이 무는 것은 셋이다:
#   (a) 부재는 정말로 캐시된다 (비용이 실제로 사라졌다),
#   (b) 그럼에도 ``active_decoder_backend()`` 는 stale 값을 보고하지 않는다,
#   (c) 캐시는 명시적 지점에서 무효화된다 (register / set / reset).


def test_missing_cv2_import_is_probed_only_once() -> None:
    """부재가 캐시되어 프레임마다 ``sys.path`` 전수 탐색이 반복되지 않는다."""
    image = encode_frame(b"x")

    with blocked_cv2() as blocker:
        assert decode_frame(image) is None
        first = blocker.lookups
        assert first >= 1, "전제 실패: blocker 가 import 시도를 보지 못했다"

        # 이후 프레임들은 캐시된 '부재' 판정을 쓰므로 import 를 다시 시도하지 않는다.
        for _ in range(20):
            assert decode_frame(image) is None
        assert blocker.lookups == first


def test_active_backend_is_never_stale_cv2() -> None:
    """캐시가 있어도 ``active_decoder_backend()`` 가 stale ``"cv2"`` 를 보고하지 않는다.

    M11-T01 이 지킨 성질 그대로다: cv2 가 있는 상태에서 성공 경로를 **먼저 데우고**
    (여기서 모듈 객체를 캐시했다면 이후 보고가 낡는다) 곧바로 부재로 전환했을 때,
    무효화 호출 **없이도** 즉시 ``None`` 이어야 한다 — 캐시되는 것은 성공이 아니라
    실패뿐이므로 낙관적 방향으로는 결코 낡지 않는다.
    """
    # 성공 경로를 데운다 (cv2 가용 + 실제 디코드까지).
    assert active_decoder_backend() == "cv2"
    payload = b"warm the success path"
    assert decode_frame(encode_frame(payload)) == payload

    with blocked_cv2():
        # 아무 무효화도 부르지 않았는데 즉시 반영된다.
        assert active_decoder_backend() is None
        assert decode_frame(encode_frame(payload)) is None

    # 차단 해제 + 무효화 후 회복 (blocked_cv2 가 나가면서 무효화한다).
    assert active_decoder_backend() == "cv2"
    assert decode_frame(encode_frame(payload)) == payload


def test_reset_probe_reinstates_cv2_after_absence_was_cached() -> None:
    """부재 캐시는 :func:`reset_decoder_backend_probe` 로 무효화된다."""
    with blocked_cv2(reset_on_exit=False):
        assert active_decoder_backend() is None  # 부재가 캐시된다

    # 무효화 전: 보수적으로 여전히 '없음' 이다 (낙관 방향으로 낡지 않는다는 성질의 대가).
    assert active_decoder_backend() is None

    reset_decoder_backend_probe()

    assert active_decoder_backend() == "cv2"
    payload = b"recovered by reset"
    assert decode_frame(encode_frame(payload)) == payload


def test_registry_calls_invalidate_the_probe_cache() -> None:
    """``set_decoder_backend`` / ``register_decoder_backend`` 도 무효화 지점이다."""
    with blocked_cv2(reset_on_exit=False):
        assert active_decoder_backend() is None

    set_decoder_backend(None)  # 명시적 재설정 = 가용성 재판정 신호
    assert active_decoder_backend() == "cv2"

    with blocked_cv2(reset_on_exit=False):
        assert active_decoder_backend() is None

    register_decoder_backend("late-arrival", lambda: (lambda image: b"late"))
    assert active_decoder_backend() == "cv2"


def test_reset_probe_is_idempotent_and_safe_to_call_anytime() -> None:
    """무효화는 스스로 probe 하지 않으며 몇 번을 불러도 계약이 그대로다."""
    reset_decoder_backend_probe()
    reset_decoder_backend_probe()
    assert active_decoder_backend() == "cv2"
    payload = b"still fine"
    assert decode_frame(encode_frame(payload)) == payload
