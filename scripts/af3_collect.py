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
    python3.8 이상.

사용법
    # 기본: 출력 폴더 하나를 훑어 CSV 로
    python3 af3_collect.py vhh_001_out -o af3_결과요약.csv

    # 여러 폴더를 한 CSV 로 (조건 비교). --label 로 조건 이름을 붙인다
    python3 af3_collect.py 축소=af3out_reduced 전체=af3out_full -o 비교.csv

    # MSA 깊이는 *_data.json 을 읽어야 하므로 느리다. 필요 없으면 끈다
    python3 af3_collect.py vhh_001_out --no-msa-depth -o 요약.csv

    # 상위 후보만 골라내기 (2단계 전략의 재실행 목록 만들기)
    python3 af3_collect.py vhh_001_out --top 100 --top-list top100.txt

주의
    * 이 스크립트는 읽기만 한다. 출력 폴더의 어떤 파일도 수정/삭제하지 않는다.
    * macOS 에서 만든 tar 를 리눅스에서 풀면 '._' 로 시작하는 AppleDouble 사이드카가
      생긴다. UTF-8 이 아니어서 읽으면 죽는다. 이 스크립트는 전부 건너뛴다.
"""

import argparse
import csv
import json
import math
import os
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# AF3 기본 패딩 버킷 사다리.
# run_alphafold.py 의 _BUCKETS 기본값과 동일하며 128 에서 시작한다 (실측 확인).
# VHH 130 aa 는 128 버킷에 들어가고, 256 버킷으로 밀리면 추론이 2.25배 느려진다.
# ---------------------------------------------------------------------------
DEFAULT_BUCKETS = [128, 256, 384, 512, 768, 1024, 1280, 1536, 2048, 2560,
                   3072, 3584, 4096, 4608, 5120]


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def is_sidecar(name):
    """macOS AppleDouble 사이드카(._*) 와 숨은 파일을 걸러낸다."""
    return name.startswith("._") or name.startswith(".")


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
                    rows.append(float(rec["ranking_score"]))
                except (KeyError, TypeError, ValueError):
                    continue
    except OSError:
        return None
    return rows or None


def collect_target(tdir, want_msa=True):
    """타깃 폴더 하나에서 지표를 뽑는다. 완료되지 않은 폴더는 None."""
    name = tdir.name
    summ_p = tdir / ("%s_summary_confidences.json" % name)
    conf_p = tdir / ("%s_confidences.json" % name)
    rank_p = tdir / ("%s_ranking_scores.csv" % name)
    data_p = tdir / ("%s_data.json" % name)

    if not summ_p.exists():
        # AF3 가 출력 폴더 이름을 소문자화하므로 접두어가 다를 수 있다. 한 번 더 찾는다.
        cands = [p for p in tdir.glob("*_summary_confidences.json")
                 if not is_sidecar(p.name)]
        if not cands:
            return None
        summ_p = cands[0]
        stem = summ_p.name[:-len("_summary_confidences.json")]
        conf_p = tdir / ("%s_confidences.json" % stem)
        rank_p = tdir / ("%s_ranking_scores.csv" % stem)
        data_p = tdir / ("%s_data.json" % stem)

    summ = load_json(summ_p)
    if summ is None:
        return None

    row = {
        "타깃": name,
        "ranking_score": summ.get("ranking_score"),
        "pTM": summ.get("ptm"),
        "ipTM": summ.get("iptm"),
        "fraction_disordered": summ.get("fraction_disordered"),
        "has_clash": summ.get("has_clash"),
    }

    # 체인별 지표 (복합체에서 어느 체인이 문제인지 보려면 필요)
    cp = summ.get("chain_ptm")
    ci = summ.get("chain_iptm")
    row["chain_pTM"] = ";".join("" if v is None else str(v) for v in cp) if isinstance(cp, list) else ""
    row["chain_ipTM"] = ";".join("" if v is None else str(v) for v in ci) if isinstance(ci, list) else ""
    cpi = summ.get("chain_pair_iptm")
    if isinstance(cpi, list) and len(cpi) > 1:
        off = [cpi[i][j] for i in range(len(cpi)) for j in range(len(cpi))
               if i != j and isinstance(cpi[i][j], (int, float))]
        row["min_chain_pair_ipTM"] = min(off) if off else None
    else:
        row["min_chain_pair_ipTM"] = None

    # ---- 원자 pLDDT 분포와 토큰 수 -------------------------------------
    plddts = []
    if conf_p.exists():
        conf = load_json(conf_p)
        if conf:
            plddts = [float(v) for v in (conf.get("atom_plddts") or [])
                      if isinstance(v, (int, float))]
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
  pTM (전체 폴드가 맞을 확률의 대리 지표, 0~1)
      0.5 초과  전체 폴드가 대체로 맞다고 볼 수 있는 하한선
  ipTM (계면 정확도, 복합체에서만 산출)
      0.8 이상  계면 신뢰
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
      버킷256   패딩 버킷이 256 이상 (추론 시간 2.25배)

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
COLUMNS = ["조건", "타깃", "등급", "경고",
           "ranking_score", "pTM", "ipTM", "pLDDT평균", "pLDDT중앙값",
           "pLDDT최소", "pLDDT_p10", "pLDDT_70이상비율", "pLDDT_90이상비율",
           "fraction_disordered", "has_clash",
           "MSA_unpaired깊이", "MSA_paired깊이",
           "토큰수", "원자수", "체인수", "체인ID", "패딩버킷",
           "샘플수", "ranking최고", "ranking최저", "ranking산포",
           "chain_pTM", "chain_ipTM", "min_chain_pair_ipTM",
           "ranking검산차", "출력경로"]


def walk_output_dir(root, label, want_msa=True):
    root = Path(root).expanduser()
    rows, incomplete = [], []
    if not root.is_dir():
        log("오류: 출력 폴더가 없다: %s" % root)
        return rows, incomplete
    for tdir in sorted(p for p in root.iterdir()
                       if p.is_dir() and not is_sidecar(p.name)):
        row = collect_target(tdir, want_msa=want_msa)
        if row is None:
            incomplete.append(tdir.name)
            continue
        row["조건"] = label
        row["출력경로"] = str(tdir)
        rows.append(grade_row(row))
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
    ap.add_argument("-o", "--out", default="af3_결과요약.csv", help="CSV 저장 경로")
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

    all_rows, all_incomplete = [], []
    for spec in args.outputs:
        label, path = parse_spec(spec)
        rows, inc = walk_output_dir(path, label, want_msa=not args.no_msa_depth)
        log("%-14s %s : 완료 %d건, 미완성/건너뜀 %d건" % (label, path, len(rows), len(inc)))
        all_rows += rows
        all_incomplete += [(label, n) for n in inc]

    if not all_rows:
        log("오류: 집계할 완료 결과가 없다. 출력 폴더 경로를 확인하라.")
        return 1

    out = Path(args.out)
    if out.parent != Path(""):
        out.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig: 엑셀에서 한글 열 이름이 깨지지 않게
    with open(out, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        for r in all_rows:
            w.writerow(r)

    # ---- 화면 요약 -------------------------------------------------------
    from collections import Counter
    print()
    print("집계 완료: %d건 -> %s" % (len(all_rows), out))
    gc = Counter(r["등급"] for r in all_rows)
    for g in sorted(gc):
        print("  %-12s %4d건 (%.1f%%)" % (g, gc[g], 100.0 * gc[g] / len(all_rows)))
    wc = Counter(w for r in all_rows for w in (r["경고"].split(";") if r["경고"] else []))
    if wc:
        print("  경고: " + ", ".join("%s %d건" % (k, v) for k, v in wc.most_common()))
    if all_incomplete:
        print("  미완성/건너뜀 %d건 (재시도 대상): %s"
              % (len(all_incomplete),
                 ", ".join(n for _, n in all_incomplete[:10])))
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
        top = havekey[:args.top]
        print()
        print("상위 %d건 (%s 기준). 이 목록이 2단계 전략의 재실행 후보다." % (len(top), key))
        for r in top[:20]:
            print("  %-10s %-28s %s=%-8s pLDDT=%-8s 등급=%s"
                  % (r["조건"], r["타깃"], key, r[key], r["pLDDT평균"], r["등급"]))
        if len(top) > 20:
            print("  ... (%d건 더)" % (len(top) - 20))
        if top:
            print("  컷오프 값: %s = %s" % (key, top[-1][key]))
        if args.top_list:
            with open(args.top_list, "w", encoding="utf-8") as fh:
                for r in top:
                    fh.write(r["타깃"] + "\n")
            print("  목록 저장: %s" % args.top_list)
        print("  ※ 이 컷오프는 자동 추천값이 아니다. 순위 보존은 측정되지 않았으므로")
        print("     소규모 예비실험(수십 건)으로 직접 정하라. 근거는 진단 리포트 참고.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
