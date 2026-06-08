#!/usr/bin/env bash
# 로컬 백업 + Drive 업로드 + 보존정책 (최신 N개만 유지).
#
# 사용:
#   bash backup-to-drive.sh
#   PROJ_DRIVE_RETAIN=10 bash backup-to-drive.sh   # 보존 개수 override
set -euo pipefail
# shellcheck source=./_drive_common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_drive_common.sh"

require_rclone

DUMP_FILE="$PROJ_DUMP_DIR/$(dump_name)"
SUM_FILE="${DUMP_FILE}.sha256"

# 1) 로컬 dump
echo "→ pg_dump → $DUMP_FILE"
pg_dump_cmd | gzip -c > "$DUMP_FILE"
SHA=$(file_sha256 "$DUMP_FILE")
echo "$SHA  $(basename "$DUMP_FILE")" > "$SUM_FILE"
SIZE=$(du -h "$DUMP_FILE" | cut -f1)
echo "[OK] $(basename "$DUMP_FILE")  $SIZE  sha256=$SHA"

# 2) 복원 가이드 자동 생성 (MXWP 패턴)
TS_TAG="$(basename "$DUMP_FILE" .sql.gz | sed "s|${PROJ_PREFIX}-db-||")"
GUIDE_FILE="$PROJ_DUMP_DIR/RESTORE-GUIDE-${TS_TAG}.md"
cat > "$GUIDE_FILE" <<EOF
# Restore Guide — $PROJ_NAME @ $TS_TAG

## 빠른 복원 (타깃 서버에서)

\`\`\`bash
cd <project_root>/scripts/drive-sync   # 또는 본 키트가 있는 곳
git pull
bash sync-from-drive.sh
\`\`\`

## 수동 복원 (이 파일을 직접 갖고 있는 경우)

\`\`\`bash
# 1. 무결성 검증
sha256sum -c $(basename "$SUM_FILE")
# 예상: $(basename "$DUMP_FILE"): OK

# 2. restore
bash restore-db.sh $(basename "$DUMP_FILE") --yes
\`\`\`

## 메타데이터

- 원본 서버: $(hostname -f 2>/dev/null || hostname)
- 생성 시각 (UTC): $TS_TAG
- DB: $POSTGRES_DB @ $POSTGRES_HOST:$POSTGRES_PORT
- 크기: $SIZE
- sha256: $SHA
EOF

# 3) Drive 업로드
echo "→ Drive 업로드: $DRIVE_PATH"
rclone copy "$DUMP_FILE" "$DRIVE_PATH/" --progress
rclone copy "$SUM_FILE"  "$DRIVE_PATH/"
rclone copy "$GUIDE_FILE" "$DRIVE_PATH/"

# 4) 보존정책 — TS 정렬 후 오래된 것 제거
echo "→ 보존정책: 최신 $DRIVE_RETAIN 개만 유지"
mapfile -t ALL < <(rclone lsf "$DRIVE_PATH" --include "${PROJ_PREFIX}-db-*.sql.gz" 2>/dev/null | sort -r)
if [[ ${#ALL[@]} -gt $DRIVE_RETAIN ]]; then
  for old in "${ALL[@]:$DRIVE_RETAIN}"; do
    echo "    - delete $old"
    rclone deletefile "$DRIVE_PATH/$old" 2>/dev/null || true
    rclone deletefile "$DRIVE_PATH/${old}.sha256" 2>/dev/null || true
    TS="$(echo "$old" | sed "s|${PROJ_PREFIX}-db-||; s|.sql.gz||")"
    rclone deletefile "$DRIVE_PATH/RESTORE-GUIDE-${TS}.md" 2>/dev/null || true
  done
fi

# 5) (옵션) 공유 링크
LINK=$(rclone link "$DRIVE_PATH/$(basename "$DUMP_FILE")" 2>/dev/null || true)

echo
echo "================================================================"
echo "✓ Drive 업로드 완료"
echo "  $DRIVE_PATH/$(basename "$DUMP_FILE")  ($SIZE)"
echo "  sha256: $SHA"
[[ -n "$LINK" ]] && {
  echo
  echo "공유 링크:"
  echo "    $LINK"
}
echo "================================================================"
