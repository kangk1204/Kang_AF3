#!/usr/bin/env bash
# =============================================================================
# af3_check.sh - AlphaFold 3 실행 환경 진단 (연구자 PC에서 1회 실행)
#
# 목적: GPU/드라이버, 도커 접근성, 도커 이미지 내부 설정, 데이터베이스 구성,
#       가중치, CPU/RAM/디스크를 한 화면에 찍어서 "무엇이 있고 무엇이 없는지"를
#       사실로 확인한다.
#       7d 에서는 이 저장소 보조 스크립트의 파이썬 의존성(python3 버전, fcntl,
#       matplotlib, rdkit)도 함께 본다. 첫 실행에서 막힐 지점을 미리 알기 위한 것이다.
#
# 사용법:
#   bash af3_check.sh                    # 화면 출력
#   bash af3_check.sh > af3_check.txt    # 파일로 저장 (권장, 공유용)
#
# 표기 규칙:
#   [측정]  이 스크립트가 실제로 읽은 값
#   [참고]  공식 문서 기준의 대조용 값 (이 PC의 값이 아님)
#   [경고]  확인이 필요한 항목
# =============================================================================
set -u

FAIL_COUNT=0
fail() {
  echo "[실패] $*"
  FAIL_COUNT=$((FAIL_COUNT + 1))
}

# 이 스크립트가 있는 폴더. 7d 에서 옆에 있는 파이썬 스크립트를 점검할 때 쓴다.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd)"

IMAGE="${AF3_IMAGE:-alphafold3}"
DB_DIR="${AF3_DB_DIR:-$HOME/public_databases_full}"
MODEL_DIR="${AF3_MODEL_DIR:-$HOME/af3_models}"
MODEL_SHA256_EXPECTED="${AF3_MODEL_SHA256:-df8bbf2621f17dd3ee21c2a921e84a50bc2b80cdc0c7971cb915c2826fee1f9b}"

line() { printf '%s\n' "-------------------------------------------------------------------------------"; }
head1() { echo; line; echo "## $*"; line; }

echo "==============================================================================="
echo " AlphaFold 3 환경 진단 리포트"
echo " 생성 시각 : $(date '+%Y-%m-%d %H:%M:%S %Z')"
echo " 실행 사용자: $(id -un)   호스트: $(hostname 2>/dev/null || echo '알수없음')"
echo " 점검 대상  : 이미지=${IMAGE}  DB=${DB_DIR}  가중치=${MODEL_DIR}"
echo "==============================================================================="

# -----------------------------------------------------------------------------
head1 "1. 시스템 기본 정보"
# -----------------------------------------------------------------------------
echo "[측정] OS 커널      : $(uname -srm)"
if [ -r /etc/os-release ]; then
  # shellcheck disable=SC1091
  . /etc/os-release 2>/dev/null || true
  echo "[측정] 배포판       : ${PRETTY_NAME:-알수없음}"
fi

CPU_LOGICAL="$(getconf _NPROCESSORS_ONLN 2>/dev/null || nproc 2>/dev/null || echo '알수없음')"
echo "[측정] 논리 CPU 수  : ${CPU_LOGICAL}"
if [ -r /proc/cpuinfo ]; then
  CPU_MODEL="$(awk -F': ' '/model name/{print $2; exit}' /proc/cpuinfo)"
  echo "[측정] CPU 모델     : ${CPU_MODEL:-알수없음}"
fi
if [ -r /proc/meminfo ]; then
  MEM_KB="$(awk '/MemTotal/{print $2; exit}' /proc/meminfo)"
  MEM_AV_KB="$(awk '/MemAvailable/{print $2; exit}' /proc/meminfo)"
  echo "[측정] 전체 RAM     : $((MEM_KB/1024/1024)) GB (가용 $((MEM_AV_KB/1024/1024)) GB)"
fi
echo "[측정] 홈 파티션 여유:"
df -h "$HOME" 2>/dev/null | sed 's/^/         /'

echo
echo "[참고] MSA(데이터 파이프라인) 단계는 CPU와 RAM을 쓴다. AF3 공식 설치 문서는"
echo "       전체 설치에 최대 1 TB 디스크와 최소 64 GB RAM을 권장한다. 실제 요구량은"
echo "       입력, DB 배치와 저장장치 성능에 따라 달라지므로 이 점검은 하한을 보장하지 않는다."
echo "       공식 AF3의 MSA CPU 기본값은 min(사용 가능 CPU 수, 8)이다. legacy af3_batch.py의"
echo "       자동값 min(논리 CPU 수/2, 8)은 이 저장소의 과거 측정에 따른 별도 정책이다."

# -----------------------------------------------------------------------------
head1 "2. GPU / 드라이버"
# -----------------------------------------------------------------------------
if command -v nvidia-smi >/dev/null 2>&1; then
  echo "[측정] nvidia-smi 요약:"
  nvidia-smi --query-gpu=index,name,driver_version,memory.total,memory.used,memory.free,utilization.gpu,compute_cap \
             --format=csv 2>/dev/null | sed 's/^/         /'
  echo
  echo "[측정] 현재 GPU를 점유 중인 프로세스:"
  nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv 2>/dev/null | sed 's/^/         /'
  echo
  echo "[경고] 여기서 memory.used 가 거의 전체 용량이라도, 그것이 곧 '실제로 그만큼 필요하다'는"
  echo "       뜻은 아니다. AlphaFold 3 공식 Dockerfile 은 XLA_PYTHON_CLIENT_PREALLOCATE=true,"
  echo "       XLA_CLIENT_MEM_FRACTION=0.95 로 설정되어 있어서, 프로세스가 시작하는 순간"
  echo "       실제 사용량과 무관하게 VRAM의 95%를 선점(preallocate)한다. 실사용량을 보려면"
  echo "       아래 7번 항목의 방법으로 선점을 끄고 다시 측정해야 한다."
else
  fail "nvidia-smi 를 찾을 수 없다. NVIDIA 드라이버가 설치되지 않았거나 PATH에 없다."
fi

# -----------------------------------------------------------------------------
head1 "3. Docker 접근성 (sudo 없이 쓸 수 있는가)"
# -----------------------------------------------------------------------------
DOCKER="${AF3_DOCKER:-}"
if [ -n "$DOCKER" ]; then
  if $DOCKER info >/dev/null 2>&1; then
    echo "[측정] 지정 Docker 명령 사용 가능: ${DOCKER}"
  else
    fail "AF3_DOCKER 로 지정한 Docker 명령을 사용할 수 없다: ${DOCKER}"
    DOCKER=""
  fi
elif ! command -v docker >/dev/null 2>&1; then
  fail "docker 명령을 찾을 수 없다. 이후 도커 관련 점검은 모두 건너뛴다."
else
  echo "[측정] docker 버전  : $(docker --version 2>&1 | head -1)"
  if docker info >/dev/null 2>&1; then
    DOCKER="docker"
    echo "[측정] sudo 없이 docker 사용 가능: 예"
  elif sudo -n docker info >/dev/null 2>&1; then
    DOCKER="sudo -n docker"
    echo "[측정] sudo 없이 docker 사용 가능: 아니오 (비대화형 sudo 는 됨)"
  else
    DOCKER=""
    fail "docker 데몬에 비대화형으로 접근할 수 없다. docker 그룹/rootless 설정이 필요하다."
  fi
  echo "[측정] 내 그룹 목록 : $(id -nG 2>/dev/null)"
  if id -nG 2>/dev/null | tr ' ' '\n' | grep -qx docker; then
    echo "[측정] docker 그룹  : 소속됨"
  else
    echo "[경고] docker 그룹에 소속되어 있지 않다. 아래 명령 후 재로그인하면 sudo 없이 쓸 수 있다."
    echo "         sudo usermod -aG docker \$USER   # 실행 후 로그아웃/로그인 (또는 재부팅)"
  fi
fi

# -----------------------------------------------------------------------------
head1 "4. 도커 이미지와 컨테이너 내부 설정"
# -----------------------------------------------------------------------------
HELP_TXT=""
if [ -n "$DOCKER" ]; then
  if $DOCKER image inspect "$IMAGE" >/dev/null 2>&1; then
    echo "[측정] 이미지 '${IMAGE}' 존재: 예"
    $DOCKER image inspect "$IMAGE" \
      --format '         생성시각={{.Created}}  크기={{.Size}} bytes  아키텍처={{.Architecture}}' 2>/dev/null
    echo
    echo "[측정] 이미지에 박혀 있는 XLA/메모리 관련 환경변수:"
    $DOCKER image inspect "$IMAGE" --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null \
      | grep -E 'XLA|TF_FORCE|CUDA|JAX' | sed 's/^/         /' || echo "         (해당 항목 없음)"
    echo
    echo "[측정] 컨테이너 안에서 GPU가 보이는가 (--gpus all 로 확인):"
    $DOCKER run --rm --gpus all "$IMAGE" \
      nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader 2>&1 \
      | head -5 | sed 's/^/         /'
    echo
    echo "[측정] 이 이미지의 run_alphafold.py 가 지원하는 주요 플래그 (실제 --help 에서 추출):"
    HELP_TXT="$($DOCKER run --rm "$IMAGE" python run_alphafold.py --help 2>&1)"
    for f in input_dir json_path output_dir buckets num_diffusion_samples num_recycles \
             num_seeds jax_compilation_cache_dir run_data_pipeline run_inference \
             jackhmmer_n_cpu nhmmer_n_cpu flash_attention_implementation save_embeddings; do
      if printf '%s' "$HELP_TXT" | grep -Fq -e "--${f}" -e "--[no]${f}"; then
        echo "         지원   --${f}"
      else
        echo "         없음   --${f}"
      fi
    done
    echo
    echo "         ※ '없음' 으로 나온 플래그는 이 이미지 버전에서 쓸 수 없다. af3_batch.py 는"
    echo "           같은 방식으로 자동 탐지해서 지원하는 플래그만 전달한다."
    echo
    echo "[측정] run_alphafold.py 가 기대하는 데이터베이스 경로 플래그와 기본값:"
    printf '%s' "$HELP_TXT" | grep -E -- '--[a-z_]*database_path|--db_dir|--pdb_database_path' \
      | sed 's/^[[:space:]]*/         /' | head -40
    if ! grep -Eq -- '--[a-z_]*database_path' <<< "$HELP_TXT"; then
      echo "         (추출 실패 - 아래 명령으로 직접 확인)"
      echo "         ${DOCKER} run --rm ${IMAGE} python run_alphafold.py --help | grep database"
    fi
  else
    fail "이미지 '${IMAGE}' 를 찾을 수 없다. 이름이 다를 수 있으니 아래 목록에서 확인하라."
    $DOCKER images 2>/dev/null | head -15 | sed 's/^/         /'
  fi
fi

# Docker 실행 경로가 실제로 쓰는 patched HMMER 를 컨테이너 안에서 확인한다.
if [ -n "$DOCKER" ] && $DOCKER image inspect "$IMAGE" >/dev/null 2>&1; then
  HMMER_TXT="$($DOCKER run --rm "$IMAGE" jackhmmer -h 2>&1)"
  if printf '%s' "$HMMER_TXT" | grep -q -- '--seq_limit'; then
    echo "[측정] 컨테이너 HMMER : jackhmmer --seq_limit 패치 확인"
  else
    fail "컨테이너 jackhmmer -h 에 --seq_limit 이 없다. AF3 patched HMMER 이미지가 아니다."
  fi
fi

# -----------------------------------------------------------------------------
head1 "5. 데이터베이스 구성 (축소 DB 인지, 무엇이 빠졌는지)"
# -----------------------------------------------------------------------------
if [ ! -d "$DB_DIR" ]; then
  fail "DB 폴더가 없다: ${DB_DIR}"
else
  echo "[측정] DB 폴더      : ${DB_DIR}"
  DB_TOTAL="$(du -sh "$DB_DIR" 2>/dev/null | awk '{print $1}')"
  # du -sb 는 GNU 전용이다. 없으면 du -sk (KB, POSIX) 로 떨어뜨린다.
  DB_TOTAL_BYTES="$(du -sb "$DB_DIR" 2>/dev/null | awk '{print $1}')"
  if [ -z "${DB_TOTAL_BYTES:-}" ]; then
    DB_KB="$(du -sk "$DB_DIR" 2>/dev/null | awk '{print $1}')"
    [ -n "${DB_KB:-}" ] && DB_TOTAL_BYTES=$(( DB_KB * 1024 ))
  fi
  echo "[측정] DB 전체 용량 : ${DB_TOTAL:-알수없음}"
  echo
  echo "[측정] 폴더 안의 실제 파일 목록 (용량순):"
  find "$DB_DIR" -mindepth 1 -maxdepth 1 \
    -printf '%s\t%M\t%u:%g\t%f\n' 2>/dev/null | sort -nr | head -40 | sed 's/^/         /'
  echo
  echo "[측정] 공식 전체 DB 파일 목록과 대조 (파일명 기준):"
  echo "       ※ 아래 목록은 [참고] 값이다. 공식 fetch_databases.sh 가 내려받는 파일들이며,"
  echo "         reduced-MSA overlay를 첫 root로 쓰면 이 root에서 일부가 '없음'일 수 있다."
  echo "         이 경우 AF3_DB_FALLBACK_DIRS의 뒤 root에서 반드시 해소되어야 한다."
  # 공식 fetch_databases.sh 가 내려받는 항목 (참고용 체크리스트)
  EXPECTED="bfd-first_non_consensus_sequences.fasta
mgy_clusters_2022_05.fa
uniref90_2022_05.fa
uniprot_all_2021_04.fa
pdb_seqres_2022_09_28.fasta
nt_rna_2023_02_23_clust_seq_id_90_cov_80_rep_seq.fasta
rfam_14_9_clust_seq_id_90_cov_80_rep_seq.fasta
rnacentral_active_seq_id_90_cov_80_linclust.fasta
mmcif_files"
  MISSING_LIST=""
  while IFS= read -r name; do
    [ -z "$name" ] && continue
    path="${DB_DIR}/${name}"
    if [ -e "$path" ]; then
      if [ -d "$path" ]; then
        if [ "$name" = "mmcif_files" ]; then
          n="$(find "$path" -maxdepth 1 -type f -name '*.cif' 2>/dev/null | wc -l | tr -d ' ')"
          count_label="CIF"
        else
          n="$(find "$path" -maxdepth 1 -type f 2>/dev/null | wc -l | tr -d ' ')"
          count_label="파일"
        fi
        sz="$(du -sh "$path" 2>/dev/null | awk '{print $1}')"
        printf '         있음   %-60s %8s (%s %s개)\n' \
          "$name" "${sz:-?}" "$count_label" "$n"
      else
        sz="$(du -sh "$path" 2>/dev/null | awk '{print $1}')"
        printf '         있음   %-60s %8s\n' "$name" "${sz:-?}"
      fi
    else
      # 이름이 비슷한 대체 파일이 있는지도 같이 본다 (축소판은 이름이 다를 수 있음)
      base="$(printf '%s' "$name" | cut -c1-6)"
      alt="$(find "$DB_DIR" -mindepth 1 -maxdepth 1 -printf '%f\n' 2>/dev/null \
        | grep -i "^${base}" | tr '\n' ',' | sed 's/,$//')"
      if [ -n "$alt" ]; then
        printf '         없음   %-60s (비슷한 이름: %s)\n' "$name" "$alt"
      else
        printf '         없음   %-60s\n' "$name"
      fi
      MISSING_LIST="${MISSING_LIST}${name} "
    fi
  done <<EOF
$EXPECTED
EOF
  echo
  if [ -n "${DB_TOTAL_BYTES:-}" ]; then
    # 크기는 참고용 휴리스틱일 뿐이다. 완전성은 아래 af3_db.py 검증으로만 판정한다.
    if [ "$DB_TOTAL_BYTES" -lt 107374182400 ]; then
      echo "[측정→참고] 첫 DB root가 100 GB 미만이다. reduced overlay일 수도, 불완전한"
      echo "            다운로드일 수도 있다. DB 크기만으로 완전성을 판정할 수 없다."
    else
      echo "[측정→참고] 첫 DB root가 100 GB 이상이다. 크기가 커도 필수 파일의 존재·안전성을"
      echo "            보장하지 않는다. DB 크기만으로 완전성을 판정할 수 없다."
    fi
  fi
  if [ -n "$MISSING_LIST" ]; then
    echo
    echo "[측정] 없는 항목 정리: ${MISSING_LIST}"
    echo "       ※ 없는 DB가 어느 검색 단계에 쓰이는지:"
    echo "         mgy_clusters / uniref90 / bfd  -> 단백질 unpaired MSA (jackhmmer)"
    echo "         uniprot_all                    -> 복합체용 paired MSA"
    echo "         pdb_seqres + mmcif_files       -> 템플릿 검색 (뒤 fallback root에서 필요)"
    echo "         nt_rna / rfam / rnacentral     -> RNA MSA (현재 러너의 엄격 사전점검 대상)"
  fi
fi

DB_PY="${AF3_PYTHON:-$(command -v python3 2>/dev/null || command -v python 2>/dev/null || true)}"
if [ -n "$DB_PY" ] && [ -f "${SCRIPT_DIR}/af3_db.py" ]; then
  DB_VERIFY_ARGS=(verify --db-dir "$DB_DIR")
  if [ -n "${AF3_DB_FALLBACK_DIRS:-}" ]; then
    IFS=':' read -r -a DB_FALLBACKS <<< "$AF3_DB_FALLBACK_DIRS"
    for fallback in "${DB_FALLBACKS[@]}"; do
      [ -n "$fallback" ] && DB_VERIFY_ARGS+=(--db-dir "$fallback")
    done
  fi
  if "$DB_PY" "${SCRIPT_DIR}/af3_db.py" "${DB_VERIFY_ARGS[@]}" >/dev/null 2>&1; then
    echo "[측정] AF3 DB 필수 9항목: ordered root 전체 확인"
  else
    fail "AF3 DB 필수 9항목이 ordered root에서 모두 확인되지 않는다. af3_db.py verify 를 실행하라."
  fi
fi

# -----------------------------------------------------------------------------
head1 "6. 가중치 (모델 파라미터)"
# -----------------------------------------------------------------------------
if [ ! -d "$MODEL_DIR" ]; then
  fail "가중치 폴더가 없다: ${MODEL_DIR}"
else
  echo "[측정] 가중치 폴더  : ${MODEL_DIR}"
  find "$MODEL_DIR" -mindepth 1 -maxdepth 1 \
    -printf '%s\t%M\t%u:%g\t%f\n' 2>/dev/null | sort -nr | sed 's/^/         /'
  BIG="$(find "$MODEL_DIR" -maxdepth 1 -type f -size +400M 2>/dev/null | head -5)"
  if [ -n "$BIG" ]; then
    echo "[측정] 400MB 이상 파일(가중치 본체로 추정):"
    printf '%s\n' "$BIG" | sed 's/^/         /'
  else
    fail "400MB 이상 파일이 없다. 가중치가 제대로 놓이지 않았을 수 있다."
    echo "       (AlphaFold 3 가중치는 1 GB 급 단일 파일이다.)"
  fi
  MODEL_FILE="${MODEL_DIR}/af3.bin"
  if [ ! -f "$MODEL_FILE" ] || [ -L "$MODEL_FILE" ]; then
    fail "일반 파일 af3.bin 이 없다: ${MODEL_FILE}"
  else
    MODEL_BYTES="$(wc -c < "$MODEL_FILE" 2>/dev/null || echo 0)"
    if [ "$MODEL_BYTES" -ne 1146811260 ]; then
      fail "af3.bin 크기가 pinned release 값과 다르다: ${MODEL_BYTES} (기대 1146811260)"
    else
      echo "[측정] af3.bin 크기   : ${MODEL_BYTES} bytes (정상)"
      if ! command -v sha256sum >/dev/null 2>&1; then
        fail "sha256sum 명령이 없어 af3.bin 무결성을 확인할 수 없다."
      else
        MODEL_SHA256_ACTUAL="$(sha256sum -- "$MODEL_FILE" 2>/dev/null | awk '{print $1}')"
        if [ "$MODEL_SHA256_ACTUAL" != "$MODEL_SHA256_EXPECTED" ]; then
          fail "af3.bin SHA-256 불일치: ${MODEL_SHA256_ACTUAL:-계산실패} (기대 ${MODEL_SHA256_EXPECTED})"
        else
          echo "[측정] af3.bin SHA-256: ${MODEL_SHA256_ACTUAL} (정상)"
        fi
      fi
    fi
  fi
fi

# -----------------------------------------------------------------------------
head1 "7. 실사용 VRAM 측정 방법 (대표 입력 1건)"
# -----------------------------------------------------------------------------
cat <<'GUIDE'
2번 항목에서 VRAM이 꽉 차 보였다면, 그것이 선점(preallocate)인지 실사용인지 아래로 구분한다.

  터미널 A - 선점을 끄고 1건만 실행:
    docker run --rm --gpus all \
      --user $(id -u):$(id -g) -e HOME=/tmp \
      -e XLA_PYTHON_CLIENT_PREALLOCATE=false \
      -e XLA_CLIENT_MEM_FRACTION=1.0 \
      -v $HOME/public_databases_full:/af3/db:ro \
      -v $HOME/af3_models:/af3/models:ro \
      -v $PWD/vhh_001_in:/af3/in:ro \
      -v $PWD/vhh_001_out:/af3/out \
      alphafold3 python run_alphafold.py \
        --json_path=/af3/in/<하나만>.json \
        --model_dir=/af3/models --db_dir=/af3/db \
        --output_dir=/af3/out
    (--user 가 없으면 결과가 root 소유가 되고, 마운트를 /root 아래 두면
     non-root 가 못 들어간다. 러너가 하는 것과 같은 조합이다)

  터미널 B - 1초마다 실제 사용량 기록:
    nvidia-smi --query-gpu=timestamp,memory.used,utilization.gpu \
               --format=csv -l 1 | tee vram_trace.csv

  해석:
    - 선점을 끈 상태의 최대 memory.used 가 '실제로 필요한 VRAM' 이다.
    - 짧은 단량체의 측정값을 긴 서열·복합체에 일반화하지 않는다. 실제 배치와 같은
      입력 종류, diffusion sample, recycle, bucket으로 대표 1건을 재야 한다.
    - 메모리가 남는 것만으로 프로세스를 여러 개 띄우지 않는다. 먼저 단일 프로세스
      --input_dir 순회의 처리량을 재고, 병렬 실행은 별도 A/B 측정으로 결정한다.
    - utilization.gpu 가 0% 인 구간이 길면 그 구간은 GPU가 아니라 CPU(MSA)나
      컴파일/로딩으로 시간을 쓰고 있다는 뜻이다. 이 구간 길이가 곧 최적화 여지다.
GUIDE

# -----------------------------------------------------------------------------
head1 "7b. 입력 폴더 위생 검사 (AppleDouble 사이드카)"
cat <<'EOS'
macOS 에서 만든 tar.gz 를 리눅스에서 풀면 파일마다 '._' 로 시작하는 AppleDouble
사이드카가 함께 생긴다. ls 에는 보이지 않지만 glob("*.json") 에는 잡히고,
UTF-8 이 아니어서 읽는 순간 UnicodeDecodeError 로 죽는다.
이 함정으로 벤치마크 측정 3시간이 통째로 실패한 사례가 있다.
EOS
FOUND_INPUT_DIR=0
for d in ./*_in; do
  [ -d "$d" ] || continue
  FOUND_INPUT_DIR=1
  n_side=$(find "$d" -maxdepth 1 -type f -name '._*' -printf '.' 2>/dev/null | wc -c)
  n_real=$(find "$d" -maxdepth 1 -type f -name '*.json' ! -name '._*' -printf '.' 2>/dev/null | wc -c)
  printf "  %-24s 정상 JSON %s건, 사이드카 %s건" "$d" "$n_real" "$n_side"
  if [ "${n_side:-0}" -gt 0 ]; then
    printf "   <- 문제. 아래로 지울 것:\n"
    printf "        find %s -name '._*' -delete\n" "$d"
  else
    printf "   (정상)\n"
  fi
done
[ "$FOUND_INPUT_DIR" -eq 1 ] || echo "  (*_in 폴더가 없다. 입력 폴더를 만든 뒤 다시 실행하라)"

head1 "7c. 패딩 버킷 확인"
cat <<'EOS'
run_alphafold.py 의 기본 버킷 사다리는 128 에서 시작한다: 128,256,384,512,768,...
더 큰 버킷은 더 많은 연산·메모리를 요구하지만 배수는 GPU와 입력 설정마다 다르다.
--buckets 를 손으로 줄 때 '128' 을 빼면 짧은 서열도 256 으로 패딩된다.
    올바름:  --buckets=128,256
    잘못됨:  --buckets=256
EOS
for d in ./*_in; do
  [ -d "$d" ] || continue
  python3 - "$d" <<'PYEOS' 2>/dev/null || echo "  (python3 로 길이를 셀 수 없었다. 건너뜀)"
import json, sys, glob, os
from collections import Counter
LADDER = [128,256,384,512,768,1024,1280,1536,2048,2560,3072,3584,4096,4608,5120]
d = sys.argv[1]
cnt, bad = Counter(), 0
for p in sorted(glob.glob(os.path.join(d, "*.json"))):
    if os.path.basename(p).startswith("._"):
        continue
    try:
        o = json.load(open(p, encoding="utf-8"))
    except Exception:
        bad += 1
        continue
    t = 0
    for e in o.get("sequences") or []:
        for k, b in (e or {}).items():
            if isinstance(b, dict) and k in ("protein","rna","dna"):
                ids = b.get("id")
                t += len(b.get("sequence") or "") * (len(ids) if isinstance(ids, list) else 1)
    fit = [x for x in LADDER if x >= t]
    cnt[min(fit) if fit else t] += 1
if cnt:
    print("  %s : 필요한 버킷 = %s" % (d, ",".join(str(b) for b in sorted(cnt))))
    for b in sorted(cnt):
        print("      버킷 %-6s %4d건" % (b, cnt[b]))
    print("      -> 손으로 쓸 때: --buckets=%s" % ",".join(str(b) for b in sorted(cnt)))
if bad:
    print("      읽을 수 없는 JSON %d건 (사이드카일 가능성)" % bad)
PYEOS
done

# -----------------------------------------------------------------------------
head1 "7d. 이 저장소 스크립트의 파이썬 의존성"
# -----------------------------------------------------------------------------
cat <<'EOS'
AF3 본체(도커/conda)와 별개로, 이 저장소의 보조 스크립트가 돌아가는지 본다.
결론부터: 핵심 Python 스크립트는 표준 라이브러리만으로 import·실행된다.
그림을 실제로 그리는 af3_visualize.py만 matplotlib을 선택적으로 사용한다.
(이 진단 스크립트 af3_check.sh 자체는 bash 라서 파이썬 의존성이 없다.)
EOS
echo

PY3="${AF3_PYTHON:-}"
if [ -z "$PY3" ]; then
  for cand in python3 python; do
    if command -v "$cand" >/dev/null 2>&1; then PY3="$cand"; break; fi
  done
fi

if [ -z "$PY3" ]; then
  fail "python3 을 찾을 수 없다. 이 저장소의 스크립트를 하나도 쓸 수 없다."
  echo "       설치:  sudo apt install python3      (우분투/데비안)"
else
  PY_VER="$("$PY3" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' 2>/dev/null || echo '알수없음')"
  PY_PATH="$(command -v "$PY3")"
  echo "[측정] python3 경로     : ${PY_PATH}"
  echo "[측정] python3 버전     : ${PY_VER}"
  # 3.8 미만이면 f-string 및 dataclasses 사용 부분에서 문제가 생길 수 있다.
  if "$PY3" -c 'import sys; sys.exit(0 if sys.version_info[:2] >= (3, 8) else 1)' 2>/dev/null; then
    echo "         -> 3.8 이상. 이 저장소의 스크립트 요구 조건을 만족한다."
  else
    echo "[경고]   -> 3.8 미만이다. 이 저장소의 스크립트는 3.8 이상을 가정한다."
  fi

  # fcntl: run_af3_batch_improved.py 의 중복 실행 방지(flock)에 필요하다.
  if "$PY3" -c 'import fcntl' 2>/dev/null; then
    echo "[측정] fcntl 모듈       : 있다 (run_af3_batch_improved.py 의 중복 실행 방지가 동작한다)"
  else
    fail "fcntl 모듈이 없다. run_af3_batch_improved.py 를 쓸 수 없다"
    echo "                          (윈도우 기본 파이썬이면 그렇다. 리눅스/macOS 에서 돌려라)"
  fi

  if "$PY3" -m pip --version >/dev/null 2>&1; then
    echo "[측정] pip              : 있다 ($("$PY3" -m pip --version 2>/dev/null | head -1))"
  else
    echo "[참고] pip              : 이 Python에는 없다. 핵심 스크립트에는 필요 없고,"
    echo "                          Ubuntu 그림 환경은 apt의 python3-matplotlib을 쓴다."
  fi

  echo
  echo "[측정] 저장소 Python 스크립트 문법 검사:"
  for s in run_af3_batch_improved.py af3_batch.py af3_db.py af3_collect.py af3_prepare.py \
           af3_stage2.py af3_rankcorr.py af3_view3d.py af3_visualize.py; do
    SP="${SCRIPT_DIR:-$(cd "$(dirname "$0")" && pwd)}/$s"
    if [ ! -f "$SP" ]; then
      printf "         %-30s 파일 없음\n" "$s"
    elif "$PY3" -c "import py_compile,sys; py_compile.compile(sys.argv[1], doraise=True)" "$SP" >/dev/null 2>&1; then
      printf "         %-30s 정상 (불러올 수 있다)\n" "$s"
    else
      printf "         %-30s 문법 오류. 파일이 손상됐을 수 있다\n" "$s"
    fi
  done

  echo
  # matplotlib: 최상위 패키지만 보면 부족하다. 실제 그리기에 쓰는 pyplot 까지 확인한다.
  MPL_OUT="$("$PY3" - <<'PYEOS' 2>&1
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot  # 실제 그리기 경로
    print("OK %s" % matplotlib.__version__)
except Exception as e:
    print("FAIL %s: %s" % (type(e).__name__, e))
PYEOS
)"
  case "$MPL_OUT" in
    OK*)
      echo "[측정] matplotlib       : 있다 (버전 ${MPL_OUT#OK })"
      echo "                          -> af3_visualize.py 로 그림을 그릴 수 있다."
      ;;
    *)
      echo "[경고] matplotlib       : 쓸 수 없다"
      echo "                          이유: ${MPL_OUT#FAIL }"
      echo "                          -> af3_visualize.py 는 죽지 않지만 그림 없이"
      echo "                             표(visualize_table.csv)와 뷰어 스크립트만 만든다."
      echo "                          그림이 필요하면 AF3 추론 환경과 분리된 venv를 쓴다:"
      echo "                             sudo apt install python3-matplotlib python3-venv"
      echo "                             python3 -m venv --without-pip --system-site-packages ~/af3_plot_env"
      echo "                          이미 matplotlib이 있는 Python은 AF3_PYTHON=/경로/python 으로 점검한다."
      ;;
  esac

  # 리간드(SMILES)를 쓸 때만 필요한 선택 의존성이다.
  if "$PY3" -c 'import rdkit' 2>/dev/null; then
    echo "[측정] rdkit (선택)     : 있다. af3_prepare.py 의 SMILES heavy atom 수를 정확히 센다"
  else
    echo "[참고] rdkit (선택)     : 없다. 단백질만 돌리면 상관없다."
    echo "                          --smiles 로 리간드를 넣을 때만 heavy atom 수가 빈칸이 된다."
  fi
fi

echo
echo "[참고] 그림 의존성은 AF3 추론 환경과 분리된 venv에 설치한다 (저장소 최상위에서):"
echo "         sudo apt install python3-matplotlib python3-venv"
echo "         python3 -m venv --without-pip --system-site-packages ~/af3_plot_env"
echo "       어느 스크립트가 무엇을 필요로 하는지는 docs/dependencies_notes.md 에 있다."

# -----------------------------------------------------------------------------
head1 "8. 종합"
# -----------------------------------------------------------------------------
cat <<'SUMMARY'
이 리포트에서 확인해야 할 핵심 5가지:
  1) DB 완전성 - DB 크기만으로 완전성을 판정할 수 없다. af3_db.py verify 결과를 본다.
     overlay를 쓰면
     ordered fallback root까지 합쳐 필수 9항목이 모두 해소되어야 한다.
  2) --input_dir 플래그 지원 여부 - 지원되면 컨테이너 1회 기동으로 전수 순회가 가능하다.
     이것이 가장 큰 개선 항목이다.
  3) docker 그룹 소속 여부 - sudo 없이 돌릴 수 있으면 배치 스크립트가 단순해진다.
  4) 논리 CPU 수 - 공식 MSA 도구 기본값은 min(사용 가능 CPU 수, 8)이다. 병렬 갈래와
     수동 override의 이득은 해당 장비에서 따로 측정한다.
  5) 7d 의 python3 과 matplotlib - 이 저장소의 보조 스크립트가 첫 실행에서 막히는지.
     matplotlib 이 없어도 집계(CSV)는 전부 되고 그림만 안 된다.

이 파일을 그대로 저장해서 공유하면 추가 진단에 쓸 수 있다.
SUMMARY

echo
echo "==============================================================================="
echo " 진단 종료: $(date '+%Y-%m-%d %H:%M:%S')"
echo "==============================================================================="
if [ "$FAIL_COUNT" -gt 0 ]; then
  echo "[실패] 필수 점검 ${FAIL_COUNT}개가 통과하지 못했다. 위 첫 실패부터 해결하라."
  exit 1
fi
echo "[OK] 필수 환경 점검을 모두 통과했다."
exit 0
