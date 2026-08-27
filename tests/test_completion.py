#!/usr/bin/env python3
"""완료 판정과 결과 폴더 이름에 대한 회귀 테스트 (과제 항목 1, 2, 6).

여기서 검증하는 것은 "무엇을 끝난 것으로 볼지" 하나뿐이다. 이 판정이 틀리면
2000건 배치에서 실패한 건이 조용히 완료로 집계되고, 아무도 눈치채지 못한다.
"""

from __future__ import annotations

from harness import (
    Workspace,
    check,
    check_equal,
    check_in,
    check_not_in,
    default_args,
    load_module,
    regression,
    run_script,
)

RUNNER = "run_af3_batch_improved.py"


@regression(
    item="1",
    prevents="_data.json 만 있는 폴더를 완료로 판정해, 추론 중 끊긴 건을 성공으로 집계하는 버그. "
    "AF3 는 write_fold_input_json 을 추론 전에 호출하므로 _data.json 은 '시작했다' 는 증거일 뿐이다.",
)
def test_data_json_only_is_not_complete():
    mod = load_module(RUNNER)
    workspace = Workspace()
    try:
        # 추론 전에 끊긴 상태: _data.json 만 있다.
        result = workspace.make_result("vhh_a", stage="data")
        check(
            not mod.is_complete(result, "vhh_a", "full"),
            "full 모드에서 _data.json 만 있는 폴더를 완료로 판정했다",
            f"폴더 내용={sorted(p.name for p in result.iterdir())}",
        )
        # 단계별 기준 구분: --mode data 에서는 _data.json 만으로 완료가 맞다.
        check(
            mod.is_complete(result, "vhh_a", "data"),
            "data 모드에서 _data.json 이 있는데도 미완료로 판정했다",
        )

        # 일부만 있는 상태(ranking_scores.csv 만)도 완료가 아니다.
        partial = workspace.make_result("vhh_b", stage="partial")
        check(
            not mod.is_complete(partial, "vhh_b", "full"),
            "3종 중 일부만 있는 폴더를 완료로 판정했다",
            f"폴더 내용={sorted(p.name for p in partial.iterdir())}",
        )

        # 3종이 있어도 크기가 0 이면 완료가 아니다 (디스크가 꽉 찬 상황).
        zero = workspace.make_result("vhh_c", stage="zero")
        sizes = {p.name: p.stat().st_size for p in zero.iterdir() if p.is_file()}
        check(
            not mod.is_complete(zero, "vhh_c", "full"),
            "크기 0 인 산출물을 완료로 판정했다",
            f"크기={sizes}",
        )

        # 3종이 모두 있고 크기가 0 보다 크면 완료다.
        full = workspace.make_result("vhh_d", stage="full")
        check(
            mod.is_complete(full, "vhh_d", "full"),
            "정상 완료 폴더를 미완료로 판정했다",
            f"폴더 내용={sorted(p.name for p in full.iterdir())}",
        )
    finally:
        workspace.cleanup()


@regression(
    item="1",
    prevents="압축 출력(--compress_large_output_files)을 쓰면 _model.cif 대신 _model.cif.zst 가 "
    "생기는데, .cif 만 찾아 정상 완료를 미완료로 오인하고 다시 돌리는 버그.",
)
def test_compressed_cif_counts_as_complete():
    mod = load_module(RUNNER)
    workspace = Workspace()
    try:
        result = workspace.make_result("vhh_z", stage="full")
        (result / "vhh_z_model.cif").unlink()
        (result / "vhh_z_model.cif.zst").write_text("zstd\n", encoding="utf-8")
        check(
            mod.is_complete(result, "vhh_z", "full"),
            "압축된 _model.cif.zst 를 인정하지 않아 완료를 미완료로 판정했다",
            f"폴더 내용={sorted(p.name for p in result.iterdir())}",
        )
    finally:
        workspace.cleanup()


@regression(
    item="1",
    prevents="끊긴 결과를 그대로 두고 재실행해, AF3 가 <이름>_<타임스탬프> 폴더를 새로 만들어 "
    "같은 타깃의 결과가 여러 폴더로 흩어지는 버그. 재실행 전에 격리해야 한다.",
)
def test_incomplete_result_is_quarantined_before_rerun():
    workspace = Workspace()
    try:
        workspace.write_json("a.json", workspace.monomer("vhh_a"))
        # 앞선 실행이 추론 도중 끊긴 상태를 만든다.
        workspace.make_result("vhh_a", stage="data")

        proc = run_script(RUNNER, default_args(workspace, "--yes"), workspace)
        check_equal(proc.returncode, 0, f"재실행이 실패했다\n표준출력:\n{proc.stdout[-1500:]}")

        quarantine = workspace.output_dir / ".af3_incomplete" / "vhh_a"
        check(
            quarantine.is_dir(),
            "미완료 결과를 .af3_incomplete 로 격리하지 않았다",
            f"출력 폴더={sorted(p.name for p in workspace.output_dir.iterdir())}",
        )
        snapshots = sorted(p for p in quarantine.iterdir() if p.is_dir())
        check_equal(len(snapshots), 1, "격리 보존본이 1개가 아니다")

        # 재실행 결과는 타임스탬프 접미사가 아니라 원래 이름 폴더에 들어와야 한다.
        stamped = [
            p.name
            for p in workspace.output_dir.iterdir()
            if p.is_dir() and p.name.startswith("vhh_a_")
        ]
        check_equal(stamped, [], "격리가 늦어 AF3 가 타임스탬프 폴더를 새로 만들었다")
        check(
            (workspace.output_dir / "vhh_a" / "vhh_a_ranking_scores.csv").is_file(),
            "재실행 후에도 최종 산출물이 없다",
        )
    finally:
        workspace.cleanup()


@regression(
    item="2",
    prevents="결과 폴더를 JSON 파일명으로 찾는 버그. AF3 는 파일명이 아니라 JSON name 필드를 "
    "정규화해 폴더를 만든다(run_alphafold.py:1075). 파일명으로 찾으면 이미 끝난 건을 "
    "매번 다시 돌린다.",
)
def test_output_dir_follows_json_name_not_filename():
    mod = load_module(RUNNER)
    workspace = Workspace()
    try:
        # 정규화 규칙 자체 확인 (folding_input.py:1054-1058)
        check_equal(mod.sanitised_name("VHH 01"), "VHH_01", "공백을 밑줄로 바꾸지 않았다")
        check_equal(mod.sanitised_name("A/B"), "AB", "허용되지 않는 문자를 지우지 않았다")
        check_equal(mod.sanitised_name("vhh.01-a_b"), "vhh.01-a_b", "허용 문자를 지웠다")
        check_equal(mod.sanitised_name("나노바디"), "", "한글이 남았다 (AF3 는 지운다)")

        # 파일명과 name 이 다른 입력. 결과는 name 기준 폴더에 이미 완료돼 있다.
        workspace.write_json("zzz_filename.json", workspace.monomer("aaa realname"))
        workspace.make_result("aaa_realname", stage="full")

        proc = run_script(
            RUNNER,
            default_args(workspace, "--audit", "--trust-unverified-results"),
            workspace,
        )
        check_in(
            "완료 1개, 미완료 0개",
            proc.stdout,
            "파일명으로 결과를 찾아 이미 끝난 건을 미완료로 봤다",
        )
        check_equal(proc.returncode, 0, "미완료가 없는데 --audit 종료코드가 0 이 아니다")
    finally:
        workspace.cleanup()


@regression(
    item="2",
    prevents="위와 같은 버그를 실행 경로에서 검증. 스텁이 실제 AF3 처럼 name 기준 폴더를 "
    "만드는지, 러너가 그 폴더로 완료를 판정하는지 함께 본다.",
)
def test_runner_and_af3_agree_on_output_folder_name():
    workspace = Workspace()
    try:
        workspace.write_json("job_01.json", workspace.monomer("VHH panel 01"))
        proc = run_script(RUNNER, default_args(workspace, "--yes"), workspace)
        check_equal(proc.returncode, 0, f"실행 실패\n{proc.stdout[-1500:]}")
        made = sorted(
            p.name for p in workspace.output_dir.iterdir() if p.is_dir()
        )
        check_equal(
            made,
            ["VHH_panel_01"],
            "AF3 가 만드는 폴더 이름(name 정규화)과 다른 폴더가 생겼다",
        )
        # 두 번째 실행은 아무것도 하지 않아야 한다.
        proc2 = run_script(RUNNER, default_args(workspace, "--yes"), workspace)
        check_equal(proc2.returncode, 0, "완료 상태 재실행이 실패했다")
        check_in("모두 이미 끝나 있습니다", proc2.stdout, "완료된 건을 다시 돌렸다")
    finally:
        workspace.cleanup()


@regression(
    item="6",
    prevents="미완료가 남았는데 종료코드 0 을 돌려주는 버그. cron/스크립트 자동화가 실패를 "
    "성공으로 오인해, 결과가 빠진 채로 다음 단계가 진행된다.",
)
def test_exit_code_nonzero_when_jobs_remain():
    workspace = Workspace()
    try:
        for index in range(1, 4):
            workspace.write_json(
                f"j{index}.json", workspace.monomer(f"vhh_{index:03d}")
            )
        # vhh_002 는 파일별 재시도에서도 계속 실패한다 (일시적 실패가 아닌 경우).
        # AF3_STUB_FAIL_AT 은 스텁 호출마다 세므로 파일별 재시도에서 성공해버린다.
        # 종료코드를 검증하려면 이름으로 지정해 재시도까지 실패시켜야 한다.
        proc = run_script(
            RUNNER,
            default_args(workspace, "--yes"),
            workspace,
            env_extra={"AF3_STUB_FAIL_NAMES": "vhh_002"},
        )
        check(
            proc.returncode != 0,
            "미완료가 남았는데 종료코드가 0 이다",
            f"종료코드={proc.returncode}\n{proc.stdout[-1500:]}",
        )
        check_in("[미완료]", proc.stdout, "미완료 목록을 알려주지 않았다")

        # --audit 도 미완료가 있으면 0 이 아니어야 한다.
        audit = run_script(RUNNER, default_args(workspace, "--audit"), workspace)
        check(
            audit.returncode != 0,
            "--audit 이 미완료를 보고했는데 종료코드가 0 이다",
            f"종료코드={audit.returncode}",
        )
    finally:
        workspace.cleanup()


@regression(
    item="6",
    prevents="한 건 실패가 나머지 전체를 막는 버그. --input_dir 순회가 끊긴 뒤 남은 건을 "
    "파일별로 재시도하지 않으면, 2000건 중 3번째에서 멈춰 1997건이 그대로 남는다.",
)
def test_single_failure_does_not_block_the_rest():
    workspace = Workspace()
    try:
        for index in range(1, 5):
            workspace.write_json(
                f"j{index}.json", workspace.monomer(f"vhh_{index:03d}")
            )
        # vhh_002 만 계속 실패한다. 나머지 3건은 끝나야 한다.
        proc = run_script(
            RUNNER,
            default_args(workspace, "--yes"),
            workspace,
            env_extra={"AF3_STUB_FAIL_NAMES": "vhh_002"},
        )
        done = sorted(
            p.name
            for p in workspace.output_dir.iterdir()
            if p.is_dir()
            and (p / f"{p.name}_ranking_scores.csv").is_file()
            and (p / f"{p.name}_ranking_scores.csv").stat().st_size > 0
        )
        check_equal(
            done,
            ["vhh_001", "vhh_003", "vhh_004"],
            f"실패 1건이 나머지를 막았다\n표준출력:\n{proc.stdout[-2500:]}",
        )
        check(proc.returncode != 0, "1건이 미완료인데 종료코드가 0 이다")
        check_in("파일별로 재시도", proc.stdout, "파일별 재시도로 전환한다는 안내가 없다")
    finally:
        workspace.cleanup()


@regression(
    item="6",
    prevents="비대화형 환경에서 확인 질문을 기다리다 멈추거나, 반대로 확인 없이 GPU 를 "
    "돌려버리는 버그. --yes 없이 자동화하면 실행하지 않고 2 로 끝나야 한다.",
)
def test_noninteractive_run_without_yes_does_not_execute():
    workspace = Workspace()
    try:
        workspace.write_json("a.json", workspace.monomer("vhh_a"))
        proc = run_script(RUNNER, default_args(workspace), workspace)
        check_equal(proc.returncode, 2, f"확인 불가 상태의 종료코드가 2 가 아니다\n{proc.stdout[-1200:]}")
        check_not_in(
            "seed-1_sample-0",
            "\n".join(sorted(str(p) for p in workspace.output_dir.rglob("*"))),
            "확인 없이 AF3 를 실행해 결과를 만들었다",
        )
        check_in("--yes", proc.stdout, "--yes 를 쓰라는 안내가 없다")
    finally:
        workspace.cleanup()
