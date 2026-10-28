"use client";
/** The card lab — the S5 card components on synthetic fixtures, with `fetch` stubbed for the card
 *  verbs (see fixtures.ts). For checking the rows, the sheet, the decision block, the inline card
 *  and the held Decline in a real browser at desktop and phone widths. Never served in production
 *  (page.tsx). Every call the components make is kept on `window.__cardLab.calls`. */
import { useEffect, useState } from "react";
import { InboxCards, NowCards, RoomBadge } from "../../../minutes/cards";
import { loadInbox, resetInbox } from "../../../minutes/cards/api";
import { loadDesktop } from "../../../minutes/desktop/desktopApi";
import { MdxDoc } from "../../../ui-kit/MdxDoc";
import { Toaster } from "../../../ui-kit/primitives/Toast";
import { CHAT_DOC, DESKTOP, PEOPLE, makeStub } from "./fixtures";
import "./lab.css";

type Stub = ReturnType<typeof makeStub>;
declare global { interface Window { __cardLab?: Stub } }

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

function install(): Stub {
  if (window.__cardLab) return window.__cardLab;
  const stub = makeStub();
  const real = window.fetch.bind(window);
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const path = url.replace(/^https?:\/\/[^/]+/, "");
    const m = /^\/api\/desktop\/card\/([a-z]+)$/.exec(path);
    if (m) {
      let body: Record<string, unknown> = {};
      try { body = init?.body ? JSON.parse(String(init.body)) : {}; } catch { body = {}; }
      await new Promise((r) => setTimeout(r, 60));   // a little latency, as over the network
      const r = stub.answer(m[1], body);
      return json(r.status, r.body);
    }
    if (path === "/api/people/mentionable") return json(200, { people: PEOPLE });
    if (path === "/api/desktop") return json(200, DESKTOP);
    return real(input, init);
  };
  window.__cardLab = stub;
  return stub;
}

export function CardsLab() {
  const [ready, setReady] = useState(false);
  const [theme, setTheme] = useState<"dark" | "light">("dark");
  useEffect(() => {
    install();
    const t = new URLSearchParams(window.location.search).get("theme");
    if (t === "light" || t === "dark") setTheme(t);
    void loadDesktop({ force: true });
    setReady(true);
  }, []);
  useEffect(() => {
    if (theme === "light") document.documentElement.setAttribute("data-theme", "light");
    else document.documentElement.removeAttribute("data-theme");
  }, [theme]);
  const reset = () => { window.__cardLab?.reset(); resetInbox(); void loadInbox({ force: true }); };

  if (!ready) return null;
  return <div className="lab-cards">
    <header className="lab-bar">
      <strong>Card lab</strong>
      <span className="lab-note">synthetic fixtures · stubbed card verbs</span>
      <span className="lab-grow" />
      <button type="button" className="vx-btn" data-variant="secondary" data-size="sm" onClick={() => setTheme(theme === "dark" ? "light" : "dark")}>
        {theme === "dark" ? "Light" : "Dark"}</button>
      <button type="button" className="vx-btn" data-variant="secondary" data-size="sm" onClick={reset}>Reset</button>
    </header>
    <main className="lab-main">
      <section className="lab-sec" aria-label="Now strip" data-lab="now">
        <h2 className="lab-h">Now strip</h2>
        <NowCards />
      </section>
      <section className="lab-sec" aria-label="Rooms" data-lab="badges">
        <h2 className="lab-h">Room badges</h2>
        <p className="lab-row"># growth <RoomBadge workspace="growth" /> · # launch <RoomBadge workspace="launch" /></p>
      </section>
      <section className="lab-sec" data-lab="inbox"><InboxCards roomName={(s) => (s === "lab-1" ? "planning" : null)} /></section>
      <section className="lab-sec" aria-label="Inline in chat" data-lab="inline">
        <h2 className="lab-h">Inline in chat</h2>
        <MdxDoc properties={false} outline={false}>{CHAT_DOC}</MdxDoc>
      </section>
    </main>
    <Toaster />
  </div>;
}
