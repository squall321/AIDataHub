#!/usr/bin/env bash
# AI Data Hub — DB → .sql.gz dump.
# 사용:
#   bash deploy/apptainer/backup-db.sh                  # /tmp 에 자동 이름
#   bash deploy/apptainer/backup-db.sh /path/to/x.sql.gz
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
load_env

OUT="${1:-/tmp/aidh-db-$(date +%Y%m%d-%H%M%S).sql.gz}"

if ! instance_running "$INST_POSTGRES"; then
  echo "[ERROR] $INST_POSTGRES 미동작 — 먼저 start_postgres.sh" >&2
  exit 1
fi

echo "→ pg_dump $POSTGRES_DB → $OUT"
apptainer exec "instance://$INST_POSTGRES" \
  pg_dump -h 127.0.0.1 -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
          --no-owner --no-acl -Fp \
  | gzip > "$OUT"

SIZE=$(ls -lh "$OUT" | awk '{print $5}')
SHA=$(sha256sum "$OUT" | awk '{print $1}')
echo
echo "✓ backup 완료"
echo "  file: $OUT"
echo "  size: $SIZE"
echo "  sha256: $SHA"
echo
echo "복원:"
echo "  bash deploy/apptainer/restore-db.sh $OUT"
