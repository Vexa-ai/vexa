"use client";
/** MINUTES — room creation as a PROACTIVE CONVERSATION, not a form.
 *
 *  The assistant asks; the person answers in their own words; the assistant picks the right seed
 *  shape for the use case from what it hears and says which one it chose. The conversation IS the
 *  onboarding (founder ruling 2026-08-21: "it asks proactive questions and should pick up the
 *  right seed room for that use case").
 *
 *  v-laptop: the interviewer is a deterministic script + keyword classifier, so the flow works with
 *  no model credential. The BYOT agent takes over the same conversation later — the surface, the
 *  message shapes and the seed contract stay; only the brain upgrades.
 */
import { useEffect, useRef, useState } from "react";
import { Modal } from "../ui-kit/Modal";
import { createSharedWorkspace, mintInvite, setWorkspacePurpose } from "./workspaceApi";
import { presentError } from "./apiClient";

type Who = "a" | "u";
type Msg = { who: Who; text: string };
type Phase = "name" | "about" | "focus" | "who" | "emails" | "creating" | "done";
type Mode = "org" | "list" | "open";

/** The seed shapes. The classifier picks one from the person's own description; the assistant
 *  names its pick out loud so a wrong guess is visible and correctable in one reply. */
const SHAPES = {
  decisions: {
    label: "a decision-making review",
    focus: ["Decisions and who owns them", "Open questions that carry across weeks", "What changed since last time"],
    match: /decision|review|architect|planning|steer|priorit|roadmap|budget/i,
  },
  standup: {
    label: "a recurring status meeting",
    focus: ["Blockers and who unblocks them", "What each person committed to", "Anything routed to another meeting"],
    match: /standup|stand-up|status|sync|daily|weekly check|check-?in/i,
  },
  counterparty: {
    label: "a meeting with an outside party",
    focus: ["What was promised, by which side", "Open items and their deadlines", "Anything that changes the relationship"],
    match: /vendor|client|customer|supplier|negotiat|sales|partner|external|contract|renewal/i,
  },
  people: {
    label: "a people conversation",
    focus: ["Agreements and follow-ups", "Themes that repeat across sessions", "What each person is waiting on"],
    match: /1:1|one.on.one|hiring|interview|candidate|coaching|feedback|performance/i,
  },
  project: {
    label: "a project workspace",
    focus: ["Milestones and their state", "Risks somebody named", "Who is waiting on whom"],
    match: /project|launch|delivery|milestone|sprint|release|migration|rollout/i,
  },
  generic: {
    label: "a working meeting",
    focus: ["Decisions and commitments", "Open questions", "What changed since last time"],
    match: /$^/,
  },
} as const;
type ShapeKey = keyof typeof SHAPES;

function classify(text: string): ShapeKey {
  for (const [k, v] of Object.entries(SHAPES)) if (k !== "generic" && v.match.test(text)) return k as ShapeKey;
  return "generic";
}
const addressOf = (name: string) =>
  (name.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "room") + "@meetings.local";

export function RoomOnboarding({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [phase, setPhase] = useState<Phase>("name");
  const [msgs, setMsgs] = useState<Msg[]>([
    { who: "a", text: "Let’s scaffold a workspace. What should it be called?\n\nA workspace usually carries the name of the meeting series or the work it holds — “Architecture review”, “Acme renewal”, “Monday standup”." },
  ]);
  const [input, setInput] = useState("");
  const [name, setName] = useState("");
  const [shape, setShape] = useState<ShapeKey>("generic");
  const [focus, setFocus] = useState<string[]>([]);
  const [mode, setMode] = useState<Mode | null>(null);
  const [emails, setEmails] = useState("");
  const [invite, setInvite] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const scroller = useRef<HTMLDivElement>(null);
  useEffect(() => { scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: "smooth" }); }, [msgs, phase]);

  const say = (text: string) => setMsgs((m) => [...m, { who: "a", text }]);
  const heard = (text: string) => setMsgs((m) => [...m, { who: "u", text }]);

  const submitText = () => {
    const t = input.trim();
    if (!t) return;
    setInput("");
    heard(t);
    if (phase === "name") {
      setName(t);
      say(`“${t}” it is — its address is ${addressOf(t)}, and inviting that address to the meeting is the whole setup.\n\nNow tell me about the meeting itself: what happens there, roughly?`);
      setPhase("about");
      return;
    }
    if (phase === "about") {
      const k = classify(t);
      setShape(k); setFocus([...SHAPES[k].focus]);
      say(`Sounds like ${SHAPES[k].label}. I’ll pay attention to:\n\n${SHAPES[k].focus.map((f) => `• ${f}`).join("\n")}\n\nAnything to add or change? Say it in your own words — or “looks right”.`);
      setPhase("focus");
      return;
    }
    if (phase === "focus") {
      if (!/^(looks right|ok|okay|yes|good|fine|right|correct)\b/i.test(t)) {
        setFocus((f) => [...f, t]);
        say("Added. Anything else — or “looks right”?");
        return;
      }
      say("Good. Last thing — who belongs in this workspace?");
      setPhase("who");
      return;
    }
    if (phase === "emails") {
      setEmails(t);
      void create("list", t);
      return;
    }
  };

  const pickMode = (m: Mode) => {
    setMode(m);
    heard(m === "org" ? "Anyone from our organisation" : m === "list" ? "A list I’ll name" : "Anyone in the meeting");
    if (m === "list") {
      say("List their addresses, separated by commas. They’ll get their first extract after the next meeting — nothing to accept, no account until they open one.");
      setPhase("emails");
    } else {
      void create(m, "");
    }
  };

  const create = async (m: Mode, emailStr: string) => {
    setPhase("creating");
    say("Setting it up…");
    try {
      const made = await createSharedWorkspace(name);
      const wsId = made.workspace_id;
      const purposeLine = msgs.filter((x) => x.who === "u")[1]?.text ?? name;  // the "about" answer
      try { await setWorkspacePurpose(wsId, purposeLine.slice(0, 140)); } catch { /* non-fatal */ }
      try {
        await fetch("/api/minutes/seed", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ wsId, name, purpose: purposeLine, matters: focus.join("\n"), kind: shape }),
        });
      } catch { /* dev seam; non-fatal */ }
      const list = emailStr.split(/[\s,;]+/).map((e) => e.trim()).filter(Boolean);
      // SUBSTRATE GAP (PRD §3a): no domain-scoped invites — "organisation" maps to an open link.
      const minted = await mintInvite({
        workspace_id: wsId,
        mode: m === "list" ? "restricted" : "open",
        ...(m === "list" && list.length ? { allowed_emails: list } : {}),
      });
      const url = typeof window !== "undefined" ? `${window.location.origin}/?invite=${minted.token}` : minted.token;
      setInvite(url);
      say(`Done. ${name} exists and it knows what to watch for.\n\nInvite ${addressOf(name)} to the meeting it serves, or hand people this join link:\n${url}\n\nNext time we can run a test meeting so you see the whole loop before the real one.`);
      setPhase("done");
      onCreated();
    } catch (e) {
      setErr(presentError(e).headline);
      say("That didn’t work — see the error below. Fix and I’ll try again.");
      setPhase("who");
    }
  };

  const chip = (label: string, onClick: () => void, primary = false) => (
    <button key={label} onClick={onClick}
      style={{ background: primary ? "var(--accent)" : "var(--panel)", color: primary ? "var(--bg)" : "var(--t2)",
        border: primary ? "none" : "1px solid var(--line)", borderRadius: 999, padding: "6px 13px", fontSize: 12.5,
        cursor: "pointer", fontWeight: primary ? 600 : 400 }}>{label}</button>
  );

  return (
    <Modal title="New workspace" onClose={onClose} width={470}>
      <div style={{ display: "flex", flexDirection: "column", height: 430 }}>
        <div ref={scroller} style={{ flex: 1, overflowY: "auto", display: "flex", flexDirection: "column", gap: 12, paddingRight: 4 }}>
          {msgs.map((m, i) => (
            <div key={i} style={{ alignSelf: m.who === "u" ? "flex-end" : "flex-start", maxWidth: "88%" }}>
              <div style={{ fontSize: 12.5, lineHeight: 1.6, whiteSpace: "pre-wrap", wordBreak: "break-word",
                background: m.who === "u" ? "var(--panel2)" : "transparent",
                color: m.who === "u" ? "var(--t1)" : "var(--t2)",
                borderRadius: 8, padding: m.who === "u" ? "7px 11px" : "0" }}>{m.text}</div>
            </div>
          ))}
          {err && <div role="alert" style={{ fontSize: 12, color: "var(--danger)", background: "var(--dangerbg)", borderRadius: 8, padding: "8px 10px" }}>⚠ {err}</div>}
        </div>
        {phase === "who" && (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 7, padding: "10px 0 2px" }}>
            {chip("Anyone from our organisation", () => pickMode("org"), true)}
            {chip("A list I’ll name", () => pickMode("list"))}
            {chip("Anyone in the meeting", () => pickMode("open"))}
          </div>
        )}
        {phase === "focus" && (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 7, padding: "10px 0 2px" }}>
            {chip("Looks right", () => { setInput("looks right"); setTimeout(() => { const t = "looks right"; setMsgs((m) => [...m, { who: "u", text: t }]); say("Good. Last thing — who belongs in this workspace?"); setPhase("who"); setInput(""); }, 0); }, true)}
          </div>
        )}
        {phase === "done" ? (
          <div style={{ display: "flex", justifyContent: "flex-end", paddingTop: 10 }}>
            {chip("Done", onClose, true)}
          </div>
        ) : phase !== "who" && phase !== "creating" ? (
          <div style={{ display: "flex", gap: 8, paddingTop: 10 }}>
            <input autoFocus value={input} disabled={phase === ("creating" as Phase)}
              placeholder={phase === "name" ? "Architecture review" : phase === "emails" ? "ana@bank.example, jonas@bank.example" : "Type your answer…"}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") submitText(); }}
              style={{ flex: 1, fontSize: 13, padding: "9px 11px", background: "var(--bg)", border: "1px solid var(--line2)", borderRadius: 8, color: "var(--t1)", outline: "none" }} />
            {chip("Send", submitText, true)}
          </div>
        ) : null}
      </div>
    </Modal>
  );
}
