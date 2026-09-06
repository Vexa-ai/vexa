"""REAL step adapters for the witness — the first real HTTP steps of core/flows.
Everything here talks to the LIVE dev stack: gateway (bots/transcripts), agent-api (processing,
workspace git), Mailpit (delivery), Postgres (fixture transcript injection — the one honest
double: no whisper service runs in this stack)."""
from __future__ import annotations

import json
import smtplib
import subprocess
import time
import urllib.request
from email.message import EmailMessage

GATEWAY = "http://localhost:18056"
AGENT_API = "http://localhost:18100"
MAILPIT = ("localhost", 1025)


def http(method: str, url: str, headers: dict, body: dict | None = None, timeout: float = 30):
    req = urllib.request.Request(url, method=method,
                                 data=json.dumps(body).encode() if body is not None else None)
    for k, v in {"content-type": "application/json", **headers}.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw.strip().startswith(("{", "[")) else raw)
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        return e.code, (json.loads(raw) if raw.strip().startswith(("{", "[")) else raw)


def send_mail(to: str, subject: str, body: str) -> str:
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = "vexa@bank.com", to, subject
    m.set_content(body)
    with smtplib.SMTP(*MAILPIT, timeout=5) as s:
        s.send_message(m)
    return f"mailpit:{to}"


def spawn_bot(api_key: str, meeting_url: str, bot_name: str = "Vexa Witness") -> dict:
    st, body = http("POST", f"{GATEWAY}/bots", {"X-API-Key": api_key},
                    {"meeting_url": meeting_url, "bot_name": bot_name,
                     "transcribe_enabled": False})   # transcript is the fixture; the BOT is real
    if st == 409:
        # a bot for this meeting already exists (our own retry) — adopt it
        st2, existing = http("GET", f"{GATEWAY}/bots", {"X-API-Key": api_key})
        for m in existing if isinstance(existing, list) else existing.get("meetings", []):
            if m.get("native_meeting_id") == meeting_url.rsplit("/", 1)[1]:
                return m
    if st not in (200, 201):
        raise RuntimeError(f"spawn failed {st}: {body}")
    return body


def meeting_status(api_key: str, platform: str, native: str) -> dict:
    st, body = http("GET", f"{GATEWAY}/transcripts/{platform}/{native}", {"X-API-Key": api_key})
    return body if st == 200 else {"status": f"http-{st}"}


def stop_bot(api_key: str, platform: str, native: str) -> None:
    http("DELETE", f"{GATEWAY}/bots/{platform}/{native}", {"X-API-Key": api_key})


def inject_fixture_transcript(meeting_id: int, session_uid: str) -> int:
    """The honest double: this stack runs no whisper, so the 'transcription' is a fixture —
    injected through the same table the collector writes."""
    lines = [
        (0.0, 6.0, "Anna", "Alright, quick sync on the pilot. Two decisions today."),
        (6.5, 14.0, "Ben", "First: we go with the phased rollout — pilot group is treasury, four weeks."),
        (14.5, 22.0, "Anna", "Agreed. Decision one: phased rollout, treasury first, four weeks starting Monday."),
        (22.5, 30.0, "Ben", "Second: Anna owns the security review. I need it before the pilot starts."),
        (30.5, 38.0, "Anna", "I commit to the security review by Friday. Send me the checklist today."),
        (38.5, 44.0, "Ben", "Will do — checklist to you by end of day. That's a commitment."),
        (44.5, 50.0, "Anna", "Open question for next time: do we invite risk & compliance to the pilot?"),
    ]
    sql = "; ".join(
        "INSERT INTO transcriptions (meeting_id,start_time,end_time,text,speaker,language,session_uid,segment_id,created_at) "
        f"VALUES ({meeting_id},{a},{b},'{t.replace(chr(39), chr(39)*2)}','{sp}','en','{session_uid}','fix-{i}',now())"
        for i, (a, b, sp, t) in enumerate(lines))
    subprocess.run(["docker", "exec", "vexa-v012-postgres-1", "psql", "-U", "postgres", "-d", "vexa",
                    "-c", sql], check=True, capture_output=True)
    return len(lines)


def agent_process(subject: str, meeting_id: int, native: str, transcript_text: str) -> None:
    """REAL agent turn: a real worker container, the real model, writing the real meeting doc."""
    prompt = (
        f"[witness] A meeting just completed (id {meeting_id}, google_meet {native}). Its final "
        f"transcript follows. Write the meeting note at kg/entities/meeting/{time.strftime('%Y-%m-%d')}-witness-{native}.md "
        "using the meeting template shape: frontmatter (type: meeting, id, title), then sections "
        "Decided / Committed / Open, each item attributed to its speaker. Interlink people as "
        "[[wikilinks]]. Keep it terse and faithful — record only what was said.\n\nTRANSCRIPT:\n"
        + transcript_text)
    st, body = http("POST", f"{AGENT_API}/api/chat",
                    {"X-User-Id": subject}, {"prompt": prompt, "session": f"meet-{meeting_id}"},
                    timeout=10)
    # SSE streams back on this connection; we don't consume it — completion is observed via git.


def workspace_git(subject: str) -> dict:
    st, body = http("GET", f"{AGENT_API}/api/workspace/git", {"X-User-Id": subject})
    return body if st == 200 else {"commits": []}


def workspace_file(subject: str, path: str) -> str | None:
    st, body = http("GET", f"{AGENT_API}/api/workspace/file?path={urllib.parse.quote(path)}",
                    {"X-User-Id": subject})
    return body.get("content") if st == 200 and isinstance(body, dict) else None


import urllib.parse  # noqa: E402
