"""The agent writes pages only with the blocks the page renders (founder 2026-10-16: a commitments
page carried MDX components the panel printed as literal tags)."""
from worker import engine


def test_every_dispatch_names_exactly_the_page_blocks():
    text = engine.page_blocks_preamble()
    assert text in engine.page_verbs_preamble()
    for block in ("[[", "{{", ":::columns", ":::callout", ":::stats", "```view", "<Steps>", "<Step title="):
        assert block in text, block
    assert "nothing else" in text.lower()
