#!/usr/bin/env python3
"""외부 패키지 없이 쓰는 최소 테스트 도구. Python 3 표준 기능만 사용한다.

pytest 를 쓰지 않는 이유: 이 저장소는 실험 기반 초보 연구자가 쓰는 것이고,
스크립트 본체도 표준 라이브러리만 쓴다. 테스트를 돌리기 위해 pip install 을
요구하면 "테스트가 안 돌아간다" 는 문의가 늘어난다.
pytest 가 이미 있는 환경에서는 pytest 로도 수집된다 (test_* 함수 이름 규약).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import traceback
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
# 기본은 저장소의 scripts/. 역검증(verify_tests_catch_bugs.py)이 버그를 재주입한
# 임시 사본을 대상으로 돌리려고 이 환경변수로 갈아끼운다. 원본은 건드리지 않는다.
SCRIPTS_DIR = Path(os.environ.get("AF3_TESTS_SCRIPTS_DIR") or (REPO_ROOT / "scripts"))
FAKE_DOCKER = TESTS_DIR / "fake_docker.py"


class Failure(AssertionError):
    """테스트 실패. 메시지는 초보자가 읽어도 무엇이 틀렸는지 알 수 있게 쓴다."""


def check(condition: bool, what: str, detail: str = "") -> None:
    if not condition:
        message = what if not detail else f"{what}\n      실제: {detail}"
        raise Failure(message)


def check_equal(actual, expected, what: str, detail: str = "") -> None:
    if actual != expected:
        message = f"{what}\n      기대: {expected!r}\n      실제: {actual!r}"
        if detail:
            message += f"\n      참고: {detail}"
        raise Failure(message)


def check_in(needle: str, haystack: str, what: str) -> None:
    if needle not in haystack:
        tail = "\n".join(haystack.splitlines()[-15:])
        raise Failure(f"{what}\n      찾던 문구: {needle!r}\n      출력 끝부분:\n{textwrap.indent(tail, '        ')}")


def check_not_in(needle: str, haystack: str, what: str) -> None:
    if needle in haystack:
        raise Failure(f"{what}\n      나오면 안 되는 문구: {needle!r}")


# ---------------------------------------------------------------------------
# 임시 작업 공간
# ---------------------------------------------------------------------------
class Workspace:
    """<name>_in / <name>_out 관례를 그대로 재현한 임시 작업 폴더."""

    def __init__(self, name: str = "vhh_t"):
        self.root = Path(tempfile.mkdtemp(prefix="af3_regress_"))
        self.input_dir = self.root / f"{name}_in"
        self.output_dir = self.root / f"{name}_out"
        self.input_dir.mkdir()
        self.output_dir.mkdir()
        # 러너가 존재를 확인하는 폴더들 (내용은 스텁이 쓰지 않으므로 비어도 된다)
        self.db_dir = self.root / "public_databases"
        self.model_dir = self.root / "af3_models"
        self.cache_dir = self.root / "af3_cache"
        for path in (self.db_dir, self.model_dir, self.cache_dir):
            path.mkdir()
        fasta = ">stub\nACDEFGHIKLMN\n"
        for name in (
            "bfd-first_non_consensus_sequences.fasta",
            "mgy_clusters_2022_05.fa",
            "uniref90_2022_05.fa",
            "uniprot_all_2021_04.fa",
            "pdb_seqres_2022_09_28.fasta",
            "nt_rna_2023_02_23_clust_seq_id_90_cov_80_rep_seq.fasta",
            "rfam_14_9_clust_seq_id_90_cov_80_rep_seq.fasta",
            "rnacentral_active_seq_id_90_cov_80_linclust.fasta",
        ):
            (self.db_dir / name).write_text(fasta, encoding="utf-8")
        mmcif = self.db_dir / "mmcif_files"
        mmcif.mkdir()
        (mmcif / "stub.cif").write_text("data_stub\n", encoding="utf-8")
        # Sparse file: exact pinned-model size without consuming 1.15 GB of blocks.
        with (self.model_dir / "af3.bin").open("wb") as handle:
            handle.truncate(1_146_811_260)
        self.stub_log = self.root / "stub_calls.jsonl"

    def write_json(self, filename: str, obj, *, raw_text: str | None = None) -> Path:
        """입력 JSON 을 쓴다. raw_text 를 주면 그 바이트를 그대로 쓴다(깨진 JSON 재현)."""
        import json as _json

        path = self.input_dir / filename
        if raw_text is not None:
            path.write_text(raw_text, encoding="utf-8")
        else:
            path.write_text(_json.dumps(obj, ensure_ascii=False), encoding="utf-8")
        return path

    def write_bytes(self, filename: str, data: bytes) -> Path:
        path = self.input_dir / filename
        path.write_bytes(data)
        return path

    def monomer(self, name: str, sequence: str = "QVQLVESGGGLVQAGGSLRLSCAAS") -> dict:
        """VHH 단량체 입력 JSON 한 건 (AF3 스키마 최소형)."""
        return {
            "name": name,
            "modelSeeds": [1],
            "sequences": [{"protein": {"id": "A", "sequence": sequence}}],
            "dialect": "alphafold3",
            "version": 1,
        }

    def result_dir(self, output_name: str) -> Path:
        return self.output_dir / output_name

    def make_result(self, output_name: str, *, stage: str = "full") -> Path:
        """AF3 결과 폴더를 손으로 만든다.

        stage="data"    -> _data.json 만 (추론 전에 끊긴 상태)
        stage="partial" -> _data.json + ranking_scores.csv 만 (일부만 있는 상태)
        stage="full"    -> 3종 산출물 모두
        stage="zero"    -> 3종이 있지만 크기가 0 (디스크 꽉 찬 상태 재현)
        """
        import json as _json

        result = self.output_dir / output_name
        result.mkdir(parents=True, exist_ok=True)
        (result / f"{output_name}_data.json").write_text("{}\n", encoding="utf-8")
        if stage == "data":
            return result
        empty = stage == "zero"
        (result / f"{output_name}_ranking_scores.csv").write_text(
            "" if empty else "seed,sample,ranking_score\n1,0,0.83\n", encoding="utf-8"
        )
        if stage == "partial":
            return result
        (result / f"{output_name}_model.cif").write_text(
            "" if empty else "data_x\n", encoding="utf-8"
        )
        (result / f"{output_name}_summary_confidences.json").write_text(
            ""
            if empty
            else _json.dumps({"ranking_score": 0.83, "ptm": 0.81, "iptm": None})
            + "\n",
            encoding="utf-8",
        )
        (result / f"{output_name}_confidences.json").write_text(
            "" if empty else _json.dumps({"atom_plddts": [80.0, 90.0]}) + "\n",
            encoding="utf-8",
        )
        return result

    def stub_calls(self) -> list[dict]:
        import json as _json

        if not self.stub_log.exists():
            return []
        return [
            _json.loads(line)
            for line in self.stub_log.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def cleanup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


# ---------------------------------------------------------------------------
# docker 가로채기
# ---------------------------------------------------------------------------
def make_stub_bin(root: Path) -> Path:
    """PATH 앞에 끼울 가짜 sudo/docker 를 만든다.

    러너가 DOCKER_COMMAND = ("sudo", "docker") 를 모듈 상수로 갖고 있어
    옵션으로 바꿀 수 없다. 그래서 PATH 를 갈아 sudo 와 docker 를 모두 가로챈다.
    sudo 는 인자를 그대로 실행하기만 한다(암호를 묻지 않는다).
    """
    bin_dir = root / "stub_bin"
    bin_dir.mkdir(exist_ok=True)
    sudo = bin_dir / "sudo"
    sudo.write_text(
        '#!/bin/sh\n'
        'if [ "${AF3_TEST_ALLOW_SUDO:-0}" != "1" ]; then exit 97; fi\n'
        'if [ "$1" = "-n" ]; then shift; fi\nexec "$@"\n',
        encoding="utf-8",
    )
    sudo.chmod(0o755)
    docker = bin_dir / "docker"
    docker.write_text(
        f'#!/bin/sh\nexec "{sys.executable}" "{FAKE_DOCKER}" "$@"\n', encoding="utf-8"
    )
    docker.chmod(0o755)
    nvidia = bin_dir / "nvidia-smi"
    nvidia.write_text(
        "#!/bin/sh\n"
        "echo '0, Stub NVIDIA GPU, 999.0, 16384 MiB, 0 MiB, 16384 MiB, 0 %, 9.0'\n",
        encoding="utf-8",
    )
    nvidia.chmod(0o755)
    return bin_dir


def run_script(
    script: str,
    args: list[str],
    workspace: Workspace,
    *,
    env_extra: dict | None = None,
    stdin_text: str = "",
    timeout: int = 120,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess:
    """scripts/<script> 를 가짜 docker 가 놓인 PATH 로 실행한다."""
    bin_dir = make_stub_bin(workspace.root)
    env = dict(os.environ)
    for key in list(env):
        if key.startswith("AF3_STUB_") or key.startswith("AF3_TEST_"):
            env.pop(key, None)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env["AF3_STUB_LOG"] = str(workspace.stub_log)
    env["PYTHONIOENCODING"] = "utf-8"
    env.setdefault("LC_ALL", "C.UTF-8")
    if env_extra:
        env.update({k: str(v) for k, v in env_extra.items()})
    return subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / script), *args],
        cwd=str(cwd or workspace.root),
        env=env,
        input=stdin_text,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def default_args(workspace: Workspace, *extra: str) -> list[str]:
    """개선 러너의 표준 인자 묶음 (경로를 임시 폴더로 돌린다)."""
    return [
        "--input-dir",
        str(workspace.input_dir),
        "--output-dir",
        str(workspace.output_dir),
        "--db-dir",
        str(workspace.db_dir),
        "--model-dir",
        str(workspace.model_dir),
        "--cache-dir",
        str(workspace.cache_dir),
        *extra,
    ]


def load_module(script_name: str, alias: str | None = None):
    """scripts/ 아래 스크립트를 모듈로 불러온다 (함수 단위 검증용)."""
    import importlib.util

    path = SCRIPTS_DIR / script_name
    name = alias or ("mod_" + script_name.replace(".", "_"))
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise Failure(f"{script_name} 을 모듈로 불러올 수 없다: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# 테스트 등록
# ---------------------------------------------------------------------------
REGISTRY: list[dict] = []


def regression(*, item: str, prevents: str, expect_fail_on_current: bool = False):
    """테스트 등록 데코레이터.

    item     -- 과제 항목 번호/이름 (보고서 대조용)
    prevents -- 이 테스트가 막는 실제 버그 한 줄. 이 문구가 이 모음의 핵심 자산이다.
    expect_fail_on_current -- 저장소 현재 버전에서 실패가 예상되는 항목.
                              (다른 트랙이 아직 고치는 중인 동작)
    """

    def wrap(func):
        REGISTRY.append(
            {
                "name": func.__name__,
                "item": item,
                "prevents": prevents,
                "func": func,
                "expect_fail": expect_fail_on_current,
                "module": func.__module__,
            }
        )
        return func

    return wrap


def format_failure(exc: BaseException) -> str:
    if isinstance(exc, Failure):
        return str(exc)
    return "".join(traceback.format_exception_only(type(exc), exc)).strip()


class capture_output:
    """테스트가 스크립트를 모듈로 불러 부를 때 나오는 출력을 잠시 가둔다.

    러너와 집계 스크립트는 사람에게 보여줄 안내를 stdout 으로 쏟는다. 그것이
    테스트 결과 목록에 섞이면 초보자가 "통과했는지 실패했는지" 를 못 읽는다.
    실패했을 때만 갇힌 출력을 꺼내 보여준다.
    """

    def __init__(self):
        self.text = ""
        self._buffer = None
        self._saved = None

    def __enter__(self):
        import io

        self._buffer = io.StringIO()
        self._saved = (sys.stdout, sys.stderr)
        sys.stdout = self._buffer
        sys.stderr = self._buffer
        return self

    def __exit__(self, exc_type, exc, tb):
        sys.stdout, sys.stderr = self._saved
        self.text = self._buffer.getvalue()
        return False
