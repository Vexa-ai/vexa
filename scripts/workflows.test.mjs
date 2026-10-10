// Workflow properties that are not a gate's: how runs are grouped, and which images a step runs.
// Run: node --test scripts/workflows.test.mjs
import test from "node:test";
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const WF = join(ROOT, ".github", "workflows");
const read = (f) => readFileSync(join(WF, f), "utf8");

// A `${{ … }}` expression, evaluated the way Actions does for the subset these workflows use.
function evaluate(template, github) {
  return template.replace(/\$\{\{\s*([\s\S]*?)\s*\}\}/g, (_, expr) => {
    // Actions reads a missing property as null: optional chaining does the same here.
    const js = expr.replace(/'([^']*)'/g, (m, s) => JSON.stringify(s)).replace(/==/g, "===").replace(/!===/g, "!==")
      .replace(/(github(?:\.[A-Za-z_]+|\[\d+\])*)/g, (chain) => chain.replace(/\.(?=[A-Za-z_])/g, "?.").replace(/\[(\d+)\]/g, "?.[$1]"));
    const v = new Function("github", `try { return (${js}); } catch { return ""; }`)(github);
    return v === undefined || v === null || v === false ? (typeof v === "boolean" ? "false" : "") : String(v);
  });
}

function concurrency(file) {
  const text = read(file);
  const group = text.match(/^concurrency:\s*\n\s+group:\s*(.+)$/m)[1].trim();
  const cancel = text.match(/^concurrency:\s*\n\s+group:.*\n\s+cancel-in-progress:\s*(.+)$/m)[1].trim();
  return { group, cancel };
}

// L1: a comment on a pull request and the pull request's own run must share one queue, or a run that
// read state before a verifier's decision can finish last and leave a stale check on the head.
test("contribution-rights: every rights run for one pull request queues together and none is cancelled", () => {
  const { group, cancel } = concurrency("contribution-rights.yml");
  const events = {
    pull_request_target: { event_name: "pull_request_target", run_id: 1, event: { pull_request: { number: 42 } } },
    issue_comment: { event_name: "issue_comment", run_id: 2, event: { issue: { number: 42, pull_request: {} } } },
    check_run: { event_name: "check_run", run_id: 3, event: { check_run: { pull_requests: [{ number: 42 }] } } },
  };
  const groups = Object.fromEntries(Object.entries(events).map(([k, g]) => [k, evaluate(group, g)]));
  assert.equal(new Set(Object.values(groups)).size, 1, `rights runs for one PR land in different groups: ${JSON.stringify(groups)}`);
  for (const [k, g] of Object.entries(events)) assert.equal(evaluate(cancel, g), "false", `${k} runs can be cancelled mid-run`);
  const other = evaluate(group, { event_name: "issue_comment", run_id: 4, event: { issue: { number: 43 } } });
  assert.notEqual(other, groups.issue_comment, "two pull requests share a queue");
  const range = { event_name: "pull_request", run_id: 5, event: { pull_request: { number: 42 } } };
  assert.notEqual(evaluate(group, range), groups.pull_request_target, "dco-range waits behind the rights queue");
  assert.equal(evaluate(cancel, range), "true", "a newer push does not supersede a dco-range run");
});

// R6-30: an image a step runs (an action's `image:` input) is pinned by digest. setup-qemu-action runs
// binfmt privileged in a job that holds the registry token.
test("every image a workflow step names in `with: image:` is pinned by digest", () => {
  const unpinned = [];
  for (const f of readdirSync(WF).filter((n) => /\.ya?ml$/.test(n))) {
    for (const m of read(f).matchAll(/^\s+image:\s*["']?([^\s"'#]+)/gm)) {
      const ref = m[1];
      if (ref.includes("${{")) continue;                     // a matrix value, scanned not run
      if (!/@sha256:[0-9a-f]{64}$/.test(ref)) unpinned.push(`${f}: ${ref}`);
    }
  }
  assert.deepEqual(unpinned, []);
});
