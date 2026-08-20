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
import csv
import json
import os
import re
import shutil
import string
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

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
C_MODEL = "/root/af3_models"
C_IN = "/root/af3_in"
C_OUT = "/root/af3_out"
C_CACHE = "/root/af3_cache"


def log(msg):
    print("[%s] %s" % (datetime.now().strftime("%H:%M:%S"), msg), flush=True)


# =============================================================================
# 도커 실행 방법 결정
# =============================================================================
def find_docker(force=None):
    """sudo 없이 docker 가 되면 그대로, 아니면 sudo 를 붙인다."""
    if force:
        return force.split()
    if shutil.which("docker") is None:
        return None
    try:
        r = subprocess.run(["docker", "info"], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=60)
        if r.returncode == 0:
            return ["docker"]
    except Exception:
        pass
    log("경고: sudo 없이 docker 를 쓸 수 없다. sudo 를 붙여서 실행한다.")
    log("      영구 해결: sudo usermod -aG docker $USER  실행 후 재로그인")
    return ["sudo", "docker"]


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
            return None
        return flags
    except Exception as e:
        log("경고: 플래그 탐지 실패(%s). 지원 여부를 확인하지 않고 진행한다." % e)
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
    """결과 폴더의 '실행 시각' 을 비교 가능한 숫자로 준다. 최신 판정에 쓴다.

    1순위: 폴더명의 AF3 재실행 접미사(_YYYYmmdd_HHMMSS). AF3 가 직접 찍은 값이라
           파일 복사/rsync 로 mtime 이 바뀌어도 살아남는다.
    2순위: 정식 산출물의 mtime 중 가장 늦은 것 (접미사 없는 첫 실행 폴더).
    두 경로 모두 실패하면 0.0 (가장 오래된 것으로 취급).
    """
    ts = info.get("run_ts")
    if ts:
        try:
            return time.mktime(time.strptime(ts, "%Y%m%d_%H%M%S"))
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
        return 0.0
    return best


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


def find_result_dirs(output_dir, fold_name):
    """한 타깃의 결과 폴더를 모두 찾는다 (없으면 빈 목록).

    AF3 는 출력 폴더가 비어 있지 않으면 <name>_<YYYYmmdd_HHMMSS> 폴더를 새로 만든다.
    2026-08 수정: 예전에는 glob(sanitise_name(name) + "_*") 로 찾았다. 두 가지가
    틀렸다.
      1) sanitise_name 이 소문자화를 해서 리눅스에서 대문자 타깃을 못 찾았다.
      2) glob 접두어 방식은 'VHH_004' 를 찾을 때 'VHH_004_variantB' 같은 별개
         타깃까지 잡았다. 이제 폴더 안 산출물의 stem 이 타깃명과 같은지 확인한다.
    """
    want = sanitise_name(fold_name)
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


# =============================================================================
# 파일 스테이징
# =============================================================================
def stage_files(paths, dest, mode="link"):
    """실행 대상만 모아둔 임시 입력 폴더를 만든다.

    mode="link": 하드링크 (원본 JSON 은 읽기만 하므로 안전, 디스크 절약)
    mode="copy": 복사 (msa_store 보호용. AF3 가 입력을 덮어쓸 수 있다고 전해지므로
                 -- 이슈 #488 이 출처로 제시되었으나 원문 대조는 하지 않았다 --
                 추론 단계 입력은 원본이 아니라 복사본을 넘긴다. MSA 산출물은
                 재계산이 비싼 자산이므로, 전제가 틀려도 복사본을 쓰는 편이 안전하다)
    """
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)
    for p in paths:
        target = dest / p.name
        if mode == "link":
            try:
                os.link(p, target)
                continue
            except OSError:
                pass
        shutil.copy2(p, target)
    return dest


# =============================================================================
# 추론 입력(_data.json) 방어 검증
# (이슈 #485 / #488 이 출처로 제시된 전제에 대한 대비. 원문 대조는 하지 않았으나,
#  아래 검사는 전제가 맞든 틀리든 손해가 없는 종류다 -- 실패할 입력을 미리 걸러낼 뿐이다.)
# =============================================================================
def validate_data_json(path):
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


# =============================================================================
# 컨테이너 실행
# =============================================================================
def build_cmd(args, docker, stage, input_dir, output_dir, buckets,
              extra_env=None, n_cpu=None, flags=None):
    """docker run 명령을 조립한다. stage 에 따라 GPU 사용/파이프라인 on-off 가 다르다."""
    cmd = list(docker) + ["run", "--rm"]

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

    cmd += ["-v", "%s:%s" % (absp(args.db_dir), C_DB),
            "-v", "%s:%s" % (absp(args.model_dir), C_MODEL),
            "-v", "%s:%s" % (absp(input_dir), C_IN),
            "-v", "%s:%s" % (absp(output_dir), C_OUT)]
    if args.cache_dir:
        cmd += ["-v", "%s:%s" % (absp(args.cache_dir), C_CACHE)]

    cmd += [args.image, "python", "run_alphafold.py",
            "--input_dir=%s" % C_IN,
            "--model_dir=%s" % C_MODEL,
            "--db_dir=%s" % C_DB,
            "--output_dir=%s" % C_OUT]

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
TOOK = re.compile(r"took\s+([\d.]+)\s+seconds")


def run_streamed(cmd, logfile, tag):
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
                                stderr=subprocess.STDOUT, text=True,
                                bufsize=1)
        for raw in proc.stdout:
            lf.write(raw)
            m = FOLD_START.search(raw)
            if m:
                now = time.time()
                if cur is not None:
                    timings.append((cur, cur_t0, now))
                cur, cur_t0 = m.group(1).strip(), now
                log("  [%s] 진행: %s" % (tag, cur))
            elif TOOK.search(raw):
                lf.flush()
        proc.wait()
        if cur is not None:
            timings.append((cur, cur_t0, time.time()))
        lf.write("\n[exit=%d, wall=%.1fs]\n" % (proc.returncode, time.time() - t0))
    return proc.returncode, timings, time.time() - t0


# =============================================================================
# 단계 실행
# =============================================================================
def collect_msa_outputs(msa_raw, msa_store):
    """MSA 단계 출력에서 *_data.json 만 골라 재사용 보관소로 모은다."""
    msa_store.mkdir(parents=True, exist_ok=True)
    moved = 0
    for p in sorted(msa_raw.rglob("*_data.json")):
        if is_sidecar(p.name):
            continue
        target = msa_store / p.name
        if target.exists() and target.stat().st_size >= p.stat().st_size:
            continue
        shutil.copy2(p, target)
        moved += 1
    return moved


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


def do_stage_msa(args, docker, flags, work, targets):
    """CPU MSA 단계: 입력을 여러 조각으로 나눠 컨테이너를 동시 실행한다."""
    msa_raw = work / "msa_raw"
    msa_raw.mkdir(parents=True, exist_ok=True)
    logs = work / "logs"
    logs.mkdir(parents=True, exist_ok=True)

    k = max(1, args.msa_workers)
    shards = [[] for _ in range(k)]
    for i, t in enumerate(targets):
        shards[i % k].append(t["path"])
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

    procs = []
    t0 = time.time()
    for si, paths in enumerate(shards):
        sd = stage_files(paths, work / ("stage_msa_%d" % si), mode="link")
        cmd = build_cmd(args, docker, "msa", sd, msa_raw, None,
                        n_cpu=per_worker_cpu, flags=flags)
        lf = logs / ("msa_shard%d.log" % si)
        if args.dry_run:
            print("\n[드라이런] MSA 갈래 %d (%d건):\n  %s" % (si, len(paths), " ".join(cmd)))
            continue
        procs.append((si, subprocess.Popen(cmd, stdout=open(lf, "a"),
                                           stderr=subprocess.STDOUT), lf))
    if args.dry_run:
        return {}, 0.0

    rcs = {}
    for si, p, lf in procs:
        rc = p.wait()
        rcs[si] = rc
        log("  MSA 갈래 %d 종료 (exit=%d, 로그=%s)" % (si, rc, lf))
    wall = time.time() - t0

    n = collect_msa_outputs(msa_raw, work / "msa_store")
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
        if not cand.exists():
            alts = [p for p in store.glob(t["path"].stem + "_data.json")
                    if not is_sidecar(p.name)]
            cand = alts[0] if alts else None
        if cand is None:
            bad.append((t["name"], "msa_store 에 *_data.json 이 없다 (MSA 단계 미실행/실패)"))
            continue
        ok, why = validate_data_json(cand)
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
                            output_dir, bks, flags=flags)
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
    cmd = build_cmd(args, docker, "infer", sd, output_dir, bks, flags=flags)

    if args.dry_run:
        print("\n[드라이런] 추론 단계 (%d건, 컨테이너 1회):\n  %s" % (len(ready), " ".join(cmd)))
        return 0, [], 0.0, ready

    lf = logs / ("infer_%s.log" % datetime.now().strftime("%Y%m%d_%H%M%S"))
    rc, timings, wall = run_streamed(cmd, lf, "추론")
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
    sd = stage_files([t["path"] for t in targets], work / "stage_oneshot", mode="link")
    cmd = build_cmd(args, docker, "oneshot", sd, output_dir, bks,
                    n_cpu=msa_n_cpu(args, 1), flags=flags)
    if args.dry_run:
        print("\n[드라이런] oneshot 단계 (%d건, 컨테이너 1회):\n  %s"
              % (len(targets), " ".join(cmd)))
        return 0, [], 0.0
    lf = logs / ("oneshot_%s.log" % datetime.now().strftime("%Y%m%d_%H%M%S"))
    rc, timings, wall = run_streamed(cmd, lf, "oneshot")
    log("oneshot 종료: exit=%d, 전체 %.1f초, 건당 평균 %.1f초 (측정값)"
        % (rc, wall, wall / max(1, len(targets))))
    return rc, timings, wall


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
    p.add_argument("--db-dir", default=os.environ.get(
        "AF3_DB_DIR", str(Path.home() / "public_databases")))
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
    p.add_argument("--limit", type=int, default=None, help="앞에서 N건만 실행 (벤치마크용)")
    p.add_argument("--retry", action="store_true", help="지난 실행에서 실패한 것만 다시 한다")
    p.add_argument("--no-skip", action="store_true", help="이미 끝난 것도 다시 계산한다")
    p.add_argument("--keep-partial", action="store_true",
                   help="미완성 결과 폴더를 partial/ 로 옮기지 않고 그대로 둔다")
    p.add_argument("--lenient-done", action="store_true",
                   help="완료 판정을 2026-08 이전 방식으로 되돌린다: 완료 표식 "
                        "(_summary_confidences.json / _ranking_scores.csv / _model.cif) "
                        "중 하나만 있어도 완료로 본다. 기본은 3종 모두 있고 크기가 "
                        "0보다 클 때만 완료다. 예전 출력 폴더를 다시 돌리지 않으려면 쓴다")
    p.add_argument("--docker", default=None, help="도커 실행 명령 강제 지정 (예: 'sudo docker')")
    p.add_argument("--extra-flag", action="append", default=[],
                   help="run_alphafold.py 에 그대로 넘길 추가 플래그. 반드시 등호 형식으로 "
                        "쓸 것: --extra-flag=--save_embeddings (공백으로 쓰면 argparse 가 "
                        "거부한다). 반복 사용 가능")
    p.add_argument("--ligand-tokens", type=int, default=30,
                   help="리간드 1개를 몇 토큰으로 어림할지 (정렬/버킷 선택에만 영향)")
    p.add_argument("--no-probe", action="store_true", help="플래그 지원 여부 탐지를 건너뛴다")
    p.add_argument("--dry-run", action="store_true",
                   help="실제 실행 없이 조립된 docker 명령만 출력한다")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    base = Path.cwd()

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
    args.db_dir = str(Path(args.db_dir).expanduser())
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
    for label, path in (("입력 폴더", input_dir), ("DB 폴더", Path(args.db_dir)),
                        ("가중치 폴더", Path(args.model_dir))):
        if not Path(path).exists():
            missing.append("%s 없음: %s" % (label, path))
    if missing:
        for m in missing:
            print("오류: %s" % m)
        if not args.dry_run:
            return 1
        print("(드라이런이므로 경로 오류를 무시하고 명령 조립만 계속한다)")

    output_dir.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
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

    targets, unreadable = [], []
    for p in json_files:
        try:
            obj = read_fold_json(p)
        except Exception as e:
            unreadable.append((p.name, str(e)))
            continue
        name = obj.get("name") or p.stem
        targets.append({"path": p, "name": name,
                        "tokens": count_tokens(obj, args.ligand_tokens)})
    if unreadable:
        log("경고: 읽을 수 없는 JSON %d건 (건너뜀): %s"
            % (len(unreadable), ", ".join(n for n, _ in unreadable[:5])))

    log("입력 JSON %d건 확인. 토큰 수로 정렬한다 (로그 가독성 목적. "
        "정렬 자체의 시간 이득은 실측 0.00초/건이다)." % len(targets))
    targets.sort(key=lambda t: t["tokens"])

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
    if not args.no_skip:
        pending, done = [], 0
        log("완료 판정 기준: %s"
            % ("<타깃>_data.json 존재 (--stage msa)" if chk_mode == "data"
               else "_ranking_scores.csv + _model.cif(.zst) + _summary_confidences.json "
                    "3종 모두 (크기 0 제외)"))
        if args.lenient_done:
            log("  --lenient-done: 완료 표식 하나만 있어도 완료로 본다 (2026-08 이전 동작)")
        for t in targets:
            dirs = find_result_dirs(output_dir, t["name"])
            if any(outdir_is_complete(d, mode=chk_mode, lenient=args.lenient_done)
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
                    dest = ptdir / ("%s_%s" % (d.name, datetime.now().strftime("%H%M%S")))
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
    docker = find_docker(args.docker)
    if docker is None:
        if not args.dry_run:
            print("오류: docker 명령을 찾을 수 없다.")
            return 1
        docker = ["docker"]
        log("(드라이런: docker 가 없으므로 'docker' 로 가정한다)")

    flags = None
    if not args.no_probe and not args.dry_run:
        log("이미지가 지원하는 플래그를 --help 로 확인한다 (1회, 수십 초)")
        flags = probe_flags(docker, args.image)
        if flags is not None:
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

    if args.stage == "oneshot":
        rc, timings, wall = do_stage_oneshot(args, docker, flags, work, output_dir, targets)
        rc_all = rc or 0
    else:
        if args.stage in ("msa", "both"):
            rcs, _ = do_stage_msa(args, docker, flags, work, targets)
            if rcs and all(v != 0 for v in rcs.values()):
                log("경고: 모든 MSA 갈래가 0이 아닌 코드로 끝났다. 로그를 확인하라.")
                rc_all = 1
        if args.stage in ("infer", "both"):
            rc, timings, wall, ran = do_stage_infer(
                args, docker, flags, work, output_dir, targets)
            rc_all = rc_all or (rc or 0)

    if args.dry_run:
        print("\n[드라이런 종료] 실제 실행은 하지 않았다. 위 명령을 그대로 복사해 써도 된다.")
        return 0

    # ---- 결과 정리 --------------------------------------------------------
    tmap = {}
    for nm, a, b in timings:
        tmap[sanitise_name(nm)] = b - a

    rows, failed = [], []
    for t in ran:
        dirs = find_result_dirs(output_dir, t["name"])
        ok = any(outdir_is_complete(d, mode=chk_mode, lenient=args.lenient_done)
                 for d in dirs)
        if not ok:
            failed.append(t["name"])
        rows.append({
            "name": t["name"],
            "tokens": t["tokens"],
            "bucket": needed_buckets([t["tokens"]])[0],
            "status": "완료" if ok else "실패",
            "wall_seconds": ("%.1f" % tmap[sanitise_name(t["name"])])
                            if sanitise_name(t["name"]) in tmap else "",
            "output": str(dirs[0]) if dirs else "",
        })

    csv_path = work / "run_summary.csv"
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=["name", "tokens", "bucket", "status",
                                           "wall_seconds", "output"])
        w.writeheader()
        w.writerows(rows)

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
        print("  재시도    : python3 af3_batch.py --name <이름> --stage %s --retry" % args.stage)
    print("  요약 CSV    : %s" % csv_path)
    print("  로그        : %s" % (work / "logs"))
    print("=" * 79)
    return rc_all


if __name__ == "__main__":
    sys.exit(main())
