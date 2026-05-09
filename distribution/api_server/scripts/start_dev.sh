#!/usr/bin/env bash
# start_dev.sh — one-command local dev bootstrap (POSIX).
#
# 1. PostgreSQL 컨테이너 기동 (docker compose).
# 2. healthcheck 통과 대기.
# 3. alembic upgrade head.
# 4. 표준 에이전트 시드.
# 5. uvicorn (api.main) 기동.
#
# Usage:
#   ./scripts/start_dev.sh

set -euo pipefail

# 프로젝트 루트로 이동.
SCRIPT_DIR="$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
ROOT="$( cd "$SCRIPT_DIR/.." && pwd )"
cd "$ROOT"

echo "[start_dev] root=$ROOT"

# venv 자동 활성화 (있으면).
if [ -f "$ROOT/.venv/bin/activate" ]; then
    echo "[start_dev] activating .venv"
    # shellcheck disable=SC1091
    . "$ROOT/.venv/bin/activate"
fi

export PYTHONPATH="$ROOT/src"
export PYTHONIOENCODING="utf-8"

# 1) Postgres 기동.
echo "[start_dev] docker compose up -d postgres"
docker compose up -d postgres

# 2) healthy 대기.
echo "[start_dev] waiting for postgres healthcheck..."
tries=0
max=30
cid="$(docker compose ps -q postgres)"
while [ "$tries" -lt "$max" ]; do
    status="$(docker inspect --format '{{json .State.Health.Status}}' "$cid" 2>/dev/null || echo '""')"
    if [[ "$status" == *"healthy"* ]]; then
        break
    fi
    sleep 2
    tries=$((tries + 1))
done
if [ "$tries" -ge "$max" ]; then
    echo "[start_dev] ERROR: postgres did not become healthy in $((max * 2))s" >&2
    exit 1
fi
echo "[start_dev] postgres healthy"

# 3) 마이그레이션.
echo "[start_dev] alembic upgrade head"
python -m alembic -c alembic.ini upgrade head

# 4) 표준 에이전트 시드.
echo "[start_dev] python -m api.seed"
python -m api.seed

# 5) API 서버.
echo "[start_dev] python -m api.main"
exec python -m api.main
