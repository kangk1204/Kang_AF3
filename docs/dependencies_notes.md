# 의존성과 출력 파일명 정책

작성 근거: 저장소 스크립트 6개의 import 문 전수 조사(AST 파싱)와, matplotlib 이 없는
환경에서의 실제 실행. 추정과 측정을 구분해 적었다.

---

## 1. 한 줄 요약

파이썬 스크립트 5개 중 **4개는 표준 라이브러리만** 쓴다. 설치할 것이 없다.
그림을 그리는 `af3_visualize.py` 하나만 **matplotlib** 이 필요하고, 그것마저 없어도
스크립트가 죽지 않고 표(CSV)와 뷰어 스크립트는 만든다.
(나머지 하나인 `af3_check.sh` 는 bash 라서 파이썬 의존성이 없다. 합쳐서 스크립트 6개다.)

```
# 그림까지 필요할 때만. 저장소 최상위에서
python3 -m pip install -r requirements.txt
```

---

## 2. 스크립트별 외부 의존성 (전수 조사 결과)

조사 방법: 각 파일을 `ast.parse` 로 읽어 모든 `Import`/`ImportFrom` 노드를 뽑고,
`sys.stdlib_module_names` 와 대조해 표준 라이브러리가 아닌 것만 남겼다.
함수 안에 숨은 지연 import 와 `try/except ImportError` 안의 import 까지 포함한다.

| 스크립트 | 행수 | 외부 의존성 | 없으면 어떻게 되는가 |
|---|---:|---|---|
| `run_af3_batch_improved.py` | 1255 | **없음** | — |
| `af3_batch.py` | 925 | **없음** | — |
| `af3_collect.py` | 563 | **없음** | — |
| `af3_prepare.py` | 839 | `rdkit` (선택) | SMILES heavy atom 수가 빈칸이 된다. 그 외 정상 |
| `af3_visualize.py` | 969 | **`matplotlib`** (필수) | 그림만 건너뛴다. 표와 뷰어 스크립트는 만든다 |
| `af3_check.sh` | 381 | 없음 (bash. 내부에서 `python3` 호출) | — |

### 쓰이는 표준 라이브러리 모듈

전 스크립트를 합쳐서: `argparse`, `collections`, `contextlib`, `csv`, `dataclasses`,
`datetime`, `fcntl`, `json`, `math`, `os`, `pathlib`, `re`, `shutil`, `socket`,
`statistics`, `string`, `subprocess`, `sys`, `tempfile`, `time`, `typing`, `__future__`.

`pandas` / `numpy` / `scipy` / `biopython` 은 **하나도 쓰지 않는다.** 이것은 우연이
아니라 의도된 것이다. 검증 호스트의 python3 에 이들이 없어서, 평균과 표준편차는
`statistics` 모듈로, CSV/JSON 은 `csv`/`json` 으로 처리했다.
(matplotlib 을 설치하면 numpy 가 의존성으로 함께 깔린다. 스크립트가 직접 쓰지는 않는다.)

### 파이썬 버전과 플랫폼

- **3.8 이상** 이 필요하다 (f-string, `pathlib`, `dataclasses` 사용).
- `run_af3_batch_improved.py` 는 `fcntl` 로 중복 실행을 막는다. `fcntl` 은
  **리눅스/macOS 전용** 이다. 윈도우 기본 파이썬에는 없으므로 이 러너를 쓸 수 없다.
- 측정: 검증 호스트 gpu-5070ti 의 시스템 python3 은 3.13.7, af3 conda 환경은 3.12.13.
  **두 곳 모두 matplotlib 이 없다.** 즉 이 저장소를 처음 쓰는 사람이 만나는
  기본 상태는 "그림을 그릴 수 없는 상태" 다. 그래서 3장의 수정이 필요했다.

### rdkit 을 필수로 넣지 않은 이유

`af3_prepare.py` 의 `smiles_heavy_atoms()` 안에서만 쓰이고, 이미
`try: ... except ImportError: return None` 으로 감싸여 있다. 없으면 heavy atom 수가
`None` 이 되어 패딩 버킷 추정이 덜 정확해지는 것이 전부다. VHH 단량체 스크리닝은
단백질만 다루므로 이 경로를 아예 지나지 않는다. 설치가 무거우므로
`requirements.txt` 에서는 주석으로 남겨 뒀다.

---

## 3. matplotlib 이 없을 때 무슨 일이 벌어지는가

### 고치기 전 (실측)

matplotlib 이 없는 별도 venv 를 만들어 실제로 돌려 확인했다. 두 가지가 나왔다.

**(가) matplotlib 이 아예 없을 때 — 죽지는 않았다. 다만 안내가 부족했다.**

```
주의: matplotlib 이 없어 그림을 그릴 수 없다. 스크립트와 표만 만든다.
      설치:  python3 -m pip install matplotlib
```

두 줄이 stderr 로 나가고 종료 코드 0 으로 끝났다. 기능은 맞지만,
이 두 줄은 이후 30여 줄의 정상 요약 출력에 묻힌다. 초보 사용자가 `> log.txt` 로
리다이렉트하면 stdout 만 저장되므로 **경고가 로그에서 사라진다**.
또 무엇이 만들어졌는지, `--no-plot` 이라는 조용한 대안이 있는지 알 수 없다.

**(나) matplotlib 은 있으나 `pyplot` 이 깨졌을 때 — 죽었다. 표까지 잃었다.**

이것이 실제 문제였다. 고치기 전 코드의 확인 방식은 이랬다.

```python
try:
    import matplotlib          # <- 최상위 패키지만 본다
    matplotlib.use("Agg")
except ImportError:
    have_mpl = False
```

`import matplotlib` 는 성공하지만 `import matplotlib.pyplot` 이 실패하는 환경이
현실에 있다 (freetype 공유 라이브러리 결손, 백엔드 미설치, 설치 중단으로 인한 부분 설치).
`plot_plddt()` 안에서 `import matplotlib.pyplot` 을 지연 import 하므로,
그리기 시점에 가서야 터진다. 재현 방법은 `pyplot` 과 `ft2font` 가 `ImportError` 를
던지는 가짜 matplotlib 패키지를 만들어 `PYTHONPATH` 로 주입하는 것이다.
변경 전 코드(`git show HEAD:scripts/af3_visualize.py`)로 실제 돌린 출력을 그대로 옮긴다:

```
주의: 타깃이 4개다. 개별 그림은 앞 1개만 만든다 (--max 로 조절).
Traceback (most recent call last):
  File "/private/tmp/orig/af3_visualize.py", line 969, in <module>
    sys.exit(main())
             ^^^^^^
  File "/private/tmp/orig/af3_visualize.py", line 891, in main
    plot_plddt(res, name, summ, p, ok)
  File "/private/tmp/orig/af3_visualize.py", line 422, in plot_plddt
    import matplotlib.pyplot as plt
  File "./testbed/brokenmpl/matplotlib/pyplot.py", line 1, in <module>
    raise ImportError("libfreetype.so.6: cannot open shared object file")
ImportError: libfreetype.so.6: cannot open shared object file
EXIT=1
산출물 개수: 0
```

종료 코드 1, 산출물 0개. 표를 쓰는 데까지 가지 못하고 죽으므로
**이미 계산한 신뢰도 값을 전부 잃는다.** 2000건을 돌린 뒤 이걸 만나면 손해가 크다.

### 고친 뒤 (실측)

두 가지를 바꿨다.

1. **확인을 `pyplot` 까지 한다.** `probe_matplotlib()` 을 새로 만들어 `matplotlib`,
   `matplotlib.use("Agg")`, `matplotlib.pyplot` 을 시작 시점에 모두 시험한다.
   `ImportError` 만 잡지 않고 `Exception` 을 잡는다 (백엔드 설정 실패는 `OSError` 로도 온다).
   실패 이유 문자열을 그대로 사용자에게 보여 준다.
2. **안내 메시지를 늘렸다.** `warn_no_matplotlib()` 이 무엇이 만들어지는지,
   설치는 어떻게 하는지, 권한 오류가 나면 어떻게 하는지, 그림이 필요 없으면
   무엇을 붙이면 되는지를 적는다.

같은 두 상황에서 이제 이렇게 나온다 (둘 다 종료 코드 0, 산출물 3개 유지).
아래는 위와 똑같이 `pyplot` 을 깨뜨린 상태에서 수정 후 코드로 돌린 실제 출력이다.

```
----------------------------------------------------------------------
주의: 그림을 그릴 수 없다. matplotlib 을 불러오지 못했다.
      이유: ImportError: libfreetype.so.6: cannot open shared object file

      그래도 이 스크립트는 계속 돌아간다. 아래 3개는 그대로 만든다:
        - visualize_table.csv   (그림에 들어갈 값을 그대로 담은 표)
        - viewer_pymol_plddt.pml      (PyMOL 에서 pLDDT 색칠)
        - viewer_chimerax_plddt.cxc   (ChimeraX 에서 같은 것)
      구조를 눈으로 확인하는 데에는 이 뷰어 스크립트만으로 충분하다.

      그림까지 필요하면 설치해라 (한 줄이면 된다):
        python3 -m pip install matplotlib
      저장소 최상위에서라면 이렇게도 된다:
        python3 -m pip install -r requirements.txt

      권한 오류(externally-managed-environment 등)가 나면 사용자 영역에 깔아라:
        python3 -m pip install --user matplotlib

      애초에 그림이 필요 없으면 --no-plot 을 붙여라. 그러면 이 경고도 안 나온다:
        python3 af3_visualize.py <AF3출력폴더> -o 그림 --no-plot
----------------------------------------------------------------------
```

`--no-plot` 과 `--summary-only` 경로는 원래도 import 로 죽지 않았고, 지금도 그렇다.
`--no-plot` 은 확인 자체를 건너뛰므로 이 경고도 나오지 않는다 (실측 확인).

### 남은 한계 (고치지 않았다)

경고는 여전히 **stderr** 로 나간다. `log()` 함수가 stderr 로 쓰도록 만들어져 있고,
그것을 바꾸면 이 스크립트의 다른 모든 진행 메시지의 스트림이 함께 바뀌어
기존 사용법(`2>/dev/null` 로 진행 로그만 죽이는 방식)을 깬다. 대신 구분선으로
감싸서 눈에 띄게 했다. `>` 로 저장할 때 경고까지 남기려면 `2>&1` 을 붙여야 한다.

---

## 4. 출력 파일명 정책

### 무엇을 바꿨나

기본값을 한글에서 ASCII 로 바꿨다. **옛 이름은 옵션으로 남겼고, 파일 내용은 완전히 같다.**

| 도구 | 옛 기본 이름 | 새 기본 이름 | 옛 이름을 쓰는 방법 |
|---|---|---|---|
| `af3_collect.py` | `af3_결과요약.csv` | `af3_summary.csv` | `--lang ko` 또는 `-o af3_결과요약.csv` |
| `af3_visualize.py` | `af3_시각화표.csv` | `visualize_table.csv` | `--filename-lang ko` |
| `af3_visualize.py` | `af3_요약.png` | `confidence_overview.png` | `--filename-lang ko` |
| `af3_visualize.py` | `pymol_색칠.pml` | `viewer_pymol_plddt.pml` | `--filename-lang ko` |
| `af3_visualize.py` | `chimerax_색칠.cxc` | `viewer_chimerax_plddt.cxc` | `--filename-lang ko` |
| `af3run.sh collect` | `<이름>_결과요약.csv` | `<이름>_summary.csv` | `AF3RUN_FILENAME_LANG=ko` |

**바꾸지 않은 것:**

- 타깃별 그림 이름 `<타깃>_plddt.png`, `<타깃>_pae.png` — 이미 ASCII 였다.
- **CSV 안의 열 이름** (`조건`, `타깃`, `등급`, `pLDDT평균` ...) — 그대로 한글이다.
  이유는 아래에 적었다.
- `-o` 로 직접 준 경로 — 사용자가 준 이름을 그대로 쓴다. 알림도 나오지 않는다.
  즉 문서에 있던 `-o af3_결과요약.csv` 형태의 명령은 **하나도 깨지지 않는다.**

### 왜 ASCII 를 기본으로 했나

1. **셸에서 걸린다.** `for f in *_결과요약.csv` 는 되지만, 변수에 담아 따옴표를
   빼먹으면(`wc -l < $F`) 깨지는 경우가 있다. 초보 사용자가 가장 자주 만드는 실수다.
2. **git 로그가 읽기 어려워진다.** git 은 기본 설정(`core.quotepath=true`)에서
   한글 경로를 8진 이스케이프로 출력한다: `"af3_\355\225\234..."`.
3. **OS 간 이동에서 이름이 달라진다.** macOS 는 유니코드를 NFD 로, 리눅스는 NFC 로
   정규화한다. 같은 이름이 다른 이름으로 보여서 `ls` 에는 있는데 스크립트가 못 찾는
   상황이 생긴다. 이 저장소는 이미 AppleDouble 사이드카(`._*`) 문제로 측정 3시간을
   잃은 전례가 있다 — macOS/리눅스 왕복이 실제 작업 흐름이라는 뜻이다.
4. **엑셀 매크로와 후속 스크립트** 에 파일 이름을 하드코딩할 때 인코딩 사고가 준다.
5. **이미 저장소가 ASCII 를 쓰고 있었다.** 새로 만든 관례가 아니다. 커밋된 예시
   파일이 처음부터 ASCII 이름이다:
   `results_example/af3_summary.csv`, `results_example/visualize_table.csv`,
   `figures/confidence_overview.png`, `examples/viewer_pymol_plddt.pml`,
   `examples/viewer_chimerax_plddt.cxc`.
   **도구가 만드는 이름과 저장소에 실린 예시 이름이 서로 달랐던 것** 이 원래 문제였다.
   새 기본값은 예시 파일 이름과 정확히 일치한다.

### 왜 열 이름은 한글로 남겼나

파일 이름과 열 이름의 위험이 다르다. 파일 이름은 셸·git·OS 가 다루므로 위에 적은
문제가 생긴다. 열 이름은 CSV 안의 데이터일 뿐이고, `utf-8-sig` 로 저장해 엑셀에서
바로 열린다. 반대로 열 이름을 바꾸면 **이미 그 열을 참조하는 엑셀 시트나 피벗이
조용히 깨진다** — 파일 이름과 달리 "파일을 못 찾겠다" 는 눈에 보이는 오류가 아니라
빈 칸으로 나온다. 이득이 작고 위험이 크므로 건드리지 않았다.

### 기본값을 바꾼 사실을 어떻게 알리나

세 곳에서 알린다.

1. **화면 출력.** ASCII 기본값으로 돌 때 실행 끝에 알림을 찍는다.
   ```
   [알림] 2026-04 부터 기본 파일 이름이 af3_결과요약.csv 에서
          af3_summary.csv 로 바뀌었다. 내용과 열 이름은 그대로다.
          옛 이름이 필요하면 --lang ko 또는 -o af3_결과요약.csv 를 써라.
   ```
   `-o` 로 이름을 직접 준 경우에는 알림이 나오지 않는다 (사용자가 이미 정했으므로).
2. **`--help` 와 스크립트 머리말 주석.** 각 옵션 설명에 "2026-04 에 기본값이
   바뀌었다" 를 적었다.
3. **이 문서와 `requirements.txt`.**

### 두 도구를 어떻게 맞췄나

`af3_visualize.py` 에는 원래 `--lang` 이 있었지만 그것은 **그림 안 라벨 언어** 였고
파일 이름과는 무관했다 (실측: `--lang en` 으로 돌려도 파일 이름은 한글 그대로였다).
`af3_collect.py` 에는 아무 옵션도 없었다. 그래서 이렇게 정리했다.

- **`--filename-lang {en,ko}`** — 파일 이름을 정한다. 두 도구가 같은 이름의 옵션을 쓴다.
- **`--lang {ko,en}`** — `af3_visualize.py` 에서는 그림 안 라벨 언어 (기존 의미 유지).
  `af3_collect.py` 에서는 그릴 것이 없으므로 `--filename-lang` 의 별칭으로 뒀다
  (`--lang ko` 가 짧아서 실제로 더 많이 쓰일 것이다).

두 옵션은 서로 독립이다. 실측으로 확인한 조합:

| 명령 | 그림 안 라벨 | 만들어진 파일 이름 |
|---|---|---|
| (기본) | 한국어 | `visualize_table.csv`, `confidence_overview.png`, `viewer_*` |
| `--lang en` | 영문 | 같음 (ASCII) |
| `--filename-lang ko` | 한국어 | `af3_시각화표.csv`, `af3_요약.png`, `pymol_색칠.pml`, `chimerax_색칠.cxc` |
| `--filename-lang ko --lang en` | 영문 | 위와 같음 (한글 이름) |

---

## 5. `af3_check.sh` 에 추가한 진단 (7d 절)

기존 절(GPU/드라이버/도커/DB/가중치) 뒤, 종합 앞에 `7d. 이 저장소 스크립트의 파이썬
의존성` 을 넣었다. 첫 실행 전에 막힐 지점을 미리 보여 주는 것이 목적이다. 점검 항목:

- `python3` 경로와 버전, 3.8 이상인지 판정
- `fcntl` 유무 (없으면 `run_af3_batch_improved.py` 를 쓸 수 없다는 뜻)
- 표준 라이브러리만 쓰는 스크립트 4개의 `py_compile` 문법 검사 (파일 손상 확인까지 겸한다)
- `matplotlib` — **`pyplot` 까지** 불러 본다. 실패하면 이유와 설치 명령을 함께 찍는다
- `rdkit` — 선택 의존성임을 명시하고, 없어도 단백질만 돌리면 무관하다고 알린다

종합(8절)의 확인 목록에도 다섯 번째 항목으로 추가했다.

실측 출력. 검증 호스트 gpu-5070ti 에서 `bash scripts/af3_check.sh` 를 실제로 돌려
받아온 로그를 그대로 옮긴 것이다 (matplotlib 이 없는 상태, 종료 코드 0):

```
(로그 원문) ## 7d. 이 저장소 스크립트의 파이썬 의존성
-------------------------------------------------------------------------------
AF3 본체(도커/conda)와 별개로, 이 저장소의 보조 스크립트가 돌아가는지 본다.
결론부터: 파이썬 스크립트 5개 중 4개는 표준 라이브러리만 쓰므로 설치할 것이 없다.
그림을 그리는 af3_visualize.py 하나만 matplotlib 이 필요하다.
(이 진단 스크립트 af3_check.sh 자체는 bash 라서 파이썬 의존성이 없다.)

[측정] python3 경로     : /usr/bin/python3
[측정] python3 버전     : 3.13.7
         -> 3.8 이상. 이 저장소의 스크립트 요구 조건을 만족한다.
[측정] fcntl 모듈       : 있다 (run_af3_batch_improved.py 의 중복 실행 방지가 동작한다)

[측정] 표준 라이브러리만 쓰는 파이썬 스크립트 4개 (설치 불필요) - 문법 검사까지 함께 한다:
         run_af3_batch_improved.py      정상 (불러올 수 있다)
         af3_batch.py                   정상 (불러올 수 있다)
         af3_collect.py                 정상 (불러올 수 있다)
         af3_prepare.py                 정상 (불러올 수 있다)

[경고] matplotlib       : 쓸 수 없다
                          이유: ModuleNotFoundError: No module named 'matplotlib'
                          -> af3_visualize.py 는 죽지 않지만 그림 없이
                             표(visualize_table.csv)와 뷰어 스크립트만 만든다.
                          그림이 필요하면:
                             python3 -m pip install matplotlib
                          권한 오류가 나면:
                             python3 -m pip install --user matplotlib
[참고] rdkit (선택)     : 없다. 단백질만 돌리면 상관없다.
                          --smiles 로 리간드를 넣을 때만 heavy atom 수가 빈칸이 된다.

[참고] 한 줄 설치 (그림까지 필요할 때. 저장소 최상위에서):
         python3 -m pip install -r requirements.txt
       어느 스크립트가 무엇을 필요로 하는지는 docs/dependencies_notes.md 에 있다.

-------------------------------------------------------------------------------
```

---

## 6. 검증 방법과 결과

| 검증 항목 | 방법 | 결과 |
|---|---|---|
| import 전수 조사 | 6개 파일 `ast.parse` | matplotlib 1건, rdkit 1건(선택). 나머지 표준 라이브러리 |
| matplotlib 부재 재현 | matplotlib 없는 전용 venv 생성 후 실행 | 고치기 전: 죽지 않으나 안내 부족. 고친 뒤: 안내 확장 |
| pyplot 파손 재현 | `pyplot`/`ft2font` 가 `ImportError` 를 던지는 가짜 패키지를 `PYTHONPATH` 로 주입 | 고치기 전: 종료 1, 산출물 0. 고친 뒤: 종료 0, 산출물 3 |
| `--lang` × `--filename-lang` | 6개 조합 실제 실행 후 `ls` | 표대로 동작. 두 옵션 독립 확인 |
| 파일 내용 동일성 | `diff` (ASCII 기본 / `--lang ko` / 명시적 `-o`) | 3개 전부 바이트 동일 |
| 기존 옵션 회귀 | `--top`, `--top-list`, `--grade-doc`, `--no-plot`, `--summary-only`, `--format pdf` | 전부 정상 |
| `af3_check.sh` | 실제 실행. matplotlib 있는 환경과 없는 환경 양쪽 | 종료 0, 기존 11개 절 전부 유지 |
| `af3run.sh` | `collect` 모드 실제 실행, 환경변수 양쪽 | ASCII/한글 모두 정상 |
| 셸 구문 | `bash -n` | `af3run.sh`, `af3_check.sh` 통과 |
| 실제 호스트 | gpu-5070ti (matplotlib 없음) 에서 위 항목 재실행 | 아래 7장 |

**측정하지 않은 것:** 윈도우에서의 동작 (`fcntl` 부재는 코드로만 확인, 실행은 안 했다).
matplotlib 3.5 가 정말 하한인지 (3.5 로 내려 실행해 보지 않았다. 쓰는 API 로 추정한 값이다).

---

## 7. 검증 호스트 gpu-5070ti 실측 결과

로컬(macOS)에서만 확인하면 인코딩·경로 문제를 놓칠 수 있으므로, 실제 리눅스 호스트에서
같은 검증을 다시 돌렸다. 이 호스트는 **matplotlib 이 어디에도 없다** — 시스템 python3
(3.13.7)에도, AF3 를 돌리는 conda 환경(`~/miniforge3/envs/af3`, python 3.12.13)에도 없다.
연구자가 처음 만나는 상태가 바로 이것이다.

아래 표의 값은 이 호스트에서 스크립트를 실제로 실행해 받아온 로그에서 읽은 것이다
(원격 작업 2건: 항목별 검증 1건, `af3_check.sh` 최종본 확인 1건). 추정값은 없다.
5절에 인용한 7d 출력은 그 로그를 바이트 그대로 옮긴 것이다.

| 항목 | 결과 |
|---|---|
| 시스템 python3, 기본 실행 | 종료 0. 안내 출력 후 파일 3개 생성 |
| af3 conda python, 기본 실행 | 종료 0. 같음 |
| `--no-plot` | 종료 0. 경고 줄 수 0 (조용히 동작) |
| `--filename-lang ko` | 종료 0. `af3_시각화표.csv`, `pymol_색칠.pml`, `chimerax_색칠.cxc` |
| `af3_collect.py` 3가지 호출 | 3개 파일 전부 `diff` 동일 |
| `af3_check.sh` | 종료 0. 기존 10개 절 + 새 7d 절 전부 정상 |
| `pip install -r requirements.txt` 후 | matplotlib 3.11.1 설치. 그림 5개 생성 확인 |

즉 **matplotlib 없이도 표와 뷰어 스크립트가 나오고, requirements.txt 한 줄로 그림까지
나온다** 는 것을 실제 대상 호스트에서 확인했다.

부수적으로 확인된 사실: 이 호스트의 AF3 conda 환경에 matplotlib 을 넣는 것보다
별도 venv 를 만드는 편이 안전하다. AF3 는 jax 0.10.2 + CUDA 12.9 로 고정돼 있고
matplotlib 이 numpy 를 함께 끌어오므로, 같은 환경에 설치하면 numpy 버전이 움직여
추론 환경을 건드릴 위험이 있다. 위 표의 마지막 줄은 그래서 `python3 -m venv` 로
분리한 환경에서 측정했다. (추정: numpy 충돌 가능성. 실제로 충돌시켜 보지는 않았다.)
