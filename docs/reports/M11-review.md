# M11 리뷰보고서 (review)

> 재작업 라운드 1의 리뷰(2회차). 1회차는 차단 1건으로 `불가` 판정해 impl로 반환했다 —
> 완료 기준 1(cv2 지연 import)을 무는 검사가 공허해, `decode.py` 상단에 `import cv2`를
> 되살려도 전체 스위트가 초록이었다. 이번 리뷰의 첫 번째 일은 **그 차단이 정말 닫혔는지**를
> 되돌림으로 직접 확인하는 것이었다.

## 비판점

### 차단 (0건)

없다. 1회차 차단은 **되돌림 실측으로 닫힘을 확인**했다(아래 `## 검증`).

### 권장 (1건 — in-review 수정 완료)

1. **`android.wakelock`을 켜 놓고 문서 두 곳은 "꺼져 있다"고 적었고, 한 곳은 이미 끝난 변경을
   하라고 지시했다** — 1회차 사소 13을 스펙에서 닫으면서(`buildozer.spec:116` `android.permissions
   = CAMERA, WAKE_LOCK`, `:162` `android.wakelock = True`) 그 값을 서술하던 문서를 함께 고치지
   않았다.
   - `docs/mobile-build.md` 6절: *"`android.wakelock`은 **지금 꺼져 있으므로**(권한 목록을 CAMERA
     하나로 유지하기 위해) … 상시 필요해지면 `android.wakelock = True` + `WAKE_LOCK` 추가로
     바꾼다"* — 세 진술이 모두 현재 스펙과 반대이고, 마지막은 **이미 적용된 변경을 하라는 실행
     지시**다.
   - `docs/v1.0-signoff.md`의 폰 실전 주의 표: *"`buildozer.spec`의 `android.wakelock`은 기본 off"*
     를 근거로 수동 기기 설정을 지시 — **1회차 사소 13이 인용한 바로 그 줄이 그대로 남아 있었다.**

   **이것이 권장인 이유**: 코드 거동에는 영향이 없다. 그러나 형태가 나쁘다 — 라운드 1이 닫은
   **권장 6**(문서가 이미 존재하는 `main.py`를 만들라고 지시)과 **정확히 같은 결함 유형**을
   wakelock 쪽에 새로 만들었고, `docs/v1.0-signoff.md`는 사람이 문면 그대로 수행하는 절차
   문서다. 반대로 `vacuous-pass` 부류는 **아니다**: 문서가 집행력을 사실보다 **넓게** 주장한 것이
   아니라 **좁게** 적은 것이라, 잘못된 통과를 만들어내는 자리가 없다(그래서 부인 기록도 없다).

### 사소 (3건 — 전부 in-review 수정 완료)

2. **새 `fresh_qr_import` fixture가 `sys.modules`는 복원하면서 부모 패키지 속성은 복원하지
   않았다** — `tests/test_decode_backend.py`. import는 서브모듈을 부모에 **속성으로도** 심으므로
   (`photontcp.qr = <module>`), 그것까지 되돌리지 않으면 `import photontcp` 뒤의 `photontcp.qr`
   속성이 **버려진 재실행 인스턴스**를 가리킨 채 남는다. fixture docstring은 "재실행으로 생긴
   별도의 모듈 인스턴스가 다른 테스트로 새지 않게 한다"고 적어 실제보다 넓게 주장했다.
   **실측**: 수정 전 `sys.modules 복원 True / 부모 속성 복원 False`. 현재 어떤 테스트도 그 속성을
   관찰하지 않아(`from … import`는 `sys.modules`를 본다) 잘못된 통과는 없었다 — 그래서 사소다.
3. **`run_peer`의 새 `ValueError` 3종이 `try/finally` 앞에서 던져져, CLI의 `--hold 0`이 우아한
   FAIL에서 트레이스백으로 바뀌고 채널이 닫히지 않았다** — `photontcp/optical/peer.py:196-213`
   vs `examples/optical_link.py`의 `finally`. CLI의 `--hold`에는 하한 검증이 없어
   `--real --role sender --hold 0`이 그 경로에 닿는다. 라운드 1이 앱에는 `MIN_HOLD` 클램프를
   넣었지만 **두 번째 소비자인 CLI는 같은 값을 그대로 통과시킨다.** 캡처 스레드가 daemon이라
   프로세스가 매달리지는 않고, 완료 기준 4가 열거한 CLI 계약 3항목에도 걸리지 않는다.
4. **`KivyCamera._own()`이 복사하지 못하는 버퍼 타입을 조용히 참조로 통과시켜, 소유권 보증이
   타입 조건부였다** — `photontcp/mobile/kivy_devices.py`. `ndarray`/`bytes`/`bytearray`/
   `memoryview`만 소유권을 가져가고 그 밖의 버퍼 프로토콜 객체(`array.array`, ctypes 배열, JNI
   래퍼)는 원본 참조를 보관한다. 클래스 docstring은 "takes ownership of the pixels"라 무조건
   단언하고 테스트 이름은 "recycling **cannot** tear a frame"이라는 무조건 명제를 건다.
   camera4kivy가 넘기는 것은 `bytes`로 알려져 있어 현재 실기 노출은 사실상 없다(**미확인** —
   기기 부재).

**이월 2건은 이번에도 이월이 타당하다**(1회차 사소 8·11): cv2 부재 시 실패 import 반복은 완화하려면
가용성 캐시가 필요한데 그것은 `active_decoder_backend()`의 live 보고 성질과 맞바꾸는 설계 결정이고,
`photontcp.optical`의 eager `app`/`session` import는 패키지 `__getattr__` 지연 재수출이라는 구조
변경이다. 둘 다 기능 회귀가 아니며 1회차가 이미 "다음 사이클 후속"으로 분류했다.

## 수정 내용

- **이슈 1**(권장): `docs/mobile-build.md` 6절을 "**스펙이 이미 막아 둔다**"로 고치고, wakelock이
  실기에서 실제로 화면을 붙잡는지는 미검증임을 밝힌 뒤 폴백(폰 설정)을 남겼다.
  `docs/v1.0-signoff.md`의 표 행도 "앱이 `android.wakelock = True`로 빌드되므로 기본 조치 불필요,
  그래도 꺼지면 …"으로 바꿨다. **폴백을 지우지 않은 이유**: 실기 미검증이라 "켜 뒀으니 끝"이라고
  쓰면 이번엔 반대 방향으로 사실보다 넓게 주장하게 된다.
- **이슈 2**(사소): fixture가 스냅샷할 때 부모 패키지의 서브모듈 속성도 함께 기록하고 teardown에서
  `setattr`로 되돌린다. docstring에 "왜 `sys.modules`만으로는 부족한가"를 적었다.
  **실측**: 수정 후 `sys.modules 복원 True / 부모 속성 복원 True`.
- **이슈 3**(사소): CLI의 `finally`에 `chan.close()`를 더했다. `OpticalChannel.close()`는 계약상
  **멱등**이므로(소스에서 확인) 정상 경로에서도 무해하고, 정리가 `run_peer`의 인자 검증 통과
  여부와 **무관해진다** — `--hold 0`뿐 아니라 모든 이른 예외를 함께 덮는다. `--hold` 자체에
  argparse 하한을 넣지 않은 것은 의도적이다: 완료 기준 4가 `--help`가 노출하는 인자 집합을 고정해
  두었고, 정리 누수를 닫는 것으로 실해가 사라진다.
- **이슈 4**(사소): `_own()`이 나열된 세 타입 밖의 버퍼를 `bytes(memoryview(pixels))`로 복사하도록
  일반화하고, 버퍼가 아닌 객체만 통과시킨다(그 사실을 주석에 적었다). 테스트에 `array.array`
  분기를 더해 보증이 타입 조건부가 아님을 기계가 물게 했다.

## 검증

리뷰가 **직접 실행한** 실측이다. 아래 값은 **in-review 수정 4건이 모두 끝난 뒤**의 측정이다.

**재검증 결과 (re-verify)** — in-review 수정 4건이 있었으므로 수정 후 전 하니스를 재실행했다:

- `python -m pytest -q` → **247 passed, 1 skipped** (실패 0). 수정 전과 동일 — 회귀 없음.
- `python examples/qr_decode_bench.py` → **exit 0**, clean 249/249 = **100.00%**,
  degraded 249/249 = **100.00%**, `full >= base-only on every set: YES`.
- `python examples/optical_link.py` → **exit 0** / `--role sender` → **exit 2**.
- `--help` 출력을 HEAD(v0.10.0)와 diff → **차이 없음**(완료 기준 4).
- `import photontcp.mobile` (Kivy 미설치) → 네 이름 전부 `None`, `__all__ == []`.
- `photontcp.__version__` → `0.10.0`.
- `tests/test_peer_runner.py` **3회 연속** 17 passed(3.05/3.08/3.19초) — 스레드 테스트 플레이키 없음.
- 문서 정합 재확인: `docs/mobile-build.md`·`docs/v1.0-signoff.md`에 "wakelock이 꺼져 있다"는
  서술이 **0건**(grep).

**1회차 차단이 닫혔음의 되돌림 실측(핵심)** — 스크래치 복제 트리의 `photontcp/qr/decode.py`
상단에 `import cv2`를 되살리고:

| | 라운드 0 (1회차 리뷰) | 라운드 1 (현재) |
|---|---|---|
| `tests/test_decode_backend.py` | 13 passed | **1 failed**, 12 passed |
| 전체 스위트 | **240 passed** (초록 — 공허) | **1 failed**, 239 passed, 1 skipped |

`fresh_qr_import`가 `sys.modules`에서 `photontcp.qr*`를 걷어내므로 `import_module`이 패키지
`__init__` → `decode.py`를 **실제로 재실행**하고, 그 재실행이 `no_cv2`가 심은 meta_path 블로커를
만난다. 테스트가 `decode_mod is not _decode_mod`를 먼저 단언해 "정말 재실행됐는가"까지 문다.

**다른 형태의 되돌림도 검토했다**: 지연 import를 유지하면서 모듈 상단에서 cv2를 만지는 형태
(`try: import cv2 / except ImportError: cv2 = None`)는 이 가드를 통과한다. 그러나 그런 형태는
완료 기준 1이 요구하는 성질(cv2 부재에서 import 성공 · `decode_frame` None · never-raise)을
**깨지 않으므로** 가드의 구멍이 아니다. 기준을 실제로 깨는 되돌림은 "cv2 부재에서 import가
던지는" 형태뿐이고, 그것은 전부 잡힌다.

**신규 테스트의 비공허성**(라운드 1의 7건 + 리뷰가 더한 1분기): 되돌림으로 확인한 것 —
지연 import 가드(위), `test_pyproject_wins_over_stale_installed_metadata`(파생 순서를 metadata
우선으로 되돌리면 `1 failed`), `test_submitted_buffer_is_owned_…`(`_own`의 소유권 획득을
무력화하면 `1 failed`). 나머지(폴백 상수·`hold<=0` 3건·`messages` 쌍)는 각각 새 `raise`를 직접
물어, 그 줄을 지우면 붉는다.

**상수 재수출의 무해성**: `examples/optical_link.py`가 `peer`에서 재수출하는 11개 값
(`ROUND_DT`·`HEARTBEAT_INTERVAL`·`IDLE_TIMEOUT`·`MAX_*_ROUNDS`·`WALL_DEADLINE_S`·`MESSAGES_A`·
`MESSAGES_B`)이 **HEAD 값과 전부 동일**함을 실측했다. 데모 고유값 `ROUND_SLEEP=0.03`은 로컬로
남아 인메모리 데모의 페이싱 의미가 바뀌지 않았다.

**미검증 잔여 리스크** — 마일스톤이 명시적으로 이월한 범위와 일치한다: APK 실빌드와 opencv 레시피
통과 여부, camera4kivy 콜백 시그니처·RGBA 행 순서·**분석 버퍼 재사용 여부**, Android GLES의
`luminance` 텍스처 수용, `flip_*` 기본값의 실기 정합성, 그리고 **`android.wakelock`이 실기에서
실제로 화면을 붙잡는지**(라운드 1 신규 — 문서에 폴백과 함께 적었다). 더해 `app.py`의 상태
전이(Start/Stop·권한 거부·camera4kivy 부재)는 여전히 자동 테스트 0건이고(Kivy 미설치), 설치
환경에서의 버전 파생(metadata 분기)도 이 환경에서는 탈 수 없다.

## 릴리즈 판정

**가능** — 추천 버전: **v0.11.0 (minor)**

계측: in-review 수정 차단 0/권장 1/사소 3 · 반증 시도 수행 성립 3건 · 연속 폴백(fallback-streak) 0 · 부인 기록(precedent-waiver) 0건 · 연속 부인(waiver-streak) 0 · 재작업 라운드(rework) 1

- **판정 근거**: 1회차 차단이 **되돌림 실측으로 닫혔다** — 같은 되돌림이 라운드 0에서는 240
  passed로 전부 초록이었고 지금은 붉는다. 완료 기준 12개 중 11개가 리뷰의 직접 실측으로 충족되고,
  미충족인 기준 9(APK 실빌드)는 마일스톤 본문이 "실패/미수행이 이 마일스톤을 깨지 않는다"고
  명시한 폴백 경로다. 이번 라운드에서 발견한 4건은 차단 0이며 전부 in-review로 닫고 재검증까지
  마쳤다. 재작업 라운드가 1이라 마일스톤 재분해 권고 대상도, 잔여 위험 수용 대상도 아니다.
- **minor인 이유**: 신규 공개 표면(`photontcp.mobile`, `optical.peer.run_peer`, 디코더 백엔드 등록
  API)이 추가되고 기존 공개 계약은 불변이다 — `decode_frame` 시그니처·None 계약·never-raise,
  CLI 인자 집합(`--help` diff 무출력)·종료 코드·요약 줄 형식을 모두 실측으로 확인했다. 라운드 1이
  더한 `run_peer`의 `ValueError` 3종은 **M11에서 처음 공개된 API**의 인자 검증이라 하위 호환을
  깨지 않는다. v1.0은 실기 사인오프 실측을 전제로 하므로 이번에도 주장하지 않는다.
- 반증 시도: 수행(서브에이전트 디스패치) — 성립 3건, 전부 위 비판점 섹션에 편입하고 in-review로
  닫았다. 1회차 차단의 닫힘 여부는 반증자와 메인이 **각각 독립적으로** 되돌림을 실측해 일치했다.

## 다음 단계

- **`/tide:release v0.11.0`**
- **릴리즈 후 후속(다음 사이클 첫 항목)**: **APK 실빌드** — ① `camerax_provider` clone
  ② Docker Desktop 기동 또는 WSL2 일반 배포판 설치 ③ `buildozer android debug` ④ 실패 시
  `android.ndk` 조정 또는 opencv 우회. 이어서 **폰 + PC 체크 B 실측**(`docs/v1.0-signoff.md` 2-B).
  실기 첫 실행에서 가장 먼저 확인할 것은 여전히 `flip_vertical`/`flip_horizontal`·`colorfmt`이고,
  거기에 **wakelock 실동작**과 **camera4kivy의 버퍼 재사용 여부**(확인되면 `copy_on_submit`
  기본값 재검토)가 더해진다.
- **이월된 사소 2건**: cv2 부재 시 실패 import 반복(가용성 캐시 ↔ `active_decoder_backend()` live
  보고의 트레이드오프), `photontcp.optical`의 eager `app`/`session` import(지연 재수출 구조 변경).
- **구조적 후속**: `unregister_decoder_backend()`/`registered_decoder_backends()` 짝 API,
  `run_peer(should_stop=…)` 취소 훅(생기면 앱의 `_CancellableChannel` 래퍼 제거 가능),
  `app.py` 상태 전이 테스트(Kivy 설치 환경 전제).
- **회고 표 갱신**: `docs/reports/retro.md` 최상단 섹션의 세 행(Micro-QR 원천 제거,
  `__version__` 불일치, `docs/v1.0-signoff.md` 미커밋)이 아직 `미반영`이다 — 다음 `/tide:retro`가
  `반영`으로 옮긴다.
