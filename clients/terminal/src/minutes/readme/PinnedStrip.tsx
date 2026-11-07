"use client";
/** THE ROOM'S "PINNED" STRIP (UI 7ee): under the room header, the room's shared pins — each resolved
 *  for THIS viewer by the server (`GET /api/pins`), with who pinned it and when. A pin the viewer
 *  cannot open shows "Restricted" with no name (shot 91a). Absent when the room has no pins. */
import { useEffect, useState } from "react";
import { Lock, Pin } from "lucide-react";
import { openRef } from "../../ui-kit/RefChip";
import type { RefAnswer } from "../../data/workspaceApi";
import { PINS_CHANGED_EVENT, pin } from "./ObjectActions";
import "./readme.css";

export type PinRow = { ref: string; by: string; at?: number; access: "readable" | "restricted"; label?: string;
  open?: Record<string, string | number>; url?: string };

export async function readPins(workspace: string, room?: string, f: typeof fetch = fetch): Promise<PinRow[]> {
  const qs = new URLSearchParams({ workspace, ...(room ? { room } : {}) });
  try {
    const r = await f(`/api/pins?${qs}`, { cache: "no-store" });
    return r.ok ? (((await r.json()) as { pins?: PinRow[] }).pins ?? []) : [];
  } catch { return []; }
}

export function PinnedStrip({ workspace, room, canPin = false }: { workspace: string; room?: string; canPin?: boolean }) {
  const [pins, setPins] = useState<PinRow[]>([]);
  useEffect(() => {
    let live = true;
    const load = () => void readPins(workspace, room).then((p) => { if (live) setPins(p); });
    load();
    const on = () => load();
    window.addEventListener(PINS_CHANGED_EVENT, on);
    return () => { live = false; window.removeEventListener(PINS_CHANGED_EVENT, on); };
  }, [workspace, room]);
  if (!pins.length) return null;
  return (
    <div className="vx-pins" data-pinned-strip="" aria-label="Pinned">
      <Pin size={12} aria-hidden />
      {pins.map((p) => p.access === "readable"
        ? <span key={p.ref} className="vx-pin">
            <button type="button" className="vx-rd-chip" title={`Pinned by ${p.by}`} onClick={() => openRef({
              kind: p.ref.slice(2, p.ref.indexOf(":")), id: "", url: p.url ?? "", access: "readable", label: p.label ?? null,
              open: p.open ?? null, request_access: false } as RefAnswer)}>{p.label || "Untitled"}</button>
            {canPin && <button type="button" className="vx-pin-x" aria-label={`Unpin ${p.label ?? ""}`} onClick={() => {
              const inner = p.ref.slice(2, -2); const i = inner.indexOf(":");
              void pin({ kind: inner.slice(0, i), id: inner.slice(i + 1) }, { workspace, room }, false);
            }}>×</button>}
          </span>
        : <span key={p.ref} className="vx-rd-chip" data-restricted="" title={`Pinned by ${p.by}`}><Lock size={11} aria-hidden /> Restricted</span>)}
    </div>
  );
}
