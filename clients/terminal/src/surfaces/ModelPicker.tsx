/** The model picker (ADR-0043): which of the deployment's models THIS chat runs on, and at which
 *  effort.
 *
 *  It lives in the composer's bottom toolbar, beside send, because that is when a pick takes
 *  effect: the chat's next turn starts a fresh agent on the picked model. It shows only what
 *  agent-api lists for this person — an admins-only model is never sent to anyone else — and it
 *  renders nothing at all on a deployment with no catalog, where the model is the operator's.
 *
 *  THE LOOK IS CLAUDE CODE'S (founder 2026-10-10): the model name as plain text, short ("Qwen 3.8
 *  27B", the catalog's parenthetical moved to the menu's muted second line), then the effort as
 *  plain text ("High"). Each is a text button that opens a small menu — no bordered selects. The
 *  effort button exists only while the chat's model lists effort levels
 *  (`capabilities.reasoning_efforts`): a model with no effort control gets no control. Changing the
 *  model clears the effort pick — a level belongs to the model it was picked for.
 *
 *  THE MENUS ARE OURS, NOT NATIVE SELECTS, and keyboard operable: Enter, Space or ArrowDown opens,
 *  the arrows, Home and End move, Enter or Space picks, Escape closes and returns focus. A native
 *  select did not always fire its change on ArrowDown + Enter (seen on the app.dev hot loop); a
 *  menu item is a button, and a button's click always fires.
 *
 *  A pick the server refuses (gone from the catalog, not open to this person, an own endpoint not
 *  set, an effort the model does not offer) is shown as the server's own sentence; the stored pick
 *  is left as it was. */
import { useEffect, useRef, useState, type CSSProperties, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from "react";
import { Fold, Icon } from "../ui-kit";
import { Gauge } from "lucide-react";
import { presentError } from "./apiClient";
import { effectiveModel, effortsOf, getModelCatalog, setChatModel, setDefaultModel, type Effort, type ModelEntry, type ModelList } from "./modelsApi";

const textButton: CSSProperties = {
  height: 28, minWidth: 0, display: "inline-flex", alignItems: "center", gap: 3, padding: "0 6px",
  borderRadius: 6, border: "none", background: "transparent", color: "var(--t2)", fontSize: 12.5,
  cursor: "pointer", fontFamily: "inherit",
};
const ellipsis: CSSProperties = { minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" };
const menuBox: CSSProperties = {
  position: "absolute", zIndex: 30, bottom: 34, right: 0, width: 260, maxWidth: "calc(100vw - 32px)",
  maxHeight: 320, overflowY: "auto", border: "1px solid var(--line)", borderRadius: 10,
  background: "var(--panel)", boxShadow: "0 14px 34px rgba(0,0,0,.32)", padding: 4,
};
const item: CSSProperties = {
  width: "100%", minWidth: 0, display: "flex", alignItems: "center", gap: 8, padding: "6px 8px",
  border: "none", borderRadius: 6, background: "transparent", color: "var(--t2)", cursor: "pointer",
  textAlign: "left", fontSize: 12.5, fontFamily: "inherit",
};
const muted: CSSProperties = { color: "var(--t3)", fontSize: 11, lineHeight: 1.3 };

const EFFORT_LABEL: Record<Effort, string> = {
  none: "No thinking", minimal: "Minimal", low: "Low", medium: "Medium", high: "High",
  xhigh: "Extra high", max: "Max",
};

/** "Qwen 3.8 27B (self-hosted)" → name "Qwen 3.8 27B", note "self-hosted". */
export function splitDisplayName(name: string): { short: string; note: string | null } {
  const m = /^(.*\S)\s*\(([^()]+)\)\s*$/.exec(name);
  return m ? { short: m[1], note: m[2] } : { short: name, note: null };
}

const ADAPTER_NOTE: Record<string, string> = {
  openrouter: "OpenRouter", anthropic: "Anthropic", custom: "your own endpoint", openai_compatible: "self-hosted",
};

/** The muted second line of a model: the catalog's own note, else where it runs. */
export function modelNote(m: ModelEntry): string {
  return splitDisplayName(m.display_name).note ?? ADAPTER_NOTE[m.adapter] ?? m.provider;
}

/** A menu's keyboard: arrows, Home and End move between its items; Escape is the caller's. */
function moveFocus(e: ReactKeyboardEvent<HTMLDivElement>) {
  const items = Array.from(e.currentTarget.querySelectorAll<HTMLElement>("[role^=menuitem]"));
  if (items.length === 0) return;
  const at = items.indexOf(document.activeElement as HTMLElement);
  const to = e.key === "ArrowDown" ? (at + 1) % items.length
    : e.key === "ArrowUp" ? (at - 1 + items.length) % items.length
    : e.key === "Home" ? 0 : e.key === "End" ? items.length - 1 : -1;
  if (to < 0) return;
  e.preventDefault();
  items[to].focus();
}

/** One text button and the small menu it opens — the model's and the effort's. */
function TextMenu({ label, title, buttonLabel, danger, data, children, open, setOpen, width }: {
  label: ReactNode; title: string; buttonLabel: string; danger?: boolean; data: Record<string, string>;
  children: ReactNode; open: boolean; setOpen: (v: boolean) => void; width?: number;
}) {
  const box = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const setOpenRef = useRef(setOpen);
  setOpenRef.current = setOpen;
  useEffect(() => {
    if (!open) return;
    // focus the checked item, else the first, so the arrows start where the person is
    const items = box.current?.querySelectorAll<HTMLElement>("[role^=menuitem]");
    const checked = box.current?.querySelector<HTMLElement>("[aria-checked=true]");
    (checked ?? items?.[0])?.focus();
    const onDown = (e: PointerEvent) => {
      if (!box.current?.contains(e.target as Node) && !trigger.current?.contains(e.target as Node)) setOpenRef.current(false);
    };
    document.addEventListener("pointerdown", onDown);
    return () => document.removeEventListener("pointerdown", onDown);
  }, [open]);
  const close = () => { setOpen(false); trigger.current?.focus(); };
  return (
    <div style={{ position: "relative", minWidth: 0, display: "flex" }}>
      <button ref={trigger} type="button" aria-label={buttonLabel} aria-haspopup="menu" aria-expanded={open} title={title}
        {...data}
        onClick={() => setOpen(!open)}
        onKeyDown={(e) => { if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); setOpen(true); } }}
        style={{ ...textButton, color: danger ? "var(--danger)" : textButton.color }}>
        <span style={ellipsis}>{label}</span>
        <Icon name="chevR" size={10} style={{ flex: "none", transform: open ? "rotate(-90deg)" : "rotate(90deg)", transition: "transform .12s", opacity: 0.7 }} />
      </button>
      {open && (
        <div ref={box} role="menu" aria-label={buttonLabel}
          onKeyDown={(e) => {
            if (e.key === "Escape" || e.key === "Tab") { e.preventDefault(); e.stopPropagation(); close(); return; }
            moveFocus(e);
          }}
          style={{ ...menuBox, ...(width ? { width } : {}) }}>
          {children}
        </div>
      )}
    </div>
  );
}

function contextLabel(m: ModelEntry): string | null {
  const n = m.capabilities.context_tokens;
  if (!n) return null;
  return n >= 1024 ? `${Math.round(n / 1024)}k` : String(n);   // 32768 → 32k, as models are named
}

export function ModelPicker({ session }: { session: string }) {
  const [list, setList] = useState<ModelList | null>(null);
  const [open, setOpen] = useState(false);
  const [effortOpen, setEffortOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Counts this chat's stored picks. A catalog read that started before the latest pick answers
  // with the chat as it was, so its `selected`/`selected_effort` must not replace the pick.
  const picks = useRef(0);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    // A NEW CHAT IS NOT THE PREVIOUS ONE. The model list is the person's and stays on screen while
    // this chat's read is in flight, but the previous chat's picks do not: a pick made now goes out
    // for this chat, on the model this chat follows (live on app.dev, a new chat's effort pick went
    // out with the previous chat's model, and the read landing after it reset the dropdown).
    picks.current = 0;
    setList((l) => (l ? { ...l, selected: null, selected_effort: null } : l));
    const startedAt = picks.current;
    getModelCatalog(session)
      // an answer that is not a model list (a server one release behind) is no catalog
      .then((l) => {
        if (cancelled) return;
        if (!Array.isArray(l?.models)) { setList(null); return; }
        // a pick made while this read was in flight is newer than what the read saw
        setList((cur) => (picks.current !== startedAt && cur
          ? { ...l, selected: cur.selected, selected_effort: cur.selected_effort } : l));
      })
      .catch(() => { if (!cancelled) setList(null); });   // no catalog route → no picker
    return () => { cancelled = true; };
  }, [session]);

  if (!list || list.models.length === 0) return null;

  const current = effectiveModel(list);
  const stale = !!list.selected && !list.models.some((m) => m.id === list.selected);
  const fallback = list.models.find((m) => m.id === list.default) ?? null;

  const pick = async (id: string) => {
    setOpen(false);
    setError(null);
    try {
      const r = await setChatModel(session, id);
      picks.current += 1;
      setList((l) => (l ? { ...l, selected: r.model, selected_effort: null } : l));
    } catch (e) {
      setError(presentError(e).headline);
    }
  };
  const makeDefault = async (id: string) => {
    setOpen(false);
    setError(null);
    try {
      await setDefaultModel(id);
      setList((l) => (l ? { ...l, default: id } : l));
    } catch (e) {
      setError(presentError(e).headline);
    }
  };

  const pickEffort = async (level: Effort | "") => {
    setError(null);
    try {
      const r = await setChatModel(session, list.selected ?? "", level);
      picks.current += 1;
      // what the server stored: its echo, else the level it accepted
      setList((l) => (l ? { ...l, selected_effort: r.effort !== undefined ? r.effort : (level || null) } : l));
    } catch (e) {
      setError(presentError(e).headline);
    }
  };

  const efforts = stale ? [] : effortsOf(current);
  const effortDefault = current?.capabilities.default_effort ?? null;
  const effortValue: Effort | "" = list.selected_effort ?? effortDefault ?? "";
  const name = stale ? "Model unavailable" : current ? splitDisplayName(current.display_name).short : "Model";
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 2, minWidth: 0, flex: "0 1 auto", position: "relative" }}>
      <div data-model-picker style={{ minWidth: 56, display: "flex" }}>
        <TextMenu open={open} setOpen={(v) => { setOpen(v); if (v) setEffortOpen(false); }} buttonLabel="Model for this chat"
          title={stale ? "This chat's model is no longer offered — pick another" : `This chat runs on ${current?.display_name ?? "the default model"}`}
          danger={stale} data={{}} label={name} width={280}>
          {list.models.map((m) => {
            const active = !stale && current?.id === m.id;
            const { short } = splitDisplayName(m.display_name);
            return (
              <button key={m.id} type="button" role="menuitemradio" aria-checked={active} data-model-id={m.id}
                onClick={() => void pick(m.id)}
                style={{ ...item, alignItems: "flex-start", background: active ? "var(--panel2)" : "transparent", color: active ? "var(--t1)" : "var(--t2)" }}>
                <span style={{ width: 13, flex: "none", display: "flex", paddingTop: 2 }}>{active ? <Icon name="check" size={13} /> : null}</span>
                <span style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: 1 }}>
                  <span style={ellipsis}>{short}</span>
                  <span style={{ ...muted, ...ellipsis }}>
                    {[modelNote(m), m.id === list.default ? "default" : null, m.access === "admins" ? "admins" : null,
                      contextLabel(m)].filter(Boolean).join(" · ")}
                  </span>
                </span>
              </button>
            );
          })}
          {((list.selected && fallback) || (current && !stale && current.id !== list.default)) && (
            <div style={{ height: 1, background: "var(--line)", margin: "4px 2px" }} />
          )}
          {list.selected && fallback && (
            <button type="button" role="menuitem" onClick={() => void pick("")} style={item}>
              <span style={ellipsis}>Follow my default ({splitDisplayName(fallback.display_name).short})</span>
            </button>
          )}
          {current && !stale && current.id !== list.default && (
            <button type="button" role="menuitem" onClick={() => void makeDefault(current.id)} style={item}>
              <span style={ellipsis}>Use for new chats</span>
            </button>
          )}
          <div style={{ ...muted, padding: "4px 8px 6px" }}>A new pick takes effect on this chat&apos;s next message.</div>
        </TextMenu>
      </div>
      {efforts.length > 0 && (
        <TextMenu open={effortOpen} setOpen={(v) => { setEffortOpen(v); if (v) setOpen(false); }} buttonLabel="Effort for this chat"
          title={`Reasoning effort for ${name}`} data={{ "data-effort-picker": "", "data-effort": effortValue }}
          // Below 560px of composer the effort is an icon (its name stays the button's title and
          // accessible name), so the model's name keeps the room (guidelines §3.3).
          label={<Fold at={560} wide={effortValue ? EFFORT_LABEL[effortValue] : "Default effort"}
            narrow={<Gauge size={14} strokeWidth={1.75} aria-hidden />} />} width={190}>
          {!effortDefault && (
            <button type="button" role="menuitemradio" aria-checked={effortValue === ""} data-effort-level=""
              onClick={() => { setEffortOpen(false); void pickEffort(""); }} style={item}>
              <span style={{ width: 13, flex: "none", display: "flex" }}>{effortValue === "" ? <Icon name="check" size={13} /> : null}</span>
              <span style={ellipsis}>Default</span>
            </button>
          )}
          {efforts.map((lvl) => (
            <button key={lvl} type="button" role="menuitemradio" aria-checked={effortValue === lvl} data-effort-level={lvl}
              onClick={() => { setEffortOpen(false); void pickEffort(lvl); }}
              style={{ ...item, background: effortValue === lvl ? "var(--panel2)" : "transparent", color: effortValue === lvl ? "var(--t1)" : "var(--t2)" }}>
              <span style={{ width: 13, flex: "none", display: "flex" }}>{effortValue === lvl ? <Icon name="check" size={13} /> : null}</span>
              <span style={{ flex: 1, ...ellipsis }}>{EFFORT_LABEL[lvl]}</span>
              {lvl === effortDefault && <span style={muted}>default</span>}
            </button>
          ))}
        </TextMenu>
      )}
      {error && (
        <div role="alert" style={{ position: "absolute", bottom: 34, right: 0, width: 260, maxWidth: "calc(100vw - 32px)", padding: "6px 8px", borderRadius: 8,
          border: "1px solid var(--line)", background: "var(--panel)", color: "var(--danger)", fontSize: 12, lineHeight: 1.35 }}>
          {error}
        </div>
      )}
    </div>
  );
}
