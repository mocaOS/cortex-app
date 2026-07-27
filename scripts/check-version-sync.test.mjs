import { test } from "node:test";
import assert from "node:assert/strict";
import { checkVersionSync, extractEnvValue, imageTag } from "./check-version-sync.mjs";

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

// --- selfhost/.env.example image-pin drift -------------------------------
// At v1.1.0, stack.json says 1.1.0 but a stale .env.example would still say
// 1.0.0 for CORTEX_BACKEND_IMAGE/CORTEX_FRONTEND_IMAGE — silently installing
// the wrong stack. envPins/templatePins are optional so callers that only
// care about root/frontend/tag drift (existing tests above) are unaffected.

const goodEnvPins = {
  backend: "1.0.0",
  frontend: "1.0.0",
  chat: "1.0.0",
  neo4j: "5.26-community",
  caddy: "2-alpine",
};
const goodTemplatePins = { chat: "1.0.0", neo4j: "5.26-community", caddy: "2-alpine" };

test("passes when .env.example pins match root version and stack.template.json", () => {
  const r = checkVersionSync({
    rootVersion: "1.0.0",
    frontendVersion: "1.0.0",
    tag: null,
    envPins: goodEnvPins,
    templatePins: goodTemplatePins,
  });
  assert.equal(r.ok, true);
  assert.deepEqual(r.problems, []);
});

test("fails when .env.example CORTEX_BACKEND_IMAGE drifts from root version", () => {
  const r = checkVersionSync({
    rootVersion: "1.1.0",
    frontendVersion: "1.1.0",
    tag: null,
    envPins: { ...goodEnvPins, frontend: "1.1.0" },
    templatePins: goodTemplatePins,
  });
  assert.equal(r.ok, false);
  assert.match(r.problems[0], /CORTEX_BACKEND_IMAGE/);
});

test("fails when .env.example CORTEX_FRONTEND_IMAGE drifts from root version", () => {
  const r = checkVersionSync({
    rootVersion: "1.1.0",
    frontendVersion: "1.1.0",
    tag: null,
    envPins: { ...goodEnvPins, backend: "1.1.0" },
    templatePins: goodTemplatePins,
  });
  assert.equal(r.ok, false);
  assert.match(r.problems[0], /CORTEX_FRONTEND_IMAGE/);
});

test("fails when .env.example CORTEX_CHAT_IMAGE drifts from stack.template.json", () => {
  const r = checkVersionSync({
    rootVersion: "1.0.0",
    frontendVersion: "1.0.0",
    tag: null,
    envPins: { ...goodEnvPins, chat: "0.9.0" },
    templatePins: goodTemplatePins,
  });
  assert.equal(r.ok, false);
  assert.match(r.problems[0], /CORTEX_CHAT_IMAGE/);
});

test("fails when .env.example NEO4J_VERSION drifts from stack.template.json", () => {
  const r = checkVersionSync({
    rootVersion: "1.0.0",
    frontendVersion: "1.0.0",
    tag: null,
    envPins: { ...goodEnvPins, neo4j: "5.25-community" },
    templatePins: goodTemplatePins,
  });
  assert.equal(r.ok, false);
  assert.match(r.problems[0], /NEO4J_VERSION/);
});

test("fails when .env.example CADDY_VERSION drifts from stack.template.json", () => {
  const r = checkVersionSync({
    rootVersion: "1.0.0",
    frontendVersion: "1.0.0",
    tag: null,
    envPins: { ...goodEnvPins, caddy: "2.8-alpine" },
    templatePins: goodTemplatePins,
  });
  assert.equal(r.ok, false);
  assert.match(r.problems[0], /CADDY_VERSION/);
});

test("reports every pin drift at once", () => {
  const r = checkVersionSync({
    rootVersion: "1.1.0",
    frontendVersion: "1.1.0",
    tag: null,
    envPins: goodEnvPins, // backend/frontend still say 1.0.0; chat/neo4j/caddy unchanged from template
    templatePins: goodTemplatePins,
  });
  assert.equal(r.problems.length, 2);
});

test("skips .env.example checks entirely when envPins is not passed", () => {
  const r = checkVersionSync({ rootVersion: "1.0.0", frontendVersion: "1.0.0", tag: null });
  assert.equal(r.ok, true);
});

// --- parsing helpers ------------------------------------------------------

test("extractEnvValue reads a KEY=value line", () => {
  const content = "FOO=bar\nCORTEX_BACKEND_IMAGE=ghcr.io/mocaos/cortex-backend:1.0.0\n";
  assert.equal(
    extractEnvValue(content, "CORTEX_BACKEND_IMAGE"),
    "ghcr.io/mocaos/cortex-backend:1.0.0"
  );
});

test("extractEnvValue returns null when the key is absent", () => {
  assert.equal(extractEnvValue("FOO=bar\n", "MISSING"), null);
});

test("extractEnvValue ignores a commented-out line", () => {
  assert.equal(extractEnvValue("# ACME_EMAIL=you@example.com\n", "ACME_EMAIL"), null);
});

test("imageTag extracts the tag after the last colon", () => {
  assert.equal(imageTag("ghcr.io/mocaos/cortex-backend:1.0.0"), "1.0.0");
});

test("imageTag returns the whole value when there is no colon", () => {
  assert.equal(imageTag("5.26-community"), "5.26-community");
});

test("imageTag returns null for null input", () => {
  assert.equal(imageTag(null), null);
});
