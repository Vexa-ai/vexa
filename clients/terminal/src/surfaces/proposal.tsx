"use client";
/** The proposal page's act, REGISTERED (Vexa-ai/vexa#1639).
 *
 *  Same seam and same reason as `surfaces/policies.tsx` one page along: the page renderer lives in
 *  `ui-kit` and must not import a shell, so it renders whatever is registered under the kind the
 *  proposal page declares. A build without this surface shows the step and no act — which is the
 *  honest degradation, because the act's whole job is to start a conversation and a build with no
 *  chat in it has none to start.
 */
import { registerTab } from "../contributions";
import { PROPOSAL_ACT_KIND } from "../ui-kit/policyDoc";
import { SendToDevelopersButton } from "../minutes/FlowProposalAct";

registerTab(PROPOSAL_ACT_KIND, ({ params }) => {
  const p = (params ?? {}) as { workspace?: unknown; path?: unknown };
  return <SendToDevelopersButton workspace={p.workspace ? String(p.workspace) : undefined}
    path={p.path ? String(p.path) : undefined} />;
});
