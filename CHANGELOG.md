# CHANGELOG

본 프로젝트의 모든 주목할 만한 변경 사항을 기록합니다.
형식은 [Keep a Changelog](https://keepachangelog.com/) 를, 버전은 [Semantic Versioning](https://semver.org/) 을 따릅니다.

## [Unreleased]

## [0.10.0] - 2026-06-23

cv2 QR 디코드 견고화(로드맵 후속). 카메라가 잡은 QR 프레임의 디코드 성공률을 끌어올려, v1.0 사인오프의 셀프체크 수신율 게이트 통과 여유를 키움. 공개 디코드 API·시그니처 불변.

### Added

- **전처리 변형 캐스케이드** (`decode_frame`): 저비용→고비용 변형을 결정적으로 시도하고 첫 성공에서 단락 — (1) 원본 그레이스케일, (2) Otsu 이진화, (3) 샤프닝 후 이진화, (4) 이진화·샤프닝 결과의 2.0×/3.0× 업스케일. 깨끗한 프레임은 변형 1에서 즉시 성공(핫패스 추가비용 0), 나머지는 지연 평가.
- **대체 detector 폴백**: 1차 `cv2.QRCodeDetector`가 모든 변형에서 실패하면 빌드가 제공하는 대체 detector(`cv2.wechat_qrcode_WeChatQRCode` 우선, 없으면 `cv2.QRCodeDetectorAruco`)로 동일 변형을 재시도해 콘텐츠 의존 블라인드스폿을 회복. 둘 다 없는 환경은 폴백을 조용히 생략(캐스케이드 단독 동작).
- **디코드 견고성 벤치/테스트**: `examples/qr_decode_bench.py`(하드웨어 불필요, base-only vs 풀 디코드율 비교), `tests/test_qr_robustness.py`(코퍼스 임계 회귀 가드 + 블라인드스폿 회복 단언).
- **광학 채널 EC 견고화**: `OpticalChannel(error=…)`의 트레이드오프 문서화 + EC 배선 회귀 테스트(`tests/test_optical_ec.py`). 기본값 `"m"` 유지.

### Changed

- **채널 프레이밍 nonce 1→2바이트**: 디코드 견고화로 nonce 폭이 QR 콘텐츠 블라인드스폿에서 분리됨에 따라, M9가 보류한 다중 바이트 nonce를 2바이트(mod-65536)로 확대. 윈도우 dedup이 실질 차단하던 래핑 거짓-dedup 코너를 산술적으로도 제거. 1B/2B 디코드율 스윕 양쪽 100%로 재검증.

### Notes

- 신규 13 테스트 추가(전체 188, 회귀 0). 벤치 측정: 깨끗 코퍼스 base 96.1%→풀 100%, 열화 코퍼스 97.1%→풀 100%.
- **디코드 핫패스 불변**: 깨끗한 QR은 변형 1에서 단락하므로 추가 전처리 비용이 없음. None 계약·never-raise 불변.
- **후속(리뷰 권장)**: 작은 페이로드의 Micro-QR 한계는 인코더가 원천(짧은 데이터에 Micro QR 생성) — 인코더에서 일반 QR 강제로 원천 제거 가능(실 패킷은 ≥22B라 비영향). 코어 환경은 대체 detector 부재 시 회복폭 축소 → 실 배포에 contrib 권장.
- **v1.0 잔여(수동)**: 실 카메라 셀프체크 수신율 ≥80% + 2-머신 왕복(ESTABLISHED→MATCH→CLOSED) 사인오프는 미수행 — 통과 시 v1.0.0 승격.

## [0.9.0] - 2026-06-22

실물 광학 링크 하드닝(로드맵 6단계 하드닝). `OpticalChannel`의 실 카메라 경로를 동작·검증 가능한 상태로 보강하고, 실 하드웨어 검증 하니스를 추가. v1.0("빛만으로 실 통신") 승격은 수동 하드웨어 사인오프 통과를 전제로 분리.

### Added

- **디스플레이 프레임 페이싱**: `OpticalChannel(hold=…)` — 연속 표시 사이 최소 간격(단조 시계)을 보장해 실 카메라가 각 QR을 잡을 시간을 확보. `hold=0`(기본)은 동작 변화 없음.
- **`Cv2Camera` 신선 프레임**: `CAP_PROP_BUFFERSIZE=1`(best-effort) + 고정 한도 드레인(`drain_to_latest`)으로 드라이버 버퍼 staleness 완화. 선택적 해상도 힌트(`width`/`height`).
- **실물 검증 하니스**: `examples/optical_selfcheck.py`(한 기기 반이중 — 화면→자기 카메라 수신율 PASS/FAIL), `examples/optical_link.py --real --role {sender,receiver}`(2-머신 실 왕복).

### Changed

- **재캡처 dedup 윈도우화**: 단일 last-delivered 슬롯 → 최근 N(8) 프레임 deque+set. 순서 흔들림(이전 프레임 재캡처)에도 거짓 인도/누락 없이 1회 인도.
- **파라미터 가드**: `poll_interval<=0`은 양수 하한으로 클램프(캡처 루프 busy-spin 차단), `hold<0`은 0으로.

### Notes

- 신규 3 테스트 추가(전체 175, 회귀 0). 상위(세션·신뢰성·앱·코덱) 계층 무수정 — `Channel` 인터페이스만으로 채널 보강.
- nonce는 1바이트 유지(다중 바이트는 한 프레임을 cv2 QR detector의 콘텐츠 의존 블라인드스폿에 빠뜨려 디코드 회귀). 윈도우 dedup + ARQ 특성(재전송 상한·고유 seq)이 결합해 래핑 거짓 dedup을 실질 제거.
- **v1.0 잔여(수동)**: 실 카메라 셀프체크 수신율 ≥80% + 2-머신 왕복(ESTABLISHED→MATCH→CLOSED) 사인오프, cv2 디코드 견고화(수신율↑), 조명/정렬/`hold`·`scale` 튜닝. 통과 시 v1.0.0 승격.

## [0.8.0] - 2026-06-22

실물 광학 채널(로드맵 6단계). QR 프레임을 화면에 표시하고 카메라로 캡처하는 `OpticalChannel`을 추가 — 상위 계층은 무수정으로 빛 위에서 동작(v1.0 후보의 기술 전제).

### Added

- **`OpticalChannel`** (`photontcp/optical/channel.py`): 디스플레이+카메라를 `Channel`로 감싸는 전이중 광학 채널. 백그라운드 캡처 스레드가 프레임을 디코드하고, **1바이트 롤링 nonce 채널 프레이밍**으로 재캡처를 dedup(연속 동일 ARQ 패킷은 nonce가 달라 각각 인도). `OpticalChannel.pair()`로 하드웨어 없는 인메모리 전이중 페어 생성.
- **디바이스 추상** (`photontcp/optical/devices.py`): `DisplaySink`/`CameraSource` 인터페이스 + 인메모리 페이크 `MemoryDisplay`/`MemoryCamera`/`memory_device_pair()`(유휴 시 같은 프레임 재반환으로 카메라 재캡처 모사) — 채널 로직을 하드웨어 없이 결정적으로 검증.
- **cv2 실물 어댑터** (`photontcp/optical/cv2_devices.py`): `Cv2Display`(`cv2.imshow`)/`Cv2Camera`(`cv2.VideoCapture`). import는 하드웨어·창 비접촉, cv2 부재 시 패키지 re-export는 가드.
- 광학 데모 예제(`examples/optical_link.py`, 기본 인메모리 / `--real` 실 디스플레이), 광학 테스트 6건(왕복·재캡처 dedup·연속 동일 패킷·종료·세션 통합·동시성 정확성).

### Notes

- **M7 권장 3 해소**: 양방향 동시 송수신에서 "보낸 집합 == 받은 집합"을 단언해 캡처 스레드의 동시 `decode_frame`(스레드로컬 detector) 정확성을 스모크 초과로 검증. 전체 172 테스트 통과(회귀 0).
- 상위(세션·신뢰성·앱) 계층 무수정 — `Channel` 인터페이스만으로 채널 교체. `ImageLoopbackChannel`은 시뮬레이션 레퍼런스로 보존.
- **잔여(v1.0)**: 실 카메라 캡처 왕복은 미검증(`--real`은 디스플레이 절반만 실물). 실 카메라 왕복·VideoCapture 버퍼 staleness·정렬/조명 튜닝은 v1.0 승격 전 수동 검증 대상.

## [0.7.0] - 2026-06-19

신뢰성 정리/하드닝. M1~M6 누적 리뷰 권장을 한데 모아 견고성·캡슐화·입력 방어·스레드 안전성을 강화(동작 보존, 회귀 0).

### Added

- `RtoEstimator.clone()` + 공개 설정 접근자(`initial_rto`/`min_rto`/`max_rto`).
- `Session(control_rto=, max_control_retries=)` 노출 · `Session.acked_bytes(stream_id)` · `Session.data_failed_streams()`.
- `ArqEvent.SEND_FAILED`(데이터 재전송 상한 초과 시) · `ArqEndpoint.acked_bytes`/`is_failed`.
- `QRCapacityError`(단일 QR 용량 초과를 명확한 예외로).

### Changed

- **ARQ 견고성**: NACK를 구멍당 1회만 보내 NACK 스톰 방지 · 데이터 재전송 상한(`max_retx`)으로 무한 재전송 차단.
- **세션 견고성**: 데이터 평면 트래픽이 세션 idle 타이머를 갱신(`note_data_activity`) · 종료 진행 중(FIN_WAIT/CLOSE_WAIT) 타임아웃은 `CLOSED`로 일관 보고.
- **입력 방어**: `FileReceiver`가 OFFER 필드(name/size/sha256)를 검증해 손상 OFFER를 거부.
- **스레드 안전성**: 루프백 채널 RNG를 락으로 보호 · QR `cv2.QRCodeDetector`를 스레드로컬로(단일 스레드 결정성·동작 보존).
- **앱 정리**: `ChatSession.received`가 복사본 반환 · 파일 전송 전용 stream(`FILE_STREAM_ID=2`, 채팅과 병행) · `FileSender.progress`를 ACK된 바이트 기준으로.

### Notes

- 하드닝 테스트 27건 추가(전체 166). 스레드 안전성은 스모크 수준 검증 — 실제 동시 송수신 정확성은 실물 광학(카메라 스레드) 마일스톤에서 본격 검증 예정.

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
