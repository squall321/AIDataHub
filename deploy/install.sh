#!/usr/bin/env bash
# ===========================================================================
# AI Data Hub — Linux/macOS 원터치 셋업 (Docker)
#
# 사용:
#   bash deploy/install.sh
#
# 동작:
#   1) Docker / docker-compose v2 검증
#   2) .env 가 없으면 .env.example 복사
#   3) docker compose up -d --build
#   4) /api/system/health 응답까지 대기 (최대 60초)
#   5) BOOTSTRAP_API_KEY 가 비어 있고 AUTH_REQUIRED=true 면 부트스트랩 키 발급 안내
# ===========================================================================
set -euo pipefail

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$DEPLOY_DIR")"
cd "$DEPLOY_DIR"

echo ""
echo "================================================================"
echo " AI Data Hub — install.sh"
echo "================================================================"
echo " deploy dir : $DEPLOY_DIR"
echo " project    : $ROOT_DIR"
echo "================================================================"

# ------------------------------------------------------------ 1) Docker 검증
if ! command -v docker > /dev/null 2>&1; then
    echo "[ERROR] Docker 가 필요하다." >&2
    echo "        설치: https://docs.docker.com/engine/install/" >&2
    exit 1
fi
if ! docker compose version > /dev/null 2>&1; then
    echo "[ERROR] docker compose v2 가 필요하다 ('docker compose' 명령)." >&2
    echo "        구버전 'docker-compose' 만 있다면 v2 로 업그레이드." >&2
    exit 1
fi
echo "[OK] Docker / compose v2 확인됨"

# ------------------------------------------------------------ 2) .env 준비
if [[ ! -f .env ]]; then
    cp .env.example .env
    echo "[INFO] .env 생성됨 — 비밀번호/포트 등은 운영 전 수정 권장"
fi
# .env 를 export (API_PORT 가 헬스체크에 필요)
set -a
# shellcheck disable=SC1091
. ./.env
set +a
echo "[OK] .env 로드됨 (API_PORT=${API_PORT:-8000}, POSTGRES_PORT=${POSTGRES_PORT:-5432})"

# ------------------------------------------------------------ 3) compose up
echo ""
echo "[1/3] PostgreSQL + API 빌드 + 기동..."
docker compose up -d --build
echo "[OK] 컨테이너 기동 명령 완료"

# ------------------------------------------------------------ 4) 헬스체크
echo ""
echo "[2/3] /api/system/health 응답 대기 (최대 60초)..."
HEALTH_URL="http://localhost:${API_PORT:-8000}/api/system/health"
SUCCESS=0
for i in $(seq 1 30); do
    if curl -sf -o /dev/null "$HEALTH_URL"; then
        SUCCESS=1
        break
    fi
    sleep 2
done
if [[ "$SUCCESS" -eq 1 ]]; then
    echo "[OK] API 응답 확인: $HEALTH_URL"
else
    echo "[WARN] 60초 안에 응답 없음 — 'docker compose logs api' 로 진단 권장"
fi

# ------------------------------------------------------------ 5) 부트스트랩 키
echo ""
echo "[3/3] 부트스트랩 API key 안내..."
if [[ "${AUTH_REQUIRED:-false}" == "true" && -z "${BOOTSTRAP_API_KEY:-}" ]]; then
    echo ""
    echo "[INFO] AUTH_REQUIRED=true 이고 BOOTSTRAP_API_KEY 미설정."
    echo "       다음 명령으로 운영 API key 를 발급한다:"
    echo ""
    cat <<'BOOTSTRAP_HINT'
         docker compose exec api python -c "
import asyncio
from api.database import SessionLocal
from api.auth.keys import create_api_key
async def go():
    async with SessionLocal() as s:
        row, plain = await create_api_key(s, name='bootstrap')
        print('API_KEY=', plain)
asyncio.run(go())
"
BOOTSTRAP_HINT
    echo ""
else
    echo "[OK] AUTH_REQUIRED=${AUTH_REQUIRED:-false} — 부트스트랩 키 발급 단계는 생략"
fi

# ------------------------------------------------------------ 완료
echo ""
echo "================================================================"
echo " 셋업 완료"
echo "================================================================"
echo " API        : http://localhost:${API_PORT:-8000}"
echo " 헬스체크   : http://localhost:${API_PORT:-8000}/api/system/health"
echo " API docs   : http://localhost:${API_PORT:-8000}/docs"
echo " discover   : http://localhost:${API_PORT:-8000}/api/discover"
echo ""
echo " 로그       : cd $DEPLOY_DIR && docker compose logs -f api"
echo " 재시작     : cd $DEPLOY_DIR && docker compose restart api"
echo " 종료       : cd $DEPLOY_DIR && docker compose down"
echo " 데이터삭제 : cd $DEPLOY_DIR && docker compose down -v   # 볼륨까지 제거"
echo "================================================================"
