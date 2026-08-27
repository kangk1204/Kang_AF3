#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
af3_batch.py - AlphaFold 3 대량 스크리닝용 최적화 배치 러너

기존 방식의 문제 (실측으로 확인됨)
    JSON 하나마다 `docker run` 을 새로 띄우면 타깃마다 아래를 처음부터 반복한다.
      (1) 컨테이너 기동  (2) JAX/CUDA 초기화  (3) 가중치 로딩  (4) XLA 커널 컴파일
    gpu-5070ti 에서 32건 x 3반복으로 통제 측정한 결과 (MSA 없는 GPU 추론 경로만, 중앙값):
      프로세스별 + 캐시 미지정 : 31.95초/건
      프로세스별 + 캐시 지정   : 18.13초/건
      단일 프로세스 + 캐시 미지정 : 6.26초/건
      단일 프로세스 + 캐시 지정   : 5.39초/건   <- 최악 대비 5.93배
    효과를 분리하면 **주효과는 단일 프로세스화(5.10배)이고 캐시 디렉터리는
    부효과(1.76배)** 다. (1)~(3) 의 프로세스 기동 비용만 건당 9.1~9.2초로 측정됐고,
    단일 프로세스에서는 0.44초/건으로 상환된다.
    캐시 지정의 이득은 첫 2건의 컴파일만 없애므로 **배치가 커지면 0으로 수렴**한다
    (96건 순회에서 정상상태 4.20초는 캐시 여부와 완전히 무관했다).

이 스크립트가 하는 일
    * 컨테이너를 1회만 띄우고 --input_dir 로 전수 JSON을 한 프로세스가 순회한다.
      -> 고정 오버헤드를 타깃 수(N)배가 아니라 1배로 압축한다. **가장 큰 개입.**
    * 입력이 실제로 쓰는 버킷만 --buckets 로 전달한다. 버킷 사다리는 128 부터
      시작한다(run_alphafold.py 기본값과 동일) -- 111~144 aa VHH 가 128 에 들어가느냐가
      추론 시간을 2.25배 바꾼다(버킷 128 4.20초 대 버킷 256 9.44초, 실측).
    * 입력을 토큰 수로 정렬한다. 단, **정렬 자체의 시간 이득은 실측 0.00초/건이다.**
      XLA 는 버킷별 컴파일 결과를 프로세스 수명 동안 보유하므로, 버킷 128/256 을
      11번 왕복해도 1번 전환과 같은 시간이 나왔다(둘 다 9.11초/건). 정렬은
      로그를 읽기 쉽게 하고 버킷 목록을 좁히는 부수 효과 때문에 남겨 둔 것이며,
      "정렬하지 않으면 재컴파일이 반복된다"는 설명은 틀렸다 -- 컴파일은 각 버킷의
      첫 등장에서만 일어난다.
    * MSA(CPU) 단계와 추론(GPU) 단계를 분리한다. MSA 산출물(*_data.json)은
      msa_store 에 보관해 재사용한다. **MSA 는 단일 갈래가 최적이다** --
      갈래를 늘리는 것은 스레드 총량이 같으면 오히려 느리다(32스레드 1갈래
      0.890 대 2갈래 0.767 타깃/분). AF3 가 이미 체인당 DB 4개를 내부에서
      병렬 검색하기 때문이다. 그래서 --msa-workers 기본값은 1 이다.
    * 재시작하면 이미 끝난 타깃을 건너뛴다. 실패 목록을 남기고 --retry 로 재시도한다.
    * sudo 의존을 없앤다 (docker 그룹이면 sudo 없이, 아니면 자동으로 sudo 부착).
    * macOS 에서 만든 tar 를 리눅스에서 풀면 생기는 '._*.json' AppleDouble 사이드카를
      입력 목록에서 제외한다 (이것 때문에 측정 3시간이 날아간 적이 있다).

무엇이 병목인지 (이 스크립트를 쓴 뒤의 이야기)
    GPU 단계는 이 스크립트로 2000건 17.8시간 -> 3.0시간이 된다. 그 다음에 무엇이
    남는지는 1단계에 쓰는 DB 구성이 정한다.
      - 축소 DB 약 2GB (연구자 현재 구성): 데이터 파이프라인이 건당 1.98초라서
        MSA 는 병목이 아니다. 2000건이면 파이프라인 1.1시간 + 추론 3.0시간.
      - 전체 DB 급 (4종 각 4GB 슬라이스): MSA 가 건당 67.0초로 2000건 37.2시간이고
        전체 40.2시간의 93%다. 이것은 프로세스를 어떻게 쪼개도 줄지 않는다
        (0.895 타깃/분에서 포화).
    즉 "코드를 고치면 MSA 가 93%" 는 전체 DB 급 구성에서만 성립하는 문장이다.
    조건별 근거는 docs/two_stage_notes.md 3절, docs/msa_correction_notes.md,
    af3_벤치마크리포트.md / af3_진단리포트.md 참고.

폴더 관례 (연구자 기존 구조를 그대로 유지)
    <작업폴더>/
      vhh_001_in/        입력 JSON (원본, 이 스크립트는 절대 수정하지 않는다)
      vhh_001_out/       최종 결과 (AF3 가 타깃별 하위 폴더를 만든다)
      vhh_001_work/      이 스크립트가 만드는 작업 공간
        stage_inputs/      원본 JSON의 fsync된 private snapshot (identity 기준)
        msa_raw/           MSA 단계 원본 출력
        msa_store/         재사용 가능한 *_data.json 보관소
        stage_msa/         MSA 단계에 넘길 입력 (자동 생성/삭제)
        stage_infer/       추론 단계에 넘길 입력 (자동 생성/삭제)
        partial/           미완성 결과 폴더를 옮겨두는 곳 (삭제하지 않는다)
        logs/              컨테이너 stdout 전체 로그
        state.json         진행 상태와 실패 목록
        run_summary.csv    타깃별 측정 결과

사용 예
    # 0) 무엇을 실행할지 먼저 눈으로 확인 (실제로 돌리지 않는다)
    python3 af3_batch.py --name vhh_001 --dry-run

    # 1) 가장 간단한 개선 (컨테이너 1회, MSA+추론을 한 프로세스가 전수 순회)
    python3 af3_batch.py --name vhh_001 --stage oneshot

    # 2) 2단계 분리 (권장). MSA 단일 갈래 -> 추론 단일 프로세스
    #    (--msa-workers 기본값이 1 이므로 따로 줄 필요 없다)
    python3 af3_batch.py --name vhh_001 --stage both

    # 3) 경량 스크리닝 설정으로 전수 -> 상위 후보만 기본값으로 재실행
    python3 af3_batch.py --name vhh_001 --stage both --diffusion-samples 1 --recycles 3

    # 4) 실패한 것만 재시도
    python3 af3_batch.py --name vhh_001 --stage both --retry
"""

import argparse
import codecs
import csv
import fcntl
import hashlib
import json
import os
import re
import selectors
import shlex
import shutil
import socket
import stat
import string
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from af3_db import (  # noqa: E402
    database_root_identity,
    verify_database_roots,
    verify_model_dir,
)

TOP_LEVEL_KEYS = {
    "name",
    "modelSeeds",
    "sequences",
    "dialect",
    "version",
    "bondedAtomPairs",
    "userCCD",
    "userCCDPath",
}
MAX_INPUT_JSON_BYTES = 512 * 1024 * 1024
MANIFEST_VERSION = 1
PROVENANCE_SUFFIX = "_af3_legacy_manifest.json"
MSA_MANIFEST_SUFFIX = "_data.af3_manifest.json"

# AF3 기본 버킷 사다리.
# run_alphafold.py 의 _BUCKETS 기본값은 **128 에서 시작한다** (소스 대조 및 실측 확인).
# 이 스크립트의 이전 버전은 256 부터 시작해 128 계단을 빠뜨렸고, 그 결과 111~144 aa VHH
# 전량이 불필요하게 256 버킷으로 밀렸다. 실측 차이는 작지 않다:
#   버킷 128 정상상태 추론 4.20초 / 버킷 256 정상상태 9.44초 = 2.25배
# (gpu-5070ti, sample 5 x recycle 10, 96건 단일 프로세스 순회)
# 128 을 빠뜨리면 2000건에서 GPU 단계가 2.3시간 -> 5.2시간이 된다.
DEFAULT_BUCKETS = [128, 256, 384, 512, 768, 1024, 1280, 1536, 2048, 2560,
                   3072, 3584, 4096, 4608, 5120]

# 컨테이너 내부 마운트 지점 (연구자 기존 명령과 동일하게 유지)
C_DB = "/root/public_databases"
C_MODEL = "/af3/models"
C_IN = "/af3/in"
C_OUT = "/af3/out"
C_CACHE = "/af3/cache"


def csv_safe_cell(value):
    """Prevent spreadsheet programs from evaluating untrusted text as a formula."""
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def log(msg):
    print("[%s] %s" % (datetime.now().strftime("%H:%M:%S"), msg), flush=True)


def sha256_file(path, chunk_size=1024 * 1024):
    """Return a streaming SHA-256 without loading large AF3 assets into RAM."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(chunk_size)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def semantic_json_sha256(obj):
    payload = json.dumps(obj, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def atomic_write_json(path, value):
    """Publish JSON without following a symlink or truncating a hardlink."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".%s.tmp." % path.name,
                                    dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def _stat_identity(path):
    """Cheap identity for very large resources that have no signed manifest."""
    try:
        info = os.lstat(path)
    except OSError:
        return {"path": str(path), "missing": True}
    return {
        "path": str(path), "device": info.st_dev, "inode": info.st_ino,
        "bytes": info.st_size, "mtime_ns": info.st_mtime_ns,
        "mode": stat.S_IFMT(info.st_mode),
    }


def _recursive_metadata_identity(directory):
    digest = hashlib.sha256()
    count = 0
    total = 0
    stack = [(Path(directory), Path("."))]
    while stack:
        current, relative = stack.pop()
        entries = sorted(os.scandir(current), key=lambda entry: entry.name, reverse=True)
        for entry in entries:
            path = Path(entry.path)
            info = os.lstat(path)
            if stat.S_ISLNK(info.st_mode):
                raise OSError("DB directory 안의 symlink는 허용하지 않는다: %s" % path)
            rel = relative / entry.name
            digest.update(("%s\0%s\0%s\n" % (
                "D" if stat.S_ISDIR(info.st_mode) else "F",
                rel.as_posix(),
                (info.st_size, info.st_mtime_ns, info.st_dev, info.st_ino),
            )).encode("utf-8", "surrogateescape"))
            count += 1
            if stat.S_ISDIR(info.st_mode):
                stack.append((path, rel))
            elif stat.S_ISREG(info.st_mode):
                total += info.st_size
            else:
                raise OSError("DB directory 안의 special file은 허용하지 않는다: %s" % path)
    return {"algorithm": "recursive-path-size-mtime-inode-v1",
            "entries": count, "bytes": total, "sha256": digest.hexdigest()}


def database_identity(db_report, db_dirs, allow_unsealed=False):
    """Use the same sealed stable-content contract as the preferred runner."""
    del db_report  # Path resolution was already validated; identity is root-manifest based.
    return {
        "roots": [
            database_root_identity(value, allow_unsealed=allow_unsealed)
            for value in db_dirs
        ]
    }


def model_identity(model_dir):
    """Use the model's exact digest; a path and byte count are not identity."""
    model = Path(model_dir).expanduser().absolute() / "af3.bin"
    record = _stat_identity(model)
    if model.is_file() and not model.is_symlink():
        record["sha256"] = sha256_file(model)
    return record


def image_identity(docker, image, dry_run=False):
    if dry_run:
        return {"reference": image, "digest": "unresolved-dry-run"}
    try:
        result = subprocess.run(
            list(docker) + ["image", "inspect", "--format", "{{.Id}}", image],
            capture_output=True, text=True, timeout=120, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("도커 이미지 identity 확인 실패: %s" % exc)
    digest = (result.stdout or "").strip()
    if result.returncode != 0 or not digest:
        raise RuntimeError("도커 이미지 identity 확인 실패(exit=%d): %s"
                           % (result.returncode, (result.stderr or "").strip()))
    return {"reference": image, "digest": digest}


# =============================================================================
# 도커 실행 방법 결정
# =============================================================================
def find_docker(force=None):
    """비대화형 Docker 명령을 고른다. 암호를 묻는 sudo 는 추측하지 않는다."""
    if force:
        command = shlex.split(force)
        return command or None
    if shutil.which("docker") is None:
        return None
    for command in (["docker"], ["sudo", "-n", "docker"]):
        if command[0] == "sudo" and shutil.which("sudo") is None:
            continue
        try:
            r = subprocess.run(command + ["info"], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=30)
            if r.returncode == 0:
                return command
        except (OSError, subprocess.TimeoutExpired):
            continue
    return None


def probe_flags(docker, image):
    """이 이미지의 run_alphafold.py 가 실제로 지원하는 플래그 집합을 --help 에서 추출.

    버전마다 플래그가 다르므로, 없는 플래그를 넘겨서 즉시 죽는 사고를 막는다.
    탐지가 실패하면 None 을 돌려주고, 호출부는 '전부 지원' 으로 가정한다.
    """
    try:
        r = subprocess.run(docker + ["run", "--rm", image,
                                     "python", "run_alphafold.py", "--help"],
                           capture_output=True, text=True, timeout=600)
        text = (r.stdout or "") + (r.stderr or "")
        found = set(re.findall(r"--(\[no\])?([a-z0-9_]+)", text))
        flags = set(f for _, f in found)
        if len(flags) < 5:
            log("오류: AF3 이미지 플래그를 확인하지 못했다 (exit=%d)." % r.returncode)
            return None
        return flags
    except Exception as e:
        log("오류: 플래그 탐지 실패(%s). 추측 실행하지 않는다." % e)
        return None


def validate_fold_job(obj):
    if not isinstance(obj, dict):
        return "최상위가 객체가 아니다"
    unknown_top = sorted(set(obj) - TOP_LEVEL_KEYS)
    if unknown_top:
        return "AF3 가 모르는 최상위 키가 있다: %s" % ", ".join(unknown_top)
    if obj.get("dialect") != "alphafold3":
        return "dialect 는 'alphafold3' 이어야 한다"
    version = obj.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version not in {1, 2, 3, 4}:
        return "version 은 1, 2, 3, 4 중 하나여야 한다"
    if not isinstance(obj.get("name"), str) or not obj["name"].strip():
        return "name 이 비어 있지 않은 문자열이 아니다"
    output_name = sanitise_name(obj["name"])
    if not output_name or output_name.startswith(".") or output_name[0] in "=+-@":
        return "AF3 정규화 후 출력 이름이 비어 있거나 위험하다"
    seeds = obj.get("modelSeeds")
    if (
        not isinstance(seeds, list)
        or not seeds
        or any(
            not isinstance(seed, int)
            or isinstance(seed, bool)
            or seed < 0
            or seed > 2**32 - 1
            for seed in seeds
        )
    ):
        return "modelSeeds 는 32-bit unsigned integer 목록이어야 한다"
    sequences = obj.get("sequences")
    if not isinstance(sequences, list) or not sequences:
        return "sequences 가 비어 있다"
    allowed_kinds = {"protein", "rna", "dna", "ligand"}
    seen_ids = set()
    for index, entry in enumerate(sequences, 1):
        if not isinstance(entry, dict):
            return "sequences %d번째 항목은 객체여야 한다" % index
        kinds = [key for key in entry if key in allowed_kinds]
        if len(kinds) != 1 or set(entry) - allowed_kinds:
            return "sequences %d번째 항목은 protein/rna/dna/ligand 중 정확히 하나여야 한다" % index
        kind = kinds[0]
        body = entry[kind]
        if not isinstance(body, dict):
            return "sequences %d번째 %s 값은 객체여야 한다" % (index, kind)
        ids = body.get("id")
        valid_ids = (
            isinstance(ids, str) and bool(ids)
            or isinstance(ids, list) and bool(ids)
            and all(isinstance(value, str) and value for value in ids)
        )
        if not valid_ids:
            return "sequences %d번째 id가 올바르지 않다" % index
        id_values = [ids] if isinstance(ids, str) else ids
        for chain_id in id_values:
            if (
                not chain_id.isalpha()
                or not chain_id.isascii()
                or chain_id.upper() != chain_id
            ):
                return "chain id는 대문자 영문자만 허용된다: %r" % chain_id
            if chain_id in seen_ids:
                return "중복 chain id가 있다: %s" % chain_id
            seen_ids.add(chain_id)
        if kind in {"protein", "rna", "dna"}:
            sequence = body.get("sequence")
            if not isinstance(sequence, str) or not sequence:
                return "sequences %d번째 %s sequence가 비어 있다" % (index, kind)
        else:
            has_ccd = isinstance(body.get("ccdCodes"), list) and bool(body.get("ccdCodes"))
            has_smiles = isinstance(body.get("smiles"), str) and bool(body.get("smiles"))
            if has_ccd == has_smiles:
                return "sequences %d번째 ligand는 ccdCodes/smiles 중 하나가 필요하다" % index
    stack = [obj]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            for key, value in current.items():
                if key in {"mmcifPath", "unpairedMsaPath", "pairedMsaPath", "userCCDPath"} and value:
                    return "%s sidecar는 legacy runner가 staging하지 않는다. 권장 runner를 사용하라" % key
                stack.append(value)
        elif isinstance(current, list):
            stack.extend(current)
    return None


# =============================================================================
# 입력 JSON 해석
# =============================================================================
# ---------------------------------------------------------------------------
# AF3 결과 폴더에서 타깃명을 결정하는 규칙  [정본 블록]
#
# 왜 폴더명을 쓰면 안 되는가 (alphafold3 commit 97d2023 에서 확인한 사실)
#   * 출력 폴더 이름은 입력 파일명이 아니라 JSON 의 name 을 정규화한 값이다
#     (folding_input.py:1054 sanitised_name -> 공백을 _ 로 바꾸고 [A-Za-z0-9_-.] 만 남긴다.
#      소문자화는 하지 않는다).
#   * run_alphafold.py:861~866 - 출력 폴더가 이미 있고 비어 있지 않으면
#     AF3 는 <폴더명>_<YYYYmmdd_HHMMSS> 폴더를 새로 만든다. 폴더 이름에는
#     타임스탬프가 붙지만 그 안의 파일 stem 은 원래 타깃명 그대로다.
#   따라서 폴더명을 타깃명으로 쓰면 재실행 결과가 별개 타깃으로 집계된다.
#
# 그래서 이 도구들은 폴더 안 산출물 파일의 stem 에서 타깃명을 얻는다.
# stem 을 믿을 수 없는 경우의 처리도 아래 resolve_result_dir 에 규정했다.
#
# 이 블록은 af3_collect.py / af3_visualize.py / af3_batch.py 에 같은 내용으로
# 들어 있다 (세 스크립트를 따로 복사해 쓰는 사용자가 있으므로 공용 모듈을 만들지
# 않았다). 고칠 때는 세 곳을 함께 고쳐라. tests/test_naming.py 가 세 사본이
# 같은 답을 내는지 검사하므로, 한 곳만 고치면 테스트가 실패한다.
# ---------------------------------------------------------------------------

# 완료의 정식 근거. 한 묶음 안의 하나라도 있으면 그 묶음은 충족으로 본다
# (mmCIF 는 --compress_large_output_files 를 쓰면 .cif.zst 로 나온다).
FINAL_SUFFIX_GROUPS = (
    ("_ranking_scores.csv",),
    ("_model.cif", "_model.cif.zst"),
    ("_summary_confidences.json",),
)
# 데이터 파이프라인(MSA)만 돌렸을 때의 산출물. 추론 완료의 근거는 아니다.
DATA_SUFFIX = "_data.json"

# AF3 가 재실행 때 붙이는 접미사: _YYYYmmdd_HHMMSS
AF3_TIMESTAMP_RE = re.compile(r"^(?P<base>.+)_(?P<ts>[0-9]{8}_[0-9]{6})$")


def is_sidecar(name):
    """집계에서 제외할 이름인지 판정한다. 두 가지를 한꺼번에 막는다.

    1) macOS AppleDouble 사이드카('._foo'). UTF-8 이 아니어서 읽으면 죽는다.
       실제로 겪은 사고다. macOS 에서 만든 tar.gz 를 리눅스에서 풀면 파일마다
       '._' 접두어 사이드카가 함께 생긴다. `ls` 에는 보이지 않지만 glob("*.json")
       에는 잡히고, 읽는 순간 UnicodeDecodeError 로 죽는다. 이 방어가 없어
       벤치마크 측정 3시간이 통째로 날아갔다.
    2) 점으로 시작하는 모든 항목. 이것은 우연이 아니라 의도다.
       배치 러너가 출력 폴더 안에 만드는 관리용 항목이 전부 점으로 시작한다:
         .af3_incomplete/    미완료 결과 격리 보관소. 여기 있는 것은 완료가 아니므로
                             집계에 섞이면 상위 후보 선별이 틀어진다.
         .af3_pending_*/     실행 중 staging 폴더. 아직 결과가 아니다.
         .run_af3_batch.lock 중복 실행 방지 lock 파일.
       run_af3_batch_improved.py 의 is_safe_output_name 도 '.af3_' 로 시작하는
       이름을 결과 이름으로 인정하지 않는다. 양쪽이 같은 약속을 지킨다.
    """
    return name.startswith("._") or name.startswith(".")


def strip_af3_timestamp(name):
    """폴더명에서 AF3 재실행 접미사(_YYYYmmdd_HHMMSS)를 떼어낸다. 없으면 그대로.

    주의: 이것은 stem 을 얻지 못했을 때의 되돌림 경로일 뿐이다. 타깃명이 원래
    '..._20260820_101010' 인 경우를 잘못 자를 수 있으므로 1순위로 쓰지 않는다.
    """
    m = AF3_TIMESTAMP_RE.match(name)
    return m.group("base") if m else name


def af3_timestamp_of(name):
    """폴더명의 재실행 접미사를 'YYYYmmdd_HHMMSS' 문자열로. 없으면 None."""
    m = AF3_TIMESTAMP_RE.match(name)
    return m.group("ts") if m else None


def _nonempty(path):
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def scan_stems(dirpath):
    """폴더 안 산출물 파일을 stem 별로 묶는다.

    반환: {stem: {"final": 충족한 묶음 수, "data": bool}}
    크기 0 인 파일은 없는 것으로 센다 (디스크가 찼거나 중간에 끊긴 흔적이다).
    """
    try:
        entries = [p for p in dirpath.iterdir()
                   if p.is_file() and not is_sidecar(p.name)]
    except OSError:
        return {}
    found = {}
    for group in FINAL_SUFFIX_GROUPS:
        for p in entries:
            for suf in group:
                if p.name.endswith(suf) and _nonempty(p):
                    stem = p.name[:-len(suf)]
                    if not stem:
                        continue
                    rec = found.setdefault(stem, {"groups": set(), "data": False})
                    rec["groups"].add(group[0])
                    break
    for p in entries:
        if p.name.endswith(DATA_SUFFIX) and _nonempty(p):
            stem = p.name[:-len(DATA_SUFFIX)]
            if stem:
                found.setdefault(stem, {"groups": set(), "data": False})["data"] = True
    return {s: {"final": len(v["groups"]), "data": v["data"]}
            for s, v in found.items()}


def resolve_result_dir(dirpath, mode="full"):
    """결과 폴더 하나의 타깃명과 완료 여부를 판정한다.

    mode="full" : 추론까지 끝났는지 (정식 3종 모두)
    mode="data" : MSA 단계만 끝났는지 (<타깃>_data.json)

    반환 dict:
        target      집계표에 쓸 타깃명
        stem        산출물 파일의 stem (없으면 None)
        source      타깃명을 어디서 얻었는가: "stem" | "folder" | "folder_stripped"
        complete    mode 기준 완료 여부
        n_final     충족한 정식 산출물 묶음 수 (0~3)
        run_ts      폴더명의 AF3 재실행 접미사 (없으면 None)
        note        사용자에게 알릴 특이사항 (없으면 "")

    stem 을 신뢰할 수 없는 경우의 처리 (문서화된 규칙):
      (a) 산출물이 하나도 없다  -> 결과 폴더가 아니다. target 은 폴더명에서
          타임스탬프를 떼어낸 값(source="folder_stripped"), complete=False.
      (b) 완료 stem 이 정확히 하나 -> 그것을 쓴다 (정상 경로).
      (c) 완료 stem 이 여러 개    -> 폴더명(또는 타임스탬프를 뗀 폴더명)과 일치하는
          stem 을 고른다. 일치하는 것이 없으면 사전순 첫 번째를 쓰고 note 에
          섞인 stem 을 모두 적는다. 임의로 고르지 않고 규칙을 고정해 두어야
          같은 폴더를 두 번 집계했을 때 답이 달라지지 않는다.
      (d) 완료 stem 은 없고 미완료 stem 만 있다 -> 같은 규칙으로 이름만 정하고
          complete=False. (추론 중 끊긴 폴더. 이름은 알려줘야 재시도할 수 있다.)
    """
    stems = scan_stems(dirpath)
    folder = dirpath.name
    stripped = strip_af3_timestamp(folder)
    run_ts = af3_timestamp_of(folder)
    def _ok(rec):
        return rec["data"] if mode == "data" else rec["final"] >= 3

    if not stems:
        return {"target": stripped, "stem": None,
                "source": "folder_stripped" if run_ts else "folder",
                "complete": False, "n_final": 0, "run_ts": run_ts,
                "note": "산출물 파일이 없다"}

    good = sorted(s for s, rec in stems.items() if _ok(rec))
    pool = good if good else sorted(stems)
    note = ""
    if len(pool) == 1:
        stem = pool[0]
    else:
        # (c)/(d): 규칙을 고정한다 - 폴더명 일치 > 타임스탬프 뗀 폴더명 일치 > 사전순
        if folder in pool:
            stem = folder
        elif stripped in pool:
            stem = stripped
        else:
            stem = pool[0]
        note = ("한 폴더에 stem 이 %d개 섞여 있다(%s). '%s' 를 대표로 골랐다"
                % (len(pool), ", ".join(pool), stem))

    rec = stems[stem]
    return {"target": stem, "stem": stem, "source": "stem",
            "complete": _ok(rec), "n_final": rec["final"], "run_ts": run_ts,
            "note": note}


def dir_run_time(dirpath, info):
    """결과 폴더의 '실행 순서' 를 비교 가능한 값으로 준다. 최신 판정에 쓴다.

    (계층, 시각) 짝을 준다. 계층을 먼저 보는 이유는 두 시계를 한 척도에서
    비교할 수 없기 때문이다. 폴더명 접미사는 AF3 가 찍은 벽시계 문자열이고
    mtime 은 파일시스템 값이라, 크기를 직접 견주면 접미사 폴더가 더 최신인데도
    오래된 것으로 판정되는 일이 생긴다 (실제로 그런 결함이 있었다).

    계층 0: 접미사 없는 폴더. AF3 는 출력 폴더가 비어 있지 않을 때만 접미사
            폴더를 만들므로, 접미사 없는 쪽이 언제나 첫 실행이다.
    계층 1: 접미사 있는 재실행 폴더.
    같은 계층 안에서만 시각을 비교한다. 계층 0 은 정식 산출물 mtime 의 최댓값,
    계층 1 은 폴더명 접미사를 초로 바꾼 값이다.
    접미사 파싱이 실패하면 계층 0 으로 떨어뜨려 mtime 으로 비교한다.
    """
    ts = info.get("run_ts")
    if ts:
        try:
            return (1, time.mktime(time.strptime(ts, "%Y%m%d_%H%M%S")))
        except ValueError:
            pass
    best = 0.0
    try:
        for p in dirpath.iterdir():
            if p.is_file() and not is_sidecar(p.name):
                try:
                    best = max(best, p.stat().st_mtime)
                except OSError:
                    continue
    except OSError:
        return (0, 0.0)
    return (0, best)


def sanitise_name(name):
    """AF3 가 출력 폴더명을 만들 때 쓰는 규칙을 모사한다.

    2026-08 수정: 예전 구현은 소문자화를 했다(`str(name).lower()`).
    실물 AF3 는 소문자화하지 않는다 - folding_input.py 의 Input.sanitised_name 은
    공백을 '_' 로 바꾸고 [A-Za-z0-9_-.] 만 남길 뿐이다 (commit 97d2023 에서 확인).
    소문자화 때문에 리눅스(대소문자 구분 파일시스템)에서 'VHH_001' 의 결과 폴더를
    'vhh_001' 로 찾아 find_result_dirs 가 빈 목록을 돌려줬다. 그 결과
    --retry 없이 재실행해도 완료된 건을 건너뛰지 못하고 전부 다시 돌렸다.
    (측정: gpu-5070ti 에서 find_result_dirs(root,"VHH_001") -> [] 확인)

    또 예전 구현은 마침표(.)를 '_' 로 바꿨는데 실물은 마침표를 남긴다.
    이제 run_af3_batch_improved.py 의 sanitised_name 과 같은 규칙이다.
    """
    spaceless = str(name).replace(" ", "_")
    allowed = set(string.ascii_letters + string.digits + "_-.")
    return "".join(ch for ch in spaceless if ch in allowed)


def read_fold_json(path):
    info = os.lstat(path)
    if path.is_symlink() or not stat.S_ISREG(info.st_mode):
        raise ValueError("일반 파일이 아닌 JSON 또는 symlink는 허용하지 않는다")
    if info.st_size > MAX_INPUT_JSON_BYTES:
        raise ValueError(
            "JSON이 안전 한도 %d bytes를 넘는다 (%d bytes)"
            % (MAX_INPUT_JSON_BYTES, info.st_size)
        )
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def count_tokens(obj, ligand_est=30):
    """토큰 수(=패딩 버킷 결정값)를 추정한다.

    단백질/RNA/DNA 는 서열 길이로 정확히 센다. 리간드는 원자 단위로 토큰화되므로
    정확히 셀 수 없어 ligand_est 개로 어림한다. 이 값은 '정렬 순서'와 '버킷 선택'에만
    쓰이므로 어림이어도 결과의 정확성에는 영향이 없다.
    """
    total = 0
    for entry in obj.get("sequences", []) or []:
        if not isinstance(entry, dict):
            continue
        for kind, body in entry.items():
            if not isinstance(body, dict):
                continue
            ids = body.get("id")
            copies = len(ids) if isinstance(ids, list) else 1
            if kind in ("protein", "rna", "dna"):
                seq = body.get("sequence") or ""
                total += len(seq) * copies
            elif kind == "ligand":
                ccd = body.get("ccdCodes")
                n = len(ccd) if isinstance(ccd, list) else 1
                total += ligand_est * n * copies
    return total


def needed_buckets(token_counts, ladder=None):
    """실제 입력들이 실제로 쓰게 되는 버킷만 골라낸다 -> 재컴파일 횟수 최소화."""
    ladder = ladder or DEFAULT_BUCKETS
    used = set()
    for t in token_counts:
        fit = [b for b in ladder if b >= t]
        if fit:
            used.add(min(fit))
        else:
            used.add(t)  # 사다리 최대치를 넘으면 그 크기로 전용 버킷이 생긴다
    return sorted(used)


# =============================================================================
# 완료/미완료 판정
#
# 2026-08 수정 (판정 기준 통일)
#   예전 outdir_is_complete 는 DONE_MARKERS 세 개 중 '하나라도' 있으면 완료로 봤다.
#   그래서 _summary_confidences.json 만 남고 추론이 끊긴 폴더를 완료로 오인했고,
#   크기 0 인 파일도 완료로 셌다 (gpu-5070ti 에서 실측 확인:
#   summary 파일 하나만 있는 폴더 -> True, 3종이 모두 0바이트인 폴더 -> True).
#   이제 정식 기준은 run_af3_batch_improved.py 의 is_complete 와 같다:
#   _ranking_scores.csv / _model.cif(또는 .cif.zst) / _summary_confidences.json
#   세 묶음이 모두 있고 크기가 0보다 클 때만 완료다.
#
#   단 이 스크립트에는 --stage msa 가 있어 단계마다 완료의 뜻이 다르다.
#     stage msa    : <타깃>_data.json 이 있으면 그 단계는 끝난 것이다 (mode="data")
#     stage infer/both/oneshot : 정식 3종을 본다 (mode="full")
#   그래서 판정 함수가 단계를 받는다.
#
#   예전 기준으로 돌아가려면 --lenient-done 을 쓴다 (기존 출력 폴더를 완료로
#   인정해 재실행을 피하고 싶을 때. 다만 끊긴 결과를 완료로 셀 수 있다).
# =============================================================================
DONE_MARKERS = ("_summary_confidences.json", "_ranking_scores.csv",
                "_model.cif", "_model.cif.zst")


def stage_check_mode(stage):
    """실행 단계에 맞는 완료 판정 모드를 준다."""
    return "data" if stage == "msa" else "full"


# 어느 단계가 무엇을 실제로 읽는가.
#   msa     -> --norun_inference       : DB 만 읽는다. af3.bin 을 열지 않는다.
#   infer   -> --norun_data_pipeline   : 모델만 읽는다. run_alphafold.py 는
#                                        --run_data_pipeline 일 때만 db_dir 을 해석한다.
#   oneshot -> 한 컨테이너에서 둘 다.
#   both    -> msa 단계와 infer 단계를 차례로 돈다. 따라서 둘 다 필요하다.
# 쓰지 않는 것을 요구하면 core 설치(가중치 미다운로드)에서 MSA 단계가 막힌다.
def stage_uses_databases(stage):
    return stage in ("msa", "oneshot", "both")


def stage_uses_model(stage):
    return stage in ("infer", "oneshot", "both")


def outdir_is_complete(d, mode="full", lenient=False):
    """AF3 결과 폴더가 해당 단계까지 끝났는지 판정. 폴더 존재만으로 판정하지 않는다.

    mode="full" : 정식 산출물 3종이 모두 있고 크기가 0보다 크다
    mode="data" : <타깃>_data.json 이 있고 크기가 0보다 크다 (--stage msa 용)
    lenient=True: 2026-08 이전 동작. 완료 표식 중 하나만 있어도 완료로 본다
    """
    if not d.is_dir():
        return False
    if lenient:
        try:
            names = [p.name for p in d.iterdir()]
        except OSError:
            return False
        return any(any(n.endswith(m) for m in DONE_MARKERS) for n in names)
    return resolve_result_dir(d, mode=mode)["complete"]


def index_result_dirs(output_dir):
    """Scan an output root once and index directories by their artifact stem."""
    index = {}
    try:
        entries = sorted(p for p in output_dir.iterdir()
                         if p.is_dir() and not p.is_symlink()
                         and not is_sidecar(p.name))
    except OSError:
        return index
    for path in entries:
        info = resolve_result_dir(path, mode="full")
        keys = set()
        if info["stem"]:
            keys.add(info["stem"])
        else:
            keys.add(strip_af3_timestamp(path.name))
        for key in keys:
            index.setdefault(key, []).append(path)
    return index


def find_result_dirs(output_dir, fold_name, result_index=None):
    """한 타깃의 결과 폴더를 모두 찾는다 (없으면 빈 목록).

    AF3 는 출력 폴더가 비어 있지 않으면 <name>_<YYYYmmdd_HHMMSS> 폴더를 새로 만든다.
    2026-08 수정: 예전에는 glob(sanitise_name(name) + "_*") 로 찾았다. 두 가지가
    틀렸다.
      1) sanitise_name 이 소문자화를 해서 리눅스에서 대문자 타깃을 못 찾았다.
      2) glob 접두어 방식은 'VHH_004' 를 찾을 때 'VHH_004_variantB' 같은 별개
         타깃까지 잡았다. 이제 폴더 안 산출물의 stem 이 타깃명과 같은지 확인한다.
    """
    want = sanitise_name(fold_name)
    if result_index is not None:
        return list(result_index.get(want, []))
    out = []
    try:
        entries = sorted(p for p in output_dir.iterdir()
                         if p.is_dir() and not is_sidecar(p.name))
    except OSError:
        return out
    for p in entries:
        # 1순위: 폴더 안 산출물 stem 이 타깃명과 같은가 (AF3 의 실제 동작)
        info = resolve_result_dir(p, mode="full")
        if info["stem"] == want:
            out.append(p)
            continue
        # 되돌림: 산출물이 아직 없는 폴더는 이름으로 판단한다
        # (추론이 시작되기 전이거나 빈 폴더. 접미사만 떼어 정확히 비교한다)
        if info["stem"] is None and strip_af3_timestamp(p.name) == want:
            out.append(p)
    return out


def result_manifest_path(result_dir, fold_name):
    return result_dir / (sanitise_name(fold_name) + PROVENANCE_SUFFIX)


def result_is_reusable(result_dir, fold_name, expected_identity,
                       trust_unverified=False, lenient=False):
    if not outdir_is_complete(result_dir, mode="full", lenient=lenient):
        return False
    manifest = result_manifest_path(result_dir, fold_name)
    if manifest_matches(manifest, expected_identity):
        try:
            stored = read_fold_json(manifest)
            artifacts = stored.get("artifacts")
            if not isinstance(artifacts, list) or not artifacts:
                return False
            for record in artifacts:
                if not isinstance(record, dict) or not isinstance(record.get("name"), str):
                    return False
                path = result_dir / record["name"]
                if (not path.is_file() or path.is_symlink()
                        or path.stat().st_nlink != 1
                        or path.stat().st_size != record.get("bytes")
                        or sha256_file(path) != record.get("sha256")):
                    return False
            return True
        except (OSError, ValueError):
            return False
    return bool(trust_unverified and not manifest.exists() and not manifest.is_symlink())


def artifact_snapshot(result_dir, fold_name):
    """Identity of required artifacts, used to avoid blessing historical output."""
    stem = sanitise_name(fold_name)
    records = []
    for group in FINAL_SUFFIX_GROUPS:
        for suffix in group:
            path = result_dir / (stem + suffix)
            if path.is_file() and not path.is_symlink():
                info = path.stat()
                records.append((path.name, info.st_dev, info.st_ino,
                                info.st_size, info.st_mtime_ns))
                break
    return tuple(records)


def publish_result_manifest(result_dir, fold_name, identity):
    """Record identity only after the current run produced complete artifacts."""
    if not outdir_is_complete(result_dir, mode="full"):
        return False
    artifacts = []
    stem = sanitise_name(fold_name)
    for group in FINAL_SUFFIX_GROUPS:
        selected = None
        for suffix in group:
            candidate = result_dir / (stem + suffix)
            if (candidate.is_file() and not candidate.is_symlink()
                    and candidate.stat().st_nlink == 1
                    and candidate.stat().st_size > 0):
                selected = candidate
                break
        if selected is None:
            return False
        artifacts.append({
            "name": selected.name,
            "bytes": selected.stat().st_size,
            "sha256": sha256_file(selected),
        })
    atomic_write_json(
        result_manifest_path(result_dir, fold_name),
        {"manifest_version": MANIFEST_VERSION, "identity": identity,
         "artifacts": artifacts},
    )
    return True


# =============================================================================
# 파일 스테이징
# =============================================================================
def _snapshot_stat_key(info):
    """Fields that must stay stable while a source file is snapshotted."""
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _copy_snapshot_bytes(source_fd, target_fd, chunk_size=1024 * 1024):
    """Copy one already-open source into one private destination descriptor."""
    while True:
        block = os.read(source_fd, chunk_size)
        if not block:
            return
        view = memoryview(block)
        while view:
            written = os.write(target_fd, view)
            if written <= 0:
                raise OSError("snapshot 파일 쓰기가 진행되지 않았다")
            view = view[written:]


def _copy_private_snapshot(source, target):
    """Create a durable, single-link snapshot and reject source-copy races.

    The source pathname is checked both before and after the descriptor copy.
    This catches in-place writes as well as rename/replacement races instead of
    committing an identity for a mixture of two source generations.
    """
    source = Path(source)
    target = Path(target)
    before_path = os.lstat(source)
    if source.is_symlink() or not stat.S_ISREG(before_path.st_mode):
        raise OSError("snapshot 원본이 일반 파일이 아니거나 symlink다: %s" % source)

    nofollow = getattr(os, "O_NOFOLLOW", 0)
    source_fd = os.open(str(source), os.O_RDONLY | nofollow)
    target_fd = None
    target_created = False
    try:
        before_fd = os.fstat(source_fd)
        if (not stat.S_ISREG(before_fd.st_mode)
                or _snapshot_stat_key(before_fd) != _snapshot_stat_key(before_path)):
            raise OSError("snapshot 원본이 열리는 동안 교체되었다: %s" % source)
        target_fd = os.open(
            str(target), os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow, 0o600)
        target_created = True
        _copy_snapshot_bytes(source_fd, target_fd)
        os.fchmod(target_fd, 0o400)
        os.fsync(target_fd)
        after_fd = os.fstat(source_fd)
        after_path = os.lstat(source)
        if (_snapshot_stat_key(after_fd) != _snapshot_stat_key(before_fd)
                or _snapshot_stat_key(after_path) != _snapshot_stat_key(before_fd)):
            raise OSError("snapshot 복사 중 원본이 수정되거나 교체되었다: %s" % source)
    except BaseException:
        if target_fd is not None:
            os.close(target_fd)
            target_fd = None
        if target_created:
            try:
                target.unlink()
            except OSError:
                pass
        raise
    finally:
        if target_fd is not None:
            os.close(target_fd)
        os.close(source_fd)

    target_info = os.lstat(target)
    if (target.is_symlink() or not stat.S_ISREG(target_info.st_mode)
            or target_info.st_nlink != 1):
        try:
            target.unlink()
        except OSError:
            pass
        raise OSError("snapshot 결과가 private regular single-link 파일이 아니다: %s"
                      % target)
    return sha256_file(target)


def _fsync_directory(path):
    directory_fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def stage_files(paths, dest, mode="snapshot", expected_sha256=None):
    """실행 대상만 모아둔 임시 입력 폴더를 만든다.

    mode="snapshot": 원본의 private single-link 복사본. 복사 도중 원본이 바뀌면
                     실패하며 파일과 디렉터리를 fsync 한다. 입력 JSON 기본 모드다.
    mode="copy": 복사 (msa_store 보호용. AF3 가 입력을 덮어쓸 수 있다고 전해지므로
                 -- 이슈 #488 이 출처로 제시되었으나 원문 대조는 하지 않았다 --
                 추론 단계 입력은 원본이 아니라 복사본을 넘긴다. MSA 산출물은
                 재계산이 비싼 자산이므로, 전제가 틀려도 복사본을 쓰는 편이 안전하다)
    """
    marker_name = ".af3_legacy_stage"
    if dest.is_symlink():
        raise OSError("staging 경로가 symlink다: %s" % dest)
    if dest.exists():
        marker = dest / marker_name
        if not marker.is_file() or marker.is_symlink() or marker.read_text(encoding="utf-8") != "Kang_AF3 legacy stage v1\n":
            raise OSError("소유 marker 없는 staging 경로를 삭제하지 않는다: %s" % dest)
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)
    (dest / marker_name).write_text("Kang_AF3 legacy stage v1\n", encoding="utf-8")
    if mode not in ("snapshot", "copy"):
        raise ValueError("알 수 없는 staging mode: %s" % mode)
    for p in paths:
        p = Path(p)
        target = dest / p.name
        if mode == "snapshot":
            copied_sha256 = _copy_private_snapshot(p, target)
            expected = (expected_sha256 or {}).get(p.name)
            if expected is not None and copied_sha256 != expected:
                try:
                    target.unlink()
                except OSError:
                    pass
                raise OSError(
                    "입력 snapshot digest가 identity와 다르다: %s" % p)
        else:
            # The inference/MSA-store copy contract is intentionally unchanged.
            shutil.copy2(p, target)
    _fsync_directory(dest)
    return dest


# =============================================================================
# 추론 입력(_data.json) 방어 검증
# (이슈 #485 / #488 이 출처로 제시된 전제에 대한 대비. 원문 대조는 하지 않았으나,
#  아래 검사는 전제가 맞든 틀리든 손해가 없는 종류다 -- 실패할 입력을 미리 걸러낼 뿐이다.)
# =============================================================================
def validate_data_json(path, expected_fold_name=None):
    """--norun_data_pipeline 로 넘길 파일이 MSA를 실제로 담고 있는지 확인.

    2단계 분리에서 가장 흔한 실패는 'Protein chain N is missing unpaired MSA' 다.
    원인은 (a) MSA가 든 *_data.json 대신 원본 JSON을 넘겼거나,
           (b) unpairedMsa/pairedMsa/templates 중 일부만 채워진 상태(부분 지정)다.
    실행 전에 걸러서 컨테이너를 헛돌리지 않는다.
    """
    try:
        obj = read_fold_json(path)
    except Exception as e:
        return False, "JSON 파싱 실패: %s" % e

    if expected_fold_name is not None:
        actual = obj.get("name")
        if not isinstance(actual, str) or sanitise_name(actual) != sanitise_name(expected_fold_name):
            return False, "내부 name(%r)이 요청 target(%r)과 다르다" % (actual, expected_fold_name)

    seqs = obj.get("sequences")
    if not seqs:
        return False, "sequences 항목이 비어 있다"

    for i, entry in enumerate(seqs, 1):
        if not isinstance(entry, dict):
            return False, "%d번째 sequences 항목의 형식이 잘못됐다" % i
        body = entry.get("protein")
        if body is None:
            continue  # 단백질이 아닌 체인은 MSA 요구 대상이 아니다
        unp = body.get("unpairedMsa", None)
        pai = body.get("pairedMsa", None)
        tpl = body.get("templates", None)
        if unp is None:
            return False, ("%d번째 단백질 체인에 unpairedMsa 가 없다(null). "
                           "MSA 단계 산출물인 *_data.json 이 아니라 원본 JSON을 "
                           "넘겼을 가능성이 크다" % i)
        if pai is None:
            return False, ("%d번째 단백질 체인의 unpairedMsa 는 있는데 pairedMsa 가 "
                           "null 이다. AF3 는 부분 지정을 거부한다(복합체는 빈 문자열 \"\" "
                           "이라도 채워져 있어야 한다)" % i)
        if tpl is None:
            return False, ("%d번째 단백질 체인의 templates 가 null 이다. "
                           "--norun_data_pipeline 에서는 템플릿 검색을 할 수 없어 "
                           "빈 목록 [] 이 들어 있어야 한다" % i)
    return True, ""


def validate_msa_artifact(path, expected_fold_name):
    """Validate current-shard ownership and base AF3 schema before publication.

    Some AF3-compatible test/dry data producers do not materialize MSA fields;
    inference readiness remains the stricter ``validate_data_json`` gate.
    """
    try:
        obj = read_fold_json(path)
    except Exception as exc:
        return False, "JSON 파싱 실패: %s" % exc
    problem = validate_fold_job(obj)
    if problem:
        return False, problem
    actual = obj.get("name")
    if sanitise_name(actual) != sanitise_name(expected_fold_name):
        return False, "내부 name(%r)이 요청 target(%r)과 다르다" % (actual, expected_fold_name)
    return True, ""


# =============================================================================
# 컨테이너 실행
# =============================================================================
# `docker run` 으로 띄운 컨테이너는 이 프로세스가 죽어도 데몬이 계속 돌린다
# (Ctrl-C, SIGTERM, SSH 끊김 모두에서 확인). 이름을 붙여 두고 끝날 때 직접 지운다.
CONTAINER_PREFIX = "af3run_"


def cache_dir_problem(cache_dir):
    """JAX 캐시를 컨테이너가 쓸 수 있는지 본다 (improved 러너와 같은 이유)."""
    d = Path(cache_dir)
    if not d.exists():
        return None
    targets = [d]
    try:
        targets.extend(c for c in d.iterdir() if c.is_dir())
    except OSError as exc:
        return "JAX 캐시 폴더를 읽을 수 없다: %s (%s)" % (d, exc)
    for path in targets:
        if not os.access(path, os.W_OK | os.X_OK):
            return ("JAX 캐시에 쓸 수 없다: %s\n"
                    "       예전 러너가 root 로 만든 캐시가 남아 있으면 이렇게 된다.\n"
                    "       고치려면: sudo chown -R $USER:$USER %s\n"
                    "       또는 --cache-dir 로 다른 폴더를 쓴다." % (path, d))
    return None


def container_user():
    """호출한 사용자의 uid:gid. POSIX 가 아니면 None (--user 를 붙이지 않는다)."""
    if not hasattr(os, "getuid"):
        return None
    return "%d:%d" % (os.getuid(), os.getgid())
_STARTED_CONTAINERS = []
_TEARDOWN_DOCKER = []


def container_name(tag):
    name = "%s%d_%s" % (CONTAINER_PREFIX, os.getpid(), tag)
    _STARTED_CONTAINERS.append(name)
    return name


def teardown_containers():
    """이 실행이 띄운 컨테이너를 남기지 않는다. 이미 끝난 것은 조용히 넘어간다."""
    if not _TEARDOWN_DOCKER:
        return
    docker = _TEARDOWN_DOCKER[0]
    names = list(dict.fromkeys(_STARTED_CONTAINERS))
    if not names:
        return
    try:
        # Remove all containers in one bounded call.  A per-container timeout
        # makes shutdown time grow without limit when --msa-workers is large.
        subprocess.run([*docker, "rm", "-f", *names],
                       stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL,
                       check=False, timeout=30)
    except (OSError, subprocess.SubprocessError):
        pass


def build_cmd(args, docker, stage, input_dir, output_dir, buckets,
              extra_env=None, n_cpu=None, flags=None, container=None):
    """docker run 명령을 조립한다. stage 에 따라 GPU 사용/파이프라인 on-off 가 다르다."""
    cmd = list(docker) + ["run", "--rm"]
    if container:
        cmd += ["--name", container]
    user = container_user()
    if user:
        # 결과를 root 가 아니라 호출한 사용자 소유로 쓴다. 마운트가 /af3/ 아래인 것도
        # 이 때문이다 (/root 는 700 이라 non-root 가 못 들어간다).
        cmd += ["--user", user, "-e", "HOME=/tmp"]

    if stage != "msa" or args.msa_gpus:
        cmd += ["--gpus", "all"]          # MSA 단계는 GPU가 필요 없다

    env = {}
    if args.unified_memory:
        env.update({"XLA_PYTHON_CLIENT_PREALLOCATE": "false",
                    "TF_FORCE_UNIFIED_MEMORY": "true",
                    "XLA_CLIENT_MEM_FRACTION": "3.2"})
    elif args.no_prealloc:
        env.update({"XLA_PYTHON_CLIENT_PREALLOCATE": "false",
                    "XLA_CLIENT_MEM_FRACTION": "1.0"})
    if extra_env:
        env.update(extra_env)
    for k, v in sorted(env.items()):
        cmd += ["-e", "%s=%s" % (k, v)]

    # docker -v 는 호스트 경로가 반드시 절대경로여야 한다. 상대경로가 섞이면
    # 도커가 이를 '볼륨 이름' 으로 오해해 엉뚱한 빈 볼륨을 마운트한다.
    def absp(p):
        return os.path.abspath(os.path.expanduser(str(p)))

    wants_db = stage_uses_databases(stage)
    wants_model = stage_uses_model(stage)

    db_mounts = []
    if wants_db:
        for index, db_dir in enumerate(args.db_dirs):
            container_db = "/af3/db_%d" % index
            db_mounts.append(container_db)
            cmd += ["-v", "%s:%s:ro" % (absp(db_dir), container_db)]
    if wants_model:
        cmd += ["-v", "%s:%s:ro" % (absp(args.model_dir), C_MODEL)]
    cmd += ["-v", "%s:%s:ro" % (absp(input_dir), C_IN),
            "-v", "%s:%s" % (absp(output_dir), C_OUT)]
    if args.cache_dir:
        cmd += ["-v", "%s:%s" % (absp(args.cache_dir), C_CACHE)]

    cmd += [args.image, "python", "run_alphafold.py",
            "--input_dir=%s" % C_IN,
            "--output_dir=%s" % C_OUT]
    if wants_model:
        cmd.append("--model_dir=%s" % C_MODEL)
    cmd += ["--db_dir=%s" % path for path in db_mounts]

    def ok(flag):
        return flags is None or flag in flags

    if stage == "msa":
        cmd.append("--norun_inference")
    elif stage == "infer":
        cmd.append("--norun_data_pipeline")

    if args.cache_dir and ok("jax_compilation_cache_dir"):
        cmd.append("--jax_compilation_cache_dir=%s" % C_CACHE)

    if stage in ("infer", "oneshot"):
        if buckets and ok("buckets"):
            cmd.append("--buckets=%s" % ",".join(str(b) for b in buckets))
        if args.diffusion_samples is not None and ok("num_diffusion_samples"):
            cmd.append("--num_diffusion_samples=%d" % args.diffusion_samples)
        if args.recycles is not None and ok("num_recycles"):
            cmd.append("--num_recycles=%d" % args.recycles)
        if args.flash_attention and ok("flash_attention_implementation"):
            cmd.append("--flash_attention_implementation=%s" % args.flash_attention)

    if stage in ("msa", "oneshot") and n_cpu:
        if ok("jackhmmer_n_cpu"):
            cmd.append("--jackhmmer_n_cpu=%d" % n_cpu)
        if ok("nhmmer_n_cpu"):
            cmd.append("--nhmmer_n_cpu=%d" % n_cpu)

    cmd += list(args.extra_flag or [])
    return cmd


FOLD_START = re.compile(r"Running fold job\s+(.+?)\.\.\.")


def _progress_signature(roots, target_names=None):
    """Return cheap recursive metadata that changes as AF3 artifacts grow.

    Symlinks and non-regular files are ignored.  ``target_names`` restricts a
    shared MSA output tree to one shard's names so another healthy shard cannot
    indefinitely conceal a stalled worker.
    """
    count = 0
    total_bytes = 0
    newest_mtime_ns = 0
    wanted = set(target_names or [])
    for raw_root in roots:
        root = Path(raw_root)
        if not root.exists() or root.is_symlink():
            continue
        for current, dirnames, filenames in os.walk(str(root), followlinks=False):
            dirnames[:] = [
                name for name in dirnames
                if not (Path(current) / name).is_symlink()
            ]
            for filename in filenames:
                path = Path(current) / filename
                if wanted:
                    try:
                        relative = path.relative_to(root)
                    except ValueError:
                        continue
                    if not any(
                            component == name or component.startswith(name + "_")
                            for component in relative.parts for name in wanted):
                        continue
                try:
                    info = os.lstat(path)
                except OSError:
                    continue
                if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
                    continue
                count += 1
                total_bytes += info.st_size
                newest_mtime_ns = max(newest_mtime_ns, info.st_mtime_ns)
    return count, total_bytes, newest_mtime_ns


def _stop_process(process, grace_seconds=5):
    """Terminate then kill a child without allowing cleanup to hang."""
    if process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=grace_seconds)
        return
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        process.kill()
        process.wait(timeout=grace_seconds)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _watchdog_poll_interval(no_progress_timeout):
    if no_progress_timeout:
        return min(1.0, max(0.1, no_progress_timeout / 4.0))
    return 1.0


def _watchdog_scan_interval(no_progress_timeout):
    if no_progress_timeout:
        return min(30.0, max(0.25, no_progress_timeout / 4.0))
    return 30.0


def run_streamed(cmd, logfile, tag, no_progress_timeout=7200,
                 progress_roots=()):
    """컨테이너를 실행하면서 stdout 을 로그로 남기고, 타깃별 벽시계 시간을 측정한다.

    AF3 가 찍는 'Running fold job <name>...' 줄을 기준으로 구간을 자른다.
    시간은 AF3 가 보고한 값이 아니라 이 스크립트가 직접 잰 벽시계 시간이다(측정값).
    """
    timings = []           # (fold_name, 시작시각, 종료시각)
    cur, cur_t0 = None, None
    t0 = time.time()
    with open(logfile, "a", encoding="utf-8") as lf:
        lf.write("\n===== %s %s =====\n" % (tag, datetime.now().isoformat()))
        lf.write("CMD: %s\n" % " ".join(cmd))
        lf.flush()
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, bufsize=0)
        selector = selectors.DefaultSelector()
        selector.register(proc.stdout, selectors.EVENT_READ)
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        line_buffer = ""
        last_progress = time.monotonic()
        signature = _progress_signature(progress_roots)
        scan_interval = _watchdog_scan_interval(no_progress_timeout)
        next_scan = last_progress + scan_interval
        timed_out = False

        def consume(text):
            nonlocal cur, cur_t0, line_buffer
            if not text:
                return
            lf.write(text)
            lf.flush()
            line_buffer += text
            lines = line_buffer.splitlines(True)
            if lines and not lines[-1].endswith(("\n", "\r")):
                line_buffer = lines.pop()
            else:
                line_buffer = ""
            for raw in lines:
                m = FOLD_START.search(raw)
                if m:
                    now = time.time()
                    if cur is not None:
                        timings.append((cur, cur_t0, now))
                    cur, cur_t0 = m.group(1).strip(), now
                    log("  [%s] 진행: %s" % (tag, cur))

        try:
            while selector.get_map() or proc.poll() is None:
                events = selector.select(
                    timeout=_watchdog_poll_interval(no_progress_timeout))
                now = time.monotonic()
                for key, _event in events:
                    try:
                        chunk = os.read(key.fd, 64 * 1024)
                    except BlockingIOError:
                        continue
                    if chunk:
                        last_progress = now
                        consume(decoder.decode(chunk))
                    else:
                        selector.unregister(key.fileobj)
                if now >= next_scan:
                    current_signature = _progress_signature(progress_roots)
                    if current_signature != signature:
                        signature = current_signature
                        last_progress = time.monotonic()
                    next_scan = time.monotonic() + scan_interval
                if (no_progress_timeout
                        and time.monotonic() - last_progress >= no_progress_timeout):
                    message = ("[%s] 오류: stdout/log 또는 결과 artifact 변화가 %s초 동안 "
                               "없어 프로세스를 종료한다.\n"
                               % (datetime.now().isoformat(), no_progress_timeout))
                    lf.write(message)
                    lf.flush()
                    log("오류: %s 단계가 %s초 동안 진행되지 않아 종료한다."
                        % (tag, no_progress_timeout))
                    _stop_process(proc)
                    timed_out = True
                    break
            if not timed_out:
                proc.wait()
            # Decode a final partial UTF-8 sequence/line after pipe EOF.
            consume(decoder.decode(b"", final=True))
            if line_buffer:
                consume("\n")
        finally:
            selector.close()
            if proc.poll() is None:
                _stop_process(proc)
            if proc.stdout is not None:
                proc.stdout.close()
        if cur is not None:
            timings.append((cur, cur_t0, time.time()))
        returncode = 124 if timed_out else proc.returncode
        lf.write("\n[exit=%d, wall=%.1fs]\n" % (returncode, time.time() - t0))
    return returncode, timings, time.time() - t0


# =============================================================================
# 단계 실행
# =============================================================================
def _atomic_copy(source, target):
    """Copy, fsync, and atomically replace a regular destination."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".%s.tmp." % target.name,
                                    dir=str(target.parent))
    tmp = Path(tmp_name)
    try:
        with open(source, "rb") as src, os.fdopen(fd, "wb") as dst:
            shutil.copyfileobj(src, dst, 1024 * 1024)
            dst.flush()
            os.fsync(dst.fileno())
        os.replace(tmp, target)
        dir_fd = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def collect_msa_outputs(msa_raw, msa_store, candidates=None, identities=None):
    """Publish validated current MSA files; never recursively replay history.

    ``candidates`` is supplied by the execution path and contains only artifacts
    created by successful current shards.  The optional fallback is retained for
    direct legacy integrations, but scans only the root and its immediate AF3
    result directories rather than recursively copying historical trees.
    """
    msa_store.mkdir(parents=True, exist_ok=True)
    moved = 0
    if candidates is None:
        candidates = list(msa_raw.glob("*_data.json"))
        for child in sorted(msa_raw.iterdir() if msa_raw.is_dir() else []):
            if child.is_dir() and not child.is_symlink() and not is_sidecar(child.name):
                candidates.extend(child.glob("*_data.json"))
    identities = identities or {}
    for p in sorted(set(candidates)):
        if is_sidecar(p.name):
            continue
        try:
            source_info = os.lstat(p)
        except OSError:
            continue
        if (not stat.S_ISREG(source_info.st_mode) or p.is_symlink()
                or source_info.st_nlink != 1):
            continue
        stem = p.name[:-len(DATA_SUFFIX)] if p.name.endswith(DATA_SUFFIX) else ""
        identity = identities.get(stem)
        ok, _why = validate_msa_artifact(p, stem) if identity is not None else (True, "")
        if not ok and identity is not None:
            continue
        target = msa_store / p.name
        _atomic_copy(p, target)
        if identity is not None:
            atomic_write_json(
                msa_store / (stem + MSA_MANIFEST_SUFFIX),
                {
                    "manifest_version": MANIFEST_VERSION,
                    "identity": identity,
                    "artifact": {"sha256": sha256_file(target), "bytes": target.stat().st_size},
                },
            )
        moved += 1
    return moved


def _data_candidates(msa_raw):
    candidates = list(msa_raw.glob("*_data.json"))
    try:
        children = sorted(msa_raw.iterdir())
    except OSError:
        children = []
    for child in children:
        if child.is_dir() and not child.is_symlink() and not is_sidecar(child.name):
            candidates.extend(child.glob("*_data.json"))
    return [path for path in candidates if path.is_file() and not path.is_symlink()]


def _file_generation(path):
    info = path.stat()
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


def msa_store_is_complete(work, fold_name, expected_identity=None,
                          trust_unverified=False):
    path = work / "msa_store" / (sanitise_name(fold_name) + "_data.json")
    try:
        if not (path.is_file() and not path.is_symlink() and path.stat().st_size > 0):
            return False
    except OSError:
        return False
    manifest = work / "msa_store" / (sanitise_name(fold_name) + MSA_MANIFEST_SUFFIX)
    if expected_identity is not None and manifest_matches(manifest, expected_identity):
        try:
            stored = read_fold_json(manifest)
            artifact = stored.get("artifact", {})
            return (artifact.get("bytes") == path.stat().st_size
                    and artifact.get("sha256") == sha256_file(path))
        except Exception:
            return False
    return bool(trust_unverified and not manifest.exists() and not manifest.is_symlink())


def msa_n_cpu(args, n_shards=1):
    """--jackhmmer_n_cpu / --nhmmer_n_cpu 에 넘길 값을 정한다.

    실측 근거 (24코어 호스트, 14개 조합 스윕):
      AF3 는 체인 1개당 DB 4개를 ThreadPoolExecutor(max_workers=4) 로 동시 검색한다.
      따라서 실효 스레드 = n_cpu x 4 x 갈래수.
      처리율은 총 스레드가 코어 수의 약 1.3배(= 코어수/2 x 4)인 지점에서 포화한다:
        24스레드 0.778 / 32스레드 0.890 / 48스레드 0.895 / 96스레드 0.848 타깃/분
      (이 스윕의 측정 조건은 전체 DB 급 = 4종 각 4GB 슬라이스다.
       축소 DB 약 2GB 에서는 데이터 파이프라인이 건당 1.98초로 훨씬 짧다.)
      즉 n_cpu = min(코어수/2, 8) 이 최적이고, 그 이상 올려도 늘지 않는다.
      AF3 기본값이 min(cpu_count, 8) 이므로 8코어 이상 머신에서는 기본값이 이미
      최적에 가깝다 -- 손대지 않는 것이 정답인 부분이다.
    """
    if args.msa_n_cpu:
        return max(1, args.msa_n_cpu)
    cores = os.cpu_count() or 8
    return max(1, min(cores // 2, 8) // max(1, n_shards)) if n_shards > 1 \
        else max(1, min(cores // 2, 8))


def wait_for_msa_processes(procs, msa_raw, no_progress_timeout=7200):
    """Wait for all MSA shards while independently detecting stalled workers.

    Each item is ``(index, process, logfile, handle, shard_targets)``.  Log-file
    growth or artifacts whose paths belong to that shard reset its own timer.
    This intentionally polls every process rather than waiting in launch order.
    """
    now = time.monotonic()
    scan_interval = _watchdog_scan_interval(no_progress_timeout)
    states = {}
    for si, process, logfile, handle, shard_targets in procs:
        names = {sanitise_name(target["name"]) for target in shard_targets}
        try:
            log_size = logfile.stat().st_size
        except OSError:
            log_size = 0
        states[si] = {
            "process": process,
            "logfile": logfile,
            "handle": handle,
            "names": names,
            "log_size": log_size,
            "artifact_signature": _progress_signature([msa_raw], names),
            "last_progress": now,
            "next_scan": now + scan_interval,
        }

    rcs = {}
    try:
        while states:
            now = time.monotonic()
            for si, state in list(states.items()):
                process = state["process"]
                rc = process.poll()
                if rc is not None:
                    state["handle"].close()
                    rcs[si] = rc
                    del states[si]
                    log("  MSA 갈래 %d 종료 (exit=%d, 로그=%s)"
                        % (si, rc, state["logfile"]))
                    continue

                try:
                    log_size = state["logfile"].stat().st_size
                except OSError:
                    log_size = 0
                if log_size != state["log_size"]:
                    state["log_size"] = log_size
                    state["last_progress"] = now

                if now >= state["next_scan"]:
                    signature = _progress_signature([msa_raw], state["names"])
                    if signature != state["artifact_signature"]:
                        state["artifact_signature"] = signature
                        state["last_progress"] = time.monotonic()
                    state["next_scan"] = time.monotonic() + scan_interval

                if (no_progress_timeout
                        and time.monotonic() - state["last_progress"]
                        >= no_progress_timeout):
                    message = ("\n[%s] 오류: 이 MSA 갈래의 stdout/log 또는 artifact 변화가 "
                               "%s초 동안 없어 프로세스를 종료한다.\n"
                               % (datetime.now().isoformat(), no_progress_timeout))
                    state["handle"].write(message)
                    state["handle"].flush()
                    log("오류: MSA 갈래 %d가 %s초 동안 진행되지 않아 종료한다."
                        % (si, no_progress_timeout))
                    _stop_process(process)
                    state["handle"].close()
                    rcs[si] = 124
                    del states[si]
                    log("  MSA 갈래 %d 종료 (exit=124, 로그=%s)"
                        % (si, state["logfile"]))
            if states:
                time.sleep(_watchdog_poll_interval(no_progress_timeout))
    finally:
        for state in states.values():
            if state["process"].poll() is None:
                _stop_process(state["process"])
            try:
                state["handle"].close()
            except OSError:
                pass
    return rcs


def target_identity(target, args, kind, db_identity_record,
                    model_identity_record, image_identity_record):
    """Build the exact reuse contract for one target and one output kind."""
    config = {
        "extra_flags": list(args.extra_flag or []),
        "msa_gpus": bool(args.msa_gpus),
        "requested_msa_n_cpu": args.msa_n_cpu,
        "requested_msa_workers": args.msa_workers,
        "ligand_tokens": args.ligand_tokens,
        "estimated_bucket": needed_buckets([target["tokens"]])[0],
    }
    if kind == "final":
        config.update({
            "diffusion_samples": args.diffusion_samples,
            "recycles": args.recycles,
            "flash_attention": args.flash_attention,
            "no_prealloc": bool(args.no_prealloc),
            "unified_memory": bool(args.unified_memory),
        })
    return {
        "manifest_version": MANIFEST_VERSION,
        "kind": kind,
        "output_name": sanitise_name(target["name"]),
        "input": {
            # Both values are derived from the durable snapshot later mounted
            # read-only into AF3, never from the mutable source pathname.
            "semantic_json_sha256": target["semantic_json_sha256"],
            "json_sha256": target["input_json_sha256"],
            "source_file": target["source_path"].name,
        },
        # validate_fold_job currently rejects external *Path values.  Keep this
        # explicit so future sidecar support must add hashes before reuse works.
        "sidecars": [],
        "databases": db_identity_record,
        "model": model_identity_record if kind == "final" else None,
        "image": image_identity_record,
        "config": config,
    }


def manifest_matches(path, expected_identity):
    try:
        info = os.lstat(path)
    except OSError:
        return False
    if (not stat.S_ISREG(info.st_mode) or path.is_symlink()
            or info.st_nlink != 1):
        return False
    try:
        stored = read_fold_json(path)
    except Exception:
        return False
    return (isinstance(stored, dict)
            and stored.get("manifest_version") == MANIFEST_VERSION
            and stored.get("identity") == expected_identity)


def do_stage_msa(args, docker, flags, work, targets):
    """CPU MSA 단계: 입력을 여러 조각으로 나눠 컨테이너를 동시 실행한다."""
    msa_raw = work / "msa_raw"
    msa_raw.mkdir(parents=True, exist_ok=True)
    logs = work / "logs"
    logs.mkdir(parents=True, exist_ok=True)

    k = max(1, args.msa_workers)
    shards = [[] for _ in range(k)]
    for i, t in enumerate(targets):
        shards[i % k].append(t)
    shards = [s for s in shards if s]

    per_worker_cpu = msa_n_cpu(args, len(shards))
    log("MSA 단계: 대상 %d건을 %d갈래로 %s (갈래당 --jackhmmer_n_cpu=%d, "
        "실효 스레드 %d = %d x DB 4개)"
        % (len(targets), len(shards),
           "동시 실행" if len(shards) > 1 else "순차 처리",
           per_worker_cpu, per_worker_cpu * 4 * len(shards), per_worker_cpu))
    if len(shards) > 1:
        log("  주의: 갈래를 1개보다 늘리는 것은 실측상 이득이 없다 "
            "(32스레드 1갈래 0.890 대 2갈래 0.767 타깃/분). --msa-workers 1 권장.")

    before = {str(path): _file_generation(path) for path in _data_candidates(msa_raw)}
    procs = []
    t0 = time.time()
    for si, shard_targets in enumerate(shards):
        paths = [target["path"] for target in shard_targets]
        sd = stage_files(
            paths,
            work / ("stage_msa_%d" % si),
            mode="snapshot",
            expected_sha256={
                target["path"].name: target["input_json_sha256"]
                for target in shard_targets
            },
        )
        cmd = build_cmd(args, docker, "msa", sd, msa_raw, None,
                        n_cpu=per_worker_cpu, flags=flags,
                        container=container_name("msa%d" % si))
        lf = logs / ("msa_shard%d.log" % si)
        if args.dry_run:
            print("\n[드라이런] MSA 갈래 %d (%d건):\n  %s" % (si, len(paths), " ".join(cmd)))
            continue
        handle = open(lf, "a", encoding="utf-8")
        try:
            process = subprocess.Popen(cmd, stdout=handle, stderr=subprocess.STDOUT)
        except BaseException:
            handle.close()
            raise
        procs.append((si, process, lf, handle, shard_targets))
    if args.dry_run:
        return {}, 0.0

    rcs = wait_for_msa_processes(
        procs, msa_raw, no_progress_timeout=args.no_progress_timeout)
    successful_names = set()
    for si, _p, _lf, _handle, shard_targets in procs:
        rc = rcs[si]
        if rc == 0:
            successful_names.update(sanitise_name(t["name"]) for t in shard_targets)
    wall = time.time() - t0

    current = []
    for path in _data_candidates(msa_raw):
        stem = path.name[:-len(DATA_SUFFIX)]
        if stem not in successful_names:
            continue
        if before.get(str(path)) == _file_generation(path):
            continue
        ok, why = validate_msa_artifact(path, stem)
        if not ok:
            log("경고: 현재 MSA 산출물을 게시하지 않는다(%s): %s" % (path, why))
            continue
        current.append(path)
    identities = {sanitise_name(t["name"]): t["msa_identity"] for t in targets}
    n = collect_msa_outputs(msa_raw, work / "msa_store",
                            candidates=current, identities=identities)
    log("MSA 단계 완료: %.1f초, *_data.json %d건을 msa_store 에 보관" % (wall, n))
    if n == 0:
        log("경고: *_data.json 이 하나도 생기지 않았다. 로그를 확인하라: %s" % logs)
        log("      GPU 없이 실행되는 것이 원인이면 --msa-gpus 를 붙여 다시 시도하라.")
    return rcs, wall


def do_stage_infer(args, docker, flags, work, output_dir, targets):
    """GPU 추론 단계: msa_store 의 *_data.json 을 단일 프로세스가 순회한다."""
    store = work / "msa_store"
    logs = work / "logs"
    logs.mkdir(parents=True, exist_ok=True)

    ready, bad = [], []
    for t in targets:
        cand = store / (sanitise_name(t["name"]) + "_data.json")
        if not cand.is_file() or cand.is_symlink():
            bad.append((t["name"], "msa_store 에 *_data.json 이 없다 (MSA 단계 미실행/실패)"))
            continue
        if not msa_store_is_complete(
                work, t["name"], t["msa_identity"], args.trust_unverified_legacy):
            bad.append((t["name"], "MSA manifest가 현재 입력/DB/image/config와 일치하지 않는다"))
            continue
        ok, why = validate_data_json(cand, t["name"])
        if not ok:
            bad.append((t["name"], why))
            continue
        t["data_json"] = cand
        ready.append(t)

    if bad and args.dry_run:
        # 드라이런에서는 MSA를 아직 돌리지 않았으므로 전건 제외되는 것이 정상이다.
        log("(드라이런: msa_store 가 비어 있어 %d건이 검증에서 제외됨 - 정상)" % len(bad))
    elif bad:
        log("추론 입력 검증에서 %d건 제외:" % len(bad))
        for nm, why in bad[:20]:
            log("  - %s : %s" % (nm, why))
        with open(work / "invalid_infer_inputs.txt", "w", encoding="utf-8") as fh:
            for nm, why in bad:
                fh.write("%s\t%s\n" % (nm, why))

    if not ready:
        if args.dry_run:
            # 드라이런에서는 MSA 산출물이 아직 없는 것이 정상이다. 명령 형태만 보여준다.
            ex = sorted(targets, key=lambda t: t["tokens"])
            bks = needed_buckets([t["tokens"] for t in ex]) if ex else [256]
            cmd = build_cmd(args, docker, "infer", work / "stage_infer",
                            output_dir, bks, flags=flags,
                            container=container_name("infer_retry"))
            print("\n[드라이런] 추론 단계 (MSA 산출물이 아직 없어 명령 형태만 표시, "
                  "대상 %d건, 컨테이너 1회):\n  %s" % (len(ex), " ".join(cmd)))
            print("  ※ 실제 실행 시에는 msa_store 의 *_data.json 을 stage_infer 로 복사한 뒤"
                  " 위 명령을 돌린다.")
            return 0, [], 0.0, ex
        log("추론 가능한 입력이 없다. MSA 단계를 먼저 완료하라.")
        return None, [], 0.0, []

    ready.sort(key=lambda t: t["tokens"])
    bks = needed_buckets([t["tokens"] for t in ready])
    log("추론 단계: %d건, 토큰 %d~%d, 사용 버킷 %s (버킷 수 = 예상 재컴파일 횟수)"
        % (len(ready), ready[0]["tokens"], ready[-1]["tokens"], bks))

    # msa_store 원본이 아니라 '복사본' 을 넘긴다. AF3 가 입력을 덮어쓸 수 있다고 전해지는데
    # (이슈 #488 이 출처로 제시되었으나 원문 대조는 하지 않았다), MSA 산출물은 재계산이
    # 비싼 자산이므로 전제가 틀려도 복사본을 쓰는 편이 안전하다. stage_files 독스트링 참고.
    sd = stage_files([t["data_json"] for t in ready], work / "stage_infer", mode="copy")
    cmd = build_cmd(args, docker, "infer", sd, output_dir, bks, flags=flags,
                    container=container_name("infer"))

    if args.dry_run:
        print("\n[드라이런] 추론 단계 (%d건, 컨테이너 1회):\n  %s" % (len(ready), " ".join(cmd)))
        return 0, [], 0.0, ready

    lf = logs / ("infer_%s.log" % datetime.now().strftime("%Y%m%d_%H%M%S"))
    rc, timings, wall = run_streamed(
        cmd, lf, "추론",
        no_progress_timeout=args.no_progress_timeout,
        progress_roots=(output_dir,),
    )
    log("추론 단계 종료: exit=%d, 전체 %.1f초, 건당 평균 %.1f초 (측정값)"
        % (rc, wall, wall / max(1, len(ready))))
    return rc, timings, wall, ready


def do_stage_oneshot(args, docker, flags, work, output_dir, targets):
    """MSA+추론을 한 프로세스가 전수 순회 (가장 적은 변경으로 얻는 개선)."""
    logs = work / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    targets.sort(key=lambda t: t["tokens"])
    bks = needed_buckets([t["tokens"] for t in targets])
    log("oneshot 단계: %d건, 토큰 %d~%d, 사용 버킷 %s"
        % (len(targets), targets[0]["tokens"], targets[-1]["tokens"], bks))
    sd = stage_files(
        [t["path"] for t in targets],
        work / "stage_oneshot",
        mode="snapshot",
        expected_sha256={
            target["path"].name: target["input_json_sha256"]
            for target in targets
        },
    )
    cmd = build_cmd(args, docker, "oneshot", sd, output_dir, bks,
                    n_cpu=msa_n_cpu(args, 1), flags=flags,
                    container=container_name("oneshot"))
    if args.dry_run:
        print("\n[드라이런] oneshot 단계 (%d건, 컨테이너 1회):\n  %s"
              % (len(targets), " ".join(cmd)))
        return 0, [], 0.0
    lf = logs / ("oneshot_%s.log" % datetime.now().strftime("%Y%m%d_%H%M%S"))
    rc, timings, wall = run_streamed(
        cmd, lf, "oneshot",
        no_progress_timeout=args.no_progress_timeout,
        progress_roots=(output_dir,),
    )
    log("oneshot 종료: exit=%d, 전체 %.1f초, 건당 평균 %.1f초 (측정값)"
        % (rc, wall, wall / max(1, len(targets))))
    return rc, timings, wall


def acquire_run_locks(output_dir, work):
    """Acquire shared resources in canonical path order to avoid AB/BA deadlock."""
    paths = {
        output_dir / ".run_af3_batch.lock",  # common with the preferred runner
        work / ".run_af3_batch.work.lock",
    }
    locks = []
    try:
        for lock_path in sorted(paths, key=lambda p: str(p.resolve(strict=False))):
            flags = os.O_RDWR | os.O_CREAT
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(lock_path, flags, 0o600)
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                os.close(descriptor)
                raise OSError("잠금 경로가 단일 일반 파일이 아니다: %s" % lock_path)
            handle = os.fdopen(descriptor, "r+", encoding="utf-8")
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BaseException:
                handle.close()
                raise
            handle.seek(0)
            handle.truncate()
            handle.write("host=%s pid=%d\n" % (socket.gethostname(), os.getpid()))
            handle.flush()
            os.fsync(handle.fileno())
            locks.append(handle)
    except BaseException:
        for handle in reversed(locks):
            handle.close()
        raise
    return locks


def _gpu_lease_root():
    uid = os.getuid() if hasattr(os, "getuid") else 0
    configured = os.environ.get("AF3_GPU_LEASE_DIR")
    root = (Path(configured).expanduser().absolute() if configured else
            Path(tempfile.gettempdir()) / ("kang-af3-gpu-leases-%d" % uid))
    root.mkdir(mode=0o700, exist_ok=True)
    info = os.lstat(root)
    if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or info.st_mode & 0o077
            or (hasattr(os, "getuid") and info.st_uid != os.getuid())):
        raise OSError("GPU lease 폴더가 안전하지 않다: %s" % root)
    return root


def _visible_gpu_keys():
    try:
        proc = subprocess.run(
            ["nvidia-smi", "--query-gpu=uuid", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return ["unknown-all-devices"]
    keys = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    return sorted(set(keys)) if proc.returncode == 0 and keys else ["unknown-all-devices"]


GPU_INVENTORY_LEASE_KEY = "inventory-global-v1"


def _acquire_legacy_gpu_lease(key, shared=False):
    """Acquire one hardened lease in the namespace shared by both runners."""
    safe = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
    path = _gpu_lease_root() / ("gpu-%s.lock" % safe)
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        os.close(descriptor)
        raise OSError("GPU lease가 단일 일반 파일이 아니다: %s" % path)
    try:
        lock_mode = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
        fcntl.flock(descriptor, lock_mode | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(descriptor)
        raise
    if not shared:
        os.ftruncate(descriptor, 0)
        os.write(
            descriptor,
            ("host=%s pid=%d device=%s\n" %
             (socket.gethostname(), os.getpid(), key)).encode("utf-8"),
        )
        os.fsync(descriptor)
    return descriptor


@contextmanager
def gpu_leases_for_legacy(enabled):
    """Lease every GPU exposed by legacy --gpus all using the preferred namespace."""
    if not enabled:
        yield
        return
    descriptors = []
    try:
        keys = _visible_gpu_keys()
        unknown = "unknown-all-devices" in keys
        # Canonical order: inventory gate first, then sorted device UUIDs.
        # Unknown --gpus all owners take the gate exclusively for their full
        # lifetime; enumerated owners share it and exclusively lease each UUID.
        descriptors.append(_acquire_legacy_gpu_lease(
            GPU_INVENTORY_LEASE_KEY, shared=not unknown))
        if not unknown:
            for key in sorted(set(keys)):
                descriptors.append(_acquire_legacy_gpu_lease(key))
        yield
    finally:
        # Container removal is part of the reservation lifetime.  Releasing
        # first would let a conflicting run start while a timed-out container
        # can still own CUDA memory.  The outer process-finally repeats this
        # bounded cleanup harmlessly as a last-resort signal path safeguard.
        teardown_containers()
        for descriptor in reversed(descriptors):
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)


# =============================================================================
# main
# =============================================================================
def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="AlphaFold 3 최적화 배치 러너 (단일 프로세스 + 2단계 분리)",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--name", default=None,
                   help="작업 이름. <name>_in / <name>_out / <name>_work 를 쓴다 (예: vhh_001)")
    p.add_argument("--input-dir", default=None, help="입력 JSON 폴더 (--name 대신 직접 지정)")
    p.add_argument("--output-dir", default=None, help="결과 폴더 (--name 대신 직접 지정)")
    p.add_argument("--work-dir", default=None, help="작업 폴더 (기본: <name>_work)")
    p.add_argument("--stage", default="both",
                   choices=["msa", "infer", "both", "oneshot"],
                   help="msa=MSA만, infer=추론만, both=MSA후추론(권장), oneshot=한프로세스에서둘다")
    p.add_argument("--image", default=os.environ.get("AF3_IMAGE", "alphafold3"))
    p.add_argument(
        "--db-dir",
        action="append",
        default=None,
        help="AF3 DB root. overlay/fallback 우선순서대로 반복 가능",
    )
    p.add_argument("--model-dir", default=os.environ.get(
        "AF3_MODEL_DIR", str(Path.home() / "af3_models")))
    p.add_argument("--cache-dir", default=os.environ.get(
        "AF3_CACHE_DIR", str(Path.home() / "af3_jax_cache")),
        help="JAX 컴파일 캐시 폴더. 빈 문자열이면 사용하지 않는다")
    p.add_argument("--msa-workers", type=int, default=1,
                   help="MSA 단계 동시 실행 갈래 수. 기본 1 (실측 근거: 갈래를 늘리면 "
                        "같은 스레드 총량에서 오히려 느리다. 32스레드 1갈래 0.890 타깃/분 "
                        "대 2갈래 0.767). AF3 가 이미 체인당 DB 4개를 내부 병렬 검색하므로 "
                        "병렬성은 이미 안에 있다. 바꾸지 않는 편이 낫다")
    p.add_argument("--msa-n-cpu", type=int, default=None,
                   help="--jackhmmer_n_cpu / --nhmmer_n_cpu 값을 직접 지정. "
                        "미지정이면 min(코어수/2, 8) 을 쓴다 (실측 최적)")
    p.add_argument("--msa-gpus", action="store_true",
                   help="MSA 단계에도 --gpus all 을 붙인다 (GPU 없이 죽는 경우만)")
    p.add_argument("--diffusion-samples", type=int, default=None,
                   help="확산 샘플 수 (기본값 5). 경량 스크리닝은 1 권장")
    p.add_argument("--recycles", type=int, default=None,
                   help="리사이클 수 (기본값 10). 경량 스크리닝은 3 권장")
    p.add_argument("--flash-attention", default=None,
                   help="flash attention 구현 지정 (예: xla). 보통 건드리지 않는다")
    p.add_argument("--no-prealloc", action="store_true",
                   help="VRAM 선점을 끈다 (실사용량 확인용)")
    p.add_argument("--unified-memory", action="store_true",
                   help="OOM 시 호스트 RAM으로 흘려보낸다 (느려지지만 안 죽는다)")
    p.add_argument("--limit", type=int, default=None,
                   help="대상을 토큰 수로 정렬한 뒤 가장 짧은 N건만 실행 (스모크용). "
                        "완료분을 건너뛴 다음에 적용되므로 재실행하면 대상이 달라진다 "
                        "- 처리시간 계획용 측정에는 쓰지 마라")
    p.add_argument("--retry", action="store_true", help="지난 실행에서 실패한 것만 다시 한다")
    p.add_argument("--no-skip", action="store_true", help="이미 끝난 것도 다시 계산한다")
    p.add_argument("--keep-partial", action="store_true",
                   help="미완성 결과 폴더를 partial/ 로 옮기지 않고 그대로 둔다")
    p.add_argument("--lenient-done", action="store_true",
                   help="완료 판정을 2026-08 이전 방식으로 되돌린다: 완료 표식 "
                        "(_summary_confidences.json / _ranking_scores.csv / _model.cif) "
                        "중 하나만 있어도 완료로 본다. 기본은 3종 모두 있고 크기가 "
                        "0보다 클 때만 완료다. 예전 출력 폴더를 다시 돌리지 않으려면 쓴다")
    p.add_argument(
        "--trust-unverified-legacy", action="store_true",
        help="manifest가 없는 옛 MSA/최종 결과를 현재 입력과 같다고 명시적으로 신뢰한다. "
             "기본값은 재사용하지 않음(과학 데이터 오귀속 방지)",
    )
    p.add_argument(
        "--allow-unsealed-db", action="store_true",
        help="호환성 전용: seal 없는 full DB를 metadata-only로 식별한다. 기본값은 거부",
    )
    p.add_argument("--docker", default=None, help="도커 실행 명령 강제 지정 (예: 'sudo docker')")
    p.add_argument("--extra-flag", action="append", default=[],
                   help="run_alphafold.py 에 그대로 넘길 추가 플래그. 반드시 등호 형식으로 "
                        "쓸 것: --extra-flag=--save_embeddings (공백으로 쓰면 argparse 가 "
                        "거부한다). 반복 사용 가능")
    p.add_argument("--ligand-tokens", type=int, default=30,
                   help="리간드 1개를 몇 토큰으로 어림할지 (정렬/버킷 선택에만 영향)")
    p.add_argument("--no-probe", action="store_true", help="플래그 지원 여부 탐지를 건너뛴다")
    p.add_argument(
        "--no-progress-timeout", type=int, default=7200,
        help="stdout/log와 결과 artifact가 모두 변하지 않는 최대 초. 기본 7200(2시간), "
             "0이면 무진행 감시를 끈다",
    )
    p.add_argument("--dry-run", action="store_true",
                   help="실제 실행 없이 조립된 docker 명령만 출력한다")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    base = Path.cwd()

    for label, value in (
        ("--msa-workers", args.msa_workers),
        ("--msa-n-cpu", args.msa_n_cpu),
        ("--diffusion-samples", args.diffusion_samples),
        ("--recycles", args.recycles),
        ("--limit", args.limit),
        ("--ligand-tokens", args.ligand_tokens),
    ):
        if value is not None and value <= 0:
            print("오류: %s 는 1 이상이어야 한다." % label)
            return 2
    if args.no_progress_timeout < 0:
        print("오류: --no-progress-timeout 은 0 이상이어야 한다.")
        return 2

    if args.input_dir:
        input_dir = Path(args.input_dir).resolve()
    elif args.name:
        input_dir = (base / ("%s_in" % args.name)).resolve()
    else:
        print("오류: --name 또는 --input-dir 중 하나는 반드시 지정해야 한다.")
        return 2

    if args.output_dir:
        output_dir = Path(args.output_dir).resolve()
    elif args.name:
        output_dir = (base / ("%s_out" % args.name)).resolve()
    else:
        output_dir = input_dir.parent / (input_dir.name.replace("_in", "") + "_out")

    work = Path(args.work_dir).resolve() if args.work_dir else \
        output_dir.parent / (output_dir.name.replace("_out", "") + "_work")

    if args.cache_dir:
        args.cache_dir = str(Path(args.cache_dir).expanduser())
        problem = cache_dir_problem(args.cache_dir)
        if problem:
            log("경고: " + problem)
            log("      이번 실행은 캐시 없이 진행한다 (첫 입력이 느려진다).")
            args.cache_dir = None
    db_values = args.db_dir or [
        os.environ.get("AF3_DB_DIR", str(Path.home() / "public_databases_full"))
    ]
    args.db_dirs = [str(Path(value).expanduser()) for value in db_values]
    # Keep the first-root attribute for old integrations that inspect it.
    args.db_dir = args.db_dirs[0]
    args.model_dir = str(Path(args.model_dir).expanduser())

    print("=" * 79)
    print(" AlphaFold 3 최적화 배치 러너")
    print(" 입력 : %s" % input_dir)
    print(" 출력 : %s" % output_dir)
    print(" 작업 : %s" % work)
    print(" 단계 : %s   이미지: %s" % (args.stage, args.image))
    print("=" * 79)

    # ---- 필수 경로 확인 -------------------------------------------------
    missing = []
    db_report = {"resolved": {}}
    model_report = {}
    if not input_dir.is_dir():
        missing.append("입력 폴더 없음: %s" % input_dir)
    if stage_uses_databases(args.stage):
        db_report = verify_database_roots(args.db_dirs)
        if not db_report["ok"]:
            missing.extend("DB 오류: %s" % error for error in db_report["errors"])
    if stage_uses_model(args.stage):
        model_report = verify_model_dir(args.model_dir)
        if not model_report["ok"]:
            missing.extend("가중치 오류: %s" % error for error in model_report["errors"])
        for warning in model_report["warnings"]:
            log("경고: %s" % warning)
    if missing:
        for m in missing:
            print("오류: %s" % m)
        if not args.dry_run:
            return 1
        print("(드라이런이므로 경로 오류를 무시하고 명령 조립만 계속한다)")

    if output_dir.is_symlink() or work.is_symlink():
        print("오류: output/work 폴더로 symlink를 사용할 수 없다.")
        return 1
    output_dir.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    try:
        # Keep both handles alive until main returns.  The output filename and
        # owner format are shared with run_af3_batch_improved.py.
        lock_path = output_dir / ".run_af3_batch.lock"
        run_locks = acquire_run_locks(output_dir, work)
    except (OSError, BlockingIOError) as exc:
        print("오류: 같은 output/work 자원을 다른 실행이 사용 중이거나 잠금 경로가 안전하지 않다: %s" % exc)
        return 1
    if args.cache_dir:
        # 컴파일 캐시는 '있으면 좋은' 것이다. 만들 수 없으면 경고만 남기고 캐시 없이 진행한다.
        try:
            Path(args.cache_dir).mkdir(parents=True, exist_ok=True)
        except OSError as e:
            log("경고: 컴파일 캐시 폴더를 만들 수 없다(%s). 캐시 없이 진행한다." % e)
            log("      다른 위치를 쓰려면 --cache-dir <경로> 를 지정하라.")
            args.cache_dir = ""

    # ---- 입력 목록 만들기 -----------------------------------------------
    json_files = sorted(p for p in input_dir.glob("*.json")
                        if not p.name.endswith("_data.json")
                        and not is_sidecar(p.name))
    n_sidecar = sum(1 for p in input_dir.glob("*.json") if is_sidecar(p.name))
    if n_sidecar:
        log("AppleDouble/숨은 파일 %d건을 제외했다 (macOS 에서 만든 tar 를 리눅스에서 "
            "풀면 생기는 '._*.json' 사이드카다. ls 에는 안 보이지만 glob 에는 잡히고 "
            "UTF-8 이 아니어서 읽으면 죽는다)." % n_sidecar)
    if not json_files:
        print("오류: '%s' 에 입력 JSON 이 없다." % input_dir)
        return 1

    # Freeze every mutable user input before parsing, identity construction, or
    # reuse decisions.  Subsequent stages copy only these private snapshots, so
    # the identity we commit describes the exact generation AF3 consumes.
    try:
        input_snapshots = stage_files(
            json_files, work / "stage_inputs", mode="snapshot")
    except (OSError, ValueError) as exc:
        log("오류: 입력 JSON snapshot 생성 실패: %s" % exc)
        return 2

    targets, unreadable = [], []
    for source_path in json_files:
        p = input_snapshots / source_path.name
        try:
            obj = read_fold_json(p)
        except Exception as e:
            unreadable.append((p.name, str(e)))
            continue
        schema_error = validate_fold_job(obj)
        if schema_error:
            unreadable.append((p.name, schema_error))
            continue
        name = obj["name"]
        targets.append({"path": p, "source_path": source_path,
                        "name": name, "obj": obj,
                        "semantic_json_sha256": semantic_json_sha256(obj),
                        "input_json_sha256": sha256_file(p),
                        "tokens": count_tokens(obj, args.ligand_tokens)})
    if unreadable:
        log("오류: 사용할 수 없는 JSON %d건:" % len(unreadable))
        for name, why in unreadable[:20]:
            log("  - %s: %s" % (name, why))
        return 2

    seen_names = {}
    for target in targets:
        output_name = sanitise_name(target["name"])
        if output_name in seen_names:
            log("오류: AF3 출력 이름 충돌: %s <-> %s"
                % (seen_names[output_name], target["path"].name))
            return 2
        seen_names[output_name] = target["path"].name

    log("입력 JSON %d건 확인. 토큰 수로 정렬한다 (로그 가독성 목적. "
        "정렬 자체의 시간 이득은 실측 0.00초/건이다)." % len(targets))
    targets.sort(key=lambda t: t["tokens"])

    # Identity must be resolved before completion checks.  Otherwise a mutable
    # image tag or replaced model/DB can make name-only output look reusable.
    docker = find_docker(args.docker)
    if docker is not None:
        _TEARDOWN_DOCKER.append(list(docker))
    if docker is None:
        if not args.dry_run:
            print("오류: docker 명령을 찾을 수 없다.")
            return 1
        docker = ["docker"]
        log("(드라이런: docker 가 없으므로 'docker' 로 가정한다)")
    try:
        image_record = image_identity(docker, args.image, args.dry_run)
    except RuntimeError as exc:
        print("오류: %s" % exc)
        return 1
    try:
        db_record = (
            database_identity(
                db_report, args.db_dirs, allow_unsealed=args.allow_unsealed_db
            )
            if stage_uses_databases(args.stage)
            else {"roots": []}
        )
    except (OSError, ValueError) as exc:
        print("오류: DB content identity 검증 실패: %s" % exc)
        return 1
    model_record = model_identity(args.model_dir) if stage_uses_model(args.stage) else None
    for target in targets:
        target["msa_identity"] = target_identity(
            target, args, "msa", db_record, None, image_record)
        target["final_identity"] = target_identity(
            target, args, "final", db_record, model_record, image_record)
        target["final_identity"]["msa_identity_sha256"] = semantic_json_sha256(
            target["msa_identity"])

    # ---- 상태 파일 / 재시도 ---------------------------------------------
    state_path = work / "state.json"
    state = {"failed": [], "history": []}
    if state_path.exists():
        try:
            state.update(read_fold_json(state_path))
        except Exception:
            pass

    if args.retry:
        keep = set(state.get("failed", []))
        if not keep:
            log("재시도 목록이 비어 있다. 실패 기록이 없으므로 그대로 종료한다.")
            return 0
        targets = [t for t in targets if t["name"] in keep]
        log("재시도 모드: %d건만 실행한다." % len(targets))

    # ---- 이미 끝난 것 건너뛰기 -------------------------------------------
    # 판정 기준은 실행 단계에 맞춘다: --stage msa 는 _data.json 만 보고,
    # 나머지는 정식 산출물 3종을 본다 (stage_check_mode 참고).
    chk_mode = stage_check_mode(args.stage)
    output_index = index_result_dirs(output_dir)
    if not args.no_skip:
        pending, done = [], 0
        log("완료 판정 기준: %s"
            % ("<타깃>_data.json 존재 (--stage msa)" if chk_mode == "data"
               else "_ranking_scores.csv + _model.cif(.zst) + _summary_confidences.json "
                    "3종 모두 (크기 0 제외)"))
        if args.lenient_done:
            log("  --lenient-done: 완료 표식 하나만 있어도 완료로 본다 (2026-08 이전 동작)")
        if args.trust_unverified_legacy:
            log("  --trust-unverified-legacy: manifest 없는 옛 산출물의 동일성을 사용자가 책임지고 신뢰")
        for t in targets:
            if chk_mode == "data":
                if msa_store_is_complete(
                        work, t["name"], t["msa_identity"],
                        args.trust_unverified_legacy):
                    done += 1
                    continue
                dirs = []
            else:
                dirs = find_result_dirs(output_dir, t["name"], output_index)
                if any(result_is_reusable(
                        d, t["name"], t["final_identity"],
                        args.trust_unverified_legacy, args.lenient_done)
                       for d in dirs):
                    done += 1
                    continue
            # 미완성 폴더가 있으면 AF3 가 타임스탬프 폴더를 새로 만들어 결과가 흩어진다.
            # 삭제하지 않고 partial/ 로 옮겨둔다.
            for d in dirs:
                if (not outdir_is_complete(d, mode=chk_mode, lenient=args.lenient_done)
                        and not args.keep_partial and not args.dry_run):
                    ptdir = work / "partial"
                    ptdir.mkdir(parents=True, exist_ok=True)
                    dest = ptdir / (
                        "%s_%s_%d" %
                        (d.name, datetime.now().strftime("%Y%m%d_%H%M%S_%f"), os.getpid())
                    )
                    shutil.move(str(d), str(dest))
                    log("  미완성 결과를 옮김: %s -> %s" % (d.name, dest))
            pending.append(t)
        log("완료 %d건 건너뜀, 남은 대상 %d건" % (done, len(pending)))
        targets = pending

    if args.limit:
        targets = targets[:args.limit]
        log("--limit 적용: %d건만 실행" % len(targets))

    if not targets:
        log("실행할 대상이 없다. 모두 완료된 상태다.")
        return 0

    # ---- 도커 / 플래그 준비 ----------------------------------------------
    flags = None
    if not args.no_probe and not args.dry_run:
        log("이미지가 지원하는 플래그를 --help 로 확인한다 (1회, 수십 초)")
        flags = probe_flags(docker, args.image)
        if flags is None:
            return 1
        need = ["input_dir", "buckets", "num_diffusion_samples",
                "num_recycles", "jax_compilation_cache_dir",
                "jackhmmer_n_cpu", "run_inference", "run_data_pipeline"]
        log("  지원: %s" % ", ".join(f for f in need if f in flags))
        absent = [f for f in need if f not in flags]
        if absent:
            log("  미지원(전달하지 않음): %s" % ", ".join(absent))
        if "input_dir" not in flags:
            log("치명적: 이 이미지는 --input_dir 을 지원하지 않는다. 단일 프로세스 순회가"
                " 불가능하므로 이미지를 최신 버전으로 다시 만들어야 한다.")
            return 1
    if args.msa_gpus:
        log("--msa-gpus: MSA 단계에도 GPU를 붙인다.")

    # ---- 실행 -------------------------------------------------------------
    t_start = time.time()
    timings, rc_all = [], 0
    ran = list(targets)
    output_index = index_result_dirs(output_dir)
    output_before = {}
    if args.stage != "msa":
        for target in ran:
            for directory in find_result_dirs(output_dir, target["name"], output_index):
                output_before[str(directory)] = artifact_snapshot(directory, target["name"])

    needs_gpu_lease = (
        args.stage in ("infer", "both", "oneshot")
        or (args.stage == "msa" and args.msa_gpus)
    ) and not args.dry_run
    try:
        with gpu_leases_for_legacy(needs_gpu_lease):
            if args.stage == "oneshot":
                rc, timings, wall = do_stage_oneshot(args, docker, flags, work, output_dir, targets)
                rc_all = rc or 0
            else:
                if args.stage in ("msa", "both"):
                    rcs, _ = do_stage_msa(args, docker, flags, work, targets)
                    if rcs and any(v != 0 for v in rcs.values()):
                        log("오류: 하나 이상의 MSA 갈래가 0이 아닌 코드로 끝났다. 로그를 확인하라.")
                        rc_all = 1
                if args.stage in ("infer", "both"):
                    rc, timings, wall, _ready = do_stage_infer(
                        args, docker, flags, work, output_dir, targets)
                    rc_all = rc_all or (rc or 0)
    except (OSError, BlockingIOError) as exc:
        log("오류: 다른 Kang_AF3 실행이 GPU lease를 보유하거나 lease가 안전하지 않다: %s" % exc)
        return 2

    if args.dry_run:
        print("\n[드라이런 종료] 실제 실행은 하지 않았다. 위 명령을 그대로 복사해 써도 된다.")
        return 0

    # Bless only complete artifacts that are new or changed in this invocation.
    # This prevents a failed rerun from attaching today's manifest to yesterday's
    # canonical structure.
    output_index = index_result_dirs(output_dir)
    if args.stage != "msa" and rc_all == 0:
        for target in ran:
            for directory in find_result_dirs(output_dir, target["name"], output_index):
                current = artifact_snapshot(directory, target["name"])
                if not current or output_before.get(str(directory)) == current:
                    continue
                try:
                    publish_result_manifest(
                        directory, target["name"], target["final_identity"])
                except OSError as exc:
                    log("오류: 결과 manifest 게시 실패(%s): %s" % (directory, exc))
                    rc_all = rc_all or 1
    elif args.stage != "msa" and rc_all != 0:
        # A nonzero producer can leave complete-looking finals.  Without a
        # per-target success signal those artifacts cannot be blessed.  Preserve
        # every changed directory for diagnosis and keep it pending.
        partial_dir = work / "partial"
        partial_dir.mkdir(parents=True, exist_ok=True)
        for target in ran:
            for directory in find_result_dirs(output_dir, target["name"], output_index):
                current = artifact_snapshot(directory, target["name"])
                if not current or output_before.get(str(directory)) == current:
                    continue
                destination = partial_dir / (
                    "%s_nonzero_%s_%d" % (
                        directory.name,
                        datetime.now().strftime("%Y%m%d_%H%M%S_%f"),
                        os.getpid(),
                    )
                )
                shutil.move(str(directory), str(destination))
                log("nonzero producer 결과를 manifest 없이 보존 이동: %s -> %s" %
                    (directory, destination))

    output_index = index_result_dirs(output_dir)

    # ---- 결과 정리 --------------------------------------------------------
    tmap = {}
    for nm, a, b in timings:
        tmap[sanitise_name(nm)] = b - a

    rows, failed = [], []
    for t in ran:
        dirs = find_result_dirs(output_dir, t["name"], output_index)
        if args.stage == "msa":
            ok = msa_store_is_complete(
                work, t["name"], t["msa_identity"], args.trust_unverified_legacy)
            reusable_dirs = []
        else:
            reusable_dirs = [d for d in dirs if result_is_reusable(
                d, t["name"], t["final_identity"],
                args.trust_unverified_legacy, args.lenient_done)]
            reusable_dirs.sort(
                key=lambda d: dir_run_time(d, resolve_result_dir(d, mode="full")),
                reverse=True,
            )
            ok = bool(reusable_dirs)
        if not ok:
            failed.append(t["name"])
        rows.append({
            "name": t["name"],
            "tokens": t["tokens"],
            "bucket": needed_buckets([t["tokens"]])[0],
            "status": "완료" if ok else "실패",
            "wall_seconds": ("%.1f" % tmap[sanitise_name(t["name"])])
                            if sanitise_name(t["name"]) in tmap else "",
            "output": (
                str(work / "msa_store" / (sanitise_name(t["name"]) + "_data.json"))
                if args.stage == "msa" and ok
                else str(reusable_dirs[0]) if reusable_dirs else ""
            ),
        })

    csv_path = work / "run_summary.csv"
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=["name", "tokens", "bucket", "status",
                                           "wall_seconds", "output"])
        w.writeheader()
        w.writerows(
            {key: csv_safe_cell(value) for key, value in row.items()}
            for row in rows
        )

    state["failed"] = failed
    state["history"].append({
        "time": datetime.now().isoformat(timespec="seconds"),
        "stage": args.stage, "n_targets": len(ran),
        "n_failed": len(failed), "wall_seconds": round(time.time() - t_start, 1),
        "diffusion_samples": args.diffusion_samples, "recycles": args.recycles,
    })
    with open(state_path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)

    wall = time.time() - t_start
    n_ok = len(ran) - len(failed)
    print()
    print("=" * 79)
    print(" 실행 요약 (측정값)")
    print("  전체 시간   : %.1f초 (%.2f시간)" % (wall, wall / 3600.0))
    print("  대상 / 성공 : %d건 / %d건" % (len(ran), n_ok))
    if n_ok:
        print("  건당 평균   : %.1f초" % (wall / n_ok))
        print("  2000건 환산 : %.1f시간 (추정, 같은 조건이 유지된다는 가정)"
              % (wall / n_ok * 2000 / 3600.0))
    if failed:
        print("  실패 %d건: %s" % (len(failed), ", ".join(failed[:10])))
        batch_script = shlex.quote(str(Path(__file__).resolve()))
        print("  재시도    : python3 %s --name <이름> --stage %s --retry"
              % (batch_script, args.stage))
        rc_all = rc_all or 1
    print("  요약 CSV    : %s" % csv_path)
    print("  로그        : %s" % (work / "logs"))
    print("=" * 79)
    return rc_all


def _terminate(signum, _frame):
    """SIGTERM 을 예외로 바꿔 teardown 이 컨테이너를 지우게 한다."""
    raise KeyboardInterrupt("signal %d" % signum)


if __name__ == "__main__":
    import signal

    signal.signal(signal.SIGTERM, _terminate)
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log("")
        log("중단됐다. 이 실행이 띄운 컨테이너를 정리한다.")
        sys.exit(130)
    finally:
        teardown_containers()
