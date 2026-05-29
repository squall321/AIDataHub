#!/usr/bin/env bash
# AX Hub × SignalForge 1회 셋업 스크립트
#
# 사용법:
#   ./setup.sh <AIDH_BASE_URL> <AIDH_API_KEY> <SF_BASE_URL> <SF_API_KEY>
#
# 예시:
#   ./setup.sh http://aidatahub:8001 $AIDH_ADMIN_KEY http://signalforge-backend:8000 $SF_INTERNAL_KEY

set -euo pipefail

AIDH_URL="${1:-}"
AIDH_KEY="${2:-}"
SF_URL="${3:-}"
SF_KEY="${4:-}"

if [[ -z "$AIDH_URL" || -z "$AIDH_KEY" || -z "$SF_URL" || -z "$SF_KEY" ]]; then
  echo "usage: $0 <AIDH_BASE_URL> <AIDH_API_KEY> <SF_BASE_URL> <SF_API_KEY>"
  exit 1
fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
H_JSON=(-H "Content-Type: application/json" -H "X-API-Key: $AIDH_KEY")

ok()   { printf "  [OK ] %s\n" "$*"; }
warn() { printf "  [SKIP] %s\n" "$*"; }
fail() { printf "  [FAIL] %s\n" "$*"; exit 1; }

# 200/201 → ok, 409 → skip, 그 외 → fail
post() {
  local path="$1" body="$2" label="$3"
  local resp status
  resp=$(curl -sS -o /tmp/_aidh_resp.$$ -w "%{http_code}" -X POST "${H_JSON[@]}" "$AIDH_URL$path" -d "$body" || true)
  status="$resp"
  case "$status" in
    200|201) ok   "$label" ;;
    409)     warn "$label (already exists)" ;;
    *)       cat /tmp/_aidh_resp.$$ >&2; fail "$label (HTTP $status)" ;;
  esac
  rm -f /tmp/_aidh_resp.$$
}

echo "==> 1) MX team 확인"
mx_exists=$(curl -sS -H "X-API-Key: $AIDH_KEY" "$AIDH_URL/api/org/teams" | grep -c '"code":"MX"' || true)
if [[ "$mx_exists" == "0" ]]; then
  post "/api/org/teams" '{"code":"MX","name":"Mobile eXperience","is_active":true}' "team MX"
else
  warn "team MX (already exists)"
fi

echo "==> 2) MX/VOC org_group 등록"
post "/api/org/groups" "$(cat "$HERE/org_group.json")" "group MX/VOC"

echo "==> 3) doc_type (voc_report, voc_metrics) 등록"
# doc_type.json 은 배열 — 각 원소 개별 POST
python3 - <<PY > /tmp/_doc_types.$$
import json, sys
with open("$HERE/doc_type.json") as f:
    arr = json.load(f)
for d in arr:
    print(json.dumps(d))
PY
while IFS= read -r line; do
  code=$(echo "$line" | python3 -c 'import json,sys; print(json.load(sys.stdin)["code"])')
  post "/api/doc-types" "$line" "doc_type $code"
done < /tmp/_doc_types.$$
rm -f /tmp/_doc_types.$$

echo "==> 4) agent (market-voc-analyst) 등록"
post "/api/agents" "$(cat "$HERE/agent.json")" "agent market-voc-analyst"

echo "==> 5) sync_source (signalforge) 등록"
python3 - <<PY > /tmp/_sync_src.$$
import json, os
with open("$HERE/sync_source.example.json") as f:
    src = json.load(f)
src["base_url"] = "$SF_URL"
src["api_key"]  = "$SF_KEY"
print(json.dumps(src))
PY
SYNC_BODY=$(cat /tmp/_sync_src.$$)
rm -f /tmp/_sync_src.$$

# 등록 (409 면 기존 id 조회)
# 주의: bash 의 $$ 는 PID. python -c 안에서 사용하려면 double-quoted shell string 으로 감싸야
#       shell expansion 이 일어나 정확한 파일 경로가 python 에 전달된다.
SYNC_RESP_FILE="/tmp/_sync_resp.$$"
RESP=$(curl -sS -o "$SYNC_RESP_FILE" -w "%{http_code}" -X POST "${H_JSON[@]}" "$AIDH_URL/api/sync/sources" -d "$SYNC_BODY" || true)
if [[ "$RESP" == "200" || "$RESP" == "201" ]]; then
  SYNC_ID=$(python3 -c "import json; print(json.load(open('$SYNC_RESP_FILE'))['id'])")
  ok "sync_source signalforge (id=$SYNC_ID)"
elif [[ "$RESP" == "409" ]]; then
  # NOTE: GET /api/sync/sources 의 'name' query param 은 server-side 필터가
  # 아니라 무시된다 (FastAPI extra='ignore'). 전체 목록을 받아 client-side 에서
  # name=='signalforge' 인 행을 찾는다.
  SYNC_ID=$(curl -sS -H "X-API-Key: $AIDH_KEY" "$AIDH_URL/api/sync/sources" \
    | python3 -c "
import json, sys
d = json.load(sys.stdin)
items = d if isinstance(d, list) else d.get('items', [])
match = [r for r in items if r.get('name') == 'signalforge-gs']
if not match:
    sys.stderr.write('signalforge source not found in /api/sync/sources\n')
    sys.exit(1)
print(match[0]['id'])
")
  warn "sync_source signalforge (already exists, id=$SYNC_ID)"
else
  cat "$SYNC_RESP_FILE" >&2; fail "sync_source signalforge (HTTP $RESP)"
fi
rm -f "$SYNC_RESP_FILE"

echo "==> 6) verify (dry-run, 1페이지 매핑 검증)"
# /verify 는 query param max_pages 만 받음 (body 무시 — Pydantic 없음).
VERIFY=$(curl -sS -w "\n%{http_code}" -X POST "${H_JSON[@]}" "$AIDH_URL/api/sync/sources/$SYNC_ID/verify?max_pages=1" || true)
V_CODE=$(echo "$VERIFY" | tail -n1)
V_BODY=$(echo "$VERIFY" | head -n-1)
if [[ "$V_CODE" == "200" ]]; then
  ok "verify (HTTP 200)"
  echo "$V_BODY" | python3 -m json.tool | sed 's/^/    /'
else
  warn "verify HTTP $V_CODE — SignalForge URL/KEY 또는 endpoint 확인 필요"
  echo "$V_BODY" | sed 's/^/    /'
fi

echo
echo "===== 셋업 완료 ====="
echo "sync_source id : $SYNC_ID"
echo
echo "다음 단계:"
echo "  1) SignalForge 측에서 1회 backfill (대량):"
echo "       cd /home/koopark/claude/SignalForge/integrations/aidatahub"
echo "       python aidatahub_sync.py --mode=push-all --config=config.yml"
echo
echo "  2) AX Hub cron 등록 (30분 주기 pull):"
echo "       echo '*/30 * * * * curl -sS -X POST -H \"X-API-Key: $AIDH_KEY\" $AIDH_URL/api/sync/sources/$SYNC_ID/run' | crontab -"
echo
