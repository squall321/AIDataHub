#!/usr/bin/env bash
# wave-5 (+ wave-1~4) 운영 검증 자동 스크립트.
# Runbook docs/04-report/wave-5-ops-runbook.md 의 §0~§4 + §6 진단을 자동화.
# 사용:
#   bash deploy/apptainer/verify-wave-5.sh                   # 기본 모든 체크
#   bash deploy/apptainer/verify-wave-5.sh --skip-seed       # 도구 업로드는 건너뜀
#   AIDH_BASE_URL=http://10.252.39.181:8001 bash ...         # base URL 명시
#
# 메모리 규칙: dev PC 에서는 실행 금지. 타겟 서버에서만.
# 이 스크립트는 install 안 함, 진단만.

set -uo pipefail
SKIP_SEED=0
for a in "$@"; do
  case "$a" in
    --skip-seed) SKIP_SEED=1 ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    *) echo "unknown arg: $a" >&2; exit 2 ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# .env 가 있으면 로드
[[ -f "$SCRIPT_DIR/.env" ]] && set -a && . "$SCRIPT_DIR/.env" && set +a

BASE="${AIDH_BASE_URL:-${HOST_URL:-http://127.0.0.1:${API_PORT:-8001}}}"
VENV_PY="$REPO_ROOT/api_server/.venv/bin/python3"

PASS=0; FAIL=0; WARN=0
ok()   { echo "  [ OK ] $*"; PASS=$((PASS+1)); }
fail() { echo "  [FAIL] $*"; FAIL=$((FAIL+1)); }
warn() { echo "  [WARN] $*"; WARN=$((WARN+1)); }
sec()  { echo; echo "================ $* ================"; }

# ─────────────────────────────────────────────────
# §0 사전 준비
# ─────────────────────────────────────────────────
sec "§0 사전 준비"
[[ -x "$VENV_PY" ]] && ok "venv python 존재 ($VENV_PY)" || { fail "venv 없음"; exit 1; }
PYVER=$("$VENV_PY" --version 2>&1)
[[ "$PYVER" == *"3.12"* ]] && ok "$PYVER" || warn "$PYVER (Python 3.12 권장)"
command -v apptainer >/dev/null 2>&1 && ok "apptainer: $(apptainer --version 2>&1 | head -1)" || warn "apptainer 없음 (wave-5 P1 빌드 불가)"
command -v nvidia-smi >/dev/null 2>&1 && ok "nvidia-smi 가용" || warn "nvidia-smi 없음 (GPU 비활성 — embedding CPU 폴백)"

# ─────────────────────────────────────────────────
# §1 DB Migration
# ─────────────────────────────────────────────────
sec "§1 DB Migration (alembic)"
if [[ -n "${DATABASE_URL:-}" ]]; then
  cd "$REPO_ROOT/api_server"
  HEAD=$("$VENV_PY" -m alembic current 2>&1 | tail -1 | awk '{print $1}')
  if [[ "$HEAD" == "0023"* ]]; then
    ok "alembic head = 0023 (mcp_upstreams 적용됨)"
  elif [[ "$HEAD" == "0021"* ]]; then
    warn "alembic head = 0021 — 0023 적용 필요: alembic upgrade head"
  else
    warn "alembic head = $HEAD — 0023 으로 업그레이드 필요"
  fi
  cd - >/dev/null
else
  warn "DATABASE_URL 미설정 — alembic 체크 skip"
fi

# ─────────────────────────────────────────────────
# §2 API health
# ─────────────────────────────────────────────────
sec "§2 API health ($BASE)"
HEALTH=$(curl -s -m 5 "$BASE/health" 2>/dev/null)
if [[ "$HEALTH" == *'"status":"ok"'* ]]; then
  ok "GET /health → ok"
else
  fail "GET /health 실패 — API 미기동? ($HEALTH)"
  exit 1
fi

DISCOVER=$(curl -s -m 10 "$BASE/api/discover" | head -c 200)
[[ -n "$DISCOVER" ]] && ok "GET /api/discover 응답 OK" || fail "GET /api/discover 실패"

# ─────────────────────────────────────────────────
# §3 MCP tool list (자체 + 동적 스크립트 + wave-5 + wave-6)
# ─────────────────────────────────────────────────
sec "§3 MCP tools (tools/list)"
TOOLS_RAW=$(curl -s -m 10 -X POST "$BASE/mcp/" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' 2>/dev/null)
# MCP streamable_http 는 SSE 포맷 (event: message\ndata: {json}) — data: 라인만 추출.
TOOLS_JSON=$(echo "$TOOLS_RAW" | sed -n 's/^data: //p' | head -1)
[[ -z "$TOOLS_JSON" ]] && TOOLS_JSON="$TOOLS_RAW"  # 평문 JSON 폴백
TOOL_COUNT=$(echo "$TOOLS_JSON" | "$VENV_PY" -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('result',{}).get('tools',[])))" 2>/dev/null)
[[ "$TOOL_COUNT" -ge 12 ]] && ok "MCP tools 수: $TOOL_COUNT (>=12 built-in 정상)" || fail "MCP tools 수 비정상: $TOOL_COUNT"

# 핵심 도구 존재 확인
for tname in discover agent_search recommend_agents hybrid_search semantic_search; do
  echo "$TOOLS_JSON" | grep -q "\"name\":\"$tname\"" && ok "tool 존재: $tname" || fail "tool 누락: $tname"
done

# wave-4 동적 스크립트
echo "$TOOLS_JSON" | grep -q '"name":"echo_args"' && ok "wave-4 echo_args 등록됨" || warn "echo_args 미등록 — mcp_scripts/ 점검"

# ─────────────────────────────────────────────────
# §4 신규 라우터 (wave-5 / wave-6)
# ─────────────────────────────────────────────────
sec "§4 신규 라우터"
MT=$(curl -s -m 5 "$BASE/api/mcp_tools/" 2>/dev/null)
[[ -n "$MT" ]] && ok "GET /api/mcp_tools/ 응답 OK (도구 수: $(echo "$MT" | "$VENV_PY" -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null))" || fail "GET /api/mcp_tools/ 실패"

UP=$(curl -s -m 5 "$BASE/api/mcp/upstreams" 2>/dev/null)
[[ -n "$UP" ]] && ok "GET /api/mcp/upstreams 응답 OK (upstream 수: $(echo "$UP" | "$VENV_PY" -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null))" || fail "GET /api/mcp/upstreams 실패"

MET=$(curl -s -m 5 "$BASE/api/metrics/mcp?tail=10" 2>/dev/null)
[[ "$MET" == *'"total"'* ]] && ok "GET /api/metrics/mcp 응답 OK" || warn "GET /api/metrics/mcp 응답 비정상 (JSONL 비어있을 수 있음)"

# ─────────────────────────────────────────────────
# §5 도구 업로드 + 호출 (옵션 — --skip-seed 로 skip)
# ─────────────────────────────────────────────────
if [[ $SKIP_SEED -eq 1 ]]; then
  sec "§5 도구 업로드 (--skip-seed — 건너뜀)"
else
  sec "§5 도구 업로드 + 호출 (stress_strain_plot)"
  echo "  → seed-stress-strain.sh 위임 (별 스크립트)"
  if [[ -x "$SCRIPT_DIR/seed-stress-strain.sh" ]]; then
    AIDH_BASE_URL="$BASE" bash "$SCRIPT_DIR/seed-stress-strain.sh" || warn "seed 일부 실패 — 위 로그 참고"
  else
    warn "seed-stress-strain.sh 미존재 또는 실행 권한 없음"
  fi
fi

# ─────────────────────────────────────────────────
# §6 요약
# ─────────────────────────────────────────────────
sec "요약"
echo "  PASS: $PASS / FAIL: $FAIL / WARN: $WARN"
if [[ $FAIL -eq 0 ]]; then
  echo "  → 검증 OK. wave-5 운영 가능."
  exit 0
else
  echo "  → 검증 실패. 위 [FAIL] 항목 + runbook docs/04-report/wave-5-ops-runbook.md §6 참고."
  exit 1
fi
