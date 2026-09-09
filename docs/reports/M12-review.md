# M12 리뷰보고서 (review)

> M12는 v1.0 사인오프를 목표로 했고, **T01~T04는 완료했으나 T05(APK 실빌드)가 실패해
> T06~T09가 미수행**이다. 마일스톤 완료 기준 7이 *"성공·실패·미수행 어느 것이든 이 마일스톤을
> 깨지 않는다"* 고 명시했으므로, 이 리뷰의 핵심 물음은 *"실패했는가"* 가 아니라
> **"실패를 정직하고 재현 가능하게 기록했는가, 그리고 완료한 넷이 실제 산출물을 냈는가"** 다.

## 비판점

### 차단 (0건)

없다.

### 권장 (3건 — 전부 in-review 수정 완료)

1. **`docs/mobile-build.md`가 한 문서 안에서 스스로를 반박했다** — 1-3절이
   *"컨테이너는 비-root(`user`)로 돌므로 … `--user root`를 붙여 원인을 가른 뒤 **다시 비-root로
   돌린다**"* 라고 **행동까지 지시**하는데, 같은 파일 5-4절은 *"호스트 볼륨을 마운트해 돌리면
   컨테이너가 **root로** 실행된다 … 이전 주석이 '비-root로 돈다'고 적고 있었는데 **틀렸다**"* 라고
   적는다. impl은 같은 거짓 문장을 `buildozer.spec`에서는 실측으로 바로잡았으면서
   **문서의 쌍둥이 문장은 놓쳤다.** 사실관계가 이번 사이클에 확정됐는데 문서가 반대 지시를
   남긴 자리다.
2. **T05의 실측이 본문 절차에 반영되지 않고 5-4절에만 고였다** — 1-3절의 명령 블록이 여전히
   `docker run --interactive --tty --rm` + `$HOME/.buildozer` **호스트 bind mount**였다. 그
   구성은 5-4가 실측으로 닫은 장애 넷(root 프롬프트 즉사 · NDK unzip 부분추출 · 호스트 메모리
   압박으로 컨테이너 사망 · `--rm` 로그 소실)을 **정확히 재현하는 조합**이고, 1-3절 어디에도
   5-4로 가는 포인터가 없었다. 문서를 위에서 아래로 따라간 다음 사람이 같은 네 함정을 그대로
   다시 밟는다. 마일스톤 T05의 변경 파일 항목이 *"`docs/mobile-build.md`(**실측으로 바뀐 절차**
   반영)"* 라고 적었으므로 **명시된 범위의 미이행**이기도 하다.
3. **`photontcp/mobile/app.py`의 docstring 헤더가 이번 세션의 사실과 정면으로 달랐다** —
   절 제목이 `확인 필요 (no Kivy install and no Android device were available here)`인데,
   T02는 이 환경에 Kivy를 **실제로 설치하고 앱을 띄웠다**. 미검증으로 남긴 것이 아니라
   **틀린 진술**이다. 같은 목록의 `connect_camera` 키워드도 이번에 확정됐는데 미검증 항목으로
   남아 있었다.

### 사소 (6건 — 4건 in-review 수정, 2건 경계 기록)

4. **`buildozer.spec`의 `android.ndk`가 이미 확정된 값을 미검증으로 남겨 두었다**(수정함) —
   주석이 *"**확인 필요** … 그 값이 opencv/numpy 조합에서 통과하는지는 **T06의 실빌드로만
   확인된다**"* 인데, 이번 실빌드가 **r28c로 opencv 포함 전 네이티브 레시피를 100% 컴파일**했다.
   지시된 축의 **반대 방향** — 확정된 것을 미검증으로 남긴 자리다. 동작 영향은 0(값이 buildozer
   기본값과 같다).
5. **새 공개 API `reset_decoder_backend_probe`가 `README.md`에 없었다**(수정함) —
   `decode.py`의 `__all__`에 등재돼 공개 표면이 늘었고 **이번 minor 판정의 주된 근거**가 바로
   그 이름이다. impl이 후속 메모 7로 스스로 신고했으므로 자기 누락은 아니다.
6. **하니스가 `buildozer.spec` 부재를 「하니스 버그」로 오분류했다**(수정함) — `_read_spec`의
   `FileNotFoundError`가 격리 경로로 흘러 *"the check itself CRASHED … This is a bug in the
   harness"* 로 안내됐다. 그러나 그것은 **환경 상태**(잘못된 cwd·불완전한 체크아웃)이고,
   점검 5가 `main.py` 부재를 환경으로 다루는 것과 같은 종류다. **이 하니스의 존재 이유가
   「도구/환경 상태의 정확한 구분」이므로** 그 목적과 어긋나는 유일한 자리였다.
7. **impl 보고서의 완료 기준 대조 표가 선언되지 않은 판정 값을 썼다**(수정함) — 기준 8·9에
   `미수행(방전)` / `미수행`을 적었으나 규약(`criteria-verdict:`)이 정한 집합은
   **충족 · 미충족 · 부분** 셋이다. **차단으로 매기지 않은 근거를 남긴다**: ⑴ 규약의
   `criteria-since: M55`는 **tide 자체 저장소의 마일스톤 번호**라 PhotonTCP의 M12에 그대로
   대입하는 것이 범주 오류이고(즉 이 저장소가 표를 낸 것은 자발적이다), ⑵ 근거 열이 *"APK가
   없어 실측값이 없다"* 를 명시해 **거짓 통과를 만들지 않았다**. `부분`으로 옮기고 그 정의가
   요구하는 **「남은 조각」** 을 근거 열에 적었다(기준 8: 6항목 중 4개 / 기준 9: 실측값 전부).
8. **`photontcp.optical.peer` 속성 접근이 조용히 사라졌다**(경계 기록 — 고치지 않음) —
   HEAD에서는 `from .peer import …`의 부작용으로 `photontcp.optical.peer` **속성**이 심겼는데,
   지연 재수출은 `_LAZY_PEER_EXPORTS`의 두 이름만 처리하므로 `import photontcp.optical as o;
   o.peer` → `AttributeError`다(`from photontcp.optical import peer`와
   `import photontcp.optical.peer` 뒤의 접근은 정상). 레포 내 호출자는 전부
   `from photontcp.optical.peer import …` 형태라 **깨지는 곳이 없다**(스위트 그린). 문서화되지
   않은 접근 형태의 미세 축소이며, **이 축소가 patch가 아니라 minor 판정을 뒷받침한다.**
9. **음성 캐시의 무효화가 사실상 테스트 fixture에 의존한다**(경계 기록 — 고치지 않음) —
   *"막았다 풀면 회복된다"* 는 이제 `blocked_cv2` 종료 시의 `reset_decoder_backend_probe()`
   덕에 성립한다. **마일스톤 완료 기준 4가 명시적으로 허용한 트레이드오프**이고 낙관 방향
   stale 불가 성질은 별도 테스트가 실제로 문다. 다만 앞으로 어떤 테스트가 그 fixture를 거치지
   않고 cv2를 막으면 **세션 전체가 캐시된 부재로 오염**되고 먼 자리에서 붉는다(현재 cv2를 막는
   곳은 `test_decode_backend.py` 하나뿐임을 확인). 또 `except Exception` 전체를 캐시하므로
   일시적 import 실패도 프로세스 수명 내내 디코드를 죽인다(HEAD는 다음 프레임에 자가 회복했다) —
   **실 발생 경로는 미확인**.

## 수정 내용

- **이슈 1**: `docs/mobile-build.md` 1-3의 비-root 서술을 실측대로 고쳤다(*"컨테이너는 실제로
  root로 돈다 … `--user`를 손댈 이유는 없다"*).
- **이슈 2**: 1-3절 앞에 **5-4를 먼저 읽으라는 경고 블록**을 넣고, 명령 블록을 **실제로 돌려
  성공한 형태**로 교체했다 — named volume 준비(0단계) · `-d --name --memory=6g` + `--rm` 제거 ·
  `docker logs -f`/`inspect`/`rm -f` 후속 명령 · NDK 사전 추출로 가는 2단계 안내.
- **이슈 3**: `app.py` docstring 헤더를 *"still unverified — no Android device, and no webcam on
  the dev machine"* 으로 바꾸고, **이번에 닫힌 둘**(위젯 트리·크래시 없는 종료 / `connect_camera`
  키워드)을 명시한 뒤 남은 미검증만 아래에 뒀다. 중복으로 남아 있던 `connect_camera` 항목을
  걷고, GLES 항목에 *"데스크톱 GL은 수용하지만 GLES는 별개 구현"* 을 덧붙였다.
- **이슈 4**: `android.ndk` 주석을 **"r28c로 확인됨"** 으로 바꾸고 무엇이 통과했는지 적었다.
  **값은 여전히 주석 처리로 둔다** — buildozer 기본값이 이미 r28c라 박아도 동작이 같고, 박으면
  buildozer를 올렸을 때 새 기본값과 어긋나 되레 낡기 때문이다(그 사유도 함께 적었다).
- **이슈 5**: `README.md`의 백엔드 seam API 열거에 `reset_decoder_backend_probe` 추가.
- **이슈 6**: 점검 7·8이 `FileNotFoundError`를 잡아 **BLOCK + 환경 상태 문구**로 보고하게 했다
  (`_spec_missing_reason` 헬퍼). **되돌림 실측**: `buildozer.spec`이 없는 복제 트리에서 두 점검이
  *"`buildozer.spec` NOT found at … run this from the repository root …"* 로 나오고, 더 이상
  "CRASHED"로 안내되지 않는다.
- **이슈 7**: impl 보고서 기준 8·9의 판정을 `부분`으로 옮기고 「남은 조각」을 근거 열에 적었다.
- **이슈 8·9는 고치지 않았다** — 경계 기록이다. 8은 문서화되지 않은 접근 형태의 축소로 실사용
  파급이 없고, 9는 마일스톤이 명시적으로 허용한 트레이드오프라 리뷰가 뒤집을 자리가 아니다.

## 검증

리뷰가 **직접 실행한** 실측이다. 아래 값은 **in-review 수정 7건이 모두 끝난 뒤**의 측정이다.

**재검증 결과 (re-verify)** — in-review 수정이 7건이므로 수정 후 전 하니스를 재실행했다:

- `python -m pytest -q` → **319 passed, 2 skipped** (수정 전과 동일 — 회귀 없음).
- `python examples/qr_decode_bench.py` → **exit 0**, clean 249/249 = **100.00%**,
  degraded 249/249 = **100.00%**, `full >= base-only on every set: YES`.
- `python examples/optical_link.py` → **exit 0** / `--role sender` → **exit 2**.
- `python examples/mobile_build_preflight.py` → **exit 0**, `VERDICT: READY`.
- `import photontcp.mobile.app` → 정상(docstring 편집이 모듈을 깨지 않았다).

**지연 재수출 경계 실측**: `from photontcp.optical import *` 정상(`__all__`의 지연 이름이 PEP 562로
해석됨) · 새 프로세스에서 `import photontcp.optical` 후 `sys.modules`에 `photontcp.app`·
`photontcp.session` **없음** · `dir()`·`__all__` 보존. 축소된 형태는 위 비판점 8 하나뿐임을 확인했다.

**공허성 되돌림 실측**(복제 트리, 작업 트리 무접촉): `_cv2_missing` 제거 → 2 failed ·
`register`/`set`의 자동 무효화 제거 → 1 failed · `optical/__init__.py` eager 원복 → 2 failed ·
`docs/conventions.md`에 버전 리터럴 복원 → 가드 1 failed(파일·행 지목) · `MIN_HOLD = 0.0` → 4 failed.
**공허한 검사는 발견되지 않았다.** 문서 가드의 오탐 방지(제외 디렉터리 실재 단언 · CHANGELOG 실주행 ·
비매치 10건 parametrize)도 실제로 물린다.

**T05 실패 주장의 물증**(반증자 실측): `bin/`이 **완전히 비어 있다**(항목 0개) — APK 미산출과
일치하고, 부분 산출물이나 오래된 APK가 남아 오판을 부를 여지도 없다. `.buildozer/`는 **5.4 GB**
(`android/`·`applibs/`·`state.db` 존재) — 빌드가 실제로 SDK/NDK를 내려받고 레시피 컴파일까지
진행됐다는 물증이며, impl의 *"재시도 비용은 캐시 덕에 작다"* 와도 일치한다. 둘 다 `.gitignore`
대상이라 커밋 위생에는 영향이 없다.

**다만 캐시 크기는 「빌드가 멀리 갔다」까지만 말해 주고 어느 레시피가 통과했는지는 말해 주지
않는다.** 빌드 로그가 컨테이너 정리로 사라져 **반증자는 opencv 100% 컴파일과
`charset_normalizer` 휠 오류를 독립 재현하지 못했다** — 그 둘은 리뷰 메인이 빌드 진행 중 로그에서
직접 확인한 것이다(`libopencv_gapi.so` 링크 · `opencv_python3` 타깃 · `build.py:927`의 pip 호출
형태). **이 비대칭을 숨기지 않고 적는다** — 이 사이클의 가장 중요한 주장 하나가 단일 관찰자에게
의존한다는 뜻이고, 다음 시도에서 `--rm` 없이 돌리라는 절차를 문서에 넣은 이유가 이것이다.

**미검증 잔여 리스크** — 마일스톤이 이월한 범위와 일치한다: APK 산출 자체, 폰 설치·기동 스모크
6항목 중 4개(권한·`flip_*`·콜백 시그니처·버퍼 재사용), 안드로이드 GLES의 `luminance` 수용,
wakelock 실동작, 체크 A·체크 B 실측값. 더해 **빌드 로그가 보존되지 않아 T05의 세부 실측을 사후에
재대조할 수 없다**(다음 시도에서는 `--rm` 없이 돌리라는 절차를 문서에 넣었다).

## 릴리즈 판정

**가능** — 추천 버전: **v0.12.0 (minor)**

계측: in-review 수정 차단 0/권장 3/사소 4 · 반증 시도 수행 성립 8건 · 연속 폴백(fallback-streak) 0 · 부인 기록(precedent-waiver) 0건 · 연속 부인(waiver-streak) 0 · 재작업 라운드(rework) 0

- **판정 근거**: 완료 기준 11개 중 **9개 충족 · 2개 부분**이고 미충족은 0이다. 부분 둘(기준 8·9)은
  APK 부재에서 비롯하며, **기준 7이 실패·미수행을 명시적으로 허용**하므로 마일스톤을 깨지 않는다.
  T01~T04는 실제 산출물을 냈고(하니스 · 데스크톱 선검증 + 45 테스트 · 이월 사소 2건 해소 ·
  문서 복제 제거 + 재발 가드), 되돌림 실측에서 **공허한 검사가 하나도 발견되지 않았다.**
  이번 라운드의 지적 9건은 차단 0이며 7건을 in-review로 닫고 2건은 경계 기록으로 남겼다.
- **T05 실패를 판정에 반영하지 않는 이유**: 마일스톤이 그 결과를 사전에 허용했고, 실패가
  **정직하고 재현 가능하게** 기록됐다 — 닫은 장애 4개는 절차·스펙에 반영됐고(위 이슈 1·2 수정
  포함), 미해결 1개는 `pythonforandroid/build.py:927`이라는 **파일·행 수준까지 특정**됐으며,
  듣지 않은 우회 둘은 **되돌려** 스펙에 죽은 설정이 남지 않았다(반증자가 `requirements`가 HEAD와
  문자 동일하고 `p4a.extra_args`가 파서에 키로 잡히지 않음을 확인).
- **minor인 이유**: 신규 공개 표면(`reset_decoder_backend_probe`)과 신규 하니스
  (`examples/mobile_build_preflight.py`)가 추가되고, 기존 계약(`decode_frame` 시그니처·None 계약,
  CLI 인자·종료 코드, `run_peer`)은 불변이다. 지연 재수출은 동작 호환이나 위 비판점 8의 미세
  축소가 있어 **patch가 아니라 minor**가 맞다. **v1.0은 사인오프 미수행이므로 주장하지 않는다.**
- 반증 시도: 수행(서브에이전트 디스패치) — 성립 8건, 전부 위 비판점 섹션에 편입했다. 반증자가
  독립 재현하지 못한 항목(빌드 로그 소실분)은 `## 검증`에 그 비대칭과 함께 명시했다.

## 다음 단계

- **`/tide:release v0.12.0`**
- **릴리즈 후 최우선 후속 — APK를 막는 단일 지점**: p4a가 안드로이드 휠 URL을 요구사항에 넣고
  `--platform` 없이 호스트 pip으로 설치하는 경로(`pythonforandroid/build.py:927`). 세 경로 중
  하나 — ① 상류 수정 제출 ② `p4a.branch`/`p4a.fork`로 그 경로가 없는 리비전 고정
  ③ `p4a.source_dir`로 패치한 체크아웃 지정. **캐시 volume `photontcp-buildozer`가 보존돼 있어
  재시도 비용이 작다**(SDK/NDK와 opencv 포함 전 레시피가 이미 컴파일돼 있다).
- **그 다음**: APK 산출 → 폰 설치 + 기동 스모크 6항목 → 체크 A·체크 B 실측 → v1.0 판단.
  **막는 것이 하드웨어에서 툴체인으로 옮겨 갔다**는 사실은 `docs/v1.0-signoff.md` 3절에 기록돼 있다.
- **경계 기록으로 남긴 둘**(비판점 8·9): `photontcp.optical.peer` 속성 접근 축소 —
  필요해지면 `__getattr__`에 서브모듈 폴백을 더한다. 음성 캐시의 fixture 의존 — cv2를 막는 두 번째
  테스트 모듈이 생기는 순간 공용 fixture로 올린다.
- **이월 후속**: `docs/project-context.md`에 `reset_decoder_backend_probe` 반영,
  `docs/mobile-build.md:264`의 `adb install … 0.10.0 ….apk` 예시 갱신(실빌드 성공 시),
  `app.py`의 Kivy 폐기예정 속성(`allow_stretch`/`keep_ratio` → `fit_mode`),
  `photontcp/optical/cv2_devices`의 가드된 eager import.
- **회고 표 갱신**: `docs/reports/retro.md` 최상단 섹션에서 M11이 닫은 세 행이 아직 `미반영`이다 —
  다음 `/tide:retro`가 `반영`으로 옮긴다. M12가 닫은 항목(이월 사소 2건·문서 버전 복제)도 함께
  반영 대상이다.
