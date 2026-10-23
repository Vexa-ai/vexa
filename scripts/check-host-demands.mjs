#!/usr/bin/env node
/**
 * The HOST-DEMAND checker — the mechanism of P24 ("least demand on the host, fewest primitives").
 *
 * Every requirement Vexa places on the operator's platform is a cost the operator pays, and the
 * expensive ones are the ones nobody listed: the schema Jobs needed a Postgres superuser, nothing
 * said so, and a bank's own database could not run them. P24 says each such demand is the minimum,
 * is a row in the platform-requirements table of docs/docs/deployment-kubernetes.mdx with its
 * reason, and is added only on purpose.
 *
 * WHAT THIS GATE SEES: a line in what an install runs (the Helm chart, the Compose stack, Lite, and
 * the services' non-test Python/SQL/shell) that asks the platform for a host-level demand:
 *   privileged          `privileged: true`, `--privileged`
 *   host-path           a `hostPath:` volume
 *   host-namespace      hostNetwork / hostPID / hostIPC, compose `network_mode|pid|ipc: host`
 *   root-user           `runAsUser: 0`, compose `user: 0` / `root`, `--user 0`
 *   added-capability    compose `cap_add`, `--cap-add`, Docker `CapAdd`, a Pod spec's capabilities "add"
 *   docker-socket       the container engine's socket (root-equivalent on the host)
 *   unconfined          a seccomp or AppArmor profile switched off
 *   cluster-rbac        a ClusterRole / ClusterRoleBinding
 *   daemonset           a DaemonSet (one Pod on every node)
 *   db-superuser        a role statement granting SUPERUSER (NOSUPERUSER, or the keyword named
 *                       outside a role statement, is not a demand)
 *   db-createdb         CREATE DATABASE, or a role statement granting CREATEDB
 *   db-createrole       CREATE ROLE, or a role statement granting CREATEROLE
 *   db-extension        CREATE EXTENSION
 * Full-line comments are skipped: naming a demand in prose is not making it.
 *
 * THE LEDGER. scripts/host-demands.json lists each (file, demand) pair the tree makes today, keyed
 * on the pair — never a line number, never a count, so an edit inside a file that already makes the
 * demand does not move it — and each entry names the requirements-table row (`row`) that tells the
 * operator why and how to avoid it. The gate fails when
 *   1. a (file, demand) pair appears that no entry covers (a NEW demand: list it, with its reason,
 *      or don't make it),
 *   2. an entry matches nothing (the demand is gone: drop the entry, the ledger only shrinks), or
 *   3. an entry's row is not in the requirements table (the operator would not see it).
 *
 * WHAT IT DOES NOT SEE: an image whose Dockerfile sets no USER runs as root without any line here
 * saying so; scripts/image-user.test.mjs holds that half (ROOT_BY_DESIGN, each with a reason).
 * Egress is declared in the table and the no-egress sections, not scanned.
 */
import { readdirSync, readFileSync, existsSync, statSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const DEFAULT_ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
export const LEDGER_PATH = "scripts/host-demands.json";
export const TABLE_DOC = "docs/docs/deployment-kubernetes.mdx";

export const DEMANDS = {
  "privileged": /\bprivileged:\s*true\b|--privileged\b/,
  "host-path": /\bhostPath:/,
  "host-namespace": /\bhost(Network|PID|IPC):\s*(true\b|\{\{)|\b(network_mode|pid|ipc):\s*["']?host\b/,
  "root-user": /\brunAsUser:\s*0\b|^\s*user:\s*["']?(0(:0)?|root)["']?\s*$|--user[= ]["']?(0|root)\b/,
  "added-capability": /\bcap_add:|--cap-add\b|\bCapAdd\b|\[["']capabilities["']\]\[["']add["']\]/,
  "docker-socket": /docker\.sock\b/,
  "unconfined": /(seccomp|apparmor)[=:]\s*["']?unconfined/,
  "cluster-rbac": /\bkind:\s*ClusterRole(Binding)?\b/,
  "daemonset": /\bkind:\s*DaemonSet\b/,
  // A role attribute is a demand when a role statement grants it; the bare keyword is also how code
  // names the attributes it refuses or switches off (NOSUPERUSER, a pg_roles column → keyword table).
  "db-superuser": /\b(CREATE|ALTER)\s+(ROLE|USER)\b[^;]*?(?<![A-Za-z])SUPERUSER\b/,
  "db-createdb": /\bCREATE\s+DATABASE\b|\b(CREATE|ALTER)\s+(ROLE|USER)\b[^;]*?(?<![A-Za-z])CREATEDB\b/,
  "db-createrole": /\bCREATE\s+ROLE\b|\b(CREATE|ALTER)\s+(ROLE|USER)\b[^;]*?(?<![A-Za-z])CREATEROLE\b/,
  "db-extension": /\bCREATE\s+EXTENSION\b/,
};

/** Where an install's demands are written. Deploy surfaces: every file. core/: what the services run. */
const DEPLOY_SCOPES = ["deploy/helm/charts", "deploy/compose", "deploy/lite"];
const CODE_SCOPES = ["core"];
const CODE_FILE = /\.(py|sql|sh)$/;
const SKIP_DIR = new Set(["node_modules", "dist", "__pycache__", ".venv", "venv", "tests", "test", "eval", "fixtures", "testdata"]);
const SKIP_FILE = /(^test_.*|_test\.[a-z]+|\.test\.[a-z]+|\.md|\.mdx|\.json|\.lock)$/;

function walk(root, dir, acc) {
  const abs = join(root, dir);
  if (!existsSync(abs)) return acc;
  for (const name of readdirSync(abs)) {
    if (name.startsWith(".") || SKIP_DIR.has(name)) continue;
    const rel = `${dir}/${name}`;
    const st = statSync(join(root, rel));
    if (st.isDirectory()) walk(root, rel, acc);
    else if (!SKIP_FILE.test(name)) acc.push(rel);
  }
  return acc;
}

export function scanFiles(root = DEFAULT_ROOT) {
  const files = DEPLOY_SCOPES.flatMap((d) => walk(root, d, []));
  for (const d of CODE_SCOPES) files.push(...walk(root, d, []).filter((f) => CODE_FILE.test(f)));
  return files.sort();
}

/** Every (file, demand) the tree makes, with the lines that make it. */
export function demandSites(root = DEFAULT_ROOT) {
  const sites = new Map();
  for (const file of scanFiles(root)) {
    let text;
    try { text = readFileSync(join(root, file), "utf8"); } catch { continue; }
    text.split("\n").forEach((line, i) => {
      if (/^\s*(#|\/\/)/.test(line)) return;
      for (const [demand, re] of Object.entries(DEMANDS)) {
        if (!re.test(line)) continue;
        const key = `${file} :: ${demand}`;
        if (!sites.has(key)) sites.set(key, { file, demand, lines: [] });
        sites.get(key).lines.push(i + 1);
      }
    });
  }
  return sites;
}

export function loadLedger(root = DEFAULT_ROOT) {
  const p = join(root, LEDGER_PATH);
  if (!existsSync(p)) return { entries: [] };
  return JSON.parse(readFileSync(p, "utf8"));
}

/** The row ids of the requirements table: a table line whose first cell is a bare id like `F2`. */
export function tableRows(root = DEFAULT_ROOT) {
  const p = join(root, TABLE_DOC);
  if (!existsSync(p)) return new Set();
  return new Set([...readFileSync(p, "utf8").matchAll(/^\|\s*([A-Z]\d{1,2})\s*\|/gm)].map((m) => m[1]));
}

export function checkHostDemands(root = DEFAULT_ROOT) {
  const sites = demandSites(root);
  const ledger = loadLedger(root);
  const rows = tableRows(root);
  const errs = [];
  const keyed = new Map();
  for (const e of ledger.entries || []) {
    const key = `${e.file} :: ${e.demand}`;
    if (keyed.has(key)) errs.push(`${LEDGER_PATH}: ${key} is listed twice`);
    keyed.set(key, e);
    if (!DEMANDS[e.demand]) errs.push(`${LEDGER_PATH}: ${key} names an unknown demand (one of ${Object.keys(DEMANDS).join(", ")})`);
    if (!e.reason || e.reason.length < 20) errs.push(`${LEDGER_PATH}: ${key} needs a reason`);
    if (!rows.has(e.row)) errs.push(`${LEDGER_PATH}: ${key} points at row ${e.row || "(none)"}, which is not in the requirements table of ${TABLE_DOC}`);
  }
  for (const [key, s] of sites) {
    if (!keyed.has(key))
      errs.push(`${s.file}:${s.lines.join(",")} makes a new host demand (${s.demand}) that nothing lists. P24: make it unnecessary, or add a row to the requirements table in ${TABLE_DOC} saying why it is needed and how to avoid it, and a ${LEDGER_PATH} entry pointing at that row`);
  }
  for (const key of keyed.keys())
    if (!sites.has(key)) errs.push(`${LEDGER_PATH}: ${key} no longer matches anything — the demand is gone, drop the entry (and its table row if nothing else uses it)`);
  return { errs, sites: sites.size, files: scanFiles(root).length, rows: rows.size };
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  for (const s of demandSites().values()) console.log(`${s.file} :: ${s.demand}  (lines ${s.lines.join(",")})`);
}
