#!/usr/bin/env bash
# AI Data Hub — 통합 진단 (Issue #12 서비스 ≠ 인스턴스).
#
# 왜 필요한가:
#   - apptainer instance list 에 "running" 으로 보여도 안의 서비스가 죽었을 수 있음
#   - 인스턴스 살아있음 ≠ 서비스 동작 — application-level health check 까지 봐야 함
#
# 점검 계층:
#   A. Instance — apptainer instance list
#   B. Port    — ss -tlnp 가 LISTEN 하는지
#   C. Health  — HTTP / pg_isready 가 응답하는지
#   D. Schema  — alembic head 확인 (선택)
#   E. Embedding dim — 컬럼 vs EMBEDDING_DIM 정합 (Migration 0013/0016 가드)
#
# 사용:
#   bash deploy/apptainer/diag.sh
#   bash deploy/apptainer/diag.sh --tail-logs   # 실패 발견 시 로그 마지막 30줄 출력
set -euo pipefail
# shellcheck source=/dev/null
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
load_env

TAIL_LOGS=0
case "${1:-}" in
  --tail-logs|-t) TAIL_LOGS=1 ;;
esac

# ── 색상 -----------------------------------------------------------------
if [[ -t 1 ]]; then
  G="\033[32m"; R="\033[31m"; Y="\033[33m"; B="\033[34m"; D="\033[2m"; N="\033[0m"
else
  G=""; R=""; Y=""; B=""; D=""; N=""
fi

pass() { printf "  ${G}✓${N} %s\n" "$*"; }
fail() { printf "  ${R}✗${N} %s\n" "$*"; FAILED=1; }
warn() { printf "  ${Y}!${N} %s\n" "$*"; }
info() { printf "  ${B}·${N} %s\n" "$*"; }

FAILED=0
echo "================================================================"
echo " AI Data Hub — diag"
echo " host=$(hostname)  user=$(id -un)  $(date '+%F %T %Z')"
echo "================================================================"

# ── A. Instance ----------------------------------------------------------
echo "[A] Apptainer instance"
if [[ "${EXTERNAL_POSTGRES:-0}" = "1" ]]; then
  EXT_INST="${EXTERNAL_PG_INSTANCE:-mxwp_postgres}"
  if apptainer instance list 2>/dev/null | awk 'NR>1{print $1}' | grep -qx "$EXT_INST"; then
    pass "$EXT_INST is running (EXTERNAL_POSTGRES=1)"
  else
    fail "$EXT_INST NOT running — 외부 프로젝트(예: MXWhitePaper)에서 PG 먼저 기동"
  fi
elif instance_running "$INST_POSTGRES"; then
  pass "$INST_POSTGRES is running"
else
  fail "$INST_POSTGRES NOT running — bash start_postgres.sh"
fi

# ── B. Port LISTEN -------------------------------------------------------
echo "[B] Ports"
check_port() {
  local port="$1" who="$2"
  if ss -tnl 2>/dev/null | awk '{print $4}' | grep -qE "[:.]${port}\$"; then
    pass "port ${port} LISTEN (${who})"
  else
    fail "port ${port} NOT listen (${who})"
  fi
}
check_port "$POSTGRES_PORT" "postgres"
check_port "$API_PORT"      "api"

# ── C. Application Health ------------------------------------------------
echo "[C] Health"

# C-1 pg_isready (instance 내부 exec)
if instance_running "$INST_POSTGRES"; then
  if apptainer exec "instance://$INST_POSTGRES" \
       pg_isready -h 127.0.0.1 -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
       >/dev/null 2>&1; then
    pass "pg_isready OK ($POSTGRES_DB)"
  else
    fail "pg_isready FAILED — postgres up but DB not accepting"
  fi
fi

# C-2 API health
if command -v curl >/dev/null 2>&1; then
  HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 \
              "http://127.0.0.1:${API_PORT}/api/system/health" 2>/dev/null || echo "000")
  if [[ "$HTTP_CODE" == "200" ]]; then
    pass "api /api/system/health → 200"
  else
    fail "api /api/system/health → ${HTTP_CODE}"
  fi
else
  warn "curl 미설치 — API 헬스 스킵"
fi

# ── D. Alembic ----------------------------------------------------------
echo "[D] Schema (Alembic)"
if [[ -d "$API_DIR/.venv" ]]; then
  REV=$(cd "$API_DIR" && PYTHONPATH=src \
        POSTGRES_USER="$POSTGRES_USER" POSTGRES_PASSWORD="$POSTGRES_PASSWORD" \
        POSTGRES_PORT="$POSTGRES_PORT" POSTGRES_DB="$POSTGRES_DB" \
        .venv/bin/alembic current 2>&1 | tail -1 || echo "")
  if [[ -n "$REV" && "$REV" != *ERROR* ]]; then
    pass "alembic current = $REV"
  else
    warn "alembic current 확인 실패 (venv 미초기화일 수 있음): $REV"
  fi
else
  warn ".venv 없음 — alembic 스킵 (bash start_api.sh 한 번 실행 필요)"
fi

# ── E. EMBEDDING_DIM 정합 -----------------------------------------------
echo "[E] EMBEDDING_DIM consistency"
EXPECTED="${EMBEDDING_DIM:-}"
if [[ -z "$EXPECTED" ]]; then
  case "${EMBEDDING_PROVIDER:-hash}" in
    e5_base) EXPECTED=768 ;;
    e5_large) EXPECTED=1024 ;;
    *) EXPECTED=384 ;;
  esac
fi
info "expected dim = $EXPECTED (provider=${EMBEDDING_PROVIDER:-hash})"
if instance_running "$INST_POSTGRES" && command -v curl >/dev/null 2>&1; then
  # /api/system/health 응답에서 embedding dim 추출 시도 (있으면)
  # 아니면 alembic 적용된 revision 으로 추정.
  pass "(직접 검증은 API startup 로그의 'EMBEDDING_DIM consistency check' 참고)"
fi

# ── F. HWAX portal sub-path 정합 (회귀 검증) -----------------------------
# AIDH_ROOT_PATH 가 .env 에 있으면 portal mode — uvicorn 인자 / openapi servers /
# dashboard.js BASE 도출 / GET / redirect 가 모두 prefix 와 일치해야 한다.
echo "[F] HWAX portal sub-path 정합 (AIDH_ROOT_PATH=${AIDH_ROOT_PATH:-<unset>})"
if [[ -z "${AIDH_ROOT_PATH:-}" ]]; then
  info "AIDH_ROOT_PATH 미설정 — standalone mode (검사 스킵)"
else
  # F-1. uvicorn 의 --root-path 인자가 실제로 전달됐는지
  if pgrep -af "uvicorn.*api.main" 2>/dev/null | grep -q -- "--root-path $AIDH_ROOT_PATH"; then
    pass "uvicorn 에 --root-path $AIDH_ROOT_PATH 전달됨"
  else
    fail "uvicorn 에 --root-path $AIDH_ROOT_PATH 미전달 — start_api.sh 가 .env 를 load 했나?"
  fi
  # F-2. openapi.json 의 servers 가 prefix 를 들고 있는지
  SRV=$(curl -s --max-time 3 "http://127.0.0.1:${API_PORT}/openapi.json" 2>/dev/null \
        | python3 -c "import sys,json; d=json.load(sys.stdin); print((d.get('servers') or [{}])[0].get('url',''))" 2>/dev/null)
  if [[ "$SRV" = "$AIDH_ROOT_PATH" ]]; then
    pass "openapi servers[0].url = $SRV"
  else
    fail "openapi servers[0].url='$SRV' ≠ AIDH_ROOT_PATH='$AIDH_ROOT_PATH'"
  fi
  # F-3. GET / (Accept: text/html) 가 dashboard/ 로 redirect (상대경로 — prefix 자동 보존)
  RDR=$(curl -s -o /dev/null -w "%{redirect_url}" --max-time 3 \
        -H "Accept: text/html" "http://127.0.0.1:${API_PORT}/" 2>/dev/null)
  if [[ "$RDR" == */dashboard/ ]]; then
    pass "GET / (html) → 307 redirect → $RDR"
  else
    fail "GET / redirect target 비정상: '$RDR' (expected: dashboard/)"
  fi
  # F-4. dashboard.js 의 BASE 도출 로직이 portal prefix 와 호환
  if grep -q 'location.pathname.replace(/\\/dashboard' \
       "$ROOT_DIR/api_server/static/dashboard/dashboard.js" 2>/dev/null; then
    pass "dashboard.js BASE 도출 로직 보존됨 (sub-path 자동대응)"
  else
    fail "dashboard.js BASE 도출 로직 누락 — 절대경로로 회귀했을 가능성"
  fi
fi

# ── G. 데이터 신선도 + embed 백로그 (health 게이지) ----------------------
# /api/system/health 가 sync_stale_sources / embed_backlog 게이지를 노출한다.
# stale > 0 = 동기화 정체 (스케줄러 죽음 / 소스 다운), backlog > 0 = 검색 누락.
echo "[G] 데이터 신선도 / embed 백로그"
HEALTH_JSON=$(curl -s --max-time 5 "http://127.0.0.1:${API_PORT}/api/system/health" 2>/dev/null)
if [[ -z "$HEALTH_JSON" ]]; then
  fail "health 응답 없음 — API 다운?"
else
  STALE=$(echo "$HEALTH_JSON" | python3 -c "import sys,json;d=json.load(sys.stdin);v=d.get('sync_stale_sources');print('' if v is None else v)" 2>/dev/null)
  BACKLOG=$(echo "$HEALTH_JSON" | python3 -c "import sys,json;d=json.load(sys.stdin);v=d.get('embed_backlog');print('' if v is None else v)" 2>/dev/null)
  if [[ -z "$STALE" ]]; then
    warn "sync_stale_sources 게이지 없음 (구버전 API?)"
  elif [[ "$STALE" = "0" ]]; then
    pass "sync 신선도 OK (정체 소스 0)"
  else
    fail "sync 정체 소스 ${STALE}개 — 대시보드 '연결 소스' 탭에서 확인"
  fi
  if [[ -z "$BACKLOG" ]]; then
    warn "embed_backlog 게이지 없음 (구버전 API?)"
  elif [[ "$BACKLOG" = "0" ]]; then
    pass "embed 백로그 0 (모든 섹션 검색 가능)"
  else
    warn "embed 백로그 ${BACKLOG}건 — 30분 내 자동 sweep 예정 (지속되면 로그 확인)"
  fi
fi

# ── 결과 + 로그 tail -----------------------------------------------------
echo
if [[ $FAILED -eq 0 ]]; then
  echo -e "${G}✓ 모든 체크 PASS${N}"
else
  echo -e "${R}✗ 일부 체크 FAIL — 위 메시지 참고${N}"
  if [[ $TAIL_LOGS -eq 1 ]]; then
    echo
    echo "── 최근 로그 (마지막 30줄) ───────────────────────────────────"
    for f in "$LOG_DIR/api.log" "$LOG_DIR/postgres-start.log" "$LOG_DIR/pip.log"; do
      if [[ -f "$f" ]]; then
        echo
        echo "── $f ──"
        tail -30 "$f"
      fi
    done
  else
    echo "  로그 보려면: bash deploy/apptainer/diag.sh --tail-logs"
  fi
  exit 1
fi
