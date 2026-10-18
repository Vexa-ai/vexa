"use client";
/** The video stage. Layout follows how many people have a tile:
 *   1 other           → they fill the stage, you are a small self-view in its corner
 *   3–4               → a 2×2 grid
 *   5–6               → 3×2
 *   7+                → 3×3, the last cell "+N", the active speaker always kept in view
 *   a screen share    → the share is the main stage, everyone else a filmstrip
 *  Vexa has no tile: it is in the call as a voice and a presence (AgentChip), not a camera. */
import type { ReactNode } from "react";
import type { CallPerson, ScreenShare } from "./types";
import { CallTile } from "./People";
import { MonitorUp } from "lucide-react";

export function CallStage({ people, share, activeId, variant = "dock", narrow, selfAction }: {
  people: CallPerson[]; share?: ScreenShare | null; activeId?: string; variant?: "dock" | "page"; narrow?: boolean;
  /** a control on your own tile ("You"): background blur */
  selfAction?: ReactNode;
}) {
  const humans = people.filter((p) => !p.agent);
  const self = humans.find((p) => p.self);
  const others = humans.filter((p) => !p.self);

  if (share) {
    const film = [...others, ...(self ? [self] : [])];
    return (
      <div className="vx-ck-stage" data-layout="share" data-variant={variant} data-narrow={narrow ? "" : undefined}>
        <section className="vx-ck-share" aria-label={`${share.byName} is presenting`}>
          <header className="vx-ck-share-head"><MonitorUp size={12} /> {share.byName} is presenting · {share.title}</header>
          <div className="vx-ck-share-body">{share.content}</div>
        </section>
        <div className="vx-ck-film" aria-label="Participants">
          {film.map((p) => <CallTile key={p.id} p={p} kind="film" active={p.id === activeId} action={p.self ? selfAction : undefined} />)}
        </div>
      </div>
    );
  }

  if (others.length <= 1) {
    const main = others[0] ?? self;
    return (
      <div className="vx-ck-stage" data-layout="one" data-variant={variant} data-narrow={narrow ? "" : undefined}>
        {main && <CallTile p={main} kind="stage" active={main.speaking} action={main.self ? selfAction : undefined} />}
        {self && main !== self && <div className="vx-ck-selfview"><CallTile p={self} kind="self" action={selfAction} /></div>}
      </div>
    );
  }

  // the grid: the active speaker is never the one pushed into "+N"
  const all = [...others, ...(self ? [self] : [])];
  const cap = narrow ? 4 : 9;
  let shown = all;
  let more = 0;
  if (all.length > cap) {
    const keep = all.slice(0, cap - 1);
    const act = all.find((p) => p.id === activeId);
    if (act && !keep.includes(act)) keep[keep.length - 1] = act;
    if (self && !keep.includes(self)) keep[keep.length - 2] = self;
    shown = keep;
    more = all.length - keep.length;
  }
  const cells = shown.length + (more ? 1 : 0);
  const cols = narrow ? (cells <= 1 ? 1 : 2) : cells <= 4 ? 2 : 3;
  return (
    <div className="vx-ck-stage" data-layout="grid" data-variant={variant} data-narrow={narrow ? "" : undefined}
      style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` }}>
      {shown.map((p) => <CallTile key={p.id} p={p} kind="grid" active={p.id === activeId} action={p.self ? selfAction : undefined} />)}
      {more > 0 && (
        <div className="vx-ck-tile vx-ck-tile-more" title={all.filter((p) => !shown.includes(p)).map((p) => p.name).join(", ")}>
          +{more} more
        </div>
      )}
    </div>
  );
}
