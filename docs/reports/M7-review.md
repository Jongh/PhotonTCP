# M7 리뷰보고서 (review)

## 비판점

### 차단 (0건)

차단 이슈 없음. 완료 기준 1~11 충족, 전체 166 테스트 통과(M1~M6 회귀 0), 예제 6종 정상. 사용자가 선택한
4개 항목군이 모두 처리됐고, 핵심 변경(ARQ NACK 억제/재전송 상한, 종료-인지 idle, 세션 통합)이 정확하다.
하드닝 목적대로 외부 동작은 보존하면서 손실·악성 입력·동시성 가장자리가 더 안전해졌다. 소스 결함 발견 없음.

검증한 핵심 정확성:
- **NACK 억제**: `_nacked` 집합 + `_prune_nacked`(rcv_base 전진 시 stale 제거)로 구멍당 1회, 같은 seq의 새 구멍은 재-NACK 가능. RTO 백업 유지.
- **재전송 상한**: `on_tick`에서 `retx_count >= max_retx`면 `_failed=True` + `SEND_FAILED` 후 즉시 중단(무한 재전송 차단).
- **종료-인지 idle**: FIN_WAIT/CLOSE_WAIT 타임아웃 → CLOSED, ESTABLISHED만 TIMED_OUT.
- **통합**: 앱 스트림 수신 시 `note_data_activity` 호출, `rto.clone` factory(비공개 속성 접근 제거), acked/failed 위임·전파.

### 권장 (3건)

1. **`Session`이 ARQ `max_retx`를 미노출** — 데이터 재전송 상한이 ARQ 기본(8) 고정. 세션/앱에서 튜닝하려면
   `Session.__init__`에 `arq_max_retx` 추가가 필요(T08은 가상시간으로 기본값 소진해 검증). 저우선.
2. **재전송 실패가 폴링 기반(`data_failed_streams()`)** — `SEND_FAILED`가 push 이벤트로 앱까지 전달되지
   않고 조회로만 노출. 파일/채팅 앱은 현재 이를 능동 감지하지 않으므로, 데이터 스트림이 영영 실패하면
   상위가 무한 대기할 수 있다(테스트는 유한 상한으로 회피). 앱 레벨에서 `data_failed_streams()`를 주기
   확인하거나 pump 반환에 실패 신호를 싣는 보강을 후속에서 검토.
3. **스레드 안전성은 "스모크" 검증 수준** — 멀티스레드 크래시 없음만 확인(결정성/정확성 보장 아님). 단일
   스레드 모델에선 충분하나, **M6 실물 광학에서 카메라 백그라운드 스레드를 도입할 때** 실제 동시 송수신
   정확성(공유 rng 락 범위·스레드로컬 detector)을 본격 재검증할 것.

### 사소 (2건)

4. **acked progress가 프레임 오버헤드 포함** — `acked_bytes`는 ARQ payload(파일 프레임 헤더 포함) 누적이라
   파일 순수 바이트보다 약간 큼 → `min(.,1.0)` 클램프로 처리(완료 시 1.0 보장). 정밀 진행률이 필요하면
   파일 페이로드 바이트만 별도 집계.
5. **`FILE_STREAM_ID=2` 고정** — 채팅(1)과 분리돼 기본 병행은 가능하나, **동시에 두 개 이상의 파일 전송**은
   기본값(2) 충돌 → `open_stream`으로 분리 필요(현 단방향 단일 파일 범위에선 무관).

## 수정 내용

- 차단 0건이라 리뷰 단계의 소스 수정은 없음.

## 검증

- `python -m pytest -q` → **166 passed in 4.41s** (M1~M6 139 + M7 27, 회귀 0).
- 예제 6종 전부 정상(파일 stream 기본 1→2 변경에도 회귀 없음).
- `tests/test_hardening.py`(27건)가 완료 기준 1~10을 1:1 매핑 검증(NACK 억제·재전송 상한·종료 정합성·
  데이터 idle 갱신·clone·스레드 스모크·QR 용량 가드·파일/채팅 stream 병행·OFFER 검증·acked progress).
- 잔여 리스크: 권장 2(실패 push)·3(스레드 정확성)은 장기 실패·실물 동시성에서 재검토 필요. 현재 범위에선 문제 없음.

## 릴리즈 판정

**가능** — 추천 버전: **v0.7.0 (minor)**

- 완료 기준 1~11 전부 충족, 차단 이슈 0건. 하드닝이지만 신규 공개 API(`RtoEstimator.clone`·`acked_bytes`·
  `Session.control_rto`/`max_control_retries`·`data_failed_streams`·`QRCapacityError`·파일 전용 stream·
  `ArqEvent`) 추가 = minor. 기반 v0.6.0(릴리즈 완료) → 목표 v0.7.0.
- 회고가 부각한 누적 권장(M1·M3·M4·M5·M6)이 대거 해소됨. 권장 3건·사소 2건은 모두 후속(실물 광학/튜닝)이
  집어갈 항목으로 릴리즈를 막지 않는다.

## 다음 단계

- 릴리즈: **`/tide:release v0.7.0`** (`.tide/release-mode`=`release` 저장돼 질문 없이 release 모드).
- 릴리즈 후 로드맵 남은 단계:
  - **6단계 실물 광학(화면 디스플레이 + 카메라 캡처)** — `ImageLoopbackChannel`을 실물로 교체. M7에서 깐
    스레드 안전성(락·스레드로컬 detector) 토대 위에서 카메라 스레드를 도입하고, 권장 3(동시성 정확성)을
    본격 검증. 여기까지 가면 "빛만으로 실 통신" 증명 → **v1.0 후보**.
  - **7단계 최적화** — 적응형 RTO 튜닝·흐름제어·패킷 간 FEC.
  - (후속) 권장 1·2(arq max_retx 노출·실패 push), 양방향/다중 파일·재개, half-close.
  다음 사이클 시작 시 우선순위를 확인할 것.
