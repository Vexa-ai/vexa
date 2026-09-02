"""REHEARSE — user states as data (PRD decision 38).

The catalogue and the executor are the ``rehearse`` package in ``deploy/dogfood/rehearse``: the same
recipes are driven by ``bin/rehearse.py`` and by ``rehearse/run_all.py``, and three copies of "what
an organizer-invited state is" would be three things to keep in step. These three tools are thin
onto that package.

IT IS A PLAIN IMPORT. The rig reached it with ``sys.path.insert`` on a directory derived from its
own ``__file__`` (seam inventory B6.4) — which is a process rewriting its own import path at run
time, and it is what a package exists to stop. Where the package is not installed, these three say
so by name and every other tool on this server is unaffected.
"""
from __future__ import annotations

import json

from .. import config
from ..identity import NotOperator, anon_guard, operator_or_refuse, subject
from ..shaping import capped, flows_unavailable
from ..registry import tool

_REHEARSE_MISSING = ("the rehearse package is not importable in this deployment — install "
                     "`vexa-rehearse` (deploy/dogfood/rehearse) beside this service, or put its "
                     "parent directory on PYTHONPATH")


def _rehearse_pkg():
    """The rehearse package, or None. A PLAIN IMPORT — never a path this process writes."""
    try:
        import rehearse  # noqa: PLC0415 — a deployment input, not an import-time dependency
    except Exception:  # noqa: BLE001
        return None
    return rehearse


@tool
@anon_guard
def rehearse(state: str, subject: str, meeting: str = "2026-03-02", when: str = "+30m",
             runner: str = "", fresh: bool = False, plan_only: bool = False,
             token: str = "") -> str:
    """PUT A PERSON IN A STATE — on the running stack, in seconds, with no rebuild.

    `states()` are the six moments a touch can reach somebody: `blank-admin` (an unclaimed
    instance, about to be claimed), `organizer-invited` (a user who just put the mailbox on a
    meeting), `attendee-stranger-minutes` (in the room, never signed in), `group-member`,
    `warm-desk-recurring` (a desk with history behind it), `reply-pending` (replied to a minutes
    mail). Each is a recipe of steps against the product's OWN doors — an invite by SMTP into the
    mail double, a transcript through the import route, a fact through the flows intake — so what
    comes back is a real touch with a real link, not a fixture.

    Returns `{links, mails, subjects, meeting_row, verify}`. The verify block is the point: it says
    the mail exists, its link is a scaffold, the record resolves, the desk holds what it should.
    Nothing is clicked — whether the link WORKS for a person is a walk, and a human's judgment.

    `subject` MUST be under the test domain (`VEXA_REHEARSE_DOMAIN`, default `rehearse.test`), and
    so must every address the recipe derives; anything else is refused before the first door. It
    also refuses while a live meeting belongs to anyone outside that domain — a live meeting is the
    one thing here nobody can re-record.

    `meeting` names a DNA fixture (a date). `when` is the meeting's start, `+30m` by default and
    in the FUTURE on purpose: the invite flow parks until start-2min, so no bot is dispatched at a
    fixture URL. `runner` pins this recipe's subjects to a harness (`openai-agent` runs them on the
    deployment's configured OpenAI-compatible endpoint) — per subject, never instance-wide.
    `fresh` resets the subject and its derived organizer first, which DELETES them.
    `plan_only` resolves and guards every step and executes none.
    """
    try:
        operator_or_refuse("rehearse")
    except NotOperator as e:
        return json.dumps({"refused": "operator only", "verb": e.verb, "who": e.who, "why": e.why,
                           "what_to_do": "An instance admin can run this. It injects facts and "
                                         "sends mail as other people, which is authority, not "
                                         "authentication."})
    pkg = _rehearse_pkg()
    if pkg is None:
        return flows_unavailable("rehearse", _REHEARSE_MISSING)
    try:
        res = pkg.rehearse(state, subject, meeting=meeting, when=when, runner=runner, fresh=fresh,
                           dry_run=plan_only, doors=pkg.LiveDoors(),
                           mailbox=config.MAIL_ADDR)
    except (pkg.Refused, pkg.DoorRefused, pkg.CatalogueError) as e:
        return json.dumps({"refused": str(e), "state": state, "as": subject})
    return capped(res.to_dict(), 12000)


@tool
@anon_guard
def subject_reset(address: str, token: str = "") -> str:
    """WIPE ONE PERSON — user, desk, sessions, pending scaffolds, friction, and their mail.

    So a state can be re-entered from nothing in seconds without blanking the instance. Test
    addresses only (`VEXA_REHEARSE_DOMAIN`); a real address is refused before anything is deleted.

    It reads the emptiness back and reports whatever it could NOT remove under `remaining` — a
    reset that half worked and said "done" is worse than one that refused.
    """
    try:
        operator_or_refuse("subject_reset")
    except NotOperator as e:
        return json.dumps({"refused": "operator only", "verb": e.verb, "who": e.who, "why": e.why,
                           "what_to_do": "An instance admin can run this — it deletes a person."})
    pkg = _rehearse_pkg()
    try:
        return json.dumps(pkg.subject_reset(address, doors=pkg.LiveDoors()), default=str)
    except (pkg.Refused, pkg.DoorRefused) as e:
        return json.dumps({"refused": str(e), "address": address})


@tool
def rehearse_states(token: str = "") -> str:
    """The state catalogue: what each state is, the doors its steps use, and what it verifies.

    NO ACCOUNT NEEDED — it reads a file. Call it before `rehearse()` rather than guessing a name.
    """
    pkg = _rehearse_pkg()
    if pkg is None:
        return flows_unavailable("rehearse_states", _REHEARSE_MISSING)
    try:
        c = pkg.load()
    except pkg.CatalogueError as e:
        return json.dumps({"error": str(e)})
    return capped({
        "domain": c.domain(), "fixtures": str(c.fixtures_dir()),
        "states": {n: {"summary": " ".join(st.summary.split()), "story": st.story,
                       "steps": [f"{s.do} ({s.door})" for s in st.steps],
                       "artefacts": st.artefacts,
                       "verify": [v["check"] for v in st.verify]}
                   for n, st in c.states.items()},
    }, 12000)
