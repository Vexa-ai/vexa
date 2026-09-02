"""`rehearse(state, as, meeting, when)` and `subject_reset(address)` — PRD decision 38.2 / 38.3.

    rehearse("organizer-invited", "olga@rehearse.test")
      → the person exists, the desk exists, the invite is in the mail double, and the prepare
        mail's `/?s=` link is in the return value. Nothing was clicked and nothing was rebuilt.

TWO REFUSALS COME BEFORE THE FIRST DOOR, and both are about the same thing — this runs on a stack
somebody else's work is living on:

  1. **Every address must be under `$VEXA_REHEARSE_DOMAIN`** (default `rehearse.test`). Not just
     the subject: the check runs over the fully interpolated arguments of every step, so an
     organizer, an attendee or a group member outside the domain stops the run. The founder's
     identities, `_global`, the DNA and OeNB groups are unreachable by construction rather than by
     care.
  2. **No live meeting may belong to a real subject.** A rehearsal writes facts and mail; a live
     meeting is the one thing on this stack that cannot be re-recorded. The probe fails closed.

IDEMPOTENCE IS BY DERIVED IDENTITY, not by a marker. The ICS UID, the fact's `source_event_id` and
the meeting's native id are all functions of (state, subject, meeting), so the second run of a
state dedups where the product already dedups — the mail poller on the ICS UID, `admit()` on
(source_event_id, flow), `user_ensure` on the address. There is nowhere for a marker to live that
a `subject_reset` would not also have to know about, and a marker that gets out of step with the
data is worse than none.
"""
from __future__ import annotations

import json
import pathlib
import re
import time
from dataclasses import dataclass, field
from typing import Any

from . import catalogue as cat
from .doors import DoorRefused, Doors

DEFAULT_MEETING = "2026-03-02"
# FAR ENOUGH OUT THAT A RUN CANNOT OUTLIVE IT. `invite_intake` parks on `await_start` until
# start−2min and then dispatches a REAL bot at the invite's URL. At +30m a catalogue run — three
# states with a 677-segment import and an agent turn each — reached start−2min while it was still
# going, and a bot was dispatched at the fixture Zoom URL (meeting 115, status `joining`, 2026-09-02
# 19:20Z). +3h is the ledger's own figure, chosen for this exact reason at station 2; not carrying
# it over is what cost this.
#
# The default is a floor, not the fix: `cancel_bot_leg` below is the fix, because a rehearsal must
# leave nothing armed no matter how long it ran.
DEFAULT_WHEN = "+3h"


class Refused(RuntimeError):
    """A guard said no. The message is the whole product — it names what is in the way."""


@dataclass
class Result:
    state: str
    subject: str
    ok: bool = False
    links: dict = field(default_factory=dict)
    mails: list = field(default_factory=list)
    subjects: dict = field(default_factory=dict)
    meeting_row: dict = field(default_factory=dict)
    verify: list = field(default_factory=list)
    steps: list = field(default_factory=list)
    wall_s: float = 0.0
    error: str = ""

    def to_dict(self) -> dict:
        return {"state": self.state, "as": self.subject, "ok": self.ok, "links": self.links,
                "mails": self.mails, "subjects": self.subjects, "meeting_row": self.meeting_row,
                "verify": self.verify, "steps": self.steps, "wall_s": round(self.wall_s, 1),
                **({"error": self.error} if self.error else {})}


# ── the guards ───────────────────────────────────────────────────────────────────────────────────

_ADDR = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def addresses_in(value: Any) -> list[str]:
    """Every email-shaped string anywhere in an interpolated argument tree."""
    out: list[str] = []
    if isinstance(value, dict):
        for v in value.values():
            out += addresses_in(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            out += addresses_in(v)
    elif isinstance(value, str):
        out += _ADDR.findall(value)
    return out


def guard_domain(addresses, domain: str, mailbox: str = "") -> None:
    """Refuse anything outside the test domain — the whole safety story, in one function.

    `mailbox` is the address the deployment's own mail double answers as (`VEXA_MAIL_ADDR`). It is
    the product's identity, not a person's, and every invite is addressed to it; excluding it here
    is the one exception and it is named rather than pattern-matched.

    IT CHECKS THE SHAPE, NOT ONLY THE SUFFIX. `endswith("@" + domain)` passes anything ending in
    those characters and says nothing about the rest, so a value that is not an address at all —
    a timestamp, an empty local part, a whole sentence — is judged only on its tail. That is how
    a domainless account (`20260902t183213z`) reached the instance on 2026-09-02: not through this
    guard, which never saw it, but the guard could not have stopped it either, and a safety check
    that would have waved it through is not one.
    """
    def refused(value: str) -> bool:
        a = value.lower().strip()
        if a == mailbox.lower().strip() and mailbox:
            return False
        local, sep, host = a.rpartition("@")
        # No whitespace anywhere: `a b@rehearse.test` has the right tail and is not an address,
        # and the display form `Real Person <a@b.test>` is the shape a header hands you unparsed.
        return not (sep and local and host == domain and not any(c.isspace() for c in a))

    bad = sorted({a.lower() for a in addresses if refused(a)})
    if bad:
        raise Refused(
            f"refusing: {', '.join(bad)} " + ("is" if len(bad) == 1 else "are") +
            f" not under @{domain}. A rehearsal writes real facts and real mail on a stack real "
            f"people are using; the test domain is the only thing keeping it off them. Set "
            f"VEXA_REHEARSE_DOMAIN to change the domain, never to reach outside one.")


def guard_no_live_real_meeting(doors: Doors, domain: str) -> list:
    live = doors.live_meetings()
    real = [m for m in live if not str(m.get("email", "")).lower().endswith("@" + domain)]
    if real:
        who = "; ".join(f"meeting {m['id']} ({m['status']}) — {m['email']}" for m in real[:5])
        raise Refused(
            f"refusing: {len(real)} live meeting(s) belong to a real subject — {who}. A live "
            f"meeting is the only thing on this stack that cannot be re-recorded. Wait for it to "
            f"finish.")
    return live


# ── fixtures ─────────────────────────────────────────────────────────────────────────────────────

def load_fixture(fixtures_dir: pathlib.Path, meeting: str) -> dict:
    p = pathlib.Path(fixtures_dir).expanduser() / f"{meeting}.transcript.json"
    if not p.is_file():
        have = sorted(x.name.split(".")[0] for x in
                      pathlib.Path(fixtures_dir).expanduser().glob("*.transcript.json")) \
            if pathlib.Path(fixtures_dir).expanduser().is_dir() else []
        raise Refused(f"no fixture {p}. Available: {', '.join(have) or 'none'}")
    fx = json.loads(p.read_text())
    m = fx.get("meeting") or {}
    segs = [{"start": float(s.get("t", s.get("start", 0.0))), "end": float(s.get("end", 0.0)),
             "speaker": s.get("speaker") or "?", "text": s.get("text") or ""}
            for s in fx.get("segments") or []]
    return {"title": m.get("title") or meeting, "native": m.get("native_meeting_id") or meeting,
            "participants": list(m.get("participants") or []), "segments": segs}


def attendee_address(display_name: str, domain: str) -> str:
    """A fixture's speaker label → a rehearse-domain address.

    "Olga Avramenko (Sony Pictures Imageworks)" → olga-avramenko@rehearse.test. The org in
    parentheses is dropped: it is the person's employer, not part of their name, and putting it in
    the local part would make the room read as a set of companies.
    """
    base = re.sub(r"\(.*?\)", " ", display_name or "").strip().lower()
    base = re.sub(r"[^a-z0-9]+", "-", base).strip("-") or "someone"
    return f"{base[:40]}@{domain}"


def parse_when(when: str, now: float | None = None) -> float:
    """`+30m` / `+3h` / `-45m` / an epoch / an ISO stamp → an epoch.

    A RELATIVE value is the normal case and the default is `+30m` on purpose: `invite_intake`
    parks on `await_start` until start-2min, so a future start means the prepare touch fires at
    once and no bot is ever dispatched at a fixture URL. A past start would spawn a real bot that
    cannot join, and the flow would fail non-retryably — the state would look broken when the
    recipe was.
    """
    now = time.time() if now is None else now
    s = str(when).strip()
    m = re.fullmatch(r"([+-])\s*(\d+)\s*([smhd])", s)
    if m:
        mult = {"s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(3)]
        delta = int(m.group(2)) * mult
        return now + (delta if m.group(1) == "+" else -delta)
    try:
        return float(s)
    except ValueError:
        pass
    try:
        import datetime as dt
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        raise Refused(f"{when!r} is not a time. Use +30m, +3h, an epoch, or an ISO-8601 stamp. "
                      f"A stamp we silently defaulted would put the meeting at the wrong moment, "
                      f"which is the one thing the argument exists to control.") from None


# ── the executor ─────────────────────────────────────────────────────────────────────────────────

def rehearse(state: str, as_: str, meeting: str = DEFAULT_MEETING, when: str = DEFAULT_WHEN, *,
             doors: Doors, catalog: cat.Catalogue | None = None, env: dict | None = None,
             mailbox: str = "", dry_run: bool = False, fresh: bool = False,
             runner: str = "") -> Result:
    """Enter `state` as `as_`, on the running stack, through the product's own doors.

    `runner` (PRD decisions 37 + 38) pins every subject this recipe resolves to a harness — today
    `openai-agent` against the CCC box's Qwen — at the moment it resolves them, so the binding is
    in place before any dispatch that subject could cause. It is PER SUBJECT: it writes that
    person's model config through admin-api, never the platform setting, so the founder's
    dispatches are untouched by construction rather than by care. `doors.RUNNER_DIALS` is the
    vocabulary and an unknown name is refused with the list.

    `fresh=True` resets the room this recipe owns first — the subject AND the organizer the recipe
    derives from them — so a state that cannot be re-entered idempotently (a stranger stops being
    one the moment the drop gives them a desk) can be entered again from nothing. It is off by
    default because a reset DELETES, and deleting is never a side effect of asking for a state.
    """
    started = time.time()
    catalog = catalog or cat.load()
    domain = catalog.domain(env)
    st = catalog[state]
    subject = str(as_).strip().lower()
    res = Result(state=state, subject=subject)

    guard_domain([subject], domain, mailbox)
    if runner:
        # Fail on the NAME before anything is touched: the same reason the domain guard is first.
        from .doors import runner_config
        runner_config(runner)
    if fresh and not dry_run:
        # The room a recipe owns is exactly two addresses: the subject, and the organizer derived
        # from them. Both are under the guarded domain by construction, and `subject_reset` re-runs
        # its own refusal on each — one function, one rule, never a second spelling of it.
        res.steps.append({"do": "(fresh)", "door": "reset", "ok": True, "out": [
            subject_reset(a, doors=doors, catalog=catalog, env=env)
            for a in (subject, f"organizer-{subject.split('@')[0]}@{domain}")]})
    fixture = load_fixture(catalog.fixtures_dir(env), meeting)
    start_epoch = parse_when(when)

    attendees = [(n, attendee_address(n, domain)) for n in fixture["participants"]]
    local = subject.split("@")[0]
    bindings: dict[str, Any] = {
        "state": state, "meeting": meeting, "when": start_epoch, "domain": domain,
        "subject": subject, "subject_local": local,
        "organizer": f"organizer-{local}@{domain}",
        "title": fixture["title"], "native": fixture["native"],
        "fixture_attendees": [a for _, a in attendees] + [subject],
        "fixture_attendee_names": {a: n for n, a in attendees},
        "_attendee_pairs": attendees,
        # The floor every `await_mail` measures against — see `_execute`.
        "_started": started,
    }

    # Everything the recipe will say, resolved as far as it can be, BEFORE anything is done. This
    # is what makes the domain guard TOTAL: it sees the organizer the recipe derives and the
    # attendees the fixture supplies, not only the address the caller typed. Tokens that only a
    # later step can bind render as `<unbound>` rather than hiding their whole row from the check.
    plan = [(s, cat.interpolate(s.args, bindings, lenient=True)) for s in st.steps]
    guard_domain(addresses_in([a for _, a in plan] + [bindings]), domain, mailbox)

    if dry_run:
        res.ok = True
        res.steps = [{"do": s.do, "door": s.door, "planned": a} for s, a in plan]
        res.wall_s = time.time() - started
        return res

    guard_no_live_real_meeting(doors, domain)

    try:
        for step in st.steps:
            t0 = time.time()
            args = cat.interpolate(step.args, bindings)
            out = _execute(step, args, doors, bindings, fixture, start_epoch)
            if runner and step.do == "user_ensure" and isinstance(out, dict) and out.get("uid"):
                # The moment a subject exists is the moment to bind their harness — before any
                # step can cause a dispatch for them. Binding afterwards would leave the turns the
                # recipe itself triggers (the post-meeting run, the prepare compose) on the
                # deployment's default, which is precisely the thing being measured.
                out["runner"] = doors.bind_runner(out["uid"], runner)["runner"]
            if step.capture:
                bindings[step.capture] = out
            res.steps.append({"do": step.do, "door": step.door, "ok": True,
                              "s": round(time.time() - t0, 1),
                              "out": _brief(out)})
            _absorb(res, step, out)
        res.verify = [_verify(row, bindings, doors, res) for row in
                      [cat.interpolate(v, bindings) for v in st.verify]]
        res.ok = all(v["ok"] for v in res.verify)
    except (DoorRefused, cat.CatalogueError, Refused) as e:
        res.ok = False
        res.error = str(e)
        res.steps.append({"do": "(stopped)", "door": "", "ok": False, "why": str(e)})
    res.subjects = {k: v for k, v in bindings.items()
                    if isinstance(v, dict) and "uid" in v and "email" in v}
    res.wall_s = time.time() - started
    return res


def _brief(out: Any) -> Any:
    if isinstance(out, dict):
        return {k: (v[:120] + "…" if isinstance(v, str) and len(v) > 120 else v)
                for k, v in out.items() if k not in ("text", "html", "body")}
    return out


def _absorb(res: Result, step: cat.Step, out: Any) -> None:
    """Fold a step's output into the caller-facing shape (links · mails · meeting_row)."""
    if not isinstance(out, dict):
        return
    if step.do == "await_mail":
        res.mails.append({"to": out.get("to"), "subject": out.get("subject"),
                          "id": out.get("id"), "links": out.get("links")})
    elif step.do == "seed_meeting":
        res.meeting_row = out
    # `res.links` is filled by the `link_present` CHECKS, not here: a link is named only once it
    # has been matched against the pattern the recipe declared. Listing every URL in a mail body
    # as "the link" would hand the caller an unsubscribe footer with the same confidence.


def _execute(step: cat.Step, args: dict, doors: Doors, bindings: dict, fixture: dict,
             start_epoch: float) -> Any:
    v = step.do
    if v == "require_instance_blank":
        return doors.require_instance_blank()
    if v == "require_subject_absent":
        return doors.require_subject_absent(args["address"])
    if v == "user_ensure":
        return doors.user_ensure(args["address"])
    if v == "desk_init":
        return doors.desk_init(str(args["subject"]))
    if v == "desk_entity":
        return doors.desk_entity(str(args["subject"]), args["kind"], args["name"],
                                 facts=args.get("facts") or (), source=args.get("source") or "",
                                 summary=args.get("summary") or "", slug=args.get("slug") or "")
    if v == "group_new":
        return doors.group_new(str(args["owner"]), args["name"], args.get("purpose") or "")
    if v == "group_join":
        return doors.group_join(str(args["group"]), str(args["owner"]), str(args["member"]),
                                args.get("member_email") or "",
                                args.get("role") or "contributor")
    if v == "request_sign_in_link":
        return doors.request_sign_in_link(args["address"])
    if v == "drop_invite":
        att = list(bindings["_attendee_pairs"]) if args.get("attendees_from_fixture") \
            else [tuple(x) for x in (args.get("attendees") or [])]
        return doors.drop_invite(args["organizer"], args["title"], float(args["start"]),
                                 attendees=att, ics_uid=args.get("ics_uid") or "",
                                 group=args.get("group") or "")
    if v == "seed_meeting":
        return doors.seed_meeting(str(args["owner"]), args["native"],
                                  args.get("title") or fixture["title"], fixture["segments"],
                                  float(args.get("started_at") or (start_epoch - 3600)),
                                  source=str(args.get("source") or "seed"))
    if v == "emit_fact":
        return doors.emit_fact(args["event_type"], args["source_event_id"], args["refs"])
    if v == "await_mail":
        # `since` IS THE CHECK. Without it the step matched a message a PREVIOUS run had sent —
        # found live on run 2, where `warm-desk-recurring` "found" run 1's Prepare mail, verified
        # run 1's scaffold, and reported a state this run had not produced. A touch is evidence
        # only if it is this run's touch; the floor is when this run started, and a recipe may
        # raise it but never lower it.
        return doors.await_mail(args["to"], args.get("subject_contains") or "",
                                int(args.get("budget_s") or 180),
                                since=float(args.get("since") or bindings["_started"]))
    if v == "reply_to_mail":
        return doors.reply_to_mail(bindings[args["to_mail"]], args["from_address"], args["body"])
    if v == "await_reaction":
        return doors.await_reaction(args["flow"], float(args.get("since") or 0.0),
                                    int(args.get("budget_s") or 300))
    if v == "cancel_bot_leg":
        return doors.cancel_bot_leg(args["flow"], args.get("source_contains") or "")
    raise cat.CatalogueError(f"no executor for verb {v!r} — catalogue.VERBS and engine._execute "
                             f"have drifted apart")


def _verify(row: dict, bindings: dict, doors: Doors, res: Result) -> dict:
    kind = row["check"]
    out = {"check": kind, "ok": False, "detail": ""}
    try:
        if kind == "mail_present":
            m = bindings[str(row["of"]).split(".")[0]]
            out["ok"] = bool(m.get("id"))
            out["detail"] = str(m.get("subject") or "")
        elif kind == "link_present":
            m = bindings[str(row["of"]).split(".")[0]]
            pat = re.compile(row["must_match"])
            hit = next((u for u in m.get("links") or [] if pat.search(u)), "")
            out["ok"] = bool(hit)
            out["detail"] = hit or f"no link matching {row['must_match']} in {m.get('subject')!r}"
            if hit and row.get("as"):
                res.links[str(row["as"])] = hit
                bindings.setdefault("_links", {})[str(row["as"])] = hit
        elif kind == "scaffold_resolves":
            url = res.links.get(str(row["link"])) or bindings.get("_links", {}).get(str(row["link"]), "")
            sid = _scaffold_id(url)
            if not sid:
                out["detail"] = f"no ?s= id in {url!r}"
            else:
                rec = doors.scaffold_get(sid)
                out["ok"] = True
                out["detail"] = f"{sid[:12]}… kind={rec.get('kind')}"
                if row.get("kind") and rec.get("kind") != row["kind"]:
                    out["ok"] = False
                    out["detail"] += f" — expected kind {row['kind']}"
                if row.get("desk_state"):
                    # `refs.state` is an OBJECT — `{"desk": "new|pile|warm", "group": …}` — and
                    # reading it as a string could never match, so the check FAILED on a state that
                    # had worked. A check that cannot pass is worse than no check: it reports a
                    # product defect where there is only a reader that guessed a shape.
                    state = (rec.get("refs") or {}).get("state")
                    got = state.get("desk") if isinstance(state, dict) else state
                    if got != row["desk_state"]:
                        out["ok"] = False
                        out["detail"] += f" — desk state {got!r}, expected {row['desk_state']!r}"
        elif kind == "no_user":
            uid = doors.user_find(row["address"])
            out["ok"] = uid is None
            out["detail"] = "absent" if uid is None else f"uid {uid} exists"
        elif kind == "user_exists":
            uid = doors.user_find(row["address"])
            out["ok"] = uid is not None
            out["detail"] = f"uid {uid}" if uid else "absent"
        elif kind == "meeting_status":
            m = bindings[str(row["of"]).split(".")[0]]
            out["ok"] = str(m.get("status")) == str(row["is"])
            out["detail"] = f"status={m.get('status')}"
        elif kind == "desk_nonempty":
            files = doors.desk_tree(str(row["subject"]), str(row.get("slug") or ""))
            real = [f for f in files if f and not f.startswith(".")]
            out["ok"] = bool(real)
            out["detail"] = f"{len(real)} file(s)"
        elif kind == "group_member":
            members = doors.group_members(str(row.get("owner") or ""), str(row["group"]))
            out["ok"] = any(m.get("email") == row["address"] for m in members)
            out["detail"] = f"{len(members)} member(s)"
        elif kind == "reaction_admitted":
            r = bindings[str(row["of"]).split(".")[0]]
            out["ok"] = bool(r.get("admitted"))
            out["detail"] = f"{r.get('flow')} state={r.get('state')}"
        else:
            out["detail"] = f"no runner for check {kind!r}"
    except (DoorRefused, KeyError, cat.CatalogueError) as e:
        out["detail"] = f"{type(e).__name__}: {e}"
    return out


def _scaffold_id(url: str) -> str:
    m = re.search(r"[?&]s=([A-Za-z0-9_\-]+)", url or "")
    return m.group(1) if m else ""


# ── the reset ────────────────────────────────────────────────────────────────────────────────────

def subject_reset(address: str, *, doors: Doors, catalog: cat.Catalogue | None = None,
                  env: dict | None = None) -> dict:
    """Remove one subject entirely — user, desk, sessions, scaffolds, friction, mail.

    A state is re-entered in seconds and the instance is never blanked (decision 38.3). The
    refusal is the same one `rehearse` uses and it comes first: a non-test address is not reset,
    ever, and the tool says so rather than doing part of it.

    It VERIFIES EMPTINESS AFTERWARDS and reports what it could not remove. A reset that half
    worked and said "done" is the failure this whole file is built around — the ledger's phantom
    `_global` write, one layer down.
    """
    catalog = catalog or cat.load()
    domain = catalog.domain(env)
    address = str(address).strip().lower()
    guard_domain([address], domain)

    out: dict = {"address": address, "removed": {}, "remaining": {}, "ok": False}
    uid = doors.user_find(address)
    out["uid"] = uid

    # Order matters: the desk and the redis keys are addressed BY uid, so the user goes last.
    if uid:
        try:
            out["removed"]["meetings"] = doors.meetings_delete_for(uid)
        except DoorRefused as e:
            out["remaining"]["meetings"] = str(e)
        try:
            out["removed"]["desk"] = doors.desk_delete(uid)
        except DoorRefused as e:
            out["remaining"]["desk"] = str(e)
        try:
            out["removed"]["sessions"] = doors.session_keys_delete(uid)
        except DoorRefused as e:
            out["remaining"]["sessions"] = str(e)
        try:
            out["removed"]["friction"] = doors.friction_delete_for(uid)
        except DoorRefused as e:
            out["remaining"]["friction"] = str(e)
        try:
            # The lane's dedup memory. Without this the subject is gone and the state still
            # cannot be re-entered: `admit()` swallows the next invite as a duplicate and the
            # touch that should follow is simply never sent.
            out["removed"]["lane_rows"] = doors.lane_rows_delete_for(uid, address)
        except DoorRefused as e:
            out["remaining"]["lane_rows"] = str(e)
    try:
        out["removed"]["scaffolds"] = doors.scaffold_keys_delete(address)
    except DoorRefused as e:
        out["remaining"]["scaffolds"] = str(e)
    try:
        out["removed"]["mail"] = doors.mail_delete_for(address)
    except DoorRefused as e:
        out["remaining"]["mail"] = str(e)
    if uid:
        try:
            out["removed"]["user"] = doors.user_delete(uid)
        except DoorRefused as e:
            out["remaining"]["user"] = str(e)

    # PROVE IT. Reading back is the whole difference between a reset and a claim of one.
    still = doors.user_find(address)
    if still:
        out["remaining"]["user_still_exists"] = still
    left_scaffolds = 0
    try:
        left_scaffolds = doors.scaffold_keys_delete(address)
    except DoorRefused:
        pass
    if left_scaffolds:
        out["remaining"]["scaffold_keys"] = left_scaffolds
    out["ok"] = not out["remaining"]
    return out
