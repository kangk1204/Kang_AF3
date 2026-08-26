#!/usr/bin/env python3
"""초보자도 안전하게 쓸 수 있는 AlphaFold 3 배치 실행기.

기본 동작은 남은 JSON을 임시 staging 폴더에 모아 컨테이너 한 번으로
처리합니다. 완료 여부는 폴더 존재가 아니라 AF3 최종 산출물로 판정하며,
중단된 결과는 ``.af3_incomplete`` 아래에 작업별로 제한 보존한 뒤 재시도합니다.

아무 옵션 없이 실행하면 현재 경로와 모드를 설명한 뒤 실제 실행 여부를
확인합니다. 설명만 보려면 ``--guide``, 상태만 보려면 ``--audit``을 사용하세요.

예시:
  python3 run_af3_batch_improved.py
  python3 run_af3_batch_improved.py --guide
  python3 run_af3_batch_improved.py --audit
  python3 run_af3_batch_improved.py --mode data
  python3 run_af3_batch_improved.py --mode inference --input-dir prepared_jsons
  python3 run_af3_batch_improved.py --cleanup
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import shlex
import socket
import stat
import string
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator, Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from af3_db import verify_database_roots, verify_model_dir  # noqa: E402


# =========================================================
# USER CONFIG (CLI 옵션을 생략했을 때 쓰는 기본값)
# =========================================================
INPUT_DIR_NAME = "vhh_001_in"
OUTPUT_DIR_NAME = "vhh_001_out"
RUN_MODE = "full"  # full | data | inference
USE_SINGLE_RUN = True

AF3_IMAGE = os.environ.get("AF3_IMAGE", "alphafold3")
DB_DIR = os.environ.get("AF3_DB_DIR", "~/public_databases_full")
MODEL_DIR = os.environ.get("AF3_MODEL_DIR", "~/af3_models")
CACHE_DIR = os.environ.get("AF3_CACHE_DIR", "~/af3_cache")
HELP_TIMEOUT_SECONDS = 300
QUARANTINE_KEEP_PER_JOB = 1
STALE_STAGE_HOURS = 24
# =========================================================


SCRIPT_ID = "run_af3_batch_improved"
STATE_FORMAT_VERSION = 1
STAGE_PREFIX = ".af3_pending_"
STAGE_MARKER_NAME = ".af3_stage_marker"
QUARANTINE_DIR_NAME = ".af3_incomplete"
QUARANTINE_MARKER_NAME = ".af3_quarantine_marker"
MAX_INPUT_JSON_BYTES = 512 * 1024 * 1024
FINAL_REQUIRED_SUFFIXES = (
    ("_ranking_scores.csv",),
    ("_model.cif", "_model.cif.zst"),
    ("_summary_confidences.json",),
)
SIDECAR_KEYS = frozenset(
    {"mmcifPath", "unpairedMsaPath", "pairedMsaPath", "userCCDPath"}
)
TOP_LEVEL_KEYS = frozenset(
    {
        "name",
        "modelSeeds",
        "sequences",
        "dialect",
        "version",
        "bondedAtomPairs",
        "userCCD",
        "userCCDPath",
    }
)
KNOWN_FLAGS = frozenset(
    {
        "json_path",
        "input_dir",
        "output_dir",
        "model_dir",
        "db_dir",
        "run_data_pipeline",
        "run_inference",
        "jax_compilation_cache_dir",
    }
)
INFRASTRUCTURE_EXIT_CODES = frozenset({125, 126, 127})
# 컨테이너 이름 규칙: <접두사><러너 PID>_<순번>.
# `docker run` 으로 띄운 컨테이너는 러너가 죽어도 데몬이 계속 돌린다 (Ctrl-C, SIGTERM,
# SSH 끊김 모두에서 확인). 이름에 PID 를 박아 두어야 나중에 "이 컨테이너를 띄운 실행이
# 이미 끝났는가" 를 판정하고 --audit/--cleanup 이 안전하게 정리할 수 있다.
CONTAINER_PREFIX = "af3run_"


def cache_dir_problem(cache_dir: Path) -> str | None:
    """JAX 캐시를 컨테이너가 쓸 수 있는지 본다. 못 쓰면 사람이 읽을 이유를 돌려준다.

    2026-08-25 이전 러너는 컨테이너를 root 로 돌려서 캐시 하위 폴더가 root 소유로
    남았다. 지금은 --user 로 돌리므로 그 폴더에 못 쓰고, AF3 는 죽지는 않지만
    PERMISSION_DENIED 를 수천 줄 뱉는다. 상위 폴더만 봐서는 안 잡힌다 - 문제는
    항상 하위(xla_gpu_per_fusion_autotune_cache_dir)에 있다.
    """
    if not cache_dir.exists():
        return None
    targets = [cache_dir]
    try:
        targets.extend(child for child in cache_dir.iterdir() if child.is_dir())
    except OSError as exc:
        return f"JAX 캐시 폴더를 읽을 수 없다: {cache_dir} ({exc})"
    for path in targets:
        if not os.access(path, os.W_OK | os.X_OK):
            return (
                f"JAX 캐시에 쓸 수 없다: {path}\n"
                f"       예전 러너가 root 로 만든 캐시가 남아 있으면 이렇게 된다.\n"
                f"       고치려면: sudo chown -R $USER:$USER {cache_dir}\n"
                f"       또는 --cache-dir 로 다른 폴더를 쓰거나 --no-cache 로 끈다."
            )
    return None


def container_user() -> str | None:
    """컨테이너 안 프로세스를 호출한 사용자의 uid:gid 로 돌린다. POSIX 가 아니면 None."""
    if not hasattr(os, "getuid"):
        return None
    return f"{os.getuid()}:{os.getgid()}"
CONTAINER_NAME_RE = re.compile(
    r"^" + CONTAINER_PREFIX + r"(?P<pid>[0-9]+)_[0-9]+$"
)


def probe_container_name() -> str:
    """이미지 확인용 컨테이너 이름.

    --rm 을 붙여도 시간초과로 docker 클라이언트가 죽으면 컨테이너는 Created 로
    남는다. 이름이 없으면 --audit 이 찾지도, --cleanup 이 지우지도 못한다.
    실행용과 같은 규칙(af3run_<pid>_<n>)을 쓰되 순번은 0 자리를 피해 999 로 둔다.
    """
    return "%s%d_999" % (CONTAINER_PREFIX, os.getpid())
CONTAINER_INPUT = "/af3/in"
CONTAINER_OUTPUT = "/af3/out"
CONTAINER_MODELS = "/af3/models"
CONTAINER_CACHE = "/af3/cache"


@dataclass(frozen=True)
class Sidecar:
    source: Path
    relative_path: Path


@dataclass(frozen=True)
class Job:
    json_file: Path
    output_name: str
    raw_name: str
    sidecars: tuple[Sidecar, ...]


@dataclass(frozen=True)
class StageStatus:
    path: Path
    removable: bool
    reason: str


def sanitised_name(name: str) -> str:
    """현재 공식 AF3 ``Input.sanitised_name``과 같은 규칙을 적용한다."""
    allowed = set(string.ascii_letters + string.digits + "_-.")
    return "".join(char for char in name.replace(" ", "_") if char in allowed)


def is_safe_output_name(name: str) -> bool:
    """결과 이름이 출력 폴더의 단일 일반 하위 경로인지 확인한다."""
    return (
        bool(name)
        and name not in {".", "..", ".run_af3_batch.lock"}
        and not name.startswith(".")
        and name[0] not in "=+-@"
        and Path(name).name == name
    )


def _valid_seed(value: object) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value <= 2**32 - 1
    )


def validate_fold_job(obj: object) -> str | None:
    """Validate the AF3 job fields that can otherwise abort a whole directory run."""

    if not isinstance(obj, dict):
        return (
            "최상위가 객체(dict)가 아닙니다. AlphaFold Server의 list 형식은 "
            "이 재개형 배치 스크립트에서 지원하지 않습니다"
        )
    unknown_top = sorted(set(obj) - TOP_LEVEL_KEYS)
    if unknown_top:
        return "AF3 가 모르는 최상위 키가 있습니다: " + ", ".join(unknown_top)
    if obj.get("dialect") != "alphafold3":
        return "dialect 는 'alphafold3' 이어야 합니다"
    version = obj.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version not in {1, 2, 3, 4}:
        return "version 은 1, 2, 3, 4 중 하나여야 합니다"
    seeds = obj.get("modelSeeds")
    if not isinstance(seeds, list) or not seeds or not all(_valid_seed(seed) for seed in seeds):
        return "modelSeeds 는 비어 있지 않은 32-bit unsigned integer 목록이어야 합니다"
    sequences = obj.get("sequences")
    if not isinstance(sequences, list) or not sequences:
        return "sequences 는 비어 있지 않은 목록이어야 합니다"
    allowed_kinds = {"protein", "rna", "dna", "ligand"}
    seen_ids: set[str] = set()
    for index, entry in enumerate(sequences, 1):
        if not isinstance(entry, dict):
            return f"sequences {index}번째 항목은 객체여야 합니다"
        kinds = [key for key in entry if key in allowed_kinds]
        if len(kinds) != 1:
            return f"sequences {index}번째 항목은 protein/rna/dna/ligand 중 정확히 하나여야 합니다"
        unknown_entry = sorted(set(entry) - allowed_kinds)
        if unknown_entry:
            return f"sequences {index}번째 항목에 모르는 키가 있습니다: {', '.join(unknown_entry)}"
        body = entry[kinds[0]]
        if not isinstance(body, dict):
            return f"sequences {index}번째 {kinds[0]} 값은 객체여야 합니다"
        ids = body.get("id")
        valid_ids = (
            isinstance(ids, str)
            and bool(ids)
            or isinstance(ids, list)
            and bool(ids)
            and all(isinstance(value, str) and value for value in ids)
        )
        if not valid_ids:
            return f"sequences {index}번째 id 는 비어 있지 않은 문자열 또는 문자열 목록이어야 합니다"
        id_values = [ids] if isinstance(ids, str) else ids
        for chain_id in id_values:
            if not chain_id.isalpha() or not chain_id.isascii() or chain_id.upper() != chain_id:
                return f"sequences {index}번째 id={chain_id!r} 는 대문자 영문자만 허용됩니다"
            if chain_id in seen_ids:
                return f"중복 chain id 가 있습니다: {chain_id}"
            seen_ids.add(chain_id)
        if kinds[0] in {"protein", "rna", "dna"}:
            sequence = body.get("sequence")
            if not isinstance(sequence, str) or not sequence:
                return f"sequences {index}번째 {kinds[0]} sequence 가 비어 있습니다"
        else:
            has_ccd = isinstance(body.get("ccdCodes"), list) and bool(body.get("ccdCodes"))
            has_smiles = isinstance(body.get("smiles"), str) and bool(body.get("smiles"))
            if has_ccd == has_smiles:
                return f"sequences {index}번째 ligand 는 ccdCodes 또는 smiles 중 정확히 하나가 필요합니다"
    return None


def nonempty_file(path: Path) -> bool:
    try:
        return not path.is_symlink() and path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


PROVENANCE_SUFFIX = "_af3run_provenance.json"
PROVENANCE_VERSION = 1


def provenance_path(result_dir: Path, output_name: str) -> Path:
    return result_dir / f"{output_name}{PROVENANCE_SUFFIX}"


def job_provenance(job_file: Path, mode: str, db_dirs: Sequence[Path],
                   model_dir: Path, image: str) -> dict:
    """이 결과가 '무엇으로부터' 나왔는지 적는다.

    이름만 같으면 완료로 보던 것이 문제였다. 서열을 고치고 같은 이름으로 다시
    돌리면 옛 구조가 새 결과로 보고된다. 그래서 다음 실행이 같은 조건인지 비교할
    수 있도록 입력과 설정을 함께 남긴다.
    """
    digest = hashlib.sha256(job_file.read_bytes()).hexdigest()
    return {
        "provenance_version": PROVENANCE_VERSION,
        "input_sha256": digest,
        "input_file": job_file.name,
        "mode": mode,
        "db_dirs": [str(Path(d).expanduser()) for d in db_dirs],
        "model_dir": str(Path(model_dir).expanduser()),
        "image": image,
    }


def write_provenance(result_dir: Path, output_name: str, record: dict) -> None:
    try:
        result_dir.mkdir(parents=True, exist_ok=True)
        provenance_path(result_dir, output_name).write_text(
            json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    except OSError as exc:
        print(f"[경고] provenance 기록을 남기지 못했습니다: {exc}")


def needs_run(output_dir: Path, job_output_name: str, mode: str, record: dict) -> str | None:
    """다시 계산해야 하면 그 이유를, 아니면 None 을 준다.

    완료 판정이 두 곳에 흩어져 있으면 한쪽만 provenance 를 보게 되어, 잠금 획득 뒤
    재확인에서 "이미 끝났다" 로 되돌아간다. 실제로 그렇게 한 번 틀렸다.
    """
    result_dir = output_dir / job_output_name
    if not is_complete(result_dir, job_output_name, mode):
        return "결과물 없음"
    return provenance_mismatch(result_dir, job_output_name, record)


def provenance_mismatch(result_dir: Path, output_name: str, record: dict) -> str | None:
    """옛 결과가 지금 조건과 다른지 본다. 기록이 없으면 판단하지 않는다(None)."""
    path = provenance_path(result_dir, output_name)
    if not path.is_file():
        return None
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "provenance 기록을 읽을 수 없다"
    for key, label in (("input_sha256", "입력 JSON 내용"), ("mode", "실행 모드"),
                       ("db_dirs", "데이터베이스 경로"), ("model_dir", "모델 폴더"),
                       ("image", "도커 이미지")):
        if stored.get(key) != record.get(key):
            return label
    return None


def is_complete(result_dir: Path, output_name: str, mode: str = "full") -> bool:
    """선택한 실행 단계가 끝났는지 정식 산출물로 판정한다."""
    if result_dir.is_symlink() or not result_dir.is_dir():
        return False
    if mode == "data":
        return nonempty_file(result_dir / f"{output_name}_data.json")
    return all(
        any(nonempty_file(result_dir / f"{output_name}{suffix}") for suffix in group)
        for group in FINAL_REQUIRED_SUFFIXES
    )


def iter_sidecar_values(obj: object) -> Iterator[tuple[str, object]]:
    """AF3가 외부 파일로 읽는 공식 ``*Path`` 필드를 재귀적으로 찾는다."""
    stack = [obj]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            for key, value in current.items():
                if key in SIDECAR_KEYS:
                    yield key, value
                else:
                    stack.append(value)
        elif isinstance(current, list):
            stack.extend(current)


def read_job(json_file: Path, input_dir: Path) -> tuple[Job | None, str | None]:
    """JSON 하나를 읽고 이름과 staging할 sidecar를 검증한다."""
    try:
        info = os.lstat(json_file)
    except OSError as exc:
        return None, f"파일 상태를 확인할 수 없습니다 ({exc})"
    if json_file.is_symlink() or not stat.S_ISREG(info.st_mode):
        return None, "일반 파일이 아닌 JSON 또는 symlink는 허용하지 않습니다"
    if info.st_size > MAX_INPUT_JSON_BYTES:
        return None, (
            f"JSON이 안전 한도 {MAX_INPUT_JSON_BYTES} bytes를 넘습니다 "
            f"({info.st_size} bytes)"
        )
    try:
        with json_file.open(encoding="utf-8") as handle:
            obj = json.load(handle)
    except UnicodeDecodeError:
        return None, "UTF-8이 아닙니다 (macOS 껍데기 파일일 수 있음)"
    except json.JSONDecodeError as exc:
        return None, f"JSON 형식 오류 ({exc.lineno}행 {exc.colno}열: {exc.msg})"
    except OSError as exc:
        return None, f"파일을 읽을 수 없습니다 ({exc})"

    schema_error = validate_fold_job(obj)
    if schema_error is not None:
        return None, schema_error

    raw_name = obj.get("name")
    if not isinstance(raw_name, str) or not raw_name.strip():
        return None, "name 필드는 비어 있지 않은 문자열이어야 합니다"
    output_name = sanitised_name(raw_name)
    if not output_name:
        return None, (
            f"name={raw_name!r}은 정규화하면 빈 문자열입니다. "
            "영문/숫자/밑줄/하이픈/점을 한 자 이상 포함하세요"
        )
    if not is_safe_output_name(output_name):
        return None, (
            f"name={raw_name!r}은 안전한 결과 폴더 이름으로 사용할 수 없습니다. "
            "점(.) 또는 = + - @ 로 시작하는 이름과 '.', '..', 관리 파일 이름은 피하세요"
        )

    input_root = input_dir.resolve()
    sidecars: dict[Path, Sidecar] = {}
    for key, value in iter_sidecar_values(obj):
        if value in (None, ""):
            continue
        if not isinstance(value, str):
            return None, f"{key}는 문자열 경로여야 합니다"
        try:
            raw_path = Path(value)
        except (OSError, ValueError) as exc:
            return None, f"{key} 경로를 해석할 수 없습니다: {exc}"
        if raw_path.is_absolute():
            return None, (
                f"{key}={value!r}은 절대경로입니다. Docker 안에서도 동작하도록 "
                "JSON 파일 기준 상대경로로 바꿔 주세요"
            )
        try:
            source = (json_file.parent / raw_path).resolve()
        except (OSError, RuntimeError, ValueError) as exc:
            return None, f"{key} 경로를 확인할 수 없습니다: {exc}"
        try:
            relative_path = source.relative_to(input_root)
        except ValueError:
            return None, (
                f"{key}={value!r}이 입력 폴더 밖을 가리킵니다. "
                "sidecar 파일을 입력 폴더 안에 두세요"
            )
        if not source.is_file():
            return None, f"{key}가 가리키는 파일이 없습니다: {source}"
        sidecars[relative_path] = Sidecar(source, relative_path)

    return (
        Job(
            json_file=json_file,
            output_name=output_name,
            raw_name=raw_name,
            sidecars=tuple(sidecars.values()),
        ),
        None,
    )


def collect_jobs(input_dir: Path) -> tuple[list[Job], list[tuple[Path, str]]]:
    json_files = sorted(
        path for path in input_dir.glob("*.json") if not path.name.startswith("._")
    )
    jobs: list[Job] = []
    errors: list[tuple[Path, str]] = []
    for json_file in json_files:
        job, error = read_job(json_file, input_dir)
        if error is not None:
            errors.append((json_file, error))
        elif job is not None:
            jobs.append(job)
    return jobs, errors


def resolve_path(base_dir: Path, value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve()


def path_has_content(path: Path) -> bool:
    if path.is_symlink() or path.is_file():
        return True
    if not path.is_dir():
        return False
    try:
        next(path.iterdir())
    except StopIteration:
        return False
    return True


def write_marker(path: Path, payload: dict[str, object]) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        mode = os.fstat(descriptor).st_mode
        if not stat.S_ISREG(mode):
            raise OSError(f"marker is not a regular file: {path}")
        with os.fdopen(descriptor, "w", encoding="utf-8", closefd=False) as handle:
            handle.write(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n"
            )
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)


def quarantine_marker_path(snapshot: Path) -> Path:
    return snapshot.with_name(snapshot.name + ".af3_quarantine_marker.json")


def valid_quarantine_snapshot(
    snapshot: Path, output_dir: Path, output_name: str
) -> bool:
    marker = quarantine_marker_path(snapshot)
    if not marker.is_file() or marker.is_symlink():
        # Read-only compatibility with snapshots created by older releases.
        marker = snapshot / QUARANTINE_MARKER_NAME
    if snapshot.is_symlink() or not snapshot.is_dir():
        return False
    if marker.is_symlink() or not marker.is_file():
        return False
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    return (
        isinstance(payload, dict)
        and payload.get("script") == SCRIPT_ID
        and payload.get("version") == STATE_FORMAT_VERSION
        and payload.get("kind") == "quarantine"
        and payload.get("output_dir") == str(output_dir.resolve())
        and payload.get("output_name") == output_name
        and payload.get("snapshot_dir") == str(snapshot.resolve())
    )


def prune_job_quarantine(job_root: Path, output_dir: Path, output_name: str, keep: int) -> int:
    """정확히 같은 작업의 marker 소유 snapshot만 최신 ``keep``개 남긴다."""
    managed = sorted(
        (
            entry
            for entry in job_root.iterdir()
            if valid_quarantine_snapshot(entry, output_dir, output_name)
        ),
        key=lambda entry: entry.name,
        reverse=True,
    )
    removed = 0
    for old_snapshot in managed[keep:]:
        shutil.rmtree(old_snapshot)
        quarantine_marker_path(old_snapshot).unlink(missing_ok=True)
        removed += 1
        print(f"[정리] 오래된 격리 결과 삭제: {old_snapshot}")
    return removed


def quarantine_incomplete(
    output_dir: Path,
    job: Job,
    mode: str,
    keep: int = QUARANTINE_KEEP_PER_JOB,
) -> Path | None:
    """미완료 결과를 작업별 제한 보존하여 AF3의 suffix 출력 회피를 막는다."""
    if keep < 1:
        raise ValueError("격리 결과 보존 개수는 1개 이상이어야 합니다")
    if not is_safe_output_name(job.output_name):
        raise OSError(f"안전하지 않은 결과 이름입니다: {job.output_name!r}")

    result_dir = output_dir / job.output_name
    if result_dir.is_symlink():
        raise OSError(f"결과 경로가 심볼릭 링크라서 이동하지 않습니다: {result_dir}")
    if result_dir.exists() and not result_dir.is_dir():
        raise OSError(f"결과 경로가 폴더가 아니라서 이동하지 않습니다: {result_dir}")
    if is_complete(result_dir, job.output_name, mode) or not path_has_content(result_dir):
        return None

    quarantine_root = output_dir / QUARANTINE_DIR_NAME
    if quarantine_root.is_symlink() or (
        quarantine_root.exists() and not quarantine_root.is_dir()
    ):
        raise OSError(f"격리 경로가 안전한 폴더가 아닙니다: {quarantine_root}")
    quarantine_root.mkdir(parents=True, exist_ok=True)
    job_root = quarantine_root / job.output_name
    if job_root.is_symlink() or (job_root.exists() and not job_root.is_dir()):
        raise OSError(f"작업별 격리 경로가 안전한 폴더가 아닙니다: {job_root}")
    job_root.mkdir(exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    target = job_root / stamp
    counter = 1
    while target.exists() or target.is_symlink():
        target = job_root / f"{stamp}_{counter}"
        counter += 1
    result_dir.rename(target)
    marker_written = False
    try:
        write_marker(
            quarantine_marker_path(target),
            {
                "script": SCRIPT_ID,
                "version": STATE_FORMAT_VERSION,
                "kind": "quarantine",
                "created_at_epoch": time.time(),
                "output_dir": str(output_dir.resolve()),
                "output_name": job.output_name,
                "snapshot_dir": str(target.resolve()),
            },
        )
        marker_written = True
    except OSError as exc:
        print(f"[경고] 격리 결과 표식을 쓰지 못해 자동 정리에서 제외합니다: {exc}")
    print(f"[보존] 미완료 결과 이동: {result_dir.name} -> {target}")
    if marker_written:
        prune_job_quarantine(job_root, output_dir, job.output_name, keep)
    return target


def stage_jobs(
    jobs: Sequence[Job], parent_dir: Path, output_dir: Path | None = None
) -> Path:
    """남은 JSON과 상대경로 sidecar만 고유 폴더에 staging한다."""
    marker_output_dir = parent_dir if output_dir is None else output_dir
    stage_dir = Path(tempfile.mkdtemp(prefix=STAGE_PREFIX, dir=str(parent_dir)))
    try:
        planned: dict[Path, Path] = {
            Path(STAGE_MARKER_NAME): stage_dir / STAGE_MARKER_NAME
        }
        for job in jobs:
            rel_json = Path(job.json_file.name)
            if rel_json in planned:
                raise OSError(f"staging 이름 충돌: {rel_json}")
            planned[rel_json] = job.json_file
            for sidecar in job.sidecars:
                rel = sidecar.relative_path
                if rel in planned and planned[rel] != sidecar.source:
                    raise OSError(f"staging sidecar 이름 충돌: {rel}")
                planned[rel] = sidecar.source
        # 한 경로가 다른 경로의 상위 폴더이면 같은 이름을 파일이자 폴더로 만들어야 한다.
        # 조상만 되짚으면 되므로 모든 쌍을 훑을 필요가 없다 (쌍 단위 검사는 건수의
        # 제곱으로 늘어나서 대량 배치의 실행 전 대기를 그대로 키운다).
        for path in planned:
            for ancestor in path.parents:
                if ancestor in planned:
                    raise OSError(f"staging 파일/폴더 경로 충돌: {ancestor} <-> {path}")

        write_marker(
            stage_dir / STAGE_MARKER_NAME,
            {
                "script": SCRIPT_ID,
                "version": STATE_FORMAT_VERSION,
                "kind": "stage",
                "pid": os.getpid(),
                "hostname": socket.gethostname(),
                "created_at_epoch": time.time(),
                "output_dir": str(marker_output_dir.resolve()),
                "stage_dir": str(stage_dir.resolve()),
            },
        )
        for job in jobs:
            shutil.copy2(job.json_file, stage_dir / job.json_file.name)
            for sidecar in job.sidecars:
                target = stage_dir / sidecar.relative_path
                if target.exists():
                    if target.samefile(sidecar.source):
                        continue
                    raise OSError(f"staging 대상이 이미 다른 파일로 존재합니다: {target}")
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    os.link(sidecar.source, target)
                except OSError:
                    shutil.copy2(sidecar.source, target)
    except BaseException:
        shutil.rmtree(stage_dir, ignore_errors=True)
        raise
    return stage_dir


def process_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def container_name(index: int) -> str:
    """이 실행이 띄우는 컨테이너 이름. PID 를 넣어 소유를 표시한다."""
    return f"{CONTAINER_PREFIX}{os.getpid()}_{index}"


def list_managed_containers(docker_command: Sequence[str]) -> list[str]:
    """이 도구가 띄운 것으로 보이는, 지금 살아 있는 컨테이너 이름 목록."""
    try:
        process = subprocess.run(
            [
                *docker_command,
                "ps",
                "--filter",
                f"name=^{CONTAINER_PREFIX}",
                "--format",
                "{{.Names}}",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if process.returncode != 0:
        return []
    return [
        line.strip()
        for line in (process.stdout or "").splitlines()
        if CONTAINER_NAME_RE.match(line.strip())
    ]


GPU_FREE_MIB_MIN = 2000


def gpu_free_mib() -> int | None:
    """비어 있는 GPU 메모리(MiB). 읽을 수 없으면 None (GPU 없는 환경 포함)."""
    if shutil.which("nvidia-smi") is None:
        return None
    try:
        proc = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=20, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    values = [int(v) for v in re.findall(r"\d+", proc.stdout)]
    return max(values) if values else None


def gpu_busy_reason(others: Sequence[str], free_mib: int | None) -> str | None:
    """GPU 를 쓸 수 있는 상태인지 본다. 못 쓰면 사람이 읽을 이유를 돌려준다.

    AF3(JAX)는 GPU 메모리의 약 95%를 먼저 잡는다. 그래서 두 번째 실행은 계산을
    시작하지도 못하고 CUDA_ERROR_OUT_OF_MEMORY 와 JAX 역추적만 남기고 죽는다.
    실측: 12288MiB 카드에서 한 실행이 11692MiB 를 점유했고, 겹쳐 띄운 쪽은
    'no supported devices found for platform CUDA' 로 끝났다. 그 상태를 만들기
    전에 멈추는 편이 낫다.
    """
    if others:
        return (
            "다른 AF3 실행이 GPU 를 쓰고 있다: " + ", ".join(sorted(others)) + "\n"
            "       AF3 는 GPU 메모리를 거의 전부 선점하므로 둘이 같이 돌 수 없다.\n"
            "       그 실행이 끝난 뒤 다시 시작하라. 상태는 docker ps 로 볼 수 있다.\n"
            "       그래도 강행하려면 --allow-busy-gpu 를 붙인다."
        )
    if free_mib is not None and free_mib < GPU_FREE_MIB_MIN:
        return (
            f"GPU 여유 메모리가 {free_mib}MiB 뿐이다 (최소 {GPU_FREE_MIB_MIN}MiB 필요).\n"
            "       다른 프로그램이 GPU 를 쓰고 있는지 nvidia-smi 로 확인하라.\n"
            "       그래도 강행하려면 --allow-busy-gpu 를 붙인다."
        )
    return None


def orphan_containers(docker_command: Sequence[str]) -> list[str]:
    """띄운 실행이 이미 끝난 컨테이너만 고른다.

    PID 가 살아 있으면 남의 실행일 수 있으므로 건드리지 않는다. 판정을 틀리는
    방향은 '덜 지우는 쪽' 이어야 한다.
    """
    orphans = []
    for name in list_managed_containers(docker_command):
        match = CONTAINER_NAME_RE.match(name)
        if match is None:
            continue
        pid = int(match.group("pid"))
        if pid != os.getpid() and not process_is_alive(pid):
            orphans.append(name)
    return sorted(orphans)


def remove_containers(docker_command: Sequence[str], names: Sequence[str]) -> int:
    removed = 0
    for name in names:
        try:
            process = subprocess.run(
                [*docker_command, "rm", "-f", name],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            print(f"[경고] 남아 있는 컨테이너를 정리하지 못했습니다: {name} ({exc})")
            continue
        if process.returncode == 0:
            removed += 1
            print(f"[정리] 종료된 실행의 컨테이너 삭제: {name}")
        else:
            print(f"[경고] 컨테이너를 정리하지 못했습니다: {name}")
    return removed


def print_container_report(
    docker_command: Sequence[str] | None, *, suggest_cleanup: bool = True
) -> list[str]:
    if docker_command is None:
        return []
    orphans = orphan_containers(docker_command)
    if not orphans:
        return []
    print(
        f"[점검] 끝난 실행이 남긴 컨테이너 {len(orphans)}개가 아직 돌고 있습니다. "
        "GPU와 CPU를 계속 씁니다."
    )
    for name in orphans[:10]:
        print(f"   - 남아 있는 컨테이너: {name}")
    if len(orphans) > 10:
        print(f"   ... 그 외 {len(orphans) - 10}개")
    if suggest_cleanup:
        print("       정리하려면 --cleanup 을 실행하세요.")
    return orphans


def scan_stage_dirs(
    parent_dir: Path,
    output_dir: Path,
    *,
    stale_after_seconds: float,
    now: float | None = None,
) -> list[StageStatus]:
    """staging 잔여물을 읽기 전용으로 분류한다."""
    if not parent_dir.is_dir():
        return []
    current_time = time.time() if now is None else now
    expected_output = str(output_dir.resolve())
    current_host = socket.gethostname()
    statuses: list[StageStatus] = []

    try:
        candidates = sorted(parent_dir.iterdir(), key=lambda path: path.name)
    except OSError as exc:
        print(f"[경고] staging 상위 폴더를 읽지 못했습니다: {parent_dir} ({exc})")
        return []

    for stage_dir in candidates:
        if not stage_dir.name.startswith(STAGE_PREFIX):
            continue
        if stage_dir.is_symlink() or not stage_dir.is_dir():
            statuses.append(StageStatus(stage_dir, False, "링크이거나 폴더가 아님"))
            continue

        marker = stage_dir / STAGE_MARKER_NAME
        if marker.is_symlink() or not marker.is_file():
            statuses.append(StageStatus(stage_dir, False, "소유 표식 없음"))
            continue
        try:
            payload = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            statuses.append(StageStatus(stage_dir, False, "소유 표식 손상"))
            continue

        valid_common = (
            isinstance(payload, dict)
            and payload.get("script") == SCRIPT_ID
            and payload.get("version") == STATE_FORMAT_VERSION
            and payload.get("kind") == "stage"
            and payload.get("stage_dir") == str(stage_dir.resolve())
        )
        if not valid_common:
            statuses.append(StageStatus(stage_dir, False, "소유 표식 불일치"))
            continue
        if payload.get("output_dir") != expected_output:
            statuses.append(StageStatus(stage_dir, False, "다른 출력 폴더용"))
            continue
        if payload.get("hostname") != current_host:
            statuses.append(StageStatus(stage_dir, False, "다른 호스트에서 생성"))
            continue

        pid = payload.get("pid")
        created_at = payload.get("created_at_epoch")
        if (
            not isinstance(pid, int)
            or isinstance(pid, bool)
            or pid <= 0
            or not isinstance(created_at, (int, float))
            or isinstance(created_at, bool)
        ):
            statuses.append(StageStatus(stage_dir, False, "소유 표식 값 오류"))
            continue
        if process_is_alive(pid):
            statuses.append(StageStatus(stage_dir, False, f"실행 중인 PID {pid}"))
            continue
        age_seconds = current_time - float(created_at)
        if age_seconds < stale_after_seconds:
            statuses.append(StageStatus(stage_dir, False, "아직 자동 정리 유예시간 이내"))
            continue
        statuses.append(StageStatus(stage_dir, True, "종료된 실행의 관리 대상"))
    return statuses


def remove_removable_stages(statuses: Sequence[StageStatus]) -> int:
    removed = 0
    for status in statuses:
        if not status.removable:
            continue
        try:
            shutil.rmtree(status.path)
        except OSError as exc:
            print(f"[경고] staging 잔여물을 정리하지 못했습니다: {status.path} ({exc})")
        else:
            removed += 1
            print(f"[정리] 종료된 실행의 staging 삭제: {status.path}")
    return removed


def print_stage_report(statuses: Sequence[StageStatus]) -> None:
    if not statuses:
        print("[점검] 남아 있는 staging 폴더가 없습니다.")
        return
    removable = [status for status in statuses if status.removable]
    retained = [status for status in statuses if not status.removable]
    print(
        f"[점검] staging 잔여 {len(statuses)}개: "
        f"안전하게 정리 가능 {len(removable)}개, 자동 보존 {len(retained)}개."
    )
    for status in statuses[:10]:
        action = "정리 가능" if status.removable else "보존"
        print(f"   - {action}: {status.path.name} ({status.reason})")
    if len(statuses) > 10:
        print(f"   ... 그 외 {len(statuses) - 10}개")


def flag_is_listed(help_text: str, name: str) -> bool:
    return f"--{name}" in help_text or f"--[no]{name}" in help_text


def parse_docker_command(value: str) -> tuple[str, ...]:
    command = tuple(shlex.split(value))
    if not command:
        raise ValueError("--docker 명령이 비어 있습니다")
    return command


def _command_works(command: Sequence[str], timeout: int = 30) -> bool:
    try:
        process = subprocess.run(
            [*command, "info"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return process.returncode == 0


def detect_docker_command(force: str | None = None) -> tuple[tuple[str, ...] | None, str | None]:
    """Choose a non-interactive Docker command; never guess an interactive sudo."""

    requested = force or os.environ.get("AF3_DOCKER")
    if requested:
        try:
            return parse_docker_command(requested), None
        except ValueError as exc:
            return None, str(exc)
    if shutil.which("docker") is None:
        return None, "docker 명령을 찾을 수 없습니다"
    if _command_works(("docker",)):
        return ("docker",), None
    if shutil.which("sudo") and _command_works(("sudo", "-n", "docker")):
        return ("sudo", "-n", "docker"), None
    return None, (
        "docker 데몬에 비대화형으로 접근할 수 없습니다. docker 그룹/rootless 설정을 "
        "고치거나 --docker 'sudo docker' 를 대화형 실행에서 명시하세요"
    )


def probe_flags(
    docker_command: Sequence[str], image: str, timeout: int
) -> set[str] | None:
    """이미지의 AF3 플래그를 확인한다. 확인 실패 시 추측하지 않는다."""
    command = [
        *docker_command,
        "run",
        "--rm",
        "--name",
        probe_container_name(),
        image,
        "python",
        "run_alphafold.py",
        "--helpfull",
    ]
    try:
        process = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        print(f"[오류] Docker 이미지 확인이 {timeout}초 안에 끝나지 않았습니다.")
        return None
    except OSError as exc:
        print(f"[오류] Docker 명령을 실행할 수 없습니다: {exc}")
        return None

    help_text = (process.stdout or "") + (process.stderr or "")
    supported = {name for name in KNOWN_FLAGS if flag_is_listed(help_text, name)}
    if not {"json_path", "output_dir"}.issubset(supported):
        print(f"[오류] AF3 플래그 확인에 실패했습니다 (종료코드 {process.returncode}).")
        for line in [line for line in help_text.splitlines() if line.strip()][-8:]:
            print(f"       {line}")
        print("       확인 실패 상태에서 최신 플래그를 추측하여 실행하지 않습니다.")
        return None
    return supported


def validate_supported_flags(supported: set[str], mode: str) -> str | None:
    required = {"json_path", "output_dir"}
    if mode in {"full", "data"}:
        required.add("db_dir")
    if mode in {"full", "inference"}:
        required.add("model_dir")
    if mode == "data":
        required.add("run_inference")
    elif mode == "inference":
        required.add("run_data_pipeline")
    missing = sorted(required - supported)
    if missing:
        return "이 AF3 이미지에 필요한 플래그가 없습니다: " + ", ".join(
            f"--{name}" for name in missing
        )
    return None


def docker_base(
    *,
    docker_command: Sequence[str],
    image: str,
    mode: str,
    input_mount: Path,
    output_dir: Path,
    db_dirs: Sequence[Path],
    model_dir: Path,
    cache_dir: Path,
    use_cache: bool,
    container: str | None = None,
) -> list[str]:
    command = [*docker_command, "run", "--rm"]
    if container:
        command.extend(("--name", container))
    user = container_user()
    if user:
        # 이것이 없으면 컨테이너가 root 로 쓰고, 사용자는 자기 결과 폴더를 지우지
        # 못한다 (rm -rf quick_out 이 Permission denied). docker 데몬이 root 라서
        # sudo 없이 돌려도 똑같이 생긴다. 실제로 그렇게 됐다.
        # 마운트를 /root 아래 두면 안 된다. 이미지의 /root 는 700 이라 non-root 는
        # 통과조차 못 한다 (실제로 /root/af3_out 에서 Permission denied). 그래서
        # 마운트는 전부 /af3/ 아래다. HOME 은 uid 에 passwd 항목이 없어도 쓸 수
        # 있는 곳으로 고정한다.
        command.extend(("--user", user, "-e", "HOME=/tmp"))
    if mode != "data":
        command.extend(("--gpus", "all"))

    command.extend(("-v", f"{input_mount}:{CONTAINER_INPUT}:ro"))
    command.extend(("-v", f"{output_dir}:{CONTAINER_OUTPUT}"))
    db_mounts: list[str] = []
    if mode in {"full", "data"}:
        for index, db_dir in enumerate(db_dirs):
            container_db = f"/af3/db_{index}"
            db_mounts.append(container_db)
            command.extend(("-v", f"{db_dir}:{container_db}:ro"))
    if mode in {"full", "inference"}:
        command.extend(("-v", f"{model_dir}:{CONTAINER_MODELS}:ro"))
    if use_cache:
        command.extend(("-v", f"{cache_dir}:{CONTAINER_CACHE}"))

    command.extend((image, "python", "run_alphafold.py"))
    command.append(f"--output_dir={CONTAINER_OUTPUT}")
    if mode in {"full", "data"}:
        command.extend(f"--db_dir={path}" for path in db_mounts)
    if mode in {"full", "inference"}:
        command.append(f"--model_dir={CONTAINER_MODELS}")
    if use_cache:
        command.append(f"--jax_compilation_cache_dir={CONTAINER_CACHE}")
    if mode == "data":
        command.append("--norun_inference")
    elif mode == "inference":
        command.append("--norun_data_pipeline")
    return command


def run_docker(
    command: Sequence[str],
    docker_command: Sequence[str] | None = None,
    container: str | None = None,
) -> int:
    """컨테이너를 돌리고, 어떻게 끝나든 그 컨테이너를 남기지 않는다.

    ``--rm`` 은 컨테이너가 스스로 끝났을 때만 지운다. 러너가 Ctrl-C 나 SIGTERM 으로
    죽으면 데몬은 컨테이너를 계속 돌린다. 그래서 finally 에서 직접 지운다.
    (러너가 SIGKILL 로 죽으면 여기까지 못 오므로, 그 경우는 --audit/--cleanup 이 맡는다.)
    """
    try:
        return subprocess.run(command, check=False).returncode
    except OSError as exc:
        print(f"[오류] Docker 명령을 시작할 수 없습니다: {exc}")
        return 127
    finally:
        if docker_command and container:
            subprocess.run(
                [*docker_command, "rm", "-f", container],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )


def run_one_by_one(
    jobs: Sequence[Job],
    *,
    stage_dir: Path,
    output_dir: Path,
    mode: str,
    docker_command: Sequence[str],
    image: str,
    db_dirs: Sequence[Path],
    model_dir: Path,
    cache_dir: Path,
    use_cache: bool,
    quarantine_keep: int = QUARANTINE_KEEP_PER_JOB,
) -> bool:
    """한 입력의 실패가 다음 입력을 막지 않도록 파일별로 실행한다."""
    print("[안내] 남은 입력을 파일별로 실행합니다. 컨테이너 기동 비용은 반복됩니다.\n")
    had_failure = False
    for index, job in enumerate(jobs, 1):
        if is_complete(output_dir / job.output_name, job.output_name, mode):
            continue
        try:
            quarantine_incomplete(output_dir, job, mode, quarantine_keep)
        except OSError as exc:
            print(f"[경고] {job.output_name}의 미완료 결과를 보존하지 못했습니다: {exc}")
            had_failure = True
            continue

        print(f"[{index}/{len(jobs)}] 연산 중: {job.json_file.name}")
        name = container_name(index)
        command = docker_base(
            docker_command=docker_command,
            image=image,
            mode=mode,
            input_mount=stage_dir,
            output_dir=output_dir,
            db_dirs=db_dirs,
            model_dir=model_dir,
            cache_dir=cache_dir,
            use_cache=use_cache,
            container=name,
        )
        command.append(f"--json_path={CONTAINER_INPUT}/{job.json_file.name}")
        returncode = run_docker(command, docker_command, name)
        complete = is_complete(output_dir / job.output_name, job.output_name, mode)
        if returncode != 0 or not complete:
            had_failure = True
            reason = f"종료코드 {returncode}" if returncode != 0 else "필수 결과물 누락"
            print(f"[경고] {job.json_file.name} 실패: {reason}")
            print("       같은 GPU 에서 AF3 를 두 개 이상 동시에 돌리면 이렇게 죽을 수 "
                  "있습니다.")
            print("       다른 실행이 있는지: docker ps    (한 번에 하나만 돌리십시오)")
        if returncode in INFRASTRUCTURE_EXIT_CODES:
            print("[오류] Docker 실행 환경 오류이므로 반복 재시도를 중단합니다.")
            return True
    return had_failure


def run_batch_with_fallback(
    jobs: Sequence[Job],
    *,
    stage_dir: Path,
    output_dir: Path,
    mode: str,
    docker_command: Sequence[str],
    image: str,
    db_dirs: Sequence[Path],
    model_dir: Path,
    cache_dir: Path,
    use_cache: bool,
    quarantine_keep: int = QUARANTINE_KEEP_PER_JOB,
) -> bool:
    # 결과 폴더 하나가 이상하다고 나머지 전부를 세우지 않는다. 파일별 경로와 같은
    # 규칙으로 건별 경고만 남기고, 종료코드에는 그 실패를 반드시 반영한다.
    quarantine_failed = False
    for job in jobs:
        try:
            quarantine_incomplete(output_dir, job, mode, quarantine_keep)
        except OSError as exc:
            print(f"[경고] {job.output_name}의 미완료 결과를 보존하지 못했습니다: {exc}")
            print("       AF3가 이 건의 결과를 타임스탬프 폴더에 따로 쓸 수 있습니다.")
            quarantine_failed = True

    name = container_name(0)
    command = docker_base(
        docker_command=docker_command,
        image=image,
        mode=mode,
        input_mount=stage_dir,
        output_dir=output_dir,
        db_dirs=db_dirs,
        model_dir=model_dir,
        cache_dir=cache_dir,
        use_cache=use_cache,
        container=name,
    )
    command.append(f"--input_dir={CONTAINER_INPUT}")
    print(f"[실행] 컨테이너 1회 기동으로 {len(jobs)}건을 순회합니다.")
    if mode != "data":
        print("[안내] 첫 입력은 JAX 컴파일 때문에 느릴 수 있으며 이후 입력이 빨라집니다.\n")
    returncode = run_docker(command, docker_command, name)

    remaining = [
        job
        for job in jobs
        if not is_complete(output_dir / job.output_name, job.output_name, mode)
    ]
    if not remaining:
        return returncode != 0 or quarantine_failed
    if returncode in INFRASTRUCTURE_EXIT_CODES:
        print(f"[오류] Docker 실행 환경 오류(종료코드 {returncode})로 배치를 중단합니다.")
        return True

    if returncode != 0:
        print(f"\n[경고] 배치 실행이 종료코드 {returncode}로 중단됐습니다.")
    else:
        print("\n[경고] 배치는 종료됐지만 필수 결과물이 없는 입력이 있습니다.")
    print(f"       완료 결과는 유지하고 남은 {len(remaining)}건만 파일별로 재시도합니다.\n")
    fallback_failed = run_one_by_one(
        remaining,
        stage_dir=stage_dir,
        output_dir=output_dir,
        mode=mode,
        docker_command=docker_command,
        image=image,
        db_dirs=db_dirs,
        model_dir=model_dir,
        cache_dir=cache_dir,
        use_cache=use_cache,
        quarantine_keep=quarantine_keep,
    )
    return returncode != 0 or fallback_failed or quarantine_failed


@contextmanager
def output_lock(output_dir: Path) -> Iterator[None]:
    """같은 출력 폴더를 대상으로 한 이 스크립트의 중복 실행을 막는다."""
    lock_path = output_dir / ".run_af3_batch.lock"
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(lock_path, flags, 0o600)
    lock_stat = os.fstat(descriptor)
    if not stat.S_ISREG(lock_stat.st_mode) or lock_stat.st_nlink != 1:
        os.close(descriptor)
        raise RuntimeError(f"잠금 경로가 단일 일반 파일이 아닙니다: {lock_path}")
    with os.fdopen(descriptor, "r+", encoding="utf-8") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            lock_file.seek(0)
            owner = lock_file.read().strip() or "알 수 없음"
            raise RuntimeError(f"다른 실행이 이 출력 폴더를 사용 중입니다 ({owner})") from exc
        lock_file.seek(0)
        lock_file.truncate()
        lock_file.write(f"host={socket.gethostname()} pid={os.getpid()}\n")
        lock_file.flush()
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--input-dir",
        default=INPUT_DIR_NAME,
        help=f"입력 JSON 폴더 (기본값: {INPUT_DIR_NAME})",
    )
    parser.add_argument(
        "--output-dir",
        default=OUTPUT_DIR_NAME,
        help=f"결과 폴더 (기본값: {OUTPUT_DIR_NAME})",
    )
    parser.add_argument(
        "--db-dir",
        action="append",
        default=None,
        help="AF3 데이터베이스 폴더. overlay/fallback 우선순서대로 반복 가능",
    )
    parser.add_argument("--model-dir", default=MODEL_DIR, help="AF3 모델 가중치 폴더")
    parser.add_argument("--cache-dir", default=CACHE_DIR, help="JAX 컴파일 캐시 폴더")
    parser.add_argument("--image", default=AF3_IMAGE, help="Docker 이미지 이름")
    parser.add_argument(
        "--docker",
        default=None,
        help="Docker 실행 명령 강제 지정 (예: 'docker' 또는 비대화형 'sudo -n docker')",
    )
    parser.add_argument(
        "--mode",
        choices=("full", "data", "inference"),
        default=RUN_MODE,
        help="full=전체, data=데이터 파이프라인만, inference=추론만",
    )
    parser.add_argument("--per-file", action="store_true", help="파일마다 컨테이너를 따로 실행")
    parser.add_argument("--no-cache", action="store_true", help="JAX 컴파일 캐시를 사용하지 않음")
    parser.add_argument("--allow-busy-gpu", action="store_true",
                        help="다른 AF3 가 GPU 를 쓰고 있어도 강행 (보통은 필요 없다)")
    action_group = parser.add_mutually_exclusive_group()
    action_group.add_argument(
        "--guide", action="store_true", help="초보자용 설명과 현재 경로만 표시하고 종료"
    )
    action_group.add_argument(
        "--audit", action="store_true", help="실행 없이 완료/미완료와 잔여 폴더만 점검"
    )
    action_group.add_argument(
        "--cleanup", action="store_true", help="확인 후 격리 결과와 안전한 staging 잔여물 정리"
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="실행/정리 확인 질문에 자동으로 예 (자동화용)",
    )
    parser.add_argument(
        "--quarantine-keep",
        type=int,
        default=QUARANTINE_KEEP_PER_JOB,
        metavar="N",
        help=f"작업별 미완료 결과 보존 개수 (기본값: {QUARANTINE_KEEP_PER_JOB})",
    )
    parser.add_argument(
        "--probe-timeout",
        type=int,
        default=HELP_TIMEOUT_SECONDS,
        metavar="SECONDS",
        help="Docker 이미지 플래그 확인 제한시간",
    )
    return parser


def print_input_errors(errors: Sequence[tuple[Path, str]]) -> None:
    print(f"\n[오류] 사용할 수 없는 입력 {len(errors)}개를 발견했습니다.")
    for path, error in errors[:10]:
        print(f"   - {path.name}: {error}")
    if len(errors) > 10:
        print(f"   ... 그 외 {len(errors) - 10}개")


def mode_description(mode: str) -> str:
    descriptions = {
        "full": "데이터 준비 + 구조 추론 전체 (DB, 모델, GPU 필요)",
        "data": "MSA/템플릿을 포함한 데이터 준비만 (DB 필요, GPU 불필요)",
        "inference": "준비된 입력으로 구조 추론만 (모델, GPU 필요)",
    }
    return descriptions[mode]


def print_quick_guide(
    *,
    input_dir: Path,
    output_dir: Path,
    db_dirs: Sequence[Path],
    model_dir: Path,
    cache_dir: Path,
    mode: str,
    quarantine_keep: int,
) -> None:
    print(
        "\n"
        "============================================================\n"
        " AlphaFold 3 초보자용 실행 안내\n"
        "============================================================\n"
        "1. 입력 폴더의 JSON을 읽어 아직 끝나지 않은 작업만 실행합니다.\n"
        "2. 정상 완료 결과는 건드리지 않습니다.\n"
        "3. 미완료 결과는 작업별 격리 폴더로 옮기고 제한 개수만 보존합니다.\n"
        "4. 실제 Docker 실행 전에는 한 번 더 확인합니다.\n"
        "5. 상태만 보려면 --audit, 설명만 보려면 --guide를 사용하세요.\n"
        "6. 격리/잔여 폴더 정리는 --cleanup으로 미리 본 뒤 실행하세요.\n"
    )
    print(
        "[사전 준비]\n"
        "- Python 외부 패키지는 필요 없습니다(Python 3 표준 기능만 사용).\n"
        "- Docker 명령과 AlphaFold 3 이미지가 필요합니다. 기본은 비대화형 자동 탐지이며 "
        "--docker 로 명시할 수 있습니다.\n"
        "- full/inference 모드는 NVIDIA GPU 드라이버와 Docker GPU 지원이 필요합니다.\n"
        "- 아래 DB/모델 폴더에는 AlphaFold 3용 실제 파일이 준비되어 있어야 합니다.\n"
    )
    print(f"[현재 모드] {mode}: {mode_description(mode)}")
    print(f"[입력 JSON] {input_dir}")
    print(f"[결과 저장] {output_dir}")
    for index, db_dir in enumerate(db_dirs, 1):
        print(f"[유전정보 DB {index}] {db_dir}")
    print(f"[모델 정보] {model_dir}")
    print(f"[JAX 캐시] {cache_dir}")
    print(f"[미완료 보존] 작업별 최신 {quarantine_keep}개")
    script_name = shlex.quote(str(Path(__file__).resolve()))
    print(
        "\n[권장 첫 실행 순서]\n"
        f"  1) python3 {script_name} --audit    # 계산 없이 상태 점검\n"
        f"  2) python3 {script_name}            # 안내 확인 후 실제 실행\n"
        f"  3) python3 {script_name} --cleanup  # 필요할 때만 잔여물 정리"
    )


def confirm_action(assume_yes: bool, prompt: str) -> bool | None:
    """True=동의, False=사용자 취소, None=비대화형이라 확인 불가."""
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        print("[중단] 자동 실행 환경에서는 확인 질문을 할 수 없습니다.")
        print("       내용을 확인한 뒤 같은 명령에 --yes를 추가하세요.")
        return None
    try:
        answer = input(prompt).strip().lower()
    except EOFError:
        print("\n[중단] 확인 답변을 읽지 못했습니다.")
        return None
    return answer in {"y", "yes", "예", "네"}


def print_quarantine_report(output_dir: Path) -> None:
    quarantine_root = output_dir / QUARANTINE_DIR_NAME
    if quarantine_root.is_symlink():
        print(f"[경고] 격리 경로가 심볼릭 링크라서 자동 처리하지 않습니다: {quarantine_root}")
    elif quarantine_root.is_dir():
        try:
            top_level_count = sum(1 for _ in quarantine_root.iterdir())
        except OSError as exc:
            print(f"[경고] 격리 폴더를 읽지 못했습니다: {exc}")
        else:
            print(
                f"[점검] 격리 폴더가 있습니다 (최상위 항목 {top_level_count}개): "
                f"{quarantine_root}"
            )
    elif quarantine_root.exists():
        print(f"[경고] 격리 경로가 폴더가 아닙니다: {quarantine_root}")
    else:
        print("[점검] 격리된 미완료 결과가 없습니다.")


def managed_quarantine_snapshots(output_dir: Path) -> list[Path]:
    quarantine_root = output_dir / QUARANTINE_DIR_NAME
    if quarantine_root.is_symlink() or not quarantine_root.is_dir():
        return []
    managed: list[Path] = []
    try:
        job_roots = list(quarantine_root.iterdir())
    except OSError:
        return []
    for job_root in job_roots:
        if job_root.is_symlink() or not job_root.is_dir():
            continue
        try:
            entries = list(job_root.iterdir())
        except OSError:
            continue
        for snapshot in entries:
            if valid_quarantine_snapshot(snapshot, output_dir, job_root.name):
                managed.append(snapshot)
    return sorted(managed)


def remove_managed_quarantine(output_dir: Path) -> int:
    snapshots = managed_quarantine_snapshots(output_dir)
    removed = 0
    for snapshot in snapshots:
        shutil.rmtree(snapshot)
        quarantine_marker_path(snapshot).unlink(missing_ok=True)
        legacy_marker = snapshot / QUARANTINE_MARKER_NAME
        legacy_marker.unlink(missing_ok=True)
        removed += 1
        print(f"[정리] 관리되는 격리 결과 삭제: {snapshot}")
    quarantine_root = output_dir / QUARANTINE_DIR_NAME
    if quarantine_root.is_dir() and not quarantine_root.is_symlink():
        for job_root in list(quarantine_root.iterdir()):
            if job_root.is_dir() and not job_root.is_symlink():
                try:
                    job_root.rmdir()
                except OSError:
                    pass
        try:
            quarantine_root.rmdir()
        except OSError:
            pass
    return removed


def cleanup_managed_state(
    output_dir: Path, assume_yes: bool, docker_command: Sequence[str] | None = None
) -> int:
    """사용자가 명시적으로 승인한 정확한 관리 폴더만 정리한다."""
    quarantine_root = output_dir / QUARANTINE_DIR_NAME
    managed_snapshots = managed_quarantine_snapshots(output_dir)
    quarantine_deletable = bool(managed_snapshots)
    if quarantine_root.is_symlink() or (
        quarantine_root.exists() and not quarantine_root.is_dir()
    ):
        print(f"[경고] 안전한 폴더가 아니어서 격리 경로를 보존합니다: {quarantine_root}")

    preview = scan_stage_dirs(
        output_dir.parent,
        output_dir,
        stale_after_seconds=0,
    )
    print("\n[정리 미리보기]")
    print_quarantine_report(output_dir)
    print_stage_report(preview)
    orphans = print_container_report(docker_command, suggest_cleanup=False)
    removable_stages = [status for status in preview if status.removable]
    if not quarantine_deletable and not removable_stages and not orphans:
        print("[완료] 자동으로 안전하게 정리할 대상이 없습니다.")
        return 0

    if quarantine_deletable:
        print(
            f"[삭제 예정] 소유 표식이 유효한 격리 snapshot {len(managed_snapshots)}개"
        )
    if quarantine_root.is_dir() and not quarantine_root.is_symlink():
        print("[보존] 표식이 없거나 소유가 불명확한 격리 내용은 삭제하지 않습니다.")
    print("[주의] 정상 완료 결과 폴더는 삭제하지 않습니다.")
    decision = confirm_action(
        assume_yes,
        "위 격리 결과와 안전한 staging 잔여물을 정리할까요? [y/N]: ",
    )
    if decision is None:
        return 2
    if not decision:
        print("[취소] 아무것도 삭제하지 않았습니다.")
        return 0

    if orphans and docker_command is not None:
        remove_containers(docker_command, orphans)
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        with output_lock(output_dir):
            remove_managed_quarantine(output_dir)
            current = scan_stage_dirs(
                output_dir.parent,
                output_dir,
                stale_after_seconds=0,
            )
            remove_removable_stages(current)
    except (OSError, RuntimeError) as exc:
        print(f"[오류] 정리를 완료하지 못했습니다: {exc}")
        return 1

    retained = scan_stage_dirs(
        output_dir.parent,
        output_dir,
        stale_after_seconds=0,
    )
    if retained:
        print("[안내] 소유 여부나 실행 상태가 불명확한 staging은 안전을 위해 보존했습니다.")
        print_stage_report(retained)
    print("[완료] 안전한 관리 대상 정리가 끝났습니다.")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.probe_timeout <= 0:
        print("[오류] --probe-timeout은 1초 이상이어야 합니다.")
        return 2
    if args.quarantine_keep < 1:
        print("[오류] --quarantine-keep은 1개 이상이어야 합니다.")
        return 2

    base_dir = Path.cwd().resolve()
    input_dir = resolve_path(base_dir, args.input_dir)
    output_dir = resolve_path(base_dir, args.output_dir)
    db_values = args.db_dir or [DB_DIR]
    db_dirs = [resolve_path(base_dir, value) for value in db_values]
    model_dir = resolve_path(base_dir, args.model_dir)
    cache_dir = resolve_path(base_dir, args.cache_dir)

    if args.guide:
        print_quick_guide(
            input_dir=input_dir,
            output_dir=output_dir,
            db_dirs=db_dirs,
            model_dir=model_dir,
            cache_dir=cache_dir,
            mode=args.mode,
            quarantine_keep=args.quarantine_keep,
        )
        print("\n[안내] --guide이므로 파일을 만들거나 Docker를 실행하지 않았습니다.")
        return 0

    if output_dir.parent == output_dir:
        print("[오류] 파일시스템 최상위 폴더는 출력 폴더로 사용할 수 없습니다.")
        return 2
    if output_dir.exists() and not output_dir.is_dir():
        print(f"[오류] 출력 경로가 폴더가 아닙니다: {output_dir}")
        return 2

    if args.cleanup:
        print(f"[정리 대상 출력 폴더] {output_dir}")
        cleanup_docker, _ = detect_docker_command(args.docker)
        return cleanup_managed_state(output_dir, args.yes, cleanup_docker)

    if not args.audit:
        print_quick_guide(
            input_dir=input_dir,
            output_dir=output_dir,
            db_dirs=db_dirs,
            model_dir=model_dir,
            cache_dir=cache_dir,
            mode=args.mode,
            quarantine_keep=args.quarantine_keep,
        )

    if not input_dir.is_dir():
        print(f"[오류] 입력 폴더를 찾을 수 없습니다: {input_dir}")
        print("       다른 폴더라면 --input-dir /실제/경로 를 지정하세요.")
        return 2
    if input_dir == output_dir:
        print("[오류] 입력 폴더와 출력 폴더는 달라야 합니다.")
        return 2

    hidden = sorted(path.name for path in input_dir.glob("._*.json"))
    if hidden:
        print(f"[안내] macOS 껍데기 파일 {len(hidden)}개를 건너뜁니다 (예: {hidden[0]}).")

    jobs, errors = collect_jobs(input_dir)
    if errors:
        print_input_errors(errors)
        return 2
    if not jobs:
        print(f"[오류] 입력 폴더에 JSON 파일이 없습니다: {input_dir}")
        return 2

    by_output: dict[str, list[Job]] = {}
    for job in jobs:
        by_output.setdefault(job.output_name, []).append(job)
    duplicates = {name: group for name, group in by_output.items() if len(group) > 1}
    if duplicates:
        print("\n[오류] 정규화 후 결과 폴더 이름이 겹치는 입력이 있습니다.")
        for name, group in list(duplicates.items())[:10]:
            files = ", ".join(job.json_file.name for job in group)
            print(f"   - {name!r} <- {files}")
        return 2

    provenance = {
        job.output_name: job_provenance(
            job.json_file, args.mode, db_dirs, model_dir, args.image)
        for job in jobs
    }
    pending = []
    changed = []
    unverifiable = []
    for job in jobs:
        reason = needs_run(output_dir, job.output_name, args.mode, provenance[job.output_name])
        if reason:
            pending.append(job)
            if reason != "결과물 없음":
                changed.append((job.output_name, reason))
        elif not provenance_path(output_dir / job.output_name, job.output_name).is_file():
            unverifiable.append(job.output_name)
    if changed:
        print(f"\n[상태] 입력이 바뀌었거나 설정이 달라진 {len(changed)}건은 다시 계산합니다.")
        for name, reason in changed[:10]:
            print(f"   - {name}: {reason} 이(가) 지난 실행과 다릅니다")
        if len(changed) > 10:
            print(f"   ... 외 {len(changed) - 10}건")
    if unverifiable:
        print(f"\n[주의] {len(unverifiable)}건은 provenance 기록이 없어 지난 실행과 같은 "
              "입력인지 확인할 수 없습니다.")
        print("       이 기록 이전 버전으로 만든 결과입니다. 완료로 보고 건너뜁니다.")
        print("       입력이 바뀐 적이 있다면 해당 결과 폴더를 지우고 다시 실행하십시오.")
    print(
        f"\n[상태] 모드={args.mode}, JSON {len(jobs)}개 중 "
        f"완료 {len(jobs) - len(pending)}개, 미완료 {len(pending)}개."
    )

    if args.audit:
        for job in pending:
            print(f"   미완료: {output_dir.name}/{job.output_name}")
        print_quarantine_report(output_dir)
        audit_stages = scan_stage_dirs(
            output_dir.parent,
            output_dir,
            stale_after_seconds=STALE_STAGE_HOURS * 3600,
        )
        print_stage_report(audit_stages)
        audit_docker, _ = detect_docker_command(args.docker)
        print_container_report(audit_docker)
        print("[점검] --audit이므로 계산용 Docker 실행은 하지 않습니다.")
        return 1 if pending else 0
    if not pending:
        print("[완료] 모두 이미 끝나 있습니다.")
        return 0

    if args.mode in {"full", "data"}:
        db_report = verify_database_roots(db_dirs)
        if not db_report["ok"]:
            print("[오류] 데이터베이스 구성이 완전하지 않습니다.")
            for error in db_report["errors"]:
                print(f"       - {error}")
            return 2
    if args.mode in {"full", "inference"}:
        model_report = verify_model_dir(model_dir)
        if not model_report["ok"]:
            print("[오류] 모델 가중치 구성이 완전하지 않습니다.")
            for error in model_report["errors"]:
                print(f"       - {error}")
            return 2
        for warning in model_report["warnings"]:
            print(f"[경고] {warning}")

    staged_preview = scan_stage_dirs(
        output_dir.parent,
        output_dir,
        stale_after_seconds=STALE_STAGE_HOURS * 3600,
    )
    if staged_preview:
        print_stage_report(staged_preview)
        print("       즉시 정리하려면 별도로 --cleanup을 실행할 수 있습니다.")

    decision = confirm_action(
        args.yes,
        f"미완료 {len(pending)}건을 AlphaFold 3로 실행할까요? [y/N]: ",
    )
    if decision is None:
        return 2
    if not decision:
        print("[취소] Docker를 실행하거나 결과를 변경하지 않았습니다.")
        return 0

    docker_command, docker_error = detect_docker_command(args.docker)
    if docker_command is None:
        print(f"[오류] {docker_error}")
        return 2

    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"[오류] 출력 폴더를 만들 수 없습니다: {output_dir} ({exc})")
        return 1
    supported = probe_flags(docker_command, args.image, args.probe_timeout)
    if supported is None:
        return 1
    unsupported_reason = validate_supported_flags(supported, args.mode)
    if unsupported_reason is not None:
        print(f"[오류] {unsupported_reason}")
        return 2

    if args.mode != "data" and not args.allow_busy_gpu:
        mine = {name for name in list_managed_containers(docker_command)
                if (CONTAINER_NAME_RE.match(name) or (None,))
                and CONTAINER_NAME_RE.match(name).group("pid") == str(os.getpid())}
        others = [name for name in list_managed_containers(docker_command) if name not in mine]
        busy = gpu_busy_reason(others, gpu_free_mib())
        if busy:
            print(f"[오류] {busy}")
            return 2

    use_cache = (
        not args.no_cache
        and args.mode != "data"
        and "jax_compilation_cache_dir" in supported
    )
    if use_cache:
        problem = cache_dir_problem(cache_dir)
        if problem:
            # 죽이지는 않는다. 캐시는 속도를 위한 것이고, 없어도 결과는 같다.
            print(f"[경고] {problem}")
            print("       이번 실행은 캐시 없이 진행한다 (첫 입력이 느려진다).")
            use_cache = False
    if use_cache:
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            print(f"[오류] JAX 캐시 폴더를 만들 수 없습니다: {cache_dir} ({exc})")
            return 1
    elif not args.no_cache and args.mode != "data":
        print("[안내] 이 AF3 이미지에는 JAX compilation cache 플래그가 없어 생략합니다.")

    started = time.monotonic()
    run_failed = False
    cleanup_failed = False
    try:
        with output_lock(output_dir):
            pending = [
                job
                for job in pending
                if needs_run(output_dir, job.output_name, args.mode, provenance[job.output_name])
            ]
            if not pending:
                print("[완료] 잠금 대기 중 다른 실행이 모든 작업을 끝냈습니다.")
                return 0

            # Creating empty result directories as the host user prevents a
            # root-running container from making the directory itself.  Empty
            # directories are valid AF3 destinations and are not treated as
            # completed output.
            for job in pending:
                result_dir = output_dir / job.output_name
                if result_dir.is_symlink() or (
                    result_dir.exists() and not result_dir.is_dir()
                ):
                    raise OSError(f"안전하지 않은 결과 경로입니다: {result_dir}")
                result_dir.mkdir(exist_ok=True)

            stale_stages = scan_stage_dirs(
                output_dir.parent,
                output_dir,
                stale_after_seconds=STALE_STAGE_HOURS * 3600,
            )
            remove_removable_stages(stale_stages)

            stage_dir = stage_jobs(pending, output_dir.parent, output_dir)
            try:
                batch_supported = "input_dir" in supported
                use_batch = USE_SINGLE_RUN and not args.per_file and batch_supported
                if not use_batch and not args.per_file and USE_SINGLE_RUN:
                    print("[안내] 이 AF3 이미지에는 --input_dir이 없어 파일별 방식으로 전환합니다.")
                if use_batch:
                    run_failed = run_batch_with_fallback(
                        pending,
                        stage_dir=stage_dir,
                        output_dir=output_dir,
                        mode=args.mode,
                        docker_command=docker_command,
                        image=args.image,
                        db_dirs=db_dirs,
                        model_dir=model_dir,
                        cache_dir=cache_dir,
                        use_cache=use_cache,
                        quarantine_keep=args.quarantine_keep,
                    )
                else:
                    run_failed = run_one_by_one(
                        pending,
                        stage_dir=stage_dir,
                        output_dir=output_dir,
                        mode=args.mode,
                        docker_command=docker_command,
                        image=args.image,
                        db_dirs=db_dirs,
                        model_dir=model_dir,
                        cache_dir=cache_dir,
                        use_cache=use_cache,
                        quarantine_keep=args.quarantine_keep,
                    )
            finally:
                try:
                    shutil.rmtree(stage_dir)
                except OSError as exc:
                    cleanup_failed = True
                    print(f"[경고] staging 폴더를 정리하지 못했습니다: {stage_dir} ({exc})")
    except RuntimeError as exc:
        print(f"[오류] {exc}")
        return 1
    except OSError as exc:
        print(f"[오류] 파일 준비 또는 결과 보존 중 오류가 발생했습니다: {exc}")
        return 1

    # 이번에 끝난 것에 한해 provenance 를 남긴다. 다음 실행이 같은 입력·설정인지
    # 비교할 근거가 여기서 생긴다. 실패한 건에는 남기지 않는다.
    for job in pending:
        if is_complete(output_dir / job.output_name, job.output_name, args.mode):
            write_provenance(
                output_dir / job.output_name,
                job.output_name,
                provenance[job.output_name],
            )

    completed = sum(
        is_complete(output_dir / job.output_name, job.output_name, args.mode)
        for job in pending
    )
    elapsed = time.monotonic() - started
    average = elapsed / completed if completed else 0.0
    print(
        f"\n[결과] 이번 대상 {completed}/{len(pending)}건 완료. "
        f"총 {elapsed / 60:.1f}분, 완료 건당 평균 {average:.1f}초."
    )
    failures = [
        job
        for job in pending
        if not is_complete(output_dir / job.output_name, job.output_name, args.mode)
    ]
    if failures:
        print(f"[미완료] {len(failures)}건:")
        for job in failures:
            print(f"   - {job.json_file.name} -> {job.output_name}")
        print("[안내] 다시 실행하면 미완료 작업만 재시도합니다.")
        return 1
    if run_failed:
        print("[오류] 필수 산출물은 존재하지만 하나 이상의 Docker 실행이 0이 아닌 코드로 끝났습니다.")
        return 1
    if cleanup_failed:
        print("[오류] 계산은 끝났지만 staging 정리가 실패했습니다. --cleanup 으로 확인하세요.")
        return 1
    return 0


def _terminate(signum, _frame):
    """SIGTERM 을 예외로 바꿔 run_docker 의 finally 가 컨테이너를 지우게 한다.

    기본 동작으로 죽으면 데몬이 컨테이너를 계속 돌린다.
    """
    raise KeyboardInterrupt(f"signal {signum}")


if __name__ == "__main__":
    import signal

    signal.signal(signal.SIGTERM, _terminate)
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n[중단] 사용자가 멈췄습니다. 다시 실행하면 미완료 작업만 이어서 합니다.")
        print("       계산 중이던 컨테이너는 정리했습니다.")
        print("       강제 종료(kill -9)로 컨테이너가 남았다면 --cleanup 으로 확인하세요.")
        raise SystemExit(130)
