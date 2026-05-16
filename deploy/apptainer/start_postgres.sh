#!/usr/bin/env bash
# AI Data Hub — PostgreSQL+pgvector 기동.
# 두 가지 모드:
#   1. SELF-MANAGED (default, EXTERNAL_POSTGRES=0): 자체 apptainer instance 띄움
#   2. EXTERNAL (EXTERNAL_POSTGRES=1): 다른 프로젝트의 PG (예: MXWP 의 mxwp_postgres:5532)
#      를 공유 사용. 인스턴스 안 띄우고 reachability + DB 존재만 검증.
set -euo pipefail
# shellcheck source=/dev/null
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
load_env
export_proxy
require_apptainer
ensure_dirs

# ── EXTERNAL_POSTGRES 모드 ────────────────────────────────────────
if [[ "${EXTERNAL_POSTGRES:-0}" = "1" ]]; then
  EXT_INST="${EXTERNAL_PG_INSTANCE:-mxwp_postgres}"
  echo "================================================================"
  echo " AI Data Hub — postgres (EXTERNAL mode)"
  echo "================================================================"
  echo "  외부 PG instance : $EXT_INST"
  echo "  host:port        : ${POSTGRES_HOST:-127.0.0.1}:${POSTGRES_PORT}"
  echo "  user / db        : $POSTGRES_USER / $POSTGRES_DB"

  if ! apptainer instance list 2>/dev/null | awk 'NR>1{print $1}' | grep -qx "$EXT_INST"; then
    echo "[ERROR] $EXT_INST 미동작 — 해당 프로젝트에서 PG 먼저 기동 필요"
    echo "        예: cd ~/Projects/MXWhitePaper && ./infra/scripts/start.sh"
    exit 1
  fi
  echo "  ✓ $EXT_INST 동작 중"

  # aidh user 로 직접 접속 시도 — 못 닿으면 setup-shared-pg.sh 안내
  if ! apptainer exec "instance://$EXT_INST" \
         env PGPASSWORD="$POSTGRES_PASSWORD" \
         psql -h 127.0.0.1 -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
              -tA -X -c "SELECT 1" 2>/dev/null | grep -q "^1$"; then
    echo "[ERROR] $POSTGRES_USER@$POSTGRES_DB 접속 실패"
    echo
    echo "        외부 PG 안에 aidh user/db 아직 안 만들었을 가능성. 한 번만:"
    echo "          bash deploy/apptainer/setup-shared-pg.sh"
    echo
    echo "        또는 .env 의 POSTGRES_PASSWORD 가 외부 PG 의 비번과 안 맞을 수도."
    exit 1
  fi
  echo "  ✓ $POSTGRES_USER@$POSTGRES_DB 접속 OK"

  # pgvector 확장 확인
  VEC=$(apptainer exec "instance://$EXT_INST" \
         env PGPASSWORD="$POSTGRES_PASSWORD" \
         psql -h 127.0.0.1 -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
              -tA -X -c "SELECT extname FROM pg_extension WHERE extname='vector'" 2>/dev/null || echo "")
  if [[ "$VEC" = "vector" ]]; then
    echo "  ✓ pgvector 확장 활성"
  else
    echo "  ! pgvector 확장 없음 — setup-shared-pg.sh 재실행 필요"
  fi

  echo
  echo "✓ EXTERNAL postgres 준비 완료. 다음: bash start_api.sh"
  exit 0
fi

# ── 이하 SELF-MANAGED 모드 (default) ─────────────────────────────────
if [[ ! -f "$APPT_DIR/postgres.sif" ]]; then
  echo "[ERROR] postgres.sif 없음. 먼저: bash build.sh" >&2
  exit 1
fi

if instance_running "$INST_POSTGRES"; then
  echo "✓ $INST_POSTGRES 이미 실행 중"
else
  # ── Stale-lock cleanup (MXWhitePaper 패턴) ────────────────────────────
  # postgres 컨테이너가 정상 종료 못 했을 때 (host reboot / OOM / kill -9 /
  # apptainer stop while busy) socket lock + postmaster.pid 가 남는다.
  # 다음 start 시 다음 에러로 pg_isready 무한 대기:
  #   FATAL: lock file ".s.PGSQL.5435.lock" already exists
  #   HINT:  Is another postmaster (PID 14) using socket file ...
  # 안전 가드:
  #   1) 인스턴스가 정말 동작 중이 아닐 때만 정리 (위 instance_running 통과)
  #   2) lock 의 PID 가 진짜 살아있는지 kill -0 으로 확인 (실수로 라이브 DB 죽이지 X)
  for f in "$DATA_DIR/postgres-run/.s.PGSQL.${POSTGRES_PORT}.lock" \
           "$DATA_DIR/postgres/pgdata/postmaster.pid"; do
    [ -e "$f" ] || continue
    pid="$(head -n1 "$f" 2>/dev/null | tr -dc '0-9')"
    if [ -z "$pid" ] || ! kill -0 "$pid" 2>/dev/null; then
      echo "  → stale lock 정리: $(basename "$f") (pid=${pid:-?} 미존재)"
      rm -f "$f"
    fi
  done

  require_port_free "$POSTGRES_PORT" "POSTGRES"
  echo "→ start $INST_POSTGRES"

  # Host network opt-in (Issue #3 — apptainer 가 자체 netns 에 들어가면
  # host 의 127.0.0.1:${POSTGRES_PORT} 로 native API 가 못 닿을 수 있음).
  # AIDH_APPT_HOST_NET=1 일 때만 ``--net --network=host`` 추가.
  # host CNI conflist 가 없는 빌드에서는 이 옵션이 에러를 내므로 기본 off.
  HOST_NET_OPTS=()
  if [[ "${AIDH_APPT_HOST_NET:-0}" = "1" ]]; then
    HOST_NET_OPTS=(--net --network=host)
    echo "  (AIDH_APPT_HOST_NET=1 — --net --network=host 적용)"
  fi

  apptainer instance start \
    "${HOST_NET_OPTS[@]}" \
    --bind "$DATA_DIR/postgres:/var/lib/postgresql/data" \
    --bind "$DATA_DIR/postgres-run:/var/run/postgresql" \
    --env "POSTGRES_USER=${POSTGRES_USER}" \
    --env "POSTGRES_PASSWORD=${POSTGRES_PASSWORD}" \
    --env "POSTGRES_DB=${POSTGRES_DB}" \
    --env "PGPORT=${POSTGRES_PORT}" \
    --env "PGDATA=/var/lib/postgresql/data/pgdata" \
    --env "LANG=C.UTF-8" \
    --env "LC_ALL=C.UTF-8" \
    "$APPT_DIR/postgres.sif" "$INST_POSTGRES" \
    > "$LOG_DIR/postgres-start.log" 2>&1
fi

echo "→ pg_isready 대기..."
_pg_ok=0
for i in $(seq 1 60); do
  if apptainer exec "instance://$INST_POSTGRES" \
       pg_isready -h 127.0.0.1 -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
       >/dev/null 2>&1; then
    echo "✓ postgres ready (${i}s)"
    _pg_ok=1; break
  fi
  sleep 1
done
if [[ "$_pg_ok" -ne 1 ]]; then
  echo "[ERROR] postgres 60s 내 미응답 — 인스턴스 로그 확인:" >&2
  echo "        apptainer logs $INST_POSTGRES   |   tail -30 $LOG_DIR/postgres-start.log" >&2
  exit 1
fi

# 앱 DB/role 실존 검증. postgres 이미지는 POSTGRES_DB/USER 를 PGDATA 최초
# init 시에만 생성한다. 이전 실패 run 이 PGDATA 를 다른 설정으로 초기화해
# 두면 (또는 .env 의 POSTGRES_DB 오타) 이 DB 가 영영 안 생기고, 그대로
# 두면 alembic 이 난해한 스택트레이스로 터진다. 여기서 명확히 멈춘다.
echo "→ DB '$POSTGRES_DB' 실존 검증"
if ! apptainer exec "instance://$INST_POSTGRES" \
     psql -h 127.0.0.1 -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
          -tAc "SELECT 1" >/dev/null 2>&1; then
  echo "[ERROR] role/DB '$POSTGRES_USER'/'$POSTGRES_DB' 로 접속 불가." >&2
  echo "        원인: postgres 이미지는 PGDATA *최초 init* 때만 POSTGRES_DB/" >&2
  echo "        USER 를 만든다. 이전 실패 run 이 PGDATA 를 다른 설정으로" >&2
  echo "        초기화했거나 .env 의 POSTGRES_DB 가 틀렸을 때 발생." >&2
  echo >&2
  echo "        조치:" >&2
  echo "          1) .env 확인 — POSTGRES_DB / POSTGRES_USER / POSTGRES_PASSWORD" >&2
  echo "             (.env.example 기본값: POSTGRES_DB=aidh, POSTGRES_USER=aidh)" >&2
  echo "          2) PGDATA 완전 초기화 후 재시도:" >&2
  echo "             bash $APPT_DIR/clean.sh        # data 디렉토리 wipe" >&2
  echo "             bash setup.sh                  # 다시" >&2
  echo "          (외부 공유 PG 면 EXTERNAL_POSTGRES=1 + setup-shared-pg.sh)" >&2
  exit 1
fi
echo "✓ DB '$POSTGRES_DB' 접속 OK"

echo "→ CREATE EXTENSION IF NOT EXISTS vector;"
apptainer exec "instance://$INST_POSTGRES" \
  psql -h 127.0.0.1 -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
       -c "CREATE EXTENSION IF NOT EXISTS vector;" \
  > "$LOG_DIR/postgres-ext.log" 2>&1 || {
    echo "[WARN] vector 확장 생성 실패 — 로그: $LOG_DIR/postgres-ext.log"
  }

echo
echo "✓ postgres 기동 완료"
echo "  host=127.0.0.1 port=${POSTGRES_PORT} user=${POSTGRES_USER} db=${POSTGRES_DB}"
echo "  로그: apptainer logs $INST_POSTGRES"
