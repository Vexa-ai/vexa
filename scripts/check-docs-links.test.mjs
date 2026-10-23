import { test } from "node:test";
import assert from "node:assert/strict";
import { judgeMintOutput } from "./check-docs-links.mjs";
import { guardTree } from "./test-tree.mjs";

guardTree();

test("a page that does not compile fails the gate even though mint exits 0", () => {
  const out = "Unable to parse minutes/meetings.mdx - 330:12: Expected a closing tag for `<ext>`\nchecking for broken links...\nsuccess no broken links found";
  const errs = judgeMintOutput(0, out);
  assert.ok(errs.some((e) => /do not compile/.test(e)));
  assert.ok(errs.some((e) => /minutes\/meetings\.mdx/.test(e)));
});

test("a run that ends without the success line fails the gate", () => {
  assert.ok(judgeMintOutput(0, "checking for broken links...\n").length > 0);
});

test("a broken link (non-zero exit) fails the gate", () => {
  assert.ok(judgeMintOutput(1, "found 1 broken link in 1 file\nminutes/x.mdx\n ⎿ /nowhere").length > 0);
});

test("a clean run passes", () => {
  assert.deepEqual(judgeMintOutput(0, "checking for broken links...\nsuccess no broken links found\n"), []);
});
