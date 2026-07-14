// release-witness-template — scaffold a witness receipt for a release from its batch.
// Usage: node scripts/release-witness-template.mjs vX.Y.Z [prevTag] > releases/vX.Y.Z/witness.json
// Prefills version/candidate and seeds values_walked with the batch PRs (title per PR) so the
// witness walks each user-visible value once and prunes the backend-invisible rows to their proxy.
// The human fills witnessed_by / witnessed_at / evidence.* and sets signed_off:true, then commits.

import { execSync } from "node:child_process";

const VERSION = process.argv[2];
const PREV = process.argv[3] || "";
const REPO = process.env.GITHUB_REPOSITORY || "Vexa-ai/vexa";
if (!VERSION) { console.error("usage: release-witness-template.mjs vX.Y.Z [prevTag]"); process.exit(2); }

const ghj = (p) => JSON.parse(execSync(`gh api "${p}"`, { encoding: "utf8" }));

let values = [];
try {
  const range = PREV ? `${PREV}...${VERSION}` : VERSION;
  const cmp = ghj(`repos/${REPO}/compare/${range}?per_page=100`);
  for (const c of cmp.commits || []) {
    const subject = (c.commit?.message || "").split("\n")[0];
    const m = subject.match(/\(#(\d+)\)\s*$/);
    if (m) values.push(subject);
  }
} catch (e) {
  values = [`(could not enumerate batch: ${e.message} — list the user-visible values by hand)`];
}
if (!values.length) values = ["(no batch commits found — list every user-visible value by hand)"];

const receipt = {
  version: VERSION,
  candidate: VERSION,
  witnessed_by: "",
  witnessed_at: "",
  deployment: "",
  evidence: {
    meeting_url: "",
    transcript: "",
    live_stream: "",
    values_walked: values,
  },
  signed_off: false,
};
console.log(JSON.stringify(receipt, null, 2));
