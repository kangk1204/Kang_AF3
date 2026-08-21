#!/usr/bin/env python3
"""Prepare, stage-2, legacy-runner, and diagnostic safety regressions."""

from __future__ import annotations

import csv
import fcntl
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
from pathlib import Path

from harness import (
    REPO_ROOT,
    SCRIPTS_DIR,
    Workspace,
    check,
    check_equal,
    check_in,
    load_module,
    make_stub_bin,
    regression,
    run_script,
)


@regression(
    item="docs",
    prevents="README/docs 정리 중 상대 링크 대상이 삭제·이동돼 초보 사용자가 설치 근거 문서를 열 수 없는 버그.",
)
def test_markdown_local_links_resolve():
    import re

    missing = []
    link_pattern = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
    for document in sorted(REPO_ROOT.rglob("*.md")):
        text = document.read_text(encoding="utf-8")
        for match in link_pattern.finditer(text):
            raw = match.group(1).strip()
            if raw.startswith("<") and ">" in raw:
                raw = raw[1:raw.index(">")]
            else:
                raw = raw.split(maxsplit=1)[0]
            if not raw or raw.startswith(("#", "http://", "https://", "mailto:")):
                continue
            relative = urllib.parse.unquote(raw.split("#", 1)[0].split("?", 1)[0])
            target = (document.parent / relative).resolve()
            if not target.exists():
                line = text.count("\n", 0, match.start()) + 1
                missing.append("%s:%d -> %s" % (document.relative_to(REPO_ROOT), line, raw))
    check(not missing, "깨진 Markdown 상대 링크가 있다", "\n".join(missing[:30]))

    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    invalid_visualize_commands = [
        line for line in readme.splitlines()
        if "af3_visualize.py" in line and "--out-dir" in line
    ]
    check(
        not invalid_visualize_commands,
        "README가 af3_visualize.py에 없는 --out-dir 옵션을 안내한다",
        "\n".join(invalid_visualize_commands),
    )
    for snippet in (
        "bash scripts/install_af3_ubuntu.sh --full --accept-weights-terms",
        "```bash\nnvidia-smi\n```",
        "python3 -m venv --without-pip --system-site-packages ~/af3_plot_env",
        "sudo apt install -y python3-matplotlib python3-venv",
        "~/af3_plot_env/bin/python scripts/af3_visualize.py",
        "python3 scripts/af3_view3d.py",
    ):
        check_in(snippet, readme, "README의 자립형 설치/시각화 흐름이 끊겼다")

    check_in("비공개 연구 협업 저장소", readme, "현재 저장소 공개 범위를 설명하지 않았다")
    for stale_tone in ("이 저장소는 공개다", "읽어라", " 보라", "마라", "함정", "급히"):
        check(stale_tone not in readme, "README에 과도한 지시체가 남았다", stale_tone)

    quick_summary = readme.partition("## Quick Start")[2].partition("\n---\n")[0]
    check(quick_summary, "README 상단 Quick Start가 없다")
    for quick_heading in ("### 1. 설치", "### 2. 예제로 배치 실행", "### 3. 본인 입력 준비"):
        check_in(quick_heading, quick_summary, "Quick Start의 설치·실행 순서가 불완전하다")
    for input_step in (
        "cp examples/vhh_monomer.json quick_in/",
        "--fasta my_sequences.fasta",
        ">sample_01",
        "--dry-run",
    ):
        check_in(input_step, quick_summary, "Quick Start의 입력 준비 절차가 불완전하다")
    for benchmark_only in ("31.95초", "5.39초", "5.93배", "189시간"):
        check(benchmark_only not in quick_summary, "Quick Start가 목적 대신 성능 비교를 앞세운다", benchmark_only)


@regression(
    item="install",
    prevents="단일 설치기가 약관 동의 전에 sudo를 호출하거나 dry-run에서 파일·네트워크를 건드리고, 부분 DB를 완료로 오인하는 버그.",
)
def test_installer_help_dry_run_and_safety_gates():
    installer = SCRIPTS_DIR / "install_af3_ubuntu.sh"
    check(installer.is_file(), "Ubuntu 단일 설치기가 없다", str(installer))

    def make_isolated_installer(root: Path, name: str) -> tuple[Path, Path]:
        isolated_scripts = root / name / "scripts"
        isolated_scripts.mkdir(parents=True)
        isolated_installer = isolated_scripts / installer.name
        isolated_source = installer.read_text(encoding="utf-8")
        production_lock = 'LOCK_FILE="/tmp/kang-af3-install-${UID}.lock"'
        lock_path = root / (name + ".lock")
        private_lock = 'LOCK_FILE="%s"' % lock_path
        check_equal(isolated_source.count(production_lock), 1, "installer lock 선언을 격리할 수 없다")
        isolated_installer.write_text(
            isolated_source.replace(production_lock, private_lock),
            encoding="utf-8",
        )
        isolated_installer.chmod(0o755)
        for support_name in ("af3_check.sh", "af3_db.py"):
            shutil.copy2(SCRIPTS_DIR / support_name, isolated_scripts / support_name)
        return isolated_installer, lock_path

    def add_fake_nvidia_smi(fake_bin: Path) -> None:
        fake_nvidia = fake_bin / "nvidia-smi"
        fake_nvidia.write_text(
            "#!/usr/bin/env bash\n"
            "if [[ ${1:-} == -L ]]; then echo 'GPU 0: test GPU'; fi\n"
            "exit 0\n",
            encoding="utf-8",
        )
        fake_nvidia.chmod(0o755)

    help_proc = subprocess.run(
        ["bash", str(installer), "--help"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    check_equal(help_proc.returncode, 0, "installer --help가 실패했다", help_proc.stderr)
    check_in("--full", help_proc.stdout, "complete-install 옵션을 설명하지 않았다")
    check_in(
        "--accept-weights-terms",
        help_proc.stdout,
        "가중치 약관 동의 옵션을 설명하지 않았다",
    )

    with tempfile.TemporaryDirectory(prefix="af3_installer_dry_") as td:
        root = Path(td)
        env = dict(os.environ)
        env.update(
            {
                "HOME": str(root),
                "AF3_WORK_DIR": str(root / "work with spaces;$(touch injected)"),
                "AF3_MODEL_DIR": str(root / "models with spaces"),
                "AF3_DB_DIR": str(root / "db with spaces"),
                "AF3_PLOT_ENV": str(root / "plot with spaces"),
                "AF3_IMAGE": "alphafold3:test",
            }
        )
        before = sorted(root.iterdir())
        dry_proc = subprocess.run(
            [
                "bash",
                str(installer),
                "--dry-run",
                "--full",
                "--accept-weights-terms",
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        check_equal(dry_proc.returncode, 0, "installer dry-run이 실패했다", dry_proc.stderr)
        check_equal(
            sorted(root.iterdir()),
            before,
            "dry-run이 HOME 아래에 파일을 만들었다",
        )
        check(not (root / "injected").exists(), "환경변수 경로에서 명령이 실행됐다")
        check_in("dry-run", (dry_proc.stdout + dry_proc.stderr).lower(), "dry-run 표시가 없다")

    with tempfile.TemporaryDirectory(prefix="af3_installer_terms_") as td:
        root = Path(td)
        fake_bin = root / "bin"
        fake_bin.mkdir()
        add_fake_nvidia_smi(fake_bin)
        marker = root / "sudo_called"
        fake_sudo = fake_bin / "sudo"
        fake_sudo.write_text(
            "#!/usr/bin/env bash\n"
            "touch \"$AF3_TEST_SUDO_MARKER\"\n"
            "exit 99\n",
            encoding="utf-8",
        )
        fake_sudo.chmod(0o755)
        env = dict(os.environ)
        env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")
        env["AF3_TEST_SUDO_MARKER"] = str(marker)
        terms_proc = subprocess.run(
            ["bash", str(installer), "--full"],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        check(terms_proc.returncode != 0, "약관 동의 없는 full 설치를 허용했다")
        check(not marker.exists(), "약관 동의 전에 sudo를 호출했다")
        check_in(
            "--accept-weights-terms",
            terms_proc.stdout + terms_proc.stderr,
            "필요한 약관 동의 방법을 설명하지 않았다",
        )

    with tempfile.TemporaryDirectory(prefix="af3_installer_paths_") as td:
        root = Path(td)
        target = root / "real_work"
        target.mkdir()
        symlink = root / "linked_work"
        symlink.symlink_to(target, target_is_directory=True)
        env = dict(os.environ)
        env["AF3_WORK_DIR"] = str(symlink)
        symlink_proc = subprocess.run(
            ["bash", str(installer), "--dry-run"],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        check(symlink_proc.returncode != 0, "symlinked 관리 경로를 허용했다")
        check_in("symlink", symlink_proc.stdout + symlink_proc.stderr, "symlink 거부 원인이 없다")

        real_parent = root / "real_parent"
        real_parent.mkdir()
        linked_parent = root / "linked_parent"
        linked_parent.symlink_to(real_parent, target_is_directory=True)
        env["AF3_WORK_DIR"] = str(linked_parent / "work")
        ancestor_proc = subprocess.run(
            ["bash", str(installer), "--dry-run"],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        check(ancestor_proc.returncode != 0, "symlink ancestor 아래의 관리 경로를 허용했다")
        check_in("symlink", ancestor_proc.stdout + ancestor_proc.stderr, "ancestor symlink 원인이 없다")

        env.update(
            {
                "AF3_WORK_DIR": str(root / "work"),
                "AF3_MODEL_DIR": str(root / "shared"),
                "AF3_DB_DIR": str(root / "shared" / "db"),
                "AF3_PLOT_ENV": str(root / "plot"),
            }
        )
        overlap_proc = subprocess.run(
            ["bash", str(installer), "--dry-run"],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        check(overlap_proc.returncode != 0, "겹치는 관리 경로를 허용했다")
        check_in("overlap", overlap_proc.stdout + overlap_proc.stderr, "경로 충돌 원인이 없다")

    with tempfile.TemporaryDirectory(prefix="af3_installer_bad_db_") as td:
        root = Path(td)
        bad_db = root / "bad_db"
        bad_db.mkdir()
        (bad_db / "partial.fasta").write_text("partial\n", encoding="utf-8")
        fake_bin = root / "bin"
        fake_bin.mkdir()
        add_fake_nvidia_smi(fake_bin)
        marker = root / "sudo_called"
        fake_sudo = fake_bin / "sudo"
        fake_sudo.write_text(
            "#!/usr/bin/env bash\n"
            "touch \"$AF3_TEST_SUDO_MARKER\"\n"
            "exit 99\n",
            encoding="utf-8",
        )
        fake_sudo.chmod(0o755)
        env = dict(os.environ)
        env.update(
            {
                "PATH": str(fake_bin) + os.pathsep + env.get("PATH", ""),
                "AF3_TEST_SUDO_MARKER": str(marker),
                "AF3_DB_DIR": str(bad_db),
                "AF3_MODEL_DIR": str(root / "models"),
            }
        )
        test_installer, _ = make_isolated_installer(root, "bad-db-repo")
        bad_db_proc = subprocess.run(
            [
                "bash",
                str(test_installer),
                "--full",
                "--accept-weights-terms",
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        check(bad_db_proc.returncode != 0, "불완전한 최종 DB를 허용했다")
        check(not marker.exists(), "불완전한 최종 DB를 확인하기 전에 sudo를 호출했다")
        check_in("incomplete or invalid", bad_db_proc.stdout + bad_db_proc.stderr, "DB 거부 원인이 없다")

    with tempfile.TemporaryDirectory(prefix="af3_installer_partial_") as td:
        root = Path(td)
        db_dir = root / "db"
        foreign_partial = Path(str(db_dir) + ".partial")
        foreign_partial.mkdir()
        (foreign_partial / "unowned.txt").write_text("foreign\n", encoding="utf-8")
        fake_bin = root / "bin"
        fake_bin.mkdir()
        add_fake_nvidia_smi(fake_bin)
        marker = root / "sudo_called"
        fake_sudo = fake_bin / "sudo"
        fake_sudo.write_text(
            "#!/usr/bin/env bash\n"
            "touch \"$AF3_TEST_SUDO_MARKER\"\n"
            "exit 99\n",
            encoding="utf-8",
        )
        fake_sudo.chmod(0o755)
        env = dict(os.environ)
        env.update(
            {
                "PATH": str(fake_bin) + os.pathsep + env.get("PATH", ""),
                "AF3_TEST_SUDO_MARKER": str(marker),
                "AF3_WORK_DIR": str(root / "work"),
                "AF3_MODEL_DIR": str(root / "models"),
                "AF3_DB_DIR": str(db_dir),
                "AF3_PLOT_ENV": str(root / "plot"),
            }
        )
        test_installer, _ = make_isolated_installer(root, "partial-repo")
        partial_proc = subprocess.run(
            ["bash", str(test_installer), "--full", "--accept-weights-terms"],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        check(partial_proc.returncode != 0, "외부에서 만든 DB partial을 재사용했다")
        check(not marker.exists(), "외부 DB partial을 거부하기 전에 sudo를 호출했다")
        check_in("not created by this installer", partial_proc.stdout + partial_proc.stderr, "partial 소유 경계를 설명하지 않았다")

    with tempfile.TemporaryDirectory(prefix="af3_installer_plot_") as td:
        root = Path(td)
        plot_env = root / "plot"
        (plot_env / "bin").mkdir(parents=True)
        fake_python = plot_env / "bin" / "python"
        fake_python.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        fake_python.chmod(0o755)
        fake_bin = root / "bin"
        fake_bin.mkdir()
        add_fake_nvidia_smi(fake_bin)
        marker = root / "sudo_called"
        fake_sudo = fake_bin / "sudo"
        fake_sudo.write_text(
            "#!/usr/bin/env bash\n"
            "touch \"$AF3_TEST_SUDO_MARKER\"\n"
            "exit 99\n",
            encoding="utf-8",
        )
        fake_sudo.chmod(0o755)
        env = dict(os.environ)
        env.update(
            {
                "PATH": str(fake_bin) + os.pathsep + env.get("PATH", ""),
                "AF3_TEST_SUDO_MARKER": str(marker),
                "AF3_WORK_DIR": str(root / "work"),
                "AF3_MODEL_DIR": str(root / "models"),
                "AF3_DB_DIR": str(root / "db"),
                "AF3_PLOT_ENV": str(plot_env),
            }
        )
        test_installer, _ = make_isolated_installer(root, "plot-repo")
        plot_proc = subprocess.run(
            ["bash", str(test_installer)],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        check(plot_proc.returncode != 0, "표식 없는 기존 plotting venv를 실행했다")
        check(not marker.exists(), "표식 없는 plotting venv를 거부하기 전에 sudo를 호출했다")
        check_in("not installer-owned", plot_proc.stdout + plot_proc.stderr, "plot venv 신뢰 경계를 설명하지 않았다")

    with tempfile.TemporaryDirectory(prefix="af3_installer_lock_") as td:
        root = Path(td)
        isolated_installer, lock_path = make_isolated_installer(root, "lock-repo")
        with lock_path.open("a+", encoding="utf-8") as lock_handle:
            fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            add_fake_nvidia_smi(fake_bin)
            marker = root / "sudo_called"
            fake_sudo = fake_bin / "sudo"
            fake_sudo.write_text(
                "#!/usr/bin/env bash\n"
                "touch \"$AF3_TEST_SUDO_MARKER\"\n"
                "exit 99\n",
                encoding="utf-8",
            )
            fake_sudo.chmod(0o755)
            env = dict(os.environ)
            env.update(
                {
                    "PATH": str(fake_bin) + os.pathsep + env.get("PATH", ""),
                    "HOME": str(root),
                    "AF3_TEST_SUDO_MARKER": str(marker),
                }
            )
            lock_proc = subprocess.run(
                ["bash", str(isolated_installer)],
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
            check(lock_proc.returncode != 0, "동시 설치 실행을 허용했다")
            check(not marker.exists(), "동시 실행 잠금 실패 뒤 sudo를 호출했다")
            check_in("already running", lock_proc.stdout + lock_proc.stderr, "동시 실행 원인이 없다")

    source = installer.read_text(encoding="utf-8")
    check('DB_PARTIAL="${DB_DIR}.partial"' in source, "DB를 sibling partial에 stage하지 않는다")
    check('EXPECTED_CIF_COUNT="195858"' in source, "고정 DB의 CIF 개수를 검증하지 않는다")
    check(
        "mgy_clusters_2022_05.fa:128579703018" in source,
        "non-empty 검사를 넘어 고정 DB byte 크기를 검증하지 않는다",
    )
    check(
        "9e7f50956c19cbcd8181dc5e9d7d6eebc08257cc858fc07d3ec88fd6b48dbbc9" in source,
        "고정 DB FASTA의 SHA-256을 검증하지 않는다",
    )
    check(
        "c5512426e160df6dfa9533175f4eef3ec31539faa9aa14b2127d0f8d22cf3458" in source,
        "고정 mmCIF content-tree SHA-256을 검증하지 않는다",
    )
    check(
        "4706ec0d948ed7a005b30eea21f5a7f9362b067e48d8bea2605671a49bd43c24" in source,
        "보존된 mmCIF archive SHA-256을 검증하지 않는다",
    )
    check("python3-matplotlib" in source, "서명된 Ubuntu 패키지로 matplotlib을 설치하지 않는다")
    check("--system-site-packages" in source, "plot venv가 Ubuntu matplotlib을 사용하지 않는다")
    check("want_fpr" in source and "fingerprints[@]} == 1" in source, "APT 키의 단일 primary fingerprint를 강제하지 않는다")
    check("stable/deb/nvidia-container-toolkit.list" not in source, "원격 APT source 설정을 그대로 설치한다")
    check("flock -n" in source, "설치기 동시 실행 잠금이 없다")
    check("mv -T --no-clobber" in source, "관리 산출물을 no-clobber로 publish하지 않는다")
    check("DB_PARTIAL_MARKER_NAME" in source, "installer-owned DB partial 표식이 없다")
    check("PLOT_ENV_MARKER_NAME" in source, "installer-owned plotting venv 표식이 없다")
    check("--proto '=https'" in source, "curl redirect를 HTTPS로 제한하지 않는다")
    check("hello-world@sha256:" in source and "ubuntu@sha256:" in source, "검증 컨테이너 tag가 mutable하다")
    check("capability-validated but unlabeled" not in source, "출처 label 없는 기존 AF3 이미지를 재사용한다")
    check("eval " not in source, "installer가 eval을 사용한다")
    check("sudo -S" not in source and "pwd.txt" not in source, "비밀번호 입력을 파일/인자로 받는다")
    check("rm -rf" not in source, "installer가 사용자 경로를 재귀 삭제한다")
    check(
        "run_with_docker_group env" in source,
        "최종 진단이 새 docker 그룹을 적용한 사용자 프로세스에서 돌지 않는다",
    )
    check("sg docker -c" in source and "DOCKER=(sudo" not in source, "오래 걸리는 설치가 sudo timestamp에 의존한다")


@regression(
    item="prepare",
    prevents="af3_prepare 가 자체 파일명만 비교해 AF3 정규화 후 충돌하거나 빈 출력 이름인 JSON을 만드는 버그.",
)
def test_prepare_rejects_af3_name_collision_and_empty_name():
    mod = load_module("af3_prepare.py")
    seq = "ACDEFGHIKLMNPQRSTVWY"
    ok, problems = mod.validate_records(
        [("A/B", seq, 1), ("AB", seq, 2), ("나노바디", seq, 3)],
        "fixture",
        1,
        100,
        False,
    )
    errors = [p.msg for p in problems if p.level == "오류"]
    check(len(ok) == 1, "AF3 충돌/빈 이름을 통과시켰다", str([(x[0], x[1]) for x in ok]))
    check(any("충돌" in msg for msg in errors), "AF3 정규화 충돌을 설명하지 않았다")
    check(any("빈" in msg for msg in errors), "AF3 빈 출력 이름을 설명하지 않았다")


@regression(
    item="prepare",
    prevents="음수 또는 32-bit 범위를 넘는 modelSeeds를 JSON으로 만들어 AF3 실행 때 뒤늦게 실패하는 버그.",
)
def test_prepare_and_stage2_reject_invalid_seed_range():
    with tempfile.TemporaryDirectory(prefix="af3_seed_") as td:
        root = Path(td)
        fasta = root / "x.fasta"
        fasta.write_text(">x\nACDEFGHIKLMN\n", encoding="utf-8")
        for script, args in (
            ("af3_prepare.py", ["--fasta", str(fasta), "-o", str(root / "out"), "--seeds", "-1"]),
            ("af3_stage2.py", ["--list", str(root / "names.txt"), "--from-out", str(root), "-o", str(root / "s2"), "--seeds", str(2**32)]),
        ):
            if script == "af3_stage2.py":
                (root / "names.txt").write_text("x\n", encoding="utf-8")
            proc = subprocess.run([sys.executable, str(SCRIPTS_DIR / script), *args], capture_output=True, text=True)
            check(proc.returncode != 0, f"{script}가 범위 밖 seed를 허용했다")
            check_in("32", proc.stdout + proc.stderr, f"{script}가 seed 범위를 설명하지 않았다")


@regression(
    item="prepare",
    prevents="명시한 partner가 숫자/공백 제거 후 비었는데 옵션이 없었던 것처럼 monomer를 만드는 버그.",
)
def test_prepare_rejects_explicit_empty_partner_and_bad_utf8():
    with tempfile.TemporaryDirectory(prefix="af3_prepare_partner_") as td:
        root = Path(td)
        fasta = root / "x.fasta"
        fasta.write_text(">x\nACDEFGHIKLMN\n", encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "af3_prepare.py"), "--fasta", str(fasta),
             "-o", str(root / "out"), "--partner-seq", "123"],
            capture_output=True, text=True,
        )
        check(proc.returncode != 0, "비어 버린 partner를 monomer로 조용히 바꿨다")
        check_in("파트너", proc.stdout + proc.stderr, "partner 오류를 설명하지 않았다")

        bad = root / "bad.fasta"
        bad.write_bytes(b">x\n\xff\xfe\n")
        proc2 = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "af3_prepare.py"), "--fasta", str(bad),
             "-o", str(root / "bad_out")],
            capture_output=True, text=True,
        )
        check(proc2.returncode != 0, "비 UTF-8 FASTA를 허용했다")
        check("Traceback" not in proc2.stderr, "비 UTF-8 진단 대신 traceback을 냈다")
        check_in("UTF-8", proc2.stdout + proc2.stderr, "인코딩 원인을 설명하지 않았다")


@regression(
    item="prepare",
    prevents="dry-run 안내가 입력 폴더를 출력 폴더로 재사용하거나 특정 과거 GPU 속도를 현재 장비의 보장처럼 보여주는 버그.",
)
def test_prepare_next_step_is_safe_distinct_and_portable():
    with tempfile.TemporaryDirectory(prefix="af3_prepare_guide_") as td:
        root = Path(td)
        fasta = root / "panel.fasta"
        fasta.write_text(">a\nACDEFGHIKLMN\n>b\nACDEFGHIKLMNPQRSTVWY\n", encoding="utf-8")
        input_dir = root / "prepared"
        proc = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS_DIR / "af3_prepare.py"),
                "--fasta",
                str(fasta),
                "-o",
                str(input_dir),
                "--dry-run",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        report = proc.stdout + proc.stderr
        check_equal(proc.returncode, 0, "prepare dry-run이 실패했다", report)
        check_in("scripts/run_af3_batch_improved.py", report, "권장 러너 경로를 안내하지 않았다")
        check_in(str(root / "prepared_out"), report, "입력과 구분된 안전한 출력 폴더를 안내하지 않았다")
        check("--output-dir %s " % input_dir not in report, "입력 폴더를 출력 폴더로 안내했다")
        check("RTX 5070 Ti" not in report and "2.25배" not in report, "과거 장비 성능을 범용 안내로 출력했다")


@regression(
    item="guide",
    prevents="--guide가 저장소 루트에서 실행되지 않는 스크립트 경로와 README와 다른 DB 기본 경로를 복사 명령으로 안내하는 버그.",
)
def test_preferred_guide_uses_executable_path_and_canonical_db_default():
    with tempfile.TemporaryDirectory(prefix="af3_guide_") as td:
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "run_af3_batch_improved.py"), "--guide"],
            cwd=td,
            capture_output=True,
            text=True,
            timeout=120,
        )
        report = proc.stdout + proc.stderr
        check_equal(proc.returncode, 0, "--guide가 실패했다", report)
        check_in(str(SCRIPTS_DIR / "run_af3_batch_improved.py"), report, "복사 가능한 실제 스크립트 경로를 안내하지 않았다")
        check_in(str(Path.home() / "public_databases_full"), report, "README canonical full DB를 기본값으로 쓰지 않았다")


@regression(
    item="stage2",
    prevents="--strip-msa 뒤에도 userCCDPath 같은 sidecar가 남아 새 폴더에서 깨진 JSON을 만드는 버그.",
)
def test_stage2_rejects_remaining_sidecar_after_strip():
    mod = load_module("af3_stage2.py")
    with tempfile.TemporaryDirectory(prefix="af3_stage2_sidecar_") as td:
        path = Path(td) / "x.json"
        path.write_text(json.dumps({
            "name": "x",
            "modelSeeds": [1],
            "dialect": "alphafold3",
            "version": 1,
            "userCCDPath": "ccd.cif",
            "sequences": [{"protein": {"id": "A", "sequence": "ACDE", "unpairedMsa": ">x\\nACDE", "pairedMsa": "", "templates": []}}],
        }), encoding="utf-8")
        built, error = mod.build_one(path, seeds=[1], name_suffix="", do_strip=True, json_version=None)
        check(built is None, "strip 뒤 남은 sidecar를 허용했다")
        check_in("userCCDPath", error or "", "남은 sidecar 이름을 설명하지 않았다")


@regression(
    item="stage2",
    prevents="name 없는 source가 KeyError traceback으로 죽거나 invalid JSON version을 쓰는 버그.",
)
def test_stage2_validates_required_name_and_json_version():
    mod = load_module("af3_stage2.py")
    with tempfile.TemporaryDirectory(prefix="af3_stage2_schema_") as td:
        path = Path(td) / "x.json"
        path.write_text(json.dumps({"sequences": [{"protein": {"id": "A", "sequence": "ACDE"}}]}), encoding="utf-8")
        built, error = mod.build_one(path, seeds=[1], name_suffix="", do_strip=False, json_version=None)
        check(built is None, "name 없는 source를 허용했다")
        check_in("name", error or "", "누락 name을 설명하지 않았다")
        help_parser = mod.build_parser()
        try:
            help_parser.parse_args(["--list", "x", "-o", "y", "--json-version", "999"])
        except SystemExit as exc:
            check(exc.code != 0, "invalid version이 성공으로 parse됐다")
        else:
            check(False, "invalid JSON version을 허용했다")


@regression(
    item="stage2",
    prevents="data JSON과 raw input을 한 폴더에 섞어 놓고 inference-only 실행을 추천해 raw 입력을 실패시키는 버그.",
)
def test_stage2_rejects_mixed_data_and_raw_sources():
    mod = load_module("af3_stage2.py")
    check(mod.choose_run_mode(2, 0, False) == "inference", "data-only 모드 판단이 틀렸다")
    check(mod.choose_run_mode(0, 2, False) == "full", "input-only 모드 판단이 틀렸다")
    try:
        mod.choose_run_mode(1, 1, False)
    except ValueError as exc:
        check_in("섞", str(exc), "혼합 모드 오류가 불명확하다")
    else:
        check(False, "data/raw 혼합을 허용했다")


@regression(
    item="legacy",
    prevents="legacy 러너가 깨진 JSON을 경고만 하고 나머지를 실행해 입력 일부가 빠진 성공을 만드는 버그.",
)
def test_legacy_runner_rejects_any_unreadable_json_before_docker():
    workspace = Workspace()
    try:
        workspace.write_json("a.json", workspace.monomer("a"))
        workspace.write_json("b.json", None, raw_text="{")
        proc = run_script(
            "af3_batch.py",
            [
                "--input-dir", str(workspace.input_dir),
                "--output-dir", str(workspace.output_dir),
                "--db-dir", str(workspace.db_dir),
                "--model-dir", str(workspace.model_dir),
                "--docker", "docker",
                "--stage", "oneshot",
            ],
            workspace,
        )
        check(proc.returncode != 0, "깨진 JSON이 있는데 legacy 러너가 성공했다")
        check_equal(workspace.stub_calls(), [], "사전 검증 실패 뒤 Docker를 실행했다")
    finally:
        workspace.cleanup()


@regression(
    item="legacy",
    prevents="두 러너의 입력 사전검증이 달라 legacy 경로만 잘못된 dialect/키/chain id를 Docker까지 넘기는 버그.",
)
def test_both_runners_reject_the_same_cheap_schema_failures():
    preferred = load_module("run_af3_batch_improved.py")
    legacy = load_module("af3_batch.py")
    base = {
        "name": "x",
        "modelSeeds": [1],
        "sequences": [{"protein": {"id": "A", "sequence": "ACDE"}}],
        "dialect": "alphafold3",
        "version": 1,
    }
    cases = []
    bad_dialect = json.loads(json.dumps(base))
    bad_dialect["dialect"] = "server"
    cases.append(bad_dialect)
    unknown = json.loads(json.dumps(base))
    unknown["memo"] = "not AF3"
    cases.append(unknown)
    bad_id = json.loads(json.dumps(base))
    bad_id["sequences"][0]["protein"]["id"] = "a"
    cases.append(bad_id)
    duplicate = json.loads(json.dumps(base))
    duplicate["sequences"].append({"protein": {"id": "A", "sequence": "ACDE"}})
    cases.append(duplicate)
    for obj in cases:
        check(preferred.validate_fold_job(obj) is not None, "preferred 러너가 bad schema를 허용했다")
        check(legacy.validate_fold_job(obj) is not None, "legacy 러너가 bad schema를 허용했다")


@regression(
    item="security",
    prevents="입력 폴더의 JSON symlink를 따라가 외부 호스트 파일을 컨테이너 staging에 복사하는 버그.",
)
def test_both_runners_reject_symlinked_input_json():
    preferred = load_module("run_af3_batch_improved.py")
    legacy = load_module("af3_batch.py")
    with tempfile.TemporaryDirectory(prefix="af3_input_link_") as td:
        root = Path(td)
        inputs = root / "inputs"
        inputs.mkdir()
        external = root / "outside.json"
        external.write_text(json.dumps({
            "name": "outside",
            "modelSeeds": [1],
            "sequences": [{"protein": {"id": "A", "sequence": "ACDE"}}],
            "dialect": "alphafold3",
            "version": 1,
        }), encoding="utf-8")
        link = inputs / "linked.json"
        link.symlink_to(external)
        job, error = preferred.read_job(link, inputs)
        check(job is None and error is not None, "preferred 러너가 symlink JSON을 허용했다")
        try:
            legacy.read_fold_json(link)
        except (OSError, ValueError):
            pass
        else:
            check(False, "legacy 러너가 symlink JSON을 허용했다")


@regression(
    item="legacy",
    prevents="--stage msa가 msa_store 산출물을 보지 않고 output_dir만 검사해 성공한 MSA를 실패로 기록하는 버그.",
)
def test_legacy_msa_stage_reports_store_completion():
    workspace = Workspace()
    try:
        workspace.write_json("a.json", workspace.monomer("a"))
        proc = run_script(
            "af3_batch.py",
            [
                "--input-dir", str(workspace.input_dir),
                "--output-dir", str(workspace.output_dir),
                "--db-dir", str(workspace.db_dir),
                "--model-dir", str(workspace.model_dir),
                "--docker", "docker",
                "--stage", "msa",
            ],
            workspace,
        )
        check_equal(proc.returncode, 0, "MSA 성공을 실패로 반환했다", proc.stdout[-1800:])
        summary = workspace.root / "vhh_t_work" / "run_summary.csv"
        check(summary.is_file(), "MSA 요약 CSV가 없다")
        rows = list(csv.DictReader(summary.open(encoding="utf-8-sig")))
        check_equal(rows[0]["status"], "완료", "msa_store 산출물을 완료로 세지 않았다")
    finally:
        workspace.cleanup()


@regression(
    item="legacy",
    prevents="여러 MSA 갈래 중 하나만 실패하면 실패 target을 최종 요약에서 빼고 종료코드 0을 반환하는 버그.",
)
def test_legacy_partial_msa_lane_failure_is_sticky_and_counted():
    workspace = Workspace()
    try:
        workspace.write_json("a.json", workspace.monomer("a"))
        workspace.write_json("b.json", workspace.monomer("b"))
        proc = run_script(
            "af3_batch.py",
            [
                "--input-dir", str(workspace.input_dir),
                "--output-dir", str(workspace.output_dir),
                "--db-dir", str(workspace.db_dir),
                "--model-dir", str(workspace.model_dir),
                "--docker", "docker",
                "--stage", "both",
                "--msa-workers", "2",
            ],
            workspace,
            env_extra={"AF3_STUB_FAIL_NAMES": "b"},
        )
        check(proc.returncode != 0, "부분 MSA lane 실패를 성공 처리했다")
        summary = workspace.root / "vhh_t_work" / "run_summary.csv"
        rows = {row["name"]: row for row in csv.DictReader(summary.open(encoding="utf-8-sig"))}
        check_equal(sorted(rows), ["a", "b"], "실패 target이 요약에서 사라졌다")
        check_equal(rows["b"]["status"], "실패", "실패 target을 완료로 표시했다")
    finally:
        workspace.cleanup()


@regression(
    item="input",
    prevents="preferred runner의 얕은 schema 검사로 lowercase/duplicate chain IDs와 unknown top key가 Docker까지 가는 버그.",
)
def test_preferred_runner_matches_cheap_af3_schema_invariants():
    mod = load_module("run_af3_batch_improved.py")
    workspace = Workspace()
    try:
        base = workspace.monomer("x")
        cases = []
        lower = json.loads(json.dumps(base))
        lower["sequences"][0]["protein"]["id"] = "a"
        cases.append(lower)
        duplicate = json.loads(json.dumps(base))
        duplicate["sequences"].append({"protein": {"id": "A", "sequence": "ACDE"}})
        cases.append(duplicate)
        unknown = dict(base)
        unknown["memo"] = "not AF3"
        cases.append(unknown)
        for obj in cases:
            check(mod.validate_fold_job(obj) is not None, "AF3가 거부할 schema를 사전 검증이 허용했다")
    finally:
        workspace.cleanup()


@regression(
    item="check",
    prevents="Docker, DB, 가중치가 없는데 af3_check.sh가 종료코드 0을 반환해 자동화가 설치 완료로 오인하는 버그.",
)
def test_environment_check_exits_nonzero_on_critical_missing_components():
    with tempfile.TemporaryDirectory(prefix="af3_check_missing_") as td:
        root = Path(td)
        env = dict(os.environ)
        env["AF3_DB_DIR"] = str(root / "missing_db")
        env["AF3_MODEL_DIR"] = str(root / "missing_model")
        proc = subprocess.run(
            ["bash", str(SCRIPTS_DIR / "af3_check.sh")],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )
        check(proc.returncode != 0, "핵심 구성요소가 없는데 환경 진단이 성공했다")
        check_in("실패", proc.stdout + proc.stderr, "종합 실패 수를 출력하지 않았다")


@regression(
    item="check",
    prevents="환경 진단 성공 경로가 image 내부 HMMER와 DB/model을 실제로 확인하지 못하는 버그.",
)
def test_environment_check_passes_complete_stub_environment():
    workspace = Workspace()
    try:
        env = dict(os.environ)
        env["PATH"] = str(make_stub_bin(workspace.root)) + os.pathsep + env.get("PATH", "")
        env["AF3_DOCKER"] = "docker"
        env["AF3_DB_DIR"] = str(workspace.db_dir)
        env["AF3_MODEL_DIR"] = str(workspace.model_dir)
        # Workspace의 sparse fixture는 동일 크기의 all-zero 파일이다. 실제 배포 기본값은
        # README에 기록한 공식 af3.bin SHA-256이고, 테스트에서만 fixture hash로 바꾼다.
        env["AF3_MODEL_SHA256"] = "121b85224e4474eb6de00bf17f0acde299569ac8ed4e13220c7b88c01192ad8d"
        proc = subprocess.run(
            ["bash", str(SCRIPTS_DIR / "af3_check.sh")],
            cwd=workspace.root,
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )
        check_equal(proc.returncode, 0, "완전한 stub 환경을 진단이 거부했다", (proc.stdout + proc.stderr)[-2000:])
        check_in("--seq_limit 패치 확인", proc.stdout, "image 내부 patched HMMER를 확인하지 않았다")
        check_in("SHA-256", proc.stdout, "모델 가중치 hash를 확인하지 않았다")
        check_in("지원   --run_data_pipeline", proc.stdout, "boolean data-pipeline 플래그를 놓쳤다")
        check_in("지원   --run_inference", proc.stdout, "boolean inference 플래그를 놓쳤다")
        check_in("(CIF ", proc.stdout, "mmcif_files의 보조 파일을 구조 파일 수에 섞었다")
    finally:
        workspace.cleanup()


@regression(
    item="check",
    prevents="크기만 같은 손상·가짜 af3.bin을 정상 가중치로 인정하는 버그.",
)
def test_environment_check_rejects_same_size_wrong_model_hash():
    workspace = Workspace()
    try:
        env = dict(os.environ)
        env["PATH"] = str(make_stub_bin(workspace.root)) + os.pathsep + env.get("PATH", "")
        env["AF3_DOCKER"] = "docker"
        env["AF3_DB_DIR"] = str(workspace.db_dir)
        env["AF3_MODEL_DIR"] = str(workspace.model_dir)
        proc = subprocess.run(
            ["bash", str(SCRIPTS_DIR / "af3_check.sh")],
            cwd=workspace.root,
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )
        report = proc.stdout + proc.stderr
        check(proc.returncode != 0, "크기만 같은 잘못된 가중치를 허용했다")
        check_in("SHA-256", report, "가중치 hash 불일치 원인을 설명하지 않았다")
    finally:
        workspace.cleanup()


@regression(
    item="check",
    prevents="환경 진단이 특정 과거 GPU의 속도·VRAM을 현재 장비의 보장처럼 출력하거나 DB 크기만으로 완전성을 판정하는 버그.",
)
def test_environment_check_guidance_is_portable_and_evidence_scoped():
    with tempfile.TemporaryDirectory(prefix="af3_check_portable_") as td:
        root = Path(td)
        env = dict(os.environ)
        env["AF3_DB_DIR"] = str(root / "missing_db")
        env["AF3_MODEL_DIR"] = str(root / "missing_model")
        proc = subprocess.run(
            ["bash", str(SCRIPTS_DIR / "af3_check.sh")],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )
        report = proc.stdout + proc.stderr
        check("RTX 5070 Ti" not in report, "특정 과거 GPU 수치를 현재 환경 진단에 노출했다")
        check("32 GB 에 여유" not in report, "현재 GPU와 무관한 32 GB 여유 보장을 출력했다")
        check("0.890" not in report and "0.767" not in report, "과거 처리율을 일반 운영 규칙으로 출력했다")
        check_in("DB 크기만으로 완전성을 판정할 수 없다", report, "DB 완전성의 증거 경계를 설명하지 않았다")
        check_in("핵심 5가지", report, "종합 절의 실제 항목 수가 제목과 맞지 않는다")
