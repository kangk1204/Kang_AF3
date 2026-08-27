#!/usr/bin/env python3
"""집계·시각화 회귀 테스트 (과제 항목 11, 12).

이 모듈이 지키는 원칙: **관리용 폴더는 결과가 아니다. 같은 타깃은 한 번만 센다.**
집계가 틀리면 2000건 스크리닝의 상위 후보 선정 자체가 틀어진다. 그리고 이 오류는
숫자로만 보이므로 눈으로 발견하기 어렵다.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from harness import (
    REPO_ROOT,
    SCRIPTS_DIR,
    Failure,
    Workspace,
    check,
    check_equal,
    check_in,
    check_not_in,
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


@regression(
    item="reporting",
    prevents="라이브러리가 CSP 때문에 막혔는데 페이지가 '인터넷/CDN 문제'라고 안내해,\n"
             "사용자가 방화벽만 뒤지다 진짜 원인을 못 찾는 버그.",
)
def test_viewer_failure_message_names_csp_when_csp_is_the_cause():
    source = (SCRIPTS_DIR / "af3_view3d.py").read_text(encoding="utf-8")
    page = source.partition("PAGE_TMPL = ")[2].partition('\n"""\n')[0]
    check(page, "PAGE_TMPL 을 찾지 못했다")

    listener = page.find("securitypolicyviolation")
    check(listener != -1, "페이지가 CSP 위반을 기록하지 않는다")

    # 위반 기록은 라이브러리를 싣기 전에 걸어야 한다. 뒤에 걸면 정작 막힌 그 로드를
    # 놓친다 (이 순서가 뒤집히면 안내가 다시 네트워크 탓을 하게 된다).
    libbody = page.find("__LIBBODY__")
    check(libbody != -1, "PAGE_TMPL 에 __LIBBODY__ 자리가 없다")
    check(
        listener < libbody,
        "CSP 위반 기록을 라이브러리 로드보다 늦게 건다",
        "listener=%d libbody=%d" % (listener, libbody),
    )

    fail_block = page.partition("function af3Fail")[2].partition("\n}")[0]
    check(fail_block, "af3Fail 을 찾지 못했다")
    check_in("__af3_csp", fail_block, "실패 안내가 기록된 CSP 위반을 쓰지 않는다")
    check_in("CSP", fail_block, "실패 안내가 CSP 를 원인으로 이름 붙이지 않는다")

    # 모든 위반을 원인으로 삼으면 안 된다. 렌더링과 무관한 위반(예: connect-src)이
    # 늘 하나 기록되므로, 그것 때문에 엉뚱한 실패까지 CSP 탓이 된다.
    # 라이브러리를 실제로 못 싣게 만드는 지시어만 원인으로 본다.
    check_in(
        "__af3_csp_blocking",
        fail_block,
        "실패 안내가 라이브러리 로드를 막는 위반만 골라 쓰지 않는다",
    )
    for directive in ("script-src", "worker-src"):
        check_in(directive, page, "차단성 지시어 목록에 %s 가 없다" % directive)


@regression(
    item="reporting",
    prevents="템플릿 주석이나 안내문에 __LIBBODY__ 같은 자리표시자를 적어,\n"
             "5MB 라이브러리나 mmCIF 가 페이지에 두 번 들어가는 버그. 오류 없이 통과한다.",
)
def test_each_template_placeholder_appears_exactly_once():
    mod = load_module("af3_view3d.py")
    for name in ("PAGE_TMPL", "INDEX_TMPL"):
        template = getattr(mod, name)
        counts = {}
        for token in mod.PLACEHOLDER_RE.findall(template):
            counts[token] = counts.get(token, 0) + 1
        repeated = sorted(tok for tok, n in counts.items() if n > 1)
        check(
            not repeated,
            "%s 안에서 같은 자리표시자가 여러 번 나온다 (내용이 중복 삽입된다)" % name,
            ", ".join(repeated),
        )
        check(counts, "%s 에 자리표시자가 하나도 없다" % name)


@regression(
    item="reporting",
    prevents="커밋된 3D 예시가 생성기보다 오래돼, 생성기에서 고친 결함이 예시에는\n"
             "남아 있는 버그. 예시는 초보자가 제일 먼저 여는 파일이라 조용히 오래된다.",
)
def test_committed_viewer_example_matches_the_current_generator_shell():
    mod = load_module("af3_view3d.py")
    example = (REPO_ROOT / "examples" / "view3d_example.html").read_text(encoding="utf-8")

    # 자리표시자를 뺀 나머지(껍데기)는 타깃과 무관하게 항상 같아야 한다.
    # 데이터가 아니라 코드가 낡았는지만 본다.
    missing = []
    for segment in mod.PLACEHOLDER_RE.split(mod.PAGE_TMPL):
        chunk = segment.strip()
        if len(chunk) < 40:
            continue
        if chunk not in example:
            missing.append(chunk[:160])
    check(
        not missing,
        "커밋된 3D 예시에 현재 생성기의 껍데기가 없다 (예시를 다시 만들어야 한다)",
        "\n---\n".join(missing[:4]),
    )


@regression(
    item="reporting",
    prevents="'시점 초기화' 가 화면만 다시 맞추고 회전은 그대로 두는 버그.\n"
             "구조를 이상한 각도로 돌려 놓은 사용자는 처음 각도로 못 돌아온다.",
)
def test_reset_button_restores_the_first_view_not_just_the_framing():
    mod = load_module("af3_view3d.py")
    for name in ("ENGINE_MOLSTAR_JS", "ENGINE_3DMOL_JS"):
        engine = getattr(mod, name)
        check_in(
            "af3InitialView",
            engine,
            "%s 가 처음 시점을 저장하지 않는다" % name,
        )
        reset = engine.partition("window.af3ResetView = function")[2].partition(";\n")[0]
        check(reset, "%s 에서 af3ResetView 를 찾지 못했다" % name)
        check_in(
            "af3InitialView",
            reset,
            "%s 의 시점 초기화가 저장해 둔 처음 시점을 쓰지 않는다" % name,
        )


@regression(
    item="reporting",
    prevents="--lib cdn 과 --lib embed 가 화면 동작까지 달라져,\n"
             "한쪽에서만 확인한 버튼·카메라 수정이 다른 쪽에는 안 들어가는 버그.",
)
def test_library_mode_changes_only_how_the_library_is_loaded():
    mod = load_module("af3_view3d.py")
    # lib 모드는 라이브러리를 어디서 싣는지만 정한다. 엔진 코드(버튼, 카메라, 색칠)는
    # 같아야 한다. 같으므로 브라우저 확인을 한쪽에서만 해도 다른 쪽에 그대로 적용된다.
    for engine in ("molstar", "3dmol"):
        engine_js = mod.ENGINE_MOLSTAR_JS if engine == "molstar" else mod.ENGINE_3DMOL_JS
        for handle in ("window.af3SetColor", "window.af3ResetView", "af3InitialView"):
            check_in(handle, engine_js, "%s 엔진에 %s 가 없다" % (engine, handle))
        check(
            "lib_mode" not in engine_js and "__LIBBODY__" not in engine_js,
            "%s 엔진 코드가 라이브러리 적재 방식에 따라 갈린다" % engine,
        )


@regression(
    item="reporting",
    prevents="요약 그림의 산점도가 복합체에서도 pTM 을 그려,\n"
             "계면이 실패한 건(ipTM 0.18)이 '오른쪽 위 = 좋은 후보' 자리에 찍히는 버그.\n"
             "등급은 C_계면실패인데 그림만 보면 상위 후보로 읽힌다.",
)
def test_summary_scatter_uses_the_interface_metric_for_complexes():
    mod = load_module("af3_visualize.py")
    check(hasattr(mod, "scatter_metric"), "산점도 지표를 고르는 함수가 없다")

    # 실측에서 나온 형태: pTM 은 높은데 ipTM 이 무너진 복합체.
    complexes = [
        {"name": "nb_1kxv", "ptm": 0.81, "iptm": 0.18, "mean_atom_plddt": 90.7},
        {"name": "nb_1kxq", "ptm": 0.96, "iptm": 0.92, "mean_atom_plddt": 96.2},
    ]
    xs, ys, names, key = mod.scatter_metric(complexes)
    check_equal(key, "iptm", "복합체인데 계면 지표를 쓰지 않았다")
    check_equal(xs[names.index("nb_1kxv")], 0.18,
                "계면이 실패한 건을 pTM 으로 그려 좋은 후보처럼 보이게 했다")

    # 단량체만 있으면 pTM 이 맞다 (ipTM 이 없다).
    monomers = [{"name": "vhh", "ptm": 0.9, "iptm": None,
                 "mean_atom_plddt": 92.4, "n_chain": 1}]
    xs, ys, names, key = mod.scatter_metric(monomers)
    check_equal(key, "ptm", "단량체인데 pTM 을 쓰지 않았다")
    check_equal(xs, [0.9], "단량체의 pTM 을 그리지 않았다")

    # 섞여 있으면 각자 자기 지표를 쓰되 축 이름이 그 사실을 밝혀야 한다.
    mixed = monomers + complexes
    xs, ys, names, key = mod.scatter_metric(mixed)
    check_equal(key, "ptm_or_iptm", "섞인 경우를 축 이름으로 밝히지 않았다")
    check_equal(xs[names.index("nb_1kxv")], 0.18, "섞인 경우에도 복합체는 ipTM 이어야 한다")
    check_equal(xs[names.index("vhh")], 0.9, "섞인 경우 단량체는 pTM 이어야 한다")
    check_in("ipTM", mod.L[key][0], "축 이름이 ipTM 을 언급하지 않는다")


@regression(
    item="reporting",
    prevents="사슬이 여럿인데 ipTM 이 없는 결과를 단량체처럼 pTM 으로 그려,\n"
             "계면을 평가하지 못한 건이 좋은 후보로 보이는 버그.\n"
             "ipTM 이 없다는 것은 '단량체' 가 아니라 '계면을 알 수 없음' 일 수 있다.",
)
def test_scatter_does_not_treat_a_multichain_target_as_a_monomer():
    mod = load_module("af3_visualize.py")
    rows = [
        {"name": "복합체인데 ipTM 없음", "ptm": 0.91, "iptm": None,
         "mean_atom_plddt": 90.0, "n_chain": 2},
        {"name": "정상 단량체", "ptm": 0.90, "iptm": None,
         "mean_atom_plddt": 92.0, "n_chain": 1},
    ]
    xs, ys, names, key = mod.scatter_metric(rows)
    check(
        "복합체인데 ipTM 없음" not in names,
        "사슬이 여럿인데 ipTM 이 없는 건을 pTM 으로 그렸다",
        f"그려진 것={names}",
    )
    check_in("정상 단량체", names, "진짜 단량체까지 빼 버렸다")


@regression(
    item="reporting",
    prevents="ipTM null을 단량체로 단정하거나 다중 사슬/사슬 수 불명 omission을 조용히 숨기는 버그.",
)
def test_missing_iptm_uses_one_tri_state_contract_everywhere():
    vis = load_module("af3_visualize.py")
    rows = [
        {"name": "single", "ptm": 0.8, "iptm": None,
         "mean_atom_plddt": 90.0, "n_chain": 1},
        {"name": "multi", "ptm": 0.9, "iptm": None,
         "mean_atom_plddt": 91.0, "n_chain": 2},
        {"name": "unknown", "ptm": 0.7, "iptm": None,
         "mean_atom_plddt": 89.0, "n_chain": None},
    ]
    xs, _ys, names, key, omitted = vis.scatter_metric_details(rows)
    check_equal(xs, [0.8], "known single만 pTM으로 그려야 한다")
    check_equal(names, ["single"], "multi/unknown missing-ipTM을 산점도에서 제외하지 않았다")
    check_equal(key, "ptm", "known single의 축은 pTM이어야 한다")
    check_equal(
        omitted,
        [("multi", "다중 사슬; ipTM 누락"), ("unknown", "사슬 수 불명; ipTM 누락")],
        "산점도 omission 이유가 tri-state 계약과 다르다",
    )
    check_equal(vis.infer_chain_count({}), None, "근거 없는 사슬 수를 0/1로 만들었다")
    check_equal(vis.infer_chain_count({"chain_pair_iptm": [[1, 0], [0, 1]]}), 2,
                "chain-pair 행렬에서 known-multi를 복원하지 못했다")

    viewer = load_module("af3_view3d.py")
    base = {"summary": {"iptm": None}, "chains": [], "residues": [],
            "mean_plddt": None, "min_plddt": None, "n_sample": 0,
            "sample_sd": None, "n_atom": 0}
    single = dict(base, chains=["A"])
    multi = dict(base, chains=["A", "B"])
    unknown = dict(base)
    check_equal(viewer.iptm_display(single), ("해당 없음", "single"), "single 상태 오류")
    check_equal(viewer.iptm_display(multi), ("누락", "multi_missing"), "multi 상태 오류")
    check_equal(viewer.iptm_display(unknown), ("불명", "unknown"), "unknown 상태 오류")
    check_in("계면을 평가할 수 없다", str(viewer.metric_rows(multi)),
             "개별 viewer가 다중 사슬 missing-ipTM 이유를 숨긴다")


@regression(
    item="reporting",
    prevents="AF3-derived figure/viewer를 생성하면서 Output Terms 사본과 정확한 법적 고지를 떼어내는 버그.",
)
def test_output_terms_are_pinned_exact_and_propagated_by_generators():
    required = ["OUTPUT_NOTICE.md", "OUTPUT_TERMS_OF_USE.md",
                "LEGALLY_BINDING_TERMS_OF_USE.txt"]
    terms = (REPO_ROOT / "OUTPUT_TERMS_OF_USE.md").read_text(encoding="utf-8")
    check_in("Last Modified: 2024-11-09", terms, "pinned Output Terms 판본이 없다")
    legal = (REPO_ROOT / "LEGALLY_BINDING_TERMS_OF_USE.txt").read_text(encoding="utf-8")
    exact = (
        "By using this information, you agree to AlphaFold 3 Output Terms of\n"
        "Use found at\n"
        "https://github.com/google-deepmind/alphafold3/blob/main/OUTPUT_TERMS_OF_USE.md."
    )
    check_in(exact, legal, "약관 5항의 exact legally-binding notice가 없다")
    check_in("Abramson J", (REPO_ROOT / "OUTPUT_NOTICE.md").read_text(encoding="utf-8"),
             "필수 AF3 논문 인용이 없다")
    check((REPO_ROOT / "examples" / "OUTPUT_NOTICE.txt").is_file(),
          "examples sidecar notice가 없다")
    check_in("AlphaFold 3 Output Terms 적용",
             (REPO_ROOT / "examples" / "view3d_example.html").read_text(encoding="utf-8"),
             "배포 예시 viewer 내부에 conspicuous notice가 없다")

    for script in ("af3_visualize.py", "af3_view3d.py"):
        mod = load_module(script)
        with tempfile.TemporaryDirectory(prefix="af3_terms_") as directory:
            mod.propagate_output_terms(directory)
            for name in required:
                generated = Path(directory) / name
                check(generated.is_file(), f"{script}가 {name}을 전달하지 않았다")
                check_equal(generated.read_bytes(), (REPO_ROOT / name).read_bytes(),
                            f"{script}가 {name} 내용을 바꿨다")


@regression(
    item="reporting",
    prevents="tracked figure의 source/generator/hash를 잃거나 원자료 없는 역사적 artifact를 재현 가능하다고 꾸미는 버그.",
)
def test_artifact_manifest_closes_lineage_without_fabricating_sources():
    manifest_path = REPO_ROOT / "ARTIFACT_MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    records = {item["path"]: item for item in manifest["artifacts"]}
    tracked = {str(path.relative_to(REPO_ROOT)) for path in (REPO_ROOT / "figures").glob("*.png")}
    check_equal(set(records) & tracked, tracked, "manifest에서 tracked PNG가 빠졌다")
    for path in sorted(tracked):
        record = records[path]
        actual = hashlib.sha256((REPO_ROOT / path).read_bytes()).hexdigest()
        check_equal(record["sha256"], actual, f"artifact hash가 stale이다: {path}")
        if record["status"] == "reproducible_from_tracked_sources":
            check(record["sources"], f"재현 가능 artifact에 source가 없다: {path}")
            for source in record["sources"]:
                source_path = REPO_ROOT / source["path"]
                check(source_path.is_file(), f"manifest source가 없다: {source['path']}")
                check_equal(source["sha256"], hashlib.sha256(source_path.read_bytes()).hexdigest(),
                            f"source hash가 stale이다: {source['path']}")
        else:
            check_equal(record["status"], "historical_not_reproducible",
                        f"알 수 없는 artifact status: {path}")
            check(record.get("reason"), f"역사적 artifact의 비재현 이유가 없다: {path}")
    check_equal(records["figures/view3d_screenshot.png"]["sources"], [],
                "원 browser capture가 없는 screenshot에 source를 꾸며 넣었다")
    for crop in (
        "figures/view3d_index_table.png",
        "figures/view3d_molstar_target.png",
    ):
        record = records[crop]
        check_equal(
            record["sources"][0]["path"],
            "figures/view3d_screenshot.png",
            f"README browser crop의 source lineage가 없다: {crop}",
        )
        check_equal(
            record.get("transform", {}).get("operation"),
            "pixel_crop",
            f"README browser crop 좌표 변환이 기록되지 않았다: {crop}",
        )
    builder = (REPO_ROOT / "scripts" / "build_reference_artifacts.py").read_text(encoding="utf-8")
    check_in("Peak VRAM (MiB)", builder, "baseline builder가 MiB 단위를 고정하지 않았다")
    check_not_in('set_ylabel("Peak VRAM (GB)")', builder, "baseline builder에 GB 혼용이 남았다")
    checked = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "build_reference_artifacts.py"), "--check"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    check_equal(checked.returncode, 0, "artifact --check가 실패했다", checked.stdout + checked.stderr)


@regression(
    item="reporting",
    prevents="문서가 selection-biased 소표본을 grade 안정성/후보 필터 validation으로 승격하거나 stale path를 유지하는 버그.",
)
def test_scientific_claim_gates_and_document_paths_are_explicit():
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    guide = (REPO_ROOT / "docs" / "researcher_guide.md").read_text(encoding="utf-8")
    report = (REPO_ROOT / "docs" / "benchmark_report.md").read_text(encoding="utf-8")
    combined = readme + "\n" + guide
    check_not_in("거르는 용도로는 그래서 쓸 만하다", combined,
                 "selection-biased 표본을 filtering validation으로 주장한다")
    check_not_in("거르는 용도로 overlay 를 쓰는 근거", combined,
                 "grade 비전환을 filtering 근거로 주장한다")
    for phrase in ("exploratory prioritization", "target(입력 분자 조합)",
                   "binder recovery", "Kd/IC50", "native interface accuracy"):
        check_in(phrase, readme, f"estimand/claim boundary가 빠졌다: {phrase}")
    check_in("calibration되지 않은 local heuristic", readme,
             "atom-weighted grade의 local heuristic 고지가 없다")
    check_in("within-run diffusion-sample", readme,
             "diffusion range를 재현성 uncertainty와 구분하지 않았다")
    check_not_in("`af3_결과요약.csv`", report, "존재하지 않는 historical path가 남았다")
    check_in("`results_example/af3_summary.csv`", report, "canonical summary path가 없다")


@regression(
    item="docs",
    prevents="수동 model 설치가 checksum 검증 전에 기존 af3.bin을 덮어쓰는 버그.",
)
def test_manual_model_download_verifies_staging_before_atomic_publish():
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    start = readme.index("### 3-3. 모델 가중치 확보")
    end = readme.index("### 3-4.", start)
    section = readme[start:end]
    check_not_in("zstd -d -f ~/af3_models/af3.bin.zst -o ~/af3_models/af3.bin", section,
                 "검증 전에 최종 model을 직접 덮는다")
    check_in("mktemp ~/af3_models/.af3.bin.", section, "same-directory staging이 없다")
    check_in("sha256sum -c -", section, "staged model SHA-256 검증이 없다")
    check_in("mv -T --no-clobber", section, "검증 뒤 no-clobber atomic publish가 없다")
    check(section.index("sha256sum -c -") < section.index("mv -T --no-clobber"),
          "SHA-256 검증보다 publish가 먼저다")
