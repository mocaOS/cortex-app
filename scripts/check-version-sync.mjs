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
