#!/usr/bin/env bash
# AI Data Hub — 빠른 상태 확인 (diag.sh 의 핵심만 출력).
# 사용: bash deploy/apptainer/status.sh
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
load_env

if [[ -t 1 ]]; then
  G="\033[32m"; R="\033[31m"; N="\033[0m"
else
  G=""; R=""; N=""
fi

mark() { [[ "$1" = "ok" ]] && printf "${G}✓${N}" || printf "${R}✗${N}"; }

# Instance — EXTERNAL 모드면 외부 인스턴스 검사
if [[ "${EXTERNAL_POSTGRES:-0}" = "1" ]]; then
  EXT_INST="${EXTERNAL_PG_INSTANCE:-mxwp_postgres}"
  if apptainer instance list 2>/dev/null | awk 'NR>1{print $1}' | grep -qx "$EXT_INST"; then
    INST_OK=ok
    INST_POSTGRES="$EXT_INST (external)"
  else
    INST_OK=fail
    INST_POSTGRES="$EXT_INST (external, MISSING)"
  fi
elif instance_running "$INST_POSTGRES"; then INST_OK=ok; else INST_OK=fail; fi

# Port
if ss -tnl 2>/dev/null | awk '{print $4}' | grep -qE "[:.]${POSTGRES_PORT}\$"; then PG_PORT=ok; else PG_PORT=fail; fi
if ss -tnl 2>/dev/null | awk '{print $4}' | grep -qE "[:.]${API_PORT}\$"; then API_PORT_OK=ok; else API_PORT_OK=fail; fi

# Health
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 3 \
            "http://127.0.0.1:${API_PORT}/api/system/health" 2>/dev/null || echo "000")
[[ "$HTTP_CODE" == "200" ]] && API_HEALTH=ok || API_HEALTH=fail

printf "%s postgres instance: %s\n" "$(mark $INST_OK)" "$INST_POSTGRES"
printf "%s port %s (postgres)\n"    "$(mark $PG_PORT)" "$POSTGRES_PORT"
printf "%s port %s (api)\n"          "$(mark $API_PORT_OK)" "$API_PORT"
printf "%s api health (HTTP %s)\n"   "$(mark $API_HEALTH)" "$HTTP_CODE"

# 종합
if [[ "$INST_OK" == "ok" && "$PG_PORT" == "ok" && "$API_PORT_OK" == "ok" && "$API_HEALTH" == "ok" ]]; then
  echo "all systems go."
  exit 0
else
  echo
  echo "상세 진단: bash deploy/apptainer/diag.sh --tail-logs"
  exit 1
fi
