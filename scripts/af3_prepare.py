#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
af3_prepare.py - FASTA 나 CSV 로 갖고 있는 서열을 AlphaFold 3 입력 JSON 으로 바꾼다.

무엇을 하는가
    손에 있는 것:  vhh_2000.fasta  (또는 vhh_2000.csv)
    필요한 것:     vhh_in/01_vhh_001.json, vhh_in/02_vhh_002.json, ... (AF3 가 읽는 형식)
    이 스크립트가 그 변환을 한다. 만들어진 폴더를 그대로
    af3_batch.py --input_dir vhh_in 또는 run_alphafold.py --input_dir vhh_in 에 넘기면 된다.

왜 필요한가
    af3_batch.py 는 JSON 을 '읽기만' 한다. 2000 건을 손으로 JSON 으로 쓰는 것은 불가능하고,
    한 글자 틀린 JSON 으로 2000 건을 돌리면 며칠을 버린다. 그래서 만들기 전에 검사한다.

의존성
    표준 라이브러리만 쓴다. python3.8 이상.
    (rdkit 이 있으면 --ligand-smiles 의 원자 수를 정확히 센다. 없어도 동작한다.)

가장 흔한 사용법 세 가지
    # 1) FASTA 한 장을 단량체 JSON 여러 개로
    python3 af3_prepare.py --fasta vhh_2000.fasta -o vhh_in

    # 2) CSV (이름 열 + 서열 열). 열 이름은 자동으로 찾고, 못 찾으면 알려준다
    python3 af3_prepare.py --csv vhh_2000.csv -o vhh_in
    python3 af3_prepare.py --csv vhh_2000.csv --name-col 클론명 --seq-col 아미노산서열 -o vhh_in

    # 3) 모든 VHH 에 같은 항원을 B 사슬로 붙여 복합체로 (다음 단계에 할 일)
    python3 af3_prepare.py --fasta vhh_2000.fasta -o vhh_ag_in \\
        --partner-fasta antigen.fasta

    # 만들기 전에 무엇이 만들어지는지만 보기
    python3 af3_prepare.py --fasta vhh_2000.fasta -o vhh_in --dry-run

확인한 AF3 입력 JSON 스펙 (실물 소스 확인, 추측 아님)
    확인 대상: alphafold3/common/folding_input.py
              (alphafold3-3.0.5.dev15+g97d20234c, 검증 호스트 gpu-5070ti)
    최상위 허용 키: dialect, version, name, modelSeeds, sequences,
                    bondedAtomPairs, userCCD, userCCDPath
      * dialect 는 반드시 "alphafold3"  (다른 값이면 거부)
      * version 은 1, 2, 3, 4 중 하나  (JSON_VERSIONS = (1,2,3,4))
        버전 값은 목록에 있는지만 검사하고 파싱을 바꾸지 않는다. 그래서 이 스크립트는
        가장 넓게 호환되는 1 을 기본으로 쓴다. --json-version 으로 바꿀 수 있다.
      * modelSeeds 는 비어 있으면 거부된다. 최소 1개 정수.
      * sequences 는 비어 있으면 거부된다.
    protein 사슬 허용 키: id, sequence, modifications, description,
                          unpairedMsa, unpairedMsaPath, pairedMsa, pairedMsaPath, templates
    ligand 사슬 허용 키: id, ccdCodes, smiles, description
      * ccdCodes 와 smiles 를 동시에 주면 거부. ccdCodes 는 반드시 리스트.
    사슬 id: 각 sequences 항목의 id. 문자열 하나 또는 문자열 리스트(같은 서열 여러 부).
             id 가 하나라도 비어 있으면 거부된다.
    서열 문자: protein 은 전부 알파벳이어야 한다 (res.isalpha()). 표준 20종이 아닌
             알파벳은 거부되지 않고 조용히 UNK/X 로 처리되므로 이 스크립트가 미리 경고한다.

주의 (실제로 시간을 날린 함정)
    * macOS 에서 만든 tar.gz 를 리눅스에서 풀면 '._' 로 시작하는 AppleDouble 사이드카가
      같이 생긴다. ls 에는 잘 안 보이지만 glob('*.json') 에 잡히고 UTF-8 이 아니라서
      AF3 가 읽는 순간 죽는다. 이 스크립트는 출력 폴더에 그런 파일이 있으면 경고한다.
      폴더를 옮길 때는 tar 대신 rsync 를 쓰거나, 리눅스에서 직접 만들어라.
    * AF3 패딩 버킷 사다리는 128 에서 시작한다. 토큰 128개까지는 128 버킷,
      129개부터는 256 버킷이고 256 버킷은 실측으로 2.25배 느리다
      (정상상태 4.20초 대 9.44초, 검증 호스트 RTX 5070 Ti).
      즉 130 aa 서열은 129~130 토큰이라 256 버킷으로 넘어간다. 이 스크립트는 그 분포를
      마지막에 요약해 보여준다. 태그를 붙이거나 링커를 넣어 길이를 늘리기 전에 꼭 봐라.
"""

import argparse
import csv
import json
import os
import re
import string
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# 상수
# ---------------------------------------------------------------------------

# AF3 가 요구하는 dialect 문자열 (folding_input.py: JSON_DIALECT)
JSON_DIALECT = "alphafold3"
# AF3 가 받아주는 version 값 (folding_input.py: JSON_VERSIONS)
JSON_VERSIONS = (1, 2, 3, 4)

# run_alphafold.py 의 --buckets 기본값. 128 에서 시작한다 (실물 확인).
DEFAULT_BUCKETS = [128, 256, 384, 512, 768, 1024, 1280, 1536, 2048, 2560,
                   3072, 3584, 4096, 4608, 5120]

# 표준 아미노산 20종. AF3 는 이 밖의 알파벳도 거부하지 않고 UNK 로 넘기므로 우리가 경고한다.
STANDARD_AA = set("ACDEFGHIKLMNPQRSTVWY")
# 자주 섞여 들어오는, 아미노산이 아닌 알파벳
AMBIGUOUS_AA = {
    "B": "Asx (D 또는 N). AF3 는 UNK 로 처리한다",
    "Z": "Glx (E 또는 Q). AF3 는 UNK 로 처리한다",
    "J": "Xle (I 또는 L). AF3 는 UNK 로 처리한다",
    "X": "미지 잔기. AF3 는 UNK 로 처리한다",
    "U": "셀레노시스테인. AF3 는 UNK 로 처리한다",
    "O": "피롤리신. AF3 는 UNK 로 처리한다",
    "*": "종결코돈 기호. 서열에서 빼야 한다",
    "-": "정렬 갭. 서열에서 빼야 한다",
    ".": "정렬 갭. 서열에서 빼야 한다",
}

# AF3 가 파일명으로 쓸 때 남기는 문자 (folding_input.py: sanitised_name)
SAFE_CHARS = set(string.ascii_letters + string.digits + "_-.")

# CSV 열 이름 자동 인식. 소문자로 바꾼 뒤 비교한다.
NAME_COL_HINTS = ["name", "id", "이름", "클론", "클론명", "클론이름", "target",
                  "타깃", "타겟", "label", "라벨", "sample", "샘플", "seq_id",
                  "seqid", "identifier", "번호", "no", "clone", "clone_id",
                  "vhh", "vhh_id", "nanobody", "나노바디", "항체", "antibody"]
SEQ_COL_HINTS = ["sequence", "seq", "서열", "아미노산", "아미노산서열", "protein",
                 "aa", "aaseq", "aa_seq", "amino_acid", "amino_acid_sequence",
                 "단백질", "단백질서열", "peptide", "fasta"]


def log(msg):
    """진행 상황은 stderr 로. stdout 은 요약표 전용."""
    print(msg, file=sys.stderr, flush=True)


def die(msg):
    log("")
    log("오류: " + msg)
    log("")
    sys.exit(1)


# ---------------------------------------------------------------------------
# 이름 정규화
# ---------------------------------------------------------------------------

def sanitise_name(raw):
    """AF3 가 출력 폴더명으로 쓰는 규칙과 같게 이름을 다듬는다.

    AF3 sanitised_name(): 공백을 _ 로 바꾸고, [A-Za-z0-9_-.] 이외를 '버린다'.
    버리기만 하면 '항체 #1' 과 '항체 #2' 가 둘 다 '항체' 가 아니라 ''(빈 문자열)이 되어
    충돌한다. 그래서 우리는 '버리기' 대신 '_ 로 바꾸기' 를 하고,
    AF3 규칙을 통과하는지 마지막에 확인한다.
    """
    s = raw.strip()
    s = s.replace(" ", "_").replace("\t", "_")
    s = "".join(ch if ch in SAFE_CHARS else "_" for ch in s)
    s = re.sub(r"_+", "_", s).strip("_.")
    if not s:
        s = "unnamed"
    # AF3 가 이 이름으로 폴더를 만들 수 있는지 (버려질 문자가 없는지) 확인
    assert all(ch in SAFE_CHARS for ch in s), s
    return s


def af3_sanitised_name(raw):
    """AF3 가 실제로 만들 폴더 이름. 우리 이름과 다르면 사용자에게 알려준다."""
    spaceless = raw.replace(" ", "_")
    return "".join(ch for ch in spaceless if ch in SAFE_CHARS)


# ---------------------------------------------------------------------------
# 입력 읽기
# ---------------------------------------------------------------------------

def read_fasta(path):
    """FASTA 를 [(이름, 서열, 원본줄번호)] 로 읽는다.

    헤더의 첫 공백까지를 이름으로 쓴다 (>vhh_001 some description -> vhh_001).
    """
    records = []
    name = None
    header_line = 0
    chunks = []
    try:
        fh = open(path, "r", encoding="utf-8")
    except UnicodeDecodeError:
        die("'%s' 를 UTF-8 로 읽을 수 없다. AppleDouble 사이드카(._로 시작하는 파일)이거나\n"
            "      다른 인코딩일 수 있다. `file '%s'` 로 확인해 봐라." % (path, path))
    except OSError as e:
        die("'%s' 를 열 수 없다: %s" % (path, e))

    with fh:
        for lineno, line in enumerate(fh, 1):
            line = line.rstrip("\r\n")
            if not line.strip():
                continue
            # ';' 와 '#' 로 시작하는 줄은 주석으로 보고 건너뛴다 (구형 FASTA 관례).
            if line.lstrip().startswith((";", "#")):
                continue
            if line.startswith(">"):
                if name is not None:
                    records.append((name, "".join(chunks), header_line))
                header = line[1:].strip()
                name = header.split()[0] if header.split() else ""
                header_line = lineno
                chunks = []
            else:
                if name is None:
                    die("'%s' 의 %d번째 줄에 '>' 헤더 없이 서열이 먼저 나온다.\n"
                        "      FASTA 는 반드시 '>이름' 줄로 시작해야 한다." % (path, lineno))
                chunks.append(line.strip())
        if name is not None:
            records.append((name, "".join(chunks), header_line))

    if not records:
        die("'%s' 에서 서열을 하나도 찾지 못했다. FASTA 파일인지 확인해라." % path)
    return records


def pick_column(fieldnames, hints, explicit, what):
    """CSV 열 이름을 자동으로 고른다. 못 고르면 사람이 읽을 수 있는 오류를 낸다."""
    if explicit:
        for f in fieldnames:
            if f is not None and f.strip() == explicit.strip():
                return f
        die("CSV 에 '%s' 라는 열이 없다.\n"
            "      실제 열 이름: %s" % (explicit, ", ".join(repr(f) for f in fieldnames)))
    lowered = {}
    for f in fieldnames:
        if f is None:
            continue
        key = re.sub(r"[\s_\-]+", "", f.strip().lower())
        lowered.setdefault(key, f)
    for h in hints:
        key = re.sub(r"[\s_\-]+", "", h.lower())
        if key in lowered:
            return lowered[key]
    # 부분 일치도 시도
    for h in hints:
        key = re.sub(r"[\s_\-]+", "", h.lower())
        for k, orig in lowered.items():
            if key and key in k:
                return orig
    die("CSV 에서 %s 열을 자동으로 찾지 못했다.\n"
        "      실제 열 이름: %s\n"
        "      --%s-col '열이름' 으로 직접 지정해라."
        % (what, ", ".join(repr(f) for f in fieldnames),
           "name" if what == "이름" else "seq"))


def read_csv_input(path, name_col, seq_col):
    """CSV 를 [(이름, 서열, 줄번호)] 로 읽는다. 구분자는 자동 추정(콤마/탭/세미콜론)."""
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as fh:
            head = fh.read(8192)
            fh.seek(0)
            try:
                dialect = csv.Sniffer().sniff(head, delimiters=",\t;|")
            except csv.Error:
                dialect = csv.excel
            reader = csv.DictReader(fh, dialect=dialect)
            if not reader.fieldnames:
                die("'%s' 에 헤더 줄이 없다. 첫 줄에 열 이름이 있어야 한다." % path)
            ncol = pick_column(reader.fieldnames, NAME_COL_HINTS, name_col, "이름")
            scol = pick_column(reader.fieldnames, SEQ_COL_HINTS, seq_col, "서열")
            log("CSV 열 인식: 이름='%s', 서열='%s'" % (ncol, scol))
            records = []
            for lineno, row in enumerate(reader, 2):  # 2 = 헤더 다음 줄
                nm = (row.get(ncol) or "").strip()
                sq = (row.get(scol) or "").strip()
                if not nm and not sq:
                    continue  # 완전히 빈 줄은 조용히 넘긴다
                records.append((nm, sq, lineno))
    except UnicodeDecodeError:
        die("'%s' 를 UTF-8 로 읽을 수 없다. 엑셀에서 저장했다면\n"
            "      '다른 이름으로 저장 > CSV UTF-8' 을 골라 다시 저장해라." % path)
    except OSError as e:
        die("'%s' 를 열 수 없다: %s" % (path, e))
    if not records:
        die("'%s' 에서 데이터 줄을 하나도 찾지 못했다." % path)
    return records


def read_single_sequence(spec, what):
    """--partner-fasta / --partner-seq 처리. 파일이면 첫 레코드, 아니면 문자열 그대로."""
    if spec is None:
        return None, None
    if os.path.isfile(spec):
        recs = read_fasta(spec)
        if len(recs) > 1:
            log("주의: %s 파일에 서열이 %d개 있다. 첫 번째('%s')만 쓴다."
                % (what, len(recs), recs[0][0]))
        return recs[0][0], clean_sequence(recs[0][1])
    return None, clean_sequence(spec)


def clean_sequence(raw):
    """공백/줄바꿈/번호를 지우고 대문자로. 문자 검증은 따로 한다."""
    s = re.sub(r"[\s\d]+", "", raw or "")
    return s.upper()


# ---------------------------------------------------------------------------
# 검증
# ---------------------------------------------------------------------------

class Problem:
    """한 건의 문제. where 는 사용자가 파일에서 바로 찾을 수 있는 위치."""

    def __init__(self, level, where, name, msg):
        self.level = level        # "오류" 또는 "경고"
        self.where = where        # "12번째 줄" 같은 문자열
        self.name = name
        self.msg = msg

    def line(self):
        return "  [%s] %s (%s): %s" % (self.level, self.where, self.name or "이름없음", self.msg)


def validate_records(records, source_label, min_len, max_len, allow_ambiguous):
    """서열을 검사해서 (통과한 레코드, 문제 목록) 을 준다.

    잡는 것: 빈 이름, 빈 서열, 알파벳이 아닌 문자, 표준 20종 아닌 알파벳,
             너무 짧음/너무 김, 이름 중복, AF3 정규화 후 이름 충돌.
    """
    problems = []
    seen_name = {}
    seen_seq = {}
    ok = []

    for idx, (raw_name, raw_seq, lineno) in enumerate(records, 1):
        where = "%s %d번째 줄" % (source_label, lineno)
        seq = clean_sequence(raw_seq)
        name = raw_name.strip()

        if not name:
            name = "unnamed_%04d" % idx
            problems.append(Problem("경고", where, name,
                                    "이름이 비어 있어 '%s' 로 자동 지정했다" % name))

        if not seq:
            problems.append(Problem("오류", where, name, "서열이 비어 있다"))
            continue

        bad = sorted({ch for ch in seq if not ch.isalpha()})
        if bad:
            problems.append(Problem(
                "오류", where, name,
                "아미노산이 아닌 문자 %s 가 있다 (AF3 는 알파벳만 받는다). "
                "종결코돈 '*', 정렬 갭 '-' 이 남아 있는지 확인해라"
                % ", ".join(repr(b) for b in bad)))
            continue

        nonstd = sorted({ch for ch in seq if ch not in STANDARD_AA})
        if nonstd:
            desc = ", ".join("%s(%s)" % (ch, AMBIGUOUS_AA.get(ch, "표준 20종이 아니다"))
                             for ch in nonstd)
            level = "경고" if allow_ambiguous else "오류"
            problems.append(Problem(level, where, name,
                                    "표준 아미노산이 아닌 문자: %s. "
                                    "그대로 돌리려면 --allow-ambiguous" % desc))
            if not allow_ambiguous:
                continue

        if len(seq) < min_len:
            problems.append(Problem("오류", where, name,
                                    "서열이 %d 잔기뿐이다 (--min-len %d 미만). "
                                    "잘린 서열이 아닌지 확인해라" % (len(seq), min_len)))
            continue

        if len(seq) > max_len:
            problems.append(Problem("오류", where, name,
                                    "서열이 %d 잔기다 (--max-len %d 초과). "
                                    "메모리와 시간이 급격히 늘어난다. 정말 돌릴 것이면 "
                                    "--max-len 을 올려라" % (len(seq), max_len)))
            continue

        if name in seen_name:
            problems.append(Problem("오류", where, name,
                                    "이름이 %s 와 중복이다. AF3 출력 폴더가 덮어써진다"
                                    % seen_name[name]))
            continue
        seen_name[name] = where

        safe = sanitise_name(name)
        if safe in seen_seq:
            problems.append(Problem(
                "오류", where, name,
                "이름을 파일명으로 다듬으면 '%s' 가 되어 %s 와 충돌한다. "
                "이름에서 특수문자를 빼라" % (safe, seen_seq[safe])))
            continue
        seen_seq[safe] = "%s(%s)" % (name, where)

        if safe != name:
            problems.append(Problem("경고", where, name,
                                    "파일명으로 쓸 수 없는 문자가 있어 '%s' 로 바꿨다" % safe))

        af3_name = af3_sanitised_name(name)
        if af3_name != safe:
            problems.append(Problem(
                "경고", where, name,
                "AF3 는 출력 폴더 이름을 '%s' 로 만든다 (JSON 의 name 필드는 '%s'). "
                "혼동을 피하려면 이름을 영문/숫자/_ 로만 쓰는 것이 안전하다"
                % (af3_name if af3_name else "(빈 이름)", safe)))

        ok.append((name, safe, seq, where))

    return ok, problems


# ---------------------------------------------------------------------------
# 토큰 수와 버킷
# ---------------------------------------------------------------------------

def smiles_heavy_atoms(smiles):
    """SMILES 의 heavy atom 수. rdkit 이 있으면 정확히, 없으면 None."""
    try:
        from rdkit import Chem  # noqa: PLC0415
        from rdkit import RDLogger  # noqa: PLC0415
        RDLogger.DisableLog("rdApp.*")
        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            return "invalid"
        return mol.GetNumHeavyAtoms()
    except ImportError:
        return None


def bucket_for(n_tokens, buckets):
    """토큰 수가 걸리는 패딩 버킷. 가장 큰 버킷보다 크면 그 값 자체가 버킷이 된다."""
    for b in buckets:
        if n_tokens <= b:
            return b
    return n_tokens


def chain_ids(n):
    """A, B, ... Z, AA, AB ... AF3 mmcif.int_id_to_str_id 와 같은 규칙 (1-based)."""
    out = []
    for i in range(1, n + 1):
        num = i
        s = ""
        while num > 0:
            num, rem = divmod(num - 1, 26)
            s = chr(ord("A") + rem) + s
        out.append(s)
    return out


# ---------------------------------------------------------------------------
# JSON 만들기
# ---------------------------------------------------------------------------

def build_fold_job(name, seq, copies, partner_seq, partner_copies,
                   ligand_ccd, ligand_smiles, ligand_copies, seeds, version):
    """AF3 입력 JSON 한 건(dict)을 만든다.

    사슬 순서: 대상 서열(A...) -> 파트너(그 다음) -> 리간드(마지막).
    같은 서열 여러 부는 id 를 리스트로 주는 방식을 쓴다 (AF3 가 지원하는 형식).
    """
    total_chains = copies + (partner_copies if partner_seq else 0) \
        + (ligand_copies if (ligand_ccd or ligand_smiles) else 0)
    ids = chain_ids(total_chains)
    cursor = 0

    sequences = []

    target_ids = ids[cursor:cursor + copies]
    cursor += copies
    sequences.append({"protein": {
        "id": target_ids if len(target_ids) > 1 else target_ids[0],
        "sequence": seq,
    }})

    if partner_seq:
        pids = ids[cursor:cursor + partner_copies]
        cursor += partner_copies
        sequences.append({"protein": {
            "id": pids if len(pids) > 1 else pids[0],
            "sequence": partner_seq,
        }})

    if ligand_ccd or ligand_smiles:
        lids = ids[cursor:cursor + ligand_copies]
        cursor += ligand_copies
        lig = {"id": lids if len(lids) > 1 else lids[0]}
        if ligand_ccd:
            # AF3 는 ccdCodes 를 반드시 리스트로 받는다 (실물 확인)
            lig["ccdCodes"] = list(ligand_ccd)
        else:
            lig["smiles"] = ligand_smiles
        sequences.append({"ligand": lig})

    return {
        "name": name,
        "modelSeeds": list(seeds),
        "sequences": sequences,
        "dialect": JSON_DIALECT,
        "version": version,
    }


def count_tokens(seq, copies, partner_seq, partner_copies,
                 ligand_ccd, ligand_smiles, ligand_copies):
    """토큰 수를 센다.

    AF3 토큰화: 표준 아미노산 1잔기 = 1토큰. 리간드/비표준 잔기는 원자 1개 = 1토큰.
    반환: (토큰수, 리간드가 불확실한지 여부)
    """
    n = len(seq) * copies
    if partner_seq:
        n += len(partner_seq) * partner_copies
    uncertain = False
    if ligand_smiles:
        na = smiles_heavy_atoms(ligand_smiles)
        if isinstance(na, int):
            n += na * ligand_copies
        else:
            uncertain = True
    elif ligand_ccd:
        # CCD 성분의 원자 수는 ccd.pickle 을 읽어야 알 수 있다. 여기서는 세지 않는다.
        uncertain = True
    return n, uncertain


# ---------------------------------------------------------------------------
# 출력 폴더 점검
# ---------------------------------------------------------------------------

def check_outdir(outdir, overwrite, dry_run):
    """AppleDouble 사이드카와 기존 JSON 을 점검한다."""
    p = Path(outdir)
    if not p.exists():
        return []
    warnings = []
    sidecars = [f.name for f in p.iterdir() if f.name.startswith("._")]
    if sidecars:
        warnings.append(
            "출력 폴더 '%s' 에 AppleDouble 사이드카가 %d개 있다: %s%s\n"
            "      AF3 가 glob('*.json') 으로 잡아서 UTF-8 오류로 죽는다. 먼저 지워라:\n"
            "        find %s -name '._*' -delete"
            % (outdir, len(sidecars), ", ".join(sidecars[:3]),
               " ..." if len(sidecars) > 3 else "", outdir))
    existing = [f.name for f in p.iterdir()
                if f.suffix == ".json" and not f.name.startswith("._")]
    if existing and not overwrite and not dry_run:
        warnings.append(
            "출력 폴더 '%s' 에 이미 JSON 이 %d개 있다. 같은 이름은 덮어쓰지 않고 건너뛴다.\n"
            "      전부 다시 만들려면 --overwrite" % (outdir, len(existing)))
    return warnings


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(
        prog="af3_prepare.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="FASTA / CSV 서열을 AlphaFold 3 입력 JSON 으로 바꾼다.",
        epilog="""예시
  # FASTA -> 단량체 JSON 폴더
  python3 af3_prepare.py --fasta vhh.fasta -o vhh_in

  # CSV, 열 이름 직접 지정
  python3 af3_prepare.py --csv vhh.csv --name-col 클론명 --seq-col 서열 -o vhh_in

  # 모든 서열에 같은 항원을 B 사슬로 붙이기 (복합체 스크리닝)
  python3 af3_prepare.py --fasta vhh.fasta --partner-fasta antigen.fasta -o vhh_ag_in

  # 동일 서열 2부 (homodimer)
  python3 af3_prepare.py --fasta vhh.fasta --copies 2 -o vhh_dimer_in

  # 리간드 붙이기 (CCD 코드 또는 SMILES)
  python3 af3_prepare.py --fasta target.fasta --ligand-ccd ATP -o with_atp_in
  python3 af3_prepare.py --fasta target.fasta --ligand-smiles 'CC(=O)Oc1ccccc1C(=O)O' -o with_asp_in

  # 시드 3개로 (같은 서열을 시드마다 예측. 시간이 3배 든다)
  python3 af3_prepare.py --fasta vhh.fasta --seeds 1,2,3 -o vhh_in

  # 만들지 않고 무엇이 만들어지는지만 보기
  python3 af3_prepare.py --fasta vhh.fasta -o vhh_in --dry-run
""")

    g = p.add_argument_group("입력 (하나는 반드시 지정)")
    g.add_argument("--fasta", help="FASTA 파일 경로")
    g.add_argument("--csv", help="CSV/TSV 파일 경로 (첫 줄이 열 이름)")
    g.add_argument("--name-col", help="CSV 의 이름 열. 생략하면 자동 인식")
    g.add_argument("--seq-col", help="CSV 의 서열 열. 생략하면 자동 인식")

    g = p.add_argument_group("출력")
    g.add_argument("-o", "--outdir", required=True,
                   help="JSON 을 만들 폴더. 없으면 만든다")
    g.add_argument("--no-index", action="store_true",
                   help="파일명 앞에 순번(01_, 02_ ...)을 붙이지 않는다. "
                        "기본은 붙인다 (정렬이 사람이 보는 순서와 같아진다)")
    g.add_argument("--overwrite", action="store_true",
                   help="같은 이름의 JSON 이 있으면 덮어쓴다")
    g.add_argument("--report", help="타깃별 토큰수/버킷 표를 이 CSV 로 저장한다")
    g.add_argument("--dry-run", action="store_true",
                   help="파일을 만들지 않고 검사와 요약만 한다")

    g = p.add_argument_group("사슬 구성")
    g.add_argument("--copies", type=int, default=1,
                   help="대상 서열을 몇 부 넣을지 (homomer). 기본 1")
    g.add_argument("--partner-fasta",
                   help="모든 타깃에 공통으로 붙일 파트너 사슬 FASTA "
                        "(예: 모든 VHH 에 같은 항원)")
    g.add_argument("--partner-seq",
                   help="파트너 서열을 문자열로 직접 지정")
    g.add_argument("--partner-copies", type=int, default=1,
                   help="파트너 사슬 부수. 기본 1")
    g.add_argument("--ligand-ccd",
                   help="리간드 CCD 코드. 여러 성분은 콤마로 (예: ATP 또는 NAG,NAG)")
    g.add_argument("--ligand-smiles", help="리간드 SMILES")
    g.add_argument("--ligand-copies", type=int, default=1,
                   help="리간드 부수. 기본 1")

    g = p.add_argument_group("AF3 설정")
    g.add_argument("--seeds", default="1",
                   help="modelSeeds. 콤마로 여러 개 (예: 1,2,3). 기본 '1'. "
                        "시드 개수만큼 시간이 곱해진다")
    g.add_argument("--json-version", type=int, default=1,
                   choices=list(JSON_VERSIONS),
                   help="입력 JSON 의 version 필드. AF3 는 %s 를 받는다. "
                        "기본 1 (가장 넓게 호환)" % (JSON_VERSIONS,))
    g.add_argument("--buckets", default=None,
                   help="버킷 사다리를 콤마로 (기본은 AF3 기본값). 128 을 반드시 포함해라")

    g = p.add_argument_group("검증")
    g.add_argument("--min-len", type=int, default=10,
                   help="이보다 짧은 서열은 오류로 잡는다. 기본 10")
    g.add_argument("--max-len", type=int, default=3000,
                   help="이보다 긴 서열은 오류로 잡는다. 기본 3000")
    g.add_argument("--allow-ambiguous", action="store_true",
                   help="X, B, Z 같은 비표준 알파벳을 경고만 하고 통과시킨다")
    g.add_argument("--force", action="store_true",
                   help="오류가 있어도 통과한 것만 만든다 (기본은 하나라도 오류면 중단)")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)

    if bool(args.fasta) == bool(args.csv):
        die("--fasta 나 --csv 중 정확히 하나를 지정해라.\n"
            "      예: python3 af3_prepare.py --fasta vhh.fasta -o vhh_in")
    if args.partner_fasta and args.partner_seq:
        die("--partner-fasta 와 --partner-seq 는 동시에 쓸 수 없다.")
    if args.ligand_ccd and args.ligand_smiles:
        die("--ligand-ccd 와 --ligand-smiles 는 동시에 쓸 수 없다.\n"
            "      AF3 가 거부한다 (folding_input.py 에서 확인).")
    for nm, v in (("--copies", args.copies), ("--partner-copies", args.partner_copies),
                  ("--ligand-copies", args.ligand_copies)):
        if v < 1:
            die("%s 는 1 이상이어야 한다 (받은 값: %d)" % (nm, v))

    try:
        seeds = [int(s) for s in str(args.seeds).replace(" ", "").split(",") if s]
    except ValueError:
        die("--seeds 는 정수를 콤마로 나열해야 한다 (예: 1,2,3). 받은 값: %r" % args.seeds)
    if not seeds:
        die("--seeds 가 비어 있다. AF3 는 modelSeeds 가 비면 거부한다. 최소 1개 필요.")

    if args.buckets:
        try:
            buckets = sorted({int(b) for b in args.buckets.replace(" ", "").split(",") if b})
        except ValueError:
            die("--buckets 는 정수를 콤마로 나열해야 한다 (예: 128,256,384)")
        if 128 not in buckets:
            log("주의: --buckets 에 128 이 없다. AF3 기본 사다리는 128 에서 시작하고,")
            log("      130 aa 급 VHH 는 128 버킷에 담기지 못하면 2.25배 느려진다.")
    else:
        buckets = DEFAULT_BUCKETS

    ligand_ccd = None
    if args.ligand_ccd:
        ligand_ccd = [c.strip().upper() for c in args.ligand_ccd.split(",") if c.strip()]
        for c in ligand_ccd:
            if c.startswith("CCD_"):
                die("--ligand-ccd 에는 'CCD_' 접두어를 붙이지 않는다. "
                    "'%s' 대신 '%s' 로 써라." % (c, c[4:]))

    if args.ligand_smiles:
        na = smiles_heavy_atoms(args.ligand_smiles)
        if na == "invalid":
            die("--ligand-smiles 를 rdkit 이 분자로 읽지 못했다: %r\n"
                "      AF3 도 같은 rdkit 으로 읽으므로 그대로 넘기면 거부된다."
                % args.ligand_smiles)

    # ---- 입력 읽기 ----
    if args.fasta:
        records = read_fasta(args.fasta)
        source_label = os.path.basename(args.fasta)
    else:
        records = read_csv_input(args.csv, args.name_col, args.seq_col)
        source_label = os.path.basename(args.csv)
    log("읽은 서열: %d 건 (%s)" % (len(records), source_label))

    partner_name, partner_seq = read_single_sequence(
        args.partner_fasta or args.partner_seq, "파트너")
    if partner_seq:
        bad = sorted({ch for ch in partner_seq if not ch.isalpha()})
        if bad:
            die("파트너 서열에 아미노산이 아닌 문자 %s 가 있다."
                % ", ".join(repr(b) for b in bad))
        log("파트너 사슬: %s, %d 잔기, %d 부"
            % (partner_name or "(이름없음)", len(partner_seq), args.partner_copies))

    # ---- 검증 ----
    ok, problems = validate_records(records, source_label,
                                    args.min_len, args.max_len, args.allow_ambiguous)
    errors = [p for p in problems if p.level == "오류"]
    warns = [p for p in problems if p.level == "경고"]

    if problems:
        log("")
        log("검사 결과: 오류 %d건, 경고 %d건" % (len(errors), len(warns)))
        shown = 0
        for p in problems:
            if shown >= 40:
                log("  ... (나머지 %d건 생략)" % (len(problems) - shown))
                break
            log(p.line())
            shown += 1
    else:
        log("검사 결과: 문제 없음")

    if errors and not args.force:
        log("")
        log("오류가 %d건 있어서 아무것도 만들지 않았다." % len(errors))
        log("입력 파일을 고치고 다시 실행하거나, 통과한 %d건만 만들려면 --force 를 붙여라."
            % len(ok))
        sys.exit(2)

    if not ok:
        die("만들 수 있는 서열이 하나도 없다.")

    # ---- 출력 폴더 점검 ----
    for w in check_outdir(args.outdir, args.overwrite, args.dry_run):
        log("주의: " + w)

    # ---- 토큰/버킷 계산 ----
    rows = []
    # 순번 자릿수. 최소 2자리로 맞춘다 (01_, 02_ ... 가 사람이 보는 순서와 같게 정렬된다).
    width = max(2, len(str(len(ok))))
    for i, (name, safe, seq, where) in enumerate(ok, 1):
        ntok, uncertain = count_tokens(seq, args.copies, partner_seq,
                                       args.partner_copies, ligand_ccd,
                                       args.ligand_smiles, args.ligand_copies)
        bk = bucket_for(ntok, buckets)
        prefix = "" if args.no_index else ("%0*d_" % (width, i))
        fname = "%s%s.json" % (prefix, safe)
        rows.append({
            "순번": i, "이름": name, "파일명": fname,
            "잔기수": len(seq), "토큰수": ntok, "버킷": bk,
            "토큰불확실": "예" if uncertain else "",
            "출처": where,
        })

    # ---- 쓰기 ----
    made = skipped = 0
    if not args.dry_run:
        os.makedirs(args.outdir, exist_ok=True)
    for r, (name, safe, seq, where) in zip(rows, ok):
        path = os.path.join(args.outdir, r["파일명"])
        job = build_fold_job(name, seq, args.copies, partner_seq,
                             args.partner_copies, ligand_ccd, args.ligand_smiles,
                             args.ligand_copies, seeds, args.json_version)
        if args.dry_run:
            continue
        if os.path.exists(path) and not args.overwrite:
            skipped += 1
            continue
        # ensure_ascii=False 로 써도 AF3 는 UTF-8 로 읽는다. 이름에 한글이 있어도 안전.
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(job, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        made += 1

    # ---- 요약 ----
    n = len(rows)
    b_count = {}
    for r in rows:
        b_count[r["버킷"]] = b_count.get(r["버킷"], 0) + 1
    lens = sorted(r["토큰수"] for r in rows)

    print("=" * 66)
    print("AF3 입력 JSON 요약")
    print("=" * 66)
    print("입력 파일        : %s" % source_label)
    print("출력 폴더        : %s%s" % (args.outdir, "  (dry-run: 만들지 않았다)"
                                      if args.dry_run else ""))
    print("만든 JSON        : %d 개%s" % (made if not args.dry_run else n,
                                          "  (건너뜀 %d)" % skipped if skipped else ""))
    print("사슬 구성        : 대상 %d부%s%s" % (
        args.copies,
        " + 파트너 %d부(%d aa)" % (args.partner_copies, len(partner_seq)) if partner_seq else "",
        " + 리간드 %d부(%s)" % (args.ligand_copies,
                                ",".join(ligand_ccd) if ligand_ccd else "SMILES")
        if (ligand_ccd or args.ligand_smiles) else ""))
    print("modelSeeds       : %s  (시드 %d개 -> 추론 시간 약 %d배)"
          % (seeds, len(seeds), len(seeds)))
    print("dialect / version: %s / %d" % (JSON_DIALECT, args.json_version))
    print("")
    print("토큰 수          : 최소 %d, 중앙값 %d, 최대 %d"
          % (lens[0], lens[len(lens) // 2], lens[-1]))
    print("")
    print("버킷 분포 (패딩 후 실제로 계산되는 크기)")
    for b in sorted(b_count):
        cnt = b_count[b]
        bar = "#" * max(1, int(40.0 * cnt / n))
        print("  버킷 %-5d : %5d 건 (%5.1f%%) %s" % (b, cnt, 100.0 * cnt / n, bar))
    if len(b_count) > 1:
        print("")
        print("  버킷이 섞여 있다. 실측 기준 버킷 256 은 버킷 128 보다 건당 2.25배 느리다")
        print("  (정상상태 9.44초 대 4.20초, RTX 5070 Ti). 129 토큰이면 이미 256 버킷이다.")
        b128 = b_count.get(128, 0)
        b256 = b_count.get(256, 0)
        if b256 and b128:
            print("  128 버킷 %d건, 256 버킷 %d건. 태그/링커로 길이를 늘리기 전에 이 표를 봐라."
                  % (b128, b256))
    if any(r["토큰불확실"] for r in rows):
        print("")
        print("  주의: 리간드가 있어 토큰 수는 단백질 부분만 센 값이다.")
        print("        CCD 성분의 원자 수는 ccd.pickle 을 읽어야 알 수 있어 여기서는 세지 않았다.")
        print("        실제 토큰 수는 AF3 로그의 'Featurising ... with X tokens' 에서 확인해라.")
    print("")
    print("다음 단계")
    print("  python3 af3_batch.py --input_dir %s --output_dir %s"
          % (args.outdir, re.sub(r"_in$", "_out", args.outdir) or "out"))
    print("=" * 66)

    if args.report:
        fields = ["순번", "이름", "파일명", "잔기수", "토큰수", "버킷", "토큰불확실", "출처"]
        with open(args.report, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)
        log("표를 저장했다: %s" % args.report)

    if errors and args.force:
        log("")
        log("--force 로 오류 %d건을 건너뛰고 %d건만 만들었다. 위 오류 목록을 확인해라."
            % (len(errors), made if not args.dry_run else n))
        sys.exit(3)
    return 0


if __name__ == "__main__":
    sys.exit(main())
