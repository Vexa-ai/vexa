"""host_claude — where THIS container can read the host's claude subscription credential, resolved
AT READ TIME.

THE INODE DEFECT THIS EXISTS TO FIX (dogfood, 2026-08-31 → 2026-09-01, ~32h of dead agents)
-------------------------------------------------------------------------------------------
Compose used to deliver the credential as a FILE bind::

    ${HOST_CLAUDE_CREDENTIALS}:/var/lib/vexa/host-claude-credentials:ro   # ~/.claude/.credentials.json

A file bind is resolved ONCE, at container create, and the kernel binds that **inode**. The claude
CLI refreshes an expiring OAuth token by writing a temp file and ``rename(2)``-ing it over
``.credentials.json`` — an atomic replace, which allocates a NEW inode and unlinks the old one. The
bind still points at the old, now-unlinked inode, so a LONG-LIVED container (agent-api, runtime)
reads the pre-refresh token **forever**, until it is recreated. Measured on the dogfood host::

    file mount, before replace: {"v":"ORIGINAL"}   dir mount: {"v":"ORIGINAL"}
    file mount, after  replace: {"v":"ORIGINAL"}   dir mount: {"v":"REFRESHED"}

Spawned WORKERS were never the broken half — each worker is a new container, so its bind resolves
the current inode at spawn time. The rot is entirely in the services that outlive a token refresh:
they keep answering the ``model_inference`` probe from a token that expired hours ago, so every
turn is refused before dispatch with "No model credentials are configured".

THE FIX
-------
Bind the DIRECTORY (``~/.claude`` → ``/var/lib/vexa/host-claude:ro``) and look the file up inside it
on every read. A directory bind tracks the directory inode; path lookup inside it happens live, so a
replaced file is visible immediately. Nothing needs to be recreated, ever.

The legacy FILE mount stays as a fallback so deployments that have not moved keep working, and
callers must NOT cache the result (never as a module constant, never as a default argument) — the
whole point is that the answer is re-derived per read.
"""
from __future__ import annotations

from pathlib import Path

#: The DIRECTORY mount — the host's ``~/.claude`` bound read-only. Inode-stable: a token refresh
#: inside it is visible immediately, with no container recreate.
CREDENTIALS_DIR_MOUNT = "/var/lib/vexa/host-claude"

#: The file inside that directory (the claude CLI's own name for it).
CREDENTIALS_FILENAME = ".credentials.json"

#: The LEGACY single-file mount. Inode-pinned — kept so deployments that predate the directory
#: mount keep working, but it goes stale the first time the CLI refreshes the token.
LEGACY_CREDENTIALS_MOUNT = "/var/lib/vexa/host-claude-credentials"


def candidate_paths() -> list[str]:
    """Every place the credential may be visible from inside this container, best first."""
    return [str(Path(CREDENTIALS_DIR_MOUNT) / CREDENTIALS_FILENAME), LEGACY_CREDENTIALS_MOUNT]


def credentials_path() -> str:
    """The path to READ the subscription credential from, right now.

    Directory mount first (live), legacy file mount second (may be a stale inode). Returns the
    legacy path when neither exists so the caller's own "missing credential" message — which names
    ``HOST_CLAUDE_CREDENTIALS`` and the remedy — is what the operator sees, rather than a path that
    means nothing to them.

    Call this per read. Binding it to a module constant or a default argument re-freezes exactly
    the staleness the directory mount removes.
    """
    for p in candidate_paths():
        if Path(p).is_file():
            return p
    return LEGACY_CREDENTIALS_MOUNT
