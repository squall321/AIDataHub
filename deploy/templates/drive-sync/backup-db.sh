#!/usr/bin/env bash
# 로컬 pg_dump → gzip + sha256 (Drive 업로드 X).
#
# 사용: bash backup-db.sh
# 결과: $PROJ_DUMP_DIR/<proj>-db-YYYYMMDD-HHMMSSZ.sql.gz (+ .sha256)
set -euo pipefail
# shellcheck source=./_drive_common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_drive_common.sh"

DUMP_FILE="$PROJ_DUMP_DIR/$(dump_name)"
SUM_FILE="${DUMP_FILE}.sha256"

echo "→ pg_dump → $DUMP_FILE"
pg_dump_cmd | gzip -c > "$DUMP_FILE"

SIZE=$(du -h "$DUMP_FILE" | cut -f1)
SHA=$(file_sha256 "$DUMP_FILE")
echo "$SHA  $(basename "$DUMP_FILE")" > "$SUM_FILE"

echo "[OK] $DUMP_FILE  ($SIZE)"
echo "     sha256: $SHA"
echo
echo "복원:"
echo "  bash restore-db.sh $DUMP_FILE"
