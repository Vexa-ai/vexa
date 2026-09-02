"""The catalogue: `states.yaml` read, validated and interpolated.

PRD decision 38.1 — *user states are data*. This module is the half that makes that claim true
rather than decorative: the recipes are a data file, and the only thing code contributes is a
CLOSED VOCABULARY of verbs and checks. A recipe naming a verb that does not exist, or naming the
wrong door for a verb that does, is refused AT LOAD — before a single door is touched.

Why load-time and not run-time: a recipe that half-executes and then discovers it cannot finish
has already changed the running stack, and the person who has to work out what it left behind is
the founder. The whole point of the catalogue is that entering a state is cheap and leaving one is
possible; a partial run is neither.

The `door:` column in every step is CHECKED against `VERBS[verb].door`. That is what stops it
becoming decoration — if the engine ever changes which service answers a verb, every recipe that
names the old one goes red in `tests/test_catalogue.py`, which is exactly the moment a human
should be reading the file.
"""
from __future__ import annotations

import os
import pathlib
import re
from dataclasses import dataclass, field
from typing import Any

import yaml

HERE = pathlib.Path(__file__).resolve().parent
STATES_FILE = HERE / "states.yaml"

# ── the closed vocabulary ────────────────────────────────────────────────────────────────────────
#
# verb -> (door, required args, optional args). The DOOR is the service that answers, in the
# deployment's own vocabulary, and it is the string every recipe must repeat. `Doors` in doors.py
# has one method per verb with the same name; the test asserts that correspondence both ways, so a
# verb can never exist in the vocabulary with nothing behind it, nor behind it with nothing in the
# vocabulary.


@dataclass(frozen=True)
class Verb:
    door: str
    required: tuple[str, ...] = ()
    optional: tuple[str, ...] = ()
    writes: bool = True          # does executing it change the stack?
    what: str = ""


VERBS: dict[str, Verb] = {
    "require_instance_blank": Verb(
        "admin-api", (), (), writes=False,
        what="assert no admin has claimed the instance and the company layer is missing"),
    "require_subject_absent": Verb(
        "admin-api", ("address",), (), writes=False,
        what="assert this address has no user — the stranger precondition"),
    "user_ensure": Verb(
        "admin-api", ("address",), ("as",),
        what="resolve or create the platform user for this address"),
    "desk_init": Verb(
        "agent-api", ("subject",), (),
        what="POST /api/workspace/init — the person's own desk, idempotent"),
    "desk_entity": Verb(
        "agent-api", ("subject", "kind", "name"), ("facts", "source", "summary", "slug"),
        what="POST /api/workspace/entity — the product's own write door for a desk page"),
    "group_new": Verb(
        "agent-api", ("owner", "name"), ("purpose", "as"),
        what="POST /api/workspace/shared/new — a group desk with an owner"),
    "group_join": Verb(
        "agent-api", ("group", "owner", "member"), ("member_email", "role"),
        what="mint an invite as the owner and redeem it as the member — the only join there is"),
    "request_sign_in_link": Verb(
        "terminal", ("address",), (),
        what="POST /api/auth/request-link — the magic-link front door a person uses"),
    "drop_invite": Verb(
        "smtp", ("organizer", "title", "start"), ("attendees_from_fixture", "ics_uid", "group",
                                                  "attendees"),
        what="SMTP an ICS invite into the mail double, exactly as a calendar would"),
    "seed_meeting": Verb(
        "gateway", ("owner", "native"), ("as", "title", "started_at", "source"),
        what="POST /meetings then POST /meetings/{id}/transcript-import with a DNA fixture"),
    "emit_fact": Verb(
        "flows-api", ("event_type", "source_event_id", "refs"), (),
        what="POST /events — the fact intake for a producer that is not the mailbox"),
    "await_mail": Verb(
        "mailpit", ("to",), ("subject_contains", "as", "budget_s", "since"), writes=False,
        what="wait for one message in the mail double, and capture it"),
    "reply_to_mail": Verb(
        "smtp", ("to_mail", "from_address", "body"), ("as",),
        what="SMTP a reply with In-Reply-To set, so the poller routes it by thread"),
    "cancel_bot_leg": Verb(
        "flows-api", ("flow",), ("source_contains",),
        what="cancel this recipe's parked invite reaction, so no bot is ever dispatched at a "
             "fixture URL after the run has finished"),
    "await_reaction": Verb(
        "flows-api", ("flow",), ("since", "as", "budget_s"), writes=False,
        what="wait for a reaction of this flow to appear"),
}

# check -> required args. Every one is answered from an artefact the run already captured or from
# a read door; none of them needs a human eye. A state whose success cannot be checked this way
# does not belong in the catalogue (README § What a state is).
CHECKS: dict[str, tuple[str, ...]] = {
    "mail_present": ("of",),
    "link_present": ("of", "must_match"),
    "scaffold_resolves": ("link",),
    "no_user": ("address",),
    "user_exists": ("address",),
    "meeting_status": ("of", "is"),
    "desk_nonempty": ("subject",),
    "group_member": ("group", "address"),
    "reaction_admitted": ("of",),
}

_TOKEN = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_.]*)\s*\}\}")


class CatalogueError(ValueError):
    """A recipe this module refuses to hand to the engine. Carries the state and the row."""


@dataclass
class Step:
    do: str
    door: str
    args: dict[str, Any]
    index: int

    @property
    def capture(self) -> str:
        return str(self.args.get("as") or "")


@dataclass
class State:
    name: str
    summary: str
    story: str
    steps: list[Step]
    verify: list[dict]
    artefacts: dict = field(default_factory=dict)
    preconditions: list = field(default_factory=list)

    @property
    def writes(self) -> bool:
        return any(VERBS[s.do].writes for s in self.steps)


@dataclass
class Catalogue:
    version: int
    domain_env: str
    default_domain: str
    fixtures_env: str
    default_fixtures: str
    states: dict[str, State]

    def __getitem__(self, name: str) -> State:
        try:
            return self.states[name]
        except KeyError:
            raise CatalogueError(
                f"{name!r} is not a state. The catalogue holds: {', '.join(sorted(self.states))}"
            ) from None

    def domain(self, env: dict | None = None) -> str:
        env = os.environ if env is None else env
        return (env.get(self.domain_env) or self.default_domain).strip().lstrip("@").lower()

    def fixtures_dir(self, env: dict | None = None) -> pathlib.Path:
        env = os.environ if env is None else env
        raw = env.get(self.fixtures_env) or self.default_fixtures
        return pathlib.Path(raw).expanduser()


def load(path: pathlib.Path | str | None = None) -> Catalogue:
    """Read and VALIDATE the catalogue. Raises CatalogueError with the offending row named."""
    p = pathlib.Path(path or STATES_FILE)
    raw = yaml.safe_load(p.read_text())
    if not isinstance(raw, dict):
        raise CatalogueError(f"{p} is not a mapping")
    for key in ("version", "domain_env", "default_domain", "states"):
        if key not in raw:
            raise CatalogueError(f"{p} has no `{key}`")

    states: dict[str, State] = {}
    for name, body in (raw["states"] or {}).items():
        if not isinstance(body, dict):
            raise CatalogueError(f"state {name!r} is not a mapping")
        for key in ("summary", "steps", "verify"):
            if not body.get(key):
                raise CatalogueError(f"state {name!r} has no `{key}` — "
                                     f"a state with no verify block is a state nobody can check")
        steps = [_step(name, i, row) for i, row in enumerate(body["steps"])]
        _check_captures(name, steps, body["verify"])
        states[name] = State(
            name=name, summary=str(body["summary"]).strip(), story=str(body.get("story") or ""),
            steps=steps, verify=[_verify(name, v) for v in body["verify"]],
            artefacts=body.get("artefacts") or {}, preconditions=body.get("preconditions") or [])

    if not states:
        raise CatalogueError(f"{p} declares no states")
    return Catalogue(
        version=int(raw["version"]), domain_env=str(raw["domain_env"]),
        default_domain=str(raw["default_domain"]),
        fixtures_env=str(raw.get("fixtures_env") or "VEXA_DNA_FIXTURES"),
        default_fixtures=str(raw.get("default_fixtures") or "~/dna-fixtures"),
        states=states)


def _step(state: str, i: int, row: Any) -> Step:
    if not isinstance(row, dict) or "do" not in row:
        raise CatalogueError(f"{state} step {i}: every row needs a `do:`")
    verb = str(row["do"])
    if verb not in VERBS:
        raise CatalogueError(
            f"{state} step {i}: {verb!r} is not a verb. The vocabulary is closed: "
            f"{', '.join(sorted(VERBS))}. Add the verb to catalogue.VERBS and the method to "
            f"doors.Doors together, or the recipe is naming something nothing can execute.")
    spec = VERBS[verb]
    door = str(row.get("door") or "")
    if door != spec.door:
        raise CatalogueError(
            f"{state} step {i} ({verb}): declares door {door!r} but {verb} is answered by "
            f"{spec.door!r}. The column is checked, not documentation — fix whichever is wrong.")
    args = {k: v for k, v in row.items() if k not in ("do", "door")}
    missing = [a for a in spec.required if a not in args]
    if missing:
        raise CatalogueError(f"{state} step {i} ({verb}): missing {missing}")
    unknown = [a for a in args if a not in spec.required + spec.optional + ("as",)]
    if unknown:
        raise CatalogueError(
            f"{state} step {i} ({verb}): unknown argument(s) {unknown}. An argument the verb "
            f"ignores is a recipe that reads as if it does something it does not.")
    return Step(do=verb, door=door, args=args, index=i)


def _verify(state: str, row: Any) -> dict:
    if not isinstance(row, dict) or "check" not in row:
        raise CatalogueError(f"{state}: every verify row needs a `check:`")
    kind = str(row["check"])
    if kind not in CHECKS:
        raise CatalogueError(f"{state}: {kind!r} is not a check. Available: "
                             f"{', '.join(sorted(CHECKS))}")
    missing = [a for a in CHECKS[kind] if a not in row]
    if missing:
        raise CatalogueError(f"{state}: check {kind} missing {missing}")
    return dict(row)


def _check_captures(state: str, steps: list[Step], verify: list) -> None:
    """A verify row may only name something a step actually captured.

    This is the load-time half of the same rule the door column enforces: a check pointing at a
    capture nobody makes passes vacuously at run time, and a vacuous check is worse than no check —
    it reports green for a state nobody proved.
    """
    captured = {s.capture for s in steps if s.capture}
    for row in verify:
        if not isinstance(row, dict):
            continue
        for key in ("of", "link"):
            ref = row.get(key)
            if not ref:
                continue
            root = str(ref).split(".", 1)[0]
            if root not in captured and key == "of":
                raise CatalogueError(
                    f"{state}: check {row.get('check')} names `{ref}`, which no step captures "
                    f"with `as:`. Captured here: {sorted(captured) or 'nothing'}.")


#: What `interpolate(..., lenient=True)` leaves where a token cannot be resolved yet. It is
#: deliberately not an empty string and deliberately not email-shaped: the lenient pass exists so
#: the safety guard can read every address a recipe WILL say, and a placeholder that looked like an
#: address would be checked as one.
UNBOUND = "<unbound>"


def interpolate(value: Any, bindings: dict, lenient: bool = False) -> Any:
    """`{{name}}` and `{{name.field}}` out of the run's bindings, recursively over dicts/lists.

    A WHOLE-STRING token keeps the bound value's TYPE (a list of attendees stays a list, an epoch
    stays a number); a token inside a longer string renders as text. The distinction matters:
    `participants: "{{fixture_attendees}}"` must reach `POST /events` as a JSON array, and a fact
    whose participants arrived as the string "['a@x', 'b@x']" fans out to nobody while looking
    entirely successful.

    An unbound token RAISES. It is the same class of failure as the door column: silently
    rendering `{{meeting_row.meeting_id}}` as empty produces a fact about meeting "" that admits
    fine and reacts to nothing.

    `lenient=True` renders unbound tokens as `UNBOUND` instead of raising. It has exactly ONE
    caller — the pre-flight pass that reads every address a recipe will utter before any door is
    touched — and it exists because that guard must see the addresses a step CAN resolve even when
    the same step also names a capture only a later step will make. Without it, one
    `{{meeting_row.meeting_id}}` in a row would hide that row's whole participant list from the
    domain check.
    """
    if isinstance(value, dict):
        return {k: interpolate(v, bindings, lenient) for k, v in value.items()}
    if isinstance(value, list):
        return [interpolate(v, bindings, lenient) for v in value]
    if not isinstance(value, str):
        return value
    whole = _TOKEN.fullmatch(value.strip())
    if whole:
        return _resolve(whole.group(1), bindings, lenient)
    return _TOKEN.sub(lambda m: str(_resolve(m.group(1), bindings, lenient)), value)


def _resolve(dotted: str, bindings: dict, lenient: bool = False) -> Any:
    cur: Any = bindings
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            if lenient:
                return UNBOUND
            raise CatalogueError(
                f"nothing is bound to {{{{{dotted}}}}}. Bound here: "
                f"{', '.join(sorted(k for k in bindings if not k.startswith('_')))}")
    return cur
