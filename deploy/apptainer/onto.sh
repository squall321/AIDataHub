#!/usr/bin/env bash
# AI Data Hub — 카드 ↔ 온톨로지 객체 연결(카드 낱말 짓기 · 허브 사전으로 잇기 · 상태 · 프로필)을 API 와 같은 환경으로 돌린다.
#
#   bash deploy/apptainer/onto.sh build [--force]                 # 카드 낱말(허브가 필요 없다 · 지문이 같으면 건너뛴다)
#   bash deploy/apptainer/onto.sh bind --hub <허브> --dict <파일>  # 허브 사전으로 잇는다(포털 card-entity-bind.py 가 부른다)
#   bash deploy/apptainer/onto.sh status                          # 마지막 짓기 · 잇기와 줄 수(읽기만)
#   bash deploy/apptainer/onto.sh profile [--domains]             # 전문가 · 도메인의 타입별 셈(읽기만)
#
# 본체는 api.scripts.onto 다(--help). DATABASE_URL 은 start_api.sh 가 쓴 api_server/.env 에서 온다. 표(이행 0033)는 API 를
# 다시 띄울 때 올라간다. 설계는 HWAXPortal docs/expert-ontology 6절.
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
load_env

VENV_PY="$API_DIR/.venv/bin/python"
[[ -x "$VENV_PY" ]] || { echo "[ERROR] venv 없음($VENV_PY) — 먼저 bash deploy/apptainer/start_api.sh" >&2; exit 1; }
[[ -f "$API_DIR/.env" ]] || { echo "[ERROR] $API_DIR/.env 없음(DATABASE_URL) — 먼저 start_api.sh" >&2; exit 1; }

cd "$API_DIR"
export PYTHONPATH="$API_DIR/src"
exec "$VENV_PY" -m api.scripts.onto "$@"
