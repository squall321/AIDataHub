#!/usr/bin/env bash
# AI Data Hub — 빠른 상태 확인 (루트 래퍼).
# 사용: bash status.sh
exec bash "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/deploy/apptainer/status.sh" "$@"
