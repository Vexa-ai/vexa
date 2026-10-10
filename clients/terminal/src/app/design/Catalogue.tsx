"use client";
/** The catalogue page: an index on the left, sections on the right — tokens, every registry entry
 *  in dark and light side by side (some at pane widths 320 / 480 / 720), and the fixture shell.
 *  `?view=shell` renders only the fixture shell, full screen, for the layout sweep. Each section
 *  carries `data-gallery={id}` for scripted capture. */
import { useEffect, useState } from "react";
import { Frame } from "./Frame";
import { REGISTRY } from "./registry";
import { TokensSection } from "./sections/Tokens";
import { FixtureShell, ShellSection } from "./sections/Shell";

export function Catalogue() {
  const [view, setView] = useState<string | null>(null);
  useEffect(() => { setView(new URLSearchParams(window.location.search).get("view")); }, []);
  if (view === "shell") return <div className="vx-cat-fill"><FixtureShell fill /></div>;
  const sections = [
    { id: "tokens", title: "Tokens" },
    ...REGISTRY.map((e) => ({ id: e.id, title: e.title })),
    { id: "shell", title: "Responsive shell" },
  ];
  return (
    <div className="vx-cat">
      <nav className="vx-cat-index" aria-label="Catalogue">
        <div className="vx-cat-brand">Design catalogue</div>
        <p className="vx-cat-p">Every token and primitive as it really renders, from fixtures. Guidelines: docs.vexa.ai, Governance › Terminal UI design.</p>
        {sections.map((s) => <a key={s.id} href={`#${s.id}`} className="vx-cat-link">{s.title}</a>)}
      </nav>
      <main className="vx-cat-main">
        <section id="tokens" data-gallery="tokens" className="vx-cat-section">
          <h2 className="vx-cat-h2">Tokens</h2>
          <p className="vx-cat-p">Contrast is computed in the page from each frame's live tokens, with the same function the gate runs.</p>
          <Frame><TokensSection /></Frame>
        </section>
        {REGISTRY.map((e) => (
          <section key={e.id} id={e.id} data-gallery={e.id} className="vx-cat-section">
            <h2 className="vx-cat-h2">{e.title}</h2>
            <p className="vx-cat-p">{e.note} <span className="vx-cat-components">{e.components.join(" · ")}</span></p>
            <Frame widths={e.widths}>{e.demo()}</Frame>
          </section>
        ))}
        <section id="shell" data-gallery="shell" className="vx-cat-section">
          <h2 className="vx-cat-h2">Responsive shell</h2>
          <p className="vx-cat-p">The layout machinery the Minutes shell runs, around placeholder panes. Drag the width: the rail folds to a strip at 1199, the pages panel becomes a sheet at 959, single column below 720.</p>
          <ShellSection />
        </section>
      </main>
    </div>
  );
}
