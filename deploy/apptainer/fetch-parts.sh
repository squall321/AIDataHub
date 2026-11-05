#!/usr/bin/env bash
# AI Data Hub — 조각 스냅숏 받기(cae00): 최신 목록을 지난번 병합한 목록과 견줘 바뀐 조각만 받는다(HWAXPortal docs/aidh-parts-sync)
#
#   bash deploy/apptainer/fetch-parts.sh            # 끝 코드 0 받음(계획 $CACHE/plan.json) · 10 바뀐 것 없음 · 4 Drive 에 목록 없음
#                                                   # · 5 목록이 전체 덤프보다 하루 넘게 낡음(dev 의 하루 한 번 내기가 멈췄다) · 그 밖 실패
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
# rclone 끝 코드 3 은 '자리 없음'(dev 가 아직 한 번도 안 냈다) — 목록 없음과 같다. 그 밖의 실패(네트워크 · 인증)는 받기 실패다 — 4 로 읽으면
# update-all 이 '목록이 없다' 고 말해 원인이 가려진다.
lrc=0; listing="$(rclone lsf "$REMOTE/" --files-only --include 'manifest-*.json' 2>/dev/null)" || lrc=$?
[ "$lrc" = 0 ] || [ "$lrc" = 3 ] || { echo "[ERROR] Drive 목록을 못 읽었다(rclone rc $lrc) — $REMOTE"; exit 1; }
latest="$(printf '%s\n' "$listing" | grep -E '^manifest-.*\.json$' | sort | tail -1 || true)"
[ -n "$latest" ] || { echo "조각 목록 없음($REMOTE) — 전체 덤프 길로"; exit 4; }

# 낡음 검사 — dev 의 하루 한 번 내기가 멈추면 목록이 그대로라 '바뀐 것 없음' 으로 끝나며 전체 덤프에 든 새 데이터를 받지 않는다. 목록이 가장 새
# 전체 덤프보다 AIDH_PARTS_STALE_HOURS(26)시간 넘게 오래면 하루치 내기를 빠뜨린 것이다 — 5 로 끝내 부르는 쪽이 전체 덤프 길로 가게 한다.
# 평소엔 내기가 덤프 1시간 남짓 뒤라, 오늘 내기 전에도 덤프와 어제 목록의 차이는 하루가 안 된다. 덤프 자리를 모르거나 못 읽으면 건너뛴다.
STALE_H="${AIDH_PARTS_STALE_HOURS:-26}"
if [ -n "${AIDH_DRIVE_REMOTE:-}" ]; then
  drc=0; dumps="$(rclone lsf "$AIDH_DRIVE_REMOTE/" --files-only --include 'aidh-db-*.sql.gz' 2>/dev/null)" || drc=$?
  newest_dump="$(printf '%s\n' "$dumps" | grep -E '^aidh-db-[0-9]{8}-[0-9]{6}Z\.sql\.gz$' | sort | tail -1 || true)"
  if [ "$drc" != 0 ] || [ -z "$newest_dump" ]; then
    echo "  · 전체 덤프 목록을 못 읽어 낡음 검사를 건너뛴다($AIDH_DRIVE_REMOTE)"
  else
    lag_h="$(python3 -c '
import re, sys, datetime as dt
t = lambda n: dt.datetime.strptime("".join(re.search(r"(\d{8})-(\d{6})Z", n).groups()), "%Y%m%d%H%M%S")
print(int((t(sys.argv[2]) - t(sys.argv[1])).total_seconds() // 3600))' "$latest" "$newest_dump")"
    if [ "$lag_h" -gt "$STALE_H" ]; then
      echo "[WARN] 조각 목록($latest)이 전체 덤프($newest_dump)보다 ${lag_h}시간 낡았다 — dev 의 하루 한 번 내기(parts-to-drive.sh)가 멈췄다. 전체 덤프 길로"
      exit 5
    fi
  fi
fi
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
