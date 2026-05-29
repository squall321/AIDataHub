#!/usr/bin/env bash
# AI Data Hub — 부팅 자동 기동 설치 / 제거 / 상태.
#
# 부팅 후 SSH 안 들어와도 boot.sh 가 자동 실행되어 PG + uvicorn 살린다.
# crontab @reboot 사용 (가벼움 + 인프라 의존성 0 + SignalForge 와 동일 방식).
#
# 사용:
#   bash autostart-install.sh           # 설치 (이미 설치돼 있으면 멱등)
#   bash autostart-install.sh --remove  # 제거
#   bash autostart-install.sh --status  # 현재 등록 상태
#   bash autostart-install.sh --run-now # 지금 즉시 실행 (테스트)
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MARKER="# AIDataHub auto-start"
# 경로에 공백/특수문자 있을 수 있으므로 쌍따옴표로 escape.
ENTRY="@reboot sleep 35 && /usr/bin/bash \"${ROOT_DIR}/boot.sh\" > /tmp/aidh_boot.log 2>&1  ${MARKER}"

mode="install"
case "${1:-}" in
  --remove) mode="remove" ;;
  --status) mode="status" ;;
  --run-now) mode="run-now" ;;
  -h|--help) sed -n '2,15p' "${BASH_SOURCE[0]}"; exit 0 ;;
esac

case "$mode" in
  install)
    if crontab -l 2>/dev/null | grep -qF "AIDataHub/boot.sh"; then
      echo "[OK] @reboot 이미 등록되어 있음."
    else
      ( crontab -l 2>/dev/null; echo "$ENTRY" ) | crontab -
      echo "[OK] @reboot 등록 완료."
    fi
    echo
    echo "확인: crontab -l | grep AIDataHub"
    echo "로그: tail -f /tmp/aidh_boot.log"
    echo "제거: bash ${ROOT_DIR}/autostart-install.sh --remove"
    ;;

  remove)
    if crontab -l 2>/dev/null | grep -qF "AIDataHub/boot.sh"; then
      crontab -l | grep -v "AIDataHub/boot.sh" | crontab -
      echo "[OK] @reboot 등록 제거 완료."
    else
      echo "[OK] 이미 등록되어 있지 않음."
    fi
    ;;

  status)
    echo "── crontab @reboot ─────────────────────────────"
    crontab -l 2>/dev/null | grep -E "AIDataHub|@reboot" || echo "(없음)"
    echo
    echo "── 서버 상태 ──────────────────────────────────"
    if curl -fsS -o /dev/null --max-time 3 http://127.0.0.1:8001/api/system/health 2>/dev/null; then
      echo "[OK] API 응답 정상 (port 8001)"
    else
      echo "[X] API 응답 없음 — bash boot.sh 또는 --run-now"
    fi
    echo
    echo "── 마지막 부팅 로그 ─────────────────────────────"
    if [[ -f /tmp/aidh_boot.log ]]; then
      tail -20 /tmp/aidh_boot.log
    else
      echo "(/tmp/aidh_boot.log 없음 — 아직 부팅 자동 기동 실행 안 됨)"
    fi
    ;;

  run-now)
    echo "→ boot.sh 즉시 실행 (테스트)"
    exec bash "${ROOT_DIR}/boot.sh"
    ;;
esac
