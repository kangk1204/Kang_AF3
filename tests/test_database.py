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
        db_mounts = [m for m in mounts if "/af3/db_" in m[1]]
        check_equal(len(db_mounts), 2, "DB root 두 개를 별도 마운트하지 않았다", str(mounts))
        values = runs[0].get("af3_flag_values", {}).get("db_dir", [])
        check_equal(values, ["/af3/db_0", "/af3/db_1"], "--db_dir 순서가 보존되지 않았다", str(values))
    finally:
        workspace.cleanup()


@regression(
    item="database",
    prevents=(
        "af3.bin 크기 핀에 우회 수단이 없어, 구글이 새 가중치를 내면 두 배치 러너가 "
        "손댈 수 없는 하드 실패로 멈추는 버그. af3_check.sh 는 AF3_MODEL_SHA256 으로 "
        "바꿀 수 있는데 러너가 쓰는 검증만 막혀 있었다."
    ),
)
def test_model_size_pin_is_overridable_and_says_so():
    import os

    mod = load_module(DB_TOOL)
    with tempfile.TemporaryDirectory(prefix="af3_model_pin_") as td:
        root = Path(td)
        model_dir = root / "models"
        model_dir.mkdir()
        # 고정 크기와 다른, 그러나 정상적인 가중치 파일.
        with (model_dir / "af3.bin").open("wb") as handle:
            handle.truncate(4096)

        saved = os.environ.pop("AF3_MODEL_BYTES", None)
        try:
            pinned = mod.verify_model_dir(model_dir)
            check(not pinned["ok"], "고정 크기와 다른 af3.bin 을 그대로 통과시켰다")
            check_in(
                "AF3_MODEL_BYTES",
                " ".join(pinned["errors"]),
                "크기 불일치 오류가 우회 방법을 알려주지 않는다",
            )

            os.environ["AF3_MODEL_BYTES"] = "4096"
            overridden = mod.verify_model_dir(model_dir)
            check(overridden["ok"], "명시적 override 를 준 가중치를 거부했다")
            check(
                overridden["warnings"],
                "고정 크기를 우회했는데 아무 경고도 남기지 않았다",
            )
            check_in(
                "AF3_MODEL_BYTES",
                " ".join(overridden["warnings"]),
                "경고가 어떤 override 때문인지 밝히지 않는다",
            )

            for bad in ("0", "-1", "abc", ""):
                os.environ["AF3_MODEL_BYTES"] = bad
                rejected = mod.verify_model_dir(model_dir)
                check(
                    not rejected["ok"],
                    "잘못된 AF3_MODEL_BYTES=%r 를 받아들였다" % bad,
                )
        finally:
            os.environ.pop("AF3_MODEL_BYTES", None)
            if saved is not None:
                os.environ["AF3_MODEL_BYTES"] = saved


@regression(
    item="staging",
    prevents=(
        "sidecar staging 의 파일/폴더 경로 충돌 검사가 모든 쌍을 훑는 O(n^2) 라서 "
        "대량 배치에서 실행 전 대기가 제곱으로 늘어나는 문제. 선형 검사로 바꾸면서 "
        "충돌 자체를 못 잡게 되는 것이 더 큰 사고이므로 두 성질을 함께 고정한다."
    ),
)
def test_staging_detects_file_directory_conflicts_without_pairwise_scan():
    import time

    mod = load_module(RUNNER)
    with tempfile.TemporaryDirectory(prefix="af3_stage_conflict_") as td:
        root = Path(td)
        input_dir = root / "in"
        input_dir.mkdir()
        stage_parent = root / "parent"
        stage_parent.mkdir()

        def job(name: str, sidecar_rel: str | None):
            json_file = input_dir / (name + ".json")
            json_file.write_text("{}", encoding="utf-8")
            sidecars = ()
            if sidecar_rel is not None:
                # 원본은 서로 다른 실제 파일이다. 충돌하는 것은 staging '목적지' 경로다
                # (한 파일시스템에 msa 와 msa/deep.a3m 을 동시에 둘 수는 없다).
                source = input_dir / ("src_" + name)
                source.write_text("x", encoding="utf-8")
                sidecars = (mod.Sidecar(source, Path(sidecar_rel)),)
            return mod.Job(
                json_file=json_file,
                output_name=name,
                raw_name=name,
                sidecars=sidecars,
            )

        # 'msa' 가 한 작업에서는 파일, 다른 작업에서는 폴더다. 이건 반드시 거부해야 한다.
        conflicting = [job("a", "msa"), job("b", "msa/deep.a3m")]
        try:
            mod.stage_jobs(conflicting, stage_parent, stage_parent)
        except OSError as exc:
            check_in("충돌", str(exc), "파일/폴더 경로 충돌 원인을 설명하지 않았다")
        else:
            check(False, "같은 이름을 파일이자 폴더로 staging 하는 것을 허용했다")

        # 충돌이 없는 큰 배치는 제곱 시간이 아니어야 한다.
        many = [job("t%05d" % index, None) for index in range(3000)]
        started = time.monotonic()
        stage_dir = mod.stage_jobs(many, stage_parent, stage_parent)
        elapsed = time.monotonic() - started
        check(
            elapsed < 20.0,
            "3000건 staging 준비가 너무 오래 걸린다 (쌍 단위 검사가 남아 있다)",
            "%.1f초" % elapsed,
        )
        check(
            (stage_dir / "t00000.json").is_file(),
            "staging 이 입력을 복사하지 않았다",
        )
