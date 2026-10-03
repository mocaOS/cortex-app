#!/usr/bin/env node
// Documentation validation. Derives expectations from canonical sources in this
// repo (backend/app/config.py, backend/app/main.py, frontend/src, compose
// files, documentation/zudoku.config.tsx) so documentation drift is caught
// without a site build. Static allowlists below cover env vars owned by
// components whose source lives outside this repo (cortex-chat, the installer/
// selfhost stack, Neo4j, Cortex Slides/Videogen) — names verified against those
// components' sources; update them when the owning component ships new
// configuration.
//
// LIMITS: all checks are static name/shape checks. They validate that names,
// paths, methods, pages and mirror entries exist and line up — not semantics,
// not default values, not runtime behavior. The `--live` mirror check verifies
// URL inclusion and coarse structure (size, document count, code fences) of
// the published llms mirror, NOT semantic equivalence with this checkout.
// Self-tests: scripts/validate-docs.test.mjs.
//
// `--live` adds network checks against the published docs site, the Cortex
// Skills manifest, and GHCR (read-only GETs only, bounded by --timeout).
// Unavailable live targets fail the gate (fail closed) rather than pass
// silently; run offline (`npm run validate`) when network is not expected.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const usage = `usage: node validate-docs.mjs [--live] [--root <cortex-app checkout>] [--timeout <ms>]`;
let root = path.resolve(here, "..", "..");
let live = false;
let timeoutMs = 15000;
const args = process.argv.slice(2);
for (let i = 0; i < args.length; i++) {
  const a = args[i];
  if (a === "--live") live = true;
  else if (a === "--root") {
    if (!args[i + 1] || args[i + 1].startsWith("--")) {
      console.error(`error: --root requires a path argument\n${usage}`);
      process.exit(2);
    }
    root = path.resolve(args[++i]);
  } else if (a === "--timeout") {
    const v = Number(args[++i]);
    if (!args[i] || !Number.isFinite(v) || v <= 0) {
      console.error(`error: --timeout requires a positive number of milliseconds\n${usage}`);
      process.exit(2);
    }
    timeoutMs = v;
  } else {
    console.error(`error: unknown argument: ${a}\n${usage}`);
    process.exit(2);
  }
}
const requiredAtRoot = ["documentation/zudoku.config.tsx", "backend/app/config.py", "backend/app/main.py"];
for (const f of requiredAtRoot) {
  if (!fs.existsSync(path.join(root, f))) {
    console.error(`error: --root ${root} is not a cortex-app checkout (missing ${f})`);
    process.exit(2);
  }
}

const failures = [];
const read = (p) => fs.readFileSync(path.join(root, p), "utf8");
const exists = (p) => fs.existsSync(path.join(root, p));
const listPages = () => {
  const out = [];
  const walk = (d) => {
    for (const e of fs.readdirSync(d, { withFileTypes: true })) {
      const p = path.join(d, e.name);
      if (e.isDirectory()) walk(p);
      else if (/\.(md|mdx)$/.test(e.name)) out.push(p);
    }
  };
  walk(path.join(root, "documentation/pages"));
  return out;
};

// ---- canonical env-name sources -------------------------------------------

const envNames = new Set();
const configPy = read("backend/app/config.py");
for (const m of configPy.matchAll(/([a-z][a-z0-9_]*)\s*:\s*[A-Za-z]+(?:\[[^\]]*\])?\s*=\s*Field\(/g)) {
  envNames.add(m[1].toUpperCase());
}
for (const m of configPy.matchAll(/"([A-Z][A-Z0-9_]{2,})"/g)) {
  envNames.add(m[1]);
}
const walkPy = (d, cb) => {
  for (const e of fs.readdirSync(d, { withFileTypes: true })) {
    const p = path.join(d, e.name);
    if (e.isDirectory()) walkPy(p, cb);
    else if (p.endsWith(".py")) cb(fs.readFileSync(p, "utf8"));
  }
};
walkPy(path.join(root, "backend/app"), (t) => {
  for (const m of t.matchAll(/(?:os\.environ(?:\.get)?\(|os\.getenv\()"([A-Z][A-Z0-9_]+)"/g)) {
    envNames.add(m[1]);
  }
});
for (const f of ["docker-compose.yml", "docker-compose.prod.yml", "docker-compose.backup.yml", "selfhost/docker-compose.yml", "selfhost/docker-compose.caddy.yml", "selfhost/docker-compose.ports.yml"]) {
  if (!exists(f)) continue;
  const t = read(f);
  for (const m of t.matchAll(/\$\{([A-Z][A-Z0-9_]{2,})(?::[-?]|\})/g)) {
    envNames.add(m[1]);
  }
}
const walkTs = (d, cb) => {
  for (const e of fs.readdirSync(d, { withFileTypes: true })) {
    const p = path.join(d, e.name);
    if (e.isDirectory()) walkTs(p, cb);
    else if (/\.(ts|tsx)$/.test(e.name)) cb(fs.readFileSync(p, "utf8"));
  }
};
walkTs(path.join(root, "frontend/src"), (t) => {
  for (const m of t.matchAll(/process\.env\.([A-Z][A-Z0-9_]+)/g)) envNames.add(m[1]);
});

const externalEnvAllowlist = new Set([
  "COMPOSE_PROFILES", "CORTEX_BACKEND_IMAGE", "CORTEX_FRONTEND_IMAGE", "CORTEX_CHAT_IMAGE", "NEO4J_VERSION",
  "CORTEX_ADMIN_EMAIL", "CORTEX_OPENAI_API_KEY", "CORTEX_OPENAI_API_BASE", "CORTEX_OPENAI_MODEL",
  "CORTEX_EMBEDDING_MODEL", "CORTEX_EMBEDDING_DIMENSION", "CORTEX_EMBEDDING_API_BASE", "CORTEX_EMBEDDING_API_KEY",
  "CORTEX_ADMIN_PASSWORD", "CORTEX_NEO4J_PASSWORD", "CORTEX_ADMIN_API_KEY", "CORTEX_SESSION_SECRET",
  "CORTEX_CHAT_ENCRYPTION_KEY", "CORTEX_MODE", "CORTEX_APP_DOMAIN", "CORTEX_CHAT_DOMAIN", "CORTEX_ACME_EMAIL",
  "NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD",
  "CORTEX_API_URL", "BACKEND_ADMIN_API_KEY", "SUPERADMIN_EMAIL", "SUPERADMIN_PASSWORD", "APP_ENCRYPTION_KEY",
  "DATABASE_PATH", "PORT", "ENABLE_REGISTRATION", "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASS",
  "SMTP_SECURE", "SMTP_FROM", "APP_BASE_URL", "OIDC_ISSUER_URL", "OIDC_CLIENT_ID", "OIDC_CLIENT_SECRET",
  "OIDC_SCOPES", "OIDC_BUTTON_LABEL", "OIDC_DEFAULT_GROUP", "OIDC_ONLY", "VOICE_STT_BASE_URL",
  "VOICE_STT_API_KEY", "VOICE_STT_MODEL", "VOICE_TTS_BASE_URL", "VOICE_TTS_API_KEY", "VOICE_TTS_MODEL",
  "VOICE_TTS_VOICE", "SENTRY_ENVIRONMENT", "SENTRY_DSN", "SENTRY_DISABLED", "SENTRY_AUTH_TOKEN",
  "NEXT_PUBLIC_SENTRY_DISABLED", "NEXT_PUBLIC_SENTRY_DSN", "NEXT_PUBLIC_SENTRY_ENVIRONMENT",
  "LIBRARY_API_URL", "NEXT_PUBLIC_API_URL", "NEXT_PUBLIC_ACCENT_COLOR", "DEMO_MODE", "DEMO_EMAIL",
  "DEMO_PASSWORD", "DEMO_GROUP",
  "VENICE_API_KEY", "CORTEX_BASE_URL", "CORTEX_API_KEY",
]);

// ---- canonical routes (method + path) --------------------------------------

const routes = new Set();
const mainPy = read("backend/app/main.py");
for (const m of mainPy.matchAll(/@app\.(get|post|put|delete|patch)\("([^"]+)"/g)) {
  routes.add(`${m[1].toUpperCase()} ${m[2].replace(/\{[^}]+\}/g, "{x}")}`);
}

// ---- navigation / page links ----------------------------------------------

const zudoku = read("documentation/zudoku.config.tsx");
const navPaths = new Set();
for (const m of zudoku.matchAll(/"(\/[a-z0-9/-]+)"/g)) {
  const p = m[1];
  if (p === "/api") continue;
  navPaths.add(p);
}
for (const p of navPaths) {
  if (!exists(`documentation/pages${p}.md`) && !exists(`documentation/pages${p}.mdx`)) {
    fail(`nav references missing page: ${p}`);
  }
}
function fail(msg) {
  failures.push(msg);
}
const pageUrls = [...navPaths];

for (const page of listPages()) {
  const rel = path.relative(root, page);
  const lines = read(path.relative(root, page)).split("\n");
  for (let i = 0; i < lines.length; i++) {
    for (const m of lines[i].matchAll(/\]\((\/[a-zA-Z0-9/_-]+)(#[^)]*)?\)/g)) {
      const target = m[1];
      if (target === "/api") continue;
      if (!navPaths.has(target)) {
        fail(`${rel}:${i + 1} internal link to unknown page: ${target}`);
      }
    }
  }
}

// ---- env-var tables --------------------------------------------------------

for (const page of listPages()) {
  const rel = path.relative(root, page);
  const lines = read(path.relative(root, page)).split("\n");
  let inEnvTable = false;
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (/^\|\s*Variable\s*\|/.test(line)) {
      inEnvTable = true;
      continue;
    }
    if (inEnvTable && line.startsWith("|")) {
      const m = line.match(/^\|\s*`([A-Z][A-Z0-9_]+)`/);
      if (m && !envNames.has(m[1]) && !externalEnvAllowlist.has(m[1])) {
        fail(`${rel}:${i + 1} env table documents unknown variable: ${m[1]}`);
      }
      continue;
    }
    if (inEnvTable && !line.startsWith("|") && line.trim() !== "") inEnvTable = false;
  }
}

// ---- skills configuration coverage (config.py <-> features/skills page) ----

const skillsPagePath = path.join(root, "documentation/pages/features/skills.mdx");
if (exists(path.relative(root, skillsPagePath))) {
  const skillsPage = fs.readFileSync(skillsPagePath, "utf8");
  for (const m of configPy.matchAll(/((?:max_)?skill[a-z_]*|skills_[a-z_]+)\s*:\s*[A-Za-z]+(?:\[[^\]]*\])?\s*=\s*Field\(/g)) {
    const envName = m[1].toUpperCase();
    if (!skillsPage.includes(envName)) {
      fail(`features/skills.mdx does not document skill config variable: ${envName}`);
    }
  }
}

// ---- endpoint tables (method + path) ---------------------------------------

for (const page of listPages()) {
  const rel = path.relative(root, page);
  const lines = read(path.relative(root, page)).split("\n");
  for (let i = 0; i < lines.length; i++) {
    const m = lines[i].match(/^\|\s*`(GET|POST|PUT|DELETE|PATCH)`\s*\|\s*`([^`]+)`/);
    if (!m) continue;
    const route = `${m[1]} ${m[2].split("?")[0].replace(/\{[^}]+\}/g, "{x}")}`;
    if (!routes.has(route)) {
      fail(`${rel}:${i + 1} documents unknown endpoint (method+path): ${m[1]} ${m[2]}`);
    }
  }
}

// ---- chat service wiring coverage (selfhost compose <-> features/cortex-chat page)

const selfhostComposePath = "selfhost/docker-compose.yml";
if (exists(selfhostComposePath) && exists("documentation/pages/features/cortex-chat.mdx")) {
  const composeText = read(selfhostComposePath);
  const chatIdx = composeText.indexOf("\n  chat:\n");
  if (chatIdx !== -1) {
    const rest = composeText.slice(chatIdx + 1);
    const end = rest.slice(6).search(/\n  [a-z]/);
    const chatBlock = end === -1 ? rest : rest.slice(0, end + 6);
    const chatPage = fs.readFileSync(
      path.join(root, "documentation/pages/features/cortex-chat.mdx"),
      "utf8"
    );
    for (const m of chatBlock.matchAll(/^\s+- ([A-Z][A-Z0-9_]+)=/gm)) {
      if (!chatPage.includes(m[1])) {
        fail(
          `features/cortex-chat.mdx does not document chat-side variable wired by selfhost compose: ${m[1]}`
        );
      }
    }
  }
}

// ---- live checks (structure/inclusion, NOT semantic equivalence) -----------

// Target overrides exist for hermetic self-tests (local loopback servers);
// unset, the defaults hit the real published endpoints.
const DOCS_URL = process.env.DOCS_URL || "https://docs.cortex.eco";
const SKILLS_INDEX_URL = process.env.SKILLS_INDEX_URL || "https://cortexskills.org/index.json";
const GHCR_AUTH_URL =
  process.env.GHCR_AUTH_URL || "https://ghcr.io/token?scope=repository:mocaos/cortex-chat:pull";
const GHCR_TAGS_URL = process.env.GHCR_TAGS_URL || "https://ghcr.io/v2/mocaos/cortex-chat/tags/list";
async function fetchText(url) {
  const res = await fetch(url, { redirect: "follow", signal: AbortSignal.timeout(timeoutMs) });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.text();
}
let liveRan = 0;
let liveFailed = 0;

if (live) {
  let llmsTxt = null;
  let llmsFull = null;
  try {
    llmsTxt = await fetchText(`${DOCS_URL}/llms.txt`);
  } catch (e) {
    fail(`llms.txt unreachable: ${e.message}`);
  }
  try {
    llmsFull = await fetchText(`${DOCS_URL}/llms-full.txt`);
  } catch (e) {
    fail(`llms-full.txt unreachable: ${e.message}`);
  }
  if (llmsTxt !== null && llmsTxt.trim().length < 200) {
    fail(`llms.txt exists but is effectively empty (${llmsTxt.trim().length} bytes)`);
  }
  if (llmsTxt !== null) liveRan++;
  if (llmsFull !== null) {
    liveRan++;
    if (llmsFull.trim().length < 50000) {
      fail(`llms-full.txt effectively empty or suspiciously small (${llmsFull.trim().length} bytes)`);
    } else {
      const docCount = (llmsFull.match(/^## Document:/gm) || []).length;
      if (docCount < pageUrls.length) {
        fail(`llms-full.txt has ${docCount} documents, expected >= ${pageUrls.length} pages`);
      }
      for (const url of pageUrls) {
        if (!llmsFull.includes(`URL: ${url}`)) fail(`llms-full.txt missing document for page: ${url}`);
      }
      const fences = (llmsFull.match(/```/g) || []).length;
      if (fences < 40) fail(`llms-full.txt carries only ${fences / 2} code blocks — mirror may be stripped of examples`);
    }
  }
  const skillsIndex = await fetchText(SKILLS_INDEX_URL)
    .then((t) => JSON.parse(t))
    .then((parsed) => {
      liveRan++;
      return parsed;
    })
    .catch((e) => {
      fail(`cortexskills.org/index.json unreachable or not valid JSON: ${e.message}`);
      return null;
    });
  if (skillsIndex) {
    if (!Array.isArray(skillsIndex.skills)) {
      fail("cortexskills.org/index.json is not a valid manifest (no skills array)");
    } else {
      const slugs = new Set(skillsIndex.skills.map((s) => s.slug));
      for (const page of listPages()) {
        const rel = path.relative(root, page);
        const text = fs.readFileSync(page, "utf8");
        for (const m of text.matchAll(/cortexskills\.org\/([a-z0-9/-]+)\/SKILL\.md/g)) {
          if (m[1] !== "SKILL" && !slugs.has(m[1]) && !slugs.has(m[1].split("/")[0])) {
            fail(`${rel} references unknown skill topic: ${m[1]}`);
          }
        }
      }
    }
  }
  const ghcrToken = await fetchText(GHCR_AUTH_URL)
    .then((t) => JSON.parse(t).token)
    .catch((e) => {
      fail(`GHCR token endpoint unreachable — chat image tag check could not run: ${e.message}`);
      return null;
    });
  if (ghcrToken) {
    const tags = await fetch(GHCR_TAGS_URL, {
      headers: { Authorization: `Bearer ${ghcrToken}` },
      signal: AbortSignal.timeout(timeoutMs),
    })
      .then((r) => {
        if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
        liveRan++;
        return r.json();
      })
      .then((d) => new Set(d.tags))
      .catch((e) => {
        fail(`GHCR tags list unreachable — chat image tag check could not run: ${e.message}`);
        return null;
      });
    if (tags) {
      for (const page of listPages()) {
        const rel = path.relative(root, page);
        const text = fs.readFileSync(page, "utf8");
        for (const m of text.matchAll(/ghcr\.io\/mocaos\/cortex-chat:([a-z0-9.-]+)/g)) {
          if (!tags.has(m[1])) fail(`${rel} references unpublished chat image tag: ${m[1]}`);
        }
      }
    }
  }
}

// ---- report ----------------------------------------------------------------

if (failures.length) {
  console.error(`\n${failures.length} documentation validation failure(s):`);
  for (const f of failures) console.error(`  - ${f}`);
  process.exit(1);
}
console.log(
  `documentation validation OK (${listPages().length} pages, ${routes.size} method+path routes, ${envNames.size}+${externalEnvAllowlist.size} env names${
    live
      ? `; live checks: ${liveRan} ran, structure/inclusion only, not semantic equivalence`
      : ", offline only"
  })`
);
