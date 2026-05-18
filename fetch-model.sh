#!/usr/bin/env bash
# AI Data Hub — 임베딩 모델 다운로드/검증 (루트 래퍼).
# 인터넷 되는 서버에서 1회 실행 → ~/.cache/huggingface 채움.
# 사용: bash fetch-model.sh
exec bash "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/deploy/apptainer/fetch-model.sh" "$@"
