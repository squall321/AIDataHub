#!/usr/bin/env bash
# AI Data Hub — Apptainer 스크립트 공용 라이브러리
# - .env 로드
# - 프록시 변수 export (대/소문자 모두)
# - 사전 검증 함수
#
# 사용: 각 스크립트 상단에서 `source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"`

# 호출 스크립트가 set -euo pipefail 을 켰어도 source 자체는 안전.
APPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$APPT_DIR/../.." && pwd)"
API_DIR="$ROOT_DIR/api_server"
DATA_DIR="$APPT_DIR/data"
LOG_DIR="$APPT_DIR/logs"

# ── 호스트 IP 자동 감지 ──────────────────────────────────────────────────
# HOST_IP placeholder 치환에 사용 (install.sh 또는 .env 수정 시).
# 1순위: ifconfig.me (인터넷 가능 시 — public IP)
# 2순위: hostname -I 첫 번째 (사내망 / LAN IP)
# 3순위: 127.0.0.1
detect_host_ip() {
  local ip
  ip=$(timeout 3 curl -s ifconfig.me 2>/dev/null || true)
  if [[ -z "$ip" || ! "$ip" =~ ^[0-9.]+$ ]]; then
    ip=$(hostname -I 2>/dev/null | awk '{print $1}')
  fi
  if [[ -z "$ip" || ! "$ip" =~ ^[0-9.]+$ ]]; then
    ip="127.0.0.1"
  fi
  echo "$ip"
}

# ── .env 로드 ────────────────────────────────────────────────────────────
load_env() {
  local env_file="$APPT_DIR/.env"
  if [[ ! -f "$env_file" ]]; then
    if [[ -f "$APPT_DIR/.env.example" ]]; then
      cp "$APPT_DIR/.env.example" "$env_file"
      echo "[INFO] .env 자동 생성 (.env.example 복사) — 필요 시 수정 후 재실행"
    else
      echo "[ERROR] .env / .env.example 둘 다 없음" >&2
      exit 1
    fi
  fi
  set -a
  # shellcheck disable=SC1090
  . "$env_file"
  set +a

  # v0.14 — INST_PREFIX 도입: 같은 서버에 여러 AIDH 인스턴스 공존 가능.
  # .env 의 ``APP_NAME`` (예: aidh, app2) 가 instance 이름 prefix 결정.
  # ``INST_POSTGRES`` 가 .env 에 명시돼 있으면 그것 우선 (하위 호환).
  : "${APP_NAME:=aidh}"
  INST_PREFIX="$APP_NAME"
  if [[ -z "${INST_POSTGRES:-}" ]]; then
    INST_POSTGRES="${INST_PREFIX}_postgres"
  fi
  export APP_NAME INST_PREFIX INST_POSTGRES
}

# ── 사내 표준 프록시 (하드코딩 폴백) ─────────────────────────────────────
# .env 의 HTTPS_PROXY / BUILD_PROXY 가 모두 비어 있을 때 이 값이 적용된다.
# 외부 환경(사내망 밖)에서 셋업할 때는 .env 에 ``BUILD_PROXY_HTTPS=off`` 로
# 명시적 opt-out 가능.
DEFAULT_FALLBACK_PROXY="http://168.219.61.252:8080"

# ── 프록시 export (소문자/대문자 양쪽, no_proxy 에 localhost/127.0.0.1 자동 포함) ──
# v0.13.0 — 우선순위 (위에서 아래로):
#   1) HTTPS_PROXY/HTTP_PROXY    (사용자가 .env 에서 명시)
#   2) BUILD_PROXY_HTTPS/HTTP    (사용자가 .env 에서 명시)
#   3) DEFAULT_FALLBACK_PROXY    (위 사내 표준 — 둘 다 비어 있을 때)
# 어떤 단계에서든 결과 프록시가 정해지면 pip / npm / huggingface / apptainer
# 모두 동일 프록시 환경변수를 본다 (안쪽 도구에 자동 전파).
# Opt-out: .env 에 ``BUILD_PROXY_HTTPS=off`` 설정 시 fallback 비활성.
export_proxy() {
  local hp="${HTTPS_PROXY:-${https_proxy:-}}"
  local hpp="${HTTP_PROXY:-${http_proxy:-}}"
  local np="${NO_PROXY:-${no_proxy:-}}"

  # 2) BUILD_PROXY 폴오버.
  if [[ -z "$hp" && -n "${BUILD_PROXY_HTTPS:-}" && "${BUILD_PROXY_HTTPS:-}" != "off" ]]; then
    hp="$BUILD_PROXY_HTTPS"
  fi
  if [[ -z "$hpp" ]]; then
    local cand="${BUILD_PROXY_HTTP:-${BUILD_PROXY_HTTPS:-}}"
    if [[ -n "$cand" && "$cand" != "off" ]]; then
      hpp="$cand"
    fi
  fi

  # 3) 하드코딩 사내 표준 폴백 — opt-out 은 BUILD_PROXY_HTTPS=off.
  if [[ "${BUILD_PROXY_HTTPS:-}" != "off" ]]; then
    if [[ -z "$hp" ]]; then
      hp="$DEFAULT_FALLBACK_PROXY"
      echo "[INFO] HTTPS_PROXY 미설정 — DEFAULT_FALLBACK_PROXY 적용 ($DEFAULT_FALLBACK_PROXY)"
    fi
    if [[ -z "$hpp" ]]; then
      hpp="$DEFAULT_FALLBACK_PROXY"
    fi
  fi

  # localhost / 127.0.0.1 은 항상 프록시 우회
  local extra="localhost,127.0.0.1,::1"
  if [[ -z "$np" ]]; then
    np="$extra"
  elif [[ ",$np," != *",localhost,"* ]]; then
    np="$np,$extra"
  fi

  if [[ -n "$hp" || -n "$hpp" ]]; then
    export HTTPS_PROXY="$hp"  https_proxy="$hp"
    export HTTP_PROXY="$hpp"  http_proxy="$hpp"
    export NO_PROXY="$np"     no_proxy="$np"
    echo "[INFO] proxy: https=$HTTPS_PROXY http=$HTTP_PROXY no=$NO_PROXY"
  fi
}

# ── 사전 검증 ────────────────────────────────────────────────────────────
require_apptainer() {
  if ! command -v apptainer >/dev/null 2>&1; then
    echo "[ERROR] apptainer 미설치. Ubuntu 24.04 기준:" >&2
    echo "        sudo add-apt-repository -y ppa:apptainer/ppa && sudo apt update && sudo apt install -y apptainer" >&2
    exit 1
  fi
  local ver
  ver="$(apptainer --version 2>&1 | awk '{print $NF}')"
  echo "[OK] apptainer $ver"
}

require_node() {
  if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
    echo "[ERROR] node/npm 미설치. Ubuntu 24.04:" >&2
    echo "        curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -" >&2
    echo "        sudo apt install -y nodejs" >&2
    exit 1
  fi
  echo "[OK] node $(node --version) / npm $(npm --version)"
}

require_python_venv() {
  # Python 3.12 우선, 없으면 3 그대로
  PYBIN="python3"
  if command -v python3.12 >/dev/null 2>&1; then
    PYBIN="python3.12"
  fi
  if ! command -v "$PYBIN" >/dev/null 2>&1; then
    echo "[ERROR] python3 미설치. Ubuntu 24.04: sudo apt install -y python3.12 python3.12-venv" >&2
    exit 1
  fi
  # venv 모듈 확인 (Ubuntu 24.04 는 python3-venv 패키지 별도)
  if ! "$PYBIN" -c "import venv" >/dev/null 2>&1; then
    echo "[ERROR] python venv 모듈 없음. sudo apt install -y python3-venv (또는 python3.12-venv)" >&2
    exit 1
  fi
  echo "[OK] $($PYBIN --version) + venv"
}

require_disk() {
  # 최소 5GB (SIF + venv + pg data 여유)
  local need_kb=$((5 * 1024 * 1024))
  local avail_kb
  avail_kb=$(df -k "$APPT_DIR" | awk 'NR==2 {print $4}')
  if [[ -z "$avail_kb" || "$avail_kb" -lt "$need_kb" ]]; then
    echo "[ERROR] 디스크 여유 부족 (필요 ~5GB, 현재 $((avail_kb/1024))MB)" >&2
    exit 1
  fi
  echo "[OK] 디스크 여유 $((avail_kb/1024/1024))GB"
}

require_port_free() {
  local port="$1" name="$2"
  if ss -tnl 2>/dev/null | awk '{print $4}' | grep -qE "[:.]${port}\$"; then
    echo "[ERROR] $name 포트 ${port} 이미 사용 중. .env 에서 ${name}_PORT 변경하라." >&2
    exit 1
  fi
  echo "[OK] port ${port} (${name}) 가용"
}

instance_running() {
  apptainer instance list 2>/dev/null | awk 'NR>1 {print $1}' | grep -qx "$1"
}

ensure_dirs() {
  mkdir -p "$DATA_DIR/postgres" "$DATA_DIR/postgres-run" \
           "$DATA_DIR/attachments" "$DATA_DIR/figures" "$LOG_DIR"
}
