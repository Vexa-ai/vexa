"use client";
/** THE MODEL CHIP (UI-DECISIONS 7f, founder 2026-10-16; behind `levels`). One composer chip,
 *  "Opus 5.5 · Medium", in place of the "1 2 3 4 5 fastest" buttons. Its menu:
 *   - the five leading models the account can use (the levels table, top of each tier), by name,
 *     smartest first, with number keys 1–5;
 *   - an Effort row — Low / Medium / High — that changes reasoning only: free and instant;
 *   - a model switch in a chat that already talked asks once, inline: it resets the chat's cache;
 *   - "More models" with the rest of the catalogue, where there is more (self-host).
 *  Reads `GET /api/models/levels` and `GET /api/models/catalog`; writes `POST /api/chat/level` (a
 *  level) and `POST /api/chat/model` (an effort, or a model from More). */
import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";
import { useViewportPlacement } from "../../ui-kit/placement";
import { LEVEL_SET_EVENT } from "../../platform/events";
import { getModelCatalog, setChatModel, type Effort, type ModelList } from "../modelsApi";
import "./usage.css";

type Row = { level: number; word: string; model: string | null };
type Levels = { levels: Row[]; chat?: { level: number; model: string } | null };
const EFFORTS: Effort[] = ["low", "medium", "high"];
const word = (e: string) => e.charAt(0).toUpperCase() + e.slice(1);

const readLevels = (session: string) => fetch(`/api/models/levels?session=${encodeURIComponent(session)}`, { cache: "no-store" })
  .then((r) => (r.ok ? r.json() : null)).then((x: Levels | null) => (x && Array.isArray(x.levels) ? x : null)).catch(() => null);
const pickLevel = (session: string, level: number, sw: boolean) => fetch("/api/chat/level", {
  method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ session, level, switch: sw }),
}).then((r) => r.ok).catch(() => false);

/** "Claude Opus 5.5 (subscription)" → "Opus 5.5": no source suffix, no vendor prefix. */
export function shortModelName(full: string): string {
  return full.replace(/\s*\([^()]*\)\s*$/, "").replace(/^(Claude|OpenAI|Google|Meta)\s+/i, "").trim() || full;
}

export function ModelChip({ session, hasMessages }: { session: string; hasMessages: boolean }) {
  const [lv, setLv] = useState<Levels | null>(null);
  const [cat, setCat] = useState<ModelList | null>(null);
  const [open, setOpen] = useState(false);
  const [asking, setAsking] = useState<{ kind: "level"; level: number; name: string } | { kind: "model"; id: string; name: string } | null>(null);
  const [more, setMore] = useState(false);
  const menu = useRef<HTMLDivElement>(null);
  const chip = useRef<HTMLButtonElement>(null);
  const placed = useViewportPlacement(open, chip, menu, { align: "end", prefer: "above" });
  const load = () => { void readLevels(session).then(setLv); void getModelCatalog(session).then(setCat).catch(() => setCat(null)); };
  useEffect(() => {
    load();
    const onSet = (e: Event) => { if ((e as CustomEvent<{ session?: string }>).detail?.session === session) load(); };
    window.addEventListener(LEVEL_SET_EVENT, onSet);
    return () => window.removeEventListener(LEVEL_SET_EVENT, onSet);
  }, [session]);  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (open) menu.current?.focus(); }, [open]);
  if (!lv || !lv.levels.some((r) => r.model)) return null;
  // the SHORT name: "Claude Opus 5.5 (subscription)" reads "Opus 5.5"; the source is a hint
  const fullOf = (id: string | null | undefined) => cat?.models.find((m) => m.id === id)?.display_name ?? id ?? "";
  const nameOf = (id: string | null | undefined) => shortModelName(fullOf(id));
  const rows = lv.levels.filter((r) => r.model).slice().sort((a, b) => b.level - a.level);   // smartest first
  const currentId = lv.chat?.model ?? cat?.selected ?? cat?.default ?? rows[0]?.model ?? "";
  const current = cat?.models.find((m) => m.id === currentId) ?? null;
  const efforts = (current?.capabilities.reasoning_efforts ?? []).filter((e) => EFFORTS.includes(e));
  const effort = (cat?.selected_effort ?? current?.capabilities.default_effort ?? "") as string;
  const label = `${nameOf(currentId)}${efforts.length && effort ? ` · ${word(effort)}` : ""}`;
  const extra = (cat?.models ?? []).filter((m) => !rows.some((r) => r.model === m.id));

  const chooseLevel = async (r: Row) => {
    if (r.model === currentId) { setOpen(false); return; }
    if (hasMessages && asking?.kind !== "level") { setAsking({ kind: "level", level: r.level, name: nameOf(r.model) }); return; }
    if (await pickLevel(session, r.level, true)) { setAsking(null); setOpen(false); load(); }
  };
  const chooseModel = async (id: string) => {
    if (hasMessages && asking?.kind !== "model") { setAsking({ kind: "model", id, name: nameOf(id) }); return; }
    await setChatModel(session, id).catch(() => null);
    setAsking(null); setOpen(false); load();
  };
  const setEffort = async (e: Effort) => {
    if (!currentId) return;
    await setChatModel(session, currentId, e).catch(() => null);
    setCat((c) => (c ? { ...c, selected_effort: e } : c));
  };
  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === "Escape") { setOpen(false); setAsking(null); return; }
    const n = Number(e.key);
    if (n >= 1 && n <= rows.length) { e.preventDefault(); void chooseLevel(rows[n - 1]); }
  };
  return (
    <span className="vx-mc" data-model-chip="">
      <button ref={chip} type="button" className="vx-chip vx-mc-chip" aria-haspopup="menu" aria-expanded={open} title={fullOf(currentId)} onClick={() => { setOpen((v) => !v); setAsking(null); }}>{label}</button>
      {open && createPortal(<div ref={menu} role="menu" aria-label="Model" tabIndex={-1} className="vx-mc-pop" style={placed} onKeyDown={onKey}>
        {rows.map((r, i) => (
          <button key={r.level} type="button" role="menuitemradio" aria-checked={r.model === currentId} className="vx-mc-row"
            onClick={() => void chooseLevel(r)}>
            <kbd>{i + 1}</kbd> <b title={fullOf(r.model)}>{nameOf(r.model)}</b> <small>{r.word}</small></button>))}
        {asking && <div className="vx-mc-ask" role="group">
          <span>Switch to {asking.name}? It resets this chat's cache, so the next reply costs a full read of the conversation.</span>
          <button type="button" className="vx-btn" data-variant="primary"
            onClick={() => void (asking.kind === "level" ? chooseLevel(rows.find((r) => r.level === asking.level)!) : chooseModel(asking.id))}>Switch to {asking.name}</button>
          <button type="button" className="vx-btn" data-variant="secondary" onClick={() => setAsking(null)}>Keep</button>
        </div>}
        {efforts.length > 0 && <div className="vx-mc-effort" role="group" aria-label="Effort">
          <span>Effort</span>
          {efforts.map((e) => <button key={e} type="button" aria-pressed={effort === e} data-on={effort === e ? "" : undefined}
            onClick={() => void setEffort(e)}>{word(e)}</button>)}
        </div>}
        {extra.length > 0 && <>
          <button type="button" className="vx-mc-more" aria-expanded={more} onClick={() => setMore((v) => !v)}>More models ({extra.length})</button>
          {more && extra.map((m) => <button key={m.id} type="button" role="menuitem" className="vx-mc-row" onClick={() => void chooseModel(m.id)}>{m.display_name}</button>)}
        </>}
      </div>, document.body)}
    </span>
  );
}
