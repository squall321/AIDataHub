#!/usr/bin/env bash
# AI Data Hub — extension 빌드+게시 (루트 래퍼).
# 사용: bash publish-ext.sh [--skip-build]
exec bash "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/deploy/apptainer/publish-ext.sh" "$@"
