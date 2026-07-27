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

// One case per component, not a single shared fixture: buildStackJson's
// validation loop throws on the FIRST missing key it finds, so a fixture
// that omits more than one component only ever proves the earliest-checked
// one. Each case here omits exactly one required component while keeping
// the other two present, so each is independently proven.
for (const missing of ["chat", "neo4j", "caddy"]) {
  test(`throws naming ${missing} when it is the only component missing`, () => {
    const components = { chat: "1.0.0", neo4j: "5.26-community", caddy: "2-alpine" };
    delete components[missing];
    const bad = { components, minInstaller: "1.0.0" };
    assert.throws(
      () => buildStackJson({ version: "1.0.0", template: bad }),
      new RegExp(missing)
    );
  });
}

test("throws when minInstaller is missing", () => {
  const bad = { components: { chat: "1.0.0", neo4j: "5.26-community", caddy: "2-alpine" } };
  assert.throws(
    () => buildStackJson({ version: "1.0.0", template: bad }),
    /minInstaller/
  );
});
