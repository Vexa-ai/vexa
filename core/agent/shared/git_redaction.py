"""git_redaction.py — the P15 scrubber, and the reason it is no longer a keyword argument.

The old shape was ``text.replace(token, "***")``: redaction only worked when the caller ALREADY HELD
the secret and remembered to pass it. On 2026-09-02 that assumption broke in the way assumptions like
this always break — the credential arrived somewhere nobody had classified as a credential. A founder
pasted a GitHub PAT into the attach dialog's REPOSITORY field; the token was the ``repo`` argument, so
``token`` was ``None``, so the redactor had nothing to replace, and git's own message —
``fatal: repository '<the token>' does not exist`` — went to the error card, to the browser console,
and into every place that error string travels.

So redaction here is **shape-based and unconditional**. It does not need to know what the secret is:

* the token families we can name (GitHub ``ghp_``/``gho_``/``ghu_``/``ghs_``/``ghr_``/``github_pat_``,
  GitLab ``glpat-``),
* credentials wearing a URL (``https://user:password@host``),
* and a **generic** long opaque run — 36+ characters of ``[A-Za-z0-9_-]`` — which is what catches the
  next provider's token before anyone has heard of it.

Known values are still passed when the caller has them (``redact(text, token)``); that is now belt to
the braces, not the mechanism.

TWO DELIBERATE HOLES, both so the scrubber does not destroy the diagnostics it exists to protect:

* a bare 40- or 64-character LOWERCASE HEX run is a git object id, not a secret — no token family in
  this file is hex-only, and masking every sha would make clone errors unreadable;
* the scrubber is applied AT THE SOURCE of git's text, never to a composed message. The deploy-key
  answer (``ssh-ed25519 AAAA…``) is a 68-character base64 run that the generic rule would eat, and it
  is the one part of that message the person actually needs.
"""
from __future__ import annotations

import re

MASK = "«redacted»"

#: Prefixes that make a string a credential no matter where it appears. Public so the request
#: validators (repo_ref) refuse the same shapes rather than keeping a second, drifting list.
TOKEN_PREFIXES = ("ghp_", "gho_", "ghu_", "ghs_", "ghr_", "github_pat_", "glpat-")

_TOKEN_FAMILIES = re.compile(
    r"\b(?:gh[pousr]_[A-Za-z0-9_]{4,}|github_pat_[A-Za-z0-9_]{4,}|glpat-[A-Za-z0-9_-]{4,})"
)
#: ``scheme://user:password@host`` — the credential is the ``user:password`` run.
_URL_USERINFO = re.compile(r"(?<=://)[^/\s:@]+:[^/\s@]+(?=@)")
#: ``scheme://token@host`` — a PAT used as the whole userinfo (how git's own clone URL carries one).
_URL_BARE_USERINFO = re.compile(r"(?<=://)[^/\s:@]{16,}(?=@)")
#: Anything long and opaque enough to be a secret we have no name for yet.
_GENERIC = re.compile(r"[A-Za-z0-9_-]{36,}")
#: …except a git object id, which is exactly what an operator reading a git error needs to keep.
_GIT_OID = re.compile(r"^[0-9a-f]{40}$|^[0-9a-f]{64}$")
#: …and except an SSH PUBLIC KEY, which is a long base64 run the generic rule would happily eat — and
#: which is the OPPOSITE of a secret: it is the answer we hand a person when a credential is missing
#: ("add this key to your repository"). Masking it would leave a message that says "add this:
#: «redacted»". Matched as the whole `ssh-ed25519 AAAA… comment` token so only key material inside a
#: real key line is spared, never a bare base64 blob on its own.
_SSH_PUBKEY = re.compile(
    r"\b(?:ssh-(?:rsa|dss|ed25519)|ecdsa-sha2-[A-Za-z0-9-]+|sk-ssh-ed25519@openssh\.com)"
    r"\s+[A-Za-z0-9+/]+=*(?:\s+\S+)?"
)


def looks_like_token(value: str) -> bool:
    """True when ``value`` is credential-shaped ON ITS OWN — the check a validator runs on a field a
    person typed, before anything is done with it."""
    v = (value or "").strip()
    return v.startswith(TOKEN_PREFIXES) or bool(_TOKEN_FAMILIES.fullmatch(v))


def redact(text, *known: "str | None") -> str:
    """``text`` with every credential-shaped run replaced by ``«redacted»``.

    Safe to call on anything (``None``, an exception, a dict repr) and safe to call twice — the mask
    contains no character the patterns match. ``known`` values, when a caller has them, are removed
    first so a short token that no pattern would catch is still gone."""
    out = str(text if text is not None else "")
    for k in known:
        k = (k or "").strip()
        if k:
            out = out.replace(k, MASK)
    out = _TOKEN_FAMILIES.sub(MASK, out)
    out = _URL_USERINFO.sub(MASK, out)
    out = _URL_BARE_USERINFO.sub(MASK, out)
    # Park public keys before the generic sweep and put them back after: the sweep cannot tell a key
    # from a secret by shape, and the difference matters more here than anywhere else in this file.
    kept: list[str] = []

    def _park(m):
        kept.append(m.group(0))
        return f"\x00pub{len(kept) - 1}\x00"

    out = _SSH_PUBKEY.sub(_park, out)
    out = _GENERIC.sub(lambda m: m.group(0) if _GIT_OID.match(m.group(0)) else MASK, out)
    for i, original in enumerate(kept):
        out = out.replace(f"\x00pub{i}\x00", original)
    return out
