#!/usr/bin/env bash
# ============================================================================
# AI Data Hub — 프로젝트 루트 한방 셋업 (Linux/Ubuntu 24.04, Apptainer 기반)
#
# 동작 (기본):
#   1) 사전 검증 (apptainer, python3-venv, node/npm)
#   2) Apptainer SIF 빌드 + Postgres 기동 + API 서버 기동
#   3) VSCode Extension 빌드 (.vsix)
#
# 사용:
#   bash setup.sh                  # 전부 실행
#   bash setup.sh --skip-server    # extension 만
#   bash setup.sh --skip-extension # 서버 만
#   bash setup.sh --force          # SIF + npm 재설치
#
# 사전 요구:
#   sudo add-apt-repository -y ppa:apptainer/ppa && sudo apt update
#   sudo apt install -y apptainer python3.12 python3.12-venv curl
#   curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash - && sudo apt install -y nodejs
#
# 프록시 환경:
#   deploy/apptainer/.env 의 HTTPS_PROXY/HTTP_PROXY/NO_PROXY 설정 (apt 는 호스트 설정)
# ============================================================================
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APPT_DIR="$ROOT_DIR/deploy/apptainer"
EXT_DIR="$ROOT_DIR/vscode_extension"

SKIP_SERVER=0
SKIP_EXTENSION=0
FORCE=0
EMBEDDER_OVERRIDE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-server)    SKIP_SERVER=1; shift ;;
    --skip-extension) SKIP_EXTENSION=1; shift ;;
    --force)          FORCE=1; shift ;;
    --embedder)       EMBEDDER_OVERRIDE="$2"; shift 2 ;;
    --embedder=*)     EMBEDDER_OVERRIDE="${1#*=}"; shift ;;
    -h|--help)
      grep -E '^# ' "${BASH_SOURCE[0]}" | sed 's/^# //'
      exit 0 ;;
    *) echo "[ERROR] 알 수 없는 옵션: $1" >&2; exit 1 ;;
  esac
done

# shellcheck source=/dev/null
source "$APPT_DIR/_common.sh"
load_env
export_proxy

# --embedder 플래그 처리 — .env 의 EMBEDDING_PROVIDER 를 덮어쓰고 필요 패키지 자동 설치.
if [[ -n "$EMBEDDER_OVERRIDE" ]]; then
  case "$EMBEDDER_OVERRIDE" in
    hash|e5_small|openai) ;;
    *) echo "[ERROR] --embedder 는 hash / e5_small / openai 중 하나" >&2; exit 1 ;;
  esac
  sed -i "s/^EMBEDDING_PROVIDER=.*/EMBEDDING_PROVIDER=${EMBEDDER_OVERRIDE}/" "$APPT_DIR/.env"
  export EMBEDDING_PROVIDER="$EMBEDDER_OVERRIDE"
  echo "[INFO] EMBEDDING_PROVIDER → $EMBEDDING_PROVIDER (.env 갱신)"
fi

# ── HOST_IP 감지 + .env 기록 (최초 1회 또는 placeholder 상태일 때) ────────
HOST_IP_VAL="$(detect_host_ip)"
ENV_FILE="$APPT_DIR/.env"
# .env 에 HOST_IP 가 없거나 아직 literal "HOST_IP" placeholder 면 감지값으로 교체
if ! grep -q "^HOST_IP=" "$ENV_FILE" 2>/dev/null || grep -q "^HOST_IP=HOST_IP$" "$ENV_FILE" 2>/dev/null; then
  if grep -q "^HOST_IP=" "$ENV_FILE" 2>/dev/null; then
    sed -i "s|^HOST_IP=.*|HOST_IP=${HOST_IP_VAL}|" "$ENV_FILE"
  else
    echo "HOST_IP=${HOST_IP_VAL}" >> "$ENV_FILE"
  fi
  echo "[INFO] HOST_IP → $HOST_IP_VAL (.env 갱신)"
else
  HOST_IP_VAL="$(grep '^HOST_IP=' "$ENV_FILE" | cut -d= -f2)"
fi

echo "================================================================"
echo " AI Data Hub — setup.sh"
echo "================================================================"
echo " ROOT       : $ROOT_DIR"
echo " host IP    : $HOST_IP_VAL"
echo " skip_server: $SKIP_SERVER  skip_ext: $SKIP_EXTENSION  force: $FORCE"
echo " embedder   : ${EMBEDDING_PROVIDER:-hash}"
echo "================================================================"

# ── 1) 서버 셋업 (PG + API) ────────────────────────────────────────────
if [[ "$SKIP_SERVER" -eq 0 ]]; then
  echo
  echo "[A] Apptainer 서버 셋업"
  if [[ "$FORCE" -eq 1 ]]; then
    bash "$APPT_DIR/build.sh" --force
    bash "$APPT_DIR/start_postgres.sh"
    bash "$APPT_DIR/start_api.sh"
  else
    bash "$APPT_DIR/install_all.sh"
  fi
else
  echo "[A] 서버 셋업 skip (--skip-server)"
fi

# ── 2) VSCode Extension 빌드 ──────────────────────────────────────────
VSIX=""
if [[ "$SKIP_EXTENSION" -eq 0 ]]; then
  echo
  echo "[B] VSCode Extension 빌드"
  require_node

  cd "$EXT_DIR"

  # 의존성 (멱등). --force 시 재설치.
  if [[ "$FORCE" -eq 1 || ! -d node_modules ]]; then
    echo "→ npm install"
    npm install > "$APPT_DIR/logs/npm-install.log" 2>&1
  else
    echo "✓ node_modules 존재 — install skip (--force 로 재설치)"
  fi

  echo "→ npm run build (tsc)"
  npm run build > "$APPT_DIR/logs/npm-build.log" 2>&1

  # 기존 .vsix 정리 (버전별 누적 방지)
  rm -f ai-data-hub-uploader-*.vsix

  echo "→ npm run package (vsce)"
  npm run package > "$APPT_DIR/logs/npm-package.log" 2>&1

  VSIX="$(ls -t "$EXT_DIR"/ai-data-hub-uploader-*.vsix 2>/dev/null | head -1)"
  if [[ -n "$VSIX" ]]; then
    echo "  ✓ $(basename "$VSIX") ($(du -h "$VSIX" | cut -f1))"

    # 대시보드 다운로드 페이지용 — static/downloads/ 에 복사 + 메타 JSON 갱신
    DOWNLOADS_DIR="$ROOT_DIR/api_server/static/downloads"
    mkdir -p "$DOWNLOADS_DIR"
    cp "$VSIX" "$DOWNLOADS_DIR/ai-data-hub-uploader-latest.vsix"
    VSIX_VER="$(basename "$VSIX" | grep -oP '[\d]+\.[\d]+\.[\d]+')"
    printf '{"version":"%s","filename":"ai-data-hub-uploader-latest.vsix","built_at":"%s"}\n' \
      "$VSIX_VER" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
      > "$DOWNLOADS_DIR/extension-meta.json"
    echo "  ✓ /downloads/ai-data-hub-uploader-latest.vsix 갱신 (v${VSIX_VER})"
  else
    echo "[ERROR] .vsix 생성 실패 — tail $APPT_DIR/logs/npm-package.log" >&2
    exit 1
  fi
else
  echo "[B] Extension 빌드 skip (--skip-extension)"
fi

# ── 3) 요약 ──────────────────────────────────────────────────────────
echo
echo "================================================================"
echo " ✓ 완료"
echo "================================================================"
if [[ "$SKIP_SERVER" -eq 0 ]]; then
  echo " Dashboard : http://${HOST_IP_VAL}:${API_PORT}/dashboard"
  echo " API       : http://${HOST_IP_VAL}:${API_PORT}/api/system/health"
  echo " Extension : http://${HOST_IP_VAL}:${API_PORT}/downloads/ai-data-hub-uploader-latest.vsix"
  echo " PG        : 127.0.0.1:${POSTGRES_PORT} (user=${POSTGRES_USER} db=${POSTGRES_DB})"
fi
if [[ "$SKIP_EXTENSION" -eq 0 && -n "$VSIX" ]]; then
  echo " VSIX  : $VSIX"
  if command -v code >/dev/null 2>&1; then
    echo "→ code --install-extension (자동)"
    code --install-extension "$VSIX" --force 2>&1 | sed 's/^/   /'
  else
    echo "         설치 → code --install-extension \"$VSIX\""
    echo "         또는 VSCode 명령 팔레트: Extensions: Install from VSIX..."
  fi
fi
echo " 정지  : bash $APPT_DIR/stop.sh"
echo "================================================================"

# 대시보드 자동 오픈 (서버 셋업 포함된 경우만)
if [[ "$SKIP_SERVER" -eq 0 ]]; then
  DASH_URL="http://${HOST_IP_VAL}:${API_PORT}/dashboard"
  if command -v xdg-open >/dev/null 2>&1; then
    echo "→ 대시보드 오픈: $DASH_URL"
    xdg-open "$DASH_URL" 2>/dev/null || true
  elif command -v open >/dev/null 2>&1; then
    open "$DASH_URL" 2>/dev/null || true
  else
    echo " 대시보드: $DASH_URL"
  fi
fi
