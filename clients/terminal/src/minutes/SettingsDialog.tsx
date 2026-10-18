"use client";
/** SETTINGS → YOUR PHOTO (UI 15g, `ProfilePhoto`) and APPEARANCE (founder 2026-10-15: "this should be normal settings to pick from, and a
 *  normal font pick, in the left bottom"). A row of accent swatches with a Custom… colour, and plain
 *  dropdowns for the interface font, the reading font, text size and density. Every change applies
 *  at once and is saved for the person on the server; "Use company default" clears their choice. */
import { useEffect, useState, type CSSProperties } from "react";
import { Modal } from "../ui-kit/Modal";
import { ProfilePhoto } from "./ProfilePhoto";
import {
  ACCENTS, FONTS_READING, FONTS_UI, accentSeeds, applyAppearance, deriveAccent, normalizeAppearance,
  readAppearance, saveAppearance, type Appearance, type AppearanceRead,
} from "../platform/appearance";
import { PublicProfileSettings } from "../surfaces/profile/PublicProfile";
import { useFeature } from "../platform/features";
import { Tabs } from "../ui-kit/primitives/Tabs";
import { NotificationSettings } from "./NotificationSettings";
import "./notifySettings.css";

/** Two tabs (0.13.4 S6): General (photo, appearance, public profile) · Notifications. The strip
 *  scrolls sideways on its own on a phone; the page never does. */
export function SettingsDialog({ onClose, initialTab = "general", loadPlaces }: {
  onClose: () => void; initialTab?: "general" | "notifications";
  /** where the per-room list comes from (fixtures and the browser check pass their own) */
  loadPlaces?: Parameters<typeof NotificationSettings>[0]["loadPlaces"];
}) {
  const [tab, setTab] = useState<string>(initialTab);
  return (
    <Modal title="Settings" onClose={onClose} width={tab === "notifications" ? 560 : 460}>
      <div className="vx-settings-tabs">
        <Tabs label="Settings sections" value={tab} onChange={setTab}
          items={[{ key: "general", label: "General" }, { key: "notifications", label: "Notifications" }]} />
      </div>
      {tab === "notifications"
        ? <div className="vx-settings-scroll"><NotificationSettings {...(loadPlaces ? { loadPlaces } : {})} /></div>
        : <GeneralSettings />}
    </Modal>
  );
}

function GeneralSettings() {
  const profilesOn = useFeature("public_profiles");
  const [read, setRead] = useState<AppearanceRead | null>(null);
  const [a, setA] = useState<Appearance | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => { void readAppearance().then((r) => { if (r) { setRead(r); setA(r.effective); } else setErr("Settings could not be read just now."); }); }, []);
  const theme = typeof document !== "undefined" && document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark";
  const set = (change: Partial<Appearance>) => {
    if (!a) return;
    const next = normalizeAppearance({ ...a, ...change }, a);
    setA(next);
    applyAppearance(next);                              // instant
    void saveAppearance(change).then((r) => { if (r) setRead(r); else setErr("Not saved — it applies on this device until the server answers."); });
  };
  const custom = a && !ACCENTS.some((x) => x.id === a.accent);
  const adjusted = a ? deriveAccent(accentSeeds(a.accent)[theme], theme).adjusted : false;
  const fromCompany = read && Object.keys(read.user ?? {}).length === 0;
  return (
    <>
      <div className="vx-settings" data-settings="">
        <ProfilePhoto />
        <div className="vx-settings-h">Appearance</div>
        {!a ? <div className="vx-settings-note">{err || "Loading…"}</div> : <>
          <div className="vx-settings-row">
            <span className="vx-settings-label">Accent colour</span>
            <div className="vx-swatches" role="radiogroup" aria-label="Accent colour">
              {ACCENTS.map((x) => (
                <button key={x.id} type="button" role="radio" aria-checked={a.accent === x.id} aria-label={x.name} title={x.name}
                  className="vx-swatch" data-accent={x.id} style={{ "--swatch": x[theme] } as CSSProperties} onClick={() => set({ accent: x.id })} />
              ))}
              <label className="vx-swatch vx-swatch-custom" title="Custom…" data-accent-custom="" aria-checked={!!custom} role="radio"
                style={custom ? ({ "--swatch": a.accent } as CSSProperties) : undefined}>
                <input type="color" aria-label="Custom accent colour" value={custom ? a.accent : accentSeeds(a.accent)[theme]}
                  onChange={(e) => set({ accent: e.target.value })} />
                {!custom && <span aria-hidden>+</span>}
              </label>
            </div>
          </div>
          {adjusted && <div className="vx-settings-note" data-contrast-adjusted="">Adjusted slightly so text on it stays readable.</div>}
          <label className="vx-settings-row">
            <span className="vx-settings-label">Interface font</span>
            <select value={a.font_ui} onChange={(e) => set({ font_ui: e.target.value as Appearance["font_ui"] })}>
              {Object.entries(FONTS_UI).map(([k, f]) => <option key={k} value={k}>{f.name}</option>)}
            </select>
          </label>
          <label className="vx-settings-row">
            <span className="vx-settings-label">Reading font</span>
            <select value={a.font_reading} onChange={(e) => set({ font_reading: e.target.value as Appearance["font_reading"] })}>
              {Object.entries(FONTS_READING).map(([k, f]) => <option key={k} value={k}>{f.name}</option>)}
            </select>
          </label>
          <label className="vx-settings-row">
            <span className="vx-settings-label">Text size</span>
            <select value={a.text_size} onChange={(e) => set({ text_size: e.target.value as Appearance["text_size"] })}>
              <option value="default">Default</option><option value="large">Large</option>
            </select>
          </label>
          <label className="vx-settings-row">
            <span className="vx-settings-label">Density</span>
            <select value={a.density} onChange={(e) => set({ density: e.target.value as Appearance["density"] })}>
              <option value="comfortable">Comfortable</option><option value="compact">Compact</option>
            </select>
          </label>
          <div className="vx-settings-foot">
            {fromCompany ? <span className="vx-settings-note">Using your company's default.</span>
              : <button type="button" className="vx-settings-link" data-settings-reset="" onClick={() => {
                  void saveAppearance({ accent: null, font_ui: null, font_reading: null, density: null, text_size: null })
                    .then((r) => { if (r) { setRead(r); setA(r.effective); } });
                }}>Use company default</button>}
            {err && <span className="vx-settings-note" role="status">{err}</span>}
          </div>
        </>}
        {profilesOn && <>
          <div className="vx-settings-h">Public profile</div>
          <PublicProfileSettings />
        </>}
      </div>
    </>
  );
}
