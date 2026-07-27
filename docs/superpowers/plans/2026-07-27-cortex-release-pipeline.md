# Cortex Release Pipeline & Self-Host Stack — Implementation Plan (Plan A)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give `mocaOS/cortex-app` and `mocaOS/cortex-chat` real versioned 1.0.0 releases that publish multi-arch container images to GHCR, plus a self-host Compose stack that brings up a working Cortex from those images and a hand-filled `.env`.

**Architecture:** Each repo's root `package.json` version is the source of truth; a tag-triggered release workflow refuses to run if the tag disagrees. Images build natively on amd64 and arm64 runners and merge into one manifest. cortex-app additionally emits a `stack.json` release asset pinning the tested component set. The self-host stack is a set of *static, valid* Compose files driven entirely by `${VAR}` interpolation — no YAML generation anywhere — with mode selected via Compose's native `COMPOSE_FILE` overlay mechanism.

**Tech Stack:** GitHub Actions, Docker Buildx, GHCR, Docker Compose v2, Node 20 (`node:test`, zero deps) for repo tooling, Python 3.11 / pytest (existing backend suite), Caddy 2.

This is **Plan A of two**. Plan B (`npx @mocaos/cortex` installer CLI) depends on the artifacts this plan produces and is written separately.

**Spec:** [`docs/superpowers/specs/2026-07-27-cortex-self-host-installer-design.md`](../specs/2026-07-27-cortex-self-host-installer-design.md)

## Global Constraints

- **Registry:** `ghcr.io/mocaos/cortex-backend`, `ghcr.io/mocaos/cortex-frontend`, `ghcr.io/mocaos/cortex-chat`. Tags per release: `<version>`, `<major>.<minor>`, `<major>`, `latest`.
- **Architectures:** `linux/amd64` (on `ubuntu-latest`) and `linux/arm64` (on `ubuntu-24.04-arm`). Native runners only — never QEMU for the backend.
- **Versions:** cortex-app root `package.json` → `1.0.0`. cortex-chat root `package.json` stays `1.0.0`. Tags are `v<version>`.
- **The backend service in every self-host Compose file MUST be named `backend`** — the published frontend image bakes `API_URL=http://backend:8000` into its Next.js rewrite manifest at build time.
- **`NEXT_PUBLIC_API_URL` must NOT be set as a build arg** when building the published frontend image. It must stay unset so the browser uses same-origin `/api/*`.
- **Never generate YAML.** Compose files are checked in, static, and valid as-is. Only `.env` is written per-install.
- **Error tracking defaults to off** in the self-host stack: `SENTRY_DSN`, `SENTRY_DSN_BACKEND`, `SENTRY_DSN_FRONTEND` all default to empty. Use the no-colon `${VAR-}` form so unset → empty, never the maintainers' GlitchTip.
- **Secrets never appear in Compose files, workflow logs, or committed examples.** Only in a user's `.env`.
- Backend image for self-host is the **full** ML image (`INSTALL_LOCAL_ML=true`) with `TORCH_VARIANT=cpu`. The slim variant cannot convert documents standalone.
- Existing behavior for Dokploy/Coolify builds must not change. Every new build arg defaults to today's behavior.

## Design refinement vs. the spec

The spec (§4) described the installer regenerating `docker-compose.yml` on update and hash-checking it for user modifications. While mapping tasks, a strictly better structure emerged and this plan implements it instead:

The Compose files are **static release artifacts** using `${VAR}` interpolation. Image tags come from `.env` (`CORTEX_BACKEND_IMAGE=ghcr.io/mocaos/cortex-backend:1.0.0`). Mode is selected by Compose's native `COMPOSE_FILE` variable listing overlay files.

Consequences, all improvements:
- Phase 2 is verifiable with no installer at all — hand-fill `.env`, run `docker compose up -d`.
- "Update" becomes "rewrite three lines in `.env`", not "regenerate and diff YAML".
- User edits to Compose can never be clobbered, so no hash-checking and no warning logic.
- The whole stack is testable with `docker compose config`.

## File Structure

**`mocaOS/cortex-app`**

| Path | Responsibility |
|---|---|
| `package.json` | Version source of truth (0.0.1 → 1.0.0) |
| `scripts/check-version-sync.mjs` | Fails if root, frontend, and a pushed tag disagree |
| `scripts/check-version-sync.test.mjs` | `node:test` coverage for the above |
| `scripts/build-stack-json.mjs` | `stack.template.json` + root version → `stack.json` |
| `scripts/build-stack-json.test.mjs` | `node:test` coverage for the above |
| `selfhost/stack.template.json` | Non-version pins (neo4j, caddy) + the chat version pin |
| `selfhost/docker-compose.yml` | Base stack: neo4j, backend, frontend, chat, backup. No published ports. |
| `selfhost/docker-compose.ports.yml` | Overlay: publish ports on `127.0.0.1` (localhost mode) |
| `selfhost/docker-compose.caddy.yml` | Overlay: add Caddy + its volumes (domain mode) |
| `selfhost/Caddyfile.template` | Two-site reverse proxy, `${VAR}`-driven |
| `selfhost/.env.example` | Every variable the stack reads, documented |
| `selfhost/README.md` | Manual (installer-free) self-host instructions |
| `backend/Dockerfile.prod:30-40` | New `TORCH_VARIANT` build arg |
| `ops/backup/backup.sh:105-118` | Cover skills + apps volumes |
| `frontend/src/app/layout.tsx:38-73` | Read logo URL at runtime, pass down |
| `frontend/src/components/layout/LayoutWrapper.tsx` | Accept + forward `logoUrl` |
| `frontend/src/components/layout/Header.tsx:59-65` | Accept `logoUrl` prop |
| `.github/workflows/release.yml` | Tag-triggered: images + stack.json + release |
| `.github/workflows/ci.yml` | Add CPU-torch smoke job + script tests |

**`mocaOS/cortex-chat`**

| Path | Responsibility |
|---|---|
| `.github/workflows/ci.yml` | New: `npm ci` + `tsc --noEmit` |
| `.github/workflows/release.yml` | New: tag guard + image build/push + release |

---

## Task 1: Version source of truth

Root `package.json` says `0.0.1` while `frontend/package.json` says `1.0.0`. Both must be `1.0.0` and must be mechanically prevented from drifting.

**Files:**
- Modify: `package.json` (root)
- Create: `scripts/check-version-sync.mjs`
- Test: `scripts/check-version-sync.test.mjs`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: nothing.
- Produces: `checkVersionSync({ rootVersion, frontendVersion, tag })` → `{ ok: boolean, problems: string[] }`. Task 5 and the release workflow both call the CLI form. `tag` may be `null` (CI on a branch), in which case only root↔frontend is compared.

- [ ] **Step 1: Write the failing test**

Create `scripts/check-version-sync.test.mjs`:

```javascript
import { test } from "node:test";
import assert from "node:assert/strict";
import { checkVersionSync } from "./check-version-sync.mjs";

test("passes when root and frontend agree and no tag is given", () => {
  const r = checkVersionSync({ rootVersion: "1.0.0", frontendVersion: "1.0.0", tag: null });
  assert.equal(r.ok, true);
  assert.deepEqual(r.problems, []);
});

test("fails when frontend drifts from root", () => {
  const r = checkVersionSync({ rootVersion: "1.0.0", frontendVersion: "0.9.0", tag: null });
  assert.equal(r.ok, false);
  assert.match(r.problems[0], /frontend\/package\.json/);
});

test("passes when the tag matches root", () => {
  const r = checkVersionSync({ rootVersion: "1.0.0", frontendVersion: "1.0.0", tag: "v1.0.0" });
  assert.equal(r.ok, true);
});

test("fails when the tag disagrees with root", () => {
  const r = checkVersionSync({ rootVersion: "1.0.0", frontendVersion: "1.0.0", tag: "v1.1.0" });
  assert.equal(r.ok, false);
  assert.match(r.problems[0], /tag v1\.1\.0/);
});

test("accepts a tag without the v prefix", () => {
  const r = checkVersionSync({ rootVersion: "1.0.0", frontendVersion: "1.0.0", tag: "1.0.0" });
  assert.equal(r.ok, true);
});

test("reports both problems at once", () => {
  const r = checkVersionSync({ rootVersion: "1.0.0", frontendVersion: "0.9.0", tag: "v2.0.0" });
  assert.equal(r.problems.length, 2);
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `node --test scripts/check-version-sync.test.mjs`
Expected: FAIL — `Cannot find module '.../check-version-sync.mjs'`

- [ ] **Step 3: Write the implementation**

Create `scripts/check-version-sync.mjs`:

```javascript
#!/usr/bin/env node
// The root package.json version is the single source of truth for a cortex-app
// release. frontend/package.json must mirror it, and a release tag must match it.
// Run bare in CI (root vs frontend); pass --tag in the release workflow.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

export function checkVersionSync({ rootVersion, frontendVersion, tag }) {
  const problems = [];

  if (rootVersion !== frontendVersion) {
    problems.push(
      `frontend/package.json is ${frontendVersion} but root package.json is ${rootVersion}`
    );
  }

  if (tag != null) {
    const tagVersion = tag.startsWith("v") ? tag.slice(1) : tag;
    if (tagVersion !== rootVersion) {
      problems.push(
        `tag ${tag} does not match root package.json version ${rootVersion}`
      );
    }
  }

  return { ok: problems.length === 0, problems };
}

// CLI entrypoint — skipped when imported by the test file.
if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const repoRoot = join(dirname(fileURLToPath(import.meta.url)), "..");
  const read = (p) => JSON.parse(readFileSync(join(repoRoot, p), "utf8")).version;

  const tagIndex = process.argv.indexOf("--tag");
  const tag = tagIndex === -1 ? null : process.argv[tagIndex + 1];

  const result = checkVersionSync({
    rootVersion: read("package.json"),
    frontendVersion: read("frontend/package.json"),
    tag,
  });

  if (!result.ok) {
    console.error("Version mismatch:");
    for (const p of result.problems) console.error(`  - ${p}`);
    process.exit(1);
  }
  console.log("Versions in sync.");
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `node --test scripts/check-version-sync.test.mjs`
Expected: PASS — 6 tests

- [ ] **Step 5: Bump the root version and wire the npm script**

Edit root `package.json` — change `"version": "0.0.1"` to `"version": "1.0.0"` and add scripts so the block reads:

```json
  "version": "1.0.0",
  "scripts": {
    "test": "cd backend && .venv/bin/python -m pytest",
    "test:scripts": "node --test scripts/",
    "check:versions": "node scripts/check-version-sync.mjs"
  },
```

- [ ] **Step 6: Verify the CLI passes against the real repo**

Run: `npm run check:versions`
Expected: `Versions in sync.` and exit 0

Run: `node scripts/check-version-sync.mjs --tag v1.1.0`
Expected: exit 1, prints `tag v1.1.0 does not match root package.json version 1.0.0`

- [ ] **Step 7: Add the script tests to CI**

In `.github/workflows/ci.yml`, add a new job after `frontend`:

```yaml
  scripts:
    name: Repo scripts (tests + version sync)
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-node@v4
        with:
          node-version: "20"

      - name: Unit tests
        run: node --test scripts/

      - name: Version sync
        run: node scripts/check-version-sync.mjs
```

- [ ] **Step 8: Commit**

```bash
git add package.json scripts/check-version-sync.mjs scripts/check-version-sync.test.mjs .github/workflows/ci.yml
git commit -m "feat(release): make root package.json the version source of truth

Root was 0.0.1 while frontend/package.json said 1.0.0. Both are now 1.0.0,
and check-version-sync.mjs fails CI on drift or on a release tag that
disagrees with the file."
```

---

## Task 2: CPU-only torch build variant

The backend installs `torch` from the default PyPI index, dragging in ~2.5 GB of CUDA wheels into an image that never installs the CUDA runtime nor requests GPU devices. A build arg makes the published self-host image use the CPU wheel index: **~7 GB → ~2 GB**.

Installing CPU torch *before* `requirements-ml.txt` is deliberate — with `--extra-index-url`, pip resolves the highest version across both indexes and would still pick the CUDA build. Satisfying the dependency first is the reliable pattern.

**Files:**
- Modify: `backend/Dockerfile.prod:25-40`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: nothing.
- Produces: build arg `TORCH_VARIANT` accepting `cuda` (default, current behavior) or `cpu`. Task 4's release workflow passes `cpu`.

- [ ] **Step 1: Add the build arg to the Dockerfile**

In `backend/Dockerfile.prod`, replace lines 25-40 with:

```dockerfile
# Build variants:
#   default (INSTALL_LOCAL_ML=true)  — full image with local torch/docling/reranker
#   slim    (INSTALL_LOCAL_ML=false) — no torch (~800MB-1GB smaller); requires
#            OpenAI embeddings + cortex-helper (RERANKER_SERVICE_URL /
#            DOCLING_SERVICE_URL, recommended HELPER_STRICT_REMOTE=true)
ARG INSTALL_LOCAL_ML=true
ARG PREDOWNLOAD_MODELS=true

# torch flavor for the full image:
#   cuda (default) — PyPI wheels, bundles ~2.5GB of nvidia-* deps
#   cpu            — CPU-only wheels, ~2.5GB smaller
# Nothing in this image installs the CUDA runtime or requests GPU devices, so
# every containerized deploy already runs the AcceleratorDevice.AUTO paths on
# CPU. `cpu` is therefore free for Docker deploys and is what the published
# self-host image uses. Default stays `cuda` so existing builds are unchanged.
ARG TORCH_VARIANT=cuda

# Copy requirements first for better caching
COPY requirements.txt requirements-base.txt requirements-ml.txt ./

# Install Python dependencies (base always; ML stack only in the full image).
# CPU torch must be installed BEFORE requirements-ml.txt: with --extra-index-url
# pip resolves the highest version across indexes and would still take the CUDA
# wheel, so we satisfy the dependency from the CPU-only index up front.
RUN pip install --no-cache-dir -r requirements-base.txt && \
    if [ "$INSTALL_LOCAL_ML" = "true" ]; then \
        if [ "$TORCH_VARIANT" = "cpu" ]; then \
            pip install --no-cache-dir \
                --index-url https://download.pytorch.org/whl/cpu \
                torch torchvision; \
        fi; \
        pip install --no-cache-dir -r requirements-ml.txt; \
    fi
```

- [ ] **Step 2: Build the CPU image locally and verify the wheel flavor**

Run:

```bash
docker build -f backend/Dockerfile.prod \
  --build-arg TORCH_VARIANT=cpu \
  --build-arg PREDOWNLOAD_MODELS=false \
  -t cortex-backend-cpu:test backend/
```

Then:

```bash
docker run --rm cortex-backend-cpu:test \
  python -c "import torch; print(torch.__version__)"
```

Expected: a version ending in `+cpu`, e.g. `2.9.1+cpu`

- [ ] **Step 3: Verify no CUDA wheels came along**

Run:

```bash
docker run --rm cortex-backend-cpu:test \
  sh -c 'pip list --format=freeze | grep -c "^nvidia-" || true'
```

Expected: `0`

- [ ] **Step 4: Verify the app still imports and the default is unchanged**

Run:

```bash
docker run --rm -e USE_OPENAI_EMBEDDINGS=true cortex-backend-cpu:test \
  python -c "import app.main; print('cpu image imports OK')"
```

Expected: `cpu image imports OK`

Then confirm the default variant is untouched:

```bash
grep -n 'ARG TORCH_VARIANT=cuda' backend/Dockerfile.prod
```

Expected: one match — the default is `cuda`, so Dokploy/Coolify builds are unaffected.

- [ ] **Step 5: Add a CI smoke job**

In `.github/workflows/ci.yml`, add after the existing `slim-image` job:

```yaml
  cpu-torch-image:
    name: CPU-torch image smoke test
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Build CPU-torch backend image
        # PREDOWNLOAD_MODELS=false keeps CI fast; the release build sets it true.
        run: |
          docker build -f backend/Dockerfile.prod \
            --build-arg TORCH_VARIANT=cpu \
            --build-arg PREDOWNLOAD_MODELS=false \
            -t cortex-backend-cpu:ci backend/

      - name: Assert CPU wheels and no CUDA payload
        run: |
          torch_version=$(docker run --rm cortex-backend-cpu:ci python -c "import torch; print(torch.__version__)")
          echo "torch: $torch_version"
          case "$torch_version" in
            *+cpu) ;;
            *) echo "::error::expected a +cpu torch build, got $torch_version"; exit 1 ;;
          esac

          nvidia_count=$(docker run --rm cortex-backend-cpu:ci sh -c 'pip list --format=freeze | grep -c "^nvidia-" || true')
          echo "nvidia packages: $nvidia_count"
          [ "$nvidia_count" = "0" ] || { echo "::error::CUDA wheels leaked into the CPU image"; exit 1; }

      - name: Import smoke test
        run: |
          docker run --rm -e USE_OPENAI_EMBEDDINGS=true \
            cortex-backend-cpu:ci \
            python -c "import app.main; print('cpu image imports OK')"
```

- [ ] **Step 6: Commit**

```bash
git add backend/Dockerfile.prod .github/workflows/ci.yml
git commit -m "feat(backend): add TORCH_VARIANT build arg for CPU-only wheels

The image never installs the CUDA runtime or requests GPU devices, so the
~2.5GB of nvidia-* wheels torch drags in from PyPI is dead weight for every
containerized deploy. TORCH_VARIANT=cpu installs from the CPU wheel index
first so requirements-ml.txt cannot pull the CUDA build back in.

Default stays cuda — Dokploy/Coolify builds are unchanged. CI asserts the
cpu image reports +cpu and ships zero nvidia-* packages."
```

---

## Task 3: Runtime-configurable logo URL

`Header.tsx:60` reads `process.env.NEXT_PUBLIC_LOGO_URL` from a Client Component, so Next inlines it at build time via the webpack DefinePlugin. On a prebuilt image that value is frozen at publish time and **logo branding silently cannot work**. Fix it the way `layout.tsx:46-50` already handles accent color: read server-side, pass down as a prop.

`LayoutWrapper` sits directly between `layout.tsx` and `Header` — `AuthProvider` wraps it as a parent, so no drilling through AuthProvider's props is needed.

**Files:**
- Modify: `frontend/src/app/layout.tsx:38-73`
- Modify: `frontend/src/components/layout/LayoutWrapper.tsx`
- Modify: `frontend/src/components/layout/Header.tsx:34-66`

**Interfaces:**
- Consumes: nothing.
- Produces: `Header` and `LayoutWrapper` both accept an optional `logoUrl?: string` prop. Resolution order is `LOGO_URL` → `NEXT_PUBLIC_LOGO_URL` → `/logo.svg`, so existing Dokploy deploys that bake `NEXT_PUBLIC_LOGO_URL` keep working unchanged.

- [ ] **Step 1: Read the logo URL at runtime in the root layout**

In `frontend/src/app/layout.tsx`, after the `accentColor` line (currently line 50), add:

```tsx
  // Same runtime-read rationale as accentColor above: LOGO_URL (non-NEXT_PUBLIC_)
  // is read from process.env at request time, so one prebuilt image can be
  // branded per deployment. NEXT_PUBLIC_LOGO_URL stays supported as a fallback
  // for existing deploys that bake it at build time.
  const logoUrl =
    process.env.LOGO_URL || process.env.NEXT_PUBLIC_LOGO_URL || "/logo.svg";
```

Then change the `LayoutWrapper` usage from:

```tsx
            <LayoutWrapper>{children}</LayoutWrapper>
```

to:

```tsx
            <LayoutWrapper logoUrl={logoUrl}>{children}</LayoutWrapper>
```

- [ ] **Step 2: Forward the prop through LayoutWrapper**

In `frontend/src/components/layout/LayoutWrapper.tsx`, change the signature from:

```tsx
export default function LayoutWrapper({
  children,
}: {
  children: React.ReactNode;
}) {
```

to:

```tsx
export default function LayoutWrapper({
  children,
  logoUrl,
}: {
  children: React.ReactNode;
  logoUrl?: string;
}) {
```

and change `<Header />` to `<Header logoUrl={logoUrl} />`.

- [ ] **Step 3: Accept the prop in Header**

In `frontend/src/components/layout/Header.tsx`, change:

```tsx
export default function Header() {
  const pathname = usePathname();
```

to:

```tsx
export default function Header({ logoUrl }: { logoUrl?: string }) {
  const pathname = usePathname();
```

and change the `<img>` src (line 60) from:

```tsx
              src={process.env.NEXT_PUBLIC_LOGO_URL || "/logo.svg"}
```

to:

```tsx
              src={logoUrl || process.env.NEXT_PUBLIC_LOGO_URL || "/logo.svg"}
```

- [ ] **Step 4: Typecheck and lint**

Run:

```bash
cd frontend && npx tsc --noEmit && npm run lint
```

Expected: both clean, no errors.

- [ ] **Step 5: Prove it works at runtime on a prebuilt image**

This is the actual regression being fixed, so verify it the way it breaks — build **without** any logo build arg, then supply it only at run time.

```bash
docker build -f frontend/Dockerfile.prod -t cortex-frontend-logo:test frontend/

docker run -d --name logo-test -p 3999:3000 \
  -e LOGO_URL=https://example.com/custom-logo.png \
  cortex-frontend-logo:test

# Give Next a moment to boot, then check the rendered HTML.
until curl -sf http://localhost:3999/login >/dev/null 2>&1; do sleep 1; done
curl -s http://localhost:3999/documents | grep -o 'https://example.com/custom-logo.png' | head -1

docker rm -f logo-test
```

Expected: `https://example.com/custom-logo.png` is printed. Before this change it would print nothing, because the value was frozen at build time.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/app/layout.tsx frontend/src/components/layout/LayoutWrapper.tsx frontend/src/components/layout/Header.tsx
git commit -m "fix(frontend): make logo URL runtime-configurable

Header is a Client Component, so NEXT_PUBLIC_LOGO_URL was inlined at build
time — meaning a prebuilt image could never be re-branded. Read LOGO_URL
server-side in the root layout and pass it down, the same pattern
layout.tsx already uses for ACCENT_COLOR.

NEXT_PUBLIC_LOGO_URL remains a fallback so deploys that bake it are
unaffected."
```

---

## Task 4: cortex-app release workflow

Tag-triggered multi-arch build and push of the backend and frontend images. Per-arch native builds, then one manifest merge.

**Files:**
- Create: `.github/workflows/release.yml`

**Interfaces:**
- Consumes: `scripts/check-version-sync.mjs --tag` (Task 1), `TORCH_VARIANT` (Task 2).
- Produces: `ghcr.io/mocaos/cortex-{backend,frontend}` tagged `<version>`, `<major>.<minor>`, `<major>`, `latest`. Task 5 adds `stack.json` and the GitHub Release to this same workflow.

- [ ] **Step 1: Create the workflow with the version guard and per-arch builds**

Create `.github/workflows/release.yml`:

```yaml
name: Release

on:
  push:
    tags: ["v*"]

permissions:
  contents: write   # create the GitHub Release
  packages: write   # push to GHCR

env:
  REGISTRY: ghcr.io
  OWNER: mocaos

jobs:
  guard:
    name: Verify tag matches package.json
    runs-on: ubuntu-latest
    outputs:
      version: ${{ steps.v.outputs.version }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: "20"

      - name: Tag must equal root package.json version
        run: node scripts/check-version-sync.mjs --tag "${GITHUB_REF_NAME}"

      - id: v
        run: echo "version=${GITHUB_REF_NAME#v}" >> "$GITHUB_OUTPUT"

  build:
    name: ${{ matrix.component }} · ${{ matrix.arch }}
    needs: guard
    runs-on: ${{ matrix.runner }}
    strategy:
      fail-fast: false
      matrix:
        # Two axes → 4 jobs. `include` matches on the arch value and adds the
        # runner label to each, so amd64 lands on ubuntu-latest and arm64 on
        # the native Arm runner.
        component: [backend, frontend]
        arch: [amd64, arm64]
        include:
          - arch: amd64
            runner: ubuntu-latest
          - arch: arm64
            runner: ubuntu-24.04-arm
    steps:
      - uses: actions/checkout@v4

      - uses: docker/setup-buildx-action@v3

      - uses: docker/login-action@v3
        with:
          registry: ${{ env.REGISTRY }}
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Build and push by digest
        id: push
        uses: docker/build-push-action@v6
        with:
          context: ${{ matrix.component == 'backend' && './backend' || './frontend' }}
          file: ${{ matrix.component == 'backend' && './backend/Dockerfile.prod' || './frontend/Dockerfile.prod' }}
          platforms: linux/${{ matrix.arch }}
          # Backend: full ML image with CPU-only torch (see Dockerfile.prod).
          # Frontend: NEXT_PUBLIC_API_URL is deliberately NOT passed — it must
          # stay unset so the browser uses same-origin /api/* and one image
          # works for every install. API_URL keeps its Dockerfile default of
          # http://backend:8000, which is why the self-host compose must name
          # the backend service `backend`.
          build-args: |
            ${{ matrix.component == 'backend' && 'INSTALL_LOCAL_ML=true' || '' }}
            ${{ matrix.component == 'backend' && 'TORCH_VARIANT=cpu' || '' }}
          cache-from: type=gha,scope=${{ matrix.component }}-${{ matrix.arch }}
          cache-to: type=gha,mode=max,scope=${{ matrix.component }}-${{ matrix.arch }}
          outputs: type=image,name=${{ env.REGISTRY }}/${{ env.OWNER }}/cortex-${{ matrix.component }},push-by-digest=true,name-canonical=true,push=true

      - name: Export digest
        run: |
          mkdir -p /tmp/digests
          echo "${{ steps.push.outputs.digest }}" > "/tmp/digests/${{ matrix.component }}-${{ matrix.arch }}"

      - uses: actions/upload-artifact@v4
        with:
          name: digest-${{ matrix.component }}-${{ matrix.arch }}
          path: /tmp/digests/${{ matrix.component }}-${{ matrix.arch }}
          retention-days: 1

  manifest:
    name: Merge ${{ matrix.component }} manifest
    needs: [guard, build]
    runs-on: ubuntu-latest
    strategy:
      matrix:
        component: [backend, frontend]
    steps:
      - uses: docker/setup-buildx-action@v3

      - uses: docker/login-action@v3
        with:
          registry: ${{ env.REGISTRY }}
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - uses: actions/download-artifact@v4
        with:
          pattern: digest-${{ matrix.component }}-*
          path: /tmp/digests
          merge-multiple: true

      - name: Create and push the multi-arch manifest
        env:
          VERSION: ${{ needs.guard.outputs.version }}
        run: |
          IMAGE="${REGISTRY}/${OWNER}/cortex-${{ matrix.component }}"
          MAJOR="${VERSION%%.*}"
          MINOR="${VERSION%.*}"

          refs=""
          for f in /tmp/digests/*; do
            refs="$refs ${IMAGE}@$(cat "$f")"
          done

          docker buildx imagetools create \
            -t "${IMAGE}:${VERSION}" \
            -t "${IMAGE}:${MINOR}" \
            -t "${IMAGE}:${MAJOR}" \
            -t "${IMAGE}:latest" \
            $refs

          docker buildx imagetools inspect "${IMAGE}:${VERSION}"
```

- [ ] **Step 2: Validate the workflow syntax before pushing a tag**

Run:

```bash
npx --yes action-validator .github/workflows/release.yml 2>/dev/null \
  || python3 -c "import yaml,sys; yaml.safe_load(open('.github/workflows/release.yml')); print('YAML OK')"
```

Expected: `YAML OK` (or action-validator reporting no errors).

- [ ] **Step 3: Verify the matrix expands to exactly four build jobs**

The `strategy.matrix` uses `component: [backend, frontend]` × `arch: [amd64, arm64]` with `include` supplying each arch's runner. Confirm by reading the expansion:

```bash
python3 - <<'PY'
import yaml
wf = yaml.safe_load(open('.github/workflows/release.yml'))
m = wf['jobs']['build']['strategy']['matrix']
print('components:', m['component'])
print('arches:', m['arch'])
print('include:', m['include'])
print('expected build jobs:', len(m['component']) * len(m['arch']))
PY
```

Expected: `expected build jobs: 4`, and `include` maps `amd64 → ubuntu-latest`, `arm64 → ubuntu-24.04-arm`.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/release.yml
git commit -m "ci: add tag-triggered multi-arch release workflow

Builds backend and frontend natively on amd64 and arm64 runners (never QEMU
— emulated torch builds take hours), pushes by digest, then merges one
manifest per component tagged version/minor/major/latest.

Backend ships INSTALL_LOCAL_ML=true + TORCH_VARIANT=cpu. Frontend
deliberately omits NEXT_PUBLIC_API_URL so one image works for every install."
```

---

## Task 5: stack.json generation and the GitHub Release

`stack.json` is the single file the installer reads. It pins the tested component set and is attached as a release asset.

**Files:**
- Create: `selfhost/stack.template.json`
- Create: `scripts/build-stack-json.mjs`
- Test: `scripts/build-stack-json.test.mjs`
- Modify: `.github/workflows/release.yml`

**Interfaces:**
- Consumes: root `package.json` version, `selfhost/stack.template.json`.
- Produces: `buildStackJson({ version, template })` → the stack manifest object. Plan B's installer parses exactly this shape: `{ stack, components: { backend, frontend, chat, neo4j, caddy }, minInstaller, notes }`.

- [ ] **Step 1: Write the failing test**

Create `scripts/build-stack-json.test.mjs`:

```javascript
import { test } from "node:test";
import assert from "node:assert/strict";
import { buildStackJson } from "./build-stack-json.mjs";

const template = {
  components: { chat: "1.0.0", neo4j: "5.26-community", caddy: "2-alpine" },
  minInstaller: "1.0.0",
};

test("stack version comes from the app version", () => {
  const s = buildStackJson({ version: "1.2.3", template });
  assert.equal(s.stack, "1.2.3");
});

test("backend and frontend are pinned to the app version", () => {
  const s = buildStackJson({ version: "1.2.3", template });
  assert.equal(s.components.backend, "1.2.3");
  assert.equal(s.components.frontend, "1.2.3");
});

test("chat, neo4j and caddy come from the template, not the app version", () => {
  const s = buildStackJson({ version: "1.2.3", template });
  assert.equal(s.components.chat, "1.0.0");
  assert.equal(s.components.neo4j, "5.26-community");
  assert.equal(s.components.caddy, "2-alpine");
});

test("minInstaller is carried through", () => {
  const s = buildStackJson({ version: "1.2.3", template });
  assert.equal(s.minInstaller, "1.0.0");
});

test("notes links to the matching release tag", () => {
  const s = buildStackJson({ version: "1.2.3", template });
  assert.equal(
    s.notes,
    "https://github.com/mocaOS/cortex-app/releases/tag/v1.2.3"
  );
});

test("throws when the template omits a required component", () => {
  const bad = { components: { chat: "1.0.0" }, minInstaller: "1.0.0" };
  assert.throws(
    () => buildStackJson({ version: "1.0.0", template: bad }),
    /neo4j/
  );
});

test("throws when minInstaller is missing", () => {
  const bad = { components: { chat: "1.0.0", neo4j: "5.26-community", caddy: "2-alpine" } };
  assert.throws(
    () => buildStackJson({ version: "1.0.0", template: bad }),
    /minInstaller/
  );
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `node --test scripts/build-stack-json.test.mjs`
Expected: FAIL — `Cannot find module '.../build-stack-json.mjs'`

- [ ] **Step 3: Write the implementation**

Create `scripts/build-stack-json.mjs`:

```javascript
#!/usr/bin/env node
// Generates stack.json — the manifest the self-host installer reads to learn
// which component versions make up one tested stack.
//
// backend and frontend always track this repo's version (they are built from
// it). chat, neo4j and caddy are pinned in selfhost/stack.template.json so a
// chat-only release can ship a new stack without republishing a 2GB backend.
import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const TEMPLATE_COMPONENTS = ["chat", "neo4j", "caddy"];

export function buildStackJson({ version, template }) {
  const fromTemplate = template?.components ?? {};

  for (const name of TEMPLATE_COMPONENTS) {
    if (!fromTemplate[name]) {
      throw new Error(`stack.template.json is missing components.${name}`);
    }
  }
  if (!template?.minInstaller) {
    throw new Error("stack.template.json is missing minInstaller");
  }

  return {
    stack: version,
    components: {
      backend: version,
      frontend: version,
      chat: fromTemplate.chat,
      neo4j: fromTemplate.neo4j,
      caddy: fromTemplate.caddy,
    },
    minInstaller: template.minInstaller,
    notes: `https://github.com/mocaOS/cortex-app/releases/tag/v${version}`,
  };
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const repoRoot = join(dirname(fileURLToPath(import.meta.url)), "..");
  const readJson = (p) => JSON.parse(readFileSync(join(repoRoot, p), "utf8"));

  const stack = buildStackJson({
    version: readJson("package.json").version,
    template: readJson("selfhost/stack.template.json"),
  });

  const outIndex = process.argv.indexOf("--out");
  const out = outIndex === -1 ? "stack.json" : process.argv[outIndex + 1];

  writeFileSync(out, `${JSON.stringify(stack, null, 2)}\n`);
  console.log(`Wrote ${out}:`);
  console.log(JSON.stringify(stack, null, 2));
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `node --test scripts/build-stack-json.test.mjs`
Expected: PASS — 7 tests

- [ ] **Step 5: Create the template**

Create `selfhost/stack.template.json`:

```json
{
  "_comment": "Pins for components NOT built from this repo. backend and frontend always track this repo's root package.json version and are injected by scripts/build-stack-json.mjs. Bump `chat` here when a new cortex-chat release should become part of the stack — the image must already exist on GHCR before this repo is tagged.",
  "components": {
    "chat": "1.0.0",
    "neo4j": "5.26-community",
    "caddy": "2-alpine"
  },
  "minInstaller": "1.0.0"
}
```

- [ ] **Step 6: Verify the real generator output**

Run: `node scripts/build-stack-json.mjs --out /tmp/stack.json && cat /tmp/stack.json`

Expected:

```json
{
  "stack": "1.0.0",
  "components": {
    "backend": "1.0.0",
    "frontend": "1.0.0",
    "chat": "1.0.0",
    "neo4j": "5.26-community",
    "caddy": "2-alpine"
  },
  "minInstaller": "1.0.0",
  "notes": "https://github.com/mocaOS/cortex-app/releases/tag/v1.0.0"
}
```

- [ ] **Step 7: Add the release job to the workflow**

Append to `.github/workflows/release.yml`:

```yaml
  release:
    name: Publish GitHub Release
    needs: [guard, manifest]
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: "20"

      - name: Generate stack.json
        run: node scripts/build-stack-json.mjs --out stack.json

      - name: Verify every pinned image is actually pullable
        # A stack.json pointing at a nonexistent image would break every
        # installer run. cortex-chat must be released BEFORE this repo is
        # tagged; this step is what enforces that ordering.
        run: |
          set -e
          for entry in \
            "ghcr.io/mocaos/cortex-backend:$(jq -r .components.backend stack.json)" \
            "ghcr.io/mocaos/cortex-frontend:$(jq -r .components.frontend stack.json)" \
            "ghcr.io/mocaos/cortex-chat:$(jq -r .components.chat stack.json)" \
            "neo4j:$(jq -r .components.neo4j stack.json)" \
            "caddy:$(jq -r .components.caddy stack.json)"
          do
            echo "checking $entry"
            docker buildx imagetools inspect "$entry" > /dev/null \
              || { echo "::error::$entry is not pullable"; exit 1; }
          done

      - name: Create the release
        uses: softprops/action-gh-release@v2
        with:
          generate_release_notes: true
          files: stack.json
```

- [ ] **Step 8: Commit**

```bash
git add selfhost/stack.template.json scripts/build-stack-json.mjs scripts/build-stack-json.test.mjs .github/workflows/release.yml
git commit -m "feat(release): generate and publish stack.json

stack.json is the one file the self-host installer reads. backend and
frontend track this repo's version; chat/neo4j/caddy are pinned in
selfhost/stack.template.json so a chat-only fix can ship a new stack
without republishing a 2GB backend image.

The release job refuses to publish a manifest whose pinned images are not
pullable, which is what enforces releasing cortex-chat first."
```

---

## Task 6: cortex-chat CI

cortex-chat has no CI at all. Add the minimum that stops a broken build becoming a published image.

**Repo:** `mocaOS/cortex-chat` (`/Volumes/WD_BLACK/PROJECTS/CORTEX/cortex-chat`)

**Files:**
- Create: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: nothing.
- Produces: nothing consumed by later tasks; a gate only.

- [ ] **Step 1: Confirm the typecheck passes locally first**

Run:

```bash
cd /Volumes/WD_BLACK/PROJECTS/CORTEX/cortex-chat && npm ci && npx tsc --noEmit
```

Expected: clean exit. If it fails, fix the type errors before adding CI — a workflow that lands red is worse than none.

- [ ] **Step 2: Create the workflow**

Create `.github/workflows/ci.yml`:

```yaml
name: CI

on:
  pull_request:
  push:
    branches: [main]

concurrency:
  group: ci-${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true

jobs:
  typecheck:
    name: Typecheck
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-node@v4
        with:
          node-version: "20"
          cache: npm

      - name: Install dependencies
        run: npm ci

      - name: Typecheck
        run: npx tsc --noEmit
```

- [ ] **Step 3: Validate the workflow YAML**

Run:

```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml')); print('YAML OK')"
```

Expected: `YAML OK`

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: add typecheck workflow

This repo had no CI. A broken build would otherwise only surface when the
release workflow builds the image — later and more noisily."
```

---

## Task 7: cortex-chat release workflow

**Repo:** `mocaOS/cortex-chat`

**Files:**
- Create: `.github/workflows/release.yml`

**Interfaces:**
- Consumes: root `package.json` version.
- Produces: `ghcr.io/mocaos/cortex-chat` tagged `<version>`, `<major>.<minor>`, `<major>`, `latest`. Task 5's `stack.template.json` pins the version this produces.

- [ ] **Step 1: Create the workflow**

Create `.github/workflows/release.yml`:

```yaml
name: Release

on:
  push:
    tags: ["v*"]

permissions:
  contents: write
  packages: write

env:
  REGISTRY: ghcr.io
  IMAGE: ghcr.io/mocaos/cortex-chat

jobs:
  guard:
    name: Verify tag matches package.json
    runs-on: ubuntu-latest
    outputs:
      version: ${{ steps.v.outputs.version }}
    steps:
      - uses: actions/checkout@v4

      - name: Tag must equal package.json version
        run: |
          pkg=$(node -p "require('./package.json').version")
          tag="${GITHUB_REF_NAME#v}"
          echo "package.json=$pkg tag=$tag"
          [ "$pkg" = "$tag" ] || {
            echo "::error::tag ${GITHUB_REF_NAME} does not match package.json version $pkg"
            exit 1
          }

      - id: v
        run: echo "version=${GITHUB_REF_NAME#v}" >> "$GITHUB_OUTPUT"

  build:
    name: Build ${{ matrix.arch }}
    needs: guard
    runs-on: ${{ matrix.runner }}
    strategy:
      fail-fast: false
      matrix:
        include:
          - arch: amd64
            runner: ubuntu-latest
          - arch: arm64
            runner: ubuntu-24.04-arm
    steps:
      - uses: actions/checkout@v4

      - uses: docker/setup-buildx-action@v3

      - uses: docker/login-action@v3
        with:
          registry: ${{ env.REGISTRY }}
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Build and push by digest
        id: push
        uses: docker/build-push-action@v6
        with:
          context: .
          file: ./Dockerfile
          platforms: linux/${{ matrix.arch }}
          # All app config is runtime env — the only build args are the
          # optional GlitchTip source-map upload, which is skipped here.
          cache-from: type=gha,scope=chat-${{ matrix.arch }}
          cache-to: type=gha,mode=max,scope=chat-${{ matrix.arch }}
          outputs: type=image,name=${{ env.IMAGE }},push-by-digest=true,name-canonical=true,push=true

      - name: Export digest
        run: |
          mkdir -p /tmp/digests
          echo "${{ steps.push.outputs.digest }}" > "/tmp/digests/${{ matrix.arch }}"

      - uses: actions/upload-artifact@v4
        with:
          name: digest-${{ matrix.arch }}
          path: /tmp/digests/${{ matrix.arch }}
          retention-days: 1

  manifest:
    name: Merge manifest and release
    needs: [guard, build]
    runs-on: ubuntu-latest
    steps:
      - uses: docker/setup-buildx-action@v3

      - uses: docker/login-action@v3
        with:
          registry: ${{ env.REGISTRY }}
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - uses: actions/download-artifact@v4
        with:
          pattern: digest-*
          path: /tmp/digests
          merge-multiple: true

      - name: Create and push the multi-arch manifest
        env:
          VERSION: ${{ needs.guard.outputs.version }}
        run: |
          MAJOR="${VERSION%%.*}"
          MINOR="${VERSION%.*}"

          refs=""
          for f in /tmp/digests/*; do
            refs="$refs ${IMAGE}@$(cat "$f")"
          done

          docker buildx imagetools create \
            -t "${IMAGE}:${VERSION}" \
            -t "${IMAGE}:${MINOR}" \
            -t "${IMAGE}:${MAJOR}" \
            -t "${IMAGE}:latest" \
            $refs

          docker buildx imagetools inspect "${IMAGE}:${VERSION}"

      - name: Create the release
        uses: softprops/action-gh-release@v2
        with:
          generate_release_notes: true
```

- [ ] **Step 2: Validate the workflow YAML**

Run:

```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/release.yml')); print('YAML OK')"
```

Expected: `YAML OK`

- [ ] **Step 3: Verify the Dockerfile needs no build args**

Run:

```bash
grep -n "^ARG" Dockerfile
```

Expected: only `SENTRY_AUTH_TOKEN` and `SOURCE_COMMIT`, both optional and scoped to the builder stage. Confirms the image is fully runtime-configured and therefore portable as published.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/release.yml
git commit -m "ci: add tag-triggered multi-arch release workflow

Builds natively on amd64 and arm64, merges one manifest, publishes to GHCR
and creates the GitHub Release. Guards that the tag matches package.json.

This repo must be released before cortex-app, whose stack.json pins the
chat version and verifies it is pullable."
```

---

## Task 8: Back up skills and apps

`backup.sh:107-112` names its source directories explicitly, so **user-installed skills and apps are silently never backed up**. Two more guarded entries fix it, backward-compatibly.

**Repo:** `mocaOS/cortex-app`

**Files:**
- Modify: `ops/backup/backup.sh:105-118`

**Interfaces:**
- Consumes: nothing.
- Produces: the backup container now also archives `/data/skills` and `/data/apps` when mounted. Task 9's Compose file mounts them.

- [ ] **Step 1: Extend the archive block**

In `ops/backup/backup.sh`, replace the file-volumes block (lines 105-118) with:

```bash
# File volumes (mounted read-only into this container). /data/chat carries
# the cortex-chat SQLite DB + assets on tenant stacks that run the chat service.
# /data/skills and /data/apps carry user-installed AgentSkills and apps — these
# are user-authored content with no other copy, so losing them is real data loss.
# Every path is existence-guarded, so stacks that do not mount one are
# unaffected (the Dokploy compose currently mounts neither skills nor apps).
if [ -d /data/uploads ] || [ -d /data/custom_inputs ] || [ -d /data/chat ] \
   || [ -d /data/skills ] || [ -d /data/apps ]; then
    rc=0
    tar -czf "$dest/files.tar.gz" \
        $( [ -d /data/uploads ] && echo /data/uploads ) \
        $( [ -d /data/custom_inputs ] && echo /data/custom_inputs ) \
        $( [ -d /data/chat ] && echo /data/chat ) \
        $( [ -d /data/skills ] && echo /data/skills ) \
        $( [ -d /data/apps ] && echo /data/apps ) \
        || rc=$?
    # GNU tar exit 1 = "file changed while reading" (live volume) — acceptable;
    # >=2 is a real failure and must not be swallowed.
    [ "$rc" -le 1 ] || fail "file-volume archive failed (tar exit $rc)"
    echo "[backup] file volumes archived"
fi
```

- [ ] **Step 2: Update the script header comment**

At `ops/backup/backup.sh:7`, change:

```
#   directory) + tar of the uploads/custom_inputs/chat volumes. No downtime,
```

to:

```
#   directory) + tar of the uploads/custom_inputs/chat/skills/apps volumes.
#   No downtime,
```

- [ ] **Step 3: Verify the new paths are archived when mounted**

Build the backup image and run just the archive logic against fixture directories:

```bash
docker build -t cortex-backup:test --build-arg NEO4J_IMAGE=neo4j:5.26-community ops/backup/

rm -rf /tmp/bk && mkdir -p /tmp/bk/{uploads,skills,apps,out}
echo doc  > /tmp/bk/uploads/a.txt
echo skill> /tmp/bk/skills/my-skill.md
echo app  > /tmp/bk/apps/my-app.json

docker run --rm \
  -v /tmp/bk/uploads:/data/uploads:ro \
  -v /tmp/bk/skills:/data/skills:ro \
  -v /tmp/bk/apps:/data/apps:ro \
  -v /tmp/bk/out:/out \
  --entrypoint sh cortex-backup:test -c '
    tar -czf /out/files.tar.gz \
      $( [ -d /data/uploads ] && echo /data/uploads ) \
      $( [ -d /data/custom_inputs ] && echo /data/custom_inputs ) \
      $( [ -d /data/chat ] && echo /data/chat ) \
      $( [ -d /data/skills ] && echo /data/skills ) \
      $( [ -d /data/apps ] && echo /data/apps )
  '

tar -tzf /tmp/bk/out/files.tar.gz | sort
```

Expected output includes all three trees and no error about the unmounted ones:

```
data/apps/
data/apps/my-app.json
data/skills/
data/skills/my-skill.md
data/uploads/
data/uploads/a.txt
```

- [ ] **Step 4: Verify backward compatibility with none of the new paths mounted**

```bash
docker run --rm \
  -v /tmp/bk/uploads:/data/uploads:ro \
  -v /tmp/bk/out:/out \
  --entrypoint sh cortex-backup:test -c '
    tar -czf /out/legacy.tar.gz \
      $( [ -d /data/uploads ] && echo /data/uploads ) \
      $( [ -d /data/skills ] && echo /data/skills ) \
      $( [ -d /data/apps ] && echo /data/apps )
  '
tar -tzf /tmp/bk/out/legacy.tar.gz | sort
```

Expected: only the `data/uploads/` entries, exit 0. Proves an existing Dokploy stack that mounts neither path is unaffected.

- [ ] **Step 5: Commit**

```bash
git add ops/backup/backup.sh
git commit -m "fix(backup): archive installed skills and apps

backup.sh named its source directories explicitly, so user-installed
AgentSkills and apps — user-authored content with no other copy — were
silently excluded from every backup.

Both new paths use the existing existence-guard pattern, so stacks that do
not mount them (including the current Dokploy compose) are unaffected."
```

---

## Task 9: The self-host Compose stack

Static, valid Compose files driven by `${VAR}` interpolation. A base file plus two overlays selected via Compose's native `COMPOSE_FILE`. No YAML is ever generated.

**Repo:** `mocaOS/cortex-app`

**Files:**
- Create: `selfhost/docker-compose.yml`
- Create: `selfhost/docker-compose.ports.yml`
- Create: `selfhost/docker-compose.caddy.yml`
- Create: `selfhost/Caddyfile.template`
- Create: `selfhost/.env.example`

**Interfaces:**
- Consumes: images from Tasks 4 and 7; `backup.sh` from Task 8.
- Produces: the artifact set Plan B's installer downloads and drives. The installer writes only `.env`. Mode is selected by `COMPOSE_FILE`:
  - localhost → `docker-compose.yml:docker-compose.ports.yml`
  - domain → `docker-compose.yml:docker-compose.caddy.yml`

- [ ] **Step 1: Create the base Compose file**

Create `selfhost/docker-compose.yml`:

```yaml
# Cortex — self-host stack.
#
# This file is a STATIC release artifact. Everything is configured through .env
# interpolation; nothing here is generated or rewritten by the installer, so
# your edits survive updates. Local changes belong in docker-compose.override.yml,
# which Compose merges automatically.
#
# Mode is selected by COMPOSE_FILE in .env:
#   localhost      COMPOSE_FILE=docker-compose.yml:docker-compose.ports.yml
#   public domain  COMPOSE_FILE=docker-compose.yml:docker-compose.caddy.yml
#
# The backend service MUST stay named `backend`: the published frontend image
# bakes API_URL=http://backend:8000 into its Next.js rewrite manifest at build
# time, and renaming this service breaks every browser request.

services:
  neo4j:
    image: neo4j:${NEO4J_VERSION:-5.26-community}
    environment:
      # Username is hardcoded to 'neo4j' — do not set NEO4J_USER.
      - NEO4J_AUTH=neo4j/${NEO4J_PASSWORD:?NEO4J_PASSWORD is required}
      - NEO4J_ACCEPT_LICENSE_AGREEMENT=yes
      - NEO4J_PLUGINS=["apoc"]
      - NEO4J_dbms_security_procedures_unrestricted=apoc.*
      - NEO4J_dbms_memory_heap_initial__size=${CORTEX_NEO4J_HEAP_INITIAL:-512m}
      - NEO4J_dbms_memory_heap_max__size=${CORTEX_NEO4J_HEAP_MAX:-2G}
      - NEO4J_dbms_memory_pagecache_size=${CORTEX_NEO4J_PAGECACHE:-512m}
      - NEO4J_db_transaction_timeout=${CORTEX_NEO4J_TX_TIMEOUT:-300s}
      # Required by the backup sidecar: APOC writes the export server-side.
      - NEO4J_apoc_export_file_enabled=true
    mem_limit: ${CORTEX_NEO4J_MEM_LIMIT:-4g}
    volumes:
      - neo4j_data:/data
      - neo4j_logs:/logs
      - backups:/var/lib/neo4j/import
    healthcheck:
      test: ["CMD-SHELL", "wget --no-verbose --tries=1 --spider http://localhost:7474 || exit 1"]
      interval: 10s
      timeout: 5s
      retries: 5
    restart: unless-stopped

  backend:
    image: ${CORTEX_BACKEND_IMAGE:?CORTEX_BACKEND_IMAGE is required}
    environment:
      - ENVIRONMENT=production
      - NEO4J_URI=bolt://neo4j:7687
      - NEO4J_PASSWORD=${NEO4J_PASSWORD}
      - OPENAI_API_KEY=${OPENAI_API_KEY:?OPENAI_API_KEY is required}
      - OPENAI_API_BASE=${OPENAI_API_BASE}
      - OPENAI_MODEL=${OPENAI_MODEL}
      - OPENAI_MAX_CONTEXT=${OPENAI_MAX_CONTEXT:-}
      - USE_OPENAI_EMBEDDINGS=${USE_OPENAI_EMBEDDINGS:-true}
      - EMBEDDING_MODEL=${EMBEDDING_MODEL}
      - EMBEDDING_DIMENSION=${EMBEDDING_DIMENSION}
      - EMBEDDING_SEND_DIMENSIONS=${EMBEDDING_SEND_DIMENSIONS:-true}
      - EMBEDDING_API_BASE=${EMBEDDING_API_BASE:-}
      - EMBEDDING_API_KEY=${EMBEDDING_API_KEY:-}
      - GRAPH_EXTRACTION_MODEL=${GRAPH_EXTRACTION_MODEL:-}
      - VISION_MODEL=${VISION_MODEL:-}
      - ENABLE_RERANKING=${ENABLE_RERANKING:-true}
      - BATCH_PROCESSING_CONCURRENCY=${BATCH_PROCESSING_CONCURRENCY:-2}
      - ADMIN_EMAIL=${ADMIN_EMAIL:-admin@example.com}
      - ADMIN_PASSWORD=${ADMIN_PASSWORD:?ADMIN_PASSWORD is required}
      - ADMIN_API_KEY=${ADMIN_API_KEY:?ADMIN_API_KEY is required}
      - SESSION_SECRET=${SESSION_SECRET:?SESSION_SECRET is required}
      - CORS_ALLOWED_ORIGINS=${CORS_ALLOWED_ORIGINS:-*}
      - ENABLE_APPS=${ENABLE_APPS:-true}
      # Error tracking is OFF by default for self-hosted instances. The no-colon
      # `-` form means unset => empty => disabled; set a DSN to opt in.
      # Self-hosters' stack traces must never reach the maintainers uninvited.
      - SENTRY_DSN=${SENTRY_DSN_BACKEND-}
      - SENTRY_ENVIRONMENT=${SENTRY_ENVIRONMENT:-}
    volumes:
      - uploads_data:/app/uploads
      - custom_inputs_data:/app/custom_inputs
      - skills_data:/app/.agents/skills
      - apps_data:/app/.agents/apps
      - hf_cache:/app/.cache/huggingface
    depends_on:
      neo4j:
        condition: service_healthy
    restart: unless-stopped
    mem_limit: ${BACKEND_MEM_LIMIT:-4g}
    healthcheck:
      test: ["CMD-SHELL", "curl -fsS http://localhost:8000/health || exit 1"]
      interval: 15s
      timeout: 10s
      retries: 10
      start_period: 90s

  frontend:
    image: ${CORTEX_FRONTEND_IMAGE:?CORTEX_FRONTEND_IMAGE is required}
    environment:
      # NEXT_PUBLIC_API_URL is deliberately unset: the browser calls same-origin
      # /api/* and the Next server proxies to API_URL. That is what makes one
      # prebuilt image work for localhost and for every domain.
      - API_URL=http://backend:8000
      - ACCENT_COLOR=${ACCENT_COLOR:-}
      - LOGO_URL=${LOGO_URL:-}
      - ADMIN_EMAIL=${ADMIN_EMAIL:-admin@example.com}
      - ADMIN_PASSWORD=${ADMIN_PASSWORD}
      - ADMIN_API_KEY=${ADMIN_API_KEY}
      - SESSION_SECRET=${SESSION_SECRET}
      # Browsers drop Secure cookies over plain HTTP, so localhost mode must
      # set this false or admin login fails with no visible error.
      - SESSION_COOKIE_SECURE=${SESSION_COOKIE_SECURE:-}
      - SENTRY_DSN=${SENTRY_DSN_FRONTEND-}
      - SENTRY_ENVIRONMENT=${SENTRY_ENVIRONMENT:-}
    depends_on:
      - backend
    restart: unless-stopped
    mem_limit: ${FRONTEND_MEM_LIMIT:-1g}

  chat:
    image: ${CORTEX_CHAT_IMAGE:?CORTEX_CHAT_IMAGE is required}
    environment:
      - CORTEX_API_URL=http://backend:8000
      # Chat reuses the Cortex admin identity so one login works in both apps.
      - BACKEND_ADMIN_API_KEY=${ADMIN_API_KEY}
      - SUPERADMIN_EMAIL=${ADMIN_EMAIL:-admin@example.com}
      - SUPERADMIN_PASSWORD=${ADMIN_PASSWORD}
      - APP_ENCRYPTION_KEY=${CHAT_APP_ENCRYPTION_KEY:?CHAT_APP_ENCRYPTION_KEY is required}
      - ENABLE_REGISTRATION=${ENABLE_REGISTRATION:-}
      - APP_BASE_URL=${CHAT_BASE_URL:-}
      - SMTP_HOST=${SMTP_HOST:-}
      - SMTP_PORT=${SMTP_PORT:-587}
      - SMTP_USER=${SMTP_USER:-}
      - SMTP_PASS=${SMTP_PASS:-}
      - SMTP_SECURE=${SMTP_SECURE:-false}
      - SMTP_FROM=${SMTP_FROM:-}
      # SENTRY_DISABLED is chat's kill switch — its DSN is baked into the image,
      # so an env override alone is not enough to keep self-hosters' errors in.
      - SENTRY_DISABLED=${CHAT_SENTRY_DISABLED:-1}
      - SENTRY_ENVIRONMENT=${SENTRY_ENVIRONMENT:-}
    volumes:
      - chat_data:/app/data
    depends_on:
      - backend
    restart: unless-stopped
    mem_limit: ${CHAT_MEM_LIMIT:-1g}

  backup:
    build:
      context: ./ops/backup
      args:
        - NEO4J_IMAGE=neo4j:${NEO4J_VERSION:-5.26-community}
    restart: unless-stopped
    mem_limit: 512m
    environment:
      - NEO4J_ADDRESS=neo4j://neo4j:7687
      - NEO4J_USER=neo4j
      - NEO4J_PASSWORD=${NEO4J_PASSWORD}
      - BACKUP_INTERVAL_SECONDS=${BACKUP_INTERVAL_SECONDS:-86400}
      - BACKUP_RETENTION_DAYS=${BACKUP_RETENTION_DAYS:-7}
      - BACKUP_INITIAL_DELAY_SECONDS=${BACKUP_INITIAL_DELAY_SECONDS:-120}
    volumes:
      - backups:/backups
      - uploads_data:/data/uploads:ro
      - custom_inputs_data:/data/custom_inputs:ro
      - chat_data:/data/chat:ro
      - skills_data:/data/skills:ro
      - apps_data:/data/apps:ro
    healthcheck:
      test:
        [
          "CMD-SHELL",
          "test -f /backups/LAST_SUCCESS && [ $$(( $$(date +%s) - $$(cat /backups/LAST_SUCCESS) )) -lt $$(( $${BACKUP_INTERVAL_SECONDS:-86400} * 2 )) ]",
        ]
      interval: 15m
      timeout: 10s
      retries: 2
      start_period: 2h
    depends_on:
      neo4j:
        condition: service_healthy

volumes:
  neo4j_data:
  neo4j_logs:
  uploads_data:
  custom_inputs_data:
  skills_data:
  apps_data:
  hf_cache:
  chat_data:
  backups:
```

- [ ] **Step 2: Create the localhost ports overlay**

Create `selfhost/docker-compose.ports.yml`:

```yaml
# Localhost mode — publish ports on the loopback interface only.
#
# Binding to 127.0.0.1 rather than 0.0.0.0 is deliberate: on a VPS, 0.0.0.0
# would expose an unauthenticated Neo4j browser and the backend API to the
# whole internet the moment the stack starts.

services:
  frontend:
    ports:
      - "${BIND_ADDR:-127.0.0.1}:${APP_PORT:-3000}:3000"

  chat:
    ports:
      - "${BIND_ADDR:-127.0.0.1}:${CHAT_PORT:-3001}:3000"

  backend:
    ports:
      - "${BIND_ADDR:-127.0.0.1}:${API_PORT:-8000}:8000"

  neo4j:
    ports:
      - "${BIND_ADDR:-127.0.0.1}:${NEO4J_HTTP_PORT:-7474}:7474"
      - "${BIND_ADDR:-127.0.0.1}:${NEO4J_BOLT_PORT:-7687}:7687"
```

- [ ] **Step 3: Create the Caddy overlay**

Create `selfhost/docker-compose.caddy.yml`:

```yaml
# Public-domain mode — Caddy terminates TLS with automatic Let's Encrypt.
#
# Only Caddy publishes ports. Nothing else is reachable from outside the
# Compose network, so the backend API and Neo4j browser stay private.

services:
  caddy:
    image: caddy:${CADDY_VERSION:-2-alpine}
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
      - "443:443/udp"
    environment:
      - APP_DOMAIN=${APP_DOMAIN:?APP_DOMAIN is required in domain mode}
      - CHAT_DOMAIN=${CHAT_DOMAIN:?CHAT_DOMAIN is required in domain mode}
      - ACME_EMAIL=${ACME_EMAIL:-}
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy_data:/data
      - caddy_config:/config
    depends_on:
      - frontend
      - chat

volumes:
  caddy_data:
  caddy_config:
```

- [ ] **Step 4: Create the Caddyfile template**

Create `selfhost/Caddyfile.template`:

```
# Cortex — Caddy reverse proxy.
#
# Caddy substitutes {$VAR} from its container environment at load time, so this
# file is static and needs no rendering. Copy it to ./Caddyfile as-is.
#
# The backend needs no domain of its own: the frontend proxies /api/*, /apps/*
# and /a/* to it, so the API is reachable at https://{$APP_DOMAIN}/api/...

{
	email {$ACME_EMAIL}
}

{$APP_DOMAIN} {
	reverse_proxy frontend:3000
}

{$CHAT_DOMAIN} {
	reverse_proxy chat:3000
}
```

- [ ] **Step 5: Create the documented .env example**

Create `selfhost/.env.example`:

```dotenv
# Cortex self-host configuration.
# Copy to .env and fill in. Keep it chmod 600 — it holds every secret.

# --- Mode ---------------------------------------------------------------
# Localhost:      docker-compose.yml:docker-compose.ports.yml
# Public domain:  docker-compose.yml:docker-compose.caddy.yml
COMPOSE_FILE=docker-compose.yml:docker-compose.ports.yml
COMPOSE_PROJECT_NAME=cortex

# --- Images (pinned from stack.json) ------------------------------------
CORTEX_BACKEND_IMAGE=ghcr.io/mocaos/cortex-backend:1.0.0
CORTEX_FRONTEND_IMAGE=ghcr.io/mocaos/cortex-frontend:1.0.0
CORTEX_CHAT_IMAGE=ghcr.io/mocaos/cortex-chat:1.0.0
NEO4J_VERSION=5.26-community
CADDY_VERSION=2-alpine

# --- Localhost mode -----------------------------------------------------
# 127.0.0.1 keeps the stack off the network. Change only if you know why.
BIND_ADDR=127.0.0.1
APP_PORT=3000
CHAT_PORT=3001
API_PORT=8000
NEO4J_HTTP_PORT=7474
NEO4J_BOLT_PORT=7687
# Browsers drop Secure cookies over plain HTTP — required for localhost login.
SESSION_COOKIE_SECURE=false

# --- Public domain mode -------------------------------------------------
# Both must already have A records pointing at this host before first start,
# or Let's Encrypt issuance fails.
# APP_DOMAIN=cortex.example.com
# CHAT_DOMAIN=chat.example.com
# ACME_EMAIL=you@example.com
# CHAT_BASE_URL=https://chat.example.com
# CORS_ALLOWED_ORIGINS=https://cortex.example.com,https://chat.example.com

# --- Secrets ------------------------------------------------------------
# Generate with:
#   NEO4J_PASSWORD              openssl rand -base64 32 | tr -d '/+=' | head -c 32
#   ADMIN_API_KEY               echo "cortex_admin_$(openssl rand -hex 32)"
#   SESSION_SECRET              openssl rand -hex 48
#   CHAT_APP_ENCRYPTION_KEY     openssl rand -base64 32
# The backend refuses to boot in production with a known placeholder value.
NEO4J_PASSWORD=
ADMIN_EMAIL=admin@example.com
ADMIN_PASSWORD=
ADMIN_API_KEY=
SESSION_SECRET=
CHAT_APP_ENCRYPTION_KEY=

# --- LLM ----------------------------------------------------------------
OPENAI_API_KEY=
OPENAI_API_BASE=https://api.openai.com/v1
OPENAI_MODEL=gpt-5.2
# OPENAI_MAX_CONTEXT=

# --- Embeddings ---------------------------------------------------------
# EMBEDDING_DIMENSION is baked into the Neo4j vector index on first use.
# Changing it later forces a full re-embed of the corpus — get it right now.
USE_OPENAI_EMBEDDINGS=true
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIMENSION=1536
EMBEDDING_SEND_DIMENSIONS=true
# EMBEDDING_API_BASE=
# EMBEDDING_API_KEY=

# --- Optional model overrides -------------------------------------------
# GRAPH_EXTRACTION_MODEL=
# VISION_MODEL=
# ENABLE_RERANKING=true

# --- Branding (runtime, works on prebuilt images) -----------------------
# Hex colors do not survive some env interpolation — use oklch/rgb/hsl.
# ACCENT_COLOR=oklch(0.79 0.18 70.67)
# LOGO_URL=

# --- Error tracking -----------------------------------------------------
# OFF by default. Leave empty unless you run your own GlitchTip/Sentry.
SENTRY_DSN_BACKEND=
SENTRY_DSN_FRONTEND=
CHAT_SENTRY_DISABLED=1
# SENTRY_ENVIRONMENT=

# --- Chat email (optional) ----------------------------------------------
# Password reset stays hidden unless SMTP_HOST is set.
# SMTP_HOST=
# SMTP_PORT=587
# SMTP_USER=
# SMTP_PASS=
# SMTP_SECURE=false
# SMTP_FROM=
# ENABLE_REGISTRATION=true

# --- Resources ----------------------------------------------------------
# CORTEX_NEO4J_MEM_LIMIT=4g
# BACKEND_MEM_LIMIT=4g
# BATCH_PROCESSING_CONCURRENCY=2
```

- [ ] **Step 6: Verify both modes produce a valid, correctly-wired config**

The `:?` guards mean an incomplete `.env` fails loudly, which is exactly what we want to confirm.

```bash
cd selfhost

# The backup service builds from ./ops/backup relative to this compose file.
# At install time the README copies the repo's ops/ next to the compose files;
# in-repo, a temporary symlink gives the same shape. (Add `selfhost/ops` to
# .gitignore in the next step so it is never committed.)
ln -sfn ../ops ops

cp .env.example .env.test

# Fill the required secrets with throwaway values. Later assignments win in a
# dotenv file, so these override the empty ones from .env.example.
cat >> .env.test <<'EOF'
NEO4J_PASSWORD=testpassword1234567890abcd
ADMIN_PASSWORD=Test-Pass-1234-Abcd
ADMIN_API_KEY=cortex_admin_deadbeef
SESSION_SECRET=0123456789abcdef0123456789abcdef0123456789abcdef
CHAT_APP_ENCRYPTION_KEY=dGVzdGtleXRlc3RrZXl0ZXN0a2V5dGVzdGtleXRlc3Q=
OPENAI_API_KEY=sk-test
EOF

# Localhost mode.
docker compose --env-file .env.test \
  -f docker-compose.yml -f docker-compose.ports.yml config > /tmp/localhost.yml
echo "localhost mode: OK"

# Domain mode. `docker compose config` has no --env flag; the domain vars are
# only referenced by the caddy overlay, so pass them through the shell.
APP_DOMAIN=cortex.example.com CHAT_DOMAIN=chat.example.com \
  docker compose --env-file .env.test \
  -f docker-compose.yml -f docker-compose.caddy.yml config > /tmp/domain.yml
echo "domain mode: OK"
```

Now assert the constraints that actually matter:

```bash
# The backend service must be named `backend` (baked frontend rewrite target).
grep -qE '^  backend:' /tmp/localhost.yml && echo "backend service name: OK"

# NEXT_PUBLIC_API_URL must never be set — it would override same-origin.
grep -q 'NEXT_PUBLIC_API_URL' /tmp/localhost.yml \
  && { echo "FAIL: NEXT_PUBLIC_API_URL leaked into the config"; exit 1; } \
  || echo "NEXT_PUBLIC_API_URL absent: OK"

# Localhost must bind loopback only, never 0.0.0.0.
grep -q '0.0.0.0' /tmp/localhost.yml \
  && { echo "FAIL: a port is bound to 0.0.0.0 in localhost mode"; exit 1; } \
  || echo "loopback-only binding: OK"

# Domain mode must publish nothing except Caddy.
python3 - <<'PY'
import yaml
cfg = yaml.safe_load(open('/tmp/domain.yml'))
published = {n: s.get('ports') for n, s in cfg['services'].items() if s.get('ports')}
assert set(published) == {'caddy'}, f"only caddy may publish ports, got {list(published)}"
print("domain mode publishes only caddy: OK")
PY

# Error tracking must default to disabled.
python3 - <<'PY'
import yaml
cfg = yaml.safe_load(open('/tmp/localhost.yml'))
for svc in ('backend', 'frontend'):
    dsn = cfg['services'][svc]['environment'].get('SENTRY_DSN', '')
    assert dsn in ('', None), f"{svc} SENTRY_DSN must default empty, got {dsn!r}"
print("error tracking off by default: OK")
PY

rm -f .env.test
```

Expected: every line prints `OK`, nothing prints `FAIL`.

- [ ] **Step 6b: Gitignore the verification symlink**

Add to `.gitignore`:

```gitignore
# Created by the selfhost compose verification; the real ops/ is copied in at
# install time by the installer (or by hand, per selfhost/README.md).
selfhost/ops
```

Confirm it is ignored:

```bash
git check-ignore -v selfhost/ops && echo "ignored: OK"
```

Expected: `ignored: OK`

- [ ] **Step 7: Verify a missing required secret fails loudly**

```bash
cd selfhost
printf 'COMPOSE_FILE=docker-compose.yml\nCORTEX_BACKEND_IMAGE=x\nCORTEX_FRONTEND_IMAGE=y\nCORTEX_CHAT_IMAGE=z\n' > .env.broken
docker compose --env-file .env.broken -f docker-compose.yml config 2>&1 | head -3
rm -f .env.broken
```

Expected: an error naming `NEO4J_PASSWORD is required`. Confirms the `:?` guards catch an incomplete config before anything starts.

- [ ] **Step 8: Commit**

```bash
git add selfhost/docker-compose.yml selfhost/docker-compose.ports.yml selfhost/docker-compose.caddy.yml selfhost/Caddyfile.template selfhost/.env.example .gitignore
git commit -m "feat(selfhost): add the self-host compose stack

Static, valid compose files driven entirely by \${VAR} interpolation — no
YAML is generated, so the stack works standalone with a hand-filled .env and
user edits can never be clobbered by an update. Mode is selected with
compose's native COMPOSE_FILE overlay mechanism.

Localhost binds 127.0.0.1 only; domain mode publishes nothing but Caddy.
Error tracking defaults off. apps_data is mounted (the Dokploy compose still
omits it) and the backup sidecar now covers skills and apps."
```

---

## Task 10: Self-host documentation

**Repo:** `mocaOS/cortex-app`

**Files:**
- Create: `selfhost/README.md`
- Modify: `.claude/development.md`
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: everything above.
- Produces: the manual path Plan B's installer automates. Plan B adds the handbook chapter once `npx @mocaos/cortex` exists.

- [ ] **Step 1: Write the self-host README**

Create `selfhost/README.md`:

````markdown
# Self-hosting Cortex

Runs Cortex and Cortex Chat from prebuilt images. Everything is configured
through `.env`; the Compose files are static release artifacts you never need
to edit.

> An interactive installer that does all of this for you is coming as
> `npx @mocaos/cortex`. These are the manual instructions it automates.

## Requirements

- Docker Engine 24+ with the **Compose v2 plugin** (`docker compose version`).
  `apt install docker.io` does *not* include it — use the official Docker
  packages or Docker Desktop.
- ~20 GB free disk, ~8 GB RAM.
- `linux/amd64` or `linux/arm64`.
- An OpenAI-compatible API key.

## Install

```bash
curl -fsSL https://github.com/mocaOS/cortex-app/releases/latest/download/stack.json -o stack.json

git clone --depth 1 --branch "v$(jq -r .stack stack.json)" \
  https://github.com/mocaOS/cortex-app.git /tmp/cortex-src

mkdir -p cortex && cd cortex
cp -r /tmp/cortex-src/selfhost/. .
cp -r /tmp/cortex-src/ops ./ops
cp Caddyfile.template Caddyfile
cp .env.example .env
chmod 600 .env
```

Edit `.env` — at minimum the secrets block and the LLM block. Generate secrets
with the commands in the comments there. Then:

```bash
docker compose up -d
docker compose ps
```

## Modes

`COMPOSE_FILE` in `.env` selects the mode.

**Localhost** (default) — ports on `127.0.0.1` only:

```dotenv
COMPOSE_FILE=docker-compose.yml:docker-compose.ports.yml
SESSION_COOKIE_SECURE=false
```

| | |
|---|---|
| Cortex | http://localhost:3000 |
| Chat | http://localhost:3001 |
| API | http://localhost:8000 |
| Neo4j | http://localhost:7474 |

**Public domain** — Caddy with automatic HTTPS. Point both A records at this
host *first*, or Let's Encrypt issuance fails:

```dotenv
COMPOSE_FILE=docker-compose.yml:docker-compose.caddy.yml
APP_DOMAIN=cortex.example.com
CHAT_DOMAIN=chat.example.com
ACME_EMAIL=you@example.com
CHAT_BASE_URL=https://chat.example.com
CORS_ALLOWED_ORIGINS=https://cortex.example.com,https://chat.example.com
```

Only Caddy publishes ports in this mode — the API and Neo4j browser stay
private on the Compose network. The API is still reachable at
`https://cortex.example.com/api/...` because the frontend proxies it.

## Logging in

Cortex and Chat share one identity: `ADMIN_EMAIL` + `ADMIN_PASSWORD`. Chat
mints its scoped backend keys with `ADMIN_API_KEY`.

## Customizing

Put local changes in `docker-compose.override.yml` — Compose merges it
automatically and updates never touch it. Do not edit `docker-compose.yml`.

**Do not rename the `backend` service.** The published frontend image bakes
`API_URL=http://backend:8000` into its Next.js rewrite manifest at build time.

## Updating

```bash
curl -fsSL https://github.com/mocaOS/cortex-app/releases/latest/download/stack.json -o stack.json
```

Update the three `CORTEX_*_IMAGE` lines in `.env` to the versions in
`stack.json`, then:

```bash
docker compose exec backup /backup.sh   # back up first
docker compose pull
docker compose up -d
```

## Backups

The `backup` sidecar runs nightly: a verified APOC graph export plus a tar of
uploads, custom inputs, chat data, skills and apps. It goes unhealthy if the
newest verified backup is older than two intervals.

```bash
docker compose exec backup /backup.sh                        # run now
docker compose exec backup ls /backups                       # list
docker compose exec -e RESTORE_WIPE=yes backup /restore.sh <timestamp>
```

Backups live in the `backups` named volume. **Ship them off-host** — a volume
on the same disk is not disaster recovery.

## Troubleshooting

**Admin login silently fails on localhost.** `SESSION_COOKIE_SECURE=false` is
required over plain HTTP; browsers drop `Secure` cookies on non-TLS origins.

**Backend exits at boot with "Insecure configuration for ENVIRONMENT=production".**
A secret is still a placeholder. Regenerate it with the commands in
`.env.example`.

**Documents fail to process.** Check `docker compose logs backend`. The most
common cause is an unreachable LLM endpoint or a wrong `OPENAI_MODEL`.

**Search returns nothing after changing the embedding model.**
`EMBEDDING_DIMENSION` is baked into the Neo4j vector index on first use.
Changing it requires re-embedding the corpus.
````

- [ ] **Step 2: Verify every command in the README is accurate**

Run the assertions the README makes:

```bash
# The generated .env.example really does document every :? required var.
grep -oE '\$\{[A-Z_]+:\?' selfhost/docker-compose.yml | sed 's/\${//;s/:?//' | sort -u > /tmp/required.txt
cat /tmp/required.txt
while read -r v; do
  grep -qE "^#? *${v}=" selfhost/.env.example \
    || { echo "FAIL: $v is required by compose but absent from .env.example"; exit 1; }
done < /tmp/required.txt
echo "all required vars documented: OK"
```

Expected: `all required vars documented: OK`

- [ ] **Step 3: Update the .claude/ docs per the routing table**

Append to `.claude/development.md` a section documenting the new deployment target:

```markdown
## Self-host (prebuilt images)

`selfhost/` holds a third deployment path alongside `coolify/` and `dokploy/`:
static Compose files that run **prebuilt GHCR images** rather than building
from source.

- `docker-compose.yml` — base stack. **Never generate or rewrite this file.**
  Everything is `${VAR}` interpolation from `.env`.
- `docker-compose.ports.yml` / `docker-compose.caddy.yml` — mode overlays
  selected by `COMPOSE_FILE` in `.env`.
- `stack.template.json` — pins for components not built from this repo
  (chat, neo4j, caddy). `scripts/build-stack-json.mjs` turns it into the
  `stack.json` release asset.

Constraints that will silently break the stack if violated:

- The backend service must stay named `backend` — the published frontend
  image bakes `API_URL=http://backend:8000` into its rewrite manifest.
- `NEXT_PUBLIC_API_URL` must never be set for the published frontend image.
- Error-tracking DSNs default to empty here, unlike the Dokploy compose.

Releases are tag-triggered (`.github/workflows/release.yml`) and guarded by
`scripts/check-version-sync.mjs`. cortex-chat must be released before
cortex-app, since `stack.json` pins its version and verifies it is pullable.
```

- [ ] **Step 4: Add the routing entries to CLAUDE.md**

In the File-Path Routing table in `CLAUDE.md`, add these rows:

```markdown
| `selfhost/**`, `scripts/build-stack-json.mjs`, `scripts/check-version-sync.mjs` | `development.md` (self-host section), `environment.md` |
| `.github/workflows/release.yml` | `development.md` (self-host section) |
```

- [ ] **Step 5: Commit**

```bash
git add selfhost/README.md .claude/development.md CLAUDE.md
git commit -m "docs: self-host instructions and .claude routing

Documents the manual path that npx @mocaos/cortex will automate, plus the
constraints that silently break the stack: the backend service name, the
unset NEXT_PUBLIC_API_URL, and the release ordering between the two repos."
```

---

## Phase exit criteria

Before Plan B starts, confirm on a clean host:

- [ ] `docker pull ghcr.io/mocaos/cortex-backend:1.0.0` succeeds **anonymously** on both amd64 and arm64. (Requires the one-time manual flip of all three GHCR packages to public — see below.)
- [ ] `curl -fsSL https://github.com/mocaOS/cortex-app/releases/latest/download/stack.json | jq .stack` prints `1.0.0`.
- [ ] Following `selfhost/README.md` verbatim brings up a healthy stack in localhost mode: `docker compose ps` shows every service healthy.
- [ ] Admin login works at http://localhost:3000 with the generated credentials.
- [ ] The same credentials log in at http://localhost:3001 (chat).
- [ ] A document uploads and finishes processing (entities appear in the graph).
- [ ] `docker compose exec backup /backup.sh` completes and `/backups/latest/files.tar.gz` contains `data/skills` and `data/apps`.
- [ ] The same run repeated in domain mode on a real VPS obtains Let's Encrypt certificates for both hosts.

## Manual prerequisites

Not automatable — do these before or during rollout:

1. **Flip all three GHCR packages to public** after the first release pushes them. Packages default to private and the installer pulls anonymously; until this is done every install fails. Org → Packages → each package → Package settings → Change visibility.
2. Confirm `mocaOS/cortex-app` and `mocaOS/cortex-chat` have Actions permitted to write packages (Settings → Actions → Workflow permissions).
3. Release **cortex-chat v1.0.0 first**, then cortex-app v1.0.0 — Task 5's pullability check enforces this and will fail the release otherwise.
