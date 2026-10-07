#!/usr/bin/env bash
# AI Data Hub watchdog — 매분 cron 실행. 죽은 컴포넌트만 자동 복구.
# (HEAXHub scripts/watchdog.sh 검증 패턴 이식, 2026-06-10)
#
# 검사 대상:
#   1. aidh_postgres apptainer instance + pg_isready (port 5435)
#   2. uvicorn API + /health 200 (port 8001) — DB 를 쓰지 않는 탐침
#   3. health 게이지 — sync_stale_sources (경고만, 자동복구 X)
#   4. 백업 신선도 — backups/ 최신 파일 48h 초과 또는 실패 marker (경고만)
#
# 복구 전략:
#   - postgres 죽음 → start_postgres.sh (멱등)
#   - API 죽음    → start_api.sh (멱등)
#       · 연결 거부(안 듣는다)      → 곧바로 복구
#       · 시간 초과(듣는데 답이 늦다) → 연속 AIDH_WATCHDOG_TIMEOUT_STRIKES 회(기본 3, 약 3분) 뒤에만 복구
#   - "죽었다" 1차 판정 후 재검증 1회 (오탐 방지) 후에만 복구.
#
# 손잡이 (deploy/apptainer/.env):
#   AIDH_WATCHDOG_PROBE_TIMEOUT_S  (기본 5) — API 탐침 한 번이 답을 기다리는 시간(초)
#   AIDH_WATCHDOG_TIMEOUT_STRIKES  (기본 3) — 시간 초과가 몇 번 연속이면 재기동하나
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
# 판정은 '안 듣는다'(연결 거부)와 '듣는데 답이 늦다'(시간 초과)를 가른다. 앞은 죽은 것이라 곧바로
# 살리고, 뒤는 바쁜 것일 수 있어 연속으로 이어질 때만 살린다. 종전에는 5초 안에 두 번 답하지 못하면
# (약 15초) 재기동이었다 — dev 에서 그렇게 난 재기동 330건(하루 18~58건)이 전부 '듣고는 있는데
# 5초 안에 못 답한' 경우였고(연결 거부였다면 매분 6~7초에 찍혔을 줄이 16~18초에 찍혔다), 그때마다
# 진행 중이던 agent_search 가 통째로 끊겼다. 프로세스 교체는 가장 바깥에 있어야 하는데 가장 안쪽
# 한도(DB 풀 대기 60초)보다 짧았던 것이다(2026-10-08).
# 탐침 대상은 /health 다. /api/system/health 는 응답 전에 DB 게이지를 재서, 프로세스가 아니라 DB 를
# 재는 셈이었다.
_pos_num() { [[ "$1" =~ ^[0-9]+([.][0-9]+)?$ ]] && awk -v v="$1" 'BEGIN { exit !(v > 0) }'; }

PROBE_TIMEOUT_S="${AIDH_WATCHDOG_PROBE_TIMEOUT_S:-5}"
if ! _pos_num "$PROBE_TIMEOUT_S"; then
  _log "WARN: AIDH_WATCHDOG_PROBE_TIMEOUT_S='$PROBE_TIMEOUT_S' 는 양수가 아니다 — 5 로 본다"
  PROBE_TIMEOUT_S=5
fi
TIMEOUT_STRIKES="${AIDH_WATCHDOG_TIMEOUT_STRIKES:-3}"
if ! [[ "$TIMEOUT_STRIKES" =~ ^[1-9][0-9]*$ ]]; then
  _log "WARN: AIDH_WATCHDOG_TIMEOUT_STRIKES='$TIMEOUT_STRIKES' 는 1 이상의 정수가 아니다 — 3 으로 본다"
  TIMEOUT_STRIKES=3
fi
# 연속 시간 초과 횟수 — cron 실행 사이에 이어져야 해서 파일에 둔다(api.pid 와 같은 자리).
STRIKE_FILE="$LOG_DIR/watchdog.api-timeout-strikes"

# API_STATE = ok | refused | timeout | bad(…)
api_probe() {
  local code rc
  code=$("$CURL" -s -o /dev/null -w '%{http_code}' --max-time "$PROBE_TIMEOUT_S" \
    "http://127.0.0.1:${API_PORT}/health" 2>/dev/null)
  rc=$?
  if [[ $rc -eq 0 && "$code" = "200" ]]; then API_STATE=ok
  elif [[ $rc -eq 7 ]]; then API_STATE=refused            # curl 7 = 연결 거부
  elif [[ $rc -eq 28 ]]; then API_STATE=timeout           # curl 28 = --max-time 초과
  else API_STATE="bad(http=$code curl=$rc)"
  fi
}

# $1 = 사유, $2 = 그 판정을 정한 손잡이(없으면 생략) — 둘 다 로그에 그대로 적힌다
api_recover() {
  if [[ $DRY_RUN -eq 1 ]]; then
    _log "DRY: api $1 — would run start_api.sh"
    return 0
  fi
  _log "api $1 — recovering${2:+($2)} via start_api.sh"
  rm -f "$STRIKE_FILE"
  bash "$APPT_DIR/start_api.sh" >> "$LOG_FILE" 2>&1 \
    && _log "api recovery OK" \
    || _log "api recovery FAILED"
}

api_probe
if [[ "$API_STATE" != ok ]]; then
  sleep 5
  api_probe      # 재검증(오탐 방지) — 이 결과로 판정한다
fi

STRIKES=$(cat "$STRIKE_FILE" 2>/dev/null || true)
[[ "$STRIKES" =~ ^[0-9]+$ ]] || STRIKES=0
# '연속' 은 이어진 cron 실행을 뜻한다. 적힌 지 5분이 넘은 횟수는 잇지 않는다 — 그 사이 이 스크립트가
# 돌았고 API 가 답했다면 파일이 지워졌을 것이므로, 남아 있다는 것은 돌지 않았다는 뜻이다(cron 이 꺼져
# 있었거나 스크립트를 되돌렸다 다시 넣은 경우). 잇는다면 며칠 전의 2회가 오늘의 첫 시간 초과를 3회로 만든다.
if [[ -n "$(find "$STRIKE_FILE" -mmin +5 2>/dev/null)" ]]; then
  STRIKES=0
  [[ $DRY_RUN -eq 1 ]] || rm -f "$STRIKE_FILE"
fi

case "$API_STATE" in
  ok)
    if [[ $STRIKES -gt 0 && $DRY_RUN -eq 0 ]]; then
      _log "api answered again — timeout $STRIKES/$TIMEOUT_STRIKES 에서 스스로 돌아왔다(재기동 없음)"
      rm -f "$STRIKE_FILE"
    fi
    ;;
  timeout)
    STRIKES=$((STRIKES + 1))
    if [[ $STRIKES -ge $TIMEOUT_STRIKES ]]; then
      api_recover "timeout $STRIKES/$TIMEOUT_STRIKES" AIDH_WATCHDOG_TIMEOUT_STRIKES
    elif [[ $DRY_RUN -eq 1 ]]; then
      _log "DRY: api timeout $STRIKES/$TIMEOUT_STRIKES — would hold (횟수는 적지 않는다)"
    else
      echo "$STRIKES" > "$STRIKE_FILE"
      _log "api timeout $STRIKES/$TIMEOUT_STRIKES — /health 가 ${PROBE_TIMEOUT_S}초 안에 답하지 않았다(AIDH_WATCHDOG_PROBE_TIMEOUT_S). 듣고는 있어 재기동을 미룬다 — 연속 ${TIMEOUT_STRIKES}회면 복구(AIDH_WATCHDOG_TIMEOUT_STRIKES)"
    fi
    ;;
  *)
    api_recover "$API_STATE"
    ;;
esac

# ── 3. 신선도 게이지 (경고만 — 복구는 인앱 스케줄러 책임) ─────────
HEALTH=$("$CURL" -s --max-time "$PROBE_TIMEOUT_S" "http://127.0.0.1:${API_PORT}/api/system/health" 2>/dev/null || true)
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
