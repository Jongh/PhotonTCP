# M5 완료보고서 (impl)

## 개요

마일스톤 M5(로드맵 4단계 — QR 코덱 실물 통합)의 6개 태스크(M5-T01~T06)를 전부 구현했다.
segno 기반 QR 인코더, OpenCV `QRCodeDetector` 기반 디코더(base64 ASCII-safe 래핑), 그리고 QR 이미지를
메모리로 주고받는 `ImageLoopbackChannel`을 완성했다. 기존 세션·ARQ·스트림·채팅 스택이 **수정 없이**
QR 이미지 위에서 그대로 동작함을 검증했다 — "패킷 → QR → 이미지 → 디코드 → 패킷" 경로가 실물
라이브러리로 통과한다. 실제 화면/카메라(M6) 직전의 마지막 메모리 단계 완성.

확정 설계 결정 4건 반영: **segno / OpenCV / base64 / 코덱+ImageLoopbackChannel**. 처음으로 stdlib 외
의존성(segno·opencv-python·numpy) 도입. QR 의존 테스트는 `pytest.importorskip`으로 미설치 환경에서 skip.
의존성 위상 순서·레벨별 병렬 디스패치: L0=T01·T02, L1=T03·T04, L2=T05·T06 (각 레벨 파일 비겹침).

## 태스크별 수행 내용

- **M5-T01** — QR 인코더(`qr/encode.py`, 패키지 re-export). `encode_frame(data, *, scale=8, border=4, error="m")->np.ndarray`: `base64.b64encode`→segno QR→모듈 매트릭스(`qr.matrix`, dark=1)를 quiet zone 포함 `np.kron`으로 scale배 확대한 2D uint8(0/255) 배열. 순수·결정적.
- **M5-T02** — QR 디코더(`qr/decode.py`). `decode_frame(image)->bytes|None`: 컬러→그레이 변환, `cv2.QRCodeDetector().detectAndDecode`→문자열→`base64.b64decode`→bytes. 검출 실패·base64 실패·cv2 예외는 `None`(손상 프레임). detector 재사용 옵션.
- **M5-T03** — `ImageLoopbackChannel`(`channel/image_loopback.py`, 채널 re-export). `LoopbackChannel`과 동일 전이중 큐이되 큐에 QR 이미지(numpy) 저장: send=encode→push, recv=pop→decode. 프레임(이미지) 단위 loss/dup(주입 seed), 선택적 `degrade`(이미지 노이즈/블러로 EC 견딤 테스트). `Channel` 인터페이스 만족 → Session/ChatSession 무수정 사용.
- **M5-T04** — QR 코덱 라운드트립 테스트(`tests/test_qr.py`, 8건): 전 바이트값 base64 무결성, 실제 `Packet.pack()`→QR→unpack CRC 통과·필드 보존, 빈 payload 패킷, 결정성, 검출 실패 None. `importorskip` 적용.
- **M5-T05** — 이미지 채널 풀스택 통합 테스트(`tests/test_image_channel.py`, 6건, 0.68s): 프레임 왕복, 세션 핸드셰이크·종료, 신뢰 데이터, ChatSession 양방향 — 모두 QR 이미지 위에서. 무손실·작은 페이로드로 빠르게.
- **M5-T06** — QR 루프백 예제(`examples/qr_loopback.py`). 무손실 QR 이미지 링크 위 ChatSession 양방향 메시지 MATCH, QR 이미지 shape(222×222, ~29모듈) 출력으로 실물 QR 통과 증명, 양쪽 CLOSED. ~0.3s. segno/cv2 미설치 시 graceful exit.

## 환경/의존성

- impl 시작 시 `pip install --user segno opencv-python numpy` 설치(시스템 site-packages 권한으로 `--user` 사용). 설치 버전: segno 1.6.6 / opencv 4.13.0 / numpy 2.4.6. (무관한 기존 패키지 `opendis`의 numpy<2 경고 있으나 영향 없음.)

## 변경 파일 요약

| 구분 | 파일 |
|---|---|
| 추가 | `photontcp/qr/__init__.py`, `photontcp/qr/encode.py`, `photontcp/qr/decode.py`, `photontcp/channel/image_loopback.py`, `tests/test_qr.py`, `tests/test_image_channel.py`, `examples/qr_loopback.py` |
| 수정 | `photontcp/channel/__init__.py`(`ImageLoopbackChannel` re-export 추가) |
| 삭제 | (없음) |

## 테스트 결과

- `python -m pytest -q` → **109 passed in 3.80s** (M1~M4 86 + M5 23, 회귀 없음). QR 의존 테스트는 라이브러리 설치돼 통과(미설치 시 skip).
- 예제 5종 전부 정상: `echo`·`session`·`reliable`·`chat`·`qr` loopback.
- 완료 기준 1~7 충족: 코덱 라운드트립·바이너리 무결성, Packet QR 왕복 CRC 통과, ImageLoopbackChannel Channel 만족, 풀스택 무수정 동작, 코덱 순수·채널 결정적·테스트 skip 가능, 예제, 전체 통과.
- 이번 사이클도 서브에이전트가 소스 결함을 발견하지 않았다.

## 미해결·후속 메모

1. **단일 QR 프레임 용량 한계** — segno 단일 QR 심볼은 용량 상한이 있어(byte mode ~2953B, base64 inflation 고려 시 원본 ~2000B 초과 시 `DataOverflowError`). PhotonTCP 프레임은 헤더 22B + `max_payload`(기본 200B) ≈ 222B → base64 ~300자로 안전. **`max_payload`를 키울 때 QR 용량을 넘지 않도록 상한 검증/문서화 필요**(M6/튜닝에서).
2. **cv2 QRCodeDetector의 소형 QR 검출 한계** — 원본 ~7B 미만(아주 작은 QR)은 검출 실패→None. 모든 실제 프레임(≥22B 헤더)은 안전하나, 코덱을 다른 용도로 쓸 때 주의. 빈 입력(`b""`)도 None.
3. **성능** — cv2 디코드가 프레임당 비용이 있어 풀스택 QR 테스트는 페이로드/메시지를 작게 유지(현재 빠름). 실물 광학(M6)·대용량 전송 시 FPS·프레임 예산 튜닝 필요(7단계).
4. **EC/degrade 견딤은 기본 비활성** — `ImageLoopbackChannel(degrade=...)`로 노이즈/블러 주입 테스트 가능하나 기본 클린. 실제 광학 노이즈(모션 블러·조명)는 M6에서 본격 검증.
5. 여전히 비범위/이월: 실제 화면 디스플레이·카메라 캡처(M6), 패킷 간 FEC(7단계), M3/M4 리뷰 권장(NACK 억제·flush-on-close 등), 파일 전송(로드맵 5단계).
