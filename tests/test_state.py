#!/usr/bin/env python3
"""staging·격리·중복 실행 안전성 회귀 테스트 (과제 항목 7, 8, 9).

이 모듈이 지키는 원칙: **남의 파일을 지우지 않는다. 무한히 쌓지도 않는다.**
러너가 작업 폴더 안에 상태를 만들기 때문에, 삭제 판정이 한 칸만 느슨해져도
연구자가 같은 폴더에 둔 다른 파일이 사라진다. 40시간 배치에서 이런 사고는
복구 불가다.
"""

from __future__ import annotations

import json
import inspect
import os
import subprocess
import sys
import time
from pathlib import Path

from harness import (
    Workspace,
    check,
    check_equal,
    check_in,
    default_args,
    load_module,
    make_stub_bin,
    regression,
    run_script,
)

RUNNER = "run_af3_batch_improved.py"


@regression(
    item="7",
    prevents="고정 이름 staging 폴더를 무조건 rmtree 해서, 같은 이름의 남의 폴더를 "
    "날리는 버그. 소유 표식이 없는 폴더는 보존해야 한다.",
)
def test_foreign_staging_dir_is_preserved():
    mod = load_module(RUNNER)
    workspace = Workspace()
    try:
        # 러너가 쓰는 접두어와 같은 이름이지만, 소유 표식이 없는 남의 폴더.
        foreign = workspace.root / ".af3_pending_myown_data"
        foreign.mkdir()
        (foreign / "귀중한파일.txt").write_text("지우면 안 된다\n", encoding="utf-8")

        statuses = mod.scan_stage_dirs(
            workspace.root, workspace.output_dir, stale_after_seconds=0
        )
        found = [s for s in statuses if s.path == foreign]
        check_equal(len(found), 1, "남의 staging 후보를 목록에서 놓쳤다")
        check(
            not found[0].removable,
            "소유 표식 없는 폴더를 삭제 대상으로 분류했다",
            f"이유={found[0].reason}",
        )

        # 실제 실행에서도 살아남아야 한다.
        workspace.write_json("a.json", workspace.monomer("vhh_a"))
        proc = run_script(RUNNER, default_args(workspace, "--yes"), workspace)
        check_equal(proc.returncode, 0, f"실행 실패\n{proc.stdout[-1200:]}")
        check(
            (foreign / "귀중한파일.txt").is_file(),
            "남의 staging 폴더 안 파일을 지웠다",
        )

        # --cleanup 에서도 보존해야 한다.
        cleanup = run_script(RUNNER, default_args(workspace, "--cleanup", "--yes"), workspace)
        check_equal(cleanup.returncode, 0, "정리가 실패했다")
        check(
            (foreign / "귀중한파일.txt").is_file(),
            "--cleanup 이 남의 staging 폴더를 지웠다",
        )
        check_in("보존", cleanup.stdout, "무엇을 보존했는지 알려주지 않았다")
    finally:
        workspace.cleanup()


@regression(
    item="7",
    prevents="다른 출력 폴더용 staging 이나 다른 호스트에서 만든 staging 을 지워, "
    "공유 스토리지에서 동시에 도는 다른 실행을 망가뜨리는 버그.",
)
def test_staging_from_other_run_is_not_removed():
    mod = load_module(RUNNER)
    workspace = Workspace()
    try:
        cases = {}
        # (a) 다른 출력 폴더용
        other = workspace.root / ".af3_pending_other"
        other.mkdir()
        mod.write_marker(
            other / mod.STAGE_MARKER_NAME,
            {
                "script": mod.SCRIPT_ID,
                "version": mod.STATE_FORMAT_VERSION,
                "kind": "stage",
                "pid": os.getpid(),
                "hostname": __import__("socket").gethostname(),
                "created_at_epoch": 0.0,
                "output_dir": str((workspace.root / "다른_out").resolve()),
                "stage_dir": str(other.resolve()),
            },
        )
        cases["다른 출력 폴더용"] = other

        # (b) 다른 호스트에서 생성
        remote = workspace.root / ".af3_pending_remote"
        remote.mkdir()
        mod.write_marker(
            remote / mod.STAGE_MARKER_NAME,
            {
                "script": mod.SCRIPT_ID,
                "version": mod.STATE_FORMAT_VERSION,
                "kind": "stage",
                "pid": os.getpid(),
                "hostname": "다른호스트",
                "created_at_epoch": 0.0,
                "output_dir": str(workspace.output_dir.resolve()),
                "stage_dir": str(remote.resolve()),
            },
        )
        cases["다른 호스트에서 생성"] = remote

        # (c) 아직 살아 있는 프로세스의 것
        alive = workspace.root / ".af3_pending_alive"
        alive.mkdir()
        mod.write_marker(
            alive / mod.STAGE_MARKER_NAME,
            {
                "script": mod.SCRIPT_ID,
                "version": mod.STATE_FORMAT_VERSION,
                "kind": "stage",
                "pid": os.getpid(),  # 지금 이 테스트 프로세스는 확실히 살아 있다
                "hostname": __import__("socket").gethostname(),
                "created_at_epoch": 0.0,
                "output_dir": str(workspace.output_dir.resolve()),
                "stage_dir": str(alive.resolve()),
            },
        )
        cases["실행 중인 PID"] = alive

        # (d) 표식이 손상된 것
        corrupt = workspace.root / ".af3_pending_corrupt"
        corrupt.mkdir()
        (corrupt / mod.STAGE_MARKER_NAME).write_text("{깨진", encoding="utf-8")
        cases["소유 표식 손상"] = corrupt

        statuses = {
            s.path: s
            for s in mod.scan_stage_dirs(
                workspace.root, workspace.output_dir, stale_after_seconds=0
            )
        }
        for label, path in cases.items():
            status = statuses.get(path)
            check(status is not None, f"{label} staging 을 목록에서 놓쳤다")
            check(
                not status.removable,
                f"{label} staging 을 삭제 대상으로 분류했다",
                f"이유={status.reason}",
            )

        mod.remove_removable_stages(list(statuses.values()))
        for label, path in cases.items():
            check(path.is_dir(), f"{label} staging 을 실제로 지웠다")
    finally:
        workspace.cleanup()


@regression(
    item="7",
    prevents="종료된 자기 실행의 staging 잔여물을 영구히 쌓아 디스크를 채우는 버그. "
    "소유가 확실하고 프로세스가 죽었고 유예시간이 지난 것은 지워야 한다.",
)
def test_own_dead_staging_is_cleaned():
    mod = load_module(RUNNER)
    workspace = Workspace()
    try:
        # 확실히 죽은 PID 를 얻는다 (바로 끝나는 자식 프로세스).
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        dead_pid = dead.pid
        if mod.process_is_alive(dead_pid):
            # PID 재사용 등으로 살아 있다고 판정되면 이 케이스를 검증할 수 없다.
            raise AssertionError(
                f"죽은 PID {dead_pid} 가 살아 있다고 나온다. 테스트 전제가 깨졌다"
            )

        stale = workspace.root / ".af3_pending_stale"
        stale.mkdir()
        (stale / "a.json").write_text("{}", encoding="utf-8")
        mod.write_marker(
            stale / mod.STAGE_MARKER_NAME,
            {
                "script": mod.SCRIPT_ID,
                "version": mod.STATE_FORMAT_VERSION,
                "kind": "stage",
                "pid": dead_pid,
                "hostname": __import__("socket").gethostname(),
                "created_at_epoch": time.time() - 48 * 3600,
                "output_dir": str(workspace.output_dir.resolve()),
                "stage_dir": str(stale.resolve()),
            },
        )
        statuses = mod.scan_stage_dirs(
            workspace.root, workspace.output_dir, stale_after_seconds=24 * 3600
        )
        target = [s for s in statuses if s.path == stale]
        check_equal(len(target), 1, "자기 staging 잔여물을 못 찾았다")
        check(
            target[0].removable,
            "죽은 자기 실행의 오래된 staging 을 정리 대상으로 잡지 않았다",
            f"이유={target[0].reason}",
        )
        mod.remove_removable_stages(statuses)
        check(not stale.exists(), "정리 대상으로 잡고도 지우지 않았다")
    finally:
        workspace.cleanup()


@regression(
    item="7",
    prevents="staging 폴더가 실행 후에도 남아 다음 실행에서 옛 입력이 섞이는 버그.",
)
def test_staging_dir_is_removed_after_run():
    workspace = Workspace()
    try:
        workspace.write_json("a.json", workspace.monomer("vhh_a"))
        proc = run_script(RUNNER, default_args(workspace, "--yes"), workspace)
        check_equal(proc.returncode, 0, f"실행 실패\n{proc.stdout[-1200:]}")
        leftovers = sorted(
            p.name
            for p in workspace.root.iterdir()
            if p.name.startswith(".af3_pending_")
        )
        check_equal(leftovers, [], "실행이 끝났는데 staging 폴더가 남았다")
    finally:
        workspace.cleanup()


@regression(
    item="8",
    prevents="반복 실패가 격리 폴더에 무한히 쌓여 디스크를 채우는 버그. 2000건 배치에서 "
    "각 건이 10번 실패하면 미완료 결과 20000개가 남는다.",
)
def test_quarantine_growth_is_bounded_per_job():
    workspace = Workspace()
    try:
        workspace.write_json("a.json", workspace.monomer("vhh_a"))
        quarantine = workspace.output_dir / ".af3_incomplete" / "vhh_a"

        # 같은 건을 5번 연속 실패시킨다.
        for attempt in range(5):
            proc = run_script(
                RUNNER,
                default_args(workspace, "--yes"),
                workspace,
                env_extra={"AF3_STUB_FAIL_NAMES": "vhh_a"},
            )
            check(
                proc.returncode != 0,
                f"{attempt + 1}번째 시도가 실패했는데 종료코드가 0 이다",
            )
            snapshots = (
                sorted(p.name for p in quarantine.iterdir() if p.is_dir())
                if quarantine.is_dir()
                else []
            )
            check(
                len(snapshots) <= 1,
                f"{attempt + 1}번째 시도 후 격리 보존본이 기본값(1개)을 넘었다",
                f"보존본={snapshots}",
            )
    finally:
        workspace.cleanup()


@regression(
    item="8",
    prevents="--quarantine-keep 을 늘렸을 때 그 개수를 지키지 않는 버그. "
    "'최근 3번의 실패를 비교하고 싶다' 는 요구를 만족시키지 못한다.",
)
def test_quarantine_keep_option_is_honoured():
    mod = load_module(RUNNER)
    workspace = Workspace()
    try:
        job = mod.Job(
            json_file=workspace.input_dir / "a.json",
            output_name="vhh_a",
            raw_name="vhh_a",
            sidecars=(),
        )
        for _ in range(6):
            # 매번 미완료 결과를 새로 만들고 격리한다.
            workspace.make_result("vhh_a", stage="data")
            mod.quarantine_incomplete(workspace.output_dir, job, "full", keep=3)
            time.sleep(0.002)  # 타임스탬프가 겹치지 않게

        quarantine = workspace.output_dir / ".af3_incomplete" / "vhh_a"
        snapshots = sorted(p.name for p in quarantine.iterdir() if p.is_dir())
        check_equal(
            len(snapshots),
            3,
            f"--quarantine-keep 3 을 지키지 않았다 (보존본={snapshots})",
        )
        # 남은 것은 최신 3개여야 한다 (이름이 타임스탬프이므로 정렬로 확인 가능).
        check_equal(snapshots, sorted(snapshots)[-3:], "오래된 것을 남기고 최신을 지웠다")
    finally:
        workspace.cleanup()


@regression(
    item="8",
    prevents="격리 정리가 소유 표식을 확인하지 않아, 연구자가 격리 폴더 안에 직접 둔 "
    "폴더를 지우는 버그.",
)
def test_quarantine_pruning_only_touches_own_snapshots():
    mod = load_module(RUNNER)
    workspace = Workspace()
    try:
        job = mod.Job(
            json_file=workspace.input_dir / "a.json",
            output_name="vhh_a",
            raw_name="vhh_a",
            sidecars=(),
        )
        quarantine = workspace.output_dir / ".af3_incomplete" / "vhh_a"
        quarantine.mkdir(parents=True)
        # 연구자가 직접 만든 폴더 (표식 없음). 이름이 정렬상 앞이라 먼저 지워질 후보다.
        manual = quarantine / "00000000_000000_내가둔것"
        manual.mkdir()
        (manual / "메모.txt").write_text("보존\n", encoding="utf-8")

        for _ in range(4):
            workspace.make_result("vhh_a", stage="data")
            mod.quarantine_incomplete(workspace.output_dir, job, "full", keep=1)
            time.sleep(0.002)

        check(
            (manual / "메모.txt").is_file(),
            "표식 없는 사용자 폴더를 격리 정리가 지웠다",
            f"격리 폴더 내용={sorted(p.name for p in quarantine.iterdir())}",
        )
    finally:
        workspace.cleanup()


@regression(
    item="8",
    prevents="정상 완료 결과를 격리로 옮겨 결과를 잃는 버그. 격리는 미완료에만 적용해야 한다.",
)
def test_complete_results_are_never_quarantined():
    mod = load_module(RUNNER)
    workspace = Workspace()
    try:
        workspace.make_result("vhh_a", stage="full")
        job = mod.Job(
            json_file=workspace.input_dir / "a.json",
            output_name="vhh_a",
            raw_name="vhh_a",
            sidecars=(),
        )
        moved = mod.quarantine_incomplete(workspace.output_dir, job, "full", keep=1)
        check_equal(moved, None, "정상 완료 결과를 격리로 옮겼다")
        check(
            (workspace.output_dir / "vhh_a" / "vhh_a_model.cif").is_file(),
            "완료 결과가 원래 자리에 없다",
        )
        # data 모드에서도 _data.json 만 있으면 완료이므로 옮기지 않아야 한다.
        workspace.make_result("vhh_b", stage="data")
        job_b = mod.Job(
            json_file=workspace.input_dir / "b.json",
            output_name="vhh_b",
            raw_name="vhh_b",
            sidecars=(),
        )
        check_equal(
            mod.quarantine_incomplete(workspace.output_dir, job_b, "data", keep=1),
            None,
            "data 모드 완료 결과를 격리로 옮겼다",
        )
    finally:
        workspace.cleanup()


@regression(
    item="9",
    prevents="같은 출력 폴더에 두 실행이 동시에 붙어, 서로의 미완료 판정을 뒤집고 "
    "AF3 가 타임스탬프 폴더를 흩뿌리는 버그. 40시간 배치에서 실수로 두 번 띄우기 쉽다.",
)
def test_concurrent_run_on_same_output_is_blocked():
    workspace = Workspace()
    try:
        for index in range(1, 4):
            workspace.write_json(
                f"j{index}.json", workspace.monomer(f"vhh_{index:03d}")
            )
        bin_dir = make_stub_bin(workspace.root)
        env = dict(os.environ)
        env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
        env["AF3_STUB_LOG"] = str(workspace.stub_log)
        env["AF3_STUB_SLEEP"] = "3"  # 첫 실행이 잠금을 3초 붙들고 있게 한다
        env["PYTHONIOENCODING"] = "utf-8"

        from harness import SCRIPTS_DIR

        command = [
            sys.executable,
            str(SCRIPTS_DIR / RUNNER),
            *default_args(workspace, "--yes"),
        ]
        first = subprocess.Popen(
            command,
            cwd=str(workspace.root),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            # 첫 실행이 잠금을 잡을 시간을 준다.
            lock_path = workspace.output_dir / ".run_af3_batch.lock"
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if lock_path.exists() and lock_path.read_text(encoding="utf-8").strip():
                    break
                time.sleep(0.05)

            env_second = dict(env)
            env_second["AF3_STUB_SLEEP"] = "0"
            second = subprocess.run(
                command,
                cwd=str(workspace.root),
                env=env_second,
                capture_output=True,
                text=True,
                timeout=60,
            )
            check(
                second.returncode != 0,
                "중복 실행이 차단되지 않고 0 으로 끝났다",
                f"종료코드={second.returncode}\n{second.stdout[-1200:]}",
            )
            check_in(
                "다른 실행이 이 출력 폴더를 사용 중",
                second.stdout,
                "중복 실행 차단 이유를 알려주지 않았다",
            )
        finally:
            first.wait(timeout=120)

        # 첫 실행은 정상적으로 끝났어야 한다.
        check_equal(first.returncode, 0, "첫 실행이 실패했다")
        stamped = [
            p.name
            for p in workspace.output_dir.iterdir()
            if p.is_dir() and p.name.startswith("vhh_") and p.name.count("_") > 1
        ]
        check_equal(stamped, [], "타임스탬프 접미사 폴더가 생겼다 (동시 실행 흔적)")
    finally:
        workspace.cleanup()


@regression(
    item="9",
    prevents="잠금 파일 자체가 결과 집계나 완료 판정에 섞이는 버그.",
)
def test_lock_file_is_not_mistaken_for_a_result():
    mod = load_module(RUNNER)
    workspace = Workspace()
    try:
        workspace.write_json("a.json", workspace.monomer("vhh_a"))
        proc = run_script(RUNNER, default_args(workspace, "--yes"), workspace)
        check_equal(proc.returncode, 0, "실행 실패")
        lock = workspace.output_dir / ".run_af3_batch.lock"
        check(lock.is_file(), "잠금 파일이 만들어지지 않았다")
        check(
            not mod.is_safe_output_name(".run_af3_batch.lock"),
            "잠금 파일 이름을 결과 이름으로 허용했다",
        )
        # --audit 이 잠금 파일을 미완료 타깃으로 세지 않아야 한다.
        audit = run_script(RUNNER, default_args(workspace, "--audit"), workspace)
        check_in("완료 1개, 미완료 0개", audit.stdout, "잠금 파일을 결과로 셌다")
    finally:
        workspace.cleanup()


@regression(
    item="9",
    prevents="잠금을 걸기 전에 판정한 미완료 목록을 그대로 써서, 대기 중에 다른 실행이 "
    "끝낸 작업을 또 돌리는 버그.",
)
def test_pending_list_is_rechecked_after_acquiring_lock():
    """잠금 안쪽에 실제 두 번째 완료 판정이 있는지 구조적으로 확인한다."""
    mod = load_module(RUNNER)
    source = inspect.getsource(mod.main)
    lock_pos = source.find("with output_lock(output_dir):")
    recheck_pos = source.find("pending = [", lock_pos)
    check(lock_pos >= 0, "main 에 output lock 이 없다")
    check(recheck_pos > lock_pos, "잠금 획득 뒤 pending 재판정이 없다")
    check(
        "is_complete(output_dir / job.output_name" in source[recheck_pos : recheck_pos + 400],
        "잠금 안쪽 재판정이 정식 완료 기준을 쓰지 않는다",
    )


@regression(
    item="9",
    prevents="staging 이 다른 실행의 것과 섞여, 두 실행이 같은 입력 파일 집합을 "
    "서로 다르게 보는 버그. staging 은 실행마다 고유 이름이어야 한다.",
)
def test_each_run_uses_a_unique_staging_dir():
    mod = load_module(RUNNER)
    workspace = Workspace()
    try:
        job = mod.Job(
            json_file=workspace.write_json("a.json", workspace.monomer("vhh_a")),
            output_name="vhh_a",
            raw_name="vhh_a",
            sidecars=(),
        )
        first = mod.stage_jobs([job], workspace.root, workspace.output_dir)
        second = mod.stage_jobs([job], workspace.root, workspace.output_dir)
        check(first != second, "두 실행이 같은 staging 폴더 이름을 썼다")
        for path in (first, second):
            marker = json.loads(
                (path / mod.STAGE_MARKER_NAME).read_text(encoding="utf-8")
            )
            check_equal(marker["pid"], os.getpid(), "소유 표식의 PID 가 틀렸다")
            check_equal(
                marker["output_dir"],
                str(workspace.output_dir.resolve()),
                "소유 표식의 출력 폴더가 틀렸다",
            )
            check(
                (path / "a.json").is_file(), "staging 에 입력 JSON 이 복사되지 않았다"
            )
    finally:
        workspace.cleanup()


@regression(
    item="7",
    prevents="상대경로 sidecar(mmcifPath 등)를 staging 에 함께 옮기지 않아, 컨테이너 안에서 "
    "파일을 찾지 못하는 버그. AF3 는 sidecar 상대경로를 JSON 파일 위치 기준으로 해석한다.",
)
def test_relative_sidecar_files_are_staged():
    mod = load_module(RUNNER)
    workspace = Workspace()
    try:
        templates = workspace.input_dir / "templates"
        templates.mkdir()
        (templates / "ref.cif").write_text("data_ref\n", encoding="utf-8")
        obj = workspace.monomer("vhh_a")
        obj["sequences"][0]["protein"]["templates"] = [
            {"mmcifPath": "templates/ref.cif", "queryIndices": [0], "templateIndices": [0]}
        ]
        json_path = workspace.write_json("a.json", obj)

        job, error = mod.read_job(json_path, workspace.input_dir)
        check_equal(error, None, f"정상 sidecar 입력을 거부했다: {error}")
        check_equal(
            [str(s.relative_path) for s in job.sidecars],
            ["templates/ref.cif"],
            "sidecar 를 인식하지 못했다",
        )
        stage = mod.stage_jobs([job], workspace.root, workspace.output_dir)
        check(
            (stage / "templates" / "ref.cif").is_file(),
            "sidecar 파일을 staging 에 옮기지 않았다",
            f"staging 내용={sorted(str(p.relative_to(stage)) for p in stage.rglob('*'))}",
        )

        # 입력 폴더 밖을 가리키는 sidecar 는 거부해야 한다 (컨테이너에서 안 보인다).
        outside = workspace.root / "outside.cif"
        outside.write_text("data_x\n", encoding="utf-8")
        obj2 = workspace.monomer("vhh_b")
        obj2["sequences"][0]["protein"]["templates"] = [
            {"mmcifPath": "../outside.cif", "queryIndices": [0], "templateIndices": [0]}
        ]
        json_path2 = workspace.write_json("b.json", obj2)
        _job2, error2 = mod.read_job(json_path2, workspace.input_dir)
        check(
            error2 is not None and "입력 폴더 밖" in error2,
            "입력 폴더 밖 sidecar 를 거부하지 않았다",
            f"결과={error2!r}",
        )
    finally:
        workspace.cleanup()
