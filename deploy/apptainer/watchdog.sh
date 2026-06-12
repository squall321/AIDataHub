#!/usr/bin/env bash
# AI Data Hub watchdog — 매분 cron 실행. 죽은 컴포넌트만 자동 복구.
# (HEAXHub scripts/watchdog.sh 검증 패턴 이식, 2026-06-10)
#
# 검사 대상:
#   1. aidh_postgres apptainer instance + pg_isready (port 5435)
#   2. uvicorn API + /api/system/health 200 (port 8001)
#   3. health 게이지 — sync_stale_sources (경고만, 자동복구 X)
#   4. 백업 신선도 — backups/ 최신 파일 48h 초과 또는 실패 marker (경고만)
#
# 복구 전략:
#   - postgres 죽음 → start_postgres.sh (멱등)
#   - API 죽음    → start_api.sh (멱등)
#   - "죽었다" 1차 판정 후 재검증 1회 (오탐 방지) 후에만 복구.
#
# 오탐 방지 (HEAXHub 교훈):
#   - cron 의 최소 PATH 때문에 ss/apptainer 미발견 → "살아있는데 죽었다"
#     오판을 막기 위해 PATH 명시 + 절대경로 resolve.
#
# 사용:
#   watchdog.sh            # 평소 실행 (복구 수행) — crontab '* * * * *'
#   watchdog.sh --dry-run  # 진단만 — mutate 하지 않음
set -uo pipefail

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:${PATH:-}"

APPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "$APPT_DIR/_common.sh"
load_env 2>/dev/null || true

DRY_RUN=0
[[ "${1:-}" = "--dry-run" ]] && DRY_RUN=1

LOG_FILE="$LOG_DIR/watchdog.log"
mkdir -p "$LOG_DIR"
# 로그 무한 증가 방지 — 기존 _common.sh rotate_log 재사용 (cap 5MB)
rotate_log "$LOG_FILE" 5 >/dev/null 2>&1 || true

_log() { echo "[$(date '+%F %T')] $*" >> "$LOG_FILE"; }

CURL="$(command -v curl || echo /usr/bin/curl)"

# ── 1. postgres ──────────────────────────────────────────────────
pg_ok() {
  command "$_AIDH_APPT" exec "instance://$INST_POSTGRES" \
    pg_isready -h 127.0.0.1 -p "$POSTGRES_PORT" -U "$POSTGRES_USER" \
    >/dev/null 2>&1
}

if ! pg_ok; then
  sleep 5
  if ! pg_ok; then
    if [[ $DRY_RUN -eq 1 ]]; then
      _log "DRY: postgres down — would run start_postgres.sh"
    else
      _log "postgres down — recovering via start_postgres.sh"
      bash "$APPT_DIR/start_postgres.sh" >> "$LOG_FILE" 2>&1 \
        && _log "postgres recovery OK" \
        || _log "postgres recovery FAILED"
    fi
  fi
fi

# ── 2. API ───────────────────────────────────────────────────────
api_ok() {
  "$CURL" -s -o /dev/null -w '%{http_code}' --max-time 5 \
    "http://127.0.0.1:${API_PORT}/api/system/health" 2>/dev/null | grep -q "^200$"
}

if ! api_ok; then
  sleep 5
  if ! api_ok; then
    if [[ $DRY_RUN -eq 1 ]]; then
      _log "DRY: api down — would run start_api.sh"
    else
      _log "api down — recovering via start_api.sh"
      bash "$APPT_DIR/start_api.sh" >> "$LOG_FILE" 2>&1 \
        && _log "api recovery OK" \
        || _log "api recovery FAILED"
    fi
  fi
fi

# ── 3. 신선도 게이지 (경고만 — 복구는 인앱 스케줄러 책임) ─────────
HEALTH=$("$CURL" -s --max-time 5 "http://127.0.0.1:${API_PORT}/api/system/health" 2>/dev/null || true)
if [[ -n "$HEALTH" ]]; then
  STALE=$(echo "$HEALTH" | python3 -c \
    "import sys,json;d=json.load(sys.stdin);v=d.get('sync_stale_sources');print('' if v is None else v)" \
    2>/dev/null || true)
  if [[ -n "$STALE" && "$STALE" != "0" ]]; then
    _log "WARN: sync_stale_sources=$STALE — 동기화 정체 (대시보드 확인)"
  fi
fi

# ── 4. 백업 신선도 (경고만) ──────────────────────────────────────
BACKUP_DIR="$APPT_DIR/backups"
if [[ -f "$BACKUP_DIR/.last-backup-failed" ]]; then
  _log "WARN: 마지막 Drive 백업 실패 marker 존재 — backup-to-drive.sh 수동 확인"
elif [[ -d "$BACKUP_DIR" ]]; then
  NEWEST=$(find "$BACKUP_DIR" -name "aidh-db-*.sql.gz" -mtime -2 2>/dev/null | head -1)
  CRON_HAS_BACKUP=$(crontab -l 2>/dev/null | grep -c "backup-to-drive" || true)
  if [[ -z "$NEWEST" && "$CRON_HAS_BACKUP" != "0" ]]; then
    _log "WARN: 48h 내 백업 파일 없음 — backup cron 동작 확인 필요"
  fi
fi

exit 0
