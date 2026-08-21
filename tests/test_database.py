#!/usr/bin/env python3
"""Database overlay, validation, and multi-root runner regressions."""

from __future__ import annotations

import json
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


DB_TOOL = "af3_db.py"
RUNNER = "run_af3_batch_improved.py"

FASTA_NAMES = (
    "bfd-first_non_consensus_sequences.fasta",
    "mgy_clusters_2022_05.fa",
    "uniref90_2022_05.fa",
    "uniprot_all_2021_04.fa",
    "nt_rna_2023_02_23_clust_seq_id_90_cov_80_rep_seq.fasta",
    "rfam_14_9_clust_seq_id_90_cov_80_rep_seq.fasta",
    "rnacentral_active_seq_id_90_cov_80_linclust.fasta",
)


def make_full_db(root: Path) -> Path:
    root.mkdir(parents=True)
    body = ">a\nAAAA\n>b\nBBBBBB\n>c\nCCCC\n"
    for name in FASTA_NAMES:
        (root / name).write_text(body, encoding="utf-8")
    (root / "pdb_seqres_2022_09_28.fasta").write_text(body, encoding="utf-8")
    mmcif = root / "mmcif_files"
    mmcif.mkdir()
    (mmcif / "x.cif").write_text("data_x\n", encoding="utf-8")
    return root


@regression(
    item="db",
    prevents="축소 DB 생성이 없는 원본 파일을 건너뛰고 부분 DB를 정상 결과처럼 공개하는 버그.",
)
def test_reduce_missing_source_fails_without_publishing_output():
    with tempfile.TemporaryDirectory(prefix="af3_db_missing_") as td:
        root = Path(td)
        source = make_full_db(root / "full")
        (source / FASTA_NAMES[0]).unlink()
        output = root / "overlay"
        proc = subprocess.run(
            [sys.executable, str(Path(__file__).resolve().parent.parent / "scripts" / DB_TOOL),
             "reduce", "--source", str(source), "--output", str(output)],
            cwd=root,
            capture_output=True,
            text=True,
        )
        check(proc.returncode != 0, "누락된 원본인데 reduce 가 성공했다")
        check(not output.exists(), "실패했는데 부분 overlay 폴더를 공개했다")


@regression(
    item="db",
    prevents="축소 DB가 호스트 절대경로 mmcif 심볼릭 링크를 만들어 Docker 안에서 링크가 깨지는 버그.",
)
def test_reduce_creates_atomic_sequence_overlay_without_symlinks():
    mod = load_module(DB_TOOL)
    with tempfile.TemporaryDirectory(prefix="af3_db_reduce_") as td:
        root = Path(td)
        source = make_full_db(root / "full")
        output = root / "overlay"
        result = mod.create_reduced_overlay(source, output, limits={name: 9 for name in FASTA_NAMES})
        check_equal(result["kind"], "af3_reduced_msa_overlay", "manifest kind 가 틀렸다")
        check(output.is_dir(), "overlay 폴더가 만들어지지 않았다")
        check(not (output / "mmcif_files").exists(), "overlay 에 mmcif 링크/복사본을 만들었다")
        check(not (output / "pdb_seqres_2022_09_28.fasta").exists(), "overlay 에 seqres 를 복사했다")
        check((output / "af3_db_manifest.json").is_file(), "manifest 가 없다")
        for name in FASTA_NAMES:
            path = output / name
            check(path.is_file() and not path.is_symlink(), f"정상 overlay 파일이 아니다: {name}")
            text = path.read_text(encoding="utf-8")
            check(text.startswith(">") and text.endswith("\n"), f"FASTA 경계가 깨졌다: {name}")
            check(text.count("\n>") >= 1, f"목표 경계까지 완전한 레코드를 쓰지 않았다: {name}")


@regression(
    item="db",
    prevents="overlay 단독을 완전 DB로 오인하거나 fallback 순서를 잃어 AF3가 필요한 파일을 못 찾는 버그.",
)
def test_verify_ordered_roots_resolves_overlay_then_full():
    mod = load_module(DB_TOOL)
    with tempfile.TemporaryDirectory(prefix="af3_db_verify_") as td:
        root = Path(td)
        full = make_full_db(root / "full")
        overlay = root / "overlay"
        mod.create_reduced_overlay(full, overlay, limits={name: 9 for name in FASTA_NAMES})
        report = mod.verify_database_roots([overlay, full])
        check(report["ok"], "overlay+full 검증이 실패했다", json.dumps(report, ensure_ascii=False))
        for name in FASTA_NAMES:
            check_equal(Path(report["resolved"][name]), overlay / name, f"overlay 우선순위를 잃었다: {name}")
        check_equal(Path(report["resolved"]["mmcif_files"]), full / "mmcif_files", "템플릿 fallback 이 틀렸다")
        check(not mod.verify_database_roots([overlay])["ok"], "불완전 overlay 단독을 완전 DB로 인정했다")


@regression(
    item="db",
    prevents="reduced overlay 파일이 생성 후 잘리거나 바뀌어도 manifest를 무시해 정상 DB로 통과시키는 버그.",
)
def test_verify_checks_reduced_overlay_manifest_sizes():
    mod = load_module(DB_TOOL)
    with tempfile.TemporaryDirectory(prefix="af3_db_manifest_") as td:
        root = Path(td)
        full = make_full_db(root / "full")
        overlay = root / "overlay"
        mod.create_reduced_overlay(full, overlay, limits={name: 9 for name in FASTA_NAMES})
        damaged = overlay / FASTA_NAMES[0]
        damaged.write_bytes(damaged.read_bytes()[:-1])
        report = mod.verify_database_roots([overlay, full])
        check(not report["ok"], "manifest 크기와 다른 overlay를 허용했다")
        check_in("manifest", " ".join(report["errors"]).lower(), "manifest 불일치 원인을 설명하지 않았다")


@regression(
    item="db",
    prevents="DB 내부의 외부 심볼릭 링크를 Docker에서 정상 경로로 오인하는 버그.",
)
def test_verify_rejects_external_symlink():
    mod = load_module(DB_TOOL)
    with tempfile.TemporaryDirectory(prefix="af3_db_link_") as td:
        root = Path(td)
        full = make_full_db(root / "full")
        external = full / "mmcif_files"
        overlay = root / "overlay"
        overlay.mkdir()
        for name in FASTA_NAMES:
            (overlay / name).write_text(">x\nAAAA\n", encoding="utf-8")
        (overlay / "pdb_seqres_2022_09_28.fasta").write_text(">x\nAAAA\n", encoding="utf-8")
        (overlay / "mmcif_files").symlink_to(external)
        report = mod.verify_database_roots([overlay])
        check(not report["ok"], "외부 심볼릭 링크 DB를 허용했다")
        check_in("symlink", " ".join(report["errors"]).lower(), "심볼릭 링크 원인을 설명하지 않았다")


@regression(
    item="db",
    prevents="반복 --db-dir 중 뒤 값이 앞 값을 덮어써 overlay/fallback 순서와 Docker 마운트를 잃는 버그.",
)
def test_preferred_runner_preserves_multiple_db_roots():
    workspace = Workspace()
    try:
        workspace.write_json("a.json", workspace.monomer("vhh_a"))
        fallback = workspace.root / "full_fallback"
        make_full_db(fallback)
        proc = run_script(
            RUNNER,
            [
                "--input-dir", str(workspace.input_dir),
                "--output-dir", str(workspace.output_dir),
                "--db-dir", str(workspace.db_dir),
                "--db-dir", str(fallback),
                "--model-dir", str(workspace.model_dir),
                "--cache-dir", str(workspace.cache_dir),
                "--docker", "docker",
                "--yes",
            ],
            workspace,
        )
        check_equal(proc.returncode, 0, "multi-root 실행이 실패했다", proc.stdout[-1600:])
        runs = [c for c in workspace.stub_calls() if c.get("call") == "run"]
        check(runs, "Docker 실행 기록이 없다")
        mounts = runs[0]["mounts"]
        db_mounts = [m for m in mounts if "/root/af3_db_" in m[1]]
        check_equal(len(db_mounts), 2, "DB root 두 개를 별도 마운트하지 않았다", str(mounts))
        values = runs[0].get("af3_flag_values", {}).get("db_dir", [])
        check_equal(values, ["/root/af3_db_0", "/root/af3_db_1"], "--db_dir 순서가 보존되지 않았다", str(values))
    finally:
        workspace.cleanup()
