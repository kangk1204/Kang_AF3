# Kang_AF3 파이프라인 초정밀 감사 보고서

- 감사일: 2026-08-27 (Asia/Seoul)
- 감사 기준: `4b1f533b5d05c4fa051dc7d5096ab9d311dc1a17`
- 브랜치: `main` (`origin/main`과 일치, 감사 시작 시 clean)
- 범위: 저장소가 소유하는 93개 tracked file, 12개 Python/셸 실행 도구, 테스트·CI, 12개 CSV, 13개 PNG, HTML viewer, 문서와 과학적 주장
- 비소유 경계: `../af3_work/alphafold3`의 pinned upstream AF3, 모델 가중치, full/reduced DB, host Docker/JAX/GPU
- 모드: read-only source audit. 이 보고서 외의 소스 코드는 수정하지 않았다.
- 적용 렌즈: `$coding-lord` 53개 expert skill path 전수, `$biostatinfo-expert`, `$donald-expert-agent`, `$audit-analysis-output-consistency`

## 1. 최종 판정

**REQUEST CHANGES — 운영 release 및 과학적 screening-validity claim을 잠근다.**

Kang_AF3는 단순한 실험 스크립트 수준을 넘어섰다. 입력 사전검증, 안전한 이름 규칙,
원자적 JSON 쓰기, incomplete quarantine, 동일 output lock, Docker flag capability probe,
CSV formula escaping, HTML/CSP/SRI, 통계 self-test, mutation testing이 강하다. 현재 CI도
Python 3.9/3.12/3.14 전부 green이다.

그러나 다음 핵심 불변식은 아직 성립하지 않는다.

1. 바뀐 입력을 재계산한 새 결과가 canonical 결과가 아니라 timestamp sibling에 생겨도,
   러너가 옛 canonical 구조에 새 provenance를 기록하고 성공 처리한다.
2. provenance가 실제 계산 입력(sidecar, DB/model contents, image digest)을 식별하지 못하고,
   쓰기 자체도 transaction이 아니다.
3. legacy MSA cache와 결과 skip은 입력 identity가 없으며, 실패한 MSA가 정상 cache를
   덮을 수 있다.
4. output/work/GPU 자원의 lock model이 통합되어 있지 않다.
5. stage-2 exact-N selection이 동점에서 target 이름으로 실험 후보를 결정한다.
6. 현재 데이터는 코드 경로와 고정 host 성능을 검증하지만, binder recovery, affinity,
   native interface accuracy 또는 screening false-negative rate를 검증하지 않는다.
7. AF3-derived artifact의 Output Terms 전달과 artifact lineage가 불완전하다.

따라서 현재 근거가 지지하는 표현은 다음과 같다.

> **Kang_AF3는 방어적으로 구현된 AF3 batch orchestration 및 exploratory prioritization
> workflow다. 현재 상태는 biologically validated screening pipeline, affinity predictor,
> assay-validated hit selector, SOTA method가 아니다.**

## 2. 정확한 계산 모델과 필수 불변식

### 2.1 계산 객체

한 작업을 다음으로 정의한다.

- `J`: AF3 JSON의 의미적 내용과 모든 sidecar bytes
- `E`: mode, DB fingerprint, model checksum, resolved image digest/revision, producer/schema version
- `R`: canonical result directory의 필수 산출물
- `P`: `R`을 만든 `H(J, E)` provenance record
- `C`: MSA/template cache와 그 data-pipeline identity
- `L`: output, work, GPU 자원에 대한 lease/lock

### 2.2 재사용 불변식

결과를 완료로 재사용할 필요충분조건은 다음이어야 한다.

```text
reusable(R, J, E)
  := complete_required_artifacts(R)
     AND atomically_committed(P in R)
     AND P == H(canonical(J), sidecars(J), E)
```

현재 구현은 `complete_required_artifacts(R)`와 일부 raw JSON/path 문자열만 비교한다.
timestamp sibling의 새 결과를 canonical `R`로 승격하는 단계도 없다.

### 2.3 cache 불변식

```text
reusable(C) := cache_manifest == H(input, sidecars, DB/data-pipeline config)
               AND producer_exit == 0
               AND cache_payload_valid
```

파일 크기 또는 non-empty는 identity나 validity의 증거가 아니다.

### 2.4 selection 불변식

score의 N번째 경계가 동점이면 top-N 집합은 유일하지 않다. 허용되는 정책은 다음 중
명시된 하나다.

- 경계 동점 전부 포함
- 오류로 중단하고 `--tie-policy` 요구
- 사전 지정한 과학적 secondary metric 사용

target 문자열의 사전순은 과학적 선택 기준이 아니다.

### 2.5 GPU 불변식

GPU check는 reservation이 아니다. 선택한 device의 readiness를 확인하고 같은 device를
Docker에 bind하며, container 종료까지 동일 lease를 보유해야 한다.

## 3. 차단급 코드·데이터 무결성 문제

### H-01. 변경 입력 재계산이 옛 canonical 구조에 귀속된다

- 위치: `scripts/run_af3_batch_improved.py:566-584`, `:1204-1207`, `:1264-1270`, `:1913-1921`
- 유형: state-transition/data-provenance failure
- 심각도: **HIGH / release blocker**

provenance mismatch로 작업을 pending에 넣어도, 기존 canonical directory가 이미 완전하면
`quarantine_incomplete()`가 그대로 반환한다. AF3는 non-empty output을 덮지 않고
`<name>_<timestamp>` sibling에 새 결과를 쓴다. 이후 러너는 canonical old directory만
검사하고 새 입력 hash를 그 old directory에 기록한다.

E2E 재현 결과:

```text
second_rc=0
direct_result_is_old=True
timestamp_result_is_changed=True
old_result_claims_changed_hash=True
dirs=['vhh_a', 'vhh_a_<timestamp>']
```

기존 회귀 `test_changed_input_is_not_mistaken_for_a_finished_result`는 Docker가 호출됐는지와
종료코드만 확인해, canonical `_data.json`/구조와 provenance가 같은 계산을 가리키는지
검증하지 않는다.

수용 기준:

- mismatch canonical을 공통 lock 안에서 보존 이동한다.
- current run은 빈 unique destination에서 수행한다.
- current-run artifact와 input identity를 검증한 뒤 canonical로 atomic publish한다.
- 테스트가 canonical `_data.json` sequence/hash와 provenance를 직접 비교한다.

### H-02. provenance가 effective input을 식별하지 않는다

- 위치: `scripts/run_af3_batch_improved.py:291-348`
- 심각도: **HIGH**

현재 record는 raw JSON hash, mode, DB/model path, mutable image tag를 기록한다. 다음은 빠져 있다.

- sidecar file contents
- DB content/manifest fingerprint
- model checksum
- resolved Docker image ID/digest 및 AF3 revision label
- producer/schema version의 실제 비교

`provenance_version`은 기록되지만 mismatch loop에서 비교하지 않으므로 stored version `999`도
통과한다. 같은 path/tag의 내용이 바뀌어도 현재 record는 같을 수 있다. raw JSON byte hash는
반대로 whitespace/key-order만 달라져도 불필요한 재계산을 만든다.

850 GB를 매 실행 hash할 필요는 없다. 설치/검증 단계가 만든 작은 signed/hashed manifest와
image/model digest를 record에 넣어야 한다.

### H-03. legacy MSA/result freshness가 여전히 identity-free다

- 위치: `scripts/af3_batch.py:914-937`, `:1005-1016`, `:1378-1385`
- 심각도: **HIGH**

크기 기반 overwrite 문제는 제거됐지만 다음 문제는 남았다.

- `msa_store_is_complete()`는 non-empty만 본다.
- changed sequence/DB/image도 name이 같으면 cache/final output을 skip한다.
- MSA shard return code와 무관하게 `collect_msa_outputs()`가 실행된다.
- collector는 known-good cache를 direct/non-atomic overwrite한다.

partial/failed shard가 old valid cache를 덮는 반례가 재현됐다. legacy compatibility를 유지하려면
동일 manifest contract를 쓰거나, 기본은 unverifiable cache를 pending으로 하고 명시적
`--trust-unverified-legacy`만 제공해야 한다.

### H-04. lock/lease 자원 모델이 불완전하다

- 위치: `scripts/af3_batch.py:1270-1286`, `scripts/run_af3_batch_improved.py:1299-1319`, `:1801-1873`
- 심각도: **HIGH**

- legacy는 `.af3_batch.lock`, preferred는 `.run_af3_batch.lock`을 사용하므로 같은 output에
  두 러너가 동시에 lock을 얻는다.
- legacy가 output만 잠그므로 서로 다른 output이 같은 explicit work directory의
  `msa_raw`, `msa_store`, stage, state를 동시에 변경할 수 있다.
- GPU 상태 확인은 output lock 이전의 check-then-act다. 서로 다른 output을 쓰는 두 실행은
  둘 다 idle GPU를 보고 동시에 시작할 수 있다.

공통 lock protocol과 canonical lock acquisition order가 필요하다. GPU는 per-device lease를
획득한 후 재확인하고 container teardown까지 유지해야 한다.

### H-05. stage-2 boundary tie가 target 이름으로 후보를 결정한다

- 위치: `scripts/af3_rankcorr.py:537-547`, `scripts/af3_stage2.py:307-320`, `scripts/af3_collect.py:935-952`
- 심각도: **HIGH / scientific selection integrity**

rank-correlation 도구는 top-N 경계 동점을 비식별로 처리한다. 그러나 stage-2는 같은 score를
target 이름으로 정렬하고 exact N을 자른다. `z=0.9`, `a=0.9`, `top=1`에서 `a`가 선택되는
것을 재현했다. CSV score가 반올림되어 있어 동점은 희귀 edge case가 아니다.

### H-06. legacy `infer`가 숨겨진 경량 설정을 강제한다

- 위치: `scripts/af3run.sh:9-18`, `:132-143`, `README.md:1108-1115`
- 심각도: **HIGH / user-visible scientific semantics**

문서상 `infer`는 stored MSA로 inference만 실행하는 mode다. 구현은 말하지 않고
`--diffusion-samples 1 --recycles 3`을 추가한다. default/full 5/10 inference-only를 표현하는
wrapper mode도 없다. 사용자는 full inference를 기대하고 screening-fidelity 결과를 얻을 수 있다.

### H-07. stage-2가 파일 이름만 확인하고 내부 target identity를 믿는다

- 위치: `scripts/af3_stage2.py:328-357`, `:589-656`
- 심각도: **HIGH / data-association integrity**

이전의 “폴더 안 첫 `_data.json` 사용” 문제는 filename/prefix filter로 좁혀졌지만, 선택한
JSON의 내부 `name`은 requested target과 비교하지 않는다. 다음 반례가 current HEAD에서
재현됐다.

```text
requested target: vhh_a
file: out/vhh_a/vhh_a_data.json
internal JSON name: vhh_b
picked: vhh_a_data.json
built output_name: vhh_b
error: None
```

즉 stale/mislabeled file의 MSA/template가 다른 target에 연결될 수 있다. hint path에서는
`tdir.name`도 wanted set에 포함되어 경계가 더 느슨하다. `nonempty()`도 symlink source를
거부하지 않는다(`:193-197`). candidate JSON을 안전하게 읽고 normalized internal `name`이
requested target과 정확히 일치하는지 확인하며, 0개 또는 복수 일치면 중단해야 한다.

## 4. 과학·생물통계 validity gate

### S-01. 검증 표본 선택이 overlay 결과에 조건부다

- 위치: `README.md:720-738`, `docs/researcher_guide.md:147-154`
- 판정: **SCIENTIFIC VALIDITY FAIL**

overlay로 본 nanobody complex 10건 중 full DB rerun은 boundary-near 3건에 집중됐고, 여기에
서로 이질적인 예제 4건을 합쳐 grade가 뒤집히지 않았고 filtering에 유용하다고 해석한다.
검증 여부가 overlay 결과에 의존하므로 이 표본으로 false-negative rate, sensitivity,
grade stability, enrichment 또는 transportability를 추정할 수 없다.

### S-02. 구현 endpoint와 biological endpoint가 다르다

HEAD는 `README.md:1331-1349`에서 ipTM이 binding/affinity 증거가 아니라고 올바르게 명시한다.
그러나 `README.md:1520-1527`의 discard/select 절차는 experimental hit enrichment 또는
native interface accuracy로 검증되지 않았다.

현재 estimand는 대략 다음이다.

> 한 AF3 confidence configuration에서 높게 순위된 target이 다른 AF3 confidence
> configuration에서도 높게 순위되는가.

이는 binder recovery, Kd/IC50, epitope truth, mutation effect 또는 experimental hit rate가 아니다.

### S-03. metric 선택과 cutoff 추정이 같은 pilot에서 이뤄진다

- 위치: `docs/two_stage_notes.md:287-342`

약 40-target pilot에서 여러 metric을 비교하고, 가장 잘 보존된 metric을 고르고, 같은 data로
safety multiplier까지 정한다. 이는 selection optimism/double dipping이다. metric은 사전 지정하거나
nested/held-out validation이 필요하다.

### S-04. rank-preservation uncertainty가 없다

- 위치: `scripts/af3_rankcorr.py:474-583`, `:607-615`

rho, tau-b, top-N overlap, safety factor는 point estimate뿐이다. safety factor는 최대 rank라는
불안정한 order statistic이다. 40-target pilot의 top fraction을 2,000-target top-100으로 단순
이식할 수 없다. `rho/tau < 약 0.8` 규칙도 miss cost, N, dataset size, uncertainty와 연결되지 않는다.

### S-05. `--all-metrics`가 서로 다른 analysis population을 비교할 수 있다

- 위치: `scripts/af3_rankcorr.py:476-488`

metric마다 missing row를 따로 제거한다. ipTM은 monomer에서 구조적으로 비어 있으므로 pTM,
ipTM, pLDDT, ranking score의 n과 target set이 달라질 수 있다. 같은 pilot에서 최적 metric을
고르는 경우 apples-to-apples 비교가 아니다.

### S-06. `ranking산포`는 reproducibility 또는 correctness uncertainty가 아니다

- 위치: `scripts/af3_collect.py:547-558`, `:612-617`, `:659-661`

통계량은 같은 input/model의 diffusion sample max-min range다. parameter uncertainty,
training-data shift, MSA uncertainty, run-to-run variability 또는 native correctness calibration을
포함하지 않는다. range는 sample count가 커질수록 커지는 성질도 있다.

이름을 `within-run diffusion-sample range`로 제한하고 `>=0.05`는 calibration되지 않은 heuristic임을
명시해야 한다.

### S-07. atom-weighted global pLDDT grade는 composition-dependent다

- 위치: `scripts/af3_collect.py:522-545`, `README.md:1404-1410`

residue atom 수와 framework 크기에 따라 가중치가 달라진다. global atom mean은 CDR/interface
accuracy의 증거가 아니다. equal-residue, per-chain, CDR, interface summary를 분리해야 한다.

### 권장 validation design

`Question`: 실제 목표를 다음 중 하나로 사전 고정한다.

- light vs full AF3 top-K preservation
- native interface/structure accuracy
- experimental binder recovery/enrichment
- affinity/mutation effect prediction

`Data structure`: target이 분석 단위다. diffusion samples는 target 내 stochastic measurement,
benchmark run은 technical replicate다.

`Primary method`:

1. score를 보기 전에 representative common panel을 고정한다.
2. 모든 panel target을 두 configuration 모두에서 계산한다.
3. monomer/complex/ligand, length, family, known difficulty를 층화한다.
4. primary metric과 tie policy를 사전 지정한다.
5. failed/missing/unevaluable target을 intention-to-screen denominator에 유지한다.

`Robustness checks`:

- target-level paired bootstrap CI for rho/tau/top-K recall
- seed/config sensitivity
- held-out validation panel
- experimental labels 또는 native structure metric과의 external validation

`Multiplicity and uncertainty`: metric/threshold를 탐색했다면 final validation set을 분리한다.
top-K recall lower confidence bound와 허용 miss rate를 의사결정 기준으로 사용한다.

`Interpretation limits`: 독립 assay/native truth 전에는 exploratory prioritization으로 제한한다.

## 5. 보안·filesystem·운영 문제

### M-01. provenance writer가 symlink를 따라 외부 파일을 덮는다

- 위치: `scripts/run_af3_batch_improved.py:311-319`
- 심각도: **MEDIUM security / HIGH provenance integrity**
- 재현: 외부 temp victim에 symlink를 만들고 `write_provenance()` 호출 후 victim이 JSON으로 변경됨

writer는 non-atomic이고 실패를 경고만 한 뒤 성공을 유지한다. output lock도 provenance 쓰기 전에
해제된다(`:1905` 대 `:1913-1921`). fresh result의 manifest 실패는 fatal이어야 한다.

### M-02. stage-2 manifest는 hardlink truncate와 FIFO hang을 허용한다

- 위치: `scripts/af3_stage2.py:86-96`

`O_NOFOLLOW`는 symlink만 막는다.

```text
hardlink victim bytes: 5 -> 0
FIFO probe: timeout rc=124
```

same-directory temporary regular file, `fstat`, `st_nlink == 1`, fsync, atomic replace가 필요하다.
또한 `O_TRUNC`가 첫 row를 쓰기 전에 기존 manifest를 0 byte로 만들므로, open 직후 예외를
발생시킨 crash simulation에서도 old manifest가 소실됐다. 이는 공격 입력이 없어도 생기는
publication atomicity 결함이다.

### M-03. `AF3_DOCKER`가 fragile scalar command API로 남아 있다

- 위치: `scripts/af3_check.sh:106-108`, `:142-180`, `:207-209`

`$DOCKER`를 unquoted scalar로 실행하여 word splitting과 pathname expansion이 발생한다.
parameter expansion 결과가 `;`, quote 또는 `$()`로 다시 shell parsing되는 것은 아니므로 이를
독립 command-injection 취약점으로 과장하면 안 된다. 또한 사용자가 `AF3_DOCKER`로 executable을
선택하는 기능 자체는 의도된 interface다. 다만 공백·glob·inherited environment에 취약한 command
representation이며, installer의 docker-group 진단 경계에서도 변수를 정리하지 않는다.
preferred Python runner처럼 명시적 tuple/array 또는 제한된 command form을 써야 한다.

### M-04. wrapper는 로그 쓰기 실패를 성공으로 보고한다

- 위치: `scripts/af3run.sh:90-91`, `:155-168`

`mkdir`/`tee` 실패를 검사하지 않고 producer의 `PIPESTATUS[0]`만 반환한다. `/proc`을 work symlink로
준 temp reproduction에서 log 생성이 실패했지만 `wrapper_rc=0`이었다.

### M-05. installer 최종 DB deep verification이 inode cache로 생략된다

- 위치: `scripts/install_af3_ubuntu.sh:325-355`, `:717-735`

`DB_VALIDATED_ID`는 device:inode만 저장한다. validated staging directory를 rename하면 inode가
같아 final `db_valid()`가 content recheck 없이 성공한다. 최종 publish 후 cache를 비우거나 final
검증을 강제로 수행해야 한다.

### M-06. 수동 model 다운로드 문서가 검증 전 기존 model을 덮는다

- 위치: `README.md:657-665`

`zstd -d -f ... -o af3.bin`이 새 SHA 검증 전에 known-good model을 overwrite한다. 임시 파일에
해제하고 checksum 검증 후 no-clobber/atomic publish해야 한다.

### M-07. JAX GPU predicate가 free-form text에 의존한다

- 위치: `scripts/af3_check.sh:167-175`

stdout/stderr 전체에서 단어 `gpu`가 하나라도 있으면 통과한다. 다음 반례가 accepted로 재현됐다.

```text
warning: gpu plugin unavailable
cpu
```

container Python 자체가 backend/device platform을 assert하고 exit status로 결과를 전달해야 한다.

### M-08. container probe에 timeout/name/cleanup contract가 없다

- 위치: `scripts/af3_check.sh:155-180`

GPU/JAX/help probes가 무기한 block할 수 있고 deterministic container name이 없다. timeout 또는 signal
후 orphan이 남을 수 있다.

### M-09. GPU readiness와 실제 device가 연결되지 않는다

- 위치: `scripts/run_af3_batch_improved.py:730-800`, `:1107`

- free memory가 가장 큰 card를 선택했다고 가정하지만 Docker는 `--gpus all`을 노출한다.
- 12,288 MiB 중 7,000 MiB free도 현재 50% floor를 통과하지만 pinned image의 95% preallocation
  요구와 맞지 않는다 (`gpu_busy_reason([], 7000, 12288) -> None`).
- 다른 output root의 두 실행 사이 GPU lease가 없다.

device ID/UUID 선택, 그 device의 readiness, `--gpus device=<id>`, provenance 기록이 하나의 계약이어야 한다.

### M-10. 대형 batch에 no-progress watchdog가 없다

- 위치: `scripts/run_af3_batch_improved.py:1149`, `:1258`

한 2,000-target `--input_dir` container가 멈추면 이후 target 전체와 output lock이 무기한 막힌다.
artifact/event growth 기반 watchdog와 bounded shard/checkpoint가 필요하다.

## 6. Output Terms, citation, artifact lineage

### C-01. Output Terms 전달이 불완전하다

- 위치: `OUTPUT_NOTICE.md`, `figures/OUTPUT_NOTICE.txt`, `results_example/OUTPUT_NOTICE.txt`,
  `examples/view3d_example.html`
- 심각도: **HIGH compliance gate**

pinned `../af3_work/alphafold3/OUTPUT_TERMS_OF_USE.md:126-157`은 conspicuous terms/modification
notice, 조건에 따른 terms copy와 exact legally-binding notice, AF3 paper citation을 요구한다.

현재 repository에는 다음이 없다.

- tracked `OUTPUT_TERMS_OF_USE.md` copy
- 요구된 exact “Legally Binding Terms of Use” notice text
- folder sidecar의 Abramson citation
- `examples/` sidecar 또는 viewer 내부 notice
- 새로 생성되는 figure/viewer에 notice를 전달하는 generator logic

`OUTPUT_NOTICE.md:38-39`의 “이 파일 또는 링크+수정사항이면 된다”는 자체 결론은 pinned terms의
조건을 모두 구현하지 않는다. 이는 법률 자문이 아니라 tracked distribution과 pinned text의
기술적 차이이며, 기관 법무/라이선스 검토가 필요하다.

### C-02. modification inventory가 실제 artifact 종류를 정확히 표현하지 않는다

`OUTPUT_NOTICE.md:28`은 `results_example/*.csv`가 summary confidence JSON을 집계하고 grade/warning을
추가했다고 설명한다. 그러나 `ab_benchmark.csv`, `msa_threads.csv`, `projection_2000.csv`,
`sort_effect.csv`, `stage2_timing.csv` 등은 timing/projection record다. 여러 tracked figure와 screenshot도
notice 표의 세 범주로 정확히 설명되지 않는다.

추가로 `OUTPUT_NOTICE.md:28`의 grade 기준 링크는 `README 8-3`을 가리키지만 4b1f533에서
grade 절은 8-4로 renumber됐다. `OUTPUT_NOTICE.md:33-34`는 `pLDDT평균`을 AF3 JSON과 그대로
일치하는 original metric처럼 설명하지만, 이는 `atom_plddts`에서 repository가 계산한
atom-weighted mean이다(`scripts/af3_collect.py:522-545`). modification disclosure가 derived
metric을 original scalar와 구분해야 한다.

### C-03. artifact lineage와 deterministic builder가 없다

13개 PNG 중 repository code로 직접 재생성 경로가 확인되는 것은 일부 confidence plot뿐이다.
benchmark PNG는 tracked builder가 없고, raw AF3 confidence/model outputs는 의도적으로 absent다.
artifact별 input hash, generator command/version, AF3 revision, terms status manifest가 없다.

따라서 clean clone에서 figure byte-level regeneration 또는 source-to-annotation audit을 할 수 없다.

### C-04. committed figure에 알려진 unit mismatch가 남아 있다

- artifact: `figures/baseline_gpu5070ti.png` panel c
- 위치: `README.md:491-495`, `docs/readme_rewrite_notes.md:167`

axis는 MiB인데 annotation은 binary/decimal conversion이 섞인 `GB`다. README가 경고하지만 rendered
artifact 자체는 잘못된 단위를 유지한다. 한 unit(MiB 또는 GiB)으로 재생성해야 한다.

### C-05. 기본 수치 교차검산은 통과했다

12개 tracked CSV에 대해 다음을 재계산했다.

- per-target wall time
- 2,000-target hour/day projection
- DB comparison delta/ratio
- `af3_summary.csv` 대 `db_confidence_comparison.csv` 값
- non-finite numeric sentinel

결과: `numeric_consistency_issues = 0`. 값 수준 기본 산술은 일관된다.

## 7. reporting/문서/UX 문제

### M-11. multichain missing-iPTM의 tri-state가 완전하지 않다

index footer는 고쳐졌지만 `af3_visualize.py:1377`이 `chain_ptm` 누락을 `n_chain=0`으로 만들고,
`scatter_metric()`가 `(n_chain or 1)`로 monomer 취급할 수 있다. all-multichain-missing-iPTM이면 summary
right panel이 조용히 사라지고 omitted count/reason이 없다. 상태를 known-single / known-multi / unknown으로
표현해야 한다.

개별 viewer도 `scripts/af3_view3d.py:977-995`에서 null ipTM을 monomer case로 설명하고,
`chains=['A','B']`, `iptm=None`인 record에서 interface가 미평가됐다는 경고 없이 ipTM row를
생략한다. index footer만 고친 것으로는 충분하지 않다. scatter, individual viewer, index가 같은
tri-state contract와 omitted count/reason을 사용해야 한다.

### L-01. README orphan sentence

- `README.md:195-198`은 `않는다.`로 시작해 overlay warning의 앞 문장이 사라졌다.

### L-02. 존재하지 않는 artifact path

- `docs/benchmark_report.md:417`은 `af3_결과요약.csv`를 가리키지만 tracked canonical은
  `results_example/af3_summary.csv`다.

### L-03. inline-code path는 Markdown link test가 검사하지 않는다

현재 link test가 L-02를 통과한다. backtick path 중 repository path 후보도 검사해야 한다.

## 8. 테스트·CI 감사

### 긍정적 증거

- Current GitHub Actions run `32971658448`, HEAD `4b1f533`: Python 3.9/3.12/3.14 전부 success
- 통제된 local release verification:
  - registered regressions: 131/131
  - naming integration: 125/125
  - filename/output compatibility: 77/77
  - mutation injection: 56/56
  - rank-correlation self-test: pass
  - release verification: pass
- `compileall`: pass
- production ShellCheck: pass
- Bash syntax: pass
- `git diff --check`, `git fsck`: pass
- repository/history secret scan: 발견 없음
- 최대 Git blob 약 1.1 MB; model/DB blob 없음

### T-01. release suite가 host Docker에 비밀리에 의존한다

`tests/run_tests.py:11`은 Docker를 항상 가로챈다고 쓰지만 일부 environment-check test는 host PATH와
`AF3_DOCKER`를 sanitize하지 않는다 (`tests/test_workflow_safety.py:1032`, `:1114`). Docker가 설치된 이
host에서는 실제 image/GPU probe로 들어가 300초 timeout이 재현됐다.

Docker command를 PATH에서 명시적으로 차단한 통제 환경에서는 전체 suite가 통과했다. 즉 CI green은
코드의 장점이지만 test hermeticity의 증거는 아니다.

### T-02. 새 JAX tests가 무관한 2.293 GB hashing을 수행한다

`tests/harness.py:91`의 sparse 1,146,811,260-byte model을 `af3_check.sh:345`가 매 complete check마다
SHA-256한다. JAX CPU/fail 두 case가 이를 두 번 반복한다. targeted JAX test가 약 73초 걸린다는 독립
측정이 있었다. GPU behavior test에서는 checksum을 stub하고, 실제 hash integration은 하나만 유지해야 한다.

### T-03. test discovery가 hard-coded다

`tests/run_tests.py:30-44`의 registered module 목록과 `tests/run_all.py:39-44`의 standalone suite
목록을 모두 수동 관리한다. `test_naming.py`와 `test_filename_lang.py`는 의도적으로
`TEST_MODULES` 밖에 있고 `run_all.py`가 직접 실행한다. “현재 누락 없음”은 모든 `test_*.py`가
registry 안이라는 뜻이 아니라, 현재 release entry point에서 의도하지 않게 빠진 suite가 발견되지
않았다는 뜻이다. future test가 조용히 빠지지 않도록 completeness gate가 필요하다.

### T-04. coverage/type/dependency audit가 release gate에 없다

- line/branch coverage 없음
- Python type checker 없음
- `ruff`, `pytest`, `pip-audit`는 이 host에 설치되지 않음
- mutation 56건은 강하지만 curated string mutation이며 repository-wide mutation score가 아님
- `requirements.txt`의 `matplotlib>=3.5`는 upper pin/hash/lock이 없음
- claimed Python 3.8 minimum은 CI matrix(3.9/3.12/3.14)로 직접 검증되지 않음

### T-05. stage-2/runner validator equivalence test가 proof가 아니다

`tests/test_state.py:1107-1110`은 두 함수 객체가 같거나 docstring이 같으면 같은 구현으로 본다.
body가 drift해도 같은 docstring이면 통과한다. current normalized AST body는 실제로 같으므로 현행
schema mismatch는 아니지만, test가 주장하는 장기 동등성 증명은 성립하지 않는다. 불필요하게 복사된
`KNOWN_FLAGS`, `INFRASTRUCTURE_EXIT_CODES`, `CONTAINER_PREFIX`도 stage-2에 남아 있다.

## 9. 알고리즘·복잡도 감사

### 승인된 부분

- Spearman average ranks와 Kendall tau-b tie accounting은 stated formula와 일치한다.
- non-finite, duplicate targets, unequal panels, top-N boundary ties를 rank-analysis 경로에서 거부한다.
- `_betainc_half` p-value implementation은 독립 `mpmath` reference grid와 약 `1.5e-12` 이내로 일치했다.
- staging ancestor collision check는 pairwise scan 없이 path depth만 추적한다.

### 성능 위험

- `af3_batch.py:1378-1385`가 target마다 모든 output dir를 재탐색하여 약 `O(T*D*F)`, 일반적으로
  `O(N^2 F)`다.
- `collect_msa_outputs()`가 historical `msa_raw`를 반복 recursive scan/copy한다.
- Kendall `O(n^2)`는 수십~수백 pilot에는 적합하다. 실제 n이 커져 dominant가 되기 전에는 복잡한
  inversion-count 구현으로 바꾸지 않는 것이 맞다.

## 10. 이전 감사 문제의 현재 상태

| 이전 문제 | 현재 판정 |
|---|---|
| `AF3_MSA_WORKERS` arithmetic command execution | **Fixed**: positive integer validation 추가 |
| wrapper `NAME` path traversal | **Fixed** |
| empty `--ligand-ccd ,`가 ligand 삭제 | **Fixed** |
| stage-2가 다른 target `_data.json` 선택 | **Partially fixed**: filename/prefix만 확인하고 내부 `name` 불일치 허용 |
| stage-2 full schema precheck 부재 | **Fixed but duplicated validator/test weak** |
| host/container/JAX GPU 기본 failure check | **Partially fixed**: mixed-output predicate/timeout 문제 남음 |
| missing iPTM을 index에서 monomer라고 단정 | **Partially fixed**: tri-state/omitted reporting 남음 |
| changed input result reuse | **Not fixed under AF3 timestamp semantics** |
| legacy MSA freshness | **Not fixed** |
| legacy output lock | **Partially fixed**: work/cross-runner/GPU lock 남음 |
| stage-2 manifest symlink overwrite | **Partially fixed**: hardlink/FIFO/non-atomic 문제 남음 |
| Output Terms notice 없음 | **Partially fixed**: copy/exact notice/citation/generator propagation 남음 |
| fixed 2,000 MiB GPU floor | **Partially fixed**: 50%가 95% model과 불일치, multi-GPU unbound |
| AF3_DOCKER scalar execution | **Unresolved** |
| wrapper tee/log false success | **Unresolved** |
| installer inode-only final validation cache | **Unresolved** |
| manual model pre-verification overwrite | **Unresolved** |
| artifact lineage/builders | **Unresolved** |
| figure mixed units / stale docs | **Unresolved** |

## 11. 우선순위별 개선 계약

### P0 — 결과 오귀속과 cache corruption을 먼저 막는다

1. canonical result state machine을 재설계한다.
2. current-run unique destination → validate → manifest → atomic canonical publish 순서를 강제한다.
3. JSON+sidecars+small verified DB/model/image manifests를 identity로 사용한다.
4. legacy result/cache는 기본 distrust, explicit compatibility opt-in만 허용한다.
5. cache publish는 producer rc=0과 payload validation 후 atomic 수행한다.

### P1 — 자원 소유권을 하나의 lock protocol로 만든다

1. preferred/legacy 공통 output lock name과 format
2. output/work lock을 canonical order로 획득
3. per-GPU device lease → recheck → device-bound Docker → teardown까지 hold
4. no-progress watchdog와 bounded shard/checkpoint

### P2 — selection과 scientific validation contract를 고친다

1. tie policy를 모든 top-N tool에 통일
2. `infer`와 `infer-screen` semantics 분리
3. unbiased common panel, prespecified metric, held-out validation
4. top-K recall lower CI와 실패 포함 denominator
5. assay/native truth 전에는 exploratory prioritization claim만 허용

### P3 — local file publication과 diagnostics를 harden한다

1. provenance/manifest 공통 atomic writer
2. regular/single-link/fstat/fsync/replace contract
3. JAX predicate를 Python exit status로 단순화
4. Docker command array와 bounded named probes
5. wrapper가 mkdir/tee 실패를 sticky nonzero로 반영

### P4 — artifact/terms/report package를 재구축한다

1. pinned Output Terms copy, exact notice, citation, examples sidecar
2. generator가 새 artifact에 notice를 자동 전달
3. artifact manifest: source hash, command, version, AF3 revision, terms status
4. deterministic benchmark figure builder
5. mixed-unit figure와 stale documentation 재생성/수정

## 12. Coding Lord expert ledger

`$S=/home/keunsoo/.codex/skills`. 동일 hash의 current/backup도 별도 path pass를 수행했으며,
중복 finding은 최종 issue count에서 한 번만 계산했다.

| # | Skill path (`$S/…`) | Decision | 핵심 |
|---:|---|---|---|
| 1 | `.backup-KangsCodexSkills-20260508-114155/biostatinfo-expert/SKILL.md` | comment | 현행 claim boundary 긍정, 외부 validation 필요 |
| 2 | `.backup-KangsCodexSkills-20260508-114155/coding-expert/SKILL.md` | request changes | provenance symlink clobber |
| 3 | `.backup-KangsCodexSkills-20260508-114155/debugging-expert/SKILL.md` | request changes | stage2 hardlink/FIFO |
| 4 | `.backup-KangsCodexSkills-20260508-114155/demis-expert-agent/SKILL.md` | request changes | JAX mixed-output false positive |
| 5 | `.backup-KangsCodexSkills-20260508-114155/donald-expert-agent/SKILL.md` | request changes | missing/failing provenance invariant |
| 6 | `.backup-KangsCodexSkills-20260508-114155/env-mixture-expert/SKILL.md` | not applicable | mixture model surface 없음 |
| 7 | `.backup-KangsCodexSkills-20260508-114155/gennady-expert-agent/SKILL.md` | request changes | special-file invariant |
| 8 | `.backup-KangsCodexSkills-20260508-114155/innovator-expert/SKILL.md` | comment | result-resolution duplication |
| 9 | `.backup-KangsCodexSkills-20260508-114155/ken-expert-agent/SKILL.md` | request changes | safe writer 재사용 필요 |
| 10 | `.backup-KangsCodexSkills-20260508-114155/novelty-expert/SKILL.md` | no novelty verdict | explicit novelty claim 없음 |
| 11 | `.backup-KangsCodexSkills-20260508-114155/petr-expert-agent/SKILL.md` | comment | safety-factor operational reduction 불안정 |
| 12 | `biostatinfo-expert/SKILL.md` | claim gate | estimand/selection/uncertainty |
| 13 | `biostatinfo-expert.backup-20260602-131809/SKILL.md` | claim gate | path 12 duplicate pass |
| 14 | `citing-expert/SKILL.md` | request changes | metric/threshold citation 부족 |
| 15 | `citing-expert.backup-20260602-131809/SKILL.md` | already covered | path 14 duplicate pass |
| 16 | `coding-expert/SKILL.md` | request changes | computational pass, decision contract fail |
| 17 | `coding-expert.backup-20260602-131809/SKILL.md` | already covered | path 16 duplicate pass |
| 18 | `coding-master/SKILL.md` | claim gate | operationally credible, biological validity 미확립 |
| 19 | `coding-master.backup-20260602-131809/SKILL.md` | request changes | test non-hermetic |
| 20 | `debugging-expert/SKILL.md` | request changes | host Docker leak |
| 21 | `debugging-expert.backup-20260602-131809/SKILL.md` | request changes | JAX text predicate |
| 22 | `demis-expert-agent/SKILL.md` | claim gate | binder enrichment evidence 없음 |
| 23 | `demis-expert-agent.backup-20260602-131809/SKILL.md` | comment | score transfer와 assay transfer 분리 |
| 24 | `dennis-expert-agent/SKILL.md` | request changes | free-form text 대신 exit status |
| 25 | `dennis-expert-agent.backup-20260602-131809/SKILL.md` | request changes | unbounded probes |
| 26 | `donald-expert-agent/SKILL.md` | approve | rank formula/invariant 정확 |
| 27 | `donald-expert-agent.backup-20260602-131809/SKILL.md` | request changes | test hash cost |
| 28 | `env-mixture-expert/SKILL.md` | not applicable | domain surface 없음 |
| 29 | `env-mixture-expert.backup-20260602-131809/SKILL.md` | not applicable | path 28 duplicate pass |
| 30 | `gennady-expert-agent/SKILL.md` | request changes | boundary tie invariant |
| 31 | `gennady-expert-agent.backup-20260602-131809/SKILL.md` | already covered | path 30 duplicate pass |
| 32 | `geoffrey-expert-agent/SKILL.md` | comment | diffusion range != calibrated uncertainty |
| 33 | `geoffrey-expert-agent.backup-20260602-131809/SKILL.md` | already covered | path 32 duplicate pass |
| 34 | `grace-expert-agent/SKILL.md` | request changes | hidden infer fidelity |
| 35 | `grace-expert-agent.backup-20260602-131809/SKILL.md` | already covered | path 34 duplicate pass |
| 36 | `innovator-expert/SKILL.md` | accept | boundary guard-band transfer |
| 37 | `innovator-expert.backup-20260602-131809/SKILL.md` | request changes | GPU lease race |
| 38 | `jeff-expert-agent/SKILL.md` | request changes | multi-GPU check/execution mismatch |
| 39 | `jeff-expert-agent.backup-20260602-131809/SKILL.md` | request changes | no-progress policy 없음 |
| 40 | `ken-expert-agent/SKILL.md` | request changes | JAX predicate duplication/divergence |
| 41 | `ken-expert-agent.backup-20260602-131809/SKILL.md` | approve | standalone script simplicity 유지 |
| 42 | `linus-expert-agent/SKILL.md` | request changes | JAX gate invariant 약함 |
| 43 | `linus-expert-agent.backup-20260602-131809/SKILL.md` | request changes | 2.293 GB unrelated test hashing |
| 44 | `novelty-expert/SKILL.md` | approve framing | novelty/SOTA claim 없음 |
| 45 | `novelty-expert.backup-20260602-131809/SKILL.md` | comment | future screening novelty 주의 |
| 46 | `petr-expert-agent/SKILL.md` | accept | legacy O(N²F) 개선 필요 |
| 47 | `petr-expert-agent.backup-20260602-131809/SKILL.md` | approve | pilot-scale Kendall O(n²) 유지 |
| 48 | `tavis-expert-agent/SKILL.md` | request changes | provenance clobber |
| 49 | `tavis-expert-agent.backup-20260602-131809/SKILL.md` | request changes | manifest hardlink/FIFO |
| 50 | `yann-expert-agent/SKILL.md` | not applicable | repo-owned vision training 없음 |
| 51 | `yann-expert-agent.backup-20260602-131809/SKILL.md` | already covered | confidence/ground-truth 경계 유지 |
| 52 | `yoshua-expert-agent/SKILL.md` | request changes | rank CI/held-out validation 없음 |
| 53 | `yoshua-expert-agent.backup-20260602-131809/SKILL.md` | request changes | generic 0.8 heuristic |

## 13. 외부 근거와 citation audit

- [Google DeepMind AlphaFold 3 output documentation, pinned revision](https://github.com/google-deepmind/alphafold3/blob/97d20234c6eb89e8d05376e9eecc9321e60a559b/docs/output.md):
  AF3 pLDDT는 per-atom confidence이고, pTM/ipTM interpretation과 ranking-score 제한을 설명한다.
- [EMBL-EBI pLDDT guide](https://www.ebi.ac.uk/training/online/courses/alphafold/inputs-and-outputs/evaluating-alphafolds-predicted-structures-using-confidence-scores/plddt-understanding-local-confidence/):
  일반적인 pLDDT category는 per-residue interpretation이다. 이를 atom-weighted whole-target grade의
  직접 calibration 근거로 쓰면 안 된다.
- pinned local `../af3_work/alphafold3/OUTPUT_TERMS_OF_USE.md`: distribution/notice/citation 기술 감사의 기준.
- [Current CI run](https://github.com/kangk1204/Kang_AF3/actions/runs/32971658448): HEAD 4b1f533,
  Python 3.9/3.12/3.14 success.

metric 정의와 threshold를 README 해당 문장 가까이에 공식 AF3 source로 연결하고, repository가
새로 만든 grade/range threshold는 local heuristic임을 문장 단위로 표시해야 한다.

## 14. 검증 재현 명령과 source checksum

### 14.1 통제된 local release verification

Docker가 설치된 host에서 일부 environment test가 real Docker를 발견하는 문제를 피하기 위해,
host Docker만 PATH에서 차단하되 각 test가 만드는 repository fake Docker는 그대로 우선하도록
다음 명령을 사용했다.

```bash
audit_bin=$(mktemp -d /tmp/kang-af3-audit-bin.XXXXXX)
trap 'rm -f "$audit_bin/docker"; rmdir "$audit_bin"' EXIT
ln -s /bin/false "$audit_bin/docker"
env -u AF3_DOCKER PATH="$audit_bin:$PATH" python3 tests/run_all.py
```

결과는 `131/131`, naming `125/125`, compatibility `77/77`, mutation `56/56`,
`release verification passed`였다. 이는 auditor session result이며 repository에 별도 log file을
추적하지 않았다. 현재 CI의 independently retained evidence는 section 13의 GitHub Actions run이다.

### 14.2 static/numerical checks

```bash
python3 -m compileall -q scripts tests
shellcheck -x scripts/af3_check.sh scripts/af3run.sh scripts/install_af3_ubuntu.sh
bash -n scripts/af3_check.sh scripts/af3run.sh scripts/install_af3_ubuntu.sh
python3 scripts/af3_rankcorr.py --selftest
git diff --check
git fsck --no-reflogs --full
```

CSV 교차검산 공식은 다음과 같았다.

- `hours_2000 = per_target_s * 2000 / 3600`
- `days_2000 = hours_2000 / 24`
- `wall_per_target = wall_s / n_targets`
- DB delta = full − reduced
- `af3_summary.csv`의 `(condition,target)` 값을 DB comparison table과 대조
- 모든 CSV에서 literal `nan/inf/-inf` 탐색

감사 당시 source checksum:

| CSV | SHA-256 |
|---|---|
| `ab_benchmark.csv` | `810cbb747ba919a84f051bc96225c5475a94c18e577765e4b4d3decd754847f2` |
| `af3_summary.csv` | `ae83d18c33a850175613c70348227b03155ecc7f3468aab4fdc1600158092762` |
| `db_confidence_comparison.csv` | `1cae2b4d78fabae0780fbacfc18824a7e2dff2e36da2c969bc55c7143cb3dab6` |
| `msa_comparison.csv` | `fcead4e4f6757670663e93f67638eacdbc26049d81fdad7cdc5790acb0a456f3` |
| `msa_threads.csv` | `fe168f30f293fc654f8248a6876b0b0a1387ce4415d59dd88c78a9af71b33576` |
| `msa_throughput.csv` | `d81171c5f36429aeaff1f8d8840265d13ec63a753d1d1e71bcfd5a74dd54953e` |
| `projection_2000.csv` | `4462bc565ceee8e3ad73fed5126b62e1ade5a1445981e4dd483ad46e56ea6bc6` |
| `sort_effect.csv` | `6308cd86aaba70e5e58a55f50bd5935f3bef6fedefc452f968f17c402dc089f9` |
| `stage2_rankcorr_db.csv` | `9b5bdf4fda4e583cc303d11e9e57789125c4ea3748b6f2b1976a2d4a8dc6d8dc` |
| `stage2_reuse_identity.csv` | `d30f06d2028a64c0b28ac4b927b8e3b4c134fbe662ee22f3ce96f57803521aae` |
| `stage2_timing.csv` | `11816fb9d570de99ad9311964ede3b2bf0451e707ca920dce76e0b09fc6a352e` |
| `visualize_table.csv` | `ccf6eede1e68e2a73a2a8ef6a0dd39759afc9683ea3a8c323079f3f8dfafe683` |

검산 결과는 `numeric_consistency_issues = 0`이었다. 이 표와 공식으로 동일 snapshot의 결과를
독립 재현할 수 있다.

## 15. 감사 한계

이번 감사에서 실행하지 않았거나 직접 증명하지 못한 것:

- 850 GB full installation end-to-end 재설치
- 실제 2,000-target AF3 run
- 독립 binder/nonbinder assay 또는 native-structure benchmark
- 모든 PNG의 byte-identical regeneration(raw AF3 outputs/builder 부재)
- dependency CVE audit(`pip-audit` 미설치)
- Python type checking/line coverage
- 법률 자문

이 한계들은 코드·CSV 산술·재현된 failure-path 결함을 무효화하지 않지만, biological accuracy와
legal compliance에 대한 최종 승인 권한은 각각 실험 validation과 기관 검토에 남는다.

## 16. 결론

Kang_AF3의 구현 품질은 상당히 높아졌고, 특히 defensive input handling과 regression culture는
강점이다. 그러나 pipeline의 가장 중요한 질문은 “Docker를 다시 호출했는가”가 아니라
“이 canonical 구조가 정확히 어떤 input/environment 계산에서 나왔는가”다. 현재 provenance,
cache, publish, lock state machine은 그 질문에 아직 정확히 답하지 못한다.

과학적으로도 현재 데이터는 AF3 confidence stability와 fixed-host throughput을 설명하지만,
screening sensitivity 또는 biological hit recovery를 추정하지 못한다. 따라서 P0 provenance/cache
transaction과 unbiased validation design이 완료되기 전에는 운영 release와 validated-screening claim을
잠그는 것이 타당하다.

## 17. Remediation addendum

이 절은 감사 이후 수행한 수정과 최종 재검증을 기록한다. 1~16절은 수정 전 snapshot의
역사적 감사 기록으로 보존한다. 현재 판정은 이 addendum가 우선한다.

### 17.1 최종 판정

- **코드·운영 release:** 승인. 본 감사와 후속 독립 review에서 확인된 release-blocking
  code/integrity/security 문제는 수정됐고, local release entry point와 실제 Docker/GPU smoke가
  통과했다.
- **과학적 사용 범위:** exploratory structure prediction/prioritization에 한해 승인. binder 여부,
  affinity/Kd, epitope, native-structure accuracy, assay hit recovery, clinical validity 또는 SOTA는
  이번 수정과 검증으로 확립되지 않았다.
- **upstream 관계:** Kang_AF3는 pinned official AlphaFold 3 inference engine을 감싸는 비공식
  운영·provenance·후처리 계층이다. 모델 architecture/weights의 정확도 우위를 주장하지 않는다.

### 17.2 감사 finding closure

| Finding | 최종 상태 | 핵심 조치 |
|---|---|---|
| H-01 canonical 오귀속 | **Closed** | changed-input canonical을 lock 안에서 격리하고 clean destination 실행 → current artifact identity 검증 → provenance atomic commit 순서로 고정 |
| H-02 effective-input provenance | **Closed** | JSON/sidecar private snapshot, model SHA-256, image ID/revision, GPU fraction, output artifact hash, reduced overlay content hash, official full-DB seal을 provenance v2에 포함 |
| H-03 legacy freshness | **Closed** | manifest 없는 cache/result 기본 distrust, producer rc=0 뒤에만 atomic publish, private JSON snapshot과 exact-byte identity 적용 |
| H-04 lock/lease 모델 | **Closed** | runner 공통 output lock, canonical output/work lock order, known GPU는 global shared+UUID exclusive, unknown inventory는 global exclusive lease |
| H-05 top-N tie | **Closed** | collect/stage2/rankcorr 기본 include-all boundary tie, exact-N은 explicit error policy |
| H-06 hidden infer fidelity | **Closed** | `infer`는 pinned 기본 정밀 설정, `infer-screen`은 명시적 1×3 경량 설정으로 분리 |
| H-07 stage2 identity | **Closed** | filename뿐 아니라 내부 target identity·schema·source uniqueness를 검증하고 manifest를 atomic publish |
| M-01/M-02 local publication | **Closed** | symlink/hardlink/FIFO/special-file 거부, `O_NOFOLLOW`, single-link, fsync, atomic replace |
| M-03/M-04 shell/wrapper | **Closed** | 제한된 Docker argv 계약과 mkdir/tee sticky nonzero 적용 |
| M-05/M-06 install integrity | **Closed** | DB final 재검증, model same-directory staging → size/SHA → no-clobber atomic publish |
| M-07/M-08 diagnostics | **Closed** | JAX Python assertion, named bounded probes, bounded cleanup, help/HMMER capability gate |
| M-09 GPU readiness | **Closed** | device별 inventory/admission/UUID binding과 post-lease recheck; known/unknown split-brain 차단 |
| M-10 watchdog | **Closed** | preferred artifact-growth watchdog와 legacy stdout/artifact·per-shard watchdog, bounded TERM→KILL |
| M-11 ipTM tri-state | **Closed** | chain count로 monomer/multimer를 판정하고 multichain missing-ipTM을 별도 상태로 유지 |
| C-01/C-02 terms/notice | **Closed** | pinned Output Terms 원문, modification notice와 모든 배포 artifact notice 전파 |
| C-03/C-04 lineage/figure | **Closed** | deterministic builder+manifest, rendering environment 기록, stale/mixed-unit figure 재생성; A/B estimator를 문서와 같은 median으로 통일 |
| T-01/T-03 discovery | **Closed** | fake-Docker hermetic release path, AST 기반 test discovery와 누락된 top-level/unittest wrapper gate |
| T-02 test hash cost | **Closed** | sparse model fixture와 snapshot/manifest fixture로 unrelated multi-GB test read 제거 |
| T-05 validator proof 표현 | **Closed** | validator equivalence를 proof가 아닌 guarded compatibility regression으로 제한 |

후속 adversarial review에서 추가로 발견된 input-root/sidecar TOCTOU, legacy hardlink staging,
self-authored official DB seal, reduced-manifest 약검증, known/unknown GPU lease 분리, deprecated JAX memory
environment 충돌도 같은 수정 범위에서 닫았다. JSON/sidecar는 한 번 연 input-root FD 기준으로
descriptor-relative snapshot되며, official DB seal validation은 pinned content와 current binding을 모두
검사한다. installer의 caller-supplied preverified seal 우회는 제거했다.

### 17.3 최종 검증 증거

통제된 local release 결과:

- `python3 tests/run_all.py` → **release verification passed**
- registered strict regressions → **177/177 passed** (`59.8 s`)
- filename compatibility → **77 passed**
- standalone legacy integrity → **17 passed**
- naming integration → **125 passed**
- mutation verification → **56/56 caught** (`52.9 s`)
- test discovery → **9 registered modules + 3 standalone suites**, 누락 0
- `tests/test_provenance_gpu.py` → **19/19 passed**
- rank-correlation self-test → 전건 일치
- Python 3.8 grammar AST → **28 files passed**
- `compileall`, Bash syntax, ShellCheck, `git diff --check`, `git fsck` → passed
- secret pattern scan → clean
- pinned Output Terms byte comparison → exact match
- artifact manifest check와 같은 rendering environment의 2회 rebuild → byte-identical

실제 환경·실행 증거:

- Ubuntu 26.04 / Docker 29.7.2 / RTX 3080 Ti에서 container GPU와 JAX `backend=gpu` 확인
- image ID `sha256:3ff5fe780040c2f47ac3e97c55d75c82cf416b2b80aab4c9a98f96242f1f1948`,
  AF3 revision `97d20234c6eb89e8d05376e9eecc9321e60a559b`
- `af3.bin` 1,146,811,260 bytes, SHA-256
  `df8bbf2621f17dd3ee21c2a921e84a50bc2b80cdc0c7971cb915c2826fee1f9b`
- 8 FASTA + extracted 195,858-mmCIF tree deep seal: **1:54:33**, official content identity
  `595ec345d6233e6176d3db221e65d15d65be765c886193908242bbef1fda9ec9`
- `af3_check.sh` → full DB seal·model·Docker·GPU·JAX·HMMER 포함 **필수 환경 전건 통과**
- 공개 VHH `vhh_7mfv_1` full-mode smoke → **1/1 complete**, data pipeline `4.52 s`,
  model inference `25.10 s`, runner `52.6 s/target`
- smoke provenance v2 → reduced overlay + official full seal + model/image/GPU identity와
  `_data.json`, `_model.cif`, `_ranking_scores.csv`, `_summary_confidences.json` 4개 hash 기록
- 같은 명령의 `--audit` → 완료 1, 미완료 0, 격리/staging 잔여 0, 계산 Docker 미실행
- smoke 종료 후 container/GPU process 잔여 0

독립 최종 review 판정:

- comprehensive code re-review → **APPROVE**
- adversarial security re-review → **APPROVE**
- scientific/statistical/claim re-review → **APPROVE** (외부 validation gate는 false 유지)
- README/upstream-boundary review → **APPROVE**
- figure visual QA → 6개 중 발견된 mean/median 불일치 수정 후 전건 승인

### 17.4 남아 있는 과학·외부 검증 gate

다음은 코드 결함이 아니라 이번 작업 범위에서 생성되지 않은 외부 증거다.

| Gate | 상태 | 허용되는 결론 |
|---|---|---|
| independent binder/nonbinder assay | **False** | 결합 여부·hit enrichment를 주장하지 않는다 |
| native/reference structure benchmark | **False** | 실제 구조 정확도 우위를 주장하지 않는다 |
| prespecified held-out screening validation | **False** | cutoff calibration/replication/SOTA를 주장하지 않는다 |
| full 2,000-target production completion | **False** | 소규모 실측의 2,000건 투영을 완주 측정으로 부르지 않는다 |
| clinical/legal institutional review | **False** | 임상 사용 승인이나 법률 자문으로 해석하지 않는다 |

따라서 최종 package는 **재현 가능하고 fail-closed인 AF3 batch orchestration 및 exploratory
prioritization 도구**로 승인한다. biological validation 또는 upstream 대비 모델 정확도 향상으로는
승인하지 않는다.
