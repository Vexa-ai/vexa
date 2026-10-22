"""bodies.py — the request bodies agent-api's routes take, as pydantic models.

One home for every named body, so a route's published schema (which the assembled MCP derives tool
arguments from) and the model a handler validates are the same class, whichever router serves it.
Moved out of `api_shared.py` unchanged; the class names are the OpenAPI schema names.
"""
from __future__ import annotations

from typing import Annotated, Optional

from pydantic import BaseModel, Field, StringConstraints, field_validator

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

#: THE ONE TYPE for a chat session id at every door — a body field, a query parameter, a path
#: parameter, a list item. Any value outside :data:`CHAT_SESSION_PATTERN` is a 422 before it reaches
#: a key, a file name or a workload name. A door that needs a session NAMED (not the default) adds
#: ``min_length=1`` beside it.
SessionId = Annotated[str, StringConstraints(pattern=CHAT_SESSION_PATTERN)]


class ChatBody(BaseModel):
    model_config = {"extra": "forbid"}
    prompt: str
    # subject is DERIVED server-side from X-User-Id (P20) — kept here only so a client that still sends it
    # doesn't 422 (extra=forbid); the value is IGNORED. Dropped from the client in Stage 4.
    subject: Optional[str] = None
    session: Optional[SessionId] = None
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
    # ADR-0048 decision 4: items the speaker explicitly shares into a shared session for THIS turn,
    # each `workspace/path`, read with the speaker's own access — the one exception to the leak rule.
    share: Optional[list[str]] = Field(default=None, max_length=5)
    # ADR-0048: the people this message @-mentions, by the account id the composer's picker carried
    # (never a name to look up). agent-api keeps only ids the speaker shares a workspace with and
    # tells the agent who each one is, so "@joe" names exactly one account.
    mentions: Optional[list[Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._@+-]{0,199}$")]]] = Field(
        default=None, max_length=10)
    # The message is for the agent, as if it opened with @vexa — what the composer sends for the
    # first message of a chat that just became shared, which was typed where the agent answers
    # everything. It grants nothing a person could not get by typing @vexa.
    to_agent: Optional[bool] = None
    # A REPLY TO THE AGENT'S MESSAGE (rooms, 2026-10-15): the turn id of the agent message this one
    # answers. In a room it is addressed to the agent, like @vexa — rule 1 of the room sensor.
    reply_to: Optional[Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")]] = None


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
    session: Optional[SessionId] = None
    subject: Optional[str] = None
    prompt: Optional[str] = None
    active: Optional[dict] = None
    context: Optional[ChatContextBody] = None  # accepted-and-ignored, same rationale
    room_meeting_id: Optional[str] = None      # accepted-and-ignored (reset mounts nothing)


class DefaultTargetBody(BaseModel):
    """The workspace a person's new chats write to — the "Writes to …" menu's "Make default"."""
    workspace: str = Field(default="", max_length=128,
                           description="a workspace slug the person can write, or \"\" for Personal")


class ChatModelBody(BaseModel):
    """``POST /api/chat/model`` — pick which of the operator's models one chat runs on."""
    model_config = {"extra": "forbid"}
    session: Optional[SessionId] = Field(default=None,
                                         description="the chat session; the default chat when absent")
    model: str = Field(max_length=64, description="a model id from GET /api/models/catalog; "
                                                  "empty puts the chat back on your default")
    effort: Optional[str] = Field(default=None, max_length=16,
                                  description="an effort level the model lists in its "
                                              "capabilities.reasoning_efforts; empty or absent runs "
                                              "it at its default_effort")


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
    """Publish a vexa-born workspace: create a repository on one of the deployment's git providers
    (unless ``remote_url`` names a pre-created one) and push the current branch's full history.
    ``token`` is optional — without it the person's saved token for that provider is used — and is
    used server-side for this call only, NEVER stored (P15)."""
    model_config = {"extra": "forbid"}
    repo_name: Optional[str] = Field(default=None, description="name of the repository to create (required unless remote_url is given)")
    private: bool = Field(default=True, description="create the repository private (the default) or public")
    token: Optional[str] = None        # per-call token from the terminal; never an agent argument — falls back to the saved one
    org: Optional[str] = Field(default=None, description="the owner to create under: a GitHub org, or a GitLab group path (nested allowed: group/subgroup); omitted = the token's own account")
    remote_url: Optional[str] = Field(default=None, description="skip creation and push to this pre-created, empty https repository")
    slug: Optional[str] = Field(default=None, description="the workspace to publish (one the caller manages); omitted = their own")
    provider: Optional[str] = Field(default=None, description="the configured git provider to create on, by name (git_providers lists them); omitted = the default")
    host: Optional[str] = None         # earlier spelling of provider: the provider's host name
    pressed: Optional[str] = Field(
        default=None, max_length=128, pattern=r"^[^\s\]]{1,128}$",
        description="the `#<pending>` of the person's pressed answer to the card that offered this "
                    "(`[answer <card> #<pending>] …`). An agent publishes only on that press: offer a "
                    "```ask card naming the workspace or the repository first")


class WorkspaceRenameBody(BaseModel):
    """Set a workspace slot's DISPLAY name (label only — the slug/parked dir are unchanged). Empty clears it."""
    model_config = {"extra": "forbid"}
    slug: str
    name: Optional[str] = None


class WorkspacePushBody(BaseModel):
    """Push a workspace's current branch to its git home (origin / vexa-publish), fast-forward only.
    ``slug`` targets one of the caller's workspaces (default = the primary); ``token`` is the caller's PAT.
    OPTIONAL — when omitted, the caller's SAVED token for the home's provider (git_credentials) is used. Whichever
    token applies is used for this push only and NEVER stored on the workspace remote (P15)."""
    model_config = {"extra": "forbid"}
    slug: Optional[str] = None
    token: Optional[str] = None


class GitTokenBody(BaseModel):
    """Save (or, with an empty/omitted ``token``, CLEAR) the caller's reusable token for one git
    provider — stored ONCE in the credential broker and used as the fallback credential for every git
    op on that provider. ``provider`` omitted = the default provider."""
    model_config = {"extra": "forbid"}
    token: Optional[str] = None
    provider: Optional[str] = None


class WorkspacePullBody(BaseModel):
    """Fetch + fast-forward a workspace from its git home. ``slug`` targets one of the caller's
    workspaces (default = primary); ``token`` (optional — public repos need none) is used for the fetch
    only and NEVER stored (P15). A divergence is refused unless ``merge`` asks for a merge."""
    model_config = {"extra": "forbid"}
    slug: Optional[str] = None
    token: Optional[str] = None
    merge: bool = Field(default=False, description=(
        "When the workspace and its repository have BOTH moved: merge the repository's commits in "
        "(never a rebase or a force). A conflict changes nothing and names the files."))


class WorkspaceStatusBody(BaseModel):
    """Which workspace to look at: ``slug`` names one of the caller's own or shared workspaces; omitted,
    their own. There is no token field — the look uses the credential a pull would (the workspace's
    deploy key or the person's saved token), and a credential is never an argument."""
    model_config = {"extra": "forbid"}
    slug: Optional[str] = None


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
    role: str = "viewer"                 # viewer | contributor (never owner; ≤ the caller's own role)
    # CAPPED (D5, security review C3/test 6): the bounds of the newer link body, enforced again in
    # ``workspace_membership.mint_invite`` for every caller that is not this route.
    expires_in_sec: int = Field(default=604800, ge=1, le=365 * 86400)   # 7 days; at most a year
    max_uses: int = Field(default=1, ge=1, le=1000)
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


class InviteSigninClaimBody(BaseModel):
    """The terminal spends the sign-in half of a mailed invite link (internal tier, PRD 0.13.4 S2)."""
    model_config = {"extra": "forbid"}
    token: str = Field(min_length=8, max_length=256)


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
    role: str = Field("contributor", description=(
        "owner | contributor | reader. Omitted, contributor — when the person names no role, do "
        "not ask: invite as contributor and say so in the one-line confirmation; they can change "
        "it after with `workspace_membership`."))
    room: Optional[str] = Field(None, max_length=128, description=(
        "optional: the id of a ROOM in this workspace to invite them into — the invite email names "
        "the room and the link lands in it. Omitted, the invite is to the workspace."))


class WorkspaceForgetBody(BaseModel):
    """FORGET something — the body behind `workspace_forget` (Vexa-ai/vexa#1783)."""
    model_config = {"extra": "forbid"}
    slug: Optional[str] = Field(None, description="the workspace; omitted, the person's own")
    paths: list[str] = Field(default_factory=list, description=(
        "files or folders to forget, workspace-relative — an attachment is its `uploads/…` path"))
    text: str = Field("", description=(
        "a name or phrase to forget wherever it is written. Never pass an identifier's digits here: "
        "use `identifiers` instead"))
    identifiers: bool = Field(False, description=(
        "also remove every government id, passport, card or account number and credential found"))
    confirm: bool = Field(False, description=(
        "false (first call): answers what WOULD be forgotten, changing nothing. true: only after "
        "the person said yes to that"))
    push: bool = Field(False, description=(
        "with confirm: also replace the workspace's remote repository history — only when the "
        "person said yes to the force-push the first answer described"))


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


class WorkspaceMemberAddBody(BaseModel):
    """Add someone who already has an account to a workspace — the body behind
    `workspace_member_add` (ADR-0048). ``person`` is the account id an @-mention carries, or an
    email; ``workspace`` is a slug or a name the caller's own workspaces answer to."""
    model_config = {"extra": "forbid"}
    workspace: str = Field(min_length=1, max_length=200,
                           description="the workspace, by its slug or its name")
    person: str = Field(min_length=1, max_length=320,
                        description="the person's account id (from an @-mention) or their email")
    role: str = Field(default="write", description="read | write")


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
    slug: Optional[str] = Field(default=None, max_length=63,
                                description="The slug to use instead of one made from the name: lowercase "
                                            "letters, digits and dashes. Refused, never changed, when taken.")


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
    name: str = Field(description="what the page is about, as a person would say it, in any "
                                  "script; it becomes the title `[[wikilinks]]` resolve to")
    facts: list[str] = Field(default_factory=list, description=(
        "a list of strings, one short sentence each, only what was SAID or READ — the same for every "
        "kind. Name another entity inside a fact as `[[Their Name]]`. Filed under `section` when "
        "given, else under `## Timeline`."))
    source: str = Field("", description=(
        "where these facts came from, in a few words — the meeting, the mail, the file, e.g. "
        "\"2026-10-13 call with Ada Lovelace\", \"Ada's email of 2026-10-12\" or "
        "\"notes/roadmap.md\". Required with facts: one source for the whole call, stamped onto "
        "every fact (do not write it inside the facts); split the call when facts came from "
        "different places. A fact with no source is refused."))
    slug: Optional[str] = Field(None, description=WRITE_SLUG_HELP + " Companies belong in the "
                                                  "company layer, `_global`.")
    dates: Optional[dict] = Field(None, description=(
        "for a meeting: any of `scheduled_at`, `held_at`, `report_delivered_at` (ISO-8601 or "
        "epoch). Any other key is dropped."))
    summary: str = Field("", description="the one line under the title; set when the page is "
                                         "created and never overwritten")
    fields: Optional[dict] = Field(None, description=(
        "facts filed into the kind's sections, `{field: value}`, each value plain text filed as "
        "written. A field naming others (`who`, `participants`, `people`, `company`) links every "
        "page it names, both ways: `[[Ada Lovelace]], [[Grace Hopper]]`, or plain `Ada Lovelace and "
        "Grace Hopper`. The sections and fields, by kind:\n" + entities_mod.tool_sections_text()))
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


class MeetingAttachBody(BaseModel):
    """MAKE A PAGE THE MEETING'S PAGE — the body behind `meeting_attach_page` (#1783)."""
    model_config = {"extra": "forbid"}
    path: str = Field(description="the page, `kg/entities/meeting/<name>.md`")
    workspace: str = Field("", description="the workspace the page is in; empty for the person's own")
    meeting_id: str = Field("", description="the meeting's row id, when the meeting exists")
    meeting_url: str = Field("", description="the meeting's link, before the meeting exists")
    native: str = Field("", description="the meeting's native id (from `parse_meeting_link`)")
    platform: str = Field("", description="the meeting's platform (from `parse_meeting_link`)")
    calendar_event: str = Field("", description="the calendar event's id, when known")
    replace: bool = Field(False, description="true to replace a page the meeting already has, after "
                                            "you merged what it held")


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
    source: str = Field("", max_length=300, description="where it came from, in a few words")
    scope: str = Field("tenant", max_length=40,
                       description="who it is about; `tenant` is the person's company")


class ClaimsProposeBody(BaseModel):
    """PROPOSE claims — the body behind `propose`. One call carries everything learned."""
    model_config = {"extra": "forbid"}
    claims: list[ClaimProposal | str] = Field(max_length=50, description=(
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
    verdicts: list[ClaimVerdict] = Field(max_length=50, description=(
        "one per claim the person answered, `{id, verdict, note?}`. Call it only after asking them."))


class WorkspaceRemoveBody(BaseModel):
    """REMOVE one page from a workspace — the body behind `workspace_delete` (Vexa-ai/vexa#1621).

    ``path`` is workspace-RELATIVE; ``slug`` names the workspace (omitted = the chat's target, or the
    caller's personal workspace when there is none).

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


class WorkspaceRestoreBody(BaseModel):
    """PUT BACK one page a removal took out — the body behind the page view's **Undo** and History's
    **Restore** (founder 2026-10-11: *"we probably need a delete for a note"*).

    ``path`` and ``slug`` as on `WorkspaceRemoveBody`. ``commit`` is the removal to undo: the sha
    `POST /api/workspace/remove` answered with, or a History row's. Omitted = the newest commit that
    removed ``path``. The bytes come from that commit's parent, so what returns is exactly what went."""
    model_config = {"extra": "forbid"}
    path: str = Field(description="workspace-relative path of the page to put back")
    slug: Optional[str] = Field(None, description=WRITE_SLUG_HELP)
    commit: Optional[str] = Field(None, description=(
        "the commit that removed the page; omit it for the newest removal of that path"))


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
        "`personal` for the person's personal workspace"))


class WorkspaceDeactivateBody(BaseModel):
    """REMOVE a workspace from the active set (park it — never destroyed). The private baseline cannot be
    deactivated (it is the subject's durable memory root)."""
    model_config = {"extra": "forbid"}
    slug: str


class WorkspaceTargetBody(BaseModel):
    """Point this conversation's writes at a workspace — `workspace_target` (Vexa-ai/vexa#1611,
    #1783). ``slug`` is a workspace this person can WRITE; empty means their own desk. The route
    only answers whether that is a target; the chat's record is written by the focus event the
    harness derives from the answer."""
    model_config = {"extra": "forbid"}
    slug: str = Field(default="", description="The workspace to work in from now on; empty = the person's own desk.")


class PageOpenBody(BaseModel):
    """Open something in the person's pages panel — `open_page` (Vexa-ai/vexa#1586, #1783)."""
    model_config = {"extra": "forbid"}
    target: str = Field(..., description=(
        "`meeting:transcript` (the live transcript of `meeting`), `meeting:note` (that meeting's "
        "own note), `<path>` (a file on the person's desk) or `<slug>/<path>` (a file in a shared "
        "workspace they belong to)."))
    meeting: str = Field(default="", description="The meeting's row id — required for the two `meeting:` targets.")


class InviteLinkCreateBody(BaseModel):
    """A link to a workspace that lets up to ``max_uses`` people join it — the body behind
    `workspace_invite_link_create` (#1783). ``slug`` is the workspace, by slug or by name."""
    model_config = {"extra": "forbid"}
    slug: str = Field(min_length=1, max_length=200, description="the workspace, by its slug or its name")
    max_uses: int = Field(default=10, ge=1, le=1000, description="how many people the link lets in")
    expires_in_days: Optional[int] = Field(default=7, ge=1, le=365,
                                           description="days until the link stops working (default 7)")
    role: str = Field(default="contributor",
                      description="what joiners get: contributor (read and write; also 'member') or reader")


class InviteLinkRevokeBody(BaseModel):
    """Stop one of a workspace's links working — `workspace_invite_link_revoke`."""
    model_config = {"extra": "forbid"}
    slug: str = Field(min_length=1, max_length=200, description="the workspace, by its slug or its name")
    link_id: str = Field(min_length=1, max_length=64, description="the link's id from workspace_invite_links")


class JoinLinkCreateBody(BaseModel):
    """A link to a CHAT (the generic door; workspace links use `InviteLinkCreateBody`)."""
    model_config = {"extra": "forbid"}
    target_kind: str = Field(description="chat")
    target_id: str = Field(min_length=1, max_length=128)
    max_uses: int = Field(default=10, ge=1, le=1000)
    expires_in_days: Optional[int] = Field(default=7, ge=1, le=365)
    role: str = Field(default="write", description="read | write")


class JoinLinkRevokeBody(BaseModel):
    model_config = {"extra": "forbid"}
    target_kind: str
    target_id: str = Field(min_length=1, max_length=128)
    link_id: str = Field(min_length=1, max_length=64)


class ListenLabel(BaseModel):
    """One thing the meeting's signal model listens for."""
    model_config = {"extra": "forbid"}
    name: str = Field(description="lower_snake_case label, e.g. `decision`, `blocker`, `price_mentioned`")
    question: Optional[str] = Field(None, description="the yes/no question the model scores each line "
                                    "against (0..1), e.g. `Did someone name a blocker to the install?`")
    wake: bool = Field(True, description="true: a line scoring high on this label counts toward waking "
                       "you; false: it is only highlighted on the transcript")


class ListenBrief(BaseModel):
    """What to listen for in this meeting. Fields you leave out keep their current value."""
    model_config = {"extra": "forbid"}
    focus: Optional[str] = Field(None, description="one or two sentences: what matters in this meeting")
    labels: Optional[list[ListenLabel]] = Field(None, description="the labels to score; "
                                                "`addressed_to_vexa` is always kept and always wakes you")
    known: Optional[str] = Field(None, description="what you already know from this meeting, in a few "
                                 "lines — the model scores what is NEW against it")
    wake_at: Optional[float] = Field(None, description="how much new information wakes you (0.2–10, "
                                     "default 1.0): lower hears more, higher hears less")
    max_wait_s: Optional[int] = Field(None, description="wake anyway after this many seconds with "
                                      "anything scored (30–1800, default 300)")
    threshold: Optional[float] = Field(None, description="the score (0–1, default 0.6) from which a "
                                       "line is highlighted on the transcript; display only, it "
                                       "does not wake you")


class MeetingListenHereBody(BaseModel):
    """«Слушать здесь»: the caller's chat becomes the one a live call wakes."""
    model_config = {"extra": "forbid"}
    session: SessionId = Field(description="the caller's chat that listens to the call from now on")


class MeetingOptinsBody(BaseModel):
    """The call's opt-ins, set by the meeting's owner; a field left out keeps its value."""
    model_config = {"extra": "forbid"}
    speak: Optional[bool] = Field(None, description="may a signal turn answer out loud in the meeting")
    post_chat: Optional[bool] = Field(None, description="may a signal turn post in the meeting's chat")


class MeetingListenBody(BaseModel):
    """SET OR REVISE a meeting's listening brief — the body behind `meeting_listen`."""
    model_config = {"extra": "forbid"}
    meeting: Annotated[str, StringConstraints(pattern=r"^[0-9]{1,20}$")] = Field(
        description="the meeting's row id")
    brief: ListenBrief = Field(description="what to listen for; omitted fields keep their current value")
