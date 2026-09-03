// Tests OF the door checker — the instrument gate:domain-doors trusts.
//
// The first three run against a SYNTHETIC repo (mkdtemp) so they assert the RULE rather than
// today's tree: a planted undeclared door must be named, a declared-optional door must pass, a
// publish edge must pass. The last two run against THIS repository and are the ones that keep the
// allowlist honest — every entry still matches a real violation (a stale entry is a failure), and
// the tip is green.
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { checkDomainDoors, doorOwner, doorSites } from "./check-domain-doors.mjs";

const REPO = join(dirname(fileURLToPath(import.meta.url)), "..");

function fixture(files) {
  const root = mkdtempSync(join(tmpdir(), "doors-"));
  for (const [p, body] of Object.entries(files)) {
    mkdirSync(join(root, dirname(p)), { recursive: true });
    writeFileSync(join(root, p), body);
  }
  return root;
}

// A flows domain whose agent door is declared the #1453 way: class `capability`, so unset means
// "the agent domain is not deployed" and the step answers not_present.
const FLOWS_CONTRACT = `DECLARED = {
    "VEXA_FLOWS_AGENT_API_URL": ("capability", None, "agent-api's internal tier. UNSET MEANS THE AGENT DOMAIN IS NOT DEPLOYED."),
    "VEXA_FLOWS_GATEWAY_URL": ("required-explicit", None, "the meetings gateway."),
}
`;

test("a planted undeclared door goes red, naming file:line", () => {
  const root = fixture({
    "core/flows/src/flows_config.py": FLOWS_CONTRACT,
    "core/flows/src/flows_steps/hidden.py":
      'import os\n\n\ndef go():\n    base = os.getenv("VEXA_FLOWS_MEETING_API_URL", "")\n    return base\n',
  });
  try {
    const { violations } = checkDomainDoors(root);
    const hit = violations.find((v) => v.site === "core/flows/src/flows_steps/hidden.py:5");
    assert.ok(hit, `expected the planted door to be named; got ${JSON.stringify(violations)}`);
    assert.equal(hit.from, "flows");
    assert.equal(hit.to, "meetings");
    assert.equal(hit.door, "VEXA_FLOWS_MEETING_API_URL");
    assert.match(hit.why, /undeclared cross-domain door to meetings/);
    // the message must say what WOULD allow it, not only that it is refused
    assert.match(hit.why, /class 'capability'/);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test("a literal http://<service>: goes red too — a hardcoded door is still a door", () => {
  const root = fixture({
    "core/flows/src/flows_config.py": FLOWS_CONTRACT,
    "core/flows/src/flows_steps/hard.py": 'BASE = "http://meeting-api:8080"\n',
  });
  try {
    const { violations } = checkDomainDoors(root);
    assert.equal(violations.length, 1);
    assert.equal(violations[0].site, "core/flows/src/flows_steps/hard.py:1");
    assert.equal(violations[0].to, "meetings");
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test("a door declared OPTIONAL (capability + degrade) passes", () => {
  const root = fixture({
    "core/flows/src/flows_config.py": FLOWS_CONTRACT,
    "core/flows/src/flows_steps/common.py":
      'import flows_config\n\nAGENT_API = flows_config.get("VEXA_FLOWS_AGENT_API_URL")\n',
  });
  try {
    const { violations, sites } = checkDomainDoors(root);
    assert.ok(sites.some((s) => s.to === "agent"), "the door must still be SEEN, only allowed");
    assert.deepEqual(violations, []);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test("a door declared 'defaulted' does NOT pass — a default asserts the domain exists", () => {
  const root = fixture({
    "core/flows/src/flows_config.py":
      'DECLARED = {\n    "VEXA_FLOWS_AGENT_API_URL": ("defaulted", "http://agent-api:8100", "agent-api"),\n}\n',
    "core/flows/src/flows_steps/common.py":
      'import flows_config\n\nAGENT_API = flows_config.get("VEXA_FLOWS_AGENT_API_URL")\n',
  });
  try {
    const { violations } = checkDomainDoors(root);
    assert.equal(violations.length, 1);
    assert.match(violations[0].why, /declared 'defaulted'/);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test("a PUBLISH edge passes — a domain that declares publishes_events may reach flows' ingress", () => {
  const withManifest = (publishes) => fixture({
    "core/meetings/mcp.tools.v1.json": JSON.stringify({
      contract: "mcp.tools.v1", domain: "meetings", base_url_env: "MEETING_API_URL", tools: [],
      ...(publishes ? { publishes_events: [{ event: "meeting.completed" }] } : {}),
    }),
    "core/meetings/src/publish.py": 'import os\n\nFLOWS = os.getenv("FLOWS_API_URL", "")\n',
  });
  const yes = withManifest(true);
  const no = withManifest(false);
  try {
    assert.deepEqual(checkDomainDoors(yes).violations, [], "publishes_events must open flows' ingress");
    const off = checkDomainDoors(no).violations;
    assert.equal(off.length, 1, "without publishes_events the same door is a violation");
    assert.equal(off[0].to, "flows");
  } finally { rmSync(yes, { recursive: true, force: true }); rmSync(no, { recursive: true, force: true }); }
});

test("the edge forwards only through a declared route binding", () => {
  const root = fixture({
    "core/flows/mcp.tools.v1.json": JSON.stringify({
      contract: "mcp.tools.v1", domain: "flows", base_url_env: "FLOWS_API_URL", tools: [],
    }),
    "core/gateway/services/gateway/src/gateway/adapters.py":
      'import os\n\nflows = os.getenv("FLOWS_API_URL", "")\nagent = os.getenv("AGENT_API_URL", "")\n',
  });
  try {
    const { violations } = checkDomainDoors(root);
    assert.equal(violations.length, 1, "the bound door passes; the unbound one does not");
    assert.equal(violations[0].to, "agent");
    assert.match(violations[0].why, /without a declared route binding/);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test("a client may name the edge and nothing else", () => {
  const root = fixture({
    "clients/terminal/src/ok.ts": "const g = process.env.GATEWAY_URL;\n",
    "clients/terminal/src/bypass.ts": "const a = process.env.AGENT_API_URL;\n",
  });
  try {
    const { violations } = checkDomainDoors(root);
    assert.equal(violations.length, 1);
    assert.equal(violations[0].site, "clients/terminal/src/bypass.ts:1");
    assert.match(violations[0].why, /bypassing the edge/);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test("tests and evals are out of scope — they stand up doubles by design", () => {
  const root = fixture({
    "core/flows/src/flows_config.py": FLOWS_CONTRACT,
    "core/flows/tests/test_x.py": 'import os\nB = os.getenv("MEETING_API_URL", "")\n',
    "core/flows/eval/harness.py": 'import os\nB = os.getenv("MEETING_API_URL", "")\n',
    "core/flows/src/test_inline.py": 'import os\nB = os.getenv("MEETING_API_URL", "")\n',
  });
  try {
    assert.deepEqual(doorSites(root), []);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test("door ownership resolves to the service NAMED, never the service reading", () => {
  assert.equal(doorOwner("VEXA_FLOWS_ADMIN_API_URL"), "identity");
  assert.equal(doorOwner("VEXA_FLOWS_AGENT_API_URL"), "agent");
  assert.equal(doorOwner("VEXA_FLOWS_GATEWAY_URL"), "gateway");
  assert.equal(doorOwner("VEXA_FLOWS_API_URL"), "flows");
  assert.equal(doorOwner("RUNTIME_API_URL"), "runtime");
  // not doors: a credential, a dial, a database, a model provider
  assert.equal(doorOwner("VEXA_FLOWS_API_KEY"), null);
  assert.equal(doorOwner("VEXA_FLOWS_API_PORT"), null);
  assert.equal(doorOwner("DATABASE_URL"), null);
  assert.equal(doorOwner("ANTHROPIC_BASE_URL"), null);
});

test("every allowlist entry still matches a real violation (a stale entry is a failure)", () => {
  const { stale, allowlisted, allow } = checkDomainDoors(REPO);
  assert.deepEqual(stale, [], "allowlist entries that no longer match a violation must be deleted");
  assert.equal(allowlisted, (allow.entries || []).length);
  for (const e of allow.entries || []) {
    assert.ok(e.ruling && e.ruling.length > 40, `${e.site}: every entry names the ruling that closes it`);
    assert.match(e.site, /^[\w./[\]-]+:\d+$/, `${e.site}: entries are pinned to path:line`);
  }
});

test("this repository is green — no undeclared door outside the allowlist", () => {
  const { violations } = checkDomainDoors(REPO);
  assert.deepEqual(violations.map((v) => `${v.site} ${v.from}→${v.to}`), []);
});
