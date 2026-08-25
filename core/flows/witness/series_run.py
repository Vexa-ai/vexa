#!/usr/bin/env python3
"""series_run — the scaffold-inference ITERATION HARNESS.

The alpha's acceptance test is the *daily-meeting smoke*: scaffold a desk fresh from episode 1,
ask only the load-bearing questions, then watch episodes 2 and 3 arrive and ask whether the
system actually inferred what is going on — the people, the projects, the vocabulary, the
running threads. This harness runs that loop OFFLINE, against REAL longitudinal meetings, so we
iterate before a single human meeting is spent on it.

    python3 witness/series_run.py list
    python3 witness/series_run.py reset  --series <slug>
    python3 witness/series_run.py run    --series <slug> --through 3
    python3 witness/series_run.py judge  --series <slug> --episode 1

WHAT IT IS
  Episodes are fixture meetings. Each one is admitted into the REAL flows engine as a
  `meeting.completed` fact carrying that episode's transcript — the same fact `post_meeting`
  reacts to in production, built the same way the existing fixture path builds it
  (`flows_steps/meeting.py: FIXTURE_LINES` → `emit_completed`). The engine, its receipts, its
  waits, its durable sqlite are all real; only the audio is a fixture.

THE AGENT SEAM (the honest double)
  The scaffolding/minutes phases are agent turns. This harness NEVER embeds a model. It probes
  agent-api: reachable → real turns through `flows_steps/agent.py`; not reachable → the phase is
  SKIPPED and says so, in the run report AND as a `*.SKIPPED.md` file in the workspace. It never
  fabricates product output. What it does compute offline is `episode-index.json` — transcript
  statistics (speakers, terms, what is new since the previous episode). That is harness
  bookkeeping for the judge, explicitly not an inference the product made.

WHERE THE SCAFFOLDING BEHAVIOR SLOTS IN
  The confidence-gated scaffolding of drafts/2026-08-25-desk-scaffolding-design.md does not exist
  in `flows_defs/production.py` yet — today production has `onboard_person` (an email interview)
  and `post_meeting` (gated minutes). So the harness defines its own flow, `series_episode`,
  whose step sequence is the shape the design calls for:

      require_desk → scaffold_desk → ask_questions → process_meeting → deliver_minutes

  `scaffold_desk` and `ask_questions` are the two steps the behavior has to fill. When it lands in
  production, `--flows production` runs the episodes through the production registry instead and
  this file's flow becomes the fallback. Nothing else about the harness changes.

NOT AUTO-SCORED. `judge` renders a side-by-side and a presence checklist. A human (or a later
agent) reads it. A number here would be a lie about what we can measure today.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
FLOWS = HERE.parent
sys.path.insert(0, str(FLOWS / "src"))

from flows import (  # noqa: E402
    Done, EventType, Registry, SqliteDB, StepCtx, SystemClock, Wait,
    admit, escalate, reclaim, status, tick,
)

SERIES_DIR = FLOWS / "tests" / "series"
STATE_DIR = HERE / "series_state"
COMPLETED = EventType("meeting.completed")


# ── fixtures ──────────────────────────────────────────────────────────────────
def say(msg: str) -> None:
    print(f"  {time.strftime('%H:%M:%S')} · {msg}", flush=True)


def load_manifest(slug: str) -> dict:
    f = SERIES_DIR / slug / "series.json"
    if not f.is_file():
        raise SystemExit(f"no such series: {slug} (looked for {f})\n"
                         f"available: {', '.join(available_series()) or '(none)'}")
    return json.loads(f.read_text())


def available_series() -> list[str]:
    if not SERIES_DIR.is_dir():
        return []
    return sorted(d.name for d in SERIES_DIR.iterdir() if (d / "series.json").is_file())


def load_episode(slug: str, ep: dict) -> list[dict]:
    """One episode's transcript in the fixture shape — the SAME columns the transcriptions table
    carries (start/end/speaker/text/language), so a segment here and a segment the collector
    wrote are interchangeable. `speaker: null` means the source captions carried no labels; we
    keep it null rather than invent a name."""
    f = SERIES_DIR / slug / ep["transcript"]
    if not f.is_file():
        raise SystemExit(f"missing transcript fixture: {f}")
    segs = []
    for i, line in enumerate(f.read_text().splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            segs.append(json.loads(line))
        except json.JSONDecodeError as e:
            raise SystemExit(f"{f}:{i + 1}: not JSON ({e})")
    return segs


def transcript_text(segs: list[dict]) -> str:
    """The exact rendering `emit_completed` puts on the fact: `speaker: text` per line.
    Unlabeled captions render as `?` — the same thing the real path does with a missing speaker."""
    return "\n".join(f"{s.get('speaker') or '?'}: {s.get('text', '')}" for s in segs)


# ── the agent seam ────────────────────────────────────────────────────────────
class SkippedAgent:
    """No model reachable. Every agent phase writes a marker naming exactly what did not run.
    This class is the reason the harness is useful with zero credentials: the non-agent phases
    (fact admission, engine, waits, receipts, fixture integrity, carryover bookkeeping) all still
    execute and prove themselves."""

    available = False

    def __init__(self, reason: str) -> None:
        self.reason = reason
        self.name = f"SKIPPED — {reason}"
        self.skipped: list[str] = []

    def _skip(self, ws: Path, phase: str, what: str) -> dict:
        self.skipped.append(phase)
        (ws / f"{phase}.SKIPPED.md").write_text(
            f"# {phase} — SKIPPED\n\n"
            f"**Not run because:** {self.reason}\n\n"
            f"**What would have run:** {what}\n\n"
            "This file is a placeholder written by the harness. It is NOT product output and "
            "contains no inference about the meeting.\n")
        return {"skipped": phase, "reason": self.reason}

    def scaffold(self, ws: Path, ep: dict, text: str) -> dict:
        return self._skip(ws, "scaffold_desk",
                          "an agent turn drafting the desk (people · projects · vocabulary · "
                          "running threads) from the episode transcript, every claim carrying "
                          "provenance + confidence.")

    def questions(self, ws: Path, ep: dict) -> dict:
        return self._skip(ws, "ask_questions",
                          "the confidence gate: the agent selects ONLY the load-bearing "
                          "low-confidence claims and asks about those; everything else ships as "
                          "a correctable assumption.")

    def minutes(self, ws: Path, ep: dict, text: str) -> dict:
        return self._skip(ws, "process_meeting",
                          "an agent turn writing the meeting note (Decided / Committed / Open) "
                          "into the desk and committing it.")


class RealAgent:
    """agent-api is answering: run the phases as REAL agent turns through the production adapter
    (`flows_steps/agent.py`), against the harness's throwaway subject id. Same file-outbox and
    workspace-file contracts production uses — no second implementation of the agent protocol."""

    name = "real (agent-api)"
    available = True

    def __init__(self, uid: str, turn_timeout_s: float) -> None:
        self.uid = uid
        self.turn_timeout_s = turn_timeout_s
        self.skipped: list[str] = []
        from flows_steps import agent as ag
        self._ag = ag
        ag.workspace_init(uid)

    def _turn(self, ws: Path, phase: str, session: str, prompt: str, timeout_s: float) -> dict:
        base = self._ag.dispatch_turn(self.uid, session, prompt)
        t0 = time.time()
        while time.time() - t0 < timeout_s:
            reply = self._ag.collect_reply(self.uid, session, base)
            if reply:
                (ws / f"{phase}.md").write_text(reply)
                return {"phase": phase, "chars": len(reply)}
            time.sleep(2.0)          # harness-side poll; the NO-SLEEP LAW governs STEPS, not this
        (ws / f"{phase}.TIMEOUT.md").write_text(
            f"# {phase} — TIMEOUT\n\nagent-api accepted the turn but produced no reply within "
            f"{timeout_s:.0f}s. Most likely: no model credential configured on the agent tier "
            "(see core/flows/HANDOFF.md § DEMO BLOCKER).\n")
        self.skipped.append(phase)
        return {"phase": phase, "timeout": True}

    def scaffold(self, ws: Path, ep: dict, text: str) -> dict:
        return self._turn(ws, "scaffold_desk", f"series-{ep['slug']}", SCAFFOLD_PROMPT.format(
            n=ep["n"], date=ep.get("date", "?"), title=ep.get("title", ""), transcript=text),
            self.turn_timeout_s)

    def questions(self, ws: Path, ep: dict) -> dict:
        return self._turn(ws, "ask_questions", f"series-{ep['slug']}", QUESTIONS_PROMPT,
                          self.turn_timeout_s / 2)

    def minutes(self, ws: Path, ep: dict, text: str) -> dict:
        return self._turn(ws, "process_meeting", f"series-{ep['slug']}-ep{ep['n']}",
                          MINUTES_PROMPT.format(n=ep["n"], date=ep.get("date", "?"),
                                                transcript=text), self.turn_timeout_s)


# Harness prompts, deliberately thin: the REAL scaffolding voice is behavior-domain
# (behavior/prompts/, private mount) and lands with the behavior. These exist so the loop is
# runnable the moment a credential appears — replace them, do not grow them here.
SCAFFOLD_PROMPT = (
    "[series-harness] Episode {n} ({date}) of a recurring meeting series: {title}.\n"
    "Scaffold the desk from THIS transcript alone — people, their roles, projects, vocabulary "
    "and acronyms, and the threads that are clearly running. Every claim carries a provenance "
    "quote and a confidence 0-100. Write desk/people.md, desk/projects.md, desk/vocabulary.md, "
    "desk/threads.md. If a later episode is processed, UPDATE these files rather than replacing "
    "them, and note what changed.\n\nTRANSCRIPT:\n{transcript}")
QUESTIONS_PROMPT = (
    "[series-harness] From the desk you just scaffolded, list ONLY the load-bearing "
    "low-confidence claims worth asking a human about. Everything else stays an assumption the "
    "human can correct by reply. Write the questions, one per line, and nothing else.")
MINUTES_PROMPT = (
    "[series-harness] Episode {n} ({date}) transcript follows. Write the meeting note: "
    "Decided / Committed / Open, each item attributed to its speaker, terse and faithful — record "
    "only what was said. Use the desk you scaffolded for names and vocabulary.\n\n"
    "TRANSCRIPT:\n{transcript}")


def make_seam(uid: str, mode: str, turn_timeout_s: float = 600.0) -> object:
    """`auto` probes agent-api and uses it when it answers. NOTE, because it cost this harness a
    two-minute hang on the first run: agent-api answering says NOTHING about a model credential
    existing behind it. A reachable-but-uncredentialed tier accepts the turn and never replies —
    that is the TIMEOUT path, and it is why the timeout is a flag."""
    if mode == "skip":
        return SkippedAgent("--agent skip was requested")
    from flows_steps.common import AGENT_API
    try:
        import urllib.request
        req = urllib.request.Request(f"{AGENT_API}/api/sessions/probe/history", method="GET")
        req.add_header("X-User-Id", uid)
        urllib.request.urlopen(req, timeout=4).read()
    except Exception as e:  # noqa: BLE001 — unreachable is the expected offline case
        if mode == "real":
            raise SystemExit(f"--agent real requested but {AGENT_API} did not answer: "
                             f"{type(e).__name__}: {e}")
        return SkippedAgent(f"agent-api at {AGENT_API} did not answer ({type(e).__name__})")
    try:
        return RealAgent(uid, turn_timeout_s)
    except Exception as e:  # noqa: BLE001
        return SkippedAgent(f"agent-api answered but workspace init failed ({type(e).__name__}: {e})")


# ── harness bookkeeping (deterministic, NOT product inference) ────────────────
_WORD = re.compile(r"[A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß\-']{2,}")
_SENT_END = re.compile(r"[.!?]\s*$")
# Capitalised words that are grammar or filler, never vocabulary — they survive the
# mid-sentence test (contractions, interjections, German pronoun "Sie") so they are named here.
_NOT_JARGON = {"I'm", "I've", "I'll", "I'd", "And", "But", "So", "Yeah", "Yes", "No", "That",
               "This", "The", "We", "It", "If", "Well", "Okay", "OK", "Oh", "Um", "Uh", "Right",
               "Ja", "Nein", "Sie", "Und", "Aber", "Das", "Der", "Die", "Herr", "Frau", "Also"}


def _salient(segs: list[dict]) -> list[str]:
    """The vocabulary a stranger would have to learn to follow this meeting: proper nouns,
    acronyms and CamelCase jargon, counted ONLY where they appear mid-sentence — a capitalised
    word that only ever starts a sentence is grammar, not jargon. Purely mechanical."""
    counts: dict[str, int] = {}
    for s in segs:
        prev_end = True                                    # segment start counts as sentence start
        for tok in re.split(r"(\s+)", s.get("text", "")):
            if not tok.strip():
                continue
            w = _WORD.match(tok)
            sentence_start = prev_end
            prev_end = bool(_SENT_END.search(tok))
            if not w:
                continue
            word = w.group(0)
            jargon = word.isupper() or (word[1:] != word[1:].lower())   # ACRONYM or CamelCase
            if not (word[0].isupper() or jargon):
                continue
            if word in _NOT_JARGON:
                continue
            if sentence_start and not jargon:
                continue                                   # capitalised only by position
            counts[word] = counts.get(word, 0) + 1
    return [t for t, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])) if n >= 3][:40]


def episode_index(segs: list[dict], prior: dict | None) -> dict:
    """Transcript STATISTICS — speakers, coverage, salient vocabulary, and what is new since the
    previous episode. This is the harness counting words, not the product understanding a
    meeting. `judge` prints it under that label so nobody mistakes one for the other."""
    speakers = sorted({s["speaker"] for s in segs if s.get("speaker")})
    terms = _salient(segs)
    prior_sp = set((prior or {}).get("speakers", []))
    prior_tm = set((prior or {}).get("top_terms", []))
    return {
        "segments": len(segs),
        "speaker_labels": bool(speakers),
        "speakers": speakers,
        "duration_s": round(max((s.get("end", 0) or 0) for s in segs), 1) if segs else 0.0,
        "top_terms": terms,
        "new_speakers": sorted(set(speakers) - prior_sp) if prior else speakers,
        "new_terms": sorted(set(terms) - prior_tm) if prior else terms,
        "carried_terms": sorted(set(terms) & prior_tm) if prior else [],
        "_note": "harness bookkeeping: word/speaker statistics over the fixture. NOT an inference "
                 "made by the product.",
    }


# ── the flow ──────────────────────────────────────────────────────────────────
def build_series_flow(reg: Registry, seam, ws: Path, log: dict) -> None:
    """`series_episode` — the shape drafts/2026-08-25-desk-scaffolding-design.md calls for, run on
    the real engine. Mirrors production's `post_meeting` and inserts the two steps the scaffolding
    behavior has to fill. Steps never sleep: every wait is a Wait (the live-witness law)."""

    @reg.step
    def require_desk(ctx: StepCtx):
        """The desk exists (episode 1 creates it fresh). Production's twin blocks minutes behind
        `.scaffolded` and nudges by email; here the desk is a directory, so readiness is
        structural and the gate is a receipt, not a wait."""
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "desk").mkdir(exist_ok=True)
        return Done({"desk": str(ws), "fresh": not any((ws / "desk").iterdir())})

    @reg.step
    def scaffold_desk(ctx: StepCtx):
        """THE BEHAVIOR SLOT — confidence-gated scaffolding (design §2): draft the desk from the
        meetings we already hold, every claim with provenance + confidence. Agent phase."""
        return Done(seam.scaffold(ws, ctx.refs["episode"], ctx.refs["transcript"]))

    @reg.step
    def ask_questions(ctx: StepCtx):
        """THE BEHAVIOR SLOT — the confidence gate (design §2): ask ONLY about load-bearing
        low-confidence items; state the rest as correctable assumptions. Never blocks minutes."""
        return Done(seam.questions(ws, ctx.refs["episode"]))

    @reg.step
    def process_meeting(ctx: StepCtx):
        """The minutes turn — production's `process_meeting` without the git/commit detection
        (the harness workspace is a plain directory, so completion is the reply itself)."""
        return Done(seam.minutes(ws, ctx.refs["episode"], ctx.refs["transcript"]))

    @reg.step
    def deliver_minutes(ctx: StepCtx):
        """UI-LESS LAW: the note travels VERBATIM in the body. Offline the 'send' is a file under
        the episode's artifact directory — a double for SMTP, never a real send."""
        ep = ctx.refs["episode"]
        note = ws / "process_meeting.md"
        body = note.read_text() if note.is_file() else "(no minutes — the agent phase was skipped)"
        out = ws / f"minutes-ep{ep['n']}.txt"
        out.write_text(f"To: {ctx.refs.get('organizer', 'organizer@example.org')}\n"
                       f"Subject: Minutes: {ep.get('title', '')}\n\n{body}\n")
        log.setdefault("delivered", []).append(str(out))
        return Done({"artifact": str(out)}, provider_ref=f"minutes-ep{ep['n']}")

    reg.flow(name="series_episode", version=1, on=COMPLETED,
             steps=[require_desk, scaffold_desk, ask_questions, process_meeting, deliver_minutes])


def build_production_flows(reg: Registry, db) -> None:
    """The slot for when the scaffolding behavior lands in `flows_defs/production.py`: run the
    episodes through the PRODUCTION registry instead of this file's flow. Imports lazily and
    reports honestly, because production's steps talk to admin-api/agent-api/gateway over HTTP —
    they are not runnable on a laptop with no stack up."""
    from flows_defs import production
    production.build(reg, db)


# ── verbs ─────────────────────────────────────────────────────────────────────
def state_of(slug: str) -> Path:
    return STATE_DIR / slug


def cmd_list(args) -> int:
    slugs = available_series()
    if not slugs:
        print(f"no series fixtures under {SERIES_DIR}")
        return 1
    for s in slugs:
        m = load_manifest(s)
        st = state_of(s)
        ran = sorted(int(p.name[2:]) for p in (st / "episodes").glob("ep*")) if (st / "episodes").is_dir() else []
        print(f"  {s:<28} {m.get('language', '?'):<3} {len(m['episodes'])} episodes  "
              f"{m.get('title', '')}")
        print(f"  {'':<28} state: {'ran ' + ','.join(map(str, ran)) if ran else 'clean'}")
    return 0


def cmd_reset(args) -> int:
    targets = [args.series] if args.series else available_series()
    for s in targets:
        d = state_of(s)
        if d.exists():
            shutil.rmtree(d)
            print(f"  wiped {d}")
        else:
            print(f"  {s}: already clean")
    print("\n'scaffold fresh from episode 1' is now honest for: " + ", ".join(targets))
    return 0


def cmd_run(args) -> int:
    m = load_manifest(args.series)
    eps = m["episodes"]
    through = args.through or len(eps)
    if through > len(eps):
        raise SystemExit(f"--through {through} but {args.series} has {len(eps)} episodes")

    st = state_of(args.series)
    st.mkdir(parents=True, exist_ok=True)
    uid = m.get("harness_uid", "series-harness")
    seam = make_seam(uid, args.agent, args.agent_timeout)

    print(f"── series_run · {args.series} · episodes 1..{through} ──")
    say(f"agent seam: {seam.name}")
    say(f"state: {st}  (durable sqlite — `reset` is the only way back to a fresh desk)")

    db = SqliteDB(str(st / "flows.db"))
    clock = SystemClock()
    prior_index: dict | None = None
    idx_file = st / "index.json"
    if idx_file.is_file():
        prior_index = json.loads(idx_file.read_text()).get("last")

    report: dict = {"series": args.series, "agent_seam": seam.name, "episodes": []}

    for ep in eps[:through]:
        ep = {**ep, "slug": args.series}
        segs = load_episode(args.series, ep)
        text = transcript_text(segs)
        ws = st / "episodes" / f"ep{ep['n']}"
        ws.mkdir(parents=True, exist_ok=True)
        # the desk is CUMULATIVE across episodes — episode N sees what N-1 left behind
        desk = st / "desk"
        desk.mkdir(exist_ok=True)
        if (ws / "desk").is_symlink() or (ws / "desk").exists():
            pass
        else:
            try:
                (ws / "desk").symlink_to(desk, target_is_directory=True)
            except OSError:
                (ws / "desk").mkdir(exist_ok=True)

        say(f"episode {ep['n']} ({ep.get('date', '?')}): {len(segs)} segments, "
            f"{len(text)} chars — {ep.get('title', '')[:60]}")

        idx = episode_index(segs, prior_index)
        (ws / "episode-index.json").write_text(json.dumps(idx, indent=2, ensure_ascii=False))
        say(f"  index: {len(idx['speakers'])} speaker labels · {idx['duration_s'] / 60:.0f} min · "
            f"{len(idx['new_terms'])} terms new since previous episode")

        reg = Registry()
        log: dict = {}
        if args.flows == "production":
            build_production_flows(reg, db)
        else:
            build_series_flow(reg, seam, ws, log)

        n = admit(db, reg, clock,
                  source_event_id=f"{args.series}-ep{ep['n']}",
                  event_type=COMPLETED.name,
                  subject_refs={"episode": ep, "transcript": text, "uid": uid,
                                "organizer": m.get("organizer", "organizer@example.org"),
                                "meeting_id": f"{args.series}-{ep['n']}",
                                "native": f"{args.series}-{ep['n']}",
                                "title": ep.get("title", "")})
        say(f"  FACT admitted: meeting.completed ({n} reaction{'s' if n != 1 else ''}) — "
            "the same fact production's post_meeting reacts to")

        deadline = time.time() + args.timeout
        while time.time() < deadline:
            reclaim(db, clock)
            escalate(db, clock)
            if tick(db, reg, clock):
                continue
            rows = db.execute(
                "SELECT status FROM reaction WHERE source_event_id LIKE :s",
                {"s": f"{args.series}-ep{ep['n']}%"})   # admission appends ::<flow> per matching flow
            if all(r[0] in ("done", "failed", "cancelled") for r in rows):
                break
            time.sleep(0.5)

        rows = db.execute("SELECT reaction_id FROM reaction WHERE source_event_id LIKE :s",
                          {"s": f"{args.series}-ep{ep['n']}%"})
        eps_report = {"n": ep["n"], "date": ep.get("date"), "segments": len(segs),
                      "index": {k: idx[k] for k in ("speakers", "duration_s", "new_terms")},
                      "reactions": []}
        for (rid,) in rows:
            s = status(db, rid)
            eps_report["reactions"].append({"status": s["status"],
                                            "receipts": [(r["step"], r["state"]) for r in s["receipts"]]})
            print(f"\n  receipts · reaction {rid[:8]} → {s['status']}")
            for r in s["receipts"]:
                print(f"    {r['state']:<10} {r['step']:<18} {r.get('provider_ref') or ''}")
        report["episodes"].append(eps_report)
        prior_index = idx
        print()

    idx_file.write_text(json.dumps({"last": prior_index}, indent=2, ensure_ascii=False))
    (st / "run-report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))

    skipped = sorted(set(getattr(seam, "skipped", [])))
    print("── what ran ──")
    print(f"  ran   : fact admission · engine (leases, receipts, waits) · fixture load · "
          f"episode index · minutes delivery (file double)")
    if skipped:
        print(f"  SKIPPED: {', '.join(skipped)} — {getattr(seam, 'reason', 'agent phase unavailable')}")
        print("           these are the AGENT phases; every other phase above executed for real.")
    else:
        print("  SKIPPED: nothing — the agent phases ran through agent-api.")
    print(f"\n  artifacts: {st}/episodes/ep<N>/   ·   run report: {st}/run-report.json")
    print(f"  next: python3 witness/series_run.py judge --series {args.series} --episode 1")
    return 0


def cmd_judge(args) -> int:
    m = load_manifest(args.series)
    ep = next((e for e in m["episodes"] if e["n"] == args.episode), None)
    if ep is None:
        raise SystemExit(f"{args.series} has no episode {args.episode}")
    gt_path = SERIES_DIR / args.series / "ground-truth" / f"ep{args.episode}.md"
    ws = state_of(args.series) / "episodes" / f"ep{args.episode}"
    if not ws.is_dir():
        raise SystemExit(f"episode {args.episode} has not been run — "
                         f"`run --series {args.series} --through {args.episode}` first")

    bar = "═" * 78
    print(bar)
    print(f"  JUDGE · {args.series} · episode {args.episode} ({ep.get('date', '?')})")
    print(f"  {ep.get('title', '')}")
    print(f"  video: {ep.get('video_url', '—')}")
    print(f"  organizer notes: {ep.get('notes_url', '— none published')}")
    print(bar)

    print("\n┌─ GROUND TRUTH (organizer-published, distilled — see the series README) " + "─" * 5)
    gt = gt_path.read_text() if gt_path.is_file() else "(no ground-truth file for this episode)"
    for line in gt.splitlines():
        print("│ " + line)

    print("\n├─ WHAT WE PRODUCED " + "─" * 57)
    produced: list[str] = []
    for name, label in (("scaffold_desk.md", "SCAFFOLD (desk drafted from this episode)"),
                        ("ask_questions.md", "QUESTIONS (the confidence gate asked these)"),
                        ("process_meeting.md", "MINUTES")):
        f = ws / name
        skip = ws / (name.replace(".md", ".SKIPPED.md"))
        timeout = ws / (name.replace(".md", ".TIMEOUT.md"))
        print(f"│\n│ ── {label} ──")
        if f.is_file():
            produced.append(f.read_text())
            for line in f.read_text().splitlines():
                print("│ " + line)
        elif timeout.is_file():
            print("│ (TIMEOUT — agent-api accepted the turn, no reply; likely no model credential)")
        elif skip.is_file():
            reason = next((l for l in skip.read_text().splitlines() if l.startswith("**Not run")), "")
            print(f"│ (SKIPPED) {reason}")
        else:
            print("│ (nothing — this phase did not run)")

    print("\n├─ HARNESS BOOKKEEPING (word statistics — NOT an inference the product made) " + "─" * 1)
    idx = json.loads((ws / "episode-index.json").read_text()) if (ws / "episode-index.json").is_file() else {}
    if idx:
        print(f"│ segments {idx['segments']} · {idx['duration_s'] / 60:.0f} min · "
              f"speaker labels: {'yes — ' + ', '.join(idx['speakers']) if idx['speakers'] else 'NONE (captions carry no speakers)'}")
        print(f"│ salient terms this episode : {', '.join(idx['top_terms'][:20])}")
        if m.get("language", "").startswith("de"):
            print("│ (German capitalises every noun, so this list is noisier here than in English — "
                  "it is a word count, and it was never the measurement anyway)")
        print(f"│ new since previous episode : {', '.join(idx['new_terms'][:20]) or '(none)'}")
        print(f"│ carried from previous      : {', '.join(idx['carried_terms'][:20]) or '(n/a — first episode)'}")

    print("\n├─ PRESENCE CHECK · does our output mention what the notes say mattered? " + "─" * 3)
    print("│ NOT A SCORE. Substring presence only — a pointer at where to look, nothing more.")
    entities = _gt_entities(gt)
    blob = "\n".join(produced).lower()
    if not entities:
        print("│ (the ground-truth file declares no `## Entities` list — add one to enable this)")
    elif not produced:
        print("│ (nothing was produced to check against — the agent phases were skipped)")
    else:
        for e in entities:
            print(f"│  {'✓' if e.lower() in blob else '✗'}  {e}")

    print("\n└─ THE QUESTION FOR THE HUMAN " + "─" * 47)
    print("   After this episode, does the desk describe what is actually going on — the people,")
    print("   the projects, the vocabulary, the running threads? What did it invent? What did it")
    print("   miss that the organizer's notes make obvious? What should it have ASKED?")
    print(f"\n   raw artifacts: {ws}")
    return 0


def _gt_entities(gt: str) -> list[str]:
    """Ground-truth files may declare `## Entities` followed by a bullet list — the things a
    correct scaffold has to know about. Everything else in the file is prose for the human."""
    out, on = [], False
    for line in gt.splitlines():
        if line.startswith("## "):
            on = line.strip().lower().startswith("## entities")
            continue
        if on and line.strip().startswith(("-", "*")):
            out.append(line.strip().lstrip("-* ").split("—")[0].strip())
    return [e for e in out if e]


def main() -> int:
    ap = argparse.ArgumentParser(prog="series_run", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="the fixture library and what has been run")

    r = sub.add_parser("run", help="play episodes 1..N through the flows stack")
    r.add_argument("--series", required=True)
    r.add_argument("--through", type=int, default=None, help="last episode (default: all)")
    r.add_argument("--agent", choices=["auto", "real", "skip"], default="auto",
                   help="auto = use agent-api if it answers, else skip and say so")
    r.add_argument("--flows", choices=["series", "production"], default="series",
                   help="'production' runs the episodes through flows_defs/production.py — the "
                        "slot for when the scaffolding behavior lands there")
    r.add_argument("--timeout", type=float, default=900.0, help="per-episode wall clock")
    r.add_argument("--agent-timeout", type=float, default=600.0,
                   help="how long one agent turn may take before it is recorded as TIMEOUT")

    j = sub.add_parser("judge", help="side-by-side: our claims vs the organizer's notes")
    j.add_argument("--series", required=True)
    j.add_argument("--episode", type=int, required=True)

    x = sub.add_parser("reset", help="wipe throwaway state so 'scaffold fresh' is honest")
    x.add_argument("--series", default=None, help="default: every series")

    args = ap.parse_args()
    return {"list": cmd_list, "run": cmd_run, "judge": cmd_judge, "reset": cmd_reset}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
