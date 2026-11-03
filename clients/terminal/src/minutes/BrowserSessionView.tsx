"use client";
/** The live view of a browser session: the person signs in inside it, and the status line says when
 *  the sign-in was saved as a bot account. Mounted once in the shell; opens on BROWSER_SESSION_OPEN. */
import { useEffect, useState } from "react";
import { connectionStyle as cs } from "./connectionStyles";
import { type as ty, T } from "./tokens";
import { useFeature } from "../platform/features";
import { BOT_ACCOUNTS_CHANGED, BROWSER_SESSION_OPEN, PLATFORM_NAMES, readOpenDetail, viewSrc, type BotPlatform, type BrowserSessionOpen } from "./browserSession";

type Status = "starting" | "ready" | "saved" | "failed" | "closed";
const FINAL: Status[] = ["saved", "failed", "closed"];
const LINE: Record<Status, string> = {
  starting: "Starting a secure browser…",
  ready: "Sign in below.",
  saved: "Signed in — saved as a bot account",
  failed: "Sign-in did not complete.",
  closed: "This browser session has ended.",
};

/** Not mounted while `browser_bots` is switched off (`platform/features`): an open request is ignored. */
export function BrowserSessionView(p: { pollMs?: number } = {}) {
  return useFeature("browser_bots") ? <BrowserSessionBody {...p} /> : null;
}

function BrowserSessionBody({ pollMs = 2000 }: { pollMs?: number }) {
  const [session, setSession] = useState<BrowserSessionOpen | null>(null);
  const [status, setStatus] = useState<Status>("starting");
  const [detail, setDetail] = useState("");

  useEffect(() => {
    const open = (event: Event) => {
      const next = readOpenDetail((event as CustomEvent).detail);
      if (!next) return;
      setSession(next); setStatus("starting"); setDetail("");
    };
    window.addEventListener(BROWSER_SESSION_OPEN, open);
    return () => window.removeEventListener(BROWSER_SESSION_OPEN, open);
  }, []);

  const id = session?.session_id;
  const final = FINAL.includes(status);
  useEffect(() => {
    if (!id || final) return;
    let stopped = false;
    const poll = async () => {
      try {
        const r = await fetch(`/api/bot-accounts/sessions/${id}`, { cache: "no-store" });
        if (stopped) return;
        if (r.status === 404) { setStatus("closed"); return; }
        if (!r.ok) return;
        const data = await r.json();
        if (stopped || !(data.status in LINE)) return;
        if (typeof data.vnc_password === "string" && data.vnc_password) {
          setSession((s) => (s && !s.vnc_password ? { ...s, vnc_password: data.vnc_password, platform: s.platform || String(data.platform || "") } : s));
        }
        setStatus(data.status);
        setDetail(typeof data.detail === "string" ? data.detail : "");
        if (data.status === "saved") window.dispatchEvent(new CustomEvent(BOT_ACCOUNTS_CHANGED));
      } catch { /* the next poll tries again */ }
    };
    void poll();
    const timer = setInterval(() => void poll(), pollMs);
    return () => { stopped = true; clearInterval(timer); };
  }, [id, final, pollMs]);

  if (!session) return null;
  const close = () => {
    if (!final) void fetch(`/api/bot-accounts/sessions/${session.session_id}`, { method: "DELETE", cache: "no-store" }).catch(() => {});
    setSession(null);
  };
  const name = PLATFORM_NAMES[session.platform as BotPlatform] ?? "your account";
  const tone = status === "saved" ? "c-success" : status === "failed" ? "c-danger" : "c-3";
  const { color: _metaTone, ...metaType } = ty.meta;
  return <div className="vx-dialog-layer">
    <div className="vx-scrim" aria-hidden />
    <section role="dialog" aria-label="Sign in a bot account" className="bg-1 c-1 bd r-lg" style={{ ...ty.body, position: "relative", display: "flex", flexDirection: "column", width: "min(1100px, 94vw)", height: "min(820px, 92vh)", overflow: "hidden" }}>
      <header className="pt-0 pr-4 pb-0 pl-4 bd-b" style={{ display: "flex", alignItems: "center", gap: 12, height: T.headerH, flex: "none" }}>
        <h2 className="m-0" style={{ ...ty.title }}>Sign in to {name}</h2>
        <p role="status" data-session-status={status} className={`m-0 ${tone}`} style={{ ...metaType, flex: 1, minWidth: 0 }}>
          {LINE[status]}{status === "failed" && detail ? " " + detail : ""}
        </p>
        <button style={cs.button} onClick={close}>{final ? "Done" : "Close"}</button>
      </header>
      {status === "ready" && session.vnc_password
        ? <iframe title={`${name} sign-in browser`} src={viewSrc(session.session_id, session.vnc_password)} className="bd-none bg-0" style={{ flex: 1, width: "100%" }} />
        : <div className="c-3 p-6" style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center" }}>{LINE[status]}</div>}
    </section>
  </div>;
}
