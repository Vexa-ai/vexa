"""One rule for where a git token may travel, and every git op obeys it.

Clone, pull and push each used to write a token into a URL by hand (`startswith("https://")`, then
`split("://")`), while the routes asked a fourth, `urlsplit`-based predicate. Four copies of a security
rule with two parsers. Now `shared/token_destination.py` is the only writer and the only reader.

Offline: no git, no network.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from shared.token_destination import embed_token, is_https, may_carry_token, saved_token_may_reach

TOKEN = "ghp_" + "T" * 36
AGENT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("url, expected", [
    ("https://github.com/acme/kg", f"https://{TOKEN}@github.com/acme/kg"),
    ("https://git.example.net:8443/acme/kg.git", f"https://{TOKEN}@git.example.net:8443/acme/kg.git"),
    ("  https://github.com/acme/kg  ", f"https://{TOKEN}@github.com/acme/kg"),
    ("HTTPS://github.com/acme/kg", f"https://{TOKEN}@github.com/acme/kg"),
])
def test_a_token_is_embedded_in_an_https_url(url, expected):
    assert embed_token(url, TOKEN) == expected


@pytest.mark.parametrize("url", [
    "http://github.com/acme/kg",            # cleartext
    "git@github.com:acme/kg.git",           # ssh: a deploy key, nowhere to put a token
    "ssh://git@github.com/acme/kg.git",
    "/local/path",
    "https://someone@github.com/acme/kg",   # already names a user: never two credentials
    "https://u:p@github.com/acme/kg",
    "https://github.com:notaport/acme/kg",  # malformed
    "",
])
def test_no_token_is_embedded_anywhere_else(url):
    assert embed_token(url, TOKEN) == url
    assert not may_carry_token(url)


def test_no_token_means_the_url_is_unchanged():
    assert embed_token("https://github.com/acme/kg", None) == "https://github.com/acme/kg"
    assert embed_token("https://github.com/acme/kg", "") == "https://github.com/acme/kg"


def test_the_saved_token_reaches_github_only_and_never_a_url_with_userinfo():
    assert saved_token_may_reach("https://github.com/acme/kg")
    assert not saved_token_may_reach("https://someone@github.com/acme/kg")
    assert not saved_token_may_reach("https://gitlab.example.com/acme/kg")
    assert is_https("https://gitlab.example.com/acme/kg")
    assert not is_https("http://github.com/acme/kg")


def test_the_routes_ask_the_same_predicates_the_git_ops_embed_through():
    from control_plane import workspace_credentials as wcreds
    from shared import token_destination
    assert wcreds.is_https is token_destination.is_https
    assert wcreds.saved_token_may_reach is token_destination.saved_token_may_reach


def test_a_url_that_names_a_user_never_gets_a_second_credential_on_clone_pull_or_push(monkeypatch, tmp_path):
    from control_plane import workspace_attach, workspace_git_sync as sync
    from shared import adapters

    url = "https://someone@github.com/acme/kg"
    seen: list[str] = []

    class _Proc:
        returncode, stdout, stderr = 1, "", "stop here"

    monkeypatch.setattr(adapters, "_git", lambda work, *a, **kw: seen.append(" ".join(a)) or "sha")
    adapters.push_with_token(tmp_path, url, "main", TOKEN)

    monkeypatch.setattr(sync, "home_remote", lambda wsp: ("origin", url))
    monkeypatch.setattr(sync, "_current_branch", lambda wsp: "main")
    monkeypatch.setattr(sync, "_git", lambda wsp, *a, **kw: seen.append(" ".join(a)) or _Proc())
    with pytest.raises(sync.RemoteSyncError):
        sync.pull_origin(tmp_path, token=TOKEN)

    def fake_run(cmd, *a, **kw):
        seen.append(" ".join(str(c) for c in cmd))
        raise workspace_attach.CloneError("stop here")
    monkeypatch.setattr(workspace_attach.subprocess, "run", fake_run)
    with pytest.raises(workspace_attach.CloneError):
        workspace_attach._git_clone(url, "main", tmp_path / "dest", TOKEN)

    assert seen and all(TOKEN not in line for line in seen)


def test_no_other_module_writes_a_token_into_a_url():
    """The rule is only one rule if nothing else implements it."""
    writer = re.compile(r'://\{(?:token|tok|pat)\}@|f"\{proto\}://')
    offenders = []
    for path in AGENT.rglob("*.py"):
        rel = path.relative_to(AGENT).as_posix()
        if rel.startswith(("tests/", ".venv/")) or "/tests/" in rel or rel == "shared/token_destination.py":
            continue
        if writer.search(path.read_text(encoding="utf-8", errors="replace")):
            offenders.append(rel)
    assert offenders == []
