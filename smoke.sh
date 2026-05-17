#!/usr/bin/env bash
# AI Data Hub — 스모크 테스트 (루트 래퍼).
# 사용: bash smoke.sh [http://host:8001]
exec bash "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/deploy/apptainer/smoke.sh" "$@"
