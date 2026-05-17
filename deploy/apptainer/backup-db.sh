#!/usr/bin/env bash
# AI Data Hub — DB → .sql.gz dump (+ retention).
# 사용:
#   bash deploy/apptainer/backup-db.sh                  # /tmp 에 자동 이름
#   bash deploy/apptainer/backup-db.sh /path/to/x.sql.gz
#
# retention: 같은 디렉토리의 aidh-db-*.sql.gz / pre-update-db-*.sql.gz 중
#   최신 AIDH_BACKUP_KEEP(기본 10) 개만 남기고 오래된 것 삭제. 무한 누적
#   방지 (update.sh 자동백업이 디스크를 채우는 것을 막음).
#   AIDH_BACKUP_KEEP=0 이면 정리 비활성.
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

# ── retention — 오래된 백업 정리 ────────────────────────────────────
KEEP="${AIDH_BACKUP_KEEP:-10}"
if [[ "$KEEP" -gt 0 ]]; then
  BK_DIR="$(cd "$(dirname "$OUT")" && pwd)"
  # aidh-db-*.sql.gz + pre-update-db-*.sql.gz 를 한 풀로 보고 최신 KEEP 개 유지.
  mapfile -t _all < <(ls -1t "$BK_DIR"/aidh-db-*.sql.gz "$BK_DIR"/pre-update-db-*.sql.gz 2>/dev/null)
  if [[ "${#_all[@]}" -gt "$KEEP" ]]; then
    _del=("${_all[@]:$KEEP}")
    echo
    echo "→ retention: ${#_all[@]}개 중 오래된 ${#_del[@]}개 삭제 (keep=$KEEP, dir=$BK_DIR)"
    for f in "${_del[@]}"; do rm -f "$f" && echo "    - $(basename "$f")"; done
  fi
fi
