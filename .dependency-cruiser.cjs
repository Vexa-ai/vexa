/**
 * gate:graph — acyclic dependency graph + the allowed-edges seam (ARCHITECTURE.md §3, P3).
 * Invoked by scripts/gates.mjs once packages exist; the rules below are the machine form of
 * the dependency-rules block. Extend as domains land (Stage 1+).
 */
module.exports = {
  forbidden: [
    {
      name: "no-circular",
      comment: "P3 — a cycle is mud; the graph must be acyclic.",
      severity: "error",
      from: {},
      to: { circular: true },
    },
    {
      name: "meetings-internals-not-agent",
      comment: "Seam — meetings INTERNALS must not import agent internals. Referencing another domain's contracts/ IS allowed (that's the published seam).",
      severity: "error",
      from: { path: "^core/meetings/(services|modules)/" },
      to: { path: "^core/agent/(services|modules)/" },
    },
    {
      name: "agent-internals-not-meetings",
      comment: "Seam — agent internals must not import meetings internals (contracts/ is allowed).",
      severity: "error",
      from: { path: "^core/agent/(services|modules)/" },
      to: { path: "^core/meetings/(services|modules)/" },
    },
    {
      name: "runtime-depends-on-nothing-above",
      comment: "P3 — the kernel's internals depend on nothing above; it may own contracts but not import a domain.",
      severity: "error",
      from: { path: "^core/runtime/(services|modules|src)/" },
      to: { path: "^(core/(meetings|agent|identity|gateway)|integrations|clients|sdks)/(services|modules)/" },
    },
  ],
  options: {
    doNotFollow: { path: "node_modules" },
    exclude: { path: "^clients/dashboard/" },   // vendored Next.js app — pending the principled refactor
    tsConfig: { fileName: "tsconfig.base.json" },
  },
};
