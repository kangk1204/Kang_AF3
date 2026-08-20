# Kang_AF3: AlphaFold 3 대량 스크리닝 도구 모음

VHH/나노바디처럼 **짧은 단백질 수천 건을 AlphaFold 3로 한 번에 돌리기** 위한 스크립트와
한국어 문서 모음이다. 실험 위주로 일하다가 처음 구조예측을 돌리는 사람이
이 README를 위에서 아래로 따라가면 설치부터 결과 해석까지 끝나도록 썼다.

이 저장소의 모든 성능 수치는 실제로 측정한 값이다. 측정하지 않은 것은
`(미측정)` 또는 `(추정)` 으로 표시했다. 측정 조건은 [12절](#12-측정-조건과-한계)에 있다.

한 줄 요약: **JSON 하나마다 `docker run` 을 새로 띄우는 방식을 컨테이너 1회 기동으로
바꾸면 GPU 추론 단계가 건당 31.95초에서 5.39초로 5.93배 빨라진다** (32건×3반복 중앙값).
그리고 그렇게 고치면 병목은 GPU가 아니라 MSA로 옮겨간다.

---

## 목차

| 절 | 내용 |
|----|------|
| [1](#1-이게-무엇인가--무엇이-아닌가) | 이게 무엇인가 / 무엇이 아닌가 |
| [2](#2-요구-사양) | 요구 사양 |
| [3](#3-설치-단계별-예상-시간) | 설치: 단계별 예상 시간 |
| [4](#4-동작-확인) | 동작 확인 |
| [5](#5-입력-파일-준비) | 입력 파일 준비 |
| [6](#6-배치-실행) | 배치 실행 |
| [7](#7-왜-빠른가-선택-읽기) | 왜 빠른가 (선택 읽기) |
| [8](#8-결과-해석) | 결과 해석 |
| [9](#9-결과-보기-시각화) | 결과 보기 (시각화) |
| [10](#10-자주-만나는-문제) | 자주 만나는 문제 |
| [11](#11-라이선스와-인용) | 라이선스와 인용 |
| [12](#12-측정-조건과-한계) | 측정 조건과 한계 |

---

## 1. 이게 무엇인가 / 무엇이 아닌가

### 이것은 무엇인가

AlphaFold 3를 **대량으로 돌리기 위한 껍데기**다. 구체적으로 다음 7개를 제공한다.

| 스크립트 | 하는 일 |
|----------|---------|
| `scripts/run_af3_batch_improved.py` | **권장 배치 러너.** 완료 판정을 최종 산출물로 하고, 미완료 결과를 격리 보존하며, 중복 실행을 차단한다 |
| `scripts/af3_check.sh` | 환경 진단. GPU, 드라이버, 가중치, DB, 도커가 제자리에 있는지 |
| `scripts/af3_prepare.py` | FASTA/CSV → AF3 입력 JSON 생성 |
| `scripts/af3_batch.py` | 최적화 배치 러너. 컨테이너 1회 기동, MSA/추론 2단계 분리, 재시작·재시도 |
| `scripts/af3run.sh` | 위 러너의 초보자용 래퍼. 작업 이름 하나만 주면 된다 |
| `scripts/af3_collect.py` | 출력 폴더 전체를 훑어 신뢰도 지표 CSV 한 장으로 |
| `scripts/af3_visualize.py` | pLDDT 플롯, PAE 히트맵, PyMOL/ChimeraX 색칠 명령 생성 |

### 이것은 무엇이 아닌가

**AlphaFold 3 자체가 아니다.** AF3 코드도, 가중치도, 데이터베이스도 이 저장소에 없다.
세 가지를 각각 따로 받아야 한다.

| 필요한 것 | 어디서 | 이 저장소에 있나 |
|-----------|--------|------------------|
| AF3 소스 코드 | https://github.com/google-deepmind/alphafold3 (Apache 2.0) | 없음 |
| AF3 모델 가중치 (`af3.bin`, 약 1.15GB) | Google DeepMind에 **직접 접근 요청**해서 받아야 한다 | 없음. 재배포 금지 |
| 서열 데이터베이스 | 공개 URL (전체 850GB) 또는 축소 세트 | 없음 |

가중치는 **비영리 목적으로만 쓸 수 있고 재배포가 금지**돼 있다. 자세한 것은
[11절](#11-라이선스와-인용)과 [docs/license_notes.md](docs/license_notes.md).

**이 저장소는 공개(public)다. 가중치 파일, `ccd.pickle`, DB 파일, 그리고 실제 연구 서열을
절대 커밋하지 마라.** `.gitignore` 가 1차 방어선이지만 최종 책임은 커밋하는 사람에게 있다.

### 이 도구가 필요한 상황인지 판단하는 기준

- 타깃이 10건 이하 → 필요 없다. AF3 공식 명령을 그대로 쓰면 된다.
- 타깃이 100건 이상, 특히 서열 길이가 짧고 비슷하다(항체 라이브러리, 나노바디 패널,
  점돌연변이 시리즈) → 이 저장소가 정확히 그 경우를 위한 것이다.
  건당 고정 오버헤드가 전체 시간을 지배하기 때문이다.

---

## 2. 요구 사양

### GPU

| 항목 | 값 | 근거 |
|------|-----|------|
| 실제 VRAM 피크 (VHH 116~144 aa, sample 5 × recycle 10) | **2,942~2,963 MiB** | gpu-5070ti 실측 23런 (A/B 18런 2,942~2,954 + 정렬효과 5런 2,953~2,963) |
| 보수적으로 잡을 값 | 3~5.3GB | 위 실측에 여유 |
| 따라서 필요한 GPU | **16GB면 충분하고도 남는다** | 위와 같음 |
| 확인된 아키텍처 | Blackwell sm_120 (RTX 5070 Ti / 5090 계열) | 실측 |

![gpu-5070ti 추론 실측: 컴파일 상환, 캐시 효과, VRAM 선점 대 실제 요구량](figures/baseline_gpu5070ti.png)

위 그림 (c) 가 이 절의 요점이다. 원 측정값을 MiB로 적으면 이렇다 (카드 총량 16,303 MiB).

| 조건 | 피크 | 비고 |
|------|------|------|
| 선점 ON (AF3 공식 기본 설정) | 15,157 MiB | **예약량이다. 수요가 아니다** |
| 선점 OFF, 스모크 1건 | 5,291 MiB | 프로세스 전체가 잡은 실제 최대 |
| 선점 OFF, 배치 23런 (sample 5 × recycle 10) | 2,942~2,963 MiB | 순회 중 순수 피크 |

5,291 MiB와 2,942~2,963 MiB의 차이는 계측 시점과 조건 차이다(스모크는 단발 실행,
배치는 순회 정상상태). 어느 값을 쓰든 16GB 카드에 여유롭게 들어가므로,
위 표에는 3~5.3GB 로 폭을 두고 적었다.

> 그림 (c) 의 막대 라벨은 MiB를 1024로 나눠 GB로 적었고(5,291 → 5.2), 점선의 '카드 총량
> 16.3 GB' 는 1000으로 나눈 값이다(16,303 → 16.3). 같은 그림 안에서 환산 기준이 섞여 있다.
> 판단에는 영향이 없지만(15GB 예약 대 5GB 요구라는 결론은 동일), 정확한 값이 필요하면
> 위 MiB 표를 쓰라.

`nvidia-smi` 가 15,157 MiB 를 쓰고 있다고 보여줄 것이다. **이건 수요가 아니라 XLA의
선점량(미리 예약한 메모리)이다.** 이 숫자를 보고 "VRAM이 부족하다"고 판단하면 틀린다.
자세히는 [10절](#10-자주-만나는-문제).

### 디스크

| 무엇 | 크기 | 비고 |
|------|------|------|
| AF3 코드 + 도커 이미지 | 수십 GB (추정) | 이미지 빌드 시 중간 레이어 포함 |
| 모델 가중치 `af3.bin` | 1,146,811,260 B (약 1.15GB) | 실측 |
| `ccd.pickle` | 542,994,372 B (약 543MB) | 실측. `build_data` 가 생성 |
| **축소 DB** | 약 2GB | 단량체 스크리닝에 충분 ([3절](#3-설치-단계별-예상-시간) 참조) |
| **전체 DB** | 압축 238.8GB → **해제 후 850GB 점유** | 실측 |
| 결과물 | 건당 수 MB~수십 MB. 2000건이면 수십 GB (추정) | `--diffusion-samples` 에 비례 |

전체 DB를 받을 계획이면 **여유 1TB 이상**을 준비하는 것이 안전하다.

### CPU와 RAM

- CPU 코어 수가 MSA 단계 속도를 직접 결정한다. 8코어 이상 권장.
- 처리율은 총 요구 스레드가 코어 수의 약 1.3배인 지점에서 **0.895 타깃/분으로 포화**한다
  (**전체 DB 급 4GB 슬라이스 4종 기준**. 축소 DB 약 2GB 는 건당 1.98초로 훨씬 빠르다).
- RAM: 검증 호스트는 126GB였다. 축소 DB만 쓰면 훨씬 적어도 된다 (하한 미측정).

### 동작이 확인된 버전 조합

아래 조합에서 처음부터 끝까지 돌아간 것을 확인했다. 다른 조합도 되겠지만
막히면 이 표로 돌아오라.

| 구성 요소 | 버전 |
|-----------|------|
| AF3 | commit `97d20234c6eb89e8d05376e9eecc9321e60a559b` (tag `v3.0.4-15-g97d2023`) |
| Python | 3.12.13 (AF3가 `requires-python >=3.12`) |
| JAX / jaxlib | 0.10.2 (`jax[cuda12]==0.10.2`) |
| jax-cuda12-plugin / pjrt | 0.10.2 |
| CUDA (JAX 번들) | 12.9: cublas 12.9.2.10, cudnn 9.24.0.43, nvcc 12.9.86, runtime 12.9.79, nccl 2.31.2 |
| numpy | 2.5.2 |
| rdkit | 2025.9.4 |
| dm-haiku | 0.0.17 |
| tokamax | 0.0.12 |
| HMMER | 3.4 + AF3의 `--seq_limit` 패치 |

부수적으로 확인된 것:

- `flash_attention` 은 triton 기본값으로 sm_120에서 그대로 동작한다.
- **시스템 nvcc 를 따로 깔 필요가 없다.** JAX가 번들로 가져온다.
- PTX 관련 오류는 나오지 않았다.

---

## 3. 설치: 단계별 예상 시간

설치 경로는 두 갈래이고, 갈리는 지점은 데이터베이스다.

| 경로 | 실작업 시간 | 디스크 | 어떤 경우에 |
|------|-------------|--------|-------------|
| **A. 축소 DB** | 약 **40분~1시간 30분** | 약 5GB | 단량체 전수 스크리닝. **대부분의 경우 이쪽** |
| **B. 전체 DB** | 약 **4~5시간** | 약 **855GB** | 복합체(항원 결합) 예측, 소수 정밀 재계산 |

두 경로 모두 **가중치 접근 승인 대기 시간은 별도**다 (Google 측 처리, 수일 걸릴 수 있음. 추정치다).
승인 절차는 3-2를 보라.

주 경로는 **Docker** 로 쓴다 (연구자 환경이 Docker이므로). conda 네이티브는 3-7에.

### 3-0. 단계 요약표

| 단계 | 예상 시간 | 근거 |
|------|-----------|------|
| ① AF3 저장소 클론 | 1~2분 | 추정 |
| ② 도커 이미지 빌드 | 20~40분 | **미측정** (검증 호스트에 Docker 없음). AF3 공식 Dockerfile 기준 추정 |
| ③ 가중치 요청 | 요청 자체는 10분, **승인 대기 수일** | 절차 |
| ④ 가중치 다운로드 (1.15GB) | 1~5분 | 회선에 따라 |
| ⑤ `build_data` (ccd.pickle 543MB 생성) | 수 분 (미측정) | 설치 시 1회만 |
| ⑥-A 축소 DB 준비 (약 2GB) | 10~30분 | 추정 |
| ⑥-B 전체 DB 다운로드+해제 | **3시간 13분** | 실측 |
| ⑦ 첫 실행 (컴파일) | **최대 406~497초** (콜드/고부하), 이후 웜 6.55~8.5초 | 실측 |

### 3-1. AF3 저장소 클론과 도커 이미지 빌드: ①②

```bash
# 작업 폴더를 하나 정한다. 여기서는 ~/af3_work 로 쓴다
mkdir -p ~/af3_work && cd ~/af3_work

# ① AF3 소스 (1~2분)
git clone https://github.com/google-deepmind/alphafold3.git
cd alphafold3

# 우리가 확인한 커밋으로 맞추고 싶다면 (권장)
git checkout 97d20234c6eb89e8d05376e9eecc9321e60a559b

# ② 도커 이미지 빌드 (20~40분, 미측정 추정)
sudo docker build -t alphafold3 -f docker/Dockerfile .
```

이미지 이름을 `alphafold3` 로 두면 이 저장소의 스크립트가 기본값으로 찾는다.
다른 이름을 쓰려면 `--image` 또는 환경변수 `AF3_IMAGE` 로 알려주면 된다.

빌드 중 화면이 몇 분씩 멈춘 것처럼 보이는 구간이 있다 (HMMER 컴파일 등). 정상이다.

빌드가 끝났으면 이미지가 보이는지 확인한다.

```bash
sudo docker image ls | grep alphafold3
```

```
alphafold3   latest   <이미지ID>   <n> minutes ago   <크기>
```

### 3-2. 모델 가중치 확보: ③④

**이 단계는 우리가 대신 해줄 수 없다.** 가중치는 Google DeepMind에서 직접 받아야 한다.

1. AF3 공식 저장소의 가중치 요청 안내를 따라 접근 요청 양식을 제출한다.
   소속, 용도(비영리 연구), 이름을 정확히 쓴다.
2. 승인 메일을 기다린다. 수일 걸릴 수 있는데, 이건 추정치다.
3. 승인 후 안내받은 방법으로 `af3.bin` (또는 `af3.bin.zst`) 를 받는다.

**약관은 "구글로부터 직접 받은 경우"만 사용을 허용한다.** 남에게 복사해 받으면
약관 위반이다. 공식 절차로 승인 기록을 남겨 두는 것을 강하게 권한다.

받은 파일은 `~/af3_models` 에 둔다.

```bash
mkdir -p ~/af3_models
mv ~/Downloads/af3.bin ~/af3_models/

# .zst 로 받았으면 풀어야 한다
# zstd -d ~/af3_models/af3.bin.zst
```

받은 파일이 맞는지 검증한다. 우리가 확인한 값은 다음과 같다.

```bash
ls -l ~/af3_models/af3.bin
sha256sum ~/af3_models/af3.bin
```

```
크기   : 1146811260  (1,146,811,260 B, 약 1.15GB)
sha256 : df8bbf2621f17dd3ee21c2a921e84a50bc2b80cdc0c7971cb915c2826fee1f9b
```

(참고: `af3.bin.zst` 는 1,020,545,840 B. 가중치 안에는 파라미터가 368,384,602개 있다.)

크기나 해시가 다르면 버전이 다르거나 다운로드가 잘린 것이다. 다시 받아라.
해시가 달라도 최신 버전이라 그럴 수 있으니, 다르면 **크기가 절반 이하인지**를 먼저 보라. 절반 이하라면 확실히 잘린 것이다.

### 3-3. `build_data` (단계 ⑤): 화학 성분 사전 굽기

AF3는 화학 성분 사전을 pickle로 미리 구워 둔다. **설치 시 1회만** 하면 된다.
도커 이미지 빌드 과정에 포함돼 있으면 건너뛰어도 된다.

```bash
# 도커 이미지 안에서 확인 (이미 있으면 아무것도 안 한다)
sudo docker run --rm alphafold3 build_data
```

생성물 (실측):

```
ccd.pickle                     542,994,372 B   (화학 성분 50,942종)
chemical_component_sets.pickle       8,424 B
```

수 분 걸린다 (미측정). **이 파일도 저장소에 커밋하면 안 된다** (543MB).

### 3-4. 데이터베이스 선택 (단계 ⑥): 여기가 가장 중요한 판단이다

두 선택지의 실측 차이는 다음과 같다. 이 표가 이 저장소에서 가장 값비싼 데이터다.

| 항목 | 축소 DB (약 2GB) | 전체 DB (850GB) | 배수 |
|------|------------------|-----------------|------|
| MSA unpaired 깊이 | 9~13 | 10,640~10,745 | **818~1,186배** |
| MSA paired 깊이 | 158~225 | 24,250~27,353 | **120~150배** |
| 건당 시간 (end-to-end) | 43.3초 | 1,830초 | **42.2배** |
| 2000건 환산 | 24시간 | **1,017시간 (42일)** | 해당 없음 |

MSA 깊이가 1000배 차이난다. 그런데 **VHH 단량체의 신뢰도는 거의 변하지 않았다.**
6종을 양쪽 조건으로 모두 돌린 실측 결과:

| 타깃 | 축소 ranking | 전체 ranking | Δ | 축소 pLDDT평균 | 전체 pLDDT평균 | Δ |
|------|--------------|--------------|-----|----------------|----------------|-----|
| vhh_7djx_1 | 0.82 | 0.82 | 0.00 | 82.56 | 82.76 | +0.19 |
| vhh_7a50_1 | 0.85 | 0.88 | +0.03 | 84.06 | 86.18 | +2.12 |
| vhh_8v8k_1 | 0.85 | 0.88 | +0.03 | 88.18 | 89.92 | +1.74 |
| vhh_4qgy_1 | 0.87 | 0.86 | **-0.01** | 87.99 | 87.00 | -0.99 |
| vhh_4s11_1 | 0.88 | 0.88 | 0.00 | 90.03 | 90.43 | +0.40 |
| vhh_7mfv_1 | 0.90 | 0.90 | 0.00 | 92.29 | 92.70 | +0.41 |

ranking score 무변화 3건, +0.03 2건, -0.01 1건. 그 -0.01은 같은 조건에서 샘플 5개를
돌렸을 때의 산포(0.002~0.008)를 살짝 넘지만 판정을 바꿀 크기가 아니다.

![축소 DB 대 전체 DB 신뢰도 비교](figures/db_confidence_comparison.png)

MSA 깊이와 검색 시간 자체는 이만큼 차이난다 (uniref90 **단일 DB** 검색만 따로 잰 것, 6종 실측).

![축소 DB 대 전체 DB의 MSA 깊이와 검색 시간]({{artifact:art_ea9ff68a-b751-49cc-81be-ab7eaf3dac76}})

> 위 그림의 깊이(축소 705~811, 전체 23,693~25,503)와 앞 표의 깊이(축소 9~13,
> 전체 10,640~10,745)는 **다른 것을 센 값이다.** 그림은 uniref90 하나만 검색해 나온
> 정렬 서열 수이고, 표는 AF3가 DB 4종 결과를 합치고 중복을 제거한 뒤 최종 입력에 담은
> unpaired 깊이다. 같은 타깃끼리 짝지으면 이렇다 (전량 실측).
>
> | 타깃 | 그림: 축소 → 전체 | 표: 축소 → 전체 |
> |------|-------------------|------------------|
> | vhh_7mfv_1 | 724 → 23,947 | 13 → 10,640 |
> | vhh_8v8k_1 | 811 → 25,503 | 12 → 10,745 |
> | vhh_4s11_1 | 721 → 23,742 | 13 → 10,666 |
> | vhh_7djx_1 | 730 → 23,885 | 10 → 10,691 |
> | vhh_4qgy_1 | 705 → 23,693 |  9 → 10,671 |
> | vhh_7a50_1 | 759 → 24,124 |  9 → 10,677 |
>
> 축소 DB 열이 세 자리에서 한 자리로 줄어드는 것(예: 4qgy 705 → 9)은 추출된 서열이
> 서로 거의 같아 중복 제거에 걸리기 때문이다. 배수가 다른 이유도 이것이다.
> 그림 기준으로는 약 31~34배, 표 기준으로는 818~1,186배가 된다.
> **한 열의 값을 다른 열과 섞어 인용하지 말 것.**

**왜 이렇게 무덤덤한가.** 나노바디는 면역글로불린 폴드가 잘 보존돼 있고 PDB에 템플릿이
아주 많다. 그리고 **템플릿 검색은 양쪽 조건 모두 전체 PDB를 쓴다.** 즉 축소 DB로 잃는 것은
공진화 신호이고, VHH 프레임워크는 그 신호 없이도 템플릿만으로 잘 잡힌다.

#### 그래서 어느 쪽을 골라야 하나

```
단량체 나노바디/VHH를 수백~수천 건 전수 스크리닝한다
    -> 축소 DB. 전체 DB로 2000건은 42일이라 애초에 선택지가 아니다.

항원-나노바디 복합체의 결합을 보고 싶다 (ipTM이 필요하다)
    -> 전체 DB. paired MSA가 120~150배 차이나므로 계면 예측에는 사실상 필수로 보인다.
       단, 이건 추론이다 -- 우리가 비교한 6종이 모두 단량체여서 ipTM이 산출되지 않았다.
       (미측정 위험. 12절 참조)

축소 DB로 전수 스크리닝 -> 상위 후보 수십 건만 전체 DB로 재계산
    -> 이게 실용적인 조합이다. 시간 예산을 상위 후보에 집중시킨다.
```

**이미 축소 DB로 돌려 놓은 결과가 있다면 버리지 마라.** 단량체 신뢰도 기준으로는
전체 DB 결과와 실질적으로 같다.

#### ⑥-A 축소 DB 준비 (10~30분, 추정)

AF3 공식 저장소가 제공하는 축소(스몰) DB 세트를 받거나, 전체 DB에서 균등 추출한
대리 세트를 쓴다. 어느 쪽이든 `~/public_databases` 에 둔다.

```bash
mkdir -p ~/public_databases
# AF3 저장소의 DB 준비 스크립트를 축소 옵션으로 실행하거나,
# 이미 갖고 있는 축소 세트를 이 경로로 옮긴다
ls -la ~/public_databases
```

**주의:** 우리가 측정에 쓴 축소 DB는 연구자의 실제 파일이 아니라 공식 전체 DB에서
균등 추출해 만든 대리 세트다. 파일 구성이 다르면 MSA 깊이 절대값은 달라질 수 있다.
자세한 것은 [docs/reduced_db.md](docs/reduced_db.md).

#### ⑥-B 전체 DB 다운로드와 해제 (총 3시간 13분, 실측)

```bash
cd ~/af3_work/alphafold3
# AF3 저장소의 DB 다운로드 스크립트 실행 (경로/이름은 버전에 따라 다르니 --help 로 확인)
bash fetch_databases.sh ~/public_databases
```

실측 소요 (4병렬 다운로드, 평균 약 41MB/s 회선):

| 단계 | 시간 | 비고 |
|------|------|------|
| 압축 파일 238.8GB 다운로드 | **1시간 37분** | 4병렬 |
| 압축 해제 (전체) | 나머지 | |
| └ 그중 mmCIF tar 해제 | **1시간 36분** | 195,859개 파일. **최장 단계** |
| **다운로드 시작 ~ 전량 해제 완료** | **3시간 13분** | |
| 해제 후 점유 | **850GB** | |

무결성: 9개 항목 전부 원격 `Content-Length` 와 바이트 단위로 일치했다.

mmCIF 해제 중에 진행이 멈춘 것처럼 보일 것이다. 파일이 20만 개라 그렇다.
`du -sh ~/public_databases` 로 조금씩 늘어나는지만 확인하면 된다.

> 공식 문서에 적힌 '약 252GB' / '630GB' 와 우리 실측(238.8GB / 850GB)이 다르다.
> 단위 해석(GB 대 GiB)과 파일시스템 블록 반올림 때문이다. 어느 쪽이 틀린 게 아니고,
> **디스크는 실측값(850GB)을 기준으로 준비하라.**

### 3-5. 폴더 관례

이 저장소의 스크립트는 아래 배치를 기본으로 가정한다. 바꿀 수 있지만
그냥 이대로 쓰면 옵션을 거의 안 줘도 된다.

```
~/public_databases/          서열 DB
~/af3_models/                가중치 (af3.bin)
~/af3_work/                  작업 폴더. 여기서 명령을 실행한다
    <이름>_in/               입력 JSON
    <이름>_out/              결과
    <이름>_work/             로그, MSA 보관, 요약 CSV (스크립트가 만든다)
```

`<이름>` 은 작업 하나를 가리키는 이름이다. 예: `vhh_001`.

### 3-6. 첫 실행은 느리다 (단계 ⑦)

처음 한 번은 XLA가 GPU 커널을 컴파일한다. 우리가 관측한 프로세스 고정 오버헤드는
**콜드/고부하 상태에서 406~497초까지** 올라갔다. 웜 상태에서는 6.55~8.5초다.

즉 **처음 실행이 5~8분 걸려도 고장이 아니다.** 두 번째부터 빨라진다.
컴파일 캐시 디렉터리를 지정하면 이 첫 컴파일을 재사용할 수 있다 (스크립트가 기본으로 한다).

다만 캐시의 이득은 **배치가 커지면 0으로 수렴**한다. 첫 2건의 컴파일만 없애기 때문이다.
96건 순회에서 정상상태 4.20초는 캐시 유무와 완전히 무관했다 (실측).

### 3-7. conda 네이티브 설치 (Docker를 못 쓰는 경우)

sudo 권한이 없거나 Docker가 없는 서버에서는 conda로 직접 설치할 수 있다.
우리 검증 호스트가 정확히 그 경우였다. 전체 명령과 그때 만난 문제·해결은
[docs/install_log.md](docs/install_log.md) 에 있다. 요지만 옮기면:

```bash
# python 3.12 환경 (AF3가 3.12 이상을 요구한다)
conda create -y -n af3 python=3.12
conda activate af3
conda install -y -c conda-forge cmake zlib hmmer

# zlib 헤더를 conda env 에서 찾게 한다 (시스템 zlib-dev 가 없을 때 필요)
export CMAKE_PREFIX_PATH=$CONDA_PREFIX

cd ~/af3_work/alphafold3
pip install "jax[cuda12]==0.10.2"
pip install .
build_data
```

Docker 경로와 성능 차이: 우리 값은 conda 네이티브 측정이므로 컨테이너 기동 비용이
빠져 있다. 따라서 **우리 수치는 Docker 환경의 하한**이다. Docker에서는 건당 시간이
같거나 조금 더 나온다.

### 3-8. 총 예상 시간 정리

| | 경로 A (축소 DB) | 경로 B (전체 DB) |
|---|---|---|
| 클론 + 이미지 빌드 | 20~40분 (추정) | 20~40분 (추정) |
| 가중치 다운로드 | 1~5분 | 1~5분 |
| build_data | 수 분 (미측정) | 수 분 (미측정) |
| DB 준비 | 10~30분 (추정) | **3시간 13분 (실측)** |
| 첫 실행 컴파일 | 최대 8분 (실측) | 최대 8분 (실측) |
| **합계 (승인 대기 제외)** | **약 40분~1시간 30분** | **약 4~5시간** |
| 디스크 점유 | 약 5GB | 약 **855GB** |

여기에 가중치 접근 승인 대기(수일, 추정)가 앞에 붙는다.
**승인 요청은 설치 첫날에 먼저 넣어 두고 나머지를 진행하라.**

---

## 4. 동작 확인

설치가 끝났으면 실행 전에 환경을 점검한다. 이 단계를 건너뛰면
"3시간 돌리고 나서 DB 경로가 틀렸다는 걸 아는" 일이 생긴다.

```bash
cd ~/af3_work
bash scripts/af3_check.sh 2>&1 | tee af3_check.txt
```

또는 래퍼로:

```bash
bash scripts/af3run.sh vhh_001 check
```

### 무엇을 확인해야 하나

진단이 출력하는 항목 중 아래 5개는 반드시 통과해야 한다.

| 확인 항목 | 통과 기준 |
|-----------|-----------|
| GPU 인식 | `nvidia-smi` 가 GPU 이름과 VRAM을 출력한다 |
| 도커 이미지 | `alphafold3` 이미지가 목록에 있다 |
| 가중치 | `~/af3_models/af3.bin` 이 있고 크기가 약 1.15GB |
| DB 경로 | `~/public_databases` 가 있고 비어 있지 않다 |
| HMMER | `jackhmmer -h` 가 동작하고 `--seq_limit` 항목이 보인다 |

`--seq_limit` 이 안 보이면 AF3 패치가 적용되지 않은 HMMER다. 도커 이미지를 쓰면
보통 문제되지 않는다. conda 설치라면 [docs/install_log.md](docs/install_log.md) 참조.

### 정상 출력의 모습

세부 형식은 스크립트 버전에 따라 다르니 **항목별 판정 표시(있음/없음, OK/실패)** 를 보라.
아래는 형태의 예시다.

```
[GPU]      NVIDIA GeForce RTX 5090, 32768 MiB, 드라이버 5xx.xx        OK
[Docker]   이미지 alphafold3 있음                                      OK
[가중치]   ~/af3_models/af3.bin  1146811260 B                        OK
[DB]       ~/public_databases  항목 n개, 총 x GB                      OK
[HMMER]    jackhmmer 3.4, --seq_limit 패치 있음                       OK
[디스크]   작업 폴더 여유 xxx GB                                      OK
```

하나라도 실패하면 [10절](#10-자주-만나는-문제) 에서 해당 항목을 찾아보라.

### 스모크 테스트: 1건 실제로 돌려 보기

진단이 통과했으면 예제 1건으로 실제 파이프라인을 확인한다.

```bash
mkdir -p ~/af3_work/smoke_in
cp examples/vhh_monomer.json ~/af3_work/smoke_in/
cd ~/af3_work
python3 scripts/af3_batch.py --name smoke --stage oneshot
```

첫 실행이라 5~8분 걸릴 수 있다 ([3-6](#3-6-첫-실행은-느리다-단계-⑦)). 끝나면
`smoke_out/vhh_7mfv_1/` 안에 `*_summary_confidences.json` 과 `*_model.cif` 가 생겨야 한다.

```bash
ls smoke_out/*/
```

여기까지 되면 설치는 끝이다.

---

## 5. 입력 파일 준비

AF3는 **타깃 하나당 JSON 파일 하나**를 입력으로 받는다. 2000건이면 JSON 2000개다.
손으로 만들 수 없으니 `af3_prepare.py` 로 만든다.

### 5-1. AF3 입력 JSON이 어떻게 생겼는지

가장 단순한 형태는 단백질 사슬 하나다 (`examples/vhh_monomer.json`).
아래 서열은 PDB 7MFV(합성 나노바디 Sb16, 116 aa)의 실제 서열이다.

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

읽는 법:

| 필드 | 뜻 |
|------|-----|
| `name` | 타깃 이름. **출력 폴더 이름이 이걸로 만들어진다.** 공백·한글·슬래시 피할 것 |
| `modelSeeds` | 난수 시드 배열. `[1]` 이면 시드 1개. 시드를 늘리면 시간도 비례해 늘어난다 |
| `sequences` | 이 구조에 들어가는 **모든 실체의 배열**. 여기 담긴 것 전부가 한 구조로 함께 예측된다 |
| `sequences[].protein.id` | 사슬 ID. `"A"`, `"B"` ... 구조 파일에서 이 이름으로 보인다 |
| `sequences[].protein.sequence` | 아미노산 1문자 서열 |
| `dialect`, `version` | AF3 입력 형식 표시. 그대로 두면 된다 |

`protein` 자리에 올 수 있는 것들:

| 키 | 무엇 | 서열/식별자 |
|----|------|-------------|
| `protein` | 단백질 사슬 | 아미노산 1문자 서열 |
| `dna` | DNA 사슬 | `ACGT` |
| `rna` | RNA 사슬 | `ACGU` |
| `ligand` | 저분자 | CCD 코드 (`{"ligand": {"id": "L", "ccdCodes": ["ATP"]}}`) 또는 SMILES |

### 5-2. 복합체 만들기: 항원 파트너 붙이기

`sequences` 배열에 항목을 하나 더 넣으면 그 둘이 **함께** 예측된다. 이게 복합체다
(`examples/vhh_antigen_complex.json`). 아래는 PDB 1MEL의 실제 조합으로, 낙타 단일도메인 항체(148 aa)와
그 항원 lysozyme(129 aa)이다.

```json
{
  "name": "vhh_antigen_complex",
  "modelSeeds": [1],
  "sequences": [
    {
      "protein": {
        "id": "A",
        "sequence": "DVQLQASGGGSVQAGGSLRLSCAASGYTIGPYCMGWFRQAPGKEREGVAAINMGGGITYYADSVKGRFTISQDNAKNTVYLLMNSLEPEDTAIYYCAADSTIYASYYECGHGLSTGGYGYDSWGQGTQVTVSSGRYPYDVPDYGSGRA"
      }
    },
    {
      "protein": {
        "id": "B",
        "sequence": "KVFGRCELAAAMKRHGLDNYRGYSLGNWVCAAKFESNFNTQATNRNTDGSTDYGILQINSRWWCNDGRTPGSRNLCNIPCSALLSSDITASVNCAKKIVSDGNGMNAWVAWRNRCKGTDVQAWIRGCRL"
      }
    }
  ],
  "dialect": "alphafold3",
  "version": 1
}
```

이렇게 하면 출력에 **ipTM(계면 신뢰도)** 이 함께 나온다. 단량체에서는 ipTM이 나오지 않는다.

복합체 예측은 두 가지를 각오해야 한다.

1. **토큰 수가 늘어나 패딩 버킷이 커진다.** 버킷은 토큰 수 이상인 가장 작은 계단이 잡히므로,
   116 aa VHH는 버킷 128에 들어가지만 130 aa는 이미 버킷 256이다 (실측 6종: 116/123 aa → 128,
   130/131/135/138 aa → 256). 여기에 300 aa 항원을 붙이면 버킷 512로 올라간다.
   버킷 128 대 256의 실측 차이만 2.25배였다 (4.20초 대 9.44초). 계단이 오를 때마다 크게 늘어난다.
2. **paired MSA가 계면 예측에 중요하다.** 축소 DB의 paired 깊이는 158~225,
   전체 DB는 24,250~27,353 로 120~150배 차이다. 복합체에는 전체 DB를 써야 할 것으로
   보인다. 다만 이건 추론이고 우리가 직접 측정하지 못했다 ([12절](#12-측정-조건과-한계)).

### 5-3. `af3_prepare.py`: FASTA/CSV에서 JSON 2000개 만들기

세부 옵션은 반드시 다음으로 확인하라. 여기 적은 것보다 스크립트가 정확하다.

```bash
python3 scripts/af3_prepare.py --help
```

기본 사용법. FASTA 하나에서 타깃별 JSON을 만든다.

```bash
# examples/vhh_panel.fasta 의 각 레코드마다 JSON 1개
python3 scripts/af3_prepare.py examples/vhh_panel.fasta --out-dir vhh_001_in
```

**실행 전에 반드시 `--dry-run` 을 먼저 하라.** 무엇이 몇 개 만들어질지, 이름이 어떻게
붙을지 실제로 쓰지 않고 보여준다. 2000건을 잘못된 이름으로 만들어 놓고 나중에 아는 것보다
훨씬 싸다.

```bash
python3 scripts/af3_prepare.py examples/vhh_panel.fasta --out-dir vhh_001_in --dry-run
```

CSV 입력도 된다. 이름과 서열을 열로 갖는 형태다 (`examples/vhh_panel.csv`,
공개 PDB 유래 6종. 아래는 앞 3줄만):

```csv
name,sequence
vhh_7djx_1,QVQLVESGGGLVQAGGSLRLSCAASGRTFSSYAMGWFRQAPGKERECVAAMDWSTSATYYADSVKGRFTISRDNAKNTVYLQMNSLKPEDTAVYYCAADLDYSDYGPFPGDMDYWGKGTQVTVSSHHHHHH
vhh_7a50_1,QVQLQESGGGLVQAGDSLRLSCAASGRTFSTYPMGWFRQAPGKEREFVAASSSRAYYADSVKGRFTISRNNAKNTVYLQMNSLKPEDTAVYYCVADSSPYYRRYDAAQDYDYWGQGTQVTVSSGRYPYDVPDYGSGRA
vhh_8v8k_1,QVQLVESGGGLVQAGGSLRLSCAASGSISSISTMGWYRQAPGKERELVAAITSGGSTNYADSVKGRFTISRDNAKNTVYLQMNSLKPEDTAVYYCNFKYYSGSYFYKSEYDYWGKGTPVTVSS
```

`examples/vhh_panel.fasta` 는 같은 6종의 FASTA 판이다.
이 6종이 [3-4절](#3-4-데이터베이스-선택-단계-⑥-여기가-가장-중요한-판단이다)의
축소 DB 대 전체 DB 비교에 쓴 타깃과 같으므로, 결과를
[results_example/af3_summary.csv](results_example/af3_summary.csv) 와 직접 비교할 수 있다.

항원 파트너를 모든 타깃에 공통으로 붙이려면 (옵션 이름은 `--help` 로 확인):

```bash
python3 scripts/af3_prepare.py examples/vhh_panel.csv \
    --out-dir vhh_cplx_in \
    --partner-fasta examples/antigen.fasta \
    --dry-run
```

리간드를 넣거나 다량체를 만드는 옵션도 있다. `--help` 를 보라.

### 5-4. 만들어진 것 확인

```bash
ls vhh_001_in | head -3
ls vhh_001_in | wc -l
python3 -c "import json;print(json.load(open('vhh_001_in/vhh_A01.json'))['name'])"
```

```
vhh_A01.json
vhh_A02.json
vhh_A03.json
2000
vhh_A01
```

**`._` 로 시작하는 파일이 보이면 지워라.** macOS에서 만든 tar를 리눅스에서 풀면 생기는
쓰레기 파일인데, 읽는 순간 파이프라인이 죽는다. 이것 때문에 실제로 측정 3시간을 날렸다.
[10절 첫 항목](#10-자주-만나는-문제)을 보라.

```bash
find vhh_001_in -name '._*' -delete
```

---

## 6. 배치 실행

### 6-1. 권장 방법: `run_af3_batch_improved.py`

```bash
cd ~/af3_work

# 1. 계산 없이 상태만 본다. 이미 돌린 결과가 있으면 여기서 점검된다
python3 scripts/run_af3_batch_improved.py --audit

# 2. 소규모로 시험한다. 실행 전에 경로와 모드를 보여주고 한 번 물어본다
python3 scripts/run_af3_batch_improved.py --input-dir test_in --output-dir test_out

# 3. 전수 실행. 백그라운드로 돌릴 때는 --yes 가 필요하다
nohup python3 scripts/run_af3_batch_improved.py --yes > af3.log 2>&1 &
tail -f af3.log
```

`./vhh_001_in` 의 JSON 전부를 **컨테이너 1회 기동**으로 순회한다. 기본 폴더 이름은
파일 위쪽 `INPUT_DIR_NAME` / `OUTPUT_DIR_NAME` 에서 바꾸거나 `--input-dir` /
`--output-dir` 로 지정한다.

**`--yes` 를 빼면 백그라운드에서 멈춘다.** 확인 질문을 띄울 수 없기 때문이고,
로그에 그렇게 적힌다. 잘못된 폴더에 2000건을 쏟지 않으려는 안전장치다.

주요 옵션은 다음과 같다.

| 명령 | 하는 일 |
|------|---------|
| `--guide` | 경로·모드 설명만 보고 끝낸다. 아무것도 만들지 않는다 |
| `--audit` | 실행 없이 완료/미완료와 잔여 폴더만 점검. 미완료가 있으면 종료코드 1 |
| `--mode data` | MSA/템플릿만. **GPU를 할당하지 않아** 추론과 병행할 수 있다 |
| `--mode inference` | 준비된 입력으로 추론만 |
| `--per-file` | 파일마다 컨테이너를 따로 띄운다 (느리다. 문제 격리용) |
| `--cleanup` | 격리 결과와 잔여 staging 을 미리 보여준 뒤 정리 |
| `--yes` | 확인 질문에 자동 응답. 백그라운드 실행에 필요 |

이 러너가 기존 방식과 다른 점은 세 가지다.

- **완료 판정을 폴더 존재가 아니라 최종 산출물로 한다.** AF3 는 추론 *전* 에
  `<name>_data.json` 을 쓰므로, 폴더만 보면 추론 중 끊긴 것을 완료로 오인한다.
  `_ranking_scores.csv`, `_model.cif`, `_summary_confidences.json` 세 개가 모두
  있고 크기가 0보다 커야 완료로 본다.
- **미완료 결과를 지우지 않고 `.af3_incomplete/` 로 옮긴다.** 작업별로 최신 하나만
  보존하므로 반복 실패해도 디스크가 차지 않는다.
- **같은 출력 폴더에 두 번 실행되지 않는다.** 파일 잠금으로 막고, 어느 프로세스가
  쓰고 있는지 알려준다.

### 6-2. 더 많은 기능이 필요할 때: `af3run.sh`

```bash
bash scripts/af3run.sh vhh_001
```

경량 스크리닝 설정, 20건 벤치마크, 결과 집계까지 한 명령으로 묶은 래퍼다.
`af3_batch.py` 를 호출한다.

두 번째 인자로 모드를 준다.

| 명령 | 하는 일 |
|------|---------|
| `af3run.sh vhh_001 check` | 환경 진단만 |
| `af3run.sh vhh_001 dry` | 실제 실행 없이 명령만 확인: **권장 첫 단계** |
| `af3run.sh vhh_001 screen` | 경량 스크리닝 (sample 1, recycle 3). 전수용 |
| `af3run.sh vhh_001 full` | 기본값 정밀 (sample 5, recycle 10). 상위 후보용 |
| `af3run.sh vhh_001 msa` | MSA(CPU)만 미리 계산해 보관 |
| `af3run.sh vhh_001 infer` | 보관된 MSA로 추론(GPU)만 |
| `af3run.sh vhh_001 oneshot` | MSA+추론을 한 프로세스에서. 가장 단순 |
| `af3run.sh vhh_001 retry` | 실패한 것만 재시도 |
| `af3run.sh vhh_001 bench` | 앞 20건만 돌려 건당 시간 측정 |
| `af3run.sh vhh_001 collect` | 결과를 CSV 한 장으로 집계 |

**2000건을 처음 돌릴 때의 권장 순서:**

```bash
bash scripts/af3run.sh vhh_001 check      # 1. 환경
bash scripts/af3run.sh vhh_001 dry        # 2. 명령 확인
bash scripts/af3run.sh vhh_001 bench      # 3. 20건으로 건당 시간 측정
# 여기서 나온 건당 시간 x 2000 이 실제 예상 시간이다. 감당 가능한지 판단하고
bash scripts/af3run.sh vhh_001 screen     # 4. 전수
bash scripts/af3run.sh vhh_001 collect    # 5. 집계
```

### 6-3. `af3_batch.py` 직접 쓰기

래퍼가 부족하면 러너를 직접 쓴다. 전체 옵션은 `--help` 로 확인하라.

```bash
python3 scripts/af3_batch.py --help
```

자주 쓰는 것:

```bash
# 무엇을 실행할지 눈으로 확인 (실제로 돌리지 않는다)
python3 scripts/af3_batch.py --name vhh_001 --dry-run

# 가장 간단한 개선: 컨테이너 1회, MSA+추론을 한 프로세스가 전수 순회
python3 scripts/af3_batch.py --name vhh_001 --stage oneshot

# 2단계 분리 (권장). MSA 먼저 -> 추론
python3 scripts/af3_batch.py --name vhh_001 --stage both

# 경량 스크리닝 설정으로 전수
python3 scripts/af3_batch.py --name vhh_001 --stage both --diffusion-samples 1 --recycles 3

# 실패한 것만 재시도
python3 scripts/af3_batch.py --name vhh_001 --stage both --retry
```

`--stage` 는 4가지다.

| 값 | 뜻 |
|----|-----|
| `msa` | MSA(CPU)만. 산출물 `*_data.json` 을 `msa_store` 에 보관 |
| `infer` | 보관된 MSA로 추론(GPU)만 |
| `both` | MSA 후 추론. **기본값이고 권장** |
| `oneshot` | 한 프로세스에서 둘 다. 가장 단순 |

주요 옵션 (전체는 `--help`):

```
--name              작업 이름. <이름>_in / <이름>_out / <이름>_work 를 쓴다
--input-dir         입력 폴더 직접 지정
--output-dir        출력 폴더 직접 지정
--image             도커 이미지 이름 (기본 alphafold3, 환경변수 AF3_IMAGE)
--db-dir            DB 경로 (기본 ~/public_databases)
--model-dir         가중치 경로 (기본 ~/af3_models)
--cache-dir         XLA 컴파일 캐시 경로
--diffusion-samples 샘플 수 (스크리닝 1, 정밀 5)
--recycles          recycle 수 (스크리닝 3, 정밀 10)
--msa-n-cpu         MSA 검색 스레드. 기본 min(코어수/2, 8)
--msa-workers       MSA 동시 갈래. 기본 1. 실측상 1이 최적이니 건드리지 말 것
--limit N           앞 N건만 (벤치마크용)
--retry             실패한 것만 다시
--no-skip           이미 끝난 것도 다시
--docker            도커 실행 명령 강제 (예: 'sudo docker')
--dry-run           실행하지 않고 명령만 출력
```

### 6-4. `--buckets` 에 128을 반드시 포함하라

> ### 경고
>
> AF3의 기본 패딩 버킷 사다리는 **128에서 시작한다**
> (`run_alphafold.py` 의 `_BUCKETS` 기본값, 소스 대조 및 실측 확인).
>
> `--buckets` 를 직접 넘길 일이 있으면 **반드시 128을 포함**하라.
> 128을 빠뜨리면 **토큰 128 이하인 건**(약 128 aa 이하의 짧은 VHH)이 갈 곳을 잃고
> 256 버킷으로 밀린다. 실측 차이:
>
> | 버킷 | 정상상태 추론 시간 |
> |------|-------------------|
> | 128 | **4.20초** |
> | 256 | 9.44초 |
>
> **2.25배**다. 2000건이면 GPU 단계가 2.3시간에서 5.2시간이 된다.
>
> `af3_batch.py` 는 기본값에 128을 포함하고 있으므로, 직접 넘기지 않으면 문제없다.
> 결과 CSV의 `패딩버킷` 열이 256으로 나오면 이 함정에 빠진 것이다.

### 6-5. 2단계 전략: MSA 먼저, 추론 나중

MSA(CPU)와 추론(GPU)을 분리하면 다음이 가능해진다.

- MSA 산출물(`*_data.json`)이 `msa_store` 에 보관되므로 **추론 설정을 바꿔 재실행할 때
  MSA를 다시 계산하지 않는다.** 스크리닝(sample 1)으로 전수 돌린 뒤 상위 후보만
  정밀(sample 5)로 재실행하는 것이 거의 공짜가 된다.
- CPU가 노는 시간과 GPU가 노는 시간을 분리해 관찰할 수 있다.

```bash
# 1단계: MSA만 전수 (CPU 바운드, 오래 걸린다)
python3 scripts/af3_batch.py --name vhh_001 --stage msa

# 2단계: 추론만, 경량 설정으로 전수 (GPU)
python3 scripts/af3_batch.py --name vhh_001 --stage infer --diffusion-samples 1 --recycles 3

# 집계 후 상위 100건 목록 만들기
python3 scripts/af3_collect.py vhh_001_out --top 100 --top-list top100.txt

# 3단계: 상위 100건만 정밀 재실행 (MSA는 재사용되므로 빠르다)
#   top100.txt 의 이름에 해당하는 JSON만 새 폴더로 모아 다시 돌린다
mkdir -p vhh_top_in
while read n; do cp "vhh_001_in/${n}.json" vhh_top_in/ 2>/dev/null; done < top100.txt
python3 scripts/af3_batch.py --name vhh_top --stage infer --diffusion-samples 5 --recycles 10
```

MSA 설정은 실측 기준으로 이미 최적값이 기본이다. **`--msa-workers` 를 올리지 마라.**
같은 스레드 총량에서 갈래를 늘리면 오히려 느려진다 (32스레드 1갈래 0.890 대
2갈래 0.767 타깃/분). AF3가 이미 체인당 DB 4개를 내부에서 병렬 검색한다
(`ThreadPoolExecutor(max_workers=4)`).

![MSA 스레드 확장성]({{artifact:art_0003072c-a939-4dd9-af85-45a0c7495a91}})

| 총 스레드 | 최고 처리율 (타깃/분) |
|-----------|----------------------|
| 24 | 0.778 |
| 32 | 0.890 |
| 48 | **0.895** |
| 96 | 0.848 (오히려 감소) |

측정 조건: 24코어 호스트, **전체 DB 급(4종 각 4GB 슬라이스) 기준**
(`results_example/msa_throughput.csv` 의 `db` 열). 축소 DB 약 2GB 에서는 데이터
파이프라인이 건당 1.98초로 훨씬 짧다.

즉 코어 수의 약 1.3배에서 포화하고, 그 이상은 손해다.
권장값은 `--jackhmmer_n_cpu = --nhmmer_n_cpu = min(코어수/2, 8)`, 동시 1갈래.

| 코어 수 | 권장 스레드 |
|---------|-------------|
| 8 | 4 |
| 12 | 6 |
| 16 | 8 |
| 24 이상 | 8 |

AF3 기본값이 `min(코어수, 8)` 이므로 **8코어 이상이면 기본값이 이미 최적에 가깝다.**

### 6-6. 진행 상황 보기

스크립트는 `<이름>_work/` 에 상태를 남긴다.

```bash
# 진행 상태와 실패 목록
cat vhh_001_work/state.json | python3 -m json.tool | head -30

# 타깃별 측정 결과 (건당 시간이 여기 쌓인다)
tail -5 vhh_001_work/run_summary.csv

# 컨테이너 stdout 전체 로그
tail -f vhh_001_work/*.log

# 지금까지 몇 건 끝났나
ls vhh_001_out | wc -l
```

GPU가 실제로 일하고 있는지:

```bash
nvidia-smi --query-gpu=utilization.gpu,memory.used --format=csv -l 5
```

`utilization.gpu` 가 오르내리면 정상이다. `memory.used` 가 15GB 근처로 크게 나오는 것은
정상이다 (XLA 선점량, [10절](#10-자주-만나는-문제) 참조).

### 6-7. 중간에 끊겼을 때

전원, 커널 OOM, ssh 끊김 등으로 멈추면 **그냥 같은 명령을 다시 실행하면 된다.**
이미 끝난 타깃은 건너뛴다.

```bash
python3 scripts/af3_batch.py --name vhh_001 --stage both
```

미완성 결과 폴더는 스크립트가 `partial/` 로 옮겨 둔다 (그대로 두려면 `--keep-partial`).
실패한 것만 골라 다시 하려면:

```bash
python3 scripts/af3_batch.py --name vhh_001 --stage both --retry
```

처음부터 다시 계산하고 싶으면 `--no-skip`. **2000건에 이걸 쓰면 처음부터 다시 돌린다.**
정말 필요할 때만.

장시간 실행은 `nohup` 이나 `tmux` 로 띄워 ssh가 끊겨도 살아 있게 하라.

```bash
tmux new -s af3
python3 scripts/af3_batch.py --name vhh_001 --stage both 2>&1 | tee vhh_001_work/run.log
# Ctrl+B, D 로 빠져나온다. 다시 붙을 때는 tmux attach -t af3
```

---

## 7. 왜 빠른가 (선택 읽기)

이 절은 안 읽어도 실행에 지장 없다. 다만 "왜 이런 구조인가"를 알면
설정을 잘못 바꾸는 일이 줄어든다.

### 문제: 건당 고정 오버헤드

JSON 하나마다 `docker run` 을 새로 띄우면 타깃마다 아래를 처음부터 반복한다.

1. 컨테이너 기동
2. JAX/CUDA 초기화
3. 가중치 로딩 (1.15GB, 파라미터 3.68억 개)
4. XLA 커널 컴파일

이 고정 비용이 **건당 9.1~9.2초**로 측정됐다. 버킷 128에 들어가는 짧은 VHH의
실제 추론은 정상상태 4.20초다.
**준비하는 시간이 일하는 시간보다 두 배 이상 길다.**

### 측정: 32건 × 3반복, MSA 없는 GPU 추론 경로만, 웜 캐시

| 조건 | 건당 시간 | 최악 대비 |
|------|-----------|-----------|
| 프로세스별 + 캐시 미지정 (= 기존 방식) | 31.95초 | 1.00배 |
| 프로세스별 + 캐시 지정 | 18.13초 | 1.76배 |
| 단일 프로세스 + 캐시 미지정 | 6.26초 | 5.10배 |
| **단일 프로세스 + 캐시 지정 (권장)** | **5.39초** | **5.93배** |

세 번 반복한 값이 5.39 / 5.39 / 5.41초로 편차 0.1%였다. 재현되는 수치다.

![A/B 벤치마크]({{artifact:art_ae536755-bba6-400b-a469-d79012f3c003}})

### 효과 분리: 무엇이 실제로 효과인가

두 개입을 따로 재면 어느 쪽이 효과인지 갈린다.

- **단일 프로세스화가 주효과다.** 캐시 미지정 조건에서만 봐도 31.95 → 6.26초, **5.10배**.
  프로세스 기동 비용 9.1~9.2초/건이 0.44초/건으로 상환된다.
- **캐시 디렉터리는 부효과다.** 1.76배. 그리고 이 이득은 **첫 2건의 컴파일만 없애므로
  배치가 커지면 0으로 수렴**한다. 96건 순회에서 정상상태 4.20초는 캐시 유무와
  완전히 무관했다.

2000건 규모에서는 사실상 단일 프로세스화가 전부다.

### 하지 않아도 되는 것: 길이순 정렬

"입력을 길이순으로 정렬하면 버킷 재컴파일을 줄여 빨라진다"는 설명은 **틀렸다.**
실측 이득은 **0.00초/건**이다. XLA는 버킷별 컴파일 결과를 **프로세스 수명 동안 보유**하므로,
버킷 128과 256을 11번 왕복해도 1번 전환과 같은 시간이 나왔다 (둘 다 9.11초/건).
컴파일은 각 버킷의 **첫 등장에서만** 일어난다.

정렬은 로그를 읽기 편하게 하고 버킷 목록을 좁히는 부수 효과로 남겨 뒀을 뿐이다.

### 왜 "여러 개 동시 실행"이 답이 아닌가

직관적으로는 GPU 하나에 프로세스 여러 개를 붙이거나 MSA를 병렬로 여럿 돌리면
빨라질 것 같다. 실측은 반대다.

- **GPU 쪽:** 고정 오버헤드(9.1초)가 프로세스마다 다시 발생하므로, 프로세스를 N개 띄우면
  오버헤드도 N배가 된다. VRAM은 남지만(실제 피크 3GB) 그게 문제가 아니다.
  단일 프로세스가 순회하면 오버헤드는 1회로 끝난다.
- **MSA 쪽:** AF3가 이미 체인당 DB 4개를 `ThreadPoolExecutor(max_workers=4)` 로
  병렬 검색한다. 위에 갈래를 더 얹으면 같은 자원을 놓고 다툰다.
  같은 스레드 총량에서 32스레드 1갈래 0.890 대 2갈래 0.767 타깃/분으로 **손해**다.

### 고치고 나면 남는 것은 MSA DB 구성이 정한다

GPU 단계를 5.93배 빠르게 만들면 GPU는 더 이상 병목이 아니다. 그 다음에 무엇이 남는지는
**1단계에 어떤 DB 를 쓰는가**에 달려 있다. 아래 두 구성은 조건이 달라 직접 비교할 수 없다.

| 구성 | 데이터 파이프라인(MSA) 건당 | GPU 추론 건당 | 2000건 합계 | MSA 비중 | 근거 |
|---|---|---|---|---|---|
| 축소 DB 약 2GB (연구자 현재 구성, 권장) | 1.98초 | 5.39초 | **4.1시간** | 27% | 각 항목 직접측정, 합계는 합산 추정 |
| 전체 DB 급 4GB 슬라이스 4종 | 67.0초 (스레드 스윕 포화점) | 5.39초 | **40.2시간 (1.7일)** | 93% | MSA 인용, 추론 직접측정, 합계는 합산 추정 |

**축소 DB 구성에서는 MSA 가 병목이 아니다.** 데이터 파이프라인 1.98초가 GPU 추론 5.39초보다
짧다. 2000건이면 파이프라인 1.1시간 + 추론 3.0시간이다.
**MSA 가 93%를 차지하는 것은 전체 DB 급 구성에서만 성립한다.** 조건을 빼고 "코드를 고치면
MSA 가 93%" 라고 쓰면 틀린 말이 된다. 조건별 원자료와 대조 측정은
`docs/two_stage_notes.md` 3절, `docs/msa_correction_notes.md` 참고.

연구자의 현재 방식은 건당 341초 = 189시간 (7.9일)이다.

**개선 배수를 정직하게 쓰면:**

- GPU 추론 단계만: **5.93배** (직접 측정)
- 전체 파이프라인: **4.7~46배** (189시간 → 4~40시간, DB 구성에 따라 갈린다.
  하단은 전체 DB 급 구성 40.2시간, 상단은 축소 DB 구성 4.1시간 기준. 모두 합산 추정)

> `341 / 5.39 = 63배` 같은 계산은 **성립하지 않는다.** 341초에는 MSA가 포함돼 있고
> 5.39초에는 포함돼 있지 않다. 비교 대상이 다르다. 이 식은 쓰지 마라.

그래서 GPU 단계를 더 짜내는 것보다 **MSA를 줄이는 것**(축소 DB 사용, MSA 재사용,
동일 서열 중복 제거)이 이제 더 큰 개입이다. 그리고 MSA 처리율은 코어 수의 1.3배 지점에서
포화하므로 CPU를 더 넣어도 한계가 있다. 포화의 원인이 CPU 경합인지 디스크 I/O인지는
**미규명**이다.

자세한 측정은 [docs/benchmark_report.md](docs/benchmark_report.md) 와
[docs/diagnosis_report.md](docs/diagnosis_report.md).

---

## 8. 결과 해석

여기가 실제로 가장 중요한 절이다. 숫자가 나왔다고 구조가 맞는 게 아니다.

### 8-1. 출력 폴더에 무엇이 들어 있나

```
vhh_001_out/
    vhh_A01/                                  <- 타깃 하나당 폴더 하나
        vhh_A01_model.cif                     1위 모델 구조 (이걸 보면 된다)
        vhh_A01_summary_confidences.json      1위 모델의 요약 지표
        vhh_A01_confidences.json              원자별 pLDDT, 토큰별 PAE
        vhh_A01_ranking_scores.csv            시드 x 샘플 전체의 ranking score
        vhh_A01_data.json                     MSA가 담긴 입력 (깊이 계산에 쓴다)
        seed-1_sample-0/                      샘플별 동일 3종 파일
        seed-1_sample-1/
        ...
```

| 파일 | 언제 보나 |
|------|-----------|
| `*_model.cif` | 구조를 눈으로 볼 때. PyMOL/ChimeraX에 이 파일을 넣는다 ([9절](#9-결과-보기-시각화)) |
| `*_summary_confidences.json` | 이 타깃을 통과시킬지 판단할 때. ranking_score, ptm, iptm |
| `*_confidences.json` | 어느 부위가 못 맞았는지 볼 때. pLDDT, PAE |
| `*_ranking_scores.csv` | 결과가 우연인지 볼 때. 샘플 간 산포 |
| `*_data.json` | MSA가 얼마나 깊었는지 볼 때 |

**타깃 폴더 바로 아래의 파일이 AF3가 1위로 뽑은 모델이다.** `seed-*_sample-*/` 하위는
개별 샘플이다. 보통 1위만 보면 된다.

### 8-2. 지표가 각각 무슨 뜻인가

| 지표 | 범위 | 무엇 | 어디 |
|------|------|------|------|
| **pLDDT** | 0~100 | **잔기/원자 단위 국소 정확도.** 이 부분을 얼마나 확신하는가 | `*_confidences.json` |
| **pTM** | 0~1 | **전체 폴드가 맞을 확률의 대리 지표** | `*_summary_confidences.json` |
| **ipTM** | 0~1 | **계면 정확도.** 복합체에서만 산출. 사슬이 서로 제대로 붙었나 | 같음 |
| **PAE** | Å | **토큰 쌍별 위치 오차 기댓값.** 두 부위의 상대 배치를 얼마나 확신하는가 | `*_confidences.json` |
| **ranking_score** | 해당 없음 | AF3가 모델을 줄 세울 때 쓰는 종합 점수 | 같음 |
| **fraction_disordered** | 0~1 | 무질서로 판정된 비율 | 같음 |
| **has_clash** | 0/1 | 원자 충돌 발생 | 같음 |

### 8-3. 판정 기준 구간

`af3_collect.py` 가 CSV의 `등급` 열에 쓰는 기준이다. AlphaFold 계열의 통상적 해석
구간을 이 배치에 맞춰 적용한 것이다.

**pLDDT (0~100)**

| 구간 | 해석 |
|------|------|
| 90 이상 | 매우 높음: 측쇄 수준까지 신뢰 |
| 70~90 | 신뢰: 주사슬(백본) 신뢰 |
| 50~70 | 낮음: 접힘 방향 정도만 |
| 50 미만 | 매우 낮음: 구조가 없거나 무질서 영역 |

**pTM (0~1)**

| 구간 | 해석 |
|------|------|
| 0.5 초과 | 전체 폴드가 대체로 맞다고 볼 수 있는 하한선 |

**ipTM (0~1, 복합체만)**

| 구간 | 해석 |
|------|------|
| 0.8 이상 | 계면 신뢰 |
| 0.6~0.8 | 회색지대: 판단 보류 |
| 0.6 미만 | 계면 실패 가능성 높음 |

**등급 판정 규칙**

복합체는 ipTM이 나오므로 그것을 1차 기준으로 쓴다.

| 등급 | 조건 |
|------|------|
| `A_계면신뢰` | ipTM ≥ 0.8 이고 pLDDT평균 ≥ 80 |
| `B_계면회색` | ipTM ≥ 0.6 |
| `C_계면실패` | 그 외 |

단량체는 ipTM이 없으므로 pLDDT와 pTM을 함께 본다.

| 등급 | 조건 |
|------|------|
| `A_높음` | pLDDT평균 ≥ 90 이고 pTM ≥ 0.7 |
| `B_신뢰` | pLDDT평균 ≥ 80 이고 pTM ≥ 0.5 |
| `C_보통` | pLDDT평균 ≥ 70 |
| `D_낮음` | 그 외 |

**경고 열** (등급과 별개로 붙는다):

| 경고 | 조건 | 뜻 |
|------|------|-----|
| `충돌` | has_clash > 0 | 원자가 겹쳤다. 구조를 의심하라 |
| `무질서` | fraction_disordered ≥ 0.1 | 무질서 비율이 높다 |
| `MSA얕음` | unpaired 깊이 < 100 | 축소 DB를 쓰면 정상적으로 붙는다 |
| `샘플불안` | ranking 산포 ≥ 0.05 | 샘플마다 결과가 흔들린다. 재현성 낮음 |
| `버킷256` | 패딩 버킷 ≥ 256 | 추론이 2.25배 느려진 건이다 |

### 8-4. 반드시 알아야 할 두 가지 함정

> ### 함정 1. 신뢰도는 정답과의 일치도가 아니다
>
> pLDDT, pTM, ipTM, ranking_score는 전부 **모델이 자기 예측을 얼마나 확신하는가**다.
> **실제 구조와 얼마나 맞는가가 아니다.**
>
> 확신에 차서 틀릴 수 있다. 특히 학습 데이터에 유사 구조가 많은 계열(면역글로불린 폴드가
> 정확히 그렇다)은 프레임워크 부분의 pLDDT가 항상 높게 나온다. 그게 CDR 루프의 배치가
> 맞다는 뜻은 아니다.
>
> **이 값들은 실험 검증 대상을 줄이는 순위 지표로만 쓰라.** "pLDDT 92니까 이 구조가 맞다"는
> 결론은 이 데이터로 낼 수 없다.

> ### 함정 2. ranking_score를 단독 순위 기준으로 쓰지 마라
>
> AF3의 ranking_score 정의는 다음과 같다.
>
> ```
> ranking_score = 0.8 x (ipTM 또는 단량체면 pTM)
>               + 0.2 x pTM
>               + 0.5 x fraction_disordered
>               - 100 x has_clash
> ```
>
> **`fraction_disordered` 를 더한다.** 즉 무질서 비율이 높은 건이 pTM이 더 낮아도
> ranking_score는 더 높게 나올 수 있다. 이 점수는 원래 "같은 타깃의 여러 샘플 중
> 어느 것을 대표로 쓸까"를 고르기 위한 것이고, **서로 다른 타깃을 줄 세우는 용도가 아니다.**
>
> 스크리닝 순위는 **pTM(단량체) 또는 ipTM(복합체) 과 pLDDT평균을 함께** 보라.
> `af3_collect.py` 의 `--top-by` 로 기준 열을 바꿀 수 있다.
>
> CSV의 `ranking검산차` 열은 위 식으로 다시 계산한 값과의 차이다.
> **0 근처가 아니면 파일 짝이 안 맞는다** (다른 실행의 파일이 섞였다).

### 8-5. `af3_collect.py` 로 표 만들기

출력 폴더 전체를 훑어 타깃별 지표를 CSV 한 장으로 모은다. 표준 라이브러리만 쓰므로
pandas가 없는 서버에서도 돌아간다 (python 3.8 이상).

```bash
# 기본
python3 scripts/af3_collect.py vhh_001_out -o vhh_001_결과요약.csv

# MSA 깊이 계산은 *_data.json 을 읽어야 해서 느리다. 필요 없으면 끈다
python3 scripts/af3_collect.py vhh_001_out --no-msa-depth -o 요약.csv

# 여러 폴더를 한 CSV 로 (조건 비교). 라벨=경로 형식
python3 scripts/af3_collect.py 축소=af3out_reduced 전체=af3out_full -o 비교.csv

# 상위 100건 골라내기 (2단계 전략의 재실행 목록)
python3 scripts/af3_collect.py vhh_001_out --top 100 --top-list top100.txt

# 순위 기준 열 바꾸기
python3 scripts/af3_collect.py vhh_001_out --top 100 --top-by pTM --top-list top100.txt

# 등급 기준 설명을 파일로 뽑기
python3 scripts/af3_collect.py --grade-doc
```

이 스크립트는 **읽기만 한다.** 출력 폴더의 어떤 파일도 수정·삭제하지 않는다.

CSV의 주요 열:

```
조건, 타깃, 등급, 경고, ranking_score, pTM, ipTM,
pLDDT평균, pLDDT중앙값, pLDDT최소, pLDDT_p10, pLDDT_70이상비율, pLDDT_90이상비율,
fraction_disordered, has_clash, MSA_unpaired깊이, MSA_paired깊이,
토큰수, 원자수, 체인수, 체인ID, 패딩버킷,
샘플수, ranking최고, ranking최저, ranking산포,
chain_pTM, chain_ipTM, min_chain_pair_ipTM, ranking검산차, 출력경로
```

실제 출력 예시는 [results_example/af3_summary.csv](results_example/af3_summary.csv) 에 있다
(축소 DB / 전체 DB 6종 비교, 실측).

### 8-6. 결과를 볼 때의 실무 순서

```
1. 등급 열로 정렬한다. D_낮음 은 일단 제외
2. 경고 열에 '충돌' 이 있는 건은 구조를 직접 열어 확인
3. 경고 열에 '샘플불안' 이 있는 건은 재현성이 낮으니 시드를 늘려 재실행
4. 남은 것을 pTM(또는 ipTM) 내림차순 + pLDDT평균 으로 정렬
5. 상위 수십 건만 구조를 실제로 눈으로 본다 (9절)
6. 그중에서 실험할 것을 고른다
```

'MSA얕음' 경고는 축소 DB를 썼으면 전량에 붙는다. 정상이다
([3-4](#3-4-데이터베이스-선택-단계-⑥-여기가-가장-중요한-판단이다) 참조).

![신뢰도 분포](figures/confidence_overview.png)

---

## 9. 결과 보기 (시각화)

숫자만으로는 무엇이 잘못됐는지 모른다. 구조를 실제로 봐야 한다.

### 9-1. `af3_visualize.py`: 그림 만들기

세부 옵션은 `--help` 로 확인하라.

```bash
python3 scripts/af3_visualize.py --help
```

기본 사용법. 타깃 하나의 pLDDT 프로파일과 PAE 히트맵을 만든다.

```bash
python3 scripts/af3_visualize.py vhh_001_out/vhh_A01 --out-dir figs
```

여러 타깃을 한꺼번에:

```bash
python3 scripts/af3_visualize.py vhh_001_out --out-dir figs
```

만들어지는 것:

| 그림 | 읽는 법 |
|------|---------|
| pLDDT 프로파일 (잔기별 꺾은선) | 낮게 파인 구간이 못 맞춘 부위다. VHH라면 CDR3 근처가 낮은 게 흔하다 |
| PAE 히트맵 (토큰 × 토큰) | 대각 블록은 도메인 내부, 대각 밖 블록이 밝으면 두 부위의 상대 배치를 확신하지 못한다 |

**PAE 히트맵 읽는 요령.** 복합체에서 사슬 A와 B에 해당하는 대각 밖 블록이 어두우면
(오차 작음) 두 사슬의 상대 위치를 확신한다는 뜻이다. 밝으면(오차 큼) 각 사슬은
잘 접혔지만 **어떻게 붙는지는 모른다**는 뜻이다. ipTM이 낮은 복합체는 대개 이 모양이다.

### 9-2. 구조를 실제로 보기

`*_model.cif` 를 뷰어에 넣는다. 세 가지 방법이 있다.

#### (a) 설치 없이: 웹 브라우저

가장 빠르다. 파일 몇 개만 볼 때 이걸 쓰면 된다.

- **RCSB PDB 3D 뷰어**: https://www.rcsb.org/3d-view (로컬 파일 열기로 `.cif` 를 넣는다)
- **Mol\* 뷰어**: https://molstar.org/viewer (`.cif` 드래그 앤 드롭)
- 두 뷰어 모두 mmCIF의 B-factor 자리에 들어간 값(AF3는 여기에 pLDDT를 쓴다)으로
  색칠하는 옵션이 있다

주의: 브라우저에 파일을 올리는 것이므로 **미공개 연구 서열이면 조직 정책을 확인하라.**
로컬에서만 처리하려면 (b) 나 (c)를 쓴다.

#### (b) PyMOL

```bash
pymol vhh_001_out/vhh_A01/vhh_A01_model.cif
```

pLDDT로 색칠한다. AF3는 mmCIF의 B-factor 자리에 pLDDT를 넣으므로 그걸 쓴다.

```python
# PyMOL 명령창에 그대로 붙인다
spectrum b, red_yellow_green_blue, minimum=50, maximum=90
show cartoon
hide lines
bg_color white
```

빨강이 낮고(50 이하) 파랑이 높다(90 이상). AlphaFold 공식 배색과 방향이 다르니
그림에 범례를 꼭 넣어라. AlphaFold 관례(파랑=높음, 주황=낮음)에 맞추려면:

```python
spectrum b, orange_yellow_cyan_blue, minimum=50, maximum=90
```

낮은 부위만 골라 보기:

```python
select lowconf, b < 70
show sticks, lowconf
color orange, lowconf
```

#### (c) ChimeraX

```bash
chimerax vhh_001_out/vhh_A01/vhh_A01_model.cif
```

```
# ChimeraX 명령줄에 붙인다
color bfactor palette alphafold
cartoon
set bgColor white
```

`palette alphafold` 가 AlphaFold 공식 pLDDT 배색을 그대로 적용한다. ChimeraX가 이 부분은 더 편하다.

낮은 부위 강조:

```
color bfactor palette alphafold
show :/bfactor<70 atoms
```

#### 여러 구조를 겹쳐 보기

같은 계열 나노바디 여러 개를 겹쳐서 CDR 루프만 다른지 확인할 때:

```python
# PyMOL
load vhh_001_out/vhh_A01/vhh_A01_model.cif, m1
load vhh_001_out/vhh_A02/vhh_A02_model.cif, m2
align m2, m1
```

```
# ChimeraX
open vhh_001_out/vhh_A01/vhh_A01_model.cif
open vhh_001_out/vhh_A02/vhh_A02_model.cif
matchmaker #2 to #1
```

`af3_visualize.py` 는 위와 같은 색칠·정렬 명령을 타깃 이름에 맞춰 생성해 주기도 한다
(`--help` 로 확인).

### 9-3. 무엇을 눈으로 확인해야 하나

| 볼 것 | 정상 | 이상 |
|-------|------|------|
| 전체 폴드 | 면역글로불린 β-샌드위치가 보인다 | 풀린 사슬, 뭉친 덩어리 |
| CDR 루프 | 루프 형태가 잡혀 있다 | 루프가 곧게 뻗어 나가 있다 (pLDDT도 낮을 것) |
| 사슬 말단 | 몇 잔기 흔들리는 것은 정상 | 긴 구간이 무질서 |
| 복합체 계면 | 두 사슬이 실제로 접촉해 있다 | 떨어져 있거나 엉뚱한 면끼리 붙어 있다 |
| 원자 충돌 | 없음 | `has_clash > 0` 이면 여기를 확인 |

복합체에서 계면이 이상하면 ipTM이 낮게 나와 있을 것이다. **숫자와 그림이 서로를
확인해 주는지** 보라. 어긋나면 파일이 섞였을 가능성을 의심하라
([8-4](#8-4-반드시-알아야-할-두-가지-함정) 의 `ranking검산차`).

---

## 10. 자주 만나는 문제

### AppleDouble 사이드카 (`._*.json`): 파이프라인이 죽는다

> **실제로 이 문제로 측정 3시간을 날렸다.** 이 저장소에서 가장 비싼 교훈이다.

**증상.** JSON을 읽다가 UTF-8 디코딩 오류나 JSON 파싱 오류로 죽는다. 파일을 `ls` 로
보면 아무 문제가 없어 보인다.

**원인.** macOS에서 만든 `tar.gz` 를 리눅스에서 풀면 `._` 로 시작하는 AppleDouble
사이드카 파일이 원본마다 하나씩 생긴다. `foo.json` 옆에 `._foo.json` 이 생긴다.
이 파일은 다음 성질을 갖는다.

- `ls` 에서는 눈에 잘 안 띈다 (이름이 원본과 비슷해서 섞여 보인다)
- **`glob('*.json')` 에는 잡힌다**
- 내용이 macOS 리소스 포크 바이너리라 **UTF-8이 아니다. 읽는 순간 죽는다**

**확인:**

```bash
find vhh_001_in -name '._*' | head
find vhh_001_in -name '._*' | wc -l
```

**해결:**

```bash
find vhh_001_in -name '._*' -delete
```

**예방:**

```bash
# macOS 에서 tar 를 만들 때
COPYFILE_DISABLE=1 tar czf inputs.tar.gz vhh_001_in

# 또는 리눅스에서 풀고 나서 항상 한 번
find . -name '._*' -delete
find . -name '.DS_Store' -delete
```

이 저장소의 스크립트(`af3_batch.py`, `af3_collect.py`)는 `._` 로 시작하는 파일을
전부 건너뛰도록 만들어져 있다. 그래도 AF3 본체가 읽으면 죽을 수 있으니 지워라.

### `nvidia-smi` 가 15GB를 쓴다고 나온다: VRAM 부족이 아니다

**증상.** `nvidia-smi` 에 15,157 MiB 가 사용 중으로 보인다. 16GB GPU에서 이걸 보면
"꽉 찼다"고 판단하게 된다.

**실제.** 이건 XLA가 **미리 선점(예약)한 양**이지 실제 수요가 아니다. 선점을 끄고 측정한
실제 피크는 23런 전체에서 **2,942~2,963 MiB** 였다 (A/B 벤치마크 18런이 2,942~2,954,
정렬효과 측정 5런이 2,953~2,963).

**대응.** 그냥 무시하라. 실제로 OOM이 나지 않으면 문제가 아니다.
실제 사용량을 보고 싶으면 선점을 끄고 실행한다 (`af3_batch.py --no-prealloc`).
단, 선점을 끄면 조금 느려진다.

### 진짜 OOM (CUDA out of memory)

선점 문제가 아니라 실제로 메모리가 부족한 경우다. VHH 단량체에서는 보기 어렵고,
큰 복합체나 긴 서열에서 발생한다.

**순서대로 시도하라.**

1. 토큰 수를 확인한다. 결과 CSV의 `토큰수` / `패딩버킷` 열을 보라.
   버킷이 1024 이상이면 메모리 요구가 급격히 커진다.
2. `--diffusion-samples` 를 줄인다 (5 → 1).
3. `--no-prealloc` 을 준다. 선점을 끄면 필요한 만큼만 잡는다.
4. `--unified-memory` 를 준다. **느려지지만 안 죽는다.** GPU 메모리가 넘치면
   시스템 RAM으로 넘긴다. 최후 수단.
5. 그래도 안 되면 그 타깃은 서열을 잘라 나누거나 포기한다.

```bash
python3 scripts/af3_batch.py --name big --stage infer --no-prealloc --unified-memory
```

### `docker` 를 쓸 때마다 `sudo` 를 쳐야 한다

**증상.** `docker: permission denied while trying to connect to the Docker daemon socket`

**해결.** 사용자를 `docker` 그룹에 넣는다. **다시 로그인해야 적용된다.**

```bash
sudo usermod -aG docker $USER
# 로그아웃 후 다시 로그인. 또는:
newgrp docker

# 확인
docker image ls
```

`sudo` 를 계속 쓰겠다면 스크립트에 알려주면 된다.

```bash
python3 scripts/af3_batch.py --name vhh_001 --docker 'sudo docker'
```

`sudo docker` 를 쓸 때는 컨테이너가 만든 결과 파일이 root 소유가 된다.
나중에 지우거나 옮길 때 권한 문제가 생기니 주의하라.

```bash
sudo chown -R $USER:$USER vhh_001_out
```

### 첫 실행이 너무 느리다 (5~8분)

**정상이다.** XLA가 GPU 커널을 컴파일하고 있다. 콜드/고부하 상태에서
프로세스 고정 오버헤드 **406~497초**까지 관측했다 (실측). 웜 상태에서는 6.55~8.5초다.

두 번째 실행부터 빨라진다. 컴파일 캐시 디렉터리를 지정하면 첫 컴파일을 재사용한다
(스크립트가 기본으로 한다). 자세히는 [7절](#7-왜-빠른가-선택-읽기).

**단, 2000건 배치에서는 캐시가 거의 의미 없다.** 첫 2건만 빨라지고 정상상태
4.20초는 캐시 유무와 무관하다.

### `--input_dir` 를 모른다는 오류 (구버전 이미지)

**증상.** `run_alphafold.py: error: unrecognized arguments: --input_dir`

**원인.** 도커 이미지가 오래된 AF3다. 이 저장소의 최적화는 `--input_dir` 로
한 프로세스가 전수 순회하는 것에 기반하므로, 이게 없으면 핵심 이득을 못 얻는다.

**해결.** 이미지를 다시 빌드한다.

```bash
cd ~/af3_work/alphafold3
git fetch --all
git checkout 97d20234c6eb89e8d05376e9eecc9321e60a559b
sudo docker build -t alphafold3 -f docker/Dockerfile .
```

**빌드가 불가능하면** 최소한 캐시 디렉터리 지정만이라도 해라. 그것만으로
31.95 → 18.13초 (1.76배)다. 단일 프로세스화(5.10배)는 포기해야 한다.

### 결과 CSV의 `패딩버킷` 이 256이다

**원인 (둘 중 하나).**

1. `--buckets` 에 128이 빠졌다. 토큰 128 이하인 짧은 VHH가 갈 곳을 잃고 256으로 밀려
   추론이 2.25배 느려진 것이다. CSV의 `토큰수` 열이 128 이하인데 `패딩버킷` 이 256이면 이 경우다.
2. 서열이 그냥 128 토큰보다 길다. 130 aa VHH는 정상적으로 256 버킷이다 (실측 6종에서
   130/131/135/138 aa 전부 256). 이건 고칠 수 없는 것이고, 짧은 건과 섞여 있으면
   두 버킷을 왕복하게 되는데 그 자체의 손해는 실측 0.00초/건이다.

**해결.** `--buckets` 를 직접 넘기지 마라. 스크립트 기본값에 128이 포함돼 있다.
직접 넘겨야 하면 128을 첫 항목으로 넣어라.
자세히는 [6-3](#6-3---buckets-에-128을-반드시-포함하라).

### `MSA얕음` 경고가 전량에 붙는다

축소 DB를 쓰면 정상이다. unpaired 깊이가 9~13 정도로 나온다 (전체 DB는 10,640~10,745).
**단량체 스크리닝에서는 신뢰도가 실질적으로 같으므로 무시해도 된다**
([3-4](#3-4-데이터베이스-선택-단계-⑥-여기가-가장-중요한-판단이다) 의 6종 비교).

복합체를 예측하면서 이 경고가 붙으면 그건 무시하면 안 된다. 전체 DB를 써라.

### MSA 단계에서 CPU를 늘렸는데 안 빨라진다

**정상이다.** 처리율은 총 요구 스레드가 코어 수의 약 1.3배인 지점에서
**0.895 타깃/분으로 포화**한다. 그 이상 늘리면 오히려 떨어진다 (96스레드 0.848).

`--msa-workers` 를 올리는 것도 손해다 (32스레드 1갈래 0.890 대 2갈래 0.767).
[6-4](#6-4-2단계-전략-msa-먼저-추론-나중) 참조.

포화의 원인이 CPU 경합인지 디스크 I/O인지는 **미규명**이다. NVMe SSD에 DB를 두면
나아질 가능성이 있으나 측정하지 않았다.

### `jackhmmer -h` 에 `--seq_limit` 이 없다

AF3가 패치한 HMMER가 아니다. 도커 이미지를 쓰면 보통 문제되지 않는다.
conda 네이티브 설치라면 AF3 저장소의 HMMER 빌드 절차를 다시 확인하라
([docs/install_log.md](docs/install_log.md)).

### AF3 설치 시 python 3.11이 거부된다

최신 AF3는 `requires-python >=3.12` 다. 3.12로 환경을 만들어라.

```bash
conda create -y -n af3 python=3.12
```

### `/usr/include/zlib.h` 없음 (sudo 불가한 서버)

시스템 zlib 개발 헤더가 없는데 sudo도 없는 경우다. conda로 넣고 cmake가
거기를 보게 만든다.

```bash
conda install -y -c conda-forge zlib cmake
export CMAKE_PREFIX_PATH=$CONDA_PREFIX
```

### 중간에 끊겼는데 어디까지 됐는지 모른다

```bash
cat vhh_001_work/state.json | python3 -m json.tool | head -40
ls vhh_001_out | wc -l
```

같은 명령을 다시 실행하면 끝난 것은 건너뛴다.
[6-6](#6-6-중간에-끊겼을-때) 참조.

---

## 11. 라이선스와 인용

### 라이선스가 세 갈래로 나뉜다

| 대상 | 라이선스 | 무엇이 되나 |
|------|----------|-------------|
| **이 저장소의 스크립트·문서** | Apache 2.0 ([LICENSE](LICENSE)) | 자유롭게 쓰고 고치고 재배포 가능 |
| **AF3 소스 코드** | Apache 2.0 (별도 저장소) | 같음 |
| **AF3 모델 가중치** | **비영리 한정. 재배포 금지** | 아래 참조 |

**세 개는 서로 다르다.** 이 저장소가 Apache 2.0이라는 것이 AF3 가중치를 자유롭게
쓸 수 있다는 뜻이 절대 아니다.

### AF3 가중치 제약: 반드시 지켜야 한다

- **비영리 목적으로만** 쓸 수 있다.
- **재배포 금지.** 남에게 파일을 주면 안 된다. 클라우드 공유 폴더, 사내 스토리지,
  깃 저장소, 어디에도 올리면 안 된다.
- **출력물로 유사 구조예측 모델을 학습시키는 것이 금지**돼 있다.
- 약관은 **"구글로부터 직접 받은 경우"만 사용을 허용한다.** 동료에게 복사해 받으면
  약관 위반이다. **공식 접근 요청 절차를 밟아 승인 기록을 남겨 두라.**
  나중에 논문 심사나 기관 감사에서 근거가 된다.

정확한 조건은 Google DeepMind가 배포하는 약관 원문을 직접 확인하라. 위 요약은
편의를 위한 것이고 법적 효력은 원문에 있다. 더 자세한 정리는
[docs/license_notes.md](docs/license_notes.md).

### 인용 의무

AF3를 써서 결과를 발표하면 다음을 인용해야 한다.

> Abramson, J., Adler, J., Dunger, J. et al. Accurate structure prediction of
> biomolecular interactions with AlphaFold 3. *Nature* **630**, 493–500 (2024).

이 저장소를 인용할 필요는 없다. 필요하면 저장소 URL을 각주로 넣으면 된다.

### 저장소에 올리면 안 되는 것

> ### 경고. 이 저장소는 공개(public)다
>
> 아래를 커밋하면 안 된다.
>
> | 무엇 | 왜 |
> |------|-----|
> | `af3.bin`, `af3.bin.zst` (가중치) | **재배포 금지. 약관 위반이다** |
> | `ccd.pickle` (543MB) | 용량. `build_data` 로 각자 만들면 된다 |
> | `public_databases/` 내용 (최대 850GB) | 용량. 공개 URL에서 각자 받는다 |
> | `*_out/` (결과) | 용량. 그리고 서열이 역추적될 수 있다 |
> | **실제 연구 서열 (FASTA/CSV/JSON)** | **미공개 연구 데이터다. 지우기도 어렵다** |
>
> `.gitignore` 가 위를 모두 막도록 만들어져 있다. 하지만 `.gitignore` 는
> **이미 추적 중인 파일은 막지 못한다.** 커밋 전에 항상 확인하라.
>
> ```bash
> git status
> git diff --cached --stat        # 무엇이 스테이징됐는지
> du -sh .git                     # .git 이 갑자기 커졌으면 뭔가 들어간 것이다
> ```
>
> 실수로 커밋했다면 **push 전이면** 되돌릴 수 있다.
>
> ```bash
> git reset HEAD~1               # 마지막 커밋 취소 (파일은 남는다)
> ```
>
> **이미 push 했다면 히스토리를 다시 써야 한다.** 그냥 지우는 커밋을 올려도
> 히스토리에 파일이 남는다. 그 경우 저장소를 비공개로 바꾸고 조치를 상의하라.
>
> `examples/` 의 서열은 공개 PDB 유래의 예시이며 실제 연구 서열이 아니다.

---

## 12. 측정 조건과 한계

이 저장소의 수치를 인용하거나 자기 환경에 적용할 때 필요한 정보다.

### 측정 환경

| 항목 | 값 |
|------|-----|
| 호스트 | gpu-5070ti |
| GPU | RTX 5070 Ti 16GB, Blackwell sm_120 |
| CPU | 24 코어 |
| RAM | 126GB |
| 설치 방식 | **conda 네이티브** (이 호스트에 Docker가 없었다) |
| AF3 | commit `97d20234c6eb89e8d05376e9eecc9321e60a559b` |

**연구자 환경과 다른 점.** 연구자는 RTX 5090 32GB에 Docker로 돌린다.

- GPU가 더 크지만 실제 VRAM 피크가 3GB이므로 **속도 차이는 연산 성능 차이만큼**이다.
- 우리는 Docker 기동 비용이 빠져 있으므로 **우리 값은 Docker 환경의 하한**이다.
  Docker에서는 건당 시간이 같거나 조금 더 나온다. 즉 개선 배수는 우리가 보고한 것보다
  **크게 나올 가능성이 있다** (작아지지는 않는다).

### 각 수치의 측정 조건

| 수치 | 조건 |
|------|------|
| 31.95 / 18.13 / 6.26 / 5.39초/건 | 32건 × 3반복 중앙값. **MSA 없는 GPU 추론 경로만.** 웜 캐시 |
| 4.20초 (정상상태), 5.59초/건 | 96건 단일 프로세스 순회. 버킷 128 |
| 9.44초 (버킷 256 정상상태) | 같은 조건, 버킷만 256 |
| VRAM 2,942~2,963 MiB | 선점 OFF, **23런** (A/B 18런 2,942~2,954 + 정렬효과 5런 2,953~2,963). VHH 116~144 aa, sample 5 × recycle 10 |
| MSA 0.895 타깃/분 | 14조합 스윕. 전체 DB의 4종 각 4GB 슬라이스 |
| 축소 대 전체 DB 43.3초 대 1,830초 | 6건 × 1회 end-to-end. 축소 DB + MSA + 추론(sample 5/recycle 10) |
| DB 다운로드 1시간 37분 | 4병렬, 평균 약 41MB/s 회선. **회선 속도에 전적으로 의존한다** |
| 신뢰도 비교 6종 | 전부 **단량체** VHH. PDB 유래 (7djx, 7a50, 8v8k, 4qgy, 4s11, 7mfv) |
| 연구자 현재 341초/건 | **연구자 보고값** (3일에 760건). 우리 측정이 아니다 |

### 측정하지 않은 것: 정직하게

아래는 이 저장소가 답하지 못하는 것들이다. 추론으로 채우지 않았다.

1. **복합체 계면에 대한 DB 크기 영향 (ipTM).** 비교한 6종이 모두 단량체여서
   ipTM이 산출되지 않았다. paired MSA가 120~150배 차이나므로 복합체에는 전체 DB가
   사실상 필수로 보이지만 **이것은 추론이다.** 항원-나노바디 복합체로 직접 확인해야 한다.
2. **CDR3 등 가변 루프의 잔기별 민감도.** 우리가 비교한 것은 원자 pLDDT의 **평균**이고,
   그 값은 잔기 수가 많은 프레임워크가 지배한다. CDR 루프만 떼어 보면 축소 DB와
   전체 DB의 차이가 더 클 수 있다. **분해하지 않았다.**
3. **MSA 처리율 포화의 원인.** CPU 경합인지 디스크 I/O인지 구분하지 못했다.
   DB를 NVMe에 두면 나아질 가능성이 있으나 측정하지 않았다.
4. **Docker 오버헤드의 실제 크기.** 검증 호스트에 Docker가 없었다.
   도커 이미지 빌드 시간(20~40분)도 추정값이다.
5. **긴 서열 / 큰 복합체의 토큰 상한.** VHH 116~144 aa (버킷 128과 256) 범위만 측정했다.
   버킷 1024 이상에서의 시간·VRAM은 측정하지 않았다. 큰 복합체는 별도로 벤치마크하라
   (`af3run.sh <이름> bench` 로 20건만).
6. **샘플링 순위 보존.** `--diffusion-samples 1` 로 스크리닝한 순위가
   `--diffusion-samples 5` 순위와 얼마나 일치하는지 측정하지 않았다.
   즉 **경량 스크리닝으로 고른 상위 100건이 정밀 계산의 상위 100건과 같다는 보장이 없다.**
   2단계 전략의 가장 큰 미검증 가정이다.
7. **축소 DB의 정체.** 우리가 쓴 축소 DB는 연구자의 실제 파일이 아니라
   공식 전체 DB에서 균등 추출해 만든 **대리 세트**다. MSA 깊이 절대값은 다를 수 있다.
8. **RAM 하한.** 126GB 호스트에서만 측정했다. 축소 DB로 얼마나 적은 RAM에서
   돌아가는지 모른다.
9. **build_data 소요 시간.** 생성물 크기(543MB)는 측정했으나 시간은 기록하지 않았다.

### 자기 환경에 적용하는 법

우리 수치를 그대로 믿지 말고 20건으로 직접 재라. 그게 가장 정확하다.

```bash
bash scripts/af3run.sh vhh_001 bench
```

건당 시간이 나오면 × 2000 하면 된다. 그 값이 우리 표(GPU 추론 5.39초 + 데이터 파이프라인
축소 DB 1.98초 / 전체 DB 급 4GB 슬라이스 30.41초)와 크게 다르면,
다른 쪽이 아니라 **당신 환경의 값이 맞다.**

---

## 문서 목록

| 문서 | 내용 |
|------|------|
| [docs/operations_guide.md](docs/operations_guide.md) | 운영 가이드: 설정·실행·모니터링·트러블슈팅 전체 |
| [docs/diagnosis_report.md](docs/diagnosis_report.md) | 진단 리포트: 건당 시간이 어느 단계에 얼마나 갔는지 |
| [docs/benchmark_report.md](docs/benchmark_report.md) | A/B 벤치마크 리포트: 현재 방식 대 최적화 방식 |
| [docs/commands.md](docs/commands.md) | 복사해 붙이는 단일 명령 모음 |
| [docs/install_log.md](docs/install_log.md) | 설치 실측 기록: 시간, 버전, 만난 문제와 해결 |
| [docs/db_notes.md](docs/db_notes.md) | DB 다운로드·해제 상세 기록과 무결성 검증 |
| [docs/reduced_db.md](docs/reduced_db.md) | 축소 DB 구성과 대리 세트 제작 방법 |
| [docs/benchmark_notes.md](docs/benchmark_notes.md) | 벤치마크 원자료 기록 |
| [docs/testing_notes.md](docs/testing_notes.md) | 회귀 테스트: 각 테스트가 막는 버그, docker 스텁 근거, 역검증 결과 |
| [docs/license_notes.md](docs/license_notes.md) | 라이선스 정리 |

## 스크립트를 고칠 때: 회귀 테스트

스크립트를 수정했다면 아래 한 줄로 옛 버그가 되살아나지 않았는지 확인한다.
Docker 도, `pip install` 도 필요 없다.

```bash
python3 tests/run_tests.py
```

각 테스트에는 "이 테스트가 막는 실제 버그" 가 한 줄로 붙어 있다
(`python3 tests/run_tests.py --list`). 실패하면 그 문장을 먼저 읽어라. 자세한 것은
[docs/testing_notes.md](docs/testing_notes.md).

## 예시 데이터

| 경로 | 내용 |
|------|------|
| [examples/](examples/) | 예제 FASTA/CSV, 예제 입력 JSON (단량체·복합체) |
| [results_example/](results_example/) | 실측 결과 CSV: 신뢰도 요약, A/B 벤치마크, 2000건 환산, MSA 스윕 |
| [figures/](figures/) | README에 쓰인 그림 |

---

문서·스크립트에 대한 문의와 오류 보고는 이 저장소의 Issues 로.
AF3 본체 문제는 https://github.com/google-deepmind/alphafold3 로.

