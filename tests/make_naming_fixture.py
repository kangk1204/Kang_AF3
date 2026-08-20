#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""make_naming_fixture.py - 타깃명 정규화 검증용 가짜 AF3 출력 폴더를 만든다.

무엇을 왜 만드는가
    AF3 는 출력 폴더가 비어 있지 않으면 <폴더명>_<YYYYmmdd_HHMMSS> 폴더를 새로 만든다
    (alphafold3 commit 97d2023, run_alphafold.py 861~866행에서 확인).
    그래서 하나의 출력 폴더 안에는
        타깃명 == 폴더명        (첫 실행)
        타깃명 != 폴더명        (재실행. 폴더에는 타임스탬프가 붙지만 파일 stem 은 그대로)
    두 종류가 섞인다. 여기에 격리 폴더(.af3_incomplete), staging(.af3_pending_*),
    lock 파일까지 같은 출력 폴더에 있다.

    이 스크립트는 그 상황을 그대로 재현한다. AF3 실물을 돌리지 않고도
    af3_collect.py / af3_visualize.py / af3_batch.py 의 타깃명 결정과 완료 판정을
    검증할 수 있게 하는 것이 목적이다. 값은 실물과 같은 구조를 갖추되 내용은 합성이다.

사용법
    python3 tests/make_naming_fixture.py /tmp/fixture_out
    python3 scripts/af3_collect.py /tmp/fixture_out --no-msa-depth -o /tmp/before.csv

만드는 것 (각 항목이 검증하려는 함정을 주석에 적었다)
    VHH_001/                        정상 완료. 폴더명 == 파일 stem
    VHH_002/                        정상 완료 (복합체. ipTM 이 있다)
    VHH_004_20260820_101010/        AF3 재실행 폴더. 안의 파일 stem 은 VHH_004
    VHH_005/                        같은 타깃이 두 폴더에 있는 경우 - 오래된 쪽
    VHH_005_20260820_120000/        같은 타깃 - 최신 쪽 (ranking 이 더 높다)
    VHH_004_variantB/               이름이 VHH_004_ 로 시작하는 별개 타깃 (오탐 함정)
    VHH_006/                        한 폴더에 stem 이 두 개 섞인 경우
    VHH_007/                        _data.json 만 있다 (추론 중 끊김. 미완료)
    zzz_folder_9/                   폴더명과 파일 stem 이 완전히 다른 경우 (stem=VHH_009)
    .af3_incomplete/VHH_003/<스탬프>/  격리된 미완료 결과 (집계에 절대 섞이면 안 된다)
    .af3_pending_1234/              staging 폴더
    .run_af3_batch.lock             lock 파일
    ._VHH_099/                      macOS AppleDouble 사이드카
"""

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

AA = ["ALA", "GLY", "SER", "LEU", "VAL"]
# 잔기별 원자 이름. 원자 수를 실물처럼 잔기마다 다르게 둬서
# '원자 -> 잔기' 매핑이 실제로 동작하는지 확인할 수 있게 한다.
ATOMS = {
    "ALA": ["N", "CA", "C", "O", "CB"],
    "GLY": ["N", "CA", "C", "O"],
    "SER": ["N", "CA", "C", "O", "CB", "OG"],
    "LEU": ["N", "CA", "C", "O", "CB", "CG"],
    "VAL": ["N", "CA", "C", "O", "CB", "CG1"],
}


def build_model(chains, mean_plddt=85.0):
    """원자 목록 [(사슬, 잔기번호, 잔기명, 원자명, pLDDT)] 를 결정적으로 만든다.

    mmCIF 와 confidences.json 이 같은 목록을 공유하므로 두 파일의 원자 수가 맞고,
    af3_visualize.py 의 B값 검산(verify_bfactor)이 통과한다.
    pLDDT 는 mean_plddt 를 중심으로 +-6 흔들어 등급 판정이 실물처럼 갈리게 한다.
    """
    atoms = []
    k = 0
    for ch, n_res in chains:
        for i in range(1, n_res + 1):
            resn = AA[(i - 1) % len(AA)]
            for a in ATOMS[resn]:
                k += 1
                jitter = ((k * 37) % 25) - 12          # -12 ~ +12
                p = max(20.0, min(98.0, mean_plddt + jitter * 0.5))
                atoms.append((ch, i, resn, a, round(p, 2)))
    return atoms


def write_cif(path, atoms, name):
    """AF3 출력과 같은 최소 mmCIF. B_iso_or_equiv 에 pLDDT 를 소수 2자리로 쓴다."""
    lines = [
        "data_%s" % name,
        "#",
        "loop_",
        "_atom_site.group_PDB",
        "_atom_site.id",
        "_atom_site.label_atom_id",
        "_atom_site.label_comp_id",
        "_atom_site.label_asym_id",
        "_atom_site.label_seq_id",
        "_atom_site.auth_asym_id",
        "_atom_site.auth_seq_id",
        "_atom_site.Cartn_x",
        "_atom_site.Cartn_y",
        "_atom_site.Cartn_z",
        "_atom_site.occupancy",
        "_atom_site.B_iso_or_equiv",
    ]
    for i, (ch, resi, resn, atom, plddt) in enumerate(atoms, 1):
        lines.append(
            "ATOM %d %s %s %s %d %s %d %.3f %.3f %.3f 1.00 %.2f"
            % (i, atom, resn, ch, resi, ch, resi,
               1.5 * i, 2.5 * i, 0.5 * i, plddt))
    lines.append("#")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_target_files(dirpath, stem, chains, ranking, n_sample=5,
                       with_data=True, with_conf=True, with_cif=True,
                       with_rank=True, with_summary=True, mean_plddt=85.0):
    """AF3 결과 폴더 하나의 산출물을 쓴다. 무엇을 뺄지 골라 미완료 상태도 만든다.

    ranking 인자는 '이 타깃의 대표 ranking_score' 다. pTM/ipTM 은 AF3 의 정의식
        ranking = 0.8*(ipTM 또는 단량체면 pTM) + 0.2*pTM + 0.5*fd - 100*clash
    을 만족하도록 역산해서 쓴다. 그러지 않으면 af3_collect.py 의 검산 열이
    전건 불일치로 뜨고, 정작 봐야 할 신호(파일 짝 안 맞음)가 묻힌다.
    """
    dirpath.mkdir(parents=True, exist_ok=True)
    atoms = build_model(chains, mean_plddt)
    n_tok = sum(n for _c, n in chains)
    is_complex = len(chains) > 1
    fd = 0.02

    if with_summary:
        # 정의식을 만족시키는 역산.
        #   단량체: ranking = 1.0*pTM + 0.5*fd        -> pTM = ranking - 0.5*fd
        #   복합체: pTM 을 먼저 정하고 ipTM 을 맞춘다
        #           ipTM = (ranking - 0.2*pTM - 0.5*fd) / 0.8
        if is_complex:
            ptm = round(ranking * 0.95, 4)
            iptm = round((ranking - 0.2 * ptm - 0.5 * fd) / 0.8, 4)
        else:
            ptm = round(ranking - 0.5 * fd, 4)
            iptm = None
        summ = {
            "ranking_score": ranking,
            "ptm": ptm,
            "fraction_disordered": fd,
            "has_clash": 0.0,
            "chain_ptm": [ptm for _ in chains],
        }
        if iptm is not None:
            summ["iptm"] = iptm
            summ["chain_iptm"] = [iptm for _ in chains]
            summ["chain_pair_iptm"] = [[iptm] * len(chains) for _ in chains]
        (dirpath / ("%s_summary_confidences.json" % stem)).write_text(
            json.dumps(summ, indent=1), encoding="utf-8")

    if with_conf:
        tok_ch, tok_res = [], []
        for ch, n_res in chains:
            tok_ch += [ch] * n_res
            tok_res += list(range(1, n_res + 1))
        conf = {
            "atom_plddts": [a[4] for a in atoms],
            "atom_chain_ids": [a[0] for a in atoms],
            "token_chain_ids": tok_ch,
            "token_res_ids": tok_res,
            "pae": [[round(1.0 + ((i * 3 + j) % 25), 2) for j in range(n_tok)]
                    for i in range(n_tok)],
        }
        (dirpath / ("%s_confidences.json" % stem)).write_text(
            json.dumps(conf), encoding="utf-8")

    if with_cif:
        write_cif(dirpath / ("%s_model.cif" % stem), atoms, stem)

    if with_rank:
        rows = ["seed,sample,ranking_score"]
        for k in range(n_sample):
            rows.append("1,%d,%.4f" % (k, ranking - 0.004 * k))
        (dirpath / ("%s_ranking_scores.csv" % stem)).write_text(
            "\n".join(rows) + "\n", encoding="utf-8")

    if with_data:
        # 실물 _data.json 은 MSA(A3M) 를 담는다. 깊이 계산 경로를 밟게 최소만 넣는다.
        seqs = []
        for ch, n_res in chains:
            a3m = ">query\n%s\n" % ("A" * n_res)
            for k in range(9):
                a3m += ">hit%d\n%s\n" % (k, "A" * n_res)
            seqs.append({"protein": {"id": ch, "sequence": "A" * n_res,
                                     "unpairedMsa": a3m, "pairedMsa": ""}})
        (dirpath / ("%s_data.json" % stem)).write_text(
            json.dumps({"name": stem, "modelSeeds": [1], "dialect": "alphafold3",
                        "version": 2, "sequences": seqs}), encoding="utf-8")
    return len(atoms)


def touch_tree(path, when):
    """폴더와 그 안 파일의 mtime 을 맞춘다. '최신' 판정 검증에 쓴다."""
    for p in sorted(path.rglob("*"), reverse=True):
        os.utime(p, (when, when))
    os.utime(path, (when, when))


def build(root):
    root = Path(root)
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)

    now = time.time()

    # --- 1. 정상 완료 2건 -------------------------------------------------
    write_target_files(root / "VHH_001", "VHH_001", [("A", 12)], 0.8123)
    # VHH_002 는 복합체. ipTM 경로와 계면 등급 판정을 밟는다.
    write_target_files(root / "VHH_002", "VHH_002", [("A", 10), ("B", 8)], 0.7551)

    # --- 2. AF3 타임스탬프 접미사 폴더. 안의 stem 은 VHH_004 ---------------
    write_target_files(root / "VHH_004_20260820_101010", "VHH_004",
                       [("A", 11)], 0.7702)

    # --- 3. 같은 타깃이 두 폴더에. 최신 쪽 ranking 이 더 높다 --------------
    d_old = root / "VHH_005"
    d_new = root / "VHH_005_20260820_120000"
    write_target_files(d_old, "VHH_005", [("A", 13)], 0.6100, mean_plddt=72.0)
    write_target_files(d_new, "VHH_005", [("A", 13)], 0.8800, mean_plddt=92.0)
    touch_tree(d_old, now - 7200)      # 2시간 전
    touch_tree(d_new, now - 600)       # 10분 전

    # --- 4. 오탐 함정: 이름이 VHH_004_ 로 시작하지만 별개 타깃 -------------
    write_target_files(root / "VHH_004_variantB", "VHH_004_variantB",
                       [("A", 11)], 0.5900)

    # --- 5. 한 폴더에 stem 두 개가 섞인 경우 ------------------------------
    # 정상 완료 stem(VHH_006) 과, 앞선 실행에서 남은 조각(VHH_006_old) 이 함께 있다.
    d6 = root / "VHH_006"
    write_target_files(d6, "VHH_006", [("A", 12)], 0.7400)
    write_target_files(d6, "VHH_006_old", [("A", 12)], 0.4000,
                       with_cif=False, with_rank=False, with_data=False)

    # --- 6. 추론 중 끊긴 폴더. _data.json 만 있다 --------------------------
    write_target_files(root / "VHH_007", "VHH_007", [("A", 12)], 0.0,
                       with_summary=False, with_conf=False, with_cif=False,
                       with_rank=False)

    # --- 7. 폴더명과 stem 이 완전히 다른 경우 ------------------------------
    write_target_files(root / "zzz_folder_9", "VHH_009", [("A", 14)], 0.9012)

    # --- 8. 격리 폴더. 집계에 섞이면 안 된다 -------------------------------
    q = root / ".af3_incomplete" / "VHH_003" / "20260820_090000"
    write_target_files(q, "VHH_003", [("A", 12)], 0.3000,
                       with_cif=False, with_rank=False)
    (q / ".af3_quarantine_marker").write_text("run_af3_batch_improved\n",
                                              encoding="utf-8")

    # --- 9. staging 폴더와 lock 파일 --------------------------------------
    st = root / ".af3_pending_1234"
    st.mkdir()
    (st / ".af3_stage_marker").write_text("run_af3_batch_improved 1234\n",
                                          encoding="utf-8")
    (st / "VHH_010.json").write_text(
        json.dumps({"name": "VHH_010", "modelSeeds": [1],
                    "dialect": "alphafold3", "version": 2,
                    "sequences": [{"protein": {"id": "A", "sequence": "AAAA"}}]}),
        encoding="utf-8")
    (root / ".run_af3_batch.lock").write_text("", encoding="utf-8")

    # --- 10. macOS AppleDouble 사이드카 ----------------------------------
    side = root / "._VHH_099"
    side.mkdir()
    (side / "._VHH_099_summary_confidences.json").write_bytes(
        b"\x00\x05\x16\x07\x00\x02\x00\x00Mac OS X")

    return root


# 검증 스크립트가 import 해서 쓰는 기대값.
#   키 = 집계표에 나와야 하는 타깃명
#   값 = (그 타깃의 대표 결과가 있어야 하는 폴더명, 대표 ranking_score)
EXPECTED = {
    "VHH_001": ("VHH_001", 0.8123),
    "VHH_002": ("VHH_002", 0.7551),
    "VHH_004": ("VHH_004_20260820_101010", 0.7702),
    "VHH_005": ("VHH_005_20260820_120000", 0.8800),   # 최신 폴더 쪽
    "VHH_004_variantB": ("VHH_004_variantB", 0.5900),
    "VHH_006": ("VHH_006", 0.7400),
    "VHH_009": ("zzz_folder_9", 0.9012),
}
# 집계표의 타깃 열에 절대 나오면 안 되는 이름 (폴더명을 그대로 쓰면 나온다)
FORBIDDEN = [
    "VHH_004_20260820_101010",
    "VHH_005_20260820_120000",
    "zzz_folder_9",
    "VHH_003",             # 격리 폴더 안에 있다
    "VHH_007",             # 추론 중 끊김
    "VHH_006_old",         # 같은 폴더에 남은 조각
    "VHH_010",             # staging
    "VHH_099",
]


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="타깃명 정규화 검증용 가짜 AF3 출력 폴더를 만든다")
    ap.add_argument("root", help="만들 폴더 (이미 있으면 지우고 다시 만든다)")
    args = ap.parse_args(argv)
    root = build(args.root)
    print("가짜 출력 폴더를 만들었다: %s" % root)
    for p in sorted(root.iterdir()):
        print("  %-30s %s" % (p.name, "폴더" if p.is_dir() else "파일"))
    print()
    print("기대하는 타깃명 %d개: %s" % (len(EXPECTED), ", ".join(sorted(EXPECTED))))
    print("나오면 안 되는 이름: %s" % ", ".join(FORBIDDEN))
    return 0


if __name__ == "__main__":
    sys.exit(main())
