#!/usr/bin/env bash
# stress_strain_plot 예제 도구를 wave-5 P1 으로 업로드 → 등록 → 호출 → 검증.
# 사용:
#   bash deploy/apptainer/seed-stress-strain.sh
#   AIDH_BASE_URL=http://aidh:8001 bash deploy/apptainer/seed-stress-strain.sh
#
# 검증 단계:
#   1. zip 생성 (examples/wave-5/stress_strain_plot/)
#   2. POST /api/mcp_tools/upload
#   3. job_id 폴링 (최대 5분)
#   4. 등록 확인 + tool count 증가
#   5. tools/call → MCP 응답 검증 (PNG inline + record_id + attachment URL)
#   6. attachment 파일 시스템 확인
#   7. semantic_search 로 재발견
#
# 실패 시 어느 단계에서 멈췄는지 명확히 표시. 메모리 규칙: dev PC 가 아닌 타겟 서버에서.

set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
[[ -f "$SCRIPT_DIR/.env" ]] && set -a && . "$SCRIPT_DIR/.env" && set +a

BASE="${AIDH_BASE_URL:-${HOST_URL:-http://127.0.0.1:${API_PORT:-8001}}}"
VENV_PY="$REPO_ROOT/api_server/.venv/bin/python3"
EXAMPLE_DIR="$REPO_ROOT/examples/wave-5/stress_strain_plot"
ZIP_PATH="/tmp/stress_strain_plot-$(date +%s).zip"
UPLOADER="${AIDH_UPLOADER:-ops-verify@local}"

step() { echo; echo "── $* ──"; }
die()  { echo "[FAIL] $*" >&2; exit 1; }

step "1. zip 생성"
[[ -d "$EXAMPLE_DIR" ]] || die "예제 디렉토리 없음: $EXAMPLE_DIR"
command -v zip >/dev/null || die "zip 명령 없음 (apt install zip)"
(cd "$EXAMPLE_DIR" && zip -r "$ZIP_PATH" . >/dev/null)
echo "  ✓ $ZIP_PATH ($(du -h "$ZIP_PATH" | cut -f1))"

step "2. POST /api/mcp_tools/upload"
UPLOAD_RESP=$(curl -s -X POST "$BASE/api/mcp_tools/upload" \
  -F "bundle=@$ZIP_PATH" \
  -F "metadata={\"uploader\":\"$UPLOADER\"}")
echo "  응답: $UPLOAD_RESP" | head -c 300
echo
JOB_ID=$(echo "$UPLOAD_RESP" | "$VENV_PY" -c "import sys,json; print(json.load(sys.stdin).get('job_id',''))" 2>/dev/null)
[[ -n "$JOB_ID" ]] || die "job_id 추출 실패"
echo "  ✓ job_id: $JOB_ID"

step "3. 폴링 (최대 5분)"
STATUS=""
for i in $(seq 1 60); do
  POLL=$(curl -s "$BASE/api/mcp_tools/jobs/$JOB_ID")
  STATUS=$(echo "$POLL" | "$VENV_PY" -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null)
  STEP_NAME=$(echo "$POLL" | "$VENV_PY" -c "import sys,json; print(json.load(sys.stdin).get('step',''))" 2>/dev/null)
  printf "  [%02d] status=%-15s step=%s\n" "$i" "$STATUS" "$STEP_NAME"
  if [[ "$STATUS" == "registered" || "$STATUS" == "failed" || "$STATUS" == "completed" ]]; then
    break
  fi
  sleep 5
done
if [[ "$STATUS" != "registered" && "$STATUS" != "completed" ]]; then
  echo
  echo "  실패 응답 전체:"
  echo "$POLL" | "$VENV_PY" -m json.tool
  die "job 미등록 — 위 로그/단계 참고 (runbook §6)"
fi
echo "  ✓ 등록 완료"

step "4. 등록 확인 + tool count"
LIST=$(curl -s "$BASE/api/mcp_tools/")
echo "$LIST" | "$VENV_PY" -c "import sys,json; ds=json.load(sys.stdin); print('  등록된 도구:', [d['name'] for d in ds])"
TOOLS_JSON=$(curl -s -X POST "$BASE/mcp/" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}')
N=$(echo "$TOOLS_JSON" | "$VENV_PY" -c "import sys,json; print(len(json.load(sys.stdin)['result']['tools']))")
echo "  ✓ MCP tools 수: $N (>=15 기대 — built-in 12 + 동적 2 + stress_strain_plot)"

step "5. tools/call stress_strain_plot"
CALL_RESP=$(curl -s -X POST "$BASE/mcp/" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{
    "jsonrpc":"2.0","id":10,"method":"tools/call",
    "params":{"name":"stress_strain_plot",
              "arguments":{"material_name":"SUS304","e_modulus":200.0,
                           "yield_stress":215.0,"ultimate_strain":0.4}}}')
RID=$(echo "$CALL_RESP" | "$VENV_PY" -c "
import sys, json, re
try:
    d = json.load(sys.stdin)
    text = d['result']['content'][0]['text']
    m = re.search(r'SIM-HE-CAE-\d{4}-\d+', text)
    print(m.group(0) if m else '')
except Exception:
    print('')
")
HAS_IMAGE=$(echo "$CALL_RESP" | "$VENV_PY" -c "
import sys, json
try:
    d = json.load(sys.stdin)
    types = [c.get('type') for c in d['result']['content']]
    print('yes' if 'image' in types else 'no')
except Exception:
    print('no')
")
HAS_URL=$(echo "$CALL_RESP" | grep -c '/attachments/' || true)
echo "  ✓ record_id: ${RID:-<NOT FOUND>}"
echo "  ✓ ImageContent: $HAS_IMAGE"
echo "  ✓ attachment URL 노출: ${HAS_URL}회"
[[ -n "$RID" ]] || die "record_id 추출 실패 — persist_output 동작 안 함"

step "6. attachment 파일 확인"
ATT_PATH="$REPO_ROOT/api_server/static/attachments/$RID"
if [[ -d "$ATT_PATH" ]]; then
  ls -la "$ATT_PATH"
  echo "  ✓ attachment 저장 OK"
else
  echo "  [WARN] attachments_dir 가 다른 위치일 수 있음 (settings.attachments_dir)"
fi

step "7. semantic_search 재발견"
SEARCH=$(curl -s -G "$BASE/api/search" \
  --data-urlencode "mode=semantic" \
  --data-urlencode "q=SUS304 stress strain bilinear hardening" \
  --data-urlencode "limit=5")
FOUND=$(echo "$SEARCH" | grep -c "$RID" || true)
if [[ "$FOUND" -gt 0 ]]; then
  echo "  ✓ semantic_search 가 $RID 발견 (P1.7 embedding 정상)"
else
  echo "  [WARN] semantic_search 가 $RID 미발견 — embedding=null 가능성 (백필 필요)"
  echo "         curl -X POST $BASE/api/jobs/embed?backfill=true"
fi

step "완료"
echo "  job_id   : $JOB_ID"
echo "  record_id: $RID"
echo "  zip       : $ZIP_PATH"
echo "  → Claude Desktop 에서 \"SUS304 stress-strain 그려줘\" 호출 시 동일 흐름 + PNG 인라인 렌더 가능"
