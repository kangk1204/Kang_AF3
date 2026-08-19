#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
af3_visualize.py - AlphaFold 3 출력 폴더를 그림으로 만든다.

무엇을 만드는가 (타깃 하나당)
    <타깃>_plddt.png    잔기 번호별 pLDDT 꺾은선. 신뢰 구간을 배경색으로 깔았다
    <타깃>_pae.png      PAE(예측 정렬 오차) 히트맵. 도메인/사슬이 서로 얼마나 확실히
                        놓였는지 본다
그리고 폴더 전체에 하나씩
    af3_요약.png        타깃별 ranking score / pTM / pLDDT 를 한 화면에 비교
    pymol_색칠.pml      PyMOL 에서 pLDDT 색칠까지 한 번에 하는 스크립트
    chimerax_색칠.cxc   ChimeraX 용 같은 것

무엇을 읽는가 (실물로 확인한 AF3 v3.0 출력 구조. 검증 호스트 gpu-5070ti)
    <출력폴더>/<타깃>/
      <타깃>_confidences.json
          atom_chain_ids   원자별 사슬 id (문자열 리스트, 길이 = 원자 수)
          atom_plddts      원자별 pLDDT 0~100 (실수 리스트, 길이 = 원자 수)
          contact_probs    토큰 x 토큰 접촉 확률 행렬
          pae              토큰 x 토큰 PAE 행렬 (옹스트롬). 이 스크립트가 쓰는 것
          token_chain_ids  토큰별 사슬 id
          token_res_ids    토큰별 잔기 번호 (1부터)
      <타깃>_summary_confidences.json
          ranking_score    AF3 가 모델을 줄 세우는 값. 클수록 좋다
          ptm              전체 구조의 TM 예측값 0~1
          iptm             사슬 간 계면 신뢰도 0~1. 단량체면 null 이다 (실측 확인)
          chain_ptm        사슬별 pTM (사슬 수만큼)
          chain_iptm       사슬별 ipTM (단량체면 [null])
          chain_pair_iptm  사슬쌍 ipTM 행렬
          chain_pair_pae_min 사슬쌍 최소 PAE 행렬
          fraction_disordered  무질서 비율 0~1
          has_clash        원자 충돌 여부 0/1
          chain_ids        토큰별 사슬 id (사슬 목록이 아니다. 길이 = 토큰 수. 실측 확인)
      <타깃>_ranking_scores.csv   seed,sample,ranking_score 세 열
      <타깃>_model.cif            1위 모델 좌표. B_iso_or_equiv 열에 원자별 pLDDT 가
                                  그대로 들어 있다 (원본 JSON 과 바이트 일치 확인)
      seed-<S>_sample-<N>/        샘플별 같은 3종 파일

의존성
    matplotlib 하나. 나머지는 표준 라이브러리다.
    pandas / numpy / biopython 을 쓰지 않는다 (검증 호스트 python3 에 없어서).
    matplotlib 이 없으면 --no-plot 으로 스크립트/표만 만들 수 있다.

사용법
    # 폴더 하나를 전부 그린다
    python3 af3_visualize.py vhh_out -o 그림

    # 타깃 몇 개만
    python3 af3_visualize.py vhh_out -o 그림 --only 01_vhh_001,03_vhh_096

    # 요약 비교 그림만 (건수가 많을 때. 개별 그림은 만들지 않는다)
    python3 af3_visualize.py vhh_out -o 그림 --summary-only

    # 구조 보기 스크립트만
    python3 af3_visualize.py vhh_out -o 그림 --no-plot

한글 폰트
    한글 글리프가 있는 폰트를 찾아서 쓴다. 없으면 라벨을 영문으로 자동 대체한다
    (두부 현상 = 글자가 네모로 나오는 것을 막기 위해서다). 어느 쪽을 썼는지 실행할 때 알려준다.
    폰트를 강제하려면 --font 'Noto Sans CJK KR', 무조건 영문으로 하려면 --lang en.
"""

import argparse
import csv
import json
import math
import os
import statistics
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# pLDDT 신뢰 구간. AF3/AF2 논문과 EBI AlphaFold DB 가 쓰는 구간과 색을 따랐다.
# (매우 높음 90+, 높음 70-90, 낮음 50-70, 매우 낮음 <50)
# ---------------------------------------------------------------------------
PLDDT_BANDS = [
    (90, 100, "#0053D6", "매우 높음 (90 이상)", "Very high (>=90)"),
    (70,  90, "#65CBF3", "높음 (70-90)",        "Confident (70-90)"),
    (50,  70, "#FFDB13", "낮음 (50-70)",        "Low (50-70)"),
    (0,   50, "#FF7D45", "매우 낮음 (50 미만)",  "Very low (<50)"),
]

# 한글 글리프를 가진 폰트 후보. 앞에서부터 찾는다.
FONT_CANDIDATES = [
    "Noto Sans CJK KR", "Noto Sans KR", "NanumGothic", "NanumBarunGothic",
    "Malgun Gothic", "AppleGothic", "Apple SD Gothic Neo",
    "Noto Sans Mono CJK KR", "Source Han Sans KR", "UnDotum", "Baekmuk Gulim",
]

# 라벨 사전. (한국어, 영문). 폰트가 없으면 영문 쪽을 쓴다.
L = {
    "resid":     ("잔기 번호", "Residue number"),
    "plddt":     ("pLDDT (0-100, 클수록 확실)", "pLDDT (0-100, higher = better)"),
    "plddt_ttl": ("잔기별 예측 신뢰도: 파란 구간이면 믿을 만하다",
                  "Per-residue confidence: blue band = trustworthy"),
    "pae_x":     ("정렬 기준 잔기", "Aligned residue"),
    "pae_y":     ("오차를 본 잔기", "Scored residue"),
    "pae_cbar":  ("PAE (옹스트롬, 작을수록 확실)", "PAE (Angstrom, lower = better)"),
    "pae_ttl":   ("두 잔기의 상대 위치가 얼마나 확실한가: 어두우면 확실",
                  "How certain the relative placement is: darker = more certain"),
    "mean":      ("평균", "mean"),
    "chain":     ("사슬", "chain"),
    "rank":      ("ranking score (클수록 좋다)", "ranking score (higher = better)"),
    "ptm":       ("pTM (0-1)", "pTM (0-1)"),
    "iptm":      ("ipTM (계면, 0-1)", "ipTM (interface, 0-1)"),
    "meanpl":    ("평균 pLDDT", "mean pLDDT"),
    "target":    ("타깃", "Target"),
    "sum_l":     ("어느 후보가 앞서는가: 오른쪽에 있을수록 좋다",
                  "Which candidates lead: further right = better"),
    "sum_r":     ("두 지표가 같은 방향을 보는가: 오른쪽 위가 좋은 후보다",
                  "Do the two metrics agree: upper right = better"),
    "spread":    ("샘플 5개 산포", "spread over 5 samples"),
    "nsample":   ("샘플", "samples"),
    "cut70":     ("pLDDT 70 (믿을 만한 경계)", "pLDDT 70 (confidence floor)"),
    "cut90":     ("pLDDT 90 (매우 확실)", "pLDDT 90 (very high)"),
    "cut08":     ("ranking 0.8 (좋은 후보 기준선)", "ranking 0.8 (good-candidate line)"),
    "higher":    ("위로 갈수록 좋다", "higher = better"),
    "band":      ("신뢰 구간", "Confidence band"),
    "monomer":   ("단량체이므로 ipTM 이 없다 (AF3 가 null 로 준다)",
                  "monomer: ipTM is null (AF3 gives null)"),
}

_LANG = 0  # 0 = 한국어, 1 = 영문


def t(key):
    """라벨을 현재 언어로 준다."""
    return L[key][_LANG]


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def die(msg):
    log("")
    log("오류: " + msg)
    sys.exit(1)


# ---------------------------------------------------------------------------
# 폰트
# ---------------------------------------------------------------------------

def setup_font(force_font, lang_opt):
    """한글 글리프가 실제로 있는 폰트를 찾는다. 없으면 영문 라벨로 내려간다.

    이름만 보고 고르면 안 된다. 이름이 맞아도 글리프가 없는 경우가 있어서
    FT2Font 로 '한' 의 글리프 인덱스를 직접 확인한다 (0 이면 없다).
    """
    global _LANG
    if lang_opt == "en":
        _LANG = 1
        log("라벨: 영문 (--lang en)")
        return None

    try:
        import matplotlib
        from matplotlib import font_manager
        from matplotlib.ft2font import FT2Font
    except ImportError:
        _LANG = 1
        return None

    def has_hangul(path):
        try:
            f = FT2Font(path)
            return all(f.get_char_index(ord(ch)) != 0 for ch in "한글값")
        except Exception:
            return False

    candidates = list(FONT_CANDIDATES)
    if force_font:
        candidates.insert(0, force_font)

    for name in candidates:
        try:
            path = font_manager.findfont(
                font_manager.FontProperties(family=name),
                fallback_to_default=False)
        except Exception:
            continue
        if path and has_hangul(path):
            matplotlib.rcParams["font.family"] = [name]
            matplotlib.rcParams["axes.unicode_minus"] = False
            _LANG = 0
            log("라벨: 한국어 (폰트 '%s')" % name)
            return name

    # 이름 목록에 없는 폰트라도 시스템에 한글 폰트가 있으면 쓴다
    try:
        for fp in font_manager.fontManager.ttflist:
            if has_hangul(fp.fname):
                matplotlib.rcParams["font.family"] = [fp.name]
                matplotlib.rcParams["axes.unicode_minus"] = False
                _LANG = 0
                log("라벨: 한국어 (폰트 '%s' 를 자동으로 찾았다)" % fp.name)
                return fp.name
    except Exception:
        pass

    _LANG = 1
    log("라벨: 영문. 한글 글리프가 있는 폰트를 찾지 못했다.")
    log("      한국어 라벨을 원하면 폰트를 설치하고 다시 실행해라:")
    log("        Ubuntu/Debian : sudo apt install fonts-noto-cjk")
    log("        conda         : conda install -c conda-forge font-ttf-noto-cjk")
    log("      (그림이 네모(두부)로 나오는 것을 막기 위해 영문으로 내려갔다.)")
    return None


# ---------------------------------------------------------------------------
# 읽기
# ---------------------------------------------------------------------------

def is_sidecar(name):
    """macOS AppleDouble 사이드카(._*) 와 숨은 파일. 읽으면 UTF-8 오류로 죽는다."""
    return name.startswith("._") or name.startswith(".")


def load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError, UnicodeDecodeError) as e:
        log("  읽기 실패 %s: %s" % (os.path.basename(path), e))
        return None


def find_targets(root, only):
    """출력 폴더 아래에서 <타깃>_summary_confidences.json 을 가진 폴더를 찾는다."""
    root = Path(root)
    if not root.is_dir():
        die("'%s' 는 폴더가 아니다. AF3 --output_dir 로 준 폴더를 지정해라." % root)
    picks = set(x.strip() for x in only.split(",")) if only else None
    found = []
    sidecars = 0
    for child in sorted(root.iterdir()):
        if is_sidecar(child.name):
            sidecars += 1
            continue
        if not child.is_dir():
            continue
        name = child.name
        if picks and name not in picks:
            continue
        summ = child / ("%s_summary_confidences.json" % name)
        if not summ.exists():
            cand = [f for f in child.glob("*_summary_confidences.json")
                    if not is_sidecar(f.name)]
            if not cand:
                continue
            summ = cand[0]
        stem = summ.name[:-len("_summary_confidences.json")]
        found.append((name, child, stem))
    if sidecars:
        log("주의: '%s' 에 AppleDouble 사이드카가 %d개 있다. 건너뛰었지만,"
            % (root, sidecars))
        log("      AF3 자체는 이것 때문에 죽는다. 지워라:  find %s -name '._*' -delete" % root)
    if not found:
        die("'%s' 아래에서 AF3 출력 타깃을 찾지 못했다.\n"
            "      기대한 구조: %s/<타깃이름>/<타깃이름>_summary_confidences.json\n"
            "      실제 내용: %s"
            % (root, root, ", ".join(p.name for p in sorted(Path(root).iterdir())[:8])))
    return found


def parse_mmcif_atoms(path):
    """mmCIF 의 _atom_site 루프를 읽어 [(사슬, 잔기번호, 잔기명, 원자명, B값)] 로 준다.

    표준 라이브러리만 쓴다. AF3 출력은 한 모델뿐이고 따옴표 필드가 없는 단순한
    루프여서 split() 으로 충분하다. 값 개수가 헤더 개수와 다른 줄은 건너뛴다.
    """
    cols = []
    rows = []
    in_loop = False
    try:
        fh = open(path, "r", encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None, None
    with fh:
        for line in fh:
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
        return None, None
    idx = {c: i for i, c in enumerate(cols)}
    need = ["auth_asym_id", "auth_seq_id", "label_comp_id", "label_atom_id",
            "B_iso_or_equiv"]
    for n in need:
        if n not in idx:
            return None, cols
    out = []
    for p in rows:
        try:
            out.append((p[idx["auth_asym_id"]], int(p[idx["auth_seq_id"]]),
                        p[idx["label_comp_id"]], p[idx["label_atom_id"]],
                        float(p[idx["B_iso_or_equiv"]])))
        except ValueError:
            continue
    return out, cols


def residue_plddt(conf, cif_atoms):
    """잔기별 평균 pLDDT 를 [(사슬, 잔기번호, 평균pLDDT, 원자수)] 로 준다.

    값의 출처는 항상 confidences.json 의 atom_plddts 다 (원본).
    mmCIF 는 '원자 -> (사슬, 잔기번호)' 매핑에만 쓴다 (그 매핑이 JSON 에는 없다).
    mmCIF 의 B값은 같은 값을 소수 2자리로 다시 쓴 것이라 반올림 차이가 날 수 있으므로
    그림 값으로는 쓰지 않는다.

    mmCIF 를 못 읽거나 원자 수가 맞지 않으면 토큰 단위로 되돌아간다
    (표준 아미노산은 토큰 1개 = 잔기 1개다).
    """
    ap = conf.get("atom_plddts") or []
    if cif_atoms and len(cif_atoms) == len(ap):
        acc = {}
        order = []
        for (ch, resi, _resn, _atom, _b), v in zip(cif_atoms, ap):
            key = (ch, resi)
            if key not in acc:
                acc[key] = []
                order.append(key)
            acc[key].append(v)
        return [(k[0], k[1], sum(acc[k]) / len(acc[k]), len(acc[k])) for k in order]
    # 되돌림 경로: 사슬/잔기 정보를 토큰에서 가져온다. 원자 pLDDT 를 잔기로 묶을 수
    # 없으므로 토큰 수와 원자 수가 같은 경우(모두 단일원자 토큰)에만 쓴다.
    tc = conf.get("token_chain_ids") or []
    tr = conf.get("token_res_ids") or []
    if tc and len(tc) == len(ap):
        return [(tc[i], tr[i], ap[i], 1) for i in range(len(tc))]
    return []


def verify_bfactor(conf, cif_atoms):
    """mmCIF 의 B_iso_or_equiv 가 pLDDT 인지 확인한다.

    이 확인은 그림 값이 아니라 PyMOL/ChimeraX 색칠 명령('b < 70' 같은 것)이
    옳다는 근거를 위한 것이다. mmCIF 는 소수 2자리로 쓰이므로 0.01 정도의
    반올림 차이는 정상이다.

    반환: (판정, 설명문). 판정은 True(같다) / False(다르다) / None(확인 못함).
    """
    ap = conf.get("atom_plddts") or []
    if not cif_atoms:
        return None, "mmCIF 를 읽지 못해 확인하지 못했다"
    if len(cif_atoms) != len(ap):
        return False, ("원자 수가 다르다 (mmCIF %d, confidences.json %d). "
                       "같은 실행의 파일인지 확인해라"
                       % (len(cif_atoms), len(ap)))
    worst = 0.0
    n_diff = 0
    for (_c, _r, _n, _a, b), v in zip(cif_atoms, ap):
        d = abs(b - v)
        if d > 0.005:
            n_diff += 1
        worst = max(worst, d)
    # mmCIF 는 소수 2자리 -> 반올림 차이는 최대 0.01 까지 정상으로 본다
    if worst <= 0.0105:
        note = ("원자 %d개 확인, 최대 차 %.4f (mmCIF 소수 2자리 반올림 범위). "
                "B_iso_or_equiv = pLDDT 로 확정" % (len(ap), worst))
        if n_diff:
            note += " [반올림 차이 %d개]" % n_diff
        return True, note
    return False, ("최대 차 %.3f, 다른 원자 %d개. 반올림으로 설명되지 않는다"
                   % (worst, n_diff))


def read_ranking_csv(path):
    """seed,sample,ranking_score 를 [(seed, sample, score)] 로."""
    if not os.path.exists(path):
        return []
    out = []
    try:
        with open(path, "r", encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                try:
                    out.append((int(row["seed"]), int(row["sample"]),
                                float(row["ranking_score"])))
                except (KeyError, ValueError, TypeError):
                    continue
    except (OSError, UnicodeDecodeError):
        return []
    return out


# ---------------------------------------------------------------------------
# 그림
# ---------------------------------------------------------------------------

def style():
    """글꼴 크기 사다리와 축 모양. 세 단계만 쓴다 (제목/축=9, 범례=8, 눈금=7)."""
    import matplotlib
    matplotlib.rcParams.update({
        "figure.dpi": 150, "savefig.dpi": 300,
        "savefig.bbox": "tight", "savefig.pad_inches": 0.05,
        "axes.titlesize": 9, "axes.labelsize": 9,
        "legend.fontsize": 8, "xtick.labelsize": 7, "ytick.labelsize": 7,
        "axes.titleweight": "normal", "axes.titlelocation": "left",
        "axes.spines.top": False, "axes.spines.right": False,
        "axes.linewidth": 0.7, "xtick.major.width": 0.7,
        "ytick.major.width": 0.7, "legend.frameon": False,
        "axes.grid": False,
    })


def plot_plddt(res, name, summ, outpath, cif_ok):
    """잔기별 pLDDT 꺾은선 + 신뢰 구간 배경."""
    import matplotlib.pyplot as plt

    chains = []
    for ch, _r, _v, _n in res:
        if ch not in chains:
            chains.append(ch)

    fig, ax = plt.subplots(figsize=(7.2, 2.6))

    # 신뢰 구간 배경. 데이터 뒤에 깔린다.
    for lo, hi, color, ko, en in PLDDT_BANDS:
        ax.axhspan(lo, hi, color=color, alpha=0.16, lw=0, zorder=0)

    x = list(range(1, len(res) + 1))
    y = [r[2] for r in res]

    # 사슬이 여러 개면 사슬 경계에 세로선을 긋고 사슬 이름을 축 안쪽에 붙인다
    # (축 위에 붙이면 제목과 겹친다)
    if len(chains) > 1:
        start = 0
        for ch in chains:
            n = sum(1 for r in res if r[0] == ch)
            if start > 0:
                ax.axvline(start + 0.5, color="0.35", lw=0.8, ls="--", zorder=3)
            ax.text(start + n / 2.0, 4, "%s %s" % (t("chain"), ch),
                    ha="center", va="bottom", fontsize=8, color="0.15",
                    zorder=7,
                    bbox=dict(boxstyle="round,pad=0.15", fc="white",
                              ec="none", alpha=0.75))
            start += n
        # x 눈금은 사슬을 이어붙인 통짜 인덱스다 (사슬별 잔기 번호가 아니다)
        ax.set_xlabel("%s (%s %s %s)"
                      % (t("resid"), t("chain"), "+".join(chains),
                         "이어붙임" if _LANG == 0 else "concatenated"))
    else:
        ax.set_xlabel(t("resid"))

    ax.plot(x, y, color="#1a1a1a", lw=1.1, zorder=4, solid_joinstyle="round")

    mean = statistics.fmean(y)
    ax.axhline(mean, color="#C2185B", lw=0.9, ls=":", zorder=5)
    # 평균 라벨은 축 바깥 오른쪽에 둔다. 안쪽에 두면 데이터 선과 겹친다.
    ax.annotate("%s %.1f" % (t("mean"), mean),
                xy=(1.0, mean), xycoords=("axes fraction", "data"),
                xytext=(3, 0), textcoords="offset points",
                ha="left", va="center", fontsize=8, color="#C2185B",
                annotation_clip=False)

    ax.set_ylabel(t("plddt"))
    ax.set_ylim(0, 100)
    ax.set_xlim(0.5, len(res) + 0.5)
    ax.set_yticks([0, 50, 70, 90, 100])

    rs = summ.get("ranking_score")
    ptm = summ.get("ptm")
    iptm = summ.get("iptm")
    bits = ["%s" % name]
    if rs is not None:
        bits.append("ranking %.2f" % rs)
    if ptm is not None:
        bits.append("pTM %.2f" % ptm)
    bits.append("ipTM %.2f" % iptm if iptm is not None else "ipTM -")
    ax.set_title("%s\n%s" % (t("plddt_ttl"), "   ".join(bits)))

    # 구간 범례. 값 자체가 색을 설명하므로 색 견본만 준다.
    handles = [plt.Rectangle((0, 0), 1, 1, color=c, alpha=0.5, lw=0)
               for _lo, _hi, c, _k, _e in PLDDT_BANDS]
    labels = [(ko if _LANG == 0 else en) for _lo, _hi, _c, ko, en in PLDDT_BANDS]
    ax.legend(handles, labels, title=t("band"), title_fontsize=8,
              loc="upper left", bbox_to_anchor=(1.10, 1.02),
              handlelength=1.0, handleheight=0.9, borderaxespad=0)

    if cif_ok is False:
        ax.text(0.01, 0.02,
                "mmCIF B값이 pLDDT 와 달랐다: 뷰어 색칠은 확인 필요"
                if _LANG == 0 else
                "mmCIF B-factor != pLDDT: check viewer colouring",
                transform=ax.transAxes, fontsize=7, color="#B71C1C")

    fig.savefig(outpath)
    plt.close(fig)
    return mean


def plot_pae(conf, name, outpath):
    """PAE 히트맵. 사슬 경계에 선을 긋는다."""
    import matplotlib.pyplot as plt

    pae = conf.get("pae")
    if not pae:
        return None
    n = len(pae)
    tc = conf.get("token_chain_ids") or ["A"] * n
    tr = conf.get("token_res_ids") or list(range(1, n + 1))

    fig, ax = plt.subplots(figsize=(3.9, 3.4))
    im = ax.imshow(pae, cmap="Greens_r", vmin=0, vmax=31.75,
                   origin="upper", interpolation="nearest",
                   extent=(0.5, n + 0.5, n + 0.5, 0.5))

    chains = []
    for ch in tc:
        if ch not in chains:
            chains.append(ch)
    if len(chains) > 1:
        start = 0
        for ch in chains:
            k = sum(1 for c in tc if c == ch)
            if start > 0:
                ax.axvline(start + 0.5, color="#B71C1C", lw=0.8)
                ax.axhline(start + 0.5, color="#B71C1C", lw=0.8)
            # 사슬 이름은 축 안쪽(위 여백)에 둔다. 축 위에 두면 제목과 겹친다.
            ax.text(start + k / 2.0, 0.02 * n, ch, ha="center", va="top",
                    fontsize=8, color="#B71C1C", zorder=6,
                    bbox=dict(boxstyle="round,pad=0.12", fc="white",
                              ec="none", alpha=0.8))
            start += k

    ax.set_xlabel(t("pae_x"))
    ax.set_ylabel(t("pae_y"))
    ax.set_title("%s\n%s" % (t("pae_ttl"), name))
    ax.set_xlim(0.5, n + 0.5)
    ax.set_ylim(n + 0.5, 0.5)
    cb = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.03)
    cb.set_label(t("pae_cbar"), fontsize=8)
    cb.ax.tick_params(labelsize=7)
    cb.outline.set_linewidth(0.5)

    fig.savefig(outpath)
    plt.close(fig)
    return max(max(r) for r in pae)


def plot_summary(rows, outpath):
    """타깃 여러 개를 한 화면에 비교. 왼쪽: ranking score, 오른쪽: pTM 대 평균 pLDDT."""
    import matplotlib.pyplot as plt

    rows = sorted(rows, key=lambda r: (r["ranking_score"] is None,
                                       -(r["ranking_score"] or 0)))
    n = len(rows)
    # 범례를 그림 아래에 두므로 그만큼 높이를 더 준다
    h = max(3.2, 0.28 * n + 2.3)
    fig, (ax1, ax2) = plt.subplots(
        1, 2, figsize=(8.6, h), gridspec_kw={"width_ratios": [1.15, 1.0]})

    # --- 왼쪽: ranking score. 단일 관측이므로 롤리팝 + 샘플 산포 ---
    ys = list(range(n, 0, -1))
    for r, y in zip(rows, ys):
        rs = r["ranking_score"]
        if rs is None:
            ax1.text(0.5, y, "n.d.", va="center", ha="center", fontsize=7, color="0.4")
            continue
        ax1.plot([0, rs], [y, y], color="0.75", lw=0.8, zorder=1)
        sc = r["sample_scores"]
        if len(sc) > 1:
            ax1.plot(sc, [y] * len(sc), "o", ms=2.6, mfc="none",
                     mec="#7B9BD1", mew=0.7, zorder=2)
        ax1.plot([rs], [y], "o", ms=5, color="#0053D6", zorder=3)
    ax1.axvline(0.8, color="#C2185B", lw=0.9, ls="--", zorder=0)
    ax1.text(0.8, n + 0.75, " " + t("cut08"), fontsize=7.5,
             color="#C2185B", ha="left", va="bottom")
    ax1.set_yticks(ys)
    ax1.set_yticklabels([r["name"] for r in rows])
    ax1.set_xlabel(t("rank"))
    ax1.set_xlim(0, 1.0)
    ax1.set_ylim(0.3, n + 1.6)
    ax1.set_title(t("sum_l"))
    score_legend = None
    if any(len(r["sample_scores"]) > 1 for r in rows):
        h1 = plt.Line2D([], [], marker="o", ls="", ms=5, color="#0053D6")
        h2 = plt.Line2D([], [], marker="o", ls="", ms=2.6, mfc="none",
                        mec="#7B9BD1", mew=0.7)
        score_legend = ([h1, h2],
                        ["AF3 1위 모델", "같은 서열의 다른 샘플"] if _LANG == 0
                        else ["AF3 top model", "other samples, same input"])

    # --- 오른쪽: pTM 대 평균 pLDDT 산점 ---
    xs = [r["ptm"] for r in rows if r["ptm"] is not None and r["mean_plddt"] is not None]
    ys2 = [r["mean_plddt"] for r in rows if r["ptm"] is not None and r["mean_plddt"] is not None]
    nm = [r["name"] for r in rows if r["ptm"] is not None and r["mean_plddt"] is not None]
    if xs:
        for lo, hi, color, _ko, _en in PLDDT_BANDS:
            ax2.axhspan(lo, hi, color=color, alpha=0.16, lw=0, zorder=0)
        ax2.plot(xs, ys2, "o", ms=5, color="#0053D6", zorder=3)
        # 최고/최저만 직접 라벨. 점이 하나뿐이면 한 번만 붙인다.
        order = sorted(range(len(xs)), key=lambda i: ys2[i])
        picks = [(order[-1], 13)] if len(order) == 1 else [(order[-1], 13),
                                                           (order[0], -13)]
        # 점이 축 위쪽/아래쪽 끝에 붙어 있으면 라벨을 반대 방향으로 뺀다 (제목/축과 겹침 방지)
        picks = [(i, -abs(dy) if ys2[i] > 85 else
                     (abs(dy) if ys2[i] < 15 else dy)) for i, dy in picks]
        for i, dy in picks:
            ax2.annotate(nm[i], (xs[i], ys2[i]), textcoords="offset points",
                         xytext=(-10, dy), fontsize=7.5,
                         va="center", ha="right", zorder=6,
                         arrowprops=dict(arrowstyle="-", lw=0.5, color="0.35",
                                         shrinkA=1, shrinkB=4))
        ax2.set_xlabel(t("ptm"))
        ax2.set_ylabel(t("meanpl"))
        ax2.set_ylim(0, 100)
        # 90 과 100 이 붙어 보이지 않게 100 은 눈금에서 뺀다 (배경 구간이 상한을 보여준다)
        ax2.set_yticks([0, 50, 70, 90])
        ax2.set_xlim(0, 1.0)
        ax2.set_title(t("sum_r"))
        band_legend = (
            [plt.Rectangle((0, 0), 1, 1, color=cc, alpha=0.5, lw=0)
             for _lo, _hi, cc, _k, _e in PLDDT_BANDS],
            [(ko if _LANG == 0 else en) for _lo, _hi, _c, ko, en in PLDDT_BANDS])
    else:
        ax2.axis("off")
        band_legend = None

    # 축 라벨과 겹치지 않게 두 범례를 그림 하단 여백에 배치한다.
    # 먼저 tight_layout 으로 축을 확정하고, 남은 아래 공간에 figure 범례를 얹는다.
    fig.tight_layout(w_pad=3.2, rect=(0, 0.155, 1, 1))
    if score_legend:
        fig.legend(*score_legend, loc="lower left", bbox_to_anchor=(0.02, 0.005),
                   ncol=1, fontsize=7.5, frameon=False)
    if band_legend:
        fig.legend(*band_legend, title=t("band"), title_fontsize=8,
                   loc="lower right", bbox_to_anchor=(0.99, 0.005), ncol=2,
                   fontsize=7.5, handlelength=1.0, handleheight=0.9,
                   frameon=False)
    fig.savefig(outpath)
    plt.close(fig)


# ---------------------------------------------------------------------------
# 구조 보기 스크립트
# ---------------------------------------------------------------------------

PYMOL_TEMPLATE = """# PyMOL 에서 AF3 결과를 pLDDT 색으로 보는 스크립트
# 만든 것: af3_visualize.py
#
# 쓰는 법
#   pymol {script_name}
# 또는 PyMOL 을 먼저 띄운 뒤
#   @{script_name}
#
# 왜 이 색이 맞는가
#   AF3 가 쓴 mmCIF 의 B_iso_or_equiv 열에 원자별 pLDDT(0~100)가 그대로 들어 있다.
#   {verify_note}
#   그래서 B값을 그대로 색 기준으로 쓰면 EBI AlphaFold DB 와 같은 색이 된다.
#
# 색 기준 (AlphaFold DB 와 같다)
#   90 이상  파랑    매우 높음. 골격도 측쇄도 믿을 만하다
#   70~90    하늘    높음. 골격은 믿을 만하다
#   50~70    노랑    낮음. 조심해서 봐라
#   50 미만  주황    매우 낮음. 무질서 영역일 가능성이 크다

reinitialize
set assembly, ""
set cartoon_transparency, 0
bg_color white

{load_lines}

hide everything
show cartoon

# pLDDT 색칠
set_color af3_vhigh, [0.051, 0.341, 0.827]
set_color af3_high,  [0.396, 0.796, 0.953]
set_color af3_low,   [1.000, 0.859, 0.075]
set_color af3_vlow,  [1.000, 0.490, 0.271]

# 주의: PyMOL 선택 문법에는 '>=' 가 없다. 'b >= 50' 은
#   Error: b > = 50<--
# 으로 죽는다 (실측). 그래서 낮은 색부터 칠하고 위에 덧칠하는 방식을 쓴다.
# 아래 4줄은 순서가 중요하다. 바꾸지 마라.
color af3_vlow,  all
color af3_low,   b > 50
color af3_high,  b > 70
color af3_vhigh, b > 90

# 낮은 신뢰 구간만 따로 보고 싶을 때 (주석을 풀어라)
# select low_conf, b < 70
# show sticks, low_conf

set ray_opaque_background, 0
orient
zoom all, 2

# 스펙트럼으로 보고 싶으면 위의 color 4줄을 지우고 이 줄을 써라
# spectrum b, orange_yellow_cyan_blue, minimum=0, maximum=100

print "AF3 pLDDT 색칠 완료. 파랑=확실, 주황=불확실."
"""

CHIMERAX_TEMPLATE = """# ChimeraX 에서 AF3 결과를 pLDDT 색으로 보는 스크립트
# 만든 것: af3_visualize.py
#
# 쓰는 법
#   chimerax {script_name}
# 또는 ChimeraX 를 먼저 띄운 뒤
#   open {script_name}
#
# 왜 이 색이 맞는가
#   AF3 mmCIF 의 B_iso_or_equiv 열이 원자별 pLDDT(0~100)다.
#   {verify_note}
#   ChimeraX 는 이 열을 bfactor 속성으로 읽으므로 아래 명령이 그대로 맞는다.
#   (참고: ChimeraX 의 'color bfactor palette alphafold' 는 0~1 스케일을 가정하는
#    버전이 있어, 여기서는 0~100 범위를 명시해 색 경계를 직접 준다.)

close session
set bgColor white

{load_lines}

hide atoms
show cartoon

# pLDDT 색칠 (AlphaFold DB 와 같은 색 경계)
color bfactor palette 0,#FF7D45:50,#FF7D45:50.001,#FFDB13:70,#FFDB13:70.001,#65CBF3:90,#65CBF3:90.001,#0053D6:100,#0053D6

# 낮은 신뢰 구간만 보고 싶을 때 (주석을 풀어라)
# select @@bfactor<70
# show sel atoms
# style sel stick

view
"""


def write_viewer_scripts(outdir, targets, verify_note, relative_to):
    """PyMOL / ChimeraX 스크립트를 만든다. 경로는 outdir 기준 상대경로."""
    pml_lines = []
    cxc_lines = []
    for name, cifpath in targets:
        try:
            rel = os.path.relpath(cifpath, start=outdir)
        except ValueError:
            rel = cifpath
        pml_lines.append('load %s, %s' % (rel, name))
        cxc_lines.append('open %s name %s' % (rel, name))

    p1 = os.path.join(outdir, "pymol_색칠.pml")
    with open(p1, "w", encoding="utf-8") as fh:
        fh.write(PYMOL_TEMPLATE.format(script_name="pymol_색칠.pml",
                                       load_lines="\n".join(pml_lines),
                                       verify_note=verify_note))
    p2 = os.path.join(outdir, "chimerax_색칠.cxc")
    with open(p2, "w", encoding="utf-8") as fh:
        fh.write(CHIMERAX_TEMPLATE.format(script_name="chimerax_색칠.cxc",
                                          load_lines="\n".join(cxc_lines),
                                          verify_note=verify_note))
    return p1, p2


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(
        prog="af3_visualize.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="AlphaFold 3 출력 폴더를 pLDDT/PAE 그림과 구조 보기 스크립트로 만든다.",
        epilog="""예시
  python3 af3_visualize.py vhh_out -o 그림
  python3 af3_visualize.py vhh_out -o 그림 --only 01_vhh_001,03_vhh_096
  python3 af3_visualize.py vhh_out -o 그림 --summary-only
  python3 af3_visualize.py vhh_out -o 그림 --no-plot        # 스크립트/표만
  python3 af3_visualize.py vhh_out -o 그림 --lang en        # 라벨을 영문으로
""")
    p.add_argument("outdir_af3", metavar="AF3출력폴더",
                   help="AF3 --output_dir 로 준 폴더")
    p.add_argument("-o", "--out", required=True, help="그림을 저장할 폴더")
    p.add_argument("--only", help="이 타깃만 (콤마로 나열)")
    p.add_argument("--max", type=int, default=200,
                   help="개별 그림을 만들 최대 타깃 수. 기본 200 "
                        "(2000건을 다 그리면 시간과 디스크가 낭비된다)")
    p.add_argument("--summary-only", action="store_true",
                   help="개별 pLDDT/PAE 그림 없이 요약 비교 그림만 만든다")
    p.add_argument("--no-pae", action="store_true", help="PAE 히트맵을 만들지 않는다")
    p.add_argument("--no-plot", action="store_true",
                   help="그림을 그리지 않고 구조 보기 스크립트와 표만 만든다 "
                        "(matplotlib 이 없는 환경)")
    p.add_argument("--font", help="한글 폰트 이름을 강제로 지정")
    p.add_argument("--lang", choices=["ko", "en"], default="ko",
                   help="라벨 언어. 기본 ko (폰트가 없으면 자동으로 en)")
    p.add_argument("--format", default="png", choices=["png", "pdf", "svg"],
                   help="그림 형식. 기본 png")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)

    have_mpl = True
    if not args.no_plot:
        try:
            import matplotlib
            matplotlib.use("Agg")
        except ImportError:
            have_mpl = False
            log("주의: matplotlib 이 없어 그림을 그릴 수 없다. 스크립트와 표만 만든다.")
            log("      설치:  python3 -m pip install matplotlib")
    else:
        have_mpl = False

    if have_mpl:
        setup_font(args.font, args.lang)
        style()
    elif args.lang == "en":
        global _LANG
        _LANG = 1

    targets = find_targets(args.outdir_af3, args.only)
    log("타깃 %d개를 찾았다." % len(targets))

    os.makedirs(args.out, exist_ok=True)

    rows = []
    cif_targets = []
    verify_notes = []
    made = []

    todo = targets if args.summary_only else targets[:args.max]
    if not args.summary_only and len(targets) > args.max:
        log("주의: 타깃이 %d개다. 개별 그림은 앞 %d개만 만든다 (--max 로 조절)."
            % (len(targets), args.max))

    for i, (name, d, stem) in enumerate(targets, 1):
        summ = load_json(d / ("%s_summary_confidences.json" % stem))
        if summ is None:
            continue
        conf = load_json(d / ("%s_confidences.json" % stem))
        cifp = d / ("%s_model.cif" % stem)
        cif_atoms, cols = (None, None)
        if cifp.exists():
            cif_atoms, cols = parse_mmcif_atoms(str(cifp))
            cif_targets.append((name, str(cifp.resolve())))

        ok, note = (None, "")
        res = []
        if conf is not None:
            ok, note = verify_bfactor(conf, cif_atoms)
            if ok is not None and len(verify_notes) < 3:
                verify_notes.append("%s: %s" % (name, note))
            if ok is False:
                log("  주의: %s 의 mmCIF B값이 pLDDT 와 다르다 (%s). "
                    "그림은 원본 JSON 값으로 그리지만, 뷰어 색칠 명령은 못 믿는다."
                    % (name, note))
            # 값은 항상 원본 JSON 을 쓴다. mmCIF 는 잔기 매핑에만 쓴다.
            res = residue_plddt(conf, cif_atoms)

        sc = [s for _sd, _sm, s in read_ranking_csv(
            str(d / ("%s_ranking_scores.csv" % stem)))]
        mean_pl = statistics.fmean([r[2] for r in res]) if res else None

        rows.append({
            "name": name,
            "ranking_score": summ.get("ranking_score"),
            "ptm": summ.get("ptm"),
            "iptm": summ.get("iptm"),
            "fraction_disordered": summ.get("fraction_disordered"),
            "has_clash": summ.get("has_clash"),
            "n_chain": len(summ.get("chain_ptm") or []),
            "n_token": len(conf.get("pae")) if conf and conf.get("pae") else None,
            "n_residue": len(res) if res else None,
            "mean_plddt": mean_pl,
            "min_plddt": min((r[2] for r in res), default=None),
            "sample_scores": sc,
            "sample_sd": (statistics.stdev(sc) if len(sc) > 1 else None),
        })

        if have_mpl and not args.summary_only and (name, d, stem) in todo and res:
            p = os.path.join(args.out, "%s_plddt.%s" % (name, args.format))
            plot_plddt(res, name, summ, p, ok)
            made.append(p)
            if conf and not args.no_pae:
                p2 = os.path.join(args.out, "%s_pae.%s" % (name, args.format))
                if plot_pae(conf, name, p2):
                    made.append(p2)
            log("  [%d/%d] %s" % (i, len(targets), name))

    if not rows:
        die("읽을 수 있는 타깃이 없었다.")

    verify_note = verify_notes[0] if verify_notes else "확인하지 못했다"
    p1, p2 = write_viewer_scripts(args.out, cif_targets, verify_note, args.outdir_af3)

    if have_mpl:
        ps = os.path.join(args.out, "af3_요약.%s" % args.format)
        plot_summary(rows, ps)
        made.append(ps)

    # 표도 같이 남긴다 (그림에서 읽은 값을 숫자로 확인할 수 있게)
    tbl = os.path.join(args.out, "af3_시각화표.csv")
    fields = ["name", "ranking_score", "ptm", "iptm", "mean_plddt", "min_plddt",
              "n_residue", "n_token", "n_chain", "fraction_disordered",
              "has_clash", "sample_sd"]
    with open(tbl, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else
                            (round(r[k], 4) if isinstance(r[k], float) else r[k]))
                        for k in fields})

    # ---- 요약 출력 ----
    print("=" * 70)
    print("AF3 시각화 결과")
    print("=" * 70)
    print("읽은 폴더    : %s" % args.outdir_af3)
    print("타깃 수      : %d" % len(rows))
    print("저장 폴더    : %s" % args.out)
    print("만든 그림    : %d 개" % len(made))
    print("구조 스크립트: %s , %s" % (os.path.basename(p1), os.path.basename(p2)))
    print("표           : %s" % os.path.basename(tbl))
    print("")
    print("B값 = pLDDT 확인")
    for n in verify_notes:
        print("  " + n)
    if not verify_notes:
        print("  확인하지 못했다 (mmCIF 또는 confidences.json 을 읽지 못함)")
    print("")
    print("%-26s %8s %6s %6s %8s %8s" % ("타깃", "ranking", "pTM", "ipTM",
                                          "평균pLDDT", "잔기"))
    for r in sorted(rows, key=lambda x: -(x["ranking_score"] or 0))[:15]:
        print("%-26s %8s %6s %6s %8s %8s" % (
            r["name"][:26],
            "%.3f" % r["ranking_score"] if r["ranking_score"] is not None else "-",
            "%.2f" % r["ptm"] if r["ptm"] is not None else "-",
            "%.2f" % r["iptm"] if r["iptm"] is not None else "-",
            "%.1f" % r["mean_plddt"] if r["mean_plddt"] is not None else "-",
            r["n_residue"] if r["n_residue"] is not None else "-"))
    if len(rows) > 15:
        print("  ... (%d개 더. 전체는 %s 를 봐라)" % (len(rows) - 15, os.path.basename(tbl)))
    if all(r["iptm"] is None for r in rows):
        print("")
        print("ipTM 이 전부 비어 있다. %s" % t("monomer"))
        print("사슬이 2개 이상일 때만 계산된다. 항원-나노바디 복합체를 돌리면 채워진다.")
    print("")
    print("그림에서 무엇을 볼 것인가")
    print("  pLDDT 그림 : 파란 배경(90 이상) 안에 선이 있으면 그 잔기는 믿을 만하다.")
    print("               CDR 루프가 노랑/주황으로 내려앉는 것은 나노바디에서 흔하다.")
    print("               다만 프레임워크(파랑)가 아니라 CDR 이 무너지면 그 후보는 의심해라.")
    print("  PAE 그림   : 대각선 근처만 어두우면 각 도메인은 확실하지만 서로의 위치는 불확실하다.")
    print("               복합체에서 사슬 경계(붉은 선) 바깥의 사각형이 어두워야 결합을 믿는다.")
    print("  요약 그림  : 오른쪽 위에 모인 것이 좋은 후보다. 열린 원의 퍼짐이 크면 재현성이 낮다.")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
