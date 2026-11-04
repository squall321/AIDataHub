#!/usr/bin/env bash
# AI Data Hub — 조각 스냅숏 내기(dev): 표를 조각으로 내고 Drive 에 새 조각 · 목록만 올린다 — cae00 은 바뀐 조각만 받는다(HWAXPortal docs/aidh-parts-sync)
#
#   bash deploy/apptainer/parts-to-drive.sh             # 내기 → 새 조각 · 목록 올리기 → 목록 7판만 두고 안 가리키는 조각 지우기(Drive · 로컬)
#   AIDH_PARTS_UPLOAD=0 bash … parts-to-drive.sh       # 내기만(로컬 $AIDH_PARTS_DIR)
#
# 전체 덤프(backup-to-drive.sh)는 그대로다 — 재해 복구와 조각이 없을 때 cae00 의 전체 병합 길이다. 조각 파일은 같은 데이터면 같은
# 바이트(aidh_parts.py — ORDER BY 키 · gzip 시각 0)라 이름(내용 해시)이 같아 다시 올리지 않는다. 조각을 다 올린 뒤에 목록을 올린다 —
# 목록이 없는 조각을 가리키는 때가 없게.
set -euo pipefail
APPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$APPT_DIR/_common.sh"
load_env

PORT="${POSTGRES_PORT:?}"; USER="${POSTGRES_USER:?}"; DB="${POSTGRES_DB:?}"; INST="${INST_POSTGRES:?}"
OUT="${AIDH_PARTS_DIR:-$APPT_DIR/backups/parts}"
REMOTE="${AIDH_PARTS_REMOTE:-}"
[ -n "$REMOTE" ] || { [ -n "${AIDH_DRIVE_REMOTE:-}" ] && REMOTE="${AIDH_DRIVE_REMOTE%/*}/db-parts"; }
KEEP="${AIDH_PARTS_KEEP:-7}"
N="${AIDH_PARTS_N:-64}"
# python 은 bash 함수(_common.sh 의 apptainer)를 못 부른다 — 핀으로 고른 실행 파일을 넘긴다.
APPT_BIN="${_AIDH_APPT:-$(command -v apptainer || echo apptainer)}"
_json() { python3 -c 'import json, sys; print(json.dumps(sys.argv[1:]))' "$@"; }
export AIDH_PSQL_CMD AIDH_PGDUMP_CMD
AIDH_PSQL_CMD="$(_json "$APPT_BIN" exec "instance://$INST" psql -h 127.0.0.1 -p "$PORT" -U "$USER" -d "$DB")"
AIDH_PGDUMP_CMD="$(_json "$APPT_BIN" exec "instance://$INST" pg_dump -h 127.0.0.1 -p "$PORT" -U "$USER" -d "$DB")"

t0=$(date +%s)
echo "→ 조각 내기($N 조각) → $OUT"
MANIFEST="$(python3 "$APPT_DIR/aidh_parts.py" export --out "$OUT" --n "$N")"
echo "  목록 $(basename "$MANIFEST") · $(find "$OUT/files" -maxdepth 1 -type f | wc -l) 파일 · $(du -sh "$OUT/files" | cut -f1) · $(( $(date +%s) - t0 ))초"

if [ "${AIDH_PARTS_UPLOAD:-1}" = 1 ]; then
  [ -n "$REMOTE" ] || { echo "[ERROR] AIDH_PARTS_REMOTE · AIDH_DRIVE_REMOTE 둘 다 비었다 — 올릴 곳을 모른다"; exit 1; }
  command -v rclone >/dev/null || { echo "[ERROR] rclone 없음"; exit 1; }
  t1=$(date +%s)
  # 새 이름만 올린다(같은 이름 = 같은 내용). 조각 → 목록 차례.
  rclone copy "$OUT/files" "$REMOTE/files" --ignore-existing --transfers 4 --checkers 8
  rclone copyto "$MANIFEST" "$REMOTE/$(basename "$MANIFEST")"
  echo "  올림 → $REMOTE · $(( $(date +%s) - t1 ))초"
  # Drive 정리 — 목록은 최근 $KEEP 판만, 남은 목록이 가리키지 않는 조각은 지운다(가장 새 목록의 조각은 늘 남는다).
  tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
  mapfile -t mans < <(rclone lsf "$REMOTE/" --files-only --include 'manifest-*.json' | sort)
  if [ "${#mans[@]}" -gt "$KEEP" ]; then
    for m in "${mans[@]:0:${#mans[@]}-$KEEP}"; do rclone deletefile "$REMOTE/$m"; done
    mans=("${mans[@]: -$KEEP}")
  fi
  for m in "${mans[@]}"; do rclone copyto "$REMOTE/$m" "$tmp/$m"; done
  python3 "$APPT_DIR/aidh_parts.py" referenced "$tmp"/manifest-*.json | sort > "$tmp/live"
  rclone lsf "$REMOTE/files/" --files-only | sort > "$tmp/remote"
  comm -23 "$tmp/remote" "$tmp/live" > "$tmp/gone"
  if [ -s "$tmp/gone" ]; then
    rclone delete "$REMOTE/files/" --files-from "$tmp/gone"
    echo "  Drive 에서 안 가리키는 조각 $(wc -l < "$tmp/gone")개를 지웠다"
  fi
fi
gone_local="$(python3 "$APPT_DIR/aidh_parts.py" gc --dir "$OUT" --keep-manifests "$KEEP" | wc -l)"
[ "$gone_local" = 0 ] || echo "  로컬에서 안 가리키는 조각 ${gone_local}개를 지웠다"
echo "✓ 조각 스냅숏 $(basename "$MANIFEST")"
