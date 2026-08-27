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
    load_module(DB_TOOL, "af3_db_fixture_sealer").seal_full_database(
        root, enforce_official_pins=False
    )
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
    prevents="같은 byte 수로 변조한 reduced overlay를 preferred/legacy identity가 manifest hash만 보고 허용하는 버그.",
)
def test_both_runners_reject_same_size_reduced_overlay_mutation():
    db = load_module(DB_TOOL, "af3_db_overlay_same_size")
    preferred = load_module(RUNNER, "af3_preferred_overlay_same_size")
    legacy = load_module("af3_batch.py", "af3_legacy_overlay_same_size")
    with tempfile.TemporaryDirectory(prefix="af3_overlay_same_size_") as td:
        root = Path(td)
        full = make_full_db(root / "full")
        overlay = root / "overlay"
        db.create_reduced_overlay(full, overlay, limits={name: 9 for name in FASTA_NAMES})
        preferred.database_fingerprints([overlay, full], "full")
        legacy.database_identity({}, [overlay, full])
        candidate = overlay / FASTA_NAMES[0]
        original = candidate.read_bytes()
        candidate.write_bytes(bytes([original[0] ^ 1]) + original[1:])
        for label, operation in (
            ("preferred", lambda: preferred.database_fingerprints([overlay, full], "full")),
            ("legacy", lambda: legacy.database_identity({}, [overlay, full])),
        ):
            try:
                operation()
            except (OSError, ValueError) as exc:
                check_in("hash", str(exc), f"{label}가 same-size 변조 원인을 hash로 설명하지 않았다")
            else:
                check(False, f"{label}가 same-size reduced overlay 변조를 허용했다")


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
    item="db-seal",
    prevents="같은 공식 DB를 다른 경로에 설치하면 inode/path가 달라 content identity까지 달라지는 버그.",
)
def test_full_seal_content_identity_is_stable_across_paths_and_runners():
    db = load_module(DB_TOOL, "af3_db_stable_identity")
    preferred = load_module(RUNNER, "af3_preferred_stable_identity")
    legacy = load_module("af3_batch.py", "af3_legacy_stable_identity")
    with tempfile.TemporaryDirectory(prefix="af3_full_seal_stable_") as td:
        root = Path(td)
        left = make_full_db(root / "left")
        right = make_full_db(root / "right")
        left_record = db.validate_full_database_seal(left)
        right_record = db.validate_full_database_seal(right)
        check_equal(
            left_record["content_identity"], right_record["content_identity"],
            "동일 content가 설치 경로 때문에 다른 stable identity가 됐다",
        )
        preferred_record = preferred.database_fingerprints([left], "full")[0]
        legacy_record = legacy.database_identity({}, [left])["roots"][0]
        check_equal(
            preferred_record["content_identity"], legacy_record["content_identity"],
            "preferred/legacy runner의 stable DB content identity가 다르다",
        )


@regression(
    item="db-seal",
    prevents="매 실행 때 수백 GB payload를 다시 hash하거나, ordinary child 교체를 cheap binding이 놓치는 버그.",
)
def test_full_seal_runtime_uses_binding_not_payload_hashes_and_detects_change():
    db = load_module(DB_TOOL, "af3_db_binding_runtime")
    with tempfile.TemporaryDirectory(prefix="af3_full_seal_binding_") as td:
        root = make_full_db(Path(td) / "full")
        original_hash = db._sha256_file

        def manifest_only(path, *args, **kwargs):
            if Path(path).name != db.FULL_MANIFEST_NAME:
                raise AssertionError("runtime attempted to hash a DB payload")
            return original_hash(path, *args, **kwargs)

        db._sha256_file = manifest_only
        db.validate_full_database_seal(root)
        db._sha256_file = original_hash
        child = root / "mmcif_files" / "x.cif"
        child.write_text("changed but ordinary\n", encoding="utf-8")
        try:
            db.validate_full_database_seal(root)
        except ValueError as exc:
            check_in("binding", str(exc), "ordinary child 변경 원인이 binding으로 설명되지 않았다")
        else:
            check(False, "seal 이후 ordinary mmCIF child 변경을 허용했다")


@regression(
    item="db-seal",
    prevents="seal 누락·malformed 상태를 metadata fallback으로 조용히 낮추거나 링크를 신뢰하는 버그.",
)
def test_full_seal_fail_closed_and_rejects_malformed_and_link_entries():
    db = load_module(DB_TOOL, "af3_db_fail_closed")
    with tempfile.TemporaryDirectory(prefix="af3_full_seal_closed_") as td:
        root = Path(td)
        unsealed = make_full_db(root / "unsealed")
        (unsealed / db.FULL_MANIFEST_NAME).unlink()
        try:
            db.seal_full_database(unsealed)
        except ValueError as exc:
            check_in("pinned", str(exc), "one-time seal이 official pinned object를 강제하지 않았다")
        else:
            check(False, "official pin과 다른 full DB에 seal을 게시했다")
        check(not (unsealed / db.FULL_MANIFEST_NAME).exists(), "pin 불일치 뒤 seal이 남았다")

        forged = make_full_db(root / "forged")
        forged_path = forged / db.FULL_MANIFEST_NAME
        forged_manifest = json.loads(forged_path.read_text(encoding="utf-8"))
        forged_manifest["kind"] = db.FULL_KIND
        forged_path.write_text(
            json.dumps(forged_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        try:
            db.validate_full_database_seal(forged)
        except ValueError as exc:
            check_in("pinned", str(exc), "self-authored official seal 거부 원인을 설명하지 않았다")
        else:
            check(False, "custom content의 kind만 official로 바꾼 forged seal을 허용했다")
        try:
            db.database_root_identity(unsealed)
        except ValueError as exc:
            check_in("seal", str(exc), "seal 누락 remediation을 설명하지 않았다")
        else:
            check(False, "seal 없는 full DB를 기본값으로 허용했다")
        fallback = db.database_root_identity(unsealed, allow_unsealed=True)
        check_equal(
            fallback["kind"], "unsealed-full-database-metadata-only",
            "호환 identity가 metadata-only라고 명시하지 않았다",
        )
        (unsealed / db.FULL_MANIFEST_NAME).write_text("{", encoding="utf-8")
        try:
            db.database_root_identity(unsealed, allow_unsealed=True)
        except ValueError:
            pass
        else:
            check(False, "malformed seal을 명시 opt-in으로 metadata fallback 했다")

        linked = make_full_db(root / "linked")
        (linked / db.FULL_MANIFEST_NAME).unlink()
        victim = linked / "outside.cif"
        victim.write_text("data_outside\n", encoding="utf-8")
        (linked / "mmcif_files" / "x.cif").unlink()
        (linked / "mmcif_files" / "x.cif").symlink_to(victim)
        try:
            db.seal_full_database(linked, enforce_official_pins=False)
        except ValueError as exc:
            check_in("symlink", str(exc), "mmCIF symlink 거부 원인을 설명하지 않았다")
        else:
            check(False, "mmCIF symlink를 seal했다")

        special = make_full_db(root / "special")
        (special / db.FULL_MANIFEST_NAME).unlink()
        import os
        os.mkfifo(special / "mmcif_files" / "pipe.cif")
        try:
            db.seal_full_database(special, enforce_official_pins=False)
        except ValueError as exc:
            check_in("non-regular", str(exc), "mmCIF special file 거부 원인을 설명하지 않았다")
        else:
            check(False, "mmCIF special file을 seal했다")

        auxiliary = make_full_db(root / "auxiliary")
        (auxiliary / db.FULL_MANIFEST_NAME).unlink()
        (auxiliary / "mmcif_files" / "mmcif_files.tar.gz").write_bytes(b"archive")
        manifest = db.seal_full_database(auxiliary, enforce_official_pins=False)
        mmcif_record = manifest["content_identity"]["entries"]["mmcif_files"]
        check_equal(mmcif_record["entries"], 1, "비-CIF 보조 archive를 effective-input seal에 포함했다")


@regression(
    item="db-seal",
    prevents="installer가 deep verify 전에 seal을 게시하거나 게시 후 final DB로 이동해 binding 계약을 깨는 버그.",
)
def test_installer_seal_publication_is_between_deep_verify_and_promotion():
    text = (Path(__file__).resolve().parents[1] / "scripts" / "install_af3_ubuntu.sh").read_text(
        encoding="utf-8"
    )
    function = text[text.index("db_valid() {"):text.index("validate_db_partial() {")]
    check_in("seal-full --db-dir \"$root\"", function, "installer가 Python-owned deep seal을 호출하지 않는다")
    check("publish-preverified-full-seal" not in text, "caller-supplied hash로 official seal을 게시하는 우회 CLI가 남았다")
    install = text[text.index("install_database() {"):]
    check(
        install.index('db_valid "$DB_PARTIAL"') < install.index('mv -T --no-clobber -- "$DB_PARTIAL"'),
        "staged DB seal/verify 전에 final path로 promote한다",
    )


@regression(
    item="db-seal",
    prevents="installer 구조 size table과 Python official pin table이 조용히 달라지는 버그.",
)
def test_installer_and_python_full_db_pins_are_identical():
    import re

    db = load_module(DB_TOOL, "af3_db_pin_parity")
    text = (Path(__file__).resolve().parents[1] / "scripts" / "install_af3_ubuntu.sh").read_text(
        encoding="utf-8"
    )
    block = re.search(r"readonly -a DB_OBJECT_SPECS=\((.*?)\n\)", text, re.DOTALL)
    check(block is not None, "installer DB_OBJECT_SPECS를 찾을 수 없다")
    shell_specs = {}
    for spec in re.findall(r'"([^"\n]+)"', block.group(1)):
        name, raw_bytes = spec.split(":")
        shell_specs[name] = int(raw_bytes)
    python_sizes = {name: record[0] for name, record in db.PINNED_FULL_FILES.items()}
    check_equal(shell_specs, python_sizes, "installer/Python FASTA size table이 다르다")
    cif_count = re.search(r'readonly EXPECTED_CIF_COUNT="([0-9]+)"', text)
    check(cif_count is not None, "installer mmCIF count pin을 찾을 수 없다")
    check_equal(int(cif_count.group(1)), db.PINNED_MMCIF_ENTRIES, "mmCIF count pin이 다르다")


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
