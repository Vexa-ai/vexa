"""`scripts/gen_vocab_docs.py` writes `docs/docs/flows/vocabulary.mdx`, which is MDX. A docstring
that says `refs.{a,b}` or `meet-<id>` is fine prose in Python and an expression or a JSX tag in
MDX: one such line broke the whole docs build on 2026-10-10, and `{uid}` parses but renders as
nothing. The generator escapes them outside inline code; these tests hold it to that, for the
real registry's docstrings and for the committed page."""
from __future__ import annotations

import importlib.util
import pathlib
import re

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "gen_vocab_docs.py"
_spec = importlib.util.spec_from_file_location("gen_vocab_docs", SCRIPT)
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)

_CODE_SPAN = re.compile(r"`[^`]*`")


def bare_mdx_syntax(page: str) -> list[str]:
    """Every unescaped `{`, `}` or tag-opening `<` outside code, as 'line: context'."""
    found, fence = [], False
    for no, line in enumerate(page.split("\n"), 1):
        if line.startswith("```"):
            fence = not fence
            continue
        if fence or line.startswith("---"):
            continue
        prose = _CODE_SPAN.sub(lambda m: "_" * len(m.group()), line)
        for m in re.finditer(r"(?<!\\)(?:[{}]|<(?=[a-z]))", prose):
            found.append(f"{no}: {line[max(0, m.start() - 20):m.start() + 20]}")
    return found


def test_braces_and_tags_are_escaped_and_code_is_left_alone():
    assert gen.escape_mdx("Reads: refs.{uid,title} · Result: {}") == \
        "Reads: refs.\\{uid,title\\} · Result: \\{\\}"
    assert gen.escape_mdx("session meet-<id> and mail_outbox/<session>.md") == \
        "session meet-\\<id> and mail_outbox/\\<session>.md"
    assert gen.escape_mdx("the link is `?a={x}&b=<y>` verbatim") == \
        "the link is `?a={x}&b=<y>` verbatim"


def test_escaping_is_idempotent():
    once = gen.escape_mdx("refs.{a} and <b>")
    assert gen.escape_mdx(once) == once


def test_a_comparison_is_not_a_tag():
    assert gen.escape_mdx("retry when n < 3 or n<3") == "retry when n < 3 or n<3"


def test_the_page_generated_from_the_registry_has_no_bare_mdx_syntax():
    page = gen.render(gen.registry_steps())
    assert bare_mdx_syntax(page) == []


def test_the_committed_page_has_no_bare_mdx_syntax():
    assert bare_mdx_syntax(gen.OUT.read_text()) == []


def test_the_committed_page_is_what_the_registry_generates():
    """Docs must stay true: a step added, removed or re-documented without regenerating the page
    fails here, naming the fix."""
    expected = gen.render(gen.registry_steps())
    committed = gen.OUT.read_text()
    assert committed == expected, (
        "docs/docs/flows/vocabulary.mdx is stale against the step registry's docstrings — "
        "run `make vocab-docs` in core/flows and commit the regenerated page"
    )
