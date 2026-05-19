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

  # ── 소유권 사전점검 ──────────────────────────────────────────────────
  # 기존 pgdata 가 다른 uid(예: 옛 setuid apptainer = 실제 root) 소유면
  # rootless/--fakeroot 컨테이너가 chmod/chown/traverse 못 해 postgres 가
  # 기동 실패한다 (로그: 'Operation not permitted' / 'Permission denied').
  # 멈추기 전에 정확한 1회 조치를 알려준다.
  _PGD="$DATA_DIR/postgres/pgdata"
  if [[ -d "$_PGD" ]]; then
    _own="$(stat -c '%u' "$_PGD" 2>/dev/null || echo '?')"
    if [[ "$_own" != "$(id -u)" ]] || ! ls "$_PGD" >/dev/null 2>&1; then
      echo "[ERROR] pgdata 소유권 불일치 — 현재 owner uid=$_own, 실행 uid=$(id -u)" >&2
      echo "        rootless/--fakeroot apptainer 가 이 디렉터리를 관리 못 합니다." >&2
      echo "        1회 조치 (데이터 보존):" >&2
      echo "          sudo chown -R \"\$(id -u):\$(id -g)\" \\" >&2
      echo "            $DATA_DIR/postgres $DATA_DIR/postgres-run" >&2
      echo "          bash deploy/apptainer/restart.sh" >&2
      echo "        또는 데이터 불필요 시:" >&2
      echo "          mv $_PGD ${_PGD}.old && bash deploy/apptainer/restart.sh" >&2
      echo "        (시스템 setuid apptainer 로 되돌리려면:" >&2
      echo "          AIDH_APPTAINER_BIN=\$(command -v apptainer) bash deploy/apptainer/restart.sh)" >&2
      exit 1
    fi
  fi

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

  # rootless apptainer (특히 setuid 없는 핀버전 = dpkg-deb 추출본)에서는
  # 공식 postgres 이미지 entrypoint 가 바인드된 PGDATA / /var/run/postgresql
  # 를 chmod 하려다 "Operation not permitted" 로 죽는다 → postgres 미기동.
  # --fakeroot 는 user namespace 에서 컨테이너를 root 로 매핑해 바인드
  # 마운트 chmod/chown 을 허용한다(= 이 문제 해결). 기본 ON.
  #   AIDH_APPT_FAKEROOT=0  → 비활성 (setuid 시스템 apptainer 등 불필요 시)
  # 기본값 자동: 핀버전(setuid 없는 .tools 추출본)이면 ON, 시스템 suid
  # apptainer 면 OFF(기존 동작 무회귀). 명시 AIDH_APPT_FAKEROOT 가 우선.
  _fr_default=0
  [[ "${_AIDH_APPT_SRC:-}" == pinned* || "${_AIDH_APPT_SRC:-}" == env* ]] && _fr_default=1
  FAKEROOT_OPTS=()
  if [[ "${AIDH_APPT_FAKEROOT:-$_fr_default}" = "1" ]]; then
    FAKEROOT_OPTS=(--fakeroot)
    echo "  (--fakeroot — rootless 바인드 chmod 허용; AIDH_APPT_FAKEROOT=0 로 해제)"
  fi

  apptainer instance start \
    "${HOST_NET_OPTS[@]}" \
    "${FAKEROOT_OPTS[@]}" \
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
    > "$LOG_DIR/postgres-start.log" 2>&1 || true

  # Fast-fail: 인스턴스가 안 떴으면 pg_isready 60s 헛대기 말고 즉시
  # 진짜 원인(start 로그)을 그대로 보여준다.
  sleep 2
  if ! instance_running "$INST_POSTGRES"; then
    echo "[ERROR] postgres instance start 실패 — apptainer=$_AIDH_APPT_SRC ($_AIDH_APPT)" >&2
    echo "        fakeroot=$([[ ${#FAKEROOT_OPTS[@]} -gt 0 ]] && echo on || echo off)" >&2
    echo "── postgres-start.log (전체) ─────────────────────────────────" >&2
    cat "$LOG_DIR/postgres-start.log" >&2 2>/dev/null || true
    echo "─────────────────────────────────────────────────────────────" >&2
    echo "조치 후보:" >&2
    echo "  · 'subuid'/'fakeroot' 거부 → AIDH_APPT_FAKEROOT=0 또는" >&2
    echo "    AIDH_APPTAINER_BIN=\$(command -v apptainer) 로 시스템 apptainer 사용" >&2
    echo "  · chmod Operation not permitted 지속 → 위 로그 그대로 공유" >&2
    exit 1
  fi
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

# 앱 DB 멱등 보장 — 없으면 생성, 있으면 그대로 사용.
# postgres 이미지는 POSTGRES_DB 를 PGDATA *최초 init* 때만 만든다. .env 의
# POSTGRES_DB 가 init 이후 바뀌었거나(오타 수정 등) 이전 run 이 다른 이름으로
# 초기화했으면 그 DB 가 없다. 단, role(POSTGRES_USER)은 한 번이라도 init 됐으면
# 슈퍼유저로 존재하므로, 관리DB(postgres)에 붙어 CREATE DATABASE 로 보강한다.
echo "→ DB '$POSTGRES_DB' 보장 (없으면 생성)"
_psql_maint() {
  # 관리용 'postgres' DB 에 POSTGRES_USER 로 접속해 임의 SQL 실행.
  apptainer exec "instance://$INST_POSTGRES" \
    psql -h 127.0.0.1 -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d postgres \
         -tA -X -v ON_ERROR_STOP=1 "$@"
}
if apptainer exec "instance://$INST_POSTGRES" \
     psql -h 127.0.0.1 -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
          -tAc "SELECT 1" >/dev/null 2>&1; then
  echo "✓ DB '$POSTGRES_DB' 이미 존재 — 사용"
elif _psql_maint -c "SELECT 1" >/dev/null 2>&1; then
  # role/비번은 정상 (관리DB 접속 OK) — 앱 DB 만 없음 → 생성.
  if _psql_maint -c "SELECT 1 FROM pg_database WHERE datname='$POSTGRES_DB'" \
       2>/dev/null | grep -q 1; then
    echo "✓ DB '$POSTGRES_DB' 존재 확인"
  else
    echo "  → CREATE DATABASE \"$POSTGRES_DB\" OWNER \"$POSTGRES_USER\""
    _psql_maint -c "CREATE DATABASE \"$POSTGRES_DB\" OWNER \"$POSTGRES_USER\";" \
      || { echo "[ERROR] CREATE DATABASE 실패 — apptainer logs $INST_POSTGRES" >&2; exit 1; }
    echo "✓ DB '$POSTGRES_DB' 생성됨"
  fi
else
  # 관리DB 접속조차 실패 = role/비번이 PGDATA 와 불일치 (다른 설정으로 init).
  echo "[ERROR] role '$POSTGRES_USER' 로 접속 불가 — PGDATA 가 다른 자격으로 초기화됨." >&2
  echo "        .env 의 POSTGRES_USER/PASSWORD 가 PGDATA 최초 init 값과 다릅니다." >&2
  echo >&2
  echo "        조치:" >&2
  echo "          1) .env 확인 (.env.example 기본: POSTGRES_USER=aidh)" >&2
  echo "          2) PGDATA 완전 초기화 후 재시도:" >&2
  echo "             bash $APPT_DIR/clean.sh && bash setup.sh" >&2
  echo "          (외부 공유 PG 면 EXTERNAL_POSTGRES=1 + setup-shared-pg.sh)" >&2
  exit 1
fi

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
