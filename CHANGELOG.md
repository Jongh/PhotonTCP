# CHANGELOG

본 프로젝트의 모든 주목할 만한 변경 사항을 기록합니다.
형식은 [Keep a Changelog](https://keepachangelog.com/) 를, 버전은 [Semantic Versioning](https://semver.org/) 을 따릅니다.

## [Unreleased]

## [0.6.0] - 2026-06-19

파일 전송(로드맵 5단계). 신뢰성 있는 단방향 파일 전송 + 앱 레벨 완료 핸드셰이크.

### Added

- **파일 전송 앱** (`photontcp/app/file.py`): `FileSender`/`FileReceiver` — OFFER(name·size·sha256) → ACCEPT → CHUNK* → DONE → ACK/NACK. 전체 파일 **SHA-256 무결성 검증**, 진행률, 손실 채널에서 ARQ 재전송으로 무손실 전달.
- **앱 레벨 완료 핸드셰이크 = flush-on-close 보장**: 송신측은 수신측 FILE_ACK(신뢰성 스트림)를 받은 뒤에만 종료 → 전 청크 전달·검증 보장(M4 리뷰 후속 해소).
- **파일 프레임 코덱** (`photontcp/app/file_codec.py`): 타입 구분 길이접두 프레임(제어 JSON + 청크 바이너리 인터리브), `FileFrameReassembler`, `sha256_hex`.
- 파일 전송 예제(`examples/file_loopback.py`), 파일 테스트 30건(코덱·통합·무결성 실패·진행률·QR 채널 위 전송).
- 회고 문서(`docs/reports/retro.md`) — M1~M5 누적 회고.

### Notes

- Session 계층 무수정(채팅처럼 순수 추가). 파일 기본 stream_id는 채팅 기본(1)과 공유(병행 시 인자로 분리). 양방향·다중 파일·재개(resume)는 후속. M3/M4 잔여 리뷰 권장은 "신뢰성 정리 마일스톤"으로 이월.

## [0.5.0] - 2026-06-19

QR 코덱 실물 통합(로드맵 4단계). 패킷이 실제 QR 이미지로 인코딩/디코딩되어 채널을 통과.

### Added

- **QR 코덱** (`photontcp/qr/`): `encode_frame`(segno로 bytes→QR 그레이스케일 numpy 이미지), `decode_frame`(OpenCV `QRCodeDetector`로 이미지→bytes). 패킷 바이트를 base64로 ASCII-safe 래핑해 바이너리 무결성 보장.
- **ImageLoopbackChannel** (`photontcp/channel/image_loopback.py`): QR 이미지를 메모리로 주고받는 전이중 채널 — 모든 프레임이 실제 QR 인코드/디코드를 거침. 프레임 단위 loss/dup + 선택적 이미지 degrade(노이즈/블러로 EC 견딤 테스트).
- 기존 세션·ARQ·스트림·채팅 스택이 **수정 없이** QR 이미지 위에서 동작(`Channel` 인터페이스만 구현). QR 루프백 예제(`examples/qr_loopback.py`).
- QR 테스트 23건(코덱 라운드트립·바이너리 무결성·풀스택 통합; `pytest.importorskip`로 라이브러리 부재 시 skip).

### Dependencies

- 광학 코덱용 선택 의존성 도입: `segno`(QR 생성), `opencv-python`(QR 디코드), `numpy`. 핵심 전송 스택은 여전히 표준 라이브러리만 사용하며, QR은 채널 계층에서만 필요(`pip install photontcp[qr]` / `[optical]`).

### Notes

- 단일 QR 프레임 용량 한계가 있어 `max_payload`는 QR 용량 내로 유지해야 함. 실제 화면/카메라(실물 광학)는 후속 마일스톤. M3/M4 리뷰 권장(NACK 억제·flush-on-close 등) 계속 이월.

## [0.4.0] - 2026-06-19

스트림 다중화 + 채팅 앱(로드맵 3단계) — **기본 통신 완성**.

### Added

- **스트림 다중화** (`photontcp/stream/`): `StreamMux` — 하나의 세션 위에 stream_id별 독립 ARQ. 한 스트림의 손실/구멍이 다른 스트림을 막지 않음(HOL 블로킹 제거). `stream_id=0`=제어, `≥1`=앱 스트림, implicit open + 개시자/응답자 패리티 할당.
- **채팅 앱** (`photontcp/app/`): `ChatSession` — 신뢰성 스트림 위 길이접두(4B)+JSON 메시지(`{msg_id, timestamp, text}`). `StreamReassembler`로 청크 경계 무관 재조립, 유니코드 보존. 손실 채널 양방향 채팅 예제(`examples/chat_loopback.py`).
- `Session` 스트림 API: `open_stream()` / `send_on()` / `recv_on()` / `recv_all()`.
- 다중화·채팅 테스트 23건(mux 격리·implicit open·패리티 / 코덱 재조립 / 손실 양방향 채팅).

### Changed

- `Session` 데이터 경로를 단일 ARQ → `StreamMux`로 통합. 레거시 `send()`/`recv()`(기본 스트림 1)는 그대로 보존(M2·M3 회귀 없음). 데이터 ACK가 stream_id≥1을 달아 핸드셰이크 ACK와 라우팅만으로 구분(ACK 분기 단순화).

### Notes

- flush-on-close·반이중(half-close)·per-stream 수명주기, M3 리뷰 권장(NACK 억제 등)은 후속(M5 파일 전송/정리 마일스톤)으로 이월.

## [0.3.0] - 2026-06-19

신뢰성 계층(로드맵 2단계). 손실·중복·순서뒤바뀜 채널 위에서 신뢰성 있는 순서 보장 전송.

### Added

- **신뢰성 계층** (`photontcp/reliability/`): Selective Repeat(SR) ARQ.
- **SR ARQ 엔진** (`ArqEndpoint`): 송신 슬라이딩 윈도우 + 선택 재전송, 수신 재정렬·중복제거 버퍼, 누적 ACK + 선택 NACK, 기본 흐름 제어(상대 광고 윈도우 반영).
- **적응형 RTO** (`RtoEstimator`): Jacobson/Karels SRTT/RTTVAR, min/max 클램프, 타임아웃 지수 백오프, Karn 규칙 RTT 샘플링.
- **32비트 wraparound-safe 시퀀스 산술** (`serial`): RFC1982 류 시리얼 번호 비교.
- **제어 패킷 재전송 + 수립 타임아웃**: SYN/SYN_ACK/FIN 손실 시 RTO 재전송, 재시도 한도 초과 시 `CONNECT_FAILED`/`TIMED_OUT`. 핸드셰이크 최종 ACK 손실도 복구.
- **Session 신뢰 데이터 경로**: `Session.send(data)` / `Session.recv()` — 손실 채널에서 바이트열 무손실·순서 전달. 손실 채널 신뢰 전송 예제(`examples/reliable_loopback.py`).
- 신뢰성 테스트 42건(serial·RTO·ARQ 엔진·신뢰성 세션 통합).

### Changed

- 세션 상태머신/드라이버가 제어 재전송과 ARQ 데이터 경로를 통합(M2 무손실 동작은 회귀 없이 유지).

### Notes

- M1 리뷰 권장(seq mod 2³²)·M2 리뷰 권장(제어 재전송·수립 타임아웃) 해소. NACK 다중 블록·반이중(half-close)·스트림 다중화·패킷 간 FEC는 후속(M4/최적화)으로 이월.

## [0.2.0] - 2026-06-19

세션 계층(로드맵 1단계). M1 전송 토대 위에 연결 지향 세션 관리를 추가.

### Added

- **세션 계층** (`photontcp/session/`): I/O와 분리된 순수 세션 상태머신 + 동기 펌프 드라이버(백그라운드 스레드 없음).
- **3-way 핸드셰이크**(SYN/SYN_ACK/ACK): `session_id` 합의 + 초기 시퀀스(ISN) 교환.
- **graceful 종료**(FIN/FIN_ACK): 대칭 auto-close — 단일 `close()`로 양쪽이 CLOSED에 수렴, 수동측에 `PEER_CLOSED` 통지.
- **하트비트/타임아웃**: 유휴 시 HEARTBEAT 송출, 무수신 `idle_timeout` 초과 시 `TIMED_OUT` + CLOSED.
- **주입 가능한 클럭**: `Clock` 인터페이스, 테스트용 `ManualClock`(가상 시간), 실운영용 `MonotonicClock`. 하트비트·타임아웃을 real sleep 없이 결정적으로 검증.
- `Session` 고수준 API(`connect`/`close`/`pump`/`run_until`, `state`/`is_established`/`is_closed`) + 세션 데모 예제(`examples/session_loopback.py`).
- 세션 단위 테스트 17건(핸드셰이크·종료·하트비트·타임아웃).

### Notes

- M2 범위는 무손실 `LoopbackChannel` 가정. 제어 패킷 손실 재전송·수립 타임아웃, `seq`/`ack` 범위 정책(mod 2³²), 스레드 안전성, 클럭 latency/jitter는 M3(ARQ)로 이월.

## [0.1.0] - 2026-06-19

최초 릴리즈. PhotonTCP의 최하위 두 계층(채널 추상화 + 패킷 직렬화) 토대.

### Added

- tide 개발 사이클 골격 초기화 (kickoff): 규약·마일스톤/리포트 구조·CHANGELOG.
- **패킷 계층**: 22바이트 고정 헤더(big-endian) + CRC32 무결성. `Packet` 데이터클래스의 `pack()`/`unpack()`, 예외 계층(`PacketError`/`ChecksumError`/`MalformedPacketError`).
- **패킷 타입·플래그**: `PacketType`(SYN/SYN_ACK/ACK/DATA/NACK/FIN/FIN_ACK/HEARTBEAT), `Flags`, `PROTOCOL_VERSION`.
- **채널 계층**: 교체 가능한 `Channel` 추상 인터페이스.
- **LoopbackChannel**: 메모리 큐 기반 전이중 가상 채널 + 노이즈 시뮬레이션(loss/dup/corrupt/reorder), seed 기반 결정적 재현.
- 단위 테스트(13건) 및 에코 예제(`examples/echo_loopback.py`).
