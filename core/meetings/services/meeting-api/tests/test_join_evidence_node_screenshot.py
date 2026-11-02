"""The bot's failed-join evidence keeps where the attempt ran and its screenshot key (2 Nov 2026).

The founder asked to see a prod failure screenshot and nobody could: the bot stored it inside the
pod, which is gone. The bot now uploads it (POST /internal/join-evidence/screenshot) and reports
`node` and `screenshot_key`; the control plane must keep both on meeting.data.join_evidence, in their
exact shapes only.
"""
from meeting_api.lifecycle.join_evidence import normalize_evidence


def _raw(**extra):
    base = {"reason": "awaiting_admission_rejected", "stage": "joining",
            "detail": "google_meet_join_refused: Google Meet refused the join on 3 of 3 knocks"}
    base.update(extra)
    return base


def test_keeps_node_and_screenshot_key():
    ev = normalize_evidence(_raw(node="lke590708-865130-0a1b2c3d", screenshot_key="join-evidence/35309/abc-123_x.png"))
    assert ev["node"] == "lke590708-865130-0a1b2c3d"
    assert ev["screenshot_key"] == "join-evidence/35309/abc-123_x.png"


def test_absent_fields_stay_absent():
    ev = normalize_evidence(_raw())
    assert "node" not in ev and "screenshot_key" not in ev


def test_malformed_values_are_dropped_not_stored():
    for node, key in [("Bad Node!", "join-evidence/1/a.png"), ("n1", "../../etc/passwd"),
                      ("n1", "join-evidence/x/a.png"), (12, None), ("n1", "join-evidence/1/a.jpg")]:
        ev = normalize_evidence(_raw(node=node, screenshot_key=key))
        assert ev is not None
        if not (isinstance(node, str) and node == "n1"):
            assert "node" not in ev
        assert "screenshot_key" not in ev or ev["screenshot_key"] == "join-evidence/1/a.png"
