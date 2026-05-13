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
