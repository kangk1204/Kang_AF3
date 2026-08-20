# 두 트랙 병합 기록 (af3_collect.py, af3_visualize.py)

작성 2026-08-20. 검증 호스트 `gpu-5070ti` (RTX 5070 Ti 16 GB, 24 CPU, Docker 없음,
AF3 는 conda 네이티브 `~/miniforge3/envs/af3`).

## 1. 무엇이 충돌했나

네 갈래 업그레이드 중 두 갈래가 같은 파일 2개를 각각 원본에서 갈라져 고쳤다.

| 파일 | 원본 | A안 (초보사용성) | B안 (타깃명정규화) | 병합 결과 |
|---|---|---|---|---|
| `af3_collect.py` | 563행 | 606행 | 858행 | 911행 |
| `af3_visualize.py` | 969행 | 1072행 | 1228행 | 1348행 |

B안을 기준(base)으로 두고 A안의 변경을 그 위에 얹었다. 근거는 변경 폭이다.
행 단위 유사도로 재면 `af3_collect.py` 는 A안이 원본과 0.955, B안이 0.746 이었다.
B안은 집계 로직 자체(타깃명 결정, 중복 정책, 완료 판정)를 다시 썼고 A안은
기본값과 옵션을 얹은 변경이다. 큰 쪽을 기준으로 삼고 작은 쪽을 재적용하는 편이
되돌릴 것이 적다.

병합 후 유사도 (실측):

| 파일 | 원본 대비 | A안 대비 | B안 대비 |
|---|---|---|---|
| `af3_collect.py` | 0.708 | 0.738 | 0.964 |
| `af3_visualize.py` | 0.801 | 0.857 | 0.937 |

B안 대비 추가/변경된 줄은 `af3_collect.py` 59줄, `af3_visualize.py` 142줄이다.
즉 병합은 B안 위에 A안의 얇은 층을 올린 형태다.

### 실제로 부딪힌 지점은 세 곳이다

**(가) 같은 함수에서 만난 곳 — `af3_visualize.py` 의 파일 이름 결정.**
A안은 폴더 전체 산출물 4개의 기본 이름을 ASCII 로 바꿨고, B안은 타깃별 그림
이름의 재료인 '타깃명' 을 폴더명에서 산출물 파일 stem 으로 바꿨다. 둘이
`main()` 의 같은 구간에서 만난다. 해소는 층을 나누는 것으로 했다.

- 타깃명은 B안이 정한다 (`find_targets` -> `resolve_result_dir`, stem 기준).
- 파일 이름 규약은 A안이 정하되 그 타깃명을 재료로 받는다
  (`out_names(filename_lang)` 가 폴더 전체 산출물 이름만 담당하고,
   타깃별 이름은 `"%s_plddt.%s" % (name, format)` 그대로 — 여기서 `name` 이
   B안의 타깃명이다).

실측 확인: 폴더명이 `zzz_folder_9` 인데 파일 stem 이 `VHH_009` 인 폴더에서
그림 이름이 `VHH_009_plddt.png` 로 나오고, 같은 실행에서 폴더 전체 요약 그림은
A안의 `confidence_overview.png` 로 나온다. 두 규약이 한 실행에서 동시에 산다.

**(나) A안이 놓친 곳 — 경고 메시지의 파일 이름이 상수로 박혀 있었다.**
A안의 `warn_no_matplotlib()` 는 `TABLE_NAME_EN` 등 ASCII 상수를 직접 찍었다.
`--filename-lang ko` 로 돌리면 실제로 만드는 파일은 `af3_시각화표.csv` 인데
경고는 `visualize_table.csv` 를 알려주게 된다. 초보 사용자가 폴더에서 파일을
못 찾는다. 병합에서 `warn_no_matplotlib(why, names)` 로 바꿔 실제로 만들 이름을
찍게 했다. A안의 의도(초보자가 다음에 무엇을 할지 알게 한다)를 살리려면 이렇게
해야 한다. 검사도 붙였다 (`tests/test_filename_lang.py` 절 3의 '경고 메시지가
실제로 만드는 이름(ko)을 적는다').

**(다) B안의 검사가 A안이 바꾼 이름을 읽고 있었다.**
`tests/test_naming.py` 가 `af3_시각화표.csv` / `pymol_색칠.pml` 을 열어
`name` 열을 확인한다. A안의 기본값 변경 뒤에는 그 파일이 없어
`FileNotFoundError` 로 죽었다 (병합 직후 실측). 이 검사의 목적은 '표 안의
name 열이 폴더명이 아니라 타깃명인가' 이고 파일 이름이 무엇이냐는 검사 대상이
아니다. 그래서 파일 이름을 모듈 상수 `VIS_TABLE` / `VIS_PYMOL` 로 뽑아 기본
이름을 따라가게 했다. 검사 건수와 절별 구성은 그대로 유지했다 (125건, 절별
29/21/19/24/24/8). 파일 이름 자체의 검사는 새 파일이 담당한다.

## 2. 어떤 결정을 어떤 근거로 우선했나

| 쟁점 | 채택 | 근거 |
|---|---|---|
| 기준 브랜치 | B안 | 변경 폭이 크고 집계 로직을 재작성했다 (유사도 0.746 대 0.955) |
| 타깃명 출처 | B안 (산출물 stem) | AF3 실물로 재현 확인된 버그를 고친 것이다. 폴더명을 쓰면 재실행이 별개 타깃이 된다 |
| 폴더 전체 산출물 이름 | A안 (ASCII 기본) | 저장소에 이미 커밋된 예시 파일이 ASCII 였다 (`results_example/af3_summary.csv`, `figures/confidence_overview.png`, `examples/viewer_pymol_plddt.pml`). 도구가 만드는 이름과 저장소 예시가 어긋난 것이 원래 문제다 |
| CSV 열 이름 | A안 (한글 유지) | A안의 판단을 존중했다. 이미 그 열을 참조하는 엑셀 시트가 조용히 깨지는 것을 막는다. 파일 이름과 달리 열 이름은 도구 연결에서 문제를 일으키지 않는다 |
| 공용 모듈 분리 | B안 (하지 않는다) | 사용자가 스크립트를 개별 파일로 작업 폴더에 복사해 쓴다. import 의존성을 만들면 한 파일만 복사한 사용자가 ImportError 로 막힌다. 정본 블록을 세 스크립트에 복사한 상태를 유지했고, 세 사본의 판정이 일치하는지 검사가 지킨다 |
| `--lang` 의 뜻 | 도구별로 다르게 | `af3_visualize.py` 는 그릴 것이 있으므로 `--lang` 이 '그림 안 라벨 언어' 라는 원래 뜻을 유지하고 파일 이름은 `--filename-lang` 이 맡는다. `af3_collect.py` 는 그릴 것이 없으므로 `--lang` 을 `--filename-lang` 의 별칭으로 뒀다. 두 도구의 옵션 이름은 `--filename-lang` 으로 통일했다 |

### 옵션 요약 (병합 후)

```
af3_collect.py    --filename-lang {en,ko}   출력 CSV 이름. 기본 en. --lang 은 별칭
af3_visualize.py  --filename-lang {en,ko}   출력 파일 이름. 기본 en
af3_visualize.py  --lang {ko,en}            그림 안 라벨 언어. 기본 ko (뜻이 다르다)
```

`-o` 로 직접 준 경로는 두 도구 모두 그대로 쓰고 기본값 변경 알림도 띄우지 않는다.

## 3. `af3run.sh` 와의 인터페이스

A안만 고친 파일이다 (`AF3RUN_FILENAME_LANG=ko` 환경변수). 확인해 보니
`af3run.sh` 는 `-o "$CSV"` 로 경로를 직접 주므로 `af3_collect.py` 의 기본값에
의존하지 않는다. 따라서 병합 결과와 인터페이스가 맞고 `af3run.sh` 는 고칠 것이
없었다. A안이 낸 파일을 그대로 뒀다 (150행).

말로만 확인하지 않고 실제로 돌렸다. 합성 AF3 출력을 `./demo_out` 으로 두고
`AF3RUN_FILENAME_LANG` 을 en/ko 로 바꿔 `bash af3run.sh demo collect` 를 실행한
결과가 각각 `demo_summary.csv` / `demo_결과요약.csv` 다 (검사 4건, 전건 통과).

`af3_batch.py` 는 B안만 고쳤다. 충돌이 아니므로 B안 것을 그대로 썼다
(바이트 동일 확인).

## 4. 검증 결과

문법 검사만이 아니라 전부 실행해서 확인했다.

### 4.1 회귀 검사 (로컬, python 3.11.15, matplotlib 3.11.0)

| 검사 | 결과 | 비고 |
|---|---|---|
| `tests/test_naming.py` | **125건 전건 통과** | B안의 성과. 절별 29/21/19/24/24/8 로 병합 전과 동일 |
| `tests/run_tests.py` | **42건 전건 통과, 실패 0** | 트랙3의 성과 |
| `tests/run_tests.py --strict` | **42건 전건 통과** | '실패 예상' 표시를 지운 뒤이므로 strict 에서도 같다 |
| `tests/test_filename_lang.py` | **75건 전건 통과** | 이번에 새로 쓴 것. A안의 성과를 지킨다 |
| `tests/verify_tests_catch_bugs.py` | **재주입 17/17건 잡음** | 검사가 실제로 버그를 잡는지 확인하는 메타 검사 |

**항목 12 의 '예상된 실패' 2건이 통과했다.** 실행기가 알려준 그대로다:

```
[39/42] 항목  12 test_timestamp_suffix_folders_are_not_separate_targets ... 통과
[40/42] 항목  12 test_timestamp_suffix_folders_are_normalized_in_visualization ... 통과
[안내] 아래 항목은 '실패 예상' 표시가 붙어 있는데 통과했다.
```

이 2건이 바로 B안이 고친 타깃명 정규화다. `tests/test_reporting.py` 의
`expect_fail_on_current=True` 두 곳을 지우고, 왜 지웠는지와 다시 실패하면
그것이 회귀라는 것을 주석으로 남겼다. 지운 뒤 `--strict` 로도 42건 전건 통과한다.

### 4.2 메타 검사에서 발견한 것 (병합이 만든 문제가 아니다)

`verify_tests_catch_bugs.py` 의 재주입 17번이 '치환 대상 0회' 로 건너뛰어졌다.
원인을 확인했다. 이 재주입은 `af3_batch.py` 의
`for p in sorted(output_dir.glob(s + "_*"))` 줄을 지우는 것인데, B안이
`find_result_dirs` 를 stem 대조 방식으로 다시 써서 그 줄이 사라졌다.
`af3_batch.py` 는 병합에서 손대지 않았고 B안 산출물과 바이트 동일하므로,
이것은 B안 단계에서 이미 낡아 있던 것이고 병합이 만든 문제가 아니다.
메타 검사 자신이 '재주입 목록을 갱신하라' 고 지시하므로 지금 코드에 같은 버그를
되살리는 형태로 재주입을 다시 겨냥했다 (stem 대조를 폴더명 대조로 되돌린다).
그 결과 17/17 전건을 잡는다. 재주입 실행 뒤 `af3_batch.py` 가 바이트 원복되는
것도 확인했다.

### 4.3 두 트랙 성과가 모두 살아있는지 — 직접 돌려서 확인

**A안 성과 1: 파일 이름.** `--filename-lang` 유무로 실제로 어떤 이름의 파일이
생기는지 돌려서 확인했다.

| 실행 | 생긴 파일 |
|---|---|
| `af3_collect.py <out>` | `af3_summary.csv` + 기본값 변경 알림 |
| `af3_collect.py <out> --filename-lang ko` | `af3_결과요약.csv`, 알림 없음 |
| `af3_collect.py <out> --lang ko` | `af3_결과요약.csv` (별칭이 동작한다) |
| `af3_collect.py <out> -o af3_결과요약.csv` | `af3_결과요약.csv`, 알림 없음 |
| `af3_visualize.py <out> -o vis` | `visualize_table.csv`, `confidence_overview.png`, `viewer_pymol_plddt.pml`, `viewer_chimerax_plddt.cxc` |
| 같은 것 `--filename-lang ko` | `af3_시각화표.csv`, `af3_요약.png`, `pymol_색칠.pml`, `chimerax_색칠.cxc` |

내용이 정말 같은지도 확인했다. 집계 CSV 는 en/ko/`-o` 세 경로가 바이트 일치,
시각화 표도 en/ko 바이트 일치, 뷰어 스크립트는 자기 이름을 적는 줄 외에 일치.

**A안 성과 2: matplotlib 처리.** 두 경우로 나눠 확인했다.

| 환경 | 병합 결과 | 대조: B안 원본 |
|---|---|---|
| matplotlib 이 아예 없다 | 종료코드 0, 산출물 3개 남음 | 종료코드 0, 산출물 3개 남음 |
| matplotlib 은 있고 pyplot 만 깨졌다 | **종료코드 0, 산출물 3개 남음** | **종료코드 1, 산출물 0개 (ImportError 트레이스백)** |

두 번째 줄이 A안이 고친 '산출물을 전부 잃던 크래시' 다. B안 단독에서는 그리는
시점(`plot_plddt` 안의 `import matplotlib.pyplot`)에 죽어서 표와 뷰어 스크립트까지
잃는다. 병합 결과는 `probe_matplotlib()` 가 pyplot 까지 미리 불러 보고 실패하면
그림만 건너뛴다. 재현 방법은 `sys.meta_path` 에 finder 를 끼워 해당 모듈만
ImportError 로 만드는 것이다 (`tests/test_filename_lang.py` 의
`make_import_blocker`). 메시지는 원인, 그래도 만들어진 파일 이름, 설치 명령,
권한 오류 대처, `--no-plot` 안내를 담는다.

**B안 성과.** 타깃명이 stem 기준인지, 중복 최신 1건 정책이 사는지, 격리/staging/
lock/사이드카가 빠지는지 — 125건 검사가 전건 통과하는 것으로 확인되고,
아래 실물 시나리오에서도 다시 확인했다.

### 4.4 새 검사가 실제로 회귀를 잡는지 (재주입 3건)

검사가 통과하는 것만으로는 그 검사가 의미 있다는 증거가 못 된다. 병합 결과에
버그를 되살려 넣어 봤다.

| 재주입 | 결과 |
|---|---|
| A안의 collect 기본 이름을 한글로 되돌린다 | 2건 실패로 잡았다 |
| A안의 pyplot 사전확인을 없앤다 (예전 방식) | 6건 실패로 잡았다 (pyplot 깨짐 경로 전부) |
| B안의 타깃명 정규화를 되돌린다 (`label = child.name`) | 2건 실패로 잡았다. 그림 이름이 `zzz_folder_9_plddt.png`, `VHH_004_20260820_101010_plddt.png` 로 나왔다 |

세 번째가 (가) 의 상호작용을 지키는 검사다. 재주입 뒤 두 스크립트 바이트 원복
확인했다.

### 4.5 합성 AF3 출력으로 전 경로 (로컬)

`tests/make_naming_fixture.py` 로 만든 폴더를 썼다. 요청된 시나리오가 모두 들어
있다: 타임스탬프 접미사 형제 폴더(`VHH_004_20260820_101010`,
`VHH_005_20260820_120000`), 폴더명과 stem 이 완전히 다른 폴더(`zzz_folder_9`
안에 `VHH_009_*`), 격리 보관소(`.af3_incomplete/VHH_003`), staging
(`.af3_pending_1234`), lock(`.run_af3_batch.lock`), AppleDouble 사이드카
(`._VHH_099`), 추론 중 끊긴 폴더(`VHH_007`), 접미사가 아닌 이름
(`VHH_004_variantB` — 접두어 glob 으로 잡으면 안 되는 것).

집계 7건, 미완성 1건(`VHH_007`, 타깃명으로 보고됨). 집계표의 '타깃' 열과
시각화표의 'name' 열이 같은 7개 집합으로 일치한다. 폴더명(`zzz_folder_9`,
타임스탬프 포함 이름)과 격리 대상(`VHH_003`, `VHH_010`)은 어느 쪽에도 없다.

### 4.6 실물 AF3 출력으로 전 경로 (검증 호스트)

합성 데이터만으로는 실제 AF3 파일을 읽는다는 증거가 안 된다. `gpu-5070ti` 의
기존 출력 `~/af3_db_track/af3out_reduced` (VHH 단량체 6건, 축소 DB)로 돌렸다.
GPU 는 쓰지 않았다 — 이미 있는 출력을 읽기만 했으므로 AF3 재실행이 필요 없다.
전송한 스크립트는 md5 로 로컬과 동일함을 확인했다
(`af3_collect.py` 6518794a…, `af3_visualize.py` 981bd68a…).

이 호스트의 `python3` 는 3.13.7 이고 **matplotlib 이 실제로 없다**
(`ModuleNotFoundError`). 즉 A안의 matplotlib 부재 처리를 모사가 아닌 실환경에서
확인한 것이다. 결과: 종료코드 0, `visualize_table.csv` +
`viewer_pymol_plddt.pml` + `viewer_chimerax_plddt.cxc` 3개가 남았고 경고 메시지가
그 3개 이름을 정확히 적었다.

집계 6건 실측값 (`ranking_score` / `pLDDT평균` / 등급):

```
vhh_7mfv_1  0.90  92.29  A_높음
vhh_4s11_1  0.88  90.03  A_높음
vhh_4qgy_1  0.87  87.99  B_신뢰
vhh_8v8k_1  0.85  88.18  B_신뢰
vhh_7a50_1  0.85  84.06  B_신뢰
vhh_7djx_1  0.82  82.56  B_신뢰
```

`ranking_score` 검산 전건 일치. mmCIF 의 `B_iso_or_equiv` 가 pLDDT 와 일치함도
확인됐다 (최대 차 0.0000~0.0100, mmCIF 소수 2자리 반올림 범위). ipTM 은 6건 모두
비어 있다 — 단량체이므로 AF3 가 null 을 준다. 열 이름은 한글로 유지됐고
(`조건`, `타깃`, `등급`, `경고`, ...) B안이 붙인 4개 열
(`폴더명`, `실행시각`, `실행수`, `중복정책`)도 그대로다.

실물 파일로 재실행/격리 시나리오도 만들어 확인했다. `vhh_4qgy_1` 을
`vhh_4qgy_1_20260820_101010` 으로 복사(폴더명에 타임스탬프, 파일 stem 은 원래
이름), `.af3_incomplete/vhh_7djx_1` 에 완료 결과를 넣고, `.af3_pending_9999`,
`.run_af3_batch.lock`, UTF-8 이 아닌 `._vhh_4qgy_1` 을 뒀다. 결과:

- 집계 2건 (`vhh_4qgy_1`, `vhh_4s11_1`). 재실행 형제 폴더가 별개 타깃이 되지 않았다.
- `vhh_4qgy_1` 의 `실행수=2`, `중복정책=최신선택(2개중)` 으로 근거가 남았다.
- 격리 폴더 안의 `vhh_7djx_1` 은 집계·시각화 어느 쪽에도 없다 (완료 결과였는데도).
- 사이드카를 건너뛰고 `find ... -name '._*' -delete` 를 알려준다.
- 집계표와 시각화표의 이름이 일치하고 폴더명 누출이 없다.

세 사본의 판정 일치도 실물에서 확인했다. `af3_collect.py` 와 `af3_visualize.py`
를 모듈로 불러 같은 입력을 먹였을 때 `is_sidecar` 6종과 `resolve_result_dir` 의
`target`/`complete` 가 전부 같은 답을 냈다. (`af3_batch.py` 를 포함한 세 사본
비교는 `tests/test_naming.py` 절 5의 24건이 담당하고 통과한다.)

## 5. 측정한 것과 측정하지 못한 것

**측정했다** (모두 이 세션에서 실행한 결과다):
검사 4종의 건수와 종료코드, 재주입 3건 + 메타 재주입 17건이 잡히는지,
`--filename-lang` 유무에 따라 생기는 파일 이름, 두 이름 모드의 내용 동일성,
matplotlib 부재/파손 두 경우의 종료코드와 남은 산출물, B안 원본과의 대조,
`af3run.sh` 를 en/ko 로 실제 실행한 결과, 합성 및 실물 AF3 출력으로 집계->시각화
전 경로, 세 사본 판정 일치.

**측정하지 못했다**:

- **그림 파일의 내용.** 검증 호스트에 matplotlib 이 없어 실물 AF3 출력으로
  `*_plddt.png` / `*_pae.png` / `confidence_overview.png` 를 그려 보지 못했다.
  로컬(matplotlib 3.11.0)에서는 합성 데이터로 그려 파일이 생기는 것과 이름이
  맞는 것까지 확인했다. 그림 내부가 실물 데이터에서 제대로 보이는지는 이번 병합의
  변경 범위가 아니지만(플롯 함수는 A안도 B안도 손대지 않았다) 확인하지 못한 것은
  맞다.
- **`--filename-lang` 을 쓰는 사용자가 실제로 겪는 혼란의 크기.** 기본값 변경이
  옳은 결정인지는 이 병합에서 판단하지 않았다. A안의 결정을 근거(저장소에 커밋된
  예시 파일 이름)와 함께 존중했을 뿐이다.
- **AF3 실물 재실행으로 타임스탬프 형제 폴더가 생기는 것.** 이번 세션에서는
  기존 폴더를 복사해 그 구조를 만들었다. 원래 재현은 B안 트랙이 AF3 실물로 했고
  (`run_alphafold.py:861`), 그 결과가 기존 출력 폴더에 남아 있다. GPU 를 짧게만
  쓰라는 제약에 따라 재실행하지 않았다.
- **2000건 규모에서의 동작.** 집계·시각화의 성능은 이번 병합의 변경 범위가
  아니어서 재지 않았다. `--max` 기본값 200 이 그대로다.

## 6. 병합으로 깨진 것

없다. 두 트랙의 검사(125건, 42건)와 새로 쓴 검사(75건), 메타 검사(17건)가 모두
통과한다. 4.2 의 낡은 재주입 1건은 병합이 아니라 B안 단계에서 이미 낡아 있던
것이고 갱신해서 해소했다.

한 가지 남긴 변경이 있다. `tests/test_naming.py` 가 읽는 파일 이름을 A안의 새
기본값으로 맞췄다 (3.의 (다)). 이 파일이 검사하려던 것(name 열이 타깃명인가)은
그대로이고 건수도 그대로지만, B안 트랙이 쓴 검사 파일을 병합자가 고쳤다는 사실은
남는다. 파일 이름 자체를 지키는 검사는 `tests/test_filename_lang.py` 로 따로
분리했으므로, 앞으로 어느 쪽 기본값이 바뀌어도 한쪽 검사가 잡는다.

## 7. 산출물

| 파일 | 상태 |
|---|---|
| `scripts/af3_collect.py` | 병합 (911행) |
| `scripts/af3_visualize.py` | 병합 (1348행) |
| `scripts/af3run.sh` | A안 그대로 (150행). 인터페이스 확인 후 수정 불필요 |
| `scripts/af3_batch.py` | B안 그대로 (바이트 동일). 충돌 아님 |
| `tests/test_naming.py` | 파일 이름 참조만 상수로 뽑음. 검사 125건 유지 |
| `tests/test_reporting.py` | 항목 12의 `expect_fail_on_current=True` 2곳 삭제 |
| `tests/verify_tests_catch_bugs.py` | 낡은 재주입 1건 갱신 (17/17 잡음) |
| `tests/test_filename_lang.py` | 새로 씀 (406행, 75건). A안의 성과를 지킨다 |
| `docs/merge_notes.md` | 이 문서 |

재현 명령:

```
python3 tests/test_naming.py            # 125건
python3 tests/test_filename_lang.py     # 75건
python3 tests/run_tests.py --strict     # 42건
python3 tests/verify_tests_catch_bugs.py  # 재주입 17건
```
