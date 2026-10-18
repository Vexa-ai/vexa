"use client";
/** Who is in a call: an avatar, a video tile, the roster line, and Vexa's own presence. */
import { Identity } from "../../ui-kit/Identity";
import type { CSSProperties, ReactNode } from "react";
import { MicOff } from "lucide-react";
import type { AgentState, CallPerson } from "./types";
import { PersonName } from "../people/PersonName";

/** A stable hue per person, so a camera-off tile is recognisable at a glance. */
export function hueOf(key: string): number {
  let h = 0;
  for (let i = 0; i < key.length; i++) h = (h * 31 + key.charCodeAt(i)) % 360;
  return h;
}

const TINTS = ["violet", "blue", "green", "warn", "meeting", "info"];
/** A person's tint (one of six token colours) and a size, as the custom properties the CSS reads. */
export function ckVars(id?: string, size?: number): CSSProperties {
  return {
    ...(id ? { "--ck-tint": `var(--${TINTS[hueOf(id) % TINTS.length]})` } : {}),
    ...(size ? { "--ck-size": `${size}px` } : {}),
  } as CSSProperties;
}

export function CallAvatar({ p, size = 28, ring }: { p: CallPerson; size?: number; ring?: boolean }) {
  if (p.agent) return <VexaMark size={size} />;
  return (
    <span className="vx-ck-avatar" data-speaking={ring && p.speaking ? "" : undefined}
      style={ckVars(p.id, size)}>
      {p.image ? <img src={p.image} alt="" /> : <Identity kind="person" id={p.id} name={p.name} size={size} />}
    </span>
  );
}

export function VexaMark({ size = 28 }: { size?: number }) {
  return (
    <span className="vx-ck-avatar vx-ck-vexa" style={ckVars(undefined, size)}>
      <img src="/vexa-logo.svg" alt="" style={{ width: "62%", height: "62%", objectFit: "contain" }} />
    </span>
  );
}

/** Speaking bars — the one motion a call shows; frozen when nobody speaks. */
export function Bars({ on, small }: { on?: boolean; small?: boolean }) {
  return (
    <span className="vx-ck-bars" data-on={on ? "" : undefined} data-small={small ? "" : undefined} aria-hidden>
      <i /><i /><i />
    </span>
  );
}

const AGENT_WORD: Record<AgentState, string> = {
  listening: "listening", thinking: "thinking…", speaking: "speaking", paused: "paused",
};

/** Vexa in the call: always visible, always saying what it is doing. */
export function AgentChip({ state, compact }: { state: AgentState; compact?: boolean }) {
  return (
    <span className="vx-ck-agent" data-state={state} title={`Vexa is ${AGENT_WORD[state]}`}>
      <VexaMark size={compact ? 16 : 18} />
      {!compact && <span>Vexa · {AGENT_WORD[state]}</span>}
      {state === "speaking" ? <Bars on small /> : state === "listening" ? <span className="vx-ck-listen" aria-hidden /> : null}
    </span>
  );
}

/** The people in a call as one line of avatars; the speaker's ring lights. */
export function Roster({ people, max = 6, size = 22 }: { people: CallPerson[]; max?: number; size?: number }) {
  const humans = people.filter((p) => !p.agent);
  const shown = humans.slice(0, max);
  const more = humans.length - shown.length;
  return (
    <span className="vx-ck-roster" aria-label={`In the call: ${humans.map((p) => p.self ? `${p.name} (you)` : p.name).join(", ")}`}>
      {shown.map((p) => (
        <span key={p.id} title={`${p.name}${p.self ? " (you)" : ""}${p.muted ? " · muted" : ""}`} className="vx-ck-roster-item">
          <CallAvatar p={p} size={size} ring />
          {p.muted && <span className="vx-ck-roster-muted"><MicOff size={8} strokeWidth={2.5} /></span>}
        </span>
      ))}
      {more > 0 && <span className="vx-ck-avatar vx-ck-more" style={ckVars(undefined, size)}>+{more}</span>}
    </span>
  );
}

/** One participant's tile. Camera on → the feed; off → the avatar on the person's tint. */
export function CallTile({ p, kind = "grid", active, label, action }: {
  p: CallPerson; kind?: "stage" | "grid" | "film" | "self"; active?: boolean; label?: string;
  /** a control ON this tile (your own: background blur) — shown on hover, always on touch */
  action?: ReactNode;
}) {
  const avatar = kind === "film" || kind === "self" ? 34 : kind === "stage" ? 96 : 64;
  return (
    <figure className="vx-ck-tile" data-kind={kind} data-active={active ? "" : undefined}
      data-speaking={p.speaking ? "" : undefined} data-self={p.self ? "" : undefined}
      style={ckVars(p.id)}>
      {p.camera && p.feed ? <div className="vx-ck-feed" data-mirror={p.self ? "" : undefined}>{p.feed}</div>
        : <div className="vx-ck-tile-off"><CallAvatar p={p} size={avatar} /></div>}
      <figcaption className="vx-ck-tile-name">
        {p.muted ? <MicOff size={11} strokeWidth={2.4} aria-label="muted" /> : <Bars on={p.speaking} small />}
        <span><PersonName id={p.id} name={label ?? (p.self ? "You" : p.name)} /></span>
      </figcaption>
      {p.joining && <div className="vx-ck-tile-joining">Joining…</div>}
      {action && <div className="vx-ck-tile-act">{action}</div>}
    </figure>
  );
}
