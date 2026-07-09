"use client";
/** DateTimePicker — a compact, dependency-free date+time picker (popover calendar + time slots).
 *
 *  Replaces the native `datetime-local` input (tiny hit-targets, locale-hostile formatting) on the
 *  planned-meeting surfaces. One button shows the friendly value ("Mon, Jul 13 · 10:00"); clicking
 *  opens a popover with quick-pick chips, a month grid, and a 15-minute time list. Emits an ISO
 *  string via `onChange` when BOTH halves are chosen (picking a day keeps the current time;
 *  picking a time keeps the current day). `onClear` (optional) renders a "No time" action —
 *  clearing a planned meeting's time flips it back to `idle`.
 */
import { useEffect, useMemo, useRef, useState } from "react";

const DAYS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"];
const MONTHS = ["January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December"];

function fmtValue(d: Date): string {
  return `${d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" })} · ${d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}`;
}
function sameDay(a: Date, b: Date): boolean {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}
function atTime(day: Date, h: number, m: number): Date {
  const d = new Date(day); d.setHours(h, m, 0, 0); return d;
}
/** Round UP to the next half-hour boundary — the default for a fresh plan. */
export function nextHalfHour(from = new Date()): Date {
  const d = new Date(from); d.setSeconds(0, 0);
  d.setMinutes(d.getMinutes() + (15 - (d.getMinutes() % 15)));
  return d;
}

const chipStyle = {
  fontSize: 11.5, padding: "3px 9px", borderRadius: 12, border: "1px solid var(--line)",
  background: "var(--panel)", color: "var(--t2)", cursor: "pointer", whiteSpace: "nowrap",
} as const;

export function DateTimePicker({ value, onChange, onClear, disabled, placeholder = "Pick a time" }: {
  value?: string;                       // ISO8601 (or undefined = unset)
  onChange: (iso: string) => void;
  onClear?: () => void;
  disabled?: boolean;
  placeholder?: string;
}) {
  const [open, setOpen] = useState(false);
  const selected = useMemo(() => {
    if (!value) return null;
    const d = new Date(value);
    return Number.isFinite(d.getTime()) ? d : null;
  }, [value]);
  const [viewMonth, setViewMonth] = useState(() => selected ?? new Date());
  const ref = useRef<HTMLDivElement>(null);
  const timeListRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    setViewMonth(selected ?? new Date());
    const close = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open, selected]);

  // scroll the time list to the selected (or working-hours) slot when the popover opens
  useEffect(() => {
    if (!open || !timeListRef.current) return;
    const idx = selected ? selected.getHours() * 4 + Math.floor(selected.getMinutes() / 15) : 36; // 09:00
    timeListRef.current.scrollTop = Math.max(0, idx * 26 - 52);
  }, [open, selected]);

  const base = selected ?? nextHalfHour();
  const pickDay = (day: Date) => onChange(atTime(day, base.getHours(), base.getMinutes()).toISOString());
  const pickTime = (h: number, m: number) => onChange(atTime(base, h, m).toISOString());

  // month grid (Monday-first)
  const first = new Date(viewMonth.getFullYear(), viewMonth.getMonth(), 1);
  const lead = (first.getDay() + 6) % 7;
  const daysInMonth = new Date(viewMonth.getFullYear(), viewMonth.getMonth() + 1, 0).getDate();
  const today = new Date();

  const quick: { label: string; d: Date }[] = [
    { label: "In 1 hour", d: nextHalfHour(new Date(Date.now() + 3600_000)) },
    { label: "Today 17:00", d: atTime(today, 17, 0) },
    { label: "Tomorrow 09:00", d: atTime(new Date(Date.now() + 86400_000), 9, 0) },
    { label: "Tomorrow 14:00", d: atTime(new Date(Date.now() + 86400_000), 14, 0) },
  ].filter((q) => q.d.getTime() > Date.now());

  return (
    <div ref={ref} style={{ position: "relative", display: "inline-block", minWidth: 0 }}>
      <button type="button" disabled={disabled} onClick={() => setOpen((v) => !v)}
        style={{
          display: "inline-flex", alignItems: "center", gap: 7, fontSize: 12.5, padding: "6px 10px",
          background: "var(--panel)", border: "1px solid var(--line)", borderRadius: 7,
          color: selected ? "var(--t1)" : "var(--t3)", cursor: disabled ? "default" : "pointer",
          opacity: disabled ? 0.6 : 1, whiteSpace: "nowrap",
        }}>
        <span aria-hidden style={{ fontSize: 12, lineHeight: 1, opacity: 0.7 }}>🗓</span>
        {selected ? fmtValue(selected) : placeholder}
      </button>
      {open && (
        <div role="dialog" aria-label="Pick date and time"
          style={{ position: "absolute", top: "100%", left: 0, marginTop: 6, zIndex: 60, width: 336,
            background: "var(--panel)", border: "1px solid var(--line2)", borderRadius: 10,
            boxShadow: "0 10px 32px rgba(0,0,0,.35)", padding: 12 }}>
          {quick.length > 0 && (
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 10 }}>
              {quick.map((q) => (
                <button key={q.label} type="button" style={chipStyle}
                  onClick={() => { onChange(q.d.toISOString()); setOpen(false); }}
                  onMouseEnter={(e) => (e.currentTarget.style.background = "var(--panel2)")}
                  onMouseLeave={(e) => (e.currentTarget.style.background = "var(--panel)")}>
                  {q.label}
                </button>
              ))}
            </div>
          )}
          <div style={{ display: "flex", gap: 12 }}>
            {/* calendar */}
            <div style={{ flex: "none", width: 196 }}>
              <div style={{ display: "flex", alignItems: "center", marginBottom: 6 }}>
                <button type="button" aria-label="Previous month"
                  onClick={() => setViewMonth(new Date(viewMonth.getFullYear(), viewMonth.getMonth() - 1, 1))}
                  style={{ background: "transparent", border: "none", color: "var(--t2)", cursor: "pointer", fontSize: 13, padding: "0 6px" }}>‹</button>
                <span style={{ flex: 1, textAlign: "center", fontSize: 12, fontWeight: 600, color: "var(--t1)" }}>
                  {MONTHS[viewMonth.getMonth()]} {viewMonth.getFullYear()}
                </span>
                <button type="button" aria-label="Next month"
                  onClick={() => setViewMonth(new Date(viewMonth.getFullYear(), viewMonth.getMonth() + 1, 1))}
                  style={{ background: "transparent", border: "none", color: "var(--t2)", cursor: "pointer", fontSize: 13, padding: "0 6px" }}>›</button>
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(7, 1fr)", gap: 1 }}>
                {DAYS.map((d) => (
                  <span key={d} style={{ fontSize: 9.5, color: "var(--t3)", textAlign: "center", padding: "2px 0", textTransform: "uppercase" }}>{d}</span>
                ))}
                {Array.from({ length: lead }).map((_, i) => <span key={`x${i}`} />)}
                {Array.from({ length: daysInMonth }).map((_, i) => {
                  const day = new Date(viewMonth.getFullYear(), viewMonth.getMonth(), i + 1);
                  const isSel = selected != null && sameDay(day, selected);
                  const isToday = sameDay(day, today);
                  const past = atTime(day, 23, 59).getTime() < Date.now();
                  return (
                    <button key={i} type="button" disabled={past} onClick={() => pickDay(day)}
                      style={{
                        fontSize: 11.5, padding: "4px 0", borderRadius: 6, cursor: past ? "default" : "pointer",
                        border: isToday && !isSel ? "1px solid var(--line2)" : "1px solid transparent",
                        background: isSel ? "var(--accent)" : "transparent",
                        color: isSel ? "var(--bg)" : past ? "var(--t3)" : "var(--t1)",
                        opacity: past ? 0.45 : 1, fontWeight: isSel ? 650 : 400,
                      }}
                      onMouseEnter={(e) => { if (!isSel && !past) e.currentTarget.style.background = "var(--panel2)"; }}
                      onMouseLeave={(e) => { if (!isSel) e.currentTarget.style.background = "transparent"; }}>
                      {i + 1}
                    </button>
                  );
                })}
              </div>
            </div>
            {/* time slots */}
            <div ref={timeListRef} style={{ flex: 1, maxHeight: 208, overflowY: "auto", borderLeft: "1px solid var(--line)", paddingLeft: 10 }}>
              {Array.from({ length: 96 }).map((_, i) => {
                const h = Math.floor(i / 4), m = (i % 4) * 15;
                const label = `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
                const isSel = selected != null && selected.getHours() === h
                  && selected.getMinutes() - (selected.getMinutes() % 15) === m;
                return (
                  <button key={label} type="button" onClick={() => { pickTime(h, m); setOpen(false); }}
                    style={{
                      display: "block", width: "100%", textAlign: "center", fontSize: 12, height: 26,
                      borderRadius: 6, border: "none", cursor: "pointer",
                      background: isSel ? "var(--accent)" : "transparent",
                      color: isSel ? "var(--bg)" : "var(--t2)", fontWeight: isSel ? 650 : 400,
                      fontFamily: "var(--mono)",
                    }}
                    onMouseEnter={(e) => { if (!isSel) e.currentTarget.style.background = "var(--panel2)"; }}
                    onMouseLeave={(e) => { if (!isSel) e.currentTarget.style.background = "transparent"; }}>
                    {label}
                  </button>
                );
              })}
            </div>
          </div>
          {onClear && (
            <div style={{ marginTop: 10, display: "flex", justifyContent: "flex-end" }}>
              <button type="button" onClick={() => { onClear(); setOpen(false); }}
                style={{ fontSize: 11.5, background: "transparent", border: "none", color: "var(--t3)", cursor: "pointer", textDecoration: "underline" }}>
                No time (plan stays idle)
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
