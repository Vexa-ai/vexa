"use client";
/** The policies page's act, REGISTERED (Vexa-ai/vexa#1627).
 *
 *  Same seam and same reason as `surfaces/canvas.tsx`'s transcript widget: the page renderer lives
 *  in `ui-kit` and must not import a shell, so it renders whatever is registered under the kind the
 *  policy page declares. A build without this surface shows the rules and no act — which is the
 *  honest degradation, because the act's whole job is to start a conversation and a build with no
 *  chat in it has none to start.
 */
import { registerTab } from "../contributions";
import { POLICY_ACT_KIND } from "../ui-kit/policyDoc";
import { SetUpPoliciesButton } from "../minutes/PoliciesAct";

registerTab(POLICY_ACT_KIND, ({ params }) => {
  const p = (params ?? {}) as { workspace?: unknown; path?: unknown };
  return <SetUpPoliciesButton workspace={p.workspace ? String(p.workspace) : undefined}
    path={p.path ? String(p.path) : undefined} />;
});
