#!/usr/bin/env bash
# Drive-Sync 1회 셋업 — rclone remote 확인 + Drive 폴더 보장.
#
# 사용: bash setup-drive-sync.sh
# 토큰 입력이 필요한 경우: 다른 PC(브라우저 되는 곳)에서
#   rclone authorize "drive"
# 로 받은 JSON 토큰을 붙여넣는다.
set -euo pipefail
# shellcheck source=./_drive_common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_drive_common.sh"

echo "================================================================"
echo " Drive sync 셋업 — project=$PROJ_NAME"
echo "          remote=$DRIVE_REMOTE_NAME folder=$DRIVE_FOLDER"
echo "================================================================"

# 1) rclone 설치 확인
require_rclone
echo "[OK] $(rclone --version 2>&1 | head -1)"

# 2) rclone remote 등록 여부
RCLONE_CONF="${RCLONE_CONFIG:-$HOME/.config/rclone/rclone.conf}"
mkdir -p "$(dirname "$RCLONE_CONF")"
if remote_configured; then
  echo "[OK] remote '$DRIVE_REMOTE_NAME' 이미 설정됨 ($RCLONE_CONF)"
else
  echo
  echo "── 토큰 받기 (브라우저 되는 PC 에서) ──"
  echo "  rclone authorize \"drive\""
  echo "  → 끝에 출력되는 {\"access_token\":...} JSON 한 줄을 복사"
  echo
  read -r -p "여기에 토큰 JSON 한 줄 붙여넣기: " TOKEN
  if [[ -z "$TOKEN" ]]; then
    echo "[ERROR] 토큰 미입력 — 중단" >&2
    exit 1
  fi
  cat >> "$RCLONE_CONF" <<EOF

[$DRIVE_REMOTE_NAME]
type = drive
scope = drive
token = $TOKEN
EOF
  chmod 600 "$RCLONE_CONF"
  echo "[OK] $RCLONE_CONF 에 [$DRIVE_REMOTE_NAME] 추가"
fi

# 3) Drive 폴더 보장 + 접근 검증
echo "→ Drive 폴더 보장: $DRIVE_PATH"
rclone mkdir "$DRIVE_PATH" 2>&1 | sed 's/^/    /' || true
if ! rclone lsf "$DRIVE_PATH" >/dev/null 2>&1; then
  echo "[ERROR] Drive 접근 실패 — 토큰/네트워크/스코프 확인" >&2
  exit 1
fi
echo "[OK] $DRIVE_PATH 접근 성공"

echo
echo "================================================================"
echo "✓ Drive sync 준비 완료 ($PROJ_NAME)"
echo "  소스 서버:  bash backup-to-drive.sh"
echo "  타깃 서버:  bash sync-from-drive.sh"
echo "================================================================"
