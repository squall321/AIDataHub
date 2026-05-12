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
}

# ── 프록시 export (소문자/대문자 양쪽, no_proxy 에 localhost/127.0.0.1 자동 포함) ──
export_proxy() {
  local hp="${HTTPS_PROXY:-${https_proxy:-}}"
  local hpp="${HTTP_PROXY:-${http_proxy:-}}"
  local np="${NO_PROXY:-${no_proxy:-}}"

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
