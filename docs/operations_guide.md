# Kang_AF3 운영 가이드

이 문서는 일상 운영용 요약이다. 설치와 라이선스의 정본은 저장소 루트
[README.md](../README.md)이며 모든 명령은 `~/af3_work/Kang_AF3`에서 실행한다.

## 1. 운영 계약

```text
~/af3_work/alphafold3/       pinned 공식 AF3 source
~/af3_work/Kang_AF3/         이 저장소와 작업 폴더
~/af3_models/af3.bin         Google에서 직접 받은 pinned model
~/public_databases_full/     공식 full DB
~/public_databases_reduced/  선택: 7개 FASTA MSA overlay
```

배치 runner는 Docker 전용이다. native AF3는 공식 `run_alphafold.py`를 직접 사용한다.
Docker는 기본적으로 `docker info`가 비대화형으로 되는 명령을 자동 선택한다. 암호를 묻는
sudo를 자동으로 고르지 않는다. sudo가 필요하면 비대화형 권한을 준비한 뒤
`--docker 'sudo -n docker'`를 명시한다.

## 2. 실행 전 점검

full DB:

```bash
cd ~/af3_work/Kang_AF3
AF3_DB_DIR=~/public_databases_full bash scripts/af3_check.sh
```

reduced-MSA overlay + full template fallback:

```bash
AF3_DB_DIR=~/public_databases_reduced \
AF3_DB_FALLBACK_DIRS=~/public_databases_full \
bash scripts/af3_check.sh
```

진단은 GPU, 비대화형 Docker 접근, image, image 내부 patched HMMER `--seq_limit`,
ordered DB 9항목, pinned `af3.bin` 크기와 SHA-256, Python/fcntl과 디스크를 확인한다. 필수 항목이 하나라도
없으면 종료코드 1이다. matplotlib과 rdkit은 선택 항목이다.

DB만 별도로 확인할 수도 있다.

```bash
python3 scripts/af3_db.py verify --db-dir ~/public_databases_full
python3 scripts/af3_db.py verify \
  --db-dir ~/public_databases_reduced \
  --db-dir ~/public_databases_full
python3 scripts/af3_db.py validate-full-seal \
  --db-dir ~/public_databases_full
```

full 설치기는 기존 deep checksum pass 직후 `af3_full_db_manifest.json`을 원자적으로 만든다.
기존 수동 설치에는 `python3 scripts/af3_db.py seal-full --db-dir
~/public_databases_full`을 한 번 실행한다. 이후 runner는 수백 GB payload를 재hash하지 않고
seal schema와 local inode/mtime/size binding을 검사한다. seal 누락은 기본 실패이며
`--allow-unsealed-db`는 content 동일성을 증명하지 못하는 metadata-only 호환 경로다. 이미 있는
seal이 malformed/stale이면 이 옵션으로 우회할 수 없다.

### 기존 결과를 처음 새 manifest 계약으로 옮길 때

현재 preferred/legacy runner는 입력·sidecar·DB·model·image identity manifest가 없는 과거
결과를 기본으로 신뢰하지 않는다. upgrade 직후에는 `--audit`/`--dry-run`으로 재계산 대상을
먼저 확인하고, 가능한 경우 새 runner로 다시 계산해 canonical manifest를 만든다.

과거 실행 당시의 입력과 환경을 별도 archive/checksum으로 확인했으며 비용 때문에 재계산하지
않겠다는 명시적 결정이 있을 때만 preferred의 `--trust-unverified-results` 또는 legacy의
`--trust-unverified-legacy`를 사용한다. 두 옵션은 과거 결과를 검증하거나 새 manifest로
승격하지 않고, 확인 불가능성을 감수하고 skip하는 compatibility opt-in이다.

## 3. 입력 준비

```bash
python3 scripts/af3_prepare.py \
  --fasta examples/vhh_panel.fasta \
  -o vhh_001_in --dry-run

python3 scripts/af3_prepare.py \
  --fasta examples/vhh_panel.fasta \
  -o vhh_001_in
```

도구는 AF3 output-name 충돌, 빈 이름, chain 구성, 32-bit seed, 서열 alphabet과 길이를
Docker 실행 전에 검사한다. 오류가 하나라도 있으면 기본적으로 아무 파일도 만들지 않는다.

## 4. 권장 배치 실행

먼저 상태만 확인한다.

```bash
python3 scripts/run_af3_batch_improved.py \
  --input-dir vhh_001_in --output-dir vhh_001_out --audit
```

full DB 실행:

```bash
nohup python3 scripts/run_af3_batch_improved.py \
  --input-dir vhh_001_in --output-dir vhh_001_out \
  --db-dir ~/public_databases_full \
  --yes > vhh_001.log 2>&1 &
```

overlay 실행:

```bash
nohup python3 scripts/run_af3_batch_improved.py \
  --input-dir vhh_001_in --output-dir vhh_001_out \
  --db-dir ~/public_databases_reduced \
  --db-dir ~/public_databases_full \
  --yes > vhh_001.log 2>&1 &
```

각 DB root는 별도의 read-only container path에 mount되고 AF3에 같은 순서의 반복
`--db_dir`로 전달된다. image probe에 실패하면 runner는 최신 flag를 추측하지 않고 중단한다.
Docker가 nonzero로 끝났다면 최종 산출물이 우연히 존재해도 전체 실행은 실패다.

## 5. 재개·점검·정리

같은 명령을 다시 실행하면 정식 산출물 3종이 있는 target은 건너뛰고 미완료만 재시도한다.

```bash
python3 scripts/run_af3_batch_improved.py \
  --input-dir vhh_001_in --output-dir vhh_001_out --audit

python3 scripts/run_af3_batch_improved.py \
  --output-dir vhh_001_out --cleanup
```

미완료 결과는 `.af3_incomplete` 아래에 제한 보존한다. `--cleanup`은 유효한 소유 marker가
있는 snapshot만 삭제하며 marker 없는 사용자 파일은 보존한다. 같은 output root의 동시 실행은
nofollow lock으로 거부한다.

preferred runner와 legacy `af3_batch.py`는 무기한 정지를 피하기 위해 기본 7200초
no-progress watchdog을 둔다. legacy의 `infer`/`oneshot`은 stdout/log 또는 output artifact,
병렬 MSA는 각 갈래의 log 또는 해당 target artifact 변화를 진행으로 본다. 단순히 GPU 사용률이
낮다는 이유로 중단하지 않는다. 정상 단계가 2시간 넘게 완전히 무출력이라는 운영 근거가 있으면
`--no-progress-timeout SECONDS`를 늘리고, `0`은 감시를 끈다. 음수는 거부된다.

## 6. 2단계 실행

MSA가 든 `_data.json`만 골라 inference-only로 다시 실행할 수 있다.

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

`af3_stage2.py`는 `_data.json`과 raw input이 한 계획에 섞이면 실행 mode가 하나로 정해지지
않으므로 거부한다. sidecar가 남은 파일도 새 위치에서 깨지기 전에 거부한다.

## 7. 결과 집계와 해석

```bash
python3 scripts/af3_collect.py vhh_001_out \
  --no-msa-depth -o vhh_001_summary.csv
```

- pLDDT는 local confidence다.
- pTM은 **predicted TM-score**이며 정답일 확률이 아니다.
- ipTM은 복합체 interface confidence다.
- `등급`은 후보 정리를 위한 경험적 규칙이지 구조 검증 판정이 아니다.
- ranking score, pTM, ipTM, pLDDT 모두 실험 검증을 대체하지 않는다.

역사적 6개 VHH 단량체 panel에서 DB 조건 간 confidence 변화가 작았다는 사실은 CDR geometry,
복합체 interface, 대규모 top-N 보존 또는 외부 정답 구조 정확도를 검증하지 않는다.

## 8. Viewer와 자료 보안

```bash
python3 scripts/af3_view3d.py vhh_001_out --out-dir viewer --top 20
python3 scripts/af3_visualize.py vhh_001_out -o figures --no-plot
```

HTML은 script-context data를 escape하고 고정 CDN asset에 SRI를 적용한다. `--lib embed` 자동
다운로드와 cache는 pinned SHA-256을 검증한다. `--lib-file`은 사용자가 신뢰한 executable
JavaScript를 명시적으로 제공하는 경로다. AF3 artifact symlink와 출력 경로 탈출은 거부한다.

## 9. 성능 수치 읽기

직접 측정된 핵심은 같은 검증 호스트의 MSA 없는 GPU 추론에서 per-process 31.95초/건과
single-process 5.39초/건의 5.93배 차이다. 4.1시간·40.2시간·2000건 수치는 서로 다른
소규모 실험의 건당 값을 합친 투영이며 end-to-end 완주 측정이 아니다. Docker overhead,
RTX 5090 절대 시간, 긴 입력, 복합체와 현행 overlay 성능은 직접 pilot으로 다시 잰다.

## 10. release 검증

```bash
python3 tests/run_all.py
```

이 명령은 등록 회귀, 명명·파일명 통합, mutation 검증, 통계 self-test, Python AST와 shell
syntax를 모두 실행한다. 실제 Docker/AF3/GPU smoke는 별도의 수동 release gate다.
