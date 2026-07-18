#!/usr/bin/env bash
# AI Data Hub — 임베딩 모델(HF 캐시)을 Google Drive 로 올린다(온라인 dev 에서 1회/갱신 시).
# 폐쇄망 cae00 은 HF 를 직접 못 받으므로(사내 TLS 프록시가 huggingface.co 를 SSL 인터셉트),
# 온라인 dev 에서 캐시된 모델을 tar 로 묶어 Drive 에 두고 cae00 이 model-from-drive.sh 로 받는다.
#
#   bash deploy/apptainer/model-to-drive.sh                # .env provider 의 모델만
#   AIDH_MODEL_REPOS="intfloat/multilingual-e5-base intfloat/multilingual-e5-large" \
#     bash deploy/apptainer/model-to-drive.sh              # 여러 모델 명시
set -euo pipefail
APPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$APPT_DIR/_common.sh"
load_env

HF_HUB="${HF_HOME:-$HOME/.cache/huggingface}/hub"
# 올릴 모델 결정: 명시(AIDH_MODEL_REPOS) > provider 기준 자동
PROVIDER="$(sed -n 's/^EMBEDDING_PROVIDER=//p' "$APPT_DIR/.env" 2>/dev/null | tail -1)"
declare -A MAP=( [e5_small]=intfloat/multilingual-e5-small [e5_base]=intfloat/multilingual-e5-base
                 [e5_large]=intfloat/multilingual-e5-large )
REPOS="${AIDH_MODEL_REPOS:-${MAP[$PROVIDER]:-}}"
[ -n "$REPOS" ] || { echo "[INFO] provider=$PROVIDER 는 HF 모델 불필요(hash/openai) — skip"; exit 0; }

# repo 이름(intfloat/multilingual-e5-base) → HF 캐시 디렉토리명(models--intfloat--multilingual-e5-base)
DIRS=""
for r in $REPOS; do
  d="models--${r//\//--}"
  [ -d "$HF_HUB/$d" ] || { echo "[ERROR] 캐시에 없음: $HF_HUB/$d — 먼저 fetch-model.sh 로 1회 로드"; exit 1; }
  DIRS="$DIRS $d"
done

# Drive remote: 전용(AIDH_MODEL_REMOTE) > db-dumps 리모트에서 경로만 models 로 치환
REMOTE="${AIDH_MODEL_REMOTE:-}"
if [ -z "$REMOTE" ]; then
  DB="$(sed -n 's/^AIDH_DRIVE_REMOTE=//p' "$APPT_DIR/.env" 2>/dev/null | tail -1)"
  REMOTE="${DB%/db-dumps}/models"; REMOTE="${REMOTE%%/}"
fi
[ -n "$REMOTE" ] && [[ "$REMOTE" == *:* ]] || { echo "[ERROR] 모델 remote 미확인 — AIDH_MODEL_REMOTE 설정"; exit 1; }
command -v rclone >/dev/null || { echo "[ERROR] rclone 미설치"; exit 1; }

TS="$(date -u +%Y%m%d-%H%M%SZ)"
STAGE="$(mktemp -d)"; trap 'rm -rf "$STAGE"' EXIT
TAR="$STAGE/aidh-models-$TS.tar.gz"
echo "→ tar: $REPOS"
tar -czf "$TAR" -C "$HF_HUB" $DIRS
sha256sum "$TAR" | awk '{print $1}' > "$TAR.sha256"
SZ="$(ls -lh "$TAR" | awk '{print $5}')"
echo "→ upload → $REMOTE/  ($SZ)"
rclone copy "$TAR" "$REMOTE/" && rclone copy "$TAR.sha256" "$REMOTE/"
# latest 포인터(파일명만) — from 스크립트가 최신을 집게
echo "aidh-models-$TS.tar.gz" | rclone rcat "$REMOTE/latest.txt"
echo "✓ pushed: $REMOTE/aidh-models-$TS.tar.gz  ($SZ)"
echo "  cae00:  bash deploy/apptainer/model-from-drive.sh"
