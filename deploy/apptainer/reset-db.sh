#!/usr/bin/env bash
# AI Data Hub — DB 만 초기화 (자동 백업 후 DROP + alembic upgrade head).
# clean.sh 와의 차이: postgres 인스턴스는 그대로, 안의 데이터만 비움.
#
# 사용:
#   bash deploy/apptainer/reset-db.sh           # 자동 백업 → DROP → migrate
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
load_env

if ! instance_running "$INST_POSTGRES"; then
  echo "[ERROR] $INST_POSTGRES 미동작 — 먼저 start_postgres.sh" >&2
  exit 1
fi

echo "이 작업은 $POSTGRES_DB DB 데이터를 모두 비우고 빈 스키마(alembic head) 로 재생성합니다."
echo "(인스턴스 자체는 그대로 유지)"
read -r -p "계속하시겠습니까? [y/N] " REPLY
[[ "$REPLY" =~ ^[Yy]$ ]] || { echo "취소됨."; exit 0; }

# 1) 자동 백업
AUTO_BACKUP="/tmp/aidh-db-pre-reset-$(date +%Y%m%d-%H%M%S).sql.gz"
echo "→ 안전 백업: $AUTO_BACKUP"
bash "$APPT_DIR/backup-db.sh" "$AUTO_BACKUP"

# 2) DROP + CREATE
echo "→ DROP + CREATE DATABASE $POSTGRES_DB"
apptainer exec "instance://$INST_POSTGRES" \
  psql -h 127.0.0.1 -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d postgres \
       -c "DROP DATABASE IF EXISTS $POSTGRES_DB WITH (FORCE); CREATE DATABASE $POSTGRES_DB;"
apptainer exec "instance://$INST_POSTGRES" \
  psql -h 127.0.0.1 -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
       -c "CREATE EXTENSION IF NOT EXISTS vector;" >/dev/null

# 3) alembic upgrade head
echo "→ alembic upgrade head"
cd "$API_DIR"
if [[ ! -d .venv ]]; then
  echo "[ERROR] .venv 없음 — 먼저 start_api.sh 한 번 실행 필요" >&2
  exit 1
fi
PYTHONPATH=src \
  POSTGRES_USER="$POSTGRES_USER" POSTGRES_PASSWORD="$POSTGRES_PASSWORD" \
  POSTGRES_PORT="$POSTGRES_PORT" POSTGRES_DB="$POSTGRES_DB" \
  .venv/bin/alembic upgrade head

echo
echo "✓ reset-db 완료"
echo "  복원 필요 시: bash deploy/apptainer/restore-db.sh $AUTO_BACKUP"
