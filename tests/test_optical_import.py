"""``photontcp.optical`` 패키지 초기화의 계층 방향 테스트 (M12-T03, 완료 기준 5).

M11-review 사소 11: ``optical/__init__.py`` 가 ``.peer`` 를 무조건 import 하고
``peer.py`` 가 ``photontcp.app``·``photontcp.session`` 을 import 하므로,
``import photontcp.optical`` 만으로 **상위 계층 전체**가 끌려왔다. README 가 말하는
"상위 계층이 ``Channel`` 에만 의존한다"의 **반대 방향** 의존이 전송 계층 패키지
초기화에 들어와 있던 것이다.

M12-T03 은 PEP 562 모듈 수준 ``__getattr__`` 로 ``run_peer``·``PeerResult`` 를
**지연 재수출**한다. 공개 표면(``__all__``)은 그대로이고
``from photontcp.optical import run_peer`` 도 종전대로 동작해야 한다
(기존 호출자: ``examples/optical_link.py``).

**왜 subprocess 인가**: ``sys.modules`` 는 프로세스 전역이고, 같은 세션의 다른
테스트가 이미 ``photontcp.app``/``photontcp.session`` 을 import 했을 수 있다. 그러면
"끌려오지 않는다" 단언이 **공허해진다** — 이미 있는 모듈은 누가 넣었는지 알 수 없다.
그래서 각 단언을 **새 프로세스**에서 돌린다. 테스트 안에서 ``sys.modules`` 를 손으로
비우는 방법은 부모 패키지 속성까지 얽혀 훨씬 취약하다.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _run(source: str) -> subprocess.CompletedProcess[str]:
    """레포 루트를 cwd 로 두고 새 인터프리터에서 ``source`` 를 실행한다."""
    return subprocess.run(
        [sys.executable, "-c", source],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )


def _run_ok(source: str) -> str:
    proc = _run(source)
    assert proc.returncode == 0, (
        f"자식 프로세스가 실패했다 (rc={proc.returncode})\n"
        f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
    )
    return proc.stdout


# --------------------------------------------------------------------------- #
# 1. 상위 계층이 끌려오지 않는다
# --------------------------------------------------------------------------- #


def test_importing_optical_does_not_pull_in_upper_layers() -> None:
    """새 프로세스에서 ``import photontcp.optical`` 직후 상위 계층이 없다."""
    out = _run_ok(
        "import sys\n"
        "import photontcp.optical\n"
        "leaked = [n for n in ('photontcp.app', 'photontcp.session',"
        " 'photontcp.optical.peer') if n in sys.modules]\n"
        "print(repr(leaked))\n"
    )
    assert out.strip() == "[]", (
        f"패키지 초기화가 상위 계층을 끌고 왔다: {out.strip()}"
    )


def test_transport_layer_modules_are_still_imported_eagerly() -> None:
    """전제 확인: 지연시킨 것은 ``peer`` 뿐이고 전송 계층은 그대로 즉시 로드된다.

    이 단언이 없으면 위 테스트는 "``optical`` 이 아무것도 import 하지 않는다"로도
    통과할 수 있어, 무엇이 지연됐는지 고정되지 않는다.
    """
    out = _run_ok(
        "import sys\n"
        "import photontcp.optical\n"
        "need = ('photontcp.optical.channel', 'photontcp.optical.devices',"
        " 'photontcp.qr.decode', 'photontcp.channel.base')\n"
        "print(repr([n for n in need if n not in sys.modules]))\n"
    )
    assert out.strip() == "[]", f"전송 계층이 로드되지 않았다: {out.strip()}"


# --------------------------------------------------------------------------- #
# 2. 공개 표면은 불변이다
# --------------------------------------------------------------------------- #


def test_attribute_access_resolves_run_peer_lazily() -> None:
    """``photontcp.optical.run_peer`` 속성 접근이 그 시점에 ``peer`` 를 로드한다."""
    out = _run_ok(
        "import sys\n"
        "import photontcp.optical as opt\n"
        "before = 'photontcp.optical.peer' in sys.modules\n"
        "fn = opt.run_peer\n"
        "after = 'photontcp.optical.peer' in sys.modules\n"
        "import photontcp.optical.peer as peer_mod\n"
        "print(repr((before, after, fn is peer_mod.run_peer, callable(fn),\n"
        "            opt.PeerResult is peer_mod.PeerResult)))\n"
    )
    assert out.strip() == "(False, True, True, True, True)", out


def test_from_import_still_works() -> None:
    """``from photontcp.optical import run_peer`` 가 종전대로 동작한다.

    ``examples/optical_link.py`` 가 정확히 이 형태를 쓴다.
    """
    out = _run_ok(
        "from photontcp.optical import PeerResult, run_peer\n"
        "import photontcp.optical.peer as peer_mod\n"
        "print(repr((run_peer is peer_mod.run_peer,"
        " PeerResult is peer_mod.PeerResult)))\n"
    )
    assert out.strip() == "(True, True)"


def test_all_and_dir_expose_the_unchanged_public_surface() -> None:
    """``__all__`` 이 그대로이고 ``dir()`` 도 지연 이름을 노출한다."""
    out = _run_ok(
        "import photontcp.optical as opt\n"
        "expected = ['OpticalChannel', 'DisplaySink', 'CameraSource',"
        " 'MemoryDisplay', 'MemoryCamera', 'memory_device_pair',"
        " 'run_peer', 'PeerResult']\n"
        "missing = [n for n in expected if n not in opt.__all__]\n"
        "names = dir(opt)\n"
        "not_in_dir = [n for n in expected if n not in names]\n"
        "unresolvable = [n for n in opt.__all__ if not hasattr(opt, n)]\n"
        "print(repr((missing, not_in_dir, unresolvable)))\n"
    )
    assert out.strip() == "([], [], [])", out


def test_all_names_are_importable_with_star_import() -> None:
    """``from photontcp.optical import *`` 가 ``__all__`` 전부를 해석한다.

    PEP 562 ``__getattr__`` 이 star-import 경로에서도 도는지 확인한다 —
    ``__all__`` 에 이름만 있고 해석되지 않으면 star-import 가 ``AttributeError`` 다.
    """
    out = _run_ok(
        "ns = {}\n"
        "exec('from photontcp.optical import *', ns)\n"
        "print(repr(callable(ns['run_peer']) and ns['PeerResult'] is not None))\n"
    )
    assert out.strip() == "True"


def test_unknown_attribute_still_raises_attribute_error() -> None:
    """``__getattr__`` 훅이 미지의 이름을 삼키지 않는다."""
    out = _run_ok(
        "import photontcp.optical as opt\n"
        "try:\n"
        "    opt.definitely_not_here\n"
        "except AttributeError as exc:\n"
        "    print('AttributeError', 'definitely_not_here' in str(exc))\n"
        "else:\n"
        "    print('NO RAISE')\n"
    )
    assert out.strip() == "AttributeError True"


# --------------------------------------------------------------------------- #
# 3. cv2 가드 성질 (M8 완료 기준 8) 은 깨지지 않았다
# --------------------------------------------------------------------------- #


def test_cv2_guard_still_holds() -> None:
    """cv2 유무와 무관하게 ``Cv2Display``/``Cv2Camera`` 속성이 존재한다.

    cv2 가 있으면 클래스이고 ``__all__`` 에 실린다. 없으면 ``None`` 이고
    ``__all__`` 에서 빠지되 ``import photontcp.optical`` 자체는 성공한다.
    지연 재수출이 이 가드를 건드리지 않았음을 확인한다.
    """
    out = _run_ok(
        "import photontcp.optical as opt\n"
        "has_attr = hasattr(opt, 'Cv2Display') and hasattr(opt, 'Cv2Camera')\n"
        "present = opt.Cv2Display is not None\n"
        "listed = 'Cv2Display' in opt.__all__ and 'Cv2Camera' in opt.__all__\n"
        "print(repr((has_attr, present == listed)))\n"
    )
    assert out.strip() == "(True, True)"


# --------------------------------------------------------------------------- #
# 4. 서브모듈 속성 접근 복원 (M13-T02, M12-review 사소 8 / 완료 기준 1-⑴)
# --------------------------------------------------------------------------- #


def test_submodule_attribute_access_is_restored_and_stays_lazy() -> None:
    """``o.peer`` 가 되살아났고 **그럼에도** 상위 계층은 끌려오지 않는다.

    M12-T03 의 지연 재수출은 ``run_peer``·``PeerResult`` 두 이름만 처리해, HEAD 에서
    ``from .peer import …`` 의 부작용으로 존재하던 ``photontcp.optical.peer``
    **속성 접근**을 조용히 없앴다(M12-review 사소 8). ``__getattr__`` 의 서브모듈
    폴백이 그것을 복원한다.

    **한 테스트가 둘을 함께 단언한다**(완료 기준 1): 복원이 지연 성질을 되돌리지
    않았음을 한 자리에서 증명해야 하기 때문이다 — 폴백을 eager import 로 바꾸면
    ⑵ 가, 폴백을 지우면 ⑴ 이 붉는다.
    """
    out = _run_ok(
        "import sys\n"
        "import photontcp.optical as opt\n"
        # ⑵ 패키지 import 직후: 상위 계층도 peer 도 아직 없다.
        "leaked_before = [n for n in ('photontcp.app', 'photontcp.session',"
        " 'photontcp.optical.peer') if n in sys.modules]\n"
        # ⑴ 속성 접근이 AttributeError 를 내지 않고 서브모듈을 돌려준다.
        "mod = opt.peer\n"
        "import photontcp.optical.peer as peer_mod\n"
        "same = mod is peer_mod\n"
        "cached = getattr(opt, 'peer', None) is peer_mod\n"
        "print(repr((leaked_before, same, cached)))\n"
    )
    assert out.strip() == "([], True, True)", out


def test_submodule_fallback_does_not_import_arbitrary_names() -> None:
    """폴백은 **실제 서브모듈일 때만** 돈다 — 아무 이름이나 import 하지 않는다.

    ``photontcp.optical.<name>`` 이 존재하지 않으면 종전대로 ``AttributeError`` 이고,
    ``sys.modules`` 에 무언가가 새로 생기지도 않는다.
    """
    out = _run_ok(
        "import sys\n"
        "import photontcp.optical as opt\n"
        "before = set(sys.modules)\n"
        "try:\n"
        "    opt.json\n"  # 최상위에 실재하지만 이 패키지의 서브모듈은 아니다
        "except AttributeError:\n"
        "    raised_json = True\n"
        "else:\n"
        "    raised_json = False\n"
        "try:\n"
        "    opt.definitely_not_here\n"
        "except AttributeError:\n"
        "    raised_unknown = True\n"
        "else:\n"
        "    raised_unknown = False\n"
        "new = sorted(n for n in set(sys.modules) - before"
        " if n.startswith('photontcp'))\n"
        "print(repr((raised_json, raised_unknown, new)))\n"
    )
    assert out.strip() == "(True, True, [])", out


def test_all_optical_submodules_are_reachable_as_attributes() -> None:
    """``channel``·``devices``·``peer`` 등 서브모듈 전부가 속성으로 잡힌다.

    사소 8 이 없앤 것은 ``peer`` 하나였지만, 폴백은 이름을 하나만 특별대우하지
    않는다 — 패키지의 모든 서브모듈에 대해 종전 형태가 성립한다.
    """
    out = _run_ok(
        "import pkgutil\n"
        "import photontcp.optical as opt\n"
        "names = sorted(m.name for m in pkgutil.iter_modules(opt.__path__))\n"
        "missing = []\n"
        "for n in names:\n"
        "    try:\n"
        "        getattr(opt, n)\n"
        "    except AttributeError:\n"
        "        missing.append(n)\n"
        "    except ImportError:\n"
        "        pass\n"  # cv2 부재 머신의 cv2_devices — 가드 성질은 별도 테스트
        "print(repr((bool(names), missing)))\n"
    )
    assert out.strip() == "(True, [])", out
