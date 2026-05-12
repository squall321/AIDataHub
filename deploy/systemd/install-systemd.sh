#!/usr/bin/env bash
# AI Data Hub — systemd unit 자동 등록.
# 재부팅 시 postgres + API 자동 기동.
#
# 사용:
#   bash deploy/systemd/install-systemd.sh             # 사용자 모드 (권장, sudo 불필요)
#   sudo bash deploy/systemd/install-systemd.sh --system  # 시스템 모드 (모든 사용자 영향)
#   bash deploy/systemd/install-systemd.sh --uninstall
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TEMPLATE="$ROOT_DIR/deploy/systemd/aidh.service.template"
USER_NAME="$(id -un)"

MODE="user"
DO_UNINSTALL=0
for arg in "$@"; do
  case "$arg" in
    --system)     MODE="system" ;;
    --uninstall)  DO_UNINSTALL=1 ;;
    -h|--help)    sed -n '2,10p' "${BASH_SOURCE[0]}"; exit 0 ;;
  esac
done

if [[ "$MODE" == "system" ]]; then
  TARGET="/etc/systemd/system/aidh.service"
  SYSTEMCTL=(sudo systemctl)
  ENABLE_CMD="sudo systemctl enable --now aidh.service"
  DISABLE_CMD="sudo systemctl disable --now aidh.service"
else
  USER_UNIT_DIR="$HOME/.config/systemd/user"
  mkdir -p "$USER_UNIT_DIR"
  TARGET="$USER_UNIT_DIR/aidh.service"
  SYSTEMCTL=(systemctl --user)
  ENABLE_CMD="systemctl --user enable --now aidh.service"
  DISABLE_CMD="systemctl --user disable --now aidh.service"
fi

if [[ $DO_UNINSTALL -eq 1 ]]; then
  echo "→ aidh.service 제거 (mode=$MODE)"
  "${SYSTEMCTL[@]}" stop aidh.service 2>/dev/null || true
  "${SYSTEMCTL[@]}" disable aidh.service 2>/dev/null || true
  if [[ "$MODE" == "system" ]]; then
    sudo rm -f "$TARGET"
    sudo systemctl daemon-reload
  else
    rm -f "$TARGET"
    systemctl --user daemon-reload
  fi
  echo "✓ 제거 완료"
  exit 0
fi

echo "================================================================"
echo " AI Data Hub — systemd install"
echo " mode    : $MODE"
echo " user    : $USER_NAME"
echo " project : $ROOT_DIR"
echo " target  : $TARGET"
echo "================================================================"

# 1) 템플릿 치환
SERVICE_CONTENT=$(sed -e "s|__USER__|$USER_NAME|g" -e "s|__PROJECT__|$ROOT_DIR|g" "$TEMPLATE")

# 2) 설치
if [[ "$MODE" == "system" ]]; then
  echo "→ install -m 644 $TARGET (sudo)"
  echo "$SERVICE_CONTENT" | sudo tee "$TARGET" >/dev/null
else
  echo "→ write $TARGET"
  echo "$SERVICE_CONTENT" > "$TARGET"
fi

# 3) daemon reload
echo "→ systemctl daemon-reload"
"${SYSTEMCTL[@]}" daemon-reload

# 4) (사용자 모드) linger 안내
if [[ "$MODE" == "user" ]]; then
  if ! loginctl show-user "$USER_NAME" 2>/dev/null | grep -q "Linger=yes"; then
    cat <<EOH

[INFO] 부팅 시 자동 기동을 원하면 user linger 활성화 필요:
    sudo loginctl enable-linger $USER_NAME

이걸 안 하면 — 로그인할 때만 service 시작됨.
EOH
  fi
fi

# 5) enable + start
echo "→ $ENABLE_CMD"
"${SYSTEMCTL[@]}" enable --now aidh.service

echo
echo "================================================================"
echo "✓ 등록 완료"
echo "================================================================"
echo
echo "확인:"
echo "  ${SYSTEMCTL[*]} status aidh.service"
echo "  ${SYSTEMCTL[*]} list-units --type=service | grep aidh"
echo
echo "수동 제어:"
echo "  ${SYSTEMCTL[*]} restart aidh.service"
echo "  ${SYSTEMCTL[*]} stop aidh.service"
echo
echo "로그:"
echo "  ${SYSTEMCTL[*]} status aidh.service -l"
echo "  journalctl --user -u aidh.service -f   # 사용자 모드"
echo "  journalctl -u aidh.service -f          # 시스템 모드"
echo
echo "제거:"
echo "  bash $0 --uninstall$([[ \"$MODE\" == \"system\" ]] && echo \" --system\")"
