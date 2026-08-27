#!/usr/bin/env bash
# Install the verified Kang_AF3 Docker path on a supported Ubuntu workstation.
set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
readonly REPO_ROOT
readonly AF3_REPOSITORY="https://github.com/google-deepmind/alphafold3.git"
readonly AF3_COMMIT="97d20234c6eb89e8d05376e9eecc9321e60a559b"
readonly AF3_VERSION="3.0.4"
readonly MODEL_URL="https://storage.googleapis.com/alphafold3/af3.bin.zst"
readonly MODEL_SHA256="df8bbf2621f17dd3ee21c2a921e84a50bc2b80cdc0c7971cb915c2826fee1f9b"
readonly WEIGHTS_TERMS_URL="https://github.com/google-deepmind/alphafold3/blob/main/WEIGHTS_TERMS_OF_USE.md"
readonly DOCKER_KEY_FINGERPRINT="9DC858229FC7DD38854AE2D88D81803C0EBFCD88"
readonly NVIDIA_KEY_FINGERPRINT="C95B321B61E88C1809C4F759DDCAE044F796ECB0"
readonly NVIDIA_TOOLKIT_VERSION="1.20.0-1"
readonly HELLO_WORLD_IMAGE="hello-world@sha256:5dd0d3e6e255913fc30f90b9f2b1d359cc2cbdb48090cc4b65f1676e203243cc"
readonly GPU_PROBE_IMAGE="ubuntu@sha256:2260313b31c8c011cd2eebe728008efac1b3982be73eb71348ea2648d2c0e09b"
readonly MIN_DB_FREE_BYTES="1000000000000"
readonly EXPECTED_CIF_COUNT="195858"
readonly MMCIF_ARCHIVE_RELATIVE="_zst/pdb_2022_09_28_mmcif_files.tar.zst"
readonly EXPECTED_MMCIF_ARCHIVE_SIZE="56979074571"
readonly EXPECTED_MMCIF_ARCHIVE_SHA256="4706ec0d948ed7a005b30eea21f5a7f9362b067e48d8bea2605671a49bd43c24"
readonly DB_PARTIAL_MARKER_NAME=".kang-af3-db-partial"
readonly PLOT_ENV_MARKER_NAME=".kang-af3-plot-env"
readonly PLOT_ENV_MARKER_VALUE="ubuntu-matplotlib-v1"
readonly SAFE_SYSTEM_PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
readonly -a DB_OBJECT_SPECS=(
  "bfd-first_non_consensus_sequences.fasta:18171626364"
  "mgy_clusters_2022_05.fa:128579703018"
  "uniref90_2022_05.fa:71821260491"
  "uniprot_all_2021_04.fa:108447942931"
  "pdb_seqres_2022_09_28.fasta:232899463"
  "nt_rna_2023_02_23_clust_seq_id_90_cov_80_rep_seq.fasta:80977012680"
  "rfam_14_9_clust_seq_id_90_cov_80_rep_seq.fasta:228433680"
  "rnacentral_active_seq_id_90_cov_80_linclust.fasta:13860314914"
)

FULL=0
ACCEPT_WEIGHTS_TERMS=0
DRY_RUN=0
TMP_DIR=""
WEIGHT_TMP=""
WEIGHT_STAGE_DIR=""
DB_VALIDATED_ID=""
DOCKER=()
readonly PROBE_TIMEOUT_SECONDS=180
readonly PROBE_PREFIX="kang-af3-install-${UID:-0}-$$"
PROBE_CONTAINERS=(
  "${PROBE_PREFIX}-hello"
  "${PROBE_PREFIX}-gpu"
  "${PROBE_PREFIX}-version"
  "${PROBE_PREFIX}-hmmer"
  "${PROBE_PREFIX}-jax"
)
revision=""
image_source=""
docker_version=""

usage() {
  cat <<'EOF'
Kang_AF3 Ubuntu installer

Usage:
  bash scripts/install_af3_ubuntu.sh [options]

Default (core installation):
  Installs Docker CE, NVIDIA Container Toolkit, the pinned AlphaFold 3 image,
  and a dedicated plotting venv backed by Ubuntu's matplotlib package. It does
  not download model weights or the full database.

Complete installation:
  bash scripts/install_af3_ubuntu.sh --full --accept-weights-terms

Options:
  --full                    Also install verified model weights and full AF3 DB.
  --accept-weights-terms    Confirm that you personally reviewed and accepted
                            Google's current weights terms. Required by --full.
  --dry-run                 Print the plan only. No sudo, network, or writes.
  -h, --help                Show this help.

Optional path environment variables (all must be absolute paths):
  AF3_WORK_DIR   default: $HOME/af3_work
  AF3_MODEL_DIR  default: $HOME/af3_models
  AF3_DB_DIR     default: $HOME/public_databases_full
  AF3_PLOT_ENV   default: $HOME/af3_plot_env
  AF3_IMAGE      default: alphafold3

Prerequisites:
  - Ubuntu 22.04, 24.04, or 26.04 on amd64
  - A working NVIDIA driver (nvidia-smi must succeed)
  - sudo access

The installer never installs or upgrades the NVIDIA driver. Docker-group access
requires logout/login after a new installation.
EOF
}

log() {
  printf '[install] %s\n' "$*"
}

warn() {
  printf '[warning] %s\n' "$*" >&2
}

die() {
  local message="$1"
  local code="${2:-1}"
  printf '[error] %s\n' "$message" >&2
  exit "$code"
}

cleanup() {
  local name
  if ((${#DOCKER[@]})); then
    for name in "${PROBE_CONTAINERS[@]}"; do
      run_with_docker_group \
        timeout --signal=TERM --kill-after=5s 30s \
        docker rm -f -- "$name" >/dev/null 2>&1 || true
    done
  fi
  if [[ -n "$WEIGHT_TMP" && -e "$WEIGHT_TMP" ]]; then
    rm -f -- "$WEIGHT_TMP"
  fi
  if [[ -n "$WEIGHT_STAGE_DIR" && -d "$WEIGHT_STAGE_DIR" ]]; then
    rm -f -- "$WEIGHT_STAGE_DIR/af3.bin.zst"
    rmdir -- "$WEIGHT_STAGE_DIR" 2>/dev/null || true
  fi
  if [[ -n "$TMP_DIR" && -d "$TMP_DIR" ]]; then
    rm -f -- \
      "$TMP_DIR/docker.asc" \
      "$TMP_DIR/docker.sources" \
      "$TMP_DIR/nvidia.key" \
      "$TMP_DIR/nvidia.gpg" \
      "$TMP_DIR/nvidia-signed.list" \
      "$TMP_DIR/docker-capture"
    rmdir -- "$TMP_DIR" 2>/dev/null || true
  fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

while (($#)); do
  case "$1" in
    --full)
      FULL=1
      ;;
    --accept-weights-terms)
      ACCEPT_WEIGHTS_TERMS=1
      ;;
    --dry-run)
      DRY_RUN=1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown option: $1 (use --help)" 2
      ;;
  esac
  shift
done

if ((FULL && !ACCEPT_WEIGHTS_TERMS)); then
  die "--full requires --accept-weights-terms after you review $WEIGHTS_TERMS_URL" 2
fi
if ((!FULL && ACCEPT_WEIGHTS_TERMS)); then
  die "--accept-weights-terms is only meaningful with --full" 2
fi

: "${HOME:?HOME is not set}"
WORK_DIR="${AF3_WORK_DIR:-$HOME/af3_work}"
MODEL_DIR="${AF3_MODEL_DIR:-$HOME/af3_models}"
DB_DIR="${AF3_DB_DIR:-$HOME/public_databases_full}"
PLOT_ENV="${AF3_PLOT_ENV:-$HOME/af3_plot_env}"
IMAGE="${AF3_IMAGE:-alphafold3}"
SOURCE_DIR="$WORK_DIR/alphafold3"
DB_PARTIAL="${DB_DIR}.partial"

validate_path() {
  local name="$1"
  local value="$2"
  local component current=""
  local -a components=()
  [[ "$value" == /* ]] || die "$name must be an absolute path: $value" 2
  [[ "$value" != "/" && "$value" != */ ]] || die "$name must name a directory below /: $value" 2
  [[ "$value" != *//* ]] || die "$name contains an empty path component: $value" 2
  [[ ! "$value" =~ [[:cntrl:]] ]] || die "$name contains a control character" 2
  IFS='/' read -r -a components <<< "${value#/}"
  for component in "${components[@]}"; do
    [[ -n "$component" && "$component" != "." && "$component" != ".." ]] || \
      die "$name contains an ambiguous path component: $value" 2
    current+="/$component"
    [[ ! -L "$current" ]] || die "$name must not contain a symlink component: $current" 2
  done
  if [[ -e "$value" ]]; then
    [[ -O "$value" ]] || die "$name is not owned by $(id -un): $value" 2
  fi
}

paths_overlap() {
  local left="$1"
  local right="$2"
  [[ "$left" == "$right" || "$left" == "$right/"* || "$right" == "$left/"* ]]
}

validate_path "AF3_WORK_DIR" "$WORK_DIR"
validate_path "AF3_MODEL_DIR" "$MODEL_DIR"
validate_path "AF3_DB_DIR" "$DB_DIR"
validate_path "AF3_PLOT_ENV" "$PLOT_ENV"
validate_path "AF3_DB_DIR.partial" "$DB_PARTIAL"
validate_path "AF3 source directory" "$SOURCE_DIR"
managed_paths=("$MODEL_DIR" "$DB_DIR" "$PLOT_ENV" "$SOURCE_DIR")
for ((left_index = 0; left_index < ${#managed_paths[@]}; left_index++)); do
  for ((right_index = left_index + 1; right_index < ${#managed_paths[@]}; right_index++)); do
    paths_overlap "${managed_paths[$left_index]}" "${managed_paths[$right_index]}" && \
      die "managed paths must not overlap: ${managed_paths[$left_index]} and ${managed_paths[$right_index]}" 2
  done
done
[[ "$IMAGE" =~ ^[a-z0-9][a-z0-9._/-]*(:[A-Za-z0-9_][A-Za-z0-9_.-]*)?$ ]] || \
  die "AF3_IMAGE contains unsupported characters: $IMAGE" 2
for required_file in "$REPO_ROOT/scripts/af3_check.sh" "$REPO_ROOT/scripts/af3_db.py"; do
  [[ -f "$required_file" && ! -L "$required_file" ]] || \
    die "clone the complete Kang_AF3 repository; required file is missing or linked: $required_file" 2
done

print_plan() {
  printf '[dry-run] mode: %s\n' "$([[ "$FULL" -eq 1 ]] && printf full || printf core)"
  printf '[dry-run] repository: %q\n' "$REPO_ROOT"
  printf '[dry-run] work directory: %q\n' "$WORK_DIR"
  printf '[dry-run] model directory: %q\n' "$MODEL_DIR"
  printf '[dry-run] database directory: %q\n' "$DB_DIR"
  printf '[dry-run] database staging directory: %q\n' "$DB_PARTIAL"
  printf '[dry-run] plot environment: %q\n' "$PLOT_ENV"
  printf '[dry-run] Docker image: %q\n' "$IMAGE"
  printf '[dry-run] stages: preflight, Docker/GPU, AF3 image, plot venv'
  if ((FULL)); then
    printf ', weights, full DB, full verification'
  fi
  printf '\n[dry-run] no sudo, network, or filesystem changes were made\n'
}

if ((DRY_RUN)); then
  print_plan
  exit 0
fi

((EUID != 0)) || die "run this installer as your normal user, not with sudo" 2
[[ -r /etc/os-release ]] || die "/etc/os-release is missing"
# shellcheck disable=SC1091
. /etc/os-release
[[ "${ID:-}" == "ubuntu" ]] || die "supported distribution is Ubuntu, got ${ID:-unknown}"
case "${VERSION_ID:-}" in
  22.04|24.04|26.04) ;;
  *) die "supported Ubuntu releases are 22.04, 24.04, and 26.04; got ${VERSION_ID:-unknown}" ;;
esac
[[ "$(dpkg --print-architecture)" == "amd64" ]] || die "only the tested amd64 path is supported"

for command in sudo apt-get dpkg-query nvidia-smi sha256sum stat find grep awk sed systemctl \
  flock cmp install mktemp mv du df dirname sort sg timeout; do
  command -v "$command" >/dev/null 2>&1 || die "required command is missing: $command"
done
nvidia-smi -L >/dev/null 2>&1 || die "NVIDIA driver is not working; fix nvidia-smi before running this installer"

conflicts=()
for package in docker.io docker-compose docker-compose-v2 docker-doc podman-docker containerd runc; do
  status="$(dpkg-query -W -f='${db:Status-Abbrev}' "$package" 2>/dev/null || true)"
  if [[ "$status" == ii* ]]; then
    conflicts+=("$package")
  fi
done
if ((${#conflicts[@]})); then
  die "conflicting distribution packages are installed: ${conflicts[*]}. Review and remove them manually; this installer will not delete them."
fi

if [[ -e "$PLOT_ENV" && ! -x "$PLOT_ENV/bin/python" ]]; then
  if find "$PLOT_ENV" -mindepth 1 -print -quit 2>/dev/null | grep -q .; then
    die "AF3_PLOT_ENV exists but is not a valid venv: $PLOT_ENV"
  fi
fi
if [[ -x "$PLOT_ENV/bin/python" ]]; then
  [[ -f "$PLOT_ENV/$PLOT_ENV_MARKER_NAME" && ! -L "$PLOT_ENV/$PLOT_ENV_MARKER_NAME" && \
     -O "$PLOT_ENV/$PLOT_ENV_MARKER_NAME" ]] || \
    die "AF3_PLOT_ENV is not installer-owned; move it aside or choose another path: $PLOT_ENV"
  [[ "$(<"$PLOT_ENV/$PLOT_ENV_MARKER_NAME")" == "$PLOT_ENV_MARKER_VALUE" ]] || \
    die "AF3_PLOT_ENV marker is incompatible: $PLOT_ENV/$PLOT_ENV_MARKER_NAME"
  plot_writable="$(find "$PLOT_ENV" \( -type d -o -type f \) -perm /022 -print -quit 2>/dev/null)"
  [[ -z "$plot_writable" ]] || \
    die "AF3_PLOT_ENV contains a group/world-writable entry: $plot_writable"
fi

LOCK_FILE="/tmp/kang-af3-install-${UID}.lock"
if [[ -e "$LOCK_FILE" ]]; then
  [[ -f "$LOCK_FILE" && ! -L "$LOCK_FILE" && -O "$LOCK_FILE" ]] || \
    die "installer lock is not a regular file owned by $(id -un): $LOCK_FILE"
else
  (umask 077; : >> "$LOCK_FILE")
fi
exec {INSTALL_LOCK_FD}>>"$LOCK_FILE"
flock -n "$INSTALL_LOCK_FD" || die "another Kang_AF3 installer is already running for this user"

weights_valid() {
  [[ -f "$MODEL_DIR/af3.bin" ]] || return 1
  [[ "$(sha256sum "$MODEL_DIR/af3.bin" | awk '{print $1}')" == "$MODEL_SHA256" ]]
}

db_structure_valid() {
  local root="$1"
  local spec name remainder expected_size actual_size cif_count
  [[ -d "$root" && ! -L "$root" ]] || return 1
  for spec in "${DB_OBJECT_SPECS[@]}"; do
    name="${spec%%:*}"
    remainder="${spec#*:}"
    expected_size="${remainder%%:*}"
    [[ -f "$root/$name" && ! -L "$root/$name" ]] || return 1
    actual_size="$(stat -c '%s' "$root/$name" 2>/dev/null)" || return 1
    [[ "$actual_size" == "$expected_size" ]] || return 1
  done
  [[ -d "$root/mmcif_files" && ! -L "$root/mmcif_files" ]] || return 1
  [[ -z "$(find "$root/mmcif_files" -maxdepth 1 -type l -print -quit 2>/dev/null)" ]] || return 1
  cif_count="$(find "$root/mmcif_files" -maxdepth 1 -type f -name '*.cif' \
    -printf '\n' 2>/dev/null | wc -l | tr -d ' ')"
  [[ "$cif_count" == "$EXPECTED_CIF_COUNT" ]] || return 1
  command -v python3 >/dev/null 2>&1 || return 1
  python3 "$REPO_ROOT/scripts/af3_db.py" verify --db-dir "$root" >/dev/null 2>&1
}

db_valid() {
  local root="$1"
  local root_id mmcif_archive mmcif_sha
  [[ -d "$root" && ! -L "$root" ]] || return 1
  root_id="$(stat -c '%d:%i' "$root" 2>/dev/null)" || return 1
  [[ -z "$DB_VALIDATED_ID" || "$root_id" != "$DB_VALIDATED_ID" ]] || return 0
  db_structure_valid "$root" || return 1
  if python3 "$REPO_ROOT/scripts/af3_db.py" validate-full-seal \
      --db-dir "$root" --require-official >/dev/null 2>&1; then
    DB_VALIDATED_ID="$root_id"
    return 0
  fi
  mmcif_archive="$root/$MMCIF_ARCHIVE_RELATIVE"
  if [[ -e "$mmcif_archive" ]]; then
    [[ -d "$root/_zst" && ! -L "$root/_zst" ]] || return 1
    [[ -f "$mmcif_archive" && ! -L "$mmcif_archive" ]] || return 1
    [[ "$(stat -c '%s' "$mmcif_archive" 2>/dev/null)" == "$EXPECTED_MMCIF_ARCHIVE_SIZE" ]] || \
      return 1
    printf '[install] SHA-256: %s\n' "$MMCIF_ARCHIVE_RELATIVE" >&2
    mmcif_sha="$(sha256sum "$mmcif_archive" | awk '{print $1}')" || return 1
    [[ "$mmcif_sha" == "$EXPECTED_MMCIF_ARCHIVE_SHA256" ]] || return 1
  fi
  # One Python-owned deep pass hashes the exact FASTA/mmCIF payloads, compares
  # the official pins, checks the binding before/after, and atomically publishes
  # the seal. There is no caller-supplied "preverified" publication bypass.
  python3 "$REPO_ROOT/scripts/af3_db.py" seal-full --db-dir "$root" || return 1
  DB_VALIDATED_ID="$root_id"
  return 0
}

validate_db_partial() {
  local marker="$DB_PARTIAL/$DB_PARTIAL_MARKER_NAME"
  local unexpected
  [[ -d "$DB_PARTIAL" && ! -L "$DB_PARTIAL" && -O "$DB_PARTIAL" ]] || \
    die "database staging path is not an owned directory: $DB_PARTIAL"
  [[ -f "$marker" && ! -L "$marker" && -O "$marker" ]] || \
    die "database staging path was not created by this installer: $DB_PARTIAL"
  [[ "$(<"$marker")" == "$AF3_COMMIT" ]] || \
    die "database staging marker targets another AF3 revision: $marker"
  unexpected="$(find "$DB_PARTIAL" -xdev -type l -print -quit 2>/dev/null)"
  [[ -z "$unexpected" ]] || die "database staging contains a symlink: $unexpected"
  unexpected="$(find "$DB_PARTIAL" -xdev ! -uid "$EUID" -print -quit 2>/dev/null)"
  [[ -z "$unexpected" ]] || die "database staging contains an entry owned by another user: $unexpected"
  chmod 0700 "$DB_PARTIAL"
}

create_db_partial() {
  (umask 077; mkdir -- "$DB_PARTIAL")
  printf '%s\n' "$AF3_COMMIT" > "$DB_PARTIAL/$DB_PARTIAL_MARKER_NAME"
  validate_db_partial
}

check_database_capacity() {
  local parent available partial_bytes=0
  parent="$(dirname "$DB_DIR")"
  while [[ ! -d "$parent" ]]; do
    parent="$(dirname "$parent")"
  done
  available="$(LC_ALL=C df -PB1 "$parent" | awk 'NR == 2 {print $4}')"
  [[ "$available" =~ ^[0-9]+$ ]] || die "could not determine free disk space"
  if [[ -d "$DB_PARTIAL" ]]; then
    partial_bytes="$(du -s --block-size=1 "$DB_PARTIAL" | awk '{print $1}')"
    [[ "$partial_bytes" =~ ^[0-9]+$ ]] || die "could not determine staged database size"
  fi
  ((available + partial_bytes >= MIN_DB_FREE_BYTES)) || \
    die "full DB installation requires at least $MIN_DB_FREE_BYTES available-or-staged bytes; available: $available, staged: $partial_bytes"
}

if ((FULL)); then
  for artifact in \
    "$MODEL_DIR/af3.bin" \
    "$MODEL_DIR/af3.bin.zst" \
    "$MODEL_DIR/af3.bin.zst.partial"; do
    [[ ! -L "$artifact" ]] || die "model artifact must not be a symlink: $artifact"
  done
  if [[ -e "$MODEL_DIR/af3.bin" ]]; then
    weights_valid || die "existing af3.bin has the wrong SHA-256: $MODEL_DIR/af3.bin"
  fi
  if [[ -e "$MODEL_DIR/af3.bin.zst" ]]; then
    command -v zstd >/dev/null 2>&1 || \
      die "existing af3.bin.zst cannot be checked before sudo because zstd is missing"
    zstd -q -t "$MODEL_DIR/af3.bin.zst" || \
      die "existing af3.bin.zst is corrupt: $MODEL_DIR/af3.bin.zst"
  fi
  if [[ -e "$DB_DIR" ]]; then
    db_structure_valid "$DB_DIR" || \
      die "AF3_DB_DIR exists but is incomplete or invalid: $DB_DIR. Move it aside explicitly before retrying."
  fi
  if [[ -e "$DB_PARTIAL" ]]; then
    validate_db_partial
  fi
  if [[ ! -e "$DB_DIR" ]]; then
    log "checking full DB disk capacity before installation changes"
    check_database_capacity
  fi
fi

log "requesting sudo once; the password is read only by sudo"
sudo -v

log "installing base Ubuntu packages"
sudo env DEBIAN_FRONTEND=noninteractive apt-get update
sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y \
  ca-certificates curl git gnupg python3 python3-matplotlib python3-venv tar wget zstd

TMP_DIR="$(mktemp -d)"

validate_single_key() {
  local key_file="$1"
  local expected="$2"
  local -a fingerprints=()
  mapfile -t fingerprints < <(
    gpg --batch --show-keys --with-colons "$key_file" 2>/dev/null | \
      awk -F: '$1 == "pub" {want_fpr=1; next} want_fpr && $1 == "fpr" {print $10; want_fpr=0}'
  )
  ((${#fingerprints[@]} == 1)) || \
    die "signing-key file must contain exactly one primary key: $key_file"
  [[ "${fingerprints[0]}" == "$expected" ]] || \
    die "signing-key fingerprint mismatch for $key_file"
}

install_root_file_exact() {
  local source="$1"
  local destination="$2"
  local mode="$3"
  local staging
  if sudo test -e "$destination"; then
    if ! sudo test -f "$destination" || sudo test -L "$destination"; then
      die "refusing to replace non-regular root configuration: $destination"
    fi
    sudo cmp -s "$source" "$destination" || \
      die "existing root configuration differs; review it manually: $destination"
    return 0
  fi
  staging="$(sudo mktemp "${destination}.kang-af3.XXXXXX")"
  if ! sudo install -m "$mode" "$source" "$staging"; then
    sudo rm -f -- "$staging"
    die "could not stage root configuration: $destination"
  fi
  if ! sudo mv -T --no-clobber -- "$staging" "$destination"; then
    sudo rm -f -- "$staging"
    die "could not publish root configuration: $destination"
  fi
  if sudo test -e "$staging"; then
    sudo rm -f -- "$staging"
    die "root configuration appeared concurrently: $destination"
  fi
}

log "configuring Docker's official apt repository"
curl --proto '=https' --proto-redir '=https' -fsSL \
  https://download.docker.com/linux/ubuntu/gpg -o "$TMP_DIR/docker.asc"
validate_single_key "$TMP_DIR/docker.asc" "$DOCKER_KEY_FINGERPRINT"
printf '%s\n' \
  'Types: deb' \
  'URIs: https://download.docker.com/linux/ubuntu' \
  "Suites: ${UBUNTU_CODENAME:-$VERSION_CODENAME}" \
  'Components: stable' \
  'Architectures: amd64' \
  'Signed-By: /etc/apt/keyrings/docker.asc' > "$TMP_DIR/docker.sources"
sudo install -m 0755 -d /etc/apt/keyrings
install_root_file_exact "$TMP_DIR/docker.asc" /etc/apt/keyrings/docker.asc 0644
install_root_file_exact "$TMP_DIR/docker.sources" /etc/apt/sources.list.d/docker.sources 0644

log "configuring NVIDIA Container Toolkit's official apt repository"
curl --proto '=https' --proto-redir '=https' -fsSL \
  https://nvidia.github.io/libnvidia-container/gpgkey -o "$TMP_DIR/nvidia.key"
validate_single_key "$TMP_DIR/nvidia.key" "$NVIDIA_KEY_FINGERPRINT"
gpg --batch --yes --dearmor --output "$TMP_DIR/nvidia.gpg" "$TMP_DIR/nvidia.key"
printf '%s\n' \
  "deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://nvidia.github.io/libnvidia-container/stable/deb/\$(ARCH) /" \
  "#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://nvidia.github.io/libnvidia-container/experimental/deb/\$(ARCH) /" \
  > "$TMP_DIR/nvidia-signed.list"
install_root_file_exact "$TMP_DIR/nvidia.gpg" \
  /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg 0644
install_root_file_exact "$TMP_DIR/nvidia-signed.list" \
  /etc/apt/sources.list.d/nvidia-container-toolkit.list 0644

log "installing Docker CE and NVIDIA Container Toolkit"
sudo env DEBIAN_FRONTEND=noninteractive apt-get update
sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y \
  docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin \
  "nvidia-container-toolkit=$NVIDIA_TOOLKIT_VERSION" \
  "nvidia-container-toolkit-base=$NVIDIA_TOOLKIT_VERSION" \
  "libnvidia-container-tools=$NVIDIA_TOOLKIT_VERSION" \
  "libnvidia-container1=$NVIDIA_TOOLKIT_VERSION"
sudo systemctl enable --now containerd docker
sudo usermod -aG docker "$(id -un)"
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker

DOCKER_MODE=""
if docker info >/dev/null 2>&1; then
  DOCKER_MODE="direct"
elif sg docker -c 'docker info >/dev/null 2>&1'; then
  DOCKER_MODE="group-shell"
else
  die "Docker daemon is not accessible after installation"
fi

quote_posix() {
  local value="$1"
  value=${value//\'/\'\\\'\'}
  printf "'%s'" "$value"
}

run_with_docker_group() {
  if [[ "$DOCKER_MODE" == "direct" ]]; then
    "$@"
    return
  fi
  local argument quoted command_text=""
  for argument in "$@"; do
    quoted="$(quote_posix "$argument")"
    command_text+="${command_text:+ }$quoted"
  done
  sg docker -c "exec $command_text"
}

run_docker() {
  run_with_docker_group docker "$@"
}

DOCKER=(run_docker)

run_installer_probe() {
  local suffix="$1"
  local name="${PROBE_PREFIX}-${suffix}"
  shift
  # DOCKER contains the shell function `run_docker`; GNU timeout can exec only
  # real programs, not an unexported function.  Put timeout and docker inside
  # the same direct/group-shell dispatcher instead.
  run_with_docker_group \
    timeout --signal=TERM --kill-after=5s "${PROBE_TIMEOUT_SECONDS}s" \
    docker run --name "$name" --rm "$@"
}

docker_capture_line() {
  local variable_name="$1"
  local captured=""
  shift
  "${DOCKER[@]}" "$@" > "$TMP_DIR/docker-capture"
  IFS= read -r captured < "$TMP_DIR/docker-capture" || true
  printf -v "$variable_name" '%s' "$captured"
  rm -f -- "$TMP_DIR/docker-capture"
}

log "verifying Docker and GPU passthrough"
run_installer_probe hello "$HELLO_WORLD_IMAGE" >/dev/null
run_installer_probe gpu --runtime=nvidia --gpus all "$GPU_PROBE_IMAGE" nvidia-smi \
  --query-gpu=name,driver_version,memory.total --format=csv,noheader

image_exists() {
  "${DOCKER[@]}" image inspect "$IMAGE" >/dev/null 2>&1
}

validate_image_capabilities() {
  run_installer_probe version --entrypoint python3 "$IMAGE" -c \
    "from alphafold3 import version; assert version.__version__ == '$AF3_VERSION'" \
    >/dev/null || die "AF3 image does not report the pinned version $AF3_VERSION: $IMAGE"
  run_installer_probe hmmer --entrypoint jackhmmer "$IMAGE" -h 2>&1 | \
    grep -Fq -- '--seq_limit' || \
    die "AF3 image lacks the patched HMMER --seq_limit flag: $IMAGE"
  run_installer_probe jax --gpus all --entrypoint python3 "$IMAGE" -c \
    "import jax; backend=jax.default_backend(); devices=jax.devices(); assert backend == 'gpu'; assert devices and all(device.platform == 'gpu' for device in devices)" \
    >/dev/null || die "AF3 image cannot reach the GPU through JAX: $IMAGE"
}

ensure_source() {
  validate_path "AF3_WORK_DIR" "$WORK_DIR"
  validate_path "AF3 source directory" "$SOURCE_DIR"
  mkdir -p -- "$WORK_DIR"
  if [[ -e "$SOURCE_DIR" ]]; then
    [[ -d "$SOURCE_DIR/.git" ]] || die "AF3 source path exists but is not a git clone: $SOURCE_DIR"
    remote="$(git -C "$SOURCE_DIR" remote get-url origin)"
    remote="${remote%.git}"
    [[ "$remote" == "${AF3_REPOSITORY%.git}" ]] || \
      die "AF3 source origin is not the official repository: $remote"
    [[ -z "$(git -C "$SOURCE_DIR" status --porcelain --untracked-files=all)" ]] || \
      die "AF3 source clone is dirty; this installer will not overwrite it: $SOURCE_DIR"
    git -C "$SOURCE_DIR" fetch origin "$AF3_COMMIT"
  else
    git clone "$AF3_REPOSITORY" "$SOURCE_DIR"
  fi
  git -C "$SOURCE_DIR" checkout --detach "$AF3_COMMIT"
  [[ "$(git -C "$SOURCE_DIR" rev-parse HEAD)" == "$AF3_COMMIT" ]] || \
    die "AF3 source commit verification failed"
}

BUILD_IMAGE=0
if image_exists; then
  docker_capture_line revision image inspect "$IMAGE" \
    --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}'
  docker_capture_line image_source image inspect "$IMAGE" \
    --format '{{ index .Config.Labels "org.opencontainers.image.source" }}'
  if [[ "$revision" == "$AF3_COMMIT" && "$image_source" == "$AF3_REPOSITORY" ]]; then
    log "reusing image with the pinned revision label: $IMAGE"
  else
    die "existing AF3_IMAGE lacks the exact source/revision labels: $IMAGE. Choose another AF3_IMAGE or rebuild it explicitly."
  fi
else
  BUILD_IMAGE=1
fi

if ((BUILD_IMAGE)); then
  log "cloning pinned AlphaFold 3 source and building image $IMAGE"
  ensure_source
  "${DOCKER[@]}" build --progress=plain \
    --label "org.opencontainers.image.source=$AF3_REPOSITORY" \
    --label "org.opencontainers.image.revision=$AF3_COMMIT" \
    -t "$IMAGE" -f "$SOURCE_DIR/docker/Dockerfile" "$SOURCE_DIR"
  docker_capture_line revision image inspect "$IMAGE" \
    --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}'
  docker_capture_line image_source image inspect "$IMAGE" \
    --format '{{ index .Config.Labels "org.opencontainers.image.source" }}'
  [[ "$revision" == "$AF3_COMMIT" && "$image_source" == "$AF3_REPOSITORY" ]] || \
    die "built image provenance labels are wrong"
fi
validate_image_capabilities

log "creating the isolated plotting environment"
validate_path "AF3_PLOT_ENV" "$PLOT_ENV"
mkdir -p -- "$(dirname "$PLOT_ENV")"
if [[ ! -x "$PLOT_ENV/bin/python" ]]; then
  (
    umask 022
    python3 -m venv --without-pip --system-site-packages "$PLOT_ENV"
    printf '%s\n' "$PLOT_ENV_MARKER_VALUE" > "$PLOT_ENV/$PLOT_ENV_MARKER_NAME"
  )
  chmod 0755 "$PLOT_ENV"
  chmod 0600 "$PLOT_ENV/$PLOT_ENV_MARKER_NAME"
fi
PYTHONNOUSERSITE=1 "$PLOT_ENV/bin/python" -c \
  'import matplotlib; print("matplotlib", matplotlib.__version__)'

install_weights() {
  validate_path "AF3_MODEL_DIR" "$MODEL_DIR"
  mkdir -p -- "$MODEL_DIR"
  if weights_valid; then
    log "reusing verified model weights"
    return 0
  fi

  local archive="$MODEL_DIR/af3.bin.zst"
  local archive_source="$archive"
  if [[ -e "$archive" ]]; then
    [[ -f "$archive" && ! -L "$archive" && -O "$archive" ]] || \
      die "existing model archive is not a regular file owned by $(id -un): $archive"
    zstd -q -t "$archive" || die "existing model archive is corrupt: $archive"
  else
    log "downloading model weights directly from Google"
    WEIGHT_STAGE_DIR="$(mktemp -d "$MODEL_DIR/.kang-af3-weights.XXXXXX")"
    chmod 0700 "$WEIGHT_STAGE_DIR"
    archive_source="$WEIGHT_STAGE_DIR/af3.bin.zst"
    curl --proto '=https' --proto-redir '=https' -L --fail \
      --retry 20 --retry-delay 5 --retry-all-errors \
      --output "$archive_source" "$MODEL_URL"
    zstd -q -t "$archive_source" || die "downloaded model archive failed zstd integrity"
  fi

  if [[ -z "$WEIGHT_STAGE_DIR" ]]; then
    WEIGHT_STAGE_DIR="$(mktemp -d "$MODEL_DIR/.kang-af3-weights.XXXXXX")"
    chmod 0700 "$WEIGHT_STAGE_DIR"
  fi
  WEIGHT_TMP="$WEIGHT_STAGE_DIR/af3.bin"
  zstd -q -d "$archive_source" -o "$WEIGHT_TMP"
  [[ "$(sha256sum "$WEIGHT_TMP" | awk '{print $1}')" == "$MODEL_SHA256" ]] || \
    die "decompressed model weights have the wrong SHA-256"

  if [[ "$archive_source" != "$archive" ]]; then
    mv -T --no-clobber -- "$archive_source" "$archive"
    [[ ! -e "$archive_source" ]] || die "model archive appeared concurrently: $archive"
  fi
  validate_path "AF3_MODEL_DIR" "$MODEL_DIR"
  [[ ! -e "$MODEL_DIR/af3.bin" ]] || die "model weights appeared concurrently: $MODEL_DIR/af3.bin"
  mv -T --no-clobber -- "$WEIGHT_TMP" "$MODEL_DIR/af3.bin"
  [[ ! -e "$WEIGHT_TMP" ]] || die "could not publish model weights without replacing a file"
  WEIGHT_TMP=""
  rmdir -- "$WEIGHT_STAGE_DIR"
  WEIGHT_STAGE_DIR=""
  weights_valid || die "published model weights failed final verification"
}

install_database() {
  if db_valid "$DB_DIR"; then
    log "reusing verified full database"
    return 0
  fi
  [[ ! -e "$DB_DIR" ]] || die "refusing to overwrite invalid final DB path: $DB_DIR"

  validate_path "AF3_DB_DIR" "$DB_DIR"
  validate_path "AF3_DB_DIR.partial" "$DB_PARTIAL"
  mkdir -p -- "$(dirname "$DB_DIR")"
  check_database_capacity

  if [[ -e "$DB_PARTIAL" ]]; then
    validate_db_partial
  else
    create_db_partial
  fi
  if db_valid "$DB_PARTIAL"; then
    log "promoting an already verified staged database"
  else
    warn "reusing installer-owned staging when present; the upstream downloader restarts objects rather than resuming byte ranges"
    ensure_source
    log "downloading the full AF3 database into the sibling staging directory"
    env -i HOME="$HOME" PATH="$SAFE_SYSTEM_PATH" LANG=C.UTF-8 LC_ALL=C.UTF-8 \
      bash "$SOURCE_DIR/fetch_databases.sh" "$DB_PARTIAL"
    db_valid "$DB_PARTIAL" || \
      die "database verification failed. Partial data was preserved at $DB_PARTIAL; rerun after diagnosing the failed object."
  fi

  validate_db_partial
  validate_path "AF3_DB_DIR" "$DB_DIR"
  validate_path "AF3_DB_DIR.partial" "$DB_PARTIAL"
  [[ ! -e "$DB_DIR" ]] || die "final DB path appeared during installation; refusing to replace it"
  mv -T --no-clobber -- "$DB_PARTIAL" "$DB_DIR"
  [[ ! -e "$DB_PARTIAL" ]] || die "could not publish the database without replacing a path"
  rm -f -- "$DB_DIR/$DB_PARTIAL_MARKER_NAME"
  DB_VALIDATED_ID=""
  db_valid "$DB_DIR" || die "published database failed final verification"
}

if ((FULL)); then
  log "weights terms acknowledgement supplied for $WEIGHTS_TERMS_URL"
  install_weights
  install_database
  log "running the complete Kang_AF3 environment check"
  run_with_docker_group env -i \
    HOME="$HOME" \
    PATH="$SAFE_SYSTEM_PATH" \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    AF3_IMAGE="$IMAGE" \
    AF3_MODEL_DIR="$MODEL_DIR" \
    AF3_DB_DIR="$DB_DIR" \
    AF3_PYTHON="$PLOT_ENV/bin/python" \
    bash "$REPO_ROOT/scripts/af3_check.sh"
fi

log "installation summary"
printf '  mode: %s\n' "$([[ "$FULL" -eq 1 ]] && printf full || printf core)"
docker_capture_line docker_version --version
printf '  Docker: %s\n' "$docker_version"
printf '  image: %s\n' "$IMAGE"
printf '  image revision target: %s\n' "$AF3_COMMIT"
printf '  plot Python: %s\n' "$PLOT_ENV/bin/python"
if ((FULL)); then
  printf '  model weights: %s\n' "$MODEL_DIR/af3.bin"
  printf '  full database: %s\n' "$DB_DIR"
fi

if id -nG | tr ' ' '\n' | grep -qx docker; then
  log "the current session already has docker-group access"
else
  warn "log out and log back in before running Kang_AF3 without sudo; docker-group membership is root-equivalent"
fi
log "complete"
