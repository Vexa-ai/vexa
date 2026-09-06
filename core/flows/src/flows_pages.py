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

_MAIL = re.compile(r"mailtext\.render\(\s*[\"']([\w-]+)[\"']")
_FRAGMENT = re.compile(r"\b(Reads|Effect|Result)\s*:\s*(.+?)(?=\s+\b(?:Reads|Effect|Result)\s*:|$)",
                       re.S)


def _collapse(text: str) -> str:
    return " ".join((text or "").split())


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
    the Python is at the foot behind a fold."""
    L: list[str] = []
    L.append("---")
    L.append(f"kind: {KIND}")
    L.append(f"flow: {facts['name']}")
    L.append(f"version: {facts['version']}")
    L.append(f"trigger: {facts['on']}")
    L.append(f"steps: {len(facts['steps'])}")
    L.append("generated: from the code that runs it — edits here are overwritten")
    L.append("---")
    L.append("")
    L.append(f"# {facts['name']}")
    L.append("")
    L.append(f"Runs when **`{facts['on']}`** happens, in {len(facts['steps'])} step"
             f"{'s' if len(facts['steps']) != 1 else ''}. This page is written from the code — the "
             f"docstrings below are the ones in the image that is running, and the Python at the "
             f"foot is that code verbatim.")
    L.append("")
    L.append("| | |")
    L.append("|---|---|")
    L.append(f"| **trigger** | `{facts['on']}` |")
    L.append(f"| **version** | {facts['version']} — a step list changes by adding a version, never "
             f"by editing one in place |")
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
        L.append("<details>")
        L.append(f"<summary>view source — <code>{step['name']}</code></summary>")
        L.append("")
        L.append("```python")
        L.append(step["source"])
        L.append("```")
        L.append("")
        L.append("</details>")
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
