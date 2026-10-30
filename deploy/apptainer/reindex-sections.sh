#!/usr/bin/env bash
# AI Data Hub — record_sections 를 records.content 에서 다시 짓는다(MX 백서 하위 절 평탄화 고침) — API 와 같은 환경으로.
#
#   bash deploy/apptainer/reindex-sections.sh            # 다시 지을 레코드 · 절 수만 센다(쓰지 않는다)
#   bash deploy/apptainer/backup-db.sh                   # --apply 앞에 DB 를 떠 둔다(옛 행은 이 명령으로 못 되돌린다)
#   bash deploy/apptainer/reindex-sections.sh --apply    # 다시 지을 레코드의 절만 다시 짓는다(멱등, held 는 건너뛴다)
#   bash deploy/apptainer/reindex-sections.sh --apply --allow-loss   # held(사라질 글이 있는 카드)까지 — held_cards 를 본 뒤에만
#
# 본체는 api.scripts.reindex_sections 다(--help). DATABASE_URL 은 start_api.sh 가 쓴 api_server/.env 에서,
# 평탄화가 읽는 AIDH_CHUNK_* 는 deploy/apptainer/.env 에서 온다 — 적재가 짓는 것과 같은 행이 나오게.
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
load_env

VENV_PY="$API_DIR/.venv/bin/python"
[[ -x "$VENV_PY" ]] || { echo "[ERROR] venv 없음($VENV_PY) — 먼저 bash deploy/apptainer/start_api.sh" >&2; exit 1; }
[[ -f "$API_DIR/.env" ]] || { echo "[ERROR] $API_DIR/.env 없음(DATABASE_URL) — 먼저 start_api.sh" >&2; exit 1; }

cd "$API_DIR"
export PYTHONPATH="$API_DIR/src"
exec "$VENV_PY" -m api.scripts.reindex_sections "$@"
