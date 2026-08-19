#!/usr/bin/env bash
# =============================================================================
# af3_check.sh - AlphaFold 3 실행 환경 진단 (연구자 PC에서 1회 실행)
#
# 목적: GPU/드라이버, 도커 접근성, 도커 이미지 내부 설정, 데이터베이스 구성,
#       가중치, CPU/RAM/디스크를 한 화면에 찍어서 "무엇이 있고 무엇이 없는지"를
#       사실로 확인한다.
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

IMAGE="${AF3_IMAGE:-alphafold3}"
DB_DIR="${AF3_DB_DIR:-$HOME/public_databases}"
MODEL_DIR="${AF3_MODEL_DIR:-$HOME/af3_models}"

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
echo "[참고] MSA(데이터 파이프라인) 단계는 CPU와 RAM을 쓴다. 공식 문서는 전체 DB 사용 시"
echo "       수백 GB 급 RAM/디스크를 권장하지만, 축소 DB를 쓰면 요구량이 크게 줄어든다."
echo "       위 논리 CPU 수는 --jackhmmer_n_cpu 값을 결정한다: min(코어수/2, 8)."
echo "       동시 실행 갈래는 1개가 최적이다 (실측: 같은 스레드 총량에서 갈래를"
echo "       늘리면 오히려 느리다. 32스레드 1갈래 0.890 대 2갈래 0.767 타깃/분)."
echo "       AF3 가 이미 체인당 DB 4개를 내부 병렬 검색하므로 병렬성이 안에 있다."

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
  echo "       아래 6번 항목의 방법으로 선점을 끄고 다시 측정해야 한다."
else
  echo "[경고] nvidia-smi 를 찾을 수 없다. NVIDIA 드라이버가 설치되지 않았거나 PATH에 없다."
fi

# -----------------------------------------------------------------------------
head1 "3. Docker 접근성 (sudo 없이 쓸 수 있는가)"
# -----------------------------------------------------------------------------
DOCKER=""
if ! command -v docker >/dev/null 2>&1; then
  echo "[경고] docker 명령을 찾을 수 없다. 이후 도커 관련 점검은 모두 건너뛴다."
else
  echo "[측정] docker 버전  : $(docker --version 2>&1 | head -1)"
  if docker info >/dev/null 2>&1; then
    DOCKER="docker"
    echo "[측정] sudo 없이 docker 사용 가능: 예"
  elif sudo -n docker info >/dev/null 2>&1; then
    DOCKER="sudo docker"
    echo "[측정] sudo 없이 docker 사용 가능: 아니오 (암호 없는 sudo 는 됨)"
  else
    DOCKER="sudo docker"
    echo "[측정] sudo 없이 docker 사용 가능: 아니오 (sudo 암호가 필요할 수 있음)"
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
      if printf '%s' "$HELP_TXT" | grep -q -- "--${f}"; then
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
    if [ -z "$(printf '%s' "$HELP_TXT" | grep -E -- '--[a-z_]*database_path')" ]; then
      echo "         (추출 실패 - 아래 명령으로 직접 확인)"
      echo "         ${DOCKER} run --rm ${IMAGE} python run_alphafold.py --help | grep database"
    fi
  else
    echo "[경고] 이미지 '${IMAGE}' 를 찾을 수 없다. 이름이 다를 수 있으니 아래 목록에서 확인하라."
    $DOCKER images 2>/dev/null | head -15 | sed 's/^/         /'
  fi
fi

# -----------------------------------------------------------------------------
head1 "5. 데이터베이스 구성 (축소 DB 인지, 무엇이 빠졌는지)"
# -----------------------------------------------------------------------------
if [ ! -d "$DB_DIR" ]; then
  echo "[경고] DB 폴더가 없다: ${DB_DIR}"
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
  ls -lLS "$DB_DIR" 2>/dev/null | sed 's/^/         /' | head -40
  echo
  echo "[측정] 공식 전체 DB 파일 목록과 대조 (파일명 기준):"
  echo "       ※ 아래 목록은 [참고] 값이다. 공식 fetch_databases.sh 가 내려받는 파일들이며,"
  echo "         축소 DB를 쓰는 경우 상당수가 '없음' 으로 나오는 것이 정상이다."
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
        n="$(find "$path" -maxdepth 1 -type f 2>/dev/null | wc -l | tr -d ' ')"
        sz="$(du -sh "$path" 2>/dev/null | awk '{print $1}')"
        printf '         있음   %-60s %8s (파일 %s개)\n' "$name" "${sz:-?}" "$n"
      else
        sz="$(du -sh "$path" 2>/dev/null | awk '{print $1}')"
        printf '         있음   %-60s %8s\n' "$name" "${sz:-?}"
      fi
    else
      # 이름이 비슷한 대체 파일이 있는지도 같이 본다 (축소판은 이름이 다를 수 있음)
      base="$(printf '%s' "$name" | cut -c1-6)"
      alt="$(ls -1 "$DB_DIR" 2>/dev/null | grep -i "^${base}" | tr '\n' ',' | sed 's/,$//')"
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
    # 공식 전체 DB 는 대략 630 GB 급. 100 GB 미만이면 축소 DB 로 판정.
    if [ "$DB_TOTAL_BYTES" -lt 107374182400 ]; then
      echo "[측정→판정] DB 총량이 100 GB 미만이다. 공식 전체 DB(대략 630 GB 급)가 아니라"
      echo "            축소 DB를 쓰고 있는 것이 맞다."
      echo "[경고] 축소 DB의 실제 영향:"
      echo "       (1) 좋은 쪽 - MSA 검색 시간이 짧다. 즉 건당 시간의 대부분은 MSA가 아니다."
      echo "       (2) 나쁜 쪽 - MSA 깊이가 얕아진다. VHH/나노바디는 MSA 깊이에 따라 신뢰도"
      echo "           지표(pLDDT/pTM)가 달라질 수 있다. 상위 후보를 최종 확정할 때는 전체 DB"
      echo "           또는 더 큰 DB로 재계산한 결과와 비교하는 것이 안전하다."
    else
      echo "[측정→판정] DB 총량이 100 GB 이상이다. 전체 DB에 가까운 구성으로 보인다."
    fi
  fi
  if [ -n "$MISSING_LIST" ]; then
    echo
    echo "[측정] 없는 항목 정리: ${MISSING_LIST}"
    echo "       ※ 없는 DB가 어느 검색 단계에 쓰이는지:"
    echo "         mgy_clusters / uniref90 / bfd  -> 단백질 unpaired MSA (jackhmmer)"
    echo "         uniprot_all                    -> 복합체용 paired MSA"
    echo "         pdb_seqres + mmcif_files       -> 템플릿 검색 (없으면 템플릿 없이 예측)"
    echo "         nt_rna / rfam / rnacentral     -> RNA MSA (단백질만 예측하면 불필요)"
  fi
fi

# -----------------------------------------------------------------------------
head1 "6. 가중치 (모델 파라미터)"
# -----------------------------------------------------------------------------
if [ ! -d "$MODEL_DIR" ]; then
  echo "[경고] 가중치 폴더가 없다: ${MODEL_DIR}"
else
  echo "[측정] 가중치 폴더  : ${MODEL_DIR}"
  ls -lL "$MODEL_DIR" 2>/dev/null | sed 's/^/         /'
  BIG="$(find "$MODEL_DIR" -maxdepth 1 -type f -size +400M 2>/dev/null | head -5)"
  if [ -n "$BIG" ]; then
    echo "[측정] 400MB 이상 파일(가중치 본체로 추정):"
    printf '%s\n' "$BIG" | sed 's/^/         /'
  else
    echo "[경고] 400MB 이상 파일이 없다. 가중치가 제대로 놓이지 않았을 수 있다."
    echo "       (AlphaFold 3 가중치는 1 GB 급 단일 파일이다.)"
  fi
fi

# -----------------------------------------------------------------------------
head1 "7. 실사용 VRAM 측정 방법 (선점 해제)"
# -----------------------------------------------------------------------------
cat <<'GUIDE'
2번 항목에서 VRAM이 꽉 차 보였다면, 그것이 선점(preallocate)인지 실사용인지 아래로 구분한다.

  터미널 A - 선점을 끄고 1건만 실행:
    docker run --rm --gpus all \
      -e XLA_PYTHON_CLIENT_PREALLOCATE=false \
      -e XLA_CLIENT_MEM_FRACTION=1.0 \
      -v $HOME/public_databases:/root/public_databases \
      -v $HOME/af3_models:/root/af3_models \
      -v $PWD/vhh_001_in:/root/af3_in \
      -v $PWD/vhh_001_out:/root/af3_out \
      alphafold3 python run_alphafold.py \
        --json_path=/root/af3_in/<하나만>.json \
        --model_dir=/root/af3_models --db_dir=/root/public_databases \
        --output_dir=/root/af3_out

  터미널 B - 0.5초마다 실제 사용량 기록:
    nvidia-smi --query-gpu=timestamp,memory.used,utilization.gpu \
               --format=csv -l 1 | tee vram_trace.csv

  해석:
    - 선점을 끈 상태의 최대 memory.used 가 '실제로 필요한 VRAM' 이다.
    - 실측: 선점 OFF 에서 130 aa VHH 단량체(패딩 후 128 토큰) 피크가 2,942~2,963 MiB
     였다(18런 전체). 보수적으로 3~5.3 GB 로 보면 되고, 어느 값이든 32 GB 에 여유가 크다.
    - 주의: 메모리가 남는다는 것을 '프로세스를 여러 개 띄울 근거'로 쓰지 말 것.
     프로세스를 하나 더 띄우면 기동 비용 9.1초/건을 그만큼 다시 낸다(실측).
     단일 프로세스 --input_dir 순회가 답이다.
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
for d in ./*_in; do
  [ -d "$d" ] || continue
  n_side=$(ls -a "$d" 2>/dev/null | grep -c '^\._' || true)
  n_real=$(ls "$d"/*.json 2>/dev/null | grep -vc '/\._' || true)
  printf "  %-24s 정상 JSON %s건, 사이드카 %s건" "$d" "$n_real" "$n_side"
  if [ "${n_side:-0}" -gt 0 ]; then
    printf "   <- 문제. 아래로 지울 것:\n"
    printf "        find %s -name '._*' -delete\n" "$d"
  else
    printf "   (정상)\n"
  fi
done
[ -d ./*_in ] 2>/dev/null || echo "  (*_in 폴더가 없다. 입력 폴더를 만든 뒤 다시 실행하라)"

head1 "7c. 패딩 버킷 확인 (2.25배를 가르는 지점)"
cat <<'EOS'
run_alphafold.py 의 기본 버킷 사다리는 128 에서 시작한다: 128,256,384,512,768,...
실측(RTX 5070 Ti, sample 5 x recycle 10, 130 aa VHH):
    버킷 128 정상상태 추론  4.20초
    버킷 256 정상상태 추론  9.44초   <- 2.25배 느리다
즉 서열이 128 토큰에 들어가느냐가 정렬 순서보다 훨씬 중요하다.
--buckets 를 손으로 줄 때 '128' 을 빼면 짧은 서열도 256 으로 패딩되어 2배 이상 느려진다.
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

head1 "8. 종합"
# -----------------------------------------------------------------------------
cat <<'SUMMARY'
이 리포트에서 확인해야 할 핵심 4가지:
  1) DB 총량 - 100 GB 미만이면 축소 DB. MSA 단계는 짧고, 건당 시간의 대부분은
     컨테이너/컴파일/로딩 고정 오버헤드다.
  2) --input_dir 플래그 지원 여부 - 지원되면 컨테이너 1회 기동으로 전수 순회가 가능하다.
     이것이 가장 큰 개선 항목이다.
  3) docker 그룹 소속 여부 - sudo 없이 돌릴 수 있으면 배치 스크립트가 단순해진다.
  4) 논리 CPU 수 - --jackhmmer_n_cpu = min(코어수/2, 8) 을 결정한다. 갈래는 1개.

이 파일을 그대로 저장해서 공유하면 추가 진단에 쓸 수 있다.
SUMMARY

echo
echo "==============================================================================="
echo " 진단 종료: $(date '+%Y-%m-%d %H:%M:%S')"
echo "==============================================================================="
