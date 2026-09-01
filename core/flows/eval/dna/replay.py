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
    except Exception as e:                                        # noqa: BLE001
        return 0, {"error": f"{type(e).__name__}: {e}"}


def history(uid: str, session: str) -> list:
    _, h = http("GET", f"{AGENT_API}/api/sessions/{session}/history", {"X-User-Id": uid})
    turns = h.get("turns", []) if isinstance(h, dict) else []
    return turns if isinstance(turns, list) else []


def chat_turn(uid: str, session: str, prompt: str, budget_s: int = 420) -> str | None:
    """Dispatch one turn and wait for the agent's reply. ``/api/chat`` is an SSE stream that stays
    open for the whole turn, so a client timeout while it runs is SUCCESS -- the same lesson
    ``flows_steps/agent.dispatch_turn`` carries. Completion is read from the session history."""
    base = len(history(uid, session))
    http("POST", f"{AGENT_API}/api/chat", {"X-User-Id": uid},
         {"prompt": prompt, "session": session}, timeout=3)
    deadline = time.time() + budget_s
    while time.time() < deadline:
        h = history(uid, session)
        if len(h) > base and h[-1].get("role") == "agent" and h[-1].get("text"):
            return h[-1]["text"].strip()
        time.sleep(6)
    return None


def mail_since(ts: float, to: str) -> list[dict]:
    """Every message to ``to`` newer than ``ts``, newest first, bodies included."""
    _, d = http("GET", f"{MAILPIT}/api/v1/messages?limit=60")
    out = []
    for m in (d.get("messages", []) if isinstance(d, dict) else []):
        if to not in [t.get("Address", "") for t in m.get("To", [])]:
            continue
        created = m.get("Created", "")
        _, full = http("GET", f"{MAILPIT}/api/v1/message/{m['ID']}")
        body = (full.get("Text") or full.get("HTML") or "") if isinstance(full, dict) else ""
        out.append({"id": m["ID"], "created": created, "subject": m.get("Subject", ""),
                    "body": body})
    return out


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
    return wait_for(lambda: next((m for m in mail_since(since, org)
                                  if m["subject"] == f"{prefix}: {title}"), None), budget_s)


def wait_note(uid: str, shas_before: list, budget_s: int):
    """A new commit touching kg/entities/meeting/ — the same completion test the flows engine
    itself applies (``flows_steps/agent.latest_meeting_note``), so the wait and the engine agree."""
    def probe():
        for c in (workspace_git(uid).get("commits") or []):
            if c.get("sha") in shas_before:
                continue
            for f in (c.get("files") or []):
                if f.startswith("kg/entities/meeting/"):
                    return (c["sha"], f)
        return None
    return wait_for(probe, budget_s)


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


# ── one fixture ──────────────────────────────────────────────────────────────────────────────────

def replay_one(rig: Rig, uid: str, org: str, fx_path: pathlib.Path, rev: int, run: pathlib.Path,
               caps: pathlib.Path) -> dict:
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

    # PER-OCCURRENCE IDENTITY, and it is not a convenience.
    #
    # A recurring meeting keeps ONE native id across every occurrence — the ten DNA fixtures are
    # all Zoom 96088138284. `process_meeting` has the agent write its note at
    # `kg/entities/meeting/{date}-{native}.md` where `date` is the day the note is WRITTEN, not the
    # day the meeting happened (flows_defs/production.py). Replay a series in one afternoon and
    # every occurrence points at one path: the second silently overwrote the first, and the third
    # found a note about a different meeting where it was told to write, refused, and asked a
    # clarifying question — so no commit arrived and the step sat until its timeout. One of those
    # two failures looks like a failure. The other one looks like success.
    #
    # That is a defect in the product (a same-day backfill or re-process collides too, and the
    # first note loses), reported as its own finding. The harness stops tripping over it by giving
    # each occurrence the identity it already has everywhere else — each one is its own meeting row
    # — so a bad note is never confused with a clobbered one.
    native = f"{m['native_meeting_id']}-{date}"
    seed = rig.call("meeting_seed", native_id=native, title=m["title"], video_id=vid)
    if not isinstance(seed, dict) or "meeting_id" not in seed:
        rec["error"] = f"meeting_seed: {str(seed)[:300]}"
        return rec
    mid = seed["meeting_id"]
    transcript = seed.get("transcript", "")
    rec.update(meeting_id=mid, segments_loaded=seed.get("segments_loaded"),
               transcript_chars_delivered=len(transcript))

    mail_mark = time.time()
    up_id = f"dna-r{rev}-prep-{date}"
    rec["emit_upcoming"] = rig.call(
        "fact_emit", event_type="meeting.upcoming", source_event_id=up_id,
        subject_refs={"organizer": org, "title": m["title"], "start": int(time.time()) + 300,
                      "uid": uid, "meeting_id": mid})
    rec["prepare_mail"] = wait_mail(org, "Prepare", m["title"], mail_mark, 300)
    print(f"  prepare_mail={bool(rec['prepare_mail'])}", flush=True)

    shas_before = [c.get("sha") for c in (workspace_git(uid).get("commits") or [])]
    done_id = f"dna-r{rev}-done-{date}"
    rec["emit_completed"] = rig.call(
        "fact_emit", event_type="meeting.completed", source_event_id=done_id,
        subject_refs={"organizer": org, "title": m["title"], "uid": uid, "meeting_id": mid,
                      "native": native, "transcript": transcript})
    hit = wait_note(uid, shas_before, 1200)
    note_path = None
    if hit:
        sha, note_path = hit
        rec["note_sha"] = sha[:9]
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
    out = {"rev": a.rev, "reset": bool(a.reset), "uid": a.uid, "fixtures_dir": str(a.fixtures),
           "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "preset_hashes": preset_hashes(rig), "records": []}
    for fx in fixtures:
        print(f"[replay] {fx.name}", flush=True)
        try:
            rec = replay_one(rig, a.uid, a.organizer, fx, a.rev, run, pathlib.Path(a.caps))
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
    print("wrote", run / "replay.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
