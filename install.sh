#!/usr/bin/env bash
# AI Data Hub — 번들 풀린 디렉토리에서 한 줄 설치 스크립트.
#
# 가정: bundle.sh 가 만든 tar.gz 를 extract 한 디렉토리 안에서 실행.
# 이 스크립트가 자동으로:
#   1. 사전 요구 검사 (apptainer / python / node) — 없으면 bootstrap 안내
#   2. .env 가 없으면 .env.example 복사 + HOST_IP / APP_NAME placeholder 자동 치환
#   3. SIF 가 번들에 포함돼 있는지 / 빌드 필요한지 판단
#   4. fresh start (start_postgres + alembic + start_api) — 옵션 --skip-* 지원
#   5. .vsix 가 있으면 code 명령으로 설치 시도 (있을 때만)
#
# 사용:
#   bash install.sh                    # 기본 (전체)
#   bash install.sh --skip-api         # PG 만
#   bash install.sh --skip-extension   # .vsix 자동 설치 skip
#   bash install.sh --force            # SIF 있어도 강제 재빌드
#   bash install.sh --host-ip 10.0.0.5 # HOST_IP 수동 지정 (자동 감지 우회)
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APPT_DIR="$ROOT_DIR/deploy/apptainer"

SKIP_API=0
SKIP_EXT=0
FORCE=0
MANUAL_IP=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-api)        SKIP_API=1; shift ;;
    --skip-extension)  SKIP_EXT=1; shift ;;
    --force)           FORCE=1; shift ;;
    --host-ip)         MANUAL_IP="$2"; shift 2 ;;
    --host-ip=*)       MANUAL_IP="${1#*=}"; shift ;;
    -h|--help)         sed -n '2,18p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "[ERROR] unknown arg: $1" >&2; exit 2 ;;
  esac
done

echo "================================================================"
echo " AI Data Hub — install (bundle target)"
echo "================================================================"

# ── 1. 사전 요구 ──────────────────────────────────────────────────
echo "[1/5] 사전 요구 검사"
MISSING=()
command -v python3   >/dev/null 2>&1 || MISSING+=("python3")
command -v curl      >/dev/null 2>&1 || MISSING+=("curl")
command -v dpkg-deb  >/dev/null 2>&1 || MISSING+=("dpkg(dpkg-deb)")
if [[ ${#MISSING[@]} -gt 0 ]]; then
  echo "[ERROR] 누락 패키지: ${MISSING[*]}"
  echo "        Ubuntu 24.04: sudo apt update && sudo apt install -y python3.12 python3.12-venv curl dpkg"
  exit 1
fi
# apptainer 는 시스템 설치를 요구하지 않는다 — 프로젝트 내부 핀버전(.tools)을
# 보장하고, 이후 모든 호출은 _common.sh 의 apptainer() 함수가 라우팅한다.
# (번들 target 은 deploy/apptainer/cache/ 에 staged .deb 동봉 → offline OK.)
bash "$APPT_DIR/install-apptainer.sh" || {
  echo "[ERROR] 핀 apptainer 설치 실패 — deploy/apptainer/cache/ 에 .deb 동봉 확인" >&2
  exit 1
}
# shellcheck source=/dev/null
source "$APPT_DIR/_common.sh"
echo "  ✓ apptainer $(apptainer --version 2>&1 | awk '{print $NF}')  (${_AIDH_APPT_SRC})"
echo "  ✓ $(python3 --version)"

# ── 2. .env 생성 + placeholder 치환 ─────────────────────────────────
echo "[2/5] .env 셋업"
ENV_FILE="$APPT_DIR/.env"
ENV_EXAMPLE="$APPT_DIR/.env.example"
if [[ -f "$ENV_FILE" ]]; then
  echo "  · 기존 .env 발견 — 그대로 사용 (placeholder 치환 skip)"
else
  if [[ ! -f "$ENV_EXAMPLE" ]]; then
    echo "[ERROR] .env.example 도 없습니다 — 번들 손상?" >&2
    exit 1
  fi
  cp "$ENV_EXAMPLE" "$ENV_FILE"
  echo "  · .env.example → .env 복사"

  # HOST_IP 감지/치환
  if [[ -n "$MANUAL_IP" ]]; then
    HOST_IP="$MANUAL_IP"
    echo "  · HOST_IP 수동 지정: $HOST_IP"
  else
    HOST_IP=$(timeout 3 curl -s ifconfig.me 2>/dev/null || true)
    if [[ -z "$HOST_IP" || ! "$HOST_IP" =~ ^[0-9.]+$ ]]; then
      HOST_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
    fi
    if [[ -z "$HOST_IP" || ! "$HOST_IP" =~ ^[0-9.]+$ ]]; then
      HOST_IP="127.0.0.1"
    fi
    echo "  · HOST_IP 자동 감지: $HOST_IP"
  fi
  # placeholder literal 'HOST_IP' 만 치환 (값 HOST_IP 인 줄 자체)
  sed -i "s|^HOST_IP=HOST_IP$|HOST_IP=$HOST_IP|" "$ENV_FILE"

  # CHANGE_ME 경고
  if grep -q "CHANGE_ME\|aidh_change_me" "$ENV_FILE"; then
    echo "  ⚠ 기본 비밀번호 (aidh_change_me) 사용 중 — 운영 전 변경 권장"
    echo "    nano $ENV_FILE   # POSTGRES_PASSWORD 줄 수정"
  fi
fi

# ── 3. SIF 확인 / 빌드 ──────────────────────────────────────────────
echo "[3/5] Apptainer SIF"
if [[ -f "$APPT_DIR/postgres-base.sif" && -f "$APPT_DIR/postgres.sif" && $FORCE -eq 0 ]]; then
  SIZE=$(du -h "$APPT_DIR"/*.sif 2>/dev/null | awk '{print $1}' | paste -sd+ | bc 2>/dev/null || echo "?")
  echo "  ✓ SIF 사전 빌드본 발견 (번들 포함) — 빌드 skip"
else
  echo "  · SIF 없음 또는 --force — bash build.sh 실행"
  bash "$APPT_DIR/build.sh" $([[ $FORCE -eq 1 ]] && echo "--force")
fi

# ── 4. fresh start (PG + alembic + API) ──────────────────────────────
echo "[4/5] 기동"
bash "$APPT_DIR/start_postgres.sh"
if [[ $SKIP_API -eq 0 ]]; then
  bash "$APPT_DIR/start_api.sh"
else
  echo "  · --skip-api — API 기동 건너뜀"
fi

# ── 5. VSCode 확장 자동 설치 (있을 때만) ────────────────────────────
echo "[5/5] VSCode 확장 (선택)"
if [[ $SKIP_EXT -eq 1 ]]; then
  echo "  · --skip-extension — 건너뜀"
else
  VSIX=$(ls "$ROOT_DIR/vscode_extension"/*.vsix 2>/dev/null | head -1 || true)
  if [[ -z "$VSIX" ]]; then
    echo "  · .vsix 없음 — 번들에 미포함 또는 별도 전송"
  elif ! command -v code >/dev/null 2>&1; then
    echo "  · code (VSCode) CLI 없음 — 수동 설치 필요"
    echo "    VSIX 위치: $VSIX"
  else
    echo "  · code --install-extension $(basename "$VSIX") --force"
    code --install-extension "$VSIX" --force 2>&1 | tail -3 || true
  fi
fi

# ── 결과 + 검증 안내 ───────────────────────────────────────────────
echo
echo "================================================================"
echo "✓ install 완료"
echo "================================================================"
echo
echo "검증:"
echo "  bash deploy/apptainer/diag.sh"
echo
echo "로그:"
echo "  tail -f deploy/apptainer/logs/api.log"
echo
echo "엔드포인트:"
HOST_IP_FROM_ENV=$(grep '^HOST_IP=' "$ENV_FILE" 2>/dev/null | cut -d= -f2 || echo "127.0.0.1")
API_PORT_FROM_ENV=$(grep '^API_PORT=' "$ENV_FILE" 2>/dev/null | cut -d= -f2 || echo "8001")
echo "  Dashboard:  http://${HOST_IP_FROM_ENV}:${API_PORT_FROM_ENV}/dashboard/"
echo "  API health: http://${HOST_IP_FROM_ENV}:${API_PORT_FROM_ENV}/api/system/health"
echo "  MCP server: http://${HOST_IP_FROM_ENV}:${API_PORT_FROM_ENV}/mcp/"
