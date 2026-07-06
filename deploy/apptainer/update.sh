#!/usr/bin/env bash
# AI Data Hub — 코드 최신화 후 재기동 (git pull → migrate → restart → verify).
#
# "서버를 최신상태로 바로 시작" 원샷. 로컬 데이터(DB)는 보존한다.
#   1) git pull --ff-only   — 최신 코드 (git repo 아니면 skip)
#   2) boot.sh --force      — venv 동기화 + alembic upgrade head + 재기동 + health 검증
#
# 다른 스크립트와의 차이:
#   - sync-from-drive.sh : DB 를 Drive 덤프로 DROP+복원한다(데이터 교체). 이건 안 함.
#   - boot.sh --force    : git pull 이 없다. 이건 먼저 코드를 최신화한다.
#
# 사용:
#   bash deploy/apptainer/update.sh              # git pull + 최신화 재기동
#   bash deploy/apptainer/update.sh --skip-git   # 코드는 그대로, 재기동만
#   (systemd 로 aidh.service 를 관리 중이면: git pull 후 systemctl restart aidh.service)
set -euo pipefail
# shellcheck source=/dev/null
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
load_env

SKIP_GIT=0
for arg in "$@"; do
  case "$arg" in
    --skip-git) SKIP_GIT=1 ;;
    -h|--help)  sed -n '2,15p' "$0"; exit 0 ;;
    *) echo "unknown arg: $arg" >&2; exit 2 ;;
  esac
done

echo "================================================================"
echo " AI Data Hub — update (git pull → migrate → restart → verify)"
echo " $(date '+%F %T %Z')"
echo "================================================================"

# ── 1. 최신 코드 ──────────────────────────────────────────────────
if [[ $SKIP_GIT -eq 0 ]]; then
  echo "[1/2] git pull --ff-only"
  if [[ -d "$ROOT_DIR/.git" ]]; then
    BEFORE=$(cd "$ROOT_DIR" && git rev-parse --short HEAD 2>/dev/null || echo "?")
    (cd "$ROOT_DIR" && git pull --ff-only 2>&1 | sed 's/^/    /') || {
      echo "[ERROR] git pull --ff-only 실패 — 로컬 커밋 분기/충돌 가능." >&2
      echo "        확인: (cd $ROOT_DIR && git status)" >&2
      echo "        코드 그대로 재기동만 하려면: bash $0 --skip-git" >&2
      exit 1
    }
    AFTER=$(cd "$ROOT_DIR" && git rev-parse --short HEAD 2>/dev/null || echo "?")
    if [[ "$BEFORE" == "$AFTER" ]]; then
      echo "    이미 최신 ($AFTER)"
    else
      echo "    $BEFORE → $AFTER"
    fi
  else
    echo "    (git repo 아님 — 번들 배포. 코드 그대로 재기동만)"
  fi
else
  echo "[1/2] git pull  (--skip-git)"
fi

# ── 2. 최신 스키마 + 재기동 + 검증 ───────────────────────────────
# boot.sh --force: 기존 프로세스를 정리하고 deps 동기화 → alembic upgrade head →
# uvicorn 재기동 → /api/system/health 200 확인까지 한다.
echo "[2/2] boot --force (deps 동기화 + alembic upgrade head + 재기동 + health)"
bash "$APPT_DIR/boot.sh" --force

echo
echo "================================================================"
echo "✓ update 완료 — 최신 코드 + 최신 스키마로 실행 중"
echo "================================================================"
