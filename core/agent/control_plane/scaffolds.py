"""scaffolds.py — THE SCAFFOLD: one record that says, for one moment a person arrives at, what the
agent knows and what the UI shows.

PRD §5.5 (founder go, 2026-09-02: *"go on the scaffold"*). The missing primitive behind every seam
failure of the founder's walk: an emailed link used to carry a preset NAME and a meeting ref, and
each of the two renderers behind it — the terminal panel and the agent's first turn — composed the
rest out of whatever it could find. Two composers, one moment, and they disagreed: the chat opened
on a Zoom number, the panel opened the reader's own README, the phase greeting beat the preset, and
the agent introduced itself to a person it already knew.

A scaffold ends that by being the ONE record both renderers read:

    Scaffold { id · who · kind · meeting (row id) · workspaces · refs · opening (a preset NAME) ·
               tabs · focus · provenance · redeemed_at / redeemed_by }

Three rules the shape enforces, each of them a fix for a specific live failure:

  * **PHASE IS NOT STORED.** It is resolved from the meeting ROW at open. An emailed link clicked
    three days late must not say "upcoming" about a meeting that has happened (ledger F4).
  * **THE OPENING IS A NAME, NEVER TEXT.** `opening` is a filename in `_global/asks/`, admin-owned
    and read hot. A record that could carry prompt text would let anyone who can mint one drive
    somebody else's agent — the same reason the URL never carried text (PRD §6).
  * **`who` IS AN ADDRESS, NOT A SUBJECT.** The recipient of a post-meeting scaffold usually has no
    account at mint time; they get one when they click. Resolution happens at REDEEM.

── WHERE IT IS STORED, AND WHY ──────────────────────────────────────────────────────────────────
Redis (the same client that already backs `_Sessions`), never the workspace volume. Three reasons,
in the order they bind:

  1. **It must survive a wipe of the recipient's desk.** A scaffold is the thing that will REBUILD
     that desk; storing it in `_system` — the per-user tier under the workspace volume — means a
     reset, a re-seed or a blank takes the record with the desk it was going to fill.
  2. **It must exist before the recipient does.** `_system` is per-SUBJECT and the subject is not
     minted until the click. There is no tier under `/workspaces` addressed by an email address.
  3. **It must be queryable by recipient** (`GET /api/scaffolds?mine`, and step 6's
     `whats_waiting`). A per-recipient index set answers that in one round trip; a directory scan
     over the workspace volume does not.

Redis is durable here by deployment: `deploy/compose/docker-compose.yml` runs valkey with
`--appendonly yes` on its own named volume, which is not the workspace volume. KNOWN GAP, stated
rather than hidden: `drafts/2026-09-02-blank-instance.sh` does not clear redis, so scaffolds outlive
a blank — a blank should add `agent:scaffold:*` / `agent:scaffolds:by:*` to what it deletes.

An in-memory fallback keeps the unit tests redis-free, exactly as `_Sessions` does.
"""
from __future__ import annotations

import json
import logging
import re
import secrets
import time
from pathlib import Path
from typing import Iterable, Optional

from control_plane import preset_library

logger = logging.getLogger("agent_api.scaffolds")

# What a person's own workspace is CALLED to that person. The terminal holds the same constant
# (`clients/terminal/src/minutes/vocabulary.ts` WORKSPACE_WORD) and the flows runtime a third
# (`core/flows/src/flows_steps/mailtext.py`); three languages cannot share a literal, so the three
# lines name each other and together they are the whole rename. Founder, 2026-09-02: a workspace is
# a DESK.
WORKSPACE_WORD = "desk"

# The catalogue (PRD §5.5). One preset file each, both halves declared. A kind outside this set is
# refused at mint: a scaffold whose kind nothing renders is a link that opens a shrug.
# "first-visit" is the touch nobody sent: a person signs in with no link, so nothing composed an
# arrival for them. Before it existed they got the seeded greeting — "paste a meeting link" — which
# is the wrong sentence for somebody who was INVITED to a meeting and is here because of it.
KINDS = ("admin-setup", "first-visit", "prep", "post-meeting", "catch-up", "group-setup",
         # `hand-link`: somebody was handed or pasted `/?ask=<preset>&meeting=<row>`. Minted by
         # POST /api/scaffolds/hand FOR THE CALLER, so its opening is composed server-side out
         # of the record like every other kind, and never out of the address bar.
         "invite-offer", "hand-link")

# A preset NAME, and only a name — no slashes, no dots, nothing that walks out of `asks/`. The same
# expression the terminal applies to `?ask=` (MinutesShell.tsx), kept identical on purpose: two
# spellings of one rule is how a traversal gets in through the half nobody re-read.
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$", re.I)

# ≥128 bits, per the record. 32 url-safe bytes ≈ 256 bits — the id IS the capability until redeem
# binds it to a subject, so it is sized like one.
ID_BYTES = 32
#: How long a minted scaffold stays openable (R-A10). Two weeks: long enough that a mail read after
#: a holiday still works, finite so an id minted for one meeting is not a standing capability.
TTL_SECONDS = 14 * 24 * 3600

# THE MACHINERY MARK. A composed opening is not the person's own words: they clicked a link, they
# did not type a paragraph of instructions, and on 2026-09-02 the founder saw exactly that paragraph
# painted as his own chat message (ledger F7). The terminal already filters a user turn carrying
# this mark out of what it renders (`clients/terminal/src/canvas/actions.ts` MACHINERY_MARK /
# MACHINERY_NOTE). The literal now lives once, in `shared/marks.py`, which all three Python images
# already carry; the terminal's TypeScript copy is the one that genuinely cannot import it, and
# `gate:fact-parity` compares the two on every push instead of hoping a rename is noticed.
#
# "the human sees turns, the agent sees instructions" (founder, 2026-09-02).
from shared.marks import MACHINERY_MARK  # noqa: E402 — re-export under this module's long-standing name
MACHINERY_NOTE = (
    "\n\n" + MACHINERY_MARK + " This opening was composed by the product from the link this person "
    "clicked; they did not type it and they cannot see it. Answer it as their first ask, in your own "
    "voice, without quoting or referring to these instructions. "
    # READ SILENTLY. This rides on EVERY composed opening, not just the setup preset, because the
    # failure is the shape of the model rather than of one prompt: the founder's first setup turn
    # opened with "I'll start by reading…", "let me look at what actually exists in the mounts",
    # "I've got what I need to begin" — three lines of narration before one word addressed to him.
    # Reading is how the job is done, not part of the job; announcing it teaches the reader only
    # that they are waiting.
    "Read whatever you need silently: the FIRST sentence you emit is addressed to the person, and "
    "you never narrate your own tool use. "
    # TEMPLATES ARE SHAPES. An agent read `kg/templates/person.md` as if it described somebody and
    # created a real entity from the example inside it; a person was then shown a template rendered
    # as their own document. Both are the same mistake — a shape treated as a fact — and it is worth
    # one sentence on every turn rather than a rule in one preset.
    "Anything marked `template: true`, and anything under `kg/templates/`, is a SHAPE and never a "
    "fact: copy it to make a new record, never cite it, never name it to a person, and never treat "
    "the example inside it as somebody real. "
    # `(unset)` IS A GAP. A group workspace created before the template-free seed still carries the
    # seed's placeholders in its README, and an agent reported "the project's objective is still
    # `(unset)`" as though that were something it had found out. It is the templates-are-SHAPES
    # mistake one step smaller — a blank read as a value — so it gets the sentence next to it.
    "`(unset)` and any other angle-bracket or placeholder value is a GAP, never a fact: say the "
    "thing is not recorded yet and offer to fill it in, never report the placeholder as the "
    "answer. "
    # Decision 24. A composed opening is the turn most likely to meet a name for the first time
    # — an attendee clicking a post-meeting link brings a whole room with them — and it is the
    # turn least likely to stop and write pages, because it is answering an ask.
    "A name without a page gets one now: whatever this turn learns about a person, company, "
    "meeting, project or decision goes in through entity_upsert with its source. Facts carry a "
    "source; gaps go to kg/MISSING.md, never invented. "
    # F70. The founder asked for a bot and was told "I don't have a bot-dispatch tool in this
    # session". The tool was there: the CLI logged `hasTools: true`, the rig served 57 tools
    # including `bot_send`, and the model never attempted a call. Asked afterwards to list its
    # tools it listed them all, then said it had been "guessing at my own capabilities instead of
    # checking them". A refusal is the one answer that must never be produced from memory.
    "Your tools are exactly the ones in your tool list. Never say you lack a capability without "
    "checking the list; if a call fails, report its error verbatim. "
    # F71. The founder was handed `curl -X POST https://api.vexa.ai/bots -H "X-API-Key: …"` — the
    # agent's workaround for a thing it believed it could not do, which it could. Two failures in
    # one line: it made him the runtime, and it put a key in a chat.
    "Never hand the person an API call, a curl command, a key or a token. If you cannot do "
    "something, say that you cannot do it right now and why, in one sentence. "
    # F73. After a successful send the agent offered a link into the product the person was
    # already looking at. The panel is moved by the harness, not by you — there is no tool here to
    # call for it, and a URL is the one thing that cannot help someone already inside the app.
    "The person is INSIDE the app: never give them an app URL or a link into it, and never tell "
    "them to open one. Say what is on screen now. "
    # F69. A bare meeting link is not a topic to discuss. It is the whole instruction, and the
    # turn that answers it with a question has spent the moment the person needed.
    "A message that is only a meeting URL means: send the bot now. "
    # F72. Correcting yourself and then asking whether to proceed spends a second turn on a
    # decision already made — and on a standing instruction it re-asks something already answered.
    "After correcting yourself on something the person has already told you, DO the thing in that "
    "same turn. Never re-ask what they have already answered.")

_FRONTMATTER = re.compile(r"^---\n([\s\S]*?)\n---\n?")

# The meeting's phase, in the meeting's own vocabulary. The status sets are the terminal's
# (`surfaces/meetingModel.ts` PREP_STATUSES / LIVE_PHASE_STATUSES) — one vocabulary, two readers,
# so the header the panel draws and the phase the agent is told can never disagree.
_PREP_STATUSES = frozenset({"idle", "scheduled"})
_LIVE_STATUSES = frozenset({"active", "joining", "requested", "awaiting_admission",
                            "needs_help", "stopping"})


class ScaffoldError(ValueError):
    """A record that cannot be minted. Carries the reason the CALLER has to act on — a mint that
    fails must stop the send, and a step that stops a send has to say why in one line."""


def phase_of(meeting_row: Optional[dict]) -> Optional[str]:
    """`prep` | `live` | `post` from the meeting ROW, or None when the row could not be read.

    None is a real answer and not a default: "we could not see the meeting" is different from
    "the meeting has happened", and the renderer that gets None keeps the layout the meeting's own
    rule produces rather than announcing a phase nobody verified. This is the whole of decision 11
    — the phase belongs to the meeting, never to the link — expressed as a function of the row."""
    if not isinstance(meeting_row, dict):
        return None
    status = str(meeting_row.get("status") or "").strip()
    data = meeting_row.get("data") if isinstance(meeting_row.get("data"), dict) else {}
    if status == "completed" and data.get("stop_requested"):
        status = "stopped"
    if status in _PREP_STATUSES:
        return "prep"
    if status in _LIVE_STATUSES:
        return "live"
    if status:
        return "post"
    return None


def group_workspace_of(meeting_row: Optional[dict]) -> str:
    """The shared workspace a meeting is BOUND to (`data.workspace_id`), or `""`."""
    data = meeting_row.get("data") if isinstance(meeting_row, dict) else None
    ws = data.get("workspace_id") if isinstance(data, dict) else None
    return str(ws).strip() if isinstance(ws, (str, int)) and not isinstance(ws, bool) else ""


# ── the preset library (`_global/asks/`) ─────────────────────────────────────────────────────────

def preset_path(global_root: str | Path, name: str) -> Path:
    if not NAME_RE.match(name or ""):
        raise ScaffoldError(f"{name!r} is not a preset name — a scaffold's opening is a NAME in "
                            "_global/asks/, never text and never a path")
    return Path(global_root) / "asks" / f"{name}.md"


#: Sentinel for "the caller did not say" — distinct from an explicit `image_root=None`, which means
#: "look in `_global` and nowhere else" and is how the tests pin the pre-fallback failure.
_LIBRARY_DEFAULT = object()


def read_preset(global_root: str | Path, name: str, *,
                image_root: "str | Path | None | object" = _LIBRARY_DEFAULT) -> tuple[dict, str]:
    """`(frontmatter, body)` for one preset. Raises `ScaffoldError` when it is absent everywhere or
    empty — which is the point: a mint whose preset does not exist must fail at MINT, where a step
    can still refuse to send, rather than at click, where a person meets an empty chat.

    TWO LOOKUP ROOTS, IN THIS ORDER: `_global/asks/` (admin-owned, read hot, edited in the product)
    and then the copy baked into this image (`preset_library`). The admin's file always wins — it is
    looked at first — and the image is what answers when the store's library is merely BEHIND the
    build, which on 2026-09-05 handed a first-time visitor an empty desk because `first-visit.md`
    had been added to the repo one day after that instance's `_global/asks/` was populated by hand.
    `preset_library.top_up` normally closes that gap by copying the file in, so an admin can see and
    edit what the product reads; the fallback is what holds when it cannot — a `_global` mounted
    read-only is a legitimate deployment shape.

    ABSENCE FALLS THROUGH, EMPTINESS DOES NOT. A file that is present and blank in `_global` is a
    present-but-broken ADMIN file, and answering it with the image's copy would be a deploy
    overruling a human edit — the same thing the additive top-up refuses to do one layer up."""
    f = preset_path(global_root, name)
    fallback = preset_library.image_asks_dir() if image_root is _LIBRARY_DEFAULT else (
        Path(image_root) if image_root is not None else None)
    try:
        raw = f.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        alt = (fallback / f"{name}.md") if fallback is not None else None
        if alt is None or not alt.is_file():
            raise ScaffoldError(f"preset asks/{name}.md cannot be read here ({e.__class__.__name__}) — "
                                "the link would open nothing") from e
        logger.info("scaffolds: preset %s is not on the store — reading the copy this image ships "
                    "(%s). preset_library.top_up puts it in _global/asks/ where an admin can edit it.",
                    name, alt)
        try:
            raw = alt.read_text(encoding="utf-8", errors="replace")
        except OSError as e2:
            raise ScaffoldError(f"preset asks/{name}.md cannot be read here ({e2.__class__.__name__}) — "
                                "the link would open nothing") from e2
    if not raw.strip():
        raise ScaffoldError(f"preset asks/{name}.md is empty — the link would open nothing")
    fm: dict = {}
    body = raw
    m = _FRONTMATTER.match(raw)
    if m:
        body = raw[m.end():]
        for line in m.group(1).splitlines():
            if ":" not in line:
                continue
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip()
    return fm, body


def frontmatter_list(fm: dict, key: str) -> list[str]:
    return [x.strip() for x in str(fm.get(key) or "").split(",") if x.strip()]


def substitute(body: str, tokens: dict) -> str:
    """`{{token}}` → its value, with the SAME vocabulary the terminal substituted client-side
    (`MinutesShell.tsx`): meeting · title · when · state · ws · workspace · today.

    Done SERVER-SIDE now, and that is the point of the record: the terminal must not compose text.
    Every value comes off the scaffold or off state the server can read; an unknown token is left
    STANDING, exactly as `mailtext.render` leaves one, so a typo in an admin's preset is visible in
    the output instead of silently becoming an empty string."""
    out = body
    for key, value in tokens.items():
        out = re.sub(r"\{\{\s*" + re.escape(key) + r"\s*\}\}", lambda _m, v=str(value): v, out)
    return out.strip()


# ── the turn the agent actually gets ─────────────────────────────────────────────────────────────

# Domains that carry NO signal about a company, however real they look in an address. A deployment
# running on a test mailbox must not be told it works for "Storm" — and the founder saw exactly that
# on 2026-09-02: with no address of its own to reason from, the agent reached for the mailbox
# address and told him its only signal was "the deployment domain (storm.test)". A placeholder
# spoken with confidence is worse than an admitted blank, because he cannot tell which it is.
PLACEHOLDER_DOMAINS = frozenset({
    "test", "storm.test", "rehearsal.test", "example.com", "example.org", "example.net",
    "localhost", "local", "invalid", "vexa.local",
})


def company_domain(address: str) -> str:
    """The part after @, when it is a real signal about a company — otherwise "".

    This is the ONE anchor a first setup turn has: the administrator's own address. `dmitry@vexa.ai`
    says "Vexa" long before anyone types it. A `.test` address says nothing at all, and saying
    nothing is the honest answer: the preset asks cold only when this is empty."""
    at = str(address or "").strip().lower().rpartition("@")
    domain = at[2] if at[1] else ""
    if not domain or domain in PLACEHOLDER_DOMAINS:
        return ""
    if domain.rsplit(".", 1)[-1] in {"test", "invalid", "localhost", "local"}:
        return ""
    return domain


def facts_block(view: dict) -> str:
    """The scaffold's REFS as the facts of this turn — one block, in front of the ask.

    Every line here is something the agent otherwise has to go and find, and on a small model
    "otherwise" often means "not at all": the prep opening that named a meeting by its Zoom id and
    then said it held nothing (ledger F1) was an agent with no facts reaching for the only meeting
    it could see. Facts are cheap, they are already in the record, and they are the difference
    between a first turn that names the meeting and one that asks the person what it is about.

    NOT a summary and never a conclusion — the agent still reads the workspace and the transcript.
    This is the invite's own knowledge, which nothing else on the turn carries."""
    refs = view.get("refs") or {}
    rows: list[str] = [f"kind: {view.get('kind')}"]
    if view.get("meeting"):
        rows.append(f"meeting: row {view['meeting']}"
                    + (f" — {refs['title']}" if refs.get("title") else ""))
    if view.get("native"):
        rows.append(f"native id: {view['native']}")
    if view.get("phase"):
        rows.append(f"phase: {view['phase']} (read from the meeting row just now, not from the link)")
    # WHO THIS TURN IS WITH. It was missing, and its absence produced the defect: the setup agent
    # had no address to reason from, so it reached for the mailbox and called it "the deployment
    # domain". The address is the anchor for the company's name AND the seed for this person's own
    # `self:` entity, and it is already on the record — it was simply never handed to the turn.
    if refs.get("who"):
        rows.append(f"you are talking to: {refs['who']}")
    if refs.get("domain"):
        rows.append(f"their email domain: {refs['domain']} — the strongest signal you have about "
                    f"the company; propose a name from it rather than asking cold")
    # WHAT THIS COMPANY ALREADY INVOLVES THEM IN. Without these two lines a first visit has nothing
    # to say about the person and falls back to the generic greeting — "paste a meeting link" — to
    # somebody who is here precisely because a colleague already put them in something. Empty is
    # reported as EMPTY rather than omitted: "nothing is shared with you yet" is a fact the preset
    # is told to say out loud, and a missing line would read as "not looked up".
    if "shared_workspaces" in refs:
        shared = refs.get("shared_workspaces") or []
        rows.append("shared with them: " + (
            "; ".join(f"{w.get('name') or w.get('slug')}"
                      + (f" — {w['purpose']}" if w.get("purpose") else "")
                      for w in shared) if shared else "nothing yet"))
    if "invited_meetings" in refs:
        invited = refs.get("invited_meetings") or []
        rows.append("meetings they are invited to: " + (
            "; ".join(f"{m.get('title') or 'untitled'}"
                      + (f" at {m['when']}" if m.get("when") else "")
                      for m in invited) if invited else "none yet"))
    for key in ("when", "organizer", "room"):
        if refs.get(key):
            rows.append(f"{key}: {refs[key]}")
    people = refs.get("participant_names") or {}
    if refs.get("participants"):
        named = [f"{people[a]} <{a}>" if people.get(a) else str(a) for a in refs["participants"]]
        rows.append("participants: " + ", ".join(named))
    state = refs.get("state") or {}
    if state:
        rows.append(f"this person's {WORKSPACE_WORD}: {state.get('desk')} · group: {state.get('group')}")
    rows.append("workspaces mounted for this chat: " + ", ".join(view.get("workspaces") or []))
    return "[scaffold] What is already known about this moment:\n" + "\n".join("  " + r for r in rows)


def turn_prompt(view: dict) -> str:
    """FACTS, then the ASK, machinery-marked. What agent-api sends as the turn's prompt when a chat
    names a scaffold — the terminal sends an id and composes nothing.

    `opening_text` is the same string the wire hands the client; the facts block is added HERE and
    only here, because it is for the agent and the client never renders it."""
    return facts_block(view) + "\n\n" + str(view.get("opening_text") or "")


# ── the recipient's desk, as a coarse state ──────────────────────────────────────────────────────
#
# Three words, deliberately coarse, so a preset can branch between a first contact and a returning
# one in prose (`{{state}}`). Computed at MINT (what the mail is written against) and RE-CHECKED at
# open (what is true when they actually click — which can be days later and, for a stranger who
# signed in meanwhile, is usually different).

def desk_state(workspaces_root: str | Path, subject: str) -> str:
    """`new` | `pile` | `warm` for one person's desk.

      new   there is no desk, or nothing has ever been written into it
      pile  meeting reports have landed and NOTHING has been wired — the shape of a desk the drop
            writes into and nobody has talked to (decision 22's economics: "a plain pile of
            reports"). This is the state the `personal:pile` half of `minutes-review-invite` exists
            for, and it is a fact about the FILES, not a guess from activity.
      warm  entities exist — somebody has worked here.
    """
    root = Path(workspaces_root) / str(subject)
    if not root.is_dir():
        return "new"
    entities = root / "kg" / "entities"
    if not entities.is_dir():
        return "new"
    meeting_reports, other = 0, 0
    try:
        for f in entities.rglob("*.md"):
            if f.name == "index.md":
                continue
            # `kg/templates/` is the SHAPE of an entity, never one — it must not make a desk warm.
            if "templates" in f.parts:
                continue
            if "meeting" in f.parts:
                meeting_reports += 1
            else:
                other += 1
    except OSError:
        return "new"
    if other:
        return "warm"
    return "pile" if meeting_reports else "new"


def group_state(workspaces_root: str | Path, group_slug: str) -> str:
    """`absent` | `new` | `warm` for the meeting's group desk. `absent` = the meeting is bound to no
    shared workspace; `new` = bound but nothing written yet; `warm` = the group has memory.

    Deliberately read off the DESK rather than off "does another meeting share this binding", which
    is the client's rule: the client already holds the meetings list, the server would have to fetch
    it, and what the preset actually branches on is whether there is group memory to build ON."""
    if not group_slug:
        return "absent"
    root = Path(workspaces_root) / str(group_slug)
    if not root.is_dir():
        # A shared workspace lives in its own store slot; an unmaterialised one is still "new".
        return "new"
    entities = root / "kg" / "entities"
    try:
        return "warm" if entities.is_dir() and any(entities.rglob("*.md")) else "new"
    except OSError:
        return "new"


def state_token(desk: str, group: str) -> str:
    """The one string a preset branches on, spelled the way the terminal spelled it."""
    return f"personal:{desk} group:{group}"


# ── the store ────────────────────────────────────────────────────────────────────────────────────

def _recipient_key(address: str) -> str:
    return str(address or "").strip().lower()


class ScaffoldStore:
    """Durable scaffold records, keyed by id, indexed by recipient ADDRESS.

    Redis when a client is wired (`agent:scaffold:<id>` holds the JSON record; `agent:scaffolds:by:
    <address>` is the per-recipient id set), in-memory otherwise. Same two-key shape and the same
    fallback discipline as `_Sessions`, for the same reason: the unit tests need no redis and the
    production path needs no second store."""

    def __init__(self, redis_client=None) -> None:
        self._redis = redis_client
        self._mem: dict[str, dict] = {}
        self._by: dict[str, list[str]] = {}

    @staticmethod
    def _key(scaffold_id: str) -> str:
        return f"agent:scaffold:{scaffold_id}"

    @staticmethod
    def _index_key(address: str) -> str:
        return f"agent:scaffolds:by:{_recipient_key(address)}"

    def mint(self, record: dict) -> dict:
        """Persist a new record, stamping its id and `minted_at`. Returns the stored record.

        WITH A LIFE (R-A10). The id is a capability — it opens a composed first turn bound to an
        address — and it was written with no `EXPIRE`, so a post-meeting scaffold minted for an
        address stayed openable forever, in a store the blank-instance script does not clear
        (`:39-42`). The touch it composes has a natural life of days; two weeks is generous for
        "the mail sat unread over a holiday" and finite, which is the property that was missing."""
        rec = dict(record)
        rec["id"] = secrets.token_urlsafe(ID_BYTES)
        rec["minted_at"] = time.time()
        rec.setdefault("redeemed_at", None)
        rec.setdefault("redeemed_by", None)
        self._put(rec)
        if self._redis is not None:
            self._redis.sadd(self._index_key(rec["who"]), rec["id"])
            self._redis.expire(self._index_key(rec["who"]), TTL_SECONDS)
        else:
            self._by.setdefault(_recipient_key(rec["who"]), []).append(rec["id"])
        return rec

    def _put(self, rec: dict) -> None:
        if self._redis is not None:
            self._redis.set(self._key(rec["id"]), json.dumps(rec), ex=TTL_SECONDS)
        else:
            self._mem[rec["id"]] = rec

    def get(self, scaffold_id: str) -> Optional[dict]:
        if not scaffold_id:
            return None
        if self._redis is not None:
            raw = self._redis.get(self._key(scaffold_id))
            if not raw:
                return None
            try:
                return json.loads(raw)
            except (TypeError, ValueError):
                logger.warning("scaffold %s is unreadable in the store", scaffold_id)
                return None
        return self._mem.get(scaffold_id)

    def redeem(self, scaffold_id: str, subject: str) -> Optional[dict]:
        """Mark FIRST open. Returns the record as the reader should see it.

        Idempotent by design: a second read by the same person is a reload, not a second redemption,
        so `redeemed_at` keeps the FIRST timestamp. The stamp is evidence of when a touch landed —
        overwriting it on every reload would destroy the only measurement the alpha ledger's
        "seconds to act" column is made of."""
        rec = self.get(scaffold_id)
        if rec is None:
            return None
        if not rec.get("redeemed_at"):
            rec["redeemed_at"] = time.time()
            rec["redeemed_by"] = str(subject)
            self._put(rec)
            # The index is "what is still WAITING for this address" — every reader of it filters on
            # `pending_only`. It never shed a redeemed id, so it grew for the life of the instance
            # and every read paid for the whole history (R-A10).
            if self._redis is not None:
                self._redis.srem(self._index_key(rec["who"]), scaffold_id)
            else:
                ids = self._by.get(_recipient_key(rec["who"]))
                if ids and scaffold_id in ids:
                    ids.remove(scaffold_id)
        return rec

    def hand_share(self, scaffold_id: str, subject: str) -> Optional[dict]:
        """Give this record's transcript share to its recipient, and record that it was given.

        THE SHARE STOPPED RIDING THE LINK (R-A08). It used to be a query parameter on the mail's one
        button — `{ui}/?s=<id>&tshare=<token>` — which is a bearer credential in a URL that crosses a
        public hostname, the recipient's mail provider, every proxy in between, and whoever they
        forward it to. `worker/engine.py` states the opposite rule one file away for the MCP
        delegation token, and the weaker spelling was on the more exposed artefact.

        So the token lives on the RECORD and is handed over an authenticated request instead. The
        caller must already have been judged the recipient — this method does not authorize; the
        route does, with a stricter predicate than the read uses (an admin may read the record and
        may never hold the capability).

        IDEMPOTENT FOR THE SAME SUBJECT, deliberately, and this is a considered departure from
        "one-time": a strictly single-use hand-out turns one dropped response into a person
        permanently unable to open the meeting they were invited to, which is a worse failure than
        the one this fixes. The property that carries the security is that the token is never in a
        URL and is only ever handed to the identity the record names. The record expires anyway
        (`TTL_SECONDS`), so the capability is bounded in time rather than by a counter."""
        rec = self.get(scaffold_id)
        if rec is None:
            return None
        if rec.get("share_token") and not rec.get("share_handed_at"):
            rec["share_handed_at"] = time.time()
            rec["share_handed_to"] = str(subject)
            self._put(rec)
        return rec

    def for_recipient(self, address: str, *, pending_only: bool = True) -> list[dict]:
        """This address's scaffolds, most recently minted first."""
        key = _recipient_key(address)
        if self._redis is not None:
            ids: Iterable[str] = self._redis.smembers(self._index_key(key)) or set()
        else:
            ids = list(self._by.get(key, []))
        rows = [r for r in (self.get(i) for i in ids) if r]
        if pending_only:
            rows = [r for r in rows if not r.get("redeemed_at")]
        rows.sort(key=lambda r: r.get("minted_at") or 0, reverse=True)
        return rows


def invited_meetings(address: str) -> list[dict]:
    """Meetings this ADDRESS is invited to, from the meeting rows the invite intake wrote.

    Read off `data.attendees` — the ICS ATTENDEE list the mailbox parser stores — because that is
    the only record that knows somebody was invited BEFORE they ever signed in. A first visit is
    exactly the moment when the person has no subject on any meeting yet, so an owner-keyed lookup
    would answer "none" for the very case this exists to serve.

    An empty list is a real answer and is reported as one. A failure RAISES rather than returning
    empty: the caller decides whether an unanswerable lookup means "none yet" or "do not say", and
    those are different sentences to a person reading their first screen.
    """
    import os

    url = (os.environ.get("VEXA_MEETINGS_DB_URL") or "").strip()
    addr = str(address or "").strip().lower()
    if not url or not addr:
        return []
    import psycopg

    want = json.dumps([{"email": addr}])
    with psycopg.connect(url, connect_timeout=5) as cx:
        rows = cx.execute(
            "SELECT id, data FROM meetings WHERE data->'attendees' @> %s::jsonb "
            "ORDER BY id DESC LIMIT 10", (want,)).fetchall()
    out: list[dict] = []
    for rid, data in rows:
        d = data if isinstance(data, dict) else {}
        out.append({"meeting": str(rid),
                    "title": d.get("title") or "",
                    "when": d.get("scheduled_at") or ""})
    return out
