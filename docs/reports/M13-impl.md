# M13 완료보고서 (impl)

## 개요

**빛만으로 실제 통신이 성립했다.** M13은 M12가 단일 지점까지 좁혀 놓은 p4a 상류 버그를 34줄
패치로 우회해 **동작하는 APK를 산출**하고, 그것을 안드로이드 기기 2대에 설치해 **체크 B(2-머신
왕복)를 통과**시켰다 — 양쪽 모두 `PASS | established=True | MATCH (2/2) | closed=True`. M8부터
6사이클 동안 "하드웨어가 없어서" 미뤄져 온 항목이 실측으로 닫혔다.

다만 **v1.0은 아직 주장하지 않는다**: 사인오프 게이트는 두 조건인데(체크 A 수신율 ≥ 80% ·
체크 B 왕복), 통과한 것은 B이고 **A는 수행 불가**다(자기 화면을 자기 카메라로 볼 수 있는 장비가
없다). 처리 방향은 리뷰·다음 마일스톤이 정한다.

하드웨어 없이 닫은 것도 함께 처리했다 — M12 경계 기록 둘, Kivy 폐기예정 속성, 그리고 **계획
밖에서 발견한 출하 결함 하나**(`on_start` 라이프사이클 충돌).

디스패치: L0 = T02 ∥ T03(서브에이전트 병렬, 예상 변경 파일 교집합 0), T01은 메인이 직접 수행
(장시간 docker 오케스트레이션과 우회 경로 판단이 세션 맥락에 의존). T04~T07은 실기 필요로
메인이 순차 수행.

재작업 라운드(rework): 0

## 태스크별 수행 내용

- **M13-T01** — **p4a 상류 버그 우회 + APK 산출. 성공.**
  - **경로 ① (p4a 리비전 고정)을 데이터로 기각**했다. 문제 기능은 커밋 `2f107b15`
    "Add support for prebuilt wheels (#3280)"(2026-03-28)이고, **그것을 포함하지 않는 가장 최근
    릴리즈가 `v2024.01.21`** 로 약 2년 전이다(태그별 `git merge-base --is-ancestor`로 실측).
    Python 3.14 / NDK r28c / buildozer 1.6.1 조합을 지원할 수 없어 마일스톤이 정한 판단 기준에서
    탈락한다.
  - **경로 ② (패치 파일)를 채택**했다. `tools/p4a-android-wheel-url.patch` — 34줄 diff.
    **M12가 "URL을 넣는다"까지만 알던 것을 이번에 구조까지 특정했다**: p4a의
    `process_python_modules()`가 pip을 `--platform=android_…`로 돌려 의존성을 해석하고,
    `is_wheel_compatible()`이 참인 휠의 URL을 요구사항에 넣는데 — 그 변수 이름이 `pure_python`
    이라 **안드로이드 전용 휠까지 "순수 파이썬"으로 분류**된다 — 설치는 그 platform 태그 없이
    호스트 pip으로 돈다. **해석과 설치가 서로 다른 플랫폼을 가정**하는 것이 버그의 정체다.
    패치는 설치 단계에 해석 단계와 같은 태그를 넘겨 둘을 일치시킨다. 포크를 벤더링하지 않고
    diff 한 장만 두어 "무엇을 왜 바꿨는지"가 한 파일로 읽히게 했고, 헤더에 증상·원인·적용
    절차·실측을 담았다(`git apply --check` 통과 및 재적용 확인).
  - **효과 실측**: 네 번의 시도를 막던 지점이 열려 순수 파이썬 의존성 11개가 정상 설치됐다 —
    `camera4kivy-0.3.3 certifi-2026.7.22 chardet-7.6.0 charset-normalizer-3.5.1 filetype-1.2.0
    gestures4kivy-0.1.4 idna-3.19 requests-2.34.2 segno-1.6.6 six-1.17.0 urllib3-2.7.0`.
  - **그 다음이 이 사이클에서 가장 위험했던 자리다 — 조용한 파국.** 빌드가 **exit 0으로
    성공하고 43MB APK가 나왔는데, 그 안에 `photontcp/`가 한 파일도 없었다.** 로그에 오류가
    하나도 없다. 원인은 p4a `bootstraps/common/build/build.py`의 분기다:
    `if not use_setup_py or (setup.py 없음 and pyproject.toml 없음)` — `dist_info.json`에
    `use_setup_py=True`가 기록돼 있고 `source.dir`에 **`pyproject.toml`이 보이기만 해도**
    p4a는 *"앱은 site-packages에 설치돼 있겠지"* 라며 `main.py` 하나만 담는다. 그런데 buildozer는
    `--ignore-setup-py`를 넘기므로 **그 설치는 일어나지 않는다.** 로그에서
    `Copying main.py's ONLY, since other app data is expected in site-packages.`와
    임시 디렉터리(`/tmp/tmpXXXX/main.py`) 컴파일로 확정했다.
    - **M12가 넣은 `source.include_exts`의 `toml`이 방아쇠였다.** 그 트레이드오프를 새 증거로
      다시 판단해 되돌렸다 — 기기에서 `__version__`이 예뻐지는 것보다 **APK가 동작하는 것**이
      압도적으로 중요하다(그 사유를 스펙 주석에 상세히 남겼다).
    - 재빌드 후 로그가 `No setup.py/pyproject.toml used, copying full private data into .apk.`
      로 바뀌고 `photontcp` 소스 36개가 실제 앱 디렉터리에서 컴파일됐다.
  - **최종 APK 검증**(완료 기준 3이 요구한 `requirements` 실측): `private.tar` 52 엔트리 중
    **photontcp 46건**(`mobile/app.pyc`·`kivy_devices.pyc` 포함), `main.pyc` 존재,
    `pyproject.toml` 부재, `libpybundle.so`에 `cv2.so`·segno 22·camera4kivy 22·
    charset_normalizer 28·numpy 981. **opencv는 APK에 실제로 들어갔다.**
  - **venv pip 혼재의 원인을 정정했다.** M12는 *"컨테이너가 OOM으로 죽으며 남긴 2차 피해"* 로
    기록했으나 **틀렸다** — M13에서 OOM 없이 재발했다. 실제 원인은 **재실행 자체**다:
    `python -m venv venv`가 기존 venv에 번들 pip(25.3)을 다시 깔고 업그레이드된 26.2.1과 섞여
    `ImportError: cannot import name 'BuildDependencyInstallError'`가 난다. 재실행 전 그 venv를
    지우는 것이 대처다.
  - 산출물: `bin/photontcp-0.12.0-arm64-v8a-debug.apk` (43.4 MB, gradle `BUILD SUCCESSFUL`).
    실측 결과는 `docs/mobile-build.md` 5-4·5-5·5-6절에 재현 가능한 형태로 남겼다.
- **M13-T02** (L0 병렬) — M12 경계 기록 둘.
  - **① `photontcp.optical.peer` 속성 접근 복원**: `__getattr__`에 서브모듈 폴백을 더하되
    `importlib.util.find_spec`으로 **실재하는 서브모듈일 때만** import한다(아무 이름이나 시도하지
    않는다). **결합 테스트가 양방향으로 문다** — 같은 테스트가 ⑴ `import photontcp.optical` 직후
    상위 계층이 `sys.modules`에 없음과 ⑵ `opt.peer` 접근 성공을 함께 단언해, 폴백을 지우면 붉고
    eager로 되돌려도 붉는다(둘 다 되돌림 실측).
  - **② 음성 캐시의 fixture 의존**: 마일스톤이 제시한 두 선택지 대신 **더 나은 세 번째**를
    택했다 — `tests/conftest.py`의 **autouse 안전망**. 근거: fixture를 공용으로 올려도 *그것을
    쓰지 않는* 미래 테스트는 여전히 세션을 오염시키므로 방어가 작성자 선택에 의존한다. autouse는
    **차단 방식과 무관하게** 매 테스트 종료 시 무효화한다. **위험이 이론이 아님을 실측했다**:
    `blocked_cv2`를 거치지 않고 cv2를 막는 테스트를 만들어 먼저 돌리자 안전망 없이는
    `test_qr.py`가 **12건 실패**(M12-review가 예측한 "먼 자리"에서), 안전망 있으면 17 passed.
  - ③ `docs/project-context.md`에 seam 공개 API 4종 반영(`README.md`는 이미 반영돼 있어 손대지
    않았다 — 실제 상태를 먼저 확인했다).
- **M13-T03** (L0 병렬) — Kivy 폐기예정 속성.
  - **p4a가 넣는 Kivy 버전을 레시피에서 직접 확인**해 판단했다: clone된 p4a(`v2026.05.09`)의
    `KivyRecipe.version = '2.3.1'`, 그리고 태그별 이력상 **`v2023.05.21` 이후 어떤 리비전도
    Kivy ≥ 2.2**를 넣는다. 즉 `fit_mode`(2.2+)를 못 쓰는 도달 가능한 핀이 없다 → `hasattr` 분기
    없이 `fit_mode="contain"`으로 직행하고, 어느 환경에서도 실행되지 않는 죽은 가지를 남기지
    않았다.
  - **등가성 실측**: Kivy 2.3.1은 `allow_stretch=True, keep_ratio=True`를 내부적으로 `fit_mode`로
    매핑한다 — 변경 전후 모두 `qr_image.fit_mode == 'contain'`. 표시 동작 불변.
  - **경고 전/후 대조**: 변경 전 폐기예정 경고 **2건**, 변경 후 **0건**.
- **계획 밖 발견 — 출하된 실제 결함 (메인이 처리)**: T03이 데스크톱 실행 중
  **아무도 Start를 누르지 않았는데 세션이 자동 시작되는 것**을 관찰했다. 원인은
  `PhotonTCPApp.on_start`가 **Kivy `App`의 라이프사이클 이벤트와 이름이 같아** 기동 시
  프레임워크가 직접 디스패치하는 것이다(`App.run` → `self.dispatch('on_start')`).
  **v0.11.0부터 출하돼 있었고**, 그대로 폰에 설치했다면 T04의 스모크 관찰 1번(권한)과 6번
  (wakelock)이 통째로 오염됐을 것이다. `on_start_pressed`로 개명하고 배선을 옮긴 뒤,
  **이름 충돌이 되살아나면 붉는 가드 2건**을 더했다(되돌림 실측: 2 failed).
  `on_stop`의 이중 역할은 **의도적**이므로 유지하고, 가드가 그 구분까지 단언한다.
  **실기에서 확증됐다** — 앱이 뜬 뒤 상태 줄이 `idle - press Start`에 머물렀다.
- **M13-T04** — **폰 설치 + 기동 스모크. 6항목 중 4 확정, 2 미확정**(리뷰 정정).
  기기: Galaxy S26 (SM-S942N, Android 16/API 36, arm64-v8a). `adb install` 성공, 앱 기동 —
  Python 3.14.2 · Kivy 2.3.1 · **OpenGL ES 3.2** (ANGLE, Samsung Xclipse 960 on Vulkan 1.4.304).
  트레이스백·ImportError 없음(= APK 코드 포함 수정이 실기에서 확인됐다).
  - **압권은 종단 검증이다**: 폰 화면의 QR을 스크린샷으로 떠 **우리 프로젝트의 `decode_frame`으로
    직접 디코드**했고, 24바이트가 나와 nonce(6)를 떼고 `Packet.unpack`에 넣으니
    **`flags=Flags.SYN`, `session_id=1`, `seq=1000`, CRC 통과** — `run_peer`의
    `isn=1000 if is_initiator`와 정확히 일치한다. encode → KivyDisplay → GLES → 화면 → 캡처 →
    decode → 패킷 해석이 한 줄로 이어졌다.
- **M13-T05** — **체크 A: 수행 불가.** 셀프체크는 한 기기가 자기 화면을 자기 카메라로 봐야
  하는데 그럴 장비가 없다: 개발 PC에는 **카메라가 아예 없고**(`Get-PnpDevice` 실측), 폰·태블릿은
  화면과 전면 카메라가 **같은 방향**이라 자기 화면을 직접 볼 수 없다(거울 필요). 앱에도 셀프체크
  모드가 없다. 따라서 **수신율(%) 실측값은 이 사이클에 존재하지 않는다.** 회고 `미반영` 행인
  **실 열화(저조도) 수신율**도 같은 이유로 닫지 못했다.
- **M13-T06** — **체크 B: PASS.** 개발 PC에 카메라가 없어 폰+PC가 불가능해, **안드로이드 기기
  2대**로 같은 전이중 왕복을 성립시켰다(사용자 판단).

  | 기기 | role | 요약 줄 |
  |---|---|---|
  | Galaxy Tab S9 FE+ (SM-X610, Mali-G68) | `receiver` | `PASS \| established=True \| MATCH (2/2) \| closed=True \| wall=16.2s` |
  | Galaxy S26 (SM-S942N, Xclipse 960) | `sender` | `PASS \| established=True \| MATCH (2/2) \| closed=True \| wall=14.0s` |

  조건: 양쪽 `front` 카메라 · `scale=8` · `hold=0.8` · 실내 형광등 · **약 20 cm** · 시도 2~3회.
  - **성공의 관건은 카메라 정렬 하나였다.** 운영자 관찰: *"대부분 수신 실패의 상황이 카메라
    정렬에 의한 실패였다."* 실패한 초기 시도들의 공통점은 상대 QR이 프레임에서 **너무 작거나
    비스듬한** 것이었다(첫 시도에서 프레임 폭의 1/4 남짓). `hold`를 0.4 → 0.8로 올린 것도 함께
    적용했다.
- **M13-T07** — 사인오프 기록 + v1.0 판단. `docs/v1.0-signoff.md` 3절에 실측 기록을 채우고,
  **v1.0 판단을 명시했다**: 게이트 두 조건 중 **(b) 체크 B는 통과, (a) 체크 A는 수행 불가**이므로
  **문서화된 정의대로라면 v1.0 승격 조건이 아직 충족되지 않았다.** `README.md`의 한계 절도
  실측으로 확정된 것만 좁혔다.

### 이번 사이클이 정정한 거짓 서술 (문서 결함)

**`flip_*` 플래그에 관한 주장이 사실이 아니었다.** README·`v1.0-signoff.md`·마일스톤·코드
docstring이 *"QR은 상하/좌우가 뒤집히면 디코드되지 않으므로 `flip_*`가 실기 첫 확인 항목"* 이라고
M11부터 반복해 왔는데, **측정 결과 거짓이다** — `cv2.QRCodeDetector`는 90/180/270° 회전과
**좌우·상하 반전(거울상)을 모두 디코드**한다(7개 변형 전부 성공, 캐스케이드 없이 **기본
detector만으로도** 성공). 사용자 질문("뒤집어서 인식이 가능하면 교차 배치를 시험하고 싶다")이
계기가 되어 측정했고, 그 결과가 **실패 원인 후보에서 `flip` 가설을 제거**해 진단을 광학 정렬
하나로 좁히는 데 직접 기여했다. 네 곳(README · signoff 4군데 · `kivy_devices.py` · `app.py`)을
정정했다.

## 완료 기준 대조

| 기준 | 판정 | 근거 |
|---|---|---|
| 1 | 충족 | ⑴ `import photontcp.optical as o; o.peer`가 `AttributeError` 없이 동작하고, **같은 테스트**가 `import photontcp.optical` 직후 `sys.modules`에 `photontcp.app`·`photontcp.session`이 **없음**도 함께 단언한다(`subprocess` 격리). 되돌림 양방향 실측: 폴백 삭제 → 2 failed, eager 원복 → 결합 테스트 붉음. ⑵ 음성 캐시는 **conftest autouse 안전망**으로 처리하고 그 선택의 근거를 위 T02에 적었다(공용 fixture보다 강한 이유 + 오염 위험 12 failed 실측). |
| 2 | 충족 | `fit_mode="contain"` 직행. 데스크톱 실행에서 폐기예정 경고 **2건 → 0건**(전/후 대조), 위젯 트리 정상 구성(root 4자식 / settings 4 / middle 2 / buttons Start·Stop), `KivyDisplay.show()`가 실제 텍스처를 올리는 경로도 확인. **p4a가 넣는 Kivy가 2.3.1**임을 레시피에서 직접 읽어 하위호환 분기가 불필요함을 근거로 남겼다. |
| 3 | 충족 | **APK 산출 성공**: `bin/photontcp-0.12.0-arm64-v8a-debug.apk` 43.4 MB, gradle `BUILD SUCCESSFUL in 1m 37s`, NDK r28c, buildozer 1.6.1.dev0. `requirements` 실측: `libpybundle.so`에 `cv2.so` 존재(**opencv 포함**), private.tar에 photontcp 46건. 택한 우회는 경로 ②(패치)이고 ①을 기각한 근거는 리비전 실측이다. 빌드 중 만난 장애 둘(휠 URL·앱 코드 누락)과 그 대처를 `docs/mobile-build.md` 5-4·5-5에 기록했다. |
| 4 | 부분 | **4/6 확정**(리뷰가 정정 — impl 초안은 5/6으로 셌으나 ④는 확정이 아니다). ① 카메라 권한 — 요청→승인→세션 진행(`granted=true` 실측) ② **GLES `luminance` 수용** — 렌더될 뿐 아니라 화면 QR이 **디코드**됨 ③ **`flip_*` 기본값** — 디코드 성공으로 옳음이 증명되고, 나아가 **flip 자체가 디코드에 무관**함이 별도 측정으로 확정 ④ camera4kivy 콜백 — **미확정**: 마일스톤은 *"실제 인자가 무엇인지 기록한다"* 를 요구했으나 인자 목록을 기기에서 적지 않았다. 확인된 것은 **경로가 동작한다**는 사실뿐이다(프리뷰 + 체크 B 성립) ⑥ **wakelock** — 앱 uid가 `SCREEN_BRIGHT_WAKE_LOCK`을 2분+ 보유(`dumpsys power`). **남은 조각: ④ 콜백 인자 목록 · ⑤ 분석 버퍼 재사용 여부** — 찢김은 관찰되지 않았으나 `copy_on_submit=True`가 켜져 있어 적극적으로 검증되지 않았다(끄고 재시험해야 확정). 확정된 항목은 README·docstring의 「확인 필요」에서 뺐다. |
| 5 | 미충족 | 체크 A **수행 불가** — 자기 화면을 자기 카메라로 볼 수 있는 장비가 없다(PC 카메라 부재 실측, 폰·태블릿은 화면과 전면 카메라가 동일 방향). 수신율(%) 실측값이 존재하지 않고, **저조도 측정도 함께 미수행**이다. 사유는 T01·T04 결과가 아니라 **장비 제약**이며 그 사실을 `docs/v1.0-signoff.md`에 적었다. |
| 6 | 충족 | 양쪽 요약 줄 원문과 `scale`/`hold`를 기록하고 게이트 판정(양쪽 PASS)을 명시했다. **PC 웹캠 부재로 폰+PC 조합은 수행 불가**였고 그 사실을 숨기지 않고 적었으며, 대신 **안드로이드 2대**로 같은 기준을 충족시켰다(양쪽 `established=True` · `MATCH (2/2)` · `closed=True`). |
| 7 | 충족 | `docs/v1.0-signoff.md` 3절의 상태 블록을 이번 결과로 갱신하고, **"체크 B 통과 · 체크 A 미수행 → 문서화된 게이트가 요구하는 두 조건이 모두 충족되지는 않았다"** 를 명시했다. `README.md` 한계 절은 실측으로 확정된 것만 좁혔다(GLES luminance·픽셀 콜백은 확정으로, 버퍼 재사용은 미검증으로 유지). |
| 8 | 충족 | 전체 스위트 그린(수는 `python -m pytest -q` 출력이 단일 출처). 벤치 exit 0, `full >= base-only on every set` 유지. `optical_link.py` exit 0 / `--role sender` exit 2. `--help` 인자 집합 불변. `mobile_build_preflight.py` 실행되고 종료 코드가 BLOCK 유무를 따른다(현재 exit 0). |
| 9 | 충족 | `buildozer.spec`을 `configparser`로 읽었을 때 `p4a.extra_args` 키가 **잡히지 않는다**(주석 상태). M12가 되돌린 `--skip-prebuilt`와 `charset-normalizer` 핀 모두 활성 설정으로 남아 있지 않고, `requirements`는 `python3,kivy,camera4kivy,gestures4kivy,numpy,segno,opencv`로 복원돼 있다. 이번에 되돌린 `source.include_exts`의 `toml`도 사유와 함께 제거됐다. |

## 변경 파일 요약

| 구분 | 파일 |
|---|---|
| 추가 | `tools/p4a-android-wheel-url.patch`, `tests/conftest.py`, `docs/milestones/M13.md`(마일스톤 단계 산출물), `docs/reports/M13-impl.md`(본 보고서) |
| 수정 | `buildozer.spec`, `docs/mobile-build.md`, `docs/v1.0-signoff.md`, `README.md`, `docs/project-context.md`, `photontcp/optical/__init__.py`, `photontcp/mobile/app.py`, `photontcp/mobile/kivy_devices.py`, `tests/test_optical_import.py`, `tests/test_decode_backend.py`, `tests/test_mobile_app.py` |
| 삭제 | (없음) |

기준선 = v0.12.0 태그 직후의 깨끗한 워킹트리. `photontcp/mobile/kivy_devices.py`의 변경은
**docstring 정정 한 건**이고(T04가 조건부로 열어 둔 코드 수정은 결함이 드러나지 않아 없었다),
`photontcp/mobile/app.py`는 T03의 `fit_mode` + 계획 밖 `on_start` 개명 + docstring 정정이다.
`camerax_provider/`·`.buildozer/`·`bin/`은 `.gitignore` 대상이라 추적되지 않는다 —
**APK는 커밋되지 않는다.**

## 테스트 결과

아래 값은 **본 보고서를 포함한 모든 산출물이 나온 뒤의 마지막 측정**이다(`measure-after-artifacts`).

- `python -m pytest -q` → **326 passed, 2 skipped** (v0.12.0 기준선 319 passed·2 skipped →
  **+7 신규**, 회귀 0). 신규: `test_optical_import.py` 3 · `test_mobile_app.py` 4(위젯 트리 ·
  `fit_mode` 가드 · `on_start` 라이프사이클 가드 2).
- `python examples/qr_decode_bench.py` → **exit 0**, clean 249/249 = **100.00%**,
  degraded 249/249 = **100.00%**, `full >= base-only on every set: YES`.
- `python examples/optical_link.py` → **exit 0** / `--role sender` → **exit 2**.
- `python examples/mobile_build_preflight.py` → **exit 0** (`VERDICT: READY`).
- **실기 실측(체크 B)**: 위 T06 표. 양쪽 `PASS`.
- **APK 빌드**: `bin/photontcp-0.12.0-arm64-v8a-debug.apk` 43.4 MB, gradle
  `BUILD SUCCESSFUL in 1m 37s`.

**구현 중 발견·처리한 이슈**

1. **조용한 파국 — 빌드 성공, 앱 코드 없음.** 오류 하나 없이 exit 0으로 43MB APK가 나왔는데
   `photontcp/`가 통째로 빠져 있었다. **완료 기준 3이 `requirements` 실측을 요구하지 않았다면
   폰에서 즉사하는 APK를 "T01 성공"으로 보고했을 것이다.** 검증 절차를 명령까지 포함해
   `docs/mobile-build.md` 5-5에 남겼다.
2. **M12 진단의 정정.** venv pip 혼재는 OOM의 2차 피해가 아니라 **재실행 시 구조적 재발**이었다.
   원인을 코드나 환경에 돌리기 전에 상태(두 `pip-*.dist-info` 공존)를 본 것이 맞았다.
3. **모니터의 거짓 종료 신호.** `docker inspect`가 일시적으로 깨진 출력을 반환해 컨테이너가
   죽었다고 보고했으나 실제로는 살아 있었다. 선언 전에 직접 확인해 오탐임을 밝히고, 이후
   모니터를 "3회 연속 실패에만 종료로 판정"하도록 고쳤다.
4. **문서가 틀렸던 자리.** `flip_*`에 관한 주장이 M11부터 네 곳에서 반복돼 왔고 모두 거짓이었다
   (위 "정정한 거짓 서술" 절). 사용자 질문이 계기가 되어 측정했고, 그 결과가 실패 진단을
   좁히는 데 직접 기여했다.
5. **고밀도 화면의 UI 잘림.** 폰(3배 밀도)에서 설정 행이 위에서 잘려 글자가 안 보인다. 태블릿
   에서는 정상 — 위젯 높이가 **픽셀 고정**(`height=44`)이라 밀도에 따라 달라진다. 기능에는
   지장이 없어 고치지 않았고 README 한계 절에 적었다.

## 미해결·후속 메모

1. **v1.0 게이트의 (a)가 열려 있다(최우선).** 체크 A를 수행하려면 셋 중 하나가 필요하다 —
   ⑴ PC용 USB 웹캠 ⑵ 앱에 **셀프체크 모드**(거울을 쓰거나, 자기 화면을 자기 카메라로 보는 배치)
   ⑶ 게이트 정의 재검토(체크 B가 A의 목적을 실질적으로 대체하는지). **⑶은 프로젝트 정의에 관한
   판단이라 리뷰·사용자의 몫이다** — impl이 정할 사안이 아니다.
2. **스모크 ⑤ 분석 버퍼 재사용은 여전히 미확정.** `copy_on_submit=True`가 켜져 있어 문제가
   드러날 수 없었다. 끄고 재시험하면 확정되지만, 실패 시 증상이 "조용한 디코드 실패"라 위험
   대비 이득이 작다. 현행 유지를 권한다.
3. **앱에 캡처 경로 관측 수단이 없다.** 이번 진단에서 "프레임이 디코더에 도달하는가"를 밖에서
   알 수 없어, 광학 문제와 캡처 경로 문제를 구분하지 못한 채 시도를 반복했다(결국 성공으로
   구분됐다). 상태 줄에 **캡처/디코드 프레임 수**를 표시하면 다음 실기 세션의 진단 비용이 크게
   준다. 실질적 후속 1순위다.
4. **고밀도 화면 UI 잘림**(위 이슈 5) — 위젯 높이를 dp 기반으로 바꾸는 작은 작업이다.
5. **`p4a` 상류 제출.** 우리 패치는 상류 버그의 최소 수정이라 그대로 PR이 될 수 있다. 다만
   **외부 저장소에 대한 공개 행위**라 사용자 판단이 필요해 이번 범위에 넣지 않았다.
6. **APK는 커밋되지 않는다**(`bin/`이 `.gitignore` 대상). 배포가 필요하면 릴리즈 자산으로
   올리는 별도 결정이 필요하다.
7. **회고 표 갱신이 밀려 있다** — M11이 닫은 세 행이 아직 `미반영`이고, 이번에 닫힌
   **v1.0 사인오프 실측(부분: 체크 B)** 과 M12 이월 사소 2건도 반영 대상이다. impl은 회고
   문서를 고치지 않으므로 다음 `/tide:retro`의 몫이다.
8. **재현성은 1회 측정이다.** 체크 B는 성공 1회를 기록했다. 사인오프 문서가 반복을 요구하지
   않으므로 기준은 충족하나, 안정성 여유(수신율·재시도율)는 알 수 없다.
