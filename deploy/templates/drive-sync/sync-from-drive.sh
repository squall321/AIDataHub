#!/usr/bin/env bash
# Drive 최신 dump → 다운로드 → 무결성 검증 → restore → health-check.
#
# 사용:
#   bash sync-from-drive.sh
#   bash sync-from-drive.sh --dry-run   # 어떤 파일을 받을지만 확인
set -euo pipefail
# shellcheck source=./_drive_common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_drive_common.sh"

require_rclone

DRY=0
for arg in "$@"; do
  [[ "$arg" = "--dry-run" ]] && DRY=1
done

echo "================================================================"
echo " sync-from-drive — project=$PROJ_NAME  remote=$DRIVE_PATH"
echo "================================================================"

# 1) Drive 에서 최신 파일 식별 (TS 정렬 마지막)
LATEST=$(rclone lsf "$DRIVE_PATH" --include "${PROJ_PREFIX}-db-*.sql.gz" 2>/dev/null \
         | sort | tail -1)
if [[ -z "$LATEST" ]]; then
  echo "[ERROR] $DRIVE_PATH 에 ${PROJ_PREFIX}-db-*.sql.gz 없음." >&2
  exit 1
fi
echo "→ 최신 dump: $LATEST"

if [[ $DRY -eq 1 ]]; then
  echo "(dry-run) 다운로드/복원 안 함. 종료."
  exit 0
fi

# 2) 다운로드 (sql.gz + sha256)
LOCAL="$PROJ_DUMP_DIR/$LATEST"
rclone copy "$DRIVE_PATH/$LATEST"        "$PROJ_DUMP_DIR/" --progress
rclone copy "$DRIVE_PATH/${LATEST}.sha256" "$PROJ_DUMP_DIR/" 2>/dev/null || true

# 3) sha256 검증
SUM_FILE="${LOCAL}.sha256"
if [[ -f "$SUM_FILE" ]]; then
  CUR=$(file_sha256 "$LOCAL")
  EXP=$(awk '{print $1}' "$SUM_FILE")
  if [[ "$CUR" != "$EXP" ]]; then
    echo "[ERROR] sha256 mismatch — abort" >&2
    exit 1
  fi
  echo "[OK] sha256 검증 통과"
else
  echo "[WARN] sha256 파일 없음 — 검증 스킵"
fi

# 4) restore (--yes 자동)
echo "→ restore 시작 (안전백업 자동 생성)"
bash "$DS_DIR/restore-db.sh" "$LOCAL" --yes

# 5) (선택) health-check
if [[ -n "$PROJ_HEALTH_URL" ]]; then
  echo "→ health-check: $PROJ_HEALTH_URL"
  sleep 3
  health_check || echo "[WARN] health 미통과 — 서비스가 자동 재시작 중일 수 있음"
fi

echo
echo "================================================================"
echo "✓ sync-from-drive 완료"
echo "  복원된 dump: $LATEST"
echo "================================================================"
