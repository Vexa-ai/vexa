/** The model picker (ADR-0043): which of the deployment's models THIS chat runs on.
 *
 *  It lives in the composer, beside the controls that act on the next turn, because that is when a
 *  pick takes effect: the chat's next turn starts a fresh agent on the picked model. It shows only
 *  what agent-api lists for this person — an admins-only model is never sent to anyone else — and it
 *  renders nothing at all on a deployment with no catalog, where the model is the operator's.
 *
 *  A pick the server refuses (gone from the catalog, not open to this person, an own endpoint not
 *  set) is shown as the server's own sentence; the stored pick is left as it was.
 *
 *  THE EFFORT SELECTOR sits beside the chip and exists only while the chat's model lists effort
 *  levels (`capabilities.reasoning_efforts`): a model with no effort control gets no control, never
 *  a selector that does nothing. Changing the model clears the effort pick — a level belongs to the
 *  model it was picked for. A level the server refuses comes back as its typed fault's sentence. */
import { useEffect, useRef, useState, type CSSProperties } from "react";
import { Icon } from "../ui-kit";
import { presentError } from "./apiClient";
import { effectiveModel, effortsOf, getModelCatalog, setChatModel, setDefaultModel, type Effort, type ModelEntry, type ModelList } from "./modelsApi";

const chip: CSSProperties = {
  height: 30, maxWidth: 190, minWidth: 0, flex: "none", display: "inline-flex", alignItems: "center", gap: 5,
  padding: "0 8px", borderRadius: 8, border: "1px solid var(--line2)", background: "transparent",
  color: "var(--t2)", fontSize: 12, cursor: "pointer",
};
const item: CSSProperties = {
  width: "100%", minWidth: 0, display: "flex", alignItems: "center", gap: 8, padding: "7px 8px",
  border: "none", borderRadius: 6, background: "transparent", color: "var(--t2)", cursor: "pointer",
  textAlign: "left", fontSize: 12.5,
};
const tag: CSSProperties = {
  flex: "none", fontSize: 10.5, color: "var(--t3)", border: "1px solid var(--line)", borderRadius: 999,
  padding: "0 6px", lineHeight: "16px",
};

function contextLabel(m: ModelEntry): string | null {
  const n = m.capabilities.context_tokens;
  if (!n) return null;
  return n >= 1024 ? `${Math.round(n / 1024)}k` : String(n);   // 32768 → 32k, as models are named
}

export function ModelPicker({ session }: { session: string }) {
  const [list, setList] = useState<ModelList | null>(null);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    getModelCatalog(session)
      // an answer that is not a model list (a server one release behind) is no catalog
      .then((l) => { if (!cancelled) setList(Array.isArray(l?.models) ? l : null); })
      .catch(() => { if (!cancelled) setList(null); });   // no catalog route → no picker
    return () => { cancelled = true; };
  }, [session]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false); };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") { e.stopPropagation(); setOpen(false); } };
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("pointerdown", onDown); document.removeEventListener("keydown", onKey); };
  }, [open]);

  if (!list || list.models.length === 0) return null;

  const current = effectiveModel(list);
  const stale = !!list.selected && !list.models.some((m) => m.id === list.selected);
  const fallback = list.models.find((m) => m.id === list.default) ?? null;

  const pick = async (id: string) => {
    setOpen(false);
    setError(null);
    try {
      const r = await setChatModel(session, id);
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
      setList((l) => (l ? { ...l, selected_effort: r.effort ?? null } : l));
    } catch (e) {
      setError(presentError(e).headline);
    }
  };

  const label = stale ? "Model unavailable" : current?.display_name ?? "Model";
  const efforts = stale ? [] : effortsOf(current);
  const effortDefault = current?.capabilities.default_effort ?? null;
  const effortValue = list.selected_effort ?? effortDefault ?? "";
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6, flex: "none", minWidth: 0 }}>
    {efforts.length > 0 && (
      <select aria-label="Effort for this chat" data-effort-picker value={effortValue}
        title={`Reasoning effort for ${label}`}
        onChange={(e) => void pickEffort(e.target.value as Effort | "")}
        style={{ ...chip, maxWidth: 120, appearance: "auto" }}>
        {!effortDefault && <option value="">effort: default</option>}
        {efforts.map((lvl) => (
          <option key={lvl} value={lvl}>effort: {lvl}{lvl === effortDefault ? " (default)" : ""}</option>
        ))}
      </select>
    )}
    <div ref={ref} data-model-picker style={{ position: "relative", flex: "none", minWidth: 0 }}>
      <button type="button" aria-label="Model for this chat" aria-haspopup="menu" aria-expanded={open}
        title={stale ? "This chat's model is no longer offered — pick another" : `This chat runs on ${label}`}
        onClick={() => setOpen((v) => !v)}
        style={{ ...chip, color: stale ? "var(--danger)" : chip.color, borderColor: stale ? "var(--danger)" : "var(--line2)" }}>
        <Icon name="spark" size={12} />
        <span style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{label}</span>
        <Icon name="chevR" size={11} style={{ transform: open ? "rotate(-90deg)" : "rotate(90deg)", transition: "transform .12s" }} />
      </button>
      {error && (
        <div role="alert" style={{ position: "absolute", bottom: 36, right: 0, width: 260, padding: "6px 8px", borderRadius: 8,
          border: "1px solid var(--line)", background: "var(--panel)", color: "var(--danger)", fontSize: 12, lineHeight: 1.35 }}>
          {error}
        </div>
      )}
      {open && (
        <div role="menu" aria-label="Models" style={{ position: "absolute", zIndex: 30, bottom: 36, right: 0, width: 280, maxHeight: 300,
          overflowY: "auto", border: "1px solid var(--line)", borderRadius: 8, background: "var(--panel)",
          boxShadow: "0 14px 34px rgba(0,0,0,.32)", padding: 4 }}>
          {list.models.map((m) => {
            const active = !stale && current?.id === m.id;
            const ctx = contextLabel(m);
            return (
              <button key={m.id} type="button" role="menuitemradio" aria-checked={active} data-model-id={m.id}
                onClick={() => void pick(m.id)}
                style={{ ...item, background: active ? "var(--panel2)" : "transparent", color: active ? "var(--t1)" : "var(--t2)" }}>
                <span style={{ width: 13, flex: "none", display: "flex" }}>{active ? <Icon name="check" size={13} /> : null}</span>
                <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{m.display_name}</span>
                {m.id === list.default && <span style={tag}>default</span>}
                {m.access === "admins" && <span style={tag}>admins</span>}
                {ctx && <span style={tag}>{ctx}</span>}
              </button>
            );
          })}
          <div style={{ height: 1, background: "var(--line)", margin: "4px 2px" }} />
          {list.selected && fallback && (
            <button type="button" role="menuitem" onClick={() => void pick("")} style={item}>
              Follow my default ({fallback.display_name})
            </button>
          )}
          {current && !stale && current.id !== list.default && (
            <button type="button" role="menuitem" onClick={() => void makeDefault(current.id)} style={item}>
              Use {current.display_name} for new chats
            </button>
          )}
          <div style={{ padding: "4px 8px 6px", color: "var(--t3)", fontSize: 11, lineHeight: 1.35 }}>
            A new pick takes effect on this chat&apos;s next message.
          </div>
        </div>
      )}
    </div>
    </div>
  );
}
