#!/usr/bin/env python3
"""Viewer, generated-command, and artifact trust-boundary regressions."""

from __future__ import annotations

import re
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from harness import (
    REPO_ROOT,
    SCRIPTS_DIR,
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
    prevents="커밋된 3D 예시가 생성기보다 오래돼 CSP와 CDN SRI 없이 배포되는 버그.",
)
def test_committed_viewer_example_has_csp_and_sri():
    html = (REPO_ROOT / "examples" / "view3d_example.html").read_text(encoding="utf-8")
    check_in("Content-Security-Policy", html, "3D 예시에 CSP가 없다")
    check_in("default-src 'none'", html, "3D 예시 CSP가 기본 외부 로드를 막지 않는다")
    check_equal(html.count('integrity="sha384-'), 2, "3D 예시의 CDN CSS/JS에 SRI가 모두 없다")
    check_equal(html.count('crossorigin="anonymous"'), 2, "3D 예시의 SRI CORS 설정이 불완전하다")


@regression(
    item="security",
    prevents="CSP를 조이다가 molstar 가 쓰는 new Function 을 막아,\n"
             "브라우저에서 지표 표만 뜨고 구조는 영영 안 나오는 버그. 정적 검사로는 통과한다.",
)
def test_viewer_csp_allows_what_the_molstar_engine_needs():
    source = (SCRIPTS_DIR / "af3_view3d.py").read_text(encoding="utf-8")
    policies = re.findall(r'http-equiv="Content-Security-Policy" content="([^"]+)"', source)
    check(policies, "생성기에 CSP 메타 태그가 없다")

    script_policies = [p for p in policies if "script-src" in p]
    check_equal(len(script_policies), 1, "스크립트를 싣는 CSP가 하나가 아니다", "\n".join(policies))
    policy = script_policies[0]

    # molstar 5.11.0 번들은 초기화 중에 new Function(...) 을 부른다. 'unsafe-eval' 이
    # 없으면 EvalError 로 죽고, 페이지는 "molstar 전역이 없다" 만 띄운다. 실제 Chrome
    # 에서 확인한 증상이다. 소스는 CDN 두 곳으로 고정돼 있고 SRI 로 잠겨 있으므로
    # 여기서 허용되는 것은 그 고정된 번들뿐이다.
    check_in("'unsafe-eval'", policy, "CSP가 molstar 초기화에 필요한 eval 을 막는다")
    for required in ("worker-src blob:", "img-src data: blob:"):
        check_in(required, policy, "CSP가 molstar 렌더링에 필요한 것을 막는다")
    # molstar 는 초기화 중 data: URI 를 fetch 한다. connect-src 'none' 이면 브라우저가
    # 매번 위반을 기록한다. 화면은 뜨지만, 위반이 항상 남아 있으면 실패 안내가 무관한
    # 원인을 CSP 탓으로 돌리게 된다. data: 는 바깥으로 나가지 않으므로 허용해도 안전하다.
    connect = [d.strip() for d in policy.split(";") if d.strip().startswith("connect-src")]
    check_equal(len(connect), 1, "connect-src 지시어가 하나가 아니다", policy)
    check_in("data:", connect[0], "CSP가 molstar 의 data: 요청을 막아 위반이 계속 쌓인다")
    check("http" not in connect[0], "connect-src 가 바깥 출처로 열렸다", connect[0])
    # 조인 상태는 유지해야 한다. 허용 목록이 넓어지면 이 검사가 잡는다.
    check_in("default-src 'none'", policy, "CSP 기본값이 열려 있다")
    check("*" not in policy, "CSP에 와일드카드 출처가 들어갔다", policy)

    example = (REPO_ROOT / "examples" / "view3d_example.html").read_text(encoding="utf-8")
    check_in(
        policy,
        example,
        "커밋된 3D 예시의 CSP가 생성기와 다르다 (예시가 오래됐거나 손으로 고쳐졌다)",
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
        check_in("종료코드 1", proc.stdout, "sticky failure 원인을 설명하지 않았다")
        provenance = (
            workspace.output_dir / "a" / "a_af3run_provenance.json"
        )
        check(not provenance.exists(), "nonzero producer 결과에 provenance를 commit했다")
        audit = run_script(
            "run_af3_batch_improved.py",
            default_args(workspace, "--audit"),
            workspace,
        )
        check(audit.returncode != 0, "nonzero producer 결과를 다음 audit이 완료로 재사용했다")
        check_in("미완료 1개", audit.stdout, "실패 결과가 pending으로 남지 않았다")
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


@regression(
    item="security",
    prevents=(
        "af3_prepare 의 --report CSV 가 원자적으로 쓰이지 않고 목적지 symlink 를 "
        "따라가, 요약표 저장이 출력 폴더 밖 파일을 덮어쓰는 버그. 입력 JSON 쓰기는 "
        "atomic_write_json 으로 막혀 있는데 리포트만 열려 있었다."
    ),
)
def test_prepare_report_does_not_follow_a_symlinked_destination():
    with tempfile.TemporaryDirectory(prefix="af3_prepare_report_") as td:
        root = Path(td)
        fasta = root / "panel.fasta"
        fasta.write_text(">x\nACDEFGHIKLMN\n", encoding="utf-8")
        external = root / "outside.csv"
        external.write_text("keep-me", encoding="utf-8")
        report = root / "report.csv"
        report.symlink_to(external)
        proc = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS_DIR / "af3_prepare.py"),
                "--fasta", str(fasta),
                "-o", str(root / "out"),
                "--report", str(report),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        check_equal(proc.returncode, 0, "prepare report fixture가 실패했다", proc.stderr[-1200:])
        check_equal(
            external.read_text(encoding="utf-8"),
            "keep-me",
            "--report 가 symlink 외부 대상을 덮어썼다",
        )
        check(
            report.is_file() and not report.is_symlink(),
            "--report 가 symlink 를 일반 파일로 교체하지 않았다",
        )
        check_in("토큰수", report.read_text(encoding="utf-8-sig"), "리포트 내용이 비었다")


@regression(
    item="prepare",
    prevents=(
        "-o 가 이미 일반 파일이면 check_outdir 가 NotADirectoryError 트레이스백을 "
        "그대로 토해내는 버그. 이 스크립트의 다른 모든 오류 경로는 사람이 읽는 "
        "한 줄 안내로 끝난다."
    ),
)
def test_prepare_rejects_a_non_directory_output_path_with_a_readable_error():
    with tempfile.TemporaryDirectory(prefix="af3_prepare_outdir_") as td:
        root = Path(td)
        fasta = root / "panel.fasta"
        fasta.write_text(">x\nACDEFGHIKLMN\n", encoding="utf-8")
        occupied = root / "already_a_file"
        occupied.write_text("not a directory", encoding="utf-8")
        proc = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS_DIR / "af3_prepare.py"),
                "--fasta", str(fasta),
                "-o", str(occupied),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        report = proc.stdout + proc.stderr
        check(proc.returncode != 0, "출력 경로가 폴더가 아닌데 성공했다", report[-1200:])
        check(
            "Traceback" not in report,
            "출력 경로 오류가 트레이스백으로 새어 나왔다",
            report[-1200:],
        )
        check_in("폴더가 아니다", report, "출력 경로 문제의 원인을 설명하지 않았다")
        check_equal(
            occupied.read_text(encoding="utf-8"),
            "not a directory",
            "출력 경로에 있던 파일을 건드렸다",
        )


@regression(
    item="security",
    prevents="stage2 의 선정내역 CSV 가 symlink 를 따라가 바깥 파일을 덮어쓰는 버그.\n"
             "결과 폴더 안에 링크만 심어 두면 임의 경로가 지워진다.",
)
def test_stage2_manifest_does_not_follow_a_symlink():
    mod = load_module("af3_stage2.py")
    check(hasattr(mod, "open_manifest"), "선정내역 CSV 를 여는 지점이 분리돼 있지 않다")
    with tempfile.TemporaryDirectory(prefix="af3_manifest_") as td:
        root = Path(td)
        outside = root / "outside.txt"
        outside.write_text("건드리면 안 되는 내용", encoding="utf-8")
        outdir = root / "out"
        outdir.mkdir()
        link = outdir / "manifest.csv"
        link.symlink_to(outside)

        try:
            with mod.open_manifest(link) as handle:
                handle.write("덮어쓰기 시도")
        except OSError:
            pass
        else:
            check(False, "symlink 대상에 그대로 썼다")

        check_equal(
            outside.read_text(encoding="utf-8"),
            "건드리면 안 되는 내용",
            "symlink 를 따라가 바깥 파일을 덮어썼다",
        )


@regression(
    item="security",
    prevents="stage2 manifest가 hardlink 피해자를 truncate하거나 FIFO에서 무기한 멈추고, 실패 중 반쪽 CSV를 게시하는 버그.",
)
def test_stage2_manifest_is_atomic_and_rejects_hardlinks_and_special_files():
    mod = load_module("af3_stage2.py")
    with tempfile.TemporaryDirectory(prefix="af3_manifest_atomic_") as td:
        root = Path(td)
        victim = root / "victim.txt"
        victim.write_text("KEEP", encoding="utf-8")
        hardlink = root / "hard.csv"
        os.link(victim, hardlink)
        try:
            with mod.open_manifest(hardlink) as handle:
                handle.write("BAD")
        except OSError:
            pass
        else:
            check(False, "hardlink manifest를 허용했다")
        check_equal(victim.read_text(encoding="utf-8"), "KEEP", "hardlink 피해자를 truncate했다")

        fifo = root / "fifo.csv"
        os.mkfifo(fifo)
        try:
            with mod.open_manifest(fifo):
                pass
        except OSError:
            pass
        else:
            check(False, "FIFO manifest를 허용했다")

        manifest = root / "manifest.csv"
        manifest.write_text("OLD", encoding="utf-8")
        try:
            with mod.open_manifest(manifest) as handle:
                handle.write("PARTIAL")
                raise RuntimeError("simulated writer crash")
        except RuntimeError:
            pass
        check_equal(manifest.read_text(encoding="utf-8"), "OLD", "실패 중 반쪽 manifest를 게시했다")
        with mod.open_manifest(manifest) as handle:
            handle.write("NEW")
        check_equal(manifest.read_text(encoding="utf-8-sig"), "NEW", "완성 manifest를 원자 게시하지 못했다")
        direct = mod.open_manifest(manifest)
        direct.write("DIRECT")
        direct.close()
        check_equal(manifest.read_text(encoding="utf-8-sig"), "DIRECT", "기존 직접 close API가 깨졌다")


@regression(
    item="security",
    prevents="stage2가 파일명만 맞는 다른 타깃 JSON, symlink, 또는 여러 후보 중 첫 파일을 MSA 원본으로 고르는 버그.",
)
def test_stage2_source_requires_regular_unique_internal_identity():
    mod = load_module("af3_stage2.py")
    with tempfile.TemporaryDirectory(prefix="af3_stage2_identity_") as td:
        root = Path(td)
        out = root / "out"
        target_dir = out / "wanted"
        target_dir.mkdir(parents=True)
        wrong = target_dir / "wanted_data.json"
        wrong.write_text('{"name":"other","sequences":[]}', encoding="utf-8")
        try:
            mod.find_data_json("wanted", out, None)
        except ValueError as exc:
            check_in("내부 name", str(exc), "내부 identity 불일치 원인을 설명하지 않았다")
        else:
            check(False, "파일명만 맞는 다른 타깃 JSON을 허용했다")

        wrong.unlink()
        real = root / "real.json"
        real.write_text('{"name":"wanted","sequences":[]}', encoding="utf-8")
        wrong.symlink_to(real)
        check(mod.find_data_json("wanted", out, None) is None, "symlink JSON을 원본으로 허용했다")
        wrong.unlink()
        os.mkfifo(wrong)
        check(mod.find_data_json("wanted", out, None) is None, "FIFO JSON을 원본으로 허용했다")
        wrong.unlink()

        first = target_dir / "wanted_data.json"
        first.write_text('{"name":"wanted","sequences":[]}', encoding="utf-8")
        other_dir = root / "other-run"
        other_dir.mkdir()
        second = other_dir / "wanted_data.json"
        second.write_text('{"name":"wanted","sequences":[]}', encoding="utf-8")
        try:
            mod.find_data_json("wanted", out, str(other_dir))
        except ValueError as exc:
            check_in("2개", str(exc), "다중 후보 수를 설명하지 않았다")
        else:
            check(False, "여러 matching source 중 첫 파일을 골랐다")


@regression(
    item="stage2",
    prevents="stage2와 batch runner validator가 복사된 상수/문구만 같고 실제 accept/reject 계약은 달라지는 버그.",
)
def test_stage2_normalized_validator_contract_matches_runner_acceptance():
    stage2 = load_module("af3_stage2.py")
    runner = load_module("run_af3_batch_improved.py")
    workspace = Workspace()
    try:
        valid = workspace.monomer("x")
        corpus = [
            valid,
            {**valid, "dialect": "wrong"},
            {**valid, "version": 99},
            {**valid, "modelSeeds": []},
            {**valid, "unknown": 1},
            {**valid, "sequences": []},
        ]
        for obj in corpus:
            stage_accepts, _code = stage2.normalized_validation_contract(obj)
            runner_accepts = runner.validate_fold_job(obj) is None
            check_equal(stage_accepts, runner_accepts, "stage2/runner validator acceptance가 갈라졌다")
    finally:
        workspace.cleanup()
