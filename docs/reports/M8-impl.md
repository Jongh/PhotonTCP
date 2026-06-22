# M8 완료보고서 (impl)

## 개요

M8(실물 광학 채널)의 6개 태스크를 전부 구현했다. QR 프레임을 메모리 큐가 아니라 **디스플레이에
표시하고 카메라로 캡처**해 디코드하는 `OpticalChannel(Channel)`을 추가했고, 하드웨어는
`DisplaySink`/`CameraSource` 디바이스 추상 뒤로 격리했다. 메모리 페이크 페어로 채널 로직(표시·백그라운드
캡처 스레드·nonce dedup·종료)을 하드웨어 없이 결정적으로 검증하고, cv2 실물 어댑터는 카메라/화면이
있을 때만 동작한다. 상위 계층(세션·신뢰성·앱)은 한 줄도 바꾸지 않고 채널 교체만으로 광 링크 위에서
동작함을 테스트·예제로 입증했다. T01은 메인이 직접, T02·T03은 L1 병렬, T04·T05·T06은 L2 병렬로
디스패치했다(파일 비중첩 확인 후 동시 실행).

## 태스크별 수행 내용

- **M8-T01** (메인 직접) — `optical` 서브패키지 신설. `optical/devices.py`에 `DisplaySink`/`CameraSource`
  추상(ABC)과 메모리 페이크 `MemoryDisplay`/`MemoryCamera` + `memory_device_pair()`를 구현. 메모리
  카메라는 `repeat_last=True`일 때 유휴 시 **마지막 프레임 객체를 동일 객체로 재반환**(카메라가 정지된
  디스플레이의 같은 QR을 반복 캡처하는 상황 재현)하고, 디스플레이는 큐로 버퍼링해 메모리 링크에서
  프레임 손실이 없게 했다(손실 임팩트는 기존 loopback에서 이미 검증).
- **M8-T02** (L1 병렬) — `optical/channel.py`에 `OpticalChannel(Channel)` 구현. **채널 프레이밍 nonce**:
  `send_frame`이 1바이트 롤링 카운터를 패킷 앞에 붙여(`bytes([nonce]) + frame`) 인코딩 → `display.show`.
  백그라운드 캡처 스레드(첫 `recv_frame`에서 지연 시작)가 `camera.read`→`decode_frame`(스레드로컬
  detector)→**직전 인도분과 바이트 비교 dedup**→nonce 떼고 인박스 큐에 인도. 재캡처는 버리고, nonce가
  다른 연속 동일 패킷(ARQ 재전송)은 각각 인도. 메모리 재캡처는 배열 **객체 identity 비교로 재디코드를
  생략**하는 빠른 경로 추가. 교차 스레드 공유 상태는 thread-safe 큐 + 원자적 `_closed` 플래그뿐. `pair()`로
  메모리 풀듀플렉스 페어 생성. `QRCapacityError`는 그대로 전파. (메인이 후처리로 `class
  OpticalChannel(Channel)` 명시 상속 추가 — 마일스톤 제목 정합, `issubclass` 성립 확인.)
- **M8-T03** (L1 병렬) — `optical/cv2_devices.py`에 `Cv2Display`/`Cv2Camera` 구현. `Cv2Display`는 창을
  지연 생성하고 `show`에서 `cv2.imshow`+`cv2.waitKey(1)`(렌더 펌프 필수), `Cv2Camera`는
  `cv2.VideoCapture`에서 BGR 프레임을 그대로 반환(`decode_frame`이 3채널을 흡수). **하드웨어·창 없이
  import 가능**(장치 접근은 전부 `__init__`/메서드 안). 미오픈 시 인덱스를 명시한 `RuntimeError`.
- **M8-T04** (L2 병렬) — `tests/test_optical_concurrency.py`. 양방향 동시 송수신(각 방향 30개 고유 패킷,
  4 워커 스레드)에서 **보낸 집합 == 받은 집합**(손실·오염·교차오염 0)을 단언 — 두 캡처 스레드의 동시
  `decode_frame`(스레드로컬 detector)이 크래시 없이 동작함을 본격 검증(M7 권장 3 해소, 스모크 초과).
  종료 무교착·idempotent close도 단언. 인터리빙은 비결정적이나 집합 동등성은 결정적이라는 점을 주석화.
- **M8-T05** (L2 병렬) — `tests/test_optical_channel.py`(5건): 양방향 왕복, 재캡처 dedup(1회 인도),
  연속 동일 패킷(2회 인도), idempotent close·종료 정합, 그리고 **세션 통합**(기존 `Session`을
  `OpticalChannel.pair` 위에서 핸드셰이크+데이터+graceful close까지 무변경 구동). cv2 QR detector가 작은
  QR을 못 잡으므로 패킷 크기(~88B) 페이로드 사용, 실 스레드 타이밍은 넉넉한 recv 타임아웃으로 흡수.
- **M8-T06** (L2 병렬) — `examples/optical_link.py`(기본 인메모리 모드 + `--real` 실물 디스플레이 모드),
  `optical/__init__.py`에 `OpticalChannel` 무조건 + cv2 디바이스 **가드 재export**(cv2 부재 시 `None`·`__all__`
  제외), `README.md` 로드맵 6단계 완료 표기·실행법·후속(양방향 광학=화면2·카메라2) 기록. 인메모리
  데모는 캡처 스레드의 비동기 인도를 라운드당 짧은 실 sleep로 흡수하고 라운드 캡+벽시계 데드라인으로
  무한루프를 차단. `python/__init__.py`(루트)는 변경 없음(버전 범프는 release 단계 몫).

## 변경 파일 요약

| 구분 | 파일 |
|---|---|
| 추가 | `photontcp/optical/__init__.py`, `photontcp/optical/devices.py`, `photontcp/optical/channel.py`, `photontcp/optical/cv2_devices.py`, `tests/test_optical_channel.py`, `tests/test_optical_concurrency.py`, `examples/optical_link.py` |
| 수정 | `README.md` |
| 삭제 | (없음) — `ImageLoopbackChannel`은 시뮬레이션 레퍼런스로 보존 |

## 테스트 결과

- `python -m pytest -q` → **172 passed in 6.59s** (M1~M7 166 + M8 신규 6: 광학 채널 5 + 동시성 1, 회귀 0).
- `python examples/optical_link.py`(기본 인메모리) → exit 0: 핸드셰이크 4라운드 → 메시지 양방향 MATCH →
  both CLOSED 3라운드(wall=0.44s).
- `issubclass(OpticalChannel, Channel)` → True(마일스톤 제목 `OpticalChannel(Channel)` 정합).
- cv2 가드: cv2 존재 시 `Cv2Display`/`Cv2Camera` 정상 export, `cv2_devices` import 실패 시 `None`으로
  떨어지고 나머지 인메모리 API는 그대로 동작.
- 플레이키 점검: T05는 5회 연속, T04는 튜닝 후 7회 연속 통과(데드라인 60s로 콜드스타트 여유 확보).

## 미해결·후속 메모

1. **패키지 전체 cv2-옵셔널은 미달성(범위 밖)**: `photontcp/qr/decode.py`가 모듈 상단에서 `import cv2`
   하므로 QR/광학 스택 전체가 opencv-python을 하드 요구한다. 완료 기준 8("하드웨어·창 없이 import")은
   충족하나, "cv2 부재에도 import" 수준의 진짜 옵셔널화를 원하면 `qr/decode.py`의 지연 import가 별도
   후속으로 필요(이번 태스크는 `cv2_devices` 한정 가드만 처리).
2. **`--real` 모드 범위**: 한 머신은 자기 창을 자기 카메라로 보기 어려워 진정한 양방향 광학 루프는
   화면 2·카메라 2가 필요하다. 현재 `--real`은 실 `Cv2Display`로 QR을 **실제 화면에 띄우는 디스플레이
   절반**만 실물로 증명하고 캡처는 인메모리다. v1.0 승격을 위한 **실물 카메라 캡처 왕복**(두 디바이스
   또는 외부 카메라로 화면을 촬영)은 다음 단계의 수동 검증으로 남는다.
3. **타이밍 의존 테스트**: 광학 채널은 실 스레드·실시간이라 테스트가 recv 타임아웃/데드라인에 의존한다.
   현재 넉넉히 잡아 안정적이나, 매우 느린 CI에서는 타임아웃 상향이 필요할 수 있다.
4. **실물 정렬·조명·동기**: 실제 카메라 캡처 시 QR 정렬·조명·프레임 동기(디스플레이 갱신율 vs 카메라
   프레임율) 튜닝이 필요. nonce dedup은 타이밍 무관하게 정확하지만, 캡처 누락 시 ARQ 재전송에 의존하므로
   `hold`/`poll_interval` 파라미터의 실물 튜닝이 후속 과제.
