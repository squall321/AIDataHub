#!/usr/bin/env bash
# AI Data Hub — curl smoke test
# 실행: bash curl_smoke.sh

# === HARDCODED API URL =====================================================
BASE="http://110.15.177.125:8000"
API_KEY=""  # 인증 활성 시 채움
# ===========================================================================

set -u

if [ -n "$API_KEY" ]; then
  AUTH=(-H "X-API-Key: $API_KEY")
else
  AUTH=()
fi

pass() { echo "[OK]   $1"; }
fail() { echo "[FAIL] $1: $2"; exit 1; }

# 1) Health
RESP=$(curl -s -w "\n%{http_code}" "${AUTH[@]}" "$BASE/api/system/health")
HTTP=$(echo "$RESP" | tail -1)
BODY=$(echo "$RESP" | sed '$d')
if [ "$HTTP" = "200" ]; then
  pass "health (200)"
  echo "  $BODY" | head -c 200; echo
else
  fail "health" "HTTP $HTTP — 서버 안 떠있거나 URL 잘못됨"
fi

# 2) Discover
RESP=$(curl -s -w "\n%{http_code}" "${AUTH[@]}" "$BASE/api/discover")
HTTP=$(echo "$RESP" | tail -1)
[ "$HTTP" = "200" ] && pass "discover (200)" || fail "discover" "HTTP $HTTP"

# 3) Search (semantic)
RESP=$(curl -s -w "\n%{http_code}" "${AUTH[@]}" \
  "$BASE/api/search?mode=semantic&q=KooRemapper&limit=3")
HTTP=$(echo "$RESP" | tail -1)
[ "$HTTP" = "200" ] && pass "search semantic (200)" || fail "search semantic" "HTTP $HTTP"

# 4) Search (fts)
RESP=$(curl -s -w "\n%{http_code}" "${AUTH[@]}" \
  "$BASE/api/search?mode=fts&q=stress&limit=3")
HTTP=$(echo "$RESP" | tail -1)
[ "$HTTP" = "200" ] && pass "search fts (200)" || fail "search fts" "HTTP $HTTP"

# 5) Search (tag)
RESP=$(curl -s -w "\n%{http_code}" "${AUTH[@]}" \
  "$BASE/api/search?mode=tag&tags=IGA&limit=3")
HTTP=$(echo "$RESP" | tail -1)
[ "$HTTP" = "200" ] && pass "search tag (200)" || fail "search tag" "HTTP $HTTP"

# 6) Records list
RESP=$(curl -s -w "\n%{http_code}" "${AUTH[@]}" "$BASE/api/records?limit=3")
HTTP=$(echo "$RESP" | tail -1)
[ "$HTTP" = "200" ] && pass "records list (200)" || fail "records" "HTTP $HTTP"

# 7) Auto groups
RESP=$(curl -s -w "\n%{http_code}" "${AUTH[@]}" \
  -X POST "$BASE/api/groups/auto" \
  -H "Content-Type: application/json" \
  -d '{"q":"KooRemapper","n_groups":2,"top_k":20}')
HTTP=$(echo "$RESP" | tail -1)
[ "$HTTP" = "200" ] && pass "groups/auto (200)" || fail "groups" "HTTP $HTTP"

# 8) Taxonomy
RESP=$(curl -s -w "\n%{http_code}" "${AUTH[@]}" "$BASE/api/taxonomy/tags?limit=10")
HTTP=$(echo "$RESP" | tail -1)
[ "$HTTP" = "200" ] && pass "taxonomy/tags (200)" || fail "taxonomy" "HTTP $HTTP"

echo "---"
echo "ALL SMOKE TESTS PASSED — direct connection to $BASE OK"
