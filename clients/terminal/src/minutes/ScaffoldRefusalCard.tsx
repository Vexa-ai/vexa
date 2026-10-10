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
      className="mt-3 mr-3 mb-0 ml-3 pt-3 pr-3 pb-3 pl-3 r-md bd bg-0" style={{ flex: "none" }}>
      <div className="t-sm c-1 mb-1" style={{ ...ty.title }}>{c.title}</div>
      <div data-refusal="body" className="c-3 lh-normal" style={{ ...ty.body }}>{c.body}</div>
      <div className="mt-2" style={{ display: "flex", gap: 8 }}>
        {/* THE WAY OUT, NEXT TO THE DIAGNOSIS (F48). Signing out lands on the sign-in screen, which
            is where somebody on the wrong account has to get to. Without this the card states a
            problem whose only fix is hidden in a menu at the foot of a rail they may have
            collapsed. It is the SAME door the account menu opens, not a second one. */}
        {c.offerSwitch && (
          <button data-refusal="switch" onClick={switchAccount}
            className="c-1 bg-none bd-strong r-md pt-0_5 pr-2 pb-0_5 pl-2" style={{ ...ty.chip, cursor: "pointer" }}>
            Switch account
          </button>
        )}
        <button data-refusal="dismiss" onClick={onDismiss}
          className="c-3 bg-none bd r-md pt-0_5 pr-2 pb-0_5 pl-2" style={{ ...ty.chip, cursor: "pointer" }}>
          Dismiss
        </button>
      </div>
    </div>
  );
}
