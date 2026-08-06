import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const read = (path) => readFileSync(join(root, path), "utf8");

test("the corporate path publishes blank terms and keeps executed records private", () => {
  const index = read("CLA/README.md");
  const policy = read("CONTRIBUTOR_RIGHTS.md");
  const template = read(".github/pull_request_template.md");

  assert.match(index, /canonical public distribution point/i);
  assert.match(index, /Return the executed agreement privately/i);
  assert.match(policy, /publishes the\s+current counsel-approved blank corporate agreement/i);
  assert.match(template, /public blank-agreement index and private return/i);
  assert.doesNotMatch(policy, /Vexa will privately send/i);
});

test("DCO check events cannot be cancelled by unrelated check completions", () => {
  const workflow = read(".github/workflows/contribution-rights.yml");

  assert.match(workflow, /github\.event\.check_run\.id/);
  assert.match(workflow, /github\.run_id/);
});

test("an unavailable agreement is stated explicitly instead of exposing an unapproved draft", () => {
  const index = read("CLA/README.md");
  const candidate = read("CLA/Corporate_CLA.draft.md");

  assert.match(index, /No corporate agreement is currently published for signature/i);
  assert.match(index, /historical Vexa CCLA v1\.0\s+was not approved for use unchanged/i);
  assert.match(index, /Do not sign or rely on a copy obtained from repository history/i);
  assert.match(index, /Harmony Entity CLA v1\.0 candidate/i);
  assert.match(candidate, /Not currently offered for signature/i);
  assert.match(candidate, /Harmony Entity Contributor License Agreement, Version 1\.0/i);
  assert.match(candidate, /Harmony Option One/i);
  assert.match(candidate, /currently Apache-2\.0/i);
  assert.doesNotMatch(candidate, /Harmony Option (?:Two|Three|Four|Five)/i);
});
