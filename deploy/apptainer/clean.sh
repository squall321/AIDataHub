#!/usr/bin/env bash
# AI Data Hub — data 디렉토리 비움 (서비스 동작 X).
# 권한 꼬임 / 부분 적재 / 차원 mismatch 등으로 처음부터 다시 시작할 때.
#
# ⚠ 모든 DB 데이터가 사라집니다. 백업이 필요하면 먼저 backup-db.sh.
#
# 사용:
#   bash deploy/apptainer/clean.sh           # 미리보기
#   bash deploy/apptainer/clean.sh --yes     # 실제 실행
set -euo pipefail
# shellcheck source=/dev/null
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
load_env

DRY=1
[ "${1:-}" = "--yes" ] && DRY=0

echo "================================================================"
echo " AI Data Hub — clean (data dir wipe)"
echo " mode=$([[ $DRY -eq 1 ]] && echo 'dry-run' || echo 'APPLY')"
echo "================================================================"

# 인스턴스 살아있으면 stop 먼저
if instance_running "$INST_POSTGRES"; then
  echo "→ stop $INST_POSTGRES (data 비우기 전 필수)"
  if [[ $DRY -eq 0 ]]; then
    apptainer instance stop "$INST_POSTGRES" || true
    sleep 2
  else
    echo "  [DRY] apptainer instance stop $INST_POSTGRES"
  fi
fi

# data 디렉토리 통째 삭제 (다음 기동 시 initdb)
for d in "$DATA_DIR/postgres" "$DATA_DIR/postgres-run" "$DATA_DIR/attachments" "$DATA_DIR/figures"; do
  if [[ -d "$d" ]]; then
    SIZE=$(du -sh "$d" 2>/dev/null | awk '{print $1}')
    if [[ $DRY -eq 1 ]]; then
      echo "  [DRY] rm -rf $d ($SIZE)"
    else
      rm -rf "$d"
      echo "  ✓ removed $d ($SIZE)"
    fi
  fi
done

# 로그도 cleanup (선택)
if [[ -d "$LOG_DIR" && $DRY -eq 0 ]]; then
  : > "$LOG_DIR/postgres-start.log" 2>/dev/null || true
  : > "$LOG_DIR/api.log" 2>/dev/null || true
fi

# 빈 디렉토리 다시 생성 (다음 기동 대비, 권한 777 — postgres user 가 init 가능)
if [[ $DRY -eq 0 ]]; then
  mkdir -p "$DATA_DIR/postgres" "$DATA_DIR/postgres-run" \
           "$DATA_DIR/attachments" "$DATA_DIR/figures" "$LOG_DIR"
  # postgres-run 만 chmod 777 (소켓 파일용)
  chmod 700 "$DATA_DIR/postgres"
  chmod 777 "$DATA_DIR/postgres-run"
fi

echo
if [[ $DRY -eq 1 ]]; then
  echo "[NEXT] 실제 적용: bash deploy/apptainer/clean.sh --yes"
else
  echo "✓ clean 완료. 다음 단계:"
  echo "    bash deploy/apptainer/start_postgres.sh   # initdb 부터 새로 시작"
  echo "  또는"
  echo "    bash deploy/apptainer/fresh.sh --yes      # clean + start + migrate 한방"
fi
