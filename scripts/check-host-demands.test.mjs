// Tests OF the host-demand checker (P24) — the instrument gate:host-demands trusts.
//
// The synthetic cases run against a mkdtemp tree so they assert the RULE rather than today's
// tree: a planted new demand goes red naming the file and the demand, a listed one passes, a
// stale entry goes red, an entry whose row is not in the requirements table goes red, and a
// comment naming a demand is not one. The last case runs against THIS repository: the tip is green.
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { checkHostDemands, demandSites } from "./check-host-demands.mjs";
import { guardTree } from "./test-tree.mjs";

guardTree();

const REPO = join(dirname(fileURLToPath(import.meta.url)), "..");
const TABLE = "| ID | Requirement |\n|---|---|\n| F2 | a hostPath |\n| D2 | CREATEROLE |\n";

function fixture(files) {
  const root = mkdtempSync(join(tmpdir(), "host-demands-"));
  for (const [p, body] of Object.entries(files)) {
    mkdirSync(join(root, dirname(p)), { recursive: true });
    writeFileSync(join(root, p), body);
  }
  return root;
}
const ledger = (entries) => JSON.stringify({ entries });
const HOSTPATH = "spec:\n  volumes:\n    - name: x\n      hostPath:\n        path: /var/lib/x\n";

test("a planted hostPath with no ledger entry goes red, naming the file and the demand", () => {
  const root = fixture({
    "deploy/helm/charts/vexa/templates/new.yaml": HOSTPATH,
    "docs/docs/deployment-kubernetes.mdx": TABLE,
    "scripts/host-demands.json": ledger([]),
  });
  try {
    const { errs } = checkHostDemands(root);
    assert.equal(errs.length, 1);
    assert.match(errs[0], /templates\/new\.yaml:4 makes a new host demand \(host-path\)/);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test("the same demand passes once listed against a row in the requirements table", () => {
  const root = fixture({
    "deploy/helm/charts/vexa/templates/new.yaml": HOSTPATH,
    "docs/docs/deployment-kubernetes.mdx": TABLE,
    "scripts/host-demands.json": ledger([{ file: "deploy/helm/charts/vexa/templates/new.yaml", demand: "host-path", row: "F2", reason: "the kubelet reads it from the node only" }]),
  });
  try { assert.deepEqual(checkHostDemands(root).errs, []); }
  finally { rmSync(root, { recursive: true, force: true }); }
});

test("an entry whose row the operator cannot see is red", () => {
  const root = fixture({
    "deploy/helm/charts/vexa/templates/new.yaml": HOSTPATH,
    "docs/docs/deployment-kubernetes.mdx": TABLE,
    "scripts/host-demands.json": ledger([{ file: "deploy/helm/charts/vexa/templates/new.yaml", demand: "host-path", row: "Z9", reason: "the kubelet reads it from the node only" }]),
  });
  try { assert.match(checkHostDemands(root).errs.join("\n"), /row Z9, which is not in the requirements table/); }
  finally { rmSync(root, { recursive: true, force: true }); }
});

test("a stale entry is red: the ledger only shrinks", () => {
  const root = fixture({
    "docs/docs/deployment-kubernetes.mdx": TABLE,
    "scripts/host-demands.json": ledger([{ file: "deploy/compose/docker-compose.yml", demand: "docker-socket", row: "F2", reason: "the runtime spawns through the socket" }]),
  });
  try { assert.match(checkHostDemands(root).errs.join("\n"), /no longer matches anything/); }
  finally { rmSync(root, { recursive: true, force: true }); }
});

test("SQL in a service: CREATE ROLE and SUPERUSER are demands, NOSUPERUSER and comments are not", () => {
  const root = fixture({
    "core/x/src/x/schema.py": [
      "# CREATE ROLE is explained here, in a comment",
      "conn.run('ALTER ROLE r WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE')",
      "conn.run('CREATE ROLE r LOGIN')",
      "conn.run('ALTER ROLE app SUPERUSER')",
      "conn.run('CREATE EXTENSION vector')",
    ].join("\n"),
    "core/x/tests/test_schema.py": "conn.run('CREATE DATABASE t')\n",
  });
  try {
    const found = [...demandSites(root).values()].map((s) => `${s.demand}@${s.lines.join(",")}`).sort();
    assert.deepEqual(found, ["db-createrole@3", "db-extension@5", "db-superuser@4"]);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test("Compose and Pod-spec spellings are seen", () => {
  const root = fixture({
    "deploy/compose/docker-compose.yml": "services:\n  a:\n    privileged: true\n    cap_add: [SYS_ADMIN]\n    network_mode: host\n    user: \"0:0\"\n    security_opt: [\"seccomp:unconfined\"]\n",
    "core/runtime/src/k.py": "security['capabilities']['add'] = ['NET_ADMIN']\n",
    "deploy/helm/charts/vexa/templates/r.yaml": "kind: ClusterRole\n---\nkind: DaemonSet\nhostPID: true\nrunAsUser: 0\n",
  });
  try {
    const found = new Set([...demandSites(root).values()].map((s) => s.demand));
    for (const d of ["privileged", "added-capability", "host-namespace", "root-user", "unconfined", "cluster-rbac", "daemonset"])
      assert.ok(found.has(d), `${d} not seen`);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test("this repository: every host demand is listed against a visible row, and no entry is stale", () => {
  const { errs } = checkHostDemands(REPO);
  assert.deepEqual(errs, []);
});
