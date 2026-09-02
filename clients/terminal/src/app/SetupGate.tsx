"use client";
/** SetupGate — what the administrator of a fresh Vexa meets, and nothing else.
 *
 *  Sits inside AuthGate: once the bootstrap-claimed admin signs in, this decides what the instance
 *  shows them. Durable state lives in the platform-settings "setup" key, so it is once per
 *  INSTANCE, never once per browser. Non-admins (the setup probe 404s → null) fall straight
 *  through — they can never see or affect instance setup.
 *
 *  ── THE COMPANY LAYER (founder ruling 2026-09-02) ──────────────────────────────────────────────
 *  "global needs to be setup by admin, it just should not let him start the service before that."
 *  A fresh instance serves NOBODY until the admin has written a thin company layer — who the company
 *  is, its principles, objectives, structure, and what is missing — into the platform `_global`
 *  workspace. Until then only the admin can sign in, the flows engine sends nothing, and the
 *  operator verbs refuse.
 *
 *  ── THERE IS NO WIZARD IN FRONT OF IT ANY MORE (founder ruling 2026-09-02, second pass) ─────────
 *  Watching a real first admin click, the founder said: "this is what I get from the first admin
 *  click — it should want to setup global here." What stood between the claim and the company-setup
 *  conversation was a three-step wizard (agent model → transcription → a screen explaining the
 *  company layer), and what it landed him in was a Personal chat with the ordinary greeting.
 *
 *  So the steps are GONE from this file. Model and transcription are not preconditions for talking
 *  about the company — they are preconditions for a MEETING, they are configurable in
 *  Settings → Models at any moment, and both were already skippable, which is the product admitting
 *  they were never gates. Asking for them first bought nothing and cost the admin the one screen
 *  that actually had to happen. They now ride on the corner card as one quiet line, probed against
 *  the same test edges the steps used, and only when something really is unset.
 *
 *  What is left is two phases and one card:
 *
 *    "opening"  the marker has not been written yet: write it, then NAVIGATE to the setup chat.
 *               A screen the admin sees for a moment, never a screen they act on — unless the
 *               write fails, which is the one thing that stops the navigation (see handOff below).
 *    "card"     the workbench is mounted and running the company-setup conversation; this component
 *               is a small fixed card in the corner watching /api/global/state for the verdict.
 *
 *  The navigation, not a phase flip, is what opens the conversation: App.tsx turns `?setup=global`
 *  into the pending preset the workbench opens on mount (`_global/asks/setup-global.md`, admin-
 *  authored and read hot at click time). A phase flip would leave the preset unstashed and the chat
 *  would never open. The handoff marker is persisted into the same platform-settings "setup" key,
 *  so a reload mid-conversation resumes AS the card instead of re-running the hand-off. */
import { useCallback, useEffect, useRef, useState, type CSSProperties } from "react";
import {
  COMPANY_LAYER_FILES, getGlobalSetting, getGlobalState, setGlobalSetting, testModels, testTranscription,
  type GlobalSetting, type GlobalState,
} from "../surfaces/settingsApi";

/** Show the setup surface at all? null = not an admin (probe 404s); completed set = already ran.
 *  Note what does NOT appear here: the company layer's own state. `setup.completed` is written by
 *  exactly one thing — the "Open this Vexa" button on the card, which only appears after the SERVER
 *  said the layer is complete. Deriving completion here as well would be a second writer on the same
 *  decision, and the two would disagree the first time a poll was in flight during a reload. */
export function shouldShowSetup(setup: GlobalSetting | null): boolean {
  if (setup === null) return false;
  return setup.completed !== "true";
}

type Phase = "checking" | "hidden" | "opening" | "card";

/** The value `setup.global` carries once the admin has been sent to the setup chat. */
const HANDOFF = "handoff";

/** Where a RELOAD should resume. The admin who reloads mid-conversation is not starting over: they
 *  have already been handed off, the workbench is already the place the work is happening, and the
 *  only honest thing to show them is the same corner card they left. */
export function setupResumePhase(setup: GlobalSetting | null): Exclude<Phase, "checking"> {
  if (!shouldShowSetup(setup)) return "hidden";
  return setup?.global === HANDOFF ? "card" : "opening";
}

/** One attempt at the hand-off per browser session.
 *
 *  The hand-off is now AUTOMATIC — nobody presses anything — and an automatic navigation that lands
 *  back where it started is a redirect loop, not a dead button. The exact bug ruling 3 is about
 *  (admin-api answering 200 while dropping the write) would produce precisely that: navigate, come
 *  back in "opening" because the marker never persisted, navigate again, forever. So we record that
 *  we tried; coming back still in "opening" is POSITIVE EVIDENCE that the write did not stick, and
 *  the admin gets the failure screen instead of another lap. Session-scoped, so closing the tab is
 *  a clean retry. */
const HANDOFF_TRIED_KEY = "vexa.setupHandoffAttempted";
const handoffTried = (): boolean => {
  try { return sessionStorage.getItem(HANDOFF_TRIED_KEY) === "1"; } catch { return false; }
};
const markHandoffTried = (): void => {
  try { sessionStorage.setItem(HANDOFF_TRIED_KEY, "1"); } catch { /* storage unavailable — worst case, one extra lap */ }
};

const primaryBtn: CSSProperties = {
  background: "var(--accent)", color: "var(--on-accent)", border: "none", borderRadius: 7,
  padding: "9px 18px", fontSize: 13, fontWeight: 600, cursor: "pointer",
};
const label: CSSProperties = {
  fontSize: 10.5, letterSpacing: ".08em", textTransform: "uppercase", color: "var(--t3)", fontWeight: 600,
};

// ── the company layer, in the company's words ───────────────────────────────────────────────────

/** THE RULE THIS TABLE EXISTS FOR, and it generalises past this screen: a human reads a STATE and
 *  the next move, never a directory.
 *
 *  The card used to render `✓ README.md ✓ PRINCIPLES.md ✓ OBJECTIVES.md ○ STRUCTURE.md ○ MISSING.md`
 *  and the founder's verdict on it was "this does not seem to me like a clear state". He is right,
 *  and the reason is not that the list was ugly: five ticks against five filenames tells the reader
 *  what a directory listing would tell them, and leaves them to work out what any of it means, what
 *  happens next, and why it matters. It is our data model shown to somebody who never asked for it.
 *
 *  So every file gets two words of its own: a `phrase` — what that file ANSWERS, which is what goes
 *  in the sentences — and a `name`, the one-word handle for the file being written right now. The
 *  filenames stay out of the card entirely; they are the right panel's tabs, where a filename is
 *  the correct label because the reader is looking at the file. */
const LAYER_WORDS: Record<string, { name: string; phrase: string }> = {
  "README.md": { name: "identity", phrase: "who you are" },
  "PRINCIPLES.md": { name: "principles", phrase: "how you work" },
  "OBJECTIVES.md": { name: "objectives", phrase: "what you are working toward" },
  "STRUCTURE.md": { name: "structure", phrase: "who does what and who can see what" },
  "MISSING.md": { name: "what is missing", phrase: "what is not yet known" },
};

/** A file the SERVER counts and this client has no words for. It cannot be dropped — the count
 *  would then disagree with the server's own gate, which is the one number the admin is watching —
 *  so it degrades to its stem rather than disappearing. A slightly wrong word beats a silent
 *  miscount. */
function words(file: string): { name: string; phrase: string } {
  const known = LAYER_WORDS[file];
  if (known) return known;
  const stem = file.replace(/\.md$/i, "").replace(/[_-]+/g, " ").toLowerCase();
  return { name: stem, phrase: stem };
}

const cap = (s: string) => (s ? s[0].toUpperCase() + s.slice(1) : s);

/** Canonical order first (so the sentence does not reshuffle between polls under the admin's eyes),
 *  then anything the server named that we did not expect, in the order it named it. */
function ordered(files: string[]): string[] {
  const set = new Set(files);
  const out = COMPANY_LAYER_FILES.filter((f) => set.has(f)) as string[];
  for (const f of files) if (!out.includes(f)) out.push(f);
  return out;
}

export type LayerStatus = {
  /** Sentence 1 — where we are. */
  where: string;
  /** Sentence 2 — what is next and why. Empty only while nothing has been read yet. */
  next: string;
  written: number;
  total: number;
};

/** The card's two sentences, DERIVED from the server's `present[]` / `missing_files[]`.
 *
 *  Nothing here is hardcoded to five: the total is whatever the server counts, so a company layer
 *  that grows a sixth file changes this screen without anybody editing it. A hardcoded count is a
 *  second opinion on the gate, and it would be wrong silently — the number would still look right.
 *
 *  `accepted` is the VERIFIER's answer (`global_setup === "completed"`), which is not the same fact
 *  as "every file exists": `mark_global_ready` re-reads the files and commits them, and until it has
 *  accepted, a complete-looking directory is still a layer nobody has agreed to. */
export function companyLayerStatus(
  state: Pick<GlobalState, "present" | "missing_files"> | null,
  accepted = false,
): LayerStatus {
  if (!state) return { where: "Reading this instance…", next: "", written: 0, total: 0 };

  const present = ordered(state.present);
  const missing = ordered(state.missing_files);
  const total = present.length + missing.length;
  const writtenPhrases = present.map((f) => words(f).phrase);
  const missingPhrases = missing.map((f) => words(f).phrase);

  const where = total === 0
    ? "Company layer: nothing to write."
    : writtenPhrases.length === 0
      ? `Company layer: 0 of ${total}. Nothing written yet.`
      : `Company layer: ${writtenPhrases.length} of ${total}. ${cap(writtenPhrases.join(", "))}: written.`;

  if (missing.length === 0) {
    return {
      where,
      next: accepted
        ? "Other people can sign in, and mails start going out."
        : "Everything is written. The agent checks it itself and opens the instance once you agree it is right.",
      written: writtenPhrases.length,
      total,
    };
  }

  const head = words(missing[0]);
  const rest = missingPhrases.slice(1);
  // "both" is only true of two, and reading "when both are written" against four remaining files is
  // the kind of small lie that makes a reader stop trusting the whole card.
  const when = missing.length === 1 ? "When that is written"
    : missing.length === 2 ? "When both are written"
      : "When all of them are written";
  const next = [
    `Now: ${head.name} — ${head.phrase}.`,
    rest.length ? `Then: ${rest.join(", ")}.` : "",
    `${when} the instance opens: other people can sign in, and mails start going out.`,
  ].filter(Boolean).join(" ");

  return { where, next, written: writtenPhrases.length, total };
}

// ── polling ─────────────────────────────────────────────────────────────────────────────────────

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

/** The two things that used to be steps 1 and 2, reduced to the only question the card has to
 *  answer about them: is either one actually unset? Probed ONCE (the same /api/{models,
 *  transcription}/test edges the steps used and Settings → Models still uses), and rendered only
 *  when the answer is yes — a deployment whose env already carries a working model and a working
 *  backend must not be told to go and configure them. */
function useUnconfigured(): string[] {
  const [gaps, setGaps] = useState<string[]>([]);
  useEffect(() => {
    let live = true;
    void Promise.all([
      testModels().then((r) => r.ok).catch(() => true),          // a probe that cannot run is not a gap
      testTranscription().then((r) => r.ok).catch(() => true),
    ]).then(([models, stt]) => {
      if (!live) return;
      const out: string[] = [];
      if (!models) out.push("the agent model");
      if (!stt) out.push("transcription");
      setGaps(out);
    });
    return () => { live = false; };
  }, []);
  return gaps;
}

// ── the corner card ─────────────────────────────────────────────────────────────────────────────

/** The card the admin talks past while the conversation underneath it does the work.
 *
 *  It says three things and stops: where we are, what is next and why, and — only once the verifier
 *  is satisfied — the one action, with what it does written beside it. The server's `reasons[]` are
 *  still rendered, but only where they are an ANSWER: after the admin asked to open the instance and
 *  the verifier refused. As a permanent block they were a wall of text restating the file list in
 *  longer form, and a reader learns to skip a paragraph that is always there. */
function GateCard({ onContinue }: { onContinue: () => void }) {
  const [done, setDone] = useState(false);
  const { state, error } = useGlobalState(done);
  const gaps = useUnconfigured();
  const complete = state?.global_setup === "completed";
  const status = companyLayerStatus(state, complete);

  // Set only when the admin ASKED to open and the verifier said no — see the comment above.
  const [refusal, setRefusal] = useState<string[] | null>(null);
  const [opening, setOpening] = useState(false);

  // Stop polling the moment the answer is yes — the card is now waiting on the human, not the server.
  useEffect(() => { if (complete) setDone(true); }, [complete]);

  /** RE-ASK before opening. The card's Continue used to fire off a stale poll's verdict; between the
   *  poll and the click the layer can have been edited, and opening an instance on a verdict nobody
   *  re-checked is the one irreversible thing on this screen. If the server now refuses, its own
   *  sentences are what the admin reads — we do not paraphrase a refusal we did not author. */
  const open = async () => {
    setOpening(true);
    setRefusal(null);
    try {
      const fresh = await getGlobalState();
      if (fresh.global_setup === "completed") { onContinue(); return; }
      setRefusal(fresh.reasons.length ? fresh.reasons : [fresh.gate_sentence || "The company layer is not complete yet."]);
      setDone(false); // it moved back under us — start watching again
    } catch (e: unknown) {
      setRefusal([e instanceof Error ? e.message : String(e)]);
    } finally {
      setOpening(false);
    }
  };

  return (
    <div
      data-testid="global-gate-card"
      style={{
        position: "fixed", right: 16, bottom: 16, width: 320, zIndex: 40,
        background: "var(--panel)", border: "1px solid var(--line2)", borderRadius: 10,
        padding: "14px 15px", display: "flex", flexDirection: "column", gap: 9,
        boxShadow: "0 8px 32px rgba(0,0,0,.34)",
      }}
    >
      <span style={label}>{state?.company && complete ? state.company : "Company layer"}</span>

      {/* 1 — where we are */}
      <div style={{ fontSize: 12.5, color: "var(--t1)", lineHeight: 1.5 }}>{status.where}</div>

      {/* 2 — what is next, and why it matters */}
      {status.next && <div style={{ fontSize: 11.5, color: "var(--t2)", lineHeight: 1.55 }}>{status.next}</div>}

      {/* 3 — the one action, and only once the verifier is satisfied. Until then the card is a
             readout: it says who is doing the work rather than offering a button that would refuse. */}
      {complete ? (
        <button style={{ ...primaryBtn, padding: "8px 14px", opacity: opening ? 0.6 : 1 }} disabled={opening}
          onClick={() => void open()}>
          {opening ? "Opening…" : "Open this Vexa"}
        </button>
      ) : (
        <div style={{ fontSize: 11, color: "var(--t3)", lineHeight: 1.45 }}>the agent is writing this with you</div>
      )}

      {refusal && (
        <div role="alert" style={{ display: "flex", flexDirection: "column", gap: 3 }}>
          {refusal.map((r) => (
            <div key={r} style={{ fontSize: 11, color: "var(--danger)", lineHeight: 1.45 }}>{r}</div>
          ))}
        </div>
      )}

      {/* What used to be steps 1 and 2. One line, last, and only when something really is unset. */}
      {gaps.length > 0 && (
        <div style={{ fontSize: 10.5, color: "var(--t3)", lineHeight: 1.45, borderTop: "1px dashed var(--line2)", paddingTop: 8 }}>
          Not set yet: {gaps.join(" and ")}. Settings &rarr; Models, whenever you like &mdash; meetings need them, this conversation does not.
        </div>
      )}

      {error && !refusal && (
        <div style={{ fontSize: 10.5, color: "var(--t3)", lineHeight: 1.45 }}>
          Couldn&rsquo;t reach the instance just now &mdash; still checking.
        </div>
      )}
    </div>
  );
}

// ── the gate itself ─────────────────────────────────────────────────────────────────────────────

export function SetupGate({ children }: { children: React.ReactNode }) {
  const [phase, setPhase] = useState<Phase>("checking");
  // Set ONLY when the hand-off write failed. Its presence is what replaces the navigation.
  const [handOffError, setHandOffError] = useState<string | null>(null);
  const handingOff = useRef(false);

  useEffect(() => {
    let on = true;
    getGlobalSetting("setup")
      .then((v) => {
        if (!on) return;
        setPhase(setupResumePhase(v));
      })
      .catch(() => on && setPhase("hidden")); // fail-safe: never block the workbench on the probe
    return () => { on = false; };
  }, []);

  const finish = () => {
    void setGlobalSetting("setup", { completed: "true" }).catch(() => undefined);
    // The admin→user seam: opening the instance must actually LAND on Meetings. The workbench's
    // layout store initializes its rail from this persisted key (layout.ts LS_LIST) and it is
    // created only when the workbench mounts, so a plain localStorage write is the whole hand-off.
    //
    // ONE CAVEAT, since the button lives on a card floating OVER a live workbench: the workbench is
    // already mounted, so it has already read this key and the rail may not move. That is the better
    // behaviour anyway — yanking the view the admin was just working in would be worse — and the
    // write still does its job on the next load. The admin's own personal onboarding (which
    // OnboardingGate holds back while the gate is up) also fires on that next load, for the same
    // reason: it is a thing to walk into, not a thing to have thrown at you mid-sentence.
    try { localStorage.setItem("vexa.terminal.activeList.v1", "meetings"); } catch { /* noop */ }
    setPhase("hidden");
  };

  /** THE HAND-OFF. Persist that we are handing off, and only then navigate.
   *
   *  ⚠ WHAT THIS USED TO DO, AND WHY IT MATTERS (founder ruling 2026-09-02, ruling 3):
   *      void setGlobalSetting("setup", { global: HANDOFF }).catch(() => undefined)
   *        .finally(() => window.location.assign("/?setup=global"));
   *  The `.catch(() => undefined)` hid a live blocker for an hour. admin-api was silently dropping
   *  the write (the field was not in its allow-list) and answering 200, so the marker never
   *  persisted, the reload resumed here instead of in the conversation, and the button looked dead.
   *  The server bug is fixed; the swallow was the more dangerous half, because it is what turned a
   *  server fault into an unreadable UI.
   *
   *  NAVIGATING ANYWAY WAS THE TEMPTING WRONG ANSWER. It looks generous — the conversation is where
   *  the admin needs to be, so send them there and let the marker catch up. It is not generous: with
   *  no marker the next load resumes right back here, which is exactly the loop that was just fixed,
   *  and the admin has no way to see that anything failed. A failure that navigates is a failure
   *  nobody can report. So: no marker, no navigation — say so, and offer the retry. */
  const handOff = useCallback(async () => {
    if (handingOff.current) return;
    handingOff.current = true;
    setHandOffError(null);
    try {
      await setGlobalSetting("setup", { global: HANDOFF });
    } catch (e: unknown) {
      handingOff.current = false;
      setHandOffError(e instanceof Error ? e.message : String(e));
      return;
    }
    markHandoffTried();
    // App.tsx turns `?setup=global` into the pending preset the workbench opens on mount
    // (`_global/asks/setup-global.md`) and strips the query itself. A full navigation, not a phase
    // flip: the flip would never stash the preset, and the conversation would never open.
    window.location.assign("/?setup=global");
  }, []);

  /** The admin does not press anything to get here. There is no longer a screen in front of the
   *  company-setup conversation, so the moment we know the marker is absent we write it and go.
   *  Guarded twice: `handingOff` against StrictMode's double effect, and the session flag against a
   *  write that answers OK and does not stick (see HANDOFF_TRIED_KEY). */
  useEffect(() => {
    if (phase !== "opening" || handOffError) return;
    if (handoffTried()) {
      setHandOffError("This Vexa accepted the write and did not keep it.");
      return;
    }
    void handOff();
  }, [phase, handOffError, handOff]);

  if (phase === "checking") return <div style={{ height: "100vh", background: "var(--bg)" }} />;
  if (phase === "hidden") return <>{children}</>;
  // HANDED OFF: the workbench mounts and runs the setup conversation; this is now a card in the
  // corner watching the server for the verdict.
  if (phase === "card") return <>{children}<GateCard onContinue={finish} /></>;

  // "opening" — a moment, unless the write failed.
  return (
    <div style={{ height: "100vh", background: "var(--bg)", display: "flex", alignItems: "center", justifyContent: "center", padding: 20 }}>
      {handOffError ? (
        <div
          data-testid="handoff-failed"
          style={{ width: 400, maxWidth: "94vw", background: "var(--panel)", border: "1px solid var(--line2)",
            borderRadius: 12, padding: 24, display: "flex", flexDirection: "column", gap: 12, boxShadow: "0 8px 32px rgba(0,0,0,.3)" }}
        >
          <div style={{ fontSize: 15, fontWeight: 600, color: "var(--t1)" }}>Couldn&rsquo;t start setting up</div>
          <div style={{ fontSize: 12, color: "var(--t3)", lineHeight: 1.6 }}>
            This Vexa could not record that you are setting it up, so the company-setup conversation was
            not opened. Opening it anyway would have put you straight back on this screen with nothing to
            show for it.
          </div>
          <div role="alert" style={{ fontSize: 11.5, color: "var(--danger)", lineHeight: 1.45 }}>{handOffError}</div>
          <button style={primaryBtn} onClick={() => { setHandOffError(null); void handOff(); }}>Try again</button>
        </div>
      ) : (
        <div style={{ fontSize: 12, color: "var(--t3)" }}>Opening the company setup conversation&hellip;</div>
      )}
    </div>
  );
}
