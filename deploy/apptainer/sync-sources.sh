#!/usr/bin/env bash
# AI Data Hub — 정기 동기화 원천(sync_sources)을 이 박스에서 보고 · 끄고 · 켠다(API 와 같은 환경으로)
#
#   bash deploy/apptainer/sync-sources.sh list                       # 원천 · 켜짐 · 마지막 상태(읽기만)
#   bash deploy/apptainer/sync-sources.sh disable <이름> [<이름> …]   # 끈다 — 스케줄러가 건너뛰고 헬스가 낡음으로 세지 않는다
#   bash deploy/apptainer/sync-sources.sh enable  <이름> [<이름> …]   # 다시 켠다
#
# 원천은 박스마다 따로인 행이다(Drive 조각 동기화가 나르지 않는다). 원천 앱이 같은 박스에 사는 dev 에서는 정기 동기화가 실제로 당기므로
# 끄지 않는다. 내부 포트 가드에 막혀 실패만 쌓이는 박스에서만 끈다(HWAXPortal docs/leftovers-1121 L21-6 ④ · L21-9). 재기동은 필요 없다 —
# 스케줄러가 틱마다 표를 다시 읽는다. 본체는 api.scripts.sync_sources 다(--help).
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
load_env

VENV_PY="$API_DIR/.venv/bin/python"
[[ -x "$VENV_PY" ]] || { echo "[ERROR] venv 없음($VENV_PY) — 먼저 bash deploy/apptainer/start_api.sh" >&2; exit 1; }
[[ -f "$API_DIR/.env" ]] || { echo "[ERROR] $API_DIR/.env 없음(DATABASE_URL) — 먼저 start_api.sh" >&2; exit 1; }

cd "$API_DIR"
export PYTHONPATH="$API_DIR/src"
exec "$VENV_PY" -m api.scripts.sync_sources "$@"
