#!/usr/bin/env bash
# AI Data Hub — 최후의 핵폭탄. 모든 게 깨졌을 때.
# desudo + clean + fresh + diag 를 순차 실행.
#
# 시나리오:
#   - 권한 깨짐 + DB 손상 + 인스턴스 좀비 + 임베딩 모델 오염 등 복합 장애.
#   - 데이터 보존 의지 없음 (백업 사전 수행 가정).
#
# ⚠ 모든 데이터 삭제. backup-db.sh 먼저!
#
# 사용:
#   bash deploy/apptainer/recover.sh           # 미리보기 (dry)
#   bash deploy/apptainer/recover.sh --yes     # 실 실행
set -euo pipefail
# shellcheck source=/dev/null
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
load_env

if [[ "${1:-}" != "--yes" ]]; then
  cat <<'EOH'
================================================================
 AI Data Hub — recover (NUCLEAR OPTION)
================================================================

이 스크립트는 다음을 순차 실행합니다:
  1. stop.sh              — 인스턴스 / API 종료
  2. desudo.sh --yes      — sudo 흔적 회복
  3. clean.sh --yes       — data 디렉토리 모두 삭제
  4. start_postgres.sh    — 새 initdb
  5. start_api.sh         — alembic upgrade head
  6. diag.sh              — 검증

⚠ 모든 DB 데이터가 사라집니다.
   백업이 필요하면 먼저:
     bash deploy/apptainer/backup-db.sh

실 실행: bash deploy/apptainer/recover.sh --yes
EOH
  exit 0
fi

echo "================================================================"
echo " AI Data Hub — recover (NUCLEAR)"
echo "================================================================"

echo
echo "[1/6] stop 모든 인스턴스 / API"
bash "$APPT_DIR/stop.sh" || true

echo
echo "[2/6] desudo (권한 회복)"
bash "$APPT_DIR/desudo.sh" --yes || true

echo
echo "[3/6] clean (data 삭제)"
bash "$APPT_DIR/clean.sh" --yes

echo
echo "[4/6] start postgres"
bash "$APPT_DIR/start_postgres.sh"

echo
echo "[5/6] start api"
bash "$APPT_DIR/start_api.sh"

echo
echo "[6/6] diag"
sleep 2
bash "$APPT_DIR/diag.sh" || true

echo
echo "================================================================"
echo "✓ recover 완료"
echo "================================================================"
