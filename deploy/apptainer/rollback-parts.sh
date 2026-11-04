#!/usr/bin/env bash
# AI Data Hub — 조각 병합 되돌리기(cae00): merge-from-drive.sh 조각 길이 남긴 앞 모습 · 새 키로 그 병합 앞으로 돌린다(HWAXPortal docs/aidh-parts-sync)
#
#   bash deploy/apptainer/rollback-parts.sh /data/backups/aidh/pre-parts-<시각>
#
# 자식 → 부모 차례로 ① 그 병합이 새로 넣은 키를 지우고 ② 덮인 줄을 앞 모습(이 박스 행 전체 — 대리 id 째)으로 되돌린다. 표본
# (agent_sample_embeddings)은 그 병합이 다시 지은 agent 의 행을 지우고 앞 모습을 넣는다. 병합 뒤 이 박스에서 같은 줄을 또 고쳤으면 그
# 고침도 앞 모습으로 돌아간다 — 되돌리기 전에 본다. 표마다 한 트랜잭션이고, 도움 표(_aidh_rb_*)는 끝에 지운다(멈춘 뒤 다시 돌리면 먼저 지운다).
set -euo pipefail
APPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$APPT_DIR/_common.sh"
load_env

PORT="${POSTGRES_PORT:?}"; USER="${POSTGRES_USER:?}"; DB="${POSTGRES_DB:?}"; INST="${INST_POSTGRES:?}"
DIR="${1:?앞 모습 폴더(merge-from-drive.sh 조각 길의 끝 줄)}"
[ -d "$DIR" ] || { echo "[ERROR] 폴더 없음: $DIR"; exit 1; }
PSQL() { apptainer exec "instance://$INST" psql -h 127.0.0.1 -p "$PORT" -U "$USER" -d "$DB" -v ON_ERROR_STOP=1 -qtA "$@"; }

# 표 : 충돌 키 — merge-from-drive.sh 의 try_merge 와 같은 키, 자식이 먼저다(새 레코드를 지우면 그 자식은 외래키로 함께 지워진다).
SPECS=(mcp_upstreams:alias external_id_map:source,external_id agent_sample_embeddings:AGENTS record_attachments:id
       record_sections:record_id,section_id agent_records:agent_type,record_id records:id agents:agent_type doc_types:code
       org_groups:team_code,code org_teams:code)
done_n=0
for spec in "${SPECS[@]}"; do
  t="${spec%%:*}"; pk="${spec#*:}"
  if [ "$pk" = AGENTS ]; then
    [ -f "$DIR/$t.agents.csv.gz" ] || continue
    PSQL -c "DROP TABLE IF EXISTS _aidh_rb_agents; DROP TABLE IF EXISTS _aidh_rb_before;
             CREATE TABLE _aidh_rb_agents (agent_type text); CREATE TABLE _aidh_rb_before (LIKE public.$t);" >/dev/null
    gunzip -c "$DIR/$t.agents.csv.gz" | PSQL -c "COPY _aidh_rb_agents FROM STDIN (FORMAT csv);" >/dev/null
    gunzip -c "$DIR/$t.before.bin.gz" | PSQL -c "COPY _aidh_rb_before FROM STDIN (FORMAT binary);" >/dev/null
    PSQL >/dev/null <<SQL
BEGIN;
DELETE FROM public.$t WHERE agent_type IN (SELECT agent_type FROM _aidh_rb_agents);
INSERT INTO public.$t SELECT * FROM _aidh_rb_before;
-- 순번은 앞으로만(merge-from-drive.sh merge_samples ① 과 같다) — 되돌린 앞 모습의 id 는 병합이 이미 순번 뒤로 밀어 둔 값 안이다.
SELECT setval(x.s, x.m) FROM (SELECT pg_get_serial_sequence('public.$t', 'id') AS s, (SELECT max(id) FROM public.$t) AS m) x
 WHERE x.m > COALESCE(pg_sequence_last_value(x.s::regclass), 0);
DROP TABLE _aidh_rb_agents; DROP TABLE _aidh_rb_before;
COMMIT;
SQL
    echo "  ✓ $t: 다시 지은 agent $(gunzip -c "$DIR/$t.agents.csv.gz" | grep -c .)개를 앞 모습으로"
    done_n=$((done_n + 1))
    continue
  fi
  [ -f "$DIR/$t.before.bin.gz" ] || continue
  on="$(printf '%s' "$pk" | tr ',' '\n' | sed 's/.*/t.& = k.&/' | paste -sd'&' - | sed 's/&/ AND /g')"
  setc="$(PSQL -c "SELECT string_agg(quote_ident(column_name) || '=EXCLUDED.' || quote_ident(column_name), ', ' ORDER BY ordinal_position)
                   FROM information_schema.columns WHERE table_schema='public' AND table_name='$t'
                    AND column_name <> ALL (string_to_array('$pk', ','));")"
  action="DO NOTHING"; [ -z "$setc" ] || action="DO UPDATE SET $setc"
  PSQL -c "DROP TABLE IF EXISTS _aidh_rb_new; DROP TABLE IF EXISTS _aidh_rb_before;
           CREATE TABLE _aidh_rb_new AS SELECT $pk FROM public.$t WITH NO DATA; CREATE TABLE _aidh_rb_before (LIKE public.$t);" >/dev/null
  [ -f "$DIR/$t.new.csv.gz" ] && gunzip -c "$DIR/$t.new.csv.gz" | PSQL -c "COPY _aidh_rb_new FROM STDIN (FORMAT csv);" >/dev/null
  gunzip -c "$DIR/$t.before.bin.gz" | PSQL -c "COPY _aidh_rb_before FROM STDIN (FORMAT binary);" >/dev/null
  out="$(PSQL <<SQL
BEGIN;
SET LOCAL aidh.keep_updated_at = 'on';
WITH d AS (DELETE FROM public.$t t USING _aidh_rb_new k WHERE $on RETURNING 1) SELECT count(*) FROM d;
WITH u AS (INSERT INTO public.$t SELECT * FROM _aidh_rb_before ON CONFLICT ($pk) $action RETURNING 1) SELECT count(*) FROM u;
DROP TABLE _aidh_rb_new; DROP TABLE _aidh_rb_before;
COMMIT;
SQL
)"
  echo "  ✓ $t: 새로 든 줄 $(printf '%s\n' "$out" | sed -n 1p)개 지움 · 덮인 줄 $(printf '%s\n' "$out" | sed -n 2p)개 되돌림"
  done_n=$((done_n + 1))
done
[ "$done_n" -gt 0 ] || { echo "[ERROR] $DIR 에 되돌릴 표가 없다(*.before.bin.gz)"; exit 1; }
echo "✓ 되돌림 — 표 $done_n 개($DIR)"
