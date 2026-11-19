"use client";
/** THE POLICIES PAGE'S OWN ACT — **Set up policies** (Vexa-ai/vexa#1627).
 *
 *  Founder, 2026-09-06: *"this is essentially a part of the onboarding process that helps decide on
 *  the policy to start with, which is a tradeoff between adoption and security, but with specific
 *  risks that we can assess and define."* The setup conversation is the first visit to that
 *  decision; it is not the only one. So the decision has a door of its own, on the page that shows
 *  what this deployment currently answers.
 *
 *  IT IS THE SAME DOOR EVERY OTHER ACT USES. `postIntent` → the open chat's inbox (#1610) → the
 *  server maps the KIND to `_global/asks/policies-wizard.md` and sends that ask as the turn
 *  (`chat_intents.INTENT_PRESETS`). Nothing here composes a sentence and nothing here names a
 *  preset: an act carries a kind and its arguments, exactly as a scaffold's opening is a NAME, and
 *  for the same reason — anyone able to make a client send an intent would otherwise be able to
 *  drive somebody else's agent.
 *
 *  NOT A JOB, AND NOT SILENT. The wizard is a conversation with five questions in it, so there is
 *  no background job to watch and no working state to wear (`chat_intents.JOB_KINDS` leaves it out);
 *  and the person pressed a labelled control, so the turn shows as its label rather than hiding
 *  (`SILENT_KINDS` leaves it out too).
 *
 *  IT ADDRESSES THE PAGE IT WAS PRESSED ON. The workspace and path come from the open document's
 *  own meta (`DocMetaContext`, threaded through the registry by `MdxDoc`), never from a tab label or
 *  a crumb — the F63 rule every other act on this screen keeps. The constants below are the floor
 *  for a caller that has no meta at all, and they name the one file this act exists for.
 */
import type { ReactNode } from "react";
import { Icon } from "../ui-kit";
import { postIntent } from "./extend";
import { type as ty, surface } from "./tokens";

/** The organisation tier, and the file the rules live in. One spelling, shared with the ask's own
 *  frontmatter and with `flows_steps/policies.GLOBAL_SLUG` / `POLICIES_FILE`. */
export const POLICIES_WORKSPACE = "_global";
export const POLICIES_PATH = "POLICIES.md";

export const SET_UP_POLICIES = "Set up policies";

const chipBtn = {
  ...ty.chip, display: "inline-flex", alignItems: "center", gap: 5, flex: "none",
  color: "var(--t1)", background: surface.raised, border: "1px solid var(--line)",
  borderRadius: 999, padding: "2px 10px", cursor: "pointer", lineHeight: 1.6,
} as const;

/** The control itself. One press, no field: the wizard's first question IS the field, and a line
 *  typed here would be a sixth question asked before the five. */
export function SetUpPoliciesButton(p: { workspace?: string; path?: string }): ReactNode {
  const workspace = (p.workspace || "").trim() || POLICIES_WORKSPACE;
  const path = (p.path || "").trim() || POLICIES_PATH;
  return (
    <button
      data-doc-act="policies-wizard" title="Five questions about your own risks, then a recommended policy with its reasoning"
      onClick={() => { postIntent({ kind: "policies_wizard", workspace, path }); }}
      style={chipBtn}
      onMouseEnter={(e) => { e.currentTarget.style.background = surface.raisedHi; e.currentTarget.style.borderColor = "var(--accent)"; }}
      onMouseLeave={(e) => { e.currentTarget.style.background = surface.raised; e.currentTarget.style.borderColor = "var(--line)"; }}>
      <span style={{ display: "flex", color: "var(--accent)" }}><Icon name="spark" size={12} /></span>
      {SET_UP_POLICIES}
    </button>
  );
}
