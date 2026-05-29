#!/usr/bin/env bash
# AX Hub × MXWhitePaper 1회 셋업 스크립트
#
# Usage:
#   ./setup.sh <AIDH_BASE_URL> <AIDH_API_KEY> <MXWP_BASE_URL> <MXWP_API_KEY>
#
# Example:
#   ./setup.sh http://aidatahub:8001 $AIDH_ADMIN_KEY http://mxwp-api:8000 $MXWP_INTERNAL_KEY
#
# 409 (already exists) 는 정상 — 재실행 안전.

set -u
set -o pipefail

AIDH_BASE_URL="${1:?BASE_URL required (e.g. http://aidatahub:8001)}"
AIDH_API_KEY="${2:?API_KEY required}"
MXWP_BASE_URL="${3:?MXWP_BASE_URL required (e.g. http://mxwp-api:8000)}"
MXWP_API_KEY="${4:?MXWP_API_KEY required}"

HERE="$(cd "$(dirname "$0")" && pwd)"

H_JSON=(-H "Content-Type: application/json")
H_AUTH=(-H "X-API-Key: ${AIDH_API_KEY}")

call() {
  local method="$1"; shift
  local path="$1"; shift
  local body="${1:-}"
  if [ -n "$body" ]; then
    curl -sS -o /tmp/aidh.body -w "%{http_code}" \
      -X "$method" "${H_AUTH[@]}" "${H_JSON[@]}" \
      "${AIDH_BASE_URL}${path}" -d "$body"
  else
    curl -sS -o /tmp/aidh.body -w "%{http_code}" \
      -X "$method" "${H_AUTH[@]}" \
      "${AIDH_BASE_URL}${path}"
  fi
}

step() { printf "\n[%s] %s\n" "$1" "$2"; }

# ---- 1) MX/WP org_group 등록 ----
step 1 "Register org_group MX/WP"
GROUP_BODY="$(cat "${HERE}/org_group.json")"
CODE="$(call POST /api/org/groups "${GROUP_BODY}")"
case "$CODE" in
  201|200) echo "  ok ($CODE)";;
  409)     echo "  already exists (409) — ok";;
  *)       echo "  FAIL ($CODE)"; cat /tmp/aidh.body; echo; exit 1;;
esac

# ---- 2) doc_type whitepaper / feasibility_study 등록 (배열 → 항목별 POST) ----
step 2 "Register doc_types"
DOC_TYPES="$(cat "${HERE}/doc_type.json")"
echo "$DOC_TYPES" | python3 -c "
import sys, json
arr = json.load(sys.stdin)
for d in arr:
    print(json.dumps(d))
" | while read -r dt; do
  CODE_NAME="$(echo "$dt" | python3 -c "import sys,json; print(json.load(sys.stdin)['code'])")"
  CODE="$(call POST /api/doc-types "${dt}")"
  case "$CODE" in
    201|200) echo "  ${CODE_NAME}: ok ($CODE)";;
    409)     echo "  ${CODE_NAME}: already exists (409) — ok";;
    *)       echo "  ${CODE_NAME}: FAIL ($CODE)"; cat /tmp/aidh.body; echo; exit 1;;
  esac
done

# ---- 3) agent mx-whitepaper-analyst 등록 ----
step 3 "Register agent mx-whitepaper-analyst"
AGENT_BODY="$(cat "${HERE}/agent.json")"
CODE="$(call POST /api/agents "${AGENT_BODY}")"
case "$CODE" in
  201|200) echo "  ok ($CODE)";;
  409)     echo "  already exists (409) — ok";;
  *)       echo "  FAIL ($CODE)"; cat /tmp/aidh.body; echo; exit 1;;
esac

# ---- 4) sync_source 등록 ----
step 4 "Register sync_source mxwp"
SYNC_BODY="$(
  python3 -c "
import json, os, sys
with open('${HERE}/sync_source.example.json') as f:
    s = json.load(f)
s['base_url'] = '${MXWP_BASE_URL}'
s['api_key']  = '${MXWP_API_KEY}'
print(json.dumps(s))
"
)"
CODE="$(call POST /api/sync/sources "${SYNC_BODY}")"
case "$CODE" in
  201|200)
    MXWP_ID="$(python3 -c "import json; print(json.load(open('/tmp/aidh.body'))['id'])")"
    echo "  ok ($CODE) — sync_source id=${MXWP_ID}"
    ;;
  409)
    echo "  already exists (409) — looking up id..."
    call GET /api/sync/sources >/dev/null
    MXWP_ID="$(python3 -c "
import json
for s in json.load(open('/tmp/aidh.body')):
    if s.get('name')=='mxwp': print(s['id']); break
")"
    echo "  found existing id=${MXWP_ID}"
    ;;
  *)
    echo "  FAIL ($CODE)"; cat /tmp/aidh.body; echo
    MXWP_ID=""
    ;;
esac

# ---- 5) dry-run verify ----
if [ -n "${MXWP_ID:-}" ]; then
  step 5 "Dry-run verify (1 page mapping check)"
  CODE="$(call POST "/api/sync/sources/${MXWP_ID}/verify" '{}')"
  echo "  verify HTTP ${CODE}"
  python3 -c "
import json
try:
    d = json.load(open('/tmp/aidh.body'))
    print('  fetched   :', d.get('fetched'))
    print('  mappable  :', d.get('mappable'))
    print('  warnings  :', d.get('warnings'))
except Exception as e:
    pass
"
fi

# ---- 결과 + cron 안내 ----
cat <<EOF

================================================================
Setup complete.
  sync_source id : ${MXWP_ID:-<unknown>}
  base_url       : ${MXWP_BASE_URL}

다음 단계 — AX Hub cron 등록 (서버측):

  cat <<CRON | sudo tee /etc/cron.d/aidh-mxwp
  */30 * * * *  aidh  curl -s -X POST \\
    -H "X-API-Key: \$AIDH_ADMIN_KEY" \\
    ${AIDH_BASE_URL}/api/sync/sources/${MXWP_ID:-MXWP_ID}/run \\
    >> /var/log/aidh/mxwp-sync.log 2>&1
  CRON

또는 즉시 1회 실행:
  curl -X POST -H "X-API-Key: \$AIDH_ADMIN_KEY" \\
    ${AIDH_BASE_URL}/api/sync/sources/${MXWP_ID:-MXWP_ID}/run
================================================================
EOF
