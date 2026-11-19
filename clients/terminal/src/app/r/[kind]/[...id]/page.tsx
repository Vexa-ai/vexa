"use client";
/** `/r/<kind>/<id>` — a universal reference opened from anywhere (a mail, a calendar body, another
 *  app). The server says what it is for this reader; the page goes there, or says one sentence and,
 *  when an owner can be asked, offers to ask. A reader who is not signed in gets the sign-in the
 *  shell gives everywhere, and comes back to this same address. See `../../refRoute.ts`. */
import { useEffect, useState } from "react";
import { App } from "../../../App";
import { resolveRef, requestRefAccess } from "../../../../data/workspaceApi";
import { OPEN_SESSION_KEY, refLanding, type RefLanding } from "../../refRoute";

export default function RefRoutePage() {
  const [landing, setLanding] = useState<RefLanding | "signin" | null>(null);
  const [asked, setAsked] = useState(false);
  const ref = typeof window !== "undefined" ? window.location.pathname : "";
  useEffect(() => {
    let on = true;
    resolveRef(ref).then((a) => {
      if (!on) return;
      const l = refLanding(a);
      if ("go" in l) {
        if (l.session) { try { localStorage.setItem(OPEN_SESSION_KEY, l.session); } catch { /* the shell opens its default */ } }
        window.location.replace(l.go);
        return;
      }
      setLanding(l);
    }, (e: { status?: number }) => { if (on) setLanding(e?.status === 401 ? "signin" : refLanding(null)); });
    return () => { on = false; };
  }, [ref]);
  if (landing === "signin") return <App />;
  if (!landing || "go" in landing) return <main className="vx-ref-landing" />;
  return (
    <main className="vx-ref-landing">
      <p>{landing.say}</p>
      {landing.ask && (asked
        ? <p>Access requested. The owner has been told.</p>
        : <button type="button" onClick={() => { void requestRefAccess(ref).then(() => setAsked(true), () => undefined); }}>Request access</button>)}
      <p><a href="/">Go to Vexa</a></p>
    </main>
  );
}
