#!/usr/bin/env bash
# AI Data Hub — crontab 등록 (@reboot 자동 기동 + 매분 watchdog).
#
# 장점:
#   - sudo 불필요
#   - 단순 (crontab 2 줄)
# watchdog 이 매분 죽은 컴포넌트만 자동 복구하므로 "서비스 죽으면 다음
# reboot 까지 영구 다운" 문제가 없다.
#
# 더 견고한 자동 기동은: bash deploy/systemd/install-systemd.sh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BOOT_SH="$ROOT_DIR/deploy/apptainer/boot.sh"
WATCHDOG_SH="$ROOT_DIR/deploy/apptainer/watchdog.sh"
LOG="$ROOT_DIR/deploy/apptainer/logs/cron-boot.log"

if [[ "${1:-}" = "--uninstall" ]]; then
  echo "→ crontab 에서 aidh @reboot + watchdog 제거"
  ( crontab -l 2>/dev/null | grep -v "aidh.*boot.sh" | grep -v "aidh-watchdog" ) | crontab -
  echo "✓ 제거 완료"
  exit 0
fi

ENTRY="@reboot sleep 20 && /bin/bash $BOOT_SH > $LOG 2>&1   # aidh"
WD_ENTRY="* * * * * /bin/bash $WATCHDOG_SH >/dev/null 2>&1   # aidh-watchdog"

echo "================================================================"
echo " AI Data Hub — crontab install (@reboot + watchdog)"
echo "================================================================"
echo "추가될 항목:"
echo "  $ENTRY"
echo "  $WD_ENTRY"
echo
echo "(sleep 20 = 시스템 부팅 안정화 대기. watchdog = 매분 죽은 컴포넌트만 복구)"
echo

# 중복 방지 — 기존 항목 제거 후 재등록 (멱등)
( crontab -l 2>/dev/null | grep -v "aidh.*boot.sh" | grep -v "aidh-watchdog"; \
  echo "$ENTRY"; echo "$WD_ENTRY" ) | crontab -

echo "✓ crontab 등록 완료"
echo
echo "확인:"
echo "  crontab -l | grep aidh"
echo
echo "재부팅 후 로그:"
echo "  tail -f $LOG"
echo "watchdog 로그:"
echo "  tail -f $ROOT_DIR/deploy/apptainer/logs/watchdog.log"
echo
echo "제거:"
echo "  bash $0 --uninstall"
