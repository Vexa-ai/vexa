// Unit tests for pr-radius.mjs: which PRs the independent reviewer may sign (small) and which
// still need a maintainer (large). Each large kind has a positive case from a real path on the
// line, and every small class has a negative control that must NOT be read as large.
// Run: node --test scripts/pr-radius.test.mjs

import test from "node:test";
import assert from "node:assert/strict";
import { classify, pathsOfFiles, unitOf } from "./pr-radius.mjs";

const large = (paths, kind) => {
  const r = classify(paths);
  assert.equal(r.radius, "large", `${paths.join(", ")} should be large: ${r.why}`);
  if (kind) assert.ok(r.kinds[kind], `${paths.join(", ")} should carry kind ${kind}, got ${Object.keys(r.kinds)}`);
  return r;
};
const small = (paths) => {
  const r = classify(paths);
  assert.equal(r.radius, "small", `${paths.join(", ")} should be small: ${r.why}`);
  return r;
};

// ── small: the founder's list, and negative controls for every large kind ──────────────────────

test("CI-only is small", () => {
  small([".github/workflows/terminal-layout.yml", ".github/workflows/pr-welcome.yml", ".github/ISSUE_TEMPLATE/bug.yml"]);
});

test("docs-only is small, across the site and service READMEs", () => {
  small(["docs/docs/governance/delivery.mdx", "docs/adr/0050-something.md", "core/meetings/services/bot/README.md", "README.md"]);
});

test("tests-only is small, even across several services", () => {
  const r = small([
    "core/meetings/services/meeting-api/tests/test_bots.py",
    "core/agent/tests/test_api.py",
    "clients/terminal/src/ui-kit/__tests__/primitives.test.tsx",
    "tests3/Makefile",
  ]);
  assert.deepEqual(r.units, []);
});

test("one service's code (no db, no contract) is small, with its tests and docs", () => {
  const r = small([
    "core/meetings/services/transcription/src/transcription/languages.py",
    "core/meetings/services/transcription/tests/test_languages.py",
    "docs/docs/api/transcription.mdx",
  ]);
  assert.deepEqual(r.units, ["core/meetings/services/transcription"]);
});

test("one client's code is small", () => {
  small(["clients/terminal/src/app/meetings/page.tsx", "clients/terminal/src/ui-kit/primitives/Chip.tsx"]);
});

test("negative controls: words near a large kind do not trip it", () => {
  small(["core/meetings/services/bot/src/platforms/meet/admission.ts"]);        // bot code, not the protocol
  small(["docs/changelog.d/1783-db-schema-flows.md"]);                           // a changelog entry naming the db gate
  small(["core/meetings/services/transcription/src/transcription/tokenizer.py"]); // token ≠ auth
  small([".github/workflows/terminal-layout.yml"]);                               // a workflow that is not a gate
  small(["core/meetings/services/meeting-api/src/meeting_api/routers/bots.py"]);
});

// ── large: one per kind ────────────────────────────────────────────────────────────────────────

test("database: schema dir, models, sql, migration notes", () => {
  large(["core/identity/services/admin-api/src/admin_api/schema/sync.py"], "database");
  large(["core/meetings/services/meeting-api/src/meeting_api/sessions/models.py"], "database");
  large(["core/flows/schema.sql"], "database");
  large(["core/agent/db/migrations/0003_add.py"], "database");
  large(["schema.seal.json"], "database");
});

test("contracts: contract dirs, goldens, json schemas, seals", () => {
  large(["core/meetings/contracts/webhook.v1/golden/Envelope.meeting-completed.json"], "contracts");
  large(["core/gateway/contracts/routes.v1/routes.json"], "contracts");
  large(["core/meetings/modules/recording/src/contracts/golden/generate.mjs"], "contracts");
  large(["packages/foo/event.schema.json"], "contracts");
  large(["contracts.seal.json"], "contracts");
});

test("a contract's test or README is still a contract change", () => {
  large(["core/gateway/contracts/gateway-identity.v1/golden/README.md"], "contracts");
  large(["core/meetings/contracts/sdk-capture.v1/tests/test_shape.py"], "contracts");
});

test("bot protocol", () => {
  large(["core/meetings/services/bot/src/protocol/messages.ts"], "bot protocol");
  large(["core/meetings/services/bot/src/capture.v1.ts"], "bot protocol");
  large(["core/seam/meeting-status-ws/server.py"], "seam");
});

test("billing", () => {
  large(["core/identity/services/admin-api/src/admin_api/billing/usage.py"], "billing");
  large(["clients/terminal/src/app/settings/StripePortal.tsx"], "billing");
});

test("auth and identity, including the whole identity domain and the credential broker", () => {
  large(["core/identity/services/admin-api/src/admin_api/routers/users.py"], "auth");
  large(["core/agent/services/credential-broker/src/credential_broker/vault.py"], "auth");
  large(["clients/terminal/src/app/api/auth/route.ts"], "auth");
  large(["core/meetings/services/meeting-api/src/meeting_api/oauth_zoom.py"], "auth");
});

test("gateway code (the auth edge); its tests stay neutral", () => {
  large(["core/gateway/services/gateway/src/gateway/app.py"], "gateway");
  small(["core/gateway/services/gateway/tests/test_proxy.py"]);
});

test("the merge gates themselves: card, radius, reviewer, gate scripts, seals, scanners", () => {
  for (const p of [
    "scripts/merge-card-gate.mjs", "scripts/merge-card-gate.test.mjs", "scripts/pr-radius.mjs", "scripts/pr-radius.test.mjs",
    "scripts/vexa-reviewer.mjs", "scripts/gates.mjs", "scripts/check-domain-doors.mjs", "scripts/domain-doors.allow.json",
    ".github/workflows/merge-card.yml", ".github/workflows/merge-card-pass.yml", ".github/workflows/vexa-reviewer.yml",
    ".github/workflows/gates.yml", ".github/workflows/codeql.yml", ".github/workflows/dco-no-override.yml",
    ".github/CODEOWNERS", "SECURITY.md", "osv-scanner.toml", ".trivyignore", "architecture.seal.json", "license-exceptions.json",
  ]) large([p], "gate");
});

test("deploy and release", () => {
  large(["deploy/helm/charts/vexa/values.yaml"], "deploy");
  large(["deploy/lite/bin/no-user-namespaces"], "deploy");
  large(["release/notes.mjs"], "release");
  large([".github/workflows/release-images.yml"], "release");
});

test("shared code and repo-wide manifests reach every service", () => {
  large(["libs/py/vexa_common/log.py"], "shared");
  large(["pnpm-lock.yaml"], "shared");
  large(["package.json"], "shared");
});

test("runtime code in two units is large", () => {
  const r = large(["core/meetings/services/bot/src/index.ts", "core/meetings/services/meeting-api/src/meeting_api/main.py"]);
  assert.match(r.why, /2 units/);
  large(["core/agent/control_plane/home.py", "core/agent/services/agent-api/src/app.py"]); // domain code + one of its services
});

test("a path nobody classified goes to the maintainer", () => {
  const r = large(["weird-new-top-level/thing.py"], "unclassified");
  assert.match(r.why, /unclassified/);
});

test("an empty list is large (the card refuses what it cannot see)", () => {
  assert.equal(classify([]).radius, "large");
});

test("one large path makes a small PR large", () => {
  large([".github/workflows/terminal-layout.yml", "docs/x.md", "core/flows/schema.sql"], "database");
});

test("a rename brings its old path: moving a contract out of contracts/ is still a contract change", () => {
  const files = [{ filename: "core/meetings/modules/x/shape.json", previous_filename: "core/meetings/contracts/x.v1/shape.json" }];
  assert.deepEqual(pathsOfFiles(files), ["core/meetings/modules/x/shape.json", "core/meetings/contracts/x.v1/shape.json"]);
  large(pathsOfFiles(files), "contracts");
});

test("units", () => {
  assert.equal(unitOf("core/meetings/services/bot/src/a.ts"), "core/meetings/services/bot");
  assert.equal(unitOf("core/flows/src/flows/run.py"), "core/flows");
  assert.equal(unitOf("clients/terminal/src/a.ts"), "clients/terminal");
  assert.equal(unitOf("Makefile"), null);
});
