# M10 완료보고서 (impl)

## 개요

M10 "cv2 QR 디코드 견고화"의 6개 태스크를 모두 구현했다. `decode_frame`에 결정적
전처리 캐스케이드(T01)와 대체 detector 폴백(T02)을 넣어 cv2 `QRCodeDetector`의
콘텐츠 의존 블라인드스폿을 회복하고, 광학 채널 EC 레벨을 설정/문서화(T03)했으며,
디코드율을 측정·회귀가드하는 코퍼스/벤치(T04)와 단위 테스트·문서(T06)를 추가했다.
디코드가 견고해진 토대 위에서 M9가 보류했던 **다중 바이트 nonce를 재검증하여 2바이트로
확대**(T05)했다. 의존 위상으로 병렬 디스패치했다(L0: T01∥T03, L1: T02, L2: T04∥T06,
L3: T05). 전체 **188 테스트 통과**(M9 175 + M10 신규 13).

## 태스크별 수행 내용

- **M10-T01 — 전처리 캐스케이드** — `decode_frame`의 단일 `detectAndDecode` 호출을
  결정적 변형 캐스케이드로 교체했다. 재사용 가능한 지연 생성기 `_decode_variants(image)`가
  변형을 저비용→고비용 순서로 yield한다: (1) 원본 그레이스케일(핫패스, 추가비용 0),
  (2) Otsu 이진화, (3) 샤프닝(unsharp `filter2D`)+Otsu, (4) 이진화/샤프닝 이미지의 2.0×·3.0×
  업스케일(M9에서 단독 업스케일이 무효였으므로 전처리와 **조합**). 첫 성공에서 단락하며,
  `cv2.error`는 **변형 단위**로 잡아 다음 변형으로 넘어간다(한 변형 실패가 전체를 죽이지
  않음). None 계약·never-raises 불변. 생성기 laziness로 깨끗한 QR은 변형 1에서 즉시 성공해
  추가 전처리 비용이 0이다.

- **M10-T02 — 대체 detector 폴백** — 1차 `cv2.QRCodeDetector`가 모든 변형에서 실패하면
  `_alt_decode(image)`가 동일 변형들을 대체 detector로 재시도한다. 가용성은 1회 probe 후
  캐시(`_resolve_alt_kind`/`_alt_kind_cached`): `cv2.wechat_qrcode_WeChatQRCode`(생성 가능 시)
  → `cv2.QRCodeDetectorAruco`(OpenCV ≥4.7, 모델파일 불필요) → 없으면 폴백 생략. 인스턴스는
  thread-local(1차 detector와 동일 패턴). 대체 detector 미가용 환경에서는 동작이 캐스케이드
  단독과 동일(추가 회복만, 기존 환경 불변). **현재 환경**(cv2 4.13, WeChat 부재)에서는 Aruco로
  해결되어 폴백이 활성.

- **M10-T03 — 광학 경로 EC 견고화** — EC 배선은 이미 완성돼 있었다(`OpticalChannel.__init__`/
  `pair`의 `error="m"` → `send_frame`이 `encode_frame(error=self.error)`). 따라서 코드 변경
  없이 (a) `error` 파라미터 docstring을 견고성 트레이드오프(높은 EC = 캡처 견고성↑·용량↓,
  `QRCapacityError` 임계↓; 기본 `"m"` 유지, 실 링크는 `"q"`)로 보강하고, (b) 회귀가드 테스트
  `tests/test_optical_ec.py`(신규)를 추가했다 — `pair(error="q")` 왕복, 기본값 호환, `"q"` vs
  `"m"` 인코딩 차이로 인자가 살아있음 증명.

- **M10-T04 — 디코드 견고성 코퍼스 + 벤치 + 임계 테스트** — `examples/qr_decode_bench.py`(신규,
  하드웨어 불필요, 시드고정 207-페이로드 코퍼스): base-only 단일패스 vs 풀 `decode_frame`를
  깨끗/열화(가우시안 블러+다운·업스케일) 코퍼스에서 비교 출력. `tests/test_qr_robustness.py`(신규,
  4테스트): 깨끗 코퍼스 100% 왕복, 열화 코퍼스 ≥0.85(실측 1.00 대비 보수적), **블라인드스폿
  회복 단언**(base 실패 & 풀 성공 케이스 탐색→단언; 미발견 시 skip으로 비-플레이키),
  Micro-QR 경계(≤9B는 cv2가 어느 배율로도 디코드 불가 — M10과 직교) 문서화.
  실측: 깨끗 base 96.14%→풀 **100%**(+8), 열화 base 97.10%→풀 **100%**(+6).

- **M10-T05 — 다중 바이트 nonce 재검증** — 디코드 견고화 후 2바이트 nonce를 재검증했다.
  (1) 측정: 대표 페이로드 세트에 1B/2B nonce를 붙여 `encode→decode_frame` 왕복 — **양쪽
  모두 100%**(245/245). (2) 전체 suite를 `_NONCE_BYTES=2`로 실행 → **그린**(M9의 회귀 소멸).
  판단: 회귀가 사라졌으므로 `_NONCE_BYTES`를 **1→2로 확대**(mod-65536). 윈도우 dedup이
  이미 거짓-dedup 코너를 *실질적으로* 막았지만, 폭 확대는 그 코너를 *산술적으로도* 제거한다
  (마일스톤 완료기준 4의 "다중 바이트 nonce" 문구를 이제 충족). 채널/모듈 docstring과 nonce
  상수 주석을 이 결정(+근거 수치)으로 갱신하고, 2바이트 프레이밍·증가를 핀하는 회귀 테스트를
  `tests/test_optical_pacing.py`에 추가했다.
  - **부수 발견·수정**: 재검증 중 `test_hold_paces_sends_and_zero_holds_nothing`이 ~50% 확률로
    실패하는 **기존(nonce 무관) 플레이키**임을 확인했다(1B에서도 동일 빈도; 차등
    `paced-unpaced` 검사가 run별 인코딩 비용 변동에 휘둘림). nonce 변경이 원인이 아님을 1B로
    되돌려 격리 측정으로 확정한 뒤, 검사를 **단측 경계 2개**(① unpaced < floor·0.9 — hold=0은
    페이싱 안 함, ② paced ≥ floor·0.9 — `time.sleep`은 하한이라 페이싱 floor를 보장)로 재작성했다.
    격리 10/10, 전체 suite 연속 2회 그린.

- **M10-T06 — 단위 테스트 + 문서** — `tests/test_qr.py`에 5개 결정적 단위테스트 추가: 변형
  캐스케이드 첫 변형=입력 identity·≥4 변형, 핫패스 단락(가짜 detector 1회 호출 후 성공),
  캐스케이드 회복(앞 N변형 실패 후 성공), 대체 detector 부재 시 None·never-raise(monkeypatch),
  대체 detector 폴백 회복(aruco 형태 가짜). `README.md`에 "QR 디코드 견고화(M10)" 절(캐스케이드·
  폴백·API 불변·벤치 실행법·v1.0 셀프체크 수신율과의 연결). docstring은 T01/T02/T03가 이미
  정확히 작성해 추가 수정 불필요로 판단.

## 변경 파일 요약

| 구분 | 파일 |
|---|---|
| 추가 | `examples/qr_decode_bench.py`, `tests/test_qr_robustness.py`, `tests/test_optical_ec.py`, `docs/milestones/M10.md`(마일스톤 단계 산출물), `docs/reports/M10-impl.md`(본 보고서) |
| 수정 | `photontcp/qr/decode.py`(T01 캐스케이드 + T02 폴백), `photontcp/optical/channel.py`(T03 EC docstring + T05 nonce 2바이트·docstring), `tests/test_qr.py`(T06 단위 5건), `tests/test_optical_pacing.py`(T05 페이싱 견고화 + 2바이트 nonce 테스트), `README.md`(T06 M10 절) |
| 삭제 | (없음) |

## 테스트 결과

- `python -m pytest -q` → **188 passed**(연속 2회 동일, ~18.6s). 기존 175 + 신규 13
  (test_qr 5 + test_qr_robustness 4 + test_optical_ec 3 + test_optical_pacing nonce 1).
  회귀 0.
- `python examples/qr_decode_bench.py` → exit 0. 깨끗 base 96.14%→풀 100%(+8), 열화
  base 97.10%→풀 100%(+6), "full ≥ base-only on every set: YES".
- 1B-vs-2B nonce decode-rate 스윕: 양쪽 245/245 = 100%.
- 페이싱 테스트 격리 10/10(재작성 전 ~50% 플레이키 → 단측 경계로 해소).
- 대체 detector 환경 확인: cv2 4.13.0, WeChat attr 부재 → Aruco로 폴백 활성.

## 미해결·후속 메모

1. **Micro-QR 한계(직교, cv2 자체 제약)** — cv2 4.13의 `QRCodeDetector`는 Micro-QR(version M*)을
   어느 배율로도 디코드 못 한다(≤9 raw bytes 페이로드에서 발생). M10 캐스케이드/폴백은 같은
   심볼을 읽는 것이라 회복 불가 — 본 프로토콜의 패킷은 헤더만 22B라 실사용에서 Micro-QR로
   가지 않으나, 코퍼스/테스트는 이를 명시적으로 제외/문서화했다. 후속에서 EC·심볼 강제 옵션
   검토 여지.
2. **대체 detector의 환경 의존성** — 본 환경은 Aruco가 가용하나, 코어 opencv-python(WeChat·
   Aruco 모두 부재)에서는 폴백이 생략되어 캐스케이드 단독 동작이 된다(회복폭↓). 실 링크
   배포 환경에 opencv-contrib(WeChat) 설치를 권장 — 리뷰/문서에서 실 수신율 측정 시 어떤
   detector가 활성인지 함께 기록할 것.
3. **열화 임계의 헤드룸** — 코퍼스 열화는 의도적으로 가벼워(풀 100%) 임계 0.85에 여유가 크다.
   실 카메라의 더 강한 열화(저조도·모션블러)는 셀프체크 하니스로만 실측 가능 — v1.0 사인오프
   시 실측 수신율을 회고/릴리즈 노트에 기록.
4. **v1.0 게이트는 여전히 수동** — M10은 디코드율(=셀프체크 수신율의 상한)을 끌어올렸을 뿐,
   v1.0 승격은 M9가 규정한 실 하드웨어 사인오프(셀프체크 ≥80% + 2-머신 왕복)가 전제다.
   리뷰에서 목표버전(v0.10.0 minor) 적정성과 함께 확인.
