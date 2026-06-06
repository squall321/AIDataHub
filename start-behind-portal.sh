#!/usr/bin/env bash
# Start AI Data Hub served UNDER the HWAX portal sub-path (/ai-data-hub/).
# The portal reverse-proxies https://hwax.sec.samsung.net/ai-data-hub/ → this app and STRIPS the
# prefix, so FastAPI routing is unchanged; AIDH_ROOT_PATH just makes docs/openapi URLs carry it.
# The dashboard derives the prefix from its own URL, and "/" redirects browsers to the dashboard.
#
#   ./start-behind-portal.sh
#
# Standalone (no portal)? Run the normal boot.sh — root path defaults to "" (unchanged behaviour).
set -euo pipefail
export AIDH_ROOT_PATH="${AIDH_ROOT_PATH:-/ai-data-hub}"
cd "$(dirname "$0")"
echo "→ AI Data Hub with root-path ${AIDH_ROOT_PATH}"
exec ./boot.sh "$@"
