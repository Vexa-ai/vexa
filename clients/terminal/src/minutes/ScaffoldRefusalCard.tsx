"use client";
/** ScaffoldRefusalCard — what a person sees when the link they clicked will not open.
 *
 *  WHY IT IS ITS OWN FILE. This card is the entire product for somebody whose link was refused: the
 *  chat behind it is not theirs and never will be. It used to live inline in MinutesShell, which
 *  meant the only way to see what it says was to render the whole shell — so nothing did, and the
 *  copy went unchecked. It states three things and they are each worth pinning: what happened, WHO
 *  the server judged the link against, and the one action that fixes the common case.
 *
 *  It sits ABOVE the chat rather than replacing it, because the reader's own conversations are
 *  still theirs and hiding them would be a second wrong.
 */
import { switchAccount } from "./AccountBadge";
import { refusalCopy, type ScaffoldRefusal } from "./scaffold";
import { type as ty } from "./tokens";

export function ScaffoldRefusalCard({ refusal, signedInAs, onDismiss }: {
  refusal: ScaffoldRefusal;
  /** The address the server judged the link against, when the identity probe answered. */
  signedInAs?: string | null;
  onDismiss: () => void;
}) {
  const c = refusalCopy(refusal, signedInAs);
  return (
    <div role="alert" data-scaffold-refusal={refusal.reason}
      style={{ flex: "none", margin: "12px 14px 0", padding: "12px 14px", borderRadius: 8,
        border: "1px solid var(--line)", background: "var(--bg2, var(--bg))" }}>
      <div style={{ ...ty.title, fontSize: 13.5, color: "var(--t1)", marginBottom: 4 }}>{c.title}</div>
      <div data-refusal="body" style={{ ...ty.body, color: "var(--t3)", lineHeight: 1.55 }}>{c.body}</div>
      <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
        {/* THE WAY OUT, NEXT TO THE DIAGNOSIS (F48). Signing out lands on the sign-in screen, which
            is where somebody on the wrong account has to get to. Without this the card states a
            problem whose only fix is hidden in a menu at the foot of a rail they may have
            collapsed. It is the SAME door the account menu opens, not a second one. */}
        {c.offerSwitch && (
          <button data-refusal="switch" onClick={switchAccount}
            style={{ ...ty.chip, color: "var(--t1)", background: "transparent",
              border: "1px solid var(--line2)", borderRadius: 6, padding: "3px 10px", cursor: "pointer" }}>
            Switch account
          </button>
        )}
        <button data-refusal="dismiss" onClick={onDismiss}
          style={{ ...ty.chip, color: "var(--t3)", background: "transparent",
            border: "1px solid var(--line)", borderRadius: 6, padding: "3px 10px", cursor: "pointer" }}>
          Dismiss
        </button>
      </div>
    </div>
  );
}
