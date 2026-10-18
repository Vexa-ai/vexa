"use client";
/** THE HOME WITH THE DESKTOP (#1783; UI decisions 15a, 15b, 15e; desktop ADR D1).
 *
 *  Behind the `desktop` feature. ON: the greeting and the composer as today, the starters under
 *  them, and the person's desktop below in place of the old composed cards. OFF — or crm refusing
 *  the desk, or the desktop not readable — `fallback`, the current home, untouched. `active` is the
 *  shell's own rule for when the home shows at all (the new chat in front, its offer unspent).
 *
 *  The desktop is read fresh each time the home appears; nothing about it is kept in this browser. */
import { deskName } from "../vocabulary";
import { featureOn } from "../../platform/features";
import { useEffect, useRef, type ReactNode } from "react";
import { useHomeFocus } from "./home2/HomeTarget";
import { useFeature } from "../../platform/features";
import { useComposerSlot } from "../../surfaces/composerSlot";
import { Toaster } from "../../ui-kit/primitives/Toast";
import { ensureSubject, loadDesktop, useDesktopState } from "./desktopApi";
import { DesktopView } from "./DesktopView";
import { CardDetailHost } from "./CardDetail";
import { HomeComposer } from "./home2/HomeTarget";
import "../home.css";
import "./desktop.css";

type Greeting = { title?: string; text?: string } | null | undefined;

function DesktopPage({ greeting, starters, extra }: { greeting: Greeting; starters?: ReactNode; extra?: ReactNode }) {
  const composer = useComposerSlot();
  const { desktop } = useDesktopState();
  // home2 (R3): the composer sticks and wears the colour of where it writes
  const home2 = useFeature("home2");
  const nameOf = (slug: string) => slug === "personal" && featureOn("shell2") ? deskName() : (desktop?.sections.find((s) => s.id === "workspaces")?.items
    .find((i) => i.kind === "workspace" && ((i as { slug?: string }).slug || "personal") === slug) as { name?: string } | undefined)?.name ?? (slug === "personal" ? deskName() : slug);
  // shell2: in focus (a workspace's "New chat") the home is the input alone — no starters, no strip
  const shell2 = featureOn("shell2");
  const focused = useHomeFocus() && shell2;
  // shell2 (mobile G1): THE HOME OPENS AT THE TOP. The conversation's "scroll to the latest" ran on the
  // home too, so a phone opened on Files, under the composer and the Now strip. Once the home is
  // laid out (and once more after the desktop arrives), its scroller goes back to the top.
  const box = useRef<HTMLElement>(null);
  const loaded = !!desktop;
  useEffect(() => {
    if (!shell2) return;
    const top = () => {
      for (let el = box.current?.parentElement; el; el = el.parentElement) {
        if (el.scrollHeight > el.clientHeight && /(auto|scroll)/.test(getComputedStyle(el).overflowY)) { el.scrollTop = 0; break; }
      }
    };
    const a = requestAnimationFrame(() => { top(); requestAnimationFrame(top); });
    return () => cancelAnimationFrame(a);
  }, [shell2, loaded]);
  return (
    <section ref={box} data-home data-desktop-home="" className="vx-home vx-desk-home" aria-label="Home">
      {!focused && (greeting?.title || greeting?.text) && (
        <header className="vx-home-greet">
          {greeting.title && <h2>{greeting.title}</h2>}
          {greeting.text && <p>{greeting.text}</p>}
        </header>
      )}
      {composer && (home2
        ? <HomeComposer nameOf={nameOf}>{composer}</HomeComposer>
        : <div data-home-composer className="vx-home-composer">{composer}</div>)}
      {starters && !focused && <div data-home-starters className="vx-home-starters">{starters}</div>}
      {!focused && extra}
      {desktop && <DesktopView desktop={desktop} />}
    </section>
  );
}

/** `extra`: what the home shows under the starters besides the desktop (the People strip, item 15). */
export function DesktopHome({ active, greeting, starters, extra, fallback }: {
  active: boolean; greeting?: Greeting; starters?: ReactNode; extra?: ReactNode; fallback: ReactNode;
}) {
  const on = useFeature("desktop");
  const show = on && active;
  const { phase } = useDesktopState(false);
  // read fresh each time the home appears (15b: "read fresh at each page load")
  useEffect(() => { if (show) { void loadDesktop({ force: true }); void ensureSubject(); } }, [show]);
  if (!show || phase === "refused" || phase === "failed") return <>{fallback}</>;
  return <DesktopPage greeting={greeting} starters={starters} extra={extra} />;
}

/** The one toast host the desktop's Undo needs, mounted once by the shell while the feature is on
 *  (the Add to desktop items in the routine and file menus toast too, off the home), and the one card
 *  dialog host (`desktop_cards`): a tile, and the Make a card toast's Open, open the same dialog. */
export function DesktopToaster() {
  const on = useFeature("desktop");
  return on ? <><Toaster /><CardDetailHost /></> : null;
}
