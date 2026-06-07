# Serving AI Data Hub behind the HWAX Portal (sub-path)

> Context for future work: this service can run **standalone** (at `/`) OR **behind the HWAX Portal**
> (`hwax.sec.samsung.net`), which reverse-proxies it under the sub-path **`/ai-data-hub/`** and
> **STRIPS that prefix** before forwarding to uvicorn. Wired up 2026-06-07. Standalone behaviour
> is unchanged.

## Why this is small (no build, no static server swap)

Unlike the Vite-based platforms, AI Data Hub has **no frontend build and no dev server**. FastAPI/
uvicorn statically serves the hand-written dashboard (`api_server/static/dashboard/`, a vanilla-JS
SPA) AND the `/api`, `/static`, `/downloads` routes — all from **one process, all at the root**.

Because everything is at the root, the portal can simply **strip** `/ai-data-hub/` and every route
maps cleanly:
- `/ai-data-hub/dashboard/` → strip → `/dashboard/` → dashboard SPA
- `/ai-data-hub/api/...` → strip → `/api/...` → API
- `/ai-data-hub/static/...`, `/ai-data-hub/downloads/...` → strip → root mounts

## What was changed (3 small things)

1. **`main.py`** — `root_path=os.environ.get("AIDH_ROOT_PATH","")` (so `/docs`/openapi URLs carry the
   public prefix), and `GET "/"` redirects **browsers** (Accept: text/html) to `dashboard/` so the
   portal tile (which opens `/ai-data-hub/`) lands on the dashboard; API clients still get JSON.
2. **`dashboard.js`** — `BASE` is **derived from the dashboard's own URL** (`""` standalone,
   `"/ai-data-hub"` behind the portal). Every `BASE + path` call (all the `/api/...` fetches) then
   follows the prefix. A small DOMContentLoaded pass also rewrites `index.html`'s internal absolute
   links (`/static`, `/docs`, …) to sit under `BASE`.
3. **`deploy/apptainer/start_api.sh`** — passes `--root-path "$AIDH_ROOT_PATH"` to uvicorn when set.

No bundler, no `dist`, no nginx/caddy, no `try_files` — the existing FastAPI static serving does it.

## One env var: `AIDH_ROOT_PATH`

- Empty / unset → standalone (prefix `""`), unchanged.
- `AIDH_ROOT_PATH=/ai-data-hub` → docs/openapi URLs carry the prefix.

Set it in the environment that launches the API (it reaches `start_api.sh` → uvicorn `--root-path`).

## Run behind the portal

```bash
AIDH_ROOT_PATH=/ai-data-hub ./boot.sh
```

The portal reverse-proxies `https://hwax.sec.samsung.net/ai-data-hub/` → `127.0.0.1:8001` **with the
prefix stripped** (in the portal's `routes.env`: `ai-data-hub=http://localhost:8001/` — trailing
slash strips). The tile opens `/ai-data-hub/` → strip → `/` → redirect → `dashboard/`.

## Gotchas

- The portal **strips** the prefix (the service serves at root). The dashboard derives its own
  prefix from `location.pathname`, so it works whether stripped or not.
- `root_path` is only for generated URLs (docs/openapi) — routing is unaffected because the proxy
  strips the prefix. If you ever switch the portal to PASS the prefix instead, you'd need uvicorn to
  see the stripped path (it would 404 otherwise).
