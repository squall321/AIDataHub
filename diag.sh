#!/usr/bin/env bash
# AI Data Hub — 통합 진단 (루트 래퍼).
# 사용: bash diag.sh
#       bash diag.sh --tail-logs
exec bash "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/deploy/apptainer/diag.sh" "$@"
