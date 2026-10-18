"use client";
/** The line under the call bar while a page is shown to everyone (founder 2026-10-15): whose page,
 *  following or not, and — for the presenter — stop showing. A refused show offers to share the
 *  workspace with whoever cannot read it first. */
import { Eye, EyeOff, MonitorUp, Pin, PinOff, X } from "lucide-react";
import { dismissOffer, follow, shareAndShow, stopFollowing, stopShowing, togglePin, useCallShow } from "./callShow";

export function CallShowBar() {
  const s = useCallShow();
  if (s.offer) {
    const names = s.offer.missing.map((m) => m.name).join(", ");
    return (
      <div className="vx-show" data-call="show-offer" role="status">
        <span><b>{names}</b> can't open this page. Share the workspace with {s.offer.missing.length > 1 ? "them" : "them"} first, then show it?</span>
        <span className="vx-cb-gap" />
        <button type="button" className="vx-cb-text" onClick={dismissOffer}>Cancel</button>
        <button type="button" className="vx-cb-text" data-tone="join" onClick={() => void shareAndShow()}>Share and show</button>
      </div>
    );
  }
  if (!s.shown) return s.note ? <div className="vx-show" role="alert">{s.note}</div> : null;
  return (
    <div className="vx-show" data-call="show" role="status">
      <MonitorUp size={13} aria-hidden />
      <span className="vx-show-title">{s.presenter ? "You are showing" : `Shown by ${s.shown.byName}`} · <b>{s.shown.title}</b></span>
      {!s.presenter && (s.following
        ? <span className="vx-cb-quiet"><Eye size={12} aria-hidden /> Following</span>
        : <span className="vx-cb-quiet"><EyeOff size={12} aria-hidden /> Browsing on your own</span>)}
      <span className="vx-cb-gap" />
      <button type="button" className="vx-cb-btn" aria-label={s.pinned ? "Unpin the highlight" : "Pin the highlight"} title={s.pinned ? "Unpin the highlight" : "Pin the highlight"}
        aria-pressed={s.pinned} onClick={togglePin}>{s.pinned ? <PinOff size={13} /> : <Pin size={13} />}</button>
      {s.presenter
        ? <button type="button" className="vx-cb-text" onClick={stopShowing}><X size={12} aria-hidden /> Stop showing</button>
        : s.following
          ? <button type="button" className="vx-cb-text" onClick={stopFollowing}>Stop following</button>
          : <button type="button" className="vx-cb-text" onClick={follow}>Follow {s.shown.byName}</button>}
    </div>
  );
}
