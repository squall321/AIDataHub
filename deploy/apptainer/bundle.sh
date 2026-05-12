#!/usr/bin/env bash
# AI Data Hub — 안전한 tar 번들 생성 (Issue #2 + #8 데이터/git 자동 exclude).
#
# 다음을 한 tar 안에 포함:
#   - 소스 코드 (api_server, vscode_extension/src, deploy/apptainer/* 등)
#   - 사전 빌드 산출물 (--with-sif: postgres-base.sif/postgres.sif, --with-vsix: .vsix)
#   - manifest.txt (포함 파일 + sha256 + 빌드 시각)
#
# 자동 exclude (호스트 의존 + 권한 위험):
#   .git / .venv / __pycache__ / node_modules / .bkit / out/ / *.log / data/
#
# 출력: /tmp/aidh-bundle-YYYYMMDD-HHMMSS.tar.gz (sha256 자동 산출)
#
# 사용:
#   bash deploy/apptainer/bundle.sh                 # 코드만
#   bash deploy/apptainer/bundle.sh --all           # SIF + vsix 포함 (권장 — 새 서버에 그대로 가져가)
#   bash deploy/apptainer/bundle.sh --with-sif --with-vsix
#   bash deploy/apptainer/bundle.sh --output /path/to/x.tar.gz
#   bash deploy/apptainer/bundle.sh --split 100M    # 100MB 단위 분할 (메일 첨부 등)
set -euo pipefail
# shellcheck source=/dev/null
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
load_env

TS=$(date +%Y%m%d-%H%M%S)
OUT="/tmp/aidh-bundle-${TS}.tar.gz"
WITH_SIF=0
WITH_VSIX=0
SPLIT_SIZE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --output|-o)  OUT="$2"; shift 2 ;;
    --with-sif)   WITH_SIF=1; shift ;;
    --with-vsix)  WITH_VSIX=1; shift ;;
    --all)        WITH_SIF=1; WITH_VSIX=1; shift ;;
    --split)      SPLIT_SIZE="$2"; shift 2 ;;
    -h|--help)
      sed -n '2,18p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "[ERROR] unknown arg: $1" >&2; exit 2 ;;
  esac
done

echo "================================================================"
echo " AI Data Hub — bundle"
echo " root : $ROOT_DIR"
echo " out  : $OUT"
echo " with-sif=$WITH_SIF  with-vsix=$WITH_VSIX  split=${SPLIT_SIZE:-no}"
echo "================================================================"

# ── 항상 제외 ────────────────────────────────────────────────────────
EXCLUDES=(
  # 호스트 의존
  --exclude='./deploy/apptainer/data'
  --exclude='./deploy/apptainer/logs'
  --exclude='./.git'
  --exclude='./.venv'
  --exclude='./.bkit'
  --exclude='./api_server/.venv'
  --exclude='./api_server/.bkit'
  # 캐시 / 컴파일 산출물
  --exclude='./api_server/__pycache__'
  --exclude='./api_server/src/**/__pycache__'
  --exclude='./__pycache__'
  --exclude='*.pyc'
  --exclude='*.pyo'
  --exclude='./vscode_extension/node_modules'
  --exclude='./vscode_extension/.vscode-test'
  --exclude='./node_modules'
  --exclude='./tmp'
  --exclude='./build'
  --exclude='./dist'
  --exclude='*.log'
  --exclude='*.tsbuildinfo'
  # dev .env (HOST_IP 치환 전이라 가져가도 무용 — 새 서버에서 .env.example 로 재생성)
  --exclude='./deploy/apptainer/.env'
  --exclude='./api_server/.env'
)

# 선택 exclude — SIF / vsix 는 --with-* 일 때만 포함
if [[ $WITH_SIF -eq 0 ]]; then
  EXCLUDES+=( --exclude='./deploy/apptainer/*.sif' )
else
  # SIF 존재 확인
  if ! ls "$APPT_DIR"/*.sif >/dev/null 2>&1; then
    echo "[WARN] --with-sif 지정했지만 SIF 파일 없음 — 먼저 bash build.sh 실행 필요"
  fi
fi

if [[ $WITH_VSIX -eq 0 ]]; then
  EXCLUDES+=( --exclude='./vscode_extension/*.vsix' )
  # out/ 도 제외 (vsix 가 있으면 out 도 같이 포함되는 게 일반적)
  EXCLUDES+=( --exclude='./vscode_extension/out' )
else
  if ! ls "$ROOT_DIR/vscode_extension"/*.vsix >/dev/null 2>&1; then
    echo "[WARN] --with-vsix 지정했지만 .vsix 없음 — 먼저 cd vscode_extension && npm run package"
  fi
fi

# ── 사이즈 사전 안내 ────────────────────────────────────────────────
echo
echo "[INFO] 자동 exclude 대상 (소스 머신 보존):"
for d in deploy/apptainer/data api_server/.venv vscode_extension/node_modules .git; do
  if [[ -d "$ROOT_DIR/$d" ]]; then
    SIZE=$(du -sh "$ROOT_DIR/$d" 2>/dev/null | awk '{print $1}')
    echo "       $d ($SIZE)"
  fi
done

# ── tar 생성 ────────────────────────────────────────────────────────
cd "$ROOT_DIR"
echo
echo "→ tar -czf $OUT ..."
tar "${EXCLUDES[@]}" -czf "$OUT" -C "$ROOT_DIR" .

SIZE=$(ls -lh "$OUT" | awk '{print $5}')
SHA=$(sha256sum "$OUT" | awk '{print $1}')

# ── manifest 별도 파일로 저장 ─────────────────────────────────────────
MANIFEST="${OUT%.tar.gz}.manifest.txt"
{
  echo "AI Data Hub Bundle Manifest"
  echo "==========================="
  echo "Created: $(date '+%Y-%m-%d %H:%M:%S %Z')"
  echo "Host: $(hostname)"
  echo "Source: $ROOT_DIR"
  echo "Bundle: $(basename "$OUT")"
  echo "Size: $SIZE"
  echo "SHA-256: $SHA"
  echo
  echo "Includes:"
  echo "  - Source (api_server, vscode_extension/src, deploy, docs)"
  echo "  - alembic migrations (0001-$(ls api_server/alembic/versions/*.py 2>/dev/null | sed 's/.*0*\([0-9]\+\)_.*/\1/' | sort -n | tail -1))"
  [[ $WITH_SIF -eq 1 ]] && echo "  - Pre-built SIF (postgres-base.sif + postgres.sif)"
  [[ $WITH_VSIX -eq 1 ]] && echo "  - VSCode extension .vsix ($(ls vscode_extension/*.vsix 2>/dev/null | xargs -n1 basename | head -1))"
  echo
  echo "Excludes (regenerated on target):"
  echo "  - .git (clone fresh)"
  echo "  - .venv, node_modules, __pycache__ (rebuilt)"
  echo "  - deploy/apptainer/data (postgres data — initdb on first start)"
  echo "  - .env (HOST_IP placeholder, install.sh fills in)"
  echo
  echo "Restore:"
  echo "  1. Transfer bundle + manifest to target server"
  echo "  2. Verify: sha256sum -c (sha matches above)"
  echo "  3. Extract: mkdir -p ~/Projects/AIDataHub && tar -xzf $(basename "$OUT") -C ~/Projects/AIDataHub"
  echo "  4. Install: cd ~/Projects/AIDataHub && bash install.sh"
} > "$MANIFEST"

# ── split (옵션) ────────────────────────────────────────────────────
if [[ -n "$SPLIT_SIZE" ]]; then
  echo
  echo "→ split into $SPLIT_SIZE chunks"
  split -b "$SPLIT_SIZE" -d "$OUT" "${OUT}.part-"
  rm "$OUT"
  echo "  생성된 part 들:"
  ls -lh "${OUT}.part-"* | awk '{print "    " $9 " (" $5 ")"}'
  echo
  echo "  복원 방법 (새 서버):"
  echo "    cat ${OUT##*/}.part-* > ${OUT##*/}"
  echo "    sha256sum -c <(echo '$SHA  ${OUT##*/}')"
fi

# ── 결과 출력 ───────────────────────────────────────────────────────
echo
echo "================================================================"
echo "✓ bundle 생성 완료"
echo "================================================================"
echo "  file: $OUT$([[ -n $SPLIT_SIZE ]] && echo " (split → .part-XX)")"
echo "  size: $SIZE"
echo "  sha256: $SHA"
echo "  manifest: $MANIFEST"
echo
echo "── 새 서버 배포 절차 ──"
cat <<'EOH'
  # 1. 번들 + manifest 옮기기 (scp / USB / etc.)
  scp /tmp/aidh-bundle-*.tar.gz /tmp/aidh-bundle-*.manifest.txt user@target:/tmp/

  # 2. 새 서버에서
  mkdir -p ~/Projects/AIDataHub
  tar -xzf /tmp/aidh-bundle-*.tar.gz -C ~/Projects/AIDataHub
  cd ~/Projects/AIDataHub

  # 3. 한 줄 설치 (HOST_IP 자동 치환 + DB 기동 + 마이그레이션)
  bash install.sh

  # 4. 검증
  bash deploy/apptainer/diag.sh
EOH
