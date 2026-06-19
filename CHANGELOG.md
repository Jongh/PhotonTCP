# CHANGELOG

본 프로젝트의 모든 주목할 만한 변경 사항을 기록합니다.
형식은 [Keep a Changelog](https://keepachangelog.com/) 를, 버전은 [Semantic Versioning](https://semver.org/) 을 따릅니다.

## [Unreleased]

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
