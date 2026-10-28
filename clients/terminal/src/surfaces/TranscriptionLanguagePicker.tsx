"use client";
/** Transcription language UI — the picker plus its three homes (Settings default, the per-meeting
 *  override on the paste-a-link senders, the live meeting header). The mapping to and from the wire
 *  lives in `transcriptionLanguage.ts`; this file holds only presentation.
 *  All of this file's visual code is local so it swaps to the ui-kit Select/Chip primitives in one edit. */
import "./transcriptionLanguage.css";
import { useEffect, useId, useState, type ReactNode } from "react";
import { Icon } from "../ui-kit";
import { presentError } from "../data/apiClient";
import { getTranscriptionPrefs, setTranscriptionPrefs } from "./settingsApi";
import { listSharedMemberships } from "../data/workspaceApi";
import { refreshMeetings } from "./liveMeetings";
import { meetingPhase, type MeetingMock } from "./meetingModel";
import {
  DEFAULT_CHOICE, LANGUAGE_CODES, LanguageSwitchError, choiceProblem, choiceToSetting, isValidationRefusal,
  languageDefaultFields, languageLabel, platformSlug, setMeetingLanguage, settingToChoice, summarizeLanguage,
  type LanguageChoice, type LanguageMode, type MeetingLanguage,
} from "./transcriptionLanguage";

// ── local look: classes in ./transcriptionLanguage.css (tokens only) ─────────────────────────────
const SCOPE = "vx-lang";
const row = "vx-lang-row";
const labelText = "vx-lang-label";
const select = "vx-lang-select";
const chip = "vx-lang-chip";
const chipRemove = "vx-lang-chip-remove";
const button = "vx-lang-btn";
const primary = "vx-lang-btn vx-lang-btn-primary";
const hint = "vx-lang-hint";
const errorText = "vx-lang-error";
const okText = "vx-lang-ok";

function Scope({ children, className, label }: { children: ReactNode; className?: string; label?: string }) {
  return <div className={`${SCOPE} ${className ?? ""}`} role="group" aria-label={label}>{children}</div>;
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
    <div className="vx-lang-stack">
      <div className={row}>
        <label htmlFor={`${id}-mode`} className={labelText}>Language</label>
        <select id={`${id}-mode`} value={value.mode} disabled={disabled} className={select}
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
          <label htmlFor={`${id}-one`} className={labelText}>Transcribe in</label>
          <select id={`${id}-one`} value={value.language} disabled={disabled} className={select}
            onChange={(e) => set({ language: e.target.value })}>
            <option value="">Choose…</option>
            {LANGUAGE_CODES.map((c) => <option key={c} value={c}>{languageLabel(c)}</option>)}
          </select>
        </>}
      </div>
      {value.mode === "few" && <>
        {value.languages.length > 0 && (
          <ul aria-label="Chosen languages" className={`${row} vx-lang-list`}>
            {value.languages.map((c) => (
              <li key={c} className={chip}>
                {languageLabel(c)}
                <button type="button" aria-label={`Remove ${languageLabel(c)}`} disabled={disabled} className={chipRemove}
                  onClick={() => set({ languages: value.languages.filter((x) => x !== c), fallback: value.fallback === c ? "" : value.fallback })}><Icon name="x" size={12} /></button>
              </li>
            ))}
          </ul>
        )}
        <div className={row}>
          <label htmlFor={`${id}-add`} className={labelText}>Add a language</label>
          <select id={`${id}-add`} value="" disabled={disabled || !remaining.length} className={select}
            onChange={(e) => { if (e.target.value) set({ languages: [...value.languages, e.target.value] }); }}>
            <option value="">Choose…</option>
            {remaining.map((c) => <option key={c} value={c}>{languageLabel(c)}</option>)}
          </select>
        </div>
        {value.languages.length >= 2 && (
          <div className={row}>
            <label htmlFor={`${id}-fallback`} className={labelText}>When none of these is detected, use</label>
            <select id={`${id}-fallback`} value={value.fallback} disabled={disabled} className={select}
              onChange={(e) => set({ fallback: e.target.value })}>
              <option value="">The first one ({languageLabel(value.languages[0])})</option>
              {value.languages.map((c) => <option key={c} value={c}>{languageLabel(c)}</option>)}
            </select>
          </div>
        )}
      </>}
      {problem && <div id={`${id}-problem`} aria-live="polite" className={hint}>{problem}</div>}
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
    <Scope label="Default transcription language" className="vx-lang-default">
      <div className={hint}>
        Meeting bots transcribe in this language unless a meeting says otherwise. Leave it on
        Deployment default to use whatever this deployment is set to.
      </div>
      <LanguagePicker value={choice} disabled={busy || !loaded}
        onChange={(c) => { setSaved(false); setChoice(c); }}
        modes={["default", "one", "few"]} defaultLabel="Deployment default" />
      <div className={row}>
        <button type="button" disabled={busy || !dirty || !!problem || !loaded} aria-busy={busy || undefined}
          className={dirty && !problem ? primary : button} onClick={() => void save()}>
          {busy ? "Saving…" : "Save language"}
        </button>
        {saved && <span role="status" className={okText}>Saved — applies to the next meeting bot</span>}
      </div>
      {err && <div role="alert" className={errorText}>{err}</div>}
    </Scope>
  );
}

/** Owner-or-editor: the caller owns the meeting, or holds owner/contributor on its bound workspace.
 *  Resolves to false while unknown, so a reader never sees a control flash. */
export function useCanChangeLanguage(m: MeetingMock): boolean {
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
export function MeetingLanguageControl({ meeting: m, compact = false, editing: editingProp, onDone }: {
  meeting: MeetingMock;
  /** THE MEETING VIEW'S FORM (founder, UI lab 2026-10-14): no "Language" row in the tools — the
   *  language is a small label once known, and changing it is an item in "…" that opens the picker
   *  (`editing`, `onDone`). */
  compact?: boolean; editing?: boolean; onDone?: () => void;
}) {
  const live = meetingPhase(m) === "live";
  const canChange = useCanChangeLanguage(m);
  const rowValue = m.transcription_language;
  const rowKey = JSON.stringify(rowValue ?? null);
  // What a switch answered, shown until the meetings list carries the new row.
  const [applied, setApplied] = useState<{ forRow: string; value: MeetingLanguage } | null>(null);
  const shown = applied && applied.forRow === rowKey ? applied.value : rowValue;
  const [editingOwn, setEditingOwn] = useState(false);
  const editing = compact ? !!editingProp : editingOwn;
  const setEditing = (v: boolean) => { if (compact) { if (!v) onDone?.(); } else setEditingOwn(v); };
  const [choice, setChoice] = useState<LanguageChoice>(DEFAULT_CHOICE);
  // opened from "…": start the picker on the setting the bot runs with
  useEffect(() => { if (compact && editingProp) setChoice(settingToChoice(shown, "auto")); }, [compact, editingProp]); // eslint-disable-line react-hooks/exhaustive-deps
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  if (!live) return null;

  const summary = shown ? summarizeLanguage(shown) : "Not reported yet";
  const editable = canChange && !!m.native_id;
  if (compact && !editing) {
    return shown ? <span data-meeting-language className="vx-lang-label" title="Transcription language">{summary}</span> : null;
  }
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
    <Scope label="Transcription language" className="vx-lang-meeting">
      <div className={row}>
        <span className={labelText}>Language</span>
        <span data-meeting-language className="vx-lang-value">{summary}</span>
        {editable && !editing && (
          <button type="button" className={button} onClick={() => { setChoice(settingToChoice(shown, "auto")); setErr(null); setEditing(true); }}>
            Change language
          </button>
        )}
      </div>
      {editable && editing && <>
        <LanguagePicker value={choice} onChange={setChoice} disabled={busy} modes={["auto", "one", "few"]} />
        <div className={row}>
          <button type="button" className={problem ? button : primary} disabled={busy || !!problem} aria-busy={busy || undefined} onClick={() => void apply()}>
            {busy ? "Switching…" : "Switch language"}
          </button>
          <button type="button" className={button} disabled={busy} onClick={() => { setEditing(false); setErr(null); }}>Cancel</button>
        </div>
      </>}
      {err && <div role="alert" className={errorText}>{err}</div>}
    </Scope>
  );
}
