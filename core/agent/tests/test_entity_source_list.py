"""A source that contains a comma stays ONE source, and re-upserting it adds nothing.

Friction report (dogfood, 2026-10-10): an agent passed
``source="Zoom call 17, 2026-10-09 14:07 UTC"`` to two upserts of the same company page. The
`sources:` frontmatter was written as a bare flow list and read back by splitting on every comma, so
the one source became two items, and the second upsert appended the whole string again beside its
halves. The person opened the page and found every source doubled in ``## Sources``.

Regression tests: the list is written so it reads back as itself, a repeated source is a no-op, a
page damaged by the old writer heals on the next upsert of the same source, and the frontmatter
remains YAML any other reader can parse.
"""
from __future__ import annotations

from workspaces.shared import entities as E

SRC = "Zoom call 17, 2026-10-09 14:07 UTC"


def _fm_sources(text: str) -> list[str]:
    line = next(ln for ln in text.splitlines() if ln.startswith("sources:"))
    return E._list_field(line.partition(":")[2])


def test_a_comma_inside_a_source_does_not_split_it(tmp_path):
    r = E.upsert_entity(tmp_path, "company", "Example Bank", ["Runs a pilot."], SRC,
                        today="2026-10-10")
    text = (tmp_path / r["path"]).read_text()
    assert _fm_sources(text) == [SRC]
    sources = text.split("## Sources", 1)[1].split("\n## ", 1)[0]
    assert sources.strip().splitlines() == [f"- {SRC}"]


def test_the_same_source_twice_is_recorded_once(tmp_path):
    E.upsert_entity(tmp_path, "company", "Example Bank", ["Runs a pilot."], SRC, today="2026-10-10")
    r = E.upsert_entity(tmp_path, "company", "Example Bank", ["Plans a rollout."], SRC,
                        today="2026-10-10")
    text = (tmp_path / r["path"]).read_text()
    assert _fm_sources(text) == [SRC]
    assert text.count(f"- {SRC}\n") == 1


def test_a_page_split_by_the_old_writer_heals_on_the_next_upsert(tmp_path):
    r = E.upsert_entity(tmp_path, "company", "Example Bank", ["Runs a pilot."], "mail",
                        today="2026-10-10")
    p = tmp_path / r["path"]
    # what the pre-fix writer left behind: the halves, then the whole re-appended unquoted
    p.write_text(p.read_text().replace(
        "sources: [mail]",
        "sources: [mail, Zoom call 17, 2026-10-09 14:07 UTC, Zoom call 17, 2026-10-09 14:07 UTC]"))
    E.upsert_entity(tmp_path, "company", "Example Bank", ["Plans a rollout."], SRC,
                    today="2026-10-10")
    assert _fm_sources(p.read_text()) == ["mail", SRC]


def test_quotes_hashes_and_brackets_round_trip():
    items = ['the "Q3" call', "Slack #general", "notes [draft]", "O'Brien's mail", "plain words",
             "- a dash first", "key: value"]
    assert E._list_field(E._render_list(items)) == items
    # bare items are still written bare, so existing pages do not churn
    assert E._render_list(["call A", "call B"]) == "[call A, call B]"


def test_the_frontmatter_is_still_yaml_other_readers_parse(tmp_path):
    yaml = __import__("pytest").importorskip("yaml")
    r = E.upsert_entity(tmp_path, "company", "Example Bank", ["Runs a pilot."], SRC,
                        today="2026-10-10")
    E.upsert_entity(tmp_path, "company", "Example Bank", ["Mentioned on Slack."], "Slack #general",
                    today="2026-10-10")
    text = (tmp_path / r["path"]).read_text()
    fm = yaml.safe_load(text.split("---", 2)[1])
    assert fm["sources"] == [SRC, "Slack #general"]


def test_a_bare_comment_after_a_value_is_still_a_comment():
    assert E._fm_get(["title: Example  # a note"], "title") == "Example"
    assert E._fm_get(['sources: ["Slack #general"]  # note'], "sources") == '["Slack #general"]'
    assert E._fm_get(["title: Robin's page"], "title") == "Robin's page"
