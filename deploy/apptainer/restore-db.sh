#!/usr/bin/env bash
# AI Data Hub — .sql.gz → DB 복원.
# ⚠ 기존 DB 데이터 덮어씀. 백업 후 사용.
#
# 사용:
#   bash deploy/apptainer/restore-db.sh /path/to/dump.sql.gz
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
load_env

YES=0
DUMP=""
for a in "$@"; do
  case "$a" in
    --yes|-y) YES=1 ;;
    -h|--help) sed -n '1,8p' "$0"; exit 0 ;;
    *) DUMP="$a" ;;
  esac
done
[[ "${AIDH_CONFIRM:-}" = "yes" ]] && YES=1

if [[ -z "$DUMP" || ! -f "$DUMP" ]]; then
  echo "usage: $0 [--yes] <dump.sql.gz>" >&2
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
if [[ "$YES" -ne 1 ]]; then
  read -r -p "계속하시겠습니까? [y/N] " REPLY
  [[ "$REPLY" =~ ^[Yy]$ ]] || { echo "취소됨."; exit 0; }
else
  echo "  (--yes/AIDH_CONFIRM=yes — 비대화형 진행)"
fi

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
# -v ON_ERROR_STOP=1 이 없으면 psql 은 SQL 에러가 나도, 입력이 0바이트여도 exit 0 이다.
# 그래서 바로 위에서 DROP+CREATE 한 뒤 복원이 통째로 실패해도 '✓ restore 완료' 가 찍혔다.
if ! gunzip -c "$DUMP" | apptainer exec "instance://$INST_POSTGRES" \
  psql -v ON_ERROR_STOP=1 -h 127.0.0.1 -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  > /tmp/restore.log 2>&1; then
  echo
  echo "✗ restore 실패 — psql 이 에러로 중단했다." >&2
  echo "  로그 꼬리(/tmp/restore.log):" >&2
  tail -20 /tmp/restore.log | sed 's/^/    /' >&2
  echo "  롤백용 안전 백업: $AUTO_BACKUP" >&2
  exit 1
fi

# 복원 후 테이블이 실제로 생겼는지 본다. 세기만 하고 비교하지 않으면 0개도 '완료'가 된다.
RESTORED_TABLES=$(apptainer exec "instance://$INST_POSTGRES" \
  psql -h 127.0.0.1 -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -tAc "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';" 2>/dev/null || echo "")
case "$RESTORED_TABLES" in
  ''|*[!0-9]*|0)
    echo "✗ 복원 후 public 스키마 테이블이 ${RESTORED_TABLES:-조회불가} — 덤프가 비었거나 복원이 안 됐다." >&2
    echo "  롤백용 안전 백업: $AUTO_BACKUP" >&2
    exit 1 ;;
esac

echo
echo "✓ restore 완료 — public schema tables=$RESTORED_TABLES"
echo "  로그: /tmp/restore.log (마지막 5줄):"
# 성공 경로라 5줄로 충분하다. 실패 경로에서는 위에서 20줄을 보여준다.
tail -5 /tmp/restore.log | sed 's/^/    /'
echo
echo "  안전 백업 (필요 시 롤백): $AUTO_BACKUP"
