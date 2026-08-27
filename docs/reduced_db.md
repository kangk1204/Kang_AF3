# Reduced-MSA overlay contract

이 문서는 현재 지원되는 경량 DB 계약과 과거 benchmark의 경계를 구분한다.
실행 명령은 저장소 최상위 [README.md](../README.md)를 먼저 따른다.

## 현재 지원하는 구성

`~/public_databases_reduced`는 standalone AlphaFold 3 DB가 아니라 7개 MSA FASTA만 담은
ordered overlay다. `pdb_seqres_2022_09_28.fasta`와 `mmcif_files`는 두 번째 full root에서
가져온다.

```text
~/public_databases_reduced/   우선 검색
  bfd-first_non_consensus_sequences.fasta
  mgy_clusters_2022_05.fa
  uniref90_2022_05.fa
  uniprot_all_2021_04.fa
  nt_rna_2023_02_23_clust_seq_id_90_cov_80_rep_seq.fasta
  rfam_14_9_clust_seq_id_90_cov_80_rep_seq.fasta
  rnacentral_active_seq_id_90_cov_80_linclust.fasta
  af3_db_manifest.json

~/public_databases_full/      fallback
  위 FASTA 7개
  pdb_seqres_2022_09_28.fasta
  mmcif_files/
```

AF3는 반복된 `--db_dir`을 순서대로 확인하고 각 논리 파일에 대해 첫 번째 존재하는 경로를
사용한다. Docker runner는 두 root를 별도의 read-only 경로로 마운트한다. 호스트 절대경로
symlink는 컨테이너에서 깨지므로 허용하지 않는다.

## 생성과 검증

```bash
python3 scripts/af3_db.py reduce \
  --source ~/public_databases_full \
  --output ~/public_databases_reduced

python3 scripts/af3_db.py verify \
  --db-dir ~/public_databases_reduced \
  --db-dir ~/public_databases_full
```

fallback full root에는 별도의 `af3_full_db_manifest.json` seal이 필요하다. 공식 설치기는 deep
checksum 검증과 같은 pass에서 seal을 게시한다. 기존 수동 설치는 한 번만
`python3 scripts/af3_db.py seal-full --db-dir ~/public_databases_full`을 실행한다. runtime은
overlay manifest와 full seal을 서로 다른 계약으로 검증하며, full payload를 매번 재hash하지 않는다.

`reduce`는 다음 불변식을 지킨다.

- 7개 source FASTA를 모두 검사한 뒤에만 쓰기를 시작한다.
- 목표 byte를 넘긴 마지막 FASTA record까지 완성하고 다음 header 직전에 멈춘다.
- sibling 임시 디렉터리에 전부 만든 뒤 한 번의 rename으로 publish한다.
- 기존 output은 덮어쓰지 않는다.
- output과 읽은 source prefix의 SHA-256, byte 수, record 수, limit을 manifest에 기록한다.
  수백 GB full source 전체를 다시 읽는 full-file hash는 reduction 경로에서 계산하지 않는다.
- `verify`는 manifest schema와 각 overlay 파일의 현재 byte 수를 매 실행 때 대조한다.
  시작 지연을 피하려고 multi-GB 파일 전체 SHA-256은 자동 재계산하지 않는다. 같은 크기의
  조용한 변조까지 의심하면 manifest의 `output_sha256`과 직접 대조한다.
- template 또는 `pdb_seqres` symlink/copy를 만들지 않는다.

## z-value

pinned AF3 source에서 `*_z_value` 기본값은 `None`이고, 명시값은 `@N` 형식의 **sharded DB**에서
필수다. 이 overlay의 FASTA는 unsharded 단일 파일이므로 HMMER가 실제 DB 크기를 사용하게
명시 z-value를 주지 않는다. 향후 sharding을 지원한다면 manifest에서 계산·검증한 값 없이는
실행을 거부해야 한다.

## 역사적 benchmark와 재현 한계

2026-08 측정은 front-sliced sequence DB와 RCSB에서 별도로 선택한 mmCIF 1,239개,
대응 `pdb_seqres` chain 3,531개를 사용했다. 당시 보고된 sequence 측정값은 다음과 같다.

| 파일 | 기록된 크기 | 기록된 수 |
|---|---:|---:|
| uniref90 | 약 520 MB | 71,974 sequences |
| BFD | 약 420 MB | 3,242,672 sequences |
| UniProt | 약 320 MB | 633,249 sequences |
| MGnify | 약 420 MB | 1,886,706 sequences |
| NT-RNA | 약 60 MB | 27,830 records |
| RNAcentral | 약 60 MB | 67,358 records |
| Rfam | 약 228 MB | 871,599 records |

그러나 정확한 1,239 PDB ID, RCSB query payload, 3,531 chain ID, retrieval manifest와 checksum은
이 저장소에 없다. 따라서 그 template 구성과 신뢰도·시간 결과는 **역사적 관측**이지 현재
저장소만으로 재현 가능한 benchmark가 아니다. 현재 overlay+full fallback은 별도 운영 구성이다.

6개 VHH 단량체에서 confidence 변화가 작았다는 관측은 정답 구조 정확도, CDR geometry,
복합체 ipTM, 대규모 top-N 순위 보존을 검증하지 않는다. 효과가 항상 낮아지거나 항상 같다고
일반화하지 않는다.
