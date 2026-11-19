"""flows_pages.py — ONE PAGE PER FLOW, WRITTEN FROM THE CODE THAT RUNS IT.

Founder, 2026-09-06: *"that seed should contain policies as flows file that is nicely rendered, we
can show python to them?"* — and the answer is yes, as the APPENDIX of a page written for them,
never as the page. A flow is a list of steps and each step already carries a docstring that says
what it reads, what it does and what it leaves behind; that contract is in the code, and a page
somebody maintains beside it is a page that is wrong the first week nobody remembers to.

So the page is DERIVED, the same way `docs/docs/flows/vocabulary.mdx` already is
(`scripts/gen_vocab_docs.py`), and `tests/test_flow_pages.py` asserts the committed pages are what
this module produces right now. A step whose behaviour changes and whose page does not is a red
test, not a discovery six weeks later.

WHAT IS DERIVED, AND FROM WHERE — nothing here is written by hand twice:

    trigger          the flow's own `on` EventType
    the steps        the flow's step list, in order, with the domains each declares and what the
                     absence of one does to it (`needs` / `absent`, F-D20)
    what each does   the step's docstring — its first paragraph, and the house `Reads: · Effect: ·
                     Result:` triple where the step wrote one
    what it mails    `mailtext.render("<name>")` and `notify(...)` in the code the step can reach
    which rules      every `POLICIES.md` key named in that same code (Vexa-ai/vexa#1615)
    view source      `inspect.getsource`, verbatim

"THE CODE THE STEP CAN REACH" is the step's own source plus every function it closes over,
recursively. It has to be: `email_attendees` honours three policy rules and names none of them —
`_attendees` and `_followup_on` do, and those are closures in `production.build`'s scope, reachable
through `__closure__` and nowhere else. Scanning only the step body would report a flow that
honours no rules at all, confidently.
"""
from __future__ import annotations

import inspect
import re
import textwrap
from typing import Iterable

from flows_steps import policies

#: Where the pages live in the repo — the organisation-tier seed, so they land in `_global/flows/`
#: on every instance the way the layer files do (`control_plane/global_seed.py`).
PAGES_DIR = ("behavior", "global", "flows")

#: The flow-page front matter's own signature, so a renderer can tell one of these from any other
#: markdown without being told its path. Positive evidence, the same discipline
#: `POLICIES.md`'s `kind: policies` uses.
KIND = "flow"

#: A STEP THIS IMAGE DOES NOT CARRY, written out for a developer instead of refused (Vexa-ai/vexa#1639).
#: Its own kind, because a proposal is not a flow: nothing runs it, nothing may run it, and the page
#: exists to be read and sent. `_global/flows/proposals/<slug>.md`.
PROPOSAL_KIND = "proposal"

#: Where proposals live, under the flows directory. One segment, joined by the caller — the
#: authoring ask names the same path in prose and `tests/test_flow_author_pages.py` pins the two
#: together.
PROPOSALS_DIRNAME = "proposals"

_MAIL = re.compile(r"mailtext\.render\(\s*[\"']([\w-]+)[\"']")
_FRAGMENT = re.compile(r"\b(Reads|Effect|Result)\s*:\s*(.+?)(?=\s+\b(?:Reads|Effect|Result)\s*:|$)",
                       re.S)


def _collapse(text: str) -> str:
    return " ".join((text or "").split())


def _longest_backtick_run(text: str) -> int:
    """A docstring in the source may itself contain a fence. A three-backtick fence around it would
    close on the wrong line and spill Python into the page as prose."""
    return max((len(m) for m in re.findall(r"`+", text or "")), default=0)


def _summary(doc: str) -> str:
    """The step's first paragraph, collapsed — what it is, before what it reads and writes."""
    body = (doc or "").strip()
    if not body:
        return "⚠ undocumented — fix the docstring"
    first = body.split("\n\n", 1)[0]
    # The house convention puts the `Reads: · Effect: · Result:` triple in its own paragraph, but
    # some steps run it onto the first. Cut it off here and report it under its own headings.
    return _collapse(re.split(r"\bReads\s*:", first)[0]) or _collapse(first)


def _fragments(doc: str) -> dict:
    """The `Reads:` / `Effect:` / `Result:` triple the step declared, if it declared one.

    PARAGRAPH BY PARAGRAPH, because the triple is one paragraph and the prose after it is not: read
    across a blank line, `Result:` swallows whatever the docstring says next — which for
    `email_attendees` was three sentences about a receipt, printed as if they were the result."""
    out: dict = {}
    for para in (doc or "").split("\n\n"):
        for m in _FRAGMENT.finditer(para):
            out[m.group(1).lower()] = _collapse(m.group(2)).strip(" ·.").strip() or ""
    return {k: v for k, v in out.items() if v}


def reachable_source(fn) -> str:
    """The step's own source plus that of every function it closes over, recursively.

    Deterministic and de-duplicated by identity: two steps that share a helper each report it, and
    one helper reached by two paths is read once."""
    seen: set = set()
    out: list[str] = []

    def walk(f) -> None:
        if id(f) in seen:
            return
        seen.add(id(f))
        try:
            out.append(inspect.getsource(f))
        except (OSError, TypeError):  # a builtin, or a function with no source on disk
            return
        for cell in (getattr(f, "__closure__", None) or ()):
            try:
                value = cell.cell_contents
            except ValueError:  # an empty cell, mid-definition
                continue
            if inspect.isfunction(value):
                walk(value)

    walk(fn)
    return "\n".join(out)


def _mails(source: str) -> list[str]:
    names = sorted(set(_MAIL.findall(source)))
    if not names and re.search(r"\bnotify\(", source):
        return ["(composed in the step, from no template)"]
    return names


def _rules(source: str) -> list[str]:
    """Every `POLICIES.md` key this step's reachable code names. The key list is `policies.DEFAULTS`,
    so a rule that is added to the file and read by a step appears here without being told to."""
    return [k for k in policies.DEFAULTS if f'"{k}"' in source or f"'{k}'" in source]


def _step_facts(reg, name: str) -> dict:
    fn = reg.steps.get(name)
    if fn is None:
        return {"name": name, "summary": "⚠ this flow names a step this image does not carry",
                "needs": [], "absent": {}, "fragments": {}, "mails": [], "rules": [], "source": ""}
    source = reachable_source(fn)
    doc = inspect.getdoc(fn) or ""
    return {
        "name": name,
        "summary": _summary(doc),
        "needs": sorted(reg.step_needs.get(name, frozenset())),
        "absent": dict(reg.step_absent.get(name, {})),
        "fragments": _fragments(doc),
        "mails": _mails(source),
        "rules": _rules(source),
        "source": textwrap.dedent(inspect.getsource(fn)).rstrip(),
    }


def flow_facts(reg, flow) -> dict:
    steps = [_step_facts(reg, n) for n in flow.steps]
    return {
        "name": flow.name,
        "version": flow.version,
        "on": flow.on.name,
        "steps": steps,
        "mails": sorted({m for s in steps for m in s["mails"]}),
        "rules": [k for k in policies.DEFAULTS if any(k in s["rules"] for s in steps)],
    }


# ── rendering ────────────────────────────────────────────────────────────────────────────────────

def _absent_prose(step: dict) -> str:
    if not step["needs"]:
        return "reaches no other domain"
    parts = []
    for domain in step["needs"]:
        policy = step["absent"].get(domain, "abort")
        parts.append({
            "abort": f"without **{domain}** the reaction ends there, saying so",
            "skip": f"without **{domain}** this step is skipped and the flow carries on",
            "degrade": f"without **{domain}** it runs anyway, with less to work with",
        }[policy])
    return " · ".join(parts)


def render(facts: dict) -> str:
    """One flow page. Plain markdown: the front matter is metadata, the body is for a person, and
    the Python is at the foot behind a fold.

    THE RUNTIME KEYS ARE OPTIONAL AND ABSENT FOR AN IMAGE FLOW, deliberately: `flow_facts` does not
    produce them, so every page `make flow-pages` writes is byte-identical to what it wrote before
    a flow could be authored from a chat (`tests/test_flow_pages.py` compares the committed set).
    `runtime_facts` adds them, and they are what makes a page written from the governance chat say
    which of them it is — who activated it, whether it is still the one new facts react on, and
    which version replaced it if it is not (Vexa-ai/vexa#1639)."""
    retired = str(facts.get("status") or "") == "retired"
    superseded = facts.get("superseded_by")
    L: list[str] = []
    L.append("---")
    L.append(f"kind: {KIND}")
    L.append(f"flow: {facts['name']}")
    L.append(f"version: {facts['version']}")
    L.append(f"trigger: {facts['on']}")
    L.append(f"steps: {len(facts['steps'])}")
    if facts.get("status"):
        L.append(f"status: {facts['status']}")
    if facts.get("created_by"):
        L.append(f"authored-by: {facts['created_by']}")
    if superseded:
        L.append(f"superseded-by: {superseded}")
    L.append("generated: from the code that runs it — edits here are overwritten")
    L.append("---")
    L.append("")
    L.append(f"# {facts['name']}")
    L.append("")
    if retired:
        # THE RETIREMENT LINE, above everything else on the page. A retired version's page is kept
        # rather than deleted — it is what the flow used to do, and the reader arriving at it from
        # the index has to learn in the first line that it is not what happens now.
        L.append(f"> **Retired — version {superseded} is what runs now.**" if superseded
                 else "> **Retired — nothing runs on this flow now.**")
        L.append(">")
        L.append("> New facts are no longer matched to this version. A reaction admitted on it keeps"
                 " the version it was admitted on and runs these steps to the end.")
        L.append("")
    L.append(f"{'Ran' if retired else 'Runs'} when **`{facts['on']}`** happens, in "
             f"{len(facts['steps'])} step"
             f"{'s' if len(facts['steps']) != 1 else ''}. This page is written from the code — the "
             f"docstrings below are the ones in the image that is running, and the Python at the "
             f"foot is that code verbatim.")
    L.append("")
    L.append("| | |")
    L.append("|---|---|")
    L.append(f"| **trigger** | `{facts['on']}` |")
    L.append(f"| **version** | {facts['version']} — a step list changes by adding a version, never "
             f"by editing one in place |")
    if facts.get("status"):
        L.append(f"| **status** | {facts['status']}"
                 + (f" — superseded by version {superseded}" if superseded else "") + " |")
    if facts.get("created_by"):
        L.append(f"| **authored** | by `{facts['created_by']}`"
                 + (f", {facts['created_at']}" if facts.get("created_at") else "")
                 + " — submitted as data (a trigger and step names), never as code |")
    L.append(f"| **mails** | {', '.join('`%s`' % m for m in facts['mails']) or 'nothing'} |")
    L.append(f"| **rules it honours** | "
             f"{', '.join('[`%s`](../POLICIES.md#%s)' % (r, r) for r in facts['rules']) or 'none'} |")
    L.append("")
    L.append("## The steps, in order")
    L.append("")
    for i, step in enumerate(facts["steps"], 1):
        L.append(f"### {i}. `{step['name']}`")
        L.append("")
        L.append(step["summary"])
        L.append("")
        fr = step["fragments"]
        rows = [("reads", fr.get("reads")), ("effect", fr.get("effect")),
                ("result", fr.get("result"))]
        for label, value in rows:
            if value:
                L.append(f"- **{label}:** {value}")
        L.append(f"- **domains:** {_absent_prose(step)}")
        if step["mails"]:
            L.append(f"- **mails:** {', '.join('`%s`' % m for m in step['mails'])}")
        if step["rules"]:
            L.append("- **rules it honours:** "
                     + ", ".join("[`%s`](../POLICIES.md#%s)" % (r, r) for r in step["rules"]))
        L.append("")
    L.append("## The code")
    L.append("")
    L.append("Read-only, and the same bytes the image runs. It is here because the founder asked "
             "whether we can show it: the page is the explanation, this is the appendix.")
    L.append("")
    for step in facts["steps"]:
        if not step["source"]:
            continue
        # `<ViewSource>`, not a raw `<details>`: both compile, but a lowercase tag written
        # literally in the source does not resolve through the renderer's component map, so the
        # fold came out unstyled while the code block inside it did not. The registry is the only
        # surface where this page's own vocabulary can be given a shape.
        L.append(f'<ViewSource step="{step["name"]}">')
        L.append("")
        fence = "`" * max(3, _longest_backtick_run(step["source"]) + 1)
        L.append(f"{fence}python")
        L.append(step["source"])
        L.append(fence)
        L.append("")
        L.append("</ViewSource>")
        L.append("")
    return "\n".join(L).rstrip() + "\n"


def render_index(all_facts: Iterable[dict]) -> str:
    facts = list(all_facts)
    L = ["---", f"kind: {KIND}-index", f"flows: {len(facts)}",
         "generated: from the code that runs them — edits here are overwritten", "---", "",
         "# Flows", "",
         "Everything this deployment does on its own, one page each. A flow is a trigger and an "
         "ordered list of steps; every page below is written from the code that runs it, down to "
         "the Python at its foot.", "",
         "| flow | runs when | steps | rules it honours |", "|---|---|---|---|"]
    for f in facts:
        rules = ", ".join(f"`{r}`" for r in f["rules"]) or "—"
        L.append(f"| [`{f['name']}`]({f['name']}.md) | `{f['on']}` | {len(f['steps'])} | {rules} |")
    L.append("")
    L.append("The rules are answered in [`POLICIES.md`](../POLICIES.md), one directory up.")
    L.append("")
    # HOW TO ADD ONE, ON THE PAGE ABOUT THEM (Vexa-ai/vexa#1639). Founder, 2026-09-06: *"we want to
    # be able to write flows for the global chat as we like."* This index is mounted read-only into
    # every worker, so it is where an agent in the governance chat — and the administrator reading
    # over its shoulder — finds out that authoring is a conversation and not a deploy.
    L.append("## Writing one")
    L.append("")
    L.append("A flow is a trigger and an ordered list of steps by name; it is submitted as data and "
             "is live in about ten seconds, with no rebuild. **Say what this deployment should do, "
             "in the governance chat of `_global`** — the agent maps the sentence onto a trigger and "
             "steps from the vocabulary above, shows it as the page it will become, and asks once "
             "before it goes live.")
    L.append("")
    # ADDRESSED TO THE AGENT, in the tier that is mounted into every worker. This is the whole
    # reason the sentence is here rather than only in the ask: an ask runs when an act posts its
    # intent, and the founder's own path is that he SAYS something in the chat. Without a pointer
    # the agent has to already know the file exists — which on 2026-09-06 it did not, and the answer
    # was *"I still don't have the instruction"*.
    L.append("> **If you are an agent in this chat and the administrator describes something this "
             "deployment should do on its own, read `../asks/flow-author.md` and follow it.** Never "
             "answer that you have no instruction: read the vocabulary, map what they said, and show "
             "them the flow.")
    L.append("")
    L.append("A flow written that way gets its own page here, one per version, at "
             "`<flow>@<version>.md`. A step list is changed by adding a version and retiring the "
             "old one, so both pages stay and the retired one says which version runs now. Where a "
             "sentence needs something no step does, the answer is a page under `proposals/` — the "
             "step written out for a developer, never executed.")
    return "\n".join(L) + "\n"


# ── the whole set ────────────────────────────────────────────────────────────────────────────────

def build_registry():
    """The production registry with EVERY flow registered, including the agent-only half.

    A page set that depends on which domains the generating environment happened to name would be a
    different set on the next machine, and the test that compares them would be a coin toss. The
    door is set here rather than in the caller so the script and the test cannot disagree about it."""
    from flows import Registry
    from flows_defs import production
    from flows_steps import common

    class _NoDB:
        def execute(self, *_a, **_k):
            return []

    before = common.AGENT_API
    common.AGENT_API = before or "http://agent.pages.local"
    try:
        reg = Registry()
        production.build(reg, _NoDB())
    finally:
        common.AGENT_API = before
    return reg


def all_pages(reg=None) -> dict:
    """`{relative filename: content}` for every flow this image carries, plus the index."""
    reg = reg or build_registry()
    facts = [flow_facts(reg, f) for f in sorted(reg.flows.values(), key=lambda f: (f.name, f.version))]
    pages = {f"{f['name']}.md": render(f) for f in facts}
    pages["README.md"] = render_index(facts)
    return pages


# ── the pages of a flow somebody WROTE, from the chat ─────────────────────────────────────────────
#
# Founder, 2026-09-06: *"we want to be able to write flows for the global chat as we like."*
# (Vexa-ai/vexa#1639.) `flows_submit` has always been able to file a flow as data and have the
# worker running it ten seconds later — what it had no answer for was the sentence AFTER that:
# where do I read what I just made. The image's flows have a page each (#1615/#1626); a flow the
# admin authored had none, so the only way to see it was to ask the API for a JSON row.
#
# ONE PAGE PER VERSION, not one per flow, and that is the whole difference from `all_pages` above.
# A submitted flow is edited by ADDING a version and retiring the old one (`Registry.match` is
# newest-wins), so the two versions are two different answers to "what happens when this fires" and
# a reader who is told only the current one cannot see what changed. `<name>@<version>.md` keeps
# them apart AND keeps them out of the image set's namespace: `post_meeting.md` is the seeded page
# for the code's flow, `post_meeting@5.md` is a version somebody authored on top of it, and nothing
# that writes one can overwrite the other.

#: A runtime page's filename shape. The `@` is what the writer on the other side (agent-api's
#: `flow_pages_watch`) keys on to decide which files in `_global/flows/` are its to write, so it is
#: named here, once, and re-derived there rather than re-typed.
RUNTIME_PAGE_SEP = "@"


def page_file(name: str, version) -> str:
    """`<flow>@<version>.md` — the page of ONE runtime-authored version."""
    return f"{name}{RUNTIME_PAGE_SEP}{version}.md"


def etag(body: str) -> str:
    """A short content hash, so a poller can ask "has this page changed" without carrying it.

    Content and nothing else: the page is derived from the row AND from the image's step sources, so
    a step whose docstring changed produces a different page for an unchanged row — which is exactly
    the case a hash of the row would miss."""
    import hashlib
    return hashlib.sha256((body or "").encode("utf-8")).hexdigest()[:16]


def when(value) -> str:
    """`created_at` as a person reads it. The column is an epoch (`clock.now()`), and an epoch
    printed on a page written for an administrator is a number they have to convert."""
    try:
        ts = float(value)
    except (TypeError, ValueError):
        return " ".join(str(value or "").split())
    if ts <= 0:
        return ""
    import datetime as _dt
    return _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).strftime("%Y-%m-%d %H:%MZ")


def _superseded_by(rows: "list[dict]", row: dict):
    """The version that governs this flow now, when `row` is not it — else None.

    `match()` is newest-wins over ACTIVE versions from either source, so the answer is the highest
    active version of the same name, wherever it came from. A retired version with nothing active
    above it is retired into silence, and says so."""
    active = [r for r in rows if r.get("name") == row.get("name")
              and str(r.get("status") or "active") == "active"]
    if not active:
        return None
    top = max(int(r["version"]) for r in active)
    return top if top > int(row["version"]) else None


def runtime_facts(reg, row: dict, rows: "list[dict] | None" = None) -> dict:
    """Page facts for ONE runtime-authored version, from its `flow_version` row.

    The step facts come from the IMAGE — the row carries step NAMES and nothing else, which is the
    whole point of the contract (`flows_submit` never accepts code) — so a row naming a step this
    image does not carry renders the same "⚠ this flow names a step this image does not carry" line
    `_step_facts` already writes for a code flow, rather than failing to produce a page at all."""
    steps = [_step_facts(reg, n) for n in (row.get("steps") or [])]
    facts = {
        "name": row["name"],
        "version": int(row["version"]),
        "on": row.get("on") or row.get("on_event") or "",
        "steps": steps,
        "mails": sorted({m for s in steps for m in s["mails"]}),
        "rules": [k for k in policies.DEFAULTS if any(k in s["rules"] for s in steps)],
        "status": str(row.get("status") or "active"),
    }
    if row.get("created_by"):
        facts["created_by"] = str(row["created_by"])
    if when(row.get("created_at")):
        facts["created_at"] = when(row["created_at"])
    sup = _superseded_by(list(rows or []), row)
    if sup is not None:
        facts["superseded_by"] = sup
    return facts


def runtime_pages(reg, rows: "list[dict]") -> list[dict]:
    """`[{file, flow, version, status, etag, body}]` for every runtime-authored version, in order.

    A DRAFT is included and says `status: draft` on its own page: a flow filed and not yet activated
    is a thing the admin can be shown before it is live, which is the whole shape of the one
    confirmation the authoring ask asks for."""
    out: list[dict] = []
    for row in sorted(rows, key=lambda r: (str(r.get("name")), int(r.get("version") or 0))):
        facts = runtime_facts(reg, row, rows)
        body = render(facts)
        out.append({"file": page_file(facts["name"], facts["version"]), "flow": facts["name"],
                    "version": facts["version"], "status": facts["status"],
                    "etag": etag(body), "body": body})
    return out
