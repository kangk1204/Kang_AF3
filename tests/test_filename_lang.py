#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_filename_lang.py - 출력 파일 이름 규약과 matplotlib 부재 처리를 검사한다.

왜 이 파일이 따로 있는가
    두 트랙이 같은 파일을 각각 고쳐서 병합했다. 타깃명정규화 트랙의 성과는
    tests/test_naming.py 가 지킨다. 이 파일은 초보사용성 트랙의 성과를 지킨다.
    병합 때 한쪽이 조용히 지워지는 것을 막으려면 각 성과에 검사가 붙어 있어야 한다.

무엇을 검사하는가
    1. -o 를 생략하면 ASCII 기본 이름이 나온다 (af3_summary.csv / visualize_table.csv
       / confidence_overview.png / viewer_*.pml,cxc)
    2. --filename-lang ko 를 주면 옛 한글 이름이 나오고 내용은 바이트까지 같다
    3. af3_collect.py 의 --lang 은 --filename-lang 의 별칭이다
       (af3_visualize.py 의 --lang 은 '그림 안 라벨 언어' 라는 다른 뜻이므로
        여기서 별칭이 아니어야 한다 - 그것도 검사한다)
    4. -o 로 직접 준 경로는 그대로 쓰이고 기본값 변경 알림이 뜨지 않는다
    5. matplotlib 을 못 써도 그림 아닌 산출물(표/뷰어 스크립트)은 남고 종료코드가 0 이다
       - matplotlib 이 아예 없는 경우
       - matplotlib 은 있지만 pyplot 이 깨진 경우 (예전에 산출물을 전부 잃던 경로)
    6. A안의 파일 이름 규약이 B안의 타깃명 위에서 돈다
       (타깃별 그림 이름이 폴더명이 아니라 산출물 stem 기준 타깃명이다)

돌리는 법
    python3 tests/test_filename_lang.py          # 저장소 루트에서
    성공하면 마지막 줄이 '전체 통과' 이고 종료 코드가 0 이다.
    AF3 실물도 도커도 필요 없다. 표준 라이브러리만 쓴다.
"""

import argparse
import csv
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SCRIPTS = ROOT / "scripts"

sys.path.insert(0, str(HERE))
import make_naming_fixture as fixture   # noqa: E402

_fail = []
_pass = 0
_section = None
_by_section = []


def section(num, name):
    global _section
    _section = [num, name, 0, 0]
    _by_section.append(_section)
    print("\n[%s] %s" % (num, name))


def check(cond, what, detail=""):
    global _pass
    if cond:
        _pass += 1
        if _section:
            _section[2] += 1
        print("  통과   %s" % what)
    else:
        _fail.append((what, detail))
        if _section:
            _section[3] += 1
        print("  실패   %s%s" % (what, ("  <- " + detail) if detail else ""))
    return bool(cond)


def run_script(name, args, cwd=None, extra_pypath=None):
    """스크립트를 별도 프로세스로 돌린다. (종료코드, stdout, stderr)."""
    env = dict(os.environ)
    if extra_pypath:
        env["PYTHONPATH"] = extra_pypath + os.pathsep + env.get("PYTHONPATH", "")
    p = subprocess.run([sys.executable, str(SCRIPTS / name)] + args,
                       capture_output=True, text=True, cwd=cwd, env=env,
                       timeout=600)
    return p.returncode, p.stdout, p.stderr


def make_import_blocker(tmp, subdir, blocked, message):
    """지정한 모듈의 import 를 막는 sitecustomize.py 를 만들고 그 폴더를 준다.

    별도 venv 를 만들지 않고 matplotlib 부재/파손을 재현하는 방법이다.
    sys.meta_path 앞에 finder 를 끼워 해당 이름만 ImportError 로 만든다.
    """
    d = tmp / subdir
    d.mkdir(parents=True, exist_ok=True)
    (d / "sitecustomize.py").write_text(
        "import importlib.util\n"
        "import sys\n"
        "_BLOCKED = %r\n"
        "_MSG = %r\n"
        "class _Block:\n"
        "    def _blocked(self, name):\n"
        "        for b in _BLOCKED:\n"
        "            if name == b or (b.endswith('.') and name.startswith(b)):\n"
        "                return True\n"
        "        return False\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if self._blocked(name):\n"
        "            return importlib.util.spec_from_loader(name, self)\n"
        "        return None\n"
        "    def create_module(self, spec):\n"
        "        return None\n"
        "    def exec_module(self, module):\n"
        "        raise ImportError(_MSG)\n"
        "    def find_module(self, name, path=None):\n"
        "        return self if self._blocked(name) else None\n"
        "    def load_module(self, name):\n"
        "        raise ImportError(_MSG)\n"
        "sys.meta_path.insert(0, _Block())\n" % (blocked, message),
        encoding="utf-8")
    return str(d)


# ===========================================================================
# 1. af3_collect.py 의 기본 파일 이름
# ===========================================================================
def test_collect_names(root, tmp):
    section("1", "af3_collect.py - 기본 파일 이름과 호환 옵션")

    # (1) -o 없이 = ASCII 기본 이름
    d1 = tmp / "c_en"
    d1.mkdir()
    rc, so, se = run_script("af3_collect.py", [str(root)], cwd=str(d1))
    if not check(rc == 0, "기본 실행이 정상 종료", se[-400:]):
        return
    check((d1 / "af3_summary.csv").is_file(),
          "-o 를 생략하면 af3_summary.csv 가 생긴다",
          str(sorted(p.name for p in d1.iterdir())))
    check(not (d1 / "af3_결과요약.csv").exists(),
          "옛 한글 기본 이름은 생기지 않는다")
    check("af3_summary.csv" in so, "화면 요약에 새 파일 이름이 나온다")
    check("알림" in so and "af3_결과요약.csv" in so,
          "기본값이 바뀐 사실을 실행 끝에 알린다 (옛 이름도 함께 적는다)")

    # (2) --filename-lang ko = 옛 이름
    d2 = tmp / "c_ko"
    d2.mkdir()
    rc, so2, se2 = run_script("af3_collect.py",
                              [str(root), "--filename-lang", "ko"], cwd=str(d2))
    check(rc == 0, "--filename-lang ko 가 정상 종료", se2[-300:])
    check((d2 / "af3_결과요약.csv").is_file(),
          "--filename-lang ko 면 af3_결과요약.csv 가 생긴다",
          str(sorted(p.name for p in d2.iterdir())))
    check(not (d2 / "af3_summary.csv").exists(),
          "ko 모드에서 ASCII 이름은 생기지 않는다")
    check("알림" not in so2,
          "ko 를 직접 지정했으면 기본값 변경 알림을 띄우지 않는다")

    # (3) --lang 은 --filename-lang 의 별칭이다 (이 스크립트는 그릴 것이 없다)
    d3 = tmp / "c_alias"
    d3.mkdir()
    rc, _so3, se3 = run_script("af3_collect.py",
                               [str(root), "--lang", "ko"], cwd=str(d3))
    check(rc == 0, "--lang ko 가 받아들여진다 (별칭)", se3[-300:])
    check((d3 / "af3_결과요약.csv").is_file(),
          "--lang ko 가 --filename-lang ko 와 같게 동작한다",
          str(sorted(p.name for p in d3.iterdir())))

    # (4) -o 로 직접 준 경로는 그대로 쓰고 알림도 없다
    d4 = tmp / "c_explicit"
    d4.mkdir()
    rc, so4, se4 = run_script("af3_collect.py",
                              [str(root), "-o", "af3_결과요약.csv"], cwd=str(d4))
    check(rc == 0, "-o 로 옛 이름을 직접 주면 정상 종료", se4[-300:])
    check((d4 / "af3_결과요약.csv").is_file(),
          "-o 로 준 경로가 그대로 쓰인다")
    check("알림" not in so4,
          "-o 로 직접 준 경로에는 기본값 변경 알림을 띄우지 않는다")

    # (5) 이름만 다르고 내용은 같아야 한다
    a = (d1 / "af3_summary.csv").read_bytes()
    b = (d2 / "af3_결과요약.csv").read_bytes()
    check(a == b, "en/ko 두 이름의 CSV 내용이 바이트까지 같다")

    # (6) 열 이름은 한글로 유지한다 (이미 참조하는 엑셀 시트를 깨지 않기 위해서다)
    head = (d1 / "af3_summary.csv").read_text(encoding="utf-8-sig").splitlines()[0]
    for col in ("조건", "타깃", "등급", "pLDDT평균"):
        check(col in head, "CSV 열 이름 '%s' 이 한글로 유지된다" % col, head[:120])

    # (7) --help 에 기본값 변경이 적혀 있다
    rc, soh, _seh = run_script("af3_collect.py", ["--help"])
    check(rc == 0 and "af3_summary.csv" in soh,
          "--help 에 새 기본 이름이 적혀 있다")
    check("af3_결과요약.csv" in soh,
          "--help 에 옛 이름으로 돌아가는 방법이 적혀 있다")


# ===========================================================================
# 2. af3_visualize.py 의 기본 파일 이름
# ===========================================================================
VIS_EN = ("visualize_table.csv", "viewer_pymol_plddt.pml",
          "viewer_chimerax_plddt.cxc")
VIS_KO = ("af3_시각화표.csv", "pymol_색칠.pml", "chimerax_색칠.cxc")


def test_visualize_names(root, tmp):
    section("2", "af3_visualize.py - 기본 파일 이름과 호환 옵션")

    vo = tmp / "v_en"
    rc, so, se = run_script("af3_visualize.py",
                            [str(root), "-o", str(vo), "--no-plot"])
    if not check(rc == 0, "기본 실행이 정상 종료", se[-400:]):
        return
    for n in VIS_EN:
        check((vo / n).is_file(), "기본으로 %s 가 생긴다" % n,
              str(sorted(p.name for p in vo.iterdir())))
    for n in VIS_KO:
        check(not (vo / n).exists(), "옛 이름 %s 는 생기지 않는다" % n)
    check("알림" in so, "기본값이 바뀐 사실을 실행 끝에 알린다")

    with (vo / "visualize_table.csv").open(encoding="utf-8-sig", newline="") as fh:
        visualize_rows = list(csv.DictReader(fh))
    with (tmp / "c_en" / "af3_summary.csv").open(
        encoding="utf-8-sig", newline=""
    ) as fh:
        collect_rows = {row["타깃"]: row for row in csv.DictReader(fh)}
    check(
        visualize_rows
        and {"mean_atom_plddt", "mean_residue_plddt"}.issubset(visualize_rows[0]),
        "시각화 표가 원자 평균과 잔기 평균 pLDDT를 구분한다",
        str(sorted(visualize_rows[0]) if visualize_rows else []),
    )
    if visualize_rows and "mean_atom_plddt" in visualize_rows[0]:
        max_diff = max(
            (
                abs(
                    float(row["mean_atom_plddt"])
                    - float(collect_rows[row["name"]]["pLDDT평균"])
                )
                for row in visualize_rows
                if row["name"] in collect_rows
            ),
            default=float("inf"),
        )
    else:
        max_diff = float("inf")
    check(
        max_diff <= 0.0001,
        "시각화 표의 원자 평균 pLDDT가 집계 CSV와 일치한다",
        "최대 차 %.6f" % max_diff,
    )

    vk = tmp / "v_ko"
    rc, sok, sek = run_script("af3_visualize.py",
                              [str(root), "-o", str(vk), "--no-plot",
                               "--filename-lang", "ko"])
    check(rc == 0, "--filename-lang ko 가 정상 종료", sek[-300:])
    for n in VIS_KO:
        check((vk / n).is_file(), "--filename-lang ko 면 %s 가 생긴다" % n,
              str(sorted(p.name for p in vk.iterdir())))
    check("알림" not in sok, "ko 를 직접 지정했으면 알림을 띄우지 않는다")

    # 내용 동일성. 표는 바이트까지, 뷰어 스크립트는 자기 이름을 적는 줄만 다르다.
    check((vo / "visualize_table.csv").read_bytes()
          == (vk / "af3_시각화표.csv").read_bytes(),
          "en/ko 두 이름의 표 내용이 바이트까지 같다")
    for en, ko in ((VIS_EN[1], VIS_KO[1]), (VIS_EN[2], VIS_KO[2])):
        a = (vo / en).read_text(encoding="utf-8").replace(en, "X")
        b = (vk / ko).read_text(encoding="utf-8").replace(ko, "X")
        check(a == b, "%s 와 %s 는 스크립트 이름 줄 외에는 같다" % (en, ko))

    # --lang 은 그림 안 라벨 언어다. 파일 이름을 바꾸면 안 된다.
    vl = tmp / "v_lang_en"
    rc, _sol, sel = run_script("af3_visualize.py",
                               [str(root), "-o", str(vl), "--no-plot",
                                "--lang", "en"])
    check(rc == 0, "--lang en 이 정상 종료", sel[-300:])
    check((vl / "visualize_table.csv").is_file(),
          "--lang 은 파일 이름에 영향을 주지 않는다 (그림 안 라벨 언어다)",
          str(sorted(p.name for p in vl.iterdir())))

    # 그림 파일 이름 (matplotlib 이 있을 때만)
    try:
        import matplotlib  # noqa: F401
        have_mpl = True
    except Exception:
        have_mpl = False
    if not have_mpl:
        print("  건너뜀 matplotlib 이 없어 그림 파일명 검사 4건을 못 했다")
        return
    vp = tmp / "v_plot"
    rc, _sop, sep = run_script("af3_visualize.py", [str(root), "-o", str(vp)])
    if not check(rc == 0, "그림까지 만드는 실행이 정상 종료", sep[-400:]):
        return
    check((vp / "confidence_overview.png").is_file(),
          "요약 그림 기본 이름이 confidence_overview.png 다",
          str(sorted(p.name for p in vp.iterdir()))[:300])
    check(not (vp / "af3_요약.png").exists(), "옛 이름 af3_요약.png 는 생기지 않는다")

    vpk = tmp / "v_plot_ko"
    rc, _s, _e = run_script("af3_visualize.py",
                            [str(root), "-o", str(vpk), "--summary-only",
                             "--filename-lang", "ko"])
    check(rc == 0 and (vpk / "af3_요약.png").is_file(),
          "--filename-lang ko 면 요약 그림이 af3_요약.png 다",
          str(sorted(p.name for p in vpk.iterdir()))[:300])

    # A안의 이름 규약이 B안의 타깃명 위에서 도는가.
    # 타깃별 그림 이름은 폴더명이 아니라 산출물 stem 기준 타깃명이어야 한다.
    pngs = sorted(p.name for p in vp.glob("*_plddt.png"))
    check("VHH_009_plddt.png" in pngs,
          "타깃별 그림 이름이 stem 기준 타깃명으로 지어진다 (폴더는 zzz_folder_9)",
          str(pngs))
    check(not any(n.startswith("zzz_folder_9") or "20260820" in n for n in pngs),
          "폴더명(타임스탬프 포함)으로 지어진 그림 파일이 없다", str(pngs))


# ===========================================================================
# 3. matplotlib 을 못 쓸 때
# ===========================================================================
def test_no_matplotlib(root, tmp):
    section("3", "matplotlib 을 못 써도 그림 아닌 산출물은 남는가")

    cases = [
        ("mpl 아예 없음", "block_all", ["matplotlib", "matplotlib."],
         "No module named 'matplotlib' (테스트용 차단)"),
        # matplotlib 은 import 되지만 pyplot 이 깨진 경우.
        # 예전에는 그리는 시점에 죽어서 표와 뷰어 스크립트까지 잃었다.
        ("pyplot 만 깨짐", "block_pyplot", ["matplotlib.pyplot"],
         "libfreetype.so.6: cannot open shared object file (테스트용 재현)"),
    ]
    for label, sub, blocked, msg in cases:
        pypath = make_import_blocker(tmp, sub, blocked, msg)
        vo = tmp / ("v_" + sub)
        rc, so, se = run_script("af3_visualize.py", [str(root), "-o", str(vo)],
                                extra_pypath=pypath)
        check(rc == 0, "[%s] 종료코드가 0 이다 (죽지 않는다)" % label,
              "rc=%s %s" % (rc, se[-300:]))
        for n in VIS_EN:
            check((vo / n).is_file(),
                  "[%s] %s 가 남는다" % (label, n),
                  str(sorted(p.name for p in vo.iterdir())) if vo.is_dir()
                  else "(폴더가 없다)")
        check(not list(vo.glob("*.png")) if vo.is_dir() else False,
              "[%s] 그림은 만들지 않는다" % label)
        # 메시지가 초보자에게 무엇을 해야 하는지 알려주는가
        check("matplotlib" in se and "pip install matplotlib" in se,
              "[%s] 설치 방법을 알려준다" % label, se[-200:])
        check("--no-plot" in se,
              "[%s] 그림이 필요 없을 때의 방법을 알려준다" % label)
        check(any(n in se for n in VIS_EN),
              "[%s] 그래도 만들어진 파일 이름을 알려준다" % label)
        check("이유:" in se,
              "[%s] 왜 못 쓰는지 원인을 그대로 보여준다" % label)

    # 경고 메시지의 파일 이름은 실제로 만드는 이름과 같아야 한다
    pypath = make_import_blocker(tmp, "block_all", ["matplotlib", "matplotlib."],
                                 "No module named 'matplotlib' (테스트용 차단)")
    vo = tmp / "v_nompl_ko"
    rc, _so, se = run_script("af3_visualize.py",
                             [str(root), "-o", str(vo), "--filename-lang", "ko"],
                             extra_pypath=pypath)
    check(rc == 0, "[ko] matplotlib 없이도 정상 종료", se[-300:])
    check("af3_시각화표.csv" in se,
          "경고 메시지가 실제로 만드는 이름(ko)을 적는다",
          "경고에 ASCII 이름이 박혀 있으면 사용자가 파일을 못 찾는다")
    check((vo / "af3_시각화표.csv").is_file(),
          "[ko] matplotlib 없이도 옛 이름 표가 만들어진다")


# ===========================================================================
# 4. af3run.sh 와의 인터페이스
# ===========================================================================
def test_af3run_interface(root, tmp):
    section("4", "af3run.sh 가 기대하는 인터페이스와 맞는가")

    sh = SCRIPTS / "af3run.sh"
    if not check(sh.is_file(), "af3run.sh 가 있다"):
        return
    text = sh.read_text(encoding="utf-8")
    check("AF3RUN_FILENAME_LANG" in text,
          "af3run.sh 가 AF3RUN_FILENAME_LANG 환경변수를 읽는다")
    check("_summary.csv" in text, "af3run.sh 의 기본 CSV 이름이 ASCII 다")
    check("_결과요약.csv" in text, "af3run.sh 가 옛 이름 경로도 남겨 뒀다")
    # af3run.sh 는 -o 로 경로를 직접 주므로 collect 의 기본값에 의존하지 않는다.
    check('-o "$CSV"' in text or "-o \"$CSV\"" in text,
          "af3run.sh 는 -o 로 경로를 직접 준다 (기본값 변경에 영향받지 않는다)")

    # 실제로 돌려 본다. af3run.sh 는 ./<NAME>_out 규약을 쓴다.
    work = tmp / "run_sh"
    work.mkdir()
    shutil.copytree(str(root), str(work / "demo_out"))
    for lang, want in (("en", "demo_summary.csv"), ("ko", "demo_결과요약.csv")):
        env = dict(os.environ)
        env["AF3RUN_FILENAME_LANG"] = lang
        p = subprocess.run(["bash", str(sh), "demo", "collect"],
                           capture_output=True, text=True, cwd=str(work),
                           env=env, timeout=600)
        check(p.returncode == 0, "[AF3RUN_FILENAME_LANG=%s] af3run.sh collect 정상 종료"
              % lang, p.stdout[-400:] + p.stderr[-400:])
        check((work / want).is_file(),
              "[AF3RUN_FILENAME_LANG=%s] %s 가 생긴다" % (lang, want),
              str(sorted(p.name for p in work.iterdir())))


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="출력 파일 이름 규약과 matplotlib 부재 처리를 검사한다")
    ap.add_argument("--keep", action="store_true", help="임시 폴더를 남긴다")
    args = ap.parse_args(argv)

    tmpdir = Path(tempfile.mkdtemp(prefix="af3_fnlang_test_"))
    root = tmpdir / "af3out"
    work = tmpdir / "work"
    work.mkdir(parents=True, exist_ok=True)
    try:
        fixture.build(root)
        print("=" * 72)
        print("출력 파일 이름 / matplotlib 부재 처리 검사")
        print("=" * 72)
        print("가짜 AF3 출력: %s" % root)

        test_collect_names(root, work)
        test_visualize_names(root, work)
        test_no_matplotlib(root, work)
        test_af3run_interface(root, work)

        print("")
        print("=" * 72)
        print("절별 건수")
        for num, name, ok, bad in _by_section:
            tag = "통과 %3d" % ok if not bad else "통과 %3d 실패 %d" % (ok, bad)
            print("  [%s] %-46s %s" % (num, name, tag))
        print("%54s 합계 %d" % ("", _pass + len(_fail)))
        print("-" * 72)
        if _fail:
            print("실패 %d건" % len(_fail))
            for what, detail in _fail:
                print("  - %s%s" % (what, ("  <- " + detail) if detail else ""))
            print("=" * 72)
            return 1
        print("전체 통과 (%d건)" % _pass)
        print("=" * 72)
        return 0
    finally:
        if args.keep:
            print("임시 폴더를 남겼다: %s" % tmpdir)
        else:
            shutil.rmtree(str(tmpdir), ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
