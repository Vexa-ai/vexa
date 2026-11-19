"use client";
/** Participants — who is in this conversation, beside the chat's name (ADR-0048).
 *
 *  Read-only: it lists the people (and agents) in the chat, lets the owner remove someone and lets
 *  anyone else leave. People are ADDED from the composer, by @-mentioning them — the message's
 *  Enter asks once whether to share the chat (`surfaces/MentionUI.tsx`), and that question carries
 *  what sharing means. There is no add-by-email here: nobody outside the people a person shares a
 *  workspace with is ever offered, and inviting someone new is a separate act.
 *
 *  The server is the authority on every rule here (only the owner removes; the owner stays); the
 *  buttons merely do not offer what it would refuse. A store that is not configured answers 503 and
 *  the control says sharing is not available rather than showing an empty list. */
import { useEffect, useRef, useState } from "react";
import { ApiError } from "../data/apiClient";
import { listParticipants, removeParticipant, type Participant } from "../surfaces/sessionsApi";
import "./minutes.css";

type Props = { session: string; count?: number };

export function Participants({ session, count }: Props) {
  const [me, setMe] = useState<string | undefined>(undefined);
  const [open, setOpen] = useState(false);
  const [people, setPeople] = useState<Participant[] | null>(null);
  const [role, setRole] = useState<string | null>(null);
  const [names, setNames] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const box = useRef<HTMLDivElement>(null);

  // READ ON OPENING THE CHAT, not only on opening the list (#1783): a shared chat said "Just you"
  // until the person clicked it. Read again every 30 s, so someone joining shows.
  useEffect(() => {
    let live = true;
    setNote("");
    const read = () => listParticipants(session).then((v) => {
      if (!live) return;
      setPeople(v.participants);
      setRole(v.role ?? null);
      setMe(v.you);
      setNames(v.names ?? {});
    }).catch((e) => {
      if (!live) return;
      if (e instanceof ApiError && e.status === 404) { setPeople([{ principal: "you", kind: "human", role: "owner" }]); setRole("owner"); setMe("you"); return; }
      if (e instanceof ApiError && e.status === 503) { setPeople(null); setNote("Sharing chats is not available on this deployment."); return; }
      if (open) setNote("Couldn't load who is in this chat.");
    });
    void read();
    const t = window.setInterval(() => { if (!document.hidden) void read(); }, 30000);
    return () => { live = false; window.clearInterval(t); };
  }, [open, session]);

  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    window.addEventListener("mousedown", away);
    return () => window.removeEventListener("mousedown", away);
  }, [open]);

  const owner = role === "owner";
  const shown = people?.length ? people.length : count || 1;
  const nameOf = (p: Participant) => p.kind === "agent" ? `@${p.principal}` : p.principal === me ? "You" : (names[p.principal] || p.principal);
  // WHO IS IN IT, not a count (founder, 2026-10-14: the badge shows who is in the chat).
  const humans = (people ?? []).filter((p) => p.kind === "human");
  const label = shown === 1 || humans.length < 2 ? "Just you"
    : humans.length <= 3 ? humans.map(nameOf).join(", ") : `${nameOf(humans[0])}, ${nameOf(humans[1])} +${humans.length - 2}`;

  async function remove(p: Participant) {
    setBusy(true); setNote("");
    try {
      const v = await removeParticipant(session, p.principal);
      setPeople(v.participants ?? (people ?? []).filter((x) => x.principal !== p.principal));
    } catch { setNote("Couldn't remove them. Try again."); }
    finally { setBusy(false); }
  }

  // NOTHING TO SAY WHILE IT IS JUST YOU (founder, 2026-10-14: declutter the header). The badge
  // appears once someone else is in the chat; people are added and removed in the chat itself.
  if (humans.length < 2 && !open) return null;

  return (
    <div ref={box} style={{ position: "relative", flex: "none" }}>
      <button aria-label={`People in this chat: ${shown}`} aria-expanded={open} onClick={() => setOpen(!open)}
        className="vx-people-toggle">
        {label}
      </button>
      {open && (
        <div role="dialog" aria-label="People in this chat"
          className="vx-people-pop" style={{ position: "absolute", right: 0, top: "calc(100% + 4px)", width: 280 }}>
          {people && people.length > 0 && (
            <ul className="vx-people-list" style={{ display: "grid", gap: 4 }}>
              {people.map((p) => (
                <li key={p.principal} className="vx-people-row" style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis" }}>
                    {nameOf(p)}
                  </span>
                  <span className="vx-people-role">{p.role}</span>
                  {p.role !== "owner" && (owner || p.principal === me) && (
                    <button aria-label={p.principal === me ? "Leave this chat" : `Remove ${nameOf(p)}`} disabled={busy}
                      onClick={() => remove(p)}
                      className="vx-people-act">
                      {p.principal === me ? "Leave" : "Remove"}
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}
          {note && <p role="alert" className="vx-people-note">{note}</p>}
        </div>
      )}
    </div>
  );
}
