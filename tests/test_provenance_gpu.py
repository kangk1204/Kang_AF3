#!/usr/bin/env python3
"""Focused regressions for transactional provenance and GPU reservations."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from harness import (
    Workspace,
    default_args,
    load_module,
    make_stub_bin,
    regression,
    run_script,
)


RUNNER = "run_af3_batch_improved.py"
RUNNER_PATH = Path(__file__).resolve().parent.parent / "scripts" / RUNNER
LEGACY_PATH = RUNNER_PATH.with_name("af3_batch.py")


class ProvenanceTests(unittest.TestCase):
    def test_private_snapshot_drives_parse_provenance_and_staging(self):
        runner = load_module(RUNNER, "input_snapshot_consistency_runner")
        workspace = Workspace()
        try:
            original = workspace.monomer("vhh_original", sequence="AAAA")
            original["sequences"][0]["protein"]["unpairedMsaPath"] = "query.a3m"
            json_path = workspace.write_json("a.json", original)
            sidecar = workspace.write_bytes("query.a3m", b">q\nAAAA\n")
            job, error = runner.read_job(json_path, workspace.input_dir)
            self.assertIsNone(error)
            self.assertEqual(job.output_name, "vhh_original")

            replacement = workspace.monomer("vhh_replacement", sequence="BBBB")
            replacement["sequences"][0]["protein"]["unpairedMsaPath"] = "query.a3m"
            json_path.write_text(json.dumps(replacement), encoding="utf-8")
            sidecar.write_bytes(b">q\nBBBB\n")

            stage = runner.stage_jobs([job], workspace.root, workspace.output_dir)
            staged = json.loads((stage / "a.json").read_text(encoding="utf-8"))
            self.assertEqual(staged, original)
            self.assertEqual((stage / "query.a3m").read_bytes(), b">q\nAAAA\n")
            provenance = runner.job_provenance(
                job,
                "inference",
                [],
                workspace.model_dir,
                "image",
                model_record=None,
                database_records=[],
            )
            expected = runner._json_bytes(original)
            self.assertEqual(
                provenance["canonical_input_sha256"],
                hashlib.sha256(expected).hexdigest(),
            )
            self.assertEqual(
                provenance["sidecars"][0]["sha256"],
                hashlib.sha256(b">q\nAAAA\n").hexdigest(),
            )
        finally:
            workspace.cleanup()

    def test_embedded_nul_sidecar_path_is_a_per_file_validation_error(self):
        runner = load_module(RUNNER, "nul_sidecar_path_runner")
        workspace = Workspace()
        try:
            payload = workspace.monomer("vhh_nul")
            payload["sequences"][0]["protein"]["unpairedMsaPath"] = "bad\0.a3m"
            path = workspace.write_json("nul.json", payload)
            job, error = runner.read_job(path, workspace.input_dir)
            self.assertIsNone(job)
            self.assertIn("sidecar snapshot", error)
        finally:
            workspace.cleanup()

    def test_symlink_swap_during_json_snapshot_fails_closed(self):
        runner = load_module(RUNNER, "input_snapshot_symlink_race_runner")
        workspace = Workspace()
        try:
            json_path = workspace.write_json("a.json", workspace.monomer("safe"))
            outside = workspace.root / "outside.json"
            outside.write_text(
                json.dumps(workspace.monomer("outside")), encoding="utf-8"
            )
            original_hook = runner._snapshot_test_hook

            def swap(path, phase):
                if path == json_path and phase == "after_lstat":
                    path.unlink()
                    path.symlink_to(outside)

            runner._snapshot_test_hook = swap
            try:
                job, error = runner.read_job(json_path, workspace.input_dir)
            finally:
                runner._snapshot_test_hook = original_hook
            self.assertIsNone(job)
            self.assertIsNotNone(error)
            self.assertIn("snapshot", error)
            self.assertEqual(
                json.loads(outside.read_text(encoding="utf-8"))["name"], "outside"
            )
        finally:
            workspace.cleanup()

    def test_sidecar_mutation_during_snapshot_fails_closed(self):
        runner = load_module(RUNNER, "sidecar_snapshot_race_runner")
        workspace = Workspace()
        try:
            sidecar = workspace.write_bytes("query.a3m", b">q\nAAAA\n")
            obj = workspace.monomer("vhh_sidecar")
            obj["sequences"][0]["protein"]["unpairedMsaPath"] = "query.a3m"
            json_path = workspace.write_json("a.json", obj)
            original_hook = runner._snapshot_test_hook

            def mutate(path, phase):
                if path == sidecar and phase == "after_copy":
                    path.write_bytes(b">q\nCHANGED\n")

            runner._snapshot_test_hook = mutate
            try:
                job, error = runner.read_job(json_path, workspace.input_dir)
            finally:
                runner._snapshot_test_hook = original_hook
            self.assertIsNone(job)
            self.assertIsNotNone(error)
            self.assertIn("변경", error)
        finally:
            workspace.cleanup()

    def test_nested_sidecar_parent_swap_stays_on_open_directory(self):
        runner = load_module(RUNNER, "sidecar_parent_swap_runner")
        workspace = Workspace()
        try:
            parent = workspace.input_dir / "templates"
            parent.mkdir()
            original_sidecar = parent / "ref.cif"
            original_sidecar.write_bytes(b"ORIGINAL_TREE\n")
            outside = workspace.root / "outside_templates"
            outside.mkdir()
            (outside / "ref.cif").write_bytes(b"INJECTED_TREE\n")
            obj = workspace.monomer("vhh_nested_sidecar")
            obj["sequences"][0]["protein"]["templates"] = [
                {
                    "mmcifPath": "templates/ref.cif",
                    "queryIndices": [0],
                    "templateIndices": [0],
                }
            ]
            json_path = workspace.write_json("a.json", obj)
            original_hook = runner._snapshot_test_hook

            def swap_parent(path, phase):
                if path == parent and phase == "after_parent_open":
                    parent.rename(workspace.input_dir / "templates_original")
                    parent.symlink_to(outside, target_is_directory=True)

            runner._snapshot_test_hook = swap_parent
            try:
                job, error = runner.read_job(json_path, workspace.input_dir)
            finally:
                runner._snapshot_test_hook = original_hook
            self.assertIsNone(error)
            self.assertIsNotNone(job)
            stage = runner.stage_jobs([job], workspace.root, workspace.output_dir)
            self.assertEqual(
                (stage / "templates" / "ref.cif").read_bytes(),
                b"ORIGINAL_TREE\n",
            )
            self.assertEqual((outside / "ref.cif").read_bytes(), b"INJECTED_TREE\n")
        finally:
            workspace.cleanup()

    def test_whole_input_root_swap_cannot_create_hybrid_snapshot(self):
        runner = load_module(RUNNER, "whole_input_root_swap_runner")
        workspace = Workspace()
        try:
            parent = workspace.input_dir / "templates"
            parent.mkdir()
            (parent / "ref.cif").write_bytes(b"ORIGINAL_ROOT\n")
            original = workspace.monomer("vhh_original_root", sequence="AAAA")
            original["sequences"][0]["protein"]["templates"] = [
                {
                    "mmcifPath": "templates/ref.cif",
                    "queryIndices": [0],
                    "templateIndices": [0],
                }
            ]
            json_path = workspace.write_json("a.json", original)
            original_hook = runner._snapshot_test_hook

            def swap_root(path, phase):
                if path == json_path and phase == "after_copy":
                    workspace.input_dir.rename(workspace.root / "input_original")
                    workspace.input_dir.mkdir()
                    replacement_parent = workspace.input_dir / "templates"
                    replacement_parent.mkdir()
                    (replacement_parent / "ref.cif").write_bytes(b"INJECTED_ROOT\n")
                    replacement = workspace.monomer(
                        "vhh_injected_root", sequence="BBBB"
                    )
                    replacement["sequences"][0]["protein"]["templates"] = [
                        {
                            "mmcifPath": "templates/ref.cif",
                            "queryIndices": [0],
                            "templateIndices": [0],
                        }
                    ]
                    workspace.write_json("a.json", replacement)

            runner._snapshot_test_hook = swap_root
            try:
                jobs, errors = runner.collect_jobs(workspace.input_dir)
            finally:
                runner._snapshot_test_hook = original_hook
            self.assertEqual(errors, [])
            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0].output_name, "vhh_original_root")
            stage = runner.stage_jobs(jobs, workspace.root, workspace.output_dir)
            staged_json = json.loads((stage / "a.json").read_text(encoding="utf-8"))
            self.assertEqual(staged_json, original)
            self.assertEqual(
                (stage / "templates" / "ref.cif").read_bytes(),
                b"ORIGINAL_ROOT\n",
            )
            self.assertEqual(
                (workspace.input_dir / "templates" / "ref.cif").read_bytes(),
                b"INJECTED_ROOT\n",
            )
        finally:
            workspace.cleanup()

    def test_changed_input_replaces_canonical_without_timestamp_stamping(self):
        workspace = Workspace()
        try:
            original = workspace.monomer("vhh_a")
            workspace.write_json("a.json", original)
            first = run_script(RUNNER, default_args(workspace, "--yes"), workspace)
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)

            result = workspace.result_dir("vhh_a")
            provenance_path = result / "vhh_a_af3run_provenance.json"
            first_provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
            self.assertEqual(first_provenance["provenance_version"], 2)
            self.assertIn("database_fingerprints", first_provenance)
            self.assertIn("model_checksum", first_provenance)
            self.assertIn("image_identity", first_provenance)
            run_calls = [
                call for call in workspace.stub_calls() if call.get("call") == "run"
            ]
            self.assertTrue(run_calls)
            self.assertEqual(run_calls[-1].get("network"), "none")
            self.assertIn("@sha256:", run_calls[-1].get("image", ""))

            changed = workspace.monomer("vhh_a", sequence="CHANGEDSEQUENCE")
            workspace.write_json("a.json", changed)
            second = run_script(RUNNER, default_args(workspace, "--yes"), workspace)
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)

            canonical = json.loads(
                (result / "vhh_a_data.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                canonical["sequences"][0]["protein"]["sequence"],
                "CHANGEDSEQUENCE",
            )
            timestamps = [
                path.name
                for path in workspace.output_dir.iterdir()
                if path.is_dir() and path.name.startswith("vhh_a_")
            ]
            self.assertEqual(timestamps, [])
            second_provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
            self.assertNotEqual(
                first_provenance["effective_input_sha256"],
                second_provenance["effective_input_sha256"],
            )

            snapshots = list(
                (workspace.output_dir / ".af3_incomplete" / "vhh_a").glob("*/vhh_a_data.json")
            )
            self.assertEqual(len(snapshots), 1)
            preserved = json.loads(snapshots[0].read_text(encoding="utf-8"))
            self.assertEqual(
                preserved["sequences"][0]["protein"]["sequence"],
                original["sequences"][0]["protein"]["sequence"],
            )
        finally:
            workspace.cleanup()

    def test_sidecar_bytes_are_part_of_effective_identity(self):
        workspace = Workspace()
        try:
            sidecar = workspace.write_bytes("query.a3m", b">q\nAAAA\n")
            job = workspace.monomer("vhh_sidecar")
            job["sequences"][0]["protein"]["unpairedMsaPath"] = "query.a3m"
            workspace.write_json("a.json", job)
            first = run_script(RUNNER, default_args(workspace, "--yes"), workspace)
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            result = workspace.result_dir("vhh_sidecar")
            before = json.loads(
                (result / "vhh_sidecar_af3run_provenance.json").read_text()
            )

            sidecar.write_bytes(b">q\nCCCC\n")
            second = run_script(RUNNER, default_args(workspace, "--yes"), workspace)
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            after = json.loads(
                (result / "vhh_sidecar_af3run_provenance.json").read_text()
            )
            self.assertNotEqual(
                before["effective_input_sha256"], after["effective_input_sha256"]
            )
            self.assertEqual(after["sidecars"][0]["path"], "query.a3m")
        finally:
            workspace.cleanup()

    def test_atomic_provenance_writer_rejects_symlink_without_touching_target(self):
        runner = load_module(RUNNER, "provenance_symlink_runner")
        with tempfile.TemporaryDirectory(prefix="af3_provenance_link_") as tmp:
            root = Path(tmp)
            result = root / "result"
            result.mkdir()
            victim = root / "victim.txt"
            victim.write_text("KEEP\n", encoding="utf-8")
            (result / "x_af3run_provenance.json").symlink_to(victim)
            with self.assertRaises(OSError):
                runner.write_provenance(result, "x", {"provenance_version": 2})
            self.assertEqual(victim.read_text(encoding="utf-8"), "KEEP\n")

    def test_missing_or_wrong_version_provenance_is_not_reused_by_default(self):
        workspace = Workspace()
        try:
            workspace.write_json("a.json", workspace.monomer("vhh_a"))
            workspace.make_result("vhh_a", stage="full")
            audit = run_script(
                RUNNER, default_args(workspace, "--audit"), workspace
            )
            self.assertNotEqual(audit.returncode, 0)
            self.assertIn("provenance 기록 없음", audit.stdout)

            trusted = run_script(
                RUNNER,
                default_args(
                    workspace, "--audit", "--trust-unverified-results"
                ),
                workspace,
            )
            self.assertEqual(trusted.returncode, 0, trusted.stdout + trusted.stderr)

            result = workspace.result_dir("vhh_a")
            runner = load_module(RUNNER, "provenance_version_runner")
            path = result / "vhh_a_af3run_provenance.json"
            path.write_text(
                json.dumps({"provenance_version": 999}), encoding="utf-8"
            )
            mismatch = runner.provenance_mismatch(
                result, "vhh_a", {"provenance_version": 2}
            )
            self.assertEqual(mismatch, "provenance 버전")
        finally:
            workspace.cleanup()

    def test_full_db_child_metadata_change_invalidates_fingerprint(self):
        runner = load_module(RUNNER, "db_child_fingerprint_runner")
        workspace = Workspace()
        try:
            before = runner.database_fingerprints([workspace.db_dir], "full")
            child = workspace.db_dir / "mmcif_files" / "stub.cif"
            child.write_text("changed\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "binding"):
                runner.database_fingerprints([workspace.db_dir], "full")
            self.assertEqual(before[0]["kind"], "af3_custom_full_database_seal")
        finally:
            workspace.cleanup()

    def test_unsealed_full_db_requires_explicit_metadata_only_opt_in(self):
        runner = load_module(RUNNER, "db_unsealed_opt_in_runner")
        workspace = Workspace()
        try:
            (workspace.db_dir / "af3_full_db_manifest.json").unlink()
            with self.assertRaisesRegex(ValueError, "seal"):
                runner.database_fingerprints([workspace.db_dir], "full")
            record = runner.database_fingerprints(
                [workspace.db_dir], "full", allow_unsealed=True
            )[0]
            self.assertEqual(record["kind"], "unsealed-full-database-metadata-only")
            (workspace.db_dir / "af3_full_db_manifest.json").write_text(
                "{", encoding="utf-8"
            )
            with self.assertRaises(ValueError):
                runner.database_fingerprints(
                    [workspace.db_dir], "full", allow_unsealed=True
                )
        finally:
            workspace.cleanup()

    def test_current_result_core_must_match_staged_input(self):
        runner = load_module(RUNNER, "current_result_core_runner")
        workspace = Workspace()
        try:
            input_path = workspace.write_json(
                "a.json", workspace.monomer("vhh_a", sequence="AAAA")
            )
            job, error = runner.read_job(input_path, workspace.input_dir)
            self.assertIsNone(error)
            workspace.make_result("vhh_a", stage="full")
            wrong = workspace.monomer("vhh_a", sequence="BBBB")
            (workspace.result_dir("vhh_a") / "vhh_a_data.json").write_text(
                json.dumps(wrong), encoding="utf-8"
            )
            problem = runner.current_result_problem(
                workspace.result_dir("vhh_a"), job, "full"
            )
            self.assertIn("core", problem)
        finally:
            workspace.cleanup()

    def test_current_result_accepts_official_grouping_of_identical_chains(self):
        runner = load_module(RUNNER, "current_result_grouped_chains_runner")
        workspace = Workspace()
        try:
            requested = workspace.monomer("vhh_dimer", sequence="AAAA")
            requested["sequences"] = [
                {"protein": {"id": "A", "sequence": "AAAA"}},
                {"protein": {"id": "B", "sequence": "AAAA"}},
            ]
            input_path = workspace.write_json("dimer.json", requested)
            job, error = runner.read_job(input_path, workspace.input_dir)
            self.assertIsNone(error)
            workspace.make_result("vhh_dimer", stage="full")

            emitted = dict(requested)
            emitted["sequences"] = [
                {"protein": {"id": ["A", "B"], "sequence": "AAAA"}}
            ]
            data_path = workspace.result_dir("vhh_dimer") / "vhh_dimer_data.json"
            data_path.write_text(json.dumps(emitted), encoding="utf-8")
            self.assertIsNone(
                runner.current_result_problem(
                    workspace.result_dir("vhh_dimer"), job, "full"
                )
            )

            emitted["sequences"][0]["protein"]["id"] = ["A", "B", "C"]
            data_path.write_text(json.dumps(emitted), encoding="utf-8")
            self.assertIn(
                "core",
                runner.current_result_problem(
                    workspace.result_dir("vhh_dimer"), job, "full"
                ),
            )
        finally:
            workspace.cleanup()

    def test_current_result_accepts_official_materialized_defaults(self):
        runner = load_module(RUNNER, "current_result_materialized_defaults_runner")
        workspace = Workspace()
        try:
            requested = workspace.monomer("vhh_defaults", sequence="AAAA")
            protein = requested["sequences"][0]["protein"]
            protein.update({"unpairedMsa": "", "pairedMsa": "", "templates": []})
            requested["bondedAtomPairs"] = []
            input_path = workspace.write_json("defaults.json", requested)
            job, error = runner.read_job(input_path, workspace.input_dir)
            self.assertIsNone(error)
            workspace.make_result("vhh_defaults", stage="full")

            emitted = json.loads(json.dumps(requested))
            emitted_protein = emitted["sequences"][0]["protein"]
            emitted_protein["unpairedMsa"] = ">query\nAAAA\n"
            emitted_protein["pairedMsa"] = ">query\nAAAA\n"
            emitted["bondedAtomPairs"] = None
            data_path = (
                workspace.result_dir("vhh_defaults") / "vhh_defaults_data.json"
            )
            data_path.write_text(json.dumps(emitted), encoding="utf-8")
            self.assertIsNone(
                runner.current_result_problem(
                    workspace.result_dir("vhh_defaults"), job, "full"
                )
            )
        finally:
            workspace.cleanup()

    def test_tampered_final_artifact_invalidates_provenance(self):
        workspace = Workspace()
        try:
            workspace.write_json("a.json", workspace.monomer("vhh_a"))
            first = run_script(RUNNER, default_args(workspace, "--yes"), workspace)
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            model = workspace.result_dir("vhh_a") / "vhh_a_model.cif"
            model.write_text("tampered but nonempty\n", encoding="utf-8")
            audit = run_script(RUNNER, default_args(workspace, "--audit"), workspace)
            self.assertNotEqual(audit.returncode, 0)
            self.assertIn("artifact hash/size", audit.stdout)
        finally:
            workspace.cleanup()

    def test_image_inspect_failure_stops_before_probe_or_run(self):
        workspace = Workspace()
        try:
            workspace.write_json("a.json", workspace.monomer("vhh_a"))
            result = run_script(
                RUNNER,
                default_args(workspace, "--yes"),
                workspace,
                env_extra={"AF3_STUB_INSPECT_FAIL": "1"},
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("immutable ID/digest", result.stdout)
            calls = workspace.stub_calls()
            self.assertTrue(any(call.get("call") == "inspect" for call in calls))
            self.assertFalse(
                any(call.get("call") in {"help", "run"} for call in calls)
            )
        finally:
            workspace.cleanup()


class GPUReservationTests(unittest.TestCase):
    @staticmethod
    def _write_inventory(path: Path, rows: str) -> None:
        path.write_text("#!/bin/sh\nprintf '%s\\n' '" + rows.replace("\n", "' '") + "'\n", encoding="utf-8")
        path.chmod(0o755)

    @staticmethod
    def _load_legacy(name: str):
        spec = importlib.util.spec_from_file_location(name, LEGACY_PATH)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module

    def test_selects_and_binds_only_a_95_percent_ready_device(self):
        runner = load_module(RUNNER, "gpu_selection_runner")
        with tempfile.TemporaryDirectory(prefix="af3_gpu_inventory_") as tmp:
            binary = Path(tmp) / "nvidia-smi"
            self._write_inventory(
                binary,
                "0, GPU-low, 11000, 12288\n1, GPU-ready, 16000, 16384",
            )
            old_path = os.environ.get("PATH", "")
            os.environ["PATH"] = f"{tmp}{os.pathsep}{old_path}"
            try:
                with runner.gpu_reservation(
                    ("docker",), allow_busy=False, memory_fraction=0.95
                ) as selected:
                    self.assertIsNotNone(selected)
                    self.assertEqual(selected.uuid, "GPU-ready")
                    command = runner.docker_base(
                        docker_command=("docker",),
                        image="alphafold3",
                        mode="inference",
                        input_mount=Path("/tmp/in"),
                        output_dir=Path("/tmp/out"),
                        db_dirs=[],
                        model_dir=Path("/tmp/model"),
                        cache_dir=Path("/tmp/cache"),
                        use_cache=False,
                        gpu_device=selected,
                        gpu_memory_fraction=0.95,
                    )
                    gpu_pos = command.index("--gpus")
                    self.assertEqual(command[gpu_pos + 1], "device=GPU-ready")
                    self.assertIn("XLA_CLIENT_MEM_FRACTION=0.95", command)
                    self.assertNotIn("XLA_PYTHON_CLIENT_MEM_FRACTION", command)
                    self.assertIsNone(runner._try_gpu_lease("GPU-ready"))
            finally:
                os.environ["PATH"] = old_path

    def test_cross_output_runners_share_the_same_gpu_lease(self):
        workspace = Workspace()
        first_output = workspace.root / "first_out"
        second_output = workspace.root / "second_out"
        first_output.mkdir()
        second_output.mkdir()
        workspace.write_json("a.json", workspace.monomer("vhh_a"))
        first_log = workspace.root / "first.jsonl"
        second_log = workspace.root / "second.jsonl"
        try:
            binary_dir = make_stub_bin(workspace.root)
            self._write_inventory(
                binary_dir / "nvidia-smi", "0, GPU-shared, 16000, 16384"
            )
            base_env = {
                key: value
                for key, value in os.environ.items()
                if not key.startswith("AF3_")
            }
            base_env["PATH"] = f"{binary_dir}{os.pathsep}{base_env.get('PATH', '')}"
            base_env["AF3_GPU_LEASE_DIR"] = str(workspace.root / "gpu_leases")
            base_env["AF3_STUB_SLEEP"] = "2"
            base_args = [
                "--input-dir", str(workspace.input_dir),
                "--db-dir", str(workspace.db_dir),
                "--model-dir", str(workspace.model_dir),
                "--cache-dir", str(workspace.cache_dir),
                "--yes",
            ]
            first_env = dict(base_env, AF3_STUB_LOG=str(first_log))
            first = subprocess.Popen(
                [sys.executable, str(RUNNER_PATH), *base_args, "--output-dir", str(first_output)],
                cwd=workspace.root,
                env=first_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if first_log.exists() and '"call": "run"' in first_log.read_text():
                    break
                time.sleep(0.05)
            else:
                first.kill()
                self.fail("first runner did not reach Docker while holding the GPU lease")

            second_env = dict(base_env, AF3_STUB_LOG=str(second_log))
            second = subprocess.run(
                [sys.executable, str(RUNNER_PATH), *base_args, "--output-dir", str(second_output)],
                cwd=workspace.root,
                env=second_env,
                capture_output=True,
                text=True,
                timeout=15,
            )
            first_output_text, _ = first.communicate(timeout=15)
            self.assertEqual(first.returncode, 0, first_output_text)
            self.assertNotEqual(second.returncode, 0, second.stdout + second.stderr)
            self.assertIn("lease", second.stdout + second.stderr)
            second_runs = (
                second_log.read_text(encoding="utf-8") if second_log.exists() else ""
            )
            self.assertNotIn('"call": "run"', second_runs)
        finally:
            workspace.cleanup()

    def test_known_preferred_and_unknown_legacy_share_global_gate(self):
        runner = load_module(RUNNER, "gpu_known_preferred_global_gate")
        legacy = self._load_legacy("gpu_unknown_legacy_global_gate")
        device = runner.GPUDevice("0", "GPU-known", 16000, 16384)
        with tempfile.TemporaryDirectory(prefix="af3_gpu_global_gate_") as tmp:
            saved = os.environ.get("AF3_GPU_LEASE_DIR")
            os.environ["AF3_GPU_LEASE_DIR"] = str(Path(tmp) / "leases")
            try:
                runner.gpu_inventory = lambda: [device]
                legacy._visible_gpu_keys = lambda: ["unknown-all-devices"]
                with runner.gpu_reservation(
                    ("docker",), allow_busy=True, memory_fraction=0.95
                ):
                    with self.assertRaises(BlockingIOError):
                        with legacy.gpu_leases_for_legacy(True):
                            pass

                # Reverse ownership: the legacy unknown run owns the global
                # gate exclusively, so the preferred known run cannot split.
                with legacy.gpu_leases_for_legacy(True):
                    with self.assertRaises(runner.GPUAdmissionError):
                        with runner.gpu_reservation(
                            ("docker",), allow_busy=True, memory_fraction=0.95
                        ):
                            pass
            finally:
                if saved is None:
                    os.environ.pop("AF3_GPU_LEASE_DIR", None)
                else:
                    os.environ["AF3_GPU_LEASE_DIR"] = saved

    def test_unknown_preferred_and_known_legacy_share_global_gate(self):
        runner = load_module(RUNNER, "gpu_unknown_preferred_global_gate")
        legacy = self._load_legacy("gpu_known_legacy_global_gate")
        device = runner.GPUDevice("0", "GPU-known", 16000, 16384)
        with tempfile.TemporaryDirectory(prefix="af3_gpu_global_gate_") as tmp:
            saved = os.environ.get("AF3_GPU_LEASE_DIR")
            os.environ["AF3_GPU_LEASE_DIR"] = str(Path(tmp) / "leases")
            try:
                runner.gpu_inventory = lambda: []
                legacy._visible_gpu_keys = lambda: [device.uuid]
                with runner.gpu_reservation(
                    ("docker",), allow_busy=True, memory_fraction=0.95
                ):
                    with self.assertRaises(BlockingIOError):
                        with legacy.gpu_leases_for_legacy(True):
                            pass

                # Reverse ownership: the legacy known run shares the global
                # gate, which blocks an unknown preferred exclusive owner.
                with legacy.gpu_leases_for_legacy(True):
                    with self.assertRaises(runner.GPUAdmissionError):
                        with runner.gpu_reservation(
                            ("docker",), allow_busy=True, memory_fraction=0.95
                        ):
                            pass
            finally:
                if saved is None:
                    os.environ.pop("AF3_GPU_LEASE_DIR", None)
                else:
                    os.environ["AF3_GPU_LEASE_DIR"] = saved

    def test_configurable_run_timeout_returns_failure(self):
        runner = load_module(RUNNER, "gpu_timeout_runner")
        started = time.monotonic()
        code = runner.run_docker(
            [sys.executable, "-c", "import time; time.sleep(5)"], timeout=1
        )
        self.assertEqual(code, 124)
        self.assertLess(time.monotonic() - started, 3)

    def test_watchdog_signature_is_limited_to_current_targets(self):
        runner = load_module(RUNNER, "bounded_watchdog_signature_runner")
        with tempfile.TemporaryDirectory(prefix="af3_watchdog_scope_") as td:
            root = Path(td)
            current = root / "current"
            unrelated = root / "already_complete"
            current.mkdir()
            unrelated.mkdir()
            (current / "progress.bin").write_bytes(b"a")
            (unrelated / "large-history.bin").write_bytes(b"history")
            before = runner._progress_signature((current,))
            (unrelated / "large-history.bin").write_bytes(b"changed history")
            self.assertEqual(runner._progress_signature((current,)), before)
            (current / "progress.bin").write_bytes(b"current progress")
            self.assertNotEqual(runner._progress_signature((current,)), before)

    def test_no_progress_watchdog_returns_failure(self):
        runner = load_module(RUNNER, "gpu_no_progress_runner")
        with tempfile.TemporaryDirectory(prefix="af3_no_progress_") as td:
            started = time.monotonic()
            code = runner.run_docker(
                [sys.executable, "-c", "import time; time.sleep(5)"],
                progress_dir=Path(td),
                no_progress_timeout=1,
            )
            self.assertEqual(code, 124)
            self.assertLess(time.monotonic() - started, 3)


def _run_case(case_type: type[unittest.TestCase], method: str) -> None:
    case = case_type(method)
    result = unittest.TestResult()
    case.run(result)
    if result.errors:
        raise AssertionError(result.errors[0][1])
    if result.failures:
        raise AssertionError(result.failures[0][1])


@regression(
    item="input-snapshot",
    prevents="JSON/sidecar를 검사한 뒤 Docker staging 전에 교체해 parsed identity와 실행 bytes를 갈라놓는 버그",
)
def registered_private_input_snapshot_consistency():
    _run_case(
        ProvenanceTests,
        "test_private_snapshot_drives_parse_provenance_and_staging",
    )


@regression(
    item="input-snapshot",
    prevents="JSON sidecar 경로의 NUL 문자가 전체 batch를 uncaught ValueError로 중단하는 버그",
)
def registered_embedded_nul_sidecar_rejection():
    _run_case(
        ProvenanceTests,
        "test_embedded_nul_sidecar_path_is_a_per_file_validation_error",
    )


@regression(
    item="input-snapshot",
    prevents="lstat 직후 입력 JSON을 symlink로 바꿔 외부 파일을 snapshot하는 check-then-open race",
)
def registered_input_json_symlink_swap_rejection():
    _run_case(
        ProvenanceTests,
        "test_symlink_swap_during_json_snapshot_fails_closed",
    )


@regression(
    item="input-snapshot",
    prevents="sidecar를 snapshot 복사 도중 수정해 provenance hash와 Docker 입력을 불일치시키는 race",
)
def registered_sidecar_snapshot_mutation_rejection():
    _run_case(
        ProvenanceTests,
        "test_sidecar_mutation_during_snapshot_fails_closed",
    )


@regression(
    item="input-snapshot",
    prevents="nested sidecar의 상위 폴더를 검사 뒤 외부 symlink로 바꿔 input root를 탈출하는 race",
)
def registered_nested_sidecar_parent_swap_confinement():
    _run_case(
        ProvenanceTests,
        "test_nested_sidecar_parent_swap_stays_on_open_directory",
    )


@regression(
    item="input-snapshot",
    prevents="JSON snapshot 뒤 input root 전체를 교체해 원본 JSON과 주입 sidecar를 혼합하는 race",
)
def registered_whole_input_root_swap_confinement():
    _run_case(
        ProvenanceTests,
        "test_whole_input_root_swap_cannot_create_hybrid_snapshot",
    )


@regression(
    item="provenance-v2",
    prevents="changed-input 결과가 timestamp sibling에 쓰이고 old canonical에 new provenance가 찍히는 버그",
)
def registered_changed_input_canonical_transaction():
    _run_case(
        ProvenanceTests,
        "test_changed_input_replaces_canonical_without_timestamp_stamping",
    )


@regression(
    item="provenance-v2",
    prevents="sidecar bytes 변경이 provenance identity에 포함되지 않는 버그",
)
def registered_sidecar_identity():
    _run_case(ProvenanceTests, "test_sidecar_bytes_are_part_of_effective_identity")


@regression(
    item="provenance-v2",
    prevents="full DB 하위 mmCIF를 in-place 수정해도 provenance DB fingerprint가 같게 남는 버그",
)
def registered_full_db_child_fingerprint():
    _run_case(
        ProvenanceTests,
        "test_full_db_child_metadata_change_invalidates_fingerprint",
    )


@regression(
    item="provenance-v2",
    prevents="seal 없는 full DB를 기본 허용하거나 malformed seal을 metadata-only로 downgrade하는 버그",
)
def registered_unsealed_full_db_policy():
    _run_case(
        ProvenanceTests,
        "test_unsealed_full_db_requires_explicit_metadata_only_opt_in",
    )


@regression(
    item="provenance-v2",
    prevents="same-name stale _data.json의 sequence/seed/ligand core에 새 provenance를 쓰는 버그",
)
def registered_current_result_core_identity():
    _run_case(ProvenanceTests, "test_current_result_core_must_match_staged_input")
    _run_case(
        ProvenanceTests,
        "test_current_result_accepts_official_grouping_of_identical_chains",
    )
    _run_case(
        ProvenanceTests,
        "test_current_result_accepts_official_materialized_defaults",
    )


@regression(
    item="provenance-v2",
    prevents="provenance commit 후 canonical final artifact가 바뀌어도 재사용하는 버그",
)
def registered_final_artifact_hashes():
    _run_case(ProvenanceTests, "test_tampered_final_artifact_invalidates_provenance")


@regression(
    item="provenance-v2",
    prevents="도커 image inspect 실패 뒤 mutable tag로 계산해 provenance identity를 잃는 버그",
)
def registered_image_identity_fail_closed():
    _run_case(
        ProvenanceTests,
        "test_image_inspect_failure_stops_before_probe_or_run",
    )


@regression(
    item="provenance-v2",
    prevents="provenance symlink를 따라 외부 파일을 덮어쓰는 버그",
)
def registered_atomic_provenance_symlink_rejection():
    _run_case(
        ProvenanceTests,
        "test_atomic_provenance_writer_rejects_symlink_without_touching_target",
    )


@regression(
    item="provenance-v2",
    prevents="provenance가 없거나 schema version이 다른 결과를 검증 완료로 재사용하는 버그",
)
def registered_missing_and_versioned_provenance_policy():
    _run_case(
        ProvenanceTests,
        "test_missing_or_wrong_version_provenance_is_not_reused_by_default",
    )


@regression(
    item="gpu-lease",
    prevents="확인한 GPU와 Docker에 bind한 GPU가 다르거나 JAX 95% 문턱보다 낮은 장치를 고르는 버그",
)
def registered_per_device_gpu_selection():
    _run_case(
        GPUReservationTests,
        "test_selects_and_binds_only_a_95_percent_ready_device",
    )


@regression(
    item="gpu-lease",
    prevents="서로 다른 output의 두 runner가 같은 GPU idle check를 동시에 통과하는 버그",
)
def registered_cross_output_gpu_lease():
    _run_case(
        GPUReservationTests, "test_cross_output_runners_share_the_same_gpu_lease"
    )


@regression(
    item="gpu-lease",
    prevents="preferred known-device 실행과 legacy unknown-all-devices 실행이 서로 다른 lock을 잡는 버그",
)
def registered_known_preferred_unknown_legacy_global_gate():
    _run_case(
        GPUReservationTests,
        "test_known_preferred_and_unknown_legacy_share_global_gate",
    )


@regression(
    item="gpu-lease",
    prevents="preferred unknown-all-devices 실행과 legacy known-device 실행이 서로 다른 lock을 잡는 버그",
)
def registered_unknown_preferred_known_legacy_global_gate():
    _run_case(
        GPUReservationTests,
        "test_unknown_preferred_and_known_legacy_share_global_gate",
    )


@regression(
    item="watchdog",
    prevents="사용자가 요청한 Docker runtime bound가 적용되지 않아 영구 정지를 회수할 수 없는 버그",
)
def registered_configurable_run_timeout():
    _run_case(GPUReservationTests, "test_configurable_run_timeout_returns_failure")


@regression(
    item="watchdog",
    prevents="기본 대형 batch가 artifact 진전 없이 멈춰 output/GPU lease를 영구 보유하는 버그",
)
def registered_no_progress_watchdog():
    _run_case(GPUReservationTests, "test_no_progress_watchdog_returns_failure")


@regression(
    item="watchdog",
    prevents="watchdog가 2,000건의 과거 완료 tree를 반복 scan해 실행시간이 누적되는 버그",
)
def registered_watchdog_current_target_scope():
    _run_case(
        GPUReservationTests,
        "test_watchdog_signature_is_limited_to_current_targets",
    )


if __name__ == "__main__":
    unittest.main(verbosity=2)
