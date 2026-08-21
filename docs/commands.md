# 명령 모음

모든 명령은 `~/af3_work/Kang_AF3`에서 실행한다. 설치·약관·경로 설명은
[README.md](../README.md)가 정본이다.

## Release 검증

```bash
python3 tests/run_all.py
```

## 환경과 DB

```bash
AF3_DB_DIR=~/public_databases_full bash scripts/af3_check.sh

python3 scripts/af3_db.py verify --db-dir ~/public_databases_full

python3 scripts/af3_db.py reduce \
  --source ~/public_databases_full \
  --output ~/public_databases_reduced

python3 scripts/af3_db.py verify \
  --db-dir ~/public_databases_reduced \
  --db-dir ~/public_databases_full
```

## 입력

```bash
python3 scripts/af3_prepare.py \
  --fasta examples/vhh_panel.fasta -o vhh_001_in --dry-run

python3 scripts/af3_prepare.py \
  --fasta examples/vhh_panel.fasta -o vhh_001_in

python3 scripts/af3_prepare.py \
  --csv examples/vhh_panel.csv \
  --partner-fasta examples/antigen.fasta \
  -o vhh_complex_in --dry-run
```

## 권장 batch runner

```bash
# 상태만
python3 scripts/run_af3_batch_improved.py \
  --input-dir vhh_001_in --output-dir vhh_001_out --audit

# full DB
python3 scripts/run_af3_batch_improved.py \
  --input-dir vhh_001_in --output-dir vhh_001_out \
  --db-dir ~/public_databases_full --yes

# reduced-MSA overlay + full template fallback
python3 scripts/run_af3_batch_improved.py \
  --input-dir vhh_001_in --output-dir vhh_001_out \
  --db-dir ~/public_databases_reduced \
  --db-dir ~/public_databases_full --yes

# rootless/docker-group 자동 탐지가 아닌 명시적 명령이 필요한 경우
python3 scripts/run_af3_batch_improved.py \
  --docker 'sudo docker' \
  --input-dir vhh_001_in --output-dir vhh_001_out \
  --db-dir ~/public_databases_full --yes
```

## 2단계

```bash
python3 scripts/af3_collect.py vhh_001_out \
  --top 100 --top-by pTM --top-list top100.txt \
  -o vhh_001_summary.csv

python3 scripts/af3_stage2.py \
  --list top100.txt --from-out vhh_001_out \
  -o vhh_stage2_in

python3 scripts/run_af3_batch_improved.py \
  --mode inference \
  --input-dir vhh_stage2_in --output-dir vhh_stage2_out \
  --model-dir ~/af3_models --yes
```

## 집계·그림·3D

```bash
python3 scripts/af3_collect.py vhh_001_out \
  --no-msa-depth -o vhh_001_summary.csv

python3 scripts/af3_visualize.py vhh_001_out \
  -o figures --no-plot

python3 scripts/af3_view3d.py vhh_001_out \
  --out-dir viewer --top 20

python3 scripts/af3_view3d.py vhh_001_out \
  --out-dir viewer_offline --top 20 --lib embed --engine 3dmol
```

## 통계 검산

```bash
python3 scripts/af3_rankcorr.py --selftest

python3 scripts/af3_rankcorr.py \
  --ref pilot_full.csv --test pilot_screen.csv \
  --top-n 10,20,50 \
  -o pilot_rankcorr.csv --pairs-out pilot_pairs.csv
```

기준·비교 target set이 다르면 기본적으로 실패한다. 의도적으로 공통 교집합만 분석할 때만
`--allow-intersection`을 명시한다. top-N 경계가 동점이면 겹침률을 판정불가로 보고한다.
