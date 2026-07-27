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
