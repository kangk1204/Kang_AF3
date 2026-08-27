#!/usr/bin/env python3
"""Dependency-free release verification entry point."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def _has_regression_decorator(node):
    for decorator in node.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if isinstance(target, ast.Name) and target.id == "regression":
            return True
    return False


def registered_test_files():
    result = set()
    for path in sorted((ROOT / "tests").glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if _has_regression_decorator(node):
                result.add(path.name)
                break
            if path.name in result:
                break
    return result


def undecorated_top_level_tests(path):
    """Return test functions that a registered module would silently omit."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return [
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
        and not _has_regression_decorator(node)
    ]


def unwrapped_unittest_methods(path):
    """Return TestCase methods not exercised by a registered _run_case wrapper."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    methods = set()
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        is_test_case = any(
            (isinstance(base, ast.Attribute) and base.attr == "TestCase")
            or (isinstance(base, ast.Name) and base.id == "TestCase")
            for base in node.bases
        )
        if not is_test_case:
            continue
        methods.update(
            (node.name, child.name)
            for child in node.body
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            and child.name.startswith("test_")
        )
    wrapped = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "_run_case"):
            continue
        if len(node.args) >= 2 and isinstance(node.args[0], ast.Name) \
                and isinstance(node.args[1], ast.Constant) \
                and isinstance(node.args[1].value, str):
            wrapped.add((node.args[0].id, node.args[1].value))
    return sorted(methods - wrapped)


def has_main_guard(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
            continue
        left = node.test.left
        if not (isinstance(left, ast.Name) and left.id == "__name__"):
            continue
        if any(isinstance(value, ast.Constant) and value.value == "__main__"
               for value in node.test.comparators):
            return True
    return False


def standalone_test_files():
    registered = registered_test_files()
    return sorted(
        path for path in (ROOT / "tests").glob("test_*.py")
        if path.name not in registered
    )


def test_discovery_check():
    all_tests = {path.name for path in (ROOT / "tests").glob("test_*.py")}
    registered = registered_test_files()
    standalone_paths = standalone_test_files()
    standalone = {path.name for path in standalone_paths}
    overlap = registered & standalone
    missing = all_tests - registered - standalone
    missing_entrypoints = [path.name for path in standalone_paths if not has_main_guard(path)]
    undecorated = {
        name: undecorated_top_level_tests(ROOT / "tests" / name)
        for name in sorted(registered)
        if undecorated_top_level_tests(ROOT / "tests" / name)
    }
    unwrapped = {
        name: unwrapped_unittest_methods(ROOT / "tests" / name)
        for name in sorted(registered)
        if unwrapped_unittest_methods(ROOT / "tests" / name)
    }
    if overlap or missing or missing_entrypoints or undecorated or unwrapped:
        if overlap:
            print("registered/standalone overlap: %s" % sorted(overlap), file=sys.stderr)
        if missing:
            print("uncovered test files: %s" % sorted(missing), file=sys.stderr)
        if missing_entrypoints:
            print("standalone tests without __main__ entrypoint: %s" %
                  missing_entrypoints, file=sys.stderr)
        if undecorated:
            print("undecorated tests in registered modules: %s" % undecorated,
                  file=sys.stderr)
        if unwrapped:
            print("unwrapped unittest methods in registered modules: %s" % unwrapped,
                  file=sys.stderr)
        return 1
    print("test discovery complete: %d registered modules, %d standalone suites" %
          (len(registered), len(standalone)))
    return 0


def run(label, command, env):
    print("\n" + "=" * 76)
    print(label)
    print("=" * 76)
    process = subprocess.run(command, cwd=ROOT, check=False, env=env)
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
    standalone_checks = [
        ("standalone " + path.stem, [sys.executable, str(path.relative_to(ROOT))])
        for path in standalone_test_files()
    ]
    checks = [
        ("registered strict regressions", [sys.executable, "tests/run_tests.py", "--strict"]),
        *standalone_checks,
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
    failed += test_discovery_check()
    with tempfile.TemporaryDirectory(prefix="af3_release_path_") as temp_dir:
        blocker = Path(temp_dir) / "docker"
        blocker.write_text("#!/bin/sh\nexit 127\n", encoding="utf-8")
        blocker.chmod(0o755)
        env = dict(os.environ)
        for key in list(env):
            if key.startswith("AF3_") or key in {"BASH_ENV", "ENV", "CDPATH", "GLOBIGNORE"}:
                env.pop(key, None)
        env["PATH"] = temp_dir + os.pathsep + env.get("PATH", "")
        for label, command in checks:
            failed += run(label, command, env) != 0
    print("\n" + "=" * 76)
    if failed:
        print("release verification failed: %d check group(s)" % failed)
        return 1
    print("release verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
