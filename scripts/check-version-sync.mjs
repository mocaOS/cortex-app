#!/usr/bin/env node
// The root package.json version is the single source of truth for a cortex-app
// release. frontend/package.json must mirror it, a release tag must match it,
// and selfhost/.env.example's pinned image tags must not drift from it either:
// CORTEX_BACKEND_IMAGE/CORTEX_FRONTEND_IMAGE track the root version directly;
// CORTEX_CHAT_IMAGE/NEO4J_VERSION/CADDY_VERSION must match the pins in
// selfhost/stack.template.json. Without this, an operator cloning a new tag
// and running `cp .env.example .env` can silently install a stale stack.
// Run bare in CI (root vs frontend vs .env.example); pass --tag in the
// release workflow.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

// Reads a `KEY=value` line out of .env-style file content. Anchored at line
// start, so a commented-out `# KEY=value` line (or a longer key that merely
// ends with KEY) does not match. Returns null when the key is absent.
export function extractEnvValue(envContent, key) {
  const match = envContent.match(new RegExp(`^${key}=(.*)$`, "m"));
  return match ? match[1].trim() : null;
}

// Pulls the tag after the last ':' off an image reference (repo/name:tag).
// Values with no colon (e.g. a bare NEO4J_VERSION like "5.26-community") are
// returned unchanged. null in, null out.
export function imageTag(ref) {
  if (ref == null) return null;
  const idx = ref.lastIndexOf(":");
  return idx === -1 ? ref : ref.slice(idx + 1);
}

// Pulls `CORTEX_VERSION = "x.y.z"` out of the backend's main.py — the version
// GET /health reports, and the backend's only declaration of which release it
// is. Throws rather than returning null if the constant is gone: this guard is
// the last gate before an irreversible publish, so a renamed constant must fail
// loudly instead of quietly disabling the check it exists to perform.
export function extractPyVersion(source) {
  const match = source.match(/^CORTEX_VERSION\s*=\s*["']([^"']+)["']/m);
  if (!match) {
    throw new Error(
      "backend/app/main.py has no top-level CORTEX_VERSION assignment — " +
        "the /health version can no longer be checked for drift"
    );
  }
  return match[1];
}

export function checkVersionSync({
  rootVersion,
  frontendVersion,
  backendVersion,
  tag,
  envPins,
  templatePins,
}) {
  const problems = [];

  if (rootVersion !== frontendVersion) {
    problems.push(
      `frontend/package.json is ${frontendVersion} but root package.json is ${rootVersion}`
    );
  }

  // backendVersion is optional for the same reason envPins is: callers that
  // only compare root/frontend/tag stay unaffected.
  if (backendVersion != null && backendVersion !== rootVersion) {
    problems.push(
      `backend/app/main.py CORTEX_VERSION is ${backendVersion} but root package.json is ${rootVersion}`
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

  // envPins is optional so callers that only care about root/frontend/tag
  // drift (e.g. a future non-selfhost consumer of this script) are unaffected.
  if (envPins) {
    if (envPins.backend !== rootVersion) {
      problems.push(
        `selfhost/.env.example CORTEX_BACKEND_IMAGE is pinned to ${envPins.backend} but root package.json is ${rootVersion}`
      );
    }
    if (envPins.frontend !== rootVersion) {
      problems.push(
        `selfhost/.env.example CORTEX_FRONTEND_IMAGE is pinned to ${envPins.frontend} but root package.json is ${rootVersion}`
      );
    }

    if (templatePins) {
      if (envPins.chat !== templatePins.chat) {
        problems.push(
          `selfhost/.env.example CORTEX_CHAT_IMAGE is pinned to ${envPins.chat} but selfhost/stack.template.json pins chat to ${templatePins.chat}`
        );
      }
      if (envPins.neo4j !== templatePins.neo4j) {
        problems.push(
          `selfhost/.env.example NEO4J_VERSION is ${envPins.neo4j} but selfhost/stack.template.json pins neo4j to ${templatePins.neo4j}`
        );
      }
      if (envPins.caddy !== templatePins.caddy) {
        problems.push(
          `selfhost/.env.example CADDY_VERSION is ${envPins.caddy} but selfhost/stack.template.json pins caddy to ${templatePins.caddy}`
        );
      }
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

  const envContent = readFileSync(join(repoRoot, "selfhost/.env.example"), "utf8");
  const template = JSON.parse(
    readFileSync(join(repoRoot, "selfhost/stack.template.json"), "utf8")
  );

  const envPins = {
    backend: imageTag(extractEnvValue(envContent, "CORTEX_BACKEND_IMAGE")),
    frontend: imageTag(extractEnvValue(envContent, "CORTEX_FRONTEND_IMAGE")),
    chat: imageTag(extractEnvValue(envContent, "CORTEX_CHAT_IMAGE")),
    neo4j: extractEnvValue(envContent, "NEO4J_VERSION"),
    caddy: extractEnvValue(envContent, "CADDY_VERSION"),
  };

  // The backend has no package.json, so its release version is a module
  // constant. A missing or renamed constant must fail rather than silently skip
  // the check, so extractPyVersion throws.
  const backendVersion = extractPyVersion(
    readFileSync(join(repoRoot, "backend/app/main.py"), "utf8")
  );

  const result = checkVersionSync({
    rootVersion: read("package.json"),
    frontendVersion: read("frontend/package.json"),
    backendVersion,
    tag,
    envPins,
    templatePins: template.components,
  });

  if (!result.ok) {
    console.error("Version mismatch:");
    for (const p of result.problems) console.error(`  - ${p}`);
    process.exit(1);
  }
  console.log("Versions in sync.");
}
