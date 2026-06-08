#!/usr/bin/env bash
# Drive-Sync 표준 키트 — 공용 헬퍼.
#
# 호출자가 set -euo pipefail 한 다음 source 한다.
# PROJECT.conf 를 자동 로드하고 prefix-agnostic 변수를 export 한다.

# ── 1. 위치 ──────────────────────────────────────────────────────
DS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── 2. PROJECT.conf 로드 ────────────────────────────────────────
if [[ ! -f "$DS_DIR/PROJECT.conf" ]]; then
  echo "[ERROR] $DS_DIR/PROJECT.conf 없음." >&2
  echo "        cp PROJECT.conf.example PROJECT.conf  후 값 채우기" >&2
  exit 1
fi
# shellcheck source=/dev/null
source "$DS_DIR/PROJECT.conf"

# 필수 변수 검증
for var in PROJ_PREFIX PROJ_NAME PROJ_ENV_FILE PROJ_DUMP_DIR; do
  if [[ -z "${!var:-}" ]]; then
    echo "[ERROR] PROJECT.conf: $var 가 비어있음." >&2
    exit 1
  fi
done
: "${PROJ_PG_INSTANCE:=}"
: "${PROJ_DRIVE_REMOTE_DEFAULT:=ApptainerImages}"
: "${PROJ_DRIVE_RETAIN_DEFAULT:=5}"
: "${PROJ_HEALTH_URL:=}"

# ── 3. 프로젝트 .env 로드 (POSTGRES_* 채우기) ───────────────────
if [[ ! -f "$PROJ_ENV_FILE" ]]; then
  echo "[ERROR] PROJ_ENV_FILE 없음: $PROJ_ENV_FILE" >&2
  exit 1
fi
# shellcheck source=/dev/null
set -a; source "$PROJ_ENV_FILE"; set +a
: "${POSTGRES_HOST:=127.0.0.1}"
: "${POSTGRES_PORT:?POSTGRES_PORT 필수 (.env 에서)}"
: "${POSTGRES_USER:?POSTGRES_USER 필수 (.env 에서)}"
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD 필수 (.env 에서)}"
: "${POSTGRES_DB:?POSTGRES_DB 필수 (.env 에서)}"

# ── 4. Drive 설정 (PROJECT.conf 우선, env override 가능) ────────
DRIVE_REMOTE_NAME="${PROJ_DRIVE_REMOTE_NAME:-$PROJ_DRIVE_REMOTE_DEFAULT}"
DRIVE_FOLDER="${PROJ_DRIVE_FOLDER:-${PROJ_NAME}/db-dumps}"
DRIVE_PATH="${DRIVE_REMOTE_NAME}:${DRIVE_FOLDER}"
DRIVE_RETAIN="${PROJ_DRIVE_RETAIN:-$PROJ_DRIVE_RETAIN_DEFAULT}"

# ── 5. dump 디렉터리 보장 ───────────────────────────────────────
mkdir -p "$PROJ_DUMP_DIR"

# ── 6. dump 파일명 규칙 (TS 기반 정렬용) ────────────────────────
ts_now() { date -u +"%Y%m%d-%H%M%SZ"; }
dump_name() { echo "${PROJ_PREFIX}-db-$(ts_now).sql.gz"; }
dump_glob() { echo "${PROJ_PREFIX}-db-*.sql.gz"; }

# ── 7. PG 명령 추상화 (apptainer instance 우선, 없으면 host) ────
pg_dump_cmd() {
  # stdout 으로 평문 SQL 출력 — 호출자가 gzip
  if [[ -n "$PROJ_PG_INSTANCE" ]] && instance_running "$PROJ_PG_INSTANCE"; then
    PGPASSWORD="$POSTGRES_PASSWORD" \
      apptainer exec "instance://$PROJ_PG_INSTANCE" \
      pg_dump -h 127.0.0.1 -p "$POSTGRES_PORT" \
              -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
              --no-owner --no-privileges --clean --if-exists
  else
    PGPASSWORD="$POSTGRES_PASSWORD" \
      pg_dump -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" \
              -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
              --no-owner --no-privileges --clean --if-exists
  fi
}

psql_cmd() {
  # 인자: psql 추가 옵션 (예: -c "SELECT 1")
  if [[ -n "$PROJ_PG_INSTANCE" ]] && instance_running "$PROJ_PG_INSTANCE"; then
    PGPASSWORD="$POSTGRES_PASSWORD" \
      apptainer exec "instance://$PROJ_PG_INSTANCE" \
      psql -h 127.0.0.1 -p "$POSTGRES_PORT" \
           -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
           "$@"
  else
    PGPASSWORD="$POSTGRES_PASSWORD" \
      psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" \
           -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
           "$@"
  fi
}

instance_running() {
  command -v apptainer >/dev/null 2>&1 || return 1
  apptainer instance list 2>/dev/null | awk '{print $1}' | grep -qx "$1"
}

# ── 8. rclone 가용성 ────────────────────────────────────────────
require_rclone() {
  if ! command -v rclone >/dev/null 2>&1; then
    echo "[ERROR] rclone 미설치. sudo apt install -y rclone 또는" >&2
    echo "        curl https://rclone.org/install.sh | sudo bash" >&2
    exit 1
  fi
}

remote_configured() {
  local rc="${RCLONE_CONFIG:-$HOME/.config/rclone/rclone.conf}"
  [[ -f "$rc" ]] && grep -q "^\[$DRIVE_REMOTE_NAME\]" "$rc"
}

# ── 9. 공용 sha256 ─────────────────────────────────────────────
file_sha256() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    shasum -a 256 "$1" | awk '{print $1}'
  fi
}

# ── 10. (선택) health-check ────────────────────────────────────
health_check() {
  [[ -z "$PROJ_HEALTH_URL" ]] && return 0
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$PROJ_HEALTH_URL" || echo 000)"
  if [[ "$code" = "200" ]]; then
    echo "[OK] health 200 — $PROJ_HEALTH_URL"
    return 0
  fi
  echo "[WARN] health $code — $PROJ_HEALTH_URL" >&2
  return 1
}
