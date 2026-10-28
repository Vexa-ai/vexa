"use client";
/** The fixture shell (guidelines §3, §9): the SAME layout machinery the Minutes shell runs —
 *  `useShellLayout`, `shellLayout()`, `Splitter`, `Sheet`, `Drawer`, `Fold`, `OverflowStrip`,
 *  `KeyValue` — around placeholder panes, so the responsive rules are visible and sweepable without
 *  a session or a single real record. The layout sweep (`scripts/layout-sweep.mjs`) loads it
 *  full-screen at `/design?view=shell`; inside the catalogue it sits in a box with a width slider.
 *  It uses the same `data-pane` / `data-composer-toolbar` hooks as the real shell. */
import { useRef, useState } from "react";
import { ChevronDown, ChevronLeft, ChevronRight, Ellipsis, FileText, Gauge, Menu as MenuIcon, PanelLeft, PenLine, Plus, Send } from "lucide-react";
import { Drawer, Fold, IconButton, KeyValue, ListRow, Menu, OverflowStrip, Sheet, Splitter, kvValue, useShellLayout } from "../../../ui-kit";
import { CHATS, PROPERTIES, TABS, WORKSPACE } from "../fixtures";

const S = 16;

export function FixtureShell({ fill }: { fill?: boolean }) {
  const root = useRef<HTMLDivElement>(null);
  const shell = useShellLayout(root, { storageKey: "vexa.shell.catalogue" });
  const L = shell.layout;
  const [tab, setTab] = useState(TABS[0]);
  const overlay = L.pagesKind === "sheet" || L.pagesKind === "fullscreen";

  const rail = (inDrawer: boolean) => (
    <nav className="vx-pane vx-cat-rail" data-pane="rail" aria-label="Chats">
      <div className="vx-cat-head"><span className="vx-cat-head-title">Chats</span>
        <IconButton label="New chat"><Plus size={S} strokeWidth={1.75} /></IconButton>
        <IconButton label={inDrawer ? "Close the chat list" : "Hide the chat list"} onClick={() => shell.setRailOpen(false)}><ChevronLeft size={S} strokeWidth={1.75} /></IconButton></div>
      <div className="vx-cat-pad">{CHATS.map((c, i) => <ListRow key={c} title={c} meta={i === 0 ? "6:29 PM" : "Thu"} selected={i === 0} onSelect={() => shell.closeOverlays()} />)}</div>
    </nav>
  );
  const pages = (
    <>
      <div className="vx-pane vx-cat-head" data-pane="pages-header" style={{ gridRow: 1, gridColumn: 3 }}>
        <OverflowStrip label="tabs" activeKey={tab} items={TABS.map((t) => ({ key: t, label: t, onSelect: () => setTab(t) }))}>
          {TABS.map((t) => <button key={t} type="button" data-strip-key={t} className="vx-tab" aria-pressed={t === tab} onClick={() => setTab(t)}>{t}</button>)}
        </OverflowStrip>
        <IconButton label={overlay ? "Back to the conversation" : "Hide the pages panel"} onClick={() => shell.setPagesOpen(false)}><ChevronRight size={S} strokeWidth={1.75} /></IconButton>
      </div>
      <div className="vx-pane vx-cat-pages" data-pane="pages" style={{ gridRow: 2, gridColumn: 3 }}>
        <h3 className="vx-cat-h3">{tab}</h3>
        <KeyValue items={Object.entries(PROPERTIES).map(([key, value]) => ({ key, value: kvValue(value) }))} />
        <p className="vx-cat-p">Placeholder page text that wraps at spaces and never breaks a word, whatever the width of this pane.</p>
      </div>
    </>
  );

  return (
    <div ref={root} data-shell-mode={L.mode} data-rail={L.railKind} data-pages={L.pagesKind}
      className="vx-cat-shell" data-fill={fill ? "" : undefined} style={{ gridTemplateColumns: L.columns }}>
      {L.railKind === "docked" ? rail(false) : L.railKind === "strip" ? (
        <nav className="vx-rail-strip" data-pane="rail-strip" aria-label="Chats">
          <IconButton label="Show the chat list" onClick={() => shell.setRailOpen(true)}><PanelLeft size={S} strokeWidth={1.75} /></IconButton>
          <IconButton label="New chat"><Plus size={S} strokeWidth={1.75} /></IconButton>
        </nav>) : null}
      {L.railKind !== "docked" && <Drawer open={shell.drawerOpen} onClose={() => shell.setRailOpen(false)} width={L.widths.drawer} label="Chats">{rail(true)}</Drawer>}
      {L.railKind === "docked" && L.bounds.rail && <Splitter label="Resize chat list" value={L.widths.rail} bounds={L.bounds.rail} grows="right"
        onPreview={(w) => shell.previewWidth("rail", w)} onCommit={(w) => shell.commitWidth("rail", w)} style={{ left: L.widths.rail - 4 }} />}

      <div className="vx-pane vx-cat-head" data-pane="header" style={{ gridRow: 1, gridColumn: 2 }}>
        {L.railKind === "drawer" && <IconButton label="Open the chat list" onClick={() => shell.setRailOpen(true)}><MenuIcon size={S} strokeWidth={1.75} /></IconButton>}
        <span className="vx-cat-head-title">Chat › Weekly sync with a deliberately long title that has to truncate</span>
        <Fold at={520} wide={<span className="vx-chip" data-selected=""><span className="vx-chip-main"><PenLine size={14} strokeWidth={1.75} aria-hidden /><span className="vx-chip-label">Writes to personal</span></span></span>}
          narrow={<Menu label="Workspaces for this chat" variant="chip" align="end" trigger={<><PenLine size={14} strokeWidth={1.75} aria-hidden />Writes to personal<ChevronDown size={14} strokeWidth={1.75} aria-hidden /></>}
            items={[{ key: "p", label: "personal", checked: true, onSelect: () => {} }, { key: "w", label: WORKSPACE, checked: false, onSelect: () => {} }]} />} />
        {overlay && <IconButton label={shell.sheetOpen ? "Close the pages panel" : "Open the pages panel"} pressed={shell.sheetOpen} onClick={() => shell.setPagesOpen(!shell.sheetOpen)}><FileText size={S} strokeWidth={1.75} /></IconButton>}
      </div>
      <main className="vx-pane vx-cat-conv" data-pane="conversation" style={{ gridRow: 2, gridColumn: 2 }}>
        <div className="vx-cat-turns">
          <div className="vx-cat-turn" data-who="you">Summarise the weekly sync and list the open questions.</div>
          <div className="vx-cat-turn">The sync agreed the rollout order. Three questions are open: pricing for the pilot, the security review date, and who owns onboarding. A long unbreakable token like {"example-workspace/meetings/2026-10-08-weekly-sync.md"} wraps at its slashes rather than letter by letter.</div>
        </div>
        <div className="vx-cat-composer">
          <textarea className="vx-cat-textarea" placeholder="Type / for skills, or ask the agent…" rows={2} aria-label="Message" />
          <div data-composer-toolbar className="vx-container vx-cat-toolbar">
            <Fold at={400} wide={<><IconButton label="Attach files"><Plus size={S} strokeWidth={1.75} /></IconButton><IconButton label="Dictate"><FileText size={S} strokeWidth={1.75} /></IconButton></>}
              narrow={<Menu label="More composer actions" placement="top" trigger={<Ellipsis size={S} strokeWidth={1.75} aria-hidden />} items={[{ key: "a", label: "Attach files", onSelect: () => {} }, { key: "d", label: "Dictate", onSelect: () => {} }]} />} />
            <span className="vx-cat-toolbar-gap" />
            <span className="vx-cat-model">Example Model 7B</span>
            <Fold at={560} wide={<span className="vx-cat-model">High</span>} narrow={<Gauge size={14} strokeWidth={1.75} aria-label="Effort: high" />} />
            <IconButton label="Send" variant="primary"><Send size={S} strokeWidth={1.75} /></IconButton>
          </div>
        </div>
      </main>

      {L.pagesKind === "docked" && L.bounds.pages && <Splitter label="Resize pages panel" value={L.widths.pages} bounds={L.bounds.pages} grows="left"
        onPreview={(w) => shell.previewWidth("pages", w)} onCommit={(w) => shell.commitWidth("pages", w)} style={{ right: L.widths.pages - 4 }} />}
      {L.pagesKind === "collapsed"
        ? <div className="vx-cat-edge" style={{ gridColumn: 3, gridRow: "1 / 3" }}><IconButton label="Show the pages panel" size="xs" onClick={() => shell.setPagesOpen(true)}><ChevronLeft size={14} strokeWidth={1.75} /></IconButton></div>
        : <Sheet form={L.pagesKind === "sheet" ? "overlay" : L.pagesKind === "fullscreen" ? "fullscreen" : "inline"} open={shell.sheetOpen}
            onClose={() => shell.setPagesOpen(false)} width={L.widths.sheet} label="Pages" dataPane="pages-sheet">{pages}</Sheet>}
    </div>
  );
}

/** In the catalogue: the fixture shell in a box whose width a slider sets. */
export function ShellSection() {
  const [w, setW] = useState(1024);
  return (
    <div className="vx-cat-col">
      <label className="vx-cat-row"><span>Width {w}px</span>
        <input type="range" min={320} max={1920} step={8} value={w} onChange={(e) => setW(Number(e.target.value))} aria-label="Fixture shell width" />
        {[360, 412, 640, 820, 1024, 1280, 1440].map((p) => <button key={p} type="button" className="vx-btn" data-variant="ghost" onClick={() => setW(p)}>{p}</button>)}
      </label>
      <div className="vx-cat-shellbox" style={{ width: w }}><FixtureShell /></div>
      <p className="vx-cat-p">Full screen, for the layout sweep: <a className="vx-link" href="/design?view=shell">/design?view=shell</a>.</p>
    </div>
  );
}
