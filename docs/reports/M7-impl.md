# M7 완료보고서 (impl)

## 개요

마일스톤 M7(신뢰성 정리/하드닝)의 8개 태스크(M7-T01~T08)를 전부 구현했다. 사용자가 선택한 4개 항목군
(신뢰성 견고성·API 견고성·입력 방어·스레드 안전성)을 모두 처리해, M1~M6 동안 이월한 리뷰 권장을 대거
해소했다. 기능 추가가 아니라 하드닝이므로 외부 동작(채팅·파일·QR·세션)은 보존되면서 손실·악성 입력·
동시성 가장자리에서 더 안전해졌다. **M1~M6 회귀 0**(전체 166 테스트 통과).

파일 소유를 태스크별 배타 분리해 충돌 없이 병렬 디스패치했다: L0=T01·T02·T03·T04·T05(5병렬, 각자 다른
모듈), L1=T06(session+mux), L2=T07(chat+file), L3=T08(테스트).

## 태스크별 수행 내용

- **M7-T01** — ARQ 하드닝(`reliability/arq.py`). NACK 억제(`_nacked` 집합, 구멍당 1회; rcv_base 전진 시 prune), 데이터 재전송 상한(`max_retx=8`, 초과 시 `is_failed` + `ArqEvent.SEND_FAILED`, 재전송 중단), `acked_bytes` 카운터 노출.
- **M7-T02** — `RtoEstimator` 공개 설정 + `clone()`(`reliability/rto.py`). `initial_rto`/`min_rto`/`max_rto` 읽기 프로퍼티, `clone()`이 동일 설정·상태 초기화 새 인스턴스 반환.
- **M7-T03** — 상태머신 정합성 + 활동 훅(`session/state_machine.py`). 종료-인지 idle: FIN_WAIT/CLOSE_WAIT 타임아웃 → `CLOSED`(ESTABLISHED만 `TIMED_OUT`). `note_data_activity(now)`로 데이터 평면 수신이 `last_recv` 갱신. states.py 변경 불필요(기존 이벤트 재사용).
- **M7-T04** — 루프백 RNG 스레드 안전성(`channel/loopback.py`, `image_loopback.py`). `pair`가 공유 rng + 공유 `threading.Lock` 주입, 난수 추출을 락으로 보호. 단일 스레드 비경쟁 → 기존 seed 결정성·테스트 패턴 보존.
- **M7-T05** — QR 스레드 안전성 + 용량 가드(`qr/decode.py`·`encode.py`·`__init__.py`). detector를 `threading.local`로(스레드별 인스턴스), `encode_frame`이 segno `DataOverflowError`를 `QRCapacityError`(크기 정보 포함)로 변환·re-export.
- **M7-T06** — 세션/믹스 통합(`session/session.py`, `stream/mux.py`). `control_rto`/`max_control_retries` 노출→state_machine, 데이터 라우팅 시 `note_data_activity` 호출, `rto.clone` factory(비공개 속성 접근 제거), `StreamMux.acked_bytes`/`Session.acked_bytes`, `SEND_FAILED` 전파(`MuxOutput.failed_streams`/`failed_stream_ids()`/`Session.data_failed_streams()`).
- **M7-T07** — 앱 하드닝(`app/chat.py`, `app/file.py`). `ChatSession.received` 복사본 반환, **`FILE_STREAM_ID=2`로 분리**(채팅 1과 병행), OFFER 필드 검증(`name`/`size`/`sha256` 타입·존재, 손상 시 REJECT+FAILED), `FileSender.progress`를 `session.acked_bytes/total`(클램프)로 — ACK 기반.
- **M7-T08** — 하드닝 테스트(`tests/test_hardening.py`, 27건). 완료 기준 1~10을 1:1 매핑 검증(NACK 억제, 재전송 상한, 종료 정합성, 데이터 idle 갱신, clone, 스레드 스모크, QR 용량 가드, 파일/채팅 stream 병행, OFFER 검증, acked progress).

## 변경 파일 요약

| 구분 | 파일 |
|---|---|
| 추가 | `tests/test_hardening.py` |
| 수정 | `photontcp/reliability/arq.py`, `photontcp/reliability/rto.py`, `photontcp/session/state_machine.py`, `photontcp/channel/loopback.py`, `photontcp/channel/image_loopback.py`, `photontcp/qr/decode.py`, `photontcp/qr/encode.py`, `photontcp/qr/__init__.py`, `photontcp/session/session.py`, `photontcp/stream/mux.py`, `photontcp/app/chat.py`, `photontcp/app/file.py` |
| 삭제 | (없음) |

> 참고: `session/states.py`는 변경 불필요(기존 `CLOSED`/`TIMED_OUT` 재사용), `app/__init__.py`는 export 심볼 불변으로 미변경 — 마일스톤 "파일 변경 요약"의 후보 중 실제 미변경 항목.

## 테스트 결과

- `python -m pytest -q` → **166 passed in 4.41s** (M1~M6 139 + M7 27, **회귀 0**).
- 예제 6종 전부 정상: `echo`·`session`·`reliable`·`chat`·`qr`·`file` loopback (파일 stream 기본 1→2 변경에도 양측 동일 기본이라 회귀 없음).
- 완료 기준 1~11 충족: NACK 억제, 재전송 상한, 종료 이벤트 정합성, 데이터 idle 갱신, `clone()`, 스레드 안전성, QR 용량 가드, 파일/채팅 stream 병행, OFFER 검증, acked progress, 전체 통과.
- 이번 사이클도 서브에이전트가 소스 결함을 발견하지 않았다(하드닝 자체가 목적).

## 미해결·후속 메모

1. **`Session`이 ARQ `max_retx`를 미노출** — 데이터 재전송 상한은 ARQ 기본(8) 고정. 세션/앱에서 튜닝하려면 `Session.__init__`에 `arq_max_retx` 추가 노출 고려(T08은 큰 시간 전진으로 기본값 소진해 검증). 저우선.
2. **acked progress의 오버헤드 포함** — `acked_bytes`는 ARQ payload(프레임 헤더 포함) 누적이라 파일 순수 바이트보다 약간 큼 → `min(.,1.0)` 클램프로 처리. 정밀 진행률이 필요하면 파일 페이로드 바이트만 별도 집계 고려. 저우선.
3. **스레드 안전성은 "스모크" 수준 검증** — 크래시 없음만 확인(결정성 보장 아님). 실제 동시 송수신(M6 카메라 스레드)에서의 정확성은 실물 광학 마일스톤에서 본격 검증 필요.
4. 비범위/이월: 실물 광학(화면+카메라, 로드맵 6단계), 패킷 간 FEC(7단계), 양방향/다중 파일·재개(resume), half-close.
