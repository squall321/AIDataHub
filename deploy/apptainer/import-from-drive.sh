#!/usr/bin/env bash
# AI Data Hub — Drive 최신 sync export 를 받아 운영서버에 upsert import (오버랩, 파괴 없음).
#
# sync-from-drive.sh(전체 DB restore, DROP+CREATE 파괴적)와 반대다:
#   여기서는 export-to-drive.sh 가 올린 records/agents JSONL 을 받아
#   POST /api/records/import (멱등 upsert) 로 '겹쳐' 넣는다 — 운영서버 자체 데이터 보존.
#
# 사용 (운영서버에서):
#   SYNC_TARGET_KEY=<x-api-key> bash deploy/apptainer/import-from-drive.sh --dry-run  # 계획만
#   SYNC_TARGET_KEY=<x-api-key> bash deploy/apptainer/import-from-drive.sh            # 실제 upsert
#   옵션: --url http://host:port (기본 127.0.0.1:API_PORT) · --records-only · --agents-only
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
load_env
export_proxy

REMOTE="${AIDH_SYNC_REMOTE:-${AIDH_DRIVE_REMOTE%/*}/sync}"
URL="${SYNC_TARGET_URL:-http://127.0.0.1:${API_PORT:-8001}}"
KEY="${SYNC_TARGET_KEY:-${BOOTSTRAP_API_KEY:-}}"
DRY=0; WHAT="both"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY=1; shift ;;
    --url) URL="$2"; shift 2 ;;
    --records-only) WHAT="records"; shift ;;
    --agents-only) WHAT="agents"; shift ;;
    -h|--help) sed -n '2,17p' "$0"; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

command -v rclone >/dev/null || { echo "[ERROR] rclone 미설치 — setup-drive-sync.sh" >&2; exit 1; }
PY="$(command -v python3 || true)"; [[ -n "$PY" ]] || { echo "[ERROR] python3 필요" >&2; exit 1; }
IMPORTER="$APPT_DIR/sync_import.py"
[[ -f "$IMPORTER" ]] || { echo "[ERROR] $IMPORTER 없음" >&2; exit 1; }

echo "================================================================"
echo " AI Data Hub — import-from-drive  $(date -u +%FT%TZ)"
echo "   remote : $REMOTE"
echo "   target : $URL   (dry-run=$DRY, what=$WHAT)"
echo "================================================================"

# ── 1) Drive 최신 manifest 찾기 (파일명 UTC TS 정렬) ────────────────
LATEST_MAN="$(rclone lsf --files-only "$REMOTE/" 2>/dev/null | grep -E '^aidh-sync-.*-manifest\.json$' | sort | tail -n1 || true)"
[[ -n "$LATEST_MAN" ]] || { echo "[ERROR] Drive 에 aidh-sync-*-manifest.json 없음 — 먼저 소스에서 export-to-drive.sh" >&2; exit 1; }
STEM="${LATEST_MAN%-manifest.json}"   # aidh-sync-<TS>
echo "[1/4] 최신 export: $STEM"

DL="$APPT_DIR/sync-import"
mkdir -p "$DL"
echo "[2/4] 다운로드"
for suf in manifest.json records.jsonl.gz agents.jsonl.gz; do
  rclone copy "$REMOTE/${STEM}-${suf}" "$DL/" 2>/dev/null || { echo "[ERROR] 다운로드 실패: ${STEM}-${suf}" >&2; exit 1; }
done
MAN="$DL/${STEM}-manifest.json"
REC="$DL/${STEM}-records.jsonl.gz"
AGT="$DL/${STEM}-agents.jsonl.gz"

# ── 3) sha256 검증 (manifest 값 vs 실제) ────────────────────────────
echo "[3/4] sha256 검증"
"$PY" - "$MAN" "$REC" "$AGT" <<'PY' || { echo "[ERROR] sha256 불일치 — abort (손상 의심)" >&2; exit 1; }
import hashlib, json, sys
man, rec, agt = sys.argv[1], sys.argv[2], sys.argv[3]
m = json.load(open(man, encoding="utf-8"))
def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()
ok = True
for key, path in (("records", rec), ("agents", agt)):
    want = m.get(key, {}).get("sha256")
    got = sha(path)
    mark = "OK" if want == got else "MISMATCH"
    print(f"    {key}: {mark} (count={m.get(key,{}).get('count')})")
    ok = ok and (want == got)
sys.exit(0 if ok else 1)
PY

# ── 4) upsert import ────────────────────────────────────────────────
echo "[4/4] upsert import ($([ $DRY -eq 1 ] && echo dry-run || echo APPLY))"
ARGS=(--url "$URL" --key "$KEY")
[[ $DRY -eq 1 ]] && ARGS+=(--dry-run)
[[ "$WHAT" != "agents"  ]] && ARGS+=(--records "$REC")
[[ "$WHAT" != "records" ]] && ARGS+=(--agents "$AGT")
"$PY" "$IMPORTER" "${ARGS[@]}"

echo
echo "✓ import-from-drive 완료 ($STEM)"
[[ $DRY -eq 1 ]] && echo "  (dry-run 이었습니다 — 실제 적용은 --dry-run 없이 재실행)"
