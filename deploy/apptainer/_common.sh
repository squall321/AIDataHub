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

# ── 핀 apptainer 해석 + 자동 프로비저닝 ──────────────────────────────────
# 정책: 시스템에 apptainer 가 어떤 버전이건(또는 없건) 무관하게, 항상
#       프로젝트 로컬(.tools)의 핀버전을 쓴다. 처음 실행이면 알아서
#       프로젝트 내부로 설치(install-apptainer.sh)한 뒤 그걸 사용한다.
#       비대화형 스크립트는 alias 가 안 먹으므로 함수로 `apptainer` 를
#       가로채 모든 호출을 핀버전으로 라우팅한다.
# 우선순위: $AIDH_APPTAINER_BIN > .tools 핀버전 > (자동설치) > 시스템 PATH.
APPTAINER_VERSION="${APPTAINER_VERSION:-1.3.6}"
_PINNED_APPT="$APPT_DIR/.tools/apptainer-${APPTAINER_VERSION}/usr/bin/apptainer"

resolve_apptainer() {
  # 단순 -x 가 아니라 실제 실행(--version) 되는지까지 본다. 깨진 .tools
  # (부분추출/arch불일치)를 고르면 instance start 가 죽어 서버가 안 뜬다
  # → 그 경우 무시하고 시스템 apptainer 로 폴백(기존 동작 유지·자가복구).
  if [[ -n "${AIDH_APPTAINER_BIN:-}" ]] && command "${AIDH_APPTAINER_BIN}" --version >/dev/null 2>&1; then
    _AIDH_APPT="$AIDH_APPTAINER_BIN"; _AIDH_APPT_SRC="env(AIDH_APPTAINER_BIN)"
  elif [[ -x "$_PINNED_APPT" ]] && command "$_PINNED_APPT" --version >/dev/null 2>&1; then
    _AIDH_APPT="$_PINNED_APPT"; _AIDH_APPT_SRC="pinned .tools v${APPTAINER_VERSION}"
  else
    _AIDH_APPT="$(command -v apptainer 2>/dev/null || echo apptainer)"
    if [[ -x "$_PINNED_APPT" ]]; then
      _AIDH_APPT_SRC="system PATH (핀 .tools 깨짐 — 폴백)"
    else
      _AIDH_APPT_SRC="system PATH (핀버전 미설치)"
    fi
  fi
  export _AIDH_APPT _AIDH_APPT_SRC APPTAINER_VERSION
}

# 핀버전이 없으면 프로젝트 내부로 자동 설치 후 재해석. (한 프로세스트리에서
# 1회만 시도 — 무한루프/반복다운로드 방지. AIDH_APPTAINER_AUTOINSTALL=0 로 비활성.)
ensure_apptainer() {
  resolve_apptainer
  [[ -x "$_PINNED_APPT" ]] && return 0
  [[ "${AIDH_APPTAINER_AUTOINSTALL:-1}" == "1" ]] || { resolve_apptainer; return 0; }
  [[ -n "${_AIDH_APPT_AUTOTRIED:-}" ]] && { resolve_apptainer; return 0; }
  export _AIDH_APPT_AUTOTRIED=1
  echo "[INFO] 핀 apptainer v${APPTAINER_VERSION} 미설치 — 프로젝트 내부로 자동 설치 시도" >&2
  if bash "$APPT_DIR/install-apptainer.sh" >&2; then
    resolve_apptainer
  else
    echo "[WARN] 핀 apptainer 자동설치 실패 — 시스템 apptainer 로 폴백 시도." >&2
    echo "       오프라인이면 deploy/apptainer/cache/ 에 apptainer_${APPTAINER_VERSION}_*.deb 두고 재실행." >&2
    resolve_apptainer
  fi
}

resolve_apptainer
# 모든 소싱 스크립트의 `apptainer ...` 호출을 핀버전으로 라우팅.
apptainer() { command "$_AIDH_APPT" "$@"; }
export -f apptainer 2>/dev/null || true

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
  # 부팅 직후 / SSH 환경에서 흔한 dbus/AppArmor 문제 자동 처리
  _aidh_runtime_autotune
  # 핀버전이 없으면 프로젝트 내부로 자동 설치 후 그걸 쓴다 (알아서 됨).
  ensure_apptainer
  if ! command "$_AIDH_APPT" --version >/dev/null 2>&1; then
    echo "[ERROR] apptainer 실행 불가 ($_AIDH_APPT — $_AIDH_APPT_SRC)." >&2
    echo "        자동설치도 실패 — 오프라인이면 .deb 반입 후 재실행:" >&2
    echo "          deploy/apptainer/cache/apptainer_${APPTAINER_VERSION}_<arch>.deb" >&2
    echo "          bash deploy/apptainer/install-apptainer.sh" >&2
    exit 1
  fi
  local ver
  ver="$(command "$_AIDH_APPT" --version 2>&1 | awk '{print $NF}')"
  echo "[OK] apptainer $ver  ($_AIDH_APPT_SRC)"
  if [[ "$_AIDH_APPT_SRC" == system* ]]; then
    echo "[WARN] 핀버전(v${APPTAINER_VERSION}) 자동설치 실패 → 시스템 apptainer 폴백." >&2
    echo "       권장: 네트워크/프록시 확인 후 또는 .deb 반입 후 재실행." >&2
  fi
}

# ── rootless apptainer 런타임 자동 튜닝 ─────────────────────────────────
# 부팅 직후 / SSH 접속 환경에서 흔한 3가지 문제 자동 처리:
#   1. XDG_RUNTIME_DIR / DBUS_SESSION_BUS_ADDRESS 미설정
#      → /run/user/$UID 가 있으면 자동 export
#   2. AppArmor unprivileged_userns_restriction=1 (Ubuntu 24.04 default)
#      → 사용자에게 영구 해제 명령 안내 (sudo 권한 필요 → 자동 수정 X)
#   3. dbus user session 없음 → instance start 실패 시 호출자가
#      AIDH_APPT_NO_CGROUPS=1 폴백을 자동 시도
_aidh_runtime_autotune() {
  # 1. dbus 환경변수 자동 설정
  local uid
  uid="$(id -u)"
  if [[ -z "${XDG_RUNTIME_DIR:-}" && -d "/run/user/$uid" ]]; then
    export XDG_RUNTIME_DIR="/run/user/$uid"
  fi
  if [[ -z "${DBUS_SESSION_BUS_ADDRESS:-}" && -S "/run/user/$uid/bus" ]]; then
    export DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$uid/bus"
  fi

  # 2. AppArmor unprivileged_userns 차단 감지 + 안내
  if [[ -r /proc/sys/kernel/apparmor_restrict_unprivileged_userns ]]; then
    local val
    val="$(cat /proc/sys/kernel/apparmor_restrict_unprivileged_userns 2>/dev/null || echo 0)"
    if [[ "$val" = "1" && -z "${AIDH_USERNS_WARNED:-}" ]]; then
      export AIDH_USERNS_WARNED=1
      echo "[INFO] AppArmor 'unprivileged_userns_restriction=1' — user namespace 차단 상태." >&2
      echo "       apptainer 가 실패하면 영구 해제 필요 (sudo 1회):" >&2
      echo "         echo 'kernel.apparmor_restrict_unprivileged_userns=0' | \\" >&2
      echo "           sudo tee /etc/sysctl.d/60-apptainer-userns.conf >/dev/null" >&2
      echo "         sudo sysctl --system" >&2
    fi
  fi
}

# instance start fast-fail 폴백 — dbus / cgroup / fakeroot 자동 재시도.
# 사용:
#   _aidh_appt_instance_start_with_fallback INST_NAME LOG_PATH "$APPT_SIF" -- \
#       --bind ... --env ... ...
# 반환: instance_running 검증까지 끝낸 후 0 = OK, 1 = 모든 폴백 실패.
_aidh_appt_instance_start_with_fallback() {
  local inst_name="$1" log_path="$2" sif_path="$3"; shift 3
  local extra_args=("$@")

  # 1차 — 평소대로
  local attempt=1
  while [[ $attempt -le 3 ]]; do
    local extra_cgroup_opt=()
    case "$attempt" in
      2) extra_cgroup_opt=(--no-cgroups)
         echo "  [폴백 $attempt] --no-cgroups (dbus user session 부재 우회)" >&2
         ;;
      3) # 폴백 마지막: 시스템 apptainer 강제 + --no-cgroups
         local sysappt
         sysappt="$(command -v apptainer 2>/dev/null || true)"
         if [[ -n "$sysappt" && "$sysappt" != "$_AIDH_APPT" ]]; then
           echo "  [폴백 $attempt] 시스템 apptainer $sysappt + --no-cgroups" >&2
           _AIDH_APPT="$sysappt"
           _AIDH_APPT_SRC="system PATH (auto-fallback)"
         else
           echo "  [폴백 $attempt] --no-cgroups 재시도" >&2
         fi
         extra_cgroup_opt=(--no-cgroups)
         ;;
    esac

    command "$_AIDH_APPT" instance start \
      "${extra_args[@]}" \
      "${extra_cgroup_opt[@]}" \
      "$sif_path" "$inst_name" \
      > "$log_path" 2>&1 || true

    sleep 2
    if instance_running "$inst_name"; then
      [[ $attempt -gt 1 ]] && echo "  ✓ 폴백 $attempt 성공" >&2
      return 0
    fi

    # 폴백 결정 — 로그에서 dbus / cgroup / OwnerUID / OperationNotPermitted 키 검출
    if grep -qE "dbus|OwnerUID|cgroup|systemd" "$log_path" 2>/dev/null; then
      attempt=$((attempt + 1))
      continue
    fi
    # 다른 에러는 즉시 실패 (의미 없는 무한 재시도 방지)
    return 1
  done
  return 1
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

# 약한/기본 비밀번호 가드. .env.example 은 POSTGRES_PASSWORD=aidh_change_me
# 로 배포된다. 그대로 운영에 올라가면 DB 기본 비번 노출. 기본은 경고만
# (PoC/내부망 흐름 안 깨게), AIDH_REQUIRE_STRONG_PW=1 이면 차단(운영용).
check_secrets() {
  local pw="${POSTGRES_PASSWORD:-}"
  local weak=0
  case "$pw" in
    ""|aidh_change_me|*CHANGE_ME*|*change_me*|postgres|aidh) weak=1 ;;
  esac
  [[ "$weak" -eq 1 ]] || return 0
  if [[ "${AIDH_REQUIRE_STRONG_PW:-0}" = "1" ]]; then
    echo "[ERROR] POSTGRES_PASSWORD 가 기본/약한 값('$pw') — AIDH_REQUIRE_STRONG_PW=1." >&2
    echo "        .env 의 POSTGRES_PASSWORD 를 강한 값으로 바꾸고 재실행." >&2
    echo "        (이미 기동 중 DB 면 비번 변경 후 재초기화/ALTER USER 필요)" >&2
    exit 1
  fi
  echo "[WARN] POSTGRES_PASSWORD 가 기본/약한 값입니다 ('$pw')."
  echo "       운영 전 .env 에서 강한 값으로 회전하세요. (강제: AIDH_REQUIRE_STRONG_PW=1)"
}

# 로그 size-기반 회전 — 파일이 cap(MB) 초과면 .1 로 1세대 보관 후 새로 시작.
# uvicorn.log 처럼 장기 무재시작 운행 시 무한 증가하는 로그용.
# rotate_log <file> [cap_mb=20]
rotate_log() {
  local f="$1" cap_mb="${2:-20}"
  [[ -f "$f" ]] || return 0
  local sz_mb
  sz_mb=$(( $(stat -c %s "$f" 2>/dev/null || echo 0) / 1024 / 1024 ))
  if [[ "$sz_mb" -ge "$cap_mb" ]]; then
    mv -f "$f" "$f.1" 2>/dev/null || true
    : > "$f"
    echo "[INFO] rotate_log: $(basename "$f") ${sz_mb}MB ≥ ${cap_mb}MB → $(basename "$f").1"
  fi
}
