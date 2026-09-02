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
 *  STEPS 1 AND 2 ARE SKIPPABLE (the terminal must never hold the UI hostage); skipped steps are
 *  recorded so Settings can nudge later. STEP 3 IS NOT, and that exception is the whole point of it
 *  — see below.
 *
 *  ── STEP 3: THE COMPANY LAYER (founder ruling 2026-09-02) ──────────────────────────────────────
 *  "global needs to be setup by admin, it just should not let him start the service before that."
 *  A fresh instance serves NOBODY until the admin has written a thin company layer — who the company
 *  is, its principles, objectives, structure, and what is missing — into the platform `_global`
 *  workspace. Until then only the admin can sign in, the flows engine sends nothing, and the
 *  operator verbs refuse. So a skippable step 3 would be a button labelled "leave the instance
 *  broken", and the honest thing is to say on screen that it cannot be skipped rather than to hide
 *  the affordance and let the admin hunt for it.
 *
 *  IT IS NOT A FORM. Nothing about a company fits in three text inputs, and a form would collect
 *  fields where the product's actual answer is a conversation. Step 3 is a HAND-OFF: its button
 *  writes localStorage["vexa.setupGlobal"]="1" and drops this component from a full-screen overlay
 *  to a small fixed card in the corner, so the workbench MOUNTS UNDERNEATH and fires the setup
 *  conversation. The wizard then stops being a wizard and becomes a progress readout: the card polls
 *  /api/global/state, names the files still absent, and — only once the server says the layer is
 *  complete — offers the Continue that finally writes setup.completed.
 *
 *  Hence the four phases: "checking" | "hidden" | "wizard" | "handoff". In "handoff" the component
 *  renders <>{children}<GateCard/></> — children being the live workbench. The handoff is persisted
 *  into the same platform-settings "setup" key that carries the per-step state, so a reload in the
 *  middle of the conversation resumes AS a handoff instead of throwing the admin back to step 1 and
 *  asking them to configure their model again. */
import { useCallback, useEffect, useRef, useState, type CSSProperties } from "react";
import {
  COMPANY_LAYER_FILES, getGlobalSetting, getGlobalState, setGlobalSetting, testModels, testTranscription,
  type ConfigTestResult, type GlobalSetting, type GlobalState,
} from "../surfaces/settingsApi";

/** Show the setup surface at all? null = not an admin (probe 404s); completed set = already ran.
 *  Note what does NOT appear here: the company layer's own state. `setup.completed` is written by
 *  exactly one thing — the Continue button on the handoff card, which only appears after the SERVER
 *  said the layer is complete. Deriving completion here as well would be a second writer on the same
 *  decision, and the two would disagree the first time a poll was in flight during a reload. */
export function shouldShowSetup(setup: GlobalSetting | null): boolean {
  if (setup === null) return false;
  return setup.completed !== "true";
}

type Phase = "checking" | "hidden" | "wizard" | "handoff";
type StepState = "done" | "skipped";

/** The value `setup.global` carries once the admin has handed off to the chat. */
const HANDOFF = "handoff";

/** Where a RELOAD should resume. The admin who reloads mid-conversation is not starting over: they
 *  have already handed off, the workbench is already the place the work is happening, and the only
 *  honest thing to show them is the same corner card they left. */
export function setupResumePhase(setup: GlobalSetting | null): Exclude<Phase, "checking"> {
  if (!shouldShowSetup(setup)) return "hidden";
  return setup?.global === HANDOFF ? HANDOFF : "wizard";
}

/** Which wizard step a reload resumes on — the first one that has no recorded outcome. `advance()`
 *  persists each step as it happens precisely so this can be read back. */
export function setupResumeStep(setup: GlobalSetting | null): 1 | 2 | 3 {
  if (setup?.transcription) return 3;
  if (setup?.models) return 2;
  return 1;
}

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
              — see the <a href="https://docs.vexa.ai/configuration" target="_blank" rel="noreferrer" style={{ color: "var(--t2)", textDecoration: "underline" }}>configuration docs</a>{" "}
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

function Steps({ at }: { at: 1 | 2 | 3 }) {
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
      <span style={{ width: 24, height: 1, background: "var(--line2)" }} />
      {dot(3, "Company")}
    </div>
  );
}

// ── the company layer ───────────────────────────────

/** How often the corner card asks the server whether the layer is done. Slow enough to be free,
 *  fast enough that the admin never wonders whether it noticed — the alternative would be pushing
 *  the gate down a socket, which is real machinery for a screen that opens once in an instance's life. */
const GLOBAL_POLL_MS = 4000;

type Poll = { state: GlobalState | null; error: string | null };

/** Read /api/global/state now and every GLOBAL_POLL_MS after, until `stop` is true.
 *
 *  A read that FAILS keeps the last good state and records the error beside it, rather than clearing
 *  it: a single dropped poll must not blank a card the admin is reading, and an unreachable server
 *  must never be rendered as "the layer went away". */
function useGlobalState(stop: boolean): Poll {
  const [poll, setPoll] = useState<Poll>({ state: null, error: null });
  const alive = useRef(true);

  const read = useCallback(async () => {
    try {
      const state = await getGlobalState();
      if (alive.current) setPoll({ state, error: null });
    } catch (e: unknown) {
      if (alive.current) setPoll((prev) => ({ state: prev.state, error: e instanceof Error ? e.message : String(e) }));
    }
  }, []);

  useEffect(() => {
    alive.current = true;
    if (stop) return () => { alive.current = false; };
    void read();
    const id = setInterval(() => void read(), GLOBAL_POLL_MS);
    return () => { alive.current = false; clearInterval(id); };
  }, [read, stop]);

  return poll;
}

/** The five files, each marked present or absent. `present` comes from the server; the constant only
 *  fixes the ORDER, so the list does not reshuffle between polls under the admin's eyes. */
function FileList({ present }: { present: string[] }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
      {COMPANY_LAYER_FILES.map((f) => {
        const have = present.includes(f);
        return (
          <div key={f} style={{ fontSize: 11.5, fontFamily: "var(--mono)", color: have ? "var(--green)" : "var(--t3)" }}>
            {have ? "✓" : "○"} {f}
          </div>
        );
      })}
    </div>
  );
}

/** Step 3 — the company layer. NOT A FORM and NOT SKIPPABLE; the file header explains both. Its one
 *  button hands off to the setup CHAT: persist that we are handing off, then navigate to
 *  `/?setup=global`, which App.tsx turns into the pending preset the workbench opens on mount. The
 *  navigation is what keeps the full-screen overlay and the chat from ever coexisting — the page
 *  reloads, the conversation opens, and this component comes back as the corner card. */
function CompanyLayerStep({ onHandOff }: { onHandOff: () => void }) {
  const { state, error } = useGlobalState(false);

  return (
    <>
      <div style={{ fontSize: 19, fontWeight: 650, color: "var(--t1)" }}>Who is this company?</div>
      <div style={{ fontSize: 12, color: "var(--t3)", lineHeight: 1.55 }}>
        The company layer is the thin set of files every part of this instance reads: who the company
        is, what it stands for, what it is working toward, how it is structured, and what is missing.
        <br />
        Until it exists this Vexa serves nobody — nothing is sent, and the agent refuses to act.
      </div>

      <div style={{ ...card, cursor: "default", gap: 9 }}>
        {/* Deliberately NOT the `desk` word from minutes/vocabulary.ts. That constant names a
            PERSON's own space; `_global` is the company layer, shared by everyone, and calling it a
            desk would teach the reader the opposite of the distinction the word exists to draw. The
            fix for "workspace" here is to name the thing, not to swap one wrong noun for another. */}
        <span style={label}>This instance&rsquo;s company layer</span>
        <FileList present={state?.present ?? []} />
        {error && !state && (
          <span role="alert" style={{ fontSize: 11.5, color: "var(--t3)" }}>
            Couldn&rsquo;t read the company layer just now &mdash; still checking.
          </span>
        )}
      </div>

      <div style={{ fontSize: 11.5, color: "var(--t2)", lineHeight: 1.5 }}>
        This step can&rsquo;t be skipped &mdash; setup isn&rsquo;t finished until the company layer exists.
      </div>

      <div style={{ display: "flex", justifyContent: "flex-end", alignItems: "center",
        marginTop: 10, paddingTop: 14, borderTop: "1px dashed var(--line2)" }}>
        <button style={primaryBtn} onClick={onHandOff}>Write it with the agent</button>
      </div>
      <div style={{ fontSize: 10.5, color: "var(--t3)", lineHeight: 1.4, textAlign: "right" }}>
        This opens a chat and moves setup to a small card in the corner.
      </div>
    </>
  );
}

/** The corner card — the wizard after the hand-off, reduced to a progress readout the admin can
 *  ignore while they talk to the agent underneath it.
 *
 *  It renders the SERVER's `reasons` verbatim rather than composing its own account of what is
 *  missing (settingsApi.GlobalState says why), and it offers Continue only when the server says
 *  `global_setup === "completed"`. Continue is the ONLY writer of `setup.completed`. */
function GateCard({ onContinue }: { onContinue: () => void }) {
  const [done, setDone] = useState(false);
  const { state, error } = useGlobalState(done);
  const complete = state?.global_setup === "completed";

  // Stop polling the moment the answer is yes — the card is now waiting on the human, not the server.
  useEffect(() => { if (complete) setDone(true); }, [complete]);

  return (
    <div
      data-testid="global-gate-card"
      style={{
        position: "fixed", right: 16, bottom: 16, width: 300, zIndex: 40,
        background: "var(--panel)", border: "1px solid var(--line2)", borderRadius: 10,
        padding: "14px 15px", display: "flex", flexDirection: "column", gap: 9,
        boxShadow: "0 8px 32px rgba(0,0,0,.34)",
      }}
    >
      <span style={label}>Company layer</span>

      {complete ? (
        <>
          <div style={{ fontSize: 13, fontWeight: 600, color: "var(--t1)", lineHeight: 1.4 }}>
            {state?.company ? `${state.company} — set up ✓` : "Company layer written ✓"}
          </div>
          <div style={{ fontSize: 11.5, color: "var(--t3)", lineHeight: 1.5 }}>
            This instance can serve your team now.
          </div>
          <button style={{ ...primaryBtn, padding: "8px 14px" }} onClick={onContinue}>Continue</button>
        </>
      ) : (
        <>
          <div style={{ fontSize: 12, color: "var(--t2)", lineHeight: 1.5 }}>
            Tell the agent about your company in the chat. This card updates as the files land.
          </div>
          <FileList present={state?.present ?? []} />
          {state?.reasons?.length ? (
            <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
              {state.reasons.map((r) => (
                <div key={r} style={{ fontSize: 11, color: "var(--t3)", lineHeight: 1.45 }}>{r}</div>
              ))}
            </div>
          ) : null}
          {error && (
            <div style={{ fontSize: 11, color: "var(--t3)", lineHeight: 1.45 }}>
              Couldn&rsquo;t reach the instance just now &mdash; still checking.
            </div>
          )}
        </>
      )}
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
      .then((v) => {
        if (!on) return;
        // Restore what the platform-settings key remembers, so a reload resumes where the admin
        // actually is — same phase (wizard vs. handed-off), same step — instead of re-asking for a
        // model provider they already configured.
        setStates({
          models: v?.models as StepState | undefined,
          transcription: v?.transcription as StepState | undefined,
        });
        setStep(setupResumeStep(v));
        setPhase(setupResumePhase(v));
      })
      .catch(() => on && setPhase("hidden")); // fail-safe: never block the workbench on the probe
    return () => { on = false; };
  }, []);

  const finish = () => {
    void setGlobalSetting("setup", { completed: "true" }).catch(() => undefined);
    // The admin→user onboarding seam: Continue must actually LAND on Meetings. The workbench's
    // layout store initializes its rail from this persisted key (layout.ts LS_LIST) and it is
    // created only when the workbench mounts, so a plain localStorage write is the whole hand-off.
    //
    // ONE CAVEAT now that Continue lives on a card floating OVER a live workbench: in the "handoff"
    // phase the workbench is already mounted, so it has already read this key and the rail may not
    // move. That is the better behaviour anyway — yanking the view the admin was just working in
    // would be worse than leaving them in it — and the write still does its job on the next load.
    try { localStorage.setItem("vexa.terminal.activeList.v1", "meetings"); } catch { /* noop */ }
    setPhase("hidden");
  };

  if (phase === "checking") return <div style={{ height: "100vh", background: "var(--bg)" }} />;
  if (phase === "hidden") return <>{children}</>;
  // HANDED OFF: the workbench mounts and runs the setup conversation; the wizard is now a card in
  // the corner watching the server for the answer.
  if (phase === "handoff") return <>{children}<GateCard onContinue={finish} /></>;

  const advance = (key: "models" | "transcription", state: StepState) => {
    const next = { ...states, [key]: state };
    setStates(next);
    // Persist per-step state as it happens — a mid-wizard reload resumes honestly.
    void setGlobalSetting("setup", { [key]: state }).catch(() => undefined);
    if (key === "models") setStep(2);
    else setStep(3);
  };

  /** Step 3's hand-off. ORDER MATTERS: persist the phase FIRST, then navigate. The navigation
   *  destroys this component, so a write started after it may never be sent — and an admin who
   *  lands in the setup chat with nothing persisted is thrown back to step 1 on their next reload,
   *  asked to configure a model provider they already configured, with no sign that the
   *  conversation they were having was the actual work. */
  const handOff = () => {
    void setGlobalSetting("setup", { global: HANDOFF })
      .catch(() => undefined)
      .finally(() => {
        // App.tsx turns `?setup=global` into the pending preset the workbench opens on mount
        // (`_global/asks/setup-global.md`) and strips the query itself. A full navigation, not a
        // phase flip, so the overlay and the chat never coexist.
        window.location.assign("/?setup=global");
      });
  };

  return (
    <div style={{ height: "100vh", background: "var(--bg)", display: "flex", alignItems: "center", justifyContent: "center", overflowY: "auto" }}>
      <div style={{ width: 520, maxWidth: "94vw", background: "var(--panel)", border: "1px solid var(--line2)",
        borderRadius: 12, padding: 26, display: "flex", flexDirection: "column", gap: 14, boxShadow: "0 8px 32px rgba(0,0,0,.3)" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <span style={label}>Set up your instance</span>
          <Steps at={step} />
        </div>
        {step === 1 && <ModelsStep onNext={(s) => advance("models", s)} />}
        {step === 2 && <TranscriptionStep onNext={(s) => advance("transcription", s)} />}
        {step === 3 && (
          <>
            {/* Steps 1–2 were skippable, so say what actually happened before asking for the one
                step that is not — the admin should not have to remember what they skipped. */}
            <div style={{ fontSize: 12.5, color: "var(--t2)", lineHeight: 1.8 }}>
              <div style={{ color: states.models === "done" ? "var(--green)" : "var(--t3)" }}>
                {states.models === "done" ? "✓ Agent model configured and tested" : "○ Agent model skipped — finish it in Settings → Models"}
              </div>
              <div style={{ color: states.transcription === "done" ? "var(--green)" : "var(--t3)" }}>
                {states.transcription === "done" ? "✓ Transcription configured and tested" : "○ Transcription skipped — finish it in Settings → Models"}
              </div>
            </div>
            <CompanyLayerStep onHandOff={handOff} />
          </>
        )}
      </div>
    </div>
  );
}
