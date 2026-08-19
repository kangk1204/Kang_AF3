> 이 문서는 저장소 `docs/install_log.md` 다. 한글 제목은 "설치 실측 기록".
> 저장소 최상위 [README.md](../README.md) 의 안내를 먼저 읽고 이 문서로 오는 것을 권한다.
> 문서 안에서 언급하는 그림은 `figures/`, 측정 CSV 는 `results_example/`,
> 스크립트는 `scripts/` 에 있다 (모두 저장소 최상위 기준 경로).

# AlphaFold 3 설치 기록 — ssh:gpu-5070ti (검증 호스트)

작성 목적: 이 호스트에서 AF3 추론이 확실히 돈다는 상태를 만들고, 그 절차를 그대로
재현할 수 있게 남긴다. Phase 1 A/B 실측이 이 기준점 위에서 돌아간다.

측정일: 2026-08-18. 모든 시간·용량 값은 이 호스트에서 실제로 측정한 값이며,
추정치는 "추정"이라고 따로 적었다.

---

## 0. 결론 먼저

- **설치 방식: conda 네이티브** (Docker 아님). 이유는 1절.
- **동작 확인된 조합**: python 3.12.13 / jax 0.10.2 / jaxlib 0.10.2 /
  jax-cuda12-plugin 0.10.2 / 번들 CUDA 12.9 / cuDNN 9.24.0.43 /
  AF3 commit `97d20234c6eb89e8d05376e9eecc9321e60a559b` (v3.0.4-15-g97d2023)
- **sm_120(Blackwell) GPU 연산 통과**: JAX backend=gpu, jit 컴파일·행렬연산·bfloat16 모두 정상.
  PTX 버전 오류 없음. flash attention은 기본값 `triton` 이 그대로 동작했다.
- **추론 스모크 테스트 성공**: VHH 3건 단일 프로세스 완주, mmCIF 18개 생성.
- **핵심 실측**: 재컴파일 회피가 확인됐다. 단일 프로세스에서 같은 패딩 버킷(128) 4건을
  연속 처리한 대조 실험(6.3절) 기준으로 추론 시간이 **26.12 → 21.64 → 4.22 → 4.21초**로
  떨어졌다. 컴파일 비용은 **첫 두 건에 걸쳐 상환**되고 **3건째부터 정상 상태(약 4.2초)**에
  진입한다. 즉 2건째는 아직 1건째의 83% 수준이고, **정상 상태가 1건째의 16%**(6.2배 차이)다.
  (초기 스모크 실행에서는 2건째가 1건째의 22%로 나왔으나(31.92 → 7.06초), 그것은 호스트
  고부하 중 3건만 돌린 단발 측정이었다. 버킷당 4건으로 통제한 6.3절 수치를 정본으로 쓴다.)

---

## 1. 호스트 사전 점검 — Docker를 못 쓴 이유

```
Ubuntu 25.10, kernel 6.17.0-41-generic, x86_64
GPU  : NVIDIA GeForce RTX 5070 Ti, 16303 MiB, compute_cap 12.0, driver 595.71.05
CPU  : 24 코어 / RAM 123 GB / 디스크 여유 7.0 TB (/)
```

빌드 도구 확인 결과:

| 도구 | 상태 |
|---|---|
| gcc, g++, make, zstd, wget, curl, git | 있음 (/usr/bin) |
| cmake, aria2c | **없음** |
| zlib 개발 헤더(`/usr/include/zlib.h`) | **없음** |
| **docker** | **없음** (`command -v docker` 실패) |
| sudo | **비밀번호 필요** (`sudo -n true` 실패) |

연구자는 Docker(`sudo docker run --rm --gpus all ...`)로 돌리고 있어서 재현성 면에서는
Docker가 나았을 것이다. 그러나 이 호스트는 **docker 바이너리 자체가 없고**, 설치하려면
`sudo apt-get` 이 필요한데 **sudo가 비밀번호를 요구**한다. 비대화형 SSH 작업에서는
진행할 수 없으므로 **conda 네이티브 설치**를 선택했다.

### 이 선택이 벤치마크 해석에 주는 차이 (중요)

Docker 실행과 네이티브 실행의 차이를 벤치마크에 반영해야 한다.

1. **컨테이너 기동 비용이 빠진다.** 연구자의 건당 5.7분에는 `docker run` 컨테이너
   생성·이미지 레이어 마운트·컨테이너 정리 비용이 포함되는데, 네이티브 측정에는 없다.
   즉 **네이티브로 측정한 고정 오버헤드는 연구자가 실제로 지불하는 값의 하한**이다.
   개선 배수를 논할 때 이 방향(연구자 쪽이 더 나쁨)을 명시해야 한다.
2. **CUDA 스택이 다르다.** 공식 Dockerfile은 `nvidia/cuda:12.6.3-base-ubuntu24.04`
   기반이고 호스트에 CUDA 12.6을 요구한다. 우리는 pip wheel로 번들된 **CUDA 12.9**를 쓴다.
   두 경우 모두 JAX는 자기 wheel의 CUDA 런타임을 쓰므로 커널 컴파일 경로는 유사하지만,
   절대 시간은 완전히 동일하지 않을 수 있다.
3. **XLA 환경변수는 동일하게 맞췄다.** 공식 Dockerfile의
   `XLA_FLAGS=--xla_gpu_enable_triton_gemm=false` 를 그대로 적용했다.
   단 메모리 관련 값은 A100 80GB 기준(`XLA_CLIENT_MEM_FRACTION=0.95`)이라
   16GB 카드에서는 0.90으로 낮췄다.

기존 conda 환경(crbnmd / plcuration / esmdl 등)은 **건드리지 않았다.** 새 환경 `af3` 만 만들었다.

---

## 2. af3 환경 및 JAX GPU 검증

### 계획과 달라진 점: python 3.11 → 3.12

계획서는 python 3.11을 지정했지만, AF3 최신 소스의 `pyproject.toml` 이
`requires-python = ">=3.12"` 로 올라가 있었다. 3.11로는 설치가 되지 않으므로 **3.12로 상향**했다.
(공식 Dockerfile도 `python3.12` 를 설치한다.)

```bash
# zlib 헤더가 시스템에 없으므로 conda 쪽 zlib 을 쓴다. cmake 도 여기서 확보.
~/miniforge3/bin/mamba create -y -n af3 -c conda-forge     python=3.12 zlib cmake ninja pkg-config

# AF3 pyproject 가 핀한 그대로 설치. 번들 CUDA wheel 경로(cuda12)를 쓴다.
uv pip install --python ~/miniforge3/envs/af3/bin/python "jax[cuda12]==0.10.2" numpy
```

시스템에 nvcc/CUDA 툴킷이 없으므로 `jax[cuda12]` 의 **번들 CUDA wheel** 경로가 필수다.
계획서에서 우려한 PTX 버전 오류(`CUDA_ERROR_UNSUPPORTED_PTX_VERSION`)는 **발생하지 않았다.**
드라이버 595.71.05 가 CUDA 13.2까지 지원하고 wheel이 CUDA 12.9를 쓰므로 여유가 있다.
호스트 노트에 기록된 crbnmd 사례(cuda-version이 13.3까지 올라가 PTX 실패)는
conda 채널 패키지를 올릴 때 생긴 문제였고, 여기서는 pip wheel로 CUDA를 고정하므로 무관하다.

### 실제로 설치된 버전 (전량 기록)

```
jax                      0.10.2      nvidia-cublas-cu12       12.9.2.10
jax-cuda12-pjrt          0.10.2      nvidia-cuda-cupti-cu12   12.9.79
jax-cuda12-plugin        0.10.2      nvidia-cuda-nvcc-cu12    12.9.86
jaxlib                   0.10.2      nvidia-cuda-nvrtc-cu12   12.9.86
numpy                    2.5.2       nvidia-cuda-runtime-cu12 12.9.79
                                     nvidia-cudnn-cu12        9.24.0.43
                                     nvidia-cufft-cu12        11.4.1.4
                                     nvidia-cusolver-cu12     11.7.5.82
                                     nvidia-cusparse-cu12     12.5.10.65
                                     nvidia-nccl-cu12         2.31.2
                                     nvidia-nvjitlink-cu12    12.9.86
                                     nvidia-nvshmem-cu12      3.7.2
```

### GPU 최소 예제 검증 결과 (AF3 코드 건드리기 전에 먼저 확인)

```json
{
  "backend": "gpu",
  "devices": ["gpu:NVIDIA GeForce RTX 5070 Ti:0"],
  "matmul_first_call_s": 2.37,      // jit 첫 호출(컴파일 포함)
  "matmul_second_call_s": 0.0008,   // 컴파일된 커널 재사용
  "matmul_rel_err_vs_cpu": 3.05e-4, // CPU numpy 대조 (float32 누적오차 범위)
  "bf16_softmax_first_s": 0.492,    // AF3가 쓰는 bfloat16 경로도 통과
  "gpu_verified": true
}
```

2048x2048 matmul의 jit 첫 호출과 두 번째 호출이 **2.37초 → 0.0008초**로 떨어진다.
이것이 이 트랙 전체에서 문제 삼는 "컴파일 캐시 적중"의 최소 재현 사례다.

---

## 3. AF3 설치 및 가중치

### 소스

```
repo   : https://github.com/google-deepmind/alphafold3.git
commit : 97d20234c6eb89e8d05376e9eecc9321e60a559b
tag    : v3.0.4-15-g97d2023
date   : 2026-08-14
```

### HMMER 3.4 — 공식 Dockerfile 절차를 그대로 따랐다

공식 문서는 `apt-get install hmmer` 를 쓰지 말고 소스 빌드하라고 한다. 이유는 두 가지다:
버전을 고정해야 하고, **AF3가 jackhmmer에 `--seq_limit` 패치를 적용**하기 때문이다.

```bash
wget http://eddylab.org/software/hmmer/hmmer-3.4.tar.gz   # eddylab.org 는 HTTPS 미지원
echo "ca70d94fd0cf271bd7063423aabb116d42de533117343a9b27a65c17ff06fbf3  hmmer-3.4.tar.gz" \
  | sha256sum --check                                      # -> OK
tar zxf hmmer-3.4.tar.gz
patch -p0 < ~/af3_work/alphafold3/docker/jackhmmer_seq_limit.patch
cd hmmer-3.4
./configure --prefix ~/miniforge3/envs/af3     # 바이너리를 af3 env prefix 에 넣는다
make -j 12 && make install
(cd easel && make install)                     # esl-sfetch 등 easel 도구도 필요
```

검증 (전부 통과):

```
jackhmmer / nhmmer / hmmbuild / hmmsearch / hmmalign  : HMMER 3.4 (Aug 2023)
esl-sfetch                                            : Easel 0.49 (Aug 2023)
패치 확인: jackhmmer -h 에 "--seq_limit <n> : if set, truncate all hits after
           this value is reached" 항목이 존재
```

### AF3 패키지 + build_data

```bash
export CMAKE_PREFIX_PATH=~/miniforge3/envs/af3   # zlib 을 conda env 에서 찾게 한다
cd ~/af3_work/alphafold3
uv pip install --python ~/miniforge3/envs/af3/bin/python .
~/miniforge3/envs/af3/bin/build_data
```

`build_data` 는 화학 성분 사전을 pickle로 굽는다. 이건 설치 시 1회만 하면 된다.

```
ccd.pickle                     542,994,372 B   (50,942 화학 성분)
chemical_component_sets.pickle       8,424 B
```

주요 의존 버전: rdkit 2025.9.4, tokamax 0.0.12, dm-haiku 0.0.17, zstandard 0.25.0.

### 가중치

```
URL    : https://storage.googleapis.com/alphafold3/af3.bin.zst
af3.bin.zst : 1,020,545,840 B
              sha256 74d0258616917cd122f5eab6d076afe4a8930e96823851e65e4f777dfb1f33ff
af3.bin     : 1,146,811,260 B
              sha256 df8bbf2621f17dd3ee21c2a921e84a50bc2b80cdc0c7971cb915c2826fee1f9b
파라미터 수 : 368,384,602 (실측, haiku params 합)
배치        : ~/af3_models/af3.bin   (연구자 경로 관례와 동일)
```

라이선스 조건은 별도 문서 `docs/license_notes.md` 로 정리했다. **비상업 연구 용도 한정이며
가중치 재배포는 금지**다. 사용 전 반드시 확인할 것.

---

## 4. 연구자 환경 재현 — 축소 DB

연구자는 공식 전체 630GB DB가 아니라 **~2GB 축소 DB**를 쓴다. 대조군을 같은 조건으로 맞췄다.

전체 DB는 다른 트랙이 병행 다운로드 중이라(`~/public_databases_full`) 기다리지 않고,
**공식 v3.0 DB의 실제 앞부분 슬라이스**로 `~/public_databases` (연구자 경로 관례)를 구성했다.
무작위 생성 서열은 쓰지 않았다 — MSA가 비면 실측이 왜곡되기 때문이다.
슬라이스 시 마지막 불완전 FASTA 레코드는 잘라내 파일 정합성을 유지했다.

| 파일 | 크기 | 서열 수 |
|---|---|---|
| uniref90_2022_05.fa | 520.0 MB | 71,974 |
| bfd-first_non_consensus_sequences.fasta | 420.0 MB | 3,242,672 |
| mgy_clusters_2022_05.fa | 420.0 MB | 1,886,706 |
| uniprot_all_2021_04.fa | 320.0 MB | 633,249 |
| rfam_14_9_...fasta | 228.4 MB | 871,599 (전체) |
| nt_rna_2023_...fasta | 60.0 MB | 27,830 |
| rnacentral_active_...fasta | 59.9 MB | 67,358 |
| **합계** | **2.0 GB** | |

mmCIF 템플릿은 RCSB에서 **실제 1,239건**을 받아 배치했고(나노바디/VHH/단일도메인항체/
Camelidae/면역글로불린 가변도메인 검색식의 합집합), pdb_seqres 는 그 엔트리에 대응하는
체인 3,531건만 남겨 정합을 맞췄다. 자세한 내용과 z_value 사용법은 `bench_inputs/docs/reduced_db.md`.

**축소 DB는 z_value 를 반드시 넘겨야 한다.** AF3의 jackhmmer/nhmmer e-value 계산은
DB 크기에 의존하므로, 잘린 DB에 기본 z_value를 쓰면 e-value가 왜곡된다.
`~/public_databases/reduced_db_stats.json` 에 산출값을 저장해 뒀다
(단백질 DB는 서열 개수, RNA DB는 100만 염기 단위).

```
--small_bfd_z_value=3242672     --uniref90_z_value=71974
--mgnify_z_value=1886706        --uniprot_cluster_annot_z_value=633249
--ntrna_z_value=56.889873       --rnacentral_z_value=57.372623
--rfam_z_value=138.115553
```

### 테스트 서열 세트

`bench_inputs/` 참조. UniProtKB에서 받은 **실제 나노바디/VHH 서열 96건** (111–144 aa).
출처·검색식·길이 분포는 `bench_inputs/README.md` 에 적었다.

**Phase 1에 중요한 사실**: AF3 기본 패딩 버킷은
`128, 256, 384, 512, 768, 1024, ...` 다. 우리 세트(111–144 aa)는 **버킷 128과 256 두 개**에만
걸린다. 즉 길이순 정렬 시 재컴파일은 **최대 2회**로 억제된다.
2000건 배치도 130 aa 내외라면 마찬가지로 버킷 2개 수준일 것이다(추정).

---

## 5. 추론 경로 스모크 테스트 — 설치 검증

GPU 추론 시간만 격리하려고 `--norun_data_pipeline` 으로 돌렸다
(입력 JSON의 `unpairedMsa`/`pairedMsa` 를 빈 문자열, `templates` 를 빈 배열로 명시).
MSA 단계 시간은 여기 포함되지 않는다.

실행 설정: `num_diffusion_samples=5`, `num_recycles=10` (AF3 기본값),
`flash_attention_implementation=triton`, `XLA_FLAGS=--xla_gpu_enable_triton_gemm=false`.

**결과: 성공.** VHH 3건 단일 프로세스 완주, mmCIF 18개 + summary_confidences 18개 생성.
`triton` flash attention이 기본값 그대로 sm_120에서 동작했다 (cudnn/xla 대체 불필요).

타깃별 top-ranked 신뢰도:

| 타깃 | ranking_score | pTM | fraction_disordered | has_clash |
|---|---|---|---|---|
| vhh_001_A0A1W5VKQ0 | 0.68 | 0.65 | 0.07 | 0 |
| vhh_002_A0A1W5VKS5 | 0.48 | 0.48 | 0.00 | 0 |
| vhh_096_A0A411PB57 | 0.84 | 0.84 | 0.00 | 0 |

단일 사슬이라 ipTM은 null이다(정상 — 사슬 간 지표). MSA 없이 돌린 값이므로
**구조 품질 평가용이 아니다.** 파이프라인이 끝까지 돌고 신뢰도 지표가 정상 범위로
나온다는 확인용이다.

---

## 6. 계측 결과 — Phase 1 기준점

### 측정 조건에 대한 경고 (먼저 읽을 것)

측정 기간 동안 **같은 호스트에서 다른 트랙이 전체 DB(수십 GB)를 다운로드·압축해제**했다.
load average가 최대 30을 넘었다. 그래서 이 절의 값은 두 종류로 나눠 읽어야 한다.

- **부하에 민감한 값**: 고정 오버헤드, wall time → 범위로 제시했다
- **부하에 강건한 값**: 같은 프로세스 안에서의 상대 비교 → 결론으로 쓸 수 있다

각 측정에 load1을 함께 기록했다.

### 6.1 프로세스 1회당 고정 오버헤드

연구자 스크립트가 타깃마다(2000번) 다시 지불하는 비용이다. 단계별로 분해했다.

| 단계 | 콜드/고부하 | 페이지 캐시 따뜻할 때 |
|---|---|---|
| `import jax` | 85–119 s | 0.3 s |
| `import haiku` | 27–44 s | 0.08 s |
| `import alphafold3` | 124–188 s | 0.6 s |
| CCD pickle 로딩 (543 MB) | 12–17 s | 2.8 s |
| JAX GPU 디바이스 초기화 | 53–132 s | 0.3 s |
| 가중치 로딩 (af3.bin 1.15 GB) | 19–30 s | 3.2 s |
| **합계** | **406–497 s** | **6.6–8.5 s** |

**해석**: 콜드/고부하에서 400~500초, 페이지 캐시가 따뜻하면 7~9초.
연구자 실제 값은 이 사이일 것이다(추정) — `docker run` 마다 컨테이너가 새로 뜨지만
호스트 페이지 캐시는 유지되므로 낮은 쪽에 가깝고, 여기에 컨테이너 기동·이미지
레이어 마운트 비용이 더해진다.

**고정 오버헤드를 단일 숫자로 단정하지 말 것.** Phase 1은 같은 호스트·같은 부하에서
두 방식을 나란히 재는 방식으로 가야 한다.

### 6.2 VRAM — 연구자의 전제가 깨지는 지점

| 조건 | 최대 VRAM |
|---|---|
| `XLA_PYTHON_CLIENT_PREALLOCATE=true` (공식 Dockerfile 기본) | 15,157 MiB / 16,303 MiB (93%) |
| **선점 OFF (실제 요구량)** | **5,291 MiB (32%)** |
| 선점 OFF + 경량 설정 (sample 1, recycle 3) | 5,287 MiB |

**연구자가 "GPU 메모리가 거의 꽉 차서 한 번에 하나밖에 못 돌린다"고 판단한 근거가
여기서 무너진다.** 15 GB가 차 보이는 것은 XLA가 **미리 선점**한 양이고, 130 aa 급
VHH 추론의 실제 요구량은 **약 5.3 GB**다.

- 연구자 카드는 RTX 5090 **32 GB** 다. 5.3 GB × 2~4 프로세스가 물리적으로 들어간다.
- 다만 동시 실행이 실제로 이득인지는 별개 문제다. 단일 GPU에서 여러 프로세스를
  띄우면 SM을 나눠 쓰므로 각 프로세스가 느려지고, 프로세스마다 고정 오버헤드를
  다시 낸다. **먼저 단일 프로세스 순회로 오버헤드를 없애는 것이 우선순위**다.
- 경량 설정에서도 VRAM이 거의 같다 → **VRAM은 diffusion sample 수에 크게 좌우되지 않는다.**
  즉 경량 설정은 시간을 줄이는 수단이지 메모리를 줄이는 수단이 아니다.

### 6.3 패딩 버킷과 재컴파일 — 길이순 정렬의 직접 증거

AF3 기본 버킷: `128, 256, 384, 512, 768, 1024, ...`
VHH 130 aa 급은 **버킷 128과 256 두 개**에만 걸린다.

단일 프로세스로 버킷128 4건 → 버킷256 2건을 순서대로 돌린 실측:

| 순서 | 길이 | 버킷 | 추론 시간 |
|---|---|---|---|
| 1 | 111 aa | 128 | 26.12 s |
| 2 | 111 aa | 128 | 21.64 s |
| 3 | 112 aa | 128 | **4.22 s** |
| 4 | 113 aa | 128 | **4.21 s** |
| 5 | 129 aa | 256 | 28.53 s ← 버킷 전환 |
| 6 | 131 aa | 256 | 9.44 s |

**정상 상태 추론 시간은 약 4.2초**이고, 첫 타깃은 26.1초다 (**6.2배 차이**).
컴파일 비용이 첫 두 건에 걸쳐 상환되고 3건째부터 정상 상태에 진입한다.
버킷이 바뀌는 5건째에 28.5초로 다시 튀는 것이 **길이순 정렬이 필요한 이유의 직접 증거**다.

정렬 없이 길이가 섞여 들어오면 버킷 128↔256을 왕복하며 이 25초짜리 비용을 반복한다.

### 6.4 지속 컴파일 캐시 — 사전 진단과 다른 결과가 나왔다 (중요)

사전 진단은 "프로세스가 다르면 `--jax_compilation_cache_dir` 이 **적중하지 않는다**"였다
(AF3 이슈 #468 기준). **이 스택에서는 그렇지 않았다.** 대조 실험으로 확인했다.

같은 입력 1건을 **별개 프로세스로** 반복 실행한 추론 시간:

| 조건 | 1회 | 2회 | 3회 | 평균 |
|---|---|---|---|---|
| C: 캐시 플래그 없음 | 31.40 s | 35.37 s | 32.86 s | **33.21 s** |
| D: 캐시 디렉터리를 매번 삭제 | 33.80 s | 27.24 s | 26.16 s | **29.07 s** |
| E: 캐시 디렉터리 유지 | 7.99 s | 8.01 s | — | **8.00 s** |

**지속 캐시가 프로세스 간에 실제로 적중했다. 3.63배 (29.07 → 8.00초).**

이것이 페이지 캐시가 따뜻해진 효과가 아님을 조건 C·D가 보증한다.
C·D는 반복해도 30초대를 유지하는데 E만 8초로 떨어진다.
캐시 디렉터리에는 파일 432개(2.1 MB)가 쌓였고 2회차 이후 개수가 늘지 않았다 — 재사용되고 있다는 뜻이다.

**Phase 1과 연구자 처방에 주는 함의**:

1. **단일 프로세스 `--input_dir` 순회가 여전히 최선이다.** 컴파일 오버헤드뿐 아니라
   import·디바이스 초기화·가중치 로딩까지 전부 1회로 압축한다.
2. **그러나 그것이 어려운 상황(작업 단위를 쪼개야 하는 경우 등)에서도
   `--jax_compilation_cache_dir` 을 고정 경로로 주는 것만으로 상당한 개선을 얻는다.**
   연구자 스크립트에 한 줄 추가하는 것만으로 얻는 이득이므로 **먼저 시도할 값이 있다.**
3. Phase 1 A/B는 이 두 조건(단일 프로세스 / 캐시 디렉터리 지정)을 **분리해서** 재확인해야 한다.
   사전 진단을 그대로 전제하면 잘못된 결론이 나온다.

버전 차이일 가능성이 높다. 이슈 #468 시점보다 jax(0.10.2)와 AF3(v3.0.4+)가 올라갔다.
**이 결과는 위 버전 조합에서의 실측이며, 다른 버전 조합에 일반화할 수 없다.**

### 6.5 방식 A/B 기준 측정 (3건)

같은 호스트·같은 조건, 지속 캐시 미사용, 3건:

| 방식 | 총 wall | 건당 wall | 최대 VRAM |
|---|---|---|---|
| A) 단일 프로세스 `--input_dir` | 97 s | **32.3 s** | 15,157 MiB (선점) |
| B) 타깃마다 프로세스 재기동 | 271 s | **90.3 s** | 15,146 MiB (선점) |

**wall 기준 2.79배.** 단 이 값은 **하한**이다. 3건뿐이라 단일 프로세스 쪽의 컴파일 상환
이득이 아직 다 반영되지 않았다. 정상 상태 추론이 4.2초인 것을 감안하면 건수가 많아질수록
배수는 커진다. Phase 1에서 더 많은 건수로 재측정해야 한다.

---

## 7. 만난 오류와 해결

| 증상 | 원인 | 해결 |
|---|---|---|
| `docker: command not found`, `sudo -n true` 실패 | Docker 미설치 + sudo 비밀번호 필요 | conda 네이티브 설치로 전환. 차이를 1절에 명시 |
| AF3 설치 시 python 3.11 부적합 | 최신 AF3가 `requires-python >=3.12` | 환경을 python 3.12로 생성 |
| `/usr/include/zlib.h` 없음 | 시스템 zlib 개발 헤더 미설치 (sudo 불가) | conda 로 zlib 설치 + `CMAKE_PREFIX_PATH` 를 env prefix 로 지정 |
| cmake 없음 | 시스템 미설치 | conda 로 설치 |
| `AttributeError: ... has no attribute 'cached_ccd'` | 계측 스크립트가 잘못된 API 사용 | 실제 API는 `chemical_components.Ccd()` 클래스. `dir()` 로 확인 후 수정 |
| RCSB 템플릿 검색이 12건만 반환 | full_text 쿼리에 인용부호를 넣어 매칭이 좁아짐 | 검색식 5개로 나눠 합집합 → 1,239건 확보 |
| `TypeError: '<' not supported between instances of 'dict' and 'dict'` | 정렬 키 미지정으로 dict끼리 비교 | `sort(key=lambda r: r[0])` |
| `ProviderError: nohup launch failed: ERR _pgid not written` (간헐) | 호스트 고부하(load 7~30) 중 작업 제출 경합 | 재시도 루프. 호스트 자체는 정상 |
| `cmd.sh: syntax error near unexpected token '('` | 제출 명령에 중첩 인용부호 | 로직을 스크립트 파일로 옮기고 `bash ./script.sh` 로 호출 |

---

## 8. 이 호스트에서 다시 돌리는 최소 절차

```bash
ENVP=$HOME/miniforge3/envs/af3
export XLA_FLAGS="--xla_gpu_enable_triton_gemm=false"
# 실제 요구량은 약 5.3GB. 선점을 끄면 다른 작업과 GPU를 공유할 수 있다.
export XLA_PYTHON_CLIENT_PREALLOCATE=false

cd $HOME/af3_work/alphafold3
$ENVP/bin/python run_alphafold.py \
  --input_dir=$HOME/af3_work/bench_json \
  --output_dir=$HOME/af3_work/out \
  --model_dir=$HOME/af3_models \
  --db_dir=$HOME/public_databases \
  --jax_compilation_cache_dir=$HOME/af3_jax_cache \
  --flash_attention_implementation=triton
```

축소 DB로 데이터 파이프라인까지 돌릴 때는 `bench_inputs/docs/reduced_db.md` 의
z_value 플래그를 반드시 함께 넘길 것.

경로 요약:

```
~/af3_work/alphafold3   AF3 소스 (commit 97d2023)
~/af3_models/af3.bin    가중치 (1,146,811,260 B)
~/public_databases      축소 DB 2.0 GB + mmCIF 1,239건 (연구자 조건 대조군)
~/public_databases_full 전체 DB (다른 트랙이 받는 중)
~/miniforge3/envs/af3   python 3.12 + jax 0.10.2 + HMMER 3.4
~/af3_work/bench_json   Phase 1 입력 JSON 96건
~/af3_work/logs         계측 로그 전량
```

---

## 9. Phase 1 에 인계하는 확정 사항

1. **설치는 동작한다.** 위 버전 조합으로 sm_120에서 AF3 추론이 끝까지 돈다.
2. **정상 상태 추론 시간 약 4.2초** (버킷128, sample 5 × recycle 10, MSA 없음).
   연구자의 건당 5.7분(342초)과 비교하면 **순수 추론은 전체의 1.2% 수준**이다.
   나머지는 고정 오버헤드와 MSA 단계다.
3. **실제 VRAM 요구량 5.3 GB.** 32 GB 카드에서 "하나밖에 못 돌린다"는 전제는 틀렸다.
4. **버킷은 128/256 두 개뿐**이므로 길이순 정렬 시 재컴파일은 최대 2회.
5. **지속 컴파일 캐시가 프로세스 간에 적중한다** (이 버전 조합에서). 사전 진단과 다르다.
   A/B 설계 시 반드시 분리 측정할 것.
6. 입력 세트(`bench_inputs/`), 축소 DB(`~/public_databases`), z_value 준비 완료.
