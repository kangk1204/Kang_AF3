#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
af3_stage2.py - 1단계(경량) 스크리닝 결과에서 상위 후보를 골라 2단계(정밀) 재실행 입력을 만든다.

무엇을 하는가
    손에 있는 것:  af3_collect.py 가 만든 결과요약 CSV (또는 --top-list 로 뽑은 이름 목록)
    필요한 것:     그 상위 후보만 담은 재실행용 입력 JSON 폴더
    이 스크립트가 그 연결을 한다. 지금까지는 사용자가 CSV 를 눈으로 보고 손으로
    골라야 했고, 2000건 규모에서는 그게 불가능했다.

핵심 - 왜 *_data.json 을 재사용하는가
    AF3 는 데이터 파이프라인 단계에서 타깃마다 `<이름>_data.json` 을 쓴다. 이 파일에는
    MSA(unpairedMsa / pairedMsa)와 템플릿 mmCIF 가 문자열로 **직접 들어 있다**.
    (실물 확인: 검증 호스트의 vhh_4qgy_1_data.json 839,761 B, unpairedMsa 1,597자 +
     pairedMsa 40,442자 + templates 인라인 mmCIF. 외부 파일을 가리키는 *Path 키는 없다.)
    그래서 이 파일을 그대로 입력으로 주고 `--norun_data_pipeline` 으로 돌리면
    MSA 검색을 건너뛴다.

    절약폭은 1단계에 쓴 DB 구성이 정한다. 전체 DB 급(4종 각 4GB 슬라이스) 구성이라면
    2000건 총 40.2시간 중 MSA 가 37.2시간(93%)이므로 재사용의 값이 크다. 축소 DB
    약 2GB 구성이라면 데이터 파이프라인이 건당 1.98초라서 시간 절약은 작고, 재사용의
    진짜 이득은 1단계와 2단계가 완전히 같은 MSA 를 쓴다는 것이다.
    이 경로가 실제로 동작한다는 것과 측정된 절약폭은 docs/two_stage_notes.md 3절에
    적었다. 그 문서의 숫자만 측정값이다. 이 docstring 에는 숫자를 적지 않는다.

경고 - 컷오프는 이 스크립트가 정해주지 않는다
    경량 설정과 기본값 설정 사이의 **순위 보존은 측정되지 않았다.** 즉 "경량으로 상위
    100건을 고르면 기본값 기준 상위 100건이 그 안에 들어 있다"는 보장이 없다.
    그래서 이 스크립트에는 컷오프 기본값이 없다. --top 이나 --min 을 반드시 직접 준다.
    자기 데이터로 컷오프를 정하는 절차는 af3_rankcorr.py 와
    docs/two_stage_notes.md 를 보라.

의존성
    표준 라이브러리만 쓴다. python3.8 이상.

가장 흔한 사용법
    # 0) 1단계 결과를 집계한다 (af3_collect.py)
    python3 af3_collect.py 경량=vhh_out -o 1단계요약.csv

    # 1) 상위 100건을 _data.json 재사용으로 (MSA 건너뜀. 가장 빠르다)
    python3 af3_stage2.py -c 1단계요약.csv --top 100 -o vhh_2단계_in

    # 2) 점수 컷오프로 (몇 건이 될지는 데이터가 정한다)
    python3 af3_stage2.py -c 1단계요약.csv --min 0.85 -o vhh_2단계_in

    # 3) 이름 목록으로 (af3_collect.py --top-list 의 출력)
    python3 af3_stage2.py --list top100.txt --from-out vhh_out -o vhh_2단계_in

    # 4) MSA 를 다시 하려면 (축소DB 로 1단계, 전체DB 로 2단계를 돌릴 때)
    python3 af3_stage2.py -c 1단계요약.csv --top 100 -o vhh_2단계_in --source input \\
        --input-dir vhh_in

    # 만들기 전에 무엇이 만들어지는지만 보기
    python3 af3_stage2.py -c 1단계요약.csv --top 100 -o vhh_2단계_in --dry-run

만든 다음 - 그대로 기존 러너에 넘긴다
    python3 run_af3_batch_improved.py --mode inference --input-dir vhh_2단계_in \\
        --output-dir vhh_2단계_out
    (--mode inference 가 AF3 에 --norun_data_pipeline 을 붙인다. _data.json 을 쓸 때
     반드시 이 모드로 돌려야 MSA 를 건너뛴다.)
    --source input 으로 만든 입력은 MSA 가 없으므로 --mode full 로 돌린다.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import string
import sys
from pathlib import Path

# af3_collect.py 가 쓰는 열 이름
COL_TARGET = "타깃"
COL_COND = "조건"
COL_PATH = "출력경로"
COL_GRADE = "등급"

# --by 로 쓸 수 있는 정렬 열. af3_collect.py --top-by 와 같은 집합에
# pLDDT_90이상비율 을 더했다 (단량체 스크리닝에서 실제로 잘 갈린다).
SORT_COLUMNS = (
    "ranking_score",
    "pLDDT평균",
    "pTM",
    "ipTM",
    "pLDDT_90이상비율",
)

# AF3 folding_input 이 최상위에서 허용하는 키. 이 밖의 키는 AF3 가 거부한다.
TOP_LEVEL_KEYS = frozenset(
    {
        "dialect",
        "version",
        "name",
        "modelSeeds",
        "sequences",
        "bondedAtomPairs",
        "userCCD",
        "userCCDPath",
    }
)

# MSA/템플릿을 담는 키. --strip-msa 로 지울 대상.
MSA_KEYS = ("unpairedMsa", "pairedMsa", "unpairedMsaPath", "pairedMsaPath", "templates")

# AF3 가 외부 파일로 읽는 키. _data.json 에 이게 있으면 복사만으로는 안 된다.
SIDECAR_KEYS = frozenset({"mmcifPath", "unpairedMsaPath", "pairedMsaPath", "userCCDPath"})


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def die(msg: str) -> None:
    log("오류: " + msg)
    sys.exit(1)


def sanitised_name(raw: str) -> str:
    """AF3 folding_input.Input.sanitised_name 과 같은 규칙.

    출력 폴더 이름은 파일명이 아니라 JSON 의 name 을 이 규칙으로 정규화한 값이다.
    (공백 -> 밑줄, [A-Za-z0-9_-.] 만 남긴다)
    """
    allowed = set(string.ascii_letters + string.digits + "_-.")
    return "".join(ch for ch in raw.replace(" ", "_") if ch in allowed)


def nonempty(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def as_float(value):
    if value is None:
        return None
    text = str(value).strip()
    if text == "":
        return None
    try:
        return float(text)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# 1단계 결과 읽기
# ---------------------------------------------------------------------------
def read_collect_csv(path: Path) -> list[dict]:
    """af3_collect.py 가 만든 CSV 를 읽는다. utf-8-sig (엑셀 BOM) 를 처리한다."""
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
    except OSError as exc:
        die("결과요약 CSV 를 읽을 수 없다: %s (%s)" % (path, exc))
    if not rows:
        die("결과요약 CSV 가 비어 있다: %s" % path)
    if COL_TARGET not in rows[0]:
        die(
            "'%s' 열이 없다: %s\n"
            "  af3_collect.py 가 만든 CSV 가 맞는지 확인하라. 있는 열: %s"
            % (COL_TARGET, path, ", ".join(list(rows[0].keys())[:12]))
        )
    return rows


def read_name_list(path: Path) -> list[str]:
    """--list 로 준 이름 목록 (한 줄에 하나). af3_collect.py --top-list 의 출력 형식."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        die("이름 목록을 읽을 수 없다: %s (%s)" % (path, exc))
    names, seen = [], set()
    for line in text.splitlines():
        name = line.strip()
        if not name or name.startswith("#"):
            continue
        if name in seen:
            continue
        seen.add(name)
        names.append(name)
    if not names:
        die("이름 목록이 비어 있다: %s" % path)
    return names


# ---------------------------------------------------------------------------
# 후보 선별
# ---------------------------------------------------------------------------
def select_rows(rows, *, by, top, minimum, grades, condition, quiet=False, take_all=False):
    """정렬 열 기준으로 상위 N 또는 컷오프 이상을 고른다.

    take_all=True 면 선별하지 않고 전건을 돌려준다. 이때는 정렬 열 값이 없는 행도
    버리지 않는다 (선별에 쓰지 않으므로 값이 없어도 무해하다).
    """
    conds = sorted({r.get(COL_COND, "") for r in rows})
    if condition:
        rows = [r for r in rows if r.get(COL_COND) == condition]
        if not rows:
            die(
                "--condition '%s' 에 해당하는 행이 없다. 있는 조건: %s"
                % (condition, ", ".join(conds))
            )
    elif len(conds) > 1:
        die(
            "조건이 %d개(%s) 섞여 있다. 같은 타깃이 중복 선정되면 2단계 입력이 겹친다.\n"
            "  --condition <라벨> 로 한 조건만 지정하라." % (len(conds), ", ".join(conds))
        )

    if grades:
        want = {g.strip() for g in grades.split(",") if g.strip()}
        before = len(rows)
        rows = [r for r in rows if r.get(COL_GRADE, "") in want]
        if not quiet:
            log("등급 필터 %s: %d건 -> %d건" % (sorted(want), before, len(rows)))
        if not rows:
            die("--grade 로 남은 행이 없다. af3_collect.py --grade-doc 으로 등급 이름을 확인하라.")

    if take_all:
        # 값이 없는 행도 남긴다. 정렬만 하고 자르지 않는다.
        keyed = [(as_float(r.get(by)), r) for r in rows]
        keyed.sort(key=lambda t: (t[0] is None, -(t[0] or 0.0), str(t[1].get(COL_TARGET, ""))))
        return [r for _, r in keyed], [v for v, _ in keyed], keyed

    scored, missing = [], []
    for r in rows:
        v = as_float(r.get(by))
        if v is None:
            missing.append(r.get(COL_TARGET, "?"))
        else:
            scored.append((v, r))
    if missing and not quiet:
        log(
            "경고: '%s' 값이 없어 제외한 %d건: %s"
            % (by, len(missing), ", ".join(missing[:5]) + (" ..." if len(missing) > 5 else ""))
        )
    if not scored:
        die("'%s' 열에 값이 있는 행이 없다. --by 를 다른 열로 바꿔라." % by)

    # 내림차순. 동점은 타깃 이름으로 안정 정렬해서 실행마다 같은 결과가 나오게 한다.
    scored.sort(key=lambda t: (-t[0], str(t[1].get(COL_TARGET, ""))))

    if minimum is not None:
        picked = [(v, r) for v, r in scored if v >= minimum]
        if not picked:
            die(
                "--min %.4g 이상인 건이 없다. 이 데이터의 최고값은 %s = %.4g 이다."
                % (minimum, by, scored[0][0])
            )
        if top is not None:
            picked = picked[:top]
    else:
        picked = scored[:top]

    return [r for _, r in picked], [v for v, _ in picked], scored


# ---------------------------------------------------------------------------
# 원본 입력 찾기
# ---------------------------------------------------------------------------
def find_data_json(target: str, out_root: Path | None, hint: str | None):
    """<출력경로>/<이름>_data.json 을 찾는다.

    AF3 출력 폴더 이름은 JSON 의 name 을 정규화한 값이고, 파일 접두어도 그 값이다.
    타깃 폴더 이름과 파일 접두어가 어긋난 경우(대소문자 등)를 위해 glob 으로도 찾는다.
    """
    candidates = []
    if hint:
        candidates.append(Path(hint))
    if out_root is not None:
        candidates.append(out_root / target)
        candidates.append(out_root / sanitised_name(target))
    for tdir in candidates:
        if not tdir.is_dir():
            continue
        exact = tdir / ("%s_data.json" % tdir.name)
        if nonempty(exact):
            return exact
        found = sorted(
            p for p in tdir.glob("*_data.json") if nonempty(p) and not p.name.startswith("._")
        )
        if found:
            return found[0]
    return None


def find_input_json(target: str, input_dir: Path):
    """원본 입력 JSON 을 찾는다. 파일명 규칙이 자유로우므로 name 값으로도 확인한다."""
    for cand in (input_dir / ("%s.json" % target), input_dir / ("%s.json" % sanitised_name(target))):
        if nonempty(cand):
            return cand
    # 파일명이 다를 수 있다 (af3_prepare.py 의 --no-index 여부 등). name 값으로 찾는다.
    want = sanitised_name(target)
    for path in sorted(input_dir.glob("*.json")):
        if path.name.startswith("._"):
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                obj = json.load(fh)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(obj, dict) and sanitised_name(str(obj.get("name", ""))) == want:
            return path
    return None


# ---------------------------------------------------------------------------
# 재실행 입력 만들기
# ---------------------------------------------------------------------------
def has_sidecar(obj) -> list[str]:
    """AF3 가 외부 파일로 읽는 *Path 키를 재귀적으로 찾는다.

    상대경로 sidecar 는 JSON 파일 위치 기준으로 해석되므로, JSON 만 다른 폴더로
    복사하면 깨진다. 있으면 알려주고 건너뛴다.
    """
    found, stack = [], [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            for k, v in cur.items():
                if k in SIDECAR_KEYS and v:
                    found.append("%s=%s" % (k, v))
                else:
                    stack.append(v)
        elif isinstance(cur, list):
            stack.extend(cur)
    return found


def msa_size(obj) -> int:
    """JSON 안에 실제로 들어 있는 MSA/템플릿 문자 수. 0 이면 MSA 가 없는 것이다."""
    total, stack = 0, [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            for k, v in cur.items():
                if k in ("unpairedMsa", "pairedMsa") and isinstance(v, str):
                    total += len(v)
                elif k == "templates" and isinstance(v, list):
                    for t in v:
                        if isinstance(t, dict) and isinstance(t.get("mmcif"), str):
                            total += len(t["mmcif"])
                else:
                    stack.append(v)
        elif isinstance(cur, list):
            stack.extend(cur)
    return total


def strip_msa(obj) -> int:
    """MSA/템플릿 키를 지워 데이터 파이프라인이 다시 돌게 한다. 지운 키 수를 돌려준다.

    주의: 키를 빈 문자열로 두면 AF3 는 '검색하지 말고 빈 MSA 로 진행' 으로 해석한다.
    다시 검색시키려면 키가 아예 없어야 한다.
    """
    removed, stack = 0, [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            for k in MSA_KEYS:
                if k in cur:
                    del cur[k]
                    removed += 1
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)
    return removed


def parse_seeds(spec: str) -> list[int]:
    """'1,2,3' 또는 '1-5' 를 정수 목록으로."""
    seeds = []
    for part in str(spec).replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part[1:]:
            a, b = part.split("-", 1)
            try:
                lo, hi = int(a), int(b)
            except ValueError:
                die("--seeds 를 해석할 수 없다: %s" % spec)
            if hi < lo:
                die("--seeds 범위가 뒤집혔다: %s" % part)
            seeds += list(range(lo, hi + 1))
        else:
            try:
                seeds.append(int(part))
            except ValueError:
                die("--seeds 를 해석할 수 없다: %s" % spec)
    if not seeds:
        die("--seeds 가 비어 있다. AF3 는 modelSeeds 가 비면 입력을 거부한다.")
    # 중복 제거, 순서 유지
    out, seen = [], set()
    for s in seeds:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def build_one(src_json: Path, *, seeds, name_suffix, do_strip, json_version):
    """원본 JSON 을 읽어 2단계용으로 고친 dict 와 진단 정보를 돌려준다."""
    try:
        with open(src_json, encoding="utf-8") as fh:
            obj = json.load(fh)
    except UnicodeDecodeError:
        return None, "UTF-8 이 아니다 (macOS AppleDouble 껍데기일 수 있다)"
    except json.JSONDecodeError as exc:
        return None, "JSON 형식 오류 (%d행 %d열: %s)" % (exc.lineno, exc.colno, exc.msg)
    except OSError as exc:
        return None, "읽을 수 없다 (%s)" % exc
    if not isinstance(obj, dict):
        return None, "최상위가 객체(dict)가 아니다"
    if not obj.get("sequences"):
        return None, "sequences 가 비어 있다 (AF3 가 거부한다)"

    sidecars = has_sidecar(obj)
    if sidecars and not do_strip:
        return None, (
            "외부 파일을 가리키는 키가 있다 (%s). 상대경로는 JSON 위치 기준으로 "
            "해석되므로 복사만으로는 깨진다. --strip-msa 를 쓰거나 원본 폴더에서 실행하라."
            % ", ".join(sidecars[:3])
        )

    removed = strip_msa(obj) if do_strip else 0
    msa_chars = msa_size(obj)

    obj["modelSeeds"] = list(seeds)
    obj["dialect"] = "alphafold3"
    if json_version is not None:
        obj["version"] = json_version
    if name_suffix:
        obj["name"] = "%s%s" % (obj.get("name", src_json.stem), name_suffix)

    # AF3 가 모르는 최상위 키는 거부되므로 떨어낸다. (다른 도구가 붙인 메모 등)
    dropped = sorted(set(obj) - TOP_LEVEL_KEYS)
    for k in dropped:
        del obj[k]
    # AF3 가 스스로 써넣은 null 값 키는 그대로 둬도 되지만, 없는 것과 같으므로 지운다.
    for k in ("bondedAtomPairs", "userCCD", "userCCDPath"):
        if k in obj and obj[k] is None:
            del obj[k]

    return {
        "obj": obj,
        "name": obj["name"],
        "output_name": sanitised_name(str(obj["name"])),
        "msa_chars": msa_chars,
        "removed": removed,
        "dropped": dropped,
    }, None


# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="1단계 스크리닝 상위 후보를 2단계 재실행 입력 JSON 으로 만든다",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    g = p.add_argument_group("후보 목록 (하나는 반드시 지정)")
    g.add_argument("-c", "--csv", help="af3_collect.py 가 만든 결과요약 CSV")
    g.add_argument("--list", dest="name_list", help="타깃 이름 목록 파일 (--top-list 의 출력)")

    g = p.add_argument_group("선별 기준 (--csv 일 때. 기본값은 없다 - 직접 정해야 한다)")
    g.add_argument("--top", type=int, default=None, help="상위 N건")
    g.add_argument(
        "--min", dest="minimum", type=float, default=None, help="정렬 열 값이 이 값 이상인 건 전부"
    )
    g.add_argument(
        "--all",
        dest="take_all",
        action="store_true",
        help="선별하지 않고 전건. 2단계 전략의 1단계 전수를 _data.json 재사용으로 "
        "돌릴 때 쓴다 (선별은 그 다음 단계에서 한다)",
    )
    g.add_argument(
        "--by", default="ranking_score", choices=SORT_COLUMNS, help="정렬 열 (기본 ranking_score)"
    )
    g.add_argument("--grade", default=None, help="이 등급만 (쉼표로 여러 개. 예: A_높음,B_신뢰)")
    g.add_argument("--condition", default=None, help="이 조건(라벨) 행만 (여러 조건을 함께 집계했을 때)")

    g = p.add_argument_group("원본 입력 찾기")
    g.add_argument(
        "--source",
        choices=("auto", "data", "input"),
        default="auto",
        help="data=_data.json 재사용(MSA 건너뜀), input=원본 JSON(MSA 다시 함), "
        "auto=_data.json 이 있으면 그것, 없으면 원본 (기본값)",
    )
    g.add_argument("--from-out", help="AF3 출력 폴더 루트 (--csv 의 출력경로 열이 있으면 생략 가능)")
    g.add_argument("--input-dir", help="원본 입력 JSON 폴더 (--source input/auto 에서 필요)")

    g = p.add_argument_group("2단계 설정")
    g.add_argument("-o", "--outdir", required=True, help="재실행 입력 JSON 을 쓸 폴더")
    g.add_argument("--seeds", default="1", help="modelSeeds (예: 1 또는 1,2,3 또는 1-5). 기본 1")
    g.add_argument("--name-suffix", default="", help="JSON name 에 붙일 접미어 (출력 폴더 이름이 바뀐다)")
    g.add_argument(
        "--strip-msa",
        action="store_true",
        help="_data.json 에서 MSA/템플릿을 지워 2단계에서 MSA 를 다시 검색하게 한다 "
        "(축소DB 로 1단계, 전체DB 로 2단계를 돌릴 때)",
    )
    g.add_argument("--json-version", type=int, default=None, help="JSON version 값을 이 값으로 (기본: 원본 유지)")

    g = p.add_argument_group("동작")
    g.add_argument("--overwrite", action="store_true", help="출력 폴더에 이미 있는 JSON 을 덮어쓴다")
    g.add_argument("--manifest", default=None, help="선정 내역을 이 CSV 로 저장 (기본: 출력폴더/2단계_선정내역.csv)")
    g.add_argument("--dry-run", action="store_true", help="쓰지 않고 무엇이 만들어질지만 보여준다")
    g.add_argument("--quiet", action="store_true", help="진행 메시지를 줄인다")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    if not args.csv and not args.name_list:
        die("--csv 또는 --list 중 하나는 필요하다.")
    if args.csv and args.name_list:
        die("--csv 와 --list 를 동시에 줄 수 없다.")
    if args.take_all and (args.top is not None or args.minimum is not None):
        die("--all 은 --top/--min 과 함께 쓸 수 없다. 선별할지 전건을 쓸지 하나만 정하라.")
    if args.csv and args.top is None and args.minimum is None and not args.take_all:
        die(
            "--top 또는 --min 을 반드시 지정하라 (선별하지 않고 전건을 쓸 거면 --all).\n"
            "  이 스크립트에는 컷오프 기본값이 없다. 경량 설정과 기본값 설정 사이의\n"
            "  순위 보존은 이 저장소에서 측정되지 않았으므로, 근거 없는 숫자를\n"
            "  기본값으로 넣지 않는다. 컷오프를 정하는 절차는\n"
            "  af3_rankcorr.py 와 docs/two_stage_notes.md 를 보라."
        )
    if args.top is not None and args.top <= 0:
        die("--top 은 1 이상이어야 한다.")

    seeds = parse_seeds(args.seeds)
    out_root = Path(args.from_out) if args.from_out else None
    input_dir = Path(args.input_dir) if args.input_dir else None
    outdir = Path(args.outdir)

    # ---- 후보 목록 -------------------------------------------------------
    scored_all = []
    if args.csv:
        rows = read_collect_csv(Path(args.csv))
        picked, values, scored_all = select_rows(
            rows,
            by=args.by,
            top=None if args.take_all else args.top,
            minimum=args.minimum,
            grades=args.grade,
            condition=args.condition,
            quiet=args.quiet,
            take_all=args.take_all,
        )
        targets = [(r[COL_TARGET], r.get(COL_PATH) or None, v) for r, v in zip(picked, values)]
    else:
        names = read_name_list(Path(args.name_list))
        if args.top is not None:
            names = names[: args.top]
        targets = [(n, None, None) for n in names]

    if not targets:
        die("선정된 후보가 없다.")

    # ---- 원본 입력 찾기 --------------------------------------------------
    plan, skipped = [], []
    for target, hint, value in targets:
        data_p = None
        if args.source in ("auto", "data"):
            data_p = find_data_json(target, out_root, hint)
        input_p = None
        if args.source in ("auto", "input") and (data_p is None or args.source == "input"):
            if input_dir is not None:
                input_p = find_input_json(target, input_dir)

        if args.source == "data":
            src, kind = data_p, "data"
            if src is None:
                skipped.append((target, "_data.json 을 찾을 수 없다 (--from-out 경로 확인)"))
                continue
        elif args.source == "input":
            src, kind = input_p, "input"
            if src is None:
                if input_dir is None:
                    skipped.append((target, "--input-dir 이 필요하다"))
                else:
                    skipped.append((target, "원본 JSON 을 찾을 수 없다: %s" % input_dir))
                continue
        else:
            if data_p is not None:
                src, kind = data_p, "data"
            elif input_p is not None:
                src, kind = input_p, "input"
            else:
                skipped.append(
                    (
                        target,
                        "_data.json 도 원본 JSON 도 찾을 수 없다 "
                        "(--from-out / --input-dir 을 확인하라)",
                    )
                )
                continue

        built, err = build_one(
            src,
            seeds=seeds,
            name_suffix=args.name_suffix,
            do_strip=args.strip_msa,
            json_version=args.json_version,
        )
        if built is None:
            skipped.append((target, err))
            continue
        built.update({"target": target, "src": src, "kind": kind, "value": value})
        plan.append(built)

    if not plan:
        log("")
        for t, why in skipped[:20]:
            log("  건너뜀 %-24s %s" % (t, why))
        die("만들 수 있는 재실행 입력이 없다.")

    # 같은 출력 이름이 겹치면 2단계 결과가 서로를 덮어쓴다. 미리 막는다.
    seen_out = {}
    dup = []
    for item in plan:
        key = item["output_name"]
        if key in seen_out:
            dup.append((item["target"], seen_out[key]))
        else:
            seen_out[key] = item["target"]
    if dup:
        log("오류: 출력 폴더 이름이 겹치는 후보가 있다. 2단계 결과가 서로를 덮어쓴다.")
        for a, b in dup[:10]:
            log("  %s <-> %s" % (a, b))
        die("--condition 으로 한 조건만 고르거나 --name-suffix 로 이름을 구분하라.")

    # MSA 유무 확인. _data.json 을 쓰는데 MSA 가 비어 있으면 건너뛰기가 무의미하다.
    data_items = [i for i in plan if i["kind"] == "data" and not args.strip_msa]
    no_msa = [i["target"] for i in data_items if i["msa_chars"] == 0]

    # ---- 보고 ------------------------------------------------------------
    n_data = sum(1 for i in plan if i["kind"] == "data")
    n_input = len(plan) - n_data
    mode_hint = "inference" if (n_data and not args.strip_msa) else "full"

    print()
    print("2단계 재실행 입력 %d건" % len(plan))
    if args.csv:
        if args.take_all:
            print("  선별: --all (전건 %d건. 선별하지 않았다)" % len(scored_all))
        else:
            print(
                "  선별: %s%s (%s 기준). 전체 %d건 중"
                % (
                    ("상위 %d건 " % args.top) if args.top is not None else "",
                    ("%s >= %.4g" % (args.by, args.minimum)) if args.minimum is not None else "",
                    args.by,
                    len(scored_all),
                )
            )
        vals = [i["value"] for i in plan if i["value"] is not None]
        if vals and not args.take_all:
            print("  선정 구간: %s = %.4g ~ %.4g (컷오프 %.4g)" % (args.by, min(vals), max(vals), min(vals)))
        elif vals:
            print("  %s 범위: %.4g ~ %.4g" % (args.by, min(vals), max(vals)))
    print("  원본: _data.json %d건, 원본 입력 JSON %d건" % (n_data, n_input))
    if args.strip_msa:
        print("  --strip-msa: MSA/템플릿을 지웠다. 2단계에서 MSA 를 다시 검색한다 (--mode full).")
    elif n_data:
        med = sorted(i["msa_chars"] for i in data_items)
        if med:
            print(
                "  MSA 내장 확인: %d건, 중앙값 %d 문자. --mode inference 로 돌리면 MSA 를 건너뛴다."
                % (len(med), med[len(med) // 2])
            )
    if no_msa:
        print(
            "  경고: _data.json 인데 MSA 가 비어 있는 %d건 (%s). MSA 건너뛰기가 무의미하다."
            % (len(no_msa), ", ".join(no_msa[:5]))
        )
    if skipped:
        print("  건너뜀 %d건:" % len(skipped))
        for t, why in skipped[:10]:
            print("    %-24s %s" % (t, why))
        if len(skipped) > 10:
            print("    ... (%d건 더)" % (len(skipped) - 10))
    dropped_any = sorted({k for i in plan for k in i["dropped"]})
    if dropped_any:
        print("  AF3 가 모르는 최상위 키를 떨어냈다: %s" % ", ".join(dropped_any))

    for i in plan[:10]:
        print(
            "    %-24s <- %-6s %s"
            % (i["output_name"], i["kind"], os.path.basename(str(i["src"])))
        )
    if len(plan) > 10:
        print("    ... (%d건 더)" % (len(plan) - 10))

    if args.dry_run:
        print()
        print("--dry-run 이므로 아무것도 쓰지 않았다.")
        return 0

    # ---- 쓰기 ------------------------------------------------------------
    outdir.mkdir(parents=True, exist_ok=True)
    written, collisions = [], []
    width = len(str(len(plan)))
    for idx, item in enumerate(plan, 1):
        fname = "%0*d_%s.json" % (width, idx, item["output_name"])
        path = outdir / fname
        if path.exists() and not args.overwrite:
            collisions.append(fname)
            continue
        tmp = path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(item["obj"], fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
        item["written"] = path
        written.append(path)

    if collisions:
        log(
            "경고: 이미 있는 파일 %d개를 건너뜀 (--overwrite 로 덮어쓴다): %s"
            % (len(collisions), ", ".join(collisions[:5]))
        )

    total_mb = sum(p.stat().st_size for p in written) / 1024.0 / 1024.0

    manifest = Path(args.manifest) if args.manifest else outdir / "2단계_선정내역.csv"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    with open(manifest, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "순위",
                "타깃",
                "1단계_" + args.by,
                "원본종류",
                "원본경로",
                "2단계_입력파일",
                "2단계_출력폴더이름",
                "MSA문자수",
                "modelSeeds",
            ]
        )
        for rank, item in enumerate(plan, 1):
            if "written" not in item:
                continue
            w.writerow(
                [
                    rank,
                    item["target"],
                    "" if item["value"] is None else item["value"],
                    item["kind"],
                    str(item["src"]),
                    item["written"].name,
                    item["output_name"],
                    item["msa_chars"],
                    ",".join(str(s) for s in seeds),
                ]
            )

    print()
    print("입력 JSON %d개 -> %s (%.1f MB)" % (len(written), outdir, total_mb))
    print("선정 내역 -> %s" % manifest)
    print()
    print("다음 단계 - 이 명령을 그대로 복사해 붙여라")
    print("  python3 run_af3_batch_improved.py --mode %s \\" % mode_hint)
    print("      --input-dir %s \\" % outdir)
    print("      --output-dir %s_out" % str(outdir).rstrip("/"))
    if mode_hint == "inference":
        print("  (--mode inference 가 AF3 에 --norun_data_pipeline 을 붙인다. MSA 를 건너뛴다.)")
        print("  Docker 없이 conda 네이티브로 돌린다면:")
        print("      python3 run_alphafold.py --input_dir %s \\" % outdir)
        print("          --output_dir %s_out --model_dir ~/af3_models \\" % str(outdir).rstrip("/"))
        print("          --norun_data_pipeline")
    else:
        print("  (MSA 를 다시 검색하므로 --mode full 이다. DB 폴더가 필요하다.)")
    print()
    print("주의: 이 선별의 컷오프가 옳은지는 이 스크립트가 판단하지 않는다.")
    print("      경량 설정과 기본값 설정 사이의 순위 보존은 측정되지 않았다.")
    print("      af3_rankcorr.py 로 자기 데이터에서 순위 상관을 먼저 재라.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
