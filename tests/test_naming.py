#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_naming.py - 타깃명 정규화가 실제로 동작하는지 검사한다.

무엇을 검사하는가
    1. af3_collect.py 의 집계표 '타깃' 열이 실제 타깃명인지 (폴더명이 아닌지)
    2. 같은 타깃이 여러 폴더에 있을 때 정책대로 처리되는지
       (기본: 최신 1건 / --all-runs: 전부)
    3. --top / --top-list 가 중복 때문에 틀어지지 않는지
    4. af3_visualize.py 가 같은 타깃명을 쓰는지 (두 도구의 이름이 어긋나면 대조 불가)
    5. 격리 폴더(.af3_incomplete) / staging(.af3_pending_*) / lock 이 제외되는지
       - 점으로 시작하는 항목을 건너뛰는 것이 우연이 아니라 의도임을 못박는다
    6. af3_batch.py 의 find_result_dirs / outdir_is_complete 가 정식 기준을 쓰는지
    7. 세 스크립트의 정본 블록 사본이 같은 답을 내는지 (한쪽만 고치는 것을 막는다)

돌리는 법
    python3 tests/test_naming.py                  # 저장소 루트에서
    python3 tests/test_naming.py --keep           # 임시 폴더를 남긴다 (사람이 확인)

    성공하면 마지막 줄이 '전체 통과' 이고 종료 코드가 0 이다.
    AF3 실물도 도커도 필요 없다. 표준 라이브러리만 쓴다.
"""

import argparse
import ast
import csv
import importlib.util
import io
import json
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

# af3_visualize.py 가 만드는 폴더 전체 산출물의 기본 이름.
# 2026-04 에 초보사용성 트랙이 기본값을 한글에서 ASCII 로 바꿨다 (저장소에 이미
# 커밋된 예시 파일 이름과 맞추기 위해서다). 옛 이름은 --filename-lang ko 로 살아 있다.
# 이 파일이 검사하는 것은 '표 안의 name 열이 폴더명이 아니라 타깃명인가' 이므로
# 파일 이름이 무엇이냐는 검사 대상이 아니다. 기본 이름만 따라간다.
# 파일 이름 자체의 검사는 tests/test_filename_lang.py 가 한다.
VIS_TABLE = "visualize_table.csv"
VIS_PYMOL = "viewer_pymol_plddt.pml"


_fail = []
_pass = 0
_section = None
_by_section = []       # [(절 이름, 통과 수, 실패 수)] - 등장 순서를 지킨다


def section(num, name):
    """절을 시작한다. 절별 건수를 스크립트가 직접 세게 하려고 둔다.

    docs/naming_fix_notes.md 에 절별 건수 표가 있는데, 사람이 손으로 옮겨 적다
    합계와 어긋난 적이 있다. 이제 스크립트가 표에 넣을 숫자를 그대로 출력한다.
    """
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


def load_module(path, name):
    """스크립트를 모듈로 불러온다.

    sys.modules 에 먼저 등록해야 한다. run_af3_batch_improved.py 는 @dataclass 를
    쓰는데, dataclasses 가 클래스의 __module__ 을 sys.modules 에서 되찾으므로
    등록 전에 exec_module 하면 AttributeError 로 죽는다.
    """
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def run_script(script, argv):
    """스크립트를 별도 프로세스로 돌린다. (종료코드, stdout, stderr)"""
    r = subprocess.run([sys.executable, str(SCRIPTS / script)] + argv,
                       capture_output=True, text=True)
    return r.returncode, r.stdout, r.stderr


def read_csv(path):
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


# ===========================================================================
# 1. af3_collect.py
# ===========================================================================
def test_collect(root, tmp):
    section("1", "af3_collect.py - 타깃명과 중복 정책")
    out = tmp / "collect.csv"
    rc, so, se = run_script("af3_collect.py",
                            [str(root), "--no-msa-depth", "-o", str(out)])
    if not check(rc == 0, "정상 종료", se[-300:]):
        return
    rows = read_csv(out)
    got = sorted(r["타깃"] for r in rows)
    want = sorted(fixture.EXPECTED)
    check(got == want, "타깃 열이 실제 타깃명과 일치",
          "얻은 것 %s / 기대 %s" % (got, want))

    for bad in fixture.FORBIDDEN:
        check(bad not in got, "'%s' 는 타깃으로 집계되지 않는다" % bad)

    # 타임스탬프 폴더의 타깃명이 접미사 없는 이름인지
    m = {r["타깃"]: r for r in rows}
    if "VHH_004" in m:
        check(m["VHH_004"]["폴더명"] == "VHH_004_20260820_101010",
              "재실행 폴더의 타깃명은 VHH_004, 폴더명 열에 실제 폴더가 남는다",
              m["VHH_004"]["폴더명"])
    # 폴더명과 stem 이 완전히 다른 경우
    if "VHH_009" in m:
        check(m["VHH_009"]["폴더명"] == "zzz_folder_9",
              "폴더명과 stem 이 다르면 stem 을 타깃명으로 쓴다",
              m["VHH_009"]["폴더명"])

    # 중복 정책: 기본은 최신 1건
    v5 = [r for r in rows if r["타깃"] == "VHH_005"]
    check(len(v5) == 1, "같은 타깃은 기본으로 한 줄만 (최신 1건)",
          "%d줄" % len(v5))
    if v5:
        check(v5[0]["폴더명"] == "VHH_005_20260820_120000",
              "최신 폴더가 선택된다 (타임스탬프 접미사 기준)", v5[0]["폴더명"])
        check(abs(float(v5[0]["ranking_score"]) - 0.88) < 1e-9,
              "선택된 행의 값이 최신 실행의 값이다", v5[0]["ranking_score"])
        check(v5[0]["실행수"] == "2" and v5[0]["중복정책"],
              "몇 번 돌았는지와 어떤 정책을 썼는지 열에 남는다",
              "실행수=%s 정책=%s" % (v5[0]["실행수"], v5[0]["중복정책"]))

    # 기대값의 대표 ranking_score 대조
    for tgt, (folder, rank) in sorted(fixture.EXPECTED.items()):
        if tgt not in m:
            continue
        check(abs(float(m[tgt]["ranking_score"]) - rank) < 1e-9,
              "%s 의 대표값이 %s 폴더의 것이다" % (tgt, folder),
              m[tgt]["ranking_score"])

    # 미완료는 타깃명으로 보고된다 (폴더명이 아니라)
    check("VHH_007" in so or "VHH_007" in se,
          "추론 중 끊긴 VHH_007 은 미완료로 보고된다")

    # --all-runs
    out2 = tmp / "collect_all.csv"
    rc, so2, _ = run_script("af3_collect.py",
                            [str(root), "--no-msa-depth", "--all-runs",
                             "-o", str(out2)])
    rows2 = read_csv(out2) if rc == 0 else []
    v5all = [r for r in rows2 if r["타깃"] == "VHH_005"]
    check(len(v5all) == 2, "--all-runs 면 같은 타깃의 두 실행이 모두 나온다",
          "%d줄" % len(v5all))
    check(all(r["타깃"] == "VHH_005" for r in v5all),
          "--all-runs 에서도 타깃명은 같다 (구분은 폴더명/실행시각 열로)")

    # --top: 중복 때문에 순위가 틀어지지 않아야 한다
    tl = tmp / "top.txt"
    rc, so3, _ = run_script("af3_collect.py",
                            [str(root), "--no-msa-depth", "--top", "3",
                             "--top-list", str(tl), "-o", str(tmp / "t.csv")])
    names = tl.read_text(encoding="utf-8").split() if tl.exists() else []
    check(names == ["VHH_009", "VHH_005", "VHH_001"],
          "--top-list 에 실제 타깃명이 순서대로 들어간다", str(names))
    check(all(n not in fixture.FORBIDDEN for n in names),
          "--top-list 에 폴더명이 섞이지 않는다", str(names))

    # --all-runs + --top: 같은 타깃이 두 번 뽑히면 안 된다
    tl2 = tmp / "top_all.txt"
    rc, so4, se4 = run_script("af3_collect.py",
                              [str(root), "--no-msa-depth", "--all-runs",
                               "--top", "8", "--top-list", str(tl2),
                               "-o", str(tmp / "t2.csv")])
    names2 = tl2.read_text(encoding="utf-8").split() if tl2.exists() else []
    check(len(names2) == len(set(names2)),
          "--all-runs 로 중복 행이 있어도 상위 목록에 같은 타깃이 두 번 나오지 않는다",
          str(names2))


# ===========================================================================
# 2. af3_visualize.py
# ===========================================================================
def test_visualize(root, tmp):
    section("2", "af3_visualize.py - 타깃명이 af3_collect.py 와 같은가")
    vo = tmp / "vis"
    rc, so, se = run_script("af3_visualize.py",
                            [str(root), "-o", str(vo), "--no-plot"])
    if not check(rc == 0, "정상 종료", se[-400:]):
        return
    rows = read_csv(vo / VIS_TABLE)
    got = sorted(r["name"] for r in rows)
    want = sorted(fixture.EXPECTED)
    check(got == want, "시각화표의 name 열이 실제 타깃명과 일치",
          "얻은 것 %s" % got)
    for bad in fixture.FORBIDDEN:
        check(bad not in got, "시각화표에 '%s' 가 없다" % bad)

    # 뷰어 스크립트의 객체 이름도 타깃명이어야 한다
    pml = (vo / VIS_PYMOL).read_text(encoding="utf-8")
    # 보안상 PyMOL command 문자열 대신 Python API + repr 인자를 쓴다.
    calls = []
    for line in pml.splitlines():
        if not line.startswith("cmd.load("):
            continue
        call = ast.parse(line, mode="eval").body
        calls.append((ast.literal_eval(call.args[0]), ast.literal_eval(call.args[1])))
    objs = sorted(obj for _path, obj in calls)
    check(all(o not in fixture.FORBIDDEN for o in objs),
          "PyMOL 객체 이름에 폴더명(타임스탬프 포함)이 쓰이지 않는다", str(objs))
    check(objs == sorted(fixture.EXPECTED),
          "PyMOL 객체 이름이 실제 타깃명 전체와 일치", str(objs))
    check(any(obj == "VHH_004" for _path, obj in calls),
          "재실행 폴더의 구조가 VHH_004 로 로드된다")
    check(any(obj == "VHH_009" and "zzz_folder_9/VHH_009_model.cif" in path
              for path, obj in calls),
          "폴더명이 달라도 객체 이름은 타깃명, 경로는 실제 폴더")

    # 그림 파일 이름.
    # af3_visualize.py 는 matplotlib 이 없어도 경고만 하고 종료코드 0 으로 끝난다
    # (스크립트와 표는 만들기 때문이다). 그래서 종료코드로는 그림을 그렸는지 알 수 없다.
    # matplotlib 을 여기서 직접 import 해 보고, 없으면 검사를 건너뛴다.
    try:
        import matplotlib  # noqa: F401
        have_mpl = True
    except ImportError:
        have_mpl = False

    if not have_mpl:
        print("  건너뜀 matplotlib 이 없어 그림 파일명 검사 2건을 못 했다 "
              "(그림 외의 검사는 모두 돌았다)")
    else:
        vo2 = tmp / "vis_plot"
        rc, so2, se2 = run_script("af3_visualize.py",
                                  [str(root), "-o", str(vo2)])
        if check(rc == 0, "그림까지 만드는 실행이 정상 종료", se2[-300:]):
            pngs = sorted(p.name for p in vo2.glob("*_plddt.png"))
            check("VHH_004_plddt.png" in pngs,
                  "그림 파일 이름이 타깃명으로 지어진다", str(pngs))
            check(not any(n.startswith("zzz_folder_9") for n in pngs),
                  "폴더명으로 지어진 그림 파일이 없다", str(pngs))

    # --all-runs 는 실행 시각으로 구분
    vo3 = tmp / "vis_all"
    rc, _so, _se = run_script("af3_visualize.py",
                              [str(root), "-o", str(vo3), "--no-plot",
                               "--all-runs"])
    if rc == 0:
        names = sorted(r["name"] for r in read_csv(vo3 / VIS_TABLE))
        check("VHH_005" in names and "VHH_005__20260820_120000" in names,
              "--all-runs 는 같은 타깃의 두 실행을 실행시각으로 구분한다", str(names))

    # --only 는 타깃명으로 고른다
    vo4 = tmp / "vis_only"
    rc, _so, se4 = run_script("af3_visualize.py",
                              [str(root), "-o", str(vo4), "--no-plot",
                               "--only", "VHH_004"])
    if check(rc == 0, "--only 에 타깃명(VHH_004)을 주면 찾는다", se4[-300:]):
        names = [r["name"] for r in read_csv(vo4 / VIS_TABLE)]
        check(names == ["VHH_004"], "--only 타깃명으로 정확히 1건", str(names))

    # --only 에 폴더명을 줘도 받아준다 (예전 사용법 호환)
    vo5 = tmp / "vis_only_dir"
    rc, _so, se5 = run_script("af3_visualize.py",
                              [str(root), "-o", str(vo5), "--no-plot",
                               "--only", "VHH_004_20260820_101010"])
    check(rc == 0, "--only 에 폴더명을 줘도 동작한다 (예전 사용법 호환)",
          se5[-300:])


# ===========================================================================
# 3. 격리/staging/lock 제외 - 우연이 아니라 의도임을 못박는다
# ===========================================================================
def test_hidden_excluded(root, tmp):
    section("3", "격리 폴더 / staging / lock 이 제외되는가")
    col = load_module(SCRIPTS / "af3_collect.py", "af3c")
    vis = load_module(SCRIPTS / "af3_visualize.py", "af3v")
    bat = load_module(SCRIPTS / "af3_batch.py", "af3b")

    for mod, nm in ((col, "af3_collect"), (vis, "af3_visualize"),
                    (bat, "af3_batch")):
        check(mod.is_sidecar(".af3_incomplete"),
              "%s.is_sidecar('.af3_incomplete') == True" % nm)
        check(mod.is_sidecar(".af3_pending_1234"),
              "%s.is_sidecar('.af3_pending_1234') == True" % nm)
        check(mod.is_sidecar(".run_af3_batch.lock"),
              "%s.is_sidecar('.run_af3_batch.lock') == True" % nm)
        check(mod.is_sidecar("._VHH_099"),
              "%s.is_sidecar('._VHH_099') == True" % nm)
        check(not mod.is_sidecar("VHH_001"),
              "%s.is_sidecar('VHH_001') == False" % nm)

    # 격리 폴더 안의 결과는 그 자체로는 미완료여야 한다 (정식 3종 중 2종만 있다)
    q = root / ".af3_incomplete" / "VHH_003" / "20260820_090000"
    info = col.resolve_result_dir(q, mode="full")
    check(info["stem"] == "VHH_003",
          "격리된 결과의 stem 은 읽을 수 있다 (재시도 목록을 만들 수 있게)")
    check(info["complete"] is False,
          "격리된 결과는 정식 완료가 아니다", str(info))

    # 집계표/시각화표에 VHH_003, VHH_010 이 없음은 test_collect/test_visualize 가 본다.
    # 여기서는 CSV 를 한 번 더 직접 확인한다.
    out = tmp / "hidden.csv"
    rc, _so, _se = run_script("af3_collect.py",
                              [str(root), "--no-msa-depth", "-o", str(out)])
    if rc == 0:
        got = set(r["타깃"] for r in read_csv(out))
        check("VHH_003" not in got, "격리된 VHH_003 이 집계표에 없다")
        check("VHH_010" not in got, "staging 의 VHH_010 이 집계표에 없다")


# ===========================================================================
# 4. af3_batch.py 의 판정
# ===========================================================================
def test_batch(root, tmp):
    section("4", "af3_batch.py - find_result_dirs 와 완료 판정")
    bat = load_module(SCRIPTS / "af3_batch.py", "af3b2")

    # sanitise_name 이 실물 AF3 와 같아야 한다 (소문자화하지 않는다)
    check(bat.sanitise_name("VHH_001") == "VHH_001",
          "sanitise_name 이 대문자를 유지한다 (실물 AF3 와 같다)",
          bat.sanitise_name("VHH_001"))
    check(bat.sanitise_name("my target v1.2") == "my_target_v1.2",
          "공백은 _ 로, 마침표는 유지",
          bat.sanitise_name("my target v1.2"))
    check(bat.sanitise_name("a/b:c") == "abc",
          "허용되지 않는 문자는 제거된다", bat.sanitise_name("a/b:c"))

    # run_af3_batch_improved.py 와 같은 규칙인가
    imp = load_module(SCRIPTS / "run_af3_batch_improved.py", "af3imp")
    for nm in ("VHH_001", "my target v1.2", "a/b:c", "VHH-004.v2"):
        check(bat.sanitise_name(nm) == imp.sanitised_name(nm),
              "sanitise_name('%s') 가 run_af3_batch_improved 와 같다" % nm,
              "%s vs %s" % (bat.sanitise_name(nm), imp.sanitised_name(nm)))

    # find_result_dirs: 대문자 타깃을 찾는다
    d = [p.name for p in bat.find_result_dirs(root, "VHH_001")]
    check(d == ["VHH_001"], "find_result_dirs('VHH_001') 이 찾는다", str(d))
    d = [p.name for p in bat.find_result_dirs(root, "VHH_004")]
    check(d == ["VHH_004_20260820_101010"],
          "타임스탬프 폴더를 찾고, VHH_004_variantB 는 잡지 않는다", str(d))
    d = [p.name for p in bat.find_result_dirs(root, "VHH_004_variantB")]
    check(d == ["VHH_004_variantB"],
          "접두어가 겹치는 별개 타깃을 정확히 구분한다", str(d))
    d = sorted(p.name for p in bat.find_result_dirs(root, "VHH_005"))
    check(d == ["VHH_005", "VHH_005_20260820_120000"],
          "같은 타깃의 두 폴더를 모두 찾는다", str(d))
    d = [p.name for p in bat.find_result_dirs(root, "VHH_009")]
    check(d == ["zzz_folder_9"],
          "폴더명이 달라도 stem 으로 찾는다", str(d))

    # 완료 판정: 정식 3종
    check(bat.outdir_is_complete(root / "VHH_001") is True,
          "정식 3종이 있는 폴더는 완료")
    check(bat.outdir_is_complete(root / "VHH_007") is False,
          "_data.json 만 있는 폴더는 완료가 아니다 (추론 중 끊김)")
    check(bat.outdir_is_complete(root / "VHH_007", mode="data") is True,
          "--stage msa 기준(mode='data')으로는 VHH_007 이 완료다")
    check(bat.outdir_is_complete(root / "VHH_001", mode="data") is True,
          "정식 완료 폴더는 mode='data' 로도 완료다")

    # summary 파일만 있는 폴더
    only = tmp / "onlysumm"
    only.mkdir(parents=True, exist_ok=True)
    (only / "onlysumm_summary_confidences.json").write_text("{}",
                                                            encoding="utf-8")
    check(bat.outdir_is_complete(only) is False,
          "summary 파일 하나만 있는 폴더는 완료가 아니다 (예전에는 완료로 봤다)")
    check(bat.outdir_is_complete(only, lenient=True) is True,
          "--lenient-done 이면 예전처럼 완료로 본다 (옛 동작을 옵션으로 남겼다)")

    # 크기 0 인 파일 3종
    zero = tmp / "zerosize"
    zero.mkdir(parents=True, exist_ok=True)
    for suf in ("_summary_confidences.json", "_ranking_scores.csv", "_model.cif"):
        (zero / ("zerosize" + suf)).write_text("", encoding="utf-8")
    check(bat.outdir_is_complete(zero) is False,
          "크기 0 인 산출물은 없는 것으로 센다 (예전에는 완료로 봤다)")

    # 압축 mmCIF
    zst = tmp / "zstd_case"
    zst.mkdir(parents=True, exist_ok=True)
    for suf in ("_summary_confidences.json", "_ranking_scores.csv",
                "_model.cif.zst"):
        (zst / ("zstd_case" + suf)).write_text("x", encoding="utf-8")
    check(bat.outdir_is_complete(zst) is True,
          "_model.cif.zst 도 완료로 인정한다 (--compress_large_output_files)")

    # 단계별 판정 모드
    check(bat.stage_check_mode("msa") == "data", "stage msa -> mode data")
    for st in ("infer", "both", "oneshot"):
        check(bat.stage_check_mode(st) == "full", "stage %s -> mode full" % st)


# ===========================================================================
# 5. 세 사본이 같은 답을 내는가
# ===========================================================================
def test_copies_agree(root, tmp):
    section("5", "정본 블록 세 사본이 같은 답을 내는가")
    col = load_module(SCRIPTS / "af3_collect.py", "af3c3")
    vis = load_module(SCRIPTS / "af3_visualize.py", "af3v3")
    bat = load_module(SCRIPTS / "af3_batch.py", "af3b3")
    mods = [("af3_collect", col), ("af3_visualize", vis), ("af3_batch", bat)]

    dirs = sorted(p for p in root.iterdir() if p.is_dir())
    for d in dirs:
        answers = {}
        for nm, mod in mods:
            if mod.is_sidecar(d.name):
                answers[nm] = "제외"
                continue
            info = mod.resolve_result_dir(d, mode="full")
            answers[nm] = (info["target"], info["stem"], info["complete"],
                           info["n_final"], info["source"])
        uniq = set(map(str, answers.values()))
        check(len(uniq) == 1,
              "'%s' 에 대해 세 사본이 같은 답" % d.name, str(answers))

    for nm, mod in mods:
        check(mod.FINAL_SUFFIX_GROUPS == col.FINAL_SUFFIX_GROUPS,
              "%s 의 FINAL_SUFFIX_GROUPS 가 같다" % nm)
        check(mod.strip_af3_timestamp("VHH_004_20260820_101010") == "VHH_004",
              "%s.strip_af3_timestamp 동작" % nm)
        check(mod.strip_af3_timestamp("VHH_004_variantB") == "VHH_004_variantB",
              "%s: 타임스탬프가 아닌 접미사는 자르지 않는다" % nm)
        check(mod.af3_timestamp_of("VHH_005_20260820_120000") == "20260820_120000",
              "%s.af3_timestamp_of 동작" % nm)


# ===========================================================================
# 6. stem 을 신뢰할 수 없는 경우의 규정된 처리
# ===========================================================================
def test_edge_cases(root, tmp):
    section("6", "stem 을 신뢰할 수 없는 경우")
    col = load_module(SCRIPTS / "af3_collect.py", "af3c4")

    # (a) 산출물이 하나도 없는 폴더
    empty = tmp / "VHH_777_20260820_101010"
    empty.mkdir(parents=True, exist_ok=True)
    info = col.resolve_result_dir(empty)
    check(info["target"] == "VHH_777" and info["source"] == "folder_stripped",
          "산출물이 없으면 폴더명에서 타임스탬프를 떼어 이름을 정한다", str(info))
    check(info["complete"] is False, "그 폴더는 완료가 아니다")

    # (c) 완료 stem 이 여러 개 - 폴더명 일치를 고른다
    multi = tmp / "VHH_888"
    fixture.write_target_files(multi, "VHH_888", [("A", 5)], 0.7)
    fixture.write_target_files(multi, "VHH_999", [("A", 5)], 0.9)
    info = col.resolve_result_dir(multi)
    check(info["target"] == "VHH_888",
          "완료 stem 이 여럿이면 폴더명과 일치하는 것을 고른다", str(info))
    check(bool(info["note"]), "그 사실을 note 로 알린다", info["note"])

    # 폴더명과 일치하는 것이 없으면 사전순 첫 번째 (재현 가능한 규칙)
    multi2 = tmp / "unrelated_dir"
    fixture.write_target_files(multi2, "BBB", [("A", 5)], 0.7)
    fixture.write_target_files(multi2, "AAA", [("A", 5)], 0.9)
    i1 = col.resolve_result_dir(multi2)
    i2 = col.resolve_result_dir(multi2)
    check(i1["target"] == "AAA", "일치가 없으면 사전순 첫 번째", str(i1))
    check(i1 == i2, "같은 폴더를 두 번 판정하면 같은 답 (임의 선택이 아니다)")

    # (d) 완료 stem 은 없고 미완료 stem 만 - 이름은 알려준다
    part = tmp / "VHH_555_20260820_101010"
    fixture.write_target_files(part, "VHH_555", [("A", 5)], 0.0,
                               with_summary=False, with_conf=False,
                               with_cif=False, with_rank=False)
    info = col.resolve_result_dir(part)
    check(info["target"] == "VHH_555" and info["complete"] is False,
          "미완료여도 타깃명은 stem 에서 얻는다 (재시도 목록에 쓸 수 있게)",
          str(info))

    # 타깃명 자체가 타임스탬프처럼 끝나는 경우 - stem 이 1순위라 잘리지 않아야 한다
    tsname = tmp / "run_20260820_101010"
    fixture.write_target_files(tsname, "run_20260820_101010", [("A", 5)], 0.7)
    info = col.resolve_result_dir(tsname)
    check(info["target"] == "run_20260820_101010",
          "타깃명이 타임스탬프로 끝나도 stem 이 1순위이므로 잘리지 않는다", str(info))


def main(argv=None):
    ap = argparse.ArgumentParser(description="타깃명 정규화 검사")
    ap.add_argument("--keep", action="store_true", help="임시 폴더를 남긴다")
    args = ap.parse_args(argv)

    tmpdir = Path(tempfile.mkdtemp(prefix="af3_naming_test_"))
    root = tmpdir / "af3_out"
    fixture.build(root)
    work = tmpdir / "work"
    work.mkdir()

    print("=" * 72)
    print("타깃명 정규화 검사")
    print("=" * 72)
    print("가짜 출력 폴더: %s" % root)

    test_collect(root, work)
    test_visualize(root, work)
    test_hidden_excluded(root, work)
    test_batch(root, work)
    test_copies_agree(root, work)
    test_edge_cases(root, work)

    print("\n" + "=" * 72)
    # 절별 건수. docs/naming_fix_notes.md 4.3 의 표에 이 숫자를 그대로 넣는다.
    print("절별 건수 (문서의 표에 그대로 옮긴다)")
    for num, name, npass, nfail in _by_section:
        print("  [%s] %-46s 통과 %3d%s"
              % (num, name[:46], npass,
                 ("   실패 %d" % nfail) if nfail else ""))
    print("  %s 합계 %3d"
          % ("".ljust(51), sum(p for _n, _m, p, _f in _by_section)))
    print("-" * 72)
    if _fail:
        print("실패 %d건 / 통과 %d건" % (len(_fail), _pass))
        for what, detail in _fail:
            print("  - %s%s" % (what, ("  <- " + detail) if detail else ""))
        print("=" * 72)
        if not args.keep:
            shutil.rmtree(tmpdir, ignore_errors=True)
        else:
            print("임시 폴더를 남겼다: %s" % tmpdir)
        return 1
    print("전체 통과 (%d건)" % _pass)
    print("=" * 72)
    if args.keep:
        print("임시 폴더를 남겼다: %s" % tmpdir)
    else:
        shutil.rmtree(tmpdir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
