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
# AIDH_MERGE_DUMP 미지정이거나, 지정됐어도 그 파일이 없으면(§3 의 --dry-run sync 가 latest.sql.gz
# 심링크만 만들고 실제 덤프는 안 받은 경우 — '덤프 없음'으로 죽던 원인) Drive 최신을 직접 받는다.
if [ -z "$DUMP" ] || [ ! -f "$DUMP" ]; then
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

merge_table() {  # $1=table $2=충돌키(csv) $3=updated_col(옵션) $4=제외컬럼(csv, 옵션) $5=m3 슬롯 채움 — 양쪽이 같아야 하는 열(csv, 옵션)
  # 충돌키는 **자연키**여야 한다. id 가 nextval() 대리키인 테이블(record_sections,
  # external_id_map 등)은 박스마다 값이 독립 생성돼, dev 의 id 가 운영에 없더라도
  # (record_id, section_id) 같은 자연키 유니크에 걸려 INSERT 전체가 죽는다
  # (실제로 cae00 에서 "record_sections: merge 실패" → set -e 로 뒤 4개 테이블과
  #  staging 정리까지 통째로 스킵됐다). 그런 테이블은 $4 로 id 를 제외해 운영 시퀀스가
  # 새 id 를 부여하게 하고, 자연키로 충돌 판정한다.
  local t="$1" pk="$2" upd="${3:-}" drop="${4:-}" same="${5:-}"
  # psql 이 접속·인증 실패로 죽으면 stdout 이 빈 문자열이라 '테이블 없음'과 구별되지 않는다.
  # 그러면 전 테이블이 skip 되는데 FAILED 에도 안 잡혀 '✓ merge 완료' 로 끝난다 —
  # 한 행도 안 들어왔는데 성공으로 보고된다. 'f' 만 진짜 '없음'이고 빈 값은 조회 실패다.
  local _tex _sex
  _tex="$(PSQL -d "$DB" -tA -c "SELECT to_regclass('public.$t') IS NOT NULL;" 2>/dev/null)"
  case "$_tex" in
    t) : ;;
    f) echo "    · $t: 운영에 없음 — skip"; return 0 ;;
    *) echo "    ✗ $t: 운영 DB 조회 실패(접속·인증 확인)" >&2; return 1 ;;
  esac
  _sex="$(STAGE_SQL -tA -c "SELECT to_regclass('public.$t') IS NOT NULL;" 2>/dev/null)"
  case "$_sex" in
    t) : ;;
    f) echo "    · $t: staging 에 없음 — skip"; return 0 ;;
    *) echo "    ✗ $t: staging 조회 실패" >&2; return 1 ;;
  esac

  # dev(staging)에만 있는 열 — 열 목록은 운영 DB 에서 읽으므로 운영 이행이 늦으면 그 열은 오류 없이 빠진다. 10-17 cae00 에서 0032 가 잠금으로
  # 늦어 m3 열이 그렇게 빠졌고, 다음 update-all 은 같은 덤프라 병합을 건너뛰었다(리뷰 H1). 이름째 알리고 끝에 3 으로 끝내 '병합함' 표식이
  # 안 적히게 한다(포털 update-all 이 3 을 '다음에 다시' 로 말한다).
  local _pc _sc _miss
  _pc="$(PSQL -d "$DB" -tA -c "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name='$t';" | sort)"
  _sc="$(STAGE_SQL -tA -c "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name='$t';" | sort)"
  _miss="$(comm -13 <(printf '%s\n' "$_pc") <(printf '%s\n' "$_sc") | grep -v '^$' | paste -sd',' - || true)"
  if [ -n "$_miss" ]; then
    echo "    ⚠ $t: dev 에만 있는 열 — $_miss — 운영 DB 이행이 늦어 이 병합에서 빠졌다(이행 뒤 merge-from-drive.sh 를 한 번 더)"
    COLS_MISSING="${COLS_MISSING:-} $t"
  fi
  local cols; cols="$(PSQL -d "$DB" -tA -c "SELECT string_agg(quote_ident(column_name),',' ORDER BY ordinal_position) FROM information_schema.columns WHERE table_schema='public' AND table_name='$t' AND column_name <> ALL (string_to_array('$drop',','));")"
  local skip="$pk"; [ -n "$drop" ] && skip="$pk,$drop"
  local setclause; setclause="$(PSQL -d "$DB" -tA -c "
    SELECT string_agg(quote_ident(column_name)||'=EXCLUDED.'||quote_ident(column_name),', ')
    FROM information_schema.columns
    WHERE table_schema='public' AND table_name='$t' AND column_name <> ALL (string_to_array('$skip',','));")"
  # 엄격히 더 새로울 때만 덮는다 — 아래 세션은 aidh.keep_updated_at 으로 운영 updated_at 을 dev 값 그대로 두므로(병합 시각으로 올리지 않는다),
  # >= 이면 바뀌지 않은 행도 매번 다시 써 m3 색인까지 갱신한다.
  local where=""; [ -n "$upd" ] && where="WHERE $t.$upd IS NULL OR EXCLUDED.$upd > $t.$upd"
  local action="DO UPDATE SET $setclause $where"; [ -n "$setclause" ] || action="DO NOTHING"

  # m3 슬롯 열(<열>_m3 · _m3_at · _m3_model)은 운영 값이 비었으면 updated_at 과 상관없이 채운다 — dev 의 m3 백필은 updated_at 을 일부러 안
  # 움직이고(keep_updated_at), 예전 병합이 운영 updated_at 을 병합 시각으로 올려 둔 행이 많아 위 upsert 가 건너뛰었다(10-17 cae00 리뷰 H1-b
  # — 레코드 시그니처 59,032건 전부). 있는 값은 덮지 않는다. $5 의 열(시그니처의 재료)이 양쪽에서 같을 때만 채운다 — 운영에서 고친 레코드는
  # 시그니처를 비워 그 박스의 스윕이 새 내용으로 다시 계산하는데(db_writer), 그 사이 병합이 dev 의 옛 내용 벡터를 넣으면 스윕이 다시 하지 않는다.
  local fill="" _s _on
  if [ -n "$upd" ] && [ -n "$same" ]; then
    _on="$(printf '%s' "$pk" | tr ',' '\n' | sed 's/.*/r.& = m.&/' | paste -sd'&' - | sed 's/&/ AND /g')"
    _on="$_on$(printf '%s' "$same" | tr ',' '\n' | sed 's/.*/ AND r.& IS NOT DISTINCT FROM m.&/' | paste -sd'\0' -)"
    for _s in $(printf '%s\n' "$_pc" | grep -E '_m3$' || true); do
      printf '%s\n' "$_sc" | grep -qx "$_s" || continue
      fill="$fill
UPDATE public.$t AS r SET $_s = m.$_s, ${_s}_at = m.${_s}_at, ${_s}_model = m.${_s}_model FROM _mg m
 WHERE $_on AND r.$_s IS NULL AND m.$_s IS NOT NULL;"
    done
  fi

  local before after
  before="$(PSQL -d "$DB" -tA -c "SELECT count(*) FROM $t;")"
  apptainer exec "instance://$INST" rm -f "$CFILE" 2>/dev/null
  # ① staging → 파일(서버 COPY)
  STAGE_SQL -v ON_ERROR_STOP=1 -q -c "COPY (SELECT $cols FROM public.$t) TO '$CFILE' (FORMAT binary);" 2>>/tmp/aidh-merge.log \
    || { echo "    ✗ $t: staging COPY 실패"; return 1; }
  # ② 운영 단일 세션: TEMP → COPY FROM 파일 → upsert
  # aidh.keep_updated_at — 운영 updated_at 을 병합 시각으로 올리지 않는다(트리거가 이 설정을 본다, 이행 0032). 세션 단위라 이 psql 하나에만 든다.
  if PSQL -d "$DB" -v ON_ERROR_STOP=1 -q >>/tmp/aidh-merge.log 2>&1 <<SQL
SET aidh.keep_updated_at = 'on';
CREATE TEMP TABLE _mg (LIKE public.$t INCLUDING DEFAULTS);
COPY _mg ($cols) FROM '$CFILE' (FORMAT binary);
INSERT INTO public.$t ($cols) SELECT $cols FROM _mg ON CONFLICT ($pk) $action;$fill
DROP TABLE _mg;
SQL
  then after="$(PSQL -d "$DB" -tA -c "SELECT count(*) FROM $t;")"; echo "    ✓ $t: +$((after-before)) (총 $after)"
  else echo "    ✗ $t: merge 실패 — /tmp/aidh-merge.log 확인"; apptainer exec "instance://$INST" rm -f "$CFILE" 2>/dev/null; return 1; fi
  apptainer exec "instance://$INST" rm -f "$CFILE" 2>/dev/null
}

echo "→ 테이블 merge (부모→자식 순)"
: > /tmp/aidh-merge.log
# 한 테이블 실패가 나머지와 staging 정리까지 막지 않게 실패를 모아 마지막에 판정한다.
FAILED=""
try_merge() { merge_table "$@" || FAILED="$FAILED $1"; }

try_merge org_teams              "code"                 ""
try_merge org_groups             "team_code,code"       ""
try_merge doc_types              "code"                 ""
try_merge agents                 "agent_type"           "updated_at"
# 레코드 시그니처(m3)는 제목 · 본문이 양쪽에서 같을 때만 채운다(시그니처 = 제목 + 본문의 caption · headers · notes).
try_merge records                "id"                   "updated_at" ""         "title,content_hash"
try_merge agent_records          "agent_type,record_id" ""
# ↓ id 가 대리키(nextval)라 자연키로 충돌 판정하고 id 는 운영 시퀀스에 맡긴다.
try_merge record_sections        "record_id,section_id" ""          "id"
try_merge record_attachments     "id"                   ""
try_merge agent_sample_embeddings "id"                  ""
try_merge external_id_map        "source,external_id"   ""          "id"
try_merge mcp_upstreams          "alias"                ""

# 5) staging 정리 (실패해도 반드시 수행 — 남으면 다음 실행이 이름 충돌로 막힌다)
PSQL -d postgres -c "DROP DATABASE IF EXISTS $STAGE_DB WITH (FORCE);" >/dev/null
if [ -n "$FAILED" ]; then
  echo "✗ merge 일부 실패:$FAILED — /tmp/aidh-merge.log 확인 (나머지 테이블은 반영됨)"
  exit 1
fi
if [ -n "${COLS_MISSING:-}" ]; then
  echo "⚠ merge 는 됐지만 dev 에만 있는 열이 빠졌다:$COLS_MISSING — 운영 DB 이행(start_api.sh 의 alembic) 뒤 이 스크립트를 한 번 더 돌린다"
  exit 3
fi
echo "✓ merge 완료 — 운영 DB 유지 + dev 신규 반영. 롤백: restore-db.sh $BK"
