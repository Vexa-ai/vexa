/** THE AGENT ASKS WHERE TO WRITE; THE PERSON DECIDES (UI decision 6b, founder 2026-10-15: "the
 *  agent should think which workspace it wants to write to and should ask to change its writing
 *  target proactively"). `propose_write_target` moves nothing — it puts this line in the agent's
 *  bubble, and only the person's click switches the chat's target (writes to a shared workspace
 *  are seen by other people). Pure, so what each click does is decided and tested here. */
import type { TargetProposal } from "./chatStream";

export type ProposalChoice = "switch" | "keep";
export type DecidedProposal = TargetProposal & { decided?: ProposalChoice };

const here = (p: TargetProposal) => p.currentName || "Personal";

/** "Write to OeNB × Vexa pilot instead of biz?" */
export function proposalQuestion(p: TargetProposal): string {
  return `Write to ${p.name} instead of ${here(p)}?`;
}

/** The two buttons' labels: [Switch] [Keep biz]. */
export function proposalButtons(p: TargetProposal): { switch: string; keep: string } {
  return { switch: "Switch", keep: `Keep ${here(p)}` };
}

/** What a click does. `target` is the person's own switch (`POST /api/chat/target`) — present only
 *  for Switch; `ask` is the answer the agent is waiting for, sent as the person's next message (the
 *  bubble shows `display`, the agent reads `prompt`). */
export function decideProposal(p: TargetProposal, choice: ProposalChoice, session: string): {
  target: { session: string; workspace: string } | null;
  ask: { display: string; prompt: string; session: string };
} {
  if (choice === "switch") {
    return {
      target: { session, workspace: p.workspace === "personal" ? "" : p.workspace },
      ask: { session, display: `Switch to ${p.name}`,
             prompt: `[write-target] I switched this chat's writes to ${p.name}. Go ahead with my request — write it there.` },
    };
  }
  return {
    target: null,
    ask: { session, display: `Keep ${here(p)}`,
           prompt: `[write-target] Keep writing to ${here(p)} for this. Go ahead with my request.` },
  };
}
