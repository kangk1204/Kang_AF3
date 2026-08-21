#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
af3_rankcorr.py - 두 설정으로 돌린 같은 타깃 집합의 순위가 얼마나 일치하는지 재고,
                  2단계 전략의 컷오프를 정할 근거를 만든다.

왜 필요한가
    2단계 전략 - "전수는 경량 설정으로 스크리닝, 상위 후보만 기본값으로 재실행" - 이
    성립하려면 **경량 설정이 기본값의 상위권을 놓치지 않아야 한다.** 이 저장소는 그것을
    측정하지 않았다. 축소DB vs 전체DB 6건에서 ranking score 변화가 작다는 측정은 있지만
    (무변화 3 / +0.03 2 / -0.01 1), 이것은 6건이고 DB 크기만 바꾼 비교이며
    샘플수/recycle 을 줄인 경량 설정의 순위 보존은 다른 문제다.

    그래서 컷오프 숫자를 주는 대신, 사용자가 **자기 데이터로 직접 재는 절차**를 준다.
    40건 규모 예비실험을 두 설정으로 돌리고 이 스크립트에 결과 두 개를 넘기면,
    순위 상관과 상위 N 겹침률이 나온다. 그 숫자를 보고 컷오프를 정한다.

무엇을 계산하는가
    Spearman 순위상관 rho  - 두 설정의 순위가 전반적으로 얼마나 같은 방향인가
                             (동점은 평균 순위로 처리한다. tie-corrected Pearson-on-ranks)
    Kendall tau-b          - 쌍(pair) 단위 일치율. 이상치에 덜 흔들린다
    top-N 겹침률 (recall)  - **2단계 전략에서 실제로 중요한 숫자.**
                             기준(정밀) 설정의 상위 N건 중 몇 건이 경량 설정의 상위 N 안에
                             들어 있는가. 0.95 면 상위 20건 중 1건을 놓친다는 뜻이다.
    top-N 안전배수          - 기준 상위 N건을 전부 잡으려면 경량 상위 몇 건까지 재실행해야
                             하는가. 예비실험에서 이 값이 1.5 면, 상위 100건을 원하면
                             경량 기준 150건을 재실행해야 한다는 근거가 된다.
    값 차이 분포            - 같은 타깃의 지표 값이 두 설정에서 얼마나 달라지는가

의존성
    표준 라이브러리만 쓴다. python3.8 이상. scipy 를 요구하지 않는다.
    (Spearman/Kendall 구현은 직접 검산했다. docs/two_stage_notes.md 참고)

가장 흔한 사용법
    # 0) 예비실험 40건을 두 설정으로 돌린다 (같은 서열, 설정만 다르게)
    #    경량: --num_diffusion_samples 1 --num_recycles 3
    #    기준: 기본값 (samples 5, recycles 10)

    # 1) 각각 집계
    python3 af3_collect.py 경량=pilot_light_out -o pilot_경량.csv
    python3 af3_collect.py 기준=pilot_full_out  -o pilot_기준.csv

    # 2) 순위 상관을 잰다. --ref 가 '정답' 쪽(기준/정밀), --test 가 스크리닝 쪽(경량)
    python3 af3_rankcorr.py --ref pilot_기준.csv --test pilot_경량.csv \\
        --top-n 5,10,20 -o 순위상관.csv

    # 한 CSV 에 두 조건이 함께 있으면 (af3_collect.py 로 한 번에 집계한 경우)
    python3 af3_collect.py 기준=full_out 경량=light_out -o 둘다.csv
    python3 af3_rankcorr.py --csv 둘다.csv --ref-condition 기준 --test-condition 경량

    # 구현 검산 (알려진 값으로 자기 자신을 확인한다)
    python3 af3_rankcorr.py --selftest
"""

from __future__ import annotations

import argparse
import csv
import math
import statistics
import sys
from pathlib import Path

COL_TARGET = "타깃"
COL_COND = "조건"

METRICS = ("ranking_score", "pLDDT평균", "pTM", "ipTM", "pLDDT_90이상비율")


def csv_safe_cell(value):
    """Prevent spreadsheet programs from evaluating untrusted text as a formula."""
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def csv_safe_row(row):
    return {key: csv_safe_cell(value) for key, value in row.items()}


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def die(msg: str) -> None:
    log("오류: " + msg)
    sys.exit(1)


# ---------------------------------------------------------------------------
# 통계 (표준 라이브러리만)
# ---------------------------------------------------------------------------
def rankdata(values):
    """동점을 평균 순위로 처리한 순위 (1부터). scipy.stats.rankdata(method='average') 와 같다."""
    n = len(values)
    order = sorted(range(n), key=lambda i: values[i])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def pearson(xs, ys):
    n = len(xs)
    if n < 2:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0.0 or syy <= 0.0:
        # 한쪽이 전부 같은 값이면 상관은 정의되지 않는다
        return None
    return sxy / math.sqrt(sxx * syy)


def spearman(xs, ys):
    """동점 보정 Spearman rho = 평균순위에 대한 Pearson 상관."""
    return pearson(rankdata(xs), rankdata(ys))


def kendall_tau_b(xs, ys):
    """Kendall tau-b (동점 보정). O(n^2) - 예비실험 규모(수십~수백 건)에서 충분하다."""
    n = len(xs)
    if n < 2:
        return None
    conc = disc = tx = ty = 0
    for i in range(n - 1):
        for j in range(i + 1, n):
            dx = xs[i] - xs[j]
            dy = ys[i] - ys[j]
            if dx == 0 and dy == 0:
                tx += 1
                ty += 1
            elif dx == 0:
                tx += 1
            elif dy == 0:
                ty += 1
            elif (dx > 0) == (dy > 0):
                conc += 1
            else:
                disc += 1
    n0 = n * (n - 1) / 2.0
    denom = math.sqrt((n0 - tx) * (n0 - ty))
    if denom <= 0.0:
        return None
    return (conc - disc) / denom


def spearman_p_approx(rho, n):
    """대략적인 양측 p값. t 근사 (n>=10 에서 쓸 만하다). 정확한 검정이 아니다."""
    if rho is None or n < 4 or abs(rho) >= 1.0:
        return None
    t = rho * math.sqrt((n - 2) / (1.0 - rho * rho))
    df = n - 2
    # Student t 양측 꼬리 확률 (연분수 없이: 정규 근사 + Cornish-Fisher 보정 대신
    # 불완전베타를 급수로. 예비실험 규모에서 소수 둘째 자리까지 맞으면 충분하다)
    x = df / (df + t * t)
    p = _betainc_half(df / 2.0, 0.5, x)
    return max(0.0, min(1.0, p))


def _betainc_half(a, b, x):
    """정규화 불완전베타 I_x(a,b) - 연분수(Lentz). t분포 꼬리용."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbeta = math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)
    front = math.exp(math.log(x) * a + math.log(1.0 - x) * b - lbeta) / a
    f, c, d = 1.0, 1.0, 0.0
    for i in range(0, 300):
        m = i // 2
        if i == 0:
            num = 1.0
        elif i % 2 == 0:
            num = (m * (b - m) * x) / ((a + 2.0 * m - 1.0) * (a + 2.0 * m))
        else:
            num = -((a + m) * (a + b + m) * x) / ((a + 2.0 * m) * (a + 2.0 * m + 1.0))
        d = 1.0 + num * d
        if abs(d) < 1e-30:
            d = 1e-30
        d = 1.0 / d
        c = 1.0 + num / c
        if abs(c) < 1e-30:
            c = 1e-30
        f *= c * d
        if abs(1.0 - c * d) < 1e-12:
            break
    return front * (f - 1.0)


def topn_overlap(ref_pairs, test_pairs, n):
    """기준 상위 n건 중 경량 상위 n건에 들어 있는 비율 (recall)."""
    ref_top = [t for t, _ in ref_pairs[:n]]
    test_top = {t for t, _ in test_pairs[:n]}
    if not ref_top:
        return None, 0, 0
    hit = sum(1 for t in ref_top if t in test_top)
    return hit / len(ref_top), hit, len(ref_top)


def safety_factor(ref_pairs, test_pairs, n):
    """기준 상위 n건을 전부 잡으려면 경량 상위 몇 건까지 봐야 하는가."""
    ref_top = {t for t, _ in ref_pairs[:n]}
    if not ref_top:
        return None, None
    pos = {t: i + 1 for i, (t, _) in enumerate(test_pairs)}
    depths = [pos[t] for t in ref_top if t in pos]
    if len(depths) != len(ref_top):
        return None, None
    need = max(depths)
    return need, need / float(n)


def quantiles(values, qs=(0.5, 0.9, 1.0)):
    if not values:
        return {}
    s = sorted(values)
    out = {}
    for q in qs:
        if q >= 1.0:
            out[q] = s[-1]
            continue
        pos = q * (len(s) - 1)
        lo = int(math.floor(pos))
        hi = min(lo + 1, len(s) - 1)
        out[q] = s[lo] + (s[hi] - s[lo]) * (pos - lo)
    return out


# ---------------------------------------------------------------------------
# 입력
# ---------------------------------------------------------------------------
def read_collect_csv(path):
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
    except OSError as exc:
        die("CSV 를 읽을 수 없다: %s (%s)" % (path, exc))
    if not rows:
        die("CSV 가 비어 있다: %s" % path)
    if COL_TARGET not in rows[0]:
        die("'%s' 열이 없다: %s (af3_collect.py 가 만든 CSV 인지 확인하라)" % (COL_TARGET, path))
    return rows


def pick_condition(rows, cond, what):
    conds = sorted({r.get(COL_COND, "") for r in rows})
    if cond is None:
        if len(conds) > 1:
            die(
                "%s CSV 에 조건이 %d개 있다 (%s). --%s-condition 으로 하나만 고르라."
                % (what, len(conds), ", ".join(conds), what)
            )
        return rows
    out = [r for r in rows if r.get(COL_COND) == cond]
    if not out:
        die("%s 조건 '%s' 에 해당하는 행이 없다. 있는 조건: %s" % (what, cond, ", ".join(conds)))
    return out


def as_float(v):
    if v is None:
        return None
    s = str(v).strip()
    if s == "":
        return None
    try:
        value = float(s)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def strip_suffix(name, suffix):
    if suffix and name.endswith(suffix):
        return name[: -len(suffix)]
    return name


# ---------------------------------------------------------------------------
# 검산
# ---------------------------------------------------------------------------
def selftest():
    """알려진 값으로 구현을 검산한다. 손으로 계산할 수 있는 예를 쓴다."""
    ok = True

    def check(label, got, want, tol=1e-9):
        nonlocal ok
        good = got is not None and abs(got - want) <= tol
        ok = ok and good
        print("  [%s] %-42s 계산 %s / 기대 %s" % ("OK" if good else "실패", label, got, want))

    print("Spearman rho - 완전 일치 / 완전 역순")
    check("rho(1..5, 1..5)", spearman([1, 2, 3, 4, 5], [1, 2, 3, 4, 5]), 1.0)
    check("rho(1..5, 5..1)", spearman([1, 2, 3, 4, 5], [5, 4, 3, 2, 1]), -1.0)

    # 손 계산: x=[1,2,3,4,5], y=[2,1,4,3,5] -> 순위차 d=[-1,1,-1,1,0], sum d^2=4
    # rho = 1 - 6*4/(5*24) = 1 - 24/120 = 0.8
    print("Spearman rho - 동점 없는 경우는 1 - 6*sum(d^2)/(n(n^2-1)) 와 같아야 한다")
    check("rho(x, y=[2,1,4,3,5]) = 1-6*4/120", spearman([1, 2, 3, 4, 5], [2, 1, 4, 3, 5]), 0.8)

    # 동점 처리: y=[1,1,3] 의 평균순위는 [1.5,1.5,3]
    print("동점 평균순위")
    check("rankdata([1,1,3])[0]", rankdata([1, 1, 3])[0], 1.5)
    check("rankdata([1,1,3])[2]", rankdata([1, 1, 3])[2], 3.0)

    # Kendall tau-b 손 계산: x=[1,2,3,4], y=[1,2,4,3]
    # 쌍 6개: (1,2)C (1,3)C (1,4)C (2,3)C (2,4)C (3,4)D -> C=5,D=1, 동점 없음
    # tau = (5-1)/6 = 0.666...
    print("Kendall tau-b - 쌍 단위 손 계산")
    check("tau([1,2,3,4],[1,2,4,3]) = (5-1)/6", kendall_tau_b([1, 2, 3, 4], [1, 2, 4, 3]), 4.0 / 6.0)
    check("tau 완전일치", kendall_tau_b([1, 2, 3], [1, 2, 3]), 1.0)
    check("tau 완전역순", kendall_tau_b([1, 2, 3], [3, 2, 1]), -1.0)
    # 동점 보정: x=[1,1,2], y=[1,2,3] -> n0=3, tx=1, ty=0, C: (1,3)C (2,3)C =2, D=0
    # tau_b = 2/sqrt((3-1)*(3-0)) = 2/sqrt(6)
    check("tau_b 동점 보정", kendall_tau_b([1, 1, 2], [1, 2, 3]), 2.0 / math.sqrt(6.0))

    # top-N 겹침 손 계산
    print("top-N 겹침률과 안전배수")
    ref = [("a", 9), ("b", 8), ("c", 7), ("d", 6), ("e", 5)]
    test = [("a", 9), ("c", 8), ("e", 7), ("b", 6), ("d", 5)]
    # 기준 상위 3 = a,b,c. 경량 상위 3 = a,c,e -> a,c 겹침 = 2/3
    r, hit, tot = topn_overlap(ref, test, 3)
    check("겹침률 top3 = 2/3", r, 2.0 / 3.0)
    # a,b,c 를 다 잡으려면 경량 순위 4위(b)까지 -> need=4, 배수 4/3
    need, fac = safety_factor(ref, test, 3)
    check("안전배수 top3 = 4/3", fac, 4.0 / 3.0)
    print("  need=%s (기준 상위 3건을 다 잡으려면 경량 상위 4건까지)" % need)

    # 실제 저장소 데이터로 한 번 (축소DB vs 전체DB 6건, results_example/db_confidence_comparison.csv)
    print("실측 데이터 검산 - results_example/db_confidence_comparison.csv 의 6건")
    red = [0.82, 0.85, 0.85, 0.87, 0.88, 0.90]
    full = [0.82, 0.88, 0.88, 0.86, 0.88, 0.90]
    rho = spearman(red, full)
    tau = kendall_tau_b(red, full)
    print("  축소DB vs 전체DB ranking_score: rho=%.4f, tau_b=%.4f (n=6)" % (rho, tau))
    print("  (이 6건은 DB 크기만 바꾼 비교다. 경량 설정의 순위 보존과는 다른 문제다)")

    print()
    print("검산 결과: %s" % ("전건 일치" if ok else "불일치 있음 - 구현을 확인하라"))
    return 0 if ok else 1


# ---------------------------------------------------------------------------
def build_parser():
    p = argparse.ArgumentParser(
        description="두 설정으로 돌린 같은 타깃 집합의 순위 상관과 상위 N 겹침률을 잰다",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    g = p.add_argument_group("입력 (두 CSV 또는 한 CSV 의 두 조건)")
    g.add_argument("--ref", help="기준(정밀) 설정의 결과요약 CSV")
    g.add_argument("--test", help="비교(경량) 설정의 결과요약 CSV")
    g.add_argument("--csv", help="두 조건이 함께 들어 있는 결과요약 CSV")
    g.add_argument("--ref-condition", help="--csv 안에서 기준 쪽 조건 라벨")
    g.add_argument("--test-condition", help="--csv 안에서 비교 쪽 조건 라벨")

    g = p.add_argument_group("설정")
    g.add_argument(
        "--metric",
        default="ranking_score",
        choices=METRICS,
        help="어느 지표의 순위를 비교할지 (기본 ranking_score)",
    )
    g.add_argument(
        "--all-metrics", action="store_true", help="쓸 수 있는 지표를 전부 계산한다"
    )
    g.add_argument(
        "--top-n", default="5,10,20,50", help="겹침률을 잴 N 값들 (쉼표. 기본 5,10,20,50)"
    )
    g.add_argument(
        "--strip-suffix",
        default="",
        help="타깃 이름을 맞추기 전에 떼어낼 접미어 (af3_stage2.py --name-suffix 를 썼다면 그 값)",
    )
    g.add_argument(
        "--allow-intersection",
        action="store_true",
        help="양쪽 타깃 집합이 달라도 공통 교집합만 분석한다 (기본은 불일치 거부)",
    )
    g.add_argument("-o", "--out", default=None, help="결과를 이 CSV 로 저장")
    g.add_argument("--pairs-out", default=None, help="타깃별 순위/값 대응표를 이 CSV 로 저장")
    g.add_argument("--selftest", action="store_true", help="알려진 값으로 구현을 검산하고 종료")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)

    if args.selftest:
        return selftest()

    if args.csv:
        rows = read_collect_csv(Path(args.csv))
        if not args.ref_condition or not args.test_condition:
            conds = sorted({r.get(COL_COND, "") for r in rows})
            die(
                "--csv 를 쓸 때는 --ref-condition 과 --test-condition 이 둘 다 필요하다. "
                "있는 조건: %s" % ", ".join(conds)
            )
        ref_rows = pick_condition(rows, args.ref_condition, "ref")
        test_rows = pick_condition(rows, args.test_condition, "test")
        ref_label, test_label = args.ref_condition, args.test_condition
    elif args.ref and args.test:
        ref_rows = pick_condition(read_collect_csv(Path(args.ref)), args.ref_condition, "ref")
        test_rows = pick_condition(read_collect_csv(Path(args.test)), args.test_condition, "test")
        ref_label = args.ref_condition or Path(args.ref).stem
        test_label = args.test_condition or Path(args.test).stem
    else:
        die("--ref 와 --test 두 CSV 를 주거나, --csv 와 두 조건 라벨을 주라.")

    metrics = list(METRICS) if args.all_metrics else [args.metric]
    try:
        top_ns = [int(x) for x in str(args.top_n).replace(" ", "").split(",") if x]
    except ValueError:
        die("--top-n 을 해석할 수 없다: %s" % args.top_n)
    if not top_ns or any(value <= 0 for value in top_ns):
        die("--top-n 은 1 이상의 정수 목록이어야 한다.")
    top_ns = list(dict.fromkeys(top_ns))

    # ---- 타깃 맞추기 -----------------------------------------------------
    def index(rows_, label):
        out = {}
        locations = {}
        for row_number, r in enumerate(rows_, 2):
            t = strip_suffix(str(r.get(COL_TARGET, "")).strip(), args.strip_suffix)
            if t:
                if t in out:
                    die(
                        "%s CSV 에 정규화 후 중복 타깃 '%s' 가 있다 (행 %d, %d)."
                        % (label, t, locations[t], row_number)
                    )
                out[t] = r
                locations[t] = row_number
        return out

    ref_idx, test_idx = index(ref_rows, "ref"), index(test_rows, "test")
    common = sorted(set(ref_idx) & set(test_idx))
    only_ref = sorted(set(ref_idx) - set(test_idx))
    only_test = sorted(set(test_idx) - set(ref_idx))

    print()
    print("타깃 대응: 공통 %d건 (기준 %d건, 비교 %d건)" % (len(common), len(ref_idx), len(test_idx)))
    if only_ref:
        print("  기준에만 %d건: %s" % (len(only_ref), ", ".join(only_ref[:6])))
    if only_test:
        print("  비교에만 %d건: %s" % (len(only_test), ", ".join(only_test[:6])))
    if (only_ref or only_test) and not args.allow_intersection:
        die(
            "기준과 비교의 타깃 집합이 다르다. 누락을 해결하거나 의도한 교집합 분석이면 "
            "--allow-intersection 을 명시하라."
        )
    if len(common) < 3:
        die(
            "공통 타깃이 %d건뿐이다. 순위 상관을 재려면 최소 3건, 실용적으로는 30건 이상이 필요하다.\n"
            "  타깃 이름이 어긋났다면 --strip-suffix 를 확인하라." % len(common)
        )
    if len(common) < 20:
        log(
            "경고: 공통 %d건은 순위 상관을 판단하기에 적다. 컷오프를 정할 근거로는 "
            "30~50건 규모를 권한다." % len(common)
        )

    results = []
    pairs_dump = []
    for metric in metrics:
        pairs = []
        for t in common:
            a = as_float(ref_idx[t].get(metric))
            b = as_float(test_idx[t].get(metric))
            if a is None or b is None:
                continue
            pairs.append((t, a, b))
        if len(pairs) < 3:
            if not args.all_metrics:
                die("'%s' 값이 양쪽에 다 있는 타깃이 %d건뿐이다." % (metric, len(pairs)))
            log("건너뜀: '%s' 값이 양쪽에 다 있는 타깃이 %d건뿐이다." % (metric, len(pairs)))
            continue

        names = [p[0] for p in pairs]
        xs = [p[1] for p in pairs]  # 기준
        ys = [p[2] for p in pairs]  # 비교
        n = len(pairs)

        rho = spearman(xs, ys)
        tau = kendall_tau_b(xs, ys)
        pval = spearman_p_approx(rho, n)
        r_lin = pearson(xs, ys)

        ref_sorted = sorted(zip(names, xs), key=lambda t: (-t[1], t[0]))
        test_sorted = sorted(zip(names, ys), key=lambda t: (-t[1], t[0]))

        diffs = [y - x for x, y in zip(xs, ys)]
        absd = [abs(d) for d in diffs]
        q = quantiles(absd)

        print()
        print("=" * 68)
        print("지표: %s   (기준 %s <- 비교 %s, 공통 %d건)" % (metric, ref_label, test_label, n))
        print("=" * 68)
        print(
            "  Spearman rho  %s   %s"
            % (
                "%.4f" % rho if rho is not None else "계산불가",
                ("(p ~ %.3g, t근사)" % pval) if pval is not None else "",
            )
        )
        print("  Kendall tau_b %s" % ("%.4f" % tau if tau is not None else "계산불가"))
        print("  Pearson r     %s  (값 자체의 선형 상관. 순위와는 다르다)"
              % ("%.4f" % r_lin if r_lin is not None else "계산불가"))
        print(
            "  값 차이 (비교 - 기준): 중앙값 %+.4f, 절대차 중앙값 %.4f, p90 %.4f, 최대 %.4f"
            % (
                statistics.median(diffs),
                q.get(0.5, float("nan")),
                q.get(0.9, float("nan")),
                q.get(1.0, float("nan")),
            )
        )
        print()
        print("  상위 N 겹침 - 2단계 전략에서 실제로 중요한 숫자")
        print("    %-6s %-14s %-10s %s" % ("N", "겹침률(recall)", "놓친건수", "안전배수(기준N을 다 잡는 경량깊이)"))
        row_tops = {}
        for nn in top_ns:
            if nn > n:
                continue
            tied_boundary = any(
                nn < len(values) and values[nn - 1][1] == values[nn][1]
                for values in (ref_sorted, test_sorted)
            )
            if tied_boundary:
                print(
                    "    %-6d %-14s %-10s %s"
                    % (nn, "판정불가(동점)", "-", "경계 동점으로 순위 집합이 유일하지 않음")
                )
                row_tops[nn] = (None, None, None, None)
                continue
            rec, hit, tot = topn_overlap(ref_sorted, test_sorted, nn)
            need, fac = safety_factor(ref_sorted, test_sorted, nn)
            missed = [t for t, _ in ref_sorted[:nn] if t not in {x for x, _ in test_sorted[:nn]}]
            print(
                "    %-6d %-14s %-10s %s"
                % (
                    nn,
                    "%.3f (%d/%d)" % (rec, hit, tot),
                    "%d" % len(missed),
                    "%.2f배 (상위 %d건)" % (fac, need) if fac else "계산불가",
                )
            )
            if missed:
                print("           놓친 타깃: %s" % ", ".join(missed[:6]))
            row_tops[nn] = (rec, len(missed), need, fac)

        results.append(
            {
                "지표": metric,
                "공통건수": n,
                "기준조건": ref_label,
                "비교조건": test_label,
                "Spearman_rho": None if rho is None else round(rho, 4),
                "Kendall_tau_b": None if tau is None else round(tau, 4),
                "Spearman_p_t근사": None if pval is None else float("%.4g" % pval),
                "Pearson_r": None if r_lin is None else round(r_lin, 4),
                "값차_중앙값": round(statistics.median(diffs), 4),
                "절대값차_중앙값": round(q.get(0.5, 0.0), 4),
                "절대값차_p90": round(q.get(0.9, 0.0), 4),
                "절대값차_최대": round(q.get(1.0, 0.0), 4),
                **{
                    "top%d_겹침률" % nn: (None if v[0] is None else round(v[0], 4))
                    for nn, v in row_tops.items()
                },
                **{"top%d_안전배수" % nn: (None if v[3] is None else round(v[3], 3)) for nn, v in row_tops.items()},
            }
        )

        ref_pos = {t: i + 1 for i, (t, _) in enumerate(ref_sorted)}
        test_pos = {t: i + 1 for i, (t, _) in enumerate(test_sorted)}
        for t, a, b in pairs:
            pairs_dump.append(
                {
                    "지표": metric,
                    "타깃": t,
                    "기준값": a,
                    "비교값": b,
                    "값차": round(b - a, 4),
                    "기준순위": ref_pos[t],
                    "비교순위": test_pos[t],
                    "순위차": test_pos[t] - ref_pos[t],
                }
            )

    if not results:
        die("계산할 수 있는 지표가 없다.")

    print()
    print("-" * 68)
    print("이 숫자로 컷오프를 정하는 방법")
    print("  1. rho 와 tau_b 가 낮으면(대략 0.8 미만) 경량 설정이 순위를 보존하지 못한다는 뜻이다.")
    print("     그러면 2단계 전략 자체를 재검토하라. 경량 설정을 덜 경량하게 바꾸는 편이 낫다.")
    print("  2. rho 가 높아도 top-N 겹침률이 낮으면 상위권에서 순위가 섞이는 것이다.")
    print("     2단계에서 중요한 것은 rho 가 아니라 겹침률이다. 겹침률을 우선 보라.")
    print("  3. '안전배수' 가 재실행 규모를 정한다. 최종적으로 상위 K건을 원한다면")
    print("     경량 기준으로 K x 안전배수 건을 재실행하라. 예비실험의 안전배수는 그 규모에서만")
    print("     측정된 값이므로, 전수 2000건에 그대로 외삽하는 것은 추정이다.")
    print("  4. 이 스크립트는 컷오프를 추천하지 않는다. 위 숫자를 보고 직접 정한다.")
    print("-" * 68)

    if args.out:
        keys = []
        for r in results:
            for k in r:
                if k not in keys:
                    keys.append(k)
        out = Path(args.out)
        if str(out.parent) != "":
            out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
            w.writeheader()
            for r in results:
                w.writerow(csv_safe_row(r))
        print("요약 저장: %s" % out)

    if args.pairs_out and pairs_dump:
        out = Path(args.pairs_out)
        if str(out.parent) != "":
            out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(pairs_dump[0].keys()))
            w.writeheader()
            w.writerows(csv_safe_row(row) for row in pairs_dump)
        print("타깃별 대응표 저장: %s" % out)

    return 0


if __name__ == "__main__":
    sys.exit(main())
