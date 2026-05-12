#!/usr/bin/env bash
# AI Data Hub — sudo 흔적 회복 (Issue #1).
#
# 증상:
#   - Permission denied 가 갑자기 등장 (인스턴스 기동 / DB write / pip log)
#   - root 소유 파일들이 data/ 또는 ~/.apptainer 안에 섞여 있음
#
# 원인:
#   - 한 번이라도 ``sudo apptainer instance start ...`` 같이 실행했을 때
#     컨테이너 프로세스가 root 로 동작 → 그 프로세스가 만든 host bind 파일들이
#     root 소유로 남음 → 다음에 일반 사용자가 못 씀.
#   - Apptainer 는 rootless 가 정상. apt-get install apptainer 외에는 sudo 금지.
#
# 사용:
#   bash deploy/apptainer/desudo.sh             # 미리보기 (dry-run)
#   bash deploy/apptainer/desudo.sh --yes       # 실제 실행
#   bash deploy/apptainer/desudo.sh --yes --hard  # /root/.apptainer 까지 정리
set -euo pipefail
# shellcheck source=/dev/null
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
load_env

DRY=1
HARD=0
for arg in "$@"; do
  case "$arg" in
    --yes|-y)   DRY=0 ;;
    --hard)     HARD=1 ;;
    *)          ;;
  esac
done

ME="$(id -un)"
ME_UID="$(id -u)"
ME_GID="$(id -g)"

echo "================================================================"
echo " AI Data Hub — desudo (recover from accidental sudo)"
echo " user=$ME uid=$ME_UID gid=$ME_GID  mode=$([[ $DRY -eq 1 ]] && echo dry-run || echo APPLY)"
echo "================================================================"

run_or_show() {
  if [[ $DRY -eq 1 ]]; then
    echo "[DRY] $*"
  else
    echo "[RUN] $*"
    eval "$@"
  fi
}

# 1) 실행 중인 instance 가 있으면 먼저 정리 안내.
if instance_running "$INST_POSTGRES"; then
  echo "[WARN] $INST_POSTGRES 인스턴스가 실행 중입니다."
  echo "       먼저 stop 후 desudo 권장: bash deploy/apptainer/stop.sh"
  echo "       (강제 진행해도 동작하지만 chown 충돌 가능)"
fi

# 2) data/ 디렉토리 chown — postgres / attachments / figures / logs 등
if [[ -d "$DATA_DIR" ]]; then
  ROOT_OWNED=$(find "$DATA_DIR" -not -user "$ME" -print 2>/dev/null | head -5 || true)
  if [[ -n "$ROOT_OWNED" ]]; then
    echo "[INFO] $DATA_DIR 내 다른 사용자 소유 파일 발견 (top 5):"
    printf '  %s\n' $ROOT_OWNED
    run_or_show "find '$DATA_DIR' -not -user '$ME' -exec chown -h '$ME':'$ME' {} +"
  else
    echo "[OK]   $DATA_DIR — 소유권 정상"
  fi
fi

# 3) ~/.apptainer/instances 정리 — 본인 소유가 아닌 state 파일 발견 시 백업 후 삭제
HOME_APPT="$HOME/.apptainer"
if [[ -d "$HOME_APPT" ]]; then
  STRANGE=$(find "$HOME_APPT" -not -user "$ME" -print 2>/dev/null | head -5 || true)
  if [[ -n "$STRANGE" ]]; then
    echo "[INFO] $HOME_APPT 내 비-본인 소유 파일 발견:"
    printf '  %s\n' $STRANGE
    # backup before nuke (state 파일이라 작음)
    BACKUP="$HOME_APPT.desudo.$(date +%s).bak"
    run_or_show "cp -a '$HOME_APPT' '$BACKUP'"
    run_or_show "find '$HOME_APPT' -not -user '$ME' -exec chown -h '$ME':'$ME' {} +"
    echo "       (백업: $BACKUP)"
  else
    echo "[OK]   $HOME_APPT — 소유권 정상"
  fi
fi

# 4) /root/.apptainer — sudo 로 띄운 instance 의 잔여 state
if [[ $HARD -eq 1 ]]; then
  if [[ -d /root/.apptainer ]] && command -v sudo >/dev/null 2>&1; then
    echo "[HARD] /root/.apptainer 정리 (sudo 필요)"
    run_or_show "sudo rm -rf /root/.apptainer/instances/*"
  fi
fi

# 5) 로그 디렉토리 정리 — root 소유 로그가 섞이면 다음 기동 시 append 실패.
if [[ -d "$LOG_DIR" ]]; then
  ROOT_LOGS=$(find "$LOG_DIR" -not -user "$ME" -print 2>/dev/null | head -3 || true)
  if [[ -n "$ROOT_LOGS" ]]; then
    echo "[INFO] $LOG_DIR 내 비-본인 소유 로그:"
    printf '  %s\n' $ROOT_LOGS
    run_or_show "find '$LOG_DIR' -not -user '$ME' -exec chown '$ME':'$ME' {} +"
  fi
fi

echo
if [[ $DRY -eq 1 ]]; then
  echo "[NEXT] 실제 적용하려면: bash deploy/apptainer/desudo.sh --yes"
else
  echo "✓ desudo 완료. 이제 일반 사용자로 다시 기동 가능:"
  echo "    bash deploy/apptainer/start_postgres.sh"
fi
