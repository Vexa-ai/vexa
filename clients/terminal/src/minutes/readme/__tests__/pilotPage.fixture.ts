/** The shape of a real pilot workspace page (app.dev, 2026-10-18), names changed. Founder: "things do
 *  not render" — `<Steps>`, `</Warning>` and `[x]` printed as text on the workspace page.
 *
 *  `PILOT_TREE` is what `POST /api/desktop/page` answered for it, cut where the server cuts: the
 *  `{{members}}` / `{{upcoming}}` blocks rendered nothing, so `:::columns` opens in one md node, its
 *  `---` sits alone in the next and its `:::` closes the third. `PILOT_TREE_WARNING` is the revision
 *  before, which said the same with `<Warning>`; it also carries a typed reference inside a step, so
 *  one task arrives as a server `line` node in the middle of an open `<Step>`. */
import type { PageTree } from "../ReadmeView";

const STEPS = `## Steps to go live

Tick as they land. Detail for steps 1–6 is in [delivery-chain.md](delivery-chain.md).

<Steps>
<Step title="1 · Hand over the channel credential — Vexa → Acme">

Full plan with every command: [runbooks/channel-credential-handover.md](runbooks/channel-credential-handover.md) · [[Channel credential]]

- [x] Before the call: account and station agreed, and Mira has \`claim.sh\` (the claim went through)
- [x] On the call: Vexa issues the code (\`add --park\`) and reads it out; [[Mira Example]] claims it with \`claim.sh\` within 15 minutes
- [ ] Save the claimed secret somewhere durable
- [ ] Claim recorded in the ledger

</Step>
<Step title="2 · Open the transport — Acme · current step">

- [x] Choose [[Harbor proxy-cache vs GitLab pull]]: **Harbor proxy-cache**
- [ ] Harbor proxy for \`channel.vexa.ai\` is up

</Step>
<Step title="3 · Publish the release — Vexa">

- [ ] [[Vexa 0.13.3-alpha.1]] signed and its chart on the channel

</Step>
</Steps>

## Requirements

| Status | Requirement | Owner |
|---|---|---|
| 🟢 done | [[Channel credential]]: claimed, rotated once | Vexa |
| 🔴 open | Harbor proxy for \`channel.vexa.ai\` | Acme |
`;

const STATUS = `**Where we are:** step 1 is mostly done: the channel credential was claimed on [Vexa Channel call](/?meeting=137). **6 days to go.**

**Unconfirmed since the call:** \`channel.vexa.ai\` reachability check, [[ADFS app registration]].`;

export const PILOT_TREE: PageTree = {
  v: 1, workspace: "acme-vexa-pilot-0a1b2c", path: "README.md", role: "owner",
  nodes: [
    { t: "md", text: "# Acme × Vexa pilot\n\n**Goal:** first 10 [[Acme]] users live on self-hosted [[Vexa]] **0.13.3**. Owner: [[Mira Example]]\n" },
    { t: "md", text: "\n:::columns" },
    { t: "md", text: "\n---" },
    { t: "md", text: `:::\n\n:::callout\n${STATUS}\n:::\n\n${STEPS}\n<!-- desk:pinned:start -->\n## Pinned\n<!-- desk:pinned:end -->\n` },
  ],
  refs: {},
};

export const PILOT_TREE_WARNING: PageTree = {
  v: 1, workspace: "acme-vexa-pilot-0a1b2c", path: "README.md", role: "reader",
  nodes: [
    { t: "md", text: `# Acme × Vexa pilot\n\n<Warning>\n\n${STATUS}\n\n</Warning>\n\n${STEPS.split("</Step>\n<Step title=\"2")[0]}` },
    { t: "line", list: true, parts: [{ t: "text", text: "- [ ] Revoke the first credential with " }, { t: "ref", key: "person:7" }, { t: "text", text: " (`oc` only)" }] },
    { t: "md", text: "\n</Step>\n</Steps>\n" },
  ],
  refs: { "person:7": { kind: "person", access: "readable", label: "Mira Example" } },
};
