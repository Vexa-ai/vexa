"use client";
import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { Markdown as MarkdownView } from "../ui-kit/Markdown";
import { useActions } from "./actions";
import type { EntityItem, TranscriptSegment } from "./types";

type Tone = "default" | "accent" | "green" | "warn";
type Size = "sm" | "md" | "lg";
type Align = "left" | "center" | "right";
type Loadable = { loading?: boolean };

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

const gap: Record<Size, number> = { sm: 6, md: 10, lg: 14 };
const textSize: Record<Size, number> = { sm: 12, md: 13, lg: 15 };

const frameStyle: CSSProperties = { boxSizing: "border-box", minWidth: 0, maxWidth: "100%" };
const scrollStyle: CSSProperties = { ...frameStyle, maxHeight: 340, overflow: "auto" };

function asTone(value: Tone | undefined): Tone {
  return value && value in toneColor ? value : "default";
}

function asSize(value: Size | undefined): Size {
  return value === "sm" || value === "lg" ? value : "md";
}

function asAlign(value: Align | undefined): Align {
  return value === "center" || value === "right" ? value : "left";
}

function toText(value: unknown, fallback = ""): string {
  if (typeof value === "string") return value;
  if (value == null) return fallback;
  return String(value);
}

function toNumber(value: unknown, fallback = 0): number {
  const next = typeof value === "number" ? value : Number(value);
  return Number.isFinite(next) ? next : fallback;
}

function safeArray<T>(value: T[] | null | undefined): T[] {
  return Array.isArray(value) ? value : [];
}

function hasContent(children: ReactNode): boolean {
  return children !== undefined && children !== null && children !== false;
}

function lineClamp(lines = 1): CSSProperties {
  return {
    minWidth: 0,
    overflow: "hidden",
    textOverflow: "ellipsis",
    display: "-webkit-box",
    WebkitLineClamp: lines,
    WebkitBoxOrient: "vertical",
    overflowWrap: "anywhere",
  };
}

function ellipsisLine(): CSSProperties {
  return { minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" };
}

function Skeleton({ lines = 3, compact = false }: { lines?: number; compact?: boolean }) {
  const count = Math.max(1, Math.min(8, Math.floor(lines)));
  return (
    <div aria-busy="true" style={{ display: "flex", flexDirection: "column", gap: compact ? 6 : 9, width: "100%", minWidth: 0 }}>
      {Array.from({ length: count }).map((_, index) => (
        <div
          key={index}
          style={{
            height: compact ? 10 : 13,
            width: `${index % 3 === 2 ? 58 : index % 2 === 1 ? 76 : 92}%`,
            maxWidth: "100%",
            borderRadius: 999,
            background: "linear-gradient(90deg, var(--panel2), var(--line2), var(--panel2))",
            opacity: 0.72,
          }}
        />
      ))}
    </div>
  );
}

function Empty({ title = "Nothing yet", body }: { title?: string; body?: string }) {
  const safeTitle = toText(title, "Nothing yet");
  const safeBody = toText(body);
  return (
    <div style={{ ...frameStyle, border: "1px dashed var(--line2)", borderRadius: 8, padding: 18, color: "var(--t3)", textAlign: "center", fontSize: 13, overflow: "hidden" }}>
      <div style={{ color: "var(--t2)", fontWeight: 600, marginBottom: safeBody ? 4 : 0, ...lineClamp(2) }}>{safeTitle}</div>
      {safeBody && <div style={{ lineHeight: 1.5, ...lineClamp(3) }}>{safeBody}</div>}
    </div>
  );
}

function Panel({ title, subtitle, tone = "default", loading = false, children }: { title?: string; subtitle?: string; tone?: Tone; children?: ReactNode } & Loadable) {
  const safeTone = asTone(tone);
  const safeTitle = toText(title);
  const safeSubtitle = toText(subtitle);
  const content = loading ? <Skeleton lines={4} /> : children;
  return (
    <section style={{ ...frameStyle, width: "100%", background: "var(--panel)", border: `1px solid ${safeTone === "default" ? "var(--line)" : toneColor[safeTone]}`, borderRadius: 8, padding: 14, overflow: "hidden" }}>
      {(safeTitle || safeSubtitle) && (
        <header style={{ marginBottom: hasContent(content) ? 12 : 0, minWidth: 0 }}>
          {safeTitle && <div style={{ color: "var(--t1)", fontSize: 14, fontWeight: 650, ...lineClamp(1) }}>{safeTitle}</div>}
          {safeSubtitle && <div style={{ color: "var(--t3)", fontSize: 12, marginTop: 3, lineHeight: 1.45, ...lineClamp(2) }}>{safeSubtitle}</div>}
        </header>
      )}
      {hasContent(content) && <div style={{ minWidth: 0, maxWidth: "100%" }}>{content}</div>}
    </section>
  );
}

function Section({ title, loading = false, children }: { title?: string; children?: ReactNode } & Loadable) {
  const safeTitle = toText(title);
  return (
    <section style={{ ...frameStyle, width: "100%", overflow: "hidden" }}>
      {safeTitle && <div style={{ color: "var(--t3)", fontSize: 11, fontWeight: 700, textTransform: "uppercase", letterSpacing: ".04em", marginBottom: 8, ...lineClamp(1) }}>{safeTitle}</div>}
      {loading ? <Skeleton lines={4} /> : children}
    </section>
  );
}

function Grid({ columns = "auto", size = "md", loading = false, children }: { columns?: 1 | 2 | 3 | 4 | "auto"; size?: Size; children?: ReactNode } & Loadable) {
  const safeSize = asSize(size);
  const count = columns === 1 || columns === 2 || columns === 3 || columns === 4 ? columns : "auto";
  const safeGap = gap[safeSize];
  const template = count === "auto"
    ? "repeat(auto-fit, minmax(min(190px, 100%), 1fr))"
    : `repeat(auto-fit, minmax(min(100%, max(220px, calc((100% - ${safeGap * (count - 1)}px) / ${count}))), 1fr))`;
  return (
    <div style={{ ...frameStyle, display: "grid", gridTemplateColumns: template, gap: safeGap, alignItems: "stretch", width: "100%" }}>
      {loading ? Array.from({ length: count === "auto" ? 3 : count }).map((_, index) => <Panel key={index} loading />) : children}
    </div>
  );
}

function Row({ align = "left", size = "md", loading = false, children }: { align?: Align; size?: Size; children?: ReactNode } & Loadable) {
  const safeAlign = asAlign(align);
  const justify = safeAlign === "right" ? "flex-end" : safeAlign === "center" ? "center" : "flex-start";
  return (
    <div style={{ ...frameStyle, display: "flex", alignItems: "center", justifyContent: justify, gap: gap[asSize(size)], flexWrap: "wrap", width: "100%", overflow: "hidden" }}>
      {loading ? <Skeleton lines={1} compact /> : children}
    </div>
  );
}

function Col({ size = "md", loading = false, children }: { size?: Size; children?: ReactNode } & Loadable) {
  return (
    <div style={{ ...frameStyle, display: "flex", flexDirection: "column", gap: gap[asSize(size)], width: "100%", overflow: "hidden" }}>
      {loading ? <Skeleton lines={5} /> : children}
    </div>
  );
}

function Stack(props: { size?: Size; children?: ReactNode } & Loadable) {
  return <Col {...props} />;
}

function Card({ title, body, ts, tone = "default", loading = false, children }: { title?: string; body?: string; ts?: string | number; tone?: Tone; children?: ReactNode } & Loadable) {
  const safeTone = asTone(tone);
  const safeTitle = toText(title);
  const safeBody = toText(body);
  const safeTs = ts == null ? "" : toText(ts);
  if (loading) return <Panel loading />;
  return (
    <article style={{ ...frameStyle, width: "100%", background: safeTone === "default" ? "var(--panel)" : toneBg[safeTone], border: "1px solid var(--line)", borderRadius: 8, padding: 11, overflow: "hidden" }}>
      <div style={{ display: "flex", gap: 8, alignItems: "baseline", minWidth: 0 }}>
        {safeTitle && <div style={{ color: "var(--t1)", fontSize: 13, fontWeight: 650, lineHeight: 1.35, flex: 1, ...lineClamp(2) }}>{safeTitle}</div>}
        {safeTs && <div title={safeTs} style={{ color: "var(--t3)", fontSize: 11, fontFamily: "var(--mono)", flex: "none", maxWidth: 120, ...lineClamp(1) }}>{safeTs}</div>}
      </div>
      {safeBody && <div style={{ color: "var(--t2)", fontSize: 12.5, lineHeight: 1.45, marginTop: safeTitle ? 5 : 0, ...lineClamp(4) }}>{safeBody}</div>}
      {hasContent(children) && <div style={{ marginTop: safeTitle || safeBody ? 9 : 0, minWidth: 0 }}>{children}</div>}
    </article>
  );
}

function Stat({ label, value, delta, tone = "default", size = "md", loading = false }: { label?: string; value?: string | number; delta?: string | number; tone?: Tone; size?: Size } & Loadable) {
  const safeTone = asTone(tone);
  const safeSize = asSize(size);
  const safeLabel = toText(label, "Metric");
  const safeValue = toText(value, "0");
  const safeDelta = delta == null ? "" : toText(delta);
  if (loading) return <Card loading />;
  return (
    <div style={{ ...frameStyle, width: "100%", background: "var(--panel)", border: "1px solid var(--line)", borderRadius: 8, padding: safeSize === "lg" ? 14 : 11, overflow: "hidden" }}>
      <div style={{ color: "var(--t3)", fontSize: 11, textTransform: "uppercase", letterSpacing: ".04em", fontWeight: 700, ...lineClamp(1) }}>{safeLabel}</div>
      <div title={safeValue} style={{ color: toneColor[safeTone] === "var(--t2)" ? "var(--t1)" : toneColor[safeTone], fontSize: safeSize === "lg" ? 26 : safeSize === "sm" ? 18 : 22, fontWeight: 700, marginTop: 5, lineHeight: 1.05, ...lineClamp(1) }}>{safeValue}</div>
      {safeDelta && <div title={safeDelta} style={{ color: "var(--t3)", fontSize: 12, marginTop: 5, ...lineClamp(1) }}>{safeDelta}</div>}
    </div>
  );
}

type Column = string | { key: string; label?: string; align?: Align };
type TableRow = Record<string, unknown> | unknown[];

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

function inferColumns(rows: TableRow[]): Column[] {
  const first = rows[0];
  if (Array.isArray(first)) return first.map((_, index) => String(index));
  if (isRecord(first)) return Object.keys(first);
  return ["value"];
}

function readCell(row: TableRow, key: string, index: number): unknown {
  if (Array.isArray(row)) return row[index];
  if (isRecord(row)) return row[key];
  return index === 0 ? row : "";
}

function Table({ columns, rows, empty = "No rows", loading = false }: { columns?: Column[]; rows?: TableRow[]; empty?: string } & Loadable) {
  const safeRows = safeArray(rows);
  if (loading) return <Panel><Skeleton lines={5} compact /></Panel>;
  if (!safeRows.length) return <Empty title={empty} body="New data will appear here automatically." />;
  const cols = safeArray(columns).length ? safeArray(columns) : inferColumns(safeRows);
  const normalized = cols.map((column) => typeof column === "string" ? { key: column, label: column, align: "left" as Align } : { key: toText(column.key), label: toText(column.label, column.key), align: asAlign(column.align) }).filter((column) => column.key);
  if (!normalized.length) return <Empty title={empty} body="No displayable columns are available." />;
  return (
    <div style={{ ...scrollStyle, border: "1px solid var(--line)", borderRadius: 8 }}>
      <table style={{ width: "100%", tableLayout: "fixed", borderCollapse: "collapse", fontSize: 12.5 }}>
        <thead>
          <tr>{normalized.map((column) => <th key={column.key} title={column.label} style={{ textAlign: column.align, color: "var(--t3)", fontSize: 11, textTransform: "uppercase", letterSpacing: ".04em", padding: "8px 10px", borderBottom: "1px solid var(--line)", ...ellipsisLine() }}>{column.label}</th>)}</tr>
        </thead>
        <tbody>
          {safeRows.map((row, rowIndex) => (
            <tr key={rowIndex}>
              {normalized.map((column, columnIndex) => {
                const text = toText(readCell(row, column.key, columnIndex));
                return <td key={column.key} title={text} style={{ color: "var(--t2)", textAlign: column.align, padding: "8px 10px", borderTop: rowIndex ? "1px solid var(--line)" : "none", verticalAlign: "top", ...ellipsisLine() }}>{text}</td>;
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

type ListItem = string | { title?: string; body?: string; meta?: string | number; tone?: Tone };

function List({ items, empty = "No items", loading = false }: { items?: ListItem[]; empty?: string } & Loadable) {
  const safeItems = safeArray(items);
  if (loading) return <Panel><Skeleton lines={4} /></Panel>;
  if (!safeItems.length) return <Empty title={empty} body="Nothing has been surfaced yet." />;
  return (
    <div style={{ ...scrollStyle, display: "flex", flexDirection: "column", gap: 7 }}>
      {safeItems.map((item, index) => {
        const obj = typeof item === "string" ? { title: item } : item ?? {};
        const safeTone = asTone(obj.tone);
        const title = toText(obj.title, `Item ${index + 1}`);
        const body = toText(obj.body);
        const meta = obj.meta == null ? "" : toText(obj.meta);
        return (
          <div key={`${title}-${index}`} style={{ ...frameStyle, border: "1px solid var(--line)", borderRadius: 8, background: "var(--panel)", padding: "8px 10px", overflow: "hidden" }}>
            <div style={{ display: "flex", gap: 8, alignItems: "baseline", minWidth: 0 }}>
              <div title={title} style={{ color: toneColor[safeTone] === "var(--t2)" ? "var(--t1)" : toneColor[safeTone], fontWeight: 600, fontSize: 13, flex: 1, ...lineClamp(1) }}>{title}</div>
              {meta && <div title={meta} style={{ color: "var(--t3)", fontFamily: "var(--mono)", fontSize: 11, flex: "none", maxWidth: 120, ...lineClamp(1) }}>{meta}</div>}
            </div>
            {body && <div title={body} style={{ color: "var(--t2)", fontSize: 12, lineHeight: 1.45, marginTop: 4, ...lineClamp(3) }}>{body}</div>}
          </div>
        );
      })}
    </div>
  );
}

const entityDotColor: Record<EntityItem["kind"], string> = {
  person: "var(--blue)",
  company: "var(--coral, var(--accent))",
  product: "var(--purple, #a78bfa)",
  number: "var(--amber, #f6c453)",
  signal: "var(--live)",
};

function entityRef(item: EntityItem): string {
  return `[[${toText(item.name, item.title ?? "Entity")}]]`;
}

function entityKey(item: EntityItem, index: number): string {
  return toText(item.id, `${item.kind}:${item.name}:${index}`);
}

function EntityActionButton({ children, onClick }: { children?: ReactNode; onClick?: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      style={{ border: "1px solid var(--line2)", borderRadius: 6, background: "var(--panel2)", color: "var(--t2)", padding: "3px 7px", fontSize: 11.5, fontWeight: 650, cursor: "pointer", maxWidth: "100%", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
    >
      {children}
    </button>
  );
}

function EntityList({ items, empty = "No entities", loading = false }: { items?: EntityItem[]; empty?: string } & Loadable) {
  const actions = useActions();
  const [hovered, setHovered] = useState<string | null>(null);
  const [menuOpen, setMenuOpen] = useState<string | null>(null);
  const safeItems = safeArray(items);
  if (loading) return <Panel><Skeleton lines={5} compact /></Panel>;
  if (!safeItems.length) return <Empty title={empty} body="Surfaced entities will appear as the meeting unfolds." />;

  return (
    <div style={{ ...frameStyle, display: "flex", flexWrap: "wrap", alignItems: "flex-start", gap: 7, width: "100%", overflow: "visible" }}>
      {safeItems.map((rawItem, index) => {
        const key = entityKey(rawItem, index);
        const name = toText(rawItem.name, rawItem.title ?? `Entity ${index + 1}`);
        const context = toText(rawItem.context);
        const summary = toText(rawItem.summary ?? rawItem.body);
        const quote = toText(rawItem.quote);
        const docPath = toText(rawItem.docPath);
        const isMenuOpen = menuOpen === key;
        const isHovered = hovered === key;
        const canOpen = Boolean(docPath);
        const research = () => actions.research({ name, kind: rawItem.kind });
        const openDoc = () => { if (docPath) actions.openDoc(docPath); };
        const copyRef = () => actions.copyRef(entityRef({ ...rawItem, name }));
        return (
          <div
            key={key}
            onMouseEnter={() => setHovered(key)}
            onMouseLeave={() => { setHovered((current) => current === key ? null : current); setMenuOpen((current) => current === key ? null : current); }}
            style={{ ...frameStyle, position: "relative", display: "inline-flex", flexDirection: "column", alignItems: "flex-start", maxWidth: "100%", overflow: "visible" }}
          >
            <div style={{ display: "inline-flex", alignItems: "center", gap: 6, maxWidth: "100%", minHeight: 28, border: "1px solid var(--line2)", borderRadius: 7, background: "var(--panel)", padding: "3px 5px 3px 7px", boxShadow: isHovered ? "0 0 0 1px var(--line2)" : "none" }}>
              <span style={{ width: 7, height: 7, borderRadius: "50%", background: entityDotColor[rawItem.kind] ?? "var(--t3)", flex: "none" }} />
              <button
                type="button"
                title={canOpen ? docPath : name}
                onClick={canOpen ? openDoc : undefined}
                style={{ minWidth: 0, maxWidth: 168, border: "none", background: "transparent", color: canOpen ? "var(--t1)" : "var(--t2)", padding: 0, fontSize: 12.5, fontWeight: 700, cursor: canOpen ? "pointer" : "default", textAlign: "left", ...ellipsisLine() }}
              >
                {name}
              </button>
              {context && <span title={context} style={{ color: "var(--t3)", fontSize: 11, maxWidth: 74, ...ellipsisLine() }}>{context}</span>}
              {rawItem.researched === false ? (
                <button type="button" onClick={research} style={{ border: "none", background: "transparent", color: "var(--accent)", padding: "0 1px", fontSize: 11.5, fontWeight: 750, cursor: "pointer", whiteSpace: "nowrap" }}>
                  + research
                </button>
              ) : docPath ? (
                <button type="button" onClick={openDoc} style={{ border: "none", background: "transparent", color: "var(--blue)", padding: "0 1px", fontSize: 11.5, fontWeight: 700, cursor: "pointer", whiteSpace: "nowrap" }}>
                  doc
                </button>
              ) : null}
              <button
                type="button"
                aria-label={`Options for ${name}`}
                onClick={() => setMenuOpen((current) => current === key ? null : key)}
                style={{ border: "none", background: "transparent", color: "var(--t3)", padding: "0 2px", fontSize: 13, lineHeight: 1, cursor: "pointer", flex: "none" }}
              >
                ▾
              </button>
            </div>
            {isMenuOpen && (
              <div style={{ position: "absolute", top: 31, right: 0, zIndex: 5, minWidth: 130, border: "1px solid var(--line)", borderRadius: 7, background: "var(--panel)", boxShadow: "0 12px 32px rgba(0,0,0,.28)", padding: 4 }}>
                {docPath && <button type="button" onClick={openDoc} style={{ width: "100%", border: "none", borderRadius: 5, background: "transparent", color: "var(--t2)", textAlign: "left", padding: "6px 8px", fontSize: 12, cursor: "pointer" }}>Open doc</button>}
                <button type="button" onClick={research} style={{ width: "100%", border: "none", borderRadius: 5, background: "transparent", color: "var(--t2)", textAlign: "left", padding: "6px 8px", fontSize: 12, cursor: "pointer" }}>Research</button>
                <button type="button" onClick={copyRef} style={{ width: "100%", border: "none", borderRadius: 5, background: "transparent", color: "var(--t2)", textAlign: "left", padding: "6px 8px", fontSize: 12, cursor: "pointer" }}>Copy ref</button>
              </div>
            )}
            {isHovered && (summary || quote) && (
              <div style={{ ...frameStyle, width: "min(280px, calc(100vw - 48px))", marginTop: 6, border: "1px solid var(--line)", borderRadius: 8, background: "var(--panel)", boxShadow: "0 12px 32px rgba(0,0,0,.24)", padding: 9, zIndex: 4 }}>
                {summary && <div title={summary} style={{ color: "var(--t2)", fontSize: 12, lineHeight: 1.4, ...lineClamp(3) }}>{summary}</div>}
                {quote && <div title={quote} style={{ color: "var(--t3)", fontSize: 11.5, fontStyle: "italic", lineHeight: 1.4, marginTop: summary ? 6 : 0, ...lineClamp(3) }}>{quote}</div>}
                <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 8 }}>
                  {docPath && <EntityActionButton onClick={openDoc}>Open doc</EntityActionButton>}
                  <EntityActionButton onClick={research}>Research</EntityActionButton>
                  <EntityActionButton onClick={copyRef}>Copy ref</EntityActionButton>
                </div>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function Timeline({ items, empty = "No events", loading = false }: { items?: { id?: string; title?: string; body?: string; ts?: string | number; kind?: string }[]; empty?: string } & Loadable) {
  const safeItems = safeArray(items);
  if (loading) return <Panel><Skeleton lines={5} /></Panel>;
  if (!safeItems.length) return <Empty title={empty} body="Meeting events will appear as they arrive." />;
  return (
    <div style={{ ...scrollStyle, display: "flex", flexDirection: "column", gap: 10 }}>
      {safeItems.map((item, index) => {
        const title = toText(item.title, `Event ${index + 1}`);
        const marker = item.ts != null ? toText(item.ts) : toText(item.kind);
        return (
          <div key={item.id ?? `${title}-${index}`} style={{ display: "grid", gridTemplateColumns: "minmax(48px, 74px) minmax(0, 1fr)", gap: 10, minWidth: 0 }}>
            <div title={marker} style={{ color: "var(--t3)", fontFamily: "var(--mono)", fontSize: 11, paddingTop: 2, ...lineClamp(1) }}>{marker}</div>
            <Card title={title} body={item.body} />
          </div>
        );
      })}
    </div>
  );
}

function Transcript({ segments, liveCaption, empty = "Waiting for transcript", loading = false }: { segments?: TranscriptSegment[]; liveCaption?: string; empty?: string } & Loadable) {
  const safeSegments = normalizeTranscriptSegments(segments);
  const caption = toText(liveCaption);
  if (loading) return <Panel><Skeleton lines={6} /></Panel>;
  if (!safeSegments.length && !caption) return <Empty title={empty} body="Transcript lines will appear once speech is available." />;
  return (
    <div style={{ ...scrollStyle, display: "flex", flexDirection: "column", gap: 8, maxHeight: 420 }}>
      {caption && <div title={caption} style={{ color: "var(--t1)", background: "var(--panel2)", border: "1px solid var(--line2)", borderRadius: 8, padding: "8px 10px", fontSize: 13, ...lineClamp(2) }}>{caption}</div>}
      {safeSegments.map((segment, index) => {
        const speaker = toText(segment.speaker, "Speaker");
        const text = toText(segment.text);
        return (
          <div key={`${index}-${segment.ts ?? ""}-${speaker}`} style={{ display: "grid", gridTemplateColumns: "minmax(68px, 92px) minmax(0, 1fr)", gap: 9, fontSize: 12.5, minWidth: 0 }}>
            <div title={speaker} style={{ color: "var(--t3)", ...lineClamp(1) }}>{speaker}</div>
            <div title={text} style={{ color: "var(--t2)", lineHeight: 1.45, ...lineClamp(3) }}>{text}</div>
          </div>
        );
      })}
    </div>
  );
}

function LiveTranscript({ segments, liveCaption, empty = "waiting for transcript…", loading = false, maxSegments = 36 }: { segments?: TranscriptSegment[]; liveCaption?: string; empty?: string; maxSegments?: number } & Loadable) {
  const scrollerRef = useRef<HTMLDivElement>(null);
  const safeSegments = normalizeTranscriptSegments(segments);
  const limit = Math.max(1, Math.floor(toNumber(maxSegments, 36)));
  const displaySegments = safeSegments.slice(-limit);
  const caption = toText(liveCaption).trim();
  const lastSegment = displaySegments[displaySegments.length - 1];
  const captionMatchesLast = Boolean(caption && lastSegment && toText(lastSegment.text).trim() === caption);
  const showStandaloneCaption = Boolean(caption && !captionMatchesLast);
  const scrollKey = `${displaySegments.length}:${lastSegment?.ts ?? ""}:${lastSegment?.speaker ?? ""}:${lastSegment?.text ?? ""}:${caption}`;

  useEffect(() => {
    const node = scrollerRef.current;
    if (!node) return;
    node.scrollLeft = node.scrollWidth;
  }, [scrollKey]);

  const bandStyle: CSSProperties = {
    ...frameStyle,
    width: "100%",
    height: 56,
    maxHeight: 56,
    border: "1px solid var(--line)",
    borderRadius: 8,
    background: "var(--sidebar, var(--panel))",
    color: "var(--t2)",
    overflow: "hidden",
    display: "flex",
    alignItems: "center",
    padding: "0 12px",
  };

  if (loading) {
    return (
      <div style={bandStyle}>
        <Skeleton lines={2} compact />
      </div>
    );
  }

  if (!displaySegments.length && !caption) {
    return (
      <div role="status" aria-live="polite" style={bandStyle}>
        <span style={{ width: 7, height: 7, borderRadius: "50%", background: "var(--t3)", flex: "none", marginRight: 8 }} />
        <span style={{ color: "var(--t3)", fontSize: 12.5, fontStyle: "italic", ...ellipsisLine() }}>{toText(empty, "waiting for transcript…")}</span>
      </div>
    );
  }

  const captionSpeaker = toText(lastSegment?.speaker, "Live");

  return (
    <div role="log" aria-live="polite" style={bandStyle}>
      <div
        ref={scrollerRef}
        style={{
          ...frameStyle,
          flex: 1,
          height: "100%",
          overflowX: "auto",
          overflowY: "hidden",
          whiteSpace: "nowrap",
          scrollbarWidth: "thin",
        }}
      >
        <div style={{ display: "inline-flex", minWidth: "max-content", height: "100%", alignItems: "center", gap: 8, paddingRight: 2 }}>
          {displaySegments.map((segment, index) => {
            const speaker = toText(segment.speaker, "Speaker");
            const text = toText(segment.text);
            const isLive = captionMatchesLast && index === displaySegments.length - 1;
            return (
              <span
                key={`${index}-${segment.ts ?? ""}-${speaker}-${text.slice(0, 28)}`}
                style={{
                  display: "inline-flex",
                  alignItems: "baseline",
                  gap: 4,
                  flex: "none",
                  maxWidth: "none",
                  color: isLive ? "var(--t1)" : "var(--t2)",
                  background: isLive ? "var(--livebg)" : "transparent",
                  border: isLive ? "1px solid var(--line2)" : "1px solid transparent",
                  borderRadius: 7,
                  padding: isLive ? "4px 7px" : 0,
                  fontSize: 12.5,
                  lineHeight: 1.35,
                }}
              >
                {index > 0 && <span aria-hidden="true" style={{ color: "var(--t3)", marginRight: 2 }}>·</span>}
                <span style={{ color: isLive ? "var(--live)" : "var(--t3)", fontWeight: 750 }}>{speaker}:</span>
                <span>{text}</span>
              </span>
            );
          })}
          {showStandaloneCaption && (
            <>
              {displaySegments.length > 0 && <span aria-hidden="true" style={{ color: "var(--t3)", flex: "none" }}>·</span>}
              <span style={{ display: "inline-flex", alignItems: "baseline", gap: 4, flex: "none", color: "var(--t1)", background: "var(--livebg)", border: "1px solid var(--line2)", borderRadius: 7, padding: "4px 7px", fontSize: 12.5, lineHeight: 1.35 }}>
                <span style={{ color: "var(--live)", fontWeight: 800 }}>{captionSpeaker}:</span>
                <span>{caption}</span>
              </span>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function normalizeTranscriptSegments(segments: TranscriptSegment[] | undefined): TranscriptSegment[] {
  return safeArray(segments)
    .map((segment) => ({ speaker: toText(segment.speaker, "Speaker"), text: toText(segment.text), ts: segment.ts }))
    .filter((segment) => segment.text.trim());
}

function Chart({ kind = "bar", data, tone = "accent", loading = false }: { kind?: "bar" | "line"; data?: (number | { label?: string; value: number })[]; tone?: Tone } & Loadable) {
  const values = safeArray(data).map((item) => typeof item === "number" ? item : toNumber(item?.value, Number.NaN)).filter(Number.isFinite);
  if (loading) return <Panel><Skeleton lines={5} /></Panel>;
  if (!values.length) return <Empty title="No chart data" body="Metrics will appear once values are available." />;
  const max = Math.max(1, ...values);
  const points = values.map((value, index) => `${(index / Math.max(1, values.length - 1)) * 100},${42 - (value / max) * 36 + 3}`).join(" ");
  const safeTone = asTone(tone);
  return (
    <svg viewBox="0 0 100 48" preserveAspectRatio="none" style={{ width: "100%", maxWidth: "100%", height: 110, display: "block", background: "var(--panel)", border: "1px solid var(--line)", borderRadius: 8, overflow: "hidden" }}>
      {kind === "line"
        ? <polyline points={points} fill="none" stroke={toneColor[safeTone]} strokeWidth="2" vectorEffect="non-scaling-stroke" />
        : values.map((value, index) => {
          const width = 80 / Math.max(1, values.length);
          const height = (value / max) * 36;
          return <rect key={index} x={10 + index * width} y={42 - height} width={Math.max(2, width - 2)} height={height} rx="1" fill={toneColor[safeTone]} />;
        })}
    </svg>
  );
}

function Badge({ children, tone = "default", loading = false }: { children?: ReactNode; tone?: Tone } & Loadable) {
  if (loading) return <span style={{ display: "inline-flex", width: 58 }}><Skeleton lines={1} compact /></span>;
  const safeTone = asTone(tone);
  return <span style={{ ...frameStyle, display: "inline-flex", alignItems: "center", maxWidth: "100%", padding: "2px 7px", borderRadius: 5, background: toneBg[safeTone], color: toneColor[safeTone], fontSize: 11, fontWeight: 700, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{children ?? "Status"}</span>;
}

function Tag({ children, tone = "default", loading = false }: { children?: ReactNode; tone?: Tone } & Loadable) {
  return <Badge tone={tone} loading={loading}>{children}</Badge>;
}

function Button({ children, tone = "default", size = "md", disabled, loading = false, onClick }: { children?: ReactNode; tone?: Tone; size?: Size; disabled?: boolean; onClick?: () => void } & Loadable) {
  const safeTone = asTone(tone);
  const safeSize = asSize(size);
  const isDisabled = Boolean(disabled || loading);
  return (
    <button type="button" onClick={isDisabled ? undefined : onClick} disabled={isDisabled} style={{ ...frameStyle, border: "1px solid var(--line2)", borderRadius: 7, background: isDisabled ? "var(--panel2)" : safeTone === "default" ? "var(--panel)" : toneBg[safeTone], color: isDisabled ? "var(--t3)" : safeTone === "default" ? "var(--t1)" : toneColor[safeTone], padding: safeSize === "sm" ? "4px 8px" : safeSize === "lg" ? "9px 13px" : "7px 10px", fontSize: textSize[safeSize], fontWeight: 650, cursor: isDisabled ? "default" : "pointer", maxWidth: "100%", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
      {loading ? "Loading" : children ?? "Action"}
    </button>
  );
}

function Toggle({ checked = false, label, loading = false, onChange }: { checked?: boolean; label?: string; onChange?: (checked: boolean) => void } & Loadable) {
  if (loading) return <Skeleton lines={1} compact />;
  const isChecked = Boolean(checked);
  const safeLabel = toText(label);
  return (
    <button type="button" onClick={() => onChange?.(!isChecked)} style={{ ...frameStyle, display: "inline-flex", alignItems: "center", gap: 8, border: "none", background: "transparent", color: "var(--t2)", fontSize: 12.5, cursor: "pointer", maxWidth: "100%", overflow: "hidden" }}>
      <span style={{ width: 30, height: 16, borderRadius: 999, background: isChecked ? "var(--greenbg)" : "var(--panel2)", border: "1px solid var(--line2)", position: "relative", flex: "none" }}>
        <span style={{ position: "absolute", width: 12, height: 12, top: 1, left: isChecked ? 15 : 1, borderRadius: "50%", background: isChecked ? "var(--green)" : "var(--t3)" }} />
      </span>
      {safeLabel && <span title={safeLabel} style={{ ...lineClamp(1) }}>{safeLabel}</span>}
    </button>
  );
}

function Tabs({ tabs, value, loading = false, onChange }: { tabs?: { id: string; label: string; content: ReactNode }[]; value?: string; onChange?: (id: string) => void } & Loadable) {
  const safeTabs = safeArray(tabs).filter((tab) => tab?.id);
  const [internal, setInternal] = useState(safeTabs[0]?.id ?? "");
  if (loading) return <Panel><Skeleton lines={5} /></Panel>;
  if (!safeTabs.length) return <Empty title="No tabs" body="Add tab content to show controls here." />;
  const active = value ?? internal ?? safeTabs[0]?.id;
  const current = safeTabs.find((tab) => tab.id === active) ?? safeTabs[0];
  return (
    <div style={{ ...frameStyle, width: "100%", overflow: "hidden" }}>
      <div style={{ display: "inline-flex", maxWidth: "100%", padding: 3, gap: 2, background: "var(--panel)", border: "1px solid var(--line)", borderRadius: 8, marginBottom: 10, overflow: "auto" }}>
        {safeTabs.map((tab) => {
          const label = toText(tab.label, tab.id);
          return (
            <button key={tab.id} type="button" onClick={() => { setInternal(tab.id); onChange?.(tab.id); }} title={label} style={{ border: "none", borderRadius: 6, background: tab.id === active ? "var(--panel2)" : "transparent", color: tab.id === active ? "var(--t1)" : "var(--t3)", padding: "5px 9px", fontSize: 12, cursor: "pointer", maxWidth: 160, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: "none" }}>
              {label}
            </button>
          );
        })}
      </div>
      <div style={{ minWidth: 0, maxWidth: "100%" }}>{current?.content}</div>
    </div>
  );
}

function Progress({ value = 0, max = 100, tone = "accent", label, loading = false }: { value?: number; max?: number; tone?: Tone; label?: string } & Loadable) {
  if (loading) return <Skeleton lines={2} compact />;
  const safeMax = Math.max(1, toNumber(max, 100));
  const pct = Math.max(0, Math.min(100, (toNumber(value) / safeMax) * 100));
  const safeLabel = toText(label);
  const safeTone = asTone(tone);
  return (
    <div style={{ ...frameStyle, width: "100%", overflow: "hidden" }}>
      {safeLabel && <div title={safeLabel} style={{ color: "var(--t3)", fontSize: 12, marginBottom: 5, ...lineClamp(1) }}>{safeLabel}</div>}
      <div style={{ height: 7, background: "var(--panel2)", border: "1px solid var(--line)", borderRadius: 999, overflow: "hidden" }}>
        <div style={{ height: "100%", width: `${pct}%`, background: toneColor[safeTone] }} />
      </div>
    </div>
  );
}

function Avatar({ name, tone = "default", size = "md", loading = false }: { name?: string; tone?: Tone; size?: Size } & Loadable) {
  if (loading) return <Skeleton lines={1} compact />;
  const safeSize = asSize(size);
  const px = safeSize === "lg" ? 36 : safeSize === "sm" ? 22 : 28;
  const safeName = toText(name, "Unknown");
  const initials = safeName.split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0]?.toUpperCase()).join("") || "?";
  const safeTone = asTone(tone);
  return <span title={safeName} style={{ width: px, height: px, minWidth: px, borderRadius: "50%", display: "inline-flex", alignItems: "center", justifyContent: "center", background: toneBg[safeTone], color: toneColor[safeTone], fontSize: safeSize === "lg" ? 13 : 11, fontWeight: 800, overflow: "hidden" }}>{initials}</span>;
}

function Markdown({ children, loading = false }: { children?: string } & Loadable) {
  const text = toText(children);
  if (loading) return <Panel><Skeleton lines={5} /></Panel>;
  if (!text.trim()) return <Empty title="No content" body="Markdown content will appear here." />;
  return (
    <div style={{ ...frameStyle, width: "100%", overflow: "hidden" }}>
      <MarkdownView>{text}</MarkdownView>
    </div>
  );
}

export const ui = {
  Panel,
  Section,
  Grid,
  Row,
  Col,
  Stack,
  Card,
  Stat,
  Table,
  List,
  EntityList,
  Timeline,
  Transcript,
  LiveTranscript,
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
