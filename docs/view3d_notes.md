# view3d_notes.md - 브라우저 3D 뷰어 설계와 검증 기록

`scripts/af3_view3d.py` 가 AF3 출력 폴더를 브라우저에서 여는 HTML 로 만든다.
이 문서는 무엇을 어떻게 정했고, 무엇을 실제로 돌려 확인했고, 무엇을 확인하지
못했는지를 적는다.

## 1. 왜 만들었나

저장소의 3D 보기 수단은 `af3_visualize.py` 가 만드는 PyMOL(.pml) /
ChimeraX(.cxc) 스크립트뿐이었다. 둘 다 데스크톱 프로그램을 따로 설치해야 열린다.
실험 기반 연구자에게 이것은 결과를 보기 전에 넘어야 하는 벽이다.
HTML 은 더블클릭하면 열린다. 설치 단계가 0 이다.

## 2. 렌더 엔진 선택: Mol* 을 기본으로 했다

후보 세 개를 비교했다.

| 항목 | Mol* 5.11.0 | 3Dmol.js 2.5.5 | NGL |
| --- | --- | --- | --- |
| mmCIF 네이티브 읽기 | 된다 | 된다 | 된다 |
| pLDDT 색칠 | 내장 테마 `plddt-confidence` | 직접 구현해야 한다 | 직접 구현해야 한다 |
| 사슬별 색칠 | 내장 테마 `chain-id` | 직접 구현해야 한다 | 내장 |
| `_ma_qa_metric_local` 인식 | 한다 | 안 한다 | 안 한다 |
| 번들 크기 | 5.03MB + CSS 0.07MB | 0.53MB | 약 1MB |
| 쓰는 곳 | RCSB PDB, EBI AlphaFold DB | 개별 논문 도구 | 개별 도구 |

기본을 Mol* 로 정한 근거는 크기가 아니라 **색이 같아진다**는 점이다.
Mol* 5.11.0 번들에서 `plddt-confidence` 테마의 색 경계와 색값을 직접 확인했다.

    l <= 50  -> 0xFF7D45 (주황)
    l <= 70  -> 0xFFDB13 (노랑)
    l <= 90  -> 0x65CBF3 (하늘)
    그 위    -> 0x0053D6 (진한 파랑)

이 네 값은 이 저장소 `af3_visualize.py` 의 `PLDDT_BANDS` 와 같다. 즉 연구자가
EBI AlphaFold DB 웹에서 본 색, `af3_visualize.py` 의 pLDDT 꺾은선 배경색,
이 HTML 뷰어의 색이 모두 일치한다. 세 화면을 나란히 놓고 비교할 수 있다.
색을 우리가 새로 정하면 그 일관성이 깨진다.

두 번째 근거는 값의 출처다. Mol* 의 테마는 mmCIF 의 `_ma_qa_metric_local`
(잔기별 pLDDT)을 1순위로 읽고, 없으면 `B_iso_or_equiv` 로 되돌린다.
AF3 출력은 두 값을 같게 쓴다. 실측으로 확인했다 (복합체 277잔기, 단량체 3건):

    |ma_qa_metric_local - (원자별 B_iso_or_equiv 의 잔기 평균)| 최대 0.005
    mmCIF 는 소수 2자리로 쓰므로 이 크기의 차이는 반올림이다

즉 Mol* 이 칠하는 색과 우리가 계산한 잔기별 pLDDT 가 같은 값을 가리킨다.

3Dmol.js 는 `--engine 3dmol` 로 남겨 두었다. 오프라인 파일을 만들 때
0.53MB 대 5.03MB 의 차이가 크다. 3Dmol 경로에서는 색을 우리가 지정한다
(파이썬이 계산한 잔기별 pLDDT 를 HTML 안 `AF3.res` 로 넘기고, 자바스크립트가
같은 네 색으로 칠한다). NGL 은 채택하지 않았다. Mol* 대비 얻는 것이 없다.

## 3. CDN 대 인라인: 둘 다 준다. 기본은 CDN 이다

절충이 있다.

- `--lib cdn` (기본): HTML 이 작다 (실측 0.12~0.21MB). 열 때 인터넷이 필요하다.
  인터넷이 없거나 사내망이 CDN 을 막으면 구조가 안 뜬다. 이때 **빈 화면이 아니라**
  "이 파일은 라이브러리를 인터넷에서 불러온다. 오프라인으로 열려면 `--lib embed`
  로 다시 만들어라" 는 안내가 나온다 (`af3Fail` 경로).
- `--lib embed`: 라이브러리를 HTML 안에 넣는다. 인터넷 없이 열린다.
  파일 크기 실측:

  | 조합 | 단량체 (138잔기) | 복합체 (277잔기) |
  | --- | --- | --- |
  | cdn + molstar | 0.12MB | 0.21MB |
  | embed + molstar | 5.22MB | 5.31MB |
  | embed + 3dmol | 0.66MB | 0.75MB |

  embed 는 타깃 수만큼 라이브러리가 복제된다. 2000건을 Mol* embed 로 만들면
  약 10GB 다 (5.2MB x 2000, 추정. 실측은 4건까지만 했다). 그래서 기본이 아니다.
  최종 후보 몇 건만 embed 로 만들고 전체 훑기는 cdn 으로 하는 것이 맞다.
  오프라인에서 여러 건이 필요하면 `--engine 3dmol --lib embed` 가 8배 작다.

라이브러리 원본은 `--lib-cache` (기본 `~/.cache/af3_view3d`) 에 한 번 내려받아
둔다. 고정 SHA-256이 맞는 cache만 재사용한다. CDN mode는 같은 고정 byte의 SRI와
`crossorigin=anonymous`를 사용하고 generated page는 CSP로 외부 연결을 차단한다.
CDN 두 곳(jsdelivr, unpkg)이 모두 막히면 npm 레지스트리 tarball 을 받아
안에서 필요한 파일만 꺼낸다 (`tarfile` 은 표준 라이브러리다). 셋 다 막히면
실패 이유와 함께 "인터넷 되는 컴퓨터에서 이 URL 을 저장해 `--lib-file` 로
넘겨라" 를 알려주고 멈춘다. `--lib-file`은 신뢰한 executable JavaScript를
명시적으로 제공하는 경로이므로 출처를 사용자가 검증해야 한다.

버전을 `latest` 로 두지 않고 고정했다 (molstar 5.11.0, 3dmol 2.5.5).
`latest` 는 어느 날 API 가 바뀌어 조용히 깨진다. 아래 4절의 확인은 이 두 버전에서
한 것이다.

## 4. 파이썬 의존성은 늘지 않았다

표준 라이브러리만 쓴다. matplotlib 도 numpy 도 필요 없다.
`requirements.txt` 에 추가한 것은 없다 (선택 항목으로 `zstandard` 를
주석으로만 적었다. 5절 참조).

## 5. `.cif.zst` 압축 출력

AF3 를 `--compress_large_output_files` 로 돌리면 mmCIF 가 `.cif.zst` 로 나온다.
파이썬 표준 라이브러리는 zstd 를 풀지 못한다. 세 단계로 처리한다.

1. `zstandard` 파이썬 모듈이 있으면 그것으로 푼다.
2. 없으면 `zstd` 명령을 찾아 `zstd -dc` 로 푼다.
3. 둘 다 없으면 **그 타깃을 조용히 빼지 않는다.** 개별 HTML 을 그대로 만들고
   (신뢰도 지표는 다 보인다) 구조 자리에 빨간 상자로 이유와 해결책을 띄운다.
   `index.html` 상단에도 "구조를 표시할 수 없는 타깃 N개" 상자가 뜨고,
   그 타깃 행은 배경이 붉게 표시된다.

실측 확인 (아래 6절): `zstd` 명령으로 압축한 실제 AF3 mmCIF 를 풀어 HTML 에
넣은 결과가 압축 전 원본과 **바이트 단위로 일치**했다. `zstd` 를 PATH 에서
없앤 환경에서는 3번 경로가 동작하고 이유가 화면에 남았다.

## 6. 검증: 무엇을 실제로 돌렸나

검증 호스트 `gpu-5070ti` (RTX 5070 Ti 16GB, Chrome 151.0.7922.71,
python 3.13.7) 와 로컬에서 돌렸다. 입력은 실제 AF3 v3.0 출력이다.

### 6-1. 쓴 데이터

| 타깃 | 종류 | 사슬 | 잔기 | 원자 | ranking score | ipTM |
| --- | --- | --- | --- | --- | --- | --- |
| vhh_7a50_1 | 단량체 | 1 | 138 | 1082 | 0.85 | 없음(null) |
| vhh_4qgy_1 | 단량체 | 1 | 135 | 1035 | 0.87 | 없음(null) |
| vhh_8v8k_1 | 단량체 | 1 | 123 | 939 | 0.85 | 없음(null) |
| vhh_antigen_complex | 복합체 | 2 (A,B) | 277 | 2101 | 0.82 | 0.82 |

단량체 3건은 `~/af3_db_track/af3out_reduced/` 에 남아 있던 기존 출력이다.
복합체는 이 작업에서 새로 돌렸다 (축소 DB `reduced_dbs_af3`,
`--num_diffusion_samples=1`, VHH 148잔기 + 리소자임 129잔기). GPU 사용은 1건뿐이다.

### 6-2. 숫자 대조 (프로그램으로)

`_model.cif` 원본과 HTML 안에 들어간 값을 대조했다. 4건 전부에서:

- HTML 안 mmCIF는 base64 data block으로 저장되고 브라우저에서 UTF-8로 복원된다.
  복원한 byte가 원본과 일치한다. 이 방식은 `</script>` data injection도 막는다.
- 잔기 수, 원자 수가 원본과 일치 (138/1082, 135/1035, 123/939, 277/2101).
- HTML 에 심긴 잔기별 pLDDT 와 원본 `B_iso_or_equiv` 의 잔기 평균의
  **최대 차 0.0000**. (같은 코드로 계산하므로 당연하지만, HTML 로 옮기는
  과정에서 값이 잘리거나 반올림되지 않았음을 확인한 것이다.)
- mmCIF 의 `_ma_qa_metric_local` (Mol* 이 읽는 값) 과의 최대 차 0.0100.
  mmCIF 는 소수 2자리로 쓰므로 이것이 반올림 한계다. 이 허용치는 이 트랙에서 새로
  정한 것이 아니라 저장소에 이미 있던 값이다. `scripts/af3_visualize.py` 663행의
  `if worst <= 0.0105:` 이고 (커밋 ce80f739b677103c933e98d2c3e51c9bf6d7b68a),
  바로 위에 "mmCIF 는 소수 2자리 -> 반올림 차이는 최대 0.01 까지 정상으로 본다" 는
  주석이 붙어 있다. 대조 스크립트의 문턱값을 그 값에 맞춘 것이다.
- 색 구간 경계에서 올바른 색이 나온다. 경계 양쪽에 실제로 있던 잔기로 확인했다:

  | 타깃 | 잔기 | pLDDT | 색 | 판정 |
  | --- | --- | --- | --- | --- |
  | vhh_7a50_1 | A112 | 89.91 | #65CBF3 | 90 미만이므로 하늘색. 맞다 |
  | vhh_7a50_1 | A46 | 90.20 | #0053D6 | 90 이상이므로 진한 파랑. 맞다 |
  | vhh_antigen_complex | A2 | 69.45 | #FFDB13 | 70 미만이므로 노랑. 맞다 |
  | vhh_antigen_complex | B73 | 70.95 | #65CBF3 | 70 이상이므로 하늘색. 맞다 |
  | vhh_4qgy_1 | A133 | 50.47 | #FFDB13 | 50 이상이므로 노랑. 맞다 |
  | vhh_4qgy_1 | A135 | 49.24 | #FF7D45 | 50 미만이므로 주황. 맞다 |

- 신뢰도 지표가 `_summary_confidences.json` 원본과 일치.
  ranking score / pTM / 평균 pLDDT 가 표에 그 값으로 있다.
  **ipTM 은 단량체 3건에서 JSON 이 null 이고, HTML 표에 그 행이 없다**
  (0 으로 표시하지 않는다). 복합체에서는 0.820 으로 표시된다.

### 6-3. 화면 렌더 확인 (실제 브라우저)

로컬 macOS 샌드박스에서는 헤드리스 Chrome 이 뜨지 않았다 (빈 페이지에서도
Abort trap 6). 그래서 검증 호스트의 Chrome 151.0.7922.71 로 확인했다.
GPU 가 없는 헤드리스라 `--enable-unsafe-swiftshader` (소프트웨어 WebGL) 로 돌렸다.
WebGL2 컨텍스트가 ANGLE/SwiftShader 로 실제로 생성됨을 먼저 확인한 뒤 진행했다.

확인은 "HTML 을 만들었다" 가 아니다. 페이지에 검사 스크립트를 주입해 브라우저
안에서 실행하고, 렌더러가 잔기마다 배정한 색을 전부 되읽어 원본과 대조했다.

되읽는 방법이 엔진마다 다르다. 이것을 틀리면 검증이 거짓 실패를 낸다.

- Mol\*: `colorThemeRegistry.create('plddt-confidence', ...)` 로 테마를 만들고
  `StructureElement.Location` 을 잔기마다 옮기며 `theme.color(loc, false)` 를 부른다.
  화면을 칠하는 것과 같은 함수다.
- 3Dmol: `colorfunc` 의 반환값을 `atom.color` 에 되쓰지 않는다. 그래서 `atom.color`
  를 읽으면 전부 기본색(`#8f8fff`)으로 보인다. 처음에 이 실수를 해서 277잔기 전부
  불일치로 나왔다. 렌더러가 원자마다 부르는 함수 자체(`window.__af3_colorOf`)를
  같은 인자로 불러야 한다.

각 페이지에서 이 순서로 확인했다.

1. `window.__af3_ready` 가 참이 되기를 기다린다 (실패하면 `__af3_error` 를 읽는다).
2. pLDDT 모드에서 잔기별 색을 전부 되읽는다.
3. `af3SetColor('chain')` 을 실제로 불러 사슬 모드에서 다시 전부 되읽는다.
4. `af3SetColor('plddt')` 로 되돌린다.
5. 엔진의 스크린샷 API 로 PNG 를 받는다
   (Mol\*: `viewportScreenshot.getImageDataUri()`, 3Dmol: `pngURI()`).
   캔버스를 직접 `getImageData` 로 읽는 방식은 프레임이 밀려 앞 화면이 잡혔다.
   같은 PNG 가 두 모드에서 나오는 일이 있어 이 방식을 버렸다.

결과. 대조한 페이지 8개 전부 색 불일치 0개다.

| 페이지 | 엔진 | 라이브러리 | 잔기 | pLDDT 색 불일치 | 사슬 색 |
| --- | --- | --- | --- | --- | --- |
| 복합체 | Mol\* 5.11.0 | cdn | 277 | 0 | A #1b9e77, B #d95f02 |
| 복합체 | Mol\* 5.11.0 | embed | 277 | 0 | A #1b9e77, B #d95f02 |
| 단량체 vhh_7a50_1 | Mol\* 5.11.0 | cdn | 138 | 0 | A #1b9e77 |
| 복합체 | 3Dmol.js 2.5.5 | cdn | 277 | 0 | A #4c72b0, B #dd8452 |
| 복합체 | 3Dmol.js 2.5.5 | embed | 277 | 0 | A #4c72b0, B #dd8452 |
| 단량체 vhh_7a50_1 | 3Dmol.js 2.5.5 | cdn | 138 | 0 | A #4c72b0 |
| 복합체 (레이아웃 수정 후 재확인) | Mol\* 5.11.0 | cdn | 277 | 0 | 같음 |
| `examples/view3d_example.html` (산출물 그 파일) | Mol\* 5.11.0 | cdn | 277 | 0 | A #1b9e77, B #d95f02 |

사슬 모드에서 사슬 하나는 한 색으로 통일되고 사슬끼리는 서로 다른 색이었다
(복합체 A/B). pLDDT 로 되돌리면 원래 색 구성으로 돌아왔다.

이 확인 과정에서 화면 결함 세 가지를 찾아 고쳤다.

| 결함 | 원인 | 고친 것 |
| --- | --- | --- |
| 단량체 페이지에서 지표 숫자가 잘려 안 보였다 | 값 칸에 `white-space:nowrap` 을 두어 '확산 샘플 5개 (표준편차 0.001)' 같은 긴 값이 표를 사이드바 밖으로 밀어냈다 | 값/이름 칸의 nowrap 을 풀고 이름 칸 폭을 58% 로 고정 |
| 사슬 범례의 색 견본이 화면 색과 달랐다 (거짓 범례) | Mol\* 은 자체 사슬 색표를 쓰는데 우리 `CHAIN_COLORS` 를 견본으로 그렸다 | 페이지가 열릴 때 `chain-id` 테마에서 실제 색을 되읽어 견본을 덮어쓴다 (`af3SetChainLegend`) |
| 3D 화면 아래에 빈 회색 띠가 남았다 | 화면 열의 높이가 정해지지 않아 사이드바가 짧은 페이지에서 빈 공간이 생겼다 | `.col` 에 `height:100%`, `.view` 에 `min-height:0` |

`figures/view3d_screenshot.png` 는 고친 뒤에 다시 찍은 것이다 (패널 a~e).

### 6-4. 어려운 상황 시나리오

`/tmp/v3d_scenario` 에 아래를 섞어 넣고 돌렸다.

| 넣은 것 | 기대 | 결과 |
| --- | --- | --- |
| `.af3_incomplete/vhh_4qgy_1/` (완료된 결과가 들어 있다) | 격리 폴더는 무조건 제외 | 제외됐다. 목록에 안 나온다 |
| `.af3_pending_abc123/` | 제외 | 제외됐다 |
| `.run_af3_batch.lock` | 제외 | 제외됐다 |
| `._vhh_ghost` (AppleDouble) | 제외하고 경고 | 제외하고 "사이드카 1개, 지워라" 안내가 나왔다 |
| `vhh_msa_only/` (`_data.json` 만) | 미완료로 제외 | "미완료로 건너뛴 폴더: vhh_msa_only(0/3)" |
| `vhh_zero/` (크기 0 산출물 3개) | 미완료로 제외 | "vhh_zero(0/3)". 크기 0 을 없는 것으로 셌다 |
| `vhh_8v8k_1_model.cif.zst` | 풀어서 쓴다 | 풀었고 원본과 바이트 일치 |
| `vhh_4qgy_1_20260820_101010/` (재실행 폴더) | 파일 이름은 `vhh_4qgy_1.html` | 그렇게 나왔다. 폴더명이 아니라 stem 을 썼다 |

`zstd` 를 PATH 에서 없앤 환경에서 같은 폴더를 돌리면 `vhh_8v8k_1` 이 목록에
남고, 개별 HTML 과 `index.html` 양쪽에 "cif.zst 를 풀 방법이 없다 /
`pip install zstandard` 또는 `zstd -d`" 가 표시됐다.

### 6-5. 정본 규약 준수 확인

`af3_view3d.py` 의 정본 블록(`FINAL_SUFFIX_GROUPS` ~ `resolve_result_dir`)이
`af3_collect.py` / `af3_visualize.py` 의 같은 블록과 **문자 단위로 일치**함을
확인했다. `is_sidecar`, `strip_af3_timestamp`, `FINAL_SUFFIX_GROUPS` 를
같은 입력으로 호출해 같은 답이 나오는 것도 확인했다.

### 6-6. 회귀 테스트

기존 242건 전부 통과. baseline 과 같다.

    python3 tests/run_tests.py --strict      42/42 통과
    python3 tests/test_naming.py            125/125 통과
    python3 tests/test_filename_lang.py      75/75 통과

## 7. 확인하지 못한 것

정직하게 남긴다.

- **실제 사용자의 데스크톱 브라우저에서 열어 보지 못했다.** 확인한 것은
  검증 호스트의 헤드리스 Chrome 151 (소프트웨어 WebGL) 뿐이다. Safari, Firefox,
  Edge, 그리고 GPU 가 있는 일반 데스크톱 Chrome 에서는 열어 보지 않았다.
  Mol* 과 3Dmol 은 둘 다 이 브라우저들을 공식 지원하지만, 이 저장소가 만든
  HTML 로 확인한 것은 아니다.
- **마우스 조작을 사람이 해 보지 못했다.** 돌리기/확대/이동은 라이브러리 기본
  동작이고 우리가 손대지 않았지만, 헤드리스로는 검증할 수 없다. 확인한 것은
  색칠 전환 버튼(`af3SetColor`)을 프로그램으로 부른 결과다.
- **시점 초기화 버튼을 눌러 보지 못했다.** 코드로만 있다
  (`requestCameraReset` / `zoomTo`).
- **`zstandard` 모듈 경로를 돌리지 못했다.** 검증 환경에 그 모듈이 없어서
  `zstd` 명령 경로와 "둘 다 없음" 경로만 실제로 돌렸다. 모듈 경로는 코드로만 있다.
- **큰 구조에서의 성능을 재지 않았다.** 확인한 것은 최대 2101원자(277잔기)다.
  VHH 스크리닝에는 충분하지만, 수만 원자 복합체에서 브라우저가 얼마나 느려지는지는
  모른다.
- **CDN 이 실제로 막힌 망에서 열어 보지 못했다.** 오프라인 실패 안내는 코드
  경로로만 있고, HTML 이 외부에서 무엇을 불러오는지(cdn 이면 2개, embed 면 0개)를
  프로그램으로 확인한 것까지가 실측이다.
- **2000건 규모로 돌려 보지 않았다.** 4건까지만 돌렸다. `--max` / `--top` 이
  그 규모를 상정한 안전장치다.
