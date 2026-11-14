"""The signed-in bot profile (prod rung MVP0): its own image, registered only when named."""
from runtime_kernel import profiles


def test_without_a_signed_in_image_the_registry_is_what_it_was(monkeypatch):
    monkeypatch.setenv("BROWSER_IMAGE", "vexaai/vexa-bot@sha256:" + "a" * 64)
    monkeypatch.delenv("SIGNED_IN_BROWSER_IMAGE", raising=False)
    reg = profiles.default_registry()
    assert profiles.SIGNED_IN_PROFILE not in reg.names()
    assert reg.get("meeting-bot").runnable.image.endswith("a" * 64)


def test_a_signed_in_image_adds_one_profile_and_leaves_meeting_bot_alone(monkeypatch):
    monkeypatch.setenv("BROWSER_IMAGE", "vexaai/vexa-bot@sha256:" + "a" * 64)
    monkeypatch.setenv("SIGNED_IN_BROWSER_IMAGE", "vexaai/vexa-bot@sha256:" + "b" * 64)
    reg = profiles.default_registry()
    bot, signed = reg.get("meeting-bot"), reg.get(profiles.SIGNED_IN_PROFILE)
    assert bot.runnable.image.endswith("a" * 64) and signed.runnable.image.endswith("b" * 64)
    assert signed.name == profiles.SIGNED_IN_PROFILE
    # Everything but the name and the image is the meeting-bot's.
    from dataclasses import replace
    assert replace(signed, name="meeting-bot", runnable=replace(signed.runnable, image=bot.runnable.image)) == bot


def test_the_lite_command_override_reaches_the_signed_in_profile_too(monkeypatch):
    monkeypatch.setenv("BROWSER_IMAGE", "x")
    monkeypatch.setenv("SIGNED_IN_BROWSER_IMAGE", "y")
    monkeypatch.setenv("BOT_COMMAND", "/usr/local/bin/vexa-bot-launch --x")
    reg = profiles.apply_command_overrides(profiles.default_registry())
    assert reg.get(profiles.SIGNED_IN_PROFILE).runnable.command == ["/usr/local/bin/vexa-bot-launch", "--x"]
