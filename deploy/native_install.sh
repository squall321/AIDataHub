#!/usr/bin/env bash
# ===========================================================================
# AI Data Hub — Linux/macOS native 설치 (Docker 없는 환경)
#
# 전제:
#   - PostgreSQL 16+ 설치 + 실행 중 (host=localhost 또는 .env 의 DATABASE_URL)
#   - pgvector 확장 설치 (CREATE EXTENSION vector)
#       Debian/Ubuntu: sudo apt install postgresql-16-pgvector
#       RHEL/Rocky   : dnf install postgresql-pgvector
#       macOS (brew) : brew install pgvector
#   - Python 3.12 + python3-venv
#
# 사용:
#   bash deploy/native_install.sh
# ===========================================================================
set -euo pipefail

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$DEPLOY_DIR")"
API_DIR="$ROOT_DIR/api_server"
cd "$API_DIR"

echo "================================================================"
echo " AI Data Hub — native_install.sh"
echo "================================================================"
echo " api_server : $API_DIR"

# ---- Python 검증 ----------------------------------------------------------
if ! command -v python3 > /dev/null 2>&1; then
    echo "[ERROR] python3 가 필요하다." >&2
    exit 1
fi

PYBIN="python3"
if command -v python3.12 > /dev/null 2>&1; then
    PYBIN="python3.12"
fi
echo "[OK] Python: $($PYBIN --version)"

# ---- venv ----------------------------------------------------------------
if [[ ! -d ".venv" ]]; then
    echo "[INFO] .venv 생성"
    "$PYBIN" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
echo "[OK] venv 활성화"

# ---- 의존성 ---------------------------------------------------------------
echo "[1/4] pip install -r requirements.txt..."
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
echo "[OK] 의존성 설치 완료"

# ---- .env -----------------------------------------------------------------
if [[ ! -f .env ]]; then
    if [[ -f .env.example ]]; then
        cp .env.example .env
        echo "[INFO] .env 생성됨 — DATABASE_URL 등 확인 후 다시 실행"
        echo "       편집: \$EDITOR $API_DIR/.env"
        exit 0
    fi
    echo "[ERROR] .env / .env.example 둘 다 없음" >&2
    exit 1
fi
echo "[OK] .env 발견"

# ---- alembic 마이그레이션 -------------------------------------------------
echo "[2/4] Alembic upgrade head..."
export PYTHONPATH="$API_DIR/src"
alembic upgrade head
echo "[OK] 마이그레이션 적용됨"

# ---- 시드 -----------------------------------------------------------------
echo "[3/4] Agent seed (멱등)..."
python -m api.seed -v || echo "[WARN] seed 실패 — 계속 진행"

# ---- 디렉터리 -------------------------------------------------------------
mkdir -p figures attachments output

# ---- 안내 -----------------------------------------------------------------
echo ""
echo "================================================================"
echo " 셋업 완료"
echo "================================================================"
echo " 서버 실행:"
echo "   cd $API_DIR"
echo "   source .venv/bin/activate"
echo "   export PYTHONPATH=src"
echo "   python -m api.main"
echo ""
echo " 또는 직접 uvicorn:"
echo "   uvicorn api.main:app --host 0.0.0.0 --port 8000"
echo "================================================================"
