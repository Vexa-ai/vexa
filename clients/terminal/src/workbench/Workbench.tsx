"use client";
/** term-workbench (v2) — the structured 3-pane shell.
 *  LEFT (resizable/collapsible): segmented list switcher + the active list.
 *  CENTER: dockview TABS — a "tab" host resolves each panel by params.kind via the tab registry.
 *  RIGHT (resizable/collapsible): the persistent workspace chat, grounded by the active center tab.
 *  Reuses the Phase-C ⌘K palette + keybindings; the kernel's services do the rest. */
import { useEffect, useRef, useState, type CSSProperties } from "react";
import { Allotment } from "allotment";
import "allotment/dist/style.css";
import { DockviewReact, type DockviewApi, type DockviewReadyEvent, type IDockviewPanelProps, type IDockviewPanelHeaderProps, themeAbyss } from "dockview-react";
import "dockview/dist/styles/dockview.css";

const PANES_KEY = "vexa.terminal.panes.v2";
const savedSizes = (): number[] | undefined => { try { const s = localStorage.getItem(PANES_KEY); const a = s ? JSON.parse(s) : null; return Array.isArray(a) && a.length === 3 ? a : undefined; } catch { return undefined; } };
const persistSizes = (s: number[]) => { try { localStorage.setItem(PANES_KEY, JSON.stringify(s)); } catch { /* noop */ } };
import { useService, useStore, KeybindingServiceId, CommandServiceId } from "../platform";
import { LayoutServiceId } from "./layout";
import { CommandPalette } from "./CommandPalette";
import { registry } from "../contributions";
import { Icon } from "../ui-kit";
import { ContextMenu, copyText } from "../ui-kit/ContextMenu";
import { Chat } from "../surfaces/chat";

// ── the dockview panel host: render a tab by its kind, tracking active state ─────
function TabHost(props: IDockviewPanelProps) {
  const layout = useService(LayoutServiceId);
  // dockview reuses ONE panel for the shared preview slot and swaps its params via updateParameters
  // WITHOUT re-rendering the React content — subscribe to the param-change event so single-clicking a
  // different meeting/file in the preview slot actually re-binds the content to the new params.
  const [params, setParams] = useState(props.params as { kind?: string; p?: Record<string, unknown> });
  useEffect(() => {
    const d = props.api.onDidParametersChange((next) => { if (next && Object.keys(next).length) setParams(next as { kind?: string; p?: Record<string, unknown> }); });
    return () => d.dispose();
  }, [props.api]);
  const kind = params.kind ?? "";
  const Comp = registry.tabComponent(kind);
  const [active, setActive] = useState<boolean>(props.api.isActive);
  useEffect(() => {
    const d = props.api.onDidActiveChange((e: { isActive: boolean }) => setActive(e.isActive));
    return () => d.dispose();
  }, [props.api]);
  useEffect(() => { if (active) layout.setActiveTab(kind ? { kind, params: params.p ?? {} } : null); }, [active, kind, layout, params.p]);
  if (!Comp) return <div style={{ padding: 24, color: "var(--t3)", fontSize: 13 }}>Unknown tab kind: {kind}</div>;
  return <Comp id={props.api.id} params={params.p ?? {}} active={active} />;
}
const dvComponents = { tab: TabHost };

// ── custom tab header: VS Code-style — PREVIEW tabs render their title in italic ──
function TabHeader(props: IDockviewPanelHeaderProps) {
  const params = props.params as { kind?: string; p?: { path?: unknown; meetingId?: unknown }; preview?: boolean };
  const preview = Boolean(params.preview);
  const [title, setTitle] = useState<string>(props.api.title ?? "");
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null);
  useEffect(() => {
    const d = props.api.onDidTitleChange((e: { title: string }) => setTitle(e.title));
    return () => d.dispose();
  }, [props.api]);
  const path = typeof params.p?.path === "string" ? params.p.path : null;
  const meetingId = typeof params.p?.meetingId === "string" ? params.p.meetingId : null;
  const copyItems = params.kind === "doc" && path
    ? [
      { id: "copy-reference", label: "Copy reference", detail: `@file:${path}`, onSelect: () => copyText(`@file:${path}`) },
      { id: "copy-path", label: "Copy path", detail: path, onSelect: () => copyText(path) },
    ]
    : params.kind === "meeting" && meetingId
      ? [{ id: "copy-reference", label: "Copy reference", detail: `@meeting:${meetingId}`, onSelect: () => copyText(`@meeting:${meetingId}`) }]
      : [];
  return (
    <div
      className="dv-default-tab"
      onMouseDown={(e) => {
        if (e.button !== 1) return;
        e.preventDefault();
        e.stopPropagation();
        props.api.close();
      }}
      onAuxClick={(e) => {
        if (e.button !== 1) return;
        e.preventDefault();
        e.stopPropagation();
      }}
      onContextMenu={(e) => {
        e.preventDefault();
        e.stopPropagation();
        if (copyItems.length === 0) {
          setMenu(null);
          return;
        }
        setMenu({ x: e.clientX, y: e.clientY });
      }}
      style={{ display: "flex", alignItems: "center", height: "100%" }}
    >
      <span className="dv-default-tab-content" style={{ fontStyle: preview ? "italic" : "normal" }}>{title}</span>
      <span
        className="dv-default-tab-action"
        role="button"
        aria-label="Close tab"
        onPointerDown={(e) => e.preventDefault()}
        onClick={(e) => { e.stopPropagation(); props.api.close(); }}
      >×</span>
      {menu && copyItems.length > 0 && <ContextMenu x={menu.x} y={menu.y} onClose={() => setMenu(null)} items={copyItems} />}
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

// ── RIGHT pane: persistent chat singleton ────────────────────────────────────────
function RightPane() {
  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column", background: "var(--rail)", borderLeft: "1px solid var(--line)", minHeight: 0 }}>
      <div style={{ flex: 1, minHeight: 0 }}>
        <Chat />
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

  // detach the dockview api on unmount (navigation/HMR dispose it) so the layout
  // service never operates on a disposed grid.
  const apiRef = useRef<DockviewApi | null>(null);
  useEffect(() => () => { if (apiRef.current) layout.detach(apiRef.current); }, [layout]);

  const onReady = (e: DockviewReadyEvent) => {
    apiRef.current = e.api;
    layout.attach(e.api);
    if (e.api.panels.length === 0) {
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

      <div style={{ flex: 1, minHeight: 0 }}>
        <Allotment onChange={persistSizes} defaultSizes={savedSizes() ?? [15, 55, 30]}>
          <Allotment.Pane visible={!leftCollapsed} minSize={180} preferredSize="15%">
            <LeftPane />
          </Allotment.Pane>
          <Allotment.Pane minSize={360} preferredSize="55%">
            <div style={{ height: "100%", position: "relative" }}>
              <div style={{ position: "absolute", inset: 0 }}>
                <DockviewReact onReady={onReady} components={dvComponents} tabComponents={dvTabComponents} defaultTabComponent={TabHeader} theme={themeAbyss} />
              </div>
            </div>
          </Allotment.Pane>
          <Allotment.Pane visible={!rightCollapsed} minSize={260} preferredSize="30%">
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
