"""Deliver: the sinks, and the CLI contract the postman path rides on."""

from __future__ import annotations

import json
import sys

from vexa_artifact_pipeline import (
    Artifact,
    CommandDelivery,
    DeliveryResult,
    FileDelivery,
    NullDelivery,
    Recipient,
    Section,
    postman_delivery,
)

ARTIFACT = Artifact(
    recipient=Recipient(display_name="Marvin Hanke", email="marvin@example.test"),
    meeting_id="12615",
    meeting_label="2026-05-18 · Microsoft Teams · 60m",
    sections=(Section(kind="you_committed", title="You committed to", items=("Show it to Toby today.",)),),
    renderer="template",
)


def test_the_file_sink_writes_the_markdown_and_the_schema_beside_it(tmp_path):
    result = FileDelivery(tmp_path).deliver(ARTIFACT, ARTIFACT.recipient)
    path = tmp_path / "12615" / "marvin-hanke.md"
    assert result.status == DeliveryResult.SENT and result.reference == str(path)
    assert path.read_text("utf-8") == ARTIFACT.to_markdown()

    sidecar = json.loads((tmp_path / "12615" / "marvin-hanke.json").read_text("utf-8"))
    assert sidecar["language"] == "en" and sidecar["meeting_id"] == "12615"
    assert Artifact.from_dict(sidecar) == ARTIFACT


def test_the_file_sink_is_safe_to_call_twice(tmp_path):
    sink = FileDelivery(tmp_path)
    sink.deliver(ARTIFACT, ARTIFACT.recipient)
    sink.deliver(ARTIFACT, ARTIFACT.recipient)
    assert len(list((tmp_path / "12615").glob("*.md"))) == 1


def test_the_null_sink_delivers_nothing_and_says_so():
    assert NullDelivery().deliver(ARTIFACT, ARTIFACT.recipient).status == "not_delivered"


def test_a_command_sink_receives_the_canonical_markdown_on_disk(tmp_path):
    out = tmp_path / "captured.md"
    sink = CommandDelivery(
        [sys.executable, "-c",
         "import pathlib,sys; pathlib.Path(sys.argv[2]).write_text("
         "pathlib.Path(sys.argv[1]).read_text('utf-8') + '\\nTO:' + sys.argv[3], 'utf-8')",
         "{artifact}", str(out), "{to}"],
        name="capture",
    )
    result = sink.deliver(ARTIFACT, ARTIFACT.recipient)

    assert result.status == DeliveryResult.SENT
    body = out.read_text("utf-8")
    assert body.startswith("**To:** Marvin Hanke")
    assert body.rstrip().endswith("TO:marvin@example.test")


def test_a_command_sink_refuses_a_recipient_it_cannot_address():
    sink = CommandDelivery([sys.executable, "-c", "raise SystemExit(0)"], name="capture")
    result = sink.deliver(ARTIFACT, Recipient(display_name="Karl Moll"))
    assert result.status == DeliveryResult.NO_ADDRESS
    assert "Karl Moll" in result.detail


def test_a_command_that_fails_is_reported_not_swallowed():
    sink = CommandDelivery([sys.executable, "-c", "import sys; sys.stderr.write('boom\\n'); raise SystemExit(3)"])
    result = sink.deliver(ARTIFACT, ARTIFACT.recipient)
    assert result.status == DeliveryResult.FAILED and "exit 3" in result.detail


def test_a_command_that_does_not_exist_is_reported_not_raised():
    result = CommandDelivery(["/nonexistent/vexa-postman"]).deliver(ARTIFACT, ARTIFACT.recipient)
    assert result.status == DeliveryResult.FAILED and "Error" in result.detail


def test_the_postman_configuration_is_the_flags_the_postman_publishes(tmp_path):
    sink = postman_delivery(tmp_path, base_url="http://door.test", smtp_port=11025)
    argv = sink._argv  # the CLI contract, asserted rather than assumed
    assert argv[1:3] == ["-m", "chat_door.postman"]
    assert "--artifact" in argv and "{artifact}" in argv
    assert "--to" in argv and "{to}" in argv
    assert argv[argv.index("--base-url") + 1] == "http://door.test"
    assert argv[argv.index("--smtp-port") + 1] == "11025"


def test_the_postman_configuration_never_carries_a_signing_key(tmp_path):
    """The key belongs to the postman and is inherited from the environment. A key on an
    argv is visible in every process listing on the host."""
    sink = postman_delivery(tmp_path, base_url="http://door.test")
    assert not any("KEY" in a.upper() or "SECRET" in a.upper() for a in sink._argv)
