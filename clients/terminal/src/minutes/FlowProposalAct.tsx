"use client";
/** A PROPOSAL PAGE'S OWN ACT — **Send to the developers** (Vexa-ai/vexa#1639).
 *
 *  Founder, 2026-09-06, in the governance chat of `_global`: *"we want to be able to write flows for
 *  the global chat as we like."* A flow is composed from step names this image already carries, so a
 *  sentence that needs something no step does used to have nowhere to go. It becomes a page instead
 *  — `_global/flows/proposals/<slug>.md`, the step written out in this repo's own shape, with the
 *  flow that would use it and the tests it needs — and this is the one thing that page is for.
 *
 *  IT IS THE SAME DOOR EVERY OTHER ACT USES. `postIntent` → the open chat's inbox (#1610) → the
 *  server maps the KIND to `_global/asks/flow-author.md` and sends that ask as the turn
 *  (`chat_intents.INTENT_PRESETS`). Nothing here composes a sentence and nothing here names a
 *  preset: an act carries a kind and its arguments, for the reason a scaffold's opening is a NAME —
 *  anyone able to make a client send an intent could otherwise drive somebody else's agent.
 *
 *  ONE KIND FOR BOTH HALVES OF THE CONVERSATION, and the PATH is what says which. The ask branches
 *  on it: a page under `flows/proposals/` is this send, anything else is writing a flow. They are
 *  one conversation with one administrator, unlike the three membership acts, whose labels a person
 *  must be able to tell apart.
 *
 *  PRESSING IT DOES NOT SEND ANYTHING. It opens the turn that reads the page and asks — the agent
 *  confirms in one sentence before a ticket exists, because a ticket reaches humans at another
 *  company and cannot be withdrawn. The title says so, so the button does not read as the send.
 *
 *  IT ADDRESSES THE PAGE IT WAS PRESSED ON. The workspace and path come from the open document's own
 *  meta (`DocMetaContext`, threaded through the registry by `MdxDoc`), never from a tab label or a
 *  crumb — the F63 rule every other act on this screen keeps.
 */
import type { ReactNode } from "react";
import { Icon } from "../ui-kit";
import { postIntent } from "./extend";
import { type as ty, surface } from "./tokens";

/** The organisation tier and the directory proposals live in. One spelling, shared with the ask's
 *  own prose and with `flows_pages.PROPOSALS_DIRNAME` on the other side. */
export const FLOWS_WORKSPACE = "_global";
export const PROPOSALS_DIR = "flows/proposals";

export const SEND_TO_DEVELOPERS = "Send to the developers";

const chipBtn = {
  ...ty.chip, display: "inline-flex", alignItems: "center", gap: 5, flex: "none",
  color: "var(--t1)", background: surface.raised, border: "1px solid var(--line)",
  borderRadius: 999, padding: "2px 10px", cursor: "pointer", lineHeight: 1.6,
} as const;

/** The control itself. One press, no field: what to send is the page, and what to say about it is
 *  the agent's one confirmation. */
export function SendToDevelopersButton(p: { workspace?: string; path?: string }): ReactNode {
  const workspace = (p.workspace || "").trim() || FLOWS_WORKSPACE;
  const path = (p.path || "").trim();
  // NO PATH, NO ACT. A send that had to guess which proposal it was about is the one mistake this
  // button cannot make — the ticket cannot be withdrawn. Rendering nothing is the honest answer;
  // the page underneath still says what the step would be.
  if (!path) return null;
  return (
    <button
      data-doc-act="flow-proposal-send"
      title="Opens the chat to confirm first — nothing is filed until you say yes"
      onClick={() => { postIntent({ kind: "flow_author", workspace, path }); }}
      style={chipBtn}
      onMouseEnter={(e) => { e.currentTarget.style.background = surface.raisedHi; e.currentTarget.style.borderColor = "var(--accent)"; }}
      onMouseLeave={(e) => { e.currentTarget.style.background = surface.raised; e.currentTarget.style.borderColor = "var(--line)"; }}>
      <span style={{ display: "flex", color: "var(--accent)" }}><Icon name="spark" size={12} /></span>
      {SEND_TO_DEVELOPERS}
    </button>
  );
}
