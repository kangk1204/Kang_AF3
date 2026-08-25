#!/usr/bin/env python3
"""테스트가 실제로 버그를 잡는지 역검증한다 (버그 재주입).

왜 필요한가
-----------
통과하는 테스트는 두 가지 이유로 통과한다. (a) 코드가 옳아서, (b) 테스트가
아무것도 확인하지 않아서. 둘을 구분하는 유일한 방법은 버그를 일부러 다시 넣고
테스트가 빨간색이 되는지 보는 것이다. 이 검증을 하지 않은 테스트는 통과해도
의미가 없다.

무엇을 하는가
-------------
scripts/ 를 임시 폴더에 복사하고, 아래 목록의 문자열 치환으로 옛 버그를 되살린 뒤,
지정한 테스트만 돌려서 **실패해야 하는 것이 실패하는지** 확인한다.
원본 저장소는 건드리지 않는다.

사용법:
  python3 tests/verify_tests_catch_bugs.py
  python3 tests/verify_tests_catch_bugs.py -k 완료판정
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent

# 재주입 목록.
# name      -- 버그 이름 (사람이 읽는 것)
# script    -- 고칠 파일
# old / new -- 문자열 치환 (old 는 파일에 정확히 1회 나와야 한다)
# tests     -- 이 버그로 실패해야 하는 테스트 이름 (-k 로 넘긴다)
INJECTIONS = [
    {
        "name": "완료판정을 폴더 존재로 되돌린다",
        "detail": "is_complete 가 폴더만 있으면 완료로 본다. 추론 중 끊긴 건이 완료로 집계된다.",
        "script": "run_af3_batch_improved.py",
        "old": """    if mode == "data":
        return nonempty_file(result_dir / f"{output_name}_data.json")
    return all(
        any(nonempty_file(result_dir / f"{output_name}{suffix}") for suffix in group)
        for group in FINAL_REQUIRED_SUFFIXES
    )""",
        "new": """    return True""",
        "tests": [
            "test_data_json_only_is_not_complete",
            "test_exit_code_nonzero_when_jobs_remain",
        ],
    },
    {
        "name": "완료판정에서 크기 0 검사를 뺀다",
        "detail": "디스크가 꽉 차서 0바이트로 쓰인 산출물을 완료로 본다.",
        "script": "run_af3_batch_improved.py",
        "old": """        return not path.is_symlink() and path.is_file() and path.stat().st_size > 0""",
        "new": """        return not path.is_symlink() and path.is_file()""",
        "tests": ["test_data_json_only_is_not_complete"],
    },
    {
        "name": "결과 폴더를 JSON name 대신 파일명으로 찾는다",
        "detail": "AF3 는 name 을 정규화해 폴더를 만든다. 파일명으로 찾으면 끝난 건을 매번 다시 돌린다.",
        "script": "run_af3_batch_improved.py",
        "old": """    output_name = sanitised_name(raw_name)""",
        "new": """    output_name = sanitised_name(json_file.stem)""",
        "tests": [
            "test_output_dir_follows_json_name_not_filename",
            "test_runner_and_af3_agree_on_output_folder_name",
        ],
    },
    {
        "name": "이름 충돌·빈 이름 검사를 없앤다",
        "detail": "'A/B' 와 'AB' 가 같은 폴더를 공유하고, 한글만인 이름은 빈 문자열이 된다.",
        "script": "run_af3_batch_improved.py",
        "old": """    duplicates = {name: group for name, group in by_output.items() if len(group) > 1}""",
        "new": """    duplicates = {}""",
        "tests": ["test_name_collision_is_rejected_before_running"],
    },
    {
        "name": "빈 정규화 이름을 통과시킨다",
        "detail": "한글만으로 된 name 이 빈 문자열이 되어 결과가 출력 루트에 쏟아진다.",
        "script": "run_af3_batch_improved.py",
        "old": """    if not output_name:
        return None, (
            f"name={raw_name!r}은 정규화하면 빈 문자열입니다. "
            "영문/숫자/밑줄/하이픈/점을 한 자 이상 포함하세요"
        )""",
        "new": """    if not output_name:
        output_name = "unnamed\"""",
        "tests": ["test_hangul_only_name_is_rejected"],
    },
    {
        "name": "깨진 JSON 사전 검증을 없앤다",
        "detail": "--input_dir 순회가 깨진 파일에서 멈춰 그 뒤 입력이 처리되지 않는다.",
        "script": "run_af3_batch_improved.py",
        "old": """    if errors:
        print_input_errors(errors)
        return 2""",
        "new": """    if errors:
        print_input_errors(errors)""",
        "tests": ["test_broken_json_is_caught_before_running"],
    },
    {
        "name": "macOS 사이드카 제외를 없앤다",
        "detail": "._*.json 은 UTF-8 이 아니라 읽는 순간 배치 전체가 죽는다.",
        "script": "run_af3_batch_improved.py",
        "old": """        path for path in input_dir.glob("*.json") if not path.name.startswith("._")""",
        "new": """        path for path in input_dir.glob("*.json")""",
        "tests": ["test_macos_appledouble_sidecar_is_excluded"],
    },
    {
        "name": "미완료가 남아도 종료코드 0 을 돌려준다",
        "detail": "자동화가 실패를 성공으로 오인해 결과가 빠진 채 다음 단계로 넘어간다.",
        "script": "run_af3_batch_improved.py",
        "old": """        print("[안내] 다시 실행하면 미완료 작업만 재시도합니다.")
        return 1
    if run_failed:
        print("[오류] 필수 산출물은 존재하지만 하나 이상의 Docker 실행이 0이 아닌 코드로 끝났습니다.")
        return 1""",
        "new": """        print("[안내] 다시 실행하면 미완료 작업만 재시도합니다.")
    if run_failed:
        print("[오류] Docker 실패를 무시합니다.")""",
        "tests": ["test_exit_code_nonzero_when_jobs_remain"],
    },
    {
        "name": "staging 소유 표식 확인을 없앤다",
        "detail": "이름만 맞으면 무조건 지운다. 같은 접두어의 남의 폴더가 사라진다.",
        "script": "run_af3_batch_improved.py",
        "old": """        marker = stage_dir / STAGE_MARKER_NAME
        if marker.is_symlink() or not marker.is_file():
            statuses.append(StageStatus(stage_dir, False, "소유 표식 없음"))
            continue""",
        "new": """        marker = stage_dir / STAGE_MARKER_NAME
        if marker.is_symlink() or not marker.is_file():
            statuses.append(StageStatus(stage_dir, True, "이름이 맞으니 삭제"))
            continue""",
        "tests": ["test_foreign_staging_dir_is_preserved"],
    },
    {
        "name": "격리 개수 제한을 없앤다",
        "detail": "반복 실패가 무한히 쌓여 디스크를 채운다.",
        "script": "run_af3_batch_improved.py",
        "old": """    removed = 0
    for old_snapshot in managed[keep:]:""",
        "new": """    removed = 0
    for old_snapshot in []:""",
        "tests": [
            "test_quarantine_growth_is_bounded_per_job",
            "test_quarantine_keep_option_is_honoured",
        ],
    },
    {
        "name": "중복 실행 잠금을 없앤다",
        "detail": "같은 출력 폴더에 두 실행이 붙어 서로의 판정을 뒤집는다.",
        "script": "run_af3_batch_improved.py",
        "old": """            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)""",
        "new": """            pass""",
        "tests": ["test_concurrent_run_on_same_output_is_blocked"],
    },
    {
        "name": "구버전 이미지 대비 전환을 없앤다",
        "detail": "--input_dir 이 없는 이미지에서 그 플래그를 그대로 붙여 즉시 실패한다.",
        "script": "run_af3_batch_improved.py",
        "old": """                batch_supported = "input_dir" in supported""",
        "new": """                batch_supported = True""",
        "tests": ["test_legacy_image_without_input_dir_falls_back_to_per_file"],
    },
    {
        "name": "이미지 확인 실패를 무시하고 최신 플래그를 추측한다",
        "detail": "이미지를 못 찾았는데 실행을 계속해 원인 모를 실패로 끝난다.",
        "script": "run_af3_batch_improved.py",
        "old": """        print("       확인 실패 상태에서 최신 플래그를 추측하여 실행하지 않습니다.")
        return None""",
        "new": """        print("       확인 실패 상태에서 최신 플래그를 추측하여 실행하지 않습니다.")
        return set(KNOWN_FLAGS)""",
        "tests": ["test_image_probe_failure_stops_with_reason"],
    },
    {
        "name": "집계에서 숨은/관리 폴더 제외를 없앤다",
        "detail": ".af3_incomplete 안의 실패 결과가 완료 건으로 집계된다.",
        "script": "af3_collect.py",
        "old": """    return name.startswith("._") or name.startswith(".")""",
        "new": """    return name.startswith("._")""",
        "tests": ["test_managed_dirs_are_excluded_from_collection"],
    },
    {
        "name": "집계 결과가 없어도 0 을 돌려준다",
        "detail": "자동화가 빈 CSV 를 정상 결과로 오인한다.",
        "script": "af3_collect.py",
        "old": """        log("오류: 집계할 완료 결과가 없다. 출력 폴더 경로를 확인하라.")
        return 1""",
        "new": """        log("오류: 집계할 완료 결과가 없다. 출력 폴더 경로를 확인하라.")
        return 0""",
        "tests": ["test_empty_collection_exits_nonzero"],
    },
    {
        "name": "시각화에서 숨은/관리 폴더 제외를 없앤다",
        "detail": "격리된 실패 결과의 플롯을 그린다.",
        "script": "af3_visualize.py",
        "old": """    return name.startswith("._") or name.startswith(".")""",
        "new": """    return name.startswith("._")""",
        "tests": ["test_managed_dirs_are_excluded_from_visualization"],
    },
    {
        "name": "타임스탬프 접미사 결과 폴더 탐색을 없앤다",
        "detail": "af3_batch.py 가 이미 끝난 건을 못 찾아 다시 돌린다.",
        "script": "af3_batch.py",
        # 2026-08 갱신: 예전 재주입은 glob(s + "_*") 줄을 지우는 것이었는데,
        # 타깃명정규화 트랙이 find_result_dirs 를 stem 기준으로 다시 써서
        # 그 줄이 없어졌다 (치환 대상 0회로 건너뛰어졌다). 같은 버그를 지금 코드에
        # 되살리려면 stem 대조를 폴더명 대조로 되돌리면 된다. 그러면 타임스탬프
        # 접미사 형제 폴더를 못 찾아 이미 끝난 건을 다시 돌린다.
        "old": """        info = resolve_result_dir(p, mode="full")
        if info["stem"] == want:""",
        "new": """        info = resolve_result_dir(p, mode="full")
        if p.name == want:""",
        "tests": ["test_batch_finds_timestamp_suffix_result_dirs"],
    },
    # 아래 항목의 old/new 는 원본에서 그대로 떠낸 조각이라 repr 로 적는다.
    # 셸 따옴표가 섞여 있어 삼중따옴표로는 원문을 정확히 보존할 수 없다.
    {
        "name": "이미지 능력 검증을 errexit 의존 형태로 되돌린다",
        "detail": "함수를 || 리스트에서 부르면 bash 가 본문 전체의 errexit 를 꺼서 AF3 버전 assert 와 --seq_limit 검사가 실패해도 설치가 계속된다.",
        "script": "install_af3_ubuntu.sh",
        "old": 'validate_image_capabilities() {\n  "${DOCKER[@]}" run --rm --entrypoint python3 "$IMAGE" -c \\\n    "from alphafold3 import version; assert version.__version__ == \'$AF3_VERSION\'" \\\n    >/dev/null || die "AF3 image does not report the pinned version $AF3_VERSION: $IMAGE"\n  "${DOCKER[@]}" run --rm --entrypoint jackhmmer "$IMAGE" -h 2>&1 | \\\n    grep -Fq -- \'--seq_limit\' || \\\n    die "AF3 image lacks the patched HMMER --seq_limit flag: $IMAGE"\n  "${DOCKER[@]}" run --rm --gpus all --entrypoint python3 "$IMAGE" -c \\\n    "import jax; assert jax.default_backend() == \'gpu\'; assert jax.devices()" \\\n    >/dev/null || die "AF3 image cannot reach the GPU through JAX: $IMAGE"\n}\n',
        "new": 'validate_image_capabilities() {\n  _af3_image_checks() {\n    "${DOCKER[@]}" run --rm --entrypoint python3 "$IMAGE" -c \\\n      "from alphafold3 import version; assert version.__version__ == \'$AF3_VERSION\'" \\\n      >/dev/null\n    "${DOCKER[@]}" run --rm --entrypoint jackhmmer "$IMAGE" -h 2>&1 | \\\n      grep -Fq -- \'--seq_limit\'\n    "${DOCKER[@]}" run --rm --gpus all --entrypoint python3 "$IMAGE" -c \\\n      "import jax; assert jax.default_backend() == \'gpu\'; assert jax.devices()" \\\n      >/dev/null\n  }\n  _af3_image_checks || die "AF3 image capability verification failed"\n}\n',
        "tests": ["test_installer_image_capability_gate_fails_on_every_check"],
    },
    {
        "name": "legacy 러너가 단계와 무관하게 가중치를 요구하게 되돌린다",
        "detail": "가중치가 필요 없는 --stage msa 가 core 설치에서 시작조차 못 한다.",
        "script": "af3_batch.py",
        "old": '    if stage_uses_model(args.stage):\n        model_report = verify_model_dir(args.model_dir)',
        "new": '    if True:\n        model_report = verify_model_dir(args.model_dir)',
        "tests": ["test_legacy_preflight_requires_only_what_the_stage_uses"],
    },
    {
        "name": "뷰어 템플릿을 순차 치환으로 되돌린다",
        "detail": "타깃명이 __ENGINEJS__ 면 엔진 스크립트가 데이터 JSON 리터럴 안으로 들어가 페이지가 죽는다.",
        "script": "af3_view3d.py",
        "old": '    return PLACEHOLDER_RE.sub(lambda match: values[match.group(0)], template)',
        "new": '    for slot, value in values.items():\n        template = template.replace(slot, value)\n    return template',
        "tests": ["test_viewer_page_placeholders_survive_target_names_that_look_like_placeholders"],
    },
    {
        "name": "af3.bin 크기 핀의 우회 수단을 없앤다",
        "detail": "새 가중치 릴리스에서 두 배치 러너가 손댈 수 없는 하드 실패로 멈춘다.",
        "script": "af3_db.py",
        "old": '    raw = os.environ.get(MODEL_BYTES_ENV)',
        "new": '    raw = None',
        "tests": ["test_model_size_pin_is_overridable_and_says_so"],
    },
    {
        "name": "staging 파일/폴더 충돌 검사를 무력화한다",
        "detail": "같은 이름을 파일이자 폴더로 staging 해서 실행 직전에 깨진다.",
        "script": "run_af3_batch_improved.py",
        "old": '                if ancestor in planned:',
        "new": '                if False:',
        "tests": ["test_staging_detects_file_directory_conflicts_without_pairwise_scan"],
    },
    {
        "name": "배치 경로의 격리 실패를 다시 무방비로 둔다",
        "detail": "결과 폴더 하나가 이상하면 나머지 전부가 시작조차 못 한다.",
        "script": "run_af3_batch_improved.py",
        "old": '    for job in jobs:\n        try:\n            quarantine_incomplete(output_dir, job, mode, quarantine_keep)\n        except OSError as exc:\n            print(f"[경고] {job.output_name}의 미완료 결과를 보존하지 못했습니다: {exc}")\n            print("       AF3가 이 건의 결과를 타임스탬프 폴더에 따로 쓸 수 있습니다.")\n            quarantine_failed = True\n\n',
        "new": '    for job in jobs:\n        quarantine_incomplete(output_dir, job, mode, quarantine_keep)\n',
        "tests": ["test_batch_run_survives_one_unquarantinable_result"],
    },
    {
        "name": "prepare --report 가 다시 symlink 를 따라가게 한다",
        "detail": "요약표 저장이 출력 폴더 밖 파일을 덮어쓴다.",
        "script": "af3_prepare.py",
        "old": '            atomic_write_text(args.report, buffer.getvalue())',
        "new": '            open(args.report, "w", encoding="utf-8-sig", newline="").write(\n                buffer.getvalue())',
        "tests": ["test_prepare_report_does_not_follow_a_symlinked_destination"],
    },
    {
        "name": "prepare 의 비-폴더 출력 경로 검사를 없앤다",
        "detail": "-o 가 일반 파일이면 NotADirectoryError 트레이스백이 그대로 나온다.",
        "script": "af3_prepare.py",
        "old": '    if p.is_symlink() or (p.exists() and not p.is_dir()):\n        die("출력 경로 \'%s\' 가 폴더가 아니다 (일반 파일이거나 symlink 다).\\n"\n            "      -o 에는 JSON 을 담을 폴더 경로를 줘라." % outdir)\n',
        "new": '',
        "tests": ["test_prepare_rejects_a_non_directory_output_path_with_a_readable_error"],
    },
    {
        "name": "컨테이너에 이름을 붙이지 않는다",
        "detail": "러너가 죽은 뒤 남은 컨테이너를 찾을 방법이 사라진다.",
        "script": "run_af3_batch_improved.py",
        "old": '    command = [*docker_command, "run", "--rm"]\n    if container:\n        command.extend(("--name", container))\n',
        "new": '    command = [*docker_command, "run", "--rm"]\n',
        "tests": ["test_runner_names_containers_and_reports_orphans"],
    },
    {
        "name": "고아 컨테이너 판정을 없앤다",
        "detail": "--audit/--cleanup 이 GPU를 계속 먹는 고아 컨테이너를 못 본다.",
        "script": "run_af3_batch_improved.py",
        "old": '        if pid != os.getpid() and not process_is_alive(pid):\n            orphans.append(name)',
        "new": '        if False:\n            orphans.append(name)',
        "tests": ["test_runner_names_containers_and_reports_orphans"],
    },
    {
        "name": "종료 시 컨테이너 정리를 없앤다",
        "detail": "Ctrl-C/SIGTERM 으로 러너가 죽으면 컨테이너가 계속 돈다.",
        "script": "run_af3_batch_improved.py",
        "old": '    finally:\n        if docker_command and container:\n            subprocess.run(\n                [*docker_command, "rm", "-f", container],\n                stdout=subprocess.DEVNULL,\n                stderr=subprocess.DEVNULL,\n                check=False,\n            )\n',
        "new": '',
        "tests": ["test_runner_names_containers_and_reports_orphans"],
    },
    {
        "name": "CSP 에서 'unsafe-eval' 을 다시 뺀다",
        "detail": "molstar 가 초기화 중 new Function 을 못 불러 죽는다. 지표 표만 뜨고 구조는 안 나온다.",
        "script": "af3_view3d.py",
        "old": """script-src 'unsafe-inline' 'unsafe-eval' https://cdn.jsdelivr.net""",
        "new": """script-src 'unsafe-inline' https://cdn.jsdelivr.net""",
        "tests": ["test_viewer_csp_allows_what_the_molstar_engine_needs"],
    },
]


def run_suite(scripts_dir: Path, keys: list[str], timeout: int = 300):
    """지정한 scripts 폴더를 대상으로 테스트를 돌린다."""
    import os

    env = dict(os.environ)
    env["AF3_TESTS_SCRIPTS_DIR"] = str(scripts_dir)
    env["PYTHONIOENCODING"] = "utf-8"
    args = [sys.executable, str(TESTS_DIR / "run_tests.py"), "--strict", "--first-fail"]
    results = {}
    for key in keys:
        proc = subprocess.run(
            [*args, "-k", key],
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout,
        )
        results[key] = proc
    return results


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="버그 재주입 역검증")
    parser.add_argument("-k", dest="filter", default=None, help="버그 이름 부분일치 필터")
    parser.add_argument(
        "--keep", action="store_true", help="재주입한 임시 사본을 지우지 않는다"
    )
    args = parser.parse_args(argv)

    injections = INJECTIONS
    if args.filter:
        injections = [i for i in INJECTIONS if args.filter in i["name"]]
    if not injections:
        print(f"[오류] 조건에 맞는 재주입이 없다: -k {args.filter}")
        return 2

    print("=" * 74)
    print(" 역검증: 버그를 다시 넣으면 테스트가 잡는가")
    print("=" * 74)
    print(" 원본 저장소는 건드리지 않는다 (임시 사본에 재주입한다).")
    print("=" * 74)

    caught: list[dict] = []
    missed: list[tuple[dict, str]] = []
    start = time.monotonic()

    for index, injection in enumerate(injections, 1):
        print(f"[{index:2d}/{len(injections)}] {injection['name']}")
        print(f"         되살린 버그: {injection['detail']}")
        temp_root = Path(tempfile.mkdtemp(prefix="af3_inject_"))
        try:
            scripts_copy = temp_root / "scripts"
            shutil.copytree(REPO_ROOT / "scripts", scripts_copy)
            target = scripts_copy / injection["script"]
            text = target.read_text(encoding="utf-8")
            occurrences = text.count(injection["old"])
            if occurrences != 1:
                print(
                    f"         [건너뜀] 치환 대상이 {occurrences}회 나온다. "
                    "스크립트가 바뀌었으니 재주입 목록을 갱신하라."
                )
                missed.append((injection, f"치환 대상 {occurrences}회 (1회여야 한다)"))
                continue
            target.write_text(
                text.replace(injection["old"], injection["new"]), encoding="utf-8"
            )

            # 기준선 확인: 버그를 넣지 않은 사본에서 그 테스트가 **통과** 해야 한다.
            # 이것을 확인하지 않으면 테스트가 오타/TypeError 로 늘 실패하는 상태에서도
            # "버그를 잡았다" 로 집계된다. 실제로 그런 일이 있었다 (check_equal 인자 오류).
            baseline_root = Path(tempfile.mkdtemp(prefix="af3_base_"))
            try:
                baseline_scripts = baseline_root / "scripts"
                shutil.copytree(REPO_ROOT / "scripts", baseline_scripts)
                baseline = run_suite(baseline_scripts, injection["tests"])
            finally:
                shutil.rmtree(baseline_root, ignore_errors=True)

            results = run_suite(scripts_copy, injection["tests"])
            all_caught = True
            for test_name, proc in results.items():
                base = baseline[test_name]
                if base.returncode != 0:
                    all_caught = False
                    first_line = next(
                        (
                            line.strip()
                            for line in base.stdout.splitlines()
                            if "무엇이 틀렸나" in line or "실패 목록" in line
                        ),
                        "(원인은 -k 로 직접 돌려 확인하라)",
                    )
                    print(
                        f"         {test_name}: 기준선 실패 — 버그를 넣지 않아도 실패한다"
                    )
                    missed.append(
                        (
                            injection,
                            f"{test_name} 이 깨끗한 코드에서도 실패한다 ({first_line})",
                        )
                    )
                    continue
                failed = proc.returncode != 0
                mark = "잡았다" if failed else "놓쳤다"
                print(f"         {test_name}: {mark}")
                if not failed:
                    all_caught = False
                    missed.append((injection, f"{test_name} 이 통과해버렸다"))
            if all_caught:
                caught.append(injection)
        finally:
            if not args.keep:
                shutil.rmtree(temp_root, ignore_errors=True)
            else:
                print(f"         사본 보존: {temp_root}")

    elapsed = time.monotonic() - start
    print("-" * 74)
    print(
        f"버그를 잡은 재주입 {len(caught)}/{len(injections)}건, {elapsed:.1f}초"
    )
    if missed:
        print("\n[문제] 아래 재주입은 테스트가 잡지 못했다. 그 테스트는 통과해도 의미가 없다.")
        for injection, reason in missed:
            print(f"  - {injection['name']}: {reason}")
        return 1
    print("\n모든 재주입을 테스트가 잡았다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
