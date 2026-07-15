#!/usr/bin/env bash
# AI Data Hub — records/agents 를 JSONL 로 export → Google Drive (머지 동기화 소스).
#
# backup-to-drive.sh(전체 DB 덤프, restore 는 DROP+CREATE 파괴적)와 다르다:
#   여기서는 '데이터만' JSONL 로 내보내고, 운영서버가 import-from-drive.sh 로
#   upsert(오버랩)한다 — 운영서버 자체 데이터는 보존, 이 데이터만 최신으로 겹침.
#
# 내보내는 것:
#   - records : deleted_at IS NULL 인 레코드 (signature_embedding 제외 → import 시 재임베딩)
#   - agents  : 전체 agent 정의 (created_at/updated_at 제외)
#   - manifest: 건수 + sha256 + 시각 (import 측 무결성 검증용)
#
# 사용:
#   bash deploy/apptainer/export-to-drive.sh
#   bash deploy/apptainer/export-to-drive.sh --local-only   # Drive 업로드 없이 로컬 파일만
#   AIDH_SYNC_REMOTE=Remote:path bash deploy/apptainer/export-to-drive.sh
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
load_env
export_proxy

LOCAL_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --local-only) LOCAL_ONLY=1 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "unknown arg: $arg" >&2; exit 2 ;;
  esac
done

# 기본 remote: db-dumps 옆 sync 폴더 (AIDH_DRIVE_REMOTE=…/db-dumps → …/sync)
REMOTE="${AIDH_SYNC_REMOTE:-${AIDH_DRIVE_REMOTE%/*}/sync}"
RETAIN="${AIDH_SYNC_RETAIN:-5}"

if ! instance_running "$INST_POSTGRES"; then
  echo "[ERROR] $INST_POSTGRES 인스턴스 없음 — 먼저 start_postgres.sh" >&2; exit 1
fi
# 좀비 방지 — 실제 쿼리로 살아있는지 확인 (backup-db.sh 와 동일 규약).
_PSQL() { apptainer exec "instance://$INST_POSTGRES" psql -h 127.0.0.1 -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" "$@" 2>/dev/null; }
if ! _PSQL -tAc 'select 1' >/dev/null; then
  echo "[ERROR] $INST_POSTGRES 가 쿼리에 응답 안 함 (좀비/기동중) — export 중단." >&2
  echo "        복구: bash deploy/apptainer/restart.sh --pg" >&2; exit 1
fi

OUT_DIR="$APPT_DIR/sync-export"
mkdir -p "$OUT_DIR"
TS="$(date -u +%Y%m%d-%H%M%SZ)"
REC="$OUT_DIR/aidh-sync-${TS}-records.jsonl.gz"
AGT="$OUT_DIR/aidh-sync-${TS}-agents.jsonl.gz"
MAN="$OUT_DIR/aidh-sync-${TS}-manifest.json"

echo "→ export records (deleted_at IS NULL, signature_embedding 제외)"
_PSQL -tAc "SELECT to_jsonb(r) - 'signature_embedding' FROM records r WHERE deleted_at IS NULL" | gzip > "$REC"
echo "→ export agents"
_PSQL -tAc "SELECT to_jsonb(a) - 'created_at' - 'updated_at' FROM agents a" | gzip > "$AGT"

# ── 무결성 검증 (fail-closed) — JSONL 라인수 == DB 행수, gzip 정상 ──────────
REC_ROWS=$(_PSQL -tAc "SELECT count(*) FROM records WHERE deleted_at IS NULL" | tr -d '[:space:]')
AGT_ROWS=$(_PSQL -tAc "SELECT count(*) FROM agents" | tr -d '[:space:]')
REC_LINES=$(zcat "$REC" 2>/dev/null | grep -c . || true)
AGT_LINES=$(zcat "$AGT" 2>/dev/null | grep -c . || true)
if ! gzip -t "$REC" 2>/dev/null || ! gzip -t "$AGT" 2>/dev/null; then
  echo "[ERROR] export gzip 손상 — 폐기." >&2; rm -f "$REC" "$AGT"; exit 1
fi
if [[ "$REC_ROWS" != "$REC_LINES" || "$AGT_ROWS" != "$AGT_LINES" ]]; then
  echo "[ERROR] export 행수 불일치 — records $REC_ROWS/$REC_LINES, agents $AGT_ROWS/$AGT_LINES. 폐기." >&2
  rm -f "$REC" "$AGT"; exit 1
fi

REC_SHA=$(sha256sum "$REC" | awk '{print $1}')
AGT_SHA=$(sha256sum "$AGT" | awk '{print $1}')
cat > "$MAN" <<EOF
{
  "kind": "aidh-sync-export",
  "ts": "$TS",
  "host": "$(hostname)",
  "records": { "file": "$(basename "$REC")", "count": $REC_ROWS, "sha256": "$REC_SHA" },
  "agents":  { "file": "$(basename "$AGT")", "count": $AGT_ROWS, "sha256": "$AGT_SHA" }
}
EOF

echo
echo "✓ export 완료 (records=$REC_ROWS, agents=$AGT_ROWS)"
echo "  $REC"
echo "  $AGT"
echo "  $MAN"

if [[ $LOCAL_ONLY -eq 1 ]]; then
  echo "→ --local-only: Drive 업로드 생략"
  exit 0
fi
command -v rclone >/dev/null || { echo "[ERROR] rclone 미설치 — 로컬 파일만 생성됨."; exit 1; }

echo "→ Drive 업로드: $REMOTE"
rclone copy "$REC" "$REMOTE/"
rclone copy "$AGT" "$REMOTE/"
rclone copy "$MAN" "$REMOTE/"

# 보존정책 — manifest 기준 최신 RETAIN 세트만 유지 (딸린 records/agents 도 함께 정리).
if [[ "$RETAIN" -gt 0 ]]; then
  mapfile -t _mans < <(rclone lsf --files-only "$REMOTE/" 2>/dev/null | grep -E '^aidh-sync-.*-manifest\.json$' | sort)
  if (( ${#_mans[@]} > RETAIN )); then
    _del=$(( ${#_mans[@]} - RETAIN ))
    echo "→ 보존정책: 최신 $RETAIN 세트 유지, 오래된 $_del 세트 삭제"
    for ((i=0; i<_del; i++)); do
      stem="${_mans[$i]%-manifest.json}"   # aidh-sync-<TS>
      for suf in records.jsonl.gz agents.jsonl.gz manifest.json; do
        rclone deletefile "$REMOTE/${stem}-${suf}" 2>/dev/null && echo "    - ${stem}-${suf}" || true
      done
    done
  fi
fi
echo "✓ Drive 동기화 소스 갱신 완료 — 운영서버에서 import-from-drive.sh 로 받으세요."
