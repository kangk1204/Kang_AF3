# 타깃명 정규화 - 문제, 정한 정책, 검증 내역

작성 2026-08. 대상 스크립트: `af3_collect.py`, `af3_visualize.py`, `af3_batch.py`.
검증 호스트: RTX 5070 Ti 16GB, AF3 conda 네이티브(commit 97d2023), 축소 DB.

이 문서는 "왜 이렇게 고쳤는가"를 남기는 것이 목적이다. 사용법만 필요하면
`docs/operations_guide.md` 를 봐라.

---

## 1. 문제

### 1.1 AF3 는 재실행할 때 폴더 이름을 바꾼다

AF3 소스에서 직접 확인한 사실이다 (`~/af3_work/alphafold3`, commit 97d2023).

`run_alphafold.py` 861~866행:

```
if not force_output_dir and output_dir.exists() and any(output_dir.iterdir()):
    new_output_dir = (
        output_dir.parent
        / f'{output_dir.name}_{datetime.datetime.now().strftime("%Y%m%d_%H%M%S")}'
    )
```

즉 출력 폴더가 이미 있고 **비어 있지 않으면** `<폴더명>_<YYYYmmdd_HHMMSS>` 폴더를
새로 만든다. 중요한 것은 **폴더 이름만 바뀌고 그 안의 파일 stem 은 원래 타깃명
그대로**라는 점이다. 파일 이름은 `fold_input.sanitised_name()` 에서 오고
그 값은 폴더 이름과 무관하다.

`folding_input.py` 1054행:

```
def sanitised_name(self) -> str:
    spaceless_name = self.name.replace(' ', '_')
    allowed_chars = set(string.ascii_letters + string.digits + '_-.')
    return ''.join(l for l in spaceless_name if l in allowed_chars)
```

공백을 `_` 로 바꾸고 `[A-Za-z0-9_-.]` 만 남긴다. **소문자화는 하지 않는다.**
출력 폴더 이름은 입력 파일명이 아니라 JSON 의 `name` 을 이 규칙으로 정규화한 값이다.

### 1.2 하위 도구가 폴더 이름을 타깃명으로 썼다

`af3_batch.py` 는 `find_result_dirs` 로 타임스탬프 접미사 폴더를 인식했지만,
`af3_collect.py` 와 `af3_visualize.py` 는 폴더 이름을 그대로 타깃명으로 썼다.

고치기 전 재현 (가짜 출력 폴더, `tests/make_naming_fixture.py`):

```
타깃                            rank     비고
VHH_001                        0.8123
VHH_002                        0.7551
VHH_004_20260820_101010        0.7702   <- 타깃은 VHH_004 다
VHH_004_variantB               0.59
VHH_005                        0.61     <- 같은 타깃이
VHH_005_20260820_120000        0.88     <- 두 줄로 나뉘었다
VHH_006                        0.74
zzz_folder_9                   0.9012   <- 타깃은 VHH_009 다
행수 8
```

`--top 3` 의 결과도 그대로 오염됐다:

```
zzz_folder_9
VHH_005_20260820_120000
VHH_001
```

이 목록이 2단계 전략의 재실행 후보로 쓰인다. 즉 **존재하지 않는 이름으로
재실행 목록을 만들고 있었다.** `af3_visualize.py` 도 같은 이름으로 그림 파일을
만들고 PyMOL 객체 이름을 붙였으므로, 집계표와 그림의 이름이 서로 어긋났다.

### 1.3 영향 범위

2000건 배치에서 중단·재실행이 섞이면:

- 집계표에 타깃 수보다 많은 행이 생긴다. 그 행이 어느 타깃인지 사람이 폴더명에서
  역산해야 한다.
- `--top 100` 이 실제로는 100개보다 적은 타깃을 고른다 (같은 타깃이 두 번 뽑힌다).
  몇 개가 중복인지 사용자에게 알려주지도 않았다.
- `--top-list` 로 만든 재실행 목록의 이름이 실제 타깃명과 달라, 그 목록으로
  다시 입력 JSON 을 고르면 매칭이 안 된다.
- 그림 파일 이름과 집계표의 이름이 달라 대조가 안 된다.

### 1.4 곁에서 발견한 두 가지 (같이 고쳤다)

**(a) `af3_batch.py` 의 `sanitise_name` 이 소문자화를 했다.**

```python
# 고치기 전
def sanitise_name(name):
    return re.sub(r"[^a-z0-9_-]", "_", str(name).lower())
```

실물 AF3 는 소문자화하지 않는다(1.1 참고). 리눅스는 대소문자를 구분하므로
타깃 `VHH_001` 의 결과 폴더 `VHH_001` 을 `vhh_001` 로 찾아 못 찾았다.
gpu-5070ti(리눅스)에서 실측한 결과:

```json
{"sanitise_VHH_001": "vhh_001",
 "find_VHH_001": [],  "find_VHH_004": [],  "find_VHH_005": []}
```

`find_result_dirs` 가 **빈 목록**을 돌려줬다. 결과적으로

- 완료된 건을 건너뛰지 못하고 전부 다시 돌렸다 (2000건 배치에서 치명적이다).
- 실행 후 완료 판정도 전건 실패로 기록됐다.

macOS 에서는 파일시스템이 대소문자를 구분하지 않아 이 버그가 드러나지 않는다.
이 저장소의 실제 사용 환경은 리눅스다.

또 예전 구현은 마침표를 `_` 로 바꿨는데 실물은 마침표를 남긴다
(`VHH-004.v2` -> 실물 `VHH-004.v2`, 예전 구현 `vhh-004_v2`).

**(b) `outdir_is_complete` 가 완료 표식 하나만 있어도 완료로 봤다.**

```python
# 고치기 전
DONE_MARKERS = ("_summary_confidences.json", "ranking_scores.csv", "_model.cif")
return any(any(n.endswith(m) for m in DONE_MARKERS) for n in names)
```

리눅스에서 실측:

| 폴더 상태 | 고치기 전 판정 | 정식 기준 |
|---|---|---|
| `_summary_confidences.json` 만 있다 | 완료 | 미완료 |
| 정식 3종이 모두 0바이트 | 완료 | 미완료 |
| `_data.json` 만 있다 (추론 중 끊김) | 미완료 | 미완료 |

AF3 는 `write_fold_input_json` 을 추론 **전**에 호출한다. 그리고 추론이 끝날 때
세 파일이 함께 나온다. 그래서 하나만 보고 판정하면 끊긴 결과를 완료로 오인한다.
`run_af3_batch_improved.py` 의 `is_complete` 는 이미 3종을 요구하고 있었으므로,
같은 저장소 안에서 두 러너의 판정 기준이 달랐다.

---

## 2. 정한 정책

### 2.1 타깃명은 폴더명이 아니라 산출물 파일의 stem 에서 얻는다

폴더 이름은 AF3 가 재실행 때 바꾸지만 파일 stem 은 바꾸지 않는다(1.1).
그러므로 stem 이 타깃명의 정본이다.

stem 을 신뢰할 수 없는 경우의 처리를 **규칙으로 고정했다**
(`resolve_result_dir` 의 독스트링에도 같은 내용이 있다):

| 상황 | 타깃명 | 완료 판정 | 사용자에게 알리는가 |
|---|---|---|---|
| (a) 산출물 파일이 하나도 없다 | 폴더명에서 `_YYYYmmdd_HHMMSS` 를 떼어낸 값 | 미완료 | note: "산출물 파일이 없다" |
| (b) 완료 stem 이 정확히 하나 | 그 stem | 완료 | 알리지 않는다 (정상) |
| (c) 완료 stem 이 여러 개 | 폴더명 일치 > 타임스탬프 뗀 폴더명 일치 > 사전순 첫 번째 | 완료 | note 로 섞인 stem 전부 표시 |
| (d) 완료 stem 은 없고 미완료 stem 만 | (c) 와 같은 규칙 | 미완료 | 미완료 목록에 타깃명으로 |

(c) 에서 "사전순 첫 번째"를 고른 이유는 **임의로 고르지 않기 위해서**다.
같은 폴더를 두 번 집계했을 때 답이 달라지면 사용자가 원인을 못 찾는다.
`tests/test_naming.py` 가 두 번 판정한 결과가 같은지 검사한다.

(a) 에서 타임스탬프를 떼어내는 것은 **되돌림 경로일 뿐 1순위가 아니다.**
타깃명 자체가 `run_20260820_101010` 처럼 끝나는 경우가 있을 수 있는데,
stem 이 1순위이므로 산출물이 있는 폴더에서는 잘리지 않는다. 이것도 테스트에 있다.

### 2.2 같은 타깃이 여러 폴더에 있으면 기본으로 최신 1건만 집계한다

**정한 것**: 집계표는 한 타깃 = 한 줄. 최신 실행 1건만 쓴다.

**근거**

1. 이 CSV 의 용도는 상위 후보 선별이다. 같은 타깃이 두 줄이면 `--top 100` 이
   실제로는 90여 개 타깃만 고르게 되고, 사용자는 그 사실을 알 수 없다.
   한 타깃 = 한 줄이어야 순위와 컷오프가 뜻을 갖는다.
2. 대상 사용자는 실험 기반 초보 연구자다. "어느 줄이 최신인가"를 폴더명의
   타임스탬프로 판독하게 만드는 것은 오해를 부른다. 최신 판정은 도구가 하고,
   근거를 열에 적어 확인할 수 있게 하는 편이 안전하다.
3. 버린 실행을 감추지는 않는다:
   - `실행수` 열에 그 타깃이 몇 개 폴더에 있는지 적는다.
   - `중복정책` 열에 `최신선택(2개중)` 처럼 무슨 일이 있었는지 적는다.
   - `폴더명` / `실행시각` 열에 실제로 어느 폴더를 썼는지 적는다.
   - 화면 요약에 중복 타깃 목록과 선택된 폴더를 출력한다.
   - `--all-runs` 로 전부 볼 수 있다.

**최신의 기준**

1순위는 폴더명의 AF3 타임스탬프 접미사다. AF3 가 직접 찍은 값이라 파일 복사나
rsync 로 mtime 이 바뀌어도 살아남는다. 접미사가 없는 폴더(첫 실행)는 산출물
파일의 mtime 중 가장 늦은 것을 쓴다. 접미사 없는 폴더는 정의상 첫 실행이므로
접미사 있는 폴더보다 항상 오래된 것으로 정렬된다(첫 실행이 있어야 두 번째
실행에서 접미사가 붙는다).

**`--top` 은 별도로 한 번 더 막는다.** `--all-runs` 를 쓰거나 조건(`라벨=경로`)이
여러 개면 집계표에 같은 타깃이 여러 줄일 수 있다. 상위 N '건' 은 상위 N '타깃'
이어야 뜻이 있으므로, 상위 선별 단계에서 타깃 단위로 중복을 걷어내고
(정렬 후 첫 행만 남기므로 각 타깃의 최고값이 남는다) 몇 개를 걷어냈는지 알린다.
조건이 여러 개일 때는 `(조건, 타깃)` 이 한 단위다 — 그때는 조건 비교가 용도이므로.

### 2.3 완료 판정을 단계 인식으로 통일했다

정식 완료 기준(`run_af3_batch_improved.py` 와 동일):

- `<타깃>_ranking_scores.csv`
- `<타깃>_model.cif` **또는** `<타깃>_model.cif.zst`
  (`--compress_large_output_files` 를 쓰면 후자다)
- `<타깃>_summary_confidences.json`

세 묶음이 **모두** 있고 **크기가 0보다** 클 때만 완료다.

`af3_batch.py` 는 `--stage msa` 가 있어 단계마다 완료의 뜻이 다르다. 그래서
판정 함수가 단계를 받는다:

| `--stage` | 판정 모드 | 완료의 근거 |
|---|---|---|
| `msa` | `data` | `<타깃>_data.json` 이 있고 크기 > 0 |
| `infer`, `both`, `oneshot` | `full` | 정식 3종 모두 |

이 매핑은 `stage_check_mode()` 한 곳에 있고 테스트가 네 단계 전부를 검사한다.

### 2.4 점(.)으로 시작하는 항목 제외는 의도다

배치 러너가 출력 폴더 안에 만드는 관리용 항목이 전부 점으로 시작한다:

| 항목 | 내용 | 집계에 섞이면 |
|---|---|---|
| `.af3_incomplete/` | 미완료 결과 격리 보관소 | 미완료 결과가 완료로 집계된다 |
| `.af3_pending_*/` | 실행 중 staging | 아직 결과가 아닌 것이 집계된다 |
| `.run_af3_batch.lock` | 중복 실행 방지 lock | 폴더가 아니므로 무해하지만 명시적으로 막는다 |
| `._*` | macOS AppleDouble 사이드카 | 읽는 순간 UnicodeDecodeError |

고치기 전에도 세 도구가 우연히 모두 점으로 시작하는 항목을 건너뛰고 있었다.
그것이 우연이 아니라 약속임을 코드와 테스트로 못박았다:

- `is_sidecar` 독스트링에 무엇을 왜 막는지 항목별로 적었다.
- `run_af3_batch_improved.py` 의 `is_safe_output_name` 도 `.af3_` 로 시작하는
  이름을 결과 이름으로 인정하지 않는다. 양쪽이 같은 약속을 지킨다.
- `tests/test_naming.py` [3] 이 세 스크립트 각각에 대해 네 종류의 이름을 검사하고,
  격리 폴더 안의 `VHH_003` 과 staging 의 `VHH_010` 이 CSV 에 없는지 확인한다.

한 가지 덧붙였다. 격리 폴더 안의 결과는 `resolve_result_dir` 로 직접 판정하면
stem(`VHH_003`)은 읽히지만 `complete=False` 다 — 정식 3종이 없으므로.
즉 실수로 격리 폴더를 집계 대상으로 넘겨도 완료로 세지 않는다. 이중 방어다.

### 2.5 코드를 왜 공용 모듈로 빼지 않았는가

`FINAL_SUFFIX_GROUPS` / `is_sidecar` / `resolve_result_dir` / `dir_run_time` 등을
세 스크립트에 **같은 내용으로 복사**해 넣었다. 공용 모듈(`af3_common.py`)을
만들지 않은 이유는, 이 저장소의 사용자가 스크립트를 개별 파일로 작업 폴더에
복사해 쓰기 때문이다(`docs/operations_guide.md` 70행: "af3_batch.py, af3run.sh,
af3_check.sh, af3_collect.py 를 작업 폴더에 둔다"). import 의존성을 새로 만들면
한 파일만 복사한 사용자가 ImportError 로 막힌다.

대신 한쪽만 고치는 사고를 테스트로 막았다. `tests/test_naming.py` [5] 가
가짜 출력 폴더의 모든 폴더에 대해 세 사본의 판정 결과가 같은지 대조한다.
한 곳만 고치면 이 테스트가 실패한다.

---

## 3. 바뀐 것 / 안 바뀐 것

### 3.1 기존 사용법은 그대로다

기존 CSV 열의 **이름과 순서를 바꾸지 않았다.** 새 열 4개
(`폴더명`, `실행시각`, `실행수`, `중복정책`)를 맨 뒤에 붙였다. 기존 CSV 를
읽던 스크립트나 엑셀 서식은 계속 동작한다.

기존 옵션은 모두 그대로다. 추가한 옵션:

| 스크립트 | 옵션 | 뜻 |
|---|---|---|
| `af3_collect.py` | `--all-runs` | 같은 타깃의 결과를 전부 집계한다 (기본은 최신 1건) |
| `af3_visualize.py` | `--all-runs` | 같은 타깃을 전부 그린다. 파일명에 실행시각이 붙는다 |
| `af3_visualize.py` | `--include-partial` | 정식 완료가 아닌 폴더도 그린다 (예전 동작) |
| `af3_batch.py` | `--lenient-done` | 완료 표식 하나만 있어도 완료로 본다 (예전 동작) |

### 3.2 기본 동작이 바뀐 것 (명시)

| 무엇 | 예전 | 이제 | 옛 동작으로 되돌리는 법 |
|---|---|---|---|
| 집계표의 `타깃` 열 | 폴더명 | 산출물 파일 stem | 없다 (예전 값은 틀린 값이었다) |
| 같은 타깃의 여러 결과 | 전부 별개 행 | 최신 1건 | `--all-runs` (단 이름은 여전히 stem) |
| `af3_visualize.py` 가 그리는 폴더 | summary 파일만 있으면 그렸다 | 정식 3종이 있는 폴더만 | `--include-partial` |
| `af3_batch.py` 완료 판정 | 표식 1개 | 정식 3종 + 크기>0 (단계 인식) | `--lenient-done` |
| `af3_batch.py` `sanitise_name` | 소문자화 + `.` 를 `_` 로 | 실물 AF3 와 동일 | 없다 (예전 값은 틀린 값이었다) |
| `af3_visualize.py --only` | 폴더명으로 골랐다 | 타깃명으로 고른다 (폴더명도 받는다) | 불필요 (둘 다 동작한다) |
| `af3_batch.py find_result_dirs` | glob 접두어 | stem 확인 | 없다 (접두어는 별개 타깃을 오탐했다) |

`--lenient-done` 을 남긴 이유: 이미 돌려둔 예전 출력 폴더가 새 기준으로는
미완료로 판정될 수 있다. 그러면 재실행이 시작되어 사용자가 놀란다. 옛 기준으로
넘어가고 싶을 때 쓸 수 있게 남겼다. 다만 켜면 끊긴 결과를 완료로 셀 수 있다는
사실을 실행 로그에 출력한다.

`af3_visualize.py --only` 는 예전에 폴더명으로 골랐는데, 재실행 폴더의 타임스탬프는
사용자가 미리 알 수 없으므로 타깃명 쪽이 맞다. 폴더명을 줘도 받아주므로
예전 사용법이 깨지지 않는다 (테스트 [2] 가 둘 다 검사한다).

---

## 4. 재현 절차와 검증 결과

### 4.1 가짜 출력 폴더로 재현하기

```bash
# 1. 문제 상황을 그대로 만든다
python3 tests/make_naming_fixture.py /tmp/fx

# 2. 집계해 보고 타깃 열을 확인한다
python3 scripts/af3_collect.py /tmp/fx --no-msa-depth -o /tmp/after.csv

# 3. 자동 검사 (matplotlib 있으면 125건, 없으면 122건)
python3 tests/test_naming.py
```

가짜 출력 폴더에 들어 있는 함정 (`tests/make_naming_fixture.py`):

| 항목 | 검증하려는 것 |
|---|---|
| `VHH_001/`, `VHH_002/` | 정상 완료. 단량체와 복합체(ipTM 경로) |
| `VHH_004_20260820_101010/` (stem=`VHH_004`) | AF3 재실행 폴더 |
| `VHH_005/` + `VHH_005_20260820_120000/` | 같은 타깃이 두 폴더에 |
| `VHH_004_variantB/` | 접두어가 겹치는 별개 타깃 (glob 오탐 함정) |
| `VHH_006/` (stem 두 개) | 한 폴더에 stem 이 섞인 경우 |
| `VHH_007/` (`_data.json` 만) | 추론 중 끊김 |
| `zzz_folder_9/` (stem=`VHH_009`) | 폴더명과 stem 이 완전히 다르다 |
| `.af3_incomplete/VHH_003/<스탬프>/` | 격리된 미완료 결과 |
| `.af3_pending_1234/`, `.run_af3_batch.lock` | staging 과 lock |
| `._VHH_099/` | macOS AppleDouble 사이드카 |

### 4.2 고치기 전 / 후 대조 (실제 실행 결과)

`af3_collect.py` 집계표의 `타깃` 열:

| 고치기 전 | 고친 후 (타깃) | 고친 후 (폴더명 열) |
|---|---|---|
| `VHH_001` | `VHH_001` | `VHH_001` |
| `VHH_002` | `VHH_002` | `VHH_002` |
| `VHH_004_20260820_101010` | `VHH_004` | `VHH_004_20260820_101010` |
| `VHH_004_variantB` | `VHH_004_variantB` | `VHH_004_variantB` |
| `VHH_005` (rank 0.61) | (최신에 흡수됨) | - |
| `VHH_005_20260820_120000` (rank 0.88) | `VHH_005` (rank 0.88, 실행수 2) | `VHH_005_20260820_120000` |
| `VHH_006` | `VHH_006` | `VHH_006` |
| `zzz_folder_9` | `VHH_009` | `zzz_folder_9` |
| **8행** | **7행** (= 실제 타깃 수) | |

`--top 3 --top-list`:

| 고치기 전 | 고친 후 |
|---|---|
| `zzz_folder_9` | `VHH_009` |
| `VHH_005_20260820_120000` | `VHH_005` |
| `VHH_001` | `VHH_001` |

`af3_visualize.py` 가 만드는 그림 파일 이름:

| 고치기 전 | 고친 후 |
|---|---|
| `VHH_004_20260820_101010_plddt.png` | `VHH_004_plddt.png` |
| `zzz_folder_9_plddt.png` | `VHH_009_plddt.png` |
| `VHH_005_plddt.png` + `VHH_005_20260820_120000_plddt.png` (2장) | `VHH_005_plddt.png` (최신 1장) |

PyMOL 스크립트의 객체 이름도 타깃명이 되었고, 경로는 실제 폴더를 가리킨다:

```
load .../VHH_004_20260820_101010/VHH_004_model.cif, VHH_004
load .../zzz_folder_9/VHH_009_model.cif, VHH_009
```

### 4.3 자동 검사

`python3 tests/test_naming.py` -> **전체 통과 (125건)**. 검사 항목:

| 절 | 내용 | 건수 |
|---|---|---|
| [1] | `af3_collect.py` 타깃명, 중복 정책, `--all-runs`, `--top` | 29 |
| [2] | `af3_visualize.py` 타깃명, 뷰어 스크립트, 그림 파일명, `--only` | 21 |
| [3] | 격리/staging/lock 제외 (세 스크립트 각각) | 19 |
| [4] | `af3_batch.py` `sanitise_name`/`find_result_dirs`/완료 판정 | 24 |
| [5] | 정본 블록 세 사본이 같은 답을 내는가 | 24 |
| [6] | stem 을 신뢰할 수 없는 경우의 규정된 처리 | 8 |
| | **합계** | **125** |

이 표의 숫자를 손으로 세어 적지 말 것. 스크립트가 마지막에 절별 건수와 합계를
직접 출력하므로 그 값을 그대로 옮긴다. (처음에는 손으로 적었다가 표의 합이 실제
총계와 4건 어긋나 있었다. 그래서 스크립트가 세게 바꿨다.)

AF3 실물도 도커도 필요 없다. 표준 라이브러리만 쓴다.
matplotlib 이 없는 환경에서는 [2] 의 그림 관련 검사 3건을 건너뛰고 **122건**이 된다
(실측 확인: `PYTHONPATH` 로 matplotlib import 를 막고 돌려 122건 전체 통과).
`af3_visualize.py` 는 matplotlib 이 없어도 스크립트와 표는 만들기 때문에 종료코드가
0 이다. 그래서 테스트는 종료코드가 아니라 matplotlib 을 직접 import 해 보고
건너뛸지 정한다.

### 4.4 리눅스에서 실측한 것

macOS 는 파일시스템이 대소문자를 구분하지 않아 1.4(a) 버그가 드러나지 않는다.
그래서 gpu-5070ti(리눅스)에서 고치기 전 코드를 직접 돌려 측정했다:

```json
{"sanitise_VHH_001": "vhh_001",
 "find_VHH_001": [], "find_VHH_004": [], "find_VHH_005": [],
 "complete_quarantined_VHH_003": true,
 "complete_VHH_007_partial": false,
 "complete_summary_only": true,
 "complete_zero_size": true}
```

읽는 법:
- `find_*` 가 전부 빈 목록 -> 대문자 타깃의 결과 폴더를 하나도 못 찾았다.
- `complete_summary_only: true` -> summary 파일 하나만 있는 폴더를 완료로 봤다.
- `complete_zero_size: true` -> 0바이트 파일 3개를 완료로 봤다.
- `complete_quarantined_VHH_003: true` -> 격리 폴더를 **직접** 넘기면 완료로 봤다.
  (평소에는 `is_sidecar` 가 막아 도달하지 않는다. 이제 정식 기준으로 `false` 다.)

### 4.5 AF3 실물로 타임스탬프 폴더 재현 (가장 확실한 검증)

가짜 폴더가 실물과 같은지 확인하기 위해, AF3 를 **같은 출력 폴더에 두 번** 돌려
타임스탬프 폴더를 실제로 만들었다. gpu-5070ti, conda 네이티브, 축소 DB,
VHH 단량체 127 aa 1건, 시드 1개 x 샘플 5개.

```bash
export PATH=$HOME/miniforge3/envs/af3/bin:$PATH   # jackhmmer/nhmmer 가 여기 있다
cd ~/af3_work/alphafold3
COMMON="--model_dir=$HOME/af3_models --db_dir=$HOME/public_databases \
        --jackhmmer_n_cpu=8 --nhmmer_n_cpu=8"
python run_alphafold.py --json_path=$IN/VHH_realtest.json --output_dir=$OUT $COMMON
python run_alphafold.py --json_path=$IN/VHH_realtest.json --output_dir=$OUT $COMMON
```

실제로 생긴 구조 (예측과 정확히 일치한다):

```
real_out/
  VHH_realtest/                      <- 1회차
    VHH_realtest_summary_confidences.json
    VHH_realtest_ranking_scores.csv
    VHH_realtest_model.cif
    VHH_realtest_confidences.json
    VHH_realtest_data.json
    seed-1_sample-0..4/
  VHH_realtest_20260820_104113/      <- 2회차. 폴더에는 타임스탬프가 붙었지만
    VHH_realtest_summary_confidences.json     파일 stem 은 VHH_realtest 그대로다
    VHH_realtest_ranking_scores.csv
    VHH_realtest_model.cif
    ...
```

### 4.6 실물 AF3 결과로 검증한 내용

**`af3_collect.py`** (같은 폴더, 같은 명령, 고치기 전/후):

| | 고치기 전 | 고친 후 |
|---|---|---|
| 집계 행 수 | 2 | 1 |
| 타깃 열 | `VHH_realtest` (0.86)<br>`VHH_realtest_20260820_104113` (0.86) | `VHH_realtest` (0.86) |
| 폴더명 열 | (없었다) | `VHH_realtest_20260820_104113` |
| 실행수 / 중복정책 | (없었다) | `2` / `최신선택(2개중)` |

고치기 전에는 **실제로 존재하지 않는 타깃 `VHH_realtest_20260820_104113`** 이
집계표에 들어갔다. 한 건을 두 번 돌린 것뿐인데 집계표에는 타깃이 두 개로 보였다.

**`af3_visualize.py`**: 타깃 1개로 인식하고 이름을 `VHH_realtest` 로 썼다.
PyMOL 스크립트도 마찬가지다 (경로는 실제 폴더, 객체 이름은 타깃명):

```
load .../real_out/VHH_realtest_20260820_104113/VHH_realtest_model.cif, VHH_realtest
```

**`af3_batch.py` 의 재실행 건너뛰기** — 실물 출력 폴더에 대해 리눅스에서 대조:

```json
{"before": {"sanitise": "vhh_realtest",
            "find_result_dirs": [],
            "skip_would_happen": false},
 "after":  {"sanitise": "VHH_realtest",
            "find_result_dirs": ["VHH_realtest", "VHH_realtest_20260820_104113"],
            "skip_would_happen": true}}
```

읽는 법: 고치기 전에는 완료된 타깃의 결과 폴더를 **하나도 찾지 못해**
(`find_result_dirs: []`) 이미 끝난 건을 다시 돌렸다. 고친 후에는 두 폴더를 모두
찾고 완료로 판정해 건너뛴다. 2000건 배치에서 중단 후 재실행할 때 직접 영향이 있다.

이 검증에서 부수적으로 확인한 것: 작업 브리핑에 적힌 `~/public_databases_reduced`
경로는 이 호스트에 없다. 축소 DB 는 `~/public_databases` (3.0 GB,
`reduced_db_stats.json` 이 함께 있다) 다. 처음에 브리핑대로 실행해
`FileNotFoundError` 로 실패했고 경로를 바꿔 다시 돌렸다.

---

## 5. 고치지 못한 것 / 남은 위험

- **`--all-runs` 와 `--top` 을 함께 쓰면 순위의 뜻이 흐려진다.** 상위 선별에서
  타깃 단위 중복은 걷어내지만, "최신"이 아니라 "최고값" 실행이 남는다.
  경고를 출력하되 막지는 않았다. 대조가 목적일 때 쓰는 조합이므로.
- **(c) 상황(한 폴더에 완료 stem 여러 개)의 선택 규칙은 임의성을 없앤 것일 뿐
  옳음을 보장하지 않는다.** 이런 폴더는 애초에 서로 다른 실행의 파일이 섞인
  비정상 상태다. note 로 알리고 `ranking검산차` 열로 짝이 맞는지 볼 수 있게 했지만,
  어느 stem 이 사용자가 원하는 것인지는 도구가 알 수 없다.
- **`_model.cif.zst` 는 읽지 못한다.** 완료 판정에는 인정하지만, `af3_visualize.py`
  는 zstd 를 표준 라이브러리로 풀 수 없어 잔기 매핑을 토큰 단위로 되돌리고
  구조 뷰어 명령에서 그 타깃을 뺀다. 그 사실을 실행 중에 알린다.
  (`zstd -d` 로 먼저 풀면 정상 동작한다.)
- **`af3_batch.py` 는 여전히 도커를 전제한다.** 이 트랙에서는 판정 로직만 고쳤다.
  검증 호스트에 도커가 없어 `af3_batch.py` 의 도커 실행 경로 전체는
  이 트랙에서 재검증하지 않았다 (`--dry-run` 과 판정 함수 단위 검사만 했다).
- **`af3_prepare.py` 는 손대지 않았다.** 입력 JSON 의 `name` 을 어떻게 정하는지가
  결국 폴더명을 결정하므로 함께 볼 가치가 있으나 이 트랙의 범위가 아니다.
