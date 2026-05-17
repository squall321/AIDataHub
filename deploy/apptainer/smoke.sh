#!/usr/bin/env bash
# AI Data Hub — 스모크 테스트 (회귀 가드).
#
# 수동 curl 로만 검증해 온 핵심 경로를 한 번에 빠르게 친다. 배포/업데이트
# 후 또는 코드 변경 후 "골격이 안 깨졌나" 를 수초에 확인.
#
# 검사: API health → REST(discover/records) → MCP(initialize 핸드셰이크
#       /tools/list/tools/call) → record 1건 read.
#
# 사용:
#   bash deploy/apptainer/smoke.sh            # 로컬 (127.0.0.1:API_PORT)
#   bash deploy/apptainer/smoke.sh http://host:8001   # 대상 URL 지정
#
# 종료코드 0 = 전부 통과. 1 = 하나라도 실패 (CI/cron 에서 활용 가능).
set -uo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
load_env

BASE="${1:-http://127.0.0.1:${API_PORT}}"
BASE="${BASE%/}"
MCP="$BASE/mcp/"
HJ=(-H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream')

if [[ -t 1 ]]; then G="\033[32m"; R="\033[31m"; N="\033[0m"; else G=""; R=""; N=""; fi
PASS=0; FAIL=0
ok()   { printf "  ${G}✓${N} %s\n" "$*"; PASS=$((PASS+1)); }
bad()  { printf "  ${R}✗${N} %s\n" "$*"; FAIL=$((FAIL+1)); }

echo "================================================================"
echo " AI Data Hub — smoke ($BASE)"
echo "================================================================"

# 1. health
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 "$BASE/api/system/health")
[[ "$code" == "200" ]] && ok "api/system/health → 200" || bad "api/system/health → $code"

# 2. discover (REST) — total_records 숫자
tr=$(curl -s --max-time 10 "$BASE/api/discover" | grep -o '"total_records"[: ]*[0-9]*' | grep -o '[0-9]*$')
[[ -n "$tr" ]] && ok "REST /api/discover → total_records=$tr" || bad "REST /api/discover (total_records 없음)"

# 3. records 목록
rc=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$BASE/api/records?limit=1")
[[ "$rc" == "200" ]] && ok "REST /api/records → 200" || bad "REST /api/records → $rc"

# 4. MCP initialize 핸드셰이크
init=$(curl -s --max-time 10 -X POST "$MCP" "${HJ[@]}" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"smoke","version":"1"}}}')
echo "$init" | grep -q '"protocolVersion"' && ok "MCP initialize 핸드셰이크" || bad "MCP initialize 실패"

# 5. MCP tools/list — 11개
ntools=$(curl -s --max-time 10 -X POST "$MCP" "${HJ[@]}" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' | grep -o '"name":"[^"]*"' | wc -l | tr -d ' ')
[[ "${ntools:-0}" -ge 11 ]] && ok "MCP tools/list → ${ntools}개" || bad "MCP tools/list → ${ntools}개 (11 기대)"

# 6. MCP tools/call discover
mt=$(curl -s --max-time 15 -X POST "$MCP" "${HJ[@]}" \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"discover","arguments":{}}}' \
  | grep -o '"total_records"[^,]*' | head -1)
[[ -n "$mt" ]] && ok "MCP tools/call discover → $mt" || bad "MCP tools/call discover 실패"

# 7. record 1건 상세 read (있으면)
rid=$(curl -s --max-time 10 "$BASE/api/records?limit=1" | grep -o '"id":"[^"]*"' | head -1 | cut -d'"' -f4)
if [[ -n "$rid" ]]; then
  drc=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$BASE/api/records/$rid")
  [[ "$drc" == "200" ]] && ok "record read $rid → 200" || bad "record read $rid → $drc"
else
  echo "  · record 0건 — read 검사 skip (적재 전이면 정상)"
fi

echo "================================================================"
if [[ "$FAIL" -eq 0 ]]; then
  printf "${G}✓ smoke PASS${N} (%d checks)\n" "$PASS"
  exit 0
else
  printf "${R}✗ smoke FAIL${N} (pass=%d fail=%d)\n" "$PASS" "$FAIL"
  echo "  상세 진단: bash $APPT_DIR/diag.sh --tail-logs"
  exit 1
fi
