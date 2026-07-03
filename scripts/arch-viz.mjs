#!/usr/bin/env node
/**
 * arch-viz.mjs — a DETERMINISTIC rendering engine for architecture.calm.json. A view spec (selector +
 * level-of-detail + scale + annotations) maps to a stable SVG; same model + same spec => byte-identical
 * output (no Date/random, every list sorted by unique-id). You look at the architecture one cluster or
 * one path at a time, at the scale/detail you choose — never the whole graph at once.
 *
 *   node scripts/arch-viz.mjs                          # list selectors
 *   node scripts/arch-viz.mjs cluster:meetings         # a concern bundle + the carriers its members touch
 *   node scripts/arch-viz.mjs cluster:terminal --lod=2 # terminal internals, more detail
 *   node scripts/arch-viz.mjs flow:transcript-flow     # a data path, contract on each hop
 *   node scripts/arch-viz.mjs path:tc-stream           # a carrier: its writers + readers
 *   node scripts/arch-viz.mjs type:service             # every node of a kind
 *   node scripts/arch-viz.mjs all                       # regenerate every cluster + flow view
 *
 * Flags: --lod=0..3 (detail)  --scale=0.6..2  --no-contracts  --no-owners
 */
import { readFileSync, writeFileSync, mkdirSync, existsSync } from "node:fs";
import { join } from "node:path";

const ROOT = process.cwd();
const M = JSON.parse(readFileSync(join(ROOT, "architecture.calm.json"), "utf8"));
const nodes = [...(M.nodes || [])].sort((a, b) => a["unique-id"].localeCompare(b["unique-id"]));
const rels = M.relationships || [], flows = M.flows || [];
const byId = new Map(nodes.map((n) => [n["unique-id"], n]));
const relById = new Map(rels.map((r) => [r["unique-id"], r]));
const composed = rels.filter((r) => r["relationship-type"]?.["composed-of"]).map((r) => r["relationship-type"]["composed-of"]);
const connects = rels.filter((r) => r["relationship-type"]?.connects).map((r) => ({
  src: r["relationship-type"].connects.source.node, dst: r["relationship-type"].connects.destination.node,
  write: (r.metadata || []).some((m) => m.access === "write"), contract: (r.metadata || []).find((m) => m.contract)?.contract,
}));

// ── spec ──────────────────────────────────────────────────────────────────────
const args = process.argv.slice(2);
const view = args.find((a) => !a.startsWith("--"));
const flag = (k, d) => { const a = args.find((x) => x.startsWith(`--${k}=`)); return a ? a.split("=")[1] : d; };
const has = (k) => args.includes(`--${k}`);
const LOD = Math.max(0, Math.min(3, parseInt(flag("lod", "3"), 10)));
const S = Math.max(0.5, Math.min(2.5, parseFloat(flag("scale", "1"))));
const showContracts = !has("no-contracts");
const showOwners = !has("no-owners");

const TIER = { webclient: 0, system: 0, service: 1, module: 2, "data-asset": 3, database: 3, contract: 4 };
// LOD gate: which node-types are visible at a given detail level
const visibleAt = (t) => (t === "service" || t === "system" || t === "webclient") ? LOD >= 1
  : t === "module" ? LOD >= 2 : t === "contract" ? LOD >= 3 : true; // carriers always
const isCarrier = (id) => ["data-asset", "database"].includes(byId.get(id)?.["node-type"]);
const ownerOf = (id) => (byId.get(id)?.controls?.ownership?.requirements?.[0]?.config?.writers || []).join("+");
const contractOf = (id) => (byId.get(id)?.metadata || []).find((m) => m.contract)?.contract;
const esc = (s) => String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

// ── svg primitives (scale-aware) ───────────────────────────────────────────────
const px = (n) => Math.round(n * S);
const F = { title: px(12.5), sub: px(10), pill: px(10), head: px(11) };
function box(x, y, w, h, o = {}) {
  return `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${px(o.rx ?? 6)}" fill="${o.accent ? "#1d4ed8" : "currentColor"}" fill-opacity="${o.accent ? 0.07 : (o.fo ?? 0.05)}" stroke="${o.accent ? "#1d4ed8" : "currentColor"}" stroke-opacity="${o.accent ? 0.9 : 0.32}" stroke-width="${o.accent ? 1.5 : 1}"${o.dash ? ' stroke-dasharray="2 2"' : ""}/>`;
}
const text = (x, y, s, o = {}) => `<text x="${x}" y="${y}"${o.anchor ? ` text-anchor="${o.anchor}"` : ""} font-size="${o.size ?? F.title}" fill="${o.fill || "currentColor"}"${o.fo ? ` fill-opacity="${o.fo}"` : ""}>${esc(s)}</text>`;
function pill(x, y, s, o = {}) {
  const w = px(Math.max(54, s.length * 6 + 16)), h = px(20);
  return box(x, y, w, h, { rx: 10, fo: 0.06, accent: o.accent }) + text(x + w / 2, y + px(14), s, { anchor: "middle", size: F.pill, fo: o.fo });
}
const edge = (x1, y1, x2, y2, o = {}) => `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${o.color || "currentColor"}" stroke-opacity="${o.write ? 0.5 : 0.22}"${o.write ? "" : ' stroke-dasharray="4 4"'} marker-end="url(#a)"/>`;
const defs = `<defs><marker id="a" markerWidth="9" markerHeight="9" refX="7" refY="3" orient="auto"><path d="M0,0 L7,3 L0,6 Z" fill="currentColor" fill-opacity="0.6"/></marker></defs>`;
const svg = (W, H, body, title) => `<svg viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg" role="img" font-family="Anthropic Sans, system-ui, sans-serif"><title>${esc(title)}</title><desc>Deterministic render of architecture.calm.json — ${esc(title)} (lod ${LOD}, scale ${S}).</desc>${defs}\n${body}\n</svg>\n`;

function out(name, content) {
  if (!existsSync(join(ROOT, "docs"))) mkdirSync(join(ROOT, "docs"));
  if (!existsSync(join(ROOT, "docs", "views"))) mkdirSync(join(ROOT, "docs", "views"));
  writeFileSync(join(ROOT, "docs", "views", name + ".svg"), content);
  console.log(`  wrote docs/views/${name}.svg  (lod ${LOD}, scale ${S})`);
}

// ── CLUSTER: a composed-of container + members (LOD-filtered) + the carriers they touch ────────────
function cluster(id) {
  const c = composed.find((x) => x.container === id);
  if (!c) return console.error(`no cluster '${id}'`);
  let members = c.nodes.filter((m) => byId.has(m) && visibleAt(byId.get(m)["node-type"]));
  members.sort((a, b) => (TIER[byId.get(a)["node-type"]] - TIER[byId.get(b)["node-type"]]) || a.localeCompare(b));
  const mset = new Set([id, ...members]);
  const cedges = connects.filter((e) => mset.has(e.src) && isCarrier(e.dst));
  const carriers = [...new Set(cedges.map((e) => e.dst))].sort();
  const cols = Math.min(4, Math.max(1, Math.ceil(Math.sqrt(members.length || 1))));
  const cw = px(150), ch = px(34), gp = px(10), pad = px(16);
  const rows = Math.ceil(members.length / cols) || 1;
  const W = Math.max(pad * 2 + cols * (cw + gp), px(520));
  const bY = px(46), bH = px(34) + rows * (ch + gp), center = new Map(), b = [];
  b.push(text(px(16), px(22), `cluster: ${byId.get(id)?.name || id}  ·  lod ${LOD}`, { size: F.head }));
  b.push(text(px(16), px(38), byId.get(id)?.description || "", { size: F.sub, fo: 0.6 }));
  b.push(box(px(12), bY, W - px(24), bH, { rx: 8, fo: 0.03 }));
  members.forEach((m, i) => {
    const x = px(12) + pad + (i % cols) * (cw + gp), y = bY + px(28) + Math.floor(i / cols) * (ch + gp);
    b.push(box(x, y, cw, ch));
    b.push(text(x + cw / 2, y + px(21), byId.get(m)?.name || m, { anchor: "middle", size: F.sub }));
    center.set(m, { x: x + cw / 2, y: y + ch });
  });
  center.set(id, { x: W / 2, y: bY + bH });
  const cY = bY + bH + px(44);
  const dw = Math.min(px(178), (W - px(24)) / Math.max(1, carriers.length) - px(10));
  if (carriers.length) b.push(text(px(16), cY - px(8), "data carriers", { size: F.sub, fo: 0.6 }));
  carriers.forEach((cr, i) => {
    const x = px(12) + i * (dw + px(10)), y = cY, ct = contractOf(cr);
    b.push(box(x, y, dw, px(48), { dash: true, fo: 0.03, accent: cr === "proc-stream" }));
    b.push(text(x + dw / 2, y + px(18), byId.get(cr)?.name || cr, { anchor: "middle", size: F.sub }));
    if (showOwners) b.push(text(x + dw / 2, y + px(31), "writer: " + (ownerOf(cr) || "—"), { anchor: "middle", size: px(9), fo: 0.55 }));
    if (showContracts && ct) b.push(text(x + dw / 2, y + px(43), ct, { anchor: "middle", size: px(9), fill: "#1d4ed8" }));
    center.set(cr, { x: x + dw / 2, y });
  });
  for (const e of cedges) { const a = center.get(e.src), z = center.get(e.dst); if (a && z) b.unshift(edge(a.x, a.y, z.x, z.y, { write: e.write })); }
  out(`cluster-${id}-lod${LOD}`, svg(W, cY + px(48) + px(16), b.join("\n"), `cluster ${id}`));
}

// ── FLOW: an ordered path; each carrier hop labelled with its contract ──────────────────────────────
function flow(id) {
  const f = flows.find((x) => x["unique-id"] === id);
  if (!f) return console.error(`no flow '${id}'`);
  const steps = [...f.transitions].sort((a, b) => a["sequence-number"] - b["sequence-number"]);
  const order = [];
  for (const t of steps) { const c = relById.get(t["relationship-unique-id"])?.["relationship-type"]?.connects; if (!c) continue; for (const n of [c.source.node, c.destination.node]) if (!order.includes(n)) order.push(n); }
  const bw = px(120), gp = px(10), pad = px(14), H = px(210), W = pad * 2 + order.length * (bw + gp) - gp;
  const center = new Map(), b = [text(px(14), px(20), `flow: ${f.name}  ·  lod ${LOD}`, { size: F.head })];
  order.forEach((n, i) => {
    const x = pad + i * (bw + gp), y = px(64), carrier = isCarrier(n), ct = contractOf(n);
    b.push(box(x, y, bw, px(60), { dash: carrier, accent: n === "proc-stream", fo: carrier ? 0.03 : 0.05 }));
    b.push(text(x + bw / 2, y + px(24), byId.get(n)?.name || n, { anchor: "middle", size: F.sub }));
    if (showOwners && carrier) b.push(text(x + bw / 2, y + px(40), "writer: " + (ownerOf(n) || "—"), { anchor: "middle", size: px(9), fo: 0.55 }));
    if (showContracts && carrier && ct) b.push(text(x + bw / 2, y + px(54), ct, { anchor: "middle", size: px(9), fill: "#1d4ed8" }));
    center.set(n, { x: x + bw / 2, top: y, bottom: y + px(60) });
  });
  steps.forEach((t) => {
    const c = relById.get(t["relationship-unique-id"])?.["relationship-type"]?.connects; if (!c) return;
    const a = center.get(c.source.node), z = center.get(c.destination.node); if (!a || !z) return;
    const w = (relById.get(t["relationship-unique-id"]).metadata || []).some((m) => m.access === "write");
    const my = px(150) + (t["sequence-number"] % 2) * px(16);
    b.push(`<path d="M${a.x},${a.bottom} L${a.x},${my} L${z.x},${my} L${z.x},${z.bottom}" fill="none" stroke="currentColor" stroke-opacity="${w ? 0.5 : 0.25}"${w ? "" : ' stroke-dasharray="4 4"'} marker-end="url(#a)"/>`);
    b.push(text((a.x + z.x) / 2, my - px(3), String(t["sequence-number"]), { anchor: "middle", size: px(9), fo: 0.6 }));
  });
  out(`flow-${id}`, svg(W, H, b.join("\n"), `flow ${id}`));
}

// ── PATH (carrier-centric): one carrier + its writers and readers, with the contract ───────────────
function carrierPath(id) {
  if (!isCarrier(id)) return console.error(`'${id}' is not a data carrier`);
  const writers = connects.filter((e) => e.dst === id && e.write).map((e) => e.src).sort();
  const readers = connects.filter((e) => e.dst === id && !e.write).map((e) => e.src).sort();
  const colW = px(190), bh = px(40), gp = px(12), pad = px(16);
  const rowsN = Math.max(writers.length, readers.length, 1);
  const W = pad * 2 + colW * 3 + px(80), H = px(70) + rowsN * (bh + gp) + px(20);
  const b = [text(px(16), px(24), `carrier: ${byId.get(id)?.name || id}  ·  writer ${ownerOf(id) || "—"}${contractOf(id) ? "  ·  " + contractOf(id) : ""}`, { size: F.head })];
  const colX = (i) => pad + i * (colW + px(40));
  const place = (list, ci, label) => { b.push(text(colX(ci), px(54), label, { size: F.sub, fo: 0.6 })); return list.map((n, i) => { const x = colX(ci), y = px(64) + i * (bh + gp); b.push(box(x, y, colW, bh)); b.push(text(x + colW / 2, y + px(25), byId.get(n)?.name || n, { anchor: "middle", size: F.sub })); return { x, y: y + bh / 2, w: colW }; }); };
  const wpos = place(writers, 0, "writers"); const cy = px(64);
  b.push(box(colX(1), cy, colW, bh, { dash: true, accent: id === "proc-stream" }));
  b.push(text(colX(1) + colW / 2, cy + px(18), byId.get(id)?.name || id, { anchor: "middle", size: F.sub }));
  if (showContracts && contractOf(id)) b.push(text(colX(1) + colW / 2, cy + px(33), contractOf(id), { anchor: "middle", size: px(9), fill: "#1d4ed8" }));
  const cpos = { x: colX(1), y: cy + bh / 2, w: colW };
  const rpos = place(readers, 2, "readers");
  for (const w of wpos) b.unshift(edge(w.x + w.w, w.y, cpos.x, cpos.y, { write: true }));
  for (const r of rpos) b.unshift(edge(cpos.x + cpos.w, cpos.y, r.x, r.y, { write: false }));
  out(`path-${id}`, svg(W, H, b.join("\n"), `carrier ${id}`));
}

// ── dispatch ───────────────────────────────────────────────────────────────────
if (!view) {
  console.log("deterministic render engine — selectors:");
  console.log("  clusters: " + composed.map((c) => `cluster:${c.container}`).join("  "));
  console.log("  flows:    " + flows.map((f) => `flow:${f["unique-id"]}`).join("  "));
  console.log("  carriers: " + nodes.filter((n) => isCarrier(n["unique-id"])).map((n) => `path:${n["unique-id"]}`).join("  "));
  console.log("  flags:    --lod=0..3  --scale=0.5..2.5  --no-contracts  --no-owners");
} else if (view === "all") { for (const c of composed) cluster(c.container); for (const f of flows) flow(f["unique-id"]); }
else if (view.startsWith("cluster:")) cluster(view.slice(8));
else if (view.startsWith("domain:")) cluster(view.slice(7));
else if (view.startsWith("flow:")) flow(view.slice(5));
else if (view.startsWith("path:")) carrierPath(view.slice(5));
else console.error(`unknown selector '${view}' — run with no args for the list`);
