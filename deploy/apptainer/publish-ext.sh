#!/usr/bin/env bash
# AI Data Hub — VSCode extension 빌드 + 대시보드 /downloads 게시 (단일 진실원).
#
# 왜: /downloads 의 vsix/meta 가 안 바뀌는 문제가 반복됐다 (setup.sh
#     --skip-server 를 따로 기억해서 돌려야만 갱신됨; update.sh 는 안 함).
#     이 스크립트가 build → copy → meta 를 한 곳에서 책임진다.
#     meta 의 version 은 *빌드된 vsix 파일명* 에서 추출 → 절대 drift 안 함.
#
# 사용:
#   bash deploy/apptainer/publish-ext.sh          # 빌드+게시
#   bash deploy/apptainer/publish-ext.sh --skip-build   # 이미 빌드된 vsix 만 게시
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
load_env
export_proxy

ROOT_DIR="$(cd "$APPT_DIR/../.." && pwd)"
EXT_DIR="$ROOT_DIR/vscode_extension"
DL_DIR="$ROOT_DIR/api_server/static/downloads"
SKIP_BUILD=0
[ "${1:-}" = "--skip-build" ] && SKIP_BUILD=1

if [[ $SKIP_BUILD -eq 0 ]]; then
  command -v node >/dev/null 2>&1 || { echo "[ERROR] node 없음 — sudo bash bootstrap.sh" >&2; exit 1; }
  cd "$EXT_DIR"
  [[ -d node_modules ]] || { echo "→ npm install"; npm install > "$LOG_DIR/npm-install.log" 2>&1; }
  rm -f ai-data-hub-uploader-*.vsix
  echo "→ npm run package (webview 게이트 포함)"
  if ! npm run package > "$LOG_DIR/npm-package.log" 2>&1; then
    echo "[ERROR] 패키징 실패 — tail $LOG_DIR/npm-package.log" >&2
    tail -15 "$LOG_DIR/npm-package.log" | sed 's/^/    /' >&2
    exit 1
  fi
fi

VSIX="$(ls -t "$EXT_DIR"/ai-data-hub-uploader-*.vsix 2>/dev/null | head -1)"
[[ -n "$VSIX" ]] || { echo "[ERROR] vsix 없음 ($EXT_DIR) — --skip-build 인데 빌드본 부재?" >&2; exit 1; }

# 진짜 버전 = 빌드된 파일명에서 추출 (meta 가 코드와 절대 어긋나지 않게).
VER="$(basename "$VSIX" | grep -oP '[0-9]+\.[0-9]+\.[0-9]+' | head -1)"
mkdir -p "$DL_DIR"
cp -f "$VSIX" "$DL_DIR/ai-data-hub-uploader-latest.vsix"
printf '{"version":"%s","filename":"ai-data-hub-uploader-latest.vsix","built_at":"%s"}\n' \
  "$VER" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$DL_DIR/extension-meta.json"

SZ="$(du -h "$DL_DIR/ai-data-hub-uploader-latest.vsix" | cut -f1)"
echo "✓ 게시 완료 — v${VER} (${SZ}) → /downloads/ai-data-hub-uploader-latest.vsix"
echo "  확인: curl -s http://127.0.0.1:${API_PORT}/downloads/extension-meta.json"
