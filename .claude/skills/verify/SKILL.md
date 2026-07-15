---
name: verify
description: Drive the running Cortex app end-to-end (login + browser) to verify frontend/backend changes at the real UI surface.
---

# Verifying Cortex changes in the running app

The dev stack runs in Docker with hot-reloaded mounted source — no rebuild
needed for frontend/backend edits:

- `cortex-frontend` → http://localhost:3000 (Next.js dev)
- `cortex-backend` → http://localhost:8000 (FastAPI)
- `cortex-neo4j` → 7474/7687

## Auth

- UI login: real form at `/login`; credentials from container env:
  `docker exec cortex-frontend printenv ADMIN_EMAIL` (and `ADMIN_PASSWORD`).
  Login sets a `session` cookie AND `admin_api_key` in localStorage (the
  frontend sends it as `X-API-Key` on fetches).
- Direct API: `KEY=$(docker exec cortex-backend printenv ADMIN_API_KEY)` then
  `curl -H "X-API-Key: $KEY" http://localhost:8000/api/...`.

## Browser driving (Playwright)

Local node (nvm v23) works; browsers cache under `~/.cache/ms-playwright`.

```bash
mkdir -p /tmp/verify-x && cd /tmp/verify-x
npm init -y && npm install playwright
npx playwright install chromium   # if cached revision mismatches
ADMIN_EMAIL=$(docker exec cortex-frontend printenv ADMIN_EMAIL) \
ADMIN_PASSWORD=$(docker exec cortex-frontend printenv ADMIN_PASSWORD) \
node script.mjs
```

Login flow in a script: goto `/login`, fill `input[name="email"]` /
`input[name="password"]`, click `button[type="submit"]`, wait for URL to
leave `/login`. Then navigate anywhere (e.g. `/documents`).

- Document cards on `/documents`: locate via
  `div.glass` containing `h4[title*="<filename>"]`; action buttons carry
  `title` attributes ("Download document", "View document", …).
- To assert download-vs-tab behavior: listen for the page `download` event
  and the context `page` event (popup).

## Test data

- Upload a throwaway doc without triggering processing:
  `curl -H "X-API-Key: $KEY" -F "file=@x.md" http://localhost:8000/api/upload`
  (stays `pending` until `/api/documents/process-pending` is called).
- Clean up: `curl -X DELETE -H "X-API-Key: $KEY" http://localhost:8000/api/documents/<id>`.

## Gotchas

- Frontend typecheck must run in the container (`docker exec cortex-frontend
  npx tsc --noEmit`) — local `node_modules` is incomplete/root-owned.
- Don't kick off document processing on real PDFs — it triggers LLM
  extraction (cost + long-running).
