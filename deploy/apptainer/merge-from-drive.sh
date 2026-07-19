#!/usr/bin/env bash
# AI Data Hub — Drive 의 dev 덤프를 cae00 운영 DB에 MERGE(추가/갱신)한다. DROP 하지 않는다.
# cae00 에서 자체 등록한 에이전트·레코드는 유지하고, dev 신규는 추가, 양쪽에 있으면
# updated_at 최신 우선(없으면 dev 로 갱신). staging DB 로 로드 후 테이블별 upsert(FK 순서).
#
#   bash deploy/apptainer/merge-from-drive.sh            # Drive 최신 덤프로 merge
#   AIDH_MERGE_DUMP=/path/to.sql.gz bash ... merge-from-drive.sh   # 로컬 덤프 지정
#
# restore-db.sh(DROP+통짜)와의 차이: 이건 비파괴 merge — 운영 DB가 순간도 비지 않는다.
set -euo pipefail
APPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$APPT_DIR/_common.sh"
load_env

PORT="${POSTGRES_PORT:?}"; USER="${POSTGRES_USER:?}"; DB="${POSTGRES_DB:?}"; INST="${INST_POSTGRES:?}"
STAGE_DB="${DB}_mergestage"
PSQL() { apptainer exec "instance://$INST" psql -h 127.0.0.1 -p "$PORT" -U "$USER" "$@"; }

# 1) 덤프 확보 — 로컬 지정 우선, 아니면 Drive 최신(sync-from-drive 의 다운로드 부분 재사용은
#    피하고 여기서 직접 받는다 — merge 는 restore 와 독립 경로).
DUMP="${AIDH_MERGE_DUMP:-}"
if [ -z "$DUMP" ]; then
  REMOTE="${AIDH_DRIVE_REMOTE:-}"; [ -n "$REMOTE" ] || { echo "[ERROR] AIDH_DRIVE_REMOTE 미설정"; exit 1; }
  command -v rclone >/dev/null || { echo "[ERROR] rclone 미설치"; exit 1; }
  LATEST="$(rclone lsf "$REMOTE/" 2>/dev/null | grep -E '\.sql\.gz$' | sort | tail -1)"
  [ -n "$LATEST" ] || { echo "[ERROR] Drive 에 덤프 없음($REMOTE)"; exit 1; }
  TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
  echo "→ Drive 덤프 다운로드: $LATEST"
  rclone copy "$REMOTE/$LATEST" "$TMP/"; DUMP="$TMP/$LATEST"
fi
[ -f "$DUMP" ] || { echo "[ERROR] 덤프 없음: $DUMP"; exit 1; }
echo "→ merge 소스: $DUMP ($(du -h "$DUMP" | cut -f1))"

# 2) staging DB 생성 + 덤프 로드(운영 DB 무손상)
echo "→ staging DB '$STAGE_DB' 생성 후 덤프 로드"
PSQL -d postgres -c "DROP DATABASE IF EXISTS $STAGE_DB WITH (FORCE);" >/dev/null
PSQL -d postgres -c "CREATE DATABASE $STAGE_DB;" >/dev/null
PSQL -d "$STAGE_DB" -c "CREATE EXTENSION IF NOT EXISTS vector;" >/dev/null 2>&1 || true
gunzip -c "$DUMP" | apptainer exec "instance://$INST" \
  psql -h 127.0.0.1 -p "$PORT" -U "$USER" -d "$STAGE_DB" >/tmp/aidh-merge-load.log 2>&1 \
  || { echo "[ERROR] staging 로드 실패 — /tmp/aidh-merge-load.log"; tail -5 /tmp/aidh-merge-load.log; PSQL -d postgres -c "DROP DATABASE IF EXISTS $STAGE_DB WITH (FORCE);" >/dev/null; exit 1; }

# 3) 운영 직전 스냅샷(롤백용, 로컬)
BK="/data/backups/aidh/pre-merge-$(date +%Y%m%d-%H%M%S).sql.gz"
mkdir -p "$(dirname "$BK")" 2>/dev/null && \
  apptainer exec "instance://$INST" pg_dump -h 127.0.0.1 -p "$PORT" -U "$USER" -d "$DB" 2>/dev/null | gzip -c > "$BK" \
  && echo "→ 운영 직전 스냅샷: $BK (롤백용)" || echo "  ⚠ 사전 스냅샷 생략(디스크/권한)"

# 4) 테이블별 MERGE — dev 실증 완료 방식(vector/배열/jsonb 무손실, 38967행 검증):
#    ① staging 서버 COPY → 컨테이너 내부 바이너리 파일(두 DB 가 같은 인스턴스라 /tmp 공유)
#    ② 운영 단일 세션에서 TEMP(LIKE 원본, 타입 자동 보존) → COPY FROM 파일 → upsert
#    updated_at 있는 테이블은 '더 최신일 때만' 갱신(cae00 최신 편집 보호), 없으면 EXCLUDED.
STAGE_SQL() { apptainer exec "instance://$INST" psql -h 127.0.0.1 -p "$PORT" -U "$USER" -d "$STAGE_DB" "$@"; }
CFILE="/tmp/aidh_mg.bin"   # 컨테이너 내부 경로(서버 COPY 대상)

merge_table() {  # $1=table $2=pk(csv) $3=updated_col(옵션)
  local t="$1" pk="$2" upd="${3:-}"
  [ "$(PSQL -d "$DB" -tA -c "SELECT to_regclass('public.$t') IS NOT NULL;")" = "t" ] \
    || { echo "    · $t: 운영에 없음 — skip"; return 0; }
  [ "$(STAGE_SQL -tA -c "SELECT to_regclass('public.$t') IS NOT NULL;")" = "t" ] \
    || { echo "    · $t: staging 에 없음 — skip"; return 0; }

  local cols; cols="$(PSQL -d "$DB" -tA -c "SELECT string_agg(quote_ident(column_name),',' ORDER BY ordinal_position) FROM information_schema.columns WHERE table_schema='public' AND table_name='$t';")"
  local setclause; setclause="$(PSQL -d "$DB" -tA -c "
    SELECT string_agg(quote_ident(column_name)||'=EXCLUDED.'||quote_ident(column_name),', ')
    FROM information_schema.columns
    WHERE table_schema='public' AND table_name='$t' AND column_name <> ALL (string_to_array('$pk',','));")"
  local where=""; [ -n "$upd" ] && where="WHERE $t.$upd IS NULL OR EXCLUDED.$upd >= $t.$upd"
  local action="DO UPDATE SET $setclause $where"; [ -n "$setclause" ] || action="DO NOTHING"

  local before after
  before="$(PSQL -d "$DB" -tA -c "SELECT count(*) FROM $t;")"
  apptainer exec "instance://$INST" rm -f "$CFILE" 2>/dev/null
  # ① staging → 파일(서버 COPY)
  STAGE_SQL -v ON_ERROR_STOP=1 -q -c "COPY (SELECT $cols FROM public.$t) TO '$CFILE' (FORMAT binary);" 2>>/tmp/aidh-merge.log \
    || { echo "    ✗ $t: staging COPY 실패"; return 1; }
  # ② 운영 단일 세션: TEMP → COPY FROM 파일 → upsert
  if PSQL -d "$DB" -v ON_ERROR_STOP=1 -q >>/tmp/aidh-merge.log 2>&1 <<SQL
CREATE TEMP TABLE _mg (LIKE public.$t INCLUDING DEFAULTS);
COPY _mg ($cols) FROM '$CFILE' (FORMAT binary);
INSERT INTO public.$t ($cols) SELECT $cols FROM _mg ON CONFLICT ($pk) $action;
DROP TABLE _mg;
SQL
  then after="$(PSQL -d "$DB" -tA -c "SELECT count(*) FROM $t;")"; echo "    ✓ $t: +$((after-before)) (총 $after)"
  else echo "    ✗ $t: merge 실패 — /tmp/aidh-merge.log 확인"; apptainer exec "instance://$INST" rm -f "$CFILE" 2>/dev/null; return 1; fi
  apptainer exec "instance://$INST" rm -f "$CFILE" 2>/dev/null
}

echo "→ 테이블 merge (부모→자식 순)"
: > /tmp/aidh-merge.log
merge_table org_teams              "code"                ""
merge_table org_groups             "team_code,code"      ""
merge_table doc_types              "code"                ""
merge_table agents                 "agent_type"          "updated_at"
merge_table records                "id"                  "updated_at"
merge_table agent_records          "agent_type,record_id" ""
merge_table record_sections        "id"                  ""
merge_table record_attachments     "id"                  ""
merge_table agent_sample_embeddings "id"                 ""
merge_table external_id_map        "id"                  ""
merge_table mcp_upstreams          "alias"               ""

# 5) staging 정리
PSQL -d postgres -c "DROP DATABASE IF EXISTS $STAGE_DB WITH (FORCE);" >/dev/null
echo "✓ merge 완료 — 운영 DB 유지 + dev 신규 반영. 롤백: restore-db.sh $BK"
