#!/usr/bin/env bash
# AI Data Hub — 핀 apptainer 별도 설치 (시스템 apptainer 무손상).
#
# 왜: 서버에 시스템 apptainer(예: 1.5.0)가 이미 있어도 우리 배포는
#     검증된 핀버전(기본 1.3.6)으로 돌려야 한다. apt 로 깔면 시스템 버전과
#     충돌/다운그레이드되므로, .deb 를 apt 대신 dpkg-deb -x 로 프로젝트
#     로컬 prefix(.tools/)에 "풀기"만 한다 — root 불필요, 시스템 무손상.
#
#     배포 스크립트는 _common.sh 의 apptainer() 함수가 이 핀버전으로
#     자동 라우팅하므로 alias/PATH 설정이 없어도 그대로 동작한다.
#     (비대화형 스크립트는 alias 가 안 먹는다 = 함수로 가로채는 이유.)
#
# 사용:
#   bash deploy/apptainer/install-apptainer.sh            # .env/기본 버전
#   APPTAINER_VERSION=1.3.6 bash .../install-apptainer.sh  # 버전 지정
#   bash .../install-apptainer.sh --deb /path/apptainer.deb  # 로컬 .deb
#   bash .../install-apptainer.sh --force                  # 재추출
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
load_env 2>/dev/null || true
export_proxy 2>/dev/null || true

VER="${APPTAINER_VERSION:-1.3.6}"
ARCH="$(dpkg --print-architecture 2>/dev/null || echo amd64)"
PREFIX="$APPT_DIR/.tools/apptainer-${VER}"
BIN="$PREFIX/usr/bin/apptainer"
CACHE_DIR="$APPT_DIR/cache"
DEB_OVERRIDE=""
FORCE=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --deb)   DEB_OVERRIDE="${2:-}"; shift 2 ;;
    --force) FORCE=1; shift ;;
    *) echo "usage: install-apptainer.sh [--deb FILE] [--force]"; exit 2 ;;
  esac
done

echo "================================================================"
echo " 핀 apptainer 설치  v${VER} (${ARCH})"
echo "  prefix : $PREFIX"
echo "  시스템 : $(command -v apptainer >/dev/null 2>&1 && (command apptainer --version 2>/dev/null) || echo '(없음)')  ← 무손상 유지"
echo "================================================================"

# 0) 이미 설치돼 있으면 skip (멱등).
if [[ -x "$BIN" && $FORCE -eq 0 ]]; then
  echo "[OK] 이미 설치됨: $("$BIN" --version 2>&1)"
  echo "     재설치하려면 --force"
  exit 0
fi

# 1) .deb 확보: --deb > cache/ > 다운로드
DEB=""
DEB_NAME="apptainer_${VER}_${ARCH}.deb"
if [[ -n "$DEB_OVERRIDE" ]]; then
  [[ -f "$DEB_OVERRIDE" ]] || { echo "[ERROR] --deb 파일 없음: $DEB_OVERRIDE" >&2; exit 1; }
  DEB="$DEB_OVERRIDE"
  echo "→ 로컬 .deb 사용: $DEB"
elif [[ -f "$CACHE_DIR/$DEB_NAME" ]]; then
  DEB="$CACHE_DIR/$DEB_NAME"
  echo "→ cached .deb 사용: $DEB"
elif [[ -f "$CACHE_DIR/deb/$DEB_NAME" ]]; then
  DEB="$CACHE_DIR/deb/$DEB_NAME"
  echo "→ cached .deb 사용 (bootstrap staged): $DEB"
elif ls "$CACHE_DIR"/deb/apptainer_*_"${ARCH}".deb >/dev/null 2>&1; then
  DEB="$(ls "$CACHE_DIR"/deb/apptainer_*_"${ARCH}".deb | head -1)"
  echo "→ cached .deb 사용 (bootstrap staged): $DEB"
else
  mkdir -p "$CACHE_DIR"
  DEB="$CACHE_DIR/$DEB_NAME"
  URL="https://github.com/apptainer/apptainer/releases/download/v${VER}/${DEB_NAME}"
  echo "→ 다운로드: $URL"
  # 직접 → 프록시 폴백
  if ! curl -fL --connect-timeout 10 -m 180 -o "$DEB" "$URL" 2>/dev/null; then
    echo "  · 직접 실패 → 프록시 재시도 (${HTTPS_PROXY:-${https_proxy:-none}})"
    if ! curl -fL --connect-timeout 10 -m 180 \
         ${HTTPS_PROXY:+--proxy "$HTTPS_PROXY"} \
         -o "$DEB" "$URL" 2>/dev/null; then
      rm -f "$DEB"
      echo "[ERROR] apptainer .deb 다운로드 실패 (직접/프록시 모두)." >&2
      echo "        인터넷 되는 곳에서 받아 반입 후:" >&2
      echo "          bash deploy/apptainer/install-apptainer.sh --deb ${DEB_NAME}" >&2
      echo "        받을 URL: $URL" >&2
      exit 1
    fi
  fi
fi

# 2) apt 가 아니라 dpkg-deb -x 로 prefix 에 추출 (시스템 무손상, root 불필요).
command -v dpkg-deb >/dev/null 2>&1 || { echo "[ERROR] dpkg-deb 없음 (dpkg 필요)." >&2; exit 1; }
rm -rf "$PREFIX"
mkdir -p "$PREFIX"
echo "→ dpkg-deb -x → $PREFIX"
dpkg-deb -x "$DEB" "$PREFIX"

# 2b) 경로 픽스 (MXWhitePaper 선례 — 검증된 버그 수정).
#  .deb 의 apptainer 바이너리는 빌드타임 절대경로(/usr/etc, /usr/var/lib)를
#  기준으로 conf / 세션 디렉터리를 찾는다. 비-`/` prefix 로 풀면 그 경로가
#  없어 `apptainer instance start` 가 FATAL: failed to resolve session
#  directory 로 죽는다(우리 start_postgres 등 전부 instance start 사용).
#  → usr/etc/apptainer, usr/var/lib/apptainer 를 실제 위치로 심볼릭.
echo "→ 경로 픽스 (conf / session dir 심볼릭 — instance start 가능하게)"
mkdir -p "$PREFIX/etc/apptainer" "$PREFIX/var/lib/apptainer"
mkdir -p "$PREFIX/usr/etc" "$PREFIX/usr/var/lib"
ln -sfn ../../etc/apptainer        "$PREFIX/usr/etc/apptainer"
ln -sfn ../../../var/lib/apptainer "$PREFIX/usr/var/lib/apptainer"

# 3) 검증 — 실패 시 반드시 $PREFIX 를 통째로 제거한다.
#  (깨진 .tools 를 남기면 _common.sh resolver 가 그걸 골라 instance start
#   가 죽고 서버가 안 뜬다 = ERR_CONNECTION_REFUSED. 차라리 없는 게 낫다 →
#   resolver 가 시스템 apptainer 로 폴백해 기존 동작 유지.)
_fail_clean() { echo "[ERROR] $1" >&2; rm -rf "$PREFIX"; exit 1; }
if [[ ! -x "$BIN" ]]; then
  ALT="$(find "$PREFIX" -name apptainer -type f -perm -u+x 2>/dev/null | head -1 || true)"
  [[ -n "$ALT" ]] && BIN="$ALT"
fi
[[ -x "$BIN" ]] || _fail_clean "추출 후 apptainer 실행파일 없음 → $PREFIX 제거(시스템 폴백)."
if ! VOUT="$("$BIN" --version 2>&1)"; then
  _fail_clean "핀 apptainer 실행 불가(libexec/arch 등) → $PREFIX 제거(시스템 폴백). 상세: $VOUT"
fi
echo "[OK] $VOUT"
case "$VOUT" in
  *"$VER"*) : ;;
  *) echo "[WARN] 보고된 버전이 v${VER} 와 다름 — 확인 필요: $VOUT" >&2 ;;
esac

# 4) 사람용 편의 심볼릭 (대화형 전용 — 스크립트는 _common.sh 함수가 처리).
LBIN="$HOME/.local/bin"
mkdir -p "$LBIN"
ln -sf "$BIN" "$LBIN/apptainer${VER}"
echo
echo "================================================================"
echo "✓ 핀 apptainer v${VER} 준비 완료"
echo "  배포 스크립트: 자동 사용 (_common.sh 의 apptainer() → 이 핀버전)"
echo "                추가 설정/alias 불필요."
echo "  수동(대화형) : $LBIN/apptainer${VER}  (PATH 에 ~/.local/bin 필요)"
echo "                또는 alias apptainer${VER}='$BIN'"
echo "  시스템 apptainer 는 그대로 유지됨."
echo "================================================================"
