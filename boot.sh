#!/usr/bin/env bash
# AI Data Hub — 부팅 후 접속 시 진입점 (루트 래퍼).
#
# 서버 reboot 후 SSH 접속해서 이거 하나만 실행하면 같은 상태로 복구된다.
# (데이터는 보존 — apptainer instance + uvicorn 만 재기동)
#
# 실제 복구 로직(stale pid/orphan state 정리 · 포트검사 · postgres+API
# 기동 · health 200 검증 · 접속 URL 출력)은 deploy/apptainer/boot.sh.
#
# 사용:
#   bash boot.sh                # postgres + API 복구 후 접속 URL 출력
#   bash boot.sh --skip-api     # postgres 만 (디버깅)
#   bash boot.sh --force        # systemd 관리 중이어도 강제 직접 기동
exec bash "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/deploy/apptainer/boot.sh" "$@"
