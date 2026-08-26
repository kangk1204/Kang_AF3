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
import tempfile
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
    # 어떤 함수로 판정하는지는 구현 사정이다. 지켜야 하는 것은 잠금 안쪽에서
    # 결과 폴더를 다시 보고 결정한다는 것, 그리고 바깥과 같은 기준을 쓴다는 것이다.
    window = source[recheck_pos : recheck_pos + 400]
    check(
        "needs_run(" in window or "is_complete(" in window,
        "잠금 안쪽 재판정이 완료 여부를 다시 보지 않는다",
        window[:200],
    )
    outer_pos = source.find("pending = []")
    if outer_pos < 0:
        outer_pos = source.find("pending = [")
    outer = source[outer_pos : outer_pos + 400]
    check(
        ("needs_run(" in outer) == ("needs_run(" in window),
        "잠금 바깥과 안쪽이 서로 다른 완료 기준을 쓴다",
        f"바깥={outer[:120]!r}\n안쪽={window[:120]!r}",
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


@regression(
    item="8",
    prevents=(
        "배치 경로에서 격리 실패가 무방비 OSError 로 올라와, 결과 폴더 하나가 이상하면 "
        "나머지 전부가 시작조차 못 하는 버그. 파일별 경로(run_one_by_one)는 건별로 "
        "경고만 하고 넘어가는데 배치 경로만 전체를 세웠다."
    ),
)
def test_batch_run_survives_one_unquarantinable_result():
    workspace = Workspace()
    try:
        workspace.write_json("a.json", workspace.monomer("vhh_a"))
        workspace.write_json("b.json", workspace.monomer("vhh_b"))
        # 두 건 모두 '미완료 결과' 를 남긴 상태로 다시 도는 상황.
        for name in ("vhh_a", "vhh_b"):
            workspace.make_result(name, stage="partial")
        # vhh_a 만 격리가 불가능하다: 작업별 격리 경로가 폴더가 아니라 일반 파일이다.
        quarantine_root = workspace.output_dir / ".af3_incomplete"
        quarantine_root.mkdir(parents=True, exist_ok=True)
        (quarantine_root / "vhh_a").write_text("not a directory", encoding="utf-8")

        proc = run_script(
            "run_af3_batch_improved.py",
            default_args(workspace, "--yes"),
            workspace,
        )
        report = proc.stdout + proc.stderr
        check(
            "Traceback" not in report,
            "격리 실패가 트레이스백으로 새어 나왔다",
            report[-1500:],
        )
        check_in("vhh_a", report, "격리하지 못한 건을 알리지 않았다")
        # 'help' 는 플래그 탐지용 호출이라 격리 이전에 이미 일어난다. 실제 계산이
        # 시작됐는지 보려면 run 호출을 봐야 한다.
        runs = [call for call in workspace.stub_calls() if call.get("call") == "run"]
        check(runs, "격리 실패 한 건 때문에 배치 전체가 시작도 못 했다")
        check(
            workspace.result_dir("vhh_b").joinpath("vhh_b_model.cif").is_file(),
            "격리 가능한 다른 건이 실행되지 않았다",
        )
        check(proc.returncode != 0, "격리 실패를 성공으로 보고했다", report[-1500:])
        check(
            (quarantine_root / "vhh_a").is_file(),
            "격리할 수 없다고 판단한 경로를 건드렸다",
        )
    finally:
        workspace.cleanup()


@regression(
    item="9",
    prevents=(
        "러너가 죽어도 `docker run` 컨테이너는 데몬이 계속 돌려서 GPU/CPU 를 먹는데, "
        "러너가 컨테이너에 이름을 붙이지 않아 --audit/--cleanup 이 그 고아를 찾지도 "
        "정리하지도 못하는 버그. Ctrl-C, SIGTERM, SSH 끊김 모두에서 실측 확인했다."
    ),
)
def test_runner_names_containers_and_reports_orphans():
    runner = load_module(RUNNER)

    # 1) 컨테이너에 이 실행을 식별할 이름이 붙어야 나중에 찾을 수 있다.
    command = runner.docker_base(
        docker_command=("docker",),
        image="alphafold3",
        mode="full",
        input_mount=Path("/tmp/in"),
        output_dir=Path("/tmp/out"),
        db_dirs=[Path("/tmp/db")],
        model_dir=Path("/tmp/model"),
        cache_dir=Path("/tmp/cache"),
        use_cache=False,
        container="af3run_4242_1",
    )
    check_in("--name", command, "컨테이너에 이름을 붙이지 않는다")
    check_in("af3run_4242_1", command, "컨테이너 이름이 명령에 들어가지 않았다")
    check(
        runner.container_name(1).startswith(runner.CONTAINER_PREFIX),
        "컨테이너 이름이 약속된 접두사로 시작하지 않는다",
    )
    check_in(str(os.getpid()), runner.container_name(1), "컨테이너 이름에 소유 PID가 없다")

    # 2) 죽은 PID 의 컨테이너만 고아로 판정해야 한다 (남의 것을 지우면 안 된다).
    dead_pid = 999_999_999
    check(not runner.process_is_alive(dead_pid), "테스트 전제: 이 PID는 죽어 있어야 한다")
    with tempfile.TemporaryDirectory(prefix="af3_orphan_") as td:
        registry = Path(td) / "containers"
        registry.write_text(
            f"{runner.CONTAINER_PREFIX}{dead_pid}_1\n"
            f"{runner.CONTAINER_PREFIX}{os.getpid()}_1\n"
            "someone_elses_container\n",
            encoding="utf-8",
        )
        workspace = Workspace()
        try:
            bin_dir = make_stub_bin(workspace.root)
            env = {
                "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
                "AF3_STUB_CONTAINERS": str(registry),
            }
            # 이 호출은 같은 프로세스 안에서 일어나므로 스텁 PATH 도 직접 걸어준다.
            saved_path = os.environ.get("PATH", "")
            os.environ["AF3_STUB_CONTAINERS"] = str(registry)
            os.environ["PATH"] = env["PATH"]
            try:
                orphans = runner.orphan_containers(("docker",))
            finally:
                os.environ.pop("AF3_STUB_CONTAINERS", None)
                os.environ["PATH"] = saved_path
            check_equal(
                orphans,
                [f"{runner.CONTAINER_PREFIX}{dead_pid}_1"],
                "고아 판정이 틀렸다 (살아 있는 실행이나 남의 컨테이너를 건드린다)",
            )

            # 3) --audit 이 고아를 사용자에게 알려야 한다.
            workspace.write_json("a.json", workspace.monomer("vhh_a"))
            audit = run_script(
                RUNNER,
                default_args(workspace, "--audit"),
                workspace,
                env_extra=env,
            )
            report = audit.stdout + audit.stderr
            check_in("남아 있는 컨테이너", report, "--audit 이 고아 컨테이너를 보고하지 않는다")
            check_in(
                f"{runner.CONTAINER_PREFIX}{dead_pid}_1",
                report,
                "--audit 이 고아 컨테이너 이름을 알려주지 않는다",
            )
            check_equal(
                registry.read_text(encoding="utf-8").count("\n"),
                3,
                "--audit 이 컨테이너를 건드렸다 (점검만 해야 한다)",
            )

            # 4) --cleanup 이 고아만 제거해야 한다.
            cleanup = run_script(
                RUNNER,
                default_args(workspace, "--cleanup", "--yes"),
                workspace,
                env_extra=env,
            )
            left = registry.read_text(encoding="utf-8").splitlines()
            check(
                f"{runner.CONTAINER_PREFIX}{dead_pid}_1" not in left,
                "--cleanup 이 고아 컨테이너를 정리하지 않았다",
                cleanup.stdout[-1200:],
            )
            check_in(
                "someone_elses_container",
                "\n".join(left),
                "--cleanup 이 관리 대상이 아닌 컨테이너를 지웠다",
            )
            check_in(
                f"{runner.CONTAINER_PREFIX}{os.getpid()}_1",
                "\n".join(left),
                "--cleanup 이 살아 있는 실행의 컨테이너를 지웠다",
            )

            # 5) 정상 종료든 중단이든 run_docker 는 자기 컨테이너를 남기지 않는다.
            #    (--rm 은 컨테이너가 스스로 끝났을 때만 지운다. 러너가 죽으면 남는다.)
            saved_path = os.environ.get("PATH", "")
            os.environ["PATH"] = env["PATH"]
            os.environ["AF3_STUB_CONTAINERS"] = str(registry)
            os.environ["AF3_STUB_LOG"] = str(workspace.stub_log)
            try:
                runner.run_docker(
                    ["docker", "run", "--rm", "--name", "af3run_1_9", "alphafold3"],
                    ("docker",),
                    "af3run_1_9",
                )
            finally:
                os.environ["PATH"] = saved_path
                os.environ.pop("AF3_STUB_CONTAINERS", None)
                os.environ.pop("AF3_STUB_LOG", None)
            removals = [
                call for call in workspace.stub_calls() if call.get("call") == "rm"
            ]
            check(
                any("af3run_1_9" in call.get("targets", []) for call in removals),
                "run_docker 가 끝난 뒤 자기 컨테이너를 지우지 않는다",
            )
        finally:
            workspace.cleanup()


@regression(
    item="beginner",
    prevents="예전 러너(root 컨테이너)가 만든 JAX 캐시가 남아 있으면, --user 로 바뀐 뒤\n"
             "컨테이너가 그 안에 못 써서 PERMISSION_DENIED 가 쏟아지는 버그.\n"
             "사용자는 원인을 알 수 없고, 로그만 수천 줄 늘어난다.",
)
def test_unwritable_jax_cache_is_detected_and_explained():
    mod = load_module("run_af3_batch_improved.py")
    check(hasattr(mod, "cache_dir_problem"), "캐시 쓰기 가능 여부를 점검하지 않는다")
    with tempfile.TemporaryDirectory(prefix="af3_cache_perm_") as td:
        root = Path(td)
        good = root / "ok"
        good.mkdir()
        check_equal(mod.cache_dir_problem(good), None, "쓸 수 있는 캐시를 문제로 봤다")

        # 예전 root 컨테이너가 남긴 하위 폴더를 흉내낸다: 상위는 쓸 수 있고
        # 하위만 못 쓴다. 상위만 검사하면 놓치는 바로 그 형태다.
        bad = root / "legacy"
        sub = bad / "xla_gpu_per_fusion_autotune_cache_dir"
        sub.mkdir(parents=True)
        sub.chmod(0o555)
        try:
            problem = mod.cache_dir_problem(bad)
            check(problem, "하위 폴더가 읽기전용인데 문제로 보지 않았다")
            check_in(str(sub), problem, "어느 폴더가 문제인지 알려주지 않는다")
            check_in("chown", problem, "고치는 방법을 알려주지 않는다")
        finally:
            sub.chmod(0o755)


@regression(
    item="beginner",
    prevents="improved 러너만 캐시 권한을 점검하고 legacy(af3run.sh 경로)는 그대로 두어,\n"
             "같은 상황에서 legacy 만 PERMISSION_DENIED 를 쏟는 버그.",
)
def test_legacy_runner_also_detects_an_unwritable_cache():
    mod = load_module("af3_batch.py")
    check(hasattr(mod, "cache_dir_problem"), "legacy 러너가 캐시 권한을 점검하지 않는다")
    with tempfile.TemporaryDirectory(prefix="af3_cache_legacy_") as td:
        root = Path(td)
        ok = root / "ok"
        ok.mkdir()
        check_equal(mod.cache_dir_problem(str(ok)), None, "쓸 수 있는 캐시를 문제로 봤다")
        bad = root / "legacy"
        sub = bad / "xla_gpu_per_fusion_autotune_cache_dir"
        sub.mkdir(parents=True)
        sub.chmod(0o555)
        try:
            problem = mod.cache_dir_problem(str(bad))
            check(problem, "하위 폴더가 읽기전용인데 문제로 보지 않았다")
            check_in("chown", problem, "고치는 방법을 알려주지 않는다")
        finally:
            sub.chmod(0o755)


@regression(
    item="beginner",
    prevents="이미지 확인 프로브가 이름 없이 컨테이너를 만들어, 시간초과로 죽으면\n"
             "Created 상태로 남고 --audit/--cleanup 이 찾지도 지우지도 못하는 버그.",
)
def test_image_probe_container_is_named_so_cleanup_can_find_it():
    mod = load_module("run_af3_batch_improved.py")
    import inspect
    src = inspect.getsource(mod.probe_flags)
    check_in("--name", src, "이미지 확인 프로브가 컨테이너에 이름을 붙이지 않는다")
    check_in("probe_container_name", src, "프로브 이름을 정해진 규칙으로 만들지 않는다")
    name = mod.probe_container_name()
    check(
        mod.CONTAINER_NAME_RE.match(name),
        "프로브 이름이 정리 대상 규칙에 걸리지 않는다",
        f"이름={name} 규칙={mod.CONTAINER_NAME_RE.pattern}",
    )


@regression(
    item="beginner",
    prevents="GPU 를 이미 다른 AF3 가 선점했는데도 컨테이너를 띄워,\n"
             "CUDA_ERROR_OUT_OF_MEMORY 와 JAX 역추적만 남기고 죽는 버그.\n"
             "사용자는 자기 입력이 잘못된 줄 안다.",
)
def test_busy_gpu_is_refused_before_starting_a_container():
    mod = load_module("run_af3_batch_improved.py")
    check(hasattr(mod, "gpu_busy_reason"), "GPU 선점 여부를 미리 보지 않는다")

    # 다른 실행이 이미 GPU 를 잡고 있다.
    reason = mod.gpu_busy_reason(others=["af3run_4242_0"], free_mib=300)
    check(reason, "다른 AF3 가 돌고 있는데 막지 않았다")
    check_in("af3run_4242_0", reason, "어떤 실행이 GPU 를 쓰는지 알려주지 않는다")

    # 우리 것만 있으면 막지 않는다 (재실행/이어하기).
    check_equal(mod.gpu_busy_reason(others=[], free_mib=11000), None,
                "GPU 가 비어 있는데 막았다")

    # 남이 아니라 메모리만 부족한 경우도 잡는다.
    only_memory = mod.gpu_busy_reason(others=[], free_mib=300)
    check(only_memory, "여유 메모리가 없는데 막지 않았다")
    check_in("MiB", only_memory, "얼마나 남았는지 숫자로 말하지 않는다")

    # 메모리를 읽을 수 없는 환경(GPU 없음 등)에서는 막지 않는다.
    check_equal(mod.gpu_busy_reason(others=[], free_mib=None), None,
                "GPU 정보를 못 읽었다고 실행을 막았다")


@regression(
    item="provenance",
    prevents="같은 이름이면 서열이 바뀌어도 옛 결과를 완료로 보고 건너뛰는 버그.\n"
             "서열을 고쳐 다시 돌린 사용자가 옛 구조를 새 결과로 보고한다.\n"
             "과학 데이터 무결성 문제다.",
)
def test_changed_input_is_not_mistaken_for_a_finished_result():
    workspace = Workspace()
    try:
        # 1회차: 어떤 서열로 끝까지 돌린다.
        workspace.write_json("a.json", workspace.monomer("vhh_a"))
        first = run_script(RUNNER, default_args(workspace, "--yes"), workspace)
        check_equal(first.returncode, 0, f"1회차 실행이 실패했다\n{first.stdout[-800:]}")
        runs_before = len([c for c in workspace.stub_calls() if c.get("call") == "run"])
        check(runs_before, "1회차에 docker 를 부르지 않았다")

        # 2회차: 같은 이름, 다른 서열. 옛 결과를 재사용하면 안 된다.
        changed = workspace.monomer("vhh_a")
        changed["sequences"][0]["protein"]["sequence"] += "WWWW"
        workspace.write_json("a.json", changed)
        workspace.stub_log.write_text("", encoding="utf-8")
        second = run_script(RUNNER, default_args(workspace, "--yes"), workspace)
        runs_after = [c for c in workspace.stub_calls() if c.get("call") == "run"]
        check(
            runs_after,
            "서열이 바뀌었는데 다시 계산하지 않고 옛 결과를 완료로 처리했다",
            second.stdout[-900:],
        )
        check_in(
            "입력이 바뀌었",
            second.stdout,
            "무엇이 달라져서 다시 도는지 알려주지 않는다",
        )
        check_equal(second.returncode, 0, f"2회차 실행이 실패했다\n{second.stdout[-800:]}")
    finally:
        workspace.cleanup()


@regression(
    item="provenance",
    prevents="입력이 그대로인데도 매번 다시 계산해 2000건 배치가 끝나지 않는 버그.\n"
             "provenance 검사를 넣다가 반대로 과하게 만드는 것을 막는다.",
)
def test_unchanged_input_is_still_skipped():
    workspace = Workspace()
    try:
        workspace.write_json("a.json", workspace.monomer("vhh_a"))
        first = run_script(RUNNER, default_args(workspace, "--yes"), workspace)
        check_equal(first.returncode, 0, "1회차 실행이 실패했다")

        workspace.stub_log.write_text("", encoding="utf-8")
        second = run_script(RUNNER, default_args(workspace, "--yes"), workspace)
        check_equal(second.returncode, 0, "2회차 실행이 실패했다")
        check_equal(
            [c for c in workspace.stub_calls() if c.get("call") == "run"],
            [],
            "입력이 그대로인데 다시 계산했다",
        )
    finally:
        workspace.cleanup()


@regression(
    item="provenance",
    prevents="legacy MSA 보관소가 '기존 파일이 더 크면 새 것을 버린다'로 동작해,\n"
             "서열이나 DB 가 바뀐 뒤에도 옛 MSA·템플릿이 추론에 쓰이는 버그.",
)
def test_legacy_msa_store_does_not_keep_a_stale_result():
    mod = load_module("af3_batch.py")
    with tempfile.TemporaryDirectory(prefix="af3_msa_store_") as td:
        root = Path(td)
        raw = root / "raw"
        store = root / "store"
        (raw / "vhh_a").mkdir(parents=True)
        store.mkdir()

        # 보관소에 옛 결과가 이미 있고, 새 결과가 더 작다 (서열이 짧아졌거나 MSA 가 얕다).
        stale = store / "vhh_a_data.json"
        stale.write_text(json.dumps({"msa": "old" * 100}), encoding="utf-8")
        fresh = raw / "vhh_a" / "vhh_a_data.json"
        fresh.write_text(json.dumps({"msa": "new"}), encoding="utf-8")

        mod.collect_msa_outputs(raw, store)
        kept = json.loads(stale.read_text(encoding="utf-8"))
        check_equal(
            kept.get("msa"),
            "new",
            "새로 계산한 MSA 를 버리고 옛 결과를 남겼다 (크기 비교로 판단했다)",
        )


@regression(
    item="9",
    prevents="legacy 러너가 공유 대상인 output-dir 이 아니라 work-dir 을 잠가,\n"
             "서로 다른 work 를 쓰는 두 실행이 같은 결과 트리에 동시에 쓰는 버그.",
)
def test_legacy_lock_protects_the_output_directory():
    mod = load_module("af3_batch.py")
    import inspect

    source = inspect.getsource(mod.main)
    pos = source.find("lock_path")
    check(pos >= 0, "legacy 러너에 잠금이 없다")
    window = source[pos : pos + 200]
    check(
        "output_dir" in window,
        "잠금이 공유 대상인 output-dir 을 보호하지 않는다",
        window[:160],
    )


@regression(
    item="provenance",
    prevents="stage2 가 정확한 이름의 _data.json 이 없으면 폴더 안 첫 파일을 그냥 골라,\n"
             "다른 타깃의 MSA 가 이 타깃의 추론에 붙는 버그.",
)
def test_stage2_does_not_borrow_another_targets_msa():
    mod = load_module("af3_stage2.py")
    with tempfile.TemporaryDirectory(prefix="af3_stage2_pick_") as td:
        out_root = Path(td)
        tdir = out_root / "vhh_a"
        tdir.mkdir()
        # 이 폴더에는 이 타깃의 _data.json 이 없고, 다른 타깃 것만 들어 있다.
        (tdir / "vhh_zzz_data.json").write_text('{"msa": "other target"}', encoding="utf-8")

        picked = mod.find_data_json("vhh_a", out_root, None)
        check(
            picked is None,
            "다른 타깃의 _data.json 을 이 타깃 것으로 골랐다",
            f"고른 파일={picked}",
        )

        # 이름이 맞으면 당연히 골라야 한다.
        good = tdir / "vhh_a_data.json"
        good.write_text('{"msa": "mine"}', encoding="utf-8")
        check_equal(
            mod.find_data_json("vhh_a", out_root, None),
            good,
            "이름이 맞는 _data.json 을 고르지 못했다",
        )


@regression(
    item="beginner",
    prevents="GPU 여유 문턱이 2000MiB 로 고정돼, 12GiB 카드에 2500MiB 만 남아도\n"
             "통과시키는 버그. AF3 는 카드의 95%를 선점하므로 그대로 죽는다.",
)
def test_gpu_floor_scales_with_card_size():
    mod = load_module("run_af3_batch_improved.py")
    check(hasattr(mod, "gpu_free_floor"), "카드 용량을 고려한 문턱이 없다")
    check_equal(mod.gpu_free_floor(12288), 6144, "12GiB 카드의 문턱이 절반이 아니다")
    check_equal(mod.gpu_free_floor(2048), 2000, "작은 카드에서 하한이 무너졌다")
    check_equal(mod.gpu_free_floor(None), 2000, "용량을 모를 때 기본 하한을 쓰지 않는다")

    # 12GiB 카드에 2500MiB 만 남은 상황은 막아야 한다.
    reason = mod.gpu_busy_reason(others=[], free_mib=2500, total_mib=12288)
    check(reason, "큰 카드에 여유가 거의 없는데 통과시켰다")
    check_in("2500", reason, "얼마나 남았는지 알려주지 않는다")

    # 넉넉하면 막지 않는다.
    check_equal(
        mod.gpu_busy_reason(others=[], free_mib=11000, total_mib=12288),
        None,
        "GPU 가 비어 있는데 막았다",
    )


@regression(
    item="beginner",
    prevents="nvidia-smi 출력을 형식 확인 없이 숫자만 긁어, 질의를 무시하는 구현에서\n"
             "free=0 으로 오인하고 멀쩡한 실행을 막는 버그.\n"
             "GPU 상태를 못 읽는 경우와 '비어 있지 않다'는 구분돼야 한다.",
)
def test_gpu_memory_reading_is_ignored_when_the_output_is_not_what_we_asked_for():
    mod = load_module("run_af3_batch_improved.py")
    import shutil as _shutil

    with tempfile.TemporaryDirectory(prefix="af3_smi_") as td:
        bin_dir = Path(td)
        fake = bin_dir / "nvidia-smi"
        # 질의를 무시하고 사람이 읽는 표를 내보내는 구현
        fake.write_text(
            "#!/bin/sh\necho '0, Stub GPU, 999.0, 16384 MiB, 0 MiB, 16384 MiB, 0 %, 9.0'\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = f"{bin_dir}{os.pathsep}{old_path}"
        try:
            free, total = mod.gpu_memory_mib()
        finally:
            os.environ["PATH"] = old_path
        check_equal((free, total), (None, None),
                    "형식이 다른 출력을 값으로 받아들였다")
        check_equal(mod.gpu_busy_reason([], free, total), None,
                    "GPU 상태를 못 읽었는데 실행을 막았다")
