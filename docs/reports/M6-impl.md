# M6 완료보고서 (impl)

## 개요

마일스톤 M6(로드맵 5단계 — 파일 전송)의 5개 태스크(M6-T01~T05)를 전부 구현했다. 타입 구분 길이접두
프레임 코덱(제어 JSON + 청크 바이너리 인터리브), `FileSender`/`FileReceiver` 앱, 그리고 **앱 레벨 완료
핸드셰이크**(FILE_DONE → 수신측 전체 SHA-256 검증 → FILE_ACK/FILE_NACK → 송신측 종료)를 완성했다.
손실 있는 채널 + 가상 클럭에서 한 파일이 손실 없이 전부 전달되고 수신 파일의 SHA-256이 원본과 일치함이
결정적으로 검증된다. 회고에서 최우선 미반영 후속으로 부각됐던 **M4 flush-on-close가 앱 레벨 완료
핸드셰이크로 해소**됐다(송신측은 FILE_ACK를 신뢰성 스트림으로 수신한 뒤에만 종료 → 전 청크 전달·검증 보장).

확정 설계 결정 4건 반영: **단일 스트림 인터리브 / 전체 SHA-256 / 앱 레벨 완료 핸드셰이크 / 단방향 단일
파일+완료확인+진행률**. Session 계층은 **수정하지 않았다**(완료 핸드셰이크가 앱 레벨이라 채팅처럼 순수 추가).
의존성 위상 순서·병렬 디스패치: L0=T01, L1=T02·T03, L2=T04·T05 (각 레벨 파일 비겹침).

## 태스크별 수행 내용

- **M6-T01** — 파일 프레임 코덱(`app/file_codec.py`). `FileFrameType`(OFFER/ACCEPT/REJECT/CHUNK/DONE/ACK/NACK), `encode_frame`(4B 길이[=1+본문] + 1B 타입 + 본문), `encode_control`/`decode_control`(JSON), `FileFrameReassembler.feed`(완전 프레임 추출, 경계 걸침), `sha256_hex`. 방어: `MAX_FRAME_BYTES=16MiB` 초과·미지 타입·길이<1 → `ValueError`. 순수 stdlib.
- **M6-T02** — `FileSender`/`FileReceiver`(`app/file.py`, 패키지 re-export). 동기 펌프 드라이버(채팅과 동일 패턴). 상태 `FileTransferState`(IDLE/OFFERED/SENDING/DONE_SENT/RECEIVING/COMPLETE/FAILED). Sender: start→OFFER, ACCEPT→청크 송신→DONE, **FILE_ACK 수신 전 close 금지**(`is_complete`만 노출, 호출자가 종료). Receiver: OFFER→ACCEPT, CHUNK 누적, DONE→SHA 검증→ACK/NACK. 속성 progress/file_bytes/name/verified. Session 미수정.
- **M6-T03** — 파일 코덱 테스트(`tests/test_file_codec.py`, 19건): 제어 라운드트립, CHUNK 전 바이트값, 인터리브, 경계 걸침, `sha256_hex` 벡터, 방어(ValueError).
- **M6-T04** — 파일 전송 통합 테스트(`tests/test_file_transfer.py`, 11건, 0.58s): 무손실 4096B/8청크 전송·SHA 일치, 손실(loss=0.2, seed [2,3,12,13]) 재전송 복구, 완료 핸드셰이크/flush-on-close, **무결성 실패 경로**(FileSender 서브클래싱으로 틀린 sha OFFER→FILE_NACK→FAILED), 진행률 단조, 결정성, QR 채널 위 1건(importorskip).
- **M6-T05** — 파일 전송 예제(`examples/file_loopback.py`). loss=20% seed=7에서 3072B "demo.bin" 전송(17라운드, 청크 재전송 발생), 진행률 25/50/75/100%, byte+SHA-256 MATCH, verified, 양쪽 CLOSED. ~0.27s.

## 변경 파일 요약

| 구분 | 파일 |
|---|---|
| 추가 | `photontcp/app/file_codec.py`, `photontcp/app/file.py`, `tests/test_file_codec.py`, `tests/test_file_transfer.py`, `examples/file_loopback.py` |
| 수정 | `photontcp/app/__init__.py`(`FileSender`/`FileReceiver`/`FileFrameType`/`FileTransferState` re-export 추가, 기존 chat re-export 유지) |
| 삭제 | (없음) |

## 테스트 결과

- `python -m pytest -q` → **139 passed in 4.16s** (M1~M5 109 + M6 30, 회귀 없음).
- 예제 6종 전부 정상: `echo`·`session`·`reliable`·`chat`·`qr`·`file` loopback.
- 완료 기준 1~9 충족: 코덱 인터리브 라운드트립·sha, 무손실/손실 전체 전송·SHA 검증·name 보존, 완료 핸드셰이크(flush-on-close)·양쪽 CLOSED, 무결성 실패→FILE_NACK, 진행률, Session 무수정·전용 스트림 병행 가능, 예제, 전체 통과.
- 이번 사이클도 서브에이전트가 소스 결함을 발견하지 않았다.

## 미해결·후속 메모

1. **파일 기본 stream_id가 채팅 기본(=1)과 동일** — `FILE_STREAM_ID = DEFAULT_STREAM_ID`(인자로 오버라이드 가능). 채팅과 **동시 병행**하려면 호출자가 다른 stream_id를 지정해야 한다(API는 지원, 기본값만 공유). 채팅+파일 동시 데모/기본 분리가 필요하면 후속에서 별도 기본 상수 부여 검토.
2. **양방향/다중 파일·재개(resume) 미지원** — 단방향 단일 파일로 범위 한정(확정 결정). 필요 시 후속 마일스톤(offset 기반 재개·다중 전송).
3. **`auto_accept=False` 수동 수락 경로 미완성** — OFFER 수신 후 메타만 저장하고 IDLE 유지(수동 accept 메서드는 본 마일스톤 범위 밖, docstring 명시).
4. **chunk_size ↔ QR 용량** — 파일 chunk_size(기본 1024)는 ARQ가 `max_payload`(≤~200B)로 다시 패킷화하므로 QR 용량(M5)에는 직접 영향 없음. 단 ImageLoopbackChannel 위 대용량 파일은 프레임 수가 많아 cv2 디코드 비용↑(테스트는 작은 파일로 제한).
5. M3/M4 리뷰 잔여 권장(NACK 억제·데이터 idle 타이머·재전송 상한/파라미터 노출·종료 이벤트 정합성)은 여전히 이월 — 회고 제안대로 "신뢰성 정리 마일스톤"으로 묶을 후보.
6. 비범위/이월: 실물 광학(화면+카메라, 로드맵 6단계), 스레드 안전성(공유 RNG·cv2 detector), 패킷 간 FEC(7단계).
