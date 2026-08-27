#!/usr/bin/env python3
"""Regression tests for legacy runner reuse, publication, locking, and wrapper semantics."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from harness import Workspace, run_script


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "af3_batch.py"
WRAPPER = ROOT / "scripts" / "af3run.sh"


def load_module():
    spec = importlib.util.spec_from_file_location("af3_legacy_integrity", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def fold(name="vhh_a", sequence="ACDE"):
    return {
        "name": name,
        "modelSeeds": [1],
        "sequences": [{"protein": {
            "id": "A", "sequence": sequence, "unpairedMsa": ">q\nACDE",
            "pairedMsa": "", "templates": [],
        }}],
        "dialect": "alphafold3",
        "version": 1,
    }


def identity(name="vhh_a", sequence="ACDE"):
    return {
        "manifest_version": 1,
        "kind": "msa",
        "output_name": name,
        "input": {"semantic_json_sha256": sequence, "source_file": "a.json"},
        "sidecars": [], "databases": {}, "model": None, "image": {}, "config": {},
    }


def test_msa_reuse_requires_matching_manifest():
    mod = load_module()
    with tempfile.TemporaryDirectory(prefix="af3_legacy_manifest_") as td:
        work = Path(td)
        store = work / "msa_store"
        store.mkdir()
        data = store / "vhh_a_data.json"
        data.write_text(json.dumps(fold()), encoding="utf-8")
        expected = identity()

        assert not mod.msa_store_is_complete(work, "vhh_a", expected)
        assert mod.msa_store_is_complete(work, "vhh_a", expected, True)

        mod.atomic_write_json(
            store / "vhh_a_data.af3_manifest.json",
            {"manifest_version": 1, "identity": expected,
             "artifact": {"bytes": data.stat().st_size,
                          "sha256": mod.sha256_file(data)}},
        )
        assert mod.msa_store_is_complete(work, "vhh_a", expected)
        changed = identity(sequence="DIFFERENT")
        assert not mod.msa_store_is_complete(work, "vhh_a", changed)

        (store / "vhh_a_data.af3_manifest.json").unlink()
        (store / "vhh_a_data.af3_manifest.json").symlink_to("missing.json")
        assert not mod.msa_store_is_complete(work, "vhh_a", expected, True)


def test_msa_publish_is_atomic_validated_and_not_recursive():
    mod = load_module()
    with tempfile.TemporaryDirectory(prefix="af3_legacy_publish_") as td:
        root = Path(td)
        raw = root / "raw"
        current_dir = raw / "vhh_a"
        historical = raw / "history" / "old"
        store = root / "store"
        current_dir.mkdir(parents=True)
        historical.mkdir(parents=True)
        current = current_dir / "vhh_a_data.json"
        current.write_text(json.dumps(fold()), encoding="utf-8")
        (historical / "old_data.json").write_text(json.dumps(fold("old")), encoding="utf-8")

        expected = identity()
        count = mod.collect_msa_outputs(
            raw, store, candidates=[current], identities={"vhh_a": expected})
        assert count == 1
        assert (store / "vhh_a_data.json").is_file()
        assert (store / "vhh_a_data.af3_manifest.json").is_file()
        assert not (store / "old_data.json").exists()
        assert not list(store.glob(".*.tmp.*"))


def test_result_reuse_and_preindex_are_identity_bound():
    mod = load_module()
    with tempfile.TemporaryDirectory(prefix="af3_legacy_result_") as td:
        output = Path(td)
        result = output / "vhh_a"
        result.mkdir()
        for suffix in ("_ranking_scores.csv", "_model.cif", "_summary_confidences.json"):
            (result / ("vhh_a" + suffix)).write_text("x", encoding="utf-8")
        expected = identity()
        index = mod.index_result_dirs(output)
        assert mod.find_result_dirs(output, "vhh_a", index) == [result]
        assert not mod.result_is_reusable(result, "vhh_a", expected)
        assert mod.result_is_reusable(result, "vhh_a", expected, trust_unverified=True)
        assert mod.publish_result_manifest(result, "vhh_a", expected)
        assert mod.result_is_reusable(result, "vhh_a", expected)
        assert not mod.result_is_reusable(result, "vhh_a", identity(sequence="changed"))
        (result / "vhh_a_model.cif").write_text("tampered", encoding="utf-8")
        assert not mod.result_is_reusable(result, "vhh_a", expected)


def test_full_db_child_change_invalidates_legacy_identity():
    mod = load_module()
    workspace = Workspace()
    try:
        before = mod.database_identity({}, [workspace.db_dir])
        child = workspace.db_dir / "mmcif_files" / "stub.cif"
        child.write_text("changed\n", encoding="utf-8")
        try:
            mod.database_identity({}, [workspace.db_dir])
        except ValueError as exc:
            assert "binding" in str(exc)
        else:
            raise AssertionError("legacy accepted a full DB changed after sealing")
        assert before["roots"][0]["kind"] == "af3_custom_full_database_seal"
    finally:
        workspace.cleanup()


def test_legacy_unsealed_db_is_explicitly_metadata_only():
    mod = load_module()
    workspace = Workspace()
    try:
        (workspace.db_dir / "af3_full_db_manifest.json").unlink()
        try:
            mod.database_identity({}, [workspace.db_dir])
        except ValueError as exc:
            assert "seal" in str(exc)
        else:
            raise AssertionError("legacy accepted unsealed full DB by default")
        record = mod.database_identity(
            {}, [workspace.db_dir], allow_unsealed=True
        )["roots"][0]
        assert record["kind"] == "unsealed-full-database-metadata-only"
    finally:
        workspace.cleanup()


def test_output_and_work_locks_are_both_exclusive():
    mod = load_module()
    with tempfile.TemporaryDirectory(prefix="af3_legacy_locks_") as td:
        root = Path(td)
        output, work = root / "out", root / "work"
        output.mkdir()
        work.mkdir()
        locks = mod.acquire_run_locks(output, work)
        try:
            assert (output / ".run_af3_batch.lock").is_file()
            assert (work / ".run_af3_batch.work.lock").is_file()
            try:
                mod.acquire_run_locks(output, work)
            except BlockingIOError:
                pass
            else:
                raise AssertionError("a second owner acquired the same resource locks")
        finally:
            for handle in reversed(locks):
                handle.close()


def test_legacy_json_staging_is_private_and_identity_bound_to_consumed_bytes():
    mod = load_module()
    with tempfile.TemporaryDirectory(prefix="af3_legacy_snapshot_") as td:
        root = Path(td)
        source = root / "a.json"
        source.write_text(json.dumps(fold()), encoding="utf-8")
        snapshots = mod.stage_files([source], root / "snapshots")
        snapshot = snapshots / source.name

        source_info = os.lstat(source)
        snapshot_info = os.lstat(snapshot)
        assert snapshot_info.st_nlink == 1
        assert (snapshot_info.st_dev, snapshot_info.st_ino) != (
            source_info.st_dev, source_info.st_ino)
        frozen_bytes = snapshot.read_bytes()
        source.write_text(json.dumps(fold(sequence="WXYZ")), encoding="utf-8")
        assert snapshot.read_bytes() == frozen_bytes

        obj = mod.read_fold_json(snapshot)
        target = {
            "path": snapshot,
            "source_path": source,
            "name": obj["name"],
            "obj": obj,
            "semantic_json_sha256": mod.semantic_json_sha256(obj),
            "input_json_sha256": mod.sha256_file(snapshot),
            "tokens": mod.count_tokens(obj),
        }
        record = mod.target_identity(
            target, mod.parse_args([]), "msa", {}, None, {})
        consumed_dir = mod.stage_files(
            [snapshot], root / "consumed", expected_sha256={
                snapshot.name: record["input"]["json_sha256"]})
        consumed = consumed_dir / snapshot.name
        assert consumed.read_bytes() == frozen_bytes
        assert record["input"]["json_sha256"] == mod.sha256_file(consumed)
        assert record["input"]["semantic_json_sha256"] == mod.semantic_json_sha256(
            mod.read_fold_json(consumed))


def test_legacy_snapshot_fails_closed_on_in_place_source_mutation():
    mod = load_module()
    with tempfile.TemporaryDirectory(prefix="af3_legacy_snapshot_race_") as td:
        root = Path(td)
        source = root / "a.json"
        source.write_bytes(b"A" * (1024 * 1024 + 17))
        original_copy = mod._copy_snapshot_bytes

        def copy_then_mutate(source_fd, target_fd, chunk_size=1024 * 1024):
            original_copy(source_fd, target_fd, chunk_size)
            with open(source, "r+b") as handle:
                handle.seek(0)
                handle.write(b"B")
                handle.flush()
                os.fsync(handle.fileno())

        mod._copy_snapshot_bytes = copy_then_mutate
        try:
            try:
                mod.stage_files([source], root / "race")
            except OSError as exc:
                assert "수정되거나 교체" in str(exc)
            else:
                raise AssertionError("legacy accepted an input mutated during snapshot")
        finally:
            mod._copy_snapshot_bytes = original_copy
        assert not (root / "race" / source.name).exists()

        source.write_bytes(b"C" * (1024 * 1024 + 17))

        def copy_then_replace(source_fd, target_fd, chunk_size=1024 * 1024):
            original_copy(source_fd, target_fd, chunk_size)
            replacement = root / "replacement.json"
            replacement.write_bytes(b"D" * (1024 * 1024 + 17))
            os.replace(replacement, source)

        mod._copy_snapshot_bytes = copy_then_replace
        try:
            try:
                mod.stage_files([source], root / "replacement-race")
            except OSError as exc:
                assert "수정되거나 교체" in str(exc)
            else:
                raise AssertionError("legacy accepted an input replaced during snapshot")
        finally:
            mod._copy_snapshot_bytes = original_copy
        assert not (root / "replacement-race" / source.name).exists()


def test_legacy_and_preferred_share_the_same_gpu_lease_namespace():
    legacy = load_module()
    preferred_path = ROOT / "scripts" / "run_af3_batch_improved.py"
    spec = importlib.util.spec_from_file_location("preferred_gpu_lease", preferred_path)
    preferred = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = preferred
    spec.loader.exec_module(preferred)
    with tempfile.TemporaryDirectory(prefix="af3_shared_gpu_lease_") as td:
        saved = os.environ.get("AF3_GPU_LEASE_DIR")
        os.environ["AF3_GPU_LEASE_DIR"] = str(Path(td) / "leases")
        legacy._visible_gpu_keys = lambda: ["unknown-all-devices"]
        descriptor = preferred._try_gpu_lease(preferred.GPU_INVENTORY_LEASE_KEY)
        try:
            try:
                with legacy.gpu_leases_for_legacy(True):
                    pass
            except BlockingIOError:
                pass
            else:
                raise AssertionError("legacy acquired the preferred runner's GPU lease")
        finally:
            preferred._release_gpu_lease(descriptor)
            if saved is None:
                os.environ.pop("AF3_GPU_LEASE_DIR", None)
            else:
                os.environ["AF3_GPU_LEASE_DIR"] = saved


def test_legacy_holds_global_gpu_gate_through_container_cleanup():
    legacy = load_module()
    preferred_path = ROOT / "scripts" / "run_af3_batch_improved.py"
    spec = importlib.util.spec_from_file_location(
        "preferred_gpu_cleanup_lease", preferred_path)
    preferred = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = preferred
    spec.loader.exec_module(preferred)
    with tempfile.TemporaryDirectory(prefix="af3_gpu_cleanup_lease_") as td:
        saved = os.environ.get("AF3_GPU_LEASE_DIR")
        os.environ["AF3_GPU_LEASE_DIR"] = str(Path(td) / "leases")
        legacy._visible_gpu_keys = lambda: ["unknown-all-devices"]
        cleanup_observations = []

        def observe_cleanup():
            descriptor = preferred._try_gpu_lease(
                preferred.GPU_INVENTORY_LEASE_KEY, shared=True)
            cleanup_observations.append(descriptor is None)
            if descriptor is not None:
                preferred._release_gpu_lease(descriptor)

        legacy.teardown_containers = observe_cleanup
        try:
            with legacy.gpu_leases_for_legacy(True):
                pass
            assert cleanup_observations == [True]
            descriptor = preferred._try_gpu_lease(
                preferred.GPU_INVENTORY_LEASE_KEY, shared=True)
            assert descriptor is not None
            preferred._release_gpu_lease(descriptor)
        finally:
            if saved is None:
                os.environ.pop("AF3_GPU_LEASE_DIR", None)
            else:
                os.environ["AF3_GPU_LEASE_DIR"] = saved


def test_nonzero_legacy_producer_does_not_publish_result_manifest():
    workspace = Workspace()
    try:
        workspace.write_json("a.json", workspace.monomer("vhh_a"))
        proc = run_script(
            "af3_batch.py",
            [
                "--input-dir", str(workspace.input_dir),
                "--output-dir", str(workspace.output_dir),
                "--work-dir", str(workspace.root / "legacy_work"),
                "--db-dir", str(workspace.db_dir),
                "--model-dir", str(workspace.model_dir),
                "--docker", "docker",
                "--stage", "oneshot",
            ],
            workspace,
            env_extra={"AF3_STUB_EXIT_AFTER_FINALS": "1"},
        )
        assert proc.returncode != 0
        assert not list(workspace.output_dir.rglob("*_af3_legacy_manifest.json"))
        assert list((workspace.root / "legacy_work" / "partial").glob("vhh_a_nonzero_*"))
    finally:
        workspace.cleanup()


def test_streamed_watchdog_times_out_and_artifact_growth_resets_it():
    mod = load_module()
    with tempfile.TemporaryDirectory(prefix="af3_legacy_watchdog_") as td:
        root = Path(td)
        stalled_log = root / "stalled.log"
        started = time.monotonic()
        rc, _timings, _wall = mod.run_streamed(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            stalled_log,
            "test-stalled",
            no_progress_timeout=1,
            progress_roots=(root / "no-artifacts",),
        )
        assert rc == 124
        assert time.monotonic() - started < 4
        assert "artifact 변화가 1초 동안 없어" in stalled_log.read_text(encoding="utf-8")

        artifacts = root / "artifacts"
        artifacts.mkdir()
        producer = (
            "import pathlib,time; root=pathlib.Path(%r); "
            "[(time.sleep(0.4), (root / ('vhh_a_%%d.tmp' %% i)).write_text('x' * (i+1))) "
            "for i in range(4)]" % str(artifacts)
        )
        rc, _timings, _wall = mod.run_streamed(
            [sys.executable, "-c", producer],
            root / "artifact-progress.log",
            "test-artifacts",
            no_progress_timeout=1,
            progress_roots=(artifacts,),
        )
        assert rc == 0

        stdout_producer = (
            "import time; "
            "[(time.sleep(0.4), print(i, flush=True)) for i in range(4)]"
        )
        rc, _timings, _wall = mod.run_streamed(
            [sys.executable, "-c", stdout_producer],
            root / "stdout-progress.log",
            "test-stdout",
            no_progress_timeout=1,
        )
        assert rc == 0
        assert "0\n1\n2\n3\n" in (root / "stdout-progress.log").read_text(
            encoding="utf-8")


def test_concurrent_msa_watchdog_is_per_shard_and_artifact_aware():
    mod = load_module()
    with tempfile.TemporaryDirectory(prefix="af3_legacy_msa_watchdog_") as td:
        root = Path(td)
        msa_raw = root / "msa_raw"
        msa_raw.mkdir()
        processes = []
        handles = []
        try:
            stalled_log = root / "stalled.log"
            stalled_handle = open(stalled_log, "a", encoding="utf-8")
            handles.append(stalled_handle)
            stalled = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(10)"],
                stdout=stalled_handle, stderr=subprocess.STDOUT,
            )
            processes.append(stalled)

            active_log = root / "active.log"
            active_handle = open(active_log, "a", encoding="utf-8")
            handles.append(active_handle)
            producer = (
                "import pathlib,time; root=pathlib.Path(%r); "
                "[(time.sleep(0.4), (root / ('active_%%d.tmp' %% i)).write_text('x' * (i+1))) "
                "for i in range(4)]" % str(msa_raw)
            )
            active = subprocess.Popen(
                [sys.executable, "-c", producer],
                stdout=active_handle, stderr=subprocess.STDOUT,
            )
            processes.append(active)

            rcs = mod.wait_for_msa_processes(
                [
                    (0, stalled, stalled_log, stalled_handle,
                     [{"name": "stalled"}]),
                    (1, active, active_log, active_handle,
                     [{"name": "active"}]),
                ],
                msa_raw,
                no_progress_timeout=1,
            )
            assert rcs == {0: 124, 1: 0}
            assert "artifact 변화가 1초 동안 없어" in stalled_log.read_text(encoding="utf-8")
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
            for handle in handles:
                if not handle.closed:
                    handle.close()


def test_legacy_watchdog_zero_disables_and_negative_is_rejected():
    mod = load_module()
    with tempfile.TemporaryDirectory(prefix="af3_legacy_watchdog_args_") as td:
        rc, _timings, _wall = mod.run_streamed(
            [sys.executable, "-c", "import time; time.sleep(0.2)"],
            Path(td) / "disabled.log",
            "test-disabled",
            no_progress_timeout=0,
        )
        assert rc == 0
    assert mod.parse_args([]).no_progress_timeout == 7200
    assert mod.parse_args(["--no-progress-timeout", "0"]).no_progress_timeout == 0
    assert mod.main(["--no-progress-timeout", "-1"]) == 2


def test_bounded_process_stop_escalates_to_kill():
    mod = load_module()
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            "print('ready', flush=True); time.sleep(10)",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        assert process.stdout.readline() == "ready\n"
        started = time.monotonic()
        mod._stop_process(process, grace_seconds=0.2)
        assert time.monotonic() - started < 2
        assert process.returncode is not None
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        process.stdout.close()


def run_wrapper(workspace, mode, env=None):
    name = "job"
    (workspace / (name + "_in")).mkdir(exist_ok=True)
    command_env = os.environ.copy()
    command_env.update(env or {})
    return subprocess.run(
        ["bash", str(WRAPPER), name, mode], cwd=workspace,
        env=command_env, text=True, capture_output=True, check=False,
    )


def test_wrapper_separates_full_and_screen_inference():
    with tempfile.TemporaryDirectory(prefix="af3_wrapper_modes_") as td:
        root = Path(td)
        recorder = root / "record.py"
        recorder.write_text(
            "#!/usr/bin/env python3\nimport pathlib,sys\n"
            "pathlib.Path('args.txt').write_text('\\n'.join(sys.argv[1:]))\n",
            encoding="utf-8",
        )
        recorder.chmod(0o755)
        full = run_wrapper(root, "infer", {"AF3_PYTHON": str(recorder)})
        assert full.returncode == 0, full.stderr
        full_args = (root / "args.txt").read_text(encoding="utf-8")
        assert "--stage\ninfer" in full_args
        assert "--diffusion-samples" not in full_args
        screen = run_wrapper(root, "infer-screen", {"AF3_PYTHON": str(recorder)})
        assert screen.returncode == 0, screen.stderr
        screen_args = (root / "args.txt").read_text(encoding="utf-8")
        assert "--diffusion-samples\n1" in screen_args
        assert "--recycles\n3" in screen_args


def test_wrapper_rejects_bad_environment_and_sticks_tee_failure():
    with tempfile.TemporaryDirectory(prefix="af3_wrapper_errors_") as td:
        root = Path(td)
        invalid = run_wrapper(root, "screen", {
            "AF3_PYTHON": "/bin/true", "AF3RUN_FILENAME_LANG": "unsafe"})
        assert invalid.returncode != 0

        work_link = root / "job_work"
        work_link.write_text("not a directory", encoding="utf-8")
        failed_mkdir = run_wrapper(root, "screen", {"AF3_PYTHON": "/bin/true"})
        assert failed_mkdir.returncode != 0
        assert "작업 폴더를 만들 수 없다" in failed_mkdir.stderr
        work_link.unlink()
        work_link.symlink_to("/proc", target_is_directory=True)
        failed_log = run_wrapper(root, "screen", {"AF3_PYTHON": "/bin/true"})
        assert failed_log.returncode != 0
        assert "로그 기록 실패" in failed_log.stderr


def main():
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
        print("PASS", test.__name__)
    print("legacy integrity: %d passed" % len(tests))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
