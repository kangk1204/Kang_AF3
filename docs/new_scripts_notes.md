# af3_prepare.py / af3_visualize.py — 사용법과 검증 내역

이 문서는 저장소에 새로 들어가는 두 스크립트를 다룬다. 앞의 도구들이 메우지 못한
두 구멍을 채운다.

| 구멍 | 채운 것 |
|---|---|
| FASTA/CSV 로 갖고 있는 서열을 AF3 입력 JSON 으로 바꿀 방법이 없었다 (af3_batch.py 는 JSON 을 읽기만 한다) | `af3_prepare.py` |
| 결과를 볼 방법이 표(CSV)뿐이었다 (af3_collect.py) | `af3_visualize.py` |

검증 환경: gpu-5070ti (RTX 5070 Ti 16GB, Blackwell sm_120, 24 CPU, RAM 126GB),
AF3 alphafold3-3.0.5.dev15+g97d20234c, conda 네이티브 설치 (`~/miniforge3/envs/af3`).
연구자 환경은 Docker + RTX 5090 이므로 여기서 통과한 것은 그쪽에서도 통과한다.

---

## 1. af3_prepare.py — 입력 JSON 생성기

### 1.1 무엇을 하는가

```
vhh_2000.fasta  ──af3_prepare.py──▶  vhh_in/01_vhh_001.json
(또는 .csv)                          vhh_in/02_vhh_002.json
                                     ...
                                     그리고 버킷 분포 요약을 화면에 출력
```

만든 폴더를 그대로 `af3_batch.py --input_dir vhh_in` 이나
`run_alphafold.py --input_dir vhh_in` 에 넘기면 된다.

### 1.2 복사해 붙이는 명령

```bash
# 가장 흔한 경우: FASTA 한 장 -> 단량체 JSON 여러 개
python3 af3_prepare.py --fasta vhh_2000.fasta -o vhh_in

# CSV. 열 이름은 자동으로 찾는다. 못 찾으면 실제 열 이름을 보여주고 멈춘다
python3 af3_prepare.py --csv vhh_2000.csv -o vhh_in
python3 af3_prepare.py --csv vhh_2000.csv --name-col 클론명 --seq-col 아미노산서열 -o vhh_in

# 만들기 전에 검사와 요약만 (2000건을 잘못 만들기 전에 이것부터 해라)
python3 af3_prepare.py --fasta vhh_2000.fasta -o vhh_in --dry-run

# 모든 VHH 에 같은 항원을 B 사슬로 붙여 복합체로 (다음 단계에 할 일)
python3 af3_prepare.py --fasta vhh_2000.fasta --partner-fasta antigen.fasta -o vhh_ag_in

# 같은 서열 2부 (homodimer)
python3 af3_prepare.py --fasta vhh.fasta --copies 2 -o dimer_in

# 리간드 (CCD 코드 또는 SMILES. 둘 중 하나만)
python3 af3_prepare.py --fasta target.fasta --ligand-ccd ATP -o with_atp_in
python3 af3_prepare.py --fasta target.fasta --ligand-smiles 'CC(=O)Oc1ccccc1C(=O)O' -o with_asp_in

# 시드 3개 (같은 서열을 시드마다 예측. 시간이 3배 든다)
python3 af3_prepare.py --fasta vhh.fasta --seeds 1,2,3 -o vhh_in

# 타깃별 토큰수/버킷 표를 CSV 로도 남기기
python3 af3_prepare.py --fasta vhh.fasta -o vhh_in --report prep_report.csv
```

### 1.3 정상 동작 시 보이는 출력

```
읽은 서열: 5 건 (example_input.fasta)
검사 결과: 문제 없음
==================================================================
AF3 입력 JSON 요약
==================================================================
입력 파일        : example_input.fasta
출력 폴더        : vhh_in
만든 JSON        : 5 개
사슬 구성        : 대상 1부
modelSeeds       : [1]  (시드 1개 -> 추론 시간 약 1배)
dialect / version: alphafold3 / 1

토큰 수          : 최소 120, 중앙값 123, 최대 148

버킷 분포 (패딩 후 실제로 계산되는 크기)
  버킷 128   :     3 건 ( 60.0%) ########################
  버킷 256   :     2 건 ( 40.0%) ################

  버킷이 섞여 있다. 실측 기준 버킷 256 은 버킷 128 보다 건당 2.25배 느리다
  (정상상태 9.44초 대 4.20초, RTX 5070 Ti). 129 토큰이면 이미 256 버킷이다.
  128 버킷 3건, 256 버킷 2건. 태그/링커로 길이를 늘리기 전에 이 표를 봐라.

다음 단계
  python3 af3_batch.py --input_dir vhh_in --output_dir vhh_out
==================================================================
```

버킷 분포 표가 이 스크립트의 핵심이다. 2000건을 돌리기 전에 여기서
"내 서열이 128 버킷에 들어가는가"를 확인해야 한다. 130 aa VHH 는 그대로면
129~130 토큰이라 이미 256 버킷이고, 실측으로 건당 2.25배 느리다.
발현 태그를 빼서 128 아래로 내려가면 GPU 단계 시간이 절반 이하가 된다.

### 1.4 종료 코드

| 코드 | 뜻 |
|---|---|
| 0 | 전부 정상 |
| 1 | 인자 자체가 잘못됐다 (`--fasta` 와 `--csv` 를 둘 다 줬다 등) |
| 2 | 서열에 오류가 있어 **아무것도 만들지 않았다** |
| 3 | `--force` 로 오류를 건너뛰고 통과한 것만 만들었다 |

배치 스크립트에서 쓸 때는 2 와 3 을 구분해서 처리해라.

### 1.5 무엇을 잡아내는가 (실측 확인)

`bad.fasta` 로 실제 확인한 결과:

```
검사 결과: 오류 4건, 경고 0건
  [오류] bad.fasta 3번째 줄 (has_stop): 아미노산이 아닌 문자 '*' 가 있다 (AF3 는 알파벳만
         받는다). 종결코돈 '*', 정렬 갭 '-' 이 남아 있는지 확인해라
  [오류] bad.fasta 5번째 줄 (empty_seq): 서열이 비어 있다
  [오류] bad.fasta 10번째 줄 (too_short): 서열이 3 잔기뿐이다 (--min-len 10 미만).
         잘린 서열이 아닌지 확인해라
  [오류] bad.fasta 12번째 줄 (ambig_X): 표준 아미노산이 아닌 문자: X(미지 잔기. AF3 는
         UNK 로 처리한다). 그대로 돌리려면 --allow-ambiguous

오류가 4건 있어서 아무것도 만들지 않았다.
입력 파일을 고치고 다시 실행하거나, 통과한 3건만 만들려면 --force 를 붙여라.
```

이름 충돌도 잡는다. `항체 #1`, `항체_1`, `항체/1` 을 같이 주면:

```
  [오류] collide.csv 4번째 줄 (항체_1): 이름을 파일명으로 다듬으면 '1' 가 되어
         항체 #1(collide.csv 2번째 줄) 와 충돌한다. 이름에서 특수문자를 빼라
```

AF3 자체는 이 충돌을 잡지 않는다. 그냥 같은 출력 폴더에 덮어써서 결과를 잃는다.
그래서 여기서 미리 막는다.

**이름은 영문/숫자/밑줄로만 쓰는 것이 안전하다.** AF3 의 `sanitised_name()` 은
허용 문자(`A-Za-z0-9_-.`) 밖의 문자를 **버린다**(치환하지 않는다). 그래서
`항체 #1` 은 AF3 안에서 `_1` 이라는 폴더가 된다. 스크립트가 이 차이를 경고로 알려준다.

### 1.6 실물로 확인한 AF3 입력 JSON 스펙

출처는 추측이 아니라 검증 호스트에 실제로 설치된 소스다:
`~/miniforge3/envs/af3/lib/python3.12/site-packages/alphafold3/common/folding_input.py`

**최상위 허용 키** (`Input.from_json` 의 `_validate_keys`, 이 밖의 키를 주면 거부):

```
dialect, version, name, modelSeeds, sequences, bondedAtomPairs, userCCD, userCCDPath
```

- `dialect` — 반드시 `"alphafold3"` (`JSON_DIALECT`). 다른 값이면
  `AlphaFold 3 input JSON has unsupported dialect: ...` 로 거부.
- `version` — `JSON_VERSIONS = (1, 2, 3, 4)` 중 하나. `JSON_VERSION` 은 4 다.
  소스를 grep 해 보면 `version` 값은 이 목록에 있는지만 검사하고 파싱 분기에
  쓰이지 않는다 (`raw_json['version'] not in JSON_VERSIONS` 한 곳뿐).
  그래서 `af3_prepare.py` 는 가장 넓게 호환되는 **1** 을 기본으로 쓴다.
  AF3 가 스스로 쓰는 `<타깃>_data.json` 은 4 를 쓴다. `--json-version 4` 로 바꿀 수 있다.
- `dialect` 와 `version` 이 **둘 다 없으면** AF3 는 AlphaFold Server 형식으로 해석한다
  (`ALPHAFOLDSERVER_JSON_DIALECT`). 이 스크립트는 항상 둘을 넣으므로 그 경로로 가지 않는다.
- `modelSeeds` — 비어 있으면 거부:
  `AlphaFold 3 input JSON must specify at least one rng seed in 'modelSeeds'.`
  정수 리스트다.
- `sequences` — 없으면 거부. 각 항목은 사전 하나이고 키가 정확히 1개여야 한다
  (`protein` / `rna` / `dna` / `ligand` 중 하나). 2개면 `Chain ... has more than 1 sequence.`

**protein 사슬 허용 키** (`ProteinChain.from_dict`):

```
id, sequence, modifications, description, unpairedMsa, unpairedMsaPath,
pairedMsa, pairedMsaPath, templates
```

- `sequence` 는 **전부 알파벳**이어야 한다 (`if not all(res.isalpha() for res in sequence)`).
  `*`, `-`, `.`, 숫자, 공백이 있으면 `Protein must contain only letters, got "..."`.
- 표준 20종이 아닌 알파벳(X, B, Z, J, U, O)은 **거부되지 않는다**. 조용히 UNK 로 처리된다.
  그래서 `af3_prepare.py` 가 기본적으로 오류로 잡고, `--allow-ambiguous` 로만 통과시킨다.
- `modifications` 는 `[{"ptmType": ..., "ptmPosition": 1-based 정수}]`.
  `ptmType` 에 `CCD_` 접두어를 붙이면 거부된다.
- `unpairedMsa` 와 `unpairedMsaPath` 를 동시에 주면 거부. 짧은 문자열이 실제 파일 경로면
  "경로는 `unpairedMsaPath` 로 줘라" 는 오류가 난다.

**ligand 사슬 허용 키** (`Ligand.from_dict`):

```
id, ccdCodes, smiles, description
```

- `ccdCodes` 와 `smiles` 를 **동시에 주면 거부**:
  `Ligand cannot have both CCD code and SMILES set at the same time`.
- `ccdCodes` 는 **반드시 리스트**다. 문자열 하나를 주면
  `CCD codes must be a list of strings, got str instead` 로 거부.
- `smiles` 는 rdkit 으로 파싱된다. 못 읽으면
  `Unable to make RDKit Mol from SMILES: ...`. `af3_prepare.py` 는 rdkit 이
  있으면 넘기기 전에 같은 검사를 먼저 한다.
- CCD 코드에 `CCD_` 접두어를 붙이는 것은 AlphaFold **Server** 형식이다.
  AF3 네이티브 형식에서는 접두어 없이 `ATP` 로 쓴다.

**사슬 id**

- 각 `sequences` 항목의 `id`. 문자열 하나(`"A"`) 또는 문자열 리스트(`["A","B"]`)다.
  리스트를 주면 같은 서열을 여러 부 넣는다는 뜻이다 (homomer).
- `id` 가 하나라도 비어 있으면 전체가 거부:
  `AlphaFold 3 input JSON contains sequences with unset IDs.`
- id 문자는 `mmcif.int_id_to_str_id` 규칙과 같게 A, B, ... Z, AA, AB ... 로 붙인다.
  `af3_prepare.py` 가 이 규칙을 그대로 구현했다.

**우리가 만드는 JSON 실물** (단량체):

```json
{
  "name": "Nb_b201_5VNV",
  "modelSeeds": [
    1
  ],
  "sequences": [
    {
      "protein": {
        "id": "A",
        "sequence": "QVQLQESGGGLVQAGGSLRLSCAASGYISDAYYMGWYRQAPGKEREFVATITHGTNTYYADSVKGRFTISRDNAKNTVYLQMNSLKPEDTAVYYCAVLETRSYSFRYWGQGTQVTVSSLE"
      }
    }
  ],
  "dialect": "alphafold3",
  "version": 1
}
```

복합체 (`--partner-fasta`):

```json
{
  "name": "cAbLys3_1MEL",
  "modelSeeds": [
    1
  ],
  "sequences": [
    { "protein": { "id": "A", "sequence": "DVQLQASGGG...DVPDYGSGRA" } },
    { "protein": { "id": "B", "sequence": "KVFGRCELAA...QAWIRGCRL" } }
  ],
  "dialect": "alphafold3",
  "version": 1
}
```

### 1.7 검증 내역

| 무엇 | 결과 |
|---|---|
| FASTA 5건 -> JSON 5건 | 통과 |
| CSV 5건 -> JSON 5건, 한국어 열 이름(`클론명`/`아미노산서열`) 자동 인식 | 통과 |
| FASTA 경로와 CSV 경로가 같은 JSON 을 만드는가 | `동일=True` (name -> sequences 전체 비교) |
| `--partner-fasta` 로 사슬 2개 | 통과, id A/B 정상 |
| `--copies 2 --ligand-ccd ATP` | 통과, 사슬 A/B + 리간드 C |
| 오류 입력 7건 (종결코돈/빈서열/짧음/비표준문자) | 오류 4건 잡고 종료코드 2, 아무것도 만들지 않음 |
| 이름 충돌 (`항체 #1` vs `항체_1` vs `항체/1`) | 충돌 2건 잡고 중단 |
| `--force --allow-ambiguous` | 오류 3건 건너뛰고 4건 생성, 종료코드 3 |
| **AF3 자체 파서 통과** (`folding_input.Input.from_json`) | 단량체 5건 + 복합체 5건 전부 `OK`, 사슬 id/길이/시드 확인 |
| **실제 AF3 로 끝까지 실행** (MSA + 추론 + 출력) | 단량체 1건, 복합체 1건 모두 완주 (아래) |

AF3 파서 통과 로그 (일부):

```
OK   vhh_in/04_Nb_b201_5VNV.json Nb_b201_5VNV seeds (1,) [('A', 'ProteinChain', 120)]
OK   cplx_in/01_cAbLys3_1MEL.json cAbLys3_1MEL seeds (1,) [('A', 'ProteinChain', 148), ('B', 'ProteinChain', 129)]
```

### 1.8 실제 AF3 실행 결과 (이 스크립트의 유일한 진짜 검증)

`af3_prepare.py` 가 만든 JSON 을 손대지 않고 그대로 `run_alphafold.py` 에 넣었다.
축소 DB(`~/public_databases`, 약 2GB)를 썼고 데이터 파이프라인(MSA)부터 전부 돌렸다.

**단량체** — `vhh_in/04_Nb_b201_5VNV.json`:

```
Running fold job Nb_b201_5VNV...
Running data pipeline...
Running data pipeline for chain A...
Running data pipeline for chain A took 10.36 seconds
Writing model input JSON to .../Nb_b201_5VNV/Nb_b201_5VNV_data.json
Predicting 3D structure for Nb_b201_5VNV with 1 seed(s)...
Featurising data with seed 1 took 1.09 seconds.
Running model inference with seed 1 took 66.65 seconds.
Extracting 5 inference samples with seed 1 took 0.16 seconds.
Fold job Nb_b201_5VNV done, output written to .../one_out/Nb_b201_5VNV

Done running 1 fold jobs.
real    1m27.303s
```

결과: `ranking_score 0.85`, `pTM 0.85`, `ipTM 없음(단량체)`, 평균 pLDDT 86.8, 잔기 120.
출력 파일 21개 (대표 5종 + 샘플 5개 폴더) 정상 생성.

**복합체** — `cplx_in/01_cAbLys3_1MEL.json` (`--partner-fasta` 로 만든 사슬 2개):

```
Running fold job cAbLys3_1MEL...
Running data pipeline for chain A took 1.72 seconds
Running data pipeline for chain B took 2.29 seconds
Running model inference with seed 1 took 83.33 seconds.
Fold job cAbLys3_1MEL done ...
real    1m39.012s
```

요약 지표 (실측 그대로):

```
{'chain_iptm': [0.92, 0.92], 'chain_pair_iptm': [[0.83, 0.92], [0.92, 0.91]],
 'chain_pair_pae_min': [[0.76, 0.96], [0.93, 0.76]], 'chain_ptm': [0.83, 0.91],
 'fraction_disordered': 0.05, 'has_clash': 0.0, 'iptm': 0.92, 'ptm': 0.87,
 'ranking_score': 0.94}
```

이 결과에는 두 가지 의미가 있다.

1. **JSON 형식이 맞다.** AF3 가 파싱하고, 사슬 2개를 인식해 각각 데이터 파이프라인을
   돌리고, 끝까지 완주했다. 파싱만 통과한 것이 아니라 실물로 구조가 나왔다.
2. **사슬 구성이 의도대로 들어갔다.** `cAbLys3` 는 리소자임의 실제 결합 나노바디이고,
   축소 DB 로도 `ipTM 0.92` / `ranking 0.94` 가 나왔다. 두 사슬이 그냥 각자 접힌 것이
   아니라 결합 자세로 놓였다는 뜻이다. `--partner-fasta` 가 A/B 사슬을 제대로
   구성했음을 결과가 뒷받침한다.

**주의: HMMER 바이너리가 PATH 에 있어야 한다.** 이것 때문에 첫 시도가 실패했다.
연구자 환경(Docker 이미지 `alphafold3`)에는 이미 들어 있지만, conda 네이티브
설치에서는 직접 넣어야 한다:

```
RuntimeError: Could not find the Jackhmmer binary. Check thet it is installed and in
the system PATH. Alternatively, pass in the full path to the binary.
```

이 오류는 **입력 JSON 문제가 아니다.** 이미 `Running fold job ...` 과
`Running data pipeline for chain A...` 까지 진행한 뒤에 나므로, 여기까지 왔다면
JSON 은 이미 통과한 것이다. 해결:

```bash
export PATH=$HOME/miniforge3/envs/af3msa/bin:$PATH   # jackhmmer, nhmmer 가 있는 곳
```

**참고: MSA 없이 형식만 빠르게 확인하려면** `--norun_data_pipeline` 을 쓰면 된다.
다만 그 경로는 MSA 필드가 채워진 JSON 을 요구하므로, `af3_prepare.py` 가 만드는
JSON(MSA 미포함)을 그대로 넣으려면 데이터 파이프라인을 돌려야 한다.
JSON 형식만 확인하려면 AF3 자체 파서를 직접 부르는 것이 가장 빠르다:

```bash
~/miniforge3/envs/af3/bin/python -c "
from alphafold3.common import folding_input
import sys
inp = folding_input.Input.from_json(open(sys.argv[1]).read())
print('OK', inp.name, inp.rng_seeds, [(c.id, type(c).__name__, len(c)) for c in inp.chains])
" vhh_in/01_이름.json
```

---

## 2. af3_visualize.py — 결과 시각화

### 2.1 무엇을 만드는가

타깃 하나당:

- `<타깃>_plddt.png` — 잔기 번호별 pLDDT 꺾은선, 신뢰 구간 배경, 평균선
- `<타깃>_pae.png` — PAE 히트맵, 사슬 경계선

폴더 전체에 하나씩:

- `af3_요약.png` — 왼쪽: 타깃별 ranking score(샘플 산포 포함), 오른쪽: pTM 대 평균 pLDDT
- `af3_시각화표.csv` — 그림에서 읽은 값을 숫자로 확인할 수 있게
- `pymol_색칠.pml` — PyMOL 에서 pLDDT 색칠까지 한 번에
- `chimerax_색칠.cxc` — ChimeraX 용 같은 것

### 2.2 복사해 붙이는 명령

```bash
# 폴더 하나를 전부 그린다
python3 af3_visualize.py vhh_out -o 그림

# 타깃 몇 개만
python3 af3_visualize.py vhh_out -o 그림 --only 01_vhh_001,03_vhh_096

# 2000건일 때: 요약 비교 그림만 (개별 그림 2000장을 만들면 시간과 디스크가 낭비된다)
python3 af3_visualize.py vhh_out -o 그림 --summary-only

# matplotlib 이 없는 환경: 뷰어 스크립트와 표만
python3 af3_visualize.py vhh_out -o 그림 --no-plot

# 라벨을 영문으로 (한글 폰트가 없거나 논문 그림용)
python3 af3_visualize.py vhh_out -o 그림 --lang en
```

### 2.3 실물로 확인한 AF3 출력 JSON 키 구조

`<타깃>_confidences.json` — 최상위 키 6개, 전부 리스트.
아래 "확인한 값" 열은 **모두 같은 타깃 하나**(`~/af3_work/out_bucket/01_L111`, 111잔기 단량체)에서
읽은 것이다. 타깃을 섞으면 서로 안 맞는 숫자를 비교하게 된다:

| 키 | 형태 | 확인한 값 |
|---|---|---|
| `atom_chain_ids` | 문자열 리스트, 길이 = 원자 수 | 867 |
| `atom_plddts` | 실수 리스트 0~100, 길이 = 원자 수 | 867, 첫 값 23.66 |
| `contact_probs` | 토큰 x 토큰 실수 행렬 | 111 x 111 |
| `pae` | 토큰 x 토큰 실수 행렬 (옹스트롬) | 111 x 111 |
| `token_chain_ids` | 문자열 리스트, 길이 = 토큰 수 | 111 |
| `token_res_ids` | 정수 리스트, 1부터 | 111, `[1,2,3] ... [109,110,111]` |

pLDDT 는 **원자별**이고 PAE 는 **토큰별**이다. 이 차이가 중요하다.
잔기별 pLDDT 그림을 그리려면 원자를 잔기로 묶어야 하는데,
원자 -> (사슬, 잔기번호) 매핑은 confidences.json 에 **없다**.
`atom_chain_ids` 는 사슬만 알려주고 잔기 번호는 알려주지 않는다.
그래서 mmCIF 의 `_atom_site` 루프에서 매핑을 가져온다.

`<타깃>_summary_confidences.json` — 최상위 키 10개. 같은 타깃(`01_L111`) 실측 전문:

```
{"chain_iptm": [null], "chain_pair_iptm": [[0.65]], "chain_pair_pae_min": [[0.76]],
 "chain_ptm": [0.65], "fraction_disordered": 0.07, "has_clash": 0.0, "iptm": null,
 "ptm": 0.65, "ranking_score": 0.68}
```

(`chain_ids` 는 길이 111 짜리 `["A","A",...,"A"]` 라서 위 출력에서 뺐다.)

| 키 | 형태 | 확인한 값 |
|---|---|---|
| `ranking_score` | 실수 | 0.68 |
| `ptm` | 실수 0~1 | 0.65 |
| `iptm` | 실수 또는 **null** | `null` (**단량체**) |
| `chain_ptm` | 실수 리스트, 길이 = 사슬 수 | `[0.65]` |
| `chain_iptm` | 리스트, 단량체면 `[null]` | `[null]` |
| `chain_pair_iptm` | 사슬 x 사슬 행렬 | `[[0.65]]` |
| `chain_pair_pae_min` | 사슬 x 사슬 행렬 | `[[0.76]]` |
| `fraction_disordered` | 실수 0~1 | 0.07 |
| `has_clash` | 실수 0/1 | 0.0 |
| `chain_ids` | 문자열 리스트 | **길이 = 토큰 수(111), 사슬 수가 아니다** |

단량체에서는 `ptm` 과 `chain_ptm[0]` 이 같은 값(0.65)이다. 사슬이 하나뿐이므로 당연하다.
복합체에서는 갈라진다 — 위 1.8 의 `cAbLys3_1MEL` 은 `ptm 0.87` 인데
`chain_ptm [0.83, 0.91]` 이다.

**`chain_ids` 는 함정이다.** 이름만 보면 사슬 목록(`["A","B"]`)일 것 같지만
실제로는 **토큰별 사슬 id** 다. 단량체에서 길이 111 짜리 `["A","A",...,"A"]` 가 나온다.
사슬 개수를 알고 싶으면 `len(chain_ptm)` 을 써라. 이 스크립트도 그렇게 한다.

`<타깃>_ranking_scores.csv` — 헤더 `seed,sample,ranking_score`, 샘플 5행:

```
seed,sample,ranking_score
1,0,0.6816427076140422
1,1,0.6465215995191087
1,2,0.6300350444511775
1,3,0.643096496335078
1,4,0.6399458843537466
```

`summary_confidences.json` 의 `ranking_score` 0.68 은 이 5개 중 1위(sample 0)의 값이다.
스크립트가 요약 그림에 찍는 열린 원이 나머지 4개이고, 그 산포가 재현성 지표가 된다.

`<타깃>_model.cif` — `_atom_site` 루프의 열 순서 (실측):

```
group_PDB, id, type_symbol, label_atom_id, label_alt_id, label_comp_id,
label_asym_id, label_entity_id, label_seq_id, pdbx_PDB_ins_code,
Cartn_x, Cartn_y, Cartn_z, occupancy, B_iso_or_equiv,
auth_seq_id, auth_asym_id, pdbx_PDB_model_num
```

첫 ATOM 줄 (같은 타깃 `01_L111`):
`ATOM 1 N N . GLU A 1 1 ? 7.335 -16.111 -8.223 1.00 23.66 1 A 1`
— 15번째 값 `23.66` 이 `B_iso_or_equiv` 이고, 이는 `atom_plddts[0]` (23.66) 과 같다.
끝에서 세 번째/두 번째 값(`1`, `A`)이 `auth_seq_id`(잔기 번호)와 `auth_asym_id`(사슬)이며,
스크립트는 이 두 열로 원자를 잔기에 묶는다.

**B_iso_or_equiv = pLDDT 확인 (실측)**

스크립트가 매번 원자 하나하나 비교해서 알려준다:

```
B값 = pLDDT 확인
  01_L111: 원자 867개 확인, 최대 차 0.0000 (mmCIF 소수 2자리 반올림 범위).
           B_iso_or_equiv = pLDDT 로 확정
  02_L111: 원자 865개 확인, 최대 차 0.0100 (mmCIF 소수 2자리 반올림 범위).
           B_iso_or_equiv = pLDDT 로 확정 [반올림 차이 1개]
  03_L112: 원자 856개 확인, 최대 차 0.0000 ...
```

`02_L111` 에서 원자 1개가 0.01 차이가 났다 (mmCIF `68.62` 대 JSON `68.63`).
mmCIF 는 소수 2자리로 쓰이므로 이 정도 반올림 차이는 정상이다. 이 사실을 알고
스크립트는 두 가지를 지킨다:

1. **그림 값은 항상 confidences.json 의 `atom_plddts`** 를 쓴다 (원본).
   mmCIF 는 원자 -> 잔기 매핑에만 쓴다.
2. B값 확인은 뷰어 색칠 명령(`color ..., b < 70`)이 옳다는 **근거**로만 쓴다.
   허용 오차는 0.0105 (소수 2자리 반올림 한계).

처음 만든 버전은 허용 오차를 0.005 로 잡아서 `02_L111` 을 "불일치"로 판정하고
그 타깃의 그림을 아예 만들지 않았다. 실물로 돌려 보고 잡은 버그다.

### 2.4 구조 보기 (pLDDT 색칠)

`pymol_색칠.pml` 은 이렇게 생성된다 (경로는 그림 폴더 기준 상대경로):

```
load ../af3_work/out_bucket/01_L111/01_L111_model.cif, 01_L111
...
set_color af3_vhigh, [0.051, 0.341, 0.827]
set_color af3_high,  [0.396, 0.796, 0.953]
set_color af3_low,   [1.000, 0.859, 0.075]
set_color af3_vlow,  [1.000, 0.490, 0.271]

color af3_vlow,  all
color af3_low,   b > 50
color af3_high,  b > 70
color af3_vhigh, b > 90
```

쓰는 법:

```bash
pymol 그림/pymol_색칠.pml
# 또는 PyMOL 을 먼저 띄운 뒤
#   @그림/pymol_색칠.pml
```

**함정: PyMOL 선택 문법에는 `>=` 가 없다.** 처음에 자연스럽게
`color af3_low, b >= 50 and b < 70` 로 썼더니 실제 PyMOL 에서 이렇게 죽었다:

```
PyMOL>color af3_low, b >= 50 and b < 70
 Error: b > = 50 and b < 70<--
```

PyMOL 은 `>=` 를 `>` 와 `=` 로 쪼개 읽는다. 그래서 **낮은 색부터 칠하고 위에
덧칠하는** 방식으로 바꿨다 (순서가 중요하다):

```
color af3_vlow,  all
color af3_low,   b > 50
color af3_high,  b > 70
color af3_vhigh, b > 90
```

`util.cbc`(사슬별 색칠) 도 뺐다. pLDDT 색칠 바로 앞에서 사슬 색을 칠하면
결국 덮이므로 의미가 없고, 초보자가 "왜 사슬 색이 안 나오나" 로 혼란만 겪는다.

수정 후 PyMOL(`pymolrender` env)에서 실제로 레이트레이싱까지 돌려 오류 0건,
1500x1150 PNG 생성을 확인했다. 렌더 결과에서 복합체 본체는 파랑(pLDDT 90 이상),
C말단 HA 태그 꼬리만 노랑/주황으로 나왔다 — pLDDT 그림의 135~148 구간 급락과
일치하므로 색칠이 실제 값을 따라간다는 확인이 된다.

ChimeraX:

```bash
chimerax 그림/chimerax_색칠.cxc
```

ChimeraX 쪽은 `color bfactor palette 0,#FF7D45:50,...` 처럼 **색 경계를 직접
적어서** 넣는다. `color bfactor palette alphafold` 라는 내장 팔레트가 있지만
0~1 스케일을 가정하는 버전이 있어서 AF3 의 0~100 값과 맞지 않을 수 있다.
경계를 명시하면 버전에 상관없이 같은 색이 나온다.

온라인 뷰어로 보고 싶으면 `<타깃>_model.cif` 를 그대로 올리면 된다:

- Mol* (https://molstar.org/viewer/) — mmCIF 를 그대로 읽고 B-factor 색칠을 지원한다
- RCSB 3D 뷰어 (https://www.rcsb.org/3d-view) — 파일 업로드 가능

### 2.5 한글 폰트 (두부 방지)

`matplotlib` 은 글리프가 없는 문자를 네모(두부, tofu)로 그린다. 논문 그림에
네모가 박히면 못 쓴다. 그래서 이 스크립트는 **폰트 이름으로 고르지 않고
글리프 존재를 직접 확인한다**:

```python
f = FT2Font(path)
all(f.get_char_index(ord(ch)) != 0 for ch in "한글값")
```

`get_char_index` 가 0 이면 그 문자의 글리프가 없다는 뜻이다.
후보 폰트를 순서대로 검사하고, 하나도 없으면 **라벨 전체를 영문으로 대체**한다.
어느 쪽을 썼는지 실행할 때 알려준다:

```
라벨: 한국어 (폰트 'Noto Sans CJK KR')
```

또는

```
라벨: 영문. 한글 글리프가 있는 폰트를 찾지 못했다.
      한국어 라벨을 원하면 폰트를 설치하고 다시 실행해라:
        Ubuntu/Debian : sudo apt install fonts-noto-cjk
        conda         : conda install -c conda-forge font-ttf-noto-cjk
      (그림이 네모(두부)로 나오는 것을 막기 위해 영문으로 내려갔다.)
```

검증 호스트에는 `/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc`
(`Noto Sans CJK KR`) 가 있어서 한국어로 렌더됐다. `--lang en` 으로 강제하면
영문 라벨로 나오는 것도 실물로 확인했다.

### 2.6 그림에서 무엇을 볼 것인가

**pLDDT 그림**
- 배경 파랑(90 이상) 안에 선이 있으면 그 잔기는 골격도 측쇄도 믿을 만하다.
- 하늘색(70~90)이면 골격은 믿을 만하다. VHH 프레임워크가 보통 여기 있다.
- 노랑/주황으로 내려앉는 곳은 CDR 루프일 가능성이 크다. 나노바디에서 흔하고
  그 자체로 실패는 아니다.
- **프레임워크가 무너지면 그 후보를 의심해라.** 서열이 잘렸거나 프레임 시프트일 수 있다.

**PAE 그림**
- 대각선 근처만 어두우면 각 도메인은 확실하지만 서로의 위치는 불확실하다.
- 단량체 VHH 에서는 전체가 어둡게 나와야 정상이다.
- 복합체에서는 **사슬 경계(붉은 선) 바깥의 사각형**을 봐야 한다. 그 블록이
  어두워야 결합 자세를 믿을 수 있다. 밝으면 두 사슬이 각자 접혔을 뿐이다.

**요약 그림**
- 왼쪽: 파란 점이 오른쪽에 있는 것이 좋은 후보다. 열린 원(같은 서열의 다른 샘플)이
  넓게 퍼져 있으면 재현성이 낮다는 뜻이므로 그 후보는 시드를 늘려 다시 봐라.
- 오른쪽: pTM 과 평균 pLDDT 가 같은 방향을 보는지 확인한다. 한쪽만 높으면
  뭔가 이상하다.

### 2.7 ipTM 은 단량체에서 비어 있다

실측으로 확인했다. 단량체 6종 전부 `iptm: null`, `chain_iptm: [null]` 이다.
스크립트가 이것을 표에서 `-` 로 표시하고 마지막에 알려준다:

```
ipTM 이 전부 비어 있다. 단량체이므로 ipTM 이 없다 (AF3 가 null 로 준다)
사슬이 2개 이상일 때만 계산된다. 항원-나노바디 복합체를 돌리면 채워진다.
```

즉 단량체 전수 스크리닝 단계에서는 ipTM 으로 후보를 고를 수 없다.
`ranking_score` 와 `ptm`, 평균 pLDDT 로 골라야 한다.
계면 신뢰도가 필요하면 `af3_prepare.py --partner-fasta` 로 복합체를 만들어
2단계로 돌려야 한다.

### 2.8 의존성

`matplotlib` 하나. `numpy`, `pandas`, `biopython` 을 쓰지 않는다.
검증 호스트의 시스템 `python3` (3.13.7) 에는 numpy 도 matplotlib 도 없어서
`af3_collect.py` 가 표준 라이브러리만 쓴 것과 같은 이유다.
mmCIF 파싱도 표준 라이브러리로 직접 했다 (AF3 출력은 따옴표 필드가 없는
단순한 루프여서 `split()` 으로 충분하다. 값 개수가 헤더 개수와 다른 줄은 건너뛴다).

`matplotlib` 이 없으면 `--no-plot` 으로 뷰어 스크립트와 표만 만들 수 있고,
`--no-plot` 없이 실행해도 스크립트가 알아서 알려주고 그 두 개만 만든다.

### 2.9 검증 내역

| 무엇 | 결과 |
|---|---|
| 실제 AF3 출력 6타깃(`out_bucket`) 시각화 | pLDDT 6장 + PAE 6장 + 요약 1장 + 표 + 뷰어 스크립트 2개 |
| 이 세션에서 새로 돌린 단량체 1건 | 그림 3장 생성 |
| 이 세션에서 새로 돌린 **복합체** 1건 (사슬 2개) | 그림 3장, 사슬 경계선/사슬 라벨 정상, ipTM 0.92 표시 |
| **값이 원본 JSON 과 일치하는가** | 8타깃 전부 `OK` (아래) |
| 한글 폰트 렌더 | `Noto Sans CJK KR` 사용, 글리프 없는 문자 0개, matplotlib 경고 0건 |
| 영문 폴백 (`--lang en`) | 라벨 전부 영문으로 전환 확인 |
| `--no-plot` (matplotlib 없는 시스템 python3 3.13.7) | 표 + 뷰어 스크립트 2개만 생성, 오류 없음 |
| PyMOL 실제 렌더 | 오류 0건, PNG 생성, 색이 pLDDT 값과 일치 |
| 그림 텍스트 겹침 검사 (bbox) | 6개 그림 전부 겹침 0건 |

값 교차검증은 스크립트를 신뢰하지 않고 **원본 JSON 에서 독립적으로 다시 계산**해
비교했다 (ranking/pTM/ipTM/사슬수/토큰수/잔기수/평균 pLDDT/최소 pLDDT/무질서 비율):

```
OK    6타깃 01_L111 nres=111 ntok=111 nchain=1 mean=69.411 min=24.274
OK    6타깃 02_L111 nres=111 ntok=111 nchain=1 mean=58.420 min=28.458
OK    6타깃 03_L112 nres=112 ntok=112 nchain=1 mean=80.215 min=29.316
OK    6타깃 04_L113 nres=113 ntok=113 nchain=1 mean=69.011 min=28.804
OK    6타깃 05_L129 nres=129 ntok=129 nchain=1 mean=33.577 min=19.146
OK    6타깃 06_L131 nres=131 ntok=131 nchain=1 mean=34.372 min=18.401
OK    단량체 Nb_b201_5VNV nres=120 ntok=120 nchain=1 mean=86.822 min=47.462
OK    복합체 cAbLys3_1MEL nres=277 ntok=277 nchain=2 mean=91.532 min=32.007
전체 일치: True
```

### 2.10 만드는 과정에서 실물로 잡은 버그 2건

기록해 둔다. 둘 다 코드를 읽어서는 안 나오고 실제로 돌려야 나오는 것들이다.

**(1) B값 허용 오차를 너무 좁게 잡아 타깃 하나를 통째로 잃었다**

처음 버전은 mmCIF B값과 `atom_plddts` 가 0.005 이내로 같아야 "일치"로 보고,
불일치면 그 타깃의 잔기 매핑을 포기해 그림을 아예 만들지 않았다.
`02_L111` 에서 원자 1개가 `68.62` 대 `68.63` 으로 0.01 차이가 났고
(mmCIF 는 소수 2자리로 쓰인다), 그 결과 6타깃 중 1개가 표에서 `-` 로 빠졌다.

고친 방식:
- 그림 값은 **항상** `atom_plddts`(원본)를 쓴다. mmCIF 는 잔기 매핑에만 쓴다.
- 허용 오차를 0.0105 로 올렸다 (소수 2자리 반올림 한계).
- 진짜로 다르면(원자 수 불일치 등) 그림은 원본 값으로 그리고, 뷰어 색칠 명령을
  못 믿는다는 경고를 그림 안과 로그에 남긴다.

**(2) PyMOL 스크립트가 문법 오류로 색칠을 못 했다** — 위 2.4 참조.
`>=` 가 없어서 4줄 중 3줄이 죽었고, `b < 50` 한 줄만 먹혀 구조가 거의 통짜
주황으로 나왔다. PyMOL 은 스크립트를 계속 실행하고 종료 코드도 0 이라
**로그를 읽지 않으면 모른다.** 색이 이상하면 반드시 PyMOL 콘솔의 Error 를 확인해라.

---

## 3. 예제 입력 (실제 공개 서열)

`example_input.fasta` / `example_input.csv` 는 전부 RCSB PDB 에 공개된
실제 나노바디 서열이다. 원본 그대로(as deposited) 옮겼고 편집하지 않았다.
발현 태그(His, HA)도 원본에 있는 대로 남겼다 — 태그가 붙어 길이가 늘면
버킷이 128 에서 256 으로 넘어가는 것을 보여주기 위해서다.

| 이름 | 출처 | 길이 | 버킷 |
|---|---|---|---|
| `cAbLys3_1MEL` | PDB 1MEL entity 1 (chain A/B), 항리소자임 낙타 VHH. C말단 HA 태그 포함 | 148 | 256 |
| `GFPnb_Minimizer_3OGO` | PDB 3OGO entity 2 (chain E-H), GFP 결합 나노바디. His 태그 포함 | 123 | 128 |
| `GFPnb_Enhancer_3K1K` | PDB 3K1K entity 2 (chain C/D), GFP 결합 나노바디 Enhancer | 123 | 128 |
| `Nb_b201_5VNV` | PDB 5VNV entity 1 (chain A), 합성 나노바디 Nb.b201 | 120 | 128 |
| `VHH_9G8_4KRP` | PDB 4KRP entity 2 (chain B), 항EGFR VHH 9G8 | 136 | 256 |

`example_antigen.fasta` 는 `--partner-fasta` 예제다:
PDB 1MEL entity 2 (chain L/M), 계란 흰자 리소자임 129 잔기 —
`cAbLys3` 의 실제 항원이다. 이 조합으로 복합체를 만들면 진짜 결합쌍이므로
ipTM 이 높게 나와야 정상이고, 파이프라인 점검용으로 쓸 수 있다.

서열은 RCSB Data API (`data.rcsb.org/rest/v1/core/polymer_entity/...`) 의
`entity_poly.pdbx_seq_one_letter_code_can` 을 그대로 받아 썼다 (2026-08 조회).

CSV 쪽은 열 이름을 일부러 한국어(`클론명`, `아미노산서열`)로 두었다.
열 이름 자동 인식이 한국어에서도 되는지 확인하는 용도다.

---

## 4. 2000건에 적용할 때 (권장 순서)

```bash
# 1) 검사부터. 만들지 않는다. 오류가 있으면 여기서 잡힌다
python3 af3_prepare.py --csv vhh_2000.csv -o vhh_in --dry-run --report prep.csv

# 2) 버킷 분포를 봐라. 256 버킷 비율이 높으면 태그를 뺄지 결정한다
#    (128 -> 256 은 건당 2.25배 느려진다. 2000건이면 몇 시간 차이다)

# 3) 실제 생성
python3 af3_prepare.py --csv vhh_2000.csv -o vhh_in --report prep.csv

# 4) 사이드카 확인 (폴더를 macOS 에서 옮겼다면 반드시)
find vhh_in -name '._*' -delete
ls vhh_in | wc -l          # 2000 이어야 한다

# 5) 실행 (af3_batch.py 는 단일 프로세스로 순회한다)
python3 af3_batch.py --input_dir vhh_in --output_dir vhh_out

# 6) 표로 집계
python3 af3_collect.py vhh_out -o af3_결과요약.csv --top 100 --top-list top100.txt

# 7) 그림. 2000건 전부 그리지 말고 요약 + 상위 후보만
python3 af3_visualize.py vhh_out -o 그림 --summary-only
python3 af3_visualize.py vhh_out -o 그림_상위 --only "$(paste -sd, top100.txt | cut -c1-500)"
```

---

## 5. 알려진 한계

- `af3_prepare.py` 는 리간드가 있을 때 **토큰 수를 정확히 세지 못한다**.
  단백질 부분만 센다. CCD 성분의 원자 수는 `ccd.pickle` (543MB) 을 읽어야 알 수 있어서
  가벼운 스크립트에 넣지 않았다. `--ligand-smiles` 는 rdkit 이 있으면 heavy atom 을
  정확히 센다. 리간드가 있으면 요약에 그 사실을 명시한다.
- `af3_prepare.py` 는 `modifications`(PTM), `bondedAtomPairs`, `userCCD`,
  RNA/DNA 사슬, MSA 직접 주기(`unpairedMsa`)를 지원하지 않는다.
  이 기능들은 JSON 을 손으로 쓰거나 다른 도구가 필요하다. 스펙은 위 1.6 에 적어 두었다.
- `af3_visualize.py` 의 mmCIF 파서는 AF3 출력 전용이다. 따옴표로 감싼 필드나
  여러 모델이 든 일반 mmCIF 는 제대로 못 읽는다. AF3 출력은 그런 경우가 없다.
- PAE 히트맵의 색 상한을 31.75 옹스트롬에 고정했다 (AF3/AF2 의 PAE 최대 bin).
  값이 그보다 크면 같은 색으로 포화된다.
