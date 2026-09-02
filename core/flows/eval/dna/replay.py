#!/usr/bin/env python
"""Replay a fixture library through the real flows, as a real user, in ONE workspace.

Calendar order, so knowledge compounds the way it does for a person. Everything the replay does to
the engine goes through the MCP (audit rule 1) except the two primed openings, which are chat turns
and go where a chat turn goes -- agent-api ``/api/chat``, exactly as ``flows_steps/agent.py`` does.

    python replay.py --fixtures ~/dna-fixtures --rev 1 --uid 68

Writes every artifact to ``--run-root/r<rev>/``: the mails, the note, the opening turns, the
reaction rows, the model proof, and ``replay.json`` -- the input ``score.py`` reads.
"""
from __future__ import annotations

import argparse
import calendar
import json
import os
import pathlib
import re
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rig import Rig                                              # noqa: E402

AGENT_API = os.environ.get("VEXA_DNA_AGENT_API", "http://127.0.0.1:18500")
MAILPIT = os.environ.get("VEXA_DNA_MAILPIT", "http://127.0.0.1:8025")


# ── tiny http ────────────────────────────────────────────────────────────────────────────────────

def http(method: str, url: str, headers: dict | None = None, body=None, timeout=30):
    req = urllib.request.Request(url, method=method,
                                 data=json.dumps(body).encode() if body is not None else None)
    for k, v in {"content-type": "application/json", **(headers or {})}.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw.strip().startswith(("{", "[")) else raw)
    except urllib.error.HTTPError as e:
        # A 4xx here CARRIES the answer: POST /events replies 400 with the list of event types a
        # flow actually reacts to. Collapsing that to code 0 and a class name threw away the one
        # thing that tells the caller what to send instead.
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except Exception:                                         # noqa: BLE001
            return e.code, {"body": raw[:400]}
    except Exception as e:                                        # noqa: BLE001
        return 0, {"error": f"{type(e).__name__}: {e}"}


def history(uid: str, session: str) -> list:
    _, h = http("GET", f"{AGENT_API}/api/sessions/{session}/history", {"X-User-Id": uid})
    turns = h.get("turns", []) if isinstance(h, dict) else []
    return turns if isinstance(turns, list) else []


def worker_alive(uid: str, session: str) -> bool:
    """Is this session's worker container still up? The runtime names it after the unit."""
    names = subprocess.run(["docker", "ps", "--format", "{{.Names}}"],
                           capture_output=True, text=True).stdout.split()
    return f"vexa-worker-{uid}-chat-{session}" in names


def chat_turn(uid: str, session: str, prompt: str, budget_s: int = 420) -> str | None:
    """Dispatch one turn and wait for the agent's reply. ``/api/chat`` is an SSE stream that stays
    open for the whole turn, so a client timeout while it runs is SUCCESS -- the same lesson
    ``flows_steps/agent.dispatch_turn`` carries. Completion is read from the session history.

    IT ALSO STOPS WHEN THE TURN IS OVER, which is not the same as waiting for the budget. A turn
    that ends having written nothing looked identical here to a turn still thinking, so a fixture
    whose agent gave up in thirty seconds still cost seven minutes -- twice, once per opening.
    Combined with the same mistake in ``process_meeting``, one stalled fixture burned 34 minutes.
    The worker container is the signal: the runtime reaps it when the unit goes idle, so once it
    has been seen and is then gone with no new turn in the history, waiting longer cannot help."""
    base = len(history(uid, session))
    http("POST", f"{AGENT_API}/api/chat", {"X-User-Id": uid},
         {"prompt": prompt, "session": session}, timeout=3)
    deadline, seen_worker = time.time() + budget_s, False
    while time.time() < deadline:
        h = history(uid, session)
        if len(h) > base and h[-1].get("role") == "agent" and h[-1].get("text"):
            return h[-1]["text"].strip()
        alive = worker_alive(uid, session)
        if alive:
            seen_worker = True
        elif seen_worker:
            print(f"  [{session}] the worker exited with no reply — not waiting out the budget",
                  flush=True)
            return None
        time.sleep(6)
    return None


def mail_search(to: str, subject: str, since: float) -> dict | None:
    """Find ONE message by recipient and exact subject, through mailpit's search.

    This used to page the newest 60 messages and filter them. That works only while this replay is
    the only thing sending mail on the host, and it is not: with the adoption simulator running
    beside it, the prepare mail this fixture was waiting for sat at position 121 and the harness
    recorded `prepare_mail=False` for a mail the product had sent correctly two minutes earlier.
    A neighbour's traffic is not a product failure, and a window big enough today is a window that
    silently shrinks tomorrow. Search is O(1) in the neighbour's volume; the window was not."""
    import urllib.parse
    q = urllib.parse.quote(f'to:"{to}" subject:"{subject}"')
    _, d = http("GET", f"{MAILPIT}/api/v1/search?query={q}&limit=25")
    for m in (d.get("messages", []) if isinstance(d, dict) else []):
        if m.get("Subject") != subject:
            continue
        if to not in [t.get("Address", "") for t in m.get("To", [])]:
            continue
        _, full = http("GET", f"{MAILPIT}/api/v1/message/{m['ID']}")
        body = (full.get("Text") or full.get("HTML") or "") if isinstance(full, dict) else ""
        return {"id": m["ID"], "created": m.get("Created", ""), "subject": m.get("Subject", ""),
                "body": body}
    return None


FLOWS_API = os.environ.get("VEXA_DNA_FLOWS_API", "http://127.0.0.1:18200")
FLOWS_KEY_FILE = pathlib.Path.home() / ".storm/flows-api-key"


def emit_fact(event_type: str, source_event_id: str, refs: dict, actor: str) -> dict:
    """Admit ONE fact through the flows engine's own intake.

    This used to go through the rig's ``fact_emit``, which was guarded by authentication alone —
    any signed-in user could inject a fact naming any organizer. That verb is operator-only now,
    so the harness uses the server-side intake directly: the SAME ``admit()``, the same
    per-(fact, flow) dedup on ``source_event_id``, and a 400 listing the reactable types if the
    event names none — a better failure than the rig gave, because a fact accepted into silence
    looks exactly like one that worked.

    The operator key is READ from where ``flows-up.sh`` exports it, never hard-coded and never
    logged. ``X-Actor`` names this harness so the trail does not read as a person."""
    key = FLOWS_KEY_FILE.read_text().strip() if FLOWS_KEY_FILE.exists() else ""
    if not key:
        return {"_error": f"no operator key at {FLOWS_KEY_FILE} — flows-up.sh exports it there"}
    code, body = http("POST", f"{FLOWS_API}/events",
                      {"X-Flows-Admin-Key": key, "X-Actor": actor},
                      {"event_type": event_type, "source_event_id": source_event_id,
                       "refs": refs})
    if code != 202:
        return {"_error": f"POST /events {code}", "detail": str(body)[:300]}
    return body if isinstance(body, dict) else {"body": str(body)[:200]}


# ── presets ──────────────────────────────────────────────────────────────────────────────────────

FM = re.compile(r"^---\n([\s\S]*?)\n---\n?")


def _unwrap_text(v) -> str:
    """Rig tools answer in several shapes: a bare string, {content}, or {status, result:{...}}."""
    for _ in range(3):
        if isinstance(v, str):
            return v
        if not isinstance(v, dict):
            return ""
        v = v.get("content") or v.get("text") or v.get("result") or ""
    return v if isinstance(v, str) else ""


def preset_prompt(rig: Rig, name: str, meeting_id) -> str | None:
    """The admin-owned opening, resolved and substituted the way the terminal resolves it
    (``MinutesShell.tsx``): read ``_global/asks/<name>.md``, strip frontmatter, substitute
    ``{{meeting}}`` with the meeting ref. The URL never carries prompt text; neither does this."""
    body = rig.call("workspace_read", path=f"asks/{name}.md", slug="_global")
    body = _unwrap_text(body)
    if not isinstance(body, str) or not body.strip():
        return None
    m = FM.match(body)
    text = body[m.end():] if m else body
    return (text.replace("{{meeting}}", str(meeting_id))
                .replace("{{ today }}", time.strftime("%Y-%m-%d"))
                .replace("{{today}}", time.strftime("%Y-%m-%d")).strip()) or None


def preset_hashes(rig: Rig) -> dict:
    import hashlib
    out = {}
    for name in ("prep", "minutes-review", "catch-up"):
        b = _unwrap_text(rig.call("workspace_read", path=f"asks/{name}.md", slug="_global"))
        out[name] = hashlib.sha256((b or "").encode()).hexdigest()[:12]
    return out


# ── waiting on the PRODUCT, not on engine bookkeeping ────────────────────────────────────────────
#
# The obvious wait is "poll the reaction row until it says done". It does not work and should not:
# the MCP's ``reactions_list`` returns no ``source_event_id``, so a row cannot be tied back to the
# fact that produced it — and with a second identity replaying beside us, flow+recency is a guess.
# The right signal is the ARTIFACT the person would have received: the mail with this meeting's
# title, the commit carrying this meeting's note. It is per-recipient, it is what the score reads
# anyway, and it judges what the run DID rather than what the engine recorded.

def reactions_snapshot(rig: Rig) -> list:
    """Engine bookkeeping, kept as EVIDENCE in the run dir — never as a completion signal."""
    out = rig.call("reactions_list", limit=60)
    if isinstance(out, dict):
        out = out.get("result", out)
        out = out.get("reactions", out) if isinstance(out, dict) else out
    return out if isinstance(out, list) else []


def wait_for(fn, budget_s: int, every: int = 8):
    """Poll ``fn`` until it returns something truthy, or the budget runs out."""
    deadline = time.time() + budget_s
    while time.time() < deadline:
        v = fn()
        if v:
            return v
        time.sleep(every)
    return None


def wait_mail(org: str, prefix: str, title: str, since: float, budget_s: int):
    return wait_for(lambda: mail_search(org, f"{prefix}: {title}", since), budget_s)


def wait_note(uid: str, shas_before: list, budget_s: int, stamp: str = "",
              transcript: str = ""):
    """A new commit touching kg/entities/meeting/ — the same completion test the flows engine
    itself applies (``flows_steps/agent.latest_meeting_note``), so the wait and the engine agree.

    ``stamp`` is a PREFERENCE, not a filter, and the reason is a finding in its own right. "The
    next new note" is only correct while this replay is the sole writer, and it is not: anything
    else dispatching a turn for the same user commits a note too, and the fixture in flight adopts
    it, scores someone else's meeting, and looks entirely successful doing so. I did that to myself
    by running a fabrication trial beside a sweep.

    The obvious fix — demand the meeting's own stamp in the filename — does not work, because THE
    AGENT DOES NOT USE THE FILENAME IT IS GIVEN. The step computes `{date}-{native}` and the prompt
    names that exact path; the agent writes `2026-09-02-dna-tsc-inaugural.md` — today's date and a
    slug of its own. So the path is preferred when it appears and accepted otherwise, with
    ``stamp_matched`` recorded either way, because a wait that demanded it would hang on every
    fixture and a wait that ignores it can be stolen.

    (Narrowing on the native id would be worse still: a recurring series shares ONE across every
    occurrence — all ten DNA fixtures are Zoom 96088138284 — so it matches the whole library.)"""
    best, GRACE = {}, 30

    def about_this_meeting(path: str) -> bool:
        """Is the note at ``path`` about the meeting this fixture is replaying?

        The stamp preference is not enough on its own. Within ONE sweep, a slow fixture's agent can
        commit its note after the next fixture has already started waiting, and the next fixture
        adopts it — three of nine rows in rev 9, caught only afterwards by the scorer. Checking the
        CONTENT at collection time stops it being collected at all, using the same six-word test
        the scorer and the engine's grounding gate use."""
        note = ws_file(uid, path) or ""
        if not note.strip() or not transcript:
            return True                  # nothing to judge with — do not reject on ignorance
        import re as _re

        def grams(t):
            w = _re.findall(r"[a-z0-9']+", t.lower())
            return {" ".join(w[i:i + 6]) for i in range(len(w) - 5)
                    if any(len(x) >= 6 for x in w[i:i + 6])}
        return bool(grams(note) & grams(transcript))

    def probe():
        for c in (workspace_git(uid).get("commits") or []):
            if c.get("sha") in shas_before:
                continue
            for f in (c.get("files") or []):
                if not f.startswith("kg/entities/meeting/"):
                    continue
                if not about_this_meeting(f):
                    continue             # another fixture's note landing late — not ours
                if stamp and stamp in f:
                    return (c["sha"], f, True)
                if "any" not in best:
                    best["any"], best["at"] = (c["sha"], f, False), time.time()
        # A PREFERENCE THAT ONLY YIELDS AT THE DEADLINE IS NOT A PREFERENCE, IT IS THE DEADLINE.
        # The first version returned the fallback after `wait_for` gave up, so on the normal path —
        # the agent naming the file itself, which it always does — every fixture paid the full
        # twenty minutes to reach a note that had been committed in the first two. That is the
        # exact defect this harness was built to catch, written into the harness. Take the
        # preferred name if it appears within a short grace, then take what is there.
        if "any" in best and time.time() - best["at"] > GRACE:
            return best["any"]
        return None

    return wait_for(probe, budget_s) or best.get("any")


def workspace_git(uid: str) -> dict:
    _, g = http("GET", f"{AGENT_API}/api/workspace/git", {"X-User-Id": uid})
    return g if isinstance(g, dict) else {}


def ws_file(uid: str, path: str) -> str | None:
    code, b = http("GET", f"{AGENT_API}/api/workspace/file?path={path}", {"X-User-Id": uid})
    return b.get("content") if code == 200 and isinstance(b, dict) else None


# ── model proof — from the worker's own argv, never from a setting ────────────────────────────────

def haiku_proof(uid: str) -> dict:
    """What model actually ran, read from the live worker process, not from configuration.
    A setting says what was asked for; ``/proc/<pid>/cmdline`` says what was run."""
    proof = {"captured": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    names = subprocess.run(["docker", "ps", "--format", "{{.Names}}"],
                           capture_output=True, text=True).stdout.split()
    workers = [n for n in names if n.startswith(f"vexa-worker-{uid}-")]
    proof["workers"] = workers
    for w in workers[:1]:
        env = subprocess.run(["docker", "inspect", w, "--format",
                              "{{range .Config.Env}}{{println .}}{{end}}"],
                             capture_output=True, text=True).stdout
        proof["env"] = [l for l in env.splitlines() if l.startswith(("VEXA_AGENT_MODEL", "VEXA_LLM_MODEL"))]
        proof["image"] = subprocess.run(["docker", "inspect", w, "--format", "{{.Config.Image}}"],
                                        capture_output=True, text=True).stdout.strip()
        argv = subprocess.run(
            ["docker", "exec", w, "sh", "-c",
             "for p in /proc/[0-9]*; do tr '\\0' ' ' < $p/cmdline 2>/dev/null | "
             "grep -l claude /dev/stdin >/dev/null 2>&1 && tr '\\0' ' ' < $p/cmdline; done"],
            capture_output=True, text=True).stdout
        m = re.search(r"--model\s+(\S+)", argv)
        if m:
            proof["argv_model"] = m.group(1)
    return proof


# ── the revolution's clean slate ─────────────────────────────────────────────────────────────────

TENANT_CLAIMS = [
    {"claim": "The tenant is a rehearsal identity replaying recorded meetings through the real "
              "flows; no live person reads its mail.", "source": "dna replay harness"},
    {"claim": "Its meetings are a fortnightly technical steering committee with recurring "
              "attendees from several studios.", "source": "the fixture corpus"},
]


def reset_workspace(rig: Rig, uid: str) -> dict:
    """Reset the loop's own workspace to the seed, then re-establish the readiness the queue gate
    needs. A revolution starts clean so `compounding` measures THIS revolution's knowledge and not
    the last one's leftovers -- and so a fixture library replayed twice is replayed identically.

    ``mark_scaffolded`` refuses without validated company context, and rightly: marking a workspace
    ready with nothing in it means every later artifact is written against an empty context. The
    harness therefore supplies the tenant claims and records the verdicts ITSELF, and says so --
    this is a scratch tenant with no human behind it. On any workspace with a real person, the
    person answers."""
    out = {"reset": http("POST", f"{AGENT_API}/api/workspace/reset", {"X-User-Id": uid},
                         {"target": "personal"})[1]}
    p = rig.call("propose", claims=TENANT_CLAIMS)
    ids = p.get("ids") or p.get("proposed") or []
    ids = [i.get("id") if isinstance(i, dict) else i for i in ids]
    out["validate"] = rig.call("validate", verdicts=[
        {"id": i, "verdict": "confirmed",
         "note": "harness-confirmed: rehearsal tenant, no human behind this identity"} for i in ids])
    out["mark_scaffolded"] = rig.call("mark_scaffolded")
    return out


def stack_stamp() -> dict:
    """WHAT THIS RUN ACTUALLY RAN AGAINST, read live and written into the run.

    Three things move independently on a hot stack — the flows engine's checkout, the agent-api
    image, and the per-dispatch worker image — and a score compared across a change to any of them
    is comparing two things at once. A revolution that cannot say which stack produced it is not a
    measurement, and none of this is recoverable after the fact."""
    def sh(*cmd):
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=20).stdout.strip()
        except Exception:                                         # noqa: BLE001
            return ""

    def image(c):
        return sh("docker", "inspect", c, "--format", "{{.Config.Image}}")

    src = ""
    for pid in sh("pgrep", "-f", "m flows_worker").split():
        try:
            env = pathlib.Path(f"/proc/{pid}/environ").read_text().split("\0")
        except Exception:                                         # noqa: BLE001
            continue
        for e in env:
            if e.startswith("PYTHONPATH=") and "adoption-sim" not in e:
                src = e.split("=", 1)[1]
    repo = src.split("/core/flows")[0] if src else ""
    return {
        "flows_engine_src": src,
        "flows_engine_sha": sh("git", "-C", repo, "rev-parse", "--short", "HEAD") if repo else "",
        "agent_api_image": image("vexa-dogfood-agent-api-1"),
        "worker_image_pin": next(
            (l.split("=", 1)[1] for l in
             pathlib.Path.home().joinpath("dev/estate/deploy/compose/.env").read_text().splitlines()
             if l.startswith("AGENT_WORKER_IMAGE=")), "") if
        pathlib.Path.home().joinpath("dev/estate/deploy/compose/.env").exists() else "",
        "runtime_image": image("vexa-dogfood-runtime-1"),
    }


# ── one fixture ──────────────────────────────────────────────────────────────────────────────────

def replay_one(rig: Rig, uid: str, org: str, fx_path: pathlib.Path, rev: int, run: pathlib.Path,
               caps: pathlib.Path, unique_native: bool = False) -> dict:
    date = fx_path.name.split(".")[0]
    fx = json.loads(fx_path.read_text())
    m = fx["meeting"]
    rec: dict = {"date": date, "title": m["title"], "t_start": time.time()}

    segs = [{"start": float(s.get("t", s.get("start", 0.0))), "end": float(s.get("end", 0.0)),
             "speaker": s.get("speaker", "?"), "text": s.get("text", "")} for s in fx["segments"]]
    rec["segments"] = len(segs)
    rec["transcript_chars_full"] = sum(len(s["speaker"]) + len(s["text"]) + 2 for s in segs)
    vid = f"dna-{date}"
    (caps / f"{vid}.segments.json").write_text(json.dumps(segs))

    # A recurring meeting keeps ONE native id across every occurrence — all ten DNA fixtures are
    # Zoom 96088138284 — and the note path used to be keyed on the day the note was WRITTEN, so a
    # series replayed in one afternoon put every occurrence on one path. The product fix
    # (`504125a45`, `_meeting_stamp`) keys it on the meeting's OWN start, taken from `refs.start`
    # first. A completed meeting therefore has to CARRY its start, which this replay was not
    # sending — a real one always has one, so sending it is the faithful thing as well as the
    # working one. The native id stays shared, as it is in life, so the fix is actually under test.
    native = m["native_meeting_id"]
    # UTC midnight of the occurrence, not local midnight. time.mktime reads the SERVER's zone, so
    # on a UTC+3 host "2026-03-02" became 2026-03-01T21:00Z and every note was filed a day early —
    # which, for a recurring series, is a collision with the previous occurrence.
    occurred = calendar.timegm(time.strptime(date, "%Y-%m-%d"))
    if unique_native:
        # --unique-native: for an engine that does NOT yet carry the note-date fix. It makes the
        # sweep complete, and it makes the fix untestable — so it is a flag the operator sets on
        # purpose and the run records, never a default that quietly hides which behaviour was live.
        native = f"{native}-{date}"
    seed = rig.call("meeting_seed", native_id=native, title=m["title"], video_id=vid,
                    occurred_at=str(occurred))
    if not isinstance(seed, dict) or "meeting_id" not in seed:
        rec["error"] = f"meeting_seed: {str(seed)[:300]}"
        return rec
    mid = seed["meeting_id"]
    # No transcript body comes back any more, and none is passed on. The agent reads the words
    # itself over the MCP; `delivered` is kept as an explicit 0 so a run before and a run after
    # this change are still readable side by side.
    rec.update(meeting_id=mid, segments_loaded=seed.get("segments_loaded"),
               scheduled_at=seed.get("scheduled_at"),
               transcript_chars_delivered=len(seed.get("transcript") or ""))

    mail_mark = time.time()
    up_id = f"dna-r{rev}-prep-{date}"
    rec["emit_upcoming"] = emit_fact(
        "meeting.upcoming", up_id,
        {"organizer": org, "title": m["title"], "start": int(time.time()) + 300,
         "uid": uid, "meeting_id": mid}, actor=f"uid {uid}")
    rec["prepare_mail"] = wait_mail(org, "Prepare", m["title"], mail_mark, 300)
    print(f"  prepare_mail={bool(rec['prepare_mail'])}", flush=True)

    shas_before = [c.get("sha") for c in (workspace_git(uid).get("commits") or [])]
    done_id = f"dna-r{rev}-done-{date}"
    rec["emit_completed"] = emit_fact(
        "meeting.completed", done_id,
        {"organizer": org, "title": m["title"], "uid": uid, "meeting_id": mid,
         "native": native, "start": occurred}, actor=f"uid {uid}")
    hit = wait_note(uid, shas_before, 1200, stamp=date,
                    transcript="\n".join(x["text"] for x in segs))
    note_path = None
    if hit:
        sha, note_path, matched = hit
        rec["note_sha"], rec["stamp_matched"] = sha[:9], matched
        rec["note"] = ws_file(uid, note_path)
    rec["note_path"] = note_path
    print(f"  note={bool(note_path)}", flush=True)
    rec["minutes_mail"] = wait_mail(org, "Minutes", m["title"], mail_mark, 300) if note_path else None
    rec["latency_s"] = round(time.time() - rec["t_start"], 1)
    rec["reactions_snapshot"] = reactions_snapshot(rig)

    for name, key in (("prep", "opening_prep"), ("minutes-review", "opening_minutes")):
        p = preset_prompt(rig, name, mid)
        rec[key] = {"prompt": p,
                    "reply": chat_turn(uid, f"askchat-r{rev}-{name}-{date}", p) if p else None}
        print(f"  {key}={bool(rec[key]['reply'])}", flush=True)
    return rec


# ── main ─────────────────────────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixtures", required=True)
    ap.add_argument("--rev", type=int, required=True)
    ap.add_argument("--uid", default="68")
    ap.add_argument("--organizer", default="loop@rehearsal.test")
    ap.add_argument("--run-root", default=str(pathlib.Path.home() / "dna-runs"))
    ap.add_argument("--caps", default=str(pathlib.Path.home() / ".storm/caps"))
    ap.add_argument("--token-file", default="")
    ap.add_argument("--limit", type=int, default=0, help="replay only the first N fixtures")
    ap.add_argument("--unique-native", action="store_true",
                    help="give each occurrence its own native id — for an engine without the "
                         "note-date fix; recorded in replay.json because it changes what is tested")
    ap.add_argument("--reset", action="store_true",
                    help="reset the workspace to the seed before replaying (start of a revolution)")
    a = ap.parse_args()

    run = pathlib.Path(a.run_root) / f"r{a.rev}"
    run.mkdir(parents=True, exist_ok=True)
    token = pathlib.Path(a.token_file or (pathlib.Path(a.run_root) / "r0" / ".token")).read_text().strip()
    rig = Rig(token)
    rig.connect()

    fixtures = sorted(pathlib.Path(a.fixtures).glob("*.transcript.json"))
    if a.limit:
        fixtures = fixtures[:a.limit]

    setup = reset_workspace(rig, a.uid) if a.reset else None
    if setup:
        (run / "reset.json").write_text(json.dumps(setup, indent=1, default=str))
        print("[replay] workspace reset:", json.dumps(setup.get("mark_scaffolded"))[:160], flush=True)

    (run / "haiku-proof.json").write_text(json.dumps(haiku_proof(a.uid), indent=1))
    out = {"rev": a.rev, "reset": bool(a.reset), "unique_native": bool(a.unique_native),
           "stack": stack_stamp(), "uid": a.uid, "fixtures_dir": str(a.fixtures),
           "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "preset_hashes": preset_hashes(rig), "records": []}
    for fx in fixtures:
        print(f"[replay] {fx.name}", flush=True)
        try:
            rec = replay_one(rig, a.uid, a.organizer, fx, a.rev, run, pathlib.Path(a.caps),
                             unique_native=a.unique_native)
        except Exception as e:                                    # noqa: BLE001
            import traceback
            rec = {"date": fx.name.split(".")[0], "error": f"{type(e).__name__}: {e}",
                   "traceback": traceback.format_exc()[-1500:]}
        out["records"].append(rec)
        (run / "replay.json").write_text(json.dumps(out, indent=1, default=str))
        print(f"[replay] {rec['date']} note={bool(rec.get('note'))} "
              f"minutes_mail={bool(rec.get('minutes_mail'))} latency={rec.get('latency_s')}",
              flush=True)
    (run / "haiku-proof.json").write_text(json.dumps(haiku_proof(a.uid), indent=1))
    # Stamp the stack AGAIN and say plainly if it moved. A sweep runs for the best part of an
    # hour on a hot deployment; revolution 3 had its engine swapped underneath it half way
    # through, so its stamp described a stack that served only the first half. A run that cannot
    # say which stack produced it is not a measurement, and a single stamp cannot say.
    out["stack_end"] = stack_stamp()
    drift = {k: [out["stack"].get(k), out["stack_end"].get(k)]
             for k in out["stack_end"] if out["stack"].get(k) != out["stack_end"].get(k)}
    out["stack_drift"] = drift
    (run / "replay.json").write_text(json.dumps(out, indent=1, default=str))
    if drift:
        print("[replay] WARNING — the stack MOVED mid-sweep; this run mixes two stacks:", flush=True)
        for k, (a_, b_) in drift.items():
            print(f"          {k}: {a_} -> {b_}", flush=True)
    print("wrote", run / "replay.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
