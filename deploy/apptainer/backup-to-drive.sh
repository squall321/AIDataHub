#!/usr/bin/env bash
# AI Data Hub — DB 덤프 → Google Drive 업로드 (+ retention, RESTORE-GUIDE).
#
# 흐름: backup-db.sh 로 .sql.gz 생성 → sha256 → RESTORE-GUIDE-*.md →
#       rclone 업로드 → Drive 보존정책(최신 AIDH_DRIVE_RETAIN 개만 유지).
# 사용:
#   bash deploy/apptainer/backup-to-drive.sh
#   AIDH_DRIVE_RETAIN=10 bash deploy/apptainer/backup-to-drive.sh   # 보존 N개 강제
#   bash deploy/apptainer/backup-to-drive.sh --note "before-migration"
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
load_env
export_proxy

# 실패 marker — cron 의 조용한 실패를 watchdog 이 감지할 수 있게.
# 성공 종료 시 마지막에 제거된다.
_FAIL_MARKER="$APPT_DIR/backups/.last-backup-failed"
mkdir -p "$APPT_DIR/backups"
trap 'rc=$?; if [[ $rc -ne 0 ]]; then date -u +"%FT%TZ rc=$rc" > "$_FAIL_MARKER"; fi' EXIT

NOTE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --note) NOTE="${2:-}"; shift 2 ;;
    --note=*) NOTE="${1#*=}"; shift ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

REMOTE="${AIDH_DRIVE_REMOTE:-}"
RETAIN="${AIDH_DRIVE_RETAIN:-5}"
if [[ -z "$REMOTE" ]]; then
  echo "[ERROR] AIDH_DRIVE_REMOTE 미설정 — 먼저 setup-drive-sync.sh" >&2
  exit 1
fi
command -v rclone >/dev/null || { echo "[ERROR] rclone 미설치"; exit 1; }
if ! instance_running "$INST_POSTGRES"; then
  echo "[ERROR] $INST_POSTGRES 미동작 — 먼저 start_postgres.sh" >&2
  exit 1
fi

# 1) 로컬 dump (UTC 타임스탬프 — 파일명 정렬로 최신 판별)
BACKUP_DIR="$APPT_DIR/backups"
mkdir -p "$BACKUP_DIR"
TS="$(date -u +%Y%m%d-%H%M%SZ)"
DUMP="$BACKUP_DIR/aidh-db-${TS}.sql.gz"

bash "$APPT_DIR/backup-db.sh" "$DUMP"

# 2) sha256
SHA="$(sha256sum "$DUMP" | awk '{print $1}')"
echo "$SHA  $(basename "$DUMP")" > "${DUMP}.sha256"

# 3) RESTORE-GUIDE
SIZE="$(ls -lh "$DUMP" | awk '{print $5}')"
GUIDE="$BACKUP_DIR/RESTORE-GUIDE-${TS}.md"
cat > "$GUIDE" <<EOF
# AI Data Hub — Restore Guide ($TS)

| 항목 | 값 |
|---|---|
| 파일 | \`$(basename "$DUMP")\` |
| 크기 | $SIZE |
| sha256 | \`$SHA\` |
| 호스트 | $(hostname) |
| 시각(UTC) | $TS |
| 메모 | ${NOTE:-(없음)} |

## 복원 (타깃 서버에서)

\`\`\`
bash deploy/apptainer/sync-from-drive.sh        # 자동: 최신 다운로드 + 검증 + restore
\`\`\`

수동 단계로 풀려면:
\`\`\`
rclone copy "$REMOTE/$(basename "$DUMP")" deploy/apptainer/backups/
sha256sum -c deploy/apptainer/backups/$(basename "$DUMP").sha256
bash deploy/apptainer/restore-db.sh --yes deploy/apptainer/backups/$(basename "$DUMP")
\`\`\`

> 주의: \`restore-db.sh\` 는 자동 안전백업 후 DROP+CREATE+restore (기존 데이터 덮어씀).
EOF

# 4) 업로드 (dump + sha256 + guide)
echo "→ Drive 업로드: $REMOTE"
rclone copy --progress "$DUMP"        "$REMOTE/"
rclone copy            "${DUMP}.sha256" "$REMOTE/"
rclone copy            "$GUIDE"       "$REMOTE/"

# 5) Drive 보존정책 — 최신 RETAIN 개 외 삭제 (sha256/guide 매칭본도)
if [[ "$RETAIN" -gt 0 ]]; then
  echo "→ 보존정책: 최신 $RETAIN 개만 유지"
  mapfile -t _all < <(rclone lsf --files-only "$REMOTE/" 2>/dev/null | grep -E '^aidh-db-.*\.sql\.gz$' | sort)
  if (( ${#_all[@]} > RETAIN )); then
    _del_count=$(( ${#_all[@]} - RETAIN ))
    for ((i=0; i<_del_count; i++)); do
      n="${_all[$i]}"
      stem="${n%.sql.gz}"
      ts="${stem#aidh-db-}"
      echo "    - $n  +  ${n}.sha256  +  RESTORE-GUIDE-${ts}.md"
      rclone deletefile "$REMOTE/$n"             2>/dev/null || true
      rclone deletefile "$REMOTE/${n}.sha256"    2>/dev/null || true
      rclone deletefile "$REMOTE/RESTORE-GUIDE-${ts}.md" 2>/dev/null || true
    done
  fi
fi

echo
echo "================================================================"
echo "✓ Drive 업로드 완료"
echo "  $REMOTE/$(basename "$DUMP")  ($SIZE)"
echo "  sha256: $SHA"
echo
echo "공유 링크:"
rclone link "$REMOTE/$(basename "$DUMP")" 2>/dev/null | sed 's/^/    /' || echo "    (rclone link 실패 — 공유 권한 확인)"
echo "================================================================"

# 성공 — 실패 marker 제거 (watchdog 의 경고 해제)
rm -f "$_FAIL_MARKER" 2>/dev/null || true
