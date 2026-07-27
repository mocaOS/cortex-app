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
