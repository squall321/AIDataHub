# Agent notes — AI Data Hub

## HWAX Portal integration (2026-06-07) — READ FIRST if touching the dashboard / deploy

This service is federated by the **HWAX Portal** (`hwax.sec.samsung.net`), reverse-proxied under the
sub-path **`/ai-data-hub/`** (the portal **strips** the prefix). Notes:

1. **No frontend build.** The dashboard (`api_server/static/dashboard/`) is hand-written vanilla JS
   served by FastAPI `StaticFiles` — there is **no bundler, no dist, nothing to build or ship**. On
   cae00 (corp network: npm/Docker Hub unreachable) this service needs only `git pull`. No Drive
   artifact is required for the frontend (the existing `backup-to-drive.sh`/`sync-from-drive.sh` are
   for DB dumps only).

2. **Sub-path handling is already implemented:** `dashboard.js` derives its prefix from
   `location.pathname`; `main.py` has `root_path` from `AIDH_ROOT_PATH` and redirects `/` (browsers)
   to the dashboard; `start_api.sh` passes `--root-path`. Run behind the portal with
   `AIDH_ROOT_PATH=/ai-data-hub ./boot.sh`. Don't add absolute `/api`,`/static` paths in the
   dashboard without routing them through the derived `BASE`.

Full details: **`docs/HWAX-PORTAL-INTEGRATION.md`**.
