#!/usr/bin/env bash
# AI Data Hub — PostgreSQL+pgvector Apptainer instance 기동
set -euo pipefail
# shellcheck source=/dev/null
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
load_env
export_proxy
require_apptainer
ensure_dirs

if [[ ! -f "$APPT_DIR/postgres.sif" ]]; then
  echo "[ERROR] postgres.sif 없음. 먼저: bash build.sh" >&2
  exit 1
fi

if instance_running "$INST_POSTGRES"; then
  echo "✓ $INST_POSTGRES 이미 실행 중"
else
  require_port_free "$POSTGRES_PORT" "POSTGRES"
  echo "→ start $INST_POSTGRES"
  apptainer instance start \
    --bind "$DATA_DIR/postgres:/var/lib/postgresql/data" \
    --bind "$DATA_DIR/postgres-run:/var/run/postgresql" \
    --env "POSTGRES_USER=${POSTGRES_USER}" \
    --env "POSTGRES_PASSWORD=${POSTGRES_PASSWORD}" \
    --env "POSTGRES_DB=${POSTGRES_DB}" \
    --env "PGPORT=${POSTGRES_PORT}" \
    --env "PGDATA=/var/lib/postgresql/data/pgdata" \
    --env "LANG=C.UTF-8" \
    --env "LC_ALL=C.UTF-8" \
    "$APPT_DIR/postgres.sif" "$INST_POSTGRES" \
    > "$LOG_DIR/postgres-start.log" 2>&1
fi

echo "→ pg_isready 대기..."
for i in $(seq 1 60); do
  if apptainer exec "instance://$INST_POSTGRES" \
       pg_isready -h 127.0.0.1 -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
       >/dev/null 2>&1; then
    echo "✓ postgres ready (${i}s)"
    break
  fi
  sleep 1
done

echo "→ CREATE EXTENSION IF NOT EXISTS vector;"
apptainer exec "instance://$INST_POSTGRES" \
  psql -h 127.0.0.1 -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
       -c "CREATE EXTENSION IF NOT EXISTS vector;" \
  > "$LOG_DIR/postgres-ext.log" 2>&1 || {
    echo "[WARN] vector 확장 생성 실패 — 로그: $LOG_DIR/postgres-ext.log"
  }

echo
echo "✓ postgres 기동 완료"
echo "  host=127.0.0.1 port=${POSTGRES_PORT} user=${POSTGRES_USER} db=${POSTGRES_DB}"
echo "  로그: apptainer logs $INST_POSTGRES"
