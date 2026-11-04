#!/usr/bin/env bash
# AI Data Hub — 조각 스냅숏 받기(cae00): 최신 목록을 지난번 병합한 목록과 견줘 바뀐 조각만 받는다(HWAXPortal docs/aidh-parts-sync)
#
#   bash deploy/apptainer/fetch-parts.sh            # 끝 코드 0 받음(계획 $CACHE/plan.json) · 10 바뀐 것 없음 · 4 Drive 에 목록 없음 · 그 밖 실패
#   bash deploy/apptainer/fetch-parts.sh --commit   # 병합(merge-from-drive.sh AIDH_MERGE_PARTS_PLAN=…)이 끝난 뒤 — 이번 목록을 '병합함' 으로 적는다
#
# 받은 조각은 sha256 으로 대조한다. 병합을 적기(--commit) 전까지는 같은 목록을 다시 받으면 같은 계획이 나온다(멱등).
set -euo pipefail
APPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$APPT_DIR/_common.sh"
load_env

REMOTE="${AIDH_PARTS_REMOTE:-}"
[ -n "$REMOTE" ] || { [ -n "${AIDH_DRIVE_REMOTE:-}" ] && REMOTE="${AIDH_DRIVE_REMOTE%/*}/db-parts"; }
CACHE="${AIDH_PARTS_CACHE:-$APPT_DIR/backups/parts-cache}"
MARK="$CACHE/merged-manifest.json"
mkdir -p "$CACHE/files"

if [ "${1:-}" = --commit ]; then
  [ -s "$CACHE/plan.manifest" ] || { echo "[ERROR] 적을 계획이 없다($CACHE/plan.manifest)"; exit 1; }
  m="$(cat "$CACHE/plan.manifest")"
  cp "$CACHE/$m" "$MARK"
  # 병합한 조각은 다시 쓸 일이 없다(다음에 바뀐 조각은 새 이름이다) — 캐시를 비운다.
  find "$CACHE/files" -maxdepth 1 -type f -delete
  find "$CACHE" -maxdepth 1 -name 'manifest-*.json' ! -name "$m" -delete
  rm -f "$CACHE/plan.json" "$CACHE/plan.manifest" "$CACHE/need.txt"
  echo "✓ 병합한 목록 — $m"
  exit 0
fi

[ -n "$REMOTE" ] || { echo "[ERROR] AIDH_PARTS_REMOTE · AIDH_DRIVE_REMOTE 둘 다 비었다"; exit 1; }
command -v rclone >/dev/null || { echo "[ERROR] rclone 없음"; exit 1; }
latest="$(rclone lsf "$REMOTE/" --files-only --include 'manifest-*.json' 2>/dev/null | sort | tail -1 || true)"
[ -n "$latest" ] || { echo "조각 목록 없음($REMOTE) — 전체 덤프 길로"; exit 4; }
rclone copyto "$REMOTE/$latest" "$CACHE/$latest"

rc=0
python3 "$APPT_DIR/aidh_parts.py" plan --manifest "$CACHE/$latest" --mark "$MARK" --out "$CACHE/plan.json" || rc=$?
if [ "$rc" = 10 ]; then
  echo "조각 변화 없음 — $latest(지난번 병합 $( [ -f "$MARK" ] && python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["created"])' "$MARK" || echo '없음'))"
  exit 10
fi
[ "$rc" = 0 ] || { echo "[ERROR] 계획을 짓지 못했다(rc $rc)"; exit 1; }

# 받을 파일 — 계획의 스키마 · 조각 가운데 캐시에 없는 것
python3 - "$CACHE/plan.json" "$CACHE/files" > "$CACHE/need.txt" <<'PY'
import json, sys
from pathlib import Path
plan, files = json.load(open(sys.argv[1])), Path(sys.argv[2])
names = [plan["schema"]["file"]] + [p["file"] for t in plan["tables"] for p in t["parts"]]
for n in dict.fromkeys(names):
    if not (files / n).is_file():
        print(n)
PY
if [ -s "$CACHE/need.txt" ]; then
  t1=$(date +%s)
  rclone copy "$REMOTE/files" "$CACHE/files" --files-from "$CACHE/need.txt" --transfers 4 --checkers 8
  echo "  받음 $(wc -l < "$CACHE/need.txt") 파일 · $(du -sh "$CACHE/files" | cut -f1) · $(( $(date +%s) - t1 ))초"
fi
python3 "$APPT_DIR/aidh_parts.py" verify --plan "$CACHE/plan.json" --cache "$CACHE/files" \
  || { echo "[ERROR] 받은 조각의 sha256 이 목록과 다르다 — 다시 받는다(캐시의 그 파일을 지우고)"; exit 1; }
printf '%s' "$latest" > "$CACHE/plan.manifest"
echo "✓ 조각 계획 — $latest → $CACHE/plan.json"
