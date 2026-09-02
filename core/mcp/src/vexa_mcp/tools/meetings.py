"""MEETINGS — bots, transcripts, seeds, participants, search, recordings, terms.

Every verb here goes to the GATEWAY with the caller's own key, which is the only edge in the stack
that verifies a user credential and stamps ``X-User-Id`` from it. Two exceptions, both named:
``transcript_terms`` forwards to agent-api, because the extractor and the entity index it matches
against live where the workspaces are; and the two transcript converters read a file the caller
already has and write it under ``VEXA_HOME`` — they reach no service at all.
"""
from __future__ import annotations

import json
import re
import time
import pathlib
import datetime

from .. import config
from ..config import AGENT_API, FLOWS_API
from ..httpc import flows_headers as _fkey, gw as _gw_http, http as _http, q as _q
from ..identity import anon_guard, caller_email, me
from ..shaping import capped, in_their_clock, meeting_ref, resolve_meeting, ui_meeting_url
from ..registry import tool


def _scheduled_joins(mid: str):
    """Live scheduled-join rows for one meeting: ``(rows, error)``. Never conflates the two.

    The reactions listing carries no meeting reference, so the only handle on "the join I booked for
    THIS meeting" is the ``source_event_id`` ``bot_schedule`` itself wrote — which is why flows-api's
    listing takes a ``source_event_prefix``. An empty list and a failed read are opposite facts to a
    person — one is "nothing is booked", the other is "I cannot see" — so they come back as
    different values, never as the same empty list.

    The rig answered this with ``psycopg.connect`` straight into the flows Postgres, past its owner,
    with a URL read out of ``~/.storm/dburl``: two access paths to one store, in one tool (seam
    inventory B6.3).
    """
    st, body = _http("GET", f"{FLOWS_API}/reactions" + _q(
        source_event_prefix=f"sched-{mid}-",
        status="admitted,retrying,blocked,running"), _fkey())
    if not (200 <= st < 300) or not isinstance(body, dict):
        return None, f"flows-api answered {st}: {str(body)[:160]}"
    return [{"id": r.get("id"), "flow": r.get("flow"), "step": r.get("step"),
             "status": r.get("status")} for r in body.get("reactions", [])], None


def person_settings(uid: str) -> dict:
    """This person's preferences, defaults filled in, from the service that owns them."""
    st, body = _http("GET", f"{AGENT_API}/api/settings", {"X-User-Id": uid})
    if 200 <= st < 300 and isinstance(body, dict):
        return body.get("settings") or {}
    return {}


def person_tz(uid: str, set_to: str = "") -> str:
    """This person's IANA zone, remembered across calls by agent-api.

    Times were once rendered on the server's clock, so a Lisbon person booking a standup was told it
    would join at 19:15 when it was 17:15 where they stood. The agent knows their zone from its own
    environment; we only have to be told once and then never state a bare time again."""
    if set_to:
        st, body = _http("POST", f"{AGENT_API}/api/settings", {"X-User-Id": uid},
                         {"key": "timezone", "value": set_to})
        if 200 <= st < 300 and isinstance(body, dict):
            return (body.get("settings") or {}).get("timezone", "")
        return person_settings(uid).get("timezone", "")
    return person_settings(uid).get("timezone", "")


@tool
@anon_guard
def meetings_list(token: str = "") -> str:
    """Every meeting a user can see, through the gateway with that user's own key.\n\n    If you have not called whats_waiting() yet this session, call it first."""
    uid = me()
    st, body = _gw_http(uid, "GET", "/meetings")
    return capped({"status": st, "result": body}, 10000)


@tool
@anon_guard
def meeting_info(meeting_url: str = "", meeting_id: str = "", token: str = "") -> str:
    """Everything known about one meeting: status, times, title, how it ended."""
    uid = me()
    mid, err = resolve_meeting(uid, meeting_url, meeting_id)
    if not mid:
        return json.dumps({"error": err})
    st, r = _gw_http(uid, "GET", f"/meetings/{mid}")
    if st != 200:
        return json.dumps({"error": "no such meeting", "status": st})
    keep = {k: r.get(k) for k in ("id", "platform", "native_meeting_id", "status",
                                  "start_time", "end_time", "completion_reason",
                                  "constructed_meeting_url", "data") if k in r}
    if keep.get("platform") and keep.get("native_meeting_id"):
        keep["ui_url"] = ui_meeting_url(keep["platform"], keep["native_meeting_id"],
                                         row_id=keep.get("id"))
    return json.dumps(keep)


@tool
@anon_guard
def meeting_update(meeting_url: str = "", meeting_id: str = "", title: str = "",
                   notes: str = "", token: str = "") -> str:
    """Rename a meeting or attach a note to it — the label the team will find it under."""
    uid = me()
    mid, err = resolve_meeting(uid, meeting_url, meeting_id)
    if not mid:
        return json.dumps({"error": err})
    out = {}
    if title:
        st, r = _gw_http(uid, "PATCH", f"/meetings/{mid}", {"title": title[:512]})
        if st == 409:
            # once the bot lifecycle owns the meeting, the title rides the annotate channel
            st2, info = _gw_http(uid, "GET", f"/meetings/{mid}")
            pf, nid = (info or {}).get("platform"), (info or {}).get("native_meeting_id")
            if pf and nid:
                st, r = _gw_http(uid, "POST", f"/meetings/{pf}/{nid}/annotate",
                            {"title": title[:512]})
        out["title"] = "set" if st == 200 else f"refused ({st}: {str(r)[:120]})"
    if notes:
        # notes ride the annotate channel, keyed by platform + native id
        st2, info = _gw_http(uid, "GET", f"/meetings/{mid}")
        pf, nid = (info or {}).get("platform"), (info or {}).get("native_meeting_id")
        if pf and nid:
            st, r = _gw_http(uid, "POST", f"/meetings/{pf}/{nid}/annotate",
                        {"metadata": {"notes": notes[:2000]}})
            out["notes"] = "attached" if st == 200 else f"refused ({st}: {str(r)[:120]})"
        else:
            out["notes"] = "refused (meeting has no native id to annotate)"
    if not out:
        return json.dumps({"error": "give title= and/or notes="})
    return json.dumps({"updated": mid, **out})


@tool
@anon_guard
def meeting_delete(meeting_url: str = "", meeting_id: str = "", token: str = "") -> str:
    """Erase one meeting and its transcript, permanently. ONLY on your person's explicit,
    named request — never as tidying, never inferred. Say plainly that it cannot be undone
    before you call this."""
    uid = me()
    mid, err = resolve_meeting(uid, meeting_url, meeting_id)
    if not mid:
        return json.dumps({"error": err})
    st, r = _gw_http(uid, "DELETE", f"/meetings/{mid}")
    return json.dumps({"deleted": st in (200, 204), "status": st})


@tool
@anon_guard
def meeting_participants(meeting_url: str, token: str = "") -> str:
    """Who was in a meeting, as the bot saw them."""
    uid = me()
    platform, mid = meeting_ref(meeting_url)
    if not platform:
        return json.dumps({"error": mid})
    st, r = _gw_http(uid, "GET", f"/meetings/{platform}/{mid}/participants")
    if st != 200:
        return json.dumps({"error": "no participant data for that meeting", "status": st})
    return capped(r, 4000)


@tool
@anon_guard
def meeting_seed(native_id: str, title: str, video_id: str,
                 started_at: str = "", occurred_at: str = "") -> str:
    """Create a COMPLETED, ADDRESSABLE meeting for a user and load a real transcript into it.

    This is the capture double: instead of driving a browser into a live call, it imports the
    words a bot would have produced. Everything downstream — the post-meeting flow, the agent
    turn, the artifacts — then runs on genuinely messy multi-speaker material rather than a
    hand-written fixture.

    TWO service calls, and nothing else. `POST /meetings` mints the row; `POST
    /meetings/{id}/transcript-import` puts the transcript on it and completes it with the
    occurrence window the recording actually covers. Both through the gateway, on the caller's own
    key — the product's `import a transcript` feature, used exactly as a person would use it.

    It used to do far more, and none of it was ours to do: read the postgres password out of
    another container with `docker inspect`, INSERT `meeting_sessions` and `transcriptions` over
    `docker exec … psql` with speaker names string-interpolated into SQL, climb the bot FSM through
    a callback meant for a browser, then UPDATE `meetings.start_time/end_time` by hand because no
    route took a time. Four writers on tables meeting-api owns (the audit's V4/N5), and it still
    produced rows the product never makes. `started_at` is now the service's input, not a column
    this tool corrects afterwards.

    `started_at` is WHEN THE MEETING HAPPENED (ISO-8601, or epoch seconds). Pass it. It is the
    row's `scheduled_at` AND the start of its occurrence window; its LENGTH is the transcript's
    own — the last segment's `end` — so a 40-minute recording seeds a 40-minute meeting instead of
    a zero-length one. Without it the default is a call that ended just this second. A double that
    cannot say when the meeting was is not a double of a meeting: `_meeting_stamp` falls back to
    today when the row has no time, so several occurrences of one recurring series collapse onto
    today's date and into a single note file. `occurred_at` is the old name for this argument and
    still works.

    IDEMPOTENT: the import's identity is (source, meeting row), so re-seeding the same transcript
    into the same meeting writes nothing and says so. Seeding it into a NEW row imports it again.

    It does NOT return the transcript. The agent reads the words itself with
    `meeting_transcript(meeting_id=<row>, tail=0)` — all of them, not a copy truncated to fit
    inside an event."""
    uid = me()
    import datetime as _dt

    segs_path = config.CAPS_DIR / f"{video_id}.segments.json"
    if not segs_path.exists():
        return json.dumps({"error": "run captions_to_segments first"})
    segs = json.loads(segs_path.read_text())
    if not segs:
        return json.dumps({"error": f"no segments in {segs_path}"})
    # The run's length is the transcript's own length — segment `end`s are seconds from the start
    # of the capture, so the last one IS the duration of the meeting the bot sat through.
    duration = max(float(s["end"]) for s in segs)
    when_raw = str(started_at or occurred_at or "").strip()
    if when_raw:
        try:
            started = (_dt.datetime.fromtimestamp(float(when_raw), _dt.timezone.utc)
                       if when_raw.replace(".", "", 1).isdigit()
                       else _dt.datetime.fromisoformat(when_raw.replace("Z", "+00:00")))
        except ValueError:
            # LOUD, not "a bad stamp must not lose the seed". A stamp we silently drop seeds the
            # meeting at the wrong moment, which is the exact defect this argument exists to fix —
            # and the caller never learns their stamp was thrown away.
            return json.dumps({"error": "started_at is neither ISO-8601 nor epoch seconds",
                               "started_at": when_raw})
        if started.tzinfo is None:
            started = started.replace(tzinfo=_dt.timezone.utc)
        started = started.astimezone(_dt.timezone.utc)
    else:
        # Default: the call ENDED just now — the state the post-meeting flow meets in the wild.
        started = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(seconds=duration)
    ended = started + _dt.timedelta(seconds=duration)
    when = started.isoformat()

    # A seeded row must be ADDRESSABLE the way a real one is. POST /meetings derives
    # (platform, native_meeting_id) from `meeting_url` and stores ("unknown", NULL) without one —
    # and several product paths identify a meeting by that pair rather than by row id. A JITSI url
    # deliberately: meeting-api requires a STRICT abc-defg-hij code for google_meet, which would
    # force a synthetic native id and break the caller's own identity — the note filename and the
    # dedup key both ride on it. The jitsi room rule is any path segment, so the native id survives
    # verbatim as native_meeting_id.
    url = f"https://meet.jit.si/{native_id}"
    st, m = _gw_http(uid, "POST", "/meetings",
                     {"title": title, "scheduled_at": when, "meeting_url": url})
    if st == 409:
        # 409 is NOT "the url was rejected". It is `uq_meeting_active_user_platform_native`
        # saying an ADDRESSABLE non-terminal row for jitsi/<native_id> already exists for this
        # user. This block used to retry WITHOUT the url, which succeeds — and mints exactly the
        # ("unknown", NULL) row the paragraph above exists to prevent: no share can be minted
        # against it, so the attendee mail ships with no token. One 409 cost the founder a click
        # into a chat that could not see meeting 97.
        #
        # We do NOT adopt the existing row. Non-terminal means planned or LIVE, and importing a
        # transcript onto a row this tool did not create would stack a second capture source on a
        # real meeting's segments — which is why the import route itself refuses a row with a bot
        # in flight. So the seed makes the caller's intent explicit instead: it names the row that
        # is in the way and the two ways out. A seed that reaches `completed` leaves the index (the
        # constraint is partial on status NOT IN (completed, failed)), so re-seeding a FINISHED
        # double never lands here; what does is a leftover idle/scheduled row, or a live meeting.
        gst, gb = _gw_http(uid, "GET", "/meetings?limit=100")
        rows = (gb or {}).get("meetings", []) if isinstance(gb, dict) else []
        dup = next((x for x in rows
                    if x.get("platform") == "jitsi"
                    and str(x.get("native_meeting_id")) == str(native_id)
                    and x.get("status") not in ("completed", "failed")), {})
        return json.dumps({"error": "a non-terminal meeting already holds this native id",
                           "status": 409, "native_id": native_id, "platform": "jitsi",
                           "existing_meeting_id": dup.get("id"),
                           "existing_status": dup.get("status"),
                           "lookup_status": gst,
                           "next": "seed under a different native_id, or meeting_delete("
                                   "meeting_id=<existing>) if that row is a leftover double"})
    if st not in (200, 201):
        # Every other non-2xx, 422 ("unrecognized 'meeting_url'") included. The url-less retry
        # used to live here as well; it is gone. An unaddressable row IS a defective double, so
        # trading a loud failure for a silent one bought nothing and lost the share.
        return json.dumps({"error": "create failed", "status": st, "body": str(m)[:300],
                           "meeting_url": url})
    mid = m["id"]
    # POST-CONDITION, checked rather than assumed: this tool must never report success having
    # created a row that cannot be addressed. Both fields come back on the create response.
    if m.get("platform") in (None, "", "unknown") or not m.get("native_meeting_id"):
        return json.dumps({"error": "seed created an UNADDRESSABLE row — no share can be minted "
                                    "against it and the attendee link would carry no token",
                           "meeting_id": mid, "platform": m.get("platform"),
                           "native_meeting_id": m.get("native_meeting_id"),
                           "meeting_url": url,
                           "next": "meeting_delete(meeting_id=%s) and fix the seed url" % mid})

    # The whole rest of the seed, in one call the product exposes. `source: "seed"` is declared,
    # never inferred — the row records that these words came from a double, so nothing downstream
    # has to guess whether a meeting was recorded or imported.
    st, body = _gw_http(uid, "POST", f"/meetings/{mid}/transcript-import", {
        "segments": [{"start": float(s["start"]), "end": float(s["end"]),
                      "speaker": s.get("speaker"), "text": s.get("text") or "",
                      "language": s.get("language") or "en"} for s in segs],
        "started_at": started.isoformat().replace("+00:00", "Z"),
        "ended_at": ended.isoformat().replace("+00:00", "Z"),
        "source": "seed",
    })
    if st != 200:
        return json.dumps({"meeting_id": mid, "error": "transcript import refused",
                           "status": st, "body": str(body)[:400],
                           "next": "meeting_delete(meeting_id=%s) and retry" % mid})
    if not isinstance(body, dict):
        return json.dumps({"meeting_id": mid, "error": "import returned no row",
                           "body": str(body)[:300]})

    # Report what the SERVICE says the row is, not what this tool intended — the same discipline
    # the psql read-back had, now for free because the route answers with the row it wrote.
    return json.dumps({"meeting_id": mid, "native_id": native_id, "title": title,
                       "segments_loaded": body.get("segments_imported"),
                       "segments_captured": body.get("segments_captured"),
                       "imported": body.get("imported"),
                       "uid": uid, "session_uid": body.get("session_uid"),
                       "source": body.get("source"), "imported_at": body.get("imported_at"),
                       "scheduled_at": when,
                       "platform": body.get("platform") or m.get("platform"),
                       "native_meeting_id": body.get("native_meeting_id")
                       or m.get("native_meeting_id"),
                       "status": body.get("status"),
                       "start_time": body.get("start_time"),
                       "end_time": body.get("end_time"),
                       "duration_minutes": round(duration / 60, 1),
                       "read_the_words_with": "meeting_transcript(meeting_id=%s, tail=0)" % mid})


@tool
@anon_guard
def meeting_transcript(meeting_url: str = "", tail: int = 80, since: str = "",
                       meeting_id: str = "", token: str = "") -> str:
    """The words of a meeting, live while it runs or complete after it ends.

    Address it EITHER by a pasted link (meeting_url) OR by its row id (meeting_id) — the same
    pair meeting_info / meeting_update / meeting_delete already take, resolved the same way.
    The row id is the one that matters in practice: every deeplink this product mints speaks row
    ids (`?meeting=<row>`), the `{{meeting}}` an ask-preset substitutes IS a row id, and a
    captured meeting with no platform/native pair — a seeded or imported one — had no address
    at all here. An agent told "you have the meeting" could not read it, because this was the
    one verb in the family that would not accept what it had been handed.

    TO READ A WHOLE MEETING, pass tail=0: you get every segment. That is what a write-up needs,
    and the alternative was paging a finished meeting as though it were still running.

    TO FOLLOW A LIVE CALL, pass back the `cursor` from your last call as since=<cursor>: you
    get only what has been said since, and the next cursor. Nothing to remember, nothing to
    diff, no watcher to build — call it again every 20-30 seconds and read out what is new.
    Without `since` you get the last `tail` segments.

    `read_ok` is always true when the read itself worked. new_segments=0 with read_ok=true
    means the room is quiet; an `error` key means your reader failed. They are opposite facts
    and your person needs to know which."""
    uid = me()
    platform = None
    if meeting_id or not meeting_url:
        row, err = resolve_meeting(uid, meeting_url, meeting_id)
        if not row:
            return json.dumps({"error": err or "give meeting_url=<link> or meeting_id=<row id>"})
        mid = row
        # The gateway already serves this shape; it was simply unreachable from a tool.
        st, r = _gw_http(uid, "GET", f"/transcripts/by-id/{row}")
    else:
        platform, mid = meeting_ref(meeting_url)
        if not platform:
            return json.dumps({"error": mid})
        st, r = _gw_http(uid, "GET", f"/transcripts/{platform}/{mid}")
    if st != 200:
        return json.dumps({"error": "could not read the transcript", "read_ok": False,
                           "status": st,
                           "tell_your_person": "Say the READ failed — never that the room is "
                                               "quiet. You do not know that.",
                           "note": "if the bot was just sent it may still be knocking — try "
                                   "again in ~20 seconds; if this repeats, report_friction()"})
    segs = (r or {}).get("segments") or []

    def _at(g):
        return g.get("absolute_start_time") or g.get("start")

    fresh = segs
    if since:
        # everything strictly after the cursor. String compare is right for ISO timestamps and
        # for the float-seconds the gateway also emits, as long as both sides come from _at.
        fresh = [g for g in segs if str(_at(g) or "") > str(since)]
    elif int(tail) <= 0:
        fresh = segs                      # tail=0 — the WHOLE meeting, for a write-up
    else:
        fresh = segs[-max(1, min(int(tail), 400)):]

    lines = [{"who": g.get("speaker") or "?",
              "said": (g.get("text") or "").strip(),
              "at": _at(g)}
             for g in fresh if (g.get("text") or "").strip()]
    live = str((r or {}).get("status", "")).lower() in ("active", "requested", "awaiting_admission")
    cursor = str(_at(segs[-1])) if segs else (since or "")
    # Addressed by row id there is no platform/native pair to build a UI link from unless the
    # row carries one — an empty string beats a well-formed link to nothing.
    _plat = platform or (r or {}).get("platform")
    _nat = (r or {}).get("native_meeting_id") if platform is None else mid
    return json.dumps({"ui_url": (ui_meeting_url(_plat, _nat) if _plat and _nat else ""),
                       "meeting": mid,
                       "status": (r or {}).get("status"),
                       "read_ok": True,
                       "cursor": cursor,
                       "new_segments": len(lines) if since else None,
                       "follow": ("Call me again in 20-30s with since=<cursor above> for only "
                                  "what is new. Do not build a watcher; there is nothing to "
                                  "diff." if live else
                                  "The meeting is over — this is the complete record."),
                       "nothing_new_means": ("The room is quiet, not broken — the read "
                                             "succeeded. Say so plainly, or say nothing and "
                                             "wait." if since and not lines else None),
                       "total_segments": len(segs), "showing": len(lines),
                       "next_options": ([
                           "Keep reading along — ask me anything about what is being said",
                           "Have the bot speak into the room (bot_say)",
                           "Stop the bot (bot_stop)",
                       ] if live else [
                           "Write this meeting up into the workspace (summary, decisions, "
                           "open questions) — I do it right here",
                           "Open it side-by-side in the terminal: deeplink(target='post_meeting', "
                           "ref='<platform/native|doc path>')",
                           "Search across all meetings for anything (transcript_search)",
                       ]),
                       "transcript": lines})


@tool
@anon_guard
def transcript_search(query: str, token: str = "") -> str:
    """Search every word this team's meetings have produced. 'What did we decide about the
    gateway?' starts here when the workspace does not already answer it."""
    uid = me()
    import urllib.parse as _up
    st, r = _gw_http(uid, "GET", "/transcripts/search?q=" + _up.quote(query))
    if st != 200:
        return json.dumps({"error": "search failed", "status": st, "detail": str(r)[:200]})
    hits = [{"meeting": h.get("native_meeting_id") or h.get("meeting_id"),
             "who": h.get("speaker"), "said": (h.get("text") or "")[:240],
             "at": h.get("absolute_start_time") or h.get("start")}
            for h in (r or {}).get("hits", [])[:25]]
    return json.dumps({"query": query, "count": (r or {}).get("count", len(hits)),
                       "hits": hits})


@tool
@anon_guard
def transcript_terms(meeting_id: str = "", since: str = "", keep: str = "",
                     meeting_url: str = "", token: str = "") -> str:
    """The things a meeting has NAMED so far — people, companies, projects, products, topics — each
    with where it was said and whether a page for it already exists.

    THIS IS THE HIGHLIGHT LAYER (PRD decision 35). The transcript view has a Highlight button; it
    posts a silent turn that calls this, decides which terms matter for THIS person and this chat,
    and publishes them. The reader then sees them as chips in the transcript: solid where a page
    exists (clicking opens it), dashed where none does (clicking asks you what it is).

    MECHANICAL — no model runs inside this tool. It is the same name extractor the write-back phase
    uses, matched against the entity index of the workspaces YOU can read. So a term it calls
    `known` is a page you can actually open, and one it calls unknown is a page decision 24 says you
    should be writing.

    TWO CALLS, AND THE SECOND ONE IS THE PUBLISH:

      1. `transcript_terms(meeting_id, since)` — LOOK. Returns every candidate. Nothing is shown to
         anyone yet. Read the list and pick the ones that matter here: a company in the deal, a
         person nobody has a page for, a product name that was decided on. Drop the ones that are
         just capitalised words.
      2. `transcript_terms(meeting_id, since, keep="Acme, Cottalango Leon")` — PUBLISH. Exactly those
         become chips in the transcript. `keep="*"` publishes everything, which is right only when
         everything genuinely matters.

    A first call publishes NOTHING on purpose: chips are on the person's screen, and a list nobody
    judged is a screen full of every capitalised word in the room.

    `since` is the CURSOR from your last call on this meeting — pass it back and you get only what
    has been said since, so pressing Highlight again adds new terms instead of re-listing the room.
    Omit it the first time.
    """
    uid = me()
    # MECHANICAL, AND IT RUNS WHERE THE PAGES ARE. The rig injected `core/agent` onto its own
    # sys.path to import `shared/terms`, then built the entity index from N HTTP reads behind two
    # caches with two TTLs (seam inventory B6.4, B1). agent-api already holds the workspaces, the
    # active-mount set and that same extractor, so the index is a directory walk there instead of a
    # fan-out here — and there is exactly one extractor, which is why a chip cannot open nothing.
    st, body = _http("GET", f"{AGENT_API}/api/transcript/terms" + _q(
        meeting_id=meeting_id, meeting_url=meeting_url, since=since, keep=keep),
        {"X-User-Id": uid})
    if st == 404:
        return json.dumps({"error": (body or {}).get("detail") if isinstance(body, dict)
                           else "no such meeting",
                           "do": "give meeting_id=<row id> or meeting_url=<link>"})
    if not (200 <= st < 300) or not isinstance(body, dict):
        return json.dumps({"error": "could not read the transcript", "read_ok": False, "status": st,
                           "tell_your_person": "Say the READ failed — never that nothing was said.",
                           "do": "try again in ~20 seconds; if it repeats, report_friction()"})
    return capped(body, 12000)


@tool
@anon_guard
def recordings_list(token: str = "") -> str:
    """Recordings this team's meetings have produced, when recording is on."""
    uid = me()
    st, r = _gw_http(uid, "GET", "/recordings")
    if st != 200:
        return json.dumps({"error": "could not list recordings", "status": st})
    return capped(r, 4000)


@tool
@anon_guard
def bot_send(meeting_url: str, bot_name: str = "", token: str = "") -> str:
    """Send a Vexa bot into a live meeting NOW. THE main verb — when your person hands you a
    meeting link, this is the call.

    The bot knocks within ~30 seconds; someone in the call admits it. From then on
    meeting_transcript(meeting_url) returns the words as they are spoken — read them into this
    conversation and work with them directly. The workspace machinery is optional."""
    uid = me()
    platform, mid = meeting_ref(meeting_url)
    if not platform:
        return json.dumps({"error": mid})
    # THE URL TRAVELS. Parsing gives a platform and a stable id to key on, but it is a
    # derivation and not a replacement: a Zoom link carries its passcode in ?pwd=, which no
    # downstream can reconstruct from the numeric id. Dropping it produced a refusal that asked
    # for the exact thing the person had already pasted.
    # resolved ONCE: the request and the sentence we say back must name the same bot. An
    # earlier cut resolved it inline and left the reply reading the raw empty parameter —
    # "the bot is at the door as ''".
    bot_name = bot_name or person_settings(uid).get("bot_name") or "Vexa"
    # What the sentence CALLS the meeting. The url is the only name we reliably have here, and it
    # is the one the person just handed us — so it reads back as theirs rather than as an id.
    title_for_say = (meeting_url or "").strip() or f"{platform}/{mid}"
    st, r = _gw_http(uid, "POST", "/bots",
                     {"platform": platform, "native_meeting_id": mid,
                      "meeting_url": meeting_url.strip(), "bot_name": bot_name})
    if st not in (200, 201):
        if st == 409:
            return json.dumps({"already_there": True,
                               "note": "a bot for this meeting is already up — go straight "
                                       "to meeting_transcript(meeting_url)"})
        return json.dumps({"error": "the bot could not be dispatched", "status": st,
                           "detail": str(r)[:300],
                           "do": "report_friction() with this, and tell your person in one "
                                 "plain sentence that the bot could not join."})
    # Wait the few seconds it takes to KNOW, instead of returning "requested" and leaving the
    # agent to poll a status field and interpret three states. A launch that is going to fail
    # (a missing image, a dead runtime) fails in this window — which is exactly the failure
    # that read as "the bot could not join" with no reason attached.
    # ONE CHECK, NO SLEEPING. An earlier version slept ~6s inside this call to be sure of the
    # answer; it blocked the server, broke the client's HTTP/2 stream with INTERNAL_ERROR and
    # killed the MCP session — reporting a failed send on a join that had actually succeeded.
    # A bot needs ~30s to be admitted anyway, so the wait bought almost nothing.
    # THE ROW ID, resolved here because this is the only place that has it cheaply. The create
    # response carries it; the status listing carries it again. Everything downstream that wants to
    # ADDRESS this meeting needs the row and not the native id — a personal room's native id spans
    # many meetings, so it names a series, not an occurrence. The harness emits the panel artifact
    # against `meeting_row`, and it cannot invent one (F73).
    state, detail, row = "knocking", "", (r or {}).get("id")
    stc, rc = _gw_http(uid, "GET", "/bots/status")
    if stc == 200:
        for b in (rc or {}).get("running_bots", []) or (rc or {}).get("running", []):
            if str(b.get("native_meeting_id")) == str(mid):
                row = row or b.get("id") or b.get("meeting_id")
                sv = str(b.get("status", "")).lower()
                if sv in ("active", "in_call", "recording"):
                    state, detail = "in_call", sv
                elif sv in ("failed", "exited"):
                    state, detail = "failed", sv
                break

    # STATE SENTENCES, AND NOT ONE LINK IN THEM (F73). This result used to carry `ui_url` and a
    # `tell_your_person` line ending in it, so the agent did the obvious thing and handed the
    # person a URL into the product they were already looking at. That is not a manners problem to
    # be fixed with an instruction — the tool was offering the link, labelled for exactly that use.
    # The panel is moved by the harness on this result; the sentence says what is about to happen.
    say = {
        "in_call": f"The bot is in the call as '{bot_name}' — the transcript is beside this chat.",
        "knocking": f"The bot is at the door of {title_for_say} as '{bot_name}'. Someone in the "
                    f"meeting has to let it in, same as any guest; the transcript opens beside "
                    f"this chat when it is admitted.",
        "failed": "The bot could not stay in the call. That is ours, not yours — I have "
                  "reported it.",
    }[state]

    # NO `ui_url`. The person is inside the app; a link into it is the one thing that cannot help
    # them, and a field named `ui_url` sitting in a tool result is an invitation to paste it.
    # `meeting_row` replaces it — the same meeting, addressed the way the panel addresses it.
    return json.dumps({
        "sent": True, "platform": platform, "meeting": mid, "meeting_row": row,
        "status": (r or {}).get("status"),
        "bot_state": state, "detail": detail,
        "tell_your_person": say,
        "then": ("Follow it with meeting_transcript(meeting_url) and pass the cursor back as "
                 "since=<cursor> every 20-30s. One call each time; never build a watcher."),
        "next_options": [
            "Read along live — I can tell you what is being said as it happens",
            "Have the bot say something into the room (bot_say)",
            "Pull the bot back out (bot_stop)",
        ],
    })


@tool
@anon_guard
def bot_stop(meeting_url: str, token: str = "") -> str:
    """Pull the bot out of a meeting. The transcript up to this moment stays readable."""
    uid = me()
    platform, mid = meeting_ref(meeting_url)
    if not platform:
        return json.dumps({"error": mid})
    st, r = _gw_http(uid, "DELETE", f"/bots/{platform}/{mid}")
    return json.dumps({"stopped": st == 200, "status": st,
                       "note": "meeting_transcript(meeting_url) still returns everything "
                               "captured up to now"})


@tool
@anon_guard
def bots_running(token: str = "") -> str:
    """Every bot this account has in a meeting right now."""
    uid = me()
    st, r = _gw_http(uid, "GET", "/bots/status")
    if st != 200:
        return json.dumps({"error": "could not list bots", "status": st})
    out = [{"meeting": b.get("native_meeting_id"), "platform": b.get("platform"),
            "status": b.get("status"), "url": b.get("constructed_meeting_url")}
           for b in (r or {}).get("running", [])]
    return json.dumps({"running": out})


@tool
@anon_guard
def bot_config(meeting_url: str, language: str = "", bot_name: str = "", token: str = "") -> str:
    """Adjust a bot already in a call: transcription language (e.g. 'es'), or its display
    name."""
    uid = me()
    platform, mid = meeting_ref(meeting_url)
    if not platform:
        return json.dumps({"error": mid})
    body = {}
    if language:
        body["language"] = language
    if bot_name:
        body["bot_name"] = bot_name
    if not body:
        return json.dumps({"error": "give language= and/or bot_name="})
    st, r = _gw_http(uid, "PUT", f"/bots/{platform}/{mid}/config", body)
    return json.dumps({"applied": st == 200, "status": st,
                       "detail": None if st == 200 else str(r)[:200]})


@tool
@anon_guard
def bot_say(meeting_url: str, text: str, asked_by_a_human: bool = False,
            token: str = "") -> str:
    """Have the bot SPEAK into the live call — a sentence read aloud to everyone in the room.

    Requires asked_by_a_human=true: pass it only when your person actually asked for these
    words to be said out loud, and say them verbatim. A required field cannot be skimmed
    past the way a warning paragraph can — and this tool is one call away from being
    audible to real people."""
    uid = me()
    if not asked_by_a_human:
        return json.dumps({
            "refused": "bot_say needs asked_by_a_human=true",
            "why": "this speaks out loud to everyone in a real meeting; it is not a place "
                   "for an agent's own initiative",
            "do": "only set it when your person asked for these exact words to be said",
        })
    platform, mid = meeting_ref(meeting_url)
    if not platform:
        return json.dumps({"error": mid})
    st, r = _gw_http(uid, "POST", f"/bots/{platform}/{mid}/speak", {"text": text[:500]})
    if st != 200:
        return json.dumps({"error": "the bot could not speak", "status": st,
                           "detail": str(r)[:200],
                           "do": "tell your person in one plain sentence, and "
                                 "report_friction()"})
    return json.dumps({"spoke": True, "text": text[:500]})


@tool
@anon_guard
def bot_schedule(meeting_url: str, in_minutes: int = 0, at_epoch: float = 0,
                 at_local: str = "", tz: str = "",
                 title: str = "", cancel: bool = False, token: str = "") -> str:
    """Book the bot to join a meeting LATER, or call that booking off with cancel=True.

    ALWAYS PASS tz — the person's IANA zone ("Europe/Lisbon"), which you know from their
    environment. Then say a time the way they said it: at_local="17:10" or "2026-09-01 17:10"
    is read in THEIR clock, and everything said back to you carries its zone. Do not convert
    times yourself; that arithmetic is where silent, late errors come from.

    in_minutes (from now) and at_epoch (unix seconds) still work. The booking lives on the
    server, so it does not depend on this conversation, this client, or this laptop staying
    alive. The person gets an acknowledgment email; after the call the write-up runs on its own.

    cancel=True with the same meeting_url calls off whatever was booked for that meeting —
    no id to find, no queue to read."""
    uid = me()
    platform, mid = meeting_ref(meeting_url)
    if not platform:
        return json.dumps({"error": mid})
    if cancel:
        rows, err = _scheduled_joins(mid)
        if err is not None:
            return json.dumps({
                "read_ok": False, "error": "could not check what is booked for that meeting",
                "detail": err,
                "tell_your_person": "Say the CHECK failed — never that nothing is booked. "
                                    "You do not know that.",
                "do": "report_friction() with this."})
        if not rows:
            return json.dumps({"read_ok": True, "cancelled": 0,
                               "tell_your_person": "Nothing is booked for that meeting."})
        gone = 0
        for r in rows:
            st, _b = _http("POST", f"{FLOWS_API}/reactions/{r['id']}/cancel", _fkey(), {})
            gone += 1 if st in (200, 204) else 0
        if gone == 0:
            return json.dumps({"read_ok": True, "cancelled": 0, "found": len(rows),
                               "error": "found the booking but could not cancel it",
                               "do": "report_friction(); tell them it is still booked."})
        return json.dumps({
            "read_ok": True, "cancelled": gone, "meeting": f"{platform}/{mid}",
            "tell_your_person": "One line: that one is called off, the bot will not join.",
            "next_options": ["Book it for a different time",
                             "Send the bot in now instead — paste the link again",
                             "Nothing else"]})
    their_tz = person_tz(uid, tz) or person_tz(uid)
    if at_local and not at_epoch:
        import datetime
        try:
            import zoneinfo
            z = zoneinfo.ZoneInfo(their_tz) if their_tz else datetime.timezone.utc
        except Exception:  # noqa: BLE001
            z = datetime.timezone.utc
        txt = at_local.strip()
        now_there = datetime.datetime.now(z)
        parsed = None
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%H:%M", "%H%M"):
            try:
                t = datetime.datetime.strptime(txt, fmt)
            except ValueError:
                continue
            if fmt in ("%H:%M", "%H%M"):
                t = now_there.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
                if t < now_there:                       # a time already gone means tomorrow
                    t += datetime.timedelta(days=1)
            else:
                t = t.replace(tzinfo=z)
            parsed = t
            break
        if parsed is None:
            return json.dumps({
                "error": f"could not read the time {at_local!r}",
                "give_me": "HH:MM, or YYYY-MM-DD HH:MM — in their own clock, with tz set",
            })
        at_epoch = parsed.timestamp()

    start = float(at_epoch) if at_epoch else time.time() + max(1, int(in_minutes)) * 60
    if start < time.time() - 60:
        return json.dumps({"error": "that time is in the past",
                           "their_clock": in_their_clock(start, their_tz)})
    # NEVER INVENT AN ADDRESS. The old fallback, f"user-{uid}@unknown", was handed to the
    # invite flow, which provisioned a real account for it — so the bot joined under a person
    # who did not exist and the meeting was invisible to the one who asked for it. A refusal is
    # recoverable; a silent second account is not.
    email = caller_email()
    if not email:
        return json.dumps({
            "error": "cannot tell which account this is",
            "do": "report_friction() with this — it is ours. Do not create an account and do "
                  "not ask your person for their email; they are already signed in.",
        })
    sid_ev = f"sched-{mid}-{int(start)}"
    res = json.loads(fact_emit(
        event_type="invite.received", source_event_id=sid_ev,
        subject_refs={"organizer": email, "url": meeting_url, "start": start,
                      "ics_uid": sid_ev, "title": title or f"Scheduled: {mid}",
                      "group": None}))
    if not res.get("admitted"):
        return json.dumps({"error": "the schedule could not be filed",
                           "detail": str(res)[:200], "do": "report_friction() with this"})
    when = in_their_clock(start, their_tz)
    return json.dumps({
        "scheduled": True, "meeting": f"{platform}/{mid}", "joins_at": when,
        "durable": "this lives in the flows engine on the server — nothing on your side "
                   "needs to stay open",
        "tell_your_person": f"The bot will join {mid} at {when} (it heads in ~2 minutes "
                            f"early). An acknowledgment lands in their inbox; after the "
                            f"call the write-up happens on its own.",
        "next_options": [
            "Call it off — say so and it is cancelled",
            "Send the bot in now as well",
            "Nothing — it runs by itself from here",
        ],
    })


@tool
@anon_guard
def captions_to_segments(video_id: str, max_minutes: int = 45, token: str = "") -> str:
    """Turn a downloaded YouTube caption track into speaker-attributed meeting segments.

    Auto-captions carry no diarization, so turns are cut on silence gaps and labelled
    Speaker 1..N rather than inventing identities — which is also what our own pipeline
    produces before attribution runs. Source stays in ~/.storm/caps/<id>.en.json3."""
    me()   # account-scoped: this touches shared state
    src = config.CAPS_DIR / f"{video_id}.en.json3"
    if not src.exists():
        return json.dumps({"error": f"no captions at {src}"})
    data = json.loads(src.read_text())
    events = [e for e in data.get("events", []) if e.get("segs")]
    turns, cur, speaker, last_end, start_t = [], [], 1, 0.0, 0.0

    def flush():
        nonlocal cur, speaker
        if cur:
            turns.append((start_t, last_end, f"Speaker {speaker}", " ".join(cur)))
            speaker = speaker % 6 + 1
            cur = []

    for e in events:
        t0 = e.get("tStartMs", 0) / 1000.0
        if t0 > max_minutes * 60:
            break
        text = "".join(s.get("utf8", "") for s in e["segs"]).strip()
        if not text or text == "\n":
            continue
        # Auto-captions run continuous, so a silence gap alone almost never fires: cut on a
        # gap OR on turn length. Without the second rule 40 minutes collapses into 7 turns of
        # 900 words each, which is not what a meeting sounds like.
        if cur and ((t0 - last_end) > 0.8 or len(" ".join(cur).split()) > 55):
            flush()
        if not cur:
            start_t = t0
        cur.append(text)
        last_end = t0 + e.get("dDurationMs", 2000) / 1000.0
    flush()

    config.CAPS_DIR.mkdir(parents=True, exist_ok=True)
    out = config.CAPS_DIR / f"{video_id}.segments.json"
    out.write_text(json.dumps([{"start": a, "end": b, "speaker": sp, "text": t}
                               for a, b, sp, t in turns]))
    words = sum(len(t.split()) for _, _, _, t in turns)
    # truncate the SAMPLE, never the payload -- slicing the rendered JSON produces invalid
    # JSON and the caller silently gets a string instead of a result.
    return json.dumps({"video_id": video_id, "turns": len(turns), "words": words,
                       "speakers": len({sp for _, _, sp, _ in turns}),
                       "minutes": round(turns[-1][1] / 60, 1) if turns else 0,
                       "written": str(out),
                       "sample": [t[:180] for _, _, _, t in turns[:3]]})


@tool
@anon_guard
def zoom_transcript_to_segments(name: str, path: str, token: str = "") -> str:
    """Convert a Zoom/LFX machine transcript into segments, keeping the REAL speaker labels.

    Lines look like `[00:00:10.620 --> 00:00:12.689] Cottalango Leon (Sony Pictures Imageworks):
    text`. Unlike YouTube auto-captions this carries genuine diarization and company
    affiliations, so it exercises attribution the way a real capture does. Consecutive lines
    from one speaker are merged into a turn."""
    me()   # account-scoped: this touches shared state
    import re
    src = pathlib.Path(path)
    if not src.exists():
        return json.dumps({"error": f"no transcript at {path}"})
    pat = re.compile(r"^\[(\d+):(\d+):([\d.]+)\s*-->\s*(\d+):(\d+):([\d.]+)\]\s*([^:]{1,60}?):\s*(.*)$")
    turns = []
    for raw in src.read_text().splitlines():
        mm = pat.match(raw.strip())
        if not mm:
            continue
        h1, m1, s1, h2, m2, s2, sp, text = mm.groups()
        a = int(h1) * 3600 + int(m1) * 60 + float(s1)
        b = int(h2) * 3600 + int(m2) * 60 + float(s2)
        sp, text = sp.strip(), text.strip()
        if not text:
            continue
        if turns and turns[-1][2] == sp and len(turns[-1][3].split()) < 60:
            turns[-1] = (turns[-1][0], b, sp, turns[-1][3] + " " + text)
        else:
            turns.append((a, b, sp, text))
    config.CAPS_DIR.mkdir(parents=True, exist_ok=True)
    out = config.CAPS_DIR / f"{name}.segments.json"
    out.write_text(json.dumps([{"start": a, "end": b, "speaker": sp, "text": t}
                               for a, b, sp, t in turns]))
    from collections import Counter
    who = Counter(sp for _, _, sp, _ in turns)
    return json.dumps({"name": name, "turns": len(turns),
                       "words": sum(len(t.split()) for _, _, _, t in turns),
                       "speakers": [{"name": k, "turns": v} for k, v in who.most_common(10)],
                       "minutes": round(turns[-1][1] / 60, 1) if turns else 0,
                       "written": str(out)})
