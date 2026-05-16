#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
#  AI Data Hub — Host bootstrap (Ubuntu 24.04 LTS)
#
#  Installs the system-level dependencies that `quickstart.sh` / `setup.sh`
#  assume are present:  apptainer, node 20 (+ npm), python 3.12 (+ venv),
#  git, curl, build-essential.
#
#  (MXWhitePaper 의 scripts/bootstrap-host.sh 패턴을 AIDataHub 에 맞게
#   적응. pnpm / datamodel-code-generator 는 MXWP 전용이라 제외.)
#
#  같은 서버에 MXWhitePaper 를 먼저 셋업한 경우:
#    MXWP bootstrap-host.sh 가 이미 apptainer/node/python 을 깔아두므로
#    이 스크립트는 idempotent 하게 거의 no-op 으로 끝난다 (have_version
#    체크 → 이미 있으면 skip). 그대로 실행해도 안전하다.
#
#  Two modes — auto-detected:
#    ONLINE   → apt / NodeSource / GitHub release .deb 에서 받음
#    OFFLINE  → 사전 staged 패키지 (deploy/apptainer/cache/) 사용
#
#  Ubuntu 24.04 quirks handled:
#    - PPA add-apt-repository 가 사내 프록시 뒤에서 hang  → apptainer 는
#      maintainer GitHub release .deb 로 설치 (PPA 경로 회피)
#    - apt-get update 가 프록시 뒤에서 hang → timeout 래핑, soft-fail
#    - curl 이 사내 방화벽에 RST → fallback 프록시로 자동 재시도
#
#  Idempotent: 모든 도구를 `--version` 으로 검사 후 이미 있으면 skip.
#
#  Usage:
#    sudo bash bootstrap.sh                 # auto-detect online/offline
#    sudo bash bootstrap.sh --offline       # force offline
#    sudo bash bootstrap.sh --online        # force online
#    sudo bash bootstrap.sh --dry-run       # 무엇을 할지만 출력
#    bash bootstrap.sh --help
#
#  Corporate proxy:
#    sudo -E bash bootstrap.sh                          # HTTP(S)_PROXY env 상속
#    sudo bash bootstrap.sh --proxy http://proxy:8080   # 명시
#  Fallback proxy (첫 다운로드 실패 시 자동 재시도):
#    기본 http://168.219.61.252:8080 (= _common.sh DEFAULT_FALLBACK_PROXY).
#    AIDH_FALLBACK_PROXY=http://x:8080 sudo -E bash bootstrap.sh   # 변경
#    AIDH_FALLBACK_PROXY= sudo bash bootstrap.sh                   # 비활성
#
#  완료 후:  일반 계정에서  bash quickstart.sh   (또는 bash setup.sh)
# ─────────────────────────────────────────────────────────────────────
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CACHE_DIR="$REPO_ROOT/deploy/apptainer/cache"
DEB_DIR="$CACHE_DIR/deb"

if [ -t 1 ]; then
  C_RESET=$'\033[0m'; C_BLUE=$'\033[1;34m'; C_GREEN=$'\033[1;32m'
  C_YELLOW=$'\033[1;33m'; C_RED=$'\033[1;31m'; C_DIM=$'\033[2m'
else
  C_RESET=""; C_BLUE=""; C_GREEN=""; C_YELLOW=""; C_RED=""; C_DIM=""
fi
step() { printf "\n${C_BLUE}▶ %s${C_RESET}\n" "$1"; }
ok()   { printf "  ${C_GREEN}✓${C_RESET} %s\n" "$*"; }
warn() { printf "  ${C_YELLOW}!${C_RESET} %s\n" "$*"; }
fail() { printf "  ${C_RED}✗${C_RESET} %s\n" "$*"; exit 1; }
note() { printf "  ${C_DIM}%s${C_RESET}\n" "$*"; }

# ── Args ────────────────────────────────────────────────────────────
MODE="auto"; DRY_RUN=0; PROXY_ARG=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --online)  MODE="online" ;;
    --offline) MODE="offline" ;;
    --dry-run) DRY_RUN=1 ;;
    --proxy)   [ -n "${2:-}" ] || fail "--proxy requires a URL"; PROXY_ARG="$2"; shift ;;
    --proxy=*) PROXY_ARG="${1#--proxy=}" ;;
    -h|--help) sed -n '2,46p' "$0" | sed 's/^# \?//'; exit 0 ;;
    *) fail "unknown arg: $1 (use --help)" ;;
  esac
  shift
done

run() { if [ "$DRY_RUN" -eq 1 ]; then note "[dry-run] $*"; else "$@"; fi; }

# ── Proxy ───────────────────────────────────────────────────────────
PROXY_URL="${PROXY_ARG:-${HTTPS_PROXY:-${HTTP_PROXY:-${https_proxy:-${http_proxy:-}}}}}"
NO_PROXY_VAL="${NO_PROXY:-${no_proxy:-localhost,127.0.0.1,::1}}"
if [ -n "$PROXY_URL" ]; then
  export HTTP_PROXY="$PROXY_URL" HTTPS_PROXY="$PROXY_URL"
  export http_proxy="$PROXY_URL" https_proxy="$PROXY_URL"
  export NO_PROXY="$NO_PROXY_VAL" no_proxy="$NO_PROXY_VAL"
  note "proxy in use: $PROXY_URL  (no_proxy: $NO_PROXY_VAL)"
fi

apt_already_has_proxy() {
  apt-config dump 2>/dev/null | grep -qE 'Acquire::https?::Proxy[[:space:]]+"[^"]+"'
}
configure_apt_proxy() {
  [ -z "$PROXY_URL" ] && return 0
  if apt_already_has_proxy; then
    note "apt proxy already configured system-wide — 그대로 사용"
  else
    local apt_conf=/etc/apt/apt.conf.d/99proxy-aidh-bootstrap
    if [ "$DRY_RUN" -eq 1 ]; then
      note "[dry-run] would write $apt_conf"
    else
      cat > "$apt_conf" <<EOF
Acquire::http::Proxy "$PROXY_URL";
Acquire::https::Proxy "$PROXY_URL";
EOF
      ok "apt proxy config → $apt_conf"
    fi
  fi
}
configure_apt_proxy

# 사내 표준 fallback (= _common.sh DEFAULT_FALLBACK_PROXY 와 일치).
FALLBACK_PROXY="${AIDH_FALLBACK_PROXY:-http://168.219.61.252:8080}"
curl_with_proxy_fallback() {
  local out="$1" url="$2"; shift 2
  local common=(-fL --retry 10 --retry-delay 5 --retry-all-errors
                --connect-timeout 30 --max-time 600 "$@")
  if curl "${common[@]}" "$url" -o "$out" 2>/tmp/aidh-curl.err; then return 0; fi
  if [ -n "$FALLBACK_PROXY" ]; then
    warn "첫 curl 실패; fallback 프록시로 재시도 $FALLBACK_PROXY"
    note "$(tail -2 /tmp/aidh-curl.err 2>/dev/null || true)"
    if curl "${common[@]}" --proxy "$FALLBACK_PROXY" "$url" -o "$out"; then
      ok "fallback 프록시로 다운로드 성공"; return 0
    fi
  fi
  return 1
}

# ── Sanity ──────────────────────────────────────────────────────────
if [ "$EUID" -ne 0 ] && [ "$DRY_RUN" -ne 1 ]; then
  fail "root 필요 (apt / dpkg 모두 sudo). 재실행: sudo bash $0"
fi
command -v apt-get >/dev/null || fail "apt-get 없음 — Ubuntu/Debian 전용 스크립트"
. /etc/os-release 2>/dev/null || true
[ "${ID:-}" = "ubuntu" ] || warn "distro=${ID:-?} ${VERSION_ID:-} (Ubuntu 24.04 에서 검증됨)"

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:${PATH}"

detect_online() {
  apt_already_has_proxy && return 0
  curl -sSf --max-time 5 --head https://archive.ubuntu.com/ubuntu/ >/dev/null 2>&1 && return 0
  curl -sSf --max-time 5 --head https://deb.nodesource.com/ >/dev/null 2>&1 && return 0
  return 1
}
if [ "$MODE" = "auto" ]; then
  if detect_online; then MODE="online"; note "auto: ONLINE"; else MODE="offline"; note "auto: OFFLINE"; fi
fi
[ "$MODE" = "offline" ] && { [ -d "$DEB_DIR" ] || fail "offline 모드인데 $DEB_DIR 없음 (패키지 사전 stage 필요)"; }

find_cached_deb() {
  local prefix="$1" f sz found=""
  shopt -s nullglob
  for dir in "$DEB_DIR" "$CACHE_DIR" "$REPO_ROOT" "${AIDH_DEB_DIR:-}"; do
    [ -n "$dir" ] && [ -d "$dir" ] || continue
    for f in "$dir/${prefix}"*.deb; do
      [ -e "$f" ] || continue
      sz="$(stat -c %s "$f" 2>/dev/null || echo 0)"
      [ "$sz" -lt 1000000 ] && continue
      found="$f"; break 2
    done
  done
  shopt -u nullglob
  printf '%s' "$found"
}

have_version() {
  local cmd="$1" min="$2" bin="" v major
  if command -v "$cmd" >/dev/null 2>&1; then bin="$(command -v "$cmd")"
  else for c in "/usr/bin/$cmd" "/usr/local/bin/$cmd"; do [ -x "$c" ] && bin="$c" && break; done; fi
  [ -z "$bin" ] && return 1
  v="$("$bin" --version 2>&1 | head -1)"
  major="$(printf '%s' "$v" | grep -oE '[0-9]+' | head -1)"
  [ -n "$major" ] && [ "$major" -ge "$min" ]
}

# ── Step 1: base packages ───────────────────────────────────────────
step "Step 1 — base packages (git/curl/make/python3.12/venv)"
NEED=()
for pkg in git curl make build-essential ca-certificates gnupg lsb-release \
           python3.12 python3.12-venv python3-pip software-properties-common; do
  dpkg -s "$pkg" >/dev/null 2>&1 || NEED+=("$pkg")
done
if [ "${#NEED[@]}" -eq 0 ]; then
  ok "base packages 모두 설치됨 (MXWP bootstrap 선행 시 정상)"
elif [ "$MODE" = "online" ]; then
  ok "apt install: ${NEED[*]}"
  run timeout --foreground 120 apt-get update -y || warn "apt-get update 실패/타임아웃 — install 계속 시도"
  run apt-get install -y --no-install-recommends "${NEED[@]}"
else
  ok "offline: $DEB_DIR/*.deb"
  run apt-get install -y --no-install-recommends "$DEB_DIR"/*.deb
  run apt-get install -y -f
fi

# ── Step 2: Apptainer ≥ 1.3 ─────────────────────────────────────────
step "Step 2 — Apptainer (≥ 1.3)"
if have_version apptainer 1; then
  ok "이미 설치됨: $(apptainer --version 2>&1 | head -1)"
else
  if [ "$MODE" = "online" ]; then
    arch="$(dpkg --print-architecture)"; ver="1.3.6"
    url="https://github.com/apptainer/apptainer/releases/download/v${ver}/apptainer_${ver}_${arch}.deb"
    mkdir -p "$DEB_DIR"; target="$DEB_DIR/apptainer_${ver}_${arch}.deb"
    cached="$(find_cached_deb apptainer)"
    if [ -n "$cached" ]; then
      ok "cached .deb 사용: $cached"
      run apt-get install -y --no-install-recommends "$cached"
    else
      note "PPA 경로 회피 (사내 프록시 뒤에서 launchpad/keyserver hang) → GitHub release .deb"
      ok "다운로드 $url"
      curl_with_proxy_fallback "$target" "$url" || fail "apptainer .deb 다운로드 실패 (직접/fallback 모두).
  인터넷 되는 PC 에서 받아 $DEB_DIR/ 에 두고 재실행:
    $url"
      run apt-get install -y --no-install-recommends "$target"
    fi
  else
    ls "$DEB_DIR"/apptainer*.deb >/dev/null 2>&1 || fail "offline 모드인데 $DEB_DIR 에 apptainer*.deb 없음"
    run apt-get install -y --no-install-recommends "$DEB_DIR"/apptainer*.deb
  fi
  ok "$(apptainer --version 2>&1 | head -1)"
fi

# ── Step 3: Node.js 20 (+ npm) ──────────────────────────────────────
step "Step 3 — Node.js 20 (+ npm)"
if have_version node 20; then
  ok "이미 설치됨: $(node --version)  (npm $(npm --version 2>/dev/null || echo '?'))"
else
  cached_deb="$(find_cached_deb nodejs)"
  cached_tar=""
  shopt -s nullglob
  for dir in "$DEB_DIR" "$CACHE_DIR" "$REPO_ROOT" "${AIDH_DEB_DIR:-}"; do
    [ -n "$dir" ] && [ -d "$dir" ] || continue
    for f in "$dir"/node-v*-linux-x64.tar.xz "$dir"/node-v*-linux-x64.tar.gz; do
      [ -e "$f" ] || continue; cached_tar="$f"; break 2
    done
  done
  shopt -u nullglob
  if [ -n "$cached_deb" ]; then
    ok "cached .deb: $cached_deb"
    run apt-get install -y --no-install-recommends "$cached_deb"
  elif [ -n "$cached_tar" ]; then
    ok "cached tarball: $cached_tar → /usr/local"
    run tar -xf "$cached_tar" -C /usr/local --strip-components=1
  elif [ "$MODE" = "online" ]; then
    ok "NodeSource 20.x repo 추가"
    s="$(mktemp --suffix=.sh)"
    if curl_with_proxy_fallback "$s" "https://deb.nodesource.com/setup_20.x"; then
      run bash "$s"; rm -f "$s"
      run apt-get install -y --no-install-recommends nodejs
    else
      rm -f "$s"
      warn "NodeSource 도달 불가 → nodejs.org tarball 폴백"
      nv="20.18.1"; turl="https://nodejs.org/dist/v${nv}/node-v${nv}-linux-x64.tar.xz"
      tf="$DEB_DIR/node-v${nv}-linux-x64.tar.xz"; mkdir -p "$DEB_DIR"
      curl_with_proxy_fallback "$tf" "$turl" || fail "Node.js 설치 실패.
  https://nodejs.org/dist/ 에서 node-v20.x-linux-x64.tar.xz 받아
  $DEB_DIR/ 에 두고 재실행."
      run tar -xf "$tf" -C /usr/local --strip-components=1
    fi
  else
    fail "offline 모드인데 nodejs .deb / node-v*.tar 없음 ($DEB_DIR)"
  fi
  ok "$(node --version)  (npm $(npm --version 2>/dev/null || echo '?'))"
fi

# ── Step 4: Firewall (API 포트 개방) ────────────────────────────────
# 대시보드/Extension/MCP 가 같은 망의 클라이언트에서 접속되므로 API 포트를
# 미리 연다. postgres 포트는 localhost 전용이라 열지 않는다 (보안).
# .env 가 있으면 거기 API_PORT 를, 없으면 기본 8001.
step "Step 4 — Firewall (API 포트 개방)"
API_PORT_VAL="8001"
_envf="$REPO_ROOT/deploy/apptainer/.env"
[ -f "$_envf" ] || _envf="$REPO_ROOT/deploy/apptainer/.env.example"
if [ -f "$_envf" ]; then
  _p="$(grep -E '^API_PORT=' "$_envf" 2>/dev/null | tail -1 | cut -d= -f2 | tr -dc '0-9')"
  [ -n "$_p" ] && API_PORT_VAL="$_p"
fi
if command -v ufw >/dev/null 2>&1; then
  # ufw allow 는 비활성 상태여도 규칙을 저장해 두므로(나중에 켜도 적용)
  # 멱등하게 항상 추가한다. ufw 가 규칙 중복은 알아서 무시.
  run ufw allow "${API_PORT_VAL}/tcp" || warn "ufw allow 실패 — 수동: sudo ufw allow ${API_PORT_VAL}/tcp"
  state="$(ufw status 2>/dev/null | head -1)"
  ok "ufw: ${API_PORT_VAL}/tcp 허용 (${state:-status unknown})"
  note "postgres 포트는 열지 않음 (localhost 전용 — 보안)"
else
  warn "ufw 없음 — 호스트 방화벽 미관리."
  note "클라우드 보안그룹/사내망 방화벽이면 ${API_PORT_VAL}/tcp 인바운드를 별도 개방하세요."
fi

# ── Done ────────────────────────────────────────────────────────────
echo
ok "Bootstrap 완료 (mode: $MODE)"
echo
note "다음 — 일반 사용자 계정에서:"
note "  cd $REPO_ROOT && bash quickstart.sh     # 자동 (bundle/source 판단 + 검증)"
note "  또는  bash setup.sh                      # 소스 빌드 직행"
