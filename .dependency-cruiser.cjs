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
      name: "meetings-not-agent",
      comment: "Seam — meetings must not import agent (coupled only via schemas/transcript.v1 + workspace).",
      severity: "error",
      from: { path: "^meetings/" },
      to: { path: "^agent/" },
    },
    {
      name: "agent-not-meetings",
      comment: "Seam — agent must not import meetings.",
      severity: "error",
      from: { path: "^agent/" },
      to: { path: "^meetings/" },
    },
    {
      name: "runtime-depends-on-nothing-above",
      comment: "P3 — the kernel depends on schemas only, nothing above it.",
      severity: "error",
      from: { path: "^runtime/" },
      to: { path: "^(meetings|agent|identity|gateway|integrations|clients|sdks)/" },
    },
  ],
  options: {
    doNotFollow: { path: "node_modules" },
    tsConfig: { fileName: "tsconfig.base.json" },
  },
};
