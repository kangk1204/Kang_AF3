#!/usr/bin/env python3
"""Dependency-free release verification entry point."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def run(label, command):
    print("\n" + "=" * 76)
    print(label)
    print("=" * 76)
    process = subprocess.run(command, cwd=ROOT, check=False)
    if process.returncode != 0:
        print("FAILED: %s (exit %d)" % (label, process.returncode), file=sys.stderr)
    return process.returncode


def static_python_check():
    failures = []
    for folder in (ROOT / "scripts", ROOT / "tests"):
        for path in sorted(folder.glob("*.py")):
            try:
                ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except (OSError, SyntaxError, UnicodeDecodeError) as exc:
                failures.append("%s: %s" % (path, exc))
    for failure in failures:
        print(failure, file=sys.stderr)
    return 1 if failures else 0


def main():
    checks = [
        ("registered strict regressions", [sys.executable, "tests/run_tests.py", "--strict"]),
        ("target naming integration", [sys.executable, "tests/test_naming.py"]),
        ("filename compatibility", [sys.executable, "tests/test_filename_lang.py"]),
        ("mutation verification", [sys.executable, "tests/verify_tests_catch_bugs.py"]),
        ("rank correlation self-test", [sys.executable, "scripts/af3_rankcorr.py", "--selftest"]),
        (
            "shell syntax",
            [
                "bash",
                "-n",
                "scripts/af3_check.sh",
                "scripts/af3run.sh",
                "scripts/install_af3_ubuntu.sh",
            ],
        ),
    ]
    failed = 0
    print("Python %s" % sys.version.split()[0])
    failed += static_python_check()
    for label, command in checks:
        failed += run(label, command) != 0
    print("\n" + "=" * 76)
    if failed:
        print("release verification failed: %d check group(s)" % failed)
        return 1
    print("release verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
