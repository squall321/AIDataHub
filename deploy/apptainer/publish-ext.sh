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
VFILE="ai-data-hub-uploader-${VER}.vsix"
# 둘 다 게시: (1) 버전 박힌 파일 = 고유 URL, 브라우저 캐시 무관, 릴리스별 보존
#            (2) latest = 항상 최신 (안정 링크 원하는 경우)
cp -f "$VSIX" "$DL_DIR/$VFILE"
cp -f "$VSIX" "$DL_DIR/ai-data-hub-uploader-latest.vsix"

# 옛 버전 vsix 정리 — 최신 KEEP(기본 5) 개만 보존 (/downloads 무한 증가 방지).
KEEP="${AIDH_VSIX_KEEP:-5}"
if [[ "$KEEP" -gt 0 ]]; then
  mapfile -t _vs < <(ls -1t "$DL_DIR"/ai-data-hub-uploader-[0-9]*.vsix 2>/dev/null)
  if [[ "${#_vs[@]}" -gt "$KEEP" ]]; then
    for f in "${_vs[@]:$KEEP}"; do rm -f "$f" && echo "  - 정리: $(basename "$f")"; done
  fi
fi

# meta — 대시보드가 버전 링크(versioned)와 안정 링크(filename) 둘 다 알 수 있게.
printf '{"version":"%s","filename":"ai-data-hub-uploader-latest.vsix","versioned_filename":"%s","built_at":"%s"}\n' \
  "$VER" "$VFILE" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$DL_DIR/extension-meta.json"

SZ="$(du -h "$DL_DIR/$VFILE" | cut -f1)"
echo "✓ 게시 완료 — v${VER} (${SZ})"
echo "  버전 링크 : /downloads/$VFILE   (캐시 무관, 권장)"
echo "  최신 링크 : /downloads/ai-data-hub-uploader-latest.vsix"
echo "  확인: curl -s http://127.0.0.1:${API_PORT}/downloads/extension-meta.json"

# [5] Drive 게시(옵션·비치명) — vsix 는 빌드 산출물이라 git 미추적(.gitignore 규칙 유지)이고
#     cae00 은 npm 이 없어 빌드 불가 → Drive ext-downloads 가 유일한 전달 경로다.
#     (수신측: HWAXPortal deploy-all-from-drive.sh 의 aidh 단계가 받아간다.)
EXT_REMOTE="${AIDH_EXT_DRIVE_REMOTE:-}"
if [[ -z "$EXT_REMOTE" && -n "${AIDH_DRIVE_REMOTE:-}" ]]; then
  EXT_REMOTE="${AIDH_DRIVE_REMOTE%/db-dumps}/ext-downloads"
fi
if [[ -n "$EXT_REMOTE" ]] && command -v rclone >/dev/null 2>&1; then
  if rclone copy "$DL_DIR/$VFILE" "$EXT_REMOTE/" 2>/dev/null \
     && rclone copyto "$DL_DIR/$VFILE" "$EXT_REMOTE/ai-data-hub-uploader-latest.vsix" 2>/dev/null \
     && rclone copy "$DL_DIR/extension-meta.json" "$EXT_REMOTE/" 2>/dev/null; then
    echo "  Drive 게시 : $EXT_REMOTE  (cae00 deploy-all 이 수신)"
  else
    echo "  ! Drive 게시 실패(비치명) — 수동: rclone copy $DL_DIR/$VFILE $EXT_REMOTE/"
  fi
else
  echo "  · Drive 게시 생략 (AIDH_DRIVE_REMOTE/rclone 없음)"
fi
