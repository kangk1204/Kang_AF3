> 이 문서는 저장소 `docs/reduced_db.md` 다. 한글 제목은 "축소 DB 구성 기록".
> 저장소 최상위 [README.md](../README.md) 의 안내를 먼저 읽고 이 문서로 오는 것을 권한다.
> 문서 안에서 언급하는 그림은 `figures/`, 측정 CSV 는 `results_example/`,
> 스크립트는 `scripts/` 에 있다 (모두 저장소 최상위 기준 경로).

# 축소 DB 구성과 z_value (연구자 조건 대조군)

이 문서는 `~/public_databases` (2.0 GB 축소 DB)를 어떻게 만들었고,
축소 DB로 AF3 데이터 파이프라인을 돌릴 때 무엇을 반드시 넘겨야 하는지 적는다.

## 왜 축소 DB인가
연구자는 공식 전체 630 GB DB가 아니라 소용량(~2 GB) DB를 받아 쓰고 있다(연구자 확인).
같은 조건의 대조군을 만들어야 A/B 비교가 공정하다.

## 어떻게 만들었나
공식 v3.0 DB(`https://storage.googleapis.com/alphafold-databases/v3.0`)의
**실제 앞부분 슬라이스**를 썼다. 무작위 생성 서열은 쓰지 않았다 —
무작위 서열은 MSA가 비어 데이터 파이프라인 시간이 비현실적으로 짧아지고 실측이 왜곡된다.
슬라이스 후 마지막 불완전 FASTA 레코드를 잘라내 파일 정합성을 유지했다.

| 파일 | 크기 | 서열 수 | z_value |
|---|---|---|---|
| uniref90_2022_05.fa | 520.0 MB | 71,974 | 71974 |
| bfd-first_non_consensus_sequences.fasta | 420.0 MB | 3,242,672 | 3242672 |
| uniprot_all_2021_04.fa | 320.0 MB | 633,249 | 633249 |
| mgy_clusters_2022_05.fa | 420.0 MB | 1,886,706 | 1886706 |
| nt_rna_2023_02_23_clust_seq_id_90_cov_80_rep_seq.fasta | 60.0 MB | 27,830 | 56.889873 |
| rnacentral_active_seq_id_90_cov_80_linclust.fasta | 59.9 MB | 67,358 | 57.372623 |
| rfam_14_9_clust_seq_id_90_cov_80_rep_seq.fasta | 228.4 MB | 871,599 | 138.115553 |

- mmCIF 템플릿: **1,239건** (RCSB에서 실제 다운로드 — 나노바디/VHH/단일도메인항체/
  Camelidae/면역글로불린 가변도메인 검색식의 합집합)
- pdb_seqres: 위 mmCIF 엔트리에 대응하는 체인만 **3,531건**
  (916,475 B). 템플릿 검색이 실제로 히트를 낼 수 있게 정합을 맞췄다.

## z_value 를 반드시 넘겨야 하는 이유 (중요)

AF3의 jackhmmer/nhmmer e-value 계산은 **데이터베이스 크기에 의존**한다.
DB를 잘라 놓고 기본 z_value(전체 DB 기준)를 쓰면 e-value가 실제보다 관대해져
MSA에 잘못된 동원체가 섞인다. 축소 DB를 쓸 때는 항상 실제 크기를 넘겨야 한다.

단백질 DB는 **서열 개수**, RNA DB는 **100만 염기 단위**가 z_value 다.

```bash
python run_alphafold.py \
  --input_dir=<입력> --output_dir=<출력> \
  --model_dir=$HOME/af3_models \
  --db_dir=$HOME/public_databases \
  --uniref90_z_value=71974 \
  --small_bfd_z_value=3242672 \
  --uniprot_cluster_annot_z_value=633249 \
  --mgnify_z_value=1886706 \
  --ntrna_z_value=56.889873 \
  --rnacentral_z_value=57.372623 \
  --rfam_z_value=138.115553 \
  --flash_attention_implementation=triton
```

## 한계 (해석 시 반드시 감안)

- 축소 DB의 MSA 깊이는 전체 DB보다 얕다. **구조 품질(pLDDT/pTM)이 전체 DB보다 낮게 나온다.**
  이 세트는 **속도 비교용 대조군**이며 구조 품질 평가용이 아니다.
- 슬라이스는 DB 앞부분이므로 서열 구성이 무작위 표본이 아니다.
  종·계통 편향이 있을 수 있고, 특정 타깃의 MSA 깊이가 전체 DB 대비 얼마나 얕아지는지는
  타깃마다 다르다.
- 전체 DB(`~/public_databases_full`)가 준비되면 동일 입력으로 비교해
  MSA 깊이·품질·시간 차이를 따로 측정하는 것이 좋다.
