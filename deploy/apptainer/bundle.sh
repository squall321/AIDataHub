#!/usr/bin/env bash
# AI Data Hub — 안전한 tar 번들 생성 (Issue #2 + #8 데이터/git 자동 exclude).
#
# 왜 필요한가:
#   - 단순 ``tar -czf project.tar.gz ./`` 는 다음을 끌고 와서 새 서버에서 폭발:
#       * deploy/apptainer/data/postgres/  (root/postgres user 소유, 새 서버에서 권한 깨짐)
#       * deploy/apptainer/data/postgres-run/  (소켓 파일)
#       * .venv/, node_modules/  (호스트 의존)
#       * .git/  (origin SSH 인증 정보 오버라이드)
#       * .bkit/ (bkit plugin local state)
#       * out/, *.vsix  (재빌드 가능)
#   - 본 스크립트는 위를 자동 exclude.
#
# 출력:
#   /tmp/aidh-bundle-YYYYMMDD-HHMMSS.tar.gz  (또는 --output 지정)
#
# 사용:
#   bash deploy/apptainer/bundle.sh
#   bash deploy/apptainer/bundle.sh --output /path/to/aidh.tar.gz
#   bash deploy/apptainer/bundle.sh --with-sif    # 사전 빌드된 SIF 포함
#   bash deploy/apptainer/bundle.sh --with-vsix   # .vsix 확장 포함
set -euo pipefail
# shellcheck source=/dev/null
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
load_env

ROOT_DIR="$(cd "$APPT_DIR/../.." && pwd)"   # 프로젝트 루트
TS=$(date +%Y%m%d-%H%M%S)
OUT="/tmp/aidh-bundle-${TS}.tar.gz"
WITH_SIF=0
WITH_VSIX=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --output|-o)  OUT="$2"; shift 2 ;;
    --with-sif)   WITH_SIF=1; shift ;;
    --with-vsix)  WITH_VSIX=1; shift ;;
    *) echo "usage: bundle.sh [--output PATH] [--with-sif] [--with-vsix]"; exit 2 ;;
  esac
done

echo "================================================================"
echo " AI Data Hub — bundle"
echo " root=$ROOT_DIR"
echo " out =$OUT"
echo " with-sif=$WITH_SIF  with-vsix=$WITH_VSIX"
echo "================================================================"

# 항상 제외 (호스트 의존 + 권한 위험)
EXCLUDES=(
  --exclude='./deploy/apptainer/data'
  --exclude='./deploy/apptainer/logs'
  --exclude='./.git'
  --exclude='./.venv'
  --exclude='./.bkit'
  --exclude='./api_server/.venv'
  --exclude='./api_server/.bkit'
  --exclude='./api_server/__pycache__'
  --exclude='./api_server/src/**/__pycache__'
  --exclude='./vscode_extension/node_modules'
  --exclude='./vscode_extension/out'
  --exclude='./vscode_extension/.vscode-test'
  --exclude='./__pycache__'
  --exclude='*.pyc'
  --exclude='*.pyo'
  --exclude='./node_modules'
  --exclude='./tmp'
  --exclude='./build'
  --exclude='./dist'
)

# 선택 exclude
if [[ $WITH_SIF -eq 0 ]]; then
  EXCLUDES+=( --exclude='./deploy/apptainer/*.sif' )
else
  echo "[INFO] *.sif 포함 — 압축 후 큰 용량(~290MB) 예상"
fi
if [[ $WITH_VSIX -eq 0 ]]; then
  EXCLUDES+=( --exclude='./vscode_extension/*.vsix' )
else
  echo "[INFO] *.vsix 포함"
fi

# 안전: 사용자가 추가로 비우고 싶은 디렉토리 검출
WARN_DIRS=()
for d in deploy/apptainer/data api_server/.venv vscode_extension/node_modules; do
  if [[ -d "$ROOT_DIR/$d" ]]; then
    SIZE=$(du -sh "$ROOT_DIR/$d" 2>/dev/null | awk '{print $1}')
    WARN_DIRS+=("$d ($SIZE)")
  fi
done
if [[ ${#WARN_DIRS[@]} -gt 0 ]]; then
  echo "[INFO] 자동 exclude 대상 (소스 머신에서 보존):"
  for x in "${WARN_DIRS[@]}"; do echo "       $x"; done
fi

cd "$ROOT_DIR"
echo
echo "→ tar -czf $OUT ..."
tar "${EXCLUDES[@]}" -czf "$OUT" -C "$ROOT_DIR" .

SIZE=$(ls -lh "$OUT" | awk '{print $5}')
SHA=$(sha256sum "$OUT" | awk '{print $1}')
echo
echo "✓ bundle 생성 완료"
echo "  file: $OUT"
echo "  size: $SIZE"
echo "  sha256: $SHA"

echo
echo "── 새 서버에서 복원 절차 ───────────────────────────────────"
cat <<'EOH'
  # 1. tarball 옮긴 후 풀기
  cd ~
  tar -xzf aidh-bundle-*.tar.gz -C ~/Projects/AIDataHub --strip-components=0

  # 2. .git 새로 만들기 (필요 시)
  cd ~/Projects/AIDataHub
  git init
  git remote add origin <REPO_URL>
  git fetch origin main
  git reset --hard origin/main

  # 3. data 디렉토리 자동 생성됨 (empty) — postgres 첫 기동 시 initdb
  # 4. .env 작성 (cp deploy/apptainer/.env.example deploy/apptainer/.env)
  # 5. 셋업 실행
  bash setup.sh
EOH
