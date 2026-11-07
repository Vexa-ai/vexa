"""version.py — what is serving, as one cheap unauthenticated fact.

Two consumers, and neither of them is a human reading a log:

1. **The deploy script** (`deploy/dogfood/bin/deploy.sh` in the estate line). A blue/green swap
   starts the new container beside the old and has to decide, from outside, whether the thing now
   answering is the thing it started. `sha` answers that. It also has to refuse a TERMINAL whose
   bundle expects a different agent-api contract than the one serving — the F55/F77 pairing rule,
   which is what `api` is for: a small integer bumped by hand whenever a change to this service
   would break a client bundle built before it.

2. **The terminal itself.** It polls this endpoint and shows a one-line "a new version is ready —
   reload" bar when `sha` moves under an open tab. Before decision 39 that was a human ritual
   ("out" / "in"); the swap is invisible now, so the tab has to notice on its own.

`api` is NOT the build. Two different builds share an `api` whenever neither breaks the client;
it moves only on a client-visible break, which is why a client can pin it as a constant
(`clients/terminal/src/version.ts`) and the swap can compare the two numbers.

`sha` is whatever the deployment stamped as `VEXA_BUILD_SHA` — the image tag / commit the operator
deployed. It is deliberately NOT derived from the source tree: the answer has to be true of the
CONTAINER, and a container knows nothing about the git repo it was built from.
"""
from __future__ import annotations

import os

# ── THE CONTRACT NUMBER ──────────────────────────────────────────────────────────────────────────
# Bump ONLY when a change here would break a terminal bundle built before it (a removed or
# renamed response field, a changed status code, a new required request field). Bumping it makes
# every older terminal image REFUSED by the swap until it is rebuilt — that is the point.
# Keep in lockstep with TERMINAL_AGENT_API in clients/terminal/src/version.ts.
API_VERSION = 1

UNKNOWN = "unknown"


def build_sha() -> str:
    """The build the CONTAINER is running, as stamped by whoever started it.

    Empty/unset reads as "unknown" rather than an empty string: a consumer comparing two versions
    must be able to tell "no answer" from "a build called ''".
    """
    return (os.environ.get("VEXA_BUILD_SHA") or "").strip() or UNKNOWN


def version_payload() -> dict:
    return {"service": "agent-api", "sha": build_sha(), "api": API_VERSION}
