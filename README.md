# PhotonTCP

화면(송신) + 카메라(수신) + 애니메이션 QR 코드만으로 동작하는, **TCP 유사 신뢰성 양방향 전송 프로토콜**.
Wi-Fi·블루투스·이더넷·USB 등 전통적 통신 매체 없이, 오직 화면·카메라·가시광선만으로 신뢰성 있는 통신이 가능한지를 탐구하는 실험 프로젝트입니다.

## 핵심 설계 원칙

- **전송 계층은 범용**(신뢰성 바이트 스트림 / 스트림 다중화)으로 만들고,
  **채팅·파일 전송은 그 위에 얹는 애플리케이션**으로 분리한다.
- 따라서 구현 순서는 **기본 통신(텍스트 채팅) 먼저 → 파일 전송 나중**.

## 확정 사항

| 항목 | 결정 |
|------|------|
| 언어/스택 | Python |
| 검증 순서 | 루프백(가상 광학 채널) 먼저 → 실제 카메라+화면 나중 |
| 구현 순서 | 기본 통신(텍스트 채팅) → 파일 전송 |

## 계층 구조

```
응용        Chat App  →  File App  (확장)
스트림 MUX  stream_id 다중화 (0 = 제어 스트림)
세션        SYN/SYN_ACK/ACK 핸드셰이크 · FIN 종료 · 하트비트
신뢰성 ARQ  슬라이딩 윈도우 · ACK/NACK · 재전송 · 재정렬 · 중복제거
패킷        헤더 직렬화 + CRC32
QR 코덱     bytes ↔ QR 프레임 (내장 Reed-Solomon EC)
채널(광학)  Loopback(가상) ⇆ Optical(화면+카메라) — 교체 가능 인터페이스
```

## 로드맵

| 단계 | 내용 |
|------|------|
| 0 | 스켈레톤 + LoopbackChannel + 패킷 코덱 + CRC |
| 1 | 세션 핸드셰이크 / 종료 / 하트비트 |
| 2 | 신뢰성 ARQ (손실/중복/순서 시뮬레이션 통과) |
| 3 | 스트림 MUX + Chat 앱 = **양방향 텍스트 채팅** (기본 통신 완성) |
| 4 | QR 코덱 실물 통합 |
| 5 | File 앱 = **파일 전송 + SHA-256 무결성** (파일 전송 완성) |
| 6 | OpticalChannel 화면 + 카메라 실물 (**완료** — 아래 참조) |
| 7 | 최적화 (적응형 RTO · 흐름제어 · 선택적 FEC) |

### 실물 광학 채널 (`OpticalChannel`)

M8에서 `ImageLoopbackChannel`(가상 광학)을 실물로 대체하는 `OpticalChannel`이 추가되었습니다.
QR 프레임을 **실제 화면(`Cv2Display`)에 띄우고 실제 카메라(`Cv2Camera`)로 캡처**해 디코드하며,
세션·신뢰성·앱 계층은 한 줄도 바꾸지 않은 채(=`Channel` 인터페이스만 의존) 그대로 동작합니다.
하드웨어는 `DisplaySink`/`CameraSource` 추상 뒤로 격리되어, 메모리 페이크(`OpticalChannel.pair()`)로
하드웨어 없이 결정적으로 테스트·시연할 수 있습니다.

M9에서 실물 경로가 하드닝되었습니다: `Cv2Camera`가 드라이버 버퍼를 최소화하고 **최신 프레임을
드레인**해 지연 누적을 막고, `OpticalChannel(hold=…)`이 **디스플레이 프레임 페이싱**(카메라가 각 QR을
최소 1회 잡을 시간 보장)을 제공하며, 재캡처 dedup이 **최근 N프레임 윈도우**로 순서 흔들림에도 견고해졌고,
`poll_interval` 등 파라미터에 하한 가드가 추가되었습니다.

M10에서 **QR 디코드가 견고화**되었습니다. 자세한 내용은 아래 [QR 디코드 견고화](#qr-디코드-견고화-m10)를 참조하세요.

데모 예제 [`examples/optical_link.py`](examples/optical_link.py):

```
python examples/optical_link.py                       # 인메모리 모드 (화면/카메라 불필요, 항상 실행 가능)
python examples/optical_link.py --real                 # 송신 QR 프레임을 실제 화면 창에 렌더 (디스플레이만, 수동)
python examples/optical_link.py --real --role sender    # 실물 양방향: 한 기기에서 송신 역할
python examples/optical_link.py --real --role receiver  # 실물 양방향: 다른 기기에서 수신 역할
```

기본(인메모리) 모드는 화면·카메라 없이 항상 동작하며 핸드셰이크→채팅→종료 왕복을 실증합니다.

#### 실물 검증 절차 (수동, 하드웨어 필요)

CI에서 자동 검증 불가한 실 카메라 경로는 아래 하니스로 수동 검증합니다:

1. **셀프체크 (반이중, 한 기기)** — [`examples/optical_selfcheck.py`](examples/optical_selfcheck.py):
   화면에 일련의 QR을 띄우고 그 화면을 비춘 웹캠으로 캡처·디코드해 **수신율/정확도 PASS/FAIL**을
   출력합니다. `python examples/optical_selfcheck.py --camera 0 --hold 0.3 --count 20` (PASS 기준:
   수신율 ≥ 80%).
2. **2-머신 왕복 (전이중)** — `optical_link.py --real --role …`를 두 기기에서 마주보게 실행해(화면 2 +
   카메라 2) 핸드셰이크→메시지 교환→graceful close가 성립하는지 확인합니다.

**v1.0 사인오프 게이트**: (a) 셀프체크 수신율 ≥ 80%, **그리고** (b) 2-머신 왕복으로 양 피어가
ESTABLISHED→메시지 MATCH→both CLOSED. 두 조건이 실물에서 통과하면 "빛만으로 실 통신"이 증명되어
v1.0으로 승격합니다. 한 기기는 자기 창을 자기 카메라로 보기 어려우므로 양방향 루프엔 화면 2 + 카메라
2(또는 외부 카메라)가 필요합니다. 조명·정렬·`--hold`/`--scale` 튜닝이 수신율에 영향을 줍니다.

### QR 디코드 견고화 (M10)

실 광학 링크에서 카메라가 잡은 QR 프레임은 흐림·조명 편차, 그리고 cv2 `QRCodeDetector`의
**콘텐츠 의존 블라인드스폿**(특정 유효 QR을 어느 배율로도 디코드 못 하는 사례)으로 디코드가
실패할 수 있습니다. M10은 `decode_frame`을 두 단계로 견고화했습니다(공개 API·시그니처 불변):

- **전처리 변형 캐스케이드**: 저비용→고비용 순서로 변형을 결정적으로 시도하고 첫 성공에서
  단락합니다 — (1) 원본 그레이스케일, (2) Otsu 이진화, (3) 샤프닝 후 이진화(흐린 캡처 대응),
  (4) 이진화·샤프닝 결과의 2.0×/3.0× 업스케일. **깨끗한 프레임은 변형 1에서 즉시 성공해 추가
  비용이 0**이며(핫패스 불변), 변형 2~4는 첫 시도 실패 시에만 지연 평가됩니다.
- **대체 detector 폴백**: 1차 detector가 모든 변형에서 실패하면, OpenCV 빌드가 제공하는 대체
  detector(`cv2.wechat_qrcode_WeChatQRCode` 우선, 없으면 `cv2.QRCodeDetectorAruco`)로 같은
  변형들을 한 번 더 시도해 블라인드스폿을 회복합니다. 둘 다 없는 코어 opencv-python 환경에서는
  폴백을 조용히 건너뛰며, 동작은 캐스케이드 단독과 동일합니다.

`decode_frame`의 **None 계약은 불변**입니다: 검출 실패·잘못된 base64·OpenCV 예외는 모두 변형
단위로 잡혀 다음 변형으로 넘어가고, 모든 경로가 실패할 때만 `None`을 반환하며 절대 raise 하지
않습니다.

벤치마크로 캐스케이드/폴백 on·off의 코퍼스 디코드율을 비교할 수 있습니다(하드웨어 불필요,
순수 인메모리):

```
python examples/qr_decode_bench.py
```

이렇게 높아진 실 디코드율은 v1.0 사인오프 게이트의 **셀프체크 수신율 ≥ 80%** 통과 여유를
키워, 동일 조명·정렬 조건에서 게이트를 더 쉽게 넘게 합니다.

### 모바일(Android) (M11)

M11에서 **안드로이드 폰이 한 피어로 동작**할 수 있게 되었습니다. 폰이 자기 화면으로 QR을 띄우고
자기 카메라로 상대 QR을 잡아, PC와 마주 놓으면 "화면 2 + 카메라 2"가 성립합니다 — v1.0 사인오프
체크 B(2-머신 왕복)에 두 번째 PC가 없어도 되는 셈입니다.

- `photontcp/mobile/`: `KivyDisplay`(`DisplaySink`) + `KivyCamera`(`CameraSource`) 어댑터와
  Kivy 앱 골격(`PhotonTCPApp`). 세션·신뢰성·앱 계층은 **한 줄도 바뀌지 않았습니다**(`Channel`
  인터페이스만 의존). Kivy 미설치 데스크톱에서도 `import photontcp.mobile`은 성공하며, 두 어댑터와
  앱 진입점은 `None`으로 노출됩니다(`photontcp/optical/__init__.py`의 cv2 가드와 동형).
- `photontcp/optical/peer.py`의 `run_peer(channel, role, …)`: 핸드셰이크→메시지 교환→정상 종료
  구동 루프가 UI/argparse와 분리되어, CLI 예제(`examples/optical_link.py`)와 모바일 앱이 **같은
  코드**를 씁니다. 진행 상황은 `on_event(kind, detail)` 콜백으로만 흘러 라이브러리가 print하지
  않습니다.
- **디코더 백엔드 seam**: `qr/decode.py`가 cv2를 **지연 import**하고, 디코더를 이름으로 등록·선택할
  수 있습니다(`register_decoder_backend` / `set_decoder_backend` / `active_decoder_backend`).
  cv2가 없는 플랫폼에서도 패키지가 import되고 `decode_frame`은 예외 대신 `None`을 돌려줍니다
  (None 계약·never-raise 불변). 이는 p4a의 무거운 opencv 레시피가 실패할 때를 위한 위험 분산입니다.
- 개발 설치: `pip install photontcp[mobile]` (Kivy·camera4kivy).

**빌드 방법**: [`docs/mobile-build.md`](docs/mobile-build.md) — Docker 경로와 WSL2 경로를 각각
명령 단위로, `adb install` 절차와 실패 시 로그 위치까지 적어 두었습니다. 설정의 단일 원본은
[`buildozer.spec`](buildozer.spec)입니다.

#### 현재 한계 (정직하게)

- **실기 사인오프 실측은 아직 수행되지 않았습니다.** 폰+PC 체크 B 절차는
  [`docs/v1.0-signoff.md`](docs/v1.0-signoff.md)에 적혀 있지만, 실제로 돌려 수신율·요약 줄을
  기록하는 일은 **다음 사이클 항목**입니다. 따라서 v1.0은 아직 주장하지 않습니다.
- **camera4kivy는 아카이브(유지보수 중단) 상태**입니다. CameraX 프리뷰의 픽셀 콜백 시그니처와
  RGBA 행 순서, 그리고 **분석 버퍼의 재사용 여부**는 문서 기준으로 구현했을 뿐 **기기에서
  검증되지 않았습니다**. 버퍼 재사용은 조용한 디코드 실패로 나타나므로 `KivyCamera`는 기본으로
  제출 시점에 버퍼를 복사합니다(`copy_on_submit=True` — 콜백 스레드의 memcpy 한 장).
- 그래서 `KivyCamera(flip_vertical=…, flip_horizontal=…)`와 `KivyDisplay(colorfmt=…,
  flip_vertical=…)`의 기본값은 **실기 확인이 필요**합니다. QR은 상하/좌우가 뒤집히면 디코드되지
  않으므로, 첫 실기 실행에서 디코드가 0이면 이 플래그부터 뒤집어 보십시오. 안드로이드 GLES
  백엔드가 `luminance` 텍스처를 받는지도 미검증이며, 거부되면 `colorfmt="rgb"`로 바꿉니다.
- APK에 opencv를 넣을지는 `buildozer.spec`에 사유와 함께 명시돼 있습니다. 레시피가 실패해
  opencv를 빼면 앱은 뜨지만 **QR을 한 장도 디코드하지 못해** 광학 링크가 성립하지 않습니다
  (우회 절차와 그 의미는 `docs/mobile-build.md` 5-3 참조). 대체 디코더 백엔드(ZXing 등)의 실제
  구현은 이번 범위 밖이며, 백엔드를 꽂을 *자리*만 만들어져 있습니다.
- 앱 UX는 골격 수준입니다(설정 영속화·로그 뷰·파일 전송 UI 없음). iOS·서명·스토어 배포도
  범위 밖입니다.

## 개발 방식

이 저장소는 [tide](https://github.com/) 개발 사이클(milestone → impl → review → release)을 따릅니다.
규약은 [`docs/conventions.md`](docs/conventions.md) 를 참조하세요.

## CHANGELOG

변경 이력은 [CHANGELOG.md](CHANGELOG.md) 를 참조하세요.
