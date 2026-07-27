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
