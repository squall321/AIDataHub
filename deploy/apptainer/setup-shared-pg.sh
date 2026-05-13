#!/usr/bin/env bash
# AI Data Hub — 외부 (공유) PostgreSQL 안에 aidh user/db/extension 자동 생성.
#
# 시나리오: 같은 서버에 이미 동작 중인 다른 Postgres (예: MXWhitePaper 의
# mxwp_postgres on 5532) 를 AIDataHub 가 공유 사용. 자기 인스턴스 안 띄움.
#
# 한 번만 실행 — 그 후 .env 의 EXTERNAL_POSTGRES=1 로 두면 start_postgres.sh
# 가 자기 인스턴스 안 띄우고 그냥 reachability 만 확인.
#
# 동작:
#   1. 외부 PG superuser 자격으로 psql 접속 (또는 apptainer exec 로 그 PG 인스턴스에 접속)
#   2. aidh user 가 없으면 CREATE USER, 있으면 PASSWORD 갱신
#   3. aidh DB 가 없으면 CREATE DATABASE
#   4. 권한 부여 + pgvector 확장
#
# 사용:
#   bash deploy/apptainer/setup-shared-pg.sh
#   bash deploy/apptainer/setup-shared-pg.sh --check    # 진단만, 변경 X
#
# .env 가 다음 변수 가져야 함:
#   POSTGRES_HOST=127.0.0.1
#   POSTGRES_PORT=5532                # 공유 PG 포트
#   POSTGRES_USER=aidh                # 만들 user (이 스크립트 가)
#   POSTGRES_PASSWORD=aidh_xxx
#   POSTGRES_DB=aidh                  # 만들 DB
#   EXTERNAL_PG_INSTANCE=mxwp_postgres # 그 PG 의 apptainer instance 이름
#   EXTERNAL_PG_SUPERUSER=mxwp        # superuser 이름 (DB owner)
#   EXTERNAL_PG_SUPERUSER_DB=mxwp     # superuser 가 붙을 default DB
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
load_env

CHECK_ONLY=0
[ "${1:-}" = "--check" ] && CHECK_ONLY=1

# ── env 검증 ─────────────────────────────────────────────────────
: "${POSTGRES_HOST:?POSTGRES_HOST 필요 (예: 127.0.0.1)}"
: "${POSTGRES_PORT:?POSTGRES_PORT 필요 (공유 PG 포트, 예: 5532)}"
: "${POSTGRES_USER:?POSTGRES_USER 필요 (만들 user 이름)}"
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD 필요}"
: "${POSTGRES_DB:?POSTGRES_DB 필요 (만들 DB 이름)}"
EXT_INST="${EXTERNAL_PG_INSTANCE:-mxwp_postgres}"
EXT_SUPER="${EXTERNAL_PG_SUPERUSER:-mxwp}"
EXT_SUPER_DB="${EXTERNAL_PG_SUPERUSER_DB:-mxwp}"

echo "================================================================"
echo " AI Data Hub — setup shared postgres"
echo "================================================================"
echo "  external PG instance : $EXT_INST"
echo "  external superuser   : $EXT_SUPER (db: $EXT_SUPER_DB)"
echo "  target host:port     : $POSTGRES_HOST:$POSTGRES_PORT"
echo "  aidh user / db       : $POSTGRES_USER / $POSTGRES_DB"
echo "  mode                 : $([[ $CHECK_ONLY -eq 1 ]] && echo CHECK-ONLY || echo APPLY)"

# ── 1. 외부 PG 인스턴스 동작 중인가 ─────────────────────────────────
if ! apptainer instance list 2>/dev/null | awk 'NR>1{print $1}' | grep -qx "$EXT_INST"; then
  echo
  echo "[ERROR] apptainer instance '$EXT_INST' 미동작"
  echo "        해당 프로젝트 (예: MXWhitePaper) 에서 PG 먼저 기동:"
  echo "          cd ~/Projects/MXWhitePaper && ./infra/scripts/start.sh"
  exit 1
fi
echo "  ✓ $EXT_INST 동작 중"

# ── 2. reachability ─────────────────────────────────────────────────
if ! apptainer exec "instance://$EXT_INST" \
       pg_isready -h 127.0.0.1 -p "$POSTGRES_PORT" -U "$EXT_SUPER" -d "$EXT_SUPER_DB" \
       >/dev/null 2>&1; then
  echo "[ERROR] $EXT_INST:$POSTGRES_PORT 가 superuser=$EXT_SUPER 로 응답 안 함"
  echo "        .env 의 EXTERNAL_PG_SUPERUSER / EXTERNAL_PG_SUPERUSER_DB 확인"
  exit 1
fi
echo "  ✓ pg_isready OK (superuser=$EXT_SUPER)"

# ── 3. aidh user / db / extension 상태 점검 ────────────────────────
psql_super() {
  apptainer exec "instance://$EXT_INST" \
    psql -h 127.0.0.1 -p "$POSTGRES_PORT" -U "$EXT_SUPER" -d "$EXT_SUPER_DB" \
         -tA -X -v ON_ERROR_STOP=1 "$@"
}

USER_EXISTS=$(psql_super -c "SELECT 1 FROM pg_roles WHERE rolname='$POSTGRES_USER'" 2>/dev/null || echo "")
DB_EXISTS=$(psql_super -c "SELECT 1 FROM pg_database WHERE datname='$POSTGRES_DB'" 2>/dev/null || echo "")

echo "  · user '$POSTGRES_USER' : $([[ -n "$USER_EXISTS" ]] && echo EXISTS || echo MISSING)"
echo "  · db   '$POSTGRES_DB'   : $([[ -n "$DB_EXISTS" ]] && echo EXISTS || echo MISSING)"

if [[ -n "$DB_EXISTS" ]]; then
  VEC_EXISTS=$(apptainer exec "instance://$EXT_INST" \
    psql -h 127.0.0.1 -p "$POSTGRES_PORT" -U "$EXT_SUPER" -d "$POSTGRES_DB" \
         -tA -X -c "SELECT 1 FROM pg_extension WHERE extname='vector'" 2>/dev/null || echo "")
  echo "  · pgvector ext       : $([[ -n "$VEC_EXISTS" ]] && echo EXISTS || echo MISSING)"
fi

if [[ $CHECK_ONLY -eq 1 ]]; then
  echo
  echo "── check 모드 — 변경 없음. 실제 적용: bash setup-shared-pg.sh"
  exit 0
fi

# ── 4. 적용 (CREATE / ALTER) ────────────────────────────────────────
echo
echo "[APPLY] user / db / extension 생성"

if [[ -z "$USER_EXISTS" ]]; then
  echo "  → CREATE USER $POSTGRES_USER"
  psql_super -c "CREATE USER \"$POSTGRES_USER\" WITH PASSWORD '$POSTGRES_PASSWORD';"
else
  echo "  → ALTER USER $POSTGRES_USER (PASSWORD 갱신)"
  psql_super -c "ALTER USER \"$POSTGRES_USER\" WITH PASSWORD '$POSTGRES_PASSWORD';"
fi

if [[ -z "$DB_EXISTS" ]]; then
  echo "  → CREATE DATABASE $POSTGRES_DB OWNER $POSTGRES_USER"
  psql_super -c "CREATE DATABASE \"$POSTGRES_DB\" OWNER \"$POSTGRES_USER\";"
else
  echo "  · db 이미 존재 — owner/권한만 보정"
  psql_super -c "ALTER DATABASE \"$POSTGRES_DB\" OWNER TO \"$POSTGRES_USER\";"
fi

echo "  → GRANT ALL PRIVILEGES"
psql_super -c "GRANT ALL PRIVILEGES ON DATABASE \"$POSTGRES_DB\" TO \"$POSTGRES_USER\";"

echo "  → CREATE EXTENSION IF NOT EXISTS vector (aidh db 안)"
apptainer exec "instance://$EXT_INST" \
  psql -h 127.0.0.1 -p "$POSTGRES_PORT" -U "$EXT_SUPER" -d "$POSTGRES_DB" \
       -X -c "CREATE EXTENSION IF NOT EXISTS vector;" >/dev/null

# 검증
echo
echo "[VERIFY] aidh user 로 직접 접속"
if apptainer exec "instance://$EXT_INST" \
     env PGPASSWORD="$POSTGRES_PASSWORD" \
     psql -h 127.0.0.1 -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
          -tA -X -c "SELECT 1" 2>&1 | grep -q "^1$"; then
  echo "  ✓ aidh@$POSTGRES_DB 접속 OK"
else
  echo "  ✗ aidh 로 접속 실패 — pg_hba.conf 또는 비밀번호 문제"
  exit 1
fi

echo
echo "================================================================"
echo "✓ shared PG 셋업 완료"
echo "================================================================"
echo
echo "이제 .env 에 EXTERNAL_POSTGRES=1 설정 후 start_api.sh 만 실행하면 됩니다."
echo "  bash deploy/apptainer/start_api.sh"
echo
echo "검증:"
echo "  bash deploy/apptainer/diag.sh"
