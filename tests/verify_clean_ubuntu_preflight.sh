#!/usr/bin/env bash
# 깨끗한 우분투에서 설치기가 처음 부딪히는 관문을 실제로 확인한다.
#
# 왜 따로 있는가
# --------------
# tests/run_all.py 는 실제 Docker 를 쓰지 않는다는 원칙으로 만들어졌다 (스텁을 쓴다).
# 그런데 설치기의 사전 점검은 "이 컴퓨터가 어떤 상태인가" 를 보는 코드라서 스텁으로는
# 검증할 수 없다. 그래서 이 스크립트만 실제 Docker 로 깨끗한 우분투를 띄운다.
# 릴리스 게이트가 아니라 손으로 돌리는 검증이다.
#
# 무엇을 확인하는가
# -----------------
# 초보자가 실제로 하는 실수 순서대로, 설치기가 멈추고 이유를 말하는지 본다.
#   1. sudo 로 실행한다            -> 거부하고 이유를 말해야 한다
#   2. 필수 명령이 없다            -> 없는 명령 이름을 말해야 한다
#   3. 우분투가 아니거나 판이 다르다 -> 지원 판을 말해야 한다
#   4. GPU 드라이버가 없다          -> nvidia-smi 를 먼저 고치라고 해야 한다
#   5. --dry-run                   -> 아무것도 바꾸지 않고 계획만 찍어야 한다
#
# 확인하지 못하는 것
# ------------------
# 실제 apt 설치, Docker 구성, 850GB 내려받기는 여기서 하지 않는다. 그것을 하려면
# GPU 가 붙은 진짜 우분투 장비가 필요하다.
#
# 사용법:
#   bash tests/verify_clean_ubuntu_preflight.sh
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE_UBUNTU="${AF3_CLEAN_UBUNTU_IMAGE:-ubuntu:24.04}"
IMAGE_OTHER="${AF3_NON_UBUNTU_IMAGE:-debian:12}"
PASS=0
FAIL=0
CASE_INDEX=0
CASE_TIMEOUT="${AF3_PREFLIGHT_CASE_TIMEOUT:-300}"
ACTIVE_CONTAINERS=()

cleanup_containers() {
  local container
  for container in "${ACTIVE_CONTAINERS[@]}"; do
    docker rm -f -- "$container" >/dev/null 2>&1 || true
  done
}
trap cleanup_containers EXIT

command -v docker >/dev/null 2>&1 || {
  printf '건너뜀: docker 명령이 없다. 이 검증은 실제 Docker 가 필요하다.\n'
  exit 0
}
docker info >/dev/null 2>&1 || {
  printf '건너뜀: docker 데몬에 접근할 수 없다.\n'
  exit 0
}

# run_case <이름> <기대 종료코드> <기대 문구> <이미지> <컨테이너 안에서 돌릴 셸 코드>
run_case() {
  local name="$1" want_code="$2" want_text="$3" image="$4" script="$5"
  local out code container
  CASE_INDEX=$((CASE_INDEX + 1))
  container="kang-af3-preflight-$$-${CASE_INDEX}"
  ACTIVE_CONTAINERS+=("$container")
  out="$(timeout --signal=TERM --kill-after=10s "$CASE_TIMEOUT" \
    docker run --rm --name "$container" -v "$REPO:/repo:ro" \
    "$image" bash -c "$script" 2>&1)"
  code=$?
  docker rm -f -- "$container" >/dev/null 2>&1 || true
  if [[ "$code" == "$want_code" ]] && grep -qF -- "$want_text" <<<"$out"; then
    printf '  [OK]   %s\n' "$name"
    PASS=$((PASS + 1))
  else
    printf '  [실패] %s\n' "$name"
    printf '         기대: 종료코드 %s + 문구 %q\n' "$want_code" "$want_text"
    printf '         실제: 종료코드 %s\n' "$code"
    while IFS= read -r line; do
      printf '         | %s\n' "$line"
    done < <(tail -5 <<<"$out")
    FAIL=$((FAIL + 1))
  fi
}

# 저장소를 컨테이너 안으로 복사해 일반 사용자 소유로 만든다.
# (읽기전용 마운트 그대로 쓰면 설치기의 소유권 검사에 걸린다.)
AS_USER='
  id af3user >/dev/null 2>&1 || useradd -m af3user
  cp -r /repo /home/af3user/Kang_AF3
  chown -R af3user /home/af3user/Kang_AF3
  su af3user -c "cd /home/af3user/Kang_AF3 && %s"
'

as_user_script() {
  local command="$1"
  printf '%s' "${AS_USER/\%s/$command}"
}

printf '깨끗한 우분투 사전 점검 검증 (%s)\n\n' "$IMAGE_UBUNTU"

run_case "root 로 실행하면 거부한다" 2 \
  "run this installer as your normal user" "$IMAGE_UBUNTU" \
  'cp -r /repo /root/Kang_AF3 && cd /root/Kang_AF3 &&
   bash scripts/install_af3_ubuntu.sh --full --accept-weights-terms'

run_case "없는 필수 명령을 이름으로 알려 준다" 1 \
  "required command is missing" "$IMAGE_UBUNTU" \
  "$(as_user_script 'bash scripts/install_af3_ubuntu.sh --full --accept-weights-terms')"

run_case "우분투가 아니면 무엇을 지원하는지 말한다" 1 \
  "supported distribution is Ubuntu" "$IMAGE_OTHER" \
  "$(as_user_script 'bash scripts/install_af3_ubuntu.sh --full --accept-weights-terms')"

# 우분투이긴 하나 지원하지 않는 판인 경우 (os-release 를 바꿔 흉내낸다)
run_case "지원하지 않는 우분투 판이면 지원 목록을 말한다" 1 \
  "supported Ubuntu releases are 22.04, 24.04, and 26.04" "$IMAGE_UBUNTU" \
  "sed -i 's/^VERSION_ID=.*/VERSION_ID=\"18.04\"/' /etc/os-release
   $(as_user_script 'bash scripts/install_af3_ubuntu.sh --full --accept-weights-terms')"

# sudo 와 나머지 명령을 채워 두고 GPU 만 없는 상태를 만든다.
WITH_TOOLS='
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq && apt-get install -y -qq sudo systemd coreutils findutils zstd curl >/dev/null 2>&1
'
run_case "GPU 드라이버가 없으면 nvidia-smi 부터 고치라고 한다" 1 \
  "nvidia-smi" "$IMAGE_UBUNTU" \
  "$WITH_TOOLS
   $(as_user_script 'bash scripts/install_af3_ubuntu.sh --full --accept-weights-terms')"

run_case "--dry-run 은 계획만 찍고 아무것도 바꾸지 않는다" 0 \
  "no sudo, network, or filesystem changes were made" "$IMAGE_UBUNTU" \
  "$(as_user_script 'bash scripts/install_af3_ubuntu.sh --full --accept-weights-terms --dry-run')"

printf '\n통과 %d개, 실패 %d개\n' "$PASS" "$FAIL"
if ((FAIL)); then
  printf '\n설치기의 사전 점검이 초보자에게 이유를 말하지 못한다.\n'
  exit 1
fi
printf '\n설치기는 깨끗한 우분투에서 각 관문마다 이유를 말하고 멈춘다.\n'
printf '실제 설치(apt, Docker 구성, DB 내려받기)는 이 검증의 범위가 아니다.\n'
