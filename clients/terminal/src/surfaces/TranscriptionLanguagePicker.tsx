"use client";
/** Transcription language UI — the picker plus its three homes (Settings default, the per-meeting
 *  override on the paste-a-link senders, the live meeting header). The mapping to and from the wire
 *  lives in `transcriptionLanguage.ts`; this file holds only presentation.
 *  All of this file's visual code is local so it swaps to the ui-kit Select/Chip primitives in one edit. */
import { useEffect, useId, useState, type CSSProperties, type ReactNode } from "react";
import { Icon } from "../ui-kit";
import { presentError } from "./apiClient";
import { getTranscriptionPrefs, setTranscriptionPrefs } from "./settingsApi";
import { listSharedMemberships } from "./workspaceApi";
import { refreshMeetings } from "./liveMeetings";
import { meetingPhase, type MeetingMock } from "./meetingModel";
import {
  DEFAULT_CHOICE, LANGUAGE_CODES, LanguageSwitchError, choiceProblem, choiceToSetting, isValidationRefusal,
  languageDefaultFields, languageLabel, platformSlug, setMeetingLanguage, settingToChoice, summarizeLanguage,
  type LanguageChoice, type LanguageMode, type MeetingLanguage,
} from "./transcriptionLanguage";

// ── local look (tokens only) ─────────────────────────────────────────────────────────────────
const SCOPE = "vx-lang";
const SCOPED_CSS = `.${SCOPE} :is(button, select):focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.${SCOPE} :is(button, select) { min-height: 24px; }
.${SCOPE} button:disabled, .${SCOPE} select:disabled { cursor: default; opacity: .55; }`;
const row: CSSProperties = { display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" };
const labelText: CSSProperties = { fontSize: 11.5, color: "var(--t3)" };
const select: CSSProperties = { fontSize: 12, padding: "3px 6px", borderRadius: 6, border: "1px solid var(--line2)", background: "var(--panel)", color: "var(--t1)", cursor: "pointer", maxWidth: "100%" };
const chip: CSSProperties = { display: "inline-flex", alignItems: "center", gap: 2, height: 24, padding: "0 2px 0 8px", borderRadius: 6, border: "1px solid var(--line2)", background: "var(--panel2)", color: "var(--t1)", fontSize: 12 };
const chipRemove: CSSProperties = { display: "inline-flex", alignItems: "center", justifyContent: "center", width: 24, height: 22, border: "none", borderRadius: 4, background: "transparent", color: "var(--t2)", cursor: "pointer", fontSize: 13, lineHeight: 1 };
const button: CSSProperties = { fontSize: 12, padding: "3px 10px", borderRadius: 6, border: "1px solid var(--line2)", background: "var(--panel2)", color: "var(--t1)", cursor: "pointer" };
const primary: CSSProperties = { ...button, background: "var(--accent)", color: "var(--on-accent)", border: "1px solid var(--accent)", fontWeight: 500 };
const hint: CSSProperties = { fontSize: 11, color: "var(--t3)", lineHeight: 1.45 };
const errorText: CSSProperties = { fontSize: 11.5, color: "var(--danger)", lineHeight: 1.45 };
const okText: CSSProperties = { fontSize: 11.5, color: "var(--green)" };

function Scope({ children, style, label }: { children: ReactNode; style?: CSSProperties; label?: string }) {
  return <div className={SCOPE} role="group" aria-label={label} style={style}><style>{SCOPED_CSS}</style>{children}</div>;
}

const MODE_LABEL: Record<Exclude<LanguageMode, "default">, string> = { auto: "Auto-detect", one: "One language", few: "A few languages" };

/** Edit a `LanguageChoice`. `modes` lists what this home offers, in order; `defaultLabel` names the
 *  "defer to the level below" mode where it is offered. */
export function LanguagePicker({ value, onChange, modes, defaultLabel = "Default", disabled }: {
  value: LanguageChoice;
  onChange: (next: LanguageChoice) => void;
  modes: LanguageMode[];
  defaultLabel?: string;
  disabled?: boolean;
}) {
  const id = useId();
  const problem = choiceProblem(value);
  const set = (patch: Partial<LanguageChoice>) => onChange({ ...value, ...patch });
  const remaining = LANGUAGE_CODES.filter((c) => !value.languages.includes(c));
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <div style={row}>
        <label htmlFor={`${id}-mode`} style={labelText}>Language</label>
        <select id={`${id}-mode`} value={value.mode} disabled={disabled} style={select}
          aria-describedby={problem ? `${id}-problem` : undefined}
          onChange={(e) => {
            const mode = e.target.value as LanguageMode;
            // Carry the pick across: one language seeds the list, the list's first seeds one.
            if (mode === "few" && !value.languages.length && value.language) set({ mode, languages: [value.language] });
            else if (mode === "one" && !value.language && value.languages.length) set({ mode, language: value.languages[0] });
            else set({ mode });
          }}>
          {modes.map((m) => <option key={m} value={m}>{m === "default" ? defaultLabel : MODE_LABEL[m]}</option>)}
        </select>
        {value.mode === "one" && <>
          <label htmlFor={`${id}-one`} style={labelText}>Transcribe in</label>
          <select id={`${id}-one`} value={value.language} disabled={disabled} style={select}
            onChange={(e) => set({ language: e.target.value })}>
            <option value="">Choose…</option>
            {LANGUAGE_CODES.map((c) => <option key={c} value={c}>{languageLabel(c)}</option>)}
          </select>
        </>}
      </div>
      {value.mode === "few" && <>
        {value.languages.length > 0 && (
          <ul aria-label="Chosen languages" style={{ ...row, listStyle: "none", margin: 0, padding: 0 }}>
            {value.languages.map((c) => (
              <li key={c} style={chip}>
                {languageLabel(c)}
                <button type="button" aria-label={`Remove ${languageLabel(c)}`} disabled={disabled} style={chipRemove}
                  onClick={() => set({ languages: value.languages.filter((x) => x !== c), fallback: value.fallback === c ? "" : value.fallback })}><Icon name="x" size={12} /></button>
              </li>
            ))}
          </ul>
        )}
        <div style={row}>
          <label htmlFor={`${id}-add`} style={labelText}>Add a language</label>
          <select id={`${id}-add`} value="" disabled={disabled || !remaining.length} style={select}
            onChange={(e) => { if (e.target.value) set({ languages: [...value.languages, e.target.value] }); }}>
            <option value="">Choose…</option>
            {remaining.map((c) => <option key={c} value={c}>{languageLabel(c)}</option>)}
          </select>
        </div>
        {value.languages.length >= 2 && (
          <div style={row}>
            <label htmlFor={`${id}-fallback`} style={labelText}>When none of these is detected, use</label>
            <select id={`${id}-fallback`} value={value.fallback} disabled={disabled} style={select}
              onChange={(e) => set({ fallback: e.target.value })}>
              <option value="">The first one ({languageLabel(value.languages[0])})</option>
              {value.languages.map((c) => <option key={c} value={c}>{languageLabel(c)}</option>)}
            </select>
          </div>
        )}
      </>}
      {problem && <div id={`${id}-problem`} aria-live="polite" style={hint}>{problem}</div>}
    </div>
  );
}

/** The compact per-meeting override on a paste-a-link sender. "Default" sends nothing. */
export function SpawnLanguagePicker({ value, onChange, disabled }: { value: LanguageChoice; onChange: (c: LanguageChoice) => void; disabled?: boolean }) {
  return (
    <Scope label="Transcription language for this meeting">
      <LanguagePicker value={value} onChange={onChange} disabled={disabled} modes={["default", "auto", "one", "few"]} defaultLabel="Default" />
    </Scope>
  );
}

/** Settings → the person's default transcription language. */
export function TranscriptionLanguageSettings() {
  const [choice, setChoice] = useState<LanguageChoice>(DEFAULT_CHOICE);
  const [initial, setInitial] = useState<string>(JSON.stringify(languageDefaultFields(DEFAULT_CHOICE)));
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const adopt = (p: { language?: string | null; allowed_languages?: string[] }) => {
    const c = settingToChoice({ language: p.language ?? null, allowed_languages: p.allowed_languages ?? [] }, "default");
    setChoice(c); setInitial(JSON.stringify(languageDefaultFields(c)));
  };
  useEffect(() => {
    let on = true;
    getTranscriptionPrefs()
      .then((p) => { if (on) { adopt(p); setLoaded(true); } })
      .catch((e: unknown) => on && setErr(presentError(e).headline));
    return () => { on = false; };
  }, []);

  const fields = languageDefaultFields(choice);
  const dirty = JSON.stringify(fields) !== initial;
  const problem = choiceProblem(choice);
  const save = async () => {
    setBusy(true); setErr(null); setSaved(false);
    try { adopt(await setTranscriptionPrefs(fields)); setSaved(true); }
    catch (e: unknown) { setErr(isValidationRefusal(e) ? e.detail : presentError(e).headline); }
    finally { setBusy(false); }
  };

  return (
    <Scope label="Default transcription language" style={{ display: "flex", flexDirection: "column", gap: 8, maxWidth: 460 }}>
      <div style={hint}>
        Meeting bots transcribe in this language unless a meeting says otherwise. Leave it on
        Deployment default to use whatever this deployment is set to.
      </div>
      <LanguagePicker value={choice} disabled={busy || !loaded}
        onChange={(c) => { setSaved(false); setChoice(c); }}
        modes={["default", "one", "few"]} defaultLabel="Deployment default" />
      <div style={row}>
        <button type="button" disabled={busy || !dirty || !!problem || !loaded} aria-busy={busy || undefined}
          style={dirty && !problem ? primary : button} onClick={() => void save()}>
          {busy ? "Saving…" : "Save language"}
        </button>
        {saved && <span role="status" style={okText}>Saved — applies to the next meeting bot</span>}
      </div>
      {err && <div role="alert" style={errorText}>{err}</div>}
    </Scope>
  );
}

/** Owner-or-editor: the caller owns the meeting, or holds owner/contributor on its bound workspace.
 *  Resolves to false while unknown, so a reader never sees a control flash. */
function useCanChangeLanguage(m: MeetingMock): boolean {
  const [role, setRole] = useState<string | null>(null);
  useEffect(() => {
    if (!m.shared || !m.workspace_id) return;
    let on = true;
    listSharedMemberships()
      .then((ms) => { if (on) setRole(ms.find((x) => x.workspace_id === m.workspace_id)?.role ?? "none"); })
      .catch(() => { if (on) setRole("none"); });
    return () => { on = false; };
  }, [m.shared, m.workspace_id]);
  if (!m.shared) return true;
  return role === "owner" || role === "contributor";
}

/** The live meeting header's language: the setting the bot is running with, and — for the owner or an
 *  editor — the control that switches it mid-meeting. Renders nothing outside the live phase. */
export function MeetingLanguageControl({ meeting: m }: { meeting: MeetingMock }) {
  const live = meetingPhase(m) === "live";
  const canChange = useCanChangeLanguage(m);
  const rowValue = m.transcription_language;
  const rowKey = JSON.stringify(rowValue ?? null);
  // What a switch answered, shown until the meetings list carries the new row.
  const [applied, setApplied] = useState<{ forRow: string; value: MeetingLanguage } | null>(null);
  const shown = applied && applied.forRow === rowKey ? applied.value : rowValue;
  const [editing, setEditing] = useState(false);
  const [choice, setChoice] = useState<LanguageChoice>(DEFAULT_CHOICE);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  if (!live) return null;

  const summary = shown ? summarizeLanguage(shown) : "Not reported yet";
  const editable = canChange && !!m.native_id;
  const problem = choiceProblem(choice);
  const apply = async () => {
    if (!m.native_id || problem) return;
    setBusy(true); setErr(null);
    try {
      const next = await setMeetingLanguage(platformSlug(m.platform), m.native_id, choiceToSetting(choice));
      setApplied({ forRow: rowKey, value: next });
      setEditing(false);
      refreshMeetings();
    } catch (e: unknown) {
      setErr(e instanceof LanguageSwitchError ? e.message : presentError(e).headline);
    } finally { setBusy(false); }
  };

  return (
    <Scope label="Transcription language" style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 6 }}>
      <div style={row}>
        <span style={labelText}>Language</span>
        <span data-meeting-language style={{ fontSize: 12, color: "var(--t1)" }}>{summary}</span>
        {editable && !editing && (
          <button type="button" style={button} onClick={() => { setChoice(settingToChoice(shown, "auto")); setErr(null); setEditing(true); }}>
            Change language
          </button>
        )}
      </div>
      {editable && editing && <>
        <LanguagePicker value={choice} onChange={setChoice} disabled={busy} modes={["auto", "one", "few"]} />
        <div style={row}>
          <button type="button" style={problem ? button : primary} disabled={busy || !!problem} aria-busy={busy || undefined} onClick={() => void apply()}>
            {busy ? "Switching…" : "Switch language"}
          </button>
          <button type="button" style={button} disabled={busy} onClick={() => { setEditing(false); setErr(null); }}>Cancel</button>
        </div>
      </>}
      {err && <div role="alert" style={errorText}>{err}</div>}
    </Scope>
  );
}
