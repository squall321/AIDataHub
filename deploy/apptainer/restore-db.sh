#!/usr/bin/env bash
# AI Data Hub — .sql.gz → DB 복원.
# ⚠ 기존 DB 데이터 덮어씀. 백업 후 사용.
#
# 사용:
#   bash deploy/apptainer/restore-db.sh /path/to/dump.sql.gz
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
load_env

DUMP="${1:-}"
if [[ -z "$DUMP" || ! -f "$DUMP" ]]; then
  echo "usage: $0 <dump.sql.gz>" >&2
  echo
  echo "최근 백업 (참고):" >&2
  ls -lh /tmp/aidh-db-*.sql.gz 2>/dev/null | head -5 >&2 || true
  exit 2
fi

if ! instance_running "$INST_POSTGRES"; then
  echo "[ERROR] $INST_POSTGRES 미동작" >&2
  exit 1
fi

echo "⚠ 이 작업은 $POSTGRES_DB DB 내용을 덮어씁니다."
echo "  dump: $DUMP ($(ls -lh "$DUMP" | awk '{print $5}'))"
read -r -p "계속하시겠습니까? [y/N] " REPLY
[[ "$REPLY" =~ ^[Yy]$ ]] || { echo "취소됨."; exit 0; }

# 자동 백업 먼저 (안전망)
AUTO_BACKUP="/tmp/aidh-db-pre-restore-$(date +%Y%m%d-%H%M%S).sql.gz"
echo "→ 안전 백업 먼저: $AUTO_BACKUP"
bash "$APPT_DIR/backup-db.sh" "$AUTO_BACKUP" || {
  echo "[WARN] 자동 백업 실패 — 그래도 진행하려면 Ctrl+C 한 번 더 누르고 직접 실행"
  exit 1
}

# DB drop + recreate
echo "→ DROP DATABASE $POSTGRES_DB IF EXISTS"
apptainer exec "instance://$INST_POSTGRES" \
  psql -h 127.0.0.1 -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d postgres \
       -c "DROP DATABASE IF EXISTS $POSTGRES_DB WITH (FORCE);"

echo "→ CREATE DATABASE $POSTGRES_DB"
apptainer exec "instance://$INST_POSTGRES" \
  psql -h 127.0.0.1 -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d postgres \
       -c "CREATE DATABASE $POSTGRES_DB;"

# pgvector 재설치 (DB drop 했으므로)
apptainer exec "instance://$INST_POSTGRES" \
  psql -h 127.0.0.1 -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
       -c "CREATE EXTENSION IF NOT EXISTS vector;" >/dev/null

echo "→ restore from $DUMP"
gunzip -c "$DUMP" | apptainer exec "instance://$INST_POSTGRES" \
  psql -h 127.0.0.1 -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  > /tmp/restore.log 2>&1

echo
echo "✓ restore 완료"
echo "  로그: /tmp/restore.log (마지막 5줄):"
tail -5 /tmp/restore.log | sed 's/^/    /'
echo
echo "  안전 백업 (필요 시 롤백): $AUTO_BACKUP"
