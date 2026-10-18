"use client";
/** WHERE THE HOME WRITES, and the workspace's face (#1783, R3 `home2`; approved mock "sticky home
 *  composer", founder 2026-10-15: "chat probably should stay sticky when we scroll down the
 *  workspaces and gets coloured in the colour of the workspace").
 *
 *  - The home composer sticks to the top of the home column; stuck, it folds to one line plus send
 *    and opens again on focus. The Now strip and the cards scroll under it.
 *  - It wears the target workspace's colour (a low-saturation tint and border), its mark and
 *    "→ <workspace>". A status card's click retints it (a short transition, none under reduced
 *    motion). Personal's colour is the default.
 *  - The target is set by the shell's one writer (HOME_TARGET_EVENT → `chooseTarget`); this module
 *    only remembers what the cards last aimed at, to draw it. */
import { useEffect, useRef, useState, useSyncExternalStore, type CSSProperties, type ReactNode } from "react";
import { HOME_TARGET_EVENT } from "../../../platform/events";
import { Identity } from "../../../ui-kit/Identity";
import { featureOn, useFeature } from "../../../platform/features";
import { X } from "lucide-react";

export const PERSONAL = "personal";
let target = PERSONAL;
const subs = new Set<() => void>();
if (typeof window !== "undefined") {
  window.addEventListener(HOME_TARGET_EVENT, (e) => {
    target = String((e as CustomEvent<{ workspace?: string }>).detail?.workspace || "") || PERSONAL;
    if (target === PERSONAL) focus = false;
    subs.forEach((f) => f());
  });
}
const sub = (f: () => void) => { subs.add(f); return () => { subs.delete(f); }; };
const snap = () => target;

/** FOCUS IS SEPARATE FROM THE TARGET (UI 7i/7t/7x, `shell2`): opening a workspace aims the home at it
 *  and leaves the home full; only a workspace's own "New chat" collapses the home to the focused
 *  input. Clearing the target (the chip's ×, or "+ New") ends focus. */
let focus = false;
const focusSnap = () => focus;
export function setHomeFocus(on: boolean): void { focus = on; subs.forEach((f) => f()); }
export function useHomeFocus(): boolean { return useSyncExternalStore(sub, focusSnap, focusSnap); }
/** Record where the home writes without firing the shell's writer — the shell already aimed the
 *  draft itself (a workspace's "New chat"). */
export function noteHomeTarget(slug: string): void { target = slug || PERSONAL; if (target === PERSONAL) focus = false; subs.forEach((f) => f()); }

/** The workspace the home composer writes to, as the cards last aimed it ("personal" by default). */
export function useHomeTarget(): string { return useSyncExternalStore(sub, snap, snap); }

/** Aim the home composer at a workspace (never a read-only one — the caller checks `write`). */
export function aimHome(slug: string): void {
  window.dispatchEvent(new CustomEvent(HOME_TARGET_EVENT, { detail: { workspace: slug === PERSONAL ? "" : slug } }));
}

/** What an aim may do to the chat in front: only the home's untouched draft is aimed; a stored
 *  conversation in front is never re-targeted from here (it has its own chip and guard), so the aim
 *  opens a new draft first. */
/** A CARD CLICK SETS THE TARGET (UI decisions; founder 2026-10-16): the first click aims the home's
 *  chat at the workspace, a click on the card already aimed resets it to Personal. */
export function toggleHomeTarget(slug: string): void {
  // shell2 (UI 7x): the target FOLLOWS the workspace you open — a second click never un-aims it;
  // the chip's × and "+ New" are the way back to My desk
  if (featureOn("shell2")) { aimHome(slug); return; }
  aimHome(target === slug && slug !== PERSONAL ? PERSONAL : slug);
}

export function homeTargetAct(frontChatId: string | undefined, draftId: string | undefined): "aim-draft" | "new-draft" {
  return frontChatId && draftId && frontChatId === draftId ? "aim-draft" : "new-draft";
}

/** Test seam. */
export function resetHomeTarget(): void { target = PERSONAL; focus = false; subs.forEach((f) => f()); }

/** A stable hue per workspace (Personal warm by default), so the same workspace always has one colour. */
export function wsHue(slug: string | undefined): number {
  const s = slug || PERSONAL;
  if (s === PERSONAL) return 28;
  let h = 0;
  for (const ch of s) h = (h * 31 + ch.codePointAt(0)!) >>> 0;
  return h % 360;
}

/** The workspace's face: its stored image (or its company's logo) when it has one, else its
 *  monogram — the product's one identity component (UI decisions 15f, 15g). */
export function WsMark({ slug, name, size = 40 }: { slug?: string; name: string; size?: number }) {
  const s = slug || PERSONAL;
  return <Identity kind="workspace" id={s} slug={s} name={name} size={size} />;
}

/** THE SPACE A STUCK COMPOSER GIVES BACK (founder 2026-10-16: the composer flickered in and out of
 *  its sticky state while scrolling). Folding to one line shrank the layout, which brought the
 *  sentinel back into view, which unstuck it, which grew it again — a loop. The spacer under it holds
 *  the height it had unstuck, so going sticky never moves anything. */
export function stickySpacer(stuck: boolean, unstuckHeight: number, height: number): number {
  return stuck ? Math.max(0, Math.round(unstuckHeight - height)) : 0;
}

export function HomeComposer({ children, nameOf }: { children: ReactNode; nameOf: (slug: string) => string }) {
  const slug = useHomeTarget();
  // shell2: the composer does not stick (it covered the Now strip on a phone), and its target chip
  // carries the × that clears it back to My desk
  const shell2 = useFeature("shell2");
  const focused = useHomeFocus();
  const sentinel = useRef<HTMLDivElement>(null);
  const box = useRef<HTMLDivElement>(null);
  const [stuck, setStuck] = useState(false);
  const stuckRef = useRef(false);
  const unstuckH = useRef(0);
  const [h, setH] = useState(0);
  useEffect(() => {
    const el = sentinel.current;
    if (!el || typeof IntersectionObserver === "undefined") return;
    const io = new IntersectionObserver(([e]) => {
      const next = !e.isIntersecting && !featureOn("shell2");
      // remember the full height at the moment it sticks, before it folds
      if (next && !stuckRef.current && box.current) unstuckH.current = box.current.getBoundingClientRect().height;
      stuckRef.current = next;
      setStuck(next);
    }, { threshold: 0 });
    io.observe(el);
    return () => io.disconnect();
  }, []);
  useEffect(() => {
    const el = box.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => {
      const now = el.getBoundingClientRect().height;
      if (!stuckRef.current) unstuckH.current = now;
      setH(now);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const name = nameOf(slug);
  const spacer = stickySpacer(stuck, unstuckH.current, h);
  return (<>
    <div ref={sentinel} className="vx-hc-sentinel" aria-hidden />
    <div ref={box} data-home-composer="" data-home-target={slug} className="vx-home-composer vx-hc" data-stuck={stuck ? "" : undefined}
      style={{ "--hc-h": String(wsHue(slug)) } as CSSProperties}>
      <div className="vx-hc-to" aria-live="polite"><WsMark slug={slug} name={name} size={16} /><span>→ {name}</span>
        {shell2 && (slug !== PERSONAL || focused) && <button type="button" className="vx-hc-clear" aria-label={`Clear the target ${name}`} title="Back to My desk"
          onClick={() => aimHome(PERSONAL)}><X size={12} strokeWidth={2} aria-hidden /></button>}</div>
      {children}
    </div>
    {spacer > 0 && <div className="vx-hc-spacer" aria-hidden style={{ height: spacer }} />}
  </>);
}
