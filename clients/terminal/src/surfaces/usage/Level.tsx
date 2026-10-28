"use client";
/** The model LEVEL a chat runs on, for hosted plans (feature `levels`, PLAN-8H 6.3; the approved mock
 *  is proto-on-product `desktop/proto/Level.tsx`). Founder 2026-10-15: "for saas version we will do
 *  the 1–5 models range… choose model in the beginning of the chat because of prompt caching".
 *   - A NEW chat shows "1 · 2 · 3 · 4 · 5" and the word (fastest … most capable).
 *   - Once it has messages the level is locked, shown as "Level 3". Changing it offers "Start a new
 *     chat at level N" or "Switch (resets the cache)".
 *  Reads `GET /api/models/levels?session=`; picks with `POST /api/chat/level`. The chat's composer
 *  shows this in place of the model picker when `levels` is on. */
import { useEffect, useState } from "react";
import { LEVEL_SET_EVENT } from "../../platform/events";
import "./usage.css";

export const LEVEL_WORD = ["", "fastest", "fast", "balanced", "thorough", "most capable"];
type Row = { level: number; word: string; model: string | null };
type Answer = { levels: Row[]; chat?: { level: number; model: string } | null };

const read = (session: string) => fetch(`/api/models/levels?session=${encodeURIComponent(session)}`, { cache: "no-store" })
  .then((r) => (r.ok ? r.json() : null))
  .then((x: Answer | null) => (x && Array.isArray(x.levels) ? x : null)).catch(() => null);
const pick = (session: string, level: number, sw: boolean) => fetch("/api/chat/level", {
  method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ session, level, switch: sw }),
}).then((r) => r.ok).catch(() => false);

/** `hasMessages`: the chat has had a turn, so its level is fixed. `onNewChat(level)`: start a new chat
 *  that will take this level (absent: only Switch is offered). */
export function LevelPicker({ session, hasMessages, defaultLevel = 3, onNewChat }: {
  session: string; hasMessages: boolean; defaultLevel?: number; onNewChat?: (level: number) => void;
}) {
  const [a, setA] = useState<Answer | null>(null);
  const [menu, setMenu] = useState(false);
  const [want, setWant] = useState<number | null>(null);
  useEffect(() => {
    let live = true;
    void read(session).then((x) => { if (live) setA(x); });
    // a new chat started "at level N" from another chat's menu: the shell set it; show it
    const onSet = (e: Event) => {
      const d = (e as CustomEvent<{ session?: string }>).detail;
      if (d?.session === session) void read(session).then((x) => { if (live && x) setA(x); });
    };
    window.addEventListener(LEVEL_SET_EVENT, onSet);
    return () => { live = false; window.removeEventListener(LEVEL_SET_EVENT, onSet); };
  }, [session]);
  if (!a || !a.levels.some((r) => r.model)) return null;
  const level = a.chat?.level ?? defaultLevel;
  const choose = async (n: number, sw: boolean) => {
    if (await pick(session, n, sw)) setA({ ...a, chat: { level: n, model: a.levels[n - 1]?.model ?? "" } });
  };
  if (!hasMessages) return (
    <div className="vx-lv" role="radiogroup" aria-label="Model level" data-level-picker=""
      title={`Level ${level} · ${LEVEL_WORD[level]} — chosen at the start, for the chat's cache`}>
      {[1, 2, 3, 4, 5].map((n) => (
        <button key={n} type="button" role="radio" aria-checked={level === n} data-on={level === n ? "" : undefined}
          title={`Level ${n} · ${LEVEL_WORD[n]}`} onClick={() => void choose(n, true)}>{n}</button>))}
      <span className="vx-lv-word">{LEVEL_WORD[level]}</span>
    </div>
  );
  const next = want ?? level;
  return (
    <span className="vx-lv-lock" data-level-locked="">
      <button type="button" className="vx-lv-badge" aria-expanded={menu} title="Fixed for this chat (prompt caching). Change…"
        onClick={() => setMenu((v) => !v)}>Level {level}</button>
      {menu && (
        <div className="vx-lv-pop" role="dialog" aria-label="Change level">
          <div className="vx-lv" role="radiogroup" aria-label="New level">
            {[1, 2, 3, 4, 5].map((n) => (
              <button key={n} type="button" role="radio" aria-checked={next === n} data-on={next === n ? "" : undefined}
                onClick={() => setWant(n)}>{n}</button>))}
            <span className="vx-lv-word">{LEVEL_WORD[next]}</span>
          </div>
          {onNewChat && (
            <button type="button" className="vx-us-btn" data-primary="" disabled={next === level}
              onClick={() => { onNewChat(next); setMenu(false); }}>Start a new chat at level {next}</button>)}
          <button type="button" className="vx-us-btn" disabled={next === level}
            onClick={() => void choose(next, true).then(() => setMenu(false))}>Switch (resets the cache)</button>
        </div>
      )}
    </span>
  );
}
