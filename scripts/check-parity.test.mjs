// Tests OF the fact-parity checker — the instrument gate:fact-parity trusts.
//
// The first group runs against synthetic repos (mkdtemp) so they assert the RULE, not today's tree.
// The last two run against THIS repository: the manifest still describes the tree, and the tip is
// green. The point of the synthetic ones is the failure modes that would make the gate LIE —
// a pattern that matches nothing, a pattern that matches twice, a ledger fact that quietly moved.
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { checkParity, asSet, asProse, asHeader, loadManifest } from "./check-parity.mjs";
import { guardTree } from "./test-tree.mjs";

guardTree();

const REPO = join(dirname(fileURLToPath(import.meta.url)), "..");

function fixture(facts, files) {
  const root = mkdtempSync(join(tmpdir(), "parity-"));
  const all = { "scripts/parity.json": JSON.stringify({ contract: "parity.v1", facts }), ...files };
  for (const [p, body] of Object.entries(all)) {
    mkdirSync(join(root, dirname(p)), { recursive: true });
    writeFileSync(join(root, p), body);
  }
  return root;
}
const run = (root) => { try { return checkParity(root); } finally { /* caller cleans */ } };

const MARK = (v) => `MACHINERY_MARK = "${v}"\n`;
const markFact = (extra = {}) => [{
  id: "machinery-mark", fact: "the machinery mark", kind: "literal", enforced: true,
  sites: [
    { path: "a/x.py", pattern: 'MACHINERY_MARK = "([^"]+)"' },
    { path: "b/y.ts", pattern: 'MACHINERY_MARK = "([^"]+)"' },
  ], ...extra,
}];

test("an ENFORCED fact whose sites agree passes; one that disagrees is named with both answers", () => {
  const ok = fixture(markFact(), { "a/x.py": MARK("[vexa-machinery]"), "b/y.ts": MARK("[vexa-machinery]") });
  const bad = fixture(markFact(), { "a/x.py": MARK("[vexa-machinery]"), "b/y.ts": MARK("[vexa-machinary]") });
  try {
    assert.deepEqual(run(ok).errs, []);
    const e = run(bad).errs;
    assert.equal(e.length, 1);
    assert.match(e[0], /DISAGREE \(2 answers\)/);
    assert.match(e[0], /\[vexa-machinery\]/);
    assert.match(e[0], /\[vexa-machinary\]/, "both live answers must be in the failure — the reader has to see what to reconcile");
    assert.match(e[0], /a\/x\.py:1/);
    assert.match(e[0], /b\/y\.ts:1/);
  } finally { rmSync(ok, { recursive: true, force: true }); rmSync(bad, { recursive: true, force: true }); }
});

test("a pattern that matches NOTHING fails — a manifest that stopped describing the tree reports green", () => {
  const root = fixture(markFact(), { "a/x.py": MARK("[vexa-machinery]"), "b/y.ts": "// the constant was renamed\n" });
  try {
    const e = run(root).errs;
    assert.equal(e.length, 1);
    assert.match(e[0], /matches nothing/);
    assert.match(e[0], /b\/y\.ts/);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test("a pattern that matches TWICE fails — the gate must not compare whichever came first", () => {
  const root = fixture(markFact(), { "a/x.py": MARK("[vexa-machinery]"), "b/y.ts": MARK("[vexa-machinery]") + MARK("[vexa-machinery]") });
  try {
    const e = run(root).errs;
    assert.equal(e.length, 1);
    assert.match(e[0], /matches 2 times/);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test("a SET fact compares membership, not spelling or order", () => {
  const facts = [{
    id: "live", fact: "the live set", kind: "set", enforced: true,
    sites: [
      { path: "a/p.py", pattern: "LIVE = \\{([^}]*)\\}" },
      { path: "b/q.ts", pattern: "LIVE = new Set\\(\\[([^\\]]*)\\]\\)" },
    ],
  }];
  const ok = fixture(facts, {
    "a/p.py": 'LIVE = {"active", "joining", "awaiting_admission"}\n',
    "b/q.ts": 'const LIVE = new Set(["awaiting_admission",\n  "active",\n  "joining"])\n',
  });
  const bad = fixture(facts, {
    "a/p.py": 'LIVE = {"active", "joining", "awaiting_admission"}\n',
    "b/q.ts": 'const LIVE = new Set(["active", "joining"])\n',
  });
  try {
    assert.deepEqual(run(ok).errs, [], "different order, different quoting, different container — same membership");
    assert.equal(run(bad).errs.length, 1, "a missing member is a real disagreement");
  } finally { rmSync(ok, { recursive: true, force: true }); rmSync(bad, { recursive: true, force: true }); }
});

test("asSet unquotes, de-dupes and sorts, and is blind to the container", () => {
  assert.deepEqual(asSet('"b", "a", \'a\''), ["a", "b"]);
  assert.deepEqual(asSet("[ 'z' , `y` ]"), ["y", "z"]);
});

test("PROSE compares the sentence, not the carrier — but never normalises the apostrophe", () => {
  const NL = "\n";
  const APOS = String.fromCharCode(39);
  const facts = [{
    id: "vis", fact: "the disclosure", kind: "prose", enforced: true,
    sites: [
      { path: "a/t.py", pattern: "S = \\(([\\s\\S]*?)\\)" },
      { path: "b/t.md", pattern: "^(Vexa runs[^\\n]*)$" },
      { path: "c/t.md", pattern: "((?:^> .*\\n)*^> Vexa runs[\\s\\S]*?agents\\.)" },
    ],
  }];
  const sentence = "Vexa runs on this org" + APOS + "s own servers; what you keep is visible to the agents.";
  const py = 'S = ("Vexa runs on this org' + APOS + 's own servers; what you keep "' + NL + '     "is visible to the agents.")' + NL;
  const md = sentence + NL;
  const quoted = "> Vexa runs on this org" + APOS + "s own servers; what you keep" + NL + "> is visible to the agents." + NL;
  const ok = fixture(facts, { "a/t.py": py, "b/t.md": md, "c/t.md": quoted });
  // the SAME sentence with a typographic apostrophe is a different sentence to the stranger reading it
  const curly = fixture(facts, { "a/t.py": py, "b/t.md": md.replace(APOS, "\u2019"), "c/t.md": quoted });
  try {
    assert.deepEqual(run(ok).errs, [],
      "a Python concat split, a markdown hard wrap and a blockquote are carriers, not different sentences");
    assert.equal(run(curly).errs.length, 1, "U+2019 vs U+0027 is a real difference in text a stranger reads");
  } finally { rmSync(ok, { recursive: true, force: true }); rmSync(curly, { recursive: true, force: true }); }
});

test("a HEADER fact compares field names case-insensitively, and nothing else", () => {
  const facts = [{
    id: "reentry", fact: "the re-entry header", kind: "header", enforced: true,
    sites: [
      { path: "gw/app.py", pattern: '^MCP_REENTRY_HEADER = "([^"]+)"' },
      { path: "mcp/reentry.py", pattern: '^OUTBOUND_HEADER = "([^"]+)"' },
    ],
  }];
  const ok = fixture(facts, { "gw/app.py": 'MCP_REENTRY_HEADER = "x-vexa-internal-mcp-identity"\n',
                              "mcp/reentry.py": 'OUTBOUND_HEADER = "X-Vexa-Internal-Mcp-Identity"\n' });
  const bad = fixture(facts, { "gw/app.py": 'MCP_REENTRY_HEADER = "x-vexa-internal-mcp-identity"\n',
                               "mcp/reentry.py": 'OUTBOUND_HEADER = "X-Vexa-Internal-MCP-Identities"\n' });
  try {
    assert.deepEqual(run(ok).errs, [], "one header in two cases is one answer");
    const e = run(bad).errs;
    assert.equal(e.length, 1);
    assert.match(e[0], /DISAGREE/);
  } finally { rmSync(ok, { recursive: true, force: true }); rmSync(bad, { recursive: true, force: true }); }
  assert.equal(asHeader(" X-Vexa-Identity "), "x-vexa-identity");
});

test("an EXTENDS fact must hold every member of its base", () => {
  const facts = (extra) => [
    { id: "base", fact: "the placeholders", kind: "set", enforced: true,
      sites: [{ path: "a/base.py", pattern: "^BASE = \\(([^)]*)\\)" }] },
    { id: "db", fact: "the db placeholders", kind: "set", enforced: true, extends: "base",
      sites: [{ path: "b/db.py", pattern: "^DB = \\(([^)]*)\\)" }], ...extra },
  ];
  const ok = fixture(facts(), { "a/base.py": 'BASE = ("changeme", "secret")\n',
                               "b/db.py": 'DB = ("postgres", "changeme", "secret")\n' });
  const bad = fixture(facts(), { "a/base.py": 'BASE = ("changeme", "secret")\n',
                                "b/db.py": 'DB = ("postgres", "changeme")\n' });
  const unknown = fixture(facts({ extends: "nope" }), { "a/base.py": 'BASE = ("x")\n', "b/db.py": 'DB = ("x")\n' });
  try {
    assert.deepEqual(run(ok).errs, []);
    const e = run(bad).errs;
    assert.equal(e.length, 1);
    assert.match(e[0], /lacks secret/);
    assert.match(run(unknown).errs.join("\n"), /not a set fact/);
  } finally { for (const r of [ok, bad, unknown]) rmSync(r, { recursive: true, force: true }); }
});

test("asProse strips the carrier and keeps the apostrophe", () => {
  assert.equal(asProse('> one\n> two'), "one two");
  assert.equal(asProse('"a "\n  "b"'), "a b");
  assert.equal(asProse("it's"), "it's");
});

test('"all": every occurrence in one file must agree before the file has an answer', () => {
  const facts = [{
    id: "idx", fact: "the index predicate", kind: "set", enforced: true,
    sites: [
      { path: "a/models.py", pattern: "IN \\(([^)]*)\\)", all: true },
      { path: "b/models.py", pattern: "IN \\(([^)]*)\\)", all: true },
    ],
  }];
  const two = "x = \"IN ('a', 'b')\"\ny = \"IN ('a', 'b')\"\n";
  const ok = fixture(facts, { "a/models.py": two, "b/models.py": two });
  const inner = fixture(facts, { "a/models.py": "x = \"IN ('a', 'b')\"\ny = \"IN ('a', 'c')\"\n", "b/models.py": two });
  try {
    assert.deepEqual(run(ok).errs, [], "two identical occurrences per file is one answer per file");
    const e = run(inner).errs;
    assert.equal(e.length, 1);
    assert.match(e[0], /its own copies disagree/,
      "an intra-file disagreement must be named where it happens, not averaged into the first match");
  } finally { rmSync(ok, { recursive: true, force: true }); rmSync(inner, { recursive: true, force: true }); }
});

test("forbid_elsewhere makes the site list an INVENTORY — a stray writer fails, a test does not", () => {
  const facts = [{
    id: "mark", fact: "the mark", kind: "literal", enforced: true, canonical: "core/shared/marks.py",
    sites: [{ path: "core/shared/marks.py", pattern: 'MARK = "([^"]+)"' }],
    forbid_elsewhere: "[vexa-machinery]", scan: ["core", "clients"],
  }];
  const clean = fixture(facts, {
    "core/shared/marks.py": 'MARK = "[vexa-machinery]"\n',
    "core/agent/tests/test_x.py": 'assert "[vexa-machinery]" in out\n',
    "core/agent/uses.py": "from shared.marks import MARK\n",
  });
  const stray = fixture(facts, {
    "core/shared/marks.py": 'MARK = "[vexa-machinery]"\n',
    "clients/terminal/src/retyped.ts": 'const M = "[vexa-machinery]";\n',
  });
  try {
    assert.deepEqual(run(clean).errs, [], "a test that asserts the literal is not a second writer of it");
    const e = run(stray).errs;
    assert.equal(e.length, 1);
    assert.match(e[0], /clients\/terminal\/src\/retyped\.ts:1/);
    assert.match(e[0], /not a declared site/);
  } finally { rmSync(clean, { recursive: true, force: true }); rmSync(stray, { recursive: true, force: true }); }
});

const ledgerFact = (answers) => [{
  id: "slug", fact: "the entity slug", kind: "literal", enforced: false,
  decision: "which slug the server writes and the client must reproduce",
  answers,
  sites: [
    { path: "a/x.py", pattern: 'SLUG = "([^"]+)"' },
    { path: "b/y.ts", pattern: 'SLUG = "([^"]+)"' },
  ],
}];

test("a LEDGER fact passes while it matches what was recorded, and fails the moment it moves", () => {
  const files = { "a/x.py": 'SLUG = "[^a-zA-Z0-9]+"\n', "b/y.ts": 'SLUG = "[^a-z0-9]+"\n' };
  const BASE = [{ value: "[^a-z0-9]+", sites: ["b/y.ts"] }, { value: "[^a-zA-Z0-9]+", sites: ["a/x.py"] }];
  const pinned = fixture(ledgerFact(BASE), files);
  const moved = fixture(ledgerFact(BASE), { ...files, "b/y.ts": 'SLUG = "[^a-z0-9_]+"\n' });
  try {
    assert.deepEqual(run(pinned).errs, [], "a drifted fact that has not moved is not news");
    const e = run(moved).errs;
    assert.equal(e.length, 1);
    assert.match(e[0], /has CHANGED since it was recorded/);
    assert.match(e[0], /decision pending|Decision pending/);
  } finally { rmSync(pinned, { recursive: true, force: true }); rmSync(moved, { recursive: true, force: true }); }
});

test("a LEDGER fact that comes into agreement FAILS — the decision was taken, so enforce it", () => {
  const root = fixture(ledgerFact([{ value: "[^a-z0-9]+", sites: ["b/y.ts"] }, { value: "[^a-zA-Z0-9]+", sites: ["a/x.py"] }]),
    { "a/x.py": 'SLUG = "[^a-z0-9]+"\n', "b/y.ts": 'SLUG = "[^a-z0-9]+"\n' });
  try {
    const e = run(root).errs;
    assert.equal(e.length, 1);
    assert.match(e[0], /now IN PARITY/);
    assert.match(e[0], /"enforced": true/, "the failure must say exactly what to do next");
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test("a LEDGER fact reds when a site SWITCHES SIDES, even though the answers are unchanged", () => {
  // The failure this test exists for: pinning only the distinct VALUES let a site move from one
  // camp to the other in silence, because both values were still present. What is pinned is the
  // MAPPING. Measured on the real tree first: flipping core/agent's runtime hostname was invisible.
  const three = [
    { path: "a/x.py", pattern: 'H = "([^"]+)"' },
    { path: "b/y.py", pattern: 'H = "([^"]+)"' },
    { path: "c/z.py", pattern: 'H = "([^"]+)"' },
  ];
  const facts = (answers) => [{ id: "host", fact: "the runtime hostname", kind: "literal",
    enforced: false, decision: "what the runtime is actually called on the network", answers, sites: three }];
  const pinnedAnswers = [
    { value: "http://runtime-api:8090", sites: ["a/x.py", "b/y.py"] },
    { value: "http://runtime:8090", sites: ["c/z.py"] },
  ];
  const H = (v) => `H = "${v}"\n`;
  const same = fixture(facts(pinnedAnswers), { "a/x.py": H("http://runtime-api:8090"), "b/y.py": H("http://runtime-api:8090"), "c/z.py": H("http://runtime:8090") });
  const switched = fixture(facts(pinnedAnswers), { "a/x.py": H("http://runtime-api:8090"), "b/y.py": H("http://runtime:8090"), "c/z.py": H("http://runtime:8090") });
  try {
    assert.deepEqual(run(same).errs, []);
    const e = run(switched).errs;
    assert.equal(e.length, 1, "both values still exist — a set comparison would have seen nothing");
    assert.match(e[0], /has CHANGED since it was recorded/);
    assert.match(e[0], /b\/y\.py/, "the failure must name the site that moved");
  } finally { rmSync(same, { recursive: true, force: true }); rmSync(switched, { recursive: true, force: true }); }
});

test("an unenforced fact that names no decision is itself an error", () => {
  const root = fixture([{ id: "x", fact: "f", kind: "literal", enforced: false, answers: [], sites: [] }], {});
  try {
    assert.ok(run(root).errs.some((e) => /must name the decision/.test(e)));
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test("a file-bytes fact is byte-exact", () => {
  const facts = [{ id: "pre", fact: "the preflight", kind: "file-bytes", enforced: true,
    sites: [{ path: "a/p.py" }, { path: "b/p.py" }] }];
  const ok = fixture(facts, { "a/p.py": "x = 1\n", "b/p.py": "x = 1\n" });
  const bad = fixture(facts, { "a/p.py": "x = 1\n", "b/p.py": "x = 1\n\n" });
  try {
    assert.deepEqual(run(ok).errs, []);
    assert.equal(run(bad).errs.length, 1, "one trailing newline is a drift — that is the point of vendoring VERBATIM");
  } finally { rmSync(ok, { recursive: true, force: true }); rmSync(bad, { recursive: true, force: true }); }
});

test("this repository's manifest still describes the tree, and the tip is green", () => {
  const res = checkParity(REPO);
  assert.deepEqual(res.errs, []);
  assert.ok(res.enforced.length >= 1, "at least the control case is enforced");
  for (const f of res.manifest.facts || []) {
    assert.ok(f.fact && f.kind && Array.isArray(f.sites), `${f.id}: a fact names itself, its kind and its sites`);
    if (!f.enforced) {
      assert.ok(f.decision && f.decision.length > 20, `${f.id}: the ledger names the decision that settles it`);
      assert.ok(Array.isArray(f.answers) && f.answers.length, `${f.id}: a ledger fact pins WHO gives WHICH answer`);
      assert.ok(!("distinct" in f), `${f.id}: the old value-only baseline must not survive — a site could switch sides under it`);
    }
    if (f.enforced) assert.ok(f.canonical || f.sites.length === 1, `${f.id}: an enforced fact names where the truth lives`);
  }
});

// The two facts the 2026-10-08 architecture pass found written twice with nothing holding them
// together. Each test copies the REAL sites into a fixture, so it fails if the manifest stops
// naming a site, and drifts exactly one copy, so it fails if the gate would not notice.
function realFixture(id, drift) {
  const fact = loadManifest(REPO).facts.find((f) => f.id === id);
  assert.ok(fact, `${id} is a fact in scripts/parity.json`);
  assert.equal(fact.enforced, true, `${id} is enforced, not ledgered`);
  const files = {};
  for (const s of fact.sites) files[s.path] = readFileSync(join(REPO, s.path), "utf8");
  return { fact, clean: fixture([fact], files), drifted: fixture([fact], { ...files, ...drift(files) }) };
}

test("the seed's unwritten marker: every seed file and flows' copy must match core/agent's", () => {
  const { fact, clean, drifted } = realFixture("unwritten-marker", (f) => ({
    "behavior/global/OBJECTIVES.md": f["behavior/global/OBJECTIVES.md"].replace("vexa:unwritten", "vexa:unwriten"),
  }));
  try {
    assert.ok(fact.sites.some((s) => s.path === "core/flows/src/flows_steps/mailtext.py"));
    assert.ok(fact.sites.filter((s) => s.path.startsWith("behavior/global/")).length >= 5);
    assert.deepEqual(run(clean).errs, []);
    const e = run(drifted).errs;
    assert.equal(e.length, 1);
    assert.match(e[0], /OBJECTIVES\.md/);
  } finally { rmSync(clean, { recursive: true, force: true }); rmSync(drifted, { recursive: true, force: true }); }
});

test("the Claude Code pin: the worker and Lite images install the same version", () => {
  const { clean, drifted } = realFixture("claude-code-pin", (f) => ({
    "deploy/lite/Dockerfile.lite": f["deploy/lite/Dockerfile.lite"].replace(/claude-code@[0-9.]+/, "claude-code@2.0.0"),
  }));
  try {
    assert.deepEqual(run(clean).errs, []);
    const e = run(drifted).errs;
    assert.equal(e.length, 1);
    assert.match(e[0], /2\.0\.0/);
  } finally { rmSync(clean, { recursive: true, force: true }); rmSync(drifted, { recursive: true, force: true }); }
});

test("forbid_elsewhere sweeps Dockerfiles, which carry no extension", () => {
  const facts = [{
    id: "pin", fact: "the pin", kind: "literal", enforced: true, canonical: "core/worker/Dockerfile",
    sites: [{ path: "core/worker/Dockerfile", pattern: "tool@([0-9.]+)" }],
    forbid_elsewhere: "tool@", scan: ["core", "deploy"],
  }];
  const stray = fixture(facts, {
    "core/worker/Dockerfile": "RUN npm i -g tool@1.2.3\n",
    "deploy/lite/Dockerfile.lite": "RUN npm i -g tool@1.2.3\n",
  });
  try {
    const e = run(stray).errs;
    assert.equal(e.length, 1);
    assert.match(e[0], /deploy\/lite\/Dockerfile\.lite:1/);
  } finally { rmSync(stray, { recursive: true, force: true }); }
});
