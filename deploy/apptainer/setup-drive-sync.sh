#!/usr/bin/env bash
# AI Data Hub — Google Drive sync 1회 설치 (rclone + 토큰 + .env).
#
# 왜: 다른 서버로 DB 를 옮기는 표준 경로(MXWhitePaper 검증 패턴) =
#     소스에서 pg_dump → tar.gz → Drive 업로드, 타깃에서 다운로드 →
#     restore. rclone 으로 Drive 와 통신, 토큰은 한 번만 받아 저장.
#
# 사용:
#   bash deploy/apptainer/setup-drive-sync.sh
#
# 입력: 다른 PC(브라우저 되는 곳)에서 rclone authorize "drive" 로 받은
#       JSON 토큰을 붙여넣는다. 헤드리스 서버에서도 OK.
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
load_env 2>/dev/null || true
export_proxy 2>/dev/null || true

REMOTE_NAME="${AIDH_DRIVE_REMOTE_NAME:-AidhDrive}"
DRIVE_FOLDER="${AIDH_DRIVE_FOLDER:-AIDataHub/db-dumps}"
RETAIN_DEFAULT="${AIDH_DRIVE_RETAIN:-5}"

echo "================================================================"
echo " Google Drive sync 설치 — remote=$REMOTE_NAME  folder=$DRIVE_FOLDER"
echo "================================================================"

# 1) rclone 설치
if ! command -v rclone >/dev/null 2>&1; then
  echo "→ rclone 미설치 — apt 로 설치"
  if command -v sudo >/dev/null 2>&1; then
    sudo apt-get update -qq && sudo apt-get install -y rclone
  else
    echo "[ERROR] sudo 없음. 수동: 'curl https://rclone.org/install.sh | sudo bash'" >&2
    exit 1
  fi
fi
echo "[OK] $(rclone --version 2>&1 | head -1)"

# 2) rclone.conf 확인
RCLONE_CONF="${RCLONE_CONFIG:-$HOME/.config/rclone/rclone.conf}"
mkdir -p "$(dirname "$RCLONE_CONF")"
if grep -q "^\[$REMOTE_NAME\]" "$RCLONE_CONF" 2>/dev/null; then
  echo "[OK] rclone remote '$REMOTE_NAME' 이미 설정됨 ($RCLONE_CONF)"
else
  echo
  echo "── 토큰 받기 (브라우저 되는 PC 에서, rclone 설치 후) ──"
  echo "  rclone authorize \"drive\""
  echo "  → 끝에 출력되는 {\"access_token\":...} JSON 한 줄을 복사"
  echo
  read -r -p "여기에 토큰 JSON 한 줄 붙여넣기: " TOKEN
  if [[ -z "$TOKEN" ]]; then
    echo "[ERROR] 토큰 미입력 — 중단" >&2
    exit 1
  fi
  cat >> "$RCLONE_CONF" <<EOF

[$REMOTE_NAME]
type = drive
scope = drive
token = $TOKEN
EOF
  chmod 600 "$RCLONE_CONF"
  echo "[OK] $RCLONE_CONF 에 [$REMOTE_NAME] 추가"
fi

# 3) Drive 폴더 보장 + 동작 검증
REMOTE_PATH="${REMOTE_NAME}:${DRIVE_FOLDER}"
echo "→ Drive 폴더 보장: $REMOTE_PATH"
rclone mkdir "$REMOTE_PATH" 2>&1 | sed 's/^/    /' || true
if ! rclone lsf "$REMOTE_PATH" >/dev/null 2>&1; then
  echo "[ERROR] Drive 접근 실패 — 토큰/네트워크/스코프 확인" >&2
  exit 1
fi
echo "[OK] $REMOTE_PATH 접근 성공"

# 4) .env 갱신
ENV_FILE="$APPT_DIR/.env"
_set_env() {
  local k="$1" v="$2"
  if grep -q "^${k}=" "$ENV_FILE" 2>/dev/null; then
    sed -i "s|^${k}=.*|${k}=${v}|" "$ENV_FILE"
  else
    echo "${k}=${v}" >> "$ENV_FILE"
  fi
}
_set_env AIDH_DRIVE_REMOTE "$REMOTE_PATH"
_set_env AIDH_DRIVE_RETAIN "$RETAIN_DEFAULT"
echo "[OK] .env 갱신: AIDH_DRIVE_REMOTE=$REMOTE_PATH · AIDH_DRIVE_RETAIN=$RETAIN_DEFAULT"

echo
echo "================================================================"
echo "✓ Drive sync 준비 완료"
echo "  소스 서버:  bash deploy/apptainer/backup-to-drive.sh"
echo "  타깃 서버:  bash deploy/apptainer/sync-from-drive.sh"
echo "================================================================"
