"use client";
/** Vexa credits as a person sees them (feature `usage`, PLAN-8H 6.2; the approved mock is
 *  proto-on-product `desktop/proto/Usage.tsx`). Numbers only, no model names:
 *   - beside the account, "42% · 5h · 18% · week"; a popover with both windows, when each resets,
 *     and "+ extra usage";
 *   - in the chat, the limit card when a turn was refused for a full window: when it is back,
 *     "+ extra usage" (primary) and "Wait".
 *  Reads `GET /api/usage` (agent-api `credits.state`); "+ extra usage" is `POST /api/usage
 *  {extra: true}` — recorded only, nothing is charged. */
import { useEffect, useRef, useState } from "react";
import { useFeature } from "../../platform/features";
import "./usage.css";

export type Window = { pct: number; used?: number; allowance?: number | null; resets_at: number | null };
export type Usage = { five_h: Window; week: Window; extra: boolean; topup_credits?: number; extra_used?: number };
export const USAGE_EVENT = "vexa:terminal:usage";

const hm = (s: number) => new Date(s * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
const day = (s: number) => new Date(s * 1000).toLocaleDateString([], { weekday: "short", hour: "2-digit", minute: "2-digit" });
const pct = (w: Window) => Math.min(100, Math.round(w.pct || 0));
const full = (w: Window) => !!w.allowance && (w.pct || 0) >= 100;

export async function readUsage(): Promise<Usage | null> {
  try {
    const r = await fetch("/api/usage", { cache: "no-store" });
    if (!r.ok) return null;
    return asUsage(await r.json());
  } catch { return null; }
}

/** Only an answer of the meter's shape is drawn: anything else (an older server, a proxy page) is none. */
function asUsage(x: unknown): Usage | null {
  const u = x as Partial<Usage> | null;
  const win = (w: unknown) => !!w && typeof w === "object" && typeof (w as Window).pct === "number";
  return u && win(u.five_h) && win(u.week) ? (u as Usage) : null;
}

/** Turn extra usage on (recorded only — no payment) and tell every meter on the page. */
export async function turnOnExtraUsage(): Promise<Usage | null> {
  try {
    const r = await fetch("/api/usage", { method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ extra: true }) });
    if (!r.ok) return null;
    const u = asUsage(await r.json());
    if (!u) return null;
    window.dispatchEvent(new CustomEvent(USAGE_EVENT, { detail: u }));
    return u;
  } catch { return null; }
}

function useUsage(on: boolean): Usage | null {
  const [u, setU] = useState<Usage | null>(null);
  useEffect(() => {
    if (!on) return;
    let live = true;
    const read = () => void readUsage().then((x) => { if (live && x) setU(x); });
    read();
    const t = window.setInterval(read, 30_000);
    const onEvent = (e: Event) => setU((e as CustomEvent<Usage>).detail);
    window.addEventListener(USAGE_EVENT, onEvent);
    window.addEventListener("focus", read);
    return () => { live = false; window.clearInterval(t); window.removeEventListener(USAGE_EVENT, onEvent); window.removeEventListener("focus", read); };
  }, [on]);
  return on ? u : null;
}

function Bar({ label, w, five }: { label: string; w: Window; five: boolean }) {
  const isFull = full(w);
  const when = w.resets_at == null ? "Starts with your next message" : isFull ? `Back at ${hm(w.resets_at)}`
    : five ? `Resets at ${hm(w.resets_at)}` : `Resets ${day(w.resets_at)}`;
  return (
    <div className="vx-us-row" data-full={isFull ? "" : undefined}>
      <div className="vx-us-top"><b>{label}</b><span>{pct(w)}% used</span></div>
      <div className="vx-us-bar" role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct(w)}>
        <i style={{ width: `${pct(w)}%` }} /></div>
      <small>{when}</small>
    </div>
  );
}

/** "42% · 5h · 18% · week" beside the account; a popover with both windows. Hidden with `usage` off. */
export function UsageMeter() {
  const on = useFeature("usage");
  const u = useUsage(on);
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => { if (!box.current?.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, [open]);
  if (!on || !u) return null;
  const anyFull = full(u.five_h) || full(u.week);
  return (
    <div className="vx-us" ref={box} data-usage-meter="">
      <button type="button" className="vx-us-chip" data-full={anyFull ? "" : undefined} aria-expanded={open}
        onClick={() => setOpen((v) => !v)} title="How much of your plan's allowance is used">
        {pct(u.five_h)}% · 5h · {pct(u.week)}% · week</button>
      {open && (
        <div className="vx-us-pop" role="dialog" aria-label="Usage">
          <h4>Plan usage</h4>
          {full(u.five_h) && u.five_h.resets_at != null && <p className="vx-us-limit">You've reached this 5-hour limit — back at {hm(u.five_h.resets_at)}.</p>}
          {!full(u.five_h) && full(u.week) && u.week.resets_at != null && <p className="vx-us-limit">You've reached this week's limit — back {day(u.week.resets_at)}.</p>}
          <Bar label="5-hour window" w={u.five_h} five />
          <Bar label="This week" w={u.week} five={false} />
          <p className="vx-us-note">Rooms use their workspace owner's weekly pool, not yours.{u.extra ? " Extra usage is on." : ""}</p>
          <div className="vx-us-acts">
            {anyFull && <button type="button" className="vx-us-btn" onClick={() => setOpen(false)}>Wait</button>}
            <button type="button" className="vx-us-btn" data-primary={anyFull ? "" : undefined} disabled={u.extra}
              onClick={() => void turnOnExtraUsage()}>{u.extra ? "Extra usage on" : "+ extra usage"}</button>
          </div>
        </div>
      )}
    </div>
  );
}

/** A turn refused for a full window (agent-api `usage_window`): its sentence starts this way. */
export function isUsageLimit(f: { source?: string; kind?: string; detail?: string }): boolean {
  return f.source === "model-provider" && f.kind === "unpaid" && /^You've reached this (5-hour|weekly) limit/.test(f.detail ?? "");
}

/** The chat's limit card: when it is back, "+ extra usage" (recorded only), "Wait". After extra
 *  usage is on, "Send again" resends the kept message. */
export function UsageLimitCard({ detail, onRetry }: { detail: string; onRetry?: () => void }) {
  const [state, setState] = useState<"limit" | "extra" | "wait">("limit");
  const head = detail.split(". ")[0].replace(/\.$/, "");
  return (
    <div role="status" className="vx-us-card" data-usage-limit={state}>
      <b>{head}</b>
      {state === "limit" && <>
        <p>Your message is kept. Rooms keep working: they use their workspace owner's pool.</p>
        <div className="vx-us-acts">
          <button type="button" className="vx-us-btn" data-primary="" onClick={() => void turnOnExtraUsage().then((u) => { if (u) setState("extra"); })}>+ extra usage</button>
          <button type="button" className="vx-us-btn" onClick={() => setState("wait")}>Wait</button>
        </div>
      </>}
      {state === "extra" && <div className="vx-us-acts"><span>Extra usage is on.</span>
        {onRetry && <button type="button" className="vx-us-btn" data-primary="" onClick={onRetry}>Send again</button>}</div>}
      {state === "wait" && <p>Okay — send it again when the window resets.</p>}
    </div>
  );
}
