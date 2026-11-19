"use client";
/** THE TWO SWITCHES (UI 7hh, 0.13.4 S4): "Vexa in this room" and "Transcribe calls", in the room's ⋯.
 *
 *  A room of two: either person turns either OFF; turning it back ON asks the other person, who gets
 *  the card below (Turn on · Keep off). A bigger room: its owners switch; members see the state, and a
 *  policy lock says why. When either is off the header shows a quiet indicator. The server is the
 *  authority (`routers/room_people.py`); this reads it and sends the click. */
import { useCallback, useEffect, useState } from "react";
import type { MenuItem } from "../../ui-kit";
import { answerSwitch, lockWords, readSwitches, saidOf, setSwitch, type RoomSwitches, type SwitchName } from "../../surfaces/roomPeopleApi";
import "./roomPeople.css";
import "../../surfaces/tagCard.css";

const LABEL: Record<SwitchName, string> = { vexa: "Vexa in this room", transcribe: "Transcribe calls" };
const WORD: Record<SwitchName, string> = { vexa: "Vexa", transcribe: "call transcription" };

/** The room's switches, read on open and every 15 s while the room is in front. */
export function useRoomSwitches(session: string, on = true) {
  const [sw, setSw] = useState<RoomSwitches | null>(null);
  const [err, setErr] = useState("");
  const reload = useCallback(() => {
    if (!on) return;
    readSwitches(session).then((s) => { setSw(s); setErr(""); }).catch(() => setSw(null));
  }, [session, on]);
  useEffect(() => {
    reload();
    const t = window.setInterval(() => { if (!document.hidden) reload(); }, 15000);
    return () => window.clearInterval(t);
  }, [reload]);
  const flip = useCallback(async (name: SwitchName, value: boolean) => {
    setErr("");
    try { setSw(await setSwitch(session, name, value)); }
    catch (e) { setErr(saidOf(e) || "Couldn't change that switch."); }
  }, [session]);
  const answer = useCallback(async (name: SwitchName, accept: boolean) => {
    setErr("");
    try { setSw(await answerSwitch(session, name, accept)); }
    catch (e) { setErr(saidOf(e) || "Couldn't answer. Try again."); }
  }, [session]);
  return { sw, err, reload, flip, answer };
}

/** The ⋯ menu rows for the two switches: checked when on; the action each one offers in words. */
export function switchItems(sw: RoomSwitches | null, flip: (n: SwitchName, v: boolean) => void): MenuItem[] {
  if (!sw) return [];
  return (["vexa", "transcribe"] as const).map((name, i) => {
    const on = sw[name];
    const lock = sw.locks?.[name]?.[on ? "off" : "on"];
    const asked = sw.asks?.[name];
    let label = LABEL[name];
    let disabled = false;
    if (lock) { label = `${LABEL[name]} · ${lockWords(lock)}`; disabled = true; }
    else if (!sw.can_switch) { label = `${LABEL[name]} · ${on ? "on" : "off"} (owners change this)`; disabled = true; }
    else if (!on && sw.pair) label = asked?.mine ? `${LABEL[name]} (off) — asked, waiting` : `${LABEL[name]} (off) — ask to turn on`;
    return { key: `sw-${name}`, label, checked: on, disabled: disabled || (!!asked?.mine && !on),
      separatorBefore: i === 0, onSelect: () => flip(name, !on), data: { "data-room-switch": name, ...(lock ? { "data-locked": "" } : {}) } };
  });
}

/** Quiet header indicators: "Vexa off" · "Not transcribed". */
export function SwitchIndicators({ sw }: { sw: RoomSwitches | null }) {
  if (!sw || (sw.vexa && sw.transcribe)) return null;
  return (
    <span className="vx-rsw-ind">
      {!sw.vexa && <span className="vx-badge" data-indicator="vexa-off" title="Vexa is switched off in this room">Vexa off</span>}
      {!sw.transcribe && <span className="vx-badge" data-indicator="not-transcribed" title="Calls here are not transcribed">Not transcribed</span>}
    </span>
  );
}

/** The other person's consent card (a room of two): shown to the person who did NOT ask. */
export function SwitchAskCards({ sw, answer, err }: { sw: RoomSwitches | null; answer: (n: SwitchName, a: boolean) => void; err?: string }) {
  if (!sw) return null;
  const waiting = (["vexa", "transcribe"] as const).filter((n) => sw.asks?.[n]);
  if (!waiting.length) return null;
  return (
    <div className="vx-rsw-asks">
      {waiting.map((n) => {
        const a = sw.asks[n]!;
        if (a.mine) return <p key={n} className="vx-rsw-sent" data-ask-sent="">You asked to turn {WORD[n]} back on · waiting for the other person</p>;
        return (
          <div key={n} className="vx-tagcard" role="group" data-turn-on-card={n} aria-label={`${a.by_name} asks to turn ${WORD[n]} back on`}>
            <div className="vx-tagcard-head"><b className="vx-tagcard-title">{a.by_name} asks to turn {WORD[n]} back on in this room</b></div>
            <p className="vx-tagcard-note">{n === "vexa" ? "Vexa would read this conversation again (never your personal data)."
              : "Calls here would be transcribed and get notes again."}</p>
            <div className="vx-tagcard-acts">
              <button type="button" className="vx-btn" data-variant="primary" onClick={() => answer(n, true)}>Turn on</button>
              <button type="button" className="vx-btn" data-variant="ghost" onClick={() => answer(n, false)}>Keep off</button>
            </div>
          </div>
        );
      })}
      {err && <p className="vx-tagcard-err" role="alert">{err}</p>}
    </div>
  );
}
