#!/usr/bin/env node
// Deterministic projections of architecture.calm.json (the SSOT chart). NEVER hand-edit the
// outputs — run `pnpm arch:dsl --write` after any chart change; gate:dataflow runs `--check`.
//
//   docs/views/architecture.dsl        compact text projection (the always-in-context LLM view)
//   docs/views/containers.mmd          C4-ish container view (systems, services, protocols)
//   docs/views/ownership.mmd           P23 data-carrier ownership (write=solid, read=dashed)
//   docs/views/flow-<id>.mmd           one sequence diagram per declared flow
//   docs/views/deployment.mmd          runtime spawn topology + the single public edge
//   docs/views/egress.mmd              tenant trust boundary; data-egress-controlled edges
import { readFileSync, writeFileSync, mkdirSync, existsSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const OUT = join(ROOT, "docs", "views");
const model = JSON.parse(readFileSync(join(ROOT, "architecture.calm.json"), "utf8"));
const nodes = model.nodes ?? [], rels = model.relationships ?? [], flows = model.flows ?? [];
const byId = new Map(nodes.map((n) => [n["unique-id"], n]));
const conns = rels.filter((r) => r["relationship-type"]?.connects).map((r) => ({
  id: r["unique-id"], desc: r.description ?? "", protocol: r.protocol ?? "",
  access: (r.metadata ?? []).find((m) => m.access)?.access ?? "",
  controls: r.controls ?? {},
  src: r["relationship-type"].connects.source.node,
  dst: r["relationship-type"].connects.destination.node,
}));
const composed = rels.filter((r) => r["relationship-type"]?.["composed-of"]).map((r) => r["relationship-type"]["composed-of"]);
const deployed = rels.filter((r) => r["relationship-type"]?.["deployed-in"]).map((r) => ({ id: r["unique-id"], ...r["relationship-type"]["deployed-in"] }));
const systemOf = new Map();
for (const c of composed) for (const m of c.nodes) systemOf.set(m, c.container);
const writersOf = (n) => (n.controls?.ownership?.requirements ?? []).flatMap((r) => r.config?.writers ?? []);
const isCarrier = (n) => Boolean(n.controls?.ownership) || n["node-type"] === "data-asset";
const q = (s) => `"${String(s).replaceAll('"', "'")}"`;
const mmId = (s) => s.replaceAll(/[^A-Za-z0-9_]/g, "_");

// ---------- architecture.dsl ----------
let dsl = "# GENERATED from architecture.calm.json — do not edit (pnpm arch:dsl --write)\n";
for (const sys of nodes.filter((n) => n["node-type"] === "system")) {
  dsl += `\nsystem ${sys["unique-id"]}  # ${sys.description ?? ""}\n`;
  for (const c of composed.filter((c) => c.container === sys["unique-id"]))
    for (const m of c.nodes.map((id) => byId.get(id)).filter(Boolean)) {
      const w = writersOf(m);
      dsl += `  ${m["node-type"]} ${m["unique-id"]}${w.length ? ` [writers: ${w.join(", ")}]` : ""}\n`;
    }
}
dsl += "\nedges:\n";
for (const c of conns) dsl += `  ${c.src} -${c.access || "req"}-> ${c.dst}${c.desc ? `  # ${c.desc}` : ""}\n`;
for (const d of deployed) dsl += `  ${d.nodes.join(", ")} deployed-in ${d.container}\n`;
dsl += "\nflows:\n";
for (const f of flows) {
  dsl += `  ${f["unique-id"]}: `;
  dsl += [...f.transitions].sort((a, b) => a["sequence-number"] - b["sequence-number"]).map((t) => t["relationship-unique-id"]).join(" -> ") + "\n";
}

// ---------- containers.mmd (services/clients/db, carriers collapsed) ----------
const boxTypes = new Set(["service", "webclient", "database"]);
let containers = "%% GENERATED — pnpm arch:dsl --write\nflowchart LR\n";
for (const sys of nodes.filter((n) => n["node-type"] === "system")) {
  const members = composed.filter((c) => c.container === sys["unique-id"]).flatMap((c) => c.nodes)
    .map((id) => byId.get(id)).filter((m) => m && boxTypes.has(m["node-type"]));
  if (!members.length) continue;
  containers += `  subgraph ${mmId(sys["unique-id"])}[${q(sys.name)}]\n`;
  for (const m of members) containers += `    ${mmId(m["unique-id"])}[${q(m.name)}]\n`;
  containers += "  end\n";
}
for (const n of nodes.filter((n) => boxTypes.has(n["node-type"]) && !systemOf.has(n["unique-id"])))
  containers += `  ${mmId(n["unique-id"])}[${q(n.name)}]\n`;
// service-to-service edges; edges into carriers collapse onto the carrier's system's redis? keep only node-to-node between boxTypes
for (const c of conns) {
  const s = byId.get(c.src), d = byId.get(c.dst);
  if (!s || !d || !boxTypes.has(s["node-type"]) || !boxTypes.has(d["node-type"])) continue;
  containers += `  ${mmId(c.src)} -->|${q(c.protocol || c.access || "")}| ${mmId(c.dst)}\n`;
}

// ---------- ownership.mmd ----------
let own = "%% GENERATED — pnpm arch:dsl --write\nflowchart LR\n";
const carriers = nodes.filter(isCarrier);
for (const n of carriers) {
  const multi = writersOf(n).length > 1;
  own += `  ${mmId(n["unique-id"])}[(${q(n.name)})]\n`;
  if (multi) own += `  style ${mmId(n["unique-id"])} stroke-dasharray: 5 5,stroke-width:3px\n`;
}
for (const c of conns) {
  const d = byId.get(c.dst);
  if (!d || !isCarrier(d)) continue;
  own += c.access === "write"
    ? `  ${mmId(c.src)} -->|write| ${mmId(c.dst)}\n`
    : `  ${mmId(c.src)} -.->|${c.access || "read"}| ${mmId(c.dst)}\n`;
}

// ---------- one sequence diagram per flow ----------
const flowFiles = {};
for (const f of flows) {
  let s = `%% GENERATED — pnpm arch:dsl --write\nsequenceDiagram\n  autonumber\n`;
  for (const t of [...f.transitions].sort((a, b) => a["sequence-number"] - b["sequence-number"])) {
    const r = rels.find((r) => r["unique-id"] === t["relationship-unique-id"]);
    const rt = r?.["relationship-type"] ?? {};
    const [a, b] = rt.connects ? [rt.connects.source.node, rt.connects.destination.node]
      : rt["deployed-in"] ? [rt["deployed-in"].container, rt["deployed-in"].nodes[0]] : [null, null];
    if (a && b) s += `  ${mmId(a)}->>${mmId(b)}: ${t.description ?? t["relationship-unique-id"]}\n`;
  }
  flowFiles[`flow-${f["unique-id"]}.mmd`] = s;
}

// ---------- deployment.mmd ----------
let dep = "%% GENERATED — pnpm arch:dsl --write\nflowchart TB\n";
for (const d of deployed) {
  dep += `  subgraph ${mmId(d.container)}_wl[${q(byId.get(d.container)?.name + " workloads")}]\n`;
  for (const n of d.nodes) dep += `    ${mmId(n)}[${q(byId.get(n)?.name ?? n)}]\n`;
  dep += "  end\n";
  for (const c of conns.filter((c) => c.dst === d.container))
    dep += `  ${mmId(c.src)} -->|${q(c.desc || "spawn")}| ${mmId(d.container)}_wl\n`;
}

// ---------- egress.mmd ----------
let eg = "%% GENERATED — pnpm arch:dsl --write\nflowchart LR\n  subgraph tenant[\"tenant boundary\"]\n";
const egressEdges = conns.filter((c) => c.controls["data-egress"]);
const outside = new Set(egressEdges.map((c) => c.dst));
for (const n of nodes.filter((n) => boxTypes.has(n["node-type"]) && !outside.has(n["unique-id"])))
  eg += `    ${mmId(n["unique-id"])}[${q(n.name)}]\n`;
eg += "  end\n";
for (const c of egressEdges) {
  const cfg = c.controls["data-egress"].requirements?.[0]?.config ?? {};
  eg += `  ${mmId(c.dst)}[${q(byId.get(c.dst)?.name ?? c.dst)}]\n`;
  eg += `  ${mmId(c.src)} ==>|${q(`${cfg.dataClass ?? "data"} -> ${cfg.destination ?? "?"}`)}| ${mmId(c.dst)}\n`;
}

const files = { "architecture.dsl": dsl, "containers.mmd": containers, "ownership.mmd": own, "deployment.mmd": dep, "egress.mmd": eg, ...flowFiles };
const mode = process.argv[2] ?? "--check";
if (mode === "--write") {
  mkdirSync(OUT, { recursive: true });
  for (const [f, body] of Object.entries(files)) writeFileSync(join(OUT, f), body);
  console.log(`arch-dsl: wrote ${Object.keys(files).length} views to docs/views/`);
} else {
  const stale = Object.entries(files).filter(([f, body]) =>
    !existsSync(join(OUT, f)) || readFileSync(join(OUT, f), "utf8") !== body).map(([f]) => f);
  if (stale.length) { console.error(`arch-dsl: stale views: ${stale.join(", ")} — run pnpm arch:dsl --write`); process.exit(1); }
  console.log("arch-dsl: views in sync");
}
