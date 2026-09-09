"""스위트 전역 안전망 (M13-T02, M12-review 사소 9).

**닫는 위험**: M12-T03 은 cv2 **부재(실패한 import)** 를 프로세스 수명 동안 캐시한다
(``photontcp/qr/decode.py`` 의 probe 캐시). 그 캐시를 무효화하는 지점은
:func:`photontcp.qr.decode.reset_decoder_backend_probe` 와 두 레지스트리 호출뿐이라,
*"cv2 를 막았다 풀면 디코드가 회복된다"* 가 지금은 ``tests/test_decode_backend.py`` 의
``blocked_cv2`` 컨텍스트가 **종료 시 그 함수를 부르는 것**에 의존한다. 앞으로 어떤 테스트가
그 헬퍼를 거치지 않고 cv2 를 막으면(``monkeypatch`` 로 ``sys.modules['cv2'] = None`` 을
넣는 것만으로도 충분하다) **세션 전체가 캐시된 부재로 오염**되고, 실패는 그 테스트가 아니라
**한참 뒤의 무관한 테스트**에서 난다.

**왜 이 형태인가** — 마일스톤은 ``blocked_cv2`` 를 여기로 **올려 공용 fixture 로 만드는**
선택지를 먼저 제시했다. 그것을 택하지 않은 이유는 둘이다:

1. **관례 이전은 위험을 없애지 못한다.** 헬퍼를 공용으로 올려도, 그것을 쓰지 **않고** cv2 를
   막는 미래의 테스트는 여전히 세션을 오염시킨다. 방어가 작성자의 선택에 계속 의존한다.
   아래 autouse 안전망은 **차단 방식과 무관하게** 매 테스트 종료 시 캐시를 무효화하므로,
   같은 위험을 기계로 닫는다.
2. ``blocked_cv2`` 는 ``_BlockCv2.lookups`` (probe 횟수) 와 ``reset_on_exit=False`` 라는,
   **음성 캐시 자체를 관찰하기 위한** 장치를 함께 들고 있다. 그 셋은 한 자리에서 읽혀야
   의미가 통하므로 ``test_decode_backend.py`` 에 남긴다(같은 파일 상단 docstring 에 이
   위험과 여기로의 포인터를 경고로 적었다).

**비용**: 테스트당 ``sys.modules`` dict 조회 한 번. ``photontcp.qr.decode`` 가 아직 import 되지
않았으면 아무것도 하지 않으므로, numpy/segno/cv2 가 없는 환경에서 이 conftest 가 무언가를
끌어오는 일도 없다.
"""

from __future__ import annotations

import sys

import pytest


@pytest.fixture(autouse=True)
def _invalidate_decoder_probe_cache():
    """매 테스트 종료 시 cv2 가용성 probe 캐시를 무효화한다.

    캐시되는 것은 **부재뿐**이므로(성공은 캐시되지 않는다 — ``decode.py`` 참조), 이
    무효화가 지우는 것은 "cv2 가 없다"는 판정 하나다. 그것을 매번 버리는 것은 항상 안전한
    방향이며(다음 사용처가 다시 probe 한다), 무효화 지점 자체를 무는 테스트들은 **한 테스트
    안에서** 차단·무효화·회복을 모두 관찰하므로 이 teardown 이 그 단언을 가리지 않는다.
    """
    yield
    decode_mod = sys.modules.get("photontcp.qr.decode")
    if decode_mod is not None:
        reset = getattr(decode_mod, "reset_decoder_backend_probe", None)
        if reset is not None:
            reset()
