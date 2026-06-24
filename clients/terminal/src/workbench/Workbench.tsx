"use client";
/** term-workbench — the shell. Fixed chrome (title bar · activity bar · status bar) around a dockview
 *  body. Every surface view is a dockview panel (tabbed/splittable/resizable/persisted via the
 *  LayoutService). The shell knows no surface — it renders the activity bar from the registry and hands
 *  dockview a single "viewport" panel component that resolves views by (surfaceId, slot). */
import { useState, type CSSProperties } from "react";
import { DockviewReact, type DockviewReadyEvent, type IDockviewPanelProps, themeAbyss } from "dockview-react";
import "dockview/dist/styles/dockview.css";
import { useService, useStore, useContainer, CommandServiceId } from "../platform";
import { LayoutServiceId } from "./layout";
import { registry, type Slot, type SurfaceId } from "../contributions";
import { Icon } from "../ui-kit";

// ── Composer (the /-skill input; lives inside a surface's main panel) ───────────
function Composer({ surfaceId, placeholder, quickChips }: { surfaceId: string; placeholder?: string; quickChips?: string[] }) {
  const commands = useService(CommandServiceId);
  const container = useContainer();
  const [value, setValue] = useState("");
  const slash = value.startsWith("/");
  const skills = slash ? commands.querySkills(value) : [];
  const onSend = () => {
    const v = value.trim();
    if (!v) return;
    if (v.startsWith("/")) { const sk = commands.querySkills(v)[0]; if (sk) void commands.execute(sk.id, v); }
    else { registry.get(surfaceId)?.onSubmit?.(v, container); }
    setValue("");
  };
  const chip: CSSProperties = { fontSize: 11.5, color: "var(--t2)", border: "1px solid var(--line2)", padding: "3px 10px", borderRadius: 20, background: "none", cursor: "pointer" };
  return (
    <div style={{ borderTop: "1px solid var(--line)", padding: "12px 24px 16px", flex: "none", background: "var(--bg)" }}>
      <div style={{ maxWidth: 760, margin: "0 auto" }}>
        {slash && skills.length > 0 && (
          <div style={{ border: "1px solid var(--line2)", borderRadius: 11, background: "var(--panel)", marginBottom: 9, overflow: "hidden" }}>
            {skills.map((c) => (
              <div key={c.id} onMouseDown={() => setValue(c.skill! + " ")} style={{ display: "flex", gap: 10, padding: "9px 12px", cursor: "pointer", fontSize: 13 }}>
                <code style={{ fontFamily: "var(--mono)", color: "var(--accent)", minWidth: 88 }}>{c.skill}</code>
                <span style={{ color: "var(--t3)", fontSize: 12 }}>{c.title}</span>
              </div>
            ))}
          </div>
        )}
        {!slash && quickChips && quickChips.length > 0 && (
          <div style={{ display: "flex", gap: 7, marginBottom: 9, flexWrap: "wrap" }}>
            {quickChips.map((q) => <button key={q} style={chip} onClick={() => setValue(q)}>{q}</button>)}
          </div>
        )}
        <div style={{ border: "1px solid var(--line2)", borderRadius: 12, background: "var(--panel)", padding: "11px 12px", display: "flex", alignItems: "center", gap: 11 }}>
          <span style={{ fontFamily: "var(--mono)", color: "var(--t3)", fontSize: 13 }}>/</span>
          <input value={value} onChange={(e) => setValue(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") onSend(); }} placeholder={placeholder ?? "Type / for skills, or ask the agent…"}
            style={{ flex: 1, background: "none", border: "none", outline: "none", color: "var(--t1)", fontSize: 14 }} />
          <button aria-label="Send" onClick={onSend}
            style={{ background: "var(--accent)", color: "#241008", border: "none", width: 30, height: 30, borderRadius: 8, display: "flex", alignItems: "center", justifyContent: "center", cursor: "pointer" }}>
            <Icon name="send" size={16} />
          </button>
        </div>
      </div>
    </div>
  );
}

// ── the dockview panel: resolves a surface's views for a slot (+ composer for main) ─────
function Viewport(props: IDockviewPanelProps<{ surfaceId: SurfaceId; slot: Slot }>) {
  const { surfaceId, slot } = props.params;
  const views = registry.views(slot, surfaceId);
  const surface = registry.get(surfaceId);
  const showComposer = slot === "main" && !!surface?.composer?.enabled;
  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column", minHeight: 0, background: "var(--bg)" }}>
      <div style={{ flex: 1, overflowY: "auto", minHeight: 0 }}>
        {views.length
          ? views.map((v) => <v.component key={v.id} surfaceId={surfaceId} />)
          : <div style={{ padding: 24, color: "var(--t3)", fontSize: 13 }}>Nothing here yet.</div>}
      </div>
      {showComposer && <Composer surfaceId={surfaceId} placeholder={surface!.composer!.placeholder} quickChips={surface!.composer!.quickChips} />}
    </div>
  );
}

const dvComponents = { viewport: Viewport };

// ── Activity bar (fixed left nav) ───────────────────────────────────────────────
function ActivityBar() {
  const layout = useService(LayoutServiceId);
  const { activeSurface } = useStore(layout.store);
  const items = registry.activityItems();
  const navItem = (active: boolean): CSSProperties => ({ display: "flex", alignItems: "center", gap: 11, padding: "7px 9px", borderRadius: 7, color: active ? "var(--t1)" : "var(--t2)", background: active ? "var(--panel2)" : "transparent", cursor: "pointer", border: "none", width: "100%", textAlign: "left", fontSize: 13.5 });
  return (
    <aside style={{ width: 212, background: "var(--sidebar)", borderRight: "1px solid var(--line)", display: "flex", flexDirection: "column", minHeight: 0, flex: "none" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 9, padding: "12px 14px 10px" }}>
        <div style={{ width: 26, height: 26, borderRadius: 7, background: "var(--accent)", color: "#241008", display: "flex", alignItems: "center", justifyContent: "center", fontWeight: 600, fontSize: 13 }}>V</div>
        <div><b style={{ fontSize: 13, fontWeight: 500, display: "block", lineHeight: 1.2 }}>Vexa EI</b><span style={{ fontSize: 11, color: "var(--t3)" }}>terminal</span></div>
      </div>
      <nav style={{ padding: "4px 8px", display: "flex", flexDirection: "column", gap: 1 }}>
        {items.map((item) => (
          <button key={item.id} style={navItem(item.id === activeSurface)} onClick={() => layout.openSurface(item.id)}>
            <span style={{ display: "flex" }}><Icon name={item.icon} size={16} /></span>{item.label}
            {item.live && <span style={{ width: 8, height: 8, borderRadius: "50%", background: "var(--live)", marginLeft: "auto" }} />}
          </button>
        ))}
      </nav>
      <div style={{ flex: 1 }} />
      <div style={{ padding: "9px 14px", borderTop: "1px solid var(--line)", fontSize: 11.5, color: "var(--t2)", display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ width: 7, height: 7, borderRadius: "50%", background: "var(--green)", flex: "none" }} />Self-hosted · air-gapped
      </div>
    </aside>
  );
}

// ── Status bar (bottom) ─────────────────────────────────────────────────────────
function StatusBar() {
  const layout = useService(LayoutServiceId);
  const { activeSurface } = useStore(layout.store);
  const cell: CSSProperties = { display: "flex", alignItems: "center", gap: 6, padding: "0 10px", height: "100%" };
  return (
    <footer style={{ height: 24, flex: "none", background: "var(--sidebar)", borderTop: "1px solid var(--line)", display: "flex", alignItems: "center", fontSize: 11.5, color: "var(--t2)" }}>
      <div style={cell}><span style={{ width: 7, height: 7, borderRadius: "50%", background: "var(--green)" }} />bbb · connected</div>
      <div style={{ ...cell, color: "var(--t3)" }}>air-gapped</div>
      <div style={{ flex: 1 }} />
      {activeSurface && <div style={{ ...cell, color: "var(--t3)" }}>{activeSurface}</div>}
      <button onClick={() => layout.resetLayout()} style={{ ...cell, background: "none", border: "none", color: "var(--t3)", cursor: "pointer" }} title="Reset layout">reset layout</button>
    </footer>
  );
}

// ── the shell ───────────────────────────────────────────────────────────────────
export function Workbench() {
  const layout = useService(LayoutServiceId);
  const onReady = (e: DockviewReadyEvent) => layout.attach(e.api, "chat");
  return (
    <div style={{ height: "100vh", display: "flex", flexDirection: "column", background: "var(--bg)", color: "var(--t1)" }}>
      <div style={{ height: 38, display: "flex", alignItems: "center", gap: 14, padding: "0 14px", borderBottom: "1px solid var(--line)", background: "var(--sidebar)", flex: "none" }}>
        <div style={{ display: "flex", gap: 8 }}>
          <i style={{ width: 12, height: 12, borderRadius: "50%", background: "#ec6a5e", display: "block" }} />
          <i style={{ width: 12, height: 12, borderRadius: "50%", background: "#f4bf4f", display: "block" }} />
          <i style={{ width: 12, height: 12, borderRadius: "50%", background: "#61c554", display: "block" }} />
        </div>
        <div style={{ fontSize: 13, color: "var(--t1)", fontWeight: 500 }}>Vexa EI · Terminal</div>
        <div style={{ flex: 1 }} />
        <button aria-label="Toggle sidebar" onClick={() => layout.toggleSidebar()} style={{ background: "none", border: "none", color: "var(--t3)", cursor: "pointer", display: "flex" }}>
          <Icon name="panel" size={16} />
        </button>
      </div>
      <div style={{ flex: 1, display: "flex", minHeight: 0 }}>
        <ActivityBar />
        <div style={{ flex: 1, minWidth: 0, minHeight: 0, position: "relative" }}>
          <div style={{ position: "absolute", inset: 0 }}>
            <DockviewReact onReady={onReady} components={dvComponents} theme={themeAbyss} />
          </div>
        </div>
      </div>
      <StatusBar />
    </div>
  );
}
