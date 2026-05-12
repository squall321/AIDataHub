#!/usr/bin/env bash
# AI Data Hub — crontab @reboot 등록 (systemd 안 쓸 때 가벼운 대안).
#
# 장점:
#   - sudo 불필요
#   - 단순 (crontab 1 줄)
# 단점:
#   - 헬스 모니터링 없음 (그냥 부팅 시 한 번 실행)
#   - 자동 재시작 안 함 (서비스 죽으면 영구히 죽은 상태)
#
# 더 견고한 자동 기동은: bash deploy/systemd/install-systemd.sh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BOOT_SH="$ROOT_DIR/deploy/apptainer/boot.sh"
LOG="$ROOT_DIR/deploy/apptainer/logs/cron-boot.log"

if [[ "${1:-}" = "--uninstall" ]]; then
  echo "→ crontab 에서 aidh @reboot 제거"
  ( crontab -l 2>/dev/null | grep -v "aidh.*boot.sh" ) | crontab -
  echo "✓ 제거 완료"
  exit 0
fi

ENTRY="@reboot sleep 20 && /bin/bash $BOOT_SH > $LOG 2>&1   # aidh"

echo "================================================================"
echo " AI Data Hub — crontab @reboot install"
echo "================================================================"
echo "추가될 항목:"
echo "  $ENTRY"
echo
echo "(sleep 20 = 시스템 부팅 안정화 대기. 네트워크 / fs mount 등 완료까지)"
echo

# 중복 방지
if crontab -l 2>/dev/null | grep -q "aidh.*boot.sh"; then
  echo "[INFO] 이미 등록돼 있음 — 갱신"
  ( crontab -l 2>/dev/null | grep -v "aidh.*boot.sh"; echo "$ENTRY" ) | crontab -
else
  ( crontab -l 2>/dev/null; echo "$ENTRY" ) | crontab -
fi

echo "✓ crontab 등록 완료"
echo
echo "확인:"
echo "  crontab -l | grep aidh"
echo
echo "재부팅 후 로그:"
echo "  tail -f $LOG"
echo
echo "제거:"
echo "  bash $0 --uninstall"
