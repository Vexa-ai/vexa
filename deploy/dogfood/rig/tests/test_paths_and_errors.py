"""R-D10 · R-D11 · R-D13 · R-D14 — untrusted strings: paths, addresses, exceptions, credentials."""
from __future__ import annotations

import json

from conftest import STATE, as_user, tool
import vexa_control_mcp as rig


def test_rd10_transcript_converters_cannot_leave_their_directories(monkeypatch):
    """GATE 6 (R-D10). `zoom_transcript_to_segments(name, path)` read ANY host path — and the
    lines it matched came back as `speakers`, so it was an arbitrary file read with its own oracle
    — and wrote to `~/.storm/caps/{name}.segments.json` with `name` unsanitized, so
    `name="../../../../tmp/x"` wrote outside. `captions_to_segments` had the same traversal.
    """
    as_user(monkeypatch, "7")
    outside = STATE / "outside.txt"
    outside.write_text("[00:00:01.0 --> 00:00:02.0] Someone (Corp): secret\n")

    out = json.loads(tool("zoom_transcript_to_segments")(name="ok", path=str(outside)))
    assert "outside the import directory" in out.get("error", ""), out

    for bad in ("../../../../tmp/x", "a/b", "with space", ""):
        out = json.loads(tool("zoom_transcript_to_segments")(name=bad, path=str(outside)))
        assert "name must match" in out.get("error", ""), (bad, out)
        out = json.loads(tool("captions_to_segments")(video_id=bad))
        assert "video_id must match" in out.get("error", ""), (bad, out)

    # …and the legitimate shape still works, from inside the import directory.
    inside = rig.IMPORT_DIR / "call.txt"
    inside.parent.mkdir(parents=True, exist_ok=True)
    inside.write_text("[00:00:01.0 --> 00:00:02.0] Someone (Corp): hello there\n")
    out = json.loads(tool("zoom_transcript_to_segments")(name="call", path=str(inside)))
    assert out.get("turns") == 1, out
    assert (rig.CAPS_DIR / "call.segments.json").is_file()


def test_rd11_start_onboarding_creates_no_account_and_answers_the_same_either_way(monkeypatch):
    """GATE 7a (R-D11). `start_onboarding` needs no account and was CREATING the platform user
    before any code was verified — so an unauthenticated caller minted accounts for addresses it
    did not own — then answered "existing" vs "created", which made it an existence oracle over the
    whole user table. It also mailed any address supplied, with only a per-address throttle.
    """
    sent = []
    monkeypatch.setattr(rig, "_send_code", lambda email, code: sent.append((email, code)) or None)
    http = as_user(monkeypatch, "7")
    rig.CURRENT.set(None)
    rig_state = rig.rig_secrets
    rig_state.write(rig.EMAIL_CODES_STORE, {})
    rig._CODE_SENDS.clear()

    new = json.loads(tool("start_onboarding")("nobody@example.com"))
    rig_state.write(rig.EMAIL_CODES_STORE, {})
    existing = json.loads(tool("start_onboarding")("known@example.com"))

    assert not [u for u in http.urls() if u.endswith("/admin/users")], \
        "an account was created before the code was verified"
    assert new["account"] == existing["account"], "the response still distinguishes the two"
    assert len(sent) == 2

    # …and the budget is a real ceiling, not a per-address one: the source of an anonymous call is
    # this process, so this is the only rate limit there is to apply.
    rig._CODE_SENDS.clear()
    for i in range(rig.CODE_BUDGET):
        rig_state.write(rig.EMAIL_CODES_STORE, {})
        tool("start_onboarding")(f"a{i}@example.com")
    rig_state.write(rig.EMAIL_CODES_STORE, {})
    over = json.loads(tool("start_onboarding")("one-too-many@example.com"))
    assert "too many sign-in codes" in over.get("error", ""), over


def test_rd11_the_code_is_single_use_and_the_account_is_made_after_the_proof(monkeypatch):
    """GATE 7b (R-D11). The code is spent on success, under the store's lock, before anything else
    happens — a code that survived its own use is a second sign-in for anyone who saw the
    transcript — and the account is created here, after the proof, never before it."""
    monkeypatch.setattr(rig, "_send_code", lambda email, code: None)
    http = as_user(monkeypatch, "7", routes={"/admin/users/email/": (404, {}),
                                             "/admin/users": (200, {"id": 42})})
    rig.CURRENT.set(None)
    rig.rig_secrets.write(rig.EMAIL_CODES_STORE, {})
    rig._CODE_SENDS.clear()

    tool("start_onboarding")("new@example.com")
    code = rig.rig_secrets.read(rig.EMAIL_CODES_STORE)["new@example.com"]["code"]

    first = json.loads(tool("confirm_login")("new@example.com", code))
    assert first.get("token", "").startswith("vxa_mcp_"), first
    assert any(u.endswith("/admin/users") for u in http.urls()), \
        "confirm_login did not create the account"

    again = json.loads(tool("confirm_login")("new@example.com", code))
    assert "no code is pending" in again.get("error", ""), again


def test_rd13_the_do_bridge_never_echoes_a_credential(monkeypatch):
    """GATE 9 (R-D13). The bridge returned `f"{type(e).__name__}: {e}"` verbatim over HTTP, and
    the tool internals under it raise with the URLs and headers they were using — including
    `psycopg.connect(url)` with the database password inside the DSN."""
    err = RuntimeError("connect failed: postgres://vexa:s3cretpassword@db:5432/flows "
                       "header X-Admin-API-Key: ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")
    out = rig._safe_error(err)

    assert "s3cretpassword" not in out
    assert "ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA" not in out
    assert "RuntimeError" in out, "the type is the diagnostic; masking it helps nobody"

    monkeypatch.setattr(rig, "_admin_key", lambda: "short-admin-key")
    rig._ADMIN_KEY_CACHE[:] = ["short-admin-key"]
    assert "short-admin-key" not in rig._safe_error(RuntimeError("used short-admin-key"))


def test_rd14_the_credential_detector_is_the_one_the_api_uses():
    """GATE 10 (R-D14). `_refuse_credentials` claimed to be "the same detector the API uses" and was
    a hand-rolled six-prefix copy: no `glpat-`, no generic long run, and a URL rule that required
    BOTH `:` and `@` in the userinfo — so `https://<gitlab-pat>@gitlab.com/a/b`, exactly how git
    writes a PAT into a remote, walked through."""
    assert rig._refuse_credentials("glpat-AAAAAAAAAAAAAAAAAAAA")
    assert rig._refuse_credentials("https://glpat-AAAAAAAAAAAAAAAAAAAA@gitlab.com/a/b")
    assert rig._refuse_credentials("https://user:password@github.com/a/b")
    assert rig._refuse_credentials("ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")

    # …and our own auth argument is not a git credential: `token=` is 40 urlsafe characters, so a
    # rule that only knows shapes would refuse every authenticated call to workspace_attach.
    assert not rig._refuse_credentials("https://github.com/Vexa-ai/vexa", "main", "team",
                                       "vxa_mcp_" + "A" * 32)
    assert not rig._refuse_credentials(rig.DELEGATION_PREFIX + "A" * 40)
    assert not rig._refuse_credentials(rig._view_token("7", "a/b.md"))
    # The detector is lifted out of the file and run on its own by
    # core/agent/tests/test_workspace_credentials.py, so its prefixes are literals. Pin them here.
    assert rig._OUR_CREDENTIAL_PREFIXES == ("vxa_mcp_", rig.DELEGATION_PREFIX, rig.VIEW_PREFIX)
