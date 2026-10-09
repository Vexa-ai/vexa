"""bodies.py — the request bodies agent-api's routes take, as pydantic models.

One home for every named body, so a route's published schema (which the assembled MCP derives tool
arguments from) and the model a handler validates are the same class, whichever router serves it.
Moved out of `api_shared.py` unchanged; the class names are the OpenAPI schema names.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, field_validator

from control_plane.ceiling import WRITE_SLUG_HELP
from workspaces.shared import entities as entities_mod


class ChatContextBody(BaseModel):
    """The terminal-state CONTEXT BUNDLE (slice 1). ``extra="ignore"`` on purpose — forward-
    tolerant: a newer terminal adding bundle fields must never 422 against this server."""
    model_config = {"extra": "ignore"}
    tz: Optional[str] = None            # IANA tz for digest rendering (invalid → UTC)
    surface: Optional[dict] = None      # {list?: str, tab?: {kind: str}} — the ambient gate signal
    focus: Optional[dict] = None        # the focused thing (meeting/file/workspace/today); None = cleared
    include: Optional[dict] = None      # {schedule?: bool} — explicit user toggle beats the gate


#: A chat session id, bounded where it enters. It becomes part of the chat's unit id
#: (`units.chat_unit_id`), its stream topics, its continuity file and the runtime's names for its
#: worker, so it may hold only what every producer mints: `main`, `onboarding`, `chat-<base36>`,
#: `meet-<row>`, `group-<slug>`, the terminal's `scaffold-<token_urlsafe>` and UUIDs. Empty means
#: the default session, as before.
CHAT_SESSION_PATTERN = r"^([A-Za-z0-9][A-Za-z0-9._-]{0,127})?$"


class ChatBody(BaseModel):
    model_config = {"extra": "forbid"}
    prompt: str
    # subject is DERIVED server-side from X-User-Id (P20) — kept here only so a client that still sends it
    # doesn't 422 (extra=forbid); the value is IGNORED. Dropped from the client in Stage 4.
    subject: Optional[str] = None
    session: Optional[str] = Field(default=None, pattern=CHAT_SESSION_PATTERN)
    # LEGACY single-focus grounding ({kind, ref}) — still honored when ``context`` is absent, so
    # old clients keep byte-identical behavior. The terminal now sends ``context`` (below) too.
    active: Optional[dict] = None
    # the terminal-state context bundle; when present it is AUTHORITATIVE (including
    # ``focus: null`` = the user cleared the focus chip — legacy ``active`` is then ignored).
    context: Optional[ChatContextBody] = None
    # Client-minted TURN NONCE (one per user turn, constant across that turn's reconnect attempts).
    # Lets the server tell a no-cursor RETRY (the stream dropped before the client ever saw an ``id:``,
    # so it can't send Last-Event-ID) from a genuinely new turn with identical text ("yes" twice):
    # a matching nonce re-attaches from the turn's recorded start — no second dispatch, no lost events.
    turn_id: Optional[str] = None
    # THE MEETING ROOM (post-meeting run). The caller may name ONLY THE MEETING — a meetings-domain
    # ROW id — and the server resolves who was in it (control_plane/meeting_room.py). There is
    # deliberately NO field here that names a workspace or a subject: a caller able to say "mount
    # u_bob" could read any user's desk by naming it, which is the exact hole this shape exists to
    # close. Accepting it additionally requires the internal-tier secret (see ``_resolve_room``), so
    # the general end-user chat surface cannot open a room at all.
    room_meeting_id: Optional[str] = None
    # MEMBERSHIP = the INVITE's participant list, as ADDRESSES. The trusted caller holds the parsed
    # invite, so it sends the addresses; agent-api resolves each one to a subject through admin-api
    # and mounts only participants who ALREADY have a subject and a desk. Addresses, never subject
    # ids and never workspace names: the resolution — and therefore the blast radius — stays here.
    room_participants: Optional[list[str]] = None
    # The invite's ICS ``CN=`` map (address → display name). Used ONLY to match transcript speaker
    # labels back to addresses that are ALREADY in the list above, i.e. only to ORDER the room. A
    # name never admits anybody, so a bad match costs position and nothing else.
    room_participant_names: Optional[dict[str, str]] = None
    # The transcript's speaker labels, already ordered by speaking time DESCENDING (the caller holds
    # the transcript, so it does that arithmetic). Unmatched labels are simply ignored.
    room_speakers: Optional[list[str]] = None
    room_read_max: Optional[int] = None
    # THE SCAFFOLD (PRD 5.5). The terminal sends this on the FIRST turn of a chat a link composed.
    # It is an ID, never a mount list and never prompt text: the record it names says which
    # workspaces this turn mounts and what the opening ask is, and the server reads BOTH from the
    # store. A scaffold that is not this subject's is ignored (never an error) — a stale or
    # forwarded id must not be able to widen anybody's mounts, and must not break their turn either.
    scaffold_id: Optional[str] = None
    # THE INTENT (PRD decision 32/35). A button pressed on a page — Extend, Create this page,
    # Explore a term in a transcript, Highlight the transcript — is an ACT on a named thing, not a
    # sentence somebody typed. The terminal has sent this since decision 32 landed client-side; with
    # `extra="forbid"` above and no field here, every one of those presses 422'd. It is a plain dict
    # rather than a model on purpose: the CLOSED vocabulary is `chat_intents.INTENT_PRESETS`, an
    # unknown kind is ignored, and a client one release ahead must not be refused at the door.
    intent: Optional[dict] = None


class ScaffoldMintBody(BaseModel):
    """`POST /internal/scaffolds` — what a FLOW says at the moment it creates a touch.

    ``extra="forbid"``: a mint is the thing a step checks before it sends, so a caller that spells a
    field wrong has to hear about it here rather than mail a link built from a field nobody read.

    There is deliberately NO field carrying prompt text. ``opening`` is a NAME in `_global/asks/`
    and the server refuses anything that is not one — the URL never carried text (PRD 6) and neither
    does the record behind it, for the same reason: anyone who can mint a touch would otherwise be
    able to drive the recipient's agent."""
    model_config = {"extra": "forbid"}
    who: str                                   # the RECIPIENT ADDRESS. Not a subject: they may not exist yet.
    kind: str                                  # one of scaffolds.KINDS
    opening: str                               # a preset NAME in _global/asks/, never text
    meeting: Optional[str] = None              # the meetings-domain ROW id, or None. PHASE IS NOT STORED.
    workspaces: Optional[list[str]] = None     # slugs to mount; None = derived (see the route)
    refs: Optional[dict] = None                # the facts for the agent: title, when, organizer, participants…
    tabs: Optional[list[str]] = None           # UI half; None = the preset's own `tabs:`
    focus: Optional[str] = None                # UI half; None = the preset's own `focus:`
    # The RESTRICTED transcript share, minted by the caller against the meeting ROW
    # (`flows_steps/meeting.mint_transcript_share`) when the meeting is not the recipient's own.
    # Minted THERE and not here because the mint needs the meeting OWNER's gateway key, which flows
    # holds and agent-api deliberately does not — see the route's docstring.
    share_token: Optional[str] = None
    provenance: Optional[dict] = None          # flow, reaction/run id, minted_by (the fact's admitted_by)


class ScaffoldHandBody(BaseModel):
    """`POST /api/scaffolds/hand` — a HAND LINK (`/?ask=<preset>&meeting=<row>`) turned into a record.

    Two fields, both NAMES, neither prompt text — the same rule as the internal mint and for the same
    reason (PRD 6, decisions 13/18): a URL must never be able to drive somebody's agent. What made
    this route necessary is that the terminal used to substitute `?meeting=`/`?ws=` straight into the
    composed opening, so a crafted link put attacker-chosen text into the first turn.

    There is deliberately NO `who`: the recipient is the SIGNED-IN CALLER, taken from the session.
    A field would let anyone who can reach this route mint a first turn for somebody else."""
    model_config = {"extra": "forbid"}
    preset: str                                # a preset NAME in _global/asks/, never text
    meeting: Optional[str] = None              # a meetings-domain ROW id the CALLER can see, or None


class ResetBody(BaseModel):
    """Body for POST /api/chat/reset — the docs (api/agent.mdx) say it's just ``{session?}``. reset only
    needs the session; ``prompt``/``subject``/``active`` are accepted-and-ignored so a client reusing the
    chat-body shape doesn't 422 (reset must NOT require a prompt the way the chat turn does)."""
    model_config = {"extra": "forbid"}
    session: Optional[str] = Field(default=None, pattern=CHAT_SESSION_PATTERN)
    subject: Optional[str] = None
    prompt: Optional[str] = None
    active: Optional[dict] = None
    context: Optional[ChatContextBody] = None  # accepted-and-ignored, same rationale
    room_meeting_id: Optional[str] = None      # accepted-and-ignored (reset mounts nothing)


class RoutineCreate(BaseModel):
    """The Routines surface / ``/routine`` create form — compiles to a routine.v1 + a schedule.v1 job."""
    model_config = {"extra": "forbid"}
    subject: Optional[str] = None  # DERIVED from X-User-Id (P20); ignored if sent. Dropped client-side in Stage 4.
    name: str
    cron: str
    prompt: str
    run_now: bool = True  # fire one immediate run so the author sees a result without waiting for cron


class RoutineEnabledPatch(BaseModel):
    model_config = {"extra": "forbid"}
    enabled: bool


class WorkspaceSwapBody(BaseModel):
    """Attach a custom external git repo as the subject's workspace. Omit ``repo`` to swap back to seed."""
    model_config = {"extra": "forbid"}
    repo: Optional[str] = None   # git URL to clone (None → swap back to the seeded default)
    ref: Optional[str] = None    # branch/tag/sha to check out (defaults to main)
    slug: Optional[str] = None   # target a parked slot DIRECTLY (e.g. a no-repo backup) — restores, no re-clone
    fresh: bool = False          # swap-to-seed only: rebuild the default from template (start fresh) vs restore the park
    token: Optional[str] = None  # access token for a PRIVATE repo — used for the clone only, never stored (P15)


class WorkspacePublishBody(BaseModel):
    """Publish the subject's vexa-born workspace to GitHub — create the repo (unless ``remote_url``
    targets a pre-created one) and push the current branch's full history. ``token`` is the caller's
    PAT, used server-side for this call only, NEVER stored (P15)."""
    model_config = {"extra": "forbid"}
    repo_name: Optional[str] = None    # name of the repo to create (required unless remote_url is given)
    private: bool = True               # create the repo private (default) or public
    token: Optional[str] = None        # GitHub PAT (repo-creation + push); OPTIONAL — falls back to the caller's SAVED token
    org: Optional[str] = None          # create under this org instead of the user's account
    remote_url: Optional[str] = None   # skip creation and push to this (pre-created/empty) repo
    slug: Optional[str] = None         # target workspace (own slot or shared membership); omitted = the seed-slot workspace


class WorkspaceRenameBody(BaseModel):
    """Set a workspace slot's DISPLAY name (label only — the slug/parked dir are unchanged). Empty clears it."""
    model_config = {"extra": "forbid"}
    slug: str
    name: Optional[str] = None


class WorkspacePushBody(BaseModel):
    """Push a workspace's current branch to its GitHub home (origin / vexa-publish), fast-forward only.
    ``slug`` targets one of the caller's workspaces (default = the primary); ``token`` is the caller's PAT.
    OPTIONAL — when omitted, the caller's SAVED reusable GitHub token (git_credentials) is used. Whichever
    token applies is used for this push only and NEVER stored on the workspace remote (P15)."""
    model_config = {"extra": "forbid"}
    slug: Optional[str] = None
    token: Optional[str] = None


class GitTokenBody(BaseModel):
    """Save (or, with an empty/omitted ``token``, CLEAR) the caller's reusable GitHub token — stored ONCE,
    server-side, and reused as the fallback credential for every git op across all their repos."""
    model_config = {"extra": "forbid"}
    token: Optional[str] = None


class WorkspacePullBody(BaseModel):
    """Fetch + fast-forward a workspace from its GitHub home. ``slug`` targets one of the caller's
    workspaces (default = primary); ``token`` (optional — public repos need none) is used for the fetch
    only and NEVER stored (P15). A divergence is refused, not merged/rebased/forced."""
    model_config = {"extra": "forbid"}
    slug: Optional[str] = None
    token: Optional[str] = None


class WorkspacePurposeBody(BaseModel):
    """Set a workspace's PURPOSE — a one-line statement of what it's for, stored IN the workspace so it
    travels when shared and is read into the agent's mount preamble. ``slug`` targets one of the caller's
    workspaces (default = primary); an empty ``purpose`` clears it."""
    model_config = {"extra": "forbid"}
    slug: Optional[str] = None
    purpose: str = ""


class InviteCreateBody(BaseModel):
    """Mint a scoped invite for a shared workspace (owner/contributor only). Returns the token ONCE."""
    model_config = {"extra": "forbid"}
    workspace_id: str
    role: str = "viewer"                 # viewer | contributor (never owner)
    expires_in_sec: int = 604800         # 7 days
    max_uses: int = 1
    # UNSTATED by default, and the route derives it (Vexa-ai/vexa#1635). It used to default to
    # "open", which meant a caller that named `allowed_emails` and said nothing else got a link
    # ANYONE holding it could redeem — the addresses were stored and never checked, because
    # accept_invite only enforces them in "restricted". A default that silently discards the one
    # thing the caller said about who the invite is for is not a default, it is a trap.
    mode: Optional[str] = None           # open (anyone-with-link) | restricted (allowed_emails only)
    allowed_emails: Optional[list[str]] = None  # restricted mode: the verified emails permitted to redeem


class InviteAcceptBody(BaseModel):
    """Redeem an invite token (any logged-in user). Idempotent per user."""
    model_config = {"extra": "forbid"}
    token: str


class RoleSetBody(BaseModel):
    """Flip a member's role (owner only) — the "change read/write permissions" DoD item."""
    model_config = {"extra": "forbid"}
    role: str                            # viewer | contributor | owner


class WorkspaceInviteBody(BaseModel):
    """Invite ONE address to a workspace — the body behind `workspace_invite` (Vexa-ai/vexa#1632).

    NAMED FIELDS, NOT A BARE `dict`: the assembled edge derives a tool's arguments from the route's
    published body, and a bare dict publishes none."""
    model_config = {"extra": "forbid"}
    slug: str = Field(description="the workspace this is about — always named, never defaulted")
    email: str = Field(description="the person's address, exactly as they gave it — never guessed "
                                   "from a name or a domain")
    role: str = Field("reader", description="owner | contributor | reader — the default is the "
                                            "smallest")


class WorkspaceMembershipBody(BaseModel):
    """Change what an address IS in a workspace, or take them off it — the body behind
    `workspace_membership` (Vexa-ai/vexa#1632).

    ``role`` carries `remove` as a fourth value rather than a second route, because it is one
    question with four answers and an agent choosing between two verbs would have to guess the
    answer before asking it."""
    model_config = {"extra": "forbid"}
    slug: str = Field(description="the workspace this is about — always named, never defaulted")
    email: str = Field(description="the member's address, as the roster shows it")
    role: str = Field(description="owner | contributor | reader, or `remove` to take them off it")


class SharedNewBody(BaseModel):
    """CREATE a new shared workspace (top-level, caller becomes owner) — the bootstrap that makes a
    workspace shareable so invites can be minted against it. ``name`` → display + workspace-id base."""
    model_config = {"extra": "forbid"}
    name: str = "Shared workspace"


class SharedAttachBody(BaseModel):
    """LOAD AN EXISTING REPO into a SHARED (group) workspace — the group counterpart of
    ``POST /api/workspace/swap``. The group's current tree is PARKED (kept, swappable-back), the repo is
    cloned in, and the member list is carried across so nobody loses access.

    ``token`` is the terminal's optional per-call PAT for an https repo; the MCP path never sends one —
    an ssh repo authenticates with the workspace's deploy key, resolved server-side."""
    model_config = {"extra": "forbid"}
    repo: Optional[str] = None   # git URL (ssh → deploy key; https → PAT). None + ``slug`` = swap back
    ref: Optional[str] = None    # branch/tag/sha (defaults to main)
    slug: Optional[str] = None   # a parked slot to restore DIRECTLY (no re-clone), e.g. "seed"
    token: Optional[str] = None  # https only, used for the clone and never stored (P15)


class SharedActiveBody(BaseModel):
    """Switch a shared workspace ON (mount) or OFF (hide) in the caller's active set — per-user, membership
    is unchanged."""
    model_config = {"extra": "forbid"}
    active: bool


class ArchiveBody(BaseModel):
    """Archive (collapse, keep) or un-archive one of the caller's own workspaces."""
    model_config = {"extra": "forbid"}
    archived: bool = True


class WorkspaceImportBody(BaseModel):
    """Import a repository as a new independent workspace using configured credentials."""
    model_config = {"extra": "forbid"}
    repo: str
    ref: str = "main"
    token: Optional[str] = None
    credential_workspace: Optional[str] = None


class WorkspaceActivateBody(BaseModel):
    """ADD a workspace to the subject's active set (the additive mount set — WP-A2.1). Pass ``repo`` to
    clone/restore a git repo, or ``slug`` to activate an already-parked slot. Unlike swap it does NOT park
    the others — the private baseline and any other active workspaces stay mounted."""
    model_config = {"extra": "forbid"}
    repo: Optional[str] = None   # git URL to clone (first time) / restore (thereafter)
    ref: Optional[str] = None    # branch/tag/sha (defaults to main)
    slug: Optional[str] = None   # activate an already-parked slot directly (no repo needed)
    token: Optional[str] = None  # access token for a PRIVATE repo — clone only, never stored (P15)


class WorkspaceNewBody(BaseModel):
    """CREATE a brand-new BLANK workspace (seeded from the template) at a fresh slug and ADD it to the
    active set — the additive-model "new workspace" action. NOT a swap: nothing is parked/rebuilt/backed
    up. ``name`` (optional) → the new workspace's display label (default a unique "New workspace")."""
    model_config = {"extra": "forbid"}
    name: Optional[str] = None


class WorkspaceWriteBody(BaseModel):
    """WRITE one page — the body behind `workspace_write`. Creates the file or replaces it whole."""
    model_config = {"extra": "forbid"}
    path: str = Field(description="workspace-relative path of the page, e.g. `notes/plan.md`")
    content: str = Field(description="the whole file as it should read after this write")
    slug: Optional[str] = Field(None, description=WRITE_SLUG_HELP)


class EntityUpsertBody(BaseModel):
    """RECORD what was learned about a person, company, meeting, project or decision — the body
    behind `entity_upsert`. One call creates `kg/entities/<kind>/<slug>.md` as a card or updates it
    in place; repeating a fact the page already carries writes nothing."""
    model_config = {"extra": "forbid"}
    kind: str = Field(description="person | company | meeting | project | decision")
    name: str = Field(description="what the page is about, as a person would say it; it becomes "
                                  "the title `[[wikilinks]]` resolve to")
    facts: list[str] = Field(default_factory=list, description=(
        "one short sentence each, only what was SAID or READ. Name another entity inside a fact as "
        "`[[Their Name]]`. Filed under `section` when given, else under `## Timeline`."))
    source: str = Field("", description=(
        "where these facts came from, in a few words — the meeting, the mail, the file. Required "
        "with facts: one source for the whole call, stamped onto every fact; split the call when "
        "facts came from different places. A fact with no source is refused."))
    slug: Optional[str] = Field(None, description=WRITE_SLUG_HELP + " Companies belong in the "
                                                  "company layer, `_global`.")
    dates: Optional[dict] = Field(None, description=(
        "for a meeting: any of `scheduled_at`, `held_at`, `report_delivered_at` (ISO-8601 or "
        "epoch). Any other key is dropped."))
    summary: str = Field("", description="the one line under the title; set when the page is "
                                         "created and never overwritten")
    fields: Optional[dict] = Field(None, description=(
        "facts filed into the kind's sections, `{field: value}`; a field naming another entity "
        "links both pages. The sections and fields, by kind:\n" + entities_mod.tool_sections_text()))
    section: str = Field("", description="the section `facts` are filed into, by its name")
    connections: list = Field(default_factory=list, description=(
        "other pages this one links to, both ways:\n" + entities_mod.tool_connection_text()))
    open_questions: list[str] = Field(default_factory=list, description=(
        "what is not known yet, written as the question — a gap goes here, never on the page as a "
        "guess"))

    @field_validator("facts", "open_questions", mode="before")
    @classmethod
    def _one_is_a_list(cls, value):
        """A single fact given as a string is one fact, not a refusal."""
        return [value] if isinstance(value, str) else value


class AssetFetchBody(BaseModel):
    """FETCH a picture into a workspace — the body behind `fetch_asset`. The server fetches it, so a
    page references the stored file relatively and never hotlinks."""
    model_config = {"extra": "forbid"}
    url: str = Field(description="the picture's address on the web")
    path: str = Field("", description="where to store it; omitted, the URL's own file name under "
                                      "`assets/`")
    slug: Optional[str] = Field(None, description=WRITE_SLUG_HELP + " Fetch it into the workspace "
                                                  "of the page that shows it.")


class TranscriptTermsBody(BaseModel):
    """LOOK at what a meeting has named, or PUBLISH the ones that matter — the body behind
    `transcript_terms`."""
    model_config = {"extra": "forbid"}
    meeting_id: str = Field(description="the meeting's row id")
    since: str = Field("", description="the `cursor` your last call on this meeting returned; only "
                                       "what was said after it is read. Omit it the first time.")
    keep: str = Field("", description=(
        "the terms to PUBLISH as chips, comma separated, exactly as the look returned them; `*` for "
        "all of them. Omitted, nothing is published — the first call only looks."))


class GlobalReadyBody(BaseModel):
    """ACCEPT the company layer — the body behind `mark_global_ready`. Both fields are optional: the
    commit is authored by the person whose identity made the call unless these name someone else."""
    model_config = {"extra": "forbid"}
    author_email: Optional[str] = Field(None, description="the accepting admin's address; omitted, "
                                                          "the caller's own")
    author_name: Optional[str] = Field(None, description="their name for the commit; omitted, the "
                                                         "address's local part")


class ClaimProposal(BaseModel):
    """One thing an agent believes about this person's work, and where it came from."""
    model_config = {"extra": "forbid"}
    claim: str = Field(description="the belief, in one short line the person can correct")
    source: str = Field("", description="where it came from, in a few words")
    scope: str = Field("tenant", description="who it is about; `tenant` is the person's company")


class ClaimsProposeBody(BaseModel):
    """PROPOSE claims — the body behind `propose`. One call carries everything learned."""
    model_config = {"extra": "forbid"}
    claims: list[ClaimProposal | str] = Field(description=(
        "every belief at once, each `{claim, source?, scope?}` or a plain string. Nothing proposed "
        "counts as company context until the person answers."))


class ClaimVerdict(BaseModel):
    """A person's word on one proposed claim."""
    model_config = {"extra": "forbid"}
    id: str = Field(description="the claim's id, as `propose` returned it (`c001`)")
    verdict: str = Field(description="confirmed | corrected | rejected")
    note: str = Field("", description="the person's own words — the correction, for `corrected`")


class ClaimVerdictsBody(BaseModel):
    """RECORD the person's answer — the body behind `validate`. One call carries the whole answer."""
    model_config = {"extra": "forbid"}
    verdicts: list[ClaimVerdict] = Field(description=(
        "one per claim the person answered, `{id, verdict, note?}`. Call it only after asking them."))


class WorkspaceRemoveBody(BaseModel):
    """REMOVE one page from a workspace — the body behind `workspace_delete` (Vexa-ai/vexa#1621).

    ``path`` is workspace-RELATIVE; ``slug`` names the workspace (omitted = the chat's target, or the
    caller's own desk when there is none).

    ⚠ WHY THIS IS A POST AND NOT `DELETE /api/workspace/file`, which is what it was written as
    first. `DELETE /api/workspace/{slug}` already exists and DESTROYS A WHOLE WORKSPACE
    irreversibly — and `{slug}` matches the literal segment `file`, so the two routes can match one
    URL and the answer would be decided by which router `create_app` includes first.
    `test_route_table.test_no_two_routes_can_match_the_same_url` caught it, which is the entire
    point of that gate: of all the pairs to leave to registration order, "remove one page" and
    "destroy the workspace" is the worst. A POST on its own literal path cannot be confused with
    anything, and it reads beside its sibling `POST /api/workspace/move`."""
    model_config = {"extra": "forbid"}
    path: str = Field(description="workspace-relative path of the page to remove")
    slug: Optional[str] = Field(None, description=WRITE_SLUG_HELP)


class WorkspaceMoveBody(BaseModel):
    """MOVE one page from one path to another — the body behind `workspace_move` (Vexa-ai/vexa#1621).

    ``path`` (where the page is now) and ``to`` are workspace-RELATIVE paths — ``path``, like every
    other page verb, and not ``from``, which no tool signature can carry because it is a Python
    keyword. ``slug`` names the workspace the page is in today; ``to_slug`` names where it is going,
    and omitted means *the same workspace*, which is the ordinary rename.

    A CROSS-WORKSPACE MOVE IS A WRITE IN THE TARGET AND A DELETE IN THE SOURCE — two repositories,
    two commits, and either end being read-only refuses the whole call before anything is written."""
    model_config = {"extra": "forbid"}
    path: str = Field(description="workspace-relative path of the page as it is now")
    to: str = Field(description="workspace-relative path it should have after the move")
    slug: Optional[str] = Field(None, description=WRITE_SLUG_HELP)
    to_slug: Optional[str] = Field(None, description=(
        "the workspace it is going to; omit it for a rename inside the same workspace, pass "
        "`personal` for the person's own desk"))


class WorkspaceDeactivateBody(BaseModel):
    """REMOVE a workspace from the active set (park it — never destroyed). The private baseline cannot be
    deactivated (it is the subject's durable memory root)."""
    model_config = {"extra": "forbid"}
    slug: str
