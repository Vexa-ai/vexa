"use client";
/** The minutes shell's registrations: the acts a page renders by kind, and the GitHub connection
 *  card Settings renders by kind. They live here, not in `surfaces/`, because the components are the
 *  minutes shell's and it sits above `surfaces/`; the composition root (`app/App.tsx`) imports this
 *  beside the surfaces barrel (Vexa-ai/vexa#1783).
 *
 *  The two acts (Vexa-ai/vexa#1627, #1639): the page renderer lives in `ui-kit` and must not import a
 *  shell, so it renders whatever is registered under the kind the policy or proposal page declares.
 *  A build without these shows the rules or the step and no act — the honest degradation, because
 *  the act's whole job is to start a conversation and a build with no chat in it has none to start.
 */
import { registerTab } from "../contributions";
import { POLICY_ACT_KIND, PROPOSAL_ACT_KIND } from "../ui-kit/policyDoc";
import { GITHUB_CONNECTION_KIND } from "../surfaces/tokens";
import { SetUpPoliciesButton } from "./PoliciesAct";
import { SendToDevelopersButton } from "./FlowProposalAct";
import { GitConnection } from "./GitConnection";
import { BLOCK_RENDERER_KIND } from "../ui-kit/MdxDoc";
import { PEEK_KIND } from "../ui-kit/peekSeam";
import { PagePeekPanel, VexaFence } from "../ui-kit";

registerTab(POLICY_ACT_KIND, ({ params }) => {
  const p = (params ?? {}) as { workspace?: unknown; path?: unknown };
  return <SetUpPoliciesButton workspace={p.workspace ? String(p.workspace) : undefined}
    path={p.path ? String(p.path) : undefined} />;
});

registerTab(PROPOSAL_ACT_KIND, ({ params }) => {
  const p = (params ?? {}) as { workspace?: unknown; path?: unknown };
  return <SendToDevelopersButton workspace={p.workspace ? String(p.workspace) : undefined}
    path={p.path ? String(p.path) : undefined} />;
});

// Settings uses the same connection editor as Minutes (surfaces/tokens.tsx renders this kind).
registerTab(GITHUB_CONNECTION_KIND, () => <GitConnection />);

// THE SHARED BLOCKS RENDERER IN PAGES (#1783): the rich-pages seams draw ```vexa fences and peek
// entity chips through these two slots, so a page, a reply and the home behave the same.
registerTab(BLOCK_RENDERER_KIND, ({ params }) => <VexaFence source={String(params?.source ?? "")} />);
registerTab(PEEK_KIND, ({ params }) => <PagePeekPanel params={params} />);
