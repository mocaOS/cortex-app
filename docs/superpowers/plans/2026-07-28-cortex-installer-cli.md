# Cortex Installer CLI — Implementation Plan (Plan B)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `npx @mocaos/cortex` walks someone through an interactive wizard and leaves them with a running, self-hosted Cortex — app and chat, wired together — pinned to a tested release and updatable from the CLI.

**Architecture:** A small ESM TypeScript CLI. It reads `stack.json` from the latest cortex-app GitHub release to learn which component versions form a tested stack, fetches that tag's `selfhost/` compose files and `ops/` directory from the release tarball, runs an interactive wizard that validates the user's LLM provider with live probes before writing anything, generates `.env` (the only file it authors), then drives `docker compose`. Day-2 verbs wrap Compose with the right project and directory.

**Tech Stack:** Node ≥20.12, ESM, TypeScript, `@clack/prompts` 1.7, `picocolors` 1.1, `semver` 7.8. Tests use `node:test` (built in). No bundler — publish compiled JS via `tsc`.

**Repo:** new `mocaOS/cortex-installer` → npm `@mocaos/cortex`.

**Spec:** [`docs/superpowers/specs/2026-07-27-cortex-self-host-installer-design.md`](../specs/2026-07-27-cortex-self-host-installer-design.md) §4–6.
**Plan A (shipped, prerequisite):** [`2026-07-27-cortex-release-pipeline.md`](2026-07-27-cortex-release-pipeline.md).

## What Plan A already delivered — verified live, do not re-derive

- `ghcr.io/mocaos/cortex-{backend,frontend,chat}` at `1.0.0`/`1.0`/`1`/`latest`, multi-arch `linux/amd64`+`linux/arm64`, **anonymously pullable**.
- `https://github.com/mocaOS/cortex-app/releases/latest/download/stack.json` returns:
  ```json
  { "stack": "1.0.0",
    "components": { "backend": "1.0.0", "frontend": "1.0.0", "chat": "1.0.0",
                    "neo4j": "5.26-community", "caddy": "2-alpine" },
    "minInstaller": "1.0.0",
    "notes": "https://github.com/mocaOS/cortex-app/releases/tag/v1.0.0" }
  ```
- `https://api.github.com/repos/mocaOS/cortex-app/tarball/v<version>` (6.4 MB) yields `selfhost/` (including `.env.example`) and `ops/backup/`. Verified extract-and-render works.
- Pull sizes: backend 1231 MB, frontend 73 MB, chat 72 MB (+ ~200 MB neo4j).

## Global Constraints

- **Node ≥ 20.12.** `package.json` sets `"engines": { "node": ">=20.12.0" }` and
  `"type": "module"`. The floor is set by `@clack/prompts` and `@clack/core`, which
  both declare `">= 20.12.0"` from 1.3.0 onward — verified against the registry.
  Node 18 reached EOL in April 2025, so matching the dependency is preferable to
  pinning the prompt library a year back to support a dead runtime.
- **npm package `@mocaos/cortex`**, single `bin` named `cortex`. The `mocaos` npm org exists; the name is free.
- **The installer authors exactly one file: `.env`.** Compose files, `Caddyfile.template` and `ops/` are fetched release artifacts, copied verbatim, never edited or generated. This is what makes user edits survive updates.
- **Mode is selected by `COMPOSE_FILE` in `.env`**, using Compose's native overlay mechanism:
  - localhost → `docker-compose.yml:docker-compose.ports.yml`
  - domain → `docker-compose.yml:docker-compose.caddy.yml`
- **Required `.env` variables** (every `${VAR:?}` in the released compose). Always: `CORTEX_BACKEND_IMAGE`, `CORTEX_FRONTEND_IMAGE`, `CORTEX_CHAT_IMAGE`, `NEO4J_PASSWORD`, `OPENAI_API_KEY`, `ADMIN_PASSWORD`, `ADMIN_API_KEY`, `SESSION_SECRET`, `CHAT_APP_ENCRYPTION_KEY`. Domain mode adds: `APP_DOMAIN`, `CHAT_DOMAIN`, `ACME_EMAIL`.
- **Never set `SESSION_COOKIE_SECURE`.** The ports overlay sets it `false`; domain mode leaves it unset so the app defaults to Secure. Writing it into `.env` would defeat that.
- **Never set `NEXT_PUBLIC_API_URL`.**
- **Prebuilt images only — no `--build` flag.** The published images have client-side error reporting disabled at build time; a source build would re-enable it. If a build path is ever added it MUST pass `NEXT_PUBLIC_SENTRY_DISABLED=1` to the chat image.
- **Secrets:** generated with `crypto.randomBytes`. Never logged, never sent anywhere, never written outside `.env` (mode `600`). Only `ADMIN_PASSWORD` is echoed in the final summary.
- **`minInstaller` is honoured**: if `stack.json` requires a newer installer than the running one, stop and tell the user to run `npx @mocaos/cortex@latest`.
- **Respect `NO_COLOR`** and non-TTY: non-interactive mode is a first-class path, not a test affordance.

## File Structure

`mocaOS/cortex-installer`:

| Path | Responsibility |
|---|---|
| `package.json` | `@mocaos/cortex`, `bin.cortex`, engines, deps |
| `tsconfig.json` | ES2022 / NodeNext, `outDir: dist` |
| `src/cli.ts` | Arg parsing, verb dispatch, top-level error handling |
| `src/ui.ts` | clack wrappers: banner, note-box, spinner, X-of-Y counter, `NO_COLOR` |
| `src/preflight.ts` | Docker/Compose/daemon/arch/disk/RAM/port checks |
| `src/stack.ts` | Fetch + parse `stack.json`; semver and `minInstaller` comparison |
| `src/artifacts.ts` | Fetch the tag tarball; extract `selfhost/` + `ops/` into the install dir |
| `src/secrets.ts` | Generate + validate credentials |
| `src/providers.ts` | Provider presets (base URLs, key requirements) |
| `src/validate.ts` | Live `/v1/models`, chat and embedding probes |
| `src/env.ts` | `InstallConfig` → `.env` text (pure) |
| `src/docker.ts` | Compose driver: pull with progress, up, health-wait, logs, exec |
| `src/state.ts` | Read/write `cortex.json` |
| `src/wizard.ts` | Prompt flow only; returns `InstallConfig` |
| `src/commands/*.ts` | One file per verb |
| `test/*.test.ts` | `node:test` suites |
| `test/fake-openai.ts` | OpenAI-compatible test server |
| `.github/workflows/{ci,release}.yml` | Typecheck + tests; npm publish on tag |

Install directory the CLI produces:

```
./cortex/
  docker-compose.yml            fetched, verbatim
  docker-compose.ports.yml      fetched, verbatim
  docker-compose.caddy.yml      fetched, verbatim
  Caddyfile.template            fetched, verbatim
  Caddyfile                     copy of the template (domain mode)
  .env.example                  fetched, kept as reference
  ops/backup/                   fetched (backup sidecar build context)
  .env                          GENERATED — the only authored file, mode 600
  cortex.json                   install state, no secrets
```

---

## Task 1: Repo scaffold, CLI entrypoint, `--version`/`--help`

**Files:**
- Create: `package.json`, `tsconfig.json`, `.gitignore`
- Create: `src/cli.ts`, `src/ui.ts`, `src/version.ts`
- Test: `test/cli.test.ts`

(`README.md` is Task 13's, not this task's.)

**Interfaces:**
- Consumes: nothing.
- Produces: `parseArgs(argv: string[]) => { verb: string, flags: Record<string, string|boolean>, positionals: string[] }`. Every later task's command module is dispatched from `src/cli.ts` by verb name. `src/ui.ts` exports `banner()`, `noteBox(title: string, lines: string[])`, `colorEnabled(): boolean`. **`src/version.ts` exports `installerVersion(): string`** — it lives in its own module, not in `cli.ts`, because every command module needs it and importing it from `cli.ts` would create a cycle (`cli` → `commands/*` → `cli`).

This task's TDD cycle needs the package manifest and dependencies in place
before a test can run at all, so the scaffold comes first and the RED step
follows it.

- [ ] **Step 1: Create the package manifest and install**

Create `package.json`, `tsconfig.json` and `.gitignore` exactly as given in
Step 4 below, then run `npm install`. Commit nothing yet.

- [ ] **Step 2: Write the failing test**

Create `test/cli.test.ts`:

```typescript
import { test } from "node:test";
import assert from "node:assert/strict";
import { parseArgs } from "../src/cli.js";

test("defaults to the install verb", () => {
  const r = parseArgs([]);
  assert.equal(r.verb, "install");
});

test("takes the first non-flag token as the verb", () => {
  assert.equal(parseArgs(["update"]).verb, "update");
  assert.equal(parseArgs(["logs", "backend"]).verb, "logs");
});

test("collects positionals after the verb", () => {
  assert.deepEqual(parseArgs(["logs", "backend"]).positionals, ["backend"]);
});

test("parses long flags with values", () => {
  const r = parseArgs(["install", "--dir", "/opt/cortex"]);
  assert.equal(r.flags.dir, "/opt/cortex");
});

test("parses --key=value form", () => {
  assert.equal(parseArgs(["install", "--dir=/opt/cortex"]).flags.dir, "/opt/cortex");
});

test("treats a valueless flag as boolean true", () => {
  assert.equal(parseArgs(["install", "--yes"]).flags.yes, true);
});

test("a flag before the verb still parses and the verb is still found", () => {
  const r = parseArgs(["--yes", "update"]);
  assert.equal(r.verb, "update");
  assert.equal(r.flags.yes, true);
});

test("rejects an unknown verb instead of silently installing", () => {
  assert.throws(() => parseArgs(["staus"]), /Unknown command "staus"/);
});

test("still defaults to install when no verb is given at all", () => {
  assert.equal(parseArgs(["--yes"]).verb, "install");
});
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `npm test`
Expected: FAIL — cannot find `../src/cli.js`

- [ ] **Step 4: The file contents referenced by Step 1**

`package.json`:

```json
{
  "name": "@mocaos/cortex",
  "version": "1.0.0",
  "description": "Interactive installer for self-hosted Cortex",
  "license": "MIT",
  "type": "module",
  "engines": { "node": ">=20.12.0" },
  "bin": { "cortex": "dist/cli.js" },
  "files": ["dist"],
  "scripts": {
    "build": "tsc",
    "typecheck": "tsc --noEmit",
    "test": "tsc --noEmit && node --test --import tsx test/*.test.ts",
    "prepack": "npm run build",
    "prepublishOnly": "npm run build"
  },
  "dependencies": {
    "@clack/prompts": "^1.7.0",
    "picocolors": "^1.1.1",
    "semver": "^7.8.5"
  },
  "devDependencies": {
    "@types/node": "^22.0.0",
    "@types/semver": "^7.7.0",
    "tsx": "^4.19.0",
    "typescript": "^5.6.0"
  }
}
```

Create `tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "NodeNext",
    "moduleResolution": "NodeNext",
    "outDir": "dist",
    "rootDir": "src",
    "strict": true,
    "declaration": false,
    "sourceMap": true,
    "skipLibCheck": true
  },
  "include": ["src/**/*.ts"]
}
```

Create `.gitignore`:

```gitignore
node_modules/
dist/
*.log
.DS_Store
```

- [ ] **Step 5: Write `src/version.ts`**

Its own module on purpose: every command needs the version, and importing it
from `cli.ts` would make `cli` → `commands/*` → `cli` a cycle.

```typescript
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

/** Reads the published package version. dist/version.js -> ../package.json */
export function installerVersion(): string {
  const here = dirname(fileURLToPath(import.meta.url));
  return JSON.parse(readFileSync(join(here, "..", "package.json"), "utf8")).version;
}
```

- [ ] **Step 6: Write `src/ui.ts`**

```typescript
import pc from "picocolors";
import * as p from "@clack/prompts";

/** Colour is suppressed by NO_COLOR, --no-color, or a non-TTY stdout. */
export function colorEnabled(): boolean {
  if (process.env.NO_COLOR) return false;
  if (process.argv.includes("--no-color")) return false;
  return process.stdout.isTTY === true;
}

const dim = (s: string) => (colorEnabled() ? pc.dim(s) : s);
const bold = (s: string) => (colorEnabled() ? pc.bold(s) : s);

export function banner(version: string): void {
  p.intro(`${bold("Cortex")} ${dim(`self-host installer ${version}`)}`);
}

/** A boxed group of key/value or plain lines. */
export function noteBox(title: string, lines: string[]): void {
  p.note(lines.join("\n"), title);
}

export { p as prompts };
```

- [ ] **Step 7: Write `src/cli.ts`**

```typescript
#!/usr/bin/env node
import * as p from "@clack/prompts";
import { installerVersion } from "./version.js";

export interface ParsedArgs {
  verb: string;
  flags: Record<string, string | boolean>;
  positionals: string[];
}

const VERBS = new Set([
  "install", "update", "config", "status", "logs",
  "start", "stop", "restart", "backup", "restore",
  "doctor", "uninstall",
]);

/** Flags that take a value; everything else is boolean. */
const VALUE_FLAGS = new Set(["dir", "config", "stack"]);

export function parseArgs(argv: string[]): ParsedArgs {
  const flags: Record<string, string | boolean> = {};
  const rest: string[] = [];

  for (let i = 0; i < argv.length; i++) {
    const tok = argv[i];
    if (tok.startsWith("--")) {
      const body = tok.slice(2);
      const eq = body.indexOf("=");
      if (eq !== -1) {
        flags[body.slice(0, eq)] = body.slice(eq + 1);
      } else if (VALUE_FLAGS.has(body) && argv[i + 1] && !argv[i + 1].startsWith("-")) {
        flags[body] = argv[++i];
      } else {
        flags[body] = true;
      }
    } else {
      rest.push(tok);
    }
  }

  // Default to `install` only when NO verb was given. A token that is present
  // but unrecognised is a typo and must be rejected — silently falling back
  // would make `cortex staus` launch the whole interactive installer.
  if (rest.length && !VERBS.has(rest[0])) {
    throw new Error(
      `Unknown command "${rest[0]}". Run \`npx @mocaos/cortex --help\` for the list.`
    );
  }
  const verb = rest.length ? rest.shift()! : "install";
  return { verb, flags, positionals: rest };
}

const HELP = `
  cortex — installer and manager for self-hosted Cortex

  Usage
    npx @mocaos/cortex [command] [options]

  Commands
    install            Interactive setup (default)
    update             Move an install to the latest released stack
    config             Re-run the wizard against an existing install
    status             Service state, health and URLs
    logs [service]     Follow logs
    start|stop|restart Lifecycle
    backup             Run a verified backup now
    restore <stamp>    Restore from a backup
    doctor             Diagnose an install
    uninstall          Remove containers, optionally volumes

  Options
    --dir <path>       Install directory (default ./cortex, or discovered upwards)
    --yes              Non-interactive; take values from the environment
    --no-color         Disable colour
    --version          Print the installer version
    --help             Show this
`;

async function main(): Promise<void> {
  const { verb, flags, positionals } = parseArgs(process.argv.slice(2));

  if (flags.version) { console.log(installerVersion()); return; }
  if (flags.help) { console.log(HELP); return; }

  const { run } = await import(`./commands/${verb}.js`);
  await run({ flags, positionals });
}

if (process.argv[1] && process.argv[1].endsWith("cli.js")) {
  main().catch((err: unknown) => {
    p.cancel(err instanceof Error ? err.message : String(err));
    process.exit(1);
  });
}
```

- [ ] **Step 8: Run the test, build, and check the version**

Run: `npm test`
Expected: PASS — 9 tests, typecheck clean

Then prove the published entrypoint works, which is where `version.ts`'s path
resolution from `dist/` is easy to get wrong:

Run: `npm run build && node dist/cli.js --version`
Expected: the package version, e.g. `1.0.0`

- [ ] **Step 9: Commit**

```bash
git add package.json tsconfig.json .gitignore src/cli.ts src/ui.ts src/version.ts test/cli.test.ts
git commit -m "feat: scaffold the installer CLI

ESM TypeScript, node:test, zero-bundler. parseArgs defaults to the install
verb so \`npx @mocaos/cortex\` with no arguments runs setup, and dispatches
to src/commands/<verb>.js lazily so a verb's dependencies only load when used."
```

---

## Task 2: `stack.ts` — fetch and validate the release manifest

**Files:**
- Create: `src/stack.ts`
- Test: `test/stack.test.ts`

**Interfaces:**
- Consumes: `installerVersion()` from Task 1.
- Produces:
  - `parseStack(raw: unknown): Stack` — throws on a malformed manifest.
  - `assertInstallerSupported(stack: Stack, installer: string): void` — throws if `stack.minInstaller` exceeds `installer`.
  - `fetchStack(opts?: { version?: string; fetchImpl?: typeof fetch }): Promise<Stack>`.
  - `interface Stack { stack: string; components: { backend: string; frontend: string; chat: string; neo4j: string; caddy: string }; minInstaller: string; notes?: string }`
  - `imageRefs(stack: Stack): { backend: string; frontend: string; chat: string }` returning full `ghcr.io/mocaos/...:tag` strings. Task 6 writes these into `.env`.

- [ ] **Step 1: Write the failing test**

Create `test/stack.test.ts`:

```typescript
import { test } from "node:test";
import assert from "node:assert/strict";
import { parseStack, assertInstallerSupported, imageRefs, fetchStack } from "../src/stack.js";

const GOOD = {
  stack: "1.0.0",
  components: { backend: "1.0.0", frontend: "1.0.0", chat: "1.0.0", neo4j: "5.26-community", caddy: "2-alpine" },
  minInstaller: "1.0.0",
  notes: "https://example.com",
};

test("parses a well-formed manifest", () => {
  const s = parseStack(GOOD);
  assert.equal(s.stack, "1.0.0");
  assert.equal(s.components.neo4j, "5.26-community");
});

for (const key of ["backend", "frontend", "chat", "neo4j", "caddy"]) {
  test(`throws when components.${key} is missing`, () => {
    const bad = structuredClone(GOOD) as Record<string, any>;
    delete bad.components[key];
    assert.throws(() => parseStack(bad), new RegExp(key));
  });
}

test("throws when stack version is missing", () => {
  const bad = structuredClone(GOOD) as Record<string, any>;
  delete bad.stack;
  assert.throws(() => parseStack(bad), /stack/);
});

test("throws when minInstaller is missing", () => {
  const bad = structuredClone(GOOD) as Record<string, any>;
  delete bad.minInstaller;
  assert.throws(() => parseStack(bad), /minInstaller/);
});

test("throws on a non-object manifest", () => {
  assert.throws(() => parseStack("nope"), /manifest/i);
});

test("accepts an installer newer than minInstaller", () => {
  assertInstallerSupported(parseStack(GOOD), "1.2.0");
});

test("accepts an installer exactly equal to minInstaller", () => {
  assertInstallerSupported(parseStack(GOOD), "1.0.0");
});

test("rejects an installer older than minInstaller, naming the upgrade command", () => {
  const s = parseStack({ ...GOOD, minInstaller: "2.0.0" });
  assert.throws(() => assertInstallerSupported(s, "1.0.0"), /@mocaos\/cortex@latest/);
});

test("builds fully-qualified GHCR image refs", () => {
  assert.deepEqual(imageRefs(parseStack(GOOD)), {
    backend: "ghcr.io/mocaos/cortex-backend:1.0.0",
    frontend: "ghcr.io/mocaos/cortex-frontend:1.0.0",
    chat: "ghcr.io/mocaos/cortex-chat:1.0.0",
  });
});

test("fetchStack uses the latest-release URL by default", async () => {
  let seen = "";
  const s = await fetchStack({
    fetchImpl: (async (url: any) => {
      seen = String(url);
      return { ok: true, status: 200, json: async () => GOOD } as any;
    }) as typeof fetch,
  });
  assert.equal(s.stack, "1.0.0");
  assert.match(seen, /releases\/latest\/download\/stack\.json$/);
});

test("fetchStack targets a specific tag when given a version", async () => {
  let seen = "";
  await fetchStack({
    version: "1.2.3",
    fetchImpl: (async (url: any) => {
      seen = String(url);
      return { ok: true, status: 200, json: async () => ({ ...GOOD, stack: "1.2.3" }) } as any;
    }) as typeof fetch,
  });
  assert.match(seen, /releases\/download\/v1\.2\.3\/stack\.json$/);
});

test("fetchStack surfaces a non-OK response with its status", async () => {
  await assert.rejects(
    fetchStack({ fetchImpl: (async () => ({ ok: false, status: 503 }) as any) as typeof fetch }),
    /503/
  );
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm test`
Expected: FAIL — cannot find `../src/stack.js`

- [ ] **Step 3: Write the implementation**

Create `src/stack.ts`:

```typescript
import semver from "semver";

export interface Stack {
  stack: string;
  components: {
    backend: string;
    frontend: string;
    chat: string;
    neo4j: string;
    caddy: string;
  };
  minInstaller: string;
  notes?: string;
}

const REPO = "mocaOS/cortex-app";
const COMPONENTS = ["backend", "frontend", "chat", "neo4j", "caddy"] as const;

export function parseStack(raw: unknown): Stack {
  if (typeof raw !== "object" || raw === null) {
    throw new Error("stack.json is not an object — the release manifest is malformed");
  }
  const o = raw as Record<string, any>;

  if (typeof o.stack !== "string") throw new Error("stack.json is missing `stack`");
  if (typeof o.minInstaller !== "string") throw new Error("stack.json is missing `minInstaller`");
  if (typeof o.components !== "object" || o.components === null) {
    throw new Error("stack.json is missing `components`");
  }
  for (const c of COMPONENTS) {
    if (typeof o.components[c] !== "string") {
      throw new Error(`stack.json is missing components.${c}`);
    }
  }
  return o as Stack;
}

export function assertInstallerSupported(stack: Stack, installer: string): void {
  // Coerce because component pins like "5.26-community" are not semver, but
  // stack/minInstaller always are.
  if (semver.lt(installer, stack.minInstaller)) {
    throw new Error(
      `Cortex ${stack.stack} needs installer >= ${stack.minInstaller}, but this is ${installer}.\n` +
        `Run \`npx @mocaos/cortex@latest\` to get the newer installer.`
    );
  }
}

export function imageRefs(stack: Stack): { backend: string; frontend: string; chat: string } {
  return {
    backend: `ghcr.io/mocaos/cortex-backend:${stack.components.backend}`,
    frontend: `ghcr.io/mocaos/cortex-frontend:${stack.components.frontend}`,
    chat: `ghcr.io/mocaos/cortex-chat:${stack.components.chat}`,
  };
}

export function stackUrl(version?: string): string {
  return version
    ? `https://github.com/${REPO}/releases/download/v${version}/stack.json`
    : `https://github.com/${REPO}/releases/latest/download/stack.json`;
}

export async function fetchStack(opts?: {
  version?: string;
  fetchImpl?: typeof fetch;
}): Promise<Stack> {
  const f = opts?.fetchImpl ?? fetch;
  const url = stackUrl(opts?.version);
  const res = await f(url, { redirect: "follow" } as RequestInit);
  if (!res.ok) {
    throw new Error(`Could not fetch ${url} (HTTP ${res.status})`);
  }
  return parseStack(await res.json());
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npm test`
Expected: PASS — 17 tests

- [ ] **Step 5: Verify against the live release**

```bash
node --import tsx -e '
import { fetchStack, imageRefs, assertInstallerSupported } from "./src/stack.js";
const s = await fetchStack();
console.log("stack:", s.stack, "minInstaller:", s.minInstaller);
console.log(imageRefs(s));
assertInstallerSupported(s, "1.0.0");
console.log("installer 1.0.0 supported: yes");
'
```

Expected: `stack: 1.0.0 minInstaller: 1.0.0`, the three `ghcr.io/mocaos/...:1.0.0` refs, and the support line.

- [ ] **Step 6: Commit**

```bash
git add src/stack.ts test/stack.test.ts
git commit -m "feat(stack): fetch and validate the release manifest

Every component key is validated independently so a manifest missing one
fails loudly rather than producing an undefined image tag. minInstaller is
enforced with semver and the error names the upgrade command."
```

---

## Task 3: `preflight.ts` — environment checks

**Files:**
- Create: `src/preflight.ts`
- Test: `test/preflight.test.ts`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `parseDockerVersion(out: string): string | null`
  - `parseComposeVersion(out: string): string | null`
  - `checkArch(arch: string): { ok: boolean; message?: string }`
  - `checkPort(port: number, host?: string): Promise<boolean>` — true when free.
  - `runPreflight(opts): Promise<PreflightReport>` where
    `interface PreflightReport { ok: boolean; checks: Array<{ name: string; ok: boolean; detail: string; fatal: boolean }> }`.
  Task 8's `install` renders this report and aborts when any `fatal` check failed.

- [ ] **Step 1: Write the failing test**

Create `test/preflight.test.ts`:

```typescript
import { test } from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:net";
import { parseDockerVersion, parseComposeVersion, checkArch, checkPort } from "../src/preflight.js";

test("parses `docker version --format` output", () => {
  assert.equal(parseDockerVersion("27.3.1\n"), "27.3.1");
});

test("parses a docker version with build metadata", () => {
  assert.equal(parseDockerVersion("27.3.1-rd\n"), "27.3.1");
});

test("returns null for unparseable docker output", () => {
  assert.equal(parseDockerVersion("Cannot connect to the Docker daemon"), null);
});

test("parses `docker compose version` output", () => {
  assert.equal(parseComposeVersion("Docker Compose version v2.29.7\n"), "2.29.7");
});

test("parses compose version without the v prefix", () => {
  assert.equal(parseComposeVersion("Docker Compose version 2.30.0\n"), "2.30.0");
});

test("returns null when the compose plugin is absent", () => {
  assert.equal(parseComposeVersion("docker: 'compose' is not a docker command."), null);
});

test("accepts amd64 and arm64", () => {
  assert.equal(checkArch("x64").ok, true);
  assert.equal(checkArch("arm64").ok, true);
});

test("rejects other architectures and names the arch", () => {
  const r = checkArch("ppc64");
  assert.equal(r.ok, false);
  assert.match(r.message!, /ppc64/);
});

test("checkPort reports a free port as free", async () => {
  assert.equal(await checkPort(45231), true);
});

test("checkPort reports a bound port as taken", async () => {
  const srv = createServer();
  await new Promise<void>((res) => srv.listen(45232, "127.0.0.1", res));
  try {
    assert.equal(await checkPort(45232), false);
  } finally {
    await new Promise<void>((res) => srv.close(() => res()));
  }
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm test`
Expected: FAIL — cannot find `../src/preflight.js`

- [ ] **Step 3: Write the implementation**

Create `src/preflight.ts`:

```typescript
import { execFile } from "node:child_process";
import { createServer } from "node:net";
import { promisify } from "node:util";
import semver from "semver";

const exec = promisify(execFile);

export interface Check {
  name: string;
  ok: boolean;
  detail: string;
  /** A failed fatal check aborts the install. */
  fatal: boolean;
}
export interface PreflightReport {
  ok: boolean;
  checks: Check[];
}

const MIN_NODE = "20.12.0";   // set by @clack/prompts + @clack/core
const MIN_COMPOSE = "2.20.0";
const MIN_DISK_GB = 20;
const HARD_DISK_GB = 10;
const WARN_RAM_GB = 8;
const MIN_RAM_GB = 6;

export function parseDockerVersion(out: string): string | null {
  const m = out.match(/(\d+\.\d+\.\d+)/);
  return m ? m[1] : null;
}

export function parseComposeVersion(out: string): string | null {
  const m = out.match(/version\s+v?(\d+\.\d+\.\d+)/i);
  return m ? m[1] : null;
}

export function checkArch(arch: string): { ok: boolean; message?: string } {
  if (arch === "x64" || arch === "arm64") return { ok: true };
  return {
    ok: false,
    message:
      `Unsupported architecture "${arch}". Published images cover linux/amd64 ` +
      `and linux/arm64 only.`,
  };
}

export function checkPort(port: number, host = "127.0.0.1"): Promise<boolean> {
  return new Promise((resolve) => {
    const srv = createServer();
    srv.once("error", () => resolve(false));
    srv.once("listening", () => srv.close(() => resolve(true)));
    srv.listen(port, host);
  });
}

async function tryExec(cmd: string, args: string[]): Promise<string> {
  try {
    const { stdout, stderr } = await exec(cmd, args);
    return `${stdout}${stderr}`;
  } catch (err: any) {
    return `${err?.stdout ?? ""}${err?.stderr ?? err?.message ?? ""}`;
  }
}

/** Parses `docker info --format {{json .}}` for MemTotal and DockerRootDir. */
async function dockerInfo(): Promise<{ memGb: number | null; rootDir: string | null }> {
  const out = await tryExec("docker", ["info", "--format", "{{json .}}"]);
  try {
    const j = JSON.parse(out);
    return {
      memGb: typeof j.MemTotal === "number" ? j.MemTotal / 1024 ** 3 : null,
      rootDir: typeof j.DockerRootDir === "string" ? j.DockerRootDir : null,
    };
  } catch {
    return { memGb: null, rootDir: null };
  }
}

async function freeDiskGb(path: string): Promise<number | null> {
  const out = await tryExec("df", ["-Pk", path]);
  const line = out.trim().split("\n").at(-1) ?? "";
  const cols = line.split(/\s+/);
  const availKb = Number(cols[3]);
  return Number.isFinite(availKb) ? availKb / 1024 ** 2 : null;
}

export async function runPreflight(opts: {
  ports?: number[];
  bindAddr?: string;
}): Promise<PreflightReport> {
  const checks: Check[] = [];

  // Node
  const nodeOk = semver.gte(process.versions.node, MIN_NODE);
  checks.push({
    name: "Node",
    ok: nodeOk,
    detail: nodeOk
      ? `v${process.versions.node}`
      : `v${process.versions.node} — need >= ${MIN_NODE}`,
    fatal: true,
  });

  // Docker daemon
  const dv = parseDockerVersion(await tryExec("docker", ["version", "--format", "{{.Server.Version}}"]));
  checks.push({
    name: "Docker daemon",
    ok: dv !== null,
    detail: dv ? `${dv}` : "not reachable — is Docker running?",
    fatal: true,
  });

  // Compose v2 plugin
  const cv = parseComposeVersion(await tryExec("docker", ["compose", "version"]));
  const cvOk = cv !== null && semver.gte(cv, MIN_COMPOSE);
  checks.push({
    name: "Compose v2",
    ok: cvOk,
    detail:
      cv === null
        ? "plugin missing. `apt install docker.io` omits it — install Docker's " +
          "official packages or Docker Desktop."
        : cvOk
          ? `v${cv}`
          : `v${cv} — need >= ${MIN_COMPOSE}`,
    fatal: true,
  });

  // Architecture
  const arch = checkArch(process.arch);
  checks.push({
    name: "Architecture",
    ok: arch.ok,
    detail: arch.ok ? process.arch : arch.message!,
    fatal: true,
  });

  // Disk + RAM
  const info = await dockerInfo();
  const disk = info.rootDir ? await freeDiskGb(info.rootDir) : null;
  if (disk !== null) {
    checks.push({
      name: "Disk",
      ok: disk >= HARD_DISK_GB,
      detail:
        disk >= MIN_DISK_GB
          ? `${disk.toFixed(0)} GB free`
          : `${disk.toFixed(0)} GB free — ${MIN_DISK_GB} GB recommended`,
      fatal: disk < HARD_DISK_GB,
    });
  }
  if (info.memGb !== null) {
    checks.push({
      name: "Memory",
      ok: info.memGb >= MIN_RAM_GB,
      detail:
        info.memGb >= WARN_RAM_GB
          ? `${info.memGb.toFixed(0)} GB`
          : `${info.memGb.toFixed(0)} GB — ${WARN_RAM_GB} GB recommended (Neo4j alone is capped at 4 GB)`,
      fatal: info.memGb < MIN_RAM_GB,
    });
  }

  // Ports (localhost mode only)
  for (const port of opts.ports ?? []) {
    const free = await checkPort(port, opts.bindAddr ?? "127.0.0.1");
    checks.push({
      name: `Port ${port}`,
      ok: free,
      detail: free ? "free" : "in use — choose another",
      fatal: false,
    });
  }

  return { ok: checks.every((c) => c.ok || !c.fatal), checks };
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npm test`
Expected: PASS — 11 new tests

- [ ] **Step 5: Verify against the real machine**

```bash
node --import tsx -e '
import { runPreflight } from "./src/preflight.js";
const r = await runPreflight({ ports: [3000, 3001, 8000, 7474, 7687] });
for (const c of r.checks) console.log(` ${c.ok ? "OK  " : c.fatal ? "FAIL" : "warn"} ${c.name}: ${c.detail}`);
console.log("overall:", r.ok);
'
```

Expected: Docker, Compose, Node, Architecture all `OK`; disk/memory reported with real numbers; `overall: true`.

- [ ] **Step 6: Commit**

```bash
git add src/preflight.ts test/preflight.test.ts
git commit -m "feat(preflight): check Docker, Compose, arch, disk, RAM and ports

Version parsers are pure and unit-tested against real command output shapes,
including the 'compose plugin missing' case that apt's docker.io produces —
the single most likely Linux failure, so its detail names the fix."
```

---

## Task 4: `secrets.ts` — generation and validation

**Files:**
- Create: `src/secrets.ts`
- Test: `test/secrets.test.ts`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `generateSecrets(): GeneratedSecrets` where
    `interface GeneratedSecrets { neo4jPassword: string; adminPassword: string; adminApiKey: string; sessionSecret: string; chatEncryptionKey: string }`
  - `WEAK_VALUES: ReadonlySet<string>` — mirrors the backend's rejection list.
  - `validateSecret(kind: keyof GeneratedSecrets, value: string): string | null` — returns an error message or `null`.
  Task 7's wizard calls `validateSecret` on every custom value; Task 6 writes the result into `.env`.

The weak-value set must mirror `backend/app/config.py`'s production validator exactly, so a bad custom secret is rejected at the prompt rather than by a container that refuses to boot 90 seconds later.

- [ ] **Step 1: Write the failing test**

Create `test/secrets.test.ts`:

```typescript
import { test } from "node:test";
import assert from "node:assert/strict";
import { generateSecrets, validateSecret, WEAK_VALUES } from "../src/secrets.js";

test("generates all five secrets", () => {
  const s = generateSecrets();
  for (const k of ["neo4jPassword", "adminPassword", "adminApiKey", "sessionSecret", "chatEncryptionKey"] as const) {
    assert.ok(s[k].length > 0, `${k} is empty`);
  }
});

test("admin API key carries the cortex_admin_ prefix", () => {
  assert.match(generateSecrets().adminApiKey, /^cortex_admin_[0-9a-f]{64}$/);
});

test("session secret is comfortably over the 32-char minimum", () => {
  assert.ok(generateSecrets().sessionSecret.length >= 64);
});

test("chat encryption key is 32 bytes base64", () => {
  const k = generateSecrets().chatEncryptionKey;
  assert.equal(Buffer.from(k, "base64").length, 32);
});

test("admin password avoids visually ambiguous characters", () => {
  for (let i = 0; i < 50; i++) {
    assert.doesNotMatch(generateSecrets().adminPassword, /[0O1lI]/);
  }
});

test("generated secrets are never members of the weak set", () => {
  for (let i = 0; i < 50; i++) {
    const s = generateSecrets();
    for (const v of Object.values(s)) assert.equal(WEAK_VALUES.has(v), false);
  }
});

test("every generated secret differs between calls", () => {
  // All five, not just one: a refactor that accidentally reused a single
  // randomBytes call for two fields would otherwise leave the suite green.
  const a = generateSecrets();
  const b = generateSecrets();
  for (const k of Object.keys(a) as Array<keyof typeof a>) {
    assert.notEqual(a[k], b[k], `${k} repeated across calls`);
  }
});

test("every generated secret passes its own validator", () => {
  const s = generateSecrets();
  for (const k of Object.keys(s) as Array<keyof typeof s>) {
    assert.equal(validateSecret(k, s[k]), null, `${k} failed validation`);
  }
});

test("rejects an empty value", () => {
  assert.match(validateSecret("neo4jPassword", "")!, /empty|required/i);
});

test("rejects a session secret shorter than 32 chars", () => {
  assert.match(validateSecret("sessionSecret", "a".repeat(31))!, /32/);
});

test("accepts a session secret of exactly 32 chars", () => {
  assert.equal(validateSecret("sessionSecret", "a".repeat(32)), null);
});

test("rejects known placeholder values the backend refuses", () => {
  assert.ok(validateSecret("adminApiKey", "cortex_admin_your-secure-api-key-here"));
  assert.ok(validateSecret("adminPassword", "your-secure-admin-password"));
  assert.ok(validateSecret("neo4jPassword", "password123"));
});

test("rejects any CHANGE_ME-prefixed value", () => {
  assert.ok(validateSecret("neo4jPassword", "CHANGE_ME_please"));
});

test("rejects a chat encryption key that is not 32 bytes base64", () => {
  assert.ok(validateSecret("chatEncryptionKey", "dGVzdA=="));
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm test`
Expected: FAIL — cannot find `../src/secrets.js`

- [ ] **Step 3: Write the implementation**

Create `src/secrets.ts`:

```typescript
import { randomBytes, randomInt } from "node:crypto";

export interface GeneratedSecrets {
  neo4jPassword: string;
  adminPassword: string;
  adminApiKey: string;
  sessionSecret: string;
  chatEncryptionKey: string;
}

/**
 * Mirrors the backend's production validator (backend/app/config.py). Keeping
 * this list in sync means a bad custom secret is rejected at the prompt rather
 * than by a container that refuses to boot 90 seconds later.
 */
export const WEAK_VALUES: ReadonlySet<string> = new Set([
  "",
  "secret",
  "password123",
  "your-pass",
  "another-pass",
  "custom-api-keyyy",
  "your-secure-admin-password",
  "cortex_admin_your-secure-api-key-here",
  "your-session-secret-key-at-least-32-characters-long",
  "default-secret-key-min-32-characters-long",
]);

/** Unambiguous alphabet: no 0/O/1/l/I, which get mistyped when transcribed. */
const READABLE = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789";

function readableGroup(len: number): string {
  let s = "";
  for (let i = 0; i < len; i++) s += READABLE[randomInt(READABLE.length)];
  return s;
}

export function generateSecrets(): GeneratedSecrets {
  return {
    // base64url of 32 bytes, stripped to an alnum-safe subset: Compose
    // interpolates `$` inside .env values, so avoid it entirely.
    neo4jPassword: randomBytes(32).toString("base64url").replace(/[^A-Za-z0-9]/g, "").slice(0, 40),
    // 4 groups of 4 from a 57-symbol alphabet — transcribable over the phone,
    // ~93 bits.
    adminPassword: [0, 1, 2, 3].map(() => readableGroup(4)).join("-"),
    adminApiKey: `cortex_admin_${randomBytes(32).toString("hex")}`,
    sessionSecret: randomBytes(48).toString("hex"),
    chatEncryptionKey: randomBytes(32).toString("base64"),
  };
}

function isPlaceholder(v: string): boolean {
  return WEAK_VALUES.has(v) || v.startsWith("CHANGE_ME");
}

export function validateSecret(kind: keyof GeneratedSecrets, value: string): string | null {
  if (value.length === 0) return "must not be empty";
  if (isPlaceholder(value)) {
    return "is a known placeholder the backend refuses in production — pick another";
  }
  if (value.includes("$")) {
    return "must not contain `$` — Docker Compose interpolates it inside .env values";
  }
  if (kind === "sessionSecret" && value.length < 32) {
    return "must be at least 32 characters";
  }
  if (kind === "chatEncryptionKey") {
    // Buffer.from(..., "base64") never throws — it decodes leniently — so a
    // length check is the only meaningful validation, and a try/catch here
    // would be dead code implying a failure mode that cannot occur.
    if (Buffer.from(value, "base64").length !== 32) {
      return "must be exactly 32 bytes, base64 encoded (openssl rand -base64 32)";
    }
  }
  return null;
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npm test`
Expected: PASS — 14 new tests

- [ ] **Step 5: Cross-check the weak set against the real backend validator**

The list must not drift from the backend. Verify it matches:

```bash
grep -A12 'weak_values = {' /Volumes/WD_BLACK/PROJECTS/CORTEX/cortex/backend/app/config.py
```

Confirm every string there appears in `WEAK_VALUES`, and that the `CHANGE_ME` prefix rule is handled. Note any difference in your report rather than silently diverging.

- [ ] **Step 6: Commit**

```bash
git add src/secrets.ts test/secrets.test.ts
git commit -m "feat(secrets): generate and validate credentials

WEAK_VALUES mirrors the backend's production validator so a bad custom secret
fails at the prompt, not at container boot. Generated values are checked
against that set, and \`\$\` is rejected everywhere because Compose
interpolates it inside .env values."
```

---

## Task 5: `providers.ts` + `validate.ts` — live LLM probes

**Files:**
- Create: `src/providers.ts`, `src/validate.ts`
- Create: `test/fake-openai.ts`
- Test: `test/validate.test.ts`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `PROVIDERS: Provider[]` where `interface Provider { id: string; label: string; baseUrl: string; needsKey: boolean; hint?: string }`
  - `listModels(cfg): Promise<string[]>` — empty array when the endpoint has no `/v1/models`.
  - `probeChat(cfg): Promise<{ ok: true; ms: number } | { ok: false; status?: number; body?: string }>`
  - `probeEmbedding(cfg): Promise<{ ok: true; dimension: number; sendDimensions: boolean } | { ok: false; status?: number; body?: string }>`
  where `cfg = { baseUrl: string; apiKey?: string; model: string; timeoutMs?: number }`.

`sendDimensions` is determined empirically: probe once without a `dimensions` parameter to learn the natural dimension, then once with it. Success → `true`, failure → `false`. A hardcoded per-model table goes stale; this does not.

Getting `EMBEDDING_DIMENSION` right at install time matters disproportionately — Neo4j bakes it into the vector index, and changing it later forces a full re-embed.

- [ ] **Step 1: Write the fake provider server**

Create `test/fake-openai.ts`:

```typescript
import { createServer, type Server } from "node:http";

export interface FakeOpts {
  /** Omit /v1/models entirely (some gateways do). */
  noModelsEndpoint?: boolean;
  /** Reject every request with 401. */
  unauthorized?: boolean;
  /** Natural embedding dimension. */
  dimension?: number;
  /** Reject requests that carry a `dimensions` parameter. */
  rejectDimensions?: boolean;
  /** Never respond, to exercise timeouts. */
  hang?: boolean;
}

export async function startFakeOpenAI(opts: FakeOpts = {}): Promise<{ url: string; close: () => Promise<void>; server: Server }> {
  const dim = opts.dimension ?? 1536;

  const server = createServer((req, res) => {
    if (opts.hang) return;
    if (opts.unauthorized) {
      res.writeHead(401, { "content-type": "application/json" });
      return res.end(JSON.stringify({ error: { message: "invalid api key" } }));
    }

    const url = req.url ?? "";
    const send = (code: number, body: unknown) => {
      res.writeHead(code, { "content-type": "application/json" });
      res.end(JSON.stringify(body));
    };

    if (url.startsWith("/v1/models")) {
      if (opts.noModelsEndpoint) return send(404, { error: { message: "not found" } });
      return send(200, { data: [{ id: "gpt-test" }, { id: "text-embedding-test" }] });
    }

    if (url.startsWith("/v1/chat/completions")) {
      return send(200, { choices: [{ message: { role: "assistant", content: "ok" } }] });
    }

    if (url.startsWith("/v1/embeddings")) {
      let raw = "";
      req.on("data", (c) => (raw += c));
      req.on("end", () => {
        let parsed: any = {};
        try { parsed = JSON.parse(raw); } catch { /* ignore */ }
        if (opts.rejectDimensions && parsed.dimensions !== undefined) {
          return send(400, { error: { message: "dimensions is not supported by this model" } });
        }
        const size = parsed.dimensions ?? dim;
        return send(200, { data: [{ embedding: new Array(size).fill(0) }] });
      });
      return;
    }

    send(404, { error: { message: "unknown route" } });
  });

  await new Promise<void>((res) => server.listen(0, "127.0.0.1", res));
  const addr = server.address();
  const port = typeof addr === "object" && addr ? addr.port : 0;
  return {
    url: `http://127.0.0.1:${port}/v1`,
    server,
    close: () => new Promise<void>((res) => server.close(() => res())),
  };
}
```

- [ ] **Step 2: Write the failing test**

Create `test/validate.test.ts`:

```typescript
import { test } from "node:test";
import assert from "node:assert/strict";
import { startFakeOpenAI } from "./fake-openai.js";
import { listModels, probeChat, probeEmbedding } from "../src/validate.js";
import { PROVIDERS } from "../src/providers.js";

test("every provider preset has an id, label and https base URL (except local ones)", () => {
  assert.ok(PROVIDERS.length >= 5);
  for (const p of PROVIDERS) {
    assert.ok(p.id && p.label, `provider missing id/label: ${JSON.stringify(p)}`);
    if (p.id !== "ollama" && p.id !== "other") {
      assert.match(p.baseUrl, /^https:\/\//, `${p.id} base URL should be https`);
    }
  }
});

test("provider ids are unique", () => {
  assert.equal(new Set(PROVIDERS.map((p) => p.id)).size, PROVIDERS.length);
});

test("lists models from a compliant endpoint", async () => {
  const f = await startFakeOpenAI();
  try {
    const models = await listModels({ baseUrl: f.url, apiKey: "k", model: "" });
    assert.deepEqual(models, ["gpt-test", "text-embedding-test"]);
  } finally { await f.close(); }
});

test("returns an empty list when /v1/models is absent, rather than throwing", async () => {
  const f = await startFakeOpenAI({ noModelsEndpoint: true });
  try {
    assert.deepEqual(await listModels({ baseUrl: f.url, apiKey: "k", model: "" }), []);
  } finally { await f.close(); }
});

test("chat probe succeeds and reports elapsed ms", async () => {
  const f = await startFakeOpenAI();
  try {
    const r = await probeChat({ baseUrl: f.url, apiKey: "k", model: "gpt-test" });
    assert.equal(r.ok, true);
    assert.ok((r as any).ms >= 0);
  } finally { await f.close(); }
});

test("chat probe surfaces a 401 with its status", async () => {
  const f = await startFakeOpenAI({ unauthorized: true });
  try {
    const r = await probeChat({ baseUrl: f.url, apiKey: "bad", model: "gpt-test" });
    assert.equal(r.ok, false);
    assert.equal((r as any).status, 401);
  } finally { await f.close(); }
});

test("embedding probe detects the natural dimension", async () => {
  const f = await startFakeOpenAI({ dimension: 1024 });
  try {
    const r = await probeEmbedding({ baseUrl: f.url, apiKey: "k", model: "e" });
    assert.equal(r.ok, true);
    assert.equal((r as any).dimension, 1024);
  } finally { await f.close(); }
});

test("embedding probe reports sendDimensions=true when the model accepts the parameter", async () => {
  const f = await startFakeOpenAI({ dimension: 1536 });
  try {
    const r = await probeEmbedding({ baseUrl: f.url, apiKey: "k", model: "e" });
    assert.equal((r as any).sendDimensions, true);
  } finally { await f.close(); }
});

test("embedding probe reports sendDimensions=false for fixed-dimension models", async () => {
  const f = await startFakeOpenAI({ dimension: 4096, rejectDimensions: true });
  try {
    const r = await probeEmbedding({ baseUrl: f.url, apiKey: "k", model: "e" });
    assert.equal(r.ok, true);
    assert.equal((r as any).dimension, 4096);
    assert.equal((r as any).sendDimensions, false);
  } finally { await f.close(); }
});

test("probes time out rather than hanging forever", async () => {
  const f = await startFakeOpenAI({ hang: true });
  try {
    const r = await probeChat({ baseUrl: f.url, apiKey: "k", model: "m", timeoutMs: 300 });
    assert.equal(r.ok, false);
    assert.match(String((r as any).body ?? ""), /timed out/i);
  } finally { await f.close(); }
});
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `npm test`
Expected: FAIL — cannot find `../src/validate.js`

- [ ] **Step 4: Write `src/providers.ts`**

```typescript
export interface Provider {
  id: string;
  label: string;
  baseUrl: string;
  needsKey: boolean;
  hint?: string;
}

export const PROVIDERS: Provider[] = [
  { id: "openai", label: "OpenAI", baseUrl: "https://api.openai.com/v1", needsKey: true },
  { id: "openrouter", label: "OpenRouter", baseUrl: "https://openrouter.ai/api/v1", needsKey: true },
  { id: "venice", label: "Venice", baseUrl: "https://api.venice.ai/api/v1", needsKey: true },
  { id: "groq", label: "Groq", baseUrl: "https://api.groq.com/openai/v1", needsKey: true },
  {
    id: "ollama",
    label: "Ollama (local)",
    baseUrl: "http://host.docker.internal:11434/v1",
    needsKey: false,
    hint: "host.docker.internal reaches the host from inside the container",
  },
  { id: "other", label: "Other OpenAI-compatible", baseUrl: "", needsKey: true },
];

export function providerById(id: string): Provider | undefined {
  return PROVIDERS.find((p) => p.id === id);
}
```

- [ ] **Step 5: Write `src/validate.ts`**

```typescript
export interface ProbeConfig {
  baseUrl: string;
  apiKey?: string;
  model: string;
  timeoutMs?: number;
}

export type ProbeFail = { ok: false; status?: number; body?: string };
export type ChatOk = { ok: true; ms: number };
export type EmbedOk = { ok: true; dimension: number; sendDimensions: boolean };

const DEFAULT_TIMEOUT = 20_000;

function headers(apiKey?: string): Record<string, string> {
  const h: Record<string, string> = { "content-type": "application/json" };
  if (apiKey) h.authorization = `Bearer ${apiKey}`;
  return h;
}

function url(baseUrl: string, path: string): string {
  return `${baseUrl.replace(/\/+$/, "")}${path}`;
}

async function call(
  cfg: ProbeConfig,
  path: string,
  init?: RequestInit
): Promise<{ res: Response; text: string } | { error: string }> {
  const ac = new AbortController();
  const timer = setTimeout(() => ac.abort(), cfg.timeoutMs ?? DEFAULT_TIMEOUT);
  try {
    const res = await fetch(url(cfg.baseUrl, path), {
      ...init,
      headers: headers(cfg.apiKey),
      signal: ac.signal,
    });
    return { res, text: await res.text() };
  } catch (err: any) {
    return { error: err?.name === "AbortError" ? "request timed out" : String(err?.message ?? err) };
  } finally {
    clearTimeout(timer);
  }
}

/** Model ids, or [] when the endpoint does not implement /v1/models. */
export async function listModels(cfg: ProbeConfig): Promise<string[]> {
  const r = await call(cfg, "/models", { method: "GET" });
  if ("error" in r || !r.res.ok) return [];
  try {
    const j = JSON.parse(r.text);
    return Array.isArray(j?.data)
      ? j.data.map((m: any) => String(m?.id)).filter((s: string) => s && s !== "undefined")
      : [];
  } catch {
    return [];
  }
}

export async function probeChat(cfg: ProbeConfig): Promise<ChatOk | ProbeFail> {
  const started = Date.now();
  const r = await call(cfg, "/chat/completions", {
    method: "POST",
    body: JSON.stringify({
      model: cfg.model,
      messages: [{ role: "user", content: "ping" }],
      max_tokens: 1,
    }),
  });
  if ("error" in r) return { ok: false, body: r.error };
  if (!r.res.ok) return { ok: false, status: r.res.status, body: r.text.slice(0, 300) };
  return { ok: true, ms: Date.now() - started };
}

/**
 * Two calls, deliberately: the first learns the model's natural dimension, the
 * second asks whether it accepts an explicit `dimensions` parameter. Empirical
 * beats a hardcoded per-model table, which goes stale.
 */
export async function probeEmbedding(cfg: ProbeConfig): Promise<EmbedOk | ProbeFail> {
  const first = await call(cfg, "/embeddings", {
    method: "POST",
    body: JSON.stringify({ model: cfg.model, input: "cortex" }),
  });
  if ("error" in first) return { ok: false, body: first.error };
  if (!first.res.ok) return { ok: false, status: first.res.status, body: first.text.slice(0, 300) };

  let dimension: number;
  try {
    dimension = JSON.parse(first.text)?.data?.[0]?.embedding?.length ?? 0;
  } catch {
    return { ok: false, body: "embedding response was not JSON" };
  }
  if (!dimension) return { ok: false, body: "embedding response contained no vector" };

  const second = await call(cfg, "/embeddings", {
    method: "POST",
    body: JSON.stringify({ model: cfg.model, input: "cortex", dimensions: dimension }),
  });
  const sendDimensions = !("error" in second) && second.res.ok;

  return { ok: true, dimension, sendDimensions };
}
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `npm test`
Expected: PASS — 10 new tests

- [ ] **Step 7: Commit**

```bash
git add src/providers.ts src/validate.ts test/validate.test.ts test/fake-openai.ts
git commit -m "feat(validate): provider presets and live LLM probes

Probes run before anything is written, so a bad key or model fails in seconds
rather than after a 1.4 GB pull. EMBEDDING_DIMENSION and
EMBEDDING_SEND_DIMENSIONS are detected empirically with two calls — Neo4j
bakes the dimension into its vector index, so getting it wrong costs a full
re-embed. A missing /v1/models degrades to free-text entry instead of failing."
```

---

## Task 6: `env.ts` — render `.env`

**Files:**
- Create: `src/env.ts`
- Test: `test/env.test.ts`

**Interfaces:**
- Consumes: `Stack`/`imageRefs` (Task 2), `GeneratedSecrets` (Task 4).
- Produces:
  - `interface InstallConfig { mode: "localhost" | "domain"; dir: string; projectName: string; stack: Stack; secrets: GeneratedSecrets; adminEmail: string; llm: {...}; ports: {...}; domains?: {...}; smtp?: {...}; errorReporting: boolean; advanced?: {...} }`
  - `renderEnv(cfg: InstallConfig): string`
  - `REQUIRED_VARS: readonly string[]` — the `${VAR:?}` set the released compose enforces.
  Task 8 writes the output to `.env` with mode `600`; Task 9's `update` rewrites only the image lines.

- [ ] **Step 1: Write the failing test**

Create `test/env.test.ts`:

```typescript
import { test } from "node:test";
import assert from "node:assert/strict";
import { renderEnv, REQUIRED_VARS, type InstallConfig } from "../src/env.js";
import { parseStack } from "../src/stack.js";
import { generateSecrets } from "../src/secrets.js";

const stack = parseStack({
  stack: "1.0.0",
  components: { backend: "1.0.0", frontend: "1.0.0", chat: "1.0.0", neo4j: "5.26-community", caddy: "2-alpine" },
  minInstaller: "1.0.0",
});

function base(over: Partial<InstallConfig> = {}): InstallConfig {
  return {
    mode: "localhost",
    dir: "/tmp/cortex",
    projectName: "cortex",
    stack,
    secrets: generateSecrets(),
    adminEmail: "me@example.com",
    llm: {
      providerId: "openai",
      baseUrl: "https://api.openai.com/v1",
      apiKey: "sk-test",
      chatModel: "gpt-5.2",
      embeddingModel: "text-embedding-3-small",
      embeddingDimension: 1536,
      embeddingSendDimensions: true,
    },
    ports: { app: 3000, chat: 3001, api: 8000, neo4jHttp: 7474, neo4jBolt: 7687 },
    errorReporting: false,
    ...over,
  };
}

function parse(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const line of text.split("\n")) {
    const t = line.trim();
    if (!t || t.startsWith("#")) continue;
    const eq = t.indexOf("=");
    if (eq > 0) out[t.slice(0, eq)] = t.slice(eq + 1);
  }
  return out;
}

test("localhost mode selects the ports overlay", () => {
  assert.equal(parse(renderEnv(base())).COMPOSE_FILE, "docker-compose.yml:docker-compose.ports.yml");
});

test("domain mode selects the caddy overlay", () => {
  const cfg = base({ mode: "domain", domains: { app: "c.example.com", chat: "ch.example.com", acmeEmail: "a@example.com" } });
  assert.equal(parse(renderEnv(cfg)).COMPOSE_FILE, "docker-compose.yml:docker-compose.caddy.yml");
});

test("pins all three image refs from the stack", () => {
  const e = parse(renderEnv(base()));
  assert.equal(e.CORTEX_BACKEND_IMAGE, "ghcr.io/mocaos/cortex-backend:1.0.0");
  assert.equal(e.CORTEX_FRONTEND_IMAGE, "ghcr.io/mocaos/cortex-frontend:1.0.0");
  assert.equal(e.CORTEX_CHAT_IMAGE, "ghcr.io/mocaos/cortex-chat:1.0.0");
  assert.equal(e.NEO4J_VERSION, "5.26-community");
  assert.equal(e.CADDY_VERSION, "2-alpine");
});

test("every required variable is present and non-empty in localhost mode", () => {
  const e = parse(renderEnv(base()));
  for (const v of REQUIRED_VARS.filter((v) => !["APP_DOMAIN", "CHAT_DOMAIN", "ACME_EMAIL"].includes(v))) {
    assert.ok(e[v] && e[v].length > 0, `${v} missing or empty`);
  }
});

test("domain mode also fills the three domain-only required vars", () => {
  const cfg = base({ mode: "domain", domains: { app: "c.example.com", chat: "ch.example.com", acmeEmail: "a@example.com" } });
  const e = parse(renderEnv(cfg));
  assert.equal(e.APP_DOMAIN, "c.example.com");
  assert.equal(e.CHAT_DOMAIN, "ch.example.com");
  assert.equal(e.ACME_EMAIL, "a@example.com");
});

test("NEVER writes SESSION_COOKIE_SECURE — the overlay owns it", () => {
  assert.equal("SESSION_COOKIE_SECURE" in parse(renderEnv(base())), false);
  const cfg = base({ mode: "domain", domains: { app: "a.example.com", chat: "b.example.com", acmeEmail: "c@example.com" } });
  assert.equal("SESSION_COOKIE_SECURE" in parse(renderEnv(cfg)), false);
});

test("NEVER writes NEXT_PUBLIC_API_URL", () => {
  assert.doesNotMatch(renderEnv(base()), /NEXT_PUBLIC_API_URL/);
});

test("error reporting is off by default — DSNs empty, chat disabled", () => {
  const e = parse(renderEnv(base()));
  assert.equal(e.SENTRY_DSN_BACKEND, "");
  assert.equal(e.SENTRY_DSN_FRONTEND, "");
  assert.equal(e.CHAT_SENTRY_DISABLED, "1");
});

test("opting into error reporting does not invent a DSN", () => {
  const e = parse(renderEnv(base({ errorReporting: true })));
  assert.equal(e.CHAT_SENTRY_DISABLED, "0");
});

test("domain mode narrows CORS to the two real origins", () => {
  const cfg = base({ mode: "domain", domains: { app: "c.example.com", chat: "ch.example.com", acmeEmail: "a@example.com" } });
  assert.equal(parse(renderEnv(cfg)).CORS_ALLOWED_ORIGINS, "https://c.example.com,https://ch.example.com");
});

test("domain mode sets CHAT_BASE_URL so password reset links work", () => {
  const cfg = base({ mode: "domain", domains: { app: "c.example.com", chat: "ch.example.com", acmeEmail: "a@example.com" } });
  assert.equal(parse(renderEnv(cfg)).CHAT_BASE_URL, "https://ch.example.com");
});

test("localhost mode writes the chosen ports", () => {
  const e = parse(renderEnv(base({ ports: { app: 4000, chat: 4001, api: 9000, neo4jHttp: 8474, neo4jBolt: 8687 } })));
  assert.equal(e.APP_PORT, "4000");
  assert.equal(e.CHAT_PORT, "4001");
  assert.equal(e.API_PORT, "9000");
});

test("binds loopback only", () => {
  assert.equal(parse(renderEnv(base())).BIND_ADDR, "127.0.0.1");
});

test("embedding settings come from the probe results", () => {
  const e = parse(renderEnv(base()));
  assert.equal(e.EMBEDDING_DIMENSION, "1536");
  assert.equal(e.EMBEDDING_SEND_DIMENSIONS, "true");
});

test("omits optional advanced vars when unset", () => {
  const text = renderEnv(base());
  assert.doesNotMatch(text, /^GRAPH_EXTRACTION_MODEL=/m);
  assert.doesNotMatch(text, /^SMTP_HOST=/m);
});

test("includes advanced overrides when provided", () => {
  const text = renderEnv(base({ advanced: { graphExtractionModel: "qwen3", visionModel: "qwen3-vl" } }));
  assert.match(text, /^GRAPH_EXTRACTION_MODEL=qwen3$/m);
  assert.match(text, /^VISION_MODEL=qwen3-vl$/m);
});

test("output has no CRLF and ends with a newline", () => {
  const text = renderEnv(base());
  assert.doesNotMatch(text, /\r/);
  assert.ok(text.endsWith("\n"));
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm test`
Expected: FAIL — cannot find `../src/env.js`

- [ ] **Step 3: Write the implementation**

Create `src/env.ts`:

```typescript
import { imageRefs, type Stack } from "./stack.js";
import type { GeneratedSecrets } from "./secrets.js";

export interface InstallConfig {
  mode: "localhost" | "domain";
  dir: string;
  projectName: string;
  stack: Stack;
  secrets: GeneratedSecrets;
  adminEmail: string;
  llm: {
    providerId: string;
    baseUrl: string;
    apiKey: string;
    chatModel: string;
    embeddingModel: string;
    embeddingDimension: number;
    embeddingSendDimensions: boolean;
    maxContext?: number;
    embeddingBaseUrl?: string;
    embeddingApiKey?: string;
  };
  ports: { app: number; chat: number; api: number; neo4jHttp: number; neo4jBolt: number };
  domains?: { app: string; chat: string; acmeEmail: string };
  smtp?: { host: string; port: number; user?: string; pass?: string; secure: boolean; from: string };
  errorReporting: boolean;
  advanced?: {
    graphExtractionModel?: string;
    visionModel?: string;
    enableReranking?: boolean;
    neo4jMemLimit?: string;
    batchConcurrency?: number;
  };
}

/** Every ${VAR:?} the released compose enforces. */
export const REQUIRED_VARS = [
  "CORTEX_BACKEND_IMAGE",
  "CORTEX_FRONTEND_IMAGE",
  "CORTEX_CHAT_IMAGE",
  "NEO4J_PASSWORD",
  "OPENAI_API_KEY",
  "ADMIN_PASSWORD",
  "ADMIN_API_KEY",
  "SESSION_SECRET",
  "CHAT_APP_ENCRYPTION_KEY",
  "APP_DOMAIN",
  "CHAT_DOMAIN",
  "ACME_EMAIL",
] as const;

const COMPOSE_FILES = {
  localhost: "docker-compose.yml:docker-compose.ports.yml",
  domain: "docker-compose.yml:docker-compose.caddy.yml",
} as const;

export function renderEnv(cfg: InstallConfig): string {
  const img = imageRefs(cfg.stack);
  const L: string[] = [];
  const put = (k: string, v: string | number) => L.push(`${k}=${v}`);
  const section = (title: string) => {
    L.push("", `# --- ${title} ${"-".repeat(Math.max(1, 68 - title.length))}`);
  };

  L.push(
    "# Cortex self-host configuration.",
    "#",
    "# Generated by `npx @mocaos/cortex`. This is the ONLY file the installer",
    "# authors — the compose files are release artifacts and are never edited,",
    "# so your changes here survive `cortex update`.",
    "#",
    "# Local compose changes belong in docker-compose.override.yml, which",
    "# Compose merges automatically and the installer never touches.",
    "#",
    "# Keep this file at mode 600: it holds every secret."
  );

  section("Mode");
  put("COMPOSE_FILE", COMPOSE_FILES[cfg.mode]);
  put("COMPOSE_PROJECT_NAME", cfg.projectName);

  section("Images (pinned from stack.json)");
  put("CORTEX_BACKEND_IMAGE", img.backend);
  put("CORTEX_FRONTEND_IMAGE", img.frontend);
  put("CORTEX_CHAT_IMAGE", img.chat);
  put("NEO4J_VERSION", cfg.stack.components.neo4j);
  put("CADDY_VERSION", cfg.stack.components.caddy);

  if (cfg.mode === "localhost") {
    section("Localhost mode");
    L.push("# Loopback only — 0.0.0.0 would expose Neo4j and the API to the network.");
    put("BIND_ADDR", "127.0.0.1");
    put("APP_PORT", cfg.ports.app);
    put("CHAT_PORT", cfg.ports.chat);
    put("API_PORT", cfg.ports.api);
    put("NEO4J_HTTP_PORT", cfg.ports.neo4jHttp);
    put("NEO4J_BOLT_PORT", cfg.ports.neo4jBolt);
    L.push(
      "",
      "# SESSION_COOKIE_SECURE is deliberately absent. The ports overlay sets it",
      "# false for plain HTTP; domain mode leaves it unset so the app defaults to",
      "# Secure. Setting it here would apply to both modes and strip Secure from",
      "# the admin session cookie on a public HTTPS site."
    );
  } else {
    const d = cfg.domains!;
    section("Public domain mode");
    L.push("# Both domains must already have A records pointing at this host.");
    put("APP_DOMAIN", d.app);
    put("CHAT_DOMAIN", d.chat);
    put("ACME_EMAIL", d.acmeEmail);
    put("CHAT_BASE_URL", `https://${d.chat}`);
    put("CORS_ALLOWED_ORIGINS", `https://${d.app},https://${d.chat}`);
  }

  section("Secrets");
  put("NEO4J_PASSWORD", cfg.secrets.neo4jPassword);
  put("ADMIN_EMAIL", cfg.adminEmail);
  put("ADMIN_PASSWORD", cfg.secrets.adminPassword);
  put("ADMIN_API_KEY", cfg.secrets.adminApiKey);
  put("SESSION_SECRET", cfg.secrets.sessionSecret);
  put("CHAT_APP_ENCRYPTION_KEY", cfg.secrets.chatEncryptionKey);

  section("LLM");
  put("OPENAI_API_KEY", cfg.llm.apiKey);
  put("OPENAI_API_BASE", cfg.llm.baseUrl);
  put("OPENAI_MODEL", cfg.llm.chatModel);
  if (cfg.llm.maxContext) put("OPENAI_MAX_CONTEXT", cfg.llm.maxContext);

  section("Embeddings");
  L.push("# EMBEDDING_DIMENSION is baked into the Neo4j vector index on first use.");
  L.push("# Changing it later forces a full re-embed of the corpus.");
  put("USE_OPENAI_EMBEDDINGS", "true");
  put("EMBEDDING_MODEL", cfg.llm.embeddingModel);
  put("EMBEDDING_DIMENSION", cfg.llm.embeddingDimension);
  put("EMBEDDING_SEND_DIMENSIONS", String(cfg.llm.embeddingSendDimensions));
  if (cfg.llm.embeddingBaseUrl) put("EMBEDDING_API_BASE", cfg.llm.embeddingBaseUrl);
  if (cfg.llm.embeddingApiKey) put("EMBEDDING_API_KEY", cfg.llm.embeddingApiKey);

  if (cfg.advanced) {
    section("Model overrides");
    const a = cfg.advanced;
    if (a.graphExtractionModel) put("GRAPH_EXTRACTION_MODEL", a.graphExtractionModel);
    if (a.visionModel) put("VISION_MODEL", a.visionModel);
    if (a.enableReranking !== undefined) put("ENABLE_RERANKING", String(a.enableReranking));
    if (a.neo4jMemLimit) put("CORTEX_NEO4J_MEM_LIMIT", a.neo4jMemLimit);
    if (a.batchConcurrency) put("BATCH_PROCESSING_CONCURRENCY", a.batchConcurrency);
  }

  section("Error tracking");
  L.push(
    "# Off by default. Your stack traces stay on your machine unless you set a",
    "# DSN pointing at your own GlitchTip/Sentry."
  );
  put("SENTRY_DSN_BACKEND", "");
  put("SENTRY_DSN_FRONTEND", "");
  put("CHAT_SENTRY_DISABLED", cfg.errorReporting ? "0" : "1");

  if (cfg.smtp) {
    section("Chat email");
    put("SMTP_HOST", cfg.smtp.host);
    put("SMTP_PORT", cfg.smtp.port);
    if (cfg.smtp.user) put("SMTP_USER", cfg.smtp.user);
    if (cfg.smtp.pass) put("SMTP_PASS", cfg.smtp.pass);
    put("SMTP_SECURE", String(cfg.smtp.secure));
    put("SMTP_FROM", cfg.smtp.from);
  }

  return `${L.join("\n")}\n`;
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npm test`
Expected: PASS — 17 new tests

- [ ] **Step 5: Prove the rendered `.env` satisfies the REAL released compose**

This is the drift guard: render an `.env`, drop it next to the actual fetched compose files, and let Compose validate it. A hand-maintained variable list would rot; this cannot.

```bash
node --import tsx -e '
import { mkdtempSync, writeFileSync, cpSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { execFileSync } from "node:child_process";
import { fetchStack } from "./src/stack.js";
import { generateSecrets } from "./src/secrets.js";
import { renderEnv } from "./src/env.js";

const dir = mkdtempSync(join(tmpdir(), "cortex-env-"));
execFileSync("bash", ["-c", `
  curl -fsSL https://api.github.com/repos/mocaOS/cortex-app/tarball/v1.0.0 -o ${dir}/s.tgz
  tar -xzf ${dir}/s.tgz -C ${dir} --strip-components=1 "mocaOS-cortex-app-*/selfhost/*" "mocaOS-cortex-app-*/ops/*"
  cp -r ${dir}/ops ${dir}/selfhost/ops
`]);

const stack = await fetchStack();
for (const mode of ["localhost", "domain"]) {
  const cfg = {
    mode, dir, projectName: "cortex", stack, secrets: generateSecrets(),
    adminEmail: "me@example.com",
    llm: { providerId: "openai", baseUrl: "https://api.openai.com/v1", apiKey: "sk-test",
           chatModel: "gpt-5.2", embeddingModel: "text-embedding-3-small",
           embeddingDimension: 1536, embeddingSendDimensions: true },
    ports: { app: 3000, chat: 3001, api: 8000, neo4jHttp: 7474, neo4jBolt: 7687 },
    domains: { app: "c.example.com", chat: "ch.example.com", acmeEmail: "a@example.com" },
    errorReporting: false,
  };
  writeFileSync(join(dir, "selfhost", ".env"), renderEnv(cfg));
  execFileSync("docker", ["compose", "config"], { cwd: join(dir, "selfhost"), stdio: "pipe" });
  console.log(`  ${mode}: docker compose config OK`);
}
'
```

Expected: `localhost: docker compose config OK` and `domain: docker compose config OK`. If a required variable were missing, Compose's `:?` guard would fail here.

- [ ] **Step 6: Commit**

```bash
git add src/env.ts test/env.test.ts
git commit -m "feat(env): render .env, the only file the installer authors

Explicit negative tests assert SESSION_COOKIE_SECURE and NEXT_PUBLIC_API_URL
are never written — both would silently break the stack (the first strips
Secure from the admin cookie on HTTPS, the second breaks same-origin API
calls). Validated against the real released compose so the variable set
cannot drift."
```

---

## Task 7: `artifacts.ts` + `state.ts` — fetch release files, track install state

**Files:**
- Create: `src/artifacts.ts`, `src/state.ts`
- Test: `test/artifacts.test.ts`, `test/state.test.ts`

**Interfaces:**
- Consumes: `Stack` (Task 2).
- Produces:
  - `fetchArtifacts(opts: { version: string; dir: string }): Promise<void>` — leaves `docker-compose*.yml`, `Caddyfile.template`, `.env.example`, `ops/backup/**` in `dir`, and copies `Caddyfile.template` → `Caddyfile`.
  - `ARTIFACT_FILES: readonly string[]` — what must exist afterwards.
  - `readState(dir: string): InstallState | null`, `writeState(dir, s): void`, `findInstallDir(start: string): string | null`
  - `interface InstallState { installer: string; stack: string; components: Stack["components"]; mode: "localhost"|"domain"; projectName: string; dir: string; installedAt: string; providerId: string; domains?: {...}; ports: {...}; previous?: { stack: string; components: Stack["components"] } }`

`cortex.json` never contains secrets — those live only in `.env`.

- [ ] **Step 1: Write the failing tests**

Create `test/state.test.ts`:

```typescript
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { readState, writeState, findInstallDir, type InstallState } from "../src/state.js";

const components = { backend: "1.0.0", frontend: "1.0.0", chat: "1.0.0", neo4j: "5.26-community", caddy: "2-alpine" };

function sample(dir: string): InstallState {
  return {
    installer: "1.0.0", stack: "1.0.0", components,
    mode: "localhost", projectName: "cortex", dir,
    installedAt: "2026-07-28T00:00:00.000Z", providerId: "openai",
    ports: { app: 3000, chat: 3001, api: 8000, neo4jHttp: 7474, neo4jBolt: 7687 },
  };
}

test("round-trips state through disk", () => {
  const dir = mkdtempSync(join(tmpdir(), "st-"));
  writeState(dir, sample(dir));
  assert.deepEqual(readState(dir), sample(dir));
});

test("readState returns null when absent", () => {
  assert.equal(readState(mkdtempSync(join(tmpdir(), "st-"))), null);
});

test("readState returns null on malformed JSON rather than throwing", () => {
  const dir = mkdtempSync(join(tmpdir(), "st-"));
  writeFileSync(join(dir, "cortex.json"), "{not json");
  assert.equal(readState(dir), null);
});

test("state never carries secret-looking keys", () => {
  const dir = mkdtempSync(join(tmpdir(), "st-"));
  writeState(dir, sample(dir));
  const raw = JSON.stringify(readState(dir));
  for (const k of ["password", "secret", "apiKey", "api_key", "encryptionKey"]) {
    assert.doesNotMatch(raw, new RegExp(k, "i"), `state leaked ${k}`);
  }
});

test("findInstallDir locates cortex.json in the directory itself", () => {
  const dir = mkdtempSync(join(tmpdir(), "st-"));
  writeState(dir, sample(dir));
  assert.equal(findInstallDir(dir), dir);
});

test("findInstallDir walks upwards", () => {
  const dir = mkdtempSync(join(tmpdir(), "st-"));
  writeState(dir, sample(dir));
  const nested = join(dir, "a", "b");
  mkdirSync(nested, { recursive: true });
  assert.equal(findInstallDir(nested), dir);
});

test("findInstallDir returns null when there is no install above", () => {
  assert.equal(findInstallDir(mkdtempSync(join(tmpdir(), "st-"))), null);
});
```

Create `test/artifacts.test.ts`:

```typescript
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, existsSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fetchArtifacts, ARTIFACT_FILES } from "../src/artifacts.js";

// Hits the real release. Skipped when offline.
test("fetches every artifact the stack needs from a real release tag", async (t) => {
  const dir = mkdtempSync(join(tmpdir(), "art-"));
  try {
    await fetchArtifacts({ version: "1.0.0", dir });
  } catch (err) {
    return t.skip(`network unavailable: ${(err as Error).message}`);
  }
  for (const f of ARTIFACT_FILES) {
    assert.ok(existsSync(join(dir, f)), `missing ${f}`);
  }
  assert.ok(existsSync(join(dir, "ops", "backup", "backup.sh")), "missing ops/backup/backup.sh");
  // Caddyfile must be a real copy, not left as a template only.
  assert.ok(existsSync(join(dir, "Caddyfile")), "Caddyfile was not created from the template");
  assert.equal(
    readFileSync(join(dir, "Caddyfile"), "utf8"),
    readFileSync(join(dir, "Caddyfile.template"), "utf8")
  );
});

test("rejects a version that has no release", async (t) => {
  const dir = mkdtempSync(join(tmpdir(), "art-"));
  try {
    await assert.rejects(fetchArtifacts({ version: "0.0.0-nope", dir }), /404|not found/i);
  } catch (err) {
    t.skip(`network unavailable: ${(err as Error).message}`);
  }
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `npm test`
Expected: FAIL — cannot find `../src/state.js` / `../src/artifacts.js`

- [ ] **Step 3: Write `src/state.ts`**

```typescript
import { readFileSync, writeFileSync, existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import type { Stack } from "./stack.js";

export interface InstallState {
  installer: string;
  stack: string;
  components: Stack["components"];
  mode: "localhost" | "domain";
  projectName: string;
  dir: string;
  installedAt: string;
  providerId: string;
  domains?: { app: string; chat: string; acmeEmail: string };
  ports: { app: number; chat: number; api: number; neo4jHttp: number; neo4jBolt: number };
  /** Kept so `update` can roll back to the previous pins. */
  previous?: { stack: string; components: Stack["components"] };
}

export const STATE_FILE = "cortex.json";

export function readState(dir: string): InstallState | null {
  const p = join(dir, STATE_FILE);
  if (!existsSync(p)) return null;
  try {
    return JSON.parse(readFileSync(p, "utf8")) as InstallState;
  } catch {
    return null;
  }
}

export function writeState(dir: string, state: InstallState): void {
  writeFileSync(join(dir, STATE_FILE), `${JSON.stringify(state, null, 2)}\n`);
}

/** Walk upwards looking for an install, so day-2 verbs work from anywhere inside it. */
export function findInstallDir(start: string): string | null {
  let cur = resolve(start);
  for (;;) {
    if (existsSync(join(cur, STATE_FILE))) return cur;
    const up = dirname(cur);
    if (up === cur) return null;
    cur = up;
  }
}
```

- [ ] **Step 4: Write `src/artifacts.ts`**

```typescript
import { execFile } from "node:child_process";
import { mkdtempSync, cpSync, rmSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { promisify } from "node:util";

const exec = promisify(execFile);

const REPO = "mocaOS/cortex-app";

/** Files that must exist in the install directory after a fetch. */
export const ARTIFACT_FILES = [
  "docker-compose.yml",
  "docker-compose.ports.yml",
  "docker-compose.caddy.yml",
  "Caddyfile.template",
  ".env.example",
] as const;

/**
 * Downloads the release tarball for `version` and lays the self-host artifacts
 * into `dir`. `ops/` must land beside the compose files because the backup
 * service builds from ./ops/backup, relative to the compose file. The release
 * attaches only stack.json, so the tarball is the supply route.
 */
export async function fetchArtifacts(opts: { version: string; dir: string }): Promise<void> {
  const url = `https://api.github.com/repos/${REPO}/tarball/v${opts.version}`;
  const work = mkdtempSync(join(tmpdir(), "cortex-art-"));
  const tgz = join(work, "src.tar.gz");

  try {
    // curl over fetch: follows redirects to codeload and streams to disk
    // without buffering a 6 MB body in memory.
    await exec("curl", ["-fsSL", url, "-o", tgz]);

    // Discover the archive's top-level directory instead of globbing for it.
    // GNU tar (every Linux install — the primary target) REFUSES wildcards in
    // member names without --wildcards, which bsdtar does not accept, so a
    // glob that works on macOS extracts nothing on Linux:
    //   tar: Pattern matching characters used in file names
    //   tar: mocaOS-cortex-app-*/selfhost: Not found in archive
    // Literal paths need no wildcard support and behave identically on both.
    const { stdout: listing } = await exec("tar", ["-tzf", tgz]);
    const prefix = listing.split("\n")[0]?.split("/")[0];
    if (!prefix) {
      throw new Error(`release v${opts.version} tarball is empty or unreadable`);
    }

    await exec("tar", [
      "-xzf", tgz,
      "-C", work,
      "--strip-components=1",
      `${prefix}/selfhost`,
      `${prefix}/ops`,
    ]);

    const src = join(work, "selfhost");
    if (!existsSync(src)) {
      throw new Error(`release v${opts.version} contains no selfhost/ directory`);
    }
    cpSync(src, opts.dir, { recursive: true });
    cpSync(join(work, "ops"), join(opts.dir, "ops"), { recursive: true });

    // The caddy overlay bind-mounts ./Caddyfile. If it is missing Docker
    // creates a root-owned DIRECTORY with that name and Caddy crash-loops on
    // "is a directory" with nothing pointing at the cause.
    cpSync(join(opts.dir, "Caddyfile.template"), join(opts.dir, "Caddyfile"));

    for (const f of ARTIFACT_FILES) {
      if (!existsSync(join(opts.dir, f))) {
        throw new Error(`release v${opts.version} is missing selfhost/${f}`);
      }
    }
  } catch (err: any) {
    const msg = String(err?.stderr ?? err?.message ?? err);
    if (/404/.test(msg)) {
      throw new Error(`No release found for v${opts.version} (HTTP 404)`);
    }
    throw new Error(`Could not fetch release artifacts for v${opts.version}: ${msg}`);
  } finally {
    rmSync(work, { recursive: true, force: true });
  }
}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `npm test`
Expected: PASS — 9 new tests (the two network tests skip cleanly if offline)

- [ ] **Step 6: Commit**

```bash
git add src/artifacts.ts src/state.ts test/artifacts.test.ts test/state.test.ts
git commit -m "feat: fetch release artifacts and track install state

fetchArtifacts lays selfhost/ plus ops/ into the install dir and copies
Caddyfile.template to Caddyfile — a missing ./Caddyfile makes Docker create a
root-owned directory and Caddy crash-loop with no clue why.

cortex.json holds no secrets, asserted by a test that greps the serialized
state for secret-looking keys."
```

---

## Task 8: `docker.ts` — the Compose driver

**Files:**
- Create: `src/docker.ts`
- Test: `test/docker.test.ts`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `composeArgs(dir: string): string[]` — the base `docker compose` args (`--project-directory`).
  - `parsePullProgress(line: string): { image: string; done: boolean } | null`
  - `parseHealth(psJson: string): Array<{ service: string; state: string; health: string | null }>`
  - `pull(dir, onProgress): Promise<void>`, `up(dir): Promise<void>`, `down(dir, volumes): Promise<void>`
  - `waitHealthy(dir, services, timeoutMs, onTick): Promise<boolean>`
  - `ps(dir): Promise<ServiceStatus[]>`, `execIn(dir, service, cmd): Promise<string>`, `logs(dir, service): Promise<number>`

`docker compose` is invoked with `--project-directory <dir>` so every verb works regardless of the caller's cwd, and `COMPOSE_FILE` from the install's `.env` selects the overlays.

- [ ] **Step 1: Write the failing test**

Create `test/docker.test.ts`:

```typescript
import { test } from "node:test";
import assert from "node:assert/strict";
import { composeArgs, parsePullProgress, parseHealth } from "../src/docker.js";

test("composeArgs pins the project directory so cwd does not matter", () => {
  const a = composeArgs("/opt/cortex");
  assert.deepEqual(a.slice(0, 3), ["compose", "--project-directory", "/opt/cortex"]);
});

test("parsePullProgress recognises a completed pull line", () => {
  const r = parsePullProgress(" backend Pulled ");
  assert.deepEqual(r, { image: "backend", done: true });
});

test("parsePullProgress recognises an in-flight pull line", () => {
  const r = parsePullProgress(" neo4j Pulling ");
  assert.deepEqual(r, { image: "neo4j", done: false });
});

test("parsePullProgress ignores layer chatter", () => {
  assert.equal(parsePullProgress(" 1f2a3b4c Downloading [====>   ] 12.4MB/98MB"), null);
});

test("parsePullProgress ignores blank lines", () => {
  assert.equal(parsePullProgress("   "), null);
});

test("parseHealth reads compose ps --format json lines", () => {
  const json = [
    JSON.stringify({ Service: "neo4j", State: "running", Health: "healthy" }),
    JSON.stringify({ Service: "backend", State: "running", Health: "starting" }),
    JSON.stringify({ Service: "frontend", State: "running", Health: "" }),
  ].join("\n");
  assert.deepEqual(parseHealth(json), [
    { service: "neo4j", state: "running", health: "healthy" },
    { service: "backend", state: "running", health: "starting" },
    { service: "frontend", state: "running", health: null },
  ]);
});

test("parseHealth handles a single JSON array instead of lines", () => {
  const json = JSON.stringify([{ Service: "chat", State: "running", Health: "" }]);
  assert.deepEqual(parseHealth(json), [{ service: "chat", state: "running", health: null }]);
});

test("parseHealth returns an empty list for empty input", () => {
  assert.deepEqual(parseHealth(""), []);
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm test`
Expected: FAIL — cannot find `../src/docker.js`

- [ ] **Step 3: Write the implementation**

Create `src/docker.ts`:

```typescript
import { spawn, execFile } from "node:child_process";
import { promisify } from "node:util";

const exec = promisify(execFile);

export interface ServiceStatus {
  service: string;
  state: string;
  health: string | null;
}

/**
 * --project-directory makes every verb independent of the caller's cwd.
 * COMPOSE_FILE in the install's .env selects the overlays, so no -f is needed.
 */
export function composeArgs(dir: string): string[] {
  return ["compose", "--project-directory", dir];
}

export function parsePullProgress(line: string): { image: string; done: boolean } | null {
  const m = line.trim().match(/^(\S+)\s+(Pulled|Pulling)$/);
  if (!m) return null;
  return { image: m[1], done: m[2] === "Pulled" };
}

export function parseHealth(psJson: string): ServiceStatus[] {
  const text = psJson.trim();
  if (!text) return [];

  const rows: any[] = [];
  if (text.startsWith("[")) {
    try { rows.push(...JSON.parse(text)); } catch { /* fall through */ }
  } else {
    for (const line of text.split("\n")) {
      const t = line.trim();
      if (!t) continue;
      try { rows.push(JSON.parse(t)); } catch { /* skip */ }
    }
  }

  return rows.map((r) => ({
    service: String(r.Service ?? ""),
    state: String(r.State ?? ""),
    health: r.Health ? String(r.Health) : null,
  }));
}

async function run(dir: string, args: string[]): Promise<string> {
  const { stdout, stderr } = await exec("docker", [...composeArgs(dir), ...args], {
    maxBuffer: 32 * 1024 * 1024,
  });
  return `${stdout}${stderr}`;
}

/** Streams pull progress; onProgress fires once per service transition. */
export function pull(
  dir: string,
  onProgress: (p: { image: string; done: boolean }) => void
): Promise<void> {
  return new Promise((resolve, reject) => {
    const child = spawn("docker", [...composeArgs(dir), "pull"], { stdio: ["ignore", "pipe", "pipe"] });
    const handle = (buf: Buffer) => {
      for (const line of buf.toString().split("\n")) {
        const p = parsePullProgress(line);
        if (p) onProgress(p);
      }
    };
    child.stdout.on("data", handle);
    child.stderr.on("data", handle);
    child.on("error", reject);
    child.on("close", (code) =>
      code === 0 ? resolve() : reject(new Error(`docker compose pull exited ${code}`))
    );
  });
}

export async function up(dir: string): Promise<void> {
  await run(dir, ["up", "-d", "--remove-orphans"]);
}

export async function down(dir: string, volumes = false): Promise<void> {
  await run(dir, volumes ? ["down", "-v"] : ["down"]);
}

export async function ps(dir: string): Promise<ServiceStatus[]> {
  return parseHealth(await run(dir, ["ps", "--format", "json"]));
}

export async function execIn(dir: string, service: string, cmd: string[]): Promise<string> {
  return run(dir, ["exec", "-T", service, ...cmd]);
}

/** Attaches `docker compose logs -f` to this process's stdio. */
export function logs(dir: string, service?: string): Promise<number> {
  return new Promise((resolve) => {
    const args = [...composeArgs(dir), "logs", "-f", "--tail", "200"];
    if (service) args.push(service);
    const child = spawn("docker", args, { stdio: "inherit" });
    child.on("close", (code) => resolve(code ?? 0));
  });
}

/**
 * Waits for the named services to report healthy. Services without a
 * healthcheck (frontend, chat, caddy) are satisfied by `running`.
 */
export async function waitHealthy(
  dir: string,
  services: string[],
  timeoutMs = 300_000,
  onTick?: (s: ServiceStatus[]) => void
): Promise<boolean> {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const status = await ps(dir);
    onTick?.(status);
    const ready = services.every((name) => {
      const s = status.find((x) => x.service === name);
      if (!s) return false;
      if (s.health) return s.health === "healthy";
      return s.state === "running";
    });
    if (ready) return true;
    if (Date.now() > deadline) return false;
    await new Promise((r) => setTimeout(r, 3000));
  }
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npm test`
Expected: PASS — 8 new tests

- [ ] **Step 5: Verify the parsers against real Compose output**

```bash
docker compose version
docker compose --project-directory /tmp ps --format json 2>&1 | head -2
```

Confirm `parseHealth` handles whatever shape your Compose version emits (newline-delimited objects on v2.21+, a JSON array on older builds — both are covered). Note the observed shape in your report.

- [ ] **Step 6: Commit**

```bash
git add src/docker.ts test/docker.test.ts
git commit -m "feat(docker): compose driver with pull progress and health waiting

Every invocation passes --project-directory so day-2 verbs work from anywhere.
waitHealthy treats services without a healthcheck (frontend, chat, caddy) as
ready when running, because the released compose declares healthchecks only on
neo4j, backend and backup."
```

---

## Task 9: `wizard.ts` + `commands/install.ts` — the install path

This is the headline deliverable: after this task, `npx @mocaos/cortex` works end to end.

**Files:**
- Create: `src/wizard.ts`, `src/commands/install.ts`
- Test: `test/wizard.test.ts`

**Interfaces:**
- Consumes: everything from Tasks 2–8.
- Produces:
  - `buildConfigNonInteractive(env: Record<string,string|undefined>, stack: Stack, dir: string): InstallConfig` — the `--yes` path; throws listing every missing required value at once.
  - `runWizard(opts): Promise<InstallConfig>` — interactive.
  - `run(ctx): Promise<void>` — the `install` command.

Nothing is written and no container starts until the LLM probes pass.

- [ ] **Step 1: Write the failing test**

Create `test/wizard.test.ts`:

```typescript
import { test } from "node:test";
import assert from "node:assert/strict";
import { buildConfigNonInteractive } from "../src/wizard.js";
import { parseStack } from "../src/stack.js";

const stack = parseStack({
  stack: "1.0.0",
  components: { backend: "1.0.0", frontend: "1.0.0", chat: "1.0.0", neo4j: "5.26-community", caddy: "2-alpine" },
  minInstaller: "1.0.0",
});

const MINIMAL = {
  CORTEX_ADMIN_EMAIL: "me@example.com",
  CORTEX_OPENAI_API_KEY: "sk-test",
  CORTEX_OPENAI_MODEL: "gpt-5.2",
  CORTEX_EMBEDDING_MODEL: "text-embedding-3-small",
  CORTEX_EMBEDDING_DIMENSION: "1536",
};

test("builds a localhost config from the minimum environment", () => {
  const cfg = buildConfigNonInteractive(MINIMAL, stack, "/tmp/c");
  assert.equal(cfg.mode, "localhost");
  assert.equal(cfg.adminEmail, "me@example.com");
  assert.equal(cfg.llm.embeddingDimension, 1536);
});

test("generates secrets when none are supplied", () => {
  const cfg = buildConfigNonInteractive(MINIMAL, stack, "/tmp/c");
  assert.ok(cfg.secrets.adminPassword.length > 0);
  assert.match(cfg.secrets.adminApiKey, /^cortex_admin_/);
});

test("honours supplied secrets instead of generating", () => {
  const cfg = buildConfigNonInteractive(
    { ...MINIMAL, CORTEX_ADMIN_PASSWORD: "My-Own-Pass-9x" },
    stack, "/tmp/c"
  );
  assert.equal(cfg.secrets.adminPassword, "My-Own-Pass-9x");
});

test("rejects a supplied secret that the backend would refuse", () => {
  assert.throws(
    () => buildConfigNonInteractive({ ...MINIMAL, CORTEX_ADMIN_PASSWORD: "your-secure-admin-password" }, stack, "/tmp/c"),
    /placeholder/i
  );
});

test("lists every missing required value at once rather than one at a time", () => {
  try {
    buildConfigNonInteractive({}, stack, "/tmp/c");
    assert.fail("should have thrown");
  } catch (err) {
    const m = (err as Error).message;
    assert.match(m, /CORTEX_ADMIN_EMAIL/);
    assert.match(m, /CORTEX_OPENAI_API_KEY/);
    assert.match(m, /CORTEX_OPENAI_MODEL/);
  }
});

test("domain mode requires the three domain values", () => {
  assert.throws(
    () => buildConfigNonInteractive({ ...MINIMAL, CORTEX_MODE: "domain" }, stack, "/tmp/c"),
    /CORTEX_APP_DOMAIN/
  );
});

test("domain mode succeeds when the domain values are present", () => {
  const cfg = buildConfigNonInteractive({
    ...MINIMAL,
    CORTEX_MODE: "domain",
    CORTEX_APP_DOMAIN: "c.example.com",
    CORTEX_CHAT_DOMAIN: "ch.example.com",
    CORTEX_ACME_EMAIL: "a@example.com",
  }, stack, "/tmp/c");
  assert.equal(cfg.mode, "domain");
  assert.equal(cfg.domains?.app, "c.example.com");
});

test("error reporting defaults to off", () => {
  assert.equal(buildConfigNonInteractive(MINIMAL, stack, "/tmp/c").errorReporting, false);
});

test("rejects an unknown mode", () => {
  assert.throws(
    () => buildConfigNonInteractive({ ...MINIMAL, CORTEX_MODE: "sideways" }, stack, "/tmp/c"),
    /sideways/
  );
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm test`
Expected: FAIL — cannot find `../src/wizard.js`

- [ ] **Step 3: Write `src/wizard.ts`**

```typescript
import { resolve4 } from "node:dns/promises";
import { prompts as p } from "./ui.js";
import { PROVIDERS, providerById } from "./providers.js";
import { generateSecrets, validateSecret, type GeneratedSecrets } from "./secrets.js";
import { listModels, probeChat, probeEmbedding } from "./validate.js";
import { checkPort } from "./preflight.js";
import type { InstallConfig } from "./env.js";
import type { Stack } from "./stack.js";

const DEFAULT_PORTS = { app: 3000, chat: 3001, api: 8000, neo4jHttp: 7474, neo4jBolt: 7687 };

function bail(msg: string): never {
  p.cancel(msg);
  process.exit(1);
}

/**
 * Spec: "offer alternatives on conflict". A taken port would otherwise surface
 * as a `docker compose up` failure after a 1.4 GB pull, so resolve it here.
 */
async function resolvePorts(): Promise<InstallConfig["ports"]> {
  const ports = { ...DEFAULT_PORTS };
  const labels: Array<[keyof typeof ports, string]> = [
    ["app", "Cortex"],
    ["chat", "Cortex Chat"],
    ["api", "backend API"],
    ["neo4jHttp", "Neo4j browser"],
    ["neo4jBolt", "Neo4j bolt"],
  ];

  for (const [key, label] of labels) {
    while (!(await checkPort(ports[key]))) {
      p.log.warn(`Port ${ports[key]} (${label}) is already in use.`);
      const next = await p.text({
        message: `Port for ${label}`,
        initialValue: String(ports[key] + 1000),
        validate: (v) => {
          const n = Number(v);
          if (!Number.isInteger(n) || n < 1 || n > 65535) return "Enter a port between 1 and 65535";
          if (Object.values(ports).includes(n)) return "Already used by another Cortex service";
          return undefined;
        },
      });
      if (p.isCancel(next)) bail("Cancelled.");
      ports[key] = Number(next);
    }
  }
  return ports;
}

/** --yes path. Throws with EVERY missing value, not just the first. */
export function buildConfigNonInteractive(
  env: Record<string, string | undefined>,
  stack: Stack,
  dir: string
): InstallConfig {
  const mode = (env.CORTEX_MODE ?? "localhost") as InstallConfig["mode"];
  if (mode !== "localhost" && mode !== "domain") {
    throw new Error(`CORTEX_MODE must be "localhost" or "domain", got "${mode}"`);
  }

  const missing: string[] = [];
  const need = (k: string): string => {
    const v = env[k];
    if (!v) { missing.push(k); return ""; }
    return v;
  };

  const adminEmail = need("CORTEX_ADMIN_EMAIL");
  const apiKey = need("CORTEX_OPENAI_API_KEY");
  const chatModel = need("CORTEX_OPENAI_MODEL");
  const embeddingModel = need("CORTEX_EMBEDDING_MODEL");
  const embeddingDimension = Number(need("CORTEX_EMBEDDING_DIMENSION"));

  let domains: InstallConfig["domains"];
  if (mode === "domain") {
    const app = need("CORTEX_APP_DOMAIN");
    const chat = need("CORTEX_CHAT_DOMAIN");
    const acmeEmail = need("CORTEX_ACME_EMAIL");
    domains = { app, chat, acmeEmail };
  }

  if (missing.length) {
    throw new Error(
      `Non-interactive install is missing required values:\n  - ${missing.join("\n  - ")}\n` +
        `Set them in the environment, or drop --yes to use the wizard.`
    );
  }

  // Supplied secrets override generated ones, and are validated the same way.
  const generated = generateSecrets();
  const overrides: Array<[keyof GeneratedSecrets, string | undefined]> = [
    ["neo4jPassword", env.CORTEX_NEO4J_PASSWORD],
    ["adminPassword", env.CORTEX_ADMIN_PASSWORD],
    ["adminApiKey", env.CORTEX_ADMIN_API_KEY],
    ["sessionSecret", env.CORTEX_SESSION_SECRET],
    ["chatEncryptionKey", env.CORTEX_CHAT_ENCRYPTION_KEY],
  ];
  const secrets = { ...generated };
  for (const [key, value] of overrides) {
    if (value === undefined) continue;
    const err = validateSecret(key, value);
    if (err) throw new Error(`${key} ${err}`);
    secrets[key] = value;
  }

  const provider = providerById(env.CORTEX_PROVIDER ?? "other");
  return {
    mode,
    dir,
    projectName: env.CORTEX_PROJECT_NAME ?? "cortex",
    stack,
    secrets,
    adminEmail,
    llm: {
      providerId: provider?.id ?? "other",
      baseUrl: env.CORTEX_OPENAI_API_BASE ?? provider?.baseUrl ?? "https://api.openai.com/v1",
      apiKey,
      chatModel,
      embeddingModel,
      embeddingDimension,
      embeddingSendDimensions: env.CORTEX_EMBEDDING_SEND_DIMENSIONS !== "false",
    },
    ports: {
      app: Number(env.CORTEX_APP_PORT ?? DEFAULT_PORTS.app),
      chat: Number(env.CORTEX_CHAT_PORT ?? DEFAULT_PORTS.chat),
      api: Number(env.CORTEX_API_PORT ?? DEFAULT_PORTS.api),
      neo4jHttp: Number(env.CORTEX_NEO4J_HTTP_PORT ?? DEFAULT_PORTS.neo4jHttp),
      neo4jBolt: Number(env.CORTEX_NEO4J_BOLT_PORT ?? DEFAULT_PORTS.neo4jBolt),
    },
    domains,
    errorReporting: env.CORTEX_ERROR_REPORTING === "true",
  };
}

export async function runWizard(opts: { stack: Stack; dir: string }): Promise<InstallConfig> {
  const mode = (await p.select({
    message: "How will you reach Cortex?",
    options: [
      { value: "localhost", label: "Localhost", hint: "http://localhost:3000" },
      { value: "domain", label: "Public domain", hint: "automatic HTTPS via Caddy" },
    ],
  })) as InstallConfig["mode"];
  if (p.isCancel(mode)) bail("Cancelled.");

  let domains: InstallConfig["domains"];
  if (mode === "domain") {
    const app = await p.text({
      message: "Domain for Cortex",
      placeholder: "cortex.example.com",
      validate: (v) => (v && v.includes(".") ? undefined : "Enter a fully-qualified domain"),
    });
    if (p.isCancel(app)) bail("Cancelled.");
    const chat = await p.text({
      message: "Domain for Cortex Chat",
      placeholder: "chat.example.com",
      validate: (v) => (v && v.includes(".") ? undefined : "Enter a fully-qualified domain"),
    });
    if (p.isCancel(chat)) bail("Cancelled.");
    const acmeEmail = await p.text({
      message: "Email for Let's Encrypt",
      validate: (v) => (v && v.includes("@") ? undefined : "Enter an email address"),
    });
    if (p.isCancel(acmeEmail)) bail("Cancelled.");
    domains = { app: String(app), chat: String(chat), acmeEmail: String(acmeEmail) };

    // Spec: resolve each domain and warn. Let's Encrypt validates over HTTP, so
    // a domain that does not resolve at all cannot possibly get a certificate —
    // catching it here beats a Caddy crash-loop after the images are pulled.
    for (const host of [domains.app, domains.chat]) {
      try {
        const addrs = await resolve4(host);
        p.log.success(`${host} resolves to ${addrs.join(", ")}`);
      } catch {
        p.log.warn(
          `${host} does not resolve. Certificate issuance will fail until its ` +
            `A record points at this host.`
        );
      }
    }
    const dnsOk = await p.confirm({
      message: "Continue? Both domains must point at this host before Caddy starts.",
      initialValue: true,
    });
    if (p.isCancel(dnsOk) || !dnsOk) bail("Cancelled. Nothing was written.");
  }

  const depth = await p.select({
    message: "Setup depth",
    options: [
      { value: "quick", label: "Quick", hint: "one provider, sensible defaults" },
      { value: "advanced", label: "Advanced", hint: "per-task models, resources, SMTP" },
    ],
  });
  if (p.isCancel(depth)) bail("Cancelled.");

  // --- LLM provider -------------------------------------------------------
  const providerId = await p.select({
    message: "LLM provider",
    options: PROVIDERS.map((pr) => ({ value: pr.id, label: pr.label, hint: pr.hint })),
  });
  if (p.isCancel(providerId)) bail("Cancelled.");
  const provider = providerById(String(providerId))!;

  let baseUrl = provider.baseUrl;
  if (!baseUrl) {
    const entered = await p.text({
      message: "OpenAI-compatible base URL",
      placeholder: "https://llm.example.com/v1",
      validate: (v) => (v?.startsWith("http") ? undefined : "Must start with http:// or https://"),
    });
    if (p.isCancel(entered)) bail("Cancelled.");
    baseUrl = String(entered);
  }

  let apiKey = "";
  if (provider.needsKey) {
    const entered = await p.password({
      message: "API key",
      validate: (v) => (v ? undefined : "Required"),
    });
    if (p.isCancel(entered)) bail("Cancelled.");
    apiKey = String(entered);
  }

  // --- model selection, from the real list when available -----------------
  const s = p.spinner();
  s.start("Fetching available models");
  const models = await listModels({ baseUrl, apiKey, model: "" });
  s.stop(models.length ? `${models.length} models from ${new URL(baseUrl).host}` : "Model list unavailable — enter names manually");

  /**
   * Falls back to free text in TWO cases, both real: the endpoint has no
   * /v1/models at all, and the endpoint lists models but none match the filter.
   * The second case is not hypothetical — OpenRouter serves embeddings happily
   * but lists zero embedding models, so filtering its 341 entries for /embed/
   * yields nothing. Showing the unfiltered list there would ask the user to
   * pick an embedding model from 341 chat models, none of which is valid.
   */
  const pickModel = async (
    message: string,
    filter: (m: string) => boolean,
    placeholder: string
  ): Promise<string> => {
    const matches = models.filter(filter);
    if (matches.length) {
      const v = await p.select({
        message,
        options: matches.map((m) => ({ value: m, label: m })),
      });
      if (p.isCancel(v)) bail("Cancelled.");
      return String(v);
    }
    if (models.length) {
      p.log.info(
        `This endpoint lists ${models.length} models but none look like a match ` +
          `for "${message.toLowerCase()}" — enter the name yourself.`
      );
    }
    const v = await p.text({
      message,
      placeholder,
      validate: (x) => (x ? undefined : "Required"),
    });
    if (p.isCancel(v)) bail("Cancelled.");
    return String(v);
  };

  const chatModel = await pickModel(
    "Chat model",
    (m) => !/embed/i.test(m),
    "gpt-5.2"
  );
  const embeddingModel = await pickModel(
    "Embedding model",
    (m) => /embed/i.test(m),
    "text-embedding-3-small"
  );

  // --- probes: nothing is written until these pass -------------------------
  s.start("Testing chat completion");
  const chatProbe = await probeChat({ baseUrl, apiKey, model: chatModel });
  if (!chatProbe.ok) {
    s.stop("Chat probe failed");
    p.log.error(
      `${chatProbe.status ? `HTTP ${chatProbe.status}` : "Request failed"}: ${chatProbe.body ?? ""}`
    );
    bail("The LLM endpoint did not answer. Nothing was written.");
  }
  s.stop(`Chat completion OK (${chatProbe.ms} ms)`);

  s.start("Testing embeddings");
  const embedProbe = await probeEmbedding({ baseUrl, apiKey, model: embeddingModel });
  if (!embedProbe.ok) {
    s.stop("Embedding probe failed");
    p.log.error(
      `${embedProbe.status ? `HTTP ${embedProbe.status}` : "Request failed"}: ${embedProbe.body ?? ""}`
    );
    bail("The embedding endpoint did not answer. Nothing was written.");
  }
  s.stop(`Embeddings OK — ${embedProbe.dimension} dimensions detected`);
  p.log.info(
    "This dimension is baked into the Neo4j vector index. Changing it later " +
      "requires re-embedding everything."
  );

  // --- identity + secrets --------------------------------------------------
  const adminEmail = await p.text({
    message: "Admin email",
    placeholder: "you@example.com",
    validate: (v) => (v?.includes("@") ? undefined : "Enter an email address"),
  });
  if (p.isCancel(adminEmail)) bail("Cancelled.");

  const secretChoice = await p.select({
    message: "Secrets",
    options: [
      { value: "generate", label: "Generate all five automatically" },
      { value: "custom", label: "Let me set them" },
    ],
  });
  if (p.isCancel(secretChoice)) bail("Cancelled.");

  let secrets = generateSecrets();
  if (secretChoice === "custom") {
    const keys: Array<[keyof GeneratedSecrets, string]> = [
      ["adminPassword", "Admin password"],
      ["neo4jPassword", "Neo4j password"],
      ["adminApiKey", "Admin API key"],
      ["sessionSecret", "Session secret (>= 32 chars)"],
      ["chatEncryptionKey", "Chat encryption key (32 bytes base64)"],
    ];
    for (const [key, label] of keys) {
      const v = await p.password({
        message: label,
        validate: (x) => validateSecret(key, String(x ?? "")) ?? undefined,
      });
      if (p.isCancel(v)) bail("Cancelled.");
      secrets = { ...secrets, [key]: String(v) };
    }
  }

  const errorReporting = await p.confirm({
    message: "Send anonymous crash reports to the Cortex maintainers?",
    initialValue: false,
  });
  if (p.isCancel(errorReporting)) bail("Cancelled.");

  let advanced: InstallConfig["advanced"];
  let smtp: InstallConfig["smtp"];
  if (depth === "advanced") {
    const gx = await p.text({ message: "Graph extraction model (blank to inherit)", defaultValue: "" });
    if (p.isCancel(gx)) bail("Cancelled.");
    const vm = await p.text({ message: "Vision model (blank to inherit)", defaultValue: "" });
    if (p.isCancel(vm)) bail("Cancelled.");
    advanced = {
      graphExtractionModel: String(gx) || undefined,
      visionModel: String(vm) || undefined,
    };

    const wantSmtp = await p.confirm({ message: "Configure SMTP for chat password reset?", initialValue: false });
    if (p.isCancel(wantSmtp)) bail("Cancelled.");
    if (wantSmtp) {
      const host = await p.text({ message: "SMTP host", validate: (v) => (v ? undefined : "Required") });
      if (p.isCancel(host)) bail("Cancelled.");
      const from = await p.text({ message: "From address", validate: (v) => (v?.includes("@") ? undefined : "Required") });
      if (p.isCancel(from)) bail("Cancelled.");
      smtp = { host: String(host), port: 587, secure: false, from: String(from) };
    }
  }

  return {
    mode,
    dir: opts.dir,
    projectName: "cortex",
    stack: opts.stack,
    secrets,
    adminEmail: String(adminEmail),
    llm: {
      providerId: provider.id,
      baseUrl,
      apiKey,
      chatModel,
      embeddingModel,
      embeddingDimension: embedProbe.dimension,
      embeddingSendDimensions: embedProbe.sendDimensions,
    },
    // Only localhost mode publishes ports, so only it needs conflict resolution.
    ports: mode === "localhost" ? await resolvePorts() : DEFAULT_PORTS,
    domains,
    smtp,
    errorReporting: Boolean(errorReporting),
    advanced,
  };
}
```

- [ ] **Step 4: Write `src/commands/install.ts`**

```typescript
import { existsSync, mkdirSync, writeFileSync, chmodSync } from "node:fs";
import { resolve, join } from "node:path";
import { banner, noteBox, prompts as p } from "../ui.js";
import { installerVersion } from "../version.js";
import { fetchStack, assertInstallerSupported } from "../stack.js";
import { runPreflight } from "../preflight.js";
import { fetchArtifacts } from "../artifacts.js";
import { renderEnv } from "../env.js";
import { writeState } from "../state.js";
import { pull, up, waitHealthy } from "../docker.js";
import { runWizard, buildConfigNonInteractive } from "../wizard.js";

export async function run(ctx: { flags: Record<string, string | boolean> }): Promise<void> {
  const version = installerVersion();
  banner(version);

  const dir = resolve(String(ctx.flags.dir ?? "./cortex"));
  if (existsSync(join(dir, "cortex.json"))) {
    p.cancel(
      `${dir} already contains an install.\n` +
        `  Use \`cortex update\` to move it to the latest release, or ` +
        `\`cortex config\` to change settings.`
    );
    process.exit(1);
  }

  // --- stack manifest -----------------------------------------------------
  const s = p.spinner();
  s.start("Reading the latest release manifest");
  const stack = await fetchStack({ version: ctx.flags.stack ? String(ctx.flags.stack) : undefined });
  assertInstallerSupported(stack, version);
  s.stop(`Cortex ${stack.stack}`);
  noteBox("Stack", [
    `backend   ${stack.components.backend}`,
    `frontend  ${stack.components.frontend}`,
    `chat      ${stack.components.chat}`,
    `neo4j     ${stack.components.neo4j}`,
  ]);

  // --- preflight ----------------------------------------------------------
  s.start("Checking your environment");
  // No ports here: the wizard resolves port conflicts interactively (and
  // domain mode publishes none), so checking defaults now would only produce a
  // warning the wizard immediately fixes.
  const pre = await runPreflight({});
  s.stop("Environment checked");
  for (const c of pre.checks) {
    const line = `${c.name}: ${c.detail}`;
    if (c.ok) p.log.success(line);
    else if (c.fatal) p.log.error(line);
    else p.log.warn(line);
  }
  if (!pre.ok) {
    p.cancel("Preflight failed. Fix the errors above and re-run.");
    process.exit(1);
  }

  // --- configuration ------------------------------------------------------
  const cfg = ctx.flags.yes
    ? buildConfigNonInteractive(process.env, stack, dir)
    : await runWizard({ stack, dir });

  // --- write, then start --------------------------------------------------
  mkdirSync(dir, { recursive: true });

  s.start("Fetching release artifacts");
  await fetchArtifacts({ version: stack.stack, dir });
  s.stop("Release artifacts in place");

  const envPath = join(dir, ".env");
  writeFileSync(envPath, renderEnv(cfg));
  chmodSync(envPath, 0o600);
  p.log.success(`Wrote ${envPath} (mode 600)`);

  writeState(dir, {
    installer: version,
    stack: stack.stack,
    components: stack.components,
    mode: cfg.mode,
    projectName: cfg.projectName,
    dir,
    installedAt: new Date().toISOString(),
    providerId: cfg.llm.providerId,
    domains: cfg.domains,
    ports: cfg.ports,
  });

  const pulled = new Set<string>();
  s.start("Pulling images");
  await pull(dir, ({ image, done }) => {
    if (done) {
      pulled.add(image);
      s.message(`Pulling images — ${pulled.size} done`);
    }
  });
  s.stop(`Pulled ${pulled.size} images`);

  s.start("Starting the stack");
  await up(dir);
  s.stop("Stack started");

  s.start("Waiting for services to become healthy");
  const healthy = await waitHealthy(dir, ["neo4j", "backend", "frontend", "chat"], 300_000, (st) => {
    s.message(`Waiting — ${st.map((x) => `${x.service}:${x.health ?? x.state}`).join(" ")}`);
  });
  s.stop(healthy ? "All services healthy" : "Timed out waiting for health");

  if (!healthy) {
    p.log.warn(`Run \`npx @mocaos/cortex logs\` in ${dir} to see what is wrong.`);
  }

  const urls =
    cfg.mode === "localhost"
      ? [`Cortex   http://localhost:${cfg.ports.app}`, `Chat     http://localhost:${cfg.ports.chat}`]
      : [`Cortex   https://${cfg.domains!.app}`, `Chat     https://${cfg.domains!.chat}`];

  noteBox("Cortex is running", [
    ...urls,
    "",
    `Login    ${cfg.adminEmail}`,
    `Password ${cfg.secrets.adminPassword}`,
    "",
    "This password is shown once. It is stored in .env.",
  ]);
  p.outro("npx @mocaos/cortex status · logs · update");
}
```

- [ ] **Step 5: Run the tests**

Run: `npm test`
Expected: PASS — 9 new wizard tests, all previous suites still green

- [ ] **Step 6: Real end-to-end install, non-interactive**

This is the task's real acceptance test. It pulls ~1.4 GB and starts a full stack, so expect several minutes.

```bash
npm run build
rm -rf /tmp/e2e && mkdir -p /tmp/e2e

CORTEX_ADMIN_EMAIL=e2e@example.com \
CORTEX_OPENAI_API_KEY="${OPENAI_API_KEY:?set a real key to exercise the probes}" \
CORTEX_OPENAI_MODEL=gpt-5.2 \
CORTEX_EMBEDDING_MODEL=text-embedding-3-small \
CORTEX_EMBEDDING_DIMENSION=1536 \
CORTEX_PROVIDER=openai \
node dist/cli.js install --yes --dir /tmp/e2e/cortex

echo "--- service state ---"
docker compose --project-directory /tmp/e2e/cortex ps
echo "--- backend health ---"
curl -fsS http://localhost:8000/health | head -c 200; echo
echo "--- frontend ---"; curl -fsS -o /dev/null -w "%{http_code}\n" http://localhost:3000
echo "--- chat ---";     curl -fsS -o /dev/null -w "%{http_code}\n" http://localhost:3001
echo "--- .env perms (expect 600) ---"; stat -f "%Lp" /tmp/e2e/cortex/.env
echo "--- no secrets in cortex.json ---"
grep -iE "password|secret|api_?key" /tmp/e2e/cortex/cortex.json && echo "LEAK" || echo "clean"
```

Expected: every service up, `/health` 200, frontend and chat both `200`, `.env` mode `600`, `cortex.json` clean.

Leave the stack running for Task 10, which needs an existing install.

- [ ] **Step 7: Commit**

```bash
git add src/wizard.ts src/commands/install.ts test/wizard.test.ts
git commit -m "feat(install): the interactive install path

The LLM probes run before anything is written and before any image is pulled,
so a bad key or model name fails in seconds instead of after 1.4 GB. The
non-interactive path reports every missing value at once rather than one per
run, and validates supplied secrets with the same rules the backend enforces."
```

---

## Task 10: Day-2 verbs — `status`, `logs`, lifecycle, `doctor`

**Files:**
- Create: `src/commands/{status,logs,start,stop,restart,doctor}.ts`
- Test: `test/status.test.ts`

**Interfaces:**
- Consumes: `findInstallDir`/`readState` (Task 7), `docker.ts` (Task 8).
- Produces: `resolveInstall(flags): { dir: string; state: InstallState }` in `src/commands/_shared.ts`, used by every day-2 verb. `formatStatusTable(rows, state): string[]`.

- [ ] **Step 1: Write the failing test**

Create `test/status.test.ts`:

```typescript
import { test } from "node:test";
import assert from "node:assert/strict";
import { formatStatusTable, serviceUrl } from "../src/commands/_shared.js";
import type { InstallState } from "../src/state.js";

const state: InstallState = {
  installer: "1.0.0", stack: "1.0.0",
  components: { backend: "1.0.0", frontend: "1.0.0", chat: "1.0.0", neo4j: "5.26-community", caddy: "2-alpine" },
  mode: "localhost", projectName: "cortex", dir: "/tmp/c",
  installedAt: "2026-07-28T00:00:00.000Z", providerId: "openai",
  ports: { app: 3000, chat: 3001, api: 8000, neo4jHttp: 7474, neo4jBolt: 7687 },
};

test("renders one line per service", () => {
  const rows = formatStatusTable(
    [
      { service: "neo4j", state: "running", health: "healthy" },
      { service: "backend", state: "running", health: "starting" },
    ],
    state
  );
  assert.equal(rows.length, 2);
  assert.match(rows[0], /neo4j/);
  assert.match(rows[0], /healthy/);
});

test("shows the bare state for services without a healthcheck", () => {
  const rows = formatStatusTable([{ service: "frontend", state: "running", health: null }], state);
  assert.match(rows[0], /running/);
  assert.doesNotMatch(rows[0], /healthy/);
});

test("localhost URLs use the configured ports", () => {
  assert.equal(serviceUrl(state, "frontend"), "http://localhost:3000");
  assert.equal(serviceUrl(state, "chat"), "http://localhost:3001");
});

test("domain URLs use https and the configured domains", () => {
  const d: InstallState = { ...state, mode: "domain", domains: { app: "c.example.com", chat: "ch.example.com", acmeEmail: "a@example.com" } };
  assert.equal(serviceUrl(d, "frontend"), "https://c.example.com");
  assert.equal(serviceUrl(d, "chat"), "https://ch.example.com");
});

test("services with no user-facing URL return null", () => {
  assert.equal(serviceUrl(state, "backup"), null);
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm test`
Expected: FAIL — cannot find `../src/commands/_shared.js`

- [ ] **Step 3: Write `src/commands/_shared.ts`**

```typescript
import { resolve } from "node:path";
import { prompts as p } from "../ui.js";
import { findInstallDir, readState, type InstallState } from "../state.js";
import type { ServiceStatus } from "../docker.js";

export function resolveInstall(flags: Record<string, string | boolean>): {
  dir: string;
  state: InstallState;
} {
  const dir = flags.dir ? resolve(String(flags.dir)) : findInstallDir(process.cwd());
  if (!dir) {
    p.cancel(
      "No Cortex install found here or in any parent directory.\n" +
        "  Pass --dir <path>, or run `npx @mocaos/cortex` to create one."
    );
    process.exit(1);
  }
  const state = readState(dir);
  if (!state) {
    p.cancel(`${dir} has no readable cortex.json — is this a Cortex install?`);
    process.exit(1);
  }
  return { dir, state };
}

export function serviceUrl(state: InstallState, service: string): string | null {
  if (state.mode === "domain") {
    if (service === "frontend") return `https://${state.domains!.app}`;
    if (service === "chat") return `https://${state.domains!.chat}`;
    return null;
  }
  if (service === "frontend") return `http://localhost:${state.ports.app}`;
  if (service === "chat") return `http://localhost:${state.ports.chat}`;
  if (service === "backend") return `http://localhost:${state.ports.api}`;
  if (service === "neo4j") return `http://localhost:${state.ports.neo4jHttp}`;
  return null;
}

export function formatStatusTable(rows: ServiceStatus[], state: InstallState): string[] {
  const width = Math.max(8, ...rows.map((r) => r.service.length));
  return rows.map((r) => {
    const status = r.health ?? r.state;
    const url = serviceUrl(state, r.service);
    return `${r.service.padEnd(width)}  ${status.padEnd(9)}  ${url ?? ""}`.trimEnd();
  });
}
```

- [ ] **Step 4: Write the verb modules**

`src/commands/status.ts`:

```typescript
import { banner, noteBox, prompts as p } from "../ui.js";
import { installerVersion } from "../version.js";
import { ps } from "../docker.js";
import { resolveInstall, formatStatusTable } from "./_shared.js";

export async function run(ctx: { flags: Record<string, string | boolean> }): Promise<void> {
  banner(installerVersion());
  const { dir, state } = resolveInstall(ctx.flags);
  const rows = await ps(dir);
  noteBox(`Cortex ${state.stack} · ${state.mode} · ${dir}`,
    rows.length ? formatStatusTable(rows, state) : ["no containers — run `cortex start`"]);
  p.outro("");
}
```

`src/commands/logs.ts`:

```typescript
import { logs } from "../docker.js";
import { resolveInstall } from "./_shared.js";

export async function run(ctx: {
  flags: Record<string, string | boolean>;
  positionals: string[];
}): Promise<void> {
  const { dir } = resolveInstall(ctx.flags);
  process.exitCode = await logs(dir, ctx.positionals[0]);
}
```

`src/commands/start.ts`:

```typescript
import { banner, prompts as p } from "../ui.js";
import { installerVersion } from "../version.js";
import { up, waitHealthy } from "../docker.js";
import { resolveInstall } from "./_shared.js";

export async function run(ctx: { flags: Record<string, string | boolean> }): Promise<void> {
  banner(installerVersion());
  const { dir } = resolveInstall(ctx.flags);
  const s = p.spinner();
  s.start("Starting");
  await up(dir);
  s.stop("Started");
  s.start("Waiting for health");
  const ok = await waitHealthy(dir, ["neo4j", "backend", "frontend", "chat"]);
  s.stop(ok ? "Healthy" : "Timed out — check `cortex logs`");
  p.outro("");
}
```

`src/commands/stop.ts`:

```typescript
import { banner, prompts as p } from "../ui.js";
import { installerVersion } from "../version.js";
import { down } from "../docker.js";
import { resolveInstall } from "./_shared.js";

export async function run(ctx: { flags: Record<string, string | boolean> }): Promise<void> {
  banner(installerVersion());
  const { dir } = resolveInstall(ctx.flags);
  const s = p.spinner();
  s.start("Stopping");
  // No -v: volumes are the user's data and are never removed by `stop`.
  await down(dir, false);
  s.stop("Stopped — your data volumes are untouched");
  p.outro("");
}
```

`src/commands/restart.ts`:

```typescript
import { run as stop } from "./stop.js";
import { run as start } from "./start.js";

export async function run(ctx: { flags: Record<string, string | boolean> }): Promise<void> {
  await stop(ctx);
  await start(ctx);
}
```

`src/commands/doctor.ts`:

```typescript
import { readFileSync, existsSync } from "node:fs";
import { join } from "node:path";
import { banner, noteBox, prompts as p } from "../ui.js";
import { installerVersion } from "../version.js";
import { runPreflight } from "../preflight.js";
import { ps, execIn } from "../docker.js";
import { probeChat } from "../validate.js";
import { fetchStack } from "../stack.js";
import { resolveInstall, formatStatusTable } from "./_shared.js";

function readEnv(dir: string): Record<string, string> {
  const p = join(dir, ".env");
  if (!existsSync(p)) return {};
  const out: Record<string, string> = {};
  for (const line of readFileSync(p, "utf8").split("\n")) {
    const t = line.trim();
    if (!t || t.startsWith("#")) continue;
    const eq = t.indexOf("=");
    if (eq > 0) out[t.slice(0, eq)] = t.slice(eq + 1);
  }
  return out;
}

export async function run(ctx: { flags: Record<string, string | boolean> }): Promise<void> {
  banner(installerVersion());
  const { dir, state } = resolveInstall(ctx.flags);
  const env = readEnv(dir);
  const lines: string[] = [];

  const pre = await runPreflight({});
  for (const c of pre.checks) lines.push(`${c.ok ? "ok  " : "FAIL"} ${c.name}: ${c.detail}`);

  const rows = await ps(dir);
  lines.push("", ...formatStatusTable(rows, state));

  // LLM key still valid?
  if (env.OPENAI_API_KEY && env.OPENAI_API_BASE && env.OPENAI_MODEL) {
    const r = await probeChat({
      baseUrl: env.OPENAI_API_BASE,
      apiKey: env.OPENAI_API_KEY,
      model: env.OPENAI_MODEL,
      timeoutMs: 15_000,
    });
    lines.push("", r.ok ? `ok   LLM reachable (${r.ms} ms)` : `FAIL LLM: ${r.status ?? ""} ${r.body ?? ""}`);
  }

  // Backup freshness
  try {
    const out = await execIn(dir, "backup", ["sh", "-c", "cat /backups/LAST_SUCCESS 2>/dev/null || true"]);
    const ts = Number(out.trim());
    lines.push(
      Number.isFinite(ts) && ts > 0
        ? `ok   last verified backup ${new Date(ts * 1000).toISOString()}`
        : "warn no verified backup yet (first run is delayed)"
    );
  } catch {
    lines.push("warn backup sidecar not reachable");
  }

  // Newer release available?
  try {
    const latest = await fetchStack();
    lines.push(
      latest.stack === state.stack
        ? `ok   up to date (${state.stack})`
        : `note Cortex ${latest.stack} available — run \`cortex update\``
    );
  } catch {
    lines.push("warn could not check for updates");
  }

  noteBox("Diagnostics", lines);
  p.outro("Paste this block when asking for help.");
}
```

- [ ] **Step 5: Run the tests**

Run: `npm test`
Expected: PASS — 5 new tests

- [ ] **Step 6: Exercise every verb against the running install from Task 9**

```bash
npm run build
cd /tmp/e2e/cortex
node /path/to/cortex-installer/dist/cli.js status
node /path/to/cortex-installer/dist/cli.js doctor
node /path/to/cortex-installer/dist/cli.js restart
node /path/to/cortex-installer/dist/cli.js status
# logs runs in the foreground; confirm it streams then interrupt
timeout 5 node /path/to/cortex-installer/dist/cli.js logs backend || true
```

Expected: `status` lists every service with health and URLs; `doctor` prints preflight, service table, a successful LLM probe, backup freshness and an up-to-date line; `restart` cycles the stack and `status` shows it healthy again; `logs backend` streams.

Confirm `status` works from a **subdirectory** too, proving the upward walk:

```bash
mkdir -p /tmp/e2e/cortex/nested/deep && cd /tmp/e2e/cortex/nested/deep
node /path/to/cortex-installer/dist/cli.js status
```

- [ ] **Step 7: Commit**

```bash
git add src/commands test/status.test.ts
git commit -m "feat: status, logs, lifecycle and doctor verbs

Every verb resolves the install by walking upwards for cortex.json, so they
work from anywhere inside it. \`stop\` never passes -v — volumes are the
user's data. \`doctor\` emits one pasteable block covering preflight, health,
LLM reachability, backup freshness and update availability."
```

---

## Task 11: `update`, `config`, `backup`, `restore`, `uninstall`

**Files:**
- Create: `src/commands/{update,config,backup,restore,uninstall}.ts`
- Test: `test/update.test.ts`

**Interfaces:**
- Consumes: everything prior.
- Produces: `diffComponents(from, to): Array<{ name: string; from: string; to: string; changed: boolean }>` in `src/update.ts`.

`update` rewrites only the image lines in `.env` — it never regenerates the file, so user edits survive.

- [ ] **Step 1: Write the failing test**

Create `test/update.test.ts`:

```typescript
import { test } from "node:test";
import assert from "node:assert/strict";
import { diffComponents, rewriteImagePins } from "../src/update.js";

const from = { backend: "1.0.0", frontend: "1.0.0", chat: "1.0.0", neo4j: "5.26-community", caddy: "2-alpine" };

test("marks only the components that actually moved", () => {
  const d = diffComponents(from, { ...from, chat: "1.0.1" });
  assert.equal(d.find((x) => x.name === "chat")!.changed, true);
  assert.equal(d.find((x) => x.name === "backend")!.changed, false);
});

test("reports every component, changed or not", () => {
  assert.equal(diffComponents(from, from).length, 5);
});

test("an identical stack yields no changes", () => {
  assert.equal(diffComponents(from, from).every((x) => !x.changed), true);
});

test("rewrites only the image pin lines and preserves everything else", () => {
  const env = [
    "# a comment the user added",
    "COMPOSE_FILE=docker-compose.yml:docker-compose.ports.yml",
    "CORTEX_BACKEND_IMAGE=ghcr.io/mocaos/cortex-backend:1.0.0",
    "CORTEX_FRONTEND_IMAGE=ghcr.io/mocaos/cortex-frontend:1.0.0",
    "CORTEX_CHAT_IMAGE=ghcr.io/mocaos/cortex-chat:1.0.0",
    "NEO4J_VERSION=5.26-community",
    "CADDY_VERSION=2-alpine",
    "MY_CUSTOM_TWEAK=keep-me",
    "",
  ].join("\n");

  const out = rewriteImagePins(env, { ...from, backend: "1.1.0", chat: "1.0.1" });

  assert.match(out, /^CORTEX_BACKEND_IMAGE=ghcr\.io\/mocaos\/cortex-backend:1\.1\.0$/m);
  assert.match(out, /^CORTEX_CHAT_IMAGE=ghcr\.io\/mocaos\/cortex-chat:1\.0\.1$/m);
  assert.match(out, /^CORTEX_FRONTEND_IMAGE=ghcr\.io\/mocaos\/cortex-frontend:1\.0\.0$/m);
  assert.match(out, /^MY_CUSTOM_TWEAK=keep-me$/m);
  assert.match(out, /^# a comment the user added$/m);
});

test("rewriting is idempotent", () => {
  const env = "CORTEX_BACKEND_IMAGE=ghcr.io/mocaos/cortex-backend:1.0.0\n";
  const once = rewriteImagePins(env, from);
  assert.equal(rewriteImagePins(once, from), once);
});

test("does not touch a secret that happens to contain an image-like string", () => {
  const env = "ADMIN_API_KEY=cortex_admin_deadbeef\nCORTEX_BACKEND_IMAGE=ghcr.io/mocaos/cortex-backend:1.0.0\n";
  const out = rewriteImagePins(env, { ...from, backend: "2.0.0" });
  assert.match(out, /^ADMIN_API_KEY=cortex_admin_deadbeef$/m);
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm test`
Expected: FAIL — cannot find `../src/update.js`

- [ ] **Step 3: Write `src/update.ts`**

```typescript
import type { Stack } from "./stack.js";

type Components = Stack["components"];

const IMAGE_LINES: Record<string, (c: Components) => string> = {
  CORTEX_BACKEND_IMAGE: (c) => `ghcr.io/mocaos/cortex-backend:${c.backend}`,
  CORTEX_FRONTEND_IMAGE: (c) => `ghcr.io/mocaos/cortex-frontend:${c.frontend}`,
  CORTEX_CHAT_IMAGE: (c) => `ghcr.io/mocaos/cortex-chat:${c.chat}`,
  NEO4J_VERSION: (c) => c.neo4j,
  CADDY_VERSION: (c) => c.caddy,
};

export function diffComponents(
  from: Components,
  to: Components
): Array<{ name: string; from: string; to: string; changed: boolean }> {
  return (Object.keys(from) as Array<keyof Components>).map((name) => ({
    name,
    from: from[name],
    to: to[name],
    changed: from[name] !== to[name],
  }));
}

/**
 * Surgically replaces only the pin lines. The rest of .env — comments, custom
 * additions, secrets — is preserved byte for byte, so an update never clobbers
 * the user's edits.
 */
export function rewriteImagePins(envText: string, components: Components): string {
  return envText
    .split("\n")
    .map((line) => {
      const eq = line.indexOf("=");
      if (eq <= 0 || line.trimStart().startsWith("#")) return line;
      const key = line.slice(0, eq);
      const build = IMAGE_LINES[key];
      return build ? `${key}=${build(components)}` : line;
    })
    .join("\n");
}
```

- [ ] **Step 4: Write the verb modules**

`src/commands/update.ts`:

```typescript
import { readFileSync, writeFileSync, chmodSync } from "node:fs";
import { join } from "node:path";
import { banner, noteBox, prompts as p } from "../ui.js";
import { installerVersion } from "../version.js";
import { fetchStack, assertInstallerSupported } from "../stack.js";
import { fetchArtifacts } from "../artifacts.js";
import { writeState } from "../state.js";
import { pull, up, waitHealthy, execIn } from "../docker.js";
import { diffComponents, rewriteImagePins } from "../update.js";
import { resolveInstall } from "./_shared.js";

export async function run(ctx: { flags: Record<string, string | boolean> }): Promise<void> {
  const version = installerVersion();
  banner(version);
  const { dir, state } = resolveInstall(ctx.flags);

  const s = p.spinner();
  s.start("Checking for a newer release");
  const latest = await fetchStack({ version: ctx.flags.stack ? String(ctx.flags.stack) : undefined });
  assertInstallerSupported(latest, version);
  s.stop(`Installed ${state.stack} · available ${latest.stack}`);

  const diff = diffComponents(state.components, latest.components);
  if (!diff.some((d) => d.changed)) {
    p.outro(`Already on ${state.stack}. Nothing to do.`);
    return;
  }

  noteBox("Changes", diff.map((d) =>
    d.changed ? `${d.name.padEnd(9)} ${d.from} → ${d.to}` : `${d.name.padEnd(9)} ${d.from} (unchanged)`
  ));

  if (!ctx.flags.yes) {
    const go = await p.confirm({ message: `Update to ${latest.stack}?`, initialValue: true });
    if (p.isCancel(go) || !go) { p.cancel("Cancelled. Nothing changed."); return; }

    const backup = await p.confirm({ message: "Run a backup first?", initialValue: true });
    if (!p.isCancel(backup) && backup) {
      s.start("Backing up");
      try {
        await execIn(dir, "backup", ["/backup.sh"]);
        s.stop("Backup complete");
      } catch (err) {
        s.stop("Backup failed");
        p.log.error(String((err as Error).message).slice(0, 400));
        const anyway = await p.confirm({ message: "Continue without a fresh backup?", initialValue: false });
        if (p.isCancel(anyway) || !anyway) { p.cancel("Cancelled."); return; }
      }
    }
  }

  // Refresh compose files + ops/ from the new tag, then repin .env.
  s.start("Fetching release artifacts");
  await fetchArtifacts({ version: latest.stack, dir });
  s.stop("Release artifacts updated");

  const envPath = join(dir, ".env");
  writeFileSync(envPath, rewriteImagePins(readFileSync(envPath, "utf8"), latest.components));
  chmodSync(envPath, 0o600);
  p.log.success("Repinned images in .env (your other settings untouched)");

  const pulled = new Set<string>();
  s.start("Pulling images");
  await pull(dir, ({ image, done }) => { if (done) { pulled.add(image); s.message(`Pulling — ${pulled.size} done`); } });
  s.stop(`Pulled ${pulled.size} images`);

  s.start("Recreating containers");
  await up(dir);
  s.stop("Containers recreated");

  s.start("Waiting for health");
  const ok = await waitHealthy(dir, ["neo4j", "backend", "frontend", "chat"]);
  s.stop(ok ? "All services healthy" : "Timed out waiting for health");

  writeState(dir, {
    ...state,
    installer: version,
    stack: latest.stack,
    components: latest.components,
    previous: { stack: state.stack, components: state.components },
  });

  if (!ok) {
    p.log.error(
      `Health check timed out. Previous pins are recorded in cortex.json.\n` +
        `  To roll back: edit the CORTEX_*_IMAGE lines in .env back to ${state.stack}, ` +
        `then run \`cortex start\`.`
    );
  }
  p.outro(ok ? `Now on Cortex ${latest.stack}.` : "Update finished with warnings.");
}
```

`src/commands/config.ts`:

```typescript
import { existsSync } from "node:fs";
import { join } from "node:path";
import { banner, prompts as p } from "../ui.js";
import { installerVersion } from "../version.js";
import { resolveInstall } from "./_shared.js";

export async function run(ctx: { flags: Record<string, string | boolean> }): Promise<void> {
  banner(installerVersion());
  const { dir } = resolveInstall(ctx.flags);
  p.log.info(
    `Settings live in ${join(dir, ".env")}.\n` +
      `  Edit it, then run \`npx @mocaos/cortex restart\`.\n` +
      `  Compose changes belong in docker-compose.override.yml, which updates never touch.`
  );
  if (existsSync(join(dir, ".env.example"))) {
    p.log.info(`Every available variable is documented in ${join(dir, ".env.example")}.`);
  }
  p.outro("");
}
```

`src/commands/backup.ts`:

```typescript
import { banner, prompts as p } from "../ui.js";
import { installerVersion } from "../version.js";
import { execIn } from "../docker.js";
import { resolveInstall } from "./_shared.js";

export async function run(ctx: { flags: Record<string, string | boolean> }): Promise<void> {
  banner(installerVersion());
  const { dir } = resolveInstall(ctx.flags);
  const s = p.spinner();
  s.start("Running a verified backup");
  try {
    const out = await execIn(dir, "backup", ["/backup.sh"]);
    s.stop("Backup complete");
    p.log.info(out.trim().split("\n").slice(-6).join("\n"));
  } catch (err) {
    s.stop("Backup failed");
    p.log.error(String((err as Error).message).slice(0, 600));
    process.exitCode = 1;
  }
  p.outro("");
}
```

`src/commands/restore.ts`:

```typescript
import { banner, noteBox, prompts as p } from "../ui.js";
import { installerVersion } from "../version.js";
import { execIn } from "../docker.js";
import { resolveInstall } from "./_shared.js";

export async function run(ctx: {
  flags: Record<string, string | boolean>;
  positionals: string[];
}): Promise<void> {
  banner(installerVersion());
  const { dir, state } = resolveInstall(ctx.flags);

  const listing = await execIn(dir, "backup", ["sh", "-c", "ls -1 /backups | grep -v LAST_SUCCESS || true"]);
  const stamps = listing.trim().split("\n").filter(Boolean);
  if (!stamps.length) { p.cancel("No backups found."); return; }

  let stamp = ctx.positionals[0];
  if (!stamp) {
    const picked = await p.select({
      message: "Which backup?",
      options: stamps.slice(-20).reverse().map((t) => ({ value: t, label: t })),
    });
    if (p.isCancel(picked)) { p.cancel("Cancelled."); return; }
    stamp = String(picked);
  }

  noteBox("Restoring is destructive", [
    `This wipes the current graph and replays ${stamp}.`,
    "",
    "The graph restore alone does NOT bring back uploads, skills, apps or",
    "chat data — those live in file volumes and need the extra step below,",
    "because the sidecar mounts them read-only.",
    "",
    "Full runbook: ops/backup/restore.sh header, and selfhost/README.md.",
  ]);

  const typed = await p.text({ message: `Type "restore" to confirm`, validate: (v) => (v === "restore" ? undefined : "Type restore to continue") });
  if (p.isCancel(typed)) { p.cancel("Cancelled."); return; }

  // No spinner and no partial execution here on purpose: step 3 of the runbook
  // needs host-level `docker run` to write volumes the sidecar mounts
  // read-only, so wrapping only some steps would leave the operator with a
  // half-restored instance and no signal about it. Print the whole runbook.
  p.log.warn(
    `Now run these from ${dir} — they need host-level docker access that this\n` +
      `CLI deliberately does not wrap, because step 2 writes to volumes the\n` +
      `sidecar cannot:\n\n` +
      `  docker compose stop backend\n` +
      `  docker compose exec -e RESTORE_WIPE=yes backup /restore.sh ${stamp}\n` +
      `  docker run --rm \\\n` +
      `    -v ${state.projectName}_uploads_data:/data/uploads \\\n` +
      `    -v ${state.projectName}_custom_inputs_data:/data/custom_inputs \\\n` +
      `    -v ${state.projectName}_chat_data:/data/chat \\\n` +
      `    -v ${state.projectName}_skills_data:/data/skills \\\n` +
      `    -v ${state.projectName}_apps_data:/data/apps \\\n` +
      `    -v ${state.projectName}_backups:/backups:ro \\\n` +
      `    alpine tar -xzf /backups/${stamp}/files.tar.gz -C /\n` +
      `  docker compose start backend`
  );
  p.outro("Follow the steps above in order.");
}
```

`src/commands/uninstall.ts`:

```typescript
import { banner, noteBox, prompts as p } from "../ui.js";
import { installerVersion } from "../version.js";
import { down } from "../docker.js";
import { resolveInstall } from "./_shared.js";

export async function run(ctx: { flags: Record<string, string | boolean> }): Promise<void> {
  banner(installerVersion());
  const { dir, state } = resolveInstall(ctx.flags);

  const s = p.spinner();
  s.start("Removing containers");
  await down(dir, false);
  s.stop("Containers removed — volumes still intact");

  noteBox("Your data is still here", [
    "Removing volumes deletes, permanently:",
    "  the knowledge graph, uploaded documents, installed skills and apps,",
    "  chat accounts and history, and every backup.",
    "",
    `Volumes are prefixed ${state.projectName}_`,
  ]);

  const wipe = await p.confirm({ message: "Delete the data volumes too?", initialValue: false });
  if (p.isCancel(wipe) || !wipe) {
    p.outro(`Kept. Bring it back with \`cortex start\` in ${dir}.`);
    return;
  }

  const typed = await p.text({
    message: `Type "delete my data" to confirm`,
    validate: (v) => (v === "delete my data" ? undefined : "Type it exactly, or Ctrl-C to abort"),
  });
  if (p.isCancel(typed)) { p.outro("Kept."); return; }

  s.start("Removing volumes");
  await down(dir, true);
  s.stop("Volumes removed");
  p.outro(`Done. ${dir} still holds .env and cortex.json — delete it by hand if you want it gone.`);
}
```

- [ ] **Step 5: Run the tests**

Run: `npm test`
Expected: PASS — 6 new tests

- [ ] **Step 6: Exercise update and backup against the running install**

```bash
npm run build
cd /tmp/e2e/cortex

# Already on the latest stack — must be a clean no-op, not an error.
node /path/to/cortex-installer/dist/cli.js update --yes

# Backup should produce a verified run.
node /path/to/cortex-installer/dist/cli.js backup
docker compose --project-directory /tmp/e2e/cortex exec -T backup ls /backups

# Prove the surgical rewrite preserves user edits.
echo "MY_CUSTOM_TWEAK=keep-me" >> .env
node -e '
const { readFileSync, writeFileSync } = require("node:fs");
const { rewriteImagePins } = require("/path/to/cortex-installer/dist/update.js");
const before = readFileSync(".env","utf8");
const after = rewriteImagePins(before, { backend:"9.9.9", frontend:"9.9.9", chat:"9.9.9", neo4j:"5.26-community", caddy:"2-alpine" });
console.log("custom line kept:", /MY_CUSTOM_TWEAK=keep-me/.test(after));
console.log("backend repinned:", /cortex-backend:9\.9\.9/.test(after));
console.log("secret intact:", before.match(/^ADMIN_API_KEY=.*$/m)[0] === after.match(/^ADMIN_API_KEY=.*$/m)[0]);
'
```

Expected: `update --yes` reports already-current and exits 0; `backup` completes and `ls /backups` shows a timestamped directory; all three rewrite assertions print `true`.

- [ ] **Step 7: Commit**

```bash
git add src/update.ts src/commands test/update.test.ts
git commit -m "feat: update, config, backup, restore and uninstall

update rewrites only the image pin lines in .env, so comments, custom
additions and secrets survive byte for byte — asserted by tests. restore
deliberately does not wrap the file-volume step: the sidecar mounts those
read-only, so it prints the exact host commands instead of pretending a
one-liner is sufficient. uninstall needs a typed phrase before touching
volumes."
```

---

## Task 12: CI, release workflow, and the npm publish

**Files:**
- Create: `.github/workflows/ci.yml`, `.github/workflows/release.yml`
- Create: `scripts/check-version-sync.mjs`
- Modify: `package.json`

**Interfaces:**
- Consumes: everything.
- Produces: a published `@mocaos/cortex` on npm.

- [ ] **Step 1: Add the tag guard**

Create `scripts/check-version-sync.mjs`:

```javascript
#!/usr/bin/env node
// The tag must match package.json, so a release can never publish a version
// that disagrees with what `cortex --version` reports.
import { readFileSync } from "node:fs";

const pkg = JSON.parse(readFileSync("package.json", "utf8")).version;
const tagArg = process.argv[process.argv.indexOf("--tag") + 1];
const tag = tagArg?.startsWith("v") ? tagArg.slice(1) : tagArg;

if (tag && tag !== pkg) {
  console.error(`tag ${tagArg} does not match package.json version ${pkg}`);
  process.exit(1);
}
console.log(`Version ${pkg} OK`);
```

- [ ] **Step 2: Create the CI workflow**

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

permissions:
  contents: read

jobs:
  test:
    name: Test (Node ${{ matrix.node }})
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        # 20 is the floor declared in engines (20.12 via @clack); 22 and 24 are
        # what most users' npx will actually run.
        node: ["20", "22", "24"]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: ${{ matrix.node }}
          cache: npm
      - run: npm ci
      - run: npm run typecheck
      - run: npm test
      - run: npm run build
      - name: The built CLI must at least run
        run: node dist/cli.js --version
```

- [ ] **Step 3: Create the release workflow**

Create `.github/workflows/release.yml`:

```yaml
name: Release

on:
  push:
    # Stable semver only — a prerelease tag must not become `latest` on npm.
    tags: ["v[0-9]+.[0-9]+.[0-9]+"]

permissions:
  contents: write
  id-token: write   # npm provenance

jobs:
  publish:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: "22"
          cache: npm
          registry-url: "https://registry.npmjs.org"

      - run: npm ci
      - name: Tag must match package.json
        run: node scripts/check-version-sync.mjs --tag "${GITHUB_REF_NAME}"
      - run: npm run typecheck
      - run: npm test
      - run: npm run build

      - name: Publish to npm
        run: npm publish --access public --provenance
        env:
          NODE_AUTH_TOKEN: ${{ secrets.NPM_TOKEN }}

      - uses: softprops/action-gh-release@v2
        with:
          generate_release_notes: true
```

- [ ] **Step 4: Verify the workflows parse and the guard works**

```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml')); print('ci.yml OK')"
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/release.yml')); print('release.yml OK')"
node scripts/check-version-sync.mjs --tag v1.0.0
node scripts/check-version-sync.mjs --tag v9.9.9 && echo "SHOULD HAVE FAILED" || echo "correctly rejected a mismatched tag"
```

Expected: both `OK`, `Version 1.0.0 OK`, then `correctly rejected a mismatched tag`.

- [ ] **Step 5: Dry-run the package contents**

Confirm the tarball ships `dist/` and nothing sensitive:

```bash
npm run build
npm pack --dry-run 2>&1 | tail -25
```

Expected: only `dist/**`, `package.json`, `README.md`, `LICENSE`. No `src/`, no `test/`, no `.env`.

- [ ] **Step 6: Commit**

```bash
git add .github/workflows scripts/check-version-sync.mjs
git commit -m "ci: test matrix, and npm publish on a stable semver tag

Tests run on Node 20, 22 and 24 — 20.12 is the engines floor (set by @clack)
and 22/24 are what most users' npx will actually use. The release trigger accepts stable semver only,
so a prerelease tag can never take the npm \`latest\` dist-tag."
```

---

## Task 13: Documentation

**Files:**
- Create: `README.md` (installer repo)
- Modify (cortex-app): `handbook/26-self-hosting.md` (new), `handbook/README.md`, `README.md`, `.claude/development.md`, `CLAUDE.md`

**Interfaces:**
- Consumes: the finished CLI.
- Produces: the docs a user finds before running anything.

- [ ] **Step 1: Write the installer repo README**

Create `README.md` in `cortex-installer`:

````markdown
# @mocaos/cortex

Interactive installer for self-hosted [Cortex](https://github.com/mocaOS/cortex-app) — an
agentic knowledge base with a chat front end.

```bash
npx @mocaos/cortex
```

## What you need

Docker with the Compose v2 plugin. That is the only thing you install yourself;
Node comes with `npx`. On Linux note that `apt install docker.io` does **not**
include Compose v2 — use Docker's official packages.

~20 GB disk, ~8 GB RAM, `linux/amd64` or `linux/arm64`, and an
OpenAI-compatible API key.

## What it does

Reads the latest tested release manifest, checks your environment, asks what it
needs, **verifies your LLM credentials with live calls before writing
anything**, then pulls pinned images and starts the stack.

Two modes: localhost (ports bound to `127.0.0.1`) or a public domain with
automatic HTTPS via Caddy.

## Commands

| Command | Does |
|---|---|
| `npx @mocaos/cortex` | Interactive install |
| `cortex update` | Move to the latest tested release |
| `cortex status` | Service state, health, URLs |
| `cortex logs [service]` | Follow logs |
| `cortex start` / `stop` / `restart` | Lifecycle |
| `cortex backup` | Run a verified backup now |
| `cortex restore [stamp]` | Guided restore |
| `cortex doctor` | One pasteable diagnostic block |
| `cortex uninstall` | Remove containers; volumes need a typed confirmation |

## Non-interactive

For scripted installs:

```bash
CORTEX_ADMIN_EMAIL=you@example.com \
CORTEX_OPENAI_API_KEY=sk-... \
CORTEX_OPENAI_MODEL=gpt-5.2 \
CORTEX_EMBEDDING_MODEL=text-embedding-3-small \
CORTEX_EMBEDDING_DIMENSION=1536 \
npx @mocaos/cortex --yes
```

Add `CORTEX_MODE=domain` with `CORTEX_APP_DOMAIN`, `CORTEX_CHAT_DOMAIN` and
`CORTEX_ACME_EMAIL` for a public deployment. Secrets are generated unless you
supply `CORTEX_ADMIN_PASSWORD`, `CORTEX_NEO4J_PASSWORD`,
`CORTEX_ADMIN_API_KEY`, `CORTEX_SESSION_SECRET` or
`CORTEX_CHAT_ENCRYPTION_KEY`.

## What it writes

```
./cortex/
  .env             the only file the installer authors — mode 600, holds your secrets
  cortex.json      install state, no secrets
  docker-compose*.yml, Caddyfile*, ops/, .env.example   release artifacts, verbatim
```

`.env` is yours to edit. Compose changes belong in `docker-compose.override.yml`,
which Compose merges automatically and updates never touch.

## Privacy

Error reporting is **off** unless you opt in and point it at your own
GlitchTip/Sentry. The installer sends nothing anywhere; your API key is used
only to call the provider you chose.
````

- [ ] **Step 2: Write the handbook chapter (cortex-app)**

Create `handbook/26-self-hosting.md` covering: what you need, `npx @mocaos/cortex`, the two modes and their DNS requirements, logging in with the shared admin identity, the day-2 commands, updating, backups and the full restore procedure, and troubleshooting. Cross-reference `selfhost/README.md` for the manual path and keep the prose consistent with it — the wording for the restore steps and the `EMBEDDING_DIMENSION` warning should match, since divergence between the two is how one goes stale.

Add it to the chapter list in `handbook/README.md`.

- [ ] **Step 3: Add the install section to cortex-app's README**

In cortex-app's `README.md`, add near the top:

```markdown
## Self-hosting

```bash
npx @mocaos/cortex
```

Interactive installer — checks your environment, validates your LLM
credentials before writing anything, then pulls pinned images and starts the
stack. Docker with Compose v2 is the only prerequisite.
See [handbook/26-self-hosting.md](handbook/26-self-hosting.md), or
[selfhost/README.md](selfhost/README.md) for the manual path.
```

- [ ] **Step 4: Update the routing docs (cortex-app)**

Append to `.claude/development.md`'s self-host section:

```markdown
### The installer

`npx @mocaos/cortex` (repo: `mocaOS/cortex-installer`) automates the manual
`selfhost/README.md` path. It reads `stack.json` from the latest release,
fetches that tag's `selfhost/` + `ops/` from the release tarball, and writes
only `.env`.

Consequences for anyone changing `selfhost/`:

- Adding a `${VAR:?}` to a compose file is a **breaking change for the
  installer** — it must also be added to the installer's `env.ts`, or every
  new install fails at `docker compose config`. The installer's test suite
  renders its `.env` against the real released compose to catch exactly this.
- Renaming or removing a compose file breaks `fetchArtifacts`'s
  `ARTIFACT_FILES` check.
- The installer never edits compose files, so their `${VAR}` interpolation is
  the whole configuration contract.
```

Add to `CLAUDE.md`'s routing table:

```markdown
| Installer changes (external repo `mocaOS/cortex-installer`) | `development.md` (self-host section), `environment.md` |
```

- [ ] **Step 5: Verify the docs match reality**

```bash
# Every command the installer README lists must exist as a verb.
node -e '
const { readFileSync } = require("node:fs");
const verbs = [...readFileSync("src/cli.ts","utf8").matchAll(/"([a-z]+)",/g)].map(m=>m[1]);
const doc = readFileSync("README.md","utf8");
const missing = ["install","update","status","logs","start","stop","restart","backup","restore","doctor","uninstall"]
  .filter(v => !doc.includes(v));
console.log(missing.length ? "MISSING FROM README: "+missing : "all verbs documented");
'
```

Expected: `all verbs documented`.

- [ ] **Step 6: Commit**

```bash
# in cortex-installer
git add README.md
git commit -m "docs: installer README"

# in cortex-app
git add handbook/26-self-hosting.md handbook/README.md README.md .claude/development.md CLAUDE.md
git commit -m "docs: self-hosting via npx @mocaos/cortex

Documents the installer and, for anyone editing selfhost/, spells out that
adding a \${VAR:?} to a compose file is a breaking change the installer's
env.ts must match."
```

---

## Phase exit criteria

Verify on a machine that has never run Cortex:

- [ ] `npx @mocaos/cortex` (from the published package) completes interactively in localhost mode and the stack comes up healthy.
- [ ] Admin login works at `http://localhost:3000` with the printed credentials.
- [ ] The **same** credentials log in at `http://localhost:3001`.
- [ ] A document uploads and finishes processing — entities appear in the graph.
- [ ] A question in Ask returns an answer with citations.
- [ ] `cortex status`, `doctor`, `logs`, `restart`, `backup` all behave.
- [ ] `cortex update` is a clean no-op when already current.
- [ ] `cortex uninstall` removes containers and requires the typed phrase before volumes.
- [ ] A wrong API key fails at the probe in under 30 seconds, **before** any image is pulled and before `.env` exists.
- [ ] On a real VPS in domain mode: both certificates issue, both domains serve, and only Caddy publishes ports.
- [ ] `.env` is mode `600`; `cortex.json` contains no secrets.
- [ ] Non-interactive `--yes` install succeeds in CI.

## Manual prerequisites

1. Create the **`mocaOS/cortex-installer`** repo.
2. Create an npm **automation token** for the `mocaos` org → `NPM_TOKEN` secret in that repo.
3. Confirm the npm org allows publishing scoped public packages (`--access public` is already passed).

## Non-goals

- Deploying to a remote Dokploy server — meta-cortex owns that.
- A `--build` fallback. Published images have client-side error reporting disabled at build time; a source build would re-enable it. If ever added, it MUST pass `NEXT_PUBLIC_SENTRY_DISABLED=1`.
- Auto-updates, Watchtower, scheduled pulls.
- An in-app update banner.
- Kubernetes or Helm.
- Wrapping the file-volume restore step — the sidecar mounts those volumes read-only, so the CLI prints the exact host commands instead.
