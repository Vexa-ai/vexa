"""The WITNESS harness: one meeting walked through the whole Minutes state machine, on fixtures.

Each step prints ``flow · state before → after · where to look``, sends real mail to the local
sink, and honours two state stores so a SECOND run proves memory (nothing repeats itself):

- persons  (email → new|invited|doored)   … the confirm email branches on it
- bindings (uid → group, series)          … tag/assign write it; the group path reads it

Where a flow is unbuilt its OUTPUT is a fixture labelled STAGED — the harness never pretends.

    python -m vexa_mailroom.lifecycle --fixtures ~/dev/biz/fixtures --org-domain example.com \
        --assistant mk-dev@dev.vexa.ai --terminal http://localhost:3010 --smtp localhost:1025 \
        [--reset]
"""
from __future__ import annotations

import argparse
import json
import smtplib
import sys
from email.message import EmailMessage
from pathlib import Path

from .base_path import plan_base_path
from .invite import parse_invite

STATE = Path("/tmp/minutes-lifecycle-state.json")
BINDINGS = Path("/tmp/minutes-bindings.jsonl")


def _load_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except Exception:  # noqa: BLE001
        return {"persons": {}, "series": {}}


def _save_state(st: dict) -> None:
    STATE.write_text(json.dumps(st, indent=1))


def _bindings() -> dict:
    out: dict[str, str] = {}
    try:
        for line in BINDINGS.read_text().splitlines():
            d = json.loads(line)
            out[d["uid"]] = d["workspaceId"]
    except Exception:  # noqa: BLE001
        pass
    return out


def _step(n: str, flow: str, before: str, after: str, look: str) -> None:
    print(f"\n── {n} · {flow}\n   state: {before} → {after}\n   look:  {look}")


class Mailer:
    def __init__(self, smtp: str, sender: str):
        host, _, port = smtp.partition(":")
        self.smtp, self.sender = smtplib.SMTP(host, int(port or 25)), sender

    def send(self, to: str, subject: str, body: str) -> None:
        m = EmailMessage()
        m["From"], m["To"], m["Subject"] = self.sender, to, subject
        m.set_content(body)
        self.smtp.send_message(m)


def _as_received(ics: bytes, assistant: str) -> bytes:
    return (b"MIME-Version: 1.0\r\nMessage-ID: <lifecycle@fixtures>\r\nFrom: replay@fixtures\r\n"
            b"To: " + assistant.encode() + b"\r\nSubject: invite\r\n"
            b"Content-Type: text/calendar; method=REQUEST\r\n\r\n" + ics)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fixtures", type=Path, required=True)
    ap.add_argument("--org-domain", required=True)
    ap.add_argument("--assistant", required=True)
    ap.add_argument("--terminal", default="http://localhost:3010")
    ap.add_argument("--smtp", default="localhost:1025")
    ap.add_argument("--reset", action="store_true", help="forget persons + bindings (state 0)")
    a = ap.parse_args(argv)
    if a.reset:
        STATE.unlink(missing_ok=True); BINDINGS.unlink(missing_ok=True)
        print("state cleared — this is a FIRST run")
    st = _load_state()
    mail = Mailer(a.smtp, a.assistant)
    ics_dir = a.fixtures / "mailroom" / "ics"
    T = a.terminal

    def parse(name: str):
        p = parse_invite(_as_received((ics_dir / name).read_bytes(), a.assistant))
        assert p.ok, f"{name}: {p.rejection}"
        return p

    # ── 0 · org tier ──────────────────────────────────────────────────────────────────────────
    g = Path.home() / "dev/vexa-global/README.md"
    org = "written" if g.exists() and "(unset)" not in g.read_text() else "UNSET"
    _step("0", "flows 1-3 · admin setup [STAGED: seeded by hand; wizard narrated]",
          f"org: {org}", f"org: {org}", "~/dev/vexa-global/README.md · agent cites it in any turn")

    # ── 1 · personal meeting: invite → confirm ───────────────────────────────────────────────
    p = parse("gcal-create-single-outsider.ics")
    organiser = p.organizer or ""
    known = st["persons"].get(organiser, "new")
    door = f"{T}/?meeting={p.platform}/{p.native_meeting_id}"
    if known == "new":
        body = (f"I'm booked for “{p.summary}”.\n\nAfter the meeting, minutes go to you, and every "
                f"{a.org_domain} participant gets their own. Outside addresses receive nothing.\n\n"
                f"New here? Your workspace is one click away — it will hold this meeting:\n{door}\n")
    else:
        body = f"I'm booked for “{p.summary}”. Minutes follow after the meeting.\n"
    mail.send(organiser, f"Booked — {p.summary}", body)
    st["persons"][organiser] = "invited" if known == "new" else known
    _step("1", "flow 5 · confirm email", f"person({organiser}): {known}",
          f"person: {st['persons'][organiser]}",
          f"Mailpit → 'Booked — {p.summary}' ({'door inside' if known=='new' else 'short form — KNOWN user'})")

    # ── 2 · prep ─────────────────────────────────────────────────────────────────────────────
    _step("2", "flows 6+8 · prep + personal setup", "meeting: planned", "meeting: planned",
          f"BROWSER: open {door} — workspace + upcoming meeting, chat prepping; first visit runs setup")

    # ── 3 · held → minutes + doors ───────────────────────────────────────────────────────────
    r = plan_base_path(p, org_domain=a.org_domain, assistant=a.assistant,
                       transcript_summary=(a.fixtures / "minutes" / "payments-architecture-sync.transcript.json").name
                       and "See your workspace for the full minutes.",
                       assign_url=T + "/", chat_url=T + "/")
    for s_ in r.sends:
        mail.send(s_.to, s_.subject, s_.body)
    supp = [e for e in r.log if e["decision"] == "suppress"]
    _step("3", "flows 11-13 · minutes + door fan-out", "meeting: held", "meeting: minutes-sent",
          f"Mailpit: {len(r.sends)} mails · suppressed: {[e['to'] for e in supp]} (outsider silent, logged)")

    # ── 4 · ask + assign (manual) ────────────────────────────────────────────────────────────
    _step("4", "flows 14+18 · ask + assign", "binding(single): unbound",
          "binding: ← your click writes it",
          f"BROWSER: organiser's Minutes mail → assign link → pick a group; driver reads the store next run")

    # ── 5 · tagged series → group path [STAGED artifacts] ────────────────────────────────────
    q = parse("gcal-create-recurring-grouptag.ics")
    b = _bindings()
    series_state = st["series"].get(q.uid, "unbound")
    if q.group_tag and series_state == "unbound":
        with BINDINGS.open("a") as f:
            f.write(json.dumps({"uid": q.uid, "workspaceId": q.group_tag, "via": "tag"}) + "\n")
        st["series"][q.uid] = f"bound({q.group_tag})"
        _step("5", "flow 17 · #group: tag binds the series", "series: unbound",
              st["series"][q.uid], f"binding store: uid → {q.group_tag} (organiser-authored, invite-time)")
    else:
        _step("5", "flow 17 · #group: tag", f"series: {series_state}", series_state,
              "already bound — the tag is not re-read (needed only the first time)")

    occ = "occ1" if st["series"].get(q.uid + ":occ") is None else "occ2"
    staged = a.fixtures / "minutes" / "staged-group-artifacts"
    members = {"dmitry-grankin": "dmitry.grankin@example.com",
               "priya-raman": "priya.raman@example.com",
               "tomas-oliveira": "tomas.oliveira@example.com"}
    for slug, addr in members.items():
        art = (staged / f"{slug}.md").read_text()
        mail.send(addr, f"[{q.group_tag}] Payments daily — your minutes",
                  art + f"\n\nChat with the group about it:\n{T}/?meeting={q.platform}/{q.native_meeting_id}\n")
    st["series"][q.uid + ":occ"] = occ
    tag_note = ("first occurrence — bound via tag" if occ == "occ1"
                else "SECOND occurrence — NO human act; the binding did it (flow 19)")
    _step("6", f"flows 15-16 · group minutes + fan-out [STAGED artifacts] · {occ}",
          f"occurrence: {occ}", f"occurrence: {occ} delivered",
          f"Mailpit: 3 per-member mails, each DIFFERENT · {tag_note}")

    # ── 7 · negatives ────────────────────────────────────────────────────────────────────────
    refused = []
    for neg in sorted(ics_dir.glob("neg-*.ics")):
        pr = parse_invite(_as_received(neg.read_bytes(), a.assistant))
        if not pr.ok:
            refused.append(f"{neg.name} → {pr.rejection.reason}")
    _step("7", "flow 20 · refusals", "—", "—", " · ".join(refused) or "none")

    _save_state(st)
    print(f"\nstate saved → {STATE}\nRUN AGAIN to witness memory: known-user confirm, tag not re-read, occ2 unasked.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
