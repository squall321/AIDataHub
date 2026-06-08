#!/usr/bin/env bash
# 로컬 sql.gz → 안전백업 후 DROP+CREATE+restore.
#
# 사용:
#   bash restore-db.sh /path/to/<proj>-db-YYYYMMDD-HHMMSSZ.sql.gz
#   bash restore-db.sh ... --yes    # 확인 프롬프트 스킵
set -euo pipefail
# shellcheck source=./_drive_common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_drive_common.sh"

DUMP="${1:-}"; shift || true
YES=0
for arg in "$@"; do
  [[ "$arg" = "--yes" ]] && YES=1
done

if [[ -z "$DUMP" || ! -f "$DUMP" ]]; then
  echo "[ERROR] dump 파일 경로 필요: bash restore-db.sh <file.sql.gz> [--yes]" >&2
  exit 1
fi

# 1) sha256 검증 (있으면)
SUM="${DUMP}.sha256"
if [[ -f "$SUM" ]]; then
  CUR=$(file_sha256 "$DUMP")
  EXP=$(awk '{print $1}' "$SUM")
  if [[ "$CUR" != "$EXP" ]]; then
    echo "[ERROR] sha256 mismatch — abort" >&2
    echo "  expected: $EXP" >&2
    echo "  actual:   $CUR" >&2
    exit 1
  fi
  echo "[OK] sha256 검증 통과"
fi

# 2) 사용자 확인
echo "→ 대상: $POSTGRES_DB @ $POSTGRES_HOST:$POSTGRES_PORT (user=$POSTGRES_USER)"
echo "  dump: $DUMP"
echo "  주의: 현재 DB 가 DROP+CREATE 됩니다. 직전 상태 안전백업 후 진행."
if [[ $YES -eq 0 ]]; then
  read -r -p "정말 진행할까요? (yes/N): " ans
  [[ "$ans" = "yes" ]] || { echo "취소"; exit 0; }
fi

# 3) 안전백업 (직전 상태 보존)
SAFETY="$PROJ_DUMP_DIR/${PROJ_PREFIX}-db-safety-$(ts_now).sql.gz"
echo "→ 안전백업: $SAFETY"
pg_dump_cmd | gzip -c > "$SAFETY"
echo "[OK] $(du -h "$SAFETY" | cut -f1) 안전백업 완료"

# 4) DROP + CREATE (postgres DB 에 연결해서 대상 DB 자체를 재생성)
echo "→ DROP DATABASE $POSTGRES_DB"
if [[ -n "$PROJ_PG_INSTANCE" ]] && instance_running "$PROJ_PG_INSTANCE"; then
  PGPASSWORD="$POSTGRES_PASSWORD" \
    apptainer exec "instance://$PROJ_PG_INSTANCE" \
    psql -h 127.0.0.1 -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d postgres \
         -c "DROP DATABASE IF EXISTS \"$POSTGRES_DB\";" \
         -c "CREATE DATABASE \"$POSTGRES_DB\" OWNER \"$POSTGRES_USER\";"
else
  PGPASSWORD="$POSTGRES_PASSWORD" \
    psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d postgres \
         -c "DROP DATABASE IF EXISTS \"$POSTGRES_DB\";" \
         -c "CREATE DATABASE \"$POSTGRES_DB\" OWNER \"$POSTGRES_USER\";"
fi

# 5) restore
echo "→ restore from $DUMP"
gunzip -c "$DUMP" | psql_cmd >/dev/null

# 6) 간이 검증
ROW_TABLES=$(psql_cmd -t -c "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';" 2>/dev/null | tr -d ' ' || echo "?")
echo "[OK] restore 완료 — public schema tables=$ROW_TABLES"

echo
echo "================================================================"
echo "✓ restore 완료"
echo "  안전백업 (롤백용): $SAFETY"
echo "================================================================"
