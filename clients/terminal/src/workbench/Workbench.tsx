"use client";
/** term-workbench — the shell. Renders parts (activity bar · sidebar · main · composer · aux · status)
 *  entirely from the contribution registry + the LayoutService. Knows nothing about any surface. */
import { useState, type CSSProperties } from "react";
import { useService, useStore, CommandServiceId } from "../platform";
import { LayoutServiceId } from "./layout";
import { registry } from "../contributions";
import { Icon } from "../ui-kit";

const S = {
  shell: { height: "100vh", display: "flex", flexDirection: "column", background: "var(--bg)", color: "var(--t1)" } as CSSProperties,
  topbar: { height: 38, display: "flex", alignItems: "center", gap: 14, padding: "0 14px", borderBottom: "1px solid var(--line)", background: "var(--sidebar)", flex: "none" } as CSSProperties,
  dot: (c: string) => ({ width: 12, height: 12, borderRadius: "50%", background: c, display: "block" } as CSSProperties),
  left: { width: 268, background: "var(--sidebar)", borderRight: "1px solid var(--line)", display: "flex", flexDirection: "column", minHeight: 0 } as CSSProperties,
  brand: { display: "flex", alignItems: "center", gap: 9, padding: "12px 14px 10px" } as CSSProperties,
  logo: { width: 26, height: 26, borderRadius: 7, background: "var(--accent)", color: "#241008", display: "flex", alignItems: "center", justifyContent: "center", fontWeight: 600, fontSize: 13 } as CSSProperties,
  nav: { padding: "4px 8px", display: "flex", flexDirection: "column", gap: 1 } as CSSProperties,
  navItem: (active: boolean) => ({ display: "flex", alignItems: "center", gap: 11, padding: "7px 9px", borderRadius: 7, color: active ? "var(--t1)" : "var(--t2)", background: active ? "var(--panel2)" : "transparent", cursor: "pointer", border: "none", width: "100%", textAlign: "left", fontSize: 13.5 } as CSSProperties),
  pulse: { width: 8, height: 8, borderRadius: "50%", background: "var(--live)", marginLeft: "auto" } as CSSProperties,
  ctx: { flex: 1, overflowY: "auto", minHeight: 0, padding: "4px 8px 10px" } as CSSProperties,
  dep: { padding: "9px 14px", borderTop: "1px solid var(--line)", fontSize: 11.5, color: "var(--t2)", display: "flex", alignItems: "center", gap: 8 } as CSSProperties,
  main: { flex: 1, minWidth: 0, minHeight: 0, display: "flex", flexDirection: "column", background: "var(--bg)" } as CSSProperties,
  vhead: { height: 46, borderBottom: "1px solid var(--line)", display: "flex", alignItems: "center", gap: 10, padding: "0 18px", flex: "none", fontSize: 15, fontWeight: 500 } as CSSProperties,
  body: { flex: 1, overflowY: "auto", minHeight: 0 } as CSSProperties,
  right: { width: 332, background: "var(--rail)", borderLeft: "1px solid var(--line)", display: "flex", flexDirection: "column", minHeight: 0 } as CSSProperties,
  composer: { borderTop: "1px solid var(--line)", padding: "12px 24px 16px" } as CSSProperties,
  cbox: { maxWidth: 760, margin: "0 auto", border: "1px solid var(--line2)", borderRadius: 12, background: "var(--panel)", padding: "11px 12px", display: "flex", alignItems: "center", gap: 11 } as CSSProperties,
  chip: { fontSize: 11.5, color: "var(--t2)", border: "1px solid var(--line2)", padding: "3px 10px", borderRadius: 20, background: "none", cursor: "pointer" } as CSSProperties,
};

function Composer({ surfaceId, placeholder, quickChips }: { surfaceId: string; placeholder?: string; quickChips?: string[] }) {
  const commands = useService(CommandServiceId);
  const [value, setValue] = useState("");
  const slash = value.startsWith("/");
  const skills = slash ? commands.querySkills(value) : [];
  return (
    <div style={S.composer}>
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
            {quickChips.map((q) => (
              <button key={q} style={S.chip} onClick={() => setValue(q)}>{q}</button>
            ))}
          </div>
        )}
        <div style={S.cbox}>
          <span style={{ fontFamily: "var(--mono)", color: "var(--t3)", fontSize: 13 }}>/</span>
          <input value={value} onChange={(e) => setValue(e.target.value)} placeholder={placeholder ?? "Type / for skills, or ask the agent…"}
            style={{ flex: 1, background: "none", border: "none", outline: "none", color: "var(--t1)", fontSize: 14 }} />
          <button aria-label="Send" onClick={() => { if (value.startsWith("/")) commands.execute(commands.querySkills(value)[0]?.id ?? "", value); setValue(""); }}
            style={{ background: "var(--accent)", color: "#241008", border: "none", width: 30, height: 30, borderRadius: 8, display: "flex", alignItems: "center", justifyContent: "center", cursor: "pointer" }}>
            <Icon name="send" size={16} />
          </button>
        </div>
      </div>
    </div>
  );
}

export function Workbench() {
  const layout = useService(LayoutServiceId);
  const { activeSurface, railOpen } = useStore(layout.store);
  const surface = registry.get(activeSurface);
  const activity = registry.activityItems();
  const mainViews = registry.views("main", activeSurface);
  const sideViews = registry.views("primarySidebar", activeSurface);
  const auxViews = registry.views("auxiliaryBar", activeSurface);
  const showRail = railOpen && auxViews.length > 0;

  return (
    <div style={S.shell}>
      <div style={S.topbar}>
        <div style={{ display: "flex", gap: 8 }}>
          <i style={S.dot("#ec6a5e")} /><i style={S.dot("#f4bf4f")} /><i style={S.dot("#61c554")} />
        </div>
        <div style={{ fontSize: 13, color: "var(--t2)" }}>
          <b style={{ color: "var(--t1)", fontWeight: 500 }}>{surface?.activity?.label ?? "Terminal"}</b>
        </div>
        <div style={{ flex: 1 }} />
        <button aria-label="Toggle panel" onClick={() => layout.toggleRail()} style={{ background: "none", border: "none", color: "var(--t3)", cursor: "pointer", display: "flex" }}>
          <Icon name="panel" size={16} />
        </button>
      </div>

      <div style={{ flex: 1, display: "flex", minHeight: 0 }}>
        <aside style={S.left}>
          <div style={S.brand}><div style={S.logo}>V</div><div><b style={{ fontSize: 13, fontWeight: 500, display: "block", lineHeight: 1.2 }}>Vexa EI</b><span style={{ fontSize: 11, color: "var(--t3)" }}>terminal</span></div></div>
          <nav style={S.nav}>
            {activity.map((item) => (
              <button key={item.id} style={S.navItem(item.id === activeSurface)} onClick={() => layout.setActiveSurface(item.id)}>
                <span style={{ display: "flex" }}><Icon name={item.icon} size={16} /></span>{item.label}
                {item.live && <span style={S.pulse} />}
              </button>
            ))}
          </nav>
          <div style={{ height: 1, background: "var(--line)", margin: "8px 12px" }} />
          <div style={S.ctx}>{sideViews.map((v) => <v.component key={v.id} surfaceId={activeSurface} />)}</div>
          <div style={S.dep}><span style={{ width: 7, height: 7, borderRadius: "50%", background: "var(--green)", flex: "none" }} />Self-hosted · air-gapped</div>
        </aside>

        <main style={S.main}>
          <div style={S.vhead}><span style={{ display: "flex", color: surface?.activity?.live ? "var(--live)" : "var(--t1)" }}><Icon name={surface?.activity?.icon ?? "msg"} size={20} /></span>{surface?.activity?.label}</div>
          <div style={S.body}>{mainViews.map((v) => <v.component key={v.id} surfaceId={activeSurface} />)}</div>
          {surface?.composer?.enabled && <Composer surfaceId={activeSurface} placeholder={surface.composer.placeholder} quickChips={surface.composer.quickChips} />}
        </main>

        {showRail && (
          <aside style={S.right}>{auxViews.map((v) => <v.component key={v.id} surfaceId={activeSurface} />)}</aside>
        )}
      </div>
    </div>
  );
}
