"""The agent asks with the one inline card (UI 7g): an ```ask fence; the answer arrives as the
person's own `[answer <id>] …` message."""
from worker import engine


def test_every_dispatch_teaches_the_inline_card():
    text = engine.inline_card_preamble()
    assert text in engine.voice_preamble()
    assert "```ask" in text and "[answer " in text and '"chips"' in text and '"primary"' in text
    assert "never make the person type a code" in text.lower()



def test_never_a_card_for_an_act_without_a_tool():
    # founder 2026-10-16: a "Create the room" card was offered, then refused three times
    text = engine.inline_card_preamble().lower()
    assert "never offer a card for something you have no tool for" in text
    assert "by its title" in text
