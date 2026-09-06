"""Agent-domain steps — agent-api HTTP. The conversation pattern is TWO steps so the worker is
restart-proof: dispatch records the history baseline in ITS receipt; collect waits (Wait, never
sleep) until the session history outgrows it."""
from __future__ import annotations

import time
import urllib.parse

from flows import Done, StepCtx, StepError, Wait

from .common import agent_door, http, require_internal_secret, scaffolded, swallowed, ws_file


def history(uid: str, session: str) -> list:
    code, hist = http("GET", f"{agent_door()}/api/sessions/{urllib.parse.quote(session)}/history",
                      {"X-User-Id": uid})
    if isinstance(hist, dict):
        hist = hist.get("turns", [])          # the endpoint wraps: {"turns": [...]}
    return hist if isinstance(hist, list) else []


def dispatch_turn(uid: str, session: str, prompt: str, room: dict | None = None,
                  flow: str = "", step: str = "") -> int:
    """Fire an agent turn; returns the history length BEFORE it (the collect baseline).
    /api/chat is an SSE STREAM that stays open for the whole turn — a client timeout while
    the stream runs is SUCCESS, not failure (the double-dispatch bug of 2026-08-23 evening).

    ``room`` opens THE MEETING ROOM: the post-meeting widening in which this turn may read the
    DESKS of the people who were in the meeting. Four fields go on the post, and the split between
    them is the whole safety property:

      ``meeting_id``      -> ``room_meeting_id``. The caller names ONLY THE MEETING, never a
                             workspace — a caller who could name workspaces could read anybody's
                             desk by naming it. Must be the meetings-domain ROW id.
      ``read``            -> ``room_participants``: the invite's ADDRESSES, in priority order. agent-api
                             resolves each through admin-api and mounts only those that already
                             have a subject AND a desk. **Addresses, not subject ids**: this side
                             deliberately does not resolve identity, so it cannot mint a ghost
                             account, and a person who is not a user is simply skipped there.
      ``names``           -> ``room_participant_names``: address -> the invite's ``CN=`` display name,
                             so the far side never has to guess a person from an email local part.
      ``read_max``        -> ``room_read_max``: the flow's cap, clamped to a server ceiling.

    THE INTERNAL-TIER HEADER IS PART OF THE ROOM, NOT AN EXTRA. agent-api refuses a room to any
    caller that cannot present ``X-Internal-Secret``, so it goes on the same post. Its value comes
    from the environment (``INTERNAL_API_SECRET``, a mode-600 file the lane's start script
    exports) and never from this repository; both entrypoints refuse to start without it, so by
    the time a turn is dispatched it exists.

    Omitted entirely when there is no room, so every other dispatch in this file sends exactly the
    body it has always sent.

    WHAT IS AND IS NOT "THE TURN IS RUNNING" (P21b). This used to be `except Exception: pass`, with
    the comment above as its whole justification — and the comment is right about ONE exception and
    wrong about every other. A client timeout while the SSE stream is open really does mean the
    turn started: that is the 2026-08-23 double-dispatch lesson and it is preserved exactly. A
    connection refused, a DNS failure, a 401, a 404, a 500 mean the opposite — nothing is running —
    and swallowing them returned a baseline as though a turn had been dispatched, after which
    `collect_reply` waited for a reply that was never coming, for as long as its caller allowed.
    A dispatch that did not happen, reported as one that did, is the exact shape P21 names.

    So: the status is checked, and only a TIMEOUT is treated as success.

    ``flow`` / ``step`` SAY WHO COMPOSED THIS TURN (Vexa-ai/vexa#1605). Nobody typed it, and on
    2026-09-06 the founder opened a held meeting's chat and read the whole `process-meeting` kick
    back as his own grey bubble — because the post carried nothing that said a machine wrote it.
    They travel as HEADERS, not body fields, and that is deliberate: `ChatBody` is `extra="forbid"`,
    so a field this side invents 422s the whole dispatch against an agent-api that predates it,
    while an unread header costs the turn its label and nothing else. agent-api composes the mark
    (`shared/marks.flow_mark`) — the marks are its vocabulary, and a caller able to compose one
    could compose any of them.

    Omitted when the caller names neither, so every dispatch that does not identify itself sends
    exactly the body and headers it has always sent."""
    base = len(history(uid, session))
    body = {"prompt": prompt, "session": session}
    headers = {"X-User-Id": uid}
    if flow and step:
        headers["X-Vexa-Flow"] = str(flow)
        headers["X-Vexa-Flow-Step"] = str(step)
    if room and room.get("meeting_id"):
        headers["X-Internal-Secret"] = require_internal_secret()
        body["room_meeting_id"] = str(room["meeting_id"])
        if room.get("read"):
            body["room_participants"] = [str(x) for x in room["read"]]
        if room.get("names"):
            body["room_participant_names"] = dict(room["names"])
        if room.get("read_max"):
            body["room_read_max"] = int(room["read_max"])
    try:
        code, out = http("POST", f"{agent_door()}/api/chat", headers, body, timeout=3)
    except StepError as e:
        if not _is_timeout(e):
            raise StepError(f"the agent turn for {uid}/{session} was not dispatched: {e}",
                            retryable=True) from e
        # THE ONE SUCCESS-SHAPED EXCEPTION, logged rather than passed over in silence (P18): a
        # swallow nobody can see is a swallow nobody can distinguish from the failure above when
        # this heuristic is one day wrong.
        swallowed("flows_steps.agent.dispatch_turn", "stream-open timeout, the turn is running", e)
        return base
    if not _ok(code):
        raise StepError(
            f"the agent turn for {uid}/{session} was not dispatched: agent-api answered {code} — "
            f"{str(out)[:200]}",
            # 5xx and 429 are the platform having a moment; a 4xx is a fact about this call, and
            # retrying it just delays the reaction without changing the answer.
            retryable=code == 429 or int(code) >= 500)
    return base


def _is_timeout(exc: BaseException) -> bool:
    """Was this `http` failure a read timeout — the stream staying open — rather than a call that
    never landed?

    Read off the CAUSE, not off the message. `common.http` wraps everything that is not an
    `HTTPError` in a `StepError`, and Python keeps the original on `__context__`; a socket read
    timeout is a `TimeoutError` (which `socket.timeout` has been an alias of since 3.10), and
    `urllib` may deliver it wrapped in a `URLError`. Matching on the formatted string would make
    this depend on an error message, which is not a contract."""
    seen = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, TimeoutError):
            return True
        reason = getattr(cur, "reason", None)
        if isinstance(reason, BaseException) and id(reason) not in seen:
            cur = reason
            continue
        cur = cur.__cause__ or cur.__context__
    return False


def collect_reply(uid: str, session: str, baseline: int):
    hist = history(uid, session)
    if len(hist) > baseline and hist[-1].get("role") == "agent" and hist[-1].get("text"):
        return hist[-1]["text"].strip()
    return None


def collect_outbox(uid: str, session: str, sent_hash: str | None):
    """THE FILE-OUTBOX CONTRACT — harness-agnostic reply collection (codex serves some workers
    and its transcripts never reach the history endpoint): the agent WRITES its email reply to
    ``mail_outbox/<session>.md``; a content change vs the last-sent hash is a new reply."""
    import hashlib
    content = ws_file(uid, f"mail_outbox/{session}.md")
    if not content or not content.strip():
        return None, sent_hash
    h = hashlib.sha256(content.encode()).hexdigest()[:16]
    if h == sent_hash:
        return None, sent_hash
    return content.strip(), h


def workspace_init(uid: str) -> dict:
    """Seed THIS subject's workspace tiers — the personal baseline plus the private `_system`
    tier — as that subject. Idempotent by contract: an existing workspace is returned untouched,
    so it is safe on every login and on every re-run.

    A non-2xx RAISES. It used to be discarded, which made "the workspace was created" and "the
    call 404'd" the same observable event: the next step then wrote into, or read out of, a
    directory that is not a git repo, and the failure surfaced somewhere with no information
    about its cause."""
    code, body = http("POST", f"{agent_door()}/api/workspace/init", {"X-User-Id": uid}, {})
    if not _ok(code):
        raise StepError(f"workspace init for {uid}: HTTP {code} — {str(body)[:200]}")
    return body if isinstance(body, dict) else {}


def mint_meeting_note(uid: str, meeting_id, path: str = "") -> str:
    """THIS MEETING'S PAGE ON THIS PERSON'S DESK, minted if it is not there — the path, or `""`.

    Vexa-ai/vexa#1601: *"the meeting doc exists from the moment the meeting exists for this
    person"*. A chat that sends a bot gets its page inside the turn, from agent-api itself; a
    meeting that arrives from the mailbox has no chat, so the flow asks for the same act here at the
    moment the row is created — and agent-api records the path on that row, which is what
    `_note_path` reads back instead of composing a second spelling.

    IDEMPOTENT on the far side: a page already there comes back untouched, never refreshed.

    DEGRADES, NEVER RAISES. A page that could not be minted costs the reader a document that
    appears when the meeting ends, exactly as it does today — it is not worth failing a prepare
    mail over, and every caller here is a step whose real work is the mail."""
    body = {"meeting_id": str(meeting_id)}
    if str(path or "").strip():
        body["path"] = str(path).strip()
    try:
        code, out = http("POST", f"{agent_door()}/api/meeting/note", {"X-User-Id": str(uid)}, body)
    except Exception as e:  # noqa: BLE001 — see the docstring
        swallowed("flows_steps.agent.mint_meeting_note", "the meeting's page was not minted", e,
                  uid=uid, meeting=str(meeting_id))
        return ""
    if not _ok(code) or not isinstance(out, dict):
        swallowed("flows_steps.agent.mint_meeting_note", "the meeting's page was not minted", None,
                  uid=uid, meeting=str(meeting_id), http=code)
        return ""
    return str(out.get("path") or "")


def propose(uid: str, *, source: str, act: str, source_label: str = "", by: str = "") -> dict:
    """APPEND ONE JOB to this person's short list — the empty chat's proposals (Vexa-ai/vexa#1614).

    Founder, 2026-09-06: the empty chat shows *"a short list that is updated by other agents when
    they see something as JTBD, can have up to 10 items"*. This is the door a flow writes one
    through. `source` + `act` are together the identity, so a re-run of the same step over the same
    meeting updates the row it already wrote instead of adding a second — the far side owns that,
    and the cap, and the ordering.

    DEGRADES, NEVER RAISES, exactly like `mint_meeting_note` and for the same reason: every caller
    is a step whose real work is something else, and a chip that did not appear is not worth losing
    a meeting's record over. The swallow is logged, so it is not silent."""
    body = {"source": str(source), "act": str(act),
            "source_label": str(source_label or ""), "by": str(by or "")}
    try:
        code, out = http("POST", f"{agent_door()}/api/proposals", {"X-User-Id": str(uid)}, body)
    except Exception as e:  # noqa: BLE001 — see the docstring
        swallowed("flows_steps.agent.propose", "the proposal was not filed", e,
                  uid=uid, source=str(source))
        return {}
    if not _ok(code) or not isinstance(out, dict):
        swallowed("flows_steps.agent.propose", "the proposal was not filed", None,
                  uid=uid, source=str(source), http=code)
        return {}
    return out


def head_sha(uid: str) -> str:
    """The current HEAD commit of ONE subject's desk, or "" if it has no repo (or cannot be read).

    Used as a BEFORE/AFTER witness, never as an assertion about content: a step that must not
    write anywhere proves it by showing the desk's history did not move. Degrading to "" is
    deliberate — a probe that cannot read must not be able to manufacture a difference, so a
    failed read compares equal to a failed read and the detector stays silent rather than
    failing a meeting on its own blind spot."""
    try:
        code, body = http("GET", f"{agent_door()}/api/workspace/git", {"X-User-Id": uid}, None)
    except Exception as e:  # noqa: BLE001 — a probe never costs the caller its step
        swallowed("flows_steps.agent.head_sha", "desk history unreadable", e, uid=uid)
        return ""
    if not _ok(code) or not isinstance(body, dict):
        swallowed("flows_steps.agent.head_sha", "desk history unreadable", None,
                  uid=uid, http=code)
        return ""
    commits = body.get("commits") or []
    if not commits or not isinstance(commits[0], dict):
        return ""
    return str(commits[0].get("sha") or "")


def head_subjects(uid: str, limit: int = 3) -> list:
    """The newest commit subjects on a desk — for naming, in a failure, exactly what landed."""
    try:
        code, body = http("GET", f"{agent_door()}/api/workspace/git", {"X-User-Id": uid}, None)
    except Exception as e:  # noqa: BLE001 — a probe never costs the caller its step
        swallowed("flows_steps.agent.head_subjects", "desk history unreadable", e, uid=uid)
        return []
    if not _ok(code) or not isinstance(body, dict):
        swallowed("flows_steps.agent.head_subjects", "desk history unreadable", None,
                  uid=uid, http=code)
        return []
    out = []
    for c in (body.get("commits") or [])[:limit]:
        if isinstance(c, dict):
            out.append(f"{str(c.get('sha') or '')[:9]} {c.get('subject') or c.get('message') or ''}")
    return out


def reset_desk(uid: str, sha: str, reason: str = "") -> dict:
    """PUT ONE SUBJECT'S DESK BACK to a sha this flow witnessed itself, and say whether it landed.

    The other half of `head_sha`. That one takes the BEFORE witness; this one undoes whatever moved
    HEAD past it. It exists because the decision-22 detector in `process_meeting` was loud, correct
    and un-actionable: on 2026-09-06 it fired twice, and both times the recovery was a person
    resetting a repository by hand and re-firing the reaction, while a grounded report sat unsent.

    ONLY BACKWARD, and only the caller's own desk — agent-api refuses a sha that is not an ancestor
    of HEAD and takes no workspace name at all, so the worst this can do is remove commits made after
    the witness. It is gated on `X-Internal-Secret`, the same edge the meeting room opens on.

    DEGRADES, NEVER RAISES, and the shape says which happened: `{"reset": bool, "detail": str, ...}`.
    A caller that cannot reset must say so in its refusal rather than die with a second failure on
    top of the first — the person reading it needs the ORIGINAL reason and the command to run."""
    body = {"sha": str(sha or "")}
    if reason:
        body["reason"] = str(reason)[:200]
    try:
        code, out = http("POST", f"{agent_door()}/api/workspace/git/reset",
                         {"X-User-Id": str(uid), "X-Internal-Secret": require_internal_secret()}, body)
    except Exception as e:  # noqa: BLE001 — see the docstring
        swallowed("flows_steps.agent.reset_desk", "the desk could not be reset", e, uid=uid, sha=sha)
        return {"reset": False, "detail": f"agent-api unreachable: {e}"}
    if not _ok(code) or not isinstance(out, dict):
        return {"reset": False, "detail": f"agent-api answered {code} — {str(out)[:200]}"}
    return out


def workspace_write(uid: str, path: str, content: str) -> None:
    """WRITE one file into ONE subject's workspace, as that subject, COMMITTED.

    `PUT /api/workspace/file` is the door the terminal's own page editor uses: it writes the file,
    `git add`s it and commits, so history stays honest and nothing out here has to reach for a
    repository it does not own. Reusing it rather than adding a second write path is the same rule
    the mail directory learned — two mechanisms writing one surface is how they drift.

    NOTE ON THE COMMIT AUTHOR: that endpoint stamps `vexa-terminal <terminal@vexa.local>`, which
    is hardcoded in `core/agent/control_plane/api.py`. It is not in `_SYSTEM_AUTHOR_NAMES`, so the
    terminal shows these commits as a real author rather than as plumbing.

    A non-2xx RAISES: a write that silently did not land leaves the workspace disagreeing with a
    mail that has already gone out, and nobody would ever learn that from a return value."""
    code, body = http("PUT", f"{agent_door()}/api/workspace/file", {"X-User-Id": uid},
                      {"path": path, "content": content})
    if not _ok(code):
        raise StepError(f"workspace write {path!r} for {uid}: HTTP {code} — {str(body)[:200]}")


def _ok(code) -> bool:
    try:
        return 200 <= int(code) < 300
    except (TypeError, ValueError):
        return False


# GONE WITH THE DESK WRITE THEY WATCHED FOR (founder decision 22, 2026-09-02):
# `latest_meeting_note(uid, baseline_shas)` and its `commit_shas(uid)` baseline. They detected the
# post-meeting turn's completion as a NEW COMMIT touching `kg/entities/meeting/` in the ORGANISER'S
# own repo. That run no longer writes into any desk — the canonical home of the note is the meeting
# row and its transcript store, and every attendee's desk (the organiser's included) receives the
# artefact afterwards, from `drop_to_attendees`. The commit will never happen, so a detector
# waiting for it would wait fifteen minutes and then fail every meeting.
#
# Deleted rather than left unused: they encode "the note is a commit in the organiser's desk",
# which is precisely the thing that stopped being true, and an unused detector is one somebody
# wires back up. Completion is now the agent's REPLY, grounded in the transcript — see
# `flows_defs/production.process_meeting`.
