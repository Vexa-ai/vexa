"use client";
/** "REPORT THIS" — the person's half of the rough-edges loop (PRD decision 33 §2).
 *
 *  Founder: the agent files its own rough edges; *"people file too — a 'report this' action on any
 *  turn or page in the terminal — one line, the same record with the human surface attached."*
 *
 *  ONE LINE, NO DIALOG. The whole control is a text field that appears where the person already is,
 *  takes a sentence, and confirms in four words. Everything else the record needs — chat, page,
 *  workspace, meeting — the client already knows and attaches silently (`frictionApi.ts`). A modal
 *  with a category dropdown and a severity picker is a report nobody files: the cost of reporting
 *  has to stay below the cost of shrugging, or the channel measures only the most annoyed people.
 *
 *  TWO PLACEMENTS, ONE COMPONENT: a chat turn (what the agent just said was wrong) and the panel's
 *  page header (this page is wrong, or is not the page I asked for). They differ only in what they
 *  attach, so `ReportField` is shared and the two exports are the affordances that open it.
 *
 *  IT NEVER BLOCKS AND NEVER THROWS. A failure to send says "couldn't send that" beside the field
 *  with the text still in it — an error dialog on top of the thing the person is already unhappy
 *  about is the product being broken twice.
 */
import type { CSSProperties } from "react";
import { useEffect, useRef, useState } from "react";
import { Icon } from "../ui-kit";
import { ContextMenu } from "../ui-kit/ContextMenu";
import { confirmation, reportFriction, type FrictionSurface } from "./frictionApi";

/** The two terminal tokens this control needs, spelled out rather than imported.
 *  `minutes/tokens.ts` is the panel's design system and `minutes/` already imports FROM here — a
 *  return edge would make the two directories mutually dependent for two style objects. */
const ty = {
  chip: { fontFamily: "var(--sans)", fontSize: 12, fontWeight: 500 } as CSSProperties,
  meta: { fontFamily: "var(--sans)", fontSize: 11, fontWeight: 400, color: "var(--t3)" } as CSSProperties,
};
const surf = { raised: "var(--panel)" };

const iconBtn: CSSProperties = {
  flex: "none", width: 26, height: 24, display: "flex", alignItems: "center", justifyContent: "center",
  background: "transparent", border: "none", borderRadius: 6, color: "var(--t3)", cursor: "pointer",
  padding: 0, transition: "color .12s, background .12s, opacity .12s",
};
const lit = (e: { currentTarget: HTMLElement }) => { e.currentTarget.style.color = "var(--t1)"; e.currentTarget.style.background = surf.raised; };
const dim = (e: { currentTarget: HTMLElement }) => { e.currentTarget.style.color = "var(--t3)"; e.currentTarget.style.background = "transparent"; };

export const REPORT_LABEL = "Report this";
export const REPORT_HINT = "Report this — one line about what did not work";

/** THE FIELD. Open, type, Enter. Escape closes it and keeps nothing — a half-typed complaint is not
 *  a draft anybody wants restored three days later. */
function ReportField({ surface, onDone }: { surface: FrictionSurface; onDone: () => void }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => { input.current?.focus(); }, []);
  // The confirmation is the last thing this control does. It stays long enough to read and then the
  // control removes itself — a report that leaves a permanent artefact on the screen makes the
  // person feel they have to tidy it up.
  useEffect(() => {
    if (said === null) return;
    const t = setTimeout(onDone, 1800);
    return () => clearTimeout(t);
  }, [said, onDone]);

  const send = async () => {
    if (!text.trim() || busy) return;
    setBusy(true);
    setSaid(confirmation(await reportFriction(text, surface)));
    setBusy(false);
  };

  if (said !== null) {
    return <span data-report="said" style={{ ...ty.meta, color: "var(--t3)" }}>{said}</span>;
  }
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 6, minWidth: 0, flex: "1 1 220px" }}>
      <input ref={input} data-report="field" aria-label={REPORT_HINT} placeholder="What did not work?"
        value={text} disabled={busy} onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") { e.preventDefault(); void send(); }
          // consumed, so the panel's own Escape (close-topmost) does not also fire
          if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); onDone(); }
        }}
        style={{
          ...ty.chip, flex: "1 1 0%", minWidth: 0, padding: "3px 8px", borderRadius: 6,
          border: "1px solid var(--line2)", background: "var(--bg)", color: "var(--t1)", outline: "none",
        }} />
      <button data-report="send" onClick={() => void send()} disabled={busy || !text.trim()}
        title="Send" aria-label="Send the report"
        style={{ ...ty.chip, flex: "none", border: "none", borderRadius: 6, padding: "3px 10px", cursor: "pointer",
                 color: "var(--on-accent)", background: "var(--accent)", opacity: busy || !text.trim() ? 0.55 : 1 }}>
        {busy ? "…" : "Send"}
      </button>
    </span>
  );
}

/** THE PANEL HEADER ACTION (decision 33 §2) — this page. Sits in the document header's utility
 *  group beside Extend, because it acts on the same thing they do: what is in front. */
export function ReportPageButton(p: { workspace?: string; path: string; chat?: string }) {
  const [open, setOpen] = useState(false);
  if (open) {
    return <ReportField onDone={() => setOpen(false)}
      surface={{ at: "page", workspace: p.workspace, path: p.path, chat: p.chat }} />;
  }
  return (
    <button data-doc-act="report" aria-label={REPORT_LABEL} title={REPORT_HINT}
      onClick={() => setOpen(true)} style={iconBtn} onMouseEnter={lit} onMouseLeave={dim}>
      <Icon name="alert" size={14} />
    </button>
  );
}

/** THE TURN ACTION (decision 33 §2) — what the agent just said, and everything the client knows
 *  about where it said it.
 *
 *  Two ways in, on purpose. The hover button is the discoverable one; the right-click menu is the
 *  one every other list and tree in this terminal already has, and a surface where right-click does
 *  nothing reads as unfinished. Both open the same field, which is the point of having one. */
export function ReportTurn(p: { surface: FrictionSurface; children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const [hover, setHover] = useState(false);
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null);
  return (
    <div data-report-turn style={{ position: "relative" }}
      onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}
      onContextMenu={(e) => { e.preventDefault(); e.stopPropagation(); setMenu({ x: e.clientX, y: e.clientY }); }}>
      {p.children}
      {!open && (hover || menu) && (
        <button data-report="open" aria-label={REPORT_LABEL} title={REPORT_HINT}
          onClick={() => setOpen(true)}
          style={{ ...iconBtn, position: "absolute", top: -2, right: -2, opacity: 0.85 }}
          onMouseEnter={lit} onMouseLeave={dim}>
          <Icon name="alert" size={13} />
        </button>
      )}
      {open && (
        <div style={{ display: "flex", alignItems: "center", gap: 6, margin: "6px 0 0", minWidth: 0 }}>
          <ReportField surface={p.surface} onDone={() => setOpen(false)} />
        </div>
      )}
      {menu && (
        <ContextMenu x={menu.x} y={menu.y} onClose={() => setMenu(null)}
          items={[{ id: "report", label: REPORT_LABEL, detail: "one line — it goes to the fix queue",
                    onSelect: () => setOpen(true) }]} />
      )}
    </div>
  );
}
