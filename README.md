# Kang_AF3: AlphaFold 3 대량 스크리닝 도구 모음

VHH/나노바디처럼 짧은 단백질 수백에서 수천 건을 AlphaFold 3로 한 번에 돌리기 위한
스크립트와 한국어 문서다. 실험 위주로 일하다가 처음 구조예측을 돌리는 사람이 이 문서를
위에서 아래로 따라가면 설치부터 결과 해석까지 끝난다.

성능 수치는 실제로 측정한 값이다. 측정하지 않은 것은 `(미측정)` 또는 `(추정)` 으로
표시했다. 측정 환경과 한계는 [12절](#12-측정-조건과-한계)에 있다.

---

## 5분 요약

**무엇을 하는 도구인가.** JSON 하나마다 `docker run` 을 새로 띄우는 방식을 컨테이너
1회 기동으로 바꾼다. GPU 추론 단계가 건당 31.95초에서 5.39초로 **5.93배** 빨라지고
(32건 곱하기 3반복 중앙값), 2000건 스크리닝이 축소 DB 구성에서 약 4.1시간이다
(연구자 현재 방식 189시간). 필요한 것은 AF3 소스, 모델 가중치(Google DeepMind 승인 필요),
서열 DB 세 개이고 모두 이 저장소에 없다
([3-1 다운로드 목록](#3-1-다운로드-목록)).

**전체 흐름은 명령 4줄이다.**

```bash
# 0. 환경이 제자리에 있는지 (GPU, 가중치, DB, 도커, HMMER)
bash scripts/af3_check.sh

# 1. FASTA/CSV 에서 입력 JSON 을 타깃당 하나씩 만든다 -> vhh_001_in/
python3 scripts/af3_prepare.py --fasta examples/vhh_panel.fasta -o vhh_001_in

# 2. 전수 실행. 컨테이너 1회 기동으로 vhh_001_in/ 전체를 순회한다 -> vhh_001_out/
python3 scripts/run_af3_batch_improved.py --input-dir vhh_001_in --output-dir vhh_001_out --yes

# 3. 결과를 CSV 한 장으로 집계한다
python3 scripts/af3_collect.py vhh_001_out -o vhh_001_결과요약.csv
```

**결과를 어떻게 읽나.** CSV 의 `등급` 열로 걸러내고, 단량체는 pTM 과 pLDDT평균, 복합체는
ipTM 을 본다. 신뢰도는 정답과의 일치도가 아니라 모델의 자기 확신이므로 실험 검증 대상을
줄이는 순위 지표로만 쓴다 (판정 기준은 [8절](#8-결과-해석)). **처음이면** 2절 요구 사양,
3절 설치, 4절 스모크 테스트 1건, 5절 입력 준비, 6절 전수 실행, 8절 해석 순서로 읽어라.

---

## 목차

[1. 이게 무엇인가 / 무엇이 아닌가](#1-이-저장소의-범위) ·
[2. 요구 사양](#2-요구-사양) · [3. 설치](#3-설치) · [4. 동작 확인](#4-동작-확인) ·
[5. 입력 파일 준비](#5-입력-파일-준비) · [6. 배치 실행](#6-배치-실행) ·
[7. 왜 빠른가](#7-속도-개선의-근거) · [8. 결과 해석](#8-결과-해석) ·
[9. 결과 보기](#9-결과-보기) · [10. 자주 만나는 문제](#10-자주-만나는-문제) ·
[11. 라이선스와 인용](#11-라이선스와-인용) · [12. 측정 조건과 한계](#12-측정-조건과-한계)

---

## 1. 이 저장소의 범위

AlphaFold 3 를 대량으로 돌리기 위한 껍데기다. 스크립트 8개를 제공한다.

| 스크립트 | 하는 일 |
|----------|---------|
| `scripts/af3_check.sh` | 환경 진단. GPU, 드라이버, 가중치, DB, 도커, HMMER |
| `scripts/af3_prepare.py` | FASTA/CSV 에서 AF3 입력 JSON 생성 |
| `scripts/run_af3_batch_improved.py` | 권장 배치 러너. 완료 판정을 최종 산출물로 하고 미완료 결과를 격리 보존하며 중복 실행을 차단한다 |
| `scripts/af3_batch.py` | 배치 러너. 컨테이너 1회 기동, MSA/추론 2단계 분리, 재시작, 재시도 |
| `scripts/af3run.sh` | 위 러너의 래퍼. 작업 이름 하나만 주면 된다 |
| `scripts/af3_collect.py` | 출력 폴더 전체를 훑어 신뢰도 지표 CSV 한 장으로 |
| `scripts/af3_visualize.py` | pLDDT 플롯, PAE 히트맵, PyMOL/ChimeraX 색칠 명령 생성 |
| `scripts/af3_stage2.py` | `_data.json` 재사용으로 MSA 를 건너뛰는 2단계 입력 생성 |

**AlphaFold 3 자체가 아니다.** AF3 코드도, 가중치도, 데이터베이스도 이 저장소에 없다
([3-1](#3-1-다운로드-목록)). 그리고 **이 저장소는 공개다. 가중치 파일,
`ccd.pickle`, DB 파일, 실제 연구 서열은 커밋하지 않는다.** `.gitignore` 가 1차 방어선이고
최종 책임은 커밋하는 사람에게 있다 ([11절](#11-라이선스와-인용)).

타깃 10건 이하면 필요 없다. AF3 공식 명령을 그대로 쓰면 된다. 타깃 100건 이상이고
서열이 짧고 서로 비슷하다면(항체 라이브러리, 나노바디 패널, 점돌연변이 시리즈)
건당 고정 오버헤드가 전체 시간을 지배한다. 이 저장소가 다루는 상황이 그것이다.

---

## 2. 요구 사양

| 항목 | 요구 | 근거 |
|------|------|------|
| GPU | **16GB 면 충분하고도 남는다.** Blackwell sm_120 (RTX 5070 Ti / 5090 계열) 확인 | 실측 |
| 실제 VRAM 피크 (VHH 116~144 aa, sample 5 곱하기 recycle 10) | **2,942~2,963 MiB** | gpu-5070ti 23런 |
| CPU | 8코어 이상 권장. MSA 단계 속도를 직접 결정한다 | 실측 |
| RAM | 검증 호스트는 126GB. 축소 DB 만 쓰면 훨씬 적어도 된다 (하한 미측정) | |
| 디스크 | 축소 DB 경로 약 5GB, 전체 DB 경로 약 855GB. 전체 DB 계획이면 여유 1TB | 실측 |

`nvidia-smi` 는 15,157 MiB 를 쓰고 있다고 표시한다. **이건 수요가 아니라 XLA 의
선점량(미리 예약한 메모리)이다.** 이 숫자를 보고 VRAM 이 부족하다고 판단하면 틀린다.

![gpu-5070ti 추론 실측: 컴파일 상환, 캐시 효과, VRAM 선점 대 실제 요구량](figures/baseline_gpu5070ti.png)

원 측정값 (카드 총량 16,303 MiB): 선점 ON 15,157 MiB(**예약량이다. 수요가 아니다**),
선점 OFF 스모크 1건 5,291 MiB, 선점 OFF 배치 23런 2,942~2,963 MiB. 뒤 두 값의 차이는
계측 조건 차이(단발 실행 대 순회 정상상태)이고 어느 값이든 16GB 카드에 여유롭게 들어간다
([docs/benchmark_report.md](docs/benchmark_report.md)). 위 그림 (c) 의 GB 표기는 환산
기준(1024 대 1000)이 섞여 있으므로 **정확한 값이 필요하면 이 문단의 MiB 를 쓴다.**

### 동작이 확인된 버전 조합

아래 조합에서 처음부터 끝까지 돌아갔다. 막히면 여기로 돌아오라.

AF3 commit `97d20234c6eb89e8d05376e9eecc9321e60a559b` (tag `v3.0.4-15-g97d2023`),
Python 3.12.13 (AF3 가 `requires-python >=3.12`), JAX/jaxlib 0.10.2
(`jax[cuda12]==0.10.2`), jax-cuda12-plugin/pjrt 0.10.2, CUDA 12.9 (JAX 번들: cublas
12.9.2.10, cudnn 9.24.0.43, nvcc 12.9.86, runtime 12.9.79, nccl 2.31.2), numpy 2.5.2,
rdkit 2025.9.4, dm-haiku 0.0.17, tokamax 0.0.12, HMMER 3.4 + AF3 의 `--seq_limit` 패치.

`flash_attention` 은 triton 기본값으로 sm_120 에서 동작한다. **시스템 nvcc 를 따로 깔
필요가 없다.** JAX 가 번들로 가져온다. PTX 관련 오류는 나오지 않았다. 설치 실측 기록은
[docs/install_log.md](docs/install_log.md).

---

## 3. 설치

설치 경로는 두 갈래이고 갈리는 지점은 데이터베이스다.

| 경로 | 실작업 시간 | 디스크 | 어떤 경우에 |
|------|-------------|--------|-------------|
| **A. 축소 DB** | 약 **40분~1시간 30분** | 약 5GB | 단량체 전수 스크리닝. 대부분의 경우 이쪽 |
| **B. 전체 DB** | 약 **4~5시간** | 약 **855GB** | 복합체(항원 결합) 예측, 소수 정밀 재계산 |

두 경로 모두 가중치 접근 승인 대기 시간은 별도다 (Google 측 처리, 수일 걸릴 수 있음, 추정).
**승인 요청은 설치 첫날에 넣어 두고 나머지를 진행하면 대기 시간이 겹친다.**

주 경로는 Docker 다. Docker 를 못 쓰는 경우의 conda 네이티브 설치는 [3-7](#3-7-conda-네이티브-설치-docker-를-못-쓰는-경우)에 있다.

### 3-1. 다운로드 목록

| 단계 | 받을 것 | 용량 | 어디서 | 시간 |
|------|---------|------|--------|------|
| ① | **AF3 소스 코드** | 수십 MB | https://github.com/google-deepmind/alphafold3 (Apache 2.0) | 1~2분 (추정) |
| ② | **도커 이미지** | 수십 GB (추정, 중간 레이어 포함) | 위 소스의 `docker/Dockerfile` 로 빌드 | 20~40분 (**미측정 추정.** 검증 호스트에 Docker 없음) |
| ③ | **가중치 접근 승인** | | **Google DeepMind 에 직접 요청.** 우리가 대신 받아줄 수 없다 | 요청 10분, **승인 대기 수일 (추정)** |
| ④ | **모델 가중치 `af3.bin`** | 1,146,811,260 B (약 1.15GB) | 승인 후 안내받은 경로 | 1~5분 (회선에 따라) |
| ⑤ | **`ccd.pickle`** | 542,994,372 B (약 543MB) | 받는 것이 아니라 `build_data` 로 굽는다 | 수 분 (미측정). 설치 시 1회 |
| ⑥-A | **축소 DB** | 약 2GB | 공식 축소 세트는 없다. 전체 DB 파일을 잘라 만든다 (3-5) | 10~20분 |
| ⑥-B | **전체 DB** | 압축 238.8GB, 해제 후 **850GB 점유** | `fetch_databases.sh` (승인 불필요) | **3시간 13분 (실측)** |
| ⑦ | 첫 실행 컴파일 | | | **최대 406~497초 (실측)**, 이후 웜 6.55~8.5초 |
| | **이 저장소** | 수 MB | https://github.com/kangk1204/Kang_AF3 | 스크립트와 문서만 |

가중치는 **비영리 목적으로만 쓸 수 있고 재배포가 금지**돼 있다. 약관은 구글로부터 직접
받은 경우만 사용을 허용하므로 동료에게 복사해 받으면 위반이다. `ccd.pickle` 과 DB 파일도
저장소에 커밋하면 안 된다 ([11절](#11-라이선스와-인용),
[docs/license_notes.md](docs/license_notes.md)).

### 3-2. AF3 소스와 도커 이미지

```bash
mkdir -p ~/af3_work && cd ~/af3_work            # 작업 폴더를 하나 정한다
git clone https://github.com/google-deepmind/alphafold3.git
cd alphafold3
git checkout 97d20234c6eb89e8d05376e9eecc9321e60a559b   # 확인된 커밋 (권장)
sudo docker build -t alphafold3 -f docker/Dockerfile .  # 20~40분
sudo docker image ls | grep alphafold3                  # 빌드 확인
```

이미지 이름을 `alphafold3` 로 두면 이 저장소의 스크립트가 기본값으로 찾는다. 다른 이름은
`--image` 또는 환경변수 `AF3_IMAGE` 로 알려준다. 빌드 중 화면이 몇 분씩 멈춘 것처럼
보이는 구간이 있다(HMMER 컴파일 등). 정상이다.

### 3-3. 모델 가중치 확보

**이 단계는 우리가 대신 해줄 수 없다.**

AF3 공식 저장소의 가중치 요청 안내를 따라 접근 요청 양식을 제출하고(소속, 용도는
비영리 연구, 이름을 정확히 쓴다), 승인 메일을 기다린 뒤(수일, 추정) 안내받은 방법으로
`af3.bin`(또는 `af3.bin.zst`)을 받아 `~/af3_models` 에 두고 크기와 해시를 확인한다.

```bash
mkdir -p ~/af3_models
mv ~/Downloads/af3.bin ~/af3_models/
# .zst 로 받았으면 풀어야 한다: zstd -d ~/af3_models/af3.bin.zst

ls -l ~/af3_models/af3.bin
sha256sum ~/af3_models/af3.bin
# 우리가 확인한 값
#   크기   : 1146811260  (약 1.15GB)
#   sha256 : df8bbf2621f17dd3ee21c2a921e84a50bc2b80cdc0c7971cb915c2826fee1f9b
```

`af3.bin.zst` 는 1,020,545,840 B 이고 가중치 안에는 파라미터가 368,384,602개 있다.
해시가 다르면 최신 버전일 수 있다. 먼저 **크기가 절반 이하인지** 확인한다.
절반 이하라면 다운로드가 잘린 것이다.

### 3-4. build_data: 화학 성분 사전 굽기

AF3 는 화학 성분 사전을 pickle 로 미리 구워 둔다. **설치 시 1회만** 하면 되고,
도커 이미지 빌드 과정에 포함돼 있으면 건너뛴다.

```bash
sudo docker run --rm alphafold3 build_data      # 이미 있으면 아무것도 안 한다
```

생성물은 `ccd.pickle` 542,994,372 B (화학 성분 50,942종)와
`chemical_component_sets.pickle` 8,424 B 다 (실측). 수 분 걸린다 (미측정).
**이 파일도 저장소에 커밋하면 안 된다.**

### 3-5. 데이터베이스 선택

두 선택지의 실측 차이:

| 항목 | 축소 DB (약 2GB) | 전체 DB (850GB) | 배수 |
|------|------------------|-----------------|------|
| MSA unpaired / paired 깊이 | 9~13 / 158~225 | 10,640~10,745 / 24,250~27,353 | **818~1,186배 / 120~150배** |
| 건당 시간 (end-to-end) | 43.3초 | 1,830초 | **42.2배** |
| 2000건 환산 | 24시간 | **1,017시간 (42일)** | 해당 없음 |

MSA 깊이가 1000배 차이나는데 **VHH 단량체의 신뢰도는 거의 변하지 않았다.** 6종을 양쪽
조건으로 모두 돌린 결과 ranking score 는 무변화 3건, +0.03 2건, -0.01 1건이었다
(0.82~0.90 범위, pLDDT평균 차이 -0.99~+2.12). 그 -0.01 은 같은 조건에서 샘플 5개를
돌렸을 때의 산포(0.002~0.008)를 살짝 넘지만 판정을 바꿀 크기가 아니다
(타깃별 값은 [results_example/af3_summary.csv](results_example/af3_summary.csv)).
나노바디는 면역글로불린 폴드가 잘 보존돼 있고 PDB 에 템플릿이 아주 많은데
**템플릿 검색은 양쪽 조건 모두 전체 PDB 를 쓴다.** 축소 DB 로 잃는 것은 공진화 신호이고
VHH 프레임워크는 그 신호 없이도 템플릿만으로 잡힌다.

![축소 DB 대 전체 DB 신뢰도 비교](figures/db_confidence_comparison.png)

> 위 표의 818~1,186배는 AF3 가 DB 4종 결과를 합치고 중복을 제거한 뒤 최종 입력에 담은
> unpaired 깊이 기준이다. uniref90 하나만 검색해 나온 정렬 서열 수 기준으로는
> 31~34배(축소 705~811, 전체 23,693~25,503)이므로 **한 쪽 값을 다른 쪽과 섞어 인용하지
> 말 것.** 타깃별 짝은 [docs/db_notes.md](docs/db_notes.md) 에 있다.

어느 쪽을 골라야 하나:

- 단량체 VHH 를 수백에서 수천 건 전수 스크리닝한다: **축소 DB.** 전체 DB 로 2000건은
  42일이라 애초에 선택지가 아니다.
- 항원-나노바디 복합체의 결합을 보고 싶다(ipTM 이 필요하다): **전체 DB.** paired MSA 가
  120~150배 차이나므로 계면 예측에는 사실상 필수로 보인다. 단 이건 추론이고, 비교한
  6종이 모두 단량체여서 ipTM 이 산출되지 않았다 ([12절](#12-측정-조건과-한계)).
- 실용적인 조합은 축소 DB 로 전수 스크리닝하고 상위 후보 수십 건만 전체 DB 로 재계산하는 것이다.

**이미 축소 DB 로 돌려 놓은 결과는 버릴 필요가 없다.** 단량체 신뢰도 기준으로는
전체 DB 결과와 실질적으로 같다.

#### ⑥-B 전체 DB 다운로드와 해제 (3시간 13분, 실측)

축소 DB 도 전체 DB 파일을 잘라 만들기 때문에 전체 DB 를 먼저 적었다.
축소 DB 만 볼 것이면 ⑥-A 로 건너뛰면 된다.

AF3 소스에 다운로드 스크립트가 들어 있다. 인자로 받을 폴더를 준다.

```bash
sudo apt install -y wget tar zstd        # 스크립트가 이 셋을 요구한다
cd ~/af3_work/alphafold3
bash fetch_databases.sh ~/public_databases
```

스크립트는 아래 9개를 `https://storage.googleapis.com/alphafold-databases/v3.0` 에서
받아 압축을 풀며, 9개를 동시에 내려받는다. 승인이나 계정이 필요하지 않다.

| 파일 | 무엇 |
|------|------|
| `pdb_2022_09_28_mmcif_files.tar` | 템플릿 검색용 PDB 구조. 해제하면 195,859개 파일 |
| `uniref90_2022_05.fa` | 단백질 MSA 주력 |
| `mgy_clusters_2022_05.fa` | MGnify. 환경 시료 유래 |
| `bfd-first_non_consensus_sequences.fasta` | BFD |
| `uniprot_all_2021_04.fa` | paired MSA 용. 복합체에서 중요하다 |
| `pdb_seqres_2022_09_28.fasta` | PDB 서열 |
| `rnacentral_...linclust.fasta` | RNA |
| `nt_rna_2023_02_23_...rep_seq.fasta` | RNA |
| `rfam_14_9_...rep_seq.fasta` | RNA |

받는 도중에 끊기면 그 파일만 반쪽으로 남고, 스크립트를 다시 돌리면 처음부터
다시 받는다. 끝난 뒤 크기를 확인해 두면 나중에 원인을 찾기 쉽다.

```bash
du -sh ~/public_databases                       # 850GB 정도여야 한다
ls -la ~/public_databases/*.fa ~/public_databases/*.fasta
ls ~/public_databases/mmcif_files | wc -l       # 195859
```

#### ⑥-A 축소 DB 준비 (전체 DB 를 이미 받았으면 10~20분)

**공식 저장소에는 축소 세트가 없다.** 우리가 측정에 쓴 축소 DB 는 전체 DB 파일의
앞부분을 서열 경계에서 잘라 만든 대리 세트다. 연구자분이 쓰시는 약 2GB 파일과
구성이 같지 않을 수 있으므로, MSA 깊이 절대값은 다를 수 있다.

FASTA 를 목표 크기까지 읽되 서열 중간에서 끊기지 않게 자른다.

```bash
mkdir -p ~/public_databases_reduced

# 단백질 DB 4종을 목표 크기로 자른다 (서열 경계에서 끊는다)
python3 - <<'EOF'
import pathlib
SRC = pathlib.Path.home() / "public_databases"
DST = pathlib.Path.home() / "public_databases_reduced"
DST.mkdir(exist_ok=True)
TARGET = {                                  # 우리가 쓴 크기 (바이트)
    "uniref90_2022_05.fa": 520_000_000,
    "bfd-first_non_consensus_sequences.fasta": 420_000_000,
    "mgy_clusters_2022_05.fa": 420_000_000,
    "uniprot_all_2021_04.fa": 320_000_000,
    "rnacentral_active_seq_id_90_cov_80_linclust.fasta": 60_000_000,
    "nt_rna_2023_02_23_clust_seq_id_90_cov_80_rep_seq.fasta": 60_000_000,
}
for name, limit in TARGET.items():
    src = SRC / name
    if not src.exists():
        print("없음, 건너뜀:", name); continue
    written = 0
    with open(src, "rb") as fi, open(DST / name, "wb") as fo:
        for line in fi:
            if written >= limit and line.startswith(b">"):
                break                       # 다음 서열이 시작되는 지점에서 끊는다
            fo.write(line); written += len(line)
    print("%12d  %s" % (written, name))
EOF

# 아래 셋은 자르지 않고 그대로 쓴다 (원래 작다)
cp ~/public_databases/pdb_seqres_2022_09_28.fasta ~/public_databases_reduced/
cp ~/public_databases/rfam_14_9_*.fasta           ~/public_databases_reduced/

# 템플릿용 PDB 구조는 링크로 공유한다 (복사하면 용량이 두 배가 된다)
ln -s ~/public_databases/mmcif_files ~/public_databases_reduced/mmcif_files
```

우리가 쓴 축소본의 실제 크기다.

| 파일 | 크기 | 서열 수 |
|------|------|---------|
| `uniref90_2022_05.fa` | 519,998,727 B | 71,974 |
| `bfd-first_non_consensus_sequences.fasta` | 419,999,948 B | 3,242,672 |
| `mgy_clusters_2022_05.fa` | 419,999,469 B | 1,886,706 |
| `uniprot_all_2021_04.fa` | 319,999,311 B | 633,249 |
| `rfam_14_9_...rep_seq.fasta` | 228,433,680 B | 자르지 않음 |
| `nt_rna_...rep_seq.fasta` | 59,999,964 B | |
| `rnacentral_...linclust.fasta` | 59,911,226 B | |
| `pdb_seqres_2022_09_28.fasta` | 916,475 B | 자르지 않음 |

**템플릿 검색용 구조 파일은 자르지 않는다.** 축소 DB 로도 VHH 신뢰도가 거의 떨어지지 않은 이유가
템플릿이 그대로 있었기 때문이다. `mmcif_files` 를 잘라내면 그 이점이 사라진다.

`--db_dir` 로 어느 쪽을 쓸지 고른다.

```bash
python3 scripts/run_af3_batch_improved.py --db-dir ~/public_databases_reduced --yes
```

실측 (4병렬 다운로드, 평균 약 41MB/s 회선): 압축 파일 238.8GB 다운로드 **1시간 37분**,
그중 mmCIF tar 해제가 **1시간 36분**(195,859개 파일, 최장 단계), 다운로드 시작부터 전량
해제 완료까지 **3시간 13분**, 해제 후 점유 **850GB**. 무결성은 9개 항목 전부 원격
`Content-Length` 와 바이트 단위로 일치했다.

mmCIF 해제 중에 진행이 멈춘 것처럼 보일 것이다. 파일이 20만 개라 그렇다.
`du -sh ~/public_databases` 로 조금씩 늘어나는지만 확인하면 된다.

공식 문서의 약 252GB / 630GB 와 우리 실측(238.8GB / 850GB)이 다른 것은 단위 해석
(GB 대 GiB)과 파일시스템 블록 반올림 때문이다. **디스크는 실측값 850GB 를 기준으로
준비하라.** 상세 기록은 [docs/db_notes.md](docs/db_notes.md).

### 3-6. 폴더 관례

스크립트는 아래 배치를 기본으로 가정한다. 이대로 쓰면 옵션을 거의 안 줘도 된다.
`<이름>` 은 작업 하나를 가리키는 이름이다 (예: `vhh_001`).

```
~/public_databases/          서열 DB
~/af3_models/                가중치 (af3.bin)
~/af3_work/                  작업 폴더. 여기서 명령을 실행한다
    <이름>_in/               입력 JSON
    <이름>_out/              결과
    <이름>_work/             로그, MSA 보관, 요약 CSV (스크립트가 만든다)
```

### 3-7. conda 네이티브 설치 (Docker 를 못 쓰는 경우)

sudo 권한이 없거나 Docker 가 없는 서버에서는 conda 로 직접 설치할 수 있다. 검증 호스트가
그 경우였다.

```bash
conda create -y -n af3 python=3.12
conda activate af3
conda install -y -c conda-forge cmake zlib hmmer
export CMAKE_PREFIX_PATH=$CONDA_PREFIX   # 시스템 zlib-dev 가 없을 때 필요
cd ~/af3_work/alphafold3
pip install "jax[cuda12]==0.10.2"
pip install .
build_data
```

전체 명령과 그때 만난 문제와 해결은 [docs/install_log.md](docs/install_log.md) 에 있다.
우리 수치는 conda 네이티브 측정이므로 컨테이너 기동 비용이 빠져 있고,
**우리 값은 Docker 환경의 하한이다.** Docker 에서는 건당 시간이 같거나 조금 더 나온다.

### 3-8. 첫 실행 지연

처음 한 번은 XLA 가 GPU 커널을 컴파일한다. 관측한 프로세스 고정 오버헤드는 콜드/고부하
상태에서 **406~497초**까지 올라갔고 웜 상태에서는 6.55~8.5초다.
**첫 실행이 5~8분 걸려도 고장이 아니다.**

컴파일 캐시 디렉터리를 지정하면 첫 컴파일을 재사용할 수 있다(스크립트가 기본으로 한다).
다만 캐시의 이득은 배치가 커지면 0으로 수렴한다. 첫 2건의 컴파일만 없애기 때문이고,
96건 순회에서 정상상태 4.20초는 캐시 유무와 무관했다 (실측).

---

## 4. 동작 확인

실행 전에 환경을 점검한다. 이 단계를 건너뛰면 3시간 돌리고 나서 DB 경로가 틀렸다는 것을
알게 된다.

```bash
cd ~/af3_work
bash scripts/af3_check.sh 2>&1 | tee af3_check.txt
```

아래 6개는 반드시 통과해야 한다.

| 확인 항목 | 통과 기준 |
|-----------|-----------|
| GPU 인식 | `nvidia-smi` 가 GPU 이름과 VRAM 을 출력한다 |
| 도커 이미지 | `alphafold3` 이미지가 목록에 있다 |
| 가중치 | `~/af3_models/af3.bin` 이 있고 크기가 약 1.15GB |
| DB 경로 | `~/public_databases` 가 있고 비어 있지 않다 |
| HMMER | `jackhmmer -h` 가 동작하고 `--seq_limit` 항목이 보인다 |
| 디스크 | 작업 폴더에 여유가 있다 |

출력 형식은 스크립트 버전에 따라 다르다. 항목별 판정 표시(OK/실패)를 기준으로 읽는다.
`--seq_limit` 이 안 보이면 AF3 패치가 적용되지 않은 HMMER 다(도커 이미지를 쓰면 보통
문제되지 않는다). 실패 항목은 [10절](#10-자주-만나는-문제)에 정리해 두었다.

### 스모크 테스트: 1건 실제로 돌려 보기

```bash
mkdir -p ~/af3_work/smoke_in
cp examples/vhh_monomer.json ~/af3_work/smoke_in/
cd ~/af3_work
python3 scripts/af3_batch.py --name smoke --stage oneshot
ls smoke_out/*/
```

첫 실행이라 5~8분 걸릴 수 있다 ([3-8](#3-8-첫-실행-지연)). 끝나면
`smoke_out/vhh_7mfv_1/` 안에 `*_summary_confidences.json` 과 `*_model.cif` 가 생긴다.
여기까지 되면 설치는 끝이다.

---

## 5. 입력 파일 준비

AF3 는 **타깃 하나당 JSON 파일 하나**를 입력으로 받는다. 2000건이면 JSON 2000개이므로
`af3_prepare.py` 로 만들고, 만들어진 JSON 은 `<이름>_in/` 에 둔다
([3-6](#3-6-폴더-관례)).

### 5-1. 입력 JSON 의 구조

가장 단순한 형태는 단백질 사슬 하나다 (`examples/vhh_monomer.json`). 아래 서열은
PDB 7MFV(합성 나노바디 Sb16, 116 aa)의 실제 서열이다.

```json
{
  "name": "vhh_7mfv_1",
  "modelSeeds": [1],
  "sequences": [
    {
      "protein": {
        "id": "A",
        "sequence": "QVQLVESGGGLVQAGGSLRLSCAASGFPVAYKTMWWYRQAPGKEREWVAAIESYGIKWTRYADSVKGRFTISRDNAKNTVYLQMNSLKPEDTAVYYCIVWVGAQYHGQGTQVTVSA"
      }
    }
  ],
  "dialect": "alphafold3",
  "version": 1
}
```

| 필드 | 뜻 |
|------|-----|
| `name` | 타깃 이름. **출력 폴더 이름이 이걸로 만들어진다.** 공백, 한글, 슬래시 피할 것 |
| `modelSeeds` | 난수 시드 배열. `[1]` 이면 시드 1개. 늘리면 시간도 비례해 늘어난다 |
| `sequences` | 이 구조에 들어가는 모든 실체의 배열. 여기 담긴 것 전부가 한 구조로 함께 예측된다 |
| `sequences[].protein.id` | 사슬 ID. `"A"`, `"B"` 등. 구조 파일에서 이 이름으로 보인다 |
| `sequences[].protein.sequence` | 아미노산 1문자 서열 |
| `dialect`, `version` | AF3 입력 형식 표시. 그대로 두면 된다 |

`protein` 자리에는 `dna`(`ACGT`), `rna`(`ACGU`), `ligand`(CCD 코드
`{"ligand": {"id": "L", "ccdCodes": ["ATP"]}}` 또는 SMILES) 도 올 수 있다.

### 5-2. 복합체: 항원 파트너 붙이기

`sequences` 배열에 항목을 하나 더 넣으면 그 둘이 함께 예측된다. 아래는 PDB 1MEL 의 실제
조합으로, 낙타 단일도메인 항체(148 aa)와 그 항원 lysozyme(129 aa)이다. 서열은 자리를
줄여 적었으니 **그대로 쓸 수 있는 전체 서열은 `examples/vhh_antigen_complex.json` 을 보라.**

```json
{
  "name": "vhh_antigen_complex",
  "modelSeeds": [1],
  "sequences": [
    { "protein": { "id": "A", "sequence": "DVQLQASGGG...WGQGTQVTVSSGRYPYDVPDYGSGRA" } },
    { "protein": { "id": "B", "sequence": "KVFGRCELAA...TDVQAWIRGCRL" } }
  ],
  "dialect": "alphafold3",
  "version": 1
}
```

복합체에서는 출력에 **ipTM(계면 신뢰도)** 이 함께 나온다. 단량체에서는 나오지 않는다.
복합체 예측은 두 가지를 각오해야 한다. 첫째, **토큰 수가 늘어나 패딩 버킷이 커진다.**
버킷은 토큰 수 이상인 가장 작은 계단이 잡히므로 116 aa VHH 는 128 에 들어가지만 130 aa 는
이미 256 이고(실측 6종: 116/123 aa 는 128, 130/131/135/138 aa 는 256), 300 aa 항원을
붙이면 512 로 올라간다. 버킷 128 대 256 의 실측 차이가 2.25배(4.20초 대 9.44초)다.
둘째, **paired MSA 가 계면 예측에 중요하다.** 축소 DB 의 paired 깊이는 158~225, 전체
DB 는 24,250~27,353 이다. 복합체에는 전체 DB 를 써야 할 것으로 보이는데 이건 추론이고
직접 측정하지 못했다 ([12절](#12-측정-조건과-한계)).

### 5-3. `af3_prepare.py`: FASTA/CSV 에서 JSON 만들기

세부 옵션은 `python3 scripts/af3_prepare.py --help` 에 있다. 여기 적은 것보다
스크립트가 정확하다.

```bash
# FASTA 의 각 레코드마다 JSON 1개
python3 scripts/af3_prepare.py --fasta examples/vhh_panel.fasta -o vhh_001_in

# 실행 전에 --dry-run 을 먼저 하라. 무엇이 몇 개 만들어지는지, 토큰수와 패딩 버킷 분포까지
# 파일을 쓰지 않고 보여준다
python3 scripts/af3_prepare.py --fasta examples/vhh_panel.fasta -o vhh_001_in --dry-run

# 항원 파트너를 모든 타깃에 공통으로 붙이기 (복합체 스크리닝)
python3 scripts/af3_prepare.py --csv examples/vhh_panel.csv -o vhh_cplx_in \
    --partner-fasta examples/antigen.fasta --dry-run

# 시드 3개로 (시간이 3배 든다), 리간드 붙이기
python3 scripts/af3_prepare.py --fasta examples/vhh_panel.fasta -o vhh_in --seeds 1,2,3
python3 scripts/af3_prepare.py --fasta target.fasta -o with_atp_in --ligand-ccd ATP
```

2000건을 잘못된 이름으로 만들어 놓고 나중에 아는 것보다 `--dry-run` 이 훨씬 싸다.
`--dry-run` 이 실제로 출력하는 버킷 분포는 이런 모양이다 (예제 6종).

```
토큰 수          : 최소 116, 중앙값 131, 최대 138
버킷 분포 (패딩 후 실제로 계산되는 크기)
  버킷 128   :     2 건 ( 33.3%)
  버킷 256   :     4 건 ( 66.7%)
```

길이 검증(`--min-len` 10, `--max-len` 3000), 비표준 알파벳(`--allow-ambiguous`),
동일 서열 복제(`--copies`), JSON version(`--json-version`) 옵션도 있다. `--help` 를 보라.

CSV 입력은 `name,sequence` 두 열을 갖는 형태다.

```csv
name,sequence
vhh_7djx_1,QVQLVESGGGLVQAGGSLRLSCAASGRTFSSYAMGWFRQAPGKERECVAAMDWSTSATYYADSVKGRFTISRDNAKNTVYLQMNSLKPEDTAVYYCAADLDYSDYGPFPGDMDYWGKGTQVTVSSHHHHHH
vhh_7a50_1,QVQLQESGGGLVQAGDSLRLSCAASGRTFSTYPMGWFRQAPGKEREFVAASSSRAYYADSVKGRFTISRNNAKNTVYLQMNSLKPEDTAVYYCVADSSPYYRRYDAAQDYDYWGQGTQVTVSSGRYPYDVPDYGSGRA
```

`examples/vhh_panel.csv` 와 `examples/vhh_panel.fasta` 가 같은 6종의 CSV 판과 FASTA
판이다(공개 PDB 유래). 이 6종이
[3-5절](#3-5-데이터베이스-선택) 의 DB 비교에 쓴 타깃과 같으므로
결과를 [results_example/af3_summary.csv](results_example/af3_summary.csv) 와 직접
비교할 수 있다. 리간드를 넣거나 다량체를 만드는 옵션도 있다. `--help` 를 보라.

### 5-4. 생성 결과 확인

```bash
ls vhh_001_in | head -3
ls vhh_001_in | wc -l
python3 -c "import json;print(json.load(open('vhh_001_in/vhh_A01.json'))['name'])"
find vhh_001_in -name '._*' -delete    # macOS 유래 사이드카를 지운다
```

`._` 로 시작하는 파일은 읽는 순간 파이프라인을 죽인다. 이것 때문에 실제로 측정 3시간을
날렸다. 원인과 예방은 [10절](#10-자주-만나는-문제) 첫 항목에 있다.

---

## 6. 배치 실행

### 6-1. 권장 방법: `run_af3_batch_improved.py`

```bash
cd ~/af3_work
python3 scripts/run_af3_batch_improved.py --audit    # 1. 계산 없이 상태만 점검
python3 scripts/run_af3_batch_improved.py --input-dir test_in --output-dir test_out  # 2. 소규모 시험
nohup python3 scripts/run_af3_batch_improved.py --yes > af3.log 2>&1 &              # 3. 전수 실행
tail -f af3.log
```

입력 폴더의 JSON 전부를 컨테이너 1회 기동으로 순회한다. 기본 폴더 이름은 파일 위쪽
`INPUT_DIR_NAME` / `OUTPUT_DIR_NAME` 에서 바꾸거나 `--input-dir` / `--output-dir` 로 준다.
2단계 실행은 경로와 모드를 보여주고 한 번 물어본다.

**`--yes` 를 빼면 백그라운드에서 멈춘다.** 확인 질문을 띄울 수 없기 때문이고 로그에
그렇게 적힌다. 잘못된 폴더에 2000건을 쏟지 않으려는 안전장치다.

| 옵션 | 하는 일 |
|------|---------|
| `--guide` | 경로와 모드 설명만 보고 끝낸다. 아무것도 만들지 않는다 |
| `--audit` | 실행 없이 완료/미완료와 잔여 폴더만 점검. 미완료가 있으면 종료코드 1 |
| `--mode data` / `--mode inference` | MSA/템플릿만 (**GPU 를 할당하지 않아** 추론과 병행 가능) / 준비된 입력으로 추론만 |
| `--per-file` | 파일마다 컨테이너를 따로 띄운다 (느리다. 문제 격리용) |
| `--cleanup` | 격리 결과와 잔여 staging 을 미리 보여준 뒤 정리 |
| `--yes` | 확인 질문에 자동 응답. 백그라운드 실행에 필요 |

이 러너가 다른 점 세 가지. **완료 판정을 폴더 존재가 아니라 최종 산출물로 한다**
(AF3 는 추론 *전에* `<name>_data.json` 을 쓰므로 폴더만 보면 추론 중 끊긴 것을 완료로
오인한다. `_ranking_scores.csv`, `_model.cif`, `_summary_confidences.json` 세 개가
모두 있고 크기가 0보다 커야 완료다). **미완료 결과를 지우지 않고 `.af3_incomplete/` 로
옮긴다**(작업별로 최신 하나만 보존하므로 반복 실패해도 디스크가 차지 않는다).
**같은 출력 폴더에 두 번 실행되지 않는다**(파일 잠금으로 막고 어느 프로세스가 쓰고
있는지 알려준다).

### 6-2. 래퍼: `af3run.sh`

작업 이름 하나로 진단부터 집계까지 묶는다 (`af3_batch.py` 를 호출한다). 두 번째 인자가
모드다: `check`(환경 진단), `dry`(실행 없이 명령만 확인), `screen`(경량 스크리닝
sample 1 / recycle 3, 전수용), `full`(정밀 sample 5 / recycle 10, 상위 후보용),
`msa`, `infer`, `oneshot`(MSA + 추론을 한 프로세스에서), `retry`(실패한 것만),
`bench`(앞 20건으로 건당 시간 측정), `collect`(CSV 집계).

2000건을 처음 돌릴 때의 권장 순서:

```bash
bash scripts/af3run.sh vhh_001 check      # 1. 환경
bash scripts/af3run.sh vhh_001 dry        # 2. 명령 확인
bash scripts/af3run.sh vhh_001 bench      # 3. 20건으로 건당 시간 측정
# 여기서 나온 건당 시간 곱하기 2000 이 실제 예상 시간이다. 감당 가능한지 판단하고
bash scripts/af3run.sh vhh_001 screen     # 4. 전수
bash scripts/af3run.sh vhh_001 collect    # 5. 집계
```

### 6-3. `af3_batch.py` 직접 쓰기

전체 옵션은 `python3 scripts/af3_batch.py --help` 로 확인하라.

```bash
# 무엇을 실행할지 눈으로 확인 (실제로 돌리지 않는다)
python3 scripts/af3_batch.py --name vhh_001 --dry-run

# 컨테이너 1회, MSA + 추론을 한 프로세스가 전수 순회
python3 scripts/af3_batch.py --name vhh_001 --stage oneshot

# 2단계 분리 (기본값이고 권장). MSA 먼저, 그 다음 추론
python3 scripts/af3_batch.py --name vhh_001 --stage both

# 경량 스크리닝 설정으로 전수
python3 scripts/af3_batch.py --name vhh_001 --stage both --diffusion-samples 1 --recycles 3

# 실패한 것만 재시도
python3 scripts/af3_batch.py --name vhh_001 --stage both --retry
```

`--stage` 는 `msa`(MSA 만, 산출물 `*_data.json` 을 `msa_store` 에 보관),
`infer`(보관된 MSA 로 추론만), `both`(기본값), `oneshot`(한 프로세스에서 둘 다) 네 가지다.

경로는 `--name` / `--input-dir` / `--output-dir` / `--db-dir` / `--model-dir` /
`--cache-dir` / `--image` / `--docker`, 계산량은 `--diffusion-samples`(스크리닝 1,
정밀 5) / `--recycles`(3, 10) / `--msa-n-cpu`(기본 `min(코어수/2, 8)`) /
`--msa-workers`(기본 1. **실측상 1이 최적이니 건드리지 말 것**) / `--limit N`,
재실행은 `--retry` / `--no-skip` 로 준다. 전체 목록은 `--help`, 명령 모음은
[docs/commands.md](docs/commands.md).

> ### 경고: 버킷 사다리에서 128을 빼지 마라
>
> AF3 의 기본 패딩 버킷 사다리는 128에서 시작한다 (`run_alphafold.py` 의 `_BUCKETS`
> 기본값, 소스 대조 및 실측 확인). 128을 빠뜨리면 토큰 128 이하인 짧은 VHH 가 갈 곳을
> 잃고 256 버킷으로 밀려 정상상태 추론이 **4.20초에서 9.44초, 2.25배**가 되고 2000건이면
> GPU 단계가 2.3시간에서 5.2시간이 된다.
>
> `af3_batch.py` 는 입력의 토큰 수를 세어 `[128, 256, 384, 512, ...]` 사다리에서 실제로
> 쓰이는 버킷만 골라 넘긴다. 그냥 쓰면 문제없다. `af3_prepare.py --buckets` 로 사다리를
> 직접 지정할 때만 128을 첫 항목으로 넣어라. 결과 CSV 의 `패딩버킷` 열이 256이면 이
> 함정에 빠졌거나 서열이 실제로 128 토큰보다 긴 것이다.

### 6-4. 2단계 전략: MSA 먼저, 추론 나중

MSA(CPU)와 추론(GPU)을 분리하면 MSA 산출물(`*_data.json`)이 `msa_store` 에 보관되므로
**추론 설정을 바꿔 재실행할 때 MSA 를 다시 계산하지 않는다.** 스크리닝(sample 1)으로 전수
돌린 뒤 상위 후보만 정밀(sample 5)로 재실행하는 것이 거의 공짜가 된다.

```bash
# 1단계: MSA 만 전수 (CPU 바운드)
python3 scripts/af3_batch.py --name vhh_001 --stage msa

# 2단계: 추론만, 경량 설정으로 전수 (GPU) 후 집계해 상위 100건 목록을 만든다
python3 scripts/af3_batch.py --name vhh_001 --stage infer --diffusion-samples 1 --recycles 3
python3 scripts/af3_collect.py vhh_001_out --top 100 --top-list top100.txt

# 3단계: 상위 100건만 정밀 재실행. MSA 는 재사용되므로 빠르다
mkdir -p vhh_top_in
while read n; do cp "vhh_001_in/${n}.json" vhh_top_in/ 2>/dev/null; done < top100.txt
python3 scripts/af3_batch.py --name vhh_top --stage infer --diffusion-samples 5 --recycles 10
```

`_data.json` 재사용의 절약폭은 1단계에 쓴 DB 구성이 정한다. 축소 DB 로 1단계를 돌렸으면
건당 4~5초, 전체 DB 급으로 돌렸으면 건당 약 30초다 (실측, VHH 4건). 축소 DB 에서도
재사용을 권하는 이유는 시간이 아니라 1단계와 2단계가 동일한 MSA 를 쓴다는 것이다
(MSA 를 다시 만들면 같은 DB 로도 깊이가 달라졌다.
[docs/two_stage_notes.md](docs/two_stage_notes.md) 3절). 다만 **경량 스크리닝으로 고른
상위 100건이 정밀 계산의 상위 100건과 같다는 보장은 없다.** 순위 보존은 측정하지 않았다
([12절](#12-측정-조건과-한계)).

MSA 설정은 실측 기준으로 이미 최적값이 기본이다. **`--msa-workers` 를 올리지 마라.**
같은 스레드 총량에서 갈래를 늘리면 오히려 느려진다 (32스레드 1갈래 0.890 대
2갈래 0.767 타깃/분). AF3 가 이미 체인당 DB 4개를 내부에서 병렬 검색한다
(`ThreadPoolExecutor(max_workers=4)`).

![MSA 스레드 확장성](figures/msa_threads_scaling.png)

처리율은 총 스레드가 코어 수의 약 1.3배인 지점(24코어 호스트에서 48스레드)에서
**0.895 타깃/분으로 포화**하고 그 이상은 손해다 (24스레드 0.778, 32스레드 0.890,
96스레드 0.848). 측정 조건은 **전체 DB 급(4종 각 4GB 슬라이스) 기준**이다
(`results_example/msa_throughput.csv` 의 `db` 열). 축소 DB 약 2GB 에서는 데이터
파이프라인이 건당 1.98초로 훨씬 짧다.

권장값은 `--jackhmmer_n_cpu = --nhmmer_n_cpu = min(코어수/2, 8)`, 동시 1갈래다.
AF3 기본값이 `min(코어수, 8)` 이므로 **8코어 이상이면 기본값이 이미 최적에 가깝다.**

### 6-5. 진행 확인과 재개

스크립트는 `<이름>_work/` 에 상태를 남긴다.

```bash
cat vhh_001_work/state.json | python3 -m json.tool | head -30   # 진행 상태와 실패 목록
tail -5 vhh_001_work/run_summary.csv                            # 타깃별 건당 시간
tail -f vhh_001_work/*.log                                      # 컨테이너 stdout
ls vhh_001_out | wc -l                                          # 몇 건 끝났나
nvidia-smi --query-gpu=utilization.gpu,memory.used --format=csv -l 5
```

`utilization.gpu` 가 오르내리면 정상이고, `memory.used` 가 15GB 근처인 것도 정상이다
(XLA 선점량, [2절](#2-요구-사양)).

전원, 커널 OOM, ssh 끊김 등으로 멈추면 **같은 명령을 다시 실행하면 된다.** 이미 끝난
타깃은 건너뛴다. 미완성 결과 폴더는 스크립트가 `partial/` 로 옮겨 둔다
(그대로 두려면 `--keep-partial`). 실패한 것만 골라 다시 하려면 `--retry`,
처음부터 다시 계산하려면 `--no-skip` 이다. **2000건에 `--no-skip` 을 쓰면 처음부터
다시 돌린다.**

장시간 실행은 `tmux new -s af3` 안에서 띄워 ssh 가 끊겨도 살아 있게 하라
(`Ctrl+B, D` 로 빠져나오고 `tmux attach -t af3` 로 다시 붙는다). 운영 절차 전체는
[docs/operations_guide.md](docs/operations_guide.md) 에 있다.

---

## 7. 속도 개선의 근거

JSON 하나마다 `docker run` 을 새로 띄우면 타깃마다 컨테이너 기동, JAX/CUDA 초기화,
가중치 로딩(1.15GB, 파라미터 3.68억 개), XLA 커널 컴파일을 처음부터 반복한다.
이 고정 비용이 건당 9.1~9.2초로 측정됐고, 버킷 128 에 들어가는 짧은 VHH 의 실제 추론은
정상상태 4.20초다. **준비하는 시간이 일하는 시간보다 두 배 이상 길다.**

A/B 실측 (32건 곱하기 3반복, MSA 없는 GPU 추론 경로만, 웜 캐시):

| 조건 | 건당 시간 | 최악 대비 |
|------|-----------|-----------|
| 프로세스별 + 캐시 미지정 (기존 방식) | 31.95초 | 1.00배 |
| 프로세스별 + 캐시 지정 | 18.13초 | 1.76배 |
| 단일 프로세스 + 캐시 미지정 | 6.26초 | 5.10배 |
| **단일 프로세스 + 캐시 지정 (권장)** | **5.39초** | **5.93배** |

세 번 반복한 값이 5.39 / 5.39 / 5.41초로 편차 0.1%였다.

![A/B 벤치마크](figures/ab_benchmark.png)

**단일 프로세스화가 주효과다.** 캐시 미지정 조건만 봐도 5.10배다. 캐시 디렉터리는
부효과(1.76배)이고 그 이득은 첫 2건의 컴파일만 없애므로 배치가 커지면 0으로 수렴한다.
**길이순 정렬의 이득은 0.00초/건이다.** XLA 는 버킷별 컴파일 결과를 프로세스 수명 동안
보유하므로 버킷을 왕복해도 손해가 없다.

GPU 단계를 고치면 다음에 무엇이 남는지는 **1단계에 어떤 DB 를 쓰는가**에 달려 있다.
아래 두 구성은 조건이 달라 직접 비교할 수 없다.

| 구성 | 데이터 파이프라인(MSA) 건당 | GPU 추론 건당 | 2000건 합계 | MSA 비중 | 근거 |
|---|---|---|---|---|---|
| 축소 DB 약 2GB (연구자 현재 구성, 권장) | 1.98초 | 5.39초 | **4.1시간** | 27% | 각 항목 직접측정, 합계는 합산 추정 |
| 전체 DB 급 4GB 슬라이스 4종 | 67.0초 (스레드 스윕 포화점) | 5.39초 | **40.2시간 (1.7일)** | 93% | MSA 인용, 추론 직접측정, 합계는 합산 추정 |

**축소 DB 구성에서는 MSA 가 병목이 아니다.** 데이터 파이프라인 1.98초가 GPU 추론
5.39초보다 짧다. **MSA 가 93%를 차지하는 것은 전체 DB 급 구성에서만 성립하고**, 조건을
빼고 "코드를 고치면 MSA 가 93%" 라고 쓰면 틀린 말이 된다.

연구자의 현재 방식은 건당 341초, 2000건 189시간(7.9일)이다. 개선 배수를 정직하게 쓰면
GPU 추론 단계만 **5.93배**(직접 측정), 전체 파이프라인은 **4.7~46배**(189시간에서
4~40시간, DB 구성에 따라 갈린다. 모두 합산 추정)다.

> `341 / 5.39 = 63배` 같은 계산은 성립하지 않는다. 341초에는 MSA 가 포함돼 있고
> 5.39초에는 포함돼 있지 않다. 이 식은 쓰지 마라.

측정 원자료와 조건별 대조는 [docs/benchmark_report.md](docs/benchmark_report.md),
[docs/diagnosis_report.md](docs/diagnosis_report.md),
[docs/msa_correction_notes.md](docs/msa_correction_notes.md) 에 있다.

---

## 8. 결과 해석

숫자가 나왔다고 구조가 맞는 게 아니다. 결과 해석과 [구조 확인](#9-결과-보기)이 실제로 가장 중요하다.

### 8-1. 출력 폴더의 구성

타깃 하나가 폴더 하나가 되고 폴더 이름은 입력 JSON 의 `name` 을 정규화한 값이다.
아래는 검증 호스트의 실제 출력이다 (VHH 단량체, sample 5).

```
vhh_001_out/vhh_4qgy_1/
    vhh_4qgy_1_model.cif                     95,280 B   1위 모델 구조
    vhh_4qgy_1_summary_confidences.json       1,209 B   1위 모델의 요약 지표
    vhh_4qgy_1_confidences.json             161,061 B   원자별 pLDDT, 토큰별 PAE
    vhh_4qgy_1_ranking_scores.csv               147 B   시드 곱하기 샘플 전체의 점수
    vhh_4qgy_1_data.json                    839,761 B   MSA 가 담긴 입력
    TERMS_OF_USE.md                          13,036 B   AF3 가 넣는 약관
    seed-1_sample-0/ ... seed-1_sample-4/               샘플별 동일 3종 파일
```

| 파일 | 무엇이 들어 있나 | 언제 쓰나 |
|------|------------------|-----------|
| `*_model.cif` | 1위 모델의 원자 좌표. mmCIF 의 `B_iso_or_equiv` 열이 원자별 pLDDT (0~100) | 구조를 눈으로 볼 때. 뷰어에 이 파일을 넣는다 ([9절](#9-결과-보기)) |
| `*_summary_confidences.json` | ranking_score, ptm, iptm, fraction_disordered, has_clash, 체인별 지표 | 이 타깃을 통과시킬지 판단할 때 |
| `*_confidences.json` | 원자별 pLDDT 배열, 토큰 쌍별 PAE 행렬, 토큰별 체인 ID | 어느 부위가 못 맞았는지 볼 때 |
| `*_ranking_scores.csv` | `seed,sample,ranking_score` 한 줄씩 (sample 5면 5줄) | 결과가 우연인지, 샘플 간 산포를 볼 때 |
| `*_data.json` | MSA(`unpairedMsa`, `pairedMsa`)와 템플릿이 문자열로 담긴 **재사용 가능한 입력** | MSA 깊이를 볼 때. `--norun_data_pipeline` 재실행에 그대로 쓴다 ([6-4](#6-4-2단계-전략-msa-먼저-추론-나중)) |
| `seed-*_sample-*/` | 샘플 하나의 `_model.cif` 와 신뢰도 2종 | 샘플 간 구조를 직접 비교할 때 |

**타깃 폴더 바로 아래의 파일이 AF3 가 1위로 뽑은 모델이다.** 보통 이것만 보면 된다.

출력 폴더가 이미 있고 비어 있지 않으면 AF3 는 `<폴더명>_<타임스탬프>` 형제 폴더를
새로 만든다. 그때도 **파일 stem 은 원래 타깃명 그대로**이므로 집계 스크립트는 타깃을
정상적으로 인식한다.

**폴더가 있다는 것은 완료를 뜻하지 않는다.** AF3 는 추론 *전에* `_data.json` 을 쓴다.
완료 기준은 `_ranking_scores.csv`, `_model.cif`(또는 `.cif.zst`),
`_summary_confidences.json` 세 개가 모두 있고 크기가 0보다 클 때다.
`run_af3_batch_improved.py` 가 이 기준으로 판정한다.

### 8-2. 지표의 정의

| 지표 | 범위 | 무엇 | 어디 |
|------|------|------|------|
| **pLDDT** | 0~100 | **잔기/원자 단위 국소 정확도** | `*_confidences.json`, `*_model.cif` 의 B-factor |
| **pTM** | 0~1 | **전체 폴드가 맞을 확률의 대리 지표** | `*_summary_confidences.json` |
| **ipTM** | 0~1 | **계면 정확도.** 복합체에서만 산출 | 같음 |
| **PAE** | Å | **토큰 쌍별 위치 오차 기댓값** | `*_confidences.json` |
| **ranking_score** | 해당 없음 | AF3 가 모델을 줄 세울 때 쓰는 종합 점수 | `*_summary_confidences.json` |
| **fraction_disordered**, **has_clash** | 0~1, 0/1 | 무질서 비율, 원자 충돌 발생 | 같음 |

### 8-3. 판정 기준선

`af3_collect.py` 가 CSV 의 `등급` 열에 쓰는 기준이고, AlphaFold 계열의 통상적 해석
구간을 이 배치에 맞춰 적용한 것이다.

| 지표 | 구간 | 해석 |
|------|------|------|
| pLDDT | 90 이상 | 매우 높음. 측쇄 수준까지 신뢰 |
| | 70~90 | 신뢰. 주사슬(백본) 신뢰 |
| | 50~70 | 낮음. 접힘 방향 정도만 |
| | 50 미만 | 매우 낮음. 구조가 없거나 무질서 영역 |
| pTM | 0.5 초과 | 전체 폴드가 대체로 맞다고 볼 수 있는 하한선 |
| ipTM (복합체만) | 0.8 이상 | 계면 신뢰 |
| | 0.6~0.8 | 회색지대. 판단 보류 |
| | 0.6 미만 | 계면 실패 가능성 높음 |

**복합체는 ipTM 을 1차 기준으로 쓴다.** 단량체는 ipTM 이 없으므로 pLDDT 와 pTM 을
함께 본다. `등급` 열은 복합체에서 `A_계면신뢰`(ipTM ≥ 0.8 이고 pLDDT평균 ≥ 80),
`B_계면회색`(ipTM ≥ 0.6), `C_계면실패`(그 외)이고, 단량체에서
`A_높음`(pLDDT평균 ≥ 90 이고 pTM ≥ 0.7), `B_신뢰`(pLDDT평균 ≥ 80 이고 pTM ≥ 0.5),
`C_보통`(pLDDT평균 ≥ 70), `D_낮음`(그 외)이다.

등급과 별개로 `경고` 열이 붙는다. `충돌`(has_clash > 0. 원자가 겹쳤으니 구조를
의심하라), `무질서`(fraction_disordered ≥ 0.1), `MSA얕음`(unpaired 깊이 < 100.
축소 DB 를 쓰면 정상적으로 붙는다), `샘플불안`(ranking 산포 ≥ 0.05. 샘플마다 결과가
흔들려 재현성이 낮다), `버킷256`(패딩 버킷 ≥ 256. 추론이 2.25배 느려진 건이다).

> ### 함정 1. 신뢰도는 정답과의 일치도가 아니다
>
> pLDDT, pTM, ipTM, ranking_score 는 전부 **모델이 자기 예측을 얼마나 확신하는가**이고
> **실제 구조와 얼마나 맞는가가 아니다.** 확신에 차서 틀릴 수 있다. 특히 학습 데이터에
> 유사 구조가 많은 계열(면역글로불린 폴드가 정확히 그렇다)은 프레임워크 부분의 pLDDT 가
> 항상 높게 나오는데 그게 CDR 루프의 배치가 맞다는 뜻은 아니다. **이 값들은 실험 검증
> 대상을 줄이는 순위 지표로만 쓰라.** "pLDDT 92니까 이 구조가 맞다" 는 결론은 이
> 데이터로 낼 수 없다.

> ### 함정 2. ranking_score 를 단독 순위 기준으로 쓰지 마라
>
> AF3 의 정의는 `0.8 x (ipTM 또는 단량체면 pTM) + 0.2 x pTM +
> 0.5 x fraction_disordered - 100 x has_clash` 다. **`fraction_disordered` 를 더하므로**
> 무질서 비율이 높은 건이 pTM 이 더 낮아도 ranking_score 는 더 높게 나올 수 있다.
> 이 점수는 원래 같은 타깃의 여러 샘플 중 대표를 고르기 위한 것이고 **서로 다른 타깃을
> 줄 세우는 용도가 아니다.**
>
> 스크리닝 순위는 **pTM(단량체) 또는 ipTM(복합체) 과 pLDDT평균을 함께** 보라.
> `af3_collect.py` 의 `--top-by` 로 기준 열을 바꿀 수 있다. CSV 의 `ranking검산차` 열은
> 위 식으로 다시 계산한 값과의 차이이고, **0 근처가 아니면 파일 짝이 안 맞는다**
> (다른 실행의 파일이 섞였다).

### 8-4. `af3_collect.py` 로 표 만들기

출력 폴더 전체를 훑어 타깃별 지표를 CSV 한 장으로 모은다. 표준 라이브러리만 쓰므로
pandas 가 없는 서버에서도 돌아간다 (python 3.8 이상). **읽기만 하고** 출력 폴더의 어떤
파일도 수정하거나 삭제하지 않는다.

```bash
python3 scripts/af3_collect.py vhh_001_out -o vhh_001_결과요약.csv    # 기본

# MSA 깊이 계산은 *_data.json 을 읽어야 해서 느리다. 필요 없으면 끈다
python3 scripts/af3_collect.py vhh_001_out --no-msa-depth -o 요약.csv

# 여러 폴더를 한 CSV 로 (조건 비교). 라벨=경로 형식
python3 scripts/af3_collect.py 축소=af3out_reduced 전체=af3out_full -o 비교.csv

# 상위 100건 골라내기 (2단계 전략의 재실행 목록). 기준 열은 --top-by 로 바꾼다
python3 scripts/af3_collect.py vhh_001_out --top 100 --top-by pTM --top-list top100.txt

python3 scripts/af3_collect.py vhh_001_out --grade-doc                # 등급 기준 설명
```

CSV 는 35열이다. `조건, 타깃, 등급, 경고`, 신뢰도(`ranking_score, pTM, ipTM,
pLDDT평균/중앙값/최소/p10/70이상비율/90이상비율, fraction_disordered, has_clash`),
MSA(`MSA_unpaired깊이, MSA_paired깊이`), 규모(`토큰수, 원자수, 체인수, 체인ID,
패딩버킷`), 샘플 산포(`샘플수, ranking최고/최저/산포`), 체인별
(`chain_pTM, chain_ipTM, min_chain_pair_ipTM`), 검산(`ranking검산차`), 그리고 출처
(`출력경로, 폴더명, 실행시각, 실행수, 중복정책`)다. 여러 번 실행해 타임스탬프 폴더가
생겼으면 `--all-runs` 로 전부 집계할 수 있고, 열 이름을 영어로 뽑으려면
`--filename-lang en` 을 준다.

실행하면 화면에 이렇게 요약이 뜬다 (검증 호스트 실물 출력, 축소 DB 6종).

```
축소  ~/af3_db_track/af3out_reduced : 완료 6건, 미완성/건너뜀 0건
집계 완료: 6건 -> out.csv
  A_높음            2건 (33.3%)
  B_신뢰            4건 (66.7%)
  경고: MSA얕음 6건, 버킷256 4건, 무질서 1건
  ranking_score 검산: 전건 일치 (파일 짝이 맞다)
```

**`ranking_score 검산` 줄을 반드시 보라.** 전건 일치가 아니면 다른 실행의 파일이 섞였다.
실제 출력 예시는 [results_example/af3_summary.csv](results_example/af3_summary.csv)
(축소 DB 대 전체 DB 6종 비교, 실측)에 있다.

### 8-5. 검토 순서

1. `등급` 열로 정렬한다. `D_낮음` 은 일단 제외한다.
2. `경고` 열에 `충돌` 이 있는 건은 구조를 직접 열어 확인한다.
3. `경고` 열에 `샘플불안` 이 있는 건은 재현성이 낮으니 시드를 늘려 재실행한다.
4. 남은 것을 pTM(또는 ipTM) 내림차순 + pLDDT평균 으로 정렬한다.
5. 상위 수십 건만 구조를 실제로 눈으로 본다 ([9절](#9-결과-보기)).
6. 그중에서 실험할 것을 고른다.

`MSA얕음` 경고는 축소 DB 를 썼으면 전량에 붙는다. 단량체 스크리닝에서는 정상이다
([3-5](#3-5-데이터베이스-선택) 의 6종 비교).

![신뢰도 분포](figures/confidence_overview.png)

---

## 9. 결과 보기

### 9-1. `af3_visualize.py`: 그림 만들기

세부 옵션은 `python3 scripts/af3_visualize.py --help` 로 확인하라.

```bash
python3 scripts/af3_visualize.py vhh_001_out/vhh_A01 --out-dir figs   # 타깃 하나
python3 scripts/af3_visualize.py vhh_001_out --out-dir figs           # 폴더 전체
```

**pLDDT 프로파일**(잔기별 꺾은선)은 낮게 파인 구간이 못 맞춘 부위다. VHH 라면 CDR3
근처가 낮은 게 흔하다. **PAE 히트맵**(토큰 곱하기 토큰)에서 대각 블록은 도메인 내부이고,
복합체에서 사슬 A 와 B 에 해당하는 대각 밖 블록이 어두우면(오차 작음) 두 사슬의 상대
위치를 확신한다는 뜻이다. 밝으면(오차 큼) 각 사슬은 잘 접혔지만 **어떻게 붙는지는
모른다**는 뜻이고, ipTM 이 낮은 복합체는 대개 이 모양이다.

이 스크립트는 뷰어용 색칠·정렬 명령을 타깃 이름에 맞춰 생성해 주기도 한다
(`examples/viewer_pymol_plddt.pml`, `examples/viewer_chimerax_plddt.cxc` 가 그 예시다).

### 9-2. `af3_view3d.py`: 복사해 붙이는 명령

AF3 출력 폴더를 HTML 로 만든다. 그 파일을 더블클릭하면 브라우저에서 구조가 뜨고
마우스로 돌린다. 파이썬 표준 라이브러리만 쓰므로 설치할 것이 없다.

```bash
# 타깃 하나만 본다
python3 scripts/af3_view3d.py vhh_001_out --only vhh_001 --out-dir 뷰어

# 출력 폴더 전체를 본다 (타깃별 HTML + 목록 index.html)
python3 scripts/af3_view3d.py vhh_001_out --out-dir 뷰어

# 2000건을 돌린 뒤 상위 20건만 자세히 본다
python3 scripts/af3_view3d.py vhh_001_out --out-dir 뷰어 --top 20

# 인터넷이 안 되는 컴퓨터로 옮겨서 볼 것이면 (파일이 커진다)
python3 scripts/af3_view3d.py vhh_001_out --out-dir 뷰어 --lib embed --engine 3dmol
```

만든 뒤 `뷰어/index.html` 을 더블클릭한다.

### 9-3. 만들어지는 파일

| 파일 | 무엇인가 |
| --- | --- |
| `뷰어/index.html` | 타깃 목록. ranking score 내림차순. pTM, ipTM, 평균 pLDDT, 사슬 수, pLDDT 구간 분포 막대가 한 줄에 있다. 타깃 이름을 누르면 구조로 간다 |
| `뷰어/<타깃>.html` | 구조 하나. 왼쪽에 신뢰도 지표와 색 범례, 오른쪽에 3D 화면 |

`--lib cdn` (기본)이면 HTML 한 개가 0.1~0.2MB 다. 열 때 인터넷이 필요하다.
`--lib embed` 면 3D 라이브러리가 파일 안에 들어가서 인터넷 없이 열리고,
한 개가 약 5MB (`--engine 3dmol` 이면 약 0.6MB)가 된다.
인터넷 없이 열어야 하는데 건수가 많으면 `--engine 3dmol --lib embed` 를 써라.

### 9-4. 화면 조작

- **돌리기**: 왼쪽 버튼으로 끌기
- **확대/축소**: 마우스 휠
- **평행 이동**: 오른쪽 버튼으로 끌기 (또는 휠 버튼)
- **pLDDT / 사슬별 버튼**: 색칠을 바꾼다. 즉시 바뀐다. 다시 만들 필요 없다
- **시점 초기화 버튼**: 돌리다 길을 잃었을 때 처음 시점으로 돌아온다

왼쪽에 ranking score, pTM, ipTM, 평균 pLDDT, 최저 pLDDT, 무질서 비율,
원자 충돌 여부, 사슬 수, 잔기/원자 수, 확산 샘플 수와 샘플 간 산포가 나온다.
ipTM 은 단량체에 없는 값이므로 단량체 화면에는 그 줄이 아예 없다 (0 이 아니다).

### 9-5. 색 해석

기본 색칠은 pLDDT 다. 잔기별 예측 신뢰도이고 0~100 이다. 경계와 색은 EBI
AlphaFold DB 와 같으므로 그쪽에서 본 그림과 나란히 비교할 수 있다.

| 색 | pLDDT | 뜻 |
| --- | --- | --- |
| 진한 파랑 | 90 이상 | 원자 위치까지 믿을 만하다 |
| 하늘색 | 70~90 | 뼈대는 맞다. 곁사슬 방향은 덜 확실하다 |
| 노랑 | 50~70 | 대략의 위치만 맞다. 결론의 근거로 쓰지 마라 |
| 주황 | 50 미만 | 위치가 사실상 정해지지 않았다 |

**믿을 만한 VHH 단량체의 모습**: 전체가 파랑/하늘색이고, 주황은 양쪽 끝
(N말단 1~2잔기, C말단 His-tag 등 꼬리)에만 있다. 그 꼬리가 낮은 것은 정상이다.
실제로 붙어 있지 않은 유연한 끝이라서 위치가 정해지지 않는 것이고, 결합에도
관여하지 않는다.

**CDR3 근처가 낮은 것은 흔하다**. VHH 의 CDR3 는 대략 95~115번 잔기 근처의
긴 루프다. 이 구간이 노랑까지 내려가는 것은 자주 보이고, 그 자체로 실패가 아니다.
루프는 원래 여러 모양을 오간다. 화면 왼쪽 아래 "pLDDT 70 미만 연속 구간" 목록에
어느 잔기가 낮은지 번호로 나오니, 그 번호가 CDR3 범위인지 확인해라.

**의심해야 하는 모양**:
- 뼈대(프레임워크, 대략 1~25 / 35~50 / 60~95 / 115~끝)까지 노랑이나 주황이다.
  면역글로불린 접힘 자체가 안 만들어진 것이다. 서열이 잘못 들어갔거나 잘렸는지 봐라
- 화면 왼쪽 목록의 "pLDDT 70 미만 연속 구간" 이 CDR 밖에서 10잔기 이상 이어진다
- 두 개의 베타 시트 샌드위치 모양이 보이지 않고 국수처럼 풀려 있다
- "무질서 비율" 이 0.3 을 넘는다
- "원자 충돌" 이 "있다" 다
- 확산 샘플 간 ranking score 표준편차가 크다 (같은 서열인데 답이 흔들린다는 뜻)

**복합체(VHH + 항원)에서 볼 것**: 사슬별 버튼으로 바꿔 어느 쪽이 VHH 이고 어느 쪽이
항원인지 먼저 확인한다. 그 다음 pLDDT 로 돌아가 **두 사슬이 닿는 면**의 색을 본다.
계면이 파랑/하늘색이면 그 결합 자세를 검토할 가치가 있다. 계면이 노랑/주황이면
붙는 자리를 모형이 못 정한 것이다. 지표로는 ipTM 을 같이 본다. ipTM 이 낮은데
pTM 이 높은 경우는 "각 사슬은 잘 접혔지만 붙이는 데는 실패" 라는 뜻이다.

### 9-6. PyMOL / ChimeraX 로 보기

`*_model.cif` 를 뷰어에 넣는다. AF3 는 mmCIF 의 B-factor 자리(`B_iso_or_equiv`)에
원자별 pLDDT 를 넣으므로, 어느 뷰어에서든 그 값으로 색칠하면 신뢰도 지도가 된다.

**(a) PyMOL.** `pymol vhh_001_out/vhh_A01/vhh_A01_model.cif` 로 열고 명령창에 붙인다.

```python
spectrum b, red_yellow_green_blue, minimum=50, maximum=90
show cartoon
hide lines
bg_color white
select lowconf, b < 70        # 낮은 부위만 골라 본다
show sticks, lowconf
color orange, lowconf
```

빨강이 낮고(50 이하) 파랑이 높다(90 이상). AlphaFold 공식 배색과 방향이 다르니 그림에
범례를 넣어라. AlphaFold 관례(파랑=높음, 주황=낮음)는
`spectrum b, orange_yellow_cyan_blue, minimum=50, maximum=90` 이다.

**(b) ChimeraX.** `chimerax ..._model.cif` 로 열고 명령줄에 붙인다.
`color bfactor palette alphafold` 가 AlphaFold 공식 pLDDT 배색을 그대로 적용하므로
이 색칠은 ChimeraX 가 한 줄로 끝난다. `cartoon`, `set bgColor white`,
`show :/bfactor<70 atoms`(낮은 부위 강조)를 함께 쓴다.

**여러 구조를 겹쳐 보기.** 같은 계열 나노바디에서 CDR 루프만 다른지 확인할 때 PyMOL 은
`load` 후 `align m2, m1`, ChimeraX 는 `open` 후 `matchmaker #2 to #1` 이다.
전체 스크립트는 `examples/viewer_pymol_plddt.pml` 과
`examples/viewer_chimerax_plddt.cxc` 에 있다.

### 9-7. 파일 하나만 급히: 웹 뷰어에 끌어다 놓기

파일 하나만 급히 보고 싶으면 도구를 쓰지 않는 길도 있다.
<https://molstar.org/viewer> 를 열고 `<타깃>_model.cif` 파일을 화면에 끌어다
놓는다. 같은 뷰어이고 pLDDT 색칠도 그쪽 메뉴에서 고를 수 있다
(Color Theme 를 pLDDT Confidence 로).

**주의: 그 방법은 파일을 외부 서비스에 올리는 것이다.** 미공개 서열이거나
특허를 낼 후보라면 쓰지 마라. 이 저장소의 `af3_view3d.py` 로 만든 HTML 은
파일이 컴퓨터 밖으로 나가지 않는다 (`--lib embed` 로 만들면 열 때 네트워크
연결조차 하지 않는다).

### 9-8. 구조가 표시되지 않을 때

- **"구조를 표시하지 못했다" 안내가 나온다**: `--lib cdn` (기본)으로 만든 파일은
  열 때 인터넷에서 3D 라이브러리를 가져온다. 인터넷이 없거나 사내망이 막았으면
  이 화면이 나온다. `--lib embed` 로 다시 만들면 인터넷 없이 열린다
- **"mmCIF 가 .cif.zst 압축이고 풀지 못했다"**: AF3 를
  `--compress_large_output_files` 로 돌린 결과다. `python3 -m pip install zstandard`
  를 하거나 `zstd -d <파일>_model.cif.zst` 로 먼저 풀고 다시 만들어라
- **목록에 타깃이 없다**: 완료 판정은 `_ranking_scores.csv`, `_model.cif`(또는
  `.cif.zst`), `_summary_confidences.json` 세 개가 모두 있고 크기가 0보다 큰
  것이다. 추론이 끝나지 않은 폴더는 빠진다. 그런 폴더도 보려면 `--include-partial`

### 9-9. 눈으로 확인할 것

| 볼 것 | 정상 | 이상 |
|-------|------|------|
| 전체 폴드 | 면역글로불린 β-샌드위치가 보인다 | 풀린 사슬, 뭉친 덩어리 |
| CDR 루프 | 루프 형태가 잡혀 있다 | 곧게 뻗어 나가 있다 (pLDDT 도 낮을 것) |
| 사슬 말단 | 몇 잔기 흔들리는 것은 정상 | 긴 구간이 무질서 |
| 복합체 계면 | 두 사슬이 실제로 접촉해 있다 | 떨어져 있거나 엉뚱한 면끼리 붙어 있다 |
| 원자 충돌 | 없음 | `has_clash > 0` 이면 여기를 확인 |

복합체에서 계면이 이상하면 ipTM 이 낮게 나와 있을 것이다. **숫자와 그림이 서로를
확인해 주는지** 보라. 어긋나면 파일이 섞였을 가능성을 의심하라
([8-3](#8-3-판정-기준선) 의 `ranking검산차`).

---

## 10. 자주 만나는 문제

| 증상 | 원인 | 해결 |
|------|------|------|
| JSON 을 읽다가 UTF-8 디코딩 오류나 JSON 파싱 오류로 죽는다. `ls` 로는 문제가 안 보인다 | macOS 에서 만든 `tar.gz` 를 리눅스에서 풀면 생기는 AppleDouble 사이드카 `._*.json`. `glob('*.json')` 에 잡히고 내용이 UTF-8 이 아니다 | `find <입력폴더> -name '._*' -delete`. 아래 상세 항목 참고 |
| `nvidia-smi` 가 15,157 MiB 사용 중이라고 나온다 | XLA 선점량. 수요가 아니다. 실제 피크는 2,942~2,963 MiB | 무시하라. 실제 사용량을 보려면 `--no-prealloc` (조금 느려진다) |
| 진짜 `CUDA out of memory` | 토큰 수가 크다. 버킷 1024 이상에서 급격히 커진다 | 아래 상세 항목의 5단계 순서 |
| `docker: permission denied ... daemon socket` | 사용자가 `docker` 그룹에 없다 | `sudo usermod -aG docker $USER` 후 재로그인. 또는 `--docker 'sudo docker'` |
| 첫 실행이 5~8분 걸린다 | XLA 커널 컴파일. 콜드/고부하에서 406~497초 관측 | 정상이다. 두 번째부터 웜 6.55~8.5초 ([3-8](#3-8-첫-실행-지연)) |
| `run_alphafold.py: error: unrecognized arguments: --input_dir` | 도커 이미지가 오래된 AF3 다 | 이미지를 다시 빌드한다. 아래 상세 항목 참고 |
| 결과 CSV 의 `패딩버킷` 이 256이다 | 사다리에서 128이 빠졌거나, 서열이 그냥 128 토큰보다 길다 (130 aa 는 정상적으로 256) | 버킷 사다리를 직접 지정하지 마라. 지정해야 하면 128을 첫 항목으로 ([6-3](#6-3-af3_batchpy-직접-쓰기)) |
| `MSA얕음` 경고가 전량에 붙는다 | 축소 DB 를 쓰면 unpaired 깊이가 9~13 이다 | 단량체 스크리닝에서는 정상. **복합체라면 무시하지 말고 전체 DB 를 쓰라** |
| MSA 단계에서 CPU 를 늘렸는데 안 빨라진다 | 처리율이 코어 수의 약 1.3배에서 0.895 타깃/분으로 포화한다 (전체 DB 급 기준) | 정상이다. `--msa-workers` 를 올리면 손해다 ([6-4](#6-4-2단계-전략-msa-먼저-추론-나중)) |
| `jackhmmer -h` 에 `--seq_limit` 이 없다 | AF3 가 패치한 HMMER 가 아니다 | 도커 이미지를 쓰면 보통 문제없다. conda 설치면 [docs/install_log.md](docs/install_log.md) |
| AF3 설치 시 python 3.11 이 거부된다 | AF3 가 `requires-python >=3.12` | `conda create -y -n af3 python=3.12` |
| `/usr/include/zlib.h` 없음, sudo 불가 | 시스템 zlib 개발 헤더가 없다 | `conda install -y -c conda-forge zlib cmake` 후 `export CMAKE_PREFIX_PATH=$CONDA_PREFIX` |
| 중간에 끊겼는데 어디까지 됐는지 모른다 | | `cat <이름>_work/state.json`, `ls <이름>_out \| wc -l`. 같은 명령을 다시 실행하면 끝난 것은 건너뛴다 |
| 결과 파일이 root 소유라 지울 수 없다 | `sudo docker` 로 돌렸다 | `sudo chown -R $USER:$USER vhh_001_out` |

아래 세 항목은 표 한 줄로 줄이면 손해라서 남겼다.

### AppleDouble 사이드카 (`._*.json`)

> **이 문제로 측정 3시간을 날렸다.** 이 저장소에서 가장 비싼 교훈이다.

macOS 에서 만든 `tar.gz` 를 리눅스에서 풀면 `foo.json` 옆에 `._foo.json` 이 생긴다.
`ls` 에서는 눈에 잘 안 띄고, `glob('*.json')` 에는 잡히고, 내용이 macOS 리소스 포크
바이너리라 읽는 순간 죽는다. 이 저장소의 스크립트(`af3_batch.py`, `af3_collect.py`)는
`._` 파일을 건너뛰지만 AF3 본체가 읽으면 죽을 수 있으니 지워라.

```bash
find vhh_001_in -name '._*' | wc -l                     # 확인
find vhh_001_in -name '._*' -delete                     # 해결
COPYFILE_DISABLE=1 tar czf inputs.tar.gz vhh_001_in      # 예방 (macOS 에서 tar 만들 때)
find . -name '._*' -delete && find . -name '.DS_Store' -delete   # 리눅스에서 풀고 나서
```

### 진짜 OOM (CUDA out of memory)

VHH 단량체에서는 보기 어렵고 큰 복합체나 긴 서열에서 발생한다. 순서대로 시도하라.
결과 CSV 의 `토큰수` / `패딩버킷` 을 확인하고(버킷 1024 이상이면 메모리 요구가 급격히
커진다), `--diffusion-samples` 를 5 에서 1로 줄이고, `--no-prealloc` 으로 선점을 끄고
(필요한 만큼만 잡는다), `--unified-memory` 를 준다(**느려지지만 안 죽는다.** GPU 메모리가
넘치면 시스템 RAM 으로 넘기는 최후 수단). 그래도 안 되면 그 타깃은 서열을 잘라 나누거나
포기한다.

```bash
python3 scripts/af3_batch.py --name big --stage infer --no-prealloc --unified-memory
```

### `--input_dir` 를 모른다는 오류 (구버전 이미지)

이 저장소의 최적화는 `--input_dir` 로 한 프로세스가 전수 순회하는 것에 기반하므로 이게
없으면 핵심 이득을 못 얻는다. `git fetch --all` 후 확인된 커밋으로 `git checkout` 하고
이미지를 다시 빌드하라 ([3-2](#3-2-af3-소스와-도커-이미지)). 빌드가 불가능하면 최소한
캐시 디렉터리 지정만이라도 하라. 그것만으로 31.95초에서 18.13초(1.76배)다.
단일 프로세스화(5.10배)는 포기해야 한다.

---

## 11. 라이선스와 인용

라이선스가 세 갈래로 나뉜다. **이 저장소의 스크립트와 문서**는 Apache 2.0
([LICENSE](LICENSE))이라 자유롭게 쓰고 고치고 재배포할 수 있고, **AF3 소스 코드**도
Apache 2.0(별도 저장소)이지만, **AF3 모델 가중치는 비영리 한정이고 재배포가 금지**돼
있다. **세 개는 서로 다르다.** 이 저장소가 Apache 2.0 이라는 것이 AF3 가중치를 자유롭게
쓸 수 있다는 뜻이 아니다.

AF3 가중치 제약은 네 가지다. **비영리 목적으로만** 쓸 수 있고, **재배포가 금지**돼
있으며(클라우드 공유 폴더, 사내 스토리지, 깃 저장소, 어디에도 올리면 안 된다),
**출력물로 유사 구조예측 모델을 학습시키는 것이 금지**돼 있고, 약관은 **구글로부터 직접
받은 경우만** 사용을 허용한다. 동료에게 복사해 받으면 위반이므로 **공식 접근 요청 절차를
밟아 승인 기록을 남겨 두라.** 나중에 논문 심사나 기관 감사에서 근거가 된다.

정확한 조건은 Google DeepMind 가 배포하는 약관 원문을 직접 확인하라. 위 요약은 편의를
위한 것이고 법적 효력은 원문에 있다. 더 자세한 정리는
[docs/license_notes.md](docs/license_notes.md).

AF3 를 써서 결과를 발표하면 다음을 인용해야 한다. 이 저장소는 인용할 필요가 없고,
필요하면 URL 을 각주로 넣으면 된다.

> Abramson, J., Adler, J., Dunger, J. et al. Accurate structure prediction of
> biomolecular interactions with AlphaFold 3. *Nature* **630**, 493–500 (2024).

> ### 경고. 이 저장소는 공개다
>
> 커밋하면 안 되는 것: 가중치 `af3.bin` / `af3.bin.zst`(**재배포 금지. 약관 위반이다**),
> `ccd.pickle` 543MB(용량. `build_data` 로 각자 만든다), `public_databases/` 내용
> (최대 850GB), `*_out/` 결과(용량, 그리고 서열이 역추적될 수 있다), 그리고 **실제 연구
> 서열 (FASTA/CSV/JSON)**(미공개 연구 데이터이고 지우기도 어렵다).
>
>
> `.gitignore` 가 위를 모두 막도록 만들어져 있지만 **이미 추적 중인 파일은 막지 못한다.**
> 커밋 전에 `git status`, `git diff --cached --stat`, `du -sh .git` 로 확인하라
> (`.git` 이 갑자기 커졌으면 뭔가 들어간 것이다). push 전이면 `git reset HEAD~1` 로
> 되돌릴 수 있지만 **이미 push 했다면 히스토리를 다시 써야 한다.** 지우는 커밋을 올려도
> 히스토리에 파일이 남는다. 그 경우 저장소를 비공개로 바꾸고 조치를 상의하라.
>
> `examples/` 의 서열은 공개 PDB 유래의 예시이며 실제 연구 서열이 아니다.

---

## 12. 측정 조건과 한계

측정 호스트는 gpu-5070ti 다. **RTX 5070 Ti 16GB**(Blackwell sm_120), 24 코어, RAM
126GB, AF3 commit `97d20234c6eb89e8d05376e9eecc9321e60a559b`, 그리고 설치 방식은
**conda 네이티브였다. 이 호스트에 Docker 가 없었다.**

**연구자 환경과 다른 점.** 연구자는 RTX 5090 32GB 에 Docker 로 돌린다. GPU 가 더 크지만
실제 VRAM 피크가 3GB 수준이므로 속도 차이는 연산 성능 차이만큼이다. 우리는 Docker 기동
비용이 빠져 있으므로 **우리 값은 Docker 환경의 하한**이고, 개선 배수는 보고한 것보다
크게 나올 가능성이 있다 (작아지지는 않는다).

각 수치의 측정 조건:

| 수치 | 조건 |
|------|------|
| 31.95 / 18.13 / 6.26 / 5.39초/건 | 32건 곱하기 3반복 중앙값. **MSA 없는 GPU 추론 경로만.** 웜 캐시 |
| 4.20초 (정상상태), 9.44초 | 96건 단일 프로세스 순회. 버킷 128 / 같은 조건 버킷만 256 |
| VRAM 2,942~2,963 MiB | 선점 OFF, 23런. VHH 116~144 aa, sample 5 곱하기 recycle 10 |
| 데이터 파이프라인 1.98초 대 30.41초 | 같은 VHH 4건으로 축소 DB 2GB 와 전체 DB 급 4GB 슬라이스 4종 대조. 직접측정 |
| MSA 0.895 타깃/분 (건당 67.0초) | 14조합 스레드 스윕. **전체 DB 급 4종 각 4GB 슬라이스.** 인용 |
| 축소 대 전체 DB 43.3초 대 1,830초 | 6건 곱하기 1회 end-to-end (MSA + 추론 sample 5 / recycle 10) |
| DB 다운로드 1시간 37분 | 4병렬, 평균 약 41MB/s. **회선 속도에 전적으로 의존한다** |
| 신뢰도 비교 6종 | 전부 **단량체** VHH. PDB 유래 (7djx, 7a50, 8v8k, 4qgy, 4s11, 7mfv) |
| 연구자 현재 341초/건 | **연구자 보고값** (3일에 760건). 우리 측정이 아니다 |

### 측정하지 않은 것

1. **샘플링 순위 보존.** `--diffusion-samples 1` 로 스크리닝한 순위가 `5` 순위와 얼마나
   일치하는지 측정하지 않았다. **경량 스크리닝으로 고른 상위 100건이 정밀 계산의 상위
   100건과 같다는 보장이 없다.** 2단계 전략의 가장 큰 미검증 가정이다.
2. **CDR3 등 가변 루프의 잔기별 민감도.** 비교한 것은 원자 pLDDT 의 평균이고 그 값은
   잔기 수가 많은 프레임워크가 지배한다. CDR 루프만 떼어 보면 축소 DB 와 전체 DB 의 차이가
   더 클 수 있다. 분해하지 않았다.
3. **복합체 계면에 대한 DB 크기 영향 (ipTM).** 비교 6종이 모두 단량체여서 ipTM 이
   산출되지 않았다. paired MSA 가 120~150배 차이나므로 복합체에는 전체 DB 가 사실상
   필수로 보이지만 **이것은 추론이다.**
4. **데이터 파이프라인 30.41초와 MSA 스윕 67.0초의 불일치.** 둘 다 전체 DB 급 4GB
   슬라이스 4종 조건인데 2배 넘게 차이난다. 30.41초는 VHH 4건 직접측정(첫 건 91.70초 포함
   평균, 2~4번째는 9.09~9.60초), 67.0초는 14조합 스윕의 포화점 인용값이다.
   **이 불일치는 해소되지 않았다** ([docs/msa_correction_notes.md](docs/msa_correction_notes.md)).
5. 그 밖에: **MSA 처리율 포화의 원인**(CPU 경합인지 디스크 I/O 인지 구분하지 못했다),
   **Docker 오버헤드의 실제 크기**(검증 호스트에 Docker 가 없었고 이미지 빌드 시간
   20~40분도 추정값이다. 그래서 우리 값은 Docker 환경의 하한이다), **긴 서열과 큰
   복합체**(VHH 116~144 aa, 버킷 128과 256 범위만 측정했다), **축소 DB 의 정체**(연구자의
   실제 파일이 아니라 공식 전체 DB 에서 균등 추출한 대리 세트이므로 MSA 깊이 절대값은
   다를 수 있다), **RAM 하한과 build_data 소요 시간**(126GB 호스트에서만 측정했고
   ccd.pickle 생성 시간은 기록하지 않았다).

**우리 수치를 그대로 믿지 말고 `bash scripts/af3run.sh vhh_001 bench` 로 20건을 직접
재라.** 건당 시간에 2000 을 곱하면 된다. 그 값이 우리 표(GPU 추론 5.39초 + 데이터
파이프라인 축소 DB 1.98초 / 전체 DB 급 4GB 슬라이스 30.41초)와 크게 다르면
**당신 환경의 값이 맞다.**

---

## 문서 목록

README 는 따라 하는 문서이고 `docs/` 는 근거 문서다. 수치의 원자료와 측정 절차는 여기 있다.

| 문서 | 내용 |
|------|------|
| [docs/operations_guide.md](docs/operations_guide.md) | 운영 가이드: 설정, 실행, 모니터링, 트러블슈팅 전체 |
| [docs/commands.md](docs/commands.md) | 복사해 붙이는 단일 명령 모음 |
| [docs/diagnosis_report.md](docs/diagnosis_report.md), [docs/diagnosis_notes.md](docs/diagnosis_notes.md) | 진단: 건당 341초가 어느 단계에 얼마나 갔는지, 원자료 |
| [docs/benchmark_report.md](docs/benchmark_report.md), [docs/benchmark_notes.md](docs/benchmark_notes.md) | A/B 벤치마크: 현재 방식 대 최적화 방식, 2000건 환산, 원자료 |
| [docs/two_stage_notes.md](docs/two_stage_notes.md) | 2단계 분리와 `_data.json` 재사용 실측 |
| [docs/msa_correction_notes.md](docs/msa_correction_notes.md) | MSA 시간 주장의 측정 조건 정정 기록 |
| [docs/db_notes.md](docs/db_notes.md), [docs/reduced_db.md](docs/reduced_db.md) | DB 다운로드와 해제, 무결성 검증, MSA 깊이 원자료, 축소 DB 제작 |
| [docs/install_log.md](docs/install_log.md), [docs/dependencies_notes.md](docs/dependencies_notes.md) | 설치 실측 기록과 의존성 조사 |
| [docs/new_scripts_notes.md](docs/new_scripts_notes.md), [docs/merge_notes.md](docs/merge_notes.md), [docs/naming_fix_notes.md](docs/naming_fix_notes.md) | 스크립트 설계, 병합, 이름 규칙과 완료 판정 수정 |
| [docs/testing_notes.md](docs/testing_notes.md) | 회귀 테스트: 각 테스트가 막는 버그, docker 스텁 근거, 역검증 |
| [docs/license_notes.md](docs/license_notes.md) | 라이선스 정리 |
| [docs/readme_rewrite_notes.md](docs/readme_rewrite_notes.md) | 이 README 재작성에서 무엇을 어디로 옮겼는지 |

## 예시 데이터와 회귀 테스트

[examples/](examples/) 에 예제 FASTA/CSV, 입력 JSON(단량체와 복합체), 뷰어 색칠
스크립트가 있고, [results_example/](results_example/) 에 실측 결과 CSV(신뢰도 요약,
A/B 벤치마크, 2000건 환산, MSA 스윕), [figures/](figures/) 에 README 의 그림이 있다.
스크립트를 수정했다면 `python3 tests/run_tests.py` 한 줄로 옛 버그가 되살아나지
않았는지 확인한다(Docker 도, `pip install` 도 필요 없다). 각 테스트에는 그 테스트가 막는
실제 버그가 한 줄로 붙어 있으므로(`python3 tests/run_tests.py --list`) 실패하면 그
문장을 먼저 읽어라.

---

문서와 스크립트에 대한 문의와 오류 보고는 이 저장소의 Issues 로.
AF3 본체 문제는 https://github.com/google-deepmind/alphafold3 로.
