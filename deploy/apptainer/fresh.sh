#!/usr/bin/env bash
# AI Data Hub — clean + start + migrate (한 방).
# 새 서버 첫 셋업 또는 완전 초기화 시나리오.
#
# ⚠ 기존 DB 데이터 모두 소실. 보존 필요하면 먼저 backup-db.sh.
#
# 사용:
#   bash deploy/apptainer/fresh.sh           # 미리보기 (clean dry-run)
#   bash deploy/apptainer/fresh.sh --yes     # 실제 실행
set -euo pipefail
# shellcheck source=/dev/null
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
load_env

if [[ "${1:-}" != "--yes" ]]; then
  echo "이 스크립트는 모든 DB 데이터를 삭제합니다."
  echo "실행하려면: bash deploy/apptainer/fresh.sh --yes"
  echo "백업 먼저: bash deploy/apptainer/backup-db.sh"
  exit 0
fi

echo "================================================================"
echo " AI Data Hub — fresh (clean + start + migrate)"
echo "================================================================"

# 1) clean
bash "$APPT_DIR/clean.sh" --yes

# 2) start postgres (initdb 부터)
bash "$APPT_DIR/start_postgres.sh"

# 3) start api (alembic upgrade head 자동 포함)
bash "$APPT_DIR/start_api.sh"

# 4) 검증
sleep 2
bash "$APPT_DIR/diag.sh" || true

echo
echo "✓ fresh 완료"
