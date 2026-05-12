#!/usr/bin/env bash
# AI Data Hub — 기존 설치 업데이트.
#
# 사용 시나리오:
#   * git clone 으로 깐 머신에서 코드 최신화
#   * 사내 git mirror 또는 bundle 새로 받아 갱신
#   * .sif 는 그대로, 코드만 바꿔서 빠르게 적용
#
# 동작:
#   1. git pull (origin/main) — git 디렉토리가 있을 때
#   2. requirements.txt 변경 감지 → pip install 재실행
#   3. vscode_extension/package.json 변경 → npm install (선택)
#   4. alembic upgrade head (스키마 변경 시)
#   5. restart_api (env 변경 반영)
#
# 사용:
#   bash update.sh                  # 전체
#   bash update.sh --pull-only      # git pull 만 (재기동 X)
#   bash update.sh --skip-deps      # pip/npm install skip (코드만 바뀌었을 때 빠르게)
#   bash update.sh --no-migrate     # alembic skip
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APPT_DIR="$ROOT_DIR/deploy/apptainer"
API_DIR="$ROOT_DIR/api_server"

PULL_ONLY=0
SKIP_DEPS=0
NO_MIGRATE=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --pull-only)  PULL_ONLY=1; shift ;;
    --skip-deps)  SKIP_DEPS=1; shift ;;
    --no-migrate) NO_MIGRATE=1; shift ;;
    -h|--help)    sed -n '2,18p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "[ERROR] unknown arg: $1" >&2; exit 2 ;;
  esac
done

echo "================================================================"
echo " AI Data Hub — update"
echo "================================================================"

# ── 1. git pull ───────────────────────────────────────────────────
echo "[1/5] git pull"
if [[ -d "$ROOT_DIR/.git" ]]; then
  cd "$ROOT_DIR"
  BEFORE=$(git rev-parse HEAD 2>/dev/null || echo "")
  if git pull --ff-only 2>&1 | tail -3; then
    AFTER=$(git rev-parse HEAD 2>/dev/null || echo "")
    if [[ "$BEFORE" == "$AFTER" ]]; then
      echo "  · 이미 최신 (no changes)"
    else
      echo "  ✓ $BEFORE → $AFTER"
      # 변경 요약
      git log --oneline "$BEFORE..$AFTER" 2>&1 | head -10 | sed 's/^/    /'
    fi
  else
    echo "  ✗ git pull 실패 (네트워크 / merge conflict / dirty tree?)"
    exit 1
  fi
else
  echo "  · .git 없음 — bundle 기반 설치로 보임 (skip)"
  echo "  → 코드를 새로 받으려면 다시 install.sh 또는 새 bundle 추출"
fi

if [[ $PULL_ONLY -eq 1 ]]; then
  echo
  echo "✓ --pull-only — 종료 (재기동 안 함)"
  exit 0
fi

# ── 2. deps 갱신 (옵션) ───────────────────────────────────────────
echo "[2/5] 의존성"
if [[ $SKIP_DEPS -eq 1 ]]; then
  echo "  · --skip-deps — pip/npm 건너뜀"
else
  # pip install — venv 있을 때만
  if [[ -f "$API_DIR/.venv/bin/python" ]]; then
    cd "$API_DIR"
    if [[ -f requirements.txt ]]; then
      echo "  → pip install -r requirements.txt"
      .venv/bin/pip install -r requirements.txt 2>&1 | tail -5 | sed 's/^/    /'
    fi
  else
    echo "  · venv 없음 — start_api.sh 가 처음 기동 시 생성"
  fi

  # npm install — extension 빌드 필요 시
  if [[ -f "$ROOT_DIR/vscode_extension/package.json" && -d "$ROOT_DIR/vscode_extension/node_modules" ]]; then
    cd "$ROOT_DIR/vscode_extension"
    echo "  → npm install (vscode_extension)"
    npm install 2>&1 | tail -3 | sed 's/^/    /' || true
  fi
fi

# ── 3. alembic upgrade ─────────────────────────────────────────────
echo "[3/5] DB 마이그레이션"
if [[ $NO_MIGRATE -eq 1 ]]; then
  echo "  · --no-migrate — skip"
else
  # source .env to get POSTGRES_*
  if [[ -f "$APPT_DIR/.env" ]]; then
    set -a; . "$APPT_DIR/.env"; set +a
  fi
  cd "$API_DIR"
  if [[ -d .venv ]]; then
    PYTHONPATH=src \
      POSTGRES_USER="${POSTGRES_USER:-aidh}" \
      POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-aidh_change_me}" \
      POSTGRES_PORT="${POSTGRES_PORT:-5435}" \
      POSTGRES_DB="${POSTGRES_DB:-aidh}" \
      .venv/bin/alembic upgrade head 2>&1 | tail -5 | sed 's/^/    /'
    echo "  ✓ alembic upgrade head"
  else
    echo "  · venv 없음 — alembic skip (start_api.sh 가 처음 기동 시 실행)"
  fi
fi

# ── 4. restart API (코드 변경 반영) ────────────────────────────────
echo "[4/5] API 재기동"
bash "$APPT_DIR/restart.sh" --api

# ── 5. 검증 ──────────────────────────────────────────────────────
echo "[5/5] 검증"
sleep 2
bash "$APPT_DIR/diag.sh" || true

echo
echo "================================================================"
echo "✓ update 완료"
echo "================================================================"
