#!/usr/bin/env python3
"""집계·시각화 회귀 테스트 (과제 항목 11, 12).

이 모듈이 지키는 원칙: **관리용 폴더는 결과가 아니다. 같은 타깃은 한 번만 센다.**
집계가 틀리면 2000건 스크리닝의 상위 후보 선정 자체가 틀어진다. 그리고 이 오류는
숫자로만 보이므로 눈으로 발견하기 어렵다.
"""

from __future__ import annotations

import csv
import json
import re
import subprocess
import sys
from pathlib import Path

from harness import (
    SCRIPTS_DIR,
    Failure,
    Workspace,
    check,
    check_equal,
    check_in,
    load_module,
    regression,
)

TIMESTAMP_SUFFIX = re.compile(r"_\d{8}_\d{6}$")


def make_af3_result(output_dir: Path, folder: str, stem: str | None = None) -> Path:
    """AF3 결과 폴더 하나를 만든다.

    stem 을 folder 와 다르게 줄 수 있는 것이 핵심이다. AF3 가 타임스탬프 접미사
    폴더를 만들 때(run_alphafold.py:861-870) 폴더 이름에는 접미사가 붙지만
    폴더 **안** 파일 이름은 job_name=sanitised_name 그대로다
    (run_alphafold.py:727, post_processing.py:121-123). 즉
      vhh_a_20260101_010101/vhh_a_ranking_scores.csv
    같은 조합이 실제로 생긴다.
    """
    stem = stem or folder
    result = output_dir / folder
    result.mkdir(parents=True, exist_ok=True)
    (result / f"{stem}_summary_confidences.json").write_text(
        json.dumps(
            {
                "ranking_score": 0.8,
                "ptm": 0.8,
                "iptm": None,
                "fraction_disordered": 0.1,
                "has_clash": 0.0,
                "chain_ptm": [0.8],
                "chain_iptm": [None],
                "chain_pair_iptm": [[0.8]],
            }
        ),
        encoding="utf-8",
    )
    (result / f"{stem}_ranking_scores.csv").write_text(
        "seed,sample,ranking_score\n1,0,0.8\n", encoding="utf-8"
    )
    (result / f"{stem}_model.cif").write_text(
        "data_x\n#\nloop_\n_atom_site.group_PDB\n", encoding="utf-8"
    )
    (result / f"{stem}_confidences.json").write_text(
        json.dumps({"atom_plddts": [80.0, 90.0], "pae": [[0.5, 1.0], [1.0, 0.5]]}),
        encoding="utf-8",
    )
    (result / f"{stem}_data.json").write_text("{}", encoding="utf-8")
    return result


def add_managed_state(workspace: Workspace) -> None:
    """러너가 남기는 관리용 상태를 출력 폴더에 심는다.

    두 가지 깊이를 함께 심는 것이 중요하다.

    (a) 중첩형: .af3_incomplete/<타깃>/<타임스탬프>/ — 현재 러너가 쓰는 구조.
    (b) 평평형: .af3_incomplete_flat/ 가 결과 파일을 직접 담은 형태.

    (b) 를 넣는 이유: 집계 스크립트는 출력 폴더 한 단계만 훑으므로, (a) 만으로는
    '숨은 폴더 제외' 규칙이 실제로 일을 하는지 확인할 수 없다. 규칙을 지워도
    (a) 는 그냥 '미완성' 으로 분류돼 결과가 같기 때문이다. 역검증(버그 재주입)에서
    이 구멍이 실제로 드러났다. (b) 는 격리 스냅샷 내용을 사람이 한 단계 올려 둔
    경우나, name 이 점으로 시작해 AF3 가 숨은 폴더를 만든 경우에 실제로 생긴다.
    """
    quarantine = workspace.output_dir / ".af3_incomplete" / "vhh_q" / "20260101_000000"
    quarantine.mkdir(parents=True)
    # 격리 보존본 안에는 완료처럼 보이는 파일이 들어 있을 수도 있다.
    make_af3_result(quarantine.parent, "20260101_000000", stem="vhh_q")
    (quarantine / ".af3_quarantine_marker").write_text("{}", encoding="utf-8")

    # (b) 평평형: 숨은 폴더가 완료 산출물을 직접 담고 있다.
    make_af3_result(workspace.output_dir, ".af3_incomplete_flat", stem="vhh_flat")

    (workspace.output_dir / ".run_af3_batch.lock").write_text(
        "host=x pid=1\n", encoding="utf-8"
    )
    pending = workspace.root / ".af3_pending_leftover"
    pending.mkdir(exist_ok=True)
    (pending / "a.json").write_text("{}", encoding="utf-8")


def run_collect(workspace: Workspace, *extra: str) -> tuple[subprocess.CompletedProcess, list[dict]]:
    out_csv = workspace.root / "summary.csv"
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS_DIR / "af3_collect.py"),
            str(workspace.output_dir),
            "-o",
            str(out_csv),
            *extra,
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    rows: list[dict] = []
    if out_csv.exists():
        with out_csv.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
    return proc, rows


@regression(
    item="11",
    prevents=".af3_incomplete/ 안의 격리 보존본과 .af3_pending_* staging 이 집계에 섞여, "
    "실패한 결과가 완료 건으로 세어지고 상위 후보 선정을 오염시키는 버그.",
)
def test_managed_dirs_are_excluded_from_collection():
    workspace = Workspace()
    try:
        make_af3_result(workspace.output_dir, "vhh_a")
        make_af3_result(workspace.output_dir, "vhh_b")
        add_managed_state(workspace)

        proc, rows = run_collect(workspace)
        check_equal(proc.returncode, 0, f"집계가 실패했다\n{proc.stderr[-1200:]}")
        targets = sorted(row["타깃"] for row in rows)
        check_equal(
            targets,
            ["vhh_a", "vhh_b"],
            "관리용 폴더나 잠금 파일이 집계 대상에 섞였다",
        )
        check(
            "vhh_q" not in targets,
            "격리된 미완료 결과를 완료 건으로 집계했다",
        )
        check(
            not any(name.startswith(".") for name in targets),
            "숨은 관리 폴더가 집계 대상에 들어왔다",
            f"타깃 목록={targets}",
        )
    finally:
        workspace.cleanup()


@regression(
    item="11",
    prevents="시각화가 관리용 폴더를 타깃으로 잡아 격리된 실패 결과의 플롯을 그리거나, "
    "잠금 파일을 읽으려다 죽는 버그.",
)
def test_managed_dirs_are_excluded_from_visualization():
    workspace = Workspace()
    try:
        make_af3_result(workspace.output_dir, "vhh_a")
        add_managed_state(workspace)

        sys.path.insert(0, str(SCRIPTS_DIR))
        try:
            import importlib.util

            spec = importlib.util.spec_from_file_location(
                "af3_visualize_mod", SCRIPTS_DIR / "af3_visualize.py"
            )
            module = importlib.util.module_from_spec(spec)
            sys.modules["af3_visualize_mod"] = module
            spec.loader.exec_module(module)
        finally:
            sys.path.remove(str(SCRIPTS_DIR))

        found = module.find_targets(workspace.output_dir, None)
        names = sorted(name for name, _child, _stem in found)
        check_equal(
            names,
            ["vhh_a"],
            "시각화가 관리용 폴더(.af3_incomplete 등)를 타깃으로 잡았다",
        )
        check(
            not any(name.startswith(".") for name in names),
            "숨은 관리 폴더를 시각화 타깃으로 잡았다",
            f"타깃 목록={names}",
        )
    finally:
        workspace.cleanup()


@regression(
    item="11",
    prevents="집계가 미완성 폴더를 조용히 빼먹고 완료 건수만 보고해, 실패를 모르고 "
    "다음 단계로 넘어가는 버그. 미완성 건수를 알려야 한다.",
)
def test_incomplete_targets_are_reported_not_hidden():
    workspace = Workspace()
    try:
        make_af3_result(workspace.output_dir, "vhh_a")
        # 추론 전에 끊긴 폴더 (_data.json 만).
        broken = workspace.output_dir / "vhh_b"
        broken.mkdir()
        (broken / "vhh_b_data.json").write_text("{}", encoding="utf-8")

        proc, rows = run_collect(workspace)
        check_equal(proc.returncode, 0, "집계가 실패했다")
        check_equal(
            sorted(row["타깃"] for row in rows), ["vhh_a"], "미완성 폴더를 완료로 집계했다"
        )
        combined = proc.stdout + proc.stderr
        check_in("미완성", combined, "미완성 건수를 알려주지 않았다")
        check_in("vhh_b", combined, "어떤 타깃이 미완성인지 알려주지 않았다")
    finally:
        workspace.cleanup()


@regression(
    item="11",
    prevents="집계할 완료 결과가 하나도 없는데 종료코드 0 을 돌려주는 버그. "
    "자동화가 빈 CSV 를 정상 결과로 오인한다.",
)
def test_empty_collection_exits_nonzero():
    workspace = Workspace()
    try:
        add_managed_state(workspace)  # 관리 폴더만 있는 상태
        proc, rows = run_collect(workspace)
        check(
            proc.returncode != 0,
            "집계할 결과가 없는데 종료코드가 0 이다",
            f"종료코드={proc.returncode}, 행수={len(rows)}",
        )
    finally:
        workspace.cleanup()


@regression(
    item="12",
    prevents="AF3 타임스탬프 접미사 폴더(vhh_a_20260101_010101)를 별개 타깃으로 집계해, "
    "같은 VHH 가 2000건 스크리닝에서 두 번 세어지고 상위 후보에 중복 등장하는 버그. "
    "폴더 이름이 아니라 폴더 안 파일 stem 이 진짜 타깃 이름이다.",
    # 2026-08: 타깃명정규화 트랙이 고쳤다. af3_collect.py 의 resolve_result_dir 이
    # 폴더명 대신 산출물 파일 stem 에서 타깃명을 얻는다. 병합 후에도 통과하므로
    # expect_fail_on_current 를 지웠다. 다시 실패하면 그것은 회귀다.
)
def test_timestamp_suffix_folders_are_not_separate_targets():
    workspace = Workspace()
    try:
        make_af3_result(workspace.output_dir, "vhh_a")
        # AF3 가 재실행 때 만든 형제 폴더. 안의 파일 stem 은 vhh_a 그대로다.
        make_af3_result(workspace.output_dir, "vhh_a_20260101_010101", stem="vhh_a")

        proc, rows = run_collect(workspace)
        check_equal(proc.returncode, 0, f"집계가 실패했다\n{proc.stderr[-800:]}")
        targets = [row["타깃"] for row in rows]
        stamped = [name for name in targets if TIMESTAMP_SUFFIX.search(name)]
        check_equal(
            stamped,
            [],
            "타임스탬프 접미사 폴더가 별개 타깃 이름으로 집계됐다 "
            "(폴더 안 파일 stem 인 'vhh_a' 로 정규화돼야 한다)",
        )
        check(
            targets.count("vhh_a") <= 1,
            "같은 타깃이 여러 번 집계됐다",
            f"타깃 목록={targets}",
        )
    finally:
        workspace.cleanup()


@regression(
    item="12",
    prevents="위와 같은 문제를 시각화 쪽에서. 같은 VHH 의 플롯이 두 개 생기고 파일 이름이 "
    "타임스탬프로 달라 어느 쪽이 최신인지 알 수 없게 되는 버그.",
    # 2026-08: 위와 같이 고쳐졌다. af3_visualize.py 의 find_targets 가
    # af3_collect.py 와 같은 규칙(resolve_result_dir)을 쓴다.
)
def test_timestamp_suffix_folders_are_normalized_in_visualization():
    workspace = Workspace()
    try:
        make_af3_result(workspace.output_dir, "vhh_a")
        make_af3_result(workspace.output_dir, "vhh_a_20260101_010101", stem="vhh_a")

        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "af3_visualize_mod2", SCRIPTS_DIR / "af3_visualize.py"
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules["af3_visualize_mod2"] = module
        spec.loader.exec_module(module)

        found = module.find_targets(workspace.output_dir, None)
        names = [name for name, _child, _stem in found]
        stamped = [name for name in names if TIMESTAMP_SUFFIX.search(name)]
        check_equal(
            stamped,
            [],
            "시각화가 타임스탬프 접미사 폴더를 별개 타깃으로 잡았다",
        )
    finally:
        workspace.cleanup()


@regression(
    item="12",
    prevents="af3_batch.py 의 결과 폴더 탐색이 타임스탬프 접미사 폴더를 못 찾아 "
    "이미 끝난 건을 다시 돌리는 버그 (반대 방향의 실수).",
)
def test_batch_finds_timestamp_suffix_result_dirs():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "af3_batch_mod", SCRIPTS_DIR / "af3_batch.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["af3_batch_mod"] = module
    spec.loader.exec_module(module)

    workspace = Workspace()
    try:
        make_af3_result(workspace.output_dir, "vhh_a_20260101_010101", stem="vhh_a")
        found = module.find_result_dirs(workspace.output_dir, "vhh_a")
        check(
            any(p.name == "vhh_a_20260101_010101" for p in found),
            "타임스탬프 접미사 결과 폴더를 찾지 못했다",
            f"찾은 폴더={[p.name for p in found]}",
        )
    finally:
        workspace.cleanup()


@regression(
    item="11",
    prevents="상위 N건 선정이 여러 조건(라벨)을 섞어 같은 타깃을 중복 선정하는 버그. "
    "2단계 전략에서 재실행 후보 목록이 오염된다.",
)
def test_top_selection_warns_on_mixed_conditions():
    workspace = Workspace()
    try:
        make_af3_result(workspace.output_dir, "vhh_a")
        second = workspace.root / "other_out"
        second.mkdir()
        make_af3_result(second, "vhh_a")

        out_csv = workspace.root / "s.csv"
        top_list = workspace.root / "top.txt"
        proc = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS_DIR / "af3_collect.py"),
                f"축소DB={workspace.output_dir}",
                f"전체DB={second}",
                "-o",
                str(out_csv),
                "--top",
                "1",
                "--top-list",
                str(top_list),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        check_equal(proc.returncode, 0, f"집계가 실패했다\n{proc.stderr[-800:]}")
        check_in(
            "--top-condition",
            proc.stdout + proc.stderr,
            "조건이 섞였을 때 중복 선정 위험을 알려주지 않았다",
        )

        # --top-condition 을 주면 그 조건에서만 골라야 한다.
        proc2 = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS_DIR / "af3_collect.py"),
                f"축소DB={workspace.output_dir}",
                f"전체DB={second}",
                "-o",
                str(out_csv),
                "--top",
                "5",
                "--top-condition",
                "축소DB",
                "--top-list",
                str(top_list),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        check_equal(proc2.returncode, 0, "조건 지정 집계가 실패했다")
        picked = top_list.read_text(encoding="utf-8").split()
        check_equal(picked, ["vhh_a"], "한 조건에서만 골라야 하는데 중복 선정됐다")
    finally:
        workspace.cleanup()


@regression(
    item="view3d",
    prevents=(
        "build_page 가 자리표시자를 순차 치환해서, 타깃명에 __ENGINEJS__/__CIF__ 같은 "
        "자리표시자 문자열이 들어 있으면 나중 치환이 이미 삽입된 라벨을 다시 때려 "
        "데이터 JSON 리터럴과 <title> 을 갈라놓고 뷰어 JavaScript 가 통째로 죽는 버그."
    ),
)
def test_viewer_page_placeholders_survive_target_names_that_look_like_placeholders():
    view = load_module("af3_view3d.py")

    # AF3 sanitised_name 은 밑줄과 대문자를 남기므로 이런 타깃명이 실제로 만들어질 수 있다.
    hostile = ["__ENGINEJS__", "__CIF__", "__DATA__", "run__TITLE__v2", "__LIBBODY__"]
    for label in hostile:
        rec = {
            "label": label,
            "dir": "/tmp/%s" % label,
            "stem": label,
            "cif": "data_x\n_atom_site.group_PDB\n",
            "problem": "",
            "summary": {"ranking_score": 0.9, "ptm": 0.9, "iptm": None},
            "residues": [{"c": "A", "i": 1, "n": "ALA", "p": 90.0, "a": 5}],
            "chains": ["A"],
            "mean_plddt": 90.0,
            "min_plddt": 90.0,
            "rank": 0.9,
            "n_sample": 1,
            "sample_sd": None,
            "n_atom": 5,
            "global_plddt_cif": None,
        }
        page = view.build_page(rec, "molstar", "cdn", None, "index.html")

        # 라벨 자체가 __ENGINEJS__ 라면 그 문자열이 '데이터로서' 페이지에 남는 것은
        # 맞다. 확인할 것은 엔진 스크립트가 제자리에 정확히 한 번만 들어갔는가다.
        check_equal(
            page.count("window.af3SetColor = "),
            1,
            "엔진 스크립트가 제자리에 한 번만 들어가지 않았다: %s" % label,
        )
        data_lines = [
            line for line in page.splitlines() if line.startswith("var AF3 = ")
        ]
        check_equal(len(data_lines), 1, "데이터 할당 줄이 정확히 1개가 아니다: %s" % label)
        payload = data_lines[0][len("var AF3 = "):].rstrip(";")
        try:
            parsed = json.loads(payload)
        except ValueError as exc:
            raise Failure(
                "타깃명 %r 때문에 뷰어 데이터 JSON 이 깨졌다: %s\n      실제: %s"
                % (label, exc, payload[:200])
            )
        check_equal(parsed["target"], label, "뷰어 데이터의 타깃명이 손상됐다")

        title_lines = [line for line in page.splitlines() if "<title>" in line]
        check_equal(len(title_lines), 1, "title 줄이 정확히 1개가 아니다: %s" % label)
        check_in(
            "AF3 구조 보기",
            title_lines[0],
            "타깃명이 <title> 을 갈라놓았다: %s" % label,
        )
