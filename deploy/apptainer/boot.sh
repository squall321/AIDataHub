#!/usr/bin/env bash
# AI Data Hub — 재부팅 후 한 줄 복구 스크립트.
#
# 서버가 reboot 되면 apptainer instance + uvicorn 둘 다 사라진다.
# 데이터는 보존돼 있으므로 이 스크립트만 실행하면 같은 상태로 복구.
#
# start_postgres.sh + start_api.sh 와 다른 점:
#   - 재부팅 후 남아있는 stale .pid / orphan state 자동 정리
#   - port 점유 검사 (혹시 다른 서비스가 점유했다면 명시 에러)
#   - 검증 단계 포함 (curl health 200 확인)
#
# 사용:
#   bash deploy/apptainer/boot.sh
#   bash deploy/apptainer/boot.sh --skip-api   # postgres만 (디버깅용)
#   bash deploy/apptainer/boot.sh --auto-sudo  # 신규 머신: AppArmor/subuid/linger 자동 셋업
set -euo pipefail

SKIP_API=0
FORCE=0
for arg in "$@"; do
  case "$arg" in
    --skip-api)  SKIP_API=1 ;;
    --force)     FORCE=1 ;;
    --auto-sudo) export AIDH_AUTO_SUDO=1 ;;
  esac
done

# shellcheck source=/dev/null
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
load_env

# systemd 가 관리 중이면 직접 호출 차단 (이중 기동 방지).
# systemd unit 의 ExecStart 가 이 스크립트를 부르므로 그 경로는 무관 (PPID 검사로 구분).
if [[ $FORCE -eq 0 ]]; then
  PARENT_CMD=$(ps -o comm= -p "$PPID" 2>/dev/null || echo "")
  if [[ "$PARENT_CMD" != "systemd" ]]; then
    # 사용자가 직접 실행한 경우 — systemd 등록 여부 검사
    if systemctl --user is-active aidh.service >/dev/null 2>&1; then
      echo "[WARN] systemd (--user) 가 aidh.service 를 이미 관리 중"
      echo "       boot.sh 직접 호출 대신:"
      echo "         systemctl --user restart aidh.service"
      echo "       강제로 직접 실행하려면: bash boot.sh --force"
      exit 1
    elif systemctl is-active aidh.service >/dev/null 2>&1; then
      echo "[WARN] systemd (system) 가 aidh.service 를 이미 관리 중"
      echo "       sudo systemctl restart aidh.service"
      echo "       강제로: bash boot.sh --force"
      exit 1
    fi
  fi
fi

echo "================================================================"
echo " AI Data Hub — boot (after reboot recovery)"
echo " $(date '+%F %T %Z')"
echo "================================================================"

# ── 1. stale 정리 ────────────────────────────────────────────────
echo "[1/4] stale pid / orphan state 정리"

# 1a. API pid file — 프로세스 없으면 그냥 파일만 지움
if [[ -f "$LOG_DIR/api.pid" ]]; then
  PID=$(cat "$LOG_DIR/api.pid" 2>/dev/null || echo "")
  if [[ -n "$PID" ]] && ! kill -0 "$PID" 2>/dev/null; then
    rm -f "$LOG_DIR/api.pid"
    echo "  · stale api.pid ($PID) 제거"
  fi
fi

# 1b. apptainer instance state — 재부팅 후 .json 만 남아있을 수 있음
APPT_STATE="$HOME/.apptainer/instances"
if [[ -d "$APPT_STATE" ]]; then
  # 인스턴스가 list 에는 없는데 state json 만 남은 경우 정리
  if ! apptainer instance list 2>/dev/null | awk 'NR>1{print $1}' | grep -qx "$INST_POSTGRES"; then
    ORPHAN=$(find "$APPT_STATE" -name "${INST_POSTGRES}.json" 2>/dev/null | head -3)
    if [[ -n "$ORPHAN" ]]; then
      for f in $ORPHAN; do
        rm -f "$f" 2>/dev/null && echo "  · orphan state 제거: $f"
      done
    fi
  fi
fi

echo "  ✓ 정리 완료"

# ── 2. 포트 검사 ──────────────────────────────────────────────────
echo "[2/4] 포트 가용성 확인"
for p in "$POSTGRES_PORT" "$API_PORT"; do
  if ss -tnl 2>/dev/null | awk '{print $4}' | grep -qE "[:.]${p}\$"; then
    PROC=$(ss -tlnp 2>/dev/null | grep -E "[:.]${p}\$" | head -1)
    echo "  ✗ port $p 이미 사용 중:"
    echo "      $PROC"
    echo
    echo "    재부팅 전에 다른 서비스가 같은 포트를 잡았을 수 있습니다."
    echo "    lsof -i :${p}    # 점유자 확인"
    echo "    또는 .env 의 PORT 변경 후 다시 실행"
    exit 1
  fi
done
echo "  ✓ port $POSTGRES_PORT / $API_PORT 가용"

# ── 3. postgres 기동 ──────────────────────────────────────────────
echo "[3/4] postgres 기동"
bash "$APPT_DIR/start_postgres.sh"

# ── 4. API 기동 + 검증 ────────────────────────────────────────────
if [[ $SKIP_API -eq 1 ]]; then
  echo "[4/4] --skip-api 지정 — API 건너뜀"
  echo
  echo "✓ boot 완료 (postgres 만)"
  exit 0
fi

echo "[4/4] API 기동"
bash "$APPT_DIR/start_api.sh"

# 헬스체크 대기 — alembic + 의존 로딩 시간 고려해 기본 120s 까지 허용.
# 환경변수 AIDH_BOOT_HEALTH_TIMEOUT (초) 로 조정 가능.
HEALTH_TIMEOUT="${AIDH_BOOT_HEALTH_TIMEOUT:-120}"
echo "  · API health 대기 (최대 ${HEALTH_TIMEOUT}초)..."
OK=0
for i in $(seq 1 "$HEALTH_TIMEOUT"); do
  if curl -s --max-time 2 "http://127.0.0.1:${API_PORT}/api/system/health" >/dev/null 2>&1; then
    echo "  ✓ api health 200 OK (${i}s)"
    OK=1; break
  fi
  sleep 1
done

if [[ $OK -eq 0 ]]; then
  echo "  ✗ API 응답 없음 (${HEALTH_TIMEOUT}초 timeout)"
  echo "    alembic 마이그레이션이 길거나 부팅 실패 가능. 늘리려면 AIDH_BOOT_HEALTH_TIMEOUT=300 bash boot.sh"
  echo "    로그: tail -30 $LOG_DIR/api.log"
  echo "    또는 bash deploy/apptainer/diag.sh --tail-logs"
  exit 1
fi

# ── 최종 ────────────────────────────────────────────────────────
echo
HOST_IP=$(grep '^HOST_IP=' "$APPT_DIR/.env" 2>/dev/null | cut -d= -f2 || echo "127.0.0.1")
echo "================================================================"
echo "✓ boot 완료 — 정상 동작 중"
echo "================================================================"
echo "  Dashboard:  http://${HOST_IP}:${API_PORT}/dashboard/"
echo "  API:        http://${HOST_IP}:${API_PORT}/api/system/health"
echo "  MCP:        http://${HOST_IP}:${API_PORT}/mcp/"
echo
echo "상태 확인:  bash deploy/apptainer/status.sh"
