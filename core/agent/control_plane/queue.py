"""THE QUEUE — everything Vexa needs from one person, assembled where the stores are.

`whats_waiting` was 253 lines inside `deploy/dogfood/rig/vexa_control_mcp.py`, and 40% of every
measured tool call on that server. It fanned out across four services, classified a failed reaction
as ours-or-theirs with a keyword list, composed the entire cold-start welcome script, composed two
menus of next options, and decided whether to offer the self-sustain loop — all as Python string
literals inside a tool body (seam inventory B2).

Two things were wrong with that and only one of them was the size.

  * A TOOL CANNOT COMPOSE ACROSS SERVICES. It has no store of its own, so every branch was a
    round-trip and every failure was a different shape. The assembly belongs where the workspace,
    the claim book and the scaffold flag already are, and that is here.
  * EVERY SENTENCE A NEW PERSON HEARS WAS BAKED INTO AN IMAGE. An admin could not change one word
    without a deploy, which is exactly what PRD §3.8 says must not be true. So the copy is read from
    `_global/queue/<key>.md` when the organisation has written one, and falls back to the default
    below. Same mechanism as the ask presets, same reason: the words are product behaviour, not code.

WHAT IT NEVER DOES is decide anything about the person's account. Identity is resolved before this
module is called; a subject that does not exist never reaches it.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

# ── the copy, as defaults ────────────────────────────────────────────────────────────────────
# Anything here is a promise made in the first thirty seconds of the relationship, so a beat for a
# broken path is an invented capability with our name on it — however true we mean it to become.
WELCOME_BEATS = [
    "Paste any meeting link and I'll put a notetaker in that call — while it runs, ask me "
    "what's being said.",
    "Afterwards the words stay here: searchable, and written up into notes your team can "
    "read.",
    "Tell me in a sentence what should happen after a meeting — \"after the standup, email "
    "me the open questions\" — and it does.",
]

# The ours-or-theirs test. A person can act on "waiting for your answer"; they cannot act on our
# mail credentials, and telling them about it hands them our plumbing as a chore. This was a Python
# tuple inside a tool; it is a declared list, and `_global/queue/ours-not-theirs.md` (one token per
# line) replaces it without a deploy.
OURS_MARKERS = ("smtp", "auth", "credential", "password", "unauthorized", "forbidden",
                "connection", "timeout", "traceback", "500", "502", "503", "535",
                "refused", "dns", "certificate")

MENU_NO_MEETINGS = [
    "Send the bot into a meeting — paste any Meet / Teams / Zoom link",
    "Bring a past meeting in — YouTube captions, a Zoom export, any transcript",
    "See the workspace in the terminal (deeplink target='meetings')",
]
MENU_WITH_MEETINGS = [
    "Open the latest meeting beside its notes (deeplink target='post_meeting')",
    "Send the bot into the next call — paste the link",
    "Ask anything across every meeting so far (transcript_search)",
    "Reshape what happens after meetings — flows are editable in plain sentences",
]
MENU_DEFAULT = [
    "Send the bot into a meeting — paste any Meet / Teams / Zoom link",
    "Is there a call today it should sit in on?",
    "Bring a past meeting in — a Zoom export, YouTube captions, any transcript",
]
MENU_LIVE = [
    "Read along live as it is being said",
    "A recap of the meeting so far",
    "Open the live view beside the notes",
]


def _global_copy(global_dir: "Path | None", key: str) -> "str | None":
    """One copy block an admin has written, or None. Read HOT — no cache, no restart.

    `_global` is a git repo on this service's own volume and the admin edits it through the same
    surface they edit the ask presets with. A missing file is the normal case and means "use the
    default", never an error."""
    if not global_dir:
        return None
    try:
        f = Path(global_dir) / "queue" / f"{key}.md"
        if f.is_file():
            text = f.read_text(encoding="utf-8").strip()
            return text or None
    except Exception:  # noqa: BLE001 — copy that cannot be read falls back; it never fails a queue
        return None
    return None


def _markers(global_dir) -> tuple:
    raw = _global_copy(global_dir, "ours-not-theirs")
    if not raw:
        return OURS_MARKERS
    toks = tuple(t.strip().lower() for t in raw.splitlines() if t.strip() and not t.startswith("#"))
    return toks or OURS_MARKERS


def anonymous_welcome(global_dir=None) -> dict:
    """What an unauthenticated caller gets. THE PRODUCT'S FIRST SENTENCE, so it is copy and it is
    overridable."""
    return {
        "authenticated": False,
        "waiting": 1,
        "items": [{
            "kind": "welcome",
            "open_with_what_they_get": _global_copy(global_dir, "welcome-anonymous") or (
                "Say this first, in one or two sentences of your own voice, before you ask them "
                "for anything: Vexa puts a notetaker in their meetings and they run it from this "
                "conversation — paste a meeting link and it joins the call; afterwards the words "
                "are here to search, summarise, and write up however they ask. Nobody finishes a "
                "setup whose point they were never told, and this one is thirty seconds."),
            "then_ask_exactly_one_thing": (
                "Which email should Vexa be set up under? That question and nothing else. On "
                "their answer call start_onboarding(email): a 6-digit code lands in that inbox, "
                "they paste it back here, you call confirm_login(email, code). One question, one "
                "code — no browser, no install, no restart."),
            "speak_product_not_plumbing": (
                "They are setting up a notetaker, not authenticating a client. Never say "
                "credential, token, config, account, session, or 'gated behind sign-in', and "
                "never name a tool. If you would not read a sentence aloud to someone who has "
                "never heard of MCP, do not write it."),
            "if_they_already_have_a_token": (
                "If their message carries a vxa_mcp_… token, or they say they have signed in "
                "before, use token=<value> and skip all of the above silently — never make a "
                "second account for someone who already has one, and never ask them to hunt for "
                "a token."),
        }],
        "next": ("start_onboarding(email) once they answer, then confirm_login(email, code), then "
                 "whats_waiting(token=…) and keep going. Everything happens in this conversation; "
                 "nothing needs restarting."),
    }


def build(*, subject: str, workspace: "Path | None", global_dir=None,
          meetings=(), bots=(), reactions=(), friction_seen: int = 0,
          scaffolded: bool | None = None) -> dict:
    """The queue for one person. Pure over what it is handed — every caller is injected, which is
    what makes this testable without a stack."""
    ws = Path(workspace) if workspace else None
    if scaffolded is None:
        scaffolded = bool(ws and (ws / ".scaffolded").is_file())
    items: list = []

    # THE FIRST TIME. Tools have just loaded, which means this is the first moment we have a trusted
    # channel to this person at all — the web page could not carry a welcome and the registration
    # command certainly could not. If we open on the scaffold instead, their first sentence from us
    # is a chore handed over by a stranger.
    if not scaffolded and not meetings:
        return {
            "uid": subject,
            "waiting": 1,
            "items": [{
                "kind": "welcome",
                "this_is_the_entire_turn": (
                    "Nothing is waiting and nothing is owed. Say the three lines, ask the one "
                    "question, stop. No setup step, no research, no list of what you can do, and "
                    "no mention of connecting or registering — from their side that finished a "
                    "moment ago."),
                "three_short_lines_in_your_own_words_under_70_words":
                    _global_copy(global_dir, "welcome-beats") or " ".join(WELCOME_BEATS),
                "then_ask": (
                    "Is there a call today I should sit in on? Or paste any meeting link and the "
                    "bot is in it in seconds."),
            }],
            "next_options": MENU_NO_MEETINGS,
            "close_with_options": ("End on two or three of these, in their words, as things they "
                                   "can say back."),
        }

    if not scaffolded:
        items.append({
            "kind": "setup", "id": "scaffold",
            "what": _global_copy(global_dir, "setup-what") or (
                "Write-ups are drafted against what Vexa knows about this team, and it knows "
                "nothing yet — so they would read like minutes from a stranger."),
            "do": _global_copy(global_dir, "setup-do") or (
                "Two calls, nothing in between. Look them up in public — their site, GitHub, "
                "LinkedIn — for the four to six things that CHANGE HOW A WRITE-UP READS: what "
                "they build, who is on the team, their shorthand, what is in flight, which "
                "meetings recur. Not pricing, funding or marketing copy — none of that changes a "
                "standup write-up. Put them in ONE propose(claims=[...]) call; it hands back the "
                "exact lines to show. Show those, then put their whole answer, however brief, "
                "into ONE validate(verdicts=[...]) call. That finishes it — there is no third "
                "step."),
            "never_block_on_this": (
                "If your person asks for something direct — send a bot, read a transcript, "
                "anything — DO THAT FIRST and return here after. Setup questions never hold up a "
                "live request."),
        })

    for c in claims_of(ws):
        if c.get("state") == "proposed":
            items.append({
                "kind": "question", "id": c["id"],
                "what": c["claim"],
                "why": c.get("source", "extracted from a meeting"),
                "do": ("Ask the open questions as a few SHORT LINES, one per line, after one "
                       "sentence of context — scannable and correctable at a glance. Never a "
                       "paragraph, which is a wall nobody corrects, and never answers a numbered "
                       "form. Record the whole answer in ONE validate(verdicts=[{id, verdict, "
                       "note}, ...]) call."),
            })

    # Ask for a report when there is something to report ON — after real work, not on an empty
    # account where the agent has nothing to say yet.
    if not scaffolded and friction_seen == 0:
        items.append({
            "kind": "tell_us", "id": "friction",
            "what": "You are one of the first agents to use this surface.",
            "do": ("As you work, call report_friction() whenever something is confusing, missing, "
                   "or takes more calls than it should. Do not save it up or wait to be asked — a "
                   "rough edge you route around silently is one nobody fixes."),
        })

    markers = _markers(global_dir)
    for r in reactions:
        if r.get("status") == "blocked":
            items.append({"kind": "blocked", "id": r["id"],
                          "what": f"{r['flow']} is waiting at {r['step']}",
                          "do": "reaction_signal(id, 'resume') once the person has answered."})
        elif r.get("status") == "failed" and r.get("reason"):
            reason = str(r.get("reason") or "")
            if any(k in reason.lower() for k in markers):
                items.append({
                    "kind": "ours_not_theirs", "id": r["id"],
                    "what": f"{r['flow']}/{r['step']} is failing on OUR side: {reason[:160]}",
                    "do": ("This is OUR infrastructure failing, not your person's task — so call "
                           "report_friction() with the detail and do not put it on their list. "
                           "Never ask them to fix our credentials or our services. Say nothing "
                           "about it unless it blocks something they wanted, and then one plain "
                           "sentence: that part is not working, we have been told. You are never "
                           "asked to hide anything from them — only not to hand them our plumbing "
                           "as a chore."),
                })
            else:
                items.append({"kind": "stuck", "id": r["id"],
                              "what": f"{r['flow']}/{r['step']}: {reason[:160]}",
                              "do": ("This one is theirs to unblock. Put it to them in one plain "
                                     "sentence, then reaction_signal(id, 'retry').")})

    # RIGHT NOW comes first: a live bot means the person is in a meeting THIS MOMENT, and everything
    # else waits behind that fact.
    for b in bots:
        items.insert(0, {
            "kind": "live_now",
            "what": (f"A meeting is happening RIGHT NOW — the bot is in "
                     f"{b.get('platform')}/{b.get('native_meeting_id')} ({b.get('status')})."),
            "meeting": b.get("native_meeting_id"),
            "platform": b.get("platform"),
            "do": ("Lead with this. Offer, in their words: read along live (meeting_transcript, "
                   "keep polling), a recap of the meeting so far, have the bot say something into "
                   "the room (bot_say), open the live view side-by-side (deeplink "
                   "target='during_meeting'), or pull the bot out (bot_stop)."),
        })

    if not items:
        return {"uid": subject, "waiting": 0, "items": [],
                "next_options": MENU_NO_MEETINGS if not meetings else MENU_WITH_MEETINGS,
                "note": "nothing is waiting — offer the options, in their words"}

    kinds = {i.get("kind") for i in items}
    out = {"uid": subject, "waiting": len(items), "items": items,
           "next": "Work these, then call whats_waiting again."}
    # A queue with work in it still has to leave the person a move. Before, next_options only
    # existed when nothing was waiting — so on the first turn after sign-in, the turn that decides
    # whether they stay, the agent had nothing concrete to offer.
    out["next_options"] = MENU_LIVE if "live_now" in kinds else MENU_DEFAULT
    out["close_with_options"] = ("End your reply with two or three of these, in their words, as "
                                 "things they can say back. Never end without a move.")
    # A loop only earns its token cost when something will keep arriving: a live meeting now, or a
    # scaffolded account whose queue fills as meetings complete. Not for a one-off setup gate.
    if ("live_now" in kinds) or (scaffolded and len(items) >= 2):
        out["offer_self_sustain"] = _global_copy(global_dir, "self-sustain") or (
            "There is standing work here. OFFER your person (do not start it yourself): \"want me "
            "to keep Vexa working on its own? I can run on a 15-minute loop, check what is "
            "waiting, and handle it — it uses your tokens and you can stop it anytime.\" On a yes, "
            "in Claude Code: /loop 15m with a prompt that calls whats_waiting and works what it "
            "returns.")
    return out


# ── the claim book, which the queue reads and `claims.py` writes ─────────────────────────────
CLAIMS_PATH = "_pending/claims.json"


def claims_of(workspace: "Path | None") -> list:
    """Every proposed/validated claim for this person, or []. Never raises: a queue must not fail
    because a JSON file somebody hand-edited will not parse."""
    if not workspace:
        return []
    try:
        book = json.loads((Path(workspace) / CLAIMS_PATH).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    rows = book.get("claims") if isinstance(book, dict) else None
    return rows if isinstance(rows, list) else []


def mark_scaffolded(workspace: Path, validated: int, group: str = "") -> str:
    """Write the readiness flag, which releases anything queued behind it."""
    name = f".scaffolded-group-{group}" if group else ".scaffolded"
    f = Path(workspace) / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"ready": True, "at": time.time(), "validated_claims": validated},
                            indent=1), encoding="utf-8")
    return name
