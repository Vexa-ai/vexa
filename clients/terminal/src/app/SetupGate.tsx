"use client";
/** SetupGate — the ADMIN first-run wizard (first-run onboarding design, 2026-07-09). Sits inside
 *  AuthGate: once the bootstrap-claimed admin signs in, this walks the two things a meeting needs
 *  — the agent model and the transcription backend — each SMOKE-TESTED inline against the real
 *  backend (the same /api/{models,transcription}/test edges Settings → Models ships), writing the
 *  GLOBAL platform settings (this is the instance-wide admin flow; per-user overrides stay in
 *  Settings). Durable state lives in the platform-settings "setup" key, so the wizard shows once
 *  per INSTANCE, never per browser. Non-admins (the setup probe 404s → null) fall straight
 *  through — they can never see or affect instance setup.
 *
 *  Every step is skippable (the terminal must never hold the UI hostage); skipped steps are
 *  recorded so Settings can nudge later. */
import { useEffect, useState, type CSSProperties } from "react";
import {
  getGlobalSetting, setGlobalSetting, testModels, testTranscription,
  type ConfigTestResult, type GlobalSetting,
} from "../surfaces/settingsApi";

/** Show the wizard? null = not an admin (probe 404s); completed set = already ran. */
export function shouldShowSetup(setup: GlobalSetting | null): boolean {
  if (setup === null) return false;
  return setup.completed !== "true";
}

type Phase = "checking" | "hidden" | "wizard";
type StepState = "done" | "skipped";

const card: CSSProperties = {
  border: "1px solid var(--line2)", borderRadius: 10, padding: "13px 15px",
  display: "flex", flexDirection: "column", gap: 6, cursor: "pointer",
};
const cardSel: CSSProperties = { ...card, borderColor: "var(--accent)", background: "var(--panel2)" };
const field: CSSProperties = {
  width: "100%", boxSizing: "border-box", fontSize: 12.5, padding: "8px 10px", borderRadius: 7,
  border: "1px solid var(--line2)", background: "var(--panel2)", color: "var(--t1)", outline: "none",
};
const primaryBtn: CSSProperties = {
  background: "var(--accent)", color: "var(--on-accent)", border: "none", borderRadius: 7,
  padding: "9px 18px", fontSize: 13, fontWeight: 600, cursor: "pointer",
};
const quietBtn: CSSProperties = {
  background: "transparent", color: "var(--t2)", border: "1px solid var(--line2)", borderRadius: 7,
  padding: "8px 14px", fontSize: 12.5, cursor: "pointer",
};
const label: CSSProperties = {
  fontSize: 10.5, letterSpacing: ".08em", textTransform: "uppercase", color: "var(--t3)", fontWeight: 600,
};

function TestLine({ res, err, busy }: { res: ConfigTestResult | null; err: string | null; busy: boolean }) {
  if (busy) return <span style={{ fontSize: 11.5, color: "var(--t3)" }}>Testing…</span>;
  if (err) return <span role="alert" style={{ fontSize: 11.5, color: "var(--danger)" }}>⚠ {err}</span>;
  if (!res) return null;
  return (
    <span style={{ fontSize: 11.5, color: res.ok ? "var(--green)" : "var(--danger)", lineHeight: 1.5 }}>
      {res.ok ? "✓" : "✗"} {res.summary}
    </span>
  );
}

/** Step 1 — agent model: Claude subscription on this machine (detect via the real test edge) or a
 *  custom OpenRouter/OpenAI-compatible endpoint. */
function ModelsStep({ onNext }: { onNext: (state: StepState) => void }) {
  const [choice, setChoice] = useState<"subscription" | "custom">("subscription");
  const [detect, setDetect] = useState<ConfigTestResult | null>(null);
  const [detecting, setDetecting] = useState(true);
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState("");
  const [res, setRes] = useState<ConfigTestResult | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const recheck = () => {
    setDetecting(true);
    testModels().then((r) => { setDetect(r); setDetecting(false); })
      .catch((e: unknown) => { setDetect({ ok: false, summary: e instanceof Error ? e.message : String(e) }); setDetecting(false); });
  };
  useEffect(recheck, []);

  const detected = !detecting && detect?.ok === true;

  const saveAndTest = async () => {
    setBusy(true); setErr(null); setRes(null);
    try {
      if (choice === "custom") {
        await setGlobalSetting("models", {
          mode: "custom", base_url: baseUrl.trim(), api_key: apiKey.trim(), model: model.trim(),
        });
      } else {
        await setGlobalSetting("models", { mode: "subscription" });
      }
      setRes(await testModels());
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const canContinue = (choice === "subscription" && detected) || res?.ok === true;

  return (
    <>
      <div style={{ fontSize: 19, fontWeight: 650, color: "var(--t1)" }}>How should the agent think?</div>
      <div style={{ fontSize: 12, color: "var(--t3)", lineHeight: 1.5 }}>
        Pick the model provider for chat, briefs, and meeting notes. You can change this anytime in
        Settings → Models.
      </div>

      <div style={choice === "subscription" ? cardSel : card} onClick={() => setChoice("subscription")}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, fontWeight: 600, color: "var(--t1)" }}>
          <Radio on={choice === "subscription"} /> Claude subscription on this machine
          {detecting
            ? <Badge tone="muted">checking…</Badge>
            : detected ? <Badge tone="ok">detected</Badge> : <Badge tone="warn">not detected</Badge>}
        </div>
        <div style={{ fontSize: 11.5, color: "var(--t3)", lineHeight: 1.5, marginLeft: 22 }}>
          Uses the Claude Code credentials already on this computer. No API key needed.
        </div>
        {!detecting && !detected && (
          <div style={{ marginLeft: 22, display: "flex", flexDirection: "column", gap: 7 }}>
            <div style={{ fontSize: 11.5, color: "var(--t2)", lineHeight: 1.5 }}>
              No Claude credentials detected in the deployment environment. Set up a model
              provider via <code style={{ fontSize: 11, fontFamily: "var(--mono)", background: "var(--panel2)", padding: "1px 4px", borderRadius: 3 }}>HOST_CLAUDE_CREDENTIALS</code>{" "}
              in deployment settings or select the "OpenRouter or custom endpoint" option above
              — see <code style={{ fontSize: 11, fontFamily: "var(--mono)", background: "var(--panel2)", padding: "1px 4px", borderRadius: 3 }}>docs/docs/configuration.mdx</code>{" "}
              for all setup options.
            </div>
            {detect && !detect.ok && <TestLine res={detect} err={null} busy={false} />}
            <button style={{ ...quietBtn, alignSelf: "flex-start" }} onClick={(e) => { e.stopPropagation(); recheck(); }}>
              Re-check
            </button>
          </div>
        )}
        {detected && choice === "subscription" && (
          <div style={{ marginLeft: 22 }}><TestLine res={detect} err={null} busy={false} /></div>
        )}
      </div>

      <div style={choice === "custom" ? cardSel : card} onClick={() => setChoice("custom")}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, fontWeight: 600, color: "var(--t1)" }}>
          <Radio on={choice === "custom"} /> OpenRouter or custom endpoint
        </div>
        <div style={{ fontSize: 11.5, color: "var(--t3)", lineHeight: 1.5, marginLeft: 22 }}>
          Any Anthropic/OpenAI-compatible endpoint. Bring your own key.
        </div>
        {choice === "custom" && (
          <div style={{ marginLeft: 22, display: "flex", flexDirection: "column", gap: 7 }}>
            <input style={field} placeholder="https://openrouter.ai/api/v1" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} />
            <input style={field} placeholder="API key" type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)} />
            <input style={field} placeholder="Model — e.g. anthropic/claude-sonnet-4.5" value={model} onChange={(e) => setModel(e.target.value)} />
          </div>
        )}
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 10, minHeight: 24 }}>
        {choice === "custom" && (
          <button style={{ ...quietBtn, opacity: busy || !baseUrl.trim() ? 0.5 : 1 }} disabled={busy || !baseUrl.trim()}
            onClick={() => void saveAndTest()}>
            {busy ? "Testing…" : "Save & test"}
          </button>
        )}
        <TestLine res={res} err={err} busy={false} />
      </div>

      <Foot
        onSkip={() => onNext("skipped")}
        next={
          <button style={{ ...primaryBtn, opacity: canContinue ? 1 : 0.5 }} disabled={!canContinue}
            onClick={async () => {
              // Subscription path: persist the explicit choice so the instance default is declared.
              if (choice === "subscription" && !res) {
                try { await setGlobalSetting("models", { mode: "subscription" }); } catch { /* declarative only */ }
              }
              onNext("done");
            }}>
            Continue
          </button>
        }
      />
    </>
  );
}

/** Step 2 — transcription: hosted Vexa token (vexa.ai/account) or any OpenAI-compatible STT. */
function TranscriptionStep({ onNext }: { onNext: (state: StepState) => void }) {
  const [choice, setChoice] = useState<"vexa" | "custom">("vexa");
  const [token, setToken] = useState("");
  const [url, setUrl] = useState("");
  const [customToken, setCustomToken] = useState("");
  const [res, setRes] = useState<ConfigTestResult | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // The deployment env may already carry a working backend (e.g. hosted Vexa baked into .env) —
  // surface that: a green pre-test means "you can just continue".
  useEffect(() => {
    testTranscription().then(setRes).catch(() => undefined);
  }, []);

  const saveAndTest = async () => {
    setBusy(true); setErr(null);
    try {
      if (choice === "vexa") {
        await setGlobalSetting("transcription", { url: "https://transcription.vexa.ai", token: token.trim() });
      } else {
        await setGlobalSetting("transcription", { url: url.trim(), token: customToken.trim() });
      }
      setRes(await testTranscription());
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const dirty = choice === "vexa" ? !!token.trim() : !!url.trim();

  return (
    <>
      <div style={{ fontSize: 19, fontWeight: 650, color: "var(--t1)" }}>Who turns speech into text?</div>
      <div style={{ fontSize: 12, color: "var(--t3)", lineHeight: 1.5 }}>
        Meeting bots stream audio to a transcription service. Hosted Vexa is the zero-setup path.
      </div>

      <div style={choice === "vexa" ? cardSel : card} onClick={() => setChoice("vexa")}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, fontWeight: 600, color: "var(--t1)" }}>
          <Radio on={choice === "vexa"} /> Vexa hosted transcription
        </div>
        <div style={{ fontSize: 11.5, color: "var(--t3)", lineHeight: 1.5, marginLeft: 22 }}>
          Get your token at{" "}
          <a href="https://www.vexa.ai/account" target="_blank" rel="noreferrer" style={{ color: "var(--accent)" }}>
            www.vexa.ai/account
          </a>{" "}
          — free tier included. Paste it here.
        </div>
        {choice === "vexa" && (
          <input style={{ ...field, marginLeft: 22, width: "calc(100% - 22px)" }} placeholder="Transcription token"
            type="password" value={token} onChange={(e) => setToken(e.target.value)} />
        )}
      </div>

      <div style={choice === "custom" ? cardSel : card} onClick={() => setChoice("custom")}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, fontWeight: 600, color: "var(--t1)" }}>
          <Radio on={choice === "custom"} /> OpenAI-compatible endpoint
        </div>
        <div style={{ fontSize: 11.5, color: "var(--t3)", lineHeight: 1.5, marginLeft: 22 }}>
          Any service speaking the OpenAI transcription API (Whisper-compatible).
        </div>
        {choice === "custom" && (
          <div style={{ marginLeft: 22, display: "flex", flexDirection: "column", gap: 7 }}>
            <input style={field} placeholder="https://your-stt.example.com" value={url} onChange={(e) => setUrl(e.target.value)} />
            <input style={field} placeholder="API key (optional)" type="password" value={customToken} onChange={(e) => setCustomToken(e.target.value)} />
          </div>
        )}
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 10, minHeight: 24 }}>
        <button style={{ ...quietBtn, opacity: busy || !dirty ? 0.5 : 1 }} disabled={busy || !dirty}
          onClick={() => void saveAndTest()}>
          {busy ? "Testing…" : "Save & test"}
        </button>
        <TestLine res={res} err={err} busy={busy} />
      </div>

      <Foot
        onSkip={() => onNext("skipped")}
        next={
          <button style={{ ...primaryBtn, opacity: res?.ok ? 1 : 0.5 }} disabled={!res?.ok} onClick={() => onNext("done")}>
            Finish setup
          </button>
        }
      />
    </>
  );
}

function Radio({ on }: { on: boolean }) {
  return (
    <span style={{
      width: 13, height: 13, borderRadius: "50%", flex: "none", boxSizing: "border-box",
      border: on ? "4px solid var(--accent)" : "1.5px solid var(--t3)",
    }} />
  );
}

function Badge({ tone, children }: { tone: "ok" | "warn" | "muted"; children: React.ReactNode }) {
  const color = tone === "ok" ? "var(--green)" : tone === "warn" ? "var(--warn, #d3ab5f)" : "var(--t3)";
  return (
    <span style={{ fontSize: 9.5, letterSpacing: ".06em", textTransform: "uppercase", fontWeight: 650,
      color, border: `1px solid ${color}`, borderRadius: 4, padding: "1px 6px", opacity: 0.9 }}>
      {children}
    </span>
  );
}

function Foot({ onSkip, next }: { onSkip: () => void; next: React.ReactNode }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center",
      marginTop: 10, paddingTop: 14, borderTop: "1px dashed var(--line2)" }}>
      <button onClick={onSkip}
        style={{ background: "none", border: "none", color: "var(--t3)", fontSize: 12, cursor: "pointer", textDecoration: "underline", padding: 0 }}>
        Skip for now
      </button>
      {next}
    </div>
  );
}

function Steps({ at }: { at: 1 | 2 }) {
  const dot = (n: number, label: string) => {
    const on = at === n, done = at > n;
    return (
      <span key={n} style={{ display: "flex", alignItems: "center", gap: 6, color: on ? "var(--t1)" : "var(--t3)", fontSize: 11.5 }}>
        <span style={{
          width: 18, height: 18, borderRadius: "50%", display: "grid", placeItems: "center", fontSize: 10, fontWeight: 700,
          background: on ? "var(--accent)" : done ? "var(--panel2)" : "transparent",
          color: on ? "var(--on-accent)" : done ? "var(--green)" : "var(--t3)",
          border: on ? "none" : "1px solid var(--line2)",
        }}>{done ? "✓" : n}</span>
        {label}
      </span>
    );
  };
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
      {dot(1, "Agent model")}
      <span style={{ width: 24, height: 1, background: "var(--line2)" }} />
      {dot(2, "Transcription")}
    </div>
  );
}

export function SetupGate({ children }: { children: React.ReactNode }) {
  const [phase, setPhase] = useState<Phase>("checking");
  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [states, setStates] = useState<{ models?: StepState; transcription?: StepState }>({});

  useEffect(() => {
    let on = true;
    getGlobalSetting("setup")
      .then((v) => on && setPhase(shouldShowSetup(v) ? "wizard" : "hidden"))
      .catch(() => on && setPhase("hidden")); // fail-safe: never block the workbench on the probe
    return () => { on = false; };
  }, []);

  if (phase === "checking") return <div style={{ height: "100vh", background: "var(--bg)" }} />;
  if (phase === "hidden") return <>{children}</>;

  const advance = (key: "models" | "transcription", state: StepState) => {
    const next = { ...states, [key]: state };
    setStates(next);
    // Persist per-step state as it happens — a mid-wizard reload resumes honestly.
    void setGlobalSetting("setup", { [key]: state }).catch(() => undefined);
    if (key === "models") setStep(2);
    else setStep(3);
  };

  const finish = () => {
    void setGlobalSetting("setup", { completed: "true" }).catch(() => undefined);
    // The admin→user onboarding seam: "Go to Meetings" must actually LAND on Meetings. The
    // workbench's layout store initializes its rail from this persisted key (layout.ts LS_LIST)
    // and it is created only when the workbench mounts — i.e. after this gate unhides — so a
    // plain localStorage write is the whole hand-off.
    try { localStorage.setItem("vexa.terminal.activeList.v1", "meetings"); } catch { /* noop */ }
    setPhase("hidden");
  };

  return (
    <div style={{ height: "100vh", background: "var(--bg)", display: "flex", alignItems: "center", justifyContent: "center", overflowY: "auto" }}>
      <div style={{ width: 520, maxWidth: "94vw", background: "var(--panel)", border: "1px solid var(--line2)",
        borderRadius: 12, padding: 26, display: "flex", flexDirection: "column", gap: 14, boxShadow: "0 8px 32px rgba(0,0,0,.3)" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <span style={label}>Set up your instance</span>
          {step < 3 && <Steps at={step as 1 | 2} />}
        </div>
        {step === 1 && <ModelsStep onNext={(s) => advance("models", s)} />}
        {step === 2 && <TranscriptionStep onNext={(s) => advance("transcription", s)} />}
        {step === 3 && (
          <>
            <div style={{ fontSize: 19, fontWeight: 650, color: "var(--t1)" }}>You&rsquo;re set ✓</div>
            <div style={{ fontSize: 12.5, color: "var(--t2)", lineHeight: 1.8 }}>
              <div style={{ color: states.models === "done" ? "var(--green)" : "var(--t3)" }}>
                {states.models === "done" ? "✓ Agent model configured and tested" : "○ Agent model skipped — finish it in Settings → Models"}
              </div>
              <div style={{ color: states.transcription === "done" ? "var(--green)" : "var(--t3)" }}>
                {states.transcription === "done" ? "✓ Transcription configured and tested" : "○ Transcription skipped — finish it in Settings → Models"}
              </div>
            </div>
            <div style={{ fontSize: 12, color: "var(--t3)", lineHeight: 1.5 }}>
              Everything a meeting needs is wired up. Next: get a meeting in front of a bot — connect
              your calendar, plan a meeting, or drop a bot on a running Meet.
            </div>
            <div>
              <button style={primaryBtn} onClick={finish}>Go to Meetings</button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
