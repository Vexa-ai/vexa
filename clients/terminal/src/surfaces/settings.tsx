"use client";
/** Settings — the footer-gear CENTER tab (design-spec meeting-lifecycle-v2, W5): account-level
 *  configuration in one place — Calendar integration, API tokens, GitHub token, Account, and for
 *  the admin, who may sign in to the instance (Sign-in). The old
 *  "API Tokens" activity-bar item retired into here (its panels are imported, not duplicated);
 *  the Meetings sidebar keeps its own first-connect calendar card at the point of need — this is
 *  the durable home (multi-calendar management lives in `calendarConnections.tsx`). Sections are a left nav (no sub-routing; one tab, local state). */
import { useEffect, useState, type CSSProperties, type ReactNode } from "react";
import { registerTab } from "../contributions";
import { minutesOnly } from "../app/mode";
import { Icon } from "../ui-kit";
import { GitHubTokenCard, TokensPanel } from "./tokens";
import { ApiError, presentError } from "./apiClient";
import { CalendarConnectionsPanel } from "./calendarConnections";
import { getModelCatalog, type ModelEntry } from "./modelsApi";
import { allowLines, getModelPrefs, setModelPrefs, getTranscriptionPrefs, setTranscriptionPrefs, getGlobalSetting, setGlobalSetting, getSigninAllow, setSigninAllow, testModels, testTranscription, type ConfigTestResult, type SigninAllow } from "./settingsApi";

type SectionId = "calendar" | "models" | "tokens" | "github" | "signin" | "account";
const SECTIONS: Array<{ id: SectionId; label: string; icon: string; adminOnly?: boolean }> = [
  { id: "calendar", label: "Calendar", icon: "cal" },
  { id: "models", label: "Models", icon: "spark" },
  { id: "tokens", label: "API tokens", icon: "key" },
  { id: "github", label: "GitHub", icon: "github" },
  { id: "signin", label: "Sign-in", icon: "shield", adminOnly: true },
  { id: "account", label: "Account", icon: "user" },
];

const field: CSSProperties = { width: "100%", boxSizing: "border-box", fontSize: 12, padding: "6px 9px", borderRadius: 6, border: "1px solid var(--line)", background: "var(--panel2)", color: "var(--t1)" };
const btn: CSSProperties = { fontSize: 12, padding: "5px 12px", borderRadius: 6, border: "1px solid var(--line)", background: "var(--panel2)", color: "var(--t1)", cursor: "pointer" };

/** One models/transcription config form — the SAME fields serve the per-user prefs and (for
 *  admins) the global platform defaults; only load/save differ. Secrets arrive MASKED
 *  (********abcd): an untouched masked value is never sent back, typing replaces it, emptying a
 *  previously-set field clears it (empty string = clear, the API's contract). */
function ConfigForm({ fields, load, save, note }: {
  fields: Array<{ key: string; label: string; placeholder?: string; secret?: boolean; options?: Array<{ value: string; label: string }>; showIf?: (v: Record<string, string>) => boolean }>;
  load: () => Promise<Record<string, string>>;
  save: (update: Record<string, string>) => Promise<Record<string, string>>;
  note?: string;
}) {
  const [values, setValues] = useState<Record<string, string>>({});
  const [initial, setInitial] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    let on = true;
    load().then((v) => { if (on) { setValues(v); setInitial(v); } })
      .catch((e: unknown) => on && setErr(presentError(e).headline));
    return () => { on = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const dirty = fields.some((f) => (values[f.key] ?? "") !== (initial[f.key] ?? ""));
  const doSave = async () => {
    setBusy(true); setErr(null); setSaved(false);
    // Send only what changed; an untouched masked secret stays server-side.
    const update: Record<string, string> = {};
    for (const f of fields) {
      if ((values[f.key] ?? "") !== (initial[f.key] ?? "")) update[f.key] = values[f.key] ?? "";
    }
    try { const v = await save(update); setValues(v); setInitial(v); setSaved(true); }
    catch (e: unknown) { setErr(presentError(e).headline); }
    finally { setBusy(false); }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8, maxWidth: 460 }}>
      {note && <div className="t-xs c-3 lh-snug">{note}</div>}
      {err && <div role="alert" className="t-xs c-danger">⚠ {err}</div>}
      {fields.map((f) => (f.showIf && !f.showIf(values)) ? null : (
        <label key={f.key} className="t-xs c-2" style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span className="c-3" style={{ width: 110, flex: "none" }}>{f.label}</span>
          {f.options ? (
            <select value={values[f.key] ?? ""}
              onChange={(e) => { setSaved(false); setValues((v) => ({ ...v, [f.key]: e.target.value })); }}
              style={{ ...field, width: "auto", flex: 1 }}>
              {f.options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          ) : (
            <input value={values[f.key] ?? ""} placeholder={f.placeholder}
              type={f.secret && (values[f.key] ?? "") !== (initial[f.key] ?? "") ? "password" : "text"}
              onChange={(e) => { setSaved(false); setValues((v) => ({ ...v, [f.key]: e.target.value })); }}
              className="vx-input" />
          )}
        </label>
      ))}
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <button disabled={busy || !dirty} onClick={() => void doSave()}
          style={{ ...btn, background: dirty ? "var(--accent)" : "var(--panel2)", color: dirty ? "var(--on-accent)" : "var(--t3)", border: dirty ? "none" : btn.border, opacity: busy ? 0.5 : 1 }}>
          {busy ? "Saving…" : "Save"}
        </button>
        {saved && <span className="t-xs c-success">Saved — next agent turn uses it</span>}
      </div>
    </div>
  );
}

/** On-demand credential test row (fail-loud surface): runs the EFFECTIVE config — the same
 *  user > global > env resolution a chat turn / bot spawn applies — against the real backend
 *  and prints the verdict inline, remedy included. What Save can't tell you, Test does. */
function TestRow({ label, run }: { label: string; run: () => Promise<ConfigTestResult> }) {
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<ConfigTestResult | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const doTest = async () => {
    setBusy(true); setErr(null); setRes(null);
    try { setRes(await run()); }
    catch (e: unknown) { setErr(presentError(e).headline); }
    finally { setBusy(false); }
  };
  const provenance = res ? [res.mode, res.source && `via ${res.source}`].filter(Boolean).join(" · ") : "";
  return (
    <div className="mt-1_5" style={{ display: "flex", flexDirection: "column", gap: 4, maxWidth: 460 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <button disabled={busy} onClick={() => void doTest()}
          style={{ ...btn, opacity: busy ? 0.5 : 1 }}>
          {busy ? "Testing…" : label}
        </button>
        {res && (
          <span className="t-xs" style={{ color: res.ok ? "var(--green)" : "var(--danger)" }}>
            {res.ok ? "✓" : "✗"} {provenance && <span className="c-3">[{provenance}] </span>}
            {res.summary}
          </span>
        )}
        {err && <span role="alert" className="t-xs c-danger">⚠ test failed: {err}</span>}
      </div>
    </div>
  );
}

/** The "Default model" selector (ADR-0043): one option per model of the deployment's catalog that
 *  this person may pick — the list agent-api serves, never typed — plus "inherit", which defers to
 *  the level below (the organisation's default, then the catalog's). No catalog: no field. */
export function defaultModelField(models: ModelEntry[], inherit: string) {
  return {
    key: "default_model", label: "Default model",
    options: [{ value: "", label: inherit }, ...models.map((m) => ({ value: m.id, label: m.display_name }))],
  };
}

/** Models — which LLM the agent runs on and which STT backend the bot transcribes with; your own
 *  settings first, the deployment-wide defaults below for admins. Empty fields = the level below
 *  decides (global settings, then the deployment env). */
export function ModelsSection() {
  const [globalAdmin, setGlobalAdmin] = useState(false);
  const [catalog, setCatalog] = useState<ModelEntry[]>([]);
  useEffect(() => {
    let on = true;
    // Admin probe: the global card renders only when /api/admin/settings answers (404 = not admin).
    getGlobalSetting("models").then((v) => on && setGlobalAdmin(v !== null)).catch(() => undefined);
    // The deployment's model catalog, as agent-api serves it to THIS person (empty = no catalog).
    getModelCatalog().then((l) => on && setCatalog(Array.isArray(l?.models) ? l.models : []))
      .catch(() => undefined);
    return () => { on = false; };
  }, []);
  const catalogFields = catalog.length
    ? [defaultModelField(catalog, "Organisation default")] : [];
  const globalCatalogFields = catalog.length
    ? [defaultModelField(catalog, "Catalog default")] : [];

  const modelFields = [
    { key: "mode", label: "Provider", options: [
      { value: "", label: "Deployment default" },
      { value: "subscription", label: "Claude subscription (deployment credentials)" },
      { value: "custom", label: "Custom endpoint (open-source / gateway)" },
    ] },
    { key: "base_url", label: "Base URL", placeholder: "https://… (Anthropic/OpenAI-compatible gateway)", showIf: (v: Record<string, string>) => v.mode === "custom" },
    { key: "api_key", label: "API key", placeholder: "unchanged unless typed", secret: true, showIf: (v: Record<string, string>) => v.mode === "custom" },
    { key: "model", label: "Chat model", placeholder: "deployment default (e.g. sonnet)" },
    { key: "effort", label: "Reasoning effort", placeholder: "CLI default (e.g. medium)", options: [
      { value: "", label: "CLI default" },
      { value: "low", label: "low" },
      { value: "medium", label: "medium" },
      { value: "high", label: "high" },
      { value: "xhigh", label: "xhigh" },
    ] },
  ];
  const transcriptionFields = [
    { key: "url", label: "Service URL", placeholder: "deployment default" },
    { key: "token", label: "Token", placeholder: "unchanged unless typed", secret: true },
  ];
  const asStrings = (v: Record<string, unknown>): Record<string, string> => {
    const out: Record<string, string> = {};
    for (const [k, val] of Object.entries(v)) if (typeof val === "string" && val) out[k] = val;
    return out;
  };
  const head: CSSProperties = { fontSize: 12, fontWeight: 600, color: "var(--t1)", margin: "14px 0 6px" };

  return (
    <div>
      <div className="t-xs c-3 lh-snug mb-3" style={{ maxWidth: 460 }}>
        Which model the agent runs on, and which transcription service meeting bots use. There is
        one model: the agent&rsquo;s — meetings themselves run no inference. Provider
        &ldquo;subscription&rdquo; rides the deployment&rsquo;s Claude credentials; &ldquo;custom&rdquo; points at your own
        Anthropic/OpenAI-compatible endpoint (a LiteLLM/OpenRouter gateway serves open-source
        models). Empty fields inherit the deployment defaults.
      </div>
      <div style={head}>Your models</div>
      {catalog.length > 0 && (
        <div className="t-xs c-3 lh-snug mb-1_5" style={{ maxWidth: 460 }}>
          This deployment offers a model catalog: each chat picks its model in the composer, and new
          chats start on your default model. The endpoint fields below are your own endpoint&rsquo;s.
        </div>
      )}
      <ConfigForm key={`own-${catalog.length}`} fields={[...catalogFields, ...modelFields]}
        load={async () => asStrings(await getModelPrefs())}
        save={async (u) => asStrings(await setModelPrefs(u))} />
      <TestRow label="Test model credentials" run={testModels} />
      <div style={head}>Your transcription backend</div>
      <ConfigForm fields={transcriptionFields} load={async () => asStrings(await getTranscriptionPrefs())}
        save={async (u) => asStrings(await setTranscriptionPrefs(u))} />
      <TestRow label="Test transcription backend" run={testTranscription} />
      {globalAdmin && <>
        <div className="mt-5 c-accent" style={{ ...head }}>Global defaults (admin — every user without own settings)</div>
        <ConfigForm key={`global-${catalog.length}`} fields={[...globalCatalogFields, ...modelFields]}
          load={async () => (await getGlobalSetting("models")) ?? {}}
          save={(u) => setGlobalSetting("models", u)} />
        <div style={head}>Global transcription backend</div>
        <ConfigForm fields={transcriptionFields} load={async () => (await getGlobalSetting("transcription")) ?? {}}
          save={(u) => setGlobalSetting("transcription", u)} />
      </>}
    </div>
  );
}

/** Sign-in — WHO MAY SIGN IN to this instance (Vexa-ai/vexa#1783). Admin-only: the section is not in
 *  the nav at all unless `/api/admin/settings/signin` answers (404 for everybody else).
 *
 *  Existing users and admins always may; this list says who ELSE may. Two halves, both shown: the
 *  entries the admin writes here (stored in admin-api's platform settings) and the deployment's
 *  `VEXA_SIGNIN_ALLOW`, read-only because it is changed where it is set. Save sends the whole list
 *  and admin-api answers with it canonicalised — or refuses the whole write, naming every bad
 *  entry, so a typo can never silently admit less (or more) than what was typed. */
export function SigninSection() {
  const [loaded, setLoaded] = useState<SigninAllow | null>(null);
  const [text, setText] = useState("");
  const [initial, setInitial] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    let on = true;
    getSigninAllow()
      .then((v) => { if (on && v) { setLoaded(v); setText(allowLines(v.allow)); setInitial(allowLines(v.allow)); } })
      .catch((e: unknown) => on && setErr(presentError(e).headline));
    return () => { on = false; };
  }, []);

  const dirty = text.trim() !== initial.trim();
  const save = async () => {
    setBusy(true); setErr(null); setSaved(false);
    try {
      const stored = await setSigninAllow(text);
      setText(allowLines(stored)); setInitial(allowLines(stored)); setSaved(true);
    } catch (e: unknown) {
      // A 422 names every entry admin-api could not read — show that, not a generic headline.
      setErr(e instanceof ApiError && e.status === 422 && e.detail ? e.detail : presentError(e).headline);
    } finally { setBusy(false); }
  };

  const envEntries = loaded ? allowLines(loaded.env).split("\n").filter(Boolean) : [];
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10, maxWidth: 460 }}>
      <div className="t-xs c-3 lh-snug">
        Existing users and admins can always sign in. Everyone else needs to be on this list —
        an exact address (<span className="f-mono">alice@example.com</span>) or a whole
        domain (<span className="f-mono">@example.com</span>), one per line. Anybody else
        is refused at every door, and the email form never tells them so.
      </div>
      {err && <div role="alert" className="t-xs c-danger">⚠ {err}</div>}
      <textarea
        aria-label="Allowed addresses and domains"
        value={text}
        rows={6}
        placeholder={"@example.com\nalice@example.org"}
        onChange={(e) => { setSaved(false); setText(e.target.value); }}
        className="f-mono" style={{ ...field, resize: "vertical" }}
      />
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <button disabled={busy || !dirty} onClick={() => void save()}
          style={{ ...btn, background: dirty ? "var(--accent)" : "var(--panel2)", color: dirty ? "var(--on-accent)" : "var(--t3)", border: dirty ? "none" : btn.border, opacity: busy ? 0.5 : 1 }}>
          {busy ? "Saving…" : "Save"}
        </button>
        {saved && <span className="t-xs c-success">Saved — applies to the next sign-in</span>}
      </div>
      <div className="t-xs c-3 lh-snug mt-1_5">
        Also allowed by this deployment&rsquo;s <span className="f-mono">VEXA_SIGNIN_ALLOW</span>
        {envEntries.length ? ":" : " — nothing set."}
      </div>
      {envEntries.length > 0 && (
        <div className="t-xs f-mono c-2 lh-normal">
          {envEntries.map((e) => <div key={e}>{e}</div>)}
        </div>
      )}
      {loaded && loaded.envProblems.length > 0 && (
        <div role="alert" className="t-xs c-danger lh-snug">
          These deployment entries never match anything: {loaded.envProblems.join("; ")}
        </div>
      )}
    </div>
  );
}

function AccountSection() {
  const [user, setUser] = useState<{ email?: string | null; name?: string | null } | null>(null);
  useEffect(() => {
    let on = true;
    fetch("/api/auth/me", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => on && setUser((d?.user as { email?: string; name?: string } | undefined) ?? null))
      .catch(() => undefined);
    return () => { on = false; };
  }, []);
  return (
    <div className="t-xs c-2 lh-normal">
      <div><span className="c-3">Signed in as</span> <span className="c-1">{user?.name || user?.email || "…"}</span></div>
      {user?.email && <div><span className="c-3">Email</span> <span className="f-mono">{user.email}</span></div>}
      <div className="c-3 mt-1_5">Theme and sign-out live next to your name in the footer.</div>
    </div>
  );
}

function SettingsView() {
  const [section, setSection] = useState<SectionId>("calendar");
  // Admin probe for the admin-only sections: they appear in the nav only when the admin route
  // answers (404 = not an admin, and the section is not even named).
  const [admin, setAdmin] = useState(false);
  useEffect(() => {
    let on = true;
    getSigninAllow().then((v) => on && setAdmin(v !== null)).catch(() => undefined);
    return () => { on = false; };
  }, []);
  const bodies: Record<SectionId, ReactNode> = {
    calendar: <CalendarConnectionsPanel />,
    models: <ModelsSection />,
    tokens: <TokensPanel />,
    github: <GitHubTokenCard />,
    signin: <SigninSection />,
    account: <AccountSection />,
  };
  return (
    <div style={{ height: "100%", display: "flex", minHeight: 0 }}>
      <div className="bd-r pt-3 pr-2 pb-3 pl-2 bg-1" style={{ width: 160, flex: "none" }}>
        <div className="t-md fw-600 c-1 pt-0 pr-2 pb-2 pl-2">Settings</div>
        {SECTIONS.filter((s) => admin || !s.adminOnly).map((s) => (
          <button key={s.id} onClick={() => setSection(s.id)}
            className="t-xs pt-1_5 pr-2 pb-1_5 pl-2 r-md bd-none" style={{ display: "flex", alignItems: "center", gap: 7, width: "100%", textAlign: "left", cursor: "pointer", color: section === s.id ? "var(--t1)" : "var(--t2)", background: section === s.id ? "var(--panel2)" : "transparent" }}>
            <Icon name={s.icon} size={13} />{s.label}
          </button>
        ))}
      </div>
      <div className="pt-4 pr-5 pb-4 pl-5" style={{ flex: 1, overflowY: "auto", minWidth: 0 }}>
        <div className="t-sm fw-600 c-1 mb-3">
          {SECTIONS.find((s) => s.id === section)?.label}
        </div>
        {bodies[section]}
      </div>
    </div>
  );
}

// Minutes has no settings hub — a participant configures nothing.
if (!minutesOnly()) registerTab("settings", SettingsView);
