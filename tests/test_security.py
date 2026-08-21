#!/usr/bin/env python3
"""Viewer, generated-command, and artifact trust-boundary regressions."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from harness import (
    Workspace,
    check,
    check_equal,
    check_in,
    default_args,
    load_module,
    regression,
    run_script,
)


@regression(
    item="security",
    prevents="mmCIF 사슬 ID의 </script> 문자열이 viewer 데이터 script를 닫아 저장형 XSS가 되는 버그.",
)
def test_viewer_script_json_escapes_html_end_tags():
    mod = load_module("af3_view3d.py")
    payload = "</script><script>globalThis.AF3_XSS=1</script>"
    encoded = mod.script_safe_json({"chains": [payload]})
    check("</script" not in encoded.lower(), "script 종료 태그가 JSON 문맥에 남았다", encoded)
    check_in("\\u003c", encoded, "< 문자를 JavaScript 보존형 escape로 바꾸지 않았다")


@regression(
    item="security",
    prevents="PyMOL/ChimeraX 스크립트에 줄바꿈이 든 타깃명이나 경로가 삽입돼 임의 명령이 실행되는 버그.",
)
def test_viewer_command_scripts_reject_control_characters():
    mod = load_module("af3_visualize.py")
    with tempfile.TemporaryDirectory(prefix="af3_cmd_inject_") as td:
        root = Path(td)
        cif = root / "model.cif"
        cif.write_text("data_x\n", encoding="utf-8")
        try:
            mod.write_viewer_scripts(root, [("safe\nrun payload.py", str(cif))], "ok", root)
        except ValueError as exc:
            check_in("unsafe", str(exc).lower(), "명령 주입 거부 이유가 불명확하다")
        else:
            check(False, "제어문자가 든 뷰어 객체명을 허용했다")


@regression(
    item="security",
    prevents="--index-name 절대경로/../ 또는 타깃 파일명 충돌로 출력 폴더 밖 파일이나 다른 HTML을 덮어쓰는 버그.",
)
def test_viewer_output_names_are_single_and_collision_free():
    mod = load_module("af3_view3d.py")
    for bad in ("../x.html", "/tmp/x.html", "", ".", "a/b.html"):
        try:
            mod.output_basename(bad)
        except ValueError:
            pass
        else:
            check(False, f"위험한 index 이름을 허용했다: {bad!r}")
    check_equal(mod.output_basename("index.html"), "index.html", "정상 index 이름을 거부했다")
    try:
        mod.plan_output_names(["a b", "a?b"], "index.html")
    except ValueError as exc:
        check_in("충돌", str(exc), "파일명 충돌을 설명하지 않았다")
    else:
        check(False, "정규화 후 같은 HTML 이름을 허용했다")


@regression(
    item="security",
    prevents="결과 폴더 안 symlink 산출물이 외부 호스트 파일을 HTML이나 viewer script에 포함하는 버그.",
)
def test_postprocessors_reject_symlinked_result_artifacts():
    view = load_module("af3_view3d.py")
    vis = load_module("af3_visualize.py")
    with tempfile.TemporaryDirectory(prefix="af3_artifact_link_") as td:
        root = Path(td)
        target = root / "target"
        target.mkdir()
        external = root / "outside.cif"
        external.write_text("data_secret\n", encoding="utf-8")
        link = target / "x_model.cif"
        link.symlink_to(external)
        for mod in (view, vis):
            check(not mod.is_safe_artifact_file(link, target), f"{mod.__name__}가 외부 symlink를 허용했다")


@regression(
    item="security",
    prevents="고정 버전 CDN/캐시 파일이 변조돼도 크기만 보고 신뢰해 연구 구조를 읽는 JavaScript로 실행하는 버그.",
)
def test_embedded_library_integrity_is_enforced():
    mod = load_module("af3_view3d.py")
    good = b"known bytes"
    digest = mod.sha256_bytes(good)
    check(mod.verify_asset_bytes(good, {digest}), "허용된 해시를 거부했다")
    check(not mod.verify_asset_bytes(b"tampered", {digest}), "변조된 라이브러리를 허용했다")


@regression(
    item="security",
    prevents="잠금 파일 symlink를 열고 truncate해 출력 폴더 밖 파일을 덮어쓰는 버그.",
)
def test_output_lock_refuses_symlink_without_overwrite():
    mod = load_module("run_af3_batch_improved.py")
    with tempfile.TemporaryDirectory(prefix="af3_lock_link_") as td:
        root = Path(td)
        external = root / "external.txt"
        external.write_text("keep-me", encoding="utf-8")
        output = root / "out"
        output.mkdir()
        (output / ".run_af3_batch.lock").symlink_to(external)
        try:
            with mod.output_lock(output):
                pass
        except OSError:
            pass
        else:
            check(False, "symlink 잠금 파일을 허용했다")
        check_equal(external.read_text(encoding="utf-8"), "keep-me", "외부 파일을 덮어썼다")


@regression(
    item="security",
    prevents="--cleanup이 소유 marker가 없는 사용자 파일까지 .af3_incomplete와 함께 삭제하는 버그.",
)
def test_cleanup_preserves_unmanaged_quarantine_content():
    workspace = Workspace()
    try:
        keep = workspace.output_dir / ".af3_incomplete" / "manual" / "keep.txt"
        keep.parent.mkdir(parents=True)
        keep.write_text("preserve", encoding="utf-8")
        proc = run_script(
            "run_af3_batch_improved.py",
            default_args(workspace, "--cleanup", "--yes"),
            workspace,
        )
        check_equal(proc.returncode, 0, "안전한 cleanup 이 실패했다", proc.stdout[-1200:])
        check(keep.is_file(), "소유하지 않은 격리 파일을 삭제했다")
    finally:
        workspace.cleanup()


@regression(
    item="security",
    prevents="Docker가 최종 파일을 쓴 뒤 nonzero로 종료해도 산출물만 보고 전체 성공으로 반환하는 버그.",
)
def test_nonzero_docker_exit_is_sticky_after_finals():
    workspace = Workspace()
    try:
        workspace.write_json("a.json", workspace.monomer("a"))
        proc = run_script(
            "run_af3_batch_improved.py",
            default_args(workspace, "--docker", "docker", "--yes"),
            workspace,
            env_extra={"AF3_STUB_EXIT_AFTER_FINALS": "1"},
        )
        check(proc.returncode != 0, "Docker nonzero 종료를 성공으로 바꿨다")
        check_in("0이 아닌", proc.stdout, "sticky failure 원인을 설명하지 않았다")
    finally:
        workspace.cleanup()


@regression(
    item="security",
    prevents="sidecar가 내부 .af3_stage_marker 이름과 충돌해 원래 입력이 조용히 사라지는 버그.",
)
def test_staging_rejects_internal_marker_collision():
    workspace = Workspace()
    try:
        job = workspace.monomer("a")
        job["userCCDPath"] = ".af3_stage_marker"
        workspace.write_json("a.json", job)
        marker_like = workspace.input_dir / ".af3_stage_marker"
        marker_like.write_text("user-data", encoding="utf-8")
        proc = run_script(
            "run_af3_batch_improved.py",
            default_args(workspace, "--docker", "docker", "--yes"),
            workspace,
        )
        check(proc.returncode != 0, "내부 marker와 sidecar 충돌을 허용했다")
        check_in("충돌", proc.stdout, "staging 충돌 원인을 설명하지 않았다")
        check_equal(marker_like.read_text(encoding="utf-8"), "user-data", "원본 sidecar를 바꿨다")
    finally:
        workspace.cleanup()


@regression(
    item="security",
    prevents="사용자 지정 조건명이나 폴더명이 CSV 수식으로 저장돼 Excel에서 열 때 명령/링크 수식이 실행되는 버그.",
)
def test_csv_exports_escape_spreadsheet_formula_cells():
    for script in (
        "af3_collect.py",
        "af3_visualize.py",
        "af3_prepare.py",
        "af3_stage2.py",
        "af3_rankcorr.py",
        "af3_batch.py",
    ):
        mod = load_module(script)
        check_equal(mod.csv_safe_cell("=1+1"), "'=1+1", f"{script}가 = 수식을 escape하지 않았다")
        check_equal(mod.csv_safe_cell("  @SUM(A1)"), "'  @SUM(A1)", f"{script}가 공백 뒤 수식을 escape하지 않았다")
        check_equal(mod.csv_safe_cell("normal"), "normal", f"{script}가 정상 셀을 바꿨다")
        check_equal(mod.csv_safe_cell(3.5), 3.5, f"{script}가 숫자 셀을 바꿨다")

    workspace = Workspace()
    try:
        workspace.make_result("safe_target")
        output = workspace.root / "summary.csv"
        proc = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve().parents[1] / "scripts" / "af3_collect.py"),
                f"+CMD={workspace.output_dir}",
                "-o",
                str(output),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        check_equal(proc.returncode, 0, "수식형 조건명 fixture 집계가 실패했다", proc.stderr)
        raw = output.read_text(encoding="utf-8-sig")
        check("\n+CMD," not in raw, "CSV에 실행 가능한 수식형 조건명이 남았다", raw)
        check_in("\n'+CMD,", raw, "CSV writer가 수식형 조건명 앞에 apostrophe를 붙이지 않았다")
    finally:
        workspace.cleanup()


@regression(
    item="security",
    prevents="af3_prepare --overwrite가 기존 JSON symlink를 따라가 출력 폴더 밖 파일을 truncate하는 버그.",
)
def test_prepare_overwrite_replaces_symlink_without_touching_target():
    with tempfile.TemporaryDirectory(prefix="af3_prepare_link_") as td:
        root = Path(td)
        fasta = root / "panel.fasta"
        fasta.write_text(">x\nACDEFGHIKLMN\n", encoding="utf-8")
        output = root / "out"
        output.mkdir()
        external = root / "outside.txt"
        external.write_text("keep-me", encoding="utf-8")
        generated = output / "01_x.json"
        generated.symlink_to(external)
        proc = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve().parents[1] / "scripts" / "af3_prepare.py"),
                "--fasta",
                str(fasta),
                "-o",
                str(output),
                "--overwrite",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        check_equal(proc.returncode, 0, "prepare overwrite fixture가 실패했다", proc.stderr)
        check_equal(external.read_text(encoding="utf-8"), "keep-me", "symlink 외부 대상을 덮어썼다")
        check(generated.is_file() and not generated.is_symlink(), "symlink를 일반 JSON으로 안전하게 교체하지 않았다")


@regression(
    item="security",
    prevents="af3_stage2의 고정 .json.tmp symlink가 출력 폴더 밖 파일을 덮어쓰고 최종 JSON도 symlink로 만드는 버그.",
)
def test_stage2_temporary_file_cannot_follow_symlink():
    with tempfile.TemporaryDirectory(prefix="af3_stage2_link_") as td:
        root = Path(td)
        inputs = root / "inputs"
        output = root / "stage2"
        inputs.mkdir()
        output.mkdir()
        (inputs / "x.json").write_text(
            '{"name":"x","modelSeeds":[1],"sequences":[{"protein":{"id":"A","sequence":"ACDE"}}],"dialect":"alphafold3","version":1}\n',
            encoding="utf-8",
        )
        names = root / "names.txt"
        names.write_text("x\n", encoding="utf-8")
        external = root / "outside.txt"
        external.write_text("keep-me", encoding="utf-8")
        (output / "1_x.json.tmp").symlink_to(external)
        proc = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve().parents[1] / "scripts" / "af3_stage2.py"),
                "--list",
                str(names),
                "--source",
                "input",
                "--input-dir",
                str(inputs),
                "-o",
                str(output),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        check_equal(proc.returncode, 0, "stage2 symlink fixture가 실패했다", proc.stdout + proc.stderr)
        check_in(
            str(Path(__file__).resolve().parents[1] / "scripts" / "run_af3_batch_improved.py"),
            proc.stdout,
            "stage2가 복사 가능한 권장 러너 경로를 안내하지 않았다",
        )
        check_equal(external.read_text(encoding="utf-8"), "keep-me", "고정 temp symlink 외부 대상을 덮어썼다")
        final = output / "1_x.json"
        check(final.is_file() and not final.is_symlink(), "최종 stage2 JSON이 일반 파일이 아니다")
        mod = load_module("af3_stage2.py")
        check_equal(mod.suggested_output_dir(Path("pilot_in")), Path("pilot_out"), "_in을 _out으로 바꾸지 않았다")
