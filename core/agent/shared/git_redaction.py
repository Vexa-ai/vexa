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
#: Anything long and opaque enough to be a secret we have no name for yet. The threshold this was
#: written with — 36 — was read off a GitHub PAT, and the secrets this deployment actually holds are
#: shorter: an admin token is 32 hex characters and `sk_live_…` is about 32 (R-A09 / R-E09). So the
#: rule now has two bands, and the second is why the first can stay:
#:
#: * **36+**, masked on LENGTH alone — exactly the old rule, unchanged, so nothing that was masked
#:   before is unmasked now;
#: * **24–35**, masked only when the run is OPAQUE — at least two of lower/upper/digit. That is what
#:   separates `a1b2c3d4a1b2c3d4a1b2c3d4a1b2c3d4` from `some-long-repository-name`, and a clone
#:   error that cannot name the repository is a scrubber that has eaten its own diagnostics.
_GENERIC = re.compile(r"[A-Za-z0-9_-]{24,}")
_OPAQUE_MIN = 36
#: A BASE64-shaped secret — the one shape `_GENERIC` cannot see, because `+` and `/` are outside its
#: class and SPLIT the run: an AWS secret key (`…/K7MDENG/…`) becomes three sub-24 fragments and
#: survives whole. Applied with `_is_opaque_b64` rather than alone, because `/` is also the commonest
#: character in a URL path and masking `github.com/owner/repository-name` out of a clone error would
#: destroy the message this scrubber exists to keep readable.
_B64_RUN = re.compile(r"[A-Za-z0-9+/]{24,}={0,2}")
#: …except a git object id, which is exactly what an operator reading a git error needs to keep —
#: and ONLY where the line is talking about git. A bare 64-char lowercase hex run is precisely what
#: `INTERNAL_API_SECRET` and `ADMIN_TOKEN` look like, so the unconditional exemption was a hole
#: shaped exactly like this deployment's own secrets.
_GIT_OID = re.compile(r"^[0-9a-f]{40}$|^[0-9a-f]{64}$")
#: The words that make a 40/64-hex run an object id rather than a secret. Read off the LINE the run
#: sits on: git's own messages always name what the id is.
_GIT_CUE = re.compile(
    r"\b(?:commit|commits|sha|sha1|oid|object|objects|blob|tree|revision|rev|ref|refs|HEAD|"
    r"branch|tag|detached|fast-forward|merge|rebase|cherry-pick|checkout|clone|fetch|pull|push|"
    r"reset|pathspec|ancestor|upstream)\b", re.I)
#: …and except an SSH PUBLIC KEY, which is a long base64 run the generic rule would happily eat — and
#: which is the OPPOSITE of a secret: it is the answer we hand a person when a credential is missing
#: ("add this key to your repository"). Masking it would leave a message that says "add this:
#: «redacted»". Matched as the whole `ssh-ed25519 AAAA… comment` token so only key material inside a
#: real key line is spared, never a bare base64 blob on its own.
_SSH_PUBKEY = re.compile(
    r"\b(?:ssh-(?:rsa|dss|ed25519)|ecdsa-sha2-[A-Za-z0-9-]+|sk-ssh-ed25519@openssh\.com)"
    r"\s+[A-Za-z0-9+/]+=*(?:\s+\S+)?"
)
#: …and a KEY FINGERPRINT, for the same reason and with a nastier failure mode. ``SHA256:`` + 43
#: base64 chars only SOMETIMES contains a ``+`` or ``/`` — those characters are outside the generic
#: rule's class, so they split the run and the fingerprint survives by accident. Roughly one in four
#: fingerprints contains neither and would be masked: an intermittent bug in the message that tells a
#: person which key to add, which is the worst place to have one. Allow-listed explicitly.
_KEY_FINGERPRINT = re.compile(r"\b(?:SHA256:[A-Za-z0-9+/]{20,}=*|MD5:(?:[0-9a-f]{2}:){5,}[0-9a-f]{2})")


def looks_like_token(value: str) -> bool:
    """True when ``value`` is credential-shaped ON ITS OWN — the check a validator runs on a field a
    person typed, before anything is done with it."""
    v = (value or "").strip()
    return v.startswith(TOKEN_PREFIXES) or bool(_TOKEN_FAMILIES.fullmatch(v))


def _line_of(text: str, start: int, end: int) -> str:
    """The line ``text[start:end]`` sits on — the context both allow-lists below are read from."""
    ls = text.rfind("\n", 0, start) + 1
    le = text.find("\n", end)
    return text[ls:le if le != -1 else len(text)]


def _spare_as_git_oid(text: str, m) -> bool:
    """Is this 40/64-hex run an object id in a git sentence, rather than a hex secret?"""
    return bool(_GIT_OID.match(m.group(0))) and bool(_GIT_CUE.search(_line_of(text, m.start(), m.end())))


def _is_opaque_run(run: str) -> bool:
    """Two of lower/upper/digit — the density that tells a token from a hyphenated English name."""
    return sum(bool(re.search(p, run)) for p in (r"[a-z]", r"[A-Z]", r"[0-9]")) >= 2


def _mask_generic(text: str, m) -> bool:
    """The two-band rule at `_GENERIC`: 36+ on length alone, 24–35 only when the run is opaque —
    and never a git object id that the line itself says is one."""
    if _spare_as_git_oid(text, m):
        return False
    return len(m.group(0)) >= _OPAQUE_MIN or _is_opaque_run(m.group(0))


def _is_opaque_b64(text: str, m) -> bool:
    """Is this run a base64 SECRET rather than a URL path or a branch name?

    The class itself does most of the work: `_B64_RUN` is the STANDARD base64 alphabet and nothing
    else, so `-` and `_` are not in it — which excludes every hyphenated repository, branch and
    package name in one stroke (`some-team/some-long-repository-name`, `feature/PR-12-thing`) while
    keeping the shape an AWS secret key actually has. Three tests then narrow what is left:

    * it must contain a `+` or a `/` — otherwise `_GENERIC` already owns the run;
    * it must not START mid-path: a path continues a hostname or another segment, so the character
      before it is `.` or `/`, where a pasted secret follows whitespace, a quote or an `=`;
    * and it must be dense — two of lower/upper/digit, which a path segment rarely is."""
    run = m.group(0)
    if not any(c in "+/" for c in run):
        return False                                   # `_GENERIC` below already owns this run
    if run.startswith(("/", "+", "=")) or run.endswith("+"):
        return False
    if m.start() and text[m.start() - 1] in "./":
        return False                                   # a hostname tail, or a path continuing
    return _is_opaque_run(run)


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
    # Park PUBLIC key material before the generic sweep and put it back after: the sweep cannot tell a
    # key from a secret by shape, and the difference matters more here than anywhere else in this file
    # — these two shapes are the answer we hand someone whose credential is missing.
    kept: list[str] = []

    def _park(m):
        kept.append(m.group(0))
        return f"\x00pub{len(kept) - 1}\x00"

    out = _SSH_PUBKEY.sub(_park, out)
    out = _KEY_FINGERPRINT.sub(_park, out)
    out = _B64_RUN.sub(lambda m: MASK if _is_opaque_b64(out, m) else m.group(0), out)
    out = _GENERIC.sub(lambda m: MASK if _mask_generic(out, m) else m.group(0), out)
    for i, original in enumerate(kept):
        out = out.replace(f"\x00pub{i}\x00", original)
    return out
