#!/usr/bin/env python3
"""Prepare, stage-2, legacy-runner, and diagnostic safety regressions."""

from __future__ import annotations

import csv
import fcntl
import json
import os
import re
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
    prevents="README/docs 정리 중 상대 링크 대상이 삭제·이동되거나 헤딩 제목이 바뀌어,\n"
             "초보 사용자가 설치 근거 문서를 열 수 없거나 목차 링크가 아무 데도 가지 않는 버그.",
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

    # 같은 문서 안을 가리키는 #앵커도 확인한다. 상대 링크만 보면 헤딩 제목이 바뀐 뒤에도
    # 파일은 그대로 있으므로 통과해 버리고, 목차 링크가 조용히 아무 데도 안 가게 된다.
    heading_pattern = re.compile(r"^#{1,6}[ \t]+(.+?)[ \t]*$", re.MULTILINE)
    fence_pattern = re.compile(r"^(?:```|~~~).*?^(?:```|~~~)[ \t]*$", re.MULTILINE | re.DOTALL)

    def github_slug(heading):
        text = heading.strip().lower()
        text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
        return re.sub(r"[\s]+", "-", text)

    dangling = []
    for document in sorted(REPO_ROOT.rglob("*.md")):
        text = document.read_text(encoding="utf-8")
        body = fence_pattern.sub("", text)
        anchors = set()
        for heading in heading_pattern.findall(body):
            base = github_slug(heading)
            if not base:
                continue
            candidate, suffix = base, 0
            while candidate in anchors:
                suffix += 1
                candidate = "%s-%d" % (base, suffix)
            anchors.add(candidate)
        for match in link_pattern.finditer(body):
            raw = match.group(1).strip()
            if not raw.startswith("#"):
                continue
            anchor = urllib.parse.unquote(raw[1:]).lower()
            if anchor and anchor not in anchors:
                line = body.count("\n", 0, match.start()) + 1
                dangling.append("%s:%d -> %s" % (document.relative_to(REPO_ROOT), line, raw))
    check(not dangling, "가리키는 헤딩이 없는 Markdown 앵커가 있다", "\n".join(dangling[:30]))

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

    # 공개/비공개는 바뀔 수 있으므로 그 상태를 문서에 박아 두지 않는다. 대신 어느
    # 쪽이든 항상 참인 규칙을 요구한다: 무엇을 커밋하면 안 되는지, 그리고 한 번
    # 커밋하면 되돌릴 수 없다는 것.
    check_in("저장소 공개 여부와 관계없이 Git에 추가하지", readme,
             "커밋하면 안 되는 것을 공개 여부와 무관하게 못박지 않았다")
    check_in("이력에 남는다", readme,
             "한 번 커밋하면 되돌릴 수 없다는 점을 말하지 않았다")
    check(
        "비공개 연구 협업 저장소" not in readme,
        "공개 여부를 문서에 박아 두면 전환할 때 거짓이 된다",
    )
    for stale_tone in ("이 저장소는 공개다", "읽어라", " 보라", "마라", "함정", "급히"):
        check(stale_tone not in readme, "README에 과도한 지시체가 남았다", stale_tone)

    quick_summary = readme.partition("## Quick Start")[2].partition("\n---\n")[0]
    check(quick_summary, "README 상단 Quick Start가 없다")
    # Quick Start 하나로 설치 -> 실행 -> 해석 -> 본인 입력까지 끝나야 한다.
    # 결과 읽는 법이 빠지면 사용자는 표를 받고도 무엇을 골라야 할지 모른다.
    for quick_heading in ("### 1. 설치", "### 2. 예제로 배치 실행",
                          "### 3. 결과 읽기", "### 4. 본인 입력 준비"):
        check_in(quick_heading, quick_summary, "Quick Start의 설치·실행·해석 순서가 불완전하다")
    # 해석 절은 글로만 설명하지 말고 실제 산출물을 보여 준다.
    for shown in ("figures/example_complex_plddt.png", "figures/example_complex_pae.png",
                  "figures/view3d_screenshot.png"):
        check_in(shown, quick_summary, "Quick Start가 실제 결과 그림을 보여 주지 않는다")
    # 가중치를 어디서 어떻게 받는지 Quick Start 안에서 답해야 한다. 실험 연구자가
    # --accept-weights-terms 를 붙이기 전에 무엇에 동의하는지 알 수 있어야 한다.
    check_in("WEIGHTS_TERMS_OF_USE.md", quick_summary,
             "Quick Start가 가중치 약관 원문을 링크하지 않는다")
    check_in("설치기가 자동으로 내려받는다", quick_summary,
             "Quick Start가 가중치를 어디서 받는지 답하지 않는다")
    for input_step in (
        "cp examples/vhh_monomer.json quick_in/",
        "--fasta my_sequences.fasta",
        ">sample_01",
        "--dry-run",
        "multi-FASTA의 각 레코드는 서로 독립된 예측 작업",
        "서로 다른 단백질 사슬이 3종 이상",
        "AF3 JSON을 직접 작성",
        "[multi-FASTA 6종](examples/vhh_panel.fasta)",
        "[단일 VHH FASTA](examples/vhh_single.fasta)",
        "[공통 항원 FASTA](examples/antigen.fasta)",
        "[서로 다른 단백질 3사슬 JSON](examples/three_protein_complex.json)",
    ):
        check_in(input_step, quick_summary, "Quick Start의 입력 준비 절차가 불완전하다")
    for output_stem in (
        "panel",
        "homodimer",
        "antigen_panel",
        "partner_dimer",
        "three_chain",
    ):
        check_in(
            f"scripts/af3_collect.py {output_stem}_out",
            quick_summary,
            "입력 유형별 CSV 집계 절차가 빠졌다",
        )
        check_in(
            f"scripts/af3_visualize.py {output_stem}_out",
            quick_summary,
            "입력 유형별 2D 시각화 절차가 빠졌다",
        )
        check_in(
            f"scripts/af3_view3d.py {output_stem}_out",
            quick_summary,
            "입력 유형별 3D 시각화 절차가 빠졌다",
        )
    for benchmark_only in ("31.95초", "5.39초", "5.93배", "189시간"):
        check(benchmark_only not in quick_summary, "Quick Start가 목적 대신 성능 비교를 앞세운다", benchmark_only)

    three_chain = json.loads(
        (REPO_ROOT / "examples" / "three_protein_complex.json").read_text(encoding="utf-8")
    )
    proteins = [entry["protein"] for entry in three_chain["sequences"]]
    check_equal([protein["id"] for protein in proteins], ["A", "B", "C"], "3사슬 JSON 예제의 chain ID가 틀렸다")
    check(all(protein["sequence"] for protein in proteins), "3사슬 JSON 예제에 빈 서열이 있다")

    summary_rows = list(csv.reader(
        (REPO_ROOT / "results_example" / "af3_summary.csv").read_text(
            encoding="utf-8-sig"
        ).splitlines()
    ))
    check_equal(len(summary_rows[0]), 35, "README가 연결한 실측 CSV가 현재 35열 형식이 아니다")
    check(
        all(len(row) == 35 for row in summary_rows),
        "실측 CSV의 행별 열 수가 서로 다르다",
    )
    for stale_claim in (
        "열 이름을 영어로 뽑으려면",
        "추론이 2.25배 느려진 건이다",
        "계면이 파랑/하늘색이면\n상대 배치 신뢰도가 높고",
        "`color bfactor palette alphafold` 가 AlphaFold 공식",
        "측정한 대표 입력 20건",
    ):
        check(stale_claim not in readme, "README에 검토에서 폐기한 설명이 남았다", stale_claim)
    for corrected_claim in (
        "이 옵션은 파일명만 바꾸며 CSV 열 이름은",
        "상대 배치는 사슬 간 PAE",
        "가장 짧은 20건을",
    ):
        check_in(corrected_claim, readme, "README의 결과 해석 또는 성능 조건 설명이 불완전하다")

    with tempfile.TemporaryDirectory(prefix="af3_quick_input_") as td:
        root = Path(td)
        homomer_out = root / "homomer"
        partner_out = root / "partner"
        for outdir, extra in (
            (homomer_out, ["--copies", "2"]),
            (
                partner_out,
                [
                    "--partner-fasta",
                    str(REPO_ROOT / "examples" / "antigen.fasta"),
                    "--partner-copies",
                    "2",
                ],
            ),
        ):
            proc = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS_DIR / "af3_prepare.py"),
                    "--fasta",
                    str(REPO_ROOT / "examples" / "vhh_single.fasta"),
                    "-o",
                    str(outdir),
                    *extra,
                ],
                capture_output=True,
                text=True,
                timeout=120,
            )
            check_equal(proc.returncode, 0, "Quick Start 복합체 예제 생성이 실패했다", proc.stdout + proc.stderr)

        homomer_job = json.loads(next(homomer_out.glob("*.json")).read_text(encoding="utf-8"))
        check_equal(homomer_job["sequences"][0]["protein"]["id"], ["A", "B"], "homomer 예제가 2사슬을 만들지 않았다")
        partner_job = json.loads(next(partner_out.glob("*.json")).read_text(encoding="utf-8"))
        check_equal(partner_job["sequences"][0]["protein"]["id"], "A", "partner 예제의 대상 chain ID가 틀렸다")
        check_equal(partner_job["sequences"][1]["protein"]["id"], ["B", "C"], "partner-copies 예제가 2사슬을 만들지 않았다")


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

    with tempfile.TemporaryDirectory(prefix="af3_installer_disk_") as td:
        root = Path(td)
        fake_bin = root / "bin"
        fake_bin.mkdir()
        add_fake_nvidia_smi(fake_bin)
        fake_df = fake_bin / "df"
        fake_df.write_text(
            "#!/usr/bin/env bash\n"
            "printf 'Filesystem 1-blocks Used Available Use%% Mounted on\\n'\n"
            "printf 'testfs 100 90 10 90%% /\\n'\n",
            encoding="utf-8",
        )
        fake_df.chmod(0o755)
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
                "AF3_DB_DIR": str(root / "nested" / "db"),
                "AF3_PLOT_ENV": str(root / "plot"),
            }
        )
        test_installer, _ = make_isolated_installer(root, "disk-repo")
        disk_proc = subprocess.run(
            ["bash", str(test_installer), "--full", "--accept-weights-terms"],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        disk_report = disk_proc.stdout + disk_proc.stderr
        check(disk_proc.returncode != 0, "용량이 부족한 full 설치를 시작했다")
        check(not marker.exists(), "디스크 용량을 확인하기 전에 sudo를 호출했다")
        check_in("full DB installation requires", disk_report, "용량 부족 원인을 설명하지 않았다")

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


def _extract_shell_function(source: str, name: str) -> str:
    """설치기 원본에서 셸 함수 하나를 그대로 떼어낸다 (사본 실행 검증용)."""
    opener = "%s() {\n" % name
    start = source.find(opener)
    if start < 0:
        raise AssertionError("설치기에서 %s 함수를 찾지 못했다" % name)
    end = source.find("\n}\n", start)
    if end < 0:
        raise AssertionError("%s 함수의 끝을 찾지 못했다" % name)
    return source[start:end + len("\n}\n")]


def _extract_call_line(source: str, name: str) -> str:
    """정의부가 아닌 실제 호출 줄을 그대로 떼어낸다."""
    calls = [
        line
        for line in source.splitlines()
        if name in line and not line.startswith("%s()" % name)
    ]
    if len(calls) != 1:
        raise AssertionError("%s 호출 줄이 정확히 1개가 아니다: %d개" % (name, len(calls)))
    return calls[0]


@regression(
    item="install",
    prevents=(
        "이미지 능력 검증을 `함수 || die` 로 부르면 bash 가 함수 본문 전체에서 errexit 를 "
        "꺼버려, AF3 버전 assert 와 patched HMMER --seq_limit 검사가 실패해도 설치가 "
        "그대로 진행되는 버그."
    ),
)
def test_installer_image_capability_gate_fails_on_every_check():
    installer = SCRIPTS_DIR / "install_af3_ubuntu.sh"
    source = installer.read_text(encoding="utf-8")
    function_text = _extract_shell_function(source, "validate_image_capabilities")
    call_line = _extract_call_line(source, "validate_image_capabilities")

    # 세 검사에 대응하는 stub docker 실패 지점. 하나라도 놓치면 설치가 계속된다.
    stages = {
        "version": "AF3 버전 assert",
        "hmmer": "patched HMMER --seq_limit",
        "gpu": "JAX GPU 백엔드",
    }
    with tempfile.TemporaryDirectory(prefix="af3_capability_") as td:
        root = Path(td)
        driver = root / "drive.sh"
        driver.write_text(
            "set -Eeuo pipefail\n"
            "die() { printf '[error] %s\\n' \"$1\" >&2; exit \"${2:-1}\"; }\n"
            "IMAGE=stub-image\n"
            "AF3_VERSION=3.0.4\n"
            "DOCKER=(stub_docker)\n"
            "stub_docker() {\n"
            "  local text=\"$*\"\n"
            "  case \"$text\" in\n"
            "    *jackhmmer*)\n"
            "      if [[ ${AF3_FAIL_STAGE:-} == hmmer ]]; then echo 'no such flag'; \n"
            "      else echo '  --seq_limit <n> : truncate hits'; fi ;;\n"
            "    *jax*)\n"
            "      [[ ${AF3_FAIL_STAGE:-} == gpu ]] && return 1 ;;\n"
            "    *version*)\n"
            "      [[ ${AF3_FAIL_STAGE:-} == version ]] && return 1 ;;\n"
            "  esac\n"
            "  return 0\n"
            "}\n"
            + function_text
            + call_line
            + "\nprintf 'CAPABILITY_GATE_PASSED\\n'\n",
            encoding="utf-8",
        )

        healthy = subprocess.run(
            ["bash", str(driver)], capture_output=True, text=True, timeout=60
        )
        check_equal(
            healthy.returncode,
            0,
            "정상 이미지를 능력 검증이 거부했다",
            (healthy.stdout + healthy.stderr)[-800:],
        )
        check_in("CAPABILITY_GATE_PASSED", healthy.stdout, "정상 경로가 끝까지 가지 않았다")

        for stage, what in stages.items():
            env = dict(os.environ)
            env["AF3_FAIL_STAGE"] = stage
            broken = subprocess.run(
                ["bash", str(driver)],
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
            )
            report = broken.stdout + broken.stderr
            check(
                broken.returncode != 0,
                "%s 검사가 실패했는데 설치기가 계속 진행했다" % what,
                report[-800:],
            )
            check(
                "CAPABILITY_GATE_PASSED" not in broken.stdout,
                "%s 검사 실패 뒤에도 능력 검증을 통과로 보고했다" % what,
            )


@regression(
    item="legacy",
    prevents=(
        "legacy 러너가 실행 단계와 무관하게 가중치/DB 를 모두 요구해, 가중치가 필요 없는 "
        "CPU 전용 --stage msa 가 core 설치(가중치 미다운로드) 환경에서 시작조차 못 하는 버그."
    ),
)
def test_legacy_preflight_requires_only_what_the_stage_uses():
    # --stage msa 는 --norun_inference 라서 af3.bin 을 읽지 않는다.
    workspace = Workspace()
    try:
        workspace.write_json("a.json", workspace.monomer("a"))
        (workspace.model_dir / "af3.bin").unlink()
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
        report = proc.stdout + proc.stderr
        check(
            "가중치 오류" not in report,
            "MSA 단계가 쓰지도 않는 모델 가중치를 요구했다",
            report[-1200:],
        )
        check_equal(proc.returncode, 0, "가중치 없는 MSA 단계를 실행하지 못했다", report[-1800:])
        check(workspace.stub_calls(), "MSA 단계가 Docker 까지 가지 못했다")
    finally:
        workspace.cleanup()

    # --stage infer 는 --norun_data_pipeline 이라서 DB 를 읽지 않는다
    # (run_alphafold.py 는 --run_data_pipeline 일 때만 db_dir 을 해석한다).
    workspace = Workspace()
    try:
        workspace.write_json("a.json", workspace.monomer("a"))
        shutil.rmtree(workspace.db_dir)
        proc = run_script(
            "af3_batch.py",
            [
                "--input-dir", str(workspace.input_dir),
                "--output-dir", str(workspace.output_dir),
                "--db-dir", str(workspace.db_dir),
                "--model-dir", str(workspace.model_dir),
                "--docker", "docker",
                "--stage", "infer",
            ],
            workspace,
        )
        report = proc.stdout + proc.stderr
        check(
            "DB 오류" not in report,
            "추론 단계가 쓰지도 않는 DB 를 요구했다",
            report[-1200:],
        )
        for call in workspace.stub_calls():
            check(
                not any(str(arg).startswith("--db_dir=") for arg in call.get("argv", [])),
                "DB 없이 도는 추론 단계가 컨테이너에 --db_dir 을 넘겼다",
            )
    finally:
        workspace.cleanup()

    # 두 단계를 모두 도는 경우에는 둘 다 여전히 필수다.
    for stage, missing, expected in (
        ("oneshot", "model", "가중치 오류"),
        ("oneshot", "db", "DB 오류"),
        ("both", "model", "가중치 오류"),
        ("both", "db", "DB 오류"),
    ):
        workspace = Workspace()
        try:
            workspace.write_json("a.json", workspace.monomer("a"))
            if missing == "model":
                (workspace.model_dir / "af3.bin").unlink()
            else:
                shutil.rmtree(workspace.db_dir)
            proc = run_script(
                "af3_batch.py",
                [
                    "--input-dir", str(workspace.input_dir),
                    "--output-dir", str(workspace.output_dir),
                    "--db-dir", str(workspace.db_dir),
                    "--model-dir", str(workspace.model_dir),
                    "--docker", "docker",
                    "--stage", stage,
                ],
                workspace,
            )
            report = proc.stdout + proc.stderr
            check(
                proc.returncode != 0,
                "--stage %s 가 %s 없이 시작했다" % (stage, missing),
                report[-1200:],
            )
            check_in(expected, report, "--stage %s 의 %s 누락 원인을 설명하지 않았다" % (stage, missing))
            check_equal(
                workspace.stub_calls(), [], "--stage %s 사전 검증 실패 뒤 Docker를 실행했다" % stage
            )
        finally:
            workspace.cleanup()


@regression(
    item="docs",
    prevents=(
        "초보자 경로가 (a) 설치 전에 결과물을 볼 방법이 없고 (b) 예제 1건에 시간 안내가 "
        "없어 수십 분 걸리는 full DB MSA 를 멈춘 줄 알고 Ctrl-C 하고 (c) 4GB 슬라이스로 "
        "잰 40.2시간을 305GB full DB 계획에 그대로 쓰는 버그."
    ),
)
def test_quick_start_sets_expectations_before_the_first_long_run():
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    quick_start = readme[readme.index("## Quick Start"): readme.index("## 목차")]

    # (a) 설치 0바이트로 결과물을 보는 경로가 있고, 그 파일이 실제로 커밋돼 있어야 한다.
    for preview in (
        "examples/view3d_example.html",
        "results_example/af3_summary.csv",
        "figures/example_complex_plddt.png",
        "figures/example_complex_pae.png",
        "figures/example_summary_6targets.png",
    ):
        check_in(preview, quick_start, "설치 전 미리보기 경로가 Quick Start 에 없다")
        check(
            (REPO_ROOT / preview).is_file(),
            "Quick Start 가 가리키는 미리보기 파일이 저장소에 없다",
            preview,
        )

    # (b) 첫 실행에 걸리는 시간과 '멈춘 게 아니다' 안내가 있어야 한다.
    # 특정 숫자를 박아 두지 않는다. 다시 측정하면 값은 바뀌고, 그때 이 테스트가
    # '틀렸다'고 말하면 안 된다. 지켜야 하는 것은 값이 아니라 계약이다:
    # 빠른 쪽은 초 단위로, 느린 쪽은 분 단위로 기대치를 적어 두어야 한다.
    quick_seconds = re.search(r"\*\*[0-9]+(?:\.[0-9]+)?초\*\*", quick_start)
    slow_minutes = re.search(r"\*\*[0-9]+(?:\.[0-9]+)?분(?: 이상)?\*\*", quick_start)
    check(quick_seconds, "overlay 경로의 소요 시간을 초 단위로 적지 않았다")
    check(slow_minutes, "full DB 경로의 소요 시간을 분 단위로 적지 않았다")
    check_in("멈춘 것처럼 보여도 정상", quick_start,
             "오래 걸리는 단계에서 사용자가 Ctrl-C 하지 않도록 안내하지 않았다")

    # overlay 를 먼저, full DB 를 fallback 으로 주는 순서가 명령에 드러나야 한다.
    check_in(
        "--db-dir ~/public_databases_reduced --db-dir ~/public_databases_full",
        quick_start,
        "Quick Start 예제가 overlay 우선 ordered root 로 실행되지 않는다",
    )
    check_in(
        "overlay는 full DB를 **대체하지 않는다.**",
        quick_start,
        "overlay 가 다운로드를 줄여준다는 오해를 막지 않는다",
    )

    # (c) 벤치마크 표의 '전체 DB 급' 이 무엇으로 잰 값인지 표 옆에 있어야 한다.
    benchmark = (REPO_ROOT / "docs" / "benchmark_report.md").read_text(encoding="utf-8")
    for caveat in ("4GB 슬라이스(합계 16GB)", "305GB", "40.2시간을 그대로 쓰면 안 된다"):
        check_in(caveat, benchmark, "40.2시간의 측정 조건 경계를 표 옆에 적지 않았다")


@regression(
    item="docs",
    prevents="재주입을 늘려도 docs/testing_notes.md 의 건수와 목록이 그대로 남아,\n"
             "문서가 실제보다 적은 검증을 했다고 말하는 버그. 조용히 낡는다.",
)
def test_testing_notes_matches_the_registered_mutations():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "af3_injections", REPO_ROOT / "tests" / "verify_tests_catch_bugs.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    injections = module.INJECTIONS
    check(injections, "재주입 목록이 비었다")

    notes = (REPO_ROOT / "docs" / "testing_notes.md").read_text(encoding="utf-8")
    total = len(injections)
    check_in(
        "재주입 %d건" % total,
        notes,
        "docs/testing_notes.md 의 재주입 건수가 실제와 다르다 (실제 %d건)" % total,
    )
    check_in(
        "mutation %d건" % total,
        notes,
        "release gate 설명의 mutation 건수가 실제와 다르다 (실제 %d건)" % total,
    )

    missing = [item["name"] for item in injections if item["name"] not in notes]
    check(
        not missing,
        "docs/testing_notes.md 에 빠진 재주입이 있다",
        "\n".join(missing[:10]),
    )


@regression(
    item="beginner",
    prevents="legacy 러너(af3run.sh 경로)만 --user 를 빠뜨려 결과가 root 소유로 남는 버그.",
)
def test_legacy_runner_writes_results_as_the_invoking_user():
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
        check_equal(proc.returncode, 0, "legacy MSA 실행이 실패했다", proc.stdout[-1500:])
        runs = [c for c in workspace.stub_calls() if c.get("call") == "run"]
        check(runs, "docker 를 실행하지 않았다")
        expected = f"{os.getuid()}:{os.getgid()}"
        for call in runs:
            check_equal(call.get("user"), expected, "legacy 러너가 --user 를 넘기지 않았다")
    finally:
        workspace.cleanup()


@regression(
    item="security",
    prevents="af3run.sh 가 AF3_MSA_WORKERS/NCPU 를 검증 없이 $((...)) 에 넣어,\n"
             "값 안의 명령 치환이 그대로 실행되는 버그.\n"
             "AF3_MSA_WORKERS='CORES[$(touch /tmp/x)]' 로 재현했다.",
)
def test_wrapper_rejects_non_numeric_thread_settings():
    import shutil

    script = SCRIPTS_DIR / "af3run.sh"
    with tempfile.TemporaryDirectory(prefix="af3_inject_") as td:
        root = Path(td)
        (root / "probe_in").mkdir()
        (root / "probe_in" / "a.json").write_text(json.dumps({
            "name": "a", "modelSeeds": [1],
            "sequences": [{"protein": {"id": "A", "sequence": "ACDE"}}],
            "dialect": "alphafold3", "version": 1,
        }), encoding="utf-8")
        marker = root / "pwned"

        for payload in (f"CORES[$(touch {marker})]", f"NCPU[$(touch {marker})]"):
            for var in ("AF3_MSA_WORKERS", "AF3_MSA_NCPU"):
                env = dict(os.environ)
                env[var] = payload
                env["PATH"] = f"{make_stub_bin(root)}{os.pathsep}{env.get('PATH', '')}"
                proc = subprocess.run(
                    ["bash", str(script), "probe"],
                    cwd=root, env=env, capture_output=True, text=True, timeout=120,
                )
                check(
                    not marker.exists(),
                    f"{var} 의 명령 치환이 실행됐다",
                    f"payload={payload}\n{proc.stdout[-400:]}",
                )
                check(
                    proc.returncode != 0,
                    f"{var} 에 숫자가 아닌 값을 주었는데 실행을 계속했다",
                    f"payload={payload}",
                )
                check_in(
                    var,
                    proc.stdout + proc.stderr,
                    f"{var} 가 잘못됐다는 것을 이름으로 알려주지 않는다",
                )


@regression(
    item="security",
    prevents="af3run.sh 의 작업 이름이 ../ 나 슬래시를 허용해,\n"
             "입력·결과·로그가 작업 폴더 밖으로 나가는 버그.",
)
def test_wrapper_rejects_names_that_escape_the_work_directory():
    script = SCRIPTS_DIR / "af3run.sh"
    with tempfile.TemporaryDirectory(prefix="af3_name_") as td:
        root = Path(td)
        work = root / "work"
        work.mkdir()
        job = json.dumps({
            "name": "a", "modelSeeds": [1],
            "sequences": [{"protein": {"id": "A", "sequence": "ACDE"}}],
            "dialect": "alphafold3", "version": 1,
        })
        for bad in ("../escape", "a/b", "..", "/abs"):
            # 폴더가 없어서 멈추는 것으로는 검증이 안 된다. 이탈 경로에 입력 폴더를
            # 실제로 만들어 두고, 그래도 거부하는지 본다.
            # (절대경로는 만들 수 없는 위치라 폴더 없이 이름 검증만 본다.)
            target = (work / f"{bad}_in").resolve()
            try:
                target.mkdir(parents=True, exist_ok=True)
                (target / "a.json").write_text(job, encoding="utf-8")
            except OSError:
                pass
            proc = subprocess.run(
                ["bash", str(script), bad],
                cwd=work, capture_output=True, text=True, timeout=60,
            )
            check(
                proc.returncode != 0,
                "작업 폴더를 벗어나는 이름을 받아들였다",
                f"이름={bad!r} 종료코드={proc.returncode}\n{proc.stdout[-300:]}",
            )
            check_in(
                "작업 이름",
                proc.stdout + proc.stderr,
                f"이름이 왜 거부됐는지 알려주지 않는다 (이름={bad!r})",
            )


@regression(
    item="check",
    prevents="af3_check.sh 가 nvidia-smi 바이너리 존재만 확인해,\n"
             "드라이버가 고장 나 실행이 실패해도 '모두 통과' 로 끝나는 버그.\n"
             "파이프 뒤의 sed 가 성공하면 producer 의 종료코드가 묻힌다.",
)
def test_environment_check_fails_when_nvidia_smi_cannot_run():
    workspace = Workspace()
    try:
        bin_dir = make_stub_bin(workspace.root)
        # 존재하지만 항상 실패하는 nvidia-smi (드라이버 고장). 나머지는 완전한 stub
        # 환경이므로, 진단이 실패한다면 원인은 이것 하나뿐이다.
        broken = bin_dir / "nvidia-smi"
        broken.write_text(
            "#!/bin/sh\necho 'could not communicate with the NVIDIA driver' >&2\nexit 9\n",
            encoding="utf-8",
        )
        broken.chmod(0o755)

        env = dict(os.environ)
        env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
        env["AF3_DOCKER"] = "docker"
        env["AF3_DB_DIR"] = str(workspace.db_dir)
        env["AF3_MODEL_DIR"] = str(workspace.model_dir)
        env["AF3_MODEL_SHA256"] = (
            "121b85224e4474eb6de00bf17f0acde299569ac8ed4e13220c7b88c01192ad8d")
        proc = subprocess.run(
            ["bash", str(SCRIPTS_DIR / "af3_check.sh")],
            cwd=workspace.root, env=env, capture_output=True, text=True, timeout=300,
        )
        check(
            proc.returncode != 0,
            "nvidia-smi 가 실패하는데 환경 점검이 통과했다",
            (proc.stdout + proc.stderr)[-600:],
        )
        check_in(
            "실행에 실패했다",
            proc.stdout,
            "드라이버 실행 실패를 이유로 들지 않았다",
        )
    finally:
        workspace.cleanup()


@regression(
    item="docs",
    prevents="AF3 로 만든 결과물을 저장소에 함께 배포하면서 Output Terms 고지와\n"
             "원본 출력에 가한 수정 내역을 붙이지 않는 버그.\n"
             "약관 5항이 눈에 띄는 고지를 요구한다.",
)
def test_distributed_af3_output_carries_its_notice():
    notice = REPO_ROOT / "OUTPUT_NOTICE.md"
    check(notice.is_file(), "OUTPUT_NOTICE.md 가 없다")
    text = notice.read_text(encoding="utf-8")
    check_in("OUTPUT_TERMS_OF_USE.md", text, "약관 원문을 가리키지 않는다")
    check_in("수정", text, "원본 출력에 가한 수정 내역이 없다")

    # 산출물 폴더와 함께 움직이도록 폴더 안에도 둔다.
    for folder in ("results_example", "figures"):
        side = REPO_ROOT / folder / "OUTPUT_NOTICE.txt"
        check(side.is_file(), f"{folder}/ 에 고지가 없다")
        check_in("Output Terms", side.read_text(encoding="utf-8"),
                 f"{folder}/ 고지가 약관을 언급하지 않는다")

    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    check_in("OUTPUT_NOTICE.md", readme, "README 가 고지를 연결하지 않는다")


@regression(
    item="check",
    prevents="컨테이너가 GPU 를 못 봐도 환경 점검이 통과하는 버그.\n"
             "nvidia-smi 가 호스트에서 도는 것과 컨테이너 안에서 GPU 를 쓰는 것은 다르다.\n"
             "docker run --gpus 의 종료코드가 파이프 뒤 head/sed 에 묻힌다.",
)
def test_environment_check_fails_when_container_cannot_see_the_gpu():
    workspace = Workspace()
    try:
        env = dict(os.environ)
        env["PATH"] = str(make_stub_bin(workspace.root)) + os.pathsep + env.get("PATH", "")
        env["AF3_DOCKER"] = "docker"
        env["AF3_DB_DIR"] = str(workspace.db_dir)
        env["AF3_MODEL_DIR"] = str(workspace.model_dir)
        env["AF3_MODEL_SHA256"] = (
            "121b85224e4474eb6de00bf17f0acde299569ac8ed4e13220c7b88c01192ad8d")
        env["AF3_STUB_GPU_FAIL"] = "1"
        proc = subprocess.run(
            ["bash", str(SCRIPTS_DIR / "af3_check.sh")],
            cwd=workspace.root, env=env, capture_output=True, text=True, timeout=300,
        )
        check(
            proc.returncode != 0,
            "컨테이너가 GPU 를 못 보는데 환경 점검이 통과했다",
            (proc.stdout + proc.stderr)[-700:],
        )
        check_in(
            "컨테이너",
            proc.stdout,
            "컨테이너에서 GPU 를 못 쓴다는 것을 이유로 들지 않았다",
        )
    finally:
        workspace.cleanup()


@regression(
    item="check",
    prevents="컨테이너가 GPU 장치를 보는 것과 JAX 가 그 위에서 도는 것은 다르다.\n"
             "드라이버/CUDA 조합이 어긋나면 nvidia-smi 는 되는데 JAX 가 CPU 로 떨어지고,\n"
             "그 상태로 배치를 돌리면 추론이 몇십 배 느려지거나 그대로 죽는다.",
)
def test_environment_check_fails_when_jax_does_not_reach_the_gpu():
    for mode, label in (("AF3_STUB_JAX_CPU", "CPU 로 떨어진 경우"),
                        ("AF3_STUB_JAX_FAIL", "초기화가 실패한 경우")):
        workspace = Workspace()
        try:
            env = dict(os.environ)
            env["PATH"] = str(make_stub_bin(workspace.root)) + os.pathsep + env.get("PATH", "")
            env["AF3_DOCKER"] = "docker"
            env["AF3_DB_DIR"] = str(workspace.db_dir)
            env["AF3_MODEL_DIR"] = str(workspace.model_dir)
            env["AF3_MODEL_SHA256"] = (
                "121b85224e4474eb6de00bf17f0acde299569ac8ed4e13220c7b88c01192ad8d")
            env[mode] = "1"
            proc = subprocess.run(
                ["bash", str(SCRIPTS_DIR / "af3_check.sh")],
                cwd=workspace.root, env=env, capture_output=True, text=True, timeout=300,
            )
            check(
                proc.returncode != 0,
                f"JAX 가 GPU 를 못 쓰는데 환경 점검이 통과했다 ({label})",
                (proc.stdout + proc.stderr)[-500:],
            )
            check_in("JAX", proc.stdout, f"JAX 를 이유로 들지 않았다 ({label})")
        finally:
            workspace.cleanup()
