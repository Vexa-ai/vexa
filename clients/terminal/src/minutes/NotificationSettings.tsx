"use client";
/** SETTINGS → NOTIFICATIONS (0.13.4 S6, mock 87h). How and when Vexa may interrupt this person:
 *  channels (email now; WhatsApp is 0.13.5), when (idle delay, digest, quiet hours, a daily
 *  attention budget), sounds (three, each with a preview, and a volume), and a level per room and
 *  workspace. Every change saves at once through `notificationPolicy` and comes back with one line if
 *  the server refuses it. Plain native selects (they are the phone's own pickers); rows wrap, so
 *  nothing scrolls sideways at 360px. */
import { useEffect, useRef, useState, type ReactNode } from "react";
import { Play } from "lucide-react";
import { Button } from "../ui-kit/primitives/Button";
import { toast } from "../ui-kit/primitives/Toast";
import { savePolicy, usePolicy, type Level, type Policy } from "./notificationPolicy";
import { audioSupported, onUnlocked, preview, setVolume, unlocked, type SoundKind } from "../platform/sounds";
import { enableSounds } from "./noticeSounds";
import { loadNavWorkspaces } from "./navigatorApi";
import { listRooms } from "../surfaces/roomsApi";
import "./notifySettings.css";

/** A row in "Per room and workspace": `key` is what the policy stores (`ws:<slug>` / `room:<id>`). */
export type Place = { key: string; label: string; kind: "workspace" | "room" };

/** The person's workspaces and the rooms they are in. Best-effort: a list that fails is empty. */
export async function defaultPlaces(): Promise<Place[]> {
  const wss = (await loadNavWorkspaces().catch(() => [])).filter((w) => w.slug && w.kind === "group");
  const out: Place[] = [];
  for (const w of wss) {
    out.push({ key: `ws:${w.slug}`, label: w.name, kind: "workspace" });
    const rooms = await listRooms(w.slug as string).catch(() => []);
    for (const r of rooms) if (r.member) out.push({ key: `room:${r.id}`, label: `#${r.title || r.id}`, kind: "room" });
  }
  return out;
}

/** What a room with no setting of its own gets — the same default the delivery gate applies. */
export const DEFAULT_LEVEL: Level = "mentions";
const REFUSED = "That setting couldn't be saved just now";
const QUIET_PRESETS: Record<string, [string, string]> = { "22-08": ["22:00", "08:00"], "23-07": ["23:00", "07:00"] };
const LEVELS: { value: Level; label: string }[] = [
  { value: "all", label: "All messages" }, { value: "mentions", label: "Mentions and cards" }, { value: "none", label: "Nothing" },
];
const SOUNDS: [SoundKind, string][] = [
  ["chime", "A card or a mention for you"],
  ["tick", "An in-app toast"],
  ["ring", "A call ringing you (until Join / Not now, 30 s)"],
];

function Toggle({ on, label, onChange, disabled }: { on: boolean; label: string; onChange?: (v: boolean) => void; disabled?: boolean }) {
  return (
    <button type="button" role="switch" aria-checked={on} aria-label={label} className="vx-nt-switch" disabled={disabled}
      onClick={() => onChange?.(!on)}>
      <span className="vx-nt-track" aria-hidden><span className="vx-nt-knob" /></span>
      <span aria-hidden>{on ? "On" : "Off"}</span>
    </button>
  );
}

function Row({ title, detail, children, disabled, id }: { title: string; detail?: string; children?: ReactNode; disabled?: boolean; id?: string }) {
  return (
    <div className="vx-nt-row" aria-disabled={disabled || undefined} data-nt-row={id}>
      <div className="vx-nt-text"><span className="vx-nt-title">{title}</span>{detail && <span className="vx-nt-detail">{detail}</span>}</div>
      <div className="vx-nt-ctl">{children}</div>
    </div>
  );
}

export function NotificationSettings({ email, loadPlaces = defaultPlaces }: { email?: string; loadPlaces?: () => Promise<Place[]> }) {
  const { phase, policy: p, timezone, limits } = usePolicy();
  const [address, setAddress] = useState(email ?? "");
  const [places, setPlaces] = useState<Place[] | null>(null);
  const [soundOk, setSoundOk] = useState(unlocked());
  const [vol, setVol] = useState(p.sounds.volume);
  const volTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => { setVol(p.sounds.volume); }, [p.sounds.volume]);
  useEffect(() => onUnlocked(() => setSoundOk(true)), []);
  useEffect(() => {
    let on = true;
    void loadPlaces().then((x) => { if (on) setPlaces(x); }, () => { if (on) setPlaces([]); });
    return () => { on = false; };
  }, [loadPlaces]);
  useEffect(() => {
    if (email) return;
    let on = true;
    fetch("/api/auth/me", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((d: { email?: string } | null) => { if (on && d?.email) setAddress(d.email); })
      .catch(() => undefined);
    return () => { on = false; };
  }, [email]);
  useEffect(() => () => { if (volTimer.current) clearTimeout(volTimer.current); }, []);

  if (phase === "idle" || phase === "loading") return <div className="vx-settings-note" data-notify-settings="loading">Loading…</div>;
  if (phase === "off") return <div className="vx-settings-note" data-notify-settings="off">Notification settings could not be read just now.</div>;

  const save = (patch: Partial<Policy>) => { void savePolicy(patch, () => toast(REFUSED)); };
  const zone = timezone ? timezone.split("/").pop()?.replace(/_/g, " ") : "";
  const idleOpts = [5, 10, 15, 30].filter((n) => n >= limits.idle_floor_min);
  if (!idleOpts.includes(p.idle_min)) idleOpts.push(p.idle_min);
  const budgetOpts = [4, 6, 8, 12].filter((n) => n <= limits.budget_ceiling);
  if (!budgetOpts.includes(p.budget)) budgetOpts.push(p.budget);
  budgetOpts.sort((a, b) => a - b);
  const quietKey = !p.quiet.on ? "off"
    : Object.entries(QUIET_PRESETS).find(([, [f, t]]) => f === p.quiet.from && t === p.quiet.to)?.[0] ?? "custom";
  const setQuiet = (k: string) => {
    if (k === "off") save({ quiet: { ...p.quiet, on: false } });
    else if (k === "custom") save({ quiet: { ...p.quiet, on: true } });
    else save({ quiet: { on: true, from: QUIET_PRESETS[k][0], to: QUIET_PRESETS[k][1] } });
  };
  const sound = (k: SoundKind | "on", v: boolean) => save({ sounds: { ...p.sounds, [k]: v } });
  const play = (k: SoundKind) => { setVolume(vol); preview(k); setSoundOk(unlocked()); };
  const onVolume = (v: number) => {
    setVol(v); setVolume(v);
    if (volTimer.current) clearTimeout(volTimer.current);
    volTimer.current = setTimeout(() => save({ sounds: { ...p.sounds, volume: v } }), 400);
  };
  const level = (key: string, v: Level) => save({ rooms: { ...p.rooms, [key]: v } });

  return (
    <div className="vx-nt" data-notify-settings="">
      <h3 className="vx-nt-sec">Channels</h3>
      <Row id="email" title="Email" detail={address || "Your sign-in address"}>
        <Toggle on={p.channels.email} label="Email" onChange={(v) => save({ channels: { ...p.channels, email: v } })} />
      </Row>
      <Row id="whatsapp" title="WhatsApp" detail="Coming soon" disabled>
        <Toggle on={false} label="WhatsApp" disabled />
      </Row>

      <h3 className="vx-nt-sec">When</h3>
      <Row id="idle" title="Nudge me outside Vexa">
        <select aria-label="Nudge me outside Vexa" value={String(p.idle_min)} onChange={(e) => save({ idle_min: Number(e.target.value) })}>
          {idleOpts.map((n) => <option key={n} value={n}>after {n} min unseen</option>)}
        </select>
      </Row>
      <Row id="digest" title="Digest">
        <select aria-label="Digest" value={p.digest} onChange={(e) => save({ digest: e.target.value as Policy["digest"] })}>
          <option value="hourly">Hourly, only if unseen</option>
          <option value="daily">Daily at {p.digest_at}</option>
          <option value="off">Off</option>
        </select>
      </Row>
      <Row id="quiet" title="Quiet hours" detail={zone ? `Your time zone: ${zone}` : undefined}>
        <select aria-label="Quiet hours" value={quietKey} onChange={(e) => setQuiet(e.target.value)}>
          <option value="off">Off</option>
          <option value="22-08">22:00–08:00</option>
          <option value="23-07">23:00–07:00</option>
          <option value="custom">{quietKey === "custom" ? `${p.quiet.from}–${p.quiet.to}` : "Custom…"}</option>
        </select>
        {quietKey === "custom" && <>
          <input type="time" aria-label="Quiet from" value={p.quiet.from} onChange={(e) => save({ quiet: { ...p.quiet, from: e.target.value } })} />
          <input type="time" aria-label="Quiet until" value={p.quiet.to} onChange={(e) => save({ quiet: { ...p.quiet, to: e.target.value } })} />
        </>}
      </Row>
      <Row id="budget" title="Attention budget">
        <select aria-label="Attention budget" value={String(p.budget)} onChange={(e) => save({ budget: Number(e.target.value) })}>
          {budgetOpts.map((n) => <option key={n} value={n}>{n} interruptions a day</option>)}
        </select>
      </Row>

      <h3 className="vx-nt-sec">Sounds</h3>
      {audioSupported() && !soundOk && (
        <Row id="enable" title="Your browser keeps sounds off until you allow them">
          <Button variant="secondary" onClick={() => { enableSounds(); setSoundOk(unlocked()); }}>Enable sounds</Button>
        </Row>
      )}
      <Row id="sounds" title="Play sounds">
        <Toggle on={p.sounds.on} label="Play sounds" onChange={(v) => sound("on", v)} />
      </Row>
      {SOUNDS.map(([k, label]) => (
        <Row key={k} id={`sound-${k}`} title={label} disabled={!p.sounds.on}>
          <Toggle on={p.sounds[k]} label={label} disabled={!p.sounds.on} onChange={(v) => sound(k, v)} />
          <Button variant="ghost" icon={<Play size={12} aria-hidden />} onClick={() => play(k)} data-play={k}>Play</Button>
        </Row>
      ))}
      <Row id="volume" title="Volume">
        <input type="range" aria-label="Volume" min={0} max={1} step={0.05} value={vol} onChange={(e) => onVolume(Number(e.target.value))} />
      </Row>

      <h3 className="vx-nt-sec">Per room and workspace</h3>
      {places === null ? <div className="vx-settings-note">Loading…</div>
        : places.length === 0 ? <div className="vx-settings-note">No rooms or shared workspaces yet.</div>
        : places.map((pl) => (
          <Row key={pl.key} id={pl.key} title={pl.label}>
            <select aria-label={`Notify for ${pl.label}`} value={p.rooms[pl.key] ?? DEFAULT_LEVEL} onChange={(e) => level(pl.key, e.target.value as Level)}>
              {LEVELS.map((l) => <option key={l.value} value={l.value}>{l.label}</option>)}
            </select>
          </Row>
        ))}

      <p className="vx-nt-note">Never in a notification: anyone&apos;s personal data from a chat. A nudge outside Vexa carries the title and who; the rest loads after sign-in.</p>
    </div>
  );
}
