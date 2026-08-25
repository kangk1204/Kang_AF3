#!/usr/bin/env python3
"""입력 검증에 대한 회귀 테스트 (과제 항목 3, 4, 5, 10).

이 모듈이 지키는 원칙은 하나다: **잘못된 입력은 GPU 를 켜기 전에 걸러낸다.**
2000건 배치에서 3번째 파일이 깨져 있으면, 사전 검증이 없으면 1997건이
조용히 처리되지 않는다. AF3 의 --input_dir 순회는 제너레이터라서
(folding_input.py:1570-1584) 잘못된 JSON 하나에서 그 자리에서 멈춘다.
"""

from __future__ import annotations

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

RUNNER = "run_af3_batch_improved.py"


@regression(
    item="3",
    prevents="'A/B' 와 'AB' 가 같은 결과 폴더를 공유해 뒤 건이 앞 건을 덮어쓰거나 "
    "AF3 가 타임스탬프 폴더로 흩뿌리는 버그. 정규화 후 이름 충돌은 실행 전에 거부해야 한다.",
)
def test_name_collision_is_rejected_before_running():
    workspace = Workspace()
    try:
        workspace.write_json("x1.json", workspace.monomer("A/B"))
        workspace.write_json("x2.json", workspace.monomer("AB"))
        proc = run_script(RUNNER, default_args(workspace, "--yes"), workspace)
        check_equal(
            proc.returncode,
            2,
            f"이름 충돌인데 종료코드가 2 가 아니다\n{proc.stdout[-1200:]}",
        )
        check_in("겹치는 입력", proc.stdout, "무엇이 겹쳤는지 알려주지 않았다")
        check_in("x1.json", proc.stdout, "충돌한 파일 이름을 알려주지 않았다")
        check_equal(
            workspace.stub_calls(), [], "거부해야 할 입력으로 docker 를 실행했다"
        )
    finally:
        workspace.cleanup()


@regression(
    item="3",
    prevents="한글만으로 된 name 이 정규화되면 빈 문자열이 되어, AF3 가 출력 폴더 자체를 "
    "출력 루트로 삼아 결과 파일이 다른 타깃 결과와 섞이는 버그.",
)
def test_hangul_only_name_is_rejected():
    mod = load_module(RUNNER)
    workspace = Workspace()
    try:
        # 정규화 규칙상 한글은 전부 지워진다 (folding_input.py:1057 allowed_chars).
        check_equal(mod.sanitised_name("나노바디_01"), "_01", "밑줄/숫자는 남아야 한다")
        check_equal(mod.sanitised_name("나노바디"), "", "한글만이면 빈 문자열이어야 한다")

        workspace.write_json("k.json", workspace.monomer("나노바디"))
        proc = run_script(RUNNER, default_args(workspace, "--yes"), workspace)
        check_equal(
            proc.returncode, 2, f"빈 이름인데 거부하지 않았다\n{proc.stdout[-1200:]}"
        )
        check_in("빈 문자열", proc.stdout, "왜 거부하는지 설명하지 않았다")
        check_equal(workspace.stub_calls(), [], "거부해야 할 입력으로 docker 를 실행했다")
    finally:
        workspace.cleanup()


@regression(
    item="3",
    prevents="name 이 '..' 이나 '.af3_incomplete' 같은 값이면 결과가 출력 폴더 밖으로 나가거나 "
    "관리 폴더를 덮어쓰는 버그.",
)
def test_dangerous_names_are_rejected():
    mod = load_module(RUNNER)
    for bad in ("..", ".", ".af3_incomplete", ".run_af3_batch.lock", ""):
        check(
            not mod.is_safe_output_name(bad),
            f"위험한 결과 이름을 허용했다: {bad!r}",
        )
    for good in ("vhh_001", "VHH-01.a", "x"):
        check(
            mod.is_safe_output_name(good),
            f"정상 이름을 거부했다: {good!r}",
        )

    workspace = Workspace()
    try:
        workspace.write_json("d.json", workspace.monomer(".."))
        proc = run_script(RUNNER, default_args(workspace, "--yes"), workspace)
        check_equal(proc.returncode, 2, "위험한 name 을 거부하지 않았다")
        check_equal(workspace.stub_calls(), [], "거부해야 할 입력으로 docker 를 실행했다")
    finally:
        workspace.cleanup()


@regression(
    item="4",
    prevents="깨진 JSON 하나가 --input_dir 순회를 멈춰 뒤 입력이 전부 처리되지 않는 버그. "
    "AF3 의 load_fold_inputs_from_dir 은 제너레이터라 ValueError 에서 그 자리에서 끊긴다.",
)
def test_broken_json_is_caught_before_running():
    workspace = Workspace()
    try:
        workspace.write_json("a_ok.json", workspace.monomer("vhh_a"))
        # 중괄호가 닫히지 않은 파일. 정렬 순서상 뒤 파일보다 앞이라 순회를 막는다.
        workspace.write_json("b_broken.json", None, raw_text='{"name": "vhh_b",')
        workspace.write_json("c_ok.json", workspace.monomer("vhh_c"))

        proc = run_script(RUNNER, default_args(workspace, "--yes"), workspace)
        check_equal(
            proc.returncode,
            2,
            f"깨진 JSON 이 있는데 종료코드가 2 가 아니다\n{proc.stdout[-1500:]}",
        )
        check_in("b_broken.json", proc.stdout, "문제 파일 이름을 알려주지 않았다")
        check_in("JSON 형식 오류", proc.stdout, "무엇이 잘못됐는지 알려주지 않았다")
        check_equal(
            workspace.stub_calls(), [], "깨진 입력이 있는 상태로 docker 를 실행했다"
        )
    finally:
        workspace.cleanup()


@regression(
    item="4",
    prevents="스텁이 실제 AF3 처럼 깨진 JSON 에서 멈추는지 확인. 이 검증이 없으면 "
    "'사전 검증이 필요하다' 는 전제 자체가 근거를 잃는다.",
)
def test_stub_reproduces_generator_stop_on_broken_json():
    workspace = Workspace()
    try:
        workspace.write_json("a_ok.json", workspace.monomer("vhh_a"))
        workspace.write_json("b_broken.json", None, raw_text='{"name": "vhh_b",')
        workspace.write_json("c_ok.json", workspace.monomer("vhh_c"))

        # 러너의 사전 검증을 우회해 스텁을 직접 부른다 (AF3 실제 동작 재현 확인).
        import subprocess
        import sys as _sys

        from harness import FAKE_DOCKER

        proc = subprocess.run(
            [
                _sys.executable,
                str(FAKE_DOCKER),
                "run",
                "--rm",
                "-v",
                f"{workspace.input_dir}:/af3/in:ro",
                "-v",
                f"{workspace.output_dir}:/af3/out",
                "alphafold3",
                "python",
                "run_alphafold.py",
                "--output_dir=/af3/out",
                "--input_dir=/af3/in",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        check(proc.returncode != 0, "깨진 JSON 인데 스텁이 0 으로 끝났다")
        made = sorted(p.name for p in workspace.output_dir.iterdir() if p.is_dir())
        # a_ok 는 처리되고, b_broken 에서 멈춰 c_ok 는 처리되지 않아야 한다.
        check_equal(
            made,
            ["vhh_a"],
            "스텁이 실제 AF3 처럼 깨진 JSON 에서 멈추지 않았다 (뒤 입력을 계속 처리했다)",
        )
    finally:
        workspace.cleanup()


@regression(
    item="4",
    prevents="AlphaFold Server 의 list 형식 JSON 을 그대로 넣었을 때, 재개형 배치가 "
    "name 을 못 찾아 엉뚱하게 동작하는 버그. 지원하지 않는다고 알려야 한다.",
)
def test_alphafoldserver_list_json_is_rejected_with_reason():
    workspace = Workspace()
    try:
        workspace.write_json("srv.json", [{"name": "vhh_a", "sequences": []}])
        proc = run_script(RUNNER, default_args(workspace, "--yes"), workspace)
        check_equal(proc.returncode, 2, "list 형식 JSON 을 거부하지 않았다")
        check_in("list 형식", proc.stdout, "왜 거부하는지 설명하지 않았다")
    finally:
        workspace.cleanup()


@regression(
    item="5",
    prevents="macOS 에서 만든 tar 를 리눅스에서 풀면 생기는 '._*.json' 사이드카가 "
    "glob('*.json') 에 잡혀 UnicodeDecodeError 로 배치 전체를 죽이는 버그.",
)
def test_macos_appledouble_sidecar_is_excluded():
    workspace = Workspace()
    try:
        workspace.write_json("a.json", workspace.monomer("vhh_a"))
        # 실제 AppleDouble 헤더 바이트. UTF-8 로 읽으면 죽는다.
        workspace.write_bytes(
            "._a.json",
            b"\x00\x05\x16\x07\x00\x02\x00\x00Mac OS X\x00\x02\x00\x00\x00\x09"
            b"\xff\xfe\xfd\xfc\xfb\xfa",
        )
        proc = run_script(RUNNER, default_args(workspace, "--yes"), workspace)
        check_equal(
            proc.returncode,
            0,
            f"사이드카 때문에 배치가 실패했다\n{proc.stdout[-1500:]}\n{proc.stderr[-800:]}",
        )
        check_in("껍데기 파일", proc.stdout, "사이드카를 건너뛴다는 안내가 없다")
        check(
            (workspace.output_dir / "vhh_a" / "vhh_a_ranking_scores.csv").is_file(),
            "정상 입력이 처리되지 않았다",
        )
        # staging 에 사이드카가 복사되지 않아야 한다 (컨테이너 안에서도 죽는다).
        for call in workspace.stub_calls():
            if call.get("call") != "run":
                continue
            for host, container, _option in call.get("mounts", []):
                if container == "/af3/in":
                    leftovers = [
                        p.name
                        for p in __import__("pathlib").Path(host).glob("._*")
                    ]
                    check_equal(
                        leftovers, [], "사이드카가 staging 폴더로 복사됐다"
                    )
    finally:
        workspace.cleanup()


@regression(
    item="5",
    prevents="사이드카를 제외하는 판정이 세 스크립트에서 서로 달라, 한쪽만 고치면 "
    "다른 쪽이 계속 죽는 버그. is_sidecar 규약을 한 자리에서 검증한다.",
)
def test_sidecar_rule_is_consistent_across_scripts():
    collect = load_module("af3_collect.py")
    visualize = load_module("af3_visualize.py")
    batch = load_module("af3_batch.py")
    for module_name, func in (
        ("af3_collect.py", collect.is_sidecar),
        ("af3_visualize.py", visualize.is_sidecar),
        ("af3_batch.py", batch.is_sidecar),
    ):
        check(func("._a.json"), f"{module_name}: AppleDouble 을 제외하지 않았다")
        check(func(".af3_incomplete"), f"{module_name}: 숨은 관리 폴더를 제외하지 않았다")
        check(not func("vhh_001"), f"{module_name}: 정상 이름을 제외했다")


@regression(
    item="10",
    prevents="--input_dir 이 없는 구버전 AF3 이미지에서 배치가 그냥 실패하는 버그. "
    "파일별 방식으로 전환하거나, 최소한 원인을 알려주고 멈춰야 한다.",
)
def test_legacy_image_without_input_dir_falls_back_to_per_file():
    workspace = Workspace()
    try:
        for index in (1, 2):
            workspace.write_json(
                f"j{index}.json", workspace.monomer(f"vhh_{index:03d}")
            )
        proc = run_script(
            RUNNER,
            default_args(workspace, "--yes", "--image", "alphafold3-legacy"),
            workspace,
        )
        check_equal(
            proc.returncode,
            0,
            f"구버전 이미지에서 배치가 실패했다\n{proc.stdout[-1500:]}",
        )
        check_in(
            "--input_dir이 없어",
            proc.stdout,
            "왜 파일별 방식으로 바뀌는지 알려주지 않았다",
        )
        runs = [c for c in workspace.stub_calls() if c.get("call") == "run"]
        check_equal(len(runs), 2, "파일별 방식이 아니라 한 번에 돌리려 했다")
        check(
            all(c["per_file"] for c in runs),
            "--json_path 가 아니라 --input_dir 로 실행했다",
            f"실제 플래그={[c['flags'] for c in runs]}",
        )
        for index in (1, 2):
            name = f"vhh_{index:03d}"
            check(
                (workspace.output_dir / name / f"{name}_ranking_scores.csv").is_file(),
                f"{name} 결과가 없다",
            )
    finally:
        workspace.cleanup()


@regression(
    item="10",
    prevents="구버전 이미지에 --jax_compilation_cache_dir 이 없는데 그 플래그를 붙여 "
    "컨테이너가 즉시 종료되는 버그. 없는 플래그는 붙이지 않아야 한다.",
)
def test_unsupported_flags_are_not_passed_to_legacy_image():
    workspace = Workspace()
    try:
        workspace.write_json("a.json", workspace.monomer("vhh_a"))
        proc = run_script(
            RUNNER,
            default_args(workspace, "--yes", "--image", "alphafold3-legacy"),
            workspace,
        )
        check_equal(proc.returncode, 0, "구버전 이미지에서 실패했다")
        runs = [c for c in workspace.stub_calls() if c.get("call") == "run"]
        check(runs, "docker 를 아예 실행하지 않았다")
        for call in runs:
            check(
                "jax_compilation_cache_dir" not in call["flags"],
                "구버전 이미지에 없는 플래그를 붙였다",
                f"붙인 플래그={call['flags']}",
            )
        check_in("JAX compilation cache", proc.stdout, "생략한다는 안내가 없다")
    finally:
        workspace.cleanup()


@regression(
    item="10",
    prevents="이미지 확인 자체가 실패했는데 최신 플래그를 추측해 실행하는 버그. "
    "확인 실패는 실패로 알려야 한다.",
)
def test_image_probe_failure_stops_with_reason():
    workspace = Workspace()
    try:
        workspace.write_json("a.json", workspace.monomer("vhh_a"))
        proc = run_script(
            RUNNER,
            default_args(workspace, "--yes", "--image", "alphafold3-broken"),
            workspace,
        )
        check(
            proc.returncode != 0,
            "이미지를 찾을 수 없는데 종료코드가 0 이다",
            f"종료코드={proc.returncode}",
        )
        check_in("플래그 확인에 실패", proc.stdout, "확인 실패 원인을 알려주지 않았다")
        # 핵심: 확인 실패 뒤에 AF3 를 실행하려 시도조차 하지 않아야 한다.
        # 'run' 뿐 아니라 'run_attempt'(이미지 없어 125 로 끝난 호출)도 함께 본다.
        # 이 구분이 없으면 '확인 실패를 무시하고 추측 실행' 버그를 잡지 못한다.
        attempts = [
            c
            for c in workspace.stub_calls()
            if c.get("call") in {"run", "run_attempt"}
        ]
        check_equal(
            attempts,
            [],
            "확인 실패 상태에서 추측하여 AF3 실행을 시도했다",
            f"스텁 호출={workspace.stub_calls()}",
        )
    finally:
        workspace.cleanup()


@regression(
    item="10",
    prevents="--mode data 인데 --norun_inference 가 없는 이미지에서, 또는 --mode inference "
    "인데 --norun_data_pipeline 이 없는 이미지에서 조용히 잘못된 단계를 돌리는 버그.",
)
def test_mode_requires_matching_flags():
    mod = load_module(RUNNER)
    modern = {
        "json_path",
        "input_dir",
        "output_dir",
        "model_dir",
        "db_dir",
        "run_data_pipeline",
        "run_inference",
        "jax_compilation_cache_dir",
    }
    check_equal(
        mod.validate_supported_flags(modern, "full"), None, "최신 이미지를 거부했다"
    )
    check_equal(
        mod.validate_supported_flags(modern, "data"), None, "최신 이미지를 거부했다"
    )
    minimal = {"json_path", "output_dir", "db_dir", "model_dir"}
    for mode, needed in (("data", "--run_inference"), ("inference", "--run_data_pipeline")):
        reason = mod.validate_supported_flags(minimal, mode)
        check(
            reason is not None and needed in reason,
            f"{mode} 모드에서 {needed} 없음을 잡지 못했다",
            f"결과={reason!r}",
        )


@regression(
    item="10",
    prevents="--mode data 에서 GPU 를 요구해, GPU 가 없는 CPU 서버에서 MSA 단계를 "
    "돌릴 수 없게 되는 버그. 2단계 분리 전략의 전제가 깨진다.",
)
def test_data_mode_does_not_request_gpu():
    workspace = Workspace()
    try:
        workspace.write_json("a.json", workspace.monomer("vhh_a"))
        proc = run_script(
            RUNNER, default_args(workspace, "--yes", "--mode", "data"), workspace
        )
        check_equal(proc.returncode, 0, f"data 모드 실행이 실패했다\n{proc.stdout[-1200:]}")
        runs = [c for c in workspace.stub_calls() if c.get("call") == "run"]
        check(runs, "docker 를 실행하지 않았다")
        for call in runs:
            check(not call["gpus"], "data 모드인데 --gpus all 을 붙였다")
            check(
                "norun_inference" in call["switches"],
                "data 모드인데 --norun_inference 를 붙이지 않았다",
                f"실제 스위치={call['switches']}",
            )
            check(
                "model_dir" not in call["flags"],
                "data 모드인데 모델 폴더를 마운트/전달했다",
            )
        # data 모드 완료 기준은 _data.json 하나다.
        check(
            (workspace.output_dir / "vhh_a" / "vhh_a_data.json").is_file(),
            "_data.json 이 만들어지지 않았다",
        )
    finally:
        workspace.cleanup()


@regression(
    item="beginner",
    prevents="컨테이너가 root 로 결과를 써서 Quick Start 를 따라한 사용자가\n"
             "rm -rf quick_out 에 실패하고, 문서는 원인을 'sudo docker' 탓으로 잘못 짚는 버그.",
)
def test_runner_writes_results_as_the_invoking_user():
    import os

    workspace = Workspace()
    try:
        workspace.write_json("a.json", workspace.monomer("vhh_a"))
        proc = run_script(RUNNER, default_args(workspace, "--yes"), workspace)
        check_equal(proc.returncode, 0, f"실행이 실패했다\n{proc.stdout[-1200:]}")
        runs = [c for c in workspace.stub_calls() if c.get("call") == "run"]
        check(runs, "docker 를 실행하지 않았다")
        expected = f"{os.getuid()}:{os.getgid()}"
        for call in runs:
            check_equal(
                call.get("user"),
                expected,
                "docker run 에 --user <uid>:<gid> 를 넘기지 않았다 (결과가 root 소유가 된다)",
            )
            # --user 값이 이미지 자리를 잡아먹지 않았는지도 본다.
            check(
                ":" not in (call.get("image") or ""),
                "--user 값이 이미지로 잘못 해석됐다",
                f"image={call.get('image')}",
            )
    finally:
        workspace.cleanup()


@regression(
    item="beginner",
    prevents="--user 는 넘기면서 마운트를 /root 아래 되돌려, 이미지의 /root(700) 를\n"
             "non-root 가 통과 못 해 'Failed to create output directory' 로 전건 실패하는 버그.",
)
def test_container_mounts_are_reachable_by_a_non_root_user():
    import os
    preferred = load_module("run_af3_batch_improved.py")
    legacy = load_module("af3_batch.py")
    for name in ("CONTAINER_INPUT", "CONTAINER_OUTPUT", "CONTAINER_MODELS", "CONTAINER_CACHE"):
        value = getattr(preferred, name)
        check(not value.startswith("/root"), f"preferred 러너 {name} 이 /root 아래다", value)
    for name in ("C_MODEL", "C_IN", "C_OUT", "C_CACHE"):
        value = getattr(legacy, name)
        check(not value.startswith("/root"), f"legacy 러너 {name} 이 /root 아래다", value)
    # DB 마운트는 인덱스로 만들어지므로 실제 조립 결과에서 본다.
    workspace = Workspace()
    try:
        workspace.write_json("a.json", workspace.monomer("vhh_a"))
        proc = run_script(RUNNER, default_args(workspace, "--yes"), workspace)
        check_equal(proc.returncode, 0, f"실행이 실패했다\n{proc.stdout[-800:]}")
        for call in [c for c in workspace.stub_calls() if c.get("call") == "run"]:
            for _host, container, _opt in call["mounts"]:
                check(not container.startswith("/root"), "docker -v 대상이 /root 아래다", container)
            check_equal(call.get("env", {}).get("HOME"), "/tmp",
                        "HOME 을 non-root 가 쓸 수 있는 곳으로 고정하지 않았다")
    finally:
        workspace.cleanup()
