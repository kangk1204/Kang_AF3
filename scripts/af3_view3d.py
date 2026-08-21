#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
af3_view3d.py - AlphaFold 3 출력 폴더를 브라우저에서 돌려 보는 HTML 로 만든다.

왜 이것이 따로 있는가
    af3_visualize.py 가 만드는 3D 보기 수단은 PyMOL(.pml) / ChimeraX(.cxc)
    스크립트뿐이고, 둘 다 데스크톱 프로그램을 따로 깔아야 열린다.
    이 스크립트가 만드는 HTML 은 더블클릭하면 브라우저에서 바로 열린다.
    설치할 것이 없다.

무엇을 만드는가
    <출력폴더>/<타깃>.html   구조 하나. 마우스로 돌리고 확대한다
    <출력폴더>/index.html    타깃 목록. ranking score 내림차순. 이름을 누르면 구조로 간다

    타깃이 하나여도 index.html 을 만든다 (--no-index 로 끈다).

색칠
    기본은 pLDDT 다. _model.cif 의 B_iso_or_equiv 열에 원자별 pLDDT 가
    그대로 들어 있다 (0~100). AlphaFold DB 와 같은 4구간 색을 쓴다:
        90 이상    진한 파랑 #0053D6   매우 높음
        70~90      하늘색    #65CBF3   높음
        50~70      노랑      #FFDB13   낮음
        50 미만    주황      #FF7D45   매우 낮음
    HTML 안 버튼으로 사슬별 색칠로 즉시 바꿀 수 있다 (페이지를 다시 만들지 않는다).
    복합체에서 어느 쪽이 VHH 이고 어느 쪽이 항원인지 볼 때 쓴다.

신뢰도 지표
    _summary_confidences.json 에서 ranking score, pTM, ipTM, fraction_disordered,
    has_clash 를 읽어 구조 위에 같이 띄운다. 잔기 평균 pLDDT는 mmCIF의 원자별 값을
    잔기별로 평균한 뒤 각 잔기에 같은 가중치를 주어 다시 평균한 값이다.
    ipTM 은 단량체에 없다 (JSON 에 null). 없으면 그 항목을 빼고 0 으로 쓰지 않는다.

3D 라이브러리를 어디서 가져오는가 (읽고 고를 것)
    기본  --lib cdn    3Dmol.js 를 CDN 에서 불러온다. HTML 이 작다 (수백 KB).
                       열 때 인터넷이 필요하다. 오프라인에서는 구조가 안 뜨고
                       "라이브러리를 불러오지 못했다" 는 안내가 뜬다 (빈 화면이 아니다).
          --lib embed  라이브러리를 HTML 안에 넣는다. 인터넷 없이 열린다.
                       파일 하나가 약 0.5MB 커진다. 라이브러리 원본은 처음 한 번
                       내려받아 캐시에 둔다 (--lib-cache, 기본 ~/.cache/af3_view3d).
                       캐시가 있으면 그 다음부터는 인터넷이 필요 없다.
          --lib-file <경로>  이미 가진 3Dmol-min.js 를 직접 지정한다 (완전 오프라인).

    embed 는 타깃 수만큼 라이브러리가 복제된다. 2000건이면 약 1GB 다.
    많은 건수를 embed 로 만들 이유는 없다. 최종 후보 몇 건만 embed 로 만들어라.

완료 판정과 타깃명
    af3_collect.py / af3_visualize.py 의 정본 블록과 같다. 폴더명이 아니라 폴더 안
    산출물 파일의 stem 에서 타깃명을 얻고, 점으로 시작하는 관리용 항목
    (.af3_incomplete/, .af3_pending_*, .run_af3_batch.lock, ._*) 을 제외한다.
    완료는 _ranking_scores.csv / _model.cif(또는 .cif.zst) /
    _summary_confidences.json 세 묶음이 모두 있고 크기가 0보다 클 때다.

.cif.zst 압축 출력
    AF3 를 --compress_large_output_files 로 돌리면 mmCIF 가 .cif.zst 로 나온다.
    파이썬 표준 라이브러리는 zstd 를 풀지 못한다. 이 스크립트는 이 순서로 시도한다:
        1) zstandard 파이썬 모듈이 있으면 그것으로 푼다
        2) 없으면 zstd 명령을 찾아 그것으로 푼다
        3) 둘 다 없으면 그 타깃을 조용히 빼지 않는다. index.html 에 빨간 줄로
           "mmCIF 가 .cif.zst 다. zstd -d 로 풀어라" 를 적고 개별 HTML 도
           그 안내를 담아 만든다 (지표는 보인다. 구조만 없다).

의존성
    표준 라이브러리만 쓴다. matplotlib 도, numpy 도 필요 없다.
    (--lib embed 로 라이브러리를 처음 내려받을 때만 urllib 로 네트워크를 쓴다.
     zstandard 는 있으면 쓰고 없으면 zstd 명령으로 대신한다. 둘 다 없어도 죽지 않는다.)

사용법
    # 타깃 하나
    python3 af3_view3d.py vhh_out --only vhh_7a50_1 --out-dir 뷰어

    # 출력 폴더 전체 (타깃별 HTML + index.html)
    python3 af3_view3d.py vhh_out --out-dir 뷰어

    # 인터넷 없는 컴퓨터로 옮겨서 볼 것 (파일이 커진다)
    python3 af3_view3d.py vhh_out --out-dir 뷰어 --lib embed

    # 상위 20건만
    python3 af3_view3d.py vhh_out --out-dir 뷰어 --top 20
"""

import argparse
import base64
import csv
import hashlib
import html
import json
import math
import os
import re
import selectors
import shutil
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

MAX_CIF_BYTES = 256 * 1024 * 1024
MAX_LIBRARY_BYTES = 16 * 1024 * 1024
MAX_TARBALL_BYTES = 32 * 1024 * 1024


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def die(msg):
    log("")
    log("오류: " + msg)
    sys.exit(1)


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
# 이 블록은 af3_collect.py / af3_visualize.py / af3_batch.py / af3_view3d.py 에
# 같은 내용으로 들어 있다 (네 스크립트를 따로 복사해 쓰는 사용자가 있으므로 공용
# 모듈을 만들지 않았다). 고칠 때는 네 곳을 함께 고쳐라. tests/test_naming.py 가
# 사본들이 같은 답을 내는지 검사하므로, 한 곳만 고치면 테스트가 실패한다.
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
        return not path.is_symlink() and path.is_file() and path.stat().st_size > 0
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


# ---------------------------------------------------------------------------
# pLDDT 색 구간. af3_visualize.py 의 PLDDT_BANDS 와 같은 값이다
# (AF3/AF2 논문과 EBI AlphaFold DB 의 구간과 색).
# 경계값은 화면 범례에 그대로 찍는다.
# ---------------------------------------------------------------------------
PLDDT_BANDS = [
    (90, 100, "#0053D6", "매우 높음 (90 이상)"),
    (70,  90, "#65CBF3", "높음 (70-90)"),
    (50,  70, "#FFDB13", "낮음 (50-70)"),
    (0,   50, "#FF7D45", "매우 낮음 (50 미만)"),
]

# 사슬별 색. 색맹 대비를 고려한 순서다 (파랑/주황/초록/보라 순).
CHAIN_COLORS = ["#4C72B0", "#DD8452", "#55A868", "#8172B3", "#937860",
                "#DA8BC3", "#8C8C8C", "#CCB974", "#64B5CD", "#C44E52"]

# 렌더 엔진별 라이브러리 주소. 버전을 고정해 둔다 (latest 로 두면 어느 날
# API 가 바뀌어 조용히 깨진다. 아래 두 버전에서 실제로 동작을 확인했다).
#
# npm 레지스트리 tarball 을 마지막 수단으로 둔다. CDN 이 막힌 사내망에서
# registry.npmjs.org 만 열려 있는 경우가 있다.
ENGINES = {
    "molstar": {
        "version": "5.11.0",
        "js": ["https://cdn.jsdelivr.net/npm/molstar@5.11.0/build/viewer/molstar.js",
               "https://unpkg.com/molstar@5.11.0/build/viewer/molstar.js"],
        "css": ["https://cdn.jsdelivr.net/npm/molstar@5.11.0/build/viewer/molstar.css",
                "https://unpkg.com/molstar@5.11.0/build/viewer/molstar.css"],
        "tarball": "https://registry.npmjs.org/molstar/-/molstar-5.11.0.tgz",
        "tar_js": "package/build/viewer/molstar.js",
        "tar_css": "package/build/viewer/molstar.css",
        "cache_js": "molstar-5.11.0.js",
        "cache_css": "molstar-5.11.0.css",
        "size_hint": "약 5.0MB (CSS 0.07MB 별도)",
        "sha256_js": "7fad5561c74bc900930fb57d6ab028d1aafdda82223a901bf932b1098e84f1f3",
        "sha256_css": "5b68ceb6d3642549b4e9b2c071e58e41b98a5350ae269180587b39da86925d55",
        "sri_js": "sha384-5Mfx4eL50NkWPky+mcH//qY0sbml4il0CLFFmrMp8uv/saB3Z6uZMHn2dUpAnH92",
        "sri_css": "sha384-RIontCdJN53gEl2fmiHN+4bscIBvaUaOiCeeGktXqmFqdEBF+COnSdt9O4IKFSvq",
    },
    "3dmol": {
        "version": "2.5.5",
        "js": ["https://cdn.jsdelivr.net/npm/3dmol@2.5.5/build/3Dmol-min.js",
               "https://unpkg.com/3dmol@2.5.5/build/3Dmol-min.js"],
        "css": [],
        "tarball": "https://registry.npmjs.org/3dmol/-/3dmol-2.5.5.tgz",
        "tar_js": "package/build/3Dmol-min.js",
        "tar_css": None,
        "cache_js": "3Dmol-min-2.5.5.js",
        "cache_css": None,
        "size_hint": "약 0.53MB",
        "sha256_js": "f7cc78921ae72e7623e89cdd111434f58c2efddd2ffda1cd212644b406fb8016",
        "sha256_css": None,
        "sri_js": "sha384-OsczYbldvrHgslr9fFp/i4GiLSeuw9l+QIlv99ITw8soOwXcoGeflFMLg+CU/X1d",
        "sri_css": None,
    },
}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify_asset_bytes(data: bytes, expected_hashes) -> bool:
    return sha256_bytes(data) in set(expected_hashes)


def script_safe_json(value) -> str:
    """Serialize data for an executable script without HTML end-tag injection."""

    return (
        json.dumps(value, ensure_ascii=False, allow_nan=False)
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def is_safe_artifact_file(path, root) -> bool:
    path = Path(path)
    root = Path(root)
    if path.is_symlink() or not path.is_file():
        return False
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(root.resolve(strict=True))
    except (OSError, RuntimeError, ValueError):
        return False
    return resolved.parent == root.resolve()


def output_basename(value):
    if not value:
        raise ValueError("output name must not be empty")
    path = Path(value)
    if (
        path.is_absolute()
        or path.name != value
        or value in {".", ".."}
        or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", value)
    ):
        raise ValueError("output name must be a single filename")
    return value


def plan_output_names(labels, index_name):
    claimed = set()
    if index_name:
        claimed.add(output_basename(index_name))
    out = {}
    for label in labels:
        filename = safe_filename(label) + ".html"
        if filename in claimed:
            raise ValueError("출력 파일 이름 충돌: %s" % filename)
        claimed.add(filename)
        out[label] = filename
    return out


def atomic_write_text(path, text):
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp.%d" % os.getpid())
    if tmp.exists() or tmp.is_symlink():
        raise OSError("temporary output already exists: %s" % tmp)
    try:
        with tmp.open("x", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def load_json(path):
    """UTF-8 로 읽고, 실패하면 예외 대신 None 을 준다(한 건 때문에 전체가 죽지 않게)."""
    try:
        path = Path(path)
        if path.is_symlink() or path.stat().st_size > MAX_CIF_BYTES:
            raise OSError("symlink 또는 256MB 초과 JSON")
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, UnicodeDecodeError, ValueError):
        return None


def read_ranking_csv(path):
    """seed,sample,ranking_score 를 [(seed, sample, score)] 로."""
    if not os.path.exists(path):
        return []
    out = []
    try:
        with open(path, "r", encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                try:
                    score = float(row["ranking_score"])
                    if math.isfinite(score):
                        out.append((int(row["seed"]), int(row["sample"]), score))
                except (KeyError, ValueError, TypeError):
                    continue
    except (OSError, UnicodeDecodeError):
        return []
    return out


# ---------------------------------------------------------------------------
# mmCIF 읽기
# ---------------------------------------------------------------------------

def parse_mmcif_atoms(text):
    """mmCIF 문자열의 _atom_site 루프를 읽는다.

    반환: ([(사슬, 잔기번호, 잔기명, 원자명, B값)], 열이름목록)
    읽지 못하면 (None, 열이름목록 또는 None).

    표준 라이브러리만 쓴다. AF3 출력은 한 모델뿐이고 따옴표 필드가 없는 단순한
    루프여서 split() 으로 충분하다 (af3_visualize.py 의 같은 함수와 동일한 판단).
    값 개수가 헤더 개수와 다른 줄은 건너뛴다.
    """
    cols = []
    rows = []
    in_loop = False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("_atom_site."):
            if not in_loop:
                in_loop = True
                cols = []
            cols.append(s.split(".", 1)[1])
            continue
        if in_loop:
            if s.startswith(("ATOM", "HETATM")):
                parts = s.split()
                if len(parts) == len(cols):
                    rows.append(parts)
            elif s.startswith("#") or s == "" or s.startswith("_") or s.startswith("loop_"):
                if rows:
                    break
    if not rows:
        return None, (cols or None)
    idx = {c: i for i, c in enumerate(cols)}
    need = ["auth_asym_id", "auth_seq_id", "label_comp_id", "label_atom_id",
            "B_iso_or_equiv"]
    for n in need:
        if n not in idx:
            return None, cols
    out = []
    for p in rows:
        try:
            bfactor = float(p[idx["B_iso_or_equiv"]])
            if not math.isfinite(bfactor):
                continue
            out.append((p[idx["auth_asym_id"]], int(p[idx["auth_seq_id"]]),
                        p[idx["label_comp_id"]], p[idx["label_atom_id"]],
                        bfactor))
        except ValueError:
            continue
    return out, cols


def residues_from_cif(cif_atoms):
    """원자 목록을 잔기별로 묶는다.

    반환: [{"c": 사슬, "i": 잔기번호, "n": 잔기명, "p": 평균 pLDDT, "a": 원자수}]
    출현 순서를 지킨다 (mmCIF 의 원자 순서 = 사슬 순서 = 서열 순서).

    카툰(리본) 표현은 원자가 아니라 잔기 단위로 색이 칠해진다. 그래서 원자별
    pLDDT 를 잔기 평균으로 묶어 색을 정한다. AlphaFold DB 의 색칠도 잔기 단위다.
    값의 출처는 mmCIF 의 B_iso_or_equiv 이고, 이것은 confidences.json 의
    atom_plddts 와 같은 값이다 (af3_visualize.py 의 verify_bfactor 가 최대 차
    0.0105 이내임을 실측으로 확인했다. mmCIF 는 소수 2자리로 쓴다).
    """
    acc = {}
    order = []
    for ch, resi, resn, _atom, b in cif_atoms:
        key = (ch, resi)
        if key not in acc:
            acc[key] = {"c": ch, "i": resi, "n": resn, "v": []}
            order.append(key)
        acc[key]["v"].append(b)
    out = []
    for key in order:
        r = acc[key]
        out.append({"c": r["c"], "i": r["i"], "n": r["n"],
                    "p": round(statistics.fmean(r["v"]), 2), "a": len(r["v"])})
    return out


def decompress_zst(path):
    """.cif.zst 를 풀어 문자열로 준다. 실패하면 (None, 이유).

    표준 라이브러리는 zstd 를 풀지 못한다. 두 경로를 시도한다.
      1) zstandard 파이썬 모듈 (있으면)
      2) zstd 명령 (PATH 에 있으면)
    둘 다 없으면 이유를 돌려준다. 호출한 쪽이 그 이유를 화면에 띄운다.
    """
    try:
        import zstandard  # 선택 의존성. 없는 것이 정상이다.
    except ImportError:
        zstandard = None
    if zstandard is not None:
        try:
            with open(path, "rb") as fh:
                reader = zstandard.ZstdDecompressor().stream_reader(fh)
                data = reader.read(MAX_CIF_BYTES + 1)
            if len(data) > MAX_CIF_BYTES:
                return None, "압축 해제 결과가 안전 한도 256MB를 넘는다"
            return data.decode("utf-8", "replace"), ""
        except Exception as exc:                      # 압축 파일이 깨진 경우
            return None, "zstandard 모듈로 풀다 실패했다 (%s)" % exc
    exe = shutil.which("zstd")
    if exe:
        try:
            p = subprocess.Popen(
                [exe, "-dc", str(path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            selector = selectors.DefaultSelector()
            assert p.stdout is not None and p.stderr is not None
            selector.register(p.stdout, selectors.EVENT_READ, "stdout")
            selector.register(p.stderr, selectors.EVENT_READ, "stderr")
            out_chunks, err_chunks = [], []
            out_size = 0
            deadline = time.monotonic() + 120
            failed_reason = None
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    failed_reason = "zstd 명령이 120초 제한을 넘었다"
                    break
                events = selector.select(min(1.0, remaining))
                for key, _mask in events:
                    chunk = key.fileobj.read1(1024 * 1024)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    if key.data == "stdout":
                        out_size += len(chunk)
                        if out_size > MAX_CIF_BYTES:
                            failed_reason = "압축 해제 결과가 안전 한도 256MB를 넘는다"
                            break
                        out_chunks.append(chunk)
                    elif sum(map(len, err_chunks)) < 64 * 1024:
                        err_chunks.append(chunk)
                if failed_reason:
                    break
            if failed_reason:
                p.kill()
            p.wait(timeout=5)
            stdout = b"".join(out_chunks)
            stderr = b"".join(err_chunks)
            if failed_reason:
                return None, failed_reason
        except (OSError, subprocess.SubprocessError) as exc:
            return None, "zstd 명령 실행이 실패했다 (%s)" % exc
        if len(stdout) > MAX_CIF_BYTES:
            return None, "압축 해제 결과가 안전 한도 256MB를 넘는다"
        if p.returncode == 0 and stdout:
            return stdout.decode("utf-8", "replace"), ""
        return None, ("zstd 명령이 실패했다 (rc=%d) %s"
                      % (p.returncode,
                         stderr.decode("utf-8", "replace").strip()[:200]))
    return None, ("zstd 를 풀 방법이 없다. 다음 중 하나를 하라: "
                  "python3 -m pip install zstandard  또는  "
                  "zstd -d <파일>.cif.zst 로 미리 풀어 두기")


# ---------------------------------------------------------------------------
# 3Dmol.js 확보 (--lib embed 일 때만 필요하다)
# ---------------------------------------------------------------------------

def _http_bytes(url, timeout, limit):
    with urllib.request.urlopen(url, timeout=timeout) as fh:
        data = fh.read(limit + 1)
    if len(data) > limit:
        raise ValueError("download exceeds %d bytes" % limit)
    return data


def _verified_text(url, timeout, expected_sha256):
    data = _http_bytes(url, timeout, MAX_LIBRARY_BYTES)
    if not verify_asset_bytes(data, {expected_sha256}):
        raise ValueError(
            "integrity mismatch for %s (got %s)" % (url, sha256_bytes(data))
        )
    return data.decode("utf-8")


def fetch_library(engine, cache_dir, lib_file=None, lib_css_file=None, timeout=90):
    """엔진의 라이브러리 원본을 준다. 성공하면 ({"js":..., "css":...}, 알림).

    실패하면 (None, 이유). 순서는 이렇다.
      1) --lib-file / --lib-css-file 로 직접 준 파일
      2) 캐시 (--lib-cache, 기본 ~/.cache/af3_view3d)
      3) CDN 두 곳
      4) npm 레지스트리 tarball (CDN 이 막힌 사내망 대비. tarfile 은 표준 라이브러리)
    내려받은 것은 캐시에 저장해서 다음부터 인터넷 없이 만들 수 있게 한다.
    """
    spec = ENGINES[engine]
    out = {"js": None, "css": None}

    if lib_file:
        try:
            lib_path = Path(lib_file)
            if lib_path.is_symlink() or lib_path.stat().st_size > MAX_LIBRARY_BYTES:
                raise ValueError("trusted library file is a symlink or exceeds 16MB")
            out["js"] = lib_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            return None, "--lib-file '%s' 를 읽지 못했다 (%s)" % (lib_file, exc)
    if lib_css_file:
        try:
            css_path = Path(lib_css_file)
            if css_path.is_symlink() or css_path.stat().st_size > MAX_LIBRARY_BYTES:
                raise ValueError("trusted CSS file is a symlink or exceeds 16MB")
            out["css"] = css_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            return None, "--lib-css-file '%s' 를 읽지 못했다 (%s)" % (lib_css_file, exc)
    if out["js"] is not None:
        return out, "직접 준 파일을 인라인했다"

    cache = Path(os.path.expanduser(cache_dir))
    cjs = cache / spec["cache_js"]
    ccss = cache / spec["cache_css"] if spec["cache_css"] else None
    if (
        cjs.is_file()
        and not cjs.is_symlink()
        and 100000 < cjs.stat().st_size <= MAX_LIBRARY_BYTES
    ):
        js_data = cjs.read_bytes()
        if verify_asset_bytes(js_data, {spec["sha256_js"]}):
            out["js"] = js_data.decode("utf-8")
        if (
            ccss is not None
            and ccss.is_file()
            and not ccss.is_symlink()
            and ccss.stat().st_size <= MAX_LIBRARY_BYTES
        ):
            css_data = ccss.read_bytes()
            if verify_asset_bytes(css_data, {spec["sha256_css"]}):
                out["css"] = css_data.decode("utf-8")
        if out["js"] is not None and (ccss is None or out["css"]):
            return out, "캐시 사용: %s" % cjs

    tried = []
    for url in spec["js"]:
        try:
            out["js"] = _verified_text(url, timeout, spec["sha256_js"])
            break
        except (urllib.error.URLError, OSError, UnicodeDecodeError, ValueError) as exc:
            tried.append("%s (%s)" % (url, exc))
    if out["js"] is not None and spec["css"]:
        for url in spec["css"]:
            try:
                out["css"] = _verified_text(url, timeout, spec["sha256_css"])
                break
            except (urllib.error.URLError, OSError, UnicodeDecodeError, ValueError) as exc:
                tried.append("%s (%s)" % (url, exc))
        if out["css"] is None:
            out["js"] = None                       # CSS 없이는 화면이 깨진다

    if out["js"] is None:
        import io
        import tarfile
        try:
            with urllib.request.urlopen(spec["tarball"], timeout=timeout) as fh:
                blob = fh.read(MAX_TARBALL_BYTES + 1)
            if len(blob) > MAX_TARBALL_BYTES:
                raise ValueError("npm tarball exceeds safety limit")
            with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tf:
                m = tf.extractfile(spec["tar_js"])
                if m is None:
                    raise KeyError(spec["tar_js"])
                js_data = m.read(MAX_LIBRARY_BYTES + 1)
                if len(js_data) > MAX_LIBRARY_BYTES or not verify_asset_bytes(
                    js_data, {spec["sha256_js"]}
                ):
                    raise ValueError("npm JavaScript integrity mismatch")
                out["js"] = js_data.decode("utf-8")
                if spec["tar_css"]:
                    m2 = tf.extractfile(spec["tar_css"])
                    if m2 is not None:
                        css_data = m2.read(MAX_LIBRARY_BYTES + 1)
                        if len(css_data) > MAX_LIBRARY_BYTES or not verify_asset_bytes(
                            css_data, {spec["sha256_css"]}
                        ):
                            raise ValueError("npm CSS integrity mismatch")
                        out["css"] = css_data.decode("utf-8")
        except Exception as exc:
            tried.append("%s (%s)" % (spec["tarball"], exc))
            return None, ("%s 라이브러리를 내려받지 못했다. 시도한 곳:\n        %s\n"
                          "      인터넷이 되는 컴퓨터에서 아래 파일을 저장해 옮기고\n"
                          "      --lib-file (필요하면 --lib-css-file) 로 넘겨라:\n"
                          "        %s"
                          % (engine, "\n        ".join(tried),
                             "\n        ".join(spec["js"][:1] + spec["css"][:1])))
    try:
        cache.mkdir(parents=True, exist_ok=True)
        atomic_write_text(cjs, out["js"])
        if ccss is not None and out["css"]:
            atomic_write_text(ccss, out["css"])
        msg = "내려받아 캐시에 저장했다: %s" % cache
    except OSError:
        msg = "내려받았다 (캐시 저장 실패. 다음에 또 내려받는다)"
    return out, msg


# ---------------------------------------------------------------------------
# 타깃 찾기 (af3_visualize.py find_targets 와 같은 규칙)
# ---------------------------------------------------------------------------

def find_targets(root, only=None, all_runs=False, include_partial=False):
    """출력 폴더 아래에서 (타깃라벨, 폴더, stem) 목록을 만든다.

    타깃명은 폴더 이름이 아니라 폴더 안 산출물 파일의 stem 이다. 같은 타깃이
    여러 폴더에 있으면 기본으로 최신 실행 1건만 쓴다 (--all-runs 로 전부).
    --only 는 타깃명으로 고르고 폴더명도 받아준다 (af3_visualize.py 와 같다).
    """
    root = Path(root)
    if not root.is_dir():
        die("'%s' 는 폴더가 아니다. AF3 --output_dir 로 준 폴더를 지정해라." % root)
    picks = set(x.strip() for x in only.split(",")) if only else None

    sidecars = 0
    resolved = []
    skipped_partial = []
    for child in sorted(root.iterdir()):
        if is_sidecar(child.name):
            # 점으로 시작하는 항목은 전부 건너뛴다. 배치 러너의 격리 폴더
            # (.af3_incomplete/), staging(.af3_pending_*), lock(.run_af3_batch.lock)
            # 이 여기에 걸린다. 격리 폴더 안은 미완료 결과이므로 보여선 안 된다.
            if child.name.startswith("._"):
                sidecars += 1
            continue
        if not child.is_dir():
            continue
        info = resolve_result_dir(child, mode="full")
        if info["note"]:
            log("  주의: %s - %s" % (child.name, info["note"]))
        if not info["complete"]:
            if not (include_partial and info["stem"]
                    and (child / ("%s_summary_confidences.json"
                                  % info["stem"])).is_file()):
                skipped_partial.append((info["target"], info["n_final"]))
                continue
            log("  %s: 정식 완료가 아니다 (정식 산출물 %d/3). --include-partial 로 넣는다."
                % (info["target"], info["n_final"]))
        resolved.append((child, info))

    by_target = {}
    for child, info in resolved:
        by_target.setdefault(info["target"], []).append((child, info))

    found = []
    for target in sorted(by_target):
        runs = by_target[target]
        if picks and target not in picks and not any(c.name in picks for c, _ in runs):
            continue
        runs.sort(key=lambda ci: (dir_run_time(ci[0], ci[1]), ci[0].name))
        chosen = runs if all_runs else [runs[-1]]
        if len(runs) > 1:
            log("  %s: 결과 폴더가 %d개다 (%s). %s"
                % (target, len(runs), ", ".join(c.name for c, _ in runs),
                   "전부 만든다" if all_runs
                   else "최신(%s)만 만든다. 전부 보려면 --all-runs" % runs[-1][0].name))
        for child, info in chosen:
            label = target
            if all_runs and len(runs) > 1 and info["run_ts"]:
                label = "%s__%s" % (target, info["run_ts"])
            found.append((label, child, info["stem"]))

    if sidecars:
        log("주의: '%s' 에 AppleDouble 사이드카가 %d개 있다. 건너뛰었지만,"
            % (root, sidecars))
        log("      AF3 자체는 이것 때문에 죽는다. 지워라:  find %s -name '._*' -delete" % root)
    if skipped_partial:
        log("미완료로 건너뛴 폴더 %d개: %s%s"
            % (len(skipped_partial),
               ", ".join("%s(%d/3)" % t for t in skipped_partial[:5]),
               " ..." if len(skipped_partial) > 5 else ""))
        log("      보고 판단해야 하면 --include-partial 을 붙여라.")
    if not found:
        die("'%s' 아래에서 완료된 AF3 출력 타깃을 찾지 못했다.\n"
            "      기대한 구조: %s/<타깃이름>/<타깃이름>_model.cif\n"
            "      완료 판정은 _ranking_scores.csv, _model.cif(또는 .cif.zst),\n"
            "      _summary_confidences.json 세 개가 모두 있고 크기가 0보다 큰 것이다.\n"
            "      실제 내용: %s"
            % (root, root, ", ".join(p.name for p in sorted(Path(root).iterdir())[:8])))
    return found


def safe_filename(name):
    """타깃명을 파일 이름으로 쓸 수 있게 다듬는다.

    AF3 가 이미 [A-Za-z0-9_-.] 로 정규화한 이름이라 보통 바뀌는 것이 없다.
    사람이 손으로 폴더를 만든 경우를 대비한 안전장치다.
    """
    out = re.sub(r"[^A-Za-z0-9._\-]", "_", name).strip("._")
    return out or "target"


# ---------------------------------------------------------------------------
# 타깃 하나의 자료 모으기
# ---------------------------------------------------------------------------

def gather_target(label, tdir, stem):
    """HTML 하나를 만들기 위한 자료를 모은다.

    반환 dict:
        label, dir, stem
        cif       mmCIF 문자열 (없으면 None)
        problem   구조를 못 얻은 이유 (없으면 "")
        metrics   화면에 띄울 지표 [(이름, 표시값)]
        summary   _summary_confidences.json 원본 (없으면 {})
        residues  [{"c","i","n","p","a"}] 잔기별 pLDDT (없으면 [])
        chains    사슬 id 목록 (출현 순서)
        mean_plddt, min_plddt  (둘 다 잔기 단위)
        rank      ranking score (정렬용 숫자. 없으면 None)
        n_sample  ranking_scores.csv 의 샘플 수
        sample_sd 샘플 간 ranking score 표준편차 (2건 이상일 때)
    """
    rec = {"label": label, "dir": str(tdir), "stem": stem, "cif": None,
           "problem": "", "summary": {}, "residues": [], "chains": [],
           "mean_plddt": None, "min_plddt": None, "rank": None,
           "n_sample": 0, "sample_sd": None, "n_atom": 0,
           "global_plddt_cif": None}

    summ = load_json(tdir / ("%s_summary_confidences.json" % stem)) or {}
    rec["summary"] = summ
    try:
        rank = float(summ.get("ranking_score"))
    except (TypeError, ValueError):
        rank = None
    rec["rank"] = rank if rank is not None and math.isfinite(rank) else None

    scores = [s for _sd, _sm, s in
              read_ranking_csv(str(tdir / ("%s_ranking_scores.csv" % stem)))]
    rec["n_sample"] = len(scores)
    if len(scores) >= 2:
        rec["sample_sd"] = statistics.pstdev(scores)

    cif_plain = tdir / ("%s_model.cif" % stem)
    cif_zst = tdir / ("%s_model.cif.zst" % stem)
    if is_safe_artifact_file(cif_plain, tdir):
        try:
            if cif_plain.stat().st_size > MAX_CIF_BYTES:
                rec["problem"] = "mmCIF 가 안전 한도 256MB를 넘는다"
            else:
                rec["cif"] = cif_plain.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            rec["problem"] = "mmCIF 를 읽지 못했다 (%s)" % exc
    elif is_safe_artifact_file(cif_zst, tdir):
        text, why = decompress_zst(cif_zst)
        if text is None:
            rec["problem"] = ("mmCIF 가 .cif.zst 압축이고 풀지 못했다. %s" % why)
        else:
            rec["cif"] = text
    elif cif_plain.is_symlink() or cif_zst.is_symlink():
        rec["problem"] = "symlinked mmCIF 산출물은 외부 파일 유출 방지를 위해 거부했다"
    else:
        rec["problem"] = "mmCIF 파일이 없다 (_model.cif / _model.cif.zst 둘 다)"

    if rec["cif"]:
        atoms, cols = parse_mmcif_atoms(rec["cif"])
        if not atoms:
            rec["problem"] = ("mmCIF 의 _atom_site 루프를 읽지 못했다 "
                              "(열: %s)" % (", ".join(cols[:6]) if cols else "없음"))
            rec["cif"] = None
        else:
            rec["n_atom"] = len(atoms)
            res = residues_from_cif(atoms)
            rec["residues"] = res
            seen = []
            for r in res:
                if r["c"] not in seen:
                    seen.append(r["c"])
            rec["chains"] = seen
            vals = [r["p"] for r in res]
            rec["mean_plddt"] = statistics.fmean(vals)
            rec["min_plddt"] = min(vals)
            m = re.search(r"_ma_qa_metric_global\.metric_value\s+([0-9.]+)",
                          rec["cif"])
            if m:
                value = float(m.group(1))
                rec["global_plddt_cif"] = value if math.isfinite(value) else None
    return rec


def fmt_num(v, nd=3):
    if v is None:
        return None
    try:
        value = float(v)
        return ("%." + str(nd) + "f") % value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def metric_rows(rec):
    """화면에 띄울 지표 목록. 값이 없는 항목은 넣지 않는다.

    ipTM 은 단량체에 없다 (JSON 에 null). 그 경우 항목 자체를 뺀다.
    0 으로 표시하면 '계면이 나쁘다' 로 읽히므로 절대 그렇게 하지 않는다.
    """
    s = rec["summary"]
    rows = []

    def add(name, val, hint=""):
        if val is not None:
            rows.append((name, val, hint))

    add("ranking score", fmt_num(s.get("ranking_score"), 3),
        "AF3 가 후보를 줄 세우는 값. 클수록 좋다")
    add("pTM", fmt_num(s.get("ptm"), 3), "구조 전체의 접힘 신뢰도 (0~1)")
    if s.get("iptm") is not None:
        add("ipTM", fmt_num(s.get("iptm"), 3),
            "사슬 사이 계면 신뢰도 (0~1). 복합체에만 있다")
    add("잔기 평균 pLDDT", fmt_num(rec["mean_plddt"], 1),
        "각 잔기에 같은 가중치를 준 평균 (0~100)")
    add("최저 잔기 pLDDT", fmt_num(rec["min_plddt"], 1),
        "가장 자신 없는 잔기의 값")
    if s.get("fraction_disordered") is not None:
        add("무질서 비율", fmt_num(s.get("fraction_disordered"), 3),
            "구조가 풀린 부분의 비율 (0~1)")
    if s.get("has_clash") is not None:
        add("원자 충돌", "있다" if float(s.get("has_clash") or 0) > 0 else "없다",
            "0 이 정상이다")
    if rec["chains"]:
        add("사슬", "%d개 (%s)" % (len(rec["chains"]), ", ".join(rec["chains"])), "")
    if rec["residues"]:
        add("잔기 / 원자", "%d / %d" % (len(rec["residues"]), rec["n_atom"]), "")
    if rec["n_sample"]:
        txt = "%d개" % rec["n_sample"]
        if rec["sample_sd"] is not None:
            txt += " (ranking score 표준편차 %.3f)" % rec["sample_sd"]
        add("확산 샘플", txt, "샘플 간 산포가 크면 재현성이 낮다")
    return rows


def plddt_histogram(residues):
    """구간별 잔기 수를 센다. [(색, 라벨, 개수, 비율%)]"""
    out = []
    n = len(residues) or 1
    for lo, hi, color, label in PLDDT_BANDS:
        if lo == 90:
            cnt = sum(1 for r in residues if r["p"] >= 90)
        elif lo == 0:
            cnt = sum(1 for r in residues if r["p"] < 50)
        else:
            cnt = sum(1 for r in residues if lo <= r["p"] < hi)
        out.append((color, label, cnt, 100.0 * cnt / n))
    return out


def low_stretches(residues, cutoff=70, min_len=3):
    """pLDDT 가 낮은 연속 구간을 찾는다. [(사슬, 시작, 끝, 길이, 평균)]

    VHH 에서 CDR3 근처가 낮게 나오는 것이 흔하므로, 어디가 낮은지 이름으로
    알려주면 사용자가 '루프라서 낮은 것' 과 '접힘이 실패한 것' 을 구분할 수 있다.
    """
    out = []
    run = []
    for r in residues + [None]:
        if r is not None and r["p"] < cutoff and (not run or run[-1]["c"] == r["c"]):
            run.append(r)
            continue
        if len(run) >= min_len:
            out.append((run[0]["c"], run[0]["i"], run[-1]["i"], len(run),
                        statistics.fmean([x["p"] for x in run])))
        run = [r] if (r is not None and r["p"] < cutoff) else []
    return out


# ---------------------------------------------------------------------------
# HTML 만들기
#
# 렌더 엔진 두 가지를 쓴다. 어느 쪽이든 사용자가 보는 것은 같다
# (pLDDT 기본 색칠 + 사슬 전환 + 범례 + 지표).
#
#   molstar  RCSB PDB 와 EBI AlphaFold DB 가 쓰는 뷰어. mmCIF 를 네이티브로 읽고,
#            plddt-confidence 색 테마가 내장돼 있다. 그 테마의 색 경계와 색값이
#            이 저장소의 PLDDT_BANDS 와 같다 (5.11.0 번들에서 확인:
#            <=50 0xFF7D45, <=70 0xFFDB13, <=90 0x65CBF3, 그 위 0x0053D6).
#            AF3 mmCIF 의 _ma_qa_metric_local 을 1순위로 읽고 없으면
#            B_iso_or_equiv 로 되돌린다. AF3 는 둘을 같은 값으로 쓴다.
#            번들이 약 5MB 라서 인라인하면 파일이 커진다.
#   3dmol    가볍다 (약 0.53MB). 색칠을 우리가 직접 지정한다. 오프라인 인라인용.
#
# 두 엔진의 자바스크립트는 아래 ENGINE_JS 에 하나씩 있고, HTML 껍데기(지표 표,
# 범례, 버튼)는 공유한다. 버튼이 부르는 함수 이름 두 개만 약속으로 맞춘다:
#     window.af3SetColor("plddt" | "chain")
#     window.af3ResetView()
# ---------------------------------------------------------------------------

ENGINE_MOLSTAR_JS = r"""
(async function(){
  var box = document.getElementById('viewer');
  try {
    if (typeof molstar === 'undefined' || !molstar.Viewer) throw new Error('molstar 전역이 없다');
    var v = await molstar.Viewer.create('viewer', {
      layoutIsExpanded: false, layoutShowControls: false,
      layoutShowSequence: true, layoutShowLog: false,
      viewportShowExpand: true, viewportShowSelectionMode: false
    });
    var P = v.plugin;
    await v.loadStructureFromData(AF3.cif, 'mmcif', {});
    var cur = P.managers.structure.hierarchy.current.structures[0];
    if (!cur) throw new Error('구조를 읽지 못했다');
    window.af3SetColor = async function(mode){
      var theme = (mode === 'chain') ? 'chain-id' : 'plddt-confidence';
      await P.managers.structure.component.updateRepresentationsTheme(
        cur.components, { color: theme });
      af3MarkButton(mode);
    };
    window.af3ResetView = function(){ P.canvas3d.requestCameraReset(); };
    // 검증 스크립트가 잔기별 색 배정을 되읽을 수 있게 손잡이를 남긴다.
    // 화면 동작에는 쓰이지 않는다 (tests/ 와 docs/view3d_notes.md 6-3 참조).
    window.__af3_plugin = P;
    // 범례 색을 Mol* 이 실제로 쓴 사슬 색으로 맞춘다. Mol* 은 자체 사슬 색표를
    // 쓰므로 우리 색 견본을 그리면 화면과 다른 색이 범례에 뜬다 (거짓 범례).
    // 그래서 테마에서 색을 되읽어 범례에 그대로 넣는다.
    try {
      var SE = molstar.lib.structure.StructureElement;
      var SP = molstar.lib.structure.StructureProperties;
      var struct = cur.cell.obj.data;
      var th = P.representation.structure.themes.colorThemeRegistry
                .create('chain-id', { structure: struct }, {});
      var loc = SE.Location.create(struct);
      var map = {};
      for (var ui = 0; ui < struct.units.length; ui++) {
        var u = struct.units[ui];
        loc.unit = u; loc.element = u.elements[0];
        var ch = SP.chain.auth_asym_id(loc);
        if (!map[ch]) {
          map[ch] = '#' + ('000000' + (th.color(loc, false) >>> 0).toString(16)).slice(-6);
        }
      }
      af3SetChainLegend(map);
    } catch (e) { /* 범례 색 갱신 실패는 화면 동작을 막지 않는다 */ }
    await window.af3SetColor('plddt');
    af3Ready('molstar ' + (molstar.version || ''));
  } catch (e) {
    af3Fail(e && e.message ? e.message : ('' + e));
  }
})();
"""

ENGINE_3DMOL_JS = r"""
(function(){
  try {
    if (typeof $3Dmol === 'undefined') throw new Error('$3Dmol 전역이 없다');
    var v = $3Dmol.createViewer('viewer', { backgroundColor: 'white' });
    var model = v.addModel(AF3.cif, 'cif');
    var atoms = model.selectedAtoms({});
    if (!atoms.length) throw new Error('mmCIF 에서 원자를 읽지 못했다');
    // 잔기별 pLDDT 를 (사슬, 잔기번호) 로 찾는다. 값의 출처는 파이썬 쪽에서
    // mmCIF B_iso_or_equiv 를 잔기 평균한 AF3.res 다 (원자 b 를 다시 쓰지 않는다.
    // 카툰은 잔기 단위로 칠해지므로 잔기 대표값이 있어야 색이 흔들리지 않는다).
    var pmap = {};
    for (var i = 0; i < AF3.res.length; i++) {
      pmap[AF3.res[i].c + '|' + AF3.res[i].i] = AF3.res[i].p;
    }
    function bandColor(p){
      if (p === undefined || p === null) return '#BFBFBF';
      if (p >= 90) return AF3.bands[0];
      if (p >= 70) return AF3.bands[1];
      if (p >= 50) return AF3.bands[2];
      return AF3.bands[3];
    }
    var chainColor = {};
    for (var k = 0; k < AF3.chains.length; k++) {
      chainColor[AF3.chains[k]] = AF3.chainColors[k % AF3.chainColors.length];
    }
    // 렌더러가 원자마다 부르는 함수다. 화면 색은 이 함수의 반환값으로 정해진다.
    function colorOf(chain, resi, mode){
      if (mode === 'chain') return chainColor[chain] || '#8C8C8C';
      return bandColor(pmap[chain + '|' + resi]);
    }
    function paint(mode){
      v.setStyle({}, { cartoon: { colorfunc: function(atom){
        return colorOf(atom.chain, atom.resi, mode);
      }}});
      v.render();
    }
    window.af3SetColor = function(mode){ paint(mode); af3MarkButton(mode); };
    window.af3ResetView = function(){ v.zoomTo(); v.render(); };
    // 검증 스크립트용 손잡이 (Mol* 경로와 같은 이유. 화면 동작에는 쓰이지 않는다).
    // 3Dmol 은 colorfunc 결과를 atom.color 에 되쓰지 않으므로, 색을 확인하려면
    // 렌더러가 부르는 이 함수 자체를 불러야 한다.
    window.__af3_viewer = v;
    window.__af3_colorOf = colorOf;
    af3SetChainLegend(chainColor);
    paint('plddt');
    v.zoomTo();
    v.render();
    af3MarkButton('plddt');
    af3Ready('3Dmol.js');
  } catch (e) {
    af3Fail(e && e.message ? e.message : ('' + e));
  }
})();
"""

PAGE_CSS = """
:root { --bd:#d8d8d8; --ink:#1a1a1a; --dim:#666; }
* { box-sizing: border-box; }
body { margin:0; font-family: -apple-system, "Apple SD Gothic Neo", "Noto Sans KR",
       "Malgun Gothic", sans-serif; color: var(--ink); background:#fff; }
header { padding:10px 14px; border-bottom:1px solid var(--bd); }
h1 { margin:0 0 2px 0; font-size:17px; }
.sub { font-size:12px; color:var(--dim); }
/* 사이드바가 길어도 3D 화면 높이는 창에 맞춘다. height 를 100% 로 두지 않으면
   사이드바가 짧은 페이지에서 아래쪽에 빈 회색 띠가 남는다. */
.wrap { display:flex; align-items:stretch; height:calc(100vh - 62px); min-height:520px; }
.side { width:330px; min-width:330px; overflow-y:auto; padding:10px 12px;
        border-right:1px solid var(--bd); font-size:13px; }
.col { display:flex; flex-direction:column; flex:1 1 auto; min-width:320px;
       height:100%; }
.view { position:relative; flex:1 1 auto; min-height:0; }
#viewer { position:absolute; inset:0; }
.bar { padding:7px 10px; border-bottom:1px solid var(--bd); background:#fafafa;
       display:flex; gap:6px; align-items:center; flex-wrap:wrap; font-size:13px; }
button { font:inherit; padding:4px 11px; border:1px solid #bbb; background:#fff;
         border-radius:3px; cursor:pointer; }
button:hover { background:#f0f0f0; }
button.on { background:#1a1a1a; color:#fff; border-color:#1a1a1a; }
table.m { border-collapse:collapse; width:100%; margin:2px 0 10px 0; }
table.m td { padding:3px 4px; border-bottom:1px solid #eee; vertical-align:top; }
table.m td.k { color:var(--dim); }
/* 값 칸은 줄바꿈을 허용한다. nowrap 으로 두면 '확산 샘플 5개 (표준편차 ...)'
   같은 긴 값이 표를 사이드바 밖으로 밀어내 숫자 칸이 잘려 보이지 않는다. */
table.m td.v { text-align:right; font-variant-numeric:tabular-nums;
               font-weight:600; }
table.m td.k { width:58%; }
.hint { font-size:11px; color:var(--dim); }
h2 { font-size:13px; margin:14px 0 5px 0; padding-bottom:3px;
     border-bottom:1px solid var(--bd); }
.leg { margin:0; padding:0; list-style:none; }
.leg li { display:flex; align-items:center; gap:7px; padding:2px 0; }
.sw { width:15px; height:15px; border:1px solid #999; flex:0 0 auto; }
.leg .n { margin-left:auto; font-variant-numeric:tabular-nums; color:var(--dim);
          font-size:12px; }
.bad { background:#fff4f4; border:1px solid #e0a0a0; padding:9px 11px;
       border-radius:3px; font-size:13px; margin:8px 0; }
.note { background:#f7f7f7; border:1px solid var(--bd); padding:8px 10px;
        border-radius:3px; font-size:12px; }
#status { position:absolute; left:50%; top:46%; transform:translateX(-50%);
          font-size:13px; color:var(--dim); text-align:center; max-width:80%; }
ul.tight { margin:4px 0 0 18px; padding:0; }
ul.tight li { margin-bottom:3px; }
a { color:#0053D6; }
"""

PAGE_TMPL = """<!DOCTYPE html>
<html lang="ko"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline' https://cdn.jsdelivr.net https://unpkg.com; style-src 'unsafe-inline' https://cdn.jsdelivr.net https://unpkg.com; img-src data: blob:; connect-src 'none'; font-src data:; worker-src blob:">
<title>__TITLE__</title>
__LIBHEAD__
<style>__CSS__</style>
</head><body>
<header>
  <h1>__TARGET__</h1>
  <div class="sub">__SUBTITLE__</div>
</header>
<div class="wrap">
  <div class="side">
    <h2>신뢰도 지표</h2>
    __METRICS__
    __LEGEND__
    __LOWBOX__
    __INDEXLINK__
  </div>
  <div class="col">
    <div class="bar">
      <span>색칠</span>
      <button id="b-plddt" onclick="af3Click('plddt')">pLDDT</button>
      <button id="b-chain" onclick="af3Click('chain')">사슬별</button>
      <button onclick="af3Click('reset')">시점 초기화</button>
      <span class="hint" id="engine"></span>
    </div>
    <div class="view">
      <div id="viewer"></div>
      <div id="status">구조를 불러오는 중이다.</div>
    </div>
  </div>
</div>
<script id="af3-cif" type="text/plain">__CIF__</script>
<script>
var AF3 = __DATA__;
var AF3_B64 = document.getElementById('af3-cif').textContent.trim();
if (AF3_B64) {
  var AF3_RAW = atob(AF3_B64), AF3_BYTES = new Uint8Array(AF3_RAW.length);
  for (var AF3_I = 0; AF3_I < AF3_RAW.length; AF3_I++) AF3_BYTES[AF3_I] = AF3_RAW.charCodeAt(AF3_I);
  AF3.cif = new TextDecoder('utf-8').decode(AF3_BYTES);
} else { AF3.cif = ''; }
function af3MarkButton(mode){
  var ids = {plddt:'b-plddt', chain:'b-chain'};
  for (var k in ids) {
    var el = document.getElementById(ids[k]);
    if (el) el.className = (k === mode) ? 'on' : '';
  }
  var lp = document.getElementById('leg-plddt'), lc = document.getElementById('leg-chain');
  if (lp) lp.style.display = (mode === 'chain') ? 'none' : '';
  if (lc) lc.style.display = (mode === 'chain') ? '' : 'none';
}
function af3SetChainLegend(map){
  // 사슬 범례의 색 견본을 렌더러가 실제로 쓴 색으로 바꾼다.
  // 범례와 화면 색이 다르면 범례가 거짓말을 하는 것이므로 반드시 맞춘다.
  var box = document.getElementById('leg-chain-list');
  if (!box || !map) return;
  var items = box.getElementsByTagName('li');
  for (var i = 0; i < items.length; i++) {
    var ch = items[i].getAttribute('data-chain');
    var sw = items[i].getElementsByClassName('sw')[0];
    if (ch && map[ch] && sw) sw.style.background = map[ch];
  }
  var n = document.getElementById('leg-chain-note');
  if (n) n.textContent = '견본 색은 이 화면이 실제로 쓴 색이다.';
}
function af3Click(what){
  if (what === 'reset') { if (window.af3ResetView) window.af3ResetView(); return; }
  if (window.af3SetColor) window.af3SetColor(what);
}
function af3Ready(engine){
  var s = document.getElementById('status');
  if (s) s.style.display = 'none';
  var e = document.getElementById('engine');
  if (e) e.textContent = '엔진: ' + engine;
  window.__af3_engine = engine;
  window.__af3_ready = true;
}
function af3Fail(msg){
  var s = document.getElementById('status');
  if (s) s.innerHTML = '<div class="bad"><b>구조를 표시하지 못했다.</b><br>'
    + msg.replace(/</g,'&lt;') + '<br><br>__FAILHINT__</div>';
  window.__af3_error = msg;
}
</script>
__LIBBODY__
<script>__ENGINEJS__</script>
</body></html>
"""


def js_string_block(text):
    """Encode mmCIF as base64 for a non-executable script data block."""

    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def html_metrics(rec):
    rows = metric_rows(rec)
    if not rows:
        return '<div class="note">_summary_confidences.json 을 읽지 못해 지표가 없다.</div>'
    out = ['<table class="m">']
    for name, val, hint in rows:
        out.append('<tr><td class="k">%s%s</td><td class="v">%s</td></tr>'
                   % (html.escape(name),
                      ('<br><span class="hint">%s</span>' % html.escape(hint))
                      if hint else "",
                      html.escape(str(val))))
    out.append("</table>")
    return "\n".join(out)


def html_legend(rec):
    hist = plddt_histogram(rec["residues"]) if rec["residues"] else []
    out = ['<div id="leg-plddt">', "<h2>pLDDT 색 구간</h2>", '<ul class="leg">']
    for color, label, cnt, pct in (hist or [(c, l, 0, 0.0)
                                            for _lo, _hi, c, l in
                                            [(b[0], b[1], b[2], b[3])
                                             for b in PLDDT_BANDS]]):
        n = ('<span class="n">%d 잔기 (%.0f%%)</span>' % (cnt, pct)) if hist else ""
        out.append('<li><span class="sw" style="background:%s"></span>%s%s</li>'
                   % (color, html.escape(label), n))
    out.append("</ul>")
    out.append('<div class="hint">경계값 90 / 70 / 50 은 AlphaFold DB 와 같다. '
               '색도 같다. 다른 도구에서 본 그림과 비교할 수 있다.</div>')
    out.append("</div>")

    out.append('<div id="leg-chain" style="display:none">')
    out.append("<h2>사슬 색</h2>")
    if rec["chains"]:
        # 견본 색은 페이지가 열릴 때 af3SetChainLegend 가 렌더러의 실제 색으로
        # 덮어쓴다 (Mol* 은 자체 사슬 색표를 쓴다). 여기 값은 그 전의 임시 색이다.
        out.append('<ul class="leg" id="leg-chain-list">')
        for i, ch in enumerate(rec["chains"]):
            out.append('<li data-chain="%s"><span class="sw" style="background:%s">'
                       "</span>사슬 %s</li>"
                       % (html.escape(ch, quote=True),
                          CHAIN_COLORS[i % len(CHAIN_COLORS)], html.escape(ch)))
        out.append("</ul>")
        out.append('<div class="hint" id="leg-chain-note">견본 색을 화면 색으로 '
                   "맞추는 중이다.</div>")
    else:
        out.append('<div class="hint">사슬 정보를 읽지 못했다.</div>')
    out.append("</div>")
    return "\n".join(out)


def html_lowbox(rec):
    if not rec["residues"]:
        return ""
    low = low_stretches(rec["residues"])
    out = ["<h2>pLDDT 70 미만 연속 구간</h2>"]
    if not low:
        out.append('<div class="note">3잔기 이상 이어지는 낮은 구간이 없다. '
                   '전체가 고르게 확실하다.</div>')
    else:
        out.append('<ul class="tight">')
        for ch, a, b, n, mean in low[:12]:
            out.append("<li>사슬 %s: %d~%d (%d잔기, 평균 %.1f)</li>"
                       % (html.escape(ch), a, b, n, mean))
        out.append("</ul>")
        if len(low) > 12:
            out.append('<div class="hint">그 외 %d개 더 있다.</div>' % (len(low) - 12))
        out.append('<div class="hint">VHH 는 CDR3(대략 95~115번 근처)이 낮게 나오는 '
                   '것이 흔하다. 루프라서 원래 유연한 것이고, 그것만으로 실패는 '
                   '아니다. 뼈대(프레임워크)까지 낮으면 의심해라.</div>')
    return "\n".join(out)


def build_page(rec, engine, lib_mode, lib_text, index_name):
    """타깃 하나의 HTML 문자열을 만든다."""
    spec = ENGINES[engine]
    engine_js = ENGINE_MOLSTAR_JS if engine == "molstar" else ENGINE_3DMOL_JS
    cdn_js = spec["js"][0]
    cdn_css = spec["css"][0] if spec["css"] else None

    libhead = []
    libbody = []
    if lib_mode == "embed" and lib_text:
        if engine == "molstar" and lib_text.get("css"):
            libhead.append("<style>%s</style>" % lib_text["css"])
        libbody.append("<script>%s</script>" % lib_text["js"])
        failhint = ("라이브러리는 이 파일 안에 들어 있다 (--lib embed). "
                    "인터넷 문제는 아니다. 브라우저를 최신으로 올려 보고, "
                    "그래도 안 되면 --engine 을 바꿔서 다시 만들어라.")
    else:
        if cdn_css:
            libhead.append(
                '<link rel="stylesheet" href="%s" integrity="%s" crossorigin="anonymous">'
                % (html.escape(cdn_css, quote=True), spec["sri_css"])
            )
        libbody.append(
            '<script src="%s" integrity="%s" crossorigin="anonymous"></script>'
            % (html.escape(cdn_js, quote=True), spec["sri_js"])
        )
        failhint = ("이 파일은 3D 라이브러리를 인터넷(CDN)에서 불러온다. "
                    "인터넷이 안 되거나 사내망이 CDN 을 막으면 이 화면이 나온다. "
                    "인터넷 없이 열려면 다시 만들어라: "
                    "python3 scripts/af3_view3d.py &lt;출력폴더&gt; --out-dir 뷰어 "
                    "--lib embed")

    data = {
        "target": rec["label"],
        "chains": rec["chains"],
        "res": rec["residues"],
        "bands": [b[2] for b in PLDDT_BANDS],
        "chainColors": CHAIN_COLORS,
    }

    sub = []
    if rec["mean_plddt"] is not None:
        sub.append("잔기 평균 pLDDT %.1f" % rec["mean_plddt"])
    if rec["rank"] is not None:
        sub.append("ranking score %s" % fmt_num(rec["rank"], 3))
    sub.append("결과 폴더 %s" % os.path.basename(rec["dir"]))
    subtitle = " · ".join(sub)

    problem_html = ""
    if rec["problem"]:
        problem_html = ('<div class="bad"><b>구조를 표시할 수 없다.</b><br>%s</div>'
                        % html.escape(rec["problem"]))

    metrics = problem_html + html_metrics(rec)
    idxlink = ""
    if index_name:
        idxlink = ('<h2>목록</h2><div><a href="%s">전체 타깃 목록으로</a></div>'
                   % html.escape(index_name, quote=True))

    page = PAGE_TMPL
    page = page.replace("__CSS__", PAGE_CSS)
    page = page.replace("__TITLE__", html.escape("%s - AF3 구조 보기" % rec["label"]))
    page = page.replace("__TARGET__", html.escape(rec["label"]))
    page = page.replace("__SUBTITLE__", html.escape(subtitle))
    page = page.replace("__METRICS__", metrics)
    page = page.replace("__LEGEND__", html_legend(rec))
    page = page.replace("__LOWBOX__", html_lowbox(rec))
    page = page.replace("__INDEXLINK__", idxlink)
    page = page.replace("__LIBHEAD__", "\n".join(libhead))
    page = page.replace("__LIBBODY__", "\n".join(libbody))
    page = page.replace("__FAILHINT__", failhint.replace("'", "\\'"))
    page = page.replace("__DATA__", script_safe_json(data))
    # __CIF__ 와 __ENGINEJS__ 는 마지막에 넣는다 (안에 __XXX__ 가 있어도 안전하게).
    page = page.replace("__ENGINEJS__", engine_js)
    if rec["cif"]:
        page = page.replace("__CIF__", js_string_block(rec["cif"]))
    else:
        page = page.replace("__CIF__", "")
        page = page.replace('<div id="status">구조를 불러오는 중이다.</div>',
                            '<div id="status"><div class="bad">%s</div></div>'
                            % html.escape(rec["problem"] or "mmCIF 가 없다"))
    return page


INDEX_TMPL = """<!DOCTYPE html>
<html lang="ko"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:; connect-src 'none'">
<title>AF3 구조 보기 목록</title>
<style>__CSS__
body { padding:14px 18px; }
table.t { border-collapse:collapse; width:100%; font-size:13px; margin-top:8px; }
table.t th, table.t td { padding:5px 8px; border-bottom:1px solid #eee;
                         text-align:right; white-space:nowrap; }
table.t th { border-bottom:2px solid #333; text-align:right; font-weight:600;
             position:sticky; top:0; background:#fff; }
table.t th:first-child, table.t td:first-child { text-align:left; }
table.t td.num { font-variant-numeric:tabular-nums; }
table.t tr.err td { background:#fff4f4; }
.bars { display:inline-flex; height:12px; width:110px; border:1px solid #ccc;
        vertical-align:middle; }
.bars i { display:block; height:100%; }
.foot { margin-top:14px; font-size:12px; color:#666; }
</style></head><body>
<h1 style="font-size:18px;margin:0">AF3 구조 보기 목록</h1>
<div class="sub">__SUBTITLE__</div>
__BADBOX__
<table class="t">
<thead><tr>
<th>타깃</th><th>ranking score</th><th>pTM</th><th>ipTM</th>
<th>잔기 평균 pLDDT</th><th>사슬</th><th>잔기</th><th>pLDDT 구간 분포</th>
</tr></thead>
<tbody>
__ROWS__
</tbody></table>
<div class="foot">
정렬은 ranking score 내림차순이다. 이름을 누르면 구조가 열린다.<br>
ipTM 칸의 '-' 는 단량체라서 값이 없다는 뜻이다 (0 이 아니다).<br>
분포 막대는 왼쪽부터 __BANDTXT__ 순이다.
</div>
</body></html>
"""


def build_index(records, files, subtitle):
    """타깃 목록 HTML. ranking score 내림차순."""
    order = sorted(records,
                   key=lambda r: (r["rank"] is None,
                                  -(r["rank"] if r["rank"] is not None else 0),
                                  r["label"]))
    rows = []
    bad = [r for r in records if r["problem"]]
    for rec in order:
        s = rec["summary"]
        hist = plddt_histogram(rec["residues"]) if rec["residues"] else []
        bars = ""
        if hist:
            bars = ('<span class="bars">%s</span>'
                    % "".join('<i style="background:%s;width:%.1f%%"></i>'
                              % (c, pct) for c, _l, _n, pct in hist))
        iptm = fmt_num(s.get("iptm"), 3) or "-"
        link = html.escape(files.get(rec["label"], ""), quote=True)
        name_cell = ('<a href="%s">%s</a>' % (link, html.escape(rec["label"]))
                     if link else html.escape(rec["label"]))
        if rec["problem"]:
            name_cell += ('<br><span class="hint">%s</span>'
                          % html.escape(rec["problem"]))
        rows.append(
            '<tr%s><td>%s</td><td class="num">%s</td><td class="num">%s</td>'
            '<td class="num">%s</td><td class="num">%s</td><td class="num">%s</td>'
            '<td class="num">%s</td><td>%s</td></tr>'
            % (' class="err"' if rec["problem"] else "",
               name_cell,
               fmt_num(rec["rank"], 3) or "-",
               fmt_num(s.get("ptm"), 3) or "-",
               iptm,
               fmt_num(rec["mean_plddt"], 1) or "-",
               len(rec["chains"]) or "-",
               len(rec["residues"]) or "-",
               bars))

    badbox = ""
    if bad:
        items = "".join("<li>%s: %s</li>" % (html.escape(r["label"]),
                                             html.escape(r["problem"]))
                        for r in bad)
        badbox = ('<div class="bad"><b>구조를 표시할 수 없는 타깃 %d개</b>'
                  '<ul class="tight">%s</ul>'
                  '지표는 표에 그대로 보인다. 구조만 빠졌다.</div>'
                  % (len(bad), items))

    page = INDEX_TMPL.replace("__CSS__", PAGE_CSS)
    page = page.replace("__SUBTITLE__", html.escape(subtitle))
    page = page.replace("__BADBOX__", badbox)
    page = page.replace("__ROWS__", "\n".join(rows))
    page = page.replace("__BANDTXT__",
                        html.escape(" / ".join(b[3] for b in PLDDT_BANDS)))
    return page


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(
        prog="af3_view3d.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="AlphaFold 3 출력 폴더를 브라우저에서 돌려 보는 HTML 로 만든다. "
                    "만든 파일을 더블클릭하면 열린다. 파이썬 의존성은 없다.",
        epilog="""예시
  # 타깃 하나만
  python3 af3_view3d.py vhh_out --only vhh_7a50_1 --out-dir 뷰어

  # 출력 폴더 전체. 타깃별 HTML + index.html (ranking score 내림차순)
  python3 af3_view3d.py vhh_out --out-dir 뷰어

  # 상위 20건만 (2000건 스크리닝 뒤에 쓴다)
  python3 af3_view3d.py vhh_out --out-dir 뷰어 --top 20

  # 인터넷 없는 컴퓨터에서 열 것 (라이브러리를 HTML 안에 넣는다. 파일이 커진다)
  python3 af3_view3d.py vhh_out --out-dir 뷰어 --lib embed

  # 오프라인 파일을 작게 만들고 싶다 (3Dmol 은 약 0.53MB, Mol* 는 약 5MB)
  python3 af3_view3d.py vhh_out --out-dir 뷰어 --lib embed --engine 3dmol

만들어지는 것
  <out-dir>/<타깃>.html   구조 하나. 마우스 왼쪽 끌기로 돌리고, 스크롤로 확대한다
  <out-dir>/index.html    타깃 목록. ranking score 내림차순. 이름을 누르면 구조로 간다

읽는 것
  <타깃>_model.cif (또는 .cif.zst)    좌표와 원자별 pLDDT (B_iso_or_equiv 열)
  <타깃>_summary_confidences.json     ranking score / pTM / ipTM
  <타깃>_ranking_scores.csv           확산 샘플 간 산포
""")
    p.add_argument("outdir_af3", metavar="AF3출력폴더",
                   help="AF3 --output_dir 로 준 폴더. 그 아래 타깃 폴더들을 훑는다")
    p.add_argument("-o", "--out-dir", "--out", dest="out", default="af3_view3d",
                   help="HTML 을 저장할 폴더. 기본 af3_view3d "
                        "(없으면 만든다). -o 로도 준다")
    p.add_argument("--only", help="이 타깃만 (콤마로 나열). 타깃명으로 고른다 "
                                  "(폴더명을 줘도 받아준다)")
    p.add_argument("--top", type=int, default=0,
                   help="ranking score 상위 N개만 HTML 을 만든다. 기본 0 (전부). "
                        "index.html 에는 전부 나온다")
    p.add_argument("--max", type=int, default=200,
                   help="HTML 을 만들 최대 타깃 수. 기본 200 "
                        "(2000건을 다 만들면 시간과 디스크가 낭비된다)")
    p.add_argument("--all-runs", action="store_true",
                   help="같은 타깃의 결과 폴더가 여러 개일 때 전부 만든다 "
                        "(기본은 최신 1건). 파일 이름에 실행 시각이 붙는다")
    p.add_argument("--include-partial", action="store_true",
                   help="정식 완료가 아닌 폴더도 넣는다. 기본은 "
                        "_ranking_scores.csv / _model.cif / "
                        "_summary_confidences.json 세 개가 모두 있는 폴더만 본다")
    p.add_argument("--engine", choices=["molstar", "3dmol"], default="molstar",
                   help="3D 렌더 엔진. 기본 molstar "
                        "(RCSB PDB / AlphaFold DB 가 쓰는 뷰어. pLDDT 색칠이 내장이고 "
                        "색 경계가 AlphaFold DB 와 같다). "
                        "3dmol 은 훨씬 가벼워서 --lib embed 로 오프라인 파일을 만들 때 좋다")
    p.add_argument("--lib", choices=["cdn", "embed"], default="cdn",
                   help="라이브러리를 어디서 가져오는가. 기본 cdn "
                        "(HTML 이 작다. 열 때 인터넷이 필요하다). "
                        "embed 는 HTML 안에 넣어 인터넷 없이 열리게 한다 "
                        "(파일이 엔진 크기만큼 커진다)")
    p.add_argument("--lib-cache", default="~/.cache/af3_view3d",
                   help="embed 로 쓸 라이브러리를 내려받아 두는 곳. "
                        "기본 ~/.cache/af3_view3d. 한 번 받으면 다음부터 인터넷이 필요 없다")
    p.add_argument("--lib-file",
                   help="이미 가진 라이브러리 자바스크립트 파일을 직접 쓴다 "
                        "(완전 오프라인. molstar.js 또는 3Dmol-min.js)")
    p.add_argument("--lib-css-file",
                   help="Mol* 의 molstar.css 를 직접 쓴다 (--engine molstar --lib embed 용)")
    p.add_argument("--no-index", action="store_true",
                   help="index.html 을 만들지 않는다")
    p.add_argument("--index-name", default="index.html",
                   help="목록 파일 이름. 기본 index.html")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.top < 0:
        die("--top 은 0 이상이어야 한다.")
    if args.max <= 0:
        die("--max 는 1 이상이어야 한다.")
    if not args.no_index:
        try:
            args.index_name = output_basename(args.index_name)
        except ValueError as exc:
            die(str(exc))

    targets = find_targets(args.outdir_af3, args.only, all_runs=args.all_runs,
                           include_partial=args.include_partial)
    log("타깃 %d개를 찾았다 (이름은 폴더명이 아니라 산출물 파일 stem 에서 얻은 타깃명이다)."
        % len(targets))

    lib_text = None
    if args.lib == "embed":
        lib_text, msg = fetch_library(args.engine, args.lib_cache,
                                      args.lib_file, args.lib_css_file)
        if lib_text is None:
            die(msg)
        log("라이브러리 인라인 (%s): %s" % (args.engine, msg))
        log("      HTML 한 개마다 %s 가 붙는다. 건수가 많으면 --lib cdn 을 써라."
            % ENGINES[args.engine]["size_hint"])
    elif args.lib_file or args.lib_css_file:
        log("주의: --lib-file 은 --lib embed 일 때만 쓰인다. 지금은 무시한다.")

    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)

    # 자료를 먼저 모은다. 정렬(--top)과 목록을 위해 전부 필요하다.
    records = []
    for label, tdir, stem in targets:
        rec = gather_target(label, tdir, stem)
        if rec["problem"]:
            log("  %s: %s" % (label, rec["problem"]))
        records.append(rec)

    order = sorted(records,
                   key=lambda r: (r["rank"] is None,
                                  -(r["rank"] if r["rank"] is not None else 0),
                                  r["label"]))
    todo = order
    if args.top > 0:
        todo = order[:args.top]
        log("ranking score 상위 %d개만 HTML 을 만든다 (목록에는 %d개 전부 나온다)."
            % (len(todo), len(order)))
    if len(todo) > args.max:
        log("주의: 타깃이 %d개다. 앞 %d개만 만든다 (--max 로 조절)."
            % (len(todo), args.max))
        todo = todo[:args.max]

    index_name = None if args.no_index else args.index_name
    try:
        files = plan_output_names([rec["label"] for rec in todo], index_name)
    except ValueError as exc:
        die(str(exc))
    made = []
    for rec in todo:
        fname = files[rec["label"]]
        page = build_page(rec, args.engine, args.lib, lib_text, index_name)
        path = outdir / fname
        atomic_write_text(path, page)
        made.append(path)
        log("  만들었다: %s (%.2f MB)" % (path, path.stat().st_size / 1048576.0))

    if index_name:
        sub = ("타깃 %d개 · 원본 %s · 엔진 %s · 라이브러리 %s · %s"
               % (len(records), args.outdir_af3, args.engine, args.lib,
                  time.strftime("%Y-%m-%d %H:%M")))
        ipath = outdir / index_name
        atomic_write_text(ipath, build_index(records, files, sub))
        made.append(ipath)
        log("  만들었다: %s" % ipath)

    log("")
    log("끝났다. 파일 %d개를 '%s' 에 만들었다." % (len(made), outdir))
    if index_name:
        log("먼저 이것을 더블클릭해라:  %s" % (outdir / index_name))
    elif made:
        log("먼저 이것을 더블클릭해라:  %s" % made[0])
    if args.lib == "cdn":
        log("주의: 이 HTML 은 3D 라이브러리를 인터넷에서 불러온다. 인터넷이 없거나")
        log("      사내망이 CDN 을 막으면 구조가 안 뜬다 (안내문이 대신 나온다).")
        log("      인터넷 없이 열려면 --lib embed 로 다시 만들어라.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
