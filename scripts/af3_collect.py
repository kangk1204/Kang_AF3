#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
af3_collect.py - AlphaFold 3 출력 폴더를 훑어 타깃별 신뢰도 지표를 CSV 한 장으로 모은다.

무엇을 읽는가 (AF3 v3.0 출력 구조)
    <출력폴더>/<타깃이름>/
        <타깃>_summary_confidences.json   대표 모델의 요약 지표 (ranking_score, ptm, iptm ...)
        <타깃>_confidences.json           원자별 pLDDT, 토큰별 PAE
        <타깃>_ranking_scores.csv         시드 x 샘플 전체의 ranking score
        <타깃>_data.json                  MSA가 담긴 입력 (깊이 계산에 사용)
        seed-<S>_sample-<N>/              샘플별 동일 3종 파일
    최상위(타깃 폴더 바로 아래)의 파일이 'AF3가 1위로 뽑은 모델'이다.
    이 스크립트는 기본적으로 그 1위 모델을 타깃의 대표값으로 쓰고,
    ranking_scores.csv 로 샘플 간 산포(재현성)를 함께 계산한다.

의존성
    표준 라이브러리만 쓴다. pandas/numpy 가 없는 서버에서도 그대로 돌아간다.
    설치할 것이 없다. python3.8 이상이면 된다.
    (그림을 그리는 af3_visualize.py 만 matplotlib 이 필요하고,
     이 스크립트는 아무것도 필요 없다.)

출력 파일 이름
    2026-04 부터 -o 를 생략했을 때의 기본 이름이 af3_summary.csv 다
    (예전에는 af3_결과요약.csv 였다). 옛 이름은 --filename-lang ko 로 그대로 쓸 수 있다
    (--lang 은 같은 뜻의 별칭이다). CSV 안의 열 이름은 바뀌지 않았다.

사용법
    # 기본: 출력 폴더 하나를 훑어 CSV 로 (기본 이름 af3_summary.csv)
    python3 af3_collect.py vhh_001_out

    # 저장 이름을 직접 정한다
    python3 af3_collect.py vhh_001_out -o af3_summary.csv

    # 옛 한글 기본 이름(af3_결과요약.csv)을 쓴다
    python3 af3_collect.py vhh_001_out --filename-lang ko

    # 여러 폴더를 한 CSV 로 (조건 비교). --label 로 조건 이름을 붙인다
    python3 af3_collect.py 축소=af3out_reduced 전체=af3out_full -o 비교.csv

    # MSA 깊이는 *_data.json 을 읽어야 하므로 느리다. 필요 없으면 끈다
    python3 af3_collect.py vhh_001_out --no-msa-depth -o 요약.csv

    # 상위 후보만 골라내기 (2단계 전략의 재실행 목록 만들기)
    python3 af3_collect.py vhh_001_out --top 100 --top-list top100.txt

타깃명은 어디서 오는가 (2026-08 수정)
    폴더 이름이 아니라 폴더 안 산출물 파일의 stem 에서 얻는다.
    AF3 는 출력 폴더가 비어 있지 않으면 <타깃>_<YYYYmmdd_HHMMSS> 폴더를 새로 만드는데
    (run_alphafold.py:861), 그 안의 파일 stem 은 원래 타깃명 그대로다. 예전에는
    폴더명을 타깃명으로 썼기 때문에 재실행 결과가 'VHH_004_20260820_101010' 이라는
    별개 타깃으로 집계됐다. 자세한 근거와 규칙은 docs/naming_fix_notes.md 를 봐라.

    같은 타깃이 여러 폴더에 있으면 기본으로 최신 실행 1건만 집계한다.
    몇 번 돌았는지는 '실행수' 열, 어느 폴더인지는 '폴더명' 열에 적힌다.
    전부 보려면 --all-runs 를 쓴다 (그러면 --top 순위가 왜곡되므로 대조용으로만).

주의
    * 이 스크립트는 읽기만 한다. 출력 폴더의 어떤 파일도 수정/삭제하지 않는다.
    * macOS 에서 만든 tar 를 리눅스에서 풀면 '._' 로 시작하는 AppleDouble 사이드카가
      생긴다. UTF-8 이 아니어서 읽으면 죽는다. 이 스크립트는 전부 건너뛴다.
    * 점(.)으로 시작하는 폴더는 전부 건너뛴다. 배치 러너의 격리 폴더
      (.af3_incomplete/), staging(.af3_pending_*), lock(.run_af3_batch.lock) 이
      그 안에 들어간다. 격리 폴더에는 미완료 결과가 있으므로 집계에 섞이면 안 된다.
"""

import argparse
import csv
import json
import math
import os
import re
import string
import sys
import time
from pathlib import Path


def csv_safe_cell(value):
    """Prevent spreadsheet programs from evaluating untrusted text as a formula."""
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


# ---------------------------------------------------------------------------
# AF3 기본 패딩 버킷 사다리.
# run_alphafold.py 의 _BUCKETS 기본값과 동일하며 128 에서 시작한다 (실측 확인).
# 단백질만 있는 130 aa 입력은 128을 넘으므로 256 버킷을 쓴다. 버킷별 속도 차이는
# GPU와 실행 설정마다 달라지므로 이 스크립트는 크기 경고만 낸다.
# ---------------------------------------------------------------------------
DEFAULT_BUCKETS = [128, 256, 384, 512, 768, 1024, 1280, 1536, 2048, 2560,
                   3072, 3584, 4096, 4608, 5120]


def log(msg):
    print(msg, file=sys.stderr, flush=True)


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


def load_json(path):
    """UTF-8 로 읽고, 실패하면 예외 대신 None 을 준다(한 건 때문에 전체가 죽지 않게)."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError, UnicodeDecodeError) as e:
        log("  경고: %s 를 읽을 수 없다 (%s)" % (path.name, e))
        return None


# ---------------------------------------------------------------------------
# 통계 (numpy 없이)
# ---------------------------------------------------------------------------
def mean(xs):
    return sum(xs) / len(xs) if xs else None


def median(xs):
    if not xs:
        return None
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2])


def percentile(xs, q):
    """선형 보간 백분위수 (numpy.percentile 기본 동작과 동일)."""
    if not xs:
        return None
    s = sorted(xs)
    if len(s) == 1:
        return s[0]
    pos = (len(s) - 1) * (q / 100.0)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return s[lo]
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def frac_ge(xs, thr):
    return (sum(1 for x in xs if x >= thr) / len(xs)) if xs else None


def r4(x):
    return None if x is None else round(x, 4)


def finite_number(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def safe_target_name(value):
    return (
        isinstance(value, str)
        and bool(value)
        and value[0] not in ".=+-@"
        and all(ch in (string.ascii_letters + string.digits + "_-.") for ch in value)
    )


# ---------------------------------------------------------------------------
# MSA 깊이 — *_data.json 을 전부 파싱하지 않고 문자열 스캔으로 센다.
# 전체 DB 로 만든 _data.json 은 10 MB 급이라 json.load 하면 2000건에서 매우 느리다.
# MSA 는 A3M 문자열이고 JSON 안에서는 줄바꿈이 '\n' 두 글자로 이스케이프되어 있으므로,
# 이스케이프된 '>' 헤더 개수를 세면 그것이 서열 수(=깊이)다.
# ---------------------------------------------------------------------------
_MSA_KEYS = ("unpairedMsa", "pairedMsa")


def _string_value_span(text, key_pos):
    """`"key"` 위치에서 시작해 그 값 문자열의 (시작, 끝) 인덱스를 찾는다.

    값이 문자열이 아니면(null 등) None 을 준다.
    """
    colon = text.find(":", key_pos)
    if colon < 0:
        return None
    i = colon + 1
    while i < len(text) and text[i] in " \t\r\n":
        i += 1
    if i >= len(text) or text[i] != '"':
        return None          # null 또는 다른 형식
    start = i + 1
    j = start
    while True:
        j = text.find('"', j)
        if j < 0:
            return None
        # 앞에 붙은 역슬래시가 짝수 개면 진짜 종료 따옴표다
        k = j - 1
        bs = 0
        while k >= start and text[k] == "\\":
            bs += 1
            k -= 1
        if bs % 2 == 0:
            return start, j
        j += 1


def msa_depths(data_json_path):
    """(unpaired 깊이, paired 깊이) 를 단백질 체인별 최소값으로 돌려준다.

    깊이 = A3M 문자열 안의 '>' 헤더 줄 수. 빈 문자열이면 0, 키가 null 이면 None.
    """
    try:
        text = data_json_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        log("  경고: %s 의 MSA 깊이를 읽을 수 없다 (%s)" % (data_json_path.name, e))
        return None, None

    out = {}
    for key in _MSA_KEYS:
        depths = []
        pat = '"%s"' % key
        pos = 0
        while True:
            pos = text.find(pat, pos)
            if pos < 0:
                break
            span = _string_value_span(text, pos)
            pos += len(pat)
            if span is None:
                continue              # null (아직 MSA 미계산)
            s, e = span
            body = text[s:e]
            if not body:
                depths.append(0)
                continue
            # 첫 줄이 '>' 로 시작하고, 이후 줄바꿈은 '\n' 두 글자로 이스케이프돼 있다
            n = body.count("\\n>")
            if body.startswith(">"):
                n += 1
            depths.append(n)
        out[key] = min(depths) if depths else None
    return out["unpairedMsa"], out["pairedMsa"]


# ---------------------------------------------------------------------------
# 타깃 하나 읽기
# ---------------------------------------------------------------------------
def bucket_for(n_tokens, ladder=None):
    ladder = ladder or DEFAULT_BUCKETS
    fit = [b for b in ladder if b >= n_tokens]
    return min(fit) if fit else n_tokens


def read_ranking_csv(path):
    """seed,sample,ranking_score 전량을 읽어 산포를 계산한다."""
    rows = []
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as fh:
            for rec in csv.DictReader(fh):
                try:
                    value = float(rec["ranking_score"])
                    if math.isfinite(value):
                        rows.append(value)
                except (KeyError, TypeError, ValueError):
                    continue
    except OSError:
        return None
    return rows or None


def collect_target(tdir, want_msa=True, info=None):
    """타깃 폴더 하나에서 지표를 뽑는다. 완료되지 않은 폴더는 None.

    타깃명은 폴더 이름이 아니라 resolve_result_dir 이 정한 값(산출물 파일 stem)을 쓴다.
    폴더 이름을 쓰면 AF3 재실행 폴더(<타깃>_20260820_101010)가 별개 타깃으로 집계된다.
    """
    if info is None:
        info = resolve_result_dir(tdir, mode="full")
    if not info["complete"]:
        return None
    stem = info["stem"]
    summ_p = tdir / ("%s_summary_confidences.json" % stem)
    conf_p = tdir / ("%s_confidences.json" % stem)
    rank_p = tdir / ("%s_ranking_scores.csv" % stem)
    data_p = tdir / ("%s_data.json" % stem)

    summ = load_json(summ_p)
    if not isinstance(summ, dict):
        return None

    row = {
        "타깃": info["target"],
        "폴더명": tdir.name,
        "실행시각": info["run_ts"] or "",
        "ranking_score": finite_number(summ.get("ranking_score")),
        "pTM": finite_number(summ.get("ptm")),
        "ipTM": finite_number(summ.get("iptm")),
        "fraction_disordered": finite_number(summ.get("fraction_disordered")),
        "has_clash": finite_number(summ.get("has_clash")),
    }

    # 체인별 지표 (복합체에서 어느 체인이 문제인지 보려면 필요)
    cp = summ.get("chain_ptm")
    ci = summ.get("chain_iptm")
    row["chain_pTM"] = ";".join("" if v is None else str(v) for v in cp) if isinstance(cp, list) else ""
    row["chain_ipTM"] = ";".join("" if v is None else str(v) for v in ci) if isinstance(ci, list) else ""
    cpi = summ.get("chain_pair_iptm")
    cpi_is_square = (
        isinstance(cpi, list)
        and all(isinstance(values, list) and len(values) == len(cpi) for values in cpi)
    )
    if cpi_is_square:
        off = [
            finite_number(cpi[i][j])
            for i in range(len(cpi))
            for j in range(len(cpi))
            if i != j
        ]
        off = [value for value in off if value is not None]
        row["min_chain_pair_ipTM"] = min(off) if off else None
    else:
        if cpi not in (None, []) and isinstance(cpi, list):
            log("  경고: %s 의 chain_pair_iptm 행렬이 정사각형이 아니다" % summ_p.name)
        row["min_chain_pair_ipTM"] = None

    # ---- 원자 pLDDT 분포와 토큰 수 -------------------------------------
    plddts = []
    if conf_p.exists():
        conf = load_json(conf_p)
        if conf:
            plddts = [finite_number(v) for v in (conf.get("atom_plddts") or [])]
            plddts = [value for value in plddts if value is not None]
            tok = conf.get("token_chain_ids") or []
            row["토큰수"] = len(tok) or None
            row["원자수"] = len(conf.get("atom_chain_ids") or []) or None
            row["체인수"] = len(set(tok)) or None
            row["체인ID"] = ",".join(sorted(set(tok))) if tok else ""
    row.setdefault("토큰수", None)
    row.setdefault("원자수", None)
    row.setdefault("체인수", None)
    row.setdefault("체인ID", "")
    row["패딩버킷"] = bucket_for(row["토큰수"]) if row["토큰수"] else None

    row["pLDDT평균"] = r4(mean(plddts))
    row["pLDDT중앙값"] = r4(median(plddts))
    row["pLDDT최소"] = r4(min(plddts)) if plddts else None
    row["pLDDT_p10"] = r4(percentile(plddts, 10))
    row["pLDDT_70이상비율"] = r4(frac_ge(plddts, 70.0))
    row["pLDDT_90이상비율"] = r4(frac_ge(plddts, 90.0))

    # ---- 샘플 간 산포 (같은 입력을 여러 번 뽑았을 때의 재현성) -----------
    rs = read_ranking_csv(rank_p) if rank_p.exists() else None
    if rs:
        row["샘플수"] = len(rs)
        row["ranking최고"] = r4(max(rs))
        row["ranking최저"] = r4(min(rs))
        row["ranking산포"] = r4(max(rs) - min(rs))
    else:
        row["샘플수"] = None
        row["ranking최고"] = None
        row["ranking최저"] = None
        row["ranking산포"] = None

    # ---- MSA 깊이 ------------------------------------------------------
    if want_msa and data_p.exists():
        u, p = msa_depths(data_p)
        row["MSA_unpaired깊이"] = u
        row["MSA_paired깊이"] = p
    else:
        row["MSA_unpaired깊이"] = None
        row["MSA_paired깊이"] = None

    # ---- ranking_score 재구성 검산 --------------------------------------
    # AF3 의 ranking score 정의:
    #   0.8 x (ipTM 또는 단량체면 pTM) + 0.2 x pTM + 0.5 x fraction_disordered
    #   - 100 x has_clash
    # 이 열이 0 근처가 아니면 파일 짝이 안 맞는 것이다(다른 실행의 파일이 섞였다).
    try:
        i_or_p = row["ipTM"] if row["ipTM"] is not None else row["pTM"]
        recon = (0.8 * i_or_p + 0.2 * row["pTM"]
                 + 0.5 * row["fraction_disordered"] - 100.0 * row["has_clash"])
        row["ranking검산차"] = r4(row["ranking_score"] - recon)
    except (TypeError, KeyError):
        row["ranking검산차"] = None

    return row


# ---------------------------------------------------------------------------
# 등급 판정
# ---------------------------------------------------------------------------
GRADE_DOC = """등급 기준 (AlphaFold 계열의 통상적 해석 구간을 이 배치에 맞춰 적용)

  pLDDT (잔기/원자 단위 국소 정확도, 0~100)
      90 이상   매우 높음 - 측쇄 수준까지 신뢰
      70~90     신뢰      - 주사슬(백본) 신뢰
      50~70     낮음      - 접힘 방향 정도만
      50 미만   매우 낮음 - 구조가 없거나 무질서 영역
  pTM (예측된 TM-score, 0~1. 정답일 확률이 아님)
      0.5 초과  탐색용 경험적 구간. 정답 구조 보증선이 아님
  ipTM (예측 계면 신뢰도, 복합체에서만 산출)
      0.8 이상  높은 예측 신뢰 구간 (실제 계면 정확도 보증선이 아님)
      0.6~0.8   회색지대 - 판단 보류
      0.6 미만  계면 실패 가능성 높음

판정 규칙 (이 스크립트가 CSV '등급' 열에 쓰는 값)
  복합체(ipTM 있음): ipTM 을 1차 기준으로 쓴다.
      A_계면신뢰    ipTM >= 0.8 이고 pLDDT평균 >= 80
      B_계면회색    ipTM >= 0.6
      C_계면실패    그 외
  단량체(ipTM 없음): pLDDT 와 pTM 을 함께 본다.
      A_높음   pLDDT평균 >= 90 이고 pTM >= 0.7
      B_신뢰   pLDDT평균 >= 80 이고 pTM >= 0.5
      C_보통   pLDDT평균 >= 70
      D_낮음   그 외
  경고 열은 등급과 별개로 붙는다:
      충돌      has_clash > 0
      무질서    fraction_disordered >= 0.1
      MSA얕음   unpaired 깊이 < 100
      샘플불안  ranking 산포 >= 0.05
      버킷256   패딩 버킷이 256 이상 (128보다 큰 연산·메모리 구간; 배수는 장비별 측정)

주의: 이 구간은 예측 신뢰도(모델이 자기 예측을 얼마나 확신하는가)이지
정답과의 일치도가 아니다. 실험 검증 대상 선정용 순위 지표로만 쓸 것.
그리고 ranking_score 는 정의상 fraction_disordered 를 더하므로
무질서 비율이 높은 건이 pTM 보다 높게 나올 수 있다. 스크리닝 순위는
ranking_score 단독보다 pLDDT평균/pTM 을 함께 보고 정하는 편이 안전하다."""


def grade_row(row):
    plddt = row.get("pLDDT평균")
    ptm = row.get("pTM")
    iptm = row.get("ipTM")

    if iptm is not None:
        if iptm >= 0.8 and plddt is not None and plddt >= 80:
            g = "A_계면신뢰"
        elif iptm >= 0.6:
            g = "B_계면회색"
        else:
            g = "C_계면실패"
    else:
        if plddt is None or ptm is None:
            g = "판정불가"
        elif plddt >= 90 and ptm >= 0.7:
            g = "A_높음"
        elif plddt >= 80 and ptm >= 0.5:
            g = "B_신뢰"
        elif plddt >= 70:
            g = "C_보통"
        else:
            g = "D_낮음"

    warn = []
    if row.get("has_clash") and row["has_clash"] > 0:
        warn.append("충돌")
    fd = row.get("fraction_disordered")
    if fd is not None and fd >= 0.1:
        warn.append("무질서")
    u = row.get("MSA_unpaired깊이")
    if u is not None and u < 100:
        warn.append("MSA얕음")
    sp = row.get("ranking산포")
    if sp is not None and sp >= 0.05:
        warn.append("샘플불안")
    b = row.get("패딩버킷")
    if b is not None and b >= 256:
        warn.append("버킷256")
    d = row.get("ranking검산차")
    if d is not None and abs(d) > 0.02:
        warn.append("검산불일치")

    row["등급"] = g
    row["경고"] = ";".join(warn)
    return row


# ---------------------------------------------------------------------------
# 폴더 순회
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 기본 출력 파일 이름
#
# 2026-04 변경: -o 를 생략했을 때의 기본 이름을 af3_결과요약.csv 에서
#   af3_summary.csv 로 바꿨다.
#   왜: 한글 파일 이름은 후속 자동화에서 걸린다. 셸 반복문/엑셀 매크로에서
#       따옴표를 빼먹으면 깨지고, git 은 기본 설정에서 한글 경로를 8진 이스케이프로
#       출력하며, macOS(NFD) 와 리눅스(NFC) 사이에서 유니코드 정규화가 달라
#       같은 이름이 다른 이름으로 보인다.
#   근거: 저장소에 이미 커밋된 예시 파일이 ASCII 이름이었다
#       (results_example/af3_summary.csv). 도구가 만드는 이름과 저장소 예시
#       이름이 서로 달랐던 것이 원래 문제다. 새 관례를 만든 것이 아니다.
#   호환: 옛 이름이 필요하면 두 가지 방법이 있다. 둘 다 내용은 완전히 같다.
#         1) --filename-lang ko   (기본 이름을 af3_결과요약.csv 로.
#                                  --lang 은 같은 뜻의 별칭이다 - 이 스크립트는
#                                  그림을 그리지 않으므로 '라벨 언어' 라는 다른
#                                  뜻이 없다. af3_visualize.py 의 --lang 은
#                                  그림 안 라벨 언어이므로 뜻이 다르다)
#         2) -o af3_결과요약.csv   (직접 지정. 예전 문서의 명령이 이 형태다)
#   주의: CSV 안의 열 이름(조건/타깃/등급...)은 바꾸지 않았다. 이미 이 열을 참조하는
#         엑셀 시트가 있을 수 있고, 열 이름은 파일 이름과 달리 도구 연결에서
#         문제를 일으키지 않는다.
# ---------------------------------------------------------------------------
DEFAULT_OUT = {"en": "af3_summary.csv", "ko": "af3_결과요약.csv"}

COLUMNS = ["조건", "타깃", "등급", "경고",
           "ranking_score", "pTM", "ipTM", "pLDDT평균", "pLDDT중앙값",
           "pLDDT최소", "pLDDT_p10", "pLDDT_70이상비율", "pLDDT_90이상비율",
           "fraction_disordered", "has_clash",
           "MSA_unpaired깊이", "MSA_paired깊이",
           "토큰수", "원자수", "체인수", "체인ID", "패딩버킷",
           "샘플수", "ranking최고", "ranking최저", "ranking산포",
           "chain_pTM", "chain_ipTM", "min_chain_pair_ipTM",
           "ranking검산차", "출력경로",
           # 아래 4개는 타깃명 정규화 때 새로 붙인 열이다. 기존 열의 이름과 순서는
           # 그대로 두었으므로 이 CSV 를 읽던 스크립트/엑셀 서식은 계속 동작한다.
           "폴더명", "실행시각", "실행수", "중복정책"]


# ---------------------------------------------------------------------------
# 같은 타깃이 여러 폴더에 있을 때의 정책
#
# 왜 이런 일이 생기는가: AF3 는 출력 폴더가 비어 있지 않으면 <타깃>_<타임스탬프>
# 폴더를 새로 만든다. 그래서 중단 후 재실행하면 같은 타깃의 결과가 2개 이상 남는다.
# 2000건 배치에서는 흔한 상황이다.
#
# 정한 것: 기본은 '타깃별 최신 실행 1건만 집계표에 넣는다'.
# 근거
#   1) 이 CSV 의 용도는 상위 후보 선별이다. 같은 타깃이 두 줄이면 --top 100 이
#      실제로는 90여 개 타깃만 고르게 되고, 사용자는 그 사실을 알 수 없다.
#      한 타깃 = 한 줄이어야 순위와 컷오프가 뜻을 갖는다.
#   2) 실험 기반 초보 사용자가 '어느 줄이 최신인가' 를 폴더명으로 판독해야 하는
#      상황을 만들지 않는다. 최신 판정은 도구가 하고 근거를 열에 적는다.
#   3) 버린 실행을 감추지는 않는다. '실행수' 열에 몇 번 돌았는지 적고,
#      화면 요약에 중복 타깃을 나열하고, --all-runs 로 전부 볼 수 있게 한다.
# 최신의 기준: 폴더명의 AF3 타임스탬프 접미사가 1순위(AF3 가 직접 찍은 값),
#   없으면 산출물 파일 mtime. dir_run_time 참고.
# 접미사 없는 폴더는 첫 실행이므로 접미사 있는 폴더보다 항상 오래된 것으로 취급된다
#   (첫 실행이 있어야 두 번째 실행에서 접미사가 붙는다).
# ---------------------------------------------------------------------------
def walk_output_dir(root, label, want_msa=True, all_runs=False):
    """출력 폴더를 훑어 (행 목록, 미완료 목록) 을 준다.

    반환하는 행에는 '폴더명', '실행시각', '실행수', '중복정책' 열이 붙는다.
    미완료 목록은 (타깃명, 폴더명) 쌍이다 - 재시도할 때 필요한 것은 타깃명이다.
    """
    root = Path(root).expanduser()
    rows, incomplete = [], []
    if not root.is_dir():
        log("오류: 출력 폴더가 없다: %s" % root)
        return rows, incomplete

    # 1) 폴더별로 타깃명과 완료 여부를 먼저 판정한다.
    #    is_sidecar 로 점으로 시작하는 항목(.af3_incomplete, .af3_pending_*, lock)을
    #    여기서 통째로 배제한다. 격리된 미완료 결과가 집계에 섞이면 안 된다.
    resolved = []
    for tdir in sorted(p for p in root.iterdir()
                       if p.is_dir() and not is_sidecar(p.name)):
        info = resolve_result_dir(tdir, mode="full")
        if not safe_target_name(info["target"]):
            log("  경고: 안전하지 않은 타깃 이름을 건너뜀: %r (%s)" % (info["target"], tdir))
            incomplete.append((info["target"], tdir.name))
            continue
        if info["note"]:
            log("  주의: %s - %s" % (tdir.name, info["note"]))
        resolved.append((tdir, info))

    # 2) 타깃별로 묶어 최신 하나를 고른다.
    by_target = {}
    for tdir, info in resolved:
        if not info["complete"]:
            incomplete.append((info["target"], tdir.name))
            continue
        by_target.setdefault(info["target"], []).append((tdir, info))

    dup_targets = []
    for target in sorted(by_target):
        runs = by_target[target]
        runs.sort(key=lambda ti: (dir_run_time(ti[0], ti[1]), ti[0].name))
        if len(runs) > 1:
            dup_targets.append((target, [t.name for t, _i in runs]))
        chosen = runs if all_runs else [runs[-1]]   # 정렬 결과의 마지막이 최신
        for tdir, info in chosen:
            row = collect_target(tdir, want_msa=want_msa, info=info)
            if row is None:
                incomplete.append((info["target"], tdir.name))
                continue
            row["조건"] = label
            row["출력경로"] = str(tdir)
            row["실행수"] = len(runs)
            if len(runs) == 1:
                row["중복정책"] = ""
            elif all_runs:
                row["중복정책"] = ("전체표시(최신=%s)" % runs[-1][0].name)
            else:
                row["중복정책"] = ("최신선택(%d개중)" % len(runs))
            rows.append(grade_row(row))

    if dup_targets:
        log("  같은 타깃이 여러 폴더에 있다 %d건 (%s 정책 적용):"
            % (len(dup_targets), "전체표시" if all_runs else "최신 1건만 집계"))
        for target, names in dup_targets[:10]:
            log("      %-20s %s  -> 최신 %s" % (target, ", ".join(names), names[-1]))
        if len(dup_targets) > 10:
            log("      ... (%d건 더)" % (len(dup_targets) - 10))
    return rows, incomplete


def parse_spec(spec):
    """'라벨=경로' 또는 '경로' 를 받아 (라벨, 경로) 로."""
    if "=" in spec:
        lab, path = spec.split("=", 1)
        return lab, path
    return Path(spec).name, spec


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="AlphaFold 3 출력에서 타깃별 신뢰도 지표를 CSV 로 모은다",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=GRADE_DOC)
    ap.add_argument("outputs", nargs="+",
                    help="AF3 출력 폴더. '라벨=경로' 형식으로 조건 이름을 붙일 수 있다")
    ap.add_argument("-o", "--out", default=None,
                    help="CSV 저장 경로. 생략하면 --filename-lang 에 따라 정해진다 "
                         "(en: af3_summary.csv, ko: af3_결과요약.csv)")
    ap.add_argument("--filename-lang", "--lang", dest="filename_lang",
                    choices=["en", "ko"], default="en",
                    help="-o 를 생략했을 때 쓸 기본 파일 이름의 언어. 기본 en "
                         "(af3_summary.csv). ko 를 주면 옛 이름 af3_결과요약.csv 를 쓴다. "
                         "2026-04 에 기본값이 ko 에서 en 으로 바뀌었다. "
                         "-o 로 직접 준 경로에는 영향이 없다. "
                         "--lang 은 같은 뜻의 별칭이다")
    ap.add_argument("--all-runs", action="store_true",
                    help="같은 타깃이 여러 폴더에 있을 때 전부 집계표에 넣는다 "
                         "(기본은 최신 1건만). 어느 실행이 어떤 값이었는지 대조할 때 쓴다. "
                         "이 옵션을 켜면 같은 타깃이 여러 줄이 되므로 --top 순위가 왜곡된다")
    ap.add_argument("--no-msa-depth", action="store_true",
                    help="MSA 깊이 계산을 건너뛴다 (*_data.json 을 읽지 않아 빠르다)")
    ap.add_argument("--top", type=int, default=None,
                    help="상위 N건 목록을 뽑는다 (2단계 전략의 재실행 후보 선정)")
    ap.add_argument("--top-by", default="ranking_score",
                    choices=["ranking_score", "pLDDT평균", "pTM", "ipTM"],
                    help="상위 N건을 무엇으로 정렬할지 (기본 ranking_score)")
    ap.add_argument("--top-list", default=None,
                    help="상위 N건의 타깃 이름을 이 파일에 한 줄씩 저장")
    ap.add_argument("--top-condition", default=None,
                    help="상위 N건을 이 조건(라벨)에서만 고른다. 여러 조건을 함께 집계했을 때 "
                         "같은 타깃이 중복 선정되는 것을 막는다")
    ap.add_argument("--grade-doc", action="store_true",
                    help="등급 기준 설명만 출력하고 종료")
    args = ap.parse_args(argv)

    if args.grade_doc:
        print(GRADE_DOC)
        return 0

    if args.top is not None and args.top <= 0:
        log("오류: --top 은 1 이상이어야 한다.")
        return 2

    missing_roots = []
    for spec in args.outputs:
        _label, path = parse_spec(spec)
        if not Path(path).expanduser().is_dir():
            missing_roots.append(path)
    if missing_roots:
        for path in missing_roots:
            log("오류: 출력 폴더가 없다: %s" % path)
        return 2

    all_rows, all_incomplete = [], []
    for spec in args.outputs:
        label, path = parse_spec(spec)
        rows, inc = walk_output_dir(path, label, want_msa=not args.no_msa_depth,
                                    all_runs=args.all_runs)
        log("%-14s %s : 완료 %d건, 미완성/건너뜀 %d건" % (label, path, len(rows), len(inc)))
        all_rows += rows
        # inc 는 (타깃명, 폴더명) 쌍이다. 재시도에 필요한 것은 타깃명이다.
        all_incomplete += [(label, tgt, dname) for tgt, dname in inc]

    if not all_rows:
        log("오류: 집계할 완료 결과가 없다. 출력 폴더 경로를 확인하라.")
        return 1

    out = Path(args.out) if args.out else Path(DEFAULT_OUT[args.filename_lang])
    if out.parent != Path(""):
        out.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig: 엑셀에서 한글 열 이름이 깨지지 않게
    with open(out, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        for r in all_rows:
            w.writerow({key: csv_safe_cell(value) for key, value in r.items()})

    # ---- 화면 요약 -------------------------------------------------------
    from collections import Counter
    print()
    print("집계 완료: %d건 -> %s" % (len(all_rows), out))
    if not args.out and args.filename_lang == "en":
        print("  [알림] 2026-04 부터 기본 파일 이름이 af3_결과요약.csv 에서")
        print("         af3_summary.csv 로 바뀌었다. 내용과 열 이름은 그대로다.")
        print("         옛 이름이 필요하면 --filename-lang ko 또는")
        print("         -o af3_결과요약.csv 를 써라.")
    gc = Counter(r["등급"] for r in all_rows)
    for g in sorted(gc):
        print("  %-12s %4d건 (%.1f%%)" % (g, gc[g], 100.0 * gc[g] / len(all_rows)))
    wc = Counter(w for r in all_rows for w in (r["경고"].split(";") if r["경고"] else []))
    if wc:
        print("  경고: " + ", ".join("%s %d건" % (k, v) for k, v in wc.most_common()))
    if all_incomplete:
        print("  미완성/건너뜀 %d건 (재시도 대상): %s"
              % (len(all_incomplete),
                 ", ".join(t for _l, t, _d in all_incomplete[:10])))
        print("     ※ 여기 적힌 것은 타깃명이다 (폴더명이 아니다). 재시도할 때 이 이름을 쓴다.")
    bad = [r["타깃"] for r in all_rows
           if r["ranking검산차"] is not None and abs(r["ranking검산차"]) > 0.02]
    if bad:
        print("  주의: ranking_score 검산 불일치 %d건 - 다른 실행의 파일이 섞였을 수 있다: %s"
              % (len(bad), ", ".join(bad[:5])))
    else:
        print("  ranking_score 검산: 전건 일치 (파일 짝이 맞다)")

    if args.top:
        key = args.top_by
        havekey = [r for r in all_rows if r.get(key) is not None]
        conds = sorted(set(r["조건"] for r in havekey))
        if args.top_condition:
            havekey = [r for r in havekey if r["조건"] == args.top_condition]
            if not havekey:
                log("오류: --top-condition '%s' 에 해당하는 행이 없다. 있는 조건: %s"
                    % (args.top_condition, ", ".join(conds)))
                return 1
        elif len(conds) > 1:
            log("경고: 조건이 %d개(%s) 섞여 있어 같은 타깃이 중복 선정될 수 있다. "
                "--top-condition <라벨> 로 한 조건만 지정하라."
                % (len(conds), ", ".join(conds)))
        havekey.sort(key=lambda r: r[key], reverse=True)
        # 같은 타깃이 여러 줄일 수 있다(--all-runs, 또는 조건이 여러 개).
        # 상위 N '건' 은 상위 N '타깃' 이어야 뜻이 있으므로 타깃 단위로 중복을 걷어낸다.
        # (조건이 여러 개일 때는 조건+타깃이 한 단위다 - 조건 비교가 용도이므로)
        seen, dedup, dropped = set(), [], 0
        multi_cond = len(conds) > 1 and not args.top_condition
        for r in havekey:
            k = (r["조건"], r["타깃"]) if multi_cond else r["타깃"]
            if k in seen:
                dropped += 1
                continue
            seen.add(k)
            dedup.append(r)
        if dropped:
            log("경고: 같은 타깃의 중복 행 %d개를 상위 선별에서 제외했다 "
                "(각 타깃의 최고값 행만 남긴다)." % dropped)
        havekey = dedup
        top = havekey[:args.top]
        print()
        print("상위 %d건 (%s 기준). 이 목록이 2단계 전략의 재실행 후보다." % (len(top), key))
        for r in top[:20]:
            print("  %-10s %-24s %s=%-8s pLDDT=%-8s 등급=%-10s 폴더=%s"
                  % (r["조건"], r["타깃"], key, r[key], r["pLDDT평균"], r["등급"],
                     r.get("폴더명", "")))
        if len(top) > 20:
            print("  ... (%d건 더)" % (len(top) - 20))
        if top:
            print("  컷오프 값: %s = %s" % (key, top[-1][key]))
        if args.top_list:
            top_list = Path(args.top_list)
            top_list.parent.mkdir(parents=True, exist_ok=True)
            with open(top_list, "w", encoding="utf-8") as fh:
                for r in top:
                    fh.write(r["타깃"] + "\n")
            print("  목록 저장: %s" % args.top_list)
        print("  ※ 이 컷오프는 자동 추천값이 아니다. 순위 보존은 측정되지 않았으므로")
        print("     소규모 예비실험(수십 건)으로 직접 정하라. 근거는 진단 리포트 참고.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
