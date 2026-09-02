"""THE THREE PROMPTS — what a person sees in their client's slash menu.

MCP prompts are the only thing a server can put in front of a PERSON without being asked, so they
carry the onboarding: `start` walks the whole setup, `whats_waiting` works the queue, `what_is_vexa`
answers the question somebody asks before they have an account.
"""
from __future__ import annotations

from .registry import prompt


@prompt(name='start', title='Set up Vexa', description="Connect this person's meetings to Vexa. Start here.")
def prompt_start() -> str:
    """The onboarding script, handed to the agent as a user turn."""
    return (
        "Set me up with Vexa.\n\n"
        "Do this now, without asking me to confirm each step:\n"
        "1. Call whats_waiting(). It tells you the single next thing to do, and it works "
        "whether or not I have an account.\n"
        "2. If I have no account, ask me ONE question — the email address my calendar invites "
        "come from — then call start_onboarding(email). A 6-digit code lands in that inbox.\n"
        "3. Ask me for the code, then call confirm_login(email, code). It returns a token — "
        "pass it as token=<value> on every account call for the rest of this conversation.\n"
        "4. Research my company from the email domain and call propose() for each thing you "
        "learn. Then ask me to confirm them, in one message, as a short list I can correct in "
        "a sentence.\n"
        "5. Record my answers with validate(), then call mark_scaffolded().\n"
        "6. Call whats_waiting() again and keep going until it is empty.\n"
        "7. Once set up, OFFER (do not start) a self-sustaining loop so Vexa keeps working "
        "between meetings — /loop 15m on a whats_waiting prompt; my yes starts it, and I can "
        "stop it anytime. Only offer it; never run it on your own.\n\n"
        "Keep it short. I want to answer two or three things, not fill in a form."
    )


@prompt(name='whats_waiting', title='What does Vexa need from me?', description='Everything Vexa is waiting on, and what to do about each.')
def prompt_waiting() -> str:
    return (
        "Call whats_waiting() and work through everything it returns. For each item, do the "
        "thing its `do` field says. Ask me only what you genuinely cannot determine yourself. "
        "When you have worked them all, call whats_waiting() once more to confirm it is empty."
    )


@prompt(name='what_is_vexa', title='What is Vexa?', description='Read the docs and answer — no account needed.')
def prompt_what() -> str:
    return (
        "Call vexa_overview(), and vexa_search_docs() for anything it does not cover. Tell me "
        "in a few sentences what this is, what it would do for my meetings, and what it would "
        "cost me to try. Say plainly if something is not supported rather than guessing."
    )
