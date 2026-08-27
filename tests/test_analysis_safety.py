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
    fields = ["조건", "타깃", "ranking_score", "pLDDT평균", "pTM", "ipTM", "pLDDT_90이상비율", "체인수"]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            value = dict(row)
            if "체인수" not in value:
                value["체인수"] = 2 if str(value.get("ipTM", "")).strip() else 1
            writer.writerow(value)


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
    item="collect",
    prevents="collector가 top-N 경계 동점을 이름순으로 잘라 stage2와 다른 후보 집합을 만드는 버그.",
)
def test_collect_top_includes_all_boundary_ties():
    workspace = Workspace()
    try:
        for target in "abc":
            result = workspace.make_result(target)
            summary = result / (target + "_summary_confidences.json")
            obj = json.loads(summary.read_text(encoding="utf-8"))
            obj["ranking_score"] = 0.8
            summary.write_text(json.dumps(obj), encoding="utf-8")
        top_list = workspace.root / "top.txt"
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "af3_collect.py"), str(workspace.output_dir),
             "--top", "2", "--top-list", str(top_list)],
            capture_output=True, text=True,
        )
        check_equal(proc.returncode, 0, "collector tie selection이 실패했다", proc.stderr[-1200:])
        check_equal(top_list.read_text(encoding="utf-8").splitlines(), ["a", "b", "c"],
                    "top-2 경계 동점 전부를 포함하지 않았다")
        check_in("요청 N=2, 실현 N=3", proc.stdout, "요청/실현 N을 보고하지 않았다")
    finally:
        workspace.cleanup()


@regression(
    item="stage2",
    prevents="stage2가 top-N 경계 동점을 임의 절단하거나 manifest에 요청 N과 실제 후보 수를 남기지 않는 버그.",
)
def test_stage2_manifest_records_tie_policy_and_realized_n():
    with tempfile.TemporaryDirectory(prefix="af3_stage2_ties_") as td:
        root = Path(td)
        source = root / "source"
        source.mkdir()
        rows = []
        for target in "abc":
            target_dir = source / target
            target_dir.mkdir()
            obj = {
                "name": target, "modelSeeds": [1], "dialect": "alphafold3", "version": 1,
                "sequences": [{"protein": {"id": "A", "sequence": "ACDE", "unpairedMsa": ">q\nACDE\n"}}],
            }
            (target_dir / (target + "_data.json")).write_text(json.dumps(obj), encoding="utf-8")
            rows.append({"조건": "r", "타깃": target, "ranking_score": 0.8})
        summary = root / "summary.csv"
        write_collect_csv(summary, rows)
        outdir, manifest = root / "stage2", root / "manifest.csv"
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "af3_stage2.py"), "--csv", str(summary),
             "--top", "2", "--from-out", str(source), "-o", str(outdir),
             "--manifest", str(manifest)],
            capture_output=True, text=True,
        )
        check_equal(proc.returncode, 0, "stage2 tie selection이 실패했다", proc.stderr[-1600:])
        with manifest.open("r", encoding="utf-8-sig", newline="") as handle:
            manifest_rows = list(csv.DictReader(handle))
        check_equal(len(manifest_rows), 3, "top-2 경계 동점 후보를 전부 쓰지 않았다")
        check_equal({row["상위동점정책"] for row in manifest_rows}, {"include-all"}, "tie 정책이 manifest에 없다")
        check_equal({row["요청N"] for row in manifest_rows}, {"2"}, "요청 N이 manifest에 없다")
        check_equal({row["실현N"] for row in manifest_rows}, {"3"}, "실현 N이 manifest에 없다")


@regression(
    item="stage2",
    prevents="명시적 tie-policy=error가 동점 경계를 이름순으로 조용히 자르는 버그.",
)
def test_explicit_error_tie_policy_rejects_boundary_ties():
    stage2 = load_module("af3_stage2.py")
    rankcorr = load_module("af3_rankcorr.py")
    rows = [{"타깃": target, "조건": "x", "ranking_score": 1.0} for target in "abc"]
    try:
        stage2.select_rows(
            rows, by="ranking_score", top=2, minimum=None, grades=None,
            condition=None, tie_policy="error",
        )
    except ValueError:
        pass
    else:
        check(False, "stage2 tie-policy=error가 경계 동점을 허용했다")
    try:
        rankcorr.top_with_boundary_ties([("a", 1.0), ("b", 1.0), ("c", 1.0)], 2, "error")
    except ValueError:
        pass
    else:
        check(False, "rankcorr tie-policy=error가 경계 동점을 허용했다")


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
    prevents="top-N 경계 동점을 알파벳 순으로 잘라 도구마다 서로 다른 후보 집합을 쓰는 버그.",
)
def test_rankcorr_includes_all_boundary_ties_and_uses_true_median():
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
        check_in("include-all", proc.stdout, "경계 동점 정책을 기록하지 않았다")
        check_in("4            2", proc.stdout, "기준 top-2 경계 동점 4건을 전부 포함하지 않았다")
        check_in("중앙값 +0.0500", proc.stdout, "even-size median을 잘못 계산했다")


@regression(
    item="rankcorr",
    prevents="--all-metrics가 지표마다 결측 타깃을 다르게 버려 서로 다른 모집단의 상관을 나란히 비교하는 버그.",
)
def test_rankcorr_all_metrics_uses_shared_complete_case_population():
    with tempfile.TemporaryDirectory(prefix="af3_rank_complete_") as td:
        root = Path(td)
        ref, test, out = root / "ref.csv", root / "test.csv", root / "out.csv"
        rows_ref, rows_test = [], []
        for i, target in enumerate("abcde"):
            base = {
                "타깃": target, "ranking_score": 0.9 - i * 0.05,
                "pLDDT평균": 90 - i, "pTM": 0.8 - i * 0.02,
                "ipTM": "", "pLDDT_90이상비율": 0.7 - i * 0.03,
            }
            rows_ref.append({"조건": "r", **base})
            rows_test.append({"조건": "t", **base, "ranking_score": base["ranking_score"] - i * 0.01})
        rows_ref[-1]["pTM"] = ""
        rows_test[-1]["pTM"] = ""
        write_collect_csv(ref, rows_ref)
        write_collect_csv(test, rows_test)
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "af3_rankcorr.py"), "--ref", str(ref),
             "--test", str(test), "--all-metrics", "--top-n", "2", "--bootstrap", "20",
             "-o", str(out)],
            capture_output=True, text=True,
        )
        check_equal(proc.returncode, 0, "complete-case 분석이 실패했다", proc.stderr[-1200:])
        with out.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        check(rows, "all-metrics 결과가 비었다")
        check_equal({row["공통건수"] for row in rows}, {"4"}, "지표마다 다른 모집단을 썼다")
        check_equal({row["모집단정책"] for row in rows}, {"complete-case"}, "모집단 정책이 기록되지 않았다")


@regression(
    item="rankcorr",
    prevents="조건 사이에서 단량체/복합체 유형이 바뀐 타깃을 같은 estimand로 조용히 합치는 버그.",
)
def test_rankcorr_rejects_monomer_complex_mismatch():
    with tempfile.TemporaryDirectory(prefix="af3_rank_kind_") as td:
        root = Path(td)
        ref, test = root / "ref.csv", root / "test.csv"
        base = [
            {"타깃": target, "ranking_score": 0.9 - i * 0.1, "pTM": 0.8, "pLDDT평균": 85,
             "ipTM": "", "pLDDT_90이상비율": 0.5}
            for i, target in enumerate("abcd")
        ]
        write_collect_csv(ref, [{"조건": "r", **row} for row in base])
        changed = [{"조건": "t", **row} for row in base]
        changed[0]["ipTM"] = 0.75
        write_collect_csv(test, changed)
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "af3_rankcorr.py"), "--ref", str(ref),
             "--test", str(test), "--bootstrap", "0"],
            capture_output=True, text=True,
        )
        check(proc.returncode != 0, "단량체/복합체 mismatch를 허용했다")
        check_in("단량체/복합체", proc.stdout + proc.stderr, "구조 유형 mismatch를 진단하지 않았다")


@regression(
    item="rankcorr",
    prevents="bootstrap CI가 실행마다 달라지거나 target이 아닌 값 단위 재표집으로 과도하게 좁아지는 버그.",
)
def test_rankcorr_bootstrap_is_deterministic_and_reports_intervals():
    mod = load_module("af3_rankcorr.py")
    pairs = [(str(i), float(i), float(i + (i % 3))) for i in range(12)]
    first = mod.bootstrap_intervals(pairs, [3], "include-all", 100, 17)
    second = mod.bootstrap_intervals(pairs, [3], "include-all", 100, 17)
    check_equal(first, second, "같은 seed의 target bootstrap 결과가 달라졌다")
    check(first["rho"][0] is not None and first["tau"][0] is not None
          and first["tops"][3]["recall"][0] is not None,
          "Spearman/Kendall/top-N bootstrap CI가 없다")


@regression(
    item="rankcorr",
    prevents="ipTM이 빠진 다중 사슬을 단량체로 분류해 잘못된 구조층에 넣는 버그.",
)
def test_rankcorr_structure_kind_uses_chain_count_not_iptm_missingness():
    mod = load_module("af3_rankcorr.py")
    check_equal(mod.structure_kind({"체인수": "1", "ipTM": ""}), "monomer",
                "1-chain target을 monomer로 분류하지 않았다")
    check_equal(mod.structure_kind({"체인수": "2", "ipTM": ""}), "complex",
                "missing-iPTM multichain을 monomer로 분류했다")
    check_equal(mod.structure_kind({"ipTM": ""}), "unknown",
                "chain count도 ipTM도 없는 target을 단량체로 추측했다")


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
