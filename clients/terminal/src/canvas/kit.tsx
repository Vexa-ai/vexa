"use client";
import { useState, type ReactNode } from "react";
import { Markdown as MarkdownView } from "../ui-kit/Markdown";

type Tone = "default" | "accent" | "green" | "warn";
type Size = "sm" | "md" | "lg";
type Align = "left" | "center" | "right";

const toneColor: Record<Tone, string> = {
  default: "var(--t2)",
  accent: "var(--accent)",
  green: "var(--green)",
  warn: "var(--live)",
};

const toneBg: Record<Tone, string> = {
  default: "var(--panel2)",
  accent: "var(--accentbg)",
  green: "var(--greenbg)",
  warn: "var(--livebg)",
};

const gap = { sm: 6, md: 10, lg: 14 };
const textSize = { sm: 12, md: 13, lg: 15 };

function Empty({ title = "Nothing yet", body }: { title?: string; body?: string }) {
  return (
    <div style={{ border: "1px dashed var(--line2)", borderRadius: 8, padding: 18, color: "var(--t3)", textAlign: "center", fontSize: 13 }}>
      <div style={{ color: "var(--t2)", fontWeight: 600, marginBottom: body ? 4 : 0 }}>{title}</div>
      {body && <div style={{ lineHeight: 1.5 }}>{body}</div>}
    </div>
  );
}

function Panel({ title, subtitle, tone = "default", children }: { title?: string; subtitle?: string; tone?: Tone; children?: ReactNode }) {
  return (
    <section style={{ background: "var(--panel)", border: `1px solid ${tone === "default" ? "var(--line)" : toneColor[tone]}`, borderRadius: 8, padding: 14, minWidth: 0 }}>
      {(title || subtitle) && (
        <header style={{ marginBottom: children ? 12 : 0 }}>
          {title && <div style={{ color: "var(--t1)", fontSize: 14, fontWeight: 650 }}>{title}</div>}
          {subtitle && <div style={{ color: "var(--t3)", fontSize: 12, marginTop: 3, lineHeight: 1.45 }}>{subtitle}</div>}
        </header>
      )}
      {children}
    </section>
  );
}

function Section({ title, children }: { title?: string; children?: ReactNode }) {
  return (
    <section style={{ minWidth: 0 }}>
      {title && <div style={{ color: "var(--t3)", fontSize: 11, fontWeight: 700, textTransform: "uppercase", letterSpacing: ".04em", marginBottom: 8 }}>{title}</div>}
      {children}
    </section>
  );
}

function Grid({ columns = "auto", size = "md", children }: { columns?: 1 | 2 | 3 | 4 | "auto"; size?: Size; children?: ReactNode }) {
  const template = columns === "auto" ? "repeat(auto-fit, minmax(190px, 1fr))" : `repeat(${columns}, minmax(0, 1fr))`;
  return <div style={{ display: "grid", gridTemplateColumns: template, gap: gap[size], minWidth: 0 }}>{children}</div>;
}

function Row({ align = "left", size = "md", children }: { align?: Align; size?: Size; children?: ReactNode }) {
  const justify = align === "right" ? "flex-end" : align === "center" ? "center" : "flex-start";
  return <div style={{ display: "flex", alignItems: "center", justifyContent: justify, gap: gap[size], minWidth: 0, flexWrap: "wrap" }}>{children}</div>;
}

function Col({ size = "md", children }: { size?: Size; children?: ReactNode }) {
  return <div style={{ display: "flex", flexDirection: "column", gap: gap[size], minWidth: 0 }}>{children}</div>;
}

function Card({ title, body, ts, tone = "default", children }: { title?: string; body?: string; ts?: string | number; tone?: Tone; children?: ReactNode }) {
  return (
    <article style={{ background: tone === "default" ? "var(--panel)" : toneBg[tone], border: "1px solid var(--line)", borderRadius: 8, padding: 11, minWidth: 0 }}>
      <div style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
        {title && <div style={{ color: "var(--t1)", fontSize: 13, fontWeight: 650, lineHeight: 1.35, flex: 1 }}>{title}</div>}
        {ts != null && <div style={{ color: "var(--t3)", fontSize: 11, fontFamily: "var(--mono)", flex: "none" }}>{String(ts)}</div>}
      </div>
      {body && <div style={{ color: "var(--t2)", fontSize: 12.5, lineHeight: 1.45, marginTop: title ? 5 : 0 }}>{body}</div>}
      {children && <div style={{ marginTop: title || body ? 9 : 0 }}>{children}</div>}
    </article>
  );
}

function Stat({ label, value, delta, tone = "default", size = "md" }: { label: string; value: string | number; delta?: string | number; tone?: Tone; size?: Size }) {
  return (
    <div style={{ background: "var(--panel)", border: "1px solid var(--line)", borderRadius: 8, padding: size === "lg" ? 14 : 11, minWidth: 0 }}>
      <div style={{ color: "var(--t3)", fontSize: 11, textTransform: "uppercase", letterSpacing: ".04em", fontWeight: 700 }}>{label}</div>
      <div style={{ color: toneColor[tone] === "var(--t2)" ? "var(--t1)" : toneColor[tone], fontSize: size === "lg" ? 26 : size === "sm" ? 18 : 22, fontWeight: 700, marginTop: 5, lineHeight: 1 }}>{value}</div>
      {delta != null && <div style={{ color: "var(--t3)", fontSize: 12, marginTop: 5 }}>{delta}</div>}
    </div>
  );
}

type Column = string | { key: string; label: string; align?: Align };

function Table({ columns, rows, empty = "No rows" }: { columns?: Column[]; rows: Record<string, unknown>[] | unknown[][]; empty?: string }) {
  if (!rows.length) return <Empty title={empty} />;
  const cols = columns ?? (Array.isArray(rows[0]) ? (rows[0] as unknown[]).map((_, i) => String(i)) : Object.keys(rows[0] as Record<string, unknown>));
  const normalized = cols.map((c) => typeof c === "string" ? { key: c, label: c, align: "left" as Align } : { align: "left" as Align, ...c });
  return (
    <div style={{ overflowX: "auto", border: "1px solid var(--line)", borderRadius: 8 }}>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12.5 }}>
        <thead>
          <tr>{normalized.map((c) => <th key={c.key} style={{ textAlign: c.align, color: "var(--t3)", fontSize: 11, textTransform: "uppercase", letterSpacing: ".04em", padding: "8px 10px", borderBottom: "1px solid var(--line)" }}>{c.label}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((row, ri) => (
            <tr key={ri}>
              {normalized.map((c, ci) => {
                const value = Array.isArray(row) ? row[ci] : row[c.key];
                return <td key={c.key} style={{ color: "var(--t2)", textAlign: c.align, padding: "8px 10px", borderTop: ri ? "1px solid var(--line)" : "none", verticalAlign: "top" }}>{String(value ?? "")}</td>;
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

type ListItem = string | { title: string; body?: string; meta?: string | number; tone?: Tone };

function List({ items, empty = "No items" }: { items: ListItem[]; empty?: string }) {
  if (!items.length) return <Empty title={empty} />;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
      {items.map((item, i) => {
        const obj = typeof item === "string" ? { title: item } : item;
        return (
          <div key={`${obj.title}-${i}`} style={{ border: "1px solid var(--line)", borderRadius: 8, background: "var(--panel)", padding: "8px 10px" }}>
            <div style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
              <div style={{ color: toneColor[obj.tone ?? "default"] === "var(--t2)" ? "var(--t1)" : toneColor[obj.tone ?? "default"], fontWeight: 600, fontSize: 13, flex: 1 }}>{obj.title}</div>
              {obj.meta != null && <div style={{ color: "var(--t3)", fontFamily: "var(--mono)", fontSize: 11 }}>{String(obj.meta)}</div>}
            </div>
            {obj.body && <div style={{ color: "var(--t2)", fontSize: 12, lineHeight: 1.45, marginTop: 4 }}>{obj.body}</div>}
          </div>
        );
      })}
    </div>
  );
}

function Timeline({ items, empty = "No events" }: { items: { id?: string; title: string; body?: string; ts?: string | number; kind?: string }[]; empty?: string }) {
  if (!items.length) return <Empty title={empty} />;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      {items.map((item, i) => (
        <div key={item.id ?? `${item.title}-${i}`} style={{ display: "grid", gridTemplateColumns: "74px minmax(0, 1fr)", gap: 10 }}>
          <div style={{ color: "var(--t3)", fontFamily: "var(--mono)", fontSize: 11, paddingTop: 2 }}>{item.ts != null ? String(item.ts) : item.kind ?? ""}</div>
          <Card title={item.title} body={item.body} />
        </div>
      ))}
    </div>
  );
}

function Transcript({ segments, liveCaption, empty = "Waiting for transcript" }: { segments: { speaker?: string; text: string; ts?: string | number }[]; liveCaption?: string; empty?: string }) {
  if (!segments.length && !liveCaption) return <Empty title={empty} />;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {liveCaption && <div style={{ color: "var(--t1)", background: "var(--panel2)", border: "1px solid var(--line2)", borderRadius: 8, padding: "8px 10px", fontSize: 13 }}>{liveCaption}</div>}
      {segments.map((s, i) => (
        <div key={`${s.ts ?? i}-${s.speaker ?? "speaker"}`} style={{ display: "grid", gridTemplateColumns: "92px minmax(0, 1fr)", gap: 9, fontSize: 12.5 }}>
          <div style={{ color: "var(--t3)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{s.speaker ?? "Speaker"}</div>
          <div style={{ color: "var(--t2)", lineHeight: 1.45 }}>{s.text}</div>
        </div>
      ))}
    </div>
  );
}

function Chart({ kind = "bar", data, tone = "accent" }: { kind?: "bar" | "line"; data: (number | { label?: string; value: number })[]; tone?: Tone }) {
  const values = data.map((d) => typeof d === "number" ? d : d.value).filter(Number.isFinite);
  const max = Math.max(1, ...values);
  const points = values.map((v, i) => `${(i / Math.max(1, values.length - 1)) * 100},${42 - (v / max) * 36 + 3}`).join(" ");
  return (
    <svg viewBox="0 0 100 48" preserveAspectRatio="none" style={{ width: "100%", height: 110, display: "block", background: "var(--panel)", border: "1px solid var(--line)", borderRadius: 8 }}>
      {kind === "line"
        ? <polyline points={points} fill="none" stroke={toneColor[tone]} strokeWidth="2" vectorEffect="non-scaling-stroke" />
        : values.map((v, i) => {
          const w = 80 / Math.max(1, values.length);
          const h = (v / max) * 36;
          return <rect key={i} x={10 + i * w} y={42 - h} width={Math.max(2, w - 2)} height={h} rx="1" fill={toneColor[tone]} />;
        })}
    </svg>
  );
}

function Badge({ children, tone = "default" }: { children: ReactNode; tone?: Tone }) {
  return <span style={{ display: "inline-flex", alignItems: "center", padding: "2px 7px", borderRadius: 5, background: toneBg[tone], color: toneColor[tone], fontSize: 11, fontWeight: 700 }}>{children}</span>;
}

function Tag({ children, tone = "default" }: { children: ReactNode; tone?: Tone }) {
  return <Badge tone={tone}>{children}</Badge>;
}

function Button({ children, tone = "default", size = "md", disabled, onClick }: { children: ReactNode; tone?: Tone; size?: Size; disabled?: boolean; onClick?: () => void }) {
  return (
    <button onClick={onClick} disabled={disabled} style={{ border: "1px solid var(--line2)", borderRadius: 7, background: disabled ? "var(--panel2)" : tone === "default" ? "var(--panel)" : toneBg[tone], color: disabled ? "var(--t3)" : tone === "default" ? "var(--t1)" : toneColor[tone], padding: size === "sm" ? "4px 8px" : size === "lg" ? "9px 13px" : "7px 10px", fontSize: textSize[size], fontWeight: 650, cursor: disabled ? "default" : "pointer" }}>
      {children}
    </button>
  );
}

function Toggle({ checked, label, onChange }: { checked: boolean; label?: string; onChange?: (checked: boolean) => void }) {
  return (
    <button onClick={() => onChange?.(!checked)} style={{ display: "inline-flex", alignItems: "center", gap: 8, border: "none", background: "transparent", color: "var(--t2)", fontSize: 12.5, cursor: "pointer" }}>
      <span style={{ width: 30, height: 16, borderRadius: 999, background: checked ? "var(--greenbg)" : "var(--panel2)", border: "1px solid var(--line2)", position: "relative" }}>
        <span style={{ position: "absolute", width: 12, height: 12, top: 1, left: checked ? 15 : 1, borderRadius: "50%", background: checked ? "var(--green)" : "var(--t3)" }} />
      </span>
      {label && <span>{label}</span>}
    </button>
  );
}

function Tabs({ tabs, value, onChange }: { tabs: { id: string; label: string; content: ReactNode }[]; value?: string; onChange?: (id: string) => void }) {
  const [internal, setInternal] = useState(tabs[0]?.id ?? "");
  const active = value ?? internal;
  const current = tabs.find((t) => t.id === active) ?? tabs[0];
  return (
    <div>
      <div style={{ display: "inline-flex", padding: 3, gap: 2, background: "var(--panel)", border: "1px solid var(--line)", borderRadius: 8, marginBottom: 10 }}>
        {tabs.map((tab) => (
          <button key={tab.id} onClick={() => { setInternal(tab.id); onChange?.(tab.id); }} style={{ border: "none", borderRadius: 6, background: tab.id === active ? "var(--panel2)" : "transparent", color: tab.id === active ? "var(--t1)" : "var(--t3)", padding: "5px 9px", fontSize: 12, cursor: "pointer" }}>
            {tab.label}
          </button>
        ))}
      </div>
      <div>{current?.content}</div>
    </div>
  );
}

function Progress({ value, max = 100, tone = "accent", label }: { value: number; max?: number; tone?: Tone; label?: string }) {
  const pct = Math.max(0, Math.min(100, (value / Math.max(1, max)) * 100));
  return (
    <div>
      {label && <div style={{ color: "var(--t3)", fontSize: 12, marginBottom: 5 }}>{label}</div>}
      <div style={{ height: 7, background: "var(--panel2)", border: "1px solid var(--line)", borderRadius: 999, overflow: "hidden" }}>
        <div style={{ height: "100%", width: `${pct}%`, background: toneColor[tone] }} />
      </div>
    </div>
  );
}

function Avatar({ name, tone = "default", size = "md" }: { name: string; tone?: Tone; size?: Size }) {
  const px = size === "lg" ? 36 : size === "sm" ? 22 : 28;
  const initials = name.split(/\s+/).filter(Boolean).slice(0, 2).map((p) => p[0]?.toUpperCase()).join("") || "?";
  return <span title={name} style={{ width: px, height: px, borderRadius: "50%", display: "inline-flex", alignItems: "center", justifyContent: "center", background: toneBg[tone], color: toneColor[tone], fontSize: size === "lg" ? 13 : 11, fontWeight: 800 }}>{initials}</span>;
}

function Markdown({ children }: { children: string }) {
  return <MarkdownView>{children}</MarkdownView>;
}

export const ui = {
  Panel,
  Section,
  Grid,
  Row,
  Col,
  Card,
  Stat,
  Table,
  List,
  Timeline,
  Transcript,
  Chart,
  Badge,
  Tag,
  Button,
  Toggle,
  Tabs,
  Progress,
  Avatar,
  Markdown,
  Empty,
};
