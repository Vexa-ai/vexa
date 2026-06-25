"use client";
/** term-workbench (v2) — the structured 3-pane shell.
 *  LEFT (resizable/collapsible): segmented list switcher + the active list.
 *  CENTER: dockview TABS — a "tab" host resolves each panel by params.kind via the tab registry.
 *  RIGHT (resizable/collapsible): a single contextual panel driven by the active tab.
 *  Reuses the Phase-C ⌘K palette + keybindings; the kernel's services do the rest. */
import { useEffect, useRef, useState, type CSSProperties } from "react";
import { Allotment } from "allotment";
import "allotment/dist/style.css";
import { DockviewReact, type DockviewApi, type DockviewReadyEvent, type IDockviewPanelProps, type IDockviewPanelHeaderProps, themeAbyss } from "dockview-react";
import "dockview/dist/styles/dockview.css";

const PANES_KEY = "vexa.terminal.panes.v1";
const savedSizes = (): number[] | undefined => { try { const s = localStorage.getItem(PANES_KEY); const a = s ? JSON.parse(s) : null; return Array.isArray(a) && a.length === 3 ? a : undefined; } catch { return undefined; } };
const persistSizes = (s: number[]) => { try { localStorage.setItem(PANES_KEY, JSON.stringify(s)); } catch { /* noop */ } };
import { useService, useStore, KeybindingServiceId, CommandServiceId } from "../platform";
import { LayoutServiceId, type TabDescriptor, type RightContext } from "./layout";
import { CommandPalette } from "./CommandPalette";
import { registry } from "../contributions";
import { Icon } from "../ui-kit";

const DEFAULT_TAB: TabDescriptor = { id: "chat:default", title: "Chat", kind: "chat", params: { subject: "u_live", session: null }, context: null };

// ── the dockview panel host: render a tab by its kind, tracking active state ─────
function TabHost(props: IDockviewPanelProps) {
  const layout = useService(LayoutServiceId);
  const params = props.params as { kind?: string; p?: Record<string, unknown>; ctx?: RightContext | null };
  const kind = params.kind ?? "";
  const Comp = registry.tabComponent(kind);
  const [active, setActive] = useState<boolean>(props.api.isActive);
  useEffect(() => {
    const d = props.api.onDidActiveChange((e: { isActive: boolean }) => setActive(e.isActive));
    return () => d.dispose();
  }, [props.api]);
  useEffect(() => { if (active) layout.setContext(params.ctx ?? null); }, [active, layout, params.ctx]);
  if (!Comp) return <div style={{ padding: 24, color: "var(--t3)", fontSize: 13 }}>Unknown tab kind: {kind}</div>;
  return <Comp id={props.api.id} params={params.p ?? {}} active={active} />;
}
const dvComponents = { tab: TabHost };

// ── custom tab header: VS Code-style — PREVIEW tabs render their title in italic ──
function TabHeader(props: IDockviewPanelHeaderProps) {
  const preview = Boolean((props.params as { preview?: boolean }).preview);
  const [title, setTitle] = useState<string>(props.api.title ?? "");
  useEffect(() => {
    const d = props.api.onDidTitleChange((e: { title: string }) => setTitle(e.title));
    return () => d.dispose();
  }, [props.api]);
  return (
    <div className="dv-default-tab" style={{ display: "flex", alignItems: "center", height: "100%" }}>
      <span className="dv-default-tab-content" style={{ fontStyle: preview ? "italic" : "normal" }}>{title}</span>
      <span
        className="dv-default-tab-action"
        role="button"
        aria-label="Close tab"
        onPointerDown={(e) => e.preventDefault()}
        onClick={(e) => { e.stopPropagation(); props.api.close(); }}
      >×</span>
    </div>
  );
}
const dvTabComponents = { default: TabHeader };

// ── LEFT pane: brand + segmented list switcher + active list ─────────────────────
function LeftPane() {
  const layout = useService(LayoutServiceId);
  const { activeList } = useStore(layout.store);
  const lists = registry.lists();
  const active = registry.list(activeList) ?? lists[0];
  const Comp = active?.component;
  const seg = (on: boolean): CSSProperties => ({ display: "flex", alignItems: "center", gap: 6, padding: "5px 9px", borderRadius: 7, fontSize: 12.5, cursor: "pointer", border: "none", color: on ? "var(--t1)" : "var(--t2)", background: on ? "var(--panel2)" : "transparent" });
  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column", background: "var(--sidebar)", borderRight: "1px solid var(--line)", minHeight: 0 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 9, padding: "12px 14px 8px", flex: "none" }}>
        <div style={{ width: 24, height: 24, borderRadius: 7, background: "var(--accent)", color: "#241008", display: "flex", alignItems: "center", justifyContent: "center", fontWeight: 600, fontSize: 12 }}>V</div>
        <div><b style={{ fontSize: 13, fontWeight: 500, display: "block", lineHeight: 1.2 }}>Vexa EI</b><span style={{ fontSize: 11, color: "var(--t3)" }}>terminal</span></div>
      </div>
      <div style={{ display: "flex", gap: 3, flexWrap: "wrap", padding: "2px 8px 8px", borderBottom: "1px solid var(--line)", flex: "none" }}>
        {lists.map((l) => (
          <button key={l.id} style={seg(l.id === active?.id)} onClick={() => layout.setActiveList(l.id)}>
            <Icon name={l.icon} size={13} />{l.label}
          </button>
        ))}
      </div>
      <div style={{ flex: 1, overflowY: "auto", minHeight: 0 }}>{Comp && <Comp />}</div>
      <div style={{ padding: "8px 14px", borderTop: "1px solid var(--line)", fontSize: 11.5, color: "var(--t2)", display: "flex", alignItems: "center", gap: 8, flex: "none" }}>
        <span style={{ width: 7, height: 7, borderRadius: "50%", background: "var(--green)" }} />Self-hosted · air-gapped
      </div>
    </div>
  );
}

// ── RIGHT pane: single contextual panel from the active tab ──────────────────────
function RightPane() {
  const layout = useService(LayoutServiceId);
  const { context } = useStore(layout.store);
  const Comp = context ? registry.contextComponent(context.kind) : undefined;
  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column", background: "var(--rail)", borderLeft: "1px solid var(--line)", minHeight: 0 }}>
      <div style={{ height: 38, flex: "none", display: "flex", alignItems: "center", padding: "0 12px 0 16px", borderBottom: "1px solid var(--line)", fontSize: 12, color: "var(--t3)", textTransform: "uppercase", letterSpacing: ".04em" }}>
        context
        <div style={{ flex: 1 }} />
        <button aria-label="Close context" onClick={() => layout.toggleRight()} style={{ background: "none", border: "none", color: "var(--t3)", cursor: "pointer", display: "flex" }}><Icon name="x" size={14} /></button>
      </div>
      <div style={{ flex: 1, overflowY: "auto", minHeight: 0 }}>
        {Comp && context ? <Comp params={context.params ?? {}} /> : <div style={{ padding: "24px 18px", color: "var(--t3)", fontSize: 13 }}>No context yet — open a document or a chat.</div>}
      </div>
    </div>
  );
}

// ── the shell ───────────────────────────────────────────────────────────────────
export function Workbench() {
  const layout = useService(LayoutServiceId);
  const keybindings = useService(KeybindingServiceId);
  const { leftCollapsed, rightCollapsed } = useStore(layout.store);
  const commands = useService(CommandServiceId);
  useEffect(() => { const d = keybindings.attach(window); return () => d.dispose(); }, [keybindings]);

  // right context pane is capped at ≤ 1/5 of the shell width (responsive)
  const shellRef = useRef<HTMLDivElement>(null);
  const [shellW, setShellW] = useState(0);
  useEffect(() => {
    const el = shellRef.current; if (!el) return;
    const ro = new ResizeObserver(() => setShellW(el.clientWidth));
    ro.observe(el); setShellW(el.clientWidth);
    return () => ro.disconnect();
  }, []);
  const rightFifth = shellW ? Math.max(220, Math.round(shellW / 5)) : 320;

  // detach the dockview api on unmount (navigation/HMR dispose it) so the layout
  // service never operates on a disposed grid.
  const apiRef = useRef<DockviewApi | null>(null);
  useEffect(() => () => { if (apiRef.current) layout.detach(apiRef.current); }, [layout]);

  const onReady = (e: DockviewReadyEvent) => {
    apiRef.current = e.api;
    layout.attach(e.api);
    if (e.api.panels.length === 0) {
      layout.openTab(DEFAULT_TAB);
      void commands.execute("meeting.openLive"); // mock: the scheduled live meeting auto-opens as a tab
    }
  };

  return (
    <div style={{ height: "100vh", display: "flex", flexDirection: "column", background: "var(--bg)", color: "var(--t1)" }}>
      <div style={{ height: 38, display: "flex", alignItems: "center", gap: 12, padding: "0 12px", borderBottom: "1px solid var(--line)", background: "var(--sidebar)", flex: "none" }}>
        <div style={{ display: "flex", gap: 8 }}>
          <i style={{ width: 12, height: 12, borderRadius: "50%", background: "#ec6a5e" }} />
          <i style={{ width: 12, height: 12, borderRadius: "50%", background: "#f4bf4f" }} />
          <i style={{ width: 12, height: 12, borderRadius: "50%", background: "#61c554" }} />
        </div>
        <button aria-label="Toggle left" onClick={() => layout.toggleLeft()} style={{ background: "none", border: "none", color: "var(--t3)", cursor: "pointer", display: "flex" }}><Icon name="panel" size={16} /></button>
        <div style={{ fontSize: 13, color: "var(--t1)", fontWeight: 500 }}>Vexa EI · Terminal</div>
        <div style={{ flex: 1 }} />
        <button aria-label="Toggle right" onClick={() => layout.toggleRight()} style={{ background: "none", border: "none", color: "var(--t3)", cursor: "pointer", display: "flex", transform: "scaleX(-1)" }}><Icon name="panel" size={16} /></button>
      </div>

      <div ref={shellRef} style={{ flex: 1, minHeight: 0 }}>
        <Allotment proportionalLayout={false} onChange={persistSizes} defaultSizes={savedSizes()}>
          <Allotment.Pane visible={!leftCollapsed} minSize={180} preferredSize={262}>
            <LeftPane />
          </Allotment.Pane>
          <Allotment.Pane minSize={360}>
            <div style={{ height: "100%", position: "relative" }}>
              <div style={{ position: "absolute", inset: 0 }}>
                <DockviewReact onReady={onReady} components={dvComponents} tabComponents={dvTabComponents} defaultTabComponent={TabHeader} theme={themeAbyss} />
              </div>
            </div>
          </Allotment.Pane>
          <Allotment.Pane visible={!rightCollapsed} minSize={200} maxSize={rightFifth} preferredSize={rightFifth}>
            <RightPane />
          </Allotment.Pane>
        </Allotment>
      </div>

      <footer style={{ height: 24, flex: "none", background: "var(--sidebar)", borderTop: "1px solid var(--line)", display: "flex", alignItems: "center", fontSize: 11.5, color: "var(--t2)" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 6, padding: "0 10px" }}><span style={{ width: 7, height: 7, borderRadius: "50%", background: "var(--green)" }} />bbb · connected</div>
        <div style={{ padding: "0 10px", color: "var(--t3)" }}>air-gapped</div>
        <div style={{ flex: 1 }} />
        <button onClick={() => layout.resetLayout()} style={{ padding: "0 10px", height: "100%", background: "none", border: "none", color: "var(--t3)", cursor: "pointer" }} title="Reset layout">reset layout</button>
      </footer>

      <CommandPalette />
    </div>
  );
}
