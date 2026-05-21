#!/usr/bin/env bash
# AI Data Hub — Google Drive 에서 최신 DB 덤프 받아와 restore (타깃 서버).
#
# 원샷 흐름 (MXWhitePaper 검증 패턴 이식):
#   1) git pull (선택)         — 코드도 최신화
#   2) 스택 보장               — postgres/api 띄움(restart.sh 위임)
#   3) Drive 최신 dump 다운로드 — 파일명 정렬(UTC TS) 기준
#   4) sha256 검증             — 무결성 깨지면 abort
#   5) restore-db.sh --yes     — 자동 안전백업 후 DROP+CREATE+restore
#   6) health 검증 + 요약       — /api/system/health 200 확인
#
# 사용:
#   bash deploy/apptainer/sync-from-drive.sh
#   bash deploy/apptainer/sync-from-drive.sh --skip-git
#   bash deploy/apptainer/sync-from-drive.sh --skip-restart
#   bash deploy/apptainer/sync-from-drive.sh --dry-run
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
load_env
export_proxy

SKIP_GIT=0; SKIP_RESTART=0; DRY=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-git)     SKIP_GIT=1; shift ;;
    --skip-restart) SKIP_RESTART=1; shift ;;
    --dry-run)      DRY=1; shift ;;
    -h|--help)      sed -n '2,18p' "$0"; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

REMOTE="${AIDH_DRIVE_REMOTE:-}"
[[ -n "$REMOTE" ]] || { echo "[ERROR] AIDH_DRIVE_REMOTE 미설정 — 먼저 setup-drive-sync.sh" >&2; exit 1; }
command -v rclone >/dev/null || { echo "[ERROR] rclone 미설치 — setup-drive-sync.sh" >&2; exit 1; }

echo "================================================================"
echo " AI Data Hub — sync-from-drive  $(date -u +%FT%TZ)"
echo "   remote: $REMOTE"
echo "   skip-git=$SKIP_GIT  skip-restart=$SKIP_RESTART  dry-run=$DRY"
echo "================================================================"

# 1) git pull
if [[ $SKIP_GIT -eq 0 ]]; then
  echo "[1/6] git pull"
  if [[ -d "$ROOT_DIR/.git" ]]; then
    (cd "$ROOT_DIR" && git pull --ff-only 2>&1 | sed 's/^/    /') || {
      echo "[WARN] git pull 실패 — 무시하고 진행 (코드 그대로)" >&2
    }
  else
    echo "    (git repo 아님 — skip)"
  fi
else
  echo "[1/6] git pull  (--skip-git)"
fi

# 2) 스택 보장 — postgres/api
if [[ $SKIP_RESTART -eq 0 && $DRY -eq 0 ]]; then
  echo "[2/6] 스택 보장 (restart.sh — 안 떠있으면 띄우고, 떠있으면 무손상)"
  bash "$APPT_DIR/restart.sh" 2>&1 | sed 's/^/    /' || {
    echo "[ERROR] 스택 기동 실패 — restore 진행 불가" >&2
    exit 1
  }
else
  echo "[2/6] 스택 보장  (--skip-restart 또는 dry-run)"
fi

# 3) Drive 최신 dump 찾기 (파일명 UTC TS 정렬)
echo "[3/6] Drive 최신 덤프 탐색"
LATEST="$(rclone lsf --files-only "$REMOTE/" 2>/dev/null | grep -E '^aidh-db-.*\.sql\.gz$' | sort | tail -n1 || true)"
if [[ -z "$LATEST" ]]; then
  echo "[ERROR] Drive 에 aidh-db-*.sql.gz 없음" >&2
  exit 1
fi
echo "    최신: $LATEST"

BACKUP_DIR="$APPT_DIR/backups"
mkdir -p "$BACKUP_DIR"
LOCAL="$BACKUP_DIR/$LATEST"
SHA_FILE="${LOCAL}.sha256"

if [[ -f "$LOCAL" ]]; then
  echo "    (이미 받음 — 재다운로드 skip)"
else
  if [[ $DRY -eq 1 ]]; then
    echo "    (dry-run — 실제 다운로드 skip)"
  else
    rclone copy --progress "$REMOTE/$LATEST"          "$BACKUP_DIR/"
    rclone copy            "$REMOTE/${LATEST}.sha256" "$BACKUP_DIR/" 2>/dev/null || true
    # 가이드도 가져오기 (있으면)
    STEM="${LATEST%.sql.gz}"; TS="${STEM#aidh-db-}"
    rclone copy "$REMOTE/RESTORE-GUIDE-${TS}.md" "$BACKUP_DIR/" 2>/dev/null || true
  fi
fi
ln -sfn "$LATEST" "$BACKUP_DIR/latest.sql.gz"

# 4) sha256 검증
echo "[4/6] sha256 검증"
if [[ -f "$SHA_FILE" && -f "$LOCAL" ]]; then
  ( cd "$BACKUP_DIR" && sha256sum -c "$(basename "$SHA_FILE")" ) || {
    echo "[ERROR] sha256 불일치 — abort (파일 손상 의심)" >&2
    exit 1
  }
else
  echo "    (sha256 파일 없음 — 검증 skip, 그래도 진행)"
fi

# 5) restore
if [[ $DRY -eq 1 ]]; then
  echo "[5/6] dry-run — restore-db.sh 호출 생략"
  echo
  echo "(dry-run) 적용하려면:"
  echo "  bash $APPT_DIR/restore-db.sh --yes $LOCAL"
  exit 0
fi
echo "[5/6] restore-db.sh --yes $LOCAL"
bash "$APPT_DIR/restore-db.sh" --yes "$LOCAL"

# 6) health + 요약
echo "[6/6] health 검증"
HC="${HOST_IP:-127.0.0.1}:${API_PORT:-8001}"
for i in $(seq 1 30); do
  if curl -fs --max-time 3 "http://${HC}/api/system/health" >/dev/null 2>&1; then
    echo "    ✓ api health 200 OK (${i}s)"; break
  fi
  sleep 1
done
echo "    /api/discover 요약:"
curl -s --max-time 6 "http://${HC}/api/discover" | python3 -c "import sys,json;d=json.load(sys.stdin);print(f'      total_records={d.get(\"total_records\")}  agents={len(d.get(\"agents\",[]))}')" 2>/dev/null || echo "    (요약 실패 — 수동: curl /api/discover)"

echo
echo "================================================================"
echo "✓ sync-from-drive 완료 — $LATEST 반영됨"
echo "================================================================"
