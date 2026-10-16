#!/usr/bin/env bash
# AI Data Hub — Drive 에서 임베딩 모델 tar 를 받아 HF 캐시로 복원(폐쇄망 cae00).
# HF 직접 다운로드가 사내 프록시 SSL 인터셉트로 막힌 서버용. 이미 있으면(멱등) skip.
#
#   bash deploy/apptainer/model-from-drive.sh          # 최신 tar 복원
#   AIDH_MODEL_REPOS="BAAI/bge-m3" bash deploy/apptainer/model-from-drive.sh   # .env 를 바꾸기 전에 bge-m3 를 먼저 받는다
set -euo pipefail
APPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$APPT_DIR/_common.sh"
load_env

HF_HUB="${HF_HOME:-$HOME/.cache/huggingface}/hub"
mkdir -p "$HF_HUB"

REMOTE="${AIDH_MODEL_REMOTE:-}"
if [ -z "$REMOTE" ]; then
  DB="$(sed -n 's/^AIDH_DRIVE_REMOTE=//p' "$APPT_DIR/.env" 2>/dev/null | tail -1)"
  REMOTE="${DB%/db-dumps}/models"; REMOTE="${REMOTE%%/}"
fi
[ -n "$REMOTE" ] && [[ "$REMOTE" == *:* ]] || { echo "[ERROR] 모델 remote 미확인 — AIDH_MODEL_REMOTE 설정"; exit 1; }
command -v rclone >/dev/null || { echo "[ERROR] rclone 미설치"; exit 1; }

# 필요한 모델이 이미 다 캐시에 있으면 굳이 안 받는다 — 멱등 게이트. 필요한 것 = 명시(AIDH_MODEL_REPOS) 또는
# provider 의 모델 + 임베딩 슬롯 m3(AIDH_EMBED_ACTIVE · AIDH_EMBED_FILL 에 m3)를 쓰면 bge-m3.
PROVIDER="$(sed -n 's/^EMBEDDING_PROVIDER=//p' "$APPT_DIR/.env" 2>/dev/null | tail -1)"
declare -A MAP=( [e5_small]=intfloat/multilingual-e5-small [e5_base]=intfloat/multilingual-e5-base
                 [e5_large]=intfloat/multilingual-e5-large [bge_m3]=BAAI/bge-m3 )
SLOTS="$(sed -n -e 's/^AIDH_EMBED_ACTIVE=//p' -e 's/^AIDH_EMBED_FILL=//p' "$APPT_DIR/.env" 2>/dev/null | tr '\n' ' ')"
AUTO="${MAP[$PROVIDER]:-}"
[[ "$SLOTS" =~ (^|[^[:alnum:]])m3([^[:alnum:]]|$) ]] && AUTO="$AUTO ${MAP[bge_m3]}"
WANT="${AIDH_MODEL_REPOS:-$AUTO}"
MISSING=""
for r in $WANT; do
  compgen -G "$HF_HUB/models--${r//\//--}/snapshots/*/config.json" >/dev/null 2>&1 || MISSING="$MISSING $r"
done
if [ -n "${WANT// /}" ] && [ -z "$MISSING" ]; then
  echo "[INFO] 이미 캐시에 존재:$(printf ' %s' $WANT) — skip"; exit 0
fi
[ -n "$MISSING" ] && echo "→ 캐시에 없는 모델:$MISSING"

LATEST="$(rclone cat "$REMOTE/latest.txt" 2>/dev/null | tr -d '[:space:]')"
[ -n "$LATEST" ] || LATEST="$(rclone lsf "$REMOTE/" 2>/dev/null | grep -E '^aidh-models-.*\.tar\.gz$' | sort | tail -1)"
[ -n "$LATEST" ] || { echo "[ERROR] Drive 에 모델 tar 없음($REMOTE) — 온라인 dev 에서 model-to-drive.sh 먼저"; exit 1; }

STAGE="$(mktemp -d)"; trap 'rm -rf "$STAGE"' EXIT
echo "→ download: $LATEST"
rclone copy "$REMOTE/$LATEST" "$STAGE/"
if rclone lsf "$REMOTE/$LATEST.sha256" >/dev/null 2>&1; then
  rclone copy "$REMOTE/$LATEST.sha256" "$STAGE/"
  echo "→ sha256 검증"
  (cd "$STAGE" && echo "$(cat "$LATEST.sha256")  $LATEST" | sha256sum -c -) || { echo "[ERROR] 무결성 실패"; exit 1; }
fi
echo "→ 복원 → $HF_HUB/"
tar -xzf "$STAGE/$LATEST" -C "$HF_HUB"
echo "✓ 모델 복원 완료. fetch-model.sh 가 이제 오프라인으로 로드/검증한다."
