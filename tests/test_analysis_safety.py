#!/usr/bin/env python3
"""Collector and rank-comparison statistical/automation safety regressions."""

from __future__ import annotations

import csv
import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path

from harness import SCRIPTS_DIR, Workspace, check, check_equal, check_in, load_module, regression


def write_collect_csv(path: Path, rows):
    fields = ["조건", "타깃", "ranking_score", "pLDDT평균", "pTM", "ipTM", "pLDDT_90이상비율"]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


@regression(
    item="collect",
    prevents="여러 조건 중 한 출력 root가 없는데 부분 CSV를 쓰고 성공해 비교 조건 누락을 숨기는 버그.",
)
def test_collect_rejects_any_missing_requested_root():
    workspace = Workspace()
    try:
        workspace.make_result("a")
        out = workspace.root / "summary.csv"
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "af3_collect.py"),
             "ok=" + str(workspace.output_dir), "missing=" + str(workspace.root / "missing"),
             "-o", str(out)],
            capture_output=True, text=True,
        )
        check(proc.returncode != 0, "누락 root가 있는데 부분 비교를 성공 처리했다")
        check(not out.exists(), "누락 조건이 있는데 부분 CSV를 썼다")
    finally:
        workspace.cleanup()


@regression(
    item="collect",
    prevents="ragged chain_pair_iptm 행렬 하나가 IndexError로 전체 집계를 중단하는 버그.",
)
def test_collect_handles_ragged_chain_pair_matrix():
    workspace = Workspace()
    try:
        result = workspace.make_result("a")
        summary = result / "a_summary_confidences.json"
        obj = json.loads(summary.read_text(encoding="utf-8"))
        obj["chain_pair_iptm"] = [[0.8], [0.5, 0.8]]
        summary.write_text(json.dumps(obj), encoding="utf-8")
        out = workspace.root / "summary.csv"
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "af3_collect.py"), str(workspace.output_dir), "-o", str(out)],
            capture_output=True, text=True,
        )
        check_equal(proc.returncode, 0, "ragged 행렬 때문에 전체 집계가 죽었다", proc.stderr[-1200:])
        check(out.is_file(), "집계 CSV가 없다")
    finally:
        workspace.cleanup()


@regression(
    item="collect",
    prevents="실제 AF3 단량체의 정상 1x1 chain_pair_iptm을 비정사각형이라고 경고하는 버그.",
)
def test_collect_accepts_monomer_one_by_one_chain_pair_matrix():
    workspace = Workspace()
    try:
        result = workspace.make_result("a")
        summary = result / "a_summary_confidences.json"
        obj = json.loads(summary.read_text(encoding="utf-8"))
        obj["chain_pair_iptm"] = [[0.44]]
        obj["chain_pair_pae_min"] = [[0.76]]
        obj["chain_ptm"] = [0.44]
        obj["chain_iptm"] = [None]
        summary.write_text(json.dumps(obj), encoding="utf-8")
        out = workspace.root / "summary.csv"
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "af3_collect.py"), str(workspace.output_dir), "-o", str(out)],
            capture_output=True, text=True,
        )
        check_equal(proc.returncode, 0, "정상 단량체 1x1 행렬 집계가 실패했다", proc.stderr)
        check("정사각형이 아니다" not in proc.stderr, "정상 1x1 행렬에 잘못된 경고를 냈다")
    finally:
        workspace.cleanup()


@regression(
    item="collect",
    prevents="--top 0이 성공해 후보 목록을 만들지 않고도 자동화가 다음 단계로 가는 버그.",
)
def test_collect_requires_positive_top():
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "af3_collect.py"), ".", "--top", "0"],
        capture_output=True, text=True,
    )
    check(proc.returncode != 0, "--top 0을 허용했다")


@regression(
    item="rankcorr",
    prevents="중복 타깃이 마지막 행으로 덮여 CSV 행 순서에 따라 rho가 바뀌는 버그.",
)
def test_rankcorr_rejects_duplicate_targets():
    with tempfile.TemporaryDirectory(prefix="af3_rank_dup_") as td:
        root = Path(td)
        ref = root / "ref.csv"
        test = root / "test.csv"
        write_collect_csv(ref, [
            {"조건": "r", "타깃": "a", "ranking_score": 0.9},
            {"조건": "r", "타깃": "a", "ranking_score": 0.1},
            {"조건": "r", "타깃": "b", "ranking_score": 0.8},
            {"조건": "r", "타깃": "c", "ranking_score": 0.7},
        ])
        write_collect_csv(test, [
            {"조건": "t", "타깃": "a", "ranking_score": 0.9},
            {"조건": "t", "타깃": "b", "ranking_score": 0.8},
            {"조건": "t", "타깃": "c", "ranking_score": 0.7},
        ])
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "af3_rankcorr.py"), "--ref", str(ref), "--test", str(test)],
            capture_output=True, text=True,
        )
        check(proc.returncode != 0, "중복 타깃을 덮어썼다")
        check_in("중복", proc.stdout + proc.stderr, "중복 행을 설명하지 않았다")


@regression(
    item="rankcorr",
    prevents="양쪽 타깃 집합이 다른데 교집합만 조용히 분석해 rank 보존을 과장하는 버그.",
)
def test_rankcorr_requires_identical_target_sets_by_default():
    with tempfile.TemporaryDirectory(prefix="af3_rank_sets_") as td:
        root = Path(td)
        ref = root / "ref.csv"
        test = root / "test.csv"
        write_collect_csv(ref, [{"조건": "r", "타깃": x, "ranking_score": i} for i, x in enumerate("abcd")])
        write_collect_csv(test, [{"조건": "t", "타깃": x, "ranking_score": i} for i, x in enumerate("abce")])
        cmd = [sys.executable, str(SCRIPTS_DIR / "af3_rankcorr.py"), "--ref", str(ref), "--test", str(test)]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        check(proc.returncode != 0, "불일치 target set을 조용히 교집합 분석했다")
        proc2 = subprocess.run(cmd + ["--allow-intersection"], capture_output=True, text=True)
        check_equal(proc2.returncode, 0, "명시적 교집합 분석을 거부했다", proc2.stderr[-800:])


@regression(
    item="rankcorr",
    prevents="top-N 경계 동점을 알파벳 순으로 깨서 순위 정보가 없는데 겹침률 1.0을 보고하는 버그.",
)
def test_rankcorr_marks_boundary_ties_nonidentifiable_and_uses_true_median():
    with tempfile.TemporaryDirectory(prefix="af3_rank_ties_") as td:
        root = Path(td)
        ref = root / "ref.csv"
        test = root / "test.csv"
        values = [0.0, 0.0, 0.1, 0.1]
        write_collect_csv(ref, [{"조건": "r", "타깃": chr(97 + i), "ranking_score": 1.0} for i in range(4)])
        write_collect_csv(test, [{"조건": "t", "타깃": chr(97 + i), "ranking_score": 1.0 + values[i]} for i in range(4)])
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "af3_rankcorr.py"), "--ref", str(ref), "--test", str(test), "--top-n", "2"],
            capture_output=True, text=True,
        )
        check_equal(proc.returncode, 0, "동점 진단 실행이 실패했다", proc.stderr[-800:])
        check_in("판정불가(동점)", proc.stdout, "경계 동점을 완전한 순위처럼 보고했다")
        check_in("중앙값 +0.0500", proc.stdout, "even-size median을 잘못 계산했다")


@regression(
    item="rankcorr",
    prevents="NaN/Inf 점수와 --top-n 0이 통계 계산에 들어가거나 TypeError traceback을 만드는 버그.",
)
def test_rankcorr_rejects_nonfinite_values_and_nonpositive_topn():
    mod = load_module("af3_rankcorr.py")
    check(mod.as_float("nan") is None and mod.as_float("inf") is None, "nonfinite 값을 허용했다")
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "af3_rankcorr.py"), "--ref", "x", "--test", "y", "--top-n", "0"],
        capture_output=True, text=True,
    )
    check(proc.returncode != 0, "--top-n 0을 허용했다")
    check("Traceback" not in proc.stderr, "잘못된 top-N이 traceback을 만들었다")
