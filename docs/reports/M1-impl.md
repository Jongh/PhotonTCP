# M1 완료보고서 (impl)

## 개요

마일스톤 M1(로드맵 0단계 — 스켈레톤·LoopbackChannel·패킷 코덱·CRC)의 7개 태스크(M1-T01~T07)를 전부 구현했다.
PhotonTCP 패키지 골격, 교체 가능한 `Channel` 추상 인터페이스와 노이즈 시뮬레이션이 가능한 `LoopbackChannel`,
22바이트 고정 헤더 + CRC32 무결성을 갖춘 패킷 직렬화/역직렬화를 완성했다. 임의 바이트열을 패킷으로 직렬화해
가상 채널로 왕복시키고 CRC 검증 후 원본을 복원하는 에코 동작과 단위 테스트가 모두 통과한다.

의존성 위상 순서로 진행했고 각 레벨의 독립 태스크는 병렬 디스패치(서브에이전트 동시 실행)했다:
레벨0 = T01·T02·T03, 레벨1 = T04·T05, 레벨2 = T06·T07. 각 레벨의 예상 변경 파일 집합이 비겹침이라 모두 병렬 안전했다.

## 태스크별 수행 내용

- **M1-T01** — 패키지 스켈레톤 + `Channel` 추상 인터페이스. `photontcp`(루트, `__version__="0.1.0"`), `channel`(`Channel` 재노출), `packet`(빈 초기화) 패키지 생성. `channel/base.py`에 `abc.ABC` 기반 `Channel` — `send_frame`/`recv_frame(timeout=None)`/`close` 추상 메서드 + docstring. 직접 인스턴스화 시 `TypeError` 확인.
- **M1-T02** — 패킷 타입·플래그. `packet/types.py`: `PROTOCOL_VERSION=1`, `PacketType(IntEnum)`(SYN=0…HEARTBEAT=7, 값 명시 고정으로 와이어 호환성 확보), `Flags(IntFlag)`(NONE/SYN/ACK/FIN/NACK = 0/1/2/4/8). 전 멤버 0~255(1바이트) 적합.
- **M1-T03** — CRC32 유틸. `packet/crc.py`: `zlib.crc32`를 감싼 `crc32(data)->int`(`& 0xFFFFFFFF`)와 `verify(data, expected)->bool`.
- **M1-T04** — 패킷 헤더 직렬화/역직렬화. `packet/header.py`: `HEADER_FORMAT=">BBBHBIIHHI"`, `HEADER_SIZE=22`, `@dataclass Packet`(헤더 필드 전부 + `payload`). `pack()`은 `payload_len` 자동 설정 후 crc=0 상태 바이트열의 CRC32를 계산해 채움. `unpack()`은 길이·`payload_len`·CRC 검증. 예외 계층 `PacketError`→`ChecksumError`/`MalformedPacketError`.
- **M1-T05** — `LoopbackChannel` + 노이즈. `channel/loopback.py`: `queue.Queue` 2개로 전이중 교차 연결하는 `pair(seed=..., **noise)`. 송신 시점에 loss/corrupt(1바이트 XOR)/dup/reorder(1-슬롯 지연 버퍼) 적용, 주입된 `random.Random(seed)`로만 난수 사용해 결정적 재현. `recv_frame`은 `queue.Empty` 시 `None`, `close`는 멱등.
- **M1-T06** — 단위 테스트. `tests/test_packet.py`(라운드트립·빈 payload·1바이트 변조→`ChecksumError`·미달 입력→`MalformedPacketError` 등), `tests/test_loopback.py`(양방향 도착·순서 보존·`loss=1.0`→None·동일/상이 seed 결정성·닫힌 채널). recv는 모두 timeout 명시.
- **M1-T07** — 에코 예제. `examples/echo_loopback.py`: 무손실 `pair()`에서 영문/한글(UTF-8)/빈 페이로드 메시지를 `DATA` 패킷으로 송신→수신→복원, MATCH/MISMATCH 출력. 레포 루트에서 직접 실행 가능(`sys.path` 보정).

## 변경 파일 요약

| 구분 | 파일 |
|---|---|
| 추가 | `photontcp/__init__.py`, `photontcp/channel/__init__.py`, `photontcp/channel/base.py`, `photontcp/channel/loopback.py`, `photontcp/packet/__init__.py`, `photontcp/packet/types.py`, `photontcp/packet/crc.py`, `photontcp/packet/header.py`, `tests/__init__.py`, `tests/test_packet.py`, `tests/test_loopback.py`, `examples/echo_loopback.py` |
| 수정 | (없음) |
| 삭제 | (없음) |

## 테스트 결과

- `python -m pytest tests/ -q` → **13 passed in 2.56s** (실패 0). 환경: Python 3.12.1 / pytest 9.0.3.
- `python examples/echo_loopback.py` → 4개 메시지(영문/한글/문장/빈값) 전부 **MATCH**, 종료 코드 0.
- 완료 기준 1~6 전부 충족: pack/unpack 필드·payload 보존, 변조 시 `ChecksumError`, 무손실 채널 도달, 동일 seed 결정적 재현, 에코 입출력 일치, pytest 전체 통과.

## 미해결·후속 메모

1. **latency/jitter 미구현(파라미터만 수용)** — `LoopbackChannel`이 인자는 받지만 전달 타이밍에 지연/지터를 적용하지 않는다. M3(신뢰성 ARQ)에서 RTO·재전송을 시간 기반으로 검증하려면 `deliver_at` 타임스탬프 + 우선순위 큐 방식으로 보강이 필요할 수 있다.
2. **reorder 1-슬롯 한계** — 마지막에 보류된 프레임은 다음 송신/비-reorder 프레임이 와야 방출되고, `close()` 시 보류 프레임은 폐기된다. 더 강한 재정렬 검증이 필요하면 N-슬롯 셔플로 확장 검토.
3. **헤더 정수 범위 검증 없음** — `pack()`에 음수/범위 초과 정수를 넣으면 stdlib `struct.error`가 그대로 전파된다(별도 래핑 안 함). 필요 시 `MalformedPacketError`로 감싸는 보강을 후속에서 결정.
4. **`channel/__init__.py`에 `LoopbackChannel` 미노출** — 현재 `Channel`만 재노출. 사용 편의를 위해 export 추가 여부는 리뷰에서 결정(태스크 분담상 의도적으로 보류).
5. Windows 기본 콘솔(cp949)에서 예제의 한글 출력이 모지바케로 보일 수 있음 — 실제 round-trip은 정상이며 `PYTHONIOENCODING=utf-8`에서 정상 표기.
