"""workspace_reader.py — read a subject's workspace (the git knowledge graph) for the Workspace surface.

Read-only view over the per-subject workspace dir the chat runner maintains. Hides `.git`/`.claude`
internals and guards against path traversal (a read path can never escape the subject's workspace).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from datetime import datetime
from typing import Iterator, Optional

from shared import tool_outcome
from workspaces.shared import workspace_paths as wpaths


def _tool_op(name: str, args: Optional[dict] = None) -> dict:
    """Classify a claude tool name into one of the terminal's op labels (read/search/edit/git/web/tool).
    Mirrors the frontend ``toolOp`` so the loaded history reads the same as a live turn — including the
    touched ``file`` (+``wrote``) that powers the transcript's actionable file chips."""
    t = (name or "").lower()
    if t == "skill":
        # The harness's Skill tool: a step that LOADS instructions, named by the skill it loaded.
        skill = (args or {}).get("skill")
        return {"label": "skill", **({"skill": skill} if isinstance(skill, str) and skill else {})}
    if any(k in t for k in ("read", "cat", "open")) and "edit" not in t:
        label = "read"
    elif any(k in t for k in ("search", "grep", "find", "glob")):
        label = "search"
    elif any(k in t for k in ("edit", "write", "append")):
        label = "edit"
    elif any(k in t for k in ("git", "commit")):
        label = "git"
    elif any(k in t for k in ("web", "fetch", "http")):
        label = "web"
    else:
        label = "tool"
    op = {"label": label}
    fp = (args or {}).get("file_path")
    if isinstance(fp, str) and fp:
        op["file"] = fp
        op["wrote"] = label == "edit"
    if label == "edit":
        written = _written_at_the_time(name, args or {})
        if written is not None:
            op["written"] = written
    return op


def _written_at_the_time(name: str, args: dict) -> Optional[dict]:
    """``{workspace, path}`` a write named WHEN IT RAN, or None — ``workspace`` None when the call left
    it to the chat's target (not recorded, so not known afterwards).

    The record behind an old message's file references (`qualified_history`): a harness write names
    its mount (`/workspaces/<slug>/<rel>`), the page verb names its `slug` (`personal` = the desk)."""
    fp = args.get("file_path") or args.get("notebook_path")
    if isinstance(fp, str) and fp.startswith("/workspaces/"):
        rest = fp[len("/workspaces/"):]
        if rest.startswith(".attached/"):
            parts = rest.split("/", 3)
            return {"workspace": parts[2], "path": parts[3]} if len(parts) == 4 and parts[3] else None
        slug, _, rel = rest.partition("/")
        return {"workspace": slug, "path": rel} if slug and rel and not slug.startswith(".") else None
    if isinstance(fp, str) and fp:
        # outside every mount (`/tmp/…`) is no workspace's file; a relative one is unknowable
        return None if fp.startswith("/") else {"workspace": None, "path": fp}
    if str(name or "").startswith("mcp__") and isinstance(args.get("path"), str) and args.get("path"):
        slug = str(args.get("slug") or "").strip()
        return {"workspace": slug or None, "path": str(args["path"]).lstrip("/")}
    return None


#: The desk's names a page verb takes (`ceiling.DESK_ALIASES`); answered as the person's own id.
_DESK_ALIASES = ("personal", "desk")


def qualified_history(turns: list[dict], subject: str) -> list[dict]:
    """An old conversation's bare file references, qualified against the ONE workspace its session
    wrote into — the message's own context, recorded at the time (founder ruling 2026-10-18: *"we do
    not want to guess which readme this should open"*).

    Each write step carries the workspace it named when it ran (`_written_at_the_time`). When every
    write in the session names one and the same workspace, a bare page path in an agent's reply
    (`` `README.md` ``, ``[plan](kg/plan.md)``) becomes ``[[page:<that workspace>/<path>]]``, and each
    write step gets its own ``ref``. A session that wrote nowhere, into two workspaces, or through a
    write that left the workspace to the chat's target is left as it was: its bare paths stay plain
    text, never a guess. Pure; returns new turn dicts."""
    from workspaces.shared import links as links_mod  # noqa: PLC0415

    def _ws(w: Optional[str]) -> Optional[str]:
        return str(subject) if w in _DESK_ALIASES else w

    seen: set = set()
    unknown = False
    for t in turns:
        for op in (t.get("ops") or []) if t.get("role") == "agent" else []:
            w = op.get("written")
            if isinstance(w, dict):
                ws = _ws(w.get("workspace"))
                if ws:
                    seen.add(ws)
                else:
                    unknown = True
    only = next(iter(seen)) if len(seen) == 1 and not unknown and subject else None
    out = []
    for t in turns:
        if t.get("role") != "agent" or (not only and not any(
                isinstance(o.get("written"), dict) and o["written"].get("workspace") for o in t.get("ops") or [])):
            out.append(t)
            continue
        ops = []
        for op in t.get("ops") or []:
            w = op.get("written")
            ws = _ws(w.get("workspace")) if isinstance(w, dict) else None
            ops.append({**op, "ref": links_mod.page_ref(ws, w["path"])} if ws and w.get("path") else op)
        text = links_mod.qualify_bare_paths(t.get("text") or "", only) if only else t.get("text")
        out.append({**t, "ops": ops, "text": text})
    return out


def _block_text(content) -> str:
    """Concatenate the ``text`` of an assistant message's content (string, or list of blocks)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            b.get("text", "") for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""

# `.git` is pure plumbing — huge/noisy, never useful in the Files tree — and `.claude` is the agent's
# own (chat continuity, harness state): both are hidden unconditionally, as every file route refuses
# a path into them (`workspace_paths.RESERVED_DIRS`). Everything else dot-prefixed is hidden by
# default but surfaced when the caller opts in via ``hidden=True``.
_ALWAYS_HIDDEN = {".git", ".claude"}

# TEMPLATES ARE NOT RECORDS. `kg/templates/` holds the SHAPE of an entity — a skeleton with
# `<Full Name>` where a name goes — and every prose file in the workspace says it is never
# knowledge. Nothing enforced that: the shapes carry conformant `type/id/title` frontmatter, and
# `tree_at` is the single enumerator behind the Files tree, the MCP `workspace_tree`, the client's
# link resolver and its find-file index, so an agent asked "what meetings do I have" could read one
# and answer with it. The founder's rule is that the SYSTEM must know a template is a template.
#
# Two tests, because both are needed: the PATH covers the shipped files before anyone edits their
# frontmatter, and the `template: true` FLAG covers a shape copied anywhere else. `hidden=True`
# still shows them — a human browsing deliberately is not the failure mode.
_RESERVED_PREFIXES = ("kg/templates/",)
_TEMPLATE_FM = re.compile(r"^(?:template|example):\s*true\b", re.M)

# Hiding a shape from every ENUMERATOR is only half of it — `read` is a second door, and it was
# open: `GET /api/workspace/file` and the MCP `workspace_read` take a path the agent supplies, so a
# shape it saw quoted anywhere (a prose file, an earlier reply, a guess) still came back as plain
# markdown with conformant `type/id/title` frontmatter and read exactly like a record.
#
# The answer is NOT a refusal: creating an entity legitimately means looking at its shape first.
# It is that the bytes must announce what they are, before the frontmatter, in the same read.
_TEMPLATE_BANNER = (
    "TEMPLATE — THIS IS THE SHAPE OF AN ENTITY, NOT A RECORD.\n"
    "Nothing below is a real person, company or meeting: the angle-bracket fields are blanks.\n"
    "Never cite it, never name it, never list or count it as prior context, and never copy a\n"
    "placeholder value into an answer. Read it only to learn the shape you are about to fill.\n"
    "\n"
)


# ── WHAT THE PERSON SAID, AND WHAT THE MACHINE SAID (F47/F51) ────────────────────────────────────
#
# A transcript line is the prompt the harness was GIVEN, not the sentence somebody typed: the worker
# prepends voice/kg-links/mounts/entity-index/global-context preambles and the control plane folds
# its grounding in front of that. Until now the terminal reconstructed the human half by STRIPPING
# all of it — a sentinel cut when one was present, else regexes matched against the preambles'
# wording — and on 2026-09-02 a changed preamble set made every stored turn in the founder's chat
# render as a grey USER bubble full of machinery with his own sentence at the bottom.
#
# So the worker now writes the human half down as its own field beside the continuity pointer
# (``worker/engine.py`` ``record_user_text`` → ``.claude/sessions/<session>.turns.jsonl``, one JSON
# object per turn: the sha256 of the exact composed prompt, and the person's words). This reader
# looks the stored prompt up by that digest and serves ``user_text`` alongside ``text``. It reads no
# English and knows nothing about preambles; a turn with no record simply carries no ``user_text``,
# and the terminal's strip stays as the fallback for everything written before the field existed.
_TURNS_SIDECAR = "{session}.turns.jsonl"

# ── continuity files are read without following a link ──────────────────────────────────────────
# A chat's pointer, its user_text sidecar and its transcript live under ``<ws>/.claude/`` — in
# ``_system`` above all, which the model's tools may write during a turn. agent-api reads them as a
# process that can see every subject's store, so a link planted there would make it read another
# subject's chat into this one's history. Below the workspace root nothing is reached through a
# link: each folder is opened without following one, a file only when it is a regular file with no
# other hard link.
#: A session pointer is an id a few dozen bytes long.
_POINTER_MAX_BYTES = 1 << 16
#: The continuity files sit under `.claude/`, which no caller-supplied path may reach
#: (`workspace_paths.RESERVED_DIRS`); these are the platform's own reads of it.
_PLUMBING = (".claude",)
_PLAIN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}")


def _read_under(ws: Path, parts: "tuple[str, ...]", name: str,
                max_bytes: Optional[int] = None) -> Optional[str]:
    """The UTF-8 text of ``ws/<parts…>/name`` read without following a link (``workspace_paths``), or
    None (missing, a link anywhere below ``ws``, not a regular file with a single link, over
    ``max_bytes``)."""
    return wpaths.read_text_inside(ws, "/".join((*parts, name)), max_bytes=max_bytes, allow=_PLUMBING)


def _real_subdirs(ws: Path, parts: "tuple[str, ...]") -> Iterator[str]:
    """Names of the folders directly in ``ws/<parts…>`` that are folders, not links to one."""
    yield from wpaths.list_dirs_inside(ws, "/".join(parts), allow=_PLUMBING)

# The write-back phase runs in the SAME harness session as the turn it follows, so its prompt and
# its reply are in this transcript. The phase declares itself with this mark — ONE literal now, in
# ``shared/marks.py``; the worker reads the same constant under its historical name WRITEBACK_MARK.
# Everything from a marked prompt until the next thing a person actually said is bookkeeping the
# founder was never meant to read back as his own conversation.
from shared import attachments as attachments_mod  # noqa: E402
from shared.marks import PHASE_MARK, QUIET_REPLY, act_label, is_signal_turn, quiet_reply, read_message_mark  # noqa: E402 — re-export under this module's long-standing name

# …and the salvage for a turn dispatched BEFORE any of those marks existed (Vexa-ai/vexa#1605):
# a flow's composed kick names its own kind in its first bracket, which is the only thing a
# record with no mark still carries.
from shared.chat_label import composed_label  # noqa: E402 — see shared/chat_label.py

# The harness's own auto-continue. It is a user line nobody typed — the runner nudging a turn that
# stopped early — and it rendered as a grey USER bubble reading "Continue from where you left off."
# Dropping it WITHOUT flushing the open agent turn also re-joins the answer it interrupted, which is
# what the reader was always meant to show: one reply, not two halves with machinery between them.
_HARNESS_CONTINUE = "Continue from where you left off."

# A SKILL'S BODY IS NOT SPEECH. When the agent calls the harness's Skill tool, Claude Code writes the
# skill's whole text into the transcript as a `user`-role line (flagged `isMeta`) that opens with this
# line. It is the harness injecting instructions, and it renders as a step of the agent's turn.
_SKILL_BASE_RE = re.compile(r"\ABase directory for this skill:\s*(\S+)")


def _skill_injection(text: str) -> Optional[str]:
    """The skill name when this user-role line is a loaded skill's body, else None."""
    m = _SKILL_BASE_RE.match(text.lstrip())
    if not m:
        return None
    return m.group(1).rstrip("/").rsplit("/", 1)[-1] or "skill"


def _user_text_index(roots: "list[Path]", session: str) -> list[dict]:
    """Every ``{key, user_text}`` record the worker wrote for this thread, oldest first.

    Searched across the same roots the pointer and transcript are, because a thread that MOVED
    anchors leaves them apart. Tolerant like everything else here: an unreadable or malformed
    sidecar yields no records, and history degrades to the terminal's fallback strip."""
    out: list[dict] = []
    for ws in roots:
        raw = _read_under(ws, (".claude", "sessions"), _TURNS_SIDECAR.format(session=session))
        if raw is None:
            continue
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(rec, dict) and isinstance(rec.get("user_text"), str) and rec.get("key"):
                out.append(rec)
    return out


# THE SEED'S BLANKS ARE NOT FACTS EITHER (F53). Workspaces created before the template-free seed
# still hold its README and dashboard skeletons, whose fields read `(unset)`. Those pages carry no
# frontmatter at all, so the `template: true` rule above cannot reach them — and an agent reported
# "the project's objective is still `(unset)`" to the founder as though it had learned something.
#
# It cannot be fixed by re-seeding: those workspaces exist, they are the users', and nobody is going
# to rewrite them. So the page announces itself IN THE SAME READ, exactly as a template does. Unlike
# a template it stays VISIBLE in the tree — it is a real page of theirs waiting to be filled in, not
# a shape that should never have been enumerable — and the banner says what to do with a blank.
_UNSET_MARKER = "(unset)"
_UNFILLED_BANNER = (
    "UNFILLED — THIS PAGE IS STILL THE SEED'S SKELETON WHERE IT SAYS `(unset)`.\n"
    "Each `(unset)` is a BLANK nobody has filled in yet, never a value. Never report one as an\n"
    "answer ('the objective is (unset)') and never copy one into a record: say the thing is not\n"
    "recorded yet, and offer to fill it in from what this turn knows.\n"
    "\n"
)


def _is_template_doc(base: Path, rel: str) -> bool:
    """Does this file DECLARE itself a shape? Frontmatter only, and only the head of it — read
    NOFOLLOW (`workspace_paths`), because the tree is one the model's tools can write."""
    if not rel.lower().endswith(".md"):
        return False
    head = wpaths.read_head_inside(base, rel, 800)
    if not head or not head.startswith("---"):
        return False
    end = head.find("\n---", 3)
    return bool(_TEMPLATE_FM.search(head if end == -1 else head[:end]))


# Commit authors that are platform/seed PLUMBING, not a member's agent — classified ``system`` so the
# activity feed never mistakes a policy or seed commit for a member push. The per-mount turn-commit stamps
# a member's principal instead (name=<subject>, email=<subject>@vexa.local — see worker/engine.py, D4).
_SYSTEM_AUTHOR_EMAILS = {"platform@vexa.ai", "agent@vexa"}
_SYSTEM_AUTHOR_NAMES = {"vexa-platform", "vexa-agent"}

# The one `git log` format both readers below use, and the one parser for it.
#
# %an·%ae carry the D4 attribution: a member's agent commit is authored as its principal
# (name=<subject>, email=<subject>@vexa.local); platform/seed commits are the plumbing authors.
# --name-only appends each commit's changed files (so the terminal can make them clickable);
# \x1e prefixes each commit record so we can split records and separate meta from the file list.
# %ct = committer unix timestamp — a sortable key so a cross-workspace activity feed can merge
# commits from several mounts by recency (the %cr relative string can't be sorted).
_LOG_FORMAT = "--pretty=format:%x1e%h\x1f%s\x1f%cr\x1f%an\x1f%ae\x1f%ct"


def _commit_records(raw: str, viewer_email: Optional[str]) -> list[dict]:
    """Parse ``_LOG_FORMAT`` output into the wire shape the terminal renders.

    Extracted from ``git_state_at`` when the page-scoped history route arrived (Vexa-ai/vexa#1623),
    because the alternative was a SECOND copy of the ``you``/``member``/``system`` classification —
    and an attribution rule that exists twice is one that will eventually disagree with itself about
    the same commit in two panels of the same screen."""
    out: list[dict] = []
    for rec in (raw or "").split("\x1e"):
        rec = rec.strip("\n")
        if not rec:
            continue
        lines = rec.split("\n")
        parts = lines[0].split("\x1f")
        if len(parts) != 6:
            continue
        sha, msg, when, an, ae, ct = parts
        if ae in _SYSTEM_AUTHOR_EMAILS or an in _SYSTEM_AUTHOR_NAMES:
            kind = "system"          # policy/seed plumbing — never a member's agent push
        elif viewer_email and ae == viewer_email:
            kind = "you"             # the caller's own agent write
        else:
            kind = "member"          # ANOTHER member's agent pushed this
        files = [
            f.strip() for f in lines[1:]
            if f.strip() and f.split("/", 1)[0].lstrip(".") not in ("git", "claude")
        ][:20]                       # cap: a root/seed commit can touch hundreds
        # %ae RIDES ALONG (Vexa-ai/vexa#1642). `author` is `%an`, which on a turn commit is the
        # person's SIGN-IN ADDRESS while `%ae` is `<subject>@vexa.local` — so the desk that holds
        # their name is findable only through the second, and the front page asked for the first as
        # if it were a subject and answered `someone`. Both halves, one record, one parser.
        out.append({"sha": sha, "msg": msg, "when": when, "author": an, "email": ae, "kind": kind,
                    "files": files, "ts": int(ct) if ct.isdigit() else 0})
    return out


def _line_at(obj: dict) -> Optional[float]:
    """When a transcript line was written (its ISO `timestamp`), epoch seconds; None if it has none."""
    ts = obj.get("timestamp")
    if not isinstance(ts, str):
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def parse_history(raw: str, records: list) -> list[dict]:
    """The terminal-renderable turns of one harness transcript (``raw``, JSONL) given the worker's
    ``{key, user_text}`` records for the thread. Shared by the workspace layout (``history``) and the
    session store (ADR-0048), so both render one conversation the same way."""
    turns: list[dict] = []
    cur_agent: Optional[dict] = None  # the open agent turn we accumulate text/ops onto
    # The person's own words for each turn, keyed by the digest of the composed prompt (F47).
    by_key = {r["key"]: r["user_text"] for r in records}
    unused = list(records)
    # Are we inside a write-back phase exchange (F51)? Set by a marked prompt, cleared by the
    # next thing a person actually said. While it is on, the agent's replies are bookkeeping.
    in_phase = False
    # Is the open agent turn answering a SIGNAL's wake (friction 6)? Such a turn may end silently:
    # its `QUIET_REPLY` is never shown. Set by the wake, cleared by the next real prompt.
    in_signal = False

    def flush_agent() -> None:
        nonlocal cur_agent
        if cur_agent is None:
            return
        open_turn, cur_agent = cur_agent, None
        if in_phase:
            return  # the phase's reply — nobody was ever shown it, and nobody asked for it
        # The marker is machinery wherever it appears: a signal turn's reply loses it (and may end
        # silently), and any other reply that carries it loses it too — it never renders.
        if in_signal or QUIET_REPLY.lower() in open_turn["text"].lower():
            open_turn["text"] = quiet_reply(open_turn["text"])
        # An agent turn with neither prose nor a single operation is not a turn: it is the
        # residue of a beat that produced nothing, and it rendered as an empty grey card.
        if not open_turn["text"].strip() and not open_turn["ops"]:
            return
        for op in open_turn["ops"]:
            op.pop("_id", None)                # the call id pairs a result with its step; not served
        turns.append(open_turn)

    def human_words(stored: str) -> Optional[str]:
        """What the person typed for a stored prompt, or None if nothing recorded it.

        Exact first: the digest of the bytes the harness was handed. The suffix pass behind it
        is belt — it costs one comparison and it survives a harness that ever decorates the
        prompt on its way into the transcript — and it is still a MACHINE test (is this recorded
        string the tail of that stored one), never a reading of what either says."""
        hit = by_key.get(hashlib.sha256(stored.encode("utf-8")).hexdigest())
        if hit is not None:
            return hit
        for rec in unused:
            ut = rec["user_text"]
            if ut and stored.endswith(ut):
                unused.remove(rec)
                return ut
        return None

    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(obj, dict):
            continue
        kind = obj.get("type")
        msg = obj.get("message")
        content = msg.get("content") if isinstance(msg, dict) else None

        if kind == "user":
            # A real user prompt is a plain string or a content list with text blocks. A list that is
            # ONLY tool_results belongs to the preceding agent turn (a tool round-trip) — skip it.
            is_tool_result = (
                isinstance(content, list)
                and content
                and all(isinstance(b, dict) and b.get("type") == "tool_result" for b in content)
            )
            if is_tool_result:
                # HOW EACH CALL ENDED, on its step (held-label-rooms-1018): a write the privacy hold
                # kept reads held after a reload too, never "Wrote".
                if cur_agent is not None:
                    by_id = {o.get("_id"): o for o in cur_agent["ops"] if o.get("_id")}
                    for b in content:
                        op = by_id.get(b.get("tool_use_id"))
                        if op is not None:
                            ended = tool_outcome.result_outcome(not b.get("is_error", False), b.get("content"))
                            if ended.get("outcome"):
                                op["outcome"] = ended["outcome"]
                continue
            text = _block_text(content)
            if not text.strip():
                continue
            # The harness nudging itself is not speech (F51). No flush: the open agent turn keeps
            # accumulating, so the answer this interrupted comes back as ONE reply.
            if text.strip() == _HARNESS_CONTINUE:
                continue
            # HARNESS INJECTIONS ARE NOT SPEECH either: a loaded skill's body, or any line the
            # harness itself flags `isMeta`. Same no-flush rule — the agent's turn carries on, and a
            # skill becomes the step "Loaded skill · <name>" on it, once.
            skill = _skill_injection(text)
            if skill is not None or obj.get("isMeta"):
                if skill is not None:
                    if cur_agent is None:
                        cur_agent = {"role": "agent", "text": "", "ops": []}
                    ops = cur_agent["ops"]
                    named = [o for o in ops if o.get("label") == "skill" and o.get("skill") == skill]
                    unnamed = [o for o in ops if o.get("label") == "skill" and not o.get("skill")]
                    if unnamed and not named:
                        unnamed[-1]["skill"] = skill
                    elif not named:
                        ops.append({"label": "skill", "skill": skill})
                continue
            flush_agent()
            if PHASE_MARK in text:
                in_phase = True   # …and everything the agent says until the next real prompt
                continue
            in_phase = False
            in_signal = is_signal_turn(text)
            turn: dict = {"role": "user", "text": text}
            # A MEETING SIGNAL'S WAKE (transcript signals): the terminal shows "From the meeting" and
            # hides the composed wake prompt, read off this field rather than the text.
            if in_signal:
                turn["trigger"] = "signal"
            # WHEN it was said — a shared chat draws people's lines between the turns by it.
            if (at := _line_at(obj)) is not None:
                turn["at"] = at
            # `user_text` is the person's own words as a FIELD; `text` stays the stored prompt so
            # a record written before the field existed still reaches the terminal's fallback.
            #
            # AN ACT IS ITS LABEL, WHATEVER WAS RECORDED (Vexa-ai/vexa#1588). A marked act's
            # `user_text` was the composed preset until the worker learned better, and those
            # records are in people's own transcripts and are not ours to rewrite. The mark is
            # in the stored prompt, so the label is derivable here on every read — which also
            # covers a worker one release behind a terminal that already sends intents.
            said = act_label(text)
            if said is None:
                said = human_words(text)
                # …AND A RECORD WRITTEN BEFORE THE MARK IS STILL NOT SPEECH (#1605). A flow
                # dispatched the founder's post-meeting turn; the worker recorded the whole
                # composed kick as his half, because on a turn nobody typed there IS no other
                # half to record. `composed_label` reads the kind that kick opens with — the one
                # thing left in the record that says a machine wrote it — and answers "" for
                # everything else, so a sentence somebody typed stays their sentence.
                if said is not None:
                    said = composed_label(said) or said
            # WHAT WAS ATTACHED IS A FIELD, AND NEVER PART OF THE WORDS (founder, 2026-10-13). The
            # mark (and the `Attached files:` lines older turns carry) is read off the stored prompt,
            # so a turn recorded before the worker stripped it still renders as thumbnails.
            files = attachments_mod.read(text)
            if files:
                turn["attachments"] = files
                if said is not None:
                    said = attachments_mod.strip(said)
                else:   # no record: the terminal strips the stored prompt — hand it one without the block
                    turn["text"] = attachments_mod.strip(text)
            elif said is not None and attachments_mod.MARK_OPEN in said:
                said = attachments_mod.strip(said)
            if said is not None:
                turn["user_text"] = said
            # ANOTHER AGENT WROTE THIS (ADR-0046 decision 13) — who, and the thread to answer.
            sent = read_message_mark(text)
            if sent is not None:
                turn["from_agent"] = {"uid": sent["uid"], "thread": sent["thread"],
                                      "label": sent["label"]}
            turns.append(turn)
        elif kind == "assistant":
            if not isinstance(content, list):
                continue
            if cur_agent is None:
                cur_agent = {"role": "agent", "text": "", "ops": []}
            if "at" not in cur_agent and (at := _line_at(obj)) is not None:
                cur_agent["at"] = at
            cur_agent["text"] += _block_text(content)
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    op = _tool_op(b.get("name", ""), b.get("input") if isinstance(b.get("input"), dict) else None)
                    if b.get("id"):
                        op["_id"] = b["id"]
                    cur_agent["ops"].append(op)
        # all other line kinds (queue-operation/last-prompt/custom-title/mode/attachment/system …) are meta — skip
    flush_agent()
    return turns


class WorkspaceReader:
    def __init__(self, workspaces_dir: str) -> None:
        self._root = Path(workspaces_dir)
        self._also: list[Path] = []

    def allow(self, base: "str | Path") -> None:
        """Admit one directory OUTSIDE the store to the path-based readers — the organisation tier
        when the deployment keeps `_global` out of the store (`system_mounts.global_root`). Exactly
        that tree; the store root stays the guard for everything else."""
        resolved = Path(base).resolve()
        if resolved not in self._also:
            self._also.append(resolved)

    @property
    def root(self) -> Path:
        return self._root

    def home_dir(self, subject: str) -> Path:
        """The person's HOME (``workspace_attach.home_dir``): where a write that names no workspace
        lands — Personal while it is on, the first active workspace once it is off. ``workspace_dir``
        stays the seed slot itself, for the few callers that mean exactly that directory."""
        from control_plane.workspace_attach import home_dir  # noqa: PLC0415
        self._ws(subject)                                     # same traversal guard (ValueError)
        return home_dir(self._root, subject)

    def _ws(self, subject: str) -> Path:
        ws = (self._root / subject).resolve()
        root = self._root.resolve()
        if ws != root and root not in ws.parents:  # traversal guard (subject must stay under root)
            raise ValueError("invalid subject")
        return ws

    def workspace_dir(self, subject: str) -> Path:
        return self._ws(subject)

    def _guard_under_root(self, base: Path) -> Path:
        """A workspace dir must live under the store root (traversal guard). Used by the path-based
        readers so a mount PATH (from the active set — own private slots under .attached, or a shared
        workspace at <root>/<id>) can be read directly, not only a ``<root>/<subject>`` dir."""
        base = base.resolve()
        for root in (self._root.resolve(), *self._also):
            if base == root or root in base.parents:
                return base
        raise ValueError("outside root")

    def tree(self, subject: str, hidden: bool = False) -> list[str]:
        """Sorted relative paths of the subject's files (the subject's own ``<root>/<subject>`` dir)."""
        return self.tree_at(self._ws(subject), hidden=hidden)

    def tree_at(self, base: Path, hidden: bool = False) -> list[str]:
        """Sorted relative paths of the files under ``base`` (any workspace dir under the store root).

        Always excludes ``.git`` and ``.claude``. By default also excludes any other dotfile/dotdir;
        pass ``hidden=True`` to include those. ``.git`` and ``.claude`` stay hidden either way.
        """
        ws = self._guard_under_root(base)
        if not ws.exists():
            return []

        def skip(rel: str) -> bool:
            name = rel.rsplit("/", 1)[-1]
            return name in _ALWAYS_HIDDEN or (not hidden and name.startswith("."))

        out: list[str] = []
        # by descriptor, following no link (``workspace_paths.walk_files_inside``): a linked folder
        # is neither entered nor listed, a linked file is not a file of this workspace
        for rel in sorted(wpaths.walk_files_inside(ws, skip_dir=skip), key=lambda r: r.split("/")):
            parts = rel.split("/")
            if any(part in _ALWAYS_HIDDEN for part in parts):
                continue
            if not hidden and any(part.startswith(".") for part in parts):
                continue
            if not hidden and (rel.startswith(_RESERVED_PREFIXES) or _is_template_doc(ws, rel)):
                continue
            out.append(rel)
        return out

    def read(self, subject: str, path: str) -> Optional[str]:
        """The text at ``path`` within the subject's own workspace, or None if absent. Traversal-guarded."""
        return self.read_at(self._ws(subject), path)

    def read_at(self, base: Path, path: str) -> Optional[str]:
        """The text at ``path`` within the ``base`` workspace dir, or None if absent. Traversal-guarded.

        A template (by reserved PATH or by ``template: true`` FLAG — the same two tests ``tree_at``
        applies) comes back with ``_TEMPLATE_BANNER`` prepended, so a shape can never be read as a
        record. Deliberately not a refusal: the shape is what you consult to write a real entity."""
        ws = self._guard_under_root(base)
        try:
            # absolute · `..` · symlink-out · `.git`/`.vexa` — checked ONCE, then read nofollow
            # through what was checked, so a link swapped in afterwards refuses the read
            rbase, rel = wpaths.locate_inside(ws, path)
        except wpaths.PathRefused as exc:
            raise ValueError(str(exc)) from None
        raw = wpaths.read_bytes_inside(rbase, rel) if rel else None
        if raw is None:
            return None
        text = raw.decode("utf-8")
        if rel.startswith(_RESERVED_PREFIXES) or _is_template_doc(rbase, rel):
            return _TEMPLATE_BANNER + text
        if _UNSET_MARKER in text:
            return _UNFILLED_BANNER + text
        return text

    def _session_id(self, ws: Path, session: str) -> Optional[str]:
        """The claude sessionId for a thread, read from its continuity pointer
        (``.claude/sessions/<session>.session``; the legacy ``main`` falls back to ``.claude/.session``)."""
        candidates = [((".claude", "sessions"), f"{session}.session")]
        if session == "main":
            candidates.append(((".claude",), ".session"))
        for parts, name in candidates:
            sid = (_read_under(ws, parts, name, _POINTER_MAX_BYTES) or "").strip()
            if sid:
                # an id names a transcript file by itself: one plain name, or no id at all
                return sid if _PLAIN_ID.fullmatch(sid) else None
        return None

    def _continuity_roots(self, subject: str, extra_roots: "list[str | Path] | None" = None) -> list[Path]:
        """Every workspace dir a thread's continuity (pointer + transcript) may live in, in preference
        order: the PRIVATE SYSTEM tier (``<root>/.system/<subject>`` — where the worker anchored chats
        before the session store; with the store on nothing new lands there), the subject's own
        workspace (the legacy location), then any caller-supplied mount dirs that are THE SUBJECT'S
        OWN trees (a parked slot, an archived run) — the turn's cwd followed the active set under the
        flat model, so chats recorded before the _system anchoring landed sit under whichever
        workspace was mounted first.

        A SHARED tree is never a continuity root. A pointer (``.claude/sessions/<session>.session``)
        does not name its subject, and a session name ("main") is the same for every member, so a
        pointer in a tree several people mount is nobody's in particular: reading it hands one
        member another member's chat. Non-existent, out-of-root and shared candidates are dropped."""
        candidates: list[Path] = [self._root / ".system" / subject, self._ws(subject)]
        for e in extra_roots or []:
            candidates.append(Path(e))
        out: list[Path] = []
        seen: set[str] = set()
        for c in candidates:
            try:
                c = self._guard_under_root(c)
            except ValueError:
                continue
            k = str(c)
            if k in seen or not c.exists() or not self._own_tree(subject, c):
                continue
            seen.add(k)
            out.append(c)
        return out

    def _own_tree(self, subject: str, path: Path) -> bool:
        """Is ``path`` (already resolved under the store) one of ``subject``'s own trees — their seed
        slot ``<root>/<subject>``, their system tier ``.system/<subject>``, a parked slot
        ``.attached/<subject>/…``, or an archived run of either (``<archive>/<run>/[.system/]<subject>``)?
        Anything else — a shared workspace at ``<root>/<id>``, another person's tree, the
        organisation tier — is not."""
        from control_plane.session_migrate import ARCHIVE  # noqa: PLC0415 — deferred: load order
        try:
            parts = path.relative_to(self._root.resolve()).parts
        except ValueError:
            return False
        if not parts:
            return False
        if parts[0] == subject:
            return True
        if parts[0] in (".system", ".attached"):
            return len(parts) > 1 and parts[1] == subject
        if parts[0] == ARCHIVE and len(parts) > 2:
            rest = parts[2:]
            return rest[0] == subject or (rest[0] == ".system" and len(rest) > 1 and rest[1] == subject)
        return False

    def _swept_roots(self, subject: str, session: str) -> Iterator[Path]:
        """Where the last-resort sweep may look for ``session``'s pointer: the subject's own parked
        slots (``<root>/.attached/<subject>/<slug>``) and nothing else. A shared workspace is not
        swept, whatever its member list says — see ``_continuity_roots``. Folders reached through a
        link are skipped."""
        if not _PLAIN_ID.fullmatch(subject):
            return
        for slug in _real_subdirs(self._root, (".attached", subject)):
            yield self._root / ".attached" / subject / slug

    def pointer_names(self, subject: str) -> "Optional[set[str]]":
        """Every file name in a ``.claude/sessions`` folder ``locate_session`` could read a pointer
        from for this subject — its continuity roots, its parked slots and every workspace at the
        root — listed once (a SUPERSET: links followed, membership not checked). A session whose
        ``<session>.session`` is not in it has no thread for this subject, so a caller that asks
        about many sessions can skip ``history`` for those without sweeping the root once per
        session (live app.dev 2026-10-18: one sweep per nameless chat was ~0.3 s of the chat list).
        ``None`` when a folder could not be listed: then nothing may be skipped."""
        if not _PLAIN_ID.fullmatch(subject):
            return set()
        dirs: list[Path] = [self._root / ".system" / subject, self._root / subject]
        try:
            for parent in (self._root / ".attached" / subject, self._root):
                try:
                    with os.scandir(parent) as it:
                        dirs.extend(Path(e.path) for e in it)
                except (FileNotFoundError, NotADirectoryError):
                    continue
            names: set[str] = set()
            for d in dirs:
                try:
                    names.update(os.listdir(d / ".claude" / "sessions"))
                except (FileNotFoundError, NotADirectoryError):
                    continue
            return names
        except OSError:
            return None

    def locate_session(self, subject: str, session: str,
                       extra_roots: "list[str | Path] | None" = None) -> "Optional[tuple[str, list[Path]]]":
        """``(sessionId, roots)`` for a thread this subject can reach, or ``None`` — the roots the
        transcript is then searched in. ``None`` is the answer for a session the subject has no
        thread for, wherever else a thread of that name may exist."""
        if "/" in session or "\\" in session or session in ("", ".", ".."):
            return None
        try:
            roots = self._continuity_roots(subject, extra_roots)
        except ValueError:
            return None
        for ws in roots:
            sid = self._session_id(ws, session)
            if sid:
                return sid, roots
        # LAST RESORT — threads recorded BEFORE continuity anchoring sit under whatever workspace
        # was the turn's cwd at the time, which may no longer be mounted (deactivated, or switched
        # off). Only the subject's own parked slots are swept: a session name ("main") is the same
        # for every subject, so a pointer in any tree somebody else also writes is not theirs.
        for ws in self._swept_roots(subject, session):
            sid = self._session_id(ws, session)
            if sid:
                return sid, [*roots, ws]
        return None

    def history(self, subject: str, session: str, extra_roots: "list[str | Path] | None" = None) -> list[dict]:
        """The session's prior conversation as ordered, terminal-renderable turns.

        Resolves the thread's claude sessionId from its continuity pointer (``locate_session``), finds
        the transcript JSONL under ``<ws>/.claude/projects/<cwd-slug>/<sessionId>.jsonl``, and parses
        it into ``Turn``-shaped dicts: user turns ``{role:"user", text}``; agent turns
        ``{role:"agent", text, ops, commit?}``. Pointer and transcript are searched across the same
        roots — they normally co-locate, but a thread that MOVED anchors (cwd-rooted →
        _system-rooted) may have them apart. Tolerant by design — a missing pointer/file or
        unparseable lines yield ``[]`` (never raises), so the surface degrades to "no history yet"
        rather than erroring."""
        found = self.locate_session(subject, session, extra_roots)
        if found is None:
            return []
        sid, roots = found
        # The cwd-slug dir is claude's encoding of the workspace path; there is normally one, but match by
        # the sessionId filename to be safe. ``rglob`` also catches subagent transcripts — we want the top.
        raw: Optional[str] = None
        for ws in roots:
            for slug in _real_subdirs(ws, (".claude", "projects")):
                raw = _read_under(ws, (".claude", "projects", slug), f"{sid}.jsonl")
                if raw is not None:
                    break
            if raw is not None:
                break
        if raw is None:
            return []

        return parse_history(raw, _user_text_index(roots, session))

    def drop_session(self, subject: str, session: str) -> bool:
        """Delete a chat thread's continuity file (``.claude/sessions/<session>.session``) so a future
        turn on the same name starts a fresh conversation. The ``"main"`` thread also clears the legacy
        single-thread file (``.claude/.session``). Returns whether anything was removed. Traversal-safe:
        ``session`` is a bare name (no path separators)."""
        if "/" in session or "\\" in session or session in ("", ".", ".."):
            raise ValueError("invalid session")
        removed = False
        targets: list[tuple[Path, str]] = []
        # every continuity root the pointer may live in (_system, home — extra mount dirs are not
        # needed here: dropping the indexed thread only has to cover the anchored locations)
        for ws in self._continuity_roots(subject):
            targets.append((ws, f".claude/sessions/{session}.session"))
            if session == "main":
                targets.append((ws, ".claude/.session"))
        for ws, rel in targets:
            # by descriptor (``workspace_paths.unlink_inside``): `.claude` is the tools user's to
            # write, so a link planted at it or at `sessions` is never followed to remove a file
            # elsewhere; a link at the pointer itself is removed as the link
            removed = wpaths.unlink_inside(ws, rel, allow=_PLUMBING) or removed
        return removed

    def drop_history(self, subject: str, session: str,
                     extra_roots: "list[str | Path] | None" = None) -> int:
        """DELETE a chat's conversation: its transcript (``.claude/projects/<slug>/<sessionId>.jsonl``)
        and its continuity pointer, in every root ``locate_session`` searches for THIS subject — so
        neither ``history`` nor a later turn on the same name finds it again. Returns how many files
        went. Files the chat wrote into a workspace are not touched: they are the workspace's.

        By descriptor, like ``drop_session``: a link planted anywhere on the way is removed as the
        link or not followed, never used to delete a file elsewhere."""
        found = self.locate_session(subject, session, extra_roots)
        gone = 0
        if found is not None:
            sid, roots = found
            if _PLAIN_ID.fullmatch(sid or ""):
                for ws in roots:
                    for slug in list(_real_subdirs(ws, (".claude", "projects"))):
                        gone += int(wpaths.unlink_inside(ws, f".claude/projects/{slug}/{sid}.jsonl", allow=_PLUMBING))
                    gone += int(wpaths.unlink_inside(ws, f".claude/sessions/{session}.session", allow=_PLUMBING))
        gone += int(self.drop_session(subject, session))
        return gone

    def git_state(self, subject: str) -> dict:
        """Real source-control state of the subject's OWN (primary) workspace — thin wrapper over
        ``git_state_at`` with the caller as viewer (so their own commits classify as ``you``)."""
        return self.git_state_at(self._ws(subject), viewer=subject)

    def git_state_at(self, base: Path, viewer: Optional[str] = None) -> dict:
        """Author-attributed source-control state (branch · working changes · recent commits) of the
        workspace at ``base`` — which may be the caller's own repo OR a SHARED workspace they're a member
        of (resolved+authorized by the API's ``_read_target``). Empty shape if not yet a repo.

        Each commit carries ``author`` (the committing principal's display id, stamped by the per-mount
        turn-commit — D4) and ``kind`` ∈ {``you``, ``member``, ``system``} so the terminal can surface
        OTHER members' agent pushes to a shared workspace distinctly from the viewer's own writes and from
        platform/seed plumbing. ``viewer`` (the caller's subject id) is what makes ``you`` resolvable — the
        turn-commit stamps author email ``<subject>@vexa.local`` (see ``worker/engine.py`` principal)."""
        from shared.gitexec import run_git

        base = self._guard_under_root(base)
        if not (base / ".git").exists():
            return {"branch": "", "changes": [], "commits": []}

        def git(*args: str) -> str:
            # shared.gitexec: nothing the workspace's repository configures runs in this process
            return run_git(base, *args).stdout.strip()

        changes = []
        for line in git("status", "--porcelain").splitlines():
            if len(line) > 3:
                path = line[3:].strip()
                if path.split("/", 1)[0].lstrip(".") in ("git", "claude"):
                    continue  # hide the agent's internal .git/.claude session plumbing
                flag = line[:2].strip()[:1] or "M"
                changes.append({"path": path, "kind": "A" if flag in ("A", "?") else flag})
        viewer_email = f"{viewer}@vexa.local" if viewer else None
        commits = _commit_records(git("log", "-8", "--name-only", _LOG_FORMAT), viewer_email)
        return {"branch": git("rev-parse", "--abbrev-ref", "HEAD") or "main", "changes": changes, "commits": commits}

    def git_history_at(self, base: Path, *, path: Optional[str] = None, limit: int = 20,
                       viewer: Optional[str] = None) -> dict:
        """The workspace's recent commits, OPTIONALLY FILTERED TO ONE PAGE — what the workspace
        README's history section reads (Vexa-ai/vexa#1623).

        ``git_state_at`` above answers a different question and answers it well: *what is going on in
        this workspace right now* — the branch, the uncommitted changes, the last eight commits. This
        one answers *how did this page get here*, which needs two things that one does not have: a
        PATHSPEC, and a limit the caller sets. Same records, same attribution (``_commit_records``),
        so a commit shown in both places is the same commit with the same author in both.

        ``path`` is a caller-supplied string and goes through ``wpaths.resolve_inside`` before it
        reaches git, exactly as ``git_diff_at``'s does: ``git log -- ../../etc`` reads history from
        outside the workspace. ``--follow`` is deliberately NOT used — it is single-path-only and
        guesses at renames, and a guess in an audit list is worse than a short one.

        Empty shape (never an exception) for a directory that is not a repository yet: a workspace
        seeded but never committed to is an ordinary state, not a failure."""
        from shared.gitexec import run_git

        base = self._guard_under_root(base)
        rel = (path or "").strip() or None
        if rel is not None:
            try:
                wpaths.resolve_inside(base, rel)
            except wpaths.PathRefused as exc:
                raise ValueError(str(exc)) from None
        n = max(1, min(int(limit or 20), 200))     # a bound the caller cannot lift: this reads git
        if not (base / ".git").exists():
            return {"branch": "", "path": rel, "limit": n, "commits": []}

        def git(*args: str) -> str:
            return run_git(base, *args).stdout.strip()

        args = ["log", f"-{n}", "--name-only", _LOG_FORMAT]
        if rel is not None:
            args += ["--", rel]
        viewer_email = f"{viewer}@vexa.local" if viewer else None
        return {"branch": git("rev-parse", "--abbrev-ref", "HEAD") or "main", "path": rel, "limit": n,
                "commits": _commit_records(git(*args), viewer_email)}

    def git_diff_at(self, base: Path, sha: str, path: Optional[str] = None) -> dict:
        """Unified diff of ONE commit (optionally scoped to a single file) in the workspace at ``base`` —
        so the terminal can HIGHLIGHT exactly what changed. Capped so a huge commit can't flood the UI."""
        import re

        from shared.gitexec import run_git

        base = self._guard_under_root(base)
        if path is not None:
            # The path is a PATHSPEC handed to `git show` — the same caller-supplied string every
            # other route guards, and `git show <sha> -- ../x` reads out of the workspace.
            try:
                wpaths.resolve_inside(base, path)
            except wpaths.PathRefused as exc:
                raise ValueError(str(exc)) from None
        if not (base / ".git").exists() or not re.fullmatch(r"[0-9a-fA-F]{4,40}", sha or ""):
            return {"sha": sha, "path": path, "diff": "", "truncated": False}  # bad sha never hits git
        args = ["show", "--no-color", "--format=", sha]
        if path:
            args += ["--", path]
        out = run_git(base, *args).stdout
        lines = out.splitlines()
        return {"sha": sha, "path": path, "diff": "\n".join(lines[:600]), "truncated": len(lines) > 600}

    def git_reset_to(self, base: Path, sha: str) -> dict:
        """UNDO commits made after ``sha`` in the workspace at ``base``, and nothing else.

        THE ONE THING A HUMAN HAD TO DO BY HAND (Vexa-ai/vexa#1606). `process_meeting`'s decision-22
        detector records the organiser's desk HEAD before the post-meeting turn and refuses the step
        when it moved. Twice on 2026-09-06 the recovery was a person opening a shell, resetting that
        repository to the sha in the error, and re-firing the reaction — so the check was loud,
        correct, and un-actionable by the system that raised it. This is that shell command, with
        the two properties a shell command does not have.

        BACKWARD ONLY, AND ONLY ALONG THIS HISTORY. ``sha`` must be a real commit AND an ancestor of
        the current HEAD; anything else is refused. So this can only ever remove commits that landed
        after the witness was taken — it can never fast-forward a desk onto work it has not done, and
        it cannot be aimed at an unrelated history. A caller who could do either would be able to
        rewrite a person's desk by naming a sha, which is a much larger capability than undoing a
        write this same turn is known to have made.

        HARD, and that is deliberate: the stray commit is the whole problem, and a soft reset would
        leave its contents staged for the next writer to commit under a different message. Returns
        ``{"before", "after", "reset", "detail"}``; ``reset`` False with a ``detail`` is the refusal,
        never an exception — the caller is a flow step whose next move is to say why it could not."""
        import re

        from shared.gitexec import run_git

        base = self._guard_under_root(base)
        if not (base / ".git").exists():
            return {"before": "", "after": "", "reset": False, "detail": "not a git workspace"}
        if not re.fullmatch(r"[0-9a-fA-F]{7,40}", sha or ""):
            return {"before": "", "after": "", "reset": False,
                    "detail": f"{sha!r} is not a commit id"}

        def git(*args: str):
            return run_git(base, *args)

        before = git("rev-parse", "HEAD").stdout.strip()
        if not before:
            return {"before": "", "after": "", "reset": False, "detail": "the workspace has no HEAD"}
        if before.startswith(sha.lower()):
            return {"before": before, "after": before, "reset": False, "detail": "HEAD is already there"}
        if git("cat-file", "-e", f"{sha}^{{commit}}").returncode != 0:
            return {"before": before, "after": before, "reset": False,
                    "detail": f"{sha} is not a commit in this workspace"}
        if git("merge-base", "--is-ancestor", sha, "HEAD").returncode != 0:
            return {"before": before, "after": before, "reset": False,
                    "detail": f"{sha[:9]} is not an ancestor of HEAD — this only ever undoes "
                              f"commits made after the sha it is given"}
        r = git("reset", "--hard", sha)
        after = git("rev-parse", "HEAD").stdout.strip()
        if r.returncode != 0 or not after.startswith(sha.lower()):
            return {"before": before, "after": after, "reset": False,
                    "detail": (r.stderr or r.stdout or "reset failed").strip()[:300]}
        return {"before": before, "after": after, "reset": True, "detail": ""}


def home_of(reader, subject: str, *, for_read: bool = False):
    """``reader.home_dir(subject)`` — the person's home (#1783) — for any reader; one that predates
    ``home_dir`` (a test's stand-in) answers with its ``workspace_dir``. A WRITE with no home raises
    ``NoHome`` (the app answers 409 saying so); ``for_read`` answers the desk's own directory instead,
    so a file the person put there earlier can still be read."""
    fn = getattr(reader, "home_dir", None)
    if not callable(fn):
        return reader.workspace_dir(subject)
    try:
        return fn(subject)
    except Exception as e:  # noqa: BLE001
        from control_plane.workspace_attach import NoHome  # noqa: PLC0415
        if for_read and isinstance(e, NoHome):
            return reader.workspace_dir(subject)
        raise
