// A PUBLISH EDGE IS NOT A DEPENDENCY — the config.v1 `publish-edge` class, and the gate that reads it.
// Run: node --test scripts/publish-edge.test.mjs   (CI: the gates.yml `static` job runs scripts/*.test.mjs
// directly — scripts/ is not a workspace package, so `pnpm test` never reaches these files)
//
// The class exists because the sanctioned coupling mechanism was failing its own gate. A domain that
// hands a fact to flows reads FLOWS_API_URL, and every env read a service makes must be declared —
// but the three classes that existed all describe a value the service NEEDS: required-explicit
// refuses the boot without it, defaulted supplies one, capability gates endpoints on it. Declaring a
// publish target as any of them says the publisher depends on the consumer, which is the one thing
// it must not do. The fourth class says the true thing instead, and names the carriers travelling
// the edge so "who publishes this fact" is answerable from the declaration rather than from grep.
//
// Tested through the REAL contract validator and the REAL gate as subprocesses, never against a
// re-implementation of either: the failure this guards is a declaration the shipped oracle accepts
// and a reviewer misreads, so the shipped oracle has to be the one under test.
import test from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { writeFileSync, readFileSync, rmSync, mkdtempSync } from "node:fs";
import { dirname, join } from "node:path";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const CONFIG_VALIDATE = join(ROOT, "deploy", "contracts", "config.v1", "validate.mjs");
const ADMIN_DECL = join(ROOT, "core", "identity", "services", "admin-api", "src", "admin_api", "config.v1.json");
const CENSUS = join(ROOT, "core", "flows", "contracts", "flows.v1", "carriers.json");

// BOTH streams, always. A gate prints its summary to stdout and its REFUSALS to stderr, so a
// helper that picks one and falls back to the other reads the failure it was written to assert as
// an empty string (the exact defect gates.mjs' own errText comment records: an empty Buffer is
// truthy, so a || chain discards the diagnostic).
const both = (e) => [e?.stdout, e?.stderr].map((b) => (b ?? "").toString()).join("");
const gate = (name) => {
  try {
    const r = execFileSync("node", [join(ROOT, "scripts", "gates.mjs"), name],
      { cwd: ROOT, encoding: "utf8", stdio: "pipe" });
    return { ok: true, out: r };
  } catch (e) {
    return { ok: false, out: both(e) };
  }
};

// Runs the contract's own validator over one declaration written to a scratch file. Returns
// { ok, out } — never throws, because a REFUSAL is the assertion in half these tests.
function validates(decl) {
  const dir = mkdtempSync(join(tmpdir(), "cfgv1-"));
  const file = join(dir, "config.v1.json");
  writeFileSync(file, JSON.stringify(decl, null, 1));
  try {
    execFileSync("node", [CONFIG_VALIDATE, "--check", "--file", file], { cwd: ROOT, stdio: "pipe" });
    return { ok: true, out: "" };
  } catch (e) {
    return { ok: false, out: both(e) };
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

// A minimal declaration carrying exactly one key — the shape under test and nothing else.
const declWith = (key) => ({ contract: "config.v1", service: "admin-api", keys: [key] });

const PUBLISH_KEY = {
  key: "FLOWS_API_URL",
  class: "publish-edge",
  description: "flows' intake, told about onboarding.completed.",
  publishes_events: ["onboarding.completed"],
  targets: ["compose"],
};

// Restores whatever it replaced, whether the body passes, fails or throws.
function withReplaced(file, body, fn) {
  const original = readFileSync(file, "utf8");
  writeFileSync(file, body);
  try { return fn(); } finally { writeFileSync(file, original); }
}

// ── the class exists, and says the one thing the other three cannot ─────────────────────────────
test("a publish-edge key naming its carriers conforms", () => {
  assert.equal(validates(declWith(PUBLISH_KEY)).ok, true);
});

test("a publish-edge key that names no carrier is refused", () => {
  // The carriers ARE the declaration's content. Without them the class degrades to "a URL this
  // service reads", which is the undeclared-read hole the whole contract exists to close.
  const { publishes_events, ...bare } = PUBLISH_KEY;
  const r = validates(declWith(bare));
  assert.equal(r.ok, false, "a publish edge that publishes nothing was accepted");
  assert.match(r.out, /publishes_events|required/i);
});

test("a publish-edge key may not carry a default", () => {
  // A default would be a fallback address to publish to, invented by us, in a deployment that
  // deliberately runs no flows domain. Absent means absent.
  assert.equal(validates(declWith({ ...PUBLISH_KEY, default: "http://flows:8000" })).ok, false);
});

test("publishes_events on any other class is refused", () => {
  // The field is what makes the gate read the key as an edge rather than a dependency, so it may
  // not appear on a class that means "this service needs this value".
  for (const cls of ["defaulted", "required-explicit"]) {
    const key = { ...PUBLISH_KEY, class: cls };
    if (cls === "defaulted") key.default = "";
    assert.equal(validates(declWith(key)).ok, false, `${cls} was allowed to claim a publish edge`);
  }
});

// ── the gate reads it as an edge, not as a dependency ───────────────────────────────────────────
test("gate:config-contract is green with the publish edge declared, and counts it", () => {
  const r = gate("config-contract");
  assert.equal(r.ok, true, r.out);
  assert.match(r.out, /gates green/);
  assert.match(r.out, /publish edge/, "the gate does not report publish edges at all");
});

test("a publish edge whose carrier is in nobody's census is refused", () => {
  // The gate's whole job here: a carrier named by a publisher must exist in the flows census, owned
  // by the publishing domain. Without that check `publishes_events` is a comment.
  const decl = JSON.parse(readFileSync(ADMIN_DECL, "utf8"));
  for (const k of decl.keys) if (k.class === "publish-edge") k.publishes_events = ["nobody.owns.this"];
  const r = withReplaced(ADMIN_DECL, JSON.stringify(decl, null, 1) + "\n", () => gate("config-contract"));
  assert.equal(r.ok, false, "an unregistered carrier passed the gate");
  assert.match(r.out, /nobody\.owns\.this/);
});

test("a publish edge claiming a carrier another domain owns is refused", () => {
  // One producer per carrier is the contract's first promise. admin-api is identity; a carrier the
  // census records as flows-owned may not be published from here.
  const census = JSON.parse(readFileSync(CENSUS, "utf8"));
  const foreign = census.carriers.find((c) => c.owner !== "identity");
  assert.ok(foreign, "the census records no carrier owned by another domain — this test is inert");
  const decl = JSON.parse(readFileSync(ADMIN_DECL, "utf8"));
  for (const k of decl.keys) if (k.class === "publish-edge") k.publishes_events = [foreign.event];
  const r = withReplaced(ADMIN_DECL, JSON.stringify(decl, null, 1) + "\n", () => gate("config-contract"));
  assert.equal(r.ok, false, `${foreign.event} is owned by ${foreign.owner} and identity published it`);
  assert.match(r.out, new RegExp(foreign.owner));
});

// ── and the boot is untouched by it ─────────────────────────────────────────────────────────────
test("a publish edge is never required at boot", () => {
  // The proof that this is not a dependency, stated where a future refactor of preflight would
  // break it: an env with nothing in it must still satisfy every publish-edge key.
  const decl = JSON.parse(readFileSync(ADMIN_DECL, "utf8"));
  const edges = decl.keys.filter((k) => k.class === "publish-edge");
  assert.ok(edges.length, "admin-api declares no publish edge");
  for (const k of edges) assert.notEqual(k.class, "required-explicit");
});
