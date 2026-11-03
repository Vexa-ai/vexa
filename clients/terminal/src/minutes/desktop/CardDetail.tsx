"use client";
/** A CARD, OPENED (#1783, desktop ADR §2.2/§2.10, D2; UI decisions 15c "opening it shows the full
 *  movement history").
 *
 *  Read fresh from crm-api (`/card/read`) each time it opens: its stage with the stages its type
 *  declares (Move stage offers only those, and only to a viewer crm says may transition; the move
 *  carries the revision that was read, so a card changed elsewhere is refused and re-read), the
 *  description, next and waiting, the face's data blocks, the sub-cards, the members (Add member for a
 *  viewer crm says may add; the person added sees it as an invite and accepts it themselves; adding a
 *  member never shares what the card is about), and the activity. One host listens for
 *  `OPEN_CARD_EVENT`, so a tile and the Make a card toast open the same dialog. */
import { useCallback, useEffect, useState } from "react";
import { useFeature } from "../../platform/features";
import type { CardView } from "../../platform/desktop";
import { Modal } from "../../ui-kit/Modal";
import { ago, dayWords } from "./faceBlocks";
import { addMember, loadPeople, OPEN_CARD_EVENT, readCard, updateCard, usePeople } from "./cardsApi";
import { CardMembers, FaceView, faceOf, Movement, openAbout, StateChip } from "./CardFace";

type Load = { phase: "loading" } | { phase: "failed" } | { phase: "ready"; card: CardView };

function AddMember({ card, onAdded }: { card: CardView; onAdded: () => void }) {
  const { people, nameOf } = usePeople();
  const [subject, setSubject] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { void loadPeople(); }, []);
  const on = new Set(card.members.map((m) => m.subject));
  const options = people.filter((p) => !on.has(p.subject));
  const submit = async () => {
    const s = subject.trim();
    if (!s || busy) return;
    setBusy(true);
    const ok = await addMember(card.id, s);
    setBusy(false);
    if (ok) { setSubject(""); onAdded(); }
  };
  return (
    <div className="vx-dk-addmember">
      <input className="vx-dk-input" role="combobox" aria-label="Person to add" aria-expanded={false} list={`vx-card-people-${card.id}`}
        placeholder="Add someone you work with" value={subject} onChange={(e) => setSubject(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter") void submit(); }} />
      <datalist id={`vx-card-people-${card.id}`}>
        {options.map((p) => <option key={p.subject} value={p.subject}>{nameOf(p.subject)}</option>)}
      </datalist>
      <button type="button" className="vx-kh-btn" disabled={!subject.trim() || busy} onClick={() => void submit()}>Add member</button>
    </div>
  );
}

export function CardDetail({ id, onClose }: { id: string; onClose: () => void }) {
  const [load, setLoad] = useState<Load>({ phase: "loading" });
  const { nameOf } = usePeople();
  const reload = useCallback(async () => {
    const c = await readCard(id);
    setLoad(c ? { phase: "ready", card: c } : { phase: "failed" });
  }, [id]);
  useEffect(() => { void reload(); void loadPeople(); }, [reload]);

  const title = load.phase === "ready" ? load.card.title : "Card";
  return (
    <Modal title={title} onClose={onClose} width={560}>
      {load.phase === "loading" && <div className="vx-dk-h" data-card-detail-loading="">Reading the card…</div>}
      {load.phase === "failed" && <div className="vx-dk-h" data-card-detail-failed="">This card can't be read right now, or you no longer have access to it.</div>}
      {load.phase === "ready" && (() => {
        const c = load.card;
        const stages = [...c.states, ...c.side_states];
        return (
          <div className="vx-dk-detail" data-card-detail={c.id}>
            <div className="vx-dk-top"><StateChip c={c} />{c.stalled && <span className="vx-dk-stalled">Stalled</span>}
              {c.due && <span className="vx-dk-duechip">Due {dayWords(c.due)}</span>}</div>
            <div className="vx-dk-stages" role="group" aria-label="Stage">
              {stages.map((s) => (
                c.can.transition && s !== c.state
                  ? <button key={s} type="button" className="vx-kh-btn" data-stage={s} aria-label={`Move to ${s}`}
                      onClick={() => { void updateCard(c, { transition: s }).then(() => reload()); }}>{s}</button>
                  : <span key={s} className="vx-kh-btn vx-dk-stage" data-stage={s} data-current={s === c.state ? "" : undefined}
                      aria-current={s === c.state ? "step" : undefined}>{s}</span>
              ))}
            </div>
            {c.summary && <p className="vx-dk-desc">{c.summary}</p>}
            {c.next && <div className="vx-dk-next">Next: {c.next}</div>}
            {c.waiting && <div className="vx-dk-next">Waiting on {c.waiting}</div>}
            <Movement card={c} />
            <div className="vx-dk-face">
              {faceOf(c).filter((b) => !["stepper", "next", "waiting", "movement", "people", "subcards", "status"].includes(b.type))
                .map((b, i) => <FaceView key={i} b={b} c={c} />)}
            </div>
            <div className="vx-dk-row">
              {c.about && <button type="button" className="vx-kh-btn vx-kh-btn-primary" onClick={() => { openAbout(c.about, c.title); onClose(); }}>Open what it's about</button>}
              {c.parent && <span className="vx-dk-h">Part of {c.parent.title}</span>}
              <span className="vx-dk-row-end"><CardMembers card={c} /></span>
            </div>

            {c.children.total > 0 && <>
              <div className="vx-dk-h">Sub-cards · {c.children.done} of {c.children.total} done</div>
              <ul className="vx-dk-sublist">
                {c.children.items.map((x) => (
                  <li key={x.id} className="vx-dk-subrow" data-subcard={x.id} data-done={x.done ? "" : undefined}>
                    <span className="vx-dk-subdot" aria-hidden />{x.title}<span className="vx-dk-h"> · {x.state}</span>
                  </li>
                ))}
              </ul>
            </>}

            <div className="vx-dk-h">Members</div>
            <ul className="vx-dk-sublist">
              {c.members.map((m) => (
                <li key={m.subject} className="vx-dk-subrow" data-card-member={m.subject}>
                  {nameOf(m.subject)}{m.status === "invited" && <span className="vx-dk-h"> · invited</span>}
                </li>
              ))}
            </ul>
            {c.can.members && <AddMember card={c} onAdded={() => void reload()} />}

            <div className="vx-dk-h">Activity</div>
            {(c.activity ?? []).length === 0
              ? <div className="vx-dk-h">Nothing has happened on this card yet.</div>
              : <ol className="vx-dk-history">
                  {(c.activity ?? []).map((a, i) => (
                    <li key={i} data-card-activity="" data-kind={a.kind}>
                      <span className="vx-dk-hwho">{a.kind === "agent" ? "Vexa" : nameOf(a.by)}</span> {a.what}
                      <span className="vx-dk-hwhen">{ago(a.at)}</span>
                    </li>
                  ))}
                </ol>}
            <a className="vx-dk-h vx-dk-crmlink" href={c.href}>Open in the CRM</a>
          </div>
        );
      })()}
    </Modal>
  );
}

/** The one dialog host: opens whatever card `OPEN_CARD_EVENT` names (while `desktop_cards` is on). */
export function CardDetailHost() {
  const on = useFeature("desktop_cards");
  const [open, setOpen] = useState<string | null>(null);
  useEffect(() => {
    if (!on) return;
    const handler = (e: Event) => {
      const id = (e as CustomEvent<{ id?: unknown }>).detail?.id;
      if (typeof id === "string" && id) setOpen(id);
    };
    window.addEventListener(OPEN_CARD_EVENT, handler);
    return () => window.removeEventListener(OPEN_CARD_EVENT, handler);
  }, [on]);
  if (!on || !open) return null;
  return <CardDetail key={open} id={open} onClose={() => setOpen(null)} />;
}
