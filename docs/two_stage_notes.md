# 2단계 스크리닝 전략 - 실행 절차와 측정 근거

VHH/나노바디 2000건을 AlphaFold 3 로 스크리닝할 때, 전수를 기본 설정으로 돌리는 대신
**전수는 경량 설정으로, 상위 후보만 정밀 설정으로 두 번 돌리는** 전략을 쓴다.
이 문서는 그 절차와 근거, 그리고 **아직 측정되지 않은 것**을 적는다.

새로 만든 도구는 두 개다.

| 도구 | 하는 일 |
|---|---|
| `scripts/af3_stage2.py` | 1단계 결과요약 CSV 에서 상위 후보를 골라 2단계 재실행 입력 JSON 을 만든다 |
| `scripts/af3_rankcorr.py` | 두 설정으로 돌린 같은 타깃 집합의 순위 상관과 상위 N 겹침률을 잰다 |

---

## 0. 가장 먼저 읽을 것 - 이 전략의 전제는 검증되지 않았다

2단계 전략이 성립하려면 **경량 설정이 정밀 설정의 상위권을 놓치지 않아야 한다.**

이 저장소는 그것을 측정하지 않았다. 있는 측정은 다른 것이다.

- 측정된 것: 축소 DB(2 GB) 와 전체 DB(850 GB) 로 같은 VHH 단량체 6건을 돌렸을 때
  MSA 깊이가 800~1000배 차이인데도 ranking score 는 무변화 3건 / +0.03 2건 / -0.01 1건.
  즉 **DB 크기를 줄이는 것**은 단량체 스크리닝에서 순위를 크게 흔들지 않는다.
- 측정되지 않은 것: **`--num_diffusion_samples` 와 `--num_recycles` 를 줄이는 경량 설정**의
  순위 보존. DB 크기와 샘플/recycle 수는 다른 축이다. 전자는 입력 정보량을,
  후자는 추론 자체의 표본 수를 줄인다.

그래서 이 저장소의 도구에는 **컷오프 기본값이 없다.** `af3_stage2.py` 는 `--top` 이나
`--min` 을 주지 않으면 거부한다. 근거 없는 숫자를 기본값으로 넣으면, 사용자는 그것이
측정된 값이라고 믿고 2000건을 날린다.

대신 discovery와 validation을 분리하는 절차를 준다. score를 보기 전에 representative
common panel을 고정하고 모든 target을 두 설정에서 계산한다. discovery subset에서 metric과
후보 multiplier를 탐색했다면 별도의 held-out subset에서 top-K recall lower confidence bound와
허용 miss-rate를 확인한다. 40건은 workflow smoke/pilot일 뿐 2,000건 cutoff의 validation이 아니다.

---

## 1. 설계 판단 - 왜 새 스크립트인가

`af3_prepare.py` 에 옵션을 더하는 안과 새 스크립트를 만드는 안을 비교했다.
새 스크립트로 갔다. 근거는 셋이다.

**입력의 성질이 다르다.** `af3_prepare.py` 는 FASTA/CSV 의 **서열**을 받아 JSON 을
만든다(22개 옵션 전부가 서열에서 JSON 을 조립하는 데 쓰인다). 2단계 입력은 서열이
아니라 **이미 만들어진 JSON** 을 받아 설정만 바꾼다. 서열을 다시 조립하는 것은
위험하다 - 1단계에서 무슨 서열이 어떤 사슬 구성으로 들어갔는지를 CSV 이름만 보고
재현하려면 원본 FASTA 와 당시 옵션(`--copies`, `--partner-fasta`, `--ligand-ccd` 등)을
정확히 같이 줘야 하고, 한 글자만 어긋나면 1단계와 다른 것을 2단계에서 돌리게 된다.
원본 JSON 을 찾아 복사하고 설정만 바꾸는 편이 안전하다.

**`_data.json` 재사용이 서열 재조립으로는 불가능하다.** 3절에서 다루는 MSA 건너뛰기는
1단계가 만든 `<이름>_data.json` 을 그대로 입력으로 쓰는 것이다. 이것은 서열에서
만들어낼 수 있는 파일이 아니다.

**`af3_prepare.py` 의 사용법을 건드리지 않는다.** 이미 22개 옵션이 있고 초보자용
설명이 길다. 성질이 다른 입력 경로를 여기에 얹으면 `--fasta`/`--csv` 와 배타적인
플래그 군이 하나 더 생기고, 어떤 옵션이 어느 경로에서 유효한지 설명이 복잡해진다.

기존 스크립트는 **아무것도 바꾸지 않았다.** `af3_collect.py --top-list` 의 출력 형식을
그대로 입력으로 받고(`af3_stage2.py --list`), 결과요약 CSV 도 그대로 읽는다.
`run_af3_batch_improved.py` 에 넘길 명령을 화면에 찍어준다.

```
af3_prepare.py  ──FASTA/CSV──►  1단계 입력 JSON
                                      │
                                      ▼  (경량 설정)
                                 1단계 출력 폴더
                                      │
                          af3_collect.py ──► 결과요약 CSV ──┐
                                                            │
   af3_rankcorr.py ◄── (예비실험 두 조건의 CSV 두 개)        │  컷오프 결정
                                                            ▼
                                  af3_stage2.py ──► 2단계 입력 JSON
                                                            │
                                                            ▼  (정밀 설정)
                                  run_af3_batch_improved.py --mode inference
```

---

## 2. `af3_stage2.py` 가 하는 일

```bash
# 상위 100건을 _data.json 재사용으로 (MSA 건너뜀. 가장 빠르다)
python3 scripts/af3_stage2.py -c 1단계요약.csv --top 100 \
    --source data --from-out vhh_out -o vhh_2단계_in

# 점수 컷오프로 (몇 건이 될지는 데이터가 정한다)
python3 scripts/af3_stage2.py -c 1단계요약.csv --min 0.85 -o vhh_2단계_in

# af3_collect.py --top-list 의 출력을 그대로
python3 scripts/af3_stage2.py --list top100.txt --from-out vhh_out -o vhh_2단계_in
```

선별은 `--top N`(상위 N건), `--min 값`(컷오프 이상 전부), `--grade`(등급 필터),
`--condition`(조건 라벨 하나만)의 조합이다. 정렬 열은 `--by` 로 고른다
(`ranking_score`, `pLDDT평균`, `pTM`, `ipTM`, `pLDDT_90이상비율`).

만들어진 폴더에는 입력 JSON 과 `2단계_선정내역.csv`(순위, 1단계 점수, 원본 경로,
MSA 문자수, 시드)가 들어간다. 나중에 "이 후보가 왜 뽑혔나" 를 되짚을 근거다.

### 안전장치 (실제로 시간을 날릴 수 있는 것들)

- **컷오프 기본값 없음.** `--top`/`--min` 없이 돌리면 거부하고, 왜 거부하는지와
  `af3_rankcorr.py` 를 안내한다.
- **조건 섞임 거부.** 결과요약 CSV 에 조건이 둘 이상 있으면(`af3_collect.py 축소=... 전체=...`)
  같은 타깃이 두 번 뽑혀 2단계 입력이 겹친다. `--condition` 을 요구한다.
- **출력 이름 충돌 사전 검출.** AF3 출력 폴더 이름은 파일명이 아니라 JSON `name` 을
  정규화한 값(`sanitised_name`: 공백→밑줄, `[A-Za-z0-9_-.]` 만)이다. 서로 다른 후보가
  같은 정규화 이름을 갖으면 2단계 결과가 서로를 덮어쓴다. 쓰기 전에 막는다.
- **sidecar 검출.** `mmcifPath`/`unpairedMsaPath` 같은 외부 파일 참조가 있으면
  상대경로가 JSON 위치 기준으로 해석되므로 복사만으로는 깨진다. 검출해 건너뛰고 이유를 말한다.
  (검증 호스트의 `_data.json` 은 MSA/템플릿을 문자열로 직접 담고 있어 이 문제가 없다.
   3절 참고)
- **MSA 빈 상태 경고.** `_data.json` 인데 MSA 문자수가 0이면 MSA 건너뛰기가 무의미하다.
- **`._` AppleDouble 사이드카 무시.** macOS 에서 만든 tar 를 리눅스에서 풀면 생기는
  파일이다. `glob('*.json')` 에 잡히고 UTF-8 이 아니라서 AF3 가 읽는 순간 죽는다.
  (이 저장소에서 실제로 3시간 측정을 날린 함정)
- **AF3 가 모르는 최상위 키 제거.** AF3 `folding_input` 은 허용 목록 밖의 최상위 키를
  거부한다. 다른 도구가 붙인 메모 등을 떨어내고, 떨어낸 키를 화면에 알린다.
- **원자적 쓰기.** `.json.tmp` 에 쓰고 `os.replace` 로 옮긴다. 도중에 끊겨도 반쪽 JSON 이
  남지 않는다 (반쪽 JSON 하나가 `load_fold_inputs_from_dir` 제너레이터를 멈추게 하면
  그 뒤 입력 전부가 처리되지 않는다).
- **경계 동점 보존.** ranking_score는 반올림돼 top-N 경계 동점이 생길 수 있다.
  기본 `--tie-policy include-all`은 경계 동점을 전부 포함하므로 실제 선택 수가 N보다
  커질 수 있다. 정확히 N개가 필요한 작업은 `--tie-policy error`로 중단한 뒤 cutoff를
  사전 재정의한다. 이름 정렬은 표시 순서만 정하며 표본을 자르는 규칙이 아니다.

---

## 3. `_data.json` 재사용 - MSA 를 건너뛴다 (실물 검증)

### 왜 재사용을 보는가 - 그리고 절약폭은 조건부다

이 저장소의 기존 측정으로 2000건 구성은 총 40.2시간이고 그중 MSA 가 37.2시간(93%)이다
(MSA 처리율 0.895건/분, 추론 5.39초/건). 이 숫자를 보면 재사용의 값이 압도적으로
보인다 - 과제 지시도 그렇게 적혀 있었다.

**그런데 그 0.895건/분은 전체 DB 급(4GB 슬라이스 4종) 구성의 측정값이다**
(`results_example/msa_throughput.csv` 의 `db` 열을 보면 명시돼 있다).
이 트랙에서 축소 DB(2 GB) 구성을 직접 재보니 데이터 파이프라인이 건당 1.98초였다.
즉 **권장 구성(축소 DB 스크리닝)에서는 MSA 가 93% 가 아니다.**

그래서 결론이 둘로 갈린다. 아래 실측 표가 그 근거다.

- 1단계를 전체 DB 급으로 돌렸다면 재사용이 건당 약 30초를 아낀다 - 큰 절약이다.
- 1단계를 축소 DB 로 돌렸다면 재사용의 시간 절약은 건당 4~5초로 작다.
  그래도 재사용을 권한다. 이유는 시간이 아니라 **1단계와 2단계가 동일한 MSA 를
  쓴다는 것**이다(뒤의 동일성 확인 참고 - MSA 를 다시 만들면 같은 DB 로도 깊이가 달라졌다).

### 구조 확인 (검증 호스트 gpu-5070ti 실물)

AF3 는 데이터 파이프라인 단계에서 타깃마다 `<이름>_data.json` 을 쓴다.
`vhh_4qgy_1_data.json` (839,761 B) 을 열어 확인한 내용:

- 최상위 키: `dialect`, `version`(=4), `name`, `modelSeeds`, `sequences`,
  `bondedAtomPairs`(=null), `userCCD`(=null)
- protein 사슬 키: `id`, `sequence`(135자), `modifications`, `unpairedMsa`(1,597자),
  `pairedMsa`(40,442자), `templates`(인라인 mmCIF)
- **외부 파일을 가리키는 `*Path` 키는 없다.** MSA 와 템플릿이 문자열로 파일 안에 있다.

그래서 이 파일을 다른 폴더로 복사해도 MSA 가 따라온다. `--norun_data_pipeline` 으로
돌리면 AF3 는 MSA 검색을 하지 않고 파일에 있는 MSA 를 그대로 쓴다.

`af3_stage2.py --source data` 로 만든 입력을 확인한 결과 (검증 호스트 실측):

```
1_vhh_7mfv_1.json  unpairedMsa=2560  pairedMsa=42318  templates=4  version=4
2_vhh_4s11_1.json  unpairedMsa=2583  pairedMsa=42534  templates=4  version=4
   최상위 키: dialect, modelSeeds, name, sequences, version
```

MSA 가 그대로 들어 있고, AF3 가 거부하는 키는 없다.

### A/B 실측 (측정값) - 그리고 절약폭은 DB 구성에 달려 있다

**검증 호스트 gpu-5070ti (RTX 5070 Ti 16GB, conda 네이티브 AF3, VHH 단량체 4건,
샘플 5 x recycle 10, 시드 1, load1 2.0~7.1).** AF3 자신의 플래그로 단계를 분리해 쟀다.
`--jax_compilation_cache_dir` 를 모든 조건에 켜고, **측정 전에 캐시를 데우는 실행을
따로 돌려** 버킷 128/256 컴파일을 미리 태웠다(그 실행은 측정에서 제외).

| 조건 | AF3 플래그 | 벽시계 4건 | 건당 |
|---|---|---|---|
| D1 데이터 파이프라인만, 축소DB 2GB | `--norun_inference` | 7.92초 | **1.98초** |
| D2 데이터 파이프라인만, 4GB 슬라이스 4종 | `--norun_inference` | 121.62초 | **30.41초** |
| I1 추론만, `_data.json` 재사용 | `--norun_data_pipeline` | 50.44초 | **12.61초** |
| I2 같은 것 반복 | `--norun_data_pipeline` | 55.39초 | **13.85초** |
| F 전체(파이프라인+추론), 축소DB | (없음) | 71.71초 | **17.93초** |

AF3 로그가 단계 분리를 확인해준다. 재사용 실행에는 `Skipping data pipeline...` 이
타깃마다 찍히고 jackhmmer 가 한 번도 실행되지 않는다. 전체 실행에는
`Running data pipeline for chain A took 1.53~3.17 seconds` 가 찍힌다.

**결론: `_data.json` 재사용은 실제로 동작하고, 절약폭은 1단계에 쓴 DB 구성이 정한다.**

- 축소 DB(2 GB) 로 1단계를 돌린 경우: 데이터 파이프라인이 건당 1.98초뿐이므로
  재사용의 절약은 **건당 약 2~5초**다. F(17.93) - I(12.61~13.85) = 4.1~5.3초.
  200건이면 14~18분. 큰 절약이 아니다.
- 4GB 슬라이스 4종(전체 DB 급) 로 1단계를 돌린 경우: 데이터 파이프라인이 건당 30.41초다.
  이것을 건너뛰는 것이 **건당 30초** 절약이다. 200건이면 1.7시간.
  이 저장소의 0.895건/분(67.0초/건) 측정도 이 구성에서 나온 값이다.

**과제 지시에 있던 "2000건 40시간 중 93%가 MSA" 라는 전제는 전체DB 급 구성의 값이다.**
축소 DB 로 스크리닝하는 권장 구성에서는 MSA 가 그만큼 크지 않다. 이 트랙에서
같은 4건으로 두 DB 구성을 직접 대조해 확인했다.

| DB 구성 | 데이터 파이프라인 건당 | MSA unpaired 깊이 (4건) |
|---|---|---|
| 축소 DB 2GB (`~/public_databases`) | 1.98초 | 7, 9, 10, 11 |
| 4GB 슬라이스 4종 (`sweep_dbs`) | 30.41초 | 91, 119, 128, 138 |

깊이가 10~14배 차이나고 시간이 15배 차이난다. 재사용의 값은 1단계 DB 가 클 때 크다.
축소 DB 로 스크리닝한다면 `_data.json` 재사용의 진짜 이점은 시간이 아니라
**1단계와 2단계가 완전히 같은 MSA 를 쓴다는 것**이다(아래 동일성 확인).

D2 의 첫 타깃만 91.70초가 걸렸고 나머지 3건은 9.09~9.26초였다. 4 GB 파일 4개를
페이지 캐시에 올리는 비용이다. 건당 30.41초는 그 첫 건을 포함한 평균이고,
정상상태는 9~10초에 가깝다. 2000건 규모에서는 정상상태가 지배한다.

주의: 첫 시도의 A/B 는 교란됐다. 재사용 조건을 먼저 돌려 JAX 컴파일 비용을 전부
물리고(추론 67~77초/건), 전체 파이프라인 조건이 따뜻한 캐시를 썼다(7.88초/건).
"재사용이 4배 느리다" 는 잘못된 결론이 나올 수 있었다. **캐시를 데운 뒤 재는 것이
필수다.** 위 표는 그렇게 다시 쟀다.

### 결과가 원래 실행과 같은가 (동일성 확인) - 실측

재사용이 "동작한다" 는 것만으로는 부족하다. **재사용한 결과가 원래 실행과 같은가**를
확인해야 한다. 검증 호스트에서 같은 6건(VHH 단량체)으로 네 조건을 대조했다.

- **원래실행** - `_data.json` 을 만든 원본 실행 (`~/af3_db_track/af3out_reduced`)
- **재사용** - 그 `_data.json` 을 `af3_stage2.py --source data` 로 입력화해 `--norun_data_pipeline` 추론
- **재사용2** - 같은 것을 한 번 더 (재현성)
- **새파이프라인** - 원본 JSON 으로 축소DB MSA 를 새로 만들어 추론

| 대조 | ranking_score 최대차 | pLDDT평균 최대차 | Spearman rho |
|---|---|---|---|
| 원래실행 vs 재사용 | 0.0100 (평균 0.0033) | 2.4769 (평균 0.5202) | 0.9706 |
| 재사용 vs 재사용2 | **0.0000** | **0.0000** | (완전 동일) |
| 원래실행 vs 새파이프라인 | 0.0100 (평균 0.0067) | 1.4331 (평균 0.8253) | 0.9549 |

읽는 법:

- **재사용은 완전히 재현된다.** 두 번 돌려 ranking_score 와 pLDDT평균이 소수점까지 같다.
  같은 MSA + 같은 시드이므로 그래야 맞고, 그것을 확인했다.
- **원래실행과의 차이는 원래실행이 다른 조건이었기 때문이다.** MSA 깊이는 6건 전건
  동일하게 보존됐다(9,13,9,10,13,12 → 9,13,9,10,13,12). 즉 `_data.json` 의 MSA 가
  그대로 실린다. 남은 차이는 AF3 확산 샘플링의 실행 간 변동으로, ranking_score 는
  최대 0.01(반올림 한 자리)이다.
- **새파이프라인은 MSA 자체가 달라진다.** 같은 축소 DB 인데 unpaired 깊이가
  9→7, 13→10, 12→9 로 줄었다(원래 실행 시점과 템플릿/DB 상태가 다르다).
  MSA 를 다시 만들면 입력이 달라진다는 뜻이다. **1단계와 2단계를 정확히 같은 MSA 로
  묶으려면 `_data.json` 재사용이 유일한 방법이다.** 축소 DB 에서 시간 절약이 작아도
  재사용을 권하는 이유가 이것이다.

정합 6건 벽시계: 재사용 67.79초 / 67.94초(반복), 새파이프라인 78.01초.
건당 11.30 / 11.32 / 13.00초.

### `--strip-msa` - 반대로 MSA 를 다시 하고 싶을 때

1단계를 축소 DB 로, 2단계를 전체 DB 로 돌리는 구성이라면 MSA 를 다시 검색해야 한다.
`--strip-msa` 가 `_data.json` 에서 MSA/템플릿 키를 **지운다**.

키를 빈 문자열로 두면 안 된다. AF3 는 `unpairedMsa=""` 를 "검색하지 말고 빈 MSA 로
진행" 으로 해석한다. 다시 검색시키려면 키가 아예 없어야 한다. 검증 호스트에서
`--strip-msa` 후 `unpairedMsa`/`pairedMsa`/`templates` 키가 모두 사라진 것을 확인했다.

`--source input` 으로 원본 입력 JSON 을 쓰는 것도 같은 결과다(MSA 가 애초에 없다).
원본 폴더가 남아 있으면 이쪽이 파일 크기가 작아 낫다.

---

## 4. `af3_rankcorr.py` - 컷오프를 정하는 예비실험

### 무엇을 계산하는가

| 값 | 뜻 | 2단계 전략에서의 의미 |
|---|---|---|
| Spearman rho | 순위의 전반적 일치 (동점 보정) | 보편적 합격선은 없다. 사전 지정한 miss cost와 CI로 판단 |
| Kendall tau-b | 쌍 단위 일치율 (동점 보정) | rho 보다 이상치에 덜 흔들린다 |
| **top-N 겹침률(recall)** | 정밀 상위 N건 중 경량 상위 N건에 든 비율 | **가장 중요한 숫자.** 0.95 면 20건 중 1건을 놓친다 |
| **top-N 안전배수** | 정밀 상위 N건을 전부 잡으려면 경량 상위 몇 건까지 봐야 하는가 | **재실행 규모를 정한다.** 1.5 면 상위 100건을 원하면 150건 재실행 |
| 값 차이 분포 | 같은 타깃의 지표 값이 두 설정에서 얼마나 달라지는가 | 컷오프를 값으로 줄 때(`--min`) 여유폭의 근거 |

rho 가 높아도 겹침률이 낮을 수 있다. 전체 순위는 맞는데 상위권만 섞이는 경우다.
2단계에서 중요한 것은 rho 가 아니라 겹침률이다.

### 예비실험 절차

**1단계. score-blind common panel을 고정한다.** target이 분석 단위다. monomer/complex,
길이, family, ligand와 예상 난이도를 층화하고 failed/missing target도 intention-to-screen
분모에 남긴다. 40건 정도 pilot은 구현 점검용이며, metric 선택용 discovery와 최종
held-out validation을 같은 행에 쓰지 않는다.

**2단계. 두 설정으로 돌린다.** 같은 서열, 설정만 다르게.

```bash
python3 scripts/af3_prepare.py --fasta pilot40.fasta -o pilot_in

# 경량 (스크리닝용). 샘플 1개, recycle 3
python3 scripts/af3_batch.py --input_dir pilot_in --output_dir pilot_light_out \
    --diffusion-samples 1 --recycles 3

# 정밀 (기본값). 샘플 5개, recycle 10
python3 scripts/af3_batch.py --input_dir pilot_in --output_dir pilot_full_out
```

예상 소요 (측정값에서 계산). MSA 는 두 설정이 같으므로 한 번만 한다. **DB 구성에 따라
크게 다르다** - 3절 실측 기준:

- 축소 DB 2GB: 40건 x 1.98초 = **약 1.3분**
- 전체 DB 급(4GB 슬라이스): 40건 x 30.41초 = **약 20분** (정상상태 9~10초로 보면 약 7분)
- 전체 DB 850GB: 이 트랙에서 재지 않았다. 기존 측정 0.895건/분으로는 40건 = 45분(추정)

추론은 정밀 40건이 5.39초/건 x 40 = 약 3.6분(기존 측정). 경량 설정의 추론 시간은
이 저장소에서 측정되지 않았다.

MSA 를 두 번 하지 않으려면 1단계를 `--mode data` 로 한 번 돌린 뒤,
`af3_stage2.py --source data` 로 두 설정의 입력을 만들고 각각 `--mode inference` 로
돌린다. 3절의 동일성 확인에서 보듯 이렇게 하면 두 설정이 **완전히 같은 MSA** 를 쓴다.
MSA 를 각각 새로 만들면 같은 DB 로도 깊이가 달라져 순위 비교가 오염된다.

**3단계. 각각 집계한다.**

```bash
python3 scripts/af3_collect.py 경량=pilot_light_out -o pilot_경량.csv
python3 scripts/af3_collect.py 정밀=pilot_full_out  -o pilot_정밀.csv
```

**4단계. 순위 상관을 잰다.** `--ref` 가 정밀(기준) 쪽, `--test` 가 경량 쪽이다.

```bash
python3 scripts/af3_rankcorr.py --ref pilot_정밀.csv --test pilot_경량.csv \
    --all-metrics --top-n 5,10,20,40 \
    -o 순위상관.csv --pairs-out 순위대응.csv
```

**5단계. discovery 결과를 기술하고 held-out validation 계약을 고정한다.** 판단 기준:

- primary metric, top-K, tie policy와 허용 miss-rate를 held-out 점수를 열기 전에 고정한다.
- rho/tau/top-K overlap은 target-level paired bootstrap confidence interval과 함께 보고한다.
- `--all-metrics`의 metric별 missingness가 다르면 공통 analysis population을 별도로 보고한다.
- discovery에서 가장 좋아 보이는 metric과 maximum-rank safety multiplier를 고른 뒤 같은
  pilot로 성능을 주장하지 않는다. held-out panel에서 top-K recall lower bound를 확인한다.
- held-out 근거가 없으면 safety multiplier는 descriptive pilot statistic이며 후보 제거에 쓰지 않는다.

### 구현 검산

`--selftest` 가 알려진 값으로 자기 자신을 확인한다. 손으로 계산할 수 있는 예를 쓴다.

```bash
python3 scripts/af3_rankcorr.py --selftest
```

검산 항목 (전건 통과 확인):

| 항목 | 손 계산 | 결과 |
|---|---|---|
| rho(1..5, 1..5) | 1.0 | 1.0 |
| rho(1..5, 5..1) | -1.0 | -1.0 |
| rho([1,2,3,4,5],[2,1,4,3,5]) | 1 - 6x4/(5x24) = 0.8 | 0.8 |
| rankdata([1,1,3]) | [1.5, 1.5, 3] | 일치 |
| tau_b([1,2,3,4],[1,2,4,3]) | 일치쌍 5, 불일치 1 → (5-1)/6 = 0.6667 | 0.6667 |
| tau_b([1,1,2],[1,2,3]) | 동점보정 2/sqrt((3-1)x(3-0)) = 0.8165 | 0.8165 |
| top3 겹침률 (a,b,c vs a,c,e) | 2/3 | 0.6667 |
| top3 안전배수 | b 가 경량 4위 → 4/3 | 1.3333 |

추가로 `scipy.stats` 대조를 별도로 했다(scipy 는 이 스크립트의 의존성이 아니다.
검증에만 썼다). **동점을 일부러 넣은 무작위 300쌍**(n=4~40, ranking_score 처럼 소수
둘째 자리로 반올림)에서 `spearmanr`/`kendalltau(variant='b')` 와 **전건 1e-9 이내 일치**.
p값 t근사도 `scipy.stats.t.sf` 와 소수 6자리까지 일치했다.

---

## 5. 전체 절차 - 2000건 실행 순서

각 단계에서 무엇을 보고 판단하는지와 예상 소요를 함께 적는다.
소요는 건당 측정값에서 계산한 것이고 **2000건 실물로 완주한 측정은 아니다**(그 부분은 추정).

**아래는 축소 DB(2 GB) 로 1단계를 돌리는 권장 구성 기준이다.** 데이터 파이프라인
건당 1.98초는 이 트랙에서 4건으로 실측했다. 전체 DB 급으로 돌린다면 이 단계가
건당 30.41초(4GB 슬라이스 실측) 또는 67.0초(0.895건/분, 다른 트랙의 전체DB 측정)로
커지고, 그때는 MSA 가 전체 시간을 지배한다.

### 0단계. 예비실험으로 컷오프를 정한다 (약 1시간)

4절의 절차. **이 단계를 건너뛰면 컷오프에 근거가 없다.**
40건을 두 설정으로 돌리고 `af3_rankcorr.py` 로 겹침률과 안전배수를 얻는다.

판단: 겹침률이 쓸 만한가. 안전배수가 몇인가. 어느 지표로 고를 것인가.

### 1단계. 전수 2000건 - MSA 만 먼저 (축소DB 약 1.1시간 / 전체DB 약 37시간)

MSA 는 경량/정밀 설정과 무관하게 같다. 한 번만 하고 재사용한다.

```bash
python3 scripts/af3_prepare.py --fasta vhh_2000.fasta -o vhh_in --report 입력표.csv
python3 scripts/run_af3_batch_improved.py --mode data \
    --input-dir vhh_in --output-dir vhh_out --yes
```

예상 소요 (DB 구성이 정한다):

| 1단계 DB | 건당 (근거) | 2000건 |
|---|---|---|
| 축소 DB 2GB | 1.98초 (이 트랙 4건 실측) | **1.1시간** |
| 4GB 슬라이스 4종 | 30.41초 (이 트랙 4건 실측, 첫건 페이지캐시 포함) | 16.9시간 |
| 같은 것, 정상상태만 | 9.1~9.6초 (이 트랙 실측, 2~4번째 건) | 5.1시간 |
| 전체 DB 850GB | 67.0초 (다른 트랙 14조합 스윕) | 37.2시간 |

스레드는 32~48 이 최적이고 그 이상은 오히려 느려진다(다른 트랙 측정).
축소 DB 에서는 1.98초/건이 이미 CPU 포화 근처가 아니어서 스레드 튜닝의 여지가
작을 수 있다(미측정).

판단: 중간에 `--audit` 로 완료/미완료를 본다. `--mode data` 의 완료 판정은
`<이름>_data.json` 존재다. 끊기면 같은 명령을 다시 돌린다(완료분은 건너뛴다).

### 2단계. 전수 2000건 - 경량 추론 (예상 1~2시간)

MSA 가 이미 `vhh_out/<타깃>/<타깃>_data.json` 에 있으므로 재사용한다.

```bash
# 1단계 data 실행을 집계한 뒤 전건을 _data.json 재사용 입력으로
python3 scripts/af3_collect.py 준비=vhh_out -o 준비완료.csv --no-msa-depth
python3 scripts/af3_stage2.py -c 준비완료.csv --all \
    --source data --from-out vhh_out -o vhh_light_in
python3 scripts/run_af3_batch_improved.py --mode inference \
    --input-dir vhh_light_in --output-dir vhh_light_out --yes
```

`--all` 은 선별하지 않고 전건을 쓴다(선별은 3단계에서 한다). `--top`/`--min` 과
함께 쓸 수 없다.

주의: `af3_collect.py` 는 최종 산출물이 있는 폴더만 완료로 센다. `--mode data` 직후에는
아직 추론 결과가 없으므로 이 집계가 0건이 나온다. 그때는 폴더 목록을 직접 만들어
`--list` 로 준다.

```bash
ls -d vhh_out/*/ | xargs -n1 basename > 준비목록.txt
python3 scripts/af3_stage2.py --list 준비목록.txt \
    --source data --from-out vhh_out -o vhh_light_in
```

`af3_batch.py --stage infer`는 준비된 `_data.json`으로 **data pipeline만 건너뛰는 stage
semantics**다. fidelity를 암묵적으로 바꾸지 않는다. `--diffusion-samples`와 `--recycles`를
생략하면 pinned AF3 기본값을 쓰고, 경량 exploratory run은 두 값을 명시한다. 따라서 기록과
비교에서는 stage(`infer`)와 fidelity parameters를 별도 열로 남긴다.

예상 소요: 기본 설정 추론이 5.39초/건이므로 2000건 = 3.0시간. 샘플 1 + recycle 3 은
그보다 짧을 것이다(**미측정** - 경량 설정의 추론 시간은 재지 않았다).

판단: `--audit` 로 완료 건수. VRAM 은 2,942~5,291 MiB 였으므로 16GB/32GB 카드에서
여유가 있다(`XLA_PYTHON_CLIENT_PREALLOCATE=false` 필요).

### 3단계. 집계하고 상위 후보를 뽑는다 (수 분)

```bash
python3 scripts/af3_collect.py 경량=vhh_light_out -o 1단계요약.csv --top 200 \
    --top-list top200.txt
```

판단: 등급 분포를 본다. `A_높음`/`B_신뢰` 가 몇 건인가. `MSA얕음` 경고가 몇 건인가.
`ranking검산차` 가 전건 일치인가(아니면 다른 실행의 파일이 섞인 것이다).
validation을 통과한 사전 지정 multiplier가 있을 때만 재실행 건수에 적용한다. 그렇지 않으면
이 단계는 exploratory ranking을 기록할 뿐 후보를 제거하지 않는다.

### 4단계. 상위 후보를 정밀 설정으로 재실행 (예상 1시간 이내)

```bash
python3 scripts/af3_stage2.py -c 1단계요약.csv --top 200 --by ranking_score \
    --source data --from-out vhh_light_out -o vhh_2단계_in --seeds 1,2,3
python3 scripts/run_af3_batch_improved.py --mode inference \
    --input-dir vhh_2단계_in --output-dir vhh_2단계_out --yes
```

`--seeds 1,2,3`으로 같은 모델/입력의 seed sensitivity를 기술할 수 있다. 집계기의
`ranking산포`는 각 실행 안 diffusion sample의 max-min range이며 독립 재현성이나 native
correctness uncertainty가 아니다.

예상 소요: 200건 x 5.39초 x 시드 3 = **약 54분** (MSA 는 건너뛴다).
MSA 를 다시 하면 축소 DB 에서 200건 x 1.98초 = 7분, 전체 DB 급에서
200건 x 30.41초 = 1.7시간(또는 0.895건/분으로 3.7시간)이 추가된다.
**축소 DB 로 스크리닝한다면 재사용의 시간 이득은 7분 수준이다.**
재사용을 쓰는 이유는 시간보다 3절의 동일성 - 1단계와 정확히 같은 MSA 를 쓴다는 것 - 이다.

### 5단계. 최종 집계와 플롯

```bash
python3 scripts/af3_collect.py 경량=vhh_light_out 정밀=vhh_2단계_out -o 최종요약.csv
# 두 단계의 순위가 실제로 어떻게 변했는지 (전수 데이터로 사후 확인)
python3 scripts/af3_rankcorr.py --csv 최종요약.csv \
    --ref-condition 정밀 --test-condition 경량 --all-metrics -o 최종_순위상관.csv
python3 scripts/af3_visualize.py vhh_2단계_out --lang ko
```

판단: 5단계의 `af3_rankcorr.py` 는 **사후 확인**이다. 재실행한 200건에서만 두 설정을
비교하므로 상위권에 치우친 표본이고, 겹침률은 정의상 낙관적으로 나온다
(경량이 안 뽑은 건은 정밀 결과가 없으므로 비교 대상에서 빠진다). 0단계의
무작위 예비실험이 진짜 근거다.

### 총계

| 단계 | 축소DB 구성 | 전체DB 구성 | 근거 |
|---|---|---|---|
| 0. 예비실험 40건 | 약 10분 | 약 50분 | 1.98 / 67.0초/건 + 추론 |
| 1. MSA 2000건 | 1.1시간 | 37.2시간 | 1.98초/건 (이 트랙 실측) / 0.895건/분 (다른 트랙) |
| 2. 경량 추론 2000건 | 3.0시간 이하 | 3.0시간 이하 | 5.39초/건 (다른 트랙). 경량은 더 짧을 것 (미측정) |
| 3. 집계 | 수 분 | 수 분 | |
| 4. 정밀 재실행 200건 x 3시드 | 약 54분 | 약 54분 | 5.39초/건, MSA 건너뜀 (3절 실측) |
| 합계 | **약 5시간** | **약 42시간** | |

**전체 시간을 무엇이 지배하는지가 DB 구성에 따라 뒤집힌다.**

- 축소 DB 로 스크리닝하면 MSA 가 1.1시간, 추론이 3~4시간이다. **추론이 지배한다.**
  이 경우 2단계 전략(전수 경량 + 상위 정밀)이 실제로 총시간을 줄인다.
  전수에 시드 3개 x 샘플 5개를 쓰면 추론이 9시간이 되는데, 2단계 전략은
  그것을 상위 200건에만 쓴다.
- 전체 DB 로 돌리면 MSA 가 37시간이라 추론을 아무리 줄여도 총시간이 안 준다.
  이 경우 2단계 전략의 값은 시간 단축이 아니라 아래 항목들이다.

DB 구성과 무관한 값:

- 상위 후보에만 시드/샘플을 더 쓸 수 있다.
- 경량 1단계 결과를 먼저 보고 전수 진행 여부를 판단할 수 있다.
- 상위 후보를 항원 복합체로 다시 돌리는 것 같은 **다른 계산**에 시간을 돌릴 수 있다.
- `_data.json` 재사용으로 1단계와 2단계가 **동일한 MSA** 를 쓴다(3절 실측).

이 트랙은 축소 DB 가 스크리닝에 충분하다는 판단(다른 트랙의 6건 측정)을 전제로 한다.
그 전제가 항원 복합체에서도 성립하는지는 측정되지 않았다.

---

## 6. 측정된 것과 측정되지 않은 것

### 측정된 것 (이 문서와 도구의 근거)

**이 트랙(트랙4)이 직접 측정한 것** - 검증 호스트 gpu-5070ti, VHH 단량체, 샘플5 x recycle10:

- `_data.json` 에 MSA/템플릿이 문자열로 직접 들어 있고, `*Path` 외부 참조가 없다.
- `_data.json` + `--norun_data_pipeline` 이 실제로 동작한다. AF3 로그에
  `Skipping data pipeline...` 이 찍히고 jackhmmer 가 실행되지 않는다.
- 데이터 파이프라인 건당: 축소DB 2GB **1.98초** (4건), 4GB 슬라이스 4종 **30.41초** (4건,
  첫건 91.70초 포함 / 2~4번째는 9.09~9.60초).
- 추론만 건당: `_data.json` 재사용 **11.30~13.85초** (캐시 데운 뒤, 4건/6건).
- 전체(파이프라인+추론) 건당: 축소DB **13.00~17.93초**.
- 재사용의 절약 = 전체 - 추론만. 축소DB 에서 **건당 1.7~5.3초**, 4GB 슬라이스 구성이면
  건당 약 30초.
- MSA 깊이 대조 (같은 4건): 축소DB unpaired 7/9/10/11 대 4GB슬라이스 91/119/128/138.
- **재사용의 재현성: 두 번 돌려 ranking_score/pLDDT평균이 소수점까지 동일** (6건).
- 재사용은 원본 실행의 MSA 깊이를 6건 전건 그대로 보존한다.
  MSA 를 새로 만들면 같은 축소DB 로도 깊이가 9→7, 13→10 로 달라진다.
- `af3_rankcorr.py` 의 rho/tau_b/p값이 scipy 와 일치 (동점 포함 무작위 300쌍, 1e-9 이내).
- `af3_stage2.py` 와 `af3_rankcorr.py` 를 실제 AF3 출력 6건으로 돌려 동작 확인.

**다른 트랙의 측정 (이 문서가 인용한 것)**:

- MSA 처리율 0.895건/분에서 포화 (전체 DB 급 4GB 슬라이스 4종 구성).
  스레드 32~48 이 최적, 그 이상은 하락.
- 추론 5.39초/건 (단일 프로세스 `--input_dir` 순회, 정상상태, 패딩 버킷 128).
  버킷 256 은 9.44초 (2.25배).
- 타깃마다 프로세스를 새로 띄우면 정상상태 추론이 7.78초 대 4.10초로 1.9배 느려진다.
- VRAM 피크 2,942~5,291 MiB (`XLA_PYTHON_CLIENT_PREALLOCATE=false`).
  nvidia-smi 의 15GB 는 JAX 선점량이지 수요가 아니다.
- 축소 DB vs 전체 DB, VHH 단량체 6건: MSA 깊이 800~1000배 차이인데 ranking score
  무변화 3 / +0.03 2 / -0.01 1.

### 측정되지 않은 것 (이 문서가 추정으로 표기한 것)

- **경량 설정(샘플 1, recycle 3)과 기본 설정 사이의 순위 보존.** 이 전략의 핵심 전제다.
  DB 크기 비교 6건은 이것의 대리 측정이 아니다. `af3_rankcorr.py` 는 이 공백을
  메우기 위한 도구이고, 값을 채우는 것은 사용자의 예비실험이다.
- **경량 설정의 추론 시간.** 샘플 1 + recycle 3 이 기본값의 몇 분의 일인지 재지 않았다.
- **2000건 실물 완주.** 5절의 총계는 건당 측정값에서 계산한 것이다.
- **안전배수의 규모 의존성.** 40건 예비실험의 안전배수를 2000건에 외삽하는 근거가 없다.
- 항원 복합체의 계면 신뢰도(ipTM)에 대한 축소 DB 영향. 단량체만 봤다.
- CDR3 잔기별 pLDDT 민감도.
- 정밀 재실행에서 시드를 늘렸을 때 순위가 얼마나 안정되는지.
- **전체 DB 850GB 에서의 데이터 파이프라인 시간.** 이 트랙은 축소 DB 와 4GB 슬라이스만 쟀다.
  850GB 구성의 67.0초/건은 다른 트랙의 값을 인용한 것이다.
- **재사용 시 VRAM.** 추론 중 별도 샘플링을 하지 못했다. 다른 트랙의 2,942~5,291 MiB 를
  인용했고, 재사용 경로가 그와 다른지는 재지 않았다.
- 복합체/리간드가 섞인 입력에서 `af3_stage2.py` 의 실제 재실행. sidecar 검출/거부까지만 확인했다.
- 4건/6건 규모다. 통계적 결론을 내릴 표본이 아니고, 경로가 동작하는지와
  건당 시간의 크기 order 를 확인한 것이다.

### 측정 과정에서 실제로 겪은 함정 (재측정할 사람을 위해)

- **JAX 컴파일 캐시를 데우지 않으면 A/B 가 뒤집힌다.** 첫 시도에서 재사용 조건을
  먼저 돌려 컴파일 비용을 전부 물렸더니 추론이 67~77초/건으로 나왔고, 뒤에 돌린
  전체 파이프라인 조건은 따뜻한 캐시로 7.88초/건이 나왔다. "재사용이 4배 느리다" 는
  잘못된 결론이 나올 수 있었다. 측정 전에 버려지는 warm-up 실행을 반드시 넣어라.
- **비교하는 두 입력 폴더의 타깃 집합이 같은지 확인하라.** `--top 4` 로 만든 재사용
  입력과 손으로 고른 원본 입력이 1건 어긋나서 점수 비교가 3건으로 줄었다.
  두 번째 측정에서 6건으로 맞췄다.
- **`/proc/loadavg` 를 매 측정마다 기록하라.** 이 호스트는 부하에 따라 타이밍이 크게
  흔들린다. 위 측정의 load1 은 1.99~7.08 이었다.
- 4GB 슬라이스 첫 타깃이 91.70초, 나머지가 9초대였다. 페이지 캐시 상태가 첫 건을
  지배한다. 건당 평균을 낼 때 첫 건을 포함하는지 명시하라.

---

## 7. 알려진 제약

- `run_af3_batch_improved.py` 에 `--num_diffusion_samples`/`--num_recycles` 옵션이 없다.
  경량 설정을 쓰려면 `af3_batch.py --diffusion-samples/--recycles` 를 쓰거나
  `run_alphafold.py` 를 직접 부른다. `af3_stage2.py` 는 입력 JSON 만 만들고 설정 플래그는
  건드리지 않는다(AF3 입력 JSON 에 샘플/recycle 을 넣는 자리가 없다 - 명령행 플래그다).
- `af3_stage2.py --source data` 로 만든 JSON 은 MSA 를 담고 있어 건당 약 0.8 MB 다.
  2000건이면 약 1.6 GB. 디스크를 본다.
- `--list` 로 이름만 주면 1단계 점수를 알 수 없어 `2단계_선정내역.csv` 의 점수 열이 빈다.
  점수를 남기려면 `-c` 로 CSV 를 준다.
- 검증은 VHH 단량체로만 했다. 복합체/리간드가 섞인 입력에서 sidecar 처리는
  검출/거부까지만 확인했고(경로가 있으면 건너뛴다), 실제 복합체 재실행은 돌리지 않았다.
