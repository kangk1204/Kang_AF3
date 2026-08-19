> 이 문서는 저장소 `docs/commands.md` 다. 한글 제목은 "복사해 붙이는 명령 모음".
> 저장소 최상위 [README.md](../README.md) 의 안내를 먼저 읽고 이 문서로 오는 것을 권한다.
> 문서 안에서 언급하는 그림은 `figures/`, 측정 CSV 는 `results_example/`,
> 스크립트는 `scripts/` 에 있다 (모두 저장소 최상위 기준 경로).

# 파이썬 스크립트 없이 쓰는 AlphaFold 3 명령 모음

복사해서 터미널에 붙여넣으면 되는 명령만 모았다.
`af3_batch.py` 를 쓰기 전에 **원리를 눈으로 확인**하고 싶을 때, 또는
스크립트가 말썽일 때 손으로 돌리기 위한 것이다.

전제:
- 도커 이미지 이름은 `alphafold3`
- DB는 `~/public_databases`, 가중치는 `~/af3_models`
- 작업 폴더에서 실행하며 입력은 `vhh_001_in`, 출력은 `vhh_001_out`
- 이름이 다르면 `vhh_001` 부분만 바꾸면 된다

---

## 0. 준비: sudo 없이 docker 쓰기 (한 번만)

지금은 `sudo docker run ...` 을 쓰고 있는데, 2000건 배치에서는 sudo가 걸림돌이 된다.
아래를 한 번 실행하고 **로그아웃 후 다시 로그인**(또는 재부팅)하면 sudo가 필요 없어진다.

```bash
sudo usermod -aG docker $USER
```

재로그인 후 확인:

```bash
docker info > /dev/null && echo "sudo 없이 docker 사용 가능"
```

이 명령이 실패하면 아래 모든 명령의 `docker` 를 `sudo docker` 로 바꿔서 쓰면 된다.
(동작에는 차이가 없다.)

---

## 1. 환경 확인 (먼저 이것부터)

```bash
# GPU와 드라이버
nvidia-smi

# 이미지가 있는지
docker images | grep -i alphafold

# 이미지에 박혀 있는 GPU 메모리 관련 설정 확인 (선점 여부를 여기서 본다)
docker image inspect alphafold3 --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -E 'XLA|CUDA|TF_FORCE'

# 이 이미지가 지원하는 플래그 확인 (--input_dir 이 있는지가 핵심)
docker run --rm alphafold3 python run_alphafold.py --help | grep -E 'input_dir|buckets|num_diffusion|num_recycles|norun|n_cpu|compilation_cache'

# DB 구성과 용량 (2GB 급이면 축소 DB 확정)
du -sh ~/public_databases
ls -lhS ~/public_databases

# 가중치
ls -lh ~/af3_models

# CPU 코어 수 (MSA 병렬 갈래 수를 정하는 값)
nproc
```

더 자세한 진단은 `af3_check.sh` 를 쓰면 한 화면에 정리해서 나온다.

```bash
bash af3_check.sh > af3_check.txt
cat af3_check.txt
```

---

## 2. 핵심 명령 — 컨테이너 1회로 전수 순회

**이것이 이번 개선의 본질이다.** 기존 방식은 JSON마다 `docker run` 을 새로 띄웠지만,
아래 명령은 `--json_path` 대신 **`--input_dir`** 를 써서
**컨테이너 하나가 폴더 안의 JSON 전부를 순회**한다.

```bash
docker run --rm --gpus all \
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
    --num_diffusion_samples=1 \
    --num_recycles=3
```

각 옵션이 하는 일:

| 옵션 | 하는 일 | 왜 |
|---|---|---|
| `--input_dir` | 폴더 전체를 한 프로세스가 순회 | **가장 중요.** 컨테이너 기동·JAX 초기화·가중치 로드·XLA 컴파일을 1회로 압축 |
| `--buckets=128,256` | 필요한 패딩 계단만 지정 | **128 을 반드시 포함.** 버킷 128 추론 4.20초 대 버킷 256 9.44초 (실측 2.25배) |
| `--num_diffusion_samples=1` | 확산 샘플 5 → 1 | 전수 스크리닝용 경량 설정. 상위 후보는 나중에 5로 재실행 |
| `--num_recycles=3` | 리사이클 10 → 3 | 같은 이유 |
| `--jax_compilation_cache_dir` | 컴파일 결과 보관 | **단일 프로세스로 바꾼 뒤에만 의미가 있다**(아래 주석 참고) |

주의: 버킷 목록에서 **`128` 을 빼면 안 된다.**
`run_alphafold.py` 의 기본 사다리는 `128, 256, 384, 512, ...` 로 128 부터 시작하고,
VHH 111~144 aa 중 128 토큰 이하인 것은 128 버킷에 들어간다.
`--buckets=256` 만 주면 111 aa 서열도 256으로 패딩되어 **추론이 2.25배 느려진다**
(실측: 버킷 128 정상상태 4.20초 대 버킷 256 정상상태 9.44초).

더 긴 서열이 섞여 있으면 필요한 계단을 모두 적는다.

```bash
    --buckets=128,256,384,512
```

정확히 몇 개가 필요한지는 `af3_batch.py` 가 입력을 읽어서 자동으로 정한다.

> **컴파일 캐시에 대한 주석 (측정으로 확정됨)**
> 이 작업에 전제로 전달받은 설명은 "컴파일 캐시는 프로세스가 바뀌면 적중하지 않는다"
> (공식 저장소 이슈 #468 이 출처로 제시)였다. **32건 x 3반복으로 통제 측정한 결과
> 이 전제는 이 스택(jax 0.10.2 + AF3 v3.0.4+)에서 성립하지 않는다** —
> 캐시는 프로세스 간에 실제로 적중한다.
>
> 다만 그 이득은 작고 배치가 커지면 사라진다:
>
> | 개입 | 개선 배수 |
> |---|---|
> | 단일 프로세스화 (캐시 미지정 상태) | **5.10배** |
> | 캐시 디렉터리 지정만 (프로세스별 유지) | 1.76배 |
> | 캐시 디렉터리 지정만 (단일 프로세스에서) | 1.16배 |
>
> 이유는 캐시가 없애는 것이 **첫 2건의 컴파일뿐**이기 때문이다. 96건 순회에서
> 정상 상태는 캐시 유무와 무관하게 **양쪽 모두 4.20초**였다(캐시 지정 시 1·2번째가
> 7.95/6.87초, 미지정 시 21.90/20.62초 — 총 28초 차이, 건당 0.3초).
> **2000건에서는 0으로 수렴한다.**
>
> 그래도 붙여 두는 이유: 한 줄이고, 중간에 멈췄다 다시 시작하는 상황에서는
> 프로세스가 새로 뜨므로 1.76배가 실제로 온다.
>
> 순회 로그에서 직접 확인할 수 있다 — 3번째 타깃부터 4~5초로 떨어지면 정상이다.

---

## 3. 상위 후보만 정밀 재실행

경량 스크리닝으로 고른 상위 후보 JSON을 `vhh_top_in` 폴더에 모아두고,
샘플 관련 플래그를 빼면 AF3 기본값(확산 샘플 5, 리사이클 10)으로 돌아간다.

```bash
docker run --rm --gpus all \
  -v $HOME/public_databases:/root/public_databases \
  -v $HOME/af3_models:/root/af3_models \
  -v $PWD/vhh_top_in:/root/af3_in \
  -v $PWD/vhh_top_out:/root/af3_out \
  -v $HOME/af3_jax_cache:/root/af3_cache \
  alphafold3 \
  python run_alphafold.py \
    --input_dir=/root/af3_in \
    --model_dir=/root/af3_models \
    --db_dir=/root/public_databases \
    --output_dir=/root/af3_out \
    --jax_compilation_cache_dir=/root/af3_cache \
    --buckets=128,256
```

---

## 4. MSA(CPU)와 추론(GPU) 분리

### 4-1. MSA만 계산 (GPU를 쓰지 않는다)

`--gpus all` 을 **일부러 뺐다.** MSA는 CPU 작업이므로 GPU를 점유할 필요가 없다.
그래야 GPU로 다른 일을 동시에 할 수 있다.

```bash
docker run --rm \
  -v $HOME/public_databases:/root/public_databases \
  -v $HOME/af3_models:/root/af3_models \
  -v $PWD/vhh_001_in:/root/af3_in \
  -v $PWD/vhh_001_msa:/root/af3_out \
  alphafold3 \
  python run_alphafold.py \
    --input_dir=/root/af3_in \
    --model_dir=/root/af3_models \
    --db_dir=/root/public_databases \
    --output_dir=/root/af3_out \
    --norun_inference \
    --jackhmmer_n_cpu=8 \
    --nhmmer_n_cpu=8
```

이 단계가 만들어내는 `*_data.json` 이 **MSA가 담긴 재사용 가능한 자산**이다.
모아두면 시드를 추가하거나 리간드를 바꿀 때 MSA를 다시 계산하지 않아도 된다.

```bash
mkdir -p vhh_001_msa_store
find vhh_001_msa -name '*_data.json' -exec cp {} vhh_001_msa_store/ \;
ls vhh_001_msa_store | head
```

### 4-2. MSA를 여러 갈래로 동시 실행하지 마라 (실측 결과)

이 절은 원래 "입력을 나눠 컨테이너를 여러 개 띄우면 CPU 병렬화 이득이 있다"고
적혀 있었다. **14개 조합 스윕으로 실측한 결과 그 전제가 틀렸다.**

| 구성 | 총 요구 스레드 | 처리율 (타깃/분) |
|---|---|---|
| `--jackhmmer_n_cpu=8` x **1갈래** | 32 | **0.890** |
| `--jackhmmer_n_cpu=4` x 2갈래 | 32 | 0.767 |
| `--jackhmmer_n_cpu=6` x 2갈래 | 48 | 0.862 |
| `--jackhmmer_n_cpu=4` x 3갈래 | 48 | 0.754 |
| `--jackhmmer_n_cpu=3` x 8갈래 | 96 | 0.848 |

**스레드 총량이 같으면 갈래를 늘린 쪽이 항상 느리다.** 처리율 상한은
약 0.895 타깃/분이고, 스레드를 96(코어 수의 4배)까지 늘려도 이 값을 못 넘는다.

이유는 AF3 소스에 있다. `pipeline.py` 가 단백질 체인 1개당 DB 4개를
`ThreadPoolExecutor(max_workers=4)` 로 동시 검색한다. 즉 **1건당 실효 스레드 =
`n_cpu` x 4** 이고, **병렬성이 이미 코드 안에 있다.** 여기에 프로세스를 더 얹으면
컨텍스트 스위칭과 메모리 대역폭 경쟁만 늘어난다.

**권장: 단일 갈래로 `--jackhmmer_n_cpu = min(코어수/2, 8)`.**

| 코어 수 | 권장 `n_cpu` | 동시 갈래 |
|---|---|---|
| 8 | 4 | 1 |
| 12 | 6 | 1 |
| 16 | 8 | 1 |
| 24 이상 | 8 (AF3 기본 상한) | 1 |

AF3 기본값이 `min(cpu_count, 8)` 이므로 **8코어 이상 머신에서는 기본값이 이미
최적에 가깝다.** 손대지 않는 편이 낫다.
따라서 4-1절의 명령(단일 갈래)을 그대로 쓰면 된다. 샤딩 스크립트는 필요 없다.

포화 원인(CPU 대 디스크 I/O)은 미측정이다.

### 4-3. 보관한 MSA로 추론만 실행

```bash
docker run --rm --gpus all \
  -v $HOME/public_databases:/root/public_databases \
  -v $HOME/af3_models:/root/af3_models \
  -v $PWD/vhh_001_msa_store:/root/af3_in \
  -v $PWD/vhh_001_out:/root/af3_out \
  -v $HOME/af3_jax_cache:/root/af3_cache \
  alphafold3 \
  python run_alphafold.py \
    --input_dir=/root/af3_in \
    --model_dir=/root/af3_models \
    --db_dir=/root/public_databases \
    --output_dir=/root/af3_out \
    --norun_data_pipeline \
    --jax_compilation_cache_dir=/root/af3_cache \
    --buckets=128,256 \
    --num_diffusion_samples=1 \
    --num_recycles=3
```

> **주의: 2단계 분리에서 가장 실패하기 쉬운 지점**
> 이 단계에 넘기는 것은 반드시 MSA 단계가 만든 **`*_data.json`** 이어야 한다.
> 원본 JSON을 넘기면 MSA 정보가 없으므로 실패한다(`missing unpaired MSA` 류 메시지).
> 또 `unpairedMsa` / `pairedMsa` / `templates` 중 일부만 채워진 파일도 거부된다
> (단량체라도 `pairedMsa` 는 `""`, `templates` 는 `[]` 여야 한다).
> `af3_batch.py` 는 이 두 경우를 컨테이너 실행 전에 걸러내고 한국어로 사유를 알려준다.
>
> 근거 수준: 이 실패 양상은 이 작업에 **전제로 전달받은 내용**이며(공식 저장소 이슈
> #485 가 출처로 제시되었다), 이슈 원문을 직접 확인하지 못했고 **재현 시도도 하지
> 않았다.** 다만 위 두 검사는 전제가 맞든 틀리든 손해가 없다 — 실패할 입력을 미리
> 걸러내는 것뿐이므로, 실제 실패 메시지가 다르게 나오더라도 이 절차를 따르는 것이 안전하다.

빠른 자체 점검:

```bash
# _data.json 에 MSA가 실제로 들어 있는지 확인 (unpairedMsa 가 보이면 정상)
head -c 400 vhh_001_msa_store/$(ls vhh_001_msa_store | head -1)
grep -c unpairedMsa vhh_001_msa_store/*.json | head
```

---

## 5. VRAM 실사용량 측정 (선점 끄기)

`nvidia-smi` 가 꽉 차 보이는 것이 선점인지 실사용인지 구분하는 방법이다.

**터미널 A** — 선점을 끄고 1건 실행:

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

**터미널 B** — 1초마다 기록:

```bash
nvidia-smi --query-gpu=timestamp,memory.used,utilization.gpu --format=csv -l 1 | tee vram_trace.csv
```

최대값 확인:

```bash
sort -t, -k2 -n vram_trace.csv | tail -3
```

읽는 법:
- 선점을 끈 상태의 최대 `memory.used` = **실제로 필요한 VRAM**
- `utilization.gpu` 가 0% 인 구간 = GPU가 아니라 CPU(MSA)나 컴파일/로딩에 쓰는 시간.
  이 구간이 길다는 것이 곧 최적화 여지가 크다는 뜻이다.

---

## 6. OOM이 날 때

메모리가 부족해서 죽으면, 호스트 RAM으로 흘려보내는 설정을 쓴다.
**느려지지만 죽지는 않는다.**

```bash
docker run --rm --gpus all \
  -e XLA_PYTHON_CLIENT_PREALLOCATE=false \
  -e TF_FORCE_UNIFIED_MEMORY=true \
  -e XLA_CLIENT_MEM_FRACTION=3.2 \
  ... (이하 동일)
```

`af3_batch.py --unified-memory` 가 같은 설정을 붙인다.

---

## 7. 오래 걸리는 작업을 안전하게 돌리기

2000건은 몇 시간에서 며칠이 걸린다. 터미널이 끊기면 작업도 죽는다.

```bash
# 방법 1: nohup (가장 간단)
nohup bash af3run.sh vhh_001 screen > af3_run.log 2>&1 &
tail -f af3_run.log        # 진행 상황 보기 (Ctrl+C 로 보기만 종료, 작업은 계속)

# 방법 2: screen (설치되어 있으면 이게 편하다)
screen -S af3
bash af3run.sh vhh_001 screen
# Ctrl+A 누른 뒤 D  -> 빠져나오기 (작업은 계속 돈다)
screen -r af3              # 다시 들어가기
```

진행 상황 확인:

```bash
# 완료된 결과 개수
ls vhh_001_out | wc -l

# 요약 CSV (af3_batch.py 를 쓴 경우)
cat vhh_001_work/run_summary.csv

# GPU가 실제로 일하고 있는지
nvidia-smi
```

---

## 8. 중간에 멈췄을 때 이어서 하기

`af3_batch.py` 는 결과 폴더에 완료 표식(`*_summary_confidences.json`)이 있는 타깃을
자동으로 건너뛴다. 그냥 같은 명령을 다시 실행하면 된다.

```bash
bash af3run.sh vhh_001 screen      # 끝난 것은 건너뛰고 남은 것만
bash af3run.sh vhh_001 retry       # 실패한 것만 다시
```

손으로 돌린 경우, `--input_dir` 방식은 이미 결과가 있는 입력도 다시 계산한다.
남은 것만 골라내려면:

```bash
mkdir -p remaining_in
for f in vhh_001_in/*.json; do
  n=$(basename "$f" .json)
  if [ ! -d "vhh_001_out/$n" ]; then cp "$f" remaining_in/; fi
done
ls remaining_in | wc -l    # 남은 건수
```

그 다음 2번 명령에서 `vhh_001_in` 을 `remaining_in` 으로 바꿔 실행한다.

---

## 9. 권장 실행 순서 요약

```bash
# 0) macOS 에서 가져온 입력이면 사이드카부터 확인 (0 이어야 한다)
ls -a vhh_001_in | grep -c '^\._'
find vhh_001_in -name '._*' -delete      # 0 이 아니었으면 실행

# 1) 환경 진단 (1회)
bash af3_check.sh > af3_check.txt

# 2) 명령이 제대로 조립되는지 눈으로 확인 (실행 안 함)
python3 af3_batch.py --name vhh_001 --stage both --dry-run

# 3) 20건만 돌려 건당 시간 측정 (개선 배수 확인)
bash af3run.sh vhh_001 bench

# 4) 전수 경량 스크리닝
nohup bash af3run.sh vhh_001 screen > af3_run.log 2>&1 &
tail -f af3_run.log                      # 3번째 타깃부터 4~5초면 정상

# 5) 결과 집계 (등급 열 포함 CSV)
bash af3run.sh vhh_001 collect

# 6) 상위 후보 목록을 뽑아 vhh_top_in 에 모은다
python3 af3_collect.py vhh_001=vhh_001_out --top 100 --top-list top100.txt -o 요약.csv
mkdir -p vhh_top_in
while read n; do find vhh_001_in -iname "${n}.json" -exec cp {} vhh_top_in/ \; ; done < top100.txt

# 7) 상위 후보 정밀 재실행
bash af3run.sh vhh_top full
```

**컷오프(몇 건을 재실행할지)는 이 문서가 정해 줄 수 없다.**
경량 설정의 순위 보존을 측정하지 못했으므로, 소규모 예비실험으로 직접 정해야 한다.
방법은 `docs/operations_guide.md` 5-4절에 있다.

---

## 10. 이 문서에서 정정된 것 (실측 반영)

이 문서의 이전 판에는 실측 전의 가정이 들어 있었다. 정정 내역:

| 항목 | 이전 서술 | 실측 결과 |
|---|---|---|
| 버킷 | `--buckets=256` 하나로 고정 | **`128` 을 포함해야 한다.** 128 버킷 4.20초 대 256 버킷 9.44초 (2.25배) |
| 컴파일 캐시 | 프로세스가 바뀌면 적중하지 않는다 (이슈 #468) | 적중한다. 다만 이득은 1.16~1.76배이고 배치가 커지면 0으로 수렴 |
| MSA 병렬 | 갈래를 3~6개로 늘리면 이득이 있다 | **손해다.** 같은 스레드 총량에서 1갈래 0.890 대 2갈래 0.767 타깃/분 |
| 길이순 정렬 | 정렬하지 않으면 재컴파일이 반복된다 | **이득 0.00초/건.** XLA 가 버킷별 컴파일을 프로세스 수명 동안 보유 |

근거는 `docs/benchmark_report.md` 에 전량 있다.
