"use client";
/** The token section: every colour role as a swatch with its contrast ratio COMPUTED IN THE PAGE
 *  from the live CSS variables of its frame's theme — the same `contrastRatio` the gate (G7) runs —
 *  plus the type scale, space, radius, elevation and motion tokens. */
import { useEffect, useRef, useState } from "react";
import { contrastRatio } from "../../../ui-kit";

const TEXT = ["--text-1", "--text-2", "--text-3", "--accent-text", "--danger-text", "--warning-text", "--success-text", "--info-text", "--meeting-text"];
const SURFACES = ["--surface-0", "--surface-1", "--surface-2", "--surface-3"];
const FILLS = ["--accent", "--danger", "--warning", "--success", "--info", "--meeting", "--border-subtle", "--border", "--border-control"];

function Ratios() {
  const box = useRef<HTMLDivElement>(null);
  const [rows, setRows] = useState<{ fg: string; cells: { bg: string; r: number | null }[] }[]>([]);
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const cs = getComputedStyle(el);
    const v = (n: string) => cs.getPropertyValue(n).trim();
    setRows(TEXT.map((fg) => ({ fg, cells: SURFACES.map((bg) => ({ bg, r: contrastRatio(v(fg), v(bg)) })) })));
  }, []);
  return (
    <div ref={box} className="vx-cat-ratios">
      <table className="vx-cat-table">
        <thead><tr><th>Text token</th>{SURFACES.map((s) => <th key={s}><code>{s}</code></th>)}</tr></thead>
        <tbody>{rows.map((r) => (
          <tr key={r.fg}><td><code>{r.fg}</code></td>{r.cells.map((c) => (
            <td key={c.bg} style={{ background: `var(${c.bg})`, color: `var(${r.fg})` }}>
              {c.r === null ? "?" : `${c.r.toFixed(2)}:1`}{c.r !== null && c.r < 4.5 ? " ✗" : ""}
            </td>))}</tr>))}</tbody>
      </table>
    </div>
  );
}

export function TokensSection() {
  return (
    <div className="vx-cat-col">
      <Ratios />
      <div className="vx-cat-swatches">
        {[...SURFACES, "--surface-overlay", ...FILLS].map((t) => (
          <div key={t} className="vx-cat-swatch"><span className="vx-cat-chip" style={{ background: `var(${t})` }} /><code>{t}</code></div>
        ))}
      </div>
      <div className="vx-cat-col">
        {(["2xl", "xl", "lg", "md", "sm", "xs"] as const).map((s) => (
          <div key={s} style={{ fontSize: `var(--text-${s})`, lineHeight: `var(--leading-${s})` }}><code>--text-{s}</code> The conversation stays readable at any width.</div>
        ))}
        <div style={{ fontFamily: "var(--font-mono)" }}><code>--font-mono</code> kg/entities/meeting/2026-10-08.md</div>
      </div>
      <div className="vx-cat-row">
        {["sm", "md", "lg", "xl"].map((r) => <div key={r} className="vx-cat-radius" style={{ borderRadius: `var(--radius-${r})` }}>radius-{r}</div>)}
        {["1", "2", "3"].map((e) => <div key={e} className="vx-cat-radius" style={{ boxShadow: `var(--shadow-${e})` }}>shadow-{e}</div>)}
      </div>
      <div className="vx-cat-row">
        {["0_5", "1", "2", "3", "4", "6", "8", "12"].map((s) => <div key={s} className="vx-cat-space"><span style={{ width: `var(--space-${s})` }} /><code>{s}</code></div>)}
      </div>
    </div>
  );
}
