# M9 완료보고서 (impl)

## 개요

M9(실물 광학 링크 하드닝 + 검증)의 6개 태스크를 전부 구현했다. M8이 인메모리로만 검증한
`OpticalChannel`의 **실 카메라 경로**를 동작·검증 가능한 상태로 끌어올렸다: `Cv2Camera` 버퍼 신선도,
디스플레이 프레임 페이싱(`hold`), 재캡처 dedup의 윈도우화, 파라미터 하한 가드를 보강하고, **한 기기
셀프체크**(반이중)와 **2-머신 실 왕복**(`--real --role`) 하니스 + v1.0 사인오프 게이트 문서를 추가했다.
코드/CI로 검증 가능한 부분(페이싱·가드·dedup)은 인메모리 결정적 테스트로 못박았고, 실 하드웨어 동작은
스크립트화된 하니스 + 문서 절차 + review 수동 사인오프로 갈음한다. 디스패치: L0 = T01 ∥ T02(파일
disjoint 병렬), L1 = T03 ∥ T04 ∥ T05(병렬), L2 = T06(메인 직접). 전체 **175 테스트 통과**(M1~M8 172 +
M9 신규 3, 회귀 0).

## 태스크별 수행 내용

- **M9-T01** (L0 병렬) — `Cv2Camera` 신선 프레임 보장. 생성 시 `CAP_PROP_BUFFERSIZE=1`(best-effort,
  guarded) + 선택적 해상도 힌트(`width`/`height`). `read`에 `drain_to_latest=True`(기본): 1회 `read` 후
  **고정 한도 `_MAX_DRAIN=4`** 만큼 `grab()`/`retrieve()`로 최신 프레임까지 폐기·취득(유한 한도라
  무한 루프·과지연 없음). 기존 BGR `ndarray`/`None` 계약·import 하드웨어 비접촉 유지.
- **M9-T02** (L0 병렬) — 디스플레이 페이싱 + 파라미터 가드. `OpticalChannel(hold=…)`이 단조 시계 기반
  최소 표시 간격 보장(`send_frame`이 부족분만 실 sleep). **`hold=0`(기본)은 sleep·동작 변화 없음**(기존
  테스트 불변). `poll_interval<=0`은 `_MIN_POLL_INTERVAL=1e-3`으로 클램프(busy-spin 차단), `hold<0`은
  0으로 클램프. `MemoryCamera.read`도 repeat_last + falsy timeout 시 `_MIN_REPEAT_WAIT=1e-3` 하한으로
  직접 tight-loop 호출자의 busy-spin 방지(채널 경로 타이밍·결정성 무영향).
- **M9-T03** (L1 병렬, **통합 시 설계 조정**) — 재캡처 dedup 윈도우화. `_capture_loop`의 단일
  `last_delivered` 슬롯을 **최근 N(`_DEDUP_DEPTH=8`) channel_frame deque + set(O(1))**로 교체 — 순서
  흔들림(이전 프레임을 카메라가 잠깐 다시 봄)에도 거짓 인도/누락 없이 1회 인도. **nonce 폭 결정 변경**:
  마일스톤은 다중 바이트 nonce(예 2B)를 제안했으나, 2B는 모든 프레임의 바이트를 이동시켜 한 세션
  프레임이 cv2 QR detector의 **콘텐츠 의존 블라인드스폿**에 빠져 결정적으로 디코드 불가→
  `test_session_handshake_…`(ManualClock 비전진이라 ARQ 재전송 없음)가 영구 정지(회귀)했다. 업스케일
  재시도 폴백을 실측했으나 그 특정 이미지는 어느 배율로도 디코드 불가였다. → **nonce를 1바이트로 유지**.
  윈도우 dedup이 dedup 깊이(8) ≪ 래핑 주기(256)를 보장하므로 **래핑 거짓 dedup(권장 3의 본래 목표)은
  윈도우만으로 이미 제거**된다(래핑된 nonce가 비교 윈도우 안에서 충돌 불가). 즉 criterion 4의 *보장*은
  충족하되 메커니즘을 다중 바이트 nonce 대신 윈도우 dedup으로 달성. 프레이밍은 `_NONCE_BYTES` 상수로
  파라미터화돼 있어 추후 폭 확대 시 다른 코드 변경 불필요. (docstring·주석에 사유 반영.)
- **M9-T04** (L1 병렬) — `examples/optical_selfcheck.py`(신규). 한 기기 반이중 실 경로 검증 하니스:
  `Cv2Display`로 고유 QR 다수를 표시하고 그 화면을 비춘 `Cv2Camera`로 캡처·`decode_frame`→표시 집합과
  비교해 hit/miss/mismatch·수신율 **PASS/FAIL**(기본 임계 80%) 출력. cv2/카메라 부재는 graceful(0 종료).
- **M9-T05** (L1 병렬) — `examples/optical_link.py`에 `--role {sender,receiver}` 추가. `--real --role …`가
  실제 `Cv2Display`+`Cv2Camera`로 `OpticalChannel`을 구성해 단일 피어 세션 드라이브(2-머신 마주보기).
  `--role` 단독(–real 없이)은 argparse 거부. 기본 인메모리·기존 `--real`(디스플레이만) 회귀 없음.
  `--scale`/`--hold` 추가, 무하드웨어 graceful 처리.
- **M9-T06** (L2 메인 직접) — `tests/test_optical_pacing.py`(신규 3건) + `README.md`. 테스트: (a) `hold`
  페이싱 차등 검증(차이로 측정해 머신 속도/부하에 견고)·(b) `poll_interval`/`hold` 하한 가드·(c) 스크립트
  카메라로 **순서 흔들림 재캡처(A,B,A,C)** 시 윈도우 dedup이 두 번째 A를 떨구고 A·B·C를 각 1회 순서대로
  인도(단일 슬롯이라면 오인도). README에 M9 하드닝·실물 검증 절차(셀프체크·2-머신)·**v1.0 사인오프
  게이트** 명시.

## 변경 파일 요약

| 구분 | 파일 |
|---|---|
| 추가 | `examples/optical_selfcheck.py`, `tests/test_optical_pacing.py` |
| 수정 | `photontcp/optical/cv2_devices.py`, `photontcp/optical/channel.py`, `photontcp/optical/devices.py`, `examples/optical_link.py`, `README.md` |
| 삭제 | (없음) |

## 테스트 결과

- `python -m pytest -q` → **175 passed**(2회 연속 동일, ~7.2s). M1~M8 172 + M9 신규 3(pacing). 회귀 0.
- 광학 테스트(`test_optical_channel.py` 5 + `test_optical_concurrency.py` 1) 3회 연속 통과(무플레이키).
- `examples/optical_link.py`(기본 인메모리) exit 0, `--help`에 `--role`/`--hold`/`--scale` 노출,
  `--role`(–real 없이)은 exit 2(graceful). `optical_selfcheck.py --help` 정상, 무카메라 graceful.
- `photontcp/qr/decode.py`는 무변경(통합 중 업스케일 폴백을 실측 후 되돌림 — git diff 없음).
- **발견·수정 이슈**: (1) T03의 2바이트 nonce가 cv2 디코드 블라인드스폿으로 세션 통합 테스트를 깨뜨림
  → nonce 1바이트 유지 + 윈도우 dedup으로 목표 달성(위 T03 참조). (2) pacing 테스트의 절대 임계
  보조 단언이 부하 시 flaky → 차등 단언만 유지(견고).

## 미해결·후속 메모

1. **nonce 폭 = 1바이트(설계 조정)**: 마일스톤 criterion 4의 문구는 "다중 바이트 nonce"지만, 실측상
   2바이트가 cv2 디코드 회귀를 유발해 **윈도우 dedup으로 동일 보장(래핑 거짓 dedup 제거)을 달성**하고
   nonce는 1바이트로 두었다. 리뷰에서 이 메커니즘 대체의 수용 여부를 판정할 것. (코드는 `_NONCE_BYTES`로
   파라미터화 — 추후 cv2 디코드 견고화가 되면 폭 확대 가능.)
2. **cv2 디코드 콘텐츠 의존 블라인드스폿**: 특정 v8 QR을 cv2가 어느 배율로도 디코드 못 하는 사례 확인.
   실 링크에선 ARQ가 새 nonce로 재전송해 회복하지만(가상시간 비전진 테스트만 영구 정지), **디코드
   견고화(대체 detector·전처리)**는 수신율 향상을 위한 별도 후속 가치가 있다(M9 범위 밖).
3. **실 하드웨어 검증은 CI 미수행**: 셀프체크 수신율·2-머신 왕복은 하드웨어 필요 — review/release 전
   **수동 사인오프**(README의 v1.0 게이트)가 v1.0 승격의 전제. 미통과 시 하드닝만 반영해 v0.9.0으로 폴백.
4. **실물 튜닝 잔여**: `hold`/`poll_interval`/`scale`·조명·정렬이 실 수신율을 좌우 — 셀프체크로 환경별
   튜닝 권장. `CAP_PROP_BUFFERSIZE`는 백엔드가 무시할 수 있어 드레인이 실질 안전망.
