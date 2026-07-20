# Apps — In-Instance App Hosting

Static web apps (built from [cortex-app-template](https://github.com/mocaOS/cortex-app-template)) installed from a zip and served by the backend under `/apps/{app_id}/`, sandboxed, with proxied least-privilege API access. Part of the app-ecosystem plan (master doc: `cortex-registry/ECOSYSTEM.md`; builder skills: cortexskills.org/builder). **Gated on `ENABLE_APPS` (default off): every route 404s and the admin UI section hides itself — zero traces when disabled** (same philosophy as x402).

## Package & storage

Package = zip of `app.json` (manifest) + `icon.svg` + `dist/` (static bundle). Storage is filesystem-only under `settings.apps_dir` (default `.agents/apps`, mounted as the `apps_data` volume in prod) — no Neo4j schema:

```
{apps_dir}/{app_id}/
  app.json       manifest, verbatim from the package
  icon.svg
  dist/          static bundle; manifest "entry" (index.html) lives here
  install.json   install record: minted key (encrypted), grants, enabled state
  config.json    admin-configured values (secrets Fernet-encrypted, enc: prefix)
```

## Manifest (app.v1)

Server-side validation in `AppService.validate_manifest()` is the twin of the template's `validate.mjs` — **keep them in sync**. Key fields: `id` (kebab slug, `launch` reserved), `version` (semver), `type` (`"static"` and `"platform"` hostable; `"service"` never hosted), `cortex.keyScope` (`read`|`read_write`), `cortex.endpoints` (proxy allowlist, `/api/`-relative), `cortex.collections` (`"user-selected"`|`"all"`|[ids]), `config[]` (same field semantics as skill config: `type: secret`, `auth_header`), `externalHosts` (CSP connect-src, `${VAR}` resolved from config), `sharing.links`, `capabilities` (platform only). Canonical JSON Schema: `cortex-app-template/schema/app.v1.json`.

## Platform capabilities (`type: "platform"`)

Implemented so far (`_SUPPORTED_CAPABILITIES`; declaring anything else fails install with a clear message — tasks/storage/llm land per ECOSYSTEM.md §5):

- **`http`** — `POST /apps/{id}/api/platform/http` with envelope `{method, url, body?, content_type?}`. Executes server-side with auth headers built from config vars carrying `auth_header` templates (skill-style; secrets never reach the browser, target needs **no CORS setup**). Targets restricted to `capabilities.http.hosts` (literals or `${CONFIG_VAR}` refs resolved from app config → `allowed_http_hosts()`), then SSRF-guarded via `ssrf_guard.validate_url(allow_private=True)` — LAN targets legit (admin-approved), loopback/metadata blocked (else the envelope could hit the cortex API bypassing the allowlist). Redirects not followed (allowlist escape). `APP_HTTP_TIMEOUT`, 20 MB response cap. Reference consumer: `coding/cortex-paperless`.
- **`config` read (implicit, all apps)** — `GET /apps/{id}/api/platform/config` returns NON-secret config values (`public_config()`); secret-typed values never cross this boundary.

## Security model (the part not to regress)

1. **No real key ever reaches the browser.** Install mints a dedicated scoped API key (`app:{id}`, created_by `"apps"`; READ or READ+MANAGE from `keyScope`; collection-restricted from `collections`). The plaintext is retained server-side in `install.json` — Fernet-encrypted when `ENCRYPTION_KEY` is set — because the proxy must present it upstream. Delete revokes the key.
2. **App tokens** (`cat_` prefix): short-lived (`APP_TOKEN_TTL_SECONDS`, default 900s) HMAC tokens, payload `{v, app, principal, role, iat, exp, jti}`. Signing secret derived from `SESSION_SECRET` (fallback `ENCRYPTION_KEY`/`ADMIN_API_KEY`). **The prefix is bound into the HMAC** (`_sign` signs `prefix+body`) so a grant token (`cag_`) can't be re-labeled `cat_` and replayed as an app token (that would skip revocation — token type confusion). `validate_token` also requires a recognized principal: `owner` (logged-in launcher), `link:{grant_id}` (share links); `appuser:{id}` reserved. Tokens validate ONLY at the app proxy — never on main API routes. The owner-token endpoint requires the caller to hold MANAGE when the app's `keyScope` is `read_write` (else a READ key could borrow the app's write-scoped minted key).
3. **Sandboxed iframe**: launcher and share shell embed apps with `sandbox="allow-scripts allow-forms allow-downloads"` and **never** `allow-same-origin` → opaque origin, no cookie access. Static serving is cookie-less by design. Token handshake via postMessage: app sends `cortex:ready`/`cortex:token:renew`, host replies `{type:"cortex:token", token}`.
4. **Proxy allowlist**: `/apps/{id}/api/cortex/{path}` forwards to `APP_PROXY_UPSTREAM` (self-loopback) with `X-API-Key` attached, but only for paths prefix-matching `cortex.endpoints` (declared `ask` covers `ask/stream`); everything else 403. `endpoint_allowed` **fully URL-decodes and rejects `..`/`.`/empty segments** before matching — otherwise an encoded `documents/..%2fsearch` passes the prefix check and the upstream normalizes it to `search`, escaping the allowlist (fixed defect). Forwarding via httpx with `read=None` timeout and raw chunk relay — **SSE streams pass through unbuffered** (chat apps break otherwise).
5. **Per-app CSP** on every served file: `default-src 'self'`, `connect-src 'self'` + resolved `externalHosts`. Zip safety at install: path traversal rejected, compressed size capped (`APP_MAX_PACKAGE_MB`), uncompressed capped at 4x.
6. **Share-link grants** (`cag_` prefix): signed, revocable records in `install.json`, optional expiry, role viewer/editor. `/a/{app_id}?g={grant}` serves a minimal cookie-less shell that exchanges the grant for an app token (`POST /api/apps/{id}/grant-exchange`, unauthenticated) and brokers renewals. Revoking a grant also kills already-issued tokens from it (checked at validate time). Visitors hold nothing that validates anywhere but the app proxy — they cannot reach the cortex UI or main API.

## Backend surface

`services/app_service.py` (`get_app_service()` singleton): lifecycle (`install_from_zip` — upgrades keep key/config/grants), config (mask-preserving saves like skills), tokens/grants, `resolve_static` (SPA fallback to entry, traversal-safe), `csp_header`, `endpoint_allowed`, `upstream_api_key`. Raises `AppValidationError` carrying ALL issues (fail-fast philosophy, surfaced as 400 `{detail:{issues}}`).

Endpoints in `main.py` (Apps section, before web-crawl): admin CRUD (`/api/admin/apps*`, install is multipart), `POST /api/apps/{id}/token` (owner, `require_read_permission`), `POST /api/apps/{id}/grant-exchange` (public), proxy `api_route /apps/{id}/api/cortex/{path}` (registered BEFORE the static catch-all `GET /apps/{id}/{path}`), `GET /a/{id}` share shell. `_require_apps_enabled()` gates all of them.

## Frontend surface

- `components/admin/AppsManager.tsx` (+ `AppConfigModal`, `AppGrantsModal`) on the Settings page — hides entirely when `GET /api/admin/apps` 404s (flag off).
- Launcher grid at `/apps`; iframe host at `/apps/launch/{appId}` (owner token handshake). Routing split: the Next page `/apps` and `/apps/launch/*` are filesystem routes; `afterFiles` rewrites send `/apps/{appId}/*` and `/a/*` to the backend.

## Tests

`backend/tests/test_apps.py` (37 tests): manifest validation, install/upgrade/delete lifecycle with fake key service, zip safety, token roundtrip/expiry/tamper, grant exchange + revocation-kills-tokens, allowlist prefix semantics, static traversal, CSP config resolution, secret masking, platform capabilities (http host resolution, auth-header injection, config secret exclusion, endpoint gating), HTTP endpoint gates (404-when-disabled, 401/403 proxy, share shell). **Security regressions** (from the 2026-07-20 review): allowlist traversal-bypass rejection, share-shell manifest XSS escaping, grant→app token type-confusion, owner-token MANAGE gate, grant-exchange rate limit. Upstream proxy forwarding is exercised live via the verify skill.

## Roadmap hooks (do not build ad hoc)

Platform capabilities (`type: "platform"`: tasks/storage/http/llm/features/branding), app users (`appuser:` principal — port cortex-chat's auth as blueprint), delegated-provisioning key scope for service apps, registry install (`cortex-registry`). All specced in `cortex-registry/ECOSYSTEM.md` — extend within manifest schema v1 additively.
