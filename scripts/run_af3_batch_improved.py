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
import json
import os
import shutil
import socket
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


# =========================================================
# USER CONFIG (CLI 옵션을 생략했을 때 쓰는 기본값)
# =========================================================
INPUT_DIR_NAME = "vhh_001_in"
OUTPUT_DIR_NAME = "vhh_001_out"
RUN_MODE = "full"  # full | data | inference
USE_SINGLE_RUN = True

DOCKER_COMMAND = ("sudo", "docker")
AF3_IMAGE = "alphafold3"
DB_DIR = "~/public_databases"
MODEL_DIR = "~/af3_models"
CACHE_DIR = "~/af3_cache"
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
FINAL_REQUIRED_SUFFIXES = (
    ("_ranking_scores.csv",),
    ("_model.cif", "_model.cif.zst"),
    ("_summary_confidences.json",),
)
SIDECAR_KEYS = frozenset(
    {"mmcifPath", "unpairedMsaPath", "pairedMsaPath", "userCCDPath"}
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
CONTAINER_INPUT = "/root/af3_in"
CONTAINER_OUTPUT = "/root/af3_out"
CONTAINER_DATABASES = "/root/public_databases"
CONTAINER_MODELS = "/root/af3_models"
CONTAINER_CACHE = "/root/af3_cache"


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
        and not name.startswith(".af3_")
        and Path(name).name == name
    )


def nonempty_file(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def is_complete(result_dir: Path, output_name: str, mode: str = "full") -> bool:
    """선택한 실행 단계가 끝났는지 정식 산출물로 판정한다."""
    if not result_dir.is_dir():
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
        with json_file.open(encoding="utf-8") as handle:
            obj = json.load(handle)
    except UnicodeDecodeError:
        return None, "UTF-8이 아닙니다 (macOS 껍데기 파일일 수 있음)"
    except json.JSONDecodeError as exc:
        return None, f"JSON 형식 오류 ({exc.lineno}행 {exc.colno}열: {exc.msg})"
    except OSError as exc:
        return None, f"파일을 읽을 수 없습니다 ({exc})"

    if not isinstance(obj, dict):
        return None, (
            "최상위가 객체(dict)가 아닙니다. AlphaFold Server의 list 형식은 "
            "이 재개형 배치 스크립트에서 지원하지 않습니다"
        )

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
            "'.', '..', '.af3_'로 시작하는 이름은 피하세요"
        )

    input_root = input_dir.resolve()
    sidecars: dict[Path, Sidecar] = {}
    for key, value in iter_sidecar_values(obj):
        if value in (None, ""):
            continue
        if not isinstance(value, str):
            return None, f"{key}는 문자열 경로여야 합니다"
        raw_path = Path(value)
        if raw_path.is_absolute():
            return None, (
                f"{key}={value!r}은 절대경로입니다. Docker 안에서도 동작하도록 "
                "JSON 파일 기준 상대경로로 바꿔 주세요"
            )
        source = (json_file.parent / raw_path).resolve()
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
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def valid_quarantine_snapshot(
    snapshot: Path, output_dir: Path, output_name: str
) -> bool:
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
            target / QUARANTINE_MARKER_NAME,
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
                    continue
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


def probe_flags(
    docker_command: Sequence[str], image: str, timeout: int
) -> set[str] | None:
    """이미지의 AF3 플래그를 확인한다. 확인 실패 시 추측하지 않는다."""
    command = [
        *docker_command,
        "run",
        "--rm",
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
    db_dir: Path,
    model_dir: Path,
    cache_dir: Path,
    use_cache: bool,
) -> list[str]:
    command = [*docker_command, "run", "--rm"]
    if mode != "data":
        command.extend(("--gpus", "all"))

    command.extend(("-v", f"{input_mount}:{CONTAINER_INPUT}:ro"))
    command.extend(("-v", f"{output_dir}:{CONTAINER_OUTPUT}"))
    if mode in {"full", "data"}:
        command.extend(("-v", f"{db_dir}:{CONTAINER_DATABASES}:ro"))
    if mode in {"full", "inference"}:
        command.extend(("-v", f"{model_dir}:{CONTAINER_MODELS}:ro"))
    if use_cache:
        command.extend(("-v", f"{cache_dir}:{CONTAINER_CACHE}"))

    command.extend((image, "python", "run_alphafold.py"))
    command.append(f"--output_dir={CONTAINER_OUTPUT}")
    if mode in {"full", "data"}:
        command.append(f"--db_dir={CONTAINER_DATABASES}")
    if mode in {"full", "inference"}:
        command.append(f"--model_dir={CONTAINER_MODELS}")
    if use_cache:
        command.append(f"--jax_compilation_cache_dir={CONTAINER_CACHE}")
    if mode == "data":
        command.append("--norun_inference")
    elif mode == "inference":
        command.append("--norun_data_pipeline")
    return command


def run_docker(command: Sequence[str]) -> int:
    try:
        return subprocess.run(command, check=False).returncode
    except OSError as exc:
        print(f"[오류] Docker 명령을 시작할 수 없습니다: {exc}")
        return 127


def run_one_by_one(
    jobs: Sequence[Job],
    *,
    stage_dir: Path,
    output_dir: Path,
    mode: str,
    docker_command: Sequence[str],
    image: str,
    db_dir: Path,
    model_dir: Path,
    cache_dir: Path,
    use_cache: bool,
    quarantine_keep: int = QUARANTINE_KEEP_PER_JOB,
) -> None:
    """한 입력의 실패가 다음 입력을 막지 않도록 파일별로 실행한다."""
    print("[안내] 남은 입력을 파일별로 실행합니다. 컨테이너 기동 비용은 반복됩니다.\n")
    for index, job in enumerate(jobs, 1):
        if is_complete(output_dir / job.output_name, job.output_name, mode):
            continue
        try:
            quarantine_incomplete(output_dir, job, mode, quarantine_keep)
        except OSError as exc:
            print(f"[경고] {job.output_name}의 미완료 결과를 보존하지 못했습니다: {exc}")
            continue

        print(f"[{index}/{len(jobs)}] 연산 중: {job.json_file.name}")
        command = docker_base(
            docker_command=docker_command,
            image=image,
            mode=mode,
            input_mount=stage_dir,
            output_dir=output_dir,
            db_dir=db_dir,
            model_dir=model_dir,
            cache_dir=cache_dir,
            use_cache=use_cache,
        )
        command.append(f"--json_path={CONTAINER_INPUT}/{job.json_file.name}")
        returncode = run_docker(command)
        complete = is_complete(output_dir / job.output_name, job.output_name, mode)
        if returncode != 0 or not complete:
            reason = f"종료코드 {returncode}" if returncode != 0 else "필수 결과물 누락"
            print(f"[경고] {job.json_file.name} 실패: {reason}")
        if returncode in INFRASTRUCTURE_EXIT_CODES:
            print("[오류] Docker 실행 환경 오류이므로 반복 재시도를 중단합니다.")
            return


def run_batch_with_fallback(
    jobs: Sequence[Job],
    *,
    stage_dir: Path,
    output_dir: Path,
    mode: str,
    docker_command: Sequence[str],
    image: str,
    db_dir: Path,
    model_dir: Path,
    cache_dir: Path,
    use_cache: bool,
    quarantine_keep: int = QUARANTINE_KEEP_PER_JOB,
) -> None:
    for job in jobs:
        quarantine_incomplete(output_dir, job, mode, quarantine_keep)

    command = docker_base(
        docker_command=docker_command,
        image=image,
        mode=mode,
        input_mount=stage_dir,
        output_dir=output_dir,
        db_dir=db_dir,
        model_dir=model_dir,
        cache_dir=cache_dir,
        use_cache=use_cache,
    )
    command.append(f"--input_dir={CONTAINER_INPUT}")
    print(f"[실행] 컨테이너 1회 기동으로 {len(jobs)}건을 순회합니다.")
    if mode != "data":
        print("[안내] 첫 입력은 JAX 컴파일 때문에 느릴 수 있으며 이후 입력이 빨라집니다.\n")
    returncode = run_docker(command)

    remaining = [
        job
        for job in jobs
        if not is_complete(output_dir / job.output_name, job.output_name, mode)
    ]
    if not remaining:
        return
    if returncode in INFRASTRUCTURE_EXIT_CODES:
        print(f"[오류] Docker 실행 환경 오류(종료코드 {returncode})로 배치를 중단합니다.")
        return

    if returncode != 0:
        print(f"\n[경고] 배치 실행이 종료코드 {returncode}로 중단됐습니다.")
    else:
        print("\n[경고] 배치는 종료됐지만 필수 결과물이 없는 입력이 있습니다.")
    print(f"       완료 결과는 유지하고 남은 {len(remaining)}건만 파일별로 재시도합니다.\n")
    run_one_by_one(
        remaining,
        stage_dir=stage_dir,
        output_dir=output_dir,
        mode=mode,
        docker_command=docker_command,
        image=image,
        db_dir=db_dir,
        model_dir=model_dir,
        cache_dir=cache_dir,
        use_cache=use_cache,
        quarantine_keep=quarantine_keep,
    )


@contextmanager
def output_lock(output_dir: Path) -> Iterator[None]:
    """같은 출력 폴더를 대상으로 한 이 스크립트의 중복 실행을 막는다."""
    lock_path = output_dir / ".run_af3_batch.lock"
    with lock_path.open("a+", encoding="utf-8") as lock_file:
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
    parser.add_argument("--db-dir", default=DB_DIR, help="AF3 데이터베이스 폴더")
    parser.add_argument("--model-dir", default=MODEL_DIR, help="AF3 모델 가중치 폴더")
    parser.add_argument("--cache-dir", default=CACHE_DIR, help="JAX 컴파일 캐시 폴더")
    parser.add_argument("--image", default=AF3_IMAGE, help="Docker 이미지 이름")
    parser.add_argument(
        "--mode",
        choices=("full", "data", "inference"),
        default=RUN_MODE,
        help="full=전체, data=데이터 파이프라인만, inference=추론만",
    )
    parser.add_argument("--per-file", action="store_true", help="파일마다 컨테이너를 따로 실행")
    parser.add_argument("--no-cache", action="store_true", help="JAX 컴파일 캐시를 사용하지 않음")
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
    db_dir: Path,
    model_dir: Path,
    cache_dir: Path,
    image: str,
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
        f"- Docker 명령과 AlphaFold 3 이미지가 필요합니다: "
        f"{' '.join(DOCKER_COMMAND)}, 이미지={image}\n"
        "- full/inference 모드는 NVIDIA GPU 드라이버와 Docker GPU 지원이 필요합니다.\n"
        "- 아래 DB/모델 폴더에는 AlphaFold 3용 실제 파일이 준비되어 있어야 합니다.\n"
    )
    print(f"[현재 모드] {mode}: {mode_description(mode)}")
    print(f"[입력 JSON] {input_dir}")
    print(f"[결과 저장] {output_dir}")
    print(f"[유전정보 DB] {db_dir}")
    print(f"[모델 정보] {model_dir}")
    print(f"[JAX 캐시] {cache_dir}")
    print(f"[미완료 보존] 작업별 최신 {quarantine_keep}개")
    script_name = Path(__file__).name
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


def cleanup_managed_state(output_dir: Path, assume_yes: bool) -> int:
    """사용자가 명시적으로 승인한 정확한 관리 폴더만 정리한다."""
    quarantine_root = output_dir / QUARANTINE_DIR_NAME
    quarantine_deletable = quarantine_root.is_dir() and not quarantine_root.is_symlink()
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
    removable_stages = [status for status in preview if status.removable]
    if not quarantine_deletable and not removable_stages:
        print("[완료] 자동으로 안전하게 정리할 대상이 없습니다.")
        return 0

    if quarantine_deletable:
        print(
            "[삭제 예정] 격리 폴더 전체(이전 버전의 표식 없는 보존본 포함): "
            f"{quarantine_root}"
        )
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

    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        with output_lock(output_dir):
            quarantine_root = output_dir / QUARANTINE_DIR_NAME
            if quarantine_root.is_dir() and not quarantine_root.is_symlink():
                shutil.rmtree(quarantine_root)
                print(f"[정리] 격리 결과 삭제: {quarantine_root}")
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
    db_dir = resolve_path(base_dir, args.db_dir)
    model_dir = resolve_path(base_dir, args.model_dir)
    cache_dir = resolve_path(base_dir, args.cache_dir)

    if args.guide:
        print_quick_guide(
            input_dir=input_dir,
            output_dir=output_dir,
            db_dir=db_dir,
            model_dir=model_dir,
            cache_dir=cache_dir,
            image=args.image,
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
        return cleanup_managed_state(output_dir, args.yes)

    if not args.audit:
        print_quick_guide(
            input_dir=input_dir,
            output_dir=output_dir,
            db_dir=db_dir,
            model_dir=model_dir,
            cache_dir=cache_dir,
            image=args.image,
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

    pending = [
        job
        for job in jobs
        if not is_complete(output_dir / job.output_name, job.output_name, args.mode)
    ]
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
        print("[점검] --audit이므로 Docker를 실행하지 않습니다.")
        return 1 if pending else 0
    if not pending:
        print("[완료] 모두 이미 끝나 있습니다.")
        return 0

    if args.mode in {"full", "data"} and not db_dir.is_dir():
        print(f"[오류] 데이터베이스 폴더를 찾을 수 없습니다: {db_dir}")
        return 2
    if args.mode in {"full", "inference"} and not model_dir.is_dir():
        print(f"[오류] 모델 가중치 폴더를 찾을 수 없습니다: {model_dir}")
        return 2

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

    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"[오류] 출력 폴더를 만들 수 없습니다: {output_dir} ({exc})")
        return 1
    supported = probe_flags(DOCKER_COMMAND, args.image, args.probe_timeout)
    if supported is None:
        return 1
    unsupported_reason = validate_supported_flags(supported, args.mode)
    if unsupported_reason is not None:
        print(f"[오류] {unsupported_reason}")
        return 2

    use_cache = (
        not args.no_cache
        and args.mode != "data"
        and "jax_compilation_cache_dir" in supported
    )
    if use_cache:
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            print(f"[오류] JAX 캐시 폴더를 만들 수 없습니다: {cache_dir} ({exc})")
            return 1
    elif not args.no_cache and args.mode != "data":
        print("[안내] 이 AF3 이미지에는 JAX compilation cache 플래그가 없어 생략합니다.")

    started = time.monotonic()
    try:
        with output_lock(output_dir):
            pending = [
                job
                for job in pending
                if not is_complete(output_dir / job.output_name, job.output_name, args.mode)
            ]
            if not pending:
                print("[완료] 잠금 대기 중 다른 실행이 모든 작업을 끝냈습니다.")
                return 0

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
                    run_batch_with_fallback(
                        pending,
                        stage_dir=stage_dir,
                        output_dir=output_dir,
                        mode=args.mode,
                        docker_command=DOCKER_COMMAND,
                        image=args.image,
                        db_dir=db_dir,
                        model_dir=model_dir,
                        cache_dir=cache_dir,
                        use_cache=use_cache,
                        quarantine_keep=args.quarantine_keep,
                    )
                else:
                    run_one_by_one(
                        pending,
                        stage_dir=stage_dir,
                        output_dir=output_dir,
                        mode=args.mode,
                        docker_command=DOCKER_COMMAND,
                        image=args.image,
                        db_dir=db_dir,
                        model_dir=model_dir,
                        cache_dir=cache_dir,
                        use_cache=use_cache,
                        quarantine_keep=args.quarantine_keep,
                    )
            finally:
                shutil.rmtree(stage_dir, ignore_errors=True)
    except RuntimeError as exc:
        print(f"[오류] {exc}")
        return 1
    except OSError as exc:
        print(f"[오류] 파일 준비 또는 결과 보존 중 오류가 발생했습니다: {exc}")
        return 1

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
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n[중단] 사용자가 멈췄습니다. 다시 실행하면 미완료 작업만 이어서 합니다.")
        raise SystemExit(130)
