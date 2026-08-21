#!/usr/bin/env bash
# =============================================================================
# af3run.sh - AlphaFold 3 배치 실행 래퍼 (인자 최소화)
#
# 가장 짧은 사용법 - 작업 이름만 준다:
#     bash af3run.sh vhh_001
#   -> ./vhh_001_in 의 JSON 전부를, 컨테이너 1회 기동으로 순회 실행한다.
#
# 두 번째 인자로 모드를 준다:
#     bash af3run.sh vhh_001 check     환경 진단만 (af3_check.sh 실행)
#     bash af3run.sh vhh_001 dry       실제 실행 없이 명령만 확인 (권장 첫 단계)
#     bash af3run.sh vhh_001 screen    경량 스크리닝 (sample 1, recycle 3) - 전수용
#     bash af3run.sh vhh_001 full      기본값 정밀 (sample 5, recycle 10) - 상위 후보용
#     bash af3run.sh vhh_001 msa       MSA(CPU)만 미리 계산해서 보관
#     bash af3run.sh vhh_001 infer     보관된 MSA로 추론(GPU)만 실행
#     bash af3run.sh vhh_001 oneshot   MSA+추론을 한 프로세스에서 (가장 단순)
#     bash af3run.sh vhh_001 retry     실패한 것만 재시도
#     bash af3run.sh vhh_001 bench     앞 20건만 돌려 건당 시간 측정
#     bash af3run.sh vhh_001 collect   결과를 CSV 한 장으로 집계 (등급 열 포함)
#
# 폴더 관례 (기존 구조를 그대로 쓴다):
#     ./<이름>_in    입력 JSON
#     ./<이름>_out   결과
#     ./<이름>_work  작업 공간(로그, MSA 보관, 요약 CSV)
#     ~/public_databases_full, ~/af3_models
# =============================================================================
set -uo pipefail

NAME="${1:-}"
MODE="${2:-screen}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${AF3_PYTHON:-python3}"
BATCH="${SCRIPT_DIR}/af3_batch.py"
CHECK="${SCRIPT_DIR}/af3_check.sh"

# MSA 동시 갈래 수: 실측 결과 1갈래가 최적이다.
# AF3 는 이미 체인당 DB 4개를 내부에서 병렬 검색하므로(ThreadPoolExecutor max_workers=4),
# 갈래를 늘려도 스레드 총량이 같으면 오히려 느려진다:
#   32스레드 1갈래 0.890 타깃/분  대  32스레드 2갈래 0.767 타깃/분 (실측)
# 처리율은 총 스레드가 코어 수의 약 1.3배인 지점에서 0.895 타깃/분으로 포화한다.
# (이 스윕은 전체 DB 급 = 4종 각 4GB 슬라이스 기준이다. 축소 DB 약 2GB 는
#  데이터 파이프라인이 건당 1.98초로, 포화점이 문제되지 않는다.)
WORKERS="${AF3_MSA_WORKERS:-1}"

# DB 검색 스레드: min(코어수/2, 8). AF3 기본값 min(코어수,8) 과 거의 같아서
# 8코어 이상이면 손대지 않아도 최적에 가깝다.
CORES="$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 8)"
NCPU="${AF3_MSA_NCPU:-$(( CORES / 2 ))}"
[ "$NCPU" -lt 1 ] && NCPU=1
[ "$NCPU" -gt 8 ] && NCPU=8

usage() {
  sed -n '/^# ==*$/,/^# ==*$/p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  exit 1
}

[ -z "$NAME" ] && usage

if [ "$MODE" = "check" ]; then
  if [ ! -f "$CHECK" ]; then
    echo "오류: af3_check.sh 를 찾을 수 없다: ${CHECK}"; exit 1
  fi
  echo "환경 진단을 실행한다. 결과는 af3_check.txt 에도 저장된다."
  bash "$CHECK" 2>&1 | tee af3_check.txt
  exit "${PIPESTATUS[0]}"
fi

if [ "$MODE" = "collect" ]; then
  COLLECT="${SCRIPT_DIR}/af3_collect.py"
  if [ ! -f "$COLLECT" ]; then
    echo "오류: af3_collect.py 를 찾을 수 없다: ${COLLECT}"; exit 1
  fi
  if [ ! -d "./${NAME}_out" ]; then
    echo "오류: 결과 폴더가 없다: ./${NAME}_out"; exit 1
  fi
  # 2026-04: 집계 CSV 이름을 ASCII 로 바꿨다 (${NAME}_결과요약.csv -> ${NAME}_summary.csv).
  # 옛 이름이 필요하면 AF3RUN_FILENAME_LANG=ko 를 주면 된다.
  if [ "${AF3RUN_FILENAME_LANG:-en}" = "ko" ]; then
    CSV="./${NAME}_결과요약.csv"
  else
    CSV="./${NAME}_summary.csv"
  fi
  echo "결과를 집계한다: ./${NAME}_out -> ${CSV}"
  echo "(MSA 깊이 계산이 느리면 --no-msa-depth 를 붙여 직접 실행하라)"
  if [ "${AF3RUN_FILENAME_LANG:-en}" != "ko" ]; then
    echo "(2026-04 부터 이름이 ${NAME}_결과요약.csv 에서 바뀌었다."
    echo " 옛 이름을 쓰려면: AF3RUN_FILENAME_LANG=ko bash af3run.sh ${NAME} collect)"
  fi
  "$PY" "$COLLECT" "${NAME}=./${NAME}_out" -o "$CSV"
  RC=$?
  echo
  echo "등급 기준을 보려면: ${PY} af3_collect.py --grade-doc ."
  exit "$RC"
fi

if [ ! -f "$BATCH" ]; then
  echo "오류: af3_batch.py 를 찾을 수 없다: ${BATCH}"; exit 1
fi
if [ ! -d "./${NAME}_in" ]; then
  echo "오류: 입력 폴더가 없다: ./${NAME}_in"
  echo "      현재 위치: $(pwd)"
  echo "      입력 JSON 을 ./${NAME}_in 에 넣고 다시 실행하라."
  exit 1
fi

# 모드별 인자 조립
ARGS=( --name "$NAME" --msa-workers "$WORKERS" --msa-n-cpu "$NCPU" )
case "$MODE" in
  dry)     ARGS+=( --stage both --dry-run ) ;;
  screen)  ARGS+=( --stage both --diffusion-samples 1 --recycles 3 ) ;;
  full)    ARGS+=( --stage both ) ;;                       # AF3 기본값(5 x 10) 사용
  msa)     ARGS+=( --stage msa ) ;;
  infer)   ARGS+=( --stage infer --diffusion-samples 1 --recycles 3 ) ;;
  oneshot) ARGS+=( --stage oneshot --diffusion-samples 1 --recycles 3 ) ;;
  retry)   ARGS+=( --stage both --retry ) ;;
  bench)   ARGS+=( --stage oneshot --limit 20 --diffusion-samples 1 --recycles 3 ) ;;
  *) echo "오류: 알 수 없는 모드 '${MODE}'"; usage ;;
esac

echo "==============================================================================="
echo " af3run.sh"
echo "  작업 이름   : ${NAME}"
echo "  모드        : ${MODE}"
echo "  입력/출력   : ./${NAME}_in -> ./${NAME}_out"
echo "  MSA 설정    : 동시갈래 ${WORKERS}, DB당 스레드 ${NCPU} (실효 $(( WORKERS * NCPU * 4 ))스레드 / 논리코어 ${CORES}개)"
echo "  실행 명령   : ${PY} af3_batch.py ${ARGS[*]}"
echo "==============================================================================="

# 오래 걸리므로 터미널이 끊겨도 살아남게 로그를 남긴다.
LOGFILE="./${NAME}_work/af3run_$(date +%Y%m%d_%H%M%S).log"
mkdir -p "./${NAME}_work"

if [ "$MODE" = "dry" ]; then
  "$PY" "$BATCH" "${ARGS[@]}"
  exit $?
fi

echo "진행 로그: ${LOGFILE}"
echo "(터미널이 끊겨도 계속 돌리려면: nohup bash af3run.sh ${NAME} ${MODE} &)"
echo
"$PY" "$BATCH" "${ARGS[@]}" 2>&1 | tee "$LOGFILE"
RC="${PIPESTATUS[0]}"

echo
if [ "$RC" -eq 0 ]; then
  echo "정상 종료. 요약 CSV: ./${NAME}_work/run_summary.csv"
else
  echo "0이 아닌 코드로 종료(${RC}). 로그를 확인하라: ${LOGFILE}"
  echo "실패 건만 재시도: bash af3run.sh ${NAME} retry"
fi
exit "$RC"
