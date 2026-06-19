# M1 리뷰보고서 (review)

## 비판점

### 차단 (0건)

없음. 마일스톤 완료 기준 1~6을 모두 충족하며, 테스트(13건)가 전부 통과한다.

### 권장 (3건)

1. **`pack()`이 범위 초과 정수에 대해 raw `struct.error`를 노출** — `seq`/`ack`/`session_id` 등에 필드 폭을 넘는 값(예: `seq=2**32`)을 넣고 `pack()` 하면 `struct.error: 'I' format requires 0 <= number <= 4294967295` 라는 저수준 메시지가 그대로 전파된다. 즉시 장애는 아니지만(현재 호출자가 없음) 시퀀스 산술이 등장하는 M3(신뢰성)에서 래핑 필요. **마스킹/래핑 정책은 seq 산술의 주인인 M3에 위임**하기로 하고, M1에서는 의도적으로 손대지 않았다(여기서 묵시적 마스킹을 넣으면 상위 계층의 버그를 숨길 수 있음).
2. **`LoopbackChannel`의 공유 RNG·스레드 안전성** — `pair()`가 두 끝점에 단일 `random.Random` 인스턴스를 공유시킨다. 단일 스레드 테스트에서는 결정적이지만, 전이중을 **여러 스레드로 동시 구동**하면 (a) RNG draw 인터리빙으로 시드별 재현성이 깨지고 (b) `random.Random`은 스레드 안전이 보장되지 않는다. `queue.Queue` 자체는 안전. M2/M3에서 두 피어를 스레드로 돌릴 때를 대비해 방향별 RNG 분리 또는 락을 검토.
3. **latency/jitter 미구현(파라미터만 수용)** — M3에서 RTO·재전송을 시간 기반으로 검증하려면 `deliver_at` 타임스탬프 + 우선순위/지연 큐로 보강이 필요하다. M1 범위에서는 정상적 보류.

### 사소 (2건)

4. **docstring 언어 혼재** — `types.py`·`header.py`는 한국어, `crc.py`·`base.py`·`loopback.py`는 영어. 동작에는 무관하나 일관성을 위해 차후 한쪽으로 통일 권장.
5. **reorder 1-슬롯 버퍼의 꼬리 유실** — 마지막에 보류된 프레임은 후속 송신이 와야 방출되고 `close()` 시 폐기된다. 예제/테스트는 sentinel(`__flush__`) 송신으로 우회 중. 더 강한 재정렬 검증이 필요하면 N-슬롯 셔플로 확장.

## 수정 내용

- **이슈(권장/사소 외 안전 정리)**: 패키지 `__init__` 재노출 일관화 — 리뷰 중 직접 수정.
  - `photontcp/packet/__init__.py`: `Packet`, `PacketType`, `Flags`, `crc32`, `verify`, 예외 계층, `HEADER_SIZE`/`HEADER_FORMAT`를 재노출(`__all__` 명시). 모든 모듈이 존재하므로 "import 없는 빈 초기화"를 정식 공개 API로 전환.
  - `photontcp/channel/__init__.py`: `LoopbackChannel`을 `Channel`과 함께 재노출.
  - 이유: 기존에 `channel`은 `Channel`을 재노출하나 `packet`은 아무것도 재노출하지 않아 두 패키지의 공개 방식이 어긋났고, 사용자가 매번 서브모듈 경로(`photontcp.packet.header.Packet`)를 알아야 했다. 테스트는 전 경로 import를 쓰므로 회귀 위험 없음.
- 이슈 1·2·3은 상위 계층(M3) 책임 또는 설계상 보류로 판단해 M1에서 코드 변경하지 않음(위 근거 참조).

## 검증

- `python -m pytest tests/ -q` → **13 passed in 2.57s** (수정 후 재실행, 실패 0).
- 재노출 확인: `from photontcp.packet import Packet, PacketType, Flags, ChecksumError, crc32` 및 `from photontcp.channel import Channel, LoopbackChannel` 모두 정상 import.
- `python examples/echo_loopback.py` → 전체 **MATCH** 유지.
- 잔여 리스크: 이슈 1·2는 M3에서 동시성/시퀀스 산술이 들어올 때 재검증 필요(테스트로 선제 커버 권장). 현재 단일 스레드·단일 방향 사용 범위에서는 문제 없음.

## 릴리즈 판정

**가능** — 추천 버전: **v0.1.0 (최초 릴리즈)**

- 완료 기준 1~6 전부 충족, 차단 이슈 0건.
- 이 저장소는 kickoff 이후 한 번도 릴리즈/등록된 적이 없다. `pyproject.toml`의 `0.1.0`은 초기 스캐폴드 버전이므로, 최초 릴리즈는 버전 범프 없이 **그 초기 버전 그대로 v0.1.0으로 태깅**한다(0.2.0으로 올리면 0.1.0이 건너뛰어짐).
- 권장 3건은 모두 후속 마일스톤(M2/M3)이 자연히 집어가는 항목으로, 릴리즈를 막지 않는다.

## 다음 단계

- 릴리즈: **`/tide:release v0.1.0`** (프리플라이트 → CHANGELOG → commit → tag → push). 초기 버전과 동일하므로 버전 파일 범프는 없다(태그가 최초 릴리즈).
- 릴리즈 후: **`/tide:milestone`** 으로 로드맵 1단계(세션 핸드셰이크/종료/하트비트, M2) 정의. 이때 권장 이슈 1(seq 범위 정책)·2(스레드/RNG)를 M2/M3 태스크 또는 완료 기준에 명시적으로 반영할 것.
