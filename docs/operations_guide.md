> 이 문서는 저장소 `docs/operations_guide.md` 다. 한글 제목은 "AF3 운영 가이드".
> 저장소 최상위 [README.md](../README.md) 의 안내를 먼저 읽고 이 문서로 오는 것을 권한다.
> 문서 안에서 언급하는 그림은 `figures/`, 측정 CSV 는 `results_example/`,
> 스크립트는 `scripts/` 에 있다 (모두 저장소 최상위 기준 경로).

# AlphaFold 3 운영 가이드 — 설정부터 실행·모니터링·트러블슈팅까지

대상: VHH/나노바디 대량 스크리닝, RTX 5090 32GB, Docker 이미지 `alphafold3`
전제 경로: `~/public_databases` (DB), `~/af3_models` (가중치), `<이름>_in` / `<이름>_out`

이 문서는 **복사해서 그대로 붙여넣는 것**을 목표로 썼다.
명령 블록은 전부 실행 가능한 형태이고, 바꿀 곳은 `vhh_001` 부분뿐이다.

읽는 순서:
- 처음 한 번만: 1장 (준비) → 2장 (환경 진단)
- 매번: 3장 (실행) → 4장 (모니터링) → 5장 (결과 확인)
- 문제가 생기면: 6장 (트러블슈팅)
- 반드시 한 번은: 9장 (라이선스)

---

## 0. 왜 지금 방식이 느린가 — 한 문단

JSON 하나마다 `docker run` 을 새로 띄우면, 타깃마다 **컨테이너 기동 + JAX/CUDA 초기화
+ 가중치 1.15GB 로딩 + XLA 커널 컴파일**을 처음부터 반복한다.
실측하면 이 고정 비용이 건당 9.1초(컨테이너 없이 재도, 컨테이너를 쓰면 더 크다)이고,
정작 구조 예측 자체는 **4.2초**다. 즉 지금은 준비에 대부분의 시간을 쓰고 있다.

해결은 `--json_path` 를 **`--input_dir`** 로 바꿔 **프로세스 하나가 폴더 전체를
순회**하게 하는 것이다. 실측 5.10배. 자세한 근거는 `docs/diagnosis_report.md` 참고.

---

## 1. 준비 (처음 한 번만)

### 1-1. sudo 없이 docker 쓰기

2000건 배치에서 매번 비밀번호를 넣는 것은 걸림돌이 된다.
아래를 한 번 실행하고 **로그아웃 후 다시 로그인**(또는 재부팅)하면 된다.

```bash
sudo usermod -aG docker $USER
```

재로그인 후 확인:

```bash
docker info > /dev/null && echo "sudo 없이 docker 사용 가능"
```

실패하면 이 문서의 모든 `docker` 를 `sudo docker` 로 바꿔 쓰면 된다. 동작은 같다.

### 1-2. 폴더 구조 만들기

기존 관례를 그대로 쓴다.

```bash
mkdir -p vhh_001_in vhh_001_out ~/af3_jax_cache
```

| 폴더 | 용도 |
|---|---|
| `vhh_001_in` | 입력 JSON. **스크립트가 절대 수정하지 않는다** |
| `vhh_001_out` | 결과. AF3 가 타깃별 하위 폴더를 만든다 |
| `vhh_001_work` | 스크립트 작업 공간(로그, MSA 보관, 요약 CSV). 자동 생성 |
| `~/af3_jax_cache` | XLA 컴파일 캐시. 한 번 만들면 계속 쓴다 |

### 1-3. 스크립트 배치

`af3_batch.py`, `af3run.sh`, `af3_check.sh`, `af3_collect.py` 를 작업 폴더에 둔다.

```bash
ls af3_batch.py af3run.sh af3_check.sh af3_collect.py
```

### 1-4. macOS 에서 파일을 가져왔다면 — 이것을 먼저 확인하라

다른 컴퓨터(특히 Mac)에서 만든 `tar.gz` 를 리눅스에서 풀면
파일마다 `._` 로 시작하는 사이드카가 함께 생긴다.
**`ls` 에는 보이지 않지만 프로그램의 `*.json` 검색에는 잡히고, UTF-8 이 아니어서
읽는 순간 죽는다.** 실제로 이것 때문에 측정 3시간을 통째로 날린 적이 있다.

```bash
# 사이드카가 있는지 확인 (0 이면 정상)
ls -a vhh_001_in | grep -c '^\._'

# 있으면 지운다 (지워도 안전하다 — Mac 의 메타데이터일 뿐이다)
find vhh_001_in -name '._*' -delete
```

`af3_batch.py` 와 `af3_collect.py` 는 이제 이 파일들을 자동으로 건너뛰지만,
손으로 명령을 쓸 때는 위 확인을 하는 편이 안전하다.

---

## 2. 환경 진단 (실행 전에 한 번)

```bash
bash af3_check.sh > af3_check.txt
cat af3_check.txt
```

한 화면에 정리해서 나온다. 직접 확인하고 싶으면:

```bash
# GPU와 드라이버
nvidia-smi

# 이미지 존재 확인
docker images | grep -i alphafold

# 이 이미지가 지원하는 플래그 — input_dir 이 있는지가 핵심
docker run --rm alphafold3 python run_alphafold.py --help \
  | grep -E 'input_dir|buckets|num_diffusion|num_recycles|norun|n_cpu|compilation_cache'

# 이미지에 박혀 있는 메모리 설정 (선점 여부를 여기서 본다)
docker image inspect alphafold3 --format '{{range .Config.Env}}{{println .}}{{end}}' \
  | grep -E 'XLA|CUDA|TF_FORCE'

# DB 크기 — 2GB 급이면 축소 DB 확정
du -sh ~/public_databases

# 가중치
ls -lh ~/af3_models

# CPU 코어 수 — MSA 스레드 수를 정하는 값
nproc
```

**`--input_dir` 이 없으면 이 가이드의 방식을 쓸 수 없다.**
그 경우 이미지를 최신 AF3 소스로 다시 빌드해야 한다.

동작이 확인된 버전 조합은 `docs/install_log.md` 에 적혀 있다
(AF3 commit `97d20234`, python 3.12, `jax[cuda12]==0.10.2`, HMMER 3.4 + AF3 패치).
Blackwell 카드에서 `flash_attention_implementation=triton` 기본값이 그대로 동작하고,
시스템 CUDA 툴킷(`nvcc`)은 필요하지 않다.

---

## 3. 실행

### 3-1. 가장 먼저: 실행하지 않고 명령만 확인

```bash
bash af3run.sh vhh_001 dry
```

조립된 `docker run` 명령이 화면에 그대로 나온다.
경로가 맞는지, 버킷이 `128,256` 로 잡혔는지 눈으로 확인하고 넘어간다.

### 3-2. 20건만 돌려 건당 시간 재기

```bash
bash af3run.sh vhh_001 bench
```

끝나면 건당 평균 시간이 나온다. **이 값을 적어 두라** — 자기 머신에서의 실제 개선
배수를 아는 유일한 방법이다. 참고로 검증 호스트(5070 Ti, 네이티브)에서는
GPU 추론만 5.39초/건이었다.

### 3-3. 전수 실행 (권장 방식)

```bash
nohup bash af3run.sh vhh_001 screen > af3_run.log 2>&1 &
```

`nohup ... &` 를 붙이면 **터미널을 닫아도 계속 돈다.**
`screen` 모드는 경량 설정(확산 샘플 1, 리사이클 3)으로 전수 스크리닝한다.

`screen` 명령이 설치되어 있으면 이쪽이 더 편하다:

```bash
screen -S af3
bash af3run.sh vhh_001 screen
# Ctrl+A 를 누른 뒤 D  -> 빠져나오기 (작업은 계속 돈다)
screen -r af3                       # 다시 들어가기
```

### 3-4. 모드 목록

```bash
bash af3run.sh vhh_001 dry        # 실행 없이 명령만 확인 (첫 단계)
bash af3run.sh vhh_001 check      # 환경 진단
bash af3run.sh vhh_001 bench      # 앞 20건만 (시간 측정)
bash af3run.sh vhh_001 screen     # 전수 경량 스크리닝 (sample 1, recycle 3)
bash af3run.sh vhh_001 full       # AF3 기본값 정밀 (sample 5, recycle 10)
bash af3run.sh vhh_001 msa        # MSA(CPU)만 미리 계산해 보관
bash af3run.sh vhh_001 infer      # 보관된 MSA로 추론(GPU)만
bash af3run.sh vhh_001 oneshot    # MSA+추론을 한 프로세스에서 (가장 단순)
bash af3run.sh vhh_001 retry      # 실패한 것만 재시도
bash af3run.sh vhh_001 collect    # 결과를 CSV 한 장으로 집계
```

### 3-5. 스크립트 없이 손으로 돌리기

스크립트가 말썽일 때, 또는 원리를 확인하고 싶을 때.
**이 한 덩어리가 이번 개선의 전부다.**

```bash
docker run --rm --gpus all \
  -e XLA_PYTHON_CLIENT_PREALLOCATE=false \
  -v $HOME/public_databases:/root/public_databases \
  -v $HOME/af3_models:/root/af3_models \
  -v $PWD/vhh_001_in:/root/af3_in \
  -v $PWD/vhh_001_out:/root/af3_out \
  -v $HOME/af3_jax_cache:/root/af3_cache \
  alphafold3 \
  python run_alphafold.py \
    --input_dir=/root/af3_in \
    --model_dir=/root/af3_models \
    --db_dir=/root/public_databases \
    --output_dir=/root/af3_out \
    --jax_compilation_cache_dir=/root/af3_cache \
    --buckets=128,256 \
    --jackhmmer_n_cpu=8 \
    --nhmmer_n_cpu=8 \
    --num_diffusion_samples=1 \
    --num_recycles=3
```

각 옵션의 역할:

| 옵션 | 하는 일 | 왜 |
|---|---|---|
| `--input_dir` | 폴더 전체를 한 프로세스가 순회 | **가장 중요.** 고정 비용을 N배에서 1배로. 실측 5.10배 |
| `--buckets=128,256` | 필요한 패딩 계단만 지정 | 128 버킷 4.20초 대 256 버킷 9.44초, **2.25배 차이** |
| `--num_diffusion_samples=1` | 확산 샘플 5 → 1 | 전수 스크리닝용 경량. 상위 후보는 나중에 5로 |
| `--num_recycles=3` | 리사이클 10 → 3 | 같은 이유 |
| `--jackhmmer_n_cpu=8` | MSA 검색 스레드 | `min(코어수/2, 8)`. 8코어 이상이면 8 |
| `--jax_compilation_cache_dir` | 컴파일 결과 보관 | 배치가 크면 이득이 작지만 한 줄이다 |
| `-e XLA_PYTHON_CLIENT_PREALLOCATE=false` | VRAM 선점 끄기 | 속도용이 아니라 GPU 공유용 |

**`--buckets` 에서 `128` 을 빼지 마라.** VHH 111~144 aa 중 128 토큰 이하인 것이
대부분인데, `--buckets=256` 만 주면 전량이 256으로 패딩되어 2.25배 느려진다.
길이가 더 긴 것이 섞여 있으면 계단을 늘린다: `--buckets=128,256,384,512`.

더 많은 손 명령(MSA/추론 분리, VRAM 측정, OOM 대처)은 `docs/commands.md` 에 있다.

### 3-6. MSA와 추론을 분리하는 이유와 방법

`--stage both` (기본)가 이것을 자동으로 한다.
MSA 단계 산출물 `*_data.json` 에는 **MSA가 그대로 담겨 있어 재사용할 수 있다.**
시드를 늘리거나 샘플 수를 바꿔 다시 돌릴 때 MSA 재계산(건당 67초)을 건너뛸 수 있다.
**2000건에서 MSA가 전체 시간의 93%이므로, 같은 서열을 두 번 이상 돌릴 계획이 있으면
이것이 가장 큰 절약이다.**

```bash
bash af3run.sh vhh_001 msa        # 1단계: MSA만 계산해서 보관
bash af3run.sh vhh_001 infer      # 2단계: 보관된 MSA로 추론
```

보관된 MSA 확인:

```bash
ls vhh_001_work/msa_store | wc -l                      # 몇 건 보관됐나
grep -c unpairedMsa vhh_001_work/msa_store/*.json | head -3   # MSA가 들어 있나
```

**중요: MSA 동시 실행 갈래는 1개가 최적이다.**
실측에서 32스레드를 1갈래에 몰면 0.890 타깃/분, 2갈래로 쪼개면 0.767 로 **더 느렸다.**
AF3 가 이미 체인당 DB 4개를 내부에서 동시 검색하기 때문이다.
`af3_batch.py --msa-workers` 기본값은 이 근거로 1 이다. **늘리지 마라.**

---

## 4. 모니터링 — 정상 동작 시 무엇이 보이나

### 4-1. 로그 보기

```bash
tail -f af3_run.log          # Ctrl+C 는 '보기'만 종료. 작업은 계속 돈다
```

### 4-2. 정상일 때 보이는 것

순회가 시작되면 타깃마다 이런 줄이 나온다:

```
Running fold job 01_vhh_001_A0A1W5VKQ0...
Featurising data for seeds (1,)...
Featurising data for seeds (1,) took 0.55 seconds.
Running model inference for seed 1...
Running model inference for seed 1 took 21.71 seconds.
...
Running fold job 02_vhh_002_A0A1W5VKS5...
Running model inference for seed 1 took 6.87 seconds.
...
Running fold job 03_...
Running model inference for seed 1 took 4.20 seconds.
```

**시간이 이렇게 줄어드는 것이 정상이고, 이것이 최적화가 듣고 있다는 증거다.**

| 순번 | 예상 시간 | 무슨 일이 일어나는가 |
|---|---|---|
| 1번째 | 8~25초 | XLA 컴파일 중 |
| 2번째 | 7~21초 | 컴파일 잔여 |
| 3번째부터 | **4~5초** | **정상 상태.** 여기 도달하면 성공이다 |
| 중간에 갑자기 27초 | | 서열 길이가 다른 버킷으로 넘어가 컴파일이 한 번 더 일어난 것. 정상이다 |

**만약 모든 타깃이 계속 20초 이상이면 무언가 잘못됐다.**
`--input_dir` 대신 `--json_path` 를 쓰고 있거나, 프로세스가 매번 새로 뜨고 있다.

### 4-3. 진행 상황 확인

```bash
# 완료된 결과 개수
ls vhh_001_out | wc -l

# 요약 CSV (스크립트를 쓴 경우)
cat vhh_001_work/run_summary.csv

# 마지막으로 끝난 타깃 5개
ls -t vhh_001_out | head -5

# 실패 목록
cat vhh_001_work/state.json | python3 -m json.tool | head -20
```

### 4-4. GPU가 실제로 일하고 있는지

```bash
nvidia-smi
```

**여기서 VRAM 숫자를 오독하지 않는 법 — 이 부분이 중요하다.**

`memory.used` 가 15GB 로 보인다고 해서 15GB 가 **필요한** 것이 아니다.
JAX 는 기본적으로 GPU 메모리를 미리 **예약(선점)** 한다. 그 숫자는
"내가 쓰겠다고 잡아둔 양"이고 "실제로 쓰는 양"이 아니다.

실측 결과:

| 조건 | `nvidia-smi` 표시 | 의미 |
|---|---|---|
| 선점 ON (기본) | 15,157 MiB | **예약.** 수요가 아니다 |
| 선점 OFF | **2,942~2,963 MiB** | **실제 수요.** 18런 전체에서 이 범위 |

즉 VHH 단량체 예측의 실제 VRAM 요구량은 **약 3GB**(보수적으로 3~5.3GB)이고,
32GB 카드에서 10% 안쪽이다.

실제 수요를 직접 보려면 선점을 끄고 돌리면서 재면 된다.

터미널 A:
```bash
docker run --rm --gpus all \
  -e XLA_PYTHON_CLIENT_PREALLOCATE=false \
  -e XLA_CLIENT_MEM_FRACTION=1.0 \
  -v $HOME/public_databases:/root/public_databases \
  -v $HOME/af3_models:/root/af3_models \
  -v $PWD/vhh_001_in:/root/af3_in \
  -v $PWD/vram_test_out:/root/af3_out \
  alphafold3 \
  python run_alphafold.py \
    --json_path=/root/af3_in/$(ls vhh_001_in | head -1) \
    --model_dir=/root/af3_models --db_dir=/root/public_databases \
    --output_dir=/root/af3_out --buckets=128,256
```

터미널 B (1초마다 기록):
```bash
nvidia-smi --query-gpu=timestamp,memory.used,utilization.gpu --format=csv -l 1 | tee vram_trace.csv
```

최대값:
```bash
sort -t, -k2 -n vram_trace.csv | tail -3
```

`utilization.gpu` 가 0% 인 구간도 함께 보라. **그 구간은 GPU 가 아니라
CPU(MSA)나 컴파일·로딩에 쓰는 시간이다.** 그 구간이 길다는 것이 곧
최적화 여지가 크다는 뜻이다.

**주의: 메모리가 남는다는 것을 "프로세스를 여러 개 띄울 근거"로 쓰지 마라.**
프로세스를 하나 더 띄우면 기동 비용 9.1초를 그만큼 다시 낸다.
단일 프로세스 순회가 답이다.

---

## 5. 결과 확인과 판정

### 5-1. 집계

```bash
bash af3run.sh vhh_001 collect
```

또는 직접:

```bash
python3 af3_collect.py vhh_001=vhh_001_out -o af3_결과요약.csv
```

`*_data.json` 이 커서 MSA 깊이 계산이 느리면:

```bash
python3 af3_collect.py vhh_001=vhh_001_out --no-msa-depth -o af3_결과요약.csv
```

화면에 등급 분포와 경고 요약이 나오고, CSV 에는 타깃별 상세가 들어간다.

### 5-2. 무엇을 보고 "이 예측을 믿어도 되나"를 판단하나

| 지표 | 무엇인가 | 기준 |
|---|---|---|
| **pLDDT** (0~100) | 잔기/원자 단위 국소 정확도 | 90 이상 측쇄까지 신뢰 / 70~90 백본 신뢰 / 50~70 낮음 / 50 미만 무질서 |
| **pTM** (0~1) | 전체 폴드가 맞을 확률의 대리 지표 | 0.5 초과가 하한선 |
| **ipTM** (0~1) | 계면 정확도. **복합체에서만 산출** | 0.8 이상 신뢰 / 0.6~0.8 회색지대 / 0.6 미만 실패 가능 |
| **ranking_score** | AF3 가 모델을 고를 때 쓰는 종합 점수 | 순위용. 절대 해석은 주의 (아래) |

`af3_collect.py` 가 CSV `등급` 열에 쓰는 규칙:

- 복합체(ipTM 있음): `A_계면신뢰` ipTM ≥ 0.8 & pLDDT평균 ≥ 80 / `B_계면회색` ipTM ≥ 0.6 / `C_계면실패`
- 단량체(ipTM 없음): `A_높음` pLDDT ≥ 90 & pTM ≥ 0.7 / `B_신뢰` pLDDT ≥ 80 & pTM ≥ 0.5 / `C_보통` pLDDT ≥ 70 / `D_낮음`

`경고` 열은 등급과 별개로 붙는다: `충돌`(has_clash>0), `무질서`(≥10%),
`MSA얕음`(unpaired 깊이 < 100), `샘플불안`(ranking 산포 ≥ 0.05),
`버킷256`(추론 2.25배 느림), `검산불일치`(파일 짝이 안 맞음).

기준 설명을 스크립트에서 직접 보려면:
```bash
python3 af3_collect.py --grade-doc .
```

**세 가지 주의:**

1. **이 값들은 "모델이 자기 예측을 얼마나 확신하는가"이지 정답과의 일치도가 아니다.**
   실험 검증 대상의 **순위를 정하는 데** 쓰는 것이고, "이 구조가 맞다"의 증명이 아니다.
2. **`ranking_score` 는 정의상 `fraction_disordered` 를 더한다.**
   (0.8×ipTM또는pTM + 0.2×pTM + 0.5×무질서비율 − 100×충돌)
   그래서 무질서 비율이 높은 건이 pTM 보다 높게 나올 수 있다.
   스크리닝 순위는 `ranking_score` 단독보다 `pLDDT평균` / `pTM` 을 함께 보고 정하라.
   실측 예: 7A50 은 축소 DB 에서 ranking 0.85, pTM 0.80 이었는데 무질서 10% 때문에
   ranking 이 pTM 보다 높다.
3. **pLDDT 평균은 구조적으로 프레임워크가 지배한다.** VHH 원자 대부분이 프레임워크이므로,
   CDR3 같은 가변 루프의 품질은 평균에 거의 반영되지 않는다.
   결합 부위 구조가 중요하면 그 구간을 따로 봐야 한다.
   (`*_confidences.json` 의 `atom_plddts` 가 원자별 값이므로 잔기 구간을 알면 계산할 수 있다.)

### 5-3. 상위 후보 뽑기 (2단계 전략)

```bash
python3 af3_collect.py vhh_001=vhh_001_out --top 100 --top-by ranking_score \
        --top-list top100.txt -o af3_결과요약.csv
```

`top100.txt` 에 타깃 이름이 한 줄씩 들어간다. 이것으로 재실행 폴더를 만든다:

```bash
mkdir -p vhh_top_in
while read n; do cp "vhh_001_in/${n}.json" vhh_top_in/ 2>/dev/null; done < top100.txt
ls vhh_top_in | wc -l
```

이름이 정확히 안 맞으면(AF3 가 출력 폴더명을 소문자화한다) 이렇게 찾는다:

```bash
while read n; do
  find vhh_001_in -iname "${n}.json" -exec cp {} vhh_top_in/ \;
done < top100.txt
```

그 다음 정밀 재실행:

```bash
bash af3run.sh vhh_top full
```

### 5-4. 2단계 전략에 대한 정직한 이야기 — 컷오프는 직접 정해야 한다

이 전략(경량 전수 → 상위만 정밀)은 비용 구조상 합리적이라 **권장한다.**

**그러나 핵심 가정 — "경량 설정의 순위가 기본값의 순위를 보존한다" — 를
측정하지 못했다.** 상관계수도, 상위 N 회수율도, 권장 컷오프도 **근거가 없다.**
`--top N` 이 알려주는 컷오프 값은 "N건을 골랐을 때의 경계값"을 보고하는 것이고
추천값이 아니다.

**그래서 컷오프는 소규모 예비실험으로 직접 정하라.** 방법:

```bash
# 1) 무작위 40건을 골라 별도 폴더로
mkdir -p pilot_in
ls vhh_001_in/*.json | shuf -n 40 | xargs -I{} cp {} pilot_in/

# 2) 경량으로 돌린다
python3 af3_batch.py --input-dir pilot_in --output-dir pilot_screen_out \
        --stage oneshot --diffusion-samples 1 --recycles 3

# 3) 같은 40건을 기본값으로 돌린다
python3 af3_batch.py --input-dir pilot_in --output-dir pilot_full_out \
        --stage oneshot

# 4) 두 결과를 한 CSV 로 집계해 순위를 비교한다
python3 af3_collect.py 경량=pilot_screen_out 정밀=pilot_full_out -o pilot_비교.csv
```

`pilot_비교.csv` 를 열어, 경량에서 상위 20% 였던 것 중 몇 %가 정밀에서도 상위 20% 인지
세어 보라. 그것이 회수율이다.

- 회수율이 높으면(예: 90% 이상) 컷오프를 타이트하게(상위 10~15%) 잡아도 된다.
- 낮으면 넉넉히(상위 30% 이상) 잡아야 놓치지 않는다.

이 예비실험 비용은 작다. 경량 설정에서 건당 몇 초이므로 40건 왕복은 수십 분 규모다.
**3일을 다시 쓸지 결정하는 판단에 수십 분을 쓰는 것은 남는 장사다.**

### 5-5. 축소 DB 를 계속 써도 되나 — 결론과 조건

실측 결과 축소 DB(2GB)는 전체 DB(850GB) 대비 MSA 깊이를 **818~1,186배** 얕게 만든다.
**그런데 VHH 단량체 신뢰도는 거의 변하지 않았다** (6건 중 무변화 3건, +0.03 두 건,
−0.01 한 건). VHH 는 면역글로불린 폴드가 보존되고 PDB 템플릿이 풍부해서
얕은 MSA로도 프레임워크가 복원된다.

**따라서 기존 760건은 폐기 대상이 아니다.** 전체 DB 로 전수 재실행하면
건당 30.5분 = 2000건 **42일**이라 비현실적이기도 하다.

권장:
- 전수 스크리닝은 축소 DB 유지 (단, 단일 프로세스로 전환)
- 최종 후보 수십 건만 전체 DB 로 재실행. 목적은 점수를 올리는 것이 아니라
  **"얕은 MSA가 원인이 아님을 확인"** 하는 것이다
- **항원-나노바디 복합체를 할 계획이면 전체 DB 확보를 검토하라.**
  paired MSA 깊이가 120~150배 차이나고, 복합체 계면은 paired MSA 에 직접 의존한다.
  **단 복합체에서의 영향은 측정하지 않았다** — 단량체라 ipTM 이 산출되지 않았기 때문이다.
  넘어갈 때 10건 정도로 축소/전체 대조를 먼저 하는 것이 안전하다.

전체 DB 가 필요해지면: 다운로드 238.8GB, 해제 후 **850GB**, 작업 중 최대 1.1TB 필요.
실측 소요 3시간 13분(4병렬 다운로드 1h37m + mmCIF 20만 파일 해제 1h36m).
자세한 절차는 `docs/db_notes.md` 참고.

---

## 6. 트러블슈팅 — 자주 만나는 오류와 대처

### 6-1. `UnicodeDecodeError: 'utf-8' codec can't decode byte 0xa3`

**원인: macOS AppleDouble 사이드카.** 1-4절 참고.

```bash
ls -a vhh_001_in | grep -c '^\._'     # 0 이 아니면 이것이 원인이다
find vhh_001_in -name '._*' -delete
```

`ls` 에는 안 보이므로 `ls -a` 로 봐야 한다. 이것 때문에 3시간을 날린 적이 있다.

### 6-2. 모든 타깃이 계속 20초 이상 걸린다

**원인: 프로세스가 매번 새로 뜨고 있다.** 확인:

```bash
grep -c 'Running fold job' af3_run.log      # 타깃 수와 같아야 한다
grep -c 'Found local devices' af3_run.log   # 1 이어야 한다. 타깃 수와 같으면 문제다
```

두 번째 값이 1 이 아니면 `--json_path` 를 타깃마다 호출하는 옛 방식이다.
`--input_dir` 로 바꿔라 (3-5절).

### 6-3. 서열 길이가 짧은데도 느리다

**원인: 버킷 지정 실수.** `--buckets` 에 `128` 이 있는지 확인하라.

```bash
grep -o '\-\-buckets=[0-9,]*' af3_run.log | head -1
```

`--buckets=256` 만 있으면 111 aa 서열도 256으로 패딩되어 2.25배 느려진다.
`--buckets=128,256` 으로 고쳐라. `af3_batch.py` 는 입력을 읽어 자동으로 정한다.

### 6-4. `Protein chain N is missing unpaired MSA`

**원인: 추론 단계에 원본 JSON 을 넘겼다.**
`--norun_data_pipeline` 에 넘기는 것은 MSA 단계가 만든 **`*_data.json`** 이어야 한다.

```bash
# MSA 산출물이 있는지 확인
ls vhh_001_work/msa_store/*_data.json | wc -l

# MSA 가 실제로 들어 있는지 확인
grep -c unpairedMsa vhh_001_work/msa_store/*.json | head -3
```

`unpairedMsa` / `pairedMsa` / `templates` 중 **일부만** 채워진 파일도 거부된다.
단량체라도 `pairedMsa` 는 `""`, `templates` 는 `[]` 여야 한다.
`af3_batch.py` 는 컨테이너를 띄우기 전에 이 두 경우를 걸러내고 한국어로 사유를 알려준다.

### 6-5. `CUDA out of memory` / OOM

VHH 단량체에서는 나올 이유가 없다(수요 3GB, 카드 32GB).
복합체나 긴 서열에서 나면:

```bash
docker run --rm --gpus all \
  -e XLA_PYTHON_CLIENT_PREALLOCATE=false \
  -e TF_FORCE_UNIFIED_MEMORY=true \
  -e XLA_CLIENT_MEM_FRACTION=3.2 \
  ... (이하 동일)
```

호스트 RAM 으로 흘려보낸다. **느려지지만 죽지는 않는다.**
`af3_batch.py --unified-memory` 가 같은 설정을 붙인다.
(주의: 이 설정의 실제 동작은 검증 호스트에서 확인하지 않았다.)

### 6-6. `CUDA_ERROR_UNSUPPORTED_PTX_VERSION`

CUDA 버전이 드라이버보다 높은 것을 요구하는 경우다.
검증 호스트에서는 `jax[cuda12]==0.10.2` (번들 CUDA 12.9) 조합으로 이 오류가 없었다.
`docs/install_log.md` 의 버전 조합을 그대로 맞추는 것이 가장 빠른 해결이다.

### 6-7. 중간에 멈췄다 — 이어서 하기

`af3_batch.py` 는 결과 폴더에 완료 표식(`*_summary_confidences.json`)이 있는 타깃을
자동으로 건너뛴다. **같은 명령을 그냥 다시 실행하면 된다.**

```bash
bash af3run.sh vhh_001 screen      # 끝난 것은 건너뛰고 남은 것만
bash af3run.sh vhh_001 retry       # 실패한 것만 다시
```

미완성 결과 폴더는 삭제하지 않고 `vhh_001_work/partial/` 로 옮겨 둔다
(AF3 가 출력 폴더가 비어 있지 않으면 타임스탬프 폴더를 새로 만들어 결과가 흩어지기 때문).

손으로 돌린 경우 `--input_dir` 방식은 이미 결과가 있는 입력도 다시 계산한다.
남은 것만 골라내려면:

```bash
mkdir -p remaining_in
for f in vhh_001_in/*.json; do
  n=$(basename "$f" .json)
  [ -d "vhh_001_out/$n" ] || cp "$f" remaining_in/
done
ls remaining_in | wc -l
```

### 6-8. 결과 폴더가 있는데 집계에서 빠진다

완료 표식이 없는 미완성 폴더다. `af3_collect.py` 가 몇 건인지 알려준다:

```bash
python3 af3_collect.py vhh_001=vhh_001_out -o /dev/null
# "미완성/건너뜀 N건 (재시도 대상): ..." 줄을 보라
```

### 6-9. `ranking_score 검산 불일치` 경고

`af3_collect.py` 가 `ranking_score` 를 정의식으로 재계산해 원본과 비교한 결과다.
불일치가 나오면 **다른 실행의 파일이 한 폴더에 섞였을 가능성이 크다.**
해당 타깃 폴더를 지우고(`partial/` 로 옮기고) 다시 돌려라.

### 6-10. `docker: permission denied`

```bash
sudo usermod -aG docker $USER    # 실행 후 재로그인
```

재로그인이 안 되는 상황이면 모든 `docker` 를 `sudo docker` 로 바꿔 쓰면 된다.

### 6-11. 출력 폴더에 결과가 흩어져 있다

AF3 는 출력 폴더가 비어 있지 않으면 `<이름>_<타임스탬프>` 폴더를 새로 만든다.
`af3_batch.py` 는 이 두 형태를 모두 찾아 완료 판정을 하지만,
손으로 돌린 경우에는 폴더 이름을 확인해야 한다:

```bash
ls -d vhh_001_out/*_20* 2>/dev/null | head    # 타임스탬프 폴더가 있으면 나온다
```

---

## 7. 권장 실행 순서 (전체 요약)

```bash
# --- 처음 한 번만 ---
sudo usermod -aG docker $USER          # 후 재로그인
mkdir -p vhh_001_in vhh_001_out ~/af3_jax_cache
ls -a vhh_001_in | grep -c '^\._'      # 0 이어야 한다 (아니면 find ... -delete)
bash af3_check.sh > af3_check.txt

# --- 매번 ---
bash af3run.sh vhh_001 dry             # 1) 명령 확인 (실행 안 함)
bash af3run.sh vhh_001 bench           # 2) 20건으로 건당 시간 측정
nohup bash af3run.sh vhh_001 screen > af3_run.log 2>&1 &   # 3) 전수 실행
tail -f af3_run.log                    # 4) 모니터링 (Ctrl+C 로 보기만 종료)
bash af3run.sh vhh_001 collect         # 5) 집계

# --- 2단계 전략 ---
mkdir -p pilot_in                      # 6) 컷오프 예비실험 (5-4절)
python3 af3_collect.py vhh_001=vhh_001_out --top 100 --top-list top100.txt -o 요약.csv
mkdir -p vhh_top_in
while read n; do find vhh_001_in -iname "${n}.json" -exec cp {} vhh_top_in/ \; ; done < top100.txt
bash af3run.sh vhh_top full            # 7) 상위 후보 정밀 재실행
```

---

## 8. 참고 — 기대 시간

| 단계 | 건당 | 2000건 | 근거 |
|---|---|---|---|
| 현재 방식 (연구자 보고) | 341초 | 189시간 (7.9일) | 연구자 |
| GPU 추론만 (권장 구성) | 5.39초 | 3.0시간 | 실측 |
| MSA (축소 DB, 코어 24 기준) | 67.0초 | 37.2시간 | 인용 |
| **전체 (권장 구성)** | 72.4초 | **40.2시간 (1.7일)** | 합산 |

**2000건 40시간 중 37시간(93%)이 MSA다.** 코드를 더 손대도 여기는 줄지 않는다.
더 빠르게 하려면 MSA 를 재사용하거나 사전 계산하는 방향뿐이다 (3-6절).

측정 조건이 다르다: 검증 호스트는 RTX 5070 Ti 16GB, 24코어, **네이티브 설치**(Docker 아님),
웜 페이지 캐시 상태. 연구자 환경(5090 32GB, Docker)에서는 절대 시간이 다르므로
`bench` 모드로 직접 재는 것이 정확하다. 자세한 가정은 `docs/benchmark_report.md` 8절.

---

## 9. 라이선스와 인용 의무 — 반드시 읽을 것

**코드와 가중치의 조건이 다르다.**

- AF3 **코드는 Apache 2.0**.
- **가중치(`af3.bin`)는 별도 약관**이며 다음 제약이 있다:
  - **비영리 목적으로만 사용 가능**
  - **재배포 금지** (가중치 파일을 다른 사람에게 주는 것)
  - AF3 출력물로 다른 구조 예측 모델을 학습시키는 것 금지
- 논문·발표에 사용하면 **Abramson et al. (2024), Nature 인용이 의무**다.
- 약관은 "구글로부터 **직접** 받은 경우"만 사용을 허용한다.
  가중치를 다른 경로로 받았다면 **공식 접근 요청 절차를 통해 승인 기록을
  남기는 것이 안전하다.** 이것은 법률 자문이 아니라 실무적 권고이며,
  기관 사용이라면 소속 기관의 담당 부서와 확인하는 것이 맞다.

AF3 는 각 결과 폴더에 `TERMS_OF_USE.md` 를 자동으로 써 넣는다.

```bash
ls vhh_001_out/*/TERMS_OF_USE.md | head -3
```

**결과를 공유하거나 이동할 때 이 파일을 지우지 마라.**
상세는 `docs/license_notes.md` 참고.

---

## 10. 함께 보는 문서

| 문서 | 내용 |
|---|---|
| `docs/diagnosis_report.md` | 3개 질문에 대한 답, 축소 DB 발견, 미측정 항목 전체 목록 |
| `docs/benchmark_report.md` | A/B 실측 전체와 2000건 환산의 계산 과정 |
| `docs/commands.md` | 파이썬 스크립트 없이 쓰는 명령 모음 |
| `af3_batch.py` | 배치 러너 (재시작·재시도·2단계 분리) |
| `af3run.sh` | 위 스크립트의 간단 래퍼 |
| `af3_check.sh` | 환경 진단 |
| `af3_collect.py` | 결과 집계 (`--grade-doc` 로 판정 기준 확인) |
| `results_example/af3_summary.csv` / `figures/confidence_overview.png` | 집계 예시와 신뢰도 도표 |
| `docs/install_log.md` | 동작 확인된 버전 조합 |
| `docs/db_notes.md` | 전체 DB 다운로드 절차와 MSA 품질 대조 |
| `docs/license_notes.md` | 라이선스 상세 |
