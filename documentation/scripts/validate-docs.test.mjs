// Self-tests for scripts/validate-docs.mjs (node:test, no dependencies).
// Each test builds an isolated shadow fixture in a temp directory containing
// only the files the validator reads (documentation tree minus node_modules/
// dist, backend oracle files, frontend/src, compose files) — no full checkout
// clone. The REAL repo files serve as the oracle; mutations are applied to the
// fixture copy only. Fully hermetic: every network target (docs mirror, skills
// manifest, GHCR token + tags) is served by a local loopback HTTP server via
// the validator's target-override env vars — npm test makes no internet calls.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";
import http from "node:http";
import { fileURLToPath } from "node:url";

// Skill slugs captured from the real https://cortexskills.org/index.json
// manifest on 2026-10-01 (fixture oracle snapshot, not derived from the pages
// under test — so the positive control is not circular).
const REAL_SKILL_SLUGS = [
  "admin", "apps", "ask", "auth", "builder", "builder/app", "builder/skill",
  "collections", "communities", "cortex", "cortex-design", "git-integration",
  "graph", "hermes", "integration", "mcp", "memory-hygiene", "openclaw",
  "search", "setup", "tasks", "trainings", "upload", "videogen", "web-import",
  "x402",
];

function fixturePageUrls(fixture) {
  const out = [];
  const walk = (d) => {
    for (const e of fs.readdirSync(d, { withFileTypes: true })) {
      const p = path.join(d, e.name);
      if (e.isDirectory()) walk(p);
      else if (/\.(md|mdx)$/.test(e.name)) {
        out.push("/" + path.relative(path.join(fixture, "documentation/pages"), p).replace(/\.(md|mdx)$/, ""));
      }
    }
  };
  walk(path.join(fixture, "documentation/pages"));
  return out;
}

// Healthy llms-full.txt built from the fixture's actual page files: one
// document entry per page, enough filler + code fences to clear the size and
// structure floors. Non-circular w.r.t. the nav config: the validator's nav
// check already asserts every nav path exists as a page file.
function healthyLlmsFull(fixture) {
  const urls = fixturePageUrls(fixture);
  const parts = ["# Documentation\n\n"];
  for (const url of urls) {
    parts.push(`## Document: ${url}\n\nStub summary.\n\nURL: ${url}\n\n\n`);
  }
  for (let i = 0; i < 30; i++) {
    parts.push("```js\n// filler example block " + i + "\nconst x = " + i + ";\n```\n\n");
  }
  const filler = "Detail paragraph with enough words to contribute real bulk to the mirror size. ";
  while (parts.join("").length < 60000) parts.push(filler);
  return parts.join("");
}

// Local loopback server standing in for ALL live targets the validator calls.
function startLiveServer({ llmsFull }) {
  return new Promise((resolve) => {
    const server = http.createServer((req, res) => {
      const respond = (body, type = "application/json") => {
        res.writeHead(200, { "content-type": type });
        res.end(body);
      };
      if (req.url === "/llms.txt") {
        respond("# Documentation\n\n> Complete documentation for Large Language Models\n\n" + "-".repeat(250), "text/plain");
      } else if (req.url === "/llms-full.txt") {
        respond(llmsFull, "text/plain");
      } else if (req.url === "/index.json") {
        respond(JSON.stringify({
          manifest_version: 1,
          root: { version: "1.0.0" },
          skills: REAL_SKILL_SLUGS.map((slug) => ({ slug, version: "1.0.0", entry: `/${slug}/SKILL.md` })),
        }));
      } else if (req.url === "/token") {
        respond(JSON.stringify({ token: "fixture-token" }));
      } else if (req.url === "/tags/list") {
        respond(JSON.stringify({ tags: ["latest", "1.3.0", "1.2.0", "1.1.0"] }));
      } else {
        res.writeHead(404);
        res.end();
      }
    });
    server.listen(0, "127.0.0.1", () => resolve({ server, port: server.address().port }));
  });
}

function liveEnv(port) {
  const base = `http://127.0.0.1:${port}`;
  return {
    DOCS_URL: base,
    SKILLS_INDEX_URL: `${base}/index.json`,
    GHCR_AUTH_URL: `${base}/token`,
    GHCR_TAGS_URL: `${base}/tags/list`,
  };
}

test("healthy fixture passes --live against fully local targets", async () => {
  const fixture = buildFixture("healthy-live");
  const { server, port } = await startLiveServer({ llmsFull: healthyLlmsFull(fixture) });
  try {
    const r = await runValidator(fixture, ["--live"], liveEnv(port));
    assert.equal(r.status, 0, `expected pass, got:\n${r.stdout}\n${r.stderr}`);
    assert.match(r.stdout, /live checks: 4 ran/);
  } finally {
    server.close();
    fs.rmSync(fixture, { recursive: true, force: true });
  }
});

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const validator = path.join(repoRoot, "documentation/scripts/validate-docs.mjs");

function buildFixture(tag) {
  const fixture = fs.mkdtempSync(path.join(os.tmpdir(), `docsval-${tag}-`));
  fs.cpSync(path.join(repoRoot, "documentation"), path.join(fixture, "documentation"), {
    recursive: true,
    filter: (src) => !src.includes(`${path.sep}node_modules`) && !src.includes(`${path.sep}dist`),
  });
  fs.mkdirSync(path.join(fixture, "backend/app/services"), { recursive: true });
  for (const f of ["config.py", "main.py", "logging_setup.py"]) {
    fs.copyFileSync(path.join(repoRoot, "backend/app", f), path.join(fixture, "backend/app", f));
  }
  const errTrack = path.join(repoRoot, "backend/app/services/error_tracking.py");
  if (fs.existsSync(errTrack)) {
    fs.copyFileSync(errTrack, path.join(fixture, "backend/app/services/error_tracking.py"));
  }
  fs.mkdirSync(path.join(fixture, "frontend"), { recursive: true });
  fs.cpSync(path.join(repoRoot, "frontend/src"), path.join(fixture, "frontend/src"), { recursive: true });
  fs.mkdirSync(path.join(fixture, "selfhost"), { recursive: true });
  fs.copyFileSync(
    path.join(repoRoot, "selfhost/docker-compose.yml"),
    path.join(fixture, "selfhost/docker-compose.yml")
  );
  for (const f of ["docker-compose.yml", "docker-compose.prod.yml", "docker-compose.backup.yml"]) {
    const src = path.join(repoRoot, f);
    if (fs.existsSync(src)) fs.copyFileSync(src, path.join(fixture, f));
  }
  return fixture;
}

function runValidator(fixture, extraArgs = [], env = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(
      process.execPath,
      [validator, "--root", fixture, "--timeout", "5000", ...extraArgs],
      { env: { ...process.env, ...env } }
    );
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (d) => (stdout += d));
    child.stderr.on("data", (d) => (stderr += d));
    child.on("error", reject);
    child.on("close", (status) => resolve({ status, stdout, stderr }));
  });
}

function runValidatorArgs(extraArgs = []) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [validator, ...extraArgs]);
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (d) => (stdout += d));
    child.stderr.on("data", (d) => (stderr += d));
    child.on("error", reject);
    child.on("close", (status) => resolve({ status, stdout, stderr }));
  });
}

function mutate(fixture, rel, from, to) {
  const p = path.join(fixture, rel);
  const before = fs.readFileSync(p, "utf8");
  assert.ok(before.includes(from), `mutation target not found in ${rel}`);
  fs.writeFileSync(p, before.replace(from, to));
}

test("healthy fixture passes offline", async () => {
  const fixture = buildFixture("healthy");
  const r = await runValidator(fixture);
  assert.equal(r.status, 0, `expected pass, got:\n${r.stdout}\n${r.stderr}`);
  assert.match(r.stdout, /documentation validation OK/);
  fs.rmSync(fixture, { recursive: true, force: true });
});

test("wrong HTTP method on a documented endpoint is rejected", async () => {
  const fixture = buildFixture("method");
  mutate(
    fixture,
    "documentation/pages/features/skills.mdx",
    "| `GET` | `/api/admin/skills` |",
    "| `DELETE` | `/api/admin/skills` |"
  );
  const r = await runValidator(fixture);
  assert.equal(r.status, 1);
  assert.match(r.stderr, /documents unknown endpoint \(method\+path\): DELETE \/api\/admin\/skills/);
  fs.rmSync(fixture, { recursive: true, force: true });
});

test("unknown env var in a config table is rejected", async () => {
  const fixture = buildFixture("env");
  mutate(
    fixture,
    "documentation/pages/features/cortex-chat.mdx",
    "| `ENABLE_REGISTRATION` |",
    "| `ENABLE_REGISTRATON` |"
  );
  const r = await runValidator(fixture);
  assert.equal(r.status, 1);
  assert.match(r.stderr, /env table documents unknown variable: ENABLE_REGISTRATON/);
  fs.rmSync(fixture, { recursive: true, force: true });
});

test("nav entry without a page file is rejected", async () => {
  const fixture = buildFixture("nav");
  mutate(fixture, "documentation/zudoku.config.tsx", '"/guides/deployment",', '"/guides/deployments",');
  const r = await runValidator(fixture);
  assert.equal(r.status, 1);
  assert.match(r.stderr, /nav references missing page: \/guides\/deployments/);
  fs.rmSync(fixture, { recursive: true, force: true });
});

test("removed skill config row is rejected (config.py <-> page coverage)", async () => {
  const fixture = buildFixture("skill");
  const p = path.join(fixture, "documentation/pages/features/skills.mdx");
  const lines = fs.readFileSync(p, "utf8").split("\n").filter((l) => !l.includes("SKILL_HTTP_ALLOW_PRIVATE"));
  fs.writeFileSync(p, lines.join("\n"));
  const r = await runValidator(fixture);
  assert.equal(r.status, 1);
  assert.match(r.stderr, /does not document skill config variable: SKILL_HTTP_ALLOW_PRIVATE/);
  fs.rmSync(fixture, { recursive: true, force: true });
});

test("empty llms-full.txt 200 response is rejected (no bypass)", async () => {
  const fixture = buildFixture("llms");
  const { server, port } = await startLiveServer({ llmsFull: "" });
  try {
    const r = await runValidator(fixture, ["--live"], liveEnv(port));
    assert.equal(r.status, 1, `expected live failure, got:\n${r.stdout}\n${r.stderr}`);
    assert.match(r.stderr, /llms-full\.txt effectively empty or suspiciously small/);
  } finally {
    server.close();
    fs.rmSync(fixture, { recursive: true, force: true });
  }
});

test("invalid --root fails clearly with exit 2", async () => {
  const r = await runValidator("/tmp/opencode/definitely-not-a-checkout");
  assert.equal(r.status, 2);
  assert.match(r.stderr, /is not a cortex-app checkout/);
});

test("unknown argument fails clearly with exit 2", async () => {
  const r = await runValidatorArgs(["--frobnicate"]);
  assert.equal(r.status, 2);
  assert.match(r.stderr, /unknown argument: --frobnicate/);
});

test("--root without a value fails clearly with exit 2", async () => {
  const r = await runValidatorArgs(["--root"]);
  assert.equal(r.status, 2);
  assert.match(r.stderr, /--root requires a path argument/);
});
