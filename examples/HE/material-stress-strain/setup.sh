#!/usr/bin/env bash
# setup.sh — Stress-Strain Curve 예제 데이터셋 자동 설치
#
# 사용법:
#   bash setup.sh <BASE_URL> <API_KEY>
#
# 예:
#   bash setup.sh http://localhost:8001 dev_key_123
#
# 단계:
#   1. doc_type 2개 등록 (material_test_data + material_test_report)
#   2. agent 1개 등록 (material-stress-strain-analyst)
#   3. records 5건 일괄 import (auto_seq → id 자동 채번)
#   4. 각 record 에 raw csv 를 attachment 로 결합 (bundle 재업로드)
#
# 모든 단계는 idempotent (재실행 안전) — 이미 존재하는 doc_type/agent 는 skip.
#
# 주의: AX Hub 는 단건 "POST /api/records/{id}/attachments" multipart 엔드포인트가
# 없다. attachment 적재는 "POST /api/ingest/bundle" (zip = record.json + csv) 를
# 통해 이루어진다. 이 스크립트는 step 3 에서 채번된 id 를 받아 step 4 에서
# bundle 형태로 재업로드해 UPSERT + attachment 적재를 수행한다.

set -euo pipefail

# ---------------------------------------------------------------------------
# 인자 파싱
# ---------------------------------------------------------------------------
BASE_URL="${1:-}"
# 보안: API_KEY 를 positional arg 로 받으면 ps aux 에 노출됨.
# 환경변수 AIDH_API_KEY 우선 (권장). positional arg 는 fallback.
API_KEY="${AIDH_API_KEY:-${2:-}}"

if [[ -z "$BASE_URL" || -z "$API_KEY" ]]; then
  echo "usage:" >&2
  echo "  AIDH_API_KEY=xxx bash setup.sh <BASE_URL>           # 권장 — ps aux 노출 X" >&2
  echo "  bash setup.sh <BASE_URL> <API_KEY>                  # 호환 — 토큰 노출 가능" >&2
  echo "  e.g. AIDH_API_KEY=dev123 bash setup.sh http://localhost:8001" >&2
  exit 2
fi

# 종료 슬래시 제거
BASE_URL="${BASE_URL%/}"

# 스크립트가 있는 디렉터리 = 자원 루트
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

H_KEY=(-H "X-API-Key: ${API_KEY}")
H_JSON=(-H "Content-Type: application/json")

# 임시 작업 디렉터리
WORK_DIR="$(mktemp -d -t aidh-stress-strain.XXXXXX)"
trap 'rm -rf "$WORK_DIR"' EXIT

# 필수 도구 확인
for cmd in curl jq zip python3; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "error: required command not found: $cmd" >&2
    exit 1
  fi
done

# ---------------------------------------------------------------------------
# helper — http 상태 코드 + body 분리 호출
# ---------------------------------------------------------------------------
http_post_json() {
  local url="$1"
  local body_file="$2"
  local out_body="$WORK_DIR/resp.body"
  local code
  code=$(curl -sS -o "$out_body" -w "%{http_code}" \
    "${H_KEY[@]}" "${H_JSON[@]}" \
    -X POST --data-binary "@${body_file}" "$url" || echo "000")
  echo "$code"
  cat "$out_body"
}

# ---------------------------------------------------------------------------
# 1. doc_type 등록 (idempotent: 409 면 skip)
# ---------------------------------------------------------------------------
echo "[1/4] doc_type 등록..."
DOC_TYPE_FILE="${SCRIPT_DIR}/doc_type.json"
if [[ ! -f "$DOC_TYPE_FILE" ]]; then
  echo "  error: $DOC_TYPE_FILE not found" >&2
  exit 1
fi

# 배열을 element 별로 분해해 POST
jq -c '.[]' "$DOC_TYPE_FILE" | while IFS= read -r dt_json; do
  code=$(echo "$dt_json" | jq -r '.code')
  echo "$dt_json" > "$WORK_DIR/dt.json"
  resp_body="$WORK_DIR/dt.resp"
  http_code=$(curl -sS -o "$resp_body" -w "%{http_code}" \
    "${H_KEY[@]}" "${H_JSON[@]}" \
    -X POST --data-binary "@${WORK_DIR}/dt.json" \
    "${BASE_URL}/api/doc-types" || echo "000")
  case "$http_code" in
    201) echo "  - created: $code" ;;
    409) echo "  - skip (exists): $code" ;;
    *)
      echo "  ! doc_type '$code' POST failed: HTTP $http_code" >&2
      cat "$resp_body" >&2 ; echo >&2
      exit 1
      ;;
  esac
done

# ---------------------------------------------------------------------------
# 2. agent 등록 (idempotent: 409 면 skip)
# ---------------------------------------------------------------------------
echo "[2/4] agent 등록..."
AGENT_FILE="${SCRIPT_DIR}/agent.json"
if [[ ! -f "$AGENT_FILE" ]]; then
  echo "  error: $AGENT_FILE not found" >&2
  exit 1
fi
AGENT_TYPE=$(jq -r '.agent_type' "$AGENT_FILE")
resp_body="$WORK_DIR/agent.resp"
http_code=$(curl -sS -o "$resp_body" -w "%{http_code}" \
  "${H_KEY[@]}" "${H_JSON[@]}" \
  -X POST --data-binary "@${AGENT_FILE}" \
  "${BASE_URL}/api/agents" || echo "000")
case "$http_code" in
  201) echo "  - created: $AGENT_TYPE" ;;
  409) echo "  - skip (exists): $AGENT_TYPE" ;;
  *)
    echo "  ! agent POST failed: HTTP $http_code" >&2
    cat "$resp_body" >&2 ; echo >&2
    exit 1
    ;;
esac

# ---------------------------------------------------------------------------
# 3. records 일괄 import (auto_seq=true)
# ---------------------------------------------------------------------------
echo "[3/4] records import (auto_seq=true)..."
RECORDS_FILE="${SCRIPT_DIR}/records.json"
if [[ ! -f "$RECORDS_FILE" ]]; then
  echo "  error: $RECORDS_FILE not found" >&2
  exit 1
fi

import_resp="$WORK_DIR/import.resp"
http_code=$(curl -sS -o "$import_resp" -w "%{http_code}" \
  "${H_KEY[@]}" "${H_JSON[@]}" \
  -X POST --data-binary "@${RECORDS_FILE}" \
  "${BASE_URL}/api/records/import?auto_seq=true" || echo "000")
if [[ "$http_code" != "200" ]]; then
  echo "  ! import failed: HTTP $http_code" >&2
  cat "$import_resp" >&2 ; echo >&2
  exit 1
fi

# 응답 파싱: results[].id (순서 = records.json 순서)
mapfile -t RECORD_IDS < <(jq -r '.results[].id' "$import_resp")
OK=$(jq -r '.ok' "$import_resp")
FAILED=$(jq -r '.failed' "$import_resp")
echo "  - imported: ok=${OK} failed=${FAILED}"
if [[ "$FAILED" != "0" ]]; then
  echo "  ! some records failed:" >&2
  jq '.results[] | select(.error)' "$import_resp" >&2
  exit 1
fi
if [[ "${#RECORD_IDS[@]}" -ne 5 ]]; then
  echo "  ! expected 5 record ids, got ${#RECORD_IDS[@]}" >&2
  exit 1
fi

for rid in "${RECORD_IDS[@]}"; do
  echo "    · $rid"
done

# ---------------------------------------------------------------------------
# 4. attachment 적재 — bundle 로 UPSERT (record + csv)
# ---------------------------------------------------------------------------
echo "[4/4] attachment (csv) 적재 — bundle UPSERT..."

# records.json 순서와 일치하는 csv 파일명 배열
CSV_FILES=(
  "AISI-1018.csv"
  "AA6061-T6.csv"
  "AA7075-T6.csv"
  "TPU-shore-A85.csv"
  "316L.csv"
)

CAPTIONS=(
  "AISI 1018 carbon steel — raw stress-strain points (15 pts)"
  "AA6061-T6 aluminum — raw stress-strain points (14 pts)"
  "AA7075-T6 aluminum — raw stress-strain points (14 pts)"
  "TPU Shore A85 — raw stress-strain points (15 pts, hyperelastic)"
  "316L stainless steel — raw stress-strain points (15 pts)"
)

# python 헬퍼: records.json 의 i 번째 record dict 를 stdout 에 JSON 으로 출력
record_at_index() {
  local idx="$1"
  python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(json.dumps(d[int(sys.argv[2])]))" \
    "$RECORDS_FILE" "$idx"
}

for i in "${!RECORD_IDS[@]}"; do
  rid="${RECORD_IDS[$i]}"
  csv="${CSV_FILES[$i]}"
  caption="${CAPTIONS[$i]}"
  csv_path="${SCRIPT_DIR}/data/${csv}"

  if [[ ! -f "$csv_path" ]]; then
    echo "  ! csv not found: $csv_path" >&2
    exit 1
  fi

  bundle_dir="${WORK_DIR}/bundle_${i}"
  mkdir -p "$bundle_dir"

  # bundle JSON 작성: 원본 record + 채번된 id + attachments[] 한 줄 추가
  python3 - "$RECORDS_FILE" "$i" "$rid" "$csv" "$caption" > "${bundle_dir}/${rid}.json" <<'PY'
import json, sys
records_file, idx, rid, csv_name, caption = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4], sys.argv[5]
rec = json.load(open(records_file))[idx]
rec["id"] = rid
content = rec.setdefault("content", {})
atts = content.setdefault("attachments", [])
# 기존 동일 file_name 제거 (재실행 idempotency)
atts = [a for a in atts if a.get("file_name") != csv_name]
atts.append({
    "id": f"{rid}-A001",
    "record_id": rid,
    "number": 1,
    "kind": "spreadsheet",
    "caption": caption,
    "file_name": csv_name,
    "file_path": csv_name,
    "mime_type": "text/csv",
})
content["attachments"] = atts
print(json.dumps(rec, ensure_ascii=False, indent=2))
PY

  # csv 사본
  cp "$csv_path" "${bundle_dir}/${csv}"

  # zip 생성 — (B) 평탄화 컨벤션
  zip_path="${WORK_DIR}/bundle_${i}.zip"
  (cd "$bundle_dir" && zip -q "$zip_path" "${rid}.json" "${csv}")

  # 업로드
  bundle_resp="${WORK_DIR}/bundle_${i}.resp"
  http_code=$(curl -sS -o "$bundle_resp" -w "%{http_code}" \
    "${H_KEY[@]}" \
    -F "file=@${zip_path};type=application/zip" \
    -X POST \
    "${BASE_URL}/api/ingest/bundle" || echo "000")
  if [[ "$http_code" != "201" ]]; then
    echo "  ! bundle upload failed: $rid (HTTP $http_code)" >&2
    cat "$bundle_resp" >&2 ; echo >&2
    exit 1
  fi
  echo "  · ${rid} + ${csv}"
done

# ---------------------------------------------------------------------------
# 완료 — 검증 명령 안내
# ---------------------------------------------------------------------------
echo
echo "==============================================="
echo "  완료. 등록된 record id:"
for rid in "${RECORD_IDS[@]}"; do
  echo "    $rid"
done
echo
echo "  검증 예시:"
echo "    curl -H 'X-API-Key: ${API_KEY}' '${BASE_URL}/api/agents/${AGENT_TYPE}'"
echo "    curl -H 'X-API-Key: ${API_KEY}' '${BASE_URL}/api/search?q=AISI+1018+yield'"
echo "    curl -H 'X-API-Key: ${API_KEY}' '${BASE_URL}/api/records/${RECORD_IDS[0]}/attachments'"
echo "    curl -H 'X-API-Key: ${API_KEY}' '${BASE_URL}/api/doc-types/material_test_data'"
echo "==============================================="
