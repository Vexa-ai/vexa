"""WORKSPACES — the knowledge, in both regimes. Forwards to agent-api.

CLOUD is the default: the files live on the stack and these verbs read and write them there.
LOCAL means the workspace lives on the person's own machine and no cloud agent runs for them —
these verbs still operate on the CLOUD copy, git is the sync (``workspace_pull`` /
``workspace_push``), and the person's own agent writes the local files itself with its native tools.
``workspace_regime`` records which, and changes nothing else: a regime is a fact about where the
files are, not a second implementation of the verbs.
"""
from __future__ import annotations

import json
import time
import urllib.parse

from .. import config
from ..config import AGENT_API
from ..httpc import http as _http
from ..identity import anon_guard, caller_email, me, subject
from ..shaping import capped, deploy_key_state, refuse_credentials, ws_url
from ..registry import tool


def _regime(uid: str) -> dict:
    try:
        return json.loads(config.REGIMES.read_text()).get(str(uid),
                                                          {"mode": config.WORKSPACE_REGIME})
    except Exception:  # noqa: BLE001
        return {"mode": config.WORKSPACE_REGIME}


def _regime_set(uid: str, rec: dict) -> None:
    try:
        d = json.loads(config.REGIMES.read_text())
    except Exception:  # noqa: BLE001
        d = {}
    d[str(uid)] = rec
    config.REGIMES.parent.mkdir(parents=True, exist_ok=True)
    config.REGIMES.write_text(json.dumps(d, indent=1))


@tool
@anon_guard
def workspace_tree(slug: str = "", token: str = "") -> str:
    """List every file in a workspace. uid is the platform user id; slug selects a group
    workspace, omitted means that person's own.\n\n    If you have not called whats_waiting() yet this session, call it first."""
    uid = me()
    q = f"?slug={slug}" if slug else ""
    st, body = _http("GET", f"{AGENT_API}/api/workspace/tree{q}", {"X-User-Id": uid})
    # A CAPABILITY line, never key material: whether this workspace was loaded from a repository, and
    # whether a credential for it exists at all. It is what lets you answer "can we push this back?"
    # without going looking — and the only shape a credential ever takes in front of a model.
    home = None
    sst, sbody = _http("GET", f"{AGENT_API}/api/workspace/git-remote-status{q}", {"X-User-Id": uid})
    if sst == 200 and isinstance(sbody, dict) and sbody.get("has_home"):
        home = f"{sbody.get('remote')} {sbody.get('url')} on {sbody.get('branch')}"
    return capped({"for_display": "every file here is reachable at <base>/w/<path>?token=... — but NEVER show a person these paths: they are arguments for workspace_read/write; show names and links", "status": st, "result": body, "git_home": home or "no git home — this workspace was not loaded from a repository"}, 8000)


@tool
@anon_guard
def workspace_read(path: str, slug: str = "", token: str = "") -> str:
    """Read one file out of a workspace — the knowledge behind any claim."""
    uid = me()
    q = f"?path={urllib.parse.quote(path)}" + (f"&slug={slug}" if slug else "")
    st, body = _http("GET", f"{AGENT_API}/api/workspace/file{q}", {"X-User-Id": uid})
    name = path.rsplit("/", 1)[-1]
    return json.dumps({"status": st, "url": ws_url(path, token or ""),
                       "paste_this_link": f"[{name}]({ws_url(path, token or '')})",
                       "never_show_the_path": "the path is an argument for tools; your "
                       "person sees the name and the link above, nothing slashed",
                       "result": body})[:12000]


@tool
@anon_guard
def workspace_write(path: str, content: str, slug: str = "", token: str = "") -> str:
    """Write a file into a workspace.

    NOTE: agent-api exposes no HTTP write — only an agent turn writes knowledge. This goes
    in through the container's own view of the volume and is a DEV DOUBLE for that missing
    endpoint; it is the gap to close before workspaces are genuinely agent-controllable."""
    uid = me()
    # THE ROUTE THE DOCSTRING ABOVE CALLS MISSING EXISTS. The rig wrote workspace files with
    # `docker exec -i vexa-dogfood-agent-api-1 sh -c 'mkdir -p "$(dirname {target})" && cat >
    # {target}'` — a docker socket, a hardcoded container name, and `target` built from this
    # caller's own `path`, unquoted (audit V4). agent-api's page editor has written through
    # `PUT /api/workspace/file` since decision 24: it authorises against the mount rules, refuses
    # `kg/templates/`, and commits so history stays honest.
    st, body = _http("PUT", f"{AGENT_API}/api/workspace/file", {"X-User-Id": uid},
                     {"path": path, "content": content, "slug": slug or ""})
    if st == 403:
        return json.dumps({"refused": (body or {}).get("detail") if isinstance(body, dict) else str(body)[:200],
                           "do": "say so plainly in one sentence — do not retry the same write"})
    if not (200 <= st < 300):
        return json.dumps({"error": "could not write that file", "status": st,
                           "detail": str(body)[:300], "do": "report_friction() with this"})
    link = ws_url(path, token or "")
    return json.dumps({"url": link, "paste_this_link": "[" + path.rsplit("/", 1)[-1] + "](" + link + ")",
                       "written": path, "bytes": len(content),
                       "never_show_the_path": "the path is an argument for tools; your person sees "
                                              "the name and the link above, nothing slashed"})


@tool
@anon_guard
def workspace_new(name: str, purpose: str = "", token: str = "") -> str:
    """Create a SHARED workspace — a place a team writes into together — and own it.

    Use when your person says "a space for the standup team", "somewhere we all keep this",
    "a workspace for the Acme deal". Their personal workspace already exists and is not this:
    this one has members, and meeting write-ups can land in it for everyone.

    `purpose` is one line saying what belongs here ("everything about the Acme deal"). It is
    stored IN the workspace, so it travels when shared, and every agent that mounts it reads it
    — which is how three mounted workspaces stay straight instead of blurring. Ask for it if
    they did not say; do not invent one."""
    uid = me()
    st, r = _http("POST", f"{AGENT_API}/api/workspace/shared/new", {"X-User-Id": uid},
                  {"name": name})
    if st not in (200, 201):
        return json.dumps({"error": "could not create that workspace", "status": st,
                           "detail": str(r)[:200],
                           "do": "tell them in one plain sentence, and report_friction()"})
    wid = (r or {}).get("workspace_id")
    out = {"created": wid, "name": name, "you_are": "owner"}
    if purpose:
        stp, _ = _http("POST", f"{AGENT_API}/api/workspace/purpose", {"X-User-Id": uid},
                       {"slug": wid, "purpose": purpose})
        out["purpose_set"] = stp in (200, 201)
    out["tell_your_person"] = (
        f"'{name}' exists and it is theirs — anything written there is shared with whoever they "
        f"let in.")
    out["next_options"] = [
        "Invite someone — workspace_invite(slug, role)",
        "Say what belongs here — workspace_purpose(slug, text)" if not purpose else
        "Point a meeting's write-up at it",
        "See what is in it — workspace_tree(slug)",
    ]
    return json.dumps(out)


@tool
@anon_guard
def workspace_attach(workspace: str = "", repo: str = "", ref: str = "main", token: str = "") -> str:
    """LOAD AN EXISTING repository as a workspace — "load the ASWF DNA workspace from github.com/... into
    this group", "we already keep this on GitHub, use that one".

    `workspace` is the group's slug (workspaces() lists them); empty means their own personal workspace.
    `repo` is the repository URL — an `ssh://`/`git@` URL uses this workspace's deploy key, an `https://`
    URL uses their saved token if they have one. `ref` is the branch.

    NEVER put a credential in any argument. There is no parameter for one and a URL carrying one is
    refused: if the repo is private and we have no credential yet, the result hands you a PUBLIC KEY —
    tell them to add it to that repository as a deploy key with write access, and to say `done` when
    they have. Then call this again.

    What is already there is not destroyed: the workspace's current contents are parked and can be
    swapped back to. If the repo is not a Vexa-shaped workspace it is nested under `kg/` inside one."""
    refusal = refuse_credentials(repo, ref, workspace, token)
    if refusal:
        return json.dumps({"refused": refusal, "next": "call again with just the repository URL"})
    uid = me()
    if not repo:
        return json.dumps({"error": "which repository?", "ask": "the repo URL, e.g. git@github.com:acme/kg.git"})
    if workspace:
        st, body = _http("POST", f"{AGENT_API}/api/workspace/shared/{workspace}/attach",
                         {"X-User-Id": uid}, {"repo": repo, "ref": ref or "main"})
    else:
        st, body = _http("POST", f"{AGENT_API}/api/workspace/swap",
                         {"X-User-Id": uid}, {"repo": repo, "ref": ref or "main"})
    if st == 403:
        return json.dumps({"error": "they can read that workspace but not replace it",
                           "tell_your_person": "an owner or contributor has to load a repo into a group workspace"})
    if st not in (200, 201):
        detail = str((body or {}).get("detail") if isinstance(body, dict) else body)[:600]
        out = {"error": "could not load that repository", "status": st, "detail": detail}
        if "deploy key" in detail or "ssh-ed25519" in detail or st == 502:
            out.update(deploy_key_state(uid, workspace, repo))
        return json.dumps(out)
    b = body or {}
    state = b.get("state") or ("cloned" if b.get("cloned") else "attached")
    return json.dumps({
        "workspace": workspace or "personal", "repo": b.get("repo"), "ref": b.get("ref"),
        "state": state, "parked": b.get("parked"), "nested": b.get("nested"),
        "tell_your_person": (f"Loaded {repo} — it is the workspace now. What was here before is parked "
                             f"and can be brought back."
                             if state == "cloned" else
                             f"That repository was already here; {state}."),
        "next_options": ["See what arrived — workspace_tree(slug)",
                         "Bring in later changes — workspace_pull(workspace)",
                         "Send our work back — workspace_push(workspace)"],
    })


@tool
@anon_guard
def workspace_pull(workspace: str = "", token: str = "") -> str:
    """Bring the outside IN to a workspace — by whichever route that workspace has.

    A workspace LOADED FROM A REPOSITORY (workspace_attach) has a git home, and this fetches and
    fast-forwards it: their teammates' commits arrive. A divergence is reported, never merged or forced.
    `workspace` is a group's slug; empty means their own.

    A workspace with NO git home falls back to the LOCAL-REGIME mirror this tool has always been:
    every personal file with its url, to fetch with workspace_read and write under local_path.

    No credential argument, and none is accepted — the deploy key or saved token is resolved
    server-side, and a missing one comes back as a key to add, not a box to fill."""
    refusal = refuse_credentials(workspace, token)
    if refusal:
        return json.dumps({"refused": refusal})
    uid = me()
    q = f"?slug={workspace}" if workspace else ""
    sst, sbody = _http("GET", f"{AGENT_API}/api/workspace/git-remote-status{q}", {"X-User-Id": uid})
    if sst == 200 and isinstance(sbody, dict) and sbody.get("has_home"):
        st, body = _http("POST", f"{AGENT_API}/api/workspace/pull", {"X-User-Id": uid},
                         {"slug": workspace or None})
        if st in (200, 201):
            b = body or {}
            return json.dumps({
                "from": b.get("url"), "branch": b.get("branch"), "updated": b.get("updated"),
                "was_behind": b.get("behind_before"),
                "tell_your_person": (f"Pulled {b.get('behind_before')} new commit(s) from {b.get('url')}."
                                     if b.get("updated") else "Already up to date with the repository."),
            })
        detail = str((body or {}).get("detail") if isinstance(body, dict) else body)[:600]
        out = {"error": "could not pull", "status": st, "detail": detail}
        if st in (400, 502) and "fast-forward" not in detail:
            out.update(deploy_key_state(uid, workspace, sbody.get("url") or ""))
        return json.dumps(out)
    if workspace:
        return json.dumps({"no_home": workspace,
                           "tell_your_person": "That workspace was not loaded from a repository, so there "
                                               "is nothing to pull from.",
                           "next": "workspace_attach(workspace, repo) loads one"})
    reg = _regime(uid)
    st, body = _http("GET", f"{AGENT_API}/api/workspace/tree", {"X-User-Id": uid})
    files = (body or {}).get("files", []) if isinstance(body, dict) else []
    return json.dumps({
        "regime": reg,
        "files": [{"path": f, "url": ws_url(f, token or "")} for f in files][:200],
        "do": "fetch each file you do not already have locally (workspace_read) and write "
              "it under local_path with the same relative path. Then work locally.",
    })[:14000]


@tool
@anon_guard
def workspace_push(workspace: str = "", token: str = "") -> str:
    """Send this workspace's commits back to the repository it came from (fast-forward only — never a
    force push). `workspace` is a group's slug; empty means their own.

    No credential argument, and none is accepted: the workspace's deploy key or their saved token is
    resolved server-side. If neither exists the result carries a public key to add — say that, and ask
    them to say `done` when it is added."""
    refusal = refuse_credentials(workspace, token)
    if refusal:
        return json.dumps({"refused": refusal})
    uid = me()
    st, body = _http("POST", f"{AGENT_API}/api/workspace/push", {"X-User-Id": uid},
                     {"slug": workspace or None})
    if st in (200, 201):
        b = body or {}
        return json.dumps({"pushed": b.get("branch"), "to": b.get("url"), "head": (b.get("head_sha") or "")[:8],
                           "tell_your_person": f"Pushed to {b.get('url')} on {b.get('branch')}."})
    detail = str((body or {}).get("detail") if isinstance(body, dict) else body)[:600]
    out = {"error": "could not push", "status": st, "detail": detail}
    if st in (400, 502):
        out.update(deploy_key_state(uid, workspace, ""))
    return json.dumps(out)


@tool
@anon_guard
def workspace_purpose(slug: str = "", text: str = "", token: str = "") -> str:
    """What a workspace is FOR, in one line. Call with just `slug` to read it.

    Stored in the workspace itself, committed to its history, and read into the agent preamble
    on every dispatch — so an agent with several workspaces mounted knows what belongs where.
    A sentence, not a document. An empty `text` clears it."""
    uid = me()
    if not text:
        st, r = _http("GET", f"{AGENT_API}/api/workspace/purpose?slug={slug}",
                      {"X-User-Id": uid})
        return json.dumps({"purpose": (r or {}).get("purpose") or None, "slug": slug or "personal",
                           "note": "empty means nobody has said what this is for yet"})
    st, r = _http("POST", f"{AGENT_API}/api/workspace/purpose", {"X-User-Id": uid},
                  {"slug": slug or None, "purpose": text})
    if st not in (200, 201):
        return json.dumps({"error": "could not set that", "status": st, "detail": str(r)[:160]})
    return json.dumps({"purpose": text, "slug": slug or "personal",
                       "tell_your_person": "One line, and every agent that opens this workspace "
                                           "reads it."})


@tool
@anon_guard
def workspace_members(slug: str, token: str = "") -> str:
    """Who is in a shared workspace, and what they can do. owner writes and invites; contributor
    writes; viewer reads."""
    uid = me()
    # X-User-Email lets the endpoint backfill the CALLER's own label, so the roster shows a
    # person instead of a subject id. It cannot invent anyone else's — theirs fills in when they
    # next call, which is why a fresh workspace shows ids for members who have not been back.
    hdr = {"X-User-Id": uid}
    em = caller_email()
    if em:
        hdr["X-User-Email"] = em
    st, r = _http("GET", f"{AGENT_API}/api/workspace/members?workspace_id={slug}", hdr)
    if st != 200:
        return json.dumps({"error": "could not read the members", "status": st,
                           "detail": str(r)[:160],
                           "note": "a workspace they are not in will refuse — that is correct"})
    rows = (r or {}).get("members") or []
    return json.dumps({
        "workspace": slug, "count": len(rows),
        "members": [{"who": m.get("email") or f"(id {m.get('subject')})",
                     "role": m.get("role")} for m in rows],
    })


@tool
@anon_guard
def workspace_invite(slug: str, role: str = "contributor", emails: str = "",
                     days: int = 7, token: str = "") -> str:
    """Mint an invite link to a shared workspace. THE ONLY WAY SOMEONE JOINS.

    There is no add-a-member verb, deliberately: a person joins by redeeming an invite they
    chose to accept. So this hands back a link for your person to send — you cannot put someone
    in a shared space on their behalf.

    role: contributor (writes) | viewer (reads). Never owner.
    emails: comma-separated, to restrict the link to those addresses; omit for anyone-with-link.
    days: how long it lives, default 7."""
    uid = me()
    if role not in ("contributor", "viewer"):
        return json.dumps({"refused": "role is contributor or viewer",
                           "why": "owner cannot be granted by invite"})
    allowed = [e.strip() for e in emails.split(",") if e.strip()]
    body = {"workspace_id": slug, "role": role,
            "expires_in_sec": max(1, int(days)) * 86400, "max_uses": 1 if allowed else 10,
            "mode": "restricted" if allowed else "open"}
    if allowed:
        body["allowed_emails"] = allowed
    st, r = _http("POST", f"{AGENT_API}/api/workspace/invites", {"X-User-Id": uid}, body)
    if st not in (200, 201):
        return json.dumps({"error": "could not mint an invite", "status": st,
                           "detail": str(r)[:200],
                           "note": "only an owner or contributor of that workspace can invite"})
    tok = (r or {}).get("token")
    base = config.CANONICAL.rsplit("/mcp", 1)[0]
    return json.dumps({
        "invite_link": f"{base}/join?i={tok}",
        "role": role, "expires_in_days": days,
        "restricted_to": allowed or None,
        "give_this_to_your_person": "Hand them the link to send. It works once per person and "
                                    "then it is spent — treat it like a key.",
        "never_show": "Do not paste the raw token anywhere else; the link is the whole thing.",
    })


@tool
@anon_guard
def workspace_remove(slug: str, member: str, token: str = "") -> str:
    """Take someone out of a shared workspace. Owner only. `member` is the email or subject id
    shown by workspace_members."""
    uid = me()
    st, r = _http("DELETE",
                  f"{AGENT_API}/api/workspace/members/{member}?workspace_id={slug}",
                  {"X-User-Id": uid})
    if st not in (200, 204):
        return json.dumps({"error": "could not remove them", "status": st,
                           "detail": str(r)[:160], "note": "only an owner can do this"})
    return json.dumps({"removed": member, "workspace": slug,
                       "tell_your_person": "Done — they can no longer read or write there."})


@tool
@anon_guard
def workspaces(token: str = "") -> str:
    """Every workspace this person can reach — their own, plus the shared ones."""
    uid = me()
    st, r = _http("GET", f"{AGENT_API}/api/workspace/shared", {"X-User-Id": uid})
    # the endpoint answers {"memberships": [{workspace_id, role, added_at}]} — not "workspaces"
    rows = (r or {}).get("memberships") or [] if st == 200 else []
    out = [{"slug": "", "name": "personal", "role": "owner"}]
    for w in rows:
        out.append({"slug": w.get("workspace_id"), "role": w.get("role"),
                    "since": w.get("added_at")})
    return json.dumps({"workspaces": out, "count": len(out),
                       "note": "slug='' is their own; the rest are shared with a team"})


@tool
@anon_guard
def workspace_init(token: str = "") -> str:
    """Seed a fresh personal workspace for a user (idempotent)."""
    uid = me()
    st, body = _http("POST", f"{AGENT_API}/api/workspace/init", {"X-User-Id": uid}, {})
    return capped({"status": st, "result": body}, 2000)


@tool
@anon_guard
def workspace_regime(mode: str = "", local_path: str = "", token: str = "") -> str:
    """Where the PERSONAL workspace lives. mode='local' + local_path=<absolute dir on the
    person's machine> makes their own disk the home of personal knowledge — from then on you
    manage those files with your NATIVE file tools (read, edit, grep), which is faster and
    fully offline. mode='cloud' returns to server-side files via workspace_* tools.

    What stays cloud in EITHER mode: group workspaces (slug=... — shared, multi-writer,
    flows write into them), and the kernel flows need at processing time (validated company
    context, the scaffold flag, preferences). Flow outputs (meeting docs) always land cloud
    first — call workspace_pull() when connected to mirror them down. Call with no arguments
    to see the current regime."""
    uid = me()
    if not mode:
        return json.dumps({"regime": _regime(uid)})
    if mode not in ("local", "cloud"):
        return json.dumps({"error": "mode must be local | cloud"})
    if mode == "local" and not local_path.startswith("/"):
        return json.dumps({"error": "local mode needs an ABSOLUTE local_path on the "
                                    "person's machine (their agent creates it)"})
    rec = {"mode": mode, **({"local_path": local_path} if mode == "local" else {}),
           "set_at": time.time()}
    _regime_set(uid, rec)
    if mode == "cloud":
        return json.dumps({"regime": rec,
                           "carry_on": "personal knowledge is server-side again — use "
                                       "workspace_read/write as before"})
    return json.dumps({
        "regime": rec,
        "for_you_the_agent": [
            f"Create {local_path} if needed and manage personal knowledge there with your "
            f"native file tools — no workspace_* calls for personal files from now on.",
            "Group workspaces (slug=...) STAY on workspace_* — they are shared and the "
            "server writes into them.",
            "Company claims still go through propose()/validate() — flows read them at "
            "processing time, so they cannot live only on a laptop.",
            "Call workspace_pull() at the start of sessions to mirror new flow outputs "
            "(meeting docs) down into the local directory.",
        ],
    })


@tool
@anon_guard
def entity_upsert(kind: str, name: str, facts: list[str] = [], source: str = "", slug: str = "",
                  dates: dict | None = None, summary: str = "", fields: dict | None = None,
                  section: str = "", connections: list | None = None,
                  open_questions: list[str] | None = None, token: str = "") -> str:
    """Record what you just learned about a person, company, meeting, project or decision.

    ONE call does the whole thing: it creates `kg/entities/<kind>/<slug>.md` if the page does not
    exist and updates it in place if it does. You never have to check first, never have to invent
    the shape, never have to merge by hand. Call it on a maybe — repeating a fact the page already
    carries writes nothing.

    THE PAGE IS A CARD, not a log: a one-line summary, then the sections below for its kind, then
    `## Connected` (links both ways), `## Sources`, `## Open questions`, and `## Timeline` last for
    anything dated. File each fact into its section with `fields` — that is what makes a page worth
    opening. A fact passed in `facts` with no `section` lands in the Timeline, which is fine for a
    log line and wrong for what someone does.

    SECTIONS AND FIELDS, by kind:
      - person: Role and organisation · What they care about · How we relate  (fields: cares_about, company, relationship, role)
      - company: What it is · People · Our relationship  (fields: people, relationship, what)
      - meeting: When and who · Decided · Committed  (fields: committed, decided, participants, when, who)
      - project: What it is · Who · Status  (fields: status, what, who)
      - decision: What was decided · Why · What it changes  (fields: changes, what, why)

    - `fields` — `{"role": "Chairs the TSC", "company": "[[Sony Pictures Imageworks]]"}`. Each key
      above files into its section. A field that names another entity also draws the link BOTH ways:
      giving a person a `company` adds them to that company's page too.

    Use it the moment a turn learns anything durable: a name and who they are, a company and what
    they do, what a meeting decided, who owns what, a decision and why it went that way. A name
    without a page gets one NOW.

    - `kind` — person | company | meeting | project | decision
    - `name` — what the page is about, as a person would say it ("Cottalango Leon", "Sony Pictures
      Imageworks"). It becomes the title `[[wikilinks]]` resolve to.
    - `facts` — one short sentence each, only what was SAID or READ. Write other entities inside a
      fact as `[[Their Name]]`; the result tells you which of those have no page yet, and those are
      your next calls. Pass `section="<one of the section names above>"` to file them, or leave it
      and they go to the Timeline.
    - `summary` — the single line under the title, in plain words. Set once; it is not overwritten,
      so give it when you create the page.
    - `connections` — `["Acme"]` or `[{"name": "Acme", "relation": "works at"}]`. Chips on this
      page and the reciprocal chip on theirs, when their page exists.
    - `open_questions` — what you would need to know, written AS the question. This is where a gap
      goes; it never goes on the page as a guess.
    - `source` — where it came from, in a few words: the meeting, the mail, the file, the person's
      own message. REQUIRED. A fact with no source is refused, not written — if you do not have one,
      the gap belongs in `kg/MISSING.md`, never on the page.
    - `slug` — a shared workspace, omitted means this person's own desk.
    - `dates` — WHEN, for a meeting: `{"scheduled_at": ..., "held_at": ..., "report_delivered_at":
      ...}`, ISO-8601 or epoch, any subset. Record `held_at` the moment you know a meeting ran and
      `report_delivered_at` the moment its write-up reached them. These are the fields the desk
      README's `Now` section and `timeline` both read, so a meeting that ran and has no write-up
      shows up as an open commitment without anyone writing a sentence about it. Any other key is
      dropped. A call with only `dates` is legal and needs no facts.
    """
    uid = me()
    if isinstance(facts, str):
        facts = [facts]
    st, body = _http("POST", f"{AGENT_API}/api/workspace/entity", {"X-User-Id": uid},
                     {"kind": kind, "name": name, "facts": list(facts or []),
                      "source": source, "slug": slug or "", "dates": dates or {},
                      "summary": summary, "fields": fields or {}, "section": section,
                      "connections": connections or [],
                      "open_questions": open_questions or []})
    if st == 422:
        detail = (body or {}).get("detail") if isinstance(body, dict) else str(body)
        return json.dumps({"refused": detail,
                           "do": "fix the fact, do not retry the same call — the refusal is the rule"})
    if st not in (200, 201):
        return json.dumps({"error": "the entity could not be written", "status": st,
                           "detail": str(body)[:300],
                           "do": "say so plainly in one sentence, and report_friction()"})
    out = dict(body) if isinstance(body, dict) else {"result": body}
    path = out.get("path") or ""
    if path:
        out["paste_this_link"] = f"[[{name}]]"
        out["never_show_the_path"] = ("the path is an argument for tools; in your reply write "
                                      "[[" + str(name) + "]] and nothing slashed")
    if out.get("filed"):
        out["next"] = out.get("next") or ""
    if out.get("links_missing"):
        out["next"] = ("these names have no page yet and will render as inert 'not found' chips — "
                       "upsert each one now, with its own source: "
                       + ", ".join(out["links_missing"]))
    return json.dumps(out)[:6000]


@tool
@anon_guard
def settings(key: str = "", value: str = "", token: str = "") -> str:
    """How Vexa behaves for THIS person. Call with nothing to see everything; with key and
    value to change one thing.

    These are per-person and take effect on the next meeting — changing one never touches
    anyone else. When your person asks for something that is one of these, set it rather than
    explaining that it cannot be done, and never edit a flow to achieve it.

    on/off settings accept on/off, true/false, yes/no."""
    uid = me()
    if not key:
        st, body = _http("GET", f"{AGENT_API}/api/settings", {"X-User-Id": uid})
        if not (200 <= st < 300) or not isinstance(body, dict):
            return json.dumps({"error": "could not read the settings", "status": st})
        return capped(body, 6000)
    st, body = _http("POST", f"{AGENT_API}/api/settings", {"X-User-Id": uid},
                     {"key": key, "value": value})
    if st == 422 and isinstance(body, dict):
        # The CLOSED VOCABULARY lives with its reader now, not in a Python dict in a tool: a setting
        # that silently does nothing is worse than an error, and an agent with no vocabulary invents
        # one. The refusal carries the list, so the tool never has to hold a copy of it.
        return json.dumps(body.get("detail") if isinstance(body.get("detail"), dict) else body)
    if not (200 <= st < 300) or not isinstance(body, dict):
        return json.dumps({"error": "could not change that", "status": st, "detail": str(body)[:200]})
    return capped(body, 6000)


@tool
@anon_guard
def propose(claim: str = "", source: str = "", scope: str = "tenant",
            claims: list = None, token: str = "") -> str:
    """Record what you believe about this person's company as PROPOSED, not as fact.

    Batch with `claims`: a list of {claim, source, scope?} — ONE call for everything you
    learned. The single-claim form (claim=, source=) still works. Anything you research or
    infer starts here; a proposed claim is never used as company context until a human
    answers — an agent cannot promote its own guess."""
    uid = me()
    batch = []
    for b in (claims or []):
        if isinstance(b, str):
            b = {"claim": b}
        if isinstance(b, dict) and b.get("claim"):
            batch.append(b)
    if claim:
        batch.append({"claim": claim, "source": source, "scope": scope})
    if not batch:
        return json.dumps({"error": "give claim= or claims=[{claim, source}] "
                                    "(plain strings work too)"})
    st, body = _http("POST", f"{AGENT_API}/api/claims", {"X-User-Id": uid}, {"claims": batch})
    if not (200 <= st < 300) or not isinstance(body, dict):
        return json.dumps({"error": "could not record those", "status": st,
                           "detail": str(body)[:300], "do": "report_friction() with this"})
    return capped(body, 8000)


@tool
@anon_guard
def validate(claim_id: str = "", verdict: str = "", note: str = "",
             verdicts: list = None, token: str = "") -> str:
    """Record a HUMAN's word on proposed claims. verdict: confirmed | corrected | rejected.

    Batch with `verdicts`: a list of {id, verdict, note?} — when the person answers everything
    in one sentence ("all correct except we're in Toronto"), ONE call records all of it. The
    single form (claim_id=, verdict=) still works. Only call after actually asking the person;
    `corrected` keeps the original alongside the correction."""
    uid = me()
    batch = list(verdicts or [])
    if claim_id:
        batch.append({"id": claim_id, "verdict": verdict, "note": note})
    if not batch:
        return json.dumps({"error": "give claim_id=+verdict= or verdicts=[{id, verdict, note}]"})
    st, body = _http("POST", f"{AGENT_API}/api/claims/verdicts", {"X-User-Id": uid},
                     {"verdicts": batch})
    if not (200 <= st < 300) or not isinstance(body, dict):
        return json.dumps({"error": "could not record that", "status": st,
                           "detail": str(body)[:300], "do": "report_friction() with this"})
    return capped(body, 8000)


@tool
@anon_guard
def company_context(token: str = "") -> str:
    """The validated company context — only claims a human has confirmed or corrected.

    This is what every agent in the tenant may rely on. Proposed claims are deliberately absent:
    if it is not here, nobody has stood behind it yet.\n\n    If you have not called whats_waiting() yet this session, call it first."""
    uid = me()
    st, body = _http("GET", f"{AGENT_API}/api/claims", {"X-User-Id": uid})
    if not (200 <= st < 300) or not isinstance(body, dict):
        return json.dumps({"error": "could not read the company context", "status": st,
                           "do": "say the READ failed — never that there is none"})
    return capped(body, 9000)


@tool
@anon_guard
def mark_scaffolded(group: str = "", token: str = "") -> str:
    """Declare the workspace ready, which releases anything queued behind it.

    Only do this once company_context() actually returns validated claims — marking it ready
    with nothing in it means every artifact afterwards is written against an empty context and
    nobody finds out until they read one."""
    uid = me()
    st, body = _http("POST", f"{AGENT_API}/api/claims/scaffold", {"X-User-Id": uid},
                     {"group": group or ""})
    if st == 409 and isinstance(body, dict):
        return json.dumps({"refused": "no validated claims yet",
                           "still_proposed": body.get("still_proposed", 0),
                           "do": "Ask the person about the proposed claims first."})
    if not (200 <= st < 300) or not isinstance(body, dict):
        return json.dumps({"error": "could not mark the workspace ready", "status": st,
                           "detail": str(body)[:300], "do": "report_friction() with this"})
    return json.dumps(body)


@tool
@anon_guard
def mark_global_ready(token: str = "") -> str:
    """ACCEPT the company layer you just wrote into `_global`, and start the service.

    Call this at the END of the company-setup conversation, once the administrator agrees the five
    files are right: README.md (the company name as its first heading, then ONE sentence of what it
    does), PRINCIPLES.md, OBJECTIVES.md, STRUCTURE.md, MISSING.md.

    It RE-READS the files itself before it accepts anything, commits them to the `_global` git
    history with the administrator as the author, and lifts the instance gate — so other people can
    sign in and the flows engine starts sending. It is a CHECK, not a claim: if the layer is
    incomplete it refuses and tells you exactly what is missing, so calling it is always safe, and
    telling the administrator it is done before this verb has accepted it is always wrong.

    Admin only. Everyone else gets a refusal naming that."""
    uid = me()
    em = caller_email() or ""
    st, body = _http("POST", f"{AGENT_API}/api/global/ready", {"X-User-Id": uid},
                     {"author_email": em, "author_name": em.split("@")[0] if em else ""})
    if st == 409 and isinstance(body, dict):
        return json.dumps({"accepted": False, "still_missing": body.get("missing_files", []),
                           "reasons": body.get("reasons", []),
                           "next": "write those, then call mark_global_ready again"})
    if st != 200:
        return json.dumps({"accepted": False, "status": st, "error": str(body)[:500]})
    return json.dumps({**body,
                       "say_this": "The instance is set up. Other people can sign in now and the "
                                   "flows start sending."})
