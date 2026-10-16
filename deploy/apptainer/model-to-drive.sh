#!/usr/bin/env bash
# AI Data Hub — 임베딩 모델(HF 캐시)을 Google Drive 로 올린다(온라인 dev 에서 1회/갱신 시).
# 폐쇄망 cae00 은 HF 를 직접 못 받으므로(사내 TLS 프록시가 huggingface.co 를 SSL 인터셉트),
# 온라인 dev 에서 캐시된 모델을 tar 로 묶어 Drive 에 두고 cae00 이 model-from-drive.sh 로 받는다.
#
#   bash deploy/apptainer/model-to-drive.sh                # .env provider 의 모델(+ 슬롯 m3 를 쓰면 bge-m3)
#   AIDH_MODEL_REPOS="intfloat/multilingual-e5-base BAAI/bge-m3" \
#     bash deploy/apptainer/model-to-drive.sh              # 여러 모델 명시
set -euo pipefail
APPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$APPT_DIR/_common.sh"
load_env

HF_HUB="${HF_HOME:-$HOME/.cache/huggingface}/hub"
# 올릴 모델 결정: 명시(AIDH_MODEL_REPOS) > provider 기준 자동
PROVIDER="$(sed -n 's/^EMBEDDING_PROVIDER=//p' "$APPT_DIR/.env" 2>/dev/null | tail -1)"
declare -A MAP=( [e5_small]=intfloat/multilingual-e5-small [e5_base]=intfloat/multilingual-e5-base
                 [e5_large]=intfloat/multilingual-e5-large [bge_m3]=BAAI/bge-m3 )
# 임베딩 슬롯 m3(AIDH_EMBED_ACTIVE · AIDH_EMBED_FILL 에 m3)를 쓰면 그 공급자(bge_m3)의 모델도 함께 올린다.
SLOTS="$(sed -n -e 's/^AIDH_EMBED_ACTIVE=//p' -e 's/^AIDH_EMBED_FILL=//p' "$APPT_DIR/.env" 2>/dev/null | tr '\n' ' ')"
AUTO="${MAP[$PROVIDER]:-}"
[[ "$SLOTS" =~ (^|[^[:alnum:]])m3([^[:alnum:]]|$) ]] && AUTO="$AUTO ${MAP[bge_m3]}"
REPOS="${AIDH_MODEL_REPOS:-$AUTO}"
[ -n "${REPOS// /}" ] || { echo "[INFO] provider=$PROVIDER 는 HF 모델 불필요(hash/openai) — skip"; exit 0; }

# 모델 하나의 HF 캐시에서 실제로 읽는 것만 고른다 — refs/main 이 가리키는 스냅샷(onnx · openvino · 그림 제외)과
# 그 링크가 가리키는 blob. bge-m3 캐시는 onnx(2.2GB)와 다른 리비전의 safetensors(2.2GB)까지 6.4GB 인데
# sentence-transformers 는 오프라인에서 main 의 pytorch_model.bin(2.2GB)과 설정 · 토크나이저만 연다(2026-10-16 실측).
HF_REAL="$(cd "$HF_HUB" && pwd -P)"
_cache_files() {  # $1 = models--org--name — tar -T 에 줄 경로(HF_HUB 기준)
  local d="$1" rev f
  rev="$(cat "$HF_HUB/$d/refs/main" 2>/dev/null)" || return 1
  [ -n "$rev" ] && [ -d "$HF_HUB/$d/snapshots/$rev" ] || return 1
  echo "$d/refs/main"
  (cd "$HF_HUB" && find "$d/snapshots/$rev" \( -name onnx -o -name openvino -o -name imgs \) -prune \
       -o \( -type l -o -type f \) ! -name '*.jpg' ! -name '*.png' -print) |
  while IFS= read -r f; do
    echo "$f"
    if [ -L "$HF_HUB/$f" ]; then f="$(readlink -f "$HF_HUB/$f")"; echo "${f#"$HF_REAL"/}"; fi
  done
}

# repo 이름(intfloat/multilingual-e5-base) → HF 캐시 디렉토리명(models--intfloat--multilingual-e5-base)
LIST="$(mktemp)"; trap 'rm -f "$LIST"' EXIT
for r in $REPOS; do
  d="models--${r//\//--}"
  [ -d "$HF_HUB/$d" ] || { echo "[ERROR] 캐시에 없음: $HF_HUB/$d — 먼저 fetch-model.sh 로 1회 로드"; exit 1; }
  _cache_files "$d" >> "$LIST" || { echo "[ERROR] $d: refs/main 스냅샷을 못 찾았다 — 캐시가 깨졌다"; exit 1; }
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
STAGE="$(mktemp -d)"; trap 'rm -rf "$STAGE" "$LIST"' EXIT
TAR="$STAGE/aidh-models-$TS.tar.gz"
echo "→ tar: $REPOS ($(wc -l < "$LIST") 항목)"
tar -czf "$TAR" -C "$HF_HUB" -T "$LIST"
sha256sum "$TAR" | awk '{print $1}' > "$TAR.sha256"
SZ="$(ls -lh "$TAR" | awk '{print $5}')"
echo "→ upload → $REMOTE/  ($SZ)"
rclone copy "$TAR" "$REMOTE/" && rclone copy "$TAR.sha256" "$REMOTE/"
# latest 포인터(파일명만) — from 스크립트가 최신을 집게
echo "aidh-models-$TS.tar.gz" | rclone rcat "$REMOTE/latest.txt"
echo "✓ pushed: $REMOTE/aidh-models-$TS.tar.gz  ($SZ)"
echo "  cae00:  bash deploy/apptainer/model-from-drive.sh"
