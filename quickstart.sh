#!/usr/bin/env bash
# AI Data Hub — quickstart: 0~7단계 전 과정 (preflight 포함)
#
# 새 머신에서 처음 보는 사람이 막힘없이 운영 가능 상태까지 한 줄.
# setup.sh / install.sh 와의 차이:
#   - setup.sh: git clone 후 native build (서버에 인터넷 가능 + apt 설치 가능)
#   - install.sh: 번들 추출 후 (사전 빌드 SIF 동봉)
#   - quickstart.sh: 둘 중 자동 판단 + preflight + 결과 검증까지
#
# 사용:
#   bash quickstart.sh             # 자동 모드 (이미 있는 산출물 활용)
#   bash quickstart.sh --bundle    # 번들 모드 강제 (install.sh 사용)
#   bash quickstart.sh --source    # 소스 빌드 강제 (setup.sh 사용)
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APPT_DIR="$ROOT_DIR/deploy/apptainer"

MODE="auto"
case "${1:-}" in
  --bundle)  MODE="bundle" ;;
  --source)  MODE="source" ;;
  ""|--auto) ;;
  -h|--help) sed -n '2,12p' "${BASH_SOURCE[0]}"; exit 0 ;;
  *) echo "[ERROR] unknown arg: $1" >&2; exit 2 ;;
esac

echo "================================================================"
echo " AI Data Hub — quickstart  (mode=$MODE)"
echo "================================================================"

# ── Preflight ─────────────────────────────────────────────────────
echo "[Preflight]"
ERR=0
for cmd in apptainer python3 git curl; do
  if command -v "$cmd" >/dev/null 2>&1; then
    echo "  ✓ $cmd"
  else
    echo "  ✗ $cmd 미설치"; ERR=1
  fi
done
if [[ $ERR -eq 1 ]]; then
  cat <<'EOH'

[Preflight 실패] Ubuntu 24.04 설치 명령:
  sudo add-apt-repository -y ppa:apptainer/ppa
  sudo apt update
  sudo apt install -y apptainer python3.12 python3.12-venv git curl

설치 후 quickstart.sh 재실행.
EOH
  exit 1
fi
echo "  ✓ all required commands present"

# ── 자동 모드 판단 ──────────────────────────────────────────────────
if [[ "$MODE" == "auto" ]]; then
  if [[ -f "$APPT_DIR/postgres.sif" && -f "$APPT_DIR/postgres-base.sif" ]]; then
    MODE="bundle"
    echo "[Auto] SIF 사전 빌드본 발견 → install.sh 모드"
  else
    MODE="source"
    echo "[Auto] SIF 없음 → setup.sh 모드 (build + setup)"
  fi
fi

# ── 분기 ────────────────────────────────────────────────────────────
case "$MODE" in
  bundle) bash "$ROOT_DIR/install.sh" ;;
  source) bash "$ROOT_DIR/setup.sh" ;;
esac

# ── 사후 검증 ────────────────────────────────────────────────────
echo
echo "[Verify]"
bash "$APPT_DIR/diag.sh" || true

echo
echo "================================================================"
echo "✓ quickstart 완료"
echo "================================================================"
echo
echo "다음 단계:"
echo "  bash deploy/apptainer/diag.sh              # 상태 재확인"
echo "  bash deploy/apptainer/restart.sh           # .env 수정 후 재기동"
echo "  bash deploy/apptainer/desudo.sh --yes      # 권한 깨졌을 때"
echo "  bash deploy/apptainer/recover.sh --yes     # 모든 게 깨졌을 때"
