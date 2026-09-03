#!/usr/bin/env node
/**
 * The DOOR boundary checker — the HTTP twin of scripts/check-isolation-py.mjs (P9).
 *
 * THE DESIGN THIS ENFORCES is docs/adr/0037-the-target-architecture-one-edge-domains-over-identity
 * -and-runtime.md — accepted 2026-09-03, and written as "the reference the domain-doors gate (P9)
 * is written against". Read it before changing a rule here: the ADR is the decision, this file is
 * only its mechanism, and the distance between the two is the allowlist.
 *
 * `gate:graph` / `gate:isolation-py` gate IMPORTS. On this branch there are zero cross-domain
 * Python imports and yet every domain reaches its neighbours anyway, because the coupling is not an
 * import: it is a DOOR — an env-configured base URL plus an HTTP call. The import seam is
 * mechanically enforced; the door seam was enforced by nothing, so "identity is the only shared
 * dependency" was a sentence in a PRD rather than a property of the tree.
 *
 * THE RULE (ADR-0037 § The design; PRD decision 46 approving it; 40.7; the ruling that runtime is a
 * primitive):
 *
 *   A DOMAIN (core/identity · core/meetings · core/flows · core/agent) may name
 *     - its own door,
 *     - identity's door        (the one shared dependency),
 *     - runtime's door         (a primitive: *→runtime is allowed exactly like *→identity),
 *   and may name ANOTHER DOMAIN's door only when that door is
 *     - DECLARED OPTIONAL in the domain's own config contract — a `capability`-class key with a
 *       declared degrade, the #1453 `domain_present` pattern: unset means "that domain is not
 *       deployed" and the caller answers `not_present` instead of knocking, or
 *     - a PUBLISH edge — the domain declares `publishes_events` in its `mcp.tools.v1.json`
 *       manifest and the door is flows' event ingress (ADR-0037: "anything one domain wants
 *       another to react to is an event in flows").
 *   A domain may never name an EDGE (the gateway, the MCP service) or a CLIENT: fronting a
 *   sibling's door with the edge does not make it not-an-edge.
 *
 *   The EDGE (core/gateway, the MCP service) and the CLIENTS (clients/*) reach a domain only
 *   through a DECLARED ROUTE BINDING — a `routes.v1.json` or `mcp.tools.v1.json` that names the
 *   domain and the `base_url_env` being read. A client may always name the edge; that is what an
 *   edge is for.
 *
 * WHAT COUNTS AS A DOOR SITE: a production line in a unit's own source that NAMES another unit's
 * door. Two spellings, scanned in production source only (tests, evals and fixtures are out of
 * scope — they stand up doubles by design):
 *   1. a door NAME — an identifier or string literal ending in `_URL` / `_API` that carries
 *      another service's token (ADMIN_API, MEETING_API, AGENT_API, FLOWS_API, GATEWAY, MCP_URL,
 *      RUNTIME_API, UI_URL);
 *   2. a literal `http://<service>:` for a deployment service name.
 * Deliberately NOT keyed on `os.getenv(` alone. Every domain that has been carefully factored
 * routes its doors through a helper — `flows_steps.common._door("VEXA_FLOWS_GATEWAY_URL")` and a
 * `_DOORS` indirection map, `shared.config` constants — so an `os.getenv`-shaped scanner would see
 * the sloppiest code and miss the tidiest, which is the wrong way round. The declaration files
 * themselves are excluded (DECLARATION_FILES): declaring a door is not opening one.
 *
 * THE ALLOWLIST. `scripts/domain-doors.allow.json` carries the doors that are open on the line
 * TODAY, each keyed on (file, env-key) — never on a line number, which any edit above it moves —
 * and each naming the ruling that will close it. It exists so the
 * gate is green on the tip on the day it lands and RED the moment a new undeclared door appears —
 * and it is checked in BOTH directions, and by COUNT: an entry that no longer matches a real
 * violation is itself a failure, so the list cannot rot into a permanent exemption. Its length is the migration
 * backlog, and the only correct direction for it is shorter.
 */
import { readdirSync, readFileSync, writeFileSync, existsSync, statSync } from "node:fs";
import { join, dirname, relative } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const DEFAULT_ROOT = join(HERE, "..");
const SKIP_DIRS = new Set([
  "node_modules", "dist", ".turbo", "__pycache__", ".venv", ".next", "coverage",
  "test-results", "playwright-report", "tests", "__tests__", "eval", "evals", "fixtures",
]);
const skippable = (n) => n.startsWith(".") || SKIP_DIRS.has(n);
const isTestFile = (n) =>
  /^test_.*\.py$/.test(n) || /_test\.py$/.test(n) || /\.(test|spec)\.[cm]?[jt]sx?$/.test(n);
const SCANNED_EXT = /\.(py|ts|tsx|js|jsx|mjs|cjs)$/;

// ── the units that own source, longest prefix wins ───────────────────────────────────────────
// kind: "domain" (owns state) · "edge" (exposes domains, owns nothing) · "client" (renders)
//       · "primitive" (runtime: spawns what it is told)
export const UNITS = [
  // the MCP service lives under core/meetings today but is an EDGE (decision 40.5: one MCP server,
  // assembled at the gateway from domain-owned manifests, ADR-0037). Listed BEFORE core/meetings so the
  // longest-prefix rule classes it correctly.
  { unit: "mcp", kind: "edge", root: "core/meetings/services/mcp" },
  { unit: "identity", kind: "domain", root: "core/identity" },
  { unit: "meetings", kind: "domain", root: "core/meetings" },
  { unit: "flows", kind: "domain", root: "core/flows" },
  { unit: "agent", kind: "domain", root: "core/agent" },
  { unit: "gateway", kind: "edge", root: "core/gateway" },
  { unit: "runtime", kind: "primitive", root: "core/runtime" },
  { unit: "terminal", kind: "client", root: "clients/terminal" },
  { unit: "extension", kind: "client", root: "clients/extension" },
  { unit: "slim", kind: "client", root: "clients/slim" },
];

// ── who owns a door ──────────────────────────────────────────────────────────────────────────
// Matched on the KEY NAME by service token, not on an exhaustive key list, so a new spelling of an
// existing service ("VEXA_MEETINGS_API_URL") is caught by the same row. Order matters: the first
// match wins, and the compound names (VEXA_FLOWS_ADMIN_API_URL) resolve to the service they NAME,
// never to the service that reads them.
export const DOOR_TOKENS = [
  [/(^|_)ADMIN_API(_|$)|(^|_)IDENTITY_API(_|$)/, "identity"],
  [/(^|_)MEETING_API(_|$)|(^|_)MEETINGS_API(_|$)|(^|_)TRANSCRIPTION_SERVICE(_|$)/, "meetings"],
  [/(^|_)AGENT_API(_|$)|(^|_)AGENTS_API(_|$)/, "agent"],
  [/(^|_)FLOWS_API(_|$)/, "flows"],
  [/(^|_)RUNTIME_API(_|$)/, "runtime"],
  [/(^|_)MCP(_|$)/, "mcp"],
  [/(^|_)GATEWAY(_|$)/, "gateway"],
  [/(^|_)UI_URL$|(^|_)TERMINAL_URL$/, "terminal"],
];
// Keys whose NAME does not carry the token of the service they actually address. Kept short and
// explicit: an entry here is a naming defect being tolerated, not a category.
export const DOOR_ALIASES = {
  VEXA_API_URL: "gateway",            // compose: VEXA_API_URL=http://gateway:8000
  VEXA_PUBLIC_API_URL: "gateway",     // the same door, as a browser sees it
  NEXT_PUBLIC_API_URL: "gateway",
};
// A door key must end in _URL or _API — a _KEY / _SECRET / _PORT carrying a service token is a
// credential or a dial, not a door.
const DOOR_KEY_SUFFIX = /(_URL|_API)$/;

// ── literal `http://<service>:` ──────────────────────────────────────────────────────────────
export const LITERAL_SERVICES = {
  "admin-api": "identity",
  "meeting-api": "meetings",
  "transcription-collector": "meetings",
  "agent-api": "agent",
  "agent-worker": "agent",
  "flows-api": "flows",
  "runtime": "runtime",
  gateway: "gateway",
  mcp: "mcp",
  terminal: "terminal",
};
const LITERAL_RE = new RegExp(
  `https?://(${Object.keys(LITERAL_SERVICES).map((s) => s.replace(/[-]/g, "\\-")).join("|")}):`,
  "g",
);

// A door NAME where it BINDS: a SCREAMING_SNAKE string literal (`os.getenv("X")`, `_door("X")`, a
// `_DOORS` map value, `flows_config.get("X")` — one rule covers every indirection any of them
// invents) and TS's dotted `process.env.X`. A bare identifier is NOT a binding: `AGENT_API` used
// nineteen times in core/flows is one door read once, and reporting nineteen sites would put
// eighteen lines of noise in front of the one that has to change. Quotes must hug the key, so a
// docstring mentioning MEETING_API_URL in prose is not a door.
const DOOR_NAME_RES = [
  /["']([A-Z][A-Z0-9_]{2,})["']/g,
  /\bprocess\.env\.([A-Z][A-Z0-9_]{2,})\b/g,
];
// pydantic-settings declares its doors as TYPED LOWERCASE FIELDS and derives the env name from an
// `env_prefix` — `meeting_api_url: str = "http://meeting-api:8080"` IS the VEXA_MEETING_API_URL
// door, and core/agent opens three of its four this way. A scanner that reads only string literals
// sees the one door that happens to carry a literal default and none of the others.
// Only inside a CLASS BODY: `def build_production_app(*, meeting_api_url: Optional[str] = None)`
// is a parameter, not a door — the gateway naming its own forwarding parameter is not the gateway
// opening a door, and five of the first run's thirty-six findings were exactly that.
const DOOR_FIELD_RE = /^ {4}([a-z][a-z0-9_]*_(?:url|api))\s*:\s*[A-Za-z[]/;
const CLASS_OPEN_RE = /^class\s+\w/;
const TOP_LEVEL_RE = /^\S/;

// The files that DECLARE doors rather than open them: a contract naming every key it may read is
// the fix for this gate's findings, not an instance of them.
const DECLARATION_FILES = new Set(["core/flows/src/flows_config.py"]);

export const doorOwner = (key) => {
  if (DOOR_ALIASES[key]) return DOOR_ALIASES[key];
  if (!DOOR_KEY_SUFFIX.test(key)) return null;
  for (const [re, owner] of DOOR_TOKENS) if (re.test(key)) return owner;
  return null;
};

const unitOf = (relPath) => {
  let best = null;
  for (const u of UNITS) if (relPath === u.root || relPath.startsWith(u.root + "/")) {
    if (!best || u.root.length > best.root.length) best = u;
  }
  return best;
};

function walkFiles(dir, root, acc) {
  let names;
  try { names = readdirSync(dir); } catch { return acc; }
  for (const name of names) {
    if (skippable(name)) continue;
    const p = join(dir, name);
    let s; try { s = statSync(p); } catch { continue; }
    if (s.isDirectory()) { walkFiles(p, root, acc); continue; }
    if (!SCANNED_EXT.test(name) || isTestFile(name)) continue;
    acc.push(p);
  }
  return acc;
}

/** Every door site in production source, with the unit that reads it and the unit that owns it. */
export function doorSites(root = DEFAULT_ROOT) {
  const sites = [];
  for (const u of UNITS) {
    const abs = join(root, u.root);
    if (!existsSync(abs)) continue;
    for (const file of walkFiles(abs, root, [])) {
      const relPath = relative(root, file).replace(/\\/g, "/");
      if (unitOf(relPath)?.unit !== u.unit) continue;   // owned by a longer-prefix unit
      if (DECLARATION_FILES.has(relPath)) continue;
      const text = readFileSync(file, "utf8");
      const lines = text.split("\n");
      const seen = new Set();
      let inClass = false;
      for (let i = 0; i < lines.length; i++) {
        const line = lines[i];
        for (const re of DOOR_NAME_RES) {
          re.lastIndex = 0;
          for (const m of line.matchAll(re)) {
            const owner = doorOwner(m[1]);
            if (!owner) continue;
            const id = `${i + 1}:${owner}:${m[1]}`;
            if (seen.has(id)) continue;
            seen.add(id);
            sites.push({ site: `${relPath}:${i + 1}`, path: relPath, line: i + 1, from: u.unit, kind: u.kind, to: owner, door: m[1], how: "name" });
          }
        }
        if (TOP_LEVEL_RE.test(line)) inClass = CLASS_OPEN_RE.test(line);
        const field = inClass && line.match(DOOR_FIELD_RE);
        if (field) {
          const key = field[1].toUpperCase();
          const owner = doorOwner(key);
          const id = `${i + 1}:${owner}:${key}`;
          if (owner && !seen.has(id)) {
            seen.add(id);
            sites.push({ site: `${relPath}:${i + 1}`, path: relPath, line: i + 1, from: u.unit, kind: u.kind, to: owner, door: key, how: "field" });
          }
        }
        LITERAL_RE.lastIndex = 0;
        for (const m of line.matchAll(LITERAL_RE)) {
          const owner = LITERAL_SERVICES[m[1]];
          const id = `${i + 1}:${owner}:http`;
          if (seen.has(id)) continue;
          seen.add(id);
          sites.push({ site: `${relPath}:${i + 1}`, path: relPath, line: i + 1, from: u.unit, kind: u.kind, to: owner, door: `http://${m[1]}:`, how: "literal" });
        }
      }
    }
  }
  return sites.sort((a, b) => a.site.localeCompare(b.site));
}

// ── declarations ─────────────────────────────────────────────────────────────────────────────
// Where each domain declares the doors it may open. Two shapes, both read: the sealed
// `config.v1.json` contract, and core/flows' in-code declaration table (flows_config.DECLARED),
// which is the same three classes and is the input to its config.v1 adoption (seam backlog B7/B9).
export const CONTRACTS = {
  identity: { form: "config.v1", path: "core/identity/services/admin-api/src/admin_api/config.v1.json" },
  meetings: { form: "config.v1", path: "core/meetings/services/meeting-api/src/meeting_api/config.v1.json" },
  agent: { form: "config.v1", path: "core/agent/control_plane/config.v1.json" },
  flows: { form: "flows-table", path: "core/flows/src/flows_config.py" },
  gateway: { form: "config.v1", path: "core/gateway/services/gateway/src/gateway/config.v1.json" },
  mcp: { form: "config.v1", path: "core/meetings/services/mcp/src/vexa_mcp/config.v1.json" },
};

/** key -> class, for one unit's contract. Unreadable/absent contract ⇒ empty (everything undeclared). */
export function declaredKeys(unit, root = DEFAULT_ROOT) {
  const c = CONTRACTS[unit];
  const out = new Map();
  if (!c) return out;
  const p = join(root, c.path);
  if (!existsSync(p)) return out;
  const text = readFileSync(p, "utf8");
  if (c.form === "config.v1") {
    let decl; try { decl = JSON.parse(text); } catch { return out; }
    const caps = decl.capabilities || {};
    for (const k of decl.keys || []) {
      const degrade = k.class === "capability" && !!(caps[k.capability] || {}).when_unconfigured;
      out.set(k.key, { cls: k.class, degrade });
    }
    return out;
  }
  // flows' table: `"KEY": ("class", default, "why")` — the class is the first tuple element.
  for (const m of text.matchAll(/"([A-Z][A-Z0-9_]*)"\s*:\s*\(\s*"?([a-z-]+)?"?/g)) {
    if (!m[2]) continue;
    out.set(m[1], { cls: m[2], degrade: m[2] === "capability" });
  }
  return out;
}

/** The route bindings the edge may forward through: domain -> set of declared base_url_env keys. */
export function routeBindings(root = DEFAULT_ROOT) {
  const byDomain = new Map();
  const add = (domain, env, where) => {
    if (!domain) return;
    if (!byDomain.has(domain)) byDomain.set(domain, { envs: new Set(), where: [] });
    if (env) byDomain.get(domain).envs.add(env);
    byDomain.get(domain).where.push(where);
  };
  const walk = (dir) => {
    let names; try { names = readdirSync(dir); } catch { return; }
    for (const name of names) {
      if (skippable(name)) continue;
      const p = join(dir, name);
      let s; try { s = statSync(p); } catch { continue; }
      if (s.isDirectory()) { walk(p); continue; }
      if (name !== "mcp.tools.v1.json" && name !== "routes.v1.json") continue;
      let doc; try { doc = JSON.parse(readFileSync(p, "utf8")); } catch { continue; }
      const where = relative(root, p).replace(/\\/g, "/");
      add(doc.domain, doc.base_url_env, where);
      for (const b of doc.bindings || []) add(b.domain, b.base_url_env, where);
    }
  };
  walk(join(root, "core"));
  return byDomain;
}

/** Domains that declare a publish edge into flows' event ingress (`publishes_events`). */
export function publishers(root = DEFAULT_ROOT) {
  const out = new Set();
  for (const [domain, v] of routeBindings(root)) {
    for (const w of v.where) {
      let doc; try { doc = JSON.parse(readFileSync(join(root, w), "utf8")); } catch { continue; }
      if (Array.isArray(doc.publishes_events) && doc.publishes_events.length) out.add(domain);
    }
  }
  return out;
}

// ── the rule ─────────────────────────────────────────────────────────────────────────────────
const KINDS = Object.fromEntries(UNITS.map((u) => [u.unit, u.kind]));

/** null when the site is allowed; otherwise the reason it is not, and what would allow it. */
export function judge(site, ctx) {
  const { from, to, kind, door } = site;
  if (from === to) return null;
  if (to === "runtime") return null;                          // the primitive: *→runtime, like *→identity

  if (kind === "domain") {
    // "Identity is the only shared dependency" is a rule about DOMAINS. A client that reaches
    // admin-api itself is not exercising that rule, it is bypassing the edge — which is the same
    // failure as reaching any other domain and is judged as one, below.
    if (to === "identity") return null;
    if (KINDS[to] === "edge")
      return `a domain may not reach ${to === "gateway" ? "the gateway" : "the MCP edge"} — fronting a sibling's door with the edge does not make it not-an-edge; declare the sibling domain optional in ${ctx.contractPath(from)} (capability class + degrade) or publish an event into flows`;
    if (KINDS[to] === "client")
      return `a domain may not name a client's URL (${to}) — the edge mints what a client sees`;
    if (KINDS[to] !== "domain")
      return `unknown door owner '${to}'`;
    const decl = ctx.declared(from).get(door);
    if (decl && decl.cls === "capability" && decl.degrade) return null;   // #1453 domain_present
    // A PUBLISH IS NOT A DEPENDENCY (ADR-0037: "an event published into flows is never a
    // dependency"; PRD 42.2). A dependency is a call whose answer the caller needs; a publish is a
    // fact handed over best-effort and swallowed, so an absent target changes nothing but where the
    // fact lands. Two spellings, both accepted: `publish-edge` on the KEY in the service's config.v1
    // (#1476 — the class exists precisely so a publish target has a declaration that does not mean
    // "this service needs this value", and gate:config-contract checks its `publishes_events`
    // carriers against core/flows/contracts/flows.v1/carriers.json), and `publishes_events` on the
    // DOMAIN's mcp.tools.v1 manifest. Without this, the sanctioned way for two domains to couple
    // would fail the very gate that exists to keep them apart.
    if (decl && decl.cls === "publish-edge") return null;
    if (to === "flows" && ctx.publishes.has(from)) return null;
    if (decl) return `${door} is declared '${decl.cls}' in ${ctx.contractPath(from)} — a cross-domain door must be class 'capability' with a declared degrade (the #1453 domain_present pattern), or 'publish-edge' if this service only HANDS FACTS to ${to} and needs no answer, so an absent ${to} domain answers not_present instead of being knocked on`;
    return `undeclared cross-domain door to ${to} — declare ${door} in ${ctx.contractPath(from)} as class 'capability' with a degrade (a dependency), or class 'publish-edge' with its carriers (a fact handed over, needing no answer), or publish an event into flows (publishes_events in ${from}/mcp.tools.v1.json)`;
  }

  if (kind === "edge") {
    if (KINDS[to] === "edge" || KINDS[to] === "client") return null;      // edge↔edge, edge→client mint
    const binding = ctx.bindings.get(to);
    if (binding && binding.envs.has(door)) return null;
    return `the edge forwards to ${to} without a declared route binding — add ${to}'s routes.v1.json / mcp.tools.v1.json declaring base_url_env "${door}" (ADR-0037: the edge assembles routes.v1 and mcp.tools.v1 from the domains present)`;
  }

  if (kind === "client") {
    if (KINDS[to] === "edge") return null;                               // a client's one legal door
    return `a client reaches ${to} directly, bypassing the edge — route it through the gateway`;
  }

  if (kind === "primitive") {
    return `the runtime is a primitive (spawn only) and may not name ${to}'s door`;
  }
  return null;
}

// ── the allowlist ────────────────────────────────────────────────────────────────────────────
export const ALLOW_PATH = "scripts/domain-doors.allow.json";

// THE KEY IS `path` + `door`, AND THE LINE IS INFORMATIONAL. Keying on `path:line` was wrong in a
// way only the merge queue could show: PR #1473 inserted four lines above `core/agent/worker/
// engine.py:1105`, and the gate then told the merger that a door nobody had touched was an
// undeclared violation AND that its allowlist entry was stale — two false accusations from one
// unrelated edit. A line number is not an identity; it is a coordinate that any edit above it
// moves. The pair (file, env-key) is the door.
//
// Multiplicity is still checked, so re-keying costs nothing in strictness: N entries for a
// (path, door) cover exactly N violations. A NEW door of the same name in the same file makes the
// count exceed the entries and turns the gate red; a REMOVED one leaves a surplus entry and turns
// it red too. So the list still cannot rot — it just no longer reacts to whitespace.
const allowKey = (e) => `${e.path}|${e.door}`;

export function loadAllowlist(root = DEFAULT_ROOT) {
  const p = join(root, ALLOW_PATH);
  if (!existsSync(p)) return { entries: [] };
  return JSON.parse(readFileSync(p, "utf8"));
}

/**
 * The whole gate, as data. Returns { violations, stale, sites, allowed } where
 *   violations = doors the rule refuses that the allowlist does not carry (RED),
 *   stale      = allowlist entries that match no current violation (RED — the list cannot rot).
 */
export function checkDomainDoors(root = DEFAULT_ROOT) {
  const sites = doorSites(root);
  const declCache = new Map();
  const ctx = {
    declared: (u) => { if (!declCache.has(u)) declCache.set(u, declaredKeys(u, root)); return declCache.get(u); },
    contractPath: (u) => CONTRACTS[u]?.path || `${u}'s config contract`,
    bindings: routeBindings(root),
    publishes: publishers(root),
  };
  const allow = loadAllowlist(root);
  const allowCount = new Map();      // path|door -> how many violations this list excuses
  for (const e of allow.entries || []) allowCount.set(allowKey(e), (allowCount.get(allowKey(e)) || 0) + 1);

  // One LINE reaching one domain is ONE door, however many names it spells it with:
  // `AGENT_API = flows_config.get("VEXA_FLOWS_AGENT_API_URL")` names the constant and the declared
  // key on the same line, and the declared one is the truth about it. Group by site+target and let
  // any allowed spelling settle the group — otherwise the gate reports a domain as undeclared on
  // the exact line where it declares it.
  const groups = new Map();
  for (const s of sites) {
    const g = `${s.site}|${s.to}`;
    if (!groups.has(g)) groups.set(g, []);
    groups.get(g).push({ ...s, why: judge(s, ctx) });
  }
  const refused = [];
  for (const members of groups.values()) {
    if (members.some((m) => !m.why)) continue;                       // the rule allows this door
    refused.push(members[0]);
  }
  // Spend the allowance per (path, door): the first N refusals of a pair are excused by its N
  // entries, any beyond that are violations, and any entries left unspent are stale.
  const budget = new Map(allowCount);
  const violations = [];
  let allowlisted = 0;
  for (const r of refused.sort((a, b) => a.site.localeCompare(b.site))) {
    const k = `${r.path}|${r.door}`;
    const left = budget.get(k) || 0;
    if (left > 0) { budget.set(k, left - 1); allowlisted++; continue; }
    violations.push(r);
  }
  const stale = [];
  for (const e of allow.entries || []) {
    const k = allowKey(e);
    const left = budget.get(k) || 0;
    if (left > 0) { budget.set(k, left - 1); stale.push(e); }
  }
  return { sites, violations, stale, allowlisted, allow, refused };
}

/**
 * Rewrite the allowlist's INFORMATIONAL `line` fields to where each excused door sits today.
 * Never changes which doors are excused — matching is on (path, door) and this touches neither.
 * It exists so the file stays readable as the tree moves, without a line number ever being able to
 * fail a gate again.
 */
export function refreshAllowlist(root = DEFAULT_ROOT) {
  const { refused, allow } = checkDomainDoors(root);
  const byPair = new Map();
  for (const r of refused) {
    const k = `${r.path}|${r.door}`;
    if (!byPair.has(k)) byPair.set(k, []);
    byPair.get(k).push(r.line);
  }
  for (const v of byPair.values()) v.sort((a, b) => a - b);
  const taken = new Map();
  let changed = 0;
  for (const e of allow.entries || []) {
    const k = `${e.path}|${e.door}`;
    const n = taken.get(k) || 0;
    const line = (byPair.get(k) || [])[n];
    taken.set(k, n + 1);
    if (line && line !== e.line) { e.line = line; changed++; }
  }
  writeFileSync(join(root, ALLOW_PATH), JSON.stringify(allow, null, 2) + "\n");
  return changed;
}

// CLI: `node scripts/check-domain-doors.mjs [--json|--sites|--refresh]`
if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  if (process.argv.includes("--refresh")) {
    const n = refreshAllowlist();
    console.log(`refreshed ${n} informational line number(s) in ${ALLOW_PATH}`);
    process.exit(0);
  }
  const res = checkDomainDoors();
  if (process.argv.includes("--sites")) {
    for (const s of res.sites) console.log(`${s.from} → ${s.to}  ${s.door}  ${s.site}  [${s.how}]`);
  } else if (process.argv.includes("--json")) {
    console.log(JSON.stringify(res, null, 2));
  } else {
    for (const v of res.violations) console.log(`✗ ${v.site} — ${v.from} → ${v.to} (${v.door}): ${v.why}`);
    for (const e of res.stale) console.log(`✗ STALE allowlist entry ${e.site} (${e.door})`);
    console.log(`${res.sites.length} door sites · ${res.violations.length} violations · ${res.allowlisted} allowlisted · ${res.stale.length} stale`);
  }
  process.exit(res.violations.length || res.stale.length ? 1 : 0);
}
