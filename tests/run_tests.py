#!/usr/bin/env python3
"""AlphaFold 3 배치 파이프라인 회귀 테스트 — 단일 진입점.

사용법:
  python3 tests/run_tests.py              # 전체 실행
  python3 tests/run_tests.py -v           # 실패 상세 + 각 테스트가 막는 버그 표시
  python3 tests/run_tests.py -k stage     # 이름/항목에 'stage' 가 든 것만
  python3 tests/run_tests.py --list       # 목록만 보기 (실행하지 않음)
  python3 tests/run_tests.py --strict     # '현재 버전 실패 예상' 항목도 실패로 계산

필요한 것: Python 3.9 이상. 외부 패키지 없음. Docker 없음.
Docker 는 tests/fake_docker.py 스텁이 가로챈다 (스텁의 근거는 그 파일 주석 참고).
"""

from __future__ import annotations

import argparse
import importlib
import platform
import sys
import time
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

import harness  # noqa: E402  (sys.path 조정 후에 불러와야 한다)

TEST_MODULES = (
    "test_completion",
    "test_inputs",
    "test_state",
    "test_reporting",
)


def load_all() -> None:
    for name in TEST_MODULES:
        importlib.import_module(name)


def print_header() -> None:
    print("=" * 74)
    print(" AlphaFold 3 배치 파이프라인 회귀 테스트")
    print("=" * 74)
    print(f" Python {platform.python_version()} / {platform.system()} {platform.machine()}")
    print(f" 대상 스크립트: {harness.SCRIPTS_DIR}")
    print(f" docker 스텁:   {harness.FAKE_DOCKER.name} (실제 Docker 는 쓰지 않는다)")
    print("=" * 74)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Docker 없이 도는 회귀 테스트",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("-k", dest="filter", default=None, help="이름/항목 부분일치 필터")
    parser.add_argument("-v", "--verbose", action="store_true", help="막는 버그 설명까지 표시")
    parser.add_argument("--list", action="store_true", help="목록만 출력하고 종료")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="'현재 버전 실패 예상' 항목의 실패도 실패로 계산",
    )
    parser.add_argument(
        "--first-fail", action="store_true", help="첫 실패에서 즉시 멈춘다"
    )
    args = parser.parse_args(argv)

    load_all()
    tests = harness.REGISTRY
    if args.filter:
        needle = args.filter.lower()
        tests = [
            t
            for t in tests
            if needle in t["name"].lower()
            or needle in t["item"].lower()
            or needle in t["module"].lower()
        ]
    if not tests:
        print(f"[오류] 조건에 맞는 테스트가 없다: -k {args.filter}")
        return 2

    if args.list:
        print_header()
        for test in tests:
            print(f"[항목 {test['item']}] {test['name']}")
            print(f"    막는 버그: {test['prevents']}")
        print(f"\n총 {len(tests)}개")
        return 0

    print_header()
    passed: list[dict] = []
    failed: list[tuple[dict, str, float]] = []
    known: list[tuple[dict, str]] = []  # 현재 버전에서 실패가 예상된 항목
    unexpected_pass: list[dict] = []

    start_all = time.monotonic()
    for index, test in enumerate(tests, 1):
        label = f"[{index:2d}/{len(tests)}] 항목 {test['item']:>3} {test['name']}"
        print(f"{label} ... ", end="", flush=True)
        started = time.monotonic()
        captured = harness.capture_output()
        try:
            with captured:
                test["func"]()
        except BaseException as exc:  # noqa: BLE001  (테스트 실패도 오류도 모두 잡는다)
            elapsed = time.monotonic() - started
            detail = harness.format_failure(exc)
            if captured.text.strip():
                tail = "\n".join(captured.text.strip().splitlines()[-12:])
                detail = f"{detail}\n      스크립트 출력(끝부분):\n" + "\n".join(
                    f"        {line}" for line in tail.splitlines()
                )
            if test["expect_fail"] and not args.strict:
                known.append((test, detail))
                print(f"예상된 실패 ({elapsed:.1f}초)")
            else:
                failed.append((test, detail, elapsed))
                print(f"실패 ({elapsed:.1f}초)")
                print(f"      막는 버그: {test['prevents']}")
                print(f"      무엇이 틀렸나: {detail}")
                if args.first_fail:
                    break
        else:
            elapsed = time.monotonic() - started
            passed.append(test)
            if test["expect_fail"]:
                unexpected_pass.append(test)
                print(f"통과 ({elapsed:.1f}초)  ※ 실패 예상이었으나 통과 - 고쳐진 것 같다")
            else:
                print(f"통과 ({elapsed:.1f}초)")
            if args.verbose:
                print(f"      막는 버그: {test['prevents']}")
    total_elapsed = time.monotonic() - start_all

    print("-" * 74)
    print(
        f"통과 {len(passed)}개, 실패 {len(failed)}개, "
        f"예상된 실패 {len(known)}개 / 총 {len(tests)}개, {total_elapsed:.1f}초"
    )
    if known:
        print("\n[예상된 실패] 다른 트랙이 아직 고치는 중인 동작이다. 진짜 문제와 구분하라.")
        for test, detail in known:
            print(f"  - 항목 {test['item']} {test['name']}")
            print(f"      {detail.splitlines()[0]}")
    if unexpected_pass:
        print("\n[안내] 아래 항목은 '실패 예상' 표시가 붙어 있는데 통과했다.")
        print("       해당 동작이 고쳐졌다는 뜻이므로 expect_fail_on_current=True 를 지워라.")
        for test in unexpected_pass:
            print(f"  - {test['name']}")
    if failed:
        print("\n[실패 목록] 각 항목의 '막는 버그' 를 먼저 읽어라. 무엇을 지키려던 것인지 나온다.")
        for test, detail, _elapsed in failed:
            print(f"  - 항목 {test['item']} {test['name']}: {detail.splitlines()[0]}")
        print("\n디버깅 요령:")
        print("  python3 tests/run_tests.py -k <테스트이름> -v   # 한 개만 다시")
        return 1
    if known:
        print(
            f"\n예상하지 못한 실패는 없다 (통과 {len(passed)}개, 예상된 실패 {len(known)}개)."
        )
    else:
        print("\n전부 통과했다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
